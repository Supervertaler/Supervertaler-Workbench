"""TermLens keeps its terms on a segment with a 100% TM match (Nov 2026).

Landing on a segment looks up its exact TM match at once and notes it in the
match cache. For a segment not in that cache yet, the note was a new entry
with an empty term list. The move's own handler then found the entry, took
the empty list for "no terms here" and TermLens showed "No matches", though
the termbases had terms for the segment; only F5 brought them back. The new
entry now carries the segment's terms. Nothing here reads a real TM or
termbase: both are stand-ins.
"""

from __future__ import annotations

import os
import sys
import threading
from types import SimpleNamespace

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import pytest

from modules.main_window.match_prefetch import MatchPrefetchMixin
from modules.main_window.translation_memories import TranslationMemoriesMixin

SOURCE = "In een uitvoeringsvorm omvat het bovenwerk een schacht."
TERMS = {
    11: {"source": "uitvoeringsvorm", "translation": "embodiment", "term_id": 11, "termbase_id": 7,
         "termbase_name": "Patents", "ranking": 1},
    12: {"source": "bovenwerk", "translation": "upper", "term_id": 12, "termbase_id": 7,
         "termbase_name": "Patents", "ranking": 1},
}


class Window(TranslationMemoriesMixin, MatchPrefetchMixin):
    """Only what the instant TM lookup touches."""

    def __init__(self, exact, terms):
        self.enable_tm_matching = True
        self.enable_mt_matching = self.enable_llm_matching = False
        self.hide_outer_wrapping_tags = False
        self.current_project = SimpleNamespace(source_lang="Dutch", target_lang="English",
                                               tm_settings={"activated_tm_ids": ["tm1"]})
        self.tm_database = SimpleNamespace(tm_metadata={"tm1": {"name": "Client TM"}},
                                           source_lang="nl", target_lang="en")
        self.db_manager = SimpleNamespace(get_exact_match=lambda **kw: exact)
        self.translation_matches_cache, self.translation_matches_cache_lock = {}, threading.Lock()
        self.termbase_cache, self.termbase_cache_lock = {}, threading.Lock()
        self.shown, self.searches, self._terms = [], 0, terms

    def set_compare_panel_matches(self, *args):
        self.shown.append(args)

    def _convert_language_to_code(self, name):
        return name[:2].lower()

    def _search_termbase_in_memory(self, source):
        self.searches += 1
        if isinstance(self._terms, Exception):
            raise self._terms
        return dict(self._terms)

    def _deduplicate_termbase_matches(self, raw):
        return raw


EXACT = {"source_text": SOURCE, "target_text": "In an embodiment, the upper comprises a shaft.",
         "tm_id": "tm1"}
SEGMENT = SimpleNamespace(id=62, source=SOURCE)


def test_a_new_entry_carries_the_segments_terms():
    win = Window(EXACT, TERMS)
    assert win._show_instant_tm_match(SEGMENT) is True
    entry = win.translation_matches_cache[62]
    assert [m.target for m in entry["TM"]] == [EXACT["target_text"]]
    assert sorted(m.source for m in entry["Termbases"]) == ["bovenwerk", "uitvoeringsvorm"]
    assert {m.metadata["term_id"] for m in entry["Termbases"]} == {11, 12}
    assert len(win.shown) == 1                         # the TM match is still shown at once


def test_terms_already_looked_up_are_not_looked_up_again():
    win = Window(EXACT, TERMS)
    win.termbase_cache[62] = {12: TERMS[12]}           # the batch worker got there first
    win._show_instant_tm_match(SEGMENT)
    assert win.searches == 0
    assert [m.source for m in win.translation_matches_cache[62]["Termbases"]] == ["bovenwerk"]


def test_an_entry_already_there_only_gets_the_tm_match():
    win = Window(EXACT, TERMS)
    kept = object()
    win.translation_matches_cache[62] = {"LLM": [], "NT": [], "MT": [], "TM": [], "Termbases": [kept]}
    win._show_instant_tm_match(SEGMENT)
    entry = win.translation_matches_cache[62]
    assert entry["Termbases"] == [kept] and win.searches == 0
    assert len(entry["TM"]) == 1


def test_a_segment_without_terms_gets_an_empty_list():
    win = Window(EXACT, {})
    win._show_instant_tm_match(SEGMENT)
    assert win.translation_matches_cache[62]["Termbases"] == []
    assert len(win.translation_matches_cache[62]["TM"]) == 1


def test_a_failed_term_lookup_still_shows_the_tm_match():
    win = Window(EXACT, RuntimeError("index being rebuilt"))
    assert win._show_instant_tm_match(SEGMENT) is True
    assert len(win.shown) == 1
    assert len(win.translation_matches_cache[62]["TM"]) == 1


def test_no_tm_match_makes_no_entry():
    # Without an entry the move does its own fresh lookup, terms included.
    win = Window(None, TERMS)
    assert win._show_instant_tm_match(SEGMENT) is False
    assert win.translation_matches_cache == {} and win.searches == 0


@pytest.mark.parametrize("repeat", [2, 3])
def test_the_repeat_calls_of_one_click_look_nothing_up_again(repeat):
    win = Window(EXACT, TERMS)
    for _ in range(repeat):
        assert win._show_instant_tm_match(SEGMENT) is True
    assert win.searches == 1 and len(win.shown) == 1
