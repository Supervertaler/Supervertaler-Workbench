"""Translation memories: the TMs tab and TM Manager, creating, importing
(TMX, SDLTM) and exporting TMs, saving segments to TMs, TM search and
concordance, and inserting TM matches.

Part of the main window (SupervertalerQt), moved here unchanged. The
methods run as the main window's own: `self` is the SupervertalerQt.
"""

import os
import re
import threading
import time
import traceback
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)
from datetime import datetime
from modules import file_dialog_helper as fdh
from modules.colour_checkboxes import BlueCheckmarkCheckBox
from modules.grid_editors import EditableGridTextEditor
from modules.help_system import Topics as HelpTopics, set_topic as set_help_topic
from modules.shortcut_display import format_shortcut_for_display
from modules.statuses import STATUSES
from modules.styled_widgets import CheckmarkCheckBox, CheckmarkRadioButton, TealCheckmarkCheckBox
from modules.superlookup_tab import _NumericTableWidgetItem
from modules.tag_text import has_formatting_tags, strip_formatting_tags, strip_outer_wrapping_tags
from pathlib import Path


class TranslationMemoriesMixin:
    def open_tmx_editor_window(self):
        """Open the TMX Editor in a standalone window.

        Reuses any existing window if one is already open instead of stacking
        multiple instances on every menu click.
        """
        from PyQt6.QtCore import Qt
        from modules.tmx_editor_qt import TmxEditorUIQt

        existing = getattr(self, '_tmx_editor_window', None)
        if existing is not None:
            try:
                existing.show()
                existing.raise_()
                existing.activateWindow()
                return
            except RuntimeError:
                # Window was destroyed without our destroyed-signal callback
                # firing (rare). Fall through and create a new one.
                self._tmx_editor_window = None

        window = QWidget()
        window.setWindowFlags(Qt.WindowType.Window)
        window.setWindowTitle(self.tr("📝 TMX Editor – Supervertaler"))
        window.resize(1200, 800)

        layout = QVBoxLayout(window)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(5)

        header = QLabel(self.tr("📝 TMX Editor"))
        header.setStyleSheet("font-size: 16pt; font-weight: bold; color: #1976D2;")
        layout.addWidget(header, 0)

        description = QLabel(
            "Edit translation memory files directly - inspired by Heartsome TMX Editor.\n"
            "Open, edit, filter, and manage your TMX translation memories."
        )
        description.setWordWrap(True)
        description.setStyleSheet(
            "color: #666; padding: 5px; background-color: #E3F2FD; border-radius: 3px;"
        )
        layout.addWidget(description, 0)

        # Pass the shared database manager so the window can access TMs.
        tmx_editor = TmxEditorUIQt(parent=window, standalone=False, db_manager=self.db_manager)
        tmx_editor.translator_name = self.get_translator_name()
        layout.addWidget(tmx_editor, 1)
        self.tmx_editor_embedded = tmx_editor

        self._tmx_editor_window = window
        # Drop the reference when the window closes so a subsequent click on
        # Tools → TMX Editor opens a fresh instance instead of trying to
        # reuse a deleted Qt object.
        window.destroyed.connect(lambda *a: setattr(self, '_tmx_editor_window', None))
        window.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        window.show()

    # ── Quick Count (project-free statistics) ─────────────────────────────
    def _all_tm_choices(self):
        """[{tm_id, name, entry_count}, …] for every TM in the database."""
        try:
            return [
                {'tm_id': tm['tm_id'], 'name': tm.get('name') or tm['tm_id'],
                 'entry_count': tm.get('entry_count', 0)}
                for tm in self.tm_metadata_mgr.get_all_tms()
            ]
        except Exception:
            try:
                return [
                    {'tm_id': tm['tm_id'], 'name': tm.get('name') or tm['tm_id'],
                     'entry_count': tm.get('entry_count', 0)}
                    for tm in self.db_manager.get_all_tms()
                ]
            except Exception:
                return []

    def show_concordance_search(self, initial_query: str = None):
        """
        Show concordance search (Ctrl+K) - now uses Superlookup instead of separate dialog.
        
        Superlookup provides concordance search plus:
        - Termbase search
        - Machine Translation
        - Web Resources
        
        The view can be toggled between Horizontal (table) and Vertical (list) modes.
        """
        try:
            # Get selected text if available and no initial query
            if not initial_query:
                # First check the currently focused widget (most reliable)
                focus_widget = QApplication.focusWidget()
                if focus_widget and hasattr(focus_widget, 'textCursor'):
                    cursor = focus_widget.textCursor()
                    if cursor.hasSelection():
                        initial_query = cursor.selectedText()
                        self.log(f"[Concordance] Got selection from focus widget: '{initial_query[:30]}...'")
                
                # Fallback: check source/target cells directly
                if not initial_query and hasattr(self, 'table') and self.table:
                    current_row = self.table.currentRow()
                    if current_row >= 0:
                        # Try source column first (column 2)
                        source_widget = self.table.cellWidget(current_row, 2)
                        if source_widget and hasattr(source_widget, 'textCursor'):
                            cursor = source_widget.textCursor()
                            if cursor.hasSelection():
                                initial_query = cursor.selectedText()
                        
                        # If no selection in source, try target column (column 3)
                        if not initial_query:
                            target_widget = self.table.cellWidget(current_row, 3)
                            if target_widget and hasattr(target_widget, 'textCursor'):
                                cursor = target_widget.textCursor()
                                if cursor.hasSelection():
                                    initial_query = cursor.selectedText()
            
            # Get project language pair
            source_lang = getattr(self, 'source_language', None)
            target_lang = getattr(self, 'target_language', None)
            
            # Log for debugging
            if initial_query:
                self.log(f"[Concordance] Opening Superlookup with query: '{initial_query[:50]}...' (lang: {source_lang} → {target_lang})")
            else:
                self.log(f"[Concordance] Opening Superlookup (no selection)")
            
            # Open Superlookup in Sidekick, with the query + language pair if any
            self._go_to_superlookup(
                query=initial_query,
                source_lang=source_lang,
                target_lang=target_lang,
            )

            # No-query path: focus the input, reset language dropdowns to "Any",
            # and switch to vertical view. Targets the in-Workbench
            # SuperLookup top tab (v1.10.10 cleanup; pre-v1.10.4 this
            # walked through Sidekick's _superlookup_widget).
            if not initial_query:
                widget = getattr(self, '_superlookup_top_widget', None)
                if widget is not None:
                    if hasattr(widget, 'source_text'):
                        widget.source_text.setFocus()
                    if hasattr(widget, 'lang_from_combo'):
                        widget.lang_from_combo.setCurrentIndex(0)
                    if hasattr(widget, 'lang_to_combo'):
                        widget.lang_to_combo.setCurrentIndex(0)
                    if hasattr(widget, 'tm_view_vertical_radio'):
                        widget.tm_view_vertical_radio.setChecked(True)
                        
        except Exception as e:
            self.log(f"Error opening concordance search: {e}")
            import traceback
            traceback.print_exc()
            QMessageBox.critical(self, "Error", f"Failed to open concordance search:\n{str(e)}")

    def show_tm_manager_tab(self, tab_index: int = 0, initial_concordance_query: str = None):
        """Show TM Manager dialog opened to specific tab.

        If initial_concordance_query is provided, the Concordance tab is
        selected and the query is auto-run – used by Superlookup's right-click
        'Open in TM browser' to jump straight to the matching segment.
        """
        from modules.tm_manager_qt import TMManagerDialog

        try:
            dialog = TMManagerDialog(self, self.db_manager, self.log,
                                     initial_concordance_query=initial_concordance_query)
            if not initial_concordance_query:
                dialog.tabs.setCurrentIndex(tab_index)
            dialog.exec()
        except Exception as e:
            self.log(f"Error opening TM Manager: {e}")
            QMessageBox.critical(self, "Error", f"Failed to open TM Manager:\n{str(e)}")

    def _show_tm_maintenance_dialog(self):
        """Open a focused TM Maintenance dialog.

        Reuses TMManagerDialog and hides every tab except Maintenance, so the
        user lands on the cleanup actions with no surrounding noise.
        """
        from modules.tm_manager_qt import TMManagerDialog
        try:
            dialog = TMManagerDialog(self, self.db_manager, self.log)
            maintenance_index = -1
            for i in range(dialog.tabs.count()):
                if "Maintenance" in dialog.tabs.tabText(i):
                    maintenance_index = i
                else:
                    dialog.tabs.setTabVisible(i, False)
            if maintenance_index >= 0:
                dialog.tabs.setCurrentIndex(maintenance_index)
            dialog.setWindowTitle(self.tr("TM Maintenance"))
            dialog.exec()
        except Exception as e:
            self.log(f"Error opening TM Maintenance: {e}")
            QMessageBox.critical(self, "Error", f"Failed to open TM Maintenance:\n{str(e)}")

    def _copy_tm_stats_to_clipboard(self, tm_metadata_mgr):
        """Build a markdown TM statistics report and copy it to the clipboard."""
        try:
            from datetime import datetime
            from modules.tmx_generator import normalize_lang_variant

            tms = tm_metadata_mgr.get_all_tms()
            total_entries = sum((tm.get('entry_count') or 0) for tm in tms)
            tm_count = len(tms)

            cursor = self.db_manager.cursor
            cursor.execute(
                "SELECT AVG(LENGTH(source_text)), AVG(LENGTH(target_text)) "
                "FROM translation_units"
            )
            row = cursor.fetchone()
            avg_source = row[0] if row and row[0] is not None else 0
            avg_target = row[1] if row and row[1] is not None else 0

            now = datetime.now().strftime("%Y-%m-%d %H:%M")
            lines = [
                "# Translation Memory Statistics",
                "",
                f"_Generated {now}_",
                "",
                f"- **Total translation units:** {total_entries:,}",
                f"- **Number of TMs:** {tm_count}",
                f"- **Average source length:** {avg_source:.1f} characters",
                f"- **Average target length:** {avg_target:.1f} characters",
                "",
                "## Breakdown by TM",
                "",
                "| TM | Languages | Entries | Share | Last modified |",
                "| --- | --- | ---: | ---: | --- |",
            ]
            sorted_tms = sorted(
                tms, key=lambda t: (t.get('entry_count') or 0), reverse=True
            )
            for tm in sorted_tms:
                entries = tm.get('entry_count') or 0
                pct = (entries / total_entries * 100) if total_entries > 0 else 0
                src = normalize_lang_variant(tm.get('source_lang')) if tm.get('source_lang') else '?'
                tgt = normalize_lang_variant(tm.get('target_lang')) if tm.get('target_lang') else '?'
                modified = tm.get('modified_date') or ''
                if isinstance(modified, str) and len(modified) > 16:
                    modified = modified[:16].replace('T', ' ')
                name = (tm.get('name') or tm.get('tm_id') or '?').replace('|', '\\|')
                lines.append(
                    f"| {name} | {src} → {tgt} | {entries:,} | {pct:.1f}% | {modified} |"
                )
            markdown = "\n".join(lines) + "\n"

            QApplication.clipboard().setText(markdown)
            self.log(
                f"📊 TM statistics copied to clipboard "
                f"({total_entries:,} TUs across {tm_count} TMs)"
            )
            QMessageBox.information(
                self,
                "Stats copied",
                f"Translation memory statistics copied to clipboard "
                f"({total_entries:,} TUs across {tm_count} TMs).",
            )
        except Exception as e:
            self.log(f"Error copying TM stats: {e}")
            QMessageBox.critical(self, "Error", f"Failed to copy TM stats:\n{str(e)}")

    def create_translation_memories_tab(self):
        """Create the Translation Memories tab with nested sub-tabs"""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        
        # Check if database is available
        if not (hasattr(self, 'db_manager') and self.db_manager):
            placeholder = QLabel(self.tr("Translation Memories Manager\n\nDatabase not initialized."))
            placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
            placeholder.setStyleSheet("color: #888; font-size: 12px;")
            layout.addWidget(placeholder, stretch=1)
            return tab
        
        # Import TM metadata manager
        from modules.tm_metadata_manager import TMMetadataManager
        tm_metadata_mgr = TMMetadataManager(self.db_manager, self.log)
        self.tm_metadata_mgr = tm_metadata_mgr  # Store for later use
        
        # Create nested tab widget for TM functionality
        tm_tabs = QTabWidget()
        tm_tabs.tabBar().setFocusPolicy(Qt.FocusPolicy.NoFocus)
        tm_tabs.tabBar().setDrawBase(False)
        tm_tabs.tabBar().setExpanding(False)
        tm_tabs.setStyleSheet("QTabBar::tab { outline: 0; } QTabBar::tab:focus { outline: none; } QTabBar::tab:selected { border-bottom: 1px solid #2196F3; background-color: rgba(33, 150, 243, 0.08); }")
        
        # Tab 1: TM List (Management) - manage multiple TMs
        tm_list_tab = self._create_tm_list_tab(tm_metadata_mgr)
        tm_tabs.addTab(tm_list_tab, "📋 TM List")
        
        # Tab 2: Browse All - browse ALL active TMs together
        from modules.tm_manager_qt import TMManagerDialog
        temp_manager = TMManagerDialog(self, self.db_manager, self.log)
        tm_tabs.addTab(temp_manager.browser_tab, "📖 Browse All")

        # Statistics (now a "Copy stats" button on the TM List tab – copies a
        # markdown report to the clipboard) and Maintenance (now a button that
        # opens a focused cleanup dialog) used to be sibling sub-tabs here.
        # Folded into TM List to reduce sub-tab noise.

        # Store reference to prevent garbage collection
        tab._tm_manager = temp_manager
        
        layout.addWidget(tm_tabs)
        
        return tab

    def _create_tm_list_tab(self, tm_metadata_mgr):
        """Create the TM List sub-tab with table and management buttons"""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(10, 10, 10, 10)
        
        # Header
        header = QLabel(self.tr("💾 TMs"))
        header.setStyleSheet("font-size: 14px; font-weight: bold; margin-bottom: 10px;")
        layout.addWidget(header)
        
        # Description
        desc = QLabel(self.tr("Manage TMs. Activate/deactivate TMs for current project. Import client TMX files as named TMs."))
        desc.setStyleSheet("color: #666; font-size: 11px; margin-bottom: 10px;")
        layout.addWidget(desc)

        # Live summary line – updated by refresh_tm_list() below.
        summary_label = QLabel("")
        summary_label.setStyleSheet("color: #555; font-size: 11px; margin-bottom: 6px;")
        layout.addWidget(summary_label)

        # Search bar
        search_layout = QHBoxLayout()
        search_box = QLineEdit()
        search_box.setPlaceholderText(self.tr("Search TMs..."))
        search_box.setMaximumWidth(300)
        search_layout.addWidget(search_box)
        search_layout.addStretch()
        layout.addLayout(search_layout)
        
        # Help message for first-time users
        help_msg = QLabel(
            "💡 <b>Translation Memories</b><br>"
            "• <b>Read</b> (green ✓): TM is used for matching segments<br>"
            "• <b>Write</b> (blue ✓): TM is updated with new translations<br>"
            "• <b>Bridge</b> (orange ✓): TM is visible to the Supervertaler for Trados plugin (Phase 2)<br>"
            "• <b>🔍 SuperLookup</b> (teal ✓): TM is searched by SuperLookup (independent of Read)<br>"
            "• <b>Typical Setup</b>: Main TM (Read + Write) + Reference TMs (Read only)"
        )
        help_msg.setWordWrap(True)
        help_msg.setStyleSheet("background-color: #e3f2fd; padding: 8px; border-radius: 4px; color: #1976d2;")
        layout.addWidget(help_msg)

        # Bulk action controls
        bulk_layout = QHBoxLayout()
        bulk_layout.addWidget(QLabel(self.tr("Quick Actions:")))

        read_header_checkbox = CheckmarkCheckBox(self.tr("Select All Read"))
        bulk_layout.addWidget(read_header_checkbox)

        # v1.10.173: explicit "Clear All" button — see Termbases tab
        # equivalent for the rationale. (Late binding: toggle_all_read
        # is defined further down in this enclosing scope; by the time
        # the user clicks the button it'll resolve.)
        tm_clear_all_read_btn = QPushButton(self.tr("Clear All Read"))
        tm_clear_all_read_btn.setToolTip(self.tr("Uncheck the Read flag on every TM"))
        tm_clear_all_read_btn.clicked.connect(lambda: toggle_all_read(False))
        bulk_layout.addWidget(tm_clear_all_read_btn)

        write_header_checkbox = BlueCheckmarkCheckBox("Select All Write")
        bulk_layout.addWidget(write_header_checkbox)

        tm_clear_all_write_btn = QPushButton(self.tr("Clear All Write"))
        tm_clear_all_write_btn.setToolTip(self.tr("Uncheck the Write flag on every TM"))
        tm_clear_all_write_btn.clicked.connect(lambda: toggle_all_write(False))
        bulk_layout.addWidget(tm_clear_all_write_btn)

        # v1.10.212: Bridge bulk toggles. Mirrors the Read/Write button
        # pair – "Select All Bridge" turns the flag on for every TM,
        # "Clear All Bridge" off. Late-binding via the toggle_all_bridge
        # closure defined further down with toggle_all_read/write.
        bridge_header_checkbox = CheckmarkCheckBox(self.tr("Select All Bridge"))
        bridge_header_checkbox.setStyleSheet("QCheckBox::indicator:checked { background-color: #FF8C00; }")
        bulk_layout.addWidget(bridge_header_checkbox)

        tm_clear_all_bridge_btn = QPushButton(self.tr("Clear All Bridge"))
        tm_clear_all_bridge_btn.setToolTip(self.tr("Uncheck the Bridge flag on every TM (hide all TMs from the Trados plugin)"))
        tm_clear_all_bridge_btn.clicked.connect(lambda: toggle_all_bridge(False))
        bulk_layout.addWidget(tm_clear_all_bridge_btn)

        # v1.10.247: SuperLookup bulk toggles. "Select All SuperLookup"
        # includes every TM in SuperLookup, "Clear All SuperLookup" excludes
        # them. Late-binding via the toggle_all_superlookup closure defined
        # further down alongside toggle_all_read/write/bridge.
        superlookup_header_checkbox = TealCheckmarkCheckBox(self.tr("Select All SuperLookup"))
        bulk_layout.addWidget(superlookup_header_checkbox)

        tm_clear_all_superlookup_btn = QPushButton(self.tr("Clear All SuperLookup"))
        tm_clear_all_superlookup_btn.setToolTip(self.tr("Uncheck the SuperLookup flag on every TM (exclude all TMs from SuperLookup)"))
        tm_clear_all_superlookup_btn.clicked.connect(lambda: toggle_all_superlookup(False))
        bulk_layout.addWidget(tm_clear_all_superlookup_btn)

        bulk_layout.addStretch()
        layout.addLayout(bulk_layout)

        # TM list with table
        tm_table = QTableWidget()
        # v1.10.247: 9 columns now – SuperLookup added at index 6, after
        # Bridge, so the four per-TM toggles (Read/Write/Bridge/SuperLookup)
        # cluster together. Last Modified moves to 7, Description to 8.
        tm_table.setColumnCount(9)
        tm_table.setHorizontalHeaderLabels(["TM Name", "Languages", "Entries", "Read", "Write", "Bridge", "🔍 SuperLookup", "Last Modified", "Description"])
        tm_table.horizontalHeader().setStretchLastSection(True)
        tm_table.setColumnWidth(0, 250)
        tm_table.setColumnWidth(1, 120)
        tm_table.setColumnWidth(2, 80)
        tm_table.setColumnWidth(3, 60)
        tm_table.setColumnWidth(4, 60)
        tm_table.setColumnWidth(5, 60)   # Bridge
        tm_table.setColumnWidth(6, 100)  # SuperLookup
        tm_table.setColumnWidth(7, 150)  # Last Modified
        # v1.10.169: click-header-to-sort on the data columns (TM Name,
        # Languages, Entries, Last Modified, Description). The Read /
        # Write columns are checkbox widgets and won't sort meaningfully
        # — clicking those headers is a visual no-op. Sorting is toggled
        # off + back on around refresh_tm_list's rebuild loop.
        tm_table.setSortingEnabled(True)
        
        # Get current project (for lambda closures below)
        current_project = self.current_project if hasattr(self, 'current_project') else None
        project_id = current_project.id if (current_project and hasattr(current_project, 'id')) else None
        
        # Connect header checkboxes to toggle all
        # v1.10.170: bulk DB write + single refresh, matching the
        # Termbases-tab pattern. The TM per-row handler is gentler than
        # the Termbase one (no per-click table rebuild), so the crash is
        # much less likely here, but doing 46 invalidate_translation_cache
        # calls in a row was wasteful and could still spike CPU.
        def toggle_all_read(checked):
            curr_proj = self.current_project if hasattr(self, 'current_project') else None
            curr_proj_id = curr_proj.id if (curr_proj and hasattr(curr_proj, 'id')) else 0
            try:
                for tm in (tm_metadata_mgr.get_all_tms() or []):
                    tm_id = tm.get('id')
                    if tm_id is None:
                        continue
                    if checked:
                        tm_metadata_mgr.activate_tm(tm_id, curr_proj_id)
                    else:
                        tm_metadata_mgr.deactivate_tm(tm_id, curr_proj_id)
            except Exception as e:
                self.log(f"⚠️ Bulk TM Read toggle failed mid-way: {e}")
            try:
                self.invalidate_translation_cache(smart_invalidation=False)
            except Exception:
                pass
            refresh_tm_list()

        def toggle_all_write(checked):
            try:
                for tm in (tm_metadata_mgr.get_all_tms() or []):
                    tm_id = tm.get('id')
                    if tm_id is None:
                        continue
                    # checked = writable, read_only is the opposite.
                    if hasattr(tm_metadata_mgr, 'set_tm_read_only'):
                        tm_metadata_mgr.set_tm_read_only(tm_id, not checked)
            except Exception as e:
                self.log(f"⚠️ Bulk TM Write toggle failed mid-way: {e}")
            refresh_tm_list()

        # v1.10.212: bulk Bridge toggle (Trados visibility for the whole list).
        def toggle_all_bridge(checked):
            try:
                for tm in (tm_metadata_mgr.get_all_tms() or []):
                    tm_id = tm.get('id')
                    if tm_id is None:
                        continue
                    if hasattr(tm_metadata_mgr, 'set_bridged_to_trados'):
                        tm_metadata_mgr.set_bridged_to_trados(tm_id, bool(checked))
            except Exception as e:
                self.log(f"⚠️ Bulk TM Bridge toggle failed mid-way: {e}")
            refresh_tm_list()

        # v1.10.247: bulk SuperLookup toggle (include/exclude every TM
        # from SuperLookup in one click). Single DB pass + one refresh,
        # mirroring the Bridge bulk toggle.
        def toggle_all_superlookup(checked):
            try:
                for tm in (tm_metadata_mgr.get_all_tms() or []):
                    tm_id = tm.get('id')
                    if tm_id is None:
                        continue
                    if hasattr(tm_metadata_mgr, 'set_superlookup_enabled'):
                        tm_metadata_mgr.set_superlookup_enabled(tm_id, bool(checked))
            except Exception as e:
                self.log(f"⚠️ Bulk TM SuperLookup toggle failed mid-way: {e}")
            refresh_tm_list()

        read_header_checkbox.toggled.connect(toggle_all_read)
        write_header_checkbox.toggled.connect(toggle_all_write)
        bridge_header_checkbox.toggled.connect(toggle_all_bridge)
        superlookup_header_checkbox.toggled.connect(toggle_all_superlookup)
        
        # Populate TM list
        def refresh_tm_list():
            # Get current project dynamically (not captured in closure!)
            # Use GLOBAL_PROJECT_ID (0) when no project is loaded for Superlookup support
            current_proj = self.current_project if hasattr(self, 'current_project') else None
            refresh_project_id = current_proj.id if (current_proj and hasattr(current_proj, 'id')) else 0  # 0 = global
            
            tms = tm_metadata_mgr.get_all_tms()
            # v1.10.169: see termbase_table refresh for the same disable/
            # restore pattern. Sorting during a populating loop reshuffles
            # rows mid-build and corrupts the row index closures captured
            # by the per-row checkbox handlers.
            _prev_tm_sorting = tm_table.isSortingEnabled()
            tm_table.setSortingEnabled(False)
            tm_table.setRowCount(len(tms))
            
            for row, tm in enumerate(tms):
                # v1.10.169: read the actual DB state regardless of whether
                # a project is loaded. See the matching comment in the
                # Termbases-tab refresh for the rationale (in short: the
                # "no project = force unchecked" override silently wiped
                # user toggles every refresh, and broke the no-project
                # SuperLookup workflow).
                is_readable = tm_metadata_mgr.is_tm_active(tm['id'], refresh_project_id)
                is_writable = not tm.get('read_only', True)  # Default to True (read-only) if not set
                
                # TM Name (bold if readable)
                name_item = QTableWidgetItem(tm['name'])
                if is_readable:
                    font = name_item.font()
                    font.setBold(True)
                    name_item.setFont(font)
                tm_table.setItem(row, 0, name_item)
                
                # Languages (normalized format: nl-NL, en-US, etc.)
                from modules.tmx_generator import normalize_lang_variant
                src_lang = normalize_lang_variant(tm['source_lang']) if tm['source_lang'] else '?'
                tgt_lang = normalize_lang_variant(tm['target_lang']) if tm['target_lang'] else '?'
                langs = f"{src_lang} → {tgt_lang}"
                tm_table.setItem(row, 1, QTableWidgetItem(langs))
                
                # Entry count
                # v1.10.173: numeric-sort variant so column-header sort
                # orders by entry count rather than lexically (so "100"
                # doesn't end up before "2").
                tm_table.setItem(row, 2, _NumericTableWidgetItem(str(tm['entry_count'])))
                
                # Read checkbox (green checkmark)
                read_checkbox = CheckmarkCheckBox()
                read_checkbox.setChecked(is_readable)
                read_checkbox.setToolTip(self.tr("Read: TM is used for matching segments"))
                
                def on_read_toggle(checked, tm_id=tm['id'], row_idx=row):
                    # Get current project ID dynamically
                    # Use 0 (global) when no project is loaded - allows Superlookup to work
                    curr_proj = self.current_project if hasattr(self, 'current_project') else None
                    curr_proj_id = curr_proj.id if (curr_proj and hasattr(curr_proj, 'id')) else 0  # 0 = global
                    
                    # Activate/deactivate for reading
                    if checked:
                        success = tm_metadata_mgr.activate_tm(tm_id, curr_proj_id)
                    else:
                        success = tm_metadata_mgr.deactivate_tm(tm_id, curr_proj_id)
                    
                    if success:
                        status = "readable" if checked else "not readable"
                        self.log(f"✅ TM {tm_id} set to {status}")
                        # Update name to be bold if readable
                        name_item = tm_table.item(row_idx, 0)
                        if name_item:
                            font = name_item.font()
                            font.setBold(checked)
                            name_item.setFont(font)
                        
                        # Invalidate translation cache so matches are refreshed with new TM settings
                        self.invalidate_translation_cache(smart_invalidation=False)
                    else:
                        self.log(f"❌ Failed to toggle TM {tm_id}")
                        # Revert checkbox on failure
                        sender_checkbox = tm_table.cellWidget(row_idx, 3)
                        if sender_checkbox:
                            sender_checkbox.blockSignals(True)
                            sender_checkbox.setChecked(not checked)
                            sender_checkbox.blockSignals(False)
                
                read_checkbox.toggled.connect(on_read_toggle)
                tm_table.setCellWidget(row, 3, read_checkbox)
                
                # Write checkbox (blue checkmark)
                write_checkbox = BlueCheckmarkCheckBox()
                write_checkbox.setChecked(is_writable)
                write_checkbox.setToolTip(self.tr("Write: TM is updated with new translations"))
                
                def on_write_toggle(checked, tm_id=tm['id'], row_idx=row):
                    # Invert logic: checked = writable, so set read_only to NOT checked
                    success = tm_metadata_mgr.set_read_only(tm_id, not checked)
                    if success:
                        status = "writable" if checked else "read-only"
                        self.log(f"✅ TM {tm_id} set to {status}")
                    else:
                        # Revert on failure
                        sender = tm_table.cellWidget(row_idx, 4)
                        if sender:
                            sender.blockSignals(True)
                            sender.setChecked(not checked)
                            sender.blockSignals(False)
                
                write_checkbox.toggled.connect(on_write_toggle)
                tm_table.setCellWidget(row, 4, write_checkbox)

                # v1.10.212: Bridge checkbox (Trados visibility).
                # Orange-checkmark variant to distinguish from Read (green)
                # and Write (blue) at a glance. Default off – freelancers
                # with multiple-client TM libraries opt in per TM.
                is_bridged = bool(tm.get('bridged_to_trados', False))
                bridge_checkbox = CheckmarkCheckBox()
                bridge_checkbox.setStyleSheet(
                    "QCheckBox::indicator:checked { background-color: #FF8C00; }"
                )
                bridge_checkbox.setChecked(is_bridged)
                bridge_checkbox.setToolTip(
                    "Bridge: TM is visible to the Supervertaler for Trados plugin "
                    "(Trados-side wiring lands in a follow-up release)"
                )

                def on_bridge_toggle(checked, tm_id=tm['id'], row_idx=row):
                    if hasattr(tm_metadata_mgr, 'set_bridged_to_trados'):
                        success = tm_metadata_mgr.set_bridged_to_trados(tm_id, bool(checked))
                        if success:
                            self.log(
                                f"✅ TM {tm_id} {'now visible to' if checked else 'hidden from'} Trados plugin"
                            )
                        else:
                            # Revert on failure
                            sender = tm_table.cellWidget(row_idx, 5)
                            if sender:
                                sender.blockSignals(True)
                                sender.setChecked(not checked)
                                sender.blockSignals(False)

                bridge_checkbox.toggled.connect(on_bridge_toggle)
                tm_table.setCellWidget(row, 5, bridge_checkbox)

                # v1.10.247: SuperLookup checkbox (teal). Independent of Read
                # — the single switch that decides whether SuperLookup searches
                # this TM. Default on (included) for every existing TM.
                is_superlookup = bool(tm.get('superlookup_enabled', True))
                superlookup_checkbox = TealCheckmarkCheckBox()
                superlookup_checkbox.setChecked(is_superlookup)
                superlookup_checkbox.setToolTip(self.tr(
                    "SuperLookup: TM is searched by SuperLookup. Independent of "
                    "Read — leave Read off for a project and still search this TM."
                ))

                def on_superlookup_toggle(checked, tm_id=tm['id'], row_idx=row):
                    if hasattr(tm_metadata_mgr, 'set_superlookup_enabled'):
                        success = tm_metadata_mgr.set_superlookup_enabled(tm_id, bool(checked))
                        if success:
                            self.log(
                                f"✅ TM {tm_id} {'included in' if checked else 'excluded from'} SuperLookup"
                            )
                        else:
                            # Revert on failure
                            sender = tm_table.cellWidget(row_idx, 6)
                            if sender:
                                sender.blockSignals(True)
                                sender.setChecked(not checked)
                                sender.blockSignals(False)

                superlookup_checkbox.toggled.connect(on_superlookup_toggle)
                tm_table.setCellWidget(row, 6, superlookup_checkbox)

                # Last modified
                modified = tm['modified_date'] or tm['created_date'] or ''
                if modified:
                    # Format datetime nicely
                    try:
                        from datetime import datetime
                        dt = datetime.fromisoformat(modified)
                        modified = dt.strftime("%Y-%m-%d %H:%M")
                    except:
                        pass
                tm_table.setItem(row, 7, QTableWidgetItem(modified))

                # Description
                desc_text = tm['description'] or ''
                tm_table.setItem(row, 8, QTableWidgetItem(desc_text))

            # Update header checkbox states based on current selection
            read_header_checkbox.blockSignals(True)
            write_header_checkbox.blockSignals(True)
            bridge_header_checkbox.blockSignals(True)
            superlookup_header_checkbox.blockSignals(True)
            all_read_checked = all(tm_table.cellWidget(r, 3).isChecked() if tm_table.cellWidget(r, 3) else False for r in range(tm_table.rowCount())) if tm_table.rowCount() > 0 else False
            all_write_checked = all(tm_table.cellWidget(r, 4).isChecked() if tm_table.cellWidget(r, 4) else False for r in range(tm_table.rowCount())) if tm_table.rowCount() > 0 else False
            all_bridge_checked = all(tm_table.cellWidget(r, 5).isChecked() if tm_table.cellWidget(r, 5) else False for r in range(tm_table.rowCount())) if tm_table.rowCount() > 0 else False
            all_superlookup_checked = all(tm_table.cellWidget(r, 6).isChecked() if tm_table.cellWidget(r, 6) else False for r in range(tm_table.rowCount())) if tm_table.rowCount() > 0 else False
            read_header_checkbox.setChecked(all_read_checked)
            write_header_checkbox.setChecked(all_write_checked)
            bridge_header_checkbox.setChecked(all_bridge_checked)
            superlookup_header_checkbox.setChecked(all_superlookup_checked)
            read_header_checkbox.blockSignals(False)
            write_header_checkbox.blockSignals(False)
            bridge_header_checkbox.blockSignals(False)
            superlookup_header_checkbox.blockSignals(False)

            # Summary line: total TUs across N TMs
            total_tus = sum((tm.get('entry_count') or 0) for tm in tms)
            summary_label.setText(
                f"📊 {total_tus:,} translation units across {len(tms)} TM"
                f"{'s' if len(tms) != 1 else ''}"
            )

            # v1.10.169: re-enable sorting now that the rebuild is done.
            tm_table.setSortingEnabled(_prev_tm_sorting)

        # Store callback as instance attribute so load_project can refresh UI after restoration
        self.tm_tab_refresh_callback = refresh_tm_list
        
        refresh_tm_list()
        layout.addWidget(tm_table, stretch=1)
        
        # Button bar
        button_layout = QHBoxLayout()
        
        create_btn = QPushButton(self.tr("+ Create New TM"))
        # Get project_id dynamically - use 0 (global) when no project is loaded
        create_btn.clicked.connect(lambda: self._show_create_tm_dialog(
            tm_metadata_mgr, refresh_tm_list,
            self.current_project.id if (hasattr(self, 'current_project') and self.current_project and hasattr(self.current_project, 'id')) else 0
        ))
        button_layout.addWidget(create_btn)
        
        import_btn = QPushButton(self.tr("📥 Import TMX"))
        import_btn.setToolTip(self.tr("Import TMX file as a new TM or add to existing TM"))
        import_btn.clicked.connect(lambda: self._import_tmx_as_tm(tm_metadata_mgr, tm_table, refresh_tm_list))
        button_layout.addWidget(import_btn)

        attach_sdltm_btn = QPushButton(self.tr("🔗 Attach Trados TM"))
        attach_sdltm_btn.setToolTip(
            "Mirror a Trados Studio .sdltm file as a read-only TM. "
            "Inline tags are preserved as Supervertaler <N> markers."
        )
        attach_sdltm_btn.clicked.connect(lambda: self._attach_sdltm_as_tm(tm_metadata_mgr, refresh_tm_list))
        set_help_topic(attach_sdltm_btn, HelpTopics.TM_TRADOS_SDLTM)
        button_layout.addWidget(attach_sdltm_btn)
        
        export_btn = QPushButton(self.tr("📤 Export TM"))
        export_btn.setToolTip(self.tr("Export selected TM to TMX file"))
        export_btn.clicked.connect(lambda: self._export_tm_to_tmx(tm_metadata_mgr, tm_table))
        button_layout.addWidget(export_btn)
        
        edit_btn = QPushButton(self.tr("✏️ Edit/Maintain TM"))
        edit_btn.setToolTip(self.tr("Open editor for selected TM (Browse, Import/Export, Maintenance)"))
        edit_btn.clicked.connect(lambda: self._show_tm_editor_dialog(tm_metadata_mgr, tm_table))
        button_layout.addWidget(edit_btn)
        
        delete_btn = QPushButton(self.tr("🗑️ Delete TM"))
        delete_btn.clicked.connect(lambda: self._delete_tm(tm_metadata_mgr, tm_table, refresh_tm_list))
        button_layout.addWidget(delete_btn)

        button_layout.addStretch()

        maintenance_btn = QPushButton(self.tr("🧹 Maintenance…"))
        maintenance_btn.setToolTip(
            "Open TM maintenance dialog: remove identical source/target pairs, "
            "remove duplicate sources (keep newest)."
        )
        maintenance_btn.clicked.connect(self._show_tm_maintenance_dialog)
        button_layout.addWidget(maintenance_btn)

        copy_stats_btn = QPushButton(self.tr("📊 Copy stats"))
        copy_stats_btn.setToolTip(
            "Copy a markdown TM statistics report to the clipboard "
            "(total TUs, per-TM breakdown, averages)."
        )
        copy_stats_btn.clicked.connect(
            lambda: self._copy_tm_stats_to_clipboard(tm_metadata_mgr)
        )
        button_layout.addWidget(copy_stats_btn)

        layout.addLayout(button_layout)

        return tab

    def _show_tm_context_menu(self, tm_table, tm_metadata_mgr, refresh_callback, pos):
        """Show context menu for TM table"""
        current_row = tm_table.rowAt(pos.y())
        if current_row < 0:
            return
        
        # Get TM info from the row (column 0 = TM Name)
        name_item = tm_table.item(current_row, 0)
        if not name_item:
            return
        
        tm_name = name_item.text()
        
        # Find the TM in the list
        all_tms = tm_metadata_mgr.get_all_tms()
        tm = None
        for t in all_tms:
            if t['name'] == tm_name:
                tm = t
                break
        
        if not tm:
            return
        
        menu = QMenu(tm_table)
        
        # Check if this TM is already the project TM
        is_project_tm = tm.get('is_project_tm', False)
        
        # Get current project
        current_proj = self.current_project if hasattr(self, 'current_project') else None
        project_id = current_proj.id if (current_proj and hasattr(current_proj, 'id')) else None
        
        if project_id:
            if not is_project_tm:
                set_project_action = menu.addAction("📌 Set as Project TM")
                set_project_action.triggered.connect(
                    lambda: self._set_tm_as_project(tm['id'], project_id, tm_metadata_mgr, refresh_callback)
                )
            else:
                unset_project_action = menu.addAction("📌 Unset as Project TM")
                unset_project_action.triggered.connect(
                    lambda: self._unset_tm_as_project(tm['id'], tm_metadata_mgr, refresh_callback)
                )
        else:
            no_project_action = menu.addAction("⚠️ Load a project first")
            no_project_action.setEnabled(False)
        
        menu.addSeparator()
        
        # Read-only toggle
        is_readonly = tm.get('read_only', False)
        readonly_text = "✏️ Set as Writable" if is_readonly else "🔒 Set as Read-Only"
        readonly_action = menu.addAction(readonly_text)
        readonly_action.triggered.connect(
            lambda: self._toggle_tm_readonly(tm['id'], not is_readonly, tm_metadata_mgr, refresh_callback)
        )
        
        menu.exec(tm_table.viewport().mapToGlobal(pos))

    def _set_tm_as_project(self, tm_id, project_id, tm_metadata_mgr, refresh_callback):
        """Set a TM as the project TM"""
        success = tm_metadata_mgr.set_as_project_tm(tm_id, project_id)
        if success:
            self.log(f"✅ Set TM as Project TM")
            refresh_callback()
        else:
            QMessageBox.warning(self, "Error", "Failed to set TM as Project TM")

    def _unset_tm_as_project(self, tm_id, tm_metadata_mgr, refresh_callback):
        """Unset a TM as project TM"""
        success = tm_metadata_mgr.unset_project_tm(tm_id)
        if success:
            self.log(f"✅ Unset Project TM")
            refresh_callback()
        else:
            QMessageBox.warning(self, "Error", "Failed to unset Project TM")

    def _toggle_tm_readonly(self, tm_id, readonly, tm_metadata_mgr, refresh_callback):
        """Toggle read-only status of a TM"""
        success = tm_metadata_mgr.set_read_only(tm_id, readonly)
        if success:
            status = "read-only" if readonly else "writable"
            self.log(f"✅ Set TM as {status}")
            refresh_callback()
        else:
            QMessageBox.warning(self, "Error", f"Failed to set TM as {'read-only' if readonly else 'writable'}")

    def import_tmx_file(self):
        """Import TMX file into translation memory with language variant handling"""
        try:
            file_path, _ = fdh.get_open_file_name(
                self, 
                "Import TMX File", 
                "TMX Files (*.tmx);;All Files (*.*)"
            )
            
            if file_path and self.tm_database:
                # Get target languages
                source_lang = getattr(self.current_project, 'source_lang', 'en') if self.current_project else 'en'
                target_lang = getattr(self.current_project, 'target_lang', 'nl') if self.current_project else 'nl'
                
                # Detect TMX languages
                tmx_langs = self.tm_database.detect_tmx_languages(file_path)
                self.log(f"TMX languages detected: {tmx_langs}")
                
                # Check compatibility
                compat = self.tm_database.check_language_compatibility(tmx_langs, source_lang, target_lang)
                
                if not compat['compatible']:
                    QMessageBox.warning(
                        self, "Language Mismatch",
                        f"TMX file languages ({', '.join(tmx_langs)}) don't match project languages ({source_lang}, {target_lang}).\n\n"
                        "Cannot import this TMX file."
                    )
                    return
                
                # Handle variant mismatch
                if compat.get('variant_match'):
                    choice = self._show_language_variant_dialog(compat)
                    
                    if choice == 'cancel':
                        return
                    
                    # Show progress dialog
                    from PyQt6.QtWidgets import QProgressDialog
                    from PyQt6.QtCore import Qt
                    import time
                    
                    # Create progress dialog with cancel button
                    progress = QProgressDialog("Counting translation units...", "Cancel", 0, 100, self)
                    progress.setWindowTitle(self.tr("TMX Import"))
                    progress.setWindowModality(Qt.WindowModality.WindowModal)
                    progress.setMinimumDuration(0)
                    progress.setAutoClose(False)
                    progress.setAutoReset(False)
                    progress.setValue(0)
                    progress.show()
                    QApplication.processEvents()
                    
                    # Track if user cancelled
                    cancelled = False
                    start_time = time.time()
                    
                    def update_progress(current, total, message):
                        nonlocal cancelled
                        if progress.wasCanceled():
                            cancelled = True
                            return
                        
                        if total > 0:
                            percentage = int((current / total) * 100)
                            progress.setValue(percentage)
                            
                            # Calculate time stats
                            elapsed = time.time() - start_time
                            if current > 0:
                                rate = current / elapsed
                                remaining = (total - current) / rate if rate > 0 else 0
                                
                                # Format message with stats
                                time_info = f" (~{int(remaining/60)}m {int(remaining%60)}s remaining)" if remaining > 10 else ""
                                progress.setLabelText(f"{message}\nRate: {int(rate):,} entries/sec{time_info}")
                            else:
                                progress.setLabelText(message)
                        else:
                            progress.setLabelText(message)
                        
                        QApplication.processEvents()
                    
                    if choice == 'import_strip':
                        # Import with variant stripping
                        tm_id, count = self.tm_database.load_tmx_file(
                            file_path, source_lang, target_lang, 
                            tm_name=None, read_only=False, progress_callback=update_progress
                        )
                        progress.close()
                        
                        if not cancelled:
                            self.log(f"Imported {count} entries (variants stripped: {compat['tmx_source']}, {compat['tmx_target']} → {source_lang}, {target_lang})")
                            QMessageBox.information(
                                self, "Import Complete",
                                f"TMX imported successfully!\n\nEntries: {count:,}\n"
                                f"Mapped: {compat['tmx_source']}, {compat['tmx_target']} → {source_lang}, {target_lang}"
                            )
                        else:
                            self.log("TMX import cancelled by user")
                            QMessageBox.information(self, "Import Cancelled", f"Import stopped.\n\nPartial entries may have been imported: {count:,}")
                    elif choice == 'create_new':
                        # Create new TM with variant languages
                        tm_id, count = self.tm_database.load_tmx_file(
                            file_path, compat['tmx_source'], compat['tmx_target'],
                            tm_name=None, read_only=False, progress_callback=update_progress
                        )
                        progress.close()
                        
                        if not cancelled:
                            self.log(f"Created new TM with variants: {compat['tmx_source']}, {compat['tmx_target']}")
                            QMessageBox.information(
                                self, "Import Complete", 
                                f"New TM created!\n\nTM ID: {tm_id}\nEntries: {count:,}\n"
                                f"Languages: {compat['tmx_source']}, {compat['tmx_target']}"
                            )
                        else:
                            self.log("TMX import cancelled by user")
                            QMessageBox.information(self, "Import Cancelled", f"Import stopped.\n\nPartial entries may have been imported: {count:,}")
                else:
                    # Exact match - proceed normally with real-time progress
                    from PyQt6.QtWidgets import QProgressDialog
                    from PyQt6.QtCore import Qt
                    import time
                    
                    progress = QProgressDialog("Counting translation units...", "Cancel", 0, 100, self)
                    progress.setWindowTitle(self.tr("TMX Import"))
                    progress.setWindowModality(Qt.WindowModality.WindowModal)
                    progress.setMinimumDuration(0)
                    progress.setAutoClose(False)
                    progress.setAutoReset(False)
                    progress.setValue(0)
                    progress.show()
                    QApplication.processEvents()
                    
                    cancelled = False
                    start_time = time.time()
                    
                    def update_progress(current, total, message):
                        nonlocal cancelled
                        if progress.wasCanceled():
                            cancelled = True
                            return
                        
                        if total > 0:
                            percentage = int((current / total) * 100)
                            progress.setValue(percentage)
                            
                            # Calculate time stats
                            elapsed = time.time() - start_time
                            if current > 0:
                                rate = current / elapsed
                                remaining = (total - current) / rate if rate > 0 else 0
                                
                                # Format message with stats
                                time_info = f" (~{int(remaining/60)}m {int(remaining%60)}s remaining)" if remaining > 10 else ""
                                progress.setLabelText(f"{message}\nRate: {int(rate):,} entries/sec{time_info}")
                            else:
                                progress.setLabelText(message)
                        else:
                            progress.setLabelText(message)
                        
                        QApplication.processEvents()
                    
                    tm_id, count = self.tm_database.load_tmx_file(file_path, source_lang, target_lang, progress_callback=update_progress)
                    progress.close()
                    
                    if not cancelled:
                        self.log(f"Successfully imported {count} entries from TMX file")
                        QMessageBox.information(self, "Import Complete", f"TMX imported!\n\nEntries: {count:,}\nTM ID: {tm_id}")
                    else:
                        self.log("TMX import cancelled by user")
                        QMessageBox.information(self, "Import Cancelled", f"Import stopped.\n\nPartial entries may have been imported: {count:,}")
            
        except Exception as e:
            self.log(f"Error importing TMX file: {e}")
            QMessageBox.critical(self, "Import Error", f"Failed to import TMX:\n\n{e}")

    def export_tmx_from_grid(self):
        """Export all segments from current project grid as TMX file"""
        try:
            if not self.current_project or not self.current_project.segments:
                QMessageBox.warning(self, "No Project", "Please open a project first")
                return
            
            file_path, _ = fdh.get_save_file_name(
                self, 
                "Export Grid as TMX", 
                "TMX Files (*.tmx);;All Files (*.*)"
            )
            
            if not file_path:
                return
            
            source_segments = []
            target_segments = []
            entry_count = 0
            
            # Export all segments from grid (including empty translations)
            self.log("Exporting all segments from grid...")
            for segment in self.current_project.segments:
                source_text = segment.source
                target_text = segment.target if segment.target else ""

                # Strip outer wrapping tags if setting is enabled (for cleaner TM matching)
                if self.hide_outer_wrapping_tags:
                    source_text, _ = strip_outer_wrapping_tags(source_text)
                    target_text, _ = strip_outer_wrapping_tags(target_text) if target_text else (target_text, None)

                if source_text:
                    source_segments.append(source_text)
                    target_segments.append(target_text)
                    entry_count += 1
            
            self.log(f"Found {entry_count} segments in grid")
            
            if entry_count == 0:
                QMessageBox.warning(self, "No Data", "No segments found in grid")
                return
            
            # Generate and save TMX
            from modules.tmx_generator import TMXGenerator
            tmx_generator = TMXGenerator(log_callback=self.log)
            
            source_lang = self.current_project.source_lang or "en"
            target_lang = self.current_project.target_lang or "nl"
            
            tmx_tree = tmx_generator.generate_tmx(
                source_segments=source_segments,
                target_segments=target_segments,
                source_lang=source_lang,
                target_lang=target_lang
            )
            
            if tmx_generator.save_tmx(tmx_tree, file_path):
                self.log(f"✓ Exported TMX from grid: {file_path} ({entry_count} segments)")
                QMessageBox.information(
                    self, 
                    "Export Complete", 
                    f"Grid exported to TMX successfully!\n\n"
                    f"File: {file_path}\n"
                    f"Segments: {entry_count}\n"
                    f"Language pair: {source_lang} → {target_lang}"
                )
            else:
                QMessageBox.warning(self, "Export Error", "Failed to save TMX file")
            
        except Exception as e:
            self.log(f"✗ Error exporting grid as TMX: {e}")
            import traceback
            self.log(traceback.format_exc())
            QMessageBox.critical(self, "Export Error", f"Failed to export:\n\n{str(e)}")

    def export_tmx_from_selected(self):
        """Export only selected segments from grid as TMX file"""
        try:
            if not self.current_project or not self.current_project.segments:
                QMessageBox.warning(self, "No Project", "Please open a project first")
                return
            
            # Get selected rows from table
            selected_rows = set()
            for index in self.table.selectedIndexes():
                selected_rows.add(index.row())
            
            if not selected_rows:
                QMessageBox.warning(
                    self, 
                    "No Selection", 
                    "Please select one or more rows in the grid first.\n\n"
                    f"Click on row numbers or use {format_shortcut_for_display('Ctrl+Click')} to select multiple rows."
                )
                return
            
            file_path, _ = QFileDialog.getSaveFileName(
                self, 
                "Export Selected Segments as TMX", 
                "supervertaler_selected.tmx", 
                "TMX Files (*.tmx);;All Files (*.*)"
            )
            
            if not file_path:
                return
            
            source_segments = []
            target_segments = []
            entry_count = 0
            
            # Export only selected segments
            self.log(f"Exporting {len(selected_rows)} selected segments...")
            for row in sorted(selected_rows):
                if row < len(self.current_project.segments):
                    segment = self.current_project.segments[row]
                    source_text = segment.source
                    target_text = segment.target if segment.target else ""

                    # Strip outer wrapping tags if setting is enabled (for cleaner TM matching)
                    if self.hide_outer_wrapping_tags:
                        source_text, _ = strip_outer_wrapping_tags(source_text)
                        target_text, _ = strip_outer_wrapping_tags(target_text) if target_text else (target_text, None)

                    if source_text:
                        source_segments.append(source_text)
                        target_segments.append(target_text)
                        entry_count += 1
            
            self.log(f"Selected {entry_count} segments to export")
            
            if entry_count == 0:
                QMessageBox.warning(self, "No Data", "No valid segments selected")
                return
            
            # Generate and save TMX
            from modules.tmx_generator import TMXGenerator
            tmx_generator = TMXGenerator(log_callback=self.log)
            
            source_lang = self.current_project.source_lang or "en"
            target_lang = self.current_project.target_lang or "nl"
            
            tmx_tree = tmx_generator.generate_tmx(
                source_segments=source_segments,
                target_segments=target_segments,
                source_lang=source_lang,
                target_lang=target_lang
            )
            
            if tmx_generator.save_tmx(tmx_tree, file_path):
                self.log(f"✓ Exported TMX from selected: {file_path} ({entry_count} segments)")
                QMessageBox.information(
                    self, 
                    "Export Complete", 
                    f"Selected segments exported to TMX successfully!\n\n"
                    f"File: {file_path}\n"
                    f"Segments: {entry_count}\n"
                    f"Language pair: {source_lang} → {target_lang}"
                )
            else:
                QMessageBox.warning(self, "Export Error", "Failed to save TMX file")
            
        except Exception as e:
            self.log(f"✗ Error exporting selected segments as TMX: {e}")
            import traceback
            self.log(traceback.format_exc())
            QMessageBox.critical(self, "Export Error", f"Failed to export:\n\n{str(e)}")

    def export_tmx_from_tm_database(self):
        """Export translation memory entries as TMX file"""
        try:
            if not self.tm_database:
                QMessageBox.warning(self, "Error", "Translation memory database not available")
                return
            
            # Get TM entries from database
            tm_entries = self.tm_database.get_tm_entries(tm_id=None, limit=None)
            if not tm_entries:
                QMessageBox.warning(
                    self, 
                    "No Data", 
                    "No translation units in Translation Memory database.\n\n"
                    "Please import a TM or save translations to the TM first."
                )
                return
            
            file_path, _ = QFileDialog.getSaveFileName(
                self, 
                "Export TM Database as TMX", 
                "supervertaler_tm_database.tmx", 
                "TMX Files (*.tmx);;All Files (*.*)"
            )
            
            if not file_path:
                return
            
            source_segments = []
            target_segments = []
            
            # Extract segments from TM entries
            self.log(f"Exporting {len(tm_entries)} entries from TM database...")
            for entry in tm_entries:
                source_text = entry.get('source_text', entry.get('source', ''))
                target_text = entry.get('target_text', entry.get('target', ''))
                # Strip outer wrapping tags if setting is enabled
                if self.hide_outer_wrapping_tags:
                    source_text, _ = strip_outer_wrapping_tags(source_text)
                    target_text, _ = strip_outer_wrapping_tags(target_text)
                source_segments.append(source_text)
                target_segments.append(target_text)
            
            # Generate and save TMX
            from modules.tmx_generator import TMXGenerator
            tmx_generator = TMXGenerator(log_callback=self.log)
            
            # Use project languages or defaults
            source_lang = "en"
            target_lang = "nl"
            if self.current_project:
                source_lang = self.current_project.source_lang or "en"
                target_lang = self.current_project.target_lang or "nl"
            
            tmx_tree = tmx_generator.generate_tmx(
                source_segments=source_segments,
                target_segments=target_segments,
                source_lang=source_lang,
                target_lang=target_lang
            )
            
            if tmx_generator.save_tmx(tmx_tree, file_path):
                self.log(f"✓ Exported TMX from TM database: {file_path} ({len(tm_entries)} entries)")
                QMessageBox.information(
                    self, 
                    "Export Complete", 
                    f"Translation Memory exported to TMX successfully!\n\n"
                    f"File: {file_path}\n"
                    f"Entries: {len(tm_entries)}\n"
                    f"Language pair: {source_lang} → {target_lang}"
                )
            else:
                QMessageBox.warning(self, "Export Error", "Failed to save TMX file")
            
        except Exception as e:
            self.log(f"✗ Error exporting TM database as TMX: {e}")
            import traceback
            self.log(traceback.format_exc())
            QMessageBox.critical(self, "Export Error", f"Failed to export:\n\n{str(e)}")

    def export_tm_as_tmx(self):
        """Legacy function - exports grid segments (for backward compatibility)"""
        self.export_tmx_from_grid()

    def clear_tm_entries(self):
        """Clear all translation memory entries (with confirmation)"""
        try:
            reply = QMessageBox.question(
                self, 
                "Clear Translation Memory", 
                "⚠️ WARNING: This will permanently delete ALL translation memory entries!\n\nThis action cannot be undone. Are you sure?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No
            )
            
            if reply == QMessageBox.StandardButton.Yes:
                # Double confirmation
                reply2 = QMessageBox.question(
                    self, 
                    "Final Confirmation", 
                    "Last chance! Are you absolutely sure you want to delete all TM entries?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No
                )
                
                if reply2 == QMessageBox.StandardButton.Yes and self.tm_database:
                    # Clear TM entries
                    import sqlite3
                    db_path = self.user_data_path / "resources" / "supervertaler.db"
                    conn = sqlite3.connect(str(db_path), timeout=15)
                    conn.execute("PRAGMA journal_mode=WAL")
                    cursor = conn.cursor()
                    cursor.execute("DELETE FROM translation_units")
                    conn.commit()
                    conn.close()
                    
                    self.log("All translation memory entries cleared")
                    QMessageBox.information(self, "TM Cleared", "All translation memory entries have been deleted.")
            
        except Exception as e:
            self.log(f"Error clearing TM entries: {e}")
            QMessageBox.critical(self, "Clear Error", f"Failed to clear TM entries:\n\n{e}")

    def _show_create_tm_dialog(self, tm_metadata_mgr, refresh_callback, project_id):
        """Show dialog to create new TM"""
        dialog = QDialog(self)
        dialog.setWindowTitle(self.tr("Create New Translation Memory"))
        dialog.setMinimumWidth(450)
        
        layout = QFormLayout()
        
        # TM Name
        name_field = QLineEdit()
        name_field.setPlaceholderText(self.tr("e.g., ClientX_Medical_2024"))
        layout.addRow("TM Name:", name_field)
        
        # TM ID (auto-generated from name, but editable)
        tm_id_field = QLineEdit()
        tm_id_field.setPlaceholderText(self.tr("e.g., clientx_medical_2024"))
        layout.addRow("TM ID:", tm_id_field)
        
        # Auto-generate TM ID from name
        def update_tm_id():
            name = name_field.text().strip()
            if name and not tm_id_field.text():
                # Convert name to valid tm_id
                tm_id = name.lower().replace(' ', '_').replace('-', '_')
                # Remove special characters
                tm_id = ''.join(c for c in tm_id if c.isalnum() or c == '_')
                tm_id_field.setText(tm_id)
        
        name_field.textChanged.connect(update_tm_id)
        
        # Source language
        source_lang_field = QLineEdit()
        source_lang_field.setPlaceholderText(self.tr("e.g., en, nl, de"))
        layout.addRow("Source Language:", source_lang_field)
        
        # Target language
        target_lang_field = QLineEdit()
        target_lang_field.setPlaceholderText(self.tr("e.g., en, nl, de"))
        layout.addRow("Target Language:", target_lang_field)
        
        # Description
        desc_field = QTextEdit()
        desc_field.setMaximumHeight(80)
        desc_field.setPlaceholderText(self.tr("Optional description..."))
        layout.addRow("Description:", desc_field)
        
        # Buttons
        button_box = QHBoxLayout()
        button_box.addStretch()
        
        cancel_btn = QPushButton(self.tr("Cancel"))
        cancel_btn.clicked.connect(dialog.reject)
        button_box.addWidget(cancel_btn)
        
        create_btn = QPushButton(self.tr("Create"))
        create_btn.setStyleSheet("font-weight: bold;")
        create_btn.clicked.connect(dialog.accept)
        button_box.addWidget(create_btn)
        
        layout.addRow("", button_box)
        
        dialog.setLayout(layout)
        
        if dialog.exec() == QDialog.DialogCode.Accepted:
            name = name_field.text().strip()
            tm_id = tm_id_field.text().strip()
            source_lang = source_lang_field.text().strip() or None
            target_lang = target_lang_field.text().strip() or None
            description = desc_field.toPlainText().strip()
            
            if not name:
                QMessageBox.warning(self, "Error", "Please enter a TM name")
                return
            
            if not tm_id:
                QMessageBox.warning(self, "Error", "Please enter a TM ID")
                return

            if tm_metadata_mgr.tm_name_exists(name):
                QMessageBox.warning(
                    self, "Name already in use",
                    f"A translation memory called “{name}” already exists. "
                    f"Please choose a different name.")
                return

            # Create TM
            result = tm_metadata_mgr.create_tm(
                name=name,
                tm_id=tm_id,
                source_lang=source_lang,
                target_lang=target_lang,
                description=description
            )
            
            if result:
                # Auto-activate for current project (or global=0 if no project loaded)
                if project_id is not None:
                    tm_metadata_mgr.activate_tm(result, project_id)
                
                QMessageBox.information(self, "Success", f"Translation Memory '{name}' created successfully!")
                refresh_callback()
            else:
                QMessageBox.critical(self, "Error", "Failed to create TM. The TM name or ID may already exist.")

    @staticmethod
    def _preselect_tmx_pair(src_combo, tgt_combo, tmx_langs, header_src):
        """Default a TMX language dialog to the source language the TMX header
        declares, and the other language as target (#105). The detected list
        is alphabetical, so "first = source" reversed every pair whose source
        sorts after its target (en-GB/de-DE)."""
        if not header_src:
            return
        from modules import language_codes as _lc
        src_i = next((i for i, lang in enumerate(tmx_langs)
                      if lang.lower() == header_src.lower()), None)
        if src_i is None:
            src_i = next((i for i, lang in enumerate(tmx_langs)
                          if _lc.same_language(lang, header_src)), None)
        if src_i is None:
            return
        src_combo.setCurrentIndex(src_i)
        tgt_i = next((i for i in range(len(tmx_langs)) if i != src_i), None)
        if tgt_i is not None:
            tgt_combo.setCurrentIndex(tgt_i)

    def _import_tmx_as_tm(self, tm_metadata_mgr, tm_table, refresh_callback):
        """Import TMX file as a new TM or add to existing TM"""
        from PyQt6.QtWidgets import QFileDialog, QRadioButton, QButtonGroup
        
        # File dialog
        filepath, _ = QFileDialog.getOpenFileName(
            self,
            "Import TMX File",
            "",
            "TMX Files (*.tmx);;All Files (*.*)"
        )
        
        if not filepath:
            return
        
        # Ask user: create new TM or add to existing?
        choice_dialog = QDialog(self)
        choice_dialog.setWindowTitle(self.tr("Import TMX"))
        choice_dialog.setMinimumWidth(400)
        
        layout = QVBoxLayout(choice_dialog)
        
        layout.addWidget(QLabel(f"Importing: {filepath}\n"))
        layout.addWidget(QLabel(self.tr("Choose import option:")))
        
        new_tm_radio = CheckmarkRadioButton(self.tr("Create new TM from this TMX"))
        new_tm_radio.setChecked(True)
        layout.addWidget(new_tm_radio)
        
        existing_tm_radio = CheckmarkRadioButton(self.tr("Add to existing TM"))
        layout.addWidget(existing_tm_radio)
        
        # TM selection combo (for existing TM option)
        tm_combo = QComboBox()
        tm_combo.setEnabled(False)
        tms = tm_metadata_mgr.get_all_tms()
        for tm in tms:
            tm_combo.addItem(tm['name'], tm['tm_id'])
        layout.addWidget(tm_combo)
        
        existing_tm_radio.toggled.connect(tm_combo.setEnabled)
        
        # Buttons
        button_box = QHBoxLayout()
        button_box.addStretch()
        
        cancel_btn = QPushButton(self.tr("Cancel"))
        cancel_btn.clicked.connect(choice_dialog.reject)
        button_box.addWidget(cancel_btn)
        
        import_btn = QPushButton(self.tr("Import"))
        import_btn.setStyleSheet("font-weight: bold;")
        import_btn.clicked.connect(choice_dialog.accept)
        button_box.addWidget(import_btn)
        
        layout.addLayout(button_box)
        
        if choice_dialog.exec() != QDialog.DialogCode.Accepted:
            return
        
        # Determine target tm_id
        target_tm_id = None
        
        if new_tm_radio.isChecked():
            # Create new TM - ask for name
            from pathlib import Path
            default_name = Path(filepath).stem  # Use filename without extension
            # TM names are unique. Offer a free one up front ("Client TM (2)")
            # and say so plainly if the user types a taken one – this used to
            # end in a bare "Failed to create TM metadata" (#105).
            n = 2
            while tm_metadata_mgr.tm_name_exists(default_name):
                default_name = f"{Path(filepath).stem} ({n})"
                n += 1

            while True:
                name, ok = QInputDialog.getText(
                    self,
                    "New TM Name",
                    "Enter name for the new Translation Memory:",
                    text=default_name
                )

                if not ok or not name:
                    return
                if not tm_metadata_mgr.tm_name_exists(name):
                    break
                QMessageBox.warning(
                    self, "Name already in use",
                    f"A translation memory called \u201c{name}\u201d already exists.\n\n"
                    f"Choose a different name, or choose \u201cAdd to existing TM\u201d "
                    f"to add these entries to it."
                )
            
            # Generate tm_id from name
            tm_id = name.lower().replace(' ', '_').replace('-', '_')
            tm_id = ''.join(c for c in tm_id if c.isalnum() or c == '_')
            
            # Detect TMX languages BEFORE creating TM
            if not self.tm_database:
                self.initialize_tm_database()
            
            if not self.tm_database:
                QMessageBox.critical(self, "Error", "TM database not available")
                return
            
            tmx_langs = self.tm_database.detect_tmx_languages(filepath)
            
            if len(tmx_langs) < 2:
                QMessageBox.warning(
                    self, "Invalid TMX",
                    f"TMX file must contain at least 2 languages.\nFound: {', '.join(tmx_langs) if tmx_langs else 'none'}"
                )
                return
            
            # Let user select source and target from detected languages
            lang_dialog = QDialog(self)
            lang_dialog.setWindowTitle(self.tr("Select Language Pair"))
            lang_dialog.setMinimumWidth(400)
            
            lang_layout = QVBoxLayout(lang_dialog)
            tmx_header_src = self.tm_database.detect_tmx_source_language(filepath)
            lang_layout.addWidget(QLabel(
                f"TMX file contains {len(tmx_langs)} languages:\n{', '.join(tmx_langs)}\n"
                + (f"The file says its source language is {tmx_header_src}.\n" if tmx_header_src else "")))
            lang_layout.addWidget(QLabel(self.tr("Select source and target languages:")))
            
            # Source language combo
            src_layout = QHBoxLayout()
            src_layout.addWidget(QLabel(self.tr("Source:")))
            src_combo = QComboBox()
            for lang in tmx_langs:
                src_combo.addItem(lang)
            src_layout.addWidget(src_combo)
            lang_layout.addLayout(src_layout)

            # Swap source/target, centred between the two rows
            swap_row = QHBoxLayout()
            swap_row.addStretch()
            swap_btn = QPushButton(self.tr("🔄 Swap"))
            swap_btn.setToolTip(self.tr("Swap source and target languages"))

            def _swap_tmx_langs():
                _s, _t = src_combo.currentIndex(), tgt_combo.currentIndex()
                src_combo.setCurrentIndex(_t)
                tgt_combo.setCurrentIndex(_s)
            swap_btn.clicked.connect(_swap_tmx_langs)
            swap_row.addWidget(swap_btn)
            swap_row.addStretch()
            lang_layout.addLayout(swap_row)

            # Target language combo
            tgt_layout = QHBoxLayout()
            tgt_layout.addWidget(QLabel(self.tr("Target:")))
            tgt_combo = QComboBox()
            for lang in tmx_langs:
                tgt_combo.addItem(lang)
            # Default to second language if available
            if len(tmx_langs) > 1:
                tgt_combo.setCurrentIndex(1)
            # ...but the TMX header's srclang wins over alphabetical order (#105)
            self._preselect_tmx_pair(src_combo, tgt_combo, tmx_langs, tmx_header_src)
            tgt_layout.addWidget(tgt_combo)
            lang_layout.addLayout(tgt_layout)
            
            # Buttons
            lang_button_box = QHBoxLayout()
            lang_button_box.addStretch()
            
            lang_cancel_btn = QPushButton(self.tr("Cancel"))
            lang_cancel_btn.clicked.connect(lang_dialog.reject)
            lang_button_box.addWidget(lang_cancel_btn)
            
            lang_ok_btn = QPushButton(self.tr("OK"))
            lang_ok_btn.setStyleSheet("font-weight: bold;")
            lang_ok_btn.clicked.connect(lang_dialog.accept)
            lang_button_box.addWidget(lang_ok_btn)
            
            lang_layout.addLayout(lang_button_box)
            
            if lang_dialog.exec() != QDialog.DialogCode.Accepted:
                return
            
            detected_src = src_combo.currentText()
            detected_tgt = tgt_combo.currentText()
            
            # Validate that source != target
            if detected_src == detected_tgt:
                QMessageBox.warning(
                    self, "Invalid Selection",
                    "Source and target languages must be different."
                )
                return
            
            # Create TM metadata entry with detected languages
            db_id = tm_metadata_mgr.create_tm(
                name=name,
                tm_id=tm_id,
                source_lang=detected_src,
                target_lang=detected_tgt,
                description=f"Imported from {Path(filepath).name}"
            )
            
            if not db_id:
                QMessageBox.critical(self, "Error", "Failed to create TM metadata")
                return

            # create_tm() makes the id unique when a TM already uses it (same
            # TMX imported twice → "name_2"). Import into the TM just created,
            # not the older one that owns the id we asked for.
            created = tm_metadata_mgr.get_tm(db_id) or {}
            target_tm_id = created.get('tm_id') or tm_id
        else:
            # Use existing TM
            target_tm_id = tm_combo.currentData()
            if not target_tm_id:
                QMessageBox.warning(self, "Error", "No TM selected")
                return
        
        # Import TMX
        try:
            if not self.tm_database:
                self.initialize_tm_database()
            
            if self.tm_database:
                # Import using existing TM database import method
                # Get TM languages from metadata
                tm_info = tm_metadata_mgr.get_tm_by_tm_id(target_tm_id)
                if not tm_info:
                    QMessageBox.critical(self, "Error", f"TM '{target_tm_id}' not found")
                    return
                
                tm_src_lang = tm_info.get('source_lang')
                tm_tgt_lang = tm_info.get('target_lang')
                
                # If TM has NULL languages, detect from TMX and let user select
                if not tm_src_lang or not tm_tgt_lang:
                    tmx_langs = self.tm_database.detect_tmx_languages(filepath)
                    
                    if len(tmx_langs) < 2:
                        QMessageBox.warning(
                            self, "Invalid TMX",
                            f"TMX file must contain at least 2 languages.\nFound: {', '.join(tmx_langs) if tmx_langs else 'none'}"
                        )
                        return
                    
                    # Let user select source and target from detected languages
                    lang_dialog = QDialog(self)
                    lang_dialog.setWindowTitle(self.tr("Select Language Pair"))
                    lang_dialog.setMinimumWidth(400)
                    
                    lang_layout = QVBoxLayout(lang_dialog)
                    tmx_header_src = self.tm_database.detect_tmx_source_language(filepath)
                    lang_layout.addWidget(QLabel(
                        f"TMX file contains {len(tmx_langs)} languages:\n{', '.join(tmx_langs)}\n"
                        + (f"The file says its source language is {tmx_header_src}.\n" if tmx_header_src else "")))
                    lang_layout.addWidget(QLabel(self.tr("Select source and target languages:")))
                    
                    # Source language combo
                    src_layout = QHBoxLayout()
                    src_layout.addWidget(QLabel(self.tr("Source:")))
                    src_combo = QComboBox()
                    for lang in tmx_langs:
                        src_combo.addItem(lang)
                    src_layout.addWidget(src_combo)
                    lang_layout.addLayout(src_layout)

                    # Swap source/target, centred between the two rows
                    swap_row = QHBoxLayout()
                    swap_row.addStretch()
                    swap_btn = QPushButton(self.tr("🔄 Swap"))
                    swap_btn.setToolTip(self.tr("Swap source and target languages"))

                    def _swap_tmx_langs():
                        _s, _t = src_combo.currentIndex(), tgt_combo.currentIndex()
                        src_combo.setCurrentIndex(_t)
                        tgt_combo.setCurrentIndex(_s)
                    swap_btn.clicked.connect(_swap_tmx_langs)
                    swap_row.addWidget(swap_btn)
                    swap_row.addStretch()
                    lang_layout.addLayout(swap_row)

                    # Target language combo
                    tgt_layout = QHBoxLayout()
                    tgt_layout.addWidget(QLabel(self.tr("Target:")))
                    tgt_combo = QComboBox()
                    for lang in tmx_langs:
                        tgt_combo.addItem(lang)
                    # Default to second language if available
                    if len(tmx_langs) > 1:
                        tgt_combo.setCurrentIndex(1)
                    # ...but the TMX header's srclang wins over alphabetical order (#105)
                    self._preselect_tmx_pair(src_combo, tgt_combo, tmx_langs, tmx_header_src)
                    tgt_layout.addWidget(tgt_combo)
                    lang_layout.addLayout(tgt_layout)
                    
                    # Buttons
                    lang_button_box = QHBoxLayout()
                    lang_button_box.addStretch()
                    
                    lang_cancel_btn = QPushButton(self.tr("Cancel"))
                    lang_cancel_btn.clicked.connect(lang_dialog.reject)
                    lang_button_box.addWidget(lang_cancel_btn)
                    
                    lang_ok_btn = QPushButton(self.tr("OK"))
                    lang_ok_btn.setStyleSheet("font-weight: bold;")
                    lang_ok_btn.clicked.connect(lang_dialog.accept)
                    lang_button_box.addWidget(lang_ok_btn)
                    
                    lang_layout.addLayout(lang_button_box)
                    
                    if lang_dialog.exec() != QDialog.DialogCode.Accepted:
                        return
                    
                    tm_src_lang = src_combo.currentText()
                    tm_tgt_lang = tgt_combo.currentText()
                    
                    # Validate that source != target
                    if tm_src_lang == tm_tgt_lang:
                        QMessageBox.warning(
                            self, "Invalid Selection",
                            "Source and target languages must be different."
                        )
                        return
                    
                    # Update TM metadata with detected languages
                    tm_metadata_mgr.update_tm(
                        target_tm_id,
                        source_lang=tm_src_lang,
                        target_lang=tm_tgt_lang
                    )
                    self.log(f"✓ Updated TM '{target_tm_id}' with detected languages: {tm_src_lang} → {tm_tgt_lang}")
                
                # Detect TMX languages and check compatibility
                tmx_langs = self.tm_database.detect_tmx_languages(filepath)
                compat = self.tm_database.check_language_compatibility(tmx_langs, tm_src_lang, tm_tgt_lang)
                
                if not compat['compatible']:
                    QMessageBox.warning(
                        self, "Language Mismatch",
                        f"TMX languages ({', '.join(tmx_langs)}) don't match TM languages ({tm_src_lang}, {tm_tgt_lang})"
                    )
                    return
                
                # Determine if we should strip variants
                strip_variants = compat.get('variant_match', False)
                if strip_variants:
                    reply = QMessageBox.question(
                        self, "Language Variants Detected",
                        f"TMX has language variants:\n{compat['tmx_source']}, {compat['tmx_target']}\n\n"
                        f"TM expects:\n{tm_src_lang}, {tm_tgt_lang}\n\n"
                        "Import by matching base languages?",
                        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
                    )
                    if reply != QMessageBox.StandardButton.Yes:
                        return
                
                # Import into existing TM with real-time progress dialog
                from PyQt6.QtWidgets import QProgressDialog
                from PyQt6.QtCore import Qt
                import time
                
                progress = QProgressDialog("Counting translation units...", "Cancel", 0, 100, self)
                progress.setWindowTitle(self.tr("TMX Import"))
                progress.setWindowModality(Qt.WindowModality.WindowModal)
                progress.setMinimumDuration(0)
                progress.setAutoClose(False)
                progress.setAutoReset(False)
                progress.setValue(0)
                progress.show()
                QApplication.processEvents()
                
                cancelled = False
                start_time = time.time()
                
                def update_progress(current, total, message):
                    nonlocal cancelled
                    if progress.wasCanceled():
                        cancelled = True
                        return
                    
                    if total > 0:
                        percentage = int((current / total) * 100)
                        progress.setValue(percentage)
                        
                        # Calculate time stats
                        elapsed = time.time() - start_time
                        if current > 0:
                            rate = current / elapsed
                            remaining = (total - current) / rate if rate > 0 else 0
                            
                            # Format message with stats
                            time_info = f" (~{int(remaining/60)}m {int(remaining%60)}s remaining)" if remaining > 10 else ""
                            progress.setLabelText(f"{message}\nRate: {int(rate):,} entries/sec{time_info}")
                        else:
                            progress.setLabelText(message)
                    else:
                        progress.setLabelText(message)
                    
                    QApplication.processEvents()
                
                count = self.tm_database._load_tmx_into_db(
                    filepath, tm_src_lang, tm_tgt_lang, target_tm_id, 
                    strip_variants=strip_variants, progress_callback=update_progress
                )
                
                progress.close()
                
                if not cancelled:
                    # Update entry count
                    tm_metadata_mgr.update_entry_count(target_tm_id)
                    self.log(f"Imported {count} entries into TM '{target_tm_id}'")
                    
                    QMessageBox.information(self, "Success", f"TMX imported successfully into TM '{target_tm_id}'!\n\nEntries imported: {count:,}")
                else:
                    self.log(f"TMX import cancelled by user. Partial import: {count} entries")
                    QMessageBox.information(self, "Import Cancelled", f"Import stopped.\n\nPartial entries imported: {count:,}")
                refresh_callback()
            else:
                QMessageBox.critical(self, "Error", "TM database not available")
        except Exception as e:
            self.log(f"✗ Error importing TMX: {e}")
            import traceback
            traceback.print_exc()
            QMessageBox.critical(self, "Import Error", f"Failed to import TMX:\n\n{str(e)}")

    def _attach_sdltm_as_tm(self, tm_metadata_mgr, refresh_callback):
        """Mirror a Trados Studio .sdltm into Supervertaler's DB as a read-only TM.

        The .sdltm file stays untouched – we open it in SQLite read-only mode
        so it's safe to attach while Trados Studio has the same TM open. Each
        translation unit's source/target XML is converted to Supervertaler's
        inline-tag form (`<N>...</N>` / `<N/>`), then bulk-inserted under a
        new tm_id.
        """
        from PyQt6.QtWidgets import QFileDialog, QProgressDialog
        from pathlib import Path
        from modules.sdltm_handler import SDLTMReader, SDLTMError

        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Attach Trados TM (.sdltm)",
            "",
            "Trados Translation Memory (*.sdltm);;All Files (*.*)"
        )
        if not file_path:
            return

        if not self.tm_database:
            self.initialize_tm_database()
        if not self.tm_database:
            QMessageBox.critical(self, "Error", "TM database not available")
            return

        try:
            reader = SDLTMReader(file_path, log_callback=self.log)
            md = reader.metadata()
        except SDLTMError as e:
            QMessageBox.critical(
                self, "Cannot Read .sdltm",
                f"Could not open the selected file as a Trados TM:\n\n{e}"
            )
            return

        # Confirmation dialog – show what we found and let the user name it.
        # Default to the full filename (including .sdltm) so the entry stands
        # out from regular TMX-imported TMs in the list.
        default_name = Path(file_path).name
        confirm_msg = (
            f"<b>{Path(file_path).name}</b><br><br>"
            f"Languages: <b>{md['source_lang']}</b> → <b>{md['target_lang']}</b><br>"
            f"Translation units: <b>{md['tu_count']:,}</b><br><br>"
            f"This will mirror the TM into Supervertaler as <i>read-only</i>. "
            f"The .sdltm file itself is not modified.<br><br>"
            f"Continue?"
        )
        reply = QMessageBox.question(
            self, "Attach Trados TM",
            confirm_msg,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Yes
        )
        if reply != QMessageBox.StandardButton.Yes:
            reader.close()
            return

        # Ask the user for the display name (default = full filename of the .sdltm).
        name, ok = QInputDialog.getText(
            self, "Trados TM name",
            "Display name for this TM in Supervertaler:",
            text=default_name
        )
        if not ok or not name.strip():
            reader.close()
            return
        name = name.strip()

        # tm_id derived from filename so re-attaching the same .sdltm
        # lands on the same TM (and acts as a "refresh"). If a TM with
        # this id already exists we ask whether to replace it.
        stem = Path(file_path).stem
        safe_stem = "".join(c if c.isalnum() else "_" for c in stem).strip("_")
        tm_id = f"trados_{safe_stem.lower()}"

        if tm_metadata_mgr.tm_id_exists(tm_id):
            existing = tm_metadata_mgr.get_tm_by_tm_id(tm_id)
            existing_count = existing.get('entry_count', 0) if existing else 0
            reply = QMessageBox.question(
                self, "Trados TM already attached",
                f"A TM with id <code>{tm_id}</code> is already attached "
                f"({existing_count:,} TUs).<br><br>"
                f"Replace it with a fresh import from the .sdltm? "
                f"This deletes the existing entries and re-imports.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel
            )
            if reply != QMessageBox.StandardButton.Yes:
                reader.close()
                return
            # Delete entries + metadata so we get a clean slate.
            if existing:
                tm_metadata_mgr.delete_tm(existing['id'], delete_entries=True)

        db_id = tm_metadata_mgr.create_tm(
            name=name,
            tm_id=tm_id,
            source_lang=md["source_lang"],
            target_lang=md["target_lang"],
            description=f"Mirrored from {file_path}",
            read_only=True,
            auto_unique_id=False,
        )
        if not db_id:
            QMessageBox.critical(self, "Error", "Failed to create TM metadata entry")
            reader.close()
            return
        actual_tm_id = tm_id

        # Bulk import with a progress dialog. We chunk the inserts so the
        # dialog updates and the user can cancel mid-flight on huge TMs.
        total = md["tu_count"]
        progress = QProgressDialog(
            f"Importing {total:,} TUs from {Path(file_path).name}...",
            "Cancel", 0, max(total, 1), self
        )
        progress.setWindowTitle(self.tr("Attaching Trados TM"))
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        progress.setValue(0)
        QApplication.processEvents()

        CHUNK = 500
        buffer = []        # (src, tgt) pairs ready for batch insert
        inserted = 0
        cancelled = False
        import_error = None
        max_id_seen = 0
        max_date_seen = ""  # lexicographic compare on ISO strings
        try:
            for tu_id, src, tgt, change_date in reader.iter_tus():
                if progress.wasCanceled():
                    cancelled = True
                    break
                buffer.append((src, tgt))
                if tu_id > max_id_seen:
                    max_id_seen = tu_id
                if change_date and change_date > max_date_seen:
                    max_date_seen = change_date
                if len(buffer) >= CHUNK:
                    inserted += self.db_manager.add_translation_units_batch(
                        buffer, md["source_lang"], md["target_lang"], actual_tm_id
                    )
                    buffer = []
                    progress.setValue(min(inserted, total))
                    progress.setLabelText(
                        f"Imported {inserted:,} / {total:,} TUs from {Path(file_path).name}..."
                    )
                    QApplication.processEvents()
            # Flush remaining buffer.
            if buffer and not cancelled:
                inserted += self.db_manager.add_translation_units_batch(
                    buffer, md["source_lang"], md["target_lang"], actual_tm_id
                )
        except Exception as e:
            # Catch and surface – Qt slot callbacks would otherwise swallow
            # the traceback silently and leave the user with an empty TM.
            import_error = e
            self.log(f"✗ Trados TM import failed: {e}")
            import traceback
            traceback.print_exc()
        finally:
            progress.close()
            reader.close()

        # Persist external-source state so the periodic sync timer can
        # delta-update from this .sdltm without duplicating work.
        if import_error is None and inserted > 0:
            try:
                self.db_manager.cursor.execute(
                    "UPDATE translation_memories SET "
                    "external_source_path = ?, "
                    "external_last_sync_id = ?, "
                    "external_last_sync_date = ?, "
                    "external_last_mtime = ? "
                    "WHERE id = ?",
                    (
                        file_path,
                        max_id_seen,
                        max_date_seen or None,
                        md.get("mtime"),
                        db_id,
                    ),
                )
                self.db_manager.connection.commit()
            except Exception as e:
                self.log(f"⚠ Could not save external-source state: {e}")

        # Refresh the entry_count on the metadata row so the TM Manager
        # display shows the right number without a manual reload.
        if hasattr(tm_metadata_mgr, 'update_entry_count'):
            tm_metadata_mgr.update_entry_count(actual_tm_id)

        if refresh_callback:
            refresh_callback()

        if import_error is not None:
            QMessageBox.critical(
                self, "Import Failed",
                f"Failed while importing TUs from <b>{Path(file_path).name}</b>:<br><br>"
                f"{import_error}<br><br>"
                f"The partial TM has been kept ({inserted:,} TUs) so you can inspect it. "
                f"Delete it from the TM Manager and try again."
            )
        elif cancelled:
            self.log(f"⚠ Trados TM attach cancelled after {inserted:,} TUs (TM kept partial)")
            QMessageBox.warning(
                self, "Attach Cancelled",
                f"Cancelled after importing {inserted:,} of {total:,} TUs.\n\n"
                f"The partial TM '{name}' has been kept – you can delete it from the "
                f"TM Manager or re-attach the .sdltm to start over."
            )
        else:
            self.log(f"✓ Attached Trados TM '{name}' ({inserted:,} TUs, tm_id={actual_tm_id})")
            QMessageBox.information(
                self, "Trados TM Attached",
                f"Successfully imported <b>{inserted:,}</b> TUs from "
                f"<b>{Path(file_path).name}</b>.<br><br>"
                f"Languages: {md['source_lang']} → {md['target_lang']}<br>"
                f"TM ID: <code>{actual_tm_id}</code> (read-only)<br><br>"
                f"Activate it for the current project from the TM list to use it for lookups."
            )

    def _sync_external_tms(self):
        """Tick of the external-TM live-sync timer.

        For each TM that was attached from an external source (currently:
        Trados Studio .sdltm), check the file's mtime; if it's newer than
        when we last synced, pull any new or modified TUs in via a delta
        query. Cheap on every tick when nothing has changed (one stat()
        call per attached TM). Silent unless we actually pulled new data.
        """
        if not self.db_manager or not getattr(self.db_manager, 'connection', None):
            return
        try:
            cur = self.db_manager.cursor
            cur.execute(
                "SELECT id, name, tm_id, source_lang, target_lang, "
                "external_source_path, external_last_sync_id, "
                "external_last_sync_date, external_last_mtime "
                "FROM translation_memories "
                "WHERE external_source_path IS NOT NULL"
            )
            rows = cur.fetchall()
        except Exception:
            # Migration may not have run yet, or DB closed mid-shutdown.
            return

        for db_id, name, tm_id, src_lang, tgt_lang, path, last_id, last_date, last_mtime in rows:
            if not path or not os.path.exists(path):
                continue
            try:
                current_mtime = os.path.getmtime(path)
            except OSError:
                continue
            # Skip if nothing has changed on disk since the last sync. Use a
            # small epsilon to dodge filesystem timestamp granularity issues
            # (FAT-flavoured filesystems round to 2-second boundaries).
            if last_mtime is not None and current_mtime <= (last_mtime + 0.001):
                continue
            self._delta_sync_external_tm(
                db_id, name, tm_id, src_lang, tgt_lang, path,
                last_id or 0, last_date, current_mtime,
            )

    def _delta_sync_external_tm(
        self, db_id, name, tm_id, src_lang, tgt_lang, path,
        since_id, since_date, current_mtime,
    ):
        """Pull new/modified TUs from an external .sdltm into our DB.

        Always updates external_last_mtime so a no-op change (mtime bumped
        without any new TUs, e.g. Trados re-saving on close) doesn't keep
        re-triggering the SQL on every tick.
        """
        from modules.sdltm_handler import SDLTMReader
        try:
            with SDLTMReader(path, log_callback=self.log) as reader:
                new_tus = list(reader.iter_tus(
                    since_id=since_id,
                    since_change_date=since_date or None,
                ))
        except Exception as e:
            self.log(f"⚠ Live-sync failed for '{name}' ({path}): {e}")
            return

        max_id = since_id
        max_date = since_date or ""
        inserted = 0
        if new_tus:
            pairs = [(src, tgt) for (_tid, src, tgt, _cd) in new_tus]
            try:
                inserted = self.db_manager.add_translation_units_batch(
                    pairs, src_lang, tgt_lang, tm_id
                )
            except Exception as e:
                self.log(f"⚠ Could not insert delta TUs for '{name}': {e}")
                return
            for tid, _src, _tgt, cd in new_tus:
                if tid > max_id:
                    max_id = tid
                if cd and cd > max_date:
                    max_date = cd

        # Always persist mtime + max-marker state so we don't redo this work.
        try:
            self.db_manager.cursor.execute(
                "UPDATE translation_memories SET "
                "external_last_sync_id = ?, "
                "external_last_sync_date = ?, "
                "external_last_mtime = ?, "
                "entry_count = (SELECT COUNT(*) FROM translation_units WHERE tm_id = ?) "
                "WHERE id = ?",
                (max_id, max_date or None, current_mtime, tm_id, db_id),
            )
            self.db_manager.connection.commit()
        except Exception as e:
            self.log(f"⚠ Could not persist sync state for '{name}': {e}")
            return

        if inserted:
            self.log(f"🔄 Synced {inserted:,} new TU(s) from '{name}'")

    def _export_tm_to_tmx(self, tm_metadata_mgr, tm_table):
        """Export selected TM to TMX file"""
        selected_row = tm_table.currentRow()
        if selected_row < 0:
            QMessageBox.warning(self, "Error", "Please select a TM to export")
            return
        
        # Get TM (column 0 = TM Name)
        tm_name = tm_table.item(selected_row, 0).text()
        tms = tm_metadata_mgr.get_all_tms()
        tm = next((t for t in tms if t['name'] == tm_name), None)
        if not tm:
            QMessageBox.warning(self, "Error", "Could not find selected TM")
            return
        
        # File dialog
        from PyQt6.QtWidgets import QFileDialog
        default_filename = f"{tm_name.replace(' ', '_')}.tmx"
        filepath, _ = QFileDialog.getSaveFileName(
            self,
            "Export TM to TMX",
            default_filename,
            "TMX Files (*.tmx);;All Files (*.*)"
        )
        
        if not filepath:
            return
        
        try:
            # Get all entries for this TM
            cursor = self.db_manager.cursor
            cursor.execute("""
                SELECT source_text, target_text 
                FROM translation_units 
                WHERE tm_id = ?
                ORDER BY id
            """, (tm['tm_id'],))
            
            entries = cursor.fetchall()
            
            if not entries:
                QMessageBox.warning(self, "Empty TM", "This TM has no entries to export")
                return
            
            # Generate TMX
            from modules.tmx_generator import TMXGenerator
            tmx_generator = TMXGenerator(log_callback=self.log)
            
            source_segments = [e[0] for e in entries]
            target_segments = [e[1] for e in entries]
            
            tmx_tree = tmx_generator.generate_tmx(
                source_segments=source_segments,
                target_segments=target_segments,
                source_lang=tm['source_lang'] or "en",
                target_lang=tm['target_lang'] or "nl"
            )
            
            if tmx_generator.save_tmx(tmx_tree, filepath):
                self.log(f"✓ Exported TM '{tm_name}' to {filepath} ({len(entries)} entries)")
                QMessageBox.information(
                    self,
                    "Export Complete",
                    f"TM exported successfully!\n\nFile: {filepath}\nEntries: {len(entries)}"
                )
            else:
                QMessageBox.warning(self, "Export Error", "Failed to save TMX file")
        
        except Exception as e:
            self.log(f"✗ Error exporting TM: {e}")
            import traceback
            traceback.print_exc()
            QMessageBox.critical(self, "Export Error", f"Failed to export TM:\n\n{str(e)}")

    def _delete_tm(self, tm_metadata_mgr, tm_table, refresh_callback):
        """Delete selected TM"""
        selected_row = tm_table.currentRow()
        if selected_row < 0:
            QMessageBox.warning(self, "Error", "Please select a TM to delete")
            return
        
        # Get TM (column 0 = TM Name)
        tm_name = tm_table.item(selected_row, 0).text()
        tms = tm_metadata_mgr.get_all_tms()
        tm = next((t for t in tms if t['name'] == tm_name), None)
        if not tm:
            return
        
        # Confirm deletion
        reply = QMessageBox.question(
            self,
            "Delete TM",
            f"Delete TM '{tm_name}'?\n\nThis will also delete all {tm['entry_count']} translation units in this TM.\n\nThis action cannot be undone!",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )
        
        if reply == QMessageBox.StandardButton.Yes:
            if tm_metadata_mgr.delete_tm(tm['id'], delete_entries=True):
                QMessageBox.information(self, "Success", f"TM '{tm_name}' deleted successfully")
                refresh_callback()
            else:
                QMessageBox.critical(self, "Error", "Failed to delete TM")

    def _show_tm_editor_dialog(self, tm_metadata_mgr, tm_table):
        """Show TM Editor dialog for selected TM (combines old TM Manager functionality)"""
        selected_row = tm_table.currentRow()
        if selected_row < 0:
            QMessageBox.warning(self, "Error", "Please select a TM to edit")
            return
        
        # Get TM (column 0 = TM Name)
        tm_name = tm_table.item(selected_row, 0).text()
        tms = tm_metadata_mgr.get_all_tms()
        tm = next((t for t in tms if t['name'] == tm_name), None)
        if not tm:
            return
        
        # Open TM Editor with this specific TM
        from modules.tm_editor_dialog import TMEditorDialog
        dialog = TMEditorDialog(self, self.db_manager, self.log, tm_id=tm['tm_id'], tm_name=tm_name)
        dialog.exec()

    def save_segment_to_activated_tms(self, source: str, target: str):
        """
        Save segment to all writable TMs for current project.

        Note: Uses get_writable_tm_ids() which checks the Write checkbox (read_only=0),
        NOT get_active_tm_ids() which checks the Read checkbox (is_active=1).

        Respects tm_save_mode setting:
        - 'latest': Overwrites existing entries with same source (keeps only newest translation)
        - 'all': Keeps all translations with different targets (default SQLite behavior)

        If hide_outer_wrapping_tags is enabled, strips structural tags (like <li-o>, <p>)
        before saving to TM for better matching leverage.

        Args:
            source: Source text
            target: Target text
        """
        if not self.current_project:
            return

        # v1.10.143: keep the untransformed source so we can do a targeted
        # match-cache invalidation below (segment.source is stored untransformed).
        original_source = source

        # Strip invisible character markers (↵, ·, →, °) to prevent marker leakage into TM
        if hasattr(self, 'reverse_invisible_replacements'):
            source = self.reverse_invisible_replacements(source)
            target = self.reverse_invisible_replacements(target)

        # Strip outer structural tags if setting is enabled (for cleaner TM matching)
        if self.hide_outer_wrapping_tags:
            source, _ = strip_outer_wrapping_tags(source)
            target, _ = strip_outer_wrapping_tags(target)

        if not hasattr(self.current_project, 'source_lang') or not hasattr(self.current_project, 'target_lang'):
            return

        # Get WRITABLE TM IDs for this project (Write checkbox enabled)
        tm_ids = []

        if hasattr(self, 'tm_metadata_mgr') and self.tm_metadata_mgr:
            if hasattr(self, 'current_project') and self.current_project:
                project_id = self.current_project.id if hasattr(self.current_project, 'id') else None

                if project_id:
                    # Use get_writable_tm_ids() to find TMs with Write enabled
                    tm_ids = self.tm_metadata_mgr.get_writable_tm_ids(project_id)
                else:
                    self.log(f"⚠️ Cannot save to TM: project has no 'id' attribute!")
            else:
                self.log(f"⚠️ Cannot save to TM: No current project loaded!")
        else:
            self.log(f"⚠️ Cannot save to TM: TM metadata manager not available!")

        # If no TMs have Write enabled, skip saving
        if not tm_ids:
            self.log("⚠️ No TMs with Write enabled - segment not saved to TM.")
            self.log(f"   - To fix: Go to Resources > Translation Memories > TM List and enable the Write checkbox")
            return

        # Check TM save mode: 'latest' = overwrite, 'all' = keep all variants
        overwrite_mode = getattr(self, 'tm_save_mode', 'latest') == 'latest'

        # Save to each writable TM
        saved_count = 0
        for tm_id in tm_ids:
            try:
                self.db_manager.add_translation_unit(
                    source=source,
                    target=target,
                    source_lang=self.current_project.source_lang,
                    target_lang=self.current_project.target_lang,
                    tm_id=tm_id,
                    overwrite=overwrite_mode
                )
                saved_count += 1
            except Exception as e:
                self.log(f"⚠️ Could not save to TM '{tm_id}': {e}")

        if saved_count > 0:
            mode_note = " (overwrite)" if overwrite_mode else ""
            msg = f"💾 Saved segment to {saved_count} TM(s){mode_note}"
            self._queue_tm_save_log(msg)
            # v1.10.143: targeted match-cache invalidation.
            # The per-segment match cache (translation_matches_cache, keyed by
            # segment id) holds the TM match that was looked up for each segment.
            # After writing this source's new translation to the TM, every
            # segment that shares this source now has a STALE cached TM match —
            # navigating back to it would show the pre-edit target in the match
            # pane (exactly the "TM still says <old word>" symptom). Clear only
            # those entries (matched by untransformed source, so repetitions are
            # handled too); everything else stays cached, so navigation is still
            # fast. A blanket clear was removed previously for that very reason.
            try:
                if hasattr(self, 'translation_matches_cache') and self.translation_matches_cache:
                    norm_source = (original_source or '').strip()
                    with self.translation_matches_cache_lock:
                        stale_ids = [
                            seg.id for seg in self.current_project.segments
                            if seg.id in self.translation_matches_cache
                            and (seg.source or '').strip() == norm_source
                        ]
                        for sid in stale_ids:
                            del self.translation_matches_cache[sid]
                    if stale_ids:
                        self._queue_tm_save_log(
                            f"🔄 Refreshed match cache for {len(stale_ids)} segment(s) sharing this source"
                        )
            except Exception as _inv_exc:
                self.log(f"⚠️ Match-cache invalidation skipped: {_inv_exc}")

    def send_segments_to_tm_dialog(self):
        """
        Show dialog to send segments to TM (similar to memoQ's Confirm and Update).
        Allows user to select scope and status filters, then sends matching segments 
        to the writable TM(s).
        """
        if not self.current_project or not self.current_project.segments:
            QMessageBox.warning(self, "No Project", "Please load a project first.")
            return
        
        if not hasattr(self, 'tm_metadata_mgr') or not self.tm_metadata_mgr:
            QMessageBox.warning(self, "No TM Manager", "Translation Memory system is not initialized.")
            return
        
        # Get available TMs
        all_tms = self.tm_metadata_mgr.get_all_tms()
        if not all_tms:
            QMessageBox.warning(self, "No TMs", 
                "No Translation Memories found.\n\nPlease create or import a TM on the 💾 TMs tab.")
            return
        
        # Get writable TMs (those with Write enabled)
        writable_tms = [tm for tm in all_tms if not tm.get('read_only', True)]
        if not writable_tms:
            QMessageBox.warning(self, "No Writable TMs", 
                "No writable Translation Memories found.\n\n"
                "Please enable Write (✓) for at least one TM on the 💾 TMs tab.")
            return
        
        # Create dialog
        dialog = QDialog(self)
        dialog.setWindowTitle(self.tr("Send Segments to TM"))
        dialog.setMinimumWidth(500)
        dialog.setMinimumHeight(400)
        layout = QVBoxLayout(dialog)
        
        # Info text
        info_label = QLabel(
            "Send translated segments to a Translation Memory.\n"
            "Only segments matching the selected criteria will be sent."
        )
        info_label.setWordWrap(True)
        info_label.setStyleSheet("color: #666; margin-bottom: 10px;")
        layout.addWidget(info_label)
        
        # Scope group
        scope_group = QGroupBox(self.tr("Scope"))
        scope_layout = QVBoxLayout(scope_group)
        
        scope_project = CheckmarkCheckBox(self.tr("Entire project"))
        scope_project.setChecked(True)
        scope_selected = CheckmarkCheckBox(self.tr("Selected segments only"))
        scope_selected.setEnabled(False)  # Will enable if selection exists
        scope_selected.setChecked(False)
        
        # Check if we have a selection
        selected_segments = self.get_selected_segments_from_grid() if hasattr(self, 'get_selected_segments_from_grid') else []
        if selected_segments:
            scope_selected.setEnabled(True)
            scope_selected.setText(f"Selected segments only ({len(selected_segments)} selected)")
        
        # Make scope checkboxes mutually exclusive
        def on_scope_project_changed(checked):
            if checked:
                scope_selected.setChecked(False)
        
        def on_scope_selected_changed(checked):
            if checked:
                scope_project.setChecked(False)
        
        scope_project.toggled.connect(on_scope_project_changed)
        scope_selected.toggled.connect(on_scope_selected_changed)
        
        scope_layout.addWidget(scope_project)
        scope_layout.addWidget(scope_selected)
        layout.addWidget(scope_group)
        
        # Status filter group
        status_group = QGroupBox(self.tr("Status of segments to send"))
        status_layout = QGridLayout(status_group)
        
        # Import status definitions
        from modules.statuses import STATUSES
        
        # Create checkboxes for each status
        status_checkboxes = {}
        statuses_to_show = [
            ("confirmed", "Confirmed", True),       # Default checked
            ("draft", "Draft", False),
            ("proofread", "Proofread", True),       # Default checked
            ("approved", "Approved", True),         # Default checked
            ("pretranslated", "Pre-translated", False),
        ]
        
        row = 0
        col = 0
        for status_key, status_label, default_checked in statuses_to_show:
            status_def = STATUSES.get(status_key)
            if status_def:
                cb = CheckmarkCheckBox(f"{status_def.icon} {status_label}")
                cb.setChecked(default_checked)
                status_checkboxes[status_key] = cb
                status_layout.addWidget(cb, row, col)
                col += 1
                if col >= 2:
                    col = 0
                    row += 1
        
        # Add "All statuses" checkbox
        all_statuses_cb = CheckmarkCheckBox(self.tr("All statuses (with translation)"))
        all_statuses_cb.setChecked(False)
        
        def on_all_statuses_changed(checked):
            for cb in status_checkboxes.values():
                cb.setEnabled(not checked)
        
        all_statuses_cb.toggled.connect(on_all_statuses_changed)
        status_layout.addWidget(all_statuses_cb, row + 1, 0, 1, 2)
        
        layout.addWidget(status_group)
        
        # Target TM group
        tm_group = QGroupBox(self.tr("Target Translation Memories"))
        tm_layout = QVBoxLayout(tm_group)
        
        # Multi-select: one checkbox per writable TM so several can be updated
        # in a single run (was a single-pick combo before). We store the STRING
        # tm_id (e.g. 'patents'), NOT the integer PK (`tm['id']`, e.g. 86): the
        # match-panel read path queries translation_units by string tm_id, so
        # writing under the integer-PK form makes the bulk-written rows invisible
        # to it (surfaced as "segment 306 won't show TM matches even after Update
        # Active TMs" – the row existed, just under the wrong key).
        # update_entry_count and add_translation_unit both expect the string
        # form, so this keeps the whole write path aligned.
        tm_checkboxes = []  # list of (checkbox, tm_id, name)
        for i, tm in enumerate(writable_tms):
            entry_count = tm.get('entry_count', 0)
            cb = CheckmarkCheckBox(f"{tm['name']} ({entry_count} entries)")
            if i == 0:
                cb.setChecked(True)  # default to the first (matches the old default)
            tm_layout.addWidget(cb)
            tm_checkboxes.append((cb, tm['tm_id'], tm['name']))

        # Note about multiple writable TMs
        if len(writable_tms) > 1:
            multi_note = QLabel(
                f"ℹ️ {len(writable_tms)} writable TMs available. Tick every TM you want to send segments to."
            )
            multi_note.setStyleSheet("color: #2196F3; font-size: 10px;")
            tm_layout.addWidget(multi_note)
        
        layout.addWidget(tm_group)
        
        # Options group
        options_group = QGroupBox(self.tr("Options"))
        options_layout = QVBoxLayout(options_group)
        
        overwrite_cb = CheckmarkCheckBox(self.tr("Update existing entries (overwrite if source matches)"))
        overwrite_cb.setChecked(True)
        overwrite_cb.setToolTip(self.tr("If a segment with the same source text exists, update its target."))
        options_layout.addWidget(overwrite_cb)
        
        skip_empty_cb = CheckmarkCheckBox(self.tr("Skip segments with empty translations"))
        skip_empty_cb.setChecked(True)
        options_layout.addWidget(skip_empty_cb)
        
        layout.addWidget(options_group)
        
        # Preview section
        preview_group = QGroupBox(self.tr("Preview"))
        preview_layout = QVBoxLayout(preview_group)
        preview_label = QLabel(self.tr("Click 'Preview' to see how many segments will be sent."))
        preview_label.setStyleSheet("color: #888;")
        preview_layout.addWidget(preview_label)
        
        def update_preview():
            # Count matching segments
            segments_to_check = selected_segments if scope_selected.isChecked() else self.current_project.segments
            
            matching = 0
            for seg in segments_to_check:
                # Check if has translation
                if skip_empty_cb.isChecked() and not seg.target.strip():
                    continue
                
                # Check status
                if all_statuses_cb.isChecked():
                    if seg.target.strip():  # Has any translation
                        matching += 1
                else:
                    for status_key, cb in status_checkboxes.items():
                        if cb.isChecked() and seg.status == status_key:
                            matching += 1
                            break
            
            preview_label.setText(f"✓ {matching} segment(s) will be sent to TM")
            preview_label.setStyleSheet("color: #2e7d32; font-weight: bold;")
        
        preview_btn = QPushButton(self.tr("Preview"))
        preview_btn.clicked.connect(update_preview)
        preview_layout.addWidget(preview_btn)
        
        layout.addWidget(preview_group)
        
        # Buttons
        layout.addStretch()
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText(self.tr("Send to TM"))
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        
        # Perform the operation — send to every ticked TM in one pass.
        selected_tms = [(tm_id, name) for cb, tm_id, name in tm_checkboxes if cb.isChecked()]
        if not selected_tms:
            QMessageBox.information(
                self, self.tr("No TM selected"),
                self.tr("Tick at least one Translation Memory to send segments to."))
            return

        segments_to_check = selected_segments if scope_selected.isChecked() else self.current_project.segments

        # Pre-clean each qualifying segment ONCE (the cleaning is identical for
        # every target TM) so the per-TM write loop stays cheap. Each item is
        # (seg, source_clean, target_clean). skipped_count is per-TM: the same
        # set is skipped for each selected TM.
        skip_empty = skip_empty_cb.isChecked()
        overwrite = overwrite_cb.isChecked()
        all_statuses = all_statuses_cb.isChecked()
        prepared = []
        skipped_count = 0
        for seg in segments_to_check:
            if skip_empty and not seg.target.strip():
                skipped_count += 1
                continue
            should_send = False
            if all_statuses:
                should_send = bool(seg.target.strip())
            else:
                for status_key, cb in status_checkboxes.items():
                    if cb.isChecked() and seg.status == status_key:
                        should_send = True
                        break
            if not should_send:
                skipped_count += 1
                continue
            # Reverse invisible-char display markers (·/→/°/↵) so they never
            # leak into the TM, then strip wrapping/formatting tags.
            source_clean = seg.source
            target_clean = seg.target
            if hasattr(self, 'reverse_invisible_replacements'):
                source_clean = self.reverse_invisible_replacements(source_clean)
                target_clean = self.reverse_invisible_replacements(target_clean)
            if self.hide_outer_wrapping_tags:
                source_clean, _ = strip_outer_wrapping_tags(source_clean)
                target_clean, _ = strip_outer_wrapping_tags(target_clean)
            source_clean = strip_formatting_tags(source_clean) if has_formatting_tags(source_clean) else source_clean
            target_clean = strip_formatting_tags(target_clean) if has_formatting_tags(target_clean) else target_clean
            prepared.append((seg, source_clean, target_clean))

        from PyQt6.QtWidgets import QProgressDialog
        _per_tm = len(prepared)
        _total = _per_tm * len(selected_tms)
        _progress = QProgressDialog(
            self.tr("Updating TMs…"), self.tr("Cancel"), 0, max(_total, 1), self)
        _progress.setWindowTitle(self.tr("Updating TM"))
        _progress.setWindowModality(Qt.WindowModality.WindowModal)
        # Only appears if the run takes longer than ~400 ms.
        _progress.setMinimumDuration(400)
        _progress.setValue(0)
        cancelled = False

        self.status_bar.showMessage(
            self.tr("Sending segments to %d TM(s)…") % len(selected_tms))
        QApplication.processEvents()

        per_tm_sent = {}   # name -> count written
        error_count = 0
        _done = 0
        for target_tm_id, target_tm_name in selected_tms:
            if cancelled:
                break
            sent_this = 0
            for seg, source_clean, target_clean in prepared:
                if _done % 20 == 0:
                    _progress.setValue(_done)
                    _progress.setLabelText(
                        self.tr("Updating '%s'… (%d/%d)") % (target_tm_name, _done + 1, max(_total, 1)))
                    QApplication.processEvents()
                    if _progress.wasCanceled():
                        cancelled = True
                        break
                # v1.10.145: honour the overwrite checkbox — entries with the
                # same source are replaced rather than appended (no duplicates).
                try:
                    self.tm_database.add_entry(
                        source=source_clean,
                        target=target_clean,
                        tm_id=target_tm_id,
                        overwrite=overwrite,
                    )
                    sent_this += 1
                except Exception as add_error:
                    self.log(f"⚠ Failed to add segment {seg.id} to '{target_tm_name}': {add_error}")
                    error_count += 1
                _done += 1
            per_tm_sent[target_tm_name] = sent_this
            # Update entry count for this TM in metadata.
            if hasattr(self, 'tm_metadata_mgr') and self.tm_metadata_mgr:
                try:
                    self.tm_metadata_mgr.update_entry_count(target_tm_id)
                except Exception as e:
                    self.log(f"⚠ Could not update entry count for '{target_tm_name}': {e}")

        _progress.setValue(max(_total, 1))
        _progress.close()

        sent_count = sum(per_tm_sent.values())

        # Refresh TM list if visible.
        if hasattr(self, 'refresh_tm_list'):
            try:
                self.refresh_tm_list()
            except Exception:
                pass

        # v1.10.213: invalidate the segment-keyed match-panel cache so the
        # newly-written TUs surface without a project reopen.
        if hasattr(self, 'invalidate_translation_cache'):
            try:
                self.invalidate_translation_cache(smart_invalidation=False)
            except Exception as e:
                self.log(f"⚠ Could not invalidate match cache: {e}")

        # Report results.
        self.log(f"✓ Sent to {len(selected_tms)} TM(s): {sent_count} write(s) total")
        for name, n in per_tm_sent.items():
            self.log(f"   {name}: {n}")
        if skipped_count > 0:
            self.log(f"  Skipped per TM: {skipped_count} (empty or non-matching status)")
        if error_count > 0:
            self.log(f"  Errors: {error_count}")

        _title = self.tr("Send to TM – Cancelled") if cancelled else self.tr("Send to TM Complete")
        _prefix = self.tr("Operation cancelled before finishing.\n\n") if cancelled else ""
        self.status_bar.showMessage(
            ("⚠ Cancelled – " if cancelled else "✓ ") +
            f"Sent {sent_count} write(s) across {len(selected_tms)} TM(s)", 5000)

        _tm_lines = "\n".join(f"  • {name}: {n}" for name, n in per_tm_sent.items())
        QMessageBox.information(
            self, _title,
            f"{_prefix}"
            f"Sent {sent_count} segment-write(s) across {len(selected_tms)} TM(s):\n"
            f"{_tm_lines}\n\n"
            f"Skipped per TM: {skipped_count}\n"
            f"Errors: {error_count}"
        )

    def initialize_tm_database(self):
        """Initialize TM database when project is loaded"""
        if not self.current_project:
            return
        
        try:
            # Update TM database languages for the current project
            # (tm_database is already initialized at startup)
            if self.tm_database:
                self.tm_database.set_tm_languages(
                    self.current_project.source_lang,
                    self.current_project.target_lang
                )
            
            # Update Superlookup tab with TM database
            if hasattr(self, 'lookup_tab') and self.lookup_tab:
                self.lookup_tab.set_tm_database(self.tm_database)
            
            self.log(f"TM database configured ({self.current_project.source_lang} → {self.current_project.target_lang})")
        except Exception as e:
            self.log(f"Warning: Could not configure TM database: {e}")
            self.tm_database = None

    def search_and_display_tm_matches(self, source_text: str):
        """Search TM and Termbases and display matches with visual diff for fuzzy matches.

        If hide_outer_wrapping_tags is enabled, strips structural tags (like <li-o>, <p>)
        from the query before searching for better TM matching.
        """
        self.log(f"🚨 search_and_display_tm_matches called with source_text: '{source_text[:50]}...'")
        if not source_text or not source_text.strip():
            if hasattr(self, 'tm_display'):
                self.tm_display.clear()
            return

        # Strip outer structural tags if setting is enabled (for cleaner TM matching)
        if self.hide_outer_wrapping_tags:
            source_text, _ = strip_outer_wrapping_tags(source_text)
        
        # Initialize TM if not already done
        if not self.tm_database and self.current_project:
            self.initialize_tm_database()
        
        if not self.tm_database:
            if hasattr(self, 'tm_display'):
                self.tm_display.setHtml(
                    "<p style='color: #999;'><i>Translation Memory not available</i></p>"
                )
            return
        
        try:
            # Get activated TM IDs for current project
            tm_ids = None
            no_tms_activated = False
            tms_wrong_language = False
            if hasattr(self, 'tm_metadata_mgr') and self.tm_metadata_mgr and self.current_project:
                project_id = self.current_project.id if hasattr(self.current_project, 'id') else None
                if project_id:
                    tm_ids = self.tm_metadata_mgr.get_active_tm_ids(project_id)
                    # Check if no TMs are activated for this project
                    if tm_ids is not None and len(tm_ids) == 0:
                        no_tms_activated = True
                    elif tm_ids and len(tm_ids) > 0:
                        # Check if any activated TMs match the project's language pair
                        project_source = (self.current_project.source_lang or '').lower()
                        project_target = (self.current_project.target_lang or '').lower()
                        all_tms = self.tm_metadata_mgr.get_all_tms()
                        has_matching_language = False
                        for tm in all_tms:
                            if tm['id'] in tm_ids:
                                tm_source = (tm.get('source_lang') or '').lower()
                                tm_target = (tm.get('target_lang') or '').lower()
                                # Match if languages align (bidirectional) or TM has no language set
                                if (not tm_source and not tm_target) or \
                                   (tm_source == project_source and tm_target == project_target) or \
                                   (tm_source == project_target and tm_target == project_source):
                                    has_matching_language = True
                                    break
                        if not has_matching_language:
                            tms_wrong_language = True

            # Search for matches (using activated TMs if available)
            matches = self.tm_database.search_all(source_text, tm_ids=tm_ids, max_matches=5)
            
            # Auto-fill exact match if enabled (consolidated setting)
            auto_fill_enabled = self.auto_fill_100_matches

            if matches and auto_fill_enabled:
                first_match = matches[0]
                match_pct = first_match.get('match_pct', 0)
                
                # Check if we have a 100% match
                if match_pct == 100:
                    # Check if current segment is empty/untranslated
                    if hasattr(self, 'table') and hasattr(self, 'current_project'):
                        current_row = self.table.currentRow()
                        if current_row >= 0 and current_row < len(self.current_project.segments):
                            segment = self.current_project.segments[current_row]
                            
                            # Only auto-propagate if segment is empty or untranslated
                            if (not segment.target or not segment.target.strip() or
                                segment.status in ["untranslated", "not_started", ""]):

                                # Fill the segment. When "Mark auto-filled segments
                                # as confirmed" is on, land it as 'confirmed';
                                # otherwise insert as a 'draft' for review.
                                _fill_status = 'confirmed' if getattr(self, 'auto_fill_confirm', False) else 'draft'
                                _undo_old_target = segment.target
                                _undo_old_status = segment.status
                                _undo_new_target = first_match.get('target', '')
                                segment.target = _undo_new_target
                                segment.status = _fill_status
                                segment.modified = True
                                try:
                                    self.record_undo_state(segment.id, _undo_old_target, _undo_new_target, _undo_old_status, _fill_status)
                                except Exception:
                                    pass  # never let undo failure break the actual operation
                                self.project_modified = True
                                
                                # Update grid (using cell widget)
                                target_widget = self.table.cellWidget(current_row, 3)
                                if target_widget and isinstance(target_widget, EditableGridTextEditor):
                                    # Strip outer wrapping tags if setting is enabled
                                    display_text = segment.target
                                    if self.hide_outer_wrapping_tags:
                                        display_text, _ = strip_outer_wrapping_tags(display_text)
                                    target_widget.setPlainText(display_text)

                                # Update status column to reflect draft/confirmed
                                try:
                                    self._refresh_segment_status(segment)
                                except Exception:
                                    pass

                                # Update project modified status (progress is handled by status bar)
                                self.log(f"✨ Auto-filled 100% TM match for segment #{current_row + 1}")
            
            if not matches:
                if hasattr(self, 'tm_display'):
                    if no_tms_activated:
                        # Show helpful message when no TMs are activated
                        self.tm_display.setHtml(
                            f"<p style='color: #666;'><b>Source:</b> {source_text}</p>"
                            f"<p style='color: #E65100;'><i>No TMs activated for this project.</i></p>"
                            f"<p style='color: #999; font-size: 9pt;'>Go to the <b>💾 TMs</b> tab and tick <b>Read</b> to activate translation memories.</p>"
                        )
                    elif tms_wrong_language:
                        # Show message when TMs are activated but don't match project language pair
                        project_lang_pair = f"{self.current_project.source_lang} → {self.current_project.target_lang}" if self.current_project else ""
                        self.tm_display.setHtml(
                            f"<p style='color: #666;'><b>Source:</b> {source_text}</p>"
                            f"<p style='color: #E65100;'><i>Activated TMs don't match project language ({project_lang_pair}).</i></p>"
                            f"<p style='color: #999; font-size: 9pt;'>Go to the <b>💾 TMs</b> tab and tick <b>Read</b> to activate TMs for this language pair.</p>"
                        )
                    else:
                        self.tm_display.setHtml(
                            f"<p style='color: #666;'><b>Source:</b> {source_text}</p>"
                            f"<p style='color: #999;'><i>No translation memory matches found</i></p>"
                        )
                return
            
            # If using TranslationResultsPanel, populate it with TM and Termbase results
            if hasattr(self.assistance_widget, 'set_matches'):
                try:
                    from modules.translation_results_panel import TranslationMatch
                    
                    # Convert TM matches with full TM metadata
                    tm_matches = []
                    for match in matches:
                        # Get full TM metadata from tm_metadata_mgr if available
                        tm_metadata = {
                            'tm_name': match.get('tm_name', 'Unknown TM'),
                            'tm_id': match.get('tm_id', ''),
                            'context': match.get('context', '')
                        }
                        
                        # Fetch full TM details if tm_metadata_mgr is available
                        if hasattr(self, 'tm_metadata_mgr') and self.tm_metadata_mgr:
                            tm_id_str = match.get('tm_id', '')
                            if tm_id_str:
                                # Find TM by tm_id string (not database ID)
                                all_tms = self.tm_metadata_mgr.get_all_tms()
                                for tm in all_tms:
                                    if tm['tm_id'] == tm_id_str:
                                        tm_metadata.update({
                                            'source_lang': tm.get('source_lang', ''),
                                            'target_lang': tm.get('target_lang', ''),
                                            'entry_count': tm.get('entry_count', 0),
                                            'modified_date': tm.get('modified_date', ''),
                                            'description': tm.get('description', '')
                                        })
                                        break
                        
                        tm_match = TranslationMatch(
                            source=match.get('source', ''),
                            target=match.get('target', ''),
                            relevance=int(match.get('match_pct', 0)),
                            metadata=tm_metadata,
                            match_type='TM',
                            compare_source=match.get('source', '')
                        )
                        tm_matches.append(tm_match)
                    
                    # Search for termbase matches
                    termbase_matches = []
                    try:
                        if hasattr(self, 'db_manager') and self.db_manager:
                            # Get current project language pair
                            source_lang = None
                            target_lang = None
                            project_id = None
                            
                            if self.current_project:
                                # current_project is a Project object, not a dict
                                project_id = self.current_project.id if hasattr(self.current_project, 'id') else None
                                source_lang = self.current_project.source_lang if hasattr(self.current_project, 'source_lang') else None
                                target_lang = self.current_project.target_lang if hasattr(self.current_project, 'target_lang') else None
                            
                            # Search for individual words in the source text
                            words = source_text.split()
                            searched_terms = set()  # Track what we've already searched
                            
                            for word in words:
                                # Remove punctuation
                                clean_word = word.strip('.,!?;:')
                                if len(clean_word) < 2 or clean_word in searched_terms:
                                    continue
                                
                                searched_terms.add(clean_word)
                                
                                # Search termbases for this word
                                tb_results = self.db_manager.search_termbases(
                                    clean_word,
                                    source_lang=source_lang,
                                    target_lang=target_lang,
                                    project_id=project_id,
                                    min_length=2
                                )
                                
                                # Convert termbase results to TranslationMatch objects
                                source_text_lower = source_text.lower()
                                import re
                                for tb_match in tb_results:
                                    source_term_full = tb_match.get('source_term', '')
                                    target_term_full = tb_match.get('target_term', '')
                                    if not source_term_full or not target_term_full:
                                        continue
                                    # Require full phrase presence in segment (word-boundary)
                                    pattern = re.compile(r"\b" + re.escape(source_term_full.lower()) + r"\b")
                                    if not pattern.search(source_text_lower):
                                        continue
                                    # Deduplicate by term_id
                                    term_id = tb_match.get('id')
                                    if any(m.metadata.get('term_id') == term_id for m in termbase_matches):
                                        continue
                                    termbase_match = TranslationMatch(
                                        source=source_term_full,
                                        target=target_term_full,
                                        relevance=95,
                                        metadata={
                                            'term_id': term_id,
                                            'termbase_id': tb_match.get('termbase_id'),
                                            'termbase_name': tb_match.get('termbase_name', ''),
                                            'ranking': tb_match.get('ranking', None),
                                            'domain': tb_match.get('domain', ''),
                                            'notes': tb_match.get('notes', ''),
                                            'project': tb_match.get('project', ''),
                                            'client': tb_match.get('client', ''),
                                            'forbidden': tb_match.get('forbidden', False),
                                            'is_project_termbase': tb_match.get('is_project_termbase', False),
                                            'target_synonyms': tb_match.get('target_synonyms', [])
                                        },
                                        match_type='Termbase',
                                        compare_source=source_term_full
                                    )
                                    termbase_matches.append(termbase_match)
                    except Exception as e:
                        self.log(f"Error searching termbases: {e}")

                    
                    # Update the panel with both TM and Termbase matches
                    matches_dict = {}
                    if tm_matches:
                        matches_dict['TM'] = tm_matches
                    if termbase_matches:
                        matches_dict['Termbases'] = termbase_matches
                    
                    if matches_dict and hasattr(self, 'results_panels'):
                        for panel in self.results_panels:
                            try:
                                panel.set_matches(matches_dict)
                            except Exception as e:
                                self.log(f"Error updating panel: {e}")
                    return
                except Exception as e:
                    self.log(f"Error populating TranslationResultsPanel: {e}")
            
            # Fallback: display in tm_display if it exists
            if not hasattr(self, 'tm_display'):
                return
            
            # Build HTML display with diff highlighting
            html = f"<div style='font-family: {self.default_font_family}; font-size: {self.default_font_size}pt;'>"
            html += f"<p style='color: #666; margin-bottom: 10px;'><b>🔍 Searching for:</b><br>{source_text}</p>"
            html += "<hr style='border: 1px solid #ddd;'>"
            
            for idx, match in enumerate(matches, start=1):
                match_pct = match.get('match_pct', 0)
                tm_name = match.get('tm_name', 'Unknown TM')
                source_match = match.get('source', '')
                target_match = match.get('target', '')
                
                # Color based on match percentage
                if match_pct == 100:
                    color = "#22c55e"  # Green for exact match
                    badge_style = "background-color: #22c55e; color: white;"
                elif match_pct >= 95:
                    color = "#3b82f6"  # Blue for high match
                    badge_style = "background-color: #3b82f6; color: white;"
                elif match_pct >= 85:
                    color = "#f59e0b"  # Orange for medium match
                    badge_style = "background-color: #f59e0b; color: white;"
                else:
                    color = "#ef4444"  # Red for low match
                    badge_style = "background-color: #ef4444; color: white;"
                
                html += f"<div style='margin: 10px 0; padding: 10px; background-color: #f9fafb; border-left: 4px solid {color}; border-radius: 4px;'>"
                html += f"<div style='margin-bottom: 5px;'>"
                html += f"<span style='padding: 2px 8px; border-radius: 3px; font-weight: bold; font-size: 10pt; {badge_style}'>{match_pct}%</span> "
                html += f"<span style='color: #666; font-size: 9pt;'>{tm_name}</span>"
                html += "</div>"
                
                # Display source with diff if not 100% match
                if match_pct < 100:
                    diff_html = self.create_diff_html(source_text, source_match)
                    html += f"<p style='margin: 5px 0; color: #444;'><b>TM Source:</b><br>{diff_html}</p>"
                else:
                    html += f"<p style='margin: 5px 0; color: #444;'><b>TM Source:</b><br>{source_match}</p>"
                
                # Display target (clickable to insert)
                html += f"<p style='margin: 5px 0; color: #059669; font-weight: bold;'><b>TM Target:</b><br>"
                html += f"<span style='cursor: pointer; text-decoration: underline;' "
                html += f"onclick='alert(\"{target_match}\")'>{target_match}</span></p>"
                html += "</div>"
            
            html += "</div>"
            self.tm_display.setHtml(html)
            
        except Exception as e:
            self.log(f"Error searching TM: {e}")
            if hasattr(self, 'tm_display'):
                self.tm_display.setHtml(
                    f"<p style='color: #ef4444;'><b>Error:</b> {e}</p>"
                )

    def _save_tab_target_to_tm(self, segment, text):
        """Save tab target text to TM after debounce delay"""
        try:
            # Only save to TM if segment is confirmed (user explicitly approved)
            if segment.status == 'confirmed' and text.strip():
                self.save_segment_to_activated_tms(segment.source, text)
        except Exception as e:
            self.log(f"Warning: Could not save to TM: {e}")

    def _queue_tm_save_log(self, msg: str):
        """Collapse repeated TM-save log messages into a single '(xN)' line.

        This prevents session log spam when confirming many segments.
        Thread-safe: flushes via a background timer and relies on self.log() being thread-safe.
        """
        import threading

        if not hasattr(self, '_tm_save_log_lock'):
            self._tm_save_log_lock = threading.Lock()
            self._tm_save_log_msg = None
            self._tm_save_log_count = 0
            self._tm_save_log_timer = None

        flush_now_msg = None
        flush_now_count = 0

        with self._tm_save_log_lock:
            # If we're switching to a different message, flush the previous one immediately.
            if self._tm_save_log_msg and self._tm_save_log_msg != msg and self._tm_save_log_count:
                flush_now_msg = self._tm_save_log_msg
                flush_now_count = self._tm_save_log_count
                self._tm_save_log_msg = None
                self._tm_save_log_count = 0

            # Accumulate the new message
            if self._tm_save_log_msg == msg:
                self._tm_save_log_count += 1
            else:
                self._tm_save_log_msg = msg
                self._tm_save_log_count = 1

            # Reset flush timer (debounce)
            if self._tm_save_log_timer is not None:
                try:
                    self._tm_save_log_timer.cancel()
                except Exception:
                    pass
                self._tm_save_log_timer = None

            self._tm_save_log_timer = threading.Timer(0.8, self._flush_tm_save_log_threadsafe)
            self._tm_save_log_timer.daemon = True
            self._tm_save_log_timer.start()

        # Flush outside lock
        if flush_now_msg and flush_now_count:
            suffix = f" (x{flush_now_count})" if flush_now_count > 1 else ""
            self.log(f"{flush_now_msg}{suffix}")

    def _flush_tm_save_log_threadsafe(self):
        import threading

        if not hasattr(self, '_tm_save_log_lock'):
            return

        msg = None
        count = 0
        with self._tm_save_log_lock:
            msg = getattr(self, '_tm_save_log_msg', None)
            count = int(getattr(self, '_tm_save_log_count', 0) or 0)
            self._tm_save_log_msg = None
            self._tm_save_log_count = 0
            self._tm_save_log_timer = None

        if msg and count:
            suffix = f" (x{count})" if count > 1 else ""
            self.log(f"{msg}{suffix}")

    def _show_instant_tm_match(self, segment) -> bool:
        """Look up the exact (100%) TM match synchronously and show it in the Match
        Panel *immediately* — independent of the prefetch cache, the termbase, the
        debounce and the async fuzzy worker.

        Exact matching is a single indexed source_hash lookup, so it is fast enough
        to run on the main thread on every click. This is the cure for "I clicked
        into a segment that's definitely in the TM and saw no match": the lookup now
        always runs, the moment you land, no matter how fast you navigate or whether
        a termbase match covers the whole segment. Fuzzy (sub-100%) matches still
        stream in afterwards via the async worker. Returns True if a match was shown.
        """
        try:
            if not getattr(self, 'enable_tm_matching', True):
                return False
            # Always-connected main DB manager (the TM lives in the same file);
            # fall back to the TMDatabase's manager.
            dbm = getattr(self, 'db_manager', None) or getattr(getattr(self, 'tm_database', None), 'db', None)
            if dbm is None or not hasattr(dbm, 'get_exact_match'):
                return False
            # Active TMs straight from the in-memory project settings — no DB query,
            # so this is cheap enough to run on every click, even during filtering.
            tm_ids = None
            if self.current_project and getattr(self.current_project, 'tm_settings', None):
                tm_ids = self.current_project.tm_settings.get('activated_tm_ids')
            if isinstance(tm_ids, list) and len(tm_ids) == 0:
                return False  # no TMs active for this project

            search_source = segment.source
            if self.hide_outer_wrapping_tags:
                search_source, _ = strip_outer_wrapping_tags(search_source)

            # ⚡ DEDUP GUARD: this method is currently driven 2–3× per click
            # (focusInEvent → selectRow → on_selection_changed → on_cell_selected,
            # the manual on_cell_selected in focusInEvent, AND the deferred
            # _on_cell_selected_full). The exact (100%) match for a given segment +
            # source does not change while you sit on it, so once we've shown it we
            # can skip the repeat query + panel rebuild entirely. Cleared on TM
            # refresh / edit via `self._instant_tm_guard = None`.
            _guard_key = (segment.id, search_source)
            if getattr(self, '_instant_tm_guard', None) == _guard_key:
                return True

            # Both directions are single indexed-hash lookups now: forward via
            # idx_tu_source_hash and reverse via idx_tu_target_hash (added in
            # v1.10.280). Together they're well under a millisecond even on a
            # large TM, so it's safe to run the full bidirectional lookup on the
            # UI thread on every click. (v1.10.279 had to skip the reverse
            # direction here because it was an unindexed target_text full scan —
            # the cause of the per-click lag; the target_hash index fixes that,
            # so reverse exact matches are instant again rather than waiting for
            # the background worker.)
            exact = dbm.get_exact_match(
                source=search_source,
                tm_ids=tm_ids,
                source_lang=getattr(getattr(self, 'tm_database', None), 'source_lang', None),
                target_lang=getattr(getattr(self, 'tm_database', None), 'target_lang', None),
                touch=False,
            )
            if not exact:
                # Remember the miss too, so repeat calls in the same click burst
                # don't re-run the query.
                self._instant_tm_guard = _guard_key
                return False

            tm_meta = getattr(self.tm_database, 'tm_metadata', {}) or {}
            _tmid = exact.get('tm_id', '')
            tm_name = tm_meta.get(_tmid, {}).get('name', _tmid) if isinstance(tm_meta, dict) else _tmid
            tm_match = {
                'source': exact.get('source_text', search_source),
                'target': exact.get('target_text', ''),
                'tm_name': tm_name,
                'tm_id': _tmid,
                'match_pct': 100,
                'reverse_match': bool(exact.get('reverse_match', False)),
            }
            self.set_compare_panel_matches(
                segment.id, search_source, [tm_match],
                getattr(self, '_compare_panel_mt_matches', []),
            )

            # Mark this segment+source as shown so the redundant repeat calls in the
            # same click burst short-circuit at the guard above.
            self._instant_tm_guard = _guard_key

            # Keep the prefetch cache consistent so the later cache-hit render (and
            # any re-render) shows the same TM instead of TM=0.
            try:
                from modules.translation_results_panel import TranslationMatch
                mo = TranslationMatch(
                    source=tm_match['source'], target=tm_match['target'], relevance=100,
                    metadata={'tm_name': tm_name, 'tm_id': _tmid,
                              'reverse_match': tm_match['reverse_match']},
                    match_type='TM', compare_source=tm_match['source'], provider_code='TM')
                with self.translation_matches_cache_lock:
                    entry = self.translation_matches_cache.get(segment.id)
                    if entry is not None:
                        entry["TM"] = [mo]
                    else:
                        # No entry yet. It holds no terms (see _segment_terms),
                        # so one with only the TM match says nothing wrong.
                        self.translation_matches_cache[segment.id] = {
                            "LLM": [], "NT": [], "MT": [], "TM": [mo]}
            except Exception:
                pass
            return True
        except Exception:
            return False

    def _on_tm_search_failed(self, segment_id: int, error: str):
        """Report a TM search that raised, instead of showing an empty pane.

        An empty match pane and a broken match pane look exactly the same to the
        user, so a failure that is never surfaced gets read as "my TM has no
        matches" and can go unexplained for months.
        """
        if getattr(self, '_last_tm_search_error', None) != error:
            self._last_tm_search_error = error
            self.log(f"⚠️ TM search failed, so the match pane is empty: {error}")
            self.log("   This is a fault, not an empty TM. Run scripts/sv_tm_diagnose.py for details.")

    def _on_tm_search_results(self, segment_id: int, all_tm_matches: list):
        """Handle TM search results from background TMSearchWorker thread.

        This runs on the main thread (via signal) so it's safe to update UI.
        """
        try:
            from modules.translation_results_panel import TranslationMatch

            # Validate we're still on the same segment
            current_row = self.table.currentRow() if hasattr(self, 'table') and self.table else -1
            current_segment_id = None
            if current_row >= 0:
                id_item = self.table.item(current_row, 0)
                if id_item:
                    try:
                        current_segment_id = int(id_item.text())
                    except (ValueError, AttributeError):
                        pass

            if all_tm_matches:
                self.log(f"🔍 TM: Found {len(all_tm_matches)} matches for segment {segment_id}")

            # Convert to TranslationMatch objects
            tm_match_objects = []
            for match in all_tm_matches:
                # v1.10.51: reverse_match flows through from the db layer's
                # bidirectional fallback. Older code here tried to derive
                # direction from a "Reverse" substring in the TM name, but
                # that heuristic was unreliable; the explicit flag is correct.
                reverse_match = bool(match.get('reverse_match', False))
                match_obj = TranslationMatch(
                    source=match.get('source', ''),
                    target=match.get('target', ''),
                    relevance=match.get('match_pct', 0),
                    metadata={
                        'context': match.get('context', ''),
                        'tm_name': match.get('tm_name', ''),
                        'tm_id': match.get('tm_id', ''),
                        'timestamp': match.get('created_at', ''),
                        'direction': 'reverse' if reverse_match else 'primary',
                        # Explicit flag for the Match Panel chip renderer
                        # (md.get('reverse_match', False) in the renderer).
                        'reverse_match': reverse_match,
                    },
                    match_type='TM',
                    compare_source=match.get('source', ''),
                    provider_code='TM'
                )
                tm_match_objects.append(match_obj)

            # Cache results regardless of whether user moved
            if tm_match_objects:
                with self.translation_matches_cache_lock:
                    entry = self.translation_matches_cache.get(segment_id)
                    if entry is not None:
                        entry["TM"] = tm_match_objects
                    else:
                        self.translation_matches_cache[segment_id] = {
                            "LLM": [], "NT": [], "MT": [], "TM": tm_match_objects}

            # If user moved to a different segment, don't update UI
            if current_segment_id is not None and current_segment_id != segment_id:
                return

            # Show TM matches in results panels
            if tm_match_objects:
                tm_only = {"TM": tm_match_objects}
                if hasattr(self, 'results_panels') and self.results_panels:
                    for panel in self.results_panels:
                        try:
                            panel.add_matches(tm_only)
                        except Exception as e:
                            self.log(f"Error adding TM matches: {e}")

                # Update Match Panel with TM matches
                tm_matches_for_panel = []
                for tm in tm_match_objects:
                    tm_name = tm.metadata.get('tm_name', 'TM') if tm.metadata else 'TM'
                    tm_id = tm.metadata.get('tm_id', '') if tm.metadata else ''
                    # v1.10.51: surface the bidirectional reverse-match flag
                    # so the Match Panel's metadata-line renderer can show
                    # its "⇄ reversed" chip. Previously the panel-bridge dict
                    # was built without this field, so the chip never showed
                    # even after the lower layers were patched.
                    rev = bool(tm.metadata.get('reverse_match', False)) if tm.metadata else False
                    tm_matches_for_panel.append({
                        'source': tm.compare_source or tm.source,
                        'target': tm.target,
                        'tm_name': tm_name,
                        'tm_id': tm_id,
                        'match_pct': int(tm.relevance),
                        'reverse_match': rev,
                    })
                self._compare_panel_tm_matches = tm_matches_for_panel
                self.set_compare_panel_matches(
                    segment_id,
                    self._tm_search_pending_segment.source if hasattr(self, '_tm_search_pending_segment') else '',
                    tm_matches=tm_matches_for_panel,
                    mt_matches=getattr(self, '_compare_panel_mt_matches', [])
                )

                # Auto-insert 100% TM match (if enabled)
                segment = getattr(self, '_tm_search_pending_segment', None)
                if segment and segment.id == segment_id and self.auto_insert_100_percent_matches:
                    target_empty = not segment.target or len(segment.target.strip()) == 0
                    if target_empty:
                        best_match = None
                        for tm_match in tm_match_objects:
                            if float(tm_match.relevance) >= 99.5:
                                best_match = tm_match
                                break
                        if best_match:
                            self._auto_insert_tm_match(segment, best_match.target, None)
                            self._play_sound_effect('tm_100_percent_match')

                # Play fuzzy match sound
                has_100_match = any(float(tm.relevance) >= 99.5 for tm in tm_match_objects)
                has_fuzzy_match = any(float(tm.relevance) < 99.5 and float(tm.relevance) >= 50 for tm in tm_match_objects)
                if has_fuzzy_match and not has_100_match:
                    self._play_sound_effect('tm_fuzzy_match')

        except Exception as e:
            self.log(f"Error handling TM search results: {e}")

    def _auto_insert_tm_match(self, segment, target_text, row=None):
        """
        Auto-insert a 100% TM match into the target field.
        Works for both grid and tab views.
        
        Args:
            segment: The segment object to update
            target_text: The translation text to insert
            row: The grid row number (if known), or None to search
        """
        try:
            # When "Mark auto-filled segments as confirmed" is on, the auto-filled
            # target lands as 'confirmed'; otherwise it stays a 'draft' for review.
            fill_status = 'confirmed' if getattr(self, 'auto_fill_confirm', False) else 'draft'
            self.log(f"🔧 Auto-insert: Starting for segment {segment.id} at row {row}, target='{target_text[:50]}...'")
            self.log(f"🔧 Auto-insert: BEFORE - segment.id={segment.id}, segment object ID={id(segment)}, old_target='{segment.target[:30] if segment.target else 'EMPTY'}'")

            # CRITICAL: Update segment data directly
            # The segment parameter IS a reference to the object in self.current_project.segments
            # No need to look it up - just modify it directly!
            _undo_old_target = segment.target
            _undo_old_status = segment.status
            segment.target = target_text
            segment.status = fill_status
            try:
                self.record_undo_state(segment.id, _undo_old_target, target_text, _undo_old_status, fill_status)
            except Exception:
                pass  # never let undo failure break the actual operation
            self.project_modified = True
            self.log(f"🔧 Auto-insert: AFTER - segment.id={segment.id}, segment object ID={id(segment)}, new_target='{segment.target[:30] if segment.target else 'EMPTY'}'")
            self.log(f"🔧 Auto-insert: Updated segment.target, status={fill_status}")
            
            # Update grid view if visible
            if hasattr(self, 'table') and self.table:
                # If row is provided, use it directly
                if row is not None and 0 <= row < self.table.rowCount():
                    self.log(f"🔧 Auto-insert: Using provided row {row}")
                    target_row = row
                else:
                    # Search for the row by segment ID (stored as text in column 0)
                    self.log(f"🔧 Auto-insert: Searching {self.table.rowCount()} rows for segment {segment.id}")
                    target_row = None
                    for r in range(self.table.rowCount()):
                        row_item = self.table.item(r, 0)
                        if row_item and row_item.text() == str(segment.id):
                            target_row = r
                            self.log(f"🔧 Auto-insert: Found segment at row {r}")
                            break
                
                if target_row is not None:
                    # Update target editor widget
                    target_widget = self.table.cellWidget(target_row, 3)
                    if target_widget:
                        self.log(f"🔧 Auto-insert: Got target widget, type={type(target_widget).__name__}")
                        if hasattr(target_widget, 'setPlainText'):
                            # CRITICAL: Do NOT block signals - we need textChanged to update segment.target
                            # The segment was already updated above (line ~13855), but the widget needs to trigger
                            # the handler so they stay in sync
                            target_widget.setPlainText(target_text)
                            self.log(f"🔧 Auto-insert: Set widget text to '{target_text[:50]}...'")
                        else:
                            self.log(f"⚠️ Auto-insert: Widget has no setPlainText method!")
                    else:
                        self.log(f"⚠️ Auto-insert: No target widget found at row {target_row}, col 3")
                    
                    # Update status icon
                    self.update_status_icon(target_row, fill_status)
                    self.log(f"🔧 Auto-insert: Updated status icon to '{fill_status}'")
                    
                    # Auto-resize the row to fit the new content
                    self._auto_resize_single_row(target_row)
                else:
                    self.log(f"⚠️ Auto-insert: Could not find row for segment {segment.id}!")
            else:
                self.log(f"⚠️ Auto-insert: No table attribute or table is None")
            
            self.log(f"✅ Auto-inserted 100% TM match into segment {segment.id}")
            
        except Exception as e:
            self.log(f"⚠ Error auto-inserting TM match: {e}")
