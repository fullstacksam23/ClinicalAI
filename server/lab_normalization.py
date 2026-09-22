from __future__ import annotations

import re
from collections import Counter
from typing import Any

import pandas as pd
from bs4 import BeautifulSoup

CANONICAL_COLUMNS = [
    "investigation",
    "result_value",
    "result_numeric",
    "result_unit",
    "ref_interval_raw",
    "ref_low",
    "ref_high",
    "method",
    "row_status",
    "raw_text",
]

_EMPTY_STATS = {
    "tables_seen": 0,
    "lab_tables": 0,
    "non_lab_tables": 0,
    "unknown_header_tables": 0,
    "rows_seen": 0,
    "rows_parsed": 0,
    "rows_section": 0,
    "rows_method_recovered": 0,
    "rows_skipped": 0,
    "rows_unparsed": 0,
    "review_required": False,
}

_SEPARATOR_CHARS = {":", "|"}
_METHOD_PREFIX_RE = re.compile(r"^method\s*:\s*(.+)$", re.IGNORECASE)


def _clean(value: Any) -> str:
    if value is None or pd.isna(value):
        return ""
    return str(value).strip()


def _normalized(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", _clean(value).lower())


def _span_count(value: Any) -> int:
    try:
        return max(1, int(value or 1))
    except (TypeError, ValueError):
        return 1


def _expanded_rows(table_html: str) -> list[list[str]]:
    soup = BeautifulSoup(table_html, "html.parser")
    grid: list[list[str | None]] = []
    for row_index, tr in enumerate(soup.find_all("tr")):
        while len(grid) <= row_index:
            grid.append([])
        column_index = 0
        for cell in tr.find_all(["th", "td"], recursive=False):
            while column_index < len(grid[row_index]) and grid[row_index][column_index] is not None:
                column_index += 1
            value = cell.get_text(" ", strip=True)
            rowspan = _span_count(cell.get("rowspan"))
            colspan = _span_count(cell.get("colspan"))
            for row_offset in range(rowspan):
                target_row = row_index + row_offset
                while len(grid) <= target_row:
                    grid.append([])
                while len(grid[target_row]) < column_index + colspan:
                    grid[target_row].append(None)
                for column_offset in range(colspan):
                    target_column = column_index + column_offset
                    if grid[target_row][target_column] is None:
                        grid[target_row][target_column] = value
            column_index += colspan
    return [[_clean(value) for value in row] for row in grid]


# BUG C FIX: a colspan header (e.g. "RESULT" spanning a colon-separator
# column and the real value column) duplicates header text across
# columns, and data rows put a bare ":" in the separator column. If a
# column's only non-empty content, across every data row, is a bare
# separator character, drop that column from every row (header included)
# before any mapping happens.
def _drop_separator_columns(rows: list[list[str]]) -> list[list[str]]:
    if len(rows) < 2:
        return rows
    data_rows = rows[1:]
    width = max(len(row) for row in rows)
    drop = set()
    for col in range(width):
        values = [row[col] for row in data_rows if col < len(row) and row[col]]
        if values and all(v.strip() in _SEPARATOR_CHARS for v in values):
            drop.add(col)
    if not drop:
        return rows
    keep = [c for c in range(width) if c not in drop]
    return [[row[c] if c < len(row) else "" for c in keep] for row in rows]


def _field_index(headers: list[str], *aliases: str) -> int | None:
    wanted = {_normalized(alias) for alias in aliases}
    for index, header in enumerate(headers):
        if _normalized(header) in wanted:
            return index
    return None


def _looks_numeric(value: str) -> bool:
    return bool(re.search(r"[-+]?\d+(?:[.,]\d+)?", value))


def _header_mapping(headers: list[str]) -> dict[str, int] | None:
    mapping = {}
    investigation = _field_index(headers, "Investigation", "Test", "Test Name", "Parameter", "Analyte", "Exam", "Description")
    result = _field_index(headers, "Result", "Value", "Observed Value", "Measured Value", "Result Value", "Findings")
    unit = _field_index(headers, "Unit", "Units", "Result Unit")
    reference = _field_index(headers, "Biological Ref. Interval", "Reference Range", "Reference Interval", "Normal Range", "Ref. Range", "Range")
    method = _field_index(headers, "Method", "Technique")
    if investigation is None or result is None:
        return None
    mapping["investigation"] = investigation
    mapping["result_value"] = result
    if unit is not None:
        mapping["result_unit"] = unit
    if reference is not None:
        mapping["ref_interval_raw"] = reference
    if method is not None:
        mapping["method"] = method
    return mapping


_FUZZY_ROLES = [
    ("unit", ("unit",)),
    ("ref_interval_raw", ("ref", "interval", "range", "normal")),
    ("result_value", ("result", "value", "finding", "observed", "measured")),
    ("investigation", ("investigation", "test", "parameter", "analyte", "exam", "description")),
    ("method", ("method", "technique")),
]


def _header_mapping_combined(headers: list[str]) -> dict[str, int] | None:
    mapping = dict(_header_mapping(headers) or {})
    used_columns = set(mapping.values())

    for role, keywords in _FUZZY_ROLES:
        if role in mapping:
            continue
        for index, header in enumerate(headers):
            if index in used_columns:
                continue
            norm = _normalized(header)
            if norm and any(keyword in norm for keyword in keywords):
                mapping[role] = index
                used_columns.add(index)
                break

    if "investigation" not in mapping or "result_value" not in mapping:
        return None
    return mapping


def _is_colspan_title(row: list[str]) -> bool:
    non_empty = [value for value in row if value]
    if len(non_empty) < 2:
        return False
    return len({_normalized(value) for value in non_empty}) == 1


_RANGE_RE = re.compile(r"\d\s*(?:-|–|—|to)\s*\d", re.IGNORECASE)
_VALUE_RE = re.compile(r"^\s*[-+]?\d+(?:[.,]\d+)?(?:\s*\S.*)?$")


def _cell_role(cell: str) -> str | None:
    if not cell:
        return None
    if cell.lstrip().startswith(("<", ">")) or _RANGE_RE.search(cell):
        return "ref_interval_raw"
    if _VALUE_RE.match(cell):
        return "result_value"
    return "text"


def _infer_headerless_mapping(rows: list[list[str]]) -> dict[str, int] | None:
    width = max((len(row) for row in rows), default=0)
    if width < 2:
        return None
    votes = [Counter(_cell_role(row[i]) for row in rows if i < len(row) and row[i]) for i in range(width)]
    top = [vote.most_common(1)[0][0] if vote else None for vote in votes]
    if top[0] != "text":
        return None
    mapping: dict[str, int] = {"investigation": 0}
    for i in range(1, width):
        role = top[i]
        if role in ("result_value", "ref_interval_raw") and role not in mapping:
            mapping[role] = i
        elif role == "text" and "method" not in mapping:
            mapping["method"] = i
    return mapping if "result_value" in mapping else None


def _is_header_like(row: list[str]) -> bool:
    aliases = {
        "investigation", "test", "testname", "parameter", "analyte", "exam", "description",
        "result", "value", "observedvalue", "measuredvalue", "resultvalue", "findings",
        "unit", "units", "resultunit", "referencerange", "referenceinterval", "normalrange",
        "refrange", "range", "method", "technique",
    }
    matches = sum(_normalized(value) in aliases for value in row if value)
    return matches >= 2 and not any(_looks_numeric(value) for value in row)


def _merge_header_rows(header: list[str], continuation: list[str]) -> list[str]:
    generic = {"result", "results", "measurement", "measurements"}
    merged = list(header)
    for index, value in enumerate(continuation):
        if index >= len(merged) or not value:
            continue
        if _normalized(merged[index]) in generic or not merged[index]:
            merged[index] = value
    return merged


def _parse_value(value: Any) -> tuple[float | None, str | None]:
    text = _clean(value).replace(",", ".")
    if not text:
        return None, None
    match = re.match(r"\s*([-+]?\d+(?:\.\d+)?)\s*(.*)$", text)
    if not match:
        return None, None
    unit = match.group(2).strip() or None
    return float(match.group(1)), unit


def _parse_range(value: Any) -> tuple[float | None, float | None]:
    text = _clean(value).replace(",", ".")
    if not text:
        return None, None
    number = r"[-+]?\d+(?:\.\d+)?"
    if text.startswith("<"):
        match = re.search(number, text)
        return None, float(match.group()) if match else None
    if text.startswith(">"):
        match = re.search(number, text)
        return float(match.group()) if match else None, None
    match = re.search(rf"({number})\s*(?:-|–|—|to)\s*({number})", text, re.IGNORECASE)
    if match:
        return float(match.group(1)), float(match.group(2))
    return None, None


def _raw_row_text(row: list[str]) -> str:
    return " | ".join(value for value in row if value)


def _empty_canonical(**overrides: Any) -> dict[str, Any]:
    row = {column: "" for column in CANONICAL_COLUMNS}
    row.update(overrides)
    return row


def _canonical_row(row: list[str], mapping: dict[str, int], status: str = "parsed") -> dict[str, Any]:
    def value_for(name: str) -> str:
        index = mapping.get(name)
        return row[index] if index is not None and index < len(row) else ""

    result_value = value_for("result_value")
    numeric, parsed_unit = _parse_value(result_value)
    explicit_unit = value_for("result_unit")
    reference = value_for("ref_interval_raw")
    low, high = _parse_range(reference)
    return {
        "investigation": value_for("investigation"),
        "result_value": result_value,
        "result_numeric": numeric,
        "result_unit": explicit_unit or parsed_unit,
        "ref_interval_raw": reference,
        "ref_low": low,
        "ref_high": high,
        "method": value_for("method"),
        "row_status": status,
        "raw_text": _raw_row_text(row),
    }


def _attach_method_or_section(
    text: str,
    table_rows: list[dict[str, Any]],
    stats: dict[str, Any],
    raw_text: str,
) -> None:
    prev = table_rows[-1] if table_rows else None
    if prev is not None and prev["row_status"] == "parsed" and not prev["method"]:
        match = _METHOD_PREFIX_RE.match(text)
        prev["method"] = match.group(1).strip() if match else text
        stats["rows_method_recovered"] += 1
    else:
        table_rows.append(_empty_canonical(
            investigation=text, row_status="section", raw_text=raw_text,
        ))
        stats["rows_section"] += 1


def normalize_tables(table_htmls: list[str]) -> tuple[pd.DataFrame, dict[str, Any]]:
    stats = dict(_EMPTY_STATS)
    canonical_rows: list[dict[str, Any]] = []
    stats["tables_seen"] = len(table_htmls)

    for table_html in table_htmls:
        rows = _expanded_rows(table_html)
        if not rows:
            stats["rows_skipped"] += 1
            stats["review_required"] = True
            continue

        rows = _drop_separator_columns(rows)
        first_row = rows[0]
        section_title: str | None = None
        seen_header: tuple[str, ...] | None = None

        if _is_colspan_title(first_row):
            section_title = next((value for value in first_row if value), "")
            data_rows = rows[1:]
            mapping = _infer_headerless_mapping(data_rows) if data_rows else None
            headerless = True
            expected_len = max((len(row) for row in data_rows), default=0)
        else:
            header = first_row
            data_start = 1
            if len(rows) > 1 and _is_header_like(rows[1]):
                header = _merge_header_rows(header, rows[1])
                data_start = 2
            data_rows = rows[data_start:]
            mapping = _header_mapping_combined(header)
            headerless = False
            expected_len = len(header)

            if mapping is None:
                mapping = _infer_headerless_mapping(rows)
                if mapping is not None:
                    data_rows = rows
                    headerless = True
                    expected_len = max((len(row) for row in rows), default=0)
            else:
                seen_header = tuple(_normalized(value) for value in header)

        if mapping is None:
            header_text = " ".join(first_row).lower()
            lab_hint = any(
                term in header_text
                for term in ("result", "value", "test", "investigation", "analyte", "unit", "range")
            )
            if lab_hint:
                stats["unknown_header_tables"] += 1
                stats["review_required"] = True
            else:
                stats["non_lab_tables"] += 1
            continue

        stats["lab_tables"] += 1
        table_rows: list[dict[str, Any]] = []

        if section_title is not None:
            table_rows.append(_empty_canonical(
                investigation=section_title, row_status="section", raw_text=section_title,
            ))
            stats["rows_section"] += 1

        for row in data_rows:
            stats["rows_seen"] += 1
            if not any(row):
                stats["rows_skipped"] += 1
                continue
            if not headerless and seen_header is not None and tuple(_normalized(v) for v in row) == seen_header:
                stats["rows_skipped"] += 1
                continue
            if _is_header_like(row):
                stats["rows_skipped"] += 1
                continue

            non_empty = [v for v in row if v]

            if len(row) != expected_len:
                # A row with at most one non-empty, non-numeric cell is either
                # a method/technique note for the row just above, or a section
                # subtitle. Otherwise it's a genuine shape mismatch.
                if len(non_empty) <= 1 and not _looks_numeric(non_empty[0] if non_empty else ""):
                    text = non_empty[0] if non_empty else ""
                    _attach_method_or_section(text, table_rows, stats, _raw_row_text(row))
                    continue
                table_rows.append(_empty_canonical(row_status="unparsed", raw_text=_raw_row_text(row)))
                stats["rows_unparsed"] += 1
                stats["review_required"] = True
                continue

            def cell(role: str, _row=row) -> str:
                index = mapping.get(role)
                return _row[index] if index is not None and index < len(_row) else ""

            if not cell("result_value") and not cell("ref_interval_raw") and cell("investigation"):
                # Length matched expected_len, but this is still a text-only
                # row (e.g. the separator column got dropped from it too,
                # same as from every real row) — same method-attach rule.
                _attach_method_or_section(cell("investigation"), table_rows, stats, _raw_row_text(row))
                continue

            table_rows.append(_canonical_row(row, mapping))
            stats["rows_parsed"] += 1

        canonical_rows.extend(table_rows)

    frame = pd.DataFrame(canonical_rows, columns=CANONICAL_COLUMNS)
    frame.attrs["extraction_stats"] = stats
    return frame, stats