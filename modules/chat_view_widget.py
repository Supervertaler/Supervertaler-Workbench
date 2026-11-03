"""
Chat View Widget for Supervertaler
=====================================

A self-contained QWidget that displays a chat conversation and provides
input controls. Multiple instances connect to the same ChatBackend so
they all stay in sync.
"""

import base64
from io import BytesIO

from PyQt6.QtCore import Qt, pyqtSignal, QTimer
from PyQt6.QtGui import QFont, QCursor, QAction, QImage
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QListWidget, QListWidgetItem,
    QPlainTextEdit, QPushButton, QLabel, QFrame, QMenu, QApplication,
    QAbstractItemView, QSizePolicy, QToolTip,
)

from modules.chat_backend import ChatBackend
from modules.chat_message_delegate import ChatMessageDelegate
from modules.trados_bridge_client import (
    TradosBridgeClient,
    TradosBridgePoller,
    format_context_for_prompt,
)


class ChatViewWidget(QWidget):
    """
    Complete chat view: message list + input + send/clear buttons.

    Parameters:
        backend: ChatBackend instance (shared across views)
        compact: tighter spacing for embedding in a floating window

    Note: the AutoPrompt button used to live in this widget (gated by a
    ``show_autoprompt`` constructor flag). It now lives in the Prompt
    Library toolbar instead, since AutoPrompt creates a prompt that
    naturally belongs in the library next to other prompts.
    """

    # Emitted when the user presses Escape in the input field
    escape_pressed = pyqtSignal()

    def __init__(self, backend: ChatBackend, parent=None, *,
                 compact: bool = False):
        super().__init__(parent)
        self._backend = backend
        self._compact = compact

        self._chat_display: QListWidget = None
        self._chat_input: QPlainTextEdit = None
        self._thinking_label: QLabel = None
        self._thinking_timer: QTimer = None
        self._thinking_dots: int = 0
        self._pending_images: list = []  # List of (label, base64_str) tuples
        self._drag_start = None  # tracks a drag attempt for the copy hint

        self._init_ui()
        self._connect_backend()
        self._load_existing_history()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _init_ui(self):
        layout = QVBoxLayout(self)
        m = 2 if self._compact else 5
        layout.setContentsMargins(m, m, m, m)
        layout.setSpacing(3 if self._compact else 5)

        # Chat display
        self._chat_display = QListWidget()
        self._chat_display.setItemDelegate(ChatMessageDelegate(self._chat_display))
        self._chat_display.setStyleSheet("""
            QListWidget {
                background-color: #FFFFFF;
                border: 1px solid #E8E8EA;
                border-radius: 4px;
                font-size: 9pt;
                font-family: 'Segoe UI', system-ui, sans-serif;
            }
            QListWidget::item { border: none; background: transparent; }
            QListWidget::item:selected { background: transparent; }
            QListWidget::item:hover { background: transparent; }
        """)
        self._chat_display.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self._chat_display.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._chat_display.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self._chat_display.setVerticalScrollMode(
            QAbstractItemView.ScrollMode.ScrollPerPixel
        )
        self._chat_display.setSpacing(0)
        self._chat_display.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu
        )
        self._chat_display.customContextMenuRequested.connect(
            self._show_context_menu
        )
        # Double-clicking a message opens it in a selectable/copyable viewer.
        # The list items are painted (not real widgets), so in-place text
        # selection isn't possible; this viewer is how users select and copy
        # part of a message rather than only the whole thing.
        self._chat_display.itemDoubleClicked.connect(
            self._open_message_viewer
        )
        # The bubbles are painted, not text widgets, so click-dragging to
        # select does nothing. Watch the viewport for a drag attempt and
        # nudge the user toward the right-click copy menu (improves
        # discoverability without the full inline-selection rewrite).
        self._chat_display.viewport().installEventFilter(self)
        layout.addWidget(self._chat_display, 1)

        # Context chips row (above input)
        self._context_chips_row = QHBoxLayout()
        self._context_chips_row.setContentsMargins(2, 0, 2, 0)
        self._context_chips_row.setSpacing(4)
        self._context_toggles = {}
        self._init_context_chips()
        layout.addLayout(self._context_chips_row, 0)

        # Image attachment strip (hidden until images are pasted)
        self._image_strip = QHBoxLayout()
        self._image_strip.setContentsMargins(2, 0, 2, 0)
        self._image_strip.setSpacing(4)
        self._image_strip_widget = QWidget()
        self._image_strip_widget.setLayout(self._image_strip)
        self._image_strip_widget.hide()
        layout.addWidget(self._image_strip_widget, 0)

        # Input area
        input_frame = QFrame()
        input_frame.setStyleSheet("""
            QFrame {
                background-color: white;
                border: 1px solid #E0E0E0;
                border-radius: 5px;
                padding: 5px;
            }
        """)
        input_layout = QVBoxLayout(input_frame)
        input_layout.setContentsMargins(5, 5, 5, 5)
        input_layout.setSpacing(5)

        self._chat_input = QPlainTextEdit()
        self._chat_input.setMaximumHeight(80 if not self._compact else 60)
        self._chat_input.setPlaceholderText(
            "Type your message here... (Shift+Enter for new line, Esc to return)"
        )
        self._chat_input.setStyleSheet("""
            QPlainTextEdit {
                border: none; font-size: 10pt;
                color: #1a1a1a; background-color: white;
                padding: 4px;
            }
        """)
        self._chat_input.installEventFilter(self)
        input_layout.addWidget(self._chat_input)

        # Bottom row: Clear (left) | stretch | model selector | Send (right)
        bottom_row = QHBoxLayout()
        bottom_row.setContentsMargins(0, 0, 0, 0)
        bottom_row.setSpacing(6)

        clear_btn = QPushButton("Clear")
        clear_btn.setStyleSheet("""
            QPushButton {
                color: #787878; background: transparent;
                border: none; font-size: 8pt; padding: 2px 6px;
            }
            QPushButton:hover { color: #424242; }
        """)
        clear_btn.clicked.connect(self._clear_chat)
        bottom_row.addWidget(clear_btn)

        bottom_row.addStretch()

        # Clickable model selector (opens provider/model menu)
        self._model_btn = QPushButton(self._backend.get_model_display_name())
        self._model_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._model_btn.setStyleSheet("""
            QPushButton {
                color: #8C8C8C; background: transparent;
                border: none; font-size: 7pt; padding: 2px 6px;
            }
            QPushButton:hover { color: #1976D2; text-decoration: underline; }
        """)
        self._model_btn.setToolTip("Click to change model")
        self._model_btn.clicked.connect(self._show_model_menu)
        bottom_row.addWidget(self._model_btn)

        send_btn = QPushButton("Send")
        send_btn.setStyleSheet("""
            QPushButton {
                background-color: #1976D2; color: white;
                font-weight: bold; padding: 8px 20px;
                border-radius: 5px; border: none;
            }
            QPushButton:hover { background-color: #1565C0; }
            QPushButton:pressed { background-color: #0D47A1; }
        """)
        send_btn.clicked.connect(self._send_message)
        bottom_row.addWidget(send_btn)

        input_layout.addLayout(bottom_row)
        layout.addWidget(input_frame, 0)

    # ------------------------------------------------------------------
    # Backend connection
    # ------------------------------------------------------------------

    def _connect_backend(self):
        self._backend.message_added.connect(self._on_message_added)
        self._backend.chat_cleared.connect(self._on_chat_cleared)
        self._backend.thinking_started.connect(self._on_thinking_started)
        self._backend.thinking_finished.connect(self._on_thinking_finished)

    def _load_existing_history(self):
        """Populate display from existing backend history (for late-created views)."""
        for msg in self._backend.get_recent_history(10):
            self._add_display_item(msg)

    # ------------------------------------------------------------------
    # Slots (from backend signals)
    # ------------------------------------------------------------------

    def _on_message_added(self, msg: dict):
        self._add_display_item(msg)
        self._chat_display.scrollToBottom()

    def _on_chat_cleared(self):
        self._chat_display.clear()
        self._on_thinking_finished()

    def _on_thinking_started(self):
        """Show an animated 'Thinking...' label below the chat."""
        if not self._thinking_label:
            self._thinking_label = QLabel()
            self._thinking_label.setStyleSheet(
                "color: #646464; font-size: 8pt; font-style: italic; "
                "padding: 4px 8px; background-color: #FCFCFC; "
                "border-top: 1px solid #E8E8EA;"
            )
            # Insert just above the input frame (second-to-last widget)
            layout = self.layout()
            layout.insertWidget(layout.count() - 1, self._thinking_label, 0)

        self._thinking_dots = 0
        self._thinking_label.setText("  Thinking")
        self._thinking_label.show()

        if not self._thinking_timer:
            self._thinking_timer = QTimer(self)
            self._thinking_timer.timeout.connect(self._animate_thinking)
        self._thinking_timer.start(400)

        self._chat_display.scrollToBottom()
        QApplication.processEvents()

    def _animate_thinking(self):
        """Cycle through Thinking, Thinking., Thinking.., Thinking..."""
        self._thinking_dots = (self._thinking_dots + 1) % 4
        dots = "." * self._thinking_dots
        self._thinking_label.setText(f"  Thinking{dots}")

    def _on_thinking_finished(self):
        """Hide the thinking indicator."""
        if self._thinking_timer:
            self._thinking_timer.stop()
        if self._thinking_label:
            self._thinking_label.hide()

    # ------------------------------------------------------------------
    # Context chips
    # ------------------------------------------------------------------

    _CHIP_OFF = """
        QPushButton {
            background-color: #F0F0F0; color: #888;
            border: 1px solid #DDD; border-radius: 10px;
            font-size: 7.5pt; padding: 2px 8px;
        }
        QPushButton:hover { background-color: #E0E0E0; color: #555; }
    """
    _CHIP_ON = """
        QPushButton {
            background-color: #D6E4F0; color: #3D5A80;
            border: 1px solid #9BB8D3; border-radius: 10px;
            font-size: 7.5pt; padding: 2px 8px; font-weight: bold;
        }
        QPushButton:hover { background-color: #C2D6E8; }
    """

    def _init_context_chips(self):
        """Create toggleable context-source chips above the input.

        Left-click toggles on/off. Right-click opens a popover with details.
        """
        chips = [
            ("doc",       "\U0001F4C4 Document",    False),
            ("tm",        "\U0001F4BE TMs",          False),
            ("termbase",  "\U0001F4DA Termbases",    False),
            ("files",     "\U0001F4CE Files",        False),
            ("trados",    "\U0001F517 Trados",        False),
        ]

        label = QLabel("Context:")
        label.setStyleSheet("color: #999; font-size: 7pt; padding-right: 2px;")
        self._context_chips_row.addWidget(label)

        for key, text, default_on in chips:
            btn = QPushButton(text)
            btn.setCheckable(True)
            btn.setChecked(default_on)
            btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            btn.setStyleSheet(self._CHIP_ON if default_on else self._CHIP_OFF)
            btn.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            btn.toggled.connect(lambda checked, k=key, b=btn: self._on_chip_toggled(k, checked, b))
            btn.customContextMenuRequested.connect(
                lambda pos, k=key, b=btn: self._show_chip_popover(k, b)
            )
            self._context_chips_row.addWidget(btn)
            self._context_toggles[key] = btn

        self._context_chips_row.addStretch()

        # Trados Supervertaler Bridge integration: detect whether the Trados
        # plugin is running and auto-light the Trados chip when it is.
        # The chip is hidden entirely when no bridge has ever been seen,
        # to avoid cluttering the UI for users who don't have the plugin.
        self._trados_bridge = TradosBridgeClient.shared()
        self._trados_bridge_available: bool = False
        # Per-user preference: "auto" (= follow availability) or "off"
        # (= user explicitly disabled). Stored on the parent app so all
        # chat views (Sidekick, AI tab, grid) share one pref – toggling
        # the chip in any view affects every Trados-context send path,
        # including the manager-driven _context_aware_send override.
        # Hide the chip until the first availability check so the chip
        # only appears for users who actually have the plugin installed.
        self._context_toggles["trados"].setVisible(False)
        # Subscribe to the process-wide bridge poller.  One off-main-thread
        # probe per backoff interval drives every chat view's chip — no
        # more per-widget QTimer ticking on the UI thread.
        self._trados_poller = TradosBridgePoller.shared()
        self._trados_poller.availability_changed.connect(
            self._on_trados_availability_changed
        )
        # Pref toggles in any sibling chat view trigger a chip refresh
        # here too, since chip checked-state depends on the shared pref.
        self._trados_poller.pref_changed.connect(
            lambda: self._on_trados_availability_changed(
                self._trados_poller.current_state()
            )
        )
        # Apply whatever the poller last observed so the chip is correct
        # on first paint without waiting for the next probe.
        self._on_trados_availability_changed(self._trados_poller.current_state())

    def _on_trados_availability_changed(self, available: bool):
        """Slot: handle a bridge availability transition from the shared poller."""
        # Always keep the chip's checked state in sync with the parent-app
        # pref + availability, even when availability hasn't changed – another
        # ChatViewWidget may have toggled the pref since the last poll.
        chip = self._context_toggles["trados"]
        pref = self._get_trados_chip_pref()
        if available:
            chip.setVisible(True)
            chip.setEnabled(True)
            should_be_on = (pref != "off")
            if chip.isChecked() != should_be_on:
                chip.blockSignals(True)
                chip.setChecked(should_be_on)
                chip.blockSignals(False)
            chip.setStyleSheet(self._CHIP_ON if should_be_on else self._CHIP_OFF)
            chip.setToolTip(
                "Trados plugin detected. Click to toggle whether the active "
                "Trados project context (segment, TM matches, termbase hits) "
                "is included in chat messages."
            )
        else:
            # Bridge gone: keep chip visible if we ever saw it, greyed
            if self._trados_bridge_available:
                chip.setVisible(True)
            chip.setEnabled(False)
            chip.blockSignals(True)
            chip.setChecked(False)
            chip.blockSignals(False)
            chip.setStyleSheet(self._CHIP_OFF)
            chip.setToolTip(
                "Trados plugin not detected. Start Trados Studio with the "
                "Supervertaler plugin to enable Trados-aware chat."
            )

        self._trados_bridge_available = available

    def _get_trados_chip_pref(self) -> str:
        """Read the shared 'auto' / 'off' Trados context pref from the parent app."""
        app = getattr(self._backend, "_parent_app", None)
        if app is None:
            return "auto"
        return getattr(app, "_trados_chip_pref", "auto")

    def _set_trados_chip_pref(self, value: str) -> None:
        app = getattr(self._backend, "_parent_app", None)
        if app is not None:
            app._trados_chip_pref = value

    def _on_chip_toggled(self, key: str, checked: bool, btn):
        """Handle context chip toggle (left-click)."""
        btn.setStyleSheet(self._CHIP_ON if checked else self._CHIP_OFF)

        # Sync with prompt manager toggles
        pm = self._get_prompt_manager()
        if pm:
            if key == "tm":
                pm.include_tm_data = checked
            elif key == "termbase":
                pm.include_termbase_data = checked

        # Trados chip: persist the explicit user preference on the parent
        # app so all chat views (Sidekick, AI tab, grid) and the manager's
        # _context_aware_send override agree.  Tell the shared poller so
        # sibling chat views can re-render their chip immediately.
        if key == "trados":
            self._set_trados_chip_pref("auto" if checked else "off")
            self._trados_poller.notify_pref_changed()

    def _get_prompt_manager(self):
        return getattr(self._backend._parent_app, 'prompt_manager_qt', None)

    def _get_parent_app(self):
        return self._backend._parent_app

    def _show_chip_popover(self, key: str, chip_btn):
        """Show a context menu popover for the given chip (right-click)."""
        if key == "doc":
            self._popover_document(chip_btn)
        elif key == "tm":
            self._popover_tms(chip_btn)
        elif key == "termbase":
            self._popover_termbases(chip_btn)
        elif key == "files":
            self._popover_files(chip_btn)

    def _popover_document(self, chip_btn):
        """Show current document info."""
        menu = QMenu(self)
        menu.setStyleSheet("QMenu { font-size: 9pt; } QMenu::item { padding: 4px 16px; }")

        app = self._get_parent_app()
        if hasattr(app, 'current_project') and app.current_project:
            proj = app.current_project
            name = getattr(proj, 'name', 'Unnamed')
            src = getattr(proj, 'source_lang', '?')
            tgt = getattr(proj, 'target_lang', '?')
            seg_count = len(proj.segments) if hasattr(proj, 'segments') else 0
            menu.addAction(f"\U0001F4C4 {name}").setEnabled(False)
            menu.addAction(f"   {src} \u2192 {tgt} \u2022 {seg_count} segments").setEnabled(False)
        else:
            menu.addAction("No document loaded").setEnabled(False)

        menu.exec(chip_btn.mapToGlobal(chip_btn.rect().bottomLeft()))

    def _popover_tms(self, chip_btn):
        """Show available TMs with checkboxes to select which to include."""
        menu = QMenu(self)
        menu.setStyleSheet("QMenu { font-size: 9pt; } QMenu::item { padding: 4px 16px; }")

        app = self._get_parent_app()
        tm_list = []
        if hasattr(app, 'tm_database') and app.tm_database:
            try:
                tm_list = app.tm_database.get_tm_list()
            except Exception:
                pass

        if not tm_list:
            menu.addAction("No translation memories loaded").setEnabled(False)
        else:
            header = menu.addAction(f"\U0001F4BE {len(tm_list)} TM(s) available")
            header.setEnabled(False)
            menu.addSeparator()
            for tm in tm_list:
                name = tm.get('name', tm.get('tm_id', '?'))
                count = tm.get('entry_count', 0)
                action = menu.addAction(f"{name} ({count:,} entries)")
                action.setCheckable(True)
                action.setChecked(tm.get('enabled', True))

        menu.addSeparator()
        toggle = menu.addAction("\u2713 Include TM data in AI context" if self._context_toggles["tm"].isChecked()
                                else "Include TM data in AI context")
        toggle.triggered.connect(lambda: self._context_toggles["tm"].toggle())

        menu.exec(chip_btn.mapToGlobal(chip_btn.rect().bottomLeft()))

    def _popover_termbases(self, chip_btn):
        """Show available termbases."""
        menu = QMenu(self)
        menu.setStyleSheet("QMenu { font-size: 9pt; } QMenu::item { padding: 4px 16px; }")

        app = self._get_parent_app()
        tb_list = []
        if hasattr(app, 'termbase_mgr') and app.termbase_mgr:
            try:
                tb_list = app.termbase_mgr.get_all_termbases()
            except Exception:
                pass

        if not tb_list:
            menu.addAction("No termbases loaded").setEnabled(False)
        else:
            header = menu.addAction(f"\U0001F4DA {len(tb_list)} termbase(s) available")
            header.setEnabled(False)
            menu.addSeparator()
            for tb in tb_list:
                name = tb.get('name', '?')
                count = tb.get('term_count', 0)
                action = menu.addAction(f"{name} ({count:,} terms)")
                action.setCheckable(True)
                action.setChecked(True)

        menu.addSeparator()
        toggle = menu.addAction("\u2713 Include termbase data in AI context" if self._context_toggles["termbase"].isChecked()
                                else "Include termbase data in AI context")
        toggle.triggered.connect(lambda: self._context_toggles["termbase"].toggle())

        menu.exec(chip_btn.mapToGlobal(chip_btn.rect().bottomLeft()))

    def _popover_files(self, chip_btn):
        """Show attached files with options to add/remove."""
        menu = QMenu(self)
        menu.setStyleSheet("QMenu { font-size: 9pt; } QMenu::item { padding: 4px 16px; }")

        pm = self._get_prompt_manager()
        files = getattr(pm, 'attached_files', []) if pm else []

        if not files:
            menu.addAction("No files attached").setEnabled(False)
        else:
            header = menu.addAction(f"\U0001F4CE {len(files)} file(s) attached")
            header.setEnabled(False)
            menu.addSeparator()
            for f in files:
                name = f.get('name', '?')
                action = menu.addAction(f"\u2022 {name}")
                action.setEnabled(False)

        menu.addSeparator()
        attach_action = menu.addAction("\U0001F4C1 Attach file\u2026")
        attach_action.triggered.connect(self._attach_file)

        menu.exec(chip_btn.mapToGlobal(chip_btn.rect().bottomLeft()))

    def _attach_file(self):
        """Open a file dialog to attach a file to the chat context."""
        from PyQt6.QtWidgets import QFileDialog
        path, _ = QFileDialog.getOpenFileName(
            self, "Attach file",
            "",
            "All supported (*.txt *.md *.csv *.json *.xml *.html *.pdf *.docx *.xlsx *.tmx *.sdlxliff *.xliff);;"
            "Text files (*.txt *.md *.csv *.json *.xml *.html);;"
            "Documents (*.pdf *.docx *.xlsx);;"
            "Translation files (*.tmx *.sdlxliff *.xliff);;"
            "All files (*)"
        )
        if not path:
            return

        import os
        try:
            name = os.path.basename(path)
            size = os.path.getsize(path)

            # Read content (text files only for now)
            content = ""
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    content = f.read()[:50000]  # First 50K chars
            except (UnicodeDecodeError, Exception):
                content = f"[Binary file: {name}, {size:,} bytes]"

            file_data = {
                'name': name,
                'path': path,
                'size': size,
                'content': content,
                'type': os.path.splitext(name)[1].lower(),
            }

            pm = self._get_prompt_manager()
            if pm:
                pm.attached_files.append(file_data)

            # Enable the files chip
            self._context_toggles["files"].setChecked(True)

            self._backend.add_message("system", f"\U0001F4CE Attached: {name} ({size:,} bytes)")

        except Exception as e:
            self._backend.add_message("system", f"\u26A0 Failed to attach file: {e}")

    def get_context_state(self) -> dict:
        """Return which context sources are enabled."""
        return {k: btn.isChecked() for k, btn in self._context_toggles.items()}

    # ------------------------------------------------------------------
    # Display helpers
    # ------------------------------------------------------------------

    def _add_display_item(self, msg: dict) -> QListWidgetItem:
        item = QListWidgetItem()
        item.setData(Qt.ItemDataRole.UserRole, msg)
        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsSelectable)
        self._chat_display.addItem(item)

        # Force delegate to compute correct size
        delegate = self._chat_display.itemDelegate()
        from PyQt6.QtWidgets import QStyleOptionViewItem
        option = QStyleOptionViewItem()
        option.rect = self._chat_display.rect()
        size = delegate.sizeHint(option, self._chat_display.indexFromItem(item))
        item.setSizeHint(size)

        return item

    def _refresh_model_label(self):
        self._model_btn.setText(self._backend.get_model_display_name())

    def _show_model_menu(self):
        """Show a provider/model selection menu (like Supervertaler for Trados)."""
        from modules.llm_clients import LLMClient

        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu { font-size: 9pt; }
            QMenu::item:checked { font-weight: bold; }
        """)

        current_provider = ""
        current_model = ""
        if self._backend.llm_client:
            current_provider = self._backend.llm_client.provider
            current_model = self._backend.llm_client.model

        # Provider → models mapping
        providers = [
            ("claude", "Anthropic", LLMClient.CLAUDE_MODELS),
            ("openai", "OpenAI", LLMClient.OPENAI_MODELS),
            ("gemini", "Google", {
                "gemini-3.1-flash-lite": {"name": "Gemini 3.1 Flash-Lite", "description": "Fast & economical"},
                "gemini-2.5-pro": {"name": "Gemini 2.5 Pro", "description": "High quality"},
                "gemini-3.1-pro-preview": {"name": "Gemini 3.1 Pro", "description": "The Pro before Gemini 4 Argon"},
                "gemini-4-argon": {"name": "Gemini 4 Argon", "description": "Google's most capable, 1M context"},
                "gemma-4-26b-a4b-it": {"name": "Gemma 4 26B MoE", "description": "Open model, lightweight"},
            }),
            ("mistral", "Mistral", LLMClient.MISTRAL_MODELS),
            ("ollama", "Ollama (local)", LLMClient.OLLAMA_MODELS),
            ("openrouter", "OpenRouter", LLMClient.OPENROUTER_MODELS),
        ]

        for provider_key, provider_name, models in providers:
            if not models:
                continue

            provider_menu = menu.addMenu(provider_name)
            if provider_key == current_provider:
                provider_menu.setTitle(f"\u2713 {provider_name}")
                font = provider_menu.menuAction().font()
                font.setBold(True)
                provider_menu.menuAction().setFont(font)

            for model_id, model_info in models.items():
                name = model_info.get('name', model_id)
                desc = model_info.get('description', '')
                action = provider_menu.addAction(name)
                action.setToolTip(desc)
                action.setCheckable(True)
                if provider_key == current_provider and model_id == current_model:
                    action.setChecked(True)
                action.setData((provider_key, model_id))
                action.triggered.connect(
                    lambda checked, a=action: self._on_model_selected(a)
                )

        # Show menu above the button
        pos = self._model_btn.mapToGlobal(self._model_btn.rect().topLeft())
        pos.setY(pos.y() - menu.sizeHint().height())
        menu.exec(pos)

    def _on_model_selected(self, action):
        """Handle model selection from the menu."""
        data = action.data()
        if not data or len(data) != 2:
            return
        provider_key, model_id = data

        # Update the backend's LLM client with the new provider/model
        # We need to get API keys and create a new client
        try:
            from modules.llm_clients import LLMClient, load_api_keys

            parent_app = self._backend._parent_app
            if hasattr(parent_app, 'load_api_keys'):
                api_keys = parent_app.load_api_keys()
            else:
                api_keys = load_api_keys()

            # Determine API key
            if provider_key == 'ollama':
                api_key = 'not-needed'
            else:
                key_name = 'google' if provider_key == 'gemini' else provider_key
                api_key = api_keys.get(key_name, '')

            if not api_key and provider_key not in ('ollama', 'custom_openai'):
                self._backend.add_message(
                    "system",
                    f"\u26A0 No API key found for {provider_key}. Configure it in Settings."
                )
                return

            factory = getattr(parent_app, 'create_llm_client', None)
            if provider_key == 'custom_openai' and factory is not None:
                # Custom endpoints need base_url plus the active profile's model
                # and key, which only the app factory resolves. The manual path
                # below never set base_url for custom_openai, so the picker built
                # a broken client (chat showed "No model" and could not send),
                # even though QuickTrans – which uses this factory – worked.
                settings = (parent_app.load_llm_settings()
                            if hasattr(parent_app, 'load_llm_settings') else None)
                self._backend.llm_client = factory(
                    provider_key, model_id, api_keys, settings=settings)
            else:
                # Get proxy and base_url
                http_proxy = None
                if provider_key != 'gemini' and hasattr(parent_app, '_get_proxy_url'):
                    http_proxy = parent_app._get_proxy_url()

                base_url = None
                if provider_key == 'mistral':
                    base_url = 'https://api.mistral.ai/v1'
                elif provider_key == 'openrouter':
                    base_url = 'https://openrouter.ai/api/v1'

                self._backend.llm_client = LLMClient(
                    api_key=api_key,
                    provider=provider_key,
                    model=model_id,
                    max_tokens=16384,
                    base_url=base_url,
                    http_proxy=http_proxy,
                )

            self._refresh_model_label()
            resolved_model = getattr(self._backend.llm_client, 'model', model_id) or model_id
            self._backend.add_message(
                "system",
                f"Switched to {provider_key} / {resolved_model}",
            )

        except Exception as e:
            self._backend.add_message("system", f"\u26A0 Failed to switch model: {e}")

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _send_message(self):
        text = self._chat_input.toPlainText().strip()
        if not text and not self._pending_images:
            return

        if not self._backend.llm_client:
            self._backend.add_message(
                "system",
                "\u26A0 AI Assistant not available. Please configure API keys in Settings."
            )
            return

        # Add user message (mention images if attached)
        display_text = text or ""
        if self._pending_images:
            display_text += f" [\U0001F5BC {len(self._pending_images)} image(s)]"
        self._backend.add_message("user", display_text.strip())
        self._chat_input.clear()
        self._refresh_model_label()

        # Grab images before clearing
        images = list(self._pending_images)
        self._clear_pending_images()

        # This will be overridden by UnifiedPromptManagerQt to inject context
        self._do_send(text, images=images)

    def _do_send(self, user_text: str, images=None):
        """
        Default send: call backend directly with minimal context.
        UnifiedPromptManagerQt replaces this with context-aware sending.
        """
        prompt = user_text or "Describe this image."
        system_prompt = "You are an AI assistant for Supervertaler, a professional translation tool."

        # Trados-aware mode: prepend the active Trados project context to
        # the system prompt when the chip is on AND the bridge is reachable.
        # Network failures here are silently ignored – the user gets a
        # plain answer instead of being blocked.
        trados_block = self._fetch_trados_context_for_prompt()
        if trados_block:
            system_prompt = trados_block + "\n" + system_prompt

        try:
            response, metadata = self._backend.send_ai_request(
                prompt, system_prompt, images=images or None,
            )
            if response and response.strip():
                self._backend.add_message("assistant", response, metadata=metadata)
            else:
                self._backend.add_message(
                    "system", "\u26A0 Received empty response from AI."
                )
        except Exception as e:
            self._backend.add_message(
                "system",
                f"\u26A0 Error communicating with AI: {e}\n\nCheck the log for details.",
            )

    def _fetch_trados_context_for_prompt(self) -> str:
        """
        If the Trados chip pref is 'auto' AND the bridge is reachable,
        fetch the active Trados project context and format it as a
        prompt-ready text block. Returns "" otherwise so callers can
        unconditionally prepend the result.

        Called on every chat send. The fetch is fast (~30 ms on localhost)
        and any failure degrades to "" – the user always gets an answer;
        they just lose the Trados grounding for that message.
        """
        if self._get_trados_chip_pref() == "off":
            return ""
        if not self._trados_bridge_available:
            return ""
        try:
            ctx = self._trados_bridge.fetch_active_context()
        except Exception:
            return ""
        if not ctx:
            return ""
        try:
            return format_context_for_prompt(ctx)
        except Exception:
            return ""

    def _clear_chat(self):
        from PyQt6.QtWidgets import QMessageBox

        reply = QMessageBox.question(
            self,
            "Clear Chat",
            "Are you sure you want to clear the chat history?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._backend.clear_history()

    # ------------------------------------------------------------------
    # Context menu
    # ------------------------------------------------------------------

    def _show_context_menu(self, pos):
        item = self._chat_display.itemAt(pos)
        if not item:
            return

        msg_data = item.data(Qt.ItemDataRole.UserRole)
        if not msg_data:
            return

        menu = QMenu(self)
        copy_action = menu.addAction("\U0001F4CB Copy whole message")
        select_action = menu.addAction("\U0001F5B1 Select / copy text…")
        action = menu.exec(self._chat_display.mapToGlobal(pos))

        if action == copy_action:
            clipboard = QApplication.clipboard()
            clipboard.setText(msg_data.get('content', ''))
        elif action == select_action:
            self._show_selectable_text(msg_data)

    def _open_message_viewer(self, item):
        """Open the selectable-text viewer for a double-clicked list item."""
        if not item:
            return
        msg_data = item.data(Qt.ItemDataRole.UserRole)
        if msg_data:
            self._show_selectable_text(msg_data)

    def _show_selectable_text(self, msg_data: dict):
        """Show a message's text in a read-only, selectable, copyable viewer.

        The chat bubbles are custom-painted, so their text can't be selected
        in place. This lightweight viewer lets the user select any portion
        and copy it (Ctrl+C), or copy the whole message with one button.
        """
        from PyQt6.QtWidgets import (
            QDialog, QTextEdit, QDialogButtonBox, QPushButton,
        )

        content = msg_data.get('content', '') or ''
        role = msg_data.get('role', 'system')
        title = {
            'user': 'Your message',
            'assistant': 'Supervertaler Sidekick',
        }.get(role, 'System message')

        dlg = QDialog(self)
        dlg.setWindowTitle(f"{title} – select & copy")
        dlg.setMinimumSize(480, 320)

        dlg_layout = QVBoxLayout(dlg)

        viewer = QTextEdit()
        viewer.setReadOnly(True)
        viewer.setPlainText(content)
        viewer.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.TextSelectableByKeyboard
        )
        viewer.setStyleSheet(
            "QTextEdit { background-color: #FFFFFF; color: #1a1a1a; "
            "border: 1px solid #E0E0E0; border-radius: 4px; "
            "font-size: 10pt; padding: 6px; }"
        )
        # Pre-select everything so a plain Ctrl+C copies the full message;
        # the user can still drag to narrow the selection to a portion.
        viewer.selectAll()
        viewer.setFocus()
        dlg_layout.addWidget(viewer, 1)

        buttons = QDialogButtonBox()
        copy_all_btn = QPushButton("\U0001F4CB Copy all")
        copy_all_btn.clicked.connect(
            lambda: QApplication.clipboard().setText(content)
        )
        buttons.addButton(copy_all_btn, QDialogButtonBox.ButtonRole.ActionRole)
        close_btn = buttons.addButton(QDialogButtonBox.StandardButton.Close)
        close_btn.setDefault(True)
        buttons.rejected.connect(dlg.reject)
        close_btn.clicked.connect(dlg.reject)
        dlg_layout.addWidget(buttons)

        dlg.exec()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def insert_text(self, text: str):
        """Insert text into the input field."""
        if text and text.strip():
            current = self._chat_input.toPlainText()
            if current.strip():
                self._chat_input.setPlainText(current + "\n" + text.strip())
            else:
                self._chat_input.setPlainText(text.strip())
            cursor = self._chat_input.textCursor()
            cursor.movePosition(cursor.MoveOperation.End)
            self._chat_input.setTextCursor(cursor)

    def focus_input(self):
        """Focus the input text field."""
        self._chat_input.setFocus()
        cursor = self._chat_input.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        self._chat_input.setTextCursor(cursor)

    def get_input_text(self) -> str:
        return self._chat_input.toPlainText().strip()

    # ------------------------------------------------------------------
    # Event filter (keyboard handling)
    # ------------------------------------------------------------------

    def _maybe_show_copy_hint(self, event):
        """Show a one-shot tooltip when the user tries to drag-select chat text.

        The chat bubbles are painted rather than real text widgets, so a
        drag selects nothing. Rather than leave that as a silent dead end,
        we point the user at the right-click copy menu.
        """
        et = event.type()
        if et == event.Type.MouseButtonPress:
            if event.button() == Qt.MouseButton.LeftButton:
                self._drag_start = event.position().toPoint()
        elif et == event.Type.MouseMove:
            if (self._drag_start is not None
                    and event.buttons() & Qt.MouseButton.LeftButton):
                moved = (event.position().toPoint() - self._drag_start).manhattanLength()
                if moved > 12:
                    QToolTip.showText(
                        event.globalPosition().toPoint(),
                        "Text selection isn't available here yet — right-click "
                        "a message to copy it, or double-click to open a "
                        "selectable view.",
                        self._chat_display,
                    )
                    self._drag_start = None  # show once per drag gesture
        elif et == event.Type.MouseButtonRelease:
            self._drag_start = None

    def eventFilter(self, obj, event):
        # Copy-hint on the chat viewport (see _maybe_show_copy_hint). Never
        # consumes the event, so right-click menu / double-click still work.
        if obj is self._chat_display.viewport():
            self._maybe_show_copy_hint(event)
            return False
        if obj is self._chat_input and event.type() == event.Type.KeyPress:
            key = event.key()
            modifiers = event.modifiers()

            if key == Qt.Key.Key_Escape:
                self.escape_pressed.emit()
                return False  # Don't consume – let parent window handle it too

            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                if modifiers & Qt.KeyboardModifier.ShiftModifier:
                    return False  # Allow newline
                self._send_message()
                return True

            # Ctrl+V with image on clipboard
            if key == Qt.Key.Key_V and modifiers & Qt.KeyboardModifier.ControlModifier:
                if self._try_paste_image():
                    return True
                # Fall through to normal text paste

        return super().eventFilter(obj, event)

    # ------------------------------------------------------------------
    # Image attachments
    # ------------------------------------------------------------------

    def _try_paste_image(self) -> bool:
        """Check clipboard for an image and add it if found. Returns True if handled."""
        try:
            clipboard = QApplication.clipboard()
            mime = clipboard.mimeData()
            if not mime:
                return False

            # Try QImage first
            if mime.hasImage():
                image = QImage(mime.imageData())
                if not image.isNull():
                    self._add_pasted_image(image)
                    return True

            # Try image formats in mime data
            for fmt in ['image/png', 'image/jpeg', 'image/bmp']:
                if mime.hasFormat(fmt):
                    data = mime.data(fmt)
                    if data and len(data) > 0:
                        image = QImage()
                        image.loadFromData(data)
                        if not image.isNull():
                            self._add_pasted_image(image)
                            return True

        except Exception as e:
            print(f"[ChatView] Image paste error: {e}")
        return False

    def _add_pasted_image(self, image: QImage):
        """Convert a QImage to base64 PNG and add to pending images."""
        from PyQt6.QtCore import QBuffer, QByteArray, QIODevice

        byte_array = QByteArray()
        qbuffer = QBuffer(byte_array)
        qbuffer.open(QIODevice.OpenModeFlag.WriteOnly)
        image.save(qbuffer, "PNG")
        qbuffer.close()

        img_base64 = base64.b64encode(bytes(byte_array)).decode('ascii')
        label = f"image_{len(self._pending_images) + 1}"
        self._pending_images.append((label, img_base64))

        self._refresh_image_strip()

        self._backend.add_message(
            "system",
            f"\U0001F5BC Image pasted ({image.width()}\u00D7{image.height()}px)",
            save=False,
        )

    def _refresh_image_strip(self):
        """Update the image strip to show pending image thumbnails."""
        # Clear existing
        while self._image_strip.count():
            item = self._image_strip.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if not self._pending_images:
            self._image_strip_widget.hide()
            return

        for i, (label, img_b64) in enumerate(self._pending_images):
            # Thumbnail
            thumb_label = QLabel()
            img_data = base64.b64decode(img_b64)
            qimg = QImage()
            qimg.loadFromData(img_data)
            from PyQt6.QtGui import QPixmap
            pixmap = QPixmap.fromImage(qimg).scaled(
                48, 48, Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation
            )
            thumb_label.setPixmap(pixmap)
            thumb_label.setStyleSheet("border: 1px solid #ccc; border-radius: 4px; padding: 1px;")
            self._image_strip.addWidget(thumb_label)

            # Remove button
            remove_btn = QPushButton("\u00D7")
            remove_btn.setFixedSize(16, 16)
            remove_btn.setStyleSheet("""
                QPushButton {
                    background: #e74c3c; color: white; border: none;
                    border-radius: 8px; font-size: 9pt; font-weight: bold;
                }
                QPushButton:hover { background: #c0392b; }
            """)
            remove_btn.clicked.connect(lambda checked, idx=i: self._remove_pending_image(idx))
            self._image_strip.addWidget(remove_btn)

        self._image_strip.addStretch()
        self._image_strip_widget.show()

    def _remove_pending_image(self, idx: int):
        """Remove a pending image by index."""
        if 0 <= idx < len(self._pending_images):
            self._pending_images.pop(idx)
            self._refresh_image_strip()

    def _clear_pending_images(self):
        """Clear all pending images."""
        self._pending_images.clear()
        self._refresh_image_strip()
