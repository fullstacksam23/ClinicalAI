from __future__ import annotations

import json
import logging
import os
import sys
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent / ".env")

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

# The pipeline modules use script-style imports (for example, ``from db``).
# Add their directory before importing the FastAPI adapter's dependencies.
PIPELINE_DIR = Path(__file__).resolve().parent
if str(PIPELINE_DIR) not in sys.path:
    sys.path.insert(0, str(PIPELINE_DIR))

from preprocessing import ProcessFile
from db import save_report
from embedding import embed_report, embed_lab_results, delete_report_vectors
from output import answer_question
from summarization import _generate_report_summary
# The established pipeline's credentials live beside its modules in
# ``server/.env``. Resolve that file explicitly so starting Uvicorn from the
# repository root exposes the identical configuration to db.py and the other
# existing pipeline modules.
import asyncio
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger("clinicalai.api")

ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = Path(__file__).resolve().parent / "static"
UPLOAD_DIR = Path(os.getenv("CLINICALAI_UPLOAD_DIR", str(ROOT / "data" / "uploads"))).resolve()
INDEX_PATH = UPLOAD_DIR / "documents.json"
CONVERSATIONS_PATH = UPLOAD_DIR / "conversations.json"
MAX_UPLOAD_BYTES = int(os.getenv("CLINICALAI_MAX_UPLOAD_MB", "25")) * 1024 * 1024
# Prototype mode intentionally uses one fixed patient scope. The database UUID
# is still resolved from Supabase so foreign-key ownership remains valid after
# the database is recreated.
EXTERNAL_PATIENT_ID = "test_patient_001"
HARDCODED_PATIENT_NAME = "Test Patient"


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=6000)
    conversation_id: str | None = None


class ChatMessage(BaseModel):
    role: str
    content: str
    created_at: str
    sources: list[dict[str, Any]] = []


class DoctorSummaryRequest(BaseModel):
    date_from: str | None = None
    date_to: str | None = None
    questions: list[str] = Field(default_factory=list, max_length=10)

    @field_validator("date_from", "date_to")
    @classmethod
    def validate_date(cls, value: str | None) -> str | None:
        if value is not None:
            try:
                datetime.strptime(value, "%Y-%m-%d")
            except ValueError as exc:
                raise ValueError("Dates must use YYYY-MM-DD format.") from exc
        return value

    @field_validator("questions")
    @classmethod
    def validate_questions(cls, value: list[str]) -> list[str]:
        if any(len(question) > 300 for question in value):
            raise ValueError("Each question must be 300 characters or fewer.")
        return value


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


def _load_conversations() -> dict[str, dict[str, Any]]:
    if not CONVERSATIONS_PATH.exists():
        return {}
    try:
        value = json.loads(CONVERSATIONS_PATH.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        logger.warning("Could not read conversation store", exc_info=True)
        return {}


def _save_conversations(conversations: dict[str, dict[str, Any]]) -> None:
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    temporary = CONVERSATIONS_PATH.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(conversations, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    temporary.replace(CONVERSATIONS_PATH)


def _conversation_for_patient(
    conversation: dict[str, Any],
    patient_id: str,
) -> bool:
    return str(conversation.get("patient_id")) == str(patient_id)


def _patient_id() -> str:
    """Resolve the sole browser session's server-controlled patient scope."""
    try:
        from db import get_or_create_patient, supabase
        result = (supabase.table("patients").select("id")
                  .eq("external_patient_id", EXTERNAL_PATIENT_ID).limit(1).execute())
        if result.data:
            return str(result.data[0]["id"])
        # The prototype database may be intentionally empty. Recreate the
        # fixed scoped patient so read endpoints return empty data instead of
        # failing with a misleading service-unavailable response.
        return str(get_or_create_patient(
            HARDCODED_PATIENT_NAME,
            external_patient_id=EXTERNAL_PATIENT_ID,
        ))
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
        "extra_metadata": report.get("extra_metadata") or {},
        "summary": report.get("summary"),
        "summary_status": report.get("summary_status") or ("completed" if report.get("summary") else "pending"),
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
    patient_id = _patient_id()
    index = _load_index()
    from db import get_report_by_id
    report = get_report_by_id(document_id, patient_id)
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
    patient_id = _patient_id()
    from db import get_report_by_id
    if not get_report_by_id(document_id, patient_id):
        raise HTTPException(404, "Document not found.")

    record = _load_index().get(document_id)
    path = Path(record.get("file_path", "")) if record else None

    if not path or not path.is_file() or UPLOAD_DIR not in path.resolve().parents:
        raise HTTPException(404, "The original PDF is not available for this document.")

    return FileResponse(
        path=path,
        media_type="application/pdf",
        filename=record.get("filename", "document.pdf"),
        content_disposition_type="inline",
    )

@app.get("/api/documents/{document_id}/status")
def get_document_status(document_id: str) -> dict[str, Any]:
    record = _load_index().get(document_id)
    if not record:
        patient_id = _patient_id()
        from db import get_report_by_id
        if get_report_by_id(document_id, patient_id):
            return {"id": document_id, "status": "processed"}
        raise HTTPException(404, "Document not found.")
    return {"id": document_id, "status": record.get("status", "processed"), "error": record.get("error")}


@app.get("/api/lab-trends")
def get_lab_trends(
    analytes: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> dict[str, Any]:
    patient_id = _patient_id()
    from db import get_lab_trend

    analyte_list = [a.strip() for a in analytes.split(",")] if analytes else None
    return get_lab_trend(patient_id, analyte_list, date_from, date_to)


@app.get("/api/lab-results")
def list_lab_results() -> list[dict[str, Any]]:
    patient_id = _patient_id()
    from db import get_all_patient_labs
    return get_all_patient_labs(patient_id)


@app.get("/api/timeline")
async def list_timeline_events(
    types: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> dict[str, list[dict[str, Any]]]:
    allowed_types = {"report", "abnormal_lab"}
    selected_types = None
    if types:
        selected_types = [item.strip() for item in types.split(",") if item.strip()]
        invalid_types = set(selected_types) - allowed_types
        if invalid_types:
            raise HTTPException(422, "types must contain only report or abnormal_lab.")
        selected_types = list(dict.fromkeys(selected_types))

    for name, value in (("date_from", date_from), ("date_to", date_to)):
        if value:
            try:
                datetime.strptime(value, "%Y-%m-%d")
            except ValueError as exc:
                raise HTTPException(422, f"{name} must use YYYY-MM-DD format.") from exc

    if date_from and date_to and date_from > date_to:
        raise HTTPException(422, "date_from cannot be later than date_to.")

    patient_id = _patient_id()
    from db import get_timeline_events

    events = await asyncio.to_thread(
        get_timeline_events,
        patient_id,
        date_from,
        date_to,
    )
    if selected_types:
        events = [event for event in events if event["type"] in selected_types]
    return {"events": events}


@app.post("/api/exports/doctor-summary")
async def export_doctor_summary(payload: DoctorSummaryRequest) -> Response:
    today = date.today()
    date_from = payload.date_from or (today - timedelta(days=365)).isoformat()
    date_to = payload.date_to or today.isoformat()
    if date_from > date_to:
        raise HTTPException(422, "date_from cannot be later than date_to.")

    patient_id = _patient_id()

    def build_export() -> bytes | None:
        from db import get_doctor_summary_data
        from export_pdf import build_doctor_summary_pdf

        data = get_doctor_summary_data(patient_id, date_from, date_to, payload.questions)
        if not data["reports"]:
            return None
        data["generated_date"] = today.isoformat()
        return build_doctor_summary_pdf(data)

    pdf = await asyncio.to_thread(build_export)
    if pdf is None:
        raise HTTPException(404, "No reports were found in the selected date range.")

    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="doctor-visit-summary-{today.isoformat()}.pdf"',
            "Cache-Control": "no-store",
        },
    )


@app.post("/api/documents/{document_id}/summary/retry")
def retry_summary(document_id: str) -> dict[str, Any]:
    patient_id = _patient_id()
    from db import get_report_summary_input, update_report_summary

    summary_input = get_report_summary_input(document_id, patient_id)
    if not summary_input:
        raise HTTPException(404, "Document not found.")

    local = _load_index().get(document_id) or {}
    update_report_summary(document_id, patient_id, "pending")
    summary_status = _generate_report_summary(
        document_id,
        str(patient_id),
        summary_input["clean_text"],
        local.get("entities", []),
        summary_input["lab_results"],
    )
    return {
        "id": document_id,
        "summary": summary_status["summary"],
        "summary_status": summary_status["status"],
    }


@app.delete("/api/documents/{document_id}", status_code=204)
def delete_document(document_id: str) -> None:
    patient_id = _patient_id()
    index = _load_index()
    local = index.get(document_id)

    from db import get_report_by_id, delete_report
    report = get_report_by_id(document_id, patient_id)

    # Failed uploads have no Supabase/vector record; they are still removable.
    if not report and not local:
        raise HTTPException(404, "Document not found.")

    if report:
        try:
            delete_report_vectors(document_id)
            if not delete_report(document_id, patient_id):
                raise HTTPException(404, "Document not found.")
        except HTTPException:
            raise
        except Exception as exc:
            logger.exception("Could not delete document %s", document_id)
            raise HTTPException(502, "Document could not be deleted completely.") from exc

    if local:
        path = Path(local.get("file_path", ""))
        if path and path.is_file() and UPLOAD_DIR in path.resolve().parents:
            path.unlink()
        index.pop(document_id, None)
        _save_index(index)


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

        fields, lab_df, clean_text, entities = await asyncio.to_thread(
            ProcessFile,
            str(path),
        )
        report_id, patient_id = await asyncio.to_thread(
            save_report,
            fields,
            lab_df,
            clean_text,
            external_patient_id=EXTERNAL_PATIENT_ID,
        )
        if str(patient_id) != _patient_id():
            # Do not expose an upload if scope configuration changes during processing.
            raise RuntimeError("Ingestion returned a patient outside the application scope.")
        def run_embeddings():
            embed_report(
                str(report_id),
                clean_text,
                fields,
                entities,
                patient_id=str(patient_id),
            )

            embed_lab_results(
                str(report_id),
                lab_df,
                fields,
                patient_id=str(patient_id),
            )
        def run_summary():
            return _generate_report_summary(
                str(report_id),
                str(patient_id),
                clean_text,
                entities,
                lab_df.to_dict(orient="records") if not lab_df.empty else [],
            )
        embedding_future = asyncio.create_task(asyncio.to_thread(run_embeddings))
        summary_future = asyncio.create_task(asyncio.to_thread(run_summary))

        metadata = {name: field.value for name, field in fields.__dict__.items() if field.value not in (None, "")}
        index = _load_index()
        index[str(report_id)] = {"id": str(report_id), "filename": filename, "size": size, "uploaded_at": _utc_now(), "status": "processed", "file_path": str(path), "metadata": metadata, "entities": entities}
        _save_index(index)

        # Wait for both workers without blocking the async event loop. A
        # summary failure must not hide the already-persisted report.
        _, summary_result = await asyncio.gather(
            embedding_future,
            summary_future,
            return_exceptions=True,
        )
        if isinstance(summary_result, Exception):
            logger.error("Summary generation failed for report %s: %s", report_id, summary_result)
            from db import update_report_summary
            try:
                update_report_summary(str(report_id), str(patient_id), "failed")
            except Exception as update_error:
                logger.error("Could not persist failed summary status for report %s: %s", report_id, update_error)
            summary_result = {"summary": None, "status": "failed"}

        return _document_from_report(
            {
                "id": report_id,
                **metadata,
                "summary": summary_result["summary"],
                "summary_status": summary_result["status"],
            },
            index[str(report_id)],
        )
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


@app.get("/api/conversations")
def list_conversations() -> list[dict[str, Any]]:
    patient_id = _patient_id()
    conversations = _load_conversations().values()
    visible = [
        {
            "id": str(c["id"]),
            "title": c.get("title") or "New chat",
            "created_at": c.get("created_at"),
            "updated_at": c.get("updated_at"),
            "message_count": len(c.get("messages", [])),
        }
        for c in conversations
        if _conversation_for_patient(c, patient_id)
    ]
    return sorted(
        visible,
        key=lambda item: item.get("updated_at") or item.get("created_at") or "",
        reverse=True,
    )


@app.get("/api/conversations/{conversation_id}")
def get_conversation(conversation_id: str) -> dict[str, Any]:
    patient_id = _patient_id()
    conversation = _load_conversations().get(conversation_id)
    if not conversation or not _conversation_for_patient(conversation, patient_id):
        raise HTTPException(404, "Conversation not found.")
    return {
        "id": str(conversation["id"]),
        "title": conversation.get("title") or "New chat",
        "created_at": conversation.get("created_at"),
        "updated_at": conversation.get("updated_at"),
        "messages": conversation.get("messages", []),
    }


@app.delete("/api/conversations/{conversation_id}", status_code=204)
def delete_conversation(conversation_id: str) -> None:
    patient_id = _patient_id()
    conversations = _load_conversations()
    conversation = conversations.get(conversation_id)
    if not conversation or not _conversation_for_patient(conversation, patient_id):
        raise HTTPException(404, "Conversation not found.")
    del conversations[conversation_id]
    _save_conversations(conversations)


@app.post("/api/chat")
def chat(payload: ChatRequest) -> dict[str, Any]:
    patient_id = _patient_id()
    question = payload.question.strip()
    if not question:
        raise HTTPException(422, "Question cannot be empty.")

    conversations = _load_conversations()
    conversation_id = payload.conversation_id

    if conversation_id:
        conversation = conversations.get(conversation_id)
        if not conversation or not _conversation_for_patient(conversation, patient_id):
            raise HTTPException(404, "Conversation not found.")
    else:
        conversation_id = str(uuid.uuid4())
        now = _utc_now()
        conversation = {
            "id": conversation_id,
            "patient_id": str(patient_id),
            "title": question[:80] + ("…" if len(question) > 80 else ""),
            "created_at": now,
            "updated_at": now,
            "messages": [],
        }
        conversations[conversation_id] = conversation

    try:
        result = answer_question(question, patient_id)
        data = result.to_dict()
        sources = data["evidence_used"]

        now = _utc_now()
        conversation["messages"].append(
            {
                "role": "user",
                "content": question,
                "created_at": now,
                "sources": [],
            }
        )
        conversation["messages"].append(
            {
                "role": "assistant",
                "content": data["answer"],
                "created_at": now,
                "sources": sources,
            }
        )
        conversation["updated_at"] = now

        if not conversation.get("title") or conversation.get("title") == "New chat":
            conversation["title"] = question[:80] + ("…" if len(question) > 80 else "")

        _save_conversations(conversations)

        return {
            "conversation_id": conversation_id,
            "answer": data["answer"],
            "sources": sources,
            "evidence_sufficient": data["evidence_sufficient"],
            "uncertainty": data["uncertainty"],
        }
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Chat request failed")
        raise HTTPException(502, "ClinicalAI could not answer right now. Please try again.") from exc


@app.get("/api/chat/history")
def chat_history() -> list[ChatMessage]:
    # Backwards-compatible endpoint: return messages from the newest saved conversation.
    patient_id = _patient_id()
    visible = [
        c for c in _load_conversations().values()
        if _conversation_for_patient(c, patient_id)
    ]
    if not visible:
        return []
    newest = max(
        visible,
        key=lambda c: c.get("updated_at") or c.get("created_at") or "",
    )
    return newest.get("messages", [])


app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="frontend")
