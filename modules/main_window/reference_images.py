"""The reference images tab: extracting pictures from DOCX files and showing
them beside the segment, and the PDF rescue window.

Part of the main window (SupervertalerQt), moved here unchanged. The
methods run as the main window's own: `self` is the SupervertalerQt.
"""

import os
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)
from modules import file_dialog_helper as fdh
from modules.styled_widgets import CheckmarkCheckBox


class ReferenceImagesMixin:
    def create_reference_images_tab(self) -> QWidget:
        """Create the Image Context tab - Load images as visual context for AI translation"""
        from modules.image_extractor import ImageExtractor

        tab = QWidget()
        main_layout = QVBoxLayout(tab)
        main_layout.setContentsMargins(10, 10, 10, 10)
        main_layout.setSpacing(5)

        # v1.10.176: the widget now lives inside the Prompt Manager tab
        # as page 1 of a right-hand QStackedWidget (page 0 = Prompt
        # Editor). Header is now a single compact toolbar row containing
        # the title AND a "← Back to Prompt Editor" button that swaps
        # the stack back. The wordy multi-line description has been
        # demoted to a tooltip on the title — saves a chunk of vertical
        # space the user was rightly complaining about.

        # ── Single-line compact header (title + tooltip + back btn) ──
        from PyQt6.QtWidgets import QSizePolicy
        header_row = QHBoxLayout()
        header_row.setContentsMargins(0, 0, 0, 4)
        header_row.setSpacing(8)

        back_btn = QPushButton(self.tr("← Back to Prompt Editor"))
        back_btn.setToolTip(self.tr("Switch the right panel back to the Prompt Editor"))
        back_btn.setStyleSheet(
            "QPushButton { padding: 2px 10px; border-radius: 3px; "
            "background-color: #ECEFF1; color: #333; } "
            "QPushButton:hover { background-color: #CFD8DC; } "
            "QPushButton:focus { outline: none; }"
        )
        # Late-binding lambda so prompt_manager_qt doesn't have to exist
        # at the moment this lambda is wired up. Resolution happens on click.
        back_btn.clicked.connect(
            lambda: hasattr(self, 'prompt_manager_qt') and self.prompt_manager_qt
            and self.prompt_manager_qt.show_prompt_editor_view()
        )
        header_row.addWidget(back_btn)

        header_title = QLabel(self.tr("🎯 Image Context for AI Translation"))
        header_title.setStyleSheet("font-size: 13px; font-weight: bold; color: #2c3e50;")
        header_title.setToolTip(
            "Pass figure images to the AI alongside your translation. When a segment "
            "references 'Figure 1' or 'see fig 2A', the AI sees the image and translates "
            "technical descriptions and part references more accurately."
        )
        header_title.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        header_row.addWidget(header_title)
        header_row.addStretch()

        # v1.10.180: contextual help button — opens the Image Context help
        # page directly at the "How loaded images reach the AI" section,
        # so the user lands on the practical "what does this actually do"
        # explanation instead of having to scroll past the where-to-find-it
        # preamble.
        try:
            from modules.styled_widgets import HelpButton
            image_help_btn = HelpButton(
                "workbench/ai-translation/image-context/#how-loaded-images-reach-the-ai",
                tooltip="Open the Image Context help page",
            )
            header_row.addWidget(image_help_btn)
        except Exception as e:
            self.log(f"[Image Context] Could not add help button: {e}")

        main_layout.addLayout(header_row)

        # ── Row 1: input files for extraction + DOCX list ─────────
        inputs_row = QHBoxLayout()

        add_file_btn = QPushButton(self.tr("📄 Add DOCX"))
        add_file_btn.clicked.connect(self._on_add_docx_file_for_extraction)
        add_file_btn.setMaximumWidth(110)
        add_file_btn.setToolTip(
            "Add a single DOCX file to the extraction queue below.\n"
            "Use this when you want to pull images OUT of a Word document.\n"
            "Click '🖼️ Extract Images' once the queue is ready."
        )
        inputs_row.addWidget(add_file_btn)

        add_folder_btn = QPushButton(self.tr("📁 Add Folder"))
        add_folder_btn.clicked.connect(self._on_add_docx_folder_for_extraction)
        add_folder_btn.setMaximumWidth(110)
        add_folder_btn.setToolTip(
            "Add EVERY DOCX file in a folder to the extraction queue below.\n"
            "Useful for batch-extracting images from many Word documents at once.\n"
            "Click '🖼️ Extract Images' once the queue is ready.\n\n"
            "Note: this is for adding *Word documents* to extract from.\n"
            "If you already have an Images folder ready, use '📁 Load Folder' instead."
        )
        inputs_row.addWidget(add_folder_btn)

        clear_list_btn = QPushButton("🗑️")
        clear_list_btn.clicked.connect(lambda: self.image_extractor_file_list.clear())
        clear_list_btn.setMaximumWidth(40)
        clear_list_btn.setToolTip(self.tr("Clear DOCX input list"))
        inputs_row.addWidget(clear_list_btn)

        self.image_extractor_file_list = QListWidget()
        self.image_extractor_file_list.setMaximumHeight(40)
        self.image_extractor_file_list.setStyleSheet("font-size: 9px;")
        inputs_row.addWidget(self.image_extractor_file_list, 1)

        main_layout.addLayout(inputs_row)

        # ── Row 2: extraction settings + all action buttons ───────
        # All four buttons (Extract, Load Folder, Clear loaded) live on
        # the same row as the extraction settings — one integrated
        # toolbar instead of the v1.10.174 two-section split.
        action_row = QHBoxLayout()

        self.image_extractor_auto_folder = CheckmarkCheckBox(self.tr("Auto-folder"))
        self.image_extractor_auto_folder.setChecked(False)
        self.image_extractor_auto_folder.setToolTip(self.tr("Create an 'Images' folder next to each DOCX file"))
        self.image_extractor_auto_folder.toggled.connect(self._on_auto_folder_toggled)
        action_row.addWidget(self.image_extractor_auto_folder)

        self.image_extractor_output_dir = QLineEdit()
        self.image_extractor_output_dir.setPlaceholderText(self.tr("Output directory…"))
        self.image_extractor_output_dir.setMaximumWidth(220)
        action_row.addWidget(self.image_extractor_output_dir)

        browse_btn = QPushButton(self.tr("Browse…"))
        browse_btn.clicked.connect(self._on_browse_output_dir_for_extraction)
        browse_btn.setMaximumWidth(80)
        action_row.addWidget(browse_btn)

        action_row.addWidget(QLabel(self.tr("Prefix:")))
        self.image_extractor_prefix = QLineEdit("Fig.")
        self.image_extractor_prefix.setMaximumWidth(60)
        action_row.addWidget(self.image_extractor_prefix)

        # v1.10.192: opt-in vision-AI fallback for images that the
        # text-pattern detector can't label. When ticked, each
        # unlabelled image (after the v1.10.190 pattern + Caption-style
        # scan) is sent to the active vision model with surrounding
        # text and asked to identify its label. Off by default — a
        # confirmation dialog with cost estimate appears before each
        # extraction run.
        self.image_extractor_ai_label = CheckmarkCheckBox(self.tr("🤖 AI label"))
        self.image_extractor_ai_label.setChecked(False)
        self.image_extractor_ai_label.setToolTip(
            "Use AI to label images that the text-pattern detector can't.\n"
            "Useful for documents that don't follow the standard label\n"
            "vocabularies (Figure / FIG. / Table / Diagram / Plate / Photo /\n"
            "Scheme / Exhibit) — marketing copy, blog posts, foreign-language\n"
            "documents, photo essays, cookbooks, etc.\n\n"
            "Requirements:\n"
            "  • Vision-capable model in Settings (Claude Sonnet/Opus 4.x,\n"
            "    GPT-4o or newer, Gemini)\n"
            "  • Internet connection\n\n"
            "Cost: ~$0.005–$0.02 per AI-labelled image depending on provider.\n"
            "Only used for images the free text-pattern detector missed —\n"
            "documents with conventional labels (patents, academic papers)\n"
            "use the cheaper path and never trigger an API call.\n\n"
            "A confirmation dialog shows the estimated cost before any\n"
            "API call is made."
        )
        action_row.addWidget(self.image_extractor_ai_label)

        # Extract button — primary action for the DOCX path.
        extract_btn = QPushButton(self.tr("🖼️ Extract Images"))
        extract_btn.setStyleSheet("""
            QPushButton {
                background-color: #3498db;
                color: white;
                font-weight: bold;
                font-size: 12px;
                padding: 6px 14px;
                border-radius: 3px;
            }
            QPushButton:hover { background-color: #2980b9; }
            QPushButton:focus { outline: none; }
        """)
        extract_btn.clicked.connect(self._on_extract_images)
        extract_btn.setToolTip(
            "Extract images from the DOCX files listed above.\n"
            "Images are saved to the Output directory (or an 'Images' folder\n"
            "next to each DOCX when 'Auto-folder' is ticked) and the resulting\n"
            "folder is automatically loaded as AI context for figure-aware\n"
            "translation."
        )
        action_row.addWidget(extract_btn)

        # Visual separator between the "extract from DOCX" path and
        # the "load a pre-existing folder" path.
        sep_label = QLabel(self.tr("  or  "))
        sep_label.setStyleSheet("color: #999; font-style: italic;")
        action_row.addWidget(sep_label)

        load_context_btn = QPushButton(self.tr("📁 Load Folder"))
        load_context_btn.setStyleSheet("""
            QPushButton {
                background-color: #4CAF50;
                color: white;
                font-weight: bold;
                padding: 6px 14px;
                border-radius: 3px;
                border: none;
                outline: none;
            }
            QPushButton:hover { background-color: #45a049; }
            QPushButton:focus { outline: none; border: none; }
        """)
        load_context_btn.setToolTip(
            "Load images from a folder that already contains image files\n"
            "(e.g. one you extracted in a previous session, or a folder\n"
            "you assembled by hand). Skips the DOCX extraction step.\n\n"
            "Note: '📁 Add Folder' (above) queues a folder of *Word documents*\n"
            "to extract from. This button loads a folder of *images* directly."
        )
        load_context_btn.clicked.connect(self._on_load_image_context_folder)
        action_row.addWidget(load_context_btn)

        action_row.addStretch()

        main_layout.addLayout(action_row)

        # ── Row 3: live status of what's currently loaded for AI ──
        # One thin row instead of v1.10.174's full section card.
        loaded_row = QHBoxLayout()
        loaded_row.setContentsMargins(0, 4, 0, 4)

        loaded_caption = QLabel(self.tr("Loaded for AI:"))
        loaded_caption.setStyleSheet("font-size: 11px; color: #555; font-weight: bold;")
        loaded_row.addWidget(loaded_caption)

        self.image_context_status_label = QLabel(self.tr("No images loaded"))
        self.image_context_status_label.setStyleSheet("color: #999; font-size: 11px;")
        loaded_row.addWidget(self.image_context_status_label, 1)

        clear_context_btn = QPushButton(self.tr("🗑️ Clear loaded"))
        clear_context_btn.clicked.connect(self._on_clear_image_context)
        clear_context_btn.setMaximumWidth(110)
        loaded_row.addWidget(clear_context_btn)

        main_layout.addLayout(loaded_row)

        # ── Results & preview ────────────────────────────────────
        # Main horizontal splitter (left: operations, right: preview)
        results_splitter = QSplitter(Qt.Orientation.Horizontal)
        
        # Left panel: Status and file list
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 0, 0)
        
        status_label = QLabel(self.tr("📋 Results"))
        status_label.setStyleSheet("font-weight: bold; font-size: 9px; color: #666;")
        left_layout.addWidget(status_label)
        
        # Status text area (resizable)
        self.image_extractor_status = QTextEdit()
        self.image_extractor_status.setReadOnly(True)
        self.image_extractor_status.setMinimumHeight(50)
        self.image_extractor_status.setStyleSheet("font-size: 9px;")
        self.image_extractor_status.setPlaceholderText(self.tr("Extraction status..."))
        left_layout.addWidget(self.image_extractor_status)
        
        # Extracted files list
        files_label = QLabel(self.tr("📂 Extracted Files (click to preview)"))
        files_label.setStyleSheet("font-weight: bold; font-size: 9px; color: #666; margin-top: 3px;")
        left_layout.addWidget(files_label)
        
        self.image_extractor_files_list = QListWidget()
        self.image_extractor_files_list.itemClicked.connect(self._on_file_list_item_clicked)
        self.image_extractor_files_list.setStyleSheet("""
            QListWidget {
                border: 1px solid #ccc;
                font-size: 9px;
            }
            QListWidget::item {
                padding: 3px;
                border-bottom: 1px solid #eee;
            }
            QListWidget::item:selected {
                background-color: #e3f2fd;
                color: black;
            }
        """)
        left_layout.addWidget(self.image_extractor_files_list)
        
        results_splitter.addWidget(left_widget)
        
        # Right panel: Full-height image preview
        preview_widget = QWidget()
        preview_layout = QVBoxLayout(preview_widget)
        preview_layout.setContentsMargins(0, 0, 0, 0)
        
        preview_label = QLabel(self.tr("🖼️ Image Preview"))
        preview_label.setStyleSheet("font-weight: bold; font-size: 9px; color: #666;")
        preview_layout.addWidget(preview_label)
        
        # Large image display area with scroll
        preview_scroll = QScrollArea()
        preview_scroll.setWidgetResizable(True)
        preview_scroll.setStyleSheet("QScrollArea { border: 1px solid #ccc; }")
        preview_scroll.setMinimumWidth(300)
        
        self.image_extractor_preview = QLabel()
        self.image_extractor_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image_extractor_preview.setStyleSheet("padding: 10px;")
        self.image_extractor_preview.setText(self.tr("No image selected\n\nClick on a file in the list to preview\nor\nExtract images to see them here"))
        self.image_extractor_preview.setWordWrap(True)
        
        preview_scroll.setWidget(self.image_extractor_preview)
        preview_layout.addWidget(preview_scroll)
        
        # Preview navigation buttons at bottom
        nav_layout = QHBoxLayout()
        
        self.preview_prev_btn = QPushButton(self.tr("◀ Previous"))
        self.preview_prev_btn.clicked.connect(self._on_preview_prev)
        self.preview_prev_btn.setEnabled(False)
        nav_layout.addWidget(self.preview_prev_btn)
        
        self.preview_image_label = QLabel(self.tr("No images"))
        self.preview_image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_image_label.setStyleSheet("font-size: 8px; color: #666;")
        nav_layout.addWidget(self.preview_image_label)
        
        self.preview_next_btn = QPushButton(self.tr("Next ▶"))
        self.preview_next_btn.clicked.connect(self._on_preview_next)
        self.preview_next_btn.setEnabled(False)
        nav_layout.addWidget(self.preview_next_btn)
        
        preview_layout.addLayout(nav_layout)
        
        results_splitter.addWidget(preview_widget)
        
        # Set initial splitter sizes (40% left, 60% right)
        results_splitter.setSizes([400, 600])

        # v1.10.176: explicit stretch=1 so the splitter claims every
        # pixel of vertical space the labels + toolbar don't need.
        # Previously the QVBoxLayout was distributing extra space across
        # the labels too, which left ridiculous gaps between the title,
        # description, and toolbar (per the user's red-arrow screenshot).
        main_layout.addWidget(results_splitter, 1)
        
        # Initialize extractor and preview state
        self.image_extractor = ImageExtractor()
        self.extracted_image_files = []
        self.current_preview_index = 0
        
        return tab

    def open_pdf_rescue_window(self):
        """Open PDF Rescue in a standalone window. Reuses an existing window
        if already open."""
        from PyQt6.QtCore import Qt
        from modules.pdf_rescue_Qt import PDFRescueQt

        existing = getattr(self, '_pdf_rescue_window', None)
        if existing is not None:
            try:
                existing.show()
                existing.raise_()
                existing.activateWindow()
                return
            except RuntimeError:
                self._pdf_rescue_window = None

        window = QWidget()
        window.setWindowFlags(Qt.WindowType.Window)
        window.setWindowTitle(self.tr("🔍 PDF Rescue – Supervertaler"))
        window.resize(1100, 800)

        # PDFRescueQt builds its own header / layout into the supplied widget.
        self.pdf_rescue_qt = PDFRescueQt(self, standalone=False)
        self.pdf_rescue_qt.create_tab(window)

        self._pdf_rescue_window = window
        window.destroyed.connect(lambda *a: setattr(self, '_pdf_rescue_window', None))
        window.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        window.show()

    def _on_add_docx_file_for_extraction(self):
        """Add a single DOCX file to the extraction list"""
        file_path, _ = fdh.get_open_file_name(
            self,
            "Select DOCX File",
            "Word Documents (*.docx)"
        )
        
        if file_path:
            # Avoid duplicates
            items = [self.image_extractor_file_list.item(i).text() 
                    for i in range(self.image_extractor_file_list.count())]
            if file_path not in items:
                self.image_extractor_file_list.addItem(file_path)

    def _on_add_docx_folder_for_extraction(self):
        """Add all DOCX files from a folder to the extraction list"""
        folder_path = fdh.get_existing_directory(
            self,
            "Select Folder Containing DOCX Files"
        )
        
        if folder_path:
            import glob
            docx_files = glob.glob(os.path.join(folder_path, "*.docx"))
            
            # Get existing items to avoid duplicates
            items = [self.image_extractor_file_list.item(i).text() 
                    for i in range(self.image_extractor_file_list.count())]
            
            added = 0
            for docx_file in docx_files:
                if docx_file not in items:
                    self.image_extractor_file_list.addItem(docx_file)
                    added += 1
            
            if added > 0:
                self.image_extractor_status.append(f"✅ Added {added} DOCX file(s) from folder")

    def _on_browse_output_dir_for_extraction(self):
        """Browse for output directory"""
        folder_path = fdh.get_existing_directory(
            self,
            "Select Output Directory for Extracted Images"
        )
        
        if folder_path:
            self.image_extractor_output_dir.setText(folder_path)

    def _on_auto_folder_toggled(self, checked):
        """Handle auto-folder checkbox toggle"""
        # Disable/enable output directory selection based on auto-folder
        self.image_extractor_output_dir.setEnabled(not checked)
        
        if checked:
            self.image_extractor_output_dir.setPlaceholderText(self.tr("Auto: 'Images' folder next to each DOCX"))
        else:
            self.image_extractor_output_dir.setPlaceholderText(self.tr("Choose output directory..."))

    def _on_file_list_item_clicked(self, item):
        """Handle click on extracted file list item"""
        if not item:
            return
        
        # Get the file path stored in the item
        file_path = item.data(Qt.ItemDataRole.UserRole)
        
        if file_path and file_path in self.extracted_image_files:
            # Update current index and preview
            self.current_preview_index = self.extracted_image_files.index(file_path)
            self._update_preview()

    def _on_extract_images(self):
        """Extract images from all DOCX files in the list"""
        # Validate inputs
        if self.image_extractor_file_list.count() == 0:
            QMessageBox.warning(
                self,
                "No Files",
                "Please add at least one DOCX file to extract images from."
            )
            return
        
        # Check if using auto-folder mode
        use_auto_folder = self.image_extractor_auto_folder.isChecked()
        
        if not use_auto_folder:
            output_dir = self.image_extractor_output_dir.text().strip()
            if not output_dir:
                QMessageBox.warning(
                    self,
                    "No Output Directory",
                    "Please select an output directory for the extracted images."
                )
                return
        
        prefix = self.image_extractor_prefix.text().strip()
        if not prefix:
            prefix = "Fig."

        # Get list of files
        docx_files = [self.image_extractor_file_list.item(i).text()
                     for i in range(self.image_extractor_file_list.count())]

        # v1.10.192: build the optional AI-label-fallback callback. If
        # the user ticked the checkbox we peek at all DOCXes first to
        # see how many images would actually need the AI step, then
        # show a confirmation dialog with an estimated cost before any
        # API call is made.
        ai_label_fn = self._build_image_extract_ai_callback(docx_files)
        if ai_label_fn is False:
            # User cancelled at the confirmation dialog — abort the
            # entire extraction (they were expecting AI labels and
            # we'd surprise them with sequential numbering otherwise).
            return

        # Clear status, file list, and preview
        self.image_extractor_status.clear()
        self.image_extractor_status.append("🔄 Starting image extraction...\n")
        self.image_extractor_files_list.clear()
        self.extracted_image_files = []
        self.current_preview_index = 0
        self.image_extractor_preview.setText(self.tr("No image selected\n\nClick on a file in the list to preview\nor\nExtract images to see them here"))
        QApplication.processEvents()
        
        try:
            total_count = 0
            all_extracted_files = []
            
            if use_auto_folder:
                # Extract to "Images" folder next to each DOCX
                self.image_extractor_status.append("📁 Mode: Auto-folder (Images subfolder per DOCX)\n")
                
                for docx_file in docx_files:
                    docx_dir = os.path.dirname(docx_file)
                    docx_name = os.path.splitext(os.path.basename(docx_file))[0]
                    auto_output_dir = os.path.join(docx_dir, "Images")
                    
                    self.image_extractor_status.append(f"📄 Processing: {os.path.basename(docx_file)}")
                    
                    count, files = self.image_extractor.extract_images_from_docx(
                        docx_file,
                        auto_output_dir,
                        prefix,
                        ai_label_fn=ai_label_fn,
                    )
                    
                    total_count += count
                    all_extracted_files.extend(files)
                    
                    if count > 0:
                        self.image_extractor_status.append(f"   ✅ {count} image(s) → {auto_output_dir}")
                    else:
                        self.image_extractor_status.append(f"   ⚠️  No images found")
                    
                    QApplication.processEvents()
                
                output_msg = f"Images saved in 'Images' subfolders next to each DOCX file"
            else:
                # Extract all to single directory
                output_dir = self.image_extractor_output_dir.text().strip()
                self.image_extractor_status.append(f"📁 Output directory: {output_dir}\n")
                
                total_count, all_extracted_files = self.image_extractor.extract_from_multiple_docx(
                    docx_files,
                    output_dir,
                    prefix,
                    ai_label_fn=ai_label_fn,
                )
                
                output_msg = output_dir
            
            # Show results
            self.image_extractor_status.append(f"\n✅ Successfully extracted {total_count} images!")
            
            if all_extracted_files:
                # Store files for preview
                self.extracted_image_files = all_extracted_files
                self.current_preview_index = 0
                
                # Populate file list
                self.image_extractor_files_list.clear()
                for i, file_path in enumerate(all_extracted_files, 1):
                    item = QListWidgetItem(f"{i}. {os.path.basename(file_path)}")
                    item.setData(Qt.ItemDataRole.UserRole, file_path)  # Store full path
                    item.setToolTip(file_path)  # Show full path on hover
                    self.image_extractor_files_list.addItem(item)
                
                # Enable preview buttons
                if len(self.extracted_image_files) > 0:
                    self.preview_prev_btn.setEnabled(len(self.extracted_image_files) > 1)
                    self.preview_next_btn.setEnabled(len(self.extracted_image_files) > 1)

                    # Select and show first image
                    self.image_extractor_files_list.setCurrentRow(0)
                    self._update_preview()

                # v1.10.174: auto-load the freshly-extracted folder as
                # Image Context for AI. The whole point of extracting
                # is usually to use them in the AI translation pipeline,
                # so doing this in one step removes the second click
                # from the common workflow. Users who want to load a
                # DIFFERENT folder can still use the "Load Images Folder"
                # button to override.
                try:
                    auto_load_folder = self._guess_extracted_folder(all_extracted_files)
                    if auto_load_folder:
                        self._load_image_context_from_path(auto_load_folder, source="just-extracted")
                except Exception as e:
                    self.log(f"[Image Context] Auto-load after extraction failed: {e}")

            # Success message
            QMessageBox.information(
                self,
                "Extraction Complete",
                f"Successfully extracted {total_count} images!\n\n{output_msg}"
            )
            
        except Exception as e:
            self.image_extractor_status.append(f"\n❌ Error: {str(e)}")
            QMessageBox.critical(
                self,
                "Extraction Error",
                f"An error occurred during extraction:\n{str(e)}"
            )

    def _update_preview(self):
        """Update the image preview with the current image"""
        if not self.extracted_image_files or self.current_preview_index >= len(self.extracted_image_files):
            self.image_extractor_preview.setText(self.tr("No image selected\n\nClick on a file in the list to preview\nor\nExtract images to see them here"))
            self.preview_image_label.setText(self.tr("No images"))
            return
        
        image_path = self.extracted_image_files[self.current_preview_index]
        
        # Update list selection to match current preview
        if hasattr(self, 'image_extractor_files_list'):
            self.image_extractor_files_list.setCurrentRow(self.current_preview_index)
        
        try:
            from PyQt6.QtGui import QPixmap
            
            pixmap = QPixmap(image_path)
            
            if not pixmap.isNull():
                # Get the scroll area size for better scaling
                scroll_area = self.image_extractor_preview.parent()
                if scroll_area and hasattr(scroll_area, 'viewport'):
                    viewport_size = scroll_area.viewport().size()
                    max_width = viewport_size.width() - 40  # Account for padding
                    max_height = viewport_size.height() - 40
                else:
                    # Fallback to larger default size
                    max_width = 800
                    max_height = 600
                
                # Scale image to fit preview area while maintaining aspect ratio
                scaled_pixmap = pixmap.scaled(
                    max_width, max_height,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation
                )
                
                self.image_extractor_preview.setPixmap(scaled_pixmap)
                
                # Update label with size info
                self.preview_image_label.setText(
                    f"Image {self.current_preview_index + 1} of {len(self.extracted_image_files)}: "
                    f"{os.path.basename(image_path)} ({pixmap.width()}×{pixmap.height()}px)"
                )
            else:
                self.image_extractor_preview.setText(f"Failed to load image:\n{os.path.basename(image_path)}")
                
        except Exception as e:
            self.image_extractor_preview.setText(f"Error loading image:\n{str(e)}")

    def _on_preview_prev(self):
        """Show previous image in preview"""
        if self.extracted_image_files and self.current_preview_index > 0:
            self.current_preview_index -= 1
            self._update_preview()

    def _on_preview_next(self):
        """Show next image in preview"""
        if self.extracted_image_files and self.current_preview_index < len(self.extracted_image_files) - 1:
            self.current_preview_index += 1
            self._update_preview()

    def _on_load_image_context_folder(self):
        """Load a folder of figure images for AI translation context"""
        from PyQt6.QtWidgets import QFileDialog

        folder = QFileDialog.getExistingDirectory(
            self,
            "Select Folder with Figure Images",
            "",
            QFileDialog.Option.ShowDirsOnly
        )

        if folder:
            self._load_image_context_from_path(folder, source="user-picked")

    def _guess_extracted_folder(self, extracted_files):
        """v1.10.174: from a list of paths returned by the extractor,
        infer the single 'Images' folder they live in. Returns the
        folder path if all files share one parent directory; None
        otherwise (e.g. when the user did 'Folder' → multiple DOCXes
        with Auto-folder OFF, files might be spread across dirs).
        Used by the post-extract auto-load workflow."""
        if not extracted_files:
            return None
        parents = {os.path.dirname(p) for p in extracted_files if p}
        if len(parents) == 1:
            only = parents.pop()
            if only and os.path.isdir(only):
                return only
        return None

    def _load_image_context_from_path(self, folder, source="user-picked"):
        """Shared implementation for loading an Images folder. Used by
        the manual "Load Images Folder" button AND the post-extract
        auto-load. The `source` parameter only affects the toast / log
        wording: "user-picked" shows a confirmation dialog; "just-
        extracted" is silent (the extraction already showed its own
        success popup, no need for a second one)."""
        if not folder:
            return
        try:
            count = self.figure_context.load_from_folder(folder)

            if count > 0:
                self.image_context_status_label.setText(
                    f"✅ {count} image{'s' if count != 1 else ''} loaded from: {os.path.basename(folder)}"
                )
                self.image_context_status_label.setStyleSheet("color: #4CAF50; font-weight: bold; font-size: 11px; padding: 5px;")
                self.image_context_status_label.setToolTip(folder)
                origin = "just-extracted folder" if source == "just-extracted" else folder
                self.log(f"[Image Context] Loaded {count} images from {origin}")

                # v1.10.177: also populate the preview list + image preview
                # pane so the user can actually SEE what they just loaded.
                # Previously only _on_extract_images() did this, so loading
                # an existing folder left the preview area blank.
                try:
                    image_exts = {'.png', '.jpg', '.jpeg', '.gif', '.bmp', '.tiff', '.tif', '.webp'}
                    discovered = []
                    for name in sorted(os.listdir(folder)):
                        full = os.path.join(folder, name)
                        if os.path.isfile(full) and os.path.splitext(name)[1].lower() in image_exts:
                            discovered.append(full)

                    if discovered and hasattr(self, 'image_extractor_files_list'):
                        self.extracted_image_files = discovered
                        self.current_preview_index = 0

                        self.image_extractor_files_list.clear()
                        for i, file_path in enumerate(discovered, 1):
                            item = QListWidgetItem(f"{i}. {os.path.basename(file_path)}")
                            item.setData(Qt.ItemDataRole.UserRole, file_path)
                            item.setToolTip(file_path)
                            self.image_extractor_files_list.addItem(item)

                        if hasattr(self, 'preview_prev_btn'):
                            self.preview_prev_btn.setEnabled(len(discovered) > 1)
                        if hasattr(self, 'preview_next_btn'):
                            self.preview_next_btn.setEnabled(len(discovered) > 1)

                        self.image_extractor_files_list.setCurrentRow(0)
                        self._update_preview()

                        if hasattr(self, 'image_extractor_status'):
                            self.image_extractor_status.append(
                                f"📁 Loaded {len(discovered)} image{'s' if len(discovered) != 1 else ''} "
                                f"from: {folder}"
                            )
                except Exception as preview_err:
                    self.log(f"[Image Context] Could not populate preview list: {preview_err}")

                # Update Prompt Manager display
                if hasattr(self, 'prompt_manager_qt') and self.prompt_manager_qt:
                    self.prompt_manager_qt.update_image_context_display()

                # No popup on auto-load — the Extraction Complete dialog
                # the user just clicked through already confirms success.
                if source == "user-picked":
                    QMessageBox.information(
                        self,
                        "Images Loaded",
                        f"Successfully loaded {count} figure image{'s' if count != 1 else ''}.\n\n"
                        f"These images will automatically be included with AI translations when the text "
                        f"references figures (e.g., 'Figure 1', 'see fig 2A')."
                    )
            else:
                self.image_context_status_label.setText(self.tr("⚠️ No valid images found in folder"))
                self.image_context_status_label.setStyleSheet("color: #FF9800; font-size: 11px; padding: 5px;")
                if source == "user-picked":
                    QMessageBox.warning(
                        self,
                        "No Images Found",
                        "The selected folder does not contain any valid image files.\n\n"
                        "Supported formats: .png, .jpg, .jpeg, .gif, .bmp, .tiff\n"
                        "Filename examples: 'Figure 1.png', 'fig2a.jpg', 'Fig. 3-B.png'"
                    )
        except Exception as e:
            self.image_context_status_label.setText(f"❌ Error loading images")
            self.image_context_status_label.setStyleSheet("color: #F44336; font-size: 11px; padding: 5px;")
            self.log(f"[Image Context] Error loading images: {e}")
            if source == "user-picked":
                QMessageBox.critical(
                    self,
                    "Error Loading Images",
                    f"Failed to load images from folder:\n\n{str(e)}"
                )

    def _on_clear_image_context(self):
        """Clear all loaded image context"""
        if self.figure_context.has_images():
            reply = QMessageBox.question(
                self,
                "Clear Image Context",
                f"Clear {self.figure_context.get_image_count()} loaded image{'s' if self.figure_context.get_image_count() != 1 else ''}?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            
            if reply == QMessageBox.StandardButton.Yes:
                self.figure_context.clear()
                self.image_context_status_label.setText(self.tr("No images loaded"))
                self.image_context_status_label.setStyleSheet("color: #999; font-size: 11px; padding: 5px;")
                self.log("[Image Context] Cleared all images")
                
                # Update Prompt Manager display
                if hasattr(self, 'prompt_manager_qt') and self.prompt_manager_qt:
                    self.prompt_manager_qt.update_image_context_display()
        else:
            QMessageBox.information(
                self,
                "No Images Loaded",
                "There are no images currently loaded."
            )

    def show_image_extractor_from_tools(self):
        """Switch the user to the Image Context pane (now under AI in v1.9.395+)."""
        if not hasattr(self, 'main_tabs'):
            return
        # Find the AI main tab by label and select it.
        for i in range(self.main_tabs.count()):
            if "AI" in self.main_tabs.tabText(i):
                self.main_tabs.setCurrentIndex(i)
                break
        # Then select the Image Context sub-tab inside the AI tab.
        if hasattr(self, 'ai_subtabs'):
            for i in range(self.ai_subtabs.count()):
                if "Image Context" in self.ai_subtabs.tabText(i):
                    self.ai_subtabs.setCurrentIndex(i)
                    break
