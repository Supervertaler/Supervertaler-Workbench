"""Saving and reopening a project when something goes wrong (issue #228).

save_project_to_file used to write json.dump straight into the .svproj, so a
save that failed half-way left a cut-off project that no longer opened, and
the grid's sorted order was not put back. These tests run the real methods of
SupervertalerQt on a window object whose __init__ never ran (importing
Supervertaler.py takes about 20 s), with the message boxes replaced.
"""

from __future__ import annotations

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtWidgets import QApplication, QMessageBox


@pytest.fixture(scope="module")
def S():
    S._app = QApplication.instance() or QApplication([])   # unreferenced, it is deleted at once
    saved = sys.stdout, sys.stderr
    import Supervertaler   # re-wraps stdout/stderr on Windows at import
    S._wrappers = sys.stdout, sys.stderr   # keep alive: closing them closes pytest's capture
    sys.stdout, sys.stderr = saved
    return Supervertaler


@pytest.fixture
def messages(monkeypatch):
    shown = []

    def critical(parent, title, text, *a, **k):
        shown.append(("critical", title, text))
        return QMessageBox.StandardButton.Ok

    monkeypatch.setattr(QMessageBox, "critical", staticmethod(critical))
    return shown


def _bare_window(S):
    """A SupervertalerQt whose __init__ never ran. Reading an attribute it
    lacks raises AttributeError (so ``hasattr`` works) instead of sip's
    "__init__() was never called"."""
    class Bare(S.SupervertalerQt):
        def __getattr__(self, name):
            raise AttributeError(name)
    return Bare.__new__(Bare)


def _window(S, segments, sorted_segments=None):
    """A SupervertalerQt with only what save_project_to_file needs."""
    win = _bare_window(S)
    win.logged = []
    win.log = win.logged.append
    win.update_window_title = lambda: None
    win._maybe_make_versioned_backup = lambda path: None
    win.current_project = S.Project(name="Job", source_lang="en", target_lang="nl",
                                    segments=list(sorted_segments or segments))
    win._original_segment_order = list(segments)
    win.spellcheck_enabled = False
    for name in ('auto_center_active_segment', 'auto_propagate_exact_matches',
                 'auto_insert_100_percent_matches', 'auto_confirm_100_percent_matches',
                 'enable_termbase_grid_highlighting', 'termbase_hide_shorter_matches',
                 'enable_smart_word_selection', 'enable_sound_effects'):
        setattr(win, name, False)
    win.precision_scroll_divisor = 1
    win.project_modified = True
    return win


def _segments(S):
    return [S.Segment(id=i, source=f"Source {i}", target=f"Doel {i}", status="translated")
            for i in (1, 2, 3)]


def test_a_save_writes_document_order_and_keeps_the_previous_version(S, messages, tmp_path):
    segs = _segments(S)
    win = _window(S, segs, sorted_segments=list(reversed(segs)))   # grid sorted Z→A
    path = str(tmp_path / "Job.svproj")
    win.save_project_to_file(path)
    win.current_project.segments[0].target = "Doel 3, edited"
    win.save_project_to_file(path)

    assert messages == []
    saved = json.load(open(path, encoding="utf-8"))
    assert [s["id"] for s in saved["segments"]] == [1, 2, 3]
    backup = json.load(open(path + ".bak", encoding="utf-8"))
    assert [s["target"] for s in backup["segments"]] == ["Doel 1", "Doel 2", "Doel 3"]
    assert [s.id for s in win.current_project.segments] == [3, 2, 1]   # the grid's order is back
    assert win.project_modified is False


def test_a_failed_save_keeps_the_file_and_the_grid_order(S, messages, monkeypatch, tmp_path):
    segs = _segments(S)
    win = _window(S, segs, sorted_segments=list(reversed(segs)))
    path = str(tmp_path / "Job.svproj")
    win.save_project_to_file(path)
    good = open(path, encoding="utf-8").read()

    def disk_full(*a, **k):
        raise OSError("No space left on device")
    monkeypatch.setattr(S, "write_project_file", disk_full)
    win.current_project.segments[0].target = "lost edit"
    win.project_modified = True
    win.save_project_to_file(path)

    assert open(path, encoding="utf-8").read() == good                  # not cut off
    assert [s.id for s in win.current_project.segments] == [3, 2, 1]    # order restored in finally
    assert win.project_modified is True                                 # still unsaved
    assert messages and "No space left on device" in messages[0][2]


def test_a_value_that_cannot_be_saved_never_touches_the_file(S, messages, tmp_path):
    segs = _segments(S)
    win = _window(S, segs)
    path = str(tmp_path / "Job.svproj")
    win.save_project_to_file(path)
    good = open(path, encoding="utf-8").read()

    win.current_project.ui_settings = {"broken": object()}               # json cannot write this
    win.project_modified = True
    win.save_project_to_file(path)

    assert open(path, encoding="utf-8").read() == good
    assert not os.path.exists(path + ".bak") or json.load(open(path + ".bak", encoding="utf-8"))
    assert [n for n in os.listdir(tmp_path) if n.endswith(".tmp")] == []
    assert win.project_modified is True
    assert messages and messages[0][0] == "critical"


# ── opening a damaged project ────────────────────────────────────────────

def _damaged_project_with_backup(tmp_path, backup=True):
    path = tmp_path / "Job.svproj"
    path.write_text('{"name": "Job", "segments": [{"id": 1, "sou', encoding="utf-8")
    if backup:
        (tmp_path / "Job.svproj.bak").write_text(
            json.dumps({"name": "Job", "segments": [{"id": 1, "source": "a", "target": "b"}]}),
            encoding="utf-8")
    return str(path)


def _question(monkeypatch, answer):
    asked = []

    def question(parent, title, text, *a, **k):
        asked.append(text)
        return answer
    monkeypatch.setattr(QMessageBox, "question", staticmethod(question))
    return asked


def _recovery_window(S):
    win = _bare_window(S)
    win.logged = []
    win.log = win.logged.append
    return win


def test_a_damaged_project_offers_its_backup(S, monkeypatch, tmp_path):
    path = _damaged_project_with_backup(tmp_path)
    asked = _question(monkeypatch, QMessageBox.StandardButton.Yes)
    try:
        json.load(open(path, encoding="utf-8"))
    except json.JSONDecodeError as damaged:
        data = _recovery_window(S)._offer_project_backup(path, damaged)
    assert data["segments"][0]["target"] == "b"
    assert len(asked) == 1 and "Job.svproj" in asked[0]


def test_declining_the_backup_reports_the_damage(S, monkeypatch, tmp_path):
    path = _damaged_project_with_backup(tmp_path)
    _question(monkeypatch, QMessageBox.StandardButton.No)
    assert _recovery_window(S)._offer_project_backup(path, ValueError("cut off")) is None


def test_no_backup_means_no_question(S, monkeypatch, tmp_path):
    path = _damaged_project_with_backup(tmp_path, backup=False)
    asked = _question(monkeypatch, QMessageBox.StandardButton.Yes)
    assert _recovery_window(S)._offer_project_backup(path, ValueError("cut off")) is None
    assert asked == []
