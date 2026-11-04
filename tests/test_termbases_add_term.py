"""Termbases tab: the terms open in a window of their own, and "+ Add Term"
opens the Add Term dialog.

Up to 1.10.375 "+ Add Term" saved an EMPTY term at once (sorted to the top of
page 1, out of sight) and put the cursor on the page's last row, an existing
term, which the user then typed over. The Terms panel sat beside the termbase
list, split by a divider that could hardly move and, dragged past that, snapped
a whole panel shut; the English/Dutch columns were squeezed to ~40 px. Now the
tab is the termbase list only, and right-click → Edit Terms… opens the terms.

These tests build the real Termbases tab (Supervertaler.py's
create_termbases_tab) against a temporary database with a 12,000-term
termbase, with the dialog filled in by the test instead of a person. Importing
Supervertaler.py takes about 20 s.
"""

from __future__ import annotations

import os
import sqlite3
import sys
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtWidgets import QApplication, QMainWindow, QMessageBox, QPushButton, QSplitter

from modules.database_manager import DatabaseManager
from modules.termbase_entry_editor import TermbaseEntryEditor
from modules.termbase_manager import TermbaseManager

BIG = 12_000


@pytest.fixture(scope="module")
def S():
    # Keep a reference: an unreferenced QApplication is freed at once, and the
    # first QWidget then aborts the whole test run.
    S._app = QApplication.instance() or QApplication([])
    saved = sys.stdout, sys.stderr
    import Supervertaler   # re-wraps stdout/stderr on Windows at import
    S._wrappers = sys.stdout, sys.stderr   # keep alive: closing them closes pytest's capture
    sys.stdout, sys.stderr = saved
    return Supervertaler


@pytest.fixture
def db(tmp_path):
    manager = DatabaseManager(db_path=str(tmp_path / "sv.db"), log_callback=lambda *_: None)
    manager.connect()
    yield manager
    manager.close()


@pytest.fixture
def tab(S, db):
    """The Termbases tab, with a 12,000-term en→nl termbase open in the Terms window."""
    tbs = TermbaseManager(db, lambda *_: None)
    big = tbs.create_termbase("BIG (en-nl)", source_lang="en", target_lang="nl")
    db.connection.executemany(
        "INSERT INTO termbase_terms (termbase_id, source_term, target_term) VALUES (?, ?, ?)",
        [(str(big), f"a term {i:05d}", f"een term {i:05d}") for i in range(BIG)])
    db.connection.commit()
    tbs.create_termbase("Small (nl-en)", source_lang="nl", target_lang="en")

    host = S.SupervertalerQt.__new__(S.SupervertalerQt)
    QMainWindow.__init__(host)
    host.db_manager = db
    host.logged = []
    host.log = host.logged.append
    host.termbase_cache_lock = threading.Lock()
    host.termbase_cache = {}
    host.current_project = None
    host.force_refresh_matches = lambda: None   # needs the Editor; not under test here
    widget = host.create_termbases_tab()
    host.setCentralWidget(widget)
    host.resize(2000, 1050)
    host.show()
    QApplication.processEvents()

    host._open_termbase_terms(big, "BIG (en-nl)")   # what right-click → Edit Terms… does
    QApplication.processEvents()

    t = type("Tab", (), {})()
    t.host, t.widget, t.db, t.tb_id = host, widget, db, big
    t.window = host.termbase_terms_window
    t.terms = host.termbase_terms_table
    t.add_button = next(b for b in t.window.findChildren(QPushButton) if b.text() == "+ Add Term")
    yield t
    t.window.hide()
    host.hide()   # not close(): the real closeEvent shuts the whole app down


@pytest.fixture
def messages(S, monkeypatch):
    """Record message boxes instead of showing them."""
    seen = []
    for name in ("information", "warning", "critical"):
        def box(_parent, title, text, *a, _kind=name, **k):
            seen.append((_kind, title, text))
            return QMessageBox.StandardButton.Ok
        monkeypatch.setattr(QMessageBox, name, box)
    return seen


def fill_dialog(monkeypatch, **fields):
    """Make the dialog 'exec' by filling it in and pressing Save (or Cancel)."""
    opened = []

    def fake_exec(dialog):
        opened.append(dialog)
        if fields.get("cancel"):
            return 0
        dialog.source_edit.setText(fields.get("source", ""))
        dialog.target_edit.setText(fields.get("target", ""))
        for name in ("domain", "url", "client", "project"):
            if name in fields:
                getattr(dialog, f"{name}_edit").setText(fields[name])
        if "notes" in fields:
            dialog.notes_edit.setPlainText(fields["notes"])
        dialog.forbidden_check.setChecked(fields.get("forbidden", False))
        dialog.nontranslatable_check.setChecked(fields.get("nontranslatable", False))
        for text in fields.get("source_synonyms", []):
            dialog.source_synonym_edit.setText(text)
            dialog.add_source_synonym()
        for text in fields.get("target_synonyms", []):
            dialog.target_synonym_edit.setText(text)
            dialog.add_target_synonym()
        dialog._accept_and_save()
        return dialog.result()

    monkeypatch.setattr(TermbaseEntryEditor, "exec", fake_exec)
    return opened


def count(db, where="1=1", params=()):
    return db.connection.execute(
        f"SELECT COUNT(*) FROM termbase_terms WHERE {where}", params).fetchone()[0]


def selected_source(t):
    rows = t.terms.selectionModel().selectedRows()
    return t.terms.item(rows[0].row(), 0).text() if rows else None


# ── the dialog ───────────────────────────────────────────────────────────

def test_add_term_saves_every_field_and_shows_the_new_term_selected(tab, monkeypatch, messages):
    opened = fill_dialog(
        monkeypatch, source="gebogen", target="curved", domain="Mechanics",
        notes="test note", project="P1", client="C1", forbidden=True,
        source_synonyms=["krom"], target_synonyms=["bent"])
    tab.add_button.click()

    dialog = opened[0]
    assert dialog.windowTitle() == "Add Term to Termbase — BIG (en-nl)"
    assert dialog._src_caption == "English" and dialog._tgt_caption == "Dutch"   # the termbase's own columns
    assert dialog._src_caption_label.text() == "<b>English (source):</b>"
    assert dialog._tgt_caption_label.text() == "<b>Dutch (target):</b>"
    assert dialog.parent() is tab.window                    # opens over the Terms window
    assert messages == []

    row = tab.db.connection.execute(
        "SELECT id, source_term, target_term, domain, notes, project, client, forbidden, "
        "source_lang, target_lang FROM termbase_terms WHERE source_term = 'gebogen'").fetchone()
    term_id = row[0]
    assert tuple(row[1:]) == ("gebogen", "curved", "Mechanics", "test note", "P1", "C1", 1, "en", "nl")
    synonyms = tab.db.connection.execute(
        "SELECT synonym_text, language FROM termbase_synonyms WHERE term_id = ?", (term_id,)).fetchall()
    assert sorted(tuple(s) for s in synonyms) == [("bent", "target"), ("krom", "source")]
    assert count(tab.db, "TRIM(source_term) = ''") == 0          # no empty term any more
    assert count(tab.db) == BIG + 1

    # Kept the A→Z sort and jumped to the page that holds it ("gebogen" sorts after
    # all 12,000 "a term …" rows: page 121 of 121), with that row selected.
    assert selected_source(tab) == "gebogen"
    assert t_page_label(tab) == "Page 121 of 121"


def t_page_label(t):
    from PyQt6.QtWidgets import QLabel
    return next(l.text() for l in t.window.findChildren(QLabel) if l.text().startswith("Page "))


def test_a_term_in_the_middle_of_the_list_opens_on_its_own_page(tab, monkeypatch, messages):
    fill_dialog(monkeypatch, source="a term 05000b", target="een term 05000b")
    tab.add_button.click()
    assert selected_source(tab) == "a term 05000b"
    assert t_page_label(tab) == "Page 51 of 121"    # rows 5000–5099 on page 51


def test_cancel_adds_nothing(tab, monkeypatch, messages):
    fill_dialog(monkeypatch, cancel=True)
    tab.add_button.click()
    assert count(tab.db) == BIG
    assert messages == []


@pytest.mark.parametrize("source,target,missing", [("", "curved", "English"), ("gebogen", "", "Dutch"),
                                                    ("   ", "curved", "English")])
def test_an_empty_term_keeps_the_dialog_open_and_adds_nothing(tab, monkeypatch, messages,
                                                              source, target, missing):
    opened = fill_dialog(monkeypatch, source=source, target=target)
    tab.add_button.click()
    assert opened[0].result() == 0                       # not accepted: still open for the user
    assert messages == [("warning", "Term missing", f"Please type the {missing} term.")]
    assert count(tab.db) == BIG


def test_a_non_translatable_with_no_target_copies_the_source(tab, monkeypatch, messages):
    fill_dialog(monkeypatch, source="Supervertaler", target="", nontranslatable=True)
    tab.add_button.click()
    row = tab.db.connection.execute(
        "SELECT target_term, is_nontranslatable FROM termbase_terms WHERE source_term = 'Supervertaler'").fetchone()
    assert tuple(row) == ("Supervertaler", 1)


def test_a_duplicate_is_not_added_and_the_existing_term_is_selected(tab, monkeypatch, messages):
    # Different case and a trailing full stop: add_term would store "A Term 00042"
    # → "een term 00042", which it treats as the existing entry.
    fill_dialog(monkeypatch, source="A Term 00042.", target="EEN TERM 00042")
    tab.add_button.click()
    assert count(tab.db) == BIG
    assert [m[:2] for m in messages] == [("information", "Already in the termbase")]
    assert selected_source(tab) == "a term 00042"


def test_a_filter_that_hides_the_new_term_is_cleared(tab, monkeypatch, messages):
    filter_box = tab.host.terms_search_box
    filter_box.setText("00042")
    QApplication.processEvents()
    fill_dialog(monkeypatch, source="gebogen", target="curved")
    tab.add_button.click()
    assert filter_box.text() == ""
    assert selected_source(tab) == "gebogen"


def test_a_read_only_database_gives_a_clear_message_and_no_crash(tab, monkeypatch, messages):
    db = tab.db
    read_only = sqlite3.connect(f"file:{db.db_path}?mode=ro", uri=True)
    writable = db.connection, db.cursor
    db.connection, db.cursor = read_only, read_only.cursor()
    try:
        fill_dialog(monkeypatch, source="gebogen", target="curved")
        tab.add_button.click()
    finally:
        db.connection, db.cursor = writable
        read_only.close()
    assert [m[:2] for m in messages] == [("warning", "Term not added")]
    assert "read-only" in messages[0][2]
    assert count(db, "source_term = 'gebogen'") == 0
    assert any("readonly" in line for line in tab.host.logged)    # the real error is in the log


def test_a_locked_database_gives_a_clear_message_and_no_crash(tab, monkeypatch, messages):
    db = tab.db
    db.connection.execute("PRAGMA busy_timeout = 200")   # don't wait the full 15 s
    other = sqlite3.connect(db.db_path, timeout=1)       # e.g. the Trados plugin mid-write
    other.execute("BEGIN EXCLUSIVE")
    try:
        fill_dialog(monkeypatch, source="gebogen", target="curved")
        tab.add_button.click()
    finally:
        other.rollback()
        other.close()
    assert [m[:2] for m in messages] == [("warning", "Term not added")]
    assert "locked" in messages[0][2]
    assert count(db, "source_term = 'gebogen'") == 0
    # The next try, once the lock is gone, works.
    messages.clear()
    tab.add_button.click()
    assert messages == [] and count(db, "source_term = 'gebogen'") == 1


# ── the list, and the Terms window ───────────────────────────────────────

def _row_of(table, name):
    return next(r for r in range(table.rowCount()) if table.item(r, 1).text() == name)


def test_the_tab_is_the_termbase_list_only(tab):
    assert tab.widget.findChild(QSplitter) is None
    assert tab.window.isWindow() and tab.window.parent() is tab.host   # closes with Workbench
    assert tab.window not in tab.widget.findChildren(type(tab.window))


def test_selecting_a_termbase_only_selects_it(tab):
    tab.window.hide()
    table = tab.host.termbase_table
    table.selectRow(_row_of(table, "Small (nl-en)"))
    QApplication.processEvents()
    assert not tab.window.isVisible()


def test_right_click_edit_terms_opens_that_termbases_terms(tab, S, monkeypatch):
    tab.window.hide()
    chosen = []

    def choose_default(menu, *_args):          # "click" the bold first entry
        chosen.append(menu.defaultAction().text())
        menu.defaultAction().trigger()
    monkeypatch.setattr(S.QMenu, "exec", choose_default)

    table = tab.host.termbase_table
    row = _row_of(table, "Small (nl-en)")
    pos = table.visualItemRect(table.item(row, 1)).center()
    tab.host._show_termbase_context_menu(pos, table, tab.host.termbase_mgr,
                                         tab.host.termbase_tab_refresh_callback)
    QApplication.processEvents()
    assert chosen == ["📝 Edit Terms…"]
    assert tab.window.isVisible()
    assert tab.window.windowTitle() == "Terms – Small (nl-en)"
    assert tab.terms.horizontalHeaderItem(0).text() == "Dutch"      # that termbase's own direction
    assert tab.terms.rowCount() == 0 and tab.add_button.isEnabled()


def test_the_window_shows_the_first_page_of_terms(tab):
    assert tab.window.isVisible()
    assert tab.window.windowTitle() == "Terms – BIG (en-nl)"
    assert tab.terms.rowCount() == 100
    assert tab.terms.item(0, 0).text() == "a term 00000"
    assert t_page_label(tab) == "Page 1 of 120"


def test_renaming_the_shown_termbase_renames_the_window_and_deleting_it_closes_it(tab):
    mgr = tab.host.termbase_mgr
    mgr.rename_termbase(tab.tb_id, "BIG renamed")
    tab.host.termbase_tab_refresh_callback()
    assert tab.window.windowTitle() == "Terms – BIG renamed"

    mgr.delete_termbase(tab.tb_id)
    tab.host.termbase_tab_refresh_callback()
    assert not tab.window.isVisible()


def test_the_term_columns_stay_readable_and_take_the_spare_width(tab):
    header = tab.terms.horizontalHeader()
    for width in (500, 2000):                     # a narrow window, then a wide one
        tab.window.resize(width, 800)
        QApplication.processEvents()
        assert header.sectionSize(0) >= 160 and header.sectionSize(1) >= 160
    # When wide, the spare width goes to the two term columns.
    others = sum(header.sectionSize(c) for c in range(2, header.count()))
    assert header.sectionSize(0) + header.sectionSize(1) >= tab.terms.viewport().width() - others - 2


# ── a failed save must not leave the connection stuck ───────────────────

def test_a_failed_add_does_not_leave_workbench_blind_to_other_programs(db):
    """add_term failing on a lock used to leave its transaction open, so this
    connection kept reading an old snapshot: terms the Trados plugin added
    afterwards stayed invisible until something else happened to commit."""
    tbs = TermbaseManager(db, lambda *_: None)
    tb = tbs.create_termbase("T", source_lang="en", target_lang="nl")
    db.connection.execute("PRAGMA busy_timeout = 200")
    trados = sqlite3.connect(db.db_path, timeout=1)
    trados.execute("BEGIN IMMEDIATE")
    assert tbs.add_term(tb, "gebogen", "curved") is None          # locked
    trados.rollback()
    assert not db.connection.in_transaction

    trados.execute("INSERT INTO termbase_terms (termbase_id, source_term, target_term) "
                   "VALUES (?, 'from Trados', 'van Trados')", (str(tb),))
    trados.commit()
    trados.close()
    assert count(db, "source_term = 'from Trados'") == 1


def test_a_failed_add_leaves_someone_elses_open_transaction_alone(db):
    tbs = TermbaseManager(db, lambda *_: None)
    tb = tbs.create_termbase("T", source_lang="en", target_lang="nl")
    db.connection.execute("INSERT INTO termbase_terms (termbase_id, source_term, target_term) "
                          "VALUES (?, 'pending', 'pending')", (str(tb),))   # not committed yet
    assert db.connection.in_transaction
    db.connection.execute("CREATE TEMP TRIGGER refuse BEFORE INSERT ON termbase_terms "
                          "WHEN NEW.source_term = 'gebogen' BEGIN SELECT RAISE(ABORT, 'refused'); END")
    assert tbs.add_term(tb, "gebogen", "curved") is None
    assert db.connection.in_transaction                           # not rolled back
    db.connection.commit()
    assert count(db, "source_term = 'pending'") == 1


def test_find_duplicate_term_matches_add_terms_own_rule(db):
    tbs = TermbaseManager(db, lambda *_: None)
    tb = tbs.create_termbase("T", source_lang="en", target_lang="nl")
    term_id = tbs.add_term(tb, "Pump housing.", "pomphuis")      # stored as "Pump housing"
    assert tbs.find_duplicate_term(tb, "pump HOUSING", "POMPHUIS") == term_id
    assert tbs.find_duplicate_term(tb, "pump housing", "pomp") is None
    assert tbs.add_term(tb, "pump housing", "Pomphuis") is None  # add_term agrees
