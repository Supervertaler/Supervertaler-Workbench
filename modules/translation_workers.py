"""Background workers: TM search, pre-translation, proofreading and glossary
extraction. Moved unchanged from Supervertaler.py.
"""

import json
import re
import time
import traceback
from PyQt6.QtCore import QThread, pyqtSignal
from modules import inline_codes as _inline_codes
from modules.tag_text import autotag_extract_tags, place_tags_via_llm, strip_all_tags


class TMSearchWorker(QThread):
    """Background worker thread for TM fuzzy search.

    Runs expensive SequenceMatcher-based fuzzy matching off the main thread
    to prevent UI freezes, especially with large TMs (1M+ entries).
    Uses its own SQLite connection since connections can't be shared across threads.
    """

    # Signal: segment_id, list of match dicts
    results_ready = pyqtSignal(int, list)
    # Signal: segment_id, error description - so a failed search reaches the log
    # instead of looking like a TM with no matches.
    search_failed = pyqtSignal(int, str)

    def __init__(self, db_path: str, source_text: str, segment_id: int,
                 tm_ids: list = None, source_lang: str = None, target_lang: str = None,
                 fuzzy_threshold: float = 0.75, max_matches: int = 10,
                 tm_metadata: dict = None):
        super().__init__()
        self.db_path = db_path
        self.source_text = source_text
        self.segment_id = segment_id
        self.tm_ids = tm_ids
        self.source_lang = source_lang
        self.target_lang = target_lang
        self.fuzzy_threshold = fuzzy_threshold
        self.max_matches = max_matches
        self.tm_metadata = tm_metadata or {}
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        """Run TM search in background thread with thread-local SQLite connection."""
        import sqlite3
        try:
            conn = sqlite3.connect(self.db_path, timeout=15)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")

            from modules.database_manager import DatabaseManager

            # Create a lightweight DatabaseManager that uses our thread-local connection
            db = DatabaseManager.__new__(DatabaseManager)
            db.connection = conn
            db.cursor = conn.cursor()
            db.log = lambda msg: None  # Suppress logging in worker thread
            db.db_path = self.db_path

            if self._cancelled:
                conn.close()
                return

            # First try exact match (fast, hash-based)
            exact_match = db.get_exact_match(
                source=self.source_text,
                tm_ids=self.tm_ids,
                source_lang=self.source_lang,
                target_lang=self.target_lang
            )

            if exact_match:
                results = [{
                    'source': exact_match['source_text'],
                    'target': exact_match['target_text'],
                    'similarity': 1.0,
                    'match_pct': 100,
                    'tm_name': self.tm_metadata.get(exact_match['tm_id'], {}).get('name', exact_match['tm_id']),
                    'tm_id': exact_match['tm_id'],
                    # v1.10.51: preserve reverse-match flag through the worker
                    # thread so _on_tm_search_results can put it on the
                    # TranslationMatch's metadata for the renderer to read.
                    'reverse_match': exact_match.get('reverse_match', False),
                }]
                if not self._cancelled:
                    self.results_ready.emit(self.segment_id, results)
                conn.close()
                return

            if self._cancelled:
                conn.close()
                return

            # Fuzzy matches (expensive - SequenceMatcher on many candidates)
            fuzzy_matches = db.search_fuzzy_matches(
                source=self.source_text,
                tm_ids=self.tm_ids,
                threshold=self.fuzzy_threshold,
                max_results=self.max_matches,
                source_lang=self.source_lang,
                target_lang=self.target_lang
            )

            results = []
            for match in fuzzy_matches:
                results.append({
                    'source': match['source_text'],
                    'target': match['target_text'],
                    'similarity': match.get('similarity', 0.85),
                    'match_pct': match.get('match_pct', 85),
                    'tm_name': self.tm_metadata.get(match['tm_id'], {}).get('name', match['tm_id']),
                    'tm_id': match['tm_id'],
                    'reverse_match': match.get('reverse_match', False),
                })

            if not self._cancelled:
                self.results_ready.emit(self.segment_id, results)

            conn.close()

        except Exception as e:
            try:
                conn.close()
            except:
                pass
            # Never fail silently here. This except used to swallow `e` and emit
            # an empty result, which renders as "no TM matches" - visually
            # identical to a TM that genuinely has nothing to offer. Any fault in
            # the search (a locked database, a malformed FTS index, a bad
            # language value) therefore looked like an empty TM, with nothing in
            # the log to say otherwise.
            import traceback
            print(f"[TM search] FAILED for segment {self.segment_id}: "
                  f"{type(e).__name__}: {e}")
            traceback.print_exc()
            self.search_failed.emit(self.segment_id, f"{type(e).__name__}: {e}")
            if not self._cancelled:
                self.results_ready.emit(self.segment_id, [])


class PreTranslationWorker(QThread):
    """Background worker thread for batch translation."""
    
    # Signals
    progress_update = pyqtSignal(int, int, str, bool, float)  # current, total, message, success, elapsed_time
    translation_complete = pyqtSignal(int, int)  # success_count, error_count
    translation_error = pyqtSignal(str)  # error_message
    retry_needed = pyqtSignal(list)  # empty_segments list of (row_index, segment)
    
    def __init__(self, parent_app, segments, provider_type, provider_name, model, tm_exact_only=False, prompt_manager=None, retry_enabled=False, retry_pass=0, tm_ids=None, glossary_terms=None, base_url=None, custom_api_key=None, http_proxy=None, use_fuzzy_fixer=False, fuzzy_match_map=None, autotag_only=False):
        super().__init__()
        self.parent_app = parent_app
        self.segments = segments
        self.provider_type = provider_type
        self.provider_name = provider_name
        self.model = model
        self.base_url = base_url
        self.custom_api_key = custom_api_key  # Profile API key (takes priority over api_keys.txt)
        self.http_proxy = http_proxy
        self.tm_exact_only = tm_exact_only
        self.prompt_manager = prompt_manager
        # FuzzyFixer: when enabled, the LLM pass runs per-segment so each
        # segment's own fuzzy TM match (fuzzy_match_map[seg.id]) can be injected.
        self.use_fuzzy_fixer = use_fuzzy_fixer
        self.fuzzy_match_map = fuzzy_match_map or {}
        # AutoTagger: autotag_only is the standalone Bulk Operations mode — no
        # translation at all, just (re)place inline tags on the given segments.
        self.autotag_only = autotag_only
        self.retry_enabled = retry_enabled
        self.retry_pass = retry_pass
        self.max_retries = 5
        self._cancelled = False
        self.tm_ids = tm_ids  # Activated TM IDs for TM pre-translation
        self.glossary_terms = glossary_terms or []  # Pre-fetched from main thread (SQLite is not thread-safe)
        self.success_count = 0
        self.error_count = 0
        # v1.10.204: collect undo entries so the main thread can push them onto
        # the app undo stack after completion. Pre-translation can overwrite
        # 500+ targets in one shot – without this it was irreversible.
        self.undo_entries = []  # list of (segment_id, old_target, new_target, old_status, new_status)

    def run(self):
        """Main translation loop - runs in background thread."""
        import time
        import re

        # Attribute this batch worker's AI calls in the persistent usage ledger
        # (thread-local; the worker thread is short-lived). Only LLM calls log.
        try:
            from modules import usage_log
            _proj = getattr(self.parent_app, 'current_project', None)
            usage_log.set_context(
                task='BatchTranslate',
                project=getattr(_proj, 'name', None),
                src_lang=getattr(_proj, 'source_lang', None),
                tgt_lang=getattr(_proj, 'target_lang', None),
            )
        except Exception:
            pass

        # For TM provider, create thread-local database connection
        # SQLite connections can't be shared across threads
        self._thread_local_db = None
        if self.provider_type == 'TM' and hasattr(self.parent_app, 'tm_database') and self.parent_app.tm_database:
            try:
                import sqlite3
                db_path = self.parent_app.tm_database.db_path
                self._thread_local_db = sqlite3.connect(db_path, timeout=15)
                self._thread_local_db.row_factory = sqlite3.Row
                self._thread_local_db.execute("PRAGMA journal_mode=WAL")
                print(f"✓ Created thread-local database connection for TM pre-translation")
            except Exception as e:
                print(f"❌ Failed to create thread-local DB connection: {e}")
                self._thread_local_db = None
        
        try:
            # Standalone AutoTagger (Bulk Operations): just (re)tag the given
            # segments, no translation, then finish.
            if self.autotag_only:
                self._run_autotag_only()
                self.translation_complete.emit(self.success_count, self.error_count)
                return

            # For TM and MT, process segments individually
            if self.provider_type in ['TM', 'MT']:
                for idx, (row_index, segment) in enumerate(self.segments):
                    if self._cancelled:
                        break
                    
                    try:
                        start_time = time.time()
                        
                        # Get translation
                        if self.provider_type == 'TM':
                            translation = self._translate_from_tm(segment)
                        else:  # MT
                            translation = self._translate_with_mt(segment)
                        
                        elapsed = time.time() - start_time
                        
                        if translation:
                            old_target = segment.target
                            old_status = segment.status
                            segment.target = translation
                            segment.status = "draft"
                            if old_target != translation or old_status != "draft":
                                self.undo_entries.append((segment.id, old_target, translation, old_status, "draft"))

                            preview = segment.source[:50] + ("..." if len(segment.source) > 50 else "")
                            message = f"[{idx+1}/{len(self.segments)}] ✓ {preview} ({elapsed:.1f}s)"
                            self.progress_update.emit(idx + 1, len(self.segments), message, True, elapsed)
                            self.success_count += 1
                        else:
                            preview = segment.source[:50] + ("..." if len(self.segments) > 50 else "")
                            message = f"[{idx+1}/{len(self.segments)}] ⊘ No match found: {preview}"
                            self.progress_update.emit(idx + 1, len(self.segments), message, False, elapsed)
                            self.error_count += 1
                    
                    except Exception as e:
                        preview = segment.source[:50] + ("..." if len(segment.source) > 50 else "")
                        message = f"[{idx+1}/{len(self.segments)}] ✗ ERROR: {preview} - {str(e)}"
                        self.progress_update.emit(idx + 1, len(self.segments), message, False, 0)
                        self.error_count += 1
        
            # FuzzyFixer: translate each segment individually so its own fuzzy
            # TM match can be injected (batching is disabled for this pass).
            elif self.use_fuzzy_fixer:
                self._run_fuzzy_fixer_llm()

            # For LLM, process in batches
            else:
                # Get batch size from settings
                general_prefs = self.parent_app.load_general_settings()
                batch_size = general_prefs.get('batch_size', 20)
                
                # Split segments into batches
                total_batches = (len(self.segments) + batch_size - 1) // batch_size
                
                for batch_num in range(total_batches):
                    if self._cancelled:
                        break
                    
                    start_idx = batch_num * batch_size
                    end_idx = min((batch_num + 1) * batch_size, len(self.segments))
                    batch_segments = self.segments[start_idx:end_idx]
                    
                    try:
                        start_time = time.time()
                    
                        # Translate entire batch
                        batch_translations = self._translate_batch_with_llm(batch_segments)
                    
                        elapsed = time.time() - start_time
                    
                        # Process results
                        for (row_index, segment), translation in zip(batch_segments, batch_translations):
                            absolute_idx = start_idx + batch_segments.index((row_index, segment))
                            
                            if translation:
                                old_target = segment.target
                                old_status = segment.status
                                segment.target = translation
                                segment.status = "draft"
                                if old_target != translation or old_status != "draft":
                                    self.undo_entries.append((segment.id, old_target, translation, old_status, "draft"))

                                preview = segment.source[:50] + ("..." if len(segment.source) > 50 else "")
                                message = f"[{absolute_idx+1}/{len(self.segments)}] ✓ {preview}"
                                self.progress_update.emit(absolute_idx + 1, len(self.segments), message, True, elapsed / len(batch_segments))
                                self.success_count += 1
                            else:
                                preview = segment.source[:50] + ("..." if len(segment.source) > 50 else "")
                                message = f"[{absolute_idx+1}/{len(self.segments)}] ⊘ No translation: {preview}"
                                self.progress_update.emit(absolute_idx + 1, len(self.segments), message, False, elapsed / len(batch_segments))
                                self.error_count += 1
                
                    except Exception as e:
                        import traceback
                        error_details = traceback.format_exc()
                        print(f"❌ Batch processing error: {e}")
                        print(f"❌ Full traceback:\n{error_details}")
                        # Surface the real reason to the app log once per batch,
                        # not just the per-segment console lines below.
                        self.translation_error.emit(str(e))
                        # Mark entire batch as failed
                        for row_index, segment in batch_segments:
                            absolute_idx = start_idx + batch_segments.index((row_index, segment))
                            preview = segment.source[:50] + ("..." if len(segment.source) > 50 else "")
                            message = f"[{absolute_idx+1}/{len(self.segments)}] ✗ BATCH ERROR: {str(e)}"
                            self.progress_update.emit(absolute_idx + 1, len(self.segments), message, False, 0)
                            self.error_count += 1
        
            # Check if retry is needed (for LLM mode with retry option enabled)
            if self.retry_enabled and self.provider_type == 'LLM' and self.retry_pass < self.max_retries:
                # Count segments that are still empty after this pass
                empty_segments_after = []
                for row_index, seg in self.segments:
                    if not seg.target or not seg.target.strip():
                        empty_segments_after.append((row_index, seg))
                
                if empty_segments_after:
                    # Emit retry_needed signal with list of empty segments
                    self.retry_needed.emit(empty_segments_after)
                    # Still emit completion for UI updates
                    self.translation_complete.emit(self.success_count, self.error_count)
                    return
        
            # Emit completion
            self.translation_complete.emit(self.success_count, self.error_count)
        
        finally:
            # Clean up thread-local database connection
            if self._thread_local_db:
                try:
                    self._thread_local_db.close()
                    print(f"✓ Closed thread-local database connection")
                except:
                    pass
    
    def cancel(self):
        """Request cancellation of translation job."""
        self._cancelled = True
    
    def _translate_from_tm(self, segment):
        """Translate a single segment using TM.
        
        Uses thread-local database connection to avoid SQLite threading errors.
        """
        try:
            print(f"🔍 TM PRE-TRANSLATE: Searching for: '{segment.source[:50]}...'")
            print(f"🔍 TM PRE-TRANSLATE: Using tm_ids: {self.tm_ids}")
            print(f"🔍 TM PRE-TRANSLATE: Exact only: {self.tm_exact_only}")
            
            # Use thread-local database connection
            if not self._thread_local_db:
                print(f"❌ TM PRE-TRANSLATE: No thread-local database connection!")
                return None
            
            # Create a thread-local DatabaseManager for TM operations
            # This avoids SQLite threading errors
            from modules.database_manager import DatabaseManager
            
            # Get db_path from parent_app's tm_database
            db_path = self.parent_app.tm_database.db_path
            
            # Create a thread-local DatabaseManager
            thread_local_tm = DatabaseManager(db_path=db_path, log_callback=print)
            thread_local_tm.connection = self._thread_local_db
            thread_local_tm.cursor = self._thread_local_db.cursor()
            
            if self.tm_exact_only:
                # Exact matches only - use hash lookup
                match = thread_local_tm.get_exact_match(segment.source, tm_ids=self.tm_ids)
                print(f"🔍 TM PRE-TRANSLATE: Exact match result: {match}")
                if match:
                    return match.get('target_text', '')
                return None
            else:
                # Fuzzy matching enabled - use activated TM IDs
                matches = thread_local_tm.search_all(
                    segment.source, 
                    tm_ids=self.tm_ids, 
                    enabled_only=False, 
                    max_matches=1
                )
                print(f"🔍 TM PRE-TRANSLATE: Fuzzy matches: {matches}")
                if matches and len(matches) > 0:
                    match = matches[0]
                    match_pct = match.get('match_pct', 0)
                    print(f"🔍 TM PRE-TRANSLATE: Best match pct: {match_pct}")
                    if match_pct >= 75:  # Accept matches 75% and above
                        return match.get('target', '')
                return None
        except Exception as e:
            print(f"TM translation error: {e}")
            import traceback
            traceback.print_exc()
            return None
    
    def _translate_with_mt(self, segment):
        """Translate a single segment using MT."""
        # This would call parent_app's MT methods
        # Implementation depends on which MT provider is selected
        try:
            # Get source/target languages
            source_lang = getattr(self.parent_app.current_project, 'source_lang', 'en')
            target_lang = getattr(self.parent_app.current_project, 'target_lang', 'nl')
            
            # Load API keys
            api_keys = self.parent_app.load_api_keys()
            
            # Call appropriate MT service
            if self.provider_name == 'Google Translate':
                return self.parent_app.call_google_translate(segment.source, source_lang, target_lang, api_keys.get('google_translate'))
            elif self.provider_name == 'DeepL':
                return self.parent_app.call_deepl(segment.source, source_lang, target_lang, api_keys.get('deepl'))
            # Add other MT providers as needed
            return None
        except Exception as e:
            print(f"MT translation error: {e}")
            return None
    
    def _translate_with_llm(self, segment):
        """Translate a single segment using LLM."""
        try:
            from modules.llm_clients import LLMClient

            # Get source/target languages
            source_lang = getattr(self.parent_app.current_project, 'source_lang', 'en')
            target_lang = getattr(self.parent_app.current_project, 'target_lang', 'nl')

            # Load API keys
            api_keys = self.parent_app.load_api_keys()
            api_key = api_keys.get(self.provider_name) or (api_keys.get('google') if self.provider_name == 'gemini' else None)
            if self.provider_name == 'custom_openai':
                # Profile API key takes priority over api_keys.txt
                if self.custom_api_key:
                    api_key = self.custom_api_key
                elif not api_key:
                    api_key = api_keys.get('custom_openai', '') or 'not-needed'

            # Build custom prompt from prompt library
            custom_prompt = None
            if self.prompt_manager:
                try:
                    # Use pre-fetched glossary terms (SQLite is not thread-safe)
                    full_prompt = self.prompt_manager.build_final_prompt(
                        source_text=segment.source,
                        source_lang=source_lang,
                        target_lang=target_lang,
                        mode="single",
                        glossary_terms=self.glossary_terms,
                        target_text=segment.target or ""
                    )
                    # Extract just the instruction part (without the source text section)
                    if "**SOURCE TEXT:**" in full_prompt:
                        custom_prompt = full_prompt.split("**SOURCE TEXT:**")[0].strip()
                    elif "Translate the following" in full_prompt:
                        custom_prompt = full_prompt.split("Translate the following")[0].strip()
                    else:
                        custom_prompt = full_prompt
                except Exception as e:
                    print(f"Warning: Could not build custom prompt: {e}")
                    custom_prompt = None

            # Create client
            client = LLMClient(
                api_key=api_key,
                provider=self.provider_name,
                model=self.model,
                base_url=self.base_url,
                http_proxy=self.http_proxy
            )

            # Translate with custom prompt
            result = client.translate(
                text=segment.source,
                source_lang=source_lang,
                target_lang=target_lang,
                custom_prompt=custom_prompt
            )
            
            return result
        except Exception as e:
            print(f"LLM translation error: {e}")
            return None
    
    def _translate_batch_with_llm(self, batch_segments):
        """Translate multiple segments in one LLM call (batch mode)."""
        try:
            from modules.llm_clients import LLMClient
            import re
            import traceback
            
            print(f"🚀 _translate_batch_with_llm: Starting batch of {len(batch_segments)} segments")

            # Get source/target languages
            source_lang = getattr(self.parent_app.current_project, 'source_lang', 'en')
            target_lang = getattr(self.parent_app.current_project, 'target_lang', 'nl')
            print(f"🚀 Languages: {source_lang} → {target_lang}")

            # Load API keys
            api_keys = self.parent_app.load_api_keys()
            api_key = api_keys.get(self.provider_name) or (api_keys.get('google') if self.provider_name == 'gemini' else None)
            if self.provider_name == 'custom_openai':
                # Profile API key takes priority over api_keys.txt
                if self.custom_api_key:
                    api_key = self.custom_api_key
                elif not api_key:
                    api_key = api_keys.get('custom_openai', '') or 'not-needed'

            # Build batch prompt – split into a static cacheable system prompt
            # (base instructions, glossary, project context) and a per-batch
            # user prompt (segment list). Anthropic prompt caching marks the
            # system prompt as ephemeral so batches 2..N hit the 0.1× cache-read
            # rate instead of the full input rate; OpenRouter→Anthropic gets
            # the same treatment via OpenAI's content-array form. Other
            # providers ignore the marker.

            # Get base prompt from prompt library – becomes the system prompt
            # so that it's byte-stable across batches in this run and cacheable.
            base_prompt = None
            if self.prompt_manager and batch_segments:
                try:
                    first_segment = batch_segments[0][1]
                    # Use pre-fetched glossary terms (SQLite is not thread-safe)
                    full_prompt = self.prompt_manager.build_final_prompt(
                        source_text=first_segment.source,
                        source_lang=source_lang,
                        target_lang=target_lang,
                        mode="single",
                        glossary_terms=self.glossary_terms,
                        target_text=first_segment.target or ""
                    )
                    # Extract just the instruction part
                    if "**SOURCE TEXT:**" in full_prompt:
                        base_prompt = full_prompt.split("**SOURCE TEXT:**")[0].strip()
                    elif "Translate the following" in full_prompt:
                        base_prompt = full_prompt.split("Translate the following")[0].strip()
                    else:
                        base_prompt = full_prompt
                except Exception:
                    base_prompt = None

            # User-message portion: critical instructions + the actual segments.
            # This is per-batch and intentionally NOT cached.
            batch_prompt_parts = []
            if not base_prompt:
                # Fallback when no prompt manager is available – include the
                # minimal language pair instruction in the user prompt itself.
                batch_prompt_parts.append(f"Translate the following text segments from {source_lang} to {target_lang}.")

            batch_prompt_parts.append(f"**SEGMENTS TO TRANSLATE ({len(batch_segments)} segments):**")
            batch_prompt_parts.append("\n⚠️ CRITICAL INSTRUCTIONS:")
            batch_prompt_parts.append(f"1. You must provide EXACTLY one translation per segment")
            batch_prompt_parts.append(f"2. You MUST translate ALL {len(batch_segments)} segments")
            batch_prompt_parts.append("3. Format: Each translation MUST start with its segment number, a period, then the translation")
            batch_prompt_parts.append("4. Line breaks: If the source segment contains line breaks, preserve them in your translation.")
            batch_prompt_parts.append("   The number label (e.g. '40.') appears only ONCE at the start; continuation lines have no number.")
            # Inline-tag preservation rule – only added when the batch actually
            # contains numbered <N> markers (SDLXLIFF/Trados inline tags), so
            # plain-text batches don't carry irrelevant tag instructions. Added
            # after reports that some models (Mistral Large) began emitting
            # empty pairs (<1></1>) or otherwise mangling the markers.
            _next_rule = 5
            _has_inline_tags = any(
                re.search(r'</?\d+/?>', seg.source or '') for _, seg in batch_segments
            )
            if _has_inline_tags:
                batch_prompt_parts.append(
                    f"{_next_rule}. Inline tags: some segments contain numbered tags like "
                    "<1>…</1> (paired) or <2/> (standalone). Keep EVERY tag, with the same "
                    "numbers, exactly as written – angle brackets, digits, no spaces. A paired "
                    "tag MUST wrap the translated word(s) it wrapped in the source; NEVER output "
                    "an empty pair like <1></1> with the text left outside it. Tags may be "
                    "reordered to fit natural target-language word order.")
                _next_rule += 1
            # The user's own inline codes (Settings → Inline Codes, issue #194)
            _codes_note = _inline_codes.prompt_note([seg.source or '' for _, seg in batch_segments])
            if _codes_note:
                batch_prompt_parts.append(f"{_next_rule}. {_codes_note}")
                _next_rule += 1
            batch_prompt_parts.append(f"{_next_rule}. NO explanations, NO commentary, ONLY the numbered translations\n")

            batch_prompt_parts.append("**SEGMENTS TO TRANSLATE:**\n")

            # Add all segments
            for row_index, seg in batch_segments:
                batch_prompt_parts.append(f"{seg.id}. {seg.source}")

            batch_prompt_parts.append("\n**YOUR TRANSLATIONS (numbered list):**")
            batch_prompt_parts.append("Begin your translations now:")

            batch_prompt = "\n".join(batch_prompt_parts)

            # Create client
            client = LLMClient(
                api_key=api_key,
                provider=self.provider_name,
                model=self.model,
                base_url=self.base_url,
                http_proxy=self.http_proxy
            )

            # Call LLM. enable_prompt_caching: the system prompt
            # (base_prompt) is byte-stable across every batch in this run, so
            # caching pays off from batch 2 onwards. Anthropic native +
            # OpenRouter→Anthropic add explicit cache_control markers;
            # OpenAI / DeepSeek / Gemini 2.5+ benefit from automatic implicit
            # caching at the provider layer; other providers ignore the flag.
            result = client.translate(
                text=batch_prompt,
                source_lang=source_lang,
                target_lang=target_lang,
                # custom_prompt=batch_prompt prevents translate() from wrapping
                # the user message with its own "Translate the following…" prefix.
                custom_prompt=batch_prompt,
                system_prompt=base_prompt,
                enable_prompt_caching=True,
            )
            
            # Parse the numbered response
            translations = []
            if result:
                lines = result.split('\n')
                translation_map = {}
                current_id = None

                for line in lines:
                    # Match "123. Translation text"
                    match = re.match(r'^(\d+)\.\s*(.*)', line)
                    if match:
                        current_id = int(match.group(1))
                        translation = match.group(2)
                        translation_map[current_id] = translation
                    elif current_id is not None:
                        # Continuation line for a multi-line translation
                        translation_map[current_id] += '\n' + line

                # Drop spurious trailing newlines: models often separate the
                # numbered entries with a blank line, which the continuation
                # branch above appends to the preceding translation as a
                # trailing '\n' (shown as ↵ on nearly every segment). Strip
                # trailing newline(s) while keeping genuine internal line breaks
                # and any meaningful trailing space on the content line.
                for _sid in translation_map:
                    translation_map[_sid] = re.sub(r'\n\s*$', '', translation_map[_sid])

                # Extract translations in order, repairing drifted numbered
                # tags (< 1>, &lt;1&gt; …) against each segment's source (#226)
                from modules.tag_repair import repair_drifted_tags
                for row_index, seg in batch_segments:
                    translation = translation_map.get(seg.id, None)
                    if translation:
                        translation = repair_drifted_tags(seg.source, translation)
                    translations.append(translation)
            else:
                # No result - all segments failed
                translations = [None] * len(batch_segments)
            
            return translations
            
        except Exception as e:
            import traceback
            error_details = traceback.format_exc()
            print(f"❌ Batch LLM translation error: {e}")
            print(f"❌ Full traceback:\n{error_details}")
            # Re-raise so the caller surfaces the actual reason (bad/unavailable
            # model id, no project access, invalid params, network, …) in the
            # progress console and the log — instead of silently reporting
            # "No translation" for every segment with no clue why. (e.g. a 403
            # "project does not have access to model X" was being hidden.)
            raise

    def _run_fuzzy_fixer_llm(self):
        """LLM pass that translates each segment individually so its own fuzzy
        TM match (from fuzzy_match_map) can be injected (FuzzyFixer batch mode)."""
        import time
        fmap = self.fuzzy_match_map or {}
        total = len(self.segments)
        for idx, (row_index, segment) in enumerate(self.segments):
            if self._cancelled:
                break
            try:
                start_time = time.time()
                fuzzy_match = fmap.get(segment.id)
                from modules import usage_log as _usage_log
                with _usage_log.UsageContext(task="FuzzyFixer"):
                    translation = self._translate_single_with_llm(segment, fuzzy_match)
                elapsed = time.time() - start_time

                if translation:
                    old_target = segment.target
                    old_status = segment.status
                    segment.target = translation
                    segment.status = "draft"
                    if old_target != translation or old_status != "draft":
                        self.undo_entries.append((segment.id, old_target, translation, old_status, "draft"))
                    tag = "🔧" if fuzzy_match else "✓"
                    preview = segment.source[:50] + ("..." if len(segment.source) > 50 else "")
                    message = f"[{idx+1}/{total}] {tag} {preview} ({elapsed:.1f}s)"
                    self.progress_update.emit(idx + 1, total, message, True, elapsed)
                    self.success_count += 1
                else:
                    preview = segment.source[:50] + ("..." if len(segment.source) > 50 else "")
                    message = f"[{idx+1}/{total}] ⊘ No translation: {preview}"
                    self.progress_update.emit(idx + 1, total, message, False, elapsed)
                    self.error_count += 1
            except Exception as e:
                preview = segment.source[:50] + ("..." if len(segment.source) > 50 else "")
                message = f"[{idx+1}/{total}] ✗ ERROR: {preview} - {str(e)}"
                self.progress_update.emit(idx + 1, total, message, False, 0)
                self.error_count += 1

    def _translate_single_with_llm(self, segment, fuzzy_match=None):
        """Translate ONE segment with the LLM, optionally injecting a fuzzy TM
        match (FuzzyFixer). Returns the translation string or None."""
        try:
            from modules.llm_clients import LLMClient

            source_lang = getattr(self.parent_app.current_project, 'source_lang', 'en')
            target_lang = getattr(self.parent_app.current_project, 'target_lang', 'nl')

            api_keys = self.parent_app.load_api_keys()
            api_key = api_keys.get(self.provider_name) or (api_keys.get('google') if self.provider_name == 'gemini' else None)
            if self.provider_name == 'custom_openai':
                if self.custom_api_key:
                    api_key = self.custom_api_key
                elif not api_key:
                    api_key = api_keys.get('custom_openai', '') or 'not-needed'

            custom_prompt = None
            if self.prompt_manager:
                custom_prompt = self.prompt_manager.build_final_prompt(
                    source_text=segment.source,
                    source_lang=source_lang,
                    target_lang=target_lang,
                    mode="single",
                    glossary_terms=self.glossary_terms,
                    target_text=segment.target or "",
                    fuzzy_match=fuzzy_match,
                )

            client = LLMClient(
                api_key=api_key,
                provider=self.provider_name,
                model=self.model,
                base_url=self.base_url,
                http_proxy=self.http_proxy,
            )
            result = client.translate(
                text=segment.source,
                source_lang=source_lang,
                target_lang=target_lang,
                custom_prompt=custom_prompt,
            )
            if not result:
                return None
            from modules.tag_repair import repair_drifted_tags
            return repair_drifted_tags(segment.source, result.strip())
        except Exception as e:
            print(f"❌ FuzzyFixer single LLM error: {e}")
            return None

    def _autotag_segment_llm(self, segment):
        """Place the source's inline tags into segment.target via the AI.
        Returns the new tagged target (str) on success, or None to leave it
        unchanged. Strips any existing target tags and re-places from source."""
        try:
            from modules.llm_clients import LLMClient
            if not autotag_extract_tags(segment.source):
                return None
            clean_target = strip_all_tags(segment.target or "")
            if not clean_target.strip():
                return None
            source_lang = getattr(self.parent_app.current_project, 'source_lang', 'en')
            target_lang = getattr(self.parent_app.current_project, 'target_lang', 'nl')
            api_keys = self.parent_app.load_api_keys()
            api_key = api_keys.get(self.provider_name) or (api_keys.get('google') if self.provider_name == 'gemini' else None)
            if self.provider_name == 'custom_openai':
                if self.custom_api_key:
                    api_key = self.custom_api_key
                elif not api_key:
                    api_key = api_keys.get('custom_openai', '') or 'not-needed'
            client = LLMClient(
                api_key=api_key, provider=self.provider_name, model=self.model,
                base_url=self.base_url, http_proxy=self.http_proxy,
            )
            new_target, reason = place_tags_via_llm(
                client, self.prompt_manager, segment.source, clean_target, source_lang, target_lang)
            if not new_target:
                print(f"🏷️ AutoTagger skipped #{segment.id}: {reason}")
            return new_target
        except Exception as e:
            print(f"❌ AutoTagger error on #{segment.id}: {e}")
            return None

    def _run_autotag_only(self):
        """Standalone AutoTagger pass (Bulk Operations): (re)place tags on already
        translated segments; no translation is performed."""
        import time
        total = len(self.segments)
        for idx, (row_index, segment) in enumerate(self.segments):
            if self._cancelled:
                break
            try:
                start_time = time.time()
                if not autotag_extract_tags(segment.source) or not (segment.target or "").strip():
                    preview = segment.source[:50] + ("..." if len(segment.source) > 50 else "")
                    self.progress_update.emit(idx + 1, total, f"[{idx+1}/{total}] ⊘ no tags / no target: {preview}", False, time.time() - start_time)
                    continue
                new_target = self._autotag_segment_llm(segment)
                elapsed = time.time() - start_time
                preview = segment.source[:50] + ("..." if len(segment.source) > 50 else "")
                if new_target and new_target != segment.target:
                    old_target = segment.target
                    old_status = segment.status
                    segment.target = new_target
                    self.undo_entries.append((segment.id, old_target, new_target, old_status, old_status))
                    self.progress_update.emit(idx + 1, total, f"[{idx+1}/{total}] 🏷️ {preview} ({elapsed:.1f}s)", True, elapsed)
                    self.success_count += 1
                elif new_target == segment.target:
                    self.progress_update.emit(idx + 1, total, f"[{idx+1}/{total}] = tags already correct: {preview}", True, elapsed)
                    self.success_count += 1
                else:
                    self.progress_update.emit(idx + 1, total, f"[{idx+1}/{total}] ⚠ could not tag: {preview}", False, elapsed)
                    self.error_count += 1
            except Exception as e:
                preview = segment.source[:50] + ("..." if len(segment.source) > 50 else "")
                self.progress_update.emit(idx + 1, total, f"[{idx+1}/{total}] ✗ ERROR: {preview} - {e}", False, 0)
                self.error_count += 1


class ProofreadWorker(QThread):
    """Background worker thread for proofreading translations."""

    # Signals
    progress_update = pyqtSignal(int, str)  # checked_count, status_message
    stats_update = pyqtSignal(int, int, int)  # checked, issues, ok
    batch_error = pyqtSignal(int, int, str)  # batch_start, batch_end, error_message
    segment_issue = pyqtSignal(int, str, str)  # row_idx, issue_text, model_name
    finished_proofreading = pyqtSignal(int, int, int)  # checked, issues, ok

    def __init__(self, segments_to_check, provider, model, custom_prompt, api_keys, source_lang, target_lang, base_url=None, custom_api_key=None, http_proxy=None):
        super().__init__()
        self.segments_to_check = segments_to_check
        self.provider = provider
        self.model = model
        self.custom_prompt = custom_prompt
        self.api_keys = api_keys
        self.source_lang = source_lang
        self.target_lang = target_lang
        self.base_url = base_url
        self.custom_api_key = custom_api_key
        self.http_proxy = http_proxy
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        """Main proofreading loop - runs in background thread."""
        import re

        # Build prompt if not custom
        if not self.custom_prompt:
            lang_specific = ""
            if self.target_lang.lower() in ['dutch', 'nl', 'nl-nl', 'nl-be', 'nederlands']:
                lang_specific = """
5. Dutch Compound Words – Verify correct spelling of compound words (e.g., "persoonsgegevens" NOT "persoongegevens", "bedrijfsnaam" NOT "bedrijfnaam"). Pay special attention to connecting letters like 's', 'e', 'en'.
6. Dutch Spelling – Check for common Dutch spelling errors including de/het articles, dt-errors, and capitalization."""
            elif self.target_lang.lower() in ['german', 'de', 'de-de', 'deutsch']:
                lang_specific = """
5. German Compound Words – Verify correct compound noun formation and capitalization of all nouns.
6. German Cases – Check correct use of cases (Nominativ, Akkusativ, Dativ, Genitiv)."""
            elif self.target_lang.lower() in ['french', 'fr', 'fr-fr', 'français']:
                lang_specific = """
5. French Accents – Verify correct use of accents (é, è, ê, à, ù, ô, etc.).
6. French Agreement – Check gender/number agreement between nouns, adjectives, and articles."""

            self.custom_prompt = f"""You are a translation proofreader. Your task is to analyze translations for errors.

DO NOT translate anything. DO NOT provide corrected translations unless specifically requested.
ONLY identify errors using the exact format specified below.

Task: Proofread this translation from {self.source_lang} to {self.target_lang}.

For each segment, verify:
1. Accuracy – Does the translation correctly convey the source meaning?
2. Completeness – Is anything missing or added?
3. Terminology – Are technical terms translated correctly and consistently?
4. Grammar & Style – Is the text natural and error-free?{lang_specific}

CRITICAL OUTPUT FORMAT (FOLLOW EXACTLY):
- If segment is OK: [SEGMENT XXXX] ✓
- If segment has issues: [SEGMENT XXXX] ⚠
  Issue: <brief description>
  Suggestion: <recommended fix>

OUTPUT ONLY THE SEGMENT MARKERS. DO NOT ADD EXPLANATIONS BEFORE OR AFTER."""

        # Initialize LLM client
        try:
            from modules.llm_clients import LLMClient

            if self.provider == 'openai':
                api_key = self.api_keys.get('openai', '')
            elif self.provider == 'claude':
                api_key = self.api_keys.get('claude', '')
            elif self.provider == 'gemini':
                api_key = self.api_keys.get('gemini', '')
            elif self.provider == 'mistral':
                api_key = self.api_keys.get('mistral', '')
            elif self.provider == 'deepseek':
                api_key = self.api_keys.get('deepseek', '')
            elif self.provider == 'ollama':
                api_key = self.api_keys.get('ollama', '') or 'not-needed'
            elif self.provider == 'custom_openai':
                api_key = self.custom_api_key or self.api_keys.get('custom_openai', '') or 'not-needed'
            else:
                api_key = ''

            llm_client = LLMClient(api_key=api_key, provider=self.provider, model=self.model, base_url=self.base_url, http_proxy=self.http_proxy)
        except Exception as e:
            self.batch_error.emit(0, 0, f"Failed to initialize LLM client:\n{str(e)}")
            self.finished_proofreading.emit(0, 0, 0)
            return

        # Process in batches of 20 segments
        batch_size = 20
        checked_count = 0
        issues_count = 0
        ok_count = 0

        for batch_start in range(0, len(self.segments_to_check), batch_size):
            if self._cancelled:
                break

            batch_end = min(batch_start + batch_size, len(self.segments_to_check))
            batch = self.segments_to_check[batch_start:batch_end]

            # Format batch for API
            batch_text = ""
            for row_idx, segment in batch:
                segment_num = f"{row_idx + 1:04d}"
                batch_text += f"[SEGMENT {segment_num}]\n{self.source_lang}: {segment.source}\n{self.target_lang}: {segment.target}\n\n"

            self.progress_update.emit(checked_count, f"Proofreading segments {batch_start + 1}-{batch_end}...")

            # Send to LLM
            try:
                full_prompt = f"{self.custom_prompt}\n\n{batch_text}"

                response = llm_client.translate(
                    text="",
                    source_lang=self.source_lang,
                    target_lang=self.target_lang,
                    custom_prompt=full_prompt
                )

                if self._cancelled:
                    break

                # Parse response
                for row_idx, segment in batch:
                    if self._cancelled:
                        break

                    segment_num = f"{row_idx + 1:04d}"

                    pattern_ok = f"\\[SEGMENT {segment_num}\\]\\s*✓"
                    pattern_issue = f"\\[SEGMENT {segment_num}\\]\\s*⚠"

                    if re.search(pattern_ok, response, re.IGNORECASE):
                        ok_count += 1
                    elif re.search(pattern_issue, response, re.IGNORECASE):
                        issue_pattern = f"\\[SEGMENT {segment_num}\\]\\s*⚠\\s*\\n(.+?)(?=\\[SEGMENT|$)"
                        issue_match = re.search(issue_pattern, response, re.DOTALL)

                        if issue_match:
                            issue_text = issue_match.group(1).strip()
                            self.segment_issue.emit(row_idx, issue_text, self.model)
                            issues_count += 1

                    checked_count += 1
                    self.stats_update.emit(checked_count, issues_count, ok_count)

            except Exception as e:
                self.batch_error.emit(batch_start, batch_end, str(e))

        self.finished_proofreading.emit(checked_count, issues_count, ok_count)


class GlossaryExtractionWorker(QThread):
    """Run an LLM glossary-extraction call off the UI thread.

    Replaces the old mechanical TermExtractor (retired v1.10.357): sends the
    project's source text to the configured LLM and returns bilingual
    source→target term pairs. Emits finished_ok(list_of_dicts) or
    failed(message, raw_response).
    """

    finished_ok = pyqtSignal(list)
    failed = pyqtSignal(str, str)

    def __init__(self, client, prompt: str):
        super().__init__()
        self.client = client
        self.prompt = prompt

    def run(self):
        try:
            raw = self.client.translate(
                text="",  # unused: custom_prompt replaces the whole prompt
                custom_prompt=self.prompt,
                skip_cleaning=True,  # response is JSON, not a translation
            )
            pairs = self._parse_pairs(raw)
            if not pairs:
                self.failed.emit("The AI returned no usable term pairs.", raw or "")
                return
            self.finished_ok.emit(pairs)
        except Exception as e:
            self.failed.emit(str(e), "")

    @staticmethod
    def _parse_pairs(raw: str) -> list:
        """Extract [{'source':…, 'target':…, 'note':…}] from the response.

        Tolerates markdown fences and prose around the JSON array — models
        often add both no matter how firmly the prompt forbids them.
        """
        import json
        import re as _re

        if not raw:
            return []
        text = raw.strip()
        # Strip ```json … ``` fences if present
        fence = _re.search(r'```(?:json)?\s*(.*?)```', text, _re.DOTALL)
        if fence:
            text = fence.group(1).strip()
        # Fall back to the outermost [...] span
        if not text.startswith('['):
            start, end = text.find('['), text.rfind(']')
            if start == -1 or end <= start:
                return []
            text = text[start:end + 1]
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return []
        if not isinstance(data, list):
            return []
        pairs = []
        for item in data:
            if not isinstance(item, dict):
                continue
            source = str(item.get('source', '') or '').strip()
            if not source:
                continue
            pairs.append({
                'source': source,
                'target': str(item.get('target', '') or '').strip(),
                'note': str(item.get('note', '') or '').strip(),
            })
        return pairs
