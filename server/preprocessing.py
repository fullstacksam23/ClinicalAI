from paddleocr import PaddleOCRClient, Model
from dotenv import load_dotenv
import os
import re
import pandas as pd
from bs4 import BeautifulSoup
from transformers import AutoTokenizer, AutoModelForTokenClassification, pipeline

load_dotenv()

PADDLE_OCR_TOKEN = os.getenv("PADDLE_OCR_TOKEN")
PADDLE_OCR_MODEL = Model.PADDLE_OCR_VL_16
NER_MODEL_NAME = "d4data/biomedical-ner-all"
NER_MIN_SCORE = 0.75  # drop low-confidence junk like "05 /" at conf=0.474

NOISE_LABELS = {'header', 'header_image', 'footer', 'footer_image', 'footnote'}

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


def ProcessFile(file_path):
    client = PaddleOCRClient(token=PADDLE_OCR_TOKEN)
    result = client.parse_document(
        file_path=file_path,
        model=PADDLE_OCR_MODEL,
    )
    metadata, lab_df, clean_text = process_ocr_result(result)
    client.close()

    if not lab_df.empty:
        print("Lab table found:")
        print(lab_df)
    else:
        print("No table — this report is pure prose, route clean_text to NER")

    print("\nMetadata:", metadata)
    print("\nText for NER:\n", clean_text)

    entities = named_entity_recognition(clean_text)
    return metadata, lab_df, clean_text, entities


def process_ocr_result(result):
    """
    Splits OCR output into:
    - metadata: dict of key:value fields (patient name, dates, IDs, etc.)
    - lab_df: DataFrame of the lab table, if one exists (else empty)
    - clean_text: leftover prose text, for NER/RAG chunking
    """
    page = result.pages[0]  # loop over result.pages for multi-page docs
    blocks = page.pruned_result['parsing_res_list']

    metadata = {}
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

        for line in content.split('\n'):
            m = re.match(r'\s*([A-Za-z /]+?)\s*:\s*(.+)', line)
            if m:
                metadata[m.group(1).strip()] = m.group(2).strip()
            elif line.strip():
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
    return metadata, lab_df, clean_text


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
    ProcessFile("rad1.pdf")