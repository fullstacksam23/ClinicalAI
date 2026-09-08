from paddleocr import PaddleOCRClient, Model
from dotenv import load_dotenv
import os
import pandas as pd
from bs4 import BeautifulSoup
from transformers import AutoTokenizer, AutoModelForTokenClassification, pipeline
from embedding import embed_report, embed_lab_results
from metadata import extract_report_fields
from db import save_report

load_dotenv()

PADDLE_OCR_TOKEN = os.getenv("PADDLE_OCR_TOKEN")
PADDLE_OCR_MODEL = Model.PADDLE_OCR_VL_16
NER_MODEL_NAME = "d4data/biomedical-ner-all"
NER_MIN_SCORE = 0.75  # drop low-confidence junk like "05 /" at conf=0.474

# NOTE: header/footnote/paragraph_title are no longer globally dropped —
# the extractor needs them (that's where doctor name, dates, IDs usually
# live). Only header_image/footer_image (pure images, no text) and footer
# (hospital branding boilerplate) are pure noise now.
NOISE_LABELS = {'header_image', 'footer_image', 'footer'}

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
    result = _ocr_client.parse_document(
        file_path=file_path,
        model=PADDLE_OCR_MODEL,
    )
    blocks_by_label, lab_df, clean_text = process_ocr_result(result)
    _ocr_client.close()

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
    page = result.pages[0]  # loop over result.pages for multi-page docs
    blocks = page.pruned_result['parsing_res_list']

    blocks_by_label = {}
    text_blocks = []
    table_html = None

    for block in blocks:
        label = block['block_label']
        content = (block.get('block_content') or '').strip()
        if not content or label in NOISE_LABELS:
            continue

        if label == 'table':
            table_html = content
            continue

        blocks_by_label.setdefault(label, []).append(content)

        # 'text' blocks still feed clean_text for NER/RAG, same as before —
        # only the metadata parsing (regex) is removed, not the text routing.
        if label == 'text':
            for line in content.split('\n'):
                if line.strip():
                    text_blocks.append(line.strip())

    lab_df = pd.DataFrame()
    if table_html:
        soup = BeautifulSoup(table_html, 'html.parser')
        rows = [[td.get_text(strip=True) for td in tr.find_all('td')]
                for tr in soup.find_all('tr')]
        rows = [r for r in rows if r]
        if rows:
            header, data_rows = rows[0], rows[1:]
            data_rows = [r for r in data_rows if len(r) == len(header)]
            lab_df = pd.DataFrame(data_rows, columns=header)

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


if __name__ == "__main__":
    fields, lab_df, clean_text, entities = ProcessFile("./pdfs/biochem1.pdf")
    report_id, patient_id = save_report(fields, lab_df, clean_text)

    embed_report(
        report_id=str(report_id),
        patient_id=str(patient_id),
        clean_text=clean_text,
        fields=fields,
        entities=entities,
    )

    # Companion step — synthesizes prose sentences from the lab table and
    # embeds those too, so lab VALUES become semantically searchable
    # (previously only report prose was). No-ops cleanly if lab_df is
    # empty (e.g. this radiology report has no lab table).
    embed_lab_results(
        report_id=str(report_id),
        lab_df=lab_df,
        fields=fields,
        patient_id=str(patient_id),
    )