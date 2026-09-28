"""Concordance search results export (issue #170).

The exported file must hold exactly the hits shown, in order, with non-ASCII
text intact in both formats, and TM text that happens to start with "=" must
stay text – openpyxl otherwise stores it as a formula.
"""

from __future__ import annotations

import csv
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from modules.concordance_export import export_concordance_results

ROWS = [
    {'source': 'Zażółć gęślą jaźń', 'target': 'Grüße aus Köln', 'tm_name': 'Project TM'},
    {'source': '=SUM(A1:A2) is a formula', 'target': '-5 mm', 'tm_name': 'Client TM'},
    {'source': 'Line one\nline two', 'target': 'Regel één\nregel twee', 'tm_name': 'Client TM'},
]


def test_csv_round_trip(tmp_path):
    path = tmp_path / "hits.csv"
    count = export_concordance_results(ROWS, str(path), query="gęślą",
                                       source_label="Polish", target_label="German")
    assert count == 3

    raw = path.read_bytes()
    assert raw.startswith(b'\xef\xbb\xbf')  # BOM so Excel reads UTF-8

    with open(path, encoding='utf-8-sig', newline='') as f:
        table = list(csv.reader(f))
    assert table[0] == ["Polish", "German", "TM"]
    assert table[1:] == [[r['source'], r['target'], r['tm_name']] for r in ROWS]


def test_xlsx_round_trip(tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    path = tmp_path / "hits.xlsx"
    export_concordance_results(ROWS, str(path), query="=gęślą",
                               source_label="Polish", target_label="German")

    wb = openpyxl.load_workbook(path)
    ws = wb["Concordance"]
    values = [[c.value for c in row] for row in ws.iter_rows()]
    assert values[0] == ["Polish", "German", "TM"]
    assert values[1:] == [[r['source'], r['target'], r['tm_name']] for r in ROWS]
    assert ws.cell(row=3, column=1).data_type == 's'  # not stored as a formula

    info = {row[0].value: row[1] for row in wb["Search"].iter_rows()}
    assert info["Search term"].value == "=gęślą"
    assert info["Search term"].data_type == 's'
    assert info["Hits"].value == 3


def test_unknown_extension_falls_back_to_csv(tmp_path):
    path = tmp_path / "hits.txt"
    export_concordance_results(ROWS[:1], str(path))
    with open(path, encoding='utf-8-sig', newline='') as f:
        assert list(csv.reader(f))[0] == ["Source", "Target", "TM"]
