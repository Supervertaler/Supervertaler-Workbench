"""Phrase (Memsource): MXLIFF and bilingual DOCX, import and export.

Part of the main window (SupervertalerQt), moved here unchanged. The
methods run as the main window's own: `self` is the SupervertalerQt.
"""

import re
import traceback
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QVBoxLayout,
)
from modules.main_dialogs import _ImportProgressDialog
from modules.segment_model import Project, Segment
from modules.statuses import DEFAULT_STATUS, STATUSES
from pathlib import Path


class PhraseMixin:
    def import_phrase_mxliff(self):
        """Import a Phrase (Memsource) MXLIFF file."""
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            self.tr("Select Phrase MXLIFF File"),
            "",
            self.tr("Phrase MXLIFF (*.mxliff);;All Files (*.*)")
        )
        if not file_path:
            return

        reimport_mode, preserved_id, preserved_tm, preserved_tb = self._check_reimport_same_file(
            file_path, ['current_project.mxliff_source_path']
        )
        if reimport_mode is None:  # User cancelled
            return

        from modules.mxliff_handler import MxliffDocument, MxliffError
        # About 1.5 s for 20,000 segments: show that something is happening.
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            doc, error = MxliffDocument.load(file_path), None
        except MxliffError as e:
            doc, error = None, e
        finally:
            QApplication.restoreOverrideCursor()
        if error is not None:
            e = error
            self.log(f"✗ Phrase MXLIFF import failed: {e}")
            QMessageBox.critical(self, self.tr("Import Error"),
                                 self.tr("Could not import the Phrase MXLIFF file:\n\n{err}").format(err=str(e)))
            return

        try:
            # The id is the unit's position: the export pairs the n-th segment
            # with the n-th unit, and checks the source text before writing.
            segments = [
                Segment(
                    id=i + 1,
                    source=unit.source,
                    target=unit.target,
                    status=unit.status if unit.status in STATUSES else DEFAULT_STATUS.key,
                    match_percent=unit.match_percent,
                    locked=unit.locked,
                    notes="",
                )
                for i, unit in enumerate(doc.units)
            ]

            source_lang = self._normalize_language_code(doc.source_lang)
            target_lang = self._normalize_language_code(doc.target_lang)
            if not self._is_known_language(source_lang) or not self._is_known_language(target_lang):
                chosen = self._confirm_language_pair(
                    source_lang if self._is_known_language(source_lang) else None,
                    target_lang if self._is_known_language(target_lang) else None,
                    context=f"File declares: {doc.source_lang} → {doc.target_lang}",
                )
                if not chosen:
                    self.log("✗ User cancelled Phrase MXLIFF import at the language prompt")
                    return
                source_lang, target_lang = chosen

            self.current_project = Project(
                name=Path(file_path).stem,
                segments=segments,
                source_lang=source_lang,
                target_lang=target_lang
            )
            self._apply_reimport_settings(self.current_project, reimport_mode, preserved_id, preserved_tm, preserved_tb)
            self.current_project.mxliff_source_path = file_path
            self.mxliff_source_file = file_path

            self.source_language = source_lang
            self.target_language = target_lang
            self.project_file_path = None
            self.project_modified = True
            self._original_segment_order = self.current_project.segments.copy()
            self.update_window_title()

            _n_segs = len(segments)
            with _ImportProgressDialog(
                self, title="Importing Phrase MXLIFF",
                initial_label=f"Loading {_n_segs:,} segments into grid…",
                initial_total=max(_n_segs, 1),
            ) as _prog:
                self.load_segments_to_grid(progress_callback=_prog.grid_callback)
            self.initialize_tm_database()
            self._clear_caches_after_import()
            if not reimport_mode:
                self._deactivate_all_resources_for_new_project()
            self.auto_resize_rows()
            self._initialize_spellcheck_for_target_language(target_lang)

            translated = sum(1 for u in doc.units if u.target.strip())
            confirmed = sum(1 for u in doc.units if u.confirmed)
            locked = sum(1 for u in doc.units if u.locked)
            self.log(f"✓ Imported {_n_segs} segments from Phrase MXLIFF: {Path(file_path).name}")
            self.log(f"  Source: {source_lang}, Target: {target_lang}; "
                     f"{translated} with a translation, {confirmed} confirmed, {locked} locked")
            for w in doc.warnings:
                self.log(f"  ⚠️ {w}")

            msg = self.tr(
                "Imported {n} segment(s) from Phrase MXLIFF.\n\n"
                "Languages: {src} → {tgt}\n"
                "With a translation: {tr}\nConfirmed: {conf}\nLocked: {lock}\n\n"
                "Export with Project > Export > Phrase (Memsource) > MXLIFF to upload "
                "the translation back to Phrase."
            ).format(n=_n_segs, src=source_lang, tgt=target_lang, tr=translated,
                     conf=confirmed, lock=locked)
            if doc.warnings:
                msg += "\n\n" + "\n\n".join(doc.warnings)

            self._finalise_import_with_indexes()
            QMessageBox.information(self, self.tr("Import Successful"), msg)
        except Exception as e:
            self.log(f"❌ Error importing Phrase MXLIFF: {e}")
            QMessageBox.critical(self, self.tr("Import Error"),
                                 self.tr("Error importing Phrase MXLIFF file:\n\n{err}").format(err=str(e)))

    def export_phrase_mxliff(self):
        """Write the translations back into the original Phrase MXLIFF."""
        if not self.current_project or not self.current_project.segments:
            QMessageBox.warning(self, self.tr("No Data"), self.tr("No segments to export"))
            return

        source = getattr(self, 'mxliff_source_file', None)
        if not source or not Path(source).exists():
            source = getattr(self.current_project, 'mxliff_source_path', None)
        if not source or not Path(source).exists():
            reply = QMessageBox.question(
                self, self.tr("Select Phrase MXLIFF Source File"),
                self.tr("To export to Phrase MXLIFF, please select the original .mxliff file "
                        "this project was imported from.\n\nWould you like to select it now?"),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            if reply != QMessageBox.StandardButton.Yes:
                self.log("Export cancelled")
                return
            source, _ = QFileDialog.getOpenFileName(
                self, self.tr("Select Original Phrase MXLIFF File"), "",
                self.tr("Phrase MXLIFF (*.mxliff);;All Files (*.*)"))
            if not source:
                self.log("Export cancelled - no source file selected")
                return
        self.mxliff_source_file = source

        output_path, _ = QFileDialog.getSaveFileName(
            self,
            self.tr("Save Translated Phrase MXLIFF"),
            self._project_export_path(Path(source).stem + "_translated.mxliff"),
            self.tr("Phrase MXLIFF (*.mxliff);;All Files (*.*)")
        )
        if not output_path:
            return

        from modules.mxliff_handler import MxliffDocument, MxliffError
        segments = self._segments_in_document_order()
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            # Read afresh every time: the file may have been replaced since the import.
            doc = MxliffDocument.load(source)
            changed = doc.save(output_path,
                               [seg.target for seg in segments],
                               [seg.status for seg in segments],
                               sources=[seg.source for seg in segments])
            error = None
        except (MxliffError, OSError) as e:
            error = e
        finally:
            QApplication.restoreOverrideCursor()
        if error is not None:
            e = error
            self.log(f"✗ Phrase MXLIFF export failed: {e}")
            QMessageBox.critical(self, self.tr("Export Error"),
                                 self.tr("Could not export the Phrase MXLIFF file:\n\n{err}\n\n"
                                         "Nothing was written.").format(err=str(e)))
            return
        self.log(f"✓ Exported Phrase MXLIFF: {changed} segment(s) with a new translation "
                 f"or confirmed flag → {Path(output_path).name}")
        QMessageBox.information(
            self, self.tr("Export Successful"),
            self.tr("Exported to Phrase MXLIFF: {n} segment(s) with a new translation or "
                    "confirmed flag.\n\nFile: {path}\n\nUpload this file to the job in "
                    "Phrase to bring the translation in.").format(n=changed, path=output_path)
        )

    def import_phrase_bilingual(self):
        """Import Phrase (Memsource) bilingual DOCX file"""
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Phrase Bilingual DOCX File",
            "",
            "Word Documents (*.docx);;All Files (*.*)"
        )

        if not file_path:
            return

        # Show confirmation dialog
        reply = QMessageBox.question(
            self,
            "Confirm Phrase Bilingual Import",
            f"You are about to import a Phrase (Memsource) bilingual DOCX file.\n\n"
            f"File: {Path(file_path).name}\n\n"
            f"This workflow is specifically for Phrase bilingual files with:\n"
            f"• Multiple tables with 7 columns\n"
            f"• Inline tags like {{1}}, {{1>text<1}}\n"
            f"• Editable target column (Column 5)\n\n"
            f"The project can be exported back to Phrase format after translation.\n\n"
            f"Continue with import?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes
        )

        if reply != QMessageBox.StandardButton.Yes:
            self.log("✗ User cancelled Phrase import")
            return

        try:
            from modules.phrase_docx_handler import PhraseDOCXHandler
            from modules.trados_docx_handler import detect_bilingual_docx_type

            # Check if this is a valid Phrase bilingual DOCX
            file_type = detect_bilingual_docx_type(file_path)
            if file_type != "phrase":
                QMessageBox.warning(
                    self, "Invalid Format",
                    "This file does not appear to be a Phrase bilingual DOCX.\n\n"
                    "Expected: a table with 7 or 8 columns whose first column "
                    "contains segment IDs of the form <id>:<index> "
                    "(e.g. SSOMDWjYi5xvD7wq_dc10:0)."
                )
                return

            # Load the file
            handler = PhraseDOCXHandler()
            if not handler.load(file_path):
                QMessageBox.critical(
                    self, "Error",
                    "Failed to load Phrase bilingual DOCX file."
                )
                return

            # Extract segments
            phrase_segments = handler.extract_source_segments()

            if not phrase_segments:
                QMessageBox.warning(
                    self, "No Segments",
                    "No segments found in the Phrase bilingual DOCX file."
                )
                return

            # Convert to internal Segment format - preserve tags for round-trip
            segments = []
            for i, p_seg in enumerate(phrase_segments):
                segment = Segment(
                    id=i + 1,
                    source=p_seg.source_text,  # Preserve tags like {1}text{1}
                    target=p_seg.target_text if p_seg.target_text else "",
                    status=STATUSES["pretranslated"].key if p_seg.target_text else DEFAULT_STATUS.key,
                    notes=f"Phrase ID: {p_seg.segment_id} | Status: {p_seg.status_code}",
                )
                segments.append(segment)

            # Store the handler and original path for round-trip export
            self.phrase_handler = handler
            self.phrase_source_file = file_path

            # v1.10.33: auto-detect the language pair from the file
            # before opening the dialog. Phrase always writes a column-
            # header table like ``Source (cs) | Target (de-de)`` and
            # also tags content runs with w:lang attributes, so the
            # information is right there in the file – no reason to
            # make the user pick manually. The handler returns ISO
            # codes (or ``None`` per side if detection failed); we
            # translate to the English names the picker uses.
            from modules.language_codes import iso_to_english_name
            try:
                detected_src_iso, detected_tgt_iso = handler.detect_language_pair()
            except Exception as e:
                print(f"[Phrase import] language detection error: {e}")
                detected_src_iso = detected_tgt_iso = None
            detected_src_name = iso_to_english_name(detected_src_iso)
            detected_tgt_name = iso_to_english_name(detected_tgt_iso)

            # Show language selection dialog – pre-filled with the
            # auto-detected pair when available, falling back to the
            # main UI's current selection if a side couldn't be
            # detected. The user always sees the dialog so they can
            # override a misdetection or pick a sub-variant.
            from PyQt6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel, QComboBox, QDialogButtonBox, QGroupBox

            lang_dialog = QDialog(self)
            lang_dialog.setWindowTitle(self.tr("Select Languages"))
            lang_dialog.setMinimumWidth(350)
            lang_layout = QVBoxLayout(lang_dialog)

            # Info text – call out auto-detection success so the user
            # knows the pre-fill came from the file rather than from
            # their current Workbench setting.
            if detected_src_name and detected_tgt_name:
                info_text = (
                    f"Auto-detected from file: "
                    f"<b>{detected_src_name}</b> → <b>{detected_tgt_name}</b>.<br>"
                    f"Confirm or change below."
                )
            elif detected_src_name or detected_tgt_name:
                # Partial detection. Tell the user which side was found.
                partial = []
                if detected_src_name:
                    partial.append(f"source = <b>{detected_src_name}</b>")
                if detected_tgt_name:
                    partial.append(f"target = <b>{detected_tgt_name}</b>")
                info_text = (
                    f"Auto-detected from file: {', '.join(partial)}.<br>"
                    f"Please pick the remaining side and confirm."
                )
            else:
                info_text = (
                    "Couldn't auto-detect the language pair – please pick "
                    "the source and target language for this project:"
                )
            info_label = QLabel(info_text)
            info_label.setTextFormat(Qt.TextFormat.RichText)
            info_label.setWordWrap(True)
            info_label.setStyleSheet("color: #666; margin-bottom: 10px;")
            lang_layout.addWidget(info_label)

            # Language group
            lang_group = QGroupBox(self.tr("Language Pair"))
            lang_group_layout = QVBoxLayout(lang_group)

            # Source language
            source_row = QHBoxLayout()
            source_row.addWidget(QLabel(self.tr("Source Language:")))
            source_combo = QComboBox()
            # The one language list (modules/language_codes.py), as every picker.
            from modules import language_codes as _lc
            available_languages = _lc.picker_names()
            source_combo.addItems(available_languages)
            # Prefer the auto-detected source; fall back to the main UI
            # current selection so the dialog still works on a file
            # that doesn't expose its language metadata.
            current_source = detected_src_name or (
                self.source_lang_combo.currentText()
                if hasattr(self, 'source_lang_combo') else "English"
            )
            source_idx = source_combo.findText(current_source)
            if source_idx >= 0:
                source_combo.setCurrentIndex(source_idx)
            source_row.addWidget(source_combo)
            lang_group_layout.addLayout(source_row)

            # Target language
            target_row = QHBoxLayout()
            target_row.addWidget(QLabel(self.tr("Target Language:")))
            target_combo = QComboBox()
            target_combo.addItems(available_languages)
            current_target = detected_tgt_name or (
                self.target_lang_combo.currentText()
                if hasattr(self, 'target_lang_combo') else "Dutch"
            )
            target_idx = target_combo.findText(current_target)
            if target_idx >= 0:
                target_combo.setCurrentIndex(target_idx)
            target_row.addWidget(target_combo)
            lang_group_layout.addLayout(target_row)

            lang_layout.addWidget(lang_group)

            # Buttons
            lang_buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
            lang_buttons.accepted.connect(lang_dialog.accept)
            lang_buttons.rejected.connect(lang_dialog.reject)
            lang_layout.addWidget(lang_buttons)

            if lang_dialog.exec() != QDialog.DialogCode.Accepted:
                self.log("✗ User cancelled Phrase import during language selection")
                return

            source_lang = source_combo.currentText()
            target_lang = target_combo.currentText()

            # Create new project with user-selected languages
            file_name = Path(file_path).stem
            self.current_project = Project(
                name=file_name,
                segments=segments,
                source_lang=source_lang,
                target_lang=target_lang
            )

            # Store Phrase source path in project for persistence across saves
            self.current_project.phrase_source_path = file_path

            # CRITICAL: Update _original_segment_order for new import
            self._original_segment_order = self.current_project.segments.copy()

            # Sync global language settings with imported project languages
            self.source_language = source_lang
            self.target_language = target_lang

            # Update UI
            self.project_file_path = None
            self.project_modified = True
            self.update_window_title()
            # v1.10.150: wrap the slow grid-load in the shared progress dialog so
            # the user sees activity instead of "Not Responding" on large files.
            # (Minimal wrap: subsequent fast finalise steps stay outside the dialog.)
            _n_segs = len(self.current_project.segments) if self.current_project else 0
            with _ImportProgressDialog(
                self, title="Importing Phrase bilingual DOCX",
                initial_label=f"Loading {_n_segs:,} segments into grid…",
                initial_total=max(_n_segs, 1),
            ) as _prog:
                self.load_segments_to_grid(progress_callback=_prog.grid_callback)
            self.initialize_tm_database()
            self._clear_caches_after_import()
            self._deactivate_all_resources_for_new_project()

            # Auto-resize rows for better initial display
            self.auto_resize_rows()

            # Initialize spellcheck for target language
            self._initialize_spellcheck_for_target_language(target_lang)

            # Count segments with tags
            tagged_count = sum(1 for s in phrase_segments if s.source_tags)

            # Log success
            self.log(f"✓ Imported {len(segments)} segments from Phrase bilingual DOCX: {Path(file_path).name}")
            if tagged_count:
                self.log(f"  {tagged_count} segments contain inline tags")

            # v1.10.153: build the termbase index and prefetch
            # TM/MT/LLM matches for the first 50 segments — without
            # this, cell-clicks fall through to per-word SQL queries
            # (2-34 s per click on a 40+ termbase database).
            self._finalise_import_with_indexes()
            QMessageBox.information(
                self, "Import Successful",
                f"Successfully imported {len(segments)} segment(s) from Phrase bilingual DOCX.\n\n"
                f"File: {Path(file_path).name}\n"
                f"Languages: {source_lang} → {target_lang}\n"
                f"Segments with tags: {tagged_count}\n\n"
                f"Note: Inline tags (e.g., {{1}}, {{1>text<1}}) are preserved for export.\n"
                f"Export back to Phrase format after translation to maintain compatibility."
            )

        except ImportError as e:
            QMessageBox.critical(
                self, "Missing Dependency",
                f"Required library is missing: {e}\n\n"
                "Install dependencies with: pip install python-docx"
            )
        except Exception as e:
            QMessageBox.critical(self, "Import Error", f"Failed to import Phrase bilingual DOCX:\n\n{str(e)}")
            self.log(f"✗ Phrase import failed: {str(e)}")
            import traceback
            traceback.print_exc()

    def export_phrase_bilingual(self):
        """Export translations back to Phrase bilingual DOCX format"""
        if not self.current_project or not self.current_project.segments:
            QMessageBox.warning(self, "No Project", "No project is currently loaded.")
            return

        # Check if we have a Phrase source - either from handler or project data
        phrase_source = None

        # First check if handler is already loaded
        if hasattr(self, 'phrase_handler') and self.phrase_handler:
            phrase_source = getattr(self, 'phrase_source_file', None)

        # If not, check if project has the source path saved
        if not phrase_source and hasattr(self.current_project, 'phrase_source_path') and self.current_project.phrase_source_path:
            phrase_source = self.current_project.phrase_source_path

        if not phrase_source:
            # Prompt user to select the original Phrase bilingual file
            reply = QMessageBox.question(
                self, "Select Phrase Source File",
                "To export to Phrase format, please select the original Phrase bilingual DOCX file.\n\n"
                "This is the file you originally imported from Phrase.\n\n"
                "Would you like to select it now?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )

            if reply == QMessageBox.StandardButton.Yes:
                file_path, _ = QFileDialog.getOpenFileName(
                    self,
                    "Select Original Phrase Bilingual DOCX",
                    "",
                    "Word Documents (*.docx);;All Files (*.*)"
                )

                if file_path:
                    phrase_source = file_path
                    self.phrase_source_file = file_path
                    # Also save to project for future exports
                    self.current_project.phrase_source_path = file_path
                    self.log(f"✓ Phrase source file set: {Path(file_path).name}")
                else:
                    self.log("Export cancelled - no source file selected")
                    return
            else:
                self.log("Export cancelled")
                return

        # Check if source file still exists
        if not Path(phrase_source).exists():
            # File not found - give user a chance to locate it
            reply = QMessageBox.question(
                self, "Source File Not Found",
                f"The original Phrase source file cannot be found:\n\n"
                f"{phrase_source}\n\n"
                f"Would you like to browse for the file in its new location?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )

            if reply == QMessageBox.StandardButton.Yes:
                file_path, _ = QFileDialog.getOpenFileName(
                    self,
                    "Select Original Phrase Bilingual DOCX",
                    "",
                    "Word Documents (*.docx);;All Files (*.*)"
                )

                if file_path:
                    phrase_source = file_path
                    self.phrase_source_file = file_path
                    self.current_project.phrase_source_path = file_path
                    self.log(f"✓ Phrase source file relocated: {Path(file_path).name}")
                else:
                    self.log("Export cancelled - no source file selected")
                    return
            else:
                self.log("Export cancelled")
                return

        # Reload the handler if needed
        if not hasattr(self, 'phrase_handler') or not self.phrase_handler:
            try:
                from modules.phrase_docx_handler import PhraseDOCXHandler
                self.phrase_handler = PhraseDOCXHandler()
                if not self.phrase_handler.load(phrase_source):
                    QMessageBox.critical(self, "Error", "Failed to reload the Phrase source file.")
                    return
                self.phrase_handler.extract_source_segments()  # Ensure segments are loaded
                self.phrase_source_file = phrase_source
                self.log(f"✓ Reloaded Phrase source file for export")
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed to reload Phrase source:\n\n{str(e)}")
                return

        # Suggest output filename
        source_path = Path(self.phrase_source_file)
        suggested_name = source_path.stem + "_translated" + source_path.suffix

        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Export Phrase Bilingual DOCX",
            str(self._export_dir_beside(source_path) / suggested_name),
            "Word Documents (*.docx);;All Files (*.*)"
        )

        if not file_path:
            return

        try:
            # Collect translations from the project's segment list, not
            # from grid cell widgets.
            #
            # v1.10.32 bug fix: prior code did
            #
            #     target_widget = self.table.cellWidget(row, 3)
            #     notes_widget  = self.table.cellWidget(row, 4)   # ← stale
            #     ...
            #     notes_text    = notes_widget.toPlainText()
            #
            # which crashed with
            #
            #     'QWidget' object has no attribute 'toPlainText'
            #
            # because the current grid layout has only five columns
            # (#, Type, Source, Target, Status) – column 4 is the
            # Status indicator (a plain ``QWidget`` badge, not a
            # ``SegmentTextEdit``). The old layout had a Notes column
            # at index 4; when that was retired, the Phrase export
            # path wasn't updated.
            #
            # The data we need is already on the segment objects: at
            # import time ``import_phrase_bilingual`` writes
            # ``notes=f"Phrase ID: {segment_id} | Status: {status}"``
            # to each segment, and the Target text lives on
            # ``segment.target``. Read straight from there –
            # bypasses the grid entirely and works even when the
            # editor isn't currently visible / built.
            translations = {}
            import re
            phrase_id_re = re.compile(r"Phrase ID:\s*([^\s|]+)")
            for segment in self.current_project.segments:
                notes_text = (segment.notes or "").strip()
                if "Phrase ID:" not in notes_text:
                    continue
                match = phrase_id_re.search(notes_text)
                if not match:
                    continue
                target_text = (segment.target or "").strip()
                if not target_text:
                    continue
                translations[match.group(1)] = target_text

            # Update the handler with translations
            updated = self.phrase_handler.update_target_segments(translations)

            # Save to new file
            if self.phrase_handler.save(file_path):
                self.log(f"✓ Exported {updated} translated segments to Phrase DOCX: {Path(file_path).name}")

                QMessageBox.information(
                    self, "Export Successful",
                    f"Successfully exported {updated} translation(s) to Phrase bilingual DOCX.\n\n"
                    f"File: {Path(file_path).name}\n\n"
                    f"This file can be imported back into Phrase (Memsource).\n"
                    f"Only Column 5 (target text) has been updated."
                )
            else:
                QMessageBox.critical(self, "Export Error", "Failed to save the Phrase bilingual DOCX file.")

        except Exception as e:
            QMessageBox.critical(self, "Export Error", f"Failed to export Phrase bilingual DOCX:\n\n{str(e)}")
            self.log(f"✗ Phrase export failed: {str(e)}")
            import traceback
            traceback.print_exc()
