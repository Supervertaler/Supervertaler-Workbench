"""CafeTran bilingual DOCX, import and export.

Part of the main window (SupervertalerQt), moved here unchanged. The
methods run as the main window's own: `self` is the SupervertalerQt.
"""

import traceback
from PyQt6.QtWidgets import QFileDialog, QMessageBox
from modules.grid_editors import TagHighlighter
from modules.main_dialogs import _ImportProgressDialog
from modules.segment_model import Project, Segment
from modules.statuses import DEFAULT_STATUS, STATUSES
from pathlib import Path


class CafeTranMixin:
    def import_cafetran_bilingual(self):
        """Import CafeTran bilingual DOCX file (table format with pipe symbols)"""
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select CafeTran Bilingual DOCX File",
            "",
            "Word Documents (*.docx);;All Files (*.*)"
        )
        
        if not file_path:
            return

        # Check if this is a re-import of the same file
        reimport_mode, preserved_id, preserved_tm, preserved_tb = self._check_reimport_same_file(
            file_path, ['current_project.cafetran_source_path']
        )
        if reimport_mode is None:  # User cancelled
            return

        try:
            from modules.cafetran_docx_handler import CafeTranDOCXHandler

            # Check if this is a valid CafeTran bilingual DOCX
            if not CafeTranDOCXHandler.is_cafetran_bilingual_docx(file_path):
                QMessageBox.warning(
                    self, "Invalid Format",
                    "This file does not appear to be a CafeTran bilingual DOCX.\n\n"
                    "Expected format: Table with columns ID | Source | Target | Notes | *"
                )
                return
            
            # Load the file
            handler = CafeTranDOCXHandler()
            if not handler.load(file_path):
                QMessageBox.critical(
                    self, "Error",
                    "Failed to load CafeTran bilingual DOCX file."
                )
                return
            
            # Extract segments
            cafetran_segments = handler.extract_source_segments()
            
            if not cafetran_segments:
                QMessageBox.warning(
                    self, "No Segments",
                    "No segments found in the CafeTran bilingual DOCX file."
                )
                return
            
            # Convert to internal Segment format - preserve pipe symbols for round-trip
            segments = []
            for i, ct_seg in enumerate(cafetran_segments):
                segment = Segment(
                    id=i + 1,
                    source=ct_seg.source_with_pipes,  # Preserve pipe symbols for formatting
                    target=ct_seg.target_with_pipes if ct_seg.target_with_pipes else "",
                    status=STATUSES["pretranslated"].key if ct_seg.target_with_pipes else DEFAULT_STATUS.key,
                    notes=ct_seg.notes,
                )
                segments.append(segment)
            
            # Store the handler and original path for round-trip export
            self.cafetran_handler = handler
            self.cafetran_source_file = file_path
            
            # Language pair (issue #200): CafeTran doesn't name it, so read the
            # Word language tags on the source and target columns and let the
            # user confirm. This used to take the main window's language combos,
            # which don't exist, so every CafeTran project became English → Dutch.
            try:
                detected_src, detected_tgt = handler.detect_language_pair()
            except Exception as e:
                print(f"[CafeTran import] language detection error: {e}")
                detected_src = detected_tgt = None
            chosen = self._confirm_language_pair(
                detected_src, detected_tgt, detected=True,
                context=self.tr("File: {}").format(Path(file_path).name))
            if not chosen:
                self.log("✗ User cancelled CafeTran import at the language prompt")
                return
            cafetran_source_lang, cafetran_target_lang = chosen

            # Enable CafeTran-specific highlighting (red pipe symbols)
            TagHighlighter._is_cafetran_project = True
            
            # Create new project
            file_name = Path(file_path).stem
            self.current_project = Project(
                name=file_name,
                segments=segments,
                source_lang=cafetran_source_lang,
                target_lang=cafetran_target_lang
            )

            # Apply preserved settings if re-importing
            self._apply_reimport_settings(self.current_project, reimport_mode, preserved_id, preserved_tm, preserved_tb)

            # Store CafeTran source path in project for persistence across saves
            self.current_project.cafetran_source_path = file_path

            # Sync global language settings with imported project languages
            self.source_language = self.current_project.source_lang
            self.target_language = self.current_project.target_lang

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
                self, title="Importing CafeTran bilingual DOCX",
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
            target_lang = self.current_project.target_lang if self.current_project else 'nl'
            self._initialize_spellcheck_for_target_language(target_lang)

            # Log success
            self.log(f"✓ Imported {len(segments)} segments from CafeTran bilingual DOCX: {Path(file_path).name}")
            
            # v1.10.153: build the termbase index and prefetch
            # TM/MT/LLM matches for the first 50 segments — without
            # this, cell-clicks fall through to per-word SQL queries
            # (2-34 s per click on a 40+ termbase database).
            self._finalise_import_with_indexes()
            QMessageBox.information(
                self, "Import Successful",
                f"Successfully imported {len(segments)} segment(s) from CafeTran bilingual DOCX.\n\n"
                f"File: {Path(file_path).name}\n\n"
                f"Note: Pipe symbol formatting (|) is preserved for export.\n"
                f"You can export back to CafeTran format after translation."
            )
            
        except ImportError:
            QMessageBox.critical(
                self, "Missing Dependency",
                "The 'python-docx' library is required for CafeTran bilingual DOCX import.\n\n"
                "Install it with: pip install python-docx"
            )
        except Exception as e:
            QMessageBox.critical(self, "Import Error", f"Failed to import CafeTran bilingual DOCX:\n\n{str(e)}")
            self.log(f"✗ CafeTran import failed: {str(e)}")
            import traceback
            traceback.print_exc()

    def export_cafetran_bilingual(self):
        """Export to CafeTran bilingual DOCX format with translations"""
        # Check if we have segments
        if not self.current_project or not self.current_project.segments:
            QMessageBox.warning(self, "No Data", "No segments to export")
            return
        
        # Check if a CafeTran source file was imported - check both instance var and project
        cafetran_source = None
        if hasattr(self, 'cafetran_source_file') and self.cafetran_source_file:
            cafetran_source = self.cafetran_source_file
        elif hasattr(self.current_project, 'cafetran_source_path') and self.current_project.cafetran_source_path:
            cafetran_source = self.current_project.cafetran_source_path
            # Verify it still exists
            if Path(cafetran_source).exists():
                self.cafetran_source_file = cafetran_source
                self.log(f"✓ Restored CafeTran source from project: {Path(cafetran_source).name}")
                # Reload the handler
                try:
                    from modules.cafetran_docx_handler import CafeTranDOCXHandler
                    self.cafetran_handler = CafeTranDOCXHandler()
                    if not self.cafetran_handler.load(cafetran_source):
                        QMessageBox.critical(self, "Error", "Failed to reload CafeTran source file")
                        return
                    self.cafetran_handler.extract_source_segments()
                except Exception as e:
                    QMessageBox.critical(self, "Error", f"Failed to reload CafeTran file: {str(e)}")
                    return
            else:
                cafetran_source = None
        
        if not cafetran_source:
            # Prompt user to select the original CafeTran bilingual file
            reply = QMessageBox.question(
                self, "Select CafeTran Source File",
                "To export to CafeTran format, please select the original CafeTran bilingual DOCX file.\n\n"
                "This is the file you originally imported from CafeTran.\n\n"
                "Would you like to select it now?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            
            if reply == QMessageBox.StandardButton.Yes:
                file_path, _ = QFileDialog.getOpenFileName(
                    self,
                    "Select Original CafeTran Bilingual DOCX",
                    "",
                    "Word Documents (*.docx);;All Files (*.*)"
                )
                
                if file_path:
                    self.cafetran_source_file = file_path
                    self.log(f"✓ CafeTran source file set: {Path(file_path).name}")
                    
                    # Load the handler
                    try:
                        from modules.cafetran_docx_handler import CafeTranDOCXHandler
                        self.cafetran_handler = CafeTranDOCXHandler()
                        if not self.cafetran_handler.load(file_path):
                            QMessageBox.critical(self, "Error", "Failed to load CafeTran source file")
                            return
                        self.cafetran_handler.extract_source_segments()
                    except Exception as e:
                        QMessageBox.critical(self, "Error", f"Failed to load CafeTran file: {str(e)}")
                        return
                else:
                    self.log("Export cancelled - no source file selected")
                    return
            else:
                self.log("Export cancelled")
                return
        
        try:
            from modules.cafetran_docx_handler import CafeTranDOCXHandler
            
            segments = self._segments_in_document_order()
            translations = [seg.target for seg in segments]
            
            if not translations or all(not t.strip() for t in translations):
                QMessageBox.warning(self, "Warning", "No translations found to export.")
                return
            
            # Update the handler with translations
            if hasattr(self, 'cafetran_handler') and self.cafetran_handler:
                handler = self.cafetran_handler
            else:
                handler = CafeTranDOCXHandler()
                if not handler.load(self.cafetran_source_file):
                    QMessageBox.critical(self, "Error", "Failed to load CafeTran source file")
                    return
                handler.extract_source_segments()
            
            # Update target segments with translations
            handler.update_target_segments(translations)
            
            # Prompt user to save the file
            source_path = Path(self.cafetran_source_file)
            default_name = str(self._export_dir_beside(source_path) / (source_path.stem + "_translated.docx"))
            save_path, _ = QFileDialog.getSaveFileName(
                self,
                "Save CafeTran Bilingual DOCX",
                default_name,
                "Word Documents (*.docx);;All Files (*.*)"
            )
            
            if save_path:
                if handler.save(save_path):
                    self.log(f"✓ Exported {len(translations)} translations to CafeTran bilingual DOCX: {Path(save_path).name}")
                    
                    QMessageBox.information(
                        self, "Export Successful",
                        f"Successfully exported {len(translations)} translation(s) to CafeTran bilingual DOCX!\n\n"
                        f"File saved: {Path(save_path).name}\n\n"
                        f"✓ Pipe symbol formatting preserved\n"
                        f"✓ Table structure preserved\n\n"
                        f"You can now import this file back into CafeTran."
                    )
                else:
                    QMessageBox.critical(self, "Error", "Failed to save CafeTran bilingual DOCX")
            
        except ImportError:
            QMessageBox.critical(
                self, "Missing Dependency",
                "The 'python-docx' library is required for CafeTran bilingual DOCX export.\n\n"
                "Install it with: pip install python-docx"
            )
        except Exception as e:
            QMessageBox.critical(self, "Export Error", f"Failed to export CafeTran bilingual DOCX:\n\n{str(e)}")
            self.log(f"✗ CafeTran export failed: {str(e)}")
            import traceback
            traceback.print_exc()
