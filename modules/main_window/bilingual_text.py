"""Importing bilingual Markdown and pasted bilingual text.

The Markdown export stays in Supervertaler.py, because it writes the
version number (__version__), which only that file defines.

Part of the main window (SupervertalerQt), moved here unchanged. The
methods run as the main window's own: `self` is the SupervertalerQt.
"""

import os
import traceback
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QTextEdit,
    QVBoxLayout,
)
from modules.main_dialogs import _ImportProgressDialog
from modules.statuses import STATUSES, get_status
from modules.styled_widgets import CheckmarkCheckBox


class BilingualTextMixin:
    def import_bilingual_markdown(self):
        """Re-import an edited bilingual Text (AI-friendly) file into the
        current project, matching segments via the .svexport.json sidecar."""
        try:
            if not self.current_project or not self.current_project.segments:
                QMessageBox.warning(
                    self, "No Project",
                    "Open the project you exported from first, then re-import the "
                    "edited text file.")
                return

            from modules.bilingual_markdown_handler import (
                parse_markdown, load_sidecar, looks_like_bracketed_markdown,
            )

            file_path, _ = QFileDialog.getOpenFileName(
                self, "Import Bilingual Text (AI-friendly)", "",
                "Text Files (*.txt *.md);;All Files (*.*)")
            if not file_path:
                return

            with open(file_path, 'r', encoding='utf-8') as f:
                md_text = f.read()

            if not looks_like_bracketed_markdown(md_text):
                QMessageBox.warning(
                    self, "Not a re-importable text file",
                    "This file doesn't contain any [SEGMENT N] markers.\n\n"
                    "Choose a file exported via Export → 🔁 Supervertaler "
                    "Re-importable → Bilingual Text (AI-friendly).")
                return

            parsed = parse_markdown(md_text)
            if not parsed:
                QMessageBox.warning(self, "No Segments",
                                    "No segments could be parsed from this file.")
                return

            sidecar = load_sidecar(file_path)
            if sidecar is None:
                # No sidecar: match by source text, as for pasted text. Matching
                # by [SEGMENT N] position alone wrote edits into the wrong
                # segments whenever the export had been filtered by status.
                self.log("ℹ No .svexport.json sidecar next to "
                         f"{os.path.basename(file_path)} – matching segments by source text")
                self._apply_bilingual_text_update(
                    parsed, origin=os.path.basename(file_path), match_by_source=True,
                    hint_sidecar=self._bilingual_text_session_hint())
                return

            self._apply_bilingual_text_update(
                parsed, sidecar=sidecar, origin=os.path.basename(file_path))

        except Exception as e:
            QMessageBox.critical(self, "Import Error",
                                 f"Failed to import bilingual text:\n\n{str(e)}")
            self.log(f"✗ Bilingual text import failed: {str(e)}")
            import traceback
            traceback.print_exc()

    def import_bilingual_text_from_paste(self):
        """Update the current project from AI-friendly bilingual text pasted
        into a dialog instead of read from a file (issue #248).

        There is no sidecar, so rows are matched by their source text (see
        match_rows_by_source); the last export/copy of this project in this
        session, if any, is used as a hint. Everything after that – preview,
        tag and lock guards, apply – is shared with the file import."""
        try:
            if not self.current_project or not self.current_project.segments:
                QMessageBox.warning(
                    self, "No Project",
                    "Open the project the text was exported from first, then "
                    "paste the edited text.")
                return

            from modules.bilingual_markdown_handler import (
                parse_markdown, looks_like_bracketed_markdown,
            )

            dialog = QDialog(self)
            dialog.setWindowTitle(self.tr("Update from Pasted Text"))
            dialog.setMinimumSize(640, 440)
            v = QVBoxLayout(dialog)
            info = QLabel(self.tr(
                "Paste the edited bilingual text (the <b>[SEGMENT NNNN]</b> format) "
                "below, for example an AI chat's reply. Segments are matched to this "
                "project by their source text, so the source lines must be left as "
                "they were."))
            info.setTextFormat(Qt.TextFormat.RichText)
            info.setWordWrap(True)
            v.addWidget(info)
            editor = QPlainTextEdit()
            editor.setPlaceholderText(self.tr("Paste here (Ctrl+V)…"))
            editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
            clip = QApplication.clipboard().text() or ""
            if looks_like_bracketed_markdown(clip):
                editor.setPlainText(clip)
            v.addWidget(editor, 1)
            buttons = QDialogButtonBox(
                QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
            buttons.button(QDialogButtonBox.StandardButton.Ok).setText(self.tr("Preview Changes..."))
            v.addWidget(buttons)

            def _accept():
                if not looks_like_bracketed_markdown(editor.toPlainText()):
                    QMessageBox.warning(
                        dialog, "No segments found",
                        "The text doesn't contain any [SEGMENT N] markers.\n\n"
                        "Paste text exported via Export → 🔁 Supervertaler "
                        "Re-importable → Bilingual Text (AI-friendly), or copied "
                        "with its Copy to Clipboard button.")
                    return
                dialog.accept()

            buttons.accepted.connect(_accept)
            buttons.rejected.connect(dialog.reject)
            editor.setFocus()
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return

            parsed = parse_markdown(editor.toPlainText())
            if not parsed:
                QMessageBox.warning(self, "No Segments",
                                    "No segments could be parsed from the pasted text.")
                return

            self._apply_bilingual_text_update(
                parsed, origin="pasted text", match_by_source=True,
                hint_sidecar=self._bilingual_text_session_hint())

        except Exception as e:
            QMessageBox.critical(self, "Import Error",
                                 f"Failed to import bilingual text:\n\n{str(e)}")
            self.log(f"✗ Bilingual text import from pasted text failed: {str(e)}")
            import traceback
            traceback.print_exc()

    def _bilingual_text_session_hint(self):
        """The sidecar of this project's last bilingual text export or copy in
        this session, or None. Only a hint: match_rows_by_source checks every
        entry against the live source before using it."""
        remembered = getattr(self, '_bilingual_text_session_sidecar', None)
        if remembered and remembered[0] == id(self.current_project):
            return remembered[1]
        return None

    def _apply_bilingual_text_update(self, parsed, *, sidecar=None, origin="",
                                     match_by_source=False, hint_sidecar=None):
        """Preview and apply parsed bilingual text to the current project.

        Shared by the file import (sidecar, or position fallback) and the
        pasted-text import (match_by_source=True)."""
        from modules.bilingual_markdown_handler import (
            build_import_diffs, match_rows_by_source, CurrentSeg,
            KIND_CHANGED, KIND_UNCHANGED, KIND_MISSING, KIND_SOURCE_MISMATCH,
            KIND_TAG_MISMATCH, KIND_LOCKED,
        )
        from modules.statuses import STATUSES, get_status

        # Build current snapshots (flush grid edits first).
        current_segments = list(self.current_project.segments)
        try:
            self._sync_grid_targets_to_segments(current_segments)
        except Exception:
            pass
        cur_list = []
        for idx, seg in enumerate(current_segments, 1):
            cur_list.append(CurrentSeg(
                id=seg.id, number=idx, source=seg.source or "",
                target=seg.target or "", status_key=get_status(seg.status).key,
                locked=bool(getattr(seg, 'locked', False)),
                comment=getattr(seg, 'notes', '') or "",
            ))

        # Status label → key (built from the canonical vocabulary).
        label_to_key = {defn.label.strip().lower(): key
                        for key, defn in STATUSES.items()}
        label_to_key.setdefault("not started", "not_started")

        row_matches = (match_rows_by_source(parsed, cur_list, hint_sidecar)
                       if match_by_source else None)

        def _compute(strict):
            return build_import_diffs(
                parsed, sidecar, cur_list,
                status_label_to_key=label_to_key, strict_tags=strict,
                row_matches=row_matches)

        # ── Preview dialog (with strict-tag toggle) ────────────────────
        dialog = QDialog(self)
        dialog.setWindowTitle(self.tr("Import Bilingual Text — Preview"))
        dialog.setMinimumSize(620, 460)
        v = QVBoxLayout(dialog)

        summary_label = QLabel()
        summary_label.setTextFormat(Qt.TextFormat.RichText)
        summary_label.setWordWrap(True)
        v.addWidget(summary_label)

        details = QTextEdit()
        details.setReadOnly(True)
        v.addWidget(details, 1)

        strict_cb = CheckmarkCheckBox(
            "Refuse to apply edits that drop required (structural) tags "
            "(recommended)")
        strict_cb.setChecked(True)
        v.addWidget(strict_cb)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText(self.tr("Apply changes"))
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        v.addWidget(buttons)

        state = {"diffs": []}

        def refresh():
            diffs = _compute(strict_cb.isChecked())
            state["diffs"] = diffs
            changed = [d for d in diffs if d.kind == KIND_CHANGED]
            unchanged = [d for d in diffs if d.kind == KIND_UNCHANGED]
            issues = [d for d in diffs if d.kind in (
                KIND_MISSING, KIND_SOURCE_MISMATCH, KIND_TAG_MISMATCH, KIND_LOCKED)]
            summary_label.setText(
                f"<b>{len(changed)}</b> segment(s) will be updated &nbsp;•&nbsp; "
                f"<b>{len(unchanged)}</b> unchanged &nbsp;•&nbsp; "
                f"<b>{len(issues)}</b> issue(s) skipped")
            lines = []
            kind_label = {
                KIND_MISSING: "MISSING", KIND_SOURCE_MISMATCH: "SOURCE CHANGED",
                KIND_TAG_MISMATCH: "TAG MISMATCH", KIND_LOCKED: "LOCKED",
            }
            for d in issues:
                lines.append(f"[SEGMENT {d.number:04d}] {kind_label.get(d.kind, d.kind)}"
                             f" — {d.note}")
            if changed:
                lines.append("")
                lines.append(f"— {len(changed)} segment(s) to update —")
                for d in changed[:200]:
                    parts = []
                    if d.new_target is not None:
                        preview = d.new_target.replace("\n", " ")
                        if len(preview) > 70:
                            preview = preview[:67] + "…"
                        parts.append(f"target → {preview}")
                    if d.new_comment is not None:
                        cprev = d.new_comment.replace("\n", " ")
                        if len(cprev) > 50:
                            cprev = cprev[:47] + "…"
                        parts.append(f"comment → {cprev}")
                    if d.new_status_key:
                        parts.append(f"status → {d.new_status_key}")
                    note = f"  ({d.note})" if d.note else ""
                    lines.append(f"[SEGMENT {d.number:04d}] " + "; ".join(parts) + note)
                if len(changed) > 200:
                    lines.append(f"… and {len(changed) - 200} more")
            details.setPlainText("\n".join(lines) if lines else "No changes detected.")

        strict_cb.toggled.connect(lambda _checked: refresh())
        refresh()

        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        final_diffs = state["diffs"]
        id_to_seg = {s.id: s for s in self.current_project.segments}
        applied = 0
        target_applied = 0
        comment_applied = 0
        for d in final_diffs:
            if d.kind != KIND_CHANGED:
                continue
            seg = id_to_seg.get(d.segment_id) if d.segment_id is not None else None
            if seg is None and 1 <= d.number <= len(self.current_project.segments):
                seg = self.current_project.segments[d.number - 1]
            if seg is None:
                continue
            touched = False
            # ``new_target`` is None when only the comment/status changed.
            if d.new_target is not None:
                seg.target = d.new_target
                target_applied += 1
                touched = True
            if d.new_comment is not None:
                # Proper API: rewrites seg.comments[] AND keeps seg.notes in
                # sync; an empty string clears the comments list.
                if hasattr(seg, 'replace_all_comments_with_text'):
                    seg.replace_all_comments_with_text(d.new_comment)
                else:
                    seg.notes = d.new_comment
                comment_applied += 1
                touched = True
            if d.new_status_key and d.new_status_key in STATUSES:
                seg.status = d.new_status_key
                touched = True
            if touched:
                applied += 1

        if applied == 0:
            QMessageBox.information(self, "Nothing to apply",
                                    "No segments were updated.")
            return

        # Refresh project/UI (same pattern as import_review_table).
        self.project_modified = True
        self.update_window_title()
        _n_segs = len(self.current_project.segments) if self.current_project else 0
        with _ImportProgressDialog(
            self, title="Importing bilingual text",
            initial_label=f"Loading {_n_segs:,} segments into grid…",
            initial_total=max(_n_segs, 1),
        ) as _prog:
            self.load_segments_to_grid(progress_callback=_prog.grid_callback)

        # v1.10.231: rebuild the Comments pane's all-comments list so newly
        # imported/edited comments show immediately (the grid reload above
        # doesn't touch it). Idempotent + cheap; guarded for early init.
        if comment_applied:
            try:
                self._refresh_segment_comments_list()
            except Exception:
                pass

        self.log(f"✓ Applied {applied} change(s) from bilingual text: "
                 f"{origin} "
                 f"({target_applied} target, {comment_applied} comment)")
        QMessageBox.information(
            self, "Import Complete",
            f"Updated {applied} segment(s) from:\n{origin}\n\n"
            f"• {target_applied} translation change(s)\n"
            f"• {comment_applied} comment change(s)")
