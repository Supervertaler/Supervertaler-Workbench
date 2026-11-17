"""Projects with several files: importing a folder and exporting each file.

Part of the main window (SupervertalerQt), moved here unchanged. The
methods run as the main window's own: `self` is the SupervertalerQt.
"""

import os
import re
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QComboBox,
    QDialog,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from modules.language_combos import _import_language_pairs, _select_lang_in_combo
from modules.main_dialogs import _ImportProgressDialog
from modules.okapi_import_options import okapi_sidecar_options, okapi_subdoc_category
from modules.segment_model import Project, Segment
from modules.statuses import DEFAULT_STATUS
from modules.styled_widgets import CheckmarkCheckBox, CheckmarkRadioButton
from modules.tag_text import get_docx_language_code, set_docx_language


class MultifileMixin:
    def import_folder_multifile(self):
        """
        Import multiple files from a folder as a single multi-file project.
        
        Supports:
        - DOCX files (monolingual)
        - TXT files (simple text, one segment per line)
        - memoQ bilingual DOCX (if detected)
        
        All project resources (TM, termbases, prompts) apply to all files.
        """
        from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, 
                                      QGroupBox, QComboBox, QPushButton, QScrollArea,
                                      QFileDialog)
        
        # Select folder
        folder_path = QFileDialog.getExistingDirectory(
            self,
            "Select Folder with Files to Import",
            "",
            QFileDialog.Option.ShowDirsOnly
        )
        
        if not folder_path:
            return
        
        # Scan folder for supported files
        supported_extensions = {'.docx': 'docx', '.txt': 'txt', '.md': 'md'}
        found_files = []

        for filename in sorted(os.listdir(folder_path)):
            ext = os.path.splitext(filename)[1].lower()
            if ext in supported_extensions:
                full_path = os.path.join(folder_path, filename)
                found_files.append({
                    'name': filename,
                    'path': full_path,
                    'type': supported_extensions[ext],
                    'size': os.path.getsize(full_path)
                })

        if not found_files:
            QMessageBox.warning(
                self,
                "No Files Found",
                f"No supported files found in folder:\n{folder_path}\n\n"
                f"Supported formats: DOCX, TXT, MD (Markdown)"
            )
            return
        
        # Show import dialog
        dialog = QDialog(self)
        dialog.setWindowTitle(self.tr("📁 Import Folder - Multi-File Project"))
        dialog.setMinimumWidth(600)
        dialog.setMinimumHeight(560)
        
        layout = QVBoxLayout(dialog)
        
        # Info
        info_label = QLabel(
            f"<b>📁 Multi-File Project Import</b><br><br>"
            f"Found <b>{len(found_files)}</b> files in: <i>{os.path.basename(folder_path)}</i><br><br>"
            f"All files will be combined into a single project.<br>"
            f"Project resources (TM, termbases, prompts) will apply to all files.<br>"
            f"You can track progress per file via File \u2192 Project Info."
        )
        info_label.setWordWrap(True)
        info_label.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(info_label)
        
        layout.addSpacing(10)
        
        # File list with checkboxes
        files_group = QGroupBox(f"Files to Import ({len(found_files)} files)")
        files_layout = QVBoxLayout(files_group)
        
        # Create a scroll area for the checkboxes. Keep it compact for a
        # handful of files; it scrolls (up to 300px) when there are many.
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setMinimumHeight(90)
        scroll_area.setMaximumHeight(300)
        scroll_widget = QWidget()
        scroll_layout = QVBoxLayout(scroll_widget)
        scroll_layout.setContentsMargins(5, 5, 5, 5)
        scroll_layout.setSpacing(2)
        
        file_checkboxes = []
        for f in found_files:
            size_kb = f['size'] / 1024
            checkbox = CheckmarkCheckBox(f"{f['name']} ({f['type'].upper()}, {size_kb:.1f} KB)")
            checkbox.setChecked(True)  # Select all by default
            checkbox.setProperty("file_data", f)  # Store file data on checkbox
            file_checkboxes.append(checkbox)
            scroll_layout.addWidget(checkbox)
        
        scroll_layout.addStretch()
        scroll_area.setWidget(scroll_widget)
        files_layout.addWidget(scroll_area)
        
        # Selection buttons
        btn_layout = QHBoxLayout()
        select_all_btn = QPushButton(self.tr("Select All"))
        select_all_btn.clicked.connect(lambda: [cb.setChecked(True) for cb in file_checkboxes])
        deselect_all_btn = QPushButton(self.tr("Deselect All"))
        deselect_all_btn.clicked.connect(lambda: [cb.setChecked(False) for cb in file_checkboxes])
        btn_layout.addWidget(select_all_btn)
        btn_layout.addWidget(deselect_all_btn)
        btn_layout.addStretch()
        files_layout.addLayout(btn_layout)
        
        layout.addWidget(files_group)
        
        # Language pair selection
        lang_group = QGroupBox(self.tr("Language Pair (applies to all files)"))
        lang_layout = QHBoxLayout(lang_group)
        
        languages = _import_language_pairs()
        
        source_label = QLabel(self.tr("Source:"))
        source_combo = QComboBox()
        for name, code in languages:
            source_combo.addItem(name, code)

        # Load last used languages from settings, or use current project, or default to English
        general_settings = self.load_general_settings()
        last_source = general_settings.get('last_import_source_lang',
                                          self.current_project.source_lang if self.current_project else 'en')
        last_target = general_settings.get('last_import_target_lang',
                                          self.current_project.target_lang if self.current_project else 'nl')

        _select_lang_in_combo(source_combo, last_source)

        target_label = QLabel(self.tr("Target:"))
        target_combo = QComboBox()
        for name, code in languages:
            target_combo.addItem(name, code)

        _select_lang_in_combo(target_combo, last_target)

        swap_btn = QPushButton(self.tr("🔄 Swap"))
        swap_btn.setToolTip(self.tr("Swap source and target languages"))

        def _swap_langs():
            s_idx, t_idx = source_combo.currentIndex(), target_combo.currentIndex()
            source_combo.setCurrentIndex(t_idx)
            target_combo.setCurrentIndex(s_idx)
        swap_btn.clicked.connect(_swap_langs)

        lang_layout.addWidget(source_label)
        lang_layout.addWidget(source_combo)
        lang_layout.addWidget(swap_btn)
        lang_layout.addWidget(target_label)
        lang_layout.addWidget(target_combo)
        lang_layout.addStretch()

        layout.addWidget(lang_group)
        
        # Import format options
        format_group = QGroupBox(self.tr("Import Options"))
        format_layout = QVBoxLayout(format_group)
        
        # memoQ format detection
        memoq_checkbox = CheckmarkCheckBox(self.tr("Detect memoQ bilingual format (DOCX files with tables)"))
        memoq_checkbox.setChecked(False)
        memoq_checkbox.setToolTip(self.tr("If checked, DOCX files with bilingual tables will be imported as memoQ bilingual format"))
        format_layout.addWidget(memoq_checkbox)

        # Sentence segmentation for TXT/MD files
        last_segment_setting = general_settings.get('last_import_sentence_segment', False)
        segment_checkbox = CheckmarkCheckBox(self.tr("Split lines into sentences (TXT/MD files)"))
        segment_checkbox.setChecked(last_segment_setting)
        segment_checkbox.setToolTip(
            "When checked, long lines in TXT/MD files will be split into individual\n"
            "sentences for easier translation. Sentences are automatically rejoined on export.\n"
            "Where lines are split is set in Settings → Segmentation Rules."
        )
        format_layout.addWidget(segment_checkbox)

        # Okapi file-type options: which parts of Office documents to import
        # (defaults come from Settings → File Types). Shown in a scroll area
        # so the dialog stays compact.
        _io_label = QLabel(self.tr("Office document parts to import (Okapi):"))
        format_layout.addWidget(_io_label)
        _io_current = self.get_effective_import_options()
        _io_widget, _io_checks = self._build_import_options_widget(_io_current)
        format_layout.addWidget(_io_widget)

        layout.addWidget(format_group)
        
        layout.addSpacing(10)
        
        # Buttons
        button_layout = QHBoxLayout()
        ok_btn = QPushButton(self.tr("📥 Import Selected Files"))
        ok_btn.setStyleSheet("background-color: #4CAF50; color: white; font-weight: bold; padding: 8px 16px; border: none; outline: none;")
        ok_btn.clicked.connect(dialog.accept)
        cancel_btn = QPushButton(self.tr("Cancel"))
        cancel_btn.clicked.connect(dialog.reject)
        
        button_layout.addStretch()
        button_layout.addWidget(cancel_btn)
        button_layout.addWidget(ok_btn)
        layout.addLayout(button_layout)
        
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        
        # Get selected files from checkboxes
        selected_files = []
        for checkbox in file_checkboxes:
            if checkbox.isChecked():
                selected_files.append(checkbox.property("file_data"))
        
        if not selected_files:
            QMessageBox.warning(self, "No Files Selected", "Please select at least one file to import.")
            return
        
        # Get language settings
        source_lang = source_combo.currentData()
        target_lang = target_combo.currentData()
        detect_memoq = memoq_checkbox.isChecked()
        use_sentence_segmentation = segment_checkbox.isChecked()
        # Per-import override of the Okapi file-type options (read by the
        # extract/merge calls via get_effective_import_options()).
        self._import_options = self._read_import_option_widgets(_io_checks)

        # Save selected settings for next time
        general_settings = self.load_general_settings()
        general_settings['last_import_source_lang'] = source_lang
        general_settings['last_import_target_lang'] = target_lang
        general_settings['last_import_sentence_segment'] = use_sentence_segmentation
        self.save_general_settings(general_settings)

        # Import files
        self._import_multifile_project(folder_path, selected_files, source_lang, target_lang, detect_memoq, use_sentence_segmentation)

    def _import_multifile_project(self, folder_path: str, files: list, source_lang: str, target_lang: str, detect_memoq: bool, sentence_segment: bool = False):
        """
        Import multiple files into a single project.

        Args:
            folder_path: Path to the folder containing files
            files: List of file dictionaries with 'name', 'path', 'type'
            source_lang: Source language code
            target_lang: Target language code
            detect_memoq: Whether to detect memoQ bilingual format
            sentence_segment: Whether to split TXT/MD lines into sentences
        """
        from datetime import datetime
        import shutil

        self.log(f"📁 Importing multi-file project from: {os.path.basename(folder_path)}")
        self.log(f"   Files to import: {len(files)}")

        # ── Okapi sidecar pre-flight ──────────────────────────────────
        # DOCX files in this batch always go through the Okapi sidecar.
        # If any DOCX is selected we need the sidecar running before the
        # loop starts; failing fast here is much friendlier than failing
        # halfway through importing a multi-file project.
        has_docx = any(f.get('type') == 'docx' for f in files)
        if has_docx and not self._ensure_okapi_sidecar():
            return

        # Create _source_files folder to store copies of original files
        # This ensures we always have the originals for format-preserving export
        source_backup_folder = os.path.join(folder_path, "_source_files")
        try:
            os.makedirs(source_backup_folder, exist_ok=True)
            self.log(f"   📦 Backing up source files to: _source_files/")
        except Exception as e:
            self.log(f"   ⚠️ Could not create backup folder: {str(e)}")
            source_backup_folder = None

        all_segments = []
        file_metadata = []  # Track file info for the project
        current_segment_id = 1
        extractors_used = []  # Human-readable extractor names, in first-seen order
        _import_opts = self.get_effective_import_options()  # Okapi file-type toggles

        # Progress dialog
        progress_dialog = QProgressDialog("Importing files...", "Cancel", 0, len(files), self)
        progress_dialog.setWindowTitle(self.tr("Importing Multi-File Project"))
        progress_dialog.setWindowModality(Qt.WindowModality.WindowModal)
        progress_dialog.setMinimumDuration(0)
        
        for idx, file_info in enumerate(files):
            if progress_dialog.wasCanceled():
                self.log("❌ Import cancelled by user")
                return
            
            progress_dialog.setValue(idx)
            progress_dialog.setLabelText(f"Importing: {file_info['name']} ({idx+1}/{len(files)})")
            QApplication.processEvents()
            
            file_id = idx + 1
            file_name = file_info['name']
            file_path = file_info['path']
            file_type = file_info['type']
            
            self.log(f"   📄 [{idx+1}/{len(files)}] {file_name}")
            
            try:
                file_segments = []
                
                # Copy original file to backup folder for safe keeping
                backup_path = None
                if source_backup_folder:
                    try:
                        backup_path = os.path.join(source_backup_folder, file_name)
                        shutil.copy2(file_path, backup_path)
                        self.log(f"      📦 Backed up: {file_name}")
                    except Exception as e:
                        self.log(f"      ⚠️ Backup failed: {str(e)}")
                        backup_path = None
                
                if file_type in ('txt', 'md'):
                    # Simple text / Markdown file
                    # Empty lines are preserved as empty segments for structure
                    # (they are hidden in the grid but kept for export fidelity)
                    _extractor_label = "Built-in text reader"
                    if _extractor_label not in extractors_used:
                        extractors_used.append(_extractor_label)
                    with open(file_path, 'r', encoding='utf-8') as f:
                        lines = f.readlines()

                    if sentence_segment:
                        txt_segmenter = self._make_sentence_segmenter(markdown=(file_type == 'md'))
                        for line_num, line in enumerate(lines, 1):
                            text = line.rstrip('\n\r')
                            if not text.strip():
                                file_segments.append(Segment(
                                    id=current_segment_id, source=text, target="",
                                    status=DEFAULT_STATUS.key, file_id=file_id,
                                    file_name=file_name, paragraph_id=line_num,
                                    document_position=line_num
                                ))
                                current_segment_id += 1
                            else:
                                for sentence, separator in txt_segmenter.segment_with_separators(text):
                                    file_segments.append(Segment(
                                        id=current_segment_id, source=sentence, target="",
                                        status=DEFAULT_STATUS.key, file_id=file_id,
                                        file_name=file_name, paragraph_id=line_num,
                                        document_position=line_num, join_before=separator
                                    ))
                                    current_segment_id += 1
                    else:
                        for line_num, line in enumerate(lines, 1):
                            text = line.rstrip('\n\r')
                            file_segments.append(Segment(
                                id=current_segment_id, source=text, target="",
                                status=DEFAULT_STATUS.key, file_id=file_id,
                                file_name=file_name, paragraph_id=line_num,
                                document_position=line_num
                            ))
                            current_segment_id += 1
                
                elif file_type == 'docx':
                    # ── Okapi extraction ─────────────────────────────
                    # Calls the sidecar's /extract endpoint and converts
                    # the result into Segment objects that carry the
                    # okapi_tu_id / okapi_segment_index metadata required
                    # by the round-trip /merge endpoint at export time.
                    self.log(f"      🔧 Extracting via Okapi sidecar…")
                    _okapi_ver = None
                    try:
                        _okapi_ver = self.okapi_sidecar.get_version()
                    except Exception:
                        _okapi_ver = None
                    _extractor_label = f"Okapi sidecar (v{_okapi_ver})" if _okapi_ver else "Okapi sidecar"
                    if _extractor_label not in extractors_used:
                        extractors_used.append(_extractor_label)
                    result = self.okapi_sidecar.extract(
                        file_path,
                        source_lang=source_lang,
                        target_lang=target_lang,
                        segment=True,
                        options=okapi_sidecar_options(_import_opts),
                    )

                    tu_id_to_para = {}
                    next_para = 0
                    skipped_count = 0

                    for seg in result.get('segments', []):
                        tu_id = seg.get('id', '')
                        sub_doc = (seg.get('subDocument') or '')
                        text = seg.get('source', '').strip()
                        tagged_text = (seg.get('sourceWithTags') or '').strip()

                        # Inclusion (comments, hidden, headers/footers, …) is
                        # controlled by the import options sent to Okapi; we
                        # just label each segment with its category.
                        category = okapi_subdoc_category(sub_doc)
                        if not text:
                            skipped_count += 1
                            continue

                        # Same-TU segments share a paragraph id.
                        if tu_id not in tu_id_to_para:
                            tu_id_to_para[tu_id] = next_para
                            next_para += 1
                        para_id = tu_id_to_para[tu_id]

                        # Prefer the tagged form so inline formatting is
                        # visible in the editor (matches single-file path).
                        display_text = tagged_text if tagged_text else text

                        segment = Segment(
                            id=current_segment_id,
                            source=display_text,
                            target="",
                            status=DEFAULT_STATUS.key,
                            type="¶",
                            paragraph_id=para_id,
                            file_id=file_id,
                            file_name=file_name,
                            okapi_tu_id=tu_id,
                            okapi_segment_index=seg.get('segmentIndex', 0),
                            category=category,
                        )
                        file_segments.append(segment)
                        current_segment_id += 1

                    self.log(f"      ✅ Okapi: {len(file_segments)} segments "
                             f"from {result.get('textUnitCount', '?')} TUs"
                             f"{f', skipped {skipped_count} non-body' if skipped_count else ''}")

                    # Import Word comments as actual comments, if requested.
                    if _import_opts.get('word_comments_mode') == 'comments':
                        self._attach_docx_comments(file_segments, file_path)

                # Track file metadata - use backup path if available, otherwise original
                stored_path = backup_path if backup_path and os.path.exists(backup_path) else file_path
                file_meta = {
                    'id': file_id,
                    'name': file_name,
                    'path': stored_path,
                    'type': file_type,
                    'segment_count': len(file_segments),
                    'start_segment_id': file_segments[0].id if file_segments else None,
                    'end_segment_id': file_segments[-1].id if file_segments else None
                }
                file_metadata.append(file_meta)
                
                all_segments.extend(file_segments)
                self.log(f"      ✓ {len(file_segments)} segments")
                
            except Exception as e:
                self.log(f"      ❌ Error: {str(e)}")
                QMessageBox.warning(
                    self,
                    "Import Error",
                    f"Failed to import file:\n{file_name}\n\nError: {str(e)}\n\nContinuing with remaining files..."
                )
        
        progress_dialog.setValue(len(files))
        progress_dialog.close()
        
        if not all_segments:
            QMessageBox.warning(self, "No Segments", "No segments were imported from the selected files.")
            return
        
        # Create multi-file project
        project = Project(
            name=f"Multi-File Project - {os.path.basename(folder_path)}",
            source_lang=source_lang,
            target_lang=target_lang,
            segments=all_segments,
            is_multifile=True,
            files=file_metadata
        )

        # Tag the project as Okapi-imported when at least one DOCX was
        # imported. Mixed projects (DOCX + TXT/MD) still get the tag –
        # the export path routes per-file by file_type, so TXT/MD files
        # use their normal text-based export.
        if has_docx:
            project.import_engine = "okapi"
            project.import_options = _import_opts

        # Set as current project
        self.current_project = project
        self.current_document_path = folder_path

        # CRITICAL: Update _original_segment_order for new import
        self._original_segment_order = self.current_project.segments.copy()

        # Load into grid
        # v1.10.150: wrap the slow grid-load in the shared progress dialog so
        # the user sees activity instead of "Not Responding" on large files.
        # (Minimal wrap: subsequent fast finalise steps stay outside the dialog.)
        _n_segs = len(self.current_project.segments) if self.current_project else 0
        with _ImportProgressDialog(
            self, title="Importing folder (multi-file project)",
            initial_label=f"Loading {_n_segs:,} segments into grid…",
            initial_total=max(_n_segs, 1),
        ) as _prog:
            self.load_segments_to_grid(progress_callback=_prog.grid_callback)

        # Initialize TM and clear stale resources from previous project
        self.initialize_tm_database()
        self._clear_caches_after_import()
        self._deactivate_all_resources_for_new_project()

        # Update UI
        self.update_window_title()
        
        # Update file filter dropdown for multi-file projects
        if hasattr(self, '_update_file_filter_combo'):
            self._update_file_filter_combo()
        
        # Log summary
        self.log(f"✓ Multi-file project created:")
        self.log(f"   Total segments: {len(all_segments)}")
        self.log(f"   Files: {len(file_metadata)}")
        self.log(f"   Language pair: {source_lang.upper()} → {target_lang.upper()}")
        
        # v1.10.153: build the termbase index and prefetch TM/MT/LLM
        # matches for the first 50 segments — without this, cell-clicks
        # fall through to per-word SQL queries (2-34 s per click on a
        # 40+ termbase database).
        self._finalise_import_with_indexes()

        # Show success message with summary
        file_summary = "\n".join([f"  • {f['name']}: {f['segment_count']} segments" for f in file_metadata[:10]])
        if len(file_metadata) > 10:
            file_summary += f"\n  ... and {len(file_metadata) - 10} more files"

        QMessageBox.information(
            self,
            "Multi-File Import Complete",
            f"Successfully imported {len(all_segments)} segments from {len(file_metadata)} files.\n\n"
            f"Language pair: {source_lang.upper()} → {target_lang.upper()}\n\n"
            f"{f'Extracted via: ' + ', '.join(extractors_used) + chr(10) + chr(10) if extractors_used else ''}"
            f"Files:\n{file_summary}\n\n"
            f"Use File \u2192 Project Info to track translation progress per file."
        )

    def relocate_source_folder(self):
        """
        Relocate the source folder for a multi-file project.
        
        This allows users to repoint to moved/renamed source folders so that
        DOCX exports can preserve original formatting.
        """
        from PyQt6.QtWidgets import QFileDialog, QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTableWidget, QTableWidgetItem, QHeaderView
        
        if not self.current_project:
            QMessageBox.warning(self, "No Project", "Please open a project first.")
            return
        
        # Check if this is a multi-file project
        is_multifile = getattr(self.current_project, 'is_multifile', False)
        files = getattr(self.current_project, 'files', [])
        
        if not is_multifile or not files:
            QMessageBox.information(
                self,
                "Single-File Project",
                "This feature is for multi-file projects only.\n\n"
                "Multi-file projects are created via:\n"
                "Project → Import → Folder (Multiple Files)..."
            )
            return
        
        # Check current status of source files
        missing_count = 0
        found_count = 0
        for f in files:
            path = f.get('path', '')
            if path:
                path = os.path.normpath(path)
                if os.path.exists(path):
                    found_count += 1
                else:
                    missing_count += 1
            else:
                missing_count += 1
        
        # Ask user to select new folder
        info_text = (
            f"Current source file status:\n"
            f"  ✓ Found: {found_count} files\n"
            f"  ✗ Missing: {missing_count} files\n\n"
            f"Select the folder containing the original source files.\n"
            f"Files will be matched by filename."
        )
        
        result = QMessageBox.question(
            self,
            "🔗 Relocate Source Folder",
            info_text + "\n\nProceed to select folder?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        
        if result != QMessageBox.StandardButton.Yes:
            return
        
        # Select new folder
        new_folder = QFileDialog.getExistingDirectory(
            self,
            "Select Folder Containing Original Source Files",
            os.path.dirname(self.current_document_path) if self.current_document_path else ""
        )
        
        if not new_folder:
            return
        
        # Scan new folder for matching files
        self.log(f"🔗 Relocating source folder to: {new_folder}")
        
        # Build a map of available files in the new folder (including subfolders)
        available_files = {}
        for root, dirs, filenames in os.walk(new_folder):
            for filename in filenames:
                full_path = os.path.join(root, filename)
                # Store by filename (case-insensitive matching)
                available_files[filename.lower()] = full_path
        
        self.log(f"   Found {len(available_files)} files in selected folder")
        
        # Try to match each project file
        updated_count = 0
        not_found = []
        
        for file_info in files:
            file_name = file_info.get('name', '')
            if not file_name:
                continue
            
            # Try to find matching file
            matched_path = available_files.get(file_name.lower())
            
            if matched_path:
                old_path = file_info.get('path', '')
                file_info['path'] = matched_path
                updated_count += 1
                self.log(f"   ✓ {file_name} → {matched_path}")
            else:
                not_found.append(file_name)
                self.log(f"   ✗ {file_name} - not found in selected folder")
        
        # Update project
        self.current_project.files = files
        
        # Show results
        if not_found:
            not_found_str = "\n".join([f"  • {f}" for f in not_found[:10]])
            if len(not_found) > 10:
                not_found_str += f"\n  ... and {len(not_found) - 10} more"
            
            QMessageBox.warning(
                self,
                "Partial Match",
                f"Updated {updated_count} file paths.\n\n"
                f"Files not found ({len(not_found)}):\n{not_found_str}\n\n"
                f"These files will use fallback export (no formatting preserved).\n\n"
                f"Save the project to keep these changes."
            )
        else:
            QMessageBox.information(
                self,
                "✓ Source Folder Relocated",
                f"Successfully updated all {updated_count} file paths.\n\n"
                f"DOCX exports will now preserve original formatting.\n\n"
                f"Save the project to keep these changes."
            )
        
        self.log(f"✓ Relocated {updated_count}/{len(files)} source file paths")

    def export_folder_multifile(self):
        """
        Export a multi-file project to a folder with separate files.
        
        Each file in the project is exported as a separate file in the target folder.
        Supports DOCX (target only) and TXT formats.
        """
        from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, 
                                      QGroupBox, QComboBox, QPushButton, QTableWidget,
                                      QTableWidgetItem, QHeaderView, QProgressDialog,
                                      QFileDialog, QButtonGroup)
        
        if not self.current_project:
            QMessageBox.warning(self, "No Project", "Please open a project first.")
            return
        
        # Check if this is a multi-file project
        is_multifile = getattr(self.current_project, 'is_multifile', False)
        files = getattr(self.current_project, 'files', [])
        
        if not is_multifile or not files:
            QMessageBox.information(
                self,
                "Single-File Project",
                "This is a single-file project.\n\n"
                "Use the regular export options (Target Only DOCX, Simple Text, etc.) instead.\n\n"
                "Folder export is for multi-file projects imported via:\n"
                "Project → Import → Folder (Multiple Files)..."
            )
            return
        
        # Show export dialog
        dialog = QDialog(self)
        dialog.setWindowTitle(self.tr("📁 Export Multi-File Project"))
        dialog.setMinimumWidth(650)
        dialog.setMinimumHeight(450)
        
        layout = QVBoxLayout(dialog)
        
        # Info
        segments = self.current_project.segments
        total_segs = len(segments)
        translated_segs = sum(1 for s in segments if s.target and s.target.strip())
        
        info_label = QLabel(
            f"<b>📁 Export Multi-File Project</b><br><br>"
            f"Project: {self.current_project.name}<br>"
            f"Files: {len(files)} | Segments: {translated_segs}/{total_segs} translated"
        )
        info_label.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(info_label)
        
        layout.addSpacing(10)
        
        # Export format selection
        format_group = QGroupBox(self.tr("Export Format"))
        format_layout = QVBoxLayout(format_group)

        format_btn_group = QButtonGroup(dialog)

        original_radio = CheckmarkRadioButton(self.tr("Original Format - Each file exports to its source format"))
        original_radio.setChecked(True)
        format_btn_group.addButton(original_radio, 5)
        format_layout.addWidget(original_radio)

        txt_radio = CheckmarkRadioButton(self.tr("Plain Text (.txt) - One line per segment"))
        format_btn_group.addButton(txt_radio, 1)
        format_layout.addWidget(txt_radio)

        md_radio = CheckmarkRadioButton(self.tr("Markdown (.md) - One line per segment (preserves syntax)"))
        format_btn_group.addButton(md_radio, 4)
        format_layout.addWidget(md_radio)

        docx_radio = CheckmarkRadioButton(self.tr("Word Document (.docx) - Target text only"))
        format_btn_group.addButton(docx_radio, 2)
        format_layout.addWidget(docx_radio)

        bilingual_radio = CheckmarkRadioButton(self.tr("Bilingual Table (.docx) - Source | Target columns"))
        format_btn_group.addButton(bilingual_radio, 3)
        format_layout.addWidget(bilingual_radio)
        
        layout.addWidget(format_group)
        
        layout.addSpacing(10)
        
        # File preview table
        preview_group = QGroupBox(f"Files to Export ({len(files)} files)")
        preview_layout = QVBoxLayout(preview_group)
        
        preview_table = QTableWidget()
        preview_table.setColumnCount(4)
        preview_table.setHorizontalHeaderLabels(["Original File", "Segments", "Translated", "Output Name"])
        preview_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        preview_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        preview_table.setRowCount(len(files))
        preview_table.setAlternatingRowColors(True)
        preview_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        
        for row, file_info in enumerate(files):
            file_id = file_info['id']
            file_name = file_info['name']
            
            # Count segments for this file
            file_segs = [s for s in segments if getattr(s, 'file_id', None) == file_id]
            total = len(file_segs)
            translated = sum(1 for s in file_segs if s.target and s.target.strip())
            
            # Generate output name
            base_name = os.path.splitext(file_name)[0]
            output_name = f"{base_name}_translated.txt"
            
            preview_table.setItem(row, 0, QTableWidgetItem(file_name))
            preview_table.setItem(row, 1, QTableWidgetItem(str(total)))
            preview_table.setItem(row, 2, QTableWidgetItem(f"{translated}/{total}"))
            preview_table.setItem(row, 3, QTableWidgetItem(output_name))
        
        preview_layout.addWidget(preview_table)
        layout.addWidget(preview_group)
        
        # Map file types to extensions for "Original Format" export
        _type_to_ext = {'txt': '.txt', 'md': '.md', 'docx': '.docx'}

        # Update output names when format changes
        def update_output_names():
            for row, file_info in enumerate(files):
                base_name = os.path.splitext(file_info['name'])[0]
                suffix = "_bilingual" if bilingual_radio.isChecked() else "_translated"
                if original_radio.isChecked():
                    ext = _type_to_ext.get(file_info.get('type', 'txt'), '.txt')
                elif txt_radio.isChecked():
                    ext = ".txt"
                elif md_radio.isChecked():
                    ext = ".md"
                else:
                    ext = ".docx"
                output_name = f"{base_name}{suffix}{ext}"
                preview_table.setItem(row, 3, QTableWidgetItem(output_name))

        original_radio.toggled.connect(update_output_names)
        txt_radio.toggled.connect(update_output_names)
        md_radio.toggled.connect(update_output_names)
        docx_radio.toggled.connect(update_output_names)
        bilingual_radio.toggled.connect(update_output_names)
        update_output_names()  # Set initial preview with original format selected
        
        layout.addSpacing(10)
        
        # Buttons
        button_layout = QHBoxLayout()
        ok_btn = QPushButton(self.tr("📤 Export to Folder..."))
        ok_btn.setStyleSheet("background-color: #4CAF50; color: white; font-weight: bold; padding: 8px 16px; border: none; outline: none;")
        ok_btn.clicked.connect(dialog.accept)
        cancel_btn = QPushButton(self.tr("Cancel"))
        cancel_btn.clicked.connect(dialog.reject)
        
        button_layout.addStretch()
        button_layout.addWidget(cancel_btn)
        button_layout.addWidget(ok_btn)
        layout.addLayout(button_layout)
        
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        
        # Get export format
        if original_radio.isChecked():
            export_format = "original"
        elif txt_radio.isChecked():
            export_format = "txt"
        elif md_radio.isChecked():
            export_format = "md"
        elif docx_radio.isChecked():
            export_format = "docx"
        else:
            export_format = "bilingual"
        
        # Select output folder
        output_folder = QFileDialog.getExistingDirectory(
            self,
            "Select Output Folder for Exported Files",
            "",
            QFileDialog.Option.ShowDirsOnly
        )
        
        if not output_folder:
            return
        
        # Export each file
        self._export_multifile_to_folder(output_folder, export_format)

    def _export_multifile_to_folder(self, output_folder: str, export_format: str):
        """
        Export each file in a multi-file project to the output folder.
        
        Args:
            output_folder: Path to output folder
            export_format: 'txt', 'docx', or 'bilingual'
        """
        files = getattr(self.current_project, 'files', [])
        segments = self.current_project.segments
        
        self.log(f"📤 Exporting multi-file project to: {output_folder}")
        self.log(f"   Format: {export_format.upper()}")
        
        # Progress dialog
        progress_dialog = QProgressDialog("Exporting files...", "Cancel", 0, len(files), self)
        progress_dialog.setWindowTitle(self.tr("Exporting Multi-File Project"))
        progress_dialog.setWindowModality(Qt.WindowModality.WindowModal)
        progress_dialog.setMinimumDuration(0)
        
        exported_count = 0
        total_segments_exported = 0
        files_missing_originals = []  # Track files where we couldn't preserve formatting
        word_count_results = []  # Post-export word-count safeguard, one per DOCX file

        for idx, file_info in enumerate(files):
            if progress_dialog.wasCanceled():
                self.log("❌ Export cancelled by user")
                break
            
            progress_dialog.setValue(idx)
            progress_dialog.setLabelText(f"Exporting: {file_info['name']} ({idx+1}/{len(files)})")
            QApplication.processEvents()
            
            file_id = file_info['id']
            file_name = file_info['name']
            base_name = os.path.splitext(file_name)[0]
            
            # Get original file path for format preservation
            # Normalize path separators (fix mixed forward/back slashes from Google Drive paths)
            original_path = file_info.get('path', None)
            if original_path:
                original_path = os.path.normpath(original_path)
            
            # Check if file exists (for format preservation during DOCX export)
            path_exists = False
            if original_path:
                path_exists = os.path.exists(original_path)
                # Fallback: try to actually open the file (more reliable for virtual filesystems like Google Drive)
                if not path_exists:
                    try:
                        with open(original_path, 'rb') as f:
                            f.read(1)
                        path_exists = True
                    except:
                        pass
            
            # Get segments for this file
            file_segments = [s for s in segments if getattr(s, 'file_id', None) == file_id]
            
            if not file_segments:
                self.log(f"   ⚠️ {file_name}: No segments found, skipping")
                continue
            
            try:
                # Resolve per-file format when "Original Format" is selected
                file_format = export_format
                if export_format == "original":
                    file_format = file_info.get('type', 'txt')

                format_note = ""
                if file_format in ("txt", "md"):
                    # Export as plain text / Markdown
                    ext = ".md" if file_format == "md" else ".txt"
                    output_path = os.path.join(output_folder, f"{base_name}_translated{ext}")
                    self._export_file_as_txt(file_segments, output_path)

                elif file_format == "docx":
                    # Export as DOCX (preserving original formatting if possible)
                    output_path = os.path.join(output_folder, f"{base_name}_translated.docx")
                    if not path_exists:
                        format_note = " (no original - plain text)"
                        files_missing_originals.append(file_name)
                    self._export_file_as_docx(file_segments, output_path, original_path if path_exists else None)
                    word_count_results.append(
                        self._verify_export_word_count(file_segments, output_path, context_label=file_name)
                    )

                elif file_format == "bilingual":
                    # Export as bilingual table
                    output_path = os.path.join(output_folder, f"{base_name}_bilingual.docx")
                    self._export_file_as_bilingual(file_segments, output_path)
                
                exported_count += 1
                total_segments_exported += len(file_segments)
                self.log(f"   ✓ {file_name} → {os.path.basename(output_path)} ({len(file_segments)} segments){format_note}")
                
            except Exception as e:
                self.log(f"   ❌ {file_name}: Export failed - {str(e)}")
        
        progress_dialog.setValue(len(files))
        progress_dialog.close()
        
        # Show summary
        self.log(f"✓ Export complete: {exported_count}/{len(files)} files, {total_segments_exported} segments")
        
        # Build message with warning if files were missing originals
        message = (
            f"Successfully exported {exported_count} files to:\n{output_folder}\n\n"
            f"Total segments: {total_segments_exported}\n"
            f"Format: {export_format.upper()}"
        )
        
        if files_missing_originals and export_format == "docx":
            message += (
                f"\n\n⚠️ {len(files_missing_originals)} file(s) exported without original formatting "
                f"(source files not found).\n\n"
                f"To preserve formatting, use:\n"
                f"Project → Export → Relocate Source Folder..."
            )
            self.log(f"   ⚠️ {len(files_missing_originals)} files without original formatting")
        
        QMessageBox.information(
            self,
            "Export Complete",
            message
        )

        # Post-export safeguard: one combined warning if any DOCX looks short.
        self._show_export_word_count_warning(word_count_results)

    def _export_file_as_txt(self, segments: list, output_path: str):
        """Export segments to a plain text file, grouping by paragraph_id."""
        import re
        from itertools import groupby

        def strip_tags(text: str) -> str:
            """Remove formatting tags from text."""
            if not text:
                return ""
            text = re.sub(r'</?b>', '', text)
            text = re.sub(r'</?i>', '', text)
            text = re.sub(r'</?u>', '', text)
            text = re.sub(r'</?li-[ob]>', '', text)
            return text

        from modules.simple_segmenter import join_segments

        with open(output_path, 'w', encoding='utf-8') as f:
            for _para_id, group in groupby(segments, key=lambda s: s.paragraph_id):
                parts = []
                for seg in group:
                    text = seg.target if seg.target and seg.target.strip() else seg.source
                    parts.append((strip_tags(text), getattr(seg, 'join_before', None)))
                # Rejoin sentence segments with the spacing they had in the
                # source line (a space if unknown); empty segments produce empty lines
                f.write(join_segments(parts) + '\n')

    def _export_file_as_docx(self, segments: list, output_path: str, original_path: str = None):
        """
        Export segments to a DOCX file, preserving original formatting if original file exists.

        For Okapi-imported projects (the standard path for new projects)
        this calls the sidecar's /merge endpoint, which gives a faithful
        round-trip via the Okapi Framework filter writers. For legacy
        standard-imported projects we fall back to the python-docx
        DOCXHandler, which preserves formatting by paragraph-text match.

        Args:
            segments: List of Segment objects for this file
            output_path: Path to save the exported file
            original_path: Path to the original DOCX file (for format preservation)
        """
        from docx import Document
        from docx.shared import Pt
        import re

        # ── Okapi merge path ──────────────────────────────────────────
        # If this project was imported via Okapi AND we have the original
        # file, route through the sidecar's /merge endpoint. The segments
        # carry okapi_tu_id / okapi_segment_index that the merge needs.
        is_okapi_project = (
            self.current_project is not None
            and getattr(self.current_project, 'import_engine', '') == 'okapi'
        )
        if (is_okapi_project and original_path and os.path.exists(original_path)
                and self.okapi_sidecar and self.okapi_sidecar.is_running()
                and any(getattr(s, 'okapi_tu_id', '') for s in segments)):
            try:
                # Build translations list, ONE entry per Okapi text-unit ID.
                #
                # Why one-per-TU and not one-per-segment: the sidecar applies
                # SRX sentence segmentation only inside its extract helper –
                # it doesn't re-segment the underlying ITextUnit. So when
                # /merge re-opens the original file and reads the same TU,
                # srcSegments.count() == 1 and the merge loop drops any
                # MergeSegment with segmentIndex >= 1 (FilterService.java),
                # silently losing the 2nd, 3rd, … sentences of every
                # paragraph. This is the SAME workaround as the single-file
                # Okapi export path; the multi-file per-file path was
                # missing it, which dropped every okapi_segment_index >= 1
                # segment from multi-file exports (reported on a real job).
                #
                # Group all per-sentence segments back by okapi_tu_id, sort
                # by okapi_segment_index, concatenate with a single space,
                # and send one merge entry per TU at segmentIndex=0.
                from collections import defaultdict as _defaultdict
                tu_groups = _defaultdict(list)  # tu_id → [(seg_idx, translation)]
                skipped = 0
                for seg in segments:
                    tu_id = getattr(seg, 'okapi_tu_id', '') or ''
                    seg_idx = getattr(seg, 'okapi_segment_index', -1)
                    if not tu_id or seg_idx is None or seg_idx < 0:
                        skipped += 1
                        continue
                    translation = (seg.target.strip()
                                   if seg.target and seg.target.strip()
                                   else seg.source)
                    # Strip Supervertaler-only tags Okapi wouldn't know.
                    translation = re.sub(r'</?(?:bi|li-[bo]|li)>', '',
                                         translation)
                    tu_groups[tu_id].append((seg_idx, translation))

                translations = []
                for tu_id, sub_segs in tu_groups.items():
                    sub_segs.sort(key=lambda pair: pair[0])
                    combined = ' '.join(t for _, t in sub_segs)
                    # Strip Okapi-internal run tags (<run1>, …) that can leak
                    # through when SRX splits inside an inline run; they
                    # unbalance Okapi's tag stack and crash the OOXML writer.
                    combined = re.sub(r'</?run\d+>', '', combined)
                    translations.append({
                        'id': tu_id,
                        'segmentIndex': 0,
                        'translation': combined,
                    })

                if translations:
                    multi_tu_count = sum(1 for sub in tu_groups.values() if len(sub) > 1)
                    if multi_tu_count:
                        self.log(f"      🔧 Combining sub-segments for merge: "
                                 f"{multi_tu_count} text-unit(s) had multiple "
                                 f"sentences (concatenated for /merge)")
                    src_lang = self.current_project.source_lang
                    trg_lang = self.current_project.target_lang
                    self.log(f"      🔧 Okapi merge ({len(translations)} segments)…")
                    self.okapi_sidecar.merge(
                        original_path=original_path,
                        translations=translations,
                        source_lang=src_lang,
                        target_lang=trg_lang,
                        output_path=output_path,
                        options=okapi_sidecar_options(getattr(self.current_project, 'import_options', None)),
                    )
                    if skipped:
                        self.log(f"      ⚠️ {skipped} segments without Okapi metadata "
                                 f"used source text in merge")
                    return
                # else: no segments had Okapi metadata – fall through.
            except Exception as e:
                self.log(f"      ⚠️ Okapi merge failed, falling back to standard: {e}")
                # Fall through to the python-docx path below.
        
        def strip_tags(text: str) -> str:
            """Remove formatting tags from text."""
            if not text:
                return ""
            text = re.sub(r'</?b>', '', text)
            text = re.sub(r'</?i>', '', text)
            text = re.sub(r'</?u>', '', text)
            text = re.sub(r'</?li-[ob]>', '', text)
            return text
        
        def file_accessible(path: str) -> bool:
            """Check if file exists and is accessible (works better with Google Drive)."""
            if not path:
                return False
            # Try os.path.exists first
            if os.path.exists(path):
                return True
            # Fallback: try to actually open the file (more reliable for virtual filesystems)
            try:
                with open(path, 'rb') as f:
                    f.read(1)
                return True
            except:
                return False
        
        # If we have the original file, use DOCXHandler for proper format preservation
        if original_path and file_accessible(original_path):
            self.log(f"      📄 Using original file as template: {os.path.basename(original_path)}")
            
            try:
                # Create a fresh DOCXHandler for this file
                from modules.docx_handler import DOCXHandler
                handler = DOCXHandler()
                
                # Import the original to set up paragraph mapping
                handler.import_docx(original_path)
                
                # Convert segments to the format expected by export_docx
                export_segments = []
                for seg in segments:
                    text = seg.target if seg.target and seg.target.strip() else seg.source
                    export_segments.append({
                        'paragraph_id': getattr(seg, 'paragraph_id', 0),
                        'source': seg.source,
                        'target': text
                    })
                
                # Use the handler's export which preserves all formatting
                target_lang = self.current_project.target_lang if hasattr(self.current_project, 'target_lang') else None
                handler.export_docx(export_segments, output_path, preserve_formatting=True, target_lang=target_lang)
                return
                
            except Exception as e:
                self.log(f"      ⚠️ DOCXHandler export failed ({str(e)}), falling back to simple export")
        
        # Fallback: create new document (loses formatting)
        self.log(f"      ⚠️ Original file not found, creating new document (formatting not preserved)")
        
        doc = Document()
        
        for segment in segments:
            text = segment.target if segment.target and segment.target.strip() else segment.source
            text = strip_tags(text)
            para = doc.add_paragraph(text)
            for run in para.runs:
                run.font.size = Pt(11)

        # Set document language to target language
        if hasattr(self.current_project, 'target_lang') and self.current_project.target_lang:
            target_lang_code = get_docx_language_code(self.current_project.target_lang)
            set_docx_language(doc, target_lang_code)
            self.log(f"✓ Set document language to: {target_lang_code}")

        doc.save(output_path)

    def _export_file_as_bilingual(self, segments: list, output_path: str):
        """Export segments to a bilingual DOCX table (Source | Target columns)."""
        from docx import Document
        from docx.shared import Inches, Pt
        import re
        
        def strip_tags(text: str) -> str:
            """Remove formatting tags from text for clean display."""
            if not text:
                return ""
            # Remove inline formatting tags
            text = re.sub(r'</?b>', '', text)
            text = re.sub(r'</?i>', '', text)
            text = re.sub(r'</?u>', '', text)
            text = re.sub(r'</?li-[ob]>', '', text)
            return text
        
        doc = Document()
        
        # Add header
        header = doc.add_paragraph()
        header.add_run(f"Bilingual Export - {len(segments)} segments").bold = True
        
        # Create table
        table = doc.add_table(rows=1, cols=3)
        table.style = 'Table Grid'
        
        # Header row
        hdr_cells = table.rows[0].cells
        hdr_cells[0].text = "#"
        hdr_cells[1].text = "Source"
        hdr_cells[2].text = "Target"
        
        # Make header bold
        for cell in hdr_cells:
            for para in cell.paragraphs:
                for run in para.runs:
                    run.bold = True
        
        # Add segments
        for segment in segments:
            row_cells = table.add_row().cells
            row_cells[0].text = str(segment.id)
            row_cells[1].text = strip_tags(segment.source)
            row_cells[2].text = strip_tags(segment.target) if segment.target else ""
        
        # Set column widths using fixed layout and explicit grid
        from docx.oxml.ns import qn
        from docx.oxml import OxmlElement
        tbl = table._tbl
        tbl_pr = tbl.tblPr if tbl.tblPr is not None else OxmlElement('w:tblPr')

        tbl_layout = OxmlElement('w:tblLayout')
        tbl_layout.set(qn('w:type'), 'fixed')
        tbl_pr.append(tbl_layout)

        # 3-column layout: narrow # (5%), equal source/target (47.5% each)
        section = doc.sections[0]
        usable_twips = int((section.page_width - section.left_margin - section.right_margin) / 914400 * 1440)
        col_twips = [int(0.05 * usable_twips), int(0.475 * usable_twips), 0]
        col_twips[2] = usable_twips - col_twips[0] - col_twips[1]

        tbl_w = OxmlElement('w:tblW')
        tbl_w.set(qn('w:w'), str(usable_twips))
        tbl_w.set(qn('w:type'), 'dxa')
        tbl_pr.append(tbl_w)

        old_grid = tbl.find(qn('w:tblGrid'))
        if old_grid is not None:
            tbl.remove(old_grid)
        new_grid = OxmlElement('w:tblGrid')
        for tw in col_twips:
            gc = OxmlElement('w:gridCol')
            gc.set(qn('w:w'), str(tw))
            new_grid.append(gc)
        tbl.insert(tbl.index(tbl_pr) + 1, new_grid)

        for row in table.rows:
            for i, tw in enumerate(col_twips):
                tc = row.cells[i]._tc
                tcPr = tc.tcPr
                if tcPr is None:
                    tcPr = OxmlElement('w:tcPr')
                    tc.insert(0, tcPr)
                tcW = tcPr.find(qn('w:tcW'))
                if tcW is None:
                    tcW = OxmlElement('w:tcW')
                    tcPr.insert(0, tcW)
                tcW.set(qn('w:w'), str(tw))
                tcW.set(qn('w:type'), 'dxa')

        doc.save(output_path)
