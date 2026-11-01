"""Settings → AI Settings / MT Settings and the start-up step, with the shared
API keys (step 1 of "API keys locked to the computer").

Runs Workbench's own methods on a stand-in for the main window - no window is
opened and nothing is started - against scratch folders checked before each
test (see test_api_key_store.py). Importing Supervertaler.py takes a while.
"""

from __future__ import annotations

import json
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QLabel, QLineEdit, QWidget

from modules import api_key_store as store
from test_api_key_store import (K1, K2, OAI_OWN, OAI_SHARED, DL, WINDOWS, lock_with_entropy, locked_map,
                                plain_map, scratch, write_locked, write_plain, write_settings)  # noqa: F401

pytestmark = pytest.mark.skipif(not WINDOWS, reason="the Windows locked file")

# Supervertaler.py wraps the console streams in UTF-8 when imported on Windows.
# Keep its wrappers alive (a collected one closes the stream under it) and
# give pytest its own back.
_streams = sys.stdout, sys.stderr
SV = pytest.importorskip("Supervertaler")
_wrapped = sys.stdout, sys.stderr
sys.stdout, sys.stderr = _streams
Window = SV.SupervertalerQt


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


class FakeWindow:
    """Just what the methods under test use, taken from the real class."""
    _get_settings_dir = Window._get_settings_dir
    _get_unified_settings_path = Window._get_unified_settings_path
    _load_unified_settings = Window._load_unified_settings
    _save_unified_settings = Window._save_unified_settings
    _load_settings_section = Window._load_settings_section
    _save_settings_section = Window._save_settings_section
    load_llm_settings = Window.load_llm_settings
    load_api_keys = Window.load_api_keys
    save_api_keys = Window.save_api_keys
    _api_key_problem = Window._api_key_problem
    _prepare_api_keys = Window._prepare_api_keys
    _save_api_keys_from_ui = Window._save_api_keys_from_ui
    _with_key_note = Window._with_key_note

    def __init__(self, scratch):
        from pathlib import Path
        self.user_data_path = Path(store.data_root())
        assert str(self._get_unified_settings_path()) == scratch.settings
        self.logged = []

    def log(self, message):
        self.logged.append(message)


def build_rows(window, names):
    """The key rows as the settings page builds them."""
    from modules.llm_clients import unreadable_key_notes
    current = window.load_api_keys()
    notes = unreadable_key_notes()
    window._api_key_inputs = {}
    rows = {}
    for name in names:
        box = QLineEdit()
        box.setText(current.get(name, ''))
        rows[name] = window._with_key_note(QWidget(), name, box, notes)
        window._api_key_inputs[name] = box
    return rows


def test_an_unreadable_key_gets_m1_under_its_box_until_a_new_one_is_typed(app, scratch):
    window = FakeWindow(scratch)
    write_locked(scratch, {"claude": lock_with_entropy(K1)})
    write_settings(scratch, {"claude": K2})          # Workbench's own older copy: not shown
    rows = build_rows(window, ["claude", "openai"])
    box = window._api_key_inputs["claude"]
    assert box.text() == ""
    notes = rows["claude"].findChildren(QLabel)
    assert [n.text() for n in notes] == [
        "Your Anthropic key can't be read on this computer – it was saved under another Windows sign-in, "
        "or your Windows password was reset. Paste the key in again; nothing else is affected."]
    assert not rows["openai"].findChildren(QLabel)
    rows["claude"].show()
    assert notes[0].isVisible()
    QTest.keyClicks(box, "x")
    assert not notes[0].isVisible()


def test_the_page_saves_only_the_keys_the_user_changed_to_the_shared_store(app, scratch):
    window = FakeWindow(scratch)
    write_plain(scratch, openai=OAI_SHARED, claude=K1)
    write_locked(scratch, {"gemini": lock_with_entropy(K1)})
    write_settings(scratch, {"openai": OAI_OWN})
    build_rows(window, ["openai", "claude", "gemini", "deepl", "ollama_endpoint"])
    # Meanwhile another product replaces the OpenAI key.
    write_plain(scratch, openai="sk-newer-PROJ-001", claude=K1)
    window._api_key_inputs["claude"].setText(K2)        # changed
    window._api_key_inputs["deepl"].setText(DL)         # added
    window._api_key_inputs["ollama_endpoint"].setText("http://localhost:11434")
    window._save_api_keys_from_ui()
    plain = plain_map(scratch.plain)
    assert plain.get("openai") == "sk-newer-PROJ-001"   # the untouched box did not put back the older key
    assert plain.get("claude") == K2 and plain.get("deepl") == DL and plain.get("ollama_endpoint") is None
    assert "gemini" in locked_map(scratch.locked)        # left empty because unreadable: kept
    own = json.load(open(scratch.settings, encoding="utf-8"))["api_keys"]
    assert own["claude"] == K2 and own["deepl"] == DL and own["ollama_endpoint"] == "http://localhost:11434"
    # Cleared: removed from both shared files.
    window._api_key_inputs["deepl"].setText("")
    window._save_api_keys_from_ui()
    assert store.status("deepl") == store.ABSENT and window.logged == []


def test_a_save_that_does_not_take_is_said_and_tried_again(app, scratch):
    window = FakeWindow(scratch)
    write_settings(scratch, {})
    build_rows(window, ["deepl"])
    window._api_key_inputs["deepl"].setText(DL)
    write_plain(scratch)
    fd = store._open_fd(scratch.plain, store._GENERIC_READ, 0, store._OPEN_EXISTING)   # held elsewhere
    try:
        window._save_api_keys_from_ui()
    finally:
        os.close(fd)
    assert window.logged == ["⚠ Your DeepL key could not be saved to the keys Supervertaler's products share. "
                             "Try again in a moment."]
    window._save_api_keys_from_ui()                      # the next auto-save tries again
    assert plain_map(scratch.plain).get("deepl") == DL


def test_start_up_joins_once_says_m8_once_and_locks_the_keys(app, scratch):
    window = FakeWindow(scratch)
    write_plain(scratch, openai=OAI_SHARED)
    write_settings(scratch, {"openai": OAI_OWN, "deepl": DL},
                   profiles=[{"name": "Acme", "endpoint": "http://localhost:1/v1", "model": "m", "api_key": K2}])
    window._prepare_api_keys()
    QTest.qWait(3300)
    m8 = ("Workbench now uses the same API keys as Supervertaler for Trados, memoQ and Sidekick. "
          "Your Workbench key for OpenAI was different; the shared one is used from now on.")
    assert window.logged == [m8]
    general = json.load(open(scratch.settings, encoding="utf-8"))["general"]
    assert general["api_key_lock"]["joined"]
    assert set(locked_map(scratch.locked)) == {"openai", "deepl", "workbench:custom_openai:Acme"}
    assert window.load_api_keys()["openai"] == OAI_SHARED
    window.logged.clear()
    window._prepare_api_keys()
    QTest.qWait(3300)
    assert window.logged == []


def test_missing_key_messages_name_the_settings_page(app, scratch):
    window = FakeWindow(scratch)
    write_settings(scratch, {})
    assert window._api_key_problem("claude") == "No API key set for Anthropic. Add one in Settings → AI Settings."
    assert window._api_key_problem("deepl") == "No API key set for DeepL. Add one in Settings → MT Settings."
    write_locked(scratch, {"google": lock_with_entropy(K1)})
    assert window._api_key_problem("google_translate").startswith(
        "Can't use your Google Translate key – it can't be read on this computer")
