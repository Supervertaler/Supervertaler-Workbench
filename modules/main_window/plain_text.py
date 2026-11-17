"""Plain-text import and export, and the export for use with an AI.

Part of the main window (SupervertalerQt), moved here unchanged. The
methods run as the main window's own: `self` is the SupervertalerQt.
"""

import os
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
)
from modules.grid_editors import TagHighlighter
from modules.language_combos import _import_language_pairs, _select_lang_in_combo
from modules.main_dialogs import _ImportProgressDialog
from modules.segment_model import Project, Segment
from modules.styled_widgets import CheckmarkCheckBox, CheckmarkRadioButton


class PlainTextMixin:
    def import_simple_txt(self):
        """
        Import a simple text file or Markdown file where each line is a source segment.
        
        This is the simplest possible import format:
        - Each line becomes one source segment
        - Empty lines are preserved as empty segments (for structure)
        - After translation, export produces a matching file with translations
        - Markdown files are imported as-is (syntax preserved for round-trip)
        """
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Text or Markdown File",
            "",
            "Text Files (*.txt *.md);;Markdown (*.md);;Plain Text (*.txt);;All Files (*.*)"
        )
        
        if not file_path:
            return
        
        # Show import options dialog
        dialog = QDialog(self)
        dialog.setWindowTitle(self.tr("Import Text / Markdown File"))
        dialog.setMinimumWidth(500)
        
        layout = QVBoxLayout(dialog)
        
        # Detect if it's a Markdown file
        is_markdown = file_path.lower().endswith('.md')
        
        # Info message
        if is_markdown:
            info_label = QLabel(
                "📝 <b>Markdown File Import</b><br><br>"
                "Each line in the file will become one source segment.<br>"
                "Markdown syntax (<b>**bold**</b>, <i>*italic*</i>, # headings, etc.) is preserved.<br>"
                "After translation, export will produce a matching Markdown file.<br><br>"
                "<b>Tip:</b> Keep Markdown syntax intact when translating!"
            )
        else:
            info_label = QLabel(
                "📄 <b>Simple Text File Import</b><br><br>"
                "Each line in the file will become one source segment.<br>"
                "After translation, you can export a matching file with translations.<br><br>"
                "Empty lines are preserved to maintain document structure."
            )
        info_label.setWordWrap(True)
        info_label.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(info_label)
        
        layout.addSpacing(15)
        
        # Language pair selection
        lang_group = QGroupBox(self.tr("Language Pair"))
        lang_layout = QHBoxLayout(lang_group)
        
        # Common languages for translation
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

        layout.addSpacing(10)

        # Sentence segmentation option
        last_segment_setting = general_settings.get('last_import_sentence_segment', False)
        segment_checkbox = CheckmarkCheckBox(self.tr("Split lines into sentences"))
        segment_checkbox.setChecked(last_segment_setting)
        segment_checkbox.setToolTip(
            "When checked, long lines will be split into individual sentences\n"
            "for easier translation. Sentences are automatically rejoined on export.\n"
            "Where lines are split is set in Settings → Segmentation Rules."
        )
        layout.addWidget(segment_checkbox)

        # Markdown caveat (shown/hidden based on checkbox state)
        if is_markdown:
            md_note = QLabel(
                "<i>Markdown syntax (links, code spans, URLs) is protected during splitting.\n"
                "Complex formatting may still need review after export.</i>"
            )
            md_note.setStyleSheet("color: #FF9800; font-size: 11px; margin-left: 22px;")
            md_note.setWordWrap(True)
            md_note.setVisible(segment_checkbox.isChecked())
            segment_checkbox.toggled.connect(md_note.setVisible)
            layout.addWidget(md_note)

        layout.addSpacing(20)

        # Buttons
        button_layout = QHBoxLayout()
        ok_btn = QPushButton(self.tr("Import"))
        ok_btn.setStyleSheet("background-color: #4CAF50; color: white; font-weight: bold; border: none; outline: none;")
        ok_btn.clicked.connect(dialog.accept)
        cancel_btn = QPushButton(self.tr("Cancel"))
        cancel_btn.clicked.connect(dialog.reject)
        
        button_layout.addStretch()
        button_layout.addWidget(cancel_btn)
        button_layout.addWidget(ok_btn)
        layout.addLayout(button_layout)
        
        # Show dialog
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        # Check if this is a re-import of the same file
        reimport_mode, preserved_id, preserved_tm, preserved_tb = self._check_reimport_same_file(
            file_path, ['current_project.original_txt_path', 'current_document_path']
        )
        if reimport_mode is None:  # User cancelled
            return

        # Store selected options
        source_lang = source_combo.currentData()
        target_lang = target_combo.currentData()
        use_sentence_segmentation = segment_checkbox.isChecked()

        # Save selected settings for next time
        general_settings = self.load_general_settings()
        general_settings['last_import_source_lang'] = source_lang
        general_settings['last_import_target_lang'] = target_lang
        general_settings['last_import_sentence_segment'] = use_sentence_segmentation
        self.save_general_settings(general_settings)

        try:
            self.log(f"📄 Importing simple text file: {os.path.basename(file_path)}")
            
            # Read the file
            with open(file_path, 'r', encoding='utf-8') as f:
                lines = f.readlines()
            
            # Create segments from lines
            segments = []
            if use_sentence_segmentation:
                segmenter = self._make_sentence_segmenter(markdown=is_markdown)
                for line_num, line in enumerate(lines, 1):
                    text = line.rstrip('\r\n')
                    if not text.strip():
                        # Empty line → empty segment (hidden in grid, preserved for export)
                        segments.append(Segment(
                            id=len(segments) + 1, source=text, target="",
                            status="untranslated", notes="", type="para",
                            paragraph_id=line_num, document_position=line_num
                        ))
                    else:
                        for sentence, separator in segmenter.segment_with_separators(text):
                            segments.append(Segment(
                                id=len(segments) + 1, source=sentence, target="",
                                status="untranslated", notes="", type="para",
                                paragraph_id=line_num, document_position=line_num,
                                join_before=separator
                            ))
            else:
                for line_num, line in enumerate(lines, 1):
                    text = line.rstrip('\r\n')
                    segments.append(Segment(
                        id=len(segments) + 1, source=text, target="",
                        status="untranslated", notes="", type="para",
                        paragraph_id=line_num, document_position=line_num
                    ))
            
            if not segments:
                QMessageBox.warning(
                    self, "Empty File",
                    "The file contains no text to import."
                )
                return
            
            # Create new project
            project = Project(
                name=f"{'MD' if is_markdown else 'TXT'} Import - {os.path.basename(file_path)}",
                source_lang=source_lang,
                target_lang=target_lang,
                segments=segments
            )

            # Apply preserved settings if re-importing
            self._apply_reimport_settings(project, reimport_mode, preserved_id, preserved_tm, preserved_tb)

            # Store original file path for export
            project.original_txt_path = file_path

            # Reset project-type specific highlighting flags
            TagHighlighter._is_cafetran_project = False
            TagHighlighter._is_markdown_project = is_markdown

            # Set as current project and load into grid
            self.current_project = project
            self.current_document_path = file_path

            # CRITICAL: Update _original_segment_order for new import
            self._original_segment_order = self.current_project.segments.copy()

            # v1.10.150: wrap the slow grid-load in the shared progress dialog so
            # the user sees activity instead of "Not Responding" on large files.
            # (Minimal wrap: subsequent fast finalise steps stay outside the dialog.)
            _n_segs = len(self.current_project.segments) if self.current_project else 0
            with _ImportProgressDialog(
                self, title="Importing plain text",
                initial_label=f"Loading {_n_segs:,} segments into grid…",
                initial_total=max(_n_segs, 1),
            ) as _prog:
                self.load_segments_to_grid(progress_callback=_prog.grid_callback)

            # Initialize TM for this project
            self.initialize_tm_database()

            # Clear caches to ensure fresh TM lookups
            self._clear_caches_after_import()

            # Only deactivate resources for truly NEW projects (not re-imports)
            if not reimport_mode:
                self._deactivate_all_resources_for_new_project()

            # Initialize spellcheck for target language
            self._initialize_spellcheck_for_target_language(target_lang)

            # Update status
            empty_count = sum(1 for seg in segments if not seg.source.strip())
            file_type = "Markdown file" if is_markdown else "text file"
            line_count = len(lines)
            self.log(f"✓ Loaded {len(segments)} segments from {line_count} lines ({file_type})")
            if use_sentence_segmentation:
                self.log(f"  ✂️ Sentence segmentation applied")
            if is_markdown:
                self.log(f"  📝 Markdown syntax highlighting enabled")
            if empty_count > 0:
                self.log(f"  ({empty_count} empty lines preserved for structure)")
            self.log(f"📍 Project language pair: {source_lang.upper()} → {target_lang.upper()}")
            self.update_window_title()
            
            # Refresh AI Assistant context
            if hasattr(self, 'prompt_manager_qt'):
                self.prompt_manager_qt.refresh_context()
            
            # v1.10.153: build the termbase index and prefetch
            # TM/MT/LLM matches for the first 50 segments — without
            # this, cell-clicks fall through to per-word SQL queries
            # (2-34 s per click on a 40+ termbase database).
            self._finalise_import_with_indexes()
            QMessageBox.information(
                self,
                "Import Complete",
                f"Successfully imported {len(segments)} lines from:\n{os.path.basename(file_path)}\n\n"
                f"📍 Language pair: {source_lang.upper()} → {target_lang.upper()}"
            )
            
        except UnicodeDecodeError:
            # Try with different encodings
            self.log("⚠️ UTF-8 failed, trying other encodings...")
            for encoding in ['latin-1', 'cp1252', 'iso-8859-1']:
                try:
                    with open(file_path, 'r', encoding=encoding) as f:
                        lines = f.readlines()
                    self.log(f"✓ Successfully read file with {encoding} encoding")
                    # Re-run import with this encoding
                    segments = []
                    if use_sentence_segmentation:
                        segmenter = self._make_sentence_segmenter(markdown=is_markdown)
                        for line_num, line in enumerate(lines, 1):
                            text = line.rstrip('\r\n')
                            if not text.strip():
                                segments.append(Segment(
                                    id=len(segments) + 1, source=text, target="",
                                    status="untranslated", notes="", type="para",
                                    paragraph_id=line_num, document_position=line_num
                                ))
                            else:
                                for sentence, separator in segmenter.segment_with_separators(text):
                                    segments.append(Segment(
                                        id=len(segments) + 1, source=sentence, target="",
                                        status="untranslated", notes="", type="para",
                                        paragraph_id=line_num, document_position=line_num,
                                        join_before=separator
                                    ))
                    else:
                        for line_num, line in enumerate(lines, 1):
                            text = line.rstrip('\r\n')
                            segments.append(Segment(
                                id=len(segments) + 1, source=text, target="",
                                status="untranslated", notes="", type="para",
                                paragraph_id=line_num, document_position=line_num
                            ))
                    
                    if segments:
                        project = Project(
                            name=f"TXT Import - {os.path.basename(file_path)}",
                            source_lang=source_lang,
                            target_lang=target_lang,
                            segments=segments
                        )
                        project.original_txt_path = file_path
                        self.current_project = project
                        self.current_document_path = file_path
                        # CRITICAL: Update _original_segment_order for new import
                        self._original_segment_order = self.current_project.segments.copy()
                        self.load_segments_to_grid()
                        self.initialize_tm_database()
                        self.log(f"✓ Loaded {len(segments)} lines from text file")
                        self.update_window_title()
                        if hasattr(self, 'prompt_manager_qt'):
                            self.prompt_manager_qt.refresh_context()
                        QMessageBox.information(
                            self,
                            "Import Complete",
                            f"Successfully imported {len(segments)} lines from:\n{os.path.basename(file_path)}\n\n"
                            f"📍 Language pair: {source_lang.upper()} → {target_lang.upper()}\n"
                            f"📝 Encoding: {encoding}"
                        )
                        return
                except:
                    continue
            
            QMessageBox.critical(
                self,
                "Encoding Error",
                "Could not read the text file.\n\n"
                "The file may use an unsupported character encoding.\n"
                "Try saving the file as UTF-8 and importing again."
            )
            
        except Exception as e:
            self.log(f"✗ Import failed: {str(e)}")
            QMessageBox.critical(
                self,
                "Import Error",
                f"Failed to import text file:\n\n{str(e)}"
            )

    def export_simple_txt(self):
        """
        Export translations as a simple text file, one line per segment.
        
        The output file will have the exact same structure as the input:
        - Each segment becomes one line
        - Empty segments become empty lines
        - Target text is used (or source if no translation)
        """
        try:
            if not self.current_project or not self.current_project.segments:
                QMessageBox.warning(self, "No Project", "Please open a project with segments first")
                return
            
            segments = self._segments_in_document_order()
            
            # Check translation status
            translated_count = sum(1 for seg in segments if seg.target and seg.target.strip())
            total_count = len(segments)
            
            if translated_count == 0:
                QMessageBox.warning(
                    self, "No Translations",
                    "No translated segments found.\n\n"
                    "Please translate some segments before exporting."
                )
                return
            
            # Show export options dialog
            dialog = QDialog(self)
            dialog.setWindowTitle(self.tr("Export Simple Text File"))
            dialog.setMinimumWidth(450)
            
            layout = QVBoxLayout(dialog)
            
            # Status info
            if translated_count < total_count:
                status_label = QLabel(
                    f"⚠️ <b>{translated_count} of {total_count}</b> segments are translated."
                )
                status_label.setTextFormat(Qt.TextFormat.RichText)
                status_label.setStyleSheet("color: #FF9800;")
            else:
                status_label = QLabel(
                    f"✅ All <b>{total_count}</b> segments are translated."
                )
                status_label.setTextFormat(Qt.TextFormat.RichText)
                status_label.setStyleSheet("color: #4CAF50;")
            layout.addWidget(status_label)
            
            layout.addSpacing(10)
            
            # Options group
            options_group = QGroupBox(self.tr("Untranslated Segments"))
            options_layout = QVBoxLayout(options_group)
            
            # Radio buttons for handling untranslated
            use_source_radio = CheckmarkRadioButton(self.tr("Use source text for untranslated segments"))
            use_source_radio.setChecked(True)
            use_empty_radio = CheckmarkRadioButton(self.tr("Leave untranslated segments empty"))
            
            options_layout.addWidget(use_source_radio)
            options_layout.addWidget(use_empty_radio)
            layout.addWidget(options_group)
            
            layout.addSpacing(10)
            
            # Encoding option
            encoding_group = QGroupBox(self.tr("File Encoding"))
            encoding_layout = QHBoxLayout(encoding_group)
            
            encoding_combo = QComboBox()
            encoding_combo.addItem("UTF-8 (recommended)", "utf-8")
            encoding_combo.addItem("UTF-8 with BOM", "utf-8-sig")
            encoding_combo.addItem("Latin-1 (ISO-8859-1)", "latin-1")
            encoding_combo.addItem("Windows-1252", "cp1252")
            encoding_layout.addWidget(encoding_combo)
            encoding_layout.addStretch()
            layout.addWidget(encoding_group)
            
            layout.addSpacing(20)
            
            # Buttons
            button_layout = QHBoxLayout()
            ok_btn = QPushButton(self.tr("Export"))
            ok_btn.setStyleSheet("background-color: #4CAF50; color: white; font-weight: bold; border: none; outline: none;")
            ok_btn.clicked.connect(dialog.accept)
            cancel_btn = QPushButton(self.tr("Cancel"))
            cancel_btn.clicked.connect(dialog.reject)
            
            button_layout.addStretch()
            button_layout.addWidget(cancel_btn)
            button_layout.addWidget(ok_btn)
            layout.addLayout(button_layout)
            
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            
            # Get options
            use_source_fallback = use_source_radio.isChecked()
            encoding = encoding_combo.currentData()
            
            # Get save path - detect if source was a markdown file
            is_markdown = (hasattr(self.current_project, 'original_txt_path')
                          and self.current_project.original_txt_path
                          and self.current_project.original_txt_path.lower().endswith('.md'))

            default_name = ""
            default_ext = ".md" if is_markdown else ".txt"
            if hasattr(self.current_project, 'original_txt_path') and self.current_project.original_txt_path:
                base = os.path.splitext(os.path.basename(self.current_project.original_txt_path))[0]
                default_name = f"{base}_translated{default_ext}"
            elif self.current_project.name:
                default_name = self.current_project.name.replace(" ", "_") + f"_translated{default_ext}"
            if default_name:
                default_name = self._project_export_path(default_name)  # into target/ (#228)

            if is_markdown:
                file_filter = "Markdown Files (*.md);;Text Files (*.txt);;All Files (*.*)"
            else:
                file_filter = "Text Files (*.txt);;Markdown Files (*.md);;All Files (*.*)"

            file_path, _ = QFileDialog.getSaveFileName(
                self,
                "Export Text / Markdown File",
                default_name,
                file_filter
            )

            if not file_path:
                return

            # Ensure valid extension
            if not file_path.lower().endswith(('.txt', '.md')):
                file_path += default_ext
            
            # Build output lines, grouping segments by paragraph_id
            # (when sentence segmentation was used, multiple segments share a paragraph_id)
            from itertools import groupby
            from modules.simple_segmenter import join_segments
            output_lines = []
            for _para_id, group in groupby(segments, key=lambda s: s.paragraph_id):
                parts = []
                for seg in group:
                    if seg.target and seg.target.strip():
                        text = seg.target
                    elif use_source_fallback:
                        text = seg.source
                    else:
                        text = ""
                    parts.append((text, getattr(seg, 'join_before', None)))
                # Rejoin sentence segments with the spacing they had in the
                # source line (a space if unknown); empty segments produce empty lines
                output_lines.append(join_segments(parts))

            # Write file
            with open(file_path, 'w', encoding=encoding, newline='\n') as f:
                for line in output_lines:
                    f.write(line + '\n')
            
            self.log(f"✓ Exported {len(output_lines)} lines ({len(segments)} segments) to: {os.path.basename(file_path)}")
            self.log(f"  Encoding: {encoding}")

            QMessageBox.information(
                self,
                "Export Complete",
                f"Successfully exported {len(output_lines)} lines to:\n{os.path.basename(file_path)}\n\n"
                f"Translated: {translated_count} / {total_count} segments"
            )
            
        except Exception as e:
            self.log(f"✗ Export failed: {str(e)}")
            QMessageBox.critical(
                self,
                "Export Error",
                f"Failed to export text file:\n\n{str(e)}"
            )

    def export_for_ai(self):
        """
        Export segments in AI-readable [SEGMENT] format.
        
        Output format:
            [SEGMENT 0001]
            NL: Source text here
            EN: Target text here (or empty if not translated)
            
            [SEGMENT 0002]
            ...
        
        This format is designed for:
        - AI translation services (ChatGPT, Claude, etc.)
        - AI-assisted review and editing
        - Easy parsing and re-import
        """
        try:
            if not self.current_project or not self.current_project.segments:
                QMessageBox.warning(self, "No Project", "Please open a project with segments first")
                return
            
            segments = self._segments_in_document_order()
            
            # Get language codes
            source_lang = self.current_project.source_lang or "Source"
            target_lang = self.current_project.target_lang or "Target"
            
            # Convert to short codes (uppercase) for the format
            source_code = self._convert_language_to_code(source_lang).upper() if source_lang else "SRC"
            target_code = self._convert_language_to_code(target_lang).upper() if target_lang else "TGT"
            
            # Check translation status
            translated_count = sum(1 for seg in segments if seg.target and seg.target.strip())
            total_count = len(segments)
            
            # Show export options dialog
            dialog = QDialog(self)
            dialog.setWindowTitle(self.tr("Export for AI"))
            dialog.setMinimumWidth(500)
            
            layout = QVBoxLayout(dialog)
            
            # Info label
            info_label = QLabel(
                "Export segments in a format optimized for AI systems.\n"
                "Each segment is numbered and labeled with language codes."
            )
            info_label.setWordWrap(True)
            layout.addWidget(info_label)
            
            layout.addSpacing(10)
            
            # Status info
            if translated_count > 0:
                status_label = QLabel(
                    f"📊 <b>{translated_count} of {total_count}</b> segments have translations."
                )
            else:
                status_label = QLabel(
                    f"📊 <b>{total_count}</b> segments (none translated yet)."
                )
            status_label.setTextFormat(Qt.TextFormat.RichText)
            layout.addWidget(status_label)
            
            layout.addSpacing(10)
            
            # Language codes group
            lang_group = QGroupBox(self.tr("Language Codes"))
            lang_layout = QGridLayout(lang_group)
            
            lang_layout.addWidget(QLabel(self.tr("Source:")), 0, 0)
            source_code_edit = QLineEdit(source_code)
            source_code_edit.setMaximumWidth(80)
            source_code_edit.setToolTip(self.tr("Language code for source text (e.g., NL, EN, DE)"))
            lang_layout.addWidget(source_code_edit, 0, 1)
            
            lang_layout.addWidget(QLabel(self.tr("Target:")), 0, 2)
            target_code_edit = QLineEdit(target_code)
            target_code_edit.setMaximumWidth(80)
            target_code_edit.setToolTip(self.tr("Language code for target text (e.g., EN, NL, FR)"))
            lang_layout.addWidget(target_code_edit, 0, 3)
            
            lang_layout.setColumnStretch(4, 1)  # Push controls left
            layout.addWidget(lang_group)
            
            # Numbering options
            numbering_group = QGroupBox(self.tr("Segment Numbering"))
            numbering_layout = QGridLayout(numbering_group)
            
            numbering_layout.addWidget(QLabel(self.tr("Start at:")), 0, 0)
            start_spin = QSpinBox()
            start_spin.setRange(1, 99999)
            start_spin.setValue(1)
            start_spin.setMaximumWidth(80)
            numbering_layout.addWidget(start_spin, 0, 1)
            
            numbering_layout.addWidget(QLabel(self.tr("Zero padding:")), 0, 2)
            padding_spin = QSpinBox()
            padding_spin.setRange(1, 8)
            padding_spin.setValue(4)
            padding_spin.setMaximumWidth(60)
            padding_spin.setToolTip(self.tr("Number of digits (4 = 0001, 0002...)"))
            numbering_layout.addWidget(padding_spin, 0, 3)
            
            numbering_layout.setColumnStretch(4, 1)
            layout.addWidget(numbering_group)
            
            # Content options
            content_group = QGroupBox(self.tr("Content Options"))
            content_layout = QVBoxLayout(content_group)
            
            include_both_radio = CheckmarkRadioButton(self.tr("Include both source and target (bilingual)"))
            include_both_radio.setChecked(True)
            source_only_radio = CheckmarkRadioButton(self.tr("Source only (for AI translation)"))
            target_only_radio = CheckmarkRadioButton(self.tr("Target only (translated segments)"))
            
            content_layout.addWidget(include_both_radio)
            content_layout.addWidget(source_only_radio)
            content_layout.addWidget(target_only_radio)
            layout.addWidget(content_group)
            
            # Filter options
            filter_group = QGroupBox(self.tr("Segment Filter"))
            filter_layout = QVBoxLayout(filter_group)
            
            all_segments_radio = CheckmarkRadioButton(self.tr("All segments"))
            all_segments_radio.setChecked(True)
            untranslated_radio = CheckmarkRadioButton(self.tr("Untranslated segments only (empty target)"))
            translated_radio = CheckmarkRadioButton(self.tr("Translated segments only (has target)"))
            
            filter_layout.addWidget(all_segments_radio)
            filter_layout.addWidget(untranslated_radio)
            filter_layout.addWidget(translated_radio)
            layout.addWidget(filter_group)
            
            layout.addSpacing(10)
            
            # Preview
            preview_group = QGroupBox(self.tr("Preview"))
            preview_layout = QVBoxLayout(preview_group)
            preview_text = QTextEdit()
            preview_text.setReadOnly(True)
            preview_text.setMaximumHeight(120)
            preview_text.setStyleSheet("font-family: Consolas, monospace; font-size: 11px;")
            preview_layout.addWidget(preview_text)
            layout.addWidget(preview_group)
            
            def update_preview():
                src_code = source_code_edit.text().strip() or "SRC"
                tgt_code = target_code_edit.text().strip() or "TGT"
                start = start_spin.value()
                pad = padding_spin.value()
                
                # Use generic example text (not from current segment)
                sample_source = "Source text goes here..."
                sample_target = "Target text goes here..."
                
                if include_both_radio.isChecked():
                    preview = f"[SEGMENT {start:0{pad}d}]\n{src_code}: {sample_source}\n{tgt_code}: {sample_target}\n"
                elif source_only_radio.isChecked():
                    preview = f"[SEGMENT {start:0{pad}d}]\n{src_code}: {sample_source}\n"
                else:
                    preview = f"[SEGMENT {start:0{pad}d}]\n{tgt_code}: {sample_target}\n"
                
                preview_text.setPlainText(preview)
            
            # Connect signals for live preview
            source_code_edit.textChanged.connect(update_preview)
            target_code_edit.textChanged.connect(update_preview)
            start_spin.valueChanged.connect(update_preview)
            padding_spin.valueChanged.connect(update_preview)
            include_both_radio.toggled.connect(update_preview)
            source_only_radio.toggled.connect(update_preview)
            target_only_radio.toggled.connect(update_preview)
            
            update_preview()  # Initial preview
            
            layout.addSpacing(10)
            
            # Buttons
            button_layout = QHBoxLayout()
            ok_btn = QPushButton(self.tr("Export"))
            ok_btn.setStyleSheet("background-color: #4CAF50; color: white; font-weight: bold; border: none; outline: none;")
            ok_btn.clicked.connect(dialog.accept)
            cancel_btn = QPushButton(self.tr("Cancel"))
            cancel_btn.clicked.connect(dialog.reject)
            
            button_layout.addStretch()
            button_layout.addWidget(cancel_btn)
            button_layout.addWidget(ok_btn)
            layout.addLayout(button_layout)
            
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            
            # Get options
            src_code = source_code_edit.text().strip() or "SRC"
            tgt_code = target_code_edit.text().strip() or "TGT"
            start_at = start_spin.value()
            padding = padding_spin.value()
            
            # Determine content mode
            if source_only_radio.isChecked():
                content_mode = "source_only"
            elif target_only_radio.isChecked():
                content_mode = "target_only"
            else:
                content_mode = "bilingual"
            
            # Filter segments
            if untranslated_radio.isChecked():
                filtered_segments = [s for s in segments if not (s.target and s.target.strip())]
            elif translated_radio.isChecked():
                filtered_segments = [s for s in segments if s.target and s.target.strip()]
            else:
                filtered_segments = segments
            
            if not filtered_segments:
                QMessageBox.warning(
                    self, "No Segments",
                    "No segments match the selected filter criteria."
                )
                return
            
            # Get save path
            default_name = ""
            if self.current_project.name:
                default_name = self.current_project.name.replace(" ", "_") + "_ai_format.txt"
            
            file_path, _ = QFileDialog.getSaveFileName(
                self,
                "Export for AI",
                default_name,
                "Text Files (*.txt);;All Files (*.*)"
            )
            
            if not file_path:
                return
            
            # Ensure .txt extension
            if not file_path.lower().endswith('.txt'):
                file_path += '.txt'
            
            # Build output
            output_lines = []
            segment_num = start_at
            
            for seg in filtered_segments:
                output_lines.append(f"[SEGMENT {segment_num:0{padding}d}]")
                
                if content_mode == "bilingual":
                    output_lines.append(f"{src_code}: {seg.source}")
                    output_lines.append(f"{tgt_code}: {seg.target or ''}")
                elif content_mode == "source_only":
                    output_lines.append(f"{src_code}: {seg.source}")
                else:  # target_only
                    output_lines.append(f"{tgt_code}: {seg.target or ''}")
                
                output_lines.append("")  # Blank line between segments
                segment_num += 1
            
            # Write file
            with open(file_path, 'w', encoding='utf-8', newline='\n') as f:
                f.write('\n'.join(output_lines))
            
            self.log(f"✓ Exported {len(filtered_segments)} segments in AI format to: {os.path.basename(file_path)}")
            
            QMessageBox.information(
                self,
                "Export Complete",
                f"Successfully exported {len(filtered_segments)} segments to:\n{os.path.basename(file_path)}\n\n"
                f"Format: [SEGMENT XXXX] with {src_code}/{tgt_code} labels"
            )
            
        except Exception as e:
            self.log(f"✗ Export for AI failed: {str(e)}")
            QMessageBox.critical(
                self,
                "Export Error",
                f"Failed to export AI format:\n\n{str(e)}"
            )
