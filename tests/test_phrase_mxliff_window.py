"""File > Import/Export > Phrase (Memsource) > MXLIFF, through the real
methods of SupervertalerQt (on a window whose __init__ never ran, as in
test_project_save_recovery.py), with the file dialogs and message boxes
replaced and the grid/TM set-up stubbed out. Everything is written to
pytest's temp folder.
"""

from __future__ import annotations

import contextlib
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtWidgets import QApplication, QFileDialog, QMessageBox

from test_mxliff_handler import make
from modules.mxliff_handler import MxliffDocument


@pytest.fixture(scope="module")
def S():
    S._app = QApplication.instance() or QApplication([])
    saved = sys.stdout, sys.stderr
    import Supervertaler
    S._wrappers = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = saved
    return Supervertaler


@pytest.fixture
def shown(monkeypatch):
    shown = []
    for kind in ("critical", "information", "warning"):
        def box(parent, title, text, *a, _k=kind, **kw):
            shown.append((_k, title, text))
            return QMessageBox.StandardButton.Ok
        monkeypatch.setattr(QMessageBox, kind, staticmethod(box))
    monkeypatch.setattr(QMessageBox, "question", staticmethod(
        lambda *a, **k: QMessageBox.StandardButton.No))
    return shown


def _window(S, monkeypatch):
    class Bare(S.SupervertalerQt):
        def __getattr__(self, name):
            raise AttributeError(name)
    win = Bare.__new__(Bare)
    win.logged = []
    win.log = win.logged.append
    win.tr = lambda text: text
    win.project_file_path = None
    win.current_project = None
    win._check_reimport_same_file = lambda path, attrs: (False, None, None, None)
    for name in ("_apply_reimport_settings", "update_window_title", "load_segments_to_grid",
                 "initialize_tm_database", "_clear_caches_after_import",
                 "_deactivate_all_resources_for_new_project", "auto_resize_rows",
                 "_initialize_spellcheck_for_target_language", "_finalise_import_with_indexes"):
        setattr(win, name, lambda *a, **k: None)

    @contextlib.contextmanager
    def no_progress(*a, **k):
        yield type("P", (), {"grid_callback": None})()
    monkeypatch.setattr(S, "_ImportProgressDialog", no_progress)
    return win


def _pick(monkeypatch, open_path=None, save_path=None):
    monkeypatch.setattr(QFileDialog, "getOpenFileName",
                        staticmethod(lambda *a, **k: (str(open_path or ""), "")))
    monkeypatch.setattr(QFileDialog, "getSaveFileName",
                        staticmethod(lambda *a, **k: (str(save_path or ""), "")))


def test_import_edit_export_round_trip(S, monkeypatch, shown, tmp_path):
    src = tmp_path / "Acme_PROJ-001.mxliff"
    src.write_bytes(make())
    win = _window(S, monkeypatch)
    _pick(monkeypatch, open_path=src)
    win.import_phrase_mxliff()

    assert shown[-1][0] == "information", shown
    project = win.current_project
    assert len(project.segments) == 5
    assert project.source_lang == "English" and project.target_lang == "Dutch"
    assert project.mxliff_source_path == str(src)
    seg = project.segments
    assert seg[0].status == "confirmed" and seg[3].locked
    assert seg[1].source.startswith("{b>Warning<b}")

    # the source path survives saving and reopening the project
    reopened = S.Project.from_dict(project.to_dict())
    assert reopened.mxliff_source_path == str(src)

    # unchanged export: identical bytes
    out = tmp_path / "unchanged.mxliff"
    _pick(monkeypatch, save_path=out)
    win.export_phrase_mxliff()
    assert out.read_bytes() == src.read_bytes()

    # translate and confirm one segment
    seg[2].target = "Open de behuizing niet."
    seg[2].status = "confirmed"
    out2 = tmp_path / "translated.mxliff"
    _pick(monkeypatch, save_path=out2)
    win.export_phrase_mxliff()
    assert shown[-1][0] == "information", shown
    unit = MxliffDocument.load(str(out2)).units[2]
    assert unit.target == "Open de behuizing niet." and unit.confirmed


def test_import_refuses_a_bad_file(S, monkeypatch, shown, tmp_path):
    bad = tmp_path / "bad.mxliff"
    bad.write_bytes(b"<xliff><file>")
    win = _window(S, monkeypatch)
    _pick(monkeypatch, open_path=bad)
    win.import_phrase_mxliff()
    assert shown[-1][0] == "critical" and "well-formed" in shown[-1][2]
    assert win.current_project is None
    assert QApplication.overrideCursor() is None       # wait cursor restored


def test_export_refuses_when_segments_were_split(S, monkeypatch, shown, tmp_path):
    src = tmp_path / "Acme_PROJ-001.mxliff"
    src.write_bytes(make())
    win = _window(S, monkeypatch)
    _pick(monkeypatch, open_path=src)
    win.import_phrase_mxliff()
    win.current_project.segments.append(S.Segment(id=6, source="extra", target="extra"))
    out = tmp_path / "out.mxliff"
    _pick(monkeypatch, save_path=out)
    win.export_phrase_mxliff()
    assert shown[-1][0] == "critical" and "Nothing was written" in shown[-1][2]
    assert not out.exists()
    assert QApplication.overrideCursor() is None


# ── WYSIWYG view of Phrase tags ─────────────────────────────────────────────

def test_phrase_tags_render_as_formatting(S):
    html = S.get_formatted_html_display("{b>Warning<b}: {bi>both<bi}, {sup>2<sup}, {1} & <b>")
    assert "<b>Warning</b>" in html
    assert "<b><i>both</i></b>" in html
    assert "<sup>2</sup>" in html
    assert "color:#888888" in html and "{1}" in html          # grey marker
    assert "&amp; &lt;b&gt;" in html                         # a literal <b> stays literal
    # Workbench's own tags are unaffected
    assert S.get_formatted_html_display("<b>x</b>").count("<b>x</b>") == 1
    assert not S.has_phrase_tags("plain {curly} text <b>x</b>")


def _wysiwyg_cell(S, text):
    from PyQt6.QtWidgets import QTableWidget
    table = QTableWidget(1, 4)
    cell = S.EditableGridTextEditor("", table, 0, table)
    cell.update_display_mode(text, False)       # False = WYSIWYG
    return cell, table


@pytest.mark.parametrize("text", [
    "{b>Warning<b}: read PROJ-001 first.",
    "Plain, {bi>bold italic<bi} and {i>italic<i}.",
    "H{sub>2<sub}O and x{sup>2<sup}, {u>under<u}.",
    "Press {1} then {2>Start<2}.",
    "{b>Bold with {1} inside<b} after",
])
def test_unedited_wysiwyg_cell_reads_back_unchanged(S, text):
    cell, _t = _wysiwyg_cell(S, text)
    win = S.SupervertalerQt.__new__(S.SupervertalerQt)
    assert S.SupervertalerQt._wysiwyg_document_to_tagged_text(win, cell) == text


def test_wysiwyg_edit_saves_phrase_syntax_and_exports(S, monkeypatch, shown, tmp_path):
    from PyQt6.QtGui import QTextCursor
    src = tmp_path / "Acme_PROJ-001.mxliff"
    src.write_bytes(make())
    win = _window(S, monkeypatch)
    _pick(monkeypatch, open_path=src)
    win.import_phrase_mxliff()
    seg = win.current_project.segments[1]
    assert seg.target == "{b>Waarschuwing<b}: lees eerst PROJ-001."

    # Edit inside the bold word in WYSIWYG view, as a translator would
    cell, _t = _wysiwyg_cell(S, seg.target)
    cursor = cell.textCursor()
    cursor.setPosition(len("Waarschuwing"))
    cursor.insertText("en")                          # typed inside bold → bold
    cursor.movePosition(QTextCursor.MoveOperation.End)
    cursor.insertText(" Nu.")
    seg.target = S.SupervertalerQt._wysiwyg_document_to_tagged_text(win, cell)
    assert seg.target == "{b>Waarschuwingen<b}: lees eerst PROJ-001. Nu."
    assert "<b>" not in seg.target

    out = tmp_path / "out.mxliff"
    _pick(monkeypatch, save_path=out)
    win.export_phrase_mxliff()
    assert shown[-1][0] == "information", shown
    text = out.read_text(encoding="utf-8")
    assert "<target>{b&gt;Waarschuwingen&lt;b}: lees eerst PROJ-001. Nu.</target>" in text
    assert MxliffDocument.load(str(out)).units[1].target == seg.target


def test_phrase_html_rendering_is_cheap_at_scale(S):
    import time
    texts = [f"{{b>Acme {i}<b}} PROJ-001 {{1}} {{bi>x<bi}} end" for i in range(20_000)]
    t0 = time.perf_counter()
    for t in texts:
        S.get_formatted_html_display(t)
    assert time.perf_counter() - t0 < 2.0     # ~0.1 s locally for 20,000 cells


# ── Phrase bilingual DOCX: the source path survives save → reopen ─────────

def test_phrase_docx_source_path_survives_reopen(S, monkeypatch, shown, tmp_path):
    import json
    docx = tmp_path / "Acme_PROJ-001_bilingual.docx"
    docx.write_bytes(b"stand-in")
    project = S.Project(name="Job", source_lang="English", target_lang="Dutch",
                        segments=[S.Segment(id=1, source="A", target="B")])
    project.phrase_source_path = str(docx)
    reopened = S.Project.from_dict(json.loads(json.dumps(project.to_dict())))
    assert reopened.phrase_source_path == str(docx)

    loaded = []

    class FakeHandler:
        def load(self, path):
            loaded.append(path)
            return True

        def extract_source_segments(self):
            return []
    import modules.phrase_docx_handler as pdh
    monkeypatch.setattr(pdh, "PhraseDOCXHandler", FakeHandler)
    asked = []
    monkeypatch.setattr(QMessageBox, "question", staticmethod(
        lambda *a, **k: asked.append(a) or QMessageBox.StandardButton.No))
    _pick(monkeypatch, save_path="")                  # stop at the save dialog

    win = _window(S, monkeypatch)
    win.current_project = reopened                    # a freshly opened project
    win.export_phrase_bilingual()
    assert asked == []                                # did not ask for the file
    assert loaded == [str(docx)]
