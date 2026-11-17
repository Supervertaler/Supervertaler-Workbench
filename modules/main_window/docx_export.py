"""Exporting the translation as a Word document: keeping the original's
formatting, adding notes as Word comments, and checking the word count.

Part of the main window (SupervertalerQt), moved here unchanged. The
methods run as the main window's own: `self` is the SupervertalerQt.
"""

import os
import re
import traceback
from PyQt6.QtWidgets import QFileDialog, QMessageBox
from modules.segment_model import Comment
from modules.tag_text import get_docx_language_code, set_docx_language, strip_all_tags
from pathlib import Path


class DocxExportMixin:
    def export_document(self):
        """Export the translated project back to a document.

        Replaces the older ``export_target_only_docx`` and
        ``export_okapi_merge`` menu handlers, which were two
        cosmetically-different entry points for what is effectively the
        same engine: Okapi merge primary, with a python-docx fallback
        for DOCX projects in case the merge ever fails.

        Routing:
         - **DOCX-origin projects** (and projects with no known origin
           file – e.g. older project formats from before Okapi-only
           import landed) go through ``export_target_only_docx``, which
           already tries Okapi merge first and falls back to python-docx
           on failure.
         - **Non-DOCX Okapi-origin projects** (IDML, HTML, XLIFF, PO,
           XLSX, PPTX) go through ``export_okapi_merge``, which insists
           on the Okapi round-trip and outputs in the original format.

        The user only ever sees one menu item; this dispatcher picks
        the right back-end.
        """
        if not self.current_project or not self.current_project.segments:
            QMessageBox.warning(
                self, "Nothing to Export", "Open a project first."
            )
            return

        # Look up the original source file (if any) to decide the route.
        original_path = (
            getattr(self, 'original_docx', None)
            or getattr(self, 'current_document_path', None)
            or getattr(self.current_project, 'original_docx_path', None)
        )
        ext = Path(original_path).suffix.lower() if original_path else ''
        is_okapi = (
            getattr(self.current_project, 'import_engine', '') == 'okapi'
        )

        # Non-DOCX Okapi-origin projects (IDML, HTML, XLIFF, …) want the
        # original-format round-trip. DOCX (Okapi-origin or otherwise)
        # and unknown-origin projects go through the DOCX exporter, which
        # is itself Okapi-aware when the project has the right metadata.
        if is_okapi and ext and ext != '.docx':
            self.export_okapi_merge()
        else:
            self.export_target_only_docx()

    def _prompt_for_original_source(self, stored_hint=None):
        """The formatting-preserving export needs the project's original source
        document. When it can't be found, warn the user and offer to locate it.

        Returns one of:
          - a valid file path the user picked (also adopted onto the project), or
          - ``None``        → user chose to export WITHOUT the original (flat), or
          - ``'__CANCEL__'`` → user cancelled the export entirely.
        """
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle(self.tr("Original document not found"))
        hint = f"\n\nStored path:\n{stored_hint}" if stored_hint else ""
        box.setText(self.tr(
            "Supervertaler can't find the original source document for this "
            "project, so the export would be rebuilt from the translated segments "
            "alone – losing the document's formatting (line numbers, headings, "
            "page layout).\n\nLocate the original document to keep its formatting.")
            + hint)
        locate_btn = box.addButton(self.tr("Locate original…"), QMessageBox.ButtonRole.AcceptRole)
        plain_btn  = box.addButton(self.tr("Export without formatting"), QMessageBox.ButtonRole.DestructiveRole)
        cancel_btn = box.addButton(QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(locate_btn)
        box.exec()

        clicked = box.clickedButton()
        if clicked == cancel_btn:
            return '__CANCEL__'
        if clicked == plain_btn:
            return None

        # Locate… – browse for the original (any supported source format).
        try:
            all_patterns = " ".join(p for _, p in self.IMPORT_DOCUMENT_FORMATS)
            file_filter = f"Source documents ({all_patterns});;All Files (*.*)"
        except Exception:
            file_filter = "Word Documents (*.docx);;All Files (*.*)"
        path, _ = QFileDialog.getOpenFileName(
            self, self.tr("Locate the original source document"), "", file_filter)
        if not path:
            return '__CANCEL__'  # opened the browser but cancelled → abort export

        # Adopt the located file for this export and persist it on the project
        # so future exports (and the next project load) find it.
        self.original_docx = path
        self.current_document_path = path
        try:
            if self.current_project is not None:
                self.current_project.original_docx_path = path
                self.project_modified = True
        except Exception:
            pass
        self.log(f"✓ Original source located for export: {os.path.basename(path)}")
        return path

    def _segments_with_lost_formatting_tags(self, segments):
        """Translated segments whose target dropped inline tags the source has.

        Compares the multiset of inline formatting tags (``<b>``, ``<i>``,
        ``<cf …>``, ``<g1>``, ``<x1/>`` …) in each segment's source against its
        target. Untranslated segments are skipped — they export their source
        verbatim, tags and all, so nothing is lost. Returns a list of
        ``(segment, [friendly tag names])`` for segments that are missing one
        or more source tags in the translation."""
        from collections import Counter

        def _tally(text):
            c: Counter = Counter()
            for tag in self._INLINE_TAG_RE.findall(text or ''):
                inner = tag[1:-1].strip()              # drop the < >
                closing = inner.startswith('/')
                selfclose = inner.endswith('/')
                inner = inner.strip('/').strip()
                name = inner.split()[0] if inner.split() else inner  # drop attrs
                if name:
                    c[('/' if closing else '') + name + ('/' if selfclose else '')] += 1
            return c

        def _friendly(token):
            base = token.lstrip('/').rstrip('/')
            return self._TAG_FRIENDLY_NAMES.get(base.lower(), base)

        out = []
        for seg in segments:
            tgt = getattr(seg, 'target', '') or ''
            if not tgt.strip():
                continue
            src_tags = _tally(getattr(seg, 'source', '') or '')
            if not src_tags:
                continue
            missing = src_tags - _tally(tgt)          # Counter keeps positives only
            if missing:
                kinds = sorted({_friendly(tok) for tok in missing})
                out.append((seg, kinds))
        return out

    def _warn_lost_formatting_tags(self, segments):
        """Pre-export guard: warn if any translation dropped source formatting.

        Returns True to proceed with the export, False to cancel. Fires only
        when there's an actual mismatch, so a clean project sees no dialog."""
        lost = self._segments_with_lost_formatting_tags(segments)
        if not lost:
            return True

        shown = lost[:15]
        lines = [f"  • Segment {getattr(seg, 'id', '?')}: missing {', '.join(kinds)}"
                 for seg, kinds in shown]
        if len(lost) > len(shown):
            lines.append(f"  • … and {len(lost) - len(shown)} more")

        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle(self.tr("Formatting tags missing from translation"))
        box.setText(self.tr(
            f"{len(lost)} translated segment(s) have inline formatting in the "
            f"source that the translation didn't keep. They will export WITHOUT "
            f"that formatting (for example, bold text becomes plain):"))
        box.setInformativeText("\n".join(lines))
        proceed = box.addButton(self.tr("Export anyway"),
                                QMessageBox.ButtonRole.AcceptRole)
        box.addButton(self.tr("Cancel && fix tags"),
                      QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(proceed)
        box.exec()
        return box.clickedButton() is proceed

    def export_target_only_docx(self):
        """Export target text only as a monolingual DOCX document, preserving original formatting"""
        try:
            if not self.current_project or not self.current_project.segments:
                QMessageBox.warning(self, "No Project", "Please open a project with segments first")
                return
            
            # Check if there are any translations
            segments = self._segments_in_document_order()
            translated_count = sum(1 for seg in segments if seg.target and seg.target.strip())
            
            if translated_count == 0:
                QMessageBox.warning(
                    self, "No Translations", 
                    "No translated segments found.\n\nPlease translate some segments before exporting."
                )
                return
            
            # Warn if not all segments are translated
            total_count = len(segments)
            if translated_count < total_count:
                reply = QMessageBox.question(
                    self, "Incomplete Translation",
                    f"Only {translated_count} of {total_count} segments are translated.\n\n"
                    f"Untranslated segments will use source text.\n\n"
                    f"Continue with export?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
                )
                if reply != QMessageBox.StandardButton.Yes:
                    return

            # Tag-loss guard: catch translations that dropped source formatting
            # (e.g. a bold <b>…</b> the MT/AI stripped) BEFORE writing the file,
            # so the loss isn't discovered only in the delivered document.
            if not self._warn_lost_formatting_tags(segments):
                return

            # Resolve the original source document UP FRONT. Without it the
            # export silently rebuilds a blank document from the segments alone,
            # losing all page formatting (line numbers, headings, layout). If
            # it's missing, stop and let the user locate it (or knowingly export
            # without formatting) instead of quietly handing back a flat file.
            original_path = getattr(self, 'original_docx', None) or getattr(self, 'current_document_path', None)
            if not (original_path and os.path.exists(original_path)):
                original_path = self._prompt_for_original_source(
                    getattr(self.current_project, 'original_docx_path', None))
                if original_path == '__CANCEL__':
                    return

            # Get save path — default into the project's target/ folder (#228)
            default_name = ""
            if self.current_project.name:
                default_name = self._project_export_path(
                    self.current_project.name.replace(" ", "_") + "_translated.docx")

            file_path, _ = QFileDialog.getSaveFileName(
                self,
                "Export Target Only DOCX",
                default_name,
                "Word Documents (*.docx);;All Files (*.*)"
            )
            
            if not file_path:
                return
            
            # Ensure .docx extension
            if not file_path.lower().endswith('.docx'):
                file_path += '.docx'
            
            from docx import Document
            import shutil

            # original_path was resolved (and possibly user-located) above. It is
            # either a valid file, or None when the user chose to export without
            # the original (the fresh-document fallback further down).

            # ── Okapi merge path ──────────────────────────────────────
            # If this project was imported via Okapi, try to use the sidecar
            # merge endpoint for a faithful round-trip preserving all formatting.
            if (getattr(self.current_project, 'import_engine', '') == 'okapi'
                    and original_path and os.path.exists(original_path)):
                okapi_merged = self._try_okapi_merge_export(
                    segments, original_path, file_path)
                if okapi_merged:
                    return
                # Fall through to standard export if merge failed

            if original_path and os.path.exists(original_path):
                # Copy original document and replace text - preserves all formatting
                self.log(f"Using original document as template: {os.path.basename(original_path)}")
                shutil.copy2(original_path, file_path)

                doc = Document(file_path)

                # Okapi-imported segments wrap text in placeholder tags
                # (<hyperlink1>, <tags2/>, <g1>, <bpt3>, etc.). When the
                # Okapi merge endpoint isn't available we have to strip
                # those too, otherwise nothing matches against python-docx
                # paragraph text and the export comes out fully untranslated.
                _is_okapi_project = (
                    getattr(self.current_project, 'import_engine', '') == 'okapi'
                )

                # Helper function to strip all formatting tags (for matching only)
                import re
                # Match Okapi placeholder tags like <hyperlink1>, <tags2/>, <g3>,
                # <bpt2>, <ept2>, <ph1/> – any tag name EXCEPT the formatting
                # tags that apply_formatted_text_to_paragraph knows how to render.
                _OKAPI_TAG_RE = re.compile(
                    r'</?(?!(?:b|i|u|bi|sub|sup|li|li-b|li-o)\b)'
                    r'[A-Za-z][A-Za-z0-9_-]*\s*/?>'
                )

                def strip_all_tags(text):
                    """Remove all formatting and list tags from text"""
                    text = re.sub(r'</?li-[bo]>', '', text)  # <li-b>, </li-b>, <li-o>, </li-o>
                    text = re.sub(r'</?li>', '', text)       # <li>, </li>
                    text = re.sub(r'</?[biu]>', '', text)    # <b>, </b>, <i>, </i>, <u>, </u>
                    text = re.sub(r'</?bi>', '', text)       # <bi>, </bi>
                    text = re.sub(r'</?sub>', '', text)      # <sub>, </sub>
                    text = re.sub(r'</?sup>', '', text)      # <sup>, </sup>
                    if _is_okapi_project:
                        # Generic strip for Okapi placeholder tags
                        text = _OKAPI_TAG_RE.sub('', text)
                    return text.strip()

                def clean_special_chars(text):
                    """Remove problematic Unicode characters like object replacement char"""
                    # Remove Unicode Object Replacement Character (U+FFFC) and similar
                    text = text.replace('\ufffc', '')  # Object Replacement Character
                    text = text.replace('\ufffe', '')  # Noncharacter
                    text = text.replace('\uffff', '')  # Noncharacter
                    text = text.replace('\u0000', '')  # Null character
                    return text
                
                def apply_formatted_text_to_paragraph(para, tagged_text):
                    """
                    Replace paragraph text with tagged text, applying bold/italic/underline formatting.
                    Parses tags like <b>, <i>, <u>, <bi> and creates appropriate runs.
                    Preserves original font name and size from the first run.
                    """
                    # Clean special characters first
                    text = clean_special_chars(tagged_text)

                    # Strip list tags - they don't affect formatting
                    text = re.sub(r'</?li-[bo]>', '', text)
                    text = re.sub(r'</?li>', '', text)

                    # Capture original font properties BEFORE clearing runs
                    original_font_name = None
                    original_font_size = None
                    original_all_caps = None
                    if para.runs:
                        first_run = para.runs[0]
                        if first_run.font:
                            original_font_name = first_run.font.name
                            original_font_size = first_run.font.size
                            original_all_caps = first_run.font.all_caps

                    # Clear existing runs
                    for run in para.runs:
                        run.clear()
                    # Remove the cleared runs
                    for run in list(para.runs):
                        run._element.getparent().remove(run._element)

                    # Parse tags and create runs with formatting
                    # Pattern matches tags or text between tags
                    tag_pattern = re.compile(r'(</?(?:b|i|u|bi|sub|sup)>)')
                    parts = tag_pattern.split(text)

                    is_bold = False
                    is_italic = False
                    is_underline = False
                    is_subscript = False
                    is_superscript = False

                    for part in parts:
                        if not part:
                            continue

                        # Check if this is a tag
                        if part == '<b>':
                            is_bold = True
                        elif part == '</b>':
                            is_bold = False
                        elif part == '<i>':
                            is_italic = True
                        elif part == '</i>':
                            is_italic = False
                        elif part == '<u>':
                            is_underline = True
                        elif part == '</u>':
                            is_underline = False
                        elif part == '<bi>':
                            is_bold = True
                            is_italic = True
                        elif part == '</bi>':
                            is_bold = False
                            is_italic = False
                        elif part == '<sub>':
                            is_subscript = True
                        elif part == '</sub>':
                            is_subscript = False
                        elif part == '<sup>':
                            is_superscript = True
                        elif part == '</sup>':
                            is_superscript = False
                        else:
                            # This is text content - create a run with current formatting
                            if part.strip() or part:  # Include whitespace
                                run = para.add_run(part)
                                run.bold = is_bold
                                run.italic = is_italic
                                run.underline = is_underline
                                if is_subscript:
                                    run.font.subscript = True
                                if is_superscript:
                                    run.font.superscript = True
                                # Restore original font properties
                                if original_font_name:
                                    run.font.name = original_font_name
                                if original_font_size:
                                    run.font.size = original_font_size
                                if original_all_caps:
                                    run.font.all_caps = original_all_caps
                
                # Build a mapping of source text (without tags) to raw target text (with tags)
                text_map = {}
                for seg in segments:
                    # Strip tags from source for matching against original DOCX
                    source_clean = strip_all_tags(seg.source) if seg.source else ""
                    source_clean = clean_special_chars(source_clean)
                    # Keep raw target text WITH formatting tags (b/i/u/sub/sup)
                    # but strip Okapi placeholder tags – apply_formatted_text_to_paragraph
                    # would otherwise insert <hyperlink1> etc. as literal text.
                    target_raw = seg.target.strip() if seg.target and seg.target.strip() else seg.source
                    if _is_okapi_project and target_raw:
                        target_raw = _OKAPI_TAG_RE.sub('', target_raw)
                    if source_clean and target_raw:
                        text_map[source_clean] = target_raw
                
                def replace_segments_in_text_with_tags(original_text, text_map):
                    """Replace all matching segments in text, preserving formatting tags."""
                    result = original_text
                    # Sort by length (longest first) to avoid partial replacement issues
                    for source_clean, target_raw in sorted(text_map.items(), key=lambda x: len(x[0]), reverse=True):
                        if source_clean in result:
                            # Keep target WITH tags for formatting preservation
                            result = result.replace(source_clean, target_raw)
                    return result
                
                replaced_count = 0
                
                # Replace text in paragraphs (outside tables)
                for para in doc.paragraphs:
                    para_text = para.text.strip()
                    
                    # First try exact match (single segment = whole paragraph)
                    if para_text in text_map:
                        target_text = text_map[para_text]
                        apply_formatted_text_to_paragraph(para, target_text)
                        replaced_count += 1
                    else:
                        # Try partial replacement (paragraph contains multiple segments)
                        new_text = replace_segments_in_text_with_tags(para_text, text_map)
                        if new_text != para_text:
                            # Text was changed - use apply_formatted_text_to_paragraph
                            # to preserve inline formatting tags (bold, italic, sub, sup, etc.)
                            apply_formatted_text_to_paragraph(para, new_text)
                            replaced_count += 1

                # Replace text in tables
                for table in doc.tables:
                    for row in table.rows:
                        for cell in row.cells:
                            for para in cell.paragraphs:
                                para_text = para.text.strip()
                                
                                # First try exact match
                                if para_text in text_map:
                                    target_text = text_map[para_text]
                                    apply_formatted_text_to_paragraph(para, target_text)
                                    replaced_count += 1
                                else:
                                    # Try partial replacement
                                    new_text = replace_segments_in_text_with_tags(para_text, text_map)
                                    if new_text != para_text:
                                        # Use apply_formatted_text_to_paragraph to preserve formatting
                                        apply_formatted_text_to_paragraph(para, new_text)
                                        replaced_count += 1

                # Set document language to target language
                if hasattr(self.current_project, 'target_lang') and self.current_project.target_lang:
                    target_lang_code = get_docx_language_code(self.current_project.target_lang)
                    set_docx_language(doc, target_lang_code)
                    self.log(f"✓ Set document language to: {target_lang_code}")

                doc.save(file_path)
                self.log(f"✓ Replaced {replaced_count} text segments in original document structure")

            else:
                # No original document - create simple paragraph-based export
                self.log("No original document found - creating new document (formatting may differ)")
                
                # Helper function to strip all formatting tags (if not already defined)
                import re
                def strip_all_tags(text):
                    """Remove all formatting and list tags from text"""
                    text = re.sub(r'</?li-[bo]>', '', text)  # <li-b>, </li-b>, <li-o>, </li-o>
                    text = re.sub(r'</?li>', '', text)       # <li>, </li>
                    text = re.sub(r'</?[biu]>', '', text)    # <b>, </b>, <i>, </i>, <u>, </u>
                    text = re.sub(r'</?bi>', '', text)       # <bi>, </bi>
                    text = re.sub(r'</?sub>', '', text)      # <sub>, </sub>
                    text = re.sub(r'</?sup>', '', text)      # <sup>, </sup>
                    return text.strip()

                def clean_special_chars(text):
                    """Remove problematic Unicode characters"""
                    text = text.replace('\ufffc', '')  # Object Replacement Character
                    text = text.replace('\ufffe', '')  # Noncharacter
                    text = text.replace('\uffff', '')  # Noncharacter
                    text = text.replace('\u0000', '')  # Null character
                    return text
                
                def add_formatted_text_to_paragraph(para, tagged_text):
                    """Add text with formatting tags to a paragraph."""
                    # Clean special characters first
                    text = clean_special_chars(tagged_text)
                    
                    # Strip list tags
                    text = re.sub(r'</?li-[bo]>', '', text)
                    text = re.sub(r'</?li>', '', text)
                    
                    # Parse and apply formatting
                    tag_pattern = re.compile(r'(</?(?:b|i|u|bi|sub|sup)>)')
                    parts = tag_pattern.split(text)

                    is_bold = False
                    is_italic = False
                    is_underline = False
                    is_subscript = False
                    is_superscript = False

                    for part in parts:
                        if not part:
                            continue
                        if part == '<b>':
                            is_bold = True
                        elif part == '</b>':
                            is_bold = False
                        elif part == '<i>':
                            is_italic = True
                        elif part == '</i>':
                            is_italic = False
                        elif part == '<u>':
                            is_underline = True
                        elif part == '</u>':
                            is_underline = False
                        elif part == '<bi>':
                            is_bold = True
                            is_italic = True
                        elif part == '</bi>':
                            is_bold = False
                            is_italic = False
                        elif part == '<sub>':
                            is_subscript = True
                        elif part == '</sub>':
                            is_subscript = False
                        elif part == '<sup>':
                            is_superscript = True
                        elif part == '</sup>':
                            is_superscript = False
                        else:
                            if part:
                                run = para.add_run(part)
                                run.bold = is_bold
                                run.italic = is_italic
                                run.underline = is_underline
                                if is_subscript:
                                    run.font.subscript = True
                                if is_superscript:
                                    run.font.superscript = True

                doc = Document()
                
                for seg in segments:
                    raw_text = seg.target.strip() if seg.target and seg.target.strip() else seg.source
                    para = doc.add_paragraph()
                    
                    # Try to apply heading style if segment has heading style
                    if hasattr(seg, 'style') and seg.style:
                        style_lower = seg.style.lower()
                        if 'heading 1' in style_lower:
                            para.style = 'Heading 1'
                        elif 'heading 2' in style_lower:
                            para.style = 'Heading 2'
                        elif 'heading 3' in style_lower:
                            para.style = 'Heading 3'
                        elif 'title' in style_lower:
                            para.style = 'Title'
                    
                    # Add text with formatting
                    if raw_text:
                        add_formatted_text_to_paragraph(para, raw_text)

                # Set document language to target language
                if hasattr(self.current_project, 'target_lang') and self.current_project.target_lang:
                    target_lang_code = get_docx_language_code(self.current_project.target_lang)
                    set_docx_language(doc, target_lang_code)
                    self.log(f"✓ Set document language to: {target_lang_code}")

                doc.save(file_path)

            # Attach segment notes as Word comments (post-process). Runs
            # for both the "copy original as template" branch above and
            # the "no original document" simple-paragraph branch — either
            # way the file at file_path is a saved DOCX by this point.
            self._attach_segment_notes_as_docx_comments(file_path, segments)

            self.log(f"✓ Exported {len(segments)} segments to: {os.path.basename(file_path)}")

            QMessageBox.information(
                self, "Export Complete",
                f"Successfully exported {translated_count} translated segments to:\n\n{os.path.basename(file_path)}"
            )

            # Post-export safeguard: warn if the file looks like it lost text.
            wc = self._verify_export_word_count(segments, file_path)
            self._show_export_word_count_warning([wc])

        except ImportError:
            QMessageBox.critical(
                self, "Missing Dependency",
                "The 'python-docx' library is required for DOCX export.\n\n"
                "Install it with: pip install python-docx"
            )
        except Exception as e:
            self.log(f"✗ Export failed: {str(e)}")
            QMessageBox.critical(self, "Export Error", f"Failed to export DOCX:\n\n{str(e)}")
            import traceback
            traceback.print_exc()

    def _split_docx_run_at_offset(self, run, offset):
        """Split a python-docx Run at the given character offset within its text.

        v1.10.60 Phase E: enables Word-comment range anchoring. To anchor
        a comment to only "schroef" within a longer paragraph, we need
        Word's commentRangeStart / commentRangeEnd to bracket exactly
        the runs covering "schroef" — and runs are immutable text
        fragments with formatting, so cutting mid-run requires creating
        a new run.

        Returns (left, right). The original ``run`` becomes ``left``
        (its text truncated to ``text[:offset]``). A new run is
        inserted into the paragraph immediately after the original,
        with text ``text[offset:]`` and the same formatting (rPr
        element deep-copied so bold/italic/font/colour all survive).

        Edge cases:
         - offset <= 0: no split, returns (None, run).
         - offset >= len(text): no split, returns (run, None).
        """
        from copy import deepcopy
        from docx.oxml.ns import qn
        from docx.text.run import Run as _DocxRun

        text = run.text or ''
        if offset <= 0:
            return (None, run)
        if offset >= len(text):
            return (run, None)

        left_text = text[:offset]
        right_text = text[offset:]

        # Deep-copy the run's XML element so the new run inherits all
        # of its formatting (rPr child with bold/italic/font/colour/etc.).
        new_element = deepcopy(run._element)

        # Strip EVERY content child from the copy (w:t, w:br, w:tab, w:cr,
        # w:noBreakHyphen, …), keeping ONLY the run's formatting (w:rPr).
        # Removing just w:t would leave a stray <w:br/>/<w:tab/> behind, which
        # python-docx reads back as a leading '\n'/'\t' — prepending a phantom
        # character to the new run and shifting every subsequent character
        # offset by one (the "breakin|g" comment-anchor bug, where a paragraph
        # held a line break before the anchored word). Both halves are then
        # rebuilt from the sliced strings via the Run.text setter, which
        # re-creates proper <w:br/>/<w:tab/> elements from any '\n'/'\t' and
        # preserves each run's w:rPr.
        rpr_tag = qn('w:rPr')
        for child in list(new_element):
            if child.tag != rpr_tag:
                new_element.remove(child)

        # Insert the new (right) run element immediately after the original,
        # then rebuild both halves' content.
        run._element.addnext(new_element)
        new_run = _DocxRun(new_element, run._parent)
        new_run.text = right_text
        run.text = left_text

        return (run, new_run)

    def _find_or_split_runs_for_range(self, para, start, end):
        """Return the runs that exactly cover ``para.text[start:end]``.

        Splits runs at the start and/or end of the range when those
        boundaries fall mid-run. The returned runs, concatenated,
        produce exactly ``para.text[start:end]``.

        After splitting, ``para.runs`` itself is also restructured —
        the new sub-runs are inserted in the paragraph's XML so that
        subsequent paragraph operations see them.

        Returns an empty list if the range is empty or out of bounds.
        """
        if start >= end:
            return []

        # Walk runs accumulating absolute offsets. We can't trust
        # para.runs across mutations, so re-snapshot after each split.
        result: list = []

        def _walk_once():
            runs = list(para.runs)
            offset = 0
            for run in runs:
                run_text = run.text or ''
                run_len = len(run_text)
                run_start = offset
                run_end = offset + run_len
                offset = run_end

                # No overlap with [start, end)?
                if run_end <= start or run_start >= end:
                    continue

                # The run intersects the range. Two split points to consider:
                # local_start = where the range begins within this run
                # local_end   = where the range ends within this run
                local_start = max(0, start - run_start)
                local_end = min(run_len, end - run_start)

                if local_start > 0:
                    # Range starts mid-run — split off the prefix.
                    _left, _right = self._split_docx_run_at_offset(run, local_start)
                    return True  # mutation happened; re-walk from scratch

                if local_end < run_len:
                    # Range ends mid-run — split off the suffix.
                    _left, _right = self._split_docx_run_at_offset(run, local_end)
                    return True  # mutation happened; re-walk

                # Run fits entirely within the range — collect it.
                result.append(run)
            return False  # walk completed without mutation

        # Iterate until no more splits are needed. Bounded by 2*N splits
        # max (one each for start and end), so loop count is small.
        for _ in range(64):
            result = []
            if not _walk_once():
                break

        return result

    @staticmethod
    def _snap_range_to_word_boundaries(text, start, end):
        """Widen [start, end) so it never cuts a word in half.

        Comment anchors are stored as character offsets in the segment target.
        After the Okapi merge the same text can be split across runs at odd
        places, and an off-by-one (or a run boundary mid-word) can leave a Word
        comment highlighting "breakin" while the trailing "g" sits outside the
        range. Proofreading comments should cover whole words, so extend each
        boundary outward to the nearest non-word character. A boundary already
        sitting at a word edge (space, punctuation, string end) is left as-is."""
        n = len(text)
        if not text or start >= end:
            return start, end
        start = max(0, min(start, n))
        end = max(start, min(end, n))

        def _is_word(ch):
            return ch.isalnum() or ch == '_'

        # Extend the start left while it sits between two word characters.
        while start > 0 and _is_word(text[start - 1]) and start < n and _is_word(text[start]):
            start -= 1
        # Extend the end right while it sits between two word characters.
        while end < n and _is_word(text[end - 1]) and _is_word(text[end]):
            end += 1
        return start, end

    def _comment_author_and_initials(self):
        """Resolve the comment author + initials used in DOCX export.

        Pulled out of the inline body in ``_attach_segment_comments_as_docx_comments``
        so it can be reused (e.g. when range-anchoring decides to put a
        snippet-prefixed comment on the whole paragraph for source-anchored
        comments).
        """
        author = self.get_translator_name()
        parts = author.split()
        if len(parts) >= 2:
            initials = ''.join(p[0] for p in parts if p)[:4].upper()
        elif parts:
            initials = parts[0][:2].upper()
        else:
            initials = 'X'
        return author, initials

    @classmethod
    def _strip_inline_tags(cls, text):
        """Return ``text`` with CAT inline formatting tags removed.

        The exported DOCX renders those tags as real run formatting (bold,
        italic, …), so the *visible* paragraph text carries none of them. To
        locate a segment's text in that paragraph we must compare against the
        same tag-free form."""
        if not text:
            return ''
        return cls._INLINE_TAG_RE.sub('', text)

    @classmethod
    def _raw_to_visible_offset(cls, raw_text, raw_off):
        """Map a character offset in tag-bearing text to the matching offset
        in its tag-stripped (visible) form, so comment anchor offsets stored
        against the raw target still land correctly once tags are removed."""
        if raw_off <= 0 or not raw_text:
            return 0
        visible = 0
        i = 0
        limit = min(raw_off, len(raw_text))
        while i < limit:
            m = cls._INLINE_TAG_RE.match(raw_text, i)
            if m and m.end() <= len(raw_text):
                i = m.end()              # skip the whole tag; no visible advance
            else:
                i += 1
                visible += 1
        return visible

    def _runs_for_segment_span(self, para, seg):
        """Runs covering this segment's target text within a paragraph.

        After the Okapi round-trip, a paragraph (text unit) can hold several
        sentence-segments, so a comment must anchor to its own segment's
        sentence rather than the whole paragraph. The segment target may carry
        inline formatting tags that the rendered paragraph doesn't, so we match
        on the tag-stripped form. Falls back to all runs when the target text
        can't be located in the paragraph."""
        seg_target = self._strip_inline_tags(
            (getattr(seg, 'target', '') or '')).strip()
        para_text = para.text or ''
        if seg_target and seg_target in para_text:
            off = para_text.find(seg_target)
            try:
                ranged = self._find_or_split_runs_for_range(para, off, off + len(seg_target))
                if ranged:
                    return ranged
            except Exception:
                pass
        return list(para.runs)

    def _normalize_docx_run_font_sizes(self, docx_path):
        """Repair run font sizes the Okapi merge dropped.

        The sidecar rebuilds runs (typically around an inline formatting code, or
        a whole merged paragraph) without their explicit size, so they fall back
        to the document's *default* size (often 12pt) and show up as an odd
        font-size change against the surrounding body text (e.g. 10pt source vs
        12pt target). Two repair passes:

         1. **Mixed paragraph** (some runs sized, some not): give the unsized
            runs the paragraph's own dominant explicit size.
         2. **Fully-unsized body paragraph** (no run carries an explicit size –
            e.g. a single merged run): give every run the *document* body size
            (the most common explicit run size across the whole file).

        Headings and titles are left untouched in pass 2 – they legitimately
        inherit a larger size from their style, so we must not force them to the
        body size. They are detected by style name ("heading"/"title")."""
        try:
            from docx import Document
            from collections import Counter
            doc = Document(docx_path)
        except Exception as e:
            self.log(f"  ⚠ Font-size normalise: could not reopen DOCX ({e})")
            return

        fixed = 0

        # ---- Document body size: most common explicit run size everywhere ----
        size_counts: "Counter" = Counter()

        def _tally(paragraphs):
            for p in paragraphs:
                for r in p.runs:
                    if (r.text or '') and r.font.size is not None:
                        size_counts[r.font.size] += 1

        _tally(doc.paragraphs)
        for table in doc.tables:
            for row in table.rows:
                for cell in row.cells:
                    _tally(cell.paragraphs)
        doc_body_size = size_counts.most_common(1)[0][0] if size_counts else None

        def _is_heading(p):
            name = (getattr(getattr(p, 'style', None), 'name', '') or '').lower()
            return ('head' in name) or ('title' in name)

        def _fix(paragraphs):
            nonlocal fixed
            for p in paragraphs:
                runs = [r for r in p.runs if (r.text or '')]
                if not runs:
                    continue
                sizes = [r.font.size for r in runs if r.font.size is not None]
                if sizes:
                    # Pass 1: mixed paragraph — match the paragraph's own size.
                    target = Counter(sizes).most_common(1)[0][0]
                elif doc_body_size is not None and not _is_heading(p):
                    # Pass 2: fully-unsized body paragraph (e.g. a single merged
                    # run) — fall back to the document body size so it stops
                    # inheriting the default. Headings are skipped.
                    target = doc_body_size
                else:
                    continue
                for r in runs:
                    if r.font.size is None:
                        r.font.size = target
                        fixed += 1

        try:
            _fix(doc.paragraphs)
            for table in doc.tables:
                for row in table.rows:
                    for cell in row.cells:
                        _fix(cell.paragraphs)
        except Exception as e:
            self.log(f"  ⚠ Font-size normalise: error while scanning runs ({e})")
            return

        if fixed:
            try:
                doc.save(docx_path)
                self.log(f"  ✓ Font-size normalise: restored size on {fixed} run(s) "
                         f"that lost it in the merge")
            except Exception as e:
                self.log(f"  ⚠ Font-size normalise: could not save ({e})")

    def _attach_segment_notes_as_docx_comments(self, docx_path, segments):
        """Post-process an exported DOCX to attach segment comments as Word comments.

        v1.10.60 Phase E: now iterates ``segment.comments`` (the new
        structured list) instead of the legacy single-string
        ``segment.notes``. Behaviour per Comment:

         - **Unanchored (segment-level)**: same as v1.10.55 behaviour —
           Word comment anchors to the full paragraph (all its runs).
         - **Target-anchored**: finds the paragraph that contains the
           segment's target text, then finds the character range within
           that paragraph corresponding to the comment's
           ``anchor_start/end``. Runs are split if the boundaries cut
           mid-run, and the comment is anchored to exactly the resulting
           sub-runs. End result in Word: a yellow comment bubble whose
           range highlight covers exactly the anchored words.
         - **Source-anchored**: the source text isn't in the exported
           DOCX (the export is target-only), so we can't highlight a
           source range. The comment is attached to the whole paragraph
           with the source-snippet prefixed in the body so the reviewer
           knows what it's about: e.g. ``[Re: "schroef" (source)] use
           "screw" not "bolt"``.

        Silently no-ops on the usual prerequisites: no segments with
        comments, missing python-docx ``add_comment`` API, DOCX path not
        reopenable. Failures attaching individual comments are logged
        but never abort the export.

        Function name and signature kept for backward-compat with the
        existing call-sites in ``_try_okapi_merge_export`` and
        ``export_target_only_docx``.
        """
        # Segment comments ARE exported as Word comments by default: in this
        # workflow they are client-facing annotations that flag problems in the
        # source text, so they must travel with the delivered document. Imported
        # comments (already present in the original) are skipped below to avoid
        # duplicating them.

        # Collect all (segment, comment) pairs that have something to attach.
        candidates: list = []  # [(seg, comment), ...] in segment+document order
        for seg in segments:
            comments_list = getattr(seg, 'comments', None) or []
            # Backward-compat: if a segment somehow only has the legacy
            # notes string (older in-memory object that wasn't migrated),
            # treat it as a single unanchored Comment-shaped tuple.
            if not comments_list:
                legacy_note = (getattr(seg, 'notes', '') or '').strip()
                if legacy_note:
                    # Build a temporary Comment-like object so the loop
                    # below doesn't need to special-case.
                    legacy_comment = Comment(
                        text=legacy_note, author='', anchor_field='',
                    )
                    candidates.append((seg, legacy_comment))
                continue
            target_text = (getattr(seg, 'target', '') or '').strip()
            if not target_text and not any(
                c.anchor_field == 'source' for c in comments_list
            ):
                # No target text and no source-anchored comments means
                # nothing locatable in the exported DOCX. Skip.
                continue
            for c in comments_list:
                # Skip comments imported from the source document: the
                # originals are still in the file (preserved by the Okapi
                # round-trip), so re-attaching them would duplicate them.
                if getattr(c, 'imported', False):
                    continue
                if c.text and c.text.strip():
                    candidates.append((seg, c))

        if not candidates:
            return

        try:
            from docx import Document
            doc = Document(docx_path)
        except Exception as e:
            self.log(f"  ⚠ Comment-attach: could not reopen exported DOCX ({e})")
            return

        if not hasattr(doc, 'add_comment'):
            self.log(
                f"  ⚠ Comment-attach: python-docx version doesn't expose "
                f"Document.add_comment; install python-docx >= 1.0 to enable "
                f"segment-comment export as Word comments. ({len(candidates)} comment(s) skipped.)"
            )
            return

        author, initials = self._comment_author_and_initials()

        # Build a segment_id → matched-paragraph cache. Multiple
        # comments on the same segment all reuse the same paragraph
        # (the one containing the segment's target text). First
        # encounter populates the cache; subsequent encounters reuse.
        segment_to_para: dict = {}
        # Track which target texts have already been anchored in each
        # paragraph. After the Okapi round-trip one paragraph (text unit) can
        # hold several sentence-segments, so two DIFFERENT segments may
        # legitimately share a paragraph — the range-anchoring then highlights
        # each one's own sentence. Only an IDENTICAL target text (a true
        # duplicate) should be pushed on to the next occurrence, so we key the
        # claim by (paragraph, target text) rather than locking the whole
        # paragraph to the first segment that lands in it.
        claimed_targets: dict = {}  # id(para) -> set(seg_target)

        def _find_paragraph_for_segment(seg):
            # Match on the tag-stripped target: the rendered paragraph carries
            # no inline tags, so a target with <b>…</b> would otherwise never
            # be found (and its comment would silently drop).
            seg_target = self._strip_inline_tags(
                (getattr(seg, 'target', '') or '')).strip()
            if not seg_target:
                return None
            if seg.id in segment_to_para:
                return segment_to_para[seg.id]
            for para in doc.paragraphs:
                para_text = para.text or ''
                if seg_target in para_text and para.runs:
                    if seg_target in claimed_targets.get(id(para), ()):
                        continue  # same text already anchored here; try next
                    segment_to_para[seg.id] = para
                    claimed_targets.setdefault(id(para), set()).add(seg_target)
                    return para
            return None

        attached_total = 0
        attached_anchored = 0
        unmatched_no_para = 0
        skipped_no_runs = 0

        for seg, comment in candidates:
            para = _find_paragraph_for_segment(seg)
            if para is None:
                unmatched_no_para += 1
                self.log(f"     · comment on segment #{getattr(seg, 'id', '?')} "
                         f"not attached (its target text wasn't found in the "
                         f"exported document)")
                continue
            if not para.runs:
                skipped_no_runs += 1
                continue

            body = comment.text
            runs_to_anchor = None

            try:
                if comment.anchor_field == 'target' and comment.is_anchored:
                    # Target-side range anchoring: split runs as needed.
                    # The anchor offsets are character offsets within the
                    # SEGMENT'S target text, but we anchor inside the PARAGRAPH
                    # text. The target may carry inline formatting tags that the
                    # rendered paragraph doesn't, so locate the tag-stripped
                    # target and map the (raw) anchor offsets into that
                    # tag-free coordinate space.
                    raw_target = getattr(seg, 'target', '') or ''
                    seg_target = self._strip_inline_tags(raw_target)
                    para_text = para.text or ''
                    para_offset = para_text.find(seg_target)
                    if para_offset < 0:
                        # Defensive: shouldn't happen because we found the
                        # paragraph by containment. Scope to the segment.
                        runs_to_anchor = self._runs_for_segment_span(para, seg)
                    else:
                        vis_len = len(seg_target)
                        vis_start = self._raw_to_visible_offset(raw_target, comment.anchor_start)
                        vis_end = self._raw_to_visible_offset(raw_target, comment.anchor_end)
                        anchor_start_para = para_offset + max(0, min(vis_start, vis_len))
                        anchor_end_para = para_offset + max(0, min(vis_end, vis_len))
                        # Never let the highlight cut a word in half ("breakin|g").
                        anchor_start_para, anchor_end_para = self._snap_range_to_word_boundaries(
                            para_text, anchor_start_para, anchor_end_para
                        )
                        ranged_runs = self._find_or_split_runs_for_range(
                            para, anchor_start_para, anchor_end_para
                        )
                        if ranged_runs:
                            runs_to_anchor = ranged_runs
                            attached_anchored += 1
                        else:
                            runs_to_anchor = self._runs_for_segment_span(para, seg)

                elif comment.anchor_field == 'source' and comment.is_anchored:
                    # Source-anchored: prefix the comment body with the
                    # source snippet so the reviewer knows what it's about,
                    # then anchor to the whole paragraph (since source text
                    # isn't present in the exported DOCX).
                    seg_source = getattr(seg, 'source', '') or ''
                    slen = len(seg_source)
                    sstart = max(0, min(comment.anchor_start, slen))
                    send = max(sstart, min(comment.anchor_end, slen))
                    snippet = seg_source[sstart:send]
                    snippet_short = snippet[:120] + ('…' if len(snippet) > 120 else '')
                    body = f'[Re: "{snippet_short}" (source)] {body}'
                    runs_to_anchor = self._runs_for_segment_span(para, seg)

                else:
                    # Unanchored / segment-level. After the Okapi round-trip a
                    # paragraph (text unit) can hold many sentence-segments, so
                    # anchoring to the whole paragraph would span all of them.
                    # Anchor to just this segment's sentence instead.
                    runs_to_anchor = self._runs_for_segment_span(para, seg)

                if not runs_to_anchor:
                    skipped_no_runs += 1
                    continue

                doc.add_comment(
                    runs=runs_to_anchor,
                    text=body,
                    author=author,
                    initials=initials,
                )
                attached_total += 1
            except Exception as e:
                self.log(
                    f"  ⚠ Comment-attach: failed for segment "
                    f"#{getattr(seg, 'id', '?')} (anchor={comment.anchor_field or 'none'}): {e}"
                )

        # Save back only if we actually added anything.
        if attached_total > 0:
            try:
                doc.save(docx_path)
                msg = (
                    f"  ✓ Attached {attached_total} segment comment(s) "
                    f"as Word comments"
                )
                if attached_anchored:
                    msg += f" ({attached_anchored} range-anchored)"
                if unmatched_no_para:
                    msg += f" ({unmatched_no_para} unmatched — target text not found in any paragraph)"
                if skipped_no_runs:
                    msg += f" ({skipped_no_runs} skipped — empty paragraph)"
                self.log(msg)
            except Exception as e:
                self.log(f"  ⚠ Comment-attach: failed to save DOCX with comments: {e}")
        else:
            if unmatched_no_para:
                self.log(
                    f"  ⚠ Comment-attach: {unmatched_no_para} segment "
                    f"comment(s) had no matching paragraph in the exported DOCX"
                )

    @staticmethod
    def _count_words(text: str) -> int:
        """Count whitespace-delimited word tokens, ignoring inline/markup
        tags (<b>, <cf …>, <x1/>, {1}, …) so the count is comparable
        between a target-cell string and the run text read back from a
        DOCX (where those tags are formatting, not text)."""
        from modules.export_word_count import count_words
        return count_words(text)

    def _count_expected_target_words(self, segments) -> int:
        """Sum of word counts across all segments' export text (target if
        translated, else source — mirroring what the exporter writes)."""
        total = 0
        for seg in segments:
            tgt = getattr(seg, 'target', '') or ''
            text = tgt if tgt.strip() else (getattr(seg, 'source', '') or '')
            total += self._count_words(text)
        return total

    def _count_exported_words(self, path: str):
        """Count the words in an exported file. Returns an int, or None when
        the format has no counter or the file can't be read (the caller then
        skips the check). DOCX, PPTX, XLSX, IDML, HTML, XLIFF and PO are
        covered (issue #219) – see modules/export_word_count.py."""
        from modules.export_word_count import count_exported_words
        try:
            return count_exported_words(path)
        except Exception as e:
            self.log(f"      ⚠️ Export word-count check: could not read "
                     f"{os.path.basename(path)} ({e})")
            return None

    def _verify_export_word_count(self, segments, output_path, context_label: str = ""):
        """Compare expected vs. actual words in a freshly-exported file.

        Returns a result dict (or None if the check was disabled, the format
        has no word counter, or the file couldn't be read). Always logs the
        outcome; never shows UI — callers decide how to surface a shortfall.
        """
        export_cfg = self._load_settings_section("export") or {}
        if not export_cfg.get("word_count_check_enabled", True):
            return None

        actual = self._count_exported_words(output_path)
        if actual is None:
            return None  # no counter for this format, or unreadable

        expected = self._count_expected_target_words(segments)
        threshold = export_cfg.get("word_count_check_threshold",
                                   self.EXPORT_WORD_COUNT_DEFAULT_THRESHOLD)
        try:
            threshold = float(threshold)
        except (TypeError, ValueError):
            threshold = self.EXPORT_WORD_COUNT_DEFAULT_THRESHOLD

        ratio = (actual / expected) if expected > 0 else 1.0
        ok = ratio >= threshold
        label = context_label or os.path.basename(output_path)

        marker = "🔢" if ok else "⚠️"
        self.log(f"      {marker} Export word-count check [{label}]: "
                 f"{actual}/{expected} words ({ratio:.0%}; "
                 f"threshold {threshold:.0%})")
        if not ok:
            self.log(f"      ⚠️ Possible dropped text in export: {label} has only "
                     f"{ratio:.0%} of the expected words — review before delivery.")

        return {
            'expected': expected, 'actual': actual, 'ratio': ratio,
            'ok': ok, 'threshold': threshold, 'label': label, 'path': output_path,
        }

    def _show_export_word_count_warning(self, results):
        """Show a single non-blocking warning dialog for any export results
        that fell below the word-count threshold. `results` is a list of
        dicts from _verify_export_word_count (None entries ignored)."""
        low = [r for r in (results or []) if r and not r['ok']]
        if not low:
            return
        lines = []
        for r in low:
            missing = max(0, r['expected'] - r['actual'])
            lines.append(
                f"• {r['label']}: {r['actual']} of {r['expected']} expected words "
                f"({r['ratio']:.0%}) — ~{missing} word(s) may be missing"
            )
        detail = "\n".join(lines)
        QMessageBox.warning(
            self,
            self.tr("Possible Missing Text in Export"),
            self.tr(
                "The exported document(s) contain noticeably fewer words than the "
                "translated segments, which can mean text was dropped during export:\n\n"
                "{detail}\n\n"
                "Please open the file(s) and check before delivering. If the "
                "difference is expected (e.g. lots of numbers or comments), you can "
                "adjust or disable this check in settings.json (\"export\" → "
                "\"word_count_check_threshold\" / \"word_count_check_enabled\")."
            ).format(detail=detail)
        )
