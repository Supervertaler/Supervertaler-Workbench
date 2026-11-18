"""The data-folder pointer (config.json) may start with a BOM (Nov 2026).

Notepad and Windows PowerShell 5 can save it that way. Plain utf-8 rejects
the BOM, so Workbench silently used the default data folder - all the user's
data seemed to vanish - and if the default folder had anything in it,
"auto-recovery" then overwrote the pointer, losing the user's choice for good.
Meanwhile the API-key reader (utf-8-sig) still followed the pointer, so keys
and everything else came from two different folders.
"""

from __future__ import annotations

import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from modules import user_data_location as udl


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A pointer file, a chosen data folder and a default folder, all in tmp."""
    appdata = tmp_path / "appdata"
    pointer = appdata / "Supervertaler" / "config.json"
    pointer.parent.mkdir(parents=True)
    chosen = tmp_path / "D_Supervertaler"
    chosen.mkdir()
    default = tmp_path / "home" / "Supervertaler"
    monkeypatch.setattr(udl, "get_config_pointer_path", lambda: pointer)
    monkeypatch.setattr(udl, "get_default_user_data_path", lambda: default)
    monkeypatch.setenv("APPDATA", str(appdata))           # for the other readers
    monkeypatch.setenv("XDG_CONFIG_HOME", str(appdata))
    return pointer, chosen, default


def write_pointer(pointer, folder, bom):
    text = json.dumps({"user_data_path": str(folder)})
    pointer.write_bytes((b"\xef\xbb\xbf" if bom else b"") + text.encode("utf-8"))


@pytest.mark.parametrize("bom", [False, True])
def test_the_chosen_folder_is_used_with_or_without_a_bom(home, bom):
    pointer, chosen, default = home
    write_pointer(pointer, chosen, bom)
    assert udl.load_user_data_path_from_config() == chosen
    assert udl.get_user_data_path() == chosen


@pytest.mark.skipif(sys.platform == "darwin", reason="the other readers look in ~/Library on macOS")
@pytest.mark.parametrize("reader", ["ui_scale", "bridge_server", "trados_client"])
def test_every_other_reader_of_the_pointer_accepts_a_bom(home, reader):
    pointer, chosen, default = home
    write_pointer(pointer, chosen, bom=True)
    if reader == "ui_scale":
        from modules import ui_scale
        found = ui_scale._user_data_path()
    elif reader == "bridge_server":
        from modules import supervertaler_bridge_server as m
        found = m._resolve_user_data_root()
    else:
        from modules import trados_bridge_client as m
        found = m._resolve_user_data_root()
    assert found == chosen


def test_an_unreadable_pointer_is_never_overwritten(home):
    # The failure path: the pointer can't be read, and the default folder has
    # data in it. Workbench falls back to the default, but must leave the
    # pointer alone so the user's choice still works once it is fixed.
    pointer, chosen, default = home
    pointer.write_text("{ not json", encoding="utf-8")
    (default / "settings").mkdir(parents=True)
    (default / "settings" / "x.json").write_text("{}", encoding="utf-8")
    assert udl.get_user_data_path() == default
    assert pointer.read_text(encoding="utf-8") == "{ not json"


def test_a_pointer_to_a_missing_folder_is_not_overwritten(home):
    # For example the data folder is on a drive that isn't plugged in.
    pointer, chosen, default = home
    gone = chosen.parent / "unplugged"
    write_pointer(pointer, gone, bom=False)
    (default / "x").mkdir(parents=True)
    (default / "x" / "y.txt").write_text("y", encoding="utf-8")
    assert udl.get_user_data_path() == default
    assert json.loads(pointer.read_text(encoding="utf-8"))["user_data_path"] == str(gone)


def test_a_missing_pointer_is_still_recovered(home):
    # Unchanged: no pointer at all and data in the default folder -> the
    # pointer is recreated there.
    pointer, chosen, default = home
    (default / "x").mkdir(parents=True)
    (default / "x" / "y.txt").write_text("y", encoding="utf-8")
    assert udl.get_user_data_path() == default
    assert json.loads(pointer.read_text(encoding="utf-8"))["user_data_path"] == str(default)
