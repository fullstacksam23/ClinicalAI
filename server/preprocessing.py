from paddleocr import PaddleOCRClient, Model
from dotenv import load_dotenv
import os
import time
from transformers import AutoTokenizer, AutoModelForTokenClassification, pipeline
from embedding import embed_report, embed_lab_results
from metadata import extract_report_fields
from db import save_report
from lab_normalization import normalize_tables
from paddleocr._api_client.errors import InvalidRequestError, NetworkError

load_dotenv()

PADDLE_OCR_TOKEN = os.getenv("PADDLE_OCR_TOKEN")
PADDLE_OCR_MODEL = Model.PADDLE_OCR_VL_16
NER_MODEL_NAME = "d4data/biomedical-ner-all"
NER_MIN_SCORE = 0.75  # drop low-confidence junk like "05 /" at conf=0.474

# NOTE: header/footnote/paragraph_title are no longer globally dropped —
# the extractor needs them (that's where doctor name, dates, IDs usually
# live). Only header_image/footer_image (pure images, no text) and footer
# (hospital branding boilerplate) are pure noise now.
NOISE_LABELS = {'header_image', 'footer_image', 'footer', 'image', 'images'}

# Load once at import time, not on every call — avoids re-downloading/
# re-initializing the model per document.
_tokenizer = AutoTokenizer.from_pretrained(NER_MODEL_NAME)
_model = AutoModelForTokenClassification.from_pretrained(NER_MODEL_NAME)
_ner_pipe = pipeline(
    "ner",
    model=_model,
    tokenizer=_tokenizer,
    aggregation_strategy="max",  # fixes wordpiece fragments like "nas"+"##st"+"##ric"
)

_ocr_client = PaddleOCRClient(
    token=PADDLE_OCR_TOKEN,
    request_timeout=300.0,
    poll_timeout=600.0,
)

def ProcessFile(file_path, patient_id=None):
    max_retries = 20
    retry_delay = 5
    network_retries = 3
    network_attempts = 0

    for attempt in range(max_retries):
        try:
            print(
                f"[PaddleOCR] Submitting document "
                f"(attempt {attempt + 1}/{max_retries})..."
            )

            result = _ocr_client.parse_document(
                file_path=file_path,
                model=PADDLE_OCR_MODEL,
            )

            print("[PaddleOCR] Successfully processed document.")
            break

        except NetworkError as exc:
            network_attempts += 1
            if network_attempts >= network_retries:
                raise RuntimeError(
                    "PaddleOCR is unreachable. Check internet/DNS access or "
                    "configure PADDLEOCR_BASE_URL before retrying."
                ) from exc

            wait = retry_delay * network_attempts
            print(
                f"[PaddleOCR] Network failure. "
                f"Waiting {wait}s before retrying ({network_attempts}/{network_retries - 1})..."
            )
            time.sleep(wait)

        except InvalidRequestError as exc:
            error = str(exc)

            if "任务提交队列已满" not in error:
                raise

            if attempt == max_retries - 1:
                raise

            print(
                f"[PaddleOCR] Queue is full. "
                f"Waiting {retry_delay}s before retrying..."
            )

            time.sleep(retry_delay)

    blocks_by_label, lab_df, clean_text = process_ocr_result(result)

    if not lab_df.empty:
        print("Lab table found:")
        print(lab_df)
    else:
        print("No table — this report is pure prose, route clean_text to NER")

    entities = named_entity_recognition(clean_text)

    # LLM primary + deterministic validation + NER cross-check, replacing
    # the old regex "Key : Value" parsing entirely.
    fields = extract_report_fields(blocks_by_label, clean_text, entities)

    print("\nExtracted fields:")
    for name, f in fields.__dict__.items():
        flag = "  <-- FLAGGED FOR REVIEW" if f.flagged_for_review else ""
        print(f"  {name:18} = {f.value!r:25} [{f.extraction_method}]{flag}")

    print("\nText for NER:\n", clean_text)

    return fields, lab_df, clean_text, entities


def process_ocr_result(result):
    """
    Splits OCR output into:
    - blocks_by_label: dict of block_label -> list of block_content strings
      (feeds the LLM extractor — replaces the old flat `metadata` dict)
    - lab_df: DataFrame of the lab table, if one exists (else empty)
    - clean_text: leftover prose text, for NER/RAG chunking
    """
    blocks_by_label = {}
    text_blocks = []
    table_htmls = []

    for page in getattr(result, 'pages', []) or []:
        blocks = page.pruned_result.get('parsing_res_list', [])

        for block in blocks:
            label = block.get('block_label')
            content = (block.get('block_content') or '').strip()
            if not content or label in NOISE_LABELS:
                continue

            if label == 'table':
                table_htmls.append(content)
                continue

            blocks_by_label.setdefault(label, []).append(content)

            # 'text' blocks still feed clean_text for NER/RAG, same as before —
            # only the metadata parsing (regex) is removed, not the text routing.
            if label == 'text':
                text_blocks.extend(
                    line.strip()
                    for line in content.splitlines()
                    if line.strip()
                )

    lab_df, extraction_stats = normalize_tables(table_htmls)
    print(
        "[Tables] "
        f"seen={extraction_stats['tables_seen']} "
        f"lab={extraction_stats['lab_tables']} "
        f"non_lab={extraction_stats['non_lab_tables']} "
        f"parsed={extraction_stats['rows_parsed']} "
        f"skipped={extraction_stats['rows_skipped']} "
        f"unparsed={extraction_stats['rows_unparsed']} "
        f"unknown_headers={extraction_stats['unknown_header_tables']}"
    )

    clean_text = '\n'.join(text_blocks)
    return blocks_by_label, lab_df, clean_text


def named_entity_recognition(text, min_score=NER_MIN_SCORE):
    if not text or not text.strip():
        return []

    raw_entities = _ner_pipe(text)

    entities = [
        {
            "entity_group": e["entity_group"],
            "text": e["word"],
            "score": round(float(e["score"]), 3),
            "start": e["start"],
            "end": e["end"],
        }
        for e in raw_entities
        if e["score"] >= min_score
    ]

    for e in entities:
        print(f"{e['entity_group']:15} | {e['text']:25} | conf={e['score']}")

    return entities
