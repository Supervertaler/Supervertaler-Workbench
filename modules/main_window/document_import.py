"""Importing a document (Word, or any format the Okapi converter reads).

Part of the main window (SupervertalerQt), moved here unchanged. The
methods run as the main window's own: `self` is the SupervertalerQt.
"""

import os
import threading
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QVBoxLayout,
)
from dataclasses import dataclass
from modules.grid_editors import TagHighlighter
from modules.help_system import Topics as HelpTopics, set_topic as set_help_topic
from modules.language_combos import _import_language_pairs, _select_lang_in_combo
from modules.okapi_import_options import okapi_sidecar_options, okapi_subdoc_category
from modules.segment_model import Comment, Project, Segment
from typing import Optional


class DocumentImportMixin:
    def import_document(self):
        """Import any supported document format via the Okapi sidecar.

        Replaces the older ``import_docx`` and ``import_okapi_format``
        menu handlers, which were two cosmetically-different entry points
        for the same underlying engine. Every supported format (DOCX,
        IDML, HTML, XLIFF, PO, XLSX, PPTX) goes through the Okapi
        sidecar — the DOCX/non-DOCX split in the old menu was a stale
        leftover from when there used to be two engines.

        File picker shows an "All supported formats" filter by default,
        with per-format filters available for users who want them. The
        language-pair dialog and the actual extraction are unchanged
        from the previous ``import_okapi_format`` flow.
        """
        # Build file-filter list: All-supported bucket first, then per-format.
        all_patterns = " ".join(p for _, p in self.IMPORT_DOCUMENT_FORMATS)
        filter_parts = [f"All supported formats ({all_patterns})"]
        filter_parts.extend(
            f"{name} ({pattern})" for name, pattern in self.IMPORT_DOCUMENT_FORMATS
        )
        filter_parts.append("All Files (*.*)")

        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Import document",
            "",
            ";;".join(filter_parts),
        )
        if not file_path:
            return

        # Language-pair dialog (same UI as the previous separate flows).
        dialog = QDialog(self)
        dialog.setWindowTitle(self.tr("Import document – Options"))
        dialog.setMinimumWidth(500)
        set_help_topic(dialog, HelpTopics.IMPORT_FORMATS)
        layout = QVBoxLayout(dialog)

        info_label = QLabel(
            f"Selected file: <b>{os.path.basename(file_path)}</b><br><br>"
            "The file will be extracted via the Okapi sidecar – the same "
            "round-trip pipeline used for every supported format. You can "
            "later export back to the original format via "
            "<i>Project → Export → Export Translated Document…</i>."
        )
        info_label.setWordWrap(True)
        info_label.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(info_label)
        layout.addSpacing(15)

        # Same language list / combo-init pattern as the legacy flows.
        languages = _import_language_pairs()
        general_settings = self.load_general_settings()
        last_source = general_settings.get(
            'last_import_source_lang',
            self.current_project.source_lang if self.current_project else 'en')
        last_target = general_settings.get(
            'last_import_target_lang',
            self.current_project.target_lang if self.current_project else 'nl')

        lang_group = QGroupBox(self.tr("Language Pair"))
        lang_layout = QHBoxLayout(lang_group)
        source_combo = QComboBox()
        target_combo = QComboBox()
        for name, code in languages:
            source_combo.addItem(name, code)
            target_combo.addItem(name, code)
        _select_lang_in_combo(source_combo, last_source)
        _select_lang_in_combo(target_combo, last_target)

        swap_btn = QPushButton(self.tr("🔄 Swap"))
        swap_btn.setToolTip(self.tr("Swap source and target languages"))

        def _swap_import_langs():
            s_idx, t_idx = source_combo.currentIndex(), target_combo.currentIndex()
            source_combo.setCurrentIndex(t_idx)
            target_combo.setCurrentIndex(s_idx)
        swap_btn.clicked.connect(_swap_import_langs)

        lang_layout.addWidget(QLabel(self.tr("Source:")))
        lang_layout.addWidget(source_combo)
        lang_layout.addWidget(swap_btn)
        lang_layout.addWidget(QLabel(self.tr("Target:")))
        lang_layout.addWidget(target_combo)
        lang_layout.addStretch()
        layout.addWidget(lang_group)

        # Per-import override of the Okapi file-type options (all formats,
        # since this importer accepts DOCX/XLSX/PPTX). Defaults come from
        # Settings → File Types.
        _io_label = QLabel(self.tr("Office document parts to import (Okapi):"))
        layout.addWidget(_io_label)
        _io_current = self.get_effective_import_options()
        _io_widget, _io_checks = self._build_import_options_widget(_io_current)
        layout.addWidget(_io_widget)

        layout.addSpacing(20)

        button_layout = QHBoxLayout()
        ok_btn = QPushButton(self.tr("Import"))
        ok_btn.setStyleSheet(
            "background-color: #4CAF50; color: white; font-weight: bold; "
            "border: none; outline: none;")
        ok_btn.clicked.connect(dialog.accept)
        cancel_btn = QPushButton(self.tr("Cancel"))
        cancel_btn.clicked.connect(dialog.reject)
        button_layout.addStretch()
        button_layout.addWidget(cancel_btn)
        button_layout.addWidget(ok_btn)
        layout.addLayout(button_layout)

        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        self._import_source_lang = source_combo.currentData()
        self._import_target_lang = target_combo.currentData()
        self._import_options = self._read_import_option_widgets(_io_checks)
        general_settings['last_import_source_lang'] = self._import_source_lang
        general_settings['last_import_target_lang'] = self._import_target_lang
        self.save_general_settings(general_settings)

        # import_docx_from_path is generic at the extract level – the
        # "docx" in its name is historical. Once we hand it the path,
        # the Okapi sidecar takes over and the format-specific bits
        # (extraction, segmentation, project build) are handled by
        # the existing pipeline.
        self.import_docx_from_path(file_path)

    def import_docx_from_path(self, file_path):
        """Import a monolingual DOCX document from a given path"""
        try:
            # Check if this is a re-import of the current project's source file
            reimport_mode = False
            preserved_project_id = None
            preserved_tm_settings = None
            preserved_termbase_settings = None

            if self.current_project:
                current_source = getattr(self, 'current_document_path', None) or \
                                 getattr(self.current_project, 'original_docx_path', None)

                # Normalize paths for comparison
                if current_source and os.path.normpath(file_path) == os.path.normpath(current_source):
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
                        return
                    elif clicked == reimport_btn:
                        reimport_mode = True
                        # Preserve project ID and settings
                        preserved_project_id = self.current_project.id
                        preserved_tm_settings = getattr(self.current_project, 'tm_settings', None)
                        preserved_termbase_settings = getattr(self.current_project, 'termbase_settings', None)
                        self.log(f"📋 Re-importing into current project (preserving TM settings)")
                    # else: new_proj_btn clicked - continue with new project

            self.log(f"Importing: {os.path.basename(file_path)}")

            # ── Okapi-only import ─────────────────────────────────────
            # DOCX import always goes through the Okapi sidecar – the
            # old engine-choice dialog is gone. _ensure_okapi_sidecar()
            # handles the three scenarios (already running, installed
            # but not yet started, not installed → offer download).
            if not self._ensure_okapi_sidecar():
                return

            segmented = None

            # ── Progress dialog (indeterminate, modal) ────────────────
            # Covers the whole import: extraction, segmentation, project
            # build, and grid load. Large DOCX files via the Okapi route
            # can take a while – this gives the user visible feedback.
            from PyQt6.QtWidgets import QProgressDialog
            progress = QProgressDialog(
                f"Importing {os.path.basename(file_path)}…",
                None,  # no cancel button – extraction can't be safely interrupted
                0, 0, self
            )
            progress.setWindowTitle(self.tr("Importing document"))
            progress.setWindowModality(Qt.WindowModality.WindowModal)
            progress.setMinimumDuration(0)
            progress.setAutoClose(False)
            progress.setAutoReset(False)
            progress.show()
            QApplication.processEvents()

            try:
                # Read the language pair the user just selected in the
                # Import DOCX Options dialog. The dialog stores them on
                # self as _import_source_lang / _import_target_lang (set
                # at lines 27256-27257). Earlier versions of this code
                # accidentally read self._last_import_source_lang /
                # _last_import_target_lang – attribute names that are
                # never assigned anywhere – which silently fell through
                # to the default 'en' for both source and target. End
                # result: regardless of what the user picked in the
                # dialog, Okapi was always asked to extract as English-
                # to-English, applying English segmentation rules to
                # non-English source texts and showing the wrong
                # direction in the progress dialog. Fixed in v1.9.421.
                src_lang = getattr(self, '_import_source_lang', None) or 'en'
                trg_lang = getattr(self, '_import_target_lang', None) or 'en'

                progress.setLabelText(
                    f"Extracting with Okapi ({src_lang.upper()} → {trg_lang.upper()})…\n"
                    f"This may take a while for large documents."
                )
                QApplication.processEvents()

                self.log(f"🔧 Extracting via Okapi sidecar ({src_lang} → {trg_lang})...")

                # Run extraction on a worker thread and pump the Qt event
                # loop on the main thread so the dialog stays animated and
                # the OS doesn't mark the window "Not Responding".
                import threading, time as _time
                _result_holder: dict = {}
                _exc_holder: dict = {}
                _import_opts = self.get_effective_import_options()

                def _okapi_worker():
                    try:
                        _result_holder['v'] = self.okapi_sidecar.extract(
                            file_path,
                            source_lang=src_lang,
                            target_lang=trg_lang,
                            segment=True,
                            options=okapi_sidecar_options(_import_opts),
                        )
                    except Exception as _e:
                        _exc_holder['e'] = _e

                _t = threading.Thread(target=_okapi_worker, daemon=True)
                _t.start()
                _start = _time.monotonic()
                while _t.is_alive():
                    QApplication.processEvents()
                    _t.join(0.05)
                    _elapsed = int(_time.monotonic() - _start)
                    progress.setLabelText(
                        f"Extracting with Okapi ({src_lang.upper()} → {trg_lang.upper()})…\n"
                        f"Working on the document – {_elapsed}s elapsed."
                    )
                    QApplication.processEvents()

                if 'e' in _exc_holder:
                    raise _exc_holder['e']
                result = _result_holder['v']

                # Convert Okapi segments → (para_id, text) tuples
                # Filter out non-body content (headers, footers, textbox
                # names) and use formatting tags when available.
                tu_id_to_para = {}
                next_para = 0
                segmented = []
                skipped_count = 0

                for seg in result.get('segments', []):
                    tu_id = seg.get('id', '')
                    sub_doc = (seg.get('subDocument') or '')
                    text = seg.get('source', '').strip()
                    tagged_text = (seg.get('sourceWithTags') or '').strip()

                    # What gets imported (comments, hidden text, headers/
                    # footers, doc properties, …) is now controlled by the
                    # import options sent to Okapi, so we no longer filter
                    # by sub-document here. We just label each segment with
                    # its category so the grid Type column can show it.
                    category = okapi_subdoc_category(sub_doc)

                    # Skip empty segments
                    if not text:
                        skipped_count += 1
                        continue

                    # Assign paragraph IDs (same TU = same paragraph)
                    if tu_id not in tu_id_to_para:
                        tu_id_to_para[tu_id] = next_para
                        next_para += 1
                    para_id = tu_id_to_para[tu_id]

                    # Use tagged text (with <b>/<i> etc.) if available,
                    # otherwise use plain text.
                    display_text = tagged_text if tagged_text else text
                    segmented.append((para_id, display_text, tu_id, seg.get('segmentIndex', 0), category))

                self.log(f"✅ Okapi extracted {len(segmented)} segments "
                         f"from {result.get('textUnitCount', '?')} text units "
                         f"(filter: {result.get('filterUsed', '?')}"
                         f"{f', skipped {skipped_count} non-body' if skipped_count else ''})")

            except Exception as e:
                # Hard-fail – the python-docx fallback is gone.
                try:
                    progress.close()
                except Exception:
                    pass
                self.log(f"✗ Okapi extraction failed: {e}")
                QMessageBox.critical(
                    self,
                    "DOCX import failed",
                    f"Okapi extraction failed for:\n"
                    f"{os.path.basename(file_path)}\n\n"
                    f"Details: {e}\n\n"
                    "Check the diagnostic log for the full traceback."
                )
                return

            self.original_docx = file_path

            progress.setLabelText("Building project…")
            QApplication.processEvents()

            # Create new project with the imported segments
            from dataclasses import dataclass
            from typing import List, Dict, Any, Optional
            from datetime import datetime
            
            @dataclass
            class ImportedSegment:
                id: int
                source: str
                target: str = ""
                status: str = "untranslated"
                notes: str = ""
                para_id: Optional[int] = None
                is_table: bool = False
                table_info: Optional[tuple] = None
                style: str = "Normal"
                doc_position: int = 0
                type: str = "#"  # Default segment type
                okapi_tu_id: str = ""
                okapi_seg_idx: int = -1
                category: str = ""

            # Convert segments
            imported_segments = []
            for seg_id, (para_id, text, okapi_tu_id, okapi_seg_idx, category) in enumerate(segmented, 1):
                # Okapi import: no per-paragraph metadata (table info, styles
                # like Heading1) is currently piped through. Style detection
                # could be added later by extending the sidecar /extract
                # response with a per-segment style hint.
                para_info = None

                is_table = False
                table_info = None
                style = "Normal"
                doc_position = para_id if para_id else seg_id
                
                if para_info:
                    style = para_info.style or "Normal"
                    doc_position = getattr(para_info, 'document_position', para_id)
                    if getattr(para_info, 'is_table_cell', False):
                        is_table = True
                        table_info = (
                            getattr(para_info, 'table_index', 0),
                            getattr(para_info, 'row_index', 0),
                            getattr(para_info, 'cell_index', 0)
                        )
                
                # Set type to "¶" so the preview renderer recognises
                # paragraph boundaries (it checks paragraph_id changes
                # only when type is "¶" or "para").
                seg_type = "¶"

                segment = ImportedSegment(
                    id=seg_id,
                    source=text,
                    target="",
                    status="untranslated",
                    notes="",
                    para_id=para_id,
                    is_table=is_table,
                    table_info=table_info,
                    style=style,
                    doc_position=doc_position,
                    type=seg_type,
                    okapi_tu_id=okapi_tu_id,
                    okapi_seg_idx=okapi_seg_idx,
                    category=category
                )
                imported_segments.append(segment)
            
            # Convert ImportedSegment objects to proper Segment objects
            segments = []
            for imported_seg in imported_segments:
                segment = Segment(
                    id=imported_seg.id,
                    source=imported_seg.source,
                    target=imported_seg.target,
                    status=imported_seg.status,
                    notes=imported_seg.notes,
                    type=imported_seg.type,  # Use actual type from import
                    paragraph_id=imported_seg.para_id if imported_seg.para_id else 0,
                    style=imported_seg.style,  # CRITICAL: Preserve style for heading detection!
                    document_position=imported_seg.doc_position,
                    is_table_cell=imported_seg.is_table,
                    table_info=imported_seg.table_info,
                    okapi_tu_id=imported_seg.okapi_tu_id,
                    okapi_segment_index=imported_seg.okapi_seg_idx,
                    category=imported_seg.category
                )
                segments.append(segment)
            
            # Create new project using the proper Project class
            # Use user-selected languages from import dialog
            source_lang = getattr(self, '_import_source_lang', None) or 'en'
            target_lang = getattr(self, '_import_target_lang', None) or 'nl'
            
            project = Project(
                name=f"DOCX Import - {os.path.basename(file_path)}",
                source_lang=source_lang,
                target_lang=target_lang,
                segments=segments
            )

            # Tag project as Okapi-imported – the export path branches on
            # this to call the sidecar's /merge endpoint.
            project.import_engine = "okapi"
            # Persist the import options so export/merge re-runs the filter
            # with the SAME toggles (keeps the TU set aligned for round-trip).
            project.import_options = _import_opts

            # Import Word comments as actual comments, if requested.
            if _import_opts.get('word_comments_mode') == 'comments':
                self._attach_docx_comments(segments, file_path)

            # If re-importing, restore the preserved project ID and settings
            if reimport_mode and preserved_project_id is not None:
                project.id = preserved_project_id
                if preserved_tm_settings:
                    project.tm_settings = preserved_tm_settings
                if preserved_termbase_settings:
                    project.termbase_settings = preserved_termbase_settings
                self.log(f"✓ Preserved project ID: {preserved_project_id}")

            # Reset project-type specific highlighting flags
            TagHighlighter._is_cafetran_project = False
            TagHighlighter._is_markdown_project = False

            # Set as current project and load into grid
            self.current_project = project
            self.current_document_path = file_path  # Store document path

            # CRITICAL: Update _original_segment_order for new import
            # This prevents old segments from being saved instead of new ones
            self._original_segment_order = self.current_project.segments.copy()

            progress.setLabelText(f"Loading {len(segments):,} segments into grid…")
            QApplication.processEvents()

            def _grid_progress(done, total):
                progress.setLabelText(
                    f"Loading segments into grid… ({done:,} / {total:,})"
                )
                QApplication.processEvents()

            self.load_segments_to_grid(progress_callback=_grid_progress)

            # Initialize TM for this project
            self.initialize_tm_database()

            # Clear translation caches to ensure fresh TM lookups
            # (important for re-imports where segments have changed)
            with self.translation_matches_cache_lock:
                self.translation_matches_cache.clear()
            self.termbase_cache.clear()

            # Only deactivate resources for truly NEW projects (not re-imports)
            if not reimport_mode:
                # Ensure new project starts with clean slate (no TMs/glossaries selected)
                self._deactivate_all_resources_for_new_project()

            # Auto-resize rows for better initial display
            self.auto_resize_rows()

            # Update status
            para_count = len(set(s[0] for s in segmented))
            self.log(f"✓ Loaded {len(segments)} segments from {para_count} paragraphs")
            self.log(f"📍 Project language pair: {project.source_lang.upper()} → {project.target_lang.upper()}")
            self.update_window_title()  # Update window title to show project is loaded

            # Initialize spellcheck for target language
            self._initialize_spellcheck_for_target_language(target_lang)

            # Auto-generate markdown if enabled
            if hasattr(self, 'auto_generate_markdown') and self.auto_generate_markdown:
                if hasattr(self, 'prompt_manager_qt'):
                    self.prompt_manager_qt.generate_markdown_for_current_document()

            # Refresh AI Assistant context
            if hasattr(self, 'prompt_manager_qt'):
                self.prompt_manager_qt.refresh_context()

            # Build the in-memory termbase index and pre-fill the match
            # cache the same way the saved-project load path does. Without
            # this, the first cell-select on a freshly-imported DOCX falls
            # through to a brute-force termbase scan – fine on a small
            # database but reproducibly produces multi-second freezes
            # (one user reported 20s on a 4 100-term database, all 40
            # termbases technically present even though deactivated for
            # the new project). The save/load path was already wired up
            # via _start_termbase_batch_worker() (line 25720); the fresh-
            # import path was missing the same call. v1.9.423.
            try:
                self._start_termbase_batch_worker()
            except Exception as _e:
                # Don't let a background-worker failure block the import
                # itself – the grid is already loaded and visible by now.
                self.log(f"⚠ Failed to start termbase batch worker after import: {_e}")

            # Prefetch TM/MT/LLM matches for the first 50 segments so
            # initial cell-clicks land on cached results instead of a
            # cold lookup. Mirrors the saved-project load path.
            try:
                if len(self.current_project.segments) > 0:
                    prefetch_ids = [seg.id for seg in self.current_project.segments[:50]]
                    self._start_prefetch_worker(prefetch_ids)
            except Exception as _e:
                self.log(f"⚠ Failed to start prefetch worker after import: {_e}")

            progress.close()

            QMessageBox.information(
                self,
                "Import Complete",
                f"Successfully imported {len(segments)} segments from:\n{os.path.basename(file_path)}\n\n"
                f"📍 Language pair: {source_lang.upper()} → {target_lang.upper()}"
            )

        except Exception as e:
            try:
                progress.close()
            except Exception:
                pass
            self.log(f"✗ Import failed: {str(e)}")
            QMessageBox.critical(
                self,
                "Import Error",
                f"Failed to import DOCX:\n\n{str(e)}"
            )

    def _attach_docx_comments(self, segments, docx_path) -> int:
        """Import Word review comments from ``docx_path`` as author-tagged,
        import-only Comment objects attached to ``segments``.

        Placement is best-effort: a comment is attached to the segment whose
        source best matches the start of the comment's highlighted text; if
        none matches cleanly, it goes to the first segment (top of the
        document). Imported comments are flagged ``imported=True`` so they are
        shown but not re-exported (the originals stay in the source file).
        Returns the number of comments attached.
        """
        if not segments:
            return 0
        try:
            from modules.docx_comments import parse_docx_comments, match_comments_to_segments
            comments = parse_docx_comments(docx_path)
        except Exception as e:
            self.log(f"      ⚠️ Could not read Word comments: {e}")
            return 0
        if not comments:
            return 0

        # Map each Word comment to a segment index by anchor-context containment,
        # with document position breaking ties between identically-worded
        # segments (see modules.docx_comments.match_comments_to_segments).
        try:
            seg_indices = match_comments_to_segments(comments, [seg.source for seg in segments])
        except Exception as e:
            self.log(f"      ⚠️ Could not place Word comments: {e}")
            seg_indices = [None] * len(comments)

        attached = 0
        unplaced = 0
        touched_segs = []
        for c, idx in zip(comments, seg_indices):
            if idx is None:
                seg = segments[0]
                unplaced += 1
            else:
                seg = segments[idx]
            seg.comments.append(Comment(
                text=c.get('text', ''),
                author=(c.get('author') or 'Reviewer'),
                created=(c.get('date') or ''),
                imported=True,
            ))
            touched_segs.append(seg)
            attached += 1

        # Sync the legacy ``notes`` mirror so the grid's comment indicator,
        # the "Commented" filter and tooltips reflect the imported comments
        # (add_comment() does this; we appended directly to keep the imported
        # flag + author + date, so sync explicitly here).
        for seg in touched_segs:
            try:
                seg.notes = seg._joined_comment_text()
            except Exception:
                pass

        note = f" ({unplaced} placed at top — no clean anchor)" if unplaced else ""
        self.log(f"      💬 Imported {attached} Word comment(s) as comments{note}")
        return attached
