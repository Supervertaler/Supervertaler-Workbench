"""Fragment matches from the TM (issue #193).

The TM holds a whole sentence and the document has it in pieces (or the
reverse): no fuzzy match, but the TM entry is still what the translator needs.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules import tm_fragments as frag
from modules.database_manager import DatabaseManager


def test_words_ignore_case_tags_and_punctuation():
    assert frag.words("Press <b>Start</b>, then OK!") == ["press", "start", "then", "ok"]


def test_classify():
    tm = frag.words("Which heading do you want to read?")
    assert frag.classify(frag.words("Which heading"), tm) == frag.IN_TM
    assert frag.classify(frag.words("do you want to read?"), tm) == frag.IN_TM
    assert frag.classify(frag.words("heading do"), tm) == frag.IN_TM
    assert frag.classify(frag.words("Which do"), tm) is None           # not an unbroken run
    assert frag.classify(frag.words("Heading"), tm) is None            # one word: glossary territory
    assert frag.classify(tm, tm) is None                               # same length: a fuzzy/exact match
    seg = frag.words("Close the valve. Then open the tap.")
    assert frag.classify(seg, frag.words("Close the valve.")) == frag.IN_SEGMENT
    assert frag.classify(seg, frag.words("open the tap")) == frag.IN_SEGMENT
    assert frag.classify(seg, frag.words("Close the tap")) is None


def test_pick_orders_by_coverage_and_skips_excluded():
    rows = [
        {"source_text": "Which heading do you want to read today, please?", "target_text": "A"},
        {"source_text": "Which heading do you want to read?", "target_text": "B"},
        {"source_text": "Which heading do you want to read?", "target_text": "dup"},
        {"source_text": "Something else entirely", "target_text": "C"},
        {"source_text": "Which heading is this?", "target_text": "D"},
    ]
    out = frag.pick("Which heading", rows)
    assert [m["target_text"] for m in out] == ["D", "B", "A"]
    assert out[0]["fragment"] == frag.IN_TM and out[0]["match_pct"] == 50
    assert frag.pick("Which heading", rows, exclude_sources={"Which heading is this?"})[0]["target_text"] == "B"
    assert frag.pick("Heading", rows) == []


@pytest.fixture
def db(tmp_path):
    d = DatabaseManager(db_path=str(tmp_path / "t.db"), log_callback=lambda *a, **k: None)
    d.connect()
    d.cursor.execute("INSERT INTO translation_memories (id, name, tm_id, source_lang, target_lang) "
                     "VALUES (1, 'Game', 'game', 'en', 'pl')")
    d.connection.commit()
    d.add_translation_units_batch(
        [("Which heading do you want to read?", "Który nagłówek chcesz przeczytać?"),
         ("Close the valve.", "Zamknij zawór."),
         ("Read the manual before you start.", "Przeczytaj instrukcję, zanim zaczniesz."),
         ("heading", "nagłówek")],
        "en", "pl", tm_id="game")
    yield d
    d.close()


def test_segment_that_is_part_of_a_tm_sentence(db):
    for segment in ("Which heading", "do you want to read?"):
        matches = db.search_fragment_matches(segment, tm_ids=["game"], source_lang="en", target_lang="pl")
        assert [(m["target_text"], m["fragment"]) for m in matches] == [
            ("Który nagłówek chcesz przeczytać?", frag.IN_TM)]


def test_tm_sentence_that_is_part_of_the_segment(db):
    matches = db.search_fragment_matches("Close the valve. Then read the manual before you start.",
                                         tm_ids=["game"], source_lang="en", target_lang="pl")
    assert [m["target_text"] for m in matches] == [
        "Przeczytaj instrukcję, zanim zaczniesz.", "Zamknij zawór."]
    assert {m["fragment"] for m in matches} == {frag.IN_SEGMENT}


def test_language_pair_tm_filter_and_exclusions(db):
    assert db.search_fragment_matches("Which heading", tm_ids=["game"], source_lang="en",
                                      target_lang="de") == []
    assert db.search_fragment_matches("Which heading", tm_ids=["other"], source_lang="en",
                                      target_lang="pl") == []
    assert db.search_fragment_matches(
        "Which heading", tm_ids=["game"], source_lang="en", target_lang="pl",
        exclude_sources={"Which heading do you want to read?"}) == []
    assert db.search_fragment_matches("heading", tm_ids=["game"]) == []
