# ClinicalAI - LLM Project Context

## How to Use This Document

This document is a handoff context for another coding or reasoning model working on the ClinicalAI repository. Treat the repository source code as authoritative when it conflicts with this document. Preserve existing user changes and make focused edits only.

When modifying this project:

- Inspect the local implementation and nearby call sites before editing.
- Preserve the established ingestion, retrieval, embedding, and persistence entry points.
- Keep patient scope controlled by the server. Never accept a patient ID from the browser or from an LLM.
- Keep report data and summaries in Supabase; `data/uploads/documents.json` is only local file/UI association state.
- Escape all user- or model-controlled text before inserting it into frontend HTML.
- Do not commit credentials, `.env` files, model caches, uploaded PDFs, `qdrant_storage/`, or `server/myenv/`.
- Avoid unrelated refactors.
- Run a focused validation command immediately after edits, followed by the broader syntax/import checks.

## Project Summary

ClinicalAI is a local full-stack clinical-record application. A user uploads scanned medical PDFs, the system extracts OCR text, metadata, entities, and laboratory tables, stores the structured report in Supabase, embeds report and lab text in Qdrant, and exposes a browser UI for document review, lab trends, health history, and grounded questions.

The application is a Python FastAPI backend with a dependency-free static frontend made from HTML, CSS, and ES modules. There is no frontend bundler and no npm project. The same FastAPI process serves the API and `server/static/`.

The main pipeline is:

```text
PDF upload
  -> server/preprocessing.py: ProcessFile
     -> PaddleOCR document parsing
     -> table extraction with BeautifulSoup/pandas
     -> biomedical NER
     -> Gemini structured metadata extraction
  -> server/db.py: save_report
     -> Supabase patients, reports, provenance, lab_results
  -> server/embedding.py
     -> MedCPT report embeddings
     -> MedCPT lab-result sentence embeddings
     -> Qdrant clinical_reports collection
  -> summary generation and API response
```

Chat follows a separate but connected path:

```text
POST /api/chat
  -> server/output.py: answer_question
  -> server/retrieval.py: ClinicalRetrievalEngine
     -> Gemini retrieval plan
     -> patient-scoped structured Supabase retrieval
     -> patient-scoped Qdrant semantic retrieval
  -> Gemini grounded answer generation
  -> plain-text citations
  -> frontend citation chips linking to document routes
```

## Repository Layout

```text
AGENTS.md                 Local engineering rules and validation commands
README.md                 User-facing setup and architecture notes
LLM_CONTEXT.md            This handoff document
requirements.txt          Python dependencies

data/uploads/
  documents.json          Local file/UI association index; not canonical report storage
  conversations.json     Local patient-scoped conversation history

server/
  app.py                  FastAPI adapter, HTTP validation, patient scope, upload flow
  db.py                   Supabase persistence and structured query functions
  embedding.py            MedCPT embedding and Qdrant storage/retrieval
  metadata.py             Gemini structured report-field extraction and validation
  preprocessing.py        PaddleOCR, table parsing, NER, metadata pipeline
  retrieval.py             Two-path patient-scoped retrieval orchestration
  output.py               Grounded answer generation and evidence sanitization
  summarization.py        Patient-facing report summary generation
  static/
    index.html            Shell, navigation, Chart.js CDN, module entry point
    app.js                Starts route handling and global handlers
    styles.css             Existing visual system and responsive styles
    js/
      state.js             Shared frontend state and view/title references
      router.js            Hash route parsing and view dispatch
      events.js            Global click handling
      utils.js             API wrapper, HTML escaping, dates, toast
      documents.js         Document list/detail/upload/summary polling
      health.js            All patient lab results view
      trends.js            Trend API and Chart.js rendering
      chat.js              Chat view, send/reset behavior
      citations.js         Markdown/citation rendering with escaping
      pdf.js               PDF blob loading and cleanup
      sidebar.js           Conversation list rendering
```

## Backend Modules

### `server/app.py`

This is the HTTP adapter and owns:

- FastAPI application creation and static-file mounting.
- CORS configuration.
- Server-controlled prototype patient resolution.
- PDF filename/content-type/signature/size validation.
- Upload persistence to `data/uploads/`.
- Patient-scoped document, lab, trend, conversation, and chat routes.
- Response shaping for the frontend.

The application inserts `server/` into `sys.path` before importing pipeline modules because those modules use script-style imports such as `from db import ...` and `from embedding import ...`.

The prototype patient is resolved using `CLINICALAI_EXTERNAL_PATIENT_ID`, defaulting to `test_patient_001`. `_patient_id()` looks up the corresponding Supabase patient ID. The browser never submits a patient ID.

#### Upload behavior

`POST /api/documents/upload` is an async endpoint. It:

1. Streams the uploaded file to a UUID-named PDF under `data/uploads/`.
2. Enforces the configured size limit and checks the `%PDF-` signature.
3. Runs `ProcessFile` in a worker thread with `asyncio.to_thread`.
4. Runs `save_report` in a worker thread with `asyncio.to_thread`.
5. Verifies that the returned patient ID still matches the server-controlled scope.
6. Starts embedding and summary work in worker threads.
7. Registers the local document index entry after Supabase persistence succeeds.
8. Waits for embedding and summary tasks without blocking the event loop.
9. If summary generation fails, persists `summary_status=failed` while retaining the report and local file association.
10. Returns the shaped document response.

Embedding failures still fail the request after Supabase persistence, so callers should inspect the database/local index if diagnosing partial ingestion. The report is not silently deleted.

### `server/preprocessing.py`

`ProcessFile(file_path, patient_id=None)` is the established ingestion entry point. The optional `patient_id` remains for backward compatibility but is currently unused by the extraction pipeline.

It loads OCR and NER models once at import time. It uses a shared PaddleOCR client and must not close that shared client after processing a document. It retries PaddleOCR queue-full errors, then:

- Parses OCR pages and blocks.
- Keeps useful header/footnote/prose blocks.
- Converts HTML tables into pandas DataFrames.
- Detects headerless table cases.
- Produces `blocks_by_label`, `lab_df`, and `clean_text`.
- Runs biomedical NER on clean text.
- Calls `metadata.extract_report_fields`.

### `server/metadata.py`

This module performs structured metadata extraction with Gemini, followed by deterministic validation and NER cross-checking. It returns a `ReportFields` dataclass whose fields are `ExtractedField` objects containing:

- `value`
- `extraction_method`
- `flagged_for_review`
- `note`

Fields include patient name, age, sex, report date, reported date, referring doctor, and report type. Dates are normalized and validated; age and sex have bounds/allow-list checks. Do not replace this with ad hoc regex parsing without preserving provenance and validation.

### `server/db.py`

This is the Supabase persistence layer. Important tables and relationships:

- `patients`: patient identity mapping, including `external_patient_id`.
- `reports`: report-level metadata, clean OCR text, dates, patient scope, summary, and summary status.
- `lab_results`: structured lab rows linked by `report_id`.
- `extraction_provenance`: per-field extraction method/review metadata linked by `report_id`.

`save_report(fields, lab_df, clean_text, raw_ocr_job_id=None, external_patient_id=None)` resolves the configured external patient, inserts the report, provenance, and lab results, and returns `(report_id, patient_id)`.

Lab matching uses normalized case/punctuation-insensitive aliases. Investigation aliases include `Investigation`, `Test`, `Test Name`, `Parameter`, `Analyte`, `Exam`, and `Description`. Result aliases include `Result`, `Value`, `Observed Value`, `Measured Value`, `Result Value`, and `Findings`. Units, reference intervals, and methods have similar aliases.

Important query helpers:

- `get_reports_list(patient_id, ...)`: newest-first report inventory.
- `get_report_by_id(report_id, patient_id)`: targeted patient-owned report fetch.
- `get_all_patient_labs(patient_id)`: all lab results across the patient’s reports, joined with report date/type.
- `get_lab_values(...)`, `get_abnormal_results(...)`, `get_latest_lab_values(...)`, `get_lab_trend(...)`: structured clinical queries.
- `get_report_summary_input(...)`: patient-scoped summary retry input.
- `update_report_summary(...)`: patient-scoped summary update.
- `delete_report(...)`: patient-scoped deletion of dependent rows and report.

Every read, update, and delete involving patient-owned data must retain ownership filters.

### `server/embedding.py`

Uses two MedCPT encoders:

- `ncbi/MedCPT-Query-Encoder` for questions.
- `ncbi/MedCPT-Article-Encoder` for report/lab text.

The vector size is 768. Qdrant is configured at `http://localhost:6333`, collection `clinical_reports`.

Collection initialization is guarded. If Qdrant is temporarily unavailable during import, the application logs a warning and continues; operations retry/fail when actually invoked. The currently verified collection name is `clinical_reports`.

`embed_report` chunks prose and stores payload metadata including:

- `patient_id`
- `report_id`
- patient/report metadata
- report date and timestamp
- entity types
- metadata review flag
- `chunk_type=prose`
- `chunk_text`

`embed_lab_results` converts each usable structured lab row into a short natural-language sentence such as a test name, value, reference interval, and report date. It stores `chunk_type=lab_result` plus structured lab payload fields.

`PatientScopedRetriever` applies exactly one Qdrant data-isolation filter: the trusted `patient_id`. It does not accept LLM-generated date, doctor, report type, or report ID filters. `delete_report_vectors` deletes all points by `report_id`.

### `server/retrieval.py`

This module enforces the two-path retrieval design.

1. `RetrievalPlanner` asks Gemini for an allow-listed operation, analytes, date filters, and semantic search query. The schema intentionally has no `patient_id`.
2. `PatientScopedSQLRetriever` obtains the patient’s report inventory from Supabase. A second Gemini selector may choose report IDs only from that already-authorized inventory. The backend intersects requested IDs with the allowed set before querying lab results.
3. `PatientScopedVectorRetriever` searches Qdrant through `PatientScopedRetriever`, whose patient scope is fixed at construction.
4. `ClinicalRetrievalEngine.retrieve` always runs both structured and vector retrieval. The planner cannot choose SQL versus vector mode.

Allow-listed structured operations are:

- `list_reports`
- `lab_values`
- `abnormal_labs`
- `latest_labs`
- `lab_trend`
- `none`

The LLM is an untrusted planner. It must never receive authority to select a patient or invent report IDs.

### `server/output.py`

`answer_question(question, patient_id)` calls `ClinicalRetrievalEngine`, serializes bounded evidence, and asks Gemini to produce a structured clinical answer. The answer model does not retrieve data and does not receive `patient_id`.

Structured evidence is authoritative for exact lab values, units, reference ranges, dates, and IDs. Vector evidence is authoritative for report narrative, impressions, and findings. Conflicts must be stated rather than silently reconciled.

Answers are required to use plain-text citation tokens only:

```text
[report:<report_id>]
[lab:<lab_result_id>]
```

The frontend converts these tokens into clickable citation controls. Do not make the model emit HTML.

### `server/summarization.py`

Report summaries use `CLINICAL_SUMMARY_MODEL`, defaulting to `gemini-3.5-flash-lite`. Summaries are patient-facing explanations, not diagnoses or treatment advice. They are persisted in `reports.summary` with `summary_status` of `pending`, `completed`, or `failed`.

Summary generation receives cleaned report text, extracted entities, and extracted lab facts. It should only state supplied facts and must remain concise.

## API Contract

All routes are under `/api`.

### Health and documents

```text
GET    /api/health
GET    /api/documents
GET    /api/documents/{id}
GET    /api/documents/{id}/file
GET    /api/documents/{id}/status
POST   /api/documents/upload       multipart field: file
DELETE /api/documents/{id}
POST   /api/documents/{id}/summary/retry
```

Document reads and deletes are patient-scoped through `get_report_by_id`. A local failed-upload index record may remain visible/removable even when no Supabase report exists.

### Structured clinical data

```text
GET /api/lab-results
GET /api/lab-trends?analytes=...&date_from=...&date_to=...
```

`/api/lab-results` returns all patient lab findings in one joined query. Each item includes IDs, investigation, result values/numeric values, units, raw/reference boundaries, method, report date, and report type.

### Chat conversations

```text
GET    /api/conversations
GET    /api/conversations/{id}
DELETE /api/conversations/{id}
POST   /api/chat
GET    /api/chat/history
```

The current implementation persists conversations in `data/uploads/conversations.json`, scoped by the server-resolved patient. `POST /api/chat` accepts:

```json
{
  "question": "What was my latest HbA1c?",
  "conversation_id": "optional-existing-id"
}
```

The response includes `conversation_id`, `answer`, `sources`, `evidence_sufficient`, and `uncertainty`.

## Frontend Behavior

The static app uses hash routing:

- `#dashboard`
- `#documents`
- `#documents/{id}`
- `#trends`
- `#trends?analytes=...&date_from=...&date_to=...`
- `#health`
- `#chat`
- `#chat/{conversation_id}`

`router.js` cleans PDF blob URLs and Chart.js instances before branching to a new route. `events.js` owns global click delegation, including document open/delete, navigation, chat selection, and new-chat reset.

Frontend security rules:

- Use `esc()` for interpolated values.
- Use `messageHtml()`/`citations.js` for answer Markdown and citations.
- Never inject raw model output as HTML.
- Preserve PDF URL cleanup and trend chart destruction when changing routes.

The health page makes one request to `/api/lab-results`; it must not reintroduce one document-detail request per report. Trends use `/api/lab-trends` and Chart.js. The trend reference-band calculation must preserve numeric zero as a valid lower boundary. Chat input expands to fit multiline text, and the new-chat control must clear the current view even when already on `#chat`.

## Configuration and External Services

Required runtime services/credentials:

- PaddleOCR cloud/model access and `PADDLE_OCR_TOKEN`.
- Gemini API key, `GEMINI_API_KEY`.
- Supabase URL and service-role key.
- Qdrant at `http://localhost:6333` unless code/config is changed.
- Local Python environment with packages from `requirements.txt`.

Important environment variables include:

```text
PADDLE_OCR_TOKEN
GEMINI_API_KEY
SUPABASE_URL
SUPABASE_SERVICE_ROLE_KEY
CLINICALAI_EXTERNAL_PATIENT_ID=test_patient_001
CLINICAL_SUMMARY_MODEL=gemini-3.5-flash-lite
CLINICAL_ROUTER_MODEL=gemini-2.5-flash-lite
CLINICAL_ANSWER_MODEL=gemini-2.5-flash
CLINICAL_METADATA_MODEL=gemini-3.5-flash-lite
CLINICALAI_UPLOAD_DIR=optional override
CLINICALAI_MAX_UPLOAD_MB=25 by default
CLINICALAI_CORS_ORIGINS=http://localhost:8000 by default
```

The current local Qdrant service has been verified reachable, and the `clinical_reports` collection exists. Qdrant can still be absent during import because collection initialization is intentionally resilient.

## Startup and Validation

From the repository root on Windows:

```powershell
.\server\myenv\Scripts\python.exe -m uvicorn server.app:app --reload
```

Open:

```text
http://localhost:8000
```

Recommended checks:

```powershell
.\server\myenv\Scripts\python.exe -m compileall -q -x "myenv" server
node --check server/static/js/documents.js
node --check server/static/js/chat.js
node --check server/static/js/citations.js
node --check server/static/js/trends.js
node --check server/static/js/health.js
node --check server/static/js/router.js
.\server\myenv\Scripts\python.exe -c "import server.app; print('app imported successfully')"
```

The import check loads OCR/NER/embedding models and may take time. It should succeed even when Qdrant is briefly unavailable, with a warning. A real upload requires configured PaddleOCR, Gemini, Supabase, Qdrant, and a valid PDF.

For report summaries, the existing Supabase `reports` table must contain:

```sql
alter table reports add column if not exists summary text;
alter table reports add column if not exists summary_status text not null default 'pending';
alter table reports drop constraint if exists reports_summary_status_check;
alter table reports add constraint reports_summary_status_check
  check (summary_status in ('pending', 'completed', 'failed'));
```

## Current Prototype Limitations and Risks

These are intentional or known constraints, not invitations for unrelated redesign:

- There is one server-configured prototype patient scope. Real authentication and user-to-patient mapping are still future work.
- Supabase uses the service-role key in the current prototype. Application-level patient filters are mandatory; production should add authenticated JWT/RLS enforcement.
- The upload endpoint performs OCR, persistence, embedding, and summary work in worker threads so the event loop remains responsive, but it currently waits for those worker tasks before returning the final response.
- Qdrant vector search is patient-wide and does not apply date/report/doctor filters. Structured retrieval handles exact report/lab selection.
- Conversation history is stored in a local JSON file rather than Supabase.
- Browser/manual upload and chat tests require live external services and a real PDF.
- `README.md` contains older wording in places; verify behavior against `server/app.py` and the relevant frontend modules before relying on it.

## Safe Change Patterns

### Adding a backend feature

1. Identify whether the behavior belongs in `app.py`, `db.py`, the established pipeline, or the frontend.
2. Keep `app.py` as an adapter; do not duplicate OCR, NER, retrieval, or embedding logic there.
3. Add patient ownership filters to every Supabase report/lab read, update, and delete.
4. Keep LLM schemas free of patient identity and validate any model-selected IDs against server-owned data.
5. Add or update a focused route/query test when practical.
6. Run compile/import checks.

### Changing ingestion

Preserve the output contract of `ProcessFile`: `(fields, lab_df, clean_text, entities)`. Preserve source OCR values and labels. If lab header aliases change, keep `db.py` and `embedding.py` behavior aligned.

### Changing retrieval or chat

Keep the two retrieval paths. Do not let the LLM choose patient scope, invent IDs, bypass structured retrieval, or generate HTML. Keep exact structured values ahead of semantic prose for numeric answers.

### Changing frontend rendering

Use existing CSS variables and components. Escape interpolated content. Preserve route cleanup, citation recovery, PDF cleanup, and responsive behavior. Run `node --check` for every changed module.

## Useful Task Prompt for Another LLM

```text
You are working on the ClinicalAI repository. Read LLM_CONTEXT.md and AGENTS.md first.

This is a Python FastAPI application with a dependency-free static ES-module frontend for patient-scoped clinical PDF ingestion and grounded chat. The canonical pipeline is:
PDF -> preprocessing.ProcessFile -> db.save_report -> embedding.embed_report/embed_lab_results -> Supabase and Qdrant.

Security invariants:
- Patient scope is server-controlled and never comes from the browser or an LLM.
- Every patient-owned Supabase read/update/delete is patient-filtered.
- Qdrant retrieval is filtered by the trusted patient_id only.
- LLMs plan retrieval and generate grounded explanations; they do not authorize access or invent IDs.
- Model/user text must be escaped before frontend HTML insertion.

Before editing, inspect the owning module and nearby call sites. Make the smallest focused change, preserve public pipeline entry points, run a focused check immediately, then run compileall and relevant node --check commands. Do not modify credentials, generated model data, uploaded PDFs, or qdrant_storage.

Task: [INSERT TASK HERE]
```
