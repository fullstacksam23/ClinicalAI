"""
Supabase persistence layer for MediLens, using the supabase-py client
instead of raw psycopg2. Same schema/logic as before — this just swaps the
query mechanism.

Uses the SERVICE ROLE key, not the anon key, because there's no real
Supabase Auth session yet (see schema.sql notes) — the service role key
bypasses Row-Level Security, so patient_id filtering is still enforced
here in application code, same as your Qdrant PatientScopedRetriever.
Once real auth exists, prefer switching reads to the anon key + a real
user JWT so RLS does the enforcement at the database level instead.
"""

import os
import re
import uuid
from datetime import datetime, timezone
import pandas as pd
from supabase import create_client, Client
from dotenv import load_dotenv
from lab_normalization import _parse_range as _normalize_range
from lab_normalization import _parse_value as _normalize_value

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")  # NOT the anon key — see note above

# TESTING ONLY — hardcoded so every uploaded report resolves to the SAME
# patient regardless of what name the LLM extracted from that report's
# text. This deliberately bypasses the name-slugging logic in
# get_or_create_patient (which has real collision/fragmentation problems
# of its own — see that function's docstring). Once frontend auth exists,
# DELETE this constant entirely and pass the real authenticated patient_id
# through save_report()'s external_patient_id parameter instead.
TEST_PATIENT_ID = "test_patient_001"

PROMOTED_REPORT_FIELDS = {
    "patient_name",
    "patient_age", "patient_sex",
    "report_date", "reported_date",
    "referring_doctor", "report_type",
}

supabase: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)


def _slugify(text):
    return re.sub(r'[^a-z0-9]+', '_', text.lower()).strip('_')


def get_or_create_patient(patient_name, external_patient_id=None):
    """
    Prototyping without auth: external_patient_id defaults to a slug of
    the patient's name. TODO: once Supabase Auth exists, this table's id
    should equal auth.users.id directly, and this name-based matching
    should be removed — it's not a safe long-term identity strategy.
    """
    external_patient_id = external_patient_id or _slugify(patient_name or "unknown_patient")

    existing = (
        supabase.table("patients")
        .select("id")
        .eq("external_patient_id", external_patient_id)
        .execute()
    )
    if existing.data:
        return existing.data[0]["id"]

    inserted = (
        supabase.table("patients")
        .insert({"external_patient_id": external_patient_id, "name": patient_name})
        .execute()
    )
    return inserted.data[0]["id"]


def insert_report(fields, clean_text, patient_id, raw_ocr_job_id=None,
                   pipeline_version="v1", extraction_stats=None):
    """
    fields: a ReportFields object from metadata_extraction.extract_report_fields()
    patient_id: already-resolved patient UUID (from get_or_create_patient) —
    this function no longer resolves it itself, so there's a single place
    (save_report) that owns "who is this report for".
    Returns the new report's UUID (as a string).
    """
    extraction_stats = extraction_stats or {}
    any_flagged = (
        any(f.flagged_for_review for f in fields.__dict__.values())
        or bool(extraction_stats.get("review_required"))
    )

    extra_metadata = {
        name: f.value for name, f in fields.__dict__.items()
        if name not in PROMOTED_REPORT_FIELDS
    }
    if extraction_stats:
        extra_metadata["ocr_table_extraction"] = extraction_stats

    report_date = (
        fields.report_date.value
        or fields.reported_date.value
        or datetime.now(timezone.utc).date().isoformat()
    )

    report_row = {
        "patient_id": patient_id,
        "report_type": fields.report_type.value,
        "report_date": report_date,
        "reported_date": fields.reported_date.value,
        "referring_doctor": fields.referring_doctor.value,
        "patient_age_at_report": fields.patient_age.value,
        "patient_sex": fields.patient_sex.value,
        "clean_text": clean_text,
        "raw_ocr_job_id": raw_ocr_job_id,
        "metadata_flagged_for_review": any_flagged,
        "extra_metadata": extra_metadata,
        "pipeline_version": pipeline_version,
        "summary_status": "pending",
    }

    inserted = supabase.table("reports").insert(report_row).execute()
    report_id = inserted.data[0]["id"]

    _insert_provenance(report_id, fields)
    return report_id


def _insert_provenance(report_id, fields):
    rows = [
        {
            "id": str(uuid.uuid4()),
            "report_id": report_id,
            "field_name": name,
            "extraction_method": f.extraction_method,
            "flagged_for_review": f.flagged_for_review,
            "note": f.note,
        }
        for name, f in fields.__dict__.items()
    ]
    supabase.table("extraction_provenance").insert(rows).execute()


def insert_lab_results(report_id, lab_df):
    """
    lab_df: the DataFrame from process_ocr_result(). No-op if empty (e.g.
    prose-only reports like radiology findings have no lab table).
    """
    if lab_df is None or lab_df.empty:
        return

    print(f"[Labs] Saving canonical rows for report {report_id}")

    rows = []
    for _, row in lab_df.iterrows():
        if row.get("row_status") != "parsed":
            continue
        investigation = "" if pd.isna(row.get("investigation")) else str(row.get("investigation") or "").strip()
        result_raw = "" if pd.isna(row.get("result_value")) else str(row.get("result_value") or "").strip()
        ref_raw = "" if pd.isna(row.get("ref_interval_raw")) else str(row.get("ref_interval_raw") or "").strip()
        method = "" if pd.isna(row.get("method")) else str(row.get("method") or "").strip()
        result_numeric = None if pd.isna(row.get("result_numeric")) else row.get("result_numeric")
        result_unit = "" if pd.isna(row.get("result_unit")) else str(row.get("result_unit") or "").strip()
        ref_low = None if pd.isna(row.get("ref_low")) else row.get("ref_low")
        ref_high = None if pd.isna(row.get("ref_high")) else row.get("ref_high")
        if not investigation or investigation.upper().startswith("****") or not result_raw:
            continue

        rows.append({
            "report_id": report_id,
            "investigation": investigation,
            "result_value": result_raw,
            "result_numeric": result_numeric,
            "result_unit": result_unit,
            "ref_interval_raw": ref_raw,
            "ref_low": ref_low,
            "ref_high": ref_high,
            "method": method,
        })

    if rows:
        supabase.table("lab_results").insert(rows).execute()
        print(f"[Labs] Inserted {len(rows)} lab rows for report {report_id}")
    else:
        print(f"[Labs] No usable lab rows found for report {report_id}")


def _parse_value(s):
    return _normalize_value(s)


def _parse_range(s):
    return _normalize_range(s)


def save_report(fields, lab_df, clean_text, raw_ocr_job_id=None, external_patient_id=None):
    """
    Single entry point wiring ProcessFile()'s output into Supabase.
    Returns (report_id, patient_id) — reuse both as the Qdrant point's
    report_id/patient_id so both stores agree on identity.

    TESTING: external_patient_id defaults to TEST_PATIENT_ID, so every
    report you process lands under the same patient regardless of what
    name is on it — lets you test multi-report queries (trends, "list my
    reports", etc.) without real auth. Once auth exists, ALWAYS pass the
    real authenticated patient_id explicitly here and delete TEST_PATIENT_ID.
    """
    external_patient_id = external_patient_id or TEST_PATIENT_ID
    patient_id = get_or_create_patient(
        fields.patient_name.value, external_patient_id=external_patient_id
    )
    extraction_stats = getattr(lab_df, "attrs", {}).get("extraction_stats", {}) if lab_df is not None else {}
    report_id = insert_report(
        fields, clean_text,
        patient_id=patient_id,
        raw_ocr_job_id=raw_ocr_job_id,
        extraction_stats=extraction_stats,
    )
    insert_lab_results(report_id, lab_df)
    return report_id, patient_id


# --- Example query functions the future router will call ---
# (structured lookups — no LLM/embedding involved, per the routing design)

def get_report_ids(patient_id, doctor=None, date_from=None, date_to=None, report_type=None):
    """
    Resolves structured filters (doctor/date/report_type) to a set of
    report_ids BEFORE any semantic search runs — this is the
    'structured-first' step: narrowing Qdrant to a handful of reports is
    far more precise than searching the patient's whole corpus, and an
    empty result here is an honest, deterministic 'no such reports exist'
    rather than letting semantic search return the nearest wrong thing.

    doctor is matched with ILIKE (fuzzy substring, case-insensitive) since
    a patient saying "Dr. Talluri" won't exact-match a stored value like
    "Ref. by Dr. K. Talluri MD".
    """
    query = supabase.table("reports").select("id").eq("patient_id", patient_id)

    if doctor:
        query = query.ilike("referring_doctor", f"%{doctor}%")
    if report_type:
        query = query.ilike("report_type", f"%{report_type}%")
    if date_from:
        query = query.gte("report_date", date_from)
    if date_to:
        query = query.lte("report_date", date_to)

    result = query.execute().data
    return [row["id"] for row in result]


def get_lab_values(patient_id, investigation=None, date_from=None, date_to=None):
    query = (
        supabase.table("lab_results")
        .select("*, reports!inner(patient_id, report_date)")
        .eq("reports.patient_id", patient_id)
    )
    if investigation:
        query = query.ilike("investigation", f"%{investigation}%")
    if date_from:
        query = query.gte("reports.report_date", date_from)
    if date_to:
        query = query.lte("reports.report_date", date_to)
    return query.execute().data


def get_abnormal_results(patient_id, date_from=None, date_to=None):
    """Illustrates the 'were any of my results abnormal' deterministic handler."""
    results = get_lab_values(patient_id, date_from=date_from, date_to=date_to)
    abnormal = [
        r for r in results
        if r["result_numeric"] is not None
        and (
            (r["ref_low"] is not None and r["result_numeric"] < r["ref_low"])
            or (r["ref_high"] is not None and r["result_numeric"] > r["ref_high"])
        )
    ]
    return abnormal


def get_latest_lab_values(patient_id, analytes, date_from=None, date_to=None):
    """
    For the router's 'latest_value' aggregate. Returns the most recent
    result per requested analyte, with the reference range and an explicit
    in/out-of-range flag rather than just the bare number — per Fable 5's
    review, a raw value with no range context invites the LLM to
    editorialize about "normal" without the data to back it up.

    supabase-py/PostgREST has no direct DISTINCT ON — fetch all matching
    rows ordered newest-first and take the first occurrence per
    investigation in Python instead.
    """
    latest = {}
    for analyte in analytes:
        rows = get_lab_values(patient_id, investigation=analyte, date_from=date_from, date_to=date_to)
        # get_lab_values doesn't guarantee order; sort newest-first here.
        rows = [r for r in rows if r.get("reports") and r["reports"].get("report_date")]
        rows.sort(key=lambda r: r["reports"]["report_date"], reverse=True)
        if not rows:
            continue
        latest_row = rows[0]
        latest[analyte] = {
            "investigation": latest_row["investigation"],
            "result_value": latest_row["result_value"],
            "result_numeric": latest_row["result_numeric"],
            "result_unit": latest_row["result_unit"],
            "ref_low": latest_row["ref_low"],
            "ref_high": latest_row["ref_high"],
            "report_date": latest_row["reports"]["report_date"],
            "in_range": _in_range(latest_row),
        }
    return latest


def get_lab_trend(patient_id, analytes, date_from=None, date_to=None):
    """
    For the router's 'trend' aggregate. Returns, per analyte, the full
    time series PLUS pre-computed summary stats — the LLM should narrate
    this, never compute deltas/percentages itself (same "code computes,
    LLM narrates" principle used everywhere else in this pipeline).

    Explicitly checks for mixed units across the series for one analyte
    (e.g. mg/dL vs µmol/L from different hospitals) and flags it rather
    than silently averaging/comparing incompatible numbers — a real gap
    Fable 5's review caught: a unit mismatch produces confident garbage,
    not an error, if nothing checks for it.

    Also checks for a mismatched REFERENCE RANGE across the series —
    different labs/hospitals can use different reference intervals for
    the same analyte (different equipment/method/population), so a value
    that's "in range" against one report's printed range might not be
    against another's. This is a distinct problem from a unit mismatch:
    two rows can share the same unit and still disagree on what counts as
    normal.
    """
    if analytes is None:
        all_rows = get_lab_values(patient_id, date_from=date_from, date_to=date_to)
        analytes = sorted({r["investigation"] for r in all_rows if r.get("investigation")})

    trends = {}
    for analyte in analytes:
        rows = get_lab_values(patient_id, investigation=analyte, date_from=date_from, date_to=date_to)
        rows = [r for r in rows if r["result_numeric"] is not None and r.get("reports", {}).get("report_date")]
        rows.sort(key=lambda r: r["reports"]["report_date"])

        if not rows:
            continue

        units_seen = {r["result_unit"] for r in rows if r["result_unit"]}
        unit_mismatch = len(units_seen) > 1

        # Only compare rows that actually HAVE a range to compare — a row
        # with a missing range (None, None) isn't a "different" range, it's
        # an absent one, and shouldn't trigger a false mismatch flag.
        ranges_seen = {(r["ref_low"], r["ref_high"]) for r in rows if r["ref_low"] is not None or r["ref_high"] is not None}
        ref_range_mismatch = len(ranges_seen) > 1

        series = [
            {
                "report_date": r["reports"]["report_date"],
                "value": r["result_numeric"],
                "unit": r["result_unit"],
                "ref_low": r["ref_low"],
                "ref_high": r["ref_high"],
                "in_range": _in_range(r),
            }
            for r in rows
        ]

        first_val, last_val = series[0]["value"], series[-1]["value"]
        delta = last_val - first_val
        pct_change = (delta / first_val * 100) if first_val else None
        out_of_range_count = sum(1 for point in series if point["in_range"] is False)

        trends[analyte] = {
            "series": series,
            "unit_mismatch": unit_mismatch,
            "units_seen": sorted(units_seen),
            "ref_range_mismatch": ref_range_mismatch,
            "ref_ranges_seen": sorted(ranges_seen),
            "summary": {
                "first_value": first_val,
                "last_value": last_val,
                "delta": delta,
                "pct_change": round(pct_change, 1) if pct_change is not None else None,
                "count": len(series),
                "out_of_range_count": out_of_range_count,
            },
        }

        warnings = []
        if unit_mismatch:
            warnings.append(
                f"Mixed units across this analyte's history ({sorted(units_seen)}) — "
                "trend delta/percentage may not be meaningful."
            )
        if ref_range_mismatch:
            warnings.append(
                f"Different reference ranges used across reports ({sorted(ranges_seen)}) — "
                "likely different labs/methods; 'in range' status may not be directly "
                "comparable point to point."
            )
        if warnings:
            trends[analyte]["warning"] = " ".join(warnings)

    return trends


def get_reports_list(patient_id, doctor=None, report_type=None, date_from=None, date_to=None):
    """
    For the router's 'list_reports' aggregate. Returns richer rows than
    get_report_ids (which only returns bare ids for structured-first
    resolution) — includes metadata_flagged_for_review so the answer layer
    can caveat any report whose extracted metadata wasn't fully trusted.
    """
    query = (
        supabase.table("reports")
        .select("id, report_type, report_date, referring_doctor, patient_age_at_report, patient_sex, metadata_flagged_for_review, extra_metadata, summary, summary_status")
        .eq("patient_id", patient_id)
        .order("report_date", desc=True)
    )
    if doctor:
        query = query.ilike("referring_doctor", f"%{doctor}%")
    if report_type:
        query = query.ilike("report_type", f"%{report_type}%")
    if date_from:
        query = query.gte("report_date", date_from)
    if date_to:
        query = query.lte("report_date", date_to)
    return query.execute().data


def get_report_by_id(report_id, patient_id):
    """Fetch a single report by ID ensuring patient-scope ownership."""
    rows = (
        supabase.table("reports")
        .select("id, report_type, report_date, referring_doctor, metadata_flagged_for_review, extra_metadata, summary, summary_status")
        .eq("id", str(report_id))
        .eq("patient_id", str(patient_id))
        .limit(1)
        .execute()
        .data
        or []
    )
    return rows[0] if rows else None


def get_all_patient_labs(patient_id):
    """
    Fetch all laboratory results across all reports belonging to this patient,
    ordered newest-first by report date.
    """
    rows = (
        supabase.table("lab_results")
        .select("id, report_id, investigation, result_value, result_numeric, result_unit, ref_interval_raw, ref_low, ref_high, method, reports!inner(report_date, report_type)")
        .eq("reports.patient_id", str(patient_id))
        .execute()
        .data
        or []
    )
    rows.sort(
        key=lambda r: (r.get("reports") or {}).get("report_date") or "",
        reverse=True,
    )
    return [
        {
            "id": r.get("id"),
            "report_id": r.get("report_id"),
            "investigation": r.get("investigation"),
            "result_value": r.get("result_value"),
            "result_numeric": r.get("result_numeric"),
            "result_unit": r.get("result_unit"),
            "ref_interval_raw": r.get("ref_interval_raw"),
            "ref_low": r.get("ref_low"),
            "ref_high": r.get("ref_high"),
            "method": r.get("method"),
            "report_date": (r.get("reports") or {}).get("report_date"),
            "report_type": (r.get("reports") or {}).get("report_type"),
        }
        for r in rows
    ]


def get_patient_name(patient_id):
    rows = (
        supabase.table("patients")
        .select("name")
        .eq("id", str(patient_id))
        .limit(1)
        .execute()
        .data
        or []
    )
    return rows[0].get("name") if rows else None


def get_doctor_summary_data(patient_id, date_from, date_to, questions=None):
    """Gather all patient-scoped inputs needed by the doctor-summary export."""
    reports = get_reports_list(patient_id, date_from=date_from, date_to=date_to)
    all_labs = get_all_patient_labs(patient_id)
    abnormal_labs = get_abnormal_results(patient_id, date_from=date_from, date_to=date_to)
    for row in abnormal_labs:
        row["report_date"] = row.get("report_date") or (row.get("reports") or {}).get("report_date")
    abnormal_labs.sort(key=lambda row: row.get("reports", {}).get("report_date") or row.get("report_date") or "", reverse=True)
    abnormal_analytes = list(dict.fromkeys(
        row.get("investigation") for row in abnormal_labs if row.get("investigation")
    ))
    trends = get_lab_trend(patient_id, abnormal_analytes, date_from=date_from, date_to=date_to)
    trends = {
        analyte: trend for analyte, trend in trends.items()
        if sum(1 for point in trend.get("series", []) if point.get("value") is not None) >= 2
    }
    latest = reports[0] if reports else {}
    return {
        "patient": {
            "patient_name": get_patient_name(patient_id),
            **{
                key: latest.get(key)
                for key in ("patient_age_at_report", "patient_sex")
                if latest.get(key) not in (None, "")
            },
        },
        "reports": reports,
        "all_labs": all_labs,
        "abnormal_labs": abnormal_labs,
        "trends": trends,
        "date_from": date_from,
        "date_to": date_to,
        "questions": questions or [],
    }


def get_timeline_events(patient_id, date_from=None, date_to=None):
    """
    Return a flat list of timeline events for the patient, newest-first.

    Event types
    -----------
    "report"       — one event per report (report date, type, referring doctor,
                     summary_status).
    "abnormal_lab" — one event per unique (report_id, investigation) pair whose
                     result_numeric falls outside its reference range.  Derived
                     from existing data; no new queries beyond the two already
                     used by the rest of the module.

    Parameters
    ----------
    patient_id : str  — server-resolved patient UUID (never from browser).
    date_from  : str | None — ISO date lower bound (inclusive), applied to
                              report_date for both event types.
    date_to    : str | None — ISO date upper bound (inclusive).

    Each event dict always contains:
        id, type, date (ISO str), report_id, title, subtitle

    "report" events also carry:   summary_status, referring_doctor
    "abnormal_lab" events also carry: lab_result_id, investigation,
        value, unit, ref_interval_raw, direction ("high"|"low")
    """
    # --- query 1: reports list (already sorted newest-first) ---
    reports = get_reports_list(patient_id, date_from=date_from, date_to=date_to)

    # --- query 2: all patient labs (flat, joined with report date/type) ---
    all_labs = get_all_patient_labs(patient_id)

    events = []

    # Build "report" events
    for rep in reports:
        rep_date = rep.get("report_date") or ""
        events.append({
            "id": f"report-{rep['id']}",
            "type": "report",
            "date": rep_date,
            "report_id": str(rep["id"]),
            "title": rep.get("report_type") or "Medical Report",
            "subtitle": rep.get("referring_doctor") or "",
            "summary_status": rep.get("summary_status") or "pending",
            "referring_doctor": rep.get("referring_doctor") or "",
        })

    # Build "abnormal_lab" events — one per unique (report_id, investigation).
    # Keep only rows with a numeric value outside the reference range.
    # Deduplication: if a patient had the same test twice on the same report
    # (rare but possible from header-row duplicates), keep the first occurrence.
    seen = set()
    for lab in all_labs:
        # apply date filters on report_date
        rep_date = lab.get("report_date") or ""
        if date_from and rep_date and rep_date < date_from:
            continue
        if date_to and rep_date and rep_date > date_to:
            continue

        value = lab.get("result_numeric")
        ref_low = lab.get("ref_low")
        ref_high = lab.get("ref_high")
        if value is None:
            continue
        if ref_low is None and ref_high is None:
            continue

        is_high = ref_high is not None and value > ref_high
        is_low = ref_low is not None and value < ref_low
        if not is_high and not is_low:
            continue

        direction = "high" if is_high else "low"
        investigation = lab.get("investigation") or "Unknown test"
        dedup_key = (str(lab.get("report_id")), investigation)
        if dedup_key in seen:
            continue
        seen.add(dedup_key)

        unit = lab.get("result_unit") or ""
        value_str = lab.get("result_value") or str(value)
        ref_raw = lab.get("ref_interval_raw") or ""
        subtitle = f"{value_str}{(' ' + unit) if unit else ''} — {direction.upper()}"
        if ref_raw:
            subtitle += f" (ref: {ref_raw})"

        events.append({
            "id": f"lab-{lab.get('id')}",
            "type": "abnormal_lab",
            "date": rep_date,
            "report_id": str(lab.get("report_id") or ""),
            "title": investigation,
            "subtitle": subtitle,
            "lab_result_id": str(lab.get("id") or ""),
            "investigation": investigation,
            "value": value_str,
            "unit": unit,
            "ref_interval_raw": ref_raw,
            "direction": direction,
        })

    # Sort all events newest-first; secondary sort by id for stability.
    events.sort(key=lambda e: (e["date"], e["id"]), reverse=True)
    return events


def get_report_summary_input(report_id, patient_id):
    """Return the stored report text for a patient-scoped summary retry."""
    report = (
        supabase.table("reports")
        .select("id, clean_text")
        .eq("id", str(report_id))
        .eq("patient_id", str(patient_id))
        .limit(1)
        .execute()
        .data
        or []
    )
    if not report:
        return None

    labs = (
        supabase.table("lab_results")
        .select("investigation, result_value, result_unit, ref_interval_raw")
        .eq("report_id", str(report_id))
        .execute()
        .data
        or []
    )
    return {"clean_text": report[0].get("clean_text") or "", "lab_results": labs}


def update_report_summary(report_id, patient_id, status, summary=None):
    """Update a summary only when the report belongs to the requested patient."""
    payload = {"summary_status": status, "summary": summary}
    return (
        supabase.table("reports")
        .update(payload)
        .eq("id", str(report_id))
        .eq("patient_id", str(patient_id))
        .execute()
        .data
    )


def delete_report(report_id, patient_id):
    """Delete one patient-scoped report and its dependent Supabase rows."""
    report_id = str(report_id)
    patient_id = str(patient_id)
    owned = (supabase.table("reports").select("id")
             .eq("id", report_id).eq("patient_id", patient_id)
             .limit(1).execute().data or [])
    if not owned:
        return False

    # Delete explicitly so this remains correct even if foreign-key cascades
    # are not enabled in an existing deployment.
    for table in ("lab_results", "extraction_provenance"):
        supabase.table(table).delete().eq("report_id", report_id).execute()
    supabase.table("reports").delete().eq("id", report_id).eq("patient_id", patient_id).execute()
    return True


def _in_range(row):
    """True/False/None (None = no reference range available to check against)."""
    value, low, high = row.get("result_numeric"), row.get("ref_low"), row.get("ref_high")
    if value is None or (low is None and high is None):
        return None
    if low is not None and value < low:
        return False
    if high is not None and value > high:
        return False
    return True


# --- usage ---
# from preprocessing import ProcessFile
# from db import save_report
#
# fields, lab_df, clean_text, entities = ProcessFile("rad1.pdf")
# report_id = save_report(fields, lab_df, clean_text)
#
# from embedding import embed_report
# embed_report(report_id=str(report_id), clean_text=clean_text, fields=fields, entities=entities)
