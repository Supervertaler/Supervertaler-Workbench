"""Big projects without the freeze (issues #185 and #203): the faster code
paths must do exactly what the slow ones did.

A 300-file, 17,000-segment Trados package froze Workbench for over ten
minutes. These tests pin the behaviour of the rewritten paths – row styling,
the file-name banners, the tag-view refresh, the files counter and the
progress repaint – including what happens when something fails half-way.
They run the real methods of SupervertalerQt on a window whose __init__ never
ran (importing Supervertaler.py takes about 20 s).
"""

from __future__ import annotations

import os
import sys
from types import SimpleNamespace

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtWidgets import QApplication, QLabel, QTableWidget, QTableWidgetItem, QTextEdit


@pytest.fixture(scope="module")
def S():
    S._app = QApplication.instance() or QApplication([])   # unreferenced, it is deleted at once
    saved = sys.stdout, sys.stderr
    import Supervertaler   # re-wraps stdout/stderr on Windows at import
    S._wrappers = sys.stdout, sys.stderr   # keep alive: closing them closes pytest's capture
    sys.stdout, sys.stderr = saved
    return Supervertaler


def _bare_window(S, **attrs):
    """A SupervertalerQt whose __init__ never ran; a missing attribute raises
    AttributeError (so ``hasattr`` works) instead of sip's RuntimeError."""
    class Bare(S.SupervertalerQt):
        def __getattr__(self, name):
            raise AttributeError(name)
    win = Bare.__new__(Bare)
    win.logged = []
    win.log = win.logged.append
    for name, value in attrs.items():
        setattr(win, name, value)
    return win


# ── grid cells are styled once per row ───────────────────────────────────

def test_a_row_applies_each_cell_style_once_and_last_wins(S, monkeypatch):
    applied = []
    real = S.EditableGridTextEditor.setStyleSheet
    monkeypatch.setattr(S.EditableGridTextEditor, "setStyleSheet",
                        lambda self, sheet: (applied.append(sheet), real(self, sheet)))
    table = QTableWidget(1, 4)
    with S._GridStyleBatch():
        editor = S.EditableGridTextEditor("Doel", table, 0, table)
        editor.apply_grid_font_size(13)
        editor.set_background_color("#fff8e1")
        assert applied == []                                   # nothing applied yet
    assert len(applied) == 1                                   # once, on leaving the row
    assert "13" in applied[0] and "#fff8e1" in applied[0]      # with the final values
    editor.apply_grid_font_size(9)                             # outside a batch: at once
    assert len(applied) == 2 and "9" in applied[1]


def test_nested_batches_apply_only_when_the_outer_one_ends(S):
    rebuilt = []

    class Cell:
        def _rebuild_grid_stylesheet(self):
            if S._GridStyleBatch.defer(self):
                return
            rebuilt.append(self)

    cell = Cell()
    with S._GridStyleBatch():
        with S._GridStyleBatch():
            cell._rebuild_grid_stylesheet()
        assert rebuilt == []
        cell._rebuild_grid_stylesheet()
    assert rebuilt == [cell]                                   # one rebuild for two requests


def test_a_failing_row_still_styles_its_cells_and_ends_the_batch(S):
    rebuilt = []

    class Cell:
        def _rebuild_grid_stylesheet(self):
            if S._GridStyleBatch.defer(self):
                return
            rebuilt.append(self)

    class Deleted:
        def _rebuild_grid_stylesheet(self):
            if S._GridStyleBatch.defer(self):
                return
            raise RuntimeError("wrapped C/C++ object has been deleted")

    cell = Cell()
    with pytest.raises(ValueError):
        with S._GridStyleBatch():
            Deleted()._rebuild_grid_stylesheet()
            cell._rebuild_grid_stylesheet()
            raise ValueError("bad segment")
    assert rebuilt == [cell]
    assert S._GridStyleBatch.depth == 0 and S._GridStyleBatch.pending == {}
    cell._rebuild_grid_stylesheet()                            # later rebuilds are not held back
    assert rebuilt == [cell, cell]


# ── file-name banners: only for the rows on screen ───────────────────────

def _multifile_grid(S, rows=3000, per_file=10):
    table = QTableWidget(rows, 4)
    for r in range(rows):
        table.setRowHeight(r, 20)
    table.resize(800, 400)
    table.show()
    QApplication.processEvents()
    segments = [SimpleNamespace(id=r + 1, file_id=r // per_file, file_name=f"doc{r // per_file:03d}.docx")
                for r in range(rows)]
    project = SimpleNamespace(is_multifile=True, segments=segments)
    return _bare_window(S, table=table, current_project=project)


def test_banners_are_made_only_for_the_files_on_screen(S):
    win = _multifile_grid(S)
    win._update_file_boundary_labels()
    labels = win._file_boundary_labels
    height = win.table.viewport().height()
    assert 0 < len(labels) <= height // (10 * 20) + 2          # not one per file in the project
    assert all(-20 <= lbl.y() <= height for lbl in labels)
    names = [lbl.text().strip() for lbl in labels]
    assert names[0].endswith("doc001.docx")                    # row 10 starts the 2nd file


def test_scrolling_moves_the_banners_and_drops_the_old_ones(S):
    win = _multifile_grid(S)
    win._update_file_boundary_labels()
    before = list(win._file_boundary_labels)
    win.table.verticalScrollBar().setValue(1500)               # far down
    QApplication.processEvents()
    win._update_file_boundary_labels()
    after = win._file_boundary_labels
    assert after and not set(map(id, after)) & set(map(id, before))
    assert all(lbl.parent() is None for lbl in before)         # removed from the grid
    first_row = win.table.rowAt(0)
    assert int(after[0].text().strip()[-8:-5]) >= first_row // 10


def test_no_banners_for_a_single_file_project(S):
    win = _multifile_grid(S)
    win.current_project.is_multifile = False
    win._update_file_boundary_labels()
    assert win._file_boundary_labels == []


# ── switching the tag view: built rows only, each from its own segment ──

class _Cell(QTextEdit):
    def __init__(self, fail=False):
        super().__init__()
        self.shown, self.fail = [], fail

    def update_display_mode(self, text, show_tags):
        if self.fail:
            raise ValueError("bad tag")
        self.shown.append((text, show_tags))


def _display_mode_window(S, fail_on_row=None):
    segs = [S.Segment(id=i, source=f"<b>Bron {i}</b>", target=f"Doel {i}") for i in (1, 2, 3)]
    table = QTableWidget(3, 4)
    for row, seg_id in enumerate((3, 1, 2)):                   # sorted view: rows ≠ ids
        table.setItem(row, 0, QTableWidgetItem(str(seg_id)))
    cells = {}
    for row in (0, 2):                                         # row 1: not built yet
        cells[row] = (_Cell(), _Cell(fail=(row == fail_on_row)))
        table.setCellWidget(row, 2, cells[row][0])
        table.setCellWidget(row, 3, cells[row][1])
    win = _bare_window(S, table=table, current_project=SimpleNamespace(segments=segs),
                       tag_view_mode="tags", hide_outer_wrapping_tags=False,
                       _suppress_target_change_handlers=False)
    win.apply_invisible_replacements = lambda text: text
    win.auto_resize_rows = lambda: None
    return win, cells


def test_switching_the_tag_view_renders_each_built_row_from_its_own_segment(S):
    win, cells = _display_mode_window(S)
    win.tag_view_mode = "compact"
    win._refresh_grid_display_mode()
    assert cells[0][1].shown == [("Doel 3", True)]
    assert cells[2][1].shown == [("Doel 2", True)]
    assert cells[0][0].shown == [("<b>Bron 3</b>", True)]
    assert win._suppress_target_change_handlers is False


def test_a_failing_cell_does_not_leave_edits_switched_off(S):
    # The flag that ignores the editors' own textChanged during the refresh
    # was only reset at the end: after an error every later edit was dropped.
    win, cells = _display_mode_window(S, fail_on_row=0)
    with pytest.raises(ValueError):
        win._refresh_grid_display_mode()
    assert win._suppress_target_change_handlers is False


# ── status bar "Files: n/N" ──────────────────────────────────────────────

def test_the_files_counter_counts_fully_confirmed_files(S):
    segs = []
    for file_id, statuses in ((1, ["confirmed", "approved"]),       # done
                              (2, ["confirmed", "translated"]),     # not done
                              (3, ["proofread"])):                  # done
        segs += [S.Segment(id=len(segs) + 1, source="a b", target="x", status=st, file_id=file_id)
                 for st in statuses]
    files = [{"id": 1}, {"id": 2}, {"id": 3}, {"id": 4}]               # 4: no segments
    project = SimpleNamespace(segments=segs, is_multifile=True, files=files)
    labels = {name: QLabel() for name in ("progress_words_label", "progress_confirmed_label",
                                          "progress_remaining_label", "progress_files_label")}
    win = _bare_window(S, current_project=project, **labels)
    win.update_progress_stats()
    assert labels["progress_files_label"].text() == "📁 Files: 2/4"
    assert win.logged == []


# ── the import progress bar repaints at most ten times a second ─────────

def test_the_grid_progress_skips_repaints_but_never_the_last_one(S):
    prog = S._ImportProgressDialog(None, "Importing", initial_total=1000)
    with prog:
        prog.set_phase("Loading", total=1000)
        values = []
        real = prog._dialog.setValue
        prog._dialog.setValue = lambda v: (values.append(v), real(v))
        for done in range(1, 1001):
            prog.grid_callback(done, 1000)
    assert values[0] == 1 and values[-1] == 1000               # first and last always shown
    assert len(values) < 100                                   # not one repaint per row
