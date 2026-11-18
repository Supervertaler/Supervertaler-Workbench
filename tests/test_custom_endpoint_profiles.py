"""Settings → AI Settings: the Custom (OpenAI-compatible) endpoint profiles (Nov 2026).

Save (also clicked by the auto-saver) read the profiles from settings.json
again instead of using the page's own list, which + and − change. So a profile
added since the page opened was never saved, a removed one came back, an edit
to a profile other than the one shown was lost, and after a removal the shown
profile's fields were written into the profile that had been removed's place.

These build the real AI Settings page on a stand-in window, against scratch
folders (the `scratch` fixture checks every path before a test runs).
"""

from __future__ import annotations

import json
import os
import sys
import traceback
from pathlib import Path

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtWidgets import (QApplication, QComboBox, QInputDialog, QLineEdit, QMainWindow,
                             QMessageBox, QPushButton)

from modules import api_key_store as store
from test_api_key_store import WINDOWS, locked_map, scratch  # noqa: F401

# Supervertaler.py wraps the console streams in UTF-8 when imported on Windows.
_streams = sys.stdout, sys.stderr
SV = pytest.importorskip("Supervertaler")
_wrapped = sys.stdout, sys.stderr
sys.stdout, sys.stderr = _streams
Window = SV.SupervertalerQt


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(app, scratch, monkeypatch):
    # No dialog may open: answer them all.
    for name in ("information", "warning", "critical"):
        monkeypatch.setattr(QMessageBox, name, staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok))
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))
    w = Window.__new__(Window)
    QMainWindow.__init__(w)
    w.user_data_path = Path(store.data_root())
    assert str(w._get_unified_settings_path()) == scratch.settings
    w.current_project = None
    w.ollama_keepwarm_timer = None
    w.logged = []
    w.log = w.logged.append
    # An error inside a button's handler would otherwise end the whole test
    # run without a word (PyQt aborts): record it and fail the test instead.
    errors = []
    monkeypatch.setattr(sys, "excepthook", lambda t, v, tb: errors.append("".join(traceback.format_exception(t, v, tb))))
    yield w
    w.deleteLater()
    assert errors == [], errors[0]


def write_profiles(window, profiles, active=""):
    llm = window.load_llm_settings()
    llm["custom_openai_profiles"] = profiles
    llm["custom_openai_active_profile"] = active
    window.save_llm_settings(llm)


def saved(window):
    with open(window._get_unified_settings_path(), encoding="utf-8") as f:
        llm = json.load(f)["ui"]["llm_settings"]
    return llm["custom_openai_profiles"], llm["custom_openai_active_profile"]


class Page:
    """The AI Settings page and the controls of its Custom endpoint group."""

    def __init__(self, window):
        self.tab = window._create_ai_settings_tab()
        buttons = self.tab.findChildren(QPushButton)
        self.add = next(b for b in buttons if b.toolTip() == "Add a new custom API profile")
        self.remove = next(b for b in buttons if b.toolTip() == "Remove the selected profile")
        self.save = next(b for b in buttons if b.property("settings_save_button"))
        self.combo = next(c for c in self.tab.findChildren(QComboBox)
                          if self.add.parentWidget() is c.parentWidget() and c.minimumWidth() == 200)
        fields = {e.placeholderText(): e for e in self.tab.findChildren(QLineEdit)}
        self.endpoint = fields["https://api.example.com/v1"]
        self.model = fields["e.g. qwen-plus, ep-xxxxx, deepseek-chat"]
        self.key = fields["API key for this endpoint (leave empty if it needs none)"]
        for w in (self.add, self.remove, self.combo, self.endpoint, self.model, self.key):
            w.setEnabled(True)          # enabled only while Custom is the chosen provider

    def add_profile(self, monkeypatch, name):
        monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: (name, True)))
        self.add.click()

    def fill(self, endpoint, model, key):
        # As typed: each field reports editingFinished when the user leaves it.
        for field, text in ((self.endpoint, endpoint), (self.model, model), (self.key, key)):
            field.setText(text)
            field.editingFinished.emit()

    def show(self, name):
        self.combo.setCurrentIndex(self.combo.findText(name))


A = {"name": "A", "endpoint": "https://a.example/v1", "model": "a-model", "api_key": "key-a"}
B = {"name": "B", "endpoint": "https://b.example/v1", "model": "b-model", "api_key": "key-b"}


def test_profiles_added_on_the_page_are_saved(window, monkeypatch):
    page = Page(window)
    page.add_profile(monkeypatch, "Volcengine")
    page.fill("https://v.example/v1", "doubao", "key-v")
    page.add_profile(monkeypatch, "Qwen")
    page.fill("https://q.example/v1", "qwen-plus", "key-q")
    page.save.click()
    profiles, active = saved(window)
    assert profiles == [
        {"name": "Volcengine", "endpoint": "https://v.example/v1", "model": "doubao", "api_key": "key-v"},
        {"name": "Qwen", "endpoint": "https://q.example/v1", "model": "qwen-plus", "api_key": "key-q"},
    ]
    assert active == "Qwen"


def test_a_profile_added_to_existing_ones_is_saved(window, monkeypatch):
    write_profiles(window, [dict(A)], "A")
    page = Page(window)
    page.add_profile(monkeypatch, "B")
    page.fill(B["endpoint"], B["model"], B["api_key"])
    page.save.click()
    assert saved(window) == ([A, B], "B")


def test_a_removed_profile_stays_removed_and_the_other_is_untouched(window):
    write_profiles(window, [dict(A), dict(B)], "A")
    page = Page(window)
    page.show("A")
    page.remove.click()
    assert page.combo.currentText() == "B"
    page.save.click()
    # Before the fix: [A with B's fields, B] - A back, and wrong.
    assert saved(window) == ([B], "B")


def test_removing_the_last_profile_saves_none(window):
    write_profiles(window, [dict(A)], "A")
    page = Page(window)
    page.remove.click()
    page.save.click()
    assert saved(window) == ([], "")


def test_an_edit_to_a_profile_not_shown_at_save_is_kept(window):
    write_profiles(window, [dict(A), dict(B)], "A")
    page = Page(window)
    page.show("A")
    page.fill("https://a2.example/v1", "a-model-2", "key-a2")
    page.show("B")
    page.save.click()
    profiles, active = saved(window)
    assert profiles[0] == {"name": "A", "endpoint": "https://a2.example/v1", "model": "a-model-2", "api_key": "key-a2"}
    assert profiles[1] == B and active == "B"


def test_saved_profiles_are_shown_when_the_page_opens_again(window, monkeypatch):
    page = Page(window)
    page.add_profile(monkeypatch, "Volcengine")
    page.fill("https://v.example/v1", "doubao", "key-v")
    page.save.click()
    again = Page(window)
    assert [again.combo.itemText(i) for i in range(again.combo.count())] == ["Volcengine"]
    assert (again.endpoint.text(), again.model.text(), again.key.text()) == ("https://v.example/v1", "doubao", "key-v")


@pytest.mark.parametrize("answer", [("", True), ("New", False), ("A", True)], ids=["empty", "cancelled", "duplicate"])
def test_a_refused_or_cancelled_add_changes_nothing(window, monkeypatch, answer):
    write_profiles(window, [dict(A)], "A")
    page = Page(window)
    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: answer))
    page.add.click()
    page.save.click()
    assert saved(window) == ([A], "A")


def test_a_declined_remove_changes_nothing(window, monkeypatch):
    write_profiles(window, [dict(A), dict(B)], "A")
    page = Page(window)
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.No))
    page.remove.click()
    page.save.click()
    assert saved(window) == ([A, B], "A")


@pytest.mark.skipif(not WINDOWS, reason="the Windows locked file")
def test_a_removed_profiles_locked_key_goes_too(window, scratch):
    write_profiles(window, [dict(A), dict(B)], "A")
    page = Page(window)
    page.save.click()
    assert set(locked_map(scratch.locked)) >= {"workbench:custom_openai:A", "workbench:custom_openai:B"}
    page.show("A")
    page.remove.click()
    page.save.click()
    assert "workbench:custom_openai:A" not in locked_map(scratch.locked)
    assert store.get("workbench:custom_openai:B") == "key-b"
