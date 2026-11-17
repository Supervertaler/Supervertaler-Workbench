"""Changing the segments themselves: undo and redo, splitting, merging and
deleting segments, adding source text, and the bulk actions on selected
segments (copy source to target, clear, lock, clean tags).

Part of the main window (SupervertalerQt), moved here unchanged. The
methods run as the main window's own: `self` is the SupervertalerQt.
"""

import re
from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QProgressDialog,
    QTableWidgetItem,
    QVBoxLayout,
)
from modules.grid_editors import EditableGridTextEditor, ReadOnlyGridTextEditor
from modules.pseudo_translate_dialog import run_pseudo_translation
from modules.segment_model import Segment
from modules.shortcut_display import format_shortcut_for_display
from modules.tag_text import autotag_extract_tags


class SegmentEditingMixin:
    def record_undo_state(self, segment_id, old_target, new_target, old_status, new_status):
        """Record an undo state when grid cells are edited.

        For bulk operations (Copy Source to Target, AI batch, TM auto-fill,
        pre-translation, etc.) prefer ``record_undo_states_batch`` — it makes a
        single UI/trim pass at the end instead of one per segment.
        """
        # Don't record if nothing actually changed
        if old_target == new_target and old_status == new_status:
            return

        # Add to undo stack
        undo_entry = {
            "segment_id": segment_id,
            "old_target": old_target,
            "new_target": new_target,
            "old_status": old_status,
            "new_status": new_status
        }
        self.undo_stack.append(undo_entry)

        # Trim undo stack to max levels
        if len(self.undo_stack) > self.max_undo_levels:
            self.undo_stack.pop(0)

        # Clear redo stack (can't redo after new edit)
        self.redo_stack.clear()

        # Update menu actions
        self.update_undo_redo_actions()

    def record_undo_states_batch(self, entries):
        """Record many undo entries at once with one UI/trim pass at the end.

        ``entries`` is an iterable of dicts shaped like
        ``{"segment_id", "old_target", "new_target", "old_status", "new_status"}``
        OR tuples ``(segment_id, old_target, new_target, old_status, new_status)``.

        Entries where neither target nor status changed are silently skipped.
        Used by bulk operations to avoid N×Qt-action-refresh and N×list.pop(0)
        overhead when a batch is larger than max_undo_levels.
        """
        appended = 0
        for entry in entries:
            if isinstance(entry, dict):
                old_target = entry.get("old_target", "")
                new_target = entry.get("new_target", "")
                old_status = entry.get("old_status", "")
                new_status = entry.get("new_status", "")
                segment_id = entry.get("segment_id")
            else:
                # Tuple form
                segment_id, old_target, new_target, old_status, new_status = entry

            if old_target == new_target and old_status == new_status:
                continue

            self.undo_stack.append({
                "segment_id": segment_id,
                "old_target": old_target,
                "new_target": new_target,
                "old_status": old_status,
                "new_status": new_status,
            })
            appended += 1

        if appended == 0:
            return

        # Trim once at the end – avoids N pop(0) operations for huge batches
        overflow = len(self.undo_stack) - self.max_undo_levels
        if overflow > 0:
            del self.undo_stack[:overflow]

        # Clear redo stack (can't redo after new edits) and refresh menu once
        self.redo_stack.clear()
        self.update_undo_redo_actions()

    def undo_action_handler(self):
        """Handle Undo (Ctrl+Z) — revert the last recorded target/status change."""
        if not self.undo_stack:
            return

        action = self.undo_stack.pop()
        # Structural edits (split / merge) carry full before/after snapshots.
        if action.get("type") == "structural":
            self._apply_structural_history(action, redo=False)
            self.redo_stack.append(action)
            self.update_undo_redo_actions()
            return
        if self._apply_undo_redo_action(action, action["old_target"], action["old_status"]):
            self.redo_stack.append(action)
        self.update_undo_redo_actions()

    def redo_action_handler(self):
        """Handle Redo (Ctrl+Shift+Z / Ctrl+Y) — re-apply the last undone change."""
        if not self.redo_stack:
            return

        action = self.redo_stack.pop()
        if action.get("type") == "structural":
            self._apply_structural_history(action, redo=True)
            self.undo_stack.append(action)
            self.update_undo_redo_actions()
            return
        if self._apply_undo_redo_action(action, action["new_target"], action["new_status"]):
            self.undo_stack.append(action)
        self.update_undo_redo_actions()

    def _apply_undo_redo_action(self, action, target, status) -> bool:
        """Set the given target/status on the action's segment and refresh its
        row. Shared by undo and redo. Returns True if the segment was found and
        updated, False otherwise (the caller then drops the action).

        NB: the segment model uses ``id`` / ``target`` (not ``segment_id`` /
        ``target_text``), the grid is ``self.table`` (not ``self.grid``), and the
        Target cell is an editable cell *widget* at column 3 (Status is col 4) –
        the previous implementation used all the old names and silently threw.
        """
        if not self.current_project:
            return False

        segment_id = action["segment_id"]
        segment = next((s for s in self.current_project.segments if s.id == segment_id), None)
        if segment is None:
            return False

        segment.target = target
        segment.status = status

        row = self._find_row_for_segment(segment_id)
        if row >= 0:
            # Target column (3) is an editable QTextEdit cell widget.
            target_widget = self.table.cellWidget(row, 3)
            if target_widget is not None and hasattr(target_widget, 'setPlainText'):
                display = (self.apply_invisible_replacements(target)
                           if hasattr(self, 'apply_invisible_replacements') else target)
                target_widget.blockSignals(True)
                target_widget.setPlainText(display)
                target_widget.blockSignals(False)
            # Status column (4) is refreshed via its dedicated helper.
            self._update_status_cell(row, segment)

        self.project_modified = True
        self.update_window_title()
        if hasattr(self, 'update_progress_stats'):
            self.update_progress_stats()
        return True

    # ── Segment split / merge (Trados / memoQ style) ──────────────────────
    def _segment_for_grid_row(self, row: int):
        """Return (segment, index_in_list) for a grid row, via the id in col 0.

        Returns (None, -1) when the row or id can't be resolved. Using the id
        column (rather than the row index) keeps this correct under pagination.
        """
        if not getattr(self, 'current_project', None):
            return None, -1
        try:
            id_item = self.table.item(row, 0)
            sid = int(id_item.text())
        except (AttributeError, ValueError, TypeError):
            return None, -1
        for i, s in enumerate(self.current_project.segments):
            if s.id == sid:
                return s, i
        return None, -1

    def _split_segment_at_row(self, row: int, offset: int):
        """Split the segment shown in grid ``row`` at source character ``offset``."""
        from modules import segment_split_merge as ssm
        proj = getattr(self, 'current_project', None)
        if proj is None or not ssm.structure_editable(proj):
            return
        # Only in document order — the live list must equal document order so the
        # _original_segment_order sync (and thus saving) stays correct.
        if getattr(self, 'current_sort', None) is not None:
            return
        seg, idx = self._segment_for_grid_row(row)
        if seg is None:
            return
        try:
            offset = int(offset)
        except (TypeError, ValueError):
            return
        # can_split requires the offset to fall strictly inside the source.
        if not ssm.can_split(seg, offset):
            return

        import copy
        before = copy.deepcopy(proj.segments)
        ssm.split_segment(proj.segments, idx, offset)
        ssm.renumber_ids(proj.segments)
        focus_id = proj.segments[idx].id  # keep focus on the (left) original part
        self._push_structural_undo(before, proj.segments, focus_id, "split", row)
        self._sync_after_structural()
        # Fast incremental grid update when the grid row maps 1:1 to the list
        # index (no pagination offset); otherwise fall back to a full reload.
        if idx == row:
            try:
                self._split_segment_grid_fast(row, proj.segments[idx], proj.segments[idx + 1])
                self._select_grid_row_by_id(focus_id)
            except Exception as e:
                self.log(f"Fast split refresh failed ({e}); full reload.")
                self.load_segments_to_grid()
                self._select_grid_row_by_id(focus_id)
        else:
            self.load_segments_to_grid()
            self._select_grid_row_by_id(focus_id)
        self.log(f"Split segment into two at character {offset}.")

    def _merge_segment_at_row(self, row: int):
        """Merge the segment shown in grid ``row`` with the next segment."""
        from modules import segment_split_merge as ssm
        proj = getattr(self, 'current_project', None)
        if proj is None or not ssm.structure_editable(proj):
            return
        if getattr(self, 'current_sort', None) is not None:
            return
        seg, idx = self._segment_for_grid_row(row)
        if seg is None:
            return
        ok, reason = ssm.can_merge(proj.segments, idx)
        if not ok:
            QMessageBox.information(self, self.tr("Merge segments"), reason)
            return

        import copy
        before = copy.deepcopy(proj.segments)
        ssm.merge_with_next(proj.segments, idx)
        ssm.renumber_ids(proj.segments)
        focus_id = proj.segments[idx].id
        self._push_structural_undo(before, proj.segments, focus_id, "merge", row)
        self._sync_after_structural()
        if idx == row:
            try:
                self._merge_segment_grid_fast(row, proj.segments[idx])
                self._select_grid_row_by_id(focus_id)
            except Exception as e:
                self.log(f"Fast merge refresh failed ({e}); full reload.")
                self.load_segments_to_grid()
                self._select_grid_row_by_id(focus_id)
        else:
            self.load_segments_to_grid()
            self._select_grid_row_by_id(focus_id)
        self.log("Merged segment with the next one.")

    def _delete_segments_at_rows(self, rows):
        """Delete the segments shown in the given grid ``rows``.

        Only available for paste / plain-text / Start-Empty projects (see
        segment_split_merge.deletable) — deleting from a DOCX / Okapi / bilingual
        project would leave a gap in the merged-back export. Undoable via the
        same structural-undo stack as split/merge.
        """
        from modules import segment_split_merge as ssm
        proj = getattr(self, 'current_project', None)
        if proj is None or not ssm.deletable(proj):
            return
        if getattr(self, 'current_sort', None) is not None:
            QMessageBox.information(self, self.tr("Delete segments"),
                self.tr("Switch to Document Order (Sort menu) to delete segments."))
            return
        # Map grid rows → segment indices (dedup, drop locked segments).
        idxs = set()
        for r in rows or []:
            seg, idx = self._segment_for_grid_row(r)
            if seg is not None and not getattr(seg, 'locked', False):
                idxs.add(idx)
        if not idxs:
            return
        n = len(idxs)
        resp = QMessageBox.question(
            self, self.tr("Delete segments"),
            f"Delete {n} segment{'s' if n != 1 else ''}? You can undo this (Ctrl+Z).",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if resp != QMessageBox.StandardButton.Yes:
            return
        import copy
        before = copy.deepcopy(proj.segments)
        first = min(idxs)
        removed = ssm.delete_segments(proj.segments, idxs)
        if not removed:
            return
        ssm.renumber_ids(proj.segments)
        # Focus the segment now sitting where the first deleted one was.
        focus_id = None
        if proj.segments:
            focus_id = proj.segments[min(first, len(proj.segments) - 1)].id
        self._push_structural_undo(before, proj.segments, focus_id, "delete",
                                   min(rows) if rows else 0)
        self._sync_after_structural()
        self.load_segments_to_grid()
        if focus_id is not None:
            self._select_grid_row_by_id(focus_id)
        self.log(f"Deleted {removed} segment{'s' if removed != 1 else ''}.")

    def add_source_text_to_project(self):
        """Append pasted source text to the open project as new segments (issue #173).

        Allowed on exactly the projects where segments may be deleted
        (segment_split_merge.deletable): pasted / plain-text / Start-Empty ones,
        whose export is rebuilt from the segments. A DOCX, Okapi or bilingual
        project has no place in its source file for new text. Undoable via the
        structural-undo stack, like split / merge / delete."""
        from modules import segment_split_merge as ssm
        proj = getattr(self, 'current_project', None)
        if proj is None:
            QMessageBox.information(self, self.tr("Add Source Text"),
                                    self.tr("Open or create a project first."))
            return
        if not ssm.deletable(proj):
            QMessageBox.information(
                self, self.tr("Add Source Text"),
                self.tr("Adding source text is only available for pasted / plain-text / "
                        "Start-Empty projects. A DOCX, Okapi or bilingual-CAT project is "
                        "exported back into its original file, which has no place for "
                        "new text."))
            return
        if getattr(self, 'current_sort', None) is not None:
            QMessageBox.information(self, self.tr("Add Source Text"),
                self.tr("Switch to Document Order (Sort menu) to add source text."))
            return

        dialog = QDialog(self)
        dialog.setWindowTitle(self.tr("Add Source Text"))
        dialog.setMinimumSize(560, 360)
        v = QVBoxLayout(dialog)
        info = QLabel(self.tr(
            "Paste or type the source text to add. It is split into sentences, the "
            "same way as when a project is created, and added as new segments at "
            "the end of the project. Each line becomes its own paragraph."))
        info.setWordWrap(True)
        v.addWidget(info)
        editor = QPlainTextEdit()
        editor.setPlaceholderText(self.tr("Paste here (Ctrl+V)…"))
        v.addWidget(editor, 1)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText(self.tr("Add Segments"))
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        v.addWidget(buttons)
        editor.setFocus()
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        is_markdown = (getattr(proj, 'original_txt_path', None) or '').lower().endswith('.md')
        segmenter = self._make_sentence_segmenter(markdown=is_markdown)
        pairs = ssm.split_source_text(editor.toPlainText(), segmenter.segment_text)
        if not pairs:
            return

        import copy
        segs = proj.segments
        before = copy.deepcopy(segs)
        next_id = (segs[-1].id + 1) if segs else 1
        para_base = max((s.paragraph_id or 0 for s in segs), default=0)
        pos_base = max((s.document_position or 0 for s in segs), default=0)
        file_id = segs[-1].file_id if segs else None
        first_row = len(segs)
        for n, (paragraph, sentence) in enumerate(pairs):
            segs.append(Segment(
                id=next_id + n, source=sentence, target="", type="para",
                paragraph_id=para_base + paragraph,
                document_position=pos_base + paragraph,
                file_id=file_id,
            ))
        focus_id = next_id
        self._push_structural_undo(before, segs, focus_id, "add", first_row)
        self._sync_after_structural()
        self.load_segments_to_grid()
        self._select_grid_row_by_id(focus_id)
        self.log(f"➕ Added {len(pairs)} segment{'s' if len(pairs) != 1 else ''} "
                 "from pasted source text.")

    def delete_current_segments(self):
        """Delete the currently selected grid rows (Edit menu entry). Acts on the
        selection, or the current row when nothing is multi-selected."""
        proj = getattr(self, 'current_project', None)
        from modules import segment_split_merge as ssm
        if proj is None or not proj.segments:
            return
        if not ssm.deletable(proj):
            QMessageBox.information(
                self, self.tr("Delete segments"),
                self.tr("Deleting segments is only available for pasted / "
                        "plain-text / Start-Empty projects. Deleting from a DOCX, "
                        "Okapi or bilingual-CAT project would leave a gap in the "
                        "exported file."))
            return
        rows = []
        if hasattr(self, 'table'):
            rows = sorted({it.row() for it in self.table.selectedItems()})
        if not rows:
            cur = self.table.currentRow() if hasattr(self, 'table') else -1
            if cur >= 0:
                rows = [cur]
        if rows:
            self._delete_segments_at_rows(rows)

    # ── Keyboard-shortcut entry points (Ctrl+Alt+S / Ctrl+Alt+M) ──────────
    def split_current_segment(self):
        """Split the current segment at the caret in its Source cell.

        Bound to Ctrl+Alt+S. Uses the source cell's caret (place the cursor in
        the source where you want the split). A no-op if the caret isn't inside
        the source text, or while tags are compacted / outer tags hidden (the
        caret can't be mapped to the real source then)."""
        from PyQt6.QtWidgets import QApplication
        fw = QApplication.focusWidget()
        src_editor = None
        row = -1
        if isinstance(fw, ReadOnlyGridTextEditor) and getattr(fw, 'row', -1) >= 0:
            src_editor, row = fw, fw.row
        else:
            row = self.table.currentRow()
            if row >= 0:
                w = self.table.cellWidget(row, 2)
                if isinstance(w, ReadOnlyGridTextEditor):
                    src_editor = w
        if src_editor is None or row < 0:
            return
        # The caret→source mapping is only well-defined without compact/hidden tags.
        if getattr(src_editor, '_compact_tag_map', None) or getattr(src_editor, '_stripped_outer_tag', None):
            self.log("Split: switch to full tag view (no compact / hidden tags) to split here.")
            return
        caret = src_editor.textCursor().position()
        prefix = src_editor.toPlainText()[:caret]
        if hasattr(self, 'reverse_invisible_replacements'):
            prefix = self.reverse_invisible_replacements(prefix)
        self._split_segment_at_row(row, len(prefix))

    def merge_current_segment(self):
        """Merge the current segment with the next one (Ctrl+Alt+M)."""
        from PyQt6.QtWidgets import QApplication
        fw = QApplication.focusWidget()
        if isinstance(fw, (ReadOnlyGridTextEditor, EditableGridTextEditor)) and getattr(fw, 'row', -1) >= 0:
            row = fw.row
        else:
            row = self.table.currentRow()
        if row is not None and row >= 0:
            self._merge_segment_at_row(row)

    def _push_structural_undo(self, before, after, focus_id, op, row):
        """Record a reversible structural edit (split / merge) as before/after
        snapshots of the whole segment list. ``op`` ('split'/'merge') and ``row``
        let undo/redo apply the same fast incremental grid update."""
        import copy
        self.undo_stack.append({
            "type": "structural",
            "op": op,
            "row": row,
            "before": before,                 # already a snapshot from the caller
            "after": copy.deepcopy(after),     # snapshot the post-edit list
            "focus_id": focus_id,
        })
        if len(self.undo_stack) > self.max_undo_levels:
            self.undo_stack.pop(0)
        self.redo_stack.clear()
        self.update_undo_redo_actions()
        self.project_modified = True

    def _sync_after_structural(self):
        """Lightweight bookkeeping shared by the fast split/merge paths.

        Keeps _original_segment_order in sync (the save path reads from it — a
        stale copy is what caused the split-segment data loss), marks the project
        modified, refreshes stats, and updates the preview (cheap when unchanged).
        Does NOT rebuild the grid — the caller does a targeted update.
        """
        if getattr(self, 'current_project', None) is not None:
            self._original_segment_order = self.current_project.segments.copy()
        self.project_modified = True
        self.update_window_title()
        if hasattr(self, 'update_progress_stats'):
            self.update_progress_stats()
        try:
            self.refresh_preview()
        except Exception:
            pass

    def _recompute_list_numbers(self):
        """Rebuild self._list_numbers (row-index → ordered-list number) after a
        structural change, mirroring the pre-pass in load_segments_to_grid."""
        import re
        list_counter = 0
        last_was_list = False
        list_numbers = {}
        for idx, segment in enumerate(self.current_project.segments):
            source_text = (segment.source or "").strip()
            is_list_item = source_text.startswith(('<li-o>', '<li-b>', '<li>'))
            is_ordered = source_text.startswith('<li-o>') or (
                source_text.startswith('<li>') and not source_text.startswith('<li-b>'))
            if is_list_item and is_ordered:
                m = re.match(r'^<li(?:-o)?>\s*(\d+)[.)\s]', source_text)
                if m:
                    list_counter = int(m.group(1))
                    list_numbers[idx] = list_counter
                    last_was_list = True
                elif last_was_list:
                    list_counter += 1
                    list_numbers[idx] = list_counter
                else:
                    list_counter = 1
                    list_numbers[idx] = list_counter
                    last_was_list = True
            else:
                last_was_list = False
                list_counter = 0
        self._list_numbers = list_numbers

    def _reindex_grid_rows_from(self, start_row: int):
        """After an insert/remove, fix the # cell text and cell-widget .row for
        every row from start_row onward (grid row == list index here), and
        recompute the populated-rows set. Cheap: no widget recreation."""
        segs = self.current_project.segments
        n = self.table.rowCount()
        for r in range(start_row, n):
            if r >= len(segs):
                break
            seg = segs[r]
            id_item = self.table.item(r, 0)
            if id_item is None:
                id_item = QTableWidgetItem()
                id_item.setFlags(id_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                id_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.table.setItem(r, 0, id_item)
            id_item.setText(str(seg.id))
            for col in (2, 3, 4, 5):
                w = self.table.cellWidget(r, col)
                if w is not None and hasattr(w, 'row'):
                    w.row = r
        self._populated_rows = {
            r for r in range(n) if self.table.cellWidget(r, 2) is not None
        }

    def _select_grid_row_by_id(self, focus_id):
        """Select and scroll to the grid row holding the segment with focus_id,
        and move keyboard focus to the table.

        Focusing the table matters after a structural edit: if focus stayed in a
        cell's QTextEdit, that widget would swallow the next Ctrl+Z (its own text
        undo) instead of letting the app-level structural undo fire."""
        if focus_id is None:
            return
        r = self._find_row_for_segment(focus_id)
        if r >= 0:
            self.table.selectRow(r)
            self.table.setCurrentCell(r, 2)
            it = self.table.item(r, 0)
            if it is not None:
                self.table.scrollToItem(it)
        # Defer the focus move: when invoked from the right-click context menu,
        # the menu restores focus to the source cell as it closes — which would
        # override an immediate setFocus and let that cell swallow the next
        # Ctrl+Z. Running on the next event-loop tick lands focus on the table
        # after the menu has finished closing, so Ctrl+Z reaches structural undo.
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(0, self.table.setFocus)

    def _split_segment_grid_fast(self, row: int, left_seg, right_seg):
        """Incrementally update the grid for a split: insert one row, repopulate
        the two affected rows, reindex the rest. Raises on any problem so the
        caller can fall back to a full reload."""
        # Suppress target-change handlers while (re)building cell editors, exactly
        # as load_segments_to_grid does — otherwise the new target editors emit
        # textChanged and record a spurious undo entry on top of the structural
        # one (which made Ctrl+Z need two presses to actually rejoin/split).
        _prev_suppress = getattr(self, '_suppress_target_change_handlers', False)
        self._suppress_target_change_handlers = True
        try:
            self._recompute_list_numbers()
            self.table.insertRow(row + 1)
            # Sanity: the grid must hold exactly one row per segment (no pagination
            # slicing). If not, bail so the caller does a correct full reload.
            if self.table.rowCount() != len(self.current_project.segments):
                raise RuntimeError("grid/segment row-count mismatch")
            self._populate_single_row(row, left_seg)
            self._populate_single_row(row + 1, right_seg)
            self._reindex_grid_rows_from(row + 2)
            self._auto_resize_single_row(row)
            self._auto_resize_single_row(row + 1)
            for r, seg in ((row, left_seg), (row + 1, right_seg)):
                try:
                    self._apply_comment_anchors_to_cell(r, seg)
                except Exception:
                    pass
        finally:
            self._suppress_target_change_handlers = _prev_suppress

    def _merge_segment_grid_fast(self, row: int, merged_seg):
        """Incrementally update the grid for a merge: repopulate the merged row,
        remove the next row, reindex the rest. Raises on any problem so the
        caller can fall back to a full reload."""
        _prev_suppress = getattr(self, '_suppress_target_change_handlers', False)
        self._suppress_target_change_handlers = True
        try:
            self._recompute_list_numbers()
            self._populate_single_row(row, merged_seg)
            self.table.removeRow(row + 1)
            if self.table.rowCount() != len(self.current_project.segments):
                raise RuntimeError("grid/segment row-count mismatch")
            self._reindex_grid_rows_from(row + 1)
            self._auto_resize_single_row(row)
            try:
                self._apply_comment_anchors_to_cell(row, merged_seg)
            except Exception:
                pass
        finally:
            self._suppress_target_change_handlers = _prev_suppress

    def _apply_structural_history(self, action, redo: bool):
        """Undo or redo a structural (split/merge) edit.

        Restores the appropriate full-list snapshot (data correctness is never
        in doubt), then applies the *same* fast incremental grid delta the
        original edit used — so undo/redo are as quick as the edit, not a full
        rebuild. Falls back to a full reload on any mismatch.
        """
        import copy
        if not getattr(self, 'current_project', None):
            return
        snap = action["after"] if redo else action["before"]
        self.current_project.segments = copy.deepcopy(snap)
        # Keep the save-order list in sync (the data-loss fix) on every history step.
        self._original_segment_order = self.current_project.segments.copy()
        self.project_modified = True

        op = action.get("op")
        row = action.get("row")
        focus_id = action.get("focus_id")
        segs = self.current_project.segments

        did_fast = False
        if (op in ("split", "merge") and isinstance(row, int)
                and getattr(self, "current_sort", None) is None):
            # After restoring the snapshot, is the segment at `row` currently in
            # its split (two rows) or merged (one row) form?
            split_state = (op == "split" and redo) or (op == "merge" and not redo)
            try:
                if split_state and row + 1 < len(segs):
                    # Grid currently shows ONE row here; expand to two.
                    self._split_segment_grid_fast(row, segs[row], segs[row + 1])
                    did_fast = True
                elif not split_state:
                    # Grid currently shows TWO rows here; collapse to one.
                    self._merge_segment_grid_fast(row, segs[row])
                    did_fast = True
            except Exception as e:
                self.log(f"Fast undo/redo refresh failed ({e}); full reload.")
                did_fast = False

        if not did_fast:
            self.load_segments_to_grid()

        self._select_grid_row_by_id(focus_id)
        if hasattr(self, 'update_progress_stats'):
            self.update_progress_stats()
        self.update_window_title()
        try:
            self.refresh_preview()
        except Exception:
            pass

    def update_undo_redo_actions(self):
        """Update enabled/disabled state of undo/redo menu actions"""
        self.undo_action.setEnabled(len(self.undo_stack) > 0)
        self.redo_action.setEnabled(len(self.redo_stack) > 0)

    def get_selected_segments_from_grid(self):
        """Get list of selected segments from grid view"""
        if not self.current_project or not hasattr(self, 'table'):
            return []
        
        selected_rows = set()
        for item in self.table.selectedItems():
            selected_rows.add(item.row())
        
        segments = []
        for row in sorted(selected_rows):
            if 0 <= row < len(self.current_project.segments):
                segments.append(self.current_project.segments[row])
        
        return segments

    def _get_selected_or_filtered_segments(self, operation_name: str) -> list:
        """Get segments for bulk operation: selected rows first, then filtered rows as fallback.

        Args:
            operation_name: Human-readable name for the confirmation dialog (e.g. "Confirm Segments")

        Returns:
            List of Segment objects, or empty list if user cancels or nothing available.
        """
        selected = self.get_selected_segments_from_grid()
        if selected:
            return selected

        # No selection – check for filtered (visible) rows
        visible_rows = [row for row in range(self.table.rowCount())
                        if not self.table.isRowHidden(row)]
        total_rows = self.table.rowCount()

        if len(visible_rows) < total_rows and len(visible_rows) > 0:
            reply = QMessageBox.question(
                self, operation_name,
                f"No segments selected.\n\n"
                f"Apply '{operation_name}' to all {len(visible_rows)} filtered (visible) segments?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if reply == QMessageBox.StandardButton.Yes:
                return [self.current_project.segments[row] for row in visible_rows
                        if row < len(self.current_project.segments)]
            return []

        QMessageBox.information(self, "No Selection",
            "Please select one or more segments, or apply a filter first.")
        return []

    def add_segment_actions_to_menu(self, menu, selected_segments, clicked_row=None):
        """Append the per-segment actions (confirm, status, lock, AutoTagger,
        clear) to <menu>.

        Shared by BOTH context menus – the one on the row columns and the one
        inside the target editor. They used to be entirely separate, so these
        actions existed only if you happened to right-click the number/status/
        source column; anyone working in the target cell (i.e. most of the time)
        never saw them. Building both from here keeps them identical by
        construction rather than by discipline.
        """
        if not selected_segments:
            return

        n = len(selected_segments)

        confirm_action = menu.addAction(f"✅ Confirm {n} Segment(s)")
        confirm_action.setToolTip(f"Confirm {n} selected segment(s)")
        confirm_action.triggered.connect(self.confirm_selected_segments)

        # Change Status submenu
        status_menu = menu.addMenu(f"🏷️ Change Status ({n})")
        # Common user-settable statuses (excluding TM-specific ones like pm, cm, tm_100, etc.)
        user_statuses = [
            ("not_started", "❌ Not started"),
            ("pretranslated", "🤖 Pre-translated"),
            ("draft", "✏️ Draft"),
            ("confirmed", "✔ Confirmed"),
            ("proofread", "🟪 Proofread"),
            ("approved", "⭐ Approved"),
            ("rejected", "🚫 Rejected"),
        ]
        for status_key, label in user_statuses:
            action = status_menu.addAction(label)
            action.triggered.connect(lambda checked, s=status_key: self.change_status_selected(s))

        # Lock / Unlock toggle
        locked_count = sum(1 for seg in selected_segments if getattr(seg, 'locked', False))
        unlocked_count = n - locked_count
        if locked_count and unlocked_count:
            # Mixed: offer both
            lock_action = menu.addAction(f"🔒 Lock {unlocked_count} Segment(s)")
            lock_action.triggered.connect(lambda: self._set_locked_on_selected(selected_segments, True))
            unlock_action = menu.addAction(f"🔓 Unlock {locked_count} Segment(s)")
            unlock_action.triggered.connect(lambda: self._set_locked_on_selected(selected_segments, False))
        elif locked_count:
            unlock_action = menu.addAction(f"🔓 Unlock {locked_count} Segment(s)")
            unlock_action.triggered.connect(lambda: self._set_locked_on_selected(selected_segments, False))
        else:
            lock_action = menu.addAction(f"🔒 Lock {n} Segment(s)")
            lock_action.triggered.connect(lambda: self._set_locked_on_selected(selected_segments, True))

        menu.addSeparator()

        # AutoTagger – only offered for segments it can actually act on (source
        # has inline tags AND there is a translation to place them into), so the
        # item never appears just to explain why it can't run.
        taggable = [s for s in selected_segments
                    if autotag_extract_tags(s.source) and (s.target or "").strip()]
        if taggable:
            if n == 1:
                autotag_action = menu.addAction(
                    f"🏷️ AutoTagger – place tags ({format_shortcut_for_display('Ctrl+Alt+G')})")
                autotag_action.setToolTip(
                    "Insert the source segment's inline tags into this translation. "
                    "The wording is never changed.")
                # The right-clicked row isn't necessarily the current row, and
                # autotag_current_segment() works on the current row - so point
                # it at the row under the cursor before running.
                autotag_action.triggered.connect(
                    lambda checked=False, r=clicked_row: self._autotag_row_from_context(r))
            else:
                autotag_action = menu.addAction(
                    f"🏷️ AutoTagger – place tags in {len(taggable)} segment(s)")
                autotag_action.setToolTip(
                    "Re-place each segment's source tags into its translation "
                    "(one AI call per segment). Wording is never changed.")
                autotag_action.triggered.connect(self.autotag_segments_bulk)

        menu.addSeparator()

        # Clear translations action
        clear_action = menu.addAction("🗑️ Clear Translations")
        clear_action.setToolTip(f"Clear translations for {n} selected segment(s)")
        clear_action.triggered.connect(lambda: self.clear_selected_translations(selected_segments, 'grid'))

        menu.addSeparator()

        # "Segments" is in the label on purpose: inside a text cell Ctrl+A means
        # "select all text", so a bare "Select All" would be ambiguous exactly
        # where this menu is most often opened.
        select_all_action = menu.addAction(
            f"📋 Select All Segments ({format_shortcut_for_display('Ctrl+A')})")
        select_all_action.setToolTip("Select every segment in the grid")
        select_all_action.triggered.connect(lambda: self.table.selectAll())

    def show_grid_context_menu(self, position):
        """Show context menu for grid view with bulk operations"""
        selected_segments = self.get_selected_segments_from_grid()

        if not selected_segments:
            return

        clicked_row = self.table.indexAt(position).row()

        # The #, Type and Status columns have no text of their own, so this
        # menu used to be a stripped-down variant — meaning which actions you
        # were offered depended on which column you happened to right-click.
        # Delegate to the source cell's menu instead: it carries the same
        # segment actions PLUS the comment / QuickLauncher / termbase items,
        # so every column now offers one identical menu.
        try:
            from PyQt6.QtGui import QContextMenuEvent
            src_widget = self.table.cellWidget(clicked_row, 2)
            if isinstance(src_widget, ReadOnlyGridTextEditor):
                global_pos = self.table.viewport().mapToGlobal(position)
                src_widget.contextMenuEvent(QContextMenuEvent(
                    QContextMenuEvent.Reason.Mouse,
                    src_widget.mapFromGlobal(global_pos),
                    global_pos))
                return
        except Exception:
            pass   # fall through to the plain menu below

        menu = QMenu(self)
        self.add_segment_actions_to_menu(menu, selected_segments, clicked_row)
        menu.exec(self.table.viewport().mapToGlobal(position))

    def _autotag_row_from_context(self, row: int):
        """Run the single-segment AutoTagger on the row the user right-clicked.

        Right-clicking a cell does not move Qt's current index, but
        autotag_current_segment() reads self.table.currentRow() - so without
        this the menu item would silently tag whichever row happened to be
        current, not the one under the cursor."""
        if row is None or row < 0:
            return
        self.table.setCurrentCell(row, self.table.currentColumn() if self.table.currentColumn() >= 0 else 0)
        self.autotag_current_segment()

    def _set_locked_on_selected(self, segments, locked: bool):
        """Lock or unlock selected segments."""
        changed = 0
        for seg in segments:
            if getattr(seg, 'locked', False) != locked:
                seg.locked = locked
                changed += 1
        if changed:
            self.project_modified = True
            self.load_segments_to_grid()
            self.update_window_title()
            action = "Locked" if locked else "Unlocked"
            self.log(f"{'🔒' if locked else '🔓'} {action} {changed} segment{'s' if changed != 1 else ''}")

    def clear_selected_translations(self, segments, view_type='grid'):
        """Clear translations for selected segments"""
        if not segments:
            return
        
        count = len(segments)
        cleared_count = 0
        undo_entries = []  # v1.10.204: batched undo for Clear Translations

        for segment in segments:
            if segment.target:  # Only clear if there's something to clear
                old_target = segment.target
                old_status = segment.status
                segment.target = ""
                if segment.status != 'untranslated':
                    segment.status = 'untranslated'
                undo_entries.append((segment.id, old_target, "", old_status, segment.status))
                cleared_count += 1

        # v1.10.204: one batched undo record for the whole Clear Translations action
        if undo_entries:
            try:
                self.record_undo_states_batch(undo_entries)
            except Exception as undo_err:
                self.log(f"⚠ Undo recording failed for clear translations: {undo_err}")

        if cleared_count > 0:
            self.project_modified = True
            self.update_window_title()
            
            # Refresh the appropriate view
            if view_type == 'grid':
                self.load_segments_to_grid()
            elif view_type == 'list':
                self.refresh_list_view()
            
            self.log(f"✓ Cleared translations for {cleared_count} segment(s)")
        else:
            self.log(f"ℹ No translations to clear for selected segments")

    def clear_selected_translations_from_menu(self):
        """Clear translations for selected/filtered segments (called from Edit menu)"""
        # Only available in Grid view
        if self.current_project and hasattr(self, 'table') and self.table:
            segments = self._get_selected_or_filtered_segments("Clear Translations")
            if segments:
                self.clear_selected_translations(segments, 'grid')
        else:
            QMessageBox.information(self, "Not Available", "Please load a project first.")

    def copy_source_to_target_bulk(self):
        """Copy source text to target for selected or filtered segments (Edit > Bulk Operations)"""
        if not self.current_project or not hasattr(self, 'table') or not self.table:
            QMessageBox.information(self, "Not Available", "Please load a project first.")
            return

        selected_segments = self._get_selected_or_filtered_segments("Copy Source to Target")
        if not selected_segments:
            return
        
        # Confirm operation
        count = len(selected_segments)
        if count > 1:
            reply = QMessageBox.question(
                self,
                "Confirm Copy",
                f"Copy source to target for {count} segment(s)?\n\nThis will overwrite existing translations.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
        
        # Perform the copy
        copied_count = 0
        undo_entries = []  # v1.10.204: batched undo for bulk Copy Source
        for segment in selected_segments:
            row = self._find_row_for_segment(segment.id)
            if row >= 0:
                # v1.9.306: Use segment.source (clean text) instead of source widget's display text
                # which may contain invisible markers (·, →, °, ↵, \u200B)
                source_text = segment.source

                old_target = segment.target
                old_status = segment.status

                # Update segment object
                segment.target = source_text

                # Update target widget with display-formatted text
                target_widget = self.table.cellWidget(row, 3)
                if target_widget:
                    target_display = self.apply_invisible_replacements(source_text) if hasattr(self, 'apply_invisible_replacements') else source_text
                    target_widget.blockSignals(True)
                    target_widget.setPlainText(target_display)
                    target_widget.blockSignals(False)

                if old_target != source_text:
                    undo_entries.append((segment.id, old_target, source_text, old_status, old_status))
                copied_count += 1

        # v1.10.204: one batched undo record for the whole bulk Copy
        if undo_entries:
            try:
                self.record_undo_states_batch(undo_entries)
            except Exception as undo_err:
                self.log(f"⚠ Undo recording failed for bulk copy: {undo_err}")

        # Auto-resize rows to fit new content
        self.auto_resize_rows()
        self.update_progress_stats()

        self.log(f"✓ Copied source to target for {copied_count} segment(s)")
        QMessageBox.information(self, "Copy Complete", f"Copied source to target for {copied_count} segment(s).")

    def copy_source_to_target_non_translatable_bulk(self):
        """Copy source to target for number/code-only segments (Edit > Bulk Operations).

        Segments that contain no alphabetic characters (only digits, punctuation,
        symbols) are considered non-translatable. Their source is copied verbatim
        into an empty target and the status is set to 'draft'.

        Only acts on segments with an empty target to avoid overwriting existing work.
        """
        if not self.current_project or not hasattr(self, 'table') or not self.table:
            QMessageBox.information(self, "Not Available", "Please load a project first.")
            return

        selected_segments = self._get_selected_or_filtered_segments("Copy Source to Target (Non-Translatable)")
        if not selected_segments:
            return

        # Identify qualifying segments: non-translatable source AND empty target
        qualifying = [
            seg for seg in selected_segments
            if self._is_non_translatable(seg.source) and not seg.target.strip()
        ]

        total_selected = len(selected_segments)
        count = len(qualifying)

        if count == 0:
            QMessageBox.information(
                self,
                "No Qualifying Segments",
                f"None of the {total_selected} selected/filtered segment(s) qualify.\n\n"
                "Qualifying segments must contain only numbers, codes, or punctuation "
                "(no letters) and have an empty target.",
            )
            return

        reply = QMessageBox.question(
            self,
            "Confirm Copy",
            f"Found {count} non-translatable segment(s) out of {total_selected} selected/filtered.\n\n"
            "Copy source to target and mark as Draft?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        source_lang = getattr(self.current_project, 'source_language', None)
        target_lang = getattr(self.current_project, 'target_language', None)

        copied_count = 0
        undo_entries = []  # v1.10.204: batched undo for non-translatable bulk copy
        for segment in qualifying:
            row = self._find_row_for_segment(segment.id)
            if row < 0:
                continue

            transformed = self._transform_non_translatable(segment.source, source_lang, target_lang)
            old_target = segment.target
            old_status = segment.status

            # Update segment data
            segment.target = transformed
            segment.status = 'draft'

            # Update target widget
            target_widget = self.table.cellWidget(row, 3)
            if target_widget:
                target_display = (
                    self.apply_invisible_replacements(transformed)
                    if hasattr(self, 'apply_invisible_replacements')
                    else transformed
                )
                target_widget.blockSignals(True)
                target_widget.setPlainText(target_display)
                target_widget.blockSignals(False)

            # Refresh status icon
            self._refresh_segment_status(segment)

            if old_target != transformed or old_status != 'draft':
                undo_entries.append((segment.id, old_target, transformed, old_status, 'draft'))
            copied_count += 1

        # v1.10.204: one batched undo record for the whole non-translatable bulk copy
        if undo_entries:
            try:
                self.record_undo_states_batch(undo_entries)
            except Exception as undo_err:
                self.log(f"⚠ Undo recording failed for non-translatable bulk copy: {undo_err}")

        self.auto_resize_rows()
        self.update_progress_stats()
        if hasattr(self, '_mark_project_modified'):
            self._mark_project_modified()

        self.log(f"✓ Copied source to target (non-translatable) for {copied_count} segment(s)")
        QMessageBox.information(
            self, "Copy Complete",
            f"Copied source to target for {copied_count} non-translatable segment(s)."
        )

    def pseudo_translate_bulk(self):
        """Fill targets with pseudo-translation for a pre-flight export test.

        Thin delegator — the dialog and the apply/undo logic live in
        modules/pseudo_translate_dialog.py to keep this file lean. See
        run_pseudo_translation() there.
        """
        if not self.current_project or not getattr(self.current_project, 'segments', None):
            QMessageBox.information(self, "Not Available", "Please load a project first.")
            return
        run_pseudo_translation(self)

    def _copy_source_to_target_selected(self, selected_segments):
        """Copy source to target for the given selected segments (keyboard shortcut path).

        Called from EditableGridTextEditor when Ctrl+Shift+S is pressed with
        multiple segments selected.  Unlike the menu bulk version, this skips
        the confirmation dialog for a snappy keyboard-driven workflow.
        """
        copied_count = 0
        undo_entries = []  # v1.10.204: batched undo for multi-select Ctrl+Shift+S
        for segment in selected_segments:
            row = self._find_row_for_segment(segment.id)
            if row >= 0:
                # v1.9.306: Use segment.source (clean text) instead of source widget's display text
                # which may contain invisible markers (·, →, °, ↵, \u200B)
                source_text = segment.source
                old_target = segment.target
                old_status = segment.status

                # Update segment object
                segment.target = source_text

                # Update target widget with display-formatted text
                target_widget = self.table.cellWidget(row, 3)
                if target_widget:
                    target_display = self.apply_invisible_replacements(source_text) if hasattr(self, 'apply_invisible_replacements') else source_text
                    target_widget.blockSignals(True)
                    target_widget.setPlainText(target_display)
                    target_widget.blockSignals(False)

                if old_target != source_text:
                    undo_entries.append((segment.id, old_target, source_text, old_status, old_status))
                copied_count += 1

        # v1.10.204: batched undo recording for multi-select Ctrl+Shift+S
        if undo_entries:
            try:
                self.record_undo_states_batch(undo_entries)
            except Exception as undo_err:
                self.log(f"⚠ Undo recording failed for multi-select copy: {undo_err}")

        if copied_count:
            self.auto_resize_rows()
            self.update_progress_stats()
            self.project_modified = True
            self.update_window_title()

        self.log(f"📋 Copied source to target for {copied_count} segment(s)")

    def show_clean_tags_dialog(self):
        """Show dialog to clean formatting tags from project segments"""
        if not self.current_project or not self.current_project.segments:
            QMessageBox.warning(self, "No Project", "Please load a project with segments first.")
            return
        
        # Import dialog class from TMX editor module
        try:
            from modules.tmx_editor_qt import TmxTagCleanerDialog
        except ImportError:
            QMessageBox.warning(self, "Module Error", "Could not load tag cleaner dialog.")
            return
        
        segment_count = len(self.current_project.segments)
        dialog = TmxTagCleanerDialog(self, tu_count=segment_count)
        dialog.setWindowTitle(self.tr("🧹 Clean Tags from Project"))
        
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.clean_project_tags(dialog)

    def clean_project_tags(self, dialog):
        """Clean tags from project segments based on dialog settings"""
        import re
        
        # Get selected tag patterns
        patterns = []
        for name, cb in dialog.tag_checkboxes.items():
            if cb.isChecked():
                patterns.append(cb.tag_pattern)
        
        if not patterns:
            QMessageBox.information(self, "No Tags Selected", "Please select at least one tag type to clean.")
            return
        
        # Get replacement string
        replacement = " " if dialog.replace_space.isChecked() else ""
        
        # Get scope
        clean_source = dialog.scope_both.isChecked() or dialog.scope_source.isChecked()
        clean_target = dialog.scope_both.isChecked() or dialog.scope_target.isChecked()
        
        # Create patterns for both literal and XML-escaped versions
        # Files may store tags as literal <b> or escaped &lt;b&gt;
        expanded_patterns = []
        for p in patterns:
            expanded_patterns.append(p)  # Original pattern for literal tags
            # Create escaped version: < becomes &lt; and > becomes &gt;
            escaped_p = p.replace('<', '&lt;').replace('>', '&gt;')
            if escaped_p != p:
                expanded_patterns.append(escaped_p)
        
        # Compile combined pattern
        combined_pattern = "|".join(f"({p})" for p in expanded_patterns)
        regex = re.compile(combined_pattern)
        
        # Process segments
        modified_count = 0
        tags_removed = 0
        
        progress = QProgressDialog("Cleaning tags...", "Cancel", 0, len(self.current_project.segments), self)
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)
        
        for i, segment in enumerate(self.current_project.segments):
            if progress.wasCanceled():
                break
            
            progress.setValue(i)
            QApplication.processEvents()
            
            segment_modified = False
            
            if clean_source and segment.source:
                new_source, count = regex.subn(replacement, segment.source)
                if count > 0:
                    segment.source = new_source.strip() if replacement == "" else new_source
                    tags_removed += count
                    segment_modified = True
            
            if clean_target and segment.target:
                new_target, count = regex.subn(replacement, segment.target)
                if count > 0:
                    segment.target = new_target.strip() if replacement == "" else new_target
                    tags_removed += count
                    segment_modified = True
            
            if segment_modified:
                modified_count += 1
        
        progress.setValue(len(self.current_project.segments))
        
        # Refresh grid
        if modified_count > 0:
            self.project_modified = True
            self.load_segments_to_grid()
            self.update_window_title()
        
        # Show results
        scope_text = "both source and target" if (clean_source and clean_target) else ("source" if clean_source else "target")
        QMessageBox.information(
            self, "Tag Cleaning Complete",
            f"✅ Cleaned {modified_count:,} segment(s)\n"
            f"🏷️ Removed {tags_removed:,} tag(s)\n"
            f"📋 Scope: {scope_text}"
        )
        
        self.log(f"🧹 Tag cleaning: {modified_count} segments modified, {tags_removed} tags removed")
