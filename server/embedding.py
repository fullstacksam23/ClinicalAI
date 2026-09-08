import torch
from transformers import AutoTokenizer, AutoModel
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams, PointStruct, Filter, FieldCondition, MatchValue, MatchAny, Range
import uuid
from datetime import datetime

from db import _parse_value, _parse_range

QDRANT_URL = "http://localhost:6333"
COLLECTION_NAME = "clinical_reports"
VECTOR_SIZE = 768  # MedCPT output dimension

DEFAULT_PATIENT_ID = "prototype_patient"
# TESTING ONLY. Distinct from db.py's TEST_PATIENT_ID (that's a lookup
# slug resolved to a real Supabase UUID via get_or_create_patient — this
# one is used AS-IS, with no resolution, directly as a Qdrant payload
# value). This fallback should rarely fire: preprocessing.py's real
# pipeline always explicitly passes the actual patient_id returned by
# save_report(). It only kicks in for code that calls embed_report() /
# query_patient_chunks() without a patient_id at all (e.g. calibration_run.py).
# Once auth exists, delete this and require patient_id everywhere — no
# fallback at all.

# MedCPT uses two separate encoders — this is core to how it was trained,
# not an implementation detail to simplify away.
QUERY_MODEL_NAME = "ncbi/MedCPT-Query-Encoder"
ARTICLE_MODEL_NAME = "ncbi/MedCPT-Article-Encoder"

device = "cuda" if torch.cuda.is_available() else "cpu"

query_tokenizer = AutoTokenizer.from_pretrained(QUERY_MODEL_NAME)
query_model = AutoModel.from_pretrained(QUERY_MODEL_NAME).to(device).eval()

article_tokenizer = AutoTokenizer.from_pretrained(ARTICLE_MODEL_NAME)
article_model = AutoModel.from_pretrained(ARTICLE_MODEL_NAME).to(device).eval()

qdrant_client = QdrantClient(url=QDRANT_URL)

if not qdrant_client.collection_exists(COLLECTION_NAME):
    qdrant_client.create_collection(
        collection_name=COLLECTION_NAME,
        vectors_config=VectorParams(size=VECTOR_SIZE, distance=Distance.COSINE),
    )


def embed_documents(texts):
    """Embed clinical report chunks — used at ingest time."""
    with torch.no_grad():
        inputs = article_tokenizer(
            texts, truncation=True, padding=True, max_length=512, return_tensors="pt"
        ).to(device)
        outputs = article_model(**inputs)
        embeddings = outputs.last_hidden_state[:, 0, :]  # [CLS] token embedding
    return embeddings.cpu().numpy().tolist()


def embed_query(text):
    """Embed a patient's question — used at search time. Different model than embed_documents."""
    with torch.no_grad():
        inputs = query_tokenizer(
            [text], truncation=True, padding=True, max_length=64, return_tensors="pt"
        ).to(device)
        outputs = query_model(**inputs)
        embedding = outputs.last_hidden_state[:, 0, :]
    return embedding.cpu().numpy()[0].tolist()


def chunk_text(text, chunk_size=300, overlap=50):
    words = text.split()
    chunks = []
    start = 0
    while start < len(words):
        end = start + chunk_size
        chunks.append(' '.join(words[start:end]))
        start += chunk_size - overlap
    return chunks


def _date_to_timestamp(iso_date_str):
    """report_date.value is stored as an ISO date string ('2016-09-13') by
    metadata_extraction.py's validate_date. Convert to a unix timestamp for
    Qdrant range filtering — Qdrant range filters need numeric fields."""
    if not iso_date_str:
        return None
    try:
        return int(datetime.fromisoformat(iso_date_str).timestamp())
    except ValueError:
        return None


def _build_common_payload(report_id, patient_id, fields, entity_groups):
    """Shared across both prose chunks (embed_report) and lab-derived
    sentence chunks (embed_lab_results) so both chunk types carry the same
    report-level metadata and are filterable/isolated the same way."""
    report_date_value = fields.report_date.value
    any_field_flagged = any(f.flagged_for_review for f in fields.__dict__.values())

    return {
        "patient_id": patient_id,
        "report_id": report_id,
        "patient_name": fields.patient_name.value,
        "patient_age_at_report": fields.patient_age.value,
        "patient_sex": fields.patient_sex.value,
        "report_date": report_date_value,
        "report_timestamp": _date_to_timestamp(report_date_value),
        "reported_date": fields.reported_date.value,
        "referring_doctor": fields.referring_doctor.value,
        "report_type": fields.report_type.value,
        "entity_types": entity_groups,
        "metadata_flagged_for_review": any_field_flagged,
    }


def embed_report(report_id, clean_text, fields, entities, patient_id=None):
    """
    fields: a ReportFields object from metadata_extraction.extract_report_fields()
    — carries validated values AND provenance (extraction_method,
    flagged_for_review) per field, which we store alongside the vector so
    retrieval/guardrail logic can choose to trust or exclude unreviewed data.
    """
    if not clean_text.strip():
        return

    patient_id = patient_id or DEFAULT_PATIENT_ID
    chunks = chunk_text(clean_text)
    entity_groups = sorted({e["entity_group"] for e in entities}) if entities else []
    vectors = embed_documents(chunks)

    payload_common = _build_common_payload(report_id, patient_id, fields, entity_groups)
    payload_common["chunk_type"] = "prose"

    points = [
        PointStruct(
            id=str(uuid.uuid4()),
            vector=vectors[i],
            payload={**payload_common, "chunk_text": chunks[i]},
        )
        for i in range(len(chunks))
    ]

    qdrant_client.upsert(collection_name=COLLECTION_NAME, points=points)
    print(
        f"Embedded {len(chunks)} prose chunk(s) for report {report_id} "
        f"(patient_id={patient_id}, flagged={payload_common['metadata_flagged_for_review']})"
    )


def _synthesize_lab_sentence(investigation, result_value, result_unit, ref_low, ref_high, report_date):
    """
    MedCPT's article encoder was trained on PubMed prose, not HTML tables —
    feeding it raw table markup is mismatched input (per architecture
    review). A short natural-language sentence per row is what the encoder
    actually expects, and it also gives semantic search something to match
    against for questions like "were any of my results high".
    """
    parts = [f"{investigation}: {result_value}"]
    if ref_low is not None and ref_high is not None:
        unit_str = f" {result_unit}" if result_unit else ""
        parts.append(f"(reference range {ref_low}-{ref_high}{unit_str})")
    elif ref_high is not None:
        parts.append(f"(reference: less than {ref_high})")
    elif ref_low is not None:
        parts.append(f"(reference: greater than {ref_low})")
    if report_date:
        parts.append(f", tested on {report_date}")
    return " ".join(parts).replace(" ,", ",")


def embed_lab_results(report_id, lab_df, fields, patient_id=None):
    """
    Companion to embed_report — synthesizes one short prose sentence per
    lab table row and embeds those, so lab VALUES become semantically
    searchable (currently only report prose is). Structured lookups should
    still go through Postgres directly (get_lab_values etc.) — this exists
    so a conversational question like "were any of my results high" can
    also be answered via retrieval, not to replace the exact-value path.

    No-ops cleanly if lab_df is empty (e.g. radiology reports with no
    table) — same behavior as insert_lab_results in db.py.
    """
    if lab_df is None or lab_df.empty:
        return

    patient_id = patient_id or DEFAULT_PATIENT_ID
    report_date_value = fields.report_date.value

    sentences = []
    row_meta = []

    for _, row in lab_df.iterrows():
        investigation = row.get("Investigation", "").strip()
        result_raw = row.get("Result", "")
        ref_raw = row.get("Biological Ref. Interval", "")

        # Skip the same rows insert_lab_results skips (footer row), PLUS
        # rows with no actual result value (e.g. a section-header row like
        # "L F T (TOTAL)" that has an investigation name but blank result —
        # not a real measurement, nothing meaningful to embed).
        if not investigation or investigation.upper().startswith("****"):
            continue
        if not str(result_raw).strip():
            continue

        result_numeric, result_unit = _parse_value(result_raw)
        ref_low, ref_high = _parse_range(ref_raw)

        sentence = _synthesize_lab_sentence(
            investigation, result_raw, result_unit, ref_low, ref_high, report_date_value
        )
        sentences.append(sentence)
        row_meta.append({
            "investigation": investigation,
            "result_value": str(result_raw),
            "result_numeric": result_numeric,
            "result_unit": result_unit,
            "ref_low": ref_low,
            "ref_high": ref_high,
        })

    if not sentences:
        return

    vectors = embed_documents(sentences)
    payload_common = _build_common_payload(report_id, patient_id, fields, entity_groups=[])
    payload_common["chunk_type"] = "lab_result"

    points = [
        PointStruct(
            id=str(uuid.uuid4()),
            vector=vectors[i],
            payload={**payload_common, "chunk_text": sentences[i], **row_meta[i]},
        )
        for i in range(len(sentences))
    ]

    qdrant_client.upsert(collection_name=COLLECTION_NAME, points=points)
    print(f"Embedded {len(sentences)} lab-result sentence(s) for report {report_id} (patient_id={patient_id})")


class PatientScopedRetriever:
    """
    Qdrant retrieval scoped ONLY by the trusted patient_id.

    The patient_id is the sole metadata/data-isolation filter. No date,
    doctor, report_type, report_id, or other metadata filter is accepted.
    The user's question is used as the semantic search signal.
    """

    def __init__(self, patient_id):
        patient_id = str(patient_id or "").strip()
        if not patient_id:
            raise ValueError("PatientScopedRetriever requires a non-empty patient_id")
        self.patient_id = patient_id

    def search(self, query_text, n_results=5, score_threshold=0.3):
        """
        Semantic search within exactly one patient's Qdrant corpus.

        score_threshold is only a relevance cutoff; it is not a data-scope
        filter. patient_id is the only metadata filter applied to Qdrant.
        """
        if not query_text or not query_text.strip():
            return []

        query_vector = embed_query(query_text)

        patient_condition = FieldCondition(
            key="patient_id",
            match=MatchValue(value=self.patient_id),
        )

        results = qdrant_client.query_points(
            collection_name=COLLECTION_NAME,
            query=query_vector,
            query_filter=Filter(must=[patient_condition]),
            limit=max(1, min(int(n_results), 50)),
        )

        return [p for p in results.points if p.score >= score_threshold]


def query_patient_chunks(query_text, patient_id, n_results=5, score_threshold=0.3):
    """Compatibility wrapper; patient_id is required."""
    retriever = PatientScopedRetriever(patient_id=patient_id)
    return retriever.search(
        query_text,
        n_results=n_results,
        score_threshold=score_threshold,
    )


# --- usage ---
# fields, lab_df, clean_text, entities = ProcessFile("rad1.pdf")
# embed_report(report_id="rad1", clean_text=clean_text, fields=fields, entities=entities)
#
# retriever = PatientScopedRetriever(patient_id="prototype_patient")
# results = retriever.search("did my chest x-ray show any lung problems?")
# for r in results:
#     print(r.score, r.payload["chunk_text"][:200])