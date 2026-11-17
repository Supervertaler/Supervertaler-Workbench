"""Déjà Vu bilingual RTF, import and export.

Part of the main window (SupervertalerQt), moved here unchanged. The
methods run as the main window's own: `self` is the SupervertalerQt.
"""

import traceback
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QLabel,
    QMessageBox,
    QVBoxLayout,
)
from modules.main_dialogs import _ImportProgressDialog
from modules.segment_model import Project, Segment
from pathlib import Path


class DejaVuMixin:
    def import_dejavu_bilingual(self):
        """Import Déjà Vu X3 bilingual RTF file (table format)"""
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Déjà Vu X3 Bilingual RTF File",
            "",
            "RTF Files (*.rtf);;All Files (*.*)"
        )
        
        if not file_path:
            return
        
        # Show info dialog about the Déjà Vu workflow
        from PyQt6.QtWidgets import QDialog, QVBoxLayout, QLabel, QDialogButtonBox
        
        dialog = QDialog(self)
        dialog.setWindowTitle(self.tr("Déjà Vu X3 Bilingual Import"))
        dialog.setMinimumWidth(450)
        layout = QVBoxLayout(dialog)
        
        # Info text
        info_label = QLabel(
            "This workflow imports Déjà Vu X3 bilingual RTF for round-tripping.\n\n"
            "• Déjà Vu inline tags (e.g., {00108}text{00109}) will be preserved\n"
            "• The project can be exported back to Déjà Vu RTF format\n"
            "• Column structure: ID | Source | Target | Comments"
        )
        info_label.setWordWrap(True)
        info_label.setStyleSheet("color: #666; margin-bottom: 10px;")
        layout.addWidget(info_label)
        
        # Note about tags
        note_label = QLabel(
            self.tr("ℹ️ Note: Déjà Vu tags {NNNNN} will be highlighted in the translation grid.")
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

        try:
            from modules.dejavurtf_handler import DejaVuRTFHandler
            
            # Load the bilingual RTF
            handler = DejaVuRTFHandler()
            if not handler.load(file_path):
                QMessageBox.critical(
                    self, "Error",
                    "Failed to load the Déjà Vu RTF file.\n\nPlease check the file format."
                )
                return
            
            # Extract segments
            segments_data = handler.extract_source_segments()
            
            if not segments_data:
                QMessageBox.warning(self, "Warning", "No segments found in the Déjà Vu RTF file.")
                return
            
            # Language pair (issue #200): the handler guesses it from how often
            # each RTF language code occurs, so let the user confirm it. When it
            # found nothing it used to fall back to Dutch → Spanish silently.
            try:
                detected_src, detected_tgt = handler.detect_language_pair()
            except Exception as e:
                print(f"[Déjà Vu import] language detection error: {e}")
                detected_src = detected_tgt = None
            chosen = self._confirm_language_pair(
                detected_src, detected_tgt, detected=True,
                context=self.tr("File: {}").format(Path(file_path).name))
            if not chosen:
                self.log("✗ User cancelled Déjà Vu import at the language prompt")
                return
            source_lang, target_lang = chosen
            # Export tags the translations with the handler's target language,
            # so keep it in step with a correction (but keep a detected regional
            # variant such as "Spanish (MX)" when the language is unchanged).
            from modules import language_codes as _lc
            if not _lc.same_language(handler.source_lang, source_lang):
                handler.source_lang = _lc.english_name(source_lang) or source_lang
            if not _lc.same_language(handler.target_lang, target_lang):
                handler.target_lang = _lc.english_name(target_lang) or target_lang
            
            # Store the handler for later export
            self.dejavu_handler = handler
            self.dejavu_source_file = file_path
            
            # Create project
            project_name = Path(file_path).stem
            self.current_project = Project(
                name=project_name,
                source_lang=source_lang,
                target_lang=target_lang,
                segments=[]
            )
            
            # Store Déjà Vu source path in project for persistence
            self.current_project.dejavu_source_path = file_path

            # Sync global language settings with imported project languages
            self.source_language = source_lang
            self.target_language = target_lang

            # Create segments
            for idx, seg_data in enumerate(segments_data):
                segment = Segment(
                    id=idx + 1,
                    source=seg_data.source_text,
                    target=seg_data.target_text or '',
                    status="draft" if seg_data.target_text.strip() else "not_started",
                    type="para",
                    notes=seg_data.comment or '',
                    dejavu_segment_id=seg_data.segment_id,
                    dejavu_row_index=seg_data.row_index,
                )
                self.current_project.segments.append(segment)

            # CRITICAL: Update _original_segment_order for new import
            self._original_segment_order = self.current_project.segments.copy()

            # Update UI
            self.project_file_path = None
            self.project_modified = True
            self.update_window_title()
            # v1.10.150: wrap the slow grid-load in the shared progress dialog so
            # the user sees activity instead of "Not Responding" on large files.
            # (Minimal wrap: subsequent fast finalise steps stay outside the dialog.)
            _n_segs = len(self.current_project.segments) if self.current_project else 0
            with _ImportProgressDialog(
                self, title="Importing DéjàVu bilingual",
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

            self.log(f"✓ Imported Déjà Vu X3 bilingual RTF: {len(segments_data)} segments from {Path(file_path).name}")

            # Store current document path for AI Assistant
            self.current_document_path = file_path

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
                f"Imported {len(segments_data)} segment(s) from Déjà Vu X3 bilingual RTF.\n\n"
                f"File: {Path(file_path).name}\n"
                f"Languages: {source_lang.upper()} → {target_lang.upper()}\n\n"
                f"Déjà Vu tags have been preserved in the source text."
            )
            
        except ImportError as e:
            QMessageBox.critical(
                self, "Import Error",
                f"Failed to import Déjà Vu RTF module:\n\n{str(e)}"
            )
        except Exception as e:
            QMessageBox.critical(self, "Import Error", f"Failed to import Déjà Vu RTF:\n\n{str(e)}")
            self.log(f"✗ Déjà Vu import failed: {str(e)}")
            import traceback
            traceback.print_exc()

    def export_dejavu_bilingual(self):
        """Export translations back to Déjà Vu X3 bilingual RTF format"""
        if not self.current_project or not self.current_project.segments:
            QMessageBox.warning(self, "No Project", "No project is currently loaded.")
            return

        # Check if we have a Déjà Vu source
        dejavu_source = None

        # First check if handler is already loaded
        if hasattr(self, 'dejavu_handler') and self.dejavu_handler:
            dejavu_source = getattr(self, 'dejavu_source_file', None)

        # If not, check if project has the source path saved
        if not dejavu_source and hasattr(self.current_project, 'dejavu_source_path') and self.current_project.dejavu_source_path:
            dejavu_source = self.current_project.dejavu_source_path

        if not dejavu_source:
            # Prompt user to select the original Déjà Vu bilingual file
            reply = QMessageBox.question(
                self, "Select Déjà Vu Source File",
                "To export to Déjà Vu format, please select the original Déjà Vu bilingual RTF file.\n\n"
                "This is the file you originally exported from Déjà Vu X3.\n\n"
                "Would you like to select it now?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )

            if reply == QMessageBox.StandardButton.Yes:
                file_path, _ = QFileDialog.getOpenFileName(
                    self,
                    "Select Original Déjà Vu Bilingual RTF",
                    "",
                    "RTF Files (*.rtf);;All Files (*.*)"
                )

                if file_path:
                    dejavu_source = file_path
                    self.dejavu_source_file = file_path
                    self.current_project.dejavu_source_path = file_path
                    self.log(f"✓ Déjà Vu source file set: {Path(file_path).name}")
                else:
                    self.log("Export cancelled - no source file selected")
                    return
            else:
                self.log("Export cancelled")
                return

        # Check if source file still exists
        if not Path(dejavu_source).exists():
            reply = QMessageBox.question(
                self, "Source File Not Found",
                f"The original Déjà Vu source file cannot be found:\n\n"
                f"{dejavu_source}\n\n"
                f"Would you like to browse for the file in its new location?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )

            if reply == QMessageBox.StandardButton.Yes:
                file_path, _ = QFileDialog.getOpenFileName(
                    self,
                    "Select Original Déjà Vu Bilingual RTF",
                    "",
                    "RTF Files (*.rtf);;All Files (*.*)"
                )

                if file_path:
                    dejavu_source = file_path
                    self.dejavu_source_file = file_path
                    self.current_project.dejavu_source_path = file_path
                    self.log(f"✓ Déjà Vu source file relocated: {Path(file_path).name}")
                else:
                    self.log("Export cancelled - no source file selected")
                    return
            else:
                self.log("Export cancelled")
                return

        # Reload the handler if needed
        if not hasattr(self, 'dejavu_handler') or not self.dejavu_handler:
            try:
                from modules.dejavurtf_handler import DejaVuRTFHandler
                self.dejavu_handler = DejaVuRTFHandler()
                if not self.dejavu_handler.load(dejavu_source):
                    QMessageBox.critical(self, "Error", "Failed to reload the Déjà Vu source file.")
                    return
                self.dejavu_handler.extract_source_segments()
                self.dejavu_source_file = dejavu_source
                self.log(f"✓ Reloaded Déjà Vu source file for export")
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed to reload Déjà Vu source:\n\n{str(e)}")
                return

        # Suggest output filename
        source_path = Path(self.dejavu_source_file)
        suggested_name = source_path.stem + "_translated" + source_path.suffix

        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Export Déjà Vu Bilingual RTF",
            str(self._export_dir_beside(source_path) / suggested_name),
            "RTF Files (*.rtf);;All Files (*.*)"
        )

        if not file_path:
            return

        try:
            # First sync grid content to project segments
            self._sync_grid_targets_to_segments(self.current_project.segments)
            
            # Collect translations from project segments
            translations = {}
            for segment in self.current_project.segments:
                # Use row_index if available (most reliable for Déjà Vu)
                if segment.dejavu_row_index is not None:
                    if segment.target and segment.target.strip():
                        translations[segment.dejavu_row_index] = segment.target
                # Fallback to segment ID mapping
                elif segment.dejavu_segment_id:
                    if segment.target and segment.target.strip():
                        translations[segment.dejavu_segment_id] = segment.target
            
            self.log(f"Collected {len(translations)} translations from project")

            # Update the handler with translations
            if any(isinstance(k, int) for k in translations.keys()):
                # Use row index method
                row_translations = {k: v for k, v in translations.items() if isinstance(k, int)}
                updated = self.dejavu_handler.update_translations_by_index(row_translations)
            else:
                # Use segment ID method
                updated = self.dejavu_handler.update_translations(translations)

            # Save to new file
            if self.dejavu_handler.save(file_path):
                self.log(f"✓ Exported {updated} translated segments to Déjà Vu RTF: {Path(file_path).name}")

                QMessageBox.information(
                    self, "Export Successful",
                    f"Successfully exported {updated} translation(s) to Déjà Vu bilingual RTF.\n\n"
                    f"File: {Path(file_path).name}\n\n"
                    f"This file can be imported back into Déjà Vu X3.\n"
                    f"Déjà Vu tags have been preserved."
                )
            else:
                QMessageBox.critical(self, "Export Error", "Failed to save the Déjà Vu bilingual RTF file.")

        except Exception as e:
            QMessageBox.critical(self, "Export Error", f"Failed to export Déjà Vu bilingual RTF:\n\n{str(e)}")
            self.log(f"✗ Déjà Vu export failed: {str(e)}")
            import traceback
            traceback.print_exc()
