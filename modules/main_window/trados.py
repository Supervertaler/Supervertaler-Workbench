"""Trados: bilingual review DOCX, SDLPPX packages and return packages, and
SDLXLIFF files.

Part of the main window (SupervertalerQt), moved here unchanged. The
methods run as the main window's own: `self` is the SupervertalerQt.
"""

import traceback
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMessageBox,
    QVBoxLayout,
)
from modules.main_dialogs import _ImportProgressDialog
from modules.segment_model import Project, Segment
from modules.statuses import DEFAULT_STATUS, STATUSES
from modules.styled_widgets import CheckmarkCheckBox
from pathlib import Path


class TradosMixin:
    def import_trados_bilingual(self):
        """Import Trados Studio bilingual review DOCX file"""
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Trados Bilingual Review DOCX File",
            "",
            "Word Documents (*.docx);;All Files (*.*)"
        )
        
        if not file_path:
            return
        
        # Show important workflow dialog with critical preparation steps
        workflow_dialog = QMessageBox(self)
        workflow_dialog.setWindowTitle(self.tr("⚠️ Important: Trados Bilingual Review Workflow"))
        workflow_dialog.setIcon(QMessageBox.Icon.Warning)
        workflow_dialog.setText(
            "<b>The Trados bilingual review format requires specific preparation!</b><br><br>"
            "This format is designed for <b>review only</b>, not translation from scratch. "
            "For the round-trip to work correctly, you must prepare the file in Trados first."
        )
        workflow_dialog.setInformativeText(
            "<b>Required Preparation Steps in Trados Studio:</b><br><br>"
            "1. Import your document into Trados Studio<br>"
            "2. Select all segments: click the first segment <i>number</i>, "
            "scroll to the bottom, then <b>Shift+click</b> the last segment number<br>"
            "3. Copy source to target for all segments<br>"
            "4. Save project<br>"
            "5. Export → Bilingual Review (DOCX)<br>"
            "6. Open in Word, select all target text, delete it, save<br><br>"
            "<b>Then in Supervertaler:</b><br>"
            "7. Import the prepared bilingual review DOCX<br>"
            "8. Translate with AI<br>"
            "9. Export → Trados Studio → Bilingual Review<br>"
            "10. <b>Rename</b> the exported file to match the original Trados export name<br>"
            "&nbsp;&nbsp;&nbsp;&nbsp;<i>Supervertaler adds \"_translated\" to the filename, e.g.:<br>"
            "&nbsp;&nbsp;&nbsp;&nbsp;MyFile_review<b>_translated</b>.docx → rename to MyFile_review.docx</i><br>"
            "11. Update from bilingual review in Trados<br><br>"
            "<i>See Help → CAT Tool Workflow Guide for details.</i>"
        )
        workflow_dialog.setStandardButtons(
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel
        )
        workflow_dialog.setDefaultButton(QMessageBox.StandardButton.Ok)
        workflow_dialog.button(QMessageBox.StandardButton.Ok).setText(self.tr("I understand, continue"))
        workflow_dialog.button(QMessageBox.StandardButton.Cancel).setText(self.tr("Cancel import"))
        
        if workflow_dialog.exec() != QMessageBox.StandardButton.Ok:
            self.log("✗ User cancelled Trados import")
            return

        # Check if this is a re-import of the same file
        reimport_mode, preserved_id, preserved_tm, preserved_tb = self._check_reimport_same_file(
            file_path, ['current_project.trados_source_path']
        )
        if reimport_mode is None:  # User cancelled
            return

        try:
            from modules.trados_docx_handler import TradosDOCXHandler, detect_bilingual_docx_type
            
            # Check if this is a valid Trados bilingual DOCX
            file_type = detect_bilingual_docx_type(file_path)
            if file_type != "trados":
                QMessageBox.warning(
                    self, "Invalid Format",
                    "This file does not appear to be a Trados bilingual review DOCX.\n\n"
                    "Expected format: Table with columns:\n"
                    "Segment ID | Segment status | Source segment | Target segment"
                )
                return
            
            # Load the file
            handler = TradosDOCXHandler()
            if not handler.load(file_path):
                QMessageBox.critical(
                    self, "Error",
                    "Failed to load Trados bilingual review DOCX file."
                )
                return
            
            # Extract segments
            trados_segments = handler.extract_source_segments()
            
            if not trados_segments:
                QMessageBox.warning(
                    self, "No Segments",
                    "No segments found in the Trados bilingual review DOCX file."
                )
                return
            
            # Convert to internal Segment format - preserve tags for round-trip
            segments = []
            for i, t_seg in enumerate(trados_segments):
                segment = Segment(
                    id=i + 1,
                    source=t_seg.source_text,  # Preserve tags like <11>text</11>
                    target=t_seg.target_text if t_seg.target_text else "",
                    status=STATUSES["pretranslated"].key if t_seg.target_text else DEFAULT_STATUS.key,
                    notes=f"Trados Status: {t_seg.status}",
                )
                segments.append(segment)
            
            # Store the handler and original path for round-trip export
            self.trados_handler = handler
            self.trados_source_file = file_path
            
            # Note: trados_source_path will be set on the project after it's created
            
            # Show language selection dialog. The review DOCX has no language
            # header, but Word language tags on the source and target columns
            # usually give the pair away – pre-fill it (issue #200).
            from PyQt6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel, QComboBox, QDialogButtonBox, QGroupBox
            from modules.language_codes import english_name
            try:
                detected_src_iso, detected_tgt_iso = handler.detect_language_pair()
            except Exception as e:
                print(f"[Trados import] language detection error: {e}")
                detected_src_iso = detected_tgt_iso = None
            detected_src_name = english_name(detected_src_iso) if detected_src_iso else None
            detected_tgt_name = english_name(detected_tgt_iso) if detected_tgt_iso else None

            lang_dialog = QDialog(self)
            lang_dialog.setWindowTitle(self.tr("Select Languages"))
            lang_dialog.setMinimumWidth(350)
            lang_layout = QVBoxLayout(lang_dialog)
            
            # Info text
            info_label = QLabel(self._language_detection_message(detected_src_iso, detected_tgt_iso))
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
            # Prefer the detected language, else the current UI selection
            current_source = detected_src_name or (
                self.source_lang_combo.currentText() if hasattr(self, 'source_lang_combo') else "English")
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
            # Prefer the detected language, else the current UI selection
            current_target = detected_tgt_name or (
                self.target_lang_combo.currentText() if hasattr(self, 'target_lang_combo') else "Dutch")
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
                self.log("✗ User cancelled Trados import during language selection")
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

            # Apply preserved settings if re-importing
            self._apply_reimport_settings(self.current_project, reimport_mode, preserved_id, preserved_tm, preserved_tb)

            # Store Trados source path in project for persistence across saves
            self.current_project.trados_source_path = file_path

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
                self, title="Importing Trados bilingual DOCX",
                initial_label=f"Loading {_n_segs:,} segments into grid…",
                initial_total=max(_n_segs, 1),
            ) as _prog:
                self.load_segments_to_grid(progress_callback=_prog.grid_callback)
            self.initialize_tm_database()

            # Clear caches to ensure fresh TM lookups
            self._clear_caches_after_import()

            # Only deactivate resources for truly NEW projects (not re-imports)
            if not reimport_mode:
                self._deactivate_all_resources_for_new_project()

            # Auto-resize rows for better initial display
            self.auto_resize_rows()

            # Initialize spellcheck for target language
            self._initialize_spellcheck_for_target_language(target_lang)

            # Count segments with tags
            tagged_count = sum(1 for s in trados_segments if s.source_tags)
            
            # Log success
            self.log(f"✓ Imported {len(segments)} segments from Trados bilingual DOCX: {Path(file_path).name}")
            if tagged_count:
                self.log(f"  {tagged_count} segments contain inline tags")
            
            # v1.10.153: build the termbase index and prefetch
            # TM/MT/LLM matches for the first 50 segments — without
            # this, cell-clicks fall through to per-word SQL queries
            # (2-34 s per click on a 40+ termbase database).
            self._finalise_import_with_indexes()
            QMessageBox.information(
                self, "Import Successful",
                f"Successfully imported {len(segments)} segment(s) from Trados bilingual review DOCX.\n\n"
                f"File: {Path(file_path).name}\n"
                f"Languages: {source_lang} → {target_lang}\n"
                f"Segments with tags: {tagged_count}\n\n"
                f"Note: Inline tags (e.g., <11>text</11>) are preserved for export.\n"
                f"Export back to Trados format after translation to maintain tag styling."
            )
            
        except ImportError as e:
            QMessageBox.critical(
                self, "Missing Dependency",
                f"Required library is missing: {e}\n\n"
                "Install dependencies with: pip install python-docx lxml"
            )
        except Exception as e:
            QMessageBox.critical(self, "Import Error", f"Failed to import Trados bilingual DOCX:\n\n{str(e)}")
            self.log(f"✗ Trados import failed: {str(e)}")
            import traceback
            traceback.print_exc()

    def export_trados_bilingual(self):
        """Export translations back to Trados bilingual review DOCX format"""
        if not self.current_project or not self.current_project.segments:
            QMessageBox.warning(self, "No Project", "No project is currently loaded.")
            return
        
        # Check if we have a Trados source - either from handler or project data
        trados_source = None
        
        # First check if handler is already loaded
        if hasattr(self, 'trados_handler') and self.trados_handler:
            trados_source = getattr(self, 'trados_source_file', None)
        
        # If not, check if project has the source path saved
        if not trados_source and hasattr(self.current_project, 'trados_source_path') and self.current_project.trados_source_path:
            trados_source = self.current_project.trados_source_path
        
        if not trados_source:
            # Prompt user to select the original Trados bilingual file
            reply = QMessageBox.question(
                self, "Select Trados Source File",
                "To export to Trados format, please select the original Trados bilingual review DOCX file.\n\n"
                "This is the file you originally imported from Trados.\n\n"
                "Would you like to select it now?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            
            if reply == QMessageBox.StandardButton.Yes:
                file_path, _ = QFileDialog.getOpenFileName(
                    self,
                    "Select Original Trados Bilingual Review DOCX",
                    "",
                    "Word Documents (*.docx);;All Files (*.*)"
                )
                
                if file_path:
                    trados_source = file_path
                    self.trados_source_file = file_path
                    # Also save to project for future exports
                    self.current_project.trados_source_path = file_path
                    self.log(f"✓ Trados source file set: {Path(file_path).name}")
                else:
                    self.log("Export cancelled - no source file selected")
                    return
            else:
                self.log("Export cancelled")
                return
        
        # Check if source file still exists
        if not Path(trados_source).exists():
            # File not found - give user a chance to locate it
            reply = QMessageBox.question(
                self, "Source File Not Found",
                f"The original Trados source file cannot be found:\n\n"
                f"{trados_source}\n\n"
                f"Would you like to browse for the file in its new location?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            
            if reply == QMessageBox.StandardButton.Yes:
                file_path, _ = QFileDialog.getOpenFileName(
                    self,
                    "Select Original Trados Bilingual Review DOCX",
                    "",
                    "Word Documents (*.docx);;All Files (*.*)"
                )
                
                if file_path:
                    trados_source = file_path
                    self.trados_source_file = file_path
                    self.current_project.trados_source_path = file_path
                    self.log(f"✓ Trados source file relocated: {Path(file_path).name}")
                else:
                    self.log("Export cancelled - no source file selected")
                    return
            else:
                self.log("Export cancelled")
                return
        
        # Reload the handler if needed
        if not hasattr(self, 'trados_handler') or not self.trados_handler:
            try:
                from modules.trados_docx_handler import TradosDOCXHandler
                self.trados_handler = TradosDOCXHandler()
                if not self.trados_handler.load(trados_source):
                    QMessageBox.critical(self, "Error", "Failed to reload the Trados source file.")
                    return
                self.trados_source_file = trados_source
                self.log(f"✓ Reloaded Trados source file for export")
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed to reload Trados source:\n\n{str(e)}")
                return
        
        # Suggest output filename
        source_path = Path(self.trados_source_file)
        suggested_name = source_path.stem + "_translated" + source_path.suffix
        
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Export Trados Bilingual DOCX",
            str(self._export_dir_beside(source_path) / suggested_name),
            "Word Documents (*.docx);;All Files (*.*)"
        )
        
        if not file_path:
            return
        
        try:
            # Collect translations by segment, not by grid row: the grid may be
            # sorted, filtered or paginated, and then row n is not table row n
            # of the review document (and rows on other pages have no editor
            # at all, so their translations were left out).
            segments = self._segments_in_document_order()
            self._sync_grid_targets_to_segments(segments)
            translations = {}
            for seg in segments:
                target_text = (seg.target or '').strip()
                if target_text:
                    # Segment n came from table row n (row 0 is the header)
                    translations[int(seg.id)] = target_text
            
            # Update the handler with translations
            updated = self.trados_handler.update_target_segments(translations)
            
            # Save to new file
            if self.trados_handler.save(file_path):
                self.log(f"✓ Exported {updated} translated segments to Trados DOCX: {Path(file_path).name}")
                
                QMessageBox.information(
                    self, "Export Successful",
                    f"Successfully exported {updated} translation(s) to Trados bilingual DOCX.\n\n"
                    f"File: {Path(file_path).name}\n\n"
                    f"This file can be imported back into Trados Studio.\n"
                    f"Tag formatting has been preserved."
                )
            else:
                QMessageBox.critical(self, "Export Error", "Failed to save the Trados bilingual DOCX file.")
                
        except Exception as e:
            QMessageBox.critical(self, "Export Error", f"Failed to export Trados bilingual DOCX:\n\n{str(e)}")
            self.log(f"✗ Trados export failed: {str(e)}")
            import traceback
            traceback.print_exc()

    def import_sdlppx_package(self):
        """Import a Trados Studio SDLPPX package file."""
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Trados Studio Package (SDLPPX)",
            "",
            "Trados Packages (*.sdlppx);;All Files (*.*)"
        )
        
        if not file_path:
            return

        handler = None
        try:
            from modules.sdlppx_handler import TradosPackageHandler

            # v1.10.150: one shared progress dialog spans the whole import
            # (extract → parse → info dialog → segment build → grid load →
            # finalise). Previously there were two separate dialogs with a
            # gap between them — and neither stayed up through the slow
            # load_segments_to_grid phase.
            handler = TradosPackageHandler(log_callback=self.log)
            package = None
            with _ImportProgressDialog(
                self, title="Importing Trados Package",
                initial_label=f"Opening {Path(file_path).name}…",
                initial_total=100,
            ) as prog:

                def _on_pkg_parse(stage: str, current: int, total: int, message: str) -> bool:
                    # Custom callback because SDLPPX's protocol carries a
                    # `stage` flag that distinguishes the indeterminate
                    # extract step from the determinate per-file parse step.
                    if prog.cancelled():
                        return False
                    if stage == 'extract':
                        prog.set_phase(message)  # indeterminate
                    elif stage == 'parse':
                        prog.update(
                            current, total=total,
                            label=(f"Parsing SDLXLIFF: {message} ({current}/{total})"
                                   if total else f"Parsing: {message}"),
                        )
                    return not prog.cancelled()

                package = handler.load_package(file_path, progress_callback=_on_pkg_parse)

                if not package:
                    if prog.cancelled():
                        self.log("❌ SDLPPX load cancelled by user")
                        handler.cleanup()
                        return
                    QMessageBox.critical(
                        self, "Error",
                        "Failed to load the Trados Studio package.\n\n"
                        "Please ensure the file is a valid .sdlppx file."
                    )
                    return

                # Package info dialog (with optional include-locked checkbox).
                # Hide the progress dialog while the user interacts.
                from PyQt6.QtWidgets import (
                    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QDialogButtonBox,
                    QGroupBox, QTextEdit, QListWidget,
                )

                info_dialog = QDialog(self)
                info_dialog.setWindowTitle(self.tr("Trados Package Information"))
                info_dialog.setMinimumWidth(550)
                # v1.10.151: cap the dialog height so packages with many files
                # (40+) don't push the Import/Cancel buttons off the screen.
                # The file list goes in a scrollable QListWidget below.
                info_dialog.setMaximumHeight(640)
                info_layout = QVBoxLayout(info_dialog)

                total_segments = sum(len(f.segments) for f in package.xliff_files)
                locked_count = sum(
                    1 for f in package.xliff_files for s in f.segments
                    if s.locked or s.trans_unit_id.startswith('lockTU_')
                )
                translatable_count = total_segments - locked_count
                file_info = [(Path(f.file_path).name, len(f.segments)) for f in package.xliff_files]

                # Compact summary at the top: project / languages / counts.
                summary = (
                    f"<b>Project:</b> {package.project_name}<br>"
                    f"<b>Languages:</b> {package.source_lang} → {package.target_lang}<br>"
                    f"<b>Total segments:</b> {total_segments:,}"
                )
                if locked_count:
                    summary += (
                        f" ({translatable_count:,} translatable, "
                        f"{locked_count:,} locked)"
                    )
                summary_label = QLabel(summary)
                summary_label.setWordWrap(True)
                info_layout.addWidget(summary_label)

                # Files in package: scrollable list with a fixed visible
                # height. Previously this was rendered inline in the QLabel,
                # which made the dialog grow taller than the screen for
                # packages with many files.
                info_layout.addWidget(QLabel(
                    f"<b>Files in package ({len(file_info):,}):</b>"
                ))
                files_list = QListWidget()
                files_list.setMaximumHeight(280)
                for fname, count in file_info:
                    files_list.addItem(f"{fname}  —  {count:,} segments")
                info_layout.addWidget(files_list)

                include_locked_cb = None
                if locked_count:
                    include_locked_cb = CheckmarkCheckBox(
                        f"Include locked/non-translatable segments for context ({locked_count:,} segments)"
                    )
                    include_locked_cb.setChecked(False)
                    include_locked_cb.setToolTip(
                        "Locked segments are non-translatable content that Trados marks as protected.\n"
                        "Including them provides context but may slow loading for large packages."
                    )
                    info_layout.addWidget(include_locked_cb)
                    info_layout.addSpacing(5)

                buttons = QDialogButtonBox(
                    QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
                )
                buttons.button(QDialogButtonBox.StandardButton.Ok).setText(self.tr("Import"))
                buttons.accepted.connect(info_dialog.accept)
                buttons.rejected.connect(info_dialog.reject)
                info_layout.addWidget(buttons)

                with prog.suspended():
                    accepted = info_dialog.exec() == QDialog.DialogCode.Accepted

                if not accepted:
                    self.log("✗ User cancelled SDLPPX import")
                    handler.cleanup()
                    return

                # Segment-build phase: per-file progress in the same dialog.
                include_locked = (include_locked_cb.isChecked()
                                  if include_locked_cb else False)
                is_multifile = len(package.xliff_files) > 1
                segments = []
                file_metadata = []
                global_index = 0
                total_locked_filtered = 0
                total_files = len(package.xliff_files)

                prog.set_phase(
                    f"Building segments… (0/{total_files} files)", total=total_files,
                )

                cancelled = False
                for file_idx, xliff_file in enumerate(package.xliff_files):
                    if prog.cancelled():
                        cancelled = True
                        break

                    file_id = file_idx + 1
                    file_name = Path(xliff_file.file_path).name
                    file_start = global_index

                    prog.update(
                        file_idx, total=total_files,
                        label=f"Building segments: {file_name} ({file_idx + 1}/{total_files})",
                    )

                    for sdl_seg in xliff_file.segments:
                        if sdl_seg.trans_unit_id.startswith('lockTU_'):
                            total_locked_filtered += 1
                            continue
                        is_locked = sdl_seg.locked
                        if is_locked and not include_locked:
                            total_locked_filtered += 1
                            continue
                        seg = self._map_sdlxliff_segment(
                            sdl_seg, global_index,
                            file_id=file_id if is_multifile else None,
                            file_name=file_name if is_multifile else "",
                            locked=is_locked
                        )
                        segments.append(seg)
                        global_index += 1

                    if is_multifile:
                        file_segs = segments[file_start:global_index]
                        file_metadata.append({
                            'id': file_id,
                            'name': file_name,
                            'path': xliff_file.file_path,
                            'type': 'sdlxliff',
                            'segment_count': len(file_segs),
                            'start_segment_id': file_segs[0].id if file_segs else None,
                            'end_segment_id': file_segs[-1].id if file_segs else None,
                        })

                if cancelled:
                    self.log("❌ SDLPPX import cancelled by user")
                    handler.cleanup()
                    return

                if total_locked_filtered:
                    self.log(f"Filtered {total_locked_filtered} "
                             f"locked/non-translatable segments "
                             f"({len(segments)} translatable)")

                if not segments:
                    QMessageBox.warning(
                        self, "No Segments",
                        "No translatable segments found in the package."
                    )
                    handler.cleanup()
                    return

                # Normalize language codes to full names
                source_lang = self._normalize_language_code(package.source_lang)
                target_lang = self._normalize_language_code(package.target_lang)

                # Create new project (multi-file when package has multiple SDLXLIFF files)
                self.current_project = Project(
                    name=package.project_name or Path(file_path).stem,
                    segments=segments,
                    source_lang=source_lang,
                    target_lang=target_lang,
                    is_multifile=is_multifile,
                    files=file_metadata if is_multifile else None
                )

                # Store handler and package info for round-trip export
                self.sdlppx_handler = handler
                self.sdlppx_source_file = file_path
                self.current_project.sdlppx_source_path = file_path

                # CRITICAL: Update _original_segment_order for new import
                self._original_segment_order = self.current_project.segments.copy()

                # Sync global language settings with imported project languages
                self.source_language = source_lang
                self.target_language = target_lang

                # Update UI
                self.project_file_path = None
                self.project_modified = True
                self.update_window_title()

                # Grid-load phase.
                prog.set_phase(
                    f"Loading {len(segments):,} segments into grid…",
                    total=len(segments),
                )
                self.load_segments_to_grid(progress_callback=prog.grid_callback)

                # Finalise (TM/spellcheck/file-filter): indeterminate.
                prog.set_phase("Finalising import…")
                self.initialize_tm_database()
                self._clear_caches_after_import()
                self._deactivate_all_resources_for_new_project()
                # v1.10.148: auto_resize_rows already called inside
                # load_segments_to_grid; the second call here was duplicate
                # work and removed.
                self._initialize_spellcheck_for_target_language(target_lang)
                self._update_file_filter_combo()

                # Count pretranslated segments
                pretrans_count = sum(1 for s in segments if s.target)

                self.log(f"✓ Imported {len(segments)} segments from Trados package: {Path(file_path).name}")
                if pretrans_count:
                    self.log(f"  {pretrans_count} segments are pretranslated")
                if is_multifile:
                    self.log(f"  {len(file_metadata)} files in package (multi-file view active)")

            # v1.10.153: build the termbase index and prefetch
            # TM/MT/LLM matches for the first 50 segments — without
            # this, cell-clicks fall through to per-word SQL queries
            # (2-34 s per click on a 40+ termbase database).
            self._finalise_import_with_indexes()
            QMessageBox.information(
                self, "Import Successful",
                f"Successfully imported {len(segments)} segment(s) from Trados Studio package.\n\n"
                f"Package: {Path(file_path).name}\n"
                f"Files: {len(package.xliff_files)}\n"
                f"Languages: {source_lang} → {target_lang}\n"
                f"Pretranslated: {pretrans_count}\n\n"
                f"After translation, export back as SDLRPX to return to the Trados user."
            )

        except ImportError as e:
            QMessageBox.critical(
                self, "Missing Dependency",
                f"Required library is missing: {e}\n\n"
                "Install dependencies with: pip install lxml"
            )
        except Exception as e:
            QMessageBox.critical(self, "Import Error", f"Failed to import Trados package:\n\n{str(e)}")
            self.log(f"✗ SDLPPX import failed: {str(e)}")
            import traceback
            traceback.print_exc()
        finally:
            # A package that was unpacked but did not become the open
            # project (cancelled, or the import failed) leaves its
            # temporary folder behind unless it is removed here.
            if handler is not None and getattr(self, 'sdlppx_handler', None) is not handler:
                try:
                    handler.cleanup()
                except Exception:
                    pass

    def _build_sdlxliff_translations_dict(self):
        """Build segment_id -> target_text mapping for SDLXLIFF export.

        Uses sdl_segment_id field (preferred) with fallback to parsing
        from notes for backward compatibility with old project files.
        """
        translations = {}
        for segment in self.current_project.segments:
            target_text = segment.target.strip() if segment.target else ''
            if not target_text:
                continue
            # Primary: dedicated sdl_segment_id field
            seg_id = getattr(segment, 'sdl_segment_id', '')
            # Backward compat: parse from notes (old project files)
            if not seg_id:
                notes = getattr(segment, 'notes', '') or ''
                if "Segment:" in notes:
                    for part in notes.split("|"):
                        part = part.strip()
                        if part.startswith("Segment:"):
                            seg_id = part.replace("Segment:", "").strip()
                            break
            if seg_id:
                translations[seg_id] = target_text
        return translations

    def _build_sdlxliff_statuses_dict(self):
        """Build segment_id -> status key for SDLXLIFF export, mirroring
        _build_sdlxliff_translations_dict so each exported segment carries the
        project's confirmation level. The handler maps these to the Trados
        ``conf`` attribute (confirmed→Translated, approved/proofread→
        ApprovedTranslation, …). Without it the exporter defaulted every
        segment to Draft, so even a fully-confirmed project shipped as Draft.
        """
        statuses = {}
        for segment in self.current_project.segments:
            target_text = segment.target.strip() if segment.target else ''
            if not target_text:
                continue
            seg_id = getattr(segment, 'sdl_segment_id', '')
            if not seg_id:
                notes = getattr(segment, 'notes', '') or ''
                if "Segment:" in notes:
                    for part in notes.split("|"):
                        part = part.strip()
                        if part.startswith("Segment:"):
                            seg_id = part.replace("Segment:", "").strip()
                            break
            if seg_id:
                statuses[seg_id] = getattr(segment, 'status', '') or 'draft'
        return statuses

    def _build_sdlxliff_comments_dict(self):
        """Build segment_id -> comment_text mapping for SDLXLIFF comment export."""
        segment_comments = {}
        for segment in self.current_project.segments:
            seg_id = getattr(segment, 'sdl_segment_id', '')
            if not seg_id:
                continue
            notes = (getattr(segment, 'notes', '') or '').strip()
            if notes:
                segment_comments[seg_id] = notes
        return segment_comments

    def _confirm_include_comments(self, segment_comments: dict):
        """Ask whether to ship internal comments/notes in a Trados export.

        Segment comments/notes are internal by default. They can hold AI
        review rationale round-tripped in via the bilingual re-import, or any
        working note the translator never meant for the client. So the export
        must NOT write them unless the translator explicitly opts in.

        Returns the dict to use: unchanged if the user ticks the box, emptied
        if not, or ``None`` if the user cancels the export. No prompt is shown
        when there are no comments to begin with.
        """
        if not segment_comments:
            return segment_comments
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle("Include comments?")
        box.setText(
            f"This project has notes/comments on {len(segment_comments)} "
            "segment(s).")
        box.setInformativeText(
            "By default these are <b>not</b> written to the exported file, so "
            "internal notes never reach the client.<br><br>"
            "Tick the box below only if you want them exported as Trados Studio "
            "comments.")
        incl = CheckmarkCheckBox(
            "Include comments as Trados comments in the export")
        incl.setChecked(False)
        box.setCheckBox(incl)
        box.setStandardButtons(
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(QMessageBox.StandardButton.Ok)
        if box.exec() != QMessageBox.StandardButton.Ok:
            self.log("Export cancelled by user at the include-comments prompt")
            return None
        if not incl.isChecked():
            self.log(f"  Excluding {len(segment_comments)} segment comment(s) "
                     "from export (default; not shipped to client)")
            return {}
        self.log(f"  Including {len(segment_comments)} segment comment(s) in "
                 "export (translator opted in)")
        return segment_comments

    def export_sdlrpx_package(self):
        """Export translations back to a Trados Studio SDLRPX return package."""
        if not self.current_project or not self.current_project.segments:
            QMessageBox.warning(self, "No Project", "No project is currently loaded.")
            return
        
        # Check if we have an SDLPPX handler
        sdlppx_source = None
        
        if hasattr(self, 'sdlppx_handler') and self.sdlppx_handler:
            sdlppx_source = getattr(self, 'sdlppx_source_file', None)
        
        if not sdlppx_source and hasattr(self.current_project, 'sdlppx_source_path'):
            sdlppx_source = self.current_project.sdlppx_source_path
        
        if not sdlppx_source:
            QMessageBox.warning(
                self, "No SDLPPX Source",
                "This project was not imported from an SDLPPX package.\n\n"
                "To export as SDLRPX, you must first import an SDLPPX package."
            )
            return
        
        # Check if source exists
        if not Path(sdlppx_source).exists():
            # Try to locate the file
            reply = QMessageBox.question(
                self, "Source Package Not Found",
                f"The original SDLPPX package cannot be found:\n\n"
                f"{sdlppx_source}\n\n"
                f"Would you like to browse for the file?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            
            if reply == QMessageBox.StandardButton.Yes:
                file_path, _ = QFileDialog.getOpenFileName(
                    self,
                    "Select Original SDLPPX Package",
                    "",
                    "Trados Packages (*.sdlppx);;All Files (*.*)"
                )
                
                if file_path:
                    sdlppx_source = file_path
                    self.sdlppx_source_file = file_path
                    self.current_project.sdlppx_source_path = file_path
                else:
                    return
            else:
                return
        
        # Reload handler if needed
        if not hasattr(self, 'sdlppx_handler') or not self.sdlppx_handler:
            try:
                from modules.sdlppx_handler import TradosPackageHandler
                self.sdlppx_handler = TradosPackageHandler(log_callback=self.log)
                package = self.sdlppx_handler.load_package(sdlppx_source)
                if not package:
                    QMessageBox.critical(self, "Error", "Failed to reload the SDLPPX package.")
                    return
                self.sdlppx_source_file = sdlppx_source
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed to reload SDLPPX:\n\n{str(e)}")
                return
        
        # Suggest output filename
        source_path = Path(sdlppx_source)
        suggested_name = source_path.stem + ".sdlrpx"
        
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Export Trados Return Package (SDLRPX)",
            str(self._export_dir_beside(source_path) / suggested_name),
            "Trados Return Packages (*.sdlrpx);;All Files (*.*)"
        )
        
        if not file_path:
            return
        
        try:
            # Sync grid targets to segment objects to ensure we have latest edits
            self._sync_grid_targets_to_segments(self.current_project.segments)

            # Build translations and comments dicts
            translations = self._build_sdlxliff_translations_dict()
            segment_comments = self._build_sdlxliff_comments_dict()
            segment_comments = self._confirm_include_comments(segment_comments)
            if segment_comments is None:
                return  # user cancelled at the include-comments prompt

            total_segs = len(self.current_project.segments)
            segs_with_target = sum(1 for s in self.current_project.segments if s.target and s.target.strip())
            segs_with_id = sum(1 for s in self.current_project.segments
                               if getattr(s, 'sdl_segment_id', '') or
                               ('Segment:' in (getattr(s, 'notes', '') or '')))
            self.log(f"  Project: {total_segs} segments, {segs_with_target} with target text, {segs_with_id} with SDLXLIFF segment IDs")
            self.log(f"  Translations to export: {len(translations)}, comments: {len(segment_comments)}")
            if not translations:
                self.log("  WARNING: No translations found to export!")
                if segs_with_target == 0:
                    self.log("    → No segments have target text. Translate segments before exporting.")
                elif segs_with_id == 0:
                    self.log("    → No segments have SDLXLIFF segment IDs. "
                             "This project may not have been imported from an SDLPPX package.")
                else:
                    self.log("    → Segments have targets and IDs, but translations couldn't be built.")
                # Log a few segments for diagnosis
                for seg in self.current_project.segments[:5]:
                    sid = getattr(seg, 'sdl_segment_id', '')
                    has_target = bool(seg.target and seg.target.strip())
                    self.log(f"    Seg {seg.id}: target={'yes' if has_target else 'NO'}, "
                             f"sdl_id='{sid}'")

            # Update the handler with translations
            updated = self.sdlppx_handler.update_translations(
                translations, self._build_sdlxliff_statuses_dict())
            self.log(f"  Handler matched: {updated} of {len(translations)} segments")

            if updated == 0 and translations:
                # Translations exist but handler couldn't match any - log segment IDs for diagnosis
                self.log("  WARNING: Handler matched 0 segments! Possible segment ID mismatch.")
                handler_ids = set()
                for xf in self.sdlppx_handler.package.xliff_files:
                    for s in xf.segments:
                        handler_ids.add(s.segment_id)
                trans_keys = set(translations.keys())
                self.log(f"    Translation keys sample: {list(trans_keys)[:3]}")
                self.log(f"    Handler segment IDs sample: {list(handler_ids)[:3]}")

            # Store comments and translator name on handler for export
            self.sdlppx_handler.segment_comments = segment_comments
            self.sdlppx_handler.username = self.get_translator_name()

            # Export return package
            result_path = self.sdlppx_handler.create_return_package(file_path)
            
            if result_path:
                self.log(f"✓ Exported {updated} translated segments to SDLRPX: {Path(file_path).name}")
                
                QMessageBox.information(
                    self, "Export Successful",
                    f"Successfully exported Trados return package.\n\n"
                    f"File: {Path(file_path).name}\n"
                    f"Translations updated: {updated}\n\n"
                    f"This file can be imported back into Trados Studio by the project manager."
                )
            else:
                QMessageBox.critical(self, "Export Error", "Failed to create the SDLRPX return package.")
                
        except Exception as e:
            QMessageBox.critical(self, "Export Error", f"Failed to export SDLRPX:\n\n{str(e)}")
            self.log(f"✗ SDLRPX export failed: {str(e)}")
            import traceback
            traceback.print_exc()

    def _map_sdlxliff_segment(self, sdl_seg, index: int, file_id: int = None,
                              file_name: str = "", locked: bool = False) -> 'Segment':
        """
        Convert an SDLSegment to an internal Segment with proper status mapping.

        Shared by import_sdlppx_package() and import_standalone_sdlxliff().
        Maps SDL origin/percent/text-match to the 13-level Supervertaler status hierarchy.

        Args:
            sdl_seg: SDLSegment to convert
            index: 0-based index used to assign segment ID
            file_id: Optional file ID for multi-file projects
            file_name: Optional file name for multi-file projects
            locked: Whether this segment is locked/non-translatable
        """
        # Determine status based on origin, match percent, and text-match attribute
        if sdl_seg.target_text:
            if sdl_seg.origin == 'tm':
                if sdl_seg.match_percent >= 100:
                    text_match = getattr(sdl_seg, 'text_match', '')
                    if text_match == 'SourceAndTarget':
                        status = STATUSES["cm"].key
                    else:
                        status = STATUSES["tm_100"].key
                elif sdl_seg.match_percent >= 75:
                    status = STATUSES["tm_fuzzy"].key
                else:
                    status = STATUSES["pretranslated"].key
            elif sdl_seg.origin == 'perfect-match':
                status = STATUSES["pm"].key
            elif sdl_seg.origin in ('document-match', 'auto-propagated'):
                status = STATUSES["repetition"].key
            elif sdl_seg.origin in ('nmt', 'mt', 'machine-translation', 'auto-translation'):
                status = STATUSES["machine_translated"].key
            elif sdl_seg.origin == 'interactive':
                status = STATUSES["draft"].key
            else:
                status = STATUSES["pretranslated"].key
        else:
            status = DEFAULT_STATUS.key

        # Overlay Trados confirmation status (conf attribute) over origin-based status.
        # In Trados, conf and origin are orthogonal dimensions:
        #   - origin = how translation was obtained (TM, MT, interactive, etc.)
        #   - conf   = workflow state (Draft, Translated, ApprovedTranslation, etc.)
        # When conf indicates an explicit user action (confirmed/approved/rejected),
        # it takes priority. Draft segments keep the origin-based status (shows TM
        # match type), and match_percent still preserves match info independently.
        sdl_conf = getattr(sdl_seg, 'status', 'not_translated')
        if sdl_seg.target_text:
            if sdl_conf == 'translated':
                # Trados conf="Translated" = translator confirmed the segment
                status = STATUSES["confirmed"].key
            elif sdl_conf == 'approved':
                # Trados conf="ApprovedTranslation" or "ApprovedSignOff"
                status = STATUSES["approved"].key
            elif sdl_conf == 'rejected':
                status = STATUSES["rejected"].key

        # Determine match_percent from origin
        segment_match_percent = None
        if sdl_seg.origin:
            text_match = getattr(sdl_seg, 'text_match', '')
            if sdl_seg.origin == 'tm' and sdl_seg.match_percent > 0:
                if text_match == 'SourceAndTarget':
                    segment_match_percent = 101
                else:
                    segment_match_percent = sdl_seg.match_percent
            elif sdl_seg.origin == 'perfect-match':
                segment_match_percent = 102

        # Import Trados comments into notes (if any)
        comment_texts = []
        if hasattr(sdl_seg, 'comments') and sdl_seg.comments:
            for cmt in sdl_seg.comments:
                prefix = f"[{cmt['user']}] " if cmt.get('user') else ""
                comment_texts.append(f"{prefix}{cmt['text']}")

        return Segment(
            id=index + 1,
            source=sdl_seg.source_text,
            target=sdl_seg.target_text if sdl_seg.target_text else "",
            status=status,
            match_percent=segment_match_percent,
            notes="\n".join(comment_texts),
            locked=locked,
            file_id=file_id,
            file_name=file_name,
            sdl_segment_id=sdl_seg.segment_id,
        )

    def import_standalone_sdlxliff(self):
        """Import standalone SDLXLIFF file(s) directly (without Trados package)."""
        file_paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Select SDLXLIFF File(s)",
            "",
            "SDLXLIFF Files (*.sdlxliff);;All Files (*.*)"
        )
        if not file_paths:
            return

        try:
            from modules.sdlppx_handler import StandaloneSDLXLIFFHandler

            self.log(f"Importing {len(file_paths)} SDLXLIFF file(s)...")

            # v1.10.150: shared _ImportProgressDialog wrapper replaces the
            # hand-rolled QProgressDialog plumbing. Dialog spans the whole
            # import; the helper supplies ready-made parser/grid callbacks
            # and a context manager for hiding it during nested modal prompts.
            first_name = Path(file_paths[0]).name
            with _ImportProgressDialog(
                self, title="Importing SDLXLIFF",
                initial_label=f"Opening {first_name}…",
                initial_total=len(file_paths),
            ) as prog:
                handler = StandaloneSDLXLIFFHandler(log_callback=self.log)
                load_ok = handler.load(
                    file_paths, progress_callback=prog.parse_callback
                )
                if prog.cancelled():
                    self.log("❌ SDLXLIFF import cancelled by user")
                    return
                if not load_ok:
                    QMessageBox.critical(
                        self, "Import Error",
                        "Failed to load the SDLXLIFF file(s). Check the log for details."
                    )
                    return

                all_sdl_segments = handler.get_all_segments()

                # Count locked segments and ask user whether to include them
                total_count = len(all_sdl_segments)
                locked_count = sum(
                    1 for s in all_sdl_segments
                    if s.locked or s.trans_unit_id.startswith('lockTU_')
                )
                include_locked = False
                if locked_count and (total_count - locked_count) > 0:
                    with prog.suspended():
                        reply = QMessageBox.question(
                            self, "Locked Segments Detected",
                            f"Found {locked_count:,} locked/non-translatable segments "
                            f"out of {total_count:,} total.\n\n"
                            f"Include locked segments in the grid?\n"
                            f"(They provide context but may slow loading.)",
                            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                            QMessageBox.StandardButton.No
                        )
                    include_locked = (reply == QMessageBox.StandardButton.Yes)

                # Build segments per-file so the multi-file UI activates
                is_multifile = len(handler.xliff_files) > 1
                segments = []
                file_metadata = []
                global_index = 0

                for file_idx, xliff_file in enumerate(handler.xliff_files):
                    file_id = file_idx + 1
                    file_name = Path(xliff_file.file_path).name
                    file_start = global_index

                    for sdl_seg in xliff_file.segments:
                        if sdl_seg.trans_unit_id.startswith('lockTU_'):
                            continue
                        is_locked = sdl_seg.locked
                        if is_locked and not include_locked:
                            continue
                        seg = self._map_sdlxliff_segment(
                            sdl_seg, global_index,
                            file_id=file_id if is_multifile else None,
                            file_name=file_name if is_multifile else "",
                            locked=is_locked
                        )
                        segments.append(seg)
                        global_index += 1

                    if is_multifile:
                        file_segs = segments[file_start:global_index]
                        if file_segs:
                            file_metadata.append({
                                'id': file_id,
                                'name': file_name,
                                'path': xliff_file.file_path,
                                'type': 'sdlxliff',
                                'segment_count': len(file_segs),
                                'start_segment_id': file_segs[0].id,
                                'end_segment_id': file_segs[-1].id,
                            })

                if not segments:
                    QMessageBox.warning(
                        self, "No Segments",
                        "No translatable segments found in the selected file(s)."
                    )
                    return

                # Normalize language codes to full names
                source_lang = self._normalize_language_code(handler.get_source_lang())
                target_lang = self._normalize_language_code(handler.get_target_lang())

                # Create project name
                if len(file_paths) == 1:
                    project_name = Path(file_paths[0]).stem
                else:
                    project_name = f"{Path(file_paths[0]).stem} (+{len(file_paths) - 1} files)"

                # Create new project
                self.current_project = Project(
                    name=project_name,
                    segments=segments,
                    source_lang=source_lang,
                    target_lang=target_lang,
                    is_multifile=is_multifile,
                    files=file_metadata if is_multifile else None
                )

                # Store handler and source paths for round-trip export
                self.sdlxliff_handler = handler
                self.sdlxliff_source_files = file_paths
                self.current_project.sdlxliff_source_paths = file_paths

                # CRITICAL: Update _original_segment_order for new import
                self._original_segment_order = self.current_project.segments.copy()

                # Sync global language settings
                self.source_language = source_lang
                self.target_language = target_lang

                # Update UI
                self.project_file_path = None
                self.project_modified = True
                self.update_window_title()

                # Grid-load phase: helper supplies a ready-made callback that
                # updates the same dialog every ~25 rows and pumps the event
                # loop so the OS never marks the window as "Not Responding".
                prog.set_phase(
                    f"Loading {len(segments):,} segments into grid…",
                    total=len(segments),
                )
                self.load_segments_to_grid(progress_callback=prog.grid_callback)

                # Final wiring: indeterminate "busy" bar for the short
                # remaining steps (TM/spellcheck/file-filter).
                prog.set_phase("Finalising import…")

                self.initialize_tm_database()
                self._clear_caches_after_import()
                self._deactivate_all_resources_for_new_project()
                # v1.10.148: auto_resize_rows() is already called inside
                # load_segments_to_grid above — the second call here was
                # duplicate work (~0.4 s on a 479-segment SDLXLIFF) and could
                # trigger spurious cell-selection events via the processEvents()
                # pump inside its loop.
                self._initialize_spellcheck_for_target_language(target_lang)
                self._update_file_filter_combo()

                # Count pretranslated segments
                pretrans_count = sum(1 for s in segments if s.target)

                files_info = f"Files: {len(file_paths)}\n" if len(file_paths) > 1 else ""
                self.log(f"✓ Imported {len(segments)} segments from {len(file_paths)} SDLXLIFF file(s)")
                if pretrans_count:
                    self.log(f"  {pretrans_count} segments are pretranslated")

            # _ImportProgressDialog __exit__ closes the dialog before we
            # show the success message, so the info box is the only modal
            # remaining when the user dismisses it.
            # v1.10.153: build the termbase index and prefetch
            # TM/MT/LLM matches for the first 50 segments — without
            # this, cell-clicks fall through to per-word SQL queries
            # (2-34 s per click on a 40+ termbase database).
            self._finalise_import_with_indexes()
            QMessageBox.information(
                self, "Import Successful",
                f"Successfully imported {len(segments)} segment(s) from SDLXLIFF.\n\n"
                f"{files_info}"
                f"Languages: {source_lang} → {target_lang}\n"
                f"Pretranslated: {pretrans_count}\n\n"
                f"After translation, export back as SDLXLIFF (.sdlxliff) to return to the Trados user."
            )

        except ImportError as e:
            QMessageBox.critical(
                self, "Missing Dependency",
                f"Failed to import SDLXLIFF handler:\n\n{str(e)}"
            )
        except Exception as e:
            QMessageBox.critical(
                self, "Import Error",
                f"Failed to import SDLXLIFF file(s):\n\n{str(e)}"
            )
            self.log(f"✗ SDLXLIFF import failed: {str(e)}")
            import traceback
            traceback.print_exc()

    def import_sdlxliff_folder(self):
        """Import all .sdlxliff files from a selected folder (optionally recursive)."""
        folder = QFileDialog.getExistingDirectory(
            self,
            "Select Folder Containing SDLXLIFF Files",
            ""
        )
        if not folder:
            return

        # Discover all .sdlxliff files in the folder
        folder_path = Path(folder)
        file_paths = sorted(folder_path.glob("**/*.sdlxliff"))

        if not file_paths:
            # Try non-recursive as fallback (already covered by ** but be explicit in message)
            QMessageBox.warning(
                self, "No SDLXLIFF Files Found",
                f"No .sdlxliff files were found in:\n{folder}\n\n"
                "Make sure the folder contains .sdlxliff files."
            )
            return

        file_paths = [str(p) for p in file_paths]
        self.log(f"Found {len(file_paths)} SDLXLIFF file(s) in folder: {folder}")

        try:
            from modules.sdlppx_handler import StandaloneSDLXLIFFHandler

            # v1.10.150: shared _ImportProgressDialog spans the whole import.
            with _ImportProgressDialog(
                self, title="Importing SDLXLIFF Folder",
                initial_label=f"Opening {len(file_paths)} SDLXLIFF file(s)…",
                initial_total=len(file_paths),
            ) as prog:
                handler = StandaloneSDLXLIFFHandler(log_callback=self.log)
                load_ok = handler.load(file_paths, progress_callback=prog.parse_callback)
                if prog.cancelled():
                    self.log("❌ SDLXLIFF folder import cancelled by user")
                    return
                if not load_ok:
                    QMessageBox.critical(
                        self, "Import Error",
                        "Failed to load the SDLXLIFF file(s). Check the log for details."
                    )
                    return

                all_sdl_segments = handler.get_all_segments()

                # Count locked segments and ask user whether to include them
                total_count = len(all_sdl_segments)
                locked_count = sum(
                    1 for s in all_sdl_segments
                    if s.locked or s.trans_unit_id.startswith('lockTU_')
                )
                include_locked = False
                if locked_count and (total_count - locked_count) > 0:
                    with prog.suspended():
                        reply = QMessageBox.question(
                            self, "Locked Segments Detected",
                            f"Found {locked_count:,} locked/non-translatable segments "
                            f"out of {total_count:,} total.\n\n"
                            f"Include locked segments in the grid?\n"
                            f"(They provide context but may slow loading.)",
                            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                            QMessageBox.StandardButton.No
                        )
                    include_locked = (reply == QMessageBox.StandardButton.Yes)

                if not all_sdl_segments:
                    QMessageBox.warning(
                        self, "No Segments",
                        "No translatable segments found in the SDLXLIFF files."
                    )
                    return

                # Build segments and file_metadata per-file so the multi-file UI activates
                is_multifile = len(handler.xliff_files) > 1
                segments = []
                file_metadata = []
                global_index = 0

                for file_idx, xliff_file in enumerate(handler.xliff_files):
                    file_id = file_idx + 1
                    file_name = Path(xliff_file.file_path).name
                    file_start = global_index

                    for sdl_seg in xliff_file.segments:
                        # Lock TU structural segments are always excluded
                        if sdl_seg.trans_unit_id.startswith('lockTU_'):
                            continue
                        # Locked (translate="no") segments: include or skip
                        is_locked = sdl_seg.locked
                        if is_locked and not include_locked:
                            continue
                        seg = self._map_sdlxliff_segment(
                            sdl_seg, global_index,
                            file_id=file_id if is_multifile else None,
                            file_name=file_name if is_multifile else "",
                            locked=is_locked
                        )
                        segments.append(seg)
                        global_index += 1

                    if is_multifile:
                        file_segs = segments[file_start:global_index]
                        file_metadata.append({
                            'id': file_id,
                            'name': file_name,
                            'path': xliff_file.file_path,
                            'type': 'sdlxliff',
                            'segment_count': len(file_segs),
                            'start_segment_id': file_segs[0].id if file_segs else None,
                            'end_segment_id': file_segs[-1].id if file_segs else None,
                        })

                # Normalize language codes to full names
                source_lang = self._normalize_language_code(handler.get_source_lang())
                target_lang = self._normalize_language_code(handler.get_target_lang())

                # Create project name from folder
                project_name = f"{folder_path.name} ({len(file_paths)} files)"

                # Create new project
                self.current_project = Project(
                    name=project_name,
                    segments=segments,
                    source_lang=source_lang,
                    target_lang=target_lang,
                    is_multifile=is_multifile,
                    files=file_metadata if is_multifile else None
                )

                # Store handler and source paths for round-trip export
                self.sdlxliff_handler = handler
                self.sdlxliff_source_files = file_paths
                self.current_project.sdlxliff_source_paths = file_paths

                # CRITICAL: Update _original_segment_order for new import
                self._original_segment_order = self.current_project.segments.copy()

                # Sync global language settings
                self.source_language = source_lang
                self.target_language = target_lang

                # Update UI
                self.project_file_path = None
                self.project_modified = True
                self.update_window_title()

                # Grid-load phase: dialog stays open with per-row progress.
                prog.set_phase(
                    f"Loading {len(segments):,} segments into grid…",
                    total=len(segments),
                )
                self.load_segments_to_grid(progress_callback=prog.grid_callback)

                # Finalise (TM/spellcheck/file-filter): indeterminate bar.
                prog.set_phase("Finalising import…")
                self.initialize_tm_database()
                self._clear_caches_after_import()
                self._deactivate_all_resources_for_new_project()
                # v1.10.148: auto_resize_rows is already called inside
                # load_segments_to_grid; remove the redundant second call
                # that other import paths still have for consistency.
                self._initialize_spellcheck_for_target_language(target_lang)
                self._update_file_filter_combo()

                # Count pretranslated segments
                pretrans_count = sum(1 for s in segments if s.target)

                self.log(f"✓ Imported {len(segments)} segments from {len(file_paths)} SDLXLIFF file(s) in folder: {folder_path.name}")
                if pretrans_count:
                    self.log(f"  {pretrans_count} segments are pretranslated")

            # v1.10.153: build the termbase index and prefetch
            # TM/MT/LLM matches for the first 50 segments — without
            # this, cell-clicks fall through to per-word SQL queries
            # (2-34 s per click on a 40+ termbase database).
            self._finalise_import_with_indexes()
            QMessageBox.information(
                self, "Import Successful",
                f"Successfully imported {len(segments)} segment(s) from {len(file_paths)} SDLXLIFF file(s).\n\n"
                f"Folder: {folder_path.name}\n"
                f"Languages: {source_lang} → {target_lang}\n"
                f"Pretranslated: {pretrans_count}\n\n"
                f"After translation, export back as SDLXLIFF (.sdlxliff) to return to the Trados user."
            )

        except ImportError as e:
            QMessageBox.critical(
                self, "Missing Dependency",
                f"Failed to import SDLXLIFF handler:\n\n{str(e)}"
            )
        except Exception as e:
            QMessageBox.critical(
                self, "Import Error",
                f"Failed to import SDLXLIFF folder:\n\n{str(e)}"
            )
            self.log(f"✗ SDLXLIFF folder import failed: {str(e)}")
            import traceback
            traceback.print_exc()

    def export_standalone_sdlxliff(self):
        """Export translations back to standalone SDLXLIFF file(s)."""
        if not self.current_project or not self.current_project.segments:
            QMessageBox.warning(self, "No Data", "No segments to export.")
            return

        try:
            from modules.sdlppx_handler import StandaloneSDLXLIFFHandler

            # Try to find handler and source files
            handler = getattr(self, 'sdlxliff_handler', None)
            source_files = getattr(self, 'sdlxliff_source_files', None)

            # Try project-stored paths if instance vars not available
            if not handler or not source_files:
                stored_paths = getattr(self.current_project, 'sdlxliff_source_paths', None)
                if stored_paths:
                    existing = [p for p in stored_paths if Path(p).exists()]
                    if existing:
                        handler = StandaloneSDLXLIFFHandler(log_callback=self.log)
                        if handler.load(existing):
                            self.sdlxliff_handler = handler
                            self.sdlxliff_source_files = existing
                            source_files = existing
                        else:
                            handler = None

            # Prompt user to locate source files if not found
            if not handler or not source_files:
                reply = QMessageBox.question(
                    self, "Source File Not Found",
                    "The original SDLXLIFF source file(s) could not be found.\n\n"
                    "Would you like to locate them? This is needed to preserve "
                    "the original file structure during export.",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
                )
                if reply != QMessageBox.StandardButton.Yes:
                    return

                file_paths, _ = QFileDialog.getOpenFileNames(
                    self,
                    "Select Original SDLXLIFF File(s)",
                    "",
                    "SDLXLIFF Files (*.sdlxliff);;All Files (*.*)"
                )
                if not file_paths:
                    return

                handler = StandaloneSDLXLIFFHandler(log_callback=self.log)
                if not handler.load(file_paths):
                    QMessageBox.critical(self, "Error", "Failed to load the selected SDLXLIFF file(s).")
                    return

                self.sdlxliff_handler = handler
                self.sdlxliff_source_files = file_paths
                self.current_project.sdlxliff_source_paths = file_paths
                source_files = file_paths

            # Sync grid targets to segment objects
            self._sync_grid_targets_to_segments(self.current_project.segments)

            # Build translations and comments dicts
            translations = self._build_sdlxliff_translations_dict()
            segment_comments = self._build_sdlxliff_comments_dict()
            segment_comments = self._confirm_include_comments(segment_comments)
            if segment_comments is None:
                return  # user cancelled at the include-comments prompt

            if not translations:
                QMessageBox.warning(
                    self, "No Translations",
                    "No translated segments found to export."
                )
                return

            # Update handler with translations, comments, and translator name
            updated = handler.update_translations(
                translations, self._build_sdlxliff_statuses_dict())
            handler.segment_comments = segment_comments
            handler.username = self.get_translator_name()
            self.log(f"Updated {updated} segment(s) in SDLXLIFF handler, {len(segment_comments)} comment(s)")

            # Save file(s)
            if len(handler.xliff_files) == 1:
                # Single file – use Save As dialog
                default_name = Path(source_files[0]).stem + "_translated.sdlxliff"
                default_dir = str(self._export_dir_beside(source_files[0]) / default_name)

                output_path, _ = QFileDialog.getSaveFileName(
                    self,
                    "Save Translated SDLXLIFF",
                    default_dir,
                    "SDLXLIFF Files (*.sdlxliff);;All Files (*.*)"
                )
                if not output_path:
                    return

                # Safety: never overwrite source
                if Path(output_path).resolve() == Path(source_files[0]).resolve():
                    QMessageBox.warning(
                        self, "Cannot Overwrite Source",
                        "Cannot save to the same path as the source file.\n"
                        "Please choose a different filename."
                    )
                    return

                success = handler.save_file(handler.xliff_files[0], output_path)
                if success:
                    QMessageBox.information(
                        self, "Export Successful",
                        f"Successfully exported translated SDLXLIFF.\n\n"
                        f"File: {Path(output_path).name}\n"
                        f"Translations updated: {updated}\n\n"
                        f"This file can be opened in Trados Studio."
                    )
                    self.log(f"✓ Exported SDLXLIFF: {output_path}")
                else:
                    QMessageBox.critical(self, "Export Error", "Failed to save the SDLXLIFF file.")
            else:
                # Multi-file – select output directory
                output_dir = QFileDialog.getExistingDirectory(
                    self,
                    "Select Output Folder for Translated SDLXLIFF Files",
                    str(Path(source_files[0]).parent)
                )
                if not output_dir:
                    return

                saved_paths = handler.save_all(output_dir)
                if saved_paths:
                    QMessageBox.information(
                        self, "Export Successful",
                        f"Successfully exported {len(saved_paths)} translated SDLXLIFF file(s) to:\n\n"
                        f"{output_dir}\n\n"
                        f"Translations updated: {updated}\n\n"
                        f"These files can be opened in Trados Studio."
                    )
                    self.log(f"✓ Exported {len(saved_paths)} SDLXLIFF files to {output_dir}")
                else:
                    QMessageBox.critical(self, "Export Error", "Failed to save the SDLXLIFF files.")

        except Exception as e:
            QMessageBox.critical(
                self, "Export Error",
                f"Failed to export SDLXLIFF:\n\n{str(e)}"
            )
            self.log(f"✗ SDLXLIFF export failed: {str(e)}")
            import traceback
            traceback.print_exc()
