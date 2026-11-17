r"""Seed clearly synthetic lab history for the prototype patient's Trends page.

Run from the repository root:
    .\server\myenv\Scripts\python.exe server\seed_sample_trends.py --apply
"""

from __future__ import annotations

import argparse
import os

from dotenv import load_dotenv

from db import get_or_create_patient, supabase


load_dotenv()

SEED_VERSION = "sample_trends_v1"
REPORT_TYPE = "Laboratory Panel"
REPORT_SUMMARY = "Laboratory panel with glucose, hemoglobin, LDL cholesterol, TSH, and platelet results."
REPORT_DATES = (
    "2026-01-15", "2026-02-15", "2026-03-15",
    "2026-04-15", "2026-05-15", "2026-06-15",
)

# These values are invented solely to exercise the UI. Units and printed
# reference intervals remain consistent for each analyte across reports.
SAMPLE_LABS = (
    ("Glucose", "mg/dL", 70, 99, (108, 104, 98, 93, 89, 94)),
    ("Hemoglobin", "g/dL", 12, 16, (11.4, 11.8, 12.2, 12.6, 13.0, 13.3)),
    ("LDL Cholesterol", "mg/dL", None, 129, (158, 149, 138, 127, 116, 108)),
    ("TSH", "mIU/L", 0.4, 4.0, (5.6, 4.8, 3.7, 2.9, 2.4, 2.1)),
    ("Platelets", "10^3/uL", 150, 450, (210, 218, 225, 235, 242, 250)),
)


def lab_rows(report_id: str, index: int) -> list[dict]:
    rows = []
    for name, unit, low, high, values in SAMPLE_LABS:
        value = values[index]
        interval = f"{low}–{high}" if low is not None else f"<={high}"
        rows.append({
            "report_id": report_id,
            "investigation": name,
            "result_value": str(value),
            "result_numeric": value,
            "result_unit": unit,
            "ref_interval_raw": interval,
            "ref_low": low,
            "ref_high": high,
            "method": "Automated analysis",
        })
    return rows


def report_text(report_date: str, rows: list[dict]) -> str:
    lines = [f"Laboratory Panel — {report_date}"]
    lines.extend(
        f"{row['investigation']}: {row['result_value']} {row['result_unit']} "
        f"(reference: {row['ref_interval_raw']} {row['result_unit']})"
        for row in rows
    )
    return "\n".join(lines)


def seed() -> tuple[int, int, int, int]:
    external_patient_id = os.getenv("CLINICALAI_EXTERNAL_PATIENT_ID", "test_patient_001")
    patient_id = get_or_create_patient(
        "Jordan Lee", external_patient_id=external_patient_id
    )
    existing = (
        supabase.table("reports")
        .select("id, raw_ocr_job_id, report_type, summary, clean_text")
        .eq("patient_id", patient_id)
        .eq("pipeline_version", SEED_VERSION)
        .execute()
        .data
    )
    existing_reports = {row["raw_ocr_job_id"]: row for row in existing}
    inserted_reports = 0
    inserted_labs = 0
    updated_reports = 0
    updated_labs = 0

    for index, report_date in enumerate(REPORT_DATES):
        marker = f"{SEED_VERSION}:{report_date}"
        rows = lab_rows("", index)
        display_fields = {
            "report_type": REPORT_TYPE,
            "summary": REPORT_SUMMARY,
            "clean_text": report_text(report_date, rows),
        }
        existing_report = existing_reports.get(marker)
        report_id = existing_report["id"] if existing_report else None
        if not report_id:
            report = (
                supabase.table("reports")
                .insert({
                    "patient_id": patient_id,
                    "report_date": report_date,
                    "raw_ocr_job_id": marker,
                    "pipeline_version": SEED_VERSION,
                    "metadata_flagged_for_review": False,
                    "extra_metadata": {"synthetic": True, "sample_seed": SEED_VERSION},
                    "summary_status": "completed",
                    **display_fields,
                })
                .execute()
                .data[0]
            )
            report_id = report["id"]
            inserted_reports += 1
        elif any(existing_report.get(key) != value for key, value in display_fields.items()):
            updated = (
                supabase.table("reports")
                .update(display_fields)
                .eq("id", report_id)
                .eq("patient_id", patient_id)
                .eq("pipeline_version", SEED_VERSION)
                .eq("raw_ocr_job_id", marker)
                .select("id")
                .execute()
                .data
            )
            updated_reports += len(updated)

        existing_labs = (
            supabase.table("lab_results")
            .select("id, investigation, method, reports!inner(patient_id)")
            .eq("report_id", report_id)
            .eq("reports.patient_id", patient_id)
            .execute()
            .data
        )
        existing_by_name = {row["investigation"]: row for row in existing_labs}
        for row in lab_rows(report_id, index):
            old_name = f"Sample {row['investigation']}"
            old = existing_by_name.get(old_name)
            if old and row["investigation"] not in existing_by_name:
                updated = (
                    supabase.table("lab_results")
                    .update({"investigation": row["investigation"], "method": row["method"]})
                    .eq("id", old["id"])
                    .eq("report_id", report_id)
                    .eq("investigation", old_name)
                    .select("id")
                    .execute()
                    .data
                )
                updated_labs += len(updated)
                existing_by_name[row["investigation"]] = old
        missing = [row for row in lab_rows(report_id, index) if row["investigation"] not in existing_by_name]
        if missing:
            supabase.table("lab_results").insert(missing).execute()
            inserted_labs += len(missing)

    return inserted_reports, inserted_labs, updated_reports, updated_labs


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Insert the synthetic rows into Supabase")
    args = parser.parse_args()
    if args.apply:
        reports, labs, revised_reports, revised_labs = seed()
        print(f"Inserted {reports} reports and {labs} lab results; updated {revised_reports} reports and {revised_labs} lab results.")
    else:
        print(f"Ready to seed {len(REPORT_DATES)} synthetic reports with {len(REPORT_DATES) * len(SAMPLE_LABS)} lab results. Use --apply to insert them.")
