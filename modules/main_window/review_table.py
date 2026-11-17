"""The bilingual review table (Word): export and re-import.

Part of the main window (SupervertalerQt), moved here unchanged. The
methods run as the main window's own: `self` is the SupervertalerQt.
"""

import os
import re
import traceback
from PyQt6.QtWidgets import QFileDialog, QMessageBox
from datetime import datetime
from modules.main_dialogs import _ImportProgressDialog
from pathlib import Path


class ReviewTableMixin:
    def export_review_table_with_tags(self):
        """Export Supervertaler Bilingual Table with formatting tags visible.
        
        This format is intended for proofreaders who will edit and return the file
        for re-import into Supervertaler.
        """
        self._export_review_table(apply_formatting=False)

    def _add_hyperlink_to_paragraph(self, paragraph, url, text):
        """Add a hyperlink to a paragraph in a Word document.
        
        Args:
            paragraph: The paragraph to add the hyperlink to
            url: The URL for the hyperlink
            text: The display text for the hyperlink
            
        Returns:
            The run containing the hyperlink text (for further formatting)
        """
        from docx.oxml.ns import qn
        from docx.oxml import OxmlElement
        
        # Get the document part
        part = paragraph.part
        
        # Create the relationship
        r_id = part.relate_to(url, 'http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink', is_external=True)
        
        # Create the hyperlink element
        hyperlink = OxmlElement('w:hyperlink')
        hyperlink.set(qn('r:id'), r_id)
        
        # Create a new run for the hyperlink text
        new_run = OxmlElement('w:r')
        rPr = OxmlElement('w:rPr')
        
        # Add the text
        text_elem = OxmlElement('w:t')
        text_elem.text = text
        new_run.append(rPr)
        new_run.append(text_elem)
        hyperlink.append(new_run)
        
        # Add hyperlink to paragraph
        paragraph._p.append(hyperlink)
        
        # Return a reference to the run for further formatting
        # We need to return a proper Run object
        from docx.text.run import Run
        return Run(new_run, paragraph)

    def _export_review_table(self, apply_formatting=False):
        """Internal method to export Supervertaler Bilingual Table.
        
        Args:
            apply_formatting: If True, apply bold/italic/underline formatting.
                            If False, show raw Supervertaler tags.
        """
        import re
        
        # Check if we have segments
        if not self.current_project or not self.current_project.segments:
            QMessageBox.warning(self, "No Data", "No segments to export")
            return
        
        segments = self._segments_in_document_order()

        if not segments:
            QMessageBox.warning(self, "No Data", "No segments to export")
            return

        # v1.10.143: flush any in-grid edits back to the segment objects before
        # exporting, so the exported targets always match what's shown in the
        # grid (defensive – the live textChanged sync should already keep these
        # in step, but a Bilingual Table is a deliverable, so we make sure).
        try:
            self._sync_grid_targets_to_segments(segments)
        except Exception as _sync_exc:
            self.log(f"⚠️ Bilingual export: grid→segment sync skipped: {_sync_exc}")
        
        # Determine default filename
        project_name = getattr(self.current_project, 'name', 'project')
        if hasattr(self, 'current_project_path') and self.current_project_path:
            project_name = Path(self.current_project_path).stem
        
        format_suffix = "_bilingual_formatted" if apply_formatting else "_bilingual"
        default_name = f"{project_name}{format_suffix}.docx"
        
        # Get save path
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Export Bilingual Table" if not apply_formatting else "Export Bilingual Table (Formatted)",
            default_name,
            "Word Documents (*.docx);;All Files (*.*)"
        )
        
        if not file_path:
            return
        
        try:
            from docx import Document
            from docx.shared import Inches, Pt, RGBColor
            from docx.enum.table import WD_TABLE_ALIGNMENT
            from docx.enum.text import WD_ALIGN_PARAGRAPH
            from docx.oxml.ns import qn
            from docx.oxml import OxmlElement
            
            doc = Document()

            # Set up document margins and landscape orientation
            from docx.enum.section import WD_ORIENT
            sections = doc.sections
            for section in sections:
                # Set landscape orientation for better visualization of long segments
                section.orientation = WD_ORIENT.LANDSCAPE
                # Swap page dimensions for landscape
                section.page_width, section.page_height = section.page_height, section.page_width
                # Set margins
                section.left_margin = Inches(0.5)
                section.right_margin = Inches(0.5)
                section.top_margin = Inches(0.5)
                section.bottom_margin = Inches(0.5)
            
            # Language code to display name mapping
            LANG_DISPLAY_NAMES = {
                'en': 'English', 'en-US': 'English (US)', 'en-GB': 'English (UK)', 'en-AU': 'English (AU)',
                'nl': 'Dutch', 'nl-NL': 'Dutch (NL)', 'nl-BE': 'Dutch (Belgium)',
                'de': 'German', 'de-DE': 'German (DE)', 'de-AT': 'German (AT)', 'de-CH': 'German (CH)',
                'fr': 'French', 'fr-FR': 'French (FR)', 'fr-CA': 'French (CA)', 'fr-BE': 'French (BE)',
                'es': 'Spanish', 'es-ES': 'Spanish (ES)', 'es-MX': 'Spanish (MX)', 'es-AR': 'Spanish (AR)',
                'it': 'Italian', 'it-IT': 'Italian (IT)',
                'pt': 'Portuguese', 'pt-PT': 'Portuguese (PT)', 'pt-BR': 'Portuguese (BR)',
                'ru': 'Russian', 'zh': 'Chinese', 'zh-CN': 'Chinese (Simplified)', 'zh-TW': 'Chinese (Traditional)',
                'ja': 'Japanese', 'ko': 'Korean', 'ar': 'Arabic', 'pl': 'Polish', 'sv': 'Swedish',
                'da': 'Danish', 'no': 'Norwegian', 'fi': 'Finnish', 'cs': 'Czech', 'hu': 'Hungarian',
                'tr': 'Turkish', 'el': 'Greek', 'he': 'Hebrew', 'th': 'Thai', 'vi': 'Vietnamese',
            }
            
            def get_language_display_name(lang_value):
                """Convert language code or name to display name."""
                if not lang_value:
                    return "Unknown"
                # If it's already a nice display name (contains space or parentheses), use it
                if ' ' in lang_value or '(' in lang_value:
                    return lang_value
                # Look up in our mapping
                if lang_value in LANG_DISPLAY_NAMES:
                    return LANG_DISPLAY_NAMES[lang_value]
                # Try lowercase
                if lang_value.lower() in LANG_DISPLAY_NAMES:
                    return LANG_DISPLAY_NAMES[lang_value.lower()]
                # Capitalize first letter as fallback
                return lang_value.capitalize() if lang_value else "Unknown"
            
            # Get language names for column headers
            source_lang = "Source"
            target_lang = "Target"
            if self.current_project:
                source_lang = get_language_display_name(self.current_project.source_lang) or "Source"
                target_lang = get_language_display_name(self.current_project.target_lang) or "Target"
            
            # === HEADER SECTION ===
            # Add decorative line above title
            header_line = doc.add_paragraph()
            header_line.alignment = WD_ALIGN_PARAGRAPH.CENTER
            line_run = header_line.add_run("━" * 50)
            line_run.font.color.rgb = RGBColor(0, 102, 204)
            line_run.font.size = Pt(10)
            header_line.paragraph_format.space_after = Pt(6)
            
            # Add title with link to Supervertaler website
            title = doc.add_paragraph()
            title.alignment = WD_ALIGN_PARAGRAPH.CENTER
            title.paragraph_format.space_before = Pt(0)
            title.paragraph_format.space_after = Pt(6)
            
            # Add globe emoji and "Supervertaler Bilingual Table" in blue (no link on title)
            globe_run = title.add_run("🌐 ")
            globe_run.font.size = Pt(18)
            
            # v1.10.231: title reflects whether this variant round-trips.
            # With-Tags (apply_formatting=False) is re-importable; Formatted
            # (apply_formatting=True) is a one-way read-only deliverable.
            _doc_title = ("Supervertaler Formatted Table"
                          if apply_formatting else "Supervertaler Re-importable Table")
            title_run = title.add_run(_doc_title)
            title_run.font.size = Pt(18)
            title_run.font.bold = True
            title_run.font.color.rgb = RGBColor(0, 102, 204)
            
            # Add subtitle with clickable website URL
            subtitle = doc.add_paragraph()
            subtitle.alignment = WD_ALIGN_PARAGRAPH.CENTER
            subtitle.paragraph_format.space_before = Pt(0)
            subtitle.paragraph_format.space_after = Pt(6)
            
            # Make Supervertaler.com/workbench a clickable link.
            # v1.10.167: switched from the bare supervertaler.com to the
            # product-specific URL so files exported by the Workbench can
            # be visually distinguished from files exported by Supervertaler
            # for Trados (which uses supervertaler.com/trados). Both
            # products keep the same "Supervertaler Bilingual Table" title
            # so the format remains one recognisable family.
            url_link = self._add_hyperlink_to_paragraph(
                subtitle, "https://supervertaler.com/workbench/",
                "Supervertaler.com/workbench")
            url_link.font.size = Pt(10)
            url_link.font.color.rgb = RGBColor(0, 102, 204)
            url_link.font.underline = True
            
            # Add decorative line below title
            footer_line = doc.add_paragraph()
            footer_line.alignment = WD_ALIGN_PARAGRAPH.CENTER
            line_run2 = footer_line.add_run("━" * 50)
            line_run2.font.color.rgb = RGBColor(0, 102, 204)
            line_run2.font.size = Pt(10)
            footer_line.paragraph_format.space_after = Pt(12)
            
            # Add project info in a nice format
            info = doc.add_paragraph()
            info.add_run(f"Project: ").bold = True
            info.add_run(f"{project_name}\n")
            info.add_run(f"Languages: ").bold = True
            info.add_run(f"{source_lang} → {target_lang}\n")
            info.add_run(f"Segments: ").bold = True
            info.add_run(f"{len(segments)}\n")
            info.add_run(f"Exported: ").bold = True
            info.add_run(f"{datetime.now().strftime('%Y-%m-%d %H:%M')}\n")
            
            # Add important notice
            notice = doc.add_paragraph()
            notice.paragraph_format.space_before = Pt(6)
            if not apply_formatting:
                warning_run = notice.add_run("⚠️ Important: ")
                warning_run.bold = True
                warning_run.font.color.rgb = RGBColor(180, 100, 0)
                notice.add_run("Do not change segment numbers (#) or source text. ").italic = True
                notice.add_run("This file can be re-imported into Supervertaler after proofreading.").italic = True
            else:
                notice.add_run("Note: This version shows applied formatting and is for client delivery or archiving. It cannot be re-imported.").italic = True
            
            # Create table with 5 columns: #, Source Language, Target Language, Status, Comments
            table = doc.add_table(rows=1, cols=5)
            table.style = 'Table Grid'
            table.alignment = WD_TABLE_ALIGNMENT.CENTER
            
            # Set column widths using fixed layout and explicit grid
            # (mirrors memoQ's proportions: 4.3% / 38.8% / 38.8% / 7.7% / 10.5%)
            tbl = table._tbl
            tbl_pr = tbl.tblPr if tbl.tblPr is not None else OxmlElement('w:tblPr')

            # Fixed layout – Word must use our exact widths
            tbl_layout = OxmlElement('w:tblLayout')
            tbl_layout.set(qn('w:type'), 'fixed')
            tbl_pr.append(tbl_layout)

            # Calculate usable width from page setup (twips = 1/1440 inch)
            section = doc.sections[0]
            usable_twips = int((section.page_width - section.left_margin - section.right_margin) / 914400 * 1440)

            # Proportional column widths (matching memoQ ratios)
            ratios = [0.043, 0.388, 0.388, 0.077, 0.105]
            col_twips = [int(r * usable_twips) for r in ratios]
            # Ensure total matches exactly
            col_twips[-1] = usable_twips - sum(col_twips[:-1])

            # Set explicit table width
            tbl_w = OxmlElement('w:tblW')
            tbl_w.set(qn('w:w'), str(usable_twips))
            tbl_w.set(qn('w:type'), 'dxa')
            tbl_pr.append(tbl_w)

            # Replace default grid with our explicit column widths
            old_grid = tbl.find(qn('w:tblGrid'))
            if old_grid is not None:
                tbl.remove(old_grid)
            new_grid = OxmlElement('w:tblGrid')
            for tw in col_twips:
                gc = OxmlElement('w:gridCol')
                gc.set(qn('w:w'), str(tw))
                new_grid.append(gc)
            # Insert grid after tblPr
            tbl.insert(tbl.index(tbl_pr) + 1, new_grid)

            # Also set cell widths for each row (belt and suspenders)
            from docx.shared import Emu
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
            
            # Add header row with actual language names.
            # v1.10.183: "Notes" → "Comments" to match the in-app
            # terminology (Segment > Comments panel, etc.). The
            # re-importer accepts both headers for back-compat with
            # files exported by older versions.
            header_cells = table.rows[0].cells
            headers = ['#', source_lang, target_lang, 'Status', 'Comments']
            for i, header in enumerate(headers):
                header_cells[i].text = header
                # Make header bold and shaded
                for para in header_cells[i].paragraphs:
                    for run in para.runs:
                        run.bold = True
                        run.font.size = Pt(10)
                # Add shading to header
                shading = OxmlElement('w:shd')
                shading.set(qn('w:fill'), 'DDDDDD')
                header_cells[i]._tc.get_or_add_tcPr().append(shading)
            
            # Helper function to add formatted text to a cell
            def add_formatted_text_to_cell(cell, text, apply_fmt=False):
                """Add text to cell, optionally applying formatting."""
                from docx.shared import RGBColor
                
                # Clear existing content
                for para in cell.paragraphs:
                    para.clear()
                
                if not text:
                    return
                
                para = cell.paragraphs[0]
                para.paragraph_format.space_before = Pt(2)
                para.paragraph_format.space_after = Pt(2)
                
                if not apply_fmt:
                    # Show raw text with tags in pink text (like in Supervertaler)
                    # Parse to find tags and color them - includes Supervertaler tags AND CAT tool tags
                    # Supervertaler tags: <b>, <i>, <u>, <bi>, <li>, <li-o>, <li-b>
                    # memoQ tags: {1}, [2}, {3], [uicontrol id="..."], {tagname}
                    # Trados tags: <1>, </1>
                    tag_pattern = re.compile(
                        r'('
                        r'</?(?:b|i|u|bi|li|li-[bo])>'  # Supervertaler tags
                        r'|</?[0-9]+>'  # Trados numeric tags: <1>, </1>
                        r'|\[[^}\]]+\}'  # memoQ mixed: [anything}
                        r'|\{[^\[\]]+\]'  # memoQ mixed: {anything]
                        r'|\[[a-zA-Z][^}\]]*\s[^}\]]*\]'  # memoQ content: [tag attr...]
                        r'|\{[a-zA-Z][a-zA-Z0-9_-]*\}'  # memoQ closing: {tagname}
                        r'|\[[0-9]+\]'  # memoQ numeric: [1]
                        r'|\{[0-9]+\}'  # memoQ/Phrase numeric: {1}
                        r')'
                    )
                    parts = tag_pattern.split(text)
                    
                    for part in parts:
                        if not part:
                            continue
                        run = para.add_run(part)
                        run.font.size = Pt(9)
                        
                        # Check if this is a tag - make text memoQ dark red
                        if tag_pattern.match(part):
                            run.font.color.rgb = RGBColor(127, 0, 1)  # memoQ dark red (#7f0001)
                else:
                    # Parse tags and apply formatting
                    # First handle list tags - convert to visible markers
                    text = re.sub(r'<li-b>\s*', '• ', text)
                    text = re.sub(r'<li-o>\s*', '◦ ', text)  # Open circle for nested
                    text = re.sub(r'<li>\s*', '– ', text)
                    text = re.sub(r'</li-[bo]>', '', text)
                    text = re.sub(r'</li>', '', text)
                    
                    # Parse formatting tags
                    tag_pattern = re.compile(r'(</?(?:b|i|u|bi)>)')
                    parts = tag_pattern.split(text)
                    
                    is_bold = False
                    is_italic = False
                    is_underline = False
                    
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
                        else:
                            # Regular text
                            run = para.add_run(part)
                            run.font.size = Pt(9)
                            run.bold = is_bold
                            run.italic = is_italic
                            run.underline = is_underline
            
            # Helper to get status display text
            def get_status_display(status):
                """Convert status to user-friendly display text."""
                status_map = {
                    'draft': 'Draft',
                    'confirmed': 'Confirmed',
                    'not_translated': 'Not Translated',
                    'proofread': 'Proofread',
                    'approved': 'Approved',
                    'rejected': 'Rejected',
                    'needs_review': 'Needs Review',
                    'edited': 'Edited',
                    'mt': 'MT',
                    'tm_match': 'TM Match',
                    'fuzzy_match': 'Fuzzy'
                }
                return status_map.get(status, status.replace('_', ' ').title() if status else '')
            
            # Detect a multi-file project so we can mark where each file
            # begins. Single-file exports are unchanged.
            _rt_file_ids = [getattr(s, 'file_id', None) for s in segments]
            _rt_multifile = len({fid for fid in _rt_file_ids if fid is not None}) > 1
            _rt_prev_fid = object()  # sentinel: the first file also gets a header

            # Add segment rows
            for i, seg in enumerate(segments):
                # File-boundary header row (multi-file projects only). Its first
                # cell is deliberately left empty so the re-importer skips it —
                # the importer ignores any row without a segment number.
                _cur_fid = getattr(seg, 'file_id', None)
                if _rt_multifile and _cur_fid != _rt_prev_fid:
                    _fname = getattr(seg, 'file_name', '') or f"File {_cur_fid}"
                    _hrow = table.add_row()
                    _hcells = _hrow.cells
                    _hcells[0].text = ""
                    _hrun = _hcells[1].paragraphs[0].add_run(f"📄 {_fname}")
                    _hrun.bold = True
                    _hrun.font.size = Pt(10)
                    _rt_prev_fid = _cur_fid

                row = table.add_row()
                cells = row.cells

                # Segment number
                cells[0].text = str(i + 1)
                for para in cells[0].paragraphs:
                    para.alignment = WD_ALIGN_PARAGRAPH.CENTER
                    for run in para.runs:
                        run.font.size = Pt(9)
                
                # Source text
                source_text = seg.source if hasattr(seg, 'source') else ''
                add_formatted_text_to_cell(cells[1], source_text, apply_formatting)
                
                # Target text
                target_text = seg.target if hasattr(seg, 'target') else ''
                add_formatted_text_to_cell(cells[2], target_text, apply_formatting)
                
                # Status
                status = seg.status if hasattr(seg, 'status') else ''
                cells[3].text = get_status_display(status)
                for para in cells[3].paragraphs:
                    para.alignment = WD_ALIGN_PARAGRAPH.CENTER
                    for run in para.runs:
                        run.font.size = Pt(8)
                        # Color-code status
                        if status in ('confirmed', 'proofread', 'approved'):
                            run.font.color.rgb = RGBColor(0, 128, 0)  # Green
                        elif status in ('not_translated', 'rejected'):
                            run.font.color.rgb = RGBColor(200, 0, 0)  # Red
                        elif status in ('draft', 'needs_review'):
                            run.font.color.rgb = RGBColor(200, 100, 0)  # Orange
                
                # Comments column - populate with segment comments if any.
                # v1.10.183: keeps reading seg.notes (the legacy mirror) —
                # which is kept in sync with the structured seg.comments[]
                # list automatically by Segment.__post_init__ and the
                # add_comment / update_comment / replace_all_comments_with_text
                # APIs. Re-importer uses the proper API to write back.
                comments_text = seg.notes if hasattr(seg, 'notes') else ''
                cells[4].text = comments_text
                for para in cells[4].paragraphs:
                    for run in para.runs:
                        run.font.size = Pt(8)
            
            # Add footer with branding
            footer_para = doc.add_paragraph()
            footer_para.alignment = WD_ALIGN_PARAGRAPH.CENTER
            footer_para.paragraph_format.space_before = Pt(12)
            
            # Add decorative line
            footer_line_run = footer_para.add_run("\n" + "━" * 50 + "\n")
            footer_line_run.font.size = Pt(10)
            footer_line_run.font.color.rgb = RGBColor(0, 102, 204)
            
            # Add footer text: "Supervertaler Bilingual Table | Supervertaler.com"
            footer_text = doc.add_paragraph()
            footer_text.alignment = WD_ALIGN_PARAGRAPH.CENTER
            
            brand_run = footer_text.add_run(_doc_title)
            brand_run.font.size = Pt(9)
            brand_run.font.color.rgb = RGBColor(100, 100, 100)
            
            separator_run = footer_text.add_run(" | ")
            separator_run.font.size = Pt(9)
            separator_run.font.color.rgb = RGBColor(150, 150, 150)
            
            # Add clickable website link in footer
            footer_link = self._add_hyperlink_to_paragraph(footer_text, "https://supervertaler.com/", "Supervertaler.com")
            footer_link.font.size = Pt(9)
            footer_link.font.color.rgb = RGBColor(0, 102, 204)
            footer_link.font.underline = True
            
            # Save the document
            doc.save(file_path)
            
            format_type = "formatted" if apply_formatting else "with tags"
            self.log(f"✓ Exported bilingual table ({format_type}) with {len(segments)} segments to: {Path(file_path).name}")
            
            QMessageBox.information(
                self, "Export Complete",
                f"Successfully exported {len(segments)} segments to bilingual table:\n\n{os.path.basename(file_path)}\n\n"
                + ("This formatted version is suitable for client review." if apply_formatting 
                   else "This version with tags can be re-imported after proofreading.")
            )
            
        except ImportError:
            QMessageBox.critical(
                self, "Missing Dependency",
                "The 'python-docx' library is required for DOCX export.\n\n"
                "Install it with: pip install python-docx"
            )
        except Exception as e:
            self.log(f"✗ Export failed: {str(e)}")
            QMessageBox.critical(self, "Export Error", f"Failed to export bilingual table:\n\n{str(e)}")
            import traceback
            traceback.print_exc()

    def import_review_table(self):
        """Import a Supervertaler Bilingual Table to update translations in current project.
        
        This allows proofreaders to make changes in the exported DOCX bilingual table
        and have those changes re-imported back into the project.
        """
        import re
        
        # Check if we have a project open
        if not self.current_project or not self.current_project.segments:
            QMessageBox.warning(
                self, "No Project Open",
                "Please open a project first before importing a bilingual table.\n\n"
                "The bilingual table will update translations in the current project."
            )
            return
        
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Bilingual Table DOCX to Import",
            "",
            "Word Documents (*.docx);;All Files (*.*)"
        )
        
        if not file_path:
            return
        
        try:
            from docx import Document
            
            doc = Document(file_path)
            
            # Find the table
            if not doc.tables:
                QMessageBox.warning(
                    self, "Invalid Format",
                    "No table found in the document.\n\n"
                    "Expected a Supervertaler Bilingual Table with columns:\n"
                    "#, Source, Target, Status, Comments"
                )
                return

            table = doc.tables[0]

            # Check header row
            if len(table.rows) < 2:
                QMessageBox.warning(
                    self, "Invalid Format",
                    "The table appears to be empty or has no data rows."
                )
                return

            header_row = table.rows[0]
            headers = [cell.text.strip().lower() for cell in header_row.cells]

            # Validate headers. v1.10.183 accepts both "Comments" (current)
            # and "Notes" (legacy export) in the 5th column.
            if len(headers) < 3:  # At minimum we need #, source, target
                QMessageBox.warning(
                    self, "Invalid Format",
                    f"Expected columns: #, Source, Target, Status, Comments\n"
                    f"Found: {', '.join(headers)}"
                )
                return

            # Parse table rows
            imported_data = []
            parse_errors = []

            for row_idx, row in enumerate(table.rows[1:], start=1):
                cells = row.cells
                if len(cells) < 3:
                    continue

                try:
                    seg_num_text = cells[0].text.strip()
                    if not seg_num_text:
                        continue
                    seg_num = int(seg_num_text)
                    source_text = cells[1].text.strip()
                    target_text = cells[2].text.strip()

                    # Optional fields
                    status_text = cells[3].text.strip() if len(cells) > 3 else ''
                    # v1.10.183: comments column. Field name stays as
                    # 'comments' internally (the legacy 'notes' label
                    # used in code is just the mirror string on
                    # Segment.notes; the structured data is
                    # Segment.comments).
                    comments_text = cells[4].text.strip() if len(cells) > 4 else ''

                    imported_data.append({
                        'segment_num': seg_num,
                        'source': source_text,
                        'target': target_text,
                        'status': status_text,
                        'comments': comments_text,
                        'row_idx': row_idx
                    })
                except ValueError as e:
                    parse_errors.append(f"Row {row_idx}: Could not parse segment number")

            if not imported_data:
                QMessageBox.warning(
                    self, "No Data Found",
                    "No valid segment data found in the bilingual table."
                )
                return

            # Compare with current project and find changes.
            # v1.10.183: detect target changes AND comments changes
            # independently. Previously a comments-only edit was silently
            # discarded — the importer only registered a row as a "change"
            # if the target text differed, so a proofreader who edited or
            # added comments without changing the translation got their
            # work thrown away on re-import.
            current_segments = list(self.current_project.segments)
            changes = []
            mismatches = []

            for data in imported_data:
                seg_num = data['segment_num']
                seg_idx = seg_num - 1  # Convert to 0-based index

                if seg_idx < 0 or seg_idx >= len(current_segments):
                    mismatches.append(f"Segment {seg_num}: Not found in project (project has {len(current_segments)} segments)")
                    continue

                current_seg = current_segments[seg_idx]
                current_source = current_seg.source if hasattr(current_seg, 'source') else ''
                current_target = current_seg.target if hasattr(current_seg, 'target') else ''
                current_comments = (current_seg.notes if hasattr(current_seg, 'notes') else '') or ''

                # Check if source matches (sanity check)
                if data['source'] != current_source:
                    # Allow for minor whitespace differences
                    if data['source'].strip() != current_source.strip():
                        mismatches.append(f"Segment {seg_num}: Source text mismatch")
                        continue

                target_changed = (data['target'] != current_target)
                # Comments are compared after stripping outer whitespace
                # on both sides — Word sometimes pads cells with trailing
                # newlines that the proofreader didn't intend to add.
                imported_comments = (data['comments'] or '').strip()
                current_comments_norm = (current_comments or '').strip()
                comments_changed = (imported_comments != current_comments_norm)

                if target_changed or comments_changed:
                    changes.append({
                        'segment_num': seg_num,
                        'segment_idx': seg_idx,
                        'old_target': current_target,
                        'new_target': data['target'],
                        'target_changed': target_changed,
                        'old_comments': current_comments_norm,
                        'new_comments': imported_comments,
                        'comments_changed': comments_changed,
                    })

            if not changes and not mismatches:
                # v1.10.153: build the termbase index and prefetch
                # TM/MT/LLM matches for the first 50 segments —
                # without this, cell-clicks fall through to per-word
                # SQL queries (2-34 s per click on a 40+ termbase
                # database).
                self._finalise_import_with_indexes()
                QMessageBox.information(
                    self, "No Changes",
                    f"Compared {len(imported_data)} segments - no changes detected."
                )
                return
            
            # v1.10.183: preview text now itemises target changes and
            # comments changes separately so the user can see at a glance
            # how the round-trip touched each segment.
            target_change_count = sum(1 for c in changes if c['target_changed'])
            comment_change_count = sum(1 for c in changes if c['comments_changed'])
            summary_bits = []
            if target_change_count:
                summary_bits.append(f"{target_change_count} target change(s)")
            if comment_change_count:
                summary_bits.append(f"{comment_change_count} comment change(s)")
            preview_text = (
                f"Found {len(changes)} segment(s) with changes "
                f"({', '.join(summary_bits)}):\n\n"
            )

            for i, change in enumerate(changes[:10]):  # Show first 10
                preview_text += f"Segment {change['segment_num']}:\n"
                if change['target_changed']:
                    old_preview = change['old_target'][:50] + ('...' if len(change['old_target']) > 50 else '')
                    new_preview = change['new_target'][:50] + ('...' if len(change['new_target']) > 50 else '')
                    preview_text += f"  Target old: {old_preview}\n"
                    preview_text += f"  Target new: {new_preview}\n"
                if change['comments_changed']:
                    old_c = change['old_comments'][:50] + ('...' if len(change['old_comments']) > 50 else '')
                    new_c = change['new_comments'][:50] + ('...' if len(change['new_comments']) > 50 else '')
                    if not change['old_comments']:
                        old_c = '(empty)'
                    if not change['new_comments']:
                        new_c = '(empty — comment will be cleared)'
                    preview_text += f"  Comment old: {old_c}\n"
                    preview_text += f"  Comment new: {new_c}\n"
                preview_text += "\n"

            if len(changes) > 10:
                preview_text += f"... and {len(changes) - 10} more changes\n\n"
            
            if mismatches:
                preview_text += f"\n⚠ {len(mismatches)} segment(s) could not be matched:\n"
                for mismatch in mismatches[:5]:
                    preview_text += f"  • {mismatch}\n"
                if len(mismatches) > 5:
                    preview_text += f"  ... and {len(mismatches) - 5} more\n"
            
            preview_text += "\nApply these changes?"
            
            reply = QMessageBox.question(
                self, "Review Changes",
                preview_text,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            
            if reply != QMessageBox.StandardButton.Yes:
                self.log("Bilingual table import cancelled by user")
                return
            
            # Apply changes.
            # v1.10.183: comments now go through the proper Segment API
            # (replace_all_comments_with_text). The pre-v1.10.183 code
            # was wrong in two ways:
            #   1. It wrote directly to seg.notes, leaving seg.comments[]
            #      (the actual source of truth since v1.10.57) untouched.
            #      The notes string was a derived mirror; mutating it
            #      directly produced an inconsistent state and the change
            #      could vanish on the next refresh / save / reload.
            #   2. It wrapped the proofreader's text as "[Review: …]" and
            #      *appended* it to existing notes — so a round-trip
            #      "open exported file → save → re-import" would balloon
            #      the comment with nested "[Review: [Review: …]]" each
            #      cycle. Replacing with the proofreader's verbatim text
            #      is the correct round-trip behaviour.
            applied_count = 0
            target_applied = 0
            comments_applied = 0
            for change in changes:
                seg_idx = change['segment_idx']
                seg = current_segments[seg_idx]

                if change['target_changed']:
                    seg.target = change['new_target']
                    # Reset status to 'not_started' — flags segment for
                    # translator review, matches the manual-edit behaviour.
                    if hasattr(seg, 'status'):
                        seg.status = 'not_started'
                    target_applied += 1

                if change['comments_changed']:
                    new_text = change['new_comments']
                    if hasattr(seg, 'replace_all_comments_with_text'):
                        # Proper API: rewrites seg.comments[] AND keeps
                        # seg.notes in sync via _joined_comment_text().
                        # Empty string clears the comments list.
                        seg.replace_all_comments_with_text(new_text)
                    else:
                        # Fallback for legacy segments without the API.
                        # Shouldn't happen in v1.10.57+ but kept defensive.
                        seg.notes = new_text
                    comments_applied += 1

                applied_count += 1
            
            # Mark project as modified and refresh UI
            self.project_modified = True
            self.update_window_title()
            # v1.10.150: wrap the slow grid-load in the shared progress dialog so
            # the user sees activity instead of "Not Responding" on large files.
            # (Minimal wrap: subsequent fast finalise steps stay outside the dialog.)
            _n_segs = len(self.current_project.segments) if self.current_project else 0
            with _ImportProgressDialog(
                self, title="Importing review-table DOCX",
                initial_label=f"Loading {_n_segs:,} segments into grid…",
                initial_total=max(_n_segs, 1),
            ) as _prog:
                self.load_segments_to_grid(progress_callback=_prog.grid_callback)

            # v1.10.231: rebuild the Comments pane's all-comments list so newly
            # imported/edited comments show immediately (the grid reload doesn't
            # touch it). Idempotent + cheap; guarded for early init.
            if comments_applied:
                try:
                    self._refresh_segment_comments_list()
                except Exception:
                    pass

            self.log(
                f"✓ Applied {applied_count} change(s) from bilingual table: "
                f"{Path(file_path).name} "
                f"({target_applied} target, {comments_applied} comments)"
            )

            QMessageBox.information(
                self, "Import Complete",
                f"Successfully applied changes to {applied_count} segment(s).\n\n"
                f"• {target_applied} translation change(s) — status reset to "
                f"'Not Started' for translator review.\n"
                f"• {comments_applied} comment change(s) — the proofreader's "
                f"text replaces the segment's existing comments verbatim."
            )
            
        except ImportError:
            QMessageBox.critical(
                self, "Missing Dependency",
                "The 'python-docx' library is required for bilingual table import.\n\n"
                "Install it with: pip install python-docx"
            )
        except Exception as e:
            QMessageBox.critical(self, "Import Error", f"Failed to import bilingual table:\n\n{str(e)}")
            self.log(f"✗ Bilingual table import failed: {str(e)}")
            import traceback
            traceback.print_exc()
