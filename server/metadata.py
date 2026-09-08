"""
Tiered structured-field extraction for clinical reports.

Replaces regex-based "Key : Value" metadata parsing, which breaks on new
report formats/label phrasing. Pipeline:

  1. LLM structured extraction (primary) — generalizes to phrasing never
     seen before, unlike regex.
  2. Deterministic validation — dates must parse, age must be 0-120, sex
     must map to a known enum. A plausible-but-wrong LLM output is worse
     than a missed regex match, because it silently corrupts the index.
  3. NER cross-check — compare LLM output against the NER model's own
     Age/Date/Sex entities (already extracted elsewhere in the pipeline).
     Disagreement -> flag for review, don't guess.
  4. Provenance — every extracted field records how it was obtained and
     whether it needs human review, so "we don't know" is representable
     instead of fabricated.
"""

import re
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, ConfigDict

from dotenv import load_dotenv
from dateutil import parser as dateutil_parser
from google import genai
from google.genai import types
import json
import os

# metadata.py is imported by preprocessing.py before preprocessing has loaded
# dotenv, so it must load the established server/.env itself when run as the
# original script pipeline.
load_dotenv()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
if not GEMINI_API_KEY:
    raise RuntimeError("GEMINI_API_KEY is not set.")
client = genai.Client(api_key=GEMINI_API_KEY)

VALID_SEX_VALUES = {"M", "F", "O"}  # Male, Female, Other/Unknown — extend if needed
MIN_AGE, MAX_AGE = 0, 120

ISO_DATE_RE = re.compile(r'^\s*(\d{4})-(\d{2})-(\d{2})')


@dataclass
class ExtractedField:
    value: Optional[object]
    extraction_method: str
    flagged_for_review: bool
    note: str = ""


@dataclass
class ReportFields:
    patient_name: ExtractedField
    patient_age: ExtractedField
    patient_sex: ExtractedField
    report_date: ExtractedField
    reported_date: ExtractedField
    referring_doctor: ExtractedField
    report_type: ExtractedField

    def to_dict(self):
        return {name: asdict(value) for name, value in self.__dict__.items()}


def safe_parse_date(raw_value: str):
    """
    dateutil's dayfirst=True is meant to disambiguate formats like
    04/09/2016 (day/month vs month/day), but it ALSO incorrectly swaps
    month/day on UNAMBIGUOUS ISO dates — confirmed in practice:
    dateutil_parser.parse("2016-09-04", dayfirst=True) silently returns
    April 9th instead of September 4th. Guard against it: if the string
    is already ISO (YYYY-MM-DD), parse it directly and skip dayfirst
    entirely. Only use dayfirst=True for the ambiguous slash/dash formats
    these reports actually use (e.g. "04/09/2016", "13-Sep-2016").
    """
    iso_match = ISO_DATE_RE.match(str(raw_value))
    if iso_match:
        year, month, day = map(int, iso_match.groups())
        return datetime(year, month, day)

    return dateutil_parser.parse(raw_value, fuzzy=True, dayfirst=True)



class ExtractionModel(BaseModel):
    model_config = ConfigDict(extra="ignore")

    patient_name: Optional[str] = None
    patient_age: Optional[int] = None
    patient_sex: Optional[str] = None
    report_date: Optional[str] = None
    reported_date: Optional[str] = None
    referring_doctor: Optional[str] = None
    report_type: Optional[str] = None


EXTRACTION_SCHEMA = ExtractionModel.model_json_schema()

def build_llm_input_text(blocks_by_label: dict, full_prose: str) -> str:
    """
    Feeds the LLM header/footnote-type blocks (where IDs/dates usually live)
    plus the START and END of the prose (doctor sign-offs often appear at
    the end, e.g. "DR.JAGADISH KIRI, RADIOLOGIST" — truncating only the
    front would miss this), rather than the whole report, to control cost.
    """
    header_like = []
    for label in ("header", "paragraph_title", "footnote"):
        header_like.extend(blocks_by_label.get(label, []))

    header_text = "\n".join(header_like)
    prose_head = full_prose[:500]
    prose_tail = full_prose[-300:] if len(full_prose) > 500 else ""

    parts = [header_text, prose_head]
    if prose_tail:
        parts.append("...[middle of report omitted]...\n" + prose_tail)

    return "\n\n".join(p for p in parts if p.strip())


def extract_fields_with_llm(input_text: str) -> dict:

    system_prompt = (
        "Extract structured identifying fields from a clinical report. "
        "Use null when a field is not clearly present. "
        "Never guess or infer information that is not supported by the text."
    )

    system_prompt = (
        "Extract structured identifying fields from a clinical report. "
        "Use null when a field is not clearly present. "
        "Never guess or infer information that is not supported by the text."
    )

    chat = client.chats.create(
        model=os.getenv(
            "CLINICAL_METADATA_MODEL",
            "gemini-3.5-flash-lite",
        ),
        config=types.GenerateContentConfig(
            temperature=0,
            response_mime_type="application/json",
            response_schema=ExtractionModel,
            system_instruction=system_prompt,
        ),
    )

    response = chat.send_message(input_text)

    parsed = getattr(response, "parsed", None)

    if isinstance(parsed, BaseModel):
        return parsed.model_dump()

    raw_text = (response.text or "").strip()

    if not raw_text:
        raise RuntimeError(
            "Gemini metadata extraction returned empty content."
        )

    try:
        return json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            "Gemini metadata extraction returned invalid JSON."
        ) from exc


def validate_date(raw_value: Optional[str]) -> ExtractedField:
    if raw_value is None:
        return ExtractedField(None, "llm", flagged_for_review=False, note="not present in report")

    try:
        parsed = safe_parse_date(raw_value)
        # Sanity bound: reject dates absurdly far in the future or before
        # modern medical record-keeping — catches OCR/LLM date corruption
        # (e.g. misread year) rather than silently trusting it.
        if parsed.year < 1950 or parsed > datetime.now():
            return ExtractedField(
                raw_value, "failed_validation", flagged_for_review=True,
                note=f"parsed year {parsed.year} outside plausible range",
            )
        return ExtractedField(parsed.date().isoformat(), "llm", flagged_for_review=False)
    except (ValueError, OverflowError) as e:
        return ExtractedField(
            raw_value, "failed_validation", flagged_for_review=True,
            note=f"could not parse: {e}",
        )


def validate_age(raw_value: Optional[int]) -> ExtractedField:
    if raw_value is None:
        return ExtractedField(None, "llm", flagged_for_review=False, note="not present in report")

    try:
        age = int(raw_value)
    except (ValueError, TypeError):
        return ExtractedField(raw_value, "failed_validation", flagged_for_review=True,
                               note="not an integer")

    if MIN_AGE <= age <= MAX_AGE:
        return ExtractedField(age, "llm", flagged_for_review=False)
    return ExtractedField(
        age, "failed_validation", flagged_for_review=True,
        note=f"age {age} outside plausible range {MIN_AGE}-{MAX_AGE}",
    )


def validate_sex(raw_value: Optional[str]) -> ExtractedField:
    if raw_value is None:
        return ExtractedField(None, "llm", flagged_for_review=False, note="not present in report")

    normalized = raw_value.strip().upper()[:1]
    if normalized in VALID_SEX_VALUES:
        return ExtractedField(normalized, "llm", flagged_for_review=False)
    return ExtractedField(
        raw_value, "failed_validation", flagged_for_review=True,
        note=f"'{raw_value}' does not map to a known sex enum",
    )


def cross_check_with_ner(field: ExtractedField, entity_group: str, entities: list) -> ExtractedField:
    """
    Compares an already-validated LLM field against NER entities of the
    corresponding type. Agreement upgrades confidence; disagreement flags
    for review rather than silently picking one source over the other.
    """
    if field.value is None or field.flagged_for_review:
        return field  # nothing to cross-check, or already flagged

    ner_matches = [e["text"] for e in entities if e["entity_group"] == entity_group]
    if not ner_matches:
        return field  # NER found nothing of this type — not a disagreement, just no signal

    if entity_group == "Date":
        return _cross_check_date(field, ner_matches)

    ner_text_joined = " ".join(ner_matches).lower()
    llm_value_str = str(field.value).lower()

    # Loose substring check — NER spans are often partial/fragmented
    # (e.g. just "74" for age), so exact match is too strict here;
    # substring containment is enough of a sanity signal.
    if llm_value_str in ner_text_joined or ner_text_joined in llm_value_str:
        return ExtractedField(field.value, "llm+ner_agree", flagged_for_review=False)

    return ExtractedField(
        field.value, "llm", flagged_for_review=True,
        note=f"NER disagreement: LLM said '{field.value}', NER found '{ner_matches}'",
    )


def _cross_check_date(field: ExtractedField, ner_matches: list) -> ExtractedField:
    """
    Date-aware comparison instead of raw string containment. A plain
    substring check treats "2016-09-04" and "09 / 2016" as disagreeing,
    even though they're the same month/year — NER spans frequently lack
    day-level precision, so full-string equality is the wrong bar here.
    Comparing parsed (year, month) is the meaningful agreement signal.
    """
    try:
        llm_date = safe_parse_date(field.value)
    except (ValueError, OverflowError):
        return field  # LLM's own value is unparseable; validate_date already handles that path

    for ner_text in ner_matches:
        try:
            ner_date = safe_parse_date(ner_text)
        except (ValueError, OverflowError):
            continue

        if (llm_date.year, llm_date.month) == (ner_date.year, ner_date.month):
            return ExtractedField(field.value, "llm+ner_agree", flagged_for_review=False)

    return ExtractedField(
        field.value, "llm", flagged_for_review=True,
        note=f"NER disagreement: LLM said '{field.value}', NER found '{ner_matches}'",
    )


def extract_report_fields(blocks_by_label: dict, full_prose: str, entities: list) -> ReportFields:
    """
    Main entry point. blocks_by_label: dict mapping block_label -> list of
    block_content strings (from your existing OCR block parsing).
    entities: NER output list (from named_entity_recognition()).
    """
    input_text = build_llm_input_text(blocks_by_label, full_prose)
    raw = extract_fields_with_llm(input_text)

    # Deterministic validation per field
    age_field = validate_age(raw.get("patient_age"))
    sex_field = validate_sex(raw.get("patient_sex"))
    report_date_field = validate_date(raw.get("report_date"))
    reported_date_field = validate_date(raw.get("reported_date"))

    name = raw.get("patient_name")
    name_field = ExtractedField(
        name, "llm", flagged_for_review=False,
        note="not present in report" if name is None else "",
    )

    doctor = raw.get("referring_doctor")
    doctor_field = ExtractedField(
        doctor, "llm", flagged_for_review=False,
        note="not present in report" if doctor is None else "",
    )

    report_type = raw.get("report_type")
    report_type_field = ExtractedField(
        report_type, "llm", flagged_for_review=False,
        note="not present in report" if report_type is None else "",
    )

    # NER cross-check on the fields where NER has corresponding labels
    age_field = cross_check_with_ner(age_field, "Age", entities)
    sex_field = cross_check_with_ner(sex_field, "Sex", entities)
    report_date_field = cross_check_with_ner(report_date_field, "Date", entities)

    return ReportFields(
        patient_name=name_field,
        patient_age=age_field,
        patient_sex=sex_field,
        report_date=report_date_field,
        reported_date=reported_date_field,
        referring_doctor=doctor_field,
        report_type=report_type_field,
    )


# --- usage ---
# blocks_by_label: build this from your existing pruned_result.parsing_res_list
# by grouping block_content strings under their block_label, e.g.:
#
# blocks_by_label = {}
# for block in page.pruned_result['parsing_res_list']:
#     blocks_by_label.setdefault(block['block_label'], []).append(block['block_content'])
#
# fields = extract_report_fields(blocks_by_label, clean_text, entities)
# print(json.dumps(fields.to_dict(), indent=2))
#
# if any(f.flagged_for_review for f in fields.__dict__.values()):
#     print("This report needs human review before trusting its structured fields.")
