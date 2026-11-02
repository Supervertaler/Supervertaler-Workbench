"""The Clipboard Manager was removed in v1.10.374. Its history table can hold
anything the user ever copied (passwords included), and nothing in Workbench
can show or clear it any more, so the next start removes it - and the copied
text must not survive in the database file's free pages either.
"""

from __future__ import annotations

import os
import sqlite3
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from modules.database_manager import DatabaseManager
from modules.database_migrations import drop_clipboard_history_table

SECRET = "correct-horse-battery-staple-4f1c"


def _open(path):
    d = DatabaseManager(db_path=str(path), log_callback=lambda *a, **k: None)
    d.connect()
    return d


def _plant_old_history(d, rows=300):
    """Recreate the table exactly as v1.10.373 left it, with a clip in it."""
    d.cursor.execute("""
        CREATE TABLE clipboard_history (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            text       TEXT,
            copied_at  TEXT    DEFAULT (datetime('now')),
            pasted     INTEGER DEFAULT 0,
            kind       TEXT    DEFAULT 'text',
            image_data BLOB
        )
    """)
    d.cursor.execute("CREATE INDEX idx_clipboard_id ON clipboard_history(id)")
    d.cursor.executemany(
        "INSERT INTO clipboard_history (text) VALUES (?)",
        [(f"{SECRET} {i}",) for i in range(rows)])
    d.connection.commit()


def _file_bytes(path):
    data = b""
    for p in (str(path), str(path) + "-wal"):
        if os.path.exists(p):
            with open(p, "rb") as f:
                data += f.read()
    return data


def _has_table(d):
    d.cursor.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='clipboard_history'")
    return d.cursor.fetchone() is not None


def test_new_database_has_no_clipboard_table(tmp_path):
    d = _open(tmp_path / "new.db")
    try:
        assert not _has_table(d)
    finally:
        d.close()


def test_start_up_removes_old_history_and_its_text(tmp_path):
    path = tmp_path / "old.db"
    d = _open(path)
    _plant_old_history(d)
    d.close()
    assert SECRET.encode() in _file_bytes(path)   # the plant really landed

    d = _open(path)                                # = the next Workbench start
    try:
        assert not _has_table(d)
        d.cursor.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        d.close()
    assert SECRET.encode() not in _file_bytes(path)


def test_removal_restores_secure_delete_setting(tmp_path):
    d = _open(tmp_path / "s.db")
    try:
        _plant_old_history(d, rows=3)
        d.cursor.execute("PRAGMA secure_delete = 0")
        assert drop_clipboard_history_table(d) is True
        assert d.cursor.execute("PRAGMA secure_delete").fetchone()[0] == 0
        assert not _has_table(d)
    finally:
        d.close()


def test_nothing_to_remove_is_success(tmp_path):
    d = _open(tmp_path / "n.db")
    try:
        assert drop_clipboard_history_table(d) is True
    finally:
        d.close()


def test_a_failing_database_reports_false_without_raising():
    conn = sqlite3.connect(":memory:")
    broken = types.SimpleNamespace(connection=conn, cursor=conn.cursor())
    conn.close()                    # every call on it now raises
    assert drop_clipboard_history_table(broken) is False


def test_the_removed_modules_are_gone():
    for name in ("clipboard_manager_widget", "snippet_library", "text_conversion_library"):
        with pytest.raises(ModuleNotFoundError):
            __import__(f"modules.{name}")
