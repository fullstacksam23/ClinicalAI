# ClinicalAI Agent Guidelines

## Project Overview

ClinicalAI is a Python FastAPI application with a dependency-free static frontend.

The main report pipeline is:

`PDF -> server/preprocessing.py -> server/db.py -> server/embedding.py -> Qdrant`

`preprocessing.ProcessFile` performs PaddleOCR, NER, and metadata extraction. Supabase stores patients, reports, lab results, provenance, and persisted summaries. Qdrant stores report and lab embeddings. The browser code lives in `server/static/` and is served by FastAPI.

## Important Rules

- Keep patient scope server-controlled. Never accept a patient ID from the browser or trust one supplied by an LLM.
- Preserve the existing pipeline entry points instead of reimplementing OCR, NER, retrieval, or embedding behavior in the API layer.
- Keep report data and summary data in Supabase. `data/uploads/documents.json` is only local UI/file association state.
- Preserve original OCR labels and clinical values when storing extracted data. Do not silently overwrite source facts.
- Treat generated summaries and chat answers as grounded explanations, not diagnoses or treatment advice.
- Escape user/model text before inserting it into frontend HTML.
- Do not commit credentials, `.env` files, model caches, `qdrant_storage/`, `server/myenv/`, or uploaded PDFs.
- Avoid unrelated refactors and do not revert existing user changes.

## Backend Conventions

- `server/app.py` is the FastAPI adapter and owns HTTP validation, patient scope resolution, upload flow, and response shaping.
- Pipeline modules use script-style imports such as `from db import ...`; keep the `server` directory on `sys.path` before importing them when changing startup code.
- Summary generation is persisted with `summary` and `summary_status` values of `pending`, `completed`, or `failed`.
- Retry is intentionally synchronous and should return the actual generated summary and final status.
- Any summary-generation failure must not discard an already-persisted report; persist `failed` and let the UI show retry behavior.
- Supabase writes must include patient/report ownership filters for update, read, and delete operations.

## Frontend Conventions

- Use the existing DM Sans/Newsreader typography, CSS variables, `.card`, `.detail-list`, `.viewer`, `.status`, and responsive layout patterns.
- The frontend is plain ES modules with no bundler or package manager. Keep browser changes in `server/static/js/` and styles in `server/static/styles.css`.
- Report detail routes use `#documents/{id}`. Upload success navigates to the new report detail page.
- Chat answers support citations and basic Markdown through `server/static/js/citations.js`; preserve citation recovery and HTML escaping when changing answer rendering.
- Use `esc()` for interpolated content and avoid rendering raw model output as HTML.

## Run and Validate

Use the project virtual environment on Windows:

```powershell
.\server\myenv\Scripts\python.exe -m compileall server
node --check server/static/js/documents.js
node --check server/static/js/chat.js
node --check server/static/js/citations.js
```

Start the application from the repository root with:

```powershell
.\server\myenv\Scripts\python.exe -m uvicorn server.app:app --reload
```

The application requires configured PaddleOCR, Gemini, Supabase, and Qdrant services for an end-to-end upload or chat test. Start Qdrant when testing retrieval or embeddings:

```powershell
docker start qdrant
```

Apply the summary columns and status constraint to the Supabase `reports` table before testing report summaries. See `README.md` for the current SQL and API list.

## Change Workflow

1. Read the local implementation and nearby call sites before editing.
2. Make the smallest change that preserves existing public APIs and data flow.
3. Run a focused validation command immediately after editing.
4. Run backend compilation and relevant frontend syntax checks before finishing.
5. Report external-service or schema prerequisites clearly when they prevent a full smoke test.
