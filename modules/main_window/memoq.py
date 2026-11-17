"""memoQ: bilingual DOCX, RTF and XLIFF, import and export.

Part of the main window (SupervertalerQt), moved here unchanged. The
methods run as the main window's own: `self` is the SupervertalerQt.
"""

import re
import traceback
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QGroupBox,
    QLabel,
    QMessageBox,
    QVBoxLayout,
)
from modules.main_dialogs import _ImportProgressDialog
from modules.segment_model import Project, Segment
from modules.statuses import (
    DEFAULT_STATUS,
    STATUSES,
    compose_memoq_status,
    match_memoq_status,
)
from modules.styled_widgets import CheckmarkCheckBox
from modules.tag_text import has_formatting_tags, runs_to_tagged_text, tagged_text_to_runs
from pathlib import Path
from typing import Optional, Tuple


class MemoQMixin:
    def _interpret_memoq_status(self, status_text: str, has_target: bool) -> Tuple[str, Optional[int]]:
        """Map memoQ status text to internal status and extract match percentage."""
        status_def, match_percent = match_memoq_status(status_text)
        if status_def.key == DEFAULT_STATUS.key and has_target:
            return STATUSES["pretranslated"].key, match_percent
        return status_def.key, match_percent

    def import_memoq_bilingual(self):
        """Import memoQ bilingual DOCX file (table format)"""
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select memoQ Bilingual DOCX File",
            "",
            "Word Documents (*.docx);;All Files (*.*)"
        )
        
        if not file_path:
            return
        
        # Show formatting options dialog
        from PyQt6.QtWidgets import QDialog, QVBoxLayout, QLabel, QDialogButtonBox, QGroupBox
        
        dialog = QDialog(self)
        dialog.setWindowTitle(self.tr("memoQ Bilingual Import Options"))
        dialog.setMinimumWidth(450)
        layout = QVBoxLayout(dialog)
        
        # Info text
        info_label = QLabel(
            "This workflow imports memoQ bilingual DOCX for round-tripping.\n"
            "The project can be exported back to memoQ bilingual DOCX format."
        )
        info_label.setWordWrap(True)
        info_label.setStyleSheet("color: #666; margin-bottom: 10px;")
        layout.addWidget(info_label)
        
        # Formatting options group
        format_group = QGroupBox(self.tr("Formatting Handling"))
        format_layout = QVBoxLayout(format_group)
        
        # Option 1: Ignore formatting (using CheckmarkCheckBox)
        ignore_checkbox = CheckmarkCheckBox(self.tr("Ignore inline formatting"))
        ignore_checkbox.setToolTip(self.tr("Bold, italic, and underline formatting will not be transferred to translations.\nUse this if formatting causes issues or isn't needed."))
        ignore_checkbox.setChecked(False)  # Default: use smart formatting
        format_layout.addWidget(ignore_checkbox)
        
        ignore_desc = QLabel(self.tr("    When checked, formatting (bold/italic/underline) will not be applied to translations."))
        ignore_desc.setStyleSheet("color: #888; font-size: 10px; margin-left: 20px;")
        format_layout.addWidget(ignore_desc)
        
        # Option 2: Smart formatting (using CheckmarkCheckBox)
        smart_checkbox = CheckmarkCheckBox(self.tr("Smart formatting transfer"))
        smart_checkbox.setToolTip(self.tr("Attempts to identify formatted text in source and apply matching formatting to corresponding words in the translation."))
        smart_checkbox.setChecked(True)  # Default
        format_layout.addWidget(smart_checkbox)
        
        smart_desc = QLabel(self.tr("    When checked, tries to match formatted source phrases to their translations."))
        smart_desc.setStyleSheet("color: #888; font-size: 10px; margin-left: 20px;")
        format_layout.addWidget(smart_desc)
        
        # Make checkboxes mutually exclusive
        def on_ignore_changed(checked):
            if checked:
                smart_checkbox.setChecked(False)
        
        def on_smart_changed(checked):
            if checked:
                ignore_checkbox.setChecked(False)
        
        ignore_checkbox.toggled.connect(on_ignore_changed)
        smart_checkbox.toggled.connect(on_smart_changed)
        
        layout.addWidget(format_group)
        
        # Note about tags - escape the angle brackets so they show literally
        note_label = QLabel(
            self.tr("ℹ️ Note: Inline XML tags (like &lt;b&gt;text&lt;/b&gt;) are always preserved regardless of this setting.")
        )
        note_label.setWordWrap(True)
        note_label.setStyleSheet("color: #2196F3; font-size: 10px; margin-top: 10px;")
        layout.addWidget(note_label)
        
        # Buttons
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        
        # Store formatting preference
        self.memoq_smart_formatting = smart_checkbox.isChecked()
        self.log(f"📄 memoQ import: {'Smart formatting transfer' if self.memoq_smart_formatting else 'Ignore formatting'}")

        # Check if this is a re-import of the same file
        reimport_mode, preserved_id, preserved_tm, preserved_tb = self._check_reimport_same_file(
            file_path, ['current_project.memoq_source_path']
        )
        if reimport_mode is None:  # User cancelled
            return

        try:
            from docx import Document
            
            # Load the bilingual DOCX
            doc = Document(file_path)
            
            if not doc.tables:
                QMessageBox.critical(
                    self, "Error",
                    "No table found in the DOCX file.\n\nExpected memoQ bilingual format with a table."
                )
                return
            
            table = doc.tables[0]
            
            # Validate table structure (should have at least 3 rows: header, column names, data)
            if len(table.rows) < 3:
                QMessageBox.critical(
                    self, "Error",
                    f"Invalid table structure.\n\nExpected at least 3 rows, found {len(table.rows)}."
                )
                return
            
            # CLASSIC APPROACH: Extract source segments to simple line-by-line list
            source_segments = []
            target_segments = []
            formatting_map = {}  # segment_index -> list of formatting info
            metadata = []  # Store comments, status, etc. for later
            
            for row_idx in range(2, len(table.rows)):
                row = table.rows[row_idx]
                
                # Ensure we have at least 3 cells (0=segment#, 1=source, 2=target)
                if len(row.cells) >= 3:
                    source_cell = row.cells[1]
                    target_cell = row.cells[2]
                    
                    # Convert runs to HTML-tagged text if smart formatting is enabled
                    if self.memoq_smart_formatting:
                        source_text = runs_to_tagged_text(source_cell.paragraphs)
                        target_text = runs_to_tagged_text(target_cell.paragraphs)
                    else:
                        source_text = source_cell.text.strip()
                        target_text = target_cell.text.strip()
                    
                    # Always add (even if empty) to maintain alignment
                    source_segments.append(source_text)
                    target_segments.append(target_text)
                    
                    # Store metadata
                    comment_text = row.cells[3].text.strip() if len(row.cells) >= 4 else ""
                    status_text = row.cells[4].text.strip() if len(row.cells) >= 5 else ""
                    metadata.append({
                        'comment': comment_text,
                        'status': status_text,
                        'target': target_text
                    })
                    
                    # Also extract formatting info for backwards compatibility / export
                    formatting_info = []
                    for paragraph in source_cell.paragraphs:
                        for run in paragraph.runs:
                            run_text = run.text
                            if run_text:  # Only store runs with actual text
                                formatting_info.append({
                                    'text': run_text,
                                    'bold': run.bold == True,
                                    'italic': run.italic == True,
                                    'underline': run.underline == True
                                })
                    
                    # Store formatting for this segment
                    if formatting_info:
                        segment_idx = len(source_segments) - 1
                        formatting_map[segment_idx] = formatting_info
            
            if not source_segments:
                QMessageBox.warning(self, "Warning", "No segments found in the bilingual file.")
                return
            
            # Detect languages from table header (row 1, columns 1 and 2).
            # Resolved through modules.language_codes, which understands names,
            # ISO codes and region variants in any spelling, rather than the
            # 26-name English-only map this used to carry. When either column
            # cannot be resolved we ASK instead of silently assuming en -> nl:
            # a project created with the wrong pair shows zero TM matches while
            # the TM still looks complete in TM Edit/Maintain, which is close to
            # undiagnosable from the user's side.
            header_row = table.rows[1]
            source_lang = target_lang = None
            source_header = target_header = ""

            if len(header_row.cells) >= 3:
                source_header = header_row.cells[1].text.strip()
                target_header = header_row.cells[2].text.strip()
                source_lang = self._lang_from_column_header(source_header)
                target_lang = self._lang_from_column_header(target_header)

            if not source_lang or not target_lang:
                self.log(
                    "⚠️ Could not read the language pair from the memoQ table header "
                    f"(source column: {source_header!r}, target column: {target_header!r}) "
                    "— asking the user."
                )
                chosen = self._confirm_language_pair(
                    source_lang, target_lang,
                    context=(
                        f"Source column header: {source_header or '(empty)'}" + chr(10) +
                        f"Target column header: {target_header or '(empty)'}"
                    ),
                )
                if not chosen:
                    self.log("✗ User cancelled memoQ import at the language prompt")
                    return
                source_lang, target_lang = chosen
            
            # SAFETY STEP: Save source segments to TXT file for user verification
            txt_file_path = Path(file_path).with_suffix('.txt')
            try:
                with open(txt_file_path, 'w', encoding='utf-8') as f:
                    f.write('\n'.join(source_segments))
                self.log(f"✓ Extracted {len(source_segments)} segments to: {txt_file_path.name}")
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed to save TXT file:\n\n{e}")
                return
            
            # Show verification dialog
            reply = QMessageBox.question(
                self,
                "Verify Extracted Segments",
                f"Extracted {len(source_segments)} source segments from memoQ file.\n\n"
                f"A text file has been saved for verification:\n{txt_file_path.name}\n\n"
                f"Please check this file to ensure all segments were extracted correctly.\n\n"
                f"Languages detected: {source_lang.upper()} → {target_lang.upper()}\n\n"
                f"Continue with import?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes
            )
            
            if reply != QMessageBox.StandardButton.Yes:
                self.log("✗ User cancelled memoQ import after extraction")
                return
            
            # Store the bilingual DOCX metadata for later export
            self.memoq_source_file = file_path
            self.memoq_source_segments = source_segments
            self.memoq_target_segments = target_segments
            self.memoq_formatting_map = formatting_map
            self.memoq_metadata = metadata
            
            # Create simple project with sequential IDs
            project_name = Path(file_path).stem
            self.current_project = Project(
                name=project_name,
                source_lang=source_lang,
                target_lang=target_lang,
                segments=[]
            )

            # Apply preserved settings if re-importing
            self._apply_reimport_settings(self.current_project, reimport_mode, preserved_id, preserved_tm, preserved_tb)

            # Store memoQ source path in project for persistence across saves
            self.current_project.memoq_source_path = file_path

            # Sync global language settings with imported project languages
            self.source_language = source_lang
            self.target_language = target_lang

            # Create segments with simple sequential IDs
            for idx, source_text in enumerate(source_segments):
                existing_target = target_segments[idx] if idx < len(target_segments) else ""
                memoq_status_text = metadata[idx].get('status', '') if idx < len(metadata) else ""

                # Use memoQ status parser to extract proper status + match percentage
                if memoq_status_text:
                    status, match_percent = self._interpret_memoq_status(
                        memoq_status_text, bool(existing_target.strip())
                    )
                else:
                    status = "draft" if existing_target.strip() else "not_started"
                    match_percent = None

                segment = Segment(
                    id=idx + 1,  # Sequential: 1, 2, 3, 4...
                    source=source_text,
                    target=existing_target,
                    status=status,
                    match_percent=match_percent,
                    type="para",
                    notes=metadata[idx].get('comment', '') if idx < len(metadata) else "",
                    memoQ_status=memoq_status_text
                )
                self.current_project.segments.append(segment)
            
            # Update UI
            self.project_file_path = None
            self.project_modified = True

            # CRITICAL: Update _original_segment_order for new import
            self._original_segment_order = self.current_project.segments.copy()

            self.update_window_title()
            # v1.10.150: wrap the slow grid-load in the shared progress dialog so
            # the user sees activity instead of "Not Responding" on large files.
            # (Minimal wrap: subsequent fast finalise steps stay outside the dialog.)
            _n_segs = len(self.current_project.segments) if self.current_project else 0
            with _ImportProgressDialog(
                self, title="Importing memoQ bilingual DOCX",
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

            # If smart formatting was used, auto-enable Tags view so user sees the tags
            if self.memoq_smart_formatting:
                self._enable_tag_view_after_import()

            self.log(f"✓ Imported memoQ bilingual DOCX: {len(source_segments)} segments from {Path(file_path).name}")

            # Store current document path for AI Assistant
            self.current_document_path = file_path

            # Generate markdown for AI Assistant if enabled
            if hasattr(self, 'auto_generate_markdown') and self.auto_generate_markdown:
                if hasattr(self, 'prompt_manager_qt'):
                    self.prompt_manager_qt.generate_markdown_for_current_document()

            # Refresh AI Assistant context
            if hasattr(self, 'prompt_manager_qt'):
                self.prompt_manager_qt.refresh_context()

            # v1.10.153: build the termbase index and prefetch
            # TM/MT/LLM matches for the first 50 segments — without
            # this, cell-clicks fall through to per-word SQL queries
            # (2-34 s per click on a 40+ termbase database).
            self._finalise_import_with_indexes()
            QMessageBox.information(
                self, "Import Successful",
                f"Imported {len(source_segments)} segment(s) from memoQ bilingual DOCX.\n\n"
                f"File: {Path(file_path).name}\n"
                f"Languages: {source_lang} → {target_lang}"
            )
            
        except ImportError:
            QMessageBox.critical(
                self, "Missing Dependency",
                "The 'python-docx' library is required for memoQ bilingual DOCX import.\n\n"
                "Install it with: pip install python-docx"
            )
        except Exception as e:
            QMessageBox.critical(self, "Import Error", f"Failed to import memoQ bilingual DOCX:\n\n{str(e)}")
            self.log(f"✗ memoQ import failed: {str(e)}")
            import traceback
            traceback.print_exc()

    def import_memoq_rtf(self):
        """Import memoQ bilingual RTF file (table format)"""
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select memoQ Bilingual RTF File",
            "",
            "RTF Files (*.rtf);;All Files (*.*)"
        )

        if not file_path:
            return

        # Show formatting options dialog (same as DOCX import)
        from PyQt6.QtWidgets import QDialog, QVBoxLayout, QLabel, QDialogButtonBox, QGroupBox

        dialog = QDialog(self)
        dialog.setWindowTitle(self.tr("memoQ Bilingual Import Options"))
        dialog.setMinimumWidth(450)
        layout = QVBoxLayout(dialog)

        # Info text
        info_label = QLabel(
            "This workflow imports memoQ bilingual RTF for round-tripping.\n"
            "The project can be exported back to memoQ bilingual RTF format."
        )
        info_label.setWordWrap(True)
        info_label.setStyleSheet("color: #666; margin-bottom: 10px;")
        layout.addWidget(info_label)

        # Formatting options group
        format_group = QGroupBox(self.tr("Formatting Handling"))
        format_layout = QVBoxLayout(format_group)

        # Option 1: Ignore formatting
        ignore_checkbox = CheckmarkCheckBox(self.tr("Ignore inline formatting"))
        ignore_checkbox.setToolTip(self.tr("Bold, italic, and underline formatting will not be transferred to translations.\nUse this if formatting causes issues or isn't needed."))
        ignore_checkbox.setChecked(False)
        format_layout.addWidget(ignore_checkbox)

        ignore_desc = QLabel(self.tr("    When checked, formatting (bold/italic/underline) will not be applied to translations."))
        ignore_desc.setStyleSheet("color: #888; font-size: 10px; margin-left: 20px;")
        format_layout.addWidget(ignore_desc)

        # Option 2: Smart formatting
        smart_checkbox = CheckmarkCheckBox(self.tr("Smart formatting transfer"))
        smart_checkbox.setToolTip(self.tr("Attempts to identify formatted text in source and apply matching formatting to corresponding words in the translation."))
        smart_checkbox.setChecked(True)
        format_layout.addWidget(smart_checkbox)

        smart_desc = QLabel(self.tr("    When checked, tries to match formatted source phrases to their translations."))
        smart_desc.setStyleSheet("color: #888; font-size: 10px; margin-left: 20px;")
        format_layout.addWidget(smart_desc)

        # Make checkboxes mutually exclusive
        def on_ignore_changed(checked):
            if checked:
                smart_checkbox.setChecked(False)

        def on_smart_changed(checked):
            if checked:
                ignore_checkbox.setChecked(False)

        ignore_checkbox.toggled.connect(on_ignore_changed)
        smart_checkbox.toggled.connect(on_smart_changed)

        layout.addWidget(format_group)

        # Note about tags
        note_label = QLabel(
            self.tr("ℹ️ Note: Inline XML tags (like &lt;b&gt;text&lt;/b&gt;) are always preserved regardless of this setting.")
        )
        note_label.setWordWrap(True)
        note_label.setStyleSheet("color: #2196F3; font-size: 10px; margin-top: 10px;")
        layout.addWidget(note_label)

        # Buttons
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        # Store formatting preference
        self.memoq_smart_formatting = smart_checkbox.isChecked()
        self.log(f"📄 memoQ RTF import: {'Smart formatting transfer' if self.memoq_smart_formatting else 'Ignore formatting'}")

        # Check for re-import scenario
        reimport_mode, preserved_id, preserved_tm, preserved_tb = self._check_reimport_same_file(
            file_path, ['current_project.memoq_rtf_source_path']
        )
        if reimport_mode is None:  # User cancelled
            return

        try:
            from modules.memoqrtf_handler import MemoQRTFHandler

            # Load the bilingual RTF
            handler = MemoQRTFHandler()
            handler.preserve_formatting = self.memoq_smart_formatting
            if not handler.load(file_path):
                QMessageBox.critical(
                    self, "Error",
                    "Failed to load memoQ bilingual RTF file.\n\n"
                    "Please ensure the file is a valid memoQ bilingual export."
                )
                return

            if not handler.segments:
                QMessageBox.warning(self, "Warning", "No segments found in the bilingual RTF file.")
                return

            # Extract segments
            source_segments = [seg.source_text for seg in handler.segments]
            target_segments = [seg.target_text for seg in handler.segments]
            metadata = [{
                'comment': seg.comment,
                'status': seg.status,
                'guid': seg.guid,
                'target': seg.target_text
            } for seg in handler.segments]

            # The header row names the languages. If it could not be read, ask
            # rather than import with the Dutch → English placeholders (#200).
            if not getattr(handler, 'languages_detected', True):
                self.log("⚠️ Could not read the language pair from the memoQ RTF header — asking the user.")
                chosen = self._confirm_language_pair(
                    None, None, context=self.tr("File: {}").format(Path(file_path).name))
                if not chosen:
                    self.log("✗ User cancelled memoQ RTF import at the language prompt")
                    return
                from modules import language_codes as _lc
                handler.source_lang = _lc.base_code(chosen[0]) or chosen[0]
                handler.target_lang = _lc.base_code(chosen[1]) or chosen[1]

            # SAFETY STEP: Save source segments to TXT file for user verification
            txt_file_path = Path(file_path).with_suffix('.txt')
            try:
                with open(txt_file_path, 'w', encoding='utf-8') as f:
                    f.write('\n'.join(source_segments))
                self.log(f"✓ Extracted {len(source_segments)} segments to: {txt_file_path.name}")
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed to save TXT file:\n\n{e}")
                return

            # Show verification dialog
            reply = QMessageBox.question(
                self,
                "Verify Extracted Segments",
                f"Extracted {len(source_segments)} source segments from memoQ RTF file.\n\n"
                f"A text file has been saved for verification:\n{txt_file_path.name}\n\n"
                f"Please check this file to ensure all segments were extracted correctly.\n\n"
                f"Languages detected: {handler.source_lang.upper()} → {handler.target_lang.upper()}\n\n"
                f"Continue with import?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes
            )

            if reply != QMessageBox.StandardButton.Yes:
                self.log("✗ User cancelled memoQ RTF import after extraction")
                return

            # Store the bilingual RTF metadata for later export
            self.memoq_rtf_source_file = file_path
            self.memoq_rtf_handler = handler
            self.memoq_rtf_metadata = metadata

            # Create project
            project_name = Path(file_path).stem
            self.current_project = Project(
                name=project_name,
                source_lang=handler.source_lang,
                target_lang=handler.target_lang,
                segments=[]
            )

            # Apply preserved settings if re-importing
            self._apply_reimport_settings(self.current_project, reimport_mode, preserved_id, preserved_tm, preserved_tb)

            # Store memoQ RTF source path in project for persistence across saves
            self.current_project.memoq_rtf_source_path = file_path

            # Sync global language settings
            self.source_language = handler.source_lang
            self.target_language = handler.target_lang

            # Create segments
            for idx, source_text in enumerate(source_segments):
                existing_target = target_segments[idx] if idx < len(target_segments) else ""
                memoq_status_text = metadata[idx].get('status', '') if idx < len(metadata) else ""

                # Use memoQ status parser to extract proper status + match percentage
                if memoq_status_text:
                    status, match_percent = self._interpret_memoq_status(
                        memoq_status_text, bool(existing_target.strip())
                    )
                else:
                    status = "draft" if existing_target.strip() else "not_started"
                    match_percent = None

                segment = Segment(
                    id=idx + 1,
                    source=source_text,
                    target=existing_target,
                    status=status,
                    match_percent=match_percent,
                    type="para",
                    notes=metadata[idx].get('comment', '') if idx < len(metadata) else "",
                    memoQ_status=memoq_status_text
                )
                self.current_project.segments.append(segment)

            # Update UI
            self.project_file_path = None
            self.project_modified = True
            self._original_segment_order = self.current_project.segments.copy()
            self.update_window_title()
            # v1.10.150: wrap the slow grid-load in the shared progress dialog so
            # the user sees activity instead of "Not Responding" on large files.
            # (Minimal wrap: subsequent fast finalise steps stay outside the dialog.)
            _n_segs = len(self.current_project.segments) if self.current_project else 0
            with _ImportProgressDialog(
                self, title="Importing memoQ RTF",
                initial_label=f"Loading {_n_segs:,} segments into grid…",
                initial_total=max(_n_segs, 1),
            ) as _prog:
                self.load_segments_to_grid(progress_callback=_prog.grid_callback)
            self.initialize_tm_database()

            # Clear caches
            self._clear_caches_after_import()

            # Only deactivate resources for truly NEW projects
            if not reimport_mode:
                self._deactivate_all_resources_for_new_project()

            # Auto-resize rows
            self.auto_resize_rows()

            # Initialize spellcheck
            self._initialize_spellcheck_for_target_language(handler.target_lang)

            # If smart formatting was used, auto-enable Tags view so user sees the tags
            if self.memoq_smart_formatting:
                self._enable_tag_view_after_import()

            self.log(f"✓ Imported memoQ bilingual RTF: {len(source_segments)} segments from {Path(file_path).name}")

            # Store current document path for AI Assistant
            self.current_document_path = file_path

            # Generate markdown for AI Assistant if enabled
            if hasattr(self, 'auto_generate_markdown') and self.auto_generate_markdown:
                if hasattr(self, 'prompt_manager_qt'):
                    self.prompt_manager_qt.generate_markdown_for_current_document()

            # Refresh AI Assistant context
            if hasattr(self, 'prompt_manager_qt'):
                self.prompt_manager_qt.refresh_context()

            # v1.10.153: build the termbase index and prefetch
            # TM/MT/LLM matches for the first 50 segments — without
            # this, cell-clicks fall through to per-word SQL queries
            # (2-34 s per click on a 40+ termbase database).
            self._finalise_import_with_indexes()
            QMessageBox.information(
                self, "Import Successful",
                f"Imported {len(source_segments)} segment(s) from memoQ bilingual RTF.\n\n"
                f"File: {Path(file_path).name}\n"
                f"Languages: {handler.source_lang} → {handler.target_lang}"
            )

        except ImportError as e:
            QMessageBox.critical(
                self, "Import Error",
                f"Failed to import memoQ RTF handler module:\n\n{str(e)}"
            )
        except Exception as e:
            QMessageBox.critical(self, "Import Error", f"Failed to import memoQ bilingual RTF:\n\n{str(e)}")
            self.log(f"✗ memoQ RTF import failed: {str(e)}")
            import traceback
            traceback.print_exc()

    def export_memoq_bilingual(self):
        """Export to memoQ bilingual DOCX format with translations"""
        # Check if we have segments
        if not self.current_project or not self.current_project.segments:
            QMessageBox.warning(self, "No Data", "No segments to export")
            return

        # Check if a memoQ source file was imported - check both instance var and project
        memoq_source = None
        if hasattr(self, 'memoq_source_file') and self.memoq_source_file:
            memoq_source = self.memoq_source_file
        elif hasattr(self.current_project, 'memoq_source_path') and self.current_project.memoq_source_path:
            memoq_source = self.current_project.memoq_source_path
            # Verify it still exists
            if Path(memoq_source).exists():
                self.memoq_source_file = memoq_source
                self.log(f"✓ Restored memoQ source from project: {Path(memoq_source).name}")
            else:
                memoq_source = None
        
        if not memoq_source:
            # Prompt user to select the original memoQ bilingual file
            reply = QMessageBox.question(
                self, "Select memoQ Source File",
                "To export to memoQ format, please select the original memoQ bilingual DOCX file.\n\n"
                "This is the file you originally imported from memoQ.\n\n"
                "Would you like to select it now?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )

            if reply == QMessageBox.StandardButton.Yes:
                file_path, _ = QFileDialog.getOpenFileName(
                    self,
                    "Select Original memoQ Bilingual DOCX",
                    "",
                    "Word Documents (*.docx);;All Files (*.*)"
                )

                if file_path:
                    self.memoq_source_file = file_path
                    self.log(f"✓ memoQ source file set: {Path(file_path).name}")
                else:
                    self.log("Export cancelled - no source file selected")
                    return
            else:
                self.log("Export cancelled")
                return
        
        try:
            from docx import Document
            from docx.shared import RGBColor
            
            segments = self._segments_in_document_order()
            translations = [seg.target for seg in segments]

            if not translations or all(not t.strip() for t in translations):
                QMessageBox.warning(self, "Warning", "No translations found to export.")
                return
            
            # Load the original bilingual DOCX
            doc = Document(self.memoq_source_file)
            table = doc.tables[0]
            
            # Write translations to target column (column 2) and update status
            segments_updated = 0
            segments_with_formatting = 0
            
            for i, translation in enumerate(translations):
                segment = segments[i]
                row_idx = i + 2  # Skip header rows (0 and 1)

                # Safety check: ensure we don't go beyond available rows
                if row_idx >= len(table.rows):
                    self.log(f"⚠ Warning: Row {row_idx} exceeds table rows ({len(table.rows)}), stopping at segment {i}")
                    break

                row = table.rows[row_idx]
                num_cells = len(row.cells)

                # Write translation to column 2 (target) with formatting
                if num_cells >= 3:
                    target_cell = row.cells[2]

                    # Check for embedded HTML tags first (new approach)
                    if has_formatting_tags(translation):
                        segments_with_formatting += 1
                        formatting_info = None  # Tags are in the text itself
                    else:
                        # Get formatting info directly from source cell (preserves color/formatting across sessions)
                        formatting_info = []
                        if num_cells >= 2:
                            source_cell = row.cells[1]
                            for paragraph in source_cell.paragraphs:
                                for run in paragraph.runs:
                                    if run.text:
                                        # Capture color if present
                                        color_hex = None
                                        try:
                                            # Method 1: Check standard rgb property
                                            if run.font.color and run.font.color.rgb:
                                                color_hex = str(run.font.color.rgb)
                                            # Method 2: Check XML directly for w:color val (more robust)
                                            elif run.element.rPr is not None:
                                                from docx.oxml.ns import qn
                                                color_elem = run.element.rPr.find(qn('w:color'))
                                                if color_elem is not None:
                                                    color_hex = color_elem.get(qn('w:val'))
                                                # Method 3: Check if run uses a character style (e.g., mqInternal)
                                                # memoQ stores tag colors in character styles, not inline
                                                if not color_hex:
                                                    style_elem = run.element.rPr.find(qn('w:rStyle'))
                                                    if style_elem is not None:
                                                        style_id = style_elem.get(qn('w:val'))
                                                        if style_id:
                                                            # Lookup the style and get its color
                                                            try:
                                                                style = doc.styles[style_id]
                                                                if style.font.color and style.font.color.rgb:
                                                                    color_hex = str(style.font.color.rgb)
                                                            except KeyError:
                                                                pass  # Style not found
                                        except Exception as e:
                                            if i == 0: 
                                                print(f"Color extract error: {e}")
                                        
                                        # Split run text by tags to allow granular formatting transfer
                                        # This ensures that if a tag like {1} is merged with text in one run,
                                        # we still extract {1} as a separate formatting unit.
                                        import re
                                        # Matches {1}, [1], <tag>, or </tag>
                                        parts = re.split(r'(\{\d+\}|\[\d+\]|<\/?[a-zA-Z0-9_]+[^>]*>)', run.text)
                                        
                                        for part in parts:
                                            if not part:
                                                continue
                                                
                                            formatting_info.append({
                                                'text': part,
                                                'bold': run.bold == True,
                                                'italic': run.italic == True,
                                                'underline': run.underline == True,
                                                'color': color_hex
                                            })
            
                        # Debug logging for first segment to help troubleshoot
                        if i == 0 and formatting_info:
                            color_tags = [f['text'] for f in formatting_info if f.get('color')]
                            if color_tags:
                                self.log(f"🔍 Debug: Found colored tags in first segment: {color_tags}")

                        if any(f.get('bold') or f.get('italic') or f.get('underline') or f.get('color') for f in formatting_info):
                            segments_with_formatting += 1

                    # Apply formatting to the target cell
                    self._apply_formatting_to_cell(target_cell, translation, formatting_info)
                    segments_updated += 1

                # Update comments in column 3 (if column exists)
                if num_cells >= 4:
                    if segment.notes and segment.notes.strip():
                        row.cells[3].text = segment.notes.strip()

                # Update status column using compose_memoq_status (if column exists)
                if num_cells >= 5:
                    existing = row.cells[4].text
                    row.cells[4].text = compose_memoq_status(segment.status, segment.match_percent, existing)
            
            # Prompt user to save the updated bilingual file
            # Use the same directory as the original import file
            source_path = Path(self.memoq_source_file)
            default_name = str(self._export_dir_beside(source_path) / (source_path.stem + "_translated.docx"))
            save_path, _ = QFileDialog.getSaveFileName(
                self,
                "Save memoQ Bilingual DOCX",
                default_name,
                "Word Documents (*.docx);;All Files (*.*)"
            )
            
            if save_path:
                doc.save(save_path)
                
                # Build success message
                success_msg = (
                    f"✓ Successfully exported {segments_updated} translation(s) to memoQ bilingual DOCX!\n\n"
                    f"File saved: {Path(save_path).name}\n\n"
                )
                
                if segments_with_formatting > 0:
                    success_msg += (
                        f"✓ Formatting preserved in {segments_with_formatting} segment(s)\n"
                        f"  (bold, italic, underline)\n\n"
                    )
                
                success_msg += (
                    f"✓ Status updated to 'Confirmed'\n"
                    f"✓ Table structure preserved\n\n"
                    f"You can now import this file back into memoQ."
                )
                
                self.log(f"✓ Exported {segments_updated} translations to memoQ bilingual DOCX: {Path(save_path).name}")
                
                QMessageBox.information(self, "Export Successful", success_msg)
        
        except ImportError:
            QMessageBox.critical(
                self, "Missing Dependency",
                "The 'python-docx' library is required for memoQ bilingual DOCX export.\n\n"
                "Install it with: pip install python-docx"
            )
        except Exception as e:
            QMessageBox.critical(self, "Export Error", f"Failed to export memoQ bilingual DOCX:\n\n{str(e)}")
            self.log(f"✗ memoQ export failed: {str(e)}")
            import traceback
            traceback.print_exc()

    def _apply_formatting_to_cell(self, cell, text, formatting_info=None):
        """
        Apply formatting to a cell based on embedded HTML tags or formatting info from source.
        
        Priority:
        1. If text contains <b>, <i>, <u> tags, parse and apply them directly
        2. Otherwise, use formatting_info for smart transfer (legacy approach)
        3. If neither, just add plain text
        """
        # Clear existing paragraphs
        cell._element.clear_content()
        
        # Add new paragraph
        paragraph = cell.add_paragraph()
        
        if not text:
            return
        
        # PRIORITY 1: Check for embedded HTML tags in the text
        if has_formatting_tags(text):
            # Parse the tags and create runs with formatting
            runs = tagged_text_to_runs(text)
            
            for run_info in runs:
                run_text = run_info.get('text', '')
                if not run_text:
                    continue
                    
                run = paragraph.add_run(run_text)
                
                if run_info.get('bold'):
                    run.bold = True
                if run_info.get('italic'):
                    run.italic = True
                if run_info.get('underline'):
                    run.underline = True
                if run_info.get('subscript'):
                    run.font.subscript = True
                if run_info.get('superscript'):
                    run.font.superscript = True

            return
        
        # PRIORITY 2: Use formatting_info for smart transfer (legacy approach)
        # Default to True so it works across restarts/without re-importing
        use_smart = getattr(self, 'memoq_smart_formatting', True)
        
        if not formatting_info:
            # No formatting info - just add plain text
            paragraph.add_run(text)
            return
        
        if not use_smart:
            # Explicitly disabled by user (only matters if they just imported)
            paragraph.add_run(text)
            return
        
        # Smart formatting transfer algorithm
        # Step 1: Identify formatted regions in source
        formatted_phrases = []
        for fmt in formatting_info:
            fmt_text = fmt.get('text', '')
            if fmt_text and (fmt.get('bold') or fmt.get('italic') or fmt.get('underline') or fmt.get('color')):
                formatted_phrases.append({
                    'text': fmt_text.strip(),
                    'bold': fmt.get('bold', False),
                    'italic': fmt.get('italic', False),
                    'underline': fmt.get('underline', False),
                    'color': fmt.get('color')
                })
        
        if not formatted_phrases:
            # No formatted text found - add plain text
            paragraph.add_run(text)
            return
        
        # Step 2: Try to find matching phrases in target text
        # Build a list of regions to format in the target
        target_lower = text.lower()
        format_regions = []  # List of (start, end, formatting_dict)
        
        for phrase_info in formatted_phrases:
            source_phrase = phrase_info['text'].lower()
            
            # Try exact match first
            idx = target_lower.find(source_phrase)
            if idx >= 0:
                format_regions.append((idx, idx + len(source_phrase), phrase_info))
                continue
            
            # Try word-by-word matching for multi-word phrases
            source_words = source_phrase.split()
            if len(source_words) > 1:
                # Try to find the first and last word to estimate region
                first_word = source_words[0]
                last_word = source_words[-1]
                
                first_idx = target_lower.find(first_word)
                # Search for last word after first word
                if first_idx >= 0:
                    last_idx = target_lower.find(last_word, first_idx)
                    if last_idx >= 0:
                        # Found a plausible region
                        end_idx = last_idx + len(last_word)
                        # Only use if the region is reasonable (not too different in length)
                        region_len = end_idx - first_idx
                        source_len = len(source_phrase)
                        if 0.3 <= region_len / source_len <= 3.0:
                            format_regions.append((first_idx, end_idx, phrase_info))
        
        if not format_regions:
            # No matches found - just add plain text
            paragraph.add_run(text)
            return
        
        # Step 3: Sort regions and merge overlaps
        format_regions.sort(key=lambda x: x[0])
        
        # Step 4: Build the formatted paragraph with runs
        current_pos = 0
        for start, end, fmt_info in format_regions:
            # Ensure we don't go backwards
            if start < current_pos:
                start = current_pos
            if end <= start:
                continue
            
            # Add unformatted text before this region
            if start > current_pos:
                unformatted_text = text[current_pos:start]
                paragraph.add_run(unformatted_text)
            
            # Add formatted text
            formatted_text = text[start:end]
            run = paragraph.add_run(formatted_text)
            
            if fmt_info.get('bold'):
                run.bold = True
            if fmt_info.get('italic'):
                run.italic = True
            if fmt_info.get('underline'):
                run.underline = True
            
            # Apply color if present
            if fmt_info.get('color'):
                try:
                    from docx.shared import RGBColor
                    hex_color = fmt_info['color']
                    # Convert "FF0000" hex to RGB
                    if len(hex_color) == 6:
                        r = int(hex_color[0:2], 16)
                        g = int(hex_color[2:4], 16)
                        b = int(hex_color[4:6], 16)
                        run.font.color.rgb = RGBColor(r, g, b)
                except Exception:
                    pass
            
            current_pos = end
        
        # Add any remaining unformatted text
        if current_pos < len(text):
            paragraph.add_run(text[current_pos:])

    def import_memoq_xliff(self):
        """Import memoQ XLIFF (.mqxliff) file"""
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select memoQ XLIFF File",
            "",
            "memoQ XLIFF (*.mqxliff);;All Files (*.*)"
        )
        
        if not file_path:
            return

        # Check if this is a re-import of the same file
        reimport_mode, preserved_id, preserved_tm, preserved_tb = self._check_reimport_same_file(
            file_path, ['current_project.mqxliff_source_path']
        )
        if reimport_mode is None:  # User cancelled
            return

        try:
            from modules.mqxliff_handler import MQXLIFFHandler

            # Load the file
            handler = MQXLIFFHandler()
            if not handler.load(file_path):
                QMessageBox.critical(
                    self, "Error",
                    "Failed to load memoQ XLIFF file."
                )
                return
            
            # Extract segments (including targets for pretranslated files)
            mqxliff_segments = handler.extract_bilingual_segments()

            if not mqxliff_segments:
                QMessageBox.warning(
                    self, "No Segments",
                    "No segments found in the memoQ XLIFF file."
                )
                return

            # Count pretranslated segments
            pretranslated_count = sum(1 for s in mqxliff_segments if s.get('target', '').strip())

            # Convert to internal Segment format. The handler already maps
            # memoQ's status to a Workbench one; the id is the segment's
            # position, which the export pairs with the file's units.
            segments = []
            for i, mq_seg in enumerate(mqxliff_segments):
                status = mq_seg.get('status', 'not_started')
                if status not in STATUSES:
                    status = 'not_started'

                segment = Segment(
                    id=i + 1,
                    source=mq_seg.get('source', ''),
                    target=mq_seg.get('target', ''),
                    status=status,
                    match_percent=mq_seg.get('match_percent'),
                    memoQ_status=mq_seg.get('mq_status', ''),
                    locked=bool(mq_seg.get('locked')),
                    notes="",
                )
                segments.append(segment)
            doc_count = len({s.get('file') for s in mqxliff_segments})
            
            # Store the handler and original path for round-trip export
            self.mqxliff_handler = handler
            self.mqxliff_source_file = file_path
            
            # Get language codes from handler. A .mqxliff with no <file>
            # element (or no language attributes on it) leaves these as
            # 'unknown'/None, which used to sail straight into the project and
            # make every TM lookup filter on a language that matches nothing.
            # Ask rather than carry a bogus pair forward.
            source_lang = self._normalize_language_code(handler.source_lang)
            target_lang = self._normalize_language_code(handler.target_lang)
            if not self._is_known_language(source_lang) or not self._is_known_language(target_lang):
                self.log(
                    "⚠️ memoQ XLIFF did not declare a usable language pair "
                    f"(source: {handler.source_lang!r}, target: {handler.target_lang!r}) "
                    "— asking the user."
                )
                chosen = self._confirm_language_pair(
                    source_lang if self._is_known_language(source_lang) else None,
                    target_lang if self._is_known_language(target_lang) else None,
                    context=f"File declares: {handler.source_lang} → {handler.target_lang}",
                )
                if not chosen:
                    self.log("✗ User cancelled memoQ XLIFF import at the language prompt")
                    return
                source_lang, target_lang = chosen
            
            # Create new project
            file_name = Path(file_path).stem
            self.current_project = Project(
                name=file_name,
                segments=segments,
                source_lang=source_lang,
                target_lang=target_lang
            )

            # Apply preserved settings if re-importing
            self._apply_reimport_settings(self.current_project, reimport_mode, preserved_id, preserved_tm, preserved_tb)

            # Store memoQ XLIFF source path in project for persistence across saves
            self.current_project.mqxliff_source_path = file_path

            # Sync global language settings with imported project languages
            self.source_language = source_lang
            self.target_language = target_lang

            # Update UI
            self.project_file_path = None
            self.project_modified = True

            # CRITICAL: Update _original_segment_order for new import
            self._original_segment_order = self.current_project.segments.copy()

            self.update_window_title()
            # v1.10.150: wrap the slow grid-load in the shared progress dialog so
            # the user sees activity instead of "Not Responding" on large files.
            # (Minimal wrap: subsequent fast finalise steps stay outside the dialog.)
            _n_segs = len(self.current_project.segments) if self.current_project else 0
            with _ImportProgressDialog(
                self, title="Importing memoQ XLIFF",
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

            # Log success
            self.log(f"✓ Imported {len(segments)} segments from memoQ XLIFF: {Path(file_path).name}")
            if doc_count > 1:
                self.log(f"  From {doc_count} documents (memoQ view)")
            self.log(f"  Source: {source_lang}, Target: {target_lang}")
            if pretranslated_count > 0:
                self.log(f"  Pretranslated: {pretranslated_count} segments with target text")

            # Build message with pretranslation info
            msg = f"Successfully imported {len(segments)} segment(s) from memoQ XLIFF.\n\nLanguages: {source_lang} → {target_lang}"
            if doc_count > 1:
                msg += f"\n\nDocuments: {doc_count}"
            if pretranslated_count > 0:
                msg += f"\n\nPretranslated: {pretranslated_count} segment(s) with target text loaded."

            # v1.10.153: build the termbase index and prefetch
            # TM/MT/LLM matches for the first 50 segments — without
            # this, cell-clicks fall through to per-word SQL queries
            # (2-34 s per click on a 40+ termbase database).
            self._finalise_import_with_indexes()
            QMessageBox.information(
                self, "Import Successful",
                msg
            )
        except Exception as e:
            self.log(f"❌ Error importing memoQ XLIFF: {e}")
            QMessageBox.critical(
                self, "Import Error",
                f"Error importing memoQ XLIFF file:\n\n{str(e)}"
            )

    def export_memoq_rtf(self):
        """Export to memoQ bilingual RTF format with translations"""
        # Check if we have segments
        if not self.current_project or not self.current_project.segments:
            QMessageBox.warning(self, "No Data", "No segments to export")
            return

        # Check if a memoQ RTF source file was imported
        memoq_rtf_source = None
        if hasattr(self, 'memoq_rtf_source_file') and self.memoq_rtf_source_file:
            memoq_rtf_source = self.memoq_rtf_source_file
        elif hasattr(self.current_project, 'memoq_rtf_source_path') and self.current_project.memoq_rtf_source_path:
            memoq_rtf_source = self.current_project.memoq_rtf_source_path
            if Path(memoq_rtf_source).exists():
                self.memoq_rtf_source_file = memoq_rtf_source
                self.log(f"✓ Restored memoQ RTF source from project: {Path(memoq_rtf_source).name}")
            else:
                memoq_rtf_source = None

        if not memoq_rtf_source:
            # Prompt user to select the original memoQ bilingual RTF file
            reply = QMessageBox.question(
                self, "Select memoQ Source File",
                "To export to memoQ RTF format, please select the original memoQ bilingual RTF file.\n\n"
                "This is the file you originally imported from memoQ.\n\n"
                "Would you like to select it now?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )

            if reply == QMessageBox.StandardButton.Yes:
                file_path, _ = QFileDialog.getOpenFileName(
                    self,
                    "Select Original memoQ Bilingual RTF",
                    "",
                    "RTF Files (*.rtf);;All Files (*.*)"
                )

                if file_path:
                    self.memoq_rtf_source_file = file_path
                    self.log(f"✓ memoQ RTF source file set: {Path(file_path).name}")
                else:
                    self.log("Export cancelled - no source file selected")
                    return
            else:
                self.log("Export cancelled")
                return

        try:
            from modules.memoqrtf_handler import MemoQRTFHandler

            segments = self._segments_in_document_order()
            translations = [seg.target for seg in segments]

            if not translations or all(not t.strip() for t in translations):
                QMessageBox.warning(self, "Warning", "No translations found to export.")
                return

            # Load or reuse the RTF handler
            if hasattr(self, 'memoq_rtf_handler') and self.memoq_rtf_handler:
                handler = self.memoq_rtf_handler
            else:
                handler = MemoQRTFHandler()
                if not handler.load(self.memoq_rtf_source_file):
                    QMessageBox.critical(
                        self, "Error",
                        "Failed to load the original memoQ bilingual RTF file."
                    )
                    return
                self.memoq_rtf_handler = handler

            # Update translations in handler
            segments_updated = 0
            for i, translation in enumerate(translations):
                if i < len(handler.segments):
                    handler.segments[i].target_text = translation
                    if translation.strip():
                        segments_updated += 1

            # Prompt for output file
            default_name = Path(self.memoq_rtf_source_file).stem + "_translated.rtf"
            default_dir = str(Path(self.memoq_rtf_source_file).parent)

            output_path, _ = QFileDialog.getSaveFileName(
                self,
                "Save Translated memoQ Bilingual RTF",
                str(Path(default_dir) / default_name),
                "RTF Files (*.rtf);;All Files (*.*)"
            )

            if not output_path:
                self.log("Export cancelled by user")
                return

            # Save the modified RTF
            if handler.save(output_path):
                self.log(f"✓ Exported memoQ RTF: {segments_updated} translations to {Path(output_path).name}")

                QMessageBox.information(
                    self, "Export Successful",
                    f"Exported {segments_updated} translation(s) to memoQ bilingual RTF.\n\n"
                    f"File: {Path(output_path).name}\n\n"
                    f"You can now import this file back into memoQ."
                )
            else:
                QMessageBox.critical(self, "Export Error", "Failed to save the translated RTF file.")

        except ImportError as e:
            QMessageBox.critical(
                self, "Export Error",
                f"Failed to import memoQ RTF handler module:\n\n{str(e)}"
            )
        except Exception as e:
            QMessageBox.critical(self, "Export Error", f"Failed to export memoQ bilingual RTF:\n\n{str(e)}")
            self.log(f"✗ memoQ RTF export failed: {str(e)}")
            import traceback
            traceback.print_exc()

    def export_memoq_xliff(self):
        """Export to memoQ XLIFF format with translations"""
        # Check if we have segments
        if not self.current_project or not self.current_project.segments:
            QMessageBox.warning(self, "No Data", "No segments to export")
            return
        
        # Check if a memoQ XLIFF source file was imported - check both instance var and project
        mqxliff_source = None
        if hasattr(self, 'mqxliff_source_file') and self.mqxliff_source_file:
            mqxliff_source = self.mqxliff_source_file
        elif hasattr(self.current_project, 'mqxliff_source_path') and self.current_project.mqxliff_source_path:
            mqxliff_source = self.current_project.mqxliff_source_path
            # Verify it still exists
            if Path(mqxliff_source).exists():
                self.mqxliff_source_file = mqxliff_source
                self.log(f"✓ Restored memoQ XLIFF source from project: {Path(mqxliff_source).name}")
                # Reload the handler
                try:
                    from modules.mqxliff_handler import MQXLIFFHandler
                    self.mqxliff_handler = MQXLIFFHandler()
                    if not self.mqxliff_handler.load(mqxliff_source):
                        QMessageBox.critical(self, "Error", "Failed to reload memoQ XLIFF source file")
                        return
                except Exception as e:
                    QMessageBox.critical(self, "Error", f"Failed to reload memoQ XLIFF file: {str(e)}")
                    return
            else:
                mqxliff_source = None
        
        if not mqxliff_source:
            # Prompt user to select the original memoQ XLIFF file
            reply = QMessageBox.question(
                self, "Select memoQ XLIFF Source File",
                "To export to memoQ XLIFF format, please select the original memoQ XLIFF file.\n\n"
                "This is the file you originally imported from memoQ.\n\n"
                "Would you like to select it now?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            
            if reply == QMessageBox.StandardButton.Yes:
                file_path, _ = QFileDialog.getOpenFileName(
                    self,
                    "Select Original memoQ XLIFF File",
                    "",
                    "memoQ XLIFF (*.mqxliff);;All Files (*.*)"
                )
                
                if file_path:
                    self.mqxliff_source_file = file_path
                    self.log(f"✓ memoQ XLIFF source file set: {Path(file_path).name}")
                    
                    # Load the handler
                    try:
                        from modules.mqxliff_handler import MQXLIFFHandler
                        self.mqxliff_handler = MQXLIFFHandler()
                        if not self.mqxliff_handler.load(file_path):
                            QMessageBox.critical(self, "Error", "Failed to load memoQ XLIFF source file")
                            return
                    except Exception as e:
                        QMessageBox.critical(self, "Error", f"Failed to load memoQ XLIFF file: {str(e)}")
                        return
                else:
                    self.log("Export cancelled - no source file selected")
                    return
            else:
                self.log("Export cancelled")
                return
        
        # Get output file path
        default_name = Path(self.mqxliff_source_file).stem + "_translated.mqxliff"
        output_path, _ = QFileDialog.getSaveFileName(
            self,
            "Save Translated memoQ XLIFF",
            default_name,
            "memoQ XLIFF (*.mqxliff);;All Files (*.*)"
        )
        
        if not output_path:
            return
        
        try:
            # Translations and statuses in document order: the handler pairs
            # the n-th segment with the n-th unit of the file
            segments = self._segments_in_document_order()
            translations = [seg.target for seg in segments]
            statuses = [seg.status for seg in segments]

            # Update the handler with translations
            updated_count = self.mqxliff_handler.update_target_segments(translations, statuses)
            
            # Save the updated file
            if self.mqxliff_handler.save(output_path):
                self.log(f"✓ Exported {updated_count} changed segments to memoQ XLIFF: {Path(output_path).name}")
                QMessageBox.information(
                    self, "Export Successful",
                    f"Exported to memoQ XLIFF: {updated_count} segment(s) with a new translation or status.\n\n"
                    f"File: {Path(output_path).name}\n\n"
                    f"You can now import this file back into memoQ."
                )
            else:
                QMessageBox.critical(
                    self, "Export Error",
                    "Failed to save memoQ XLIFF file."
                )
        except Exception as e:
            self.log(f"❌ Error exporting memoQ XLIFF: {e}")
            QMessageBox.critical(
                self, "Export Error",
                f"Error exporting memoQ XLIFF file:\n\n{str(e)}"
            )
