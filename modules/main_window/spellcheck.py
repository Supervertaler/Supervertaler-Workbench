"""Spellchecking the target: switching it on and off, the custom dictionary,
and the spellcheck information dialog.

Part of the main window (SupervertalerQt), moved here unchanged. The
methods run as the main window's own: `self` is the SupervertalerQt.
"""

import os
from PyQt6.QtCore import QUrl, Qt
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)
from modules.grid_editors import EditableGridTextEditor, TagHighlighter
from modules.platform_helpers import open_folder
from modules.shortcut_display import format_shortcut_for_display


class SpellcheckMixin:
    def add_word_to_dictionary_shortcut(self):
        """Add word at cursor position to custom dictionary (Alt+D shortcut)
        
        Finds the misspelled word at the current cursor position and adds it to the
        custom dictionary. Works when focus is in a grid target cell.
        """
        # Get currently focused widget
        focused_widget = QApplication.focusWidget()
        
        # Check if we're in an editable grid cell
        if not isinstance(focused_widget, EditableGridTextEditor):
            self.statusBar().showMessage(
                f"{format_shortcut_for_display('Alt+D')}: Place cursor on a misspelled word in the target cell first",
                3000,
            )
            return
        
        # Get cursor position
        cursor = focused_widget.textCursor()
        
        # Try to find misspelled word at cursor
        word_info = focused_widget._get_misspelled_word_at_cursor(cursor)
        
        if word_info[0] is None:
            # No misspelled word found at cursor
            self.statusBar().showMessage("No misspelled word at cursor position", 3000)
            return
        
        word, start_pos, end_pos = word_info
        
        # Add to dictionary
        focused_widget._add_to_dictionary(word)

    def _initialize_spellcheck_for_target_language(self, target_lang: str):
        """Initialize spellcheck for the given target language after import/load"""
        if not target_lang:
            return
        
        try:
            if hasattr(self, 'spellcheck_manager') and self.spellcheck_manager:
                if self.spellcheck_manager.set_language(target_lang):
                    self.log(f"✓ Spellcheck initialized for target language: {target_lang}")
                else:
                    self.log(f"⚠ Spellcheck dictionary not available for {target_lang}")
        except Exception as e:
            self.log(f"⚠ Could not initialize spellcheck: {e}")

    def _update_spellcheck_button_style(self):
        """Update spellcheck button style based on enabled state"""
        if hasattr(self, 'spellcheck_btn'):
            if self.spellcheck_enabled:
                self.spellcheck_btn.setStyleSheet("background-color: #4CAF50; color: white; font-weight: bold; padding: 3px 5px;")
            else:
                self.spellcheck_btn.setStyleSheet("background-color: #9E9E9E; color: white; font-weight: bold; padding: 3px 5px;")

    def _toggle_spellcheck(self, checked=None):
        """Toggle spellcheck on/off"""
        self.spellcheck_enabled = not self.spellcheck_enabled
        TagHighlighter.set_spellcheck_enabled(self.spellcheck_enabled)
        
        # Update UI
        self._update_spellcheck_button_style()
        if hasattr(self, 'spellcheck_btn'):
            self.spellcheck_btn.setChecked(self.spellcheck_enabled)
        if hasattr(self, 'spellcheck_toggle_action'):
            self.spellcheck_toggle_action.setChecked(self.spellcheck_enabled)
        
        # Use project's target language if available, otherwise fall back to app preference
        spellcheck_lang = self.current_project.target_lang if self.current_project else self.target_language
        
        # Initialize spellcheck for target language if enabling
        if self.spellcheck_enabled:
            if self.spellcheck_manager.set_language(spellcheck_lang):
                self.log(f"✓ Spellcheck enabled for {spellcheck_lang} (backend: {self.spellcheck_manager.get_backend_info()})")
            else:
                self.log(f"⚠ Spellcheck dictionary not available for {spellcheck_lang}")
                # Disable spellcheck since we couldn't initialize
                self.spellcheck_enabled = False
                TagHighlighter.set_spellcheck_enabled(False)
                self._update_spellcheck_button_style()
                if hasattr(self, 'spellcheck_btn'):
                    self.spellcheck_btn.setChecked(False)
                if hasattr(self, 'spellcheck_toggle_action'):
                    self.spellcheck_toggle_action.setChecked(False)
                # Show warning with diagnostics
                diag = self.spellcheck_manager.get_diagnostics()
                QMessageBox.warning(
                    self, "Spellcheck Unavailable",
                    f"Could not initialize spellcheck for '{spellcheck_lang}'.\n\n"
                    f"Backends:\n"
                    f"• Hunspell: {'Available' if diag['hunspell_available'] else 'Not installed'}\n"
                    f"• pyspellchecker: {'Available' if diag['pyspellchecker_available'] else 'Not installed'}\n\n"
                    f"Try opening Spellcheck Info (right-click menu) for more details."
                )
        else:
            self.log("✗ Spellcheck disabled")
        
        # Save setting
        self._save_spellcheck_settings()
        
        # Refresh grid to show/hide underlines
        self._refresh_all_highlighters()

    def _toggle_spellcheck_from_button(self, checked):
        """Handle button click (separate from menu action to avoid double-toggle)"""
        # The button click already toggles the checked state, so we need to sync
        self.spellcheck_enabled = checked
        TagHighlighter.set_spellcheck_enabled(self.spellcheck_enabled)
        
        # Update menu action
        if hasattr(self, 'spellcheck_toggle_action'):
            self.spellcheck_toggle_action.setChecked(self.spellcheck_enabled)
        
        # Same logic as _toggle_spellcheck
        self._update_spellcheck_button_style()
        
        # Use project's target language if available, otherwise fall back to app preference
        spellcheck_lang = self.current_project.target_lang if self.current_project else self.target_language
        
        if self.spellcheck_enabled:
            if self.spellcheck_manager.set_language(spellcheck_lang):
                self.log(f"✓ Spellcheck enabled for {spellcheck_lang} (backend: {self.spellcheck_manager.get_backend_info()})")
            else:
                self.log(f"⚠ Spellcheck could not be initialized for {spellcheck_lang}")
                # Disable spellcheck since we couldn't initialize
                self.spellcheck_enabled = False
                TagHighlighter.set_spellcheck_enabled(False)
                self._update_spellcheck_button_style()
                if hasattr(self, 'spellcheck_btn'):
                    self.spellcheck_btn.setChecked(False)
                if hasattr(self, 'spellcheck_toggle_action'):
                    self.spellcheck_toggle_action.setChecked(False)
                # Show warning
                diag = self.spellcheck_manager.get_diagnostics()
                QMessageBox.warning(
                    self, "Spellcheck Unavailable",
                    f"Could not initialize spellcheck for '{spellcheck_lang}'.\n\n"
                    f"Backends:\n"
                    f"• Hunspell: {'Available' if diag['hunspell_available'] else 'Not installed'}\n"
                    f"• pyspellchecker: {'Available' if diag['pyspellchecker_available'] else 'Not installed'}\n\n"
                    f"Try opening Spellcheck Info for more details."
                )
        else:
            self.log("✗ Spellcheck disabled")
        
        self._save_spellcheck_settings()
        self._refresh_all_highlighters()

    def _refresh_all_highlighters(self):
        """Refresh syntax highlighters in VISIBLE rows only (performance optimization)
        
        Instead of refreshing all 3000+ rows, we only refresh the rows currently
        visible in the viewport. This makes add-to-dictionary/ignore instant.
        Other rows will get updated when they become visible (on navigation).
        """
        if not hasattr(self, 'table') or not self.table:
            return
        
        # Get visible row range from viewport
        viewport = self.table.viewport()
        if not viewport:
            return
            
        # Find first and last visible rows
        first_visible_row = self.table.rowAt(0)
        last_visible_row = self.table.rowAt(viewport.height())
        
        # Handle edge cases
        if first_visible_row < 0:
            first_visible_row = 0
        if last_visible_row < 0 or last_visible_row >= self.table.rowCount():
            last_visible_row = self.table.rowCount() - 1
            
        # Add small buffer for smoother experience
        first_visible_row = max(0, first_visible_row - 2)
        last_visible_row = min(self.table.rowCount() - 1, last_visible_row + 2)
        
        # Only refresh visible rows
        for row in range(first_visible_row, last_visible_row + 1):
            # Target column (3) has editable text editor with spellcheck
            target_widget = self.table.cellWidget(row, 3)
            if target_widget and hasattr(target_widget, 'highlighter'):
                target_widget.highlighter.rehighlight()

    def _open_custom_dictionary_dialog(self):
        """Manage the spellchecker's custom dictionary (issue #109): edit the
        word list directly, import (plain lists or Hunspell .dic), export,
        and sort / de-duplicate."""
        from modules.spellcheck_manager import parse_word_list

        dialog = QDialog(self)
        dialog.setWindowTitle(self.tr("Custom Dictionary"))
        dialog.setMinimumSize(460, 540)
        layout = QVBoxLayout(dialog)

        info_label = QLabel(self.tr(
            "Words in the custom dictionary are never marked as spelling errors. "
            "One word per line; case does not matter. Click Save to keep your changes."))
        info_label.setWordWrap(True)
        layout.addWidget(info_label)

        tools = QHBoxLayout()
        import_btn = QPushButton(self.tr("📥 Import..."))
        import_btn.setToolTip(self.tr(
            "Add words from a text file (one per line) or a Hunspell .dic file"))
        export_btn = QPushButton(self.tr("📤 Export..."))
        export_btn.setToolTip(self.tr("Save the word list as a text file"))
        tidy_btn = QPushButton(self.tr("🔤 Sort && remove duplicates"))
        folder_btn = QPushButton(self.tr("📂 Open folder"))
        folder_btn.setToolTip(str(self.spellcheck_manager.custom_words_file))
        for b in (import_btn, export_btn, tidy_btn, folder_btn):
            tools.addWidget(b)
        tools.addStretch()
        layout.addLayout(tools)

        editor = QPlainTextEdit()
        editor.setPlaceholderText(self.tr("Enter custom words here, one per line..."))
        editor.setPlainText('\n'.join(self.spellcheck_manager.get_custom_words()))
        layout.addWidget(editor)
        self.custom_dict_editor = editor

        count_label = QLabel()
        layout.addWidget(count_label)

        def update_count():
            words, dupes = parse_word_list(editor.toPlainText())
            text = f"{len(words)} word{'s' if len(words) != 1 else ''} in dictionary"
            if dupes:
                text += f" ({dupes} duplicate{'s' if dupes != 1 else ''} will be merged)"
            count_label.setText(text)

        editor.textChanged.connect(update_count)
        update_count()

        def tidy():
            words, _ = parse_word_list(editor.toPlainText())
            editor.setPlainText('\n'.join(words))

        def import_words():
            path, _ = QFileDialog.getOpenFileName(
                dialog, self.tr("Import Words"), "",
                "Word lists (*.txt *.dic);;All Files (*.*)")
            if not path:
                return
            try:
                with open(path, 'r', encoding='utf-8-sig', errors='replace') as f:
                    incoming, _ = parse_word_list(f.read())
            except Exception as e:
                QMessageBox.warning(dialog, self.tr("Import Words"), f"Could not read the file:\n\n{e}")
                return
            current, _ = parse_word_list(editor.toPlainText())
            added = sorted(set(incoming) - set(current))
            editor.setPlainText('\n'.join(sorted(set(current) | set(incoming))))
            QMessageBox.information(
                dialog, self.tr("Import Words"),
                f"{len(added)} new word(s) added from {os.path.basename(path)} "
                f"({len(incoming) - len(added)} already present). Click Save to keep them.")

        def export_words():
            path, _ = QFileDialog.getSaveFileName(
                dialog, self.tr("Export Words"), "custom_words.txt", "Text Files (*.txt)")
            if not path:
                return
            words, _ = parse_word_list(editor.toPlainText())
            try:
                with open(path, 'w', encoding='utf-8') as f:
                    f.write('\n'.join(words) + ('\n' if words else ''))
            except Exception as e:
                QMessageBox.warning(dialog, self.tr("Export Words"), f"Could not write the file:\n\n{e}")
                return
            self.log(f"✓ Exported {len(words)} custom dictionary words to {os.path.basename(path)}")

        def open_folder():
            QDesktopServices.openUrl(QUrl.fromLocalFile(
                str(self.spellcheck_manager.dictionaries_path)))

        tidy_btn.clicked.connect(tidy)
        import_btn.clicked.connect(import_words)
        export_btn.clicked.connect(export_words)
        folder_btn.clicked.connect(open_folder)

        button_layout = QHBoxLayout()
        button_layout.addStretch()
        save_btn = QPushButton(self.tr("💾 Save"))
        save_btn.clicked.connect(lambda: self._save_custom_dictionary(dialog))
        button_layout.addWidget(save_btn)
        cancel_btn = QPushButton(self.tr("Cancel"))
        cancel_btn.clicked.connect(dialog.reject)
        button_layout.addWidget(cancel_btn)
        layout.addLayout(button_layout)

        dialog.exec()

    def _save_custom_dictionary(self, dialog):
        """Save custom dictionary from dialog (one file write, however many words)."""
        from modules.spellcheck_manager import parse_word_list
        words, _ = parse_word_list(self.custom_dict_editor.toPlainText())
        count = self.spellcheck_manager.set_custom_words(words)
        self.log(f"✓ Custom dictionary saved with {count} words")
        self._refresh_all_highlighters()
        dialog.accept()

    def _show_spellcheck_info(self):
        """Show information about spellcheck configuration with clickable links"""
        backend = self.spellcheck_manager.get_backend_info()
        language = self.spellcheck_manager.get_current_language() or "Not set"
        available_languages = self.spellcheck_manager.get_available_languages()
        available = ', '.join(available_languages) or "None"
        custom_count = len(self.spellcheck_manager.get_custom_words())
        dict_path = str(self.spellcheck_manager.dictionaries_path)
        diag = self.spellcheck_manager.get_diagnostics()
        is_hunspell = "Hunspell" in backend
        
        # Create dialog with two-column horizontal layout
        dialog = QDialog(self)
        dialog.setWindowTitle(self.tr("Spellcheck Info"))
        dialog.setMinimumWidth(700)
        dialog.setMaximumHeight(520)
        
        main_layout = QVBoxLayout(dialog)
        main_layout.setSpacing(10)
        
        # ═══════════════ Top Row: Status + Language + Backend ═══════════════
        top_row = QHBoxLayout()
        
        status_text = "✓ Enabled" if self.spellcheck_enabled else "✗ Disabled"
        status_color = "green" if self.spellcheck_enabled else "red"
        status_label = QLabel(f"<b style='color: {status_color};'>{status_text}</b>")
        top_row.addWidget(status_label)
        top_row.addSpacing(20)
        
        top_row.addWidget(QLabel(self.tr("Language:")))
        lang_combo = QComboBox()
        lang_combo.setMinimumWidth(100)
        lang_combo.addItems(available_languages)
        if language in available_languages:
            lang_combo.setCurrentText(language)
        top_row.addWidget(lang_combo)
        top_row.addSpacing(20)
        
        backend_icon = "📚" if is_hunspell else "🐍"
        backend_label = QLabel(f"{backend_icon} {backend}")
        top_row.addWidget(backend_label)
        top_row.addStretch()
        main_layout.addLayout(top_row)
        
        def on_language_changed(new_lang):
            if self.spellcheck_manager.set_language(new_lang):
                self.log(f"✓ Spellcheck language changed to: {new_lang}")
                new_backend = self.spellcheck_manager.get_backend_info()
                is_hunspell_new = "Hunspell" in new_backend
                backend_icon_new = "📚" if is_hunspell_new else "🐍"
                backend_label.setText(f"{backend_icon_new} {new_backend}")
                if self.spellcheck_enabled:
                    self._refresh_all_highlighters()
            else:
                self.log(f"⚠ Could not set spellcheck language to: {new_lang}")
        lang_combo.currentTextChanged.connect(on_language_changed)
        
        # ═══════════════ Two-Column Layout ═══════════════
        columns = QHBoxLayout()
        columns.setSpacing(15)
        
        # ─────────── LEFT COLUMN ───────────
        left_col = QVBoxLayout()
        left_col.setSpacing(8)
        
        # How It Works - determine which backend is active
        backend_lower = backend.lower()
        is_spylls = 'spylls' in backend_lower
        is_pyspellchecker = 'pyspellchecker' in backend_lower or 'built-in' in backend_lower
        
        # How It Works
        how_group = QGroupBox(self.tr("How Spellcheck Works"))
        how_layout = QVBoxLayout(how_group)
        how_layout.setSpacing(4)
        
        # Build the explanation with active backend highlighted
        spylls_style = "background-color: #90EE90; padding: 2px;" if is_spylls else ""
        pyspell_style = "background-color: #90EE90; padding: 2px;" if is_pyspellchecker else ""
        hunspell_style = "background-color: #90EE90; padding: 2px;" if is_hunspell and not is_spylls else ""
        
        how_label = QLabel(
            f"<div style='{pyspell_style}'>"
            "🐍 <b>Built-in (pyspellchecker)</b><br>"
            "&nbsp;&nbsp;&nbsp;EN, NL, DE, FR, ES, PT, IT, RU<br>"
            "&nbsp;&nbsp;&nbsp;<i>Works out of the box!</i></div><br>"
            f"<div style='{spylls_style}'>"
            "📗 <b>Spylls (pure Python Hunspell)</b><br>"
            "&nbsp;&nbsp;&nbsp;Bundled: EN, RU, SV<br>"
            "&nbsp;&nbsp;&nbsp;+ Any .dic/.aff files you add</div><br>"
            f"<div style='{hunspell_style}'>"
            "📚 <b>Hunspell (native C library)</b><br>"
            "&nbsp;&nbsp;&nbsp;Any language with .dic/.aff files<br>"
            "&nbsp;&nbsp;&nbsp;<i>Requires cyhunspell (Linux/Mac)</i></div><br>"
            "<b>⚡ Auto-switching:</b> Best available backend<br>"
            "is used automatically based on dictionaries."
        )
        how_label.setWordWrap(True)
        how_layout.addWidget(how_label)
        left_col.addWidget(how_group)
        
        # Available Languages
        lang_group = QGroupBox(self.tr("Available Languages"))
        lang_layout = QVBoxLayout(lang_group)
        lang_list = QLabel(available)
        lang_list.setWordWrap(True)
        lang_layout.addWidget(lang_list)
        left_col.addWidget(lang_group)
        
        # Custom Dictionary
        custom_group = QGroupBox(self.tr("Custom Dictionary"))
        custom_layout = QVBoxLayout(custom_group)
        custom_layout.setSpacing(4)
        custom_layout.addWidget(QLabel(f"<b>{custom_count}</b> words in custom_words.txt"))
        custom_folder = str(self.spellcheck_manager.dictionaries_path)
        custom_btn = QPushButton(self.tr("📁 Open Folder"))
        custom_btn.clicked.connect(lambda: self._open_folder_in_explorer(custom_folder))
        custom_layout.addWidget(custom_btn)
        left_col.addWidget(custom_group)
        
        left_col.addStretch()
        columns.addLayout(left_col)
        
        # ─────────── RIGHT COLUMN ───────────
        right_col = QVBoxLayout()
        right_col.setSpacing(8)
        
        # Add More Dictionaries
        hunspell_group = QGroupBox(self.tr("Add More Dictionaries"))
        hunspell_layout = QVBoxLayout(hunspell_group)
        hunspell_layout.setSpacing(4)
        
        hunspell_layout.addWidget(QLabel(self.tr("<b>To add more languages:</b>")))
        hunspell_layout.addWidget(QLabel(self.tr("1. Download .dic + .aff files for your language")))
        hunspell_layout.addWidget(QLabel(self.tr("2. Place them in the dictionaries folder")))
        hunspell_layout.addWidget(QLabel(self.tr("3. Restart Supervertaler")))
        
        hunspell_layout.addWidget(QLabel(""))
        hunspell_layout.addWidget(QLabel(self.tr("<b>Download sources:</b>")))
        
        memoq_label = QLabel(self.tr('🌐 <a href="https://hunspell.memoq.com/">hunspell.memoq.com</a> – 70+ languages'))
        memoq_label.setOpenExternalLinks(True)
        hunspell_layout.addWidget(memoq_label)
        
        github_label = QLabel(self.tr('🌐 <a href="https://github.com/wooorm/dictionaries/tree/main/dictionaries">GitHub: wooorm/dictionaries</a> – 92+ languages'))
        github_label.setOpenExternalLinks(True)
        hunspell_layout.addWidget(github_label)
        
        libreoffice_label = QLabel(self.tr('🌐 <a href="https://extensions.libreoffice.org/?Tags%5B%5D=50">LibreOffice Extensions</a> – .oxt → .zip'))
        libreoffice_label.setOpenExternalLinks(True)
        hunspell_layout.addWidget(libreoffice_label)
        
        dict_btn = QPushButton(self.tr("📁 Open Dictionaries Folder"))
        dict_btn.clicked.connect(lambda: self._open_folder_in_explorer(dict_path))
        hunspell_layout.addWidget(dict_btn)
        
        # Note about Spylls bundled dictionaries location
        spylls_note = QLabel(
            "<small><i>💡 Spylls bundled dicts are in its pip package.<br>"
            "Add your own .dic/.aff files to the folder above.</i></small>"
        )
        spylls_note.setWordWrap(True)
        hunspell_layout.addWidget(spylls_note)
        
        right_col.addWidget(hunspell_group)
        
        # Diagnostics
        diag_group = QGroupBox(self.tr("🔧 Diagnostics"))
        diag_layout = QVBoxLayout(diag_group)
        diag_layout.setSpacing(2)
        
        diag_text = (
            f"<table cellspacing='2'>"
            f"<tr><td>Hunspell:</td><td>{'✓' if diag.get('hunspell_available') else '✗'} avail, {'✓' if diag.get('hunspell_initialized') else '✗'} init</td></tr>"
            f"<tr><td>Spylls:</td><td>{'✓' if diag.get('spylls_available') else '✗'} avail, {'✓' if diag.get('spylls_initialized') else '✗'} init</td></tr>"
            f"<tr><td>pyspellchecker:</td><td>{'✓' if diag.get('pyspellchecker_available') else '✗'} avail, {'✓' if diag.get('pyspellchecker_initialized') else '✗'} init</td></tr>"
            f"<tr><td>Custom words:</td><td>{diag.get('custom_words_count', 0)}</td></tr>"
            f"<tr><td>Session ignored:</td><td>{diag.get('ignored_words_count', 0)}</td></tr>"
            f"<tr><td>Cache size:</td><td>{diag.get('cache_size', 0)}</td></tr>"
        )
        if diag.get('pyspellchecker_word_count'):
            diag_text += f"<tr><td>Dict words:</td><td>{diag['pyspellchecker_word_count']:,}</td></tr>"
        diag_text += "</table>"
        
        diag_label = QLabel(diag_text)
        diag_label.setTextFormat(Qt.TextFormat.RichText)
        diag_layout.addWidget(diag_label)
        
        # Show warning only if NO backend is initialized
        any_initialized = diag.get('hunspell_initialized') or diag.get('spylls_initialized') or diag.get('pyspellchecker_initialized')
        if not any_initialized:
            warn_label = QLabel(self.tr("<span style='color: orange;'>⚠️ Not initialized - try changing language</span>"))
            diag_layout.addWidget(warn_label)
        
        right_col.addWidget(diag_group)
        
        # Project Links (for technical users)
        links_group = QGroupBox(self.tr("📖 Project Links"))
        links_layout = QVBoxLayout(links_group)
        links_layout.setSpacing(2)
        
        pyspell_link = QLabel(self.tr('<a href="https://github.com/barrust/pyspellchecker">pyspellchecker</a> – Built-in word frequency spellcheck'))
        pyspell_link.setOpenExternalLinks(True)
        links_layout.addWidget(pyspell_link)
        
        spylls_link = QLabel(self.tr('<a href="https://github.com/zverok/spylls">spylls</a> – Pure Python Hunspell implementation'))
        spylls_link.setOpenExternalLinks(True)
        links_layout.addWidget(spylls_link)
        
        hunspell_link = QLabel(self.tr('<a href="http://hunspell.github.io/">Hunspell</a> – Original C/C++ spellcheck library'))
        hunspell_link.setOpenExternalLinks(True)
        links_layout.addWidget(hunspell_link)
        
        right_col.addWidget(links_group)
        
        right_col.addStretch()
        columns.addLayout(right_col)
        
        main_layout.addLayout(columns, stretch=1)
        
        # ═══════════════ OK Button ═══════════════
        button_layout = QHBoxLayout()
        button_layout.addStretch()
        ok_btn = QPushButton(self.tr("OK"))
        ok_btn.clicked.connect(dialog.accept)
        ok_btn.setDefault(True)
        button_layout.addWidget(ok_btn)
        main_layout.addLayout(button_layout)
        
        dialog.exec()
