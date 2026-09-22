"""Patient-facing summaries for processed clinical reports."""

from __future__ import annotations

import json
import os
from typing import Any

from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv()

SUMMARY_MODEL = os.getenv("CLINICAL_SUMMARY_MODEL", "gemini-3.5-flash-lite")
MAX_OCR_CHARS = 12000
MAX_SUMMARY_WORDS = 100

SUMMARY_SYSTEM_PROMPT = """
Summarize this medical report for the patient who received it.
Use plain, reassuring language and avoid clinical jargon.
Write exactly 3 to 5 short sentences and keep the response under 100 words.
Mention documented abnormal findings, new diagnoses, or medication changes when present.
Only state facts supported by the supplied report, entities, and lab results.
Do not diagnose, speculate, recommend treatment, or invent missing information.
Return only the summary text, with no heading or bullet list.
""".strip()

_client = None


def _get_client():
    global _client
    if _client is None:
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise RuntimeError("GEMINI_API_KEY is not set.")
        _client = genai.Client(api_key=api_key)
    return _client


def _lab_facts(lab_results: list[dict[str, Any]]) -> str:
    lines = []
    for row in lab_results:
        investigation = str(row.get("investigation") or "").strip()
        value = str(row.get("result_value") or "").strip()
        unit = str(row.get("result_unit") or "").strip()
        reference = str(row.get("ref_interval_raw") or "").strip()
        if not investigation or not value:
            continue
        result = f"{investigation}: {value} {unit}".strip()
        if reference:
            result += f" (reference interval: {reference})"
        lines.append(result)
    return "\n".join(lines) or "None extracted"


def build_summary_prompt(
    clean_text: str,
    entities: list[dict[str, Any]],
    lab_results: list[dict[str, Any]],
) -> dict[str, str | None]:
    text = " ".join(str(clean_text or "").split())[:MAX_OCR_CHARS]
    entity_lines = []
    for entity in entities:
        label = str(entity.get("entity_group") or "entity").strip()
        value = str(entity.get("text") or "").strip()
        if value:
            entity_lines.append(f"{label}: {value}")

    if not text and not entity_lines and not lab_results:
        raise ValueError("OCR did not produce enough report information to summarize.")

    return (
        "CLEANED REPORT TEXT:\n"
        f"{text or 'None extracted'}\n\n"
        "EXTRACTED MEDICAL ENTITIES:\n"
        f"{'\n'.join(entity_lines) or 'None extracted'}\n\n"
        "EXTRACTED LAB RESULTS:\n"
        f"{_lab_facts(lab_results)}"
    )


def generate_summary(
    clean_text: str,
    entities: list[dict[str, Any]],
    lab_results: list[dict[str, Any]],
) -> str:
    prompt = build_summary_prompt(clean_text, entities, lab_results)
    response = _get_client().models.generate_content(
        model=SUMMARY_MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            temperature=0,
            system_instruction=SUMMARY_SYSTEM_PROMPT,
        ),
    )
    summary = " ".join((response.text or "").split()).strip()
    if not summary:
        raise RuntimeError("Gemini summary generation returned empty content.")
    if len(summary.split()) > MAX_SUMMARY_WORDS:
        raise RuntimeError("Gemini summary exceeded the 100-word limit.")
    return summary

def _generate_report_summary(
    report_id: str,
    patient_id: str,
    clean_text: str,
    entities: list[dict[str, Any]],
    lab_results: list[dict[str, Any]],
) -> dict[str, str | None]:
    try:
        summary = generate_summary(clean_text, entities, lab_results)
        from db import update_report_summary
        update_report_summary(report_id, patient_id, "completed", summary)
        return {"status": "completed", "summary": summary}
    except Exception:
        try:
            from db import update_report_summary
            update_report_summary(report_id, patient_id, "failed")
        except Exception:
            print("Could not persist failed summary state for report %s", report_id)
        return {"status": "failed", "summary": None}