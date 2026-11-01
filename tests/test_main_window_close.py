"""Closing the main window quits Workbench (issue #255).

SupervertalerQt used to define closeEvent twice. Python keeps the last one,
which had no explicit quit(); with the tray icon's quit-on-last-window-closed
turned off, the window's X hid the window and left the process running.

The behaviour tests import Supervertaler.py (about 20 s) and run the real
closeEvent / _tray_quit against a stand-in window object.
"""

from __future__ import annotations

import ast
import collections
import os
import subprocess
import sys
import types
from types import SimpleNamespace

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtGui import QCloseEvent
from PyQt6.QtWidgets import QApplication, QMessageBox


def _main_window_methods():
    with open(os.path.join(ROOT, "Supervertaler.py"), encoding="utf-8") as f:
        tree = ast.parse(f.read())
    [cls] = [n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "SupervertalerQt"]
    defs = collections.defaultdict(list)
    for item in cls.body:
        if isinstance(item, ast.FunctionDef):
            defs[item.name].append(item.lineno)
    return defs


def test_main_window_defines_each_qt_event_handler_once():
    # A second definition silently replaces the first – that is the #255 bug.
    dupes = {name: lines for name, lines in _main_window_methods().items()
             if name.endswith("Event") and len(lines) > 1}
    assert dupes == {}


# ── behaviour ────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def S():
    QApplication.instance() or QApplication([])
    saved = sys.stdout, sys.stderr
    import Supervertaler   # re-wraps stdout/stderr on Windows at import
    S._wrappers = sys.stdout, sys.stderr   # keep alive: closing them closes pytest's capture
    sys.stdout, sys.stderr = saved
    return Supervertaler


@pytest.fixture
def quits(S, monkeypatch):
    calls = []
    fake_app = SimpleNamespace(quit=lambda: calls.append("quit"))
    monkeypatch.setattr(S, "QApplication", SimpleNamespace(instance=lambda: fake_app))
    return calls


def _sleeper():
    """A real child process that would outlive us unless stopped."""
    return subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])


@pytest.fixture
def children():
    procs = []
    yield procs
    for p in procs:                 # never leak a sleeper if a test fails
        if p.poll() is None:
            p.kill()
            p.wait(5)


def make_window(S, children, *, ui_prefs=None, project_modified=False):
    from modules.okapi_sidecar import OkapiSidecar

    sidecar = OkapiSidecar(sidecar_dir=ROOT)       # stands in for the Java sidecar
    sidecar._process = _sleeper()
    sidecar._started_by_us = True
    ahk = _sleeper()                               # stands in for AutoHotkey
    children += [sidecar._process, ahk]
    hotkeys = SimpleNamespace(stopped=False)
    hotkeys.stop = lambda: setattr(hotkeys, "stopped", True)

    w = SimpleNamespace(
        _tray_icon=object(),
        _really_quit=False,
        _load_settings_section=lambda section: dict(ui_prefs or {}),
        _save_settings_section=lambda section, data: None,
        is_loading_model=False,
        project_modified=project_modified,
        okapi_sidecar=sidecar,
        lookup_tab=SimpleNamespace(_hotkey_manager=hotkeys, ahk_process=ahk),
        hidden=False,
        saves=0,
    )
    w.hide = lambda: setattr(w, "hidden", True)
    w.save_project = lambda: setattr(w, "saves", w.saves + 1)   # e.g. Save As cancelled
    for name in ("_stop_okapi_sidecar", "_cleanup_web_views", "_close_detached_log_windows"):
        setattr(w, name, types.MethodType(getattr(S.SupervertalerQt, name), w))
    w.sidecar_proc, w.ahk, w.hotkeys = sidecar._process, ahk, hotkeys
    return w


def close(S, w):
    event = QCloseEvent()
    S.SupervertalerQt.closeEvent(w, event)
    return event


def test_the_x_button_stops_children_and_quits(S, quits, children):
    w = make_window(S, children)
    event = close(S, w)
    assert event.isAccepted()
    assert quits == ["quit"]
    assert w.sidecar_proc.poll() is not None and w.ahk.poll() is not None
    assert w.hotkeys.stopped


def test_close_to_tray_hides_and_keeps_everything_running(S, quits, children):
    w = make_window(S, children, ui_prefs={"close_to_tray": True, "close_to_tray_hint_shown": True})
    event = close(S, w)
    assert not event.isAccepted() and w.hidden
    assert quits == []
    assert w.sidecar_proc.poll() is None and w.ahk.poll() is None


def test_a_failing_cleanup_step_still_quits(S, quits, children):
    # The app quits while a child process is still running and stopping it
    # fails: the quit must still happen, and the other child still stopped.
    w = make_window(S, children)

    def broken_stop():
        raise OSError("access denied")
    w.lookup_tab._hotkey_manager.stop = broken_stop
    w.okapi_sidecar.stop = broken_stop
    event = close(S, w)
    assert event.isAccepted()
    assert quits == ["quit"]
    assert w.ahk.poll() is not None


def test_cancel_on_unsaved_changes_keeps_the_app_open(S, quits, children, monkeypatch):
    monkeypatch.setattr(QMessageBox, "question",
                        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Cancel))
    w = make_window(S, children, project_modified=True)
    w._really_quit = True                         # came from tray → Quit
    event = close(S, w)
    assert not event.isAccepted()
    assert quits == []
    assert w._really_quit is False                # a later X can hide to tray again
    assert w.sidecar_proc.poll() is None          # nothing was torn down


def test_a_cancelled_save_keeps_the_app_open(S, quits, children, monkeypatch):
    monkeypatch.setattr(QMessageBox, "question",
                        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Save))
    w = make_window(S, children, project_modified=True)   # save_project leaves it modified
    event = close(S, w)
    assert w.saves == 1
    assert not event.isAccepted()
    assert quits == []


def test_discarding_unsaved_changes_quits(S, quits, children, monkeypatch):
    monkeypatch.setattr(QMessageBox, "question",
                        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Discard))
    w = make_window(S, children, project_modified=True)
    event = close(S, w)
    assert event.isAccepted() and quits == ["quit"]
    assert w.saves == 0


@pytest.mark.parametrize("closed, expected", [(True, ["quit"]), (False, [])])
def test_tray_quit_only_quits_if_the_window_agreed_to_close(S, quits, closed, expected):
    w = SimpleNamespace(_really_quit=False, close=lambda: closed)
    S.SupervertalerQt._tray_quit(w)
    assert w._really_quit is True
    assert quits == expected
