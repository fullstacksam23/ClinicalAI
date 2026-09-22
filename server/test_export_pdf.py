from export_pdf import build_doctor_summary_pdf


def test_export_pdf_fixture():
    data = {
        "generated_date": "2026-09-21",
        "date_from": "2025-09-21",
        "date_to": "2026-09-21",
        "patient": {"patient_name": "A <Patient> & Co", "patient_age_at_report": 42, "patient_sex": "F"},
        "abnormal_labs": [
            {"investigation": "A < B", "result_value": "0", "result_numeric": 0, "result_unit": "mg", "ref_interval_raw": "0 - 4", "ref_low": 0, "ref_high": 4, "report_date": "2026-09-01"},
            {"investigation": "Missing range", "result_value": "bad", "result_numeric": None, "ref_low": None, "ref_high": None, "report_date": "2026-08-01"},
        ],
        "trends": {"Marker": {"series": [{"report_date": "2026-08-01", "value": 1, "ref_low": 0, "ref_high": 2}, {"report_date": "2026-09-01", "value": 3, "ref_low": 0, "ref_high": 2}]}},
        "reports": [{"report_type": "Lab <report>", "report_date": "2026-09-01", "referring_doctor": "Dr. A & B", "summary_status": "completed", "summary": "Summary <text> & details"}],
        "questions": ["Is <this> & that important?"],
    }
    pdf = build_doctor_summary_pdf(data)
    assert pdf.startswith(b"%PDF-")
    empty_pdf = build_doctor_summary_pdf({"reports": data["reports"], "abnormal_labs": [], "questions": []})
    assert empty_pdf.startswith(b"%PDF-")


if __name__ == "__main__":
    test_export_pdf_fixture()
    print("export PDF fixture passed")