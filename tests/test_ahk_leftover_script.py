"""The AutoHotkey fallback ends only our own hotkey script (Nov 2026).

When the usual hotkeys (pynput) can't be set up, Workbench starts
supervertaler_hotkeys.ahk in AutoHotkey. Before starting it, it ran
`taskkill /F /IM AutoHotkey.exe`, which ended every AutoHotkey script on the
PC, the user's own included. Now only a copy of our script still running is
ended. psutil is a stand-in here: no real process is touched.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import psutil
import pytest

from modules import superlookup_tab


class Proc:
    def __init__(self, cmdline, error=None):
        self.info = {"cmdline": cmdline}
        self.killed, self._error = False, error

    def kill(self):
        if self._error:
            raise self._error
        self.killed = True


@pytest.fixture
def processes(monkeypatch):
    procs = {
        "ours": Proc([r"C:\Program Files\AutoHotkey\AutoHotkey.exe",
                      r"D:\Sv\Supervertaler\Supervertaler_Hotkeys.ahk"]),
        "users_own": Proc([r"C:\Program Files\AutoHotkey\AutoHotkey.exe", r"C:\Users\me\my-shortcuts.ahk"]),
        "plain_ahk": Proc([r"C:\Program Files\AutoHotkey\AutoHotkey.exe"]),
        "no_access": Proc(None),                       # a system process: cmdline can't be read
        "editor": Proc([r"C:\Windows\notepad.exe", r"C:\Users\me\notes.txt"]),
    }
    monkeypatch.setattr(psutil, "process_iter", lambda attrs=None: list(procs.values()))
    return procs


def test_only_our_hotkey_script_is_ended(processes):
    assert superlookup_tab.stop_leftover_hotkey_scripts() == 1
    assert processes["ours"].killed
    assert not any(p.killed for name, p in processes.items() if name != "ours")


def test_a_process_that_is_gone_or_protected_is_skipped(processes):
    gone = Proc(["AutoHotkey.exe", "supervertaler_hotkeys.ahk"], error=psutil.NoSuchProcess(123))
    denied = Proc(["AutoHotkey.exe", "supervertaler_hotkeys.ahk"], error=psutil.AccessDenied(456))
    processes.update(gone=gone, denied=denied)
    assert superlookup_tab.stop_leftover_hotkey_scripts() == 1      # ours still ended
    assert processes["ours"].killed


def test_nothing_ends_every_autohotkey_any_more():
    with open(superlookup_tab.__file__, encoding="utf-8") as f:
        source = f.read()
    assert "/IM', 'AutoHotkey.exe'" not in source and "/IM AutoHotkey" not in source
