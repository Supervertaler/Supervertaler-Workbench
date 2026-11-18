"""A failed termbase write ends the transaction it started (Nov 2026).

add_term got this on 4 Nov 2026. The other writes - adding a synonym, the
grid's inline edit, the forbidden flag, deleting a term or a termbase - still
left the transaction open when they failed (typically "database is locked"
while the Trados plugin writes). Until something else committed, Workbench's
main connection then read an old snapshot, so terms the plugin added stayed
invisible; and a delete of several statements could be left half done.
"""

from __future__ import annotations

import ast
import glob
import os
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import pytest

from modules.database_manager import DatabaseManager
from modules.termbase_manager import TermbaseManager, rollback_if_failed


@pytest.fixture
def db(tmp_path):
    manager = DatabaseManager(db_path=str(tmp_path / "sv.db"), log_callback=lambda *_: None)
    manager.connect()
    yield manager
    manager.close()


@pytest.fixture
def tbs(db):
    mgr = TermbaseManager(db, lambda *_: None)
    tb = mgr.create_termbase("T", source_lang="en", target_lang="nl")
    term = mgr.add_term(tb, "bend", "buiging")
    mgr.add_synonym(term, "kromming")
    syn = mgr.get_synonyms(term)[0]["id"]
    mgr.tb, mgr.term, mgr.syn = tb, term, syn
    return mgr


def count(db, sql, params=()):
    return db.connection.execute(sql, params).fetchone()[0]


WRITES = {
    "set_nontranslatable":      lambda m: m.set_nontranslatable(m.term, True),
    "update_term":              lambda m: m.update_term(m.term, target_term="bocht"),
    "delete_term":              lambda m: m.delete_term(m.term),
    "add_synonym":              lambda m: m.add_synonym(m.term, "bocht"),
    "update_synonym_order":     lambda m: m.update_synonym_order(m.syn, 3),
    "update_synonym_forbidden": lambda m: m.update_synonym_forbidden(m.syn, True),
    "delete_synonym":           lambda m: m.delete_synonym(m.syn),
}


@pytest.mark.parametrize("write", sorted(WRITES))
def test_a_write_that_fails_on_a_lock_leaves_no_transaction_open(db, tbs, write):
    db.connection.execute("PRAGMA busy_timeout = 200")
    trados = sqlite3.connect(db.db_path, timeout=1)
    trados.execute("BEGIN IMMEDIATE")                       # the plugin is writing
    assert WRITES[write](tbs) is False                       # locked: reported, no crash
    trados.rollback()
    assert not db.connection.in_transaction

    # And Workbench sees what the plugin writes next.
    trados.execute("INSERT INTO termbase_terms (termbase_id, source_term, target_term) "
                   "VALUES (?, 'from Trados', 'van Trados')", (str(tbs.tb),))
    trados.commit()
    trados.close()
    assert count(db, "SELECT COUNT(*) FROM termbase_terms WHERE source_term = 'from Trados'") == 1


@pytest.mark.parametrize("write", sorted(WRITES))
def test_each_write_still_works_when_nothing_is_locked(tbs, write):
    assert WRITES[write](tbs) is True


def test_a_write_of_several_statements_is_not_left_half_done(db, tbs):
    # Deleting a termbase is three statements; if the last one fails, the
    # first two must not stay pending (and be committed by whatever writes next).
    con = db.connection
    con.execute("CREATE TEMP TRIGGER refuse BEFORE DELETE ON termbases "
                "BEGIN SELECT RAISE(ABORT, 'refused'); END")
    with pytest.raises(sqlite3.DatabaseError):
        with rollback_if_failed(con):
            con.execute("DELETE FROM termbase_terms WHERE termbase_id = ?", (tbs.tb,))
            con.execute("DELETE FROM termbase_activation WHERE termbase_id = ?", (tbs.tb,))
            con.execute("DELETE FROM termbases WHERE id = ?", (tbs.tb,))
            con.commit()
    assert not con.in_transaction
    assert count(db, "SELECT COUNT(*) FROM termbase_terms WHERE termbase_id = ?", (tbs.tb,)) == 1


def test_someone_elses_open_transaction_is_left_alone(db, tbs):
    con = db.connection
    con.execute("INSERT INTO termbase_terms (termbase_id, source_term, target_term) "
                "VALUES (?, 'pending', 'pending')", (str(tbs.tb),))      # not committed yet
    assert con.in_transaction
    with pytest.raises(sqlite3.DatabaseError):
        with rollback_if_failed(con):
            con.execute("INSERT INTO no_such_table VALUES (1)")
    assert con.in_transaction                                           # still theirs
    con.commit()
    assert count(db, "SELECT COUNT(*) FROM termbase_terms WHERE source_term = 'pending'") == 1


def test_every_termbase_write_in_the_main_window_is_wrapped():
    # The main window also writes to the termbase tables directly. Each such
    # statement must sit inside `with rollback_if_failed(...)`.
    writes = ("UPDATE termbase_", "DELETE FROM termbase", "INSERT INTO termbase_")
    unwrapped = []
    for path in glob.glob(os.path.join(ROOT, "modules", "main_window", "*.py")):
        tree = ast.parse(open(path, encoding="utf-8").read())
        wrapped = set()
        for w in ast.walk(tree):
            if isinstance(w, ast.With) and "rollback_if_failed" in ast.unparse(w.items[0]):
                wrapped |= {id(n) for n in ast.walk(w)}
            # A write that runs its own transaction and rolls it back itself
            # (reversing term direction) is fine as it is.
            if isinstance(w, ast.Try) and any("rollback()" in ast.unparse(h) for h in w.handlers):
                wrapped |= {id(n) for n in ast.walk(w)}
        for call in ast.walk(tree):
            if (isinstance(call, ast.Call) and getattr(call.func, "attr", "") == "execute"
                    and call.args and isinstance(call.args[0], ast.Constant)
                    and isinstance(call.args[0].value, str)
                    and any(k in call.args[0].value for k in writes)
                    and id(call) not in wrapped):
                unwrapped.append(f"{os.path.basename(path)}:{call.lineno}")
    assert unwrapped == []
