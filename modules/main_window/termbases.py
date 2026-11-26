"""Termbases: the Termbases tab, adding and editing terms, termbase search and
matching, TermLens, term extraction, import and export.

Part of the main window (SupervertalerQt), moved here unchanged. The
methods run as the main window's own: `self` is the SupervertalerQt.
"""

import os
import os as _os
import re
import threading
import time
import traceback
from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtGui import QAction, QColor, QFont
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QButtonGroup,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)
from modules.colour_checkboxes import BlueCheckmarkCheckBox, OrangeCheckmarkCheckBox, PinkCheckmarkCheckBox
from modules.grid_editors import EditableGridTextEditor, _contains_cjk
from modules.help_system import Topics as HelpTopics, set_topic as set_help_topic
from modules.statuses import DEFAULT_STATUS
from modules.styled_widgets import CheckmarkCheckBox, CheckmarkRadioButton, TealCheckmarkCheckBox
from modules.superlookup_tab import _NumericTableWidgetItem
from modules.tag_text import strip_all_tags
from modules.termbase_manager import rollback_if_failed
from modules.translation_workers import GlossaryExtractionWorker
from typing import Dict, Optional


class TermbasesMixin:
    def show_term_insert_popup(self):
        """Show the TermLens-mirror popup for the current segment.

        Triggered by a lone Ctrl tap (press and release Ctrl with no other key).
        v1.10.87 replaced the previous memoQ-style numbered-list popup with a
        floating mirror of the docked TermLens panel — same chip rendering,
        same colours, same metadata indicators — adding keyboard cycling and
        per-chip Edit / Info shortcuts so the popup matches the Trados
        plugin's TermLensPopupForm behaviour.

        Keyboard inside the popup:
          - Right / Tab         → next chip (wraps)
          - Left / Shift+Tab    → previous chip
          - Enter               → insert current chip
          - 1–9                 → insert chip N directly
          - E / Up              → open editor for current chip
          - I / Down            → toggle metadata sticky popup for current chip
          - Esc                 → close

        Auto-closes on mouse movement (>4 px), focus loss, or any other key
        that isn't a pure modifier. Single-NT shortcut behaviour preserved:
        a single NT with no termbase matches inserts directly without a popup.

        v1.10.90: toggles. A second Ctrl tap while the popup is already
        on-screen closes it instead of opening a duplicate. Cleaner UX
        than the v1.10.87 behaviour, which silently dropped the second
        tap because the still-active popup intercepted the keystroke.
        """
        # ── Toggle if already visible ────────────────────────────────
        existing = getattr(self, '_termlens_popup_instance', None)
        if existing is not None:
            try:
                # The popup uses WA_DeleteOnClose — a live reference
                # means it's actually on-screen. The closeEvent clears
                # this attribute via the on_closed callback below.
                if existing.isVisible():
                    existing.close()
                    return
            except RuntimeError:
                # Stale reference (the C++ object has already been
                # destroyed); fall through and open a fresh popup.
                self._termlens_popup_instance = None

        if not self.current_project:
            return
        current_row = self.table.currentRow()
        if current_row < 0 or current_row >= len(self.current_project.segments):
            self.statusBar().showMessage("No segment selected", 2000)
            return

        segment = self.current_project.segments[current_row]
        segment_id = segment.id

        # ── Gather termbase matches from cache ────────────────────────
        with self.termbase_cache_lock:
            raw_tb = dict(self.termbase_cache.get(segment_id, {}))

        # v1.10.98 — cache-staleness fallback. After certain operations
        # (e.g. adding a synonym to a term that matches the current
        # segment) the termbase_cache entry for this segment can be
        # missing or stale even though the in-memory termbase_index
        # already reflects the new state. Falling back to a fresh
        # ``_search_termbase_in_memory`` call when the cache is empty
        # ensures the popup never opens with a misleadingly empty
        # chip set — and removes the previous "Ctrl tap auto-inserts
        # an NT because termbase_matches is empty so the smart-NT
        # path fires" footgun that prompted this fix. Reported by a
        # user: "After adding a synonym, when I press Ctrl, TermLens
        # popup is no longer shown, but a term is inserted at the
        # cursor."
        if not raw_tb:
            try:
                raw_tb = self._search_termbase_in_memory(segment.source) or {}
            except Exception:
                raw_tb = {}

        termbase_matches = []
        for match in raw_tb.values():
            if not isinstance(match, dict):
                continue
            # Pass through ALL fields the chip renderer cares about
            # (synonyms, abbreviations, metadata, forbidden/NT flags)
            # — pre-v1.10.87 we projected to a thin 6-field dict and
            # the popup chips silently lost all the metadata indicators
            # the docked panel showed. The TermLensWidget normalises
            # the field aliases (source vs source_term, translation
            # vs target_term), so we can hand it the raw dict.
            forward = dict(match)
            forward.setdefault('source_term', match.get('source', ''))
            forward.setdefault('target_term', match.get('translation', ''))
            termbase_matches.append(forward)
        # Sort: project termbase (ranking=1) first, then alphabetically
        # — same ordering the docked panel uses so the popup chips
        # appear in identical order.
        termbase_matches.sort(
            key=lambda x: (-(x.get('ranking') or 0), (x.get('source_term') or '').lower())
        )

        # ── Gather NT matches ────────────────────────────────────────
        nt_matches = self.find_nt_matches_in_source(segment.source)

        if not termbase_matches and not nt_matches:
            self.statusBar().showMessage("No terms or NTs found for this segment", 2000)
            return

        # v1.10.98 — REMOVED: the "smart single-NT" shortcut that used
        # to auto-insert the NT text when there were no termbase matches
        # and exactly one NT match. With the new TermLens-mirror popup
        # (v1.10.87+), the popup itself IS the affordance — auto-inserting
        # bypasses the user's chance to see what's on offer. Also, when
        # combined with the v1.10.x termbase cache going briefly stale
        # after a synonym add, the smart-NT path was firing for segments
        # the user actually had termbase matches on, inserting an
        # unrelated NT at the cursor. Always show the popup now;
        # single-item segments just show a popup with one chip.

        # ── Show TermLens-mirror popup ───────────────────────────────
        from modules.termlens_popup import TermLensPopup
        # Pull the user's TermLens font settings so the popup chips
        # render at the same size the docked panel does. Read from the
        # general-settings JSON via the helper that the General-tab
        # config UI also uses, so the popup automatically stays in sync
        # with whatever the user has set there.
        font_family = 'Segoe UI'
        font_size = 10
        font_bold = False
        try:
            gen = self.load_general_settings() if hasattr(self, 'load_general_settings') else {}
            if isinstance(gen, dict):
                font_family = gen.get('termlens_font_family', font_family)
                font_size = int(gen.get('termlens_font_size', font_size))
                font_bold = bool(gen.get('termlens_font_bold', font_bold))
        except Exception:
            pass
        popup = TermLensPopup(
            source_text=segment.source,
            termbase_matches=termbase_matches,
            nt_matches=nt_matches,
            host_widget=self,
            theme_manager=getattr(self, 'theme_manager', None),
            db_manager=getattr(self, 'db_manager', None),
            log_callback=self.log if hasattr(self, 'log') else None,
            font_family=font_family,
            font_size=font_size,
            font_bold=font_bold,
        )
        popup.term_inserted.connect(self.insert_termlens_text)
        # Edit (E key) closes the popup itself first, then asks the
        # main window to open the existing TermLens editor dialog.
        popup.edit_requested.connect(self._on_termlens_edit_entry)
        # Track the live popup so a second Ctrl tap can toggle it
        # closed instead of stacking. Clear the reference on close
        # so the next Ctrl tap opens a fresh one.
        self._termlens_popup_instance = popup
        def _on_popup_closed(*_):
            if getattr(self, '_termlens_popup_instance', None) is popup:
                self._termlens_popup_instance = None
        popup.destroyed.connect(_on_popup_closed)
        popup.show()
        popup.setFocus()

    def show_term_picker_dialog(self):
        """Show the TermPicker modal dialog (Ctrl+Shift+P).

        v1.10.87 — parity with the Trados TermPickerDialog. Renamed
        in v1.10.205 from "TermLens – Term Picker" to just "TermPicker"
        to clarify it's a sibling surface to TermLens (both consume
        termbase matches), not a sub-mode of TermLens.

        A tabular grid of all matches for the current segment with
        synonym expand/collapse and number-key quick-pick. Use cases:
          - Comparing many alternatives at once when several termbases
            return overlapping hits and the TermLens chip grid feels
            crowded.
          - Keyboard-only insertion via 0-9 when ≤9 matches (auto-
            inserts on digit press); otherwise digit selects + Enter
            inserts.
        """
        # v1.10.97 — diagnostic [TermPicker] logs removed now that the
        # Ctrl+Shift tap chord (which they were added to triage) is gone.
        if not self.current_project:
            return
        current_row = self.table.currentRow()
        if current_row < 0 or current_row >= len(self.current_project.segments):
            self.statusBar().showMessage("No segment selected", 2000)
            return

        segment = self.current_project.segments[current_row]
        segment_id = segment.id

        with self.termbase_cache_lock:
            raw_tb = dict(self.termbase_cache.get(segment_id, {}))

        # v1.10.98 — same cache-staleness fallback as show_term_insert_popup.
        if not raw_tb:
            try:
                raw_tb = self._search_termbase_in_memory(segment.source) or {}
            except Exception:
                raw_tb = {}

        termbase_matches = []
        for match in raw_tb.values():
            if not isinstance(match, dict):
                continue
            forward = dict(match)
            forward.setdefault('source_term', match.get('source', ''))
            forward.setdefault('target_term', match.get('translation', ''))
            termbase_matches.append(forward)
        termbase_matches.sort(
            key=lambda x: (-(x.get('ranking') or 0), (x.get('source_term') or '').lower())
        )
        nt_matches = self.find_nt_matches_in_source(segment.source)

        if not termbase_matches and not nt_matches:
            self.statusBar().showMessage("No terms or NTs found for this segment", 2000)
            return

        from modules.term_picker_dialog import TermPickerDialog, build_picker_matches
        # Persist size + column widths via the main settings dict if
        # we have one. Falls back to an in-process dict that resets
        # on app restart.
        if not hasattr(self, '_term_picker_settings'):
            self._term_picker_settings = {}

        rows = build_picker_matches(termbase_matches, nt_matches)
        dlg = TermPickerDialog(
            matches=rows,
            settings=self._term_picker_settings,
            parent=self,
            source_lang_label=(
                getattr(self.current_project, 'source_language', None) or 'Source'
            ),
            target_lang_label=(
                getattr(self.current_project, 'target_language', None) or 'Target'
            ),
        )
        if dlg.exec() == dlg.DialogCode.Accepted and dlg.selected_target_term:
            self.insert_termlens_text(dlg.selected_target_term)

    def _update_both_termlens(self, source_text, termbase_list, nt_matches, status_hint=None):
        """Update all three TermLens instances with the same data.

        TermLens locations:
        1. Under grid (collapsible via View menu)
        2. Match Panel tab (top section)

        Args:
            source_text: The source text for the current segment
            termbase_list: List of termbase match dictionaries
            nt_matches: List of NT (Never Translate) matches
            status_hint: Optional hint for display when no matches:
                         'no_termbases_activated' - no glossaries activated for project
                         'wrong_language' - activated glossaries don't match project language
        """
        # v1.9.306: Defensive guard – strip invisible character markers from source text
        # so the TermLens tokenizer sees clean text (e.g. "hinge load" not "hinge·\u200Bload")
        if hasattr(self, 'reverse_invisible_replacements'):
            source_text = self.reverse_invisible_replacements(source_text)

        # Update left TermLens (under grid)
        if hasattr(self, 'termlens_widget') and self.termlens_widget:
            try:
                self.termlens_widget.update_with_matches(source_text, termbase_list, nt_matches, status_hint)
            except Exception as e:
                self.log(f"Error updating left termlens: {e}")

        # Update Match Panel TermLens
        if hasattr(self, 'termlens_widget_match') and self.termlens_widget_match:
            try:
                self.termlens_widget_match.update_with_matches(source_text, termbase_list, nt_matches, status_hint)
            except Exception as e:
                self.log(f"Error updating Match Panel termlens: {e}")

        # Keep the QuickTrans panels (under-grid and Match Panel) pointed at
        # the current segment. Each self-guards: it only actually fetches when
        # it is the visible tab, and auto-fetches the cheap MT engines only
        # (LLMs stay on-demand), so this is cheap to call on every segment.
        try:
            if self.current_project:
                src = self.current_project.source_lang or 'en'
                tgt = self.current_project.target_lang or 'nl'
            else:
                src = getattr(self, 'source_language', 'en')
                tgt = getattr(self, 'target_language', 'nl')
            for _panel in (getattr(self, 'quicktrans_panel', None),
                           getattr(self, 'quicktrans_panel_match', None)):
                if _panel is not None:
                    _panel.request_update(source_text, src, tgt)
        except Exception as e:
            self.log(f"Error updating QuickTrans panel(s): {e}")

    def _update_termlens_for_segment(self, segment):
        """Explicitly update termlens for a segment (v1.9.182).

        This is called directly from Ctrl+Enter navigation to ensure
        the termlens updates immediately, bypassing the deferred timer approach.
        """
        if not segment or ((not (hasattr(self, 'termlens_widget') and self.termlens_widget)) and (not (hasattr(self, 'termlens_widget_match') and self.termlens_widget_match))):
            return

        try:
            # Use in-memory index for fast lookup
            stored_matches = self.find_termbase_matches_in_source(segment.source)

            # Convert dict format to list format for termlens
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

            # Get NT matches
            nt_matches = self.find_nt_matches_in_source(segment.source)

            # Get status hint
            status_hint = self._get_termbase_status_hint()

            # Update both TermLens widgets
            self._update_both_termlens(segment.source, termbase_matches, nt_matches, status_hint)

        except Exception as e:
            self.log(f"Error in _update_termlens_for_segment: {e}")

    def _get_termbase_status_hint(self) -> str:
        """Check termbase activation status and return appropriate hint.

        Returns:
            'no_termbases_activated' - if no glossaries are activated for this project
            'wrong_language' - if activated glossaries don't match project language pair
            None - if everything is correctly configured
        """
        if not self.current_project:
            return None

        project_id = self.current_project.id if hasattr(self.current_project, 'id') else None
        if not project_id:
            return None

        # Check if termbase manager is available
        if not hasattr(self, 'termbase_mgr') or not self.termbase_mgr:
            return None

        try:
            # Get active termbase IDs for this project
            active_tb_ids = self.termbase_mgr.get_active_termbase_ids(project_id)

            # Check if no termbases are activated
            if not active_tb_ids or len(active_tb_ids) == 0:
                return 'no_termbases_activated'

            # Check if any activated termbases match the project's language pair.
            # Normalise both sides through _convert_language_to_code so that
            # full names ("Dutch") and ISO codes ("nl") compare equal – the
            # project stores its languages as full English names by default
            # (chosen from the Settings dropdown) while imported termbases
            # tend to carry ISO codes. Without normalisation, "dutch" == "nl"
            # is false and the UI shows "Glossaries don't match language pair"
            # even when the langs are in fact the same.
            project_source = self._convert_language_to_code(self.current_project.source_lang or '') or ''
            project_target = self._convert_language_to_code(self.current_project.target_lang or '') or ''

            # Get all termbases and check language pairs
            all_termbases = self.termbase_mgr.get_all_termbases()
            has_matching_language = False

            for tb in all_termbases:
                if tb['id'] in active_tb_ids:
                    tb_source = self._convert_language_to_code(tb.get('source_lang') or '') or ''
                    tb_target = self._convert_language_to_code(tb.get('target_lang') or '') or ''
                    # Match if: no language set, or languages match (bidirectional)
                    if (not tb_source and not tb_target) or \
                       (tb_source == project_source and tb_target == project_target) or \
                       (tb_source == project_target and tb_target == project_source):
                        has_matching_language = True
                        break

            if not has_matching_language:
                return 'wrong_language'

            return None  # All good

        except Exception as e:
            self.log(f"Error checking termbase status: {e}")
            return None

    def _refresh_termbase_display_for_current_segment(self):
        """Refresh only termbase/glossary display for the current segment.
        
        This is a targeted refresh that does NOT trigger a TM search.
        Use this after adding a term to a glossary to update the display
        without the overhead of re-searching translation memories.
        """
        current_row = self.table.currentRow()
        if current_row < 0 or not self.current_project:
            return
        
        # Get the segment from the grid (use ID from cell, not row index)
        id_item = self.table.item(current_row, 0)
        if not id_item:
            return
        
        try:
            segment_id = int(id_item.text())
        except (ValueError, AttributeError):
            return
        
        segment = next((seg for seg in self.current_project.segments if seg.id == segment_id), None)
        if not segment:
            return
        
        # Clear only the termbase cache for this segment (NOT the TM cache)
        with self.termbase_cache_lock:
            if segment.id in self.termbase_cache:
                del self.termbase_cache[segment.id]
                self.log(f"🗑️ Cleared termbase cache for segment {segment.id}")
        
        # Search for fresh termbase matches
        termbase_matches = self.find_termbase_matches_in_source(segment.source)
        
        # Update termbase cache
        with self.termbase_cache_lock:
            self.termbase_cache[segment.id] = termbase_matches
        
        # Update TermLens widget
        if (hasattr(self, 'termlens_widget') and self.termlens_widget) or (hasattr(self, 'termlens_widget_match') and self.termlens_widget_match):
            try:
                # Convert termbase matches to list format for termlens.
                # Note: find_termbase_matches_in_source returns dict with
                # 'source' and 'translation' keys (not 'source_term' /
                # 'target_term') — translation_list normalises to the
                # *_term shape TermLens expects.
                #
                # v1.10.73 (Tier 1 + Tier 2): forward the new metadata
                # fields (``definition``, ``url``, ``source_abbreviation``,
                # ``target_abbreviation``) and synonym lists
                # (``source_synonyms``, ``target_synonyms``) that
                # ``_search_termbase_in_memory`` now populates on every
                # match dict. The TermBlock tooltip's _meta_lines helper
                # immediately picks up the metadata fields, and the
                # update_with_matches loop appends target synonyms as
                # additional clickable translations.
                termbase_list = [
                    {
                        'source_term': match.get('source', ''),
                        'target_term': match.get('translation', ''),
                        'termbase_name': match.get('termbase_name', ''),
                        'ranking': match.get('ranking', 0),
                        'is_project_termbase': match.get('is_project_termbase', False),
                        'term_id': match.get('term_id'),
                        'termbase_id': match.get('termbase_id'),
                        'notes': match.get('notes', ''),
                        'definition': match.get('definition', ''),
                        'domain': match.get('domain', ''),
                        'url': match.get('url', ''),
                        'source_abbreviation': match.get('source_abbreviation', ''),
                        'target_abbreviation': match.get('target_abbreviation', ''),
                        'source_synonyms': match.get('source_synonyms', []),
                        'target_synonyms': match.get('target_synonyms', []),
                        # v1.10.75 (Tier 3): forbidden + NT flags for
                        # the new background-colour treatment in TermBlock.
                        'forbidden': match.get('forbidden', False),
                        'is_nontranslatable': match.get('is_nontranslatable', False),
                        # v1.10.75 (Tier 3c) abbreviation marker
                        'matched_via_abbreviation': match.get('matched_via_abbreviation', False),
                        # v1.10.309: full term forms (see _search_termbase_in_memory)
                        'full_source_term': match.get('full_source_term', ''),
                        'full_target_term': match.get('full_target_term', ''),
                    }
                    for match in termbase_matches.values() if isinstance(match, dict)
                ] if isinstance(termbase_matches, dict) else []
                
                # Get NT matches
                nt_matches = self.find_nt_matches_in_source(segment.source)

                # Get status hint for termbase activation
                status_hint = self._get_termbase_status_hint()

                # Update both TermLens widgets (left and right)
                self._update_both_termlens(segment.source, termbase_list, nt_matches, status_hint)
            except Exception as e:
                self.log(f"Error updating termlens: {e}")
        
        # Update Translation Results panel termbase section (without touching TM)
        if hasattr(self, 'results_panels'):
            for panel in self.results_panels:
                try:
                    # Get the existing matches from the panel and update only Termbases
                    if hasattr(panel, 'current_matches') and panel.current_matches:
                        # Convert termbase matches to TranslationMatch format
                        # Note: find_termbase_matches_in_source returns 'source' and 'translation' keys
                        from modules.translation_results_panel import TranslationMatch
                        tb_matches = []
                        for match_data in termbase_matches.values():
                            if isinstance(match_data, dict):
                                tb_match = TranslationMatch(
                                    source=match_data.get('source', ''),  # 'source' not 'source_term'
                                    target=match_data.get('translation', ''),  # 'translation' not 'target_term'
                                    relevance=100.0,
                                    match_type='Termbase',
                                    provider=match_data.get('termbase_name', 'Termbase'),
                                    metadata={
                                        'termbase_name': match_data.get('termbase_name', ''),
                                        'ranking': match_data.get('ranking', 0),
                                        'is_project_termbase': match_data.get('is_project_termbase', False),
                                        'term_id': match_data.get('term_id'),
                                        'termbase_id': match_data.get('termbase_id'),
                                        'notes': match_data.get('notes', '')
                                    }
                                )
                                tb_matches.append(tb_match)
                        
                        # Update just the Termbases section
                        panel.current_matches['Termbases'] = tb_matches
                        panel.set_matches(panel.current_matches)
                except Exception as e:
                    self.log(f"Error updating results panel termbases: {e}")
        
        # Re-highlight termbase matches in the source cell.
        # We call highlight_source_with_termbase() directly (NOT rehighlight()),
        # because rehighlight() is expensive (spellcheck + tag detection + termbase).
        # This only applies the lightweight termbase + NT formatting pass.
        self.highlight_source_with_termbase(current_row, segment.source, termbase_matches)

        self.log(f"🔄 Refreshed termbase display for segment {segment.id} (TM untouched)")

    def _orient_term_for_termbase(self, source_text: str, target_text: str, termbase: dict):
        """Return (source_term, target_term, source_lang_code, target_lang_code)
        oriented to match the **termbase's declared direction**, not the
        project's.

        v1.10.62 termbase-direction bug fix. Mirrors the Trados v4.19.22
        fix (commit ``cf8d70b`` — "termbase direction overhaul"). The
        write side of the codebase previously trusted the project's
        direction blindly: it took ``project.source_lang`` as the
        term's ``source_lang`` and ``source_text`` as the term's
        ``source_term``, even when the termbase declared the opposite
        direction. Result for users running e.g. NL→EN projects with
        EN→NL termbases: the Dutch term landed in the column the
        termbase declares as English (and vice versa), and per-row
        lang codes pointed the wrong way. The bidirectional read-side
        indexer (``_build_termbase_index``) then swapped them again
        on lookup, so TermLens searched the Dutch segment for the
        English text and found nothing.

        This helper sits between the call sites (Ctrl+Q quick-add,
        Ctrl+Alt+T dialog add, Alt+Up project-termbase add) and
        ``TermbaseManager.add_term`` to do a per-termbase orientation
        fix: if the termbase declares the reverse of the project's
        direction, swap source/target text and lang codes so the
        INSERT lands them in the columns the termbase actually
        expects.

        Returns the inputs unchanged when:
         - there is no current project, or
         - the termbase declares no languages (legacy), or
         - the termbase's direction matches the project's, or
         - the termbase's direction is unrelated to the project's
           (different language pair entirely).

        In the matching case (termbase aligned with project) we still
        normalise the lang codes to ISO via
        ``_convert_language_to_code`` so the per-row ``source_lang`` /
        ``target_lang`` columns always carry codes, never full names.
        """
        # No project context → can't compare; trust the caller.
        if not self.current_project:
            return source_text, target_text, '', ''

        project_source = self._convert_language_to_code(
            self.current_project.source_lang or '') or ''
        project_target = self._convert_language_to_code(
            self.current_project.target_lang or '') or ''
        tb_source = self._convert_language_to_code(
            termbase.get('source_lang') or '') or ''
        tb_target = self._convert_language_to_code(
            termbase.get('target_lang') or '') or ''

        # Termbase has no declared direction → write project-direction
        # codes; harmless and at least populates the columns for any
        # future indexer that needs them.
        if not tb_source or not tb_target:
            return source_text, target_text, project_source, project_target

        # Reverse case: termbase's source matches project's target AND
        # termbase's target matches project's source. Swap so the
        # storage layer's source_term column always contains a term in
        # the termbase's declared source language.
        if (project_source and project_target
                and tb_source == project_target
                and tb_target == project_source):
            tb_name = termbase.get('name', '?')
            self.log(
                f"⚠️ Termbase '{tb_name}' direction ({tb_source}→{tb_target}) "
                f"is reverse of project ({project_source}→{project_target}); "
                f"swapping source/target on save so the term lands in the "
                f"columns the termbase expects."
            )
            return target_text, source_text, tb_source, tb_target

        # Default: termbase direction matches project direction (or the
        # two are unrelated entirely). Write project-direction lang
        # codes; the storage row's source_term/target_term already
        # match the termbase's declared direction by definition.
        return source_text, target_text, project_source, project_target

    def add_term_pair_to_termbase(self, source_text: str, target_text: str):
        """Add a term pair to active termbase(s) with metadata dialog"""
        # Check if we have a current project
        if not hasattr(self, 'current_project') or not self.current_project:
            QMessageBox.warning(self, "No Active Project", "Please open or create a project before adding terms to termbase.")
            return
        
        # Get active termbases for current project
        if not hasattr(self, 'termbase_mgr') or not self.termbase_mgr:
            QMessageBox.critical(self, "Error", "Termbase manager not initialized")
            return
        
        # Resolve project ID (prefer persistent project.id from loaded project)
        project_id = getattr(self.current_project, 'id', None)
        if not project_id:
            # Fallback for legacy projects without id
            import hashlib
            if hasattr(self, 'project_file_path') and self.project_file_path:
                project_id = int(hashlib.md5(self.project_file_path.encode()).hexdigest()[:8], 16)
            else:
                project_id = int(hashlib.md5(self.current_project.name.encode()).hexdigest()[:8], 16)

        # Get all termbases for dialog visibility, but preselect only active ones for this project
        active_termbases = self.termbase_mgr.get_all_termbases()
        active_termbase_ids = set(self.termbase_mgr.get_active_termbase_ids(project_id)) if project_id else set()
        
        if not active_termbases:
            QMessageBox.warning(self, "No Termbase", "Please create or activate at least one termbase on the 🏷️ Termbases tab.")
            return
        
        # Show metadata dialog with termbase selection
        from modules.termbase_entry_editor import TermbaseEntryEditor
        dialog = TermbaseEntryEditor(
            self,
            source_term=source_text,
            target_term=target_text,
            active_termbases=active_termbases,
            user_data_path=self.user_data_path,
            default_selected_termbase_ids=active_termbase_ids,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return  # User cancelled

        metadata = dialog.get_metadata()
        source_synonyms = dialog.get_source_synonyms()
        target_synonyms = dialog.get_target_synonyms()

        # The user can correct the source/target text in the dialog now;
        # use those values if they changed.
        if hasattr(dialog, 'get_source_term'):
            edited_source = dialog.get_source_term()
            edited_target = dialog.get_target_term()
            if edited_source:
                source_text = edited_source
            if edited_target:
                target_text = edited_target

        # Determine destination termbases automatically. The picker that
        # used to live in the dialog is gone; the term is written to every
        # termbase that is BOTH active for this project AND not marked
        # read-only in the Termbases tab. If nothing matches, surface a
        # clear error so the user knows they need to flip a Write toggle.
        selected_termbase_ids = [
            tb['id'] for tb in active_termbases
            if tb['id'] in active_termbase_ids
            and not tb.get('read_only', False)
        ]
        if not selected_termbase_ids:
            QMessageBox.warning(
                self, "No Writable Termbase",
                "There are no termbases marked <b>Write</b> for the current "
                "project.<br><br>"
                "Open <b>Termbases → TM List</b> and tick the <b>Write</b> "
                "checkbox on at least one active termbase, then try again."
            )
            return

        # v1.10.62: orient + write is now per-termbase, since each
        # destination termbase may declare a different direction.
        # Synonym lists ride along unchanged — orientation only
        # decides which COLUMN the primary source/target text lands
        # in, not which list a synonym belongs to.
        self.log(f"📝 Adding term: {source_text} → {target_text}")
        if source_synonyms:
            self.log(f"   With {len(source_synonyms)} source synonym(s): {', '.join([s['text'] for s in source_synonyms])}")
        if target_synonyms:
            self.log(f"   With {len(target_synonyms)} target synonym(s): {', '.join([s['text'] for s in target_synonyms])}")

        # "Similar Term Found" check (Trados parity). If the new pair partially
        # overlaps an existing entry, offer to merge as a synonym instead of
        # creating a near-duplicate. Merged termbases are skipped in the loop.
        target_tbs = [tb for tb in active_termbases
                      if tb['id'] in selected_termbase_ids]
        decision, merged_ids = self._detect_and_merge_synonyms(
            source_text, target_text, target_tbs,
            source_synonyms, target_synonyms)
        if decision == 'cancel':
            return
        if decision == 'failed':
            QMessageBox.warning(
                self, "Term not added",
                f"Workbench could not add “{source_text} → {target_text}” as a synonym.\n\n"
                "Another program may be using the database (it is locked). "
                "The Session Log has the details.")
            return

        # Add term to selected termbases only
        success_count = 0
        duplicate_count = 0
        error_count = 0
        for tb in active_termbases:
            if tb['id'] not in selected_termbase_ids:
                continue  # Skip unselected termbases
            if tb['id'] in merged_ids:
                continue  # Already folded into an existing entry as a synonym

            # v1.10.62 per-termbase orientation (see _orient_term_for_termbase).
            src_term, tgt_term, src_lang, tgt_lang = (
                self._orient_term_for_termbase(source_text, target_text, tb)
            )
            # Detect whether orient flipped source<->target. If yes, the
            # dialog's "source synonyms" panel (user-typed in project's
            # source language) actually belongs in the termbase's
            # TARGET column, and vice versa. Flip the language tag
            # passed to add_synonym accordingly.
            tb_was_swapped = (src_term != source_text)
            syn_source_lang = 'target' if tb_was_swapped else 'source'
            syn_target_lang = 'source' if tb_was_swapped else 'target'

            try:
                term_id = self.termbase_mgr.add_term(
                    termbase_id=tb['id'],
                    source_term=src_term,
                    target_term=tgt_term,
                    source_lang=src_lang,
                    target_lang=tgt_lang,
                    domain=metadata['domain'],
                    notes=metadata['notes'],
                    project=metadata['project'],
                    client=metadata['client'],
                    forbidden=metadata['forbidden'],
                    is_nontranslatable=metadata.get('is_nontranslatable', False),
                    definition=metadata.get('definition', ''),
                    url=metadata.get('url', ''),
                    source_abbreviation=metadata.get('source_abbreviation', ''),
                    target_abbreviation=metadata.get('target_abbreviation', ''),
                )
                
                if term_id:
                    success_count += 1
                    self.log(f"✓ Added term to termbase '{tb['name']}': {source_text} → {target_text}")
                    
                    # Add source synonyms if any. Language tag is
                    # ``syn_source_lang`` (= 'source' normally, or
                    # 'target' when the termbase is reverse-direction
                    # to the project — see the swap-detection above).
                    if source_synonyms:
                        for syn_data in source_synonyms:
                            if self.termbase_mgr.add_synonym(
                                term_id,
                                syn_data['text'],
                                language=syn_source_lang,
                                display_order=syn_data['order'],
                                forbidden=syn_data['forbidden']
                            ):
                                forbidden_marker = " (forbidden)" if syn_data['forbidden'] else ""
                                self.log(f"  ✓ Added source synonym: {syn_data['text']}{forbidden_marker}")
                            else:
                                self.log(f"  ✗ Failed to add source synonym: {syn_data['text']}")

                    # Add target synonyms if any (same flip logic).
                    if target_synonyms:
                        for syn_data in target_synonyms:
                            if self.termbase_mgr.add_synonym(
                                term_id,
                                syn_data['text'],
                                language=syn_target_lang,
                                display_order=syn_data['order'],
                                forbidden=syn_data['forbidden']
                            ):
                                forbidden_marker = " (forbidden)" if syn_data['forbidden'] else ""
                                self.log(f"  ✓ Added target synonym: {syn_data['text']}{forbidden_marker}")
                            else:
                                self.log(f"  ✗ Failed to add target synonym: {syn_data['text']}")

                else:
                    duplicate_count += 1
                    
            except Exception as e:
                error_count += 1
                self.log(f"✗ Error adding term to termbase '{tb['name']}': {e}")

        # Count synonym-merges (Trados parity) as successful adds so the
        # messaging below reports them and the index/display refresh runs.
        success_count += len(merged_ids)

        # Show result
        if success_count > 0:
            self._play_sound_effect('glossary_term_added')

            # Non-modal info bar message (status bar)
            try:
                if hasattr(self, 'statusBar') and self.statusBar():
                    if success_count == 1:
                        self.statusBar().showMessage(f"✓ Added termbase entry: {source_text} → {target_text} (to 1 termbase)", 3500)
                    else:
                        self.statusBar().showMessage(f"✓ Added termbase entry to {success_count} termbases: {source_text} → {target_text}", 3500)
            except Exception:
                pass

            QMessageBox.information(self, "Term Added", f"Successfully added term pair to {success_count} termbase(s):\n\nSource: {source_text}\nTarget: {target_text}\n\nDomain: {metadata['domain'] or '(none)'}")
            
            # Keep in-memory termbase search/index in sync with DB so new term appears immediately
            try:
                with self.termbase_cache_lock:
                    self.termbase_cache.clear()
                self._build_termbase_index()
            except Exception as e:
                self.log(f"WARNING: Failed to rebuild termbase index after add: {e}")

# Refresh termbase display (NOT TM - that would be wasteful)
            self._refresh_termbase_display_for_current_segment()
            
            # IMPORTANT: Refresh the termbase list UI if it's currently open to update term counts
            # Find the termbase tab and call its refresh function
            if hasattr(self, 'termbase_tab_refresh_callback') and self.termbase_tab_refresh_callback:
                self.log("🔄 Refreshing termbase list to update term counts")
                self.termbase_tab_refresh_callback()
            else:
                self.log("⚠️ No termbase refresh callback found (tab not initialized yet)")
        else:
            if duplicate_count > 0 and error_count == 0:
                self._play_sound_effect('glossary_term_duplicate')
                try:
                    if hasattr(self, 'statusBar') and self.statusBar():
                        self.statusBar().showMessage("⚠ Duplicate termbase entry (not added)", 3500)
                except Exception:
                    pass
                QMessageBox.warning(
                    self,
                    "Duplicate Term",
                    "This term already exists in the selected termbase(s). Duplicate terms are not allowed."
                )
            else:
                self._play_sound_effect('glossary_term_error')
                try:
                    if hasattr(self, 'statusBar') and self.statusBar():
                        self.statusBar().showMessage("❌ Error adding termbase entry (see log)", 3500)
                except Exception:
                    pass
                QMessageBox.warning(self, "Error Adding Term", "Failed to add term to any termbase. Check the log for details.")

    def _snapshot_termbase_db_state(self):
        """Return a small tuple summarising the termbase tables' state.

        Used by the file watcher to decide whether anything actually
        changed in the termbase tables before triggering a rebuild.
        Cheap (4 aggregate queries, sub-millisecond). Returns ``None``
        on any error so the caller can fall back to "assume changed".
        """
        try:
            if not hasattr(self, 'db_manager') or self.db_manager is None:
                return None
            cur = self.db_manager.cursor
            cur.execute("SELECT COUNT(*), COALESCE(MAX(id), 0) FROM termbases")
            row_tb = cur.fetchone() or (0, 0)
            cur.execute("SELECT COUNT(*), COALESCE(MAX(id), 0), COALESCE(MAX(modified_date), '') FROM termbase_terms")
            row_terms = cur.fetchone() or (0, 0, '')
            return (row_tb[0], row_tb[1], row_terms[0], row_terms[1], row_terms[2])
        except Exception:
            return None

    def _setup_termbase_db_watcher(self):
        """Install a QFileSystemWatcher on the active SQLite DB file
        and wire it to a debounced auto-refresh of the termbase index.

        Safe to call repeatedly — the previous watcher (if any) is
        torn down first, so this can be re-invoked on project switch
        without leaking watchers or duplicate-firing.
        """
        try:
            from PyQt6.QtCore import QFileSystemWatcher, QTimer
        except Exception as e:
            self.log(f"⚠️ Termbase DB watcher: PyQt6 missing QFileSystemWatcher? {e}")
            return

        # Tear down any previous watcher / timer cleanly.
        try:
            old_w = getattr(self, '_termbase_db_watcher', None)
            if old_w is not None:
                try:
                    paths = old_w.files()
                    if paths:
                        old_w.removePaths(paths)
                except Exception:
                    pass
                old_w.deleteLater()
            old_t = getattr(self, '_termbase_db_debounce_timer', None)
            if old_t is not None:
                old_t.stop()
                old_t.deleteLater()
        except Exception:
            pass
        self._termbase_db_watcher = None
        self._termbase_db_debounce_timer = None

        # Resolve the DB path. Bail quietly if no db_manager yet —
        # caller can retry after init.
        try:
            db_path = getattr(self.db_manager, 'db_path', None) if hasattr(self, 'db_manager') else None
            if not db_path:
                return
            import os as _os
            if not _os.path.isfile(db_path):
                self.log(f"⚠️ Termbase DB watcher: file does not exist ({db_path}); skipping auto-refresh")
                return
        except Exception as e:
            self.log(f"⚠️ Termbase DB watcher: path resolution failed ({e}); skipping auto-refresh")
            return

        # Snapshot the current termbase state so the first fire after
        # setup has a reference point to compare against.
        self._termbase_db_last_snapshot = self._snapshot_termbase_db_state()

        try:
            watcher = QFileSystemWatcher(self)
            ok = watcher.addPath(db_path)
            if not ok:
                self.log(f"⚠️ Termbase DB watcher: addPath returned False for {db_path}; skipping auto-refresh")
                watcher.deleteLater()
                return
        except Exception as e:
            self.log(f"⚠️ Termbase DB watcher: failed to install ({e}); manual 🔄 still works")
            return

        # Debounce timer — restarted on every fileChanged hit; only
        # actually fires the rebuild once changes have stopped.
        debounce = QTimer(self)
        debounce.setSingleShot(True)
        debounce.setInterval(2000)  # 2 s quiet window
        debounce.timeout.connect(self._on_termbase_db_debounce_fire)

        def _on_changed(_path):
            # Some platforms (Windows in particular) remove the file
            # from the watcher's tracking list when it's
            # rewritten / replaced (which happens with SQLite WAL
            # checkpoints). Re-add the path defensively if it
            # disappeared so subsequent edits still fire events.
            try:
                if db_path not in watcher.files():
                    watcher.addPath(db_path)
            except Exception:
                pass
            debounce.start()  # restart the quiet window

        watcher.fileChanged.connect(_on_changed)

        self._termbase_db_watcher = watcher
        self._termbase_db_debounce_timer = debounce
        self._termbase_db_path = db_path
        self.log(f"👁️  Termbase DB auto-refresh: watching {_os.path.basename(db_path)} (2s debounce)")

    def _on_termbase_db_debounce_fire(self):
        """Debounce timer fired — DB has been quiet for 2 s.

        v1.10.72: just defers to ``force_refresh_matches``, which
        does its own snapshot-gating (only rebuilds the index when
        the termbase tables actually changed) and then runs the full
        per-segment refresh chain. The pre-v1.10.72 version did its
        own snapshot check here and called a separate
        ``_post_termbase_delete_refresh``; merging both into the F5
        path collapses every refresh entry point (F5, 🔄 button,
        auto-refresh) into a single code path.
        """
        try:
            self.force_refresh_matches()
        except Exception as e:
            self.log(f"⚠️ Termbase DB auto-refresh failed: {e}")

    def _force_termlens_display_redraw(self):
        """Force a full TermLens redraw using the F5 path
        (``force_refresh_matches``) when the lightweight refresh isn't
        enough.

        v1.10.71 background: a user reported that after clicking the
        v1.10.68 ↻ refresh button, pills for some terms (specifically
        ones added moments earlier) disappeared from TermLens for the
        current segment — even though the cache and index both
        contained them (the cell-select log line on the next segment
        switch confirmed ``TB=13`` matches still cached, but the
        widget only rendered 12 pills). Pressing F5 (which calls
        ``force_refresh_matches``) immediately restored all pills.

        The two paths look near-identical on paper but differ in
        thoroughness: the light path
        (``_refresh_termbase_display_for_current_segment``) clears
        only the per-segment termbase cache; ``force_refresh_matches``
        additionally clears the translation-matches cache, re-runs
        the TM search, builds a richer ``tb_list`` that includes
        ``target_synonyms``, and updates the Translation Results
        panel — and somewhere in that broader chain the TermLens
        widget rebuilds cleanly.

        Rather than chase the exact divergence (race conditions in
        the FlowLayoutPanel re-layout when controls are added/cleared
        rapidly are notoriously hard to pin down without a
        reproducer), this helper just calls into the F5 path. It's
        slightly heavier work — including a TM re-search — but the
        TM search is cached past the first hit and the user feedback
        is unambiguous that F5 works where the lightweight refresh
        doesn't. Correctness > a few hundred milliseconds.

        Best-effort: wrapped in try/except so the helper can never
        tear down whatever caller triggered it.
        """
        try:
            if hasattr(self, 'force_refresh_matches'):
                self.force_refresh_matches()
            else:
                # Defensive fallback if the F5 handler is somehow gone —
                # call the light path instead so something still happens.
                self._refresh_termbase_display_for_current_segment()
        except Exception as e:
            self.log(f"⚠️ Forced TermLens redraw failed: {e}")

    def _post_termbase_delete_refresh(self):
        """Run the full refresh chain after a term is deleted from any
        termbase, from any UI surface.

        v1.10.64 bug fix. Five different code paths can delete a term
        (Termbases tab "Delete Term" button, the Termbase Editor's
        "Delete Selected Term", the Edit Termbase Entry dialog's red
        Delete button, the right-click "Delete Termbase Entry" on a
        translation-results match card, and the right-click "Delete
        Termbase Entry" on a TermLens block). All five used to do an
        ``UPDATE termbase_terms`` followed by *partial* refresh logic
        — typically clearing ``self.termbase_cache`` and re-running
        ``find_termbase_matches_in_source`` for the current segment.

        The bug: ``find_termbase_matches_in_source`` reads from the
        **in-memory** ``self.termbase_index`` (built by
        ``_build_termbase_index``). That index still contains the
        just-deleted entry until it's rebuilt. So the user deletes a
        term, looks at TermLens, and sees the "deleted" term still
        there because the search hit comes from the stale index.

        This helper consolidates the post-delete refresh into one
        place. It:
         1. Clears ``self.termbase_cache`` (per-segment match cache).
         2. Rebuilds ``self.termbase_index`` (the lookup index).
         3. Re-runs the search for the current segment so TermLens
            and the source-cell highlight reflect the change.
         4. Refreshes the Termbases tab UI if it's open, so the
            "term count" badge and the terms grid update.

        Safe to call when nothing's open / no current segment / no
        termbase tab — every step is wrapped in best-effort try/except.
        """
        # v1.10.72: collapsed into ``force_refresh_matches``. Every
        # entry point that used to call this helper (the five delete
        # paths from v1.10.64) now gets the same treatment users get
        # from F5 / the 🔄 button: smart index rebuild (snapshot
        # check decides whether the rebuild step actually runs),
        # then the full per-segment cache clear + re-search + redraw.
        #
        # After a delete, the snapshot will always differ (one fewer
        # row in termbase_terms), so the rebuild will always fire
        # here — which is correct: the deleted term must be evicted
        # from the in-memory index or the next TermLens search will
        # surface it again as a phantom match. The snapshot gating
        # makes no-op refreshes cheap for other callers, but it's
        # not used to *skip* the rebuild after a known delete.
        try:
            self.force_refresh_matches()
        except Exception as e:
            self.log(f"⚠️ post-delete refresh failed: {e}")
        try:
            if hasattr(self, 'termbase_tab_refresh_callback') and self.termbase_tab_refresh_callback:
                self.termbase_tab_refresh_callback()
        except Exception as e:
            self.log(f"⚠️ post-delete refresh: tab refresh failed: {e}")

    def _quick_add_term_with_priority(self, target: str = "project"):
        """Quick add selected term pair to the Project or first Background glossary.

        Args:
            target: "project" for the Project glossary (priority=1),
                    "background" for the first writable background glossary.
                    Legacy int values (1, 2) are auto-mapped for backward compat.
        """
        # Legacy compat: map old int calls
        if target == 1:
            target = "project"
        elif target == 2:
            target = "background"

        # Get current row
        current_row = self.table.currentRow() if hasattr(self, 'table') and self.table else -1
        if current_row < 0:
            self.statusBar().showMessage("No segment selected", 3000)
            return

        # Get source selection – strip invisible markers before saving
        source_widget = self.table.cellWidget(current_row, 2)
        source_text = ""
        if source_widget and hasattr(source_widget, 'textCursor'):
            source_text = self.reverse_invisible_replacements(source_widget.textCursor().selectedText()).strip()

        # Get target selection – strip invisible markers before saving
        target_widget = self.table.cellWidget(current_row, 3)
        target_text = ""
        if target_widget and hasattr(target_widget, 'textCursor'):
            target_text = self.reverse_invisible_replacements(target_widget.textCursor().selectedText()).strip()

        # Validate selections
        if not source_text or not target_text:
            self.statusBar().showMessage("Select text in both Source and Target cells first", 3000)
            return

        # Check prerequisites
        if not hasattr(self, 'current_project') or not self.current_project:
            self.statusBar().showMessage("No project open", 3000)
            return

        if not hasattr(self, 'termbase_mgr') or not self.termbase_mgr:
            self.statusBar().showMessage("Termbase manager not initialized", 3000)
            return

        project_id = self.current_project.id if hasattr(self.current_project, 'id') else None
        if not project_id:
            self.statusBar().showMessage("No project ID - save project first", 3000)
            return

        # Find target glossary based on project/background
        target_termbase = None
        glossary_rank = 1 if target == "project" else 0  # For cache entry
        all_termbases = self.termbase_mgr.get_all_termbases()

        for tb in all_termbases:
            tb_id = tb['id']
            if not self.termbase_mgr.is_termbase_active(tb_id, project_id):
                continue  # Not activated
            priority = self.termbase_mgr.get_termbase_priority(tb_id, project_id)
            is_writable = not tb.get('read_only', True)
            is_project_tb = (priority == 1)

            if target == "project" and is_project_tb:
                target_termbase = tb
                break
            elif target == "background" and not is_project_tb and is_writable:
                target_termbase = tb
                break

        label = "Project termbase" if target == "project" else "Background termbase"
        if not target_termbase:
            self.statusBar().showMessage(f"No {label} found – check the 🏷️ Termbases tab", 3000)
            return
        
        self.log(f"⚡ Quick-adding term to {label} '{target_termbase['name']}': {source_text} → {target_text}")

        # "Similar Term Found" check (Trados parity). Only interrupts on a
        # same-source/same-target conflict; otherwise stays silent. When the
        # user merges, the new term is folded into the existing entry as a
        # synonym and we refresh and return without a fresh insert.
        decision, merged_ids = self._detect_and_merge_synonyms(
            source_text, target_text, [target_termbase])
        if decision == 'cancel':
            return
        if decision == 'failed':
            self.statusBar().showMessage(
                f"✗ Could not add as synonym (database locked?): {source_text} → {target_text}", 5000)
            return
        if decision == 'merged':
            self._play_sound_effect('glossary_term_added')
            self.statusBar().showMessage(
                f"✓ Merged as synonym into '{target_termbase['name']}': "
                f"{source_text} → {target_text}", 3000)
            try:
                with self.termbase_cache_lock:
                    self.termbase_cache.clear()
                self._build_termbase_index()
            except Exception as e:
                self.log(f"WARNING: Failed to rebuild termbase index after merge: {e}")
            self._refresh_termbase_display_for_current_segment()
            if (hasattr(self, 'termbase_tab_refresh_callback')
                    and self.termbase_tab_refresh_callback):
                self.termbase_tab_refresh_callback()
            return

        # v1.10.62: orient for termbase direction. Project termbases
        # are usually aligned with the project, but background
        # termbases (also reachable here in some configurations) may
        # not be. _orient_term_for_termbase is the single source of
        # truth for the swap decision.
        src_term, tgt_term, src_lang, tgt_lang = (
            self._orient_term_for_termbase(source_text, target_text, target_termbase)
        )

        try:
            term_id = self.termbase_mgr.add_term(
                termbase_id=target_termbase['id'],
                source_term=src_term,
                target_term=tgt_term,
                source_lang=src_lang,
                target_lang=tgt_lang,
                priority=99,  # Default priority for the term itself
                notes="",
                domain="",
                project="",
                client="",
                forbidden=False
            )
            
            if term_id:
                self._play_sound_effect('glossary_term_added')
                self.log(f"✓ Added to '{target_termbase['name']}': {source_text} → {target_text}")
                self.statusBar().showMessage(f"✓ Added to '{target_termbase['name']}': {source_text} → {target_text}", 3000)
                
                # OPTIMIZATION: Directly add the new term to cache and TermLens instead of full search
                # This avoids the 5-6 second delay from searching all words in long patent segments
                current_row = self.table.currentRow()
                if current_row >= 0 and self.current_project:
                    id_item = self.table.item(current_row, 0)
                    if id_item:
                        try:
                            segment_id = int(id_item.text())
                            segment = next((seg for seg in self.current_project.segments if seg.id == segment_id), None)
                            
                            if segment:
                                # Create match entry for the new term
                                is_project = (glossary_rank == 1)
                                new_match = {
                                    'source': source_text,
                                    'translation': target_text,
                                    'ranking': glossary_rank,
                                    'forbidden': False,
                                    'is_project_termbase': is_project,
                                    'term_id': term_id,
                                    'termbase_id': target_termbase['id'],
                                    'termbase_name': target_termbase['name'],
                                    'domain': '',
                                    'notes': '',
                                    'project': '',
                                    'client': '',
                                    'target_synonyms': []
                                }

                                # v1.9.182: add the new term to the in-memory
                                # index FIRST so the full recompute below picks
                                # it up.
                                import re
                                source_lower = source_text.lower().strip()
                                try:
                                    if _contains_cjk(source_lower):
                                        # CJK/Thai: substring match (see _contains_cjk)
                                        pattern = re.compile(re.escape(source_lower))
                                    elif any(c in source_lower for c in '.%,/-'):
                                        pattern = re.compile(r'(?<!\w)' + re.escape(source_lower) + r'(?!\w)')
                                    else:
                                        pattern = re.compile(r'\b' + re.escape(source_lower) + r'\b')
                                except re.error:
                                    pattern = None

                                # v1.10.73: include the new metadata + synonym
                                # fields in the optimisation-path index entry
                                # so it has the same shape as a full-rebuild
                                # entry. Empty defaults for everything that
                                # only gets populated by the dialog later —
                                # quick-add never sets definition / URL /
                                # abbreviations / synonyms, so they stay empty
                                # until the user either edits the entry or the
                                # next full rebuild picks them up from the DB.
                                index_entry = {
                                    'term_id': term_id,
                                    'source_term': source_text,
                                    'source_term_lower': source_lower,
                                    'target_term': target_text,
                                    'termbase_id': target_termbase['id'],
                                    'domain': '',
                                    'notes': '',
                                    'project': '',
                                    'client': '',
                                    'forbidden': False,
                                    'is_project_termbase': is_project,
                                    'termbase_name': target_termbase['name'],
                                    'ranking': glossary_rank,
                                    'definition': '',
                                    'url': '',
                                    'source_abbreviation': '',
                                    'target_abbreviation': '',
                                    'source_synonyms': [],
                                    'target_synonyms': [],
                                    'pattern': pattern,
                                    'abbreviation_variants': [],
                                    'source_synonym_patterns': [],
                                }
                                with self.termbase_index_lock:
                                    # Drop any stale entry for this term_id (re-add safety),
                                    # then append and re-sort longest-first for phrase matching.
                                    self.termbase_index = [
                                        e for e in self.termbase_index
                                        if e.get('term_id') != term_id
                                    ]
                                    self.termbase_index.append(index_entry)
                                    self.termbase_index.sort(key=lambda x: len(x['source_term_lower']), reverse=True)
                                    self.termbase_index_generation = getattr(self, 'termbase_index_generation', 0) + 1

                                # v1.10.232: recompute the FULL match set for this
                                # segment rather than seeding the cache with only
                                # the new term. The previous optimisation set
                                # termbase_cache[segment_id] = {new term} whenever
                                # the segment's raw cache was empty (common — the
                                # matches shown came from the translation-matches
                                # cache, a different store), which made every OTHER
                                # TermLens match vanish until F5. _search_termbase_in_memory
                                # is the fast (<1 ms) in-memory matcher — the old
                                # "avoid a 5-6 s search" concern was about the
                                # legacy per-word DB path, which is no longer used —
                                # so a full recompute here is cheap and correct, and
                                # now includes the term we just appended to the index.
                                # Use find_termbase_matches_in_source so this matches
                                # the navigation path exactly (strips invisible
                                # markers, guards the index build, dedups).
                                cached_matches = self.find_termbase_matches_in_source(segment.source)
                                with self.termbase_cache_lock:
                                    self.termbase_cache[segment_id] = cached_matches
                                self.log(f"⚡ Term added; recomputed {len(cached_matches)} match(es) for segment")

                                # Update TermLens widget with the full, refreshed match set
                                if (hasattr(self, 'termlens_widget') and self.termlens_widget) or (hasattr(self, 'termlens_widget_match') and self.termlens_widget_match):

                                    # Convert to list format for TermLens.
                                    # v1.10.73 (Tier 1 + Tier 2): forward the
                                    # new metadata + synonym fields so the
                                    # tooltip and alternative-translation
                                    # rendering pick them up consistently
                                    # with the other call sites that build
                                    # termbase_list.
                                    termbase_list = [
                                        {
                                            'source_term': match.get('source', ''),
                                            'target_term': match.get('translation', ''),
                                            'termbase_name': match.get('termbase_name', ''),
                                            'ranking': match.get('ranking', 0),
                                            'is_project_termbase': match.get('is_project_termbase', False),
                                            'term_id': match.get('term_id'),
                                            'termbase_id': match.get('termbase_id'),
                                            'notes': match.get('notes', ''),
                                            'definition': match.get('definition', ''),
                                            'domain': match.get('domain', ''),
                                            'url': match.get('url', ''),
                                            'source_abbreviation': match.get('source_abbreviation', ''),
                                            'target_abbreviation': match.get('target_abbreviation', ''),
                                            'source_synonyms': match.get('source_synonyms', []),
                                            'target_synonyms': match.get('target_synonyms', []),
                                            # v1.10.75 (Tier 3) flags
                                            'forbidden': match.get('forbidden', False),
                                            'is_nontranslatable': match.get('is_nontranslatable', False),
                                            # v1.10.75 (Tier 3c) abbreviation marker
                                            'matched_via_abbreviation': match.get('matched_via_abbreviation', False),
                                            # v1.10.309: full term forms
                                            'full_source_term': match.get('full_source_term', ''),
                                            'full_target_term': match.get('full_target_term', ''),
                                        }
                                        for match in cached_matches.values() if isinstance(match, dict)
                                    ]
                                    
                                    # Get NT matches
                                    nt_matches = self.find_nt_matches_in_source(segment.source)

                                    # Get status hint (although after adding a term, it should be fine)
                                    status_hint = self._get_termbase_status_hint()

                                    # Update both TermLens widgets (left and right)
                                    self._update_both_termlens(segment.source, termbase_list, nt_matches, status_hint)
                                    self.log(f"✅ Both TermLens widgets updated instantly with new term")
                                
                                # Update source cell highlighting with updated cache
                                # Call the highlighting function directly with the new matches
                                self.highlight_source_with_termbase(current_row, segment.source, cached_matches)
                                self.log(f"✅ Source highlighting updated with new term")

                                # Invalidate stale cache entries for ALL OTHER segments.
                                # The batch worker pre-populated these before the new term existed,
                                # so they may be missing the new term. Clear them now so the next
                                # navigation triggers a fresh lookup (or the batch worker refills them).
                                with self.termbase_cache_lock:
                                    stale_tb = [k for k in self.termbase_cache if k != segment_id]
                                    for k in stale_tb:
                                        del self.termbase_cache[k]
                                with self.translation_matches_cache_lock:
                                    stale_tm = [k for k in self.translation_matches_cache if k != segment_id]
                                    for k in stale_tm:
                                        del self.translation_matches_cache[k]

                                # Re-start the batch worker to re-populate all segments with the new
                                # term included (fast: ~0.3s for 659 segments in background).
                                self._start_termbase_batch_worker()
                                self.log(f"🔄 Cache invalidated for other segments; batch worker re-started")

                        except Exception as e:
                            self.log(f"⚠️  Quick cache update failed, falling back to full search: {e}")
                            # Fallback to full refresh if something goes wrong
                            self._refresh_termbase_display_for_current_segment()
                
                # OPTIMIZATION: Skip refreshing the entire termbase table UI (slow)
                # We're just adding ONE term to ONE glossary - no need to rebuild the whole table
                # The term count will update naturally when user switches segments or tabs
                # if hasattr(self, 'termbase_tab_refresh_callback') and self.termbase_tab_refresh_callback:
                #     self.termbase_tab_refresh_callback()
            else:
                self._play_sound_effect('glossary_term_duplicate')
                self.statusBar().showMessage(f"Term already exists in '{target_termbase['name']}'", 3000)
        except Exception as e:
            self.log(f"✗ Error adding term: {e}")
            self._play_sound_effect('glossary_term_error')
            self.statusBar().showMessage(f"Error adding term: {e}", 3000)

    def create_termbases_tab(self):
        """Create the Termbases tab - manage all termbases (global and project-specific)"""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(10, 10, 10, 10)
        
        # Header
        header = QLabel(self.tr("📚 Termbases"))
        header.setStyleSheet("font-size: 14px; font-weight: bold; margin-bottom: 10px;")
        layout.addWidget(header)

        # Description
        desc = QLabel(self.tr("Manage termbases for terminology searching. Activate/deactivate for current project."))
        desc.setStyleSheet("color: #666; font-size: 11px; margin-bottom: 10px;")
        layout.addWidget(desc)

        # Check if database is available
        if not (hasattr(self, 'db_manager') and self.db_manager):
            placeholder = QLabel(self.tr("Termbase Manager\n\nDatabase not initialized."))
            placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
            placeholder.setStyleSheet("color: #888; font-size: 12px;")
            layout.addWidget(placeholder, stretch=1)
            return tab
        
        # Import here to avoid issues if database not available
        from modules.termbase_manager import TermbaseManager
        termbase_mgr = TermbaseManager(self.db_manager, self.log)
        self.termbase_mgr = termbase_mgr  # Store for later use in get_termbase_code

        set_help_topic(tab, HelpTopics.GLOSSARY_BASICS)

        # ========== LEFT PANEL: Termbase List ==========
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 5, 0)
        
        # Search bar
        search_layout = QHBoxLayout()
        search_box = QLineEdit()
        search_box.setPlaceholderText(self.tr("Search termbases..."))
        search_box.setMaximumWidth(300)
        search_layout.addWidget(search_box)
        search_layout.addStretch()
        left_layout.addLayout(search_layout)
        
        # Help message
        help_msg = QLabel(
            "💡 <b>Termbases</b><br>"
            "Manage termbases for terminology searching. Activate/deactivate for current project.<br>"
            "• <b>Read</b> (green ✓): Termbase is used for terminology matching<br>"
            "• <b>Write</b> (blue ✓): Termbase is updated with new terms<br>"
            "• <b>Project</b> (pink ✓): Set as Project Termbase (highest priority, one at a time)<br>"
            "• <b>AI</b> (orange ✓): Send termbase terms to LLM with every translation (increases prompt size)<br>"
            "• <b>🔍 SuperLookup</b> (teal ✓): Termbase is searched by SuperLookup (independent of Read)<br>"
            "To see, add or edit a termbase's terms, right-click it → <b>Edit Terms…</b>"
        )
        help_msg.setWordWrap(True)
        help_msg.setStyleSheet("background-color: #e3f2fd; padding: 8px; border-radius: 4px; color: #1976d2;")
        left_layout.addWidget(help_msg)
        
        # Bulk action controls. They wrap onto a second line when the panel is
        # narrow: one fixed row of seven controls made the left panel at least
        # ~770 px wide, so the splitter could hardly move.
        from modules.termlens_widget import FlowLayout
        from modules.table_column_fit import FillColumns

        def _side_by_side(*widgets):
            group = QWidget()
            row = QHBoxLayout(group)
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(4)
            for w in widgets:
                row.addWidget(w)
            return group

        tb_bulk_container = QWidget()
        tb_bulk_layout = FlowLayout(tb_bulk_container, margin=0, spacing=6)

        tb_read_header_checkbox = CheckmarkCheckBox(self.tr("Select All Read"))

        # v1.10.173: explicit "Clear All" button next to the checkbox.
        # The checkbox toggles all-on/all-off, but for users it reads as
        # an indicator rather than a button — a dedicated Clear All is
        # easier to spot.
        tb_clear_all_read_btn = QPushButton(self.tr("Clear All Read"))
        tb_clear_all_read_btn.setToolTip(self.tr("Uncheck the Read flag on every termbase"))
        tb_clear_all_read_btn.clicked.connect(lambda: toggle_all_tb_read(False))

        tb_write_header_checkbox = BlueCheckmarkCheckBox("Select All Write")

        tb_clear_all_write_btn = QPushButton(self.tr("Clear All Write"))
        tb_clear_all_write_btn.setToolTip(self.tr("Uncheck the Write flag on every termbase"))
        tb_clear_all_write_btn.clicked.connect(lambda: toggle_all_tb_write(False))

        # v1.10.247: SuperLookup bulk toggles (include/exclude every termbase
        # from SuperLookup). Late-binding via toggle_all_tb_superlookup below.
        tb_superlookup_header_checkbox = TealCheckmarkCheckBox(self.tr("Select All SuperLookup"))

        tb_clear_all_superlookup_btn = QPushButton(self.tr("Clear All SuperLookup"))
        tb_clear_all_superlookup_btn.setToolTip(self.tr("Uncheck the SuperLookup flag on every termbase (exclude all from SuperLookup)"))
        tb_clear_all_superlookup_btn.clicked.connect(lambda: toggle_all_tb_superlookup(False))

        tb_bulk_layout.addWidget(_side_by_side(
            QLabel(self.tr("Quick Actions:")), tb_read_header_checkbox, tb_clear_all_read_btn))
        tb_bulk_layout.addWidget(_side_by_side(tb_write_header_checkbox, tb_clear_all_write_btn))
        tb_bulk_layout.addWidget(_side_by_side(tb_superlookup_header_checkbox, tb_clear_all_superlookup_btn))
        left_layout.addWidget(tb_bulk_container)
        
        # Termbase list with table
        termbase_table = QTableWidget()
        self.termbase_table = termbase_table  # Store for external access (Superlookup navigation)
        # v1.10.247: added 🔍 SuperLookup column (per-termbase toggle for
        # SuperLookup inclusion, independent of Read). The 🎤 Voice column
        # (v1.10.28, dictation vocabulary) went with the dictation.
        termbase_table.setColumnCount(9)
        termbase_table.setHorizontalHeaderLabels([
            "Type", "Name", "Languages", "Terms",
            "Read", "Write", "Project", "AI", "🔍 SuperLookup",
        ])
        termbase_table.horizontalHeader().setStretchLastSection(False)
        termbase_table.setColumnWidth(0, 80)   # Type (Project/Background)
        termbase_table.setColumnWidth(1, 180)  # Name
        termbase_table.setColumnWidth(2, 100)  # Languages
        termbase_table.setColumnWidth(3, 50)   # Terms
        termbase_table.setColumnWidth(4, 50)   # Read checkbox
        termbase_table.setColumnWidth(5, 50)   # Write checkbox
        termbase_table.setColumnWidth(6, 60)   # Priority
        termbase_table.setColumnWidth(7, 40)   # AI checkbox
        termbase_table.setColumnWidth(8, 100)  # 🔍 SuperLookup checkbox
        FillColumns(termbase_table, (1,), minimum=180)  # Name takes the spare width
        termbase_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        termbase_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        termbase_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)  # Disable inline editing
        # v1.10.169: click-header-to-sort on the data columns (Type, Name,
        # Languages, Terms). The checkbox columns (Read / Write / Project /
        # AI / SuperLookup) can be header-clicked too, but they don't carry sortable
        # data — Qt's QTableWidget sort orders cellWidgets by their row's
        # text item, which for those columns is None, so clicking them is
        # a visual no-op. setSortingEnabled is flipped off + back on around
        # the rebuild loop (see refresh_termbase_list) so cell population
        # doesn't trigger re-sorts during insertion.
        termbase_table.setSortingEnabled(True)
        
        # Enable context menu for termbase table
        termbase_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        termbase_table.customContextMenuRequested.connect(
            lambda pos: self._show_termbase_context_menu(pos, termbase_table, termbase_mgr, refresh_termbase_list)
        )

        # Double-clicking a termbase opens its terms, like the right-click
        # menu's Edit Terms… (its default action). Not on the checkbox
        # columns: a checkbox passes a double-click on to the table, and
        # quickly ticking one off and on again must not open a window.
        def open_terms_of_row(row, column):
            from PyQt6.QtWidgets import QAbstractButton
            widget = termbase_table.cellWidget(row, column)
            if widget is not None and (isinstance(widget, QAbstractButton)
                                       or widget.findChild(QAbstractButton) is not None):
                return
            name_item = termbase_table.item(row, 1)
            if name_item is not None:
                self._open_termbase_terms(name_item.data(Qt.ItemDataRole.UserRole), name_item.text())

        termbase_table.cellDoubleClicked.connect(open_terms_of_row)
        
        # NOTE: do NOT capture the current project here. This tab is built once
        # at startup, before any project is open, so anything derived from it
        # would be permanently None. Every consumer resolves the project live
        # instead (see refresh_termbase_list and the button handlers below).

        # Filter function for search box
        def filter_termbase_table(search_text):
            """Filter termbase table rows by name"""
            search_text = search_text.lower().strip()
            for row in range(termbase_table.rowCount()):
                name_item = termbase_table.item(row, 1)
                if name_item:
                    name = name_item.text().lower()
                    # Show row if search text is in the name, or if search is empty
                    termbase_table.setRowHidden(row, search_text and search_text not in name)
        
        # Connect search box to filter function
        search_box.textChanged.connect(filter_termbase_table)
        
        # Connect header checkboxes to toggle all
        def toggle_all_tb_read(checked):
            # v1.10.170: hardened against the "rebuild-during-iteration"
            # crash. The old version called setChecked() on every row's
            # checkbox, which fired on_read_toggle, which called
            # deactivate_termbase + _build_termbase_index + a full
            # refresh_termbase_list — destroying the very checkboxes
            # the outer loop was about to touch. With ≈50 termbases this
            # back-to-back rebuild thrash hard-crashed Qt.
            #
            # Do the DB write directly in a single pass, then trigger
            # exactly ONE refresh at the end.
            curr_proj = self.current_project if hasattr(self, 'current_project') else None
            curr_proj_id = curr_proj.id if (curr_proj and hasattr(curr_proj, 'id')) else 0
            try:
                for tb in (termbase_mgr.get_all_termbases() or []):
                    tb_id = tb.get('id')
                    if tb_id is None:
                        continue
                    if checked:
                        termbase_mgr.activate_termbase(tb_id, curr_proj_id)
                    else:
                        termbase_mgr.deactivate_termbase(tb_id, curr_proj_id)
            except Exception as e:
                self.log(f"⚠️ Bulk Read toggle failed mid-way: {e}")

            # Single index rebuild + single table rebuild.
            with self.termbase_cache_lock:
                self.termbase_cache.clear()
            try:
                self._build_termbase_index()
            except Exception as e:
                self.log(f"⚠️ Termbase index rebuild failed: {e}")
            refresh_termbase_list()

        def toggle_all_tb_write(checked):
            # v1.10.170: same fix as toggle_all_tb_read — bulk DB write +
            # single refresh at the end, instead of triggering 46 cascading
            # per-row handlers. Write doesn't refresh per-row in the
            # per-row handler so the crash is much less likely here, but
            # the consistent pattern is safer either way.
            try:
                for tb in (termbase_mgr.get_all_termbases() or []):
                    tb_id = tb.get('id')
                    if tb_id is None:
                        continue
                    # checked = writable, so read_only is the opposite.
                    termbase_mgr.set_termbase_read_only(tb_id, not checked)
            except Exception as e:
                self.log(f"⚠️ Bulk Write toggle failed mid-way: {e}")
            refresh_termbase_list()

        # v1.10.247: bulk SuperLookup toggle. Single DB pass + one refresh.
        # No index rebuild needed — SuperLookup inclusion doesn't affect the
        # in-memory termbase matching index (that follows the Read flag).
        def toggle_all_tb_superlookup(checked):
            try:
                for tb in (termbase_mgr.get_all_termbases() or []):
                    tb_id = tb.get('id')
                    if tb_id is None:
                        continue
                    termbase_mgr.set_termbase_superlookup_enabled(tb_id, bool(checked))
            except Exception as e:
                self.log(f"⚠️ Bulk SuperLookup toggle failed mid-way: {e}")
            refresh_termbase_list()

        tb_read_header_checkbox.toggled.connect(toggle_all_tb_read)
        tb_write_header_checkbox.toggled.connect(toggle_all_tb_write)
        tb_superlookup_header_checkbox.toggled.connect(toggle_all_tb_superlookup)
        
        # ========== TERMS WINDOW ==========
        # The terms of one termbase, in a window of their own (right-click a
        # termbase → Edit Terms…). Until the 4 Nov 2026 change this panel sat
        # beside the termbase list, split by a divider that could hardly move.
        # One window, reused: opening another termbase loads it into the same
        # window. It is a child of the main window, so it closes with it.
        right_panel = QWidget(self, Qt.WindowType.Window)
        terms_window = right_panel
        self.termbase_terms_window = terms_window
        terms_window.resize(1300, 800)
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(10, 10, 10, 10)
        
        # Terms header with termbase name
        terms_header_layout = QHBoxLayout()
        terms_header = QLabel(self.tr("📝 Terms"))
        terms_header.setStyleSheet("font-size: 13px; font-weight: bold;")
        terms_header_layout.addWidget(terms_header)
        
        selected_tb_label = QLabel(self.tr("Select a termbase to view/edit terms"))
        selected_tb_label.setStyleSheet("color: #666; font-style: italic;")
        # A long termbase name is cut off (full name in the tooltip) rather
        # than setting the panel's minimum width.
        selected_tb_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        terms_header_layout.addWidget(selected_tb_label, 1)
        right_layout.addLayout(terms_header_layout)
        
        # Search/filter for terms
        terms_search_layout = QHBoxLayout()
        terms_search_box = QLineEdit()
        terms_search_box.setPlaceholderText(self.tr("Filter terms..."))
        terms_search_box.setMaximumWidth(250)
        terms_search_layout.addWidget(terms_search_box)
        self.terms_search_box = terms_search_box  # Store for external access
        terms_search_layout.addStretch()
        right_layout.addLayout(terms_search_layout)
        
        # ========== PAGINATION CONTROLS ==========
        # State variables for pagination (using lists to allow modification in nested functions)
        terms_page_size = [100]  # Terms per page
        terms_current_page = [0]  # Current page (0-indexed)
        terms_total_count = [0]  # Total terms in current termbase
        terms_filter_text = ['']  # Current filter text
        # v1.10.65: configurable ORDER BY clause for the terms query so
        # users can flip the grid between "alphabetical" (default) and
        # "newest first" — the latter is essential for finding/fixing
        # a batch of terms you just added but realised were wrong.
        # Whitelisted strings only (we inline them into the SQL); see
        # _SORT_OPTIONS below.
        terms_sort_order = ['source_term COLLATE NOCASE ASC']

        # The controls wrap onto a second line when the panel is narrow (one
        # fixed row made the Terms panel at least ~700 px wide).
        pagination_container = QWidget()
        pagination_layout = FlowLayout(pagination_container, margin=0, spacing=12)

        # Page size selector
        page_size_label = QLabel(self.tr("Show:"))
        page_size_combo = QComboBox()
        page_size_combo.addItems(["50", "100", "250", "500", "All"])
        page_size_combo.setCurrentText("100")
        page_size_combo.setFixedWidth(70)
        pagination_layout.addWidget(_side_by_side(page_size_label, page_size_combo))

        # Sort selector (v1.10.65). Label → SQL ORDER BY clause.
        # The order-by strings are NEVER user-supplied; they're keyed
        # off a fixed dropdown, so inlining them into the SELECT is
        # safe. (Parameterising ORDER BY would require sqlite3
        # gymnastics for no real benefit here.)
        _SORT_OPTIONS = [
            ("Source term (A→Z)",      "source_term COLLATE NOCASE ASC"),
            ("Source term (Z→A)",      "source_term COLLATE NOCASE DESC"),
            ("Created (newest first)", "created_date DESC, id DESC"),
            ("Created (oldest first)", "created_date ASC, id ASC"),
            ("Modified (newest first)", "modified_date DESC, id DESC"),
            ("Modified (oldest first)", "modified_date ASC, id ASC"),
        ]
        sort_label = QLabel(self.tr("Sort:"))
        sort_combo = QComboBox()
        for label, _clause in _SORT_OPTIONS:
            sort_combo.addItem(label)
        sort_combo.setFixedWidth(180)
        sort_combo.setToolTip(
            "Choose how terms are ordered in the grid.\n"
            "Tip: 'Created (newest first)' is the quickest way to find\n"
            "(and fix or delete) a batch of terms you just added."
        )
        pagination_layout.addWidget(_side_by_side(sort_label, sort_combo))

        def on_sort_changed(idx):
            if 0 <= idx < len(_SORT_OPTIONS):
                clause = _SORT_OPTIONS[idx][1]
                terms_sort_order[0] = clause
                terms_current_page[0] = 0  # Reset to first page so newest jumps into view
                # v1.10.66: mirror the dropdown choice on the column
                # header sort indicator so the two stay visually in sync.
                # Map: option index → (column index, ascending).
                # 0:Source A→Z (col 0 ASC), 1:Source Z→A (col 0 DESC),
                # 2:Created newest (col 7 DESC), 3:Created oldest (col 7 ASC),
                # 4/5:Modified (no column → leave indicator where it is).
                _DROPDOWN_TO_HEADER = {
                    0: (0, True),
                    1: (0, False),
                    2: (7, False),
                    3: (7, True),
                }
                hdr = _DROPDOWN_TO_HEADER.get(idx)
                if hdr is not None:
                    _set_header_indicator(hdr[0], hdr[1])
                if current_terms_tb_id[0]:
                    load_terms_page()
        sort_combo.currentIndexChanged.connect(on_sort_changed)

        # Navigation buttons
        first_page_btn = QPushButton("⏮")
        first_page_btn.setFixedWidth(30)
        first_page_btn.setToolTip(self.tr("First page"))
        prev_page_btn = QPushButton("◀")
        prev_page_btn.setFixedWidth(30)
        prev_page_btn.setToolTip(self.tr("Previous page"))
        
        page_info_label = QLabel(self.tr("Page 1 of 1"))
        page_info_label.setStyleSheet("padding: 0 10px;")
        
        next_page_btn = QPushButton("▶")
        next_page_btn.setFixedWidth(30)
        next_page_btn.setToolTip(self.tr("Next page"))
        last_page_btn = QPushButton("⏭")
        last_page_btn.setFixedWidth(30)
        last_page_btn.setToolTip(self.tr("Last page"))
        
        # Total count label
        total_terms_label = QLabel(self.tr("(0 terms total)"))
        total_terms_label.setStyleSheet("color: #666;")

        pagination_layout.addWidget(_side_by_side(
            first_page_btn, prev_page_btn, page_info_label, next_page_btn, last_page_btn,
            total_terms_label))
        right_layout.addWidget(pagination_container)
        
        # Terms table - columns: Source, Target, Domain, Notes, Project, Client, Forbidden, Created, Delete
        # v1.10.65: added the **Created** column (read-only timestamp,
        # rendered yyyy-mm-dd HH:MM) so users can flip the sort
        # dropdown to "Created (newest first)" and immediately see —
        # and triage — terms they just added but realised were wrong.
        # The Delete button moved from col 7 → col 8.
        terms_table = QTableWidget()
        # Issue #114: read-only Synonyms column at 8; Delete moved to 9.
        terms_table.setColumnCount(10)
        terms_table.setHorizontalHeaderLabels(["Source Term", "Target Term", "Domain", "Notes", "Project", "Client", "Forbidden", "Created", "Synonyms", ""])
        terms_table.horizontalHeader().setStretchLastSection(False)
        # Source and Target (cols 0, 1) are sized by FillColumns below.
        terms_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)  # Domain
        terms_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Interactive)  # Notes
        terms_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Interactive)  # Project
        terms_table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.Interactive)  # Client
        terms_table.horizontalHeader().setSectionResizeMode(6, QHeaderView.ResizeMode.Interactive)  # Forbidden
        terms_table.horizontalHeader().setSectionResizeMode(7, QHeaderView.ResizeMode.Interactive)  # Created
        terms_table.horizontalHeader().setSectionResizeMode(8, QHeaderView.ResizeMode.Interactive)  # Synonyms
        terms_table.horizontalHeader().setSectionResizeMode(9, QHeaderView.ResizeMode.Fixed)        # Delete button
        terms_table.setColumnWidth(2, 100)  # Domain
        terms_table.setColumnWidth(3, 120)  # Notes
        terms_table.setColumnWidth(4, 100)  # Project
        terms_table.setColumnWidth(5, 100)  # Client
        terms_table.setColumnWidth(6, 70)   # Forbidden
        terms_table.setColumnWidth(7, 130)  # Created (fits "2026-05-17 14:32" with a little slack)
        terms_table.setColumnWidth(8, 160)  # Synonyms
        terms_table.setColumnWidth(9, 30)   # Delete button
        terms_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        # v1.10.66: click-to-sort on every data column. We do NOT call
        # setSortingEnabled(True) — that would tell Qt to sort the
        # *visible page* in-place (12 rows of 50,000), which is the
        # wrong answer. Instead we just show the sort indicator and
        # re-issue the SQL with a new ORDER BY when a header is
        # clicked. Sort the entire termbase, not just the page that
        # happens to be loaded.
        terms_table.horizontalHeader().setSortIndicatorShown(True)
        terms_table.horizontalHeader().setSectionsClickable(True)
        # Column-index → (SQL fragment, dropdown index if any). Columns
        # 8 (Synonyms) and 9 (Delete button) are not sortable — we filter
        # clicks on them below. The fragments use COLLATE NOCASE on text columns so
        # the alphabetical sort isn't case-sensitive (matches the
        # existing dropdown convention).
        _COL_SORT = {
            0: ('source_term COLLATE NOCASE',   0),  # → dropdown "Source term (A→Z)"
            1: ('target_term COLLATE NOCASE',   None),
            2: ('domain COLLATE NOCASE',        None),
            3: ('notes COLLATE NOCASE',         None),
            4: ('project COLLATE NOCASE',       None),
            5: ('client COLLATE NOCASE',        None),
            6: ('forbidden',                    None),  # 0/1 numeric
            7: ('created_date',                 2),     # → dropdown "Created (newest first)"
        }
        # Source and Target share the spare width, at least 160 px each; when
        # the panel is narrow the grid scrolls sideways instead. (They used
        # to be Stretch columns, which shrank to ~40 px.)
        FillColumns(terms_table, (0, 1), minimum=160)
        right_layout.addWidget(terms_table, stretch=1)

        # Store current termbase ID for the terms panel
        current_terms_tb_id = [None]  # Use list to allow modification in nested function

        def _set_header_indicator(col: int, ascending: bool):
            """Show the up/down arrow on the clicked header column.

            We disconnect the dropdown's signal first so changing the
            dropdown's index here doesn't recurse back into a reload.
            """
            from PyQt6.QtCore import Qt as _Qt
            order = _Qt.SortOrder.AscendingOrder if ascending else _Qt.SortOrder.DescendingOrder
            terms_table.horizontalHeader().setSortIndicator(col, order)

        def _sync_dropdown_to_sort(sort_clause: str):
            """Best-effort sync: if the current sort matches one of the
            dropdown options, surface that option as selected. Otherwise
            leave the dropdown where it is — it just reads as a 'hint
            of the last preset chosen' rather than the current sort.
            """
            for idx, (_label, clause) in enumerate(_SORT_OPTIONS):
                if clause.startswith(sort_clause):
                    sort_combo.blockSignals(True)
                    sort_combo.setCurrentIndex(idx)
                    sort_combo.blockSignals(False)
                    return
            # No exact match — clear visual selection by selecting
            # nothing (cosmetic only; sort still applies).
            pass

        def on_header_clicked(col: int):
            if col not in _COL_SORT:
                return  # Delete button column — ignore
            sql_col, _dropdown_idx = _COL_SORT[col]
            # Toggle direction if same column was clicked again.
            current = terms_sort_order[0]
            if current.startswith(sql_col):
                ascending = ' DESC' in current  # was DESC, flip to ASC
            else:
                # Sensible defaults: text/forbidden columns ascending,
                # date columns descending (newest first — that's what
                # users almost always want for "Created" / "Modified").
                ascending = sql_col not in ('created_date', 'modified_date')
            direction = 'ASC' if ascending else 'DESC'
            # Tiebreak by id so the sort is deterministic across pages
            # when many rows share the same value (esp. timestamps for
            # bulk-imported termbases).
            terms_sort_order[0] = f"{sql_col} {direction}, id {direction}"
            terms_current_page[0] = 0
            _set_header_indicator(col, ascending)
            _sync_dropdown_to_sort(f"{sql_col} {direction}")
            if current_terms_tb_id[0]:
                load_terms_page()

        terms_table.horizontalHeader().sectionClicked.connect(on_header_clicked)

        # Show the initial indicator on column 0 (matches the default
        # terms_sort_order = source_term ASC).
        _set_header_indicator(0, True)

        def filter_terms_table(search_text):
            """Filter terms table rows by source or target term"""
            search_text = search_text.lower().strip()
            terms_filter_text[0] = search_text
            # When filter changes, reset to page 0 and reload
            terms_current_page[0] = 0
            if current_terms_tb_id[0]:
                load_terms_page()
        
        terms_search_box.textChanged.connect(filter_terms_table)
        
        def update_pagination_ui():
            """Update pagination controls based on current state"""
            page_size = terms_page_size[0]
            total = terms_total_count[0]
            current = terms_current_page[0]
            
            if page_size == 0:  # "All" selected
                total_pages = 1
            else:
                total_pages = max(1, (total + page_size - 1) // page_size)
            
            page_info_label.setText(f"Page {current + 1} of {total_pages}")
            total_terms_label.setText(f"({total:,} terms total)")
            
            # Enable/disable navigation buttons
            first_page_btn.setEnabled(current > 0)
            prev_page_btn.setEnabled(current > 0)
            next_page_btn.setEnabled(current < total_pages - 1)
            last_page_btn.setEnabled(current < total_pages - 1)
        
        def load_terms_page():
            """Load current page of terms from the database"""
            if current_terms_tb_id[0] is None:
                return
            
            terms_table.setRowCount(0)
            terms_table.blockSignals(True)
            
            try:
                tb_id = current_terms_tb_id[0]
                filter_text = terms_filter_text[0]
                page_size = terms_page_size[0]
                offset = terms_current_page[0] * page_size if page_size > 0 else 0
                
                # v1.10.65: ORDER BY is driven by the Sort dropdown
                # (terms_sort_order). The clause is a whitelisted string
                # from a fixed list (_SORT_OPTIONS), never user-supplied,
                # so inlining it into the SQL is safe.
                order_clause = terms_sort_order[0]

                # Build query with optional filter. SELECT now also
                # returns created_date so the v1.10.65 Created column
                # can render the timestamp.
                if filter_text:
                    # Count filtered results
                    self.db_manager.cursor.execute(
                        """SELECT COUNT(*) FROM termbase_terms
                           WHERE termbase_id = CAST(? AS TEXT)
                           AND (LOWER(source_term) LIKE ? OR LOWER(target_term) LIKE ?)""",
                        (tb_id, f'%{filter_text}%', f'%{filter_text}%')
                    )
                    terms_total_count[0] = self.db_manager.cursor.fetchone()[0]

                    # Get filtered page
                    if page_size > 0:
                        self.db_manager.cursor.execute(
                            f"""SELECT id, source_term, target_term, domain, notes, project, client, forbidden, created_date
                               FROM termbase_terms
                               WHERE termbase_id = CAST(? AS TEXT)
                               AND (LOWER(source_term) LIKE ? OR LOWER(target_term) LIKE ?)
                               ORDER BY {order_clause} LIMIT ? OFFSET ?""",
                            (tb_id, f'%{filter_text}%', f'%{filter_text}%', page_size, offset)
                        )
                    else:  # All
                        self.db_manager.cursor.execute(
                            f"""SELECT id, source_term, target_term, domain, notes, project, client, forbidden, created_date
                               FROM termbase_terms
                               WHERE termbase_id = CAST(? AS TEXT)
                               AND (LOWER(source_term) LIKE ? OR LOWER(target_term) LIKE ?)
                               ORDER BY {order_clause}""",
                            (tb_id, f'%{filter_text}%', f'%{filter_text}%')
                        )
                else:
                    # Count all results
                    self.db_manager.cursor.execute(
                        "SELECT COUNT(*) FROM termbase_terms WHERE termbase_id = CAST(? AS TEXT)",
                        (tb_id,)
                    )
                    terms_total_count[0] = self.db_manager.cursor.fetchone()[0]

                    # Get page
                    if page_size > 0:
                        self.db_manager.cursor.execute(
                            f"""SELECT id, source_term, target_term, domain, notes, project, client, forbidden, created_date
                               FROM termbase_terms WHERE termbase_id = CAST(? AS TEXT)
                               ORDER BY {order_clause} LIMIT ? OFFSET ?""",
                            (tb_id, page_size, offset)
                        )
                    else:  # All
                        self.db_manager.cursor.execute(
                            f"""SELECT id, source_term, target_term, domain, notes, project, client, forbidden, created_date
                               FROM termbase_terms WHERE termbase_id = CAST(? AS TEXT) ORDER BY {order_clause}""",
                            (tb_id,)
                        )
                
                terms = self.db_manager.cursor.fetchall()
                terms_table.setRowCount(len(terms))
                # Synonyms for the whole page in one query (issue #114)
                page_synonyms = termbase_mgr.synonyms_for_terms([t[0] for t in terms])

                for row, term in enumerate(terms):
                    term_id, source, target, domain, notes, project, client, forbidden, created_raw = term

                    # Source term (editable)
                    source_item = QTableWidgetItem(source or "")
                    source_item.setData(Qt.ItemDataRole.UserRole, term_id)
                    terms_table.setItem(row, 0, source_item)

                    # Target term (editable)
                    target_item = QTableWidgetItem(target or "")
                    terms_table.setItem(row, 1, target_item)

                    # Domain (editable)
                    domain_item = QTableWidgetItem(domain or "")
                    terms_table.setItem(row, 2, domain_item)

                    # Notes (editable)
                    notes_item = QTableWidgetItem(notes or "")
                    terms_table.setItem(row, 3, notes_item)

                    # Project (editable)
                    project_item = QTableWidgetItem(project or "")
                    terms_table.setItem(row, 4, project_item)

                    # Client (editable)
                    client_item = QTableWidgetItem(client or "")
                    terms_table.setItem(row, 5, client_item)

                    # Forbidden (checkbox)
                    forbidden_checkbox = CheckmarkCheckBox()
                    forbidden_checkbox.setChecked(bool(forbidden))
                    forbidden_checkbox.setToolTip(self.tr("Mark term as forbidden (do not use)"))
                    forbidden_checkbox.toggled.connect(lambda checked, tid=term_id: save_forbidden_state(tid, checked))
                    terms_table.setCellWidget(row, 6, forbidden_checkbox)

                    # Created timestamp (read-only). SQLite returns
                    # CURRENT_TIMESTAMP values as "YYYY-MM-DD HH:MM:SS"
                    # strings (UTC). Trim the seconds for compactness;
                    # the full value goes into the tooltip for anyone
                    # who needs the precise instant. Cell is marked
                    # non-editable so the save_term_edit handler can
                    # safely ignore changes here.
                    created_str = str(created_raw) if created_raw else ""
                    created_display = created_str[:16] if len(created_str) >= 16 else created_str
                    created_item = QTableWidgetItem(created_display)
                    if created_str:
                        created_item.setToolTip(f"Created: {created_str} (UTC)")
                    created_item.setFlags(created_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                    terms_table.setItem(row, 7, created_item)

                    # Synonyms (read-only; issue #114): "source → target"
                    syns = page_synonyms.get(int(term_id), {})
                    src_syns, tgt_syns = syns.get('source', []), syns.get('target', [])
                    syn_text = ""
                    if src_syns or tgt_syns:
                        syn_text = f"{', '.join(src_syns) or '–'}  →  {', '.join(tgt_syns) or '–'}"
                    syn_item = QTableWidgetItem(syn_text)
                    syn_item.setFlags(syn_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                    if syn_text:
                        syn_item.setToolTip(
                            f"Source synonyms: {', '.join(src_syns) or '(none)'}\n"
                            f"Target synonyms: {', '.join(tgt_syns) or '(none)'}")
                    terms_table.setItem(row, 8, syn_item)

                    # Delete button (col 7 → 8 in v1.10.65, → 9 with the Synonyms column)
                    delete_btn = QPushButton("🗑")
                    delete_btn.setFixedSize(24, 24)
                    delete_btn.setToolTip(self.tr("Delete this term"))
                    delete_btn.setStyleSheet("QPushButton { border: none; } QPushButton:hover { background-color: #ffcccc; }")
                    delete_btn.clicked.connect(lambda checked, tid=term_id: delete_term(tid))
                    terms_table.setCellWidget(row, 9, delete_btn)
                
                update_pagination_ui()
                
            except Exception as e:
                self.log(f"⚠️ Error loading terms: {e}")
            finally:
                terms_table.blockSignals(False)
        
        def show_termbase_name(tb_name):
            selected_tb_label.setText(f"<b>{tb_name}</b>")
            selected_tb_label.setToolTip(tb_name)
            terms_window.setWindowTitle(f"Terms – {tb_name}")

        def load_terms_for_termbase(tb_id, tb_name):
            """Load terms from selected termbase into the terms table (resets to page 1)"""
            current_terms_tb_id[0] = tb_id
            terms_current_page[0] = 0  # Reset to first page
            terms_filter_text[0] = ''  # Clear filter
            terms_search_box.clear()  # Clear search box
            show_termbase_name(tb_name)
            selected_tb_label.setStyleSheet("color: #1976d2; font-weight: bold;")

            # Update Source/Target column headers with the termbase's actual
            # base-language names (e.g. "Dutch" instead of "Source Term",
            # stripping region variants like "nl-NL" → "Dutch").
            try:
                tb_info = termbase_mgr.get_termbase(tb_id)
                if tb_info:
                    src_code = tb_info.get('source_lang') or ''
                    tgt_code = tb_info.get('target_lang') or ''
                    src_header = self._normalize_language_code(src_code) if src_code else "Source Term"
                    tgt_header = self._normalize_language_code(tgt_code) if tgt_code else "Target Term"
                    terms_table.setHorizontalHeaderLabels([
                        src_header, tgt_header, "Domain", "Notes",
                        "Project", "Client", "Forbidden", "Created", "Synonyms", ""
                    ])
            except Exception:
                pass

            load_terms_page()
            self.log(f"📝 Loaded {terms_total_count[0]:,} terms from termbase '{tb_name}'")
        
        # Pagination button handlers
        def go_first_page():
            terms_current_page[0] = 0
            load_terms_page()
        
        def go_prev_page():
            if terms_current_page[0] > 0:
                terms_current_page[0] -= 1
                load_terms_page()
        
        def go_next_page():
            page_size = terms_page_size[0]
            if page_size > 0:
                total_pages = (terms_total_count[0] + page_size - 1) // page_size
                if terms_current_page[0] < total_pages - 1:
                    terms_current_page[0] += 1
                    load_terms_page()
        
        def go_last_page():
            page_size = terms_page_size[0]
            if page_size > 0:
                total_pages = max(1, (terms_total_count[0] + page_size - 1) // page_size)
                terms_current_page[0] = total_pages - 1
                load_terms_page()
        
        def on_page_size_changed(text):
            if text == "All":
                terms_page_size[0] = 0
            else:
                terms_page_size[0] = int(text)
            terms_current_page[0] = 0  # Reset to first page
            load_terms_page()
        
        first_page_btn.clicked.connect(go_first_page)
        prev_page_btn.clicked.connect(go_prev_page)
        next_page_btn.clicked.connect(go_next_page)
        last_page_btn.clicked.connect(go_last_page)
        page_size_combo.currentTextChanged.connect(on_page_size_changed)

        def save_forbidden_state(term_id, forbidden):
            """Save the forbidden state of a term"""
            try:
                # v1.10.74: bump modified_date so the snapshot-gated
                # auto-refresh in force_refresh_matches sees the
                # change. See modules/termbase_entry_editor.save_term
                # for the full rationale.
                with rollback_if_failed(self.db_manager.connection, self.log):
                    self.db_manager.cursor.execute(
                        "UPDATE termbase_terms SET forbidden = ?, "
                        "modified_date = CURRENT_TIMESTAMP WHERE id = ?",
                        (1 if forbidden else 0, term_id)
                    )
                    self.db_manager.connection.commit()
                # Clear termbase cache
                with self.termbase_cache_lock:
                    self.termbase_cache.clear()
            except Exception as e:
                self.log(f"⚠️ Error saving forbidden state: {e}")
        
        def delete_term(term_id):
            """Delete a term from the termbase"""
            reply = QMessageBox.question(
                terms_window, "Delete Term",
                "Are you sure you want to delete this term?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            if reply == QMessageBox.StandardButton.Yes:
                try:
                    with rollback_if_failed(self.db_manager.connection, self.log):
                        self.db_manager.cursor.execute("DELETE FROM termbase_terms WHERE id = ?", (term_id,))
                        self.db_manager.connection.commit()
                    self.log(f"🗑️ Deleted term {term_id}")
                    # Reload terms
                    if current_terms_tb_id[0]:
                        # Get current termbase name from table
                        for row in range(termbase_table.rowCount()):
                            name_item = termbase_table.item(row, 1)
                            if name_item and name_item.data(Qt.ItemDataRole.UserRole) == current_terms_tb_id[0]:
                                load_terms_for_termbase(current_terms_tb_id[0], name_item.text())
                                break
                    # Also refresh termbase list to update term count
                    refresh_termbase_list()
                    # v1.10.64: clear cache + rebuild index + refresh
                    # TermLens for current segment so the deleted term
                    # stops showing up as a match.
                    self._post_termbase_delete_refresh()
                except Exception as e:
                    QMessageBox.warning(terms_window, "Error", f"Failed to delete term: {e}")
        
        def save_term_edit(row, column):
            """Save edited term to database"""
            if current_terms_tb_id[0] is None:
                return

            # Skip columns that have widgets or are read-only
            # (Forbidden=6 checkbox, Created=7 read-only timestamp,
            # Synonyms=8 read-only, Delete=9 button). v1.10.65: Created
            # column inserted at 7; issue #114: Synonyms at 8, Delete at 9.
            if column in (6, 7, 8, 9):
                return

            source_item = terms_table.item(row, 0)
            if not source_item:
                return

            term_id = source_item.data(Qt.ItemDataRole.UserRole)
            if not term_id:
                return

            source = terms_table.item(row, 0).text() if terms_table.item(row, 0) else ""
            target = terms_table.item(row, 1).text() if terms_table.item(row, 1) else ""
            domain = terms_table.item(row, 2).text() if terms_table.item(row, 2) else ""
            notes = terms_table.item(row, 3).text() if terms_table.item(row, 3) else ""
            project = terms_table.item(row, 4).text() if terms_table.item(row, 4) else ""
            client = terms_table.item(row, 5).text() if terms_table.item(row, 5) else ""

            try:
                # v1.10.74: bump modified_date so the snapshot-gated
                # auto-refresh sees the change. Required because
                # SQLite doesn't auto-update modified_date on UPDATE
                # — the column's DEFAULT CURRENT_TIMESTAMP only
                # fires on INSERT. Without this bump, inline grid
                # edits to terms (Termbases tab) wouldn't propagate
                # to the live TermLens display because the snapshot
                # would still match. See entry_editor.save_term for
                # the full back-story.
                with rollback_if_failed(self.db_manager.connection, self.log):
                    self.db_manager.cursor.execute(
                        """UPDATE termbase_terms
                           SET source_term = ?, target_term = ?, domain = ?, notes = ?,
                               project = ?, client = ?,
                               modified_date = CURRENT_TIMESTAMP
                           WHERE id = ?""",
                        (source, target, domain, notes, project, client, term_id)
                    )
                    self.db_manager.connection.commit()
                # Clear termbase cache
                with self.termbase_cache_lock:
                    self.termbase_cache.clear()
            except Exception as e:
                self.log(f"⚠️ Error saving term: {e}")
        
        terms_table.cellChanged.connect(save_term_edit)
        
        # Add new term button
        terms_btn_layout = QHBoxLayout()
        add_term_btn = QPushButton(self.tr("+ Add Term"))
        add_term_btn.setToolTip(self.tr("Add a term to the selected termbase"))
        add_term_btn.setEnabled(False)

        def term_position(term_id):
            """0-based position of term_id in the Terms grid's current filter
            and sort order, or None if the filter hides it. Same WHERE and
            ORDER BY as load_terms_page; reads ids only and stops at the term."""
            where = "termbase_id = CAST(? AS TEXT)"
            params = [current_terms_tb_id[0]]
            if terms_filter_text[0]:
                where += " AND (LOWER(source_term) LIKE ? OR LOWER(target_term) LIKE ?)"
                params += [f'%{terms_filter_text[0]}%'] * 2
            rows = self.db_manager.connection.execute(
                f"SELECT id FROM termbase_terms WHERE {where} ORDER BY {terms_sort_order[0]}",
                params)
            for pos, row in enumerate(rows):
                if row[0] == term_id:
                    return pos
            return None

        def show_term(term_id):
            """Open the page holding term_id and select its row, keeping the
            user's sort. A filter that hides the term is cleared."""
            try:
                pos = term_position(term_id)
                if pos is None and terms_filter_text[0]:
                    terms_search_box.clear()  # reloads page 1 unfiltered
                    pos = term_position(term_id)
            except Exception as e:
                self.log(f"⚠️ Could not locate term {term_id} in the grid: {e}")
                return
            if pos is None:
                return
            page_size = terms_page_size[0]
            terms_current_page[0] = pos // page_size if page_size > 0 else 0
            load_terms_page()
            for r in range(terms_table.rowCount()):
                item = terms_table.item(r, 0)
                if item and item.data(Qt.ItemDataRole.UserRole) == term_id:
                    terms_table.selectRow(r)
                    terms_table.scrollToItem(item, QAbstractItemView.ScrollHint.PositionAtCenter)
                    break

        def add_new_term():
            """Add a term to the selected termbase through the Add Term dialog
            (the one the Editor uses), then show it selected in the grid.

            Up to 1.10.375 this button saved an EMPTY term straight away and
            put the cursor on the page's last row - an existing term, which
            the user then typed over."""
            tb_id = current_terms_tb_id[0]
            if tb_id is None:
                return
            tb = termbase_mgr.get_termbase(tb_id) or {}
            tb_name = tb.get('name') or str(tb_id)

            from modules.termbase_entry_editor import TermbaseEntryEditor
            from modules.termbase_manager import normalize_term_for_save
            dialog = TermbaseEntryEditor(terms_window, db_manager=self.db_manager, termbase_id=tb_id)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            meta = dialog.get_metadata()
            source, target = dialog.get_source_term(), dialog.get_target_term()
            if not meta['is_nontranslatable']:  # as add_term will store them
                source, target = normalize_term_for_save(source), normalize_term_for_save(target)

            def already_there(existing_id):
                QMessageBox.information(
                    terms_window, self.tr("Already in the termbase"),
                    f"“{source} → {target}” is already in {tb_name}, so it was not "
                    f"added again.\n\nIt is selected in the list.")
                show_term(existing_id)

            try:
                existing_id = termbase_mgr.find_duplicate_term(tb_id, source, target)
            except Exception as e:
                QMessageBox.warning(terms_window, self.tr("Term not added"),
                                    f"Workbench could not read {tb_name}:\n\n{e}")
                return
            if existing_id:
                already_there(existing_id)
                return

            # "Similar term found": the same source with another target (or the
            # other way round) is offered as a synonym of that entry, as when
            # adding from the Editor. The dialog is in this termbase's own
            # direction, so nothing is turned round.
            decision, _merged = self._detect_and_merge_synonyms(
                source, target, [{**tb, 'id': tb_id, 'name': tb_name}],
                dialog.get_source_synonyms(), dialog.get_target_synonyms(),
                in_termbase_direction=True)
            if decision == 'cancel':
                return
            if decision == 'failed':
                QMessageBox.warning(
                    terms_window, self.tr("Term not added"),
                    f"Workbench could not add “{source} → {target}” to {tb_name} as a synonym.\n\n"
                    "Another program may be using the database (it is locked). "
                    "The Session Log has the details.")
                return
            if decision == 'merged':
                self.log(f"✓ Added as a synonym in termbase '{tb_name}': {source} → {target}")
                refresh_termbase_list()
                try:
                    self.force_refresh_matches()
                except Exception as e:
                    self.log(f"⚠️ Refresh after adding a synonym failed: {e}")
                merged_into = termbase_mgr.find_merge_matches(tb_id, source, target)
                if merged_into:
                    show_term(merged_into[0]['term_id'])
                return

            term_id = termbase_mgr.add_term(
                termbase_id=tb_id, source_term=source, target_term=target,
                source_lang=self._convert_language_to_code(tb.get('source_lang') or '') or None,
                target_lang=self._convert_language_to_code(tb.get('target_lang') or '') or None,
                domain=meta['domain'], notes=meta['notes'],
                project=meta['project'], client=meta['client'],
                forbidden=meta['forbidden'], is_nontranslatable=meta['is_nontranslatable'],
                definition=meta['definition'], url=meta['url'],
                source_abbreviation=meta['source_abbreviation'],
                target_abbreviation=meta['target_abbreviation'],
            )
            if not term_id:
                # add_term says None both for a duplicate (another program,
                # e.g. the Trados plugin, may have just added the same pair)
                # and for a failure; its log line has the error.
                try:
                    existing_id = termbase_mgr.find_duplicate_term(tb_id, source, target)
                except Exception:
                    existing_id = None
                if existing_id:
                    already_there(existing_id)
                else:
                    QMessageBox.warning(
                        terms_window, self.tr("Term not added"),
                        f"Workbench could not save “{source} → {target}” to {tb_name}.\n\n"
                        "Another program may be using the database (it is locked), or the "
                        "database file is read-only. The Session Log has the details.")
                return

            # The dialog's captions follow this termbase's own direction, so its
            # source/target synonym lists map straight onto the stored columns.
            for language, synonyms in (('source', dialog.get_source_synonyms()),
                                       ('target', dialog.get_target_synonyms())):
                for syn in synonyms:
                    if not termbase_mgr.add_synonym(term_id, syn['text'], language=language,
                                                    display_order=syn['order'],
                                                    forbidden=syn['forbidden']):
                        self.log(f"✗ Could not add {language} synonym: {syn['text']}")
            self.log(f"✓ Added term to termbase '{tb_name}': {source} → {target}")

            refresh_termbase_list()  # term count
            try:
                self.force_refresh_matches()  # TermLens and matches see the new term
            except Exception as e:
                self.log(f"⚠️ Refresh after adding a term failed: {e}")
            show_term(term_id)

        add_term_btn.clicked.connect(add_new_term)
        terms_btn_layout.addWidget(add_term_btn)
        terms_btn_layout.addStretch()
        right_layout.addLayout(terms_btn_layout)
        
        def open_terms_window(tb_id, tb_name):
            """Show tb_id's terms in the Terms window and bring it to the front."""
            load_terms_for_termbase(tb_id, tb_name)
            add_term_btn.setEnabled(True)
            if terms_window.isMinimized():
                terms_window.showNormal()
            terms_window.show()
            terms_window.raise_()
            terms_window.activateWindow()

        def on_termbase_selected():
            """Open the Terms window for the termbase selected in the list.
            Not connected to selection changes (selecting a row only selects
            it); SuperLookup's "go to termbase entry" calls it."""
            selected_rows = termbase_table.selectionModel().selectedRows()
            if selected_rows:
                name_item = termbase_table.item(selected_rows[0].row(), 1)
                if name_item:
                    open_terms_window(name_item.data(Qt.ItemDataRole.UserRole), name_item.text())

        self._on_termbase_selected = on_termbase_selected  # Store for external access
        self._open_termbase_terms = open_terms_window  # the list's right-click menu
        self.termbase_terms_table = terms_table  # SuperLookup selects the first match in it
        
        # Populate termbase list
        def refresh_termbase_list():
            # CRITICAL FIX: Get project_id dynamically, not from closure
            # Use 0 (global) when no project is loaded for Superlookup support
            current_proj = self.current_project if hasattr(self, 'current_project') else None
            refresh_project_id = current_proj.id if (current_proj and hasattr(current_proj, 'id')) else 0  # 0 = global
            
            self.log(f"📋 Refreshing termbase list (project_id: {refresh_project_id})")
            termbases = termbase_mgr.get_all_termbases()
            self.log(f"  Found {len(termbases)} termbase(s) in database")
            # Turn sorting off during rebuild — otherwise inserting items
            # one column at a time triggers a live re-sort after each
            # setItem call, mangling the row mapping the closures below
            # rely on. Re-enabled after the loop.
            _prev_sorting = termbase_table.isSortingEnabled()
            termbase_table.setSortingEnabled(False)
            termbase_table.setRowCount(len(termbases))
            
            for row, tb in enumerate(termbases):
                # v1.10.169: read the actual DB state regardless of whether
                # a project is loaded. Previously this block forced both
                # checkboxes to unchecked when current_proj was None, which
                # silently wiped the user's selection state every time the
                # table refreshed in the "no project" use case — and made
                # the no-project SuperLookup workflow basically uncontrollable
                # because Read couldn't be ticked: clicking it saved to the
                # DB then the next refresh undid the visual tick. The DB
                # is the source of truth in both modes.
                is_readable = termbase_mgr.is_termbase_active(tb['id'], refresh_project_id)
                is_writable = not tb.get('read_only', True)  # Default to True (read-only) if not set
                
                # Get project glossary flag from termbase_activation table
                priority = termbase_mgr.get_termbase_priority(tb['id'], refresh_project_id) if is_readable else None
                is_project_tb = (priority == 1)
                
                # Type (Project/Background) - auto-determined by priority
                if is_project_tb:
                    type_label = QLabel(self.tr("📌 Project"))
                    type_label.setStyleSheet("color: #FF69B4; font-weight: bold;")  # Pink
                else:
                    type_label = QLabel(self.tr("Background"))
                    type_label.setStyleSheet("color: #666;")
                termbase_table.setCellWidget(row, 0, type_label)
                
                # Name (bold if readable, pink if project termbase)
                name_item = QTableWidgetItem(tb['name'])
                name_item.setData(Qt.ItemDataRole.UserRole, tb['id'])
                if is_readable:
                    font = name_item.font()
                    font.setBold(True)
                    name_item.setFont(font)
                if is_project_tb:
                    name_item.setForeground(QColor("#FF69B4"))  # Pink for project termbase
                termbase_table.setItem(row, 1, name_item)
                
                # Languages
                langs = f"{tb['source_lang'] or '?'} → {tb['target_lang'] or '?'}"
                termbase_table.setItem(row, 2, QTableWidgetItem(langs))
                
                # Term count — numeric-sort variant so clicking the
                # column header orders by count, not by lexical string.
                try:
                    self.db_manager.cursor.execute("SELECT COUNT(*) FROM termbase_terms WHERE termbase_id = CAST(? AS TEXT)", (tb['id'],))
                    live_count = self.db_manager.cursor.fetchone()[0]
                except Exception as e:
                    live_count = tb.get('term_count', 0)
                termbase_table.setItem(row, 3, _NumericTableWidgetItem(str(live_count)))
                
                # Read checkbox (green)
                read_checkbox = CheckmarkCheckBox()
                read_checkbox.setChecked(is_readable)
                read_checkbox.setToolTip(self.tr("Read: Termbase is used for terminology matching"))
                
                def on_read_toggle(checked, tb_id=tb['id'], row_idx=row):
                    # Use 0 (global) when no project is loaded - allows Superlookup to work
                    curr_proj = self.current_project if hasattr(self, 'current_project') else None
                    curr_proj_id = curr_proj.id if (curr_proj and hasattr(curr_proj, 'id')) else 0  # 0 = global

                    if checked:
                        termbase_mgr.activate_termbase(tb_id, curr_proj_id)
                    else:
                        termbase_mgr.deactivate_termbase(tb_id, curr_proj_id)

                    # Clear cache and rebuild in-memory index (v1.9.182)
                    with self.termbase_cache_lock:
                        self.termbase_cache.clear()
                    self._build_termbase_index()  # Rebuild index with new activation state
                    refresh_termbase_list()
                
                read_checkbox.toggled.connect(on_read_toggle)
                termbase_table.setCellWidget(row, 4, read_checkbox)
                
                # Write checkbox (blue)
                write_checkbox = BlueCheckmarkCheckBox()
                write_checkbox.setChecked(is_writable)
                write_checkbox.setToolTip(self.tr("Write: Termbase is updated with new terms"))
                
                def on_write_toggle(checked, tb_id=tb['id']):
                    # Invert logic: checked = writable, so set read_only to NOT checked
                    success = termbase_mgr.set_termbase_read_only(tb_id, not checked)
                    if success:
                        status = "writable" if checked else "read-only"
                        self.log(f"✅ Termbase {tb_id} set to {status}")
                    else:
                        # v1.10.182: revert on failure — find the row dynamically
                        # by tb_id (not the captured cell-creation index, which
                        # goes stale the moment the user sorts the table). Same
                        # bug class as on_project_toggle pre-v1.10.181: rare here
                        # because it only fires on DB write failure, but a fix
                        # is essentially free since the lookup pattern is the
                        # same one used everywhere else now.
                        for r in range(termbase_table.rowCount()):
                            name_item = termbase_table.item(r, 1)
                            if name_item and name_item.data(Qt.ItemDataRole.UserRole) == tb_id:
                                sender = termbase_table.cellWidget(r, 5)
                                if sender:
                                    sender.blockSignals(True)
                                    sender.setChecked(not checked)
                                    sender.blockSignals(False)
                                break
                
                write_checkbox.toggled.connect(on_write_toggle)
                termbase_table.setCellWidget(row, 5, write_checkbox)
                
                # Project glossary checkbox. v1.10.360: always rendered when
                # a project is open — previously it only existed for termbases
                # with Read ticked, and the dash shown instead read as a
                # rendering glitch. Ticking it on a non-Read termbase also
                # enables Read (set_termbase_priority auto-activates: a
                # project termbase must be readable to match).
                if refresh_project_id:
                    is_project_glossary = (priority == 1)
                    project_checkbox = PinkCheckmarkCheckBox()
                    project_checkbox.setChecked(is_project_glossary)
                    if is_readable:
                        project_checkbox.setToolTip(self.tr("Set as Project Termbase (highest priority, pink)"))
                    else:
                        project_checkbox.setToolTip(self.tr("Set as Project Termbase (highest priority, pink). "
                                                            "Ticking also enables Read – a project termbase is "
                                                            "always used for matching."))

                    def on_project_toggle(checked, tb_id=tb['id']):
                        # v1.10.181: do NOT capture row_idx as a closure
                        # parameter — with column sorting enabled the captured
                        # index goes stale; look rows up dynamically by tb_id
                        # (stored in column 1's UserRole) instead.
                        curr_proj = self.current_project if hasattr(self, 'current_project') else None
                        curr_proj_id = curr_proj.id if (curr_proj and hasattr(curr_proj, 'id')) else None

                        success = False
                        if not curr_proj_id:
                            self.log(f"⚠️ No project loaded, cannot change project termbase")
                        else:
                            new_priority = 1 if checked else None
                            # Single write path: promotes exclusively, ensures
                            # an active activation row (Read), and keeps the
                            # legacy is_project_termbase flag in sync.
                            success = termbase_mgr.set_termbase_priority(tb_id, curr_proj_id, new_priority)

                        if not success:
                            # Revert the tick visually (dynamic row lookup)
                            for r in range(termbase_table.rowCount()):
                                name_item = termbase_table.item(r, 1)
                                if name_item and name_item.data(Qt.ItemDataRole.UserRole) == tb_id:
                                    sender = termbase_table.cellWidget(r, 6)
                                    if isinstance(sender, PinkCheckmarkCheckBox):
                                        sender.blockSignals(True)
                                        sender.setChecked(not checked)
                                        sender.blockSignals(False)
                                    break
                            return

                        label = "Project termbase" if checked else "Background"
                        self.log(f"✅ Set termbase {tb_id} as {label}")

                        # Promotion may also have ticked Read, and the Type
                        # column / row styling changed — rebuild the matching
                        # index and refresh the whole table (same pattern as
                        # on_read_toggle) rather than patching widgets by hand.
                        with self.termbase_cache_lock:
                            self.termbase_cache.clear()
                        self._build_termbase_index()
                        refresh_termbase_list()

                    project_checkbox.toggled.connect(on_project_toggle)
                    termbase_table.setCellWidget(row, 6, project_checkbox)
                else:
                    # No project open: nothing to scope the role to
                    priority_item = QTableWidgetItem("–")
                    priority_item.setForeground(QColor("#999"))
                    priority_item.setToolTip(self.tr("Open a project to set a project termbase"))
                    priority_item.setFlags(priority_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                    termbase_table.setItem(row, 6, priority_item)

                # AI checkbox (purple/orange) - whether to inject terms into LLM prompts
                ai_enabled = termbase_mgr.get_termbase_ai_inject(tb['id'])
                ai_checkbox = OrangeCheckmarkCheckBox()
                ai_checkbox.setChecked(ai_enabled)
                ai_checkbox.setToolTip(self.tr("AI: Send termbase terms to LLM with translation prompts"))

                def on_ai_toggle(checked, tb_id=tb['id'], tb_name=tb['name']):
                    if checked:
                        from PyQt6.QtWidgets import QMessageBox
                        # Count this termbase's terms so we can warn about large or
                        # general-purpose termbases, which bloat every translation
                        # prompt with terms irrelevant to the current segment. Mirrors
                        # the large-termbase guard in Supervertaler for Trados.
                        try:
                            self.db_manager.cursor.execute(
                                "SELECT COUNT(*) FROM termbase_terms WHERE termbase_id = CAST(? AS TEXT)",
                                (tb_id,))
                            term_count = self.db_manager.cursor.fetchone()[0] or 0
                        except Exception:
                            term_count = 0

                        # Threshold mirrors the Trados plugin's AutoPrompt warning.
                        LARGE_TERMBASE_AI_WARNING = 50

                        msg = QMessageBox()
                        if term_count > LARGE_TERMBASE_AI_WARNING:
                            msg.setIcon(QMessageBox.Icon.Warning)
                            msg.setWindowTitle(self.tr("Large termbase"))
                            msg.setText(f"'{tb_name}' contains {term_count:,} terms.")
                            msg.setInformativeText(
                                "With AI injection on, the terms from this termbase that appear in "
                                "each segment are sent to the LLM. Even with that filtering, a large "
                                "or general-purpose termbase injects many incidental matches — common "
                                "words that merely happen to appear in the segment — which crowd the "
                                "prompt, can make results worse, and cost more.\n\n"
                                "Best results come from a small, project-specific termbase — typically "
                                "a few dozen carefully chosen terms. Consider enabling AI only on a "
                                "compact termbase.\n\n"
                                "Enable AI injection anyway?"
                            )
                            msg.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
                            msg.setDefaultButton(QMessageBox.StandardButton.No)
                        else:
                            msg.setWindowTitle(self.tr("Enable AI Injection"))
                            msg.setText(f"Enable AI injection for '{tb_name}'?")
                            msg.setInformativeText(
                                "Terms from this termbase that appear in each segment will be sent to "
                                "the LLM, helping the AI use your preferred terminology consistently."
                            )
                            msg.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
                            msg.setDefaultButton(QMessageBox.StandardButton.Yes)

                        if msg.exec() != QMessageBox.StandardButton.Yes:
                            # User cancelled - revert checkbox
                            sender = termbase_table.cellWidget(termbase_table.currentRow(), 7)
                            if sender:
                                sender.blockSignals(True)
                                sender.setChecked(False)
                                sender.blockSignals(False)
                            return
                    termbase_mgr.set_termbase_ai_inject(tb_id, checked)
                    self.log(f"{'✅ Enabled' if checked else '❌ Disabled'} AI injection for termbase: {tb_name}")

                ai_checkbox.toggled.connect(on_ai_toggle)
                termbase_table.setCellWidget(row, 7, ai_checkbox)

                # v1.10.247: 🔍 SuperLookup checkbox (teal). The single
                # switch deciding whether SuperLookup searches this termbase,
                # independent of Read. Default on (included) for every
                # existing termbase. No index rebuild on toggle — it doesn't
                # affect the active-project matching index.
                is_superlookup = bool(tb.get('superlookup_enabled', True))
                tb_superlookup_checkbox = TealCheckmarkCheckBox()
                tb_superlookup_checkbox.setChecked(is_superlookup)
                tb_superlookup_checkbox.setToolTip(self.tr(
                    "SuperLookup: termbase is searched by SuperLookup. "
                    "Independent of Read — leave Read off for a project and "
                    "still search this termbase."
                ))

                def on_tb_superlookup_toggle(checked, tb_id=tb['id'], row_idx=row):
                    success = termbase_mgr.set_termbase_superlookup_enabled(tb_id, bool(checked))
                    if success:
                        self.log(
                            f"{'✅ Included in' if checked else '❌ Excluded from'} "
                            f"SuperLookup: termbase {tb_id}"
                        )
                    else:
                        sender = termbase_table.cellWidget(row_idx, 8)
                        if sender:
                            sender.blockSignals(True)
                            sender.setChecked(not checked)
                            sender.blockSignals(False)

                tb_superlookup_checkbox.toggled.connect(on_tb_superlookup_toggle)
                termbase_table.setCellWidget(row, 8, tb_superlookup_checkbox)

            # Update header checkbox states based on current selection
            tb_read_header_checkbox.blockSignals(True)
            tb_write_header_checkbox.blockSignals(True)
            tb_superlookup_header_checkbox.blockSignals(True)
            all_tb_read_checked = all(termbase_table.cellWidget(r, 4).isChecked() if termbase_table.cellWidget(r, 4) else False for r in range(termbase_table.rowCount())) if termbase_table.rowCount() > 0 else False
            all_tb_write_checked = all(termbase_table.cellWidget(r, 5).isChecked() if termbase_table.cellWidget(r, 5) else False for r in range(termbase_table.rowCount())) if termbase_table.rowCount() > 0 else False
            all_tb_superlookup_checked = all(termbase_table.cellWidget(r, 8).isChecked() if termbase_table.cellWidget(r, 8) else False for r in range(termbase_table.rowCount())) if termbase_table.rowCount() > 0 else False
            tb_read_header_checkbox.setChecked(all_tb_read_checked)
            tb_write_header_checkbox.setChecked(all_tb_write_checked)
            tb_superlookup_header_checkbox.setChecked(all_tb_superlookup_checked)
            tb_read_header_checkbox.blockSignals(False)
            tb_write_header_checkbox.blockSignals(False)
            tb_superlookup_header_checkbox.blockSignals(False)

            # v1.10.169: re-enable sorting now that the rebuild is done.
            # See the matching disable call at the top of this function.
            termbase_table.setSortingEnabled(_prev_sorting)

            # Keep the Terms window honest: renamed → new title; deleted → closed.
            shown_id = current_terms_tb_id[0]
            if shown_id is not None:
                shown = next((tb for tb in termbases if tb['id'] == shown_id), None)
                if shown is None:
                    current_terms_tb_id[0] = None
                    terms_window.hide()
                else:
                    show_termbase_name(shown['name'])

        # Store callback as instance attribute so add_term_to_termbase can call it
        self.termbase_tab_refresh_callback = refresh_termbase_list
        
        refresh_termbase_list()
        left_layout.addWidget(termbase_table, stretch=1)
        
        # Button bar for left panel
        button_layout = QHBoxLayout()
        
        create_btn = QPushButton(self.tr("+ Create New"))
        # Same stale-capture trap as Extract Terms below: resolve the project on
        # click, otherwise a "Project-specific" termbase would be created with a
        # NULL project_id and behave like an orphaned global one.
        create_btn.clicked.connect(lambda: self._show_create_termbase_dialog(
            termbase_mgr,
            refresh_termbase_list,
            getattr(getattr(self, 'current_project', None), 'id', None)
        ))
        button_layout.addWidget(create_btn)
        
        extract_btn = QPushButton(self.tr("🔍 Extract Terms"))
        extract_btn.setToolTip(self.tr("Extract terminology from project segments to create project termbase"))
        # The tab is built once at startup, before any project exists, so the
        # enabled state must NOT be derived from the project_id captured above
        # (it is always None here and was never refreshed, leaving the button
        # permanently greyed out). Resolve the project live on click instead.
        extract_btn.clicked.connect(lambda: self._show_term_extraction_dialog(
            termbase_mgr,
            refresh_termbase_list,
            getattr(getattr(self, 'current_project', None), 'id', None)
        ))
        button_layout.addWidget(extract_btn)
        
        import_btn = QPushButton(self.tr("📥 Import"))
        import_btn.clicked.connect(lambda: self._import_termbase(termbase_mgr, termbase_table))
        button_layout.addWidget(import_btn)
        
        export_btn = QPushButton(self.tr("📤 Export"))
        export_btn.clicked.connect(lambda: self._export_termbase(termbase_mgr, termbase_table))
        button_layout.addWidget(export_btn)
        
        delete_btn = QPushButton(self.tr("🗑️ Delete"))
        delete_btn.clicked.connect(lambda: self._delete_termbase(termbase_mgr, termbase_table, refresh_termbase_list))
        button_layout.addWidget(delete_btn)
        
        button_layout.addStretch()
        left_layout.addLayout(button_layout)
        
        layout.addWidget(left_panel, stretch=1)
        
        return tab

    def _show_create_termbase_dialog(self, termbase_mgr, refresh_callback, project_id):
        """Show dialog to create new glossary"""
        from modules.termbase_manager import TermbaseManager
        
        dialog = QDialog(self)
        dialog.setWindowTitle(self.tr("Create New Termbase"))
        dialog.setMinimumWidth(400)
        
        layout = QFormLayout()
        
        # Name
        name_field = QLineEdit()
        layout.addRow("Termbase Name:", name_field)
        
        # Source language
        source_lang_field = QLineEdit()
        source_lang_field.setPlaceholderText(self.tr("e.g., en, nl, de"))
        layout.addRow("Source Language:", source_lang_field)
        
        # Target language
        target_lang_field = QLineEdit()
        target_lang_field.setPlaceholderText(self.tr("e.g., en, nl, de"))
        layout.addRow("Target Language:", target_lang_field)
        
        # Description
        desc_field = QTextEdit()
        desc_field.setMaximumHeight(80)
        layout.addRow("Description:", desc_field)
        
        # Scope - use styled checkboxes instead of radio buttons for better visibility
        scope_group = QButtonGroup()
        scope_group.setExclusive(True)  # Only one can be checked
        
        global_checkbox = BlueCheckmarkCheckBox("Global (all projects)")
        global_checkbox.setChecked(True)
        project_checkbox = PinkCheckmarkCheckBox("Project-specific")
        if project_id is None:
            # Nothing to scope the termbase to - offering the choice would only
            # produce a termbase with no project association.
            project_checkbox.setEnabled(False)
            project_checkbox.setToolTip(self.tr("Open a project first to create a project-specific termbase"))

        scope_group.addButton(global_checkbox, 0)
        scope_group.addButton(project_checkbox, 1)
        
        scope_layout = QHBoxLayout()
        scope_layout.addWidget(global_checkbox)
        scope_layout.addWidget(project_checkbox)
        layout.addRow("Scope:", scope_layout)
        
        # Buttons
        button_layout = QHBoxLayout()
        create_btn = QPushButton(self.tr("Create"))
        cancel_btn = QPushButton(self.tr("Cancel"))
        
        def create_termbase():
            name = name_field.text().strip()
            if not name:
                QMessageBox.warning(dialog, "Error", "Please enter a termbase name")
                return
            
            source_lang = source_lang_field.text().strip() or None
            target_lang = target_lang_field.text().strip() or None
            description = desc_field.toPlainText().strip()
            is_global = scope_group.checkedId() == 0
            
            if not is_global and project_id is None:
                QMessageBox.warning(dialog, "Error",
                                    "No project is open, so a project-specific termbase cannot be created.\n\n"
                                    "Open a project first, or choose 'Global (all projects)'.")
                return

            tb_project_id = None if is_global else project_id

            tb_id = termbase_mgr.create_termbase(
                name=name,
                source_lang=source_lang,
                target_lang=target_lang,
                project_id=tb_project_id,
                description=description,
                is_global=is_global
            )
            
            if tb_id:
                self._play_sound_effect('glossary_created')
                QMessageBox.information(dialog, "Success", f"Termbase '{name}' created successfully!")
                refresh_callback()
                dialog.accept()
            else:
                QMessageBox.critical(dialog, "Error", "Failed to create termbase")
        
        create_btn.clicked.connect(create_termbase)
        cancel_btn.clicked.connect(dialog.reject)
        
        button_layout.addStretch()
        button_layout.addWidget(create_btn)
        button_layout.addWidget(cancel_btn)
        layout.addRow("", button_layout)
        
        dialog.setLayout(layout)
        dialog.exec()

    def _show_term_extraction_dialog(self, termbase_mgr, refresh_callback, project_id):
        """AI glossary extraction: send project source text to the configured
        LLM and review the returned bilingual term pairs before creating a
        project termbase. Replaced the mechanical TermExtractor in v1.10.357."""

        if project_id is None:
            QMessageBox.information(
                self,
                self.tr("No Project Open"),
                self.tr("Open a project first – term extraction reads the source "
                        "segments of the current project.")
            )
            return

        dialog = QDialog(self)
        dialog.setWindowTitle(self.tr("Extract Terms with AI"))
        dialog.setMinimumWidth(800)
        dialog.setMinimumHeight(600)

        layout = QVBoxLayout()

        # Info label
        info_label = QLabel(self.tr("The configured AI model reads your source text and proposes a bilingual project glossary. Review and edit the pairs, then create the project termbase."))
        info_label.setWordWrap(True)
        layout.addWidget(info_label)
        
        # Source text section
        source_group = QGroupBox(self.tr("Source Text"))
        source_layout = QVBoxLayout()
        
        # Text source options
        source_type_layout = QHBoxLayout()
        use_project_radio = CheckmarkRadioButton(self.tr("Use project segments"))
        use_project_radio.setChecked(True)
        use_manual_radio = CheckmarkRadioButton(self.tr("Paste text manually"))
        
        source_type_layout.addWidget(use_project_radio)
        source_type_layout.addWidget(use_manual_radio)
        source_type_layout.addStretch()
        source_layout.addLayout(source_type_layout)
        
        # Manual text input
        text_input = QTextEdit()
        text_input.setPlaceholderText(self.tr("Paste source text here..."))
        text_input.setMaximumHeight(150)
        text_input.setEnabled(False)
        source_layout.addWidget(text_input)
        
        def on_source_type_changed():
            text_input.setEnabled(use_manual_radio.isChecked())
        
        use_project_radio.toggled.connect(on_source_type_changed)
        use_manual_radio.toggled.connect(on_source_type_changed)
        
        source_group.setLayout(source_layout)
        layout.addWidget(source_group)
        
        # Extraction settings
        params_group = QGroupBox(self.tr("Extraction Settings"))
        params_layout = QFormLayout()

        # Language pair, prefilled from the project (editable free text so any
        # language code or name works – the LLM is not limited to a fixed list)
        source_lang_edit = QLineEdit(getattr(self.current_project, 'source_lang', None) or 'en')
        source_lang_edit.setMaximumWidth(120)
        params_layout.addRow(self.tr("Source Language:"), source_lang_edit)

        target_lang_edit = QLineEdit(getattr(self.current_project, 'target_lang', None) or 'nl')
        target_lang_edit.setMaximumWidth(120)
        params_layout.addRow(self.tr("Target Language:"), target_lang_edit)

        # Optional domain hint; blank = let the model infer
        domain_edit = QLineEdit()
        domain_edit.setPlaceholderText(self.tr("Optional – e.g. 'mechanical engineering'. Leave blank to let the AI infer."))
        params_layout.addRow(self.tr("Domain / Subject:"), domain_edit)

        params_group.setLayout(params_layout)
        layout.addWidget(params_group)

        # Extract button + busy indicator
        extract_row = QHBoxLayout()
        extract_btn = QPushButton(self.tr("🤖 Extract Terms with AI"))
        extract_btn.setMaximumWidth(200)
        busy_label = QLabel(self.tr("Asking the AI model… large projects can take a minute or more."))
        busy_label.setVisible(False)
        extract_row.addWidget(extract_btn)
        extract_row.addWidget(busy_label)
        extract_row.addStretch()
        layout.addLayout(extract_row)

        # Results table: bilingual, editable in place
        results_table = QTableWidget()
        results_table.setColumnCount(4)
        results_table.setHorizontalHeaderLabels([
            self.tr("Select"), self.tr("Source"), self.tr("Target"), self.tr("Note")])
        results_table.horizontalHeader().setStretchLastSection(False)
        results_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        results_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        results_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        results_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        results_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        results_table.setVisible(False)

        # Right-click: tick/untick all currently selected rows at once
        def show_results_context_menu(pos):
            selected_rows = sorted({idx.row() for idx in results_table.selectedIndexes()})
            if not selected_rows:
                return

            def set_checked(state):
                for r in selected_rows:
                    check_item = results_table.item(r, 0)
                    if check_item:
                        check_item.setCheckState(state)

            menu = QMenu(results_table)
            untick_action = QAction(self.tr("Untick selected ({0})").format(len(selected_rows)), menu)
            untick_action.triggered.connect(lambda: set_checked(Qt.CheckState.Unchecked))
            menu.addAction(untick_action)
            tick_action = QAction(self.tr("Tick selected ({0})").format(len(selected_rows)), menu)
            tick_action.triggered.connect(lambda: set_checked(Qt.CheckState.Checked))
            menu.addAction(tick_action)
            menu.exec(results_table.viewport().mapToGlobal(pos))

        results_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        results_table.customContextMenuRequested.connect(show_results_context_menu)

        results_label = QLabel(self.tr("Extracted terms will appear here"))
        results_label.setVisible(False)

        layout.addWidget(results_label)
        layout.addWidget(results_table)

        extracted_terms = []
        # Keep a reference so the worker is not garbage-collected mid-flight
        worker_holder = {'worker': None}

        # Cap the text sent in one call. ~48k chars ≈ 12k tokens of source
        # text – roughly a 8–9k-word project. Larger projects get a visible
        # warning that only the first part was analysed (chunk-and-dedup is a
        # planned follow-up, not silently pretending full coverage).
        MAX_CHARS = 48_000
        
        def _do_extract_terms():
            """Extract terms and populate results table"""
            # Get source text
            if use_project_radio.isChecked():
                # Get all source segments from current project
                if not self.current_project:
                    QMessageBox.warning(dialog, "Error", "No project is currently loaded")
                    return
                
                # Read from the project's segment list, not from grid widgets:
                # self.grid_widget has never existed on this class, so the old
                # cellWidget() walk raised AttributeError inside the click slot
                # and the button silently did nothing.
                segments = []
                for seg in self.current_project.segments:
                    text = strip_all_tags(seg.source) if seg.source else ""
                    # v1.9.306: Strip invisible character markers before term extraction
                    if text and hasattr(self, 'reverse_invisible_replacements'):
                        text = self.reverse_invisible_replacements(text)
                    text = text.strip()
                    if text:
                        segments.append(text)

                if not segments:
                    QMessageBox.warning(dialog, "Error", "No source segments found in project")
                    return
                
                source_text = "\n".join(segments)
            else:
                source_text = text_input.toPlainText().strip()
                if not source_text:
                    QMessageBox.warning(dialog, "Error", "Please enter some text to analyze")
                    return
            
            # Cap oversized projects with a visible warning – never silently
            truncated = False
            if len(source_text) > MAX_CHARS:
                source_text = source_text[:MAX_CHARS]
                truncated = True

            # Build the configured LLM client
            settings = self.load_llm_settings()
            provider = settings.get('provider', 'openai')
            model = settings.get(f"{provider}_model")
            api_keys = self.load_api_keys()
            client = self.create_llm_client(provider, model, api_keys, settings=settings)

            source_lang = source_lang_edit.text().strip() or 'en'
            target_lang = target_lang_edit.text().strip() or 'nl'
            domain = domain_edit.text().strip()
            domain_line = (
                f"The text's domain is: {domain}.\n"
                if domain else
                "Infer the domain from the text itself.\n"
            )

            prompt = (
                "You are a terminology extractor for professional translators.\n"
                f"From the {source_lang} text below, identify the domain-specific terms, "
                "technical vocabulary, proper nouns, and recurring multi-word terms worth "
                "capturing in a project glossary. Ignore ordinary/common words and function phrases.\n"
                f"{domain_line}"
                "For each term give its canonical (dictionary/lemma) form in "
                f"{source_lang} and the best {target_lang} translation for this domain. "
                "If you are not confident of a translation, leave \"target\" empty and "
                "explain briefly in \"note\".\n\n"
                "Return ONLY a JSON array, no prose, no markdown fences. Each element: "
                '{"source": "...", "target": "...", "note": "..."} '
                "(\"note\" is optional context such as a domain label or a caveat).\n\n"
                f"TEXT:\n{source_text}"
            )

            def on_extraction_ok(pairs):
                try:
                    busy_label.setVisible(False)
                except RuntimeError:
                    return  # dialog was closed while the AI call was running
                extract_btn.setEnabled(True)

                nonlocal extracted_terms
                extracted_terms = pairs

                results_table.setRowCount(len(pairs))
                for i, pair in enumerate(pairs):
                    check_item = QTableWidgetItem()
                    check_item.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
                    check_item.setCheckState(Qt.CheckState.Checked)
                    results_table.setItem(i, 0, check_item)
                    # Source and Target stay editable so pairs can be fixed
                    # in place before committing them to the termbase.
                    results_table.setItem(i, 1, QTableWidgetItem(pair['source']))
                    results_table.setItem(i, 2, QTableWidgetItem(pair['target']))
                    note_item = QTableWidgetItem(pair['note'])
                    note_item.setFlags(note_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                    results_table.setItem(i, 3, note_item)

                results_table.setVisible(True)
                label = self.tr("Extracted {0} term pairs (edit cells to correct, untick to skip):").format(len(pairs))
                if truncated:
                    label += self.tr(" ⚠️ Project is large – only the first ~{0}k characters were analysed.").format(MAX_CHARS // 1000)
                results_label.setText(label)
                results_label.setVisible(True)
                self.log(f"✓ AI extracted {len(pairs)} term pairs from project")

            def on_extraction_failed(message, raw):
                try:
                    busy_label.setVisible(False)
                except RuntimeError:
                    return  # dialog was closed while the AI call was running
                extract_btn.setEnabled(True)
                detail = f"\n\nRaw response (first 1000 chars):\n{raw[:1000]}" if raw else ""
                QMessageBox.critical(dialog, self.tr("Extraction Failed"),
                                     self.tr("AI term extraction failed: {0}{1}").format(message, detail))
                self.log(f"✗ AI term extraction failed: {message}")

            worker = GlossaryExtractionWorker(client, prompt)
            worker.finished_ok.connect(on_extraction_ok)
            worker.failed.connect(on_extraction_failed)
            worker_holder['worker'] = worker

            extract_btn.setEnabled(False)
            busy_label.setVisible(True)
            worker.start()

        def extract_terms():
            """Guarded entry point for the Extract Terms button.

            Qt swallows exceptions raised inside a click slot, which is how a
            stale attribute reference made this button look completely dead
            rather than reporting an error. Surface anything that escapes.
            """
            try:
                _do_extract_terms()
            except Exception as e:
                import traceback
                traceback.print_exc()
                QMessageBox.critical(dialog, "Error", f"Term extraction failed: {str(e)}")
                self.log(f"✗ Term extraction failed: {e}")

        extract_btn.clicked.connect(extract_terms)
        
        # Buttons
        button_layout = QHBoxLayout()
        
        create_btn = QPushButton(self.tr("Create Project Termbase"))
        create_btn.setToolTip(self.tr("Create a new project termbase with selected terms"))
        
        cancel_btn = QPushButton(self.tr("Cancel"))
        
        def _do_create_project_termbase():
            """Create project termbase with selected terms"""
            if not extracted_terms:
                QMessageBox.warning(dialog, "Error", "Please extract terms first")
                return
            
            # Get selected pairs — read Source/Target from the table cells, not
            # the original extraction, so in-place edits are respected.
            selected = []
            for i in range(results_table.rowCount()):
                check_item = results_table.item(i, 0)
                if check_item and check_item.checkState() == Qt.CheckState.Checked:
                    src_item = results_table.item(i, 1)
                    tgt_item = results_table.item(i, 2)
                    src = src_item.text().strip() if src_item else ""
                    tgt = tgt_item.text().strip() if tgt_item else ""
                    if src:
                        selected.append({'source': src, 'target': tgt})

            if not selected:
                QMessageBox.warning(dialog, "Error", "Please select at least one term")
                return
            
            # Ask for termbase name
            # current_project is a Project object, not a dict — .get() raised
            # AttributeError here and, because Qt swallows exceptions in a click
            # slot, the button appeared to do nothing.
            project_name = getattr(self.current_project, 'name', None) or 'Project'
            name, ok = QInputDialog.getText(
                dialog,
                "Termbase Name",
                "Enter name for project termbase:",
                text=f"{project_name} Terminology"
            )
            
            if not ok or not name.strip():
                return

            # If the project already has a project termbase, don't refuse at
            # the end of the whole flow (extraction + review is work the user
            # shouldn't lose) – ask whether the new termbase should take over
            # the project-termbase role or be saved alongside as a regular one.
            make_project_tb = True
            existing_ptb = termbase_mgr.get_project_termbase(project_id)
            if existing_ptb:
                choice = QMessageBox.question(
                    dialog,
                    self.tr("Project Termbase Exists"),
                    self.tr("This project's termbase is currently '{0}'.\n\n"
                            "Make the new termbase '{1}' the project termbase instead?\n\n"
                            "• Yes – '{1}' becomes the project termbase; '{0}' is kept as a regular termbase\n"
                            "• No – save '{1}' as a regular termbase; '{0}' stays the project termbase\n"
                            "• Cancel – go back (nothing is saved)").format(existing_ptb['name'], name.strip()),
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No | QMessageBox.StandardButton.Cancel,
                    QMessageBox.StandardButton.Yes
                )
                if choice == QMessageBox.StandardButton.Cancel:
                    return
                make_project_tb = (choice == QMessageBox.StandardButton.Yes)

            # Create termbase (bilingual – targets come from the AI extraction).
            # Always created with is_project_termbase=False so the manager's
            # one-per-project guard can't refuse; the role is assigned after
            # via set_as_project_termbase, which demotes the old one atomically.
            source_lang = source_lang_edit.text().strip() or 'en'
            target_lang = target_lang_edit.text().strip() or None
            tb_id = termbase_mgr.create_termbase(
                name=name.strip(),
                source_lang=source_lang,
                target_lang=target_lang,
                project_id=project_id,
                description=f"AI-extracted glossary from project segments ({len(selected)} term pairs)",
                is_global=False,
                is_project_termbase=False
            )

            if not tb_id:
                QMessageBox.critical(dialog, "Error", "Failed to create termbase.")
                return

            if make_project_tb:
                # v1.10.360: single write path — activates the termbase
                # (Read), sets priority=1 (pink Project ✓) exclusively, and
                # keeps the legacy is_project_termbase flag in sync.
                termbase_mgr.set_as_project_termbase(tb_id, project_id)

            # Add term pairs (target may be empty where the AI was unsure)
            added = 0
            for pair in selected:
                success = termbase_mgr.add_term(
                    termbase_id=tb_id,
                    source_term=pair['source'],
                    target_term=pair['target']
                )
                if success:
                    added += 1
            
            role = "project termbase" if make_project_tb else "termbase"
            self.log(f"✓ Created {role} '{name}' with {added} terms")
            QMessageBox.information(
                dialog,
                "Success",
                self.tr("{0} '{1}' created with {2} term pairs!").format(
                    "Project termbase" if make_project_tb else "Termbase",
                    name, added)
            )
            
            # Clear cache and refresh
            with self.termbase_cache_lock:
                self.termbase_cache.clear()
            refresh_callback()
            dialog.accept()

        def create_project_termbase():
            """Guarded entry point for the Create Project Termbase button.

            Qt discards exceptions raised inside a click slot, so an error here
            would otherwise leave the button looking inert. Surface it instead.
            """
            try:
                _do_create_project_termbase()
            except Exception as e:
                import traceback
                traceback.print_exc()
                QMessageBox.critical(dialog, "Error", f"Failed to create termbase: {str(e)}")
                self.log(f"✗ Create project termbase failed: {e}")

        create_btn.clicked.connect(create_project_termbase)
        cancel_btn.clicked.connect(dialog.reject)
        
        button_layout.addStretch()
        button_layout.addWidget(create_btn)
        button_layout.addWidget(cancel_btn)
        layout.addLayout(button_layout)
        
        dialog.setLayout(layout)
        dialog.exec()

    def _show_termbase_context_menu(self, pos, termbase_table, termbase_mgr, refresh_callback):
        """Show context menu for termbase table with Rename and Delete options"""
        # Get the row under the cursor
        item = termbase_table.itemAt(pos)
        if not item:
            return
        
        row = item.row()
        name_item = termbase_table.item(row, 1)
        if not name_item:
            return
        
        tb_name = name_item.text()
        tb_id = name_item.data(Qt.ItemDataRole.UserRole)
        
        menu = QMenu(self)
        
        # Edit Terms… opens the termbase's terms in their own window
        edit_terms_action = QAction(self.tr("📝 Edit Terms…"), self)
        edit_terms_action.triggered.connect(lambda: self._open_termbase_terms(tb_id, tb_name))
        menu.addAction(edit_terms_action)
        menu.setDefaultAction(edit_terms_action)
        menu.addSeparator()

        # Rename action
        rename_action = QAction("✏️ Rename Termbase", self)
        rename_action.triggered.connect(
            lambda: self._rename_termbase_dialog(tb_id, tb_name, termbase_mgr, refresh_callback)
        )
        menu.addAction(rename_action)
        
        menu.addSeparator()
        
        # Delete action
        delete_action = QAction("🗑️ Delete Termbase", self)
        delete_action.triggered.connect(
            lambda: self._delete_termbase(termbase_mgr, termbase_table, refresh_callback)
        )
        menu.addAction(delete_action)
        
        menu.exec(termbase_table.viewport().mapToGlobal(pos))

    def _rename_termbase_dialog(self, termbase_id, current_name, termbase_mgr, refresh_callback):
        """Show dialog to rename a termbase"""
        from PyQt6.QtWidgets import QInputDialog
        
        new_name, ok = QInputDialog.getText(
            self,
            "Rename Termbase",
            f"Enter new name for '{current_name}':",
            text=current_name
        )
        
        if ok and new_name and new_name.strip():
            new_name = new_name.strip()
            if new_name != current_name:
                if termbase_mgr.rename_termbase(termbase_id, new_name):
                    self.log(f"✓ Renamed termbase '{current_name}' to '{new_name}'")
                    QMessageBox.information(self, "Success", f"Termbase renamed to '{new_name}'")
                    # Clear cache and refresh
                    with self.termbase_cache_lock:
                        self.termbase_cache.clear()
                    refresh_callback()
                else:
                    QMessageBox.critical(self, "Error", "Failed to rename termbase")

    def _delete_termbase(self, termbase_mgr, termbase_table, refresh_callback):
        """Delete selected termbase"""
        selected_row = termbase_table.currentRow()
        if selected_row < 0:
            QMessageBox.warning(self, "Error", "Please select a termbase to delete")
            return
        
        # Get termbase info from Name column (column 1 - has ID stored in UserRole)
        name_item = termbase_table.item(selected_row, 1)
        if not name_item:
            QMessageBox.warning(self, "Error", "Could not read termbase information")
            return
        
        tb_name = name_item.text()
        termbase_id = name_item.data(Qt.ItemDataRole.UserRole)
        
        if not termbase_id:
            QMessageBox.warning(self, "Error", "Could not find termbase ID")
            return
        
        # Confirm deletion
        reply = QMessageBox.question(
            self,
            "Confirm Deletion",
            f"Are you sure you want to delete termbase '{tb_name}'?\n\nThis will permanently delete all terms in this termbase.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )
        
        if reply == QMessageBox.StandardButton.Yes:
            # Use the termbase ID we got from row data
            if termbase_id:
                try:
                    # Delete from database
                    cursor = self.db_manager.cursor
                    with rollback_if_failed(self.db_manager.connection, self.log):
                        cursor.execute("DELETE FROM termbase_terms WHERE termbase_id = ?", (termbase_id,))
                        cursor.execute("DELETE FROM termbase_activation WHERE termbase_id = ?", (termbase_id,))
                        cursor.execute("DELETE FROM termbases WHERE id = ?", (termbase_id,))
                        self.db_manager.connection.commit()
                    
                    self.log(f"✓ Deleted termbase: {tb_name}")
                    QMessageBox.information(self, "Success", f"Termbase '{tb_name}' has been deleted")
                    
                    # Clear cache and refresh
                    with self.termbase_cache_lock:
                        self.termbase_cache.clear()
                    refresh_callback()
                except Exception as e:
                    QMessageBox.critical(self, "Error", f"Failed to delete termbase: {str(e)}")
                    self.log(f"✗ Error deleting termbase: {e}")

    def _refresh_termbase_table(self, termbase_table, termbase_mgr):
        """Refresh termbase table to show updated term counts"""
        # Get current project ID
        current_proj = self.current_project if hasattr(self, 'current_project') else None
        project_id = current_proj.id if (current_proj and hasattr(current_proj, 'id')) else 0
        
        # Update term counts for each row
        for row in range(termbase_table.rowCount()):
            name_item = termbase_table.item(row, 1)
            if name_item:
                tb_id = name_item.data(Qt.ItemDataRole.UserRole)
                if tb_id:
                    try:
                        self.db_manager.cursor.execute(
                            "SELECT COUNT(*) FROM termbase_terms WHERE termbase_id = CAST(? AS TEXT)", 
                            (tb_id,)
                        )
                        term_count = self.db_manager.cursor.fetchone()[0]
                        count_item = termbase_table.item(row, 3)
                        if count_item:
                            count_item.setText(str(term_count))
                    except Exception as e:
                        self.log(f"⚠ Error updating term count: {e}")

    def _import_termbase(self, termbase_mgr, termbase_table):
        """Import terms into selected termbase from TSV file"""
        selected_row = termbase_table.currentRow()
        if selected_row < 0:
            QMessageBox.warning(self, "Error", "Please select a termbase to import into")
            return
        
        # Get termbase info from Name column (column 1 - has ID stored in UserRole)
        name_item = termbase_table.item(selected_row, 1)
        if not name_item:
            QMessageBox.warning(self, "Error", "Could not read termbase information")
            return
        
        tb_name = name_item.text()
        termbase_id = name_item.data(Qt.ItemDataRole.UserRole)
        
        if not termbase_id:
            QMessageBox.warning(self, "Error", "Could not find termbase ID")
            return
        
        # File dialog
        from PyQt6.QtWidgets import QFileDialog
        filepath, _ = QFileDialog.getOpenFileName(
            self,
            "Import Termbase",
            "",
            "TSV Files (*.tsv *.txt);;All Files (*.*)"
        )
        
        if not filepath:
            return

        # Count rows and get language pair for the confirmation dialog
        try:
            with open(filepath, 'r', encoding='utf-8-sig') as f:
                row_count = max(sum(1 for _ in f) - 1, 0)  # subtract header
        except Exception:
            row_count = 0

        tb_info = termbase_mgr.get_termbase(termbase_id)
        src_lang = (tb_info or {}).get('source_lang', '?') or '?'
        tgt_lang = (tb_info or {}).get('target_lang', '?') or '?'
        lang_pair = f"{src_lang} \u2192 {tgt_lang}"
        file_name = os.path.basename(filepath)

        # Show options dialog
        options_dialog = QDialog(self)
        options_dialog.setWindowTitle(self.tr("Import Options"))
        options_dialog.setMinimumWidth(400)

        layout = QVBoxLayout(options_dialog)

        # Info with filename, row count, termbase name, and language pair
        row_text = f"{row_count:,} row{'s' if row_count != 1 else ''}" if row_count > 0 else "unknown rows"
        info_label = QLabel(
            f'Import <b>{row_text}</b> from "<b>{file_name}</b>"'
            f'<br>into termbase "<b>{tb_name}</b>" ({lang_pair})')
        info_label.setWordWrap(True)
        layout.addWidget(info_label)
        
        # Duplicate handling
        dup_group = QGroupBox(self.tr("Duplicate Handling"))
        dup_layout = QVBoxLayout()
        
        skip_radio = CheckmarkRadioButton(self.tr("Skip duplicates (keep existing terms)"))
        skip_radio.setChecked(True)
        update_radio = CheckmarkRadioButton(self.tr("Update duplicates (overwrite existing terms)"))
        
        dup_layout.addWidget(skip_radio)
        dup_layout.addWidget(update_radio)
        dup_group.setLayout(dup_layout)
        layout.addWidget(dup_group)
        
        # Buttons
        button_box = QHBoxLayout()
        button_box.addStretch()
        
        cancel_btn = QPushButton(self.tr("Cancel"))
        cancel_btn.clicked.connect(options_dialog.reject)
        button_box.addWidget(cancel_btn)
        
        import_btn = QPushButton(self.tr("Import"))
        import_btn.setStyleSheet("font-weight: bold; background-color: #4CAF50; color: white; padding: 8px 20px; border: none; outline: none;")
        import_btn.clicked.connect(options_dialog.accept)
        button_box.addWidget(import_btn)
        
        layout.addLayout(button_box)
        
        if options_dialog.exec() != QDialog.DialogCode.Accepted:
            return
        
        # Create progress dialog with scrolling log
        progress_dialog = QDialog(self)
        progress_dialog.setWindowTitle(self.tr("Importing Termbase"))
        progress_dialog.setMinimumSize(600, 400)
        progress_dialog.setModal(True)
        
        progress_layout = QVBoxLayout(progress_dialog)
        
        # Header info
        header_label = QLabel(f"<b>📥 Importing into: {tb_name}</b>")
        header_label.setStyleSheet("font-size: 12pt; padding: 5px;")
        progress_layout.addWidget(header_label)
        
        # File info
        file_info = QLabel(f"File: {os.path.basename(filepath)}")
        file_info.setStyleSheet("color: #666; padding: 2px 5px;")
        progress_layout.addWidget(file_info)
        
        # Progress bar
        progress_bar = QProgressBar()
        progress_bar.setMinimum(0)
        progress_bar.setMaximum(100)
        progress_bar.setValue(0)
        progress_bar.setTextVisible(True)
        progress_layout.addWidget(progress_bar)
        
        # Stats labels row
        stats_layout = QHBoxLayout()
        imported_label = QLabel(self.tr("✅ Imported: 0"))
        imported_label.setStyleSheet("color: #4CAF50; font-weight: bold;")
        skipped_label = QLabel(self.tr("⏭️ Skipped: 0"))
        skipped_label.setStyleSheet("color: #FF9800;")
        errors_label = QLabel(self.tr("❌ Errors: 0"))
        errors_label.setStyleSheet("color: #F44336;")
        stats_layout.addWidget(imported_label)
        stats_layout.addWidget(skipped_label)
        stats_layout.addWidget(errors_label)
        stats_layout.addStretch()
        progress_layout.addLayout(stats_layout)
        
        # Scrolling log area
        log_label = QLabel(self.tr("Import Log:"))
        log_label.setStyleSheet("font-weight: bold; padding-top: 10px;")
        progress_layout.addWidget(log_label)
        
        log_text = QTextEdit()
        log_text.setReadOnly(True)
        log_text.setFont(QFont("Consolas", 9))
        log_text.setStyleSheet("background-color: #1e1e1e; color: #d4d4d4; border: 1px solid #555;")
        progress_layout.addWidget(log_text)
        
        # Close button (initially disabled)
        close_btn = QPushButton(self.tr("Close"))
        close_btn.setEnabled(False)
        close_btn.clicked.connect(progress_dialog.accept)
        progress_layout.addWidget(close_btn)
        
        # Show dialog
        progress_dialog.show()
        QApplication.processEvents()
        
        # Progress callback function
        import_stats = {'imported': 0, 'skipped': 0, 'errors': 0}
        
        def progress_callback(current, total, message):
            """Update progress dialog with import progress"""
            if total > 0:
                percent = int((current / total) * 100)
                progress_bar.setValue(percent)
                progress_bar.setFormat(f"{current}/{total} ({percent}%)")
            
            # Update stats from message
            if message.startswith("✅"):
                import_stats['imported'] += 1
                imported_label.setText(f"✅ Imported: {import_stats['imported']}")
            elif message.startswith("⏭️"):
                import_stats['skipped'] += 1
                skipped_label.setText(f"⏭️ Skipped: {import_stats['skipped']}")
            elif message.startswith("❌"):
                import_stats['errors'] += 1
                errors_label.setText(f"❌ Errors: {import_stats['errors']}")
            
            # Add to log
            log_text.append(message)
            
            # Auto-scroll to bottom
            scrollbar = log_text.verticalScrollBar()
            scrollbar.setValue(scrollbar.maximum())
            
            # Process events to keep UI responsive
            QApplication.processEvents()
        
        # Perform import with progress callback
        from modules.termbase_import_export import TermbaseImporter
        importer = TermbaseImporter(self.db_manager, termbase_mgr)
        
        self.log(f"📥 Importing termbase from {filepath}...")
        
        result = importer.import_tsv(
            filepath=filepath,
            termbase_id=termbase_id,
            skip_duplicates=skip_radio.isChecked(),
            update_duplicates=update_radio.isChecked(),
            progress_callback=progress_callback
        )
        
        # Update final stats
        progress_bar.setValue(100)
        imported_label.setText(f"✅ Imported: {result.imported_count}")
        skipped_label.setText(f"⏭️ Skipped: {result.skipped_count}")
        errors_label.setText(f"❌ Errors: {result.error_count}")
        
        # Add summary to log
        log_text.append("")
        log_text.append("=" * 50)
        if result.success:
            log_text.append(f"✓ Import complete: {result.imported_count} terms imported")
            if result.skipped_count > 0:
                log_text.append(f"  Skipped {result.skipped_count} duplicates")
            if result.error_count > 0:
                log_text.append(f"  {result.error_count} errors occurred")
            header_label.setText(f"<b>✓ Import Complete: {tb_name}</b>")
            header_label.setStyleSheet("font-size: 12pt; padding: 5px; color: #4CAF50;")
        else:
            log_text.append(f"✗ Import failed: {result.message}")
            header_label.setText(f"<b>✗ Import Failed: {tb_name}</b>")
            header_label.setStyleSheet("font-size: 12pt; padding: 5px; color: #F44336;")
        
        # Enable close button
        close_btn.setEnabled(True)
        close_btn.setFocus()
        
        # Wait for user to close dialog
        progress_dialog.exec()
        
        # Log and clear cache
        if result.success:
            self.log(f"✓ {result.message}")
            with self.termbase_cache_lock:
                self.termbase_cache.clear()
            
            # Refresh the termbase table to show updated term counts
            self._refresh_termbase_table(termbase_table, termbase_mgr)
        else:
            self.log(f"✗ Import failed: {result.message}")

    def _export_termbase(self, termbase_mgr, termbase_table):
        """Export selected termbase to TSV file"""
        selected_row = termbase_table.currentRow()
        if selected_row < 0:
            QMessageBox.warning(self, "Error", "Please select a termbase to export")
            return
        
        # Get termbase info from Name column (column 1 - has ID stored in UserRole)
        name_item = termbase_table.item(selected_row, 1)
        if not name_item:
            QMessageBox.warning(self, "Error", "Could not read termbase information")
            return
        
        tb_name = name_item.text()
        termbase_id = name_item.data(Qt.ItemDataRole.UserRole)
        
        if not termbase_id:
            QMessageBox.warning(self, "Error", "Could not find termbase ID")
            return
        
        # File dialog
        from PyQt6.QtWidgets import QFileDialog
        default_filename = f"{tb_name.replace(' ', '_')}.tsv"
        filepath, _ = QFileDialog.getSaveFileName(
            self,
            "Export Termbase",
            default_filename,
            "TSV Files (*.tsv);;Text Files (*.txt);;All Files (*.*)"
        )
        
        if not filepath:
            return
        
        # Show options dialog
        options_dialog = QDialog(self)
        options_dialog.setWindowTitle(self.tr("Export Options"))
        options_dialog.setMinimumWidth(400)
        
        layout = QVBoxLayout(options_dialog)
        
        # Info
        info_label = QLabel(f"Exporting termbase: <b>{tb_name}</b>")
        info_label.setWordWrap(True)
        layout.addWidget(info_label)
        
        # Metadata checkbox
        metadata_check = CheckmarkCheckBox(self.tr("Include all metadata (project, client, forbidden)"))
        metadata_check.setChecked(True)
        layout.addWidget(metadata_check)
        
        # Buttons
        button_box = QHBoxLayout()
        button_box.addStretch()
        
        cancel_btn = QPushButton(self.tr("Cancel"))
        cancel_btn.clicked.connect(options_dialog.reject)
        button_box.addWidget(cancel_btn)
        
        export_btn = QPushButton(self.tr("Export"))
        export_btn.setStyleSheet("font-weight: bold; background-color: #2196F3; color: white; padding: 8px 20px; border: none; outline: none;")
        export_btn.clicked.connect(options_dialog.accept)
        button_box.addWidget(export_btn)
        
        layout.addLayout(button_box)
        
        if options_dialog.exec() != QDialog.DialogCode.Accepted:
            return
        
        # Perform export
        from modules.termbase_import_export import TermbaseExporter
        exporter = TermbaseExporter(self.db_manager, termbase_mgr)
        
        self.log(f"📤 Exporting termbase to {filepath}...")
        
        success, message = exporter.export_tsv(
            termbase_id=termbase_id,
            filepath=filepath,
            include_metadata=metadata_check.isChecked()
        )
        
        # Show result
        if success:
            QMessageBox.information(self, "Export Complete", message)
            self.log(f"✓ {message}")
        else:
            QMessageBox.critical(self, "Export Failed", message)
            self.log(f"✗ Export failed: {message}")

    def _update_term_forbidden(self, term_id: int, forbidden: bool):
        """Update forbidden flag for a term"""
        try:
            cursor = self.db_manager.cursor
            with rollback_if_failed(self.db_manager.connection, self.log):
                cursor.execute("UPDATE termbase_terms SET forbidden = ? WHERE id = ?", (1 if forbidden else 0, term_id))
                self.db_manager.connection.commit()
            self.log(f"✓ Updated term {term_id} forbidden status to {forbidden}")
        except Exception as e:
            self.log(f"✗ Error updating term forbidden status: {e}")

    def _show_edit_terms_dialog(self, termbase_mgr, termbase_table, refresh_callback):
        """Show dialog to edit terms in selected termbase"""
        selected_row = termbase_table.currentRow()
        if selected_row < 0:
            QMessageBox.warning(self, "Error", "Please select a termbase first")
            return
        
        # Get termbase ID and name from table (column 1 is Name with ID stored in UserRole)
        name_item = termbase_table.item(selected_row, 1)
        if not name_item:
            QMessageBox.warning(self, "Error", "Could not find selected termbase")
            return
        
        termbase_id = name_item.data(Qt.ItemDataRole.UserRole)
        tb_name = name_item.text()
        
        if not termbase_id:
            QMessageBox.warning(self, "Error", "Could not find selected termbase")
            return
        
        # Create dialog
        dialog = QDialog(self)
        dialog.setWindowTitle(f"Edit Terms - {tb_name}")
        dialog.setMinimumSize(700, 500)
        
        layout = QVBoxLayout()
        
        # Terms table
        terms_table = QTableWidget()
        terms_table.setColumnCount(7)
        terms_table.setHorizontalHeaderLabels(["Source", "Target", "Domain", "Notes", "Project", "Client", "Forbidden"])
        terms_table.horizontalHeader().setStretchLastSection(False)
        terms_table.setColumnWidth(0, 120)
        terms_table.setColumnWidth(1, 120)
        terms_table.setColumnWidth(2, 100)
        terms_table.setColumnWidth(3, 150)
        terms_table.setColumnWidth(4, 100)
        terms_table.setColumnWidth(5, 100)
        terms_table.setColumnWidth(6, 80)
        # v1.10.62: extended selection so the Reverse Direction action
        # can repair multiple legacy reversed entries at once.
        terms_table.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
        terms_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        terms_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        
        # Load terms
        def refresh_terms_table():
            terms = termbase_mgr.get_terms(termbase_id)
            terms_table.setRowCount(len(terms))
            
            for row, term in enumerate(terms):
                # Store term ID in first column (hidden from user)
                source_item = QTableWidgetItem(term['source_term'])
                source_item.setData(Qt.ItemDataRole.UserRole, term['id'])  # Store term ID
                terms_table.setItem(row, 0, source_item)
                
                terms_table.setItem(row, 1, QTableWidgetItem(term['target_term']))
                terms_table.setItem(row, 2, QTableWidgetItem(term['domain'] or ""))

                # Notes column (truncated for display)
                notes_text = term.get('notes', '') or ""
                if len(notes_text) > 50:
                    notes_text = notes_text[:47] + "..."
                terms_table.setItem(row, 3, QTableWidgetItem(notes_text))

                # Project and Client columns
                terms_table.setItem(row, 4, QTableWidgetItem(term.get('project', '') or ""))
                terms_table.setItem(row, 5, QTableWidgetItem(term.get('client', '') or ""))

                # Forbidden checkbox
                forbidden_check = CheckmarkCheckBox()
                forbidden_check.setChecked(term['forbidden'])
                forbidden_check.toggled.connect(lambda checked, t_id=term['id']: self._update_term_forbidden(t_id, checked))
                terms_table.setCellWidget(row, 6, forbidden_check)
        
        refresh_terms_table()
        layout.addWidget(QLabel(f"Terms in '{tb_name}':"), 0)
        layout.addWidget(terms_table, 1)
        
        # Add term section
        add_layout = QHBoxLayout()
        
        source_field = QLineEdit()
        source_field.setPlaceholderText(self.tr("Source term"))
        add_layout.addWidget(source_field)
        
        target_field = QLineEdit()
        target_field.setPlaceholderText(self.tr("Target term"))
        add_layout.addWidget(target_field)
        
        add_btn = QPushButton(self.tr("+ Add"))
        def add_term():
            source = source_field.text().strip()
            target = target_field.text().strip()
            if not source or not target:
                QMessageBox.warning(dialog, "Error", "Both source and target terms are required")
                return

            termbase_mgr.add_term(
                termbase_id=termbase_id,
                source_term=source,
                target_term=target
            )
            
            source_field.clear()
            target_field.clear()
            refresh_terms_table()
        
        add_btn.clicked.connect(add_term)
        add_layout.addWidget(add_btn)
        layout.addLayout(add_layout)
        
        # v1.10.62: Reverse source/target on selected term(s).
        # Repair action for the pre-v1.10.62 termbase-direction bug:
        # entries written with project source/target swapped relative
        # to the termbase's declared direction can be fixed in place
        # here. Works on a single selected row or multiple rows at
        # once. Mirrors the Trados v4.19.22 "Reverse source/target"
        # right-click action that solved the same bug there.
        def reverse_direction_on_selected():
            from PyQt6.QtWidgets import QMessageBox as _QMessageBox
            # Collect distinct selected rows. Items() returns
            # QTableWidgetItem objects, possibly multiple per row;
            # dedupe via a set of row indices.
            selected_rows = sorted({
                item.row() for item in terms_table.selectedItems()
            })
            if not selected_rows:
                _QMessageBox.warning(
                    dialog, "No selection",
                    "Select one or more terms first, then use this action "
                    "to swap their source ↔ target text + language tags."
                )
                return

            # Collect the (term_id, source, target) tuples for confirmation.
            entries_to_reverse = []
            for row in selected_rows:
                id_item = terms_table.item(row, 0)
                if not id_item:
                    continue
                term_id = id_item.data(Qt.ItemDataRole.UserRole)
                if not term_id:
                    continue
                source = terms_table.item(row, 0).text() if terms_table.item(row, 0) else ''
                target = terms_table.item(row, 1).text() if terms_table.item(row, 1) else ''
                entries_to_reverse.append((term_id, source, target))

            if not entries_to_reverse:
                return

            # Confirmation dialog. Show the first few rows so the user
            # can sanity-check what's about to flip.
            preview_lines = []
            for tid, s, t in entries_to_reverse[:5]:
                preview_lines.append(f"  • '{s}' ↔ '{t}'")
            preview = "\n".join(preview_lines)
            extra = (f"\n  ... and {len(entries_to_reverse) - 5} more"
                     if len(entries_to_reverse) > 5 else "")

            reply = _QMessageBox.question(
                dialog, "Reverse source/target",
                f"Reverse source ↔ target on {len(entries_to_reverse)} "
                f"term(s)?\n\n{preview}{extra}\n\n"
                f"This swaps each entry's source_term, target_term, "
                f"source_lang, target_lang, source_abbreviation, and "
                f"target_abbreviation. It also flips each synonym's "
                f"language tag (source ↔ target). The change is committed "
                f"in a single transaction.\n\n"
                f"Use this to fix terms that were saved with text in the "
                f"wrong columns before the v1.10.62 termbase-direction fix.",
                _QMessageBox.StandardButton.Yes | _QMessageBox.StandardButton.No,
                _QMessageBox.StandardButton.No,
            )
            if reply != _QMessageBox.StandardButton.Yes:
                return

            cursor = self.db_manager.cursor
            try:
                cursor.execute("BEGIN")
                for tid, _s, _t in entries_to_reverse:
                    # Swap text + lang + abbreviation pairs in one row.
                    cursor.execute(
                        """
                        UPDATE termbase_terms
                        SET source_term = target_term,
                            target_term = source_term,
                            source_lang = target_lang,
                            target_lang = source_lang,
                            source_abbreviation = target_abbreviation,
                            target_abbreviation = source_abbreviation
                        WHERE id = ?
                        """,
                        (tid,),
                    )
                    # Flip every synonym's language: 'source' → 'target'
                    # and vice versa. Done in two passes via a temporary
                    # sentinel to avoid mid-update collisions.
                    cursor.execute(
                        "UPDATE termbase_synonyms SET language = '__tmp__' "
                        "WHERE term_id = ? AND language = 'source'",
                        (tid,),
                    )
                    cursor.execute(
                        "UPDATE termbase_synonyms SET language = 'source' "
                        "WHERE term_id = ? AND language = 'target'",
                        (tid,),
                    )
                    cursor.execute(
                        "UPDATE termbase_synonyms SET language = 'target' "
                        "WHERE term_id = ? AND language = '__tmp__'",
                        (tid,),
                    )
                self.db_manager.connection.commit()
                self.log(f"🔄 Reversed direction on {len(entries_to_reverse)} term(s) in '{tb_name}'")
            except Exception as e:
                self.db_manager.connection.rollback()
                _QMessageBox.critical(
                    dialog, "Reverse failed",
                    f"Could not reverse direction:\n\n{e}\n\n"
                    f"No changes were committed (transaction rolled back).",
                )
                return

            refresh_terms_table()
            # Rebuild the in-memory index so TermLens immediately
            # reflects the swap.
            try:
                with self.termbase_cache_lock:
                    self.termbase_cache.clear()
                self._build_termbase_index()
            except Exception as e:
                self.log(f"⚠️ Index rebuild after reverse failed: {e}")

        # Right-click context menu on terms_table — same action, more
        # discoverable for power users.
        def show_terms_context_menu(pos):
            from PyQt6.QtWidgets import QMenu as _QMenu
            from PyQt6.QtGui import QAction as _QAction
            menu = _QMenu(terms_table)
            reverse_action = _QAction("🔄 Reverse source / target on selected", menu)
            reverse_action.triggered.connect(reverse_direction_on_selected)
            menu.addAction(reverse_action)
            menu.exec(terms_table.viewport().mapToGlobal(pos))

        terms_table.customContextMenuRequested.connect(show_terms_context_menu)

        # Action buttons
        action_layout = QHBoxLayout()

        # Reverse direction button (v1.10.62 termbase-direction repair).
        reverse_dir_btn = QPushButton(self.tr("🔄 Reverse Source/Target"))
        reverse_dir_btn.setToolTip(
            "Swap source ↔ target on selected term(s). Use this to repair "
            "terms that were saved in the wrong direction before the "
            "v1.10.62 fix."
        )
        reverse_dir_btn.clicked.connect(reverse_direction_on_selected)
        action_layout.addWidget(reverse_dir_btn)

        # Edit selected term button
        edit_term_btn = QPushButton(self.tr("✏️ Edit Selected Term"))
        def edit_selected_term():
            selected_row = terms_table.currentRow()
            if selected_row < 0:
                QMessageBox.warning(dialog, "Error", "Please select a term to edit")
                return
            
            term_id = terms_table.item(selected_row, 0).data(Qt.ItemDataRole.UserRole)
            if term_id:
                from modules.termbase_entry_editor import TermbaseEntryEditor
                edit_dialog = TermbaseEntryEditor(
                    parent=self,
                    db_manager=self.db_manager,
                    termbase_id=termbase_id,
                    term_id=term_id
                )
                if edit_dialog.exec():
                    refresh_terms_table()
        
        edit_term_btn.clicked.connect(edit_selected_term)
        action_layout.addWidget(edit_term_btn)
        
        # Delete selected term button
        delete_term_btn = QPushButton(self.tr("🗑️ Delete Selected Term"))
        delete_term_btn.setStyleSheet("background-color: #f44336; color: white; font-weight: bold; padding: 3px 5px;")
        def delete_selected_term():
            selected_row = terms_table.currentRow()
            if selected_row < 0:
                QMessageBox.warning(dialog, "Error", "Please select a term to delete")
                return
            
            term_id = terms_table.item(selected_row, 0).data(Qt.ItemDataRole.UserRole)
            source_term = terms_table.item(selected_row, 0).text()
            target_term = terms_table.item(selected_row, 1).text()
            
            if term_id:
                reply = QMessageBox.question(
                    dialog,
                    "Confirm Deletion",
                    f"Delete this term?\n\nSource: {source_term}\nTarget: {target_term}\n\nThis action cannot be undone.",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No
                )
                
                if reply == QMessageBox.StandardButton.Yes:
                    try:
                        cursor = self.db_manager.cursor
                        with rollback_if_failed(self.db_manager.connection, self.log):
                            cursor.execute("DELETE FROM termbase_terms WHERE id = ?", (term_id,))
                            self.db_manager.connection.commit()
                        self.log(f"✓ Deleted term: {source_term} → {target_term}")
                        refresh_terms_table()
                        # v1.10.64: full post-delete refresh chain
                        # (clears cache, rebuilds index, refreshes
                        # TermLens for current segment) so the just-
                        # deleted term stops appearing as a match.
                        self._post_termbase_delete_refresh()
                    except Exception as e:
                        QMessageBox.critical(dialog, "Error", f"Failed to delete term: {e}")
        
        delete_term_btn.clicked.connect(delete_selected_term)
        action_layout.addWidget(delete_term_btn)
        
        action_layout.addStretch()
        layout.addLayout(action_layout)
        
        # Close button
        close_btn = QPushButton(self.tr("Close"))
        close_btn.clicked.connect(dialog.accept)
        layout.addWidget(close_btn)
        
        dialog.setLayout(layout)
        dialog.exec()

    def _build_termbase_index(self):
        """
        Build in-memory index of ALL terms from activated termbases (v1.9.182).

        This is called ONCE on project load and replaces thousands of per-word
        database queries with a single bulk load + fast in-memory lookups.

        Performance: Reduces 349-segment termbase search from 365 seconds to <1 second.
        """
        import re
        import time
        start_time = time.time()

        if not self.current_project or not hasattr(self, 'db_manager') or not self.db_manager:
            return

        project_id = self.current_project.id if hasattr(self.current_project, 'id') else None

        # Project languages, normalised to ISO codes for direction comparison.
        # Empty string when not set – that disables the swap logic below and
        # the index is built in its raw (forward) orientation, matching the
        # historical behaviour for projects without language metadata.
        project_source_code = self._convert_language_to_code(self.current_project.source_lang or '') or ''
        project_target_code = self._convert_language_to_code(self.current_project.target_lang or '') or ''

        # Query ALL terms from activated termbases in ONE query.
        # Includes tb.source_lang / tb.target_lang so we can detect
        # reverse-direction termbases and orient them to the project,
        # and t.is_nontranslatable so NT highlighting can branch off
        # the same index without a second pass over the database.
        #
        # v1.10.73 (Tier 1): also pulls ``definition``, ``url``,
        # ``source_abbreviation`` and ``target_abbreviation``. These
        # columns have existed in the schema for ages but were never
        # SELECTed here, so the TermBlock tooltip's pre-existing
        # ``_meta_lines`` rendering helper got empty strings and
        # silently suppressed the rows. Adding them to the SELECT
        # makes the tooltip immediately show Abbr / Definition /
        # Domain / URL with zero extra UI code. ``COALESCE(…, '')``
        # so legacy databases without those columns (or with NULLs)
        # come back as empty strings rather than blowing up the
        # SELECT or feeding ``None`` downstream.
        # v1.10.360: the project-termbase role is activation-based only —
        # ``ta.priority = 1`` (what the Termbases tab shows as the pink
        # Project tick). The legacy ``tb.is_project_termbase`` flag is no
        # longer consulted: a stale flag used to resurrect termbases with
        # Read unticked into the index and paint them pink.
        query = """
            SELECT
                t.id, t.source_term, t.target_term, t.termbase_id,
                t.domain, t.notes, t.project, t.client, t.forbidden,
                CASE WHEN COALESCE(ta.priority, 0) = 1 THEN 1 ELSE 0 END as is_project_termbase,
                tb.name as termbase_name,
                tb.source_lang as tb_source_lang, tb.target_lang as tb_target_lang,
                CASE WHEN COALESCE(ta.priority, 0) = 1 THEN 1 ELSE 0 END as ranking,
                COALESCE(t.is_nontranslatable, 0) as is_nontranslatable,
                COALESCE(t.definition, '') as definition,
                COALESCE(t.url, '') as url,
                COALESCE(t.source_abbreviation, '') as source_abbreviation,
                COALESCE(t.target_abbreviation, '') as target_abbreviation
            FROM termbase_terms t
            LEFT JOIN termbases tb ON CAST(t.termbase_id AS INTEGER) = tb.id
            LEFT JOIN termbase_activation ta ON ta.termbase_id = tb.id
                AND ta.project_id = ? AND ta.is_active = 1
            WHERE ta.is_active = 1
        """

        # v1.10.73 (Tier 2): bulk-load every term's synonyms in ONE
        # extra query, keyed by term_id, before iterating the main
        # result set. Same single-query pattern Trados uses in
        # ``BulkLoadTargetSynonyms`` — orders of magnitude faster than
        # one query per term, and the synonym row count (typically a
        # few hundred per termbase) stays manageable. Synonyms are
        # then attached to each index entry below, so
        # ``_search_termbase_in_memory`` can copy them into match
        # results without re-querying the DB.
        synonyms_by_term = self._load_synonyms_bulk()

        new_index = []
        try:
            self.db_manager.cursor.execute(query, [project_id or 0])
            rows = self.db_manager.cursor.fetchall()

            for row in rows:
                source_term = row[1]
                target_term = row[2]
                tb_source_code = self._convert_language_to_code(row[11] or '') or ''
                tb_target_code = self._convert_language_to_code(row[12] or '') or ''
                # v1.10.73 (Tier 1) extra metadata columns.
                row_definition = row[15] if len(row) > 15 else ''
                row_url = row[16] if len(row) > 16 else ''
                row_src_abbr = row[17] if len(row) > 17 else ''
                row_tgt_abbr = row[18] if len(row) > 18 else ''

                # If the termbase runs the opposite direction to the project
                # (e.g. termbase en→nl in an nl→en project), swap source/target
                # so the index lookup key is always in the project's source
                # language. Without this swap, _search_termbase_in_memory
                # would search Dutch segment text against English source_term
                # values and find nothing.
                #
                # v1.10.73 (Tier 1): also swap source/target
                # **abbreviations** in lockstep — they're per-language
                # too, so they need the same flip when the termbase
                # runs opposite the project direction. Otherwise the
                # tooltip would show the abbreviation pair in the
                # wrong order (English abbreviation labelled as
                # Dutch and vice versa).
                #
                # v1.10.73 (Tier 2): also swap source/target
                # **synonym lists**. Same reason — synonyms are
                # per-language, so when source/target swap their
                # synonym lists swap with them.
                synonym_pair = synonyms_by_term.get(row[0], {'source': [], 'target': []})
                source_synonyms = list(synonym_pair.get('source', []))
                target_synonyms = list(synonym_pair.get('target', []))
                if (project_source_code and project_target_code
                        and tb_source_code and tb_target_code
                        and tb_source_code == project_target_code
                        and tb_target_code == project_source_code):
                    source_term, target_term = target_term, source_term
                    row_src_abbr, row_tgt_abbr = row_tgt_abbr, row_src_abbr
                    source_synonyms, target_synonyms = target_synonyms, source_synonyms

                if not source_term:
                    continue

                source_term_lower = source_term.lower().strip()
                if len(source_term_lower) < 2:
                    continue

                # Pre-compile regex pattern for word-boundary matching
                # This avoids recompiling the same pattern thousands of times
                try:
                    if _contains_cjk(source_term_lower):
                        # CJK/Thai: no inter-word spaces, so match as a substring
                        # (a word-boundary would never fire inside a run). See
                        # _contains_cjk.
                        pattern = re.compile(re.escape(source_term_lower))
                    # Handle terms with punctuation differently
                    elif any(c in source_term_lower for c in '.%,/-'):
                        pattern = re.compile(r'(?<!\w)' + re.escape(source_term_lower) + r'(?!\w)')
                    else:
                        pattern = re.compile(r'\b' + re.escape(source_term_lower) + r'\b')
                except re.error:
                    # If regex fails, use simple substring matching
                    pattern = None

                # v1.10.75 (Tier 3c): build abbreviation patterns
                # for pipe-separated source_abbreviation variants
                # (e.g. "GC|G.C.|gc"). Each variant gets its own
                # regex with the same word-boundary semantics as the
                # main term pattern. Stored as
                # ``[(variant_lower, compiled_pattern), …]`` so the
                # search loop can iterate variants and report which
                # one matched. Empty source_abbreviation OR empty
                # target_abbreviation means "no abbreviation chip" —
                # we want both sides because the abbreviation chip
                # displays target_abbreviation as the primary text;
                # without one the chip would have nothing to show.
                abbreviation_variants = []
                if row_src_abbr and row_tgt_abbr:
                    for variant in row_src_abbr.split('|'):
                        variant = variant.strip()
                        if not variant:
                            continue
                        variant_lower = variant.lower()
                        try:
                            if _contains_cjk(variant_lower):
                                v_pattern = re.compile(re.escape(variant_lower))
                            elif any(c in variant_lower for c in '.%,/-'):
                                v_pattern = re.compile(
                                    r'(?<!\w)' + re.escape(variant_lower) + r'(?!\w)')
                            else:
                                v_pattern = re.compile(
                                    r'\b' + re.escape(variant_lower) + r'\b')
                            abbreviation_variants.append((variant, variant_lower, v_pattern))
                        except re.error:
                            pass

                # v1.10.199: pre-compile source-synonym patterns so
                # they're searchable too. Until v1.10.198 the source
                # synonyms were loaded into the index as metadata but
                # the search loop only tested the main source_term
                # pattern + abbreviation patterns — synonyms were
                # carried along for tooltip display, never matched.
                # Result: a user who added "een verdere uitvoering" as
                # a source synonym for the main "een verdere
                # uitvoeringsvorm" entry got no termbase match when a
                # segment used the synonym instead of the main form.
                # Reported via the Trados-style "Similar Term Found"
                # → "Add as Synonym" flow; fixed by mirroring the
                # abbreviation_variants pattern for source_synonyms.
                source_synonym_patterns = []
                for syn_text in source_synonyms:
                    if not syn_text:
                        continue
                    syn_lower = syn_text.lower().strip()
                    if len(syn_lower) < 2:
                        continue
                    try:
                        if _contains_cjk(syn_lower):
                            syn_pattern = re.compile(re.escape(syn_lower))
                        elif any(c in syn_lower for c in '.%,/-'):
                            syn_pattern = re.compile(
                                r'(?<!\w)' + re.escape(syn_lower) + r'(?!\w)')
                        else:
                            syn_pattern = re.compile(
                                r'\b' + re.escape(syn_lower) + r'\b')
                        source_synonym_patterns.append(
                            (syn_text, syn_lower, syn_pattern))
                    except re.error:
                        pass

                new_index.append({
                    'term_id': row[0],
                    'source_term': source_term,
                    'source_term_lower': source_term_lower,
                    'target_term': target_term,
                    'termbase_id': row[3],
                    'domain': row[4],
                    'notes': row[5],
                    'project': row[6],
                    'client': row[7],
                    'forbidden': row[8],
                    'is_project_termbase': row[9],
                    'termbase_name': row[10],
                    'ranking': row[13],
                    'is_nontranslatable': bool(row[14]),
                    # v1.10.73 (Tier 1): metadata fields that were
                    # in the schema but never SELECTed before.
                    'definition': row_definition,
                    'url': row_url,
                    'source_abbreviation': row_src_abbr,
                    'target_abbreviation': row_tgt_abbr,
                    # v1.10.73 (Tier 2): synonyms bulk-loaded from
                    # termbase_synonyms, already direction-swapped
                    # above so lists match the project's source/
                    # target orientation.
                    'source_synonyms': source_synonyms,
                    'target_synonyms': target_synonyms,
                    # v1.10.75 (Tier 3c): pipe-split abbreviation
                    # variants with pre-compiled regexes for the
                    # abbreviation-as-primary chip rendering.
                    'abbreviation_variants': abbreviation_variants,
                    # v1.10.199: source-synonym patterns for matching
                    # (see _search_termbase_in_memory). Built above
                    # alongside abbreviation_variants for the same
                    # word-boundary regex reasons.
                    'source_synonym_patterns': source_synonym_patterns,
                    'pattern': pattern,  # Pre-compiled regex (main source_term)
                })

            # Sort by term length (longest first) for better phrase matching
            new_index.sort(key=lambda x: len(x['source_term_lower']), reverse=True)

            # Thread-safe update of the index
            with self.termbase_index_lock:
                self.termbase_index = new_index
                # v1.10.153: mark the index as "actually built" so
                # find_termbase_matches_in_source knows an empty list
                # means "no active terms" rather than "not yet built".
                self._termbase_index_built = True
                self.termbase_index_generation = getattr(self, 'termbase_index_generation', 0) + 1

            # Everything worked out from the old terms goes. The match cache
            # keeps a term list per segment, and nothing cleared it here: a
            # segment looked ahead to before a termbase was ticked, or before
            # the index was built at all, kept its empty list and TermLens
            # showed "No matches" there until F5 (Nov 2026). The callers
            # cleared termbase_cache, most of them, and never this one.
            with self.termbase_cache_lock:
                self.termbase_cache.clear()
            with self.translation_matches_cache_lock:
                self.translation_matches_cache.clear()

            elapsed = time.time() - start_time
            self.log(f"✅ Built termbase index: {len(new_index)} terms in {elapsed:.2f}s")

            # v1.10.69: take a fresh snapshot of the termbase tables
            # so the file watcher's snapshot-gate (see
            # _on_termbase_db_debounce_fire) recognises subsequent
            # own-writes as "already accounted for" and skips
            # redundant rebuilds. Without this, every own-write would
            # cause a no-op rebuild ~2 s later when the file watcher
            # fired — harmless but wasteful.
            try:
                self._termbase_db_last_snapshot = self._snapshot_termbase_db_state()
            except Exception:
                pass

        except Exception as e:
            self.log(f"❌ Failed to build termbase index: {e}")
            import traceback
            self.log(traceback.format_exc())

    def _search_termbase_in_memory(self, source_text: str) -> dict:
        """
        Search termbase using in-memory index (v1.9.182).

        This replaces _search_termbases_thread_safe() for batch operations.
        Instead of N database queries (one per word), we do:
        - 1 pass through the index (typically ~1000 terms)
        - Fast string 'in' check + pre-compiled regex validation

        Performance: <1ms per segment vs 1+ second per segment.
        """
        if not source_text:
            return {}

        with self.termbase_index_lock:
            if not self.termbase_index:
                return {}
            index = self.termbase_index  # Local reference for thread safety

        source_lower = source_text.lower()
        matches = {}

        for term in index:
            term_lower = term['source_term_lower']

            # Quick substring check first (very fast, implemented in C)
            main_in_source = term_lower in source_lower

            # Word boundary validation using pre-compiled pattern.
            # When the main source_term doesn't substring-hit, we
            # still need to consider abbreviation matches below.
            main_matched = False
            if main_in_source:
                pattern = term.get('pattern')
                if pattern is None or pattern.search(source_lower):
                    main_matched = True

            # v1.10.75 (Tier 3c): try every abbreviation variant
            # in addition to the main source_term. When the
            # abbreviation form ("GC") appears in the source as a
            # separate token, register a SECOND entry in the matches
            # dict keyed by a synthetic ``abbr_<id>_<idx>`` key, so
            # the TermLens widget gets a chip for the abbreviation
            # form (purple bg, target_abbreviation as display text)
            # in addition to (or instead of) the full-term chip.
            abbr_hits = []  # [(variant_text, variant_lower)]
            for variant_text, variant_lower, abbr_pattern in term.get('abbreviation_variants', []):
                if variant_lower not in source_lower:
                    continue
                if abbr_pattern.search(source_lower):
                    abbr_hits.append((variant_text, variant_lower))

            # v1.10.199: also try source-synonym patterns. Reported
            # bug: user added "een verdere uitvoering" as a source
            # synonym for "een verdere uitvoeringsvorm" via the
            # Trados-style "Similar Term Found" merge dialog, then
            # got no termbase match when a segment used the synonym
            # form. Cause: pre-v1.10.199 the search only tested the
            # main source_term pattern + abbreviation patterns;
            # source synonyms were loaded into the index for
            # tooltip-display purposes but never actually searched.
            # The fix is symmetric with the abbreviation path —
            # quick substring check then word-boundary regex.
            # Issue #114: synonyms are looked for outside the main term's
            # own matches, so one that is part of it ("ethylketoxime" in
            # "methyl-ethylketoxime") doesn't put a second chip under it.
            syn_haystack = source_lower
            if main_matched and term.get('source_synonym_patterns'):
                pattern = term.get('pattern') or re.compile(re.escape(term_lower))
                chars = list(source_lower)
                for m in pattern.finditer(source_lower):
                    chars[m.start():m.end()] = ' ' * (m.end() - m.start())
                syn_haystack = ''.join(chars)
            syn_hits = []  # [(syn_text, syn_lower)]
            for syn_text, syn_lower, syn_pattern in term.get('source_synonym_patterns', []):
                if syn_lower not in syn_haystack:
                    continue
                if syn_pattern.search(syn_haystack):
                    syn_hits.append((syn_text, syn_lower))

            if not (main_matched or abbr_hits or syn_hits):
                continue

            # Term matches! Add to results
            term_id = term['term_id']

            # v1.10.75 (Tier 3c): build a base match dict that both
            # the main entry and any abbreviation entries share —
            # they all carry the same metadata (definition / domain /
            # notes / URL / synonyms / flags / etc.) since those are
            # properties of the underlying termbase term, not of the
            # particular surface form that matched.
            base = {
                'term_id': term_id,
                'termbase_id': term['termbase_id'],
                'termbase_name': term['termbase_name'],
                'ranking': term['ranking'],
                'is_project_termbase': term['is_project_termbase'],
                'forbidden': term['forbidden'],
                'domain': term['domain'],
                'notes': term['notes'],
                'project': term['project'],
                'client': term['client'],
                # v1.10.73 (Tier 1): metadata fields surfaced for
                # the TermBlock tooltip's ``_meta_lines`` renderer.
                'definition': term.get('definition', ''),
                'url': term.get('url', ''),
                'source_abbreviation': term.get('source_abbreviation', ''),
                'target_abbreviation': term.get('target_abbreviation', ''),
                # v1.10.309: the full term forms, so a chip that matched via an
                # abbreviation (or synonym) can still show what it stands for in
                # the tooltip (source_term/target_term below hold the matched
                # surface form, which for an abbreviation match is just "ASR").
                'full_source_term': term['source_term'],
                'full_target_term': term['target_term'],
                # v1.10.73 (Tier 2): synonyms.
                'source_synonyms': term.get('source_synonyms', []),
                'target_synonyms': term.get('target_synonyms', []),
                # v1.10.75 (Tier 3a): forbidden / NT flags drive chip bg.
                'is_nontranslatable': term.get('is_nontranslatable', False),
            }

            if main_matched:
                matches[term_id] = dict(base, **{
                    'source': term['source_term'],
                    'translation': term['target_term'],
                    'matched_via_abbreviation': False,
                })
            # Issue #114: every source synonym the segment uses gets a chip
            # of its own at its own position – also when the main term is
            # in the same segment ("methyl-ethylketoxime (MEKO)"), which
            # before only showed the main term. A synonym chip offers the
            # identical target synonym when there is one (MEKO → MEKO),
            # otherwise the main target term; the rest stay alternatives.
            # v1.10.199: with no main-term hit, the first synonym takes the
            # term's own key.
            from modules.termbase_manager import synonym_chip_translation
            for i, (syn_text, _syn_lower) in enumerate(syn_hits):
                translation, alternatives = synonym_chip_translation(
                    syn_text, term['target_term'], term.get('target_synonyms', []))
                key = term_id if (i == 0 and not main_matched) else f"syn_{term_id}_{i}"
                matches[key] = dict(base, **{
                    'source': syn_text,
                    'translation': translation,
                    'target_synonyms': alternatives,
                    'matched_via_abbreviation': False,
                    'matched_via_synonym': True,
                })

            # v1.10.75 (Tier 3c): one entry per matched abbreviation
            # variant, keyed by a synthetic string so it doesn't
            # collide with the main ``matches[term_id]`` integer key.
            # ``source`` is the variant text ("GC"); ``translation``
            # is the target_abbreviation (also "GC" typically) so the
            # chip displays the abbreviation pair, not the full term.
            for i, (variant_text, variant_lower) in enumerate(abbr_hits):
                synthetic_key = f"abbr_{term_id}_{i}"
                matches[synthetic_key] = dict(base, **{
                    'source': variant_text,
                    'translation': term.get('target_abbreviation', '') or term['target_term'],
                    'matched_via_abbreviation': True,
                })

        return matches

    def _start_termbase_batch_worker(self):
        """
        Start background thread to batch-process termbase matches for all segments.
        This pre-fills the cache while the user works on the project.
        """
        if not self.current_project or len(self.current_project.segments) == 0:
            return

        # Build in-memory termbase index FIRST (v1.9.182)
        # This is the key optimization: load all terms once, then do fast in-memory lookups
        self._build_termbase_index()

        # 🧪 EXPERIMENTAL: Skip batch worker if cache kill switch is enabled
        if getattr(self, 'disable_all_caches', False):
            self.log("🧪 Termbase batch worker SKIPPED (caches disabled)")
            return

        # Stop any existing worker thread
        self.termbase_batch_stop_event.set()
        if self.termbase_batch_worker_thread and self.termbase_batch_worker_thread.is_alive():
            self.log("⏹️  Stopping existing termbase batch worker...")
            self.termbase_batch_worker_thread.join(timeout=2)

        # Reset stop event for new worker
        self.termbase_batch_stop_event.clear()

        # Start new background worker thread
        segment_count = len(self.current_project.segments)
        self.log(f"🔄 Starting background termbase batch processor for {segment_count} segments...")
        
        self.termbase_batch_worker_thread = threading.Thread(
            target=self._termbase_batch_worker_run,
            args=(self.current_project.segments,),
            daemon=True  # Daemon thread - won't prevent program exit
        )
        self.termbase_batch_worker_thread.start()

    def _termbase_batch_worker_run(self, segments):
        """
        Background worker thread: process all segments and populate termbase cache.
        Runs in separate thread to not block UI.

        v1.9.182: Now uses in-memory termbase index for 1000x faster lookups.
        Old approach: 365 seconds for 349 segments (1 second/segment)
        New approach: <1 second for 349 segments (<3ms/segment)
        """
        if not segments:
            return

        try:
            processed = 0
            cached = 0
            with_matches = 0
            start_time = time.time()

            for segment in segments:
                # Check if stop event was signaled (user closed project or started new one)
                if self.termbase_batch_stop_event.is_set():
                    self.log(f"⏹️  Termbase batch worker stopped by user (processed {processed} segments)")
                    break

                segment_id = segment.id

                # Skip if already in cache (thread-safe check)
                with self.termbase_cache_lock:
                    if segment_id in self.termbase_cache:
                        cached += 1
                        continue

                # v1.9.182: Use in-memory index for instant lookup (no database queries!)
                try:
                    matches = self._search_termbase_in_memory(segment.source)

                    # Store in cache (thread-safe) - even empty results to avoid re-lookup
                    with self.termbase_cache_lock:
                        self.termbase_cache[segment_id] = matches

                    processed += 1
                    if matches:
                        with_matches += 1

                except Exception as e:
                    self.log(f"❌ Error processing segment {segment_id} in batch worker: {e}")
                    continue

            elapsed = time.time() - start_time
            total_cached = len(self.termbase_cache)
            rate = processed / elapsed if elapsed > 0 else 0
            self.log(f"✅ Termbase batch worker complete: {processed} segments in {elapsed:.2f}s " +
                     f"({rate:.0f} seg/sec, {with_matches} with matches)")

        except Exception as e:
            self.log(f"❌ Termbase batch worker error: {e}")
            import traceback
            self.log(traceback.format_exc())

    def _search_termbases_thread_safe(self, source_text: str, cursor, source_lang: str = None, target_lang: str = None, project_id: int = None) -> Dict[str, str]:
        """
        Search termbases using a provided cursor (thread-safe for background threads).
        This method allows background workers to query the database without SQLite threading errors.

        Implements BIDIRECTIONAL matching: searches both source_term and target_term columns.
        When a match is found on target_term, source and target are swapped in the result.
        This matches memoQ/Trados behavior where a NL→EN termbase also works for EN→NL projects.

        Args:
            source_text: The source text to search for
            cursor: A database cursor from a thread-local connection
            source_lang: Source language code
            target_lang: Target language code
            project_id: Current project ID (required to filter by activated termbases)

        Returns:
            Dictionary of {term: translation} matches
        """
        if not source_text or not cursor:
            return {}

        try:
            # Convert language names to codes (match interactive search logic)
            source_lang_code = self._convert_language_to_code(source_lang) if source_lang else None
            target_lang_code = self._convert_language_to_code(target_lang) if target_lang else None

            words = source_text.split()
            source_text_lower = source_text.lower()
            matches: Dict[str, Dict] = {}

            import re

            for word in words:
                clean_word = word.strip('.,!?;:')
                if len(clean_word) < 2:
                    continue

                try:
                    # JOIN termbases AND termbase_activation to filter by activated termbases
                    # This matches the logic in database_manager.py search_termbases()
                    # BIDIRECTIONAL: Search both source_term (forward) and target_term (reverse)
                    # Using UNION to combine both directions
                    query = """
                        SELECT * FROM (
                            -- Forward match: search source_term
                            SELECT
                                t.id, t.source_term, t.target_term, t.termbase_id,
                                t.domain, t.notes, t.project, t.client, t.forbidden,
                                CASE WHEN COALESCE(ta.priority, 0) = 1 THEN 1 ELSE 0 END as is_project_termbase,
                                tb.name as termbase_name,
                                CASE WHEN COALESCE(ta.priority, 0) = 1 THEN 1 ELSE 0 END as ranking,
                                'source' as match_direction
                            FROM termbase_terms t
                            LEFT JOIN termbases tb ON CAST(t.termbase_id AS INTEGER) = tb.id
                            LEFT JOIN termbase_activation ta ON ta.termbase_id = tb.id AND ta.project_id = ? AND ta.is_active = 1
                            WHERE LOWER(t.source_term) LIKE ?
                            AND ta.is_active = 1
                    """
                    params = [project_id if project_id else 0, f"%{clean_word.lower()}%"]

                    # Language filters for forward query
                    if source_lang_code:
                        query += " AND (t.source_lang = ? OR (t.source_lang IS NULL AND tb.source_lang = ?) OR (t.source_lang IS NULL AND tb.source_lang IS NULL))"
                        params.extend([source_lang_code, source_lang_code])
                    if target_lang_code:
                        query += " AND (t.target_lang = ? OR (t.target_lang IS NULL AND tb.target_lang = ?) OR (t.target_lang IS NULL AND tb.target_lang IS NULL))"
                        params.extend([target_lang_code, target_lang_code])

                    # Reverse match: search target_term, swap source/target in output
                    query += """
                            UNION ALL
                            -- Reverse match: search target_term, swap columns
                            SELECT
                                t.id, t.target_term as source_term, t.source_term as target_term,
                                t.termbase_id,
                                t.domain, t.notes, t.project, t.client, t.forbidden,
                                CASE WHEN COALESCE(ta.priority, 0) = 1 THEN 1 ELSE 0 END as is_project_termbase,
                                tb.name as termbase_name,
                                CASE WHEN COALESCE(ta.priority, 0) = 1 THEN 1 ELSE 0 END as ranking,
                                'target' as match_direction
                            FROM termbase_terms t
                            LEFT JOIN termbases tb ON CAST(t.termbase_id AS INTEGER) = tb.id
                            LEFT JOIN termbase_activation ta ON ta.termbase_id = tb.id AND ta.project_id = ? AND ta.is_active = 1
                            WHERE LOWER(t.target_term) LIKE ?
                            AND ta.is_active = 1
                    """
                    params.extend([project_id if project_id else 0, f"%{clean_word.lower()}%"])

                    # Language filters for reverse query (swapped)
                    if source_lang_code:
                        # For reverse: source_lang filters target_lang column
                        query += " AND (t.target_lang = ? OR (t.target_lang IS NULL AND tb.target_lang = ?) OR (t.target_lang IS NULL AND tb.target_lang IS NULL))"
                        params.extend([source_lang_code, source_lang_code])
                    if target_lang_code:
                        # For reverse: target_lang filters source_lang column
                        query += " AND (t.source_lang = ? OR (t.source_lang IS NULL AND tb.source_lang = ?) OR (t.source_lang IS NULL AND tb.source_lang IS NULL))"
                        params.extend([target_lang_code, target_lang_code])

                    # Close UNION and limit
                    query += ") combined LIMIT 30"
                    cursor.execute(query, params)
                    results = cursor.fetchall()

                    for row in results:
                        # Uniform access (columns are already swapped for reverse matches)
                        source_term = row[1] if isinstance(row, tuple) else row['source_term']
                        target_term = row[2] if isinstance(row, tuple) else row['target_term']
                        match_direction = row[12] if isinstance(row, tuple) else row.get('match_direction', 'source')
                        if not source_term or not target_term:
                            continue

                        # Phrase-level validation with word boundaries (case-insensitive)
                        pattern = re.compile(r"\b" + re.escape(source_term.lower()) + r"\b")
                        if not pattern.search(source_text_lower):
                            continue

                        term_id = row[0] if isinstance(row, tuple) else row['id']
                        termbase_id = row[3] if isinstance(row, tuple) else row['termbase_id']
                        domain = row[4] if isinstance(row, tuple) else row['domain']
                        notes = row[5] if isinstance(row, tuple) else row['notes']
                        project = row[6] if isinstance(row, tuple) else row['project']
                        client = row[7] if isinstance(row, tuple) else row['client']
                        forbidden = row[8] if isinstance(row, tuple) else row['forbidden']
                        is_project_tb = row[9] if isinstance(row, tuple) else row['is_project_termbase']
                        termbase_name = row[10] if isinstance(row, tuple) else row['termbase_name']
                        ranking = row[11] if isinstance(row, tuple) else row.get('ranking', None)

                        existing = matches.get(source_term.strip())
                        # Deduplicate: keep numerically lowest ranking (highest priority)
                        # For project termbases, ranking is None so they always win
                        # Also prefer 'source' matches over 'target' matches when equal
                        if existing:
                            existing_ranking = existing.get('ranking', None)
                            existing_direction = existing.get('match_direction', 'source')
                            if is_project_tb:
                                # Project termbase always wins
                                pass
                            elif existing.get('is_project_termbase'):
                                # Existing is project termbase, skip this one
                                continue
                            elif existing_ranking is not None and ranking is not None:
                                # Both have rankings, keep lower (higher priority)
                                if existing_ranking < ranking:
                                    continue
                                elif existing_ranking == ranking:
                                    # Same ranking: prefer source match over target match
                                    if existing_direction == 'source' and match_direction == 'target':
                                        continue

                        matches[source_term.strip()] = {
                            'translation': target_term.strip(),
                            'term_id': term_id,
                            'termbase_id': termbase_id,
                            'ranking': ranking,
                            'domain': domain or '',
                            'notes': notes or '',
                            'project': project or '',
                            'client': client or '',
                            'forbidden': forbidden or False,
                            'is_project_termbase': bool(is_project_tb),
                            'termbase_name': termbase_name or '',
                            'target_synonyms': [],  # Will be populated below
                            'match_direction': match_direction  # Track if this was a reverse match
                        }

                        # Fetch synonyms for this term
                        # For reverse matches, fetch 'source' synonyms since they become targets
                        try:
                            synonym_lang = 'source' if match_direction == 'target' else 'target'
                            cursor.execute("""
                                SELECT synonym_text FROM termbase_synonyms
                                WHERE term_id = ? AND language = ? AND forbidden = 0
                            """, (term_id, synonym_lang))
                            synonym_rows = cursor.fetchall()
                            for syn_row in synonym_rows:
                                synonym = syn_row[0] if isinstance(syn_row, tuple) else syn_row['synonym_text']
                                if synonym:
                                    matches[source_term.strip()]['target_synonyms'].append(synonym)
                        except Exception:
                            pass  # Synonyms are optional, don't fail on error
                except Exception:
                    # Skip word on any error; continue with next word
                    continue

            return matches
        except Exception:
            return {}

    def stop_termbase_batch_worker(self):
        """Stop the background termbase batch worker gracefully"""
        if self.termbase_batch_worker_thread and self.termbase_batch_worker_thread.is_alive():
            self.log("⏹️  Stopping termbase batch worker...")
            self.termbase_batch_stop_event.set()
            self.termbase_batch_worker_thread.join(timeout=2)
            self.log("✓ Termbase batch worker stopped")

    def set_termlens_position(self, position, save=True):
        """Position the under-grid TermLens panel live.

        position: 'above' or 'below' to show it in that spot, or None to hide it.
        Reorders the vertical splitter so no project reopen is needed, and keeps
        the View-menu checkmarks in sync.
        """
        if not hasattr(self, 'bottom_tabs') or not hasattr(self, 'left_vertical_splitter'):
            return

        splitter = self.left_vertical_splitter
        tabs = self.bottom_tabs

        if position == 'above':
            if splitter.indexOf(tabs) != 0:
                splitter.insertWidget(0, tabs)  # moves it; grid drops to index 1
            tabs.setVisible(True)
            splitter.setSizes([200, 600])
            self.tabs_above_grid = True
            visible = True
        elif position == 'below':
            if splitter.indexOf(tabs) != 1:
                splitter.insertWidget(1, tabs)
            tabs.setVisible(True)
            splitter.setSizes([600, 200])
            self.tabs_above_grid = False
            visible = True
        else:  # hide; keep last-known position so re-showing returns there
            tabs.setVisible(False)
            visible = False

        # The under-grid dock now holds two tabs (TermLens + QuickTrans), so
        # the menu checkmarks depend on both visibility/position AND which tab
        # is active. _sync_bottom_dock_menu() works that out.
        self._sync_bottom_dock_menu()

        if save:
            settings = self.load_general_settings()
            settings['termlens_under_grid_visible'] = visible
            settings['tabs_above_grid'] = self.tabs_above_grid
            self.save_general_settings(settings)

    def _on_cell_selected_glossary_only(self, current_row):
        """Update glossary highlights and TermLens from cache when in filtered mode.
        Skips TM/MT/LLM lookups - only handles cheap, cache-based termbase operations."""
        try:
            if not self.current_project or current_row < 0:
                return

            id_item = self.table.item(current_row, 0)
            if not id_item:
                return
            try:
                segment_id = int(id_item.text())
            except (ValueError, AttributeError):
                return

            segment = next((seg for seg in self.current_project.segments if seg.id == segment_id), None)
            if not segment:
                return

            # --- Termbase grid highlighting (from cache) ---
            if self.enable_termbase_grid_highlighting:
                tb_dict = {}
                with self.termbase_cache_lock:
                    tb_dict = self.termbase_cache.get(segment_id, {})

                if not tb_dict:
                    # Fall back to translation_matches_cache
                    with self.translation_matches_cache_lock:
                        cached_matches = self.translation_matches_cache.get(segment_id)
                    if cached_matches:
                        cached_tb = cached_matches.get("Termbases", [])
                        if cached_tb:
                            for match in cached_tb:
                                term_id = match.metadata.get('term_id') if match.metadata else None
                                key = term_id if term_id is not None else match.source
                                tb_dict[key] = {
                                    'source': match.source,
                                    'translation': match.target,
                                    'ranking': match.metadata.get('ranking', 0) if match.metadata else 0,
                                    'is_project_termbase': match.metadata.get('is_project_termbase', False) if match.metadata else False,
                                    'forbidden': match.metadata.get('forbidden', False) if match.metadata else False,
                                    'termbase_name': match.metadata.get('termbase_name', '') if match.metadata else '',
                                    'notes': match.metadata.get('notes', '') if match.metadata else '',
                                    'term_id': term_id,
                                    'termbase_id': match.metadata.get('termbase_id') if match.metadata else None,
                                }
                            if tb_dict:
                                with self.termbase_cache_lock:
                                    self.termbase_cache[segment_id] = tb_dict

                if tb_dict:
                    self.highlight_source_with_termbase(current_row, segment.source, tb_dict)
                else:
                    # No cached matches – do a fresh (cheap) termbase-only lookup
                    fresh_tb = self.find_termbase_matches_in_source(segment.source)
                    if fresh_tb:
                        self.highlight_source_with_termbase(current_row, segment.source, fresh_tb)

            # --- TermLens pane update ---
            if self.current_project and (
                (hasattr(self, 'termlens_widget') and self.termlens_widget) or
                (hasattr(self, 'termlens_widget_match') and self.termlens_widget_match)
            ):
                termbase_matches = []
                with self.translation_matches_cache_lock:
                    tv_cached = self.translation_matches_cache.get(segment_id)
                if tv_cached:
                    termbase_matches = [
                        {
                            'source_term': match.source,
                            'target_term': match.target,
                            'termbase_name': match.metadata.get('termbase_name', '') if match.metadata else '',
                            'ranking': match.metadata.get('ranking', 0) if match.metadata else 0,
                            'is_project_termbase': match.metadata.get('is_project_termbase', False) if match.metadata else False,
                            'term_id': match.metadata.get('term_id') if match.metadata else None,
                            'termbase_id': match.metadata.get('termbase_id') if match.metadata else None,
                            'notes': match.metadata.get('notes', '') if match.metadata else ''
                        }
                        for match in tv_cached.get("Termbases", [])
                    ]

                nt_matches = self.find_nt_matches_in_source(segment.source)
                status_hint = self._get_termbase_status_hint()
                self._update_both_termlens(segment.source, termbase_matches, nt_matches, status_hint)
        except Exception as e:
            if self.debug_mode_enabled:
                self.log(f"Error in glossary-only cell selection: {e}")

    def get_termbase_code_map(self) -> Dict[int, str]:
        """Get mapping of termbase_id to custom codes (from user preferences)"""
        try:
            prefs = self._load_settings_section("ui")
            return prefs.get('termbase_code_map', {})  # {termbase_id: "CODE"}
        except:
            return {}

    def save_termbase_code_map(self, code_map: Dict[int, str]):
        """Save termbase code mappings to user preferences"""
        try:
            all_settings = self._load_unified_settings()
            all_settings.setdefault("ui", {})['termbase_code_map'] = code_map
            self._save_unified_settings(all_settings)
        except Exception as e:
            self.log(f"⚠ Could not save termbase code map: {str(e)}")

    def get_termbase_code(self, termbase_id: int, code_map: Optional[Dict[int, str]] = None) -> str:
        """
        Get code for a termbase (custom code or auto-generated from name)
        
        Args:
            termbase_id: The termbase ID
            code_map: Optional pre-loaded code map (for performance)
            
        Returns:
            Code string (e.g., "MED", "LEG", "TECH") or empty string
        """
        if code_map is None:
            code_map = self.get_termbase_code_map()
        
        # Check if user has set a custom code
        if termbase_id in code_map:
            return code_map[termbase_id]
        
        # Auto-generate code from termbase name
        try:
            termbase_name = None
            
            if hasattr(self, 'termbase_mgr') and self.termbase_mgr:
                termbase = self.termbase_mgr.get_termbase(termbase_id)
                if termbase:
                    termbase_name = termbase.get('name', '') if isinstance(termbase, dict) else getattr(termbase, 'name', '')
            
            # Fallback: query database directly
            if not termbase_name and hasattr(self, 'db_manager') and self.db_manager:
                cursor = self.db_manager.cursor
                cursor.execute("SELECT name FROM termbases WHERE id = ?", (termbase_id,))
                row = cursor.fetchone()
                if row:
                    termbase_name = row[0]
            
            if not termbase_name:
                return ""
            
            # Generate code from name: take first 3-4 uppercase letters
            # Examples: "Medical Dictionary" -> "MED", "Legal Terms" -> "LEG", "Technical Glossary" -> "TECH"
            words = termbase_name.split()
            if len(words) >= 2:
                # Take first letter of first two words, then add more if needed
                code = (words[0][0] + words[1][0]).upper()
                if len(code) < 3 and len(words[0]) >= 2:
                    code = words[0][:3].upper()
            else:
                # Single word: take first 3 uppercase letters
                code = termbase_name[:3].upper()
            
            # Ensure code is 2-4 characters
            if len(code) > 4:
                code = code[:4]
            
            return code
        except Exception as e:
            self.log(f"Error generating termbase code: {e}")
            return ""

    def find_termbase_matches_in_source(self, source_text: str) -> Dict[str, str]:
        """
        Find all termbase matches in source text
        Returns dict of {term: translation} for all matches found

        v1.9.182: Uses in-memory index for instant lookup when available.
        Falls back to per-word database queries if index not built.
        """
        if not source_text or not hasattr(self, 'db_manager') or not self.db_manager:
            return {}

        # v1.9.306: Defensive guard – strip any invisible character markers
        # that may have leaked into the source text (e.g. ·, →, °, ↵, \u200B)
        # so that multi-word terms like "hinge load" match correctly.
        if hasattr(self, 'reverse_invisible_replacements'):
            source_text = self.reverse_invisible_replacements(source_text)

        try:
            # v1.9.182: Use in-memory index for instant lookup (1000x faster)
            # The index is built on project load by _build_termbase_index().
            # v1.10.153: distinguish "index built but empty" (no active
            # terms — return {} instantly) from "index never built"
            # (rare — fall through to per-word SQL queries below). The
            # latter path is ~2-34 s on a 45-termbase database, so we
            # really want to avoid hitting it just because the user
            # deactivated all termbases for this project.
            with self.termbase_index_lock:
                index_built = getattr(self, '_termbase_index_built', False)
                has_terms = bool(self.termbase_index)

            # v1.10.200: if the in-memory index hasn't been built yet
            # (rare — only hit during the brief startup window before
            # the batch worker has run, or after an aborted load that
            # left the build flag unset), force a synchronous build
            # right now rather than falling through to the legacy
            # per-word SQL path. The legacy path doesn't search source
            # synonyms (only the main source_term is matched), so on
            # a project that uses synonyms heavily the user would
            # silently get fewer matches on early cell-clicks than on
            # later ones — inconsistent and confusing. Cost of the
            # build is well under a second on real-world termbase
            # sizes (5,686 terms in the reporting user's project →
            # ~0.7 s); same cost the post-load worker would have paid
            # anyway, just moved a few hundred ms forward in time.
            if not index_built:
                try:
                    self._build_termbase_index()
                    with self.termbase_index_lock:
                        index_built = getattr(self, '_termbase_index_built', False)
                        has_terms = bool(self.termbase_index)
                except Exception as e:
                    self.log(f"⚠ On-demand termbase index build failed: {e}")

            if index_built:
                if not has_terms:
                    return {}
                # Fast path: use pre-built in-memory index, then deduplicate
                matches = self._search_termbase_in_memory(source_text)
                return self._deduplicate_termbase_matches(matches)

            # True fallback: the index couldn't be built (no project,
            # DB unavailable, etc.). The per-word SQL path below
            # doesn't search source synonyms — synonyms in the
            # database go through ``_search_termbase_in_memory`` only.
            # This path is now exceedingly rare in practice (a build
            # was attempted just above), so the documented synonym
            # gap is acceptable.
            source_lang = self.current_project.source_lang if self.current_project else None
            target_lang = self.current_project.target_lang if self.current_project else None

            # Convert language names to codes for termbase search
            source_lang_code = self._convert_language_to_code(source_lang) if source_lang else None
            target_lang_code = self._convert_language_to_code(target_lang) if target_lang else None

            # Strip HTML/XML/CAT tool tags from source text before word splitting
            # This handles <b>, </b>, <i>, memoQ {1}, [2}, Trados <1>, Déjà Vu {00001}, etc.
            import re
            clean_source_text = re.sub(r'</?(?:b|i|u|bi|sub|sup|li-[ob]|\d+)/?>', '', source_text)  # HTML/XML tags
            clean_source_text = re.sub(r'[\[{]\d+[}\]]', '', clean_source_text)  # memoQ/Phrase numeric tags: {1}, [2}, {3]
            clean_source_text = re.sub(r'\{\d{5}\}', '', clean_source_text)  # Déjà Vu tags: {00001}
            # memoQ content tags: [uicontrol id="..."}  or  {uicontrol]  or  [tagname ...}  or  {tagname]
            clean_source_text = re.sub(r'\[[^\[\]]*\}', '', clean_source_text)  # Opening: [anything}
            clean_source_text = re.sub(r'\{[^\{\}]*\]', '', clean_source_text)  # Closing: {anything]

            # Search termbases for all terms that appear in the source text
            # Split source text into words and search for each one
            words = clean_source_text.split()
            matches = {}
            # Use the ORIGINAL source text (with tags) for position matching later
            source_text_lower = source_text.lower()
            # Also create a tag-stripped lowercase version for pattern matching
            clean_source_lower = clean_source_text.lower()
            # Only log word count, not individual words (too verbose)
            
            # Comprehensive set of quote and punctuation characters to strip
            # Includes: straight quotes, curly quotes (left/right), German quotes, guillemets, single quotes
            # ALSO includes parentheses, brackets, and braces for terms like "(typisch)" or "[example]"
            # Using explicit characters to avoid encoding issues
            PUNCT_CHARS = '.,!?;:\"\'\u201C\u201D\u201E\u00AB\u00BB\u2018\u2019\u201A\u2039\u203A()[]{}' 
            
            for word in words:
                # Remove punctuation including quotes (preserve internal punctuation like "gew.%")
                # First strip trailing punctuation
                clean_word = word.rstrip(PUNCT_CHARS)
                # Then strip leading punctuation
                clean_word = clean_word.lstrip(PUNCT_CHARS)
                
                if len(clean_word) < 2:  # Skip short words
                    continue
                
                # Get project ID for termbase priority lookup
                project_id = self.current_project.id if self.current_project and hasattr(self.current_project, 'id') else None
                
                # CRITICAL FIX: Search for BOTH the cleaned word AND the original word with punctuation
                # This allows glossary entries like "ca." to match source text "ca." where
                # the tokenized/cleaned version is "ca" but the glossary entry has the period
                words_to_search = [clean_word]
                # Also try searching with trailing punctuation (for entries like "ca.", "gew.%")
                original_word_stripped_leading = word.lstrip(PUNCT_CHARS)
                if original_word_stripped_leading != clean_word and len(original_word_stripped_leading) >= 2:
                    words_to_search.append(original_word_stripped_leading)
                
                termbase_results = []
                for search_word in words_to_search:
                    results = self.db_manager.search_termbases(
                        search_word,
                        source_lang=source_lang_code,
                        target_lang=target_lang_code,
                        project_id=project_id,
                        min_length=2
                    )
                    if results:
                        termbase_results.extend(results)
                
                if termbase_results:
                    for result in termbase_results:
                        source_term = result.get('source_term', '').strip()
                        target_term = result.get('target_term', '').strip()
                        ranking = result.get('ranking', None)
                        forbidden = result.get('forbidden', False)
                        is_project_termbase = result.get('is_project_termbase', False)
                        term_id = result.get('id')
                        termbase_id = result.get('termbase_id')
                        termbase_name = result.get('termbase_name', 'Unknown')
                        domain = result.get('domain', '')
                        notes = result.get('notes', '')
                        project = result.get('project', '')
                        client = result.get('client', '')
                        if not source_term or not target_term:
                            continue
                        # FILTER: Only keep if full source term appears in source text (case-insensitive)
                        # Use lookaround word boundaries to handle terms with punctuation like "gew.%"
                        import re
                        # Normalize source text: replace ALL quote variants with spaces
                        # This ensures word boundaries work correctly for terms in quotes
                        # Using Unicode escapes to avoid encoding issues
                        normalized_source = clean_source_lower
                        for quote_char in '\"\'\u201C\u201D\u201E\u00AB\u00BB\u2018\u2019\u201A\u2039\u203A':
                            normalized_source = normalized_source.replace(quote_char, ' ')
                        
                        # CRITICAL FIX: Strip trailing punctuation from glossary term before matching
                        # This allows entries like "De huidige uitvinding...problemen." (with period)
                        # to match source text "...problemen." where tokenization strips the period
                        # Strip from both ends to handle quotes and trailing punctuation
                        normalized_term = source_term.lower().rstrip(PUNCT_CHARS).lstrip(PUNCT_CHARS)
                        
                        # Check if term has punctuation - use different pattern
                        if any(char in normalized_term for char in ['.', '%', ',', '-', '/']):
                            pattern = re.compile(r'(?<!\w)' + re.escape(normalized_term) + r'(?!\w)')
                        else:
                            pattern = re.compile(r"\b" + re.escape(normalized_term) + r"\b")
                        
                        # Try matching on normalized (tag-stripped, quote-stripped) text first, 
                        # then tag-stripped, then original with tags
                        if not pattern.search(normalized_source) and not pattern.search(clean_source_lower) and not pattern.search(source_text_lower):
                            # Skip terms whose full phrase isn't in the segment
                            continue
                        # CRITICAL FIX: Use term_id as dict key instead of source_term
                        # This allows multiple translations for the same source term to coexist
                        # Example: "inrichting → device" AND "inrichting → apparatus" both displayed
                        # Old code: matches[source_term] = {...} - only kept one translation
                        # New code: matches[term_id] = {...} - keeps all translations
                        matches[term_id] = {
                            'source': source_term,
                            'translation': target_term,
                            'ranking': ranking,
                            'forbidden': forbidden,
                            'is_project_termbase': is_project_termbase,
                            'term_id': term_id,
                            'termbase_id': termbase_id,
                            'termbase_name': termbase_name,
                            'domain': domain,
                            'notes': notes,
                            'project': project,
                            'client': client,
                            'target_synonyms': result.get('target_synonyms', [])  # Include synonyms
                        }
            
            return self._deduplicate_termbase_matches(matches)
            
        except Exception as e:
            self.log(f"❌ Error finding termbase matches: {e}")
            import traceback
            self.log(f"Traceback: {traceback.format_exc()}")
            return {}

    def _deduplicate_termbase_matches(self, matches: dict) -> dict:
        """Deduplicate termbase matches: if the same source→target pair exists in
        multiple glossaries, keep only the one from the highest-ranked glossary
        (1=project > 0=background)."""
        filtered = {}
        seen_pairs = {}  # (source_lower, target_lower) -> (term_id, ranking)

        for term_id, match_info in matches.items():
            source_lower = match_info['source'].lower()
            target_lower = match_info['translation'].lower()
            pair_key = (source_lower, target_lower)
            ranking = match_info.get('ranking', 0)

            if pair_key in seen_pairs:
                existing_term_id, existing_ranking = seen_pairs[pair_key]
                if ranking > existing_ranking:
                    del filtered[existing_term_id]
                    filtered[term_id] = match_info
                    seen_pairs[pair_key] = (term_id, ranking)
            else:
                filtered[term_id] = match_info
                seen_pairs[pair_key] = (term_id, ranking)

        return filtered

    def highlight_source_with_termbase(self, row: int, source_text: str, termbase_matches: Optional[Dict] = None):
        """
        Highlight termbase matches in the source column using text formatting.
        Does NOT replace the widget - just adds background colors to existing ReadOnlyGridTextEditor.
        
        Args:
            row: Row number in table
            source_text: Source text to highlight
            termbase_matches: Optional pre-computed matches dict (to avoid duplicate searches)
        """
        if not self.table or row < 0:
            return
        
        try:
            # Use provided matches or find them
            if termbase_matches is None:
                termbase_matches = self.find_termbase_matches_in_source(source_text)
            
            # Get the existing source widget (ReadOnlyGridTextEditor)
            source_widget = self.table.cellWidget(row, 2)
            if not source_widget or not hasattr(source_widget, 'highlight_termbase_matches'):
                return
            
            # Apply termbase highlighting first
            if termbase_matches:
                source_widget.highlight_termbase_matches(termbase_matches)
            
            # Also highlight non-translatables (pastel yellow). NTs now
            # come from termbase entries flagged is_nontranslatable=1, so
            # the search is always available – no nt_manager guard needed.
            nt_matches = self.find_nt_matches_in_source(source_text)
            if nt_matches and hasattr(source_widget, 'highlight_non_translatables'):
                source_widget.highlight_non_translatables(nt_matches)
            
        except Exception as e:
            self.log(f"Error highlighting termbase matches: {e}")
            import traceback
            self.log(f"Traceback: {traceback.format_exc()}")

    def insert_term_translation(self, row: int, translation: str):
        """
        Double-click handler: insert termbase translation into target cell
        """
        if not self.table or row < 0 or row >= len(self.current_project.segments):
            return
        
        try:
            # Get the target cell
            target_item = self.table.item(row, 3)
            if not target_item:
                return
            
            # Insert translation (using cell widget)
            segment = self.current_project.segments[row]
            old_target = segment.target
            old_status = segment.status
            segment.target = translation
            target_widget = self.table.cellWidget(row, 3)
            if target_widget and isinstance(target_widget, EditableGridTextEditor):
                target_widget.setPlainText(translation)

            # Update status
            new_status = old_status
            if segment.status in (DEFAULT_STATUS.key, 'pretranslated', 'rejected'):
                segment.status = 'draft'
                new_status = 'draft'
                self.update_status_icon(row, 'draft')
            else:
                self._refresh_segment_status(segment)

            # v1.10.204: record undo for the insertion so Ctrl+Z restores the
            # previous translation. update_status_icon records its own undo
            # entry for the status change; we still need this one for the
            # target text itself.
            if old_target != translation:
                try:
                    self.record_undo_state(segment.id, old_target, translation, old_status, new_status)
                except Exception as undo_err:
                    self.log(f"⚠ Undo recording failed for insert_term_translation: {undo_err}")
            
            self.project_modified = True
            self.update_window_title()
            self.log(f"✓ Term translation inserted in segment {row + 1}")
            
        except Exception as e:
            self.log(f"Error inserting term translation: {e}")

    def _handle_termlens_shortcut(self, key_num: int):
        """Handle TermLens shortcut with double-tap detection for terms 11-20
        
        TermLens numbering starts at 1 (Alt+1..Alt+9). Alt+0 is reserved for the Compare Panel.
        
        Single tap (Alt+N): Insert term N (1-9)
        Double tap (Alt+N,N within 300ms): Undo first insert, then insert term N+10 (11, 22, ..., 99)
        """
        import time

        # Alt+0 is reserved for the Compare Panel.
        if key_num == 0:
            return

        current_time = time.time()
        double_tap_threshold = 0.3  # 300ms window for double-tap
        
        # Check if this is a double-tap (same key within threshold)
        if (hasattr(self, '_termlens_last_key') and 
            self._termlens_last_key == key_num and 
            hasattr(self, '_termlens_last_time') and
            (current_time - self._termlens_last_time) < double_tap_threshold):
            # Double-tap detected! 
            # First, undo the single-tap insertion
            current_widget = self._get_current_target_widget()
            if current_widget and hasattr(current_widget, 'undo'):
                current_widget.undo()
            
            # Now insert term 10-19 (displayed as 00, 11, ..., 99)
            term_index = key_num + 10
            self._termlens_last_key = None  # Reset to prevent triple-tap
            self._termlens_last_time = 0
            self.insert_termlens_term_by_number(term_index)
        else:
            # Single tap - insert term 1-9
            self._termlens_last_key = key_num
            self._termlens_last_time = current_time
            self.insert_termlens_term_by_number(key_num)

    def insert_termlens_term_by_number(self, term_number: int):
        """Insert term from the visible TermLens by number (0-19).

        There are two TermLens instances (under-grid and Match Panel). Since
        v1.10.283 each one defers its render – and its shortcut_terms map – while
        hidden, so we must target whichever is actually on screen. Previously this
        always called the under-grid instance, so Alt+N did nothing when only the
        Match Panel TermLens was visible.
        """
        candidates = []
        for attr in ('termlens_widget', 'termlens_widget_match'):
            w = getattr(self, attr, None)
            if w is not None and hasattr(w, 'insert_term_by_number'):
                candidates.append(w)

        target = None
        # Prefer a visible instance (its shortcut_terms reflect the current segment)
        for w in candidates:
            try:
                if w.isVisible():
                    target = w
                    break
            except Exception:
                pass
        # Fall back to any instance that has populated shortcut mappings
        if target is None:
            for w in candidates:
                if getattr(w, 'shortcut_terms', None):
                    target = w
                    break
        if target is None and candidates:
            target = candidates[0]

        if target is not None:
            try:
                target.insert_term_by_number(term_number)
            except Exception as e:
                self.log(f"Error inserting TermLens term #{term_number}: {e}")

    def insert_termlens_text(self, text: str):
        """Insert text from TermLens into the currently active target field"""
        try:
            # Find the active target editor
            current_row = self.table.currentRow()
            if current_row >= 0:
                target_widget = self.table.cellWidget(current_row, 3)
                if target_widget and hasattr(target_widget, 'textCursor'):
                    # Insert at current cursor position
                    cursor = target_widget.textCursor()
                    cursor.insertText(text)
                    target_widget.setFocus()
                    self.log(f"✓ Inserted term: {text}")
                    return
            
            # Fallback: try tab editor panels
            if hasattr(self, 'tabbed_panels'):
                for panel in self.tabbed_panels:
                    if hasattr(panel, 'editor_widget'):
                        editor = panel.editor_widget
                        if editor.target_editor.hasFocus() or True:  # Insert into first available
                            cursor = editor.target_editor.textCursor()
                            cursor.insertText(text)
                            editor.target_editor.setFocus()
                            self.log(f"✓ Inserted term: {text}")
                            return
            
            self.log("⚠️ No active target field found for insertion")
        except Exception as e:
            self.log(f"✗ Error inserting termlens text: {e}")

    def _on_termlens_font_size_changed(self, new_size: int):
        """Persist new TermLens font size and sync the other TermLens panel.

        Triggered by the inline A-/A+ buttons on either TermLens widget.
        """
        try:
            settings = self.load_general_settings()
            if settings.get('termlens_font_size') == new_size:
                # The widget that fired the signal is already at new_size; sync the other.
                pass
            else:
                settings['termlens_font_size'] = new_size
                self.save_general_settings(settings)

            family = settings.get('termlens_font_family', 'Segoe UI')
            bold = settings.get('termlens_font_bold', False)

            # Sync both TermLens widgets without re-emitting the signal.
            sender = self.sender()
            for widget in (getattr(self, 'termlens_widget', None),
                           getattr(self, 'termlens_widget_match', None)):
                if widget is None or widget is sender:
                    continue
                if widget.current_font_size == new_size:
                    continue
                widget.current_font_size = new_size
                if getattr(widget, 'current_source', '') and hasattr(widget, '_last_termbase_matches'):
                    widget.update_with_matches(
                        widget.current_source,
                        widget._last_termbase_matches or [],
                        getattr(widget, '_last_nt_matches', None),
                        getattr(widget, '_status_hint', None),
                    )
                else:
                    widget.set_font_settings(family, new_size, bold)
        except Exception as e:
            self.log(f"⚠ Could not save TermLens font size: {e}")

    def _on_termlens_edit_entry(self, term_id: int, termbase_id: int):
        """Handle edit glossary entry request from TermLens"""
        try:
            # v1.10.67 diagnostic: record what TermLens actually
            # emitted so an open user report (dialog opens with empty
            # fields or with project-direction captions when the
            # termbase declares the reverse direction) can be checked
            # against the dialog's LOAD line. If termbase_id here
            # disagrees with the real termbases.id for this term, the
            # bug is in the TermLens display pipeline upstream — the
            # dialog itself self-corrects via the term_id JOIN in
            # TermbaseEntryEditor.setup_ui (v1.10.67).
            self.log(
                f"[edit-from-termlens] received term_id={term_id!r} "
                f"termbase_id={termbase_id!r} (types: "
                f"{type(term_id).__name__}, {type(termbase_id).__name__})"
            )

            from modules.termbase_entry_editor import TermbaseEntryEditor

            dialog = TermbaseEntryEditor(
                parent=self,
                db_manager=self.db_manager,
                termbase_id=termbase_id,
                term_id=term_id
            )
            
            if dialog.exec():
                # Entry was edited (or deleted via the dialog's red
                # Delete button — both paths call self.accept()).
                # v1.10.64: use the full post-delete refresh helper
                # so the in-memory termbase_index is rebuilt too.
                # That's necessary for the delete-via-dialog path; it's
                # harmless overhead for the edit-via-dialog path (index
                # rebuild is sub-second on typical term counts).
                self.log(f"✓ Termbase entry {term_id} updated/deleted via dialog")
                self._post_termbase_delete_refresh()
        except Exception as e:
            self.log(f"✗ Error editing termbase entry: {e}")

    def _on_termlens_refresh_requested(self):
        """Handle the TermLens 🔄 refresh button (v1.10.68).

        v1.10.72: collapsed into the same handler as F5
        (``force_refresh_matches``). The button is now just a
        discoverable visual entry point for the F5 shortcut — useful
        for users who don't know about F5 but find the button by
        hovering. Behaviour is identical to pressing F5:

         - Snapshot-check whether the termbase tables actually
           changed since the last index rebuild; if yes, rebuild
           the in-memory index from disk (catches cross-process
           edits from the Trados plugin or external tools); if no,
           skip the rebuild (keeps the common case fast).
         - Clear per-segment caches.
         - Re-search termbases / TMs / NT lists.
         - Redraw TermLens widgets + the Translation Results panel.

        Before v1.10.72 this called a separate
        ``_post_termbase_delete_refresh`` that always rebuilt the
        index. Having two near-identical refresh paths confused
        users and caused the v1.10.71 "F5 fixes what the 🔄 button
        leaves under-refreshed" symptom; the merge fixes both.
        """
        try:
            self.force_refresh_matches()
        except Exception as e:
            self.log(f"✗ TermLens refresh failed: {e}")

    def _on_termlens_delete_entry(self, term_id: int, termbase_id: int, source_term: str, target_term: str):
        """Handle delete glossary entry request from TermLens"""
        from PyQt6.QtWidgets import QMessageBox
        
        try:
            # Confirm deletion
            reply = QMessageBox.question(
                self,
                "Confirm Deletion",
                f"Delete termbase entry?\n\nSource: {source_term}\nTarget: {target_term}\n\nThis action cannot be undone.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No
            )
            
            if reply == QMessageBox.StandardButton.Yes:
                if self.termbase_mgr:
                    try:
                        if self.termbase_mgr.delete_term(term_id):
                            self.log(f"✓ Deleted termbase entry: {source_term} → {target_term}")

                            # v1.10.64: previously this only called
                            # _refresh_current_segment_matches, which
                            # clears the per-segment cache and re-runs
                            # find_termbase_matches_in_source. But that
                            # search reads from the in-memory
                            # termbase_index, which still contained the
                            # just-deleted entry → the term kept showing
                            # in TermLens after delete. The post-delete
                            # helper additionally rebuilds the index.
                            self._post_termbase_delete_refresh()
                        else:
                            self.log(f"✗ Failed to delete termbase entry")
                    except Exception as e:
                        self.log(f"✗ Error deleting termbase entry from database: {e}")
        except Exception as e:
            self.log(f"✗ Error deleting termbase entry: {e}")

    def get_ai_inject_glossary_terms(self) -> list:
        """Get glossary terms from AI-inject-enabled termbases for the current project.

        Returns:
            List of term dictionaries with source_term, target_term, forbidden keys
        """
        if not hasattr(self, 'termbase_mgr') or not self.termbase_mgr:
            return []

        project_id = None
        if hasattr(self, 'current_project') and self.current_project:
            project_id = getattr(self.current_project, 'id', None)

        return self.termbase_mgr.get_ai_inject_terms(project_id)
