from lab_normalization import normalize_tables


def test_mixed_headers_are_normalized_independently():
    tables = [
        "<table><tr><th>Investigation</th><th>Result</th><th>Unit</th></tr><tr><td>Glucose</td><td>5</td><td>mmol/L</td></tr></table>",
        "<table><tr><th>Test Name</th><th>Value</th><th>Units</th><th>Reference Range</th></tr><tr><td>TSH</td><td>7.2</td><td>mIU/L</td><td>0.4 - 4</td></tr></table>",
    ]
    frame, stats = normalize_tables(tables)
    assert frame["investigation"].tolist() == ["Glucose", "TSH"]
    assert frame["result_value"].tolist() == ["5", "7.2"]
    assert frame["result_unit"].tolist() == ["mmol/L", "mIU/L"]
    assert stats["rows_parsed"] == 2


def test_rowspan_and_colspan_are_expanded():
    table = """
    <table><tr><th rowspan='2'>Investigation</th><th colspan='2'>Result</th></tr>
    <tr><th>Value</th><th>Unit</th></tr>
    <tr><td>Hemoglobin</td><td>12.4</td><td>g/dL</td></tr></table>
    """
    frame, stats = normalize_tables([table])
    assert stats["lab_tables"] == 1
    assert frame.iloc[0]["investigation"] == "Hemoglobin"
    assert frame.iloc[0]["result_value"] == "12.4"


def test_ambiguous_short_row_is_unparsed():
    table = "<table><tr><th>Investigation</th><th>Result</th><th>Unit</th></tr><tr><td>Platelets</td><td>100</td></tr></table>"
    frame, stats = normalize_tables([table])
    assert frame.iloc[0]["row_status"] == "unparsed"
    assert frame.iloc[0]["raw_text"] == "Platelets | 100"
    assert stats["rows_unparsed"] == 1
    assert stats["review_required"] is True


def test_headerless_unit_column_is_supported():
    table = "<table><tr><td>Potassium</td><td>mmol/L</td><td>4.2</td></tr></table>"
    frame, stats = normalize_tables([table])
    assert stats["rows_parsed"] == 1
    assert frame.iloc[0]["result_value"] == "4.2"
    assert frame.iloc[0]["result_unit"] == "mmol/L"


def test_qualitative_repeated_headers_and_missing_ranges():
    table = """
    <table><tr><th>Test</th><th>Result</th><th>Reference Range</th></tr>
    <tr><td>Protein</td><td>Negative</td><td></td></tr>
    <tr><td>Test</td><td>Result</td><td>Reference Range</td></tr>
    <tr><td>Ketones</td><td>Trace</td><td></td></tr></table>
    """
    frame, stats = normalize_tables([table])
    assert frame["investigation"].tolist() == ["Protein", "Ketones"]
    assert frame["result_numeric"].isna().all()
    assert frame["ref_low"].isna().all()
    assert stats["rows_skipped"] == 1


def test_non_lab_table_is_not_routed_to_lab_rows():
    table = "<table><tr><th>Patient Name</th><th>Age</th><th>Sex</th></tr><tr><td>Alex</td><td>42</td><td>F</td></tr></table>"
    frame, stats = normalize_tables([table])
    assert frame.empty
    assert stats["non_lab_tables"] == 1
    assert stats["lab_tables"] == 0


if __name__ == "__main__":
    test_mixed_headers_are_normalized_independently()
    test_rowspan_and_colspan_are_expanded()
    test_ambiguous_short_row_is_unparsed()
    test_headerless_unit_column_is_supported()
    test_qualitative_repeated_headers_and_missing_ranges()
    test_non_lab_table_is_not_routed_to_lab_rows()
    print("OCR table normalization fixtures passed")