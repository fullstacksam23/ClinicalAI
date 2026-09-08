"""
Clinical AI answer generation
=============================

Takes the already-retrieved evidence from ClinicalRetrievalEngine and generates
a grounded answer.

IMPORTANT:
- This model does NOT retrieve anything.
- This model does NOT choose SQL vs vector.
- Retrieval has already happened.
- patient_id is never supplied to the answer model.
- The answer must be grounded only in the supplied evidence.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, asdict
from typing import Any, Dict, List, Literal, Optional

from dotenv import load_dotenv
from google import genai
from google.genai import types
from pydantic import BaseModel, ConfigDict, Field

from retrieval import ClinicalRetrievalEngine, RetrievalResponse


load_dotenv()


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

ANSWER_MODEL = os.getenv(
    "CLINICAL_ANSWER_MODEL",
    "gemini-2.5-flash",
)

MAX_QUESTION_CHARS = 6000

# Maximum amount of evidence sent to the final answer model.
# These are application-owned limits, NOT model-controlled.
MAX_SQL_JSON_CHARS = 50000
MAX_VECTOR_JSON_CHARS = 50000

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
if not GEMINI_API_KEY:
    raise RuntimeError("GEMINI_API_KEY is not set.")

_gemini = genai.Client(api_key=GEMINI_API_KEY)



# ---------------------------------------------------------------------------
# Gemini structured-output models
# ---------------------------------------------------------------------------

class EvidenceUsedModel(BaseModel):
    model_config = ConfigDict(extra="ignore")

    source_type: Literal[
        "report_metadata",
        "lab_result",
        "vector_report_text",
        "vector_lab_text",
    ]
    report_id: Optional[str] = None
    lab_result_id: Optional[str] = None
    reason: str


class ClinicalAnswerModel(BaseModel):
    model_config = ConfigDict(extra="ignore")

    answer: str
    evidence_used: List[EvidenceUsedModel] = Field(default_factory=list)
    evidence_sufficient: bool
    uncertainty: str = ""


# ---------------------------------------------------------------------------
# Answer schema
# ---------------------------------------------------------------------------

ANSWER_SCHEMA = ClinicalAnswerModel.model_json_schema()


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

ANSWER_SYSTEM_PROMPT = """
You are the answer-generation model for a clinical record assistant.

Your job is to answer the user's question using ONLY the evidence supplied
in the current request.

The evidence comes from two trusted retrieval paths:

1. STRUCTURED DATABASE EVIDENCE
   - Report metadata
   - Exact laboratory results
   - Reference ranges
   - Dates
   - Report IDs
   - Lab-result IDs

2. SEMANTIC VECTOR EVIDENCE
   - Text extracted from the patient's medical reports
   - Narrative findings
   - Report impressions
   - Embedded lab-result text

IMPORTANT GROUNDING RULES
-------------------------
- Do NOT retrieve additional information.
- Do NOT assume facts that are not present in the supplied evidence.
- Do NOT invent dates, values, diagnoses, doctors, report IDs, or results.
- If the evidence does not support the answer, say so clearly.
- Never claim that a report exists if the supplied evidence does not show it.
- Never claim a lab value exists if the supplied structured evidence does not show it.
- Never manufacture a trend when there are insufficient measurements.

SOURCE PRIORITY
---------------
For exact numerical laboratory information:
    structured database evidence is authoritative.

For report wording, impressions, findings, and narrative statements:
    vector report-text evidence is authoritative.

If structured and vector evidence appear to conflict:
    do not silently reconcile them.
    State the conflict and prefer the structured value for exact lab numbers.

REPORT/LAB ASSOCIATION
----------------------
Use report_id and lab_result_id from the supplied evidence when available.

Do not invent identifiers.

MEDICAL SAFETY
--------------
You are a clinical-record summarization assistant, not a diagnosing physician.

You may summarize what is documented in the patient's records.

Do not:
- invent a diagnosis
- claim a condition is definitely present when the record does not establish it
- recommend medication changes
- recommend treatment changes
- present speculation as fact

When interpretation is uncertain, explicitly say that the available record
does not establish the conclusion.

ANSWER STYLE
------------
- Answer the user's actual question directly.
- Be concise but informative.
- Use dates when relevant.
- Include exact laboratory values and units when available.
- Include reference ranges when relevant.
- For trends, describe direction and the available measurements.
- For report questions, summarize the documented finding rather than rewriting
  the entire report.
- If nothing relevant was found, say that directly.

CITATION STYLE
--------------
Inside the answer text, cite supporting evidence using:

    [report:<report_id>]

for a report.

For a laboratory result use:

    [lab:<lab_result_id>]

For vector report text, use the report ID when available.

Only cite IDs that actually appear in the supplied evidence.

Do not create citations for unsupported information.
""".strip()


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class GeneratedAnswer:
    answer: str
    evidence_used: List[Dict[str, Any]]
    evidence_sufficient: bool
    uncertainty: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Evidence sanitization
# ---------------------------------------------------------------------------

def _sanitize_sql_evidence(sql_results: Dict[str, Any]) -> Dict[str, Any]:
    """
    Remove fields that are not needed by the answer model.

    In particular, patient_id must never be sent to the answer model.
    """

    sanitized: Dict[str, Any] = {}

    # -----------------------------------------------------------------------
    # Report metadata
    # -----------------------------------------------------------------------

    report_metadata = sql_results.get("report_metadata") or []

    sanitized["report_metadata"] = []

    for row in report_metadata:
        sanitized["report_metadata"].append({
            "id": row.get("id"),
            "report_type": row.get("report_type"),
            "report_date": row.get("report_date"),
            "referring_doctor": row.get("referring_doctor"),
            "metadata_flagged_for_review": row.get(
                "metadata_flagged_for_review"
            ),
            "selected_for_question": row.get(
                "selected_for_question",
                False,
            ),
        })

    # -----------------------------------------------------------------------
    # Selected reports
    # -----------------------------------------------------------------------

    selected_reports = sql_results.get("selected_reports") or []

    sanitized["selected_reports"] = []

    for row in selected_reports:
        sanitized["selected_reports"].append({
            "id": row.get("id"),
            "report_type": row.get("report_type"),
            "report_date": row.get("report_date"),
            "referring_doctor": row.get("referring_doctor"),
            "metadata_flagged_for_review": row.get(
                "metadata_flagged_for_review"
            ),
        })

    # -----------------------------------------------------------------------
    # Laboratory results
    # -----------------------------------------------------------------------

    lab_results = sql_results.get("lab_results") or []

    sanitized["lab_results"] = []

    for row in lab_results:
        sanitized["lab_results"].append({
            "id": row.get("id"),
            "report_id": row.get("report_id"),
            "investigation": row.get("investigation"),
            "result_value": row.get("result_value"),
            "result_numeric": row.get("result_numeric"),
            "result_unit": row.get("result_unit"),
            "ref_interval_raw": row.get("ref_interval_raw"),
            "ref_low": row.get("ref_low"),
            "ref_high": row.get("ref_high"),
            "method": row.get("method"),
        })

    # -----------------------------------------------------------------------
    # Selection information
    # -----------------------------------------------------------------------

    selection = sql_results.get("selection")

    if selection:
        sanitized["selection"] = {
            "selected_report_ids": selection.get(
                "selected_report_ids",
                []
            ),
            "needs_lab_results": selection.get(
                "needs_lab_results",
                False
            ),
            "reason": selection.get("reason", ""),
        }

    return sanitized


def _sanitize_vector_evidence(
    vector_results: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """
    Keep only fields useful to the answering model.

    patient_id is explicitly removed even though it may exist inside
    the Qdrant payload.
    """

    sanitized: List[Dict[str, Any]] = []

    for result in vector_results:
        payload = dict(result.get("payload") or {})

        cleaned_payload = {
            "report_id": payload.get("report_id"),
            "text": payload.get("chunk_text"),
            "report_type": payload.get("report_type"),
            "report_date": payload.get("report_date"),
            "referring_doctor": payload.get("referring_doctor"),
            "reported_date": payload.get("reported_date"),
            "patient_name": payload.get("patient_name"),
            "patient_age": payload.get("patient_age_at_report"),
            "patient_sex": payload.get("patient_sex"),
            "entity_types": payload.get("entity_types"),
            "investigation": payload.get("investigation"),
            "result_value": payload.get("result_value"),
            "result_numeric": payload.get("result_numeric"),
            "result_unit": payload.get("result_unit"),
            "ref_low": payload.get("ref_low"),
            "ref_high": payload.get("ref_high"),
        }

        # Remove absent values to keep the prompt smaller.
        cleaned_payload = {
            key: value
            for key, value in cleaned_payload.items()
            if value is not None
        }

        sanitized.append({
            "score": result.get("score"),
            "payload": cleaned_payload,
        })

    return sanitized


# ---------------------------------------------------------------------------
# JSON size limiting
# ---------------------------------------------------------------------------

def _bounded_json(data: Any, max_chars: int) -> str:
    """
    Serialize JSON and enforce an application-side character budget.

    This protects the final model from unexpectedly large retrieval payloads.
    """

    text = json.dumps(
        data,
        ensure_ascii=False,
        default=str,
        indent=2,
    )

    if len(text) <= max_chars:
        return text

    return (
        text[:max_chars]
        + "\n\n[Evidence truncated by application context budget.]"
    )


# ---------------------------------------------------------------------------
# Answer generator
# ---------------------------------------------------------------------------

class ClinicalAnswerGenerator:
    """
    Generates a grounded answer from an already-completed retrieval response.
    """

    def __init__(
        self,
        model: str = ANSWER_MODEL,
        *,
        max_sql_chars: int = MAX_SQL_JSON_CHARS,
        max_vector_chars: int = MAX_VECTOR_JSON_CHARS,
    ):
        self.model = model
        self.max_sql_chars = max_sql_chars
        self.max_vector_chars = max_vector_chars

    def generate(
        self,
        question: str,
        retrieval_response: RetrievalResponse,
    ) -> GeneratedAnswer:

        question = (question or "").strip()

        if not question:
            raise ValueError("question cannot be empty")

        if len(question) > MAX_QUESTION_CHARS:
            question = question[:MAX_QUESTION_CHARS]

        # -------------------------------------------------------------------
        # Prepare evidence
        # -------------------------------------------------------------------

        sql_evidence = _sanitize_sql_evidence(
            retrieval_response.sql_results
        )

        vector_evidence = _sanitize_vector_evidence(
            retrieval_response.vector_results
        )

        sql_json = _bounded_json(
            sql_evidence,
            self.max_sql_chars,
        )

        vector_json = _bounded_json(
            vector_evidence,
            self.max_vector_chars,
        )

        # -------------------------------------------------------------------
        # Build final answer prompt
        # -------------------------------------------------------------------

        user_prompt = f"""
USER QUESTION
-------------
{question}

STRUCTURED DATABASE EVIDENCE
----------------------------
{sql_json}

SEMANTIC VECTOR EVIDENCE
------------------------
{vector_json}

Answer the user's question using only the evidence above.

Remember:
- Exact lab numbers should come from structured database evidence.
- Narrative report findings should come from vector report evidence.
- Do not invent missing information.
- Cite supporting report IDs or lab-result IDs in the answer.
- State clearly when the available evidence is insufficient.
""".strip()

        # -------------------------------------------------------------------
        # Final LLM call
        # -------------------------------------------------------------------

        chat = _gemini.chats.create(
            model=self.model,
            config=types.GenerateContentConfig(
                temperature=0,
                response_mime_type="application/json",
                response_schema=ClinicalAnswerModel,
            ),
        )
        response = chat.send_message(user_prompt)

        parsed = getattr(response, "parsed", None)
        if isinstance(parsed, BaseModel):
            raw = parsed.model_dump()
        else:
            raw_content = (response.text or "").strip()
            if not raw_content:
                raise RuntimeError(
                    "Gemini answer-generation model returned empty content."
                )
            try:
                raw = json.loads(raw_content)
            except json.JSONDecodeError as exc:
                raise RuntimeError(
                    "Gemini answer-generation model returned invalid JSON."
                ) from exc

        # -------------------------------------------------------------------
        # Defensive normalization
        # -------------------------------------------------------------------

        answer = str(raw.get("answer") or "").strip()

        if not answer:
            answer = (
                "I could not generate an answer from the available "
                "clinical-record evidence."
            )

        evidence_used = raw.get("evidence_used") or []

        # Only allow source objects that are structurally valid.
        clean_evidence_used: List[Dict[str, Any]] = []

        for item in evidence_used:
            if not isinstance(item, dict):
                continue

            clean_evidence_used.append({
                "source_type": item.get("source_type"),
                "report_id": item.get("report_id"),
                "lab_result_id": item.get("lab_result_id"),
                "reason": str(item.get("reason") or ""),
            })

        evidence_sufficient = bool(
            raw.get("evidence_sufficient", False)
        )

        uncertainty = str(
            raw.get("uncertainty") or ""
        ).strip()

        return GeneratedAnswer(
            answer=answer,
            evidence_used=clean_evidence_used,
            evidence_sufficient=evidence_sufficient,
            uncertainty=uncertainty,
        )


# ---------------------------------------------------------------------------
# One-call convenience function
# ---------------------------------------------------------------------------

def answer_question(
    question: str,
    patient_id: str,
) -> GeneratedAnswer:

    engine = ClinicalRetrievalEngine(patient_id)

    retrieval_response = engine.retrieve(question)

    generator = ClinicalAnswerGenerator()

    return generator.generate(
        question=question,
        retrieval_response=retrieval_response,
    )


# ---------------------------------------------------------------------------
# Local test harness
# ---------------------------------------------------------------------------

if __name__ == "__main__":

    test_patient_id = os.getenv("TEST_PATIENT_ID")

    if not test_patient_id:
        raise SystemExit(
            "Set TEST_PATIENT_ID to the patient's Supabase/Qdrant UUID "
            "before running this test."
        )

    generator = ClinicalAnswerGenerator()
    engine = ClinicalRetrievalEngine(test_patient_id)

    questions = [
        "how are my x ray reports",
    ]

    for question in questions:

        print("\n" + "=" * 100)
        print("QUESTION")
        print(question)

        try:
            retrieval_response = engine.retrieve(question)

            generated = generator.generate(
                question=question,
                retrieval_response=retrieval_response,
            )

            print("\nANSWER")
            print(generated.answer)

            print("\nEVIDENCE USED")
            print(
                json.dumps(
                    generated.evidence_used,
                    indent=2,
                    ensure_ascii=False,
                )
            )

            print("\nEVIDENCE SUFFICIENT")
            print(generated.evidence_sufficient)

            print("\nUNCERTAINTY")
            print(generated.uncertainty)

        except Exception as exc:
            print(
                f"\nERROR: {type(exc).__name__}: {exc}"
            )