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
