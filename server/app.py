"""Thin HTTP adapter for the existing ClinicalAI pipeline.

This module intentionally contains no OCR, extraction, embedding, retrieval, or
answer-generation logic. It only validates browser requests, resolves the
server-owned prototype patient context, and calls the established modules.
"""
from __future__ import annotations

import json
import logging
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

# The established pipeline's credentials live beside its modules in
# ``server/.env``. Resolve that file explicitly so starting Uvicorn from the
# repository root exposes the identical configuration to db.py and the other
# existing pipeline modules.
load_dotenv(Path(__file__).resolve().parent / ".env")
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger("clinicalai.api")

ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = Path(__file__).resolve().parent / "static"
# The protected pipeline has script-style imports (``from db import ...``).
# Keep it executable both as its original scripts and through ``server.app``.
PIPELINE_DIR = Path(__file__).resolve().parent
if str(PIPELINE_DIR) not in sys.path:
    sys.path.insert(0, str(PIPELINE_DIR))
UPLOAD_DIR = Path(os.getenv("CLINICALAI_UPLOAD_DIR", str(ROOT / "data" / "uploads"))).resolve()
INDEX_PATH = UPLOAD_DIR / "documents.json"
MAX_UPLOAD_BYTES = int(os.getenv("CLINICALAI_MAX_UPLOAD_MB", "25")) * 1024 * 1024
EXTERNAL_PATIENT_ID = os.getenv("CLINICALAI_EXTERNAL_PATIENT_ID", "test_patient_001")


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=6000)


class ChatMessage(BaseModel):
    role: str
    content: str
    created_at: str
    sources: list[dict[str, Any]] = []


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_index() -> dict[str, dict[str, Any]]:
    if not INDEX_PATH.exists():
        return {}
    try:
        value = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        logger.warning("Could not read local document index", exc_info=True)
        return {}


def _save_index(index: dict[str, dict[str, Any]]) -> None:
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    temporary = INDEX_PATH.with_suffix(".tmp")
    temporary.write_text(json.dumps(index, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    temporary.replace(INDEX_PATH)


def _patient_id() -> str:
    """Resolve the sole browser session's server-controlled patient scope."""
    try:
        from db import supabase
        result = (supabase.table("patients").select("id")
                  .eq("external_patient_id", EXTERNAL_PATIENT_ID).limit(1).execute())
        if result.data:
            return str(result.data[0]["id"])
    except Exception as exc:
        logger.warning("Could not resolve prototype patient: %s", exc)
    raise HTTPException(503, "Clinical record service is unavailable. Check database configuration.")


def _document_from_report(report: dict[str, Any], local: dict[str, Any] | None = None) -> dict[str, Any]:
    local = local or {}
    return {
        "id": str(report.get("id") or local.get("id")),
        "filename": local.get("filename") or "Medical report",
        "size": local.get("size"),
        "uploaded_at": local.get("uploaded_at"),
        "status": local.get("status", "processed"),
        "error": local.get("error"),
        "report_type": report.get("report_type") or local.get("metadata", {}).get("report_type"),
        "report_date": report.get("report_date") or local.get("metadata", {}).get("report_date"),
        "referring_doctor": report.get("referring_doctor") or local.get("metadata", {}).get("referring_doctor"),
        "metadata_flagged_for_review": bool(report.get("metadata_flagged_for_review")),
        "has_file": bool(local.get("file_path") and Path(local["file_path"]).is_file()),
    }


def _existing_reports() -> list[dict[str, Any]]:
    from db import get_reports_list
    return get_reports_list(_patient_id())


app = FastAPI(title="ClinicalAI", version="1.0.0")
origins = [item.strip() for item in os.getenv("CLINICALAI_CORS_ORIGINS", "http://localhost:8000").split(",") if item.strip()]
app.add_middleware(CORSMiddleware, allow_origins=origins, allow_credentials=True, allow_methods=["*"], allow_headers=["*"])


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/documents")
def list_documents() -> list[dict[str, Any]]:
    index = _load_index()
    reports = _existing_reports()
    known = {str(report["id"]): report for report in reports}
    documents = [_document_from_report(report, index.get(str(report["id"]))) for report in reports]
    # Preserve a visible failed-upload state even where pipeline persistence failed.
    documents.extend(_document_from_report({}, item) for key, item in index.items() if key not in known and item.get("status") == "failed")
    return sorted(documents, key=lambda item: item.get("uploaded_at") or item.get("report_date") or "", reverse=True)


@app.get("/api/documents/{document_id}")
def get_document(document_id: str) -> dict[str, Any]:
    index = _load_index()
    report = next((row for row in _existing_reports() if str(row.get("id")) == document_id), None)
    local = index.get(document_id)
    if not report and not local:
        raise HTTPException(404, "Document not found.")
    document = _document_from_report(report or {}, local)
    document["metadata"] = (local or {}).get("metadata", {})
    document["entities"] = (local or {}).get("entities", [])
    document["lab_results"] = []
    if report:
        try:
            from db import supabase
            document["lab_results"] = (supabase.table("lab_results").select("id, investigation, result_value, result_unit, ref_interval_raw")
                                       .eq("report_id", document_id).execute().data or [])
        except Exception as exc:
            logger.warning("Could not load labs for document %s: %s", document_id, exc)
    return document


@app.get("/api/documents/{document_id}/file")
def get_document_file(document_id: str) -> FileResponse:
    # Authorization is enforced by proving the document belongs to the server-owned current scope.
    if document_id not in {str(row.get("id")) for row in _existing_reports()}:
        raise HTTPException(404, "Document not found.")
    record = _load_index().get(document_id)
    path = Path(record.get("file_path", "")) if record else None
    if not path or not path.is_file() or UPLOAD_DIR not in path.resolve().parents:
        raise HTTPException(404, "The original PDF is not available for this document.")
    return FileResponse(path, media_type="application/pdf", filename=record.get("filename", "document.pdf"))


@app.get("/api/documents/{document_id}/status")
def get_document_status(document_id: str) -> dict[str, Any]:
    record = _load_index().get(document_id)
    if not record:
        if any(str(row.get("id")) == document_id for row in _existing_reports()):
            return {"id": document_id, "status": "processed"}
        raise HTTPException(404, "Document not found.")
    return {"id": document_id, "status": record.get("status", "processed"), "error": record.get("error")}


@app.post("/api/documents/upload", status_code=201)
async def upload_document(file: UploadFile = File(...)) -> dict[str, Any]:
    filename = Path(file.filename or "document.pdf").name
    if not filename.lower().endswith(".pdf") or file.content_type not in {"application/pdf", "application/octet-stream", None}:
        raise HTTPException(415, "Please upload a PDF document.")
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    upload_id = str(uuid.uuid4())
    path = UPLOAD_DIR / f"{upload_id}.pdf"
    size = 0
    try:
        with path.open("wb") as destination:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    destination.close()
                    path.unlink(missing_ok=True)
                    raise HTTPException(413, f"PDF exceeds the {MAX_UPLOAD_BYTES // 1024 // 1024} MB limit.")
                destination.write(chunk)
        # A lightweight signature check guards against renamed non-PDF uploads.
        if path.read_bytes()[:5] != b"%PDF-":
            path.unlink(missing_ok=True)
            raise HTTPException(415, "The uploaded file is not a valid PDF.")

        # Existing protected ingestion sequence: OCR -> metadata/NER -> SQL -> vector storage.
        from preprocessing import ProcessFile
        from db import save_report
        from embedding import embed_report, embed_lab_results
        fields, lab_df, clean_text, entities = ProcessFile(str(path))
        report_id, patient_id = save_report(fields, lab_df, clean_text, external_patient_id=EXTERNAL_PATIENT_ID)
        if str(patient_id) != _patient_id():
            # Do not expose an upload if scope configuration changes during processing.
            raise RuntimeError("Ingestion returned a patient outside the application scope.")
        embed_report(str(report_id), clean_text, fields, entities, patient_id=str(patient_id))
        embed_lab_results(str(report_id), lab_df, fields, patient_id=str(patient_id))
        metadata = {name: field.value for name, field in fields.__dict__.items() if field.value not in (None, "")}
        index = _load_index()
        index[str(report_id)] = {"id": str(report_id), "filename": filename, "size": size, "uploaded_at": _utc_now(), "status": "processed", "file_path": str(path), "metadata": metadata, "entities": entities}
        _save_index(index)
        return _document_from_report({"id": report_id, **metadata}, index[str(report_id)])
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Pipeline failed for upload %s", upload_id)
        index = _load_index()
        index[upload_id] = {"id": upload_id, "filename": filename, "size": size, "uploaded_at": _utc_now(), "status": "failed", "error": "Processing could not be completed.", "file_path": str(path)}
        _save_index(index)
        raise HTTPException(502, "Document processing failed. Your PDF was retained so you can retry later.") from exc
    finally:
        await file.close()


@app.post("/api/chat")
def chat(payload: ChatRequest) -> dict[str, Any]:
    try:
        from output import answer_question
        result = answer_question(payload.question.strip(), _patient_id())
        data = result.to_dict()
        return {"answer": data["answer"], "sources": data["evidence_used"], "evidence_sufficient": data["evidence_sufficient"], "uncertainty": data["uncertainty"]}
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except Exception as exc:
        logger.exception("Chat request failed")
        raise HTTPException(502, "ClinicalAI could not answer right now. Please try again.") from exc


@app.get("/api/chat/history")
def chat_history() -> list[ChatMessage]:
    # The existing RAG pipeline has no conversation persistence; the UI owns ephemeral history.
    return []


app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="frontend")
