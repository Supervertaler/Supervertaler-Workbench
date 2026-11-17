"""TM Manager > Import/Export > Import TMX uses the TMs tab's import.

Its own import wrote to the database from a background thread through the
main thread's SQLite connection. SQLite refused every write ("objects created
in a thread can only be used in that same thread"), the errors only reached
the log, and it reported "Successfully imported N entries" all the same.
"""

from __future__ import annotations

import os
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

pytest.importorskip("PyQt6")

import modules.tm_manager_qt as tmq  # noqa: E402


def test_the_broken_thread_import_is_gone():
    assert not hasattr(tmq, "TMXImportThread")


def test_import_tmx_goes_through_the_tms_tab_import():
    seen = []

    class App:
        tm_metadata_mgr = object()

        def _import_tmx_as_tm(self, mgr, table, refresh):
            seen.append((mgr, table))
            refresh()

    class Dialog:
        parent_app = App()
        refreshed = []

        def refresh_browser(self):
            self.refreshed.append("browser")

        def refresh_stats(self):
            self.refreshed.append("stats")

    d = Dialog()
    tmq.TMManagerDialog.import_tmx(d)
    assert seen == [(App.tm_metadata_mgr, None)]
    assert d.refreshed == ["browser", "stats"]


def test_why_a_worker_thread_cannot_use_the_main_connection(tmp_path):
    """The failure the old import hid: keep any new import off this path."""
    from modules.database_manager import DatabaseManager
    db = DatabaseManager(db_path=str(tmp_path / "t.db"), log_callback=lambda m: None)
    assert db.connect()
    db.cursor.execute("INSERT INTO translation_memories (name, tm_id) VALUES ('T', 't')")
    db.connection.commit()
    result = []
    t = threading.Thread(target=lambda: result.append(db.add_translation_unit(
        source="a", target="b", source_lang="nl", target_lang="en", tm_id="t")))
    t.start()
    t.join()
    assert result == [None]
    assert db.cursor.execute("SELECT COUNT(*) FROM translation_units").fetchone()[0] == 0
