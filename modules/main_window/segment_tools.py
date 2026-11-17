"""Tools that work on the current segment or a selection: QuickLauncher,
the Supervertaler Assistant, QuickTrans direction, FuzzyFixer, AutoTagger,
QA checks and LanguageTool.

Part of the main window (SupervertalerQt), moved here unchanged. The
methods run as the main window's own: `self` is the SupervertalerQt.
"""

import traceback
from PyQt6.QtGui import QCursor, QKeySequence
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QMenu,
    QMessageBox,
    QPushButton,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
)
from modules.grid_editors import EditableGridTextEditor
from modules.help_system import Topics as HelpTopics, set_topic as set_help_topic
from modules.main_dialogs import LiveProgressDialog
from modules.segment_model import Segment
from modules.tag_text import (
    autotag_extract_tags,
    compact_tags,
    extract_all_tags,
    place_tags_via_llm,
    strip_all_tags,
    strip_outer_wrapping_tags,
)
from modules.translation_workers import PreTranslationWorker
from pathlib import Path
from typing import List, Tuple


class SegmentToolsMixin:
    def open_quicklauncher(self):
        """Ctrl+Q / Alt+K: pop up the QuickLauncher prompt menu for the
        current segment, the same menu available from the grid right-click.

        Each configured QuickLauncher prompt is listed as a submenu offering
        Run (show response), Run and replace selection, and Copy to clipboard.
        The menu acts on the focused source/target editor, falling back to the
        current row's target editor.
        """
        from PyQt6.QtWidgets import QMenu, QMessageBox
        from PyQt6.QtGui import QCursor

        try:
            # Decide which editor the prompt should act on
            origin = QApplication.focusWidget()
            if not isinstance(origin, QTextEdit):
                origin = None
            if origin is None and hasattr(self, 'table') and self.table:
                row = self.table.currentRow()
                if row is not None and row >= 0:
                    cell = self.table.cellWidget(row, 3)  # target column
                    if isinstance(cell, QTextEdit):
                        origin = cell
            if not isinstance(origin, QTextEdit):
                QMessageBox.information(self, "QuickLauncher", "Select a segment in the grid first.")
                return

            # Gather configured QuickLauncher prompts
            quicklauncher_items = []
            if hasattr(self, 'prompt_manager_qt') and self.prompt_manager_qt:
                lib = getattr(self.prompt_manager_qt, 'library', None)
                if lib and hasattr(lib, 'get_quicklauncher_grid_prompts'):
                    quicklauncher_items = lib.get_quicklauncher_grid_prompts() or []

            menu = QMenu(self)
            if not quicklauncher_items:
                act = menu.addAction("No QuickLauncher prompts configured")
                act.setEnabled(False)
            else:
                for rel_path, label in sorted(quicklauncher_items, key=lambda x: (x[1] or x[0]).lower()):
                    prompt_menu = menu.addMenu(label or rel_path)
                    run_show = prompt_menu.addAction("▶ Run (show response)…")
                    run_show.triggered.connect(
                        lambda checked=False, p=rel_path: self.run_grid_quicklauncher_prompt(p, origin_widget=origin, behavior="show")
                    )
                    run_replace = prompt_menu.addAction("↺ Run and replace target selection")
                    run_replace.triggered.connect(
                        lambda checked=False, p=rel_path: self.run_grid_quicklauncher_prompt(p, origin_widget=origin, behavior="replace")
                    )
                    copy_clip = prompt_menu.addAction("📋 Copy to clipboard")
                    copy_clip.triggered.connect(
                        lambda checked=False, p=rel_path: self.run_grid_quicklauncher_prompt(p, origin_widget=origin, behavior="clipboard")
                    )

            menu.exec(QCursor.pos())

        except Exception as e:
            self.log(f"❌ Error opening QuickLauncher: {e}")

    def show_supervertaler_assistant(self, initial_text=None, external_mode=False):
        """Navigate to the Supervertaler Sidekick (AI Assistant tab) and optionally insert text.

        Instead of a separate floating window, this reuses the existing AI Assistant
        inside the AI tab. For external mode, it also brings Supervertaler
        to the foreground first.
        """
        try:
            # If called from external app, bring Supervertaler to front
            if external_mode:
                self.show()
                self.raise_()
                self.activateWindow()

            # Switch to Grid tab (index 0) so the right panel is visible
            if hasattr(self, 'main_tabs'):
                self.main_tabs.setCurrentIndex(0)

            # Switch the right panel to the AI Assistant tab
            if hasattr(self, 'right_tabs') and hasattr(self, '_assistant_tab_index'):
                self.right_tabs.setCurrentIndex(self._assistant_tab_index)

            # Insert text into the assistant chat input
            if hasattr(self, 'prompt_manager_qt') and self.prompt_manager_qt:
                self.prompt_manager_qt.receive_text_for_assistant(
                    initial_text or "", from_external=external_mode
                )

            print(f"[Assistant] Navigated to Supervertaler Sidekick (external={external_mode})")

        except Exception as e:
            print(f"[Assistant] Error navigating to assistant: {e}")
            import traceback
            traceback.print_exc()

    def _quicktrans_base_pair(self):
        """The project's (source, target) language pair for QuickTrans, before
        any auto-detect or manual override. Prefers the open project's pair,
        falling back to the app defaults."""
        proj = getattr(self, 'current_project', None)
        if proj and getattr(proj, 'source_lang', None) and getattr(proj, 'target_lang', None):
            return (proj.source_lang, proj.target_lang)
        return (getattr(self, 'source_language', 'English'),
                getattr(self, 'target_language', 'Dutch'))

    def set_quicktrans_direction_override(self, src, tgt):
        """Record a manual QuickTrans direction chosen in the popup's language
        selector. It's tagged with the base pair it was set against, so it stays
        sticky for later popups but is automatically dropped once the project /
        default pair is changed elsewhere (Settings → Language Pair, project
        switch, etc.)."""
        self._quicktrans_lang_override = {
            'src': src, 'tgt': tgt, 'base': self._quicktrans_base_pair(),
        }

    def resolve_quicktrans_direction(self, text):
        """Resolve the (source, target) direction for a QuickTrans request.

        Priority: a sticky manual override (if still valid for the current base
        pair) → otherwise per-text auto-detection → otherwise the base pair.
        """
        base = self._quicktrans_base_pair()
        override = getattr(self, '_quicktrans_lang_override', None)
        if override and override.get('base') == base:
            return override['src'], override['tgt']
        # Stale (pair changed elsewhere) or none → forget it and auto-detect.
        if override:
            self._quicktrans_lang_override = None
        try:
            from modules.lang_detect import resolve_direction
            return resolve_direction(text, base[0], base[1])
        except Exception as e:
            try:
                self.log(f"QuickTrans language auto-detect skipped: {e}")
            except Exception:
                pass
            return base

    def refresh_shortcut_enabled_states(self):
        """Refresh enabled/disabled states and key bindings of all global shortcuts from shortcut manager.
        
        When disabled: clears the key sequence to fully release the key combination.
        When enabled: restores the key sequence from the manager (custom or default).
        """
        if not hasattr(self, 'global_shortcuts'):
            return
        for shortcut_id, shortcut in self.global_shortcuts.items():
            is_enabled = self.shortcut_manager.is_enabled(shortcut_id)
            if is_enabled:
                # Get the current key sequence from manager (may have been customized)
                key_sequence = self.shortcut_manager.get_shortcut(shortcut_id)
                if key_sequence:
                    self.global_shortcut_keys[shortcut_id] = key_sequence
                    shortcut.setKey(QKeySequence(key_sequence))
            else:
                # Clear the key to release the combination for other uses
                shortcut.setKey(QKeySequence())

    def show_qa_checks_dialog(self):
        """QA → Run QA Checks (issue #209). Non-modal, so findings can be fixed
        in the grid while the list stays open."""
        from modules.qa_checks_dialog import QAChecksDialog
        dlg = getattr(self, '_qa_checks_dialog', None)
        if dlg is None:
            def _reload_fr_sets():
                mgr = getattr(self, 'fr_sets_manager', None)
                if mgr is not None:
                    try:
                        mgr.reload_sets()
                    except RuntimeError:
                        pass  # its dialog has been closed and deleted
            dlg = QAChecksDialog(
                self, Path(self.user_data_path) / "find_replace_sets",
                get_segments=lambda: self.current_project.segments if self.current_project else [],
                navigate=self._navigate_to_segment_by_id,
                on_sets_changed=_reload_fr_sets,
                extract_tags=extract_all_tags)
            set_help_topic(dlg, HelpTopics.QA_CHECKS)  # F1 opens its help page
            self._qa_checks_dialog = dlg
        else:
            dlg.refresh_sets()
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

    def show_languagetool_dialog(self):
        """QA → Check with LanguageTool (issue #233). Non-modal."""
        from modules.languagetool_dialog import LanguageToolDialog
        dlg = getattr(self, '_languagetool_dialog', None)
        if dlg is None:
            def _proxies():
                url = self._get_proxy_url() if hasattr(self, '_get_proxy_url') else None
                return {'http': url, 'https': url} if url else None
            dlg = LanguageToolDialog(
                self,
                get_segments=lambda: self.current_project.segments if self.current_project else [],
                get_target_language=lambda: getattr(self.current_project, 'target_lang', '') if self.current_project else '',
                navigate=self._navigate_to_segment_by_id,
                apply_target=self._set_target_from_tool,
                load_settings=self.load_general_settings,
                save_settings=self.save_general_settings,
                get_proxies=_proxies)
            set_help_topic(dlg, HelpTopics.QA_LANGUAGETOOL)  # F1 opens its help page
            self._languagetool_dialog = dlg
        else:
            from modules.languagetool_client import lt_language
            if self.current_project:
                dlg.language_edit.setText(lt_language(getattr(self.current_project, 'target_lang', '')))
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

    def _set_target_from_tool(self, segment_index, new_text):
        """Replace a segment's target on behalf of a checking tool (LanguageTool
        suggestion): undoable with Ctrl+Z, grid cell updated in place."""
        if not self.current_project or not 0 <= segment_index < len(self.current_project.segments):
            return
        segment = self.current_project.segments[segment_index]
        old_target, old_status = segment.target, segment.status
        if new_text == old_target:
            return
        segment.target = new_text
        self.record_undo_state(segment.id, old_target, new_text, old_status, old_status)
        widget = self.table.cellWidget(segment_index, 3) if hasattr(self, 'table') else None
        if widget is not None and hasattr(widget, 'setPlainText'):
            display_text = new_text
            if getattr(self, 'hide_outer_wrapping_tags', False):
                display_text, _ = strip_outer_wrapping_tags(display_text)
            widget.blockSignals(True)
            widget.setPlainText(display_text)
            widget.blockSignals(False)
        self.project_modified = True
        self.update_window_title()

    def fuzzy_fix_current_segment(self, *args):
        """FuzzyFixer: adapt the currently selected fuzzy TM match to the
        current source using the AI (Match Panel button / Ctrl+Alt+F).

        Reads the match shown in the Match Panel TM Source/Target boxes, checks
        it falls within the configured % range, then delegates to
        translate_current_segment() with the match injected into the prompt.
        """
        if not self.current_project:
            QMessageBox.warning(self, "No Project", "Open a project first.")
            return

        matches = getattr(self, 'match_panel_tm_matches', None)
        if not matches:
            QMessageBox.information(
                self, "FuzzyFixer",
                "No TM match is shown for this segment, so there is nothing to fix.\n\n"
                "Select a segment that has a fuzzy TM match in the Match Panel.")
            return

        idx = getattr(self, 'match_panel_tm_index', 0)
        if idx < 0 or idx >= len(matches):
            idx = 0
        match = matches[idx]

        # Support both dict and TranslationMatch objects (mirrors _update_match_panel_tm_display)
        if isinstance(match, dict):
            tm_source = match.get('source', '')
            tm_target = match.get('target', '')
            tm_name = match.get('tm_name', '') or 'TM'
            match_pct = match.get('match_pct', 0)
        else:
            tm_source = getattr(match, 'compare_source', '') or getattr(match, 'source', '')
            tm_target = getattr(match, 'target', '')
            md = match.metadata if hasattr(match, 'metadata') and match.metadata else {}
            tm_name = md.get('tm_name', '') or 'TM'
            match_pct = getattr(match, 'relevance', 0)

        try:
            match_pct_int = int(round(float(match_pct or 0)))
        except (TypeError, ValueError):
            match_pct_int = 0

        if not (tm_target or '').strip():
            QMessageBox.information(self, "FuzzyFixer",
                                    "The selected TM match has no target text to adapt.")
            return

        min_pct = int(getattr(self, 'fuzzy_fixer_min_pct', 75))
        max_pct = int(getattr(self, 'fuzzy_fixer_max_pct', 99))
        if match_pct_int > max_pct:
            QMessageBox.information(
                self, "FuzzyFixer",
                f"This is a {match_pct_int}% match. FuzzyFixer only adapts matches "
                f"between {min_pct}% and {max_pct}% – exact matches need no fixing.")
            return
        if match_pct_int < min_pct:
            QMessageBox.information(
                self, "FuzzyFixer",
                f"This is only a {match_pct_int}% match, below the FuzzyFixer minimum "
                f"of {min_pct}%. Adjust the range in Settings → AI Settings if needed.")
            return

        fuzzy_match = {
            'source': tm_source,
            'target': tm_target,
            'match_pct': match_pct_int,
            'tm_name': tm_name,
        }
        self.translate_current_segment(fuzzy_match=fuzzy_match)

    def _show_fuzzy_fix_diff(self, tm_target: str, new_target: str):
        """Render a track-changes view (AI output vs original TM target) in the
        Match Panel TM Target box, so the user sees what the FuzzyFixer changed.

        Transient: it is replaced the next time the TM display refreshes.
        """
        if not tm_target or not new_target:
            return
        if not (hasattr(self, 'match_panel_tm_target') and self.match_panel_tm_target):
            return
        try:
            # Reuse the existing track-changes renderer: 'current' is the new
            # target, 'tm_source' is the original target being annotated.
            self._set_compare_panel_text_with_diff(self.match_panel_tm_target, new_target, tm_target)
        except Exception:
            self.match_panel_tm_target.setPlainText(new_target)

    def _update_fuzzy_fixer_btn_state(self, match_pct):
        """Enable the Match Panel FuzzyFixer button only when the current match
        percentage is within the configured range (None = no match)."""
        btn = getattr(self, 'match_panel_fuzzy_fixer_btn', None)
        if btn is None:
            return
        enabled = False
        if match_pct is not None:
            try:
                pct = int(round(float(match_pct or 0)))
                min_pct = int(getattr(self, 'fuzzy_fixer_min_pct', 75))
                max_pct = int(getattr(self, 'fuzzy_fixer_max_pct', 99))
                enabled = min_pct <= pct <= max_pct
            except (TypeError, ValueError):
                enabled = False
        btn.setEnabled(enabled)

    def autotag_current_segment(self, *args):
        """AutoTagger: place the source segment's inline tags into the current
        target translation using the AI (toolbar button / menu / Ctrl+Alt+G).

        The translation wording is never changed — only tags are inserted. The
        result is validated (same tags as source, words unchanged, well-formed)
        before it is written; on failure the target is left untouched.
        """
        current_row = self.table.currentRow()
        if current_row < 0 or not self.current_project:
            QMessageBox.warning(self, "No Selection", "Please select a segment first.")
            return
        segment = self.current_project.segments[current_row]

        if not autotag_extract_tags(segment.source):
            QMessageBox.information(self, "AutoTagger",
                                    "The source segment has no inline tags, so there's nothing to place.")
            return
        if not (segment.target or "").strip():
            QMessageBox.information(self, "AutoTagger",
                                    "Translate this segment first — AutoTagger places tags into an existing translation.")
            return

        clean_target = strip_all_tags(segment.target)

        # Resolve provider/model/keys (same approach as translate_current_segment)
        settings = self.load_llm_settings()
        provider = settings.get('provider', 'openai')
        model = self._resolve_provider_model(settings, provider, 'gpt-6.1-sol')
        if provider == 'ollama':
            api_keys = {'ollama': 'not-needed'}
        else:
            api_keys = self.load_api_keys() or {}
            has_key = (provider in api_keys
                       or (provider == 'gemini' and 'google' in api_keys)
                       or provider == 'custom_openai')
            if not has_key:
                reply = QMessageBox.question(
                    self, f"{provider.title()} API Key Missing",
                    f"{self._api_key_problem(provider) or f'No API key set for {provider.title()}.'} Configure it now?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
                if reply == QMessageBox.StandardButton.Yes:
                    self._go_to_settings_tab("AI Settings")
                return

        try:
            client = self.create_llm_client(provider, model, api_keys, settings)
        except Exception as e:
            QMessageBox.critical(self, "AutoTagger", f"Could not initialise the AI client:\n\n{e}")
            return

        src_lang = self.current_project.source_lang
        tgt_lang = self.current_project.target_lang
        self.status_bar.showMessage(f"🏷️ AutoTagger placing tags in segment #{segment.id}...")
        QApplication.processEvents()
        try:
            new_target, reason = place_tags_via_llm(
                client, self.prompt_manager_qt, segment.source, clean_target, src_lang, tgt_lang)
        except Exception as e:
            self.log(f"✗ AutoTagger error: {e}")
            QMessageBox.critical(self, "AutoTagger", f"AutoTagger failed:\n\n{e}")
            self.status_bar.showMessage("AutoTagger failed", 3000)
            return

        if not new_target:
            self.log(f"⚠ AutoTagger could not tag #{segment.id}: {reason}")
            QMessageBox.warning(self, "AutoTagger",
                                "Couldn't place the tags reliably, so the target was left unchanged.\n\n"
                                f"Reason: {reason}")
            self.status_bar.showMessage("AutoTagger: target left unchanged", 3000)
            return

        if new_target == segment.target:
            self.status_bar.showMessage("🏷️ AutoTagger: tags already correct", 3000)
            return

        _undo_old_target = segment.target
        segment.target = new_target
        try:
            self.record_undo_state(segment.id, _undo_old_target, new_target, segment.status, segment.status)
        except Exception:
            pass

        # Push the DISPLAY form into the cell, not the raw text: the grid renders
        # target cells through compact_tags + apply_invisible_replacements at load
        # time, so writing raw text here left the new content rendered differently
        # from every other cell — most visibly a tab inside a tag (memoQ's
        # <mq:ch val="\t" />) showing as a bare gap in the target while the source
        # cell showed the → marker, which reads as "the tag lost its content".
        target_widget = self.table.cellWidget(current_row, 3)
        display_target = new_target
        try:
            if target_widget is not None and getattr(target_widget, '_compact_tag_map', None) is not None:
                display_target = compact_tags(display_target, target_widget._compact_tag_map)
            display_target = self.apply_invisible_replacements(display_target)
        except Exception:
            display_target = new_target   # display nicety only; never block the write
        if target_widget and isinstance(target_widget, EditableGridTextEditor):
            target_widget.setPlainText(display_target)
        else:
            self.table.setItem(current_row, 3, QTableWidgetItem(display_target))
        self.update_status_icon(current_row, segment.status)
        self._auto_resize_single_row(current_row)
        self.project_modified = True
        self.update_window_title()
        n = len(autotag_extract_tags(segment.source))
        self.log(f"🏷️ AutoTagger placed {n} tag(s) in #{segment.id}")
        self.status_bar.showMessage(f"🏷️ AutoTagger placed {n} tag(s) in segment #{segment.id}", 3000)

    def _quicklauncher_get_selection_text(self, widget: QTextEdit) -> str:
        """Get selected text (or full text) from a QTextEdit, normalized for LLMs."""
        cursor = widget.textCursor()
        text = cursor.selectedText() if cursor.hasSelection() else widget.toPlainText()
        text = (text or "").replace('\u2029', '\n')

        # Reverse invisible-character display markers if present
        try:
            if hasattr(self, 'reverse_invisible_replacements'):
                text = self.reverse_invisible_replacements(text)
        except Exception:
            pass

        return text.strip()

    def _build_quicklauncher_document_context(self, mode: str = "both") -> str:
        """Build document context for QuickLauncher prompts.
        
        Args:
            mode: One of:
                - "both": Include both source and target text (for proofreading)
                - "source": Include only source text (for translation/terminology questions)
                - "target": Include only target text (for consistency/style analysis)
        
        Returns a formatted string with segments from the project for context.
        Uses the 'quicklauncher_context_percent' setting (default: 50% of total segments).
        """
        if not hasattr(self, 'current_project') or not self.current_project or not self.current_project.segments:
            return "(No project context available)"
        
        try:
            # Get settings
            general_prefs = self.load_general_settings()
            context_percent = general_prefs.get('quicklauncher_context_percent',
                                               general_prefs.get('quickmenu_context_percent', 50))  # Default: 50%
            max_context_segments = general_prefs.get('quicklauncher_context_max',
                                                     general_prefs.get('quickmenu_context_max', 100))  # Safety limit
            
            # Calculate how many segments to include
            total_segments = len(self.current_project.segments)
            num_segments = min(
                int(total_segments * context_percent / 100),
                max_context_segments
            )
            
            if num_segments == 0:
                return "(Document context disabled)"
            
            # Get segments (from start of document)
            context_segments = self.current_project.segments[:num_segments]
            
            # Format segments based on mode
            context_parts = []
            mode_labels = {"both": "SOURCE + TARGET", "source": "SOURCE ONLY", "target": "TARGET ONLY"}
            context_parts.append(f"=== DOCUMENT CONTEXT ({mode_labels.get(mode, 'UNKNOWN')}) ===")
            context_parts.append(f"(Showing {num_segments} of {total_segments} segments - {context_percent}%)")
            context_parts.append("")
            
            for seg in context_segments:
                if mode == "both":
                    # Source + Target
                    context_parts.append(f"[{seg.id}] {seg.source}")
                    if seg.target and seg.target.strip():
                        context_parts.append(f"    → {seg.target}")
                elif mode == "source":
                    # Source only
                    context_parts.append(f"[{seg.id}] {seg.source}")
                elif mode == "target":
                    # Target only (skip empty targets)
                    if seg.target and seg.target.strip():
                        context_parts.append(f"[{seg.id}] {seg.target}")
                context_parts.append("")  # Blank line between segments
            
            return "\n".join(context_parts)
            
        except Exception as e:
            return f"(Error building document context: {e})"

    def _quicklauncher_show_result_dialog(self, title: str, output_text: str, apply_callback=None):
        from PyQt6.QtWidgets import QDialog, QVBoxLayout, QTextEdit, QDialogButtonBox, QPushButton, QApplication

        dlg = QDialog(self)
        dlg.setWindowTitle(title)
        dlg.setMinimumSize(700, 450)
        layout = QVBoxLayout(dlg)

        out = QTextEdit()
        out.setReadOnly(True)
        out.setMarkdown(output_text or "")
        layout.addWidget(out)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        btn_copy = QPushButton(self.tr("Copy"))
        buttons.addButton(btn_copy, QDialogButtonBox.ButtonRole.ActionRole)
        btn_copy.clicked.connect(lambda: QApplication.clipboard().setText(output_text or ""))

        if apply_callback is not None:
            btn_apply = QPushButton(self.tr("Replace"))
            buttons.addButton(btn_apply, QDialogButtonBox.ButtonRole.AcceptRole)
            btn_apply.clicked.connect(lambda: (apply_callback(), dlg.accept()))

        buttons.rejected.connect(dlg.reject)
        layout.addWidget(buttons)
        dlg.exec()

    def _get_selected_segments_with_rows(self) -> List[Tuple[int, Segment]]:
        """Return selected segments across active views as (row_index, segment)."""
        if not self.current_project:
            return []

        selected_ids = set()

        try:
            for seg in self.get_selected_segments_from_grid():
                if hasattr(seg, 'id'):
                    selected_ids.add(seg.id)
        except Exception:
            pass

        try:
            for seg in self.get_selected_segments_from_list():
                if hasattr(seg, 'id'):
                    selected_ids.add(seg.id)
        except Exception:
            pass

        if not selected_ids:
            return []

        segments_with_rows: List[Tuple[int, Segment]] = []
        for idx, seg in enumerate(self.current_project.segments):
            if seg.id in selected_ids:
                segments_with_rows.append((idx, seg))

        return segments_with_rows

    def _get_filtered_segments_with_rows(self) -> List[Tuple[int, Segment]]:
        """Return currently visible/filtered segments as (row_index, segment)."""
        if not self.current_project:
            return []

        # Check if table is currently filtered
        if not hasattr(self, 'table') or self.table.rowCount() == 0:
            return []

        visible_segments: List[Tuple[int, Segment]] = []
        
        # Iterate through visible rows in the table
        for visual_row in range(self.table.rowCount()):
            if not self.table.isRowHidden(visual_row):
                # Get the segment from this row
                # The visual row index corresponds to the segment index in the project
                if visual_row < len(self.current_project.segments):
                    seg = self.current_project.segments[visual_row]
                    visible_segments.append((visual_row, seg))

        return visible_segments

    def autotag_segments_bulk(self):
        """Bulk Operations: (re)place inline tags on already-translated segments
        with the AI. Operates on the selected segments, or all segments if none
        are selected. Only touches segments that have source tags AND a target;
        wording is never changed. One AI call per segment; one Undo reverts all."""
        if not self.current_project:
            QMessageBox.warning(self, "No Project", "Open a project first.")
            return

        segs = self._get_selected_segments_with_rows()
        scope_desc = "selected"
        if not segs:
            segs = [(i, s) for i, s in enumerate(self.current_project.segments)]
            scope_desc = "all"

        targets = [(i, s) for (i, s) in segs
                   if autotag_extract_tags(s.source) and (s.target or "").strip()]
        if not targets:
            QMessageBox.information(
                self, "AutoTagger",
                f"None of the {scope_desc} segments have both inline tags in the "
                "source and a translation to tag.")
            return

        reply = QMessageBox.question(
            self, "AutoTagger",
            f"Run AutoTagger on {len(targets)} segment(s) ({scope_desc} scope)?\n\n"
            "Each segment's source tags are re-placed into its translation by the "
            "AI (one AI call per segment). Wording is never changed; only tags move.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply != QMessageBox.StandardButton.Yes:
            return

        # Resolve LLM provider/model/keys
        settings = self.load_llm_settings()
        provider = settings.get('provider', 'openai')
        model = self._resolve_provider_model(settings, provider, 'gpt-6.1-sol')
        if provider == 'ollama':
            api_keys = {'ollama': 'not-needed'}
        else:
            api_keys = self.load_api_keys() or {}
            has_key = (provider in api_keys
                       or (provider == 'gemini' and 'google' in api_keys)
                       or provider == 'custom_openai')
            if not has_key:
                r = QMessageBox.question(
                    self, f"{provider.title()} API Key Missing",
                    f"{self._api_key_problem(provider) or f'No API key set for {provider.title()}.'} Configure it now?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
                if r == QMessageBox.StandardButton.Yes:
                    self._go_to_settings_tab("AI Settings")
                return

        custom_base_url = None
        custom_api_key = None
        if provider == 'custom_openai':
            profile = self._get_active_custom_profile(settings)
            if profile:
                custom_base_url = profile.get('endpoint') or None
                pk = (profile.get('api_key') or '').strip()
                if pk:
                    custom_api_key = pk

        worker = PreTranslationWorker(
            self, targets, 'LLM', provider, model,
            prompt_manager=self.prompt_manager_qt if hasattr(self, 'prompt_manager_qt') else None,
            base_url=custom_base_url, custom_api_key=custom_api_key,
            http_proxy=self._get_proxy_url() if provider != 'gemini' else None,
            autotag_only=True,
        )
        dialog = LiveProgressDialog(self, len(targets), f"AutoTagger ({provider} | {model})")
        sc = {'s': 0, 'e': 0}

        def on_progress(current, total, message, success, elapsed_time):
            if success:
                sc['s'] += 1
            else:
                sc['e'] += 1
            dialog.add_console_line(message, success)
            dialog.update_progress(current, total, elapsed_time, sc['s'], sc['e'])
            if success and current <= len(targets):
                row_index, segment = targets[current - 1]
                if row_index < self.table.rowCount():
                    display_text = segment.target
                    if self.hide_outer_wrapping_tags:
                        display_text, _ = strip_outer_wrapping_tags(display_text)
                    tw = self.table.cellWidget(row_index, 3)
                    if tw and isinstance(tw, EditableGridTextEditor):
                        tw.setPlainText(display_text)
                    else:
                        self.table.setItem(row_index, 3, QTableWidgetItem(display_text))
                    self.update_status_icon(row_index, segment.status)

        def on_complete(final_success, final_error):
            dialog.show_completion_message(final_success, final_error)
            self.project_modified = True
            self.update_window_title()
            self.auto_resize_rows()
            self.update_progress_stats()
            try:
                entries = getattr(worker, 'undo_entries', None) or []
                if entries:
                    self.record_undo_states_batch(entries)
            except Exception as e:
                self.log(f"⚠ Undo recording failed for AutoTagger batch: {e}")
            self.log(f"🏷️ AutoTagger batch complete: {final_success} tagged, {final_error} skipped/failed")

        worker.progress_update.connect(on_progress)
        worker.translation_complete.connect(on_complete)
        dialog.rejected.connect(worker.cancel)
        worker.start()
        dialog.exec()
        worker.wait()
