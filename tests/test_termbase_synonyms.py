"""Source synonyms get TermLens chips of their own; the Glossaries tab lists synonyms (issue #114)."""

from __future__ import annotations

import os
import sqlite3
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.termbase_manager import TermbaseManager, synonym_chip_translation


def test_a_synonym_kept_as_is_translates_to_itself():
    # the issue's example: MEKO in the source, MEKO among the target synonyms
    assert synonym_chip_translation("MEKO", "methyl ethyl ketoxime", ["MEKO"]) == (
        "MEKO", ["methyl ethyl ketoxime"])
    assert synonym_chip_translation("meko", "methyl ethyl ketoxime", ["x", "MEKO"]) == (
        "MEKO", ["methyl ethyl ketoxime", "x"])


def test_otherwise_the_main_target_term_is_offered():
    assert synonym_chip_translation("een verdere uitvoering", "a further embodiment",
                                    ["another embodiment"]) == ("a further embodiment", ["another embodiment"])
    assert synonym_chip_translation("X", "Y", []) == ("Y", [])


def test_synonyms_for_a_page_of_terms_in_one_query():
    conn = sqlite3.connect(":memory:")
    conn.execute("""CREATE TABLE termbase_synonyms (id INTEGER PRIMARY KEY, term_id INTEGER,
                    synonym_text TEXT, language TEXT, display_order INTEGER DEFAULT 0,
                    forbidden INTEGER DEFAULT 0, created_date TEXT DEFAULT CURRENT_TIMESTAMP,
                    modified_date TEXT)""")
    conn.executemany("INSERT INTO termbase_synonyms (term_id, synonym_text, language, display_order) VALUES (?,?,?,?)",
                     [(1, "MEKO", "source", 0), (1, "MEKO", "target", 0), (1, "2-butanone oxime", "target", 1),
                      (2, "GC", "source", 0), (3, "unrelated", "source", 0)])
    manager = TermbaseManager.__new__(TermbaseManager)
    manager.db_manager = SimpleNamespace(cursor=conn.cursor())
    manager.log = lambda *a, **k: None
    assert manager.synonyms_for_terms([1, 2, 4]) == {
        1: {"source": ["MEKO"], "target": ["MEKO", "2-butanone oxime"]},
        2: {"source": ["GC"], "target": []}}
    assert manager.synonyms_for_terms([]) == {}


# ── the in-memory search: a chip per synonym the segment uses ────────────

import re
import threading

import pytest


@pytest.fixture(scope="module")
def S():
    from PyQt6.QtWidgets import QApplication
    S._app = QApplication.instance() or QApplication([])   # unreferenced, it is deleted at once
    saved = sys.stdout, sys.stderr
    import Supervertaler   # re-wraps stdout/stderr on Windows at import
    S._wrappers = sys.stdout, sys.stderr   # keep alive: closing them closes pytest's capture
    sys.stdout, sys.stderr = saved
    return Supervertaler


def _pattern(text):
    if any(c in text for c in '.%,/-'):
        return re.compile(r'(?<!\w)' + re.escape(text) + r'(?!\w)')
    return re.compile(r'\b' + re.escape(text) + r'\b')


def _entry(term_id, source, target, source_synonyms=(), target_synonyms=()):
    return {
        'term_id': term_id, 'source_term': source, 'source_term_lower': source.lower(),
        'target_term': target, 'termbase_id': 1, 'termbase_name': 'Chem', 'ranking': 1,
        'is_project_termbase': True, 'forbidden': False, 'domain': '', 'notes': '',
        'project': '', 'client': '', 'is_nontranslatable': False,
        'source_synonyms': list(source_synonyms), 'target_synonyms': list(target_synonyms),
        'abbreviation_variants': [], 'pattern': _pattern(source.lower()),
        'source_synonym_patterns': [(s, s.lower(), _pattern(s.lower())) for s in source_synonyms],
    }


def _search(S, index, text):
    class Bare(S.SupervertalerQt):
        def __getattr__(self, name):
            raise AttributeError(name)
    win = Bare.__new__(Bare)
    win.termbase_index_lock = threading.Lock()
    win.termbase_index = index
    return win._search_termbase_in_memory(text)


MEKO = ("methyl-ethylketoxime", "methyl ethyl ketoxime", ["MEKO"], ["MEKO", "2-butanone oxime"])


def test_a_synonym_next_to_the_main_term_gets_its_own_chip(S):
    found = _search(S, [_entry(7, *MEKO)], "Op basis van methyl-ethylketoxime (MEKO) en water.")
    chips = {(m['source'], m['translation']) for m in found.values()}
    assert chips == {("methyl-ethylketoxime", "methyl ethyl ketoxime"), ("MEKO", "MEKO")}
    syn = next(m for m in found.values() if m['source'] == "MEKO")
    assert syn['matched_via_synonym'] and syn['term_id'] == 7
    assert syn['target_synonyms'] == ["methyl ethyl ketoxime", "2-butanone oxime"]


def test_a_synonym_alone_takes_the_terms_own_key(S):
    found = _search(S, [_entry(7, *MEKO)], "Bevat MEKO.")
    assert list(found) == [7] and found[7]['source'] == "MEKO"


def test_a_synonym_inside_the_main_term_adds_no_second_chip(S):
    entry = _entry(7, "methyl-ethylketoxime", "methyl ethyl ketoxime", ["ethylketoxime"])
    found = _search(S, [entry], "Op basis van methyl-ethylketoxime.")
    assert [m['source'] for m in found.values()] == ["methyl-ethylketoxime"]
    # ... but where it stands on its own, it does
    found = _search(S, [entry], "Methyl-ethylketoxime, ook ethylketoxime genoemd.")
    assert sorted(m['source'] for m in found.values()) == ["ethylketoxime", "methyl-ethylketoxime"]


def test_a_synonym_that_contains_the_main_term_is_still_found(S):
    entry = _entry(3, "pomp", "pump", ["pompen"], ["pumps"])
    found = _search(S, [entry], "De pomp en de pompen.")
    assert sorted(m['source'] for m in found.values()) == ["pomp", "pompen"]


def test_no_synonym_in_the_segment_means_no_synonym_chip(S):
    found = _search(S, [_entry(7, *MEKO)], "Methyl-ethylketoxime is giftig.")
    assert [m['source'] for m in found.values()] == ["methyl-ethylketoxime"]
    assert _search(S, [_entry(7, *MEKO)], "Niets hier.") == {}


def test_a_failing_synonym_query_leaves_the_column_empty():
    class Broken:
        def execute(self, *a):
            raise sqlite3.OperationalError("database is locked")
    manager = TermbaseManager.__new__(TermbaseManager)
    manager.db_manager = SimpleNamespace(cursor=Broken())
    logged = []
    manager.log = logged.append
    assert manager.synonyms_for_terms([1, 2]) == {}
    assert logged and "locked" in logged[0]


def test_a_page_of_more_than_500_terms_is_read_in_chunks():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE termbase_synonyms (term_id INTEGER, synonym_text TEXT, language TEXT,"
                 " display_order INTEGER DEFAULT 0, created_date TEXT DEFAULT CURRENT_TIMESTAMP)")
    conn.executemany("INSERT INTO termbase_synonyms (term_id, synonym_text, language) VALUES (?,?,?)",
                     [(i, f"s{i}", "source") for i in range(1, 1201)])
    manager = TermbaseManager.__new__(TermbaseManager)
    manager.db_manager = SimpleNamespace(cursor=conn.cursor())
    manager.log = lambda *a, **k: None
    found = manager.synonyms_for_terms(range(1, 1201))
    assert len(found) == 1200 and found[1200] == {"source": ["s1200"], "target": []}
