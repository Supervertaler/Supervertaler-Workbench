"""Gettext PO files, import and export.

Part of the main window (SupervertalerQt), moved here unchanged. The
methods run as the main window's own: `self` is the SupervertalerQt.
"""

from PyQt6.QtWidgets import QFileDialog, QMessageBox
from modules.main_dialogs import _ImportProgressDialog
from modules.segment_model import Project, Segment
from pathlib import Path


class PoMixin:
    def import_po_file(self):
        """Import a GNU gettext .po or .pot file (Linux/Django/WordPress l10n)."""
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            self.tr("Select GNU gettext .po / .pot File"),
            "",
            self.tr("gettext catalogue (*.po *.pot);;All Files (*.*)")
        )

        if not file_path:
            return

        reimport_mode, preserved_id, preserved_tm, preserved_tb = self._check_reimport_same_file(
            file_path, ['current_project.po_source_path']
        )
        if reimport_mode is None:
            return

        try:
            from modules.po_handler import POHandler

            handler = POHandler()
            if not handler.load(file_path):
                QMessageBox.critical(
                    self, self.tr("Error"),
                    self.tr("Failed to load gettext .po file.")
                )
                return

            po_segments = handler.extract_bilingual_segments()
            if not po_segments:
                QMessageBox.warning(
                    self, self.tr("No Segments"),
                    self.tr("No translatable strings found in the .po file.")
                )
                return

            pretranslated_count = sum(1 for s in po_segments if s.get('target', '').strip())
            fuzzy_count = sum(1 for s in po_segments if s.get('is_fuzzy'))

            segments = []
            for i, po_seg in enumerate(po_segments):
                status = po_seg.get('status', 'not_started')
                if status not in ['not_started', 'pre_translated', 'draft', 'translated', 'confirmed', 'locked']:
                    status = 'not_started'

                segment = Segment(
                    id=i + 1,
                    source=po_seg.get('source', ''),
                    target=po_seg.get('target', ''),
                    status=status,
                    match_percent=None,
                    notes=po_seg.get('notes', '') or '',
                )
                segments.append(segment)

            self.po_handler = handler
            self.po_source_file = file_path

            source_lang = self._normalize_language_code(handler.source_lang)
            target_lang = self._normalize_language_code(handler.target_lang)

            file_name = Path(file_path).stem
            self.current_project = Project(
                name=file_name,
                segments=segments,
                source_lang=source_lang,
                target_lang=target_lang
            )

            self._apply_reimport_settings(self.current_project, reimport_mode, preserved_id, preserved_tm, preserved_tb)
            self.current_project.po_source_path = file_path

            self.source_language = source_lang
            self.target_language = target_lang

            self.project_file_path = None
            self.project_modified = True
            self._original_segment_order = self.current_project.segments.copy()

            self.update_window_title()
            _n_segs = len(self.current_project.segments) if self.current_project else 0
            with _ImportProgressDialog(
                self, title=self.tr("Importing gettext .po"),
                initial_label=self.tr("Loading {n} segments into grid…").format(n=f"{_n_segs:,}"),
                initial_total=max(_n_segs, 1),
            ) as _prog:
                self.load_segments_to_grid(progress_callback=_prog.grid_callback)
            self.initialize_tm_database()
            self._clear_caches_after_import()

            if not reimport_mode:
                self._deactivate_all_resources_for_new_project()

            self.auto_resize_rows()
            self._initialize_spellcheck_for_target_language(target_lang)

            self.log(f"✓ Imported {len(segments)} segments from .po: {Path(file_path).name}")
            self.log(f"  Source: {source_lang}, Target: {target_lang}")
            if pretranslated_count:
                self.log(f"  Pretranslated: {pretranslated_count} segments with target text")
            if fuzzy_count:
                self.log(f"  Fuzzy: {fuzzy_count} segments")
            if handler.is_template:
                self.log("  (.pot template — all targets empty)")

            msg = self.tr("Successfully imported {n} segment(s) from gettext .po.\n\nLanguages: {src} → {tgt}").format(
                n=len(segments), src=source_lang, tgt=target_lang
            )
            if pretranslated_count:
                msg += "\n\n" + self.tr("Pretranslated: {n} segment(s) with target text loaded.").format(n=pretranslated_count)
            if fuzzy_count:
                msg += "\n" + self.tr("Fuzzy: {n} segment(s) marked draft (clear the fuzzy flag by editing).").format(n=fuzzy_count)

            self._finalise_import_with_indexes()
            QMessageBox.information(self, self.tr("Import Successful"), msg)
        except Exception as e:
            self.log(f"❌ Error importing .po file: {e}")
            QMessageBox.critical(
                self, self.tr("Import Error"),
                self.tr("Error importing gettext .po file:\n\n{err}").format(err=str(e))
            )

    def export_po_file(self):
        """Export the current project back to a GNU gettext .po file."""
        if not self.current_project or not self.current_project.segments:
            QMessageBox.warning(self, self.tr("No Data"), self.tr("No segments to export"))
            return

        po_source = None
        if hasattr(self, 'po_source_file') and self.po_source_file:
            po_source = self.po_source_file
        elif hasattr(self.current_project, 'po_source_path') and self.current_project.po_source_path:
            po_source = self.current_project.po_source_path
            if Path(po_source).exists():
                self.po_source_file = po_source
                self.log(f"✓ Restored .po source from project: {Path(po_source).name}")
                try:
                    from modules.po_handler import POHandler
                    self.po_handler = POHandler()
                    if not self.po_handler.load(po_source):
                        QMessageBox.critical(self, self.tr("Error"), self.tr("Failed to reload .po source file"))
                        return
                except Exception as e:
                    QMessageBox.critical(self, self.tr("Error"), self.tr("Failed to reload .po file: {err}").format(err=str(e)))
                    return
            else:
                po_source = None

        if not po_source:
            reply = QMessageBox.question(
                self, self.tr("Select .po Source File"),
                self.tr(
                    "To export to gettext .po format, please select the original .po (or .pot) file.\n\n"
                    "This is the file you originally imported.\n\n"
                    "Would you like to select it now?"
                ),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )

            if reply == QMessageBox.StandardButton.Yes:
                file_path, _ = QFileDialog.getOpenFileName(
                    self,
                    self.tr("Select Original .po File"),
                    "",
                    self.tr("gettext catalogue (*.po *.pot);;All Files (*.*)")
                )
                if file_path:
                    self.po_source_file = file_path
                    self.log(f"✓ .po source file set: {Path(file_path).name}")
                    try:
                        from modules.po_handler import POHandler
                        self.po_handler = POHandler()
                        if not self.po_handler.load(file_path):
                            QMessageBox.critical(self, self.tr("Error"), self.tr("Failed to load .po source file"))
                            return
                    except Exception as e:
                        QMessageBox.critical(self, self.tr("Error"), self.tr("Failed to load .po file: {err}").format(err=str(e)))
                        return
                else:
                    self.log("Export cancelled - no source file selected")
                    return
            else:
                self.log("Export cancelled")
                return

        # Default to .po even when the source was .pot — translators ship .po files.
        source_stem = Path(self.po_source_file).stem
        default_name = f"{source_stem}_translated.po"
        output_path, _ = QFileDialog.getSaveFileName(
            self,
            self.tr("Save Translated .po File"),
            default_name,
            self.tr("gettext catalogue (*.po);;All Files (*.*)")
        )

        if not output_path:
            return

        try:
            translations = [seg.target for seg in self._segments_in_document_order()]
            updated_count = self.po_handler.update_target_segments(translations)

            if self.po_handler.save(output_path):
                self.log(f"✓ Exported {updated_count} segments to .po: {Path(output_path).name}")
                QMessageBox.information(
                    self, self.tr("Export Successful"),
                    self.tr(
                        "Successfully exported {n} translated segment(s) to gettext .po.\n\n"
                        "File: {name}"
                    ).format(n=updated_count, name=Path(output_path).name)
                )
            else:
                QMessageBox.critical(
                    self, self.tr("Export Error"),
                    self.tr("Failed to save .po file.")
                )
        except Exception as e:
            self.log(f"❌ Error exporting .po: {e}")
            QMessageBox.critical(
                self, self.tr("Export Error"),
                self.tr("Error exporting gettext .po file:\n\n{err}").format(err=str(e))
            )
