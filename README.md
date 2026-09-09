# ClinicalAI

ClinicalAI is a local full-stack workspace for uploading scanned medical PDFs, reviewing their extracted information, and asking record-grounded questions.

## Architecture

The established pipeline remains the source of truth and is not reimplemented by the web layer:

`PDF → preprocessing.ProcessFile (Paddle OCR, NER, metadata extraction) → db.save_report (Supabase) → embedding.embed_report + embed_lab_results (Qdrant)`

For chat, `output.answer_question` calls the existing `ClinicalRetrievalEngine`. That engine always performs patient-scoped SQL retrieval and patient-scoped vector retrieval before the existing grounded answer generator runs. The API does not add a retrieval router.

`server/app.py` is deliberately a thin FastAPI adapter. It validates files, keeps the browser from supplying a patient ID, invokes those existing entry points, and stores only original-PDF/UI association data in `data/uploads/documents.json`. The original report and extracted medical data remain in the existing Supabase/Qdrant pipeline.

The responsive browser application is dependency-free static HTML/CSS/JS in `server/static/` and is served by the same FastAPI process.

## Run

1. Create and activate a Python 3.10+ virtual environment.

2. Install the dependencies:

   ```powershell
   pip install -r requirements.txt
   ```

3. Copy `.env.example` to `.env` and add the existing pipeline credentials: Paddle OCR, Gemini, Supabase, and a running Qdrant service. Retain `CLINICALAI_EXTERNAL_PATIENT_ID=test_patient_001` for the current single-patient prototype.

   Before uploading reports, add the summary fields to the existing Supabase `reports` table in the SQL editor:

   ```sql
   alter table reports add column if not exists summary text;
   alter table reports add column if not exists summary_status text not null default 'pending';
   alter table reports drop constraint if exists reports_summary_status_check;
   alter table reports add constraint reports_summary_status_check
      check (summary_status in ('pending', 'completed', 'failed'));
   ```

4. Start the product:

   ```powershell
   uvicorn server.app:app --reload
   ```

5. Open `http://localhost:8000`.

## API

- `GET /api/health`
- `GET /api/documents`
- `GET /api/documents/{id}`
- `GET /api/documents/{id}/file`
- `GET /api/documents/{id}/status`
- `POST /api/documents/upload` (multipart `file`, PDF, configurable 25 MB limit)
- `POST /api/documents/{id}/summary/retry`
- `POST /api/chat` (`{ "question": "..." }`)
- `GET /api/chat/history` (empty because the existing RAG layer does not persist conversations)

## Notes on patient scope

The client never submits a patient ID. Until authentication is added, the API resolves exactly one server-configured patient scope. Once auth is introduced, replace the prototype resolver and pass the authenticated user’s existing patient mapping into the pipeline’s `save_report(..., external_patient_id=...)`; do not accept an arbitrary browser patient ID.

## Verification

Run syntax checks without requiring model downloads or external services:

```powershell
python -m compileall server
```

An end-to-end upload/chat test requires valid configured external services and a real PDF, because the API intentionally does not mock OCR, SQL, vector storage, or answer generation.

Report summaries are generated in a FastAPI background task after OCR, NER, Supabase persistence, and vector indexing finish. The upload response is returned with `summary_status: pending`; the report detail view polls until the stored summary is `completed` or shows a retry action after `failed`. Summaries are read from Supabase on later loads and are never regenerated during page rendering.
