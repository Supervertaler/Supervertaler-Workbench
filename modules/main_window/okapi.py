"""The Okapi converter: starting and stopping it, and exporting other
document formats through it.

Part of the main window (SupervertalerQt), moved here unchanged. The
methods run as the main window's own: `self` is the SupervertalerQt.
"""

import os
import platform
import traceback
from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFileDialog,
    QMessageBox,
    QProgressDialog,
)
from modules.okapi_import_options import okapi_sidecar_options
from pathlib import Path


class OkapiMixin:
    def _try_okapi_merge_export(self, segments, original_path, output_path):
        """Attempt to export via Okapi sidecar merge. Returns True on success, False to fall back."""
        try:
            import re as _re

            # Check sidecar availability
            if not getattr(self, 'okapi_sidecar', None) or not self.okapi_sidecar.is_running():
                self.log("Okapi sidecar not running -- falling back to standard export")
                QMessageBox.warning(
                    self, "Okapi Sidecar Unavailable",
                    "This project was imported with the Okapi engine, but the sidecar "
                    "is not currently running.\n\n"
                    "Falling back to standard export. Some formatting may differ from "
                    "the original document."
                )
                return False

            # Build translations list, ONE entry per Okapi text-unit ID.
            #
            # Why one-per-TU and not one-per-segment: the sidecar applies
            # SRX sentence segmentation only inside its extract helper
            # (segmentTextContentWithTags) – it doesn't actually re-segment
            # the underlying ITextUnit. So when /merge re-opens the original
            # file and reads the same TU, srcSegments.count() == 1. The
            # merge loop then drops any MergeSegment with segmentIndex >= 1,
            # silently losing the 2nd, 3rd, … sentences of every paragraph
            # we sent (verified end-to-end against an IDML round-trip in
            # v1.9.470 testing).
            #
            # Workaround: group all our per-sentence segments back by
            # okapi_tu_id, sort by okapi_segment_index, concatenate the
            # translations with a single space, and send one merge entry
            # per TU at segmentIndex=0. The whole TU's text gets written
            # into the single source segment that actually exists in the
            # filter's view of the document. This costs us a possible
            # extra space at SRX sentence boundaries (the LLM strips
            # leading/trailing whitespace from each segment translation),
            # which is a rounding error in laid-out text but matters less
            # than the alternative of silently losing sentences.
            from collections import defaultdict as _defaultdict
            tu_groups: dict = _defaultdict(list)  # tu_id → [(seg_idx, translation)]
            skipped = 0
            for seg in segments:
                tu_id = getattr(seg, 'okapi_tu_id', '')
                seg_idx = getattr(seg, 'okapi_segment_index', -1)

                if not tu_id or seg_idx < 0:
                    skipped += 1
                    continue

                # Use target if translated, otherwise use source
                translation = seg.target.strip() if seg.target and seg.target.strip() else seg.source

                # Keep inline formatting tags (<b>, <i>, etc.) -- the Java
                # merge reconstructs Okapi inline codes from them.  Only
                # strip Supervertaler-specific tags that Okapi wouldn't know.
                translation = _re.sub(r'</?(?:bi|li-[bo]|li)>', '', translation)

                tu_groups[tu_id].append((seg_idx, translation))

            translations = []
            for tu_id, sub_segs in tu_groups.items():
                sub_segs.sort(key=lambda pair: pair[0])
                combined = ' '.join(t for _, t in sub_segs)

                # Strip Okapi-internal run tags (<run1>, </run2>, …) that
                # occasionally leak through from the source-to-cf
                # conversion when SRX segments split inside an inline run.
                # Our format uses <cf>/<b>/<i> etc.; <run\d+> tags should
                # never appear in user-facing translations. If they reach
                # /merge they unbalance Okapi's tag stack and crash the
                # OpenXML writer with NoSuchElementException — see the
                # BlockTextUnitWriter.writeCode → ArrayDeque.pop crash
                # reported by a Mac user on v1.9.484 with a docx whose
                # paragraphs had inline run boundaries mid-sentence.
                combined = _re.sub(r'</?run\d+>', '', combined)

                translations.append({
                    'id': tu_id,
                    'segmentIndex': 0,
                    'translation': combined,
                })

            if not translations:
                self.log("No segments with Okapi metadata found -- falling back to standard export")
                return False

            multi_tu_count = sum(1 for tu_id, sub in tu_groups.items() if len(sub) > 1)
            if multi_tu_count:
                self.log(f"Combining sub-segments for merge: {multi_tu_count} text-unit(s) "
                         f"had multiple sentences (concatenated for /merge)")
            if skipped > 0:
                self.log(f"{skipped} segments without Okapi metadata will use source text in merge")

            self.log(f"Merging via Okapi sidecar ({len(translations)} segments)...")

            result_path = self.okapi_sidecar.merge(
                original_path=original_path,
                translations=translations,
                source_lang=self.current_project.source_lang,
                target_lang=self.current_project.target_lang,
                output_path=output_path,
                options=okapi_sidecar_options(getattr(self.current_project, 'import_options', None)),
            )

            self.log(f"Okapi merge complete: {os.path.basename(result_path)}")

            # Post-process (DOCX only — Okapi can also produce IDML/HTML/XLIFF,
            # which python-docx can't open):
            if result_path.lower().endswith('.docx'):
                # 1) Repair run font sizes the merge may have dropped.
                self._normalize_docx_run_font_sizes(result_path)
                # 2) Attach segment comments as client-facing Word comments
                #    (they flag problems in the source, so they ship with the file).
                self._attach_segment_notes_as_docx_comments(result_path, segments)

            translated_count = sum(1 for seg in segments if seg.target and seg.target.strip())
            QMessageBox.information(
                self, "Export Complete (Okapi Merge)",
                f"Successfully exported {translated_count} translated segments to:\n\n"
                f"{os.path.basename(output_path)}\n\n"
                f"Used Okapi Framework merge for faithful format preservation."
            )
            # Post-export safeguard: warn if the file looks like it lost text.
            wc = self._verify_export_word_count(segments, result_path)
            self._show_export_word_count_warning([wc])
            return True

        except FileNotFoundError:
            self.log(f"Original file not found for Okapi merge: {original_path}")
            QMessageBox.warning(
                self, "Original File Not Found",
                f"The original DOCX file needed for Okapi merge was not found:\n\n"
                f"{original_path}\n\n"
                "Falling back to standard export."
            )
            return False

        except Exception as e:
            self.log(f"Okapi merge failed, falling back to standard: {e}")
            import traceback
            traceback.print_exc()
            QMessageBox.warning(
                self, "Okapi Merge Failed",
                f"Okapi merge encountered an error:\n\n{str(e)}\n\n"
                "Falling back to standard export."
            )
            return False

    def export_okapi_merge(self):
        """Export an Okapi-imported project back to its original format.

        Pairs with ``import_okapi_format``: looks up the original file path
        stored on import, opens a Save As dialog defaulted to the same
        extension, and calls ``_try_okapi_merge_export`` to round-trip
        the translation through the sidecar.
        """
        if not self.current_project or not self.current_project.segments:
            QMessageBox.warning(self, "Nothing to Export", "Open a project first.")
            return
        if getattr(self.current_project, 'import_engine', '') != 'okapi':
            QMessageBox.warning(
                self, "Not an Okapi project",
                "This menu entry only works for projects imported via "
                "<i>Project → Import → Other format (via Okapi)…</i> (or DOCX, "
                "but DOCX has its own dedicated export entry).<br><br>"
                "The current project was imported through a different path."
            )
            return

        original_path = (getattr(self, 'original_docx', None)
                         or getattr(self, 'current_document_path', None)
                         or getattr(self.current_project, 'original_docx_path', None))
        if not original_path or not os.path.exists(original_path):
            QMessageBox.warning(
                self, "Original File Not Found",
                f"The original source file is needed for the Okapi merge step "
                f"but couldn't be located:<br><br>"
                f"<code>{original_path or 'no path stored'}</code><br><br>"
                f"Re-import the source file to refresh the link, then try "
                f"the export again."
            )
            return

        ext = Path(original_path).suffix.lower()  # includes the dot
        ext_label = ext.lstrip('.').upper() or "file"
        default_name = self._project_export_path(
            Path(original_path).stem + "_translated" + ext)  # into target/ (#228)

        file_path, _ = QFileDialog.getSaveFileName(
            self,
            f"Export {ext_label} via Okapi",
            default_name,
            f"{ext_label} files (*{ext});;All Files (*.*)"
        )
        if not file_path:
            return

        ok = self._try_okapi_merge_export(
            self.current_project.segments, original_path, file_path
        )
        if not ok:
            self.log("⚠ Okapi merge export did not complete – check the log above.")

    def _ensure_okapi_sidecar(self) -> bool:
        """Make sure the Okapi sidecar is up. Returns True on success.

        Handles three scenarios:

        1. Sidecar is already running – return True immediately.
        2. Sidecar is installed (JAR present) but not running – start it.
        3. Sidecar is not installed (typical for pip-installed users
           without the bundled JRE) – offer a one-click download from
           the GitHub release, with a progress dialog. After the download
           completes, start the sidecar and verify.

        Shows a clear error dialog and returns False if any step fails
        or the user declines the download.
        """
        if not self.okapi_sidecar:
            QMessageBox.critical(
                self,
                "Okapi sidecar unavailable",
                "The Okapi sidecar client is not initialised. This is "
                "usually caused by a missing 'requests' Python package. "
                "Please reinstall Supervertaler."
            )
            return False

        # Fast path: already running – unless it is an outdated lazy-downloaded
        # JAR. Start-up (_start_okapi_sidecar) launches whatever JAR is on
        # disk, so without this check the self-heal below was never reached.
        # Stop it before updating: on Windows the update replaces the JRE the
        # running sidecar is using.
        if self.okapi_sidecar.is_running():
            if not self.okapi_sidecar.needs_update():
                return True
            self.okapi_sidecar.stop()
            if self.okapi_sidecar.is_running():
                # Not ours to stop (e.g. another Workbench window started
                # it). Keep using it rather than replace files under it.
                return True

        # Path 2: installed but not running. Try to start it.
        if self.okapi_sidecar.is_installed():
            # Self-heal: a lazy-downloaded JAR left over from an older app
            # version won't match what this build expects. Refresh it to the
            # pinned version automatically so the user never has to rebuild or
            # delete it by hand. Best-effort: if the download fails (e.g.
            # offline) we fall back to starting the existing JAR.
            if self.okapi_sidecar.needs_update():
                old = self.okapi_sidecar.installed_version_hint() or "older"
                self.log(f"Okapi sidecar is outdated ({old} → "
                         f"v{self.okapi_sidecar.EXPECTED_VERSION}); downloading update…")
                prog = QProgressDialog("Updating Okapi sidecar…", None, 0, 100, self)
                prog.setWindowTitle(self.tr("Updating Okapi sidecar"))
                prog.setWindowModality(Qt.WindowModality.WindowModal)
                prog.setMinimumDuration(0)
                prog.setAutoClose(False)
                prog.setAutoReset(False)
                prog.show()
                QApplication.processEvents()

                def _upd_progress(done, total):
                    if total > 0:
                        prog.setValue(int(done * 100 / total))
                        prog.setLabelText(
                            f"Updating Okapi sidecar… "
                            f"({done // (1024*1024)} / {total // (1024*1024)} MB)")
                    QApplication.processEvents()

                try:
                    if self.okapi_sidecar.download_install(progress_callback=_upd_progress):
                        self.log(f"✅ Okapi sidecar updated to v{self.okapi_sidecar.EXPECTED_VERSION}")
                    else:
                        self.log("⚠ Sidecar update download failed – continuing with the existing version")
                except Exception as e:
                    self.log(f"⚠ Sidecar update error: {e} – continuing with the existing version")
                finally:
                    try:
                        prog.close()
                    except Exception:
                        pass

            self.log("Starting Okapi sidecar…")
            if self.okapi_sidecar.start() and self.okapi_sidecar.is_running():
                return True
            QMessageBox.critical(
                self,
                "Okapi sidecar failed to start",
                "DOCX import requires the Okapi sidecar, but it failed "
                "to start.\n\n"
                "What to try:\n"
                "• Restart Supervertaler.\n"
                "• Check the diagnostic log for errors mentioning "
                "\"okapi sidecar\"."
            )
            return False

        # Path 3: not installed at all – offer a download.
        # Four flavours by platform + arch:
        #  - Windows:        ~70 MB bundle (JAR + bundled Windows JRE).
        #  - Apple Silicon:  ~50 MB bundle (JAR + bundled arm64 JRE).
        #  - Intel Mac:      ~28 MB JAR; requires system Java.
        #  - Linux:          ~28 MB JAR; requires system Java.
        sysname = platform.system()
        is_apple_silicon = (sysname == "Darwin"
                            and platform.machine() == "arm64")
        has_bundled_jre = (sysname == "Windows") or is_apple_silicon

        if not has_bundled_jre:
            # Intel Mac / Linux: verify Java is reachable before
            # downloading the JAR. Otherwise we'd download 28 MB and
            # then fail to launch, which is a worse experience than
            # telling the user up-front what's missing.
            java_path = self.okapi_sidecar._find_java()
            if java_path is None:
                if sysname == "Darwin":
                    install_hint = (
                        "Install Java first (one-time setup):\n"
                        "  • Homebrew (recommended): brew install --cask temurin@17\n"
                        "  • Or download from https://adoptium.net\n\n"
                        "Verify with: java -version\n"
                        "Then restart Supervertaler and re-try the import."
                    )
                else:
                    install_hint = (
                        "Install Java first (one-time setup):\n"
                        "  • Debian/Ubuntu: sudo apt install default-jre\n"
                        "  • Fedora/RHEL:  sudo dnf install java-latest-openjdk\n"
                        "  • Arch:         sudo pacman -S jre-openjdk\n"
                        "  • Or download from https://www.java.com\n\n"
                        "Then re-try the import."
                    )
                self.log("⚠️ Showing 'Java required' dialog – "
                         "DOCX import blocked, no system Java found "
                         f"({sysname})")
                QMessageBox.critical(
                    self,
                    "Java required",
                    "DOCX import requires the Okapi sidecar, a small "
                    "Java service. No Java runtime was found on your "
                    "system.\n\n" + install_hint
                )
                self.log("ℹ️ User dismissed 'Java required' dialog – "
                         "import aborted")
                return False

        if has_bundled_jre:
            size_text = "about 50 MB" if is_apple_silicon else "about 70 MB"
            extra_note = ""
        else:
            size_text = "about 28 MB"
            extra_note = (
                "Java was detected on your system, so no separate "
                "Java install is needed.\n\n"
            )

        self.log(f"ℹ️ Showing 'Download Okapi sidecar?' dialog "
                 f"({size_text}, has_bundled_jre={has_bundled_jre})")
        reply = QMessageBox.question(
            self,
            "Download Okapi sidecar?",
            "DOCX import requires the Okapi sidecar – a small Java "
            "service that handles document extraction.\n\n"
            "Supervertaler can download and install it for you "
            f"({size_text}, one-time download). It will be saved to "
            "your local app data folder.\n\n"
            f"{extra_note}"
            "Download now?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if reply != QMessageBox.StandardButton.Yes:
            self.log("ℹ️ User declined sidecar download – import aborted")
            return False
        self.log("ℹ️ User accepted sidecar download – starting fetch")

        progress = QProgressDialog(
            "Downloading Okapi sidecar…",
            None,  # not cancellable mid-download (extraction would leave a mess)
            0, 100, self
        )
        progress.setWindowTitle(self.tr("Installing Okapi sidecar"))
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        progress.show()
        QApplication.processEvents()

        def _dl_progress(done, total):
            if total > 0:
                pct = int(done * 100 / total)
                progress.setValue(pct)
                progress.setLabelText(
                    f"Downloading Okapi sidecar… "
                    f"({done // (1024*1024)} / {total // (1024*1024)} MB)"
                )
            else:
                progress.setLabelText(
                    f"Downloading Okapi sidecar… "
                    f"({done // (1024*1024)} MB)"
                )
            QApplication.processEvents()

        try:
            ok = self.okapi_sidecar.download_install(progress_callback=_dl_progress)
        finally:
            try:
                progress.close()
            except Exception:
                pass

        if not ok:
            QMessageBox.critical(
                self,
                "Sidecar download failed",
                "The Okapi sidecar could not be downloaded. Check your "
                "internet connection and try again.\n\n"
                "If the problem persists, you can download the full "
                "Supervertaler release zip from GitHub – it ships with "
                "the sidecar bundled."
            )
            return False

        # Now try to start the freshly-installed sidecar.
        self.log("Starting freshly-installed Okapi sidecar…")
        if self.okapi_sidecar.start() and self.okapi_sidecar.is_running():
            self.log(f"✅ Okapi sidecar v{self.okapi_sidecar.get_version() or '?'} ready")
            return True

        QMessageBox.critical(
            self,
            "Sidecar installed but failed to start",
            "The Okapi sidecar was downloaded successfully, but it "
            "failed to launch.\n\n"
            "Check the diagnostic log for errors. You may need to "
            "restart Supervertaler."
        )
        return False

    def _init_okapi_sidecar_deferred(self):
        """Schedule Okapi sidecar startup after the UI is fully loaded.
        This is non-blocking – the sidecar starts in the background."""
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(1500, self._start_okapi_sidecar)

    def _start_okapi_sidecar(self):
        """Start the local Okapi sidecar process (if available).

        We deliberately keep ``self.okapi_sidecar`` set even when the
        sidecar can't run yet (no JAR, no Java, etc.). The lazy-download
        / install flow at import time relies on the OkapiSidecar object
        being present so it can drive the platform-specific download.
        Setting it to None here would short-circuit that flow with a
        misleading "client not initialised" error.
        """
        try:
            from modules.okapi_sidecar import OkapiSidecar
            self.okapi_sidecar = OkapiSidecar()

            if self.okapi_sidecar.is_available:
                if self.okapi_sidecar.start():
                    version = self.okapi_sidecar.get_version() or "?"
                    self.log(f"✅ Okapi sidecar started (v{version}) – "
                             f"enhanced file filters available")
                else:
                    self.log("⚠️ Okapi sidecar failed to start – "
                             "lazy install flow will retry on first use")
            elif not self.okapi_sidecar.is_installed():
                self.log("ℹ️ Okapi sidecar not installed yet – "
                         "will offer to download on first DOCX import")
            else:
                # JAR present but Java missing (Intel Mac / Linux only;
                # Windows and Apple Silicon ship a bundled JRE).
                self.log("⚠️ Okapi sidecar found but Java not detected – "
                         "install Java to enable enhanced filters")

            # Proactive heads-up for Intel Mac / Linux users with no Java.
            # Without it, the user happily uses the app for plain-text work
            # and only discovers the missing dependency when they try to
            # open a Word document (where the lazy-download flow will catch
            # them anyway). Better to warn at startup so they can fix it
            # before hitting the wall.
            if self.okapi_sidecar.needs_system_java_install():
                self._maybe_show_okapi_java_warning()
        except Exception as e:
            self.log(f"⚠️ Okapi sidecar unavailable: {e}")
            self.okapi_sidecar = None

    def _maybe_show_okapi_java_warning(self):
        """Show a one-time-per-session dialog explaining how to install
        Java on Intel Macs / Linux. Respects a 'don't show again' user pref
        stored under settings → general → ``okapi_java_warning_dismissed``.
        """
        try:
            general = self._load_settings_section("general")
            if general.get("okapi_java_warning_dismissed"):
                self.log("ℹ️ Skipping Java-required startup dialog "
                         "(user previously checked 'Don't show again')")
                return
        except Exception:
            general = {}

        self.log("⚠️ Showing 'Java needed for enhanced file filters' "
                 "startup dialog (no system Java detected on this "
                 "platform; sidecar can't run)")

        from PyQt6.QtWidgets import QMessageBox, QCheckBox

        sysname = platform.system()
        if sysname == "Darwin":
            install_cmd = "brew install --cask temurin@17"
            verify_cmd = "java -version"
            platform_note = (
                "Intel Macs need a separate Java install – the bundled "
                "Java runtime in the macOS download is built for Apple "
                "Silicon only."
            )
        else:
            install_cmd = "sudo apt install default-jre   # or your distro's equivalent"
            verify_cmd = "java -version"
            platform_note = (
                "On Linux the sidecar uses your system Java rather than "
                "a bundled one."
            )

        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Information)
        box.setWindowTitle(self.tr("Java needed for enhanced file filters"))
        box.setText(
            "Supervertaler can use a small Java helper (the Okapi "
            "sidecar) to import Word, Excel, HTML and other office "
            "documents. No Java runtime was found on your system."
        )
        box.setInformativeText(
            f"{platform_note}\n\n"
            f"To enable it, install Java 17 (one-time, ~150 MB):\n\n"
            f"    {install_cmd}\n\n"
            f"Then verify with:\n\n"
            f"    {verify_cmd}\n\n"
            "Restart Supervertaler after installing. Plain-text "
            "translation, TMX, etc. all work without Java."
        )
        dont_show = QCheckBox(self.tr("Don't show this again"))
        box.setCheckBox(dont_show)
        box.addButton(QMessageBox.StandardButton.Ok)
        box.exec()
        self.log(f"ℹ️ User dismissed Java-required dialog "
                 f"(don't-show-again={'on' if dont_show.isChecked() else 'off'})")

        if dont_show.isChecked():
            try:
                general["okapi_java_warning_dismissed"] = True
                self._save_settings_section("general", general)
            except Exception as e:
                self.log(f"⚠ Could not persist Java-warning preference: {e}")

    def _stop_okapi_sidecar(self):
        """Stop the Okapi sidecar process on app exit."""
        try:
            if self.okapi_sidecar:
                self.okapi_sidecar.stop()
                self.okapi_sidecar = None
        except Exception:
            pass
