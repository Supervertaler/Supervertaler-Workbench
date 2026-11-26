"""Looking up TM, termbase and MT matches ahead of time (prefetch), the
translation cache, and refreshing the matches for a segment.

Part of the main window (SupervertalerQt), moved here unchanged. The
methods run as the main window's own: `self` is the SupervertalerQt.
"""

import json
import threading
from modules.tag_text import strip_outer_wrapping_tags


class MatchPrefetchMixin:
    def _load_synonyms_bulk(self):
        """Load every synonym from ``termbase_synonyms`` in one query,
        return as ``{term_id: {'source': [text, …], 'target': [text, …]}}``.

        v1.10.73 (Tier 2). Called by ``_build_termbase_index`` once per
        rebuild so per-term synonym lookups during search are pure
        dict access — no N+1 query storm. Synonyms are filtered to
        non-forbidden entries (Trados does the same in
        ``BulkLoadTargetSynonyms``) and ordered by ``display_order``
        when present so the "preferred synonym first" convention
        users set in the Edit Termbase Entry dialog is preserved
        through to the TermLens display.

        Defensive about schema variation: the ``forbidden`` and
        ``display_order`` columns were added in later schema
        migrations, so we check for their presence via
        ``PRAGMA table_info`` (mirrors the pattern in
        ``modules/termbase_entry_editor.load_synonyms``). Older
        databases — or in-memory test fixtures that haven't run
        every migration — still work, just without the
        forbidden-filter or the display-order sort.

        Returns ``{}`` on any error so the caller can carry on with
        an empty synonym map instead of failing the whole index
        build over a synonym query hiccup. The TermLens display just
        won't show synonyms in that case — same as today.
        """
        result = {}
        try:
            if not hasattr(self, 'db_manager') or self.db_manager is None:
                return result
            cur = self.db_manager.cursor
            cur.execute("PRAGMA table_info(termbase_synonyms)")
            cols = [r[1] for r in cur.fetchall()]
            has_forbidden = 'forbidden' in cols
            has_display_order = 'display_order' in cols

            where_clause = "WHERE COALESCE(forbidden, 0) = 0" if has_forbidden else ""
            order_clause = ("ORDER BY term_id, COALESCE(display_order, 0), id"
                            if has_display_order
                            else "ORDER BY term_id, id")
            cur.execute(
                f"SELECT term_id, synonym_text, language "
                f"FROM termbase_synonyms {where_clause} {order_clause}"
            )
            for term_id, syn_text, language in cur.fetchall():
                if not term_id or not syn_text:
                    continue
                bucket = result.setdefault(term_id, {'source': [], 'target': []})
                if language == 'source':
                    bucket['source'].append(syn_text)
                elif language == 'target':
                    bucket['target'].append(syn_text)
                # Anything else (legacy 'both', NULL, etc.) is silently
                # ignored — schema only documents 'source' and 'target'.
        except Exception as e:
            try:
                self.log(f"⚠️ Bulk synonym load failed: {e}")
            except Exception:
                pass
        return result

    def _trigger_idle_prefetch(self, current_row: int):
        """
        Trigger prefetch for the next few segments while user is idle.
        Called after user stops typing (debounced) - uses their thinking time productively.
        
        This makes Ctrl+Enter feel INSTANT because matches are already cached.
        Also triggers PROACTIVE HIGHLIGHTING for upcoming segments with glossary matches.
        """
        import json

        if not self.current_project or current_row < 0:
            return
        
        try:
            # Prefetch next 5 segments (enough for fast workflow, not too many to waste resources)
            next_segment_ids = []
            already_cached_ids = []
            start_idx = current_row + 1
            end_idx = min(start_idx + 5, len(self.current_project.segments))

            for seg in self.current_project.segments[start_idx:end_idx]:
                # Check if already cached
                with self.translation_matches_cache_lock:
                    if seg.id not in self.translation_matches_cache:
                        next_segment_ids.append(seg.id)
                    else:
                        already_cached_ids.append(seg.id)

            # For already-cached segments, trigger proactive highlighting immediately
            # This handles the case where segments were cached earlier but not highlighted
            for seg_id in already_cached_ids:
                try:
                    with self.termbase_cache_lock:
                        termbase_raw = self.termbase_cache.get(seg_id, {})
                    if termbase_raw:
                        termbase_json = json.dumps(termbase_raw)
                        # Apply highlighting on main thread (we're already on main thread here)
                        self._apply_proactive_highlighting(seg_id, termbase_json)
                except Exception:
                    pass  # Silent failure for proactive highlighting
            
            if next_segment_ids:
                # Start prefetch in background (silent, no logging)
                self._start_prefetch_worker(next_segment_ids)
        except Exception:
            pass  # Silent failure - prefetch is optimization, not critical

    def _start_prefetch_worker(self, segment_ids):
        """
        Start background thread to prefetch TM/MT/LLM matches for given segments.
        This enables instant switching between segments without waiting for match lookups.
        """
        if not segment_ids:
            return
        
        # 🧪 EXPERIMENTAL: Skip prefetch if cache kill switch is enabled
        if getattr(self, 'disable_all_caches', False):
            return
        
        # Stop any existing worker thread
        self.prefetch_stop_event.set()
        if self.prefetch_worker_thread and self.prefetch_worker_thread.is_alive():
            self.prefetch_worker_thread.join(timeout=2)
        
        # Reset stop event for new worker
        self.prefetch_stop_event.clear()
        
        # Start new background worker thread (silent - no logging)
        self.prefetch_worker_thread = threading.Thread(
            target=self._prefetch_worker_run,
            args=(segment_ids,),
            daemon=True  # Daemon thread - won't prevent program exit
        )
        self.prefetch_worker_thread.start()

    def _prefetch_worker_run(self, segment_ids):
        """
        Background worker: prefetch TM/MT/LLM matches for given segments.
        Runs in separate thread to avoid blocking UI.
        
        Creates its own database connection for thread-safe termbase lookups.
        Also emits signal to apply proactive highlighting on the main thread.
        """
        import sqlite3
        import json
        
        # Create thread-local database connection for termbase searches
        thread_db_cursor = None
        thread_db_connection = None
        try:
            if hasattr(self, 'db_manager') and self.db_manager and hasattr(self.db_manager, 'db_path'):
                thread_db_connection = sqlite3.connect(self.db_manager.db_path, timeout=15)
                thread_db_connection.row_factory = sqlite3.Row
                thread_db_connection.execute("PRAGMA journal_mode=WAL")
                thread_db_cursor = thread_db_connection.cursor()
        except Exception:
            pass  # Continue without thread-local connection - will use cache only

        # Active TM ids for this project (computed once, thread-safely, on our own
        # cursor). Lets the prefetch cache carry exact (100%) TM matches so the
        # match panel shows them instantly instead of "(No TM match)". Mirrors
        # TMMetadataManager.get_active_tm_ids() but runs on the worker connection.
        active_tm_ids = None
        try:
            pid = getattr(self.current_project, 'id', None) if self.current_project else None
            if pid and thread_db_cursor is not None:
                thread_db_cursor.execute(
                    "SELECT DISTINCT tm.tm_id FROM translation_memories tm "
                    "INNER JOIN tm_activation ta ON tm.id = ta.tm_db_id "
                    "WHERE ta.project_id = ? AND ta.is_active = 1",
                    (pid,),
                )
                active_tm_ids = [r[0] for r in thread_db_cursor.fetchall()]
        except Exception:
            active_tm_ids = None
        
        try:
            for idx, segment_id in enumerate(segment_ids):
                # Check stop signal
                if self.prefetch_stop_event.is_set():
                    return
                
                # Check if already cached
                with self.translation_matches_cache_lock:
                    if segment_id in self.translation_matches_cache:
                        continue  # Already cached, skip
                
                # Find segment
                segment = None
                if self.current_project:
                    for seg in self.current_project.segments:
                        if seg.id == segment_id:
                            segment = seg
                            break
                
                if not segment:
                    continue
                
                # Fetch TM/termbase matches (pass cursor for thread-safe termbase lookups)
                generation = getattr(self, 'termbase_index_generation', 0)
                matches = self._fetch_all_matches_for_segment(segment, thread_db_cursor, tm_ids=active_tm_ids)
                if generation != getattr(self, 'termbase_index_generation', 0):
                    # The terms changed while this segment was being looked
                    # up, and the caches were cleared for it: what we have
                    # is from the old terms. Leave it to a later pass.
                    continue

                # Count matches for logging and proactive highlighting
                tm_count = len(matches.get("TM", []))
                tb_count = len(matches.get("Termbases", []))
                mt_count = len(matches.get("MT", []))
                llm_count = len(matches.get("LLM", []))
                total_matches = tm_count + tb_count + mt_count + llm_count
                
                # Only cache results if we found something
                # Don't cache empty results - let main thread do fresh lookup
                if total_matches > 0:
                    with self.translation_matches_cache_lock:
                        self.translation_matches_cache[segment_id] = matches

                # PROACTIVE HIGHLIGHTING: Emit signal to apply highlighting on main thread
                # This makes upcoming segments show their glossary matches immediately
                if tb_count > 0:
                    try:
                        # Extract raw termbase matches from cache for highlighting
                        with self.termbase_cache_lock:
                            termbase_raw = self.termbase_cache.get(segment_id, {})

                        if termbase_raw:
                            # Convert to JSON for thread-safe signal transfer
                            termbase_json = json.dumps(termbase_raw)
                            # Emit signal - will be handled on main thread
                            self._proactive_highlight_signal.emit(segment_id, termbase_json)
                    except Exception:
                        pass  # Silent fail for proactive highlighting
            
        except Exception as e:
            self.log(f"Error in prefetch worker: {e}")
        
        finally:
            # Close thread-local database connection
            if thread_db_cursor:
                try:
                    thread_db_cursor.close()
                except:
                    pass
            if thread_db_connection:
                try:
                    thread_db_connection.close()
                except:
                    pass

    def _fetch_all_matches_for_segment(self, segment, thread_db_cursor=None, tm_ids=None):
        """
        Fetch TM, MT, and LLM matches for a single segment.
        Used by prefetch worker. Returns matches_dict with all match types.

        Args:
            segment: The segment to fetch matches for
            thread_db_cursor: Optional thread-local database cursor for termbase searches.
                              If provided and segment not in termbase_cache, will do direct lookup.
            tm_ids: Active TM ids for the project. When provided (with a cursor), the
                    exact (100%) TM match is looked up here so it is cached and shown
                    instantly; fuzzy TM still streams in on-demand via TMSearchWorker.
        """
        from modules.translation_results_panel import TranslationMatch
        
        matches_dict = {
            "LLM": [],
            "NT": [],
            "MT": [],
            "TM": [],
            "Termbases": [],
            "NonTrans": []  # Non-translatables
        }
        
        # Get project languages
        source_lang = getattr(self.current_project, 'source_lang', None) if self.current_project else None
        target_lang = getattr(self.current_project, 'target_lang', None) if self.current_project else None
        
        if not source_lang or not target_lang:
            return matches_dict
        
        # Convert language names to codes
        source_lang_code = self._convert_language_to_code(source_lang)
        target_lang_code = self._convert_language_to_code(target_lang)
        
        # 1. TM matches — cheap exact (100%) lookup on the worker's own cursor.
        # Previously skipped entirely ("not thread-safe"), which meant the prefetch
        # cached every segment with TM=[]. Since patent segments almost always have
        # termbase hits, those TM-less entries were cached as "complete" and the
        # match panel then showed "(No TM match)" even for segments that DO have a
        # 100% match. Looking the exact match up here (read-only, touch=False, uses
        # the indexed source_hash) makes the cache correct. Fuzzy TM still streams
        # in on-demand via TMSearchWorker on navigation.
        if self.enable_tm_matching and thread_db_cursor is not None and tm_ids:
            try:
                from modules.database_manager import DatabaseManager
                _dbm = DatabaseManager.__new__(DatabaseManager)
                _dbm.connection = thread_db_cursor.connection
                _dbm.cursor = thread_db_cursor
                _dbm.log = lambda _m: None
                exact = _dbm.get_exact_match(
                    source=segment.source,
                    tm_ids=tm_ids,
                    source_lang=source_lang_code,
                    target_lang=target_lang_code,
                    touch=False,
                )
                if exact:
                    tm_meta = getattr(self.tm_database, 'tm_metadata', {}) or {}
                    _tmid = exact.get('tm_id', '')
                    tm_name = tm_meta.get(_tmid, {}).get('name', _tmid) if isinstance(tm_meta, dict) else _tmid
                    matches_dict["TM"].append(TranslationMatch(
                        source=exact.get('source_text', ''),
                        target=exact.get('target_text', ''),
                        relevance=100,
                        metadata={
                            'tm_name': tm_name,
                            'tm_id': _tmid,
                            'reverse_match': bool(exact.get('reverse_match', False)),
                        },
                        match_type='TM',
                        compare_source=exact.get('source_text', ''),
                        provider_code='TM',
                    ))
            except Exception:
                pass  # On-demand worker will still fetch TM on navigation

        # 2. MT matches (if enabled)
        if self.enable_mt_matching:
            # Use the same MT fetching logic as the main search
            # For now, we'll skip MT in prefetch to avoid rate limiting
            # MT will still be fetched on-demand when user clicks
            pass
        
        # 3. LLM matches (if enabled)
        if self.enable_llm_matching:
            # Skip LLM in prefetch - too expensive and slow
            # LLM will still be fetched on-demand when user clicks
            pass
        
        # 4. Termbase matches - try cache first, then direct lookup if cursor provided
        termbase_matches_raw = None
        
        with self.termbase_cache_lock:
            if segment.id in self.termbase_cache:
                termbase_matches_raw = self.termbase_cache[segment.id]
        
        # If not in cache, match against the in-memory termbase index.
        #
        # v1.10.232: switched from the legacy per-word _search_termbases_thread_safe
        # (LIKE %word% + LIMIT 30, no ORDER BY) to the same _search_termbase_in_memory
        # that the interactive path and TermPicker use. The old path silently dropped
        # matches when a common word (e.g. "fractie"/"fraction" in a patent termbase)
        # appeared in >30 term entries — SQLite truncated the per-word result set
        # arbitrarily, so a longer phrase like "non-ferrometalen fractie" could be
        # cut while its shorter sibling "ferrometalen fractie" survived. TermLens
        # (fed from this prefetch cache) then showed the wrong/shorter term while
        # TermPicker (which falls back to the in-memory matcher) showed the correct
        # one. The in-memory index scans every term with no cap, so all surfaces now
        # agree. It reads self.termbase_index under termbase_index_lock, so it's
        # thread-safe to call from this worker; no DB cursor needed.
        if termbase_matches_raw is None:
            try:
                generation = getattr(self, 'termbase_index_generation', 0)
                termbase_matches_raw = self._deduplicate_termbase_matches(
                    self._search_termbase_in_memory(segment.source)
                )
                # Also populate the termbase cache for future use (not with a
                # result from terms that were replaced while we searched)
                if termbase_matches_raw:
                    with self.termbase_cache_lock:
                        if generation == getattr(self, 'termbase_index_generation', 0):
                            self.termbase_cache[segment.id] = termbase_matches_raw
            except Exception:
                pass  # Silently continue
        
        # Convert raw termbase matches to TranslationMatch objects
        if termbase_matches_raw:
            for term_id, match_info in termbase_matches_raw.items():
                # Extract source term, translation, ranking, and other metadata from match_info
                if isinstance(match_info, dict):
                    source_term = match_info.get('source', '')
                    target_term = match_info.get('translation', '')
                    ranking = match_info.get('ranking', None)
                    forbidden = match_info.get('forbidden', False)
                    is_project_termbase = match_info.get('is_project_termbase', False)
                    termbase_name = match_info.get('termbase_name', 'Default')
                else:
                    # Backward compatibility: if just string (shouldn't happen with new code)
                    source_term = str(term_id)
                    target_term = match_info
                    ranking = None
                    forbidden = False
                    is_project_termbase = False
                    termbase_name = 'Default'

                match_obj = TranslationMatch(
                    source=source_term,
                    target=target_term,
                    relevance=95,
                    metadata={
                        'termbase_name': termbase_name,
                        'ranking': ranking,
                        'forbidden': forbidden,
                        'is_project_termbase': is_project_termbase,
                        'term_id': match_info.get('term_id') if isinstance(match_info, dict) else None,
                        'termbase_id': match_info.get('termbase_id') if isinstance(match_info, dict) else None,
                        'domain': match_info.get('domain', '') if isinstance(match_info, dict) else '',
                        'notes': match_info.get('notes', '') if isinstance(match_info, dict) else '',
                        'project': match_info.get('project', '') if isinstance(match_info, dict) else '',
                        'client': match_info.get('client', '') if isinstance(match_info, dict) else '',
                        'target_synonyms': match_info.get('target_synonyms', []) if isinstance(match_info, dict) else []
                    },
                    match_type='Termbase',
                    compare_source=source_term,
                    provider_code='TB'
                )
                matches_dict["Termbases"].append(match_obj)
        
        return matches_dict

    def stop_prefetch_worker(self):
        """Stop the background prefetch worker gracefully"""
        if self.prefetch_worker_thread and self.prefetch_worker_thread.is_alive():
            self.log("⏹️  Stopping prefetch worker...")
            self.prefetch_stop_event.set()
            self.prefetch_worker_thread.join(timeout=2)
            self.log("✓ Prefetch worker stopped")

    def invalidate_translation_cache(self, smart_invalidation=True):
        """
        Invalidate prefetch cache when TM/Termbase is updated.
        
        Args:
            smart_invalidation: If True, only clear future segments (keeps already-seen segments cached).
                               If False, clear entire cache.
        
        Smart mode: When translator saves segment N, only clears cache for segments > N.
        This way, going backwards still uses cache, but going forward gets fresh TM matches.
        """
        with self.translation_matches_cache_lock:
            if not smart_invalidation:
                # Full invalidation (e.g., termbase import, bulk TM import)
                cache_size = len(self.translation_matches_cache)
                if cache_size > 0:
                    self.translation_matches_cache.clear()
                    self.log(f"🔄 Full cache invalidated ({cache_size} segments cleared)")
            else:
                # Smart invalidation: Only clear segments ahead of current position
                if hasattr(self, 'table') and self.table:
                    current_row = self.table.currentRow()
                    if current_row >= 0 and self.current_project:
                        # Clear cache for all segments AFTER current position
                        segments_to_clear = []
                        for seg in self.current_project.segments[current_row + 1:]:
                            if seg.id in self.translation_matches_cache:
                                segments_to_clear.append(seg.id)
                        
                        for seg_id in segments_to_clear:
                            del self.translation_matches_cache[seg_id]
                        
                        if segments_to_clear:
                            self.log(f"🔄 Smart cache: cleared {len(segments_to_clear)} future segments (keeping past segments cached)")
                else:
                    # Fallback: full clear if we can't determine position
                    cache_size = len(self.translation_matches_cache)
                    if cache_size > 0:
                        self.translation_matches_cache.clear()
                        self.log(f"🔄 Cache invalidated ({cache_size} segments)")

    def create_diff_html(self, current_text: str, tm_text: str) -> str:
        """Create HTML with visual diff highlighting between current and TM source
        
        Shows changes inline like memoQ:
        - Red strikethrough for deleted text (in TM but not in current)
        - Green underline + bold for added text (in current but not in TM)
        - Sequential display: deletions then additions in order
        """
        from difflib import SequenceMatcher
        
        s = SequenceMatcher(None, tm_text, current_text)
        html_parts = []
        
        for tag, i1, i2, j1, j2 in s.get_opcodes():
            tm_chunk = tm_text[i1:i2]
            current_chunk = current_text[j1:j2]
            
            if tag == 'equal':
                # Matching text - show normally
                html_parts.append(tm_chunk)
            elif tag == 'delete':
                # Text removed from TM - show with red strikethrough (like memoQ deletion)
                html_parts.append(f'<span style="color: #dc2626; text-decoration: line-through;">{tm_chunk}</span>')
            elif tag == 'insert':
                # Text added in current - show with green underline + bold (like memoQ insertion)
                html_parts.append(f'<span style="color: #22c55e; text-decoration: underline; font-weight: bold;">{current_chunk}</span>')
            elif tag == 'replace':
                # Text changed - show deletion (strikethrough) followed by insertion (underline)
                # This matches memoQ's behavior: old text strikethrough, new text underlined
                html_parts.append(f'<span style="color: #dc2626; text-decoration: line-through;">{tm_chunk}</span>')
                html_parts.append(' ')  # Add space between deletion and insertion
                html_parts.append(f'<span style="color: #22c55e; text-decoration: underline; font-weight: bold;">{current_chunk}</span>')
        
        return ''.join(html_parts)

    def force_refresh_matches(self):
        """Force refresh all glossary and TM matches for current segment.

        Bound to F5 and to the TermLens 🔄 button (v1.10.72: collapsed
        into a single code path). Does:

        1. **Smart index rebuild** — compares a fresh snapshot of the
           termbase tables against the snapshot captured at the end of
           the last ``_build_termbase_index()`` call (same gating the
           v1.10.69 file watcher uses). If anything changed in
           ``termbases`` or ``termbase_terms`` (count, max id, max
           modified_date), rebuilds the in-memory index from disk so
           cross-process edits (typically from the Supervertaler for
           Trados plugin sharing the same SQLite database) are picked
           up. If nothing changed, skips the rebuild — keeping the
           common-case F5 fast (<100 ms) for "I just want to redraw".

        2. **Cache clear** — drops the per-segment termbase match cache
           and the per-segment translation-matches cache for the
           current segment.

        3. **Fresh re-search** — termbases (via the in-memory index),
           TMs (via SQL), NT lists.

        4. **Full redraw** — TermLens widgets (both instances) and the
           Translation Results panel.

        Before v1.10.72 this was the "F5-only" path and a separate
        ``_post_termbase_delete_refresh`` did step 1 unconditionally.
        Having two paths was confusing — users didn't know which to
        press — and the lighter F5 path occasionally left the TermLens
        widget under-refreshed after term edits (the v1.10.71 issue).
        Collapsing into one path with smart gating fixes both: F5 and
        🔄 do the same thing, and the snapshot decides whether the
        rebuild step actually runs.
        """
        if not self.current_project:
            self.statusBar().showMessage("No project open", 3000)
            return

        # v1.10.72: smart index rebuild — only if the termbase tables
        # actually changed since we last rebuilt. The snapshot is
        # updated at the end of every ``_build_termbase_index()``
        # call, so own-writes that ran through a full rebuild don't
        # re-trigger here. Cross-process writes (Trados plugin) and
        # own-writes via the optimisation path that bypassed the
        # rebuild will both have shifted the snapshot, and the
        # rebuild fires.
        try:
            fresh_snap = self._snapshot_termbase_db_state()
            prev_snap = getattr(self, '_termbase_db_last_snapshot', None)
            if fresh_snap is None or prev_snap is None or fresh_snap != prev_snap:
                if prev_snap is not None and fresh_snap is not None:
                    self.log(
                        f"🔄 Termbase DB changed since last rebuild — "
                        f"rebuilding index "
                        f"(termbases:{prev_snap[0]}→{fresh_snap[0]}, "
                        f"terms:{prev_snap[2]}→{fresh_snap[2]})"
                    )
                else:
                    self.log("🔄 Termbase index snapshot missing — rebuilding")
                self._build_termbase_index()
                # _build_termbase_index updates _termbase_db_last_snapshot
                # so subsequent F5 / 🔄 presses skip the rebuild step.
        except Exception as e:
            self.log(f"⚠️ Smart index-rebuild check failed: {e}")
        
        # Get current segment
        current_row = self.table.currentRow()
        if current_row < 0:
            self.statusBar().showMessage("No segment selected", 3000)
            return
        
        # Get segment ID from the grid cell
        id_item = self.table.item(current_row, 0)
        if not id_item:
            self.statusBar().showMessage("Could not find segment", 3000)
            return
        
        try:
            segment_id = int(id_item.text())
        except (ValueError, AttributeError):
            self.statusBar().showMessage("Invalid segment ID", 3000)
            return
        
        # Find the segment
        segment = next((seg for seg in self.current_project.segments if seg.id == segment_id), None)
        if not segment:
            self.statusBar().showMessage(f"Segment {segment_id} not found", 3000)
            return
        
        self.log(f"🔄 Force refresh: Clearing all caches for segment {segment_id}...")
        
        # 1. Clear termbase cache for this segment (and all segments to be safe)
        with self.termbase_cache_lock:
            self.termbase_cache.clear()
            self.log("   ✓ Termbase cache cleared")
        
        # 2. Clear translation matches cache for this segment
        with self.translation_matches_cache_lock:
            if segment_id in self.translation_matches_cache:
                del self.translation_matches_cache[segment_id]
            self.log("   ✓ Translation matches cache cleared for this segment")
        
        # 3. Force fresh termbase search
        self.log("   🔍 Searching termbases...")
        termbase_matches = self.find_termbase_matches_in_source(segment.source)
        tb_count = len(termbase_matches) if termbase_matches else 0
        self.log(f"   ✓ Found {tb_count} termbase matches")
        
        # 4. Force fresh TM search. The previous block searched via self.tm_manager
        #    and a tm_settings['tms']/read shape that DON'T EXIST in this app, so F5
        #    always found 0 TM matches — and step 7b below then CLEARED the Match
        #    Panel. Do it properly: an exact (100%) lookup now (instant), with
        #    fuzzy/MT/LLM streaming in via the async worker scheduled at the end.
        self.log("   🔍 Searching translation memories...")
        tm_matches = []
        try:
            dbm = getattr(self, 'db_manager', None) or getattr(getattr(self, 'tm_database', None), 'db', None)
            tm_ids = None
            if getattr(self.current_project, 'tm_settings', None):
                tm_ids = self.current_project.tm_settings.get('activated_tm_ids')
            if dbm is not None and not (isinstance(tm_ids, list) and len(tm_ids) == 0):
                search_source = segment.source
                if self.hide_outer_wrapping_tags:
                    search_source, _ = strip_outer_wrapping_tags(search_source)
                exact = dbm.get_exact_match(
                    source=search_source, tm_ids=tm_ids,
                    source_lang=getattr(getattr(self, 'tm_database', None), 'source_lang', None),
                    target_lang=getattr(getattr(self, 'tm_database', None), 'target_lang', None),
                    touch=False)
                if exact:
                    _meta = getattr(self.tm_database, 'tm_metadata', {}) or {}
                    _tid = exact.get('tm_id', '')
                    tm_matches = [{
                        'source': exact.get('source_text', ''),
                        'target': exact.get('target_text', ''),
                        'similarity': 1.0,
                        'tm_name': _meta.get(_tid, {}).get('name', _tid) if isinstance(_meta, dict) else _tid,
                        'tm_id': _tid,
                        'reverse_match': bool(exact.get('reverse_match', False)),
                    }]
        except Exception as e:
            self.log(f"   ⚠️ TM search error: {e}")

        tm_count = len(tm_matches) if tm_matches else 0
        self.log(f"   ✓ Found {tm_count} exact TM match(es); fuzzy/MT/LLM will stream in")
        
        # 5. Get NT matches (fresh)
        nt_matches = self.find_nt_matches_in_source(segment.source)
        nt_count = len(nt_matches) if nt_matches else 0
        
        # 6. Update TermLens with fresh results
        if (hasattr(self, 'termlens_widget') and self.termlens_widget) or (hasattr(self, 'termlens_widget_match') and self.termlens_widget_match):
            try:
                # Convert termbase matches dict to list format for termlens
                # v1.10.73 (Tier 1 + Tier 2): forward all metadata
                # fields and source synonyms in addition to the
                # target_synonyms field that was already here. Brings
                # this builder into line with the two other call sites
                # so the TermBlock tooltip + alternative-translation
                # rendering have the same data regardless of which
                # refresh path got us here.
                tb_list = []
                for term_id, match_info in termbase_matches.items():
                    tb_list.append({
                        'source_term': match_info.get('source', ''),
                        'target_term': match_info.get('translation', ''),
                        'termbase_name': match_info.get('termbase_name', ''),
                        'ranking': match_info.get('ranking', 0),
                        'is_project_termbase': match_info.get('is_project_termbase', False),
                        'source_synonyms': match_info.get('source_synonyms', []),
                        'target_synonyms': match_info.get('target_synonyms', []),
                        'term_id': term_id,
                        'termbase_id': match_info.get('termbase_id'),
                        'notes': match_info.get('notes', ''),
                        'definition': match_info.get('definition', ''),
                        'domain': match_info.get('domain', ''),
                        'url': match_info.get('url', ''),
                        'source_abbreviation': match_info.get('source_abbreviation', ''),
                        'target_abbreviation': match_info.get('target_abbreviation', ''),
                        # v1.10.75 (Tier 3) flags
                        'forbidden': match_info.get('forbidden', False),
                        'is_nontranslatable': match_info.get('is_nontranslatable', False),
                        # v1.10.75 (Tier 3c) abbreviation marker
                        'matched_via_abbreviation': match_info.get('matched_via_abbreviation', False),
                    })
                # Get status hint for termbase activation
                status_hint = self._get_termbase_status_hint()

                # Update both TermLens widgets
                self._update_both_termlens(segment.source, tb_list, nt_matches, status_hint)
                self.log("   ✓ TermLens updated")
            except Exception as e:
                self.log(f"   ⚠️ TermLens update error: {e}")
        
        # 7. Update Translation Results panel with fresh results
        if hasattr(self, 'results_panels'):
            from modules.translation_results_panel import TranslationMatch
            
            # Build matches dict for panel
            matches = {
                "TM": [],
                "Termbases": [],
                "MT": [],
                "LLM": []
            }
            
            # Add TM matches
            for tm_match in tm_matches:
                matches["TM"].append(TranslationMatch(
                    source=tm_match.get('source', ''),
                    target=tm_match.get('target', ''),
                    relevance=tm_match.get('similarity', 0) * 100,
                    match_type="TM",
                    metadata={
                        'tm_name': tm_match.get('tm_name', 'Unknown TM'),
                    }
                ))
            
            # Add termbase matches
            for term_id, match_info in termbase_matches.items():
                matches["Termbases"].append(TranslationMatch(
                    source=match_info.get('source', ''),
                    target=match_info.get('translation', ''),
                    relevance=100,  # Termbase matches are always exact
                    match_type="Termbase",
                    metadata={
                        'termbase_name': match_info.get('termbase_name', ''),
                        'ranking': match_info.get('ranking', 0),
                        'is_project_termbase': match_info.get('is_project_termbase', False),
                        'notes': match_info.get('notes', ''),
                        'term_id': term_id,
                        'termbase_id': match_info.get('termbase_id')
                    }
                ))
            
            for panel in self.results_panels:
                try:
                    panel.clear()
                    panel.set_matches(matches)
                except Exception as e:
                    self.log(f"   ⚠️ Panel update error: {e}")
            self.log("   ✓ Translation Results panel updated")
        
        # 7b. Update Match Panel with TM matches (must use TranslationMatch objects for correct display)
        from modules.translation_results_panel import TranslationMatch
        tm_matches_for_panel = []
        for tm_match in tm_matches:
            tm_matches_for_panel.append(TranslationMatch(
                source=tm_match.get('source', ''),
                target=tm_match.get('target', ''),
                relevance=int(tm_match.get('similarity', 0) * 100),
                metadata={
                    'tm_name': tm_match.get('tm_name', 'TM'),
                    'tm_id': tm_match.get('tm_id', ''),
                },
                match_type='TM',
                compare_source=tm_match.get('source', ''),
                provider_code='TM'
            ))
        self.set_compare_panel_matches(segment_id, segment.source, tm_matches_for_panel, [])
        self.log("   ✓ Match Panel updated")
        
        # 8. Re-apply termbase highlighting in the grid source cell
        try:
            source_widget = self.table.cellWidget(current_row, 2)
            if source_widget and hasattr(source_widget, 'highlight_termbase_matches'):
                source_widget.highlight_termbase_matches(termbase_matches)
                self.log("   ✓ Grid highlighting updated")
            else:
                self.log("   ⚠️ Could not find source widget for highlighting")
        except Exception as e:
            self.log(f"   ⚠️ Highlighting error: {e}")
        
        # Fuzzy/MT/LLM stream in asynchronously (the exact match is already shown).
        try:
            self._schedule_mt_and_llm_matches(segment)
        except Exception:
            pass

        # Show status
        total_matches = tb_count + tm_count + nt_count
        self.statusBar().showMessage(f"🔄 Refreshed: {tb_count} termbase, {tm_count} TM, {nt_count} NT matches", 5000)
        self.log(f"🔄 Force refresh complete: {total_matches} total matches for segment {segment_id}")
