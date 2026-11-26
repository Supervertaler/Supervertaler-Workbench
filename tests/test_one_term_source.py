"""A segment's terms come from one place (Nov 2026).

The match cache used to keep its own copy of each segment's terms, next to
the TM matches. Each speed-up trusted that copy more, and each time the terms
changed in a way nobody had covered, TermLens showed an old or empty list
until F5: on a segment with a 100% TM match (PR #281), after a termbase was
ticked (PR #283), on a move that only refreshes the glossary. There is no
copy now. Everything that shows a segment's terms asks _segment_terms, which
remembers its answer in termbase_cache; _build_termbase_index clears that
whenever the terms change. A match-cache entry holds no terms at all.

These tests use a real term database in a temp folder.
"""

from __future__ import annotations

import inspect
import os
import sys
import threading
from types import SimpleNamespace

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import pytest

from modules.database_manager import DatabaseManager
from modules.main_window.match_prefetch import MatchPrefetchMixin
from modules.main_window.termbases import TermbasesMixin
from modules.main_window.translation_memories import TranslationMemoriesMixin
from modules.termbase_manager import TermbaseManager

PROJECT = 42
SOURCE = "In de stand der techniek zijn systemen bekend voor een tank."
SEGMENT = SimpleNamespace(id=6, source=SOURCE)
OTHER = SimpleNamespace(id=7, source="Deze tank is leeg.")
BARE = SimpleNamespace(id=8, source="Zonder iets.")
CODES = {"Dutch": "nl", "English": "en"}
EXACT = {"source_text": SOURCE, "target_text": "Systems are known in the prior art.", "tm_id": "tm1"}


class Window(TermbasesMixin, MatchPrefetchMixin, TranslationMemoriesMixin):
    """Only what looking a segment up, and showing it, touch."""

    def __init__(self, db, exact=None):
        self.db_manager = db
        self.current_project = SimpleNamespace(id=PROJECT, source_lang="Dutch", target_lang="English",
                                               segments=[SEGMENT, OTHER, BARE],
                                               tm_settings={"activated_tm_ids": ["tm1"]})
        self.tm_database = SimpleNamespace(tm_metadata={"tm1": {"name": "Client TM"}},
                                           source_lang="nl", target_lang="en")
        self.enable_tm_matching = self.enable_termbase_matching = True
        self.enable_mt_matching = self.enable_llm_matching = False
        self.enable_termbase_grid_highlighting = True
        self.hide_outer_wrapping_tags = self.debug_mode_enabled = False
        self.termbase_index, self.termbase_index_lock = [], threading.Lock()
        self.termbase_index_generation = 0
        self._termbase_index_built = False
        self.termbase_cache, self.termbase_cache_lock = {}, threading.Lock()
        self.translation_matches_cache, self.translation_matches_cache_lock = {}, threading.Lock()
        self.prefetch_stop_event = threading.Event()
        self.logged, self.termlens, self.highlighted, self.compared = [], [], [], []
        self.searches = 0
        self._exact = exact

    def log(self, message):
        self.logged.append(message)

    def _convert_language_to_code(self, name):
        return CODES.get(name, (name or "").lower())

    def find_termbase_matches_in_source(self, source):
        self.searches += 1
        return super().find_termbase_matches_in_source(source)

    # what the views are fed with
    def _update_both_termlens(self, source, terms, nt, hint=None):
        self.termlens.append(sorted(t["source_term"] for t in terms))

    def highlight_source_with_termbase(self, row, source, terms):
        self.highlighted.append(sorted(t["source"] for t in terms.values()))

    def set_compare_panel_matches(self, *args, **kwargs):
        self.compared.append(args)

    def find_nt_matches_in_source(self, source):
        return []

    def _get_termbase_status_hint(self):
        return None


@pytest.fixture
def db(tmp_path):
    d = DatabaseManager(db_path=str(tmp_path / "t.db"), log_callback=lambda *a, **k: None)
    d.connect()
    return d


@pytest.fixture
def mgr(db):
    return TermbaseManager(db, log_callback=lambda *a, **k: None)


@pytest.fixture
def termbase(mgr):
    """A termbase with two of the segment's terms, not ticked for the project yet."""
    tb = mgr.create_termbase("Patents", "nl", "en", project_id=None, is_global=True)
    mgr.add_term(tb, "stand der techniek", "prior art")
    mgr.add_term(tb, "tank", "tank")
    return tb


@pytest.fixture
def win(db, mgr, termbase):
    """The project open with the termbase ticked and its terms loaded."""
    mgr.activate_termbase(termbase, PROJECT)
    w = Window(db)
    w._build_termbase_index()
    w.searches = 0
    return w


def names(terms):
    return sorted(t["source"] for t in terms.values())


# --- the one source -------------------------------------------------------

def test_a_segments_terms_are_looked_up_once(win):
    assert names(win._segment_terms(SEGMENT)) == ["stand der techniek", "tank"]
    assert names(win._segment_terms(SEGMENT)) == ["stand der techniek", "tank"]
    assert win.searches == 1


def test_no_terms_is_remembered_too(win):
    assert win._segment_terms(BARE) == {} and win._segment_terms(BARE) == {}
    assert win.searches == 1 and win.termbase_cache[8] == {}


def test_matching_switched_off_gives_nothing_and_searches_nothing(win):
    win.enable_termbase_matching = False
    assert win._segment_terms(SEGMENT) == {}
    assert win.searches == 0 and win.termbase_cache == {}


def test_with_the_caches_switched_off_nothing_is_remembered(win):
    win.disable_all_caches = True
    win._segment_terms(SEGMENT)
    win._segment_terms(SEGMENT)
    assert win.searches == 2 and win.termbase_cache == {}


def test_a_lookup_that_fails_remembers_nothing(win, monkeypatch):
    def broken(self, source):
        raise RuntimeError("index being rebuilt")

    monkeypatch.setattr(Window, "find_termbase_matches_in_source", broken)
    with pytest.raises(RuntimeError):
        win._segment_terms(SEGMENT)
    assert win.termbase_cache == {}
    monkeypatch.undo()
    assert names(win._segment_terms(SEGMENT)) == ["stand der techniek", "tank"]   # the next try works


def test_terms_replaced_during_a_search_are_not_remembered(win, monkeypatch):
    real = TermbasesMixin.find_termbase_matches_in_source

    def slow(self, source):
        found = real(self, source)
        self._build_termbase_index()                  # the terms change while we searched
        return found

    monkeypatch.setattr(Window, "find_termbase_matches_in_source", slow)
    win._segment_terms(SEGMENT)
    assert win.termbase_cache == {}


# --- the terms change -----------------------------------------------------

def test_ticking_a_termbase_shows_its_terms_on_a_segment_already_visited(db, mgr, termbase):
    w = Window(db)
    w._build_termbase_index()                         # project opened: nothing ticked
    assert w._segment_terms(SEGMENT) == {}
    w.translation_matches_cache[6] = {"TM": ["a 100% match"]}

    mgr.activate_termbase(termbase, PROJECT)
    w._build_termbase_index()                         # what ticking Read does

    assert names(w._segment_terms(SEGMENT)) == ["stand der techniek", "tank"]
    assert w.translation_matches_cache == {6: {"TM": ["a 100% match"]}}   # the TM match is kept


def test_unticking_a_termbase_takes_its_terms_away(win, mgr, termbase):
    assert len(win._segment_terms(SEGMENT)) == 2
    mgr.deactivate_termbase(termbase, PROJECT)
    win._build_termbase_index()
    assert win._segment_terms(SEGMENT) == {}


def test_a_term_added_shows_on_a_segment_already_visited(win, mgr, termbase):
    win._segment_terms(SEGMENT)
    mgr.add_term(termbase, "systemen", "systems")
    win._build_termbase_index()
    assert names(win._segment_terms(SEGMENT)) == ["stand der techniek", "systemen", "tank"]


def test_a_build_that_fails_leaves_the_index_and_what_is_remembered(win, db):
    kept = win._segment_terms(SEGMENT)
    index, generation = win.termbase_index, win.termbase_index_generation
    win.db_manager = SimpleNamespace(cursor=SimpleNamespace(
        execute=lambda *a: (_ for _ in ()).throw(RuntimeError("database is locked")),
        fetchall=lambda: []), connection=db.connection)
    win._build_termbase_index()                       # logs, does not raise
    assert any("Failed to build termbase index" in m for m in win.logged)
    assert win.termbase_index is index and win.termbase_index_generation == generation
    assert win.termbase_cache == {6: kept}            # still right for the index still in use


# --- a match-cache entry holds no terms -----------------------------------

def run_worker(win, segment_ids):
    """Run the look-ahead worker's own method here, on this thread."""
    name = next(n for n, f in inspect.getmembers(MatchPrefetchMixin, inspect.isfunction)
                if "prefetch_stop_event.is_set()" in inspect.getsource(f)
                and "_fetch_all_matches_for_segment(segment, thread_db_cursor" in inspect.getsource(f))
    getattr(win, name)(segment_ids)


def test_the_look_ahead_keeps_entries_without_terms_and_remembers_the_terms_once(win):
    run_worker(win, [6, 7, 8])
    assert set(win.translation_matches_cache) == {6, 7}          # 8 found nothing: no entry
    assert all("Termbases" not in entry for entry in win.translation_matches_cache.values())
    assert names(win.termbase_cache[6]) == ["stand der techniek", "tank"]
    assert win.termbase_cache[8] == {}
    searched = win.searches
    assert names(win._segment_terms(SEGMENT)) == ["stand der techniek", "tank"]
    assert win.searches == searched                   # the move reuses the worker's lookup


def test_the_look_ahead_still_reports_the_terms_it_found(win):
    found = win._fetch_all_matches_for_segment(SEGMENT)
    assert sorted(m.source for m in found["Termbases"]) == ["stand der techniek", "tank"]
    assert {m.metadata["termbase_name"] for m in found["Termbases"]} == {"Patents"}


def test_the_worker_never_builds_the_index_itself(db, mgr, termbase):
    mgr.activate_termbase(termbase, PROJECT)
    w = Window(db)                                    # project just opened: index not built
    assert w._segment_terms(SEGMENT, build_index=False) == {}
    assert w.termbase_index_generation == 0 and w.termbase_cache == {}   # nothing built, nothing remembered
    assert names(w._segment_terms(SEGMENT)) == ["stand der techniek", "tank"]   # the move builds it


def test_the_worker_carries_on_when_the_term_lookup_fails(win, monkeypatch):
    def broken(self, segment, build_index=True):
        raise RuntimeError("no terms today")

    monkeypatch.setattr(Window, "_segment_terms", broken)
    assert win._fetch_all_matches_for_segment(SEGMENT)["Termbases"] == []


def test_the_instant_tm_match_makes_an_entry_without_terms(win):
    win.db_manager = SimpleNamespace(get_exact_match=lambda **kw: EXACT)
    assert win._show_instant_tm_match(SEGMENT) is True
    entry = win.translation_matches_cache[6]
    assert [m.target for m in entry["TM"]] == [EXACT["target_text"]] and "Termbases" not in entry
    assert len(win.compared) == 1


def test_tm_results_make_an_entry_without_terms(win):
    win._pending_termbase_matches = ["another segment's terms"]   # what it once borrowed
    win.results_panels, win.table = [], None
    try:
        win._on_tm_search_results(6, [{"source": SOURCE, "target": "Systems are known.", "match_pct": 100,
                                       "tm_name": "Client TM", "tm_id": "tm1"}])
    except Exception:
        pass                                          # the display part needs a real window
    entry = win.translation_matches_cache[6]
    assert len(entry["TM"]) == 1 and "Termbases" not in entry


# --- what the translator sees ---------------------------------------------

def glossary_move(win, segment):
    row = win.current_project.segments.index(segment)
    win.table = SimpleNamespace(item=lambda r, c: SimpleNamespace(text=lambda: str(win.current_project.segments[r].id)))
    win.termlens_widget, win.termlens_widget_match = object(), None
    win._on_cell_selected_glossary_only(row)


def test_a_glossary_only_move_shows_terms_with_no_match_entry_at_all(win):
    glossary_move(win, SEGMENT)                       # it showed "No matches" here before
    assert win.termlens == [["stand der techniek", "tank"]]
    assert win.highlighted == [["stand der techniek", "tank"]]


def test_a_segment_with_only_a_tm_entry_still_shows_its_terms(win):
    win.db_manager_real = win.db_manager
    win.db_manager = SimpleNamespace(get_exact_match=lambda **kw: EXACT, cursor=win.db_manager.cursor,
                                     connection=win.db_manager.connection)
    win._show_instant_tm_match(SEGMENT)               # the entry that once meant "no terms"
    glossary_move(win, SEGMENT)
    assert win.termlens == [["stand der techniek", "tank"]]


def test_a_segment_without_terms_shows_none_and_highlights_nothing(win):
    glossary_move(win, BARE)
    assert win.termlens == [[]] and win.highlighted == []


def test_no_code_reads_terms_out_of_a_match_cache_entry():
    """The move handler is too large to run here: check its source instead."""
    import modules.main_window.grid_navigation as nav
    import modules.main_window.termbases as tb
    hit = inspect.getsource(nav.GridNavigationMixin)
    start = hit.index("CHECK PREFETCH CACHE FIRST")
    stop = hit.index("Check if TM/Termbase matching is enabled", start)
    hit = hit[start:stop]
    assert "self._segment_terms(segment)" in hit
    assert "self._terms_for_termlens(cached_terms)" in hit
    assert "self.termbase_cache" not in hit           # no second look-up route beside the one source
    glossary = inspect.getsource(tb.TermbasesMixin._on_cell_selected_glossary_only)
    assert "translation_matches_cache" not in glossary and '"Termbases"' not in glossary
