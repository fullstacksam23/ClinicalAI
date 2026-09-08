"""
Clinical AI retrieval layer
===========================

Two retrieval paths, ALWAYS executed for every user question:

1. PATIENT-SCOPED STRUCTURED / SQL-LIKE RETRIEVAL
   - The LLM chooses only an allow-listed structured operation and filters.
   - The application owns patient_id.
   - patient_id is NOT part of the LLM schema and cannot be changed by the model.
   - SQL/database access is implemented with the existing db.py query functions,
     which all receive the application-owned patient_id.

2. PATIENT-SCOPED VECTOR RETRIEVAL
   - Uses the existing PatientScopedRetriever from embedding.py.
   - The application-owned patient_id is passed to the retriever constructor.
   - Qdrant is filtered ONLY by that server-controlled patient_id.
   - No date, doctor, report type, report ID, or other metadata filter is used.

There is NO LLM choice between SQL, vector, or hybrid. Both paths always run.
The LLM only helps choose the most useful structured SQL operation/filters and
rewrites the semantic search query.

SECURITY MODEL
--------------
The LLM is an untrusted planner. It never gets to choose patient_id.
The trusted application layer supplies patient_id from the authenticated
request context (or TEST_PATIENT_ID while prototyping).

For production, replace the prototype patient ID with your authenticated
user/patient mapping and enable database RLS as a second, independent layer.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, asdict
from typing import Any, Dict, List, Literal, Optional

from dotenv import load_dotenv
from groq import Groq

from db import (
    get_abnormal_results,
    get_lab_trend,
    get_latest_lab_values,
    get_lab_values,
    get_reports_list,
)
from embedding import PatientScopedRetriever

load_dotenv()

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

ROUTER_MODEL = os.getenv("CLINICAL_ROUTER_MODEL", "openai/gpt-oss-20b")
ROUTER_MAX_QUESTION_CHARS = 6000
DEFAULT_VECTOR_RESULTS = 6
DEFAULT_VECTOR_SCORE_THRESHOLD = 0.30

# Used only by the local test harness below. In your real API request, pass
# the authenticated patient's ID into ClinicalRetrievalEngine(...).
TEST_PATIENT_ID = os.getenv("TEST_PATIENT_ID")

_groq = Groq()


# ---------------------------------------------------------------------------
# Planner schema
# ---------------------------------------------------------------------------

# Deliberately NO patient_id property here.
# Also deliberately NO mode property: both retrieval systems always run.
ROUTER_SCHEMA = {
    "type": "object",
    "properties": {
        "operation": {
            "type": "string",
            "enum": [
                "list_reports",
                "lab_values",
                "abnormal_labs",
                "latest_labs",
                "lab_trend",
                "none",
            ],
        },
        "analytes": {
            "type": "array",
            "items": {"type": "string"},
            "maxItems": 12,
        },
        "doctor": {"type": ["string", "null"]},
        "report_type": {"type": ["string", "null"]},
        "date_from": {"type": ["string", "null"]},
        "date_to": {"type": ["string", "null"]},
        "search_query": {"type": ["string", "null"]},
        "limit": {"type": "integer", "minimum": 1, "maximum": 20},
        "reason": {"type": "string"},
    },
    "required": [
        "operation",
        "analytes",
        "doctor",
        "report_type",
        "date_from",
        "date_to",
        "search_query",
        "limit",
        "reason",
    ],
    "additionalProperties": False,
}


ROUTER_SYSTEM_PROMPT = """
You are the retrieval planner for a clinical record assistant.

Your job is ONLY to create a structured retrieval plan. Do not answer
medical questions and do not invent patient data.

IMPORTANT: BOTH retrieval systems will ALWAYS run after this plan is created.
You do NOT choose SQL vs vector vs hybrid. There is no mode field.

For SQL/structured retrieval, choose the single most useful allow-listed
operation and filters for the question.

For vector retrieval, provide a semantic search_query that preserves the user's
meaning and is useful for searching report prose and embedded lab-result text.
The query is the ONLY retrieval signal besides the server-enforced patient scope.

IMPORTANT SECURITY RULE:
- Never request, infer, alter, or output a patient_id.
- The application injects the patient_id server-side.
- You cannot switch patients, search another patient, or broaden beyond the
  current patient's records.

For SQL operations:
- list_reports: inventory of reports, optionally filtered by doctor/type/date.
- lab_values: exact lab rows, optionally filtered by analyte/date.
- abnormal_labs: exact out-of-reference lab rows.
- latest_labs: latest result for one or more analytes.
- lab_trend: time series and pre-computed trend stats for one or more analytes.

For vector retrieval:
- search_query should be the user's semantic question, rewritten only to be
  retrieval-friendly without changing its meaning.

Dates should use ISO YYYY-MM-DD when present.
Do not make up dates or analytes.
""".strip()


@dataclass(frozen=True)
class RetrievalPlan:
    operation: Literal[
        "list_reports",
        "lab_values",
        "abnormal_labs",
        "latest_labs",
        "lab_trend",
        "none",
    ]
    analytes: List[str]
    doctor: Optional[str]
    report_type: Optional[str]
    date_from: Optional[str]
    date_to: Optional[str]
    search_query: Optional[str]
    limit: int
    reason: str


@dataclass
class RetrievalResponse:
    patient_id: str
    plan: RetrievalPlan
    sql_results: Dict[str, Any]
    vector_results: List[Dict[str, Any]]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Trusted patient scope
# ---------------------------------------------------------------------------

class PatientContext:

    __slots__ = ("__patient_id",)

    def __init__(self, patient_id: str):
        patient_id = (patient_id or "").strip()
        if not patient_id:
            raise ValueError("A non-empty patient_id is required.")
        self.__patient_id = patient_id

    @property
    def patient_id(self) -> str:
        return self.__patient_id


# ---------------------------------------------------------------------------
# LLM planner
# ---------------------------------------------------------------------------

class RetrievalPlanner:
    def __init__(self, model: str = ROUTER_MODEL):
        self.model = model

    def plan(self, question: str) -> RetrievalPlan:
        question = (question or "").strip()
        if not question:
            raise ValueError("question cannot be empty")
        if len(question) > ROUTER_MAX_QUESTION_CHARS:
            question = question[:ROUTER_MAX_QUESTION_CHARS]

        response = _groq.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": ROUTER_SYSTEM_PROMPT},
                {"role": "user", "content": question},
            ],
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "clinical_retrieval_plan",
                    "strict": True,
                    "schema": ROUTER_SCHEMA,
                },
            },
            temperature=0,
        )

        raw = json.loads(response.choices[0].message.content)

        # Defensive normalization; do not trust model-generated values.
        operation = raw.get("operation")
        if operation not in {
            "list_reports",
            "lab_values",
            "abnormal_labs",
            "latest_labs",
            "lab_trend",
            "none",
        }:
            raise ValueError(f"Invalid retrieval operation from planner: {operation!r}")

        analytes = [str(x).strip() for x in (raw.get("analytes") or []) if str(x).strip()]
        limit = int(raw.get("limit") or DEFAULT_VECTOR_RESULTS)
        limit = max(1, min(limit, 20))

        return RetrievalPlan(
            operation=operation,
            analytes=analytes,
            doctor=(raw.get("doctor") or None),
            report_type=(raw.get("report_type") or None),
            date_from=(raw.get("date_from") or None),
            date_to=(raw.get("date_to") or None),
            search_query=(raw.get("search_query") or None),
            limit=limit,
            reason=str(raw.get("reason") or ""),
        )


# ---------------------------------------------------------------------------
# SQL / structured retrieval
# ---------------------------------------------------------------------------

REPORT_SELECTION_SCHEMA = {
    "type": "object",
    "properties": {
        "selected_report_ids": {
            "type": "array",
            "items": {"type": "string"},
            "maxItems": 20,
        },
        "needs_lab_results": {"type": "boolean"},
        "reason": {"type": "string"},
    },
    "required": ["selected_report_ids", "needs_lab_results", "reason"],
    "additionalProperties": False,
}


REPORT_SELECTION_SYSTEM_PROMPT = """
You are the second-stage structured retrieval selector for a clinical record
assistant.

You receive:
1. The user's original question.
2. A list of report METADATA for the current patient only.

Your job is to select which report IDs are relevant to the user's question and
whether the backend should retrieve lab_results for those selected reports.

IMPORTANT SECURITY RULES:
- The report list was already retrieved for the authenticated patient.
- You may ONLY select IDs that appear exactly in the supplied report list.
- Never invent, modify, guess, or substitute a report ID.
- You do not know and must not request patient_id.
- Do not answer the medical question.

Selection rules:
- Select every report that could contain evidence needed to answer the question.
- For questions about trends, latest values, abnormal values, blood tests,
  laboratory measurements, or named analytes, select reports that plausibly
  contain the relevant lab results.
- Set needs_lab_results=true when lab_results could materially contribute to
  answering the question.
- For purely report-inventory questions (for example, listing reports), select
  the reports needed for that inventory and set needs_lab_results=false.
- Do not use semantic report matching as an excuse to invent an ID. IDs are
  copied only from the supplied metadata.
""".strip()


@dataclass(frozen=True)
class ReportSelection:
    selected_report_ids: List[str]
    needs_lab_results: bool
    reason: str


class ReportSelectionPlanner:
    def __init__(self, model: str = ROUTER_MODEL):
        self.model = model

    def select(
        self,
        question: str,
        report_metadata: List[Dict[str, Any]],
    ) -> ReportSelection:
        if not report_metadata:
            return ReportSelection([], False, "No reports exist for the current patient.")

        allowed_ids = {
            str(row["id"])
            for row in report_metadata
            if row.get("id") is not None
        }

        response = _groq.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": REPORT_SELECTION_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "question": question,
                            "reports": report_metadata,
                        },
                        ensure_ascii=False,
                        default=str,
                    ),
                },
            ],
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "select_clinical_reports",
                    "strict": True,
                    "schema": REPORT_SELECTION_SCHEMA,
                },
            },
            temperature=0,
        )

        raw = json.loads(response.choices[0].message.content)
        requested_ids = [str(x).strip() for x in (raw.get("selected_report_ids") or [])]

        # Critical backend authorization check: the model can only select IDs
        # that came from the already patient-scoped report inventory.
        selected_ids = list(dict.fromkeys(x for x in requested_ids if x in allowed_ids))

        return ReportSelection(
            selected_report_ids=selected_ids,
            needs_lab_results=bool(raw.get("needs_lab_results", False)),
            reason=str(raw.get("reason") or ""),
        )


class PatientScopedSQLRetriever:

    def __init__(
        self,
        context: PatientContext,
        *,
        selector: Optional[ReportSelectionPlanner] = None,
    ):
        self._context = context
        self._selector = selector or ReportSelectionPlanner()

    @property
    def patient_id(self) -> str:
        return self._context.patient_id

    def _get_all_report_metadata(self) -> List[Dict[str, Any]]:
        # Intentionally NO LLM-generated doctor/type/date filters here.
        # This is the patient's authoritative report inventory.
        return get_reports_list(self._context.patient_id)

    def _get_lab_results_for_reports(
        self,
        report_ids: List[str],
        *,
        limit: int,
    ) -> List[Dict[str, Any]]:
        if not report_ids:
            return []

        # The IDs have already been intersected with the patient-scoped report
        # inventory. Query lab_results only by those authorized report IDs.
        # Importing the existing db.supabase keeps this retrieval in the same
        # persistence layer without exposing a patient_id parameter to the LLM.
        from db import supabase

        response = (
            supabase.table("lab_results")
            .select(
                "id, report_id, investigation, result_value, "
                "ref_interval_raw, method"
            )
            .in_("report_id", report_ids)
            .limit(max(1, min(limit, 100)))
            .execute()
        )

        rows = response.data or []
        return [
            {**row, "source_type": "lab_result"}
            for row in rows
        ]

    def run(
        self,
        question: str,
        *,
        limit: int = 20,
    ) -> Dict[str, Any]:
        """
        Execute the complete two-stage SQL retrieval pipeline.

        Returns separate report metadata and lab-result evidence so the answer
        model can distinguish the two structured sources.
        """
        reports = self._get_all_report_metadata()

        # Bound what is shown to the selector, while preserving the newest-first
        # report inventory returned by db.py. The backend remains patient-scoped.
        report_inventory = [
            {
                **row,
                "source_type": "report_metadata",
            }
            for row in reports
        ]

        selection = self._selector.select(question, report_inventory)
        selected_set = set(selection.selected_report_ids)

        selected_reports = [
            row for row in report_inventory if str(row.get("id")) in selected_set
        ]
        for row in selected_reports:
            row["selected_for_question"] = True

        # Preserve the full report inventory as stage-1 evidence, but mark what
        # the selector chose. This is useful for debugging and auditability.
        for row in report_inventory:
            if str(row.get("id")) not in selected_set:
                row["selected_for_question"] = False

        labs: List[Dict[str, Any]] = []
        if selection.needs_lab_results:
            labs = self._get_lab_results_for_reports(
                selection.selected_report_ids,
                limit=limit,
            )

        return {
            "report_metadata": report_inventory,
            "selected_reports": selected_reports,
            "lab_results": labs,
            "selection": asdict(selection),
        }

# ---------------------------------------------------------------------------
# Vector retrieval
# ---------------------------------------------------------------------------

class PatientScopedVectorRetriever:
    """
    Thin adapter around the existing Qdrant PatientScopedRetriever.

    The patient scope is set when this object is constructed; callers cannot
    pass a different patient ID to search(). The underlying Qdrant retrieval
    receives no other metadata filters from the LLM planner or application.
    """

    def __init__(self, context: PatientContext):
        self._patient_id = context.patient_id
        self._retriever = PatientScopedRetriever(patient_id=self._patient_id)

    @property
    def patient_id(self) -> str:
        return self._patient_id

    def search(
        self,
        query: str,
        *,
        limit: int,
        score_threshold: float = DEFAULT_VECTOR_SCORE_THRESHOLD,
    ) -> List[Dict[str, Any]]:
        if not query or not query.strip():
            return []

        # The vector database has exactly ONE metadata boundary:
        # the application-owned patient_id inside PatientScopedRetriever.
        # No date, doctor, report_type, report_id, or other planner-generated
        # metadata filter is ever forwarded to Qdrant.
        points = self._retriever.search(
            query_text=query,
            n_results=max(1, min(limit, 20)),
            score_threshold=score_threshold,
        )

        return [
            {
                "score": float(point.score),
                "payload": dict(point.payload or {}),
            }
            for point in points
        ]


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

class ClinicalRetrievalEngine:
    """
    Main entry point for your API/backend.

    Example:
        engine = ClinicalRetrievalEngine(patient_id=current_patient_id)
        evidence = engine.retrieve("What was my latest HbA1c?")

    `current_patient_id` MUST come from your authenticated application context,
    not from the user's message and not from the LLM.
    """

    def __init__(
        self,
        patient_id: str,
        *,
        planner: Optional[RetrievalPlanner] = None,
        vector_score_threshold: float = DEFAULT_VECTOR_SCORE_THRESHOLD,
    ):
        self.context = PatientContext(patient_id)
        self.planner = planner or RetrievalPlanner()
        self.sql = PatientScopedSQLRetriever(self.context)
        self.vector = PatientScopedVectorRetriever(self.context)
        self.vector_score_threshold = vector_score_threshold

    def retrieve(self, question: str) -> RetrievalResponse:
        plan = self.planner.plan(question)

        sql_results: Dict[str, Any] = {}
        vector_results: List[Dict[str, Any]] = []

        # ALWAYS run the two-stage structured retrieval pipeline.
        # Stage 1 retrieves the patient's report metadata; stage 2 uses an LLM
        # only to select IDs from that already-authorized inventory and decide
        # whether lab_results are needed. The backend executes the actual DB query.
        sql_results = self.sql.run(question, limit=plan.limit)

        # ALWAYS run semantic vector retrieval as well. The LLM only supplies the
        # semantic query text; patient_id remains fixed inside PatientScopedRetriever.
        query = plan.search_query or question
        # Vector retrieval is ALWAYS patient-wide within this patient's
        # corpus. There are no report/date/doctor/type filters on Qdrant.
        # The user question itself is the semantic signal.
        vector_results = self.vector.search(
            query,
            limit=plan.limit,
            score_threshold=self.vector_score_threshold,
        )

        return RetrievalResponse(
            patient_id=self.context.patient_id,
            plan=plan,
            sql_results=sql_results,
            vector_results=vector_results,
        )

    def build_llm_context(self, response: RetrievalResponse) -> str:
        """
        Convert retrieved evidence into a bounded context string for your
        answer-generation LLM.

        This intentionally labels structured data separately from semantic
        evidence, so the answering model can distinguish exact database facts
        from report-text snippets.
        """
        payload = {
            "retrieval_plan": asdict(response.plan),
            "structured_database_evidence": response.sql_results,
            "semantic_vector_evidence": response.vector_results,
            "safety_note": (
                "Evidence is limited to the current patient context. "
                "Do not introduce information not present in the evidence."
            ),
        }
        return json.dumps(payload, ensure_ascii=False, indent=2, default=str)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------



# ---------------------------------------------------------------------------
# Deterministic tests / examples
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # For local testing, set the SAME patient UUID used by Supabase/Qdrant:
    #
    #   PowerShell:
    #   $env:TEST_PATIENT_ID="e037a105-0f1a-47fc-935d-e88c0a9034eb"
    #   python retrieval.py
    #
    # Do not hardcode a real patient's ID in source code. In the real app,
    # obtain it from the authenticated patient context.
    if not TEST_PATIENT_ID:
        raise SystemExit(
            "Set TEST_PATIENT_ID to the patient's Supabase/Qdrant UUID before running "
            "this test harness."
        )
    engine = ClinicalRetrievalEngine(TEST_PATIENT_ID)

    questions = [
        "was my latest blood report all ok"
    ]

    for question in questions:
        print("\n" + "=" * 90)
        print("QUESTION:", question)

        try:
            response = engine.retrieve(question)
            print("PLAN:")
            print(json.dumps(asdict(response.plan), indent=2))
            print("\nSTRUCTURED/SQL RESULTS:")
            print(json.dumps(response.sql_results, indent=2, default=str))
            print("\nVECTOR RESULTS:")
            print(json.dumps(response.vector_results, indent=2, default=str))
        except Exception as exc:
            print(f"ERROR: {type(exc).__name__}: {exc}")
