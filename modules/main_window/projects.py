"""The main window's projects: new, open, load, save, save as and close, the sample project,
recent projects, automatic and versioned backups, reopening the last project at start and the
project info dialog.

Moved out of Supervertaler.py unchanged (Nov 2026). SupervertalerQt inherits this class, so the
methods still run as its own.

Part of the main window (SupervertalerQt), moved here unchanged. The
methods run as the main window's own: `self` is the SupervertalerQt.
"""

import json
import os
from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtGui import QAction, QColor
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QProgressDialog,
    QPushButton,
    QScrollArea,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)
from datetime import datetime
from modules import file_dialog_helper as fdh
from modules.grid_editors import TagHighlighter, _canon_path
from modules.language_combos import _import_language_pairs
from modules.project_assets import (
    TM_SUBDIR,
    bundle_round_trip_sources,
    bundle_source,
    nest_in_own_folder,
    read_backup,
    resolve_source_path,
    restore_round_trip_sources,
    write_project_file,
)
from modules.segment_model import Project, Segment
from modules.styled_widgets import CheckmarkCheckBox
from modules.tag_text import strip_invisible_markers
from pathlib import Path
from typing import Dict, List


class ProjectsMixin:
    def open_sample_project(self):
        """Help → Open Sample Project (issue #147): a small English → Dutch
        project with a sample TM and glossary that show TermLens, exact and
        fuzzy TM matches (with the differences highlighted), a forbidden term,
        a non-translatable and an inline tag. The TM and glossary are created
        once and reused; nothing else in the user's data is touched."""
        from modules import sample_project as sp

        if self.current_project and self.project_modified:
            reply = QMessageBox.question(
                self, "Unsaved Changes",
                "Save the current project before opening the sample project?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
                | QMessageBox.StandardButton.Cancel)
            if reply == QMessageBox.StandardButton.Cancel:
                return
            if reply == QMessageBox.StandardButton.Yes:
                self.save_project()
                if self.project_modified:
                    return

        try:
            sp.ensure_resources(self.db_manager, self.tm_metadata_mgr, self.termbase_mgr)
        except Exception as e:
            self.log(f"⚠ Sample project: could not set up the sample TM/glossary: {e}")

        segments = [Segment(id=i, source=source, target=target, status=status)
                    for i, (source, target, status) in enumerate(sp.SEGMENTS, start=1)]
        self.current_project = Project(name=sp.PROJECT_NAME, source_lang=sp.SOURCE_LANG,
                                       target_lang=sp.TARGET_LANG, segments=segments,
                                       id=sp.PROJECT_ID)
        # No source document behind it (same reset as New Project)
        self.original_docx = None
        self.current_document_path = None
        self.source_language = sp.SOURCE_LANG
        self.target_language = sp.TARGET_LANG
        self.project_file_path = None
        self.project_modified = False
        self._original_segment_order = self.current_project.segments.copy()

        self.update_window_title()
        self.load_segments_to_grid()
        self.initialize_tm_database()
        self._clear_caches_after_import()
        for refresh in ('_update_scratchpad_for_project', '_refresh_segment_comments_list',
                        '_refresh_proofreading_comments_list', 'tm_tab_refresh_callback',
                        'termbase_tab_refresh_callback', '_finalise_import_with_indexes'):
            fn = getattr(self, refresh, None)
            if callable(fn):
                try:
                    fn()
                except Exception as e:
                    self.log(f"⚠ Sample project: {refresh} failed: {e}")
        self.log(f"🎓 Opened the sample project ({len(segments)} segments, "
                 f"with \u201c{sp.TM_NAME}\u201d and \u201c{sp.GLOSSARY_NAME}\u201d)")

        # Start on a segment with a fuzzy match and glossary terms
        self._navigate_to_segment_by_id(4)
        QMessageBox.information(
            self, "Sample project",
            "This is a small English → Dutch sample project with its own TM and glossary.\n\n"
            "Things to try:\n"
            "• Segment 4 is selected. The match panel shows a fuzzy TM match with the "
            "differences highlighted, and TermLens shows the glossary terms in it.\n"
            "• Segment 3 has a 100% TM match. Segment 5 has a forbidden term "
            "(“pompbehuizing”) next to the preferred one.\n"
            "• Segment 9 has an inline tag: press Ctrl+, to insert it into the target.\n"
            "• Segment 13 has a non-translatable brand name (HydroFlow).\n"
            "• Press Ctrl+Enter to confirm a segment and move on, or Ctrl+T to translate "
            "one with AI (needs an API key in Settings).\n"
            "• Try the Preview, Comments and Scratchpad tabs on the right.\n\n"
            "The sample TM and glossary are listed in the TMs and Termbases tabs, and are "
            "only switched on for this project. Save the project with Ctrl+S if you want "
            "to keep your changes.")

    def new_project(self):
        """Create a new project"""
        from PyQt6.QtWidgets import (
            QVBoxLayout, QHBoxLayout, QFormLayout, QGroupBox, QTextEdit,
            QComboBox, QRadioButton, QButtonGroup, QPushButton, QTabWidget
        )

        # Create dialog
        dialog = QDialog(self)
        dialog.setWindowTitle(self.tr("New Translation Project"))
        dialog.setMinimumWidth(700)
        dialog.setMinimumHeight(500)
        
        layout = QVBoxLayout(dialog)
        
        # Project Settings Group
        settings_group = QGroupBox(self.tr("Project Settings"))
        settings_layout = QFormLayout()
        
        # Project name
        name_input = QLineEdit()
        name_input.setText(self.tr("Untitled Project"))
        name_input.selectAll()
        settings_layout.addRow("Project Name:", name_input)
        
        # Source language
        source_lang_combo = QComboBox()
        # The one language list (modules/language_codes.py), as every picker.
        available_langs = _import_language_pairs()
        for lang_name, lang_code in available_langs:
            source_lang_combo.addItem(lang_name, lang_code)
        settings_layout.addRow("Source Language:", source_lang_combo)

        # Target language
        target_lang_combo = QComboBox()
        for lang_name, lang_code in available_langs:
            target_lang_combo.addItem(lang_name, lang_code)

        # Set defaults based on global language settings
        source_lang_combo.setCurrentText(self.source_language)
        target_lang_combo.setCurrentText(self.target_language)
        
        settings_layout.addRow("Target Language:", target_lang_combo)
        
        settings_group.setLayout(settings_layout)
        layout.addWidget(settings_group)
        
        # Import Options Group
        import_group = QGroupBox(self.tr("Import Source Text"))
        import_layout = QVBoxLayout()
        
        # Tab widget for different import methods
        import_tabs = QTabWidget()
        import_tabs.tabBar().setFocusPolicy(Qt.FocusPolicy.NoFocus)
        import_tabs.tabBar().setDrawBase(False)
        import_tabs.tabBar().setExpanding(False)
        import_tabs.setStyleSheet("QTabBar::tab { outline: 0; } QTabBar::tab:focus { outline: none; } QTabBar::tab:selected { border-bottom: 1px solid #2196F3; background-color: rgba(33, 150, 243, 0.08); }")
        
        # Tab 1: Paste Text
        paste_tab = QWidget()
        paste_layout = QVBoxLayout(paste_tab)
        paste_layout.addWidget(QLabel(self.tr("Paste or type your source text below:")))
        
        text_input = QTextEdit()
        text_input.setPlaceholderText(
            "Paste your text here...\n\n"
            "Text will be automatically segmented into sentences.\n"
            "Each sentence becomes a translatable segment."
        )
        paste_layout.addWidget(text_input)
        import_tabs.addTab(paste_tab, "📝 Paste Text")
        
        # Tab 2: Load from File
        file_tab = QWidget()
        file_layout = QVBoxLayout(file_tab)
        file_layout.addWidget(QLabel(self.tr("Load a plain-text or Markdown (.txt / .md) file:")))
        
        file_path_display = QLineEdit()
        file_path_display.setPlaceholderText(self.tr("No file selected..."))
        file_path_display.setReadOnly(True)
        
        browse_btn = QPushButton(self.tr("📁 Browse..."))
        
        def browse_file():
            file_path, _ = QFileDialog.getOpenFileName(
                dialog,
                "Select a text or Markdown file",
                "",
                "Text & Markdown files (*.txt *.md)"
            )
            if file_path:
                file_path_display.setText(file_path)
                try:
                    with open(file_path, 'r', encoding='utf-8') as f:
                        text_input.setText(f.read())
                    import_tabs.setCurrentIndex(0)  # Switch to paste tab to show loaded text
                except Exception as e:
                    QMessageBox.warning(dialog, "Error", f"Could not load file:\n{e}")
        
        browse_btn.clicked.connect(browse_file)
        
        file_btn_layout = QHBoxLayout()
        file_btn_layout.addWidget(file_path_display)
        file_btn_layout.addWidget(browse_btn)
        file_layout.addLayout(file_btn_layout)

        _load_hint = QLabel(self.tr(
            "Loads a plain-text or Markdown file as source text, segmented into "
            "sentences. For Word, IDML, or bilingual CAT files (memoQ, Trados, "
            "CafeTran, Phrase, Déjà Vu), use Project → Import instead."))
        _load_hint.setWordWrap(True)
        _load_hint.setStyleSheet("color: #666; font-size: 9pt; padding-top: 6px;")
        file_layout.addWidget(_load_hint)
        file_layout.addStretch()

        import_tabs.addTab(file_tab, "📄 Load Text File (.txt / .md)")
        
        # Tab 3: Start Empty
        empty_tab = QWidget()
        empty_layout = QVBoxLayout(empty_tab)
        empty_layout.addWidget(QLabel(self.tr("Create an empty project:")))
        empty_layout.addWidget(QLabel(self.tr("You can add segments manually after creation.")))
        empty_layout.addStretch()
        import_tabs.addTab(empty_tab, "\u2b50 Start Empty")
        
        import_layout.addWidget(import_tabs)
        import_group.setLayout(import_layout)
        layout.addWidget(import_group)

        # Give the project its own folder (project-folder model, #228). Keeps
        # source/ and target/ tidy inside a dedicated folder instead of dropping
        # them into whatever folder the user saves into. Default on.
        subfolder_check = CheckmarkCheckBox(
            self.tr("📁 Create a dedicated folder for this project"))
        subfolder_check.setChecked(self._create_project_subfolder_pref())
        subfolder_check.setToolTip(self.tr(
            "When you first save, place the .svproj in its own folder so its "
            "source/ and target/ subfolders stay self-contained."))
        layout.addWidget(subfolder_check)

        # Buttons
        button_layout = QHBoxLayout()
        button_layout.addStretch()
        
        create_btn = QPushButton(self.tr("Create Project"))
        create_btn.setDefault(True)
        create_btn.setMinimumWidth(120)
        create_btn.clicked.connect(dialog.accept)
        
        cancel_btn = QPushButton(self.tr("Cancel"))
        cancel_btn.setMinimumWidth(120)
        cancel_btn.clicked.connect(dialog.reject)
        
        button_layout.addWidget(create_btn)
        button_layout.addWidget(cancel_btn)
        layout.addLayout(button_layout)
        
        # Show dialog
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        # Remember the folder preference for the first save and future Save As
        # – across sessions too (issue #228).
        self.create_project_subfolder = subfolder_check.isChecked()
        try:
            _gs = self.load_general_settings()
            _gs['create_project_subfolder'] = self.create_project_subfolder
            self.save_general_settings(_gs)
        except Exception as e:
            self.log(f"⚠ Could not remember the project folder preference: {e}")

        # Create project
        project_name = name_input.text().strip() or "Untitled Project"
        source_lang = source_lang_combo.currentData()
        target_lang = target_lang_combo.currentData()
        
        # Validate languages
        if source_lang == target_lang:
            QMessageBox.warning(
                self,
                "Invalid Languages",
                "Source and target languages must be different!"
            )
            return
        
        # Create project
        self.current_project = Project(
            name=project_name,
            source_lang=source_lang,
            target_lang=target_lang,
            segments=[]
        )

        # A New Project (pasted/loaded text or empty) has no bound source
        # document, so clear any path left over from a previously-open project.
        # Otherwise "Export Translated Document" would copy that prior project's
        # file and emit it verbatim. (Same stale-state class fixed in
        # load_project; DOCX import is a separate path that sets these itself.)
        self.original_docx = None
        self.current_document_path = None

        # Sync global language settings with new project languages
        self.source_language = source_lang
        self.target_language = target_lang

        # Process source text if provided
        source_text = text_input.toPlainText().strip()
        if source_text:
            try:
                segmenter = self._make_sentence_segmenter()
                sentences = segmenter.segment_text(source_text)
                
                # Create segments
                for idx, sentence in enumerate(sentences, start=1):
                    if sentence.strip():
                        segment = Segment(
                            id=idx,
                            source=sentence.strip(),
                            target="",
                            status="untranslated"
                        )
                        self.current_project.segments.append(segment)
                
                self.log(f"Created {len(self.current_project.segments)} segments from source text")
            except Exception as e:
                QMessageBox.warning(
                    self,
                    "Segmentation Error",
                    f"Could not segment text:\n{e}\n\nProject created but no segments added."
                )
        
        # Update UI
        self.project_file_path = None
        self.project_modified = True  # Mark as modified since it hasn't been saved

        # Store original segment order for "Document Order" sort reset
        self._original_segment_order = self.current_project.segments.copy()

        self.update_window_title()
        self.load_segments_to_grid()
        self.initialize_tm_database()  # Initialize TM for this project
        
        # Clear scratchpad for new project
        self._update_scratchpad_for_project()
        # Rebuild the all-comments list (empty for a brand-new project)
        self._refresh_segment_comments_list()
        self._refresh_proofreading_comments_list()
        
        self.log(f"Created new project: {project_name} ({source_lang} → {target_lang})")
        
        # Prompt to save
        if self.current_project.segments:
            reply = QMessageBox.question(
                self,
                "Save Project",
                "Would you like to save your new project now?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            if reply == QMessageBox.StandardButton.Yes:
                self.save_project_as()

    def open_project(self):
        """Open a project file"""
        file_path, _ = fdh.get_open_file_name(
            self,
            "Open Project",
            "Supervertaler Projects (*.svproj);;Legacy Projects (*.json);;All Files (*.*)"
        )
        
        if file_path:
            self.load_project(file_path)

    def load_project(self, file_path: str):
        """Load project from file"""
        # ── Progress dialog (indeterminate, modal) ────────────────────
        # Loading large projects (1000+ segments) takes seconds because
        # the grid build runs on the main thread. The dialog gives the
        # user visible feedback and prevents the OS marking the window
        # "Not Responding".
        from PyQt6.QtWidgets import QProgressDialog
        progress = QProgressDialog(
            f"Opening {os.path.basename(file_path)}…",
            None,  # no cancel button
            0, 0, self
        )
        progress.setWindowTitle(self.tr("Opening project"))
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        progress.show()
        QApplication.processEvents()

        try:
            progress.setLabelText("Reading project file…")
            QApplication.processEvents()

            # Try UTF-8 first, fall back to latin-1 if it fails
            recovered_from_backup = False
            try:
                try:
                    with open(file_path, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                except UnicodeDecodeError:
                    self.log(f"⚠ UTF-8 decoding failed, trying latin-1 encoding...")
                    with open(file_path, 'r', encoding='latin-1') as f:
                        data = json.load(f)
            except json.JSONDecodeError as damaged:
                # A damaged project file: offer the copy kept at the last good
                # save (<name>.svproj.bak, issue #228)
                data = self._offer_project_backup(file_path, damaged, progress)
                if data is None:
                    raise
                recovered_from_backup = True
            
            # If no name in file, use filename
            if 'name' not in data:
                data['name'] = Path(file_path).stem
            
            self.current_project = Project.from_dict(data)
            self.project_file_path = file_path
            # Exports must not use the memoQ/Trados/… file of the project
            # that was open before this one
            self._forget_round_trip_state()
            # Opened from the backup: mark it modified so the next save repairs
            # the damaged file
            self.project_modified = recovered_from_backup

            # Clear the previous project's per-segment comment/notes editors up
            # front, so opening this project directly (without closing the last
            # one first) can't leave stale comments in the Comments tab. The
            # all-comments lists are rebuilt further down. (issue #235)
            self._reset_comment_ui_state()

            # Store original segment order for "Document Order" sort reset
            self._original_segment_order = self.current_project.segments.copy()

            # Always reset sort state when loading - project should open in document order
            self.current_sort = None

            # Sync global language settings with project languages
            if self.current_project.source_lang:
                self.source_language = self.current_project.source_lang
            if self.current_project.target_lang:
                self.target_language = self.current_project.target_lang

            # Restore prompt settings if they exist (unified library)
            if hasattr(self.current_project, 'prompt_settings') and self.current_project.prompt_settings:
                prompt_settings = self.current_project.prompt_settings
                if hasattr(self, 'prompt_manager_qt') and self.prompt_manager_qt:
                    library = self.prompt_manager_qt.library
                    
                    # Restore primary prompt
                    primary_path = prompt_settings.get('active_primary_prompt_path')
                    if primary_path:
                        # Check if it's an external prompt (path starts with [EXTERNAL])
                        if primary_path.startswith('[EXTERNAL] '):
                            # Extract the actual file path
                            external_file_path = primary_path[len('[EXTERNAL] '):]
                            if os.path.exists(external_file_path):
                                success, _ = library.set_external_primary_prompt(external_file_path)
                                if success:
                                    # Update the UI label
                                    display_name = Path(external_file_path).stem
                                    self.prompt_manager_qt.primary_prompt_label.setText(f"📁 {display_name}")
                                    self.prompt_manager_qt.primary_prompt_label.setStyleSheet("color: #000; font-weight: bold;")
                                    # Display the external prompt in the editor
                                    self.prompt_manager_qt._display_external_prompt_in_editor(external_file_path, display_name)
                                    self.log(f"✓ Restored external primary prompt: {display_name}")
                            else:
                                self.log(f"⚠ External prompt file not found: {external_file_path}")
                        elif primary_path in library.prompts:
                            # Regular library prompt — exact path match.
                            self.prompt_manager_qt._set_primary_prompt(primary_path)
                            self.log(f"✓ Restored primary prompt: {primary_path}")
                        else:
                            # v1.10.156: stored path no longer exists in the
                            # library. Most common cause: the user renamed or
                            # moved the prompt after activating it, and the
                            # next .svproj save captured the new path (good)
                            # but the OLD .svproj on disk still has the
                            # stored one. Try a basename-match fallback so a
                            # single rename doesn't lose the activation, and
                            # log loudly either way so the user knows.
                            stem = Path(primary_path).stem.lower()
                            candidates = [p for p in library.prompts
                                          if Path(p).stem.lower() == stem]
                            if len(candidates) == 1:
                                fallback = candidates[0]
                                self.prompt_manager_qt._set_primary_prompt(fallback)
                                self.log(f"⚠ Primary prompt path '{primary_path}' no longer exists; "
                                         f"restored by name match to '{fallback}'.")
                            elif len(candidates) > 1:
                                self.log(f"⚠ Primary prompt path '{primary_path}' no longer exists; "
                                         f"name-match found {len(candidates)} candidates — too ambiguous "
                                         f"to auto-select. Set Custom Prompt manually.")
                            else:
                                self.log(f"⚠ Primary prompt path '{primary_path}' no longer exists in the "
                                         f"library (renamed, moved, or deleted). Set Custom Prompt manually.")
                    
                    # Restore attached prompts
                    attached_paths = prompt_settings.get('attached_prompt_paths', [])
                    for path in attached_paths:
                        if path in library.prompts:
                            # Use the UI method to update both internal state and UI
                            self.prompt_manager_qt._attach_prompt(path)
                            self.log(f"✓ Restored attached prompt: {path}")
                    
                    # Restore mode
                    mode = prompt_settings.get('mode', 'single')
                    if hasattr(self.prompt_manager_qt, 'set_mode'):
                        self.prompt_manager_qt.set_mode(mode)
                
                # Restore image context folder
                image_folder = prompt_settings.get('image_context_folder')
                if image_folder and hasattr(self, 'figure_context') and self.figure_context:
                    try:
                        if os.path.exists(image_folder):
                            count = self.figure_context.load_from_folder(image_folder)
                            if count > 0:
                                if hasattr(self, 'image_context_status_label'):
                                    self.image_context_status_label.setText(f"✅ {count} images loaded: {Path(image_folder).name}")
                                    self.image_context_status_label.setStyleSheet("color: #4CAF50; font-weight: bold; font-size: 11px; padding: 5px;")
                                self.log(f"✅ Restored image context: {count} images from {image_folder}")
                        else:
                            self.log(f"⚠️  Saved image folder not found: {image_folder}")
                    except Exception as e:
                        self.log(f"❌ Error restoring image context: {e}")
                
                # Restore Image Extractor file list
                extractor_files = prompt_settings.get('image_extractor_files', [])
                if extractor_files and hasattr(self, 'image_extractor_file_list'):
                    self.image_extractor_file_list.clear()
                    valid_files = []
                    for file_path in extractor_files:
                        if os.path.exists(file_path):
                            self.image_extractor_file_list.addItem(file_path)
                            valid_files.append(file_path)
                        else:
                            self.log(f"⚠️ Image extractor file not found: {file_path}")
                    if valid_files:
                        self.log(f"✅ Restored {len(valid_files)} files in Image Extractor")
            
            # Restore the original DOCX path for structure-preserving export.
            # Reset FIRST: a freshly-loaded project must never inherit the
            # previously-open project's source document. Without this reset,
            # opening a project that has no bound source (or one whose file is
            # missing on this machine) left self.original_docx /
            # self.current_document_path pointing at the *last* project's file —
            # so "Export Translated Document" copied that file and, finding none
            # of the current segments in it, emitted it verbatim. (Reported: a
            # pseudo-translated project exported an earlier project's document.)
            self.original_docx = None
            self.current_document_path = None
            stored_source = getattr(self.current_project, 'original_docx_path', None)
            if stored_source:
                # New projects store this RELATIVE to the project folder
                # (project-folder model, #228); legacy projects store an
                # absolute path. resolve_source_path handles both.
                docx_path = resolve_source_path(stored_source, os.path.dirname(file_path))
                if docx_path and os.path.exists(docx_path):
                    self.original_docx = docx_path
                    self.current_document_path = docx_path
                    self.log(f"✓ Restored original DOCX path: {Path(docx_path).name}")
                else:
                    self.log(f"⚠️ Original DOCX not found (export will rebuild from segments): {stored_source}")
            
            # Round-trip sources (memoQ, Trados, CafeTran, SDLPPX, …) whose
            # original is gone – the project folder was moved, or opened on
            # another computer – are taken from their copies in source/ (#228).
            try:
                switched = restore_round_trip_sources(
                    self.current_project, os.path.dirname(os.path.abspath(file_path)),
                    self.current_project.bundled_sources)
                if switched:
                    self.log(f"✓ Using the copies in the project's source/ folder for: {', '.join(switched)}")
            except Exception as restore_exc:
                self.log(f"⚠ Could not check the project's source/ copies: {restore_exc}")

            # Restore SDLPPX handler for Trados package projects
            if hasattr(self.current_project, 'sdlppx_source_path') and self.current_project.sdlppx_source_path:
                sdlppx_path = self.current_project.sdlppx_source_path
                if os.path.exists(sdlppx_path):
                    try:
                        from modules.sdlppx_handler import TradosPackageHandler
                        self.sdlppx_handler = TradosPackageHandler(log_callback=self.log)
                        package = self.sdlppx_handler.load_package(sdlppx_path)
                        if package:
                            self.sdlppx_source_file = sdlppx_path  # Store source file path for export
                            self.log(f"✓ Restored Trados package handler: {Path(sdlppx_path).name}")
                            self.log(f"  → SDLRPX export enabled for this project")
                        else:
                            self.log(f"⚠️ Could not load SDLPPX package: {sdlppx_path}")
                            self.sdlppx_handler = None
                    except Exception as e:
                        self.log(f"⚠️ Could not restore SDLPPX handler: {e}")
                        self.sdlppx_handler = None
                else:
                    self.log(f"⚠️ SDLPPX package not found: {sdlppx_path}")
                    self.sdlppx_handler = None

            # Restore standalone SDLXLIFF handler
            sdlxliff_paths = getattr(self.current_project, 'sdlxliff_source_paths', None)
            if sdlxliff_paths:
                existing = [p for p in sdlxliff_paths if os.path.exists(p)]
                if existing:
                    try:
                        from modules.sdlppx_handler import StandaloneSDLXLIFFHandler
                        handler = StandaloneSDLXLIFFHandler(log_callback=self.log)
                        if handler.load(existing):
                            self.sdlxliff_handler = handler
                            self.sdlxliff_source_files = existing
                            self.log(f"✓ Restored SDLXLIFF handler: {len(existing)} file(s)")
                            self.log(f"  → SDLXLIFF export enabled for this project")
                        else:
                            self.log(f"⚠️ Could not reload SDLXLIFF files")
                            self.sdlxliff_handler = None
                    except Exception as e:
                        self.log(f"⚠️ Could not restore SDLXLIFF handler: {e}")
                        self.sdlxliff_handler = None
                else:
                    missing = [Path(p).name for p in sdlxliff_paths if not os.path.exists(p)]
                    self.log(f"⚠️ SDLXLIFF source file(s) not found: {', '.join(missing)}")
                    self.sdlxliff_handler = None

            seg_count = len(self.current_project.segments) if self.current_project else 0
            progress.setLabelText(f"Loading {seg_count:,} segments into grid…")
            QApplication.processEvents()

            def _grid_progress(done, total):
                # Show a real filling % bar for the grid-build phase (the
                # dominant cost when opening a large project) instead of the
                # indeterminate spinner — issue #169 ("% progress bar instead
                # of beach ball"). Switch the dialog to determinate on the
                # first callback once the row total is known.
                if total > 0:
                    if progress.maximum() != total:
                        progress.setRange(0, total)
                    progress.setValue(min(done, total))
                progress.setLabelText(
                    f"Loading segments into grid… ({done:,} / {total:,})"
                )
                QApplication.processEvents()

            self.load_segments_to_grid(progress_callback=_grid_progress)

            # Back to an indeterminate spinner for the quick finalisation phases
            # below (TM init, etc.), which have no meaningful per-item total.
            progress.setRange(0, 0)
            progress.setLabelText("Initialising translation memory…")
            QApplication.processEvents()
            self.initialize_tm_database()  # Initialize TM for this project
            self.update_window_title()
            self.add_to_recent_projects(file_path)
            
            # Auto-resize rows for better initial display
            self.auto_resize_rows()
            
            # Update file filter dropdown for multi-file projects
            if hasattr(self, '_update_file_filter_combo'):
                self._update_file_filter_combo()
            
            # Restore activated TMs for this project
            if hasattr(self, 'tm_metadata_mgr') and self.tm_metadata_mgr and self.current_project:
                project_id = self.current_project.id if hasattr(self.current_project, 'id') else None
                if project_id:
                    # First deactivate all TMs for this project (start clean)
                    all_tms = self.tm_metadata_mgr.get_all_tms()
                    for tm in all_tms:
                        self.tm_metadata_mgr.deactivate_tm(tm['id'], project_id)

                    # Then restore saved TM activations if they exist
                    if hasattr(self.current_project, 'tm_settings') and self.current_project.tm_settings:
                        activated_tm_ids = self.current_project.tm_settings.get('activated_tm_ids', [])
                        if activated_tm_ids:
                            # v1.10.213: do NOT auto-apply saved read_only.
                            # translation_memories.read_only is a GLOBAL column
                            # (per-TM, not per-project). Re-applying it on
                            # every project load silently flipped a user's
                            # Write toggles whenever they switched between
                            # projects that had saved different states for
                            # the same shared TM. Activation (Read) is fine
                            # to restore – it's already per-project. Just
                            # warn if the saved Write disagrees with the
                            # current global state so the user can notice.
                            tm_read_only_status = self.current_project.tm_settings.get('tm_read_only_status', {})
                            for tm_id in activated_tm_ids:
                                # Find TM by tm_id (not db id)
                                tm = next((t for t in all_tms if t['tm_id'] == tm_id), None)
                                if tm:
                                    self.tm_metadata_mgr.activate_tm(tm['id'], project_id)
                                    if tm_id in tm_read_only_status:
                                        saved_ro = bool(tm_read_only_status[tm_id])
                                        current_ro = bool(tm.get('read_only', False))
                                        if saved_ro != current_ro:
                                            saved_label = "read-only" if saved_ro else "writable"
                                            current_label = "read-only" if current_ro else "writable"
                                            self.log(
                                                f"ℹ️ Restored activated TM: {tm['name']} "
                                                f"(project saved {saved_label}, current global is {current_label}; "
                                                f"keeping global – set Write manually in the TMs tab if you want it different)"
                                            )
                                        else:
                                            status = "read-only" if current_ro else "writable"
                                            self.log(f"✓ Restored TM: {tm['name']} (activated, {status})")
                                    else:
                                        self.log(f"✓ Restored activated TM: {tm['name']}")
                                else:
                                    self.log(f"⚠️ Could not find TM with tm_id: {tm_id}")
                    else:
                        # NEW PROJECT (no saved tm_settings): Start with clean slate
                        # User should explicitly select which TMs to use for this project
                        pass

                    # Refresh TM UI to show (de)activations
                    if hasattr(self, 'tm_tab_refresh_callback'):
                        self.tm_tab_refresh_callback()

            # Restore activated termbases for this project
            if hasattr(self, 'termbase_mgr') and self.termbase_mgr and self.current_project:
                project_id = self.current_project.id if hasattr(self.current_project, 'id') else None
                if project_id:
                    # First deactivate all termbases for this project (start clean)
                    all_termbases = self.termbase_mgr.get_all_termbases()
                    for tb in all_termbases:
                        self.termbase_mgr.deactivate_termbase(tb['id'], project_id)
                    
                    # Then restore saved active termbases (if any)
                    if hasattr(self.current_project, 'termbase_settings') and self.current_project.termbase_settings:
                        active_termbase_ids = self.current_project.termbase_settings.get('active_termbase_ids', [])
                        termbase_priorities = self.current_project.termbase_settings.get('termbase_priorities', {})
                        
                        if active_termbase_ids:
                            for tb_id in active_termbase_ids:
                                # Find termbase by id
                                tb = next((t for t in all_termbases if t['id'] == tb_id), None)
                                if tb:
                                    self.termbase_mgr.activate_termbase(tb_id, project_id)
                                    self.log(f"✓ Restored activated termbase: {tb['name']}")
                                    
                                    # Restore priority if saved
                                    if str(tb_id) in termbase_priorities:
                                        priority = termbase_priorities[str(tb_id)]
                                        self.termbase_mgr.set_termbase_priority(tb_id, project_id, priority)
                                        self.log(f"✓ Restored termbase priority: {tb['name']} → #{priority}")
                                else:
                                    self.log(f"⚠️ Could not find termbase with id: {tb_id}")
                        else:
                            self.log(f"📋 No active termbases saved for this project")
                    else:
                        self.log(f"📋 No termbase settings found in project file")
                    
                    # Refresh termbase UI to show restored activations
                    if hasattr(self, 'termbase_tab_refresh_callback'):
                        self.termbase_tab_refresh_callback()
                        self.log(f"✓ Refreshed termbase UI with restored activations")
            
            # The legacy "active non-translatables lists" project setting
            # was removed in v1.9.393 along with the standalone NT tab; NTs
            # now ride on termbase activations and don't need their own
            # per-project state. Any nt_settings field in older project
            # files is ignored on load.

            # Restore spellcheck settings from project
            if self.current_project.spellcheck_settings:
                spellcheck_settings = self.current_project.spellcheck_settings
                self.spellcheck_enabled = spellcheck_settings.get('enabled', False)
                TagHighlighter.set_spellcheck_enabled(self.spellcheck_enabled)
                
                # Update UI elements
                if hasattr(self, 'spellcheck_btn'):
                    self.spellcheck_btn.setChecked(self.spellcheck_enabled)
                if hasattr(self, 'spellcheck_toggle_action'):
                    self.spellcheck_toggle_action.setChecked(self.spellcheck_enabled)
                self._update_spellcheck_button_style()
                
                # Always use the project's target language for spellcheck
                # (ignore any saved language - target language is the correct one)
                project_target = self.current_project.target_lang if self.current_project else self.target_language
                if project_target and self.spellcheck_manager.set_language(project_target):
                    if self.spellcheck_enabled:
                        self.log(f"✓ Spellcheck enabled for target language: {project_target}")
                    else:
                        self.log(f"✓ Spellcheck initialized for target language: {project_target} (disabled)")
                else:
                    self.log(f"⚠ Spellcheck dictionary not available for {project_target}")
            else:
                # No spellcheck settings in project - always initialize for target language
                # (even if spellcheck is disabled, set the language so it's ready when enabled)
                if self.current_project:
                    project_target = self.current_project.target_lang
                    if project_target and self.spellcheck_manager.set_language(project_target):
                        self.log(f"✓ Spellcheck initialized for target language: {project_target}")
            
            # Restore results pane font sizes from project
            if hasattr(self.current_project, 'ui_settings') and self.current_project.ui_settings:
                match_size = self.current_project.ui_settings.get('results_match_font_size')
                compare_size = self.current_project.ui_settings.get('results_compare_font_size')

                if match_size and 7 <= match_size <= 16:
                    from modules.translation_results_panel import CompactMatchItem
                    CompactMatchItem.set_font_size(match_size)
                    if hasattr(self, 'results_panels'):
                        for panel in self.results_panels:
                            if hasattr(panel, 'set_font_size'):
                                panel.set_font_size(match_size)

                if compare_size and 7 <= compare_size <= 14:
                    from modules.translation_results_panel import TranslationResultsPanel
                    TranslationResultsPanel.compare_box_font_size = compare_size
                    if hasattr(self, 'results_panels'):
                        for panel in self.results_panels:
                            if hasattr(panel, 'set_compare_box_font_size'):
                                panel.set_compare_box_font_size(compare_size)

                # v1.10.184: also restore Match Panel zoom (View → Match
                # Panel → Zoom In/Out/Reset) from the project. Project
                # value overrides the global default loaded earlier from
                # general_settings. Same accepted range as the View menu
                # (7-18pt). If missing or out of range, the previously-
                # loaded global value stays in effect.
                mp_size = self.current_project.ui_settings.get('match_panel_font_size')
                if mp_size and 7 <= mp_size <= 18:
                    type(self).match_panel_font_size = mp_size
                    self._apply_match_panel_font_size()
            
            # Set Superlookup language dropdowns to match project languages
            if hasattr(self, 'lookup_tab') and self.lookup_tab and self.current_project:
                source_lang = self.current_project.source_lang
                target_lang = self.current_project.target_lang
                if source_lang or target_lang:
                    self.lookup_tab.set_project_languages(source_lang, target_lang)
                    self.log(f"✓ Set Superlookup languages: {source_lang} → {target_lang}")
            
            # Load project-specific settings (override general settings while project is active)
            if hasattr(self.current_project, 'general_settings_overrides') and self.current_project.general_settings_overrides:
                project_settings = self.current_project.general_settings_overrides
                
                # Override general settings with project-specific ones
                if 'auto_center_active_segment' in project_settings:
                    self.auto_center_active_segment = project_settings['auto_center_active_segment']
                    self.log(f"✓ Project override: auto-center = {self.auto_center_active_segment}")
                
                # Consolidated auto-fill setting (new key, back-compat fallback).
                if 'auto_fill_100_matches' in project_settings:
                    self.auto_fill_100_matches = project_settings['auto_fill_100_matches']
                elif 'auto_propagate_exact_matches' in project_settings:
                    self.auto_fill_100_matches = project_settings['auto_propagate_exact_matches']
                elif 'auto_insert_100_percent_matches' in project_settings:
                    self.auto_fill_100_matches = project_settings['auto_insert_100_percent_matches']
                self.auto_propagate_exact_matches = self.auto_fill_100_matches
                self.auto_insert_100_percent_matches = self.auto_fill_100_matches
                self.log(f"✓ Project override: auto-fill 100% = {self.auto_fill_100_matches}")

                if 'auto_fill_confirm' in project_settings:
                    self.auto_fill_confirm = project_settings['auto_fill_confirm']

                if 'auto_propagate_on_confirm' in project_settings:
                    self.auto_propagate_on_confirm = project_settings['auto_propagate_on_confirm']
                if 'auto_propagate_confirm' in project_settings:
                    self.auto_propagate_confirm = project_settings['auto_propagate_confirm']
                if 'auto_propagate_overwrite' in project_settings:
                    self.auto_propagate_overwrite = project_settings['auto_propagate_overwrite']

                if 'auto_confirm_100_percent_matches' in project_settings:
                    self.auto_confirm_100_percent_matches = project_settings['auto_confirm_100_percent_matches']
                    self.log(f"✓ Project override: auto-confirm 100% = {self.auto_confirm_100_percent_matches}")
                
                if 'enable_termbase_grid_highlighting' in project_settings:
                    self.enable_termbase_grid_highlighting = project_settings['enable_termbase_grid_highlighting']
                    self.log(f"✓ Project override: termbase highlighting = {self.enable_termbase_grid_highlighting}")
                
                if 'precision_scroll_divisor' in project_settings:
                    self.precision_scroll_divisor = project_settings['precision_scroll_divisor']
                    self.log(f"✓ Project override: precision scroll = {self.precision_scroll_divisor}")
                
                if 'termbase_hide_shorter_matches' in project_settings:
                    self.termbase_hide_shorter_matches = project_settings['termbase_hide_shorter_matches']
                
                if 'enable_smart_word_selection' in project_settings:
                    self.enable_smart_word_selection = project_settings['enable_smart_word_selection']
                
                if 'enable_sound_effects' in project_settings:
                    self.enable_sound_effects = project_settings['enable_sound_effects']
                    self.log(f"✓ Project override: sound effects = {self.enable_sound_effects}")
                
                if 'sound_effects_map' in project_settings:
                    self.sound_effects_map = project_settings['sound_effects_map']
                    self.log(f"✓ Project override: sound effects map loaded")
            
            # Update scratchpad with project's private notes
            self._update_scratchpad_for_project()
            # Rebuild the all-comments list from the loaded project
            self._refresh_segment_comments_list()
            self._refresh_proofreading_comments_list()
            
            self.log(f"✓ Loaded project: {self.current_project.name} ({len(self.current_project.segments)} segments)")

            
            # Start background batch processing of termbase matches for all segments
            # This pre-fills the cache while user works on the project
            self._start_termbase_batch_worker()

            # v1.10.69: install the auto-refresh file watcher on the
            # active SQLite DB so cross-process termbase edits (most
            # commonly via the Supervertaler for Trados plugin sharing
            # the same DB) automatically trigger an index rebuild.
            # Snapshot-gated and debounced; see
            # _setup_termbase_db_watcher for the full rationale.
            try:
                self._setup_termbase_db_watcher()
            except Exception as _e:
                # Non-fatal — the manual 🔄 button on TermLens always
                # works regardless of whether the watcher succeeded.
                self.log(f"⚠️ Could not install termbase DB auto-refresh watcher: {_e}")

            # Start prefetch worker for first 50 segments (instant switching like memoQ)
            if len(self.current_project.segments) > 0:
                prefetch_ids = [seg.id for seg in self.current_project.segments[:50]]
                self._start_prefetch_worker(prefetch_ids)

        except UnicodeDecodeError as e:
            error_msg = (
                f"Failed to load project - file encoding error:\n\n"
                f"{str(e)}\n\n"
                f"The project file may be corrupted or saved with an incompatible encoding.\n"
                f"This can happen if the file was edited outside Supervertaler.\n\n"
                f"Try opening the file in a text editor and re-saving it as UTF-8."
            )
            QMessageBox.critical(self, "Encoding Error", error_msg)
            self.log(f"✗ Encoding error loading project: {e}")
        except Exception as e:
            # Self-heal: a recent entry that can't be parsed (e.g. a non-project
            # file that slipped into the list, or a corrupt/empty file) is
            # dropped from the recent list so it stops erroring on every click.
            self._remove_from_recent_projects(file_path)
            QMessageBox.critical(self, "Error",
                f"Failed to load project:\n{str(e)}\n\n"
                f"This entry has been removed from your recent projects.")
            self.log(f"✗ Error loading project (removed from recent): {e}")
        finally:
            try:
                progress.close()
            except Exception:
                pass

    def _forget_round_trip_state(self):
        """Forget the round-trip state of the project opened before this one.

        Exports prefer these over the paths stored in the project, so a
        memoQ/Trados/… file loaded for an earlier project was used to export
        the next one – its structure, filled with the new project's segments.
        A Trados package was unpacked into a temporary folder, which goes too.
        """
        old_package = getattr(self, 'sdlppx_handler', None)
        if old_package is not None and hasattr(old_package, 'cleanup'):
            try:
                old_package.cleanup()
            except Exception as cleanup_exc:
                self.log(f"⚠ Could not remove the previous package's temporary files: {cleanup_exc}")
        for attr in self._ROUND_TRIP_STATE:
            setattr(self, attr, None)

    def _offer_project_backup(self, file_path: str, error, progress=None):
        """The project file will not parse: offer the backup copy from the
        last good save. Returns its data, or None to report the error."""
        found = read_backup(file_path)
        if found is None:
            return None
        data, saved_at = found
        if progress is not None:
            progress.hide()
        when = datetime.fromtimestamp(saved_at).strftime('%Y-%m-%d %H:%M')
        reply = QMessageBox.question(
            self, "Project file damaged",
            f"{os.path.basename(file_path)} is damaged and cannot be read:\n{error}\n\n"
            f"A backup copy from the previous save ({when}) is available. "
            "Open the backup? Saving then replaces the damaged file.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes)
        if progress is not None:
            progress.show()
        if reply != QMessageBox.StandardButton.Yes:
            return None
        self.log(f"♻ Opened the backup copy of {os.path.basename(file_path)} (saved {when})")
        return data

    def save_project(self):
        """Save current project"""
        if not self.current_project:
            return
        
        if not self.project_file_path:
            self.save_project_as()
        else:
            self.save_project_to_file(self.project_file_path)

    def save_project_as(self):
        """Save project with new filename"""
        if not self.current_project:
            return

        file_path, _ = fdh.get_save_file_name(
            self,
            "Save Project As",
            "Supervertaler Projects (*.svproj);;All Files (*.*)"
        )

        if file_path:
            # Ensure .svproj extension
            if not file_path.lower().endswith('.svproj'):
                file_path += '.svproj'
            # Optionally tuck the project into its own folder so its source/ and
            # target/ subfolders stay self-contained (project-folder model, #228).
            if self._create_project_subfolder_pref():
                try:
                    file_path = nest_in_own_folder(file_path)
                except Exception as exc:
                    self.log(f"⚠ Could not create a dedicated project folder: {exc}")
            # Update project name to match the new filename
            new_name = Path(file_path).stem
            self.current_project.name = new_name

            self.save_project_to_file(file_path)
            self.project_file_path = file_path
            self.add_to_recent_projects(file_path)

    def _create_project_subfolder_pref(self) -> bool:
        """"Create a dedicated folder for this project" (issue #228): this
        session's choice, else the one remembered in the settings (default on)."""
        if hasattr(self, 'create_project_subfolder'):
            return bool(self.create_project_subfolder)
        try:
            return bool(self.load_general_settings().get('create_project_subfolder', True))
        except Exception:
            return True

    def save_project_to_file(self, file_path: str):
        """Save project to specified file"""
        try:
            self.current_project.modified = datetime.now().isoformat()
            
            # DEBUG: Log first 5 segments before saving
            self.log(f"💾 DEBUG: Saving {len(self.current_project.segments)} segments")
            for i, seg in enumerate(self.current_project.segments[:7]):
                self.log(f"💾 Seg {seg.id}: target='{seg.target[:50] if seg.target else 'EMPTY'}...', status={seg.status}")
            
            # Save prompt settings if prompt manager is available
            if hasattr(self, 'prompt_manager_qt') and self.prompt_manager_qt:
                # Unified prompt library - save active primary and attached prompts
                library = self.prompt_manager_qt.library
                self.current_project.prompt_settings = {
                    'active_primary_prompt_path': library.active_primary_prompt_path,
                    'attached_prompt_paths': library.attached_prompt_paths.copy() if library.attached_prompt_paths else [],
                    'mode': getattr(self.prompt_manager_qt, 'current_mode', 'single'),
                }
            
            # Save image/figure context folder path
            if hasattr(self, 'figure_context') and self.figure_context and self.figure_context.figure_context_folder:
                if not self.current_project.prompt_settings:
                    self.current_project.prompt_settings = {}
                self.current_project.prompt_settings['image_context_folder'] = self.figure_context.figure_context_folder
            
            # Save Image Extractor file list (DOCX files added for image extraction)
            if hasattr(self, 'image_extractor_file_list') and self.image_extractor_file_list:
                if not self.current_project.prompt_settings:
                    self.current_project.prompt_settings = {}
                extractor_files = [self.image_extractor_file_list.item(i).text() 
                                   for i in range(self.image_extractor_file_list.count())]
                if extractor_files:
                    self.current_project.prompt_settings['image_extractor_files'] = extractor_files
            
            # Save activated TM IDs and read_only status for this project
            if hasattr(self, 'tm_metadata_mgr') and self.tm_metadata_mgr and hasattr(self.current_project, 'id'):
                project_id = self.current_project.id
                if project_id:
                    activated_tm_ids = self.tm_metadata_mgr.get_active_tm_ids(project_id)
                    if not hasattr(self.current_project, 'tm_settings'):
                        self.current_project.tm_settings = {}
                    self.current_project.tm_settings['activated_tm_ids'] = activated_tm_ids or []
                    
                    # Save read_only status for each activated TM
                    tm_read_only_status = {}
                    all_tms = self.tm_metadata_mgr.get_all_tms()
                    for tm in all_tms:
                        if tm['tm_id'] in activated_tm_ids:
                            tm_read_only_status[tm['tm_id']] = tm.get('read_only', True)
                    self.current_project.tm_settings['tm_read_only_status'] = tm_read_only_status
            
            # Save results pane font sizes
            if hasattr(self, 'results_panels') and self.results_panels:
                from modules.translation_results_panel import CompactMatchItem, TranslationResultsPanel
                if not self.current_project.ui_settings:
                    self.current_project.ui_settings = {}
                if hasattr(CompactMatchItem, 'font_size_pt'):
                    self.current_project.ui_settings['results_match_font_size'] = CompactMatchItem.font_size_pt
                if hasattr(TranslationResultsPanel, 'compare_box_font_size'):
                    self.current_project.ui_settings['results_compare_font_size'] = TranslationResultsPanel.compare_box_font_size

            # v1.10.184: persist Match Panel zoom (View → Match Panel →
            # Zoom In/Out/Reset) to the project's ui_settings, matching
            # the pattern used above for the results-pane fonts. Pre-
            # v1.10.184 this lived in global general_settings only,
            # which silently failed to round-trip for at least one user
            # — symptom was that the zoom reset to 10pt on every restart
            # despite the menu having been used. Project-side save also
            # follows the per-project mental model better (different
            # documents want different zoom levels).
            if not self.current_project.ui_settings:
                self.current_project.ui_settings = {}
            self.current_project.ui_settings['match_panel_font_size'] = type(self).match_panel_font_size
            
            # Save activated termbase IDs and priorities for this project
            if hasattr(self, 'termbase_mgr') and self.termbase_mgr and hasattr(self.current_project, 'id'):
                project_id = self.current_project.id
                if project_id:
                    active_termbase_ids = self.termbase_mgr.get_active_termbase_ids(project_id)
                    if not hasattr(self.current_project, 'termbase_settings'):
                        self.current_project.termbase_settings = {}
                    self.current_project.termbase_settings['active_termbase_ids'] = active_termbase_ids or []
                    
                    # Also save priorities for each active termbase
                    termbase_priorities = {}
                    for tb_id in active_termbase_ids:
                        priority = self.termbase_mgr.get_termbase_priority(tb_id, project_id)
                        if priority is not None:
                            termbase_priorities[str(tb_id)] = priority
                    self.current_project.termbase_settings['termbase_priorities'] = termbase_priorities
            
            # Save spellcheck settings to project (spellcheck_settings is now properly initialized in dataclass)
            self.current_project.spellcheck_settings['enabled'] = self.spellcheck_enabled
            if hasattr(self, 'target_language'):
                self.current_project.spellcheck_settings['language'] = self.target_language
            
            # Save current general settings as project overrides (while project is active)
            # These will override general_settings.json when this project is loaded
            self.current_project.general_settings_overrides = {
                'auto_center_active_segment': self.auto_center_active_segment,
                'auto_propagate_exact_matches': self.auto_propagate_exact_matches,
                'auto_insert_100_percent_matches': self.auto_insert_100_percent_matches,
                'auto_confirm_100_percent_matches': self.auto_confirm_100_percent_matches,
                'enable_termbase_grid_highlighting': self.enable_termbase_grid_highlighting,
                'precision_scroll_divisor': self.precision_scroll_divisor,
                'termbase_hide_shorter_matches': self.termbase_hide_shorter_matches,
                'enable_smart_word_selection': self.enable_smart_word_selection,
                'enable_sound_effects': self.enable_sound_effects,
                'sound_effects_map': getattr(self, 'sound_effects_map', {}),
            }
            
            # Bundle the source document into the project folder (source/) and
            # store it RELATIVE to the .svproj, so the project is portable and
            # can never bind to an unrelated file (project-folder model, #228).
            # A directory source (multi-file/folder projects) keeps the legacy
            # absolute path. Bundling is a one-time copy: once original_docx is
            # the bundled file, later saves are no-ops.
            original_path = getattr(self, 'original_docx', None) or getattr(self, 'current_document_path', None)
            if original_path and os.path.exists(original_path):
                if os.path.isfile(original_path):
                    project_dir = os.path.dirname(os.path.abspath(file_path))
                    try:
                        rel = bundle_source(original_path, project_dir)
                        if rel:
                            self.current_project.original_docx_path = rel
                            bundled_abs = os.path.normpath(os.path.join(project_dir, rel))
                            self.original_docx = bundled_abs
                            self.current_document_path = bundled_abs
                        else:
                            self.current_project.original_docx_path = original_path
                    except Exception as bundle_exc:
                        self.log(f"⚠ Could not bundle source into project folder: {bundle_exc}")
                        self.current_project.original_docx_path = original_path
                else:
                    # Directory source (multi-file / folder project): legacy path.
                    self.current_project.original_docx_path = original_path

            # The bilingual files and packages the project exports back into
            # (memoQ, Trados, CafeTran, SDLPPX, …) get a copy in source/ as
            # well. The project keeps using the originals; the copies stand in
            # when the originals are gone, e.g. after the folder was moved.
            try:
                self.current_project.bundled_sources = bundle_round_trip_sources(
                    self.current_project, os.path.dirname(os.path.abspath(file_path)),
                    previous=self.current_project.bundled_sources,
                    main_copy=getattr(self, 'original_docx', None)) or None
            except Exception as bundle_exc:
                self.log(f"⚠ Could not copy the round-trip source files into the project folder: {bundle_exc}")

            # IMPORTANT: Always save segments in original document order, not sorted order
            # Store current sort state and temporarily restore original order
            current_sort_state = getattr(self, 'current_sort', None)
            current_segments = self.current_project.segments.copy()  # Save current (possibly sorted) order

            # Restore original order for saving
            if hasattr(self, '_original_segment_order') and self._original_segment_order:
                self.current_project.segments = self._original_segment_order.copy()

            # Sanitise: strip any Show-Invisibles display markers that should
            # never appear in the saved file.  This is a belt-and-braces guard;
            # the markers should already be absent from segment.target, but if
            # any slipped through (e.g. from a race between textChanged and the
            # grid refresh), we catch them here at save time.
            for seg in self.current_project.segments:
                if seg.target:
                    seg.target = strip_invisible_markers(seg.target)

            try:
                # Serialise first, then write through a temporary file that
                # replaces the project in one step, keeping the previous
                # version as <name>.svproj.bak (issue #228). Writing straight
                # into the .svproj left it truncated when a save failed half-way.
                payload = json.dumps(self.current_project.to_dict(), indent=2, ensure_ascii=False)
                write_project_file(file_path, payload)
            finally:
                # Restore the current (sorted) order after saving – also when
                # the save failed
                self.current_project.segments = current_segments
            
            self.project_modified = False
            self.update_window_title()
            self.log(f"✓ Saved project: {Path(file_path).name}")

            # Every N saves, keep a timestamped copy in the backups folder.
            self._maybe_make_versioned_backup(file_path)

        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to save project:\n{str(e)}")
            self.log(f"✗ Error saving project: {e}")

    def _maybe_make_versioned_backup(self, project_path: str):
        """Every N saves, drop a timestamped copy of the .svproj into the
        backups folder (<user_data>/workbench/backups/<project>/), pruning to
        the most recent K. Best-effort: never raises into the save path."""
        try:
            gs = self.load_general_settings()
            if not gs.get('enable_versioned_backup', True):
                return
            every_n = max(1, int(gs.get('versioned_backup_every_n_saves', 1)))
            keep = max(1, int(gs.get('versioned_backup_keep', 100)))

            self._save_count = getattr(self, '_save_count', 0) + 1
            if self._save_count % every_n != 0:
                return

            import shutil
            from datetime import datetime
            src = Path(project_path)
            if not src.exists():
                return
            backups_dir = Path(self.user_data_path) / "workbench" / "backups" / src.stem
            backups_dir.mkdir(parents=True, exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d-%H%M%S")
            dest = backups_dir / f"{src.stem}_{ts}{src.suffix}"
            shutil.copy2(src, dest)
            self.log(f"💾 Versioned backup saved: {dest.name} (every {every_n} saves)")

            # Prune oldest beyond the retention count (timestamped names sort chronologically).
            existing = sorted(backups_dir.glob(f"{src.stem}_*{src.suffix}"))
            for old in existing[:max(0, len(existing) - keep)]:
                try:
                    old.unlink()
                except Exception:
                    pass
        except Exception as e:
            self.log(f"⚠ Versioned backup skipped: {e}")

    def restart_auto_backup_timer(self):
        """Restart the auto backup timer based on current settings"""
        from PyQt6.QtCore import QTimer
        from datetime import datetime

        # Stop existing timer if running
        if self.auto_backup_timer:
            self.auto_backup_timer.stop()
            self.auto_backup_timer = None

        # Load settings
        general_settings = self.load_general_settings()
        enabled = general_settings.get('enable_auto_backup', True)
        interval_minutes = general_settings.get('backup_interval_minutes', 5)

        if not enabled:
            self.log("Auto backup disabled")
            return

        # Create and start timer
        self.auto_backup_timer = QTimer()
        self.auto_backup_timer.timeout.connect(self.perform_auto_backup)
        interval_ms = interval_minutes * 60 * 1000  # Convert minutes to milliseconds
        self.auto_backup_timer.start(interval_ms)

        self.log(f"Auto backup enabled: every {interval_minutes} minute(s)")

    def perform_auto_backup(self):
        """Perform automatic backup of project and TMX"""
        from datetime import datetime
        from modules.tmx_generator import TMXGenerator

        # Check if there's a project to backup
        if not self.current_project or not self.project_file_path:
            return

        try:
            # Save project.json
            self.save_project_to_file(self.project_file_path)

            # Export the TMX into the project's tm/ folder (issue #228). A
            # backup left in the project folder itself by an older version is
            # moved there first.
            project_dir = Path(self.project_file_path).parent
            project_name = Path(self.project_file_path).stem
            tmx_file_path = project_dir / TM_SUBDIR / f"{project_name}_backup.tmx"
            tmx_file_path.parent.mkdir(parents=True, exist_ok=True)
            legacy_tmx = project_dir / f"{project_name}_backup.tmx"
            if legacy_tmx.exists() and not tmx_file_path.exists():
                try:
                    os.replace(legacy_tmx, tmx_file_path)
                except OSError:
                    pass

            # Prepare segments for TMX export
            source_segments = []
            target_segments = []

            for segment in self.current_project.segments:
                source_text = segment.source
                target_text = segment.target if segment.target else ""

                if source_text:
                    source_segments.append(source_text)
                    target_segments.append(target_text)

            if source_segments:
                # Generate and save TMX
                tmx_generator = TMXGenerator(log_callback=self.log)

                source_lang = self.current_project.source_lang or "en"
                target_lang = self.current_project.target_lang or "nl"

                tmx_tree = tmx_generator.generate_tmx(
                    source_segments=source_segments,
                    target_segments=target_segments,
                    source_lang=source_lang,
                    target_lang=target_lang
                )

                tmx_generator.save_tmx(tmx_tree, str(tmx_file_path))

            # Update last backup time
            self.last_backup_time = datetime.now()
            timestamp = self.last_backup_time.strftime("%H:%M:%S")

            self.log(f"💾 Auto backup completed at {timestamp}: {Path(self.project_file_path).name} + {tmx_file_path.name}")

        except Exception as e:
            self.log(f"⚠️ Auto backup failed: {e}")

    def close_project(self):
        """Close current project"""
        if not self.current_project:
            QMessageBox.information(self, "No Project", "No project is currently open")
            return
        
        # Check for unsaved changes
        if self.project_modified:
            reply = QMessageBox.question(
                self, "Unsaved Changes",
                "Save current project before closing?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No | QMessageBox.StandardButton.Cancel
            )
            if reply == QMessageBox.StandardButton.Cancel:
                return
            elif reply == QMessageBox.StandardButton.Yes:
                self.save_project()
                # If save was cancelled or failed, project_modified will still be True
                if self.project_modified:
                    return
        
        # Stop background batch workers
        self.stop_termbase_batch_worker()
        self.stop_prefetch_worker()
        
        # Clear caches for this project
        self.termbase_cache.clear()
        self.translation_matches_cache.clear()
        
        # Clear project data
        self.current_project = None
        self.project_file_path = None
        self.project_modified = False
        
        # Clear the grid
        self.clear_grid()
        
        # Clear translation results in all panels
        if hasattr(self, 'results_panels'):
            for panel in self.results_panels:
                if hasattr(panel, 'clear_results'):
                    panel.clear_results()
                if hasattr(panel, 'tm_results_table'):
                    panel.tm_results_table.setRowCount(0)
                if hasattr(panel, 'termbase_results_table'):
                    panel.termbase_results_table.setRowCount(0)

        # Clear the comment/notes UI so the closed project's comments don't
        # linger in the Comments tab until a restart (issue #235). Clears the
        # per-segment editors and rebuilds the (now empty) all-comments lists.
        self._reset_comment_ui_state()
        self._refresh_segment_comments_list()
        self._refresh_proofreading_comments_list()

        # Update window title
        self.update_window_title()

        self.log("✓ Project closed")

    def update_recent_menu(self):
        """Update recent projects menu"""
        self.recent_menu.clear()
        
        recent_projects = self.load_recent_projects()
        
        if not recent_projects:
            no_recent = QAction("No recent projects", self)
            no_recent.setEnabled(False)
            self.recent_menu.addAction(no_recent)
            return
        
        # Add menu items for each recent project
        for i, project_info in enumerate(recent_projects[:self.MAX_RECENT_PROJECTS]):
            path = project_info['path']
            name = project_info['name']
            
            # Show relative path if in workspace, otherwise just filename
            try:
                rel_path = Path(path).relative_to(Path.cwd())
                display_name = f"{i+1}. {name} ({rel_path.parent})"
            except ValueError:
                display_name = f"{i+1}. {name}"
            
            action = QAction(display_name, self)
            action.setStatusTip(path)
            action.triggered.connect(lambda checked, p=path: self._load_recent_project_with_resize(p))
            self.recent_menu.addAction(action)
        
        # Add separator and "Clear Recent" option
        if recent_projects:
            self.recent_menu.addSeparator()
            clear_action = QAction("Clear Recent Projects", self)
            clear_action.triggered.connect(self.clear_recent_projects)
            self.recent_menu.addAction(clear_action)

    def _load_recent_project_with_resize(self, project_path: str):
        """Load a project from recent menu and auto-enable row resize"""
        self.load_project(project_path)
        # Auto-enable row resize after loading
        if hasattr(self, 'auto_resize_rows'):
            QTimer.singleShot(100, self.auto_resize_rows)  # Slight delay to ensure grid is fully loaded

    def add_to_recent_projects(self, file_path: str):
        """Add project to recent projects list"""
        if not file_path or not os.path.exists(file_path):
            return

        # Only actual project files belong in the recent-PROJECTS list. Source
        # documents (.docx, .sdlxliff, …) opened or imported via other paths must
        # never land here – clicking one later runs json.load() on it and fails
        # with "Expecting value: line 1 column 1 (char 0)".
        if not file_path.lower().endswith('.svproj'):
            return

        # Load existing recent projects
        recent_projects = self.load_recent_projects()

        abs_path = os.path.abspath(file_path)

        # Use the open project's display name only when this entry IS that
        # project; otherwise fall back to the file's own stem, so an entry can
        # never appear under a different project's name.
        if (self.current_project
                and getattr(self, 'project_file_path', None)
                and _canon_path(self.project_file_path) == _canon_path(abs_path)):
            entry_name = self.current_project.name
        else:
            entry_name = Path(file_path).stem

        # Create new entry
        new_entry = {
            'path': abs_path,
            'name': entry_name,
            'last_opened': datetime.now().isoformat()
        }

        # Remove any existing entry for the same file (case/separator-insensitive
        # on Windows) so the same project can't appear twice.
        new_canon = _canon_path(abs_path)
        recent_projects = [p for p in recent_projects if _canon_path(p.get('path', '')) != new_canon]
        
        # Add to beginning
        recent_projects.insert(0, new_entry)
        
        # Keep only MAX_RECENT_PROJECTS
        recent_projects = recent_projects[:self.MAX_RECENT_PROJECTS]
        
        # Save back to file
        self.save_recent_projects(recent_projects)
        
        # Update menu and home display
        self.update_recent_menu()
        self.update_recent_projects_display()

    def _remove_from_recent_projects(self, file_path: str):
        """Remove a single entry from the recent-projects list (e.g. after it
        failed to open) and refresh the menu / home display."""
        if not file_path:
            return
        try:
            target = _canon_path(file_path)
            recent_projects = self.load_recent_projects()
            filtered = [p for p in recent_projects if _canon_path(p.get('path', '')) != target]
            if len(filtered) != len(recent_projects):
                self.save_recent_projects(filtered)
                self.update_recent_menu()
                self.update_recent_projects_display()
        except Exception as e:
            self.log(f"Could not remove '{file_path}' from recent projects: {e}")

    def load_recent_projects(self) -> List[Dict[str, str]]:
        """Load recent projects from file"""
        if not self.recent_projects_file.exists():
            return []
        
        try:
            # Try UTF-8 first, fall back to latin-1 if it fails
            try:
                with open(self.recent_projects_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
            except UnicodeDecodeError:
                self.log(f"⚠ UTF-8 decoding failed for recent projects, trying latin-1...")
                with open(self.recent_projects_file, 'r', encoding='latin-1') as f:
                    data = json.load(f)
            
            # Handle both old dict format and new list format
            if isinstance(data, dict):
                # Old format: convert to list
                recent = []
                for key, value in data.items():
                    if isinstance(value, list):
                        # Extract path and name from old list format
                        for item in value:
                            if isinstance(item, dict):
                                recent.append(item)
                            else:
                                # String path
                                recent.append({
                                    'path': str(item),
                                    'name': Path(str(item)).stem,
                                    'last_opened': datetime.now().isoformat()
                                })
                    elif isinstance(value, str):
                        recent.append({
                            'path': value,
                            'name': Path(value).stem,
                            'last_opened': datetime.now().isoformat()
                        })
                return recent
            elif isinstance(data, list):
                # New format: already a list
                # Ensure all entries have required fields
                normalized = []
                for item in data:
                    if isinstance(item, dict) and 'path' in item:
                        # Ensure all required fields exist
                        if 'name' not in item:
                            item['name'] = Path(item['path']).stem
                        if 'last_opened' not in item:
                            item['last_opened'] = datetime.now().isoformat()
                        # Only include if the file still exists AND is an actual
                        # project file – this auto-purges any stray non-.svproj
                        # entries (e.g. source documents) left by older builds.
                        if (os.path.exists(item['path'])
                                and str(item['path']).lower().endswith('.svproj')):
                            normalized.append(item)
                return normalized
            
            return []
        
        except Exception as e:
            self.log(f"Error loading recent projects: {e}")
            return []

    def save_recent_projects(self, recent_projects: List[Dict[str, str]]):
        """Save recent projects to file"""
        try:
            # Ensure directory exists
            self.user_data_path.mkdir(parents=True, exist_ok=True)
            
            with open(self.recent_projects_file, 'w', encoding='utf-8') as f:
                json.dump(recent_projects, f, indent=2, ensure_ascii=False)
        
        except Exception as e:
            self.log(f"Error saving recent projects: {e}")

    def update_recent_projects_display(self):
        """Update the recent projects display on the home screen"""
        if not hasattr(self, 'recent_projects_layout'):
            return
        
        # Clear existing widgets
        for i in reversed(range(self.recent_projects_layout.count())):
            item = self.recent_projects_layout.itemAt(i)
            if item and item.widget():
                item.widget().deleteLater()
        
        # Load recent projects
        recent_projects = self.load_recent_projects()
        
        if not recent_projects:
            no_recent = QLabel(self.tr("No recent projects"))
            no_recent.setStyleSheet("color: #888; font-style: italic; padding: 10px;")
            no_recent.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.recent_projects_layout.addWidget(no_recent)
            return
        
        # Add project buttons
        for project_info in recent_projects[:5]:  # Show max 5 on home screen
            path = project_info['path']
            name = project_info.get('name', Path(path).stem)
            
            btn = QPushButton(f"📄 {name}")
            btn.setStyleSheet("text-align: left; padding: 8px; border-radius: 3px;")
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda checked, p=path: self.load_project(p))
            self.recent_projects_layout.addWidget(btn)
        
        self.recent_projects_layout.addStretch()

    def clear_recent_projects(self):
        """Clear all recent projects"""
        reply = QMessageBox.question(
            self,
            "Clear Recent Projects",
            "Are you sure you want to clear all recent projects?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        
        if reply == QMessageBox.StandardButton.Yes:
            self.save_recent_projects([])
            self.update_recent_menu()
            if hasattr(self, 'recent_projects_layout'):
                self.update_recent_projects_display()
            self.log("Recent projects cleared")

    def restore_last_project_if_enabled(self):
        """Restore the last opened project on startup if the setting is enabled"""
        try:
            # Check if setting is enabled
            general_settings = self.load_general_settings()
            if not general_settings.get('restore_last_project', False):
                return
            
            # Get recent projects
            recent_projects = self.load_recent_projects()
            if not recent_projects:
                return
            
            # Get the most recent project (first in the list)
            # Recent projects are sorted by most recent first
            last_project = recent_projects[0]
            project_path = last_project.get('path') if isinstance(last_project, dict) else last_project
            
            # Verify file exists
            if not project_path or not os.path.exists(project_path):
                self.log(f"⚠ Last project file not found: {project_path}")
                return
            
            # Load the project
            self.log(f"🔄 Restoring last project: {Path(project_path).name}")
            self.load_project(project_path)
            
        except Exception as e:
            # Silently fail - don't block startup if restoration fails
            self.log(f"⚠ Could not restore last project: {e}")

    def _deactivate_all_resources_for_new_project(self):
        """Ensure new projects start with a clean slate - no TMs or termbases selected.

        This is called when importing a new document to create a fresh project.
        By design, users should explicitly choose which TMs and glossaries to use
        for each project, rather than inheriting selections from previous work.
        """
        if not self.current_project or not hasattr(self.current_project, 'id'):
            return

        project_id = self.current_project.id
        if not project_id:
            return

        # Deactivate all TMs for this project (clean slate: Read OFF + Write OFF)
        if hasattr(self, 'tm_metadata_mgr') and self.tm_metadata_mgr:
            all_tms = self.tm_metadata_mgr.get_all_tms()
            if all_tms:
                for tm in all_tms:
                    self.tm_metadata_mgr.deactivate_tm(tm['id'], project_id)
                    self.tm_metadata_mgr.set_read_only(tm['id'], True)  # Write OFF

        # Deactivate all termbases for this project (clean slate: Read OFF + Write OFF)
        if hasattr(self, 'termbase_mgr') and self.termbase_mgr:
            all_termbases = self.termbase_mgr.get_all_termbases()
            if all_termbases:
                for tb in all_termbases:
                    self.termbase_mgr.deactivate_termbase(tb['id'], project_id)
                    self.termbase_mgr.set_termbase_read_only(tb['id'], True)  # Write OFF

        # The legacy "deactivate NT lists" step was removed in v1.9.393
        # along with the standalone Non-Translatables tab – NTs now live
        # on termbase entries and follow the termbase activation state.

        # Refresh TM and termbase UI to reflect the deactivations
        if hasattr(self, 'tm_tab_refresh_callback') and self.tm_tab_refresh_callback:
            self.tm_tab_refresh_callback()
        if hasattr(self, 'termbase_tab_refresh_callback') and self.termbase_tab_refresh_callback:
            self.termbase_tab_refresh_callback()

    def show_project_info_dialog(self, initial_tab=0):
        """Show comprehensive project information dialog with tabs.

        Args:
            initial_tab: Tab index to show initially (0=Overview, 1=File Progress)
        """
        if not self.current_project:
            QMessageBox.information(self, "No Project", "Please open or create a project first.")
            return

        from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QGroupBox,
                                      QScrollArea, QFrame, QTabWidget, QTableWidget,
                                      QTableWidgetItem, QHeaderView, QProgressBar)
        from PyQt6.QtCore import Qt
        from PyQt6.QtGui import QColor
        from datetime import datetime

        proj = self.current_project

        # Calculate statistics
        total_segments = len(proj.segments) if proj.segments else 0
        translated = sum(1 for s in proj.segments if s.target and s.target.strip()) if proj.segments else 0
        confirmed = sum(1 for s in proj.segments if s.status == "confirmed") if proj.segments else 0
        draft = sum(1 for s in proj.segments if s.status == "draft") if proj.segments else 0
        not_started = sum(1 for s in proj.segments if s.status in ("not_started", "")) if proj.segments else 0

        # Word counts
        source_words = sum(len(s.source.split()) for s in proj.segments if s.source) if proj.segments else 0
        target_words = sum(len(s.target.split()) for s in proj.segments if s.target) if proj.segments else 0

        # Character counts
        source_chars = sum(len(s.source) for s in proj.segments if s.source) if proj.segments else 0
        target_chars = sum(len(s.target) for s in proj.segments if s.target) if proj.segments else 0

        dialog = QDialog(self)
        dialog.setWindowTitle(self.tr("Project Information"))
        dialog.resize(750, 700)
        dialog.setMinimumWidth(600)
        dialog.setMinimumHeight(500)

        main_layout = QVBoxLayout(dialog)
        main_layout.setSpacing(10)
        main_layout.setContentsMargins(15, 15, 15, 15)

        # ═══════════════ Tab Widget ═══════════════
        tabs = QTabWidget()
        tabs.tabBar().setFocusPolicy(Qt.FocusPolicy.NoFocus)
        tabs.tabBar().setDrawBase(False)
        tabs.tabBar().setExpanding(False)
        tabs.setStyleSheet("QTabBar::tab { outline: 0; } QTabBar::tab:focus { outline: none; } QTabBar::tab:selected { border-bottom: 1px solid #2196F3; background-color: rgba(33, 150, 243, 0.08); }")

        # ═══════════════════════════════════════════════
        # TAB 1: Overview
        # ═══════════════════════════════════════════════
        overview_scroll = QScrollArea()
        overview_scroll.setWidgetResizable(True)
        overview_scroll.setFrameShape(QFrame.Shape.NoFrame)

        overview_content = QWidget()
        layout = QVBoxLayout(overview_content)
        layout.setSpacing(12)

        # ─── Project Overview ───
        overview_group = QGroupBox(self.tr("Project Overview"))
        overview_layout = QVBoxLayout(overview_group)

        overview_layout.addWidget(QLabel(f"<b>Name:</b> {proj.name}"))
        if hasattr(self, 'current_project_path') and self.current_project_path:
            overview_layout.addWidget(QLabel(f"<b>File:</b> <span style='color: #666;'>{self.current_project_path}</span>"))

        overview_layout.addWidget(QLabel(f"<b>Languages:</b> {proj.source_lang} → {proj.target_lang}"))

        try:
            created_dt = datetime.fromisoformat(proj.created) if proj.created else None
            created_str = created_dt.strftime("%Y-%m-%d %H:%M") if created_dt else "Unknown"
        except:
            created_str = proj.created or "Unknown"

        try:
            modified_dt = datetime.fromisoformat(proj.modified) if proj.modified else None
            modified_str = modified_dt.strftime("%Y-%m-%d %H:%M") if modified_dt else "Unknown"
        except:
            modified_str = proj.modified or "Unknown"

        overview_layout.addWidget(QLabel(f"<b>Created:</b> {created_str}"))
        overview_layout.addWidget(QLabel(f"<b>Modified:</b> {modified_str}"))

        if proj.id:
            overview_layout.addWidget(QLabel(f"<b>Project ID:</b> <span style='color: #999;'>{proj.id}</span>"))

        layout.addWidget(overview_group)

        # ─── Statistics ───
        stats_group = QGroupBox(self.tr("Statistics"))
        stats_main_layout = QHBoxLayout(stats_group)

        left_col = QVBoxLayout()
        progress_pct = (translated / total_segments * 100) if total_segments > 0 else 0
        left_col.addWidget(QLabel(f"<b>Total Segments:</b> {total_segments:,}"))
        left_col.addWidget(QLabel(f"<b>Translated:</b> {translated:,} ({progress_pct:.1f}%)"))
        left_col.addWidget(QLabel(f"<b>Confirmed:</b> {confirmed:,}"))
        left_col.addWidget(QLabel(f"<b>Draft:</b> {draft:,}"))
        left_col.addWidget(QLabel(f"<b>Not Started:</b> {not_started:,}"))
        left_col.addStretch()

        right_col = QVBoxLayout()
        right_col.addWidget(QLabel(f"<b>Source Words:</b> {source_words:,}"))
        right_col.addWidget(QLabel(f"<b>Target Words:</b> {target_words:,}"))
        right_col.addWidget(QLabel(f"<b>Source Characters:</b> {source_chars:,}"))
        right_col.addWidget(QLabel(f"<b>Target Characters:</b> {target_chars:,}"))
        right_col.addStretch()

        stats_main_layout.addLayout(left_col)
        stats_main_layout.addLayout(right_col)

        layout.addWidget(stats_group)

        # ─── Source Files ───
        has_sources = any([
            proj.original_docx_path, proj.memoq_source_path, proj.cafetran_source_path,
            proj.trados_source_path, proj.sdlppx_source_path,
            getattr(proj, 'sdlxliff_source_paths', None), proj.original_txt_path,
            proj.is_multifile
        ])

        if has_sources:
            source_group = QGroupBox(self.tr("Source Files"))
            source_layout = QVBoxLayout(source_group)

            if proj.is_multifile and proj.files:
                source_layout.addWidget(QLabel(f"<b>Multi-file Project:</b> {len(proj.files)} files"))
                for f in proj.files[:5]:
                    source_layout.addWidget(QLabel(f"  \u2022 {f.get('name', 'Unknown')}"))
                if len(proj.files) > 5:
                    source_layout.addWidget(QLabel(f"  <i>... and {len(proj.files) - 5} more</i>"))
            else:
                if proj.original_docx_path:
                    source_layout.addWidget(QLabel(f"<b>DOCX:</b> {proj.original_docx_path}"))
                if proj.memoq_source_path:
                    source_layout.addWidget(QLabel(f"<b>memoQ:</b> {proj.memoq_source_path}"))
                if proj.cafetran_source_path:
                    source_layout.addWidget(QLabel(f"<b>CafeTran:</b> {proj.cafetran_source_path}"))
                if proj.trados_source_path:
                    source_layout.addWidget(QLabel(f"<b>Trados DOCX:</b> {proj.trados_source_path}"))
                if proj.sdlppx_source_path:
                    source_layout.addWidget(QLabel(f"<b>Trados Package:</b> {proj.sdlppx_source_path}"))
                sdlxliff_paths = getattr(proj, 'sdlxliff_source_paths', None)
                if sdlxliff_paths:
                    if len(sdlxliff_paths) == 1:
                        source_layout.addWidget(QLabel(f"<b>SDLXLIFF:</b> {sdlxliff_paths[0]}"))
                    else:
                        source_layout.addWidget(QLabel(f"<b>SDLXLIFF:</b> {len(sdlxliff_paths)} files"))
                        for p in sdlxliff_paths[:3]:
                            source_layout.addWidget(QLabel(f"  - {Path(p).name}"))
                        if len(sdlxliff_paths) > 3:
                            source_layout.addWidget(QLabel(f"  ... and {len(sdlxliff_paths) - 3} more"))
                if proj.original_txt_path:
                    source_layout.addWidget(QLabel(f"<b>Text File:</b> {proj.original_txt_path}"))

            layout.addWidget(source_group)

        # ─── AI & Prompts ───
        ai_group = QGroupBox(self.tr("AI && Prompts"))
        ai_layout = QVBoxLayout(ai_group)

        # The AI provider and model currently in use (issue #93) – resolved the
        # same way as the status-bar indicator, so the two always agree.
        try:
            import html as _html
            llm_settings = self.load_llm_settings()
            provider = llm_settings.get('provider', 'openai')
            model = self._resolve_provider_model(llm_settings, provider, 'gpt-6.1-sol')
            provider_names = {
                'openai': "OpenAI", 'claude': "Anthropic Claude", 'gemini': "Google Gemini",
                'mistral': "Mistral AI", 'deepseek': "DeepSeek", 'openrouter': "OpenRouter",
                'ollama': "Local LLM (Ollama)", 'custom_openai': "Custom (OpenAI-compatible)",
            }
            provider_text = provider_names.get(provider, provider)
            if provider == 'custom_openai':
                profile = self._get_active_custom_profile(llm_settings)
                if profile and profile.get('name'):
                    provider_text += f" – {profile['name']}"
            ai_layout.addWidget(QLabel(
                f"<b>AI Model:</b> {_html.escape(model or 'not set')} "
                f"<span style='color: #999;'>({_html.escape(provider_text)})</span>"))
        except Exception as e:
            self.log(f"Error getting AI model info: {e}")
            provider = model = None

        # Per-job AI cost (issue #8): what the usage log has recorded for this
        # project so far, and an estimate for AI-translating what is left.
        try:
            from modules import usage_log
            from modules.cost_estimate import format_cost
            currency, rate = self._cost_display_settings()
            spent = usage_log.project_totals(proj.name)
            if spent['calls']:
                spent_text = self.tr("{} over {:,} AI call(s) · {:,} tokens in / {:,} out").format(
                    format_cost(spent['cost_usd'], currency, rate),
                    spent['calls'], spent['input'], spent['output'])
                if spent.get('unpriced'):
                    spent_text += self.tr(" · {:,} call(s) with a model not in the price list").format(
                        spent['unpriced'])
            else:
                spent_text = self.tr("none recorded yet")
            spent_label = QLabel(f"<b>{self.tr('AI cost so far:')}</b> {spent_text}")
            spent_label.setToolTip(self.tr(
                "From the token-usage log (Tools → Token Usage & Costs), matched on the\n"
                "project name. Calls made while the log was switched off are not counted."))
            ai_layout.addWidget(spent_label)

            remaining = [s for s in proj.segments if (s.source or '').strip()
                         and not (s.target or '').strip()]
            if remaining and provider and model:
                est = self._estimate_batch_ai_cost(remaining, provider, model)
                if provider == 'ollama':
                    est_text = self.tr("free (local model)")
                elif est['cost_usd'] is None:
                    est_text = self.tr("unknown – this model is not in the price list")
                else:
                    est_text = "~" + format_cost(est['cost_usd'], currency, rate)
                ai_layout.addWidget(QLabel(
                    f"<b>{self.tr('Estimated cost to AI-translate the rest:')}</b> "
                    + self.tr("{} for {:,} empty segment(s)").format(est_text, len(remaining))))
        except Exception as e:
            self.log(f"Error getting AI cost info: {e}")

        primary_prompt_text = "None"
        attached_count = 0
        if hasattr(self, 'prompt_manager_qt') and self.prompt_manager_qt:
            library = self.prompt_manager_qt.library
            if library.active_primary_prompt_path:
                prompt_path = library.active_primary_prompt_path
                if prompt_path.startswith("[EXTERNAL]"):
                    import os
                    primary_prompt_text = f"{os.path.basename(prompt_path.replace('[EXTERNAL] ', ''))}"
                else:
                    primary_prompt_text = f"{prompt_path}"
            attached_count = len(library.attached_prompt_paths) if library.attached_prompt_paths else 0

        ai_layout.addWidget(QLabel(f"<b>Custom Prompt:</b> {primary_prompt_text}"))
        if attached_count > 0:
            ai_layout.addWidget(QLabel(f"<b>Attached Prompts:</b> {attached_count}"))

        layout.addWidget(ai_group)

        # ─── Translation Memories ───
        tm_group = QGroupBox(self.tr("Translation Memories"))
        tm_layout = QVBoxLayout(tm_group)

        active_tm_info = []
        if hasattr(self, 'tm_metadata_mgr') and self.tm_metadata_mgr:
            try:
                all_tms = self.tm_metadata_mgr.get_all_tms()
                for tm in all_tms:
                    if self.tm_metadata_mgr.is_tm_active(tm['id'], proj.id):
                        active_tm_info.append((tm['name'], tm.get('entry_count', 0)))
            except Exception as e:
                self.log(f"Error getting TM info: {e}")

        if active_tm_info:
            for tm_name, tu_count in active_tm_info:
                tm_layout.addWidget(QLabel(f"<b>{tm_name}</b> ({tu_count:,} TUs)"))
        else:
            tm_layout.addWidget(QLabel(self.tr("<span style='color: #999;'>No TMs activated</span>")))

        layout.addWidget(tm_group)

        # ─── Glossaries ───
        gloss_group = QGroupBox(self.tr("Termbases"))
        gloss_layout = QVBoxLayout(gloss_group)

        active_tb_info = []
        if hasattr(self, 'termbase_mgr') and self.termbase_mgr:
            try:
                all_termbases = self.termbase_mgr.get_all_termbases()
                for tb in all_termbases:
                    if self.termbase_mgr.is_termbase_active(tb['id'], proj.id):
                        active_tb_info.append((tb['name'], tb.get('term_count', 0)))
            except Exception as e:
                self.log(f"Error getting termbase info: {e}")

        if active_tb_info:
            for tb_name, term_count in active_tb_info:
                gloss_layout.addWidget(QLabel(f"<b>{tb_name}</b> ({term_count:,} terms)"))
        else:
            gloss_layout.addWidget(QLabel(self.tr("<span style='color: #999;'>No termbases activated</span>")))

        layout.addWidget(gloss_group)

        # ─── Image Context ───
        image_group = QGroupBox(self.tr("Image Context"))
        image_layout = QVBoxLayout(image_group)

        if hasattr(self, 'figure_context') and self.figure_context and self.figure_context.has_images():
            count = self.figure_context.get_image_count()
            folder = self.figure_context.get_folder_name() or "Unknown folder"
            image_layout.addWidget(QLabel(f"<b>{count} image{'s' if count != 1 else ''}</b> loaded from: {folder}"))
        else:
            image_layout.addWidget(QLabel(self.tr("<span style='color: #999;'>No images loaded</span>")))

        layout.addWidget(image_group)

        # ─── Other Settings ───
        settings_group = QGroupBox(self.tr("Other Settings"))
        settings_layout = QVBoxLayout(settings_group)

        if hasattr(self, 'spellcheck_manager') and self.spellcheck_manager:
            sp_enabled = self.spellcheck_manager.enabled
            sp_lang = self.spellcheck_manager._current_language or "Not set"
            sp_status = f"Enabled ({sp_lang})" if sp_enabled else "Disabled"
            settings_layout.addWidget(QLabel(f"<b>Spellcheck:</b> {sp_status}"))
        else:
            settings_layout.addWidget(QLabel(f"<b>Spellcheck:</b> Not available"))

        layout.addWidget(settings_group)

        layout.addStretch()

        overview_scroll.setWidget(overview_content)
        tabs.addTab(overview_scroll, "Overview")

        # ═══════════════════════════════════════════════
        # TAB 2: File Progress
        # ═══════════════════════════════════════════════
        progress_tab = QWidget()
        progress_layout = QVBoxLayout(progress_tab)

        is_multifile = getattr(proj, 'is_multifile', False)
        files = getattr(proj, 'files', [])

        if not is_multifile or not files:
            # Single-file project
            info_label = QLabel(
                "<b>Single-File Project</b><br><br>"
                "This is a single-file project. Multi-file progress tracking is available "
                "when you import a folder using:<br>"
                "<i>File \u2192 Import \u2192 Folder (Multiple Files)...</i>"
            )
            info_label.setWordWrap(True)
            info_label.setTextFormat(Qt.TextFormat.RichText)
            progress_layout.addWidget(info_label)
            progress_layout.addSpacing(20)
        else:
            # Multi-file header
            info_label = QLabel(
                f"<b>Multi-File Project: {len(files)} files</b><br>"
                f"Project: {proj.name}"
            )
            info_label.setTextFormat(Qt.TextFormat.RichText)
            progress_layout.addWidget(info_label)
            progress_layout.addSpacing(10)

        # Overall progress section (shown for both single and multi-file)
        self._add_overall_progress_section(progress_layout)

        if is_multifile and files:
            progress_layout.addSpacing(10)

            # Per-file progress table
            files_group = QGroupBox(self.tr("Per-File Progress"))
            files_layout = QVBoxLayout(files_group)

            table = QTableWidget()
            table.setColumnCount(7)
            table.setHorizontalHeaderLabels([
                "File", "Segments", "Words", "Translated", "Confirmed", "Progress", "Status"
            ])
            table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
            table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.Fixed)
            table.setColumnWidth(5, 120)
            table.setRowCount(len(files))
            table.setAlternatingRowColors(True)
            table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
            table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)

            segments = proj.segments
            confirmed_statuses = {'confirmed', 'proofread', 'approved'}

            for row, file_info in enumerate(files):
                file_id = file_info['id']
                file_name = file_info['name']

                file_segments = [s for s in segments if getattr(s, 'file_id', None) == file_id]
                total_segs = len(file_segments)

                total_words = sum(len(s.source.split()) for s in file_segments if s.source)
                translated_segs = sum(1 for s in file_segments if s.target and s.target.strip())
                confirmed_segs = sum(1 for s in file_segments if s.status in confirmed_statuses)

                trans_percent = (translated_segs / total_segs * 100) if total_segs > 0 else 0
                conf_percent = (confirmed_segs / total_segs * 100) if total_segs > 0 else 0

                if conf_percent == 100:
                    status = "Complete"
                    status_color = "#4CAF50"
                elif trans_percent == 100:
                    status = "Translated"
                    status_color = "#2196F3"
                elif trans_percent > 0:
                    status = "In Progress"
                    status_color = "#FF9800"
                else:
                    status = "Not Started"
                    status_color = "#9E9E9E"

                table.setItem(row, 0, QTableWidgetItem(file_name))
                table.setItem(row, 1, QTableWidgetItem(str(total_segs)))
                table.setItem(row, 2, QTableWidgetItem(str(total_words)))
                table.setItem(row, 3, QTableWidgetItem(f"{translated_segs}/{total_segs} ({trans_percent:.0f}%)"))
                table.setItem(row, 4, QTableWidgetItem(f"{confirmed_segs}/{total_segs} ({conf_percent:.0f}%)"))

                progress_bar = QProgressBar()
                progress_bar.setMinimum(0)
                progress_bar.setMaximum(100)
                progress_bar.setValue(int(conf_percent))
                progress_bar.setTextVisible(True)
                progress_bar.setFormat(f"{conf_percent:.0f}%")
                table.setCellWidget(row, 5, progress_bar)

                status_item = QTableWidgetItem(status)
                status_item.setForeground(QColor(status_color))
                table.setItem(row, 6, status_item)

                trans_item = table.item(row, 3)
                if trans_percent == 100:
                    trans_item.setForeground(QColor("#4CAF50"))
                elif trans_percent > 0:
                    trans_item.setForeground(QColor("#FF9800"))

            files_layout.addWidget(table)

            # Double-click to navigate
            def on_file_double_click(row, col):
                file_info = files[row]
                file_id = file_info['id']
                file_name = file_info['name']

                reply = QMessageBox.question(
                    dialog,
                    "Navigate to File",
                    f"Go to the first segment of:\n{file_name}?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
                )
                if reply == QMessageBox.StandardButton.Yes:
                    for seg in segments:
                        if getattr(seg, 'file_id', None) == file_id:
                            self.go_to_segment(seg.id)
                            dialog.accept()
                            return

            table.cellDoubleClicked.connect(on_file_double_click)

            progress_layout.addWidget(files_group)

        tabs.addTab(progress_tab, "File Progress")

        # Set initial tab
        if initial_tab < tabs.count():
            tabs.setCurrentIndex(initial_tab)

        main_layout.addWidget(tabs)

        # ═══════════════ OK Button ═══════════════
        button_layout = QHBoxLayout()
        button_layout.addStretch()
        ok_btn = QPushButton(self.tr("OK"))
        ok_btn.setDefault(True)
        ok_btn.clicked.connect(dialog.accept)
        button_layout.addWidget(ok_btn)
        main_layout.addLayout(button_layout)

        dialog.exec()

    def _update_scratchpad_for_project(self):
        """Update the Scratchpad tab with the current project's scratchpad notes"""
        if not hasattr(self, 'scratchpad_edit') or not self.scratchpad_edit:
            return
        
        self.scratchpad_edit.blockSignals(True)
        if self.current_project and hasattr(self.current_project, 'scratchpad_notes'):
            self.scratchpad_edit.setPlainText(self.current_project.scratchpad_notes or '')
        else:
            self.scratchpad_edit.clear()
        self.scratchpad_edit.blockSignals(False)
