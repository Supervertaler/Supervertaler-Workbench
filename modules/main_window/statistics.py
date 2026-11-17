"""Project statistics and the quick statistics dialog.

Part of the main window (SupervertalerQt), moved here unchanged. The
methods run as the main window's own: `self` is the SupervertalerQt.
"""

import os
import traceback
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QApplication,
    QFileDialog,
    QMessageBox,
    QProgressDialog,
)
from modules.okapi_import_options import okapi_sidecar_options


class StatisticsMixin:
    def show_statistics_dialog(self):
        """Open the Statistics dialog to analyse the open project against TMs.

        Mirrors the analysis step of Trados Studio / memoQ: scans the current
        project's segments against one or more translation memories and reports
        a match breakdown (repetitions, 100%, fuzzy bands, no-match) so the
        translator can gauge how much work the document represents.
        """
        if not getattr(self, 'current_project', None) or not getattr(self.current_project, 'segments', None):
            QMessageBox.information(
                self, self.tr("Statistics"),
                self.tr("Open a project with segments first, then run Statistics."))
            return

        try:
            from modules.statistics_dialog_qt import StatisticsDialog

            # Build TM choices (real names + entry counts) from the metadata manager,
            # falling back to the database manager's distinct-tm_id list.
            tm_choices = []
            try:
                tm_choices = [
                    {'tm_id': tm['tm_id'], 'name': tm.get('name') or tm['tm_id'],
                     'entry_count': tm.get('entry_count', 0)}
                    for tm in self.tm_metadata_mgr.get_all_tms()
                ]
            except Exception:
                try:
                    tm_choices = [
                        {'tm_id': tm['tm_id'], 'name': tm.get('name') or tm['tm_id'],
                         'entry_count': tm.get('entry_count', 0)}
                        for tm in self.db_manager.get_all_tms()
                    ]
                except Exception:
                    tm_choices = []

            # Pre-select the project's currently activated TMs.
            preselected = []
            try:
                if hasattr(self.current_project, 'tm_settings') and self.current_project.tm_settings:
                    preselected = list(self.current_project.tm_settings.get('activated_tm_ids', []) or [])
            except Exception:
                preselected = []

            dialog = StatisticsDialog(
                parent=self,
                db_manager=self.db_manager,
                segments=self.current_project.segments,
                source_lang=self.current_project.source_lang,
                target_lang=self.current_project.target_lang,
                tm_choices=tm_choices,
                preselected_tm_ids=preselected,
                project_name=getattr(self.current_project, 'name', '') or '',
            )
            dialog.exec()
        except Exception as e:
            self.log(f"Error opening Statistics: {e}")
            import traceback
            traceback.print_exc()
            QMessageBox.critical(self, self.tr("Error"), f"Failed to open Statistics:\n{str(e)}")

    def _quick_count_lang_pair(self):
        """Source/target language codes to use for a project-free count: the
        open project's pair if any, else the last-used import pair."""
        proj = getattr(self, 'current_project', None)
        if proj is not None and getattr(proj, 'source_lang', None):
            return (proj.source_lang or 'en', proj.target_lang or 'nl')
        try:
            gs = self.load_general_settings()
            return (gs.get('last_import_source_lang', 'en') or 'en',
                    gs.get('last_import_target_lang', 'nl') or 'nl')
        except Exception:
            return ('en', 'nl')

    def _quick_extract_sources(self, file_path, src_lang, tgt_lang):
        """Extract a list of source-text strings from one file for a quick count.

        Supports DOCX and the other Okapi formats (sentence-segmented like a
        normal import), plus Trados .sdlxliff and memoQ .mqxliff. Raises
        ValueError for an unsupported extension; the caller handles per-file
        errors so one bad file doesn't sink the rest.
        """
        ext = os.path.splitext(file_path)[1].lower()
        okapi_exts = {'.docx', '.xlsx', '.pptx', '.html', '.htm',
                      '.xliff', '.xlf', '.idml', '.po'}
        if ext in okapi_exts:
            if not self._ensure_okapi_sidecar():
                raise RuntimeError("The Okapi sidecar is not available.")
            result = self.okapi_sidecar.extract(
                file_path, source_lang=src_lang, target_lang=tgt_lang, segment=True,
                options=okapi_sidecar_options(self.get_effective_import_options()),
            )
            return [s.get('source', '').strip()
                    for s in result.get('segments', [])
                    if (s.get('source') or '').strip()]
        if ext == '.sdlxliff':
            from modules.sdlppx_handler import StandaloneSDLXLIFFHandler
            h = StandaloneSDLXLIFFHandler()
            h.load([file_path])
            return [s.source_text for s in h.get_all_segments()
                    if getattr(s, 'source_text', '').strip()]
        if ext == '.mqxliff':
            from modules.mqxliff_handler import MQXLIFFHandler
            h = MQXLIFFHandler()
            h.load(file_path)
            return [s.plain_text for s in h.extract_source_segments()
                    if getattr(s, 'plain_text', '').strip()]
        raise ValueError(f"Unsupported file type: {ext or '(none)'}")

    def show_quick_statistics_dialog(self):
        """Quick Count: pick one or more files (no project needed), extract their
        source segments, then open the Statistics dialog to count them against
        the selected TMs."""
        from types import SimpleNamespace
        from PyQt6.QtWidgets import QProgressDialog

        formats = [
            ("Word Document", "*.docx"),
            ("Trados bilingual", "*.sdlxliff"),
            ("memoQ bilingual", "*.mqxliff"),
            ("Adobe InDesign Markup", "*.idml"),
            ("HTML", "*.html *.htm"),
            ("XLIFF 1.2", "*.xliff *.xlf"),
            ("PO (gettext)", "*.po"),
            ("Microsoft Excel", "*.xlsx"),
            ("Microsoft PowerPoint", "*.pptx"),
        ]
        all_patterns = " ".join(p for _, p in formats)
        filter_parts = [f"All supported formats ({all_patterns})"]
        filter_parts += [f"{n} ({p})" for n, p in formats]
        filter_parts.append("All Files (*.*)")

        paths, _ = QFileDialog.getOpenFileNames(
            self, self.tr("Quick Count – select file(s)"), "", ";;".join(filter_parts))
        if not paths:
            return

        src_lang, tgt_lang = self._quick_count_lang_pair()

        progress = QProgressDialog(
            self.tr("Reading files…"), None, 0, len(paths), self)
        progress.setWindowTitle(self.tr("Quick Count"))
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        progress.show()
        QApplication.processEvents()

        segments = []
        errors = []
        for i, path in enumerate(paths):
            progress.setValue(i)
            progress.setLabelText(self.tr("Reading: ") + os.path.basename(path))
            QApplication.processEvents()
            try:
                sources = self._quick_extract_sources(path, src_lang, tgt_lang)
                _fname = os.path.basename(path)
                segments.extend(SimpleNamespace(source=s, file_name=_fname) for s in sources)
            except Exception as e:
                errors.append(f"{os.path.basename(path)}: {e}")
                self.log(f"⚠ Quick Count could not read {os.path.basename(path)}: {e}")
        progress.setValue(len(paths))
        progress.close()

        if errors:
            QMessageBox.warning(
                self, self.tr("Quick Count"),
                self.tr("Some files could not be read:\n\n") + "\n".join(errors))

        if not segments:
            QMessageBox.information(
                self, self.tr("Quick Count"),
                self.tr("No countable source segments were found in the selected file(s)."))
            return

        scope = (os.path.basename(paths[0]) if len(paths) == 1
                 else f"{len(paths)} files")
        try:
            from modules.statistics_dialog_qt import StatisticsDialog
            dialog = StatisticsDialog(
                parent=self,
                db_manager=self.db_manager,
                segments=segments,
                source_lang=src_lang,
                target_lang=tgt_lang,
                tm_choices=self._all_tm_choices(),
                preselected_tm_ids=[],
                project_name=scope,
            )
            dialog.exec()
        except Exception as e:
            self.log(f"Error opening Quick Count: {e}")
            import traceback
            traceback.print_exc()
            QMessageBox.critical(self, self.tr("Error"), f"Failed to open Quick Count:\n{str(e)}")
