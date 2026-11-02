"""Exports after sorting the grid (PR #252).

Sorting the grid reorders current_project.segments itself, and the
round-trip exports pair the n-th segment with the n-th unit of the original
file. After a sort by source text, every translation went into the wrong
unit: the translation of "Apple" ended up under "Zebra". The exports now read
the segments in document order. These tests run the real export on a window
whose __init__ never ran (importing Supervertaler.py takes about 20 s).
"""

from __future__ import annotations

import os
import sys
from xml.etree import ElementTree as ET

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtWidgets import QApplication, QFileDialog, QMessageBox

from modules import mqxliff_handler as mq


@pytest.fixture(scope="module")
def S():
    S._app = QApplication.instance() or QApplication([])   # unreferenced, it is deleted at once
    saved = sys.stdout, sys.stderr
    import Supervertaler   # re-wraps stdout/stderr on Windows at import
    S._wrappers = sys.stdout, sys.stderr   # keep alive: closing them closes pytest's capture
    sys.stdout, sys.stderr = saved
    return Supervertaler


def _bare_window(S, **attrs):
    class Bare(S.SupervertalerQt):
        def __getattr__(self, name):
            raise AttributeError(name)
    win = Bare.__new__(Bare)
    win.logged = []
    win.log = win.logged.append
    for name, value in attrs.items():
        setattr(win, name, value)
    return win


MQXLIFF = """<?xml version="1.0" encoding="utf-8"?>
<xliff xmlns="urn:oasis:names:tc:xliff:document:1.2" xmlns:mq="MQXliff" version="1.2">
<file original="Fruit.docx" source-language="en-gb" target-language="nl-nl"><body>
<trans-unit id="1" mq:status="NotStarted"><source>Zebra</source><target></target></trans-unit>
<trans-unit id="2" mq:status="NotStarted"><source>Mango</source><target></target></trans-unit>
</body></file>
<file original="More.docx" source-language="en-gb" target-language="nl-nl"><body>
<trans-unit id="1" mq:status="NotStarted"><source>Apple</source><target></target></trans-unit>
</body></file>
</xliff>
"""


def _project(S, sorted_by_source):
    segs = [S.Segment(id=1, source="Zebra", target="Zebra (nl)", status="confirmed"),
            S.Segment(id=2, source="Mango", target="Mango (nl)", status="confirmed"),
            S.Segment(id=3, source="Apple", target="Appel", status="confirmed")]
    shown = sorted(segs, key=lambda s: s.source) if sorted_by_source else list(segs)
    return S.Project(name="Fruit", source_lang="en", target_lang="nl", segments=shown), segs


def test_document_order_however_the_grid_is_sorted(S):
    project, segs = _project(S, sorted_by_source=True)
    assert [s.source for s in project.segments] == ["Apple", "Mango", "Zebra"]
    win = _bare_window(S, current_project=project, _original_segment_order=list(segs))
    assert [s.id for s in win._segments_in_document_order()] == [1, 2, 3]
    assert [s.source for s in project.segments] == ["Apple", "Mango", "Zebra"]   # grid untouched


def test_ids_that_are_not_numbers_fall_back_to_the_saved_order(S):
    project, segs = _project(S, sorted_by_source=True)
    for s in segs:
        s.id = f"seg-{s.source}"
    win = _bare_window(S, current_project=project, _original_segment_order=list(segs))
    assert [s.source for s in win._segments_in_document_order()] == ["Zebra", "Mango", "Apple"]


def test_no_project_means_no_segments(S):
    assert _bare_window(S, current_project=None)._segments_in_document_order() == []


@pytest.mark.parametrize("sorted_by_source", [False, True])
def test_memoq_xliff_export_puts_each_translation_in_its_own_unit(S, monkeypatch, tmp_path, sorted_by_source):
    source = tmp_path / "fruit.mqxliff"
    source.write_text(MQXLIFF, encoding="utf-8")
    handler = mq.MQXLIFFHandler()
    assert handler.load(str(source))
    project, segs = _project(S, sorted_by_source)
    win = _bare_window(S, current_project=project, _original_segment_order=list(segs),
                       mqxliff_source_file=str(source), mqxliff_handler=handler)
    out = tmp_path / "fruit_translated.mqxliff"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(out), "")))
    shown = []
    for kind in ("information", "warning", "critical"):
        monkeypatch.setattr(QMessageBox, kind, staticmethod(
            lambda parent, title, text, *a, _k=kind, **k: shown.append((_k, text))))

    win.export_memoq_xliff()

    assert [k for k, _ in shown] == ["information"]
    units = ET.parse(out).getroot().findall(".//{%s}trans-unit" % mq.XLIFF_NS)
    pairs = [(u.find("{%s}source" % mq.XLIFF_NS).text, u.find("{%s}target" % mq.XLIFF_NS).text)
             for u in units]
    assert pairs == [("Zebra", "Zebra (nl)"), ("Mango", "Mango (nl)"), ("Apple", "Appel")]
    assert all(u.get("{MQXliff}status") == "ManuallyConfirmed" for u in units)
