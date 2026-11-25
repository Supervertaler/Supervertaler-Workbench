"""Building and drawing the translation grid: the grid widget, pages,
filling the rows, the status cells, row heights, colours and fonts,
non-translatable highlighting, invisible characters and the tag view.

Part of the main window (SupervertalerQt), moved here unchanged. The
methods run as the main window's own: `self` is the SupervertalerQt.
"""

import json
import re
import time
from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtGui import (
    QAction,
    QColor,
    QFont,
    QFontMetrics,
    QTextCharFormat,
)
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QButtonGroup,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTableWidgetSelectionRange,
    QVBoxLayout,
    QWidget,
)
from modules.grid_editors import (
    EditableGridTextEditor,
    ReadOnlyGridTextEditor,
    TagHighlighter,
    WordWrapDelegate,
    _GridStyleBatch,
)
from modules.help_system import Topics as HelpTopics, set_topic as set_help_topic
from modules.main_dialogs import _ImportProgressDialog
from modules.segment_model import Segment
from modules.shortcut_display import format_shortcut_for_display
from modules.statuses import STATUSES, get_status
from modules.tag_text import (
    WORD_JOINER,
    compact_tags,
    expand_compact_tags,
    protect_tags_from_linebreak,
    strip_outer_wrapping_tags,
    wysiwyg_runs_to_phrase_text,
)
from modules.termlens_widget import TermLensWidget


class GridViewMixin:
    def create_grid_view_widget(self):
        """Create the Grid View widget (existing grid functionality)"""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        
        # Splitter for grid and assistance panel
        self.editor_splitter = QSplitter(Qt.Orientation.Horizontal)
        
        # Left side: Grid container with filter boxes
        grid_container = QWidget()
        grid_layout = QVBoxLayout(grid_container)
        grid_layout.setContentsMargins(0, 0, 0, 0)
        grid_layout.setSpacing(5)
        
        # Warning banner for replace in source (hidden by default)
        editor_warning = self._get_warning_banner('editor_grid')
        grid_layout.addWidget(editor_warning)
        editor_warning.hide()  # Hidden by default
        
        # Filter panel (like memoQ)
        filter_panel = QWidget()
        filter_layout = QHBoxLayout(filter_panel)
        filter_layout.setContentsMargins(0, 0, 0, 0)
        filter_layout.setSpacing(10)
        
        # Source filter
        source_filter_label = QLabel(self.tr("Source:"))
        self.source_filter = self._ensure_shared_filter(
            'source_filter',
            "Type to filter source segments...",
            on_change=self.apply_filters,
            on_return=self.apply_filters,
        )
        
        # Target filter
        target_filter_label = QLabel(self.tr("Target:"))
        self.target_filter = self._ensure_shared_filter(
            'target_filter',
            "Type to filter target segments...",
            on_change=self.apply_filters,
            on_return=self.apply_filters,
        )
        
        # Clear filters button
        clear_filters_btn = QPushButton(self.tr("Clear Filters"))
        clear_filters_btn.clicked.connect(self.clear_filters)
        clear_filters_btn.setMaximumWidth(100)
        clear_filters_btn.setStyleSheet("padding: 3px 5px;")

        # Show Invisibles button with dropdown menu
        show_invisibles_btn = QPushButton(self.tr("¶ Show Invisibles"))
        show_invisibles_btn.setMaximumWidth(140)
        show_invisibles_btn.setStyleSheet("background-color: #607D8B; color: white; font-weight: bold; padding: 3px 5px;")
        show_invisibles_menu = QMenu(show_invisibles_btn)

        # Create checkable actions for each invisible character type
        self.show_spaces_action = show_invisibles_menu.addAction("Spaces (·)")
        self.show_spaces_action.setCheckable(True)
        self.show_spaces_action.setChecked(False)
        self.show_spaces_action.triggered.connect(lambda: self.toggle_invisible_display('spaces'))

        self.show_tabs_action = show_invisibles_menu.addAction("Tabs (→)")
        self.show_tabs_action.setCheckable(True)
        self.show_tabs_action.setChecked(True)
        self.show_tabs_action.triggered.connect(lambda: self.toggle_invisible_display('tabs'))

        self.show_nbsp_action = show_invisibles_menu.addAction("Non-breaking Spaces")
        self.show_nbsp_action.setCheckable(True)
        self.show_nbsp_action.setChecked(True)
        self.show_nbsp_action.triggered.connect(lambda: self.toggle_invisible_display('nbsp'))

        self.show_linebreaks_action = show_invisibles_menu.addAction("Line Breaks (↵)")
        self.show_linebreaks_action.setCheckable(True)
        self.show_linebreaks_action.setChecked(True)
        self.show_linebreaks_action.triggered.connect(lambda: self.toggle_invisible_display('linebreaks'))

        show_invisibles_menu.addSeparator()

        # Toggle All option
        toggle_all_action = show_invisibles_menu.addAction("Toggle All")
        toggle_all_action.triggered.connect(self.toggle_all_invisibles)

        show_invisibles_btn.setMenu(show_invisibles_menu)

        # Spellcheck toggle button with dropdown menu
        # Load saved spellcheck state from preferences
        saved_enabled = self._load_spellcheck_settings()
        self.spellcheck_enabled = saved_enabled
        TagHighlighter.set_spellcheck_enabled(self.spellcheck_enabled)
        
        self.spellcheck_btn = QPushButton(self.tr("📝 Spellcheck"))
        self.spellcheck_btn.setMaximumWidth(120)
        self.spellcheck_btn.setCheckable(True)
        self.spellcheck_btn.setChecked(self.spellcheck_enabled)
        self._update_spellcheck_button_style()
        
        # Create dropdown menu for spellcheck options
        spellcheck_menu = QMenu(self.spellcheck_btn)
        
        # Toggle spellcheck action
        self.spellcheck_toggle_action = spellcheck_menu.addAction("Enable Spellcheck")
        self.spellcheck_toggle_action.setCheckable(True)
        self.spellcheck_toggle_action.setChecked(self.spellcheck_enabled)
        self.spellcheck_toggle_action.triggered.connect(self._toggle_spellcheck)
        
        spellcheck_menu.addSeparator()
        
        # Open custom dictionary action
        open_dict_action = spellcheck_menu.addAction("📖 Manage Custom Dictionary...")
        open_dict_action.triggered.connect(self._open_custom_dictionary_dialog)
        
        # Show spellcheck info
        info_action = spellcheck_menu.addAction("ℹ️ Spellcheck Info")
        info_action.triggered.connect(self._show_spellcheck_info)
        
        self.spellcheck_btn.setMenu(spellcheck_menu)
        # Also allow clicking the button itself to toggle
        self.spellcheck_btn.clicked.connect(self._toggle_spellcheck_from_button)

        filter_layout.addWidget(source_filter_label)
        filter_layout.addWidget(self.source_filter, stretch=1)
        filter_layout.addWidget(target_filter_label)
        filter_layout.addWidget(self.target_filter, stretch=1)
        filter_layout.addWidget(clear_filters_btn)
        filter_layout.addWidget(show_invisibles_btn)
        filter_layout.addWidget(self.spellcheck_btn)
        
        grid_layout.addWidget(filter_panel)
        
        # Translation Grid (create it if it doesn't exist or has been deleted)
        if not hasattr(self, 'table') or not self._widget_is_alive(self.table):
            self.create_translation_grid()
        grid_layout.addWidget(self.table)
        
        # Add grid container to splitter
        self.editor_splitter.addWidget(grid_container)
        
        # Set splitter proportions - Qt displays RIGHT to LEFT
        # Position 0 (grid/RIGHT) gets 1000, Position 1 (assistance/LEFT) gets 400
        self.editor_splitter.setSizes([1000, 400])
        
        layout.addWidget(self.editor_splitter)
        
        self.update_warning_banner()

        return widget

    def create_grid_view_widget_for_home(self):
        """Create Grid View widget with translation results panel on the right (memoQ style)"""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        
        # Main horizontal splitter: Grid+Segment Editor (left) and Translation Results (right)
        main_horizontal_splitter = QSplitter(Qt.Orientation.Horizontal)
        
        # Left side: Vertical splitter with Grid (top) and Segment Editor/Comments (bottom)
        left_vertical_splitter = QSplitter(Qt.Orientation.Vertical)
        
        # Top of left side: Grid container with filter boxes
        grid_container = QWidget()
        grid_layout = QVBoxLayout(grid_container)
        grid_layout.setContentsMargins(0, 0, 0, 0)
        grid_layout.setSpacing(5)
        
        # Warning banner for replace in source (hidden by default)
        home_warning = self._get_warning_banner('home_grid')
        grid_layout.addWidget(home_warning)
        home_warning.hide()  # Hidden by default
        
        # Filter panel (like memoQ)
        filter_panel = QWidget()
        filter_layout = QHBoxLayout(filter_panel)
        filter_layout.setContentsMargins(0, 0, 0, 0)
        filter_layout.setSpacing(10)
        
        # Source filter
        source_filter_label = QLabel(self.tr("Source:"))
        self.source_filter = self._ensure_shared_filter(
            'source_filter',
            "Type to filter source segments... (Press Enter or click Filter)",
            on_return=self.apply_filters,
        )
        
        # Target filter
        target_filter_label = QLabel(self.tr("Target:"))
        self.target_filter = self._ensure_shared_filter(
            'target_filter',
            "Type to filter target segments... (Press Enter or click Filter)",
            on_return=self.apply_filters,
        )
        
        # Filter button (activates the filter)
        apply_filter_btn = QPushButton(self.tr("Filter"))
        apply_filter_btn.clicked.connect(self.apply_filters)
        apply_filter_btn.setMaximumWidth(80)
        apply_filter_btn.setStyleSheet("background-color: #4CAF50; color: white; font-weight: bold; padding: 3px 5px;")
        
        # Clear filters button
        clear_filters_btn = QPushButton(self.tr("Clear Filters"))
        clear_filters_btn.clicked.connect(self.clear_filters)
        clear_filters_btn.setMaximumWidth(100)
        clear_filters_btn.setStyleSheet("padding: 3px 5px;")
        
        # Quick Filters dropdown menu
        quick_filter_btn = QPushButton(self.tr("⚡ Quick Filters"))
        quick_filter_btn.setMaximumWidth(130)
        quick_filter_btn.setStyleSheet("background-color: #D84315; color: white; font-weight: bold; padding: 3px 5px;")
        quick_filter_menu = QMenu(self)
        quick_filter_menu.addAction("🔍 Empty segments", lambda: self.apply_quick_filter("empty"))
        quick_filter_menu.addAction("❌ Not started", lambda: self.apply_quick_filter("not_started"))
        quick_filter_menu.addAction("✓ Confirmed", lambda: self.apply_quick_filter("confirmed"))
        quick_filter_menu.addAction("🔒 Locked", lambda: self.apply_quick_filter("locked"))
        quick_filter_menu.addAction("🔓 Not locked", lambda: self.apply_quick_filter("not_locked"))
        quick_filter_menu.addAction("💬 Commented", lambda: self.apply_quick_filter("commented"))
        quick_filter_btn.setMenu(quick_filter_menu)
        
        # Advanced Filters dialog button
        advanced_filter_btn = QPushButton(self.tr("⚙️ Advanced Filters"))
        advanced_filter_btn.clicked.connect(self.show_advanced_filters_dialog)
        advanced_filter_btn.setMaximumWidth(160)
        advanced_filter_btn.setStyleSheet("background-color: #2196F3; color: white; font-weight: bold; padding: 3px 5px;")

        # Sort dropdown button (similar to memoQ)
        sort_btn = QPushButton(self.tr("⇅ Sort"))
        sort_btn.setMaximumWidth(100)
        sort_btn.setStyleSheet("background-color: #FF9800; color: white; font-weight: bold; padding: 3px 5px;")
        sort_menu = QMenu(self)

        # Initialize sort state if not exists
        if not hasattr(self, 'current_sort'):
            self.current_sort = None  # None = document order

        # Sort by source text
        sort_menu.addAction("📝 Source A → Z", lambda: self.apply_sort('source_asc'))
        sort_menu.addAction("📝 Source Z → A", lambda: self.apply_sort('source_desc'))

        sort_menu.addSeparator()

        # Sort by target text
        sort_menu.addAction("📄 Target A → Z", lambda: self.apply_sort('target_asc'))
        sort_menu.addAction("📄 Target Z → A", lambda: self.apply_sort('target_desc'))

        sort_menu.addSeparator()

        # Sort by text length
        sort_menu.addAction("📏 Source (longer first)", lambda: self.apply_sort('source_length_desc'))
        sort_menu.addAction("📏 Source (shorter first)", lambda: self.apply_sort('source_length_asc'))
        sort_menu.addAction("📏 Target (longer first)", lambda: self.apply_sort('target_length_desc'))
        sort_menu.addAction("📏 Target (shorter first)", lambda: self.apply_sort('target_length_asc'))

        sort_menu.addSeparator()

        # Sort by match rate
        sort_menu.addAction("🎯 Match Rate (higher first)", lambda: self.apply_sort('match_desc'))
        sort_menu.addAction("🎯 Match Rate (lower first)", lambda: self.apply_sort('match_asc'))

        sort_menu.addSeparator()

        # Sort by frequency
        sort_menu.addAction("📊 Source Frequency (higher first)", lambda: self.apply_sort('source_freq_desc'))
        sort_menu.addAction("📊 Source Frequency (lower first)", lambda: self.apply_sort('source_freq_asc'))
        sort_menu.addAction("📊 Target Frequency (higher first)", lambda: self.apply_sort('target_freq_desc'))
        sort_menu.addAction("📊 Target Frequency (lower first)", lambda: self.apply_sort('target_freq_asc'))

        sort_menu.addSeparator()

        # Sort by last changed
        sort_menu.addAction("🕒 Last Changed (newest first)", lambda: self.apply_sort('modified_desc'))
        sort_menu.addAction("🕒 Last Changed (oldest first)", lambda: self.apply_sort('modified_asc'))

        sort_menu.addSeparator()

        # Sort by row status
        sort_menu.addAction("🚦 Row Status", lambda: self.apply_sort('status'))

        sort_menu.addSeparator()

        # Reset to document order
        sort_menu.addAction("↩️ Document Order (default)", lambda: self.apply_sort(None))

        sort_btn.setMenu(sort_menu)
        sort_btn.setToolTip(self.tr("Sort segments by various criteria"))

        # File filter dropdown (for multi-file projects)
        self.file_filter_combo = QComboBox()
        self.file_filter_combo.setMinimumWidth(150)
        self.file_filter_combo.setMaximumWidth(250)
        self.file_filter_combo.addItem("📁 All Files", None)
        self.file_filter_combo.currentIndexChanged.connect(self._on_file_filter_changed)
        self.file_filter_combo.setToolTip(self.tr("Filter segments by file (multi-file projects only)"))
        self.file_filter_combo.hide()  # Hidden by default, shown for multi-file projects

        # Show Invisibles button with dropdown menu
        show_invisibles_btn_home = QPushButton(self.tr("¶ Show Invisibles"))
        show_invisibles_btn_home.setMaximumWidth(140)
        show_invisibles_btn_home.setStyleSheet("background-color: #607D8B; color: white; font-weight: bold; padding: 3px 5px;")
        show_invisibles_menu_home = QMenu(show_invisibles_btn_home)

        # Use the same actions (they're stored as instance variables)
        if not hasattr(self, 'show_spaces_action'):
            # Create actions if they don't exist yet (first time)
            self.show_spaces_action = show_invisibles_menu_home.addAction("Spaces (·)")
            self.show_spaces_action.setCheckable(True)
            self.show_spaces_action.setChecked(False)
            self.show_spaces_action.triggered.connect(lambda: self.toggle_invisible_display('spaces'))

            self.show_tabs_action = show_invisibles_menu_home.addAction("Tabs (→)")
            self.show_tabs_action.setCheckable(True)
            self.show_tabs_action.setChecked(True)
            self.show_tabs_action.triggered.connect(lambda: self.toggle_invisible_display('tabs'))

            self.show_nbsp_action = show_invisibles_menu_home.addAction("Non-breaking Spaces")
            self.show_nbsp_action.setCheckable(True)
            self.show_nbsp_action.setChecked(True)
            self.show_nbsp_action.triggered.connect(lambda: self.toggle_invisible_display('nbsp'))

            self.show_linebreaks_action = show_invisibles_menu_home.addAction("Line Breaks (¶)")
            self.show_linebreaks_action.setCheckable(True)
            self.show_linebreaks_action.setChecked(True)
            self.show_linebreaks_action.triggered.connect(lambda: self.toggle_invisible_display('linebreaks'))

            show_invisibles_menu_home.addSeparator()

            toggle_all_action = show_invisibles_menu_home.addAction("Toggle All")
            toggle_all_action.triggered.connect(self.toggle_all_invisibles)
        else:
            # Reuse existing actions
            show_invisibles_menu_home.addAction(self.show_spaces_action)
            show_invisibles_menu_home.addAction(self.show_tabs_action)
            show_invisibles_menu_home.addAction(self.show_nbsp_action)
            show_invisibles_menu_home.addAction(self.show_linebreaks_action)
            show_invisibles_menu_home.addSeparator()
            toggle_all_action = show_invisibles_menu_home.addAction("Toggle All")
            toggle_all_action.triggered.connect(self.toggle_all_invisibles)

        show_invisibles_btn_home.setMenu(show_invisibles_menu_home)

        # Spellcheck toggle button (reuse the same instance variable from Editor tab)
        if not hasattr(self, 'spellcheck_btn') or self.spellcheck_btn is None:
            # Create spellcheck button if it doesn't exist yet
            self.spellcheck_btn = QPushButton(self.tr("📝 Spellcheck"))
            self.spellcheck_btn.setMaximumWidth(120)
            self.spellcheck_btn.setCheckable(True)
            self.spellcheck_btn.setChecked(self.spellcheck_enabled)
            self._update_spellcheck_button_style()
            
            # Create dropdown menu for spellcheck options
            spellcheck_menu = QMenu(self.spellcheck_btn)
            
            # Toggle spellcheck action
            self.spellcheck_toggle_action = spellcheck_menu.addAction("Enable Spellcheck")
            self.spellcheck_toggle_action.setCheckable(True)
            self.spellcheck_toggle_action.setChecked(self.spellcheck_enabled)
            self.spellcheck_toggle_action.triggered.connect(self._toggle_spellcheck)
            
            spellcheck_menu.addSeparator()
            
            # Open custom dictionary action
            open_dict_action = spellcheck_menu.addAction("📖 Manage Custom Dictionary...")
            open_dict_action.triggered.connect(self._open_custom_dictionary_dialog)
            
            # Show spellcheck info
            info_action = spellcheck_menu.addAction("ℹ️ Spellcheck Info")
            info_action.triggered.connect(self._show_spellcheck_info)
            
            self.spellcheck_btn.setMenu(spellcheck_menu)
            self.spellcheck_btn.clicked.connect(self._toggle_spellcheck_from_button)

        filter_layout.addWidget(source_filter_label)
        filter_layout.addWidget(self.source_filter, stretch=1)
        filter_layout.addWidget(target_filter_label)
        filter_layout.addWidget(self.target_filter, stretch=1)
        filter_layout.addWidget(apply_filter_btn)
        filter_layout.addWidget(clear_filters_btn)
        filter_layout.addWidget(quick_filter_btn)
        filter_layout.addWidget(advanced_filter_btn)
        filter_layout.addWidget(sort_btn)  # Sort dropdown
        filter_layout.addWidget(self.file_filter_combo)  # File filter for multi-file projects
        filter_layout.addWidget(show_invisibles_btn_home)
        filter_layout.addWidget(self.spellcheck_btn)
        
        # Don't add filter_panel to grid_layout yet - will be positioned based on tabs_above_grid setting
        
        # Pagination controls (like Tkinter version)
        pagination_panel = QWidget()
        pagination_panel.setStyleSheet("padding: 5px;")
        pagination_layout = QHBoxLayout(pagination_panel)
        pagination_layout.setContentsMargins(10, 5, 10, 5)
        pagination_layout.setSpacing(10)
        
        # Pagination label (left side)
        if not hasattr(self, 'pagination_label') or not self._widget_is_alive(self.pagination_label):
            self.pagination_label = QLabel(self.tr("Segments 1-50 of 0"))
        self.pagination_label.setStyleSheet("color: #555;")
        pagination_layout.addWidget(self.pagination_label)

        # Tip label for Ctrl+, shortcut (subtle, helpful for new users)
        tip_label = QLabel(f"💡 Tip: {format_shortcut_for_display('Ctrl+,')} inserts the next tag from source")
        tip_label.setStyleSheet("color: #888; font-size: 9pt; margin-left: 20px;")
        tip_label.setToolTip(self.tr("Select text first to wrap it with a tag pair (e.g., <b>selection</b>)"))
        pagination_layout.addWidget(tip_label)

        pagination_layout.addStretch()
        
        # Pagination controls (right side)
        # First page button
        if not hasattr(self, 'first_page_btn') or not self._widget_is_alive(self.first_page_btn):
            self.first_page_btn = QPushButton(self.tr("⏮ First"))
            self.first_page_btn.setMaximumWidth(70)
            if hasattr(self, 'go_to_first_page'):
                self.first_page_btn.clicked.connect(self.go_to_first_page)
        pagination_layout.addWidget(self.first_page_btn)
        
        # Previous page button
        if not hasattr(self, 'prev_page_btn') or not self._widget_is_alive(self.prev_page_btn):
            self.prev_page_btn = QPushButton(self.tr("◀ Prev"))
            self.prev_page_btn.setMaximumWidth(70)
            if hasattr(self, 'go_to_prev_page'):
                self.prev_page_btn.clicked.connect(self.go_to_prev_page)
        pagination_layout.addWidget(self.prev_page_btn)
        
        # Page number input
        page_label = QLabel(self.tr("Page:"))
        pagination_layout.addWidget(page_label)
        
        if not hasattr(self, 'page_number_input') or not self._widget_is_alive(self.page_number_input):
            self.page_number_input = QLineEdit()
            self.page_number_input.setMaximumWidth(50)
            self.page_number_input.setText("1")
            if hasattr(self, 'go_to_page'):
                self.page_number_input.returnPressed.connect(self.go_to_page)
        pagination_layout.addWidget(self.page_number_input)
        
        if not hasattr(self, 'total_pages_label') or not self._widget_is_alive(self.total_pages_label):
            self.total_pages_label = QLabel(self.tr("of 1"))
        pagination_layout.addWidget(self.total_pages_label)
        
        # Next page button
        if not hasattr(self, 'next_page_btn') or not self._widget_is_alive(self.next_page_btn):
            self.next_page_btn = QPushButton(self.tr("Next ▶"))
            self.next_page_btn.setMaximumWidth(70)
            if hasattr(self, 'go_to_next_page'):
                self.next_page_btn.clicked.connect(self.go_to_next_page)
        pagination_layout.addWidget(self.next_page_btn)
        
        # Last page button
        if not hasattr(self, 'last_page_btn') or not self._widget_is_alive(self.last_page_btn):
            self.last_page_btn = QPushButton(self.tr("Last ⏭"))
            self.last_page_btn.setMaximumWidth(70)
            if hasattr(self, 'go_to_last_page'):
                self.last_page_btn.clicked.connect(self.go_to_last_page)
        pagination_layout.addWidget(self.last_page_btn)
        
        # Page size selector
        page_size_label = QLabel(self.tr("Per page:"))
        pagination_layout.addWidget(page_size_label)
        
        if not hasattr(self, 'page_size_combo') or not self._widget_is_alive(self.page_size_combo):
            self.page_size_combo = QComboBox()
            self.page_size_combo.addItems(["5", "10", "25", "50", "100", "200", "500", "All"])
            # Default to showing every segment ("All"). _maybe_auto_set_page_size()
            # drops large projects (>2000 segments) back to a paged size on load.
            if not hasattr(self, 'grid_page_size'):
                self.grid_page_size = 999999
            self.page_size_combo.setCurrentText(str(self.grid_page_size) if self.grid_page_size != 999999 else "All")
            if hasattr(self, 'on_page_size_changed'):
                self.page_size_combo.currentTextChanged.connect(self.on_page_size_changed)
        self.page_size_combo.setMaximumWidth(80)
        pagination_layout.addWidget(self.page_size_combo)
        
        # Create a combined filter + pagination container for flexible positioning
        filter_pagination_container = QWidget()
        filter_pagination_layout = QVBoxLayout(filter_pagination_container)
        filter_pagination_layout.setContentsMargins(0, 0, 0, 0)
        filter_pagination_layout.setSpacing(2)
        filter_pagination_layout.addWidget(filter_panel)
        filter_pagination_layout.addWidget(pagination_panel)
        
        # Store reference for later positioning
        self.filter_pagination_container = filter_pagination_container
        
        # Note: filter_pagination_container will be added to layout based on tabs_above_grid setting
        
        # Note: Pagination methods (go_to_first_page, go_to_prev_page, etc.) will be implemented
        # when pagination functionality is fully added. For now, buttons are created but won't work.
        
        # Translation Grid (create it if it doesn't exist or has been deleted)
        if not hasattr(self, 'table') or not self._widget_is_alive(self.table):
            self.create_translation_grid()
        grid_layout.addWidget(self.table)
        
        # Compact toolbar below grid with action buttons
        toolbar = QWidget()
        toolbar_layout = QHBoxLayout(toolbar)
        toolbar_layout.setContentsMargins(5, 5, 5, 5)
        toolbar_layout.setSpacing(8)
        
        # Segment info label
        tab_seg_info = QLabel(self.tr("Segment: -"))
        tab_seg_info.setStyleSheet("font-weight: bold;")
        toolbar_layout.addWidget(tab_seg_info)
        
        # View mode segmented control (WYSIWYG / Compact / Tags)
        from PyQt6.QtWidgets import QButtonGroup

        view_mode_container = QWidget()
        view_mode_layout = QHBoxLayout(view_mode_container)
        view_mode_layout.setContentsMargins(0, 0, 0, 0)
        view_mode_layout.setSpacing(0)

        # Create button group to ensure only one is checked
        view_mode_group = QButtonGroup(self)
        view_mode_group.setExclusive(True)

        _seg_btn_base = """
            QPushButton {{
                background-color: #757575;
                color: white;
                font-weight: bold;
                padding: 4px 6px;
                font-size: 8pt;
                border: none;
                {radius}
            }}
            QPushButton:checked {{
                background-color: #9C27B0;
            }}
            QPushButton:hover:!checked {{
                background-color: #858585;
            }}
            QPushButton:focus {{
                outline: none;
            }}
        """

        # WYSIWYG button (left)
        wysiwyg_btn = QPushButton(self.tr("WYSIWYG"))
        wysiwyg_btn.setCheckable(True)
        wysiwyg_btn.setChecked(False)
        wysiwyg_btn.setToolTip(
            f"WYSIWYG View ({format_shortcut_for_display('Ctrl+Shift+H')})\nShows formatted text without raw tags"
        )
        wysiwyg_btn.setStyleSheet(_seg_btn_base.format(
            radius="border-top-left-radius: 3px; border-bottom-left-radius: 3px;"
        ))
        wysiwyg_btn.clicked.connect(lambda: self._set_tag_view_mode('wysiwyg'))
        view_mode_group.addButton(wysiwyg_btn, 0)
        view_mode_layout.addWidget(wysiwyg_btn)

        # Compact button (middle) – shortens verbose tags to {1}, {/1}
        compact_btn = QPushButton(self.tr("Compact"))
        compact_btn.setCheckable(True)
        compact_btn.setChecked(False)
        compact_btn.setToolTip(
            self.tr("Compact Tag View\nShortens verbose tags like <bmk id=\"0\" ...> to {1}")
        )
        compact_btn.setStyleSheet(_seg_btn_base.format(radius=""))
        compact_btn.clicked.connect(lambda: self._set_tag_view_mode('compact'))
        view_mode_group.addButton(compact_btn, 2)
        view_mode_layout.addWidget(compact_btn)

        # Tags button (right)
        tags_btn = QPushButton(self.tr("Tags"))
        tags_btn.setCheckable(True)
        tags_btn.setChecked(True)  # Default: Tags mode
        tags_btn.setToolTip(
            f"Tag View ({format_shortcut_for_display('Ctrl+Shift+H')})\nShows raw tags like <b>bold</b>"
        )
        tags_btn.setStyleSheet(_seg_btn_base.format(
            radius="border-top-right-radius: 3px; border-bottom-right-radius: 3px;"
        ))
        tags_btn.clicked.connect(lambda: self._set_tag_view_mode('tags'))
        view_mode_group.addButton(tags_btn, 1)
        view_mode_layout.addWidget(tags_btn)

        # Never narrower than their text. The style sheet's bold 8 pt is not in
        # the buttons' own size hint, so on a narrow window the toolbar
        # squeezed them until "WYSIWYG" read "YSIWY".
        _btn_font = QFont(wysiwyg_btn.font())
        _btn_font.setPointSize(8)
        _btn_font.setBold(True)
        _fm = QFontMetrics(_btn_font)
        for _btn in (wysiwyg_btn, compact_btn, tags_btn):
            _btn.setMinimumWidth(_fm.horizontalAdvance(_btn.text()) + 16)  # + padding 6+6 and a margin

        toolbar_layout.addWidget(view_mode_container)

        # Store references for keyboard shortcut and programmatic access
        self.wysiwyg_btn = wysiwyg_btn
        self.compact_btn = compact_btn
        self.tags_btn = tags_btn
        self.view_mode_group = view_mode_group

        # Initialize tag view state: 'tags' (default), 'compact', or 'wysiwyg'
        if not hasattr(self, 'tag_view_mode'):
            self.tag_view_mode = 'tags'
        if not hasattr(self, 'show_tags'):
            self.show_tags = True  # Default: show tags
        
        # Status selector
        from modules.statuses import get_status, STATUSES
        status_label = QLabel(self.tr("Status:"))
        tab_status_combo = QComboBox()
        tab_status_combo.setMinimumWidth(130)  # Ensure full status text is visible
        for status_key in STATUSES.keys():
            definition = get_status(status_key)
            tab_status_combo.addItem(definition.label, status_key)
        tab_status_combo.currentIndexChanged.connect(self.on_tab_status_combo_changed)
        toolbar_layout.addWidget(status_label)
        toolbar_layout.addWidget(tab_status_combo)
        
        toolbar_layout.addWidget(QLabel("|"))  # Separator
        
        preview_prompt_btn = QPushButton(self.tr("🧪 Preview Prompts"))
        preview_prompt_btn.setToolTip(self.tr("Preview the complete assembled prompt\n(System Prompt + Custom Prompts + current segment)"))
        preview_prompt_btn.setStyleSheet("background-color: #9C27B0; color: white; font-weight: bold; padding: 3px 5px; border: none; outline: none;")
        preview_prompt_btn.clicked.connect(self._preview_combined_prompt_from_grid)
        toolbar_layout.addWidget(preview_prompt_btn)

        autotag_btn = QPushButton(self.tr("🏷️ AutoTagger"))
        autotag_btn.setToolTip(self.tr(
            "AutoTagger: ask the AI to place the source segment's inline tags into "
            f"the current translation ({format_shortcut_for_display('Ctrl+Alt+G')})"))
        autotag_btn.setStyleSheet("background-color: #00897B; color: white; font-weight: bold; padding: 3px 5px; border: none; outline: none;")
        autotag_btn.clicked.connect(self.autotag_current_segment)
        toolbar_layout.addWidget(autotag_btn)

        
        # Always-On Voice toggle button
        alwayson_btn = QPushButton(self.tr("🎧 Always-On: OFF"))
        alwayson_btn.setCheckable(True)
        alwayson_btn.setChecked(False)
        alwayson_btn.setStyleSheet("""
            QPushButton {
                background-color: #757575;
                color: white;
                font-weight: bold;
                padding: 3px 5px;
                border-radius: 3px;
            }
            QPushButton:checked {
                background-color: #2E7D32;
            }
            QPushButton:focus {
                outline: none;
            }
        """)
        alwayson_btn.setToolTip(self.tr("Voice commands – listen for spoken commands until switched off\nSpeech that isn't a command is never typed"))
        alwayson_btn.clicked.connect(lambda checked: self._toggle_alwayson_from_grid_btn(checked, alwayson_btn))
        toolbar_layout.addWidget(alwayson_btn)
        self.grid_alwayson_btn = alwayson_btn  # Store reference

        toolbar_layout.addWidget(QLabel("|"))  # Separator

        # Log button – opens the session log in its own movable window.
        # Replaces the old right-panel "Session Log" tab; the same action is
        # also available via Tools ▸ Log Window.
        log_window_btn = QPushButton(self.tr("📋 Log"))
        log_window_btn.setToolTip(self.tr("Open the session log in a separate window that can be moved to another screen"))
        log_window_btn.clicked.connect(self.detach_log_window)
        toolbar_layout.addWidget(log_window_btn)

        toolbar_layout.addStretch()
        
        save_next_btn = QPushButton(self.tr("✓ Confirm && Next"))
        save_next_btn.setStyleSheet("background-color: #2196F3; color: white; font-weight: bold; padding: 3px 5px; border: none; outline: none;")
        save_next_btn.clicked.connect(self.confirm_selected_or_next)
        save_next_btn.setToolTip(
            f"Confirm current segment and go to next unconfirmed ({format_shortcut_for_display('Ctrl+Enter')})"
        )
        toolbar_layout.addWidget(save_next_btn)
        
        grid_layout.addWidget(toolbar)
        
        # Store references
        self.tab_seg_info = tab_seg_info
        self.tab_status_combo = tab_status_combo
        
        # Create TermLens and Session Log tabs widget
        from PyQt6.QtWidgets import QTabWidget
        from modules.termlens_widget import TermLensWidget
        
        bottom_tabs = QTabWidget()
        # Same Mac-elide fix as right_tabs: stop QTabBar from truncating
        # labels to "Term…" / "Sessio…" when there's plenty of room.
        bottom_tabs.tabBar().setElideMode(Qt.TextElideMode.ElideNone)
        bottom_tabs.tabBar().setUsesScrollButtons(True)
        bottom_tabs.setStyleSheet("QTabBar::tab { outline: 0; } QTabBar::tab:focus { outline: none; } QTabBar::tab:selected { border-bottom: 1px solid #2196F3; background-color: rgba(33, 150, 243, 0.08); }")
        
        # TermLens tab
        self.termlens_widget = TermLensWidget(self, db_manager=self.db_manager, log_callback=self.log, theme_manager=self.theme_manager)
        set_help_topic(self.termlens_widget, HelpTopics.GLOSSARY_TERMLENS)
        self.termlens_widget.term_insert_requested.connect(self.insert_termlens_text)
        self.termlens_widget.edit_entry_requested.connect(self._on_termlens_edit_entry)
        self.termlens_widget.delete_entry_requested.connect(self._on_termlens_delete_entry)
        self.termlens_widget.font_size_changed.connect(self._on_termlens_font_size_changed)
        # v1.10.68: refresh button rebuilds the in-memory termbase
        # index (covers cross-process edits — e.g. terms deleted /
        # added via the Supervertaler for Trados plugin while
        # Workbench is open against the same shared SQLite database).
        self.termlens_widget.refresh_requested.connect(self._on_termlens_refresh_requested)

        # Apply saved termlens font settings
        font_settings = self.load_general_settings()
        termlens_family = font_settings.get('termlens_font_family', 'Segoe UI')
        termlens_size = font_settings.get('termlens_font_size', 10)
        termlens_bold = font_settings.get('termlens_font_bold', False)
        self.termlens_widget.set_font_settings(termlens_family, termlens_size, termlens_bold)
        
        # Session Log tab
        session_log_widget = QWidget()
        session_log_layout = QVBoxLayout(session_log_widget)
        session_log_layout.setContentsMargins(5, 5, 5, 5)
        
        self.session_log_text = QPlainTextEdit()
        self.session_log_text.setReadOnly(True)
        self.session_log_text.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.session_log_text.setStyleSheet("font-family: 'Consolas', 'Courier New', monospace; font-size: 9pt;")
        session_log_layout.addWidget(self.session_log_text)
        
        # Segment-comments sub-tab. v1.10.56 restructured this into a
        # split view:
        #   • Top: scrollable list of every segment in the project with a
        #     comment. Each entry has a clickable "Segment #N" header
        #     that jumps to that segment in the grid (cross-page aware).
        #   • Bottom: editor for the CURRENTLY SELECTED segment's
        #     comment, identical to the pre-1.10.56 behaviour.
        # The list rebuilds when a comment is edited (via
        # _on_bottom_notes_changed) and when a project is loaded.
        notes_widget = QWidget()
        notes_layout = QVBoxLayout(notes_widget)
        notes_layout.setContentsMargins(5, 5, 5, 5)
        notes_layout.setSpacing(4)

        from PyQt6.QtWidgets import QScrollArea as _QScrollArea, QFrame as _QFrame

        # ── All-segment-comments list (fills the tab) ──
        # Comments are added from the editor (select text and press Ctrl+M, or
        # right-click → Add comment) and edited/deleted here via right-click on a
        # Segment #N header. The old "Comment on current segment" editor box was
        # removed (v1.10.142): it could only hold one unanchored comment and
        # destructively replaced the whole list.
        self._segment_comments_list_container = QWidget()
        self._segment_comments_list_layout = QVBoxLayout(self._segment_comments_list_container)
        self._segment_comments_list_layout.setContentsMargins(0, 0, 0, 0)
        self._segment_comments_list_layout.setSpacing(0)
        # Initial placeholder; replaced by _refresh_segment_comments_list().
        _initial_empty = QLabel(
            "(No segment comments yet — add one from the editor: select text and "
            "press Ctrl+M, or right-click → Add comment.)")
        _initial_empty.setStyleSheet("color: #999; font-style: italic; padding: 8px;")
        _initial_empty.setWordWrap(True)
        self._segment_comments_list_layout.addWidget(_initial_empty)
        self._segment_comments_list_layout.addStretch()

        comments_list_scroll = _QScrollArea()
        comments_list_scroll.setWidgetResizable(True)
        comments_list_scroll.setFrameShape(_QFrame.Shape.NoFrame)
        comments_list_scroll.setWidget(self._segment_comments_list_container)
        # Stored so _open_comment_in_panel() can scroll a specific comment
        # (reached via "Open comment" on a cell's anchored highlight) into view.
        self._segment_comments_list_scroll = comments_list_scroll
        notes_layout.addWidget(comments_list_scroll)
        
        # Add only TermLens tab to bottom_tabs (Segment Note and Session Log will be in right panel)
        bottom_tabs.addTab(self.termlens_widget, "🔍 TermLens")

        # QuickTrans docked panel – a trimmed, always-on version of the
        # QuickTrans popup. Auto-fetches cheap MT for the current segment;
        # LLMs stay on-demand. Lives as a second tab next to TermLens.
        try:
            from modules.quicktrans import QuickTransPanel
            self.quicktrans_panel = QuickTransPanel(self)
            self.quicktrans_panel.translation_selected.connect(self._insert_quicktrans_translation)
            bottom_tabs.addTab(self.quicktrans_panel, "⚡ QuickTrans")
        except Exception as e:
            self.quicktrans_panel = None
            self.log(f"Could not create QuickTrans panel: {e}")

        # Default to TermLens tab (index 0)
        bottom_tabs.setCurrentIndex(0)

        # Store reference to bottom_tabs for later access
        self.bottom_tabs = bottom_tabs
        # Keep the View-menu dock checkmarks in sync as the user switches
        # between the TermLens and QuickTrans tabs (and remember the choice).
        bottom_tabs.currentChanged.connect(self._on_bottom_tab_changed)
        
        # Proofreading-comments sub-tab (issue #234 pts 4/5). Restructured from
        # a read-only per-segment browser into an all-project list that mirrors
        # the Segment-Comments list, so both sub-tabs behave the same way. Each
        # entry shows a clickable "Segment #N" header (jumps to the segment),
        # the LLM model name, the proofreading text, and a delete button. The
        # PC text itself stays read-only (only deletion is offered).
        proofreading_notes_widget = QWidget()
        proofreading_notes_layout = QVBoxLayout(proofreading_notes_widget)
        proofreading_notes_layout.setContentsMargins(5, 5, 5, 5)
        proofreading_notes_layout.setSpacing(4)

        self._proofreading_comments_list_container = QWidget()
        self._proofreading_comments_list_layout = QVBoxLayout(self._proofreading_comments_list_container)
        self._proofreading_comments_list_layout.setContentsMargins(0, 0, 0, 0)
        self._proofreading_comments_list_layout.setSpacing(0)
        _pc_initial_empty = QLabel(
            "(No proofreading comments yet — run <b>QA ▸ Proofreading ▸ "
            "Proofread Translation…</b> to generate AI review comments.)")
        _pc_initial_empty.setTextFormat(Qt.TextFormat.RichText)
        _pc_initial_empty.setStyleSheet("color: #999; font-style: italic; padding: 8px;")
        _pc_initial_empty.setWordWrap(True)
        self._proofreading_comments_list_layout.addWidget(_pc_initial_empty)
        self._proofreading_comments_list_layout.addStretch()

        proofreading_list_scroll = _QScrollArea()
        proofreading_list_scroll.setWidgetResizable(True)
        proofreading_list_scroll.setFrameShape(_QFrame.Shape.NoFrame)
        proofreading_list_scroll.setWidget(self._proofreading_comments_list_container)
        self._proofreading_comments_list_scroll = proofreading_list_scroll
        proofreading_notes_layout.addWidget(proofreading_list_scroll)

        # Store notes_widget, proofreading_notes_widget, and session_log_widget for adding to right panel later
        self._notes_widget_for_right_panel = notes_widget
        self._proofreading_notes_widget_for_right_panel = proofreading_notes_widget
        self._session_log_widget_for_right_panel = session_log_widget
        
        # Scratchpad widget (for private translator notes - project-level, not segment-level)
        scratchpad_widget = QWidget()
        scratchpad_layout = QVBoxLayout(scratchpad_widget)
        scratchpad_layout.setContentsMargins(5, 5, 5, 5)
        
        # Info label at top
        scratchpad_info = QLabel(
            "📝 <b>Private notes for this project</b><br>"
            "<small>Stored in .svproj file only – never exported to CAT tools</small>"
        )
        scratchpad_info.setWordWrap(True)
        scratchpad_info.setStyleSheet("color: #666; font-size: 9pt; padding: 3px; background: #f9f9f9; border-radius: 3px; margin-bottom: 5px;")
        scratchpad_layout.addWidget(scratchpad_info)
        
        self.scratchpad_edit = QPlainTextEdit()
        self.scratchpad_edit.setPlaceholderText(
            "Use this scratchpad for your private notes:\n\n"
            "• Terminology decisions\n"
            "• Client preferences\n"
            "• Research findings\n"
            "• Questions to ask\n"
            "• Reminders"
        )
        self.scratchpad_edit.setStyleSheet("font-family: 'Consolas', 'Courier New', monospace; font-size: 10pt;")
        self.scratchpad_edit.textChanged.connect(self._on_scratchpad_changed)
        scratchpad_layout.addWidget(self.scratchpad_edit)
        
        self._scratchpad_widget_for_right_panel = scratchpad_widget
        
        # Create a container for the left side that will hold everything
        left_container = QWidget()
        left_container_layout = QVBoxLayout(left_container)
        left_container_layout.setContentsMargins(0, 0, 0, 0)
        left_container_layout.setSpacing(0)
        
        # Add widgets based on tabs_above_grid setting
        if self.tabs_above_grid:
            # Layout: Filter/Pagination → TermLens tabs → Grid
            # This keeps terminology matches closest to the segment being edited
            left_container_layout.addWidget(filter_pagination_container)
            left_vertical_splitter.addWidget(bottom_tabs)
            left_vertical_splitter.addWidget(grid_container)
            # Set vertical splitter proportions: Tabs smaller, grid larger
            left_vertical_splitter.setSizes([200, 600])
        else:
            # Layout: Filter/Pagination → Grid → TermLens tabs (default)
            left_container_layout.addWidget(filter_pagination_container)
            left_vertical_splitter.addWidget(grid_container)
            left_vertical_splitter.addWidget(bottom_tabs)
            # Set vertical splitter proportions: Grid larger, tabs smaller
            left_vertical_splitter.setSizes([600, 200])
        
        left_container_layout.addWidget(left_vertical_splitter)
        left_vertical_splitter.setHandleWidth(8)
        left_vertical_splitter.setChildrenCollapsible(False)

        # Keep references so the View-menu items can reposition TermLens live
        self.grid_container = grid_container
        self.left_vertical_splitter = left_vertical_splitter
        
        # Allow left panel to shrink smaller so right panel can expand
        left_container.setMinimumWidth(400)  # Allow grid to shrink to 400px minimum
        
        # Add left side to main horizontal splitter
        main_horizontal_splitter.addWidget(left_container)

        # Pre-load match panel font settings so _create_match_panel() uses the saved values
        try:
            _pre_settings = self.load_general_settings()
            _mp_size = _pre_settings.get('match_panel_font_size', 10)
            if 7 <= _mp_size <= 18:
                type(self).match_panel_font_size = _mp_size
            _mp_family = _pre_settings.get('match_panel_font_family', '')
            type(self).match_panel_font_family = _mp_family
            type(self).match_panel_font_bold = _pre_settings.get('match_panel_font_bold', False)
            # v1.9.307: Load TM pane layout preference (horizontal vs vertical stacking)
            # Default: True (vertical) – stacked gives each pane the full panel width
            self.match_panel_tm_vertical = _pre_settings.get('match_panel_tm_vertical', True)
        except Exception:
            self.match_panel_tm_vertical = True

        # Create tabbed container for right panel
        right_tabs = QTabWidget()
        right_tabs.tabBar().setFocusPolicy(Qt.FocusPolicy.NoFocus)
        right_tabs.tabBar().setDrawBase(False)
        right_tabs.tabBar().setExpanding(False)
        # macOS clips tab labels to ellipsis ("Match...", "AI Ass...",
        # "Proofreadin...", "Sessio...", "Scrat...") even when there is
        # plenty of horizontal space, because Qt's default ElideRight is
        # too aggressive on Mac. Force the tab bar to size each tab to its
        # full label and offer scroll buttons if the bar overflows the
        # available width — that way every label stays readable on Mac
        # and the layout is unchanged on Windows.
        right_tabs.tabBar().setElideMode(Qt.TextElideMode.ElideNone)
        right_tabs.tabBar().setUsesScrollButtons(True)
        right_tabs.setStyleSheet("QTabBar::tab { outline: 0; } QTabBar::tab:focus { outline: none; } QTabBar::tab:selected { border-bottom: 1px solid #2196F3; background-color: rgba(33, 150, 243, 0.08); }")

        # NOTE: Translation Results panel is deprecated - MT/LLM is now only via QuickTrans (Ctrl+Alt+Q global hotkey)
        # Panel is no longer created to save resources. Set to None for compatibility checks.
        self.translation_results_panel = None
        if not hasattr(self, 'results_panels'):
            self.results_panels = []

        # Track tab indices for visibility-aware default selection
        tab_index = 0
        preview_tab_index = -1

        # Tab 1: Match Panel (TermLens + TM Source/Target) - primary TM display
        match_panel_widget = self._create_match_panel()
        self.match_panel_widget = match_panel_widget  # Store reference for mode detection
        right_tabs.addTab(match_panel_widget, "🎯 Match Panel")
        match_panel_tab_index = tab_index
        tab_index += 1
        
        # Tab 2: Document Preview
        preview_widget = self._create_preview_tab()
        right_tabs.addTab(preview_widget, "📄 Preview")
        preview_tab_index = tab_index
        self._preview_tab_index = preview_tab_index  # Store for visibility checks
        tab_index += 1

        # Tab 3: Comments — single parent tab containing two sub-tabs
        # (Segment / Proofreading) so the related surfaces sit together
        # in the right panel. The Segment sub-tab additionally shows an
        # all-project list of every segment with a comment (built later
        # by _attach_all_segment_comments_list).
        from PyQt6.QtWidgets import QTabWidget as _QTabWidget
        comments_tab = QWidget()
        comments_tab_layout = QVBoxLayout(comments_tab)
        comments_tab_layout.setContentsMargins(0, 0, 0, 0)
        comments_tab_layout.setSpacing(0)

        self.comments_sub_tabs = _QTabWidget()
        # Keep the sub-tab bar visually subordinate to the parent tab bar.
        self.comments_sub_tabs.setDocumentMode(True)
        self.comments_sub_tabs.addTab(
            self._notes_widget_for_right_panel,
            "📝 Segment",
        )
        self.comments_sub_tabs.addTab(
            self._proofreading_notes_widget_for_right_panel,
            "✅ Proofreading",
        )
        comments_tab_layout.addWidget(self.comments_sub_tabs)
        right_tabs.addTab(comments_tab, "💬 Comments")
        # Index of the parent Comments tab, exposed for keyboard shortcuts
        # and any code that needs to switch to it programmatically (e.g.
        # the Ctrl+N "focus comments editor" handler in _focus_notes_tab).
        self._comments_tab_index = tab_index
        tab_index += 1

        # Session Log no longer has its own right-panel tab (v1.10.x). The
        # session_log_text widget below is still built and still receives every
        # log line, so it serves as the live buffer that the detached Log
        # window (toolbar "📋 Log" button / Tools ▸ Log Window) copies from.

        # Tab 6: Scratchpad (private translator notes for the whole project)
        right_tabs.addTab(self._scratchpad_widget_for_right_panel, "📝 Scratchpad")
        tab_index += 1

        # Note: AI Assistant tab is added later (after prompt_manager_qt is created)
        # via _add_assistant_to_right_panel(), since the grid is built before the AI tab.
        self._right_panel_next_tab_index = tab_index

        # Set default selected tab to Match Panel (always show Match Panel first)
        right_tabs.setCurrentIndex(match_panel_tab_index)
        
        # Store reference for later use
        self.right_tabs = right_tabs

        # Update AI Assistant context sidebar when its tab becomes visible
        def _on_right_tab_changed(index):
            if hasattr(self, '_assistant_tab_index') and index == self._assistant_tab_index:
                if hasattr(self, 'prompt_manager_qt') and self.prompt_manager_qt:
                    self.prompt_manager_qt._on_assistant_shown()
            # Re-render the document Preview whenever it becomes visible. The
            # preview is otherwise only rebuilt on a full grid (re)load, so
            # translating segments in place left it showing the stale (often
            # all-source) render from when the document was first loaded
            # (issue #224). Refreshing on show keeps it in step with the
            # current target text and segment statuses.
            if hasattr(self, '_preview_tab_index') and index == self._preview_tab_index:
                # Defer the (potentially expensive) refresh to the next event-loop
                # tick so the tab switch itself paints first — the user is taken to
                # the Preview immediately and the content updates a moment later.
                from PyQt6.QtCore import QTimer
                QTimer.singleShot(0, self._refresh_preview_on_show)
        right_tabs.currentChanged.connect(_on_right_tab_changed)

        # Allow right panel to expand larger for better splitter flexibility
        right_tabs.setMinimumWidth(250)  # Prevent complete collapse
        right_tabs.setMaximumWidth(16777215)  # Remove maximum width constraint (Qt max = 16777215)
        
        main_horizontal_splitter.addWidget(right_tabs)
        
        # Set stretch factors so both sides can resize freely
        main_horizontal_splitter.setStretchFactor(0, 7)  # Left panel gets 70% weight
        main_horizontal_splitter.setStretchFactor(1, 3)  # Right panel gets 30% weight
        
        # Set horizontal splitter proportions: Grid area larger, results smaller
        # Give more space to the grid+editor on the left
        main_horizontal_splitter.setSizes([900, 400])
        main_horizontal_splitter.setHandleWidth(8)
        main_horizontal_splitter.setChildrenCollapsible(False)
        
        layout.addWidget(main_horizontal_splitter)
        
        self.update_warning_banner()

        return widget

    def _maybe_auto_set_page_size(self):
        """Pick the grid page size once per freshly-opened project.

        Defaults to "All" (show every segment); for very large documents
        (> PAGE_AUTO_ALL_THRESHOLD segments) it falls back to
        PAGE_FALLBACK_SIZE per page. Runs once per project object — keyed on
        the project's identity — so re-renders (sort, filter, page change) and
        any manual "Per page" choice the user makes during the session are not
        overridden.
        """
        if not self.current_project or not self.current_project.segments:
            return
        key = id(self.current_project)
        if getattr(self, '_page_size_decided_for', None) == key:
            return
        self._page_size_decided_for = key

        total = len(self.current_project.segments)
        if total > self.PAGE_AUTO_ALL_THRESHOLD:
            self.grid_page_size = self.PAGE_FALLBACK_SIZE
            self.log(
                f"📄 Large project ({total} segments) — paginating at "
                f"{self.PAGE_FALLBACK_SIZE}/page. Switch to 'All' via the "
                f"Per-page selector if you prefer one page.")
        else:
            self.grid_page_size = 999999  # All
        self.grid_current_page = 0

        # Reflect the decision in the combo without retriggering the handler.
        if (hasattr(self, 'page_size_combo')
                and self._widget_is_alive(self.page_size_combo)):
            self.page_size_combo.blockSignals(True)
            self.page_size_combo.setCurrentText(
                "All" if self.grid_page_size >= 999999
                else str(self.grid_page_size))
            self.page_size_combo.blockSignals(False)

    def _get_total_pages(self) -> int:
        """Calculate total number of pages based on segment count and page size"""
        if not self.current_project or not self.current_project.segments:
            return 1
        total_segments = len(self.current_project.segments)
        if not hasattr(self, 'grid_page_size') or self.grid_page_size >= 999999:
            return 1  # "All" selected
        return max(1, (total_segments + self.grid_page_size - 1) // self.grid_page_size)

    def _update_pagination_ui(self):
        """Update pagination labels and button states"""
        if not self.current_project or not self.current_project.segments:
            if hasattr(self, 'pagination_label') and self._widget_is_alive(self.pagination_label):
                self.pagination_label.setText(self.tr("Segments 0-0 of 0"))
            if hasattr(self, 'total_pages_label') and self._widget_is_alive(self.total_pages_label):
                self.total_pages_label.setText(self.tr("of 1"))
            if hasattr(self, 'page_number_input') and self._widget_is_alive(self.page_number_input):
                self.page_number_input.setText("1")
            return

        total_segments = len(self.current_project.segments)
        total_pages = self._get_total_pages()

        if not hasattr(self, 'grid_current_page'):
            self.grid_current_page = 0
        if not hasattr(self, 'grid_page_size'):
            self.grid_page_size = 50

        # Clamp current page to valid range
        self.grid_current_page = max(0, min(self.grid_current_page, total_pages - 1))

        # Check if a filter is active (text filter or quick filter)
        filter_allowlist = getattr(self, '_active_text_filter_rows', None)
        is_filtered = filter_allowlist is not None

        # Calculate segment range for current page
        if self.grid_page_size >= 999999:
            # "All" mode - show all segments
            start_seg = 1
            end_seg = total_segments
        else:
            start_seg = self.grid_current_page * self.grid_page_size + 1
            end_seg = min((self.grid_current_page + 1) * self.grid_page_size, total_segments)

        # Update pagination label - show filtered count when filter is active
        if hasattr(self, 'pagination_label') and self._widget_is_alive(self.pagination_label):
            if is_filtered:
                filtered_count = len(filter_allowlist)
                self.pagination_label.setText(f"Showing {filtered_count} of {total_segments} segments")
            else:
                self.pagination_label.setText(f"Segments {start_seg}-{end_seg} of {total_segments}")
        
        if hasattr(self, 'total_pages_label') and self._widget_is_alive(self.total_pages_label):
            self.total_pages_label.setText(f"of {total_pages}")
        
        if hasattr(self, 'page_number_input') and self._widget_is_alive(self.page_number_input):
            self.page_number_input.setText(str(self.grid_current_page + 1))
        
        # Enable/disable navigation buttons
        is_first_page = self.grid_current_page == 0
        is_last_page = self.grid_current_page >= total_pages - 1
        
        if hasattr(self, 'first_page_btn') and self._widget_is_alive(self.first_page_btn):
            self.first_page_btn.setEnabled(not is_first_page)
        if hasattr(self, 'prev_page_btn') and self._widget_is_alive(self.prev_page_btn):
            self.prev_page_btn.setEnabled(not is_first_page)
        if hasattr(self, 'next_page_btn') and self._widget_is_alive(self.next_page_btn):
            self.next_page_btn.setEnabled(not is_last_page)
        if hasattr(self, 'last_page_btn') and self._widget_is_alive(self.last_page_btn):
            self.last_page_btn.setEnabled(not is_last_page)

    def _apply_pagination_to_grid(self):
        """Show/hide rows based on current page, optionally constrained by active filters.

        IMPORTANT: When a text filter is active (Filter Source/Target boxes), we show ALL
        matching rows across the entire document, ignoring pagination. This ensures users
        can find content regardless of which page they're on.
        
        When no filter is active, normal pagination applies.
        """
        if not self.current_project or not self.current_project.segments:
            return
        
        total_segments = len(self.current_project.segments)
        
        if not hasattr(self, 'grid_current_page'):
            self.grid_current_page = 0
        if not hasattr(self, 'grid_page_size'):
            self.grid_page_size = 50
        
        # If a text filter (Filter Source/Target) is active, it will populate an allowlist
        # of row indices that are allowed to be visible.
        filter_allowlist = getattr(self, '_active_text_filter_rows', None)

        # Build set of empty structural segments (always hidden regardless of pagination/filter)
        segments = self.current_project.segments
        empty_rows = {i for i in range(total_segments) if not segments[i].source.strip()}

        # Compute the set of rows that will be visible. We do this first (rather
        # than iterating + flipping setRowHidden) so the lazy-populate pass
        # below knows exactly which rows it needs to install widgets for.
        if filter_allowlist is not None:
            # Filter mode: show all matching rows across the entire document.
            rows_to_show = {r for r in filter_allowlist if r not in empty_rows}
        else:
            # Normal pagination mode.
            if self.grid_page_size >= 999999:
                # "All" mode - show everything.
                start_row = 0
                end_row = total_segments
            else:
                start_row = self.grid_current_page * self.grid_page_size
                end_row = min(start_row + self.grid_page_size, total_segments)
            rows_to_show = {r for r in range(start_row, end_row) if r not in empty_rows}

        # v1.10.147 (page-aware grid loading – based on Hans Lenting's
        # Simpelvertaler fork): rows that haven't yet had their full editor
        # widgets installed get them now, just-in-time, so the user only ever
        # pays the widget-construction cost for rows that are about to be
        # visible. Untouched rows continue to use their cheap placeholder.
        #
        # v1.10.151: when a single pagination change brings a large number
        # of un-populated rows into view at once (typically switching from
        # 100/200/page to "All" on a multi-thousand-segment project), wrap
        # the populate loop in the shared _ImportProgressDialog so the user
        # sees activity instead of a "Not Responding" freeze. Threshold
        # chosen so small page flips stay invisible.
        populated = getattr(self, '_populated_rows', None)
        if populated is not None:
            seg_count = len(segments)
            rows_to_populate = [r for r in rows_to_show
                                if r not in populated and r < seg_count]

            def _do_populate(progress_callback=None):
                old_suppress = getattr(self, '_suppress_target_change_handlers', False)
                self._suppress_target_change_handlers = True
                try:
                    for i, row in enumerate(rows_to_populate):
                        self._populate_single_row(row, segments[row])
                        # The newly-installed target editor inherits the
                        # suppression flag we just set; unblock its signals
                        # so user edits will reach the change handler.
                        w = self.table.cellWidget(row, 3)
                        if w:
                            w.blockSignals(False)
                        # Every ~25 rows, give the progress dialog a chance
                        # to repaint and the event loop a chance to keep the
                        # OS from marking the window unresponsive.
                        if progress_callback is not None and i % 25 == 0:
                            progress_callback(i, len(rows_to_populate))
                finally:
                    self._suppress_target_change_handlers = old_suppress

            if len(rows_to_populate) >= 200:
                # Substantial batch — show a progress dialog. 200 was chosen
                # because that's where the per-row widget-construction cost
                # starts being perceptible (~5 s on a typical SDLXLIFF).
                with _ImportProgressDialog(
                    self, title="Loading more segments",
                    initial_label=(
                        f"Loading {len(rows_to_populate):,} additional "
                        "segments into grid…"
                    ),
                    initial_total=len(rows_to_populate),
                ) as _prog:
                    _do_populate(progress_callback=_prog.grid_callback)
            elif rows_to_populate:
                # Small batch — populate inline, no dialog flash.
                _do_populate()

        # Now hide/show in a single batched pass.
        self.table.setUpdatesEnabled(False)
        try:
            for row in range(total_segments):
                self.table.setRowHidden(row, row not in rows_to_show)
        finally:
            self.table.setUpdatesEnabled(True)

        # Recalculate heights for visible rows to prevent layout corruption.
        # Call synchronously first, then schedule a deferred resize to catch any
        # layout issues that Qt processes asynchronously.
        self._resize_visible_rows()
        QTimer.singleShot(50, self._resize_visible_rows)

        # Update pagination UI
        self._update_pagination_ui()

        # v1.10.152: kick off background prefetch of the NEXT page in idle
        # time so the next "Next page" click (or Ctrl+Enter on the last
        # segment of this page) is instantaneous instead of triggering a
        # 200-row populate-and-progress-dialog cycle.
        self._start_background_populate()

    def _start_background_populate(self):
        """Schedule the next page to be populated in idle time.

        Skips when there's nothing useful to prefetch: "All" mode (every
        row will be populated by the foreground path anyway), filter mode
        (pagination doesn't apply), already on the last page, or the next
        page already has its widgets installed.
        """
        # Always bump the generation, which cancels any in-flight prefetch
        # — this is what makes navigating-while-prefetching safe.
        self._background_populate_generation = getattr(
            self, '_background_populate_generation', 0
        ) + 1

        if not self.current_project or not self.current_project.segments:
            return

        populated = getattr(self, '_populated_rows', None)
        if populated is None:
            return

        # Filter mode shows results across the whole document, not paged.
        if getattr(self, '_active_text_filter_rows', None) is not None:
            return

        page_size = getattr(self, 'grid_page_size', 50)
        if (not isinstance(page_size, int)
                or page_size <= 0
                or page_size >= 999999):
            return

        current_page = getattr(self, 'grid_current_page', 0)
        total = len(self.current_project.segments)
        next_start = (current_page + 1) * page_size
        if next_start >= total:
            return  # already on the last page

        next_end = min(next_start + page_size, total)
        rows_to_prefetch = [r for r in range(next_start, next_end)
                            if r not in populated]
        if not rows_to_prefetch:
            return  # next page is already populated (e.g. backwards nav)

        self._background_populate_queue = rows_to_prefetch
        my_gen = self._background_populate_generation
        # Give the freshly-loaded current page a moment to settle and the
        # user a moment to start interacting before we steal main-thread
        # time for the prefetch.
        QTimer.singleShot(
            self.BACKGROUND_POPULATE_INITIAL_DELAY_MS,
            lambda gen=my_gen: self._background_populate_step(gen),
        )

    def _background_populate_step(self, generation: int):
        """Populate a small batch and reschedule until the queue is empty."""
        # Generation mismatch: a newer prefetch (or project change) has
        # superseded us. Drop our queue and exit silently.
        if generation != getattr(self, '_background_populate_generation', 0):
            return
        queue = getattr(self, '_background_populate_queue', None)
        if not queue:
            return
        if not self.current_project or not self.current_project.segments:
            return

        segments = self.current_project.segments
        seg_count = len(segments)
        populated = getattr(self, '_populated_rows', None)
        if populated is None:
            return

        batch = queue[:self.BACKGROUND_POPULATE_BATCH_SIZE]
        self._background_populate_queue = queue[self.BACKGROUND_POPULATE_BATCH_SIZE:]

        old_suppress = getattr(self, '_suppress_target_change_handlers', False)
        self._suppress_target_change_handlers = True
        try:
            for row in batch:
                # Re-check membership each iteration — a foreground populate
                # (e.g. user clicked Next while we were mid-batch) may have
                # already populated this row.
                if row in populated or row >= seg_count:
                    continue
                self._populate_single_row(row, segments[row])
                w = self.table.cellWidget(row, 3)
                if w:
                    w.blockSignals(False)
        finally:
            self._suppress_target_change_handlers = old_suppress

        # If there's more to do, hop to the event loop and come back.
        if self._background_populate_queue:
            QTimer.singleShot(
                self.BACKGROUND_POPULATE_BATCH_DELAY_MS,
                lambda gen=generation: self._background_populate_step(gen),
            )

    def go_to_first_page(self):
        """Navigate to first page"""
        if not hasattr(self, 'grid_current_page'):
            self.grid_current_page = 0
        if self.grid_current_page != 0:
            self.grid_current_page = 0
            self._apply_pagination_to_grid()

    def go_to_prev_page(self):
        """Navigate to previous page"""
        if not hasattr(self, 'grid_current_page'):
            self.grid_current_page = 0
        if self.grid_current_page > 0:
            self.grid_current_page -= 1
            self._apply_pagination_to_grid()

    def go_to_next_page(self):
        """Navigate to next page"""
        if not hasattr(self, 'grid_current_page'):
            self.grid_current_page = 0
        total_pages = self._get_total_pages()
        if self.grid_current_page < total_pages - 1:
            self.grid_current_page += 1
            self._apply_pagination_to_grid()

    def select_range_page_up(self):
        """Select a range of segments upward (Shift+Page Up).
        
        Extends selection from current row upward by one page worth of segments.
        If no selection anchor exists, starts from current row.
        """
        if not hasattr(self, 'table') or not self.table or not self.current_project:
            return
        
        current_row = self.table.currentRow()
        if current_row < 0:
            return
        
        # Calculate page size (number of segments to select). When "All" is
        # active (sentinel 999999) fall back to a screenful so Shift+PgUp
        # doesn't select to the top of the document.
        page_size = getattr(self, 'grid_page_size', 50)
        if not isinstance(page_size, int) or page_size >= 999999 or page_size <= 0:
            page_size = 50

        # Get or set the selection anchor (starting point for range selection)
        if not hasattr(self, '_selection_anchor_row'):
            self._selection_anchor_row = current_row

        # Calculate target row (one page up from current, but not below 0)
        target_row = max(0, current_row - page_size)
        
        # Select range from anchor to target
        self._select_range_between(self._selection_anchor_row, target_row)
        
        # Move focus to the target row
        self.table.setCurrentCell(target_row, 3)  # Column 3 is target cell
        self.table.scrollToItem(self.table.item(target_row, 0))

    def select_range_page_down(self):
        """Select a range of segments downward (Shift+Page Down).
        
        Extends selection from current row downward by one page worth of segments.
        If no selection anchor exists, starts from current row.
        """
        if not hasattr(self, 'table') or not self.table or not self.current_project:
            return
        
        current_row = self.table.currentRow()
        if current_row < 0:
            return
        
        # Calculate page size (number of segments to select). When "All" is
        # active (sentinel 999999) fall back to a screenful so Shift+PgDn
        # doesn't select to the bottom of the document.
        page_size = getattr(self, 'grid_page_size', 50)
        if not isinstance(page_size, int) or page_size >= 999999 or page_size <= 0:
            page_size = 50

        # Get or set the selection anchor (starting point for range selection)
        if not hasattr(self, '_selection_anchor_row'):
            self._selection_anchor_row = current_row

        # Calculate target row (one page down from current, but not beyond last segment)
        max_row = len(self.current_project.segments) - 1
        target_row = min(max_row, current_row + page_size)
        
        # Select range from anchor to target
        self._select_range_between(self._selection_anchor_row, target_row)
        
        # Move focus to the target row
        self.table.setCurrentCell(target_row, 3)  # Column 3 is target cell
        self.table.scrollToItem(self.table.item(target_row, 0))

    def _select_range_between(self, start_row: int, end_row: int):
        """Select all rows between start_row and end_row (inclusive).
        
        Args:
            start_row: Selection anchor row
            end_row: Target row to extend selection to
        """
        if not hasattr(self, 'table') or not self.table:
            return
        
        # Determine actual start and end (handle both directions)
        min_row = min(start_row, end_row)
        max_row = max(start_row, end_row)
        
        # Clear existing selection
        self.table.clearSelection()
        
        # Select the range using QTableWidgetSelectionRange
        col_count = self.table.columnCount()
        selection_range = QTableWidgetSelectionRange(min_row, 0, max_row, col_count - 1)
        self.table.setRangeSelected(selection_range, True)

    def _clear_selection_anchor(self):
        """Clear the selection anchor when a non-shift click occurs."""
        if hasattr(self, '_selection_anchor_row'):
            del self._selection_anchor_row

    def go_to_last_page(self):
        """Navigate to last page"""
        if not hasattr(self, 'grid_current_page'):
            self.grid_current_page = 0
        total_pages = self._get_total_pages()
        if self.grid_current_page != total_pages - 1:
            self.grid_current_page = total_pages - 1
            self._apply_pagination_to_grid()

    def go_to_page(self):
        """Navigate to specific page from input field"""
        if not hasattr(self, 'page_number_input') or not self._widget_is_alive(self.page_number_input):
            return
        try:
            page_num = int(self.page_number_input.text())
            total_pages = self._get_total_pages()
            if not hasattr(self, 'grid_current_page'):
                self.grid_current_page = 0
            new_page = max(0, min(page_num - 1, total_pages - 1))  # Convert to 0-indexed and clamp
            if new_page != self.grid_current_page:
                self.grid_current_page = new_page
                self._apply_pagination_to_grid()
            else:
                # Even if same page, update the input to show clamped value
                self.page_number_input.setText(str(self.grid_current_page + 1))
        except ValueError:
            # Invalid input, reset to current page
            if hasattr(self, 'grid_current_page'):
                self.page_number_input.setText(str(self.grid_current_page + 1))

    def on_page_size_changed(self, text: str):
        """Handle page size change"""
        if not hasattr(self, 'grid_page_size'):
            self.grid_page_size = 50
        
        old_page_size = self.grid_page_size
        
        if text == "All":
            self.grid_page_size = 999999
        else:
            try:
                self.grid_page_size = int(text)
            except ValueError:
                self.grid_page_size = 50
        
        # Reset to first page when page size changes
        if not hasattr(self, 'grid_current_page'):
            self.grid_current_page = 0
        else:
            self.grid_current_page = 0
        
        # Apply the new pagination
        self._apply_pagination_to_grid()

    def create_translation_grid(self):
        """Create the translation grid (QTableWidget)"""
        self.table = QTableWidget()
        set_help_topic(self.table, HelpTopics.TRANSLATION_GRID)

        # Configure columns
        self.table.setColumnCount(5)
        self.table.setHorizontalHeaderLabels(["#", "Type", "Source", "Target", "Status"])

        # Explicitly set header font to normal weight (not bold)
        header = self.table.horizontalHeader()
        header_font = QFont(self.default_font_family, self.default_font_size, QFont.Weight.Normal)
        header.setFont(header_font)

        # Also set font on individual header items through the model (extra insurance)
        model = self.table.model()
        if model:
            for col in range(5):
                item = model.headerData(col, Qt.Orientation.Horizontal, Qt.ItemDataRole.DisplayRole)
                model.setHeaderData(col, Qt.Orientation.Horizontal, item, Qt.ItemDataRole.DisplayRole)
                model.setHeaderData(col, Qt.Orientation.Horizontal, header_font, Qt.ItemDataRole.FontRole)

        # Column widths - Source and Target columns stretch to fill space, others are interactive
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)  # ID
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)  # Type
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)  # Source - stretch to fill space
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)  # Target - stretch to fill space
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Interactive)  # Status - allow resizing
        header.setStretchLastSection(False)  # Don't auto-stretch last section (we use Stretch mode for Source/Target)

        # Optional: show Status column before Target
        # moveSection reorders the display without changing logical indices,
        # so all code referencing column 3 (Target) and 4 (Status) still works.
        settings = self.load_general_settings()
        self.status_column_before_target = settings.get('status_column_before_target', True)
        if self.status_column_before_target:
            header.moveSection(4, 3)  # Move Status (logical 4) to visual position 3

        # Recalculate row heights when columns are resized (text reflows to new width)
        header.sectionResized.connect(self._on_column_resized)

        # Set initial column widths - give Source and Target equal space
        # ID column width will be auto-adjusted by _update_segment_column_width() after segments load
        self.table.setColumnWidth(0, 35)   # ID - temporary, auto-adjusts to fit content
        self.table.setColumnWidth(1, 40)   # Type - narrower
        self.table.setColumnWidth(2, 400)  # Source
        self.table.setColumnWidth(3, 400)  # Target
        self.table.setColumnWidth(4, 75)   # Status - wide enough for icon + match %
        
        # Enable word wrap in cells (both display and edit mode)
        self.table.setWordWrap(True)
        
        # Apply custom delegate for word wrap in edit mode
        # Pass None for assistance_panel (keyboard shortcuts disabled for now)
        # Pass allow_replace_in_source setting to control source editing
        self.table.setItemDelegate(WordWrapDelegate(None, self.table, self.allow_replace_in_source))
        
        # Row behavior - Enable multi-selection with full row selection (memoQ-style)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)  # Select full rows for multi-selection
        self.table.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)  # Allow Ctrl/Shift multi-selection

        # Scroll by pixels, not by whole rows. Qt's default jumps a row at a
        # time, so the wheel moved in uneven lurches (a notch was three rows,
        # whether those were one line or twenty) and a segment taller than the
        # window could never be scrolled through. One wheel notch is three
        # steps: 120 px, about four one-line rows.
        self.table.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.table.verticalScrollBar().setSingleStep(40)
        
        # Alternating row colors (simplified view)
        self.table.setAlternatingRowColors(True)

        # Simplified grid styling - subtle borders and padding only
        # Note: Colors are controlled by theme_manager.py for proper dark mode support
        self.table.setStyleSheet("""
            QTableWidget::item {
                padding: 4px;
                border: none;
                border-bottom: 1px solid rgba(128, 128, 128, 0.15);
                border-right: 1px solid rgba(128, 128, 128, 0.15);
            }
            QTableWidget::item:last-child {
                border-right: none;
            }
            /* Trados-style strong blue for the active row so the user can see
               at a glance which segment they're working on. The theme's
               default highlight is too pale (#e3f2fd ≈ near-white) for the
               editor grid, where the active segment needs to stand out from
               the alternating-row tint. */
            QTableWidget::item:selected {
                background-color: #CFE5F5;
                color: #000000;
            }
            QTableWidget::item:selected:!active {
                background-color: #DEE9F2;
                color: #000000;
            }

            /* Narrower scrollbar with visible arrow buttons */
            QScrollBar:vertical {
                border: none;
                background: #F0F0F0;
                width: 12px;
                margin: 12px 0 12px 0;
            }
            QScrollBar::handle:vertical {
                background: #C0C0C0;
                min-height: 20px;
                border-radius: 2px;
            }
            QScrollBar::handle:vertical:hover {
                background: #A0A0A0;
            }
            QScrollBar::add-line:vertical {
                height: 12px;
                background: #E0E0E0;
                subcontrol-position: bottom;
                subcontrol-origin: margin;
            }
            QScrollBar::add-line:vertical:hover {
                background: #2196F3;
            }
            QScrollBar::sub-line:vertical {
                height: 12px;
                background: #E0E0E0;
                subcontrol-position: top;
                subcontrol-origin: margin;
            }
            QScrollBar::sub-line:vertical:hover {
                background: #2196F3;
            }
            /* Arrow images */
            QScrollBar::up-arrow:vertical {
                image: url(assets/scrollbar_up.png);
                width: 8px;
                height: 8px;
            }
            QScrollBar::down-arrow:vertical {
                image: url(assets/scrollbar_down.png);
                width: 8px;
                height: 8px;
            }
        """)
        
        # Simplified editing: Double-click only (no F2 key) - companion tool philosophy
        # Grid is primarily for viewing/reviewing, with minor edits allowed
        self.table.setEditTriggers(QTableWidget.EditTrigger.DoubleClicked)
        
        # Enable context menu for bulk operations
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self.show_grid_context_menu)
        
        # Connect signals for debugging
        self.table.itemChanged.connect(self.on_cell_changed)
        self.table.currentCellChanged.connect(self.on_cell_selected)
        self.table.currentCellChanged.connect(self._update_row_selection_tint)
        self.table.itemClicked.connect(self.on_cell_clicked)
        self.table.cellDoubleClicked.connect(self.on_cell_double_clicked)

        # Add additional selection signal for row-based selection mode
        self.table.itemSelectionChanged.connect(self.on_selection_changed)

        # Reposition file boundary banners when the user scrolls or resizes columns
        self.table.verticalScrollBar().valueChanged.connect(self._update_file_boundary_labels)
        self.table.horizontalHeader().sectionResized.connect(lambda *_: self._update_file_boundary_labels())

        # Debug: Confirm signal connections
        self.log("🔌 Table signals connected: currentCellChanged, itemClicked, cellDoubleClicked, itemSelectionChanged")

    def add_precision_scroll_buttons(self):
        """Add precision scroll buttons at top/bottom of scrollbar (memoQ-style)"""
        from PyQt6.QtWidgets import QPushButton
        from PyQt6.QtCore import Qt, QTimer
        
        # Get the vertical scrollbar
        scrollbar = self.table.verticalScrollBar()
        
        # Create scroll up button (appears at top)
        self.scroll_up_btn = QPushButton("▲", self.table)
        self.scroll_up_btn.setFixedSize(18, 22)
        self.scroll_up_btn.setStyleSheet("""
            QPushButton {
                background-color: rgba(240, 240, 240, 220);
                border: 1px solid #ccc;
                border-radius: 3px;
                font-size: 9pt;
                font-weight: bold;
                padding: 0px;
            }
            QPushButton:hover {
                background-color: #2196F3;
                color: white;
                border: 1px solid #1976D2;
            }
            QPushButton:pressed {
                background-color: #1565C0;
            }
            QPushButton:focus {
                outline: none;
            }
        """)
        self.scroll_up_btn.setToolTip(self.tr("Scroll up one row (Precision scroll)"))
        self.scroll_up_btn.clicked.connect(lambda: self.precision_scroll(-1))
        self.scroll_up_btn.raise_()  # Bring to front
        self.scroll_up_btn.show()  # Start visible for testing
        
        # Create scroll down button (appears at bottom)
        self.scroll_down_btn = QPushButton("▼", self.table)
        self.scroll_down_btn.setFixedSize(18, 22)
        self.scroll_down_btn.setStyleSheet("""
            QPushButton {
                background-color: rgba(240, 240, 240, 220);
                border: 1px solid #ccc;
                border-radius: 3px;
                font-size: 9pt;
                font-weight: bold;
                padding: 0px;
            }
            QPushButton:hover {
                background-color: #2196F3;
                color: white;
                border: 1px solid #1976D2;
            }
            QPushButton:pressed {
                background-color: #1565C0;
            }
            QPushButton:focus {
                outline: none;
            }
        """)
        self.scroll_down_btn.setToolTip(self.tr("Scroll down one row (Precision scroll)"))
        self.scroll_down_btn.clicked.connect(lambda: self.precision_scroll(1))
        self.scroll_down_btn.raise_()  # Bring to front
        self.scroll_down_btn.show()  # Start visible for testing
        
        # Position buttons initially
        QTimer.singleShot(100, self.position_precision_scroll_buttons)
        
        # Update position when table is resized
        self.table.resizeEvent = self._table_resize_event_wrapper(self.table.resizeEvent)

    def _table_resize_event_wrapper(self, original_resize_event):
        """Wrapper for table resize event to reposition scroll buttons"""
        def wrapped_resize_event(event):
            result = original_resize_event(event)
            self.position_precision_scroll_buttons()
            return result
        return wrapped_resize_event

    def precision_scroll(self, direction):
        """Scroll table by a small amount for fine-tuned navigation
        Args:
            direction: -1 for up, 1 for down
        """
        scrollbar = self.table.verticalScrollBar()
        current = scrollbar.value()
        
        # Use fixed pixel amounts based on divisor (inversely related)
        # Higher divisor = smaller scroll amount = finer control
        divisor = getattr(self, 'precision_scroll_divisor', 3)
        
        # Map divisor (1-10) to scroll amount (50-5 pixels)
        # divisor 1 = 50px, divisor 3 = 30px, divisor 5 = 20px, divisor 10 = 5px
        scroll_amount = max(5, 55 - (divisor * 5))
        
        new_value = current + (direction * scroll_amount)
        scrollbar.setValue(new_value)

    def position_precision_scroll_buttons(self):
        """Position precision scroll buttons at top/bottom of scrollbar"""
        if not hasattr(self, 'scroll_up_btn') or not hasattr(self, 'scroll_down_btn'):
            return
        
        scrollbar = self.table.verticalScrollBar()
        
        # Always position buttons, even if scrollbar not visible yet
        table_width = self.table.width()
        table_height = self.table.height()
        
        # Position buttons to the left of scrollbar (scrollbar is ~17px wide)
        scrollbar_width = scrollbar.width() if scrollbar.isVisible() else 17
        x_pos = table_width - scrollbar_width - 24  # 24 = button width + small margin
        
        # Up button at top (below header)
        header_height = self.table.horizontalHeader().height()
        self.scroll_up_btn.move(x_pos, header_height + 5)
        
        # Down button at bottom (above status bar area)
        self.scroll_down_btn.move(x_pos, table_height - 30)
        
        # Show/hide based on scrollbar visibility
        if scrollbar.isVisible():
            self.scroll_up_btn.show()
            self.scroll_down_btn.show()
        else:
            self.scroll_up_btn.hide()
            self.scroll_down_btn.hide()

    def _compute_initial_visible_rows(self, total: int) -> set:
        """Return the set of row indices that should get full editor widgets
        on initial grid load (current page only).

        Part of the page-aware grid loading optimisation in v1.10.147 (based
        on Hans Lenting's Simpelvertaler fork). Off-page rows get a cheap
        QTableWidgetItem placeholder and are populated lazily by the
        pagination handler when they become visible.
        """
        filter_allowlist = getattr(self, '_active_text_filter_rows', None)
        if filter_allowlist is not None:
            return set(filter_allowlist)
        page_size = getattr(self, 'grid_page_size', 50)
        if not isinstance(page_size, int) or page_size <= 0 or page_size >= 999999:
            # "All" mode (or unset) — populate every row.
            return set(range(total))
        current_page = getattr(self, 'grid_current_page', 0)
        start_row = current_page * page_size
        end_row = min(start_row + page_size, total)
        return set(range(start_row, end_row))

    def _populate_single_row(self, row, segment):
        """Install full source/target editor widgets and metadata for one row
        (see _populate_single_row_widgets); each editor's stylesheet is applied
        once, when the row is complete."""
        with _GridStyleBatch():
            self._populate_single_row_widgets(row, segment)

    def _populate_single_row_widgets(self, row, segment):
        """Install full source/target editor widgets and metadata for one row.

        Extracted from the per-row body of load_segments_to_grid as part of
        the page-aware grid loading optimisation in v1.10.147 (based on
        Hans Lenting's Simpelvertaler fork). Called once per row at grid
        load for rows on the current page, and called lazily by the
        pagination handler when off-page rows become visible. Tracks which
        rows have widgets via self._populated_rows.
        """
        if not hasattr(self, '_populated_rows'):
            self._populated_rows = set()
        # The pre-pass in load_segments_to_grid stashes the computed list
        # numbers on self; use them here for both eager and lazy population.
        list_numbers = getattr(self, '_list_numbers', {})

        # Clear any previous cell widgets
        self.table.removeCellWidget(row, 2)  # Source
        self.table.removeCellWidget(row, 3)  # Target
        self.table.removeCellWidget(row, 4)  # Match
        self.table.removeCellWidget(row, 5)  # Status
        
        # ID - Segment number (black in light themes, theme text color in dark themes)
        id_item = QTableWidgetItem(str(segment.id))
        id_item.setFlags(id_item.flags() & ~Qt.ItemFlag.ItemIsEditable)  # Read-only
        id_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        # Black for light themes, theme text color for dark themes
        theme = self.theme_manager.current_theme
        segment_num_color = "black" if theme.name in ["Light (Default)", "Soft Gray", "Warm Cream", "Sepia", "High Contrast"] else theme.text
        id_item.setForeground(QColor(segment_num_color))
        id_item.setBackground(QColor())  # Default background from theme
        # Smaller font for segment numbers
        seg_num_font = QFont(self.default_font_family, max(9, self.default_font_size - 1))
        id_item.setFont(seg_num_font)
        self.table.setItem(row, 0, id_item)
        
        # Type - show segment type based on style and content
        # Determine type display from style attribute and segment type
        style = getattr(segment, 'style', 'Normal')
        
        # Check for list items - use stored list_type/list_number if available
        list_type = getattr(segment, 'list_type', '')
        list_number_stored = getattr(segment, 'list_number', None)
        
        # Use pre-calculated list number from our loop above
        list_number_calculated = list_numbers.get(row, None)
        
        # Fallback: detect from source text if list_type not set
        source_text = segment.source.strip()
        is_list_item = bool(list_type) or (
            source_text.startswith('<li-o>') or  # Tagged ordered list item
            source_text.startswith('<li-b>') or  # Tagged bullet list item
            source_text.startswith('<li>') or    # Legacy list tag
            source_text.lstrip().startswith(('• ', '- ', '* ', '· ')) or
            (len(source_text) > 2 and source_text[0].isdigit() and source_text[1:3] in ('. ', ') '))
        )
        
        # Okapi sub-document category (comment/header/footer/property/notes)
        # takes precedence over style-based labelling, so the user can see at
        # a glance which segments are not ordinary body text.
        category = getattr(segment, 'category', '') or ''
        _CATEGORY_LABELS = {
            'comment':  ('Cmt',  'Comment'),
            'header':   ('Hdr',  'Header'),
            'footer':   ('Ftr',  'Footer'),
            'property': ('Prop', 'Document property'),
            'notes':    ('Note', 'Speaker notes'),
        }

        # Determine type display
        if category in _CATEGORY_LABELS:
            type_display = _CATEGORY_LABELS[category][0]
        elif 'Title' in style:
            type_display = "Title"
        elif 'Heading 1' in style or 'Heading1' in style:
            type_display = "H1"
        elif 'Heading 2' in style or 'Heading2' in style:
            type_display = "H2"
        elif 'Heading 3' in style or 'Heading3' in style:
            type_display = "H3"
        elif 'Heading 4' in style or 'Heading4' in style:
            type_display = "H4"
        elif 'Subtitle' in style:
            type_display = "Sub"
        elif is_list_item:
            # Show list number for numbered lists, bullet for unordered
            # Check for <li-b> tag first (explicit bullet)
            if '<li-b>' in source_text:
                type_display = "•"
            # Check for bullet patterns in text
            elif source_text.lstrip().startswith(('• ', '- ', '* ', '· ')):
                type_display = "•"
            elif list_type == "bullet":
                type_display = "•"
            elif list_number_stored is not None:
                type_display = f"#{list_number_stored}"
            elif list_number_calculated is not None:
                type_display = f"#{list_number_calculated}"
            elif '<li-o>' in source_text or '<li>' in source_text:
                # Ordered list - use calculated number or show #?
                type_display = f"#{list_numbers.get(row, '?')}"
            elif list_type == "numbered":
                type_display = "#?"
            else:
                # Fallback: check for number at start of text
                import re
                num_match = re.match(r'^(\d+)[.)\s]', source_text)
                if num_match:
                    type_display = f"#{num_match.group(1)}"
                elif '<li-o>' in source_text:
                    # Has <li-o> but no number - numbered list
                    type_display = "#?"
                else:
                    type_display = "li"
        elif segment.type and segment.type != "para":
            # Handle # type specially - only show #N for actual numbered items
            if segment.type == "#":
                # Plain "#" means continuation text within a list, show as paragraph
                type_display = "¶"
            elif segment.type.startswith("#") and len(segment.type) > 1:
                # Has a number like "#1", "#2" - keep it
                type_display = segment.type
            else:
                type_display = segment.type.upper()
        else:
            type_display = "¶"  # Paragraph symbol
        
        type_item = QTableWidgetItem(type_display)
        type_item.setFlags(type_item.flags() & ~Qt.ItemFlag.ItemIsEditable)  # Read-only
        type_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

        # Color-code by type for better visibility
        if category in _CATEGORY_LABELS:
            # Non-body content: comments stand out in magenta; headers/
            # footers/properties/notes share a purple. Tooltip spells it out.
            type_item.setToolTip(_CATEGORY_LABELS[category][1])
            type_item.setForeground(
                QColor("#C2185B") if category == 'comment' else QColor("#7B1FA2"))
        elif type_display in ("H1", "H2", "H3", "H4", "Title"):
            type_item.setForeground(QColor("#1976D2"))  # Blue for headings (works in both themes)
        elif type_display.startswith("#") or type_display in ("•", "li"):
            type_item.setForeground(QColor("#388E3C"))  # Green for list items (works in both themes)

        # Slightly smaller font for type symbols (kept in sync with apply_font_to_grid).
        # The ¶ paragraph mark renders heavier than the text labels, so shave an
        # extra point off it specifically.
        _type_delta = 2 if type_display == "¶" else 1
        type_font = QFont(self.default_font_family, max(6, self.default_font_size - _type_delta))
        type_item.setFont(type_font)

        self.table.setItem(row, 1, type_item)
        
        # Source - Use read-only QTextEdit widget for easy text selection
        # Strip outer wrapping tags if setting is enabled (display only, data unchanged)
        source_for_display = segment.source
        stripped_source_tag = None
        if self.hide_outer_wrapping_tags:
            stripped, stripped_source_tag = strip_outer_wrapping_tags(segment.source)
            source_for_display = stripped
        # Apply compact tag shortening if in compact mode
        # Build tag_map from source so target uses same numbering
        _compact_tag_map = None
        if getattr(self, 'tag_view_mode', 'tags') == 'compact':
            _compact_tag_map = {}
            source_for_display = compact_tags(source_for_display, _compact_tag_map)
        # Apply invisible character replacements for display only
        source_display_text = self.apply_invisible_replacements(source_for_display)
        # v1.10.229: Honour the "Allow Replace in Source Text" setting at cell
        # creation time. The cell widget (this editor) pre-empts the table
        # delegate's createEditor, so the delegate's source-edit branch never
        # ran — that's why the warning banner appeared but the cell stayed
        # locked. Source remains read-only for locked segments regardless.
        _allow_src_edit = bool(getattr(self, 'allow_replace_in_source', False)) \
                          and not getattr(segment, 'locked', False)
        source_editor = ReadOnlyGridTextEditor(
            source_display_text, self.table, row, allow_edit=_allow_src_edit
        )
        # Stash the compact tag map + stripped wrapping tag on the source
        # editor so the textChanged handler can reverse the display-only
        # transforms before writing back to segment.source.
        source_editor._compact_tag_map = _compact_tag_map
        source_editor._stripped_outer_tag = stripped_source_tag
        # Track segment locked state on the widget so update_grid_delegate()
        # can refresh editability without re-deriving it from row indices
        # (which don't survive filters / sorts).
        source_editor._segment_locked = bool(getattr(segment, 'locked', False))

        # Initialize empty termbase matches (will be populated lazily on segment selection or by background worker)
        source_editor.termbase_matches = {}

        # Set font to match grid – both setFont (so editor knows its
        # logical font) AND apply_grid_font_size (so the size lives in
        # the widget's own stylesheet and wins over ThemeManager's
        # app-level QTextEdit rule at non-100% UI scale).
        font = QFont(self.default_font_family, self.default_font_size)
        source_editor.setFont(font)
        source_editor.apply_grid_font_size(self.default_font_size)

        # Set as cell widget (allows easy text selection)
        self.table.setCellWidget(row, 2, source_editor)

        # Also set a placeholder item for row height calculation
        source_item = QTableWidgetItem()
        source_item.setFlags(Qt.ItemFlag.NoItemFlags)  # No interaction
        self.table.setItem(row, 2, source_item)

        # v1.10.229: Wire up the source textChanged handler unconditionally;
        # it gates on self.allow_replace_in_source AND the editor's
        # allow_source_edit at fire time, so it's a no-op when the setting is
        # off. Connecting it always means toggling the setting at runtime via
        # set_source_editable() immediately picks up edits without rebuilding
        # the grid.
        source_editor.textChanged.connect(
            self._make_source_changed_handler(segment.id, source_editor)
        )

        # Target - Use editable QTextEdit widget for easy text selection and editing
        # Strip outer wrapping tags if enabled (will be auto-restored when saving)
        target_for_display = segment.target
        stripped_target_tag = None
        if self.hide_outer_wrapping_tags:
            stripped, stripped_target_tag = strip_outer_wrapping_tags(segment.target)
            target_for_display = stripped
            # Store stripped tag on segment for restore during save
            # Use source tag as reference (target should match source structure)
            segment._stripped_outer_tag = stripped_source_tag or stripped_target_tag

        # Apply compact tag shortening to target (display only – reversed before saving)
        if _compact_tag_map is not None:
            target_for_display = compact_tags(target_for_display, _compact_tag_map)
        # Apply invisible character replacements for display (will be reversed when saving)
        target_display_text = self.apply_invisible_replacements(target_for_display)
        target_editor = EditableGridTextEditor(target_display_text, self.table, row, self.table)
        target_editor.setFont(font)
        target_editor.apply_grid_font_size(self.default_font_size)
        # Store stripped tag on editor for auto-restore
        target_editor._stripped_outer_tag = stripped_source_tag or stripped_target_tag
        # Store compact tag map for reverse-expanding on save
        target_editor._compact_tag_map = _compact_tag_map

        # Make locked segments read-only (included for context only)
        if getattr(segment, 'locked', False):
            target_editor.setReadOnly(True)

        # Connect text changes to update segment
        # Use a factory function to create a proper closure that captures the segment ID
        def make_target_changed_handler(segment_id, editor_widget):
            # Create debounce timer for expensive operations
            debounce_timer = None
            
            def on_target_text_changed():
                nonlocal debounce_timer
                # In WYSIWYG view the cell shows rendered bold/italic/etc., so
                # toPlainText() would silently drop those tags. Rebuild them
                # from the cell's actual formatting instead; fall back to plain
                # text on any error so an edit is never lost. Tags/Compact view
                # already show the literal tags, so they stay on toPlainText().
                if getattr(self, 'tag_view_mode', 'tags') == 'wysiwyg':
                    try:
                        new_text = self._wysiwyg_document_to_tagged_text(editor_widget)
                    except Exception as _e:
                        new_text = editor_widget.toPlainText()
                        if getattr(self, 'debug_mode_enabled', False):
                            self.log(f"⚠ WYSIWYG tag rebuild failed, kept plain text: {_e}")
                else:
                    new_text = editor_widget.toPlainText()

                # Reverse compact tag placeholders before saving
                compact_map = getattr(editor_widget, '_compact_tag_map', None)
                if compact_map:
                    new_text = expand_compact_tags(new_text, compact_map)

                # Reverse invisible character replacements before saving
                new_text = self.reverse_invisible_replacements(new_text)

                # Auto-restore stripped outer wrapping tags if they were hidden
                stripped_tag = getattr(editor_widget, '_stripped_outer_tag', None)
                if stripped_tag and self.hide_outer_wrapping_tags:
                    # Only re-add if the text doesn't already have the tag
                    if not new_text.strip().startswith(f'<{stripped_tag}'):
                        new_text = f'<{stripped_tag}>{new_text}</{stripped_tag}>'

                # DEBUG: Log EVERY call to catch the culprit (only in debug mode)
                if self.debug_mode_enabled:
                    self.log(f"🔔 textChanged FIRED: segment_id={segment_id}, new_text='{new_text[:20] if new_text else 'EMPTY'}...'")

                # Mark that we've seen the first textChanged on this editor, but
                # do NOT swallow it. We used to return here unconditionally to
                # drop Qt's spurious queued event after a grid-load setPlainText
                # — but that also ate a *genuine* first edit that arrives as a
                # single event, most visibly **clearing** a cell: the empty
                # value never reached segment.target, so the old translation
                # survived save + reload (and kept showing in the Preview).
                # The idempotent guard below (new_text == old_target → return)
                # already absorbs the spurious event (it re-delivers unchanged
                # text), so a real change must be allowed through even when it's
                # the first one seen.
                editor_widget._initial_load_complete = True

                if self._suppress_target_change_handlers:
                    if self.debug_mode_enabled:
                        self.log(f"🔔 textChanged SUPPRESSED for segment {segment_id}")
                    return
                
                # CRITICAL: Find segment by ID, not by row index!
                # Row indices can change, but segment IDs are stable
                target_segment = next((seg for seg in self.current_project.segments if seg.id == segment_id), None)
                if not target_segment:
                    return
                
                # Capture old values for undo
                old_target = target_segment.target
                old_status = target_segment.status

                # Idempotent guard (v1.10.143): if the fully-cleaned text already
                # equals what's stored on the segment, this textChanged is a
                # spurious/programmatic event — Qt delivers queued document-change
                # events after our own blockSignals()+setPlainText() display
                # re-render (below) and at grid load — never a genuine user edit,
                # which always changes the text. Returning here keeps
                # segment.target reliably in sync on every real keystroke.
                # (Previously the re-render reset _initial_load_complete=False,
                # which could make the NEXT genuine edit be dropped, leaving a
                # stale segment.target → stale export + stale TM until a
                # re-confirm. That reset has been removed below.)
                if new_text == old_target:
                    return

                # Genuine user edit: from now on this cell keeps its own undo
                # history (so Ctrl+Z undoes typing); focusInEvent will no longer
                # clear it. See EditableGridTextEditor.focusInEvent.
                editor_widget._user_has_edited = True

                # Update the target text
                if self.debug_mode_enabled:
                    self.log(f"📝 BEFORE update: seg {segment_id} target='{target_segment.target[:30] if target_segment.target else 'EMPTY'}...', status={target_segment.status}, obj_id={id(target_segment)}")
                target_segment.target = new_text

                # Live-update the Document Preview (debounced, no-op when the
                # Preview tab is hidden) so the rendered text tracks edits and
                # flips source→translation as you type / clears back to source.
                self._schedule_preview_refresh()

                # If invisible markers are active, re-apply them so the widget display
                # stays in sync (e.g. ↵ after Shift+Enter when Show Invisibles is on).
                # In compact mode, use compact text for display (not the expanded save text)
                if hasattr(self, 'invisible_display_settings') and any(self.invisible_display_settings.values()):
                    display_source = new_text
                    compact_map = getattr(editor_widget, '_compact_tag_map', None)
                    if compact_map:
                        display_source = compact_tags(display_source, compact_map)
                    display_text = self.apply_invisible_replacements(display_source)
                    if display_text != editor_widget.toPlainText():
                        # Cursor position in the clean (marker-free) new_text.
                        # The widget currently shows the old display text; we need to
                        # map the cursor to its position in new_text first.
                        cur = editor_widget.textCursor()
                        old_display_pos = cur.position()
                        old_display = editor_widget.toPlainText()
                        # Convert old_display_pos → clean-text position by stripping
                        # markers that appear before cursor in the OLD display text.
                        old_before_cursor = old_display[:old_display_pos]
                        clean_pos = len(self.reverse_invisible_replacements(old_before_cursor))
                        # Now map clean_pos → new display_text position.
                        # apply_invisible_replacements inserts markers before each
                        # invisible char; count them in the display text up to clean_pos.
                        new_display_before = self.apply_invisible_replacements(display_source[:clean_pos])
                        new_pos = min(len(new_display_before), len(display_text))
                        editor_widget.blockSignals(True)
                        editor_widget.setPlainText(display_text)
                        cur.setPosition(new_pos)
                        editor_widget.setTextCursor(cur)
                        # v1.10.143: do NOT reset _initial_load_complete here.
                        # The queued spurious textChanged this setPlainText may
                        # emit is now harmlessly absorbed by the idempotent guard
                        # above (new_text == segment.target), so we no longer risk
                        # swallowing the user's next real keystroke.
                        editor_widget.blockSignals(False)

                # Reset 'confirmed' status to 'draft' when user edits the segment
                # This prevents auto-saving to TM until user re-confirms the edit
                new_status = old_status
                if old_status == 'confirmed' and new_text != old_target:
                    from PyQt6.QtCore import QTimer as QTimerLocal
                    new_status = 'draft'
                    target_segment.status = new_status
                    if self.debug_mode_enabled:
                        self.log(f"📝 Status reset: confirmed → draft (segment edited)")
                    # Refresh the status icon in the grid (debounced to avoid UI lag)
                    QTimerLocal.singleShot(0, lambda sid=segment_id: self._refresh_segment_status_by_id(sid))
                
                if self.debug_mode_enabled:
                    self.log(f"📝 AFTER update: seg {segment_id} target='{target_segment.target[:30] if target_segment.target else 'EMPTY'}...', status={target_segment.status}, obj_id={id(target_segment)}")
                
                # Record undo state with any status change
                self.record_undo_state(segment_id, old_target, new_text, old_status, new_status)
                
                # Mark project as modified
                self.project_modified = True
                
                # DEBOUNCED: Expensive UI/DB operations (only after user stops typing)
                # Cancel previous timer
                if debounce_timer:
                    debounce_timer.stop()
                
                # Schedule expensive operations after 500ms of inactivity
                from PyQt6.QtCore import QTimer
                debounce_timer = QTimer()
                debounce_timer.setSingleShot(True)
                # CRITICAL: Use default parameter to capture new_text BY VALUE, not by reference
                # This prevents the closure from capturing a variable that changes later
                debounce_timer.timeout.connect(lambda text=new_text: self._handle_target_text_debounced_by_id(
                    segment_id, text
                ))
                debounce_timer.start(1000)  # 1000ms delay (increased for better responsiveness)
                    
            return on_target_text_changed
        
        # Set as cell widget FIRST (before connecting signals)
        self.table.setCellWidget(row, 3, target_editor)
        
        # Also set a placeholder item for row height calculation
        target_item = QTableWidgetItem()
        target_item.setFlags(Qt.ItemFlag.NoItemFlags)  # No interaction
        self.table.setItem(row, 3, target_item)
        
        # CRITICAL: Connect textChanged AFTER widget is in table and setText is done
        # This prevents programmatic setText from triggering TM saves during grid reload
        target_editor.textChanged.connect(make_target_changed_handler(segment.id, target_editor))
        
        # DO NOT unblock signals here - they stay blocked until end of load_segments_to_grid

        # Pre-populate status cell item so gridlines render before widget assignment
        status_placeholder = QTableWidgetItem()
        status_placeholder.setFlags(Qt.ItemFlag.NoItemFlags)
        status_placeholder.setBackground(QColor(get_status(segment.status).color))
        self.table.setItem(row, 4, status_placeholder)

        # Status column (icon + match + comment)
        self._update_status_cell(row, segment)
        
        # Apply alternating row colors to source and target widgets
        self._apply_row_color(row, source_editor, target_editor)

        # File boundary separator for multi-file projects
        if getattr(self.current_project, 'is_multifile', False) and row > 0:
            prev_file_id = getattr(self.current_project.segments[row - 1], 'file_id', None)
            curr_file_id = getattr(segment, 'file_id', None)
            if curr_file_id is not None and prev_file_id is not None and curr_file_id != prev_file_id:
                source_editor.set_file_boundary(True)
                target_editor.set_file_boundary(True)
                # Re-apply row color so the border-top takes effect in the stylesheet
                self._apply_row_color(row, source_editor, target_editor)

        self._populated_rows.add(row)

    def load_segments_to_grid(self, progress_callback=None):
        """Load segments into the grid with termbase highlighting.

        progress_callback: optional callable(rows_done, total_rows). Called every
        ~25 rows during the build loop so callers can update a progress dialog
        and pump the Qt event loop, keeping the UI responsive on large imports.
        """
        self.log(f"🔄🔄🔄 load_segments_to_grid CALLED - this will RELOAD grid from segment data!")

        # Ensure original segment order is stored (for Document Order sort)
        if self.current_project and not hasattr(self, '_original_segment_order'):
            self._original_segment_order = self.current_project.segments.copy()

        # Clear row color settings cache to ensure fresh settings are loaded
        if hasattr(self, '_row_color_settings_cached'):
            delattr(self, '_row_color_settings_cached')
        
        # DEBUG: Log segment data BEFORE loading to grid
        if self.current_project and self.current_project.segments:
            self.log(f"🔄 BEFORE LOAD: First 7 segments:")
            for seg in self.current_project.segments[:7]:
                self.log(f"🔄   Seg {seg.id} (obj {id(seg)}): target='{seg.target[:30] if seg.target else 'EMPTY'}...', len={len(seg.target)}, status={seg.status}")
        
        if not self.current_project or not self.current_project.segments:
            self.clear_grid()
            return

        # Decide the page size for a newly-opened project (All, or paged for
        # very large docs). Runs once per project; no-op on re-renders.
        self._maybe_auto_set_page_size()

        self.table.setRowCount(len(self.current_project.segments))

        previous_suppression = self._suppress_target_change_handlers
        self._suppress_target_change_handlers = True
        
        # Pre-calculate list numbers for numbered lists
        # Track consecutive <li-o> or <li> items and assign numbers
        list_counter = 0
        last_was_list = False
        list_numbers = {}  # {segment_index: list_number}
        
        for idx, segment in enumerate(self.current_project.segments):
            source_text = segment.source.strip()
            # Support both new tags (<li-o>, <li-b>) and legacy <li> tag
            is_list_item = source_text.startswith('<li-o>') or source_text.startswith('<li-b>') or source_text.startswith('<li>')
            # <li-o> is ordered, <li> (legacy) is treated as ordered for numbering
            is_ordered = source_text.startswith('<li-o>') or (source_text.startswith('<li>') and not source_text.startswith('<li-b>'))
            
            # Check if it's a numbered list item (vs bullet)
            # If text inside <li-o> or <li> starts with a number pattern, extract it
            # Otherwise, if it's part of a consecutive list sequence, count it
            if is_list_item and is_ordered:
                import re
                # Check for number at start: <li-o>1. or <li>1. or <li-o>2) etc
                num_match = re.match(r'^<li(?:-o)?>\s*(\d+)[.)\s]', source_text)
                if num_match:
                    # Has explicit number in text
                    list_numbers[idx] = int(num_match.group(1))
                    list_counter = int(num_match.group(1))
                    last_was_list = True
                elif last_was_list:
                    # Continue numbering from previous
                    list_counter += 1
                    list_numbers[idx] = list_counter
                else:
                    # First item, start at 1
                    list_counter = 1
                    list_numbers[idx] = list_counter
                    last_was_list = True
            else:
                # Not a list item, reset counter
                last_was_list = False
                list_counter = 0

        try:
            _total_rows = len(self.current_project.segments)
            # v1.10.147: stash list_numbers on self so the lazy
            # populate path (pagination) can read them, and decide which
            # rows get full widgets right now vs cheap placeholders.
            self._list_numbers = list_numbers
            self._populated_rows = set()
            _initial_visible = self._compute_initial_visible_rows(_total_rows)

            for row, segment in enumerate(self.current_project.segments):
                # Yield to the Qt event loop every ~25 rows so the window
                # repaints and the OS doesn't mark it "Not Responding".
                if progress_callback is not None and row % 25 == 0:
                    try:
                        progress_callback(row, _total_rows)
                    except Exception:
                        pass


                if row in _initial_visible:
                    # Eager populate: this row is on the current page.
                    self._populate_single_row(row, segment)
                else:
                    # Cheap placeholder for off-page rows; pagination
                    # will lazy-populate full widgets on navigation.
                    self.table.removeCellWidget(row, 2)
                    self.table.removeCellWidget(row, 3)
                    self.table.removeCellWidget(row, 4)
                    self.table.removeCellWidget(row, 5)
                    _id_item = QTableWidgetItem(str(segment.id))
                    _id_item.setFlags(_id_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                    _id_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                    self.table.setItem(row, 0, _id_item)
                    _status_ph = QTableWidgetItem()
                    _status_ph.setFlags(Qt.ItemFlag.NoItemFlags)
                    _status_ph.setBackground(QColor(get_status(segment.status).color))
                    self.table.setItem(row, 4, _status_ph)


            # v1.10.148: per-row fonts are already set by _populate_single_row
            # at construction time, so the per-row pass of apply_font_to_grid
            # is pure duplicated work here (~9 s on a 479-segment SDLXLIFF).
            # Pass skip_per_row=True to do only the table-wide font/header/
            # column-width tweaks. The full pass still runs when the user
            # changes the font from the menu.
            self.apply_font_to_grid(skip_per_row=True)

            # Auto-resize rows
            self.auto_resize_rows()
            self._enforce_status_row_heights()

            self.log(f"✓ Loaded {len(self.current_project.segments)} segments to grid")

            # Apply pagination - show only segments for current page
            self._apply_pagination_to_grid()

            # Reposition file boundary banners (pagination may have changed row visibility)
            self._update_file_boundary_labels()

            # Apply current tag view mode (WYSIWYG or Tags)
            if hasattr(self, 'show_tags') and self.show_tags:
                # If tags mode is enabled, refresh to show raw tags
                self._refresh_grid_display_mode()

            # Also refresh List view if it exists
            if hasattr(self, 'list_tree'):
                self.refresh_list_view()
        finally:
            self._suppress_target_change_handlers = previous_suppression
            
            # NOW unblock all target editor signals - grid loading is complete
            # This MUST happen after suppression flag is restored to avoid race conditions
            for row in range(self.table.rowCount()):
                target_widget = self.table.cellWidget(row, 3)
                if target_widget:
                    target_widget.blockSignals(False)

            # v1.10.57 Phase C: paint background highlight on any
            # anchored comment ranges. Done after all cells are
            # populated and signals are unblocked, so the highlight
            # applies to the final character formatting layer.
            try:
                self._apply_comment_anchors_to_all_cells()
            except Exception as e:
                self.log(f"⚠ Anchor highlights skipped: {e}")

            # Update progress stats in status bar
            self.update_progress_stats()

            # Refresh document preview
            self.refresh_preview()

            # Deferred row resize: columns use Stretch mode, so their final widths
            # are only known after Qt processes the layout. Schedule a resize for
            # after all pending events are processed.
            QTimer.singleShot(0, self._resize_visible_rows)

    def _comment_indicator_bg(self, has_sc: bool, has_pc: bool):
        """Return a Qt-stylesheet ``background:`` value for the Status-column
        comment indicator, or None when the segment has no comments. Amber for
        Segment Comments, purple for Proofreading Comments, and a hard 50/50
        amber|purple split when a segment has both (issue #234 pt.1)."""
        if has_sc and has_pc:
            return (
                "qlineargradient(x1:0, y1:0, x2:1, y2:0, "
                f"stop:0 {self.COMMENT_SC_BG}, stop:0.499 {self.COMMENT_SC_BG}, "
                f"stop:0.5 {self.COMMENT_PC_BG}, stop:1 {self.COMMENT_PC_BG})"
            )
        if has_sc:
            return self.COMMENT_SC_BG
        if has_pc:
            return self.COMMENT_PC_BG
        return None

    def _create_status_cell_widget(self, segment: Segment) -> QWidget:
        widget = QWidget()
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(2, 0, 2, 0)  # Zero vertical margins to prevent icon clipping on single-line rows
        layout.setSpacing(1)
        layout.setAlignment(Qt.AlignmentFlag.AlignVCenter)  # Center content vertically
        # Remove fixed minimum height - let it adapt to row height
        widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        # Note: Do NOT set WA_TransparentForMouseEvents - we want tooltips to work

        # Check if segment has notes or proofreading notes
        has_notes = bool(segment.notes and segment.notes.strip())
        has_proofreading = bool(getattr(segment, 'proofreading_notes', None))
        has_any_notes = has_notes or has_proofreading

        # Distinct Status-column backgrounds so Segment Comments (SC) and
        # Proofreading Comments (PC) are tellable apart at a glance (issue #234
        # pt.1): SC → amber, PC → purple, both → a hard amber|purple split.
        # `indicator_bg` is a Qt-stylesheet value usable with `background:`.
        indicator_bg = self._comment_indicator_bg(has_notes, has_proofreading)

        # Widget background always transparent
        widget.setStyleSheet("background: transparent;")
        if has_any_notes:
            tooltip_parts = []
            if has_notes:
                tooltip_parts.append(f"Segment comment: {segment.notes.strip()}")
            if has_proofreading:
                model_count = len(segment.proofreading_notes)
                tooltip_parts.append(f"Proofreading comment: {model_count} LLM result{'s' if model_count != 1 else ''}")
            widget.setToolTip("\n".join(tooltip_parts))

        # Comment access from the Status column (Trados-parity). Build a
        # tooltip from the segment's actual comments (anchored + segment-level)
        # and, when there are any, wire a right-click "Open comment(s)" menu so
        # segment-level comments — which have no highlighted range in the cells
        # — are reachable straight from the status indicator. The eventFilter
        # appends ``comment_tooltip`` to the hovered status glyph's tooltip.
        seg_comments = [c for c in (getattr(segment, 'comments', None) or [])
                        if c.text and c.text.strip()]
        if seg_comments:
            tip_lines = []
            for c in seg_comments[:4]:
                prefix = f"⚓{c.anchor_field} " if c.is_anchored else ""
                tip_lines.append(f"💬 {prefix}{c.text.strip()}")
            if len(seg_comments) > 4:
                tip_lines.append(f"…(+{len(seg_comments) - 4} more)")
            widget.setProperty("comment_tooltip", "\n".join(tip_lines))
            first_cid = seg_comments[0].id
            widget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            widget.customContextMenuRequested.connect(
                lambda pos, w=widget, cid=first_cid, n=len(seg_comments):
                    self._status_cell_comment_menu(w, pos, cid, n)
            )

        status_def = get_status(segment.status)

        # Status icons: ✔ (green text glyph) and ❌ (native emoji, ~16px on Windows)
        # Use CSS font-size for the checkmark to match the emoji size visually.
        # The ✔ is a text character so font-size controls it; emojis ignore font-size.
        # IMPORTANT: On single-line rows, vertical space is very tight (~20-24px).
        # The icon + container margins + QLabel internal padding must fit within that.
        is_locked = getattr(segment, 'locked', False)

        # Trados-style coloured text badge (PM / CM) in place of the emoji icon.
        # The badge already conveys the match level, so we don't repeat the 101/102%.
        if status_def.badge_text:
            badge = QLabel(status_def.badge_text)
            badge.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignCenter)
            badge.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
            badge.setStyleSheet(
                f"background-color:{status_def.badge_bg}; color:{status_def.badge_fg};"
                " border-radius:3px; padding:0px 4px; font-size:10px; font-weight:bold;"
            )
            badge.setProperty("status_tooltip", status_def.label)
            badge.installEventFilter(self)
            layout.addWidget(badge)
            if is_locked:
                lock_lbl = QLabel(self.tr('<span style="font-size:14px; line-height:1.25;">🔒</span>'))
                lock_lbl.setTextFormat(Qt.TextFormat.RichText)
                lock_lbl.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
                lock_lbl.setContentsMargins(0, 0, 0, 0)
                lock_lbl.setProperty("status_tooltip", "Locked")
                lock_lbl.installEventFilter(self)
                layout.addWidget(lock_lbl)
            if indicator_bg:
                widget.setStyleSheet(f"background: {indicator_bg};")
            layout.addStretch(1)
            return widget

        # Icon colour now comes from the status definition (icon_color) instead
        # of a hard-coded special case for "confirmed". Statuses whose glyph is
        # a colour emoji leave icon_color empty and draw in their own colours;
        # the two commonest statuses use monochrome Dingbats text glyphs
        # (✔ U+2714 / ✘ U+2718) precisely so they obey font-size and cannot be
        # clipped by the tight single-line row.
        icon_text = "🔒" if is_locked else status_def.icon
        icon_colour = "" if is_locked else (status_def.icon_color or "")
        colour_css = f"color:{icon_colour}; " if icon_colour else ""
        icon_html = f'<span style="{colour_css}font-size:14px; line-height:1.25;">{icon_text}</span>'

        status_label = QLabel(icon_html)
        status_label.setTextFormat(Qt.TextFormat.RichText)  # Enable HTML rendering
        status_label.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        status_label.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)  # Expand vertically to match row height
        status_label.setContentsMargins(0, 0, 0, 0)  # No internal padding to prevent clipping in tight rows
        # Store tooltip text as property - we'll show it manually to avoid black tooltip bug
        status_label.setProperty("status_tooltip", status_def.label)
        status_label.installEventFilter(self)

        # Colour-code the comment background: amber (SC) / purple (PC) / split
        # (both). See _comment_indicator_bg. (issue #234 pt.1)
        if indicator_bg:
            status_label.setStyleSheet(f"padding: 2px 4px; background: {indicator_bg}; border-radius: 3px;")
        # Note: No stylesheet for non-notes case to avoid interfering with HTML color
        layout.addWidget(status_label)

        # Show match percentage if available, otherwise show the status short_label
        if segment.match_percent is not None:
            match_text = f"{segment.match_percent}%"
            match_label = QLabel(match_text)
            match_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            match_label.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
            if segment.match_percent >= 101:
                match_label.setStyleSheet("color: #1b5e20; font-weight: bold; font-size: 11px; padding: 0px;")
                match_label.setProperty("status_tooltip", "Context match (101% or better)")
            elif segment.match_percent >= 100:
                match_label.setStyleSheet("color: #2e7d32; font-weight: bold; font-size: 11px; padding: 0px;")
                match_label.setProperty("status_tooltip", "Exact match (100%)")
            elif segment.match_percent >= 90:
                match_label.setStyleSheet("color: #1565C0; font-weight: bold; font-size: 11px; padding: 0px;")
                match_label.setProperty("status_tooltip", f"High fuzzy match {segment.match_percent}%")
            else:
                match_label.setStyleSheet("color: #0d47a1; font-size: 11px; padding: 0px;")
                match_label.setProperty("status_tooltip", f"Fuzzy match {segment.match_percent}%")
            match_label.installEventFilter(self)
            layout.addWidget(match_label)
        elif status_def.short_label:
            # No match %, show a short text abbreviation to the right of the icon (e.g. "CM", "MT", "Rep")
            short_lbl = QLabel(status_def.short_label)
            short_lbl.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
            short_lbl.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
            short_lbl.setStyleSheet("color: #444; font-size: 10px; font-weight: bold; padding: 0px;")
            short_lbl.setProperty("status_tooltip", status_def.label)
            short_lbl.installEventFilter(self)
            layout.addWidget(short_lbl)

        layout.addStretch(1)

        return widget

    def _status_cell_comment_menu(self, widget, pos, comment_id, count):
        """Right-click menu on the Status column for a segment that has comments.

        Lets segment-level comments (no highlighted range in the cells) be
        opened straight from the status indicator.
        """
        from PyQt6.QtWidgets import QMenu
        from PyQt6.QtGui import QAction
        menu = QMenu(widget)
        label = "💬 Open comment" if count <= 1 else f"💬 Open comments ({count})"
        act = QAction(label, menu)
        act.triggered.connect(lambda: self._open_comment_in_panel(comment_id))
        menu.addAction(act)
        menu.exec(widget.mapToGlobal(pos))

    def _get_custom_tooltip(self):
        """Get or create the custom tooltip label widget."""
        if not hasattr(self, '_custom_tooltip') or self._custom_tooltip is None:
            self._custom_tooltip = QLabel()
            self._custom_tooltip.setWindowFlags(
                Qt.WindowType.ToolTip | Qt.WindowType.FramelessWindowHint
            )
            self._custom_tooltip.setStyleSheet("""
                QLabel {
                    background-color: #f5f5f5;
                    color: #333333;
                    border: 1px solid #d0d0d0;
                    padding: 4px 8px;
                    font-size: 12px;
                }
            """)
            self._custom_tooltip.hide()
        return self._custom_tooltip

    def _update_status_cell(self, row: int, segment: Segment):
        status_widget = self._create_status_cell_widget(segment)
        self.table.setCellWidget(row, 4, status_widget)
        status_item = self.table.item(row, 4)
        if status_item is None:
            status_item = QTableWidgetItem()
            self.table.setItem(row, 4, status_item)
        status_item.setText("")
        status_item.setFlags(Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled)
        # Set background color on table item (widget is transparent)
        status_item.setBackground(QColor(get_status(segment.status).color))
        status_item.setData(Qt.ItemDataRole.DisplayRole, "")
        status_widget.updateGeometry()

    def _refresh_segment_status(self, segment: Segment):
        if not self.current_project:
            return
        updated_row = None
        for row, seg in enumerate(self.current_project.segments):
            if seg.id == segment.id:
                self._update_status_cell(row, seg)
                updated_row = row
                break

        # Update list view entry in place (if visible)
        if hasattr(self, 'list_tree') and self.list_tree:
            matches = self.list_tree.findItems(str(segment.id), Qt.MatchFlag.MatchExactly, 0)
            for item in matches:
                status_def = get_status(segment.status)
                match_text = f"{segment.match_percent}%" if segment.match_percent is not None else ""
                display = f"{status_def.icon}"
                if match_text:
                    display += f"  {match_text}"
                if segment.notes and segment.notes.strip():
                    display += "  💬"
                if getattr(segment, 'proofreading_notes', None):
                    display += "  ✅"
                item.setText(2, display)
                status_tooltip = status_def.label
                if segment.match_percent is not None:
                    status_tooltip += f" | {segment.match_percent}% match"
                if segment.notes and segment.notes.strip():
                    status_tooltip += f"\nSegment comment: {segment.notes.strip()}"
                if getattr(segment, 'proofreading_notes', None):
                    model_count = len(segment.proofreading_notes)
                    status_tooltip += f"\nProofreading comment: {model_count} LLM result{'s' if model_count != 1 else ''}"
                item.setToolTip(2, status_tooltip)
                item.setBackground(2, QColor(status_def.color))
                # Monochrome text-glyph icons carry their colour in the status
                # definition; without this the list view would draw the ✘/✔ in
                # plain black now that they are no longer colour emoji.
                if status_def.icon_color:
                    item.setForeground(2, QColor(status_def.icon_color))

        # Recalculate row height after status cell update to prevent layout corruption
        if updated_row is not None:
            self._auto_resize_single_row(updated_row)

    def _refresh_segment_status_by_id(self, segment_id: int):
        """Refresh the status display for a segment by its ID."""
        if not self.current_project:
            return
        segment = next((seg for seg in self.current_project.segments if seg.id == segment_id), None)
        if segment:
            self._refresh_segment_status(segment)

    def _enforce_status_row_heights(self):
        """No longer needed - status widgets now adapt to row height."""
        # This method is kept for backward compatibility but does nothing
        pass

    def clear_grid(self):
        """Clear all rows from grid"""
        self.table.setRowCount(0)

    def _update_file_boundary_labels(self):
        """Create/reposition floating filename banner labels at file boundaries in multi-file projects.

        Labels are parented to the table viewport so they scroll with the table automatically.
        Called after load_segments_to_grid(), auto_resize_rows(), and on scroll/resize.
        """
        # Clean up existing labels
        for lbl in getattr(self, '_file_boundary_labels', []):
            lbl.setParent(None)
            lbl.deleteLater()
        self._file_boundary_labels = []

        if not hasattr(self, 'table') or not self.table:
            return
        if not self.current_project or not getattr(self.current_project, 'is_multifile', False):
            return

        segments = self.current_project.segments
        viewport = self.table.viewport()
        viewport_width = viewport.width()

        # Only rows on screen get a banner: this runs on every scroll step, and
        # making a label for every file boundary in the project (300 files →
        # 300 labels per step) made scrolling and loading slow (issue #185).
        # The row just below the viewport is included, since its banner sits
        # above its top edge.
        first = self.table.rowAt(0)
        if first < 0:
            return
        last = self.table.rowAt(viewport.height() - 1)
        if last < 0:
            last = len(segments) - 1
        for row in range(max(first, 1), min(last + 2, len(segments))):
            segment = segments[row]
            prev_file_id = getattr(segments[row - 1], 'file_id', None)
            curr_file_id = getattr(segment, 'file_id', None)
            if curr_file_id is None or prev_file_id is None or curr_file_id == prev_file_id:
                continue
            if self.table.isRowHidden(row):
                continue  # Don't show banner when file is filtered out

            # Get the Y position of this row in the viewport
            rect = self.table.visualRect(self.table.model().index(row, 0))
            if rect.isNull() or rect.top() < 0:
                continue

            file_name = getattr(segment, 'file_name', '') or f"File {curr_file_id}"
            lbl = QLabel(f"  📄  {file_name}", viewport)
            lbl.setFixedHeight(20)
            lbl.setFixedWidth(viewport_width)
            lbl.move(0, rect.top() - 20)
            lbl.setStyleSheet(
                "QLabel {"
                "  background-color: #1a3a5c;"
                "  color: #e8f0fe;"
                "  font-size: 10px;"
                "  font-weight: bold;"
                "  padding-left: 8px;"
                "  border-bottom: 1px solid #2196F3;"
                "}"
            )
            lbl.show()
            lbl.raise_()
            self._file_boundary_labels.append(lbl)

    def auto_resize_rows(self):
        """Auto-resize all rows to fit content - Compact version"""
        if not hasattr(self, 'table') or not self.table:
            return

        # Reduce width slightly to account for padding and prevent text cut-off
        width_reduction = 8

        # Manually calculate and set row heights for compact display
        last_pump = time.monotonic()
        for row in range(self.table.rowCount()):
            # Keep UI responsive during large grid updates – a few times a
            # second is enough; pumping every 50 rows cost more than the
            # resizing itself on a 17,000-row project (issue #185).
            if time.monotonic() - last_pump > 0.1:
                QApplication.processEvents()
                last_pump = time.monotonic()
            self._auto_resize_single_row(row, width_reduction)

        self.log("✓ Auto-resized rows to fit content (compact)")
        self._enforce_status_row_heights()
        self._update_file_boundary_labels()

    def _auto_resize_single_row(self, row: int, width_reduction: int = 8):
        """Auto-resize a single row to fit its content. Called automatically after text changes."""
        if not hasattr(self, 'table') or not self.table:
            return
        if row < 0 or row >= self.table.rowCount():
            return

        # Minimum column width threshold - if columns are narrower than this,
        # layout isn't ready yet and we should skip resizing
        MIN_COL_WIDTH = 100

        max_height = 1

        # Check source cell (column 2)
        source_widget = self.table.cellWidget(row, 2)
        if source_widget and isinstance(source_widget, ReadOnlyGridTextEditor):
            doc = source_widget.document()
            if doc:
                col_width = self.table.columnWidth(2) - width_reduction
                if col_width >= MIN_COL_WIDTH:
                    doc.setTextWidth(col_width)
                    # Use documentLayout().documentSize() to force fresh calculation
                    layout = doc.documentLayout()
                    if layout:
                        size = layout.documentSize()
                        if size.isValid():
                            height = int(size.height())
                            max_height = max(max_height, height)

        # Check target cell (column 3)
        target_widget = self.table.cellWidget(row, 3)
        if target_widget and isinstance(target_widget, EditableGridTextEditor):
            doc = target_widget.document()
            if doc:
                col_width = self.table.columnWidth(3) - width_reduction
                if col_width >= MIN_COL_WIDTH:
                    doc.setTextWidth(col_width)
                    # Use documentLayout().documentSize() to force fresh calculation
                    layout = doc.documentLayout()
                    if layout:
                        size = layout.documentSize()
                        if size.isValid():
                            height = int(size.height())
                            max_height = max(max_height, height)
        
        # Set row height with minimal padding
        # Minimum 32px to accommodate status icons (16px) + match text + padding without any cutoff
        compact_height = max(max_height + 2, 32)

        # Add space for the file boundary banner (20px) at the BOTTOM of the last row of each file.
        # The banner floats above the NEXT row's top, so the space must come from this row's height.
        if (self.current_project and
                getattr(self.current_project, 'is_multifile', False)):
            segments = self.current_project.segments
            next_row = row + 1
            if next_row < len(segments):
                curr_file_id = getattr(segments[row], 'file_id', None)
                next_file_id = getattr(segments[next_row], 'file_id', None)
                if (curr_file_id is not None and next_file_id is not None
                        and curr_file_id != next_file_id):
                    compact_height += 20  # Banner sits in the extra space at the bottom of this row

        self.table.setRowHeight(row, compact_height)

    def _resize_visible_rows(self):
        """Resize only visible (non-hidden) rows for efficiency after pagination/filter changes."""
        if not hasattr(self, 'table') or not self.table:
            return
        row_count = self.table.rowCount()
        for row in range(row_count):
            if not self.table.isRowHidden(row):
                self._auto_resize_single_row(row)

    def _on_column_resized(self, logical_index: int, old_size: int, new_size: int):
        """Handle column resize - recalculate row heights for text reflow.

        Only reacts to Source (2) or Target (3) column changes since those
        contain wrapped text. Uses debouncing to avoid excessive recalculations
        during continuous dragging.
        """
        # Only care about Source or Target columns (they contain wrapped text)
        if logical_index not in (2, 3):
            return

        # Debounce: cancel previous timer and start a new one
        if hasattr(self, '_column_resize_timer') and self._column_resize_timer is not None:
            self._column_resize_timer.stop()

        self._column_resize_timer = QTimer()
        self._column_resize_timer.setSingleShot(True)
        self._column_resize_timer.timeout.connect(self._resize_visible_rows)
        self._column_resize_timer.start(150)  # 150ms debounce

    def apply_font_to_grid(self, skip_per_row: bool = False):
        """Apply selected font to all grid cells.

        Args:
            skip_per_row: When True, only the table-wide setup (table font,
                header font, segment-column width) runs; the per-row
                iteration that re-applies fonts to every QTextEdit and
                Type-column item is skipped. v1.10.148: used from
                load_segments_to_grid, where _populate_single_row has
                already set per-row fonts at construction time — re-doing
                that work added ~9 s on a 479-segment file.
        """
        font = QFont(self.default_font_family, self.default_font_size)

        # Re-setting an unchanged font still re-lays out every cell editor
        # (seconds on a big project, issue #185), so only set a real change.
        if self.table.font() != font:
            self.table.setFont(font)

        # Also update header font - same size as grid content, normal weight
        header_font = QFont(self.default_font_family, self.default_font_size, QFont.Weight.Normal)
        if self.table.horizontalHeader().font() != header_font:
            self.table.horizontalHeader().setFont(header_font)

        if skip_per_row:
            # Fast path for initial load: fonts on individual cells/widgets
            # were already set by _populate_single_row at construction time.
            self._update_segment_column_width()
            return

        # Type column (1) uses its own slightly smaller font – must match the
        # formula used at grid-creation time so zoom scales ¶ along with the text.
        # The ¶ paragraph mark gets an extra point shaved off (heavier glyph).
        type_font_para = QFont(self.default_font_family, max(6, self.default_font_size - 2))
        type_font_label = QFont(self.default_font_family, max(6, self.default_font_size - 1))

        # Update fonts in QTextEdit widgets (source and target columns)
        if hasattr(self, 'table') and self.table:
            for row in range(self.table.rowCount()):
                # Keep UI responsive during large grid updates
                if row % 50 == 0:
                    QApplication.processEvents()

                # Type column (1) - QTableWidgetItem with paragraph mark / heading label
                type_item = self.table.item(row, 1)
                if type_item is not None:
                    type_item.setFont(type_font_para if type_item.text() == "¶" else type_font_label)

                # Source column (2) - ReadOnlyGridTextEditor.
                # Use apply_grid_font_size (sets the size in the widget's own
                # stylesheet) – setFont alone loses to ThemeManager's app-level
                # `QTextEdit { font-size }` rule emitted at non-100% UI scale.
                source_widget = self.table.cellWidget(row, 2)
                if source_widget and isinstance(source_widget, ReadOnlyGridTextEditor):
                    source_widget.setFont(font)
                    source_widget.apply_grid_font_size(self.default_font_size)

                # Target column (3) - EditableGridTextEditor (same reason)
                target_widget = self.table.cellWidget(row, 3)
                if target_widget and isinstance(target_widget, EditableGridTextEditor):
                    target_widget.setFont(font)
                    target_widget.apply_grid_font_size(self.default_font_size)

        # Adjust segment number column width based on font size
        self._update_segment_column_width()

    def _update_segment_column_width(self):
        """Adjust segment number column width to fit the largest segment number.
        
        Uses font metrics to calculate exact width needed for the highest segment number.
        """
        if not hasattr(self, 'table') or not self.table:
            return
        
        # Get the highest segment number we need to display
        max_segment = self.table.rowCount()
        if max_segment == 0:
            max_segment = 1
        
        # Use font metrics to calculate exact width needed
        font = QFont(self.default_font_family, self.default_font_size)
        fm = QFontMetrics(font)
        
        # Measure the width of the largest number (as string)
        text_width = fm.horizontalAdvance(str(max_segment))
        
        # Add padding (8px on each side = 16px total)
        new_width = text_width + 16

        # Ensure minimum width for very small numbers
        new_width = max(30, new_width)

        self.table.setColumnWidth(0, new_width)

    def set_font_family(self, family_name: str):
        """Set font family from menu"""
        self.default_font_family = family_name
        self.apply_font_to_grid()
        self.auto_resize_rows()
        self.log(f"✓ Font changed to {family_name}")

    def increase_font_size(self):
        """Increase grid font size."""
        self.default_font_size = min(72, self.default_font_size + 1)
        self.apply_font_to_grid()
        self.auto_resize_rows()
        self.log(f"✓ Font size: {self.default_font_size}")
        # Save font size to preferences
        self.save_current_font_sizes()

    def decrease_font_size(self):
        """Decrease grid font size."""
        self.default_font_size = max(7, self.default_font_size - 1)
        self.apply_font_to_grid()
        self.auto_resize_rows()
        self.log(f"✓ Font size: {self.default_font_size}")
        # Save font size to preferences
        self.save_current_font_sizes()

    def refresh_grid_tag_colors(self):
        """Refresh tag highlight colors in all grid cells"""
        if not hasattr(self, 'table') or not self.table:
            return

        for row in range(self.table.rowCount()):
            # Keep UI responsive during large grid updates
            if row % 50 == 0:
                QApplication.processEvents()

            # Source column (2) - ReadOnlyGridTextEditor
            source_widget = self.table.cellWidget(row, 2)
            if source_widget and isinstance(source_widget, ReadOnlyGridTextEditor):
                if hasattr(source_widget, 'highlighter'):
                    source_widget.highlighter.set_tag_color(EditableGridTextEditor.tag_highlight_color)

            # Target column (3) - EditableGridTextEditor
            target_widget = self.table.cellWidget(row, 3)
            if target_widget and isinstance(target_widget, EditableGridTextEditor):
                if hasattr(target_widget, 'highlighter'):
                    target_widget.highlighter.set_tag_color(EditableGridTextEditor.tag_highlight_color)

    def _apply_row_color(self, row: int, source_widget, target_widget):
        """Apply alternating row color to source and target widgets for a specific row"""
        # Load settings (with caching to avoid repeated file reads)
        # Cache is keyed by theme name to refresh when theme changes
        theme = self.theme_manager.current_theme
        cache_key = f'_row_color_cache_{theme.name}'

        if not hasattr(self, cache_key):
            settings = self.load_general_settings()
            # Always use current theme colors for row alternation, not saved settings
            # This ensures rows are dark in dark themes and light in light themes
            cached_data = {
                'enabled': settings.get('enable_alternating_row_colors', True),
                'even_color': theme.base,  # Always use theme color
                'odd_color': theme.alternate_bg  # Always use theme color
            }
            setattr(self, cache_key, cached_data)
            # Also update the old cache name for backwards compatibility
            self._row_color_settings_cached = cached_data
        else:
            self._row_color_settings_cached = getattr(self, cache_key)
        
        settings = self._row_color_settings_cached
        
        if not settings['enabled']:
            # Use theme default colors when disabled - remove hardcoded backgrounds
            # The theme system will handle the background colors
            return
        
        # Check if this row is a locked segment (non-translatable, included for context)
        segment = None
        if (self.current_project and self.current_project.segments
                and 0 <= row < len(self.current_project.segments)):
            segment = self.current_project.segments[row]

        if segment and getattr(segment, 'locked', False):
            # Locked segments get a clearly distinct background across all themes
            is_dark = theme.name in ("Dark Blue", "Nord Dark")
            color = '#4a3a30' if is_dark else '#F0D9C8'
            row_color = QColor(color)
        else:
            # Normal: determine color based on row index (even/odd)
            color = settings['even_color'] if row % 2 == 0 else settings['odd_color']
            row_color = QColor(color)

        # Apply to ID column (column 0)
        id_item = self.table.item(row, 0)
        if id_item:
            id_item.setBackground(row_color)
        
        # Apply to Type column (column 1) - but preserve heading/list item colors
        type_item = self.table.item(row, 1)
        if type_item:
            type_text = type_item.text()
            # Only apply row color if it's not a special type (headings/list items have their own colors)
            if type_text not in ("H1", "H2", "H3", "H4", "Title", "li"):
                type_item.setBackground(row_color)
        
        # Apply to source widget
        if hasattr(source_widget, 'set_background_color'):
            source_widget.set_background_color(color)

        # Apply to target widget
        if hasattr(target_widget, 'set_background_color'):
            target_widget.set_background_color(color)

    def _update_row_selection_tint(self, current_row, current_col, previous_row, previous_col):
        """Tint every column of the active row with the selection blue.

        QSS `::item:selected` is meant to handle this for free, but Qt paints
        explicit ``item.setBackground()`` brushes on top of the QSS colour –
        and we set those brushes via _apply_row_color for the ID/Type cells.
        That's why without this method the source/target widgets show the
        selection blue but the segment-number column stays on its alternating
        colour, breaking the visual unity of the active row.

        We walk every column so any cell widget that knows how to be tinted
        (``set_background_color``) gets the same shade, and any plain
        QTableWidgetItem gets its background brush overridden. The previous
        row is reverted to its alternating colour via the existing helper.
        """
        if not hasattr(self, 'table') or self.table is None:
            return

        column_count = self.table.columnCount()
        tint = QColor(self.SELECTION_TINT)

        # Restore alternating colour on the row we left.
        if previous_row is not None and previous_row >= 0 and previous_row != current_row:
            try:
                src_w = self.table.cellWidget(previous_row, 2)
                tgt_w = self.table.cellWidget(previous_row, 3)
                if src_w is not None or tgt_w is not None:
                    # _apply_row_color handles cols 0/1 (items) + 2/3 (widgets).
                    self._apply_row_color(previous_row, src_w, tgt_w)
            except RuntimeError:
                # Widget may have been deleted (e.g., during grid reload).
                pass

        # Tint the new current row across every column.
        if current_row is not None and current_row >= 0:
            try:
                for col in range(column_count):
                    widget = self.table.cellWidget(current_row, col)
                    if widget is not None:
                        # Cell widgets that opt into themed backgrounds
                        # (source/target QTextEdits) take the tint via their
                        # API. Other cell widgets are left alone – they have
                        # their own opinions about how to render selection.
                        if hasattr(widget, 'set_background_color'):
                            # Force dark text so the cell stays readable on the
                            # pale-blue selection tint (light editor text on light
                            # blue is unreadable in dark themes).
                            widget.set_background_color(self.SELECTION_TINT, text_color='#000000')
                    else:
                        item = self.table.item(current_row, col)
                        if item is not None:
                            item.setBackground(tint)
            except RuntimeError:
                pass

    def apply_alternating_row_colors(self):
        """Apply alternating row colors to all source and target cells in the grid"""
        if not hasattr(self, 'table') or not self.table:
            return

        # Clear cached settings to force reload
        if hasattr(self, '_row_color_settings_cached'):
            delattr(self, '_row_color_settings_cached')

        for row in range(self.table.rowCount()):
            # Keep UI responsive during large grid updates
            if row % 50 == 0:
                QApplication.processEvents()

            source_widget = self.table.cellWidget(row, 2)
            target_widget = self.table.cellWidget(row, 3)

            if source_widget and target_widget:
                self._apply_row_color(row, source_widget, target_widget)

        # Re-apply the selection tint to the current row across all columns –
        # we just overwrote its cells with the alternating colour. Pass
        # previous_row=-1 so the helper skips the restore branch.
        current_row = self.table.currentRow()
        if current_row is not None and current_row >= 0:
            self._update_row_selection_tint(current_row, 0, -1, 0)

        self.log("✓ Alternating row colors applied")

    def on_font_changed(self):
        """Handle font change - legacy method for compatibility"""
        self.apply_font_to_grid()
        self.auto_resize_rows()

    def zoom_in(self):
        """Increase grid font size (zoom in)."""
        self.increase_font_size()

    def zoom_out(self):
        """Decrease grid font size (zoom out)."""
        self.decrease_font_size()

    def save_current_font_sizes(self):
        """Save current font sizes to preferences (global general_settings)."""
        try:
            from modules.translation_results_panel import CompactMatchItem, TranslationResultsPanel
            general_settings = self.load_general_settings()
            general_settings['grid_font_family'] = self.default_font_family
            general_settings['grid_font_size'] = self.default_font_size
            if hasattr(CompactMatchItem, 'font_size_pt'):
                general_settings['results_match_font_size'] = CompactMatchItem.font_size_pt
            if hasattr(TranslationResultsPanel, 'compare_box_font_size'):
                general_settings['results_compare_font_size'] = TranslationResultsPanel.compare_box_font_size
            if hasattr(CompactMatchItem, 'show_tags'):
                general_settings['results_show_tags'] = CompactMatchItem.show_tags
            if hasattr(EditableGridTextEditor, 'tag_highlight_color'):
                general_settings['tag_highlight_color'] = EditableGridTextEditor.tag_highlight_color
            # Save Match Panel font settings
            general_settings['match_panel_font_size'] = type(self).match_panel_font_size
            general_settings['match_panel_font_family'] = type(self).match_panel_font_family
            general_settings['match_panel_font_bold'] = type(self).match_panel_font_bold
            # Preserve other settings
            if 'restore_last_project' not in general_settings:
                general_settings['restore_last_project'] = False
            self.save_general_settings(general_settings)

            # v1.10.184: mirror Match Panel zoom into the current
            # project's ui_settings so the value round-trips per-
            # project as well as globally. The next .svproj save
            # picks it up via save_project's existing ui_settings
            # write; the per-project load on project open restores it
            # in preference to the global default. Pre-v1.10.184 this
            # was global-only and a user reported it not surviving
            # restart at all — by also writing through the project
            # path we get a second persistence layer, so the zoom
            # survives even if the global settings file is somehow
            # not being written (which the silent except below used
            # to mask).
            if hasattr(self, 'current_project') and self.current_project is not None:
                if not getattr(self.current_project, 'ui_settings', None):
                    self.current_project.ui_settings = {}
                self.current_project.ui_settings['match_panel_font_size'] = type(self).match_panel_font_size
        except Exception as e:
            # v1.10.184: was a silent `pass`. Hidden failures here are
            # exactly what caused the recurring "match-panel zoom
            # doesn't survive restart" bug to go undiagnosed — without
            # a log line there's nothing to point at. Keep the error
            # non-fatal (font-size save shouldn't take down the app)
            # but surface it in the log so the user can paste it.
            try:
                self.log(f"⚠ Could not save font sizes: {e}")
            except Exception:
                pass

    def find_nt_matches_in_source(self, source_text: str) -> list:
        """
        Find non-translatable matches in source text.

        NTs are now sourced from termbase entries flagged with
        ``is_nontranslatable = 1`` (the same convention the Trados plugin
        uses), rather than from a separate .svntl file system. The
        termbase index built by ``_build_termbase_index`` already carries
        the flag plus pre-compiled regex patterns, so this is a single
        pass with no extra database round-trips.

        Args:
            source_text: Source text to search in

        Returns:
            List of match dicts with 'text', 'start', 'end', 'list_name'
            (kept named ``list_name`` for compatibility with consumers
            inherited from the old .svntl system; populated with the
            termbase name).
        """
        if not source_text:
            return []

        try:
            with self.termbase_index_lock:
                if not self.termbase_index:
                    return []
                index = self.termbase_index
        except Exception:
            return []

        source_lower = source_text.lower()
        matches = []
        seen_positions = set()

        for term in index:
            if not term.get('is_nontranslatable'):
                continue

            term_lower = term.get('source_term_lower') or ''
            if not term_lower or term_lower not in source_lower:
                continue

            pattern = term.get('pattern')
            if pattern:
                try:
                    iter_matches = pattern.finditer(source_lower)
                except Exception:
                    continue
                for m in iter_matches:
                    start, end = m.start(), m.end()
                    key = (start, end)
                    if key in seen_positions:
                        continue
                    seen_positions.add(key)
                    matches.append({
                        'text': source_text[start:end],
                        'start': start,
                        'end': end,
                        'list_name': term.get('termbase_name', ''),
                        'term_id': term.get('term_id'),
                        'termbase_id': term.get('termbase_id'),
                    })

        # Sort by position, then by length (longer matches first for same start)
        matches.sort(key=lambda m: (m['start'], -(m['end'] - m['start'])))
        return matches

    def _apply_proactive_highlighting(self, segment_id: int, termbase_matches_json: str):
        """
        Apply termbase highlighting proactively to a segment that was prefetched.
        Called on main thread via signal from prefetch worker.
        
        This enables "see-ahead" highlighting: while you're working on segment N,
        segments N+1, N+2, N+3 already show their glossary matches highlighted.
        
        Args:
            segment_id: The segment ID to highlight
            termbase_matches_json: JSON-encoded termbase matches dict (thread-safe transfer)
        """
        import json

        if not self.current_project or not self.table:
            return

        try:
            # Decode the matches from JSON
            termbase_matches = json.loads(termbase_matches_json) if termbase_matches_json else {}

            if not termbase_matches:
                return  # Nothing to highlight

            # Find the row for this segment ID
            row = -1
            for r in range(self.table.rowCount()):
                id_item = self.table.item(r, 0)
                if id_item:
                    try:
                        row_seg_id = int(id_item.text())
                        if row_seg_id == segment_id:
                            row = r
                            break
                    except ValueError:
                        continue

            if row < 0:
                return  # Segment not visible in current page

            # Get segment source text
            segment = None
            for seg in self.current_project.segments:
                if seg.id == segment_id:
                    segment = seg
                    break

            if not segment:
                return

            # Apply highlighting (this updates the source cell widget)
            self.highlight_source_with_termbase(row, segment.source, termbase_matches)

        except Exception:
            pass  # Silent failure for proactive highlighting

    @staticmethod
    def _apply_case_pattern(original: str, replacement: str) -> str:
        """
        Adjust *replacement* to match the case pattern of *original*.

        Recognised patterns (checked in order of specificity):
          1. ALL CAPS          → replacement.upper()
          2. all lowercase     → replacement.lower()
          3. Title Case        → replacement.title()
          4. Sentence case     → first char upper, rest as-is from replacement
          5. Mixed / unknown   → replacement unchanged

        Only the first character is checked for (4) when the string is longer
        than one word; for single-word originals only UPPER/lower/Title are used.
        """
        if not original or not replacement:
            return replacement

        if original.isupper():
            return replacement.upper()
        if original.islower():
            return replacement.lower()
        if original.istitle():
            return replacement.title()
        # Sentence case: first char upper, rest lower-or-mixed in original
        if original[0].isupper() and original[1:].islower():
            return replacement[0].upper() + replacement[1:] if replacement else replacement
        # Fallback: preserve replacement as typed
        return replacement

    def toggle_invisible_display(self, char_type):
        """Toggle display of specific invisible character type"""
        if not hasattr(self, 'invisible_display_settings'):
            self.invisible_display_settings = {
                'spaces': False,
                'tabs': False,
                'nbsp': False,
                'linebreaks': False
            }

        # Toggle the setting
        self.invisible_display_settings[char_type] = not self.invisible_display_settings[char_type]

        # Keep the highlighter NBSP-shading flag in sync (covers the no-project case too,
        # where refresh_grid_invisibles() early-returns before it can update the flag).
        TagHighlighter._show_nbsp = self.invisible_display_settings.get('nbsp', False)

        # Refresh the grid to show/hide invisibles (re-runs the highlighter via setPlainText)
        self.refresh_grid_invisibles()

        # Log the change
        status = "enabled" if self.invisible_display_settings[char_type] else "disabled"
        char_names = {
            'spaces': 'Spaces',
            'tabs': 'Tabs',
            'nbsp': 'Non-breaking Spaces',
            'linebreaks': 'Line Breaks'
        }
        self.log(f"Show invisibles: {char_names[char_type]} {status}")

    def toggle_all_invisibles(self):
        """Toggle all invisible characters on or off"""
        if not hasattr(self, 'invisible_display_settings'):
            self.invisible_display_settings = {
                'spaces': False,
                'tabs': False,
                'nbsp': False,
                'linebreaks': False
            }

        # Check if any are currently on
        any_on = any(self.invisible_display_settings.values())

        # Toggle all to opposite state
        new_state = not any_on
        self.invisible_display_settings = {
            'spaces': new_state,
            'tabs': new_state,
            'nbsp': new_state,
            'linebreaks': new_state
        }

        # Keep the highlighter NBSP-shading flag in sync (covers the no-project case too).
        TagHighlighter._show_nbsp = self.invisible_display_settings.get('nbsp', False)

        # Update menu checkboxes – block signals so setChecked() does NOT re-fire
        # toggle_invisible_display() and cause 4 extra grid reloads
        for action_attr in ('show_spaces_action', 'show_tabs_action',
                            'show_nbsp_action', 'show_linebreaks_action'):
            action = getattr(self, action_attr, None)
            if action is not None:
                action.blockSignals(True)
                action.setChecked(new_state)
                action.blockSignals(False)

        # Refresh the grid (in-place, no full reload; re-runs the highlighter via setPlainText)
        self.refresh_grid_invisibles()

        status = "enabled" if new_state else "disabled"
        self.log(f"Show invisibles: All {status}")

    def refresh_grid_invisibles(self):
        """Refresh invisible-character display in-place without reloading the grid.

        Iterates existing cell widgets and re-applies (or removes) invisible-character
        substitutions directly, avoiding the expensive full grid rebuild that
        load_segments_to_grid() would trigger.

        Signal handling strategy
        -----------------------
        We call setPlainText() with Qt signals blocked on each widget.  Unlike
        load_segments_to_grid() we do NOT unblock signals afterwards – the widgets
        already have their textChanged handlers connected from the original grid
        load, and those handlers remain active for future user edits.  Keeping
        signals blocked only for the duration of setPlainText() (and the brief
        queued-event window) prevents stale invisible markers from being written
        back into segment.target.

        We also raise _suppress_target_change_handlers as a belt-and-braces guard
        so that any signal that does slip through (e.g. on an already-focused cell
        whose queued event fires before blockSignals takes effect) is silently
        ignored by the on_target_text_changed handler.
        """
        if not hasattr(self, 'table') or not self.table:
            return
        if not hasattr(self, 'invisible_display_settings'):
            return
        if not hasattr(self, 'current_project') or not self.current_project:
            return

        # Update the legacy boolean used by word-wrap logic
        self.showing_invisible_spaces = self.invisible_display_settings.get('spaces', False)

        # Keep the highlighter's NBSP shading flag in sync BEFORE we re-set widget text:
        # NBSP is no longer substituted into the text, so toggling it does not change the
        # string. The per-cell setPlainText() below re-runs TagHighlighter.highlightBlock(),
        # which reads this class flag to decide whether to shade real NBSP characters.
        TagHighlighter._show_nbsp = self.invisible_display_settings.get('nbsp', False)

        segments = self.current_project.segments
        row_count = self.table.rowCount()

        # Suppress the target-changed handler for all cells during the refresh
        old_suppress = getattr(self, '_suppress_target_change_handlers', False)
        self._suppress_target_change_handlers = True
        try:
            for row in range(row_count):
                if row >= len(segments):
                    break
                segment = segments[row]

                # --- Source column (col 2) – read-only, no save risk ---
                source_widget = self.table.cellWidget(row, 2)
                if source_widget is not None:
                    source_for_display = segment.source
                    if self.hide_outer_wrapping_tags:
                        stripped, _ = strip_outer_wrapping_tags(source_for_display)
                        source_for_display = stripped
                    if getattr(self, 'tag_view_mode', 'tags') == 'compact':
                        source_for_display = compact_tags(source_for_display)
                    new_source_text = self.apply_invisible_replacements(source_for_display)
                    source_widget.blockSignals(True)
                    source_widget.setPlainText(new_source_text)
                    source_widget.blockSignals(False)

                # --- Target column (col 3) ---
                # Always use segment.target (the clean, marker-free canonical text)
                # as the source of truth, then re-apply current marker settings.
                target_widget = self.table.cellWidget(row, 3)
                if target_widget is not None:
                    target_for_display = segment.target
                    if self.hide_outer_wrapping_tags:
                        stripped, _ = strip_outer_wrapping_tags(target_for_display)
                        target_for_display = stripped
                    # Apply compact tag shortening (display only – reversed before saving)
                    if getattr(self, 'tag_view_mode', 'tags') == 'compact':
                        # Re-use the tag_map built from source so numbering stays consistent
                        tag_map = {}
                        src_display = segment.source
                        if self.hide_outer_wrapping_tags:
                            src_display, _ = strip_outer_wrapping_tags(src_display)
                        compact_tags(src_display, tag_map)
                        target_for_display = compact_tags(target_for_display, tag_map)
                        target_widget._compact_tag_map = tag_map
                    else:
                        target_widget._compact_tag_map = None
                    new_target_text = self.apply_invisible_replacements(target_for_display)
                    target_widget.blockSignals(True)
                    target_widget.setPlainText(new_target_text)
                    # Keep signals blocked – user edits will unblock naturally when
                    # the widget is next focused and the handler fires from keystrokes.
                    # We restore signals here so the widget stays interactive, but
                    # _suppress_target_change_handlers guards against the queued event.
                    target_widget.blockSignals(False)
                    # Reset initial-load flag so the single queued textChanged event
                    # that Qt delivers after blockSignals(False) is eaten harmlessly.
                    target_widget._initial_load_complete = False
        finally:
            self._suppress_target_change_handlers = old_suppress

        # Resize rows since space→middle-dot substitution changes text width
        self.auto_resize_rows()

        # Refresh Match Panel TM panes so ↵ markers appear/disappear with toggling
        if hasattr(self, 'match_panel_tm_matches') and self.match_panel_tm_matches:
            try:
                self._update_match_panel_tm_display()
            except Exception:
                pass

    def _refresh_source_column_display(self):
        """Refresh grid to reflect hide_outer_wrapping_tags setting.

        Reloads the entire grid to properly handle:
        - Tag stripping on both Source and Target columns
        - WYSIWYG list numbering/bullet prefixes
        - Proper list position calculation
        """
        if not hasattr(self, 'table') or not self.table:
            return
        if not hasattr(self, 'current_project') or not self.current_project:
            return

        # Reload grid to apply changes with proper list numbering calculation
        self.load_segments_to_grid()

    def apply_invisible_replacements(self, text):
        """Apply invisible character replacements to text based on settings"""
        # Always guard recognised tags against being split mid-way by the grid
        # word-wrap (see protect_tags_from_linebreak) – runs even when no
        # invisible-marker settings are active. The WORD JOINER it inserts is
        # stripped again by reverse_invisible_replacements, so saved text is
        # unaffected. Applies to both source and target display cells.
        text = protect_tags_from_linebreak(text)

        if not hasattr(self, 'invisible_display_settings'):
            return text

        result = text

        # Replace spaces with middle dot (·) followed by zero-width space for word-wrap capability
        # The zero-width space (U+200B) provides a line-break opportunity
        if self.invisible_display_settings.get('spaces', False):
            result = result.replace(' ', '·\u200B')

        # Replace tabs with right arrow (→) followed by zero-width space
        if self.invisible_display_settings.get('tabs', False):
            result = result.replace('\t', '→\u200B')

        # Non-breaking spaces are NO LONGER substituted (Camp B approach, as used by
        # VS Code/memoQ/Trados): the real U+00A0 / U+202F characters stay in the text and
        # their positions are shaded with a coloured background box by the TagHighlighter.
        # This avoids the old fragile '°' sentinel that collided with real degree signs.

        # Replace line breaks with return arrow (↵)
        if self.invisible_display_settings.get('linebreaks', False):
            result = result.replace('\n', '↵\n')
            result = result.replace('\r', '↵')

        return result

    def reverse_invisible_replacements(self, text):
        """Reverse ALL invisible character replacements to get original text.

        NOTE: We always strip ALL marker types unconditionally, regardless of
        which settings are currently enabled.  This is necessary because:
        - The user may toggle a setting OFF after markers were already placed in
          the widget; the markers must still be removed from the saved text.
        - refresh_grid_invisibles() calls setPlainText() with the clean
          segment.target text, but a queued textChanged event fired afterwards
          would see the freshly-set text (which may contain markers) and must
          still clean it correctly.
        """
        result = text

        # Strip the tag-protection WORD JOINER (U+2060) unconditionally. It is
        # display-only (inserted by protect_tags_from_linebreak) and must never
        # reach saved segment text, tag counts, TM, or exports.
        result = result.replace(WORD_JOINER, '')

        # Reverse spaces (middle dot + zero-width space → space) – always
        result = result.replace('·\u200B', ' ')
        result = result.replace('·', ' ')  # Fallback for any without zero-width space

        # Reverse tabs (right arrow + zero-width space → tab) – always
        result = result.replace('→\u200B', '\t')
        result = result.replace('→', '\t')  # Fallback

        # Reverse line breaks (return arrow → line break) – always
        result = result.replace('↵\n', '\n')
        result = result.replace('↵', '\r')
        # Legacy: pilcrow was used as line-break marker before v1.9.295
        result = result.replace('¶\n', '\n')
        result = result.replace('¶', '\r')

        # Always strip any stray zero-width spaces left over
        result = result.replace('\u200B', '')

        return result

    def _toggle_tag_view_via_shortcut(self):
        """Toggle tag view using keyboard shortcut (Ctrl+Shift+H) – cycles: tags → compact → wysiwyg"""
        mode = getattr(self, 'tag_view_mode', 'tags')
        cycle = {'tags': 'compact', 'compact': 'wysiwyg', 'wysiwyg': 'tags'}
        self._set_tag_view_mode(cycle.get(mode, 'tags'))

    def _set_tag_view_mode(self, mode: str):
        """Set the tag view mode: 'tags', 'compact', or 'wysiwyg'"""
        self.tag_view_mode = mode
        self.show_tags = (mode == 'tags' or mode == 'compact')

        # Update segmented control buttons
        if hasattr(self, 'wysiwyg_btn'):
            self.wysiwyg_btn.setChecked(mode == 'wysiwyg')
        if hasattr(self, 'compact_btn'):
            self.compact_btn.setChecked(mode == 'compact')
        if hasattr(self, 'tags_btn'):
            self.tags_btn.setChecked(mode == 'tags')

        labels = {'tags': '🏷️ Tag View – showing raw tags',
                  'compact': '📦 Compact View – verbose tags shortened',
                  'wysiwyg': '✨ WYSIWYG View – showing formatted text'}
        self.log(labels.get(mode, mode))

        if hasattr(self, 'table') and self.current_project:
            self._refresh_grid_display_mode()

    @staticmethod
    def _wysiwyg_runs_to_tagged_text(runs):
        """Serialise ``[(text, active_tag_names)]`` runs back into our inline
        tag syntax, opening/closing tags in a stable order so the output
        round-trips (e.g. ``<b><i>x</i></b>``)."""
        parts = []
        open_tags = []  # currently-open tag names, in the order opened
        for text, active in runs:
            # Longest common prefix of what's open vs. what this run wants.
            common = 0
            while (common < len(open_tags) and common < len(active)
                   and open_tags[common] == active[common]):
                common += 1
            # Close everything opened past the common prefix (reverse order).
            for t in reversed(open_tags[common:]):
                parts.append(f'</{t}>')
            del open_tags[common:]
            # Open the tags this run needs that aren't open yet.
            for t in active[common:]:
                parts.append(f'<{t}>')
                open_tags.append(t)
            parts.append(text)
        for t in reversed(open_tags):
            parts.append(f'</{t}>')
        return ''.join(parts)

    def _wysiwyg_document_to_tagged_text(self, text_edit):
        """Rebuild our inline formatting tags from a QTextEdit that is showing
        rendered (WYSIWYG) formatting.

        Editing a cell in WYSIWYG view used to drop bold/italic/underline,
        because the save path read ``toPlainText()`` — and plain text of
        rendered bold carries no ``<b>``. Here the character formatting is read
        back out and re-emitted as ``<b>/<i>/<u>/<sub>/<sup>`` tags. Any other
        inline tags (``<cf …>``, ``<g1>``, ``<x1/>``) are already literal text
        in the document (WYSIWYG only renders the five it knows), so they ride
        along untouched. Returns the reconstructed string."""
        from PyQt6.QtGui import QTextCharFormat, QFont

        doc = text_edit.document()
        # Baseline from the editor's own font, so a (hypothetical) bold base
        # font doesn't make every fragment look bold.
        base = text_edit.font()
        base_bold = base.weight() >= QFont.Weight.Bold
        base_italic = base.italic()
        base_underline = base.underline()
        AlignSub = QTextCharFormat.VerticalAlignment.AlignSubScript
        AlignSup = QTextCharFormat.VerticalAlignment.AlignSuperScript

        # (tag, predicate) in a fixed order → stable nesting.
        DIMS = [
            ('b',   lambda f: (f.fontWeight() >= QFont.Weight.Bold) and not base_bold),
            ('i',   lambda f: f.fontItalic() and not base_italic),
            ('u',   lambda f: f.fontUnderline() and not base_underline),
            ('sub', lambda f: f.verticalAlignment() == AlignSub),
            ('sup', lambda f: f.verticalAlignment() == AlignSup),
        ]

        runs = []
        block = doc.begin()
        first_block = True
        while block.isValid():
            if not first_block:
                runs.append(('\n', []))   # paragraph break between blocks
            first_block = False
            it = block.begin()
            while not it.atEnd():
                frag = it.fragment()
                if frag.isValid():
                    fmt = frag.charFormat()
                    active = [name for name, pred in DIMS if pred(fmt)]
                    runs.append((frag.text(), active))
                it += 1
            block = block.next()

        # A cell rendered from Phrase tags ({b>…<b}) is saved back in Phrase
        # syntax: <b> in an MXLIFF or Phrase DOCX target is not a Phrase tag.
        if getattr(text_edit, '_phrase_tags', False):
            rebuilt = wysiwyg_runs_to_phrase_text(runs)
        else:
            rebuilt = self._wysiwyg_runs_to_tagged_text(runs)
        # QTextEdit uses U+2028/U+2029 for soft/line breaks; normalise to \n.
        return rebuilt.replace(chr(0x2028), '\n').replace(chr(0x2029), '\n')

    def toggle_tag_view(self, checked: bool, button: QPushButton = None):
        """Legacy toggle – routes to _set_tag_view_mode for backward compatibility"""
        self._set_tag_view_mode('tags' if checked else 'wysiwyg')

    def _refresh_grid_display_mode(self):
        """Refresh all visible cells to reflect current tag view mode"""
        if not hasattr(self, 'table') or not self.current_project:
            return

        mode = getattr(self, 'tag_view_mode', 'tags')

        # Suppress target change handlers during refresh to prevent data corruption
        # (switching display modes changes widget content which could trigger textChanged)
        previous_suppression = getattr(self, '_suppress_target_change_handlers', False)
        self._suppress_target_change_handlers = True
        try:
            self._refresh_grid_display_mode_rows(mode)
        finally:
            # Also after an error: left on, every later edit would be dropped
            self._suppress_target_change_handlers = previous_suppression

        # Auto-resize rows since tag length changes affect row heights
        self.auto_resize_rows()

    def _refresh_grid_display_mode_rows(self, mode):
        """Re-render the built rows' editors in ``mode`` (see _refresh_grid_display_mode)."""
        # One id → segment map for the whole pass: looking each row up with
        # _segment_for_grid_row scanned the segment list per row, which took
        # 16 s on a 17,000-segment project (issue #185).
        segments_by_id = {s.id: s for s in self.current_project.segments}

        # Get current visible rows
        for row in range(self.table.rowCount()):
            # Rows not built yet (another page, or not scrolled to) have no
            # editors to update – they are built in the current mode later.
            source_widget = self.table.cellWidget(row, 2)
            target_widget = self.table.cellWidget(row, 3)
            if source_widget is None and target_widget is None:
                continue
            # Resolve the segment via the id in column 0 — NOT segments[row].
            # The table row index only equals the list position in unsorted,
            # unfiltered, single-page view; under a sort/filter/pagination the
            # positional lookup rendered each cell from the WRONG segment, so
            # toggling the view mode appeared to "not update" (or showed stale
            # text). The id-based lookup is correct in every view state.
            try:
                segment = segments_by_id.get(int(self.table.item(row, 0).text()))
            except (AttributeError, ValueError, TypeError):
                segment = None
            if segment is None:
                continue

            # Apply tag stripping if enabled
            source_for_display = segment.source
            target_for_display = segment.target

            if self.hide_outer_wrapping_tags:
                stripped_source, _ = strip_outer_wrapping_tags(segment.source)
                source_for_display = stripped_source
                stripped_target, _ = strip_outer_wrapping_tags(segment.target)
                target_for_display = stripped_target

            # Compact mode: build tag map from source, apply to both columns.
            # The map is stored on the target widget so edits can be reversed.
            if mode == 'compact':
                tag_map = {}
                source_for_display = compact_tags(source_for_display, tag_map)
                target_for_display = compact_tags(target_for_display, tag_map)
            else:
                tag_map = None

            # Apply invisible character replacements (e.g. ↵ for line breaks)
            source_for_display = self.apply_invisible_replacements(source_for_display)
            target_for_display = self.apply_invisible_replacements(target_for_display)

            # For compact mode, use show_tags=True so TagHighlighter can color the placeholders
            show_tags_for_cell = (mode in ('tags', 'compact'))

            # Update source cell (column 2)
            if source_widget and hasattr(source_widget, 'update_display_mode'):
                source_widget.update_display_mode(source_for_display, show_tags_for_cell)

            # Update target cell (column 3)
            if target_widget and hasattr(target_widget, 'update_display_mode'):
                target_widget.update_display_mode(target_for_display, show_tags_for_cell)
                # Store tag map for reverse-expanding on save
                target_widget._compact_tag_map = tag_map

    def update_grid_delegate(self):
        """Update the grid's item delegate to reflect current settings.

        v1.10.229: Also walks the existing source cell widgets and flips
        their editable state. The setItemDelegate call below is kept for
        the (rare) code paths that still rely on the delegate, but the
        source column is rendered via setCellWidget — which pre-empts the
        delegate — so the delegate swap alone is not enough to make the
        "Allow Replace in Source Text" setting take effect on a populated
        grid. This is what made the feature appear silently broken: banner
        flipped, delegate recreated, cells stayed locked.
        """
        if not hasattr(self, 'table') or not self._widget_is_alive(self.table):
            return

        # Recreate the delegate with updated settings (kept for parity with
        # any non-cell-widget rendering paths).
        self.table.setItemDelegate(
            WordWrapDelegate(None, self.table, self.allow_replace_in_source)
        )

        # Walk the live source cell widgets and toggle their readOnly /
        # interaction-flags / amber-warning styling in place. Locked
        # segments stay read-only regardless. We read the locked flag from
        # the widget itself (stashed at creation time) instead of indexing
        # into current_project.segments by row — that's wrong under
        # filters / sorts where row N ≠ segment N.
        try:
            for row in range(self.table.rowCount()):
                widget = self.table.cellWidget(row, 2)
                if widget is None or not hasattr(widget, 'set_source_editable'):
                    continue
                locked = bool(getattr(widget, '_segment_locked', False))
                widget.set_source_editable(
                    bool(self.allow_replace_in_source) and not locked
                )
        except Exception as e:
            # Never let a UI refresh failure crash the settings dialog.
            self.log(f"⚠ Could not refresh source cell editability: {e}")
