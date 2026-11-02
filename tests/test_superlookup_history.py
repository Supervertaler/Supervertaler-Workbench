"""SuperLookup's search history lives in workbench/settings/ with Workbench's
other settings files. It used to be read and written in <data>/settings/,
the folder those files moved out of (and where the products' shared
api-keys.json now lives); a history still there is read once."""

from __future__ import annotations

import json
import os
import sys
from types import SimpleNamespace

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtWidgets import QApplication


@pytest.fixture(scope="module")
def S():
    S._app = QApplication.instance() or QApplication([])   # unreferenced, it is deleted at once
    saved = sys.stdout, sys.stderr
    import Supervertaler   # re-wraps stdout/stderr on Windows at import
    S._wrappers = sys.stdout, sys.stderr   # keep alive: closing them closes pytest's capture
    sys.stdout, sys.stderr = saved
    return Supervertaler


def _tab(S, data_dir):
    tab = SimpleNamespace(main_window=SimpleNamespace(user_data_path=str(data_dir)))
    S.SuperlookupTab._init_search_history(tab)
    return tab


def _write(path, searches):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"searches": searches}), encoding="utf-8")


def test_the_history_is_kept_in_workbench_settings(S, tmp_path):
    tab = _tab(S, tmp_path)
    assert tab._search_history_file == tmp_path / "workbench" / "settings" / "superlookup_history.json"
    tab.search_history = ["pomp"]
    S.SuperlookupTab._save_search_history(tab)
    assert json.loads(tab._search_history_file.read_text(encoding="utf-8")) == {"searches": ["pomp"]}
    assert not (tmp_path / "settings").exists()                    # the shared folder is left alone


def test_a_history_in_the_old_place_is_read_once(S, tmp_path):
    _write(tmp_path / "settings" / "superlookup_history.json", ["oud", "ouder"])
    tab = _tab(S, tmp_path)
    assert tab.search_history == ["oud", "ouder"]
    S.SuperlookupTab._save_search_history(tab)                      # next save: the new place
    _write(tmp_path / "settings" / "superlookup_history.json", ["stale"])
    assert _tab(S, tmp_path).search_history == ["oud", "ouder"]    # the new file wins from now on


def test_a_damaged_history_starts_empty(S, tmp_path):
    path = tmp_path / "workbench" / "settings" / "superlookup_history.json"
    path.parent.mkdir(parents=True)
    path.write_text("{not json", encoding="utf-8")
    assert _tab(S, tmp_path).search_history == []


def test_a_history_that_cannot_be_saved_is_no_error(S, tmp_path):
    (tmp_path / "workbench").mkdir()
    (tmp_path / "workbench" / "settings").write_text("a file where the folder should be")
    tab = _tab(S, tmp_path)
    tab.search_history = ["pomp"]
    S.SuperlookupTab._save_search_history(tab)                      # no exception
