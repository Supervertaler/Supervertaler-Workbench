"""Segment notes and comments, and the segment editor tab: adding, editing
and showing comments, and their anchors in the text.

Part of the main window (SupervertalerQt), moved here unchanged. The
methods run as the main window's own: `self` is the SupervertalerQt.
"""

from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QLabel,
    QMenu,
    QPushButton,
    QTableWidget,
    QTextEdit,
    QVBoxLayout,
)
from datetime import datetime
from modules.statuses import DEFAULT_STATUS, get_status


class CommentsMixin:
    def add_comment_from_selection(self):
        """Create a new Comment anchored to the text selected in the source
        or target cell of the focused row (Ctrl+M).

        Phase B of the v1.10.57+ range-anchored comments work.
        (Originally bound to Ctrl+Shift+M; reclaimed Ctrl+M in v1.10.61
        once the misleading "(Ctrl+M)" labels on the QuickTrans menu
        items were corrected — Ctrl+M was never actually bound in-app,
        QuickTrans uses the global Ctrl+Alt+Q hotkey.)

        Behaviour:
         - If the focused widget is a source-cell or target-cell editor
           AND the user has selected text in it, the new Comment is
           anchored to that character range (Python-slice semantics)
           on that field. Example: select "schroef" in the source cell
           → Ctrl+M → enter "translated as 'screw' based on
           context" → Comment is stored with anchor_field='source',
           anchor_start/end matching the selection.
         - If there's no selection (just a cursor position), the
           Comment is segment-level (no anchor). Useful when you want
           to attach a general note to the whole segment without
           clicking into the legacy bottom-of-tab editor.
         - If the focused widget isn't a source/target cell, shows a
           hint in the status bar and bails. The user can still use
           the legacy bottom-of-tab editor for segment-level notes.

        Visible end-user payoff: Phases C (highlight in editor) and E
        (Word comment anchored to the exact characters in DOCX export)
        light up after this Comment is created. In Phase B alone, the
        Comment exists in the data and shows up in the all-comments
        list (as part of the segment's combined comment text — Phase D
        rebuilds the list to show one entry per Comment with proper
        anchor display).
        """
        from PyQt6.QtWidgets import (QDialog as _QDialog, QDialogButtonBox as _QDialogButtonBox,
                                     QPlainTextEdit as _QPlainTextEdit)

        if not self.current_project or not self.current_project.segments:
            return
        if not hasattr(self, 'table') or not self.table:
            return

        # Identify the focused source/target cell editor (if any).
        focused = QApplication.focusWidget()
        if not isinstance(focused, QTextEdit):
            if hasattr(self, 'status_bar'):
                self.status_bar.showMessage(
                    "Click into a source or target cell first, "
                    "then select text and press Ctrl+M to add a comment.",
                    5000,
                )
            return

        # Walk up the parent chain looking for the row, since the focused
        # widget might be a child of the cell editor (e.g. completion popup).
        row = getattr(focused, 'row', None)
        if row is None or row < 0:
            return

        source_widget = self.table.cellWidget(row, 2)
        target_widget = self.table.cellWidget(row, 3)
        anchor_field = ''
        if focused is source_widget:
            anchor_field = 'source'
        elif focused is target_widget:
            anchor_field = 'target'
        else:
            # Not in a recognised source/target cell — bail rather than
            # create an unanchored comment in a surprising context.
            return

        # Capture selection (or empty range = unanchored).
        cursor = focused.textCursor()
        sel_start = cursor.selectionStart()
        sel_end = cursor.selectionEnd()
        has_selection = sel_end > sel_start
        selected_text = (
            focused.toPlainText()[sel_start:sel_end] if has_selection else ''
        )
        if not has_selection:
            anchor_field = ''  # No anchor without selection
            sel_start = 0
            sel_end = 0

        # Resolve the target Segment via the row's id cell.
        id_item = self.table.item(row, 0)
        if not id_item:
            return
        try:
            segment_id = int(id_item.text())
        except (ValueError, AttributeError):
            return
        segment = next(
            (s for s in self.current_project.segments if s.id == segment_id), None
        )
        if not segment:
            return

        # ── Capture dialog ──────────────────────────────────────────
        dialog = _QDialog(self)
        dialog.setWindowTitle(self.tr("Add comment"))
        dialog.setMinimumWidth(480)
        layout = QVBoxLayout(dialog)

        anchor_desc = (
            f"<b>Segment #{segment.id}</b> &mdash; "
            f"{'anchored to ' + anchor_field if anchor_field else 'segment-level (no anchor)'}"
        )
        info = QLabel(anchor_desc)
        info.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(info)

        if selected_text:
            # Trim a long selection so the dialog doesn't blow out.
            snippet = selected_text[:140] + ('…' if len(selected_text) > 140 else '')
            # Escape for HTML so user text doesn't break the label.
            try:
                import html as _html
                snippet_escaped = _html.escape(snippet)
            except Exception:
                snippet_escaped = snippet
            snippet_label = QLabel(f'Re: <i>"{snippet_escaped}"</i>')
            snippet_label.setWordWrap(True)
            snippet_label.setTextFormat(Qt.TextFormat.RichText)
            snippet_label.setStyleSheet(
                "color: #555; padding: 6px 8px; "
                "background-color: rgba(37, 99, 235, 0.08); "
                "border-radius: 4px; margin: 4px 0;"
            )
            layout.addWidget(snippet_label)

        layout.addWidget(QLabel(self.tr("Comment:")))
        text_edit = _QPlainTextEdit()
        text_edit.setMinimumHeight(90)
        text_edit.setPlaceholderText(self.tr("Your comment…"))
        layout.addWidget(text_edit)

        buttons = _QDialogButtonBox(
            _QDialogButtonBox.StandardButton.Ok
            | _QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        text_edit.setFocus()

        if dialog.exec() != _QDialog.DialogCode.Accepted:
            return

        comment_text = text_edit.toPlainText().strip()
        if not comment_text:
            return  # No empty comments

        author = (
            self.get_translator_name()
            if hasattr(self, 'get_translator_name') else ''
        )
        segment.add_comment(
            text=comment_text,
            author=author,
            anchor_field=anchor_field,
            anchor_start=sel_start,
            anchor_end=sel_end,
        )
        self.project_modified = True
        try:
            self.update_window_title()
        except Exception:
            pass

        # Refresh the all-comments list + status cell + anchor highlight.
        try:
            self._refresh_segment_comments_list()
        except Exception as e:
            self.log(f"⚠ Comments list refresh failed: {e}")
        try:
            self._refresh_segment_status(segment)
        except Exception:
            pass
        # Re-paint anchor highlights on the affected row's cells.
        try:
            self._apply_comment_anchors_to_cell(row, segment)
        except Exception as e:
            self.log(f"⚠ Anchor highlight refresh failed: {e}")

        if hasattr(self, 'status_bar'):
            anchor_note = (
                f" (anchored to {anchor_field})" if anchor_field else ""
            )
            self.status_bar.showMessage(
                f"💬 Comment added to segment #{segment.id}{anchor_note}",
                3000,
            )

    def focus_segment_notes(self):
        """Switch to the Comments tab → Segment sub-tab and focus the editor.

        v1.10.56 collapsed the previous separate Segment-note and
        Proofreading-note tabs into a single "💬 Comments" parent tab
        with two sub-tabs. This handler now navigates that two-level
        structure: parent first, then sub-tab, then focus the editor.
        """
        if not hasattr(self, 'right_tabs'):
            return

        # Find the parent Comments tab — by stored index first (set in
        # the tab-construction code), with a fallback label scan in case
        # the tab order shifts later.
        target_index = getattr(self, '_comments_tab_index', None)
        if target_index is None or target_index >= self.right_tabs.count():
            for i in range(self.right_tabs.count()):
                if "Comments" in self.right_tabs.tabText(i):
                    target_index = i
                    break
        if target_index is not None:
            self.right_tabs.setCurrentIndex(target_index)

        # Switch the inner tab widget to "📝 Segment" (sub-index 0).
        if hasattr(self, 'comments_sub_tabs'):
            self.comments_sub_tabs.setCurrentIndex(0)

        # Focus the editor so the user can start typing immediately.
        if hasattr(self, 'bottom_notes_edit'):
            self.bottom_notes_edit.setFocus()

    def on_tab_target_change(self):
        """Handle target text change in tab editor - updates all panels"""
        if not hasattr(self, 'tab_current_segment_id') or not self.tab_current_segment_id:
            return
        
        # Get the new text from whichever panel triggered this
        new_text = None
        if hasattr(self, 'tabbed_panels'):
            for panel in self.tabbed_panels:
                try:
                    if hasattr(panel, 'editor_widget') and hasattr(panel.editor_widget, 'target_editor'):
                        new_text = panel.editor_widget.target_editor.toPlainText()
                        break  # Use text from first panel found
                except:
                    continue
        
        if new_text is None:
            return
        
        # Auto-save to current_project data structure
        if self.current_project and hasattr(self.current_project, 'segments'):
            for seg in self.current_project.segments:
                if seg.id == self.tab_current_segment_id:
                    # Check if text actually changed
                    old_target = seg.target
                    old_status = seg.status
                    
                    # IMMEDIATE: Update segment data
                    seg.target = new_text
                    self.project_modified = True
                    
                    # Reset 'confirmed' status to 'draft' when user edits the segment
                    # This prevents auto-saving to TM until user re-confirms the edit
                    if old_status == 'confirmed' and new_text != old_target:
                        seg.status = 'draft'
                        if self.debug_mode_enabled:
                            self.log(f"📝 Tab editor: Status reset confirmed → draft (segment edited)")
                        # Refresh status in all views
                        self._refresh_segment_status(seg)
                    
                    # Update all other panels to keep them in sync
                    if hasattr(self, 'tabbed_panels'):
                        for panel in self.tabbed_panels:
                            try:
                                if hasattr(panel, 'editor_widget') and hasattr(panel.editor_widget, 'target_editor'):
                                    # Temporarily disconnect to avoid infinite loop
                                    panel.editor_widget.target_editor.blockSignals(True)
                                    panel.editor_widget.target_editor.setPlainText(new_text)
                                    panel.editor_widget.target_editor.blockSignals(False)
                            except:
                                pass
                    
                    # DEBOUNCED: Save to TM (expensive operation)
                    # Cancel previous timer
                    if hasattr(self, '_tab_target_debounce_timer'):
                        self._tab_target_debounce_timer.stop()
                    
                    # Schedule TM save after 500ms of inactivity
                    from PyQt6.QtCore import QTimer
                    self._tab_target_debounce_timer = QTimer()
                    self._tab_target_debounce_timer.setSingleShot(True)
                    # CRITICAL: Capture variables by value using default parameters to avoid closure bugs
                    self._tab_target_debounce_timer.timeout.connect(lambda segment=seg, text=new_text: self._save_tab_target_to_tm(segment, text))
                    self._tab_target_debounce_timer.start(500)
                    
                    break

    def on_tab_status_combo_changed(self, index: int):
        combo = self.sender()
        if isinstance(combo, QComboBox) and index >= 0:
            status_key = combo.itemData(index)
            if status_key:
                self.on_tab_segment_status_change(status_key)

    def on_tab_segment_status_change(self, status_key: str):
        """Handle status change in tab editor - updates all panels"""
        if not hasattr(self, 'tab_current_segment_id') or not self.tab_current_segment_id:
            return

        status_key = status_key or DEFAULT_STATUS.key
        status_def = get_status(status_key)

        if self.current_project and hasattr(self.current_project, 'segments'):
            for seg in self.current_project.segments:
                if seg.id == self.tab_current_segment_id:
                    seg.status = status_key
                    self.project_modified = True
                    self.log(f"✓ Status changed to: {status_def.label}")

                    # Update all panels to keep them in sync
                    if hasattr(self, 'tabbed_panels'):
                        for panel in self.tabbed_panels:
                            try:
                                if hasattr(panel, 'editor_widget') and hasattr(panel.editor_widget, 'status_combo'):
                                    combo = panel.editor_widget.status_combo
                                    combo.blockSignals(True)
                                    idx = combo.findData(status_key)
                                    if idx >= 0:
                                        combo.setCurrentIndex(idx)
                                    combo.blockSignals(False)
                            except Exception as e:
                                self.log(f"Error syncing status combo: {e}")
                    self._refresh_segment_status(seg)
                    
                    # Save to TM ONLY if status changed to confirmed (user explicitly approved)
                    if status_key == 'confirmed' and seg.target.strip():
                        try:
                            self.save_segment_to_activated_tms(seg.source, seg.target)
                            self.log(f"✓ Saved to TM: {seg.source[:30]}... → {seg.target[:30]}...")
                        except Exception as e:
                            self.log(f"Warning: Could not save to TM: {e}")
                    
                    break

    def on_tab_notes_change(self):
        """Handle comments change in tab editor - updates all panels"""
        if not hasattr(self, 'tab_current_segment_id') or not self.tab_current_segment_id:
            return
        
        # Get the new comments from whichever panel triggered this
        new_notes = None
        if hasattr(self, 'tabbed_panels'):
            for panel in self.tabbed_panels:
                try:
                    if hasattr(panel, 'notes_widget') and hasattr(panel.notes_widget, 'notes_editor'):
                        new_notes = panel.notes_widget.notes_editor.toPlainText()
                        break
                except:
                    continue
        
        if new_notes is None:
            return
        
        if self.current_project and hasattr(self.current_project, 'segments'):
            for seg in self.current_project.segments:
                if seg.id == self.tab_current_segment_id:
                    seg.notes = new_notes
                    self.project_modified = True
                    
                    # Update all other panels to keep them in sync
                    if hasattr(self, 'tabbed_panels'):
                        for panel in self.tabbed_panels:
                            try:
                                if hasattr(panel, 'notes_widget') and hasattr(panel.notes_widget, 'notes_editor'):
                                    panel.notes_widget.notes_editor.blockSignals(True)
                                    panel.notes_widget.notes_editor.setPlainText(new_notes)
                                    panel.notes_widget.notes_editor.blockSignals(False)
                            except:
                                pass
                    self._refresh_segment_status(seg)
                    break

    def _on_results_panel_notes_changed(self):
        """Handle notes change in Translation Results panel - saves to current segment.
        NOTE: This function is deprecated - Translation Results panel has been removed.
        """
        # Translation Results panel is deprecated - this should never be called
        if not self.translation_results_panel:
            return
        if not self.current_project:
            return
        
        # Get current segment from grid selection
        current_row = self.table.currentRow()
        if current_row < 0:
            return
        
        id_item = self.table.item(current_row, 0)
        if not id_item:
            return
        
        try:
            segment_id = int(id_item.text())
        except (ValueError, AttributeError):
            return
        
        # Find segment and update notes
        for seg in self.current_project.segments:
            if seg.id == segment_id:
                new_notes = self.translation_results_panel.notes_edit.toPlainText()
                seg.notes = new_notes
                self.project_modified = True
                # Sync with bottom notes panel
                if hasattr(self, 'bottom_notes_edit') and self.bottom_notes_edit:
                    self.bottom_notes_edit.blockSignals(True)
                    self.bottom_notes_edit.setPlainText(new_notes)
                    self.bottom_notes_edit.blockSignals(False)
                # Refresh the status cell to update the notes indicator
                self._refresh_segment_status(seg)
                break

    def _comment_anchor_bg_color(self):
        if self._COMMENT_ANCHOR_BG_QCOLOR is None:
            # Use a light amber with full alpha — cell renderers don't
            # always honour transparency, and full-alpha picks up cleanly
            # against the editor's default white background.
            self._COMMENT_ANCHOR_BG_QCOLOR = QColor(254, 240, 138)  # amber-200
        return self._COMMENT_ANCHOR_BG_QCOLOR

    def _apply_comment_anchors_to_cell(self, row: int, segment):
        """Apply a background highlight to every anchored character range
        in this row's source and target cell editors.

        Called from load_segments_to_grid (initial paint), after
        Ctrl+Shift+M creates a comment, and after edit/delete.
        Cheap to call repeatedly — at most one cursor pass per
        anchored comment, and visible cells only.

        Defensive against text-length mismatches: anchor offsets are
        clamped to the actual cell text length. If the user has
        edited the target text since the comment was anchored, the
        highlight may not match the original wording, but at least
        it won't crash or wander into invalid territory.
        """
        if not segment or not getattr(segment, 'comments', None):
            return
        if not hasattr(self, 'table') or not self.table:
            return

        from PyQt6.QtGui import QTextCursor as _QTextCursor, QTextCharFormat as _QTextCharFormat

        bg_color = self._comment_anchor_bg_color()
        fmt = _QTextCharFormat()
        fmt.setBackground(bg_color)
        # Force black text on the amber highlight so it stays readable in dark
        # themes (light theme text on light amber is unreadable). The amber is
        # always light, so black works in every theme.
        fmt.setForeground(QColor('#000000'))

        for col, field_name in [(2, 'source'), (3, 'target')]:
            editor = self.table.cellWidget(row, col)
            if editor is None or not hasattr(editor, 'document'):
                continue
            text_len = len(editor.toPlainText())
            for c in segment.comments:
                if c.anchor_field != field_name or not c.is_anchored:
                    continue
                start = max(0, min(c.anchor_start, text_len))
                end = max(start, min(c.anchor_end, text_len))
                if start == end:
                    continue
                cursor = _QTextCursor(editor.document())
                cursor.setPosition(start)
                cursor.setPosition(end, _QTextCursor.MoveMode.KeepAnchor)
                cursor.mergeCharFormat(fmt)

    def _apply_comment_anchors_to_all_cells(self):
        """Apply anchor highlights to every currently-visible row.

        Called once at the end of load_segments_to_grid so the
        highlights show up immediately after a project load or a
        page-switch (with pagination, only the current page's rows
        actually have cell widgets).
        """
        if (not self.current_project
                or not self.current_project.segments
                or not hasattr(self, 'table')
                or not self.table):
            return

        # Build a quick lookup: segment_id → segment, since cells store id only.
        by_id = {s.id: s for s in self.current_project.segments}

        for row in range(self.table.rowCount()):
            id_item = self.table.item(row, 0)
            if not id_item:
                continue
            try:
                seg_id = int(id_item.text())
            except (ValueError, AttributeError):
                continue
            segment = by_id.get(seg_id)
            if segment is None:
                continue
            self._apply_comment_anchors_to_cell(row, segment)

    def _refresh_cell_text_for_anchors(self, row: int, segment):
        """Reset character formatting on this row's source/target cells.

        Used as a "cheap clear" before re-applying anchor highlights,
        since mergeCharFormat is additive (no per-range undo). Calling
        setPlainText() with the cell's current text strips all rich-text
        char formats (Qt documented behaviour) without changing the text
        content. Signals are blocked so the legacy textChanged handlers
        don't interpret this as a user edit.
        """
        if not hasattr(self, 'table') or not self.table:
            return
        from PyQt6.QtGui import QTextCursor as _QTextCursor, QTextCharFormat as _QTextCharFormat
        for col in (2, 3):
            editor = self.table.cellWidget(row, col)
            if editor is None or not hasattr(editor, 'setPlainText'):
                continue
            current_text = editor.toPlainText()
            editor.blockSignals(True)
            try:
                editor.setPlainText(current_text)
                # setPlainText re-inserts the text using the editor's *current*
                # char format. If the caret happened to be resting inside an
                # amber comment range when this clear runs (e.g. deleting the
                # comment you just clicked into), that current format carries
                # the amber background, so the whole cell would come back
                # yellow — issue #223, where deleting a comment painted the
                # entire segment instead of clearing the word. Explicitly reset
                # every character to a default format so no inherited background
                # survives; the caller re-applies anchor highlights for any
                # remaining comments.
                _clear_cur = _QTextCursor(editor.document())
                _clear_cur.select(_QTextCursor.SelectionType.Document)
                _clear_cur.setCharFormat(_QTextCharFormat())
            finally:
                editor.blockSignals(False)

    def _find_row_for_segment_id(self, segment_id: int) -> int:
        """Return the row index for a segment id in the visible table,
        or -1 if the segment isn't on the current page. Helper for the
        targeted anchor-refresh after a single comment is created/edited."""
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

    def _reset_comment_ui_state(self):
        """Clear the per-segment comment/notes editors and active-comment
        tracking so comments from a previously-open project don't linger in
        the Comments tab (issue #235).

        Only the per-segment editors need explicit clearing here — the
        all-comments lists are rebuilt from ``current_project`` by
        ``_refresh_segment_comments_list`` / ``_refresh_proofreading_comments_list``.
        Signals are blocked while clearing so the textChanged handlers don't
        try to write the empty string back onto a stale/absent segment.
        """
        self.tab_current_segment_id = None
        self._active_comment_seg_id = None

        # Bottom single-string notes/comment editor.
        if getattr(self, 'bottom_notes_edit', None):
            self.bottom_notes_edit.blockSignals(True)
            self.bottom_notes_edit.setPlainText('')
            self.bottom_notes_edit.blockSignals(False)

        # Per-panel "Add comments for this segment…" tab editors.
        if hasattr(self, 'tabbed_panels'):
            for panel in self.tabbed_panels:
                try:
                    nw = getattr(panel, 'notes_widget', None)
                    if nw is not None and hasattr(nw, 'notes_editor'):
                        nw.notes_editor.blockSignals(True)
                        nw.notes_editor.setPlainText('')
                        nw.notes_editor.blockSignals(False)
                except Exception:
                    pass

    def _refresh_segment_comments_list(self):
        """Rebuild the all-comments list at the top of the Comments → Segment sub-tab.

        v1.10.57 Phase D: iterates ``segment.comments`` (one entry per
        Comment) instead of the legacy single-string ``segment.notes``.
        A segment with three comments shows three entries; each entry
        carries its own anchor snippet (if any), author, timestamp,
        and a right-click context menu for edit / delete.

        Idempotent. Cheap to call on every change — number of comments
        per project is typically small.
        """
        if not hasattr(self, '_segment_comments_list_layout'):
            return  # Sub-tab not built yet (early init)

        from PyQt6.QtWidgets import QFrame as _QFrame

        layout = self._segment_comments_list_layout

        # Map of comment id → its header button, so _open_comment_in_panel()
        # can scroll a specific comment into view and flash it. Rebuilt here
        # since the widgets are recreated on every refresh.
        self._comment_list_header_btns = {}
        # seg_id → [header buttons] for that segment, so selecting a segment can
        # scroll the list to (and highlight) its comment(s) — issue #234 pt.3.
        self._comment_list_seg_btns = {}

        # Clear existing children. Walk takeAt(0) until the layout is
        # empty so both widgets AND the trailing stretch get cleaned up.
        while layout.count() > 0:
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        # Collect (segment, comment) pairs in document order, one row
        # per Comment. Skips comments with empty text just in case.
        entries: list = []  # [(seg_id, segment, comment), ...]
        if self.current_project and self.current_project.segments:
            for seg in self.current_project.segments:
                for c in getattr(seg, 'comments', []) or []:
                    if c.text and c.text.strip():
                        entries.append((seg.id, seg, c))

        if not entries:
            empty = QLabel(
                "(No comments in this project yet. Select text in a source or "
                "target cell and press <b>Ctrl+Shift+M</b> to add an anchored "
                "comment, or use the editor below for a segment-level note.)"
            )
            empty.setTextFormat(Qt.TextFormat.RichText)
            empty.setStyleSheet("color: #999; padding: 8px;")
            empty.setWordWrap(True)
            layout.addWidget(empty)
            layout.addStretch()
            return

        for idx, (seg_id, segment, comment) in enumerate(entries):
            # Divider between entries (not above the first).
            if idx > 0:
                divider = _QFrame()
                divider.setFrameShape(_QFrame.Shape.HLine)
                divider.setStyleSheet("color: #e0e0e0; max-height: 1px;")
                layout.addWidget(divider)

            # ── Header: "Segment #N" + optional anchor tag ──
            header_label = f"Segment #{seg_id}"
            if comment.is_anchored:
                header_label += f"  ⚓ {comment.anchor_field}"
            header_btn = QPushButton(header_label)
            header_btn.setFlat(True)
            header_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            _header_qss = (
                "QPushButton { "
                "  color: #2563eb; "
                "  font-weight: bold; "
                "  font-size: 10pt; "
                "  text-align: left; "
                "  padding: 6px 8px 2px 8px; "
                "  border: none; "
                "  background: transparent; "
                "} "
                "QPushButton:hover { "
                "  background-color: rgba(37, 99, 235, 0.08); "
                "  text-decoration: underline; "
                "}"
            )
            header_btn.setStyleSheet(_header_qss)
            header_btn._base_qss = _header_qss  # for active-segment highlight (pt.3)
            header_btn.clicked.connect(
                lambda _checked, sid=seg_id: self._navigate_to_segment_by_id(sid)
            )
            self._comment_list_header_btns[comment.id] = header_btn
            self._comment_list_seg_btns.setdefault(seg_id, []).append(header_btn)
            # Right-click context menu for edit/delete.
            header_btn.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            header_btn.customContextMenuRequested.connect(
                lambda _pos, seg=segment, cid=comment.id, btn=header_btn:
                    self._comment_context_menu(seg, cid, btn)
            )
            layout.addWidget(header_btn)

            # ── Optional anchored-text snippet ──
            if comment.is_anchored:
                source_text = (segment.source if comment.anchor_field == 'source'
                               else segment.target) or ''
                snippet_raw = source_text[comment.anchor_start:comment.anchor_end]
                snippet = snippet_raw[:140] + ('…' if len(snippet_raw) > 140 else '')
                try:
                    import html as _html
                    snippet_escaped = _html.escape(snippet) if snippet else '(range)'
                except Exception:
                    snippet_escaped = snippet or '(range)'
                anchor_label = QLabel(f'<i>"{snippet_escaped}"</i>')
                anchor_label.setTextFormat(Qt.TextFormat.RichText)
                anchor_label.setWordWrap(True)
                anchor_label.setStyleSheet(
                    "color: #555; padding: 0 12px 4px 12px; font-size: 9pt;"
                )
                layout.addWidget(anchor_label)

            # ── Body ──
            body = QLabel(comment.text)
            body.setWordWrap(True)
            body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            body.setStyleSheet(
                "padding: 0 12px 4px 12px; "
                "font-size: 10pt; "
                "color: #333;"
            )
            layout.addWidget(body)

            # ── Footer: author + timestamp ──
            footer_parts = []
            if comment.author:
                footer_parts.append(comment.author)
            if comment.created:
                # Show just date + HH:MM, not the full ISO blob.
                try:
                    from datetime import datetime as _dt
                    parsed = _dt.fromisoformat(comment.created)
                    footer_parts.append(parsed.strftime('%Y-%m-%d %H:%M'))
                except Exception:
                    footer_parts.append(comment.created[:16])
            if footer_parts:
                footer = QLabel(' · '.join(footer_parts) + '  (right-click for edit/delete)')
                footer.setStyleSheet(
                    "color: #888; padding: 0 12px 8px 12px; font-size: 8pt; "
                    "font-style: italic;"
                )
                layout.addWidget(footer)

        layout.addStretch()

        # Update bottom-editor lock state — when any segment in the project
        # has multiple comments or any anchored comment, the legacy
        # single-string editor would silently clobber them. Lock it
        # whenever the currently-selected segment is in that state.
        self._update_bottom_notes_editor_lock_state()

        # Re-apply the active-segment highlight after a rebuild (the buttons
        # were just recreated, so the old highlight is gone). No scroll. (pt.3)
        _active = getattr(self, '_active_comment_seg_id', None)
        if _active is not None:
            self._sync_segment_comments_to_active(_active, do_scroll=False)

    def _sync_segment_comments_to_active(self, seg_id, do_scroll: bool = True):
        """Scroll the Segment-Comments list to the active segment's comment(s)
        and give them a persistent highlight (issue #234 pt.3). No-op if the
        sub-tab isn't built or the segment has no comments in the list."""
        self._active_comment_seg_id = seg_id

        # Clear the previous highlight (skip widgets destroyed by a rebuild).
        for btn in getattr(self, '_active_comment_highlight_btns', []):
            if self._widget_is_alive(btn):
                base = getattr(btn, '_base_qss', None)
                if base is not None:
                    btn.setStyleSheet(base)
        self._active_comment_highlight_btns = []

        btns = getattr(self, '_comment_list_seg_btns', {}).get(seg_id, [])
        if not btns:
            return

        for btn in btns:
            if self._widget_is_alive(btn):
                base = getattr(btn, '_base_qss', btn.styleSheet())
                btn.setStyleSheet(
                    base +
                    "\nQPushButton { background-color: rgba(37, 99, 235, 0.14); "
                    "border-radius: 3px; }"
                )
                self._active_comment_highlight_btns.append(btn)

        if do_scroll:
            scroll = getattr(self, '_segment_comments_list_scroll', None)
            first = btns[0]

            def _reveal():
                try:
                    if (scroll is not None and self._widget_is_alive(scroll)
                            and self._widget_is_alive(first)):
                        scroll.ensureWidgetVisible(first)
                except Exception:
                    pass

            # Defer so any pending layout / tab switch settles before scrolling.
            QTimer.singleShot(0, _reveal)

    def _comment_context_menu(self, segment: 'Segment', comment_id: str, anchor_widget):
        """Right-click context menu on a comments-list entry. Edit / Delete."""
        from PyQt6.QtWidgets import QMenu, QMessageBox as _QMessageBox
        from PyQt6.QtGui import QAction as _QAction

        comment = segment.get_comment(comment_id)
        if not comment:
            return

        menu = QMenu(anchor_widget)
        edit_action = _QAction("✏️ Edit comment…", menu)
        delete_action = _QAction("🗑️ Delete comment", menu)
        menu.addAction(edit_action)
        menu.addAction(delete_action)

        chosen = menu.exec(anchor_widget.mapToGlobal(anchor_widget.rect().bottomLeft()))
        if chosen is edit_action:
            self._edit_comment_dialog(segment, comment_id)
        elif chosen is delete_action:
            reply = _QMessageBox.question(
                self, "Delete comment",
                f"Delete this comment from segment #{segment.id}?\n\n"
                f"\"{comment.text[:100]}{'…' if len(comment.text) > 100 else ''}\"",
                _QMessageBox.StandardButton.Yes | _QMessageBox.StandardButton.No,
                _QMessageBox.StandardButton.No,
            )
            if reply == _QMessageBox.StandardButton.Yes:
                segment.remove_comment(comment_id)
                self.project_modified = True
                try:
                    self.update_window_title()
                except Exception:
                    pass
                try:
                    self._refresh_segment_status(segment)
                except Exception:
                    pass
                self._refresh_segment_comments_list()
                # Re-apply anchor highlights on the affected row (the
                # removed comment's highlight needs to go; remaining
                # anchored comments stay highlighted).
                affected_row = self._find_row_for_segment_id(segment.id)
                if affected_row >= 0:
                    # We need to clear ALL char-format backgrounds first
                    # since mergeCharFormat is additive and we have no
                    # cheap way to identify "our" old highlight to undo.
                    # Easiest: refresh the cell text, which resets formats.
                    self._refresh_cell_text_for_anchors(affected_row, segment)
                    self._apply_comment_anchors_to_cell(affected_row, segment)
                # Keep the bottom editor in sync if the deleted comment
                # belonged to the currently-selected segment.
                if hasattr(self, '_update_bottom_notes_for_segment'):
                    self._update_bottom_notes_for_segment(segment)

    def _edit_comment_dialog(self, segment: 'Segment', comment_id: str):
        """Open a dialog to edit an existing comment's text. Anchor + author
        are kept as-is; only the body is editable here."""
        from PyQt6.QtWidgets import (QDialog as _QDialog, QDialogButtonBox as _QDialogButtonBox,
                                     QPlainTextEdit as _QPlainTextEdit)

        comment = segment.get_comment(comment_id)
        if not comment:
            return

        dialog = _QDialog(self)
        dialog.setWindowTitle(self.tr("Edit comment"))
        dialog.setMinimumWidth(480)
        layout = QVBoxLayout(dialog)

        header = QLabel(
            f"<b>Segment #{segment.id}</b> &mdash; " +
            (f"anchored to {comment.anchor_field}" if comment.is_anchored else "segment-level")
        )
        header.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(header)

        if comment.is_anchored:
            source_text = (segment.source if comment.anchor_field == 'source'
                           else segment.target) or ''
            snippet_raw = source_text[comment.anchor_start:comment.anchor_end]
            snippet = snippet_raw[:140] + ('…' if len(snippet_raw) > 140 else '')
            try:
                import html as _html
                snippet_escaped = _html.escape(snippet)
            except Exception:
                snippet_escaped = snippet
            sl = QLabel(f'Re: <i>"{snippet_escaped}"</i>')
            sl.setTextFormat(Qt.TextFormat.RichText)
            sl.setWordWrap(True)
            sl.setStyleSheet(
                "color: #555; padding: 6px 8px; "
                "background-color: rgba(37, 99, 235, 0.08); "
                "border-radius: 4px; margin: 4px 0;"
            )
            layout.addWidget(sl)

        layout.addWidget(QLabel(self.tr("Comment:")))
        text_edit = _QPlainTextEdit()
        text_edit.setPlainText(comment.text)
        text_edit.setMinimumHeight(120)
        layout.addWidget(text_edit)

        buttons = _QDialogButtonBox(
            _QDialogButtonBox.StandardButton.Ok
            | _QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        text_edit.setFocus()
        # Place cursor at end so the user can append immediately.
        cur = text_edit.textCursor()
        cur.movePosition(cur.MoveOperation.End)
        text_edit.setTextCursor(cur)

        if dialog.exec() != _QDialog.DialogCode.Accepted:
            return
        new_text = text_edit.toPlainText().strip()
        if new_text == comment.text:
            return  # No change

        if new_text:
            segment.update_comment(comment_id, new_text)
        else:
            segment.remove_comment(comment_id)
        self.project_modified = True
        try:
            self.update_window_title()
        except Exception:
            pass
        try:
            self._refresh_segment_status(segment)
        except Exception:
            pass
        self._refresh_segment_comments_list()
        # Re-apply anchor highlights on the affected row (text content
        # of the anchor is unchanged but a delete-via-empty-edit may
        # have removed a comment whose highlight needs to go).
        affected_row = self._find_row_for_segment_id(segment.id)
        if affected_row >= 0:
            self._refresh_cell_text_for_anchors(affected_row, segment)
            self._apply_comment_anchors_to_cell(affected_row, segment)
        if hasattr(self, '_update_bottom_notes_for_segment'):
            self._update_bottom_notes_for_segment(segment)

    def _update_bottom_notes_editor_lock_state(self):
        """Disable the legacy bottom comment editor when the current segment
        has multiple comments or any anchored comment.

        The bottom editor is a single QPlainTextEdit that writes to
        ``segment.notes`` (via ``replace_all_comments_with_text``).
        That works for the simple one-segment-level-comment case but
        would silently clobber anchored comments or merge multiple
        comments into one. When the current segment is in that "complex"
        state, the editor goes read-only with a hint that the user
        should use the all-comments list above instead.
        """
        if not hasattr(self, 'bottom_notes_edit') or not self.bottom_notes_edit:
            return
        # Determine the currently-selected segment.
        segment = None
        if (self.current_project and self.current_project.segments
                and hasattr(self, 'table') and self.table):
            current_row = self.table.currentRow()
            if current_row >= 0:
                id_item = self.table.item(current_row, 0)
                if id_item:
                    try:
                        seg_id = int(id_item.text())
                        segment = next(
                            (s for s in self.current_project.segments
                             if s.id == seg_id), None
                        )
                    except (ValueError, AttributeError):
                        pass

        is_complex = False
        if segment:
            comments = getattr(segment, 'comments', []) or []
            if len(comments) > 1:
                is_complex = True
            elif any(c.is_anchored for c in comments):
                is_complex = True

        if is_complex:
            self.bottom_notes_edit.setReadOnly(True)
            self.bottom_notes_edit.setPlaceholderText(
                "This segment has multiple comments or an anchored comment. "
                "Use the list above to edit or delete individual comments "
                "(right-click on a Segment #N header)."
            )
            self.bottom_notes_edit.setStyleSheet(
                "font-size: 10pt; background-color: #f5f5f5; color: #888;"
            )
        else:
            self.bottom_notes_edit.setReadOnly(False)
            self.bottom_notes_edit.setPlaceholderText(
                "Add a comment about this segment — context, "
                "translation concerns, anything worth flagging..."
            )
            self.bottom_notes_edit.setStyleSheet("font-size: 10pt;")

    def _navigate_to_segment_by_id(self, segment_id: int):
        """Navigate to a segment by ID, switching pages first if needed.

        Unlike :py:meth:`_navigate_to_segment_in_grid` (which looks the
        segment up by row index in the *currently visible* table), this
        version finds the segment's index in the full project segment
        list, computes which page it's on, switches to that page if
        we're not already there, and only then selects the row. Works
        correctly across pagination boundaries.
        """
        if not self.current_project or not self.current_project.segments:
            return
        if not hasattr(self, 'table') or not self.table:
            return

        # Locate the target segment in the full project list.
        segments = self.current_project.segments
        target_idx = None
        for i, seg in enumerate(segments):
            if seg.id == segment_id:
                target_idx = i
                break
        if target_idx is None:
            self.log(f"⚠ Segment #{segment_id} not found in project")
            return

        # If pagination is active and the segment is on a different page,
        # switch to that page first. The grid rebuilds synchronously
        # inside go_to_page, so by the time it returns the target row
        # is visible.
        if (hasattr(self, 'page_size_combo')
                and self.page_size_combo.currentText() != "All"):
            try:
                page_size = int(self.page_size_combo.currentText())
                target_page = (target_idx // page_size) + 1
                current_page = getattr(self, 'grid_current_page', 0) + 1
                if current_page != target_page and hasattr(self, 'page_number_input'):
                    self.page_number_input.setText(str(target_page))
                    self.go_to_page()
            except (ValueError, AttributeError):
                pass

        # Switch to the Grid (Editor) tab if we're elsewhere.
        if hasattr(self, 'main_tabs'):
            self.main_tabs.setCurrentIndex(0)

        # Find the row in the (possibly newly-rebuilt) table and select it.
        for row in range(self.table.rowCount()):
            id_item = self.table.item(row, 0)
            if id_item:
                try:
                    if int(id_item.text()) == segment_id:
                        self.table.setCurrentCell(row, 3)  # Column 3 = Target
                        self.table.scrollToItem(
                            self.table.item(row, 0),
                            QTableWidget.ScrollHint.PositionAtCenter,
                        )
                        target_widget = self.table.cellWidget(row, 3)
                        if target_widget:
                            target_widget.setFocus()
                        return
                except (ValueError, AttributeError):
                    continue

        # Reaching here means the page-switch logic didn't put the
        # segment on the current page — shouldn't happen but log it.
        self.log(
            f"⚠ Segment #{segment_id} not on current grid page after "
            f"page-switch attempt (unexpected)"
        )

    def _open_comment_in_panel(self, comment_id: str):
        """Reveal a specific comment in the Comments ▸ Segment sub-tab.

        Invoked by the "💬 Open comment" item on a cell's anchored-comment
        highlight. Switches the right panel to the Comments tab, scrolls the
        all-comments list to the matching entry, and briefly flashes it. Handy
        when the Match Panel (or any other tab) is showing and you want to jump
        straight from a yellow-highlighted range to its comment.
        """
        # Ensure the list (and the comment→button map) is current.
        try:
            self._refresh_segment_comments_list()
        except Exception:
            pass

        # Switch the right panel to Comments → Segment sub-tab. Resolve the
        # Comments tab by its LABEL, not the stored _comments_tab_index — the
        # Chat/AI tab is inserted into the row after build time, shifting the
        # Comments tab down, so the cached index goes stale (it pointed at
        # Preview).
        if hasattr(self, 'right_tabs'):
            target_index = None
            for i in range(self.right_tabs.count()):
                if "Comments" in self.right_tabs.tabText(i):
                    target_index = i
                    break
            if target_index is not None:
                self.right_tabs.setCurrentIndex(target_index)
        if hasattr(self, 'comments_sub_tabs'):
            self.comments_sub_tabs.setCurrentIndex(0)

        btn = getattr(self, '_comment_list_header_btns', {}).get(comment_id)
        if btn is None:
            return
        scroll = getattr(self, '_segment_comments_list_scroll', None)

        def _reveal_and_flash():
            try:
                if scroll is not None and self._widget_is_alive(scroll):
                    scroll.ensureWidgetVisible(btn)
                self._flash_comment_header(btn)
            except Exception:
                pass

        # Defer so the tab switch / layout settles before we scroll.
        QTimer.singleShot(0, _reveal_and_flash)

    def _flash_comment_header(self, btn):
        """Briefly tint a comments-list header button so the eye lands on it."""
        if not self._widget_is_alive(btn):
            return
        original = btn.styleSheet()
        btn.setStyleSheet(
            original +
            "\nQPushButton { background-color: rgba(255, 213, 79, 0.55); "
            "border-radius: 3px; }"
        )

        def _restore():
            if self._widget_is_alive(btn):
                btn.setStyleSheet(original)

        QTimer.singleShot(1400, _restore)

    def _on_bottom_notes_changed(self):
        """Handle notes change in bottom Notes tab - saves to current segment and syncs with Translation Results panel"""
        if not self.current_project:
            return
        
        # Get current segment from grid selection
        current_row = self.table.currentRow()
        if current_row < 0:
            return
        
        id_item = self.table.item(current_row, 0)
        if not id_item:
            return
        
        try:
            segment_id = int(id_item.text())
        except (ValueError, AttributeError):
            return
        
        # Find segment and update notes
        for seg in self.current_project.segments:
            if seg.id == segment_id:
                new_notes = self.bottom_notes_edit.toPlainText()
                # v1.10.57: route the assignment through the legacy
                # bridge helper so segment.comments stays in sync with
                # the legacy segment.notes single-string field. The
                # bridge preserves the existing comment's id when the
                # text is edited in place (no churn), or replaces the
                # comments list with a single new Comment otherwise.
                seg.replace_all_comments_with_text(
                    new_notes,
                    author=self.get_translator_name() if hasattr(self, 'get_translator_name') else '',
                )
                self.project_modified = True
                # Sync with Translation Results panel notes
                if hasattr(self, 'translation_results_panel') and self.translation_results_panel:
                    if hasattr(self.translation_results_panel, 'notes_edit') and self.translation_results_panel.notes_edit:
                        self.translation_results_panel.notes_edit.blockSignals(True)
                        self.translation_results_panel.notes_edit.setPlainText(new_notes)
                        self.translation_results_panel.notes_edit.blockSignals(False)
                # Refresh the status cell to update the notes indicator
                self._refresh_segment_status(seg)
                # v1.10.56: rebuild the all-comments list in the
                # Comments → Segment sub-tab so the new/edited comment
                # appears (or disappears, if cleared) immediately.
                self._refresh_segment_comments_list()
                break

    def _on_scratchpad_changed(self):
        """Handle scratchpad change - saves to current project (not segment-level)"""
        if not self.current_project:
            return
        
        new_notes = self.scratchpad_edit.toPlainText()
        self.current_project.scratchpad_notes = new_notes
        self.current_project.modified = datetime.now().isoformat()
        self.project_modified = True

    def _update_bottom_notes_for_segment(self, segment):
        """Update the bottom Notes tab with the current segment's notes.

        v1.10.57 Phase D: when the segment has multiple comments or any
        anchored comment, the legacy single-string editor would silently
        clobber them on the next textChanged. We detect that case and
        leave the editor empty (it'll be locked read-only by
        _update_bottom_notes_editor_lock_state, which the comments-list
        refresh calls). Otherwise — simple case, one segment-level
        comment (or none) — populate as before.
        """
        if not hasattr(self, 'bottom_notes_edit') or not self.bottom_notes_edit:
            return

        self.bottom_notes_edit.blockSignals(True)
        try:
            if segment and hasattr(segment, 'comments'):
                comments = getattr(segment, 'comments', []) or []
                is_complex = len(comments) > 1 or any(c.is_anchored for c in comments)
                if is_complex:
                    # Leave empty — the read-only state + placeholder
                    # tells the user to use the all-list above.
                    self.bottom_notes_edit.setPlainText('')
                elif comments:
                    # Exactly one unanchored segment-level comment.
                    self.bottom_notes_edit.setPlainText(comments[0].text or '')
                else:
                    self.bottom_notes_edit.setPlainText('')
            elif segment and hasattr(segment, 'notes'):
                # Pre-Phase-A segment (shouldn't happen post-migration,
                # but defensive — old in-memory objects might not have
                # the comments[] field yet).
                self.bottom_notes_edit.setPlainText(segment.notes or '')
            else:
                self.bottom_notes_edit.setPlainText('')
        finally:
            self.bottom_notes_edit.blockSignals(False)
        # Lock/unlock based on the segment's complexity.
        self._update_bottom_notes_editor_lock_state()

    def copy_source_to_tab_target(self):
        """Copy source to target in tab editor - works with all panels"""
        if hasattr(self, 'tabbed_panels'):
            for panel in self.tabbed_panels:
                try:
                    if hasattr(panel, 'editor_widget'):
                        source_text = panel.editor_widget.source_editor.toPlainText()
                        panel.editor_widget.target_editor.setPlainText(source_text)
                except:
                    pass

    def clear_tab_target(self):
        """Clear target in tab editor - works with all panels"""
        if hasattr(self, 'tabbed_panels'):
            for panel in self.tabbed_panels:
                try:
                    if hasattr(panel, 'editor_widget'):
                        panel.editor_widget.target_editor.clear()
                except:
                    pass

    def save_tab_segment(self):
        """Save current segment in tab editor"""
        if not hasattr(self, 'tab_current_segment_id') or not self.tab_current_segment_id:
            return
        
        # Find segment and save to TM if appropriate
        if self.current_project and hasattr(self.current_project, 'segments'):
            for seg in self.current_project.segments:
                if seg.id == self.tab_current_segment_id:
                    # Save to TM ONLY if status is confirmed (user explicitly approved)
                    if seg.status == 'confirmed' and seg.target.strip():
                        try:
                            self.save_segment_to_activated_tms(seg.source, seg.target)
                            self.log(f"✓ Saved segment {self.tab_current_segment_id} to TM")
                        except Exception as e:
                            self.log(f"✓ Saved segment {self.tab_current_segment_id} (TM save failed: {e})")
                    else:
                        self.log(f"✓ Saved segment {self.tab_current_segment_id}")
                    break
        else:
            self.log(f"✓ Saved segment {self.tab_current_segment_id}")

    def save_tab_segment_and_next(self):
        """Save current segment and move to next in tab editor"""
        self.save_tab_segment()
        
        # Move to next segment in grid view
        if hasattr(self, 'table') and self.table:
            current_row = self.table.currentRow()
            if current_row < self.table.rowCount() - 1:
                self.table.setCurrentCell(current_row + 1, self.table.currentColumn())
