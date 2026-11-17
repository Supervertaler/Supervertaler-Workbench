"""
Translation Memory Manager for Supervertaler Qt
Provides comprehensive TM management features:
- Browse all TM entries
- Concordance search
- Import/Export TMX files
- Delete entries
- View statistics
"""

from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QTabWidget,
                              QTableWidget, QTableWidgetItem, QLineEdit, QPushButton,
                              QLabel, QMessageBox, QFileDialog, QHeaderView,
                              QGroupBox, QTextEdit, QComboBox, QSpinBox, QCheckBox,
                              QProgressBar, QProgressDialog, QWidget, QStyle,
                              QStyledItemDelegate)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QSize
from PyQt6.QtGui import QColor, QFont, QPalette
import sqlite3
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional, Callable


class TMCleanupThread(QThread):
    """Background worker for TM Maintenance cleanup operations.

    Opens its own SQLite connection inside ``run`` so it never touches the
    main-thread connection. WAL mode (set on the database at startup) lets
    this writer coexist with the main thread's readers without locking the
    UI.

    Modes:
      MODE_IDENTICAL_PAIRS   – DELETE rows where source_text == target_text.
      MODE_DUPLICATE_SOURCES – for each duplicated source_hash, keep the row
                               with the most recent modified_date and delete
                               the rest (ties broken by id, deterministic).
    """

    MODE_IDENTICAL_PAIRS = "identical_pairs"
    MODE_DUPLICATE_SOURCES = "duplicate_sources"

    progress = pyqtSignal(int, int, str)        # done, total, status text
    finished = pyqtSignal(bool, str, int, int)  # success, error_msg, deleted, considered

    def __init__(self, db_path: str, mode: str, filter_tm_ids=None):
        super().__init__()
        self.db_path = db_path
        self.mode = mode
        self.filter_tm_ids = list(filter_tm_ids) if filter_tm_ids else None

    def _filter_clause(self, joiner: str):
        """Return ('AND tm_id IN (?,?,?)', [ids…]) or ('', [])."""
        if not self.filter_tm_ids:
            return "", []
        placeholders = ",".join("?" * len(self.filter_tm_ids))
        return f"{joiner} tm_id IN ({placeholders})", list(self.filter_tm_ids)

    def run(self):
        try:
            conn = sqlite3.connect(self.db_path, timeout=60)
            conn.execute("PRAGMA journal_mode=WAL")
            cursor = conn.cursor()
            try:
                if self.mode == self.MODE_IDENTICAL_PAIRS:
                    deleted, considered = self._clean_identical_pairs(cursor)
                elif self.mode == self.MODE_DUPLICATE_SOURCES:
                    deleted, considered = self._clean_duplicate_sources(cursor)
                else:
                    raise ValueError(f"Unknown cleanup mode: {self.mode}")
                conn.commit()
                self.finished.emit(True, "", deleted, considered)
            finally:
                conn.close()
        except Exception as e:
            self.finished.emit(False, str(e), 0, 0)

    def _clean_identical_pairs(self, cursor):
        clause, params = self._filter_clause("AND")
        self.progress.emit(0, 0, "Counting identical source/target pairs…")
        cursor.execute(
            f"SELECT COUNT(*) FROM translation_units "
            f"WHERE source_text = target_text {clause}",
            params,
        )
        before = cursor.fetchone()[0]
        if before == 0:
            return 0, 0
        self.progress.emit(0, 0, f"Deleting {before:,} identical pairs…")
        cursor.execute(
            f"DELETE FROM translation_units "
            f"WHERE source_text = target_text {clause}",
            params,
        )
        return before, before

    def _clean_duplicate_sources(self, cursor):
        # Find all duplicated source hashes (one row per group).
        where_clause, where_params = self._filter_clause("WHERE")
        self.progress.emit(0, 0, "Finding duplicate sources…")
        cursor.execute(
            f"""
            SELECT source_hash
            FROM translation_units
            {where_clause}
            GROUP BY source_hash
            HAVING COUNT(*) > 1
            """,
            where_params,
        )
        duplicate_hashes = [r[0] for r in cursor.fetchall()]
        total_groups = len(duplicate_hashes)
        if total_groups == 0:
            return 0, 0

        and_clause, and_params = self._filter_clause("AND")
        total_deleted = 0
        report_every = max(1, total_groups // 200)  # ≈200 progress ticks max

        for idx, source_hash in enumerate(duplicate_hashes, start=1):
            params = [source_hash] + and_params
            cursor.execute(
                f"""
                SELECT id FROM translation_units
                WHERE source_hash = ? {and_clause}
                ORDER BY modified_date DESC, id DESC
                """,
                params,
            )
            ids = [row[0] for row in cursor.fetchall()]
            if len(ids) > 1:
                ids_to_delete = ids[1:]  # keep the newest
                placeholders = ",".join("?" * len(ids_to_delete))
                cursor.execute(
                    f"DELETE FROM translation_units WHERE id IN ({placeholders})",
                    ids_to_delete,
                )
                total_deleted += len(ids_to_delete)
            if idx % report_every == 0 or idx == total_groups:
                self.progress.emit(
                    idx, total_groups,
                    f"Processing duplicate sources… {idx:,} / {total_groups:,} "
                    f"(removed {total_deleted:,} so far)"
                )
        return total_deleted, total_groups


class HighlightDelegate(QStyledItemDelegate):
    """Custom delegate to render HTML with highlighted text in table cells"""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.search_term = ""
    
    def set_search_term(self, term: str):
        """Set the term to highlight"""
        self.search_term = term
    
    def paint(self, painter, option, index):
        """Paint the cell with HTML rendering for highlighting"""
        from PyQt6.QtGui import QTextDocument, QAbstractTextDocumentLayout
        from PyQt6.QtCore import QRectF
        
        # Get the text
        text = index.data(Qt.ItemDataRole.DisplayRole)
        if not text:
            super().paint(painter, option, index)
            return
        
        # Create HTML with highlighting
        if self.search_term:
            import re
            pattern = re.compile(re.escape(self.search_term), re.IGNORECASE)
            html_text = pattern.sub(
                lambda m: f"<span style='background-color: #FFD54F; font-weight: bold;'>{m.group()}</span>",
                text
            )
        else:
            html_text = text
        
        # Setup painter
        painter.save()
        
        # Draw selection background if selected
        if option.state & QStyle.StateFlag.State_Selected:
            painter.fillRect(option.rect, option.palette.highlight())
        
        # Create document for HTML rendering
        doc = QTextDocument()
        doc.setDefaultFont(option.font)
        doc.setHtml(html_text)
        doc.setTextWidth(option.rect.width() - 6)  # Some padding
        
        # Translate to cell position
        painter.translate(option.rect.left() + 3, option.rect.top() + 2)
        
        # Create clip rect
        clip = QRectF(0, 0, option.rect.width() - 6, option.rect.height() - 4)
        
        # Draw the document
        ctx = QAbstractTextDocumentLayout.PaintContext()
        if option.state & QStyle.StateFlag.State_Selected:
            ctx.palette.setColor(QPalette.ColorRole.Text, option.palette.highlightedText().color())
        doc.documentLayout().draw(painter, ctx)
        
        painter.restore()
    
    def sizeHint(self, option, index):
        """Return size hint based on content"""
        from PyQt6.QtGui import QTextDocument
        
        text = index.data(Qt.ItemDataRole.DisplayRole)
        if not text:
            return super().sizeHint(option, index)
        
        doc = QTextDocument()
        doc.setDefaultFont(option.font)
        doc.setHtml(text)
        doc.setTextWidth(option.rect.width() if option.rect.width() > 0 else 400)
        
        return QSize(int(doc.idealWidth()), max(int(doc.size().height()) + 8, 50))


class ConcordanceSearchDialog(QDialog):
    """
    Lightweight Concordance Search dialog for Ctrl+K.
    Focused on quick concordance search without other TM management features.
    Features two view modes: List view and Table view (memoQ-style side-by-side).
    """
    
    def __init__(self, parent, db_manager, log_callback: Optional[Callable] = None, initial_query: str = None):
        super().__init__(parent)
        self.db_manager = db_manager
        self.log = log_callback if log_callback else lambda x: None
        self.parent_app = parent
        self.current_results = []  # Store results for both views
        self.current_search_term = ""
        self._updating_heights = False  # Flag to prevent recursive updates
        self._initial_query = initial_query  # Store for after show
        
        # Get language names from parent app
        self.source_lang_name = getattr(parent, 'source_language', 'Source')
        self.target_lang_name = getattr(parent, 'target_language', 'Target')
        
        self.setWindowTitle("Concordance Search")
        self.setMinimumSize(800, 600)
        
        self.setup_ui()
    
    def exec(self):
        """Override exec to restore saved geometry or match parent window"""
        # Try to restore saved geometry from project
        geometry_restored = False
        if hasattr(self.parent_app, 'current_project') and self.parent_app.current_project:
            project = self.parent_app.current_project
            if hasattr(project, 'concordance_geometry') and project.concordance_geometry:
                geom = project.concordance_geometry
                self.setGeometry(geom['x'], geom['y'], geom['width'], geom['height'])
                geometry_restored = True
        
        # If no saved geometry, match parent window size and position
        if not geometry_restored and self.parent_app:
            parent_geom = self.parent_app.geometry()
            self.setGeometry(parent_geom)
        
        self.show()
        
        # Set initial query and search if provided
        if self._initial_query:
            self.search_input.setText(self._initial_query)
            self.do_search()
        
        return super().exec()
    
    def closeEvent(self, event):
        """Save window geometry to project when closing"""
        if hasattr(self.parent_app, 'current_project') and self.parent_app.current_project:
            geom = self.geometry()
            self.parent_app.current_project.concordance_geometry = {
                'x': geom.x(),
                'y': geom.y(),
                'width': geom.width(),
                'height': geom.height()
            }
        super().closeEvent(event)
    
    def setup_ui(self):
        """Setup the UI with the TM concordance view"""
        layout = QVBoxLayout()
        layout.setContentsMargins(15, 15, 15, 15)
        
        # Header
        header = QLabel("🔍 Concordance Search")
        header_font = QFont()
        header_font.setPointSize(14)
        header_font.setBold(True)
        header.setFont(header_font)
        layout.addWidget(header)
        
        # Description
        desc = QLabel("Search across translation memories for exact and partial matches")
        desc.setStyleSheet("color: #666; margin-bottom: 10px;")
        layout.addWidget(desc)
        
        # Search controls
        search_layout = QHBoxLayout()
        search_layout.addWidget(QLabel("Search:"))
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Enter text to search...")
        self.search_input.returnPressed.connect(self.do_search)
        self.search_input.setStyleSheet("padding: 8px; font-size: 13px;")
        search_layout.addWidget(self.search_input)
        
        search_btn = QPushButton("🔍 Search")
        search_btn.clicked.connect(self.do_search)
        search_btn.setStyleSheet("padding: 8px 16px;")
        search_layout.addWidget(search_btn)
        
        layout.addLayout(search_layout)
        
        # Tab widget for result views
        self.view_tabs = QTabWidget()

        # TM Concordance (exact/fuzzy text matching)
        self.tm_tab = QWidget()
        tm_layout = QVBoxLayout(self.tm_tab)
        tm_layout.setContentsMargins(0, 10, 0, 0)

        self.search_results = QTextEdit()
        self.search_results.setReadOnly(True)
        self.search_results.setFont(QFont("Segoe UI", 10))
        self.search_results.setStyleSheet("background-color: #fafafa; border: 1px solid #ddd; border-radius: 4px;")
        tm_layout.addWidget(self.search_results)

        # Tab title carries the result count (updated after search)
        self.view_tabs.addTab(self.tm_tab, "📋 TM Matches")
        
        layout.addWidget(self.view_tabs)
        
        # Status bar
        status_layout = QHBoxLayout()
        self.status_label = QLabel("Enter a search term and press Search or Enter")
        self.status_label.setStyleSheet("color: #666;")
        status_layout.addWidget(self.status_label)
        status_layout.addStretch()
        
        # Close button
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        close_btn.setStyleSheet("padding: 6px 20px;")
        status_layout.addWidget(close_btn)
        
        layout.addLayout(status_layout)
        
        self.setLayout(layout)
        
        # Focus on search input
        self.search_input.setFocus()

    def do_search(self):
        """Perform the TM concordance search"""
        search_text = self.search_input.text().strip()
        if not search_text:
            self.status_label.setText("⚠️ Please enter a search term")
            return

        self.status_label.setText("🔍 Searching...")
        self.search_results.clear()
        self.current_search_term = search_text

        tm_count = 0

        # Search TM (concordance)
        try:
            results = self.db_manager.concordance_search(search_text)
            self.current_results = results if results else []
            tm_count = len(self.current_results)
            
            if not results:
                self.search_results.setHtml(
                    f"<p style='color: #666; padding: 20px; text-align: center;'>"
                    f"No TM matches found for '<b>{search_text}</b>'</p>"
                )
            else:
                self.update_tm_view()
                
        except Exception as e:
            self.search_results.setHtml(f"<p style='color: red; padding: 20px;'>TM Search Error: {str(e)}</p>")
            self.log(f"TM Concordance search error: {e}")

        # Update tab title with count
        self.view_tabs.setTabText(0, f"📋 TM Matches ({tm_count})")

        # Update status
        if tm_count > 0:
            self.status_label.setText(f"✓ Found {tm_count} TM matches")
            self.log(f"Concordance: Found {tm_count} TM matches for '{search_text}'")
        else:
            self.status_label.setText("No matches found")
    
    def update_tm_view(self):
        """Update the TM concordance view with current results"""
        if not self.current_results:
            return
        
        search_text = self.current_search_term
        results = self.current_results
        
        # Format results with highlighting
        html = f"<h3 style='color: #333; margin-bottom: 15px;'>Found {len(results)} TM matches for '<span style='color: #2196F3;'>{search_text}</span>'</h3>"
        
        for idx, match in enumerate(results, 1):
            source = match.get('source_text', '')
            target = match.get('target_text', '')
            tm_id = match.get('tm_id', 'Unknown')
            usage_count = match.get('usage_count', 0)
            modified_date = match.get('modified_date', 'Unknown')
            
            # Highlight search term in source and target
            highlighted_source = self._highlight_term(source, search_text)
            highlighted_target = self._highlight_term(target, search_text)
            
            # Alternating background colors for better visibility
            bg_color = '#f5f5f5' if idx % 2 == 0 else '#ffffff'
            
            html += f"""
            <div style='background-color: {bg_color}; padding: 10px 8px; margin: 0;'>
                <div style='color: #555; font-size: 11px; margin-bottom: 6px;'>
                    #{idx} - TM: <b>{tm_id}</b> - Used: {usage_count} times - Modified: {modified_date}
                </div>
                <div style='margin-bottom: 4px;'>
                    <b style='color: #1976D2;'>{self.source_lang_name}:</b> {highlighted_source}
                </div>
                <div>
                    <b style='color: #388E3C;'>{self.target_lang_name}:</b> {highlighted_target}
                </div>
            </div>
            <hr style='border: none; border-top: 2px solid #666; margin: 0;'>
            """
        
        self.search_results.setHtml(html)
    
    def _highlight_term(self, text: str, search_term: str) -> str:
        """Highlight search term in text with yellow/orange background"""
        if not text or not search_term:
            return text or ""
        
        import re
        # Case-insensitive highlighting
        pattern = re.compile(re.escape(search_term), re.IGNORECASE)
        return pattern.sub(
            lambda m: f"<span style='background-color: #FFD54F; padding: 1px 3px; border-radius: 2px; font-weight: bold;'>{m.group()}</span>",
            text
        )


class TMManagerDialog(QDialog):
    """Translation Memory Manager dialog"""
    
    def __init__(self, parent, db_manager, log_callback: Optional[Callable] = None, tm_ids: list = None,
                 initial_concordance_query: str = None):
        super().__init__(parent)
        self.db_manager = db_manager
        self.log = log_callback if log_callback else lambda x: None
        self.parent_app = parent
        self.filter_tm_ids = tm_ids  # Optional filter: only show entries from these TM IDs
        self._initial_concordance_query = initial_concordance_query

        self.setWindowTitle("Translation Memory Manager")
        self.resize(1000, 700)

        self.setup_ui()
        self.load_initial_data()

        # If a concordance query was provided, switch to Concordance tab and run it
        if self._initial_concordance_query:
            self.tabs.setCurrentIndex(1)  # Concordance tab
            self.search_input.setText(self._initial_concordance_query)
            self.do_concordance_search()
    
    def setup_ui(self):
        """Setup the UI with tabs"""
        layout = QVBoxLayout()
        
        # Header
        header = QLabel("📚 Translation Memory Manager")
        header_font = QFont()
        header_font.setPointSize(14)
        header_font.setBold(True)
        header.setFont(header_font)
        layout.addWidget(header)
        
        # Tab widget
        self.tabs = QTabWidget()
        
        # Create tabs
        self.browser_tab = self.create_browser_tab()
        self.search_tab = self.create_search_tab()
        self.import_export_tab = self.create_import_export_tab()
        self.stats_tab = self.create_stats_tab()
        
        self.tabs.addTab(self.browser_tab, "📋 Browse")
        self.tabs.addTab(self.search_tab, "🔍 Concordance")
        self.tabs.addTab(self.import_export_tab, "📥 Import/Export")
        self.tabs.addTab(self.stats_tab, "📊 Statistics")
        
        # Add maintenance tab for cleaning
        self.maintenance_tab = self.create_maintenance_tab()
        self.tabs.addTab(self.maintenance_tab, "🧹 Maintenance")
        
        layout.addWidget(self.tabs)
        
        # Close button
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        layout.addWidget(close_btn)
        
        self.setLayout(layout)
    
    def create_browser_tab(self):
        """Create TM browser tab"""
        widget = QGroupBox()
        layout = QVBoxLayout()
        
        # Filter controls
        filter_layout = QHBoxLayout()
        filter_layout.addWidget(QLabel("Filter:"))
        self.browser_filter = QLineEdit()
        self.browser_filter.setPlaceholderText("Type to filter entries...")
        self.browser_filter.textChanged.connect(self.filter_browser_entries)
        filter_layout.addWidget(self.browser_filter)
        
        self.browser_limit = QSpinBox()
        self.browser_limit.setRange(100, 10000)
        self.browser_limit.setValue(500)
        self.browser_limit.setSingleStep(100)
        self.browser_limit.setPrefix("Show: ")
        self.browser_limit.setSuffix(" entries")
        filter_layout.addWidget(self.browser_limit)
        
        refresh_btn = QPushButton("🔄 Refresh")
        refresh_btn.clicked.connect(self.refresh_browser)
        filter_layout.addWidget(refresh_btn)
        
        layout.addLayout(filter_layout)
        
        # Table
        self.browser_table = QTableWidget()
        self.browser_table.setColumnCount(6)
        self.browser_table.setHorizontalHeaderLabels([
            "ID", "Source", "Target", "TM", "Usage", "Modified"
        ])
        self.browser_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.browser_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.browser_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.browser_table.setAlternatingRowColors(True)
        # Enable sorting by clicking column headers
        self.browser_table.setSortingEnabled(True)
        layout.addWidget(self.browser_table)
        
        # Action buttons
        btn_layout = QHBoxLayout()
        delete_btn = QPushButton("🗑️ Delete Selected")
        delete_btn.clicked.connect(self.delete_selected_entry)
        btn_layout.addWidget(delete_btn)
        btn_layout.addStretch()
        
        self.browser_status = QLabel("Ready")
        btn_layout.addWidget(self.browser_status)
        
        layout.addLayout(btn_layout)
        
        widget.setLayout(layout)
        return widget
    
    def create_search_tab(self):
        """Create concordance search tab"""
        widget = QGroupBox()
        layout = QVBoxLayout()
        
        # Search controls
        search_layout = QHBoxLayout()
        search_layout.addWidget(QLabel("Search:"))
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Enter text to search in source and target...")
        self.search_input.returnPressed.connect(self.do_concordance_search)
        search_layout.addWidget(self.search_input)
        
        search_btn = QPushButton("🔍 Search")
        search_btn.clicked.connect(self.do_concordance_search)
        search_layout.addWidget(search_btn)
        
        layout.addLayout(search_layout)
        
        # Results display
        self.search_results = QTextEdit()
        self.search_results.setReadOnly(True)
        self.search_results.setFont(QFont("Segoe UI", 10))
        layout.addWidget(self.search_results)
        
        # Status
        self.search_status = QLabel("Enter a search term and press Search")
        layout.addWidget(self.search_status)
        
        widget.setLayout(layout)
        return widget
    
    def create_import_export_tab(self):
        """Create import/export tab"""
        widget = QGroupBox()
        layout = QVBoxLayout()
        
        # Import section
        import_group = QGroupBox("Import TMX")
        import_layout = QVBoxLayout()
        
        import_info = QLabel(
            "Import translation units from a TMX file into your database.\n"
            "All entries will be added to a new TM or merged with an existing one."
        )
        import_info.setWordWrap(True)
        import_layout.addWidget(import_info)
        
        import_controls = QHBoxLayout()
        import_btn = QPushButton("📂 Select and Import TMX...")
        import_btn.clicked.connect(self.import_tmx)
        import_controls.addWidget(import_btn)
        import_layout.addLayout(import_controls)
        
        import_group.setLayout(import_layout)
        layout.addWidget(import_group)
        
        # Export section
        export_group = QGroupBox("Export TMX")
        export_layout = QVBoxLayout()
        
        export_info = QLabel(
            "Export your translation memory to a standard TMX file.\n"
            "The TMX file can be used in other CAT tools or shared with colleagues."
        )
        export_info.setWordWrap(True)
        export_layout.addWidget(export_info)
        
        export_controls = QHBoxLayout()
        export_controls.addWidget(QLabel("TM to export:"))
        self.export_tm_selector = QComboBox()
        self.export_tm_selector.addItem("All TMs", "all")
        self.export_tm_selector.addItem("Project TM only", "project")
        export_controls.addWidget(self.export_tm_selector)
        
        export_btn = QPushButton("💾 Export to TMX...")
        export_btn.clicked.connect(self.export_tmx)
        export_controls.addWidget(export_btn)
        export_layout.addLayout(export_controls)
        
        self.export_status = QLabel("")
        export_layout.addWidget(self.export_status)
        
        export_group.setLayout(export_layout)
        layout.addWidget(export_group)
        
        layout.addStretch()
        
        widget.setLayout(layout)
        return widget
    
    def create_stats_tab(self):
        """Create statistics tab"""
        widget = QGroupBox()
        layout = QVBoxLayout()
        
        self.stats_display = QTextEdit()
        self.stats_display.setReadOnly(True)
        self.stats_display.setFont(QFont("Courier New", 10))
        layout.addWidget(self.stats_display)
        
        refresh_btn = QPushButton("🔄 Refresh Statistics")
        refresh_btn.clicked.connect(self.refresh_stats)
        layout.addWidget(refresh_btn)
        
        widget.setLayout(layout)
        return widget
    
    def load_initial_data(self):
        """Load initial data for all tabs"""
        self.refresh_browser()
        self.refresh_stats()
    
    def refresh_browser(self):
        """Refresh the TM browser table"""
        try:
            limit = self.browser_limit.value()
            filter_text = self.browser_filter.text().strip()
            
            # Build TM filter clause
            tm_filter = ""
            params = []
            if self.filter_tm_ids:
                placeholders = ','.join('?' * len(self.filter_tm_ids))
                tm_filter = f" WHERE tm_id IN ({placeholders})"
                params = self.filter_tm_ids[:]
            
            # Get entries from database
            if filter_text:
                entries = self.db_manager.concordance_search(filter_text)
                # Apply TM filter to concordance results
                if self.filter_tm_ids:
                    entries = [e for e in entries if e.get('tm_id') in self.filter_tm_ids]
            else:
                # Get recent entries
                query = f"SELECT * FROM translation_units{tm_filter} ORDER BY modified_date DESC LIMIT {limit}"
                self.db_manager.cursor.execute(query, params)
                entries = [dict(row) for row in self.db_manager.cursor.fetchall()]
            
            # Populate table
            self.browser_table.setRowCount(len(entries))
            for row, entry in enumerate(entries):
                self.browser_table.setItem(row, 0, QTableWidgetItem(str(entry['id'])))
                self.browser_table.setItem(row, 1, QTableWidgetItem(entry['source_text'][:100]))
                self.browser_table.setItem(row, 2, QTableWidgetItem(entry['target_text'][:100]))
                self.browser_table.setItem(row, 3, QTableWidgetItem(entry['tm_id']))
                self.browser_table.setItem(row, 4, QTableWidgetItem(str(entry.get('usage_count', 0))))
                self.browser_table.setItem(row, 5, QTableWidgetItem(entry.get('modified_date', '')[:16]))
            
            self.browser_status.setText(f"Showing {len(entries)} entries")
            self.log(f"TM Browser: Loaded {len(entries)} entries")
            
        except Exception as e:
            self.browser_status.setText(f"Error: {str(e)}")
            self.log(f"Error refreshing TM browser: {e}")
    
    def filter_browser_entries(self):
        """Filter browser entries as user types"""
        # Auto-refresh on filter change (with debouncing in real implementation)
        pass
    
    def delete_selected_entry(self):
        """Delete the selected TM entry"""
        selected_rows = self.browser_table.selectedItems()
        if not selected_rows:
            QMessageBox.warning(self, "No Selection", "Please select an entry to delete")
            return
        
        row = self.browser_table.currentRow()
        entry_id = int(self.browser_table.item(row, 0).text())
        source = self.browser_table.item(row, 1).text()
        target = self.browser_table.item(row, 2).text()
        
        # Confirm deletion
        reply = QMessageBox.question(
            self, "Confirm Deletion",
            f"Delete this TM entry?\n\nSource: {source}\nTarget: {target}\n\nThis cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        
        if reply == QMessageBox.StandardButton.Yes:
            try:
                self.db_manager.cursor.execute("DELETE FROM translation_units WHERE id = ?", (entry_id,))
                self.db_manager.connection.commit()
                self.log(f"Deleted TM entry {entry_id}")
                self.refresh_browser()
                QMessageBox.information(self, "Success", "Entry deleted successfully")
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed to delete entry: {str(e)}")
    
    def do_concordance_search(self):
        """Perform concordance search"""
        query = self.search_input.text().strip()
        if not query:
            self.search_status.setText("Please enter a search term")
            return
        
        try:
            results = self.db_manager.concordance_search(query)
            
            # Display results
            self.search_results.clear()
            html = f"<h3>Found {len(results)} matches for '{query}'</h3>"
            
            for idx, match in enumerate(results, 1):
                source_highlighted = match['source_text'].replace(
                    query, f"<span style='background-color: yellow;'>{query}</span>"
                )
                target_highlighted = match['target_text'].replace(
                    query, f"<span style='background-color: yellow;'>{query}</span>"
                )
                
                html += f"""
                <div style='border: 1px solid #ccc; padding: 10px; margin: 10px 0;'>
                    <p><strong>#{idx}</strong> - TM: {match['tm_id']} - Used: {match.get('usage_count', 0)} times</p>
                    <p><strong>Source:</strong> {source_highlighted}</p>
                    <p><strong>Target:</strong> {target_highlighted}</p>
                    <p style='color: #888; font-size: 9pt;'>Modified: {match.get('modified_date', 'N/A')}</p>
                </div>
                """
            
            self.search_results.setHtml(html)
            self.search_status.setText(f"Found {len(results)} matches")
            self.log(f"Concordance search: {len(results)} matches for '{query}'")
            
        except Exception as e:
            self.search_status.setText(f"Error: {str(e)}")
            self.log(f"Error in concordance search: {e}")
    
    def import_tmx(self):
        """Import a TMX file, through the same import as the TMs tab.

        This tab had an import of its own that wrote to the database from a
        background thread through the main thread's SQLite connection. SQLite
        refused every write, the errors only reached the log, and it still
        reported "Successfully imported N entries". The TMs tab's import asks
        whether to make a new TM or add to one, matches the TMX languages and
        streams large files.
        """
        app = self.parent_app
        if not (hasattr(app, '_import_tmx_as_tm') and getattr(app, 'tm_metadata_mgr', None)):
            QMessageBox.warning(self, "Import TMX",
                                "Import TMX files from the TMs tab (Import TMX…).")
            return

        def _refresh():
            self.refresh_browser()
            self.refresh_stats()

        app._import_tmx_as_tm(app.tm_metadata_mgr, None, _refresh)

    def export_tmx(self):
        """Export TM to TMX file"""
        file_path, _ = QFileDialog.getSaveFileName(
            self, "Save TMX File", "",
            "TMX Files (*.tmx);;All Files (*.*)"
        )
        
        if not file_path:
            return
        
        try:
            tm_filter = self.export_tm_selector.currentData()
            
            # Get entries to export
            if tm_filter == "all":
                self.db_manager.cursor.execute("SELECT * FROM translation_units")
            else:
                self.db_manager.cursor.execute("SELECT * FROM translation_units WHERE tm_id = ?", (tm_filter,))
            
            entries = [dict(row) for row in self.db_manager.cursor.fetchall()]
            
            if not entries:
                QMessageBox.warning(self, "No Entries", "No translation units to export")
                return
            
            # Create TMX
            tmx = ET.Element('tmx')
            tmx.set('version', '1.4')
            
            header = ET.SubElement(tmx, 'header')
            header.set('creationdate', datetime.now().strftime('%Y%m%dT%H%M%SZ'))
            header.set('srclang', 'en')
            header.set('adminlang', 'en')
            header.set('segtype', 'sentence')
            header.set('creationtool', 'Supervertaler')
            header.set('creationtoolversion', '4.0')
            header.set('datatype', 'plaintext')
            
            body = ET.SubElement(tmx, 'body')
            
            for entry in entries:
                tu = ET.SubElement(body, 'tu')
                
                # Source
                tuv_src = ET.SubElement(tu, 'tuv')
                tuv_src.set('xml:lang', entry.get('source_lang', 'en'))
                seg_src = ET.SubElement(tuv_src, 'seg')
                seg_src.text = entry['source_text']
                
                # Target
                tuv_tgt = ET.SubElement(tu, 'tuv')
                tuv_tgt.set('xml:lang', entry.get('target_lang', 'de'))
                seg_tgt = ET.SubElement(tuv_tgt, 'seg')
                seg_tgt.text = entry['target_text']
            
            # Write to file
            tree = ET.ElementTree(tmx)
            ET.indent(tree, space="  ")
            tree.write(file_path, encoding='utf-8', xml_declaration=True)
            
            self.export_status.setText(f"Exported {len(entries)} entries to {Path(file_path).name}")
            QMessageBox.information(self, "Export Complete", 
                                   f"Successfully exported {len(entries)} translation units")
            self.log(f"Exported {len(entries)} entries to {file_path}")
            
        except Exception as e:
            self.export_status.setText(f"Error: {str(e)}")
            QMessageBox.critical(self, "Export Failed", f"Failed to export TMX:\n{str(e)}")
            self.log(f"Error exporting TMX: {e}")
    
    def refresh_stats(self):
        """Refresh TM statistics"""
        try:
            # Build TM filter clause
            tm_filter = ""
            params = []
            if self.filter_tm_ids:
                placeholders = ','.join('?' * len(self.filter_tm_ids))
                tm_filter = f" WHERE tm_id IN ({placeholders})"
                params = self.filter_tm_ids[:]
            
            # Get various statistics
            self.db_manager.cursor.execute(f"SELECT COUNT(*) FROM translation_units{tm_filter}", params)
            total_entries = self.db_manager.cursor.fetchone()[0]
            
            self.db_manager.cursor.execute(f"SELECT COUNT(DISTINCT tm_id) FROM translation_units{tm_filter}", params)
            tm_count = self.db_manager.cursor.fetchone()[0]
            
            query = f"""
                SELECT tm_id, COUNT(*) as count 
                FROM translation_units{tm_filter}
                GROUP BY tm_id 
                ORDER BY count DESC
            """
            self.db_manager.cursor.execute(query, params)
            tm_breakdown = self.db_manager.cursor.fetchall()
            
            query = f"""
                SELECT AVG(LENGTH(source_text)), AVG(LENGTH(target_text))
                FROM translation_units{tm_filter}
            """
            self.db_manager.cursor.execute(query, params)
            avg_lengths = self.db_manager.cursor.fetchone()
            
            # Handle empty TM (AVG returns None)
            avg_source = avg_lengths[0] if avg_lengths[0] is not None else 0
            avg_target = avg_lengths[1] if avg_lengths[1] is not None else 0
            
            # Format statistics
            stats_text = f"""
═══════════════════════════════════════════════
  TRANSLATION MEMORY STATISTICS
═══════════════════════════════════════════════

Total Translation Units: {total_entries:,}
Number of TMs: {tm_count}

Average Source Length: {avg_source:.1f} characters
Average Target Length: {avg_target:.1f} characters

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  BREAKDOWN BY TM
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

"""
            for tm_id, count in tm_breakdown:
                pct = (count / total_entries * 100) if total_entries > 0 else 0
                stats_text += f"{tm_id:20s} {count:8,} entries ({pct:5.1f}%)\n"
            
            self.stats_display.setPlainText(stats_text)
            self.log("TM statistics refreshed")
            
        except Exception as e:
            self.stats_display.setPlainText(f"Error loading statistics:\n{str(e)}")
            self.log(f"Error refreshing stats: {e}")
    
    def create_maintenance_tab(self):
        """Create maintenance/cleaning tab"""
        widget = QGroupBox()
        layout = QVBoxLayout()
        
        # Header
        header_label = QLabel("<h3>🧹 TM Maintenance & Cleaning</h3>")
        layout.addWidget(header_label)
        
        info_label = QLabel(
            "Clean up your translation memory by removing duplicates and redundant entries.\n"
            "This helps keep your TM efficient and reduces clutter."
        )
        info_label.setWordWrap(True)
        info_label.setStyleSheet("color: #666; margin-bottom: 15px;")
        layout.addWidget(info_label)
        
        # Duplicate cleaning section
        dup_group = QGroupBox("Duplicate Entry Cleaning")
        dup_layout = QVBoxLayout()
        
        # Option 1: Identical source + target
        dup1_layout = QHBoxLayout()
        dup1_desc = QLabel(
            "<b>Remove identical source + target pairs:</b><br>"
            "Deletes entries where both source and target text are exactly the same.<br>"
            "<i>Example: 'Hello' → 'Hello' (untranslated entries)</i>"
        )
        dup1_desc.setWordWrap(True)
        dup1_layout.addWidget(dup1_desc, 1)
        
        clean_identical_btn = QPushButton("🗑️ Clean")
        clean_identical_btn.setFixedWidth(100)
        clean_identical_btn.clicked.connect(self.clean_identical_source_target)
        dup1_layout.addWidget(clean_identical_btn)
        dup_layout.addLayout(dup1_layout)
        
        dup_layout.addWidget(QLabel(""))  # Spacer
        
        # Option 2: Identical source (keep newest)
        dup2_layout = QHBoxLayout()
        dup2_desc = QLabel(
            "<b>Remove duplicate sources (keep newest only):</b><br>"
            "For entries with identical source text, keeps only the most recent translation.<br>"
            "<i>Useful for removing outdated translations of the same source.</i>"
        )
        dup2_desc.setWordWrap(True)
        dup2_layout.addWidget(dup2_desc, 1)
        
        clean_duplicates_btn = QPushButton("🗑️ Clean")
        clean_duplicates_btn.setFixedWidth(100)
        clean_duplicates_btn.clicked.connect(self.clean_duplicate_sources)
        dup2_layout.addWidget(clean_duplicates_btn)
        dup_layout.addLayout(dup2_layout)
        
        dup_group.setLayout(dup_layout)
        layout.addWidget(dup_group)
        
        # Results display
        self.maintenance_results = QTextEdit()
        self.maintenance_results.setReadOnly(True)
        self.maintenance_results.setMaximumHeight(200)
        self.maintenance_results.setPlaceholderText("Cleaning results will appear here...")
        layout.addWidget(self.maintenance_results)
        
        layout.addStretch()
        
        widget.setLayout(layout)
        return widget
    
    def clean_identical_source_target(self):
        """Remove entries where source and target are identical (background)."""
        reply = QMessageBox.question(
            self, "Confirm Cleaning",
            "This will delete all TM entries where the source and target text are identical.\n\n"
            "This action cannot be undone. Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self._run_cleanup(
            mode=TMCleanupThread.MODE_IDENTICAL_PAIRS,
            dialog_title="Cleaning identical pairs",
            initial_label="Counting identical source/target pairs…",
            on_zero_text="✅ No identical source/target entries found. TM is clean!",
            success_template=(
                "✅ Cleaning Complete!\n\n"
                "Removed {deleted:,} entries where source = target\n\n"
                "These were likely untranslated entries or placeholders.\n"
                "Your TM is now cleaner and more efficient."
            ),
            log_template="Cleaned {deleted} identical source/target entries from TM",
        )

    def clean_duplicate_sources(self):
        """Remove duplicate sources, keep newest translation (background)."""
        reply = QMessageBox.question(
            self, "Confirm Cleaning",
            "This will find entries with identical source text and keep only the most recent translation.\n\n"
            "Older translations of the same source will be deleted.\n"
            "This action cannot be undone. Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self._run_cleanup(
            mode=TMCleanupThread.MODE_DUPLICATE_SOURCES,
            dialog_title="Cleaning duplicate sources",
            initial_label="Finding duplicate sources…",
            on_zero_text="✅ No duplicate sources found. TM is clean!",
            success_template=(
                "✅ Cleaning Complete!\n\n"
                "Found {considered:,} sources with multiple translations\n"
                "Removed {deleted:,} older translations\n"
                "Kept the most recent translation for each source\n\n"
                "Your TM now has only the latest translations."
            ),
            log_template="Cleaned {deleted} duplicate source entries from TM (kept newest)",
        )

    def _run_cleanup(self, *, mode, dialog_title, initial_label, on_zero_text,
                     success_template, log_template):
        """Spin up a TMCleanupThread + QProgressDialog and wire the signals.

        Keeps the cleanup off the main thread so the UI stays responsive on
        large TMs. Cancellation is intentionally not offered: a partial
        cleanup mid-DELETE could leave the user wondering which rows
        survived. The progress dialog is informational only.
        """
        progress = QProgressDialog(initial_label, "", 0, 0, self)
        progress.setWindowTitle(dialog_title)
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setCancelButton(None)  # no cancel – see docstring
        progress.setMinimumDuration(0)
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        progress.setValue(0)

        thread = TMCleanupThread(
            db_path=self.db_manager.db_path,
            mode=mode,
            filter_tm_ids=self.filter_tm_ids,
        )

        def on_progress(done, total, msg):
            progress.setLabelText(msg)
            if total > 0:
                if progress.maximum() != total:
                    progress.setRange(0, total)
                progress.setValue(done)
            else:
                # Indeterminate – keep range (0,0)
                if progress.maximum() != 0 or progress.minimum() != 0:
                    progress.setRange(0, 0)

        def on_finished(success, error_msg, deleted, considered):
            progress.close()
            if not success:
                text = f"❌ Error during cleaning:\n{error_msg}"
                self.maintenance_results.setPlainText(text)
                QMessageBox.critical(self, "Cleaning Error", error_msg)
                return
            if deleted == 0 and considered == 0:
                self.maintenance_results.setPlainText(on_zero_text)
                return
            self.maintenance_results.setPlainText(
                success_template.format(deleted=deleted, considered=considered)
            )
            self.log(log_template.format(deleted=deleted))
            try:
                self.refresh_stats()
            except Exception:
                pass

        thread.progress.connect(on_progress)
        thread.finished.connect(on_finished)
        # Keep a reference so the thread isn't garbage-collected mid-run.
        self._cleanup_thread = thread
        thread.start()
