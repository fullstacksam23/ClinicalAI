from __future__ import annotations

from datetime import date, datetime
from io import BytesIO
from math import isfinite
from typing import Any
from xml.sax.saxutils import escape

from reportlab.graphics.charts.lineplots import LinePlot
from reportlab.graphics.shapes import Drawing, Rect, String
from reportlab.lib import colors
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)


DISCLAIMER = (
    "Generated from the patient's uploaded records. "
    "Not a diagnosis or medical advice."
)


def _text(value: Any) -> str:
    return escape(str(value)) if value is not None else ""


def _paragraph(value: Any, style: ParagraphStyle) -> Paragraph:
    return Paragraph(_text(value), style)


def _display_date(value: Any) -> str:
    if not value:
        return "Date unavailable"
    try:
        return datetime.fromisoformat(str(value)).strftime("%b %d, %Y")
    except ValueError:
        return str(value)


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if isfinite(number) else None


def _chart(analyte: str, trend: dict[str, Any], styles: Any) -> Drawing | None:
    numeric_series = []
    for point in trend.get("series", []):
        value = _number(point.get("value"))
        if value is not None:
            numeric_series.append((point, value))
    points = [(index, value) for index, (_, value) in enumerate(numeric_series)]
    if len(points) < 2:
        return None

    refs = [
        _number(point.get(boundary))
        for point, _ in numeric_series
        for boundary in ("ref_low", "ref_high")
    ]
    values = [value for _, value in points]
    finite_refs = [value for value in refs if value is not None]
    minimum = min(values + finite_refs) if finite_refs else min(values)
    maximum = max(values + finite_refs) if finite_refs else max(values)
    if minimum == maximum:
        minimum -= 1
        maximum += 1
    padding = (maximum - minimum) * 0.12
    minimum -= padding
    maximum += padding

    width, height = 440, 220
    left, bottom, plot_width, plot_height = 48, 34, 375, 150
    drawing = Drawing(width, height)
    low = _number(numeric_series[0][0].get("ref_low"))
    high = _number(numeric_series[0][0].get("ref_high"))
    has_complete_band = all(
        _number(point.get("ref_low")) is not None and _number(point.get("ref_high")) is not None
        for point, _ in numeric_series
    )
    if has_complete_band and low is not None and high is not None:
        y = bottom + (low - minimum) / (maximum - minimum) * plot_height
        band_height = (high - low) / (maximum - minimum) * plot_height
        drawing.add(Rect(left, y, plot_width, max(0, band_height), fillColor=colors.HexColor("#e7f3ef"), strokeColor=None))

    plot = LinePlot()
    plot.x = left
    plot.y = bottom
    plot.width = plot_width
    plot.height = plot_height
    plot.data = [points]
    plot.lines[0].strokeColor = colors.HexColor("#17695a")
    plot.lines[0].strokeWidth = 2
    plot.xValueAxis.valueMin = 0
    plot.xValueAxis.valueMax = max(1, len(points) - 1)
    plot.xValueAxis.labels.fontSize = 7
    plot.yValueAxis.valueMin = minimum
    plot.yValueAxis.valueMax = maximum
    plot.yValueAxis.labels.fontSize = 8
    drawing.add(plot)
    drawing.add(String(left, height - 18, _text(analyte), fontName="Helvetica-Bold", fontSize=10, fillColor=colors.HexColor("#172525")))
    dates = ", ".join(
        _display_date(point.get("report_date"))
        for point in trend.get("series", [])
        if _number(point.get("value")) is not None
    )
    drawing.add(String(left, 10, _text(f"Dates: {dates}"), fontName="Helvetica", fontSize=7, fillColor=colors.HexColor("#6c7a7a")))
    return drawing


def _footer(canvas: Any, document: Any) -> None:
    canvas.saveState()
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(colors.HexColor("#6c7a7a"))
    canvas.drawString(document.leftMargin, 0.42 * inch, DISCLAIMER)
    canvas.drawRightString(LETTER[0] - document.rightMargin, 0.42 * inch, f"Page {document.page}")
    canvas.restoreState()


def _medications_section(story: list[Any], styles: Any, data: dict[str, Any]) -> None:
    """Reserved section hook for future medication data."""
    return None


def build_doctor_summary_pdf(data: dict[str, Any]) -> bytes:
    """Build a doctor-visit summary from already-fetched, trusted-shaped data."""
    buffer = BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=LETTER,
        rightMargin=0.55 * inch,
        leftMargin=0.55 * inch,
        topMargin=0.55 * inch,
        bottomMargin=0.7 * inch,
        title="Doctor visit summary",
    )
    base = getSampleStyleSheet()
    styles = type("ExportStyles", (), {})()
    styles.title = ParagraphStyle("ExportTitle", parent=base["Title"], fontName="Helvetica-Bold", fontSize=20, textColor=colors.HexColor("#0f453a"), spaceAfter=8)
    styles.heading = ParagraphStyle("ExportHeading", parent=base["Heading2"], fontName="Helvetica-Bold", fontSize=13, textColor=colors.HexColor("#17695a"), spaceBefore=12, spaceAfter=7)
    styles.body = ParagraphStyle("ExportBody", parent=base["BodyText"], fontName="Helvetica", fontSize=9, leading=12)
    styles.small = ParagraphStyle("ExportSmall", parent=styles.body, fontSize=8, textColor=colors.HexColor("#6c7a7a"))
    styles.table = ParagraphStyle("ExportTable", parent=styles.body, fontSize=8, leading=10)
    styles.question = ParagraphStyle("ExportQuestion", parent=styles.body, leftIndent=8, bulletIndent=0, spaceAfter=5)

    patient = data.get("patient") or {}
    reports = data.get("reports") or []
    abnormal = data.get("abnormal_labs") or []
    trends = data.get("trends") or {}
    date_from = data.get("date_from")
    date_to = data.get("date_to")
    story: list[Any] = [
        _paragraph("Doctor-visit summary", styles.title),
        _paragraph(f"Generated {_display_date(data.get('generated_date') or date.today().isoformat())}", styles.small),
        _paragraph(f"Covered date range: {_display_date(date_from)} - {_display_date(date_to)}", styles.small),
    ]
    identity = (("Patient", patient.get("patient_name")), ("Age", patient.get("patient_age_at_report")), ("Sex", patient.get("patient_sex")))
    details = [f"{label}: {_text(value)}" for label, value in identity if value not in (None, "")]
    if details:
        story.append(_paragraph(" | ".join(details), styles.body))

    story.extend([_paragraph("Abnormal lab results", styles.heading)])
    if abnormal:
        rows = [[_paragraph(label, styles.table) for label in ("Investigation", "Value + unit", "Reference range", "Flag", "Report date")]]
        for lab in abnormal:
            value = lab.get("result_value")
            if value in (None, ""):
                value = lab.get("result_numeric")
            unit = lab.get("result_unit") or ""
            low = lab.get("ref_low")
            high = lab.get("ref_high")
            reference = lab.get("ref_interval_raw") or ""
            if not reference and (low is not None or high is not None):
                reference = f"{'' if low is None else low} - {'' if high is None else high}"
            flag = "High" if high is not None and _number(value) is not None and _number(value) > high else "Low"
            rows.append([
                _paragraph(lab.get("investigation") or "Unknown test", styles.table),
                _paragraph(f"{value} {unit}".strip(), styles.table),
                _paragraph(reference, styles.table),
                _paragraph(flag, styles.table),
                _paragraph(_display_date(lab.get("report_date")), styles.table),
            ])
        table = Table(rows, colWidths=[1.35 * inch, 1.05 * inch, 1.25 * inch, 0.5 * inch, 1.05 * inch], repeatRows=1)
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e7f3ef")),
            ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#dce7e5")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 5),
            ("RIGHTPADDING", (0, 0), (-1, -1), 5),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ]))
        story.append(table)
    else:
        story.append(_paragraph("No abnormal lab results were recorded in this date range.", styles.body))

    story.append(_paragraph("Trend charts", styles.heading))
    chart_count = 0
    for analyte, trend in trends.items():
        if chart_count >= 6:
            break
        chart = _chart(analyte, trend, styles)
        if chart is not None:
            story.extend([chart, Spacer(1, 8)])
            chart_count += 1
    if chart_count == 0:
        story.append(_paragraph("No abnormal analytes had at least two numeric results in this date range.", styles.body))

    story.append(_paragraph("Recent reports", styles.heading))
    if reports:
        report_rows = [[_paragraph(label, styles.table) for label in ("Report type", "Date", "Doctor", "Patient-facing summary")]]
        for report in reports:
            summary = report.get("summary") if report.get("summary_status") == "completed" else ""
            if summary and len(summary) > 700:
                summary = summary[:697].rstrip() + "..."
            report_rows.append([
                _paragraph(report.get("report_type") or "Medical report", styles.table),
                _paragraph(_display_date(report.get("report_date")), styles.table),
                _paragraph(report.get("referring_doctor") or "", styles.table),
                _paragraph(summary, styles.table),
            ])
        report_table = Table(report_rows, colWidths=[1.15 * inch, 0.9 * inch, 1.15 * inch, 2.0 * inch], repeatRows=1)
        report_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e7f3ef")),
            ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#dce7e5")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 5),
            ("RIGHTPADDING", (0, 0), (-1, -1), 5),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ]))
        story.append(report_table)
    else:
        story.append(_paragraph("No reports were recorded in this date range.", styles.body))

    story.append(_paragraph("Questions for the doctor", styles.heading))
    questions = data.get("questions") or []
    if questions:
        for question in questions:
            story.append(_paragraph(f"• {_text(question)}", styles.question))
    else:
        story.append(_paragraph("No questions added.", styles.body))

    _medications_section(story, styles, data)
    document.build(story, onFirstPage=_footer, onLaterPages=_footer)
    return buffer.getvalue()