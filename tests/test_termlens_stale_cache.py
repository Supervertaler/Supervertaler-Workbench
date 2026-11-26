"""Matches worked out from old terms go when the terms change (Nov 2026).

The match cache keeps a term list per segment. Rebuilding the term index
(a termbase ticked, a term added, the first build after a project opens)
cleared the other term cache and never this one. So a segment looked ahead
to before a termbase was ticked kept its empty term list, and TermLens
showed "No matches" there until F5. It hit segments with a TM match, because
the look-ahead only keeps a segment it found something for.

The index builder now clears both caches itself, and the look-ahead worker
drops a result it worked out while the terms were being replaced. These
tests use a real term database in a temp folder.
"""

from __future__ import annotations

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
CODES = {"Dutch": "nl", "English": "en"}


class Window(TermbasesMixin, MatchPrefetchMixin, TranslationMemoriesMixin):
    """Only what building the index and looking a segment up touch."""

    def __init__(self, db):
        self.db_manager = db
        self.current_project = SimpleNamespace(id=PROJECT, source_lang="Dutch", target_lang="English",
                                               segments=[SEGMENT, OTHER], tm_settings={})
        self.enable_tm_matching = self.enable_mt_matching = self.enable_llm_matching = False
        self.termbase_index, self.termbase_index_lock = [], threading.Lock()
        self.termbase_index_generation = 0
        self._termbase_index_built = False
        self.termbase_cache, self.termbase_cache_lock = {}, threading.Lock()
        self.translation_matches_cache, self.translation_matches_cache_lock = {}, threading.Lock()
        self.logged = []

    def log(self, message):
        self.logged.append(message)

    def _convert_language_to_code(self, name):
        return CODES.get(name, (name or "").lower())


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


def terms(entry):
    return sorted(m.source for m in entry["Termbases"])


def test_ticking_a_termbase_drops_what_was_looked_up_without_it(db, mgr, termbase):
    win = Window(db)
    win._build_termbase_index()                       # project opened: nothing ticked
    stale = win._fetch_all_matches_for_segment(SEGMENT)
    assert stale["Termbases"] == []
    win.translation_matches_cache[6] = stale          # as the look-ahead kept it (it had a TM match)

    mgr.activate_termbase(termbase, PROJECT)
    win._build_termbase_index()                       # what ticking Read does

    assert win.translation_matches_cache == {}        # so the move looks the segment up afresh
    assert terms(win._fetch_all_matches_for_segment(SEGMENT)) == ["stand der techniek", "tank"]


def test_unticking_a_termbase_drops_its_terms_from_both_caches(db, mgr, termbase):
    mgr.activate_termbase(termbase, PROJECT)
    win = Window(db)
    win._build_termbase_index()
    win.translation_matches_cache[6] = win._fetch_all_matches_for_segment(SEGMENT)
    assert 6 in win.termbase_cache and len(win.translation_matches_cache[6]["Termbases"]) == 2

    mgr.deactivate_termbase(termbase, PROJECT)
    win._build_termbase_index()

    assert win.termbase_cache == {} and win.translation_matches_cache == {}
    assert win._fetch_all_matches_for_segment(SEGMENT)["Termbases"] == []


def test_a_term_added_shows_on_a_segment_already_looked_up(db, mgr, termbase):
    mgr.activate_termbase(termbase, PROJECT)
    win = Window(db)
    win._build_termbase_index()
    win.translation_matches_cache[6] = win._fetch_all_matches_for_segment(SEGMENT)

    mgr.add_term(termbase, "systemen", "systems")
    win._build_termbase_index()

    assert terms(win._fetch_all_matches_for_segment(SEGMENT)) == ["stand der techniek", "systemen", "tank"]


def test_each_build_moves_the_generation_on(db, mgr, termbase):
    win = Window(db)
    win._build_termbase_index()
    win._build_termbase_index()
    assert win.termbase_index_generation == 2


def test_a_build_that_fails_leaves_the_index_and_the_caches_alone(db, mgr, termbase):
    mgr.activate_termbase(termbase, PROJECT)
    win = Window(db)
    win._build_termbase_index()
    kept = win._fetch_all_matches_for_segment(SEGMENT)
    win.translation_matches_cache[6] = kept
    index, generation = win.termbase_index, win.termbase_index_generation

    win.db_manager = SimpleNamespace(cursor=SimpleNamespace(
        execute=lambda *a: (_ for _ in ()).throw(RuntimeError("database is locked")),
        fetchall=lambda: []), connection=db.connection)
    win._build_termbase_index()                       # logs, does not raise

    assert any("Failed to build termbase index" in m for m in win.logged)
    assert win.termbase_index is index and win.termbase_index_generation == generation
    assert win.translation_matches_cache == {6: kept}   # still right for the index still in use


# --- the look-ahead worker, with the terms replaced under it ---------------

def test_the_worker_drops_a_result_from_terms_replaced_while_it_searched(db, mgr, termbase):
    win = Window(db)
    win._build_termbase_index()                       # nothing ticked
    win.prefetch_stop_event = threading.Event()
    win._highlight_requested = []
    mgr.activate_termbase(termbase, PROJECT)
    real = win._fetch_all_matches_for_segment

    def slow(segment, cursor=None, tm_ids=None):
        found = real(segment, cursor, tm_ids=tm_ids)  # searched the old, empty index
        found["TM"] = ["a 100% match"]                # so the worker would keep it
        if segment.id == 6:
            win._build_termbase_index()               # the translator ticks Read meanwhile
        return found

    win._fetch_all_matches_for_segment = slow
    run_worker(win, [6])
    assert 6 not in win.translation_matches_cache     # not kept with an empty term list
    assert terms(real(SEGMENT)) == ["stand der techniek", "tank"]


def test_the_worker_keeps_a_result_when_nothing_changed(db, mgr, termbase):
    mgr.activate_termbase(termbase, PROJECT)
    win = Window(db)
    win._build_termbase_index()
    win.prefetch_stop_event = threading.Event()
    run_worker(win, [6, 7])
    assert terms(win.translation_matches_cache[6]) == ["stand der techniek", "tank"]
    assert terms(win.translation_matches_cache[7]) == ["tank"]


def run_worker(win, segment_ids):
    """Run the look-ahead worker's own method here, on this thread."""
    import inspect
    name = next(n for n, f in inspect.getmembers(MatchPrefetchMixin, inspect.isfunction)
                if "prefetch_stop_event.is_set()" in inspect.getsource(f)
                and "_fetch_all_matches_for_segment(segment, thread_db_cursor" in inspect.getsource(f))
    getattr(win, name)(segment_ids)


# --- TM results arriving for a segment with no entry yet -------------------

def test_tm_results_for_a_new_entry_bring_that_segments_own_terms(db, mgr, termbase):
    mgr.activate_termbase(termbase, PROJECT)
    win = Window(db)
    win._build_termbase_index()
    win._pending_termbase_matches = ["another segment's terms"]   # what it used to borrow
    win.results_panels = []
    win.table = None
    try:
        win._on_tm_search_results(6, [{"source": SOURCE, "target": "Systems are known.", "match_pct": 100,
                                       "tm_name": "Client TM", "tm_id": "tm1"}])
    except Exception:
        pass                                          # the display part needs a real window
    entry = win.translation_matches_cache[6]
    assert len(entry["TM"]) == 1
    assert terms(entry) == ["stand der techniek", "tank"]
