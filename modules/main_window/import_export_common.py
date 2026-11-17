"""Helpers that every import and export uses: where exports go, segments in document order,
import options, re-importing the same file, tidying up after an import, and
checking the language pair a file declares.

Part of the main window (SupervertalerQt), moved here unchanged. The
methods run as the main window's own: `self` is the SupervertalerQt.
"""

import os
import re
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)
from modules.language_combos import _import_language_pairs, _select_lang_in_combo
from modules.okapi_import_options import DEFAULT_IMPORT_OPTIONS, IMPORT_OPTION_GROUPS
from modules.project_assets import ensure_target_dir, is_in_source_dir
from modules.styled_widgets import CheckmarkCheckBox
from pathlib import Path
from typing import Any, Dict


class ImportExportCommonMixin:
    def _export_dir_beside(self, source_path) -> Path:
        """Where a round-trip export is offered by default: next to the file it
        came from – or in the project's target/ folder when that file is the
        copy in source/ (#228), which is no place for a translation."""
        source_path = Path(source_path)
        project_path = getattr(self, 'project_file_path', None)
        if project_path:
            project_dir = os.path.dirname(os.path.abspath(project_path))
            if is_in_source_dir(str(source_path), project_dir):
                try:
                    return Path(ensure_target_dir(project_dir))
                except Exception:
                    pass
        return source_path.parent

    def _segments_in_document_order(self):
        """The project's segments in document order, however the grid is sorted.

        Sorting the grid reorders ``current_project.segments`` itself, so an
        export that reads that list after a sort by source text wrote the
        segments in that order – and the round-trip exports, which pair the
        n-th segment with the n-th unit of the original file, put every
        translation into the wrong unit. The segment id is the document
        position; Document Order sorts by it as well.
        """
        segments = list(self.current_project.segments) if self.current_project else []
        try:
            return sorted(segments, key=lambda seg: int(seg.id))
        except (TypeError, ValueError):
            return list(getattr(self, '_original_segment_order', None) or segments)

    def _project_export_path(self, filename):
        """Default save path for a generated translation: the project's target/
        folder (project-folder model, #228).

        Falls back to the bare filename — so Qt opens the last-used directory —
        when the project hasn't been saved yet (no folder exists to write into).
        """
        project_path = getattr(self, 'project_file_path', None)
        if project_path:
            try:
                return os.path.join(
                    ensure_target_dir(os.path.dirname(project_path)), filename)
            except Exception as exc:
                self.log(f"⚠ Could not prepare the project's target/ folder: {exc}")
        return filename

    def _finalise_import_with_indexes(self):
        """v1.10.153: shared post-import wiring for every import path.

        Calls ``_start_termbase_batch_worker()`` (which builds the in-memory
        termbase index — without it ``find_termbase_matches_in_source``
        falls through to per-word SQL queries, ~2-34 s per cell click on a
        45-termbase database) and ``_start_prefetch_worker(first 50)``
        (which pre-warms the TM/MT/LLM match cache so the first cell-clicks
        land on cached results instead of cold lookups). Mirrors the
        wiring that ``import_docx_from_path`` has had since v1.9.423 but
        that was never replicated to the other ~13 import paths. Both
        calls are wrapped in try/except so a worker failure can't roll
        back the import — the grid is already on screen by the time we
        get here.
        """
        try:
            self._start_termbase_batch_worker()
        except Exception as _e:
            self.log(f"⚠ Failed to start termbase batch worker after import: {_e}")
        try:
            if (self.current_project
                    and len(self.current_project.segments) > 0):
                prefetch_ids = [
                    seg.id
                    for seg in self.current_project.segments[:50]
                ]
                self._start_prefetch_worker(prefetch_ids)
        except Exception as _e:
            self.log(f"⚠ Failed to start prefetch worker after import: {_e}")
        # Rebuild the all-comments list so any comments carried in by the
        # import (e.g. Word comments imported as comments) show up without
        # needing a project reload.
        try:
            self._refresh_segment_comments_list()
            self._refresh_proofreading_comments_list()
        except Exception as _e:
            self.log(f"⚠ Failed to refresh comments list after import: {_e}")

    def _normalize_language_code(self, lang_code: str) -> str:
        """Resolve an ISO code / variant to the full English language name.

        Delegates to modules.language_codes (the single authority). Falls
        back to the original input when the language is not recognised."""
        from modules import language_codes as _lc
        return _lc.english_name(lang_code) or lang_code

    @staticmethod
    def _is_known_language(value) -> bool:
        """True when `value` resolves to a language the canonical table knows.

        Guards against placeholders like 'unknown' (which base_code() turns
        into 'un') and empty values reaching a project's language pair.
        """
        from modules import language_codes as _lc
        return bool(_lc.iso_to_english_name(_lc.base_code(value)))

    @staticmethod
    def _lang_from_column_header(header_text: str):
        """Resolve a bilingual-table column header to a base ISO code, or None.

        memoQ writes whatever its UI language calls the column: "Italian",
        "Italian (Italy)", "IT", "it-IT", sometimes with extra words around it.
        Everything recognisable is routed through modules.language_codes (the
        single normalisation authority) rather than a local name list, so the
        26 hardcoded English names this used to accept no longer limit us.

        Returns None when nothing recognisable is present. That matters: the
        old code silently fell back to English -> Dutch, and a project created
        with the wrong pair finds zero TM matches while the TM still looks
        perfectly healthy in TM Edit/Maintain. A None here makes the caller
        ask instead of guess.
        """
        from modules import language_codes as _lc

        text = (header_text or "").strip()
        if not text:
            return None

        def _valid(code):
            # base_code() will happily turn "Source" into "so"; only accept a
            # code the canonical table actually knows.
            return code if (code and _lc.iso_to_english_name(code)) else None

        # Whole cell first ("Italian (Italy)", "it-IT", "IT").
        found = _valid(_lc.base_code(text))
        if found:
            return found

        # Then any single word in it ("Target: Italian", "Italian text").
        for word in re.split(r"[^A-Za-z\-]+", text):
            if len(word) < 2:
                continue
            found = _valid(_lc.base_code(word))
            if found:
                return found
        return None

    def _language_detection_message(self, source_lang, target_lang):
        """Rich-text callout for an import language dialog: what was
        auto-detected from the file, if anything (issue #200). Same wording as
        the Phrase import."""
        import html as _html
        from modules import language_codes as _lc
        src = _lc.english_name(source_lang) if source_lang else None
        tgt = _lc.english_name(target_lang) if target_lang else None
        src = _html.escape(src) if src else None
        tgt = _html.escape(tgt) if tgt else None
        if src and tgt:
            return self.tr("Auto-detected from file: <b>{}</b> → <b>{}</b>.<br>"
                           "Confirm or change below.").format(src, tgt)
        if src or tgt:
            found = []
            if src:
                found.append(self.tr("source = <b>{}</b>").format(src))
            if tgt:
                found.append(self.tr("target = <b>{}</b>").format(tgt))
            return self.tr("Auto-detected from file: {}.<br>"
                           "Please pick the remaining side and confirm.").format(", ".join(found))
        return self.tr("Couldn't auto-detect the language pair – please pick "
                       "the source and target language for this project:")

    def _confirm_language_pair(self, source_lang, target_lang, context="", detected=False):
        """Let the user confirm/correct a document's language pair.

        Shown when an importer could not read the pair off the file, or – with
        ``detected=True`` – to confirm a pair that was read heuristically, pre-
        filled with whatever was found (issue #200; a side may be None). Returns
        (source_code, target_code), or None if the user cancelled.
        """
        # PyQt6, like the rest of the app. This used to import PySide6, which
        # isn't installed, so the prompt could never open (issue #200).
        from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QFormLayout, QComboBox,
                                     QDialogButtonBox, QLabel)

        dlg = QDialog(self)
        dlg.setWindowTitle(self.tr("Confirm language pair"))
        layout = QVBoxLayout(dlg)
        if detected and (source_lang or target_lang):
            msg = QLabel(self._language_detection_message(source_lang, target_lang))
            msg.setTextFormat(Qt.TextFormat.RichText)
        else:
            msg = QLabel(self.tr(
                "Supervertaler could not read the language pair from this file.\n\n"
                "Please set it now. If this is wrong, the TM is searched for the "
                "wrong languages and no matches are ever shown, even though the TM "
                "itself looks fine in TM Edit/Maintain."))
        msg.setWordWrap(True)
        layout.addWidget(msg)
        if context:
            hint = QLabel(context)
            hint.setWordWrap(True)
            layout.addWidget(hint)

        form = QFormLayout()
        src_combo, tgt_combo = QComboBox(), QComboBox()
        for name, code in _import_language_pairs():
            src_combo.addItem(name, code)
            tgt_combo.addItem(name, code)
        _select_lang_in_combo(src_combo, source_lang or "en")
        _select_lang_in_combo(tgt_combo, target_lang or "nl")
        form.addRow(self.tr("Source language:"), src_combo)
        form.addRow(self.tr("Target language:"), tgt_combo)
        layout.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        layout.addWidget(buttons)

        if dlg.exec() != QDialog.DialogCode.Accepted:
            return None
        return (src_combo.currentData() or src_combo.currentText(),
                tgt_combo.currentData() or tgt_combo.currentText())

    def get_effective_import_options(self) -> Dict[str, Any]:
        """Return the active Okapi file-type import options.

        Starts from DEFAULT_IMPORT_OPTIONS and overlays the user's saved
        defaults (general_settings['import_options']). The import-time
        dialog may further override these per import via
        self._import_options.
        """
        def _coerce(k, v):
            # Preserve string-valued options (e.g. word_comments_mode);
            # everything else is a boolean toggle.
            return str(v) if isinstance(DEFAULT_IMPORT_OPTIONS[k], str) else bool(v)

        opts = dict(DEFAULT_IMPORT_OPTIONS)
        try:
            saved = (self._load_general_settings_from_file() or {}).get('import_options') or {}
            opts.update({k: _coerce(k, v) for k, v in saved.items() if k in DEFAULT_IMPORT_OPTIONS})
        except Exception:
            pass
        # Per-import override set by the Import Options dialog, if any
        override = getattr(self, '_import_options', None)
        if isinstance(override, dict):
            opts.update({k: _coerce(k, v) for k, v in override.items() if k in DEFAULT_IMPORT_OPTIONS})
        return opts

    def _build_import_options_widget(self, current_opts, formats=None):
        """Build a checkbox widget for the Okapi file-type import options.

        Returns (container_widget, checks) where checks maps option key →
        QCheckBox. `formats` limits which format groups are shown (e.g.
        ['word']); None shows all. Used by the Settings panel and the
        per-import override in the import dialogs.
        """
        from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QGroupBox,
                                     QGridLayout, QLabel, QComboBox)
        from PyQt6.QtCore import Qt
        container = QWidget()
        grid = QGridLayout(container)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(8)
        checks = {}
        boxes = []
        for fmt_key, group_title, items in IMPORT_OPTION_GROUPS:
            if formats is not None and fmt_key not in formats:
                continue
            gb = QGroupBox(group_title)
            gl = QVBoxLayout(gb)
            gl.setSpacing(2)
            for key, label, tip in items:
                if key == "word_comments_mode":
                    # Three-way control: Skip / As comments / As segments.
                    row = QWidget()
                    rl = QHBoxLayout(row)
                    rl.setContentsMargins(0, 0, 0, 0)
                    lbl = QLabel(label + ":")
                    combo = QComboBox()
                    combo.addItem("Skip", "off")
                    combo.addItem("Import as comments", "comments")
                    combo.addItem("Import as translatable text", "segments")
                    cur = str(current_opts.get(key, DEFAULT_IMPORT_OPTIONS[key]))
                    combo.setCurrentIndex(max(0, combo.findData(cur)))
                    if tip:
                        lbl.setToolTip(tip)
                        combo.setToolTip(tip)
                    rl.addWidget(lbl)
                    rl.addWidget(combo)
                    rl.addStretch()
                    gl.addWidget(row)
                    checks[key] = combo
                    continue
                cb = CheckmarkCheckBox(label)
                cb.setChecked(bool(current_opts.get(key, DEFAULT_IMPORT_OPTIONS[key])))
                if tip:
                    cb.setToolTip(tip)
                gl.addWidget(cb)
                checks[key] = cb
            boxes.append(gb)

        # Two-column layout to use horizontal space instead of one tall
        # column: the first (largest) group fills the left column; the rest
        # stack in the right column. With all three formats this puts Word on
        # the left and Excel above PowerPoint on the right.
        if boxes:
            right = boxes[1:]
            grid.addWidget(boxes[0], 0, 0, max(len(right), 1), 1,
                           Qt.AlignmentFlag.AlignTop)
            for r, gb in enumerate(right):
                grid.addWidget(gb, r, 1, Qt.AlignmentFlag.AlignTop)
            grid.setColumnStretch(0, 1)
            grid.setColumnStretch(1, 1)
            grid.setRowStretch(max(len(right), 1), 1)
        return container, checks

    def _read_import_option_widgets(self, checks) -> Dict[str, Any]:
        """Read current values from the widgets returned by
        _build_import_options_widget. Handles both checkboxes (bool) and the
        word_comments_mode combobox (str)."""
        from PyQt6.QtWidgets import QComboBox
        out = {}
        for key, w in checks.items():
            if isinstance(w, QComboBox):
                out[key] = w.currentData()
            else:
                out[key] = w.isChecked()
        return out

    def _check_reimport_same_file(self, file_path: str, source_path_attrs: list) -> tuple:
        """Check if importing a file that's already loaded in the current project.

        Args:
            file_path: Path to the file being imported
            source_path_attrs: List of attribute names to check (e.g., ['memoq_source_path', 'current_document_path'])

        Returns:
            Tuple of (reimport_mode, preserved_project_id, preserved_tm_settings, preserved_termbase_settings)
            If user cancels, returns (None, None, None, None) to signal cancellation.
        """
        if not self.current_project:
            return (False, None, None, None)

        # Check all possible source path attributes
        current_source = None
        for attr in source_path_attrs:
            if attr.startswith('current_project.'):
                # Check attribute on project object
                proj_attr = attr.replace('current_project.', '')
                current_source = getattr(self.current_project, proj_attr, None)
            else:
                # Check attribute on self
                current_source = getattr(self, attr, None)
            if current_source:
                break

        if not current_source:
            return (False, None, None, None)

        # Normalize paths for comparison
        if os.path.normpath(file_path) != os.path.normpath(current_source):
            return (False, None, None, None)

        # Same file - ask user what they want to do
        dialog = QMessageBox(self)
        dialog.setWindowTitle(self.tr("Re-import Document"))
        dialog.setText(f"This file is already loaded in the current project:\n\n{os.path.basename(file_path)}")
        dialog.setInformativeText("Do you want to re-import it into the current project (keeping TM and termbase settings), or create a new project?")

        reimport_btn = dialog.addButton("Re-import into Current Project", QMessageBox.ButtonRole.AcceptRole)
        new_proj_btn = dialog.addButton("Create New Project", QMessageBox.ButtonRole.ActionRole)
        cancel_btn = dialog.addButton(QMessageBox.StandardButton.Cancel)

        dialog.exec()

        clicked = dialog.clickedButton()
        if clicked == cancel_btn:
            return (None, None, None, None)  # Signal cancellation
        elif clicked == reimport_btn:
            # Preserve project ID and settings
            preserved_id = self.current_project.id
            preserved_tm = getattr(self.current_project, 'tm_settings', None)
            preserved_tb = getattr(self.current_project, 'termbase_settings', None)
            self.log(f"📋 Re-importing into current project (preserving TM settings)")
            return (True, preserved_id, preserved_tm, preserved_tb)
        else:
            # Create new project
            return (False, None, None, None)

    def _apply_reimport_settings(self, project, reimport_mode: bool, preserved_id, preserved_tm, preserved_tb):
        """Apply preserved settings to project if in reimport mode.

        Args:
            project: The Project object to modify
            reimport_mode: Whether this is a re-import
            preserved_id: The project ID to preserve
            preserved_tm: The tm_settings to preserve
            preserved_tb: The termbase_settings to preserve
        """
        if reimport_mode and preserved_id is not None:
            project.id = preserved_id
            if preserved_tm:
                project.tm_settings = preserved_tm
            if preserved_tb:
                project.termbase_settings = preserved_tb
            self.log(f"✓ Preserved project ID: {preserved_id}")

    def _clear_caches_after_import(self):
        """Clear translation and termbase caches after import."""
        with self.translation_matches_cache_lock:
            self.translation_matches_cache.clear()
        self.termbase_cache.clear()

    def _enable_tag_view_after_import(self):
        """Auto-enable Tag View after importing a document with formatting tags"""
        if hasattr(self, 'tags_btn'):
            self._set_tag_view_mode('tags')
            self.log("🏷️ Tag View auto-enabled (formatting tags detected in import)")
