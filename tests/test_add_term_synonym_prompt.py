"""Termbases tab → Terms window → "+ Add Term": "Similar term found" (Nov 2026).

Adding from the Editor asks whether a term with the same source (or target)
as an existing entry should become a synonym of that entry. The Terms
window's own "+ Add Term" skipped the question and always made a new entry.
It now asks too, in the termbase's own direction (its dialog is labelled with
the termbase's languages, so nothing is turned round to match a project).

Also: when the user chose "synonym" but the synonym could not be saved (the
database locked by the Trados plugin, say), every add path still said
"merged", and the term silently went nowhere. That is now reported.

These build the real Termbases tab on a stand-in window with a scratch
database; the two dialogs are answered by the test.
"""

from __future__ import annotations

import os
import sqlite3
import sys
import traceback
from pathlib import Path

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtWidgets import QApplication, QDialog, QMainWindow, QMessageBox, QPushButton

from modules import merge_prompt_dialog, termbase_entry_editor
from modules.database_manager import DatabaseManager

# Supervertaler.py wraps the console streams in UTF-8 when imported on Windows.
_streams = sys.stdout, sys.stderr
SV = pytest.importorskip("Supervertaler")
_wrapped = sys.stdout, sys.stderr
sys.stdout, sys.stderr = _streams
Window = SV.SupervertalerQt


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


class Answers:
    """What the test's stand-ins for the two dialogs answer."""
    source = target = ""
    source_synonyms: list = []
    target_synonyms: list = []
    merge_choice = merge_prompt_dialog.MergePromptDialog.SYNONYM
    merge_prompts: list = []
    warnings: list = []


class FakeEntryEditor:
    """The Add Term dialog, already filled in and accepted."""

    def __init__(self, *a, **k):
        pass

    def exec(self):
        return QDialog.DialogCode.Accepted

    def get_metadata(self):
        return {k: (False if k in ("forbidden", "is_nontranslatable") else "")
                for k in ("domain", "notes", "project", "client", "forbidden", "is_nontranslatable",
                          "definition", "url", "source_abbreviation", "target_abbreviation")}

    def get_source_term(self):
        return Answers.source

    def get_target_term(self):
        return Answers.target

    def get_source_synonyms(self):
        return Answers.source_synonyms

    def get_target_synonyms(self):
        return Answers.target_synonyms


class FakeMergePrompt:
    CANCEL, SYNONYM, EDIT, KEEP_BOTH = "cancel", "synonym", "edit", "keep_both"

    def __init__(self, parent, matches, source, target):
        Answers.merge_prompts.append([(m["source_term"], m["target_term"], m["match_type"]) for m in matches])
        self.choice = None

    def exec(self):
        self.choice = Answers.merge_choice


@pytest.fixture
def tab(app, tmp_path, monkeypatch):
    Answers.source_synonyms, Answers.target_synonyms = [], []
    Answers.merge_choice = FakeMergePrompt.SYNONYM
    Answers.merge_prompts, Answers.warnings = [], []
    monkeypatch.setattr(termbase_entry_editor, "TermbaseEntryEditor", FakeEntryEditor)
    monkeypatch.setattr(merge_prompt_dialog, "MergePromptDialog", FakeMergePrompt)
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: Answers.warnings.append(a[2])))
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: None))
    errors = []
    monkeypatch.setattr(sys, "excepthook", lambda t, v, tb: errors.append("".join(traceback.format_exception(t, v, tb))))

    db = DatabaseManager(db_path=str(tmp_path / "sv.db"), log_callback=lambda *_: None)
    db.connect()
    w = Window.__new__(Window)
    QMainWindow.__init__(w)
    w.user_data_path = tmp_path
    w.db_manager = db
    w.current_project = None
    w.logged = []
    w.log = w.logged.append
    w.force_refresh_matches = lambda: None
    w.termbases_tab = w.create_termbases_tab()
    mgr = w.termbase_mgr
    tb = mgr.create_termbase("BEIJER", source_lang="nl", target_lang="en")
    mgr.bend = mgr.add_term(tb, "buiging", "bend")
    w._open_termbase_terms(tb, "BEIJER")
    w.tb, w.mgr = tb, mgr
    w.add_button = next(b for b in w.findChildren(QPushButton) if b.text() == "+ Add Term")
    yield w
    db.close()
    w.deleteLater()
    assert errors == [], errors[0]


def add(w, source, target):
    Answers.source, Answers.target = source, target
    w.add_button.click()


def entries(w):
    return sorted((t["source_term"], t["target_term"]) for t in w.mgr.get_terms(w.tb))


def synonyms(w, term_id, language):
    return [s["synonym_text"] for s in w.mgr.get_synonyms(term_id, language=language)]


def test_same_source_other_target_is_offered_and_added_as_a_target_synonym(tab):
    add(tab, "buiging", "curve")
    assert Answers.merge_prompts == [[("buiging", "bend", "source")]]
    assert entries(tab) == [("buiging", "bend")]                     # no second entry
    assert synonyms(tab, tab.mgr.bend, "target") == ["curve"]


def test_same_target_other_source_becomes_a_source_synonym(tab):
    add(tab, "kromming", "bend")
    assert entries(tab) == [("buiging", "bend")]
    assert synonyms(tab, tab.mgr.bend, "source") == ["kromming"]


def test_the_terms_are_not_turned_round_to_match_a_project(tab):
    # An EN→NL project open while adding to this NL→EN termbase: the dialog
    # is in the termbase's direction, so "buiging" is still the source.
    class Project:
        source_lang, target_lang = "en", "nl"
    tab.current_project = Project()
    add(tab, "buiging", "curve")
    assert synonyms(tab, tab.mgr.bend, "target") == ["curve"]
    assert entries(tab) == [("buiging", "bend")]


def test_synonyms_typed_in_the_dialog_go_with_it(tab):
    Answers.target_synonyms = [{"text": "arc", "order": 0, "forbidden": False}]
    add(tab, "buiging", "curve")
    assert sorted(synonyms(tab, tab.mgr.bend, "target")) == ["arc", "curve"]


def test_keep_both_makes_a_new_entry(tab):
    Answers.merge_choice = FakeMergePrompt.KEEP_BOTH
    add(tab, "buiging", "curve")
    assert entries(tab) == [("buiging", "bend"), ("buiging", "curve")]
    assert synonyms(tab, tab.mgr.bend, "target") == []


def test_cancel_adds_nothing(tab):
    Answers.merge_choice = FakeMergePrompt.CANCEL
    add(tab, "buiging", "curve")
    assert entries(tab) == [("buiging", "bend")]
    assert synonyms(tab, tab.mgr.bend, "target") == []


def test_an_unrelated_term_is_added_without_a_question(tab):
    add(tab, "klep", "valve")
    assert Answers.merge_prompts == []
    assert entries(tab) == [("buiging", "bend"), ("klep", "valve")]


def test_an_exact_duplicate_is_still_refused_without_a_question(tab):
    add(tab, "buiging", "bend")
    assert Answers.merge_prompts == []
    assert entries(tab) == [("buiging", "bend")]


def test_a_synonym_that_cannot_be_saved_is_reported_and_nothing_is_added(tab):
    # The Trados plugin holds the write lock while the user answers "synonym".
    tab.db_manager.connection.execute("PRAGMA busy_timeout = 200")
    trados = sqlite3.connect(tab.db_manager.db_path, timeout=1)
    trados.execute("BEGIN IMMEDIATE")
    try:
        add(tab, "buiging", "curve")
    finally:
        trados.rollback()
        trados.close()
    assert len(Answers.warnings) == 1 and "synonym" in Answers.warnings[0]
    assert entries(tab) == [("buiging", "bend")]
    assert synonyms(tab, tab.mgr.bend, "target") == []
    assert not tab.db_manager.connection.in_transaction


def test_the_detector_says_failed_when_no_synonym_was_saved(tab, monkeypatch):
    # The Editor's paths share the detector; they now get 'failed', not 'merged'.
    tb = {"id": tab.tb, "name": "BEIJER", "source_lang": "nl", "target_lang": "en"}
    with monkeypatch.context() as m:
        m.setattr(tab.mgr, "add_synonym", lambda *a, **k: False)
        assert tab._detect_and_merge_synonyms("buiging", "curve", [tb]) == ("failed", set())
    assert tab._detect_and_merge_synonyms("buiging", "curve", [tb]) == ("merged", {tab.tb})
