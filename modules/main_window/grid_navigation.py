"""Moving through the grid and confirming: selecting a cell and what that
looks up, going to the next or previous segment, confirming and
auto-propagating, and saving a segment from the grid.

Part of the main window (SupervertalerQt), moved here unchanged. The
methods run as the main window's own: `self` is the SupervertalerQt.
"""

import json
import traceback
from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtGui import QBrush, QColor, QTextCursor
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QMessageBox,
    QTableWidget,
    QTableWidgetItem,
)
from contextlib import contextmanager
from modules.grid_editors import ClickableHighlightLabel, EditableGridTextEditor
from modules.shortcut_display import format_shortcut_for_display
from modules.statuses import get_status
from modules.tag_text import expand_compact_tags, strip_outer_wrapping_tags
from typing import Dict, Optional


def format_move_steps(steps, total_ms: float) -> str:
    """One log line saying where a slow move's time went.

    `steps` is [(label, ms), ...] in the order they ran. Steps of 1 ms or
    more are named, slowest first; a label that ran twice is added up.
    Everything else (the steps under 1 ms, and whatever ran after the last
    one) is the "rest", so the figures always add up to the total.
    """
    by_label = {}
    for label, ms in steps:
        by_label[label] = by_label.get(label, 0.0) + ms
    named = sorted(((ms, label) for label, ms in by_label.items() if ms >= 1.0), reverse=True)
    parts = [f"{label} {ms:.1f}" for ms, label in named]
    rest = total_ms - sum(ms for ms, _ in named)
    parts.append(f"rest {max(rest, 0.0):.1f}")
    return ", ".join(parts)


class GridNavigationMixin:
    def get_status_icon(self, status: str) -> str:
        """Get status icon for display"""
        return get_status(status).icon

    def _make_source_changed_handler(self, segment_id, editor_widget):
        """v1.10.229: Factory for the source-cell textChanged handler.

        Companion to ``make_target_changed_handler`` (inlined in
        ``load_segments_to_grid``), but for the source column. Only fires
        when the global ``allow_replace_in_source`` setting AND the editor's
        own ``allow_source_edit`` flag are both on — so when the setting is
        off, this handler is a cheap no-op even though it's always connected.

        Limitations (intentional for v1):
        - No undo recording (the global undo stack is target-centric). The
          warning banner already says "use with extreme caution".
        - No automatic TM/MT/termbase re-lookup. Existing matches may be
          stale until the user navigates away and back.
        """
        def on_source_text_changed():
            # Cheap guards first — fire-on-every-keystroke path.
            # We gate ONLY on the widget's own allow_source_edit flag (not
            # the global self.allow_replace_in_source) so that per-cell
            # ephemeral F2 edits — which don't flip the global setting —
            # still write back to segment.source. The widget flag is set
            # by both paths: set_source_editable (global) and
            # _enable_ephemeral_source_edit (F2 per-cell).
            if not getattr(editor_widget, 'allow_source_edit', False):
                return
            if getattr(self, '_suppress_source_change_handlers', False):
                return

            new_text = editor_widget.toPlainText()

            # Reverse display-only transforms before saving (mirrors the
            # target-cell handler): compact tags → expanded, invisible
            # markers stripped, outer wrapping tag re-attached.
            compact_map = getattr(editor_widget, '_compact_tag_map', None)
            if compact_map:
                new_text = expand_compact_tags(new_text, compact_map)
            new_text = self.reverse_invisible_replacements(new_text)
            stripped_tag = getattr(editor_widget, '_stripped_outer_tag', None)
            if stripped_tag and self.hide_outer_wrapping_tags:
                if not new_text.strip().startswith(f'<{stripped_tag}'):
                    new_text = f'<{stripped_tag}>{new_text}</{stripped_tag}>'

            # Find the segment (by ID, not row — row indices shift under
            # filters / sorts).
            target_segment = next(
                (seg for seg in self.current_project.segments if seg.id == segment_id),
                None,
            )
            if target_segment is None:
                return

            # Idempotent guard — programmatic setPlainText() (e.g. grid
            # refresh, theme change) round-trips through textChanged with
            # the same text. Bail before marking the project dirty.
            if new_text == target_segment.source:
                return

            target_segment.source = new_text
            self.project_modified = True

        return on_source_text_changed

    def _handle_target_text_debounced_by_id(self, segment_id, new_text):
        """
        Handle expensive target text change operations after user stops typing.
        Called 500ms after last keystroke to avoid UI lag.
        Uses segment ID to find the correct segment (not row index).
        """
        try:
            # Find segment by ID
            segment = next((seg for seg in self.current_project.segments if seg.id == segment_id), None)
            if not segment:
                return
            
            # Find current row for this segment
            row = next((i for i, seg in enumerate(self.current_project.segments) if seg.id == segment_id), None)
            if row is None:
                return
            
            # Note: Status is now set to "not_started" immediately on edit (in on_target_text_changed)
            # User must manually confirm/approve to change status to translated/confirmed
            
            # Update window title
            self.update_window_title()
            
            # Auto-resize only this row (fast, single-row operation)
            self._auto_resize_single_row(row)
            
            # Save to TM ONLY if segment is confirmed (user explicitly approved)
            # Do NOT save for 'draft' status - that's just machine/batch translated
            if segment.status == 'confirmed' and new_text.strip():
                try:
                    self.save_segment_to_activated_tms(segment.source, new_text)
                except Exception as e:
                    self.log(f"Warning: Could not save to TM: {e}")
            
            # Update status bar progress stats (debounced - only after user stops typing)
            self.update_progress_stats()
            
            # 🚀 IDLE PREFETCH: User stopped typing, prefetch next segments for instant Ctrl+Enter
            self._trigger_idle_prefetch(row)
            
        except Exception as e:
            self.log(f"Error in debounced target handler: {e}")

    def update_status_icon(self, row: int, status: str):
        """Update status icon for a specific row"""
        if not self.current_project or row >= len(self.current_project.segments):
            return
        segment = self.current_project.segments[row]
        old_status = segment.status
        old_target = segment.target
        segment.status = status
        
        # Record undo state for status change
        self.record_undo_state(segment.id, old_target, segment.target, old_status, status)
        
        self._refresh_segment_status(segment)
        
        # Save to TM ONLY if status changed to confirmed (user explicitly approved)
        # Do NOT save for 'draft' or 'approved' status - user must confirm first
        find_replace_active = getattr(self, 'find_replace_active', False)
        if not find_replace_active and status == 'confirmed' and segment.target.strip():
            try:
                self.save_segment_to_activated_tms(segment.source, segment.target)
            except Exception as e:
                self.log(f"Warning: Could not save to TM: {e}")

    def on_cell_changed(self, item: QTableWidgetItem):
        """Handle cell content changes - now mainly for placeholder items"""
        # Target cell changes are handled by the EditableGridTextEditor's textChanged signal
        # This method is kept for compatibility but should rarely be called now
        # Placeholder items (columns 2 and 3) are non-interactive, so this shouldn't trigger
        pass

    def on_cell_selected(self, current_row, current_col, previous_row, previous_col):
        """Handle cell selection change"""
        if self.debug_mode_enabled:
            self.log(f"🎯 on_cell_selected called: row {current_row}, col {current_col}")

        # If this activation came from the keyboard (arrow keys / Ctrl+Enter),
        # cancel any pending click-triggered recenter and re-enable immediate
        # centering — the deferred-center machinery is only for mouse clicks
        # (issue #240). These flags are set by the key handlers just before
        # navigation triggers this signal, and are reset to False further down.
        if getattr(self, '_arrow_key_navigation', False) or getattr(self, '_ctrl_enter_navigation', False):
            self._cancel_pending_click_center()

        # ⚡ INSTANT exact-TM lookup — runs BEFORE the same-row guard so a 100%
        # match is (re)asserted whenever you're on a segment, even when returning
        # to the same row after an edit. The guard below only skips the HEAVY
        # (fuzzy/MT/LLM/termbase) work; TM display must never be skipped. This is
        # synchronous and independent of the cache, the termbase and filtering —
        # one indexed source_hash query, idempotent. The cure for "landed on a
        # segment that's definitely in the TM and saw no match, no matter how
        # long I waited".
        _instant_seg = None
        _instant_shown = False
        try:
            if self.current_project and 0 <= current_row < self.table.rowCount():
                _id_item = self.table.item(current_row, 0)
                if _id_item:
                    _instant_seg = next((s for s in self.current_project.segments
                                         if s.id == int(_id_item.text())), None)
                    if _instant_seg:
                        with self._pcs("instant TM match (before the move shows)"):
                            _instant_shown = self._show_instant_tm_match(_instant_seg)
        except Exception:
            pass

        # 🚫 GUARD: Don't re-run the HEAVY (fuzzy/MT/LLM/termbase) lookups if we're
        # staying on the same row — the instant TM match above has already run.
        if hasattr(self, '_last_selected_row') and self._last_selected_row == current_row:
            if self.debug_mode_enabled:
                self.log(f"⏭️ Skipping heavy lookup - already on row {current_row}")
            return
        self._last_selected_row = current_row

        # Row actually changed: if the new segment has NO exact (100%) match,
        # clear the panel *now* so the previous segment's match can't linger
        # while the debounced fuzzy/MT/LLM lookup catches up. (Before this, a
        # segment with no exact match kept showing the last segment's match —
        # e.g. confirming through several no-match segments left a stale 100%
        # from two segments back. Fuzzy matches still stream in afterwards.)
        if _instant_seg is not None and not _instant_shown:
            try:
                self.set_compare_panel_matches(_instant_seg.id, _instant_seg.source, [], [])
            except Exception:
                pass

        # ⚡ FAST PATH: Defer heavy lookups for ALL navigation (arrow keys, Ctrl+Enter, AND mouse clicks)
        # This makes segment navigation feel INSTANT - cursor moves first, lookups happen after
        # Reset any navigation flags
        self._arrow_key_navigation = False
        self._ctrl_enter_navigation = False

        # In filtered mode, use a longer debounce so rapid navigation stays snappy
        # but settling on a segment still triggers TM/MT/LLM lookups
        is_filtering = getattr(self, 'filtering_active', False)
        debounce_ms = 250 if is_filtering else 10

        # Schedule deferred lookup with short delay - debounce prevents hammering during rapid navigation
        if hasattr(self, '_deferred_lookup_timer') and self._deferred_lookup_timer:
            self._deferred_lookup_timer.stop()
        from PyQt6.QtCore import QTimer
        self._deferred_lookup_timer = QTimer()
        self._deferred_lookup_timer.setSingleShot(True)
        self._deferred_lookup_timer.timeout.connect(
            lambda r=current_row, c=current_col, pr=previous_row, pc=previous_col:
            self._on_cell_selected_full(r, c, pr, pc)
        )
        self._deferred_lookup_timer.start(debounce_ms)

        # Do minimal UI update immediately (orange highlight, scroll)
        with self._pcs("highlight and scroll (before the move shows)"):
            self._on_cell_selected_minimal(current_row, previous_row)

        # In filtered mode, also update glossary highlights from cache (cheap)
        if is_filtering:
            self._on_cell_selected_glossary_only(current_row)

    def _center_row_in_viewport(self, row: int):
        """Center the given row vertically in the visible table viewport.

        Uses Qt's built-in scrollTo() with PositionAtCenter hint.
        """
        if row < 0 or row >= self.table.rowCount():
            return

        # Get the model index for any cell in this row (use column 0)
        index = self.table.model().index(row, 0)
        if index.isValid():
            # Use Qt's built-in centering - PositionAtCenter puts the item in the center of the viewport
            self.table.scrollTo(index, QAbstractItemView.ScrollHint.PositionAtCenter)

    # ------------------------------------------------------------------
    # Deferred, double-click-cancellable auto-centering (issue #240)
    #
    # When "Keep active segment centered" is on, activating a segment by
    # *clicking* into it used to recenter the grid immediately, inside the
    # first press of the click. For a double-click that scroll happened
    # *between* the two clicks, moving the text out from under the cursor so
    # the second click landed on the wrong word (or none). Trados/memoQ only
    # recenter on a plain single click and never on a double-click.
    #
    # So a mouse click no longer centers immediately: it sets
    # ``_click_activation`` (which suppresses the immediate centers in the
    # minimal/full selection paths) and schedules the recenter for one
    # double-click interval later. A double-click that arrives in that window
    # cancels the pending recenter, leaving the view still. Keyboard
    # navigation is unaffected and still centers immediately.
    # ------------------------------------------------------------------
    def _schedule_click_center(self, row: int):
        """Schedule a recenter of ``row`` one double-click interval from now,
        cancellable by a following double-click. Only used for plain mouse
        clicks when auto-centering is enabled."""
        from PyQt6.QtCore import QTimer
        from PyQt6.QtWidgets import QApplication
        existing = getattr(self, '_pending_center_timer', None)
        if existing is not None:
            try:
                existing.stop()
            except Exception:
                pass
        # Mark the in-flight activation as a click so the immediate centers in
        # _on_cell_selected_minimal / _full are skipped for it.
        self._click_activation = True
        self._click_center_cancelled = False
        timer = QTimer()
        timer.setSingleShot(True)
        timer.timeout.connect(lambda r=row: self._fire_click_center(r))
        timer.start(QApplication.doubleClickInterval())
        self._pending_center_timer = timer

    def _fire_click_center(self, row: int):
        """Timer callback: the double-click interval elapsed without a cancel.

        We do NOT center directly here. On a tall segment, on_cell_selected can
        block the event loop for ~1s doing termbase highlighting; when it
        unblocks, this overdue timer can fire *before* a double-click that is
        still queued in the OS input queue — so centering here would scroll the
        grid out from under the second click (issue #240). Instead we re-post
        the actual centering through a zero-timer, which Qt runs only after all
        pending window-system (input) events. That lets the queued double-click
        reach _cancel_pending_click_center first and abort us."""
        from PyQt6.QtCore import QTimer
        self._pending_center_timer = None
        QTimer.singleShot(0, lambda r=row: self._apply_click_center(r))

    def _click_center_would_help(self, row: int) -> bool:
        """Whether recentering a *clicked* row is worth doing (issue #240).

        Skip only when the row is taller than the viewport — scrollTo can't
        meaningfully center something bigger than the screen (it just bounces).
        Otherwise center it, Trados-style, even if it's currently fully visible.

        (Earlier we also skipped already-visible rows to dodge a mid-double-click
        scroll; that's no longer needed — the double-click cancels the pending
        center, and the real jitter culprit, the focus-border reflow, is fixed.)"""
        try:
            return self.table.rowHeight(row) < self.table.viewport().height()
        except Exception:
            return True

    def _apply_click_center(self, row: int):
        """Zero-timer tail of the single-click recenter: runs after any queued
        double-click has had its chance to cancel us."""
        self._click_activation = False
        if getattr(self, '_click_center_cancelled', False):
            self._click_center_cancelled = False
            return
        try:
            if (self.table.currentRow() == row
                    and getattr(self, 'auto_center_active_segment', False)
                    and not getattr(self, 'filtering_active', False)
                    and self._click_center_would_help(row)):
                self._center_row_in_viewport(row)
        except Exception:
            pass

    def _cancel_pending_click_center(self):
        """Cancel a pending click-triggered recenter (called by double-click
        handlers and by keyboard navigation). Also sets a flag so a recenter
        that has already been re-posted via the zero-timer aborts too."""
        timer = getattr(self, '_pending_center_timer', None)
        if timer is not None:
            try:
                timer.stop()
            except Exception:
                pass
        self._pending_center_timer = None
        self._click_activation = False
        self._click_center_cancelled = True

    def _on_cell_selected_minimal(self, current_row, previous_row):
        """Minimal UI update for fast arrow key navigation - just highlight and scroll"""
        try:
            # Clear previous highlighting
            if previous_row >= 0 and previous_row < self.table.rowCount():
                prev_id_item = self.table.item(previous_row, 0)
                if prev_id_item:
                    prev_id_item.setBackground(QBrush())
                    theme = self.theme_manager.current_theme
                    segment_num_color = "black" if theme.name in ["Light (Default)", "Soft Gray", "Warm Cream", "Sepia", "High Contrast"] else theme.text
                    prev_id_item.setForeground(QBrush(QColor(segment_num_color)))

            # Highlight current segment number in orange
            if current_row >= 0 and current_row < self.table.rowCount():
                current_id_item = self.table.item(current_row, 0)
                if current_id_item:
                    current_id_item.setBackground(QColor("#FFA500"))
                    current_id_item.setForeground(QColor("white"))
                
                # Auto-center if enabled. Skip the *immediate* center when the
                # activation came from a mouse click (issue #240) — a click
                # schedules a deferred, double-click-cancellable recenter
                # instead (see _schedule_click_center).
                if (getattr(self, 'auto_center_active_segment', False)
                        and not getattr(self, 'filtering_active', False)
                        and not getattr(self, '_click_activation', False)):
                    self._center_row_in_viewport(current_row)
        except Exception as e:
            if self.debug_mode_enabled:
                self.log(f"Error in minimal cell selection: {e}")

    def _pcs(self, label: str):
        """Context manager that logs elapsed time for a named block during
        cell-selection. Always active – the timer itself is cheap, and we
        only call self.log() when a block exceeds 50 ms, so normal fast
        clicks produce no log output. Used to pinpoint which lookups in
        `_on_cell_selected_full` are responsible for the multi-second UI
        freezes that some users hit after clicking a segment.
        """
        from contextlib import contextmanager
        import time as _t

        @contextmanager
        def _timer():
            t0 = _t.perf_counter()
            try:
                yield
            finally:
                elapsed_ms = (_t.perf_counter() - t0) * 1000.0
                if elapsed_ms > 50.0:
                    self.log(f"⏱️ [cell-select] {label}: {elapsed_ms:.1f} ms")

        return _timer()

    def _on_cell_selected_full(self, current_row, current_col, previous_row, previous_col):
        """Full cell selection handler with lookups and highlighting"""
        # Top-level stopwatch – any total over ~250 ms is logged so the
        # multi-second freezes that some users hit show up in the log
        # without needing debug mode enabled. Fine-grained `self._pcs("…")`
        # blocks below show which step ate the time.
        import time as _profile_time
        _profile_t0 = _profile_time.perf_counter()

        # Every stretch of this method is timed, so a slow move can say where
        # its time went: the named `_pcs` blocks only speak above 50 ms, and
        # on a real project two thirds of a 250 ms move sat in steps below
        # that. `_lap` costs one clock read; nothing is logged for a fast move.
        _steps = []
        _lap_t = [_profile_t0]

        def _lap(label):
            now = _profile_time.perf_counter()
            _steps.append((label, (now - _lap_t[0]) * 1000.0))
            _lap_t[0] = now

        # Clear text selections in previous row's source and target cells
        if previous_row >= 0 and previous_row < self.table.rowCount():
            # Clear source cell selection (column 2)
            source_widget = self.table.cellWidget(previous_row, 2)
            if source_widget and hasattr(source_widget, 'textCursor'):
                cursor = source_widget.textCursor()
                cursor.clearSelection()
                source_widget.setTextCursor(cursor)
            
            # Clear target cell selection (column 3)
            target_widget = self.table.cellWidget(previous_row, 3)
            if target_widget and hasattr(target_widget, 'textCursor'):
                cursor = target_widget.textCursor()
                cursor.clearSelection()
                target_widget.setTextCursor(cursor)
        _lap("clear old selection")
        
        # DEBUG: Also log all connected signals for this table (only in debug mode)
        if self.debug_mode_enabled:
            self.log(f"🔗 Table currentCellChanged signal connected: {self.table.currentCellChanged}")
            self.log(f"🔗 Table itemClicked signal connected: {self.table.itemClicked}")
            self.log(f"🔗 Table itemSelectionChanged signal connected: {self.table.itemSelectionChanged}")
        try:
            # Update segment number highlighting (orange = active)
            if previous_row >= 0 and previous_row < self.table.rowCount():
                prev_id_item = self.table.item(previous_row, 0)
                if prev_id_item:
                    prev_id_item.setBackground(QBrush())
                    theme = self.theme_manager.current_theme
                    segment_num_color = "black" if theme.name in ["Light (Default)", "Soft Gray", "Warm Cream", "Sepia", "High Contrast"] else theme.text
                    prev_id_item.setForeground(QBrush(QColor(segment_num_color)))

            if current_row >= 0 and current_row < self.table.rowCount():
                current_id_item = self.table.item(current_row, 0)
                if current_id_item:
                    current_id_item.setBackground(QColor("#FFA500"))
                    current_id_item.setForeground(QColor("white"))
                
                if (getattr(self, 'auto_center_active_segment', False)
                        and not getattr(self, 'filtering_active', False)
                        and not getattr(self, '_click_activation', False)):
                    self._center_row_in_viewport(current_row)

            if not self.current_project or current_row < 0:
                return
            _lap("number highlight and centre")

            if current_row < len(self.current_project.segments):
                # CRITICAL: Get segment by ID from the grid, not by row index!
                # The row index might not match the segment's position in the list
                id_item = self.table.item(current_row, 0)
                if not id_item:
                    self.log(f"⚠️ No ID item found at row {current_row}")
                    return
                
                # Get segment ID from the grid cell
                try:
                    segment_id = int(id_item.text())
                except (ValueError, AttributeError):
                    self.log(f"⚠️ Could not parse segment ID from cell at row {current_row}")
                    return
                
                # Find the segment by ID in the project's segment list
                segment = next((seg for seg in self.current_project.segments if seg.id == segment_id), None)
                if not segment:
                    self.log(f"⚠️ Could not find segment with ID {segment_id} in project")
                    return
                _lap("find segment")
                
                # If segment has a batch TM match, display it in the Match Panel immediately
                batch_match = getattr(segment, '_batch_tm_match', None)
                if batch_match:
                    search_source = segment.source
                    if self.hide_outer_wrapping_tags:
                        search_source, _ = strip_outer_wrapping_tags(search_source)
                    tm_matches = [{
                        'source': batch_match.get('source', search_source),
                        'target': batch_match.get('target', ''),
                        'tm_name': batch_match.get('tm_name', 'TM'),
                        'tm_id': batch_match.get('tm_id', ''),
                        'match_pct': batch_match.get('match_pct', 100),
                        # v1.10.50: surface reverse-match info so the Match
                        # Panel can show a "⇄ reversed" chip.
                        'reverse_match': batch_match.get('reverse_match', False),
                    }]
                    self.set_compare_panel_matches(segment.id, search_source, tm_matches, [])
                _lap("batch TM match")

                # ⚡ ALWAYS show the exact (100%) TM match instantly on selection —
                # synchronous, cache-independent, never blocked by a termbase match
                # or the debounced async worker. Fuzzy matches still stream in below.
                self._show_instant_tm_match(segment)
                _lap("instant TM match")

                # Update Preview panel - scroll to and highlight this segment
                self._scroll_preview_to_segment(segment_id)
                _lap("preview scroll")

                # Update Translation Results panel header with segment info
                if hasattr(self, 'results_panels'):
                    for panel in self.results_panels:
                        try:
                            panel.set_segment_info(segment.id, segment.source)
                        except Exception as e:
                            self.log(f"Error updating Translation Results panel header: {e}")
                _lap("results header")
                
                # Update toolbar segment info and status
                if hasattr(self, 'tab_seg_info'):
                    try:
                        self.tab_seg_info.setText(f"Segment {segment.id}")
                        
                        # Update status combo if it exists
                        if hasattr(self, 'tab_status_combo'):
                            from modules.statuses import STATUSES
                            idx = -1
                            for i in range(self.tab_status_combo.count()):
                                if self.tab_status_combo.itemData(i) == segment.status:
                                    idx = i
                                    break
                            if idx >= 0:
                                self.tab_status_combo.blockSignals(True)
                                self.tab_status_combo.setCurrentIndex(idx)
                                self.tab_status_combo.blockSignals(False)
                    except Exception as e:
                        self.log(f"Error updating toolbar: {e}")
                _lap("toolbar")
                
                # Update Translation Results panel notes
                if hasattr(self, 'translation_results_panel') and self.translation_results_panel:
                    try:
                        notes_text = segment.notes if segment.notes else ""
                        self.translation_results_panel.notes_edit.blockSignals(True)
                        self.translation_results_panel.notes_edit.setPlainText(notes_text)
                        self.translation_results_panel.notes_edit.blockSignals(False)
                    except Exception as e:
                        self.log(f"Error updating notes in Translation Results panel: {e}")
                _lap("results notes")
                
                # Update bottom Notes tab with segment notes
                self._update_bottom_notes_for_segment(segment)
                _lap("bottom notes")
                # Scroll + highlight the Segment-Comments list to this segment (pt.3)
                self._sync_segment_comments_to_active(segment.id)
                _lap("segment comments")
                # Update Proofreading note tab
                self._update_proofreading_notes_for_segment(segment)
                _lap("proofreading notes")

                # Update legacy tabbed panels (if they exist for other views)
                if hasattr(self, 'update_tab_segment_editor'):
                    self.update_tab_segment_editor(
                        segment_id=segment.id,
                        source_text=segment.source,
                        target_text=segment.target,
                        status=segment.status,
                        notes=segment.notes
                    )
                _lap("segment editor tab")
                
                # ⚡ PERFORMANCE: Skip heavy lookups if Preview tab is open
                # User is likely just reading/reviewing, not actively translating
                if self._is_preview_tab_active():
                    if self.debug_mode_enabled:
                        self.log(f"⏭️ Preview tab active - skipping TM/Termbase lookups for segment {segment.id}")
                    return
                
                # Get termbase matches (from cache or search on-demand) - ONLY if enabled
                matches_dict = None  # Initialize at the top level
                cached_matches = None  # Initialize for cache skip path

                # 🧪 EXPERIMENTAL: Skip ALL cache checks if cache kill switch is enabled
                if not getattr(self, 'disable_all_caches', False):
                    # 🚀 CHECK PREFETCH CACHE FIRST for instant display (like memoQ)
                    segment_id = segment.id
                    with self.translation_matches_cache_lock:
                        cached_matches = self.translation_matches_cache.get(segment_id)
                    cached_terms = {}
                    if cached_matches is not None:
                        # The entry holds no terms: they come from the one
                        # place every view reads them (_segment_terms).
                        with self._pcs("_segment_terms (cache-hit)"):
                            cached_terms = self._segment_terms(segment)
                        cached_matches = {**cached_matches, "Termbases": self._terms_as_matches(cached_terms)}

                        # Count matches in each category
                        tm_count = len(cached_matches.get("TM", []))
                        tb_count = len(cached_matches.get("Termbases", []))
                        mt_count = len(cached_matches.get("MT", []))
                        llm_count = len(cached_matches.get("LLM", []))

                        self.log(f"⚡ CACHE HIT for segment {segment_id}: TM={tm_count}, TB={tb_count}, MT={mt_count}, LLM={llm_count}")
                else:
                    if self.debug_mode_enabled:
                        self.log(f"🧪 Cache DISABLED - forcing fresh lookup for segment {segment.id}")
                _lap("match cache and segment terms")
                
                # Process cached matches if we have them
                if cached_matches is not None:
                    # Display cached matches immediately
                    if hasattr(self, 'results_panels'):
                        for panel in self.results_panels:
                            try:
                                panel.clear()
                                panel.set_matches(cached_matches)
                            except Exception as e:
                                self.log(f"Error displaying cached matches: {e}")
                    _lap("results panels")
                    
                    # 🔄 Update TermLens with cached termbase matches (always update, even if empty)
                    if self.current_project and ((hasattr(self, 'termlens_widget') and self.termlens_widget) or (hasattr(self, 'termlens_widget_match') and self.termlens_widget_match)):
                        try:
                            termbase_matches = self._terms_for_termlens(cached_terms)
                            _lap("terms for TermLens")
                            # Also get NT matches (fresh, not cached - they may have changed)
                            with self._pcs("find_nt_matches_in_source (cache-hit)"):
                                nt_matches = self.find_nt_matches_in_source(segment.source)

                            # Get status hint for termbase activation
                            with self._pcs("_get_termbase_status_hint (cache-hit)"):
                                status_hint = self._get_termbase_status_hint()
                            _lap("NT matches and termbase hint")

                            # Update both TermLens widgets (left and right)
                            with self._pcs("_update_both_termlens (cache-hit)"):
                                self._update_both_termlens(segment.source, termbase_matches, nt_matches, status_hint)
                        except Exception as e:
                            self.log(f"Error updating termlens from cache: {e}")
                    _lap("TermLens")
                    
                    # 🎯 AUTO-FILL 100% TM MATCH from cache (if enabled in settings)
                    tm_count = len(cached_matches.get("TM", []))
                    if self.auto_fill_100_matches and tm_count > 0:
                        # Check if segment target is empty (don't overwrite existing translations)
                        target_empty = not segment.target or len(segment.target.strip()) == 0
                        
                        if target_empty:
                            # Find first 100% match in cached TM results
                            best_match = None
                            for tm_match in cached_matches.get("TM", []):
                                # Use >= 99.5 to handle potential floating point issues
                                if float(tm_match.relevance) >= 99.5:
                                    best_match = tm_match
                                    break
                            
                            if best_match:
                                self.log(f"✨ Auto-inserting 100% TM match into segment {segment.id}")
                                self._auto_insert_tm_match(segment, best_match.target, current_row)
                                # Play 100% TM match alert sound
                                self._play_sound_effect('tm_100_percent_match')
                    
                    # 🔊 Play fuzzy match sound if fuzzy matches found from cache (but not 100%)
                    if tm_count > 0:
                        tm_matches = cached_matches.get("TM", [])
                        has_100_match = any(float(tm.relevance) >= 99.5 for tm in tm_matches)
                        has_fuzzy_match = any(float(tm.relevance) < 99.5 and float(tm.relevance) >= 50 for tm in tm_matches)
                        if has_fuzzy_match and not has_100_match:
                            self._play_sound_effect('tm_fuzzy_match')
                    _lap("auto-fill and sounds")
                    
                    # Highlight termbase matches in source cell
                    if self.enable_termbase_grid_highlighting and cached_terms:
                        with self._pcs("highlight_source_with_termbase (cache-hit)"):
                            self.highlight_source_with_termbase(current_row, segment.source, cached_terms)
                    _lap("source highlight")

                    # Skip the slow TERMBASE lookup below, we already have termbase matches cached
                    # But TM lookup was skipped in prefetch (not thread-safe), so schedule it now
                    matches_dict = cached_matches  # Set for later use

                    # v1.9.182: Schedule TM lookup even on cache hit (prefetch skips TM - not thread-safe)
                    # v1.9.261: Skip if segment already has batch TM match data from pre-translation
                    tm_count = len(cached_matches.get("TM", []))
                    has_batch_match = getattr(segment, '_batch_tm_match', None)

                    # v1.9.273: Update Match Panel from cached TM matches (fixes #164)
                    # On revisit, tm_count > 0 so _schedule_mt_and_llm_matches is skipped,
                    # but the Match Panel still needs to be updated with cached TM results.
                    if tm_count > 0:
                        tm_matches_for_panel = []
                        for tm in cached_matches.get("TM", []):
                            tm_name = tm.metadata.get('tm_name', 'TM') if tm.metadata else 'TM'
                            tm_id = tm.metadata.get('tm_id', '') if tm.metadata else ''
                            # v1.10.51: read reverse_match from the
                            # TranslationMatch's metadata so the chip
                            # survives the cache-hit re-render path too.
                            rev = bool(tm.metadata.get('reverse_match', False)) if tm.metadata else False
                            tm_matches_for_panel.append({
                                'source': tm.compare_source or tm.source,
                                'target': tm.target,
                                'tm_name': tm_name,
                                'tm_id': tm_id,
                                'match_pct': int(tm.relevance),
                                'reverse_match': rev,
                            })
                        self.set_compare_panel_matches(
                            segment_id,
                            segment.source,
                            tm_matches=tm_matches_for_panel,
                            mt_matches=getattr(self, '_compare_panel_mt_matches', [])
                        )

                    if tm_count == 0 and self.enable_tm_matching and not has_batch_match:
                        find_replace_active = getattr(self, 'find_replace_active', False)
                        if not find_replace_active:
                            # Get termbase matches for the lookup
                            termbase_matches_for_tm = [
                                {
                                    'source_term': match.source,
                                    'target_term': match.target,
                                    'termbase_name': match.metadata.get('termbase_name', '') if match.metadata else '',
                                }
                                for match in cached_matches.get("Termbases", [])
                            ]
                            self._schedule_mt_and_llm_matches(segment, termbase_matches_for_tm)
                    _lap("match panel and scheduling TM/MT/AI")
                
                # Check if TM/Termbase matching is enabled
                if not matches_dict and (not self.enable_tm_matching and not self.enable_termbase_matching):
                    self.log("⏭️ TM/Termbase matching disabled - skipping all lookups")
                elif not matches_dict:  # Only do slow lookup if cache missed
                    try:
                        source_widget = self.table.cellWidget(current_row, 2)  # Source column is column 2

                        # Termbase lookup (if enabled)
                        stored_matches = {}
                        if self.enable_termbase_matching:
                            with self._pcs("_segment_terms (miss)"):
                                stored_matches = self._segment_terms(segment)

                            # CRITICAL FIX: Always update TermLens (even with empty results) - show "No matches" state
                            if self.current_project and ((hasattr(self, 'termlens_widget') and self.termlens_widget) or (hasattr(self, 'termlens_widget_match') and self.termlens_widget_match)):
                                try:
                                    # Convert dict format to list format
                                    termbase_matches = [
                                        {
                                            'source_term': match_data.get('source', ''),
                                            'target_term': match_data.get('translation', ''),
                                            'termbase_name': match_data.get('termbase_name', ''),
                                            'ranking': match_data.get('ranking', 0),
                                            'is_project_termbase': match_data.get('is_project_termbase', False),
                                            'term_id': match_data.get('term_id'),
                                            'termbase_id': match_data.get('termbase_id'),
                                            'notes': match_data.get('notes', '')
                                        }
                                        for match_data in stored_matches.values()
                                    ] if stored_matches else []
                                    # Also get NT matches
                                    with self._pcs("find_nt_matches_in_source (miss)"):
                                        nt_matches = self.find_nt_matches_in_source(segment.source)

                                    # Get status hint for termbase activation
                                    with self._pcs("_get_termbase_status_hint (miss)"):
                                        status_hint = self._get_termbase_status_hint()

                                    # Update both TermLens widgets (left and right)
                                    with self._pcs("_update_both_termlens (miss)"):
                                        self._update_both_termlens(segment.source, termbase_matches, nt_matches, status_hint)
                                except Exception as e:
                                    self.log(f"Error refreshing termlens: {e}")

                            # Store in widget for backwards compatibility
                            if source_widget and hasattr(source_widget, 'termbase_matches'):
                                source_widget.termbase_matches = stored_matches
                            
                            # Highlight termbase matches and NT matches in source cell (if enabled)
                            if self.enable_termbase_grid_highlighting:
                                with self._pcs("highlight_source_with_termbase (miss)"):
                                    self.highlight_source_with_termbase(current_row, segment.source, stored_matches)
                        else:
                            self.log("⏭️ Termbase matching disabled - skipping termbase lookup")

                        if stored_matches:
                            # Convert stored matches to TranslationMatch objects
                            from modules.translation_results_panel import TranslationMatch
                            matches_dict = {
                                "LLM": [],
                                "NT": [],
                                "MT": [],
                                "TM": [],
                                "Termbases": [],
                                "NonTrans": []  # Non-translatables
                            }
                            
                            # Add non-translatable matches (now sourced from
                            # termbase entries flagged is_nontranslatable=1)
                            nt_matches = self.find_nt_matches_in_source(segment.source)
                            if nt_matches:
                                for nt_match in nt_matches:
                                    nt_obj = TranslationMatch(
                                        source=nt_match.get('text', ''),
                                        target=nt_match.get('text', ''),  # Same as source (not translated)
                                        relevance=100,  # Always 100% match
                                        metadata={
                                            'list_name': nt_match.get('list_name', 'Non-Translatables'),
                                            'is_non_translatable': True
                                        },
                                        match_type='NonTrans',
                                        compare_source=nt_match.get('text', ''),
                                        provider_code='NT'
                                    )
                                    matches_dict["NonTrans"].append(nt_obj)

                            for term_id_key, match_info in stored_matches.items():
                                # Extract all fields from match_info (including source term)
                                if isinstance(match_info, dict):
                                    source_term = match_info.get('source', '')
                                    target_term = match_info.get('translation', '')
                                    ranking = match_info.get('ranking', None)
                                    forbidden = match_info.get('forbidden', False)
                                    is_project_termbase = match_info.get('is_project_termbase', False)
                                    term_id = match_info.get('term_id')
                                    termbase_id = match_info.get('termbase_id')
                                    termbase_name = match_info.get('termbase_name', 'Unknown')
                                    domain = match_info.get('domain', '')
                                    notes = match_info.get('notes', '')
                                    project = match_info.get('project', '')
                                    client = match_info.get('client', '')
                                else:
                                    # Backward compatibility: if just string (shouldn't happen with new code)
                                    source_term = str(term_id_key)
                                    target_term = match_info
                                    ranking = None
                                    forbidden = False
                                    is_project_termbase = False
                                    term_id = None
                                    termbase_id = None
                                    termbase_name = 'Unknown'
                                    domain = ''
                                    notes = ''
                                    project = ''
                                    client = ''
                                
                                # Get target synonyms from match_info
                                target_synonyms = match_info.get('target_synonyms', []) if isinstance(match_info, dict) else []
                                
                                match_obj = TranslationMatch(
                                    source=source_term,
                                    target=target_term,
                                    relevance=95,  # High relevance for termbase matches
                                    metadata={
                                        'termbase_name': termbase_name,
                                        'ranking': ranking,
                                        'is_project_termbase': is_project_termbase,
                                        'domain': domain,
                                        'notes': notes,
                                        'project': project,
                                        'client': client,
                                        'forbidden': forbidden,
                                        'term_id': term_id,
                                        'termbase_id': termbase_id,
                                        'target_synonyms': target_synonyms
                                    },
                                    match_type='Termbase',
                                    compare_source=source_term,
                                    provider_code='TB'
                                )
                                matches_dict["Termbases"].append(match_obj)

                            # Get current project languages for all translation services
                            source_lang = getattr(self.current_project, 'source_lang', None) if self.current_project else None
                            target_lang = getattr(self.current_project, 'target_lang', None) if self.current_project else None

                            # Convert language names to codes if needed
                            if source_lang:
                                source_lang_code = self._convert_language_to_code(source_lang)
                            if target_lang:
                                target_lang_code = self._convert_language_to_code(target_lang)

                            # Clear panels first, then show termbase matches immediately
                            if hasattr(self, 'results_panels'):
                                for panel in self.results_panels:
                                    try:
                                        panel.clear()  # Clear old matches
                                        panel.set_matches(matches_dict)  # Add termbase matches immediately
                                    except Exception as e:
                                        self.log(f"Error updating results panel: {e}")
                        else:
                            # No termbase matches - but still check for NT matches
                            matches_dict = {
                                "LLM": [],
                                "NT": [],
                                "MT": [],
                                "TM": [],
                                "Termbases": [],
                                "NonTrans": []  # Non-translatables
                            }
                            
                            # Add non-translatable matches even without termbase matches
                            from modules.translation_results_panel import TranslationMatch
                            nt_matches = self.find_nt_matches_in_source(segment.source)
                            if nt_matches:
                                for nt_match in nt_matches:
                                    nt_obj = TranslationMatch(
                                        source=nt_match.get('text', ''),
                                        target=nt_match.get('text', ''),  # Same as source (not translated)
                                        relevance=100,  # Always 100% match
                                        metadata={
                                            'list_name': nt_match.get('list_name', 'Non-Translatables'),
                                            'is_non_translatable': True
                                        },
                                        match_type='NonTrans',
                                        compare_source=nt_match.get('text', ''),
                                        provider_code='NT'
                                    )
                                    matches_dict["NonTrans"].append(nt_obj)
                                
                                if matches_dict["NonTrans"]:
                                    self.log(f"📋 Found {len(matches_dict['NonTrans'])} NT matches")
                            
                            if hasattr(self, 'results_panels'):
                                for panel in self.results_panels:
                                    try:
                                        panel.clear()
                                        if matches_dict["NonTrans"]:
                                            panel.set_matches(matches_dict)
                                    except Exception as e:
                                        self.log(f"Error clearing results panel: {e}")
                    except Exception as e:
                        self.log(f"Error retrieving stored termbase matches: {e}")

                    # Schedule expensive searches (TM, MT, LLM) with debouncing to prevent UI blocking
                    # ONLY schedule if:
                    # 1. Cache miss OR cache hit with no TM matches (prefetch doesn't include TM - not thread-safe)
                    # 2. TM matching is enabled
                    # 3. Find/Replace is not active (to avoid slowdowns during navigation)
                    needs_tm_lookup = True
                    with self.translation_matches_cache_lock:
                        if segment_id in self.translation_matches_cache:
                            cached = self.translation_matches_cache[segment_id]
                            # v1.9.182: Check if TM matches exist - prefetch worker skips TM lookups
                            needs_tm_lookup = len(cached.get("TM", [])) == 0

                    # v1.9.261: Skip background TM lookup if segment has batch match data from pre-translation
                    has_batch_match = getattr(segment, '_batch_tm_match', None)
                    find_replace_active = getattr(self, 'find_replace_active', False)

                    if needs_tm_lookup and not has_batch_match and self.enable_tm_matching and not find_replace_active:
                        # Get termbase matches if they exist (could be None or empty)
                        termbase_matches = matches_dict.get('Termbases', []) if matches_dict else []
                        self._schedule_mt_and_llm_matches(segment, termbase_matches)
                _lap("lookup without a cached entry")
                
                # Trigger prefetch for next 20 segments (adaptive background caching)
                # Also trigger PROACTIVE HIGHLIGHTING for already-cached segments
                if self.current_project and current_row >= 0:
                    import json
                    next_segment_ids = []
                    start_idx = current_row + 1
                    end_idx = min(start_idx + 20, len(self.current_project.segments))

                    for seg in self.current_project.segments[start_idx:end_idx]:
                        # Check if already cached
                        with self.translation_matches_cache_lock:
                            is_cached = seg.id in self.translation_matches_cache
                        
                        if not is_cached:
                            next_segment_ids.append(seg.id)
                        else:
                            # PROACTIVE HIGHLIGHTING: Apply highlighting for cached segments
                            # that haven't been highlighted yet
                            try:
                                with self.termbase_cache_lock:
                                    termbase_raw = self.termbase_cache.get(seg.id, {})
                                if termbase_raw:
                                    termbase_json = json.dumps(termbase_raw)
                                    self._apply_proactive_highlighting(seg.id, termbase_json)
                            except Exception:
                                pass  # Silent failure for proactive highlighting

                    if next_segment_ids:
                        self._start_prefetch_worker(next_segment_ids)
                _lap("prefetch and highlighting of the next 20")

        except Exception as e:
            self.log(f"Critical error in on_cell_selected: {e}")
        finally:
            _total_ms = (_profile_time.perf_counter() - _profile_t0) * 1000.0
            # 100 ms is where a move starts to feel slow (it was 250 ms,
            # which hid the moves that are sluggish rather than frozen).
            if _total_ms > 100.0:
                self.log(f"⏱️ [cell-select] TOTAL: {_total_ms:.1f} ms")
                self.log(f"⏱️ [cell-select] steps: {format_move_steps(_steps, _total_ms)}")

    def on_selection_changed(self):
        """Handle selection change for row-based selection mode"""
        try:
            # Get currently selected row
            current_row = self.table.currentRow()
            current_col = self.table.currentColumn()
            
            # ⚡ FAST PATH: Skip if this is arrow key navigation (already handled by on_cell_selected)
            if getattr(self, '_arrow_key_navigation', False):
                return  # Let on_cell_selected handle it
            
            # 🚫 GUARD: Skip if we're on the same row (prevents double-processing)
            if hasattr(self, '_last_selection_row') and self._last_selection_row == current_row:
                return
            self._last_selection_row = current_row
            
            # If we have a valid row selection, trigger the same logic as on_cell_selected
            if current_row >= 0:
                # Call on_cell_selected with appropriate parameters
                self.on_cell_selected(current_row, current_col, -1, -1)
        except Exception as e:
            self.log(f"Error in on_selection_changed: {e}")

    def test_cell_selection(self):
        """Test method to manually trigger cell selection for debugging"""
        self.log("🧪 TEST: Manually triggering cell selection for row 0")
        if self.table.rowCount() > 0:
            # Try to programmatically select first row
            self.table.selectRow(0)
            self.table.setCurrentCell(0, 2)  # Select source column
            self.on_cell_selected(0, 2, -1, -1)  # Manually trigger
        else:
            self.log("🧪 TEST: No rows available for testing")

    def _schedule_delayed_lookup(self, segment, current_row):
        """Schedule TM and termbase lookup with delay (cancel if user moves to another segment)"""
        self.log(f"🚀 _schedule_delayed_lookup called for segment {current_row + 1}: '{segment.source[:50]}...'")
        
        # Cancel any pending lookup
        if self.lookup_timer:
            self.lookup_timer.stop()
            self.lookup_timer = None
        
        # Store current segment ID for checking if user moved away
        segment_id = id(segment)
        self.current_lookup_segment_id = segment_id
        
        # If matching is disabled, skip
        self.log(f"🔧 SCHEDULING CHECK: TM enabled={self.enable_tm_matching}, Termbase enabled={self.enable_termbase_matching}")
        if not (self.enable_tm_matching or self.enable_termbase_matching):
            self.log(f"🔧 SCHEDULING BLOCKED: Both TM and termbase matching are disabled")
            return
        self.log(f"🔧 SCHEDULING OK: At least one matching type is enabled")
        
        # Get user-configured delay (default 1.5 seconds)
        general_prefs = self.load_general_settings()
        lookup_delay = general_prefs.get('lookup_delay', 1500)
        
        # Schedule lookup after user-configured delay (allows user to navigate quickly without triggering searches)
        from PyQt6.QtCore import QTimer
        self.lookup_timer = QTimer()
        self.lookup_timer.setSingleShot(True)
        # CRITICAL: Capture variables by value using default parameters to avoid closure bugs
        self.lookup_timer.timeout.connect(lambda seg=segment, row=current_row, seg_id=segment_id: self._perform_delayed_lookup(seg, row, seg_id))
        self.lookup_timer.start(lookup_delay)
        self.log(f"🚀 _schedule_delayed_lookup: Will search segment {current_row + 1} in {lookup_delay}ms")

    def _perform_delayed_lookup(self, segment, current_row, expected_segment_id):
        """Perform TM and termbase lookup after delay (only if user is still on same segment)"""
        self.log(f"� DELAYED LOOKUP STARTED for segment {current_row + 1}: '{segment.source[:50]}...'")
        self.log(f"🚨 DELAYED LOOKUP: expected_id={expected_segment_id}, current_id={self.current_lookup_segment_id}")
        
        # Check if user moved to another segment
        if self.current_lookup_segment_id != expected_segment_id:
            self.log(f"� DELAYED LOOKUP CANCELLED: User moved to different segment")
            return  # User moved away, cancel lookup
            
        self.log(f"🚨 DELAYED LOOKUP CONTINUING: User still on same segment")
        
        # Check if still on the same row
        if current_row < 0 or current_row >= self.table.rowCount():
            return
        
        current_segment = self.table.item(current_row, 2)  # Source column
        if not current_segment or current_segment.text() != segment.source:
            return  # Segment changed
        
        try:
            # Update TM matches (if enabled)
            if self.enable_tm_matching:
                try:
                    self.search_and_display_tm_matches(segment.source)
                except Exception as e:
                    self.log(f"Error searching TM: {e}")
            
            # Update TranslationResultsPanel if available
            if hasattr(self, 'assistance_widget') and hasattr(self.assistance_widget, 'set_segment_info'):
                try:
                    # Generate matches dictionary for the panel
                    from modules.translation_results_panel import TranslationMatch
                    
                    matches_dict = {
                        "LLM": [],     # LLM Translation (appears first)
                        "NT": [],      # No Translation
                        "MT": [],      # Machine Translation
                        "TM": [],      # Translation Memory
                        "Termbases": [], # Terminology
                        "NonTrans": []  # Non-translatables
                    }
                    
                    # Generate TM matches from database if available (and enabled)
                    self.log(f"� DEBUG: Reached TM search section in _perform_delayed_lookup")
                    self.log(f"�🔍 TM SEARCH CHECK: TM enabled={self.enable_tm_matching}, has_db_manager={hasattr(self, 'db_manager')}, db_manager_valid={self.db_manager is not None if hasattr(self, 'db_manager') else False}")
                    if self.enable_tm_matching and hasattr(self, 'db_manager') and self.db_manager:
                        try:
                            self.log(f"🟢 TM SEARCH: Searching for matches for '{segment.source[:50]}...'")
                            
                            # Get current project languages
                            source_lang = getattr(self.current_project, 'source_lang', None) if self.current_project else None
                            target_lang = getattr(self.current_project, 'target_lang', None) if self.current_project else None
                            
                            # Convert language names to codes if needed
                            if source_lang:
                                source_lang = self._convert_language_to_code(source_lang)
                            if target_lang:
                                target_lang = self._convert_language_to_code(target_lang)
                            
                            self.log(f"🟢 TM SEARCH: Project languages: {source_lang} → {target_lang}")
                            
                            # Search in primary direction (current project direction)
                            tm_matches = self.db_manager.search_all(segment.source, max_results=10)
                            
                            # Also search in reverse direction (bidirectional TM matching)
                            # This is crucial for reusing TM in opposite direction
                            reverse_matches = []
                            if source_lang and target_lang and source_lang != target_lang:
                                try:
                                    # Search for segment.source as target text in reverse direction
                                    reverse_query = """
                                        SELECT source_text, target_text, source_lang, target_lang, tm_id, usage_count
                                        FROM translation_units 
                                        WHERE target_text = ? AND source_lang = ? AND target_lang = ?
                                        ORDER BY usage_count DESC
                                        LIMIT 5
                                    """
                                    self.db_manager.cursor.execute(reverse_query, (segment.source, target_lang, source_lang))
                                    reverse_rows = self.db_manager.cursor.fetchall()
                                    
                                    for row in reverse_rows:
                                        reverse_matches.append({
                                            'source': row['target_text'],  # Swap source/target for reverse match
                                            'target': row['source_text'],
                                            'match_pct': 95,  # High relevance for reverse exact match
                                            'tm_name': f"{row['tm_id'].replace('_', ' ').title()} (Reverse)",
                                            'tm_id': row['tm_id'],
                                            # v1.10.51: explicit flag in addition to the legacy
                                            # "(Reverse)" name suffix, so the Match Panel chip
                                            # works for this older code path too. This whole
                                            # block is now functionally redundant with the
                                            # bidirectional support in db.get_exact_match /
                                            # get_exact_matches_batch, but is left in place
                                            # until the call site is refactored.
                                            'reverse_match': True,
                                        })
                                    
                                    self.log(f"🟢 TM SEARCH: Found {len(reverse_matches)} reverse matches")
                                except Exception as e:
                                    self.log(f"Error in reverse TM search: {e}")
                            
                            # Combine primary and reverse matches
                            all_tm_matches = tm_matches + reverse_matches
                            
                            self.log(f"🟢 TM SEARCH: Total matches found: {len(all_tm_matches)} ({len(tm_matches)} primary + {len(reverse_matches)} reverse)")
                            
                            for match in all_tm_matches:
                                match_obj = TranslationMatch(
                                    source=match.get('source', ''),
                                    target=match.get('target', ''),
                                    relevance=match.get('match_pct', 0),
                                    metadata={
                                        'context': match.get('context', ''),
                                        'tm_name': match.get('tm_name', ''),
                                        'tm_id': match.get('tm_id', ''),
                                        'timestamp': match.get('created_at', ''),
                                        'direction': 'reverse' if 'Reverse' in match.get('tm_name', '') else 'primary'
                                    },
                                    match_type='TM',
                                    compare_source=match.get('source', ''),
                                    provider_code='TM'
                                )
                                matches_dict["TM"].append(match_obj)
                        except Exception as e:
                            self.log(f"Error loading TM matches: {e}")
                    
                    # Add termbase matches to the results panel (if enabled)
                    if self.enable_termbase_matching:
                        try:
                            if hasattr(self, 'db_manager') and self.db_manager:
                                # Search termbases directly to get full information including termbase_id
                                source_lang = getattr(self.current_project, 'source_lang', None) if self.current_project else 'en'  # Default to English
                                target_lang = getattr(self.current_project, 'target_lang', None) if self.current_project else 'nl'  # Default to Dutch
                                
                                # Debug logging
                                self.log(f"🔍 Termbase matching: segment='{segment.source}', langs={source_lang}->{target_lang}, project={self.current_project is not None}")
                                self.log(f"🔍 TM matching enabled: {self.enable_tm_matching}, TM database: {hasattr(self, 'tm_database') and self.tm_database is not None}")
                                project_id_raw = getattr(self.current_project, 'id', None) if (self.current_project and hasattr(self.current_project, 'id')) else None
                                
                                # Convert project_id to string if needed (database stores as TEXT)
                                project_id = str(project_id_raw) if project_id_raw is not None else None
                                
                                # Search for all terms in the source text
                                if segment.source:
                                    # First try exact phrase match, then fall back to word-by-word
                                    termbase_results_by_term = {}
                                    
                                    # Try exact phrase match first (whole source text)
                                    try:
                                        self.log("🟢 TERMBASE CALL: From delayed lookup (_delayed_lookup)")
                                        self.log(f"🔍 Searching termbases for exact phrase: '{segment.source.strip()}'")
                                        exact_results = self.db_manager.search_termbases(
                                            segment.source.strip(),
                                            source_lang=source_lang,
                                            target_lang=target_lang,
                                            project_id=project_id,
                                            min_length=len(segment.source.strip())
                                        )
                                        self.log(f"🔍 Exact phrase results: {len(exact_results)} matches")
                                        for tb_match in exact_results:
                                            source_term = tb_match.get('source_term', '').strip()
                                            target_term = tb_match.get('target_term', '').strip()
                                            termbase_id_raw = tb_match.get('termbase_id')
                                            
                                            try:
                                                termbase_id = int(termbase_id_raw) if termbase_id_raw else None
                                            except (ValueError, TypeError):
                                                termbase_id = termbase_id_raw
                                            
                                            if source_term and target_term and termbase_id:
                                                key = (source_term, target_term, termbase_id)
                                                if key not in termbase_results_by_term:
                                                    termbase_results_by_term[key] = tb_match
                                    except Exception as exact_error:
                                        self.log(f"Error in exact phrase search: {exact_error}")
                                    
                                    # Then search word-by-word for partial matches
                                    words = segment.source.split()
                                    for word in words:
                                        clean_word = word.strip('.,!?;:')
                                        if len(clean_word) < 2:
                                            continue
                                        
                                        try:
                                            tb_results = self.db_manager.search_termbases(
                                                clean_word,
                                                source_lang=source_lang,
                                                target_lang=target_lang,
                                                project_id=project_id,
                                                min_length=2
                                            )
                                            
                                            for tb_match in tb_results:
                                                source_term = tb_match.get('source_term', '').strip()
                                                target_term = tb_match.get('target_term', '').strip()
                                                termbase_id_raw = tb_match.get('termbase_id')
                                                
                                                # Convert termbase_id to int if it's stored as string
                                                try:
                                                    termbase_id = int(termbase_id_raw) if termbase_id_raw else None
                                                except (ValueError, TypeError):
                                                    termbase_id = termbase_id_raw  # Keep as-is if conversion fails
                                                
                                                if source_term and target_term and termbase_id:
                                                    # Store with termbase_id as key to avoid duplicates
                                                    key = (source_term, target_term, termbase_id)
                                                    if key not in termbase_results_by_term:
                                                        termbase_results_by_term[key] = tb_match
                                        except Exception as search_error:
                                            self.log(f"Error searching termbases for '{clean_word}': {search_error}")
                                            continue
                                    
                                    # Get termbase names for IDs found
                                    termbase_code_map = self.get_termbase_code_map()
                                    
                                    # Create TranslationMatch objects
                                    for (source_term, target_term, termbase_id), tb_match in termbase_results_by_term.items():
                                        try:
                                            # Get termbase code (or generate from name)
                                            termbase_code = self.get_termbase_code(termbase_id, termbase_code_map)
                                            
                                            # Get termbase ranking and is_project_termbase
                                            termbase_ranking = tb_match.get('ranking', None)  # From JOIN
                                            is_project_termbase = tb_match.get('is_project_termbase', False)
                                            
                                            match_obj = TranslationMatch(
                                                source=source_term,
                                                target=target_term,
                                                relevance=95,  # High relevance for termbase matches
                                                metadata={
                                                    'term_id': tb_match.get('id'),  # Term entry ID for editing
                                                    'termbase_id': termbase_id,
                                                    'termbase_name': tb_match.get('termbase_name', 'Unknown'),
                                                    'ranking': termbase_ranking,
                                                    'is_project_termbase': is_project_termbase,
                                                    'domain': tb_match.get('domain', ''),
                                                    'notes': tb_match.get('notes', ''),
                                                    'project': tb_match.get('project', ''),
                                                    'client': tb_match.get('client', ''),
                                                    'forbidden': tb_match.get('forbidden', False),
                                                    'target_synonyms': tb_match.get('target_synonyms', [])
                                                },
                                                match_type='Termbase',
                                                compare_source=source_term,
                                                provider_code=termbase_code
                                            )
                                            matches_dict["Termbases"].append(match_obj)
                                        except Exception as match_error:
                                            import traceback
                                            self.log(f"❌ Error creating termbase match: {match_error}")
                                            self.log(f"Traceback: {traceback.format_exc()}")
                                            continue
                                    
                                    if termbase_results_by_term:
                                        num_created = len(matches_dict["Termbases"])
                                        self.log(f"✓ Found {len(termbase_results_by_term)} termbase matches, created {num_created} TranslationMatch objects")
                                    else:
                                        self.log(f"ℹ️ No termbase matches found for segment: '{segment.source[:50]}...'")
                                else:
                                    self.log("⚠️ No source text in segment for termbase search")
                        
                            self.log("🎯 CHECKPOINT: Termbase search completed, about to continue to set_matches...")
                        except Exception as e:
                            import traceback
                            self.log(f"❌ Error adding termbase matches: {e}")
                            self.log(f"Traceback: {traceback.format_exc()}")
                    
                    self.log("🎯 CHECKPOINT: About to fetch LLM and MT, then call set_matches...")
                    
                    # Fetch LLM translation for current segment (async, updates panel when ready)
                    self._fetch_llm_translation_async(segment.source, segment, current_row)
                    
                    # Fetch MT translation for current segment (async, updates panel when ready)
                    self._fetch_mt_translation_async(segment.source, segment, current_row)
                    
                    # Display matches (LLM and MT will be added when ready)
                    total_matches = sum(len(matches) for matches in matches_dict.values())
                    self.log(f"🔍 Calling set_matches() with {total_matches} total matches:")
                    for match_type, matches in matches_dict.items():
                        if matches:
                            self.log(f"  {match_type}: {len(matches)} matches")
                            if match_type == "Termbases" and matches:
                                for i, match in enumerate(matches[:3]):  # Show first 3 for debugging
                                    self.log(f"    [{i}] {match.source} → {match.target} (relevance: {match.relevance})")
                    
                    # Update all tabbed results panels
                    if hasattr(self, 'results_panels') and self.results_panels:
                        self.log(f"✅ Updating {len(self.results_panels)} results panels")
                        for panel in self.results_panels:
                            try:
                                panel.set_matches(matches_dict)
                            except Exception as e:
                                self.log(f"Error updating panel: {e}")
                        self.log(f"✅ All panels updated successfully")
                    else:
                        self.log(f"❌ results_panels missing or empty: {getattr(self, 'results_panels', 'NOT_SET')}")
                except Exception as e:
                    self.log(f"Error updating TranslationResultsPanel: {e}")
        except Exception as e:
            self.log(f"Error in delayed lookup: {e}")

    def on_cell_clicked(self, item: QTableWidgetItem):
        """Handle cell click - Status column clicks are disabled (use Segment Editor instead)"""
        self.log(f"🖱️ on_cell_clicked called: row {item.row()}, col {item.column()}")
        if not self.current_project:
            return

        row = item.row()
        col = item.column()
        
        # Reset selection anchor on normal click (so next Shift+PageUp/Down starts fresh)
        # Note: We set it to the clicked row, so Shift+Page will extend from here
        self._selection_anchor_row = row

        # Status column (4) - clicks disabled, use Segment Editor to change status
        # This prevents visual issues and unwanted status changes
        if col == 4:
            self.log(f"Status column click ignored - use Segment Editor to change status")
            return

    def on_cell_double_clicked(self, row: int, col: int):
        """Handle double-click on a cell - remove highlight widget and start editing if needed"""
        self.log(f"🖱️ on_cell_double_clicked called: row {row}, col {col}")

        # Check if this cell has a highlight widget (ClickableHighlightLabel)
        cell_widget = self.table.cellWidget(row, col)
        if cell_widget and isinstance(cell_widget, ClickableHighlightLabel):
            self.log(f"🖱️ Removing highlight widget and starting edit for row {row}, col {col}")

            # Get the plain text stored in the widget
            plain_text = cell_widget.plain_text

            # Get or create the underlying QTableWidgetItem
            item = self.table.item(row, col)
            if item is None:
                item = QTableWidgetItem(plain_text)
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable)
                self.table.setItem(row, col, item)
            else:
                # Restore the plain text and ensure it's editable
                item.setText(plain_text)
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable)

            # Remove the cell widget
            self.table.removeCellWidget(row, col)

            # Start editing
            self.table.setCurrentCell(row, col)
            self.table.editItem(item)

    def copy_source_to_grid_target(self):
        """Copy source to target in currently selected grid row"""
        if not hasattr(self, 'table') or not self.table:
            return

        current_row = self.table.currentRow()
        if current_row < 0 or not self.current_project or current_row >= len(self.current_project.segments):
            return

        segment = self.current_project.segments[current_row]
        old_target = segment.target
        old_status = segment.status
        segment.target = segment.source

        # v1.10.204: record undo BEFORE the visible update so Ctrl+Z restores
        # the previous translation (this single-row menu path was missing it).
        if old_target != segment.target:
            try:
                self.record_undo_state(segment.id, old_target, segment.target, old_status, old_status)
            except Exception as undo_err:
                self.log(f"⚠ Undo recording failed for copy_source_to_grid_target: {undo_err}")

        # Update grid cell - target is column 3 (ID=0, Status=1, Source=2, Target=3)
        # v1.9.306: Apply invisible markers for display
        target_widget = self.table.cellWidget(current_row, 3)
        if target_widget and isinstance(target_widget, EditableGridTextEditor):
            display_text = self.apply_invisible_replacements(segment.source) if hasattr(self, 'apply_invisible_replacements') else segment.source
            target_widget.setPlainText(display_text)

        self.project_modified = True
        self.log(f"📋 Copied source to target in segment {segment.id}")

    def clear_grid_target(self):
        """Clear target in currently selected grid row"""
        if not hasattr(self, 'table') or not self.table:
            return

        current_row = self.table.currentRow()
        if current_row < 0 or not self.current_project or current_row >= len(self.current_project.segments):
            return

        segment = self.current_project.segments[current_row]
        old_target = segment.target
        old_status = segment.status
        segment.target = ""

        # v1.10.204: record undo BEFORE the visible update so Ctrl+Z restores
        # the cleared text.
        if old_target:
            try:
                self.record_undo_state(segment.id, old_target, "", old_status, old_status)
            except Exception as undo_err:
                self.log(f"⚠ Undo recording failed for clear_grid_target: {undo_err}")

        # Update grid cell - target is column 3 (ID=0, Status=1, Source=2, Target=3)
        target_widget = self.table.cellWidget(current_row, 3)
        if target_widget and isinstance(target_widget, EditableGridTextEditor):
            target_widget.clear()

        self.project_modified = True
        self.log(f"🗑️ Cleared target in segment {segment.id}")

    def save_grid_segment(self):
        """Save current segment from grid"""
        if not hasattr(self, 'table') or not self.table:
            return
        
        current_row = self.table.currentRow()
        if current_row < 0 or current_row >= len(self.current_project.segments):
            return
        
        segment = self.current_project.segments[current_row]
        
        # Save to TM ONLY if status is confirmed (user explicitly approved)
        if segment.status == 'confirmed' and segment.target.strip():
            try:
                self.save_segment_to_activated_tms(segment.source, segment.target)
                self.log(f"💾 Saved segment {segment.id} to TM")
            except Exception as e:
                self.log(f"💾 Saved segment {segment.id} (TM save failed: {e})")
        else:
            self.log(f"💾 Saved segment {segment.id}")

    def save_grid_segment_and_next(self):
        """Save current segment and move to next in grid"""
        self.save_grid_segment()
        
        if hasattr(self, 'table') and self.table:
            current_row = self.table.currentRow()
            if current_row < self.table.rowCount() - 1:
                next_row = current_row + 1
                
                # ⚡ INSTANT NAVIGATION
                self._ctrl_enter_navigation = True
                
                self.table.setCurrentCell(next_row, 3)  # Column 3 = Target (widget column)
                # Get the target cell widget and set focus to it
                target_widget = self.table.cellWidget(next_row, 3)
                if target_widget:
                    target_widget.setFocus()
                    # Move cursor to end of text
                    target_widget.moveCursor(QTextCursor.MoveOperation.End)

    def go_to_previous_segment(self, target_column: int = None, to_last_line: bool = False):
        """Navigate to previous segment (Alt+Up)
        
        Args:
            target_column: If provided, position cursor at this column (for arrow key nav)
            to_last_line: If True with target_column, go to last line at that column
        """
        from PyQt6.QtGui import QTextCursor
        
        if hasattr(self, 'table') and self.table:
            current_row = self.table.currentRow()
            if current_row > 0:
                new_row = current_row - 1
                # Skip hidden rows (empty structural segments, filtered rows)
                while new_row > 0 and self.table.isRowHidden(new_row):
                    new_row -= 1
                if self.table.isRowHidden(new_row):
                    return  # No visible row above
                self.table.clearSelection()
                self.table.setCurrentCell(new_row, 3)  # Column 3 = Target (widget column)
                # Get the target cell widget and set focus to it
                target_widget = self.table.cellWidget(new_row, 3)
                if target_widget:
                    target_widget.setFocus()
                    if target_column is not None and to_last_line:
                        # Position at target column on VISUAL last line (for Up arrow navigation)
                        cursor = target_widget.textCursor()
                        doc = target_widget.document()
                        last_block = doc.lastBlock()
                        layout = last_block.layout()

                        if layout and layout.lineCount() > 0:
                            # Get the last VISUAL line within this block
                            last_visual_line = layout.lineAt(layout.lineCount() - 1)
                            line_start_in_block = int(last_visual_line.textStart())
                            line_length = int(last_visual_line.textLength())
                            target_pos_in_line = min(target_column, max(0, line_length - 1))
                            absolute_pos = last_block.position() + line_start_in_block + target_pos_in_line
                            cursor.setPosition(absolute_pos)
                        else:
                            # Fallback: position at target column from block start
                            cursor.setPosition(last_block.position())
                            line_length = last_block.length() - 1
                            target_pos = min(target_column, max(0, line_length))
                            cursor.movePosition(QTextCursor.MoveOperation.Right, QTextCursor.MoveMode.MoveAnchor, target_pos)

                        target_widget.setTextCursor(cursor)
                    else:
                        # Default: move cursor to end of text
                        target_widget.moveCursor(QTextCursor.MoveOperation.End)

    def go_to_next_segment(self, target_column: int = None, to_first_line: bool = False):
        """Navigate to next segment (Alt+Down)

        Args:
            target_column: If provided, position cursor at this column (for arrow key nav)
            to_first_line: If True with target_column, go to first line at that column
        """
        from PyQt6.QtGui import QTextCursor

        if hasattr(self, 'table') and self.table:
            current_row = self.table.currentRow()
            if current_row < self.table.rowCount() - 1:
                new_row = current_row + 1
                # Skip hidden rows (empty structural segments, filtered rows)
                while new_row < self.table.rowCount() - 1 and self.table.isRowHidden(new_row):
                    new_row += 1
                if self.table.isRowHidden(new_row):
                    return  # No visible row below
                self.table.clearSelection()
                self.table.setCurrentCell(new_row, 3)  # Column 3 = Target (widget column)
                # Get the target cell widget and set focus to it
                target_widget = self.table.cellWidget(new_row, 3)
                if target_widget:
                    target_widget.setFocus()
                    if target_column is not None and to_first_line:
                        # Position at target column on VISUAL first line (for Down arrow navigation)
                        cursor = target_widget.textCursor()
                        doc = target_widget.document()
                        first_block = doc.firstBlock()
                        layout = first_block.layout()
                        
                        if layout and layout.lineCount() > 0:
                            # Get the first VISUAL line within this block
                            first_visual_line = layout.lineAt(0)
                            line_length = int(first_visual_line.textLength())
                            target_pos_in_line = min(target_column, max(0, line_length - 1))
                            absolute_pos = first_block.position() + target_pos_in_line
                            cursor.setPosition(absolute_pos)
                        else:
                            # Fallback: position at target column from block start
                            cursor.setPosition(first_block.position())
                            line_length = first_block.length() - 1
                            target_pos = min(target_column, max(0, line_length))
                            cursor.movePosition(QTextCursor.MoveOperation.Right, QTextCursor.MoveMode.MoveAnchor, target_pos)
                        
                        target_widget.setTextCursor(cursor)
                    else:
                        # Default: move cursor to end of text
                        target_widget.moveCursor(QTextCursor.MoveOperation.End)

    def go_to_first_segment(self):
        """Navigate to first segment (Ctrl+Home).

        Pagination/filters may hide row 0, so switch to the first page and then
        move to the first visible row.
        """
        if not hasattr(self, 'table') or not self.table or self.table.rowCount() <= 0:
            return

        # If pagination is enabled, ensure we're on the first page so row 0 is visible.
        if hasattr(self, 'go_to_first_page'):
            self.go_to_first_page()

        target_row = 0
        if self.table.isRowHidden(target_row):
            # Filters can also hide rows; pick the first visible row.
            target_row = next((r for r in range(self.table.rowCount()) if not self.table.isRowHidden(r)), 0)

        self.table.setCurrentCell(target_row, 3)  # Column 3 = Target (widget column)
        id_item = self.table.item(target_row, 0)
        if id_item is not None:
            self.table.scrollToItem(id_item)
        self.log("⏫ Moved to first segment")

        target_widget = self.table.cellWidget(target_row, 3)
        if target_widget:
            target_widget.setFocus()
            target_widget.moveCursor(QTextCursor.MoveOperation.Start)

    def go_to_last_segment(self):
        """Navigate to last segment (Ctrl+End).

        Pagination/filters may hide the last row, so switch to the last page and then
        move to the last visible row.
        """
        if not hasattr(self, 'table') or not self.table or self.table.rowCount() <= 0:
            return

        # If pagination is enabled, ensure we're on the last page so the last row is visible.
        if hasattr(self, 'go_to_last_page'):
            self.go_to_last_page()

        target_row = self.table.rowCount() - 1
        if self.table.isRowHidden(target_row):
            target_row = next((r for r in range(self.table.rowCount() - 1, -1, -1) if not self.table.isRowHidden(r)), target_row)

        self.table.setCurrentCell(target_row, 3)  # Column 3 = Target (widget column)
        id_item = self.table.item(target_row, 0)
        if id_item is not None:
            self.table.scrollToItem(id_item)
        self.log(f"⏬ Moved to last segment ({target_row + 1})")

        target_widget = self.table.cellWidget(target_row, 3)
        if target_widget:
            target_widget.setFocus()
            target_widget.moveCursor(QTextCursor.MoveOperation.End)

    def confirm_and_next_unconfirmed(self):
        """Set current segment to confirmed and move to next unconfirmed segment (Ctrl+Enter)."""
        if not hasattr(self, 'table') or not self.table or not self.current_project:
            self.log(f"⚠️ {format_shortcut_for_display('Ctrl+Enter')}: Missing table or project")
            return
        
        current_row = self.table.currentRow()
        if current_row < 0:
            self.log(f"⚠️ {format_shortcut_for_display('Ctrl+Enter')}: No row selected")
            return

        # If text filters are active, Ctrl+Enter should advance through the filtered/visible rows,
        # not "next unconfirmed". This avoids selecting hidden rows and prevents pagination from
        # accidentally unhiding rows.
        if self._is_text_filter_active():
            self._confirm_current_row_segment()
            # Re-apply filters in case the target text changed and affects matching
            self.apply_filters()
            self._move_to_next_visible_row(current_row)
            return
        
        # CRITICAL: Ensure the target widget has focus before reading from it
        # This fixes the issue where Ctrl+Enter doesn't work without mouse interaction
        target_widget_current = self.table.cellWidget(current_row, 3)
        if target_widget_current and not target_widget_current.hasFocus():
            target_widget_current.setFocus()
            # Give Qt event loop time to process the focus change
            QApplication.processEvents()
        
        # Confirm current row (shared implementation)
        self._confirm_current_row_segment()
        
        # Find next unconfirmed segment
        for row in range(current_row + 1, self.table.rowCount()):
            if row < len(self.current_project.segments):
                seg = self.current_project.segments[row]
                if seg.status not in ['confirmed', 'approved']:
                    # Check if row is hidden due to pagination
                    if self.table.isRowHidden(row):
                        # Calculate which page this row is on and switch to it
                        if hasattr(self, 'grid_page_size') and hasattr(self, 'grid_current_page'):
                            target_page = row // self.grid_page_size
                            if target_page != self.grid_current_page:
                                self.grid_current_page = target_page
                                self._update_pagination_ui()
                                self._apply_pagination_to_grid()
                    
                    # ⚡ INSTANT NAVIGATION: Set flag so on_cell_selected uses fast path
                    # This makes Ctrl+Enter feel instant - cursor moves first, lookups happen after
                    self._ctrl_enter_navigation = True
                    
                    self.table.clearSelection()
                    self.table.setCurrentCell(row, 3)  # Column 3 = Target (widget column)
                    self.table.selectRow(row)  # v1.9.182: Ensure row is visually selected
                    # Ensure the row is visible by scrolling to it
                    self.table.scrollToItem(self.table.item(row, 0), QTableWidget.ScrollHint.PositionAtCenter)
                    self.log(f"⏭️ Moved to next unconfirmed segment {seg.id}")

                    # v1.9.182: Explicitly update termlens (don't rely on deferred signal)
                    self._update_termlens_for_segment(seg)

                    # v1.9.182: Explicitly schedule TM lookup (don't rely on deferred signal)
                    if self.enable_tm_matching:
                        find_replace_active = getattr(self, 'find_replace_active', False)
                        if not find_replace_active:
                            self._schedule_mt_and_llm_matches(seg, [])

                    # Get the target cell widget and set focus to it IMMEDIATELY
                    # (moved BEFORE auto-confirm check for instant responsiveness)
                    target_widget = self.table.cellWidget(row, 3)
                    if target_widget:
                        target_widget.setFocus()
                        # Move cursor to end of text
                        target_widget.moveCursor(QTextCursor.MoveOperation.End)

                    # v1.9.182: Defer auto-confirm check to not block navigation
                    # The TM lookup is slow - do it asynchronously after navigation completes
                    if self.auto_confirm_100_percent_matches:
                        QTimer.singleShot(50, lambda r=row, s=seg: self._check_auto_confirm_100_percent(r, s))
                    return
        
        # No more unconfirmed segments, just go to next
        if current_row < self.table.rowCount() - 1:
            next_row = current_row + 1
            
            # Check if row is hidden due to pagination
            if self.table.isRowHidden(next_row):
                # Calculate which page this row is on and switch to it
                if hasattr(self, 'grid_page_size') and hasattr(self, 'grid_current_page'):
                    target_page = next_row // self.grid_page_size
                    if target_page != self.grid_current_page:
                        self.grid_current_page = target_page
                        self._update_pagination_ui()
                        self._apply_pagination_to_grid()
            
            # ⚡ INSTANT NAVIGATION
            self._ctrl_enter_navigation = True
            
            self.table.clearSelection()
            self.table.setCurrentCell(next_row, 3)  # Column 3 = Target (widget column)
            self.table.selectRow(next_row)  # v1.9.182: Ensure row is visually selected
            # Ensure the row is visible by scrolling to it
            self.table.scrollToItem(self.table.item(next_row, 0), QTableWidget.ScrollHint.PositionAtCenter)
            self.log(f"⏭️ Moved to next segment (all remaining confirmed)")

            # v1.9.182: Explicitly update termlens (don't rely on deferred signal)
            if next_row < len(self.current_project.segments):
                next_seg = self.current_project.segments[next_row]
                self._update_termlens_for_segment(next_seg)

                # v1.9.182: Explicitly schedule TM lookup (don't rely on deferred signal)
                if self.enable_tm_matching:
                    find_replace_active = getattr(self, 'find_replace_active', False)
                    if not find_replace_active:
                        self._schedule_mt_and_llm_matches(next_seg, [])

            # Get the target cell widget and set focus to it
            target_widget = self.table.cellWidget(next_row, 3)
            if target_widget:
                target_widget.setFocus()
                # Move cursor to end of text
                target_widget.moveCursor(QTextCursor.MoveOperation.End)

    def _check_auto_confirm_100_percent(self, row: int, seg):
        """
        v1.9.182: Deferred auto-confirm check for 100% TM matches.

        This is called asynchronously after Ctrl+Enter navigation to avoid blocking
        the UI thread with slow TM database queries.
        """
        try:
            # Verify we're still on the same segment (user may have navigated away)
            current_row = self.table.currentRow() if hasattr(self, 'table') and self.table else -1
            if current_row != row:
                return  # User has moved - don't auto-confirm wrong segment

            if not self.enable_tm_matching or not hasattr(self, 'db_manager') or not self.db_manager:
                return

            # Get activated TM IDs from project settings
            activated_tm_ids = []
            if hasattr(self.current_project, 'tm_settings') and self.current_project.tm_settings:
                activated_tm_ids = self.current_project.tm_settings.get('activated_tm_ids', [])

            if not activated_tm_ids:
                return

            # Use get_exact_match for 100% matches
            source_lang = self.current_project.source_lang if hasattr(self.current_project, 'source_lang') else None
            target_lang = self.current_project.target_lang if hasattr(self.current_project, 'target_lang') else None
            exact_match = self.db_manager.get_exact_match(
                seg.source,
                tm_ids=activated_tm_ids,
                source_lang=source_lang,
                target_lang=target_lang
            )

            if not exact_match:
                return

            # Check if there's a 100% match and (target is empty OR overwrite is enabled)
            target_is_empty = not seg.target.strip()
            can_auto_confirm = target_is_empty or self.auto_confirm_overwrite_existing

            if not can_auto_confirm:
                return

            # Verify AGAIN that we're still on the same segment (TM query may have taken time)
            current_row = self.table.currentRow() if hasattr(self, 'table') and self.table else -1
            if current_row != row:
                return  # User has moved during TM lookup

            match_target = exact_match.get('target_text', '')
            if not match_target:
                return

            overwrite_note = " (overwriting existing)" if not target_is_empty else " (empty target)"
            self.log(f"🎯 Auto-confirm: Found 100% TM match for segment {seg.id}{overwrite_note}")

            # Insert the match into the target cell
            target_widget = self.table.cellWidget(row, 3)
            if target_widget:
                target_widget.setPlainText(match_target)
                seg.target = match_target
                seg.status = 'confirmed'
                self.update_status_icon(row, 'confirmed')
                self.project_modified = True

                # Save to TM
                try:
                    self.save_segment_to_activated_tms(seg.source, seg.target)
                    self.log(f"💾 Auto-confirmed and saved segment {seg.id} to TM")
                except Exception as e:
                    self.log(f"⚠️ Error saving auto-confirmed segment to TM: {e}")

                # Continue to the NEXT unconfirmed segment (skip this one)
                self.confirm_and_next_unconfirmed()
        except Exception as e:
            self.log(f"⚠️ Error in auto-confirm check: {e}")

    def confirm_selected_or_next(self):
        """Smart confirm: if multiple segments selected, confirm all; otherwise confirm and go to next.
        
        This is the handler for Ctrl+Enter:
        - Multiple segments selected: confirm all selected segments
        - Single segment: confirm current and go to next unconfirmed
        """
        selected_segments = self.get_selected_segments_from_grid()
        
        if len(selected_segments) > 1:
            # Multiple segments selected - confirm all
            self.confirm_selected_segments()
        else:
            # Single segment - if text filters are active, advance through the filtered rows
            # (handled inside confirm_and_next_unconfirmed for centralization).
            self.confirm_and_next_unconfirmed()

    def _is_text_filter_active(self) -> bool:
        """Return True if the Filter Source/Target boxes currently contain text."""
        source_text = self._get_line_edit_text('source_filter').strip()
        target_text = self._get_line_edit_text('target_filter').strip()
        return bool(source_text or target_text)

    def _confirm_current_row_segment(self) -> Optional[int]:
        """Confirm the current row's segment and sync target text from the grid.

        Returns the confirmed segment ID on success, else None.
        """
        if not hasattr(self, 'table') or not self.table or not self.current_project:
            return None

        current_row = self.table.currentRow()
        if current_row < 0:
            return None

        # Ensure the target widget has focus before reading from it
        target_widget_current = self.table.cellWidget(current_row, 3)
        if target_widget_current and not target_widget_current.hasFocus():
            target_widget_current.setFocus()
            QApplication.processEvents()

        id_item = self.table.item(current_row, 0)
        if not id_item:
            self.log(f"⚠️ Ctrl+Enter: No ID item at row {current_row}")
            return None

        try:
            segment_id = int(id_item.text())
        except (ValueError, AttributeError):
            self.log(f"⚠️ Ctrl+Enter: Could not parse segment ID from row {current_row}")
            return None

        segment = next((seg for seg in self.current_project.segments if seg.id == segment_id), None)
        if not segment:
            self.log(f"⚠️ Ctrl+Enter: Could not find segment with ID {segment_id}")
            return None

        self.log(f"🔍 Ctrl+Enter: Row {current_row}, Segment ID {segment.id}")

        old_target = segment.target
        old_status = segment.status

        # v1.10.143: use the robust grid→segment sync (reverses invisible-char
        # display markers — ·, →, °, ↵ — and restores any stripped outer
        # wrapping tag) instead of a naive toPlainText(), so confirming a
        # segment that was edited with "Show invisibles" on can't write display
        # markers into segment.target (and from there into the TM / export).
        if self.table.cellWidget(current_row, 3):
            self._sync_grid_targets_to_segments([segment])

        segment.status = 'confirmed'

        # Record undo state for Ctrl+Enter confirmation
        self.record_undo_state(segment_id, old_target, segment.target, old_status, 'confirmed')
        self.update_status_icon(current_row, 'confirmed')
        # Live-update the Preview so the segment's status background colour
        # changes as you confirm (debounced; no-op when the Preview is hidden).
        self._schedule_preview_refresh()
        self.project_modified = True
        self.log(f"✅ Segment {segment.id} confirmed")

        self.update_progress_stats()

        # Play sound effect for segment confirmation
        self._play_sound_effect('segment_confirmed')

        if segment.target and segment.target.strip():
            try:
                self.save_segment_to_activated_tms(segment.source, segment.target)
                self.log(f"💾 Saved segment {segment.id} to TM: '{segment.target[:50]}...'")
            except Exception as e:
                self.log(f"⚠️ Error saving to TM: {e}")

        # Auto-propagate the just-confirmed translation to identical segments
        # (memoQ/Trados-style propagate-on-confirm). Guarded + try/except inside
        # so it can never break the confirm action.
        if getattr(self, 'auto_propagate_on_confirm', False):
            self._auto_propagate_to_identical(segment)

        return segment_id

    def _auto_propagate_to_identical(self, source_segment) -> None:
        """Copy a confirmed segment's target to every other identical-source segment.

        For each OTHER segment whose source matches source_segment.source
        (compared as stripped strings), fill its target with the confirmed
        target — only if that segment is empty, or if "Overwrite existing
        translations when propagating" is enabled. Status becomes 'confirmed'
        when "Mark propagated segments as confirmed" is on, else 'draft'.

        Entirely guarded by try/except so it can never break the confirm action.
        """
        try:
            if not getattr(self, 'auto_propagate_on_confirm', False):
                return
            if not self.current_project or not getattr(self.current_project, 'segments', None):
                return

            new_target = source_segment.target
            if not new_target or not new_target.strip():
                return  # Nothing to propagate

            src_key = (source_segment.source or '').strip()
            if not src_key:
                return

            prop_status = 'confirmed' if getattr(self, 'auto_propagate_confirm', False) else 'draft'
            overwrite = getattr(self, 'auto_propagate_overwrite', False)

            count = 0
            for other in self.current_project.segments:
                if other.id == source_segment.id:
                    continue
                if (other.source or '').strip() != src_key:
                    continue

                target_empty = not (other.target or '').strip()
                if not (target_empty or overwrite):
                    continue
                # Skip if the target is already exactly what we'd write
                if (other.target or '') == new_target and other.status == prop_status:
                    continue

                old_target = other.target
                old_status = other.status
                other.target = new_target
                other.status = prop_status
                other.modified = True
                try:
                    self.record_undo_state(other.id, old_target, new_target, old_status, prop_status)
                except Exception:
                    pass  # never let undo failure break propagation

                # Refresh the grid row in place (if currently populated)
                try:
                    row = self._find_row_for_segment(other.id)
                    if row >= 0:
                        target_widget = self.table.cellWidget(row, 3)
                        if target_widget and hasattr(target_widget, 'setPlainText'):
                            display_text = other.target
                            if getattr(self, 'hide_outer_wrapping_tags', False):
                                display_text, _ = strip_outer_wrapping_tags(display_text)
                            target_widget.setPlainText(display_text)
                        self._refresh_segment_status(other)
                except Exception:
                    pass

                count += 1

            if count > 0:
                self.project_modified = True
                self.update_progress_stats()
                self.log(f"↪ Auto-propagated to {count} identical segment(s)")
        except Exception as e:
            self.log(f"⚠️ Auto-propagate to identical segments failed: {e}")

    def _move_to_next_visible_row(self, current_row: int) -> None:
        """Move focus to the next visible row after current_row (respects filters + pagination)."""
        if not hasattr(self, 'table') or not self.table or not self.current_project:
            return

        row_count = self.table.rowCount()
        if row_count <= 0:
            return

        page_size = getattr(self, 'grid_page_size', 50)
        has_pagination = isinstance(page_size, int) and page_size < 999999 and page_size > 0

        last_page_applied = getattr(self, 'grid_current_page', 0)

        for row in range(current_row + 1, row_count):
            if has_pagination:
                target_page = row // page_size
                if target_page != last_page_applied:
                    self.grid_current_page = target_page
                    last_page_applied = target_page
                    self._apply_pagination_to_grid()

            if not self.table.isRowHidden(row):
                self.table.clearSelection()
                self.table.setCurrentCell(row, 3)
                id_item = self.table.item(row, 0)
                if id_item is not None:
                    self.table.scrollToItem(id_item)

                target_widget = self.table.cellWidget(row, 3)
                if target_widget:
                    target_widget.setFocus()
                    target_widget.moveCursor(QTextCursor.MoveOperation.End)
                return

        # No more visible rows
        self.log("✅ No more filtered segments")

    def confirm_selected_segments(self, segments=None):
        """Confirm all selected segments in the grid.

        Sets status to 'confirmed' for all selected/provided segments and saves them to TM.

        Args:
            segments: Optional list of segments to confirm. If None, uses grid selection.
        """
        if not self.current_project:
            self.log("⚠️ No project loaded")
            return

        selected_segments = segments or self.get_selected_segments_from_grid()

        if not selected_segments:
            self.log("⚠️ No segments selected")
            return
        
        # Sync all target text from grid widgets first
        self._sync_grid_targets_to_segments(selected_segments)
        
        confirmed_count = 0
        tm_saved_count = 0
        
        for segment in selected_segments:
            # Skip already confirmed segments
            if segment.status == 'confirmed':
                continue
            
            old_status = segment.status
            segment.status = 'confirmed'
            confirmed_count += 1
            
            # Update grid status icon
            row = self._find_row_for_segment(segment.id)
            if row >= 0:
                self.update_status_icon(row, 'confirmed')
            
            # Save to TM if target has content
            if segment.target and segment.target.strip():
                try:
                    self.save_segment_to_activated_tms(segment.source, segment.target)
                    tm_saved_count += 1
                except Exception as e:
                    self.log(f"⚠️ Error saving segment {segment.id} to TM: {e}")
        
        if confirmed_count > 0:
            self.project_modified = True
            self.update_window_title()
            self.log(f"✅ Confirmed {confirmed_count} segment(s), saved {tm_saved_count} to TM")
            # Update status bar progress stats
            self.update_progress_stats()
        else:
            self.log(f"ℹ️ All {len(selected_segments)} selected segment(s) were already confirmed")

    def confirm_selected_segments_from_menu(self):
        """Confirm selected/filtered segments (called from Edit > Bulk Operations menu)"""
        if not self.current_project:
            QMessageBox.information(self, "Not Available", "Please load a project first.")
            return

        if not hasattr(self, 'table') or not self.table:
            QMessageBox.information(self, "Not Available", "Grid view is not available.")
            return

        segments = self._get_selected_or_filtered_segments("Confirm Segments")
        if segments:
            self.confirm_selected_segments(segments)

    def change_status_selected(self, new_status: str, from_menu: bool = False):
        """Change status of all selected/filtered segments to the specified status.

        Args:
            new_status: The status key to set (e.g., 'draft', 'pretranslated')
            from_menu: If True, show message boxes for errors (called from menu)
        """
        if not self.current_project:
            if from_menu:
                QMessageBox.information(self, "Not Available", "Please load a project first.")
            else:
                self.log("⚠️ No project loaded")
            return

        if not hasattr(self, 'table') or not self.table:
            if from_menu:
                QMessageBox.information(self, "Not Available", "Grid view is not available.")
            return

        # Get status definition for dialog label
        from modules.statuses import get_status
        status_def = get_status(new_status)

        if from_menu:
            selected_segments = self._get_selected_or_filtered_segments(f"Change Status to '{status_def.label}'")
        else:
            selected_segments = self.get_selected_segments_from_grid()

        if not selected_segments:
            if not from_menu:
                self.log("⚠️ No segments selected")
            return

        # Sync all target text from grid widgets first
        self._sync_grid_targets_to_segments(selected_segments)

        changed_count = 0
        for segment in selected_segments:
            # Skip if already has this status
            if segment.status == new_status:
                continue

            segment.status = new_status
            changed_count += 1

            # Update grid status icon
            row = self._find_row_for_segment(segment.id)
            if row >= 0:
                self.update_status_icon(row, new_status)

        if changed_count > 0:
            self.project_modified = True
            self.update_window_title()
            self.log(f"✅ Changed {changed_count} segment(s) to '{status_def.label}'")
            # Update status bar progress stats
            self.update_progress_stats()
        else:
            self.log(f"ℹ️ All {len(selected_segments)} selected segment(s) already have status '{status_def.label}'")

    def _sync_grid_targets_to_segments(self, segments):
        """Sync target text from grid widgets to segment objects.

        This ensures any edits in the grid are captured before confirming.
        Reverses invisible character display replacements and restores
        stripped outer wrapping tags to get clean text for export.
        """
        # One pass over the grid instead of a row search per segment, which
        # was quadratic: minutes for a 17,000-segment package export.
        rows = self._rows_by_segment_id()
        for segment in segments:
            try:
                row = rows.get(int(segment.id), -1)
            except (TypeError, ValueError):
                row = -1
            if row >= 0:
                target_widget = self.table.cellWidget(row, 3)
                if target_widget:
                    text = target_widget.toPlainText().strip()
                    # Reverse invisible character display replacements (·→space, →→tab, etc.)
                    text = self.reverse_invisible_replacements(text)
                    # Re-add stripped outer wrapping tag if applicable
                    stripped_tag = getattr(target_widget, '_stripped_outer_tag', None)
                    if stripped_tag and self.hide_outer_wrapping_tags:
                        if text and not text.strip().startswith(f'<{stripped_tag}'):
                            text = f'<{stripped_tag}>{text}</{stripped_tag}>'
                    segment.target = text

    def _find_row_for_segment(self, segment_id: int) -> int:
        """Find the grid row index for a segment by ID."""
        if not hasattr(self, 'table') or not self.table:
            return -1
        
        for row in range(self.table.rowCount()):
            id_item = self.table.item(row, 0)
            if id_item:
                try:
                    if int(id_item.text()) == segment_id:
                        return row
                except (ValueError, AttributeError):
                    continue
        return -1

    def _rows_by_segment_id(self) -> Dict[int, int]:
        """``{segment id: grid row}`` for the rows in the grid, in one pass."""
        rows = {}
        if not hasattr(self, 'table') or not self.table:
            return rows
        for row in range(self.table.rowCount()):
            id_item = self.table.item(row, 0)
            if id_item:
                try:
                    rows.setdefault(int(id_item.text()), row)
                except (ValueError, AttributeError):
                    continue
        return rows

    def _refresh_current_segment_matches(self):
        """Refresh termbase matches for the current segment (after edit/delete)"""
        try:
            # Use the targeted refresh method that doesn't trigger TM search
            self._refresh_termbase_display_for_current_segment()
        except Exception as e:
            self.log(f"✗ Error refreshing segment matches: {e}")

    def save_tab_notes(self):
        """Save comments in tab editor"""
        if not hasattr(self, 'tab_current_segment_id') or not self.tab_current_segment_id:
            return
        self.log(f"✓ Saved comments for segment {self.tab_current_segment_id}")
