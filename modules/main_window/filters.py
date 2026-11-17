"""Filtering and sorting the grid: text and file filters, quick and
advanced filters, empty segments, and saved views.

Part of the main window (SupervertalerQt), moved here unchanged. The
methods run as the main window's own: `self` is the SupervertalerQt.
"""

import re
import traceback
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QTextCharFormat, QTextCursor
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)
from modules.grid_editors import EditableGridTextEditor, ReadOnlyGridTextEditor
from modules.main_dialogs import AdvancedFiltersDialog
from modules.shortcut_display import format_shortcut_for_display
from modules.styled_widgets import CheckmarkCheckBox


class FiltersMixin:
    def _clear_filter_highlights_in_widget(self, row: int, col: int):
        """Remove yellow filter highlights from a QTextEdit widget while preserving other formatting.
        
        This is more efficient than reloading the entire grid just to clear highlights.
        """
        widget = self.table.cellWidget(row, col)
        if not widget or not hasattr(widget, 'document'):
            return
        
        # Iterate through the document and clear only yellow backgrounds
        document = widget.document()
        cursor = QTextCursor(document)
        cursor.movePosition(QTextCursor.MoveOperation.Start)
        
        # Select the entire document
        cursor.movePosition(QTextCursor.MoveOperation.End, QTextCursor.MoveMode.KeepAnchor)
        
        # Get current block and iterate character by character
        # For efficiency, we'll just clear all backgrounds that are yellow
        # This preserves termbase highlights (which are green shades) and tag colors
        block = document.begin()
        while block.isValid():
            it = block.begin()
            while not it.atEnd():
                fragment = it.fragment()
                if fragment.isValid():
                    fmt = fragment.charFormat()
                    bg = fmt.background().color()
                    # Check if it's a yellow highlight (filter highlight)
                    if bg.name().upper() == "#FFFF00":
                        # Clear this specific range
                        cursor.setPosition(fragment.position())
                        cursor.setPosition(fragment.position() + fragment.length(), QTextCursor.MoveMode.KeepAnchor)
                        clear_fmt = QTextCharFormat()
                        clear_fmt.setBackground(QColor(Qt.GlobalColor.transparent))
                        cursor.mergeCharFormat(clear_fmt)
                it += 1
            block = block.next()

    def apply_filters(self):
        """Apply source and target filters to show/hide rows and highlight matches.
        
        OPTIMIZED: No longer reloads the entire grid. Instead:
        1. Clears only filter highlights (preserves termbase/tag formatting)
        2. Shows/hides rows in place
        3. Applies new highlights to matching text
        """
        if not self.current_project:
            return
        
        # Safety check: ensure table and filter widgets exist
        if not hasattr(self, 'table') or self.table is None:
            return
        has_source = self._widget_is_alive(getattr(self, 'source_filter', None))
        has_target = self._widget_is_alive(getattr(self, 'target_filter', None))
        if not has_source and not has_target:
            return

        source_filter_text = self._get_line_edit_text('source_filter').strip()
        target_filter_text = self._get_line_edit_text('target_filter').strip()
        
        # If both empty, clear everything
        if not source_filter_text and not target_filter_text:
            self.clear_filters()
            return
        
        # Set flag to disable auto-center scrolling during filtering
        self.filtering_active = True

        # Track which rows match the active text filters so pagination can respect filtering
        matching_rows: set[int] = set()
        
        # Batch UI updates for performance
        self.table.setUpdatesEnabled(False)
        
        try:
            visible_count = 0
            segments = self.current_project.segments
            total_segments = len(segments)
            
            # Plain text is a case-insensitive substring; /pattern/ is a regex
            from modules.grid_filter import GridFilter
            source_spec = GridFilter(source_filter_text)
            target_spec = GridFilter(target_filter_text)
            
            # IMPORTANT: Always search through ALL segments, not just visible rows
            # Pagination state should not affect which segments we search
            for row in range(total_segments):
                if row >= total_segments:
                    break
                    
                segment = segments[row]
                source_match = source_spec.matches(segment.source)
                target_match = target_spec.matches(segment.target)
                
                show_row = source_match and target_match

                if show_row:
                    matching_rows.add(row)
                
                if show_row:
                    visible_count += 1
                    
                    # Clear previous filter highlights first (only yellow, preserves other formatting)
                    self._clear_filter_highlights_in_widget(row, 2)
                    self._clear_filter_highlights_in_widget(row, 3)
                    
                    # Highlight matching terms in the QTextEdit widgets
                    if source_spec:
                        self._highlight_text_in_widget(row, 2, source_spec)
                    if target_spec:
                        self._highlight_text_in_widget(row, 3, target_spec)
                else:
                    # Clear highlights from hidden rows too (for when they become visible again)
                    self._clear_filter_highlights_in_widget(row, 2)
                    self._clear_filter_highlights_in_widget(row, 3)
        finally:
            # Re-enable UI updates
            self.table.setUpdatesEnabled(True)

        # Persist allowlist and apply combined pagination+filter visibility
        self._active_text_filter_rows = matching_rows
        if hasattr(self, '_apply_pagination_to_grid'):
            self._apply_pagination_to_grid()

        # Update status
        if source_filter_text or target_filter_text:
            self.log(f"Filter applied: showing {visible_count} of {len(self.current_project.segments)} segments")
        # After the log line, which also writes to the status bar
        for spec in (source_spec, target_spec):
            if spec.error:
                self.statusBar().showMessage(
                    f"⚠ Invalid regular expression {spec.text}: {spec.error} "
                    "– matching it as plain text", 8000)

    def clear_filters(self):
        """Clear all filter boxes, highlighting, and show all rows.
        
        OPTIMIZED: No longer reloads the entire grid. Instead:
        1. Clears only yellow filter highlights (preserves termbase/tag formatting)
        2. Shows all rows in place
        3. Much faster than reloading all widgets
        """
        source_widget = getattr(self, 'source_filter', None)
        target_widget = getattr(self, 'target_filter', None)
        has_source = self._widget_is_alive(source_widget)
        has_target = self._widget_is_alive(target_widget)
        if not has_source and not has_target:
            return

        # Clear delegate highlights and global search terms
        if hasattr(self, 'table') and self.table is not None:
            delegate = self.table.itemDelegate()
            if delegate:
                if hasattr(delegate, 'clear_all_highlights'):
                    delegate.clear_all_highlights()
                # Clear global search terms
                delegate.global_search_term = None
                delegate.global_source_search_term = None

        # Remember which segment was selected before clearing
        selected_segment_id = None
        current_column = 3  # Default to target column
        if self.current_project and hasattr(self, 'table') and self.table is not None:
            current_row = self.table.currentRow()
            current_column = self.table.currentColumn()
            
            if current_row >= 0:
                # Get segment ID directly from the ID cell (column 0)
                id_item = self.table.item(current_row, 0)
                if id_item:
                    try:
                        selected_segment_id = int(id_item.text())
                    except (ValueError, AttributeError):
                        pass

        if has_source:
            source_widget.blockSignals(True)
            source_widget.clear()
            source_widget.blockSignals(False)
        else:
            self.source_filter = None

        if has_target:
            target_widget.blockSignals(True)
            target_widget.clear()
            target_widget.blockSignals(False)
        else:
            self.target_filter = None

        # Clear any active text-filter allowlist so pagination can show rows normally
        self._active_text_filter_rows = None
        
        # OPTIMIZED: Clear highlights and show rows WITHOUT reloading grid
        if self.current_project:
            # Safety check: ensure table exists
            if not hasattr(self, 'table') or self.table is None:
                return
            
            # Batch UI updates for performance
            self.table.setUpdatesEnabled(False)
            
            try:
                row_count = self.table.rowCount()
                
                for row in range(row_count):
                    # Clear yellow filter highlights (preserves other formatting)
                    self._clear_filter_highlights_in_widget(row, 2)  # Source
                    self._clear_filter_highlights_in_widget(row, 3)  # Target
                    
                    # Show all rows
                    self.table.setRowHidden(row, False)
            finally:
                # Re-enable UI updates
                self.table.setUpdatesEnabled(True)
            
            # Clear filtering flag to re-enable auto-center
            self.filtering_active = False

            # Resolve the previously selected segment's row index BEFORE we
            # re-apply pagination. If pagination is on, we need to switch to
            # the page that contains this row first; otherwise the row stays
            # hidden and the scroll/select call below silently no-ops on a
            # hidden cell, leaving the user looking at page 1 even though
            # they were working at the end of the project.
            target_row = None
            if selected_segment_id is not None:
                for row, segment in enumerate(self.current_project.segments):
                    if segment.id == selected_segment_id:
                        target_row = row
                        break

            page_size = getattr(self, 'grid_page_size', 50)
            if target_row is not None and page_size and page_size < 999999:
                target_page = target_row // page_size
                if getattr(self, 'grid_current_page', 0) != target_page:
                    self.grid_current_page = target_page

            # Re-apply pagination after clearing filters (also resizes visible rows)
            if hasattr(self, '_apply_pagination_to_grid'):
                self._apply_pagination_to_grid()

            # Restore selection to the previously selected segment.
            # PositionAtCenter centres it in the viewport so the user lands
            # back where they were looking, not at the top of the page.
            if target_row is not None:
                self.table.setCurrentCell(target_row, current_column)
                self.table.scrollToItem(
                    self.table.item(target_row, 0),
                    QTableWidget.ScrollHint.PositionAtCenter
                )
        
        # Reset file filter to "All Files"
        if hasattr(self, 'file_filter_combo') and self.file_filter_combo:
            self.file_filter_combo.blockSignals(True)
            self.file_filter_combo.setCurrentIndex(0)
            self.file_filter_combo.blockSignals(False)
        
        self.log("Filters cleared")

    def _on_file_filter_changed(self, index):
        """Handle file filter dropdown change for multi-file projects."""
        if not self.current_project or not hasattr(self, 'file_filter_combo'):
            return

        data = self.file_filter_combo.currentData()

        # "Manage Views..." action
        if data == "manage_views":
            # Reset to previous selection (All Files) before opening dialog
            self.file_filter_combo.blockSignals(True)
            self.file_filter_combo.setCurrentIndex(0)
            self.file_filter_combo.blockSignals(False)
            self.show_manage_views_dialog()
            return

        if data is None:
            # "All Files" selected - re-apply pagination (keeps empty segments hidden)
            if hasattr(self, '_apply_pagination_to_grid'):
                self._apply_pagination_to_grid()
            self._update_file_boundary_labels()
            self.log("File filter: showing all files")
            return

        if not hasattr(self, 'table') or not self.table:
            return

        # View selected (dict with view_file_ids)
        if isinstance(data, dict) and 'view_file_ids' in data:
            view_file_ids = set(data['view_file_ids'])
            visible_count = 0
            for row, segment in enumerate(self.current_project.segments):
                if row >= self.table.rowCount():
                    break
                segment_file_id = getattr(segment, 'file_id', None)
                show_row = segment_file_id in view_file_ids and segment.source.strip()
                self.table.setRowHidden(row, not show_row)
                if show_row:
                    visible_count += 1
            self.log(f"View filter: showing {visible_count} segments from {len(view_file_ids)} files")
            self._update_file_boundary_labels()
            return

        # Single file selected (int file_id)
        file_id = data
        visible_count = 0
        for row, segment in enumerate(self.current_project.segments):
            if row >= self.table.rowCount():
                break
            segment_file_id = getattr(segment, 'file_id', None)
            show_row = segment_file_id == file_id and segment.source.strip()
            self.table.setRowHidden(row, not show_row)
            if show_row:
                visible_count += 1

        file_name = "Unknown"
        files = getattr(self.current_project, 'files', [])
        for f in files:
            if f['id'] == file_id:
                file_name = f['name']
                break

        self.log(f"File filter: showing {visible_count} segments from '{file_name}'")
        self._update_file_boundary_labels()

    def _update_file_filter_combo(self):
        """Update the file filter dropdown with files and views from the current project."""
        if not hasattr(self, 'file_filter_combo') or not self.file_filter_combo:
            return

        self.file_filter_combo.blockSignals(True)
        self.file_filter_combo.clear()
        self.file_filter_combo.addItem("All Files", None)

        is_multifile = getattr(self.current_project, 'is_multifile', False) if self.current_project else False
        files = getattr(self.current_project, 'files', []) if self.current_project else []

        if is_multifile and files:
            # Saved views section
            views = getattr(self.current_project, 'views', []) or []
            if views:
                self.file_filter_combo.insertSeparator(self.file_filter_combo.count())
                for view in views:
                    view_name = view.get('name', 'Unnamed View')
                    file_count = len(view.get('file_ids', []))
                    self.file_filter_combo.addItem(
                        f"\U0001F441 {view_name} ({file_count} files)",
                        {"view_file_ids": view.get('file_ids', [])}
                    )

            # Individual files section
            self.file_filter_combo.insertSeparator(self.file_filter_combo.count())
            for file_info in files:
                file_id = file_info['id']
                file_name = file_info['name']
                seg_count = file_info.get('segment_count', 0)
                self.file_filter_combo.addItem(f"\U0001F4C4 {file_name} ({seg_count} seg)", file_id)

            # Manage Views action
            self.file_filter_combo.insertSeparator(self.file_filter_combo.count())
            self.file_filter_combo.addItem("Manage Views...", "manage_views")

            self.file_filter_combo.show()
        else:
            self.file_filter_combo.hide()

        self.file_filter_combo.blockSignals(False)

    def show_manage_views_dialog(self):
        """Show dialog to create, edit, and delete views for multi-file projects."""
        from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel,
                                      QTableWidget, QTableWidgetItem, QHeaderView,
                                      QPushButton, QLineEdit, QCheckBox, QGroupBox,
                                      QInputDialog)
        from PyQt6.QtCore import Qt

        if not self.current_project or not getattr(self.current_project, 'is_multifile', False):
            QMessageBox.information(self, "Not Available",
                                    "Views are only available for multi-file projects.")
            return

        proj = self.current_project
        files = getattr(proj, 'files', []) or []
        if not files:
            return

        dialog = QDialog(self)
        dialog.setWindowTitle(self.tr("Manage Views"))
        dialog.setMinimumWidth(550)
        dialog.setMinimumHeight(400)

        layout = QVBoxLayout(dialog)

        info_label = QLabel(
            "Views let you work on a subset of files in a multi-file project.\n"
            "Create a view by selecting files, then use the file filter dropdown to activate it."
        )
        info_label.setWordWrap(True)
        layout.addWidget(info_label)

        layout.addSpacing(10)

        # ─── Existing Views Table ───
        views = getattr(proj, 'views', []) or []

        views_group = QGroupBox(f"Saved Views ({len(views)})")
        views_layout = QVBoxLayout(views_group)

        views_table = QTableWidget()
        views_table.setColumnCount(3)
        views_table.setHorizontalHeaderLabels(["View Name", "Files", "Segments"])
        views_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        views_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        views_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        views_table.setAlternatingRowColors(True)

        def refresh_views_table():
            current_views = getattr(proj, 'views', []) or []
            views_table.setRowCount(len(current_views))
            views_group.setTitle(f"Saved Views ({len(current_views)})")
            for row, view in enumerate(current_views):
                views_table.setItem(row, 0, QTableWidgetItem(view.get('name', '')))
                view_file_ids = set(view.get('file_ids', []))
                file_names = [f.get('name', '?') for f in files if f['id'] in view_file_ids]
                views_table.setItem(row, 1, QTableWidgetItem(", ".join(file_names)))
                seg_count = sum(
                    1 for s in proj.segments
                    if getattr(s, 'file_id', None) in view_file_ids
                )
                views_table.setItem(row, 2, QTableWidgetItem(str(seg_count)))

        refresh_views_table()
        views_layout.addWidget(views_table)

        # View action buttons
        view_btn_layout = QHBoxLayout()

        def delete_selected_view():
            row = views_table.currentRow()
            if row < 0:
                return
            current_views = getattr(proj, 'views', []) or []
            if row >= len(current_views):
                return
            view_name = current_views[row].get('name', 'this view')
            reply = QMessageBox.question(
                dialog, "Delete View",
                f"Delete view \"{view_name}\"?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            if reply == QMessageBox.StandardButton.Yes:
                current_views.pop(row)
                proj.views = current_views
                refresh_views_table()
                self._update_file_filter_combo()

        delete_btn = QPushButton(self.tr("Delete Selected"))
        delete_btn.clicked.connect(delete_selected_view)
        view_btn_layout.addWidget(delete_btn)
        view_btn_layout.addStretch()
        views_layout.addLayout(view_btn_layout)

        layout.addWidget(views_group)

        # ─── Create New View ───
        create_group = QGroupBox(self.tr("Create New View"))
        create_layout = QVBoxLayout(create_group)

        name_layout = QHBoxLayout()
        name_layout.addWidget(QLabel(self.tr("Name:")))
        name_input = QLineEdit()
        name_input.setPlaceholderText(self.tr("e.g. Chapters 1-3"))
        name_layout.addWidget(name_input)
        create_layout.addLayout(name_layout)

        create_layout.addWidget(QLabel(self.tr("Select files to include:")))

        # File checkboxes
        file_checkboxes = []
        for file_info in files:
            file_id = file_info['id']
            file_name = file_info['name']
            seg_count = file_info.get('segment_count', 0)
            cb = CheckmarkCheckBox(f"{file_name} ({seg_count} segments)")
            cb.setProperty("file_id", file_id)
            file_checkboxes.append(cb)
            create_layout.addWidget(cb)

        # Select all / none
        sel_layout = QHBoxLayout()
        select_all_btn = QPushButton(self.tr("Select All"))
        select_all_btn.clicked.connect(lambda: [cb.setChecked(True) for cb in file_checkboxes])
        select_none_btn = QPushButton(self.tr("Select None"))
        select_none_btn.clicked.connect(lambda: [cb.setChecked(False) for cb in file_checkboxes])
        sel_layout.addWidget(select_all_btn)
        sel_layout.addWidget(select_none_btn)
        sel_layout.addStretch()
        create_layout.addLayout(sel_layout)

        def create_view():
            name = name_input.text().strip()
            if not name:
                QMessageBox.warning(dialog, "Name Required", "Please enter a name for the view.")
                return
            selected_ids = [
                cb.property("file_id") for cb in file_checkboxes if cb.isChecked()
            ]
            if not selected_ids:
                QMessageBox.warning(dialog, "No Files Selected",
                                    "Please select at least one file for the view.")
                return
            if proj.views is None:
                proj.views = []
            proj.views.append({"name": name, "file_ids": selected_ids})
            name_input.clear()
            for cb in file_checkboxes:
                cb.setChecked(False)
            refresh_views_table()
            self._update_file_filter_combo()
            self.log(f"Created view '{name}' with {len(selected_ids)} files")

        create_btn = QPushButton(self.tr("Create View"))
        create_btn.clicked.connect(create_view)
        create_layout.addWidget(create_btn)

        layout.addWidget(create_group)

        # ─── Close button ───
        button_layout = QHBoxLayout()
        button_layout.addStretch()
        close_btn = QPushButton(self.tr("Close"))
        close_btn.clicked.connect(dialog.accept)
        button_layout.addWidget(close_btn)
        layout.addLayout(button_layout)

        dialog.exec()

    def filter_empty_segments(self):
        """Quick filter to show only segments with empty target"""
        if not self.current_project:
            return
        
        # Safety check: ensure table exists
        if not hasattr(self, 'table') or self.table is None:
            return
        
        # Clear filter boxes first
        source_widget = getattr(self, 'source_filter', None)
        target_widget = getattr(self, 'target_filter', None)
        if self._widget_is_alive(source_widget):
            source_widget.blockSignals(True)
            source_widget.clear()
            source_widget.blockSignals(False)
        if self._widget_is_alive(target_widget):
            target_widget.blockSignals(True)
            target_widget.clear()
            target_widget.blockSignals(False)
        
        # OPTIMIZED: Batch UI updates and don't reload grid
        self.table.setUpdatesEnabled(False)
        try:
            # Clear any yellow text filter highlights (but preserve termbase/tag formatting)
            self._clear_all_filter_highlights()
            
            # Hide rows with non-empty target (empty structural segments always hidden)
            visible_count = 0
            for row, segment in enumerate(self.current_project.segments):
                if row >= self.table.rowCount():
                    break

                has_empty_target = not segment.target or not segment.target.strip()
                show_row = has_empty_target and segment.source.strip()
                self.table.setRowHidden(row, not show_row)

                if show_row:
                    visible_count += 1
        finally:
            self.table.setUpdatesEnabled(True)
        
        self.log(f"🔍 Empty segments filter: showing {visible_count} of {len(self.current_project.segments)} segments")

    def _clear_all_filter_highlights(self):
        """Clear yellow filter highlights from all cells without reloading grid"""
        if not hasattr(self, 'table') or self.table is None:
            return
        
        for row in range(self.table.rowCount()):
            # Clear source column (2)
            self._clear_filter_highlights_in_widget(row, 2)
            
            # Clear target column (3)
            self._clear_filter_highlights_in_widget(row, 3)

    def apply_quick_filter(self, filter_type: str):
        """Apply quick filter based on type - integrates with pagination system"""
        if not self.current_project:
            return

        if not hasattr(self, 'table') or self.table is None:
            return

        # Clear filter boxes first
        source_widget = getattr(self, 'source_filter', None)
        target_widget = getattr(self, 'target_filter', None)
        if self._widget_is_alive(source_widget):
            source_widget.blockSignals(True)
            source_widget.clear()
            source_widget.blockSignals(False)
        if self._widget_is_alive(target_widget):
            target_widget.blockSignals(True)
            target_widget.clear()
            target_widget.blockSignals(False)

        # Clear any yellow text filter highlights (but preserve termbase/tag formatting)
        self._clear_all_filter_highlights()

        # Calculate matching rows - collect into a set for pagination integration
        matching_rows = set()
        for row, segment in enumerate(self.current_project.segments):
            show_row = False

            if filter_type == "empty":
                show_row = not segment.target or not segment.target.strip()
            elif filter_type == "not_started":
                show_row = segment.status in ["not_started", "draft"]
            elif filter_type == "confirmed":
                show_row = segment.status == "confirmed"
            elif filter_type == "locked":
                show_row = getattr(segment, 'locked', False)
            elif filter_type == "not_locked":
                show_row = not getattr(segment, 'locked', False)
            elif filter_type == "commented":
                show_row = bool(segment.notes and segment.notes.strip())

            if show_row:
                matching_rows.add(row)

        # Integrate with pagination system - this ensures the filter persists
        # when other UI events trigger pagination updates
        self._active_text_filter_rows = matching_rows
        self.filtering_active = True

        # Apply the filter through the pagination system
        if hasattr(self, '_apply_pagination_to_grid'):
            self._apply_pagination_to_grid()

        filter_names = {
            "empty": "Empty segments",
            "not_started": "Not started",
            "confirmed": "Confirmed",
            "locked": "Locked",
            "not_locked": "Not locked",
            "commented": "Commented"
        }
        self.log(f"🔍 {filter_names.get(filter_type, 'Quick')} filter: showing {len(matching_rows)} of {len(self.current_project.segments)} segments")

    def _update_bulk_menu_label(self):
        """Update the Bulk Operations menu label to show filter state.

        v1.10.234: use the translated base label via self.tr(...) instead of a
        hard-coded English literal. This handler runs on the menu's aboutToShow,
        so the previous literal "&Bulk Operations" overwrote the translated menu
        title every time the menu was opened — the title showed correctly
        translated when the menu was closed, then reverted to English when
        opened (reported by a user on the Simplified/Traditional Chinese UI).
        """
        if not hasattr(self, 'bulk_menu') or not self.bulk_menu:
            return
        action = self.bulk_menu.menuAction()
        base = self.tr("&Bulk Operations")  # honour the active UI translation
        if not self.current_project or not hasattr(self, 'table') or not self.table:
            action.setText(base)
            return
        visible = sum(1 for r in range(self.table.rowCount())
                      if not self.table.isRowHidden(r))
        total = self.table.rowCount()
        if visible < total and visible > 0:
            # Translatable filtered-count suffix; falls back to English "(N filtered)"
            # if the active locale hasn't translated the suffix string.
            suffix = self.tr(" ({n} filtered)").format(n=visible)
            action.setText(base + suffix)
        else:
            action.setText(base)

    def show_advanced_filters_dialog(self):
        """Show advanced filters dialog with detailed filtering options"""
        if not self.current_project:
            QMessageBox.information(self, "No Project", "Please open or create a project first.")
            return
        
        dialog = AdvancedFiltersDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            filters = dialog.get_filters()
            self.apply_advanced_filters(filters)

    def apply_advanced_filters(self, filters: dict):
        """Apply advanced filters to grid - OPTIMIZED for speed"""
        if not self.current_project or not hasattr(self, 'table') or self.table is None:
            return
        
        # Clear text filter boxes
        source_widget = getattr(self, 'source_filter', None)
        target_widget = getattr(self, 'target_filter', None)
        if self._widget_is_alive(source_widget):
            source_widget.blockSignals(True)
            source_widget.clear()
            source_widget.blockSignals(False)
        if self._widget_is_alive(target_widget):
            target_widget.blockSignals(True)
            target_widget.clear()
            target_widget.blockSignals(False)
        
        # OPTIMIZED: Batch UI updates and don't reload grid
        self.table.setUpdatesEnabled(False)
        try:
            # Clear any yellow text filter highlights (but preserve termbase/tag formatting)
            self._clear_all_filter_highlights()
            
            visible_count = 0
            for row, segment in enumerate(self.current_project.segments):
                if row >= self.table.rowCount():
                    break
                
                show_row = True
                
                # Match rate filter
                if filters.get('match_rate_enabled'):
                    match_percent = getattr(segment, 'match_percent', 0) or 0
                    min_rate = filters.get('match_rate_min', 0)
                    max_rate = filters.get('match_rate_max', 102)
                    if not (min_rate <= match_percent <= max_rate):
                        show_row = False
                
                # Row status filters
                status_filters = filters.get('row_status', [])
                if status_filters:
                    if segment.status not in status_filters:
                        show_row = False
                
                # Locked/unlocked filter
                if filters.get('locked_filter'):
                    locked_value = getattr(segment, 'locked', False)
                    if filters['locked_filter'] == 'locked' and not locked_value:
                        show_row = False
                    elif filters['locked_filter'] == 'unlocked' and locked_value:
                        show_row = False
                
                # Other properties
                if filters.get('has_comments'):
                    if not (segment.notes and segment.notes.strip()):
                        show_row = False
                
                if filters.get('has_proofreading'):
                    if not getattr(segment, 'proofreading_notes', None):
                        show_row = False
                
                if filters.get('repetitions_only'):
                    # TODO: Implement repetition detection
                    pass

                # Empty structural segments are always hidden
                if not segment.source.strip():
                    show_row = False

                self.table.setRowHidden(row, not show_row)

                if show_row:
                    visible_count += 1
        finally:
            self.table.setUpdatesEnabled(True)
        
        self.log(f"🔍 Advanced filters: showing {visible_count} of {len(self.current_project.segments)} segments")

    def apply_sort(self, sort_type: str = None):
        """Sort segments by various criteria (similar to memoQ)"""
        if not self.current_project or not hasattr(self, 'table') or self.table is None:
            return

        if not self.current_project.segments:
            return

        # Show progress dialog during sorting
        from PyQt6.QtWidgets import QProgressDialog
        from PyQt6.QtCore import Qt

        progress = QProgressDialog("Sorting segments, please wait...", None, 0, 0, self)
        progress.setWindowTitle(self.tr("Sorting"))
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)  # Show immediately
        progress.show()
        QApplication.processEvents()  # Force UI update

        try:
            # Store original document order if not already stored
            if not hasattr(self, '_original_segment_order'):
                self._original_segment_order = self.current_project.segments.copy()

            # Update current sort state
            self.current_sort = sort_type

            # If sort_type is None, restore document order
            if sort_type is None:
                # Restore document order by sorting by segment ID (original position)
                # This works even if the stored original order is wrong
                self.current_project.segments.sort(key=lambda seg: int(seg.id))

                # Update stored original order to this correct order
                self._original_segment_order = self.current_project.segments.copy()

                # Set pagination to "All" to show all segments
                if hasattr(self, 'page_size_combo') and self._widget_is_alive(self.page_size_combo):
                    self.page_size_combo.blockSignals(True)
                    self.page_size_combo.setCurrentText("All")
                    self.page_size_combo.blockSignals(False)
                    # Update the internal page size variable
                    if hasattr(self, 'grid_page_size'):
                        self.grid_page_size = 999999

                self.load_segments_to_grid()
                self.log("↩️ Restored document order (showing all segments)")
                return

            # Helper function to get text without tags for more accurate sorting
            def strip_tags(text: str) -> str:
                """Remove HTML/XML tags from text for sorting"""
                import re
                return re.sub(r'<[^>]+>', '', text).strip()

            # Calculate frequency maps if needed
            frequency_cache = {}
            if 'freq' in sort_type:
                from collections import Counter
                if 'source' in sort_type:
                    counter = Counter(strip_tags(seg.source).lower() for seg in self.current_project.segments)
                    frequency_cache = {strip_tags(seg.source).lower(): counter[strip_tags(seg.source).lower()]
                                     for seg in self.current_project.segments}
                else:  # target frequency
                    counter = Counter(strip_tags(seg.target).lower() for seg in self.current_project.segments if seg.target)
                    frequency_cache = {strip_tags(seg.target).lower(): counter[strip_tags(seg.target).lower()]
                                     for seg in self.current_project.segments if seg.target}

            # Sort based on selected criterion
            if sort_type == 'source_asc':
                self.current_project.segments.sort(key=lambda s: strip_tags(s.source).lower())
                sort_name = "Source A → Z"
            elif sort_type == 'source_desc':
                self.current_project.segments.sort(key=lambda s: strip_tags(s.source).lower(), reverse=True)
                sort_name = "Source Z → A"
            elif sort_type == 'target_asc':
                self.current_project.segments.sort(key=lambda s: strip_tags(s.target).lower() if s.target else "")
                sort_name = "Target A → Z"
            elif sort_type == 'target_desc':
                self.current_project.segments.sort(key=lambda s: strip_tags(s.target).lower() if s.target else "", reverse=True)
                sort_name = "Target Z → A"
            elif sort_type == 'source_length_asc':
                self.current_project.segments.sort(key=lambda s: len(strip_tags(s.source)))
                sort_name = "Source (shorter first)"
            elif sort_type == 'source_length_desc':
                self.current_project.segments.sort(key=lambda s: len(strip_tags(s.source)), reverse=True)
                sort_name = "Source (longer first)"
            elif sort_type == 'target_length_asc':
                self.current_project.segments.sort(key=lambda s: len(strip_tags(s.target)) if s.target else 0)
                sort_name = "Target (shorter first)"
            elif sort_type == 'target_length_desc':
                self.current_project.segments.sort(key=lambda s: len(strip_tags(s.target)) if s.target else 0, reverse=True)
                sort_name = "Target (longer first)"
            elif sort_type == 'match_asc':
                self.current_project.segments.sort(key=lambda s: getattr(s, 'match_percent', 0) or 0)
                sort_name = "Match Rate (lower first)"
            elif sort_type == 'match_desc':
                self.current_project.segments.sort(key=lambda s: getattr(s, 'match_percent', 0) or 0, reverse=True)
                sort_name = "Match Rate (higher first)"
            elif sort_type == 'source_freq_asc':
                self.current_project.segments.sort(key=lambda s: frequency_cache.get(strip_tags(s.source).lower(), 0))
                sort_name = "Source Frequency (lower first)"
            elif sort_type == 'source_freq_desc':
                self.current_project.segments.sort(key=lambda s: frequency_cache.get(strip_tags(s.source).lower(), 0), reverse=True)
                sort_name = "Source Frequency (higher first)"
            elif sort_type == 'target_freq_asc':
                self.current_project.segments.sort(key=lambda s: frequency_cache.get(strip_tags(s.target).lower(), 0) if s.target else 0)
                sort_name = "Target Frequency (lower first)"
            elif sort_type == 'target_freq_desc':
                self.current_project.segments.sort(key=lambda s: frequency_cache.get(strip_tags(s.target).lower(), 0) if s.target else 0, reverse=True)
                sort_name = "Target Frequency (higher first)"
            elif sort_type == 'modified_asc':
                self.current_project.segments.sort(key=lambda s: s.modified_at if s.modified_at else "")
                sort_name = "Last Changed (oldest first)"
            elif sort_type == 'modified_desc':
                self.current_project.segments.sort(key=lambda s: s.modified_at if s.modified_at else "", reverse=True)
                sort_name = "Last Changed (newest first)"
            elif sort_type == 'status':
                # Sort by status in a logical order: not_started, draft, confirmed
                status_order = {'not_started': 0, 'draft': 1, 'confirmed': 2, 'approved': 3}
                self.current_project.segments.sort(key=lambda s: status_order.get(s.status, 99))
                sort_name = "Row Status"
            else:
                self.log(f"⚠️ Unknown sort type: {sort_type}")
                return

            # Set pagination to "All" to show all sorted segments
            if hasattr(self, 'page_size_combo') and self._widget_is_alive(self.page_size_combo):
                self.page_size_combo.blockSignals(True)
                self.page_size_combo.setCurrentText("All")
                self.page_size_combo.blockSignals(False)
                # Update the internal page size variable
                if hasattr(self, 'grid_page_size'):
                    self.grid_page_size = 999999

            # Reload grid to reflect new order
            self.load_segments_to_grid()
            self.log(f"⇅ Sorted by: {sort_name} (showing all segments)")

        except Exception as e:
            self.log(f"❌ Error sorting segments: {e}")
            import traceback
            traceback.print_exc()
        finally:
            # Close progress dialog
            progress.close()

    def filter_on_selected_text(self):
        """Filter based on currently selected text in source or target column.
        
        Toggle behavior: If filters are already active, pressing Ctrl+Shift+F 
        will clear the filters. Otherwise, filter on selected text.
        """
        from PyQt6.QtWidgets import QApplication
        
        # Check if filters are currently active FIRST (for toggle behavior)
        source_filter_text = self._get_line_edit_text('source_filter').strip() if hasattr(self, 'source_filter') else ""
        target_filter_text = self._get_line_edit_text('target_filter').strip() if hasattr(self, 'target_filter') else ""
        filters_active = bool(source_filter_text or target_filter_text)
        
        # Toggle: if filters are active, clear them and return
        if filters_active:
            self.clear_filters()
            self.log(f"🔍 Filters cleared ({format_shortcut_for_display('Ctrl+Shift+F')} toggle)")
            return
        
        # No filters active - try to filter on selected text
        focused_widget = QApplication.focusWidget()
        
        selected_text = ""
        is_source = False
        is_target = False
        
        # Check if it's a text editor widget (source or target)
        if focused_widget and isinstance(focused_widget, (EditableGridTextEditor, ReadOnlyGridTextEditor)):
            cursor = focused_widget.textCursor()
            selected_text = cursor.selectedText().strip()
            
            # Determine if it's source or target by checking which column it's in
            if hasattr(self, 'table') and self.table:
                for row in range(self.table.rowCount()):
                    # Check source column (index 2)
                    source_widget = self.table.cellWidget(row, 2)
                    if source_widget == focused_widget:
                        is_source = True
                        break
                    # Check target column (index 3)
                    target_widget = self.table.cellWidget(row, 3)
                    if target_widget == focused_widget:
                        is_target = True
                        break
        
        # Strip invisible character markers so filtering matches clean segment text
        if selected_text and hasattr(self, 'reverse_invisible_replacements'):
            selected_text = self.reverse_invisible_replacements(selected_text)
            selected_text = selected_text.strip()

        if not selected_text:
            self.log("⚠️ No text selected. Select text in source or target column first.")
            return

        # Put selected text in appropriate filter box and apply filter
        if is_source and hasattr(self, 'source_filter') and self.source_filter:
            self.source_filter.setText(selected_text)
            self.apply_filters()
            self.log(f"🔍 Filtering source on: '{selected_text}'")
        elif is_target and hasattr(self, 'target_filter') and self.target_filter:
            self.target_filter.setText(selected_text)
            self.apply_filters()
            self.log(f"🔍 Filtering target on: '{selected_text}'")
