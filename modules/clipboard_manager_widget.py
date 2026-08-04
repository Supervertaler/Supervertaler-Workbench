"""
Clipboard Manager Widget for Supervertaler Workbench.

Monitors the system clipboard and maintains a persistent history of TEXT and
RASTER IMAGE clips that survives application restarts.  Items change colour
after being pasted, making it easy to track which clips have already been
used in a session.

Originally lived inside Supervertaler Sidekick (retired in v1.10.4); now
mounted as the "📋 Clipboard" top tab on Workbench itself.

Cross-platform: relies on QApplication.clipboard().dataChanged (Qt handles
the OS-level plumbing on Windows, macOS, and Linux/X11).
"""

import re
import hashlib

from pathlib import Path

from PyQt6.QtCore import Qt, QEvent, QSize, QBuffer, QIODevice, QTimer
from PyQt6.QtGui import QColor, QPixmap, QImage, QIcon, QBrush, QFont
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QListWidget, QListWidgetItem, QListView, QAbstractItemView, QApplication,
    QSplitter, QStackedLayout, QMenu, QTreeWidget, QTreeWidgetItem,
    QDialog, QDialogButtonBox, QLineEdit, QPlainTextEdit, QMessageBox,
)

from modules.styled_widgets import HelpButton
from modules.help_system import Topics as HelpTopics
from modules.ui_scale import scaled_pt


# ---------- Item data roles ------------------------------------------------
# Stored on each QListWidgetItem.  UserRole numbering is shifted up to leave
# room for future additions without breaking older indices.

_ROLE_DB_ID    = Qt.ItemDataRole.UserRole          # int row id, or None
_ROLE_KIND     = Qt.ItemDataRole.UserRole + 1      # 'text' or 'image'
_ROLE_TEXT     = Qt.ItemDataRole.UserRole + 2      # full text (text clips only)
_ROLE_IMG      = Qt.ItemDataRole.UserRole + 3      # PNG bytes (image clips only)
_ROLE_PASTED   = Qt.ItemDataRole.UserRole + 4      # bool

# Action-tree (3rd column) items: identifies snippet nodes for the tree's
# right-click menu. UserRole itself already carries the callback index /
# category sentinel there, so this rides on UserRole + 1.
# Value: ('category', <category name>, '') on snippet category rows, or
#        ('snippet', <category name>, <str path to .md file>) on leaves.
_ROLE_TREE_SNIPPET = Qt.ItemDataRole.UserRole + 1


class SnippetEditDialog(QDialog):
    """Small label + body editor used by the Clipboard Manager's
    right-click "save as snippet" / "new snippet" / "edit snippet"
    actions (v1.10.349). The label becomes the .md filename (and thus
    the entry shown in the Menu column); the body is the exact text
    the snippet inserts."""

    def __init__(self, parent=None, *, title: str,
                 label_text: str = "", body_text: str = ""):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumSize(460, 300)
        self.result_label = ""
        self.result_body = ""

        layout = QVBoxLayout(self)

        lbl1 = QLabel("Label (shown in the Menu column, becomes the filename):")
        layout.addWidget(lbl1)
        self._label_edit = QLineEdit(label_text)
        layout.addWidget(self._label_edit)

        lbl2 = QLabel("Text to insert:")
        layout.addWidget(lbl2)
        self._body_edit = QPlainTextEdit(body_text)
        layout.addWidget(self._body_edit, 1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        # Land the user in whichever field still needs input.
        if label_text:
            self._body_edit.setFocus()
        else:
            self._label_edit.setFocus()

    def _on_accept(self):
        label = self._label_edit.text().strip()
        body = self._body_edit.toPlainText()
        if not label:
            QMessageBox.warning(self, "Missing label",
                                "Please enter a label for the snippet.")
            return
        if not body.strip():
            QMessageBox.warning(self, "Missing text",
                                "Please enter the text the snippet should "
                                "insert.")
            return
        self.result_label = label
        self.result_body = body
        self.accept()


class ClipboardManagerWidget(QWidget):
    """
    Persistent clipboard history panel, mounted as Workbench's
    "📋 Clipboard" top tab.

    Two callbacks are wired by the parent (Workbench):

      paste_text_callback(text: str)
          Called when the user clicks a TEXT item. The callback is expected
          to put the text on the clipboard and (when invoked via the
          Ctrl+Alt+C hotkey) send Ctrl+V back to the source window.

      paste_image_callback(pixmap: QPixmap)
          Called when the user clicks an IMAGE item. Same contract but for
          a raster image.

    Pasted state is persisted to the shared SQLite database so it survives
    restarts. The db is accessed lazily via ``ensure_db_loaded()``, which
    Workbench calls once the database is ready.
    """

    # Independent caps per kind so a flood of images can't push your text
    # history out, and vice versa.
    MAX_TEXT_ITEMS  = 200
    MAX_IMAGE_ITEMS = 50

    _COLOUR_NORMAL = QColor("#1E1E1E")
    _COLOUR_PASTED = QColor("#AAAAAA")
    _BG_PASTED     = QColor("#F8F8F8")
    _BG_NORMAL     = QColor(Qt.GlobalColor.white)

    _THUMB_SIZE = QSize(48, 48)   # icon size shown in the list

    # Privacy defaults (issue #246). Capture stays ON so existing installs
    # behave exactly as before; everything else is opt-in.
    DEFAULT_PRIVACY = {
        'capture_enabled':      True,
        'auto_delete_enabled':  False,
        'auto_delete_minutes':  60,
        'excluded_apps':        [],     # process names, e.g. "keepass.exe"
    }

    # Offered by the Settings page's one-click button. Not a default: a user
    # who copies a URL out of KeePass and finds it missing from the history
    # would rightly call that broken, so the exclusion list starts empty and
    # the user opts in with full knowledge of what it does.
    COMMON_SECRET_APPS = [
        "keepass.exe", "keepassxc.exe", "1password.exe", "bitwarden.exe",
        "dashlane.exe", "lastpass.exe", "nordpass.exe", "protonpass.exe",
        "roboform.exe", "enpass.exe", "keeper.exe", "passwordsafe.exe",
    ]

    def __init__(self, parent_app, paste_text_callback=None,
                 paste_image_callback=None, parent=None):
        super().__init__(parent)
        self._parent_app           = parent_app
        self._paste_text_callback  = paste_text_callback
        self._paste_image_callback = paste_image_callback
        self._suppress_next        = False   # True while we set the clipboard ourselves
        self._db_loaded            = False
        self._last_image_hash      = None    # for dedup of identical re-copies
        # Text of the clip currently being pasted back, kept so the
        # paste-back step can *type* it (AHK SendText) into targets that
        # ignore a synthetic Ctrl+V. None while an image clip is in
        # flight (images can only be delivered via Ctrl+V).
        self._pending_paste_text   = None
        # One-shot override set by the "Paste by typing" context-menu
        # action: forces the typing path for the very next paste-back
        # regardless of the persisted paste-method setting.
        self._force_typing_once    = False
        # Source-window handle captured when the user arrived via a
        # global hotkey (Ctrl+Alt+C). Snippet / conversion activations
        # use this to paste-and-return: clipboard set → Workbench
        # hidden → source window refocused → Ctrl+V sent. ``None``
        # means "no source" – the user navigated to this tab manually,
        # so we just set the clipboard and stay in Workbench.
        self._source_window = None

        # Privacy controls (issue #246). Loaded before the UI is built so the
        # header can show the paused state immediately on startup.
        self._privacy = dict(self.DEFAULT_PRIVACY)
        self._load_privacy_settings()

        self._init_ui()
        self._start_monitoring()
        self._update_capture_state_ui()

        # Auto-delete sweeper. Created unconditionally, started only when the
        # feature is on (see _apply_auto_delete_timer).
        self._purge_timer = QTimer(self)
        self._purge_timer.setInterval(60_000)      # one sweep per minute
        self._purge_timer.timeout.connect(self._purge_expired_items)
        self._apply_auto_delete_timer()

        # v1.10.16: light up the column header whose widget currently
        # holds keyboard focus, so users navigating between the three
        # columns with ← / → arrow keys can tell at a glance which
        # column they're in. Connected via Qt::UniqueConnection-style
        # singleton (we only ever construct one of these widgets, so
        # the connection lives for the widget's lifetime; Qt
        # disconnects it automatically when self is destroyed).
        try:
            QApplication.instance().focusChanged.connect(self._refresh_focus_styles)
        except Exception as e:
            print(f"[ClipboardManagerWidget] focusChanged hook failed: {e}")

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    # Property (not class constant) so it picks up the current global UI
    # font scale at the moment the clipboard widget is constructed.
    @property
    def _LIST_STYLESHEET(self) -> str:
        return f"""
            QListWidget {{
                border: 1px solid #E0E0E0; border-radius: 4px;
                background: white; font-size: {scaled_pt(9):.1f}pt; outline: none;
            }}
            QListWidget::item {{
                padding: 5px 8px; border-bottom: 1px solid #E4E4E4;
            }}
            QListWidget::item:selected {{
                background-color: #E8F4FD; color: #1E1E1E;
            }}
            QListWidget::item:hover {{
                background-color: #F5F9FF;
            }}
        """

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        # Header: title + Clear button
        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        self._count_label = QLabel("Clipboard History")
        self._count_label.setStyleSheet(
            f"font-weight: bold; font-size: {scaled_pt(9):.1f}pt; color: #3D5A80; border: none;"
        )
        header.addWidget(self._count_label)

        # Paused indicator (issue #246) – hidden unless capture is off.
        self._capture_state_label = QLabel("")
        self._capture_state_label.setStyleSheet(
            f"color: #C62828; font-size: {scaled_pt(8):.1f}pt; "
            f"font-weight: bold; border: none; padding-left: 8px;"
        )
        self._capture_state_label.hide()
        header.addWidget(self._capture_state_label)

        header.addStretch()

        clear_btn = QPushButton("Clear all")
        clear_btn.setStyleSheet(f"""
            QPushButton {{
                color: #888; background: transparent;
                border: 1px solid #DDD; border-radius: 3px;
                padding: 2px 8px; font-size: {scaled_pt(8):.1f}pt;
            }}
            QPushButton:hover {{
                background-color: #FFF0F0;
                border-color: #E57373; color: #C62828;
            }}
        """)
        clear_btn.setToolTip("Remove all clipboard history (cannot be undone)")
        clear_btn.clicked.connect(self._clear_all)
        header.addWidget(clear_btn)
        header.addWidget(HelpButton(HelpTopics.CLIPBOARD,
                                    tooltip="Open Clipboard help"))
        layout.addLayout(header)

        # Three-column split (v1.10.2, Phase 3 of issue #199):
        #   1. Text clipboard history (left)
        #   2. Image clipboard history (middle)
        #   3. Menu – Snippets / Special Characters / Text Conversions /
        #      QuickLauncher Prompts (right) – previously Sidekick's
        #      right-pane action tree, now folded in as a third column
        #      so the Workbench Clipboard tab matches the keyboard-
        #      navigable feel users had in Sidekick.
        # QSplitter so users can rebalance to taste.
        self._splitter = QSplitter(Qt.Orientation.Horizontal)
        self._splitter.setHandleWidth(4)
        self._splitter.setChildrenCollapsible(False)

        self._text_list = self._make_list_widget(row_height_hint=24)
        self._text_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._text_list.customContextMenuRequested.connect(
            lambda pos: self._on_context_menu(pos, self._text_list))
        self._text_empty = self._make_empty_label(
            "No text yet –\ncopy any text to start.")
        # v1.10.16: header was "📝 Text snippets" – renamed to plain
        # "📝 Text" because "snippets" overlapped with the "Personal
        # Snippets" entry in the 3rd-column action menu, which
        # confused users about which one held the clipboard history.
        self._text_header = QLabel("📝 Text")
        text_col = self._make_column(
            self._text_header, self._text_list, self._text_empty)
        self._splitter.addWidget(text_col)

        self._image_list = self._make_list_widget(
            row_height_hint=self._THUMB_SIZE.height() + 10)
        self._image_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._image_list.customContextMenuRequested.connect(
            lambda pos: self._on_context_menu(pos, self._image_list))
        # v1.10.351: keyboard image preview. While the Images column has
        # focus, pausing on an item for ~350 ms pops up a decent-sized
        # preview next to the list (48 px thumbnails are too small to
        # tell near-identical screenshots apart). The popup is a ToolTip
        # window – never takes focus, so arrow-key navigation continues
        # uninterrupted. Any further navigation hides it and re-arms the
        # pause timer; focus loss / paste / dismissal hide it for good.
        self._preview_popup = None
        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(350)
        self._preview_timer.timeout.connect(self._show_image_preview)
        self._image_list.currentItemChanged.connect(
            self._on_image_current_changed)
        self._image_empty = self._make_empty_label(
            "No images yet –\ncopy any image to start.")
        self._image_header = QLabel("🖼 Images")
        image_col = self._make_column(
            self._image_header, self._image_list, self._image_empty)
        self._splitter.addWidget(image_col)

        # Third column: action menu (snippets, characters, conversions,
        # prompts). Built lazily by _build_action_tree(); always added
        # to the splitter so the layout reserves space even if the
        # tree's content takes a moment to populate.
        self._action_items = []  # Callbacks, indexed by QTreeWidgetItem UserRole
        action_col_container = QWidget()
        action_col_layout = QVBoxLayout(action_col_container)
        action_col_layout.setContentsMargins(0, 0, 0, 0)
        action_col_layout.setSpacing(4)
        self._action_header = QLabel("📑 Menu")
        # v1.10.16: use the shared _COL_HEADER_INACTIVE/_ACTIVE
        # styles so all three column headers light up consistently
        # when the user navigates between columns with the arrow
        # keys. Previously this header had its own one-off style and
        # never picked up the focus highlight, so users couldn't tell
        # at a glance when they were in the Menu column.
        self._action_header.setStyleSheet(self._COL_HEADER_INACTIVE)
        # Menu header row with a Refresh button on the right.  The
        # snippet library and QuickLauncher prompts are file-backed
        # (.md files under <user_data>/snippet_library/ and the shared
        # prompt_library folder respectively); when the user edits
        # those on disk the changes don't auto-propagate.  Refresh
        # rebuilds the entire action tree from disk in one shot.
        action_header_row = QHBoxLayout()
        action_header_row.setContentsMargins(0, 0, 0, 0)
        action_header_row.setSpacing(4)
        action_header_row.addWidget(self._action_header, 1)
        action_refresh_btn = QPushButton("🔄 Refresh")
        action_refresh_btn.setToolTip(
            "Reload the Menu lists from disk.\n\n"
            "Use this after editing any snippet (.md file under "
            "snippet_library/) or QuickLauncher prompt outside Workbench."
        )
        action_refresh_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        action_refresh_btn.setStyleSheet(
            f"QPushButton {{ font-size: {scaled_pt(7):.1f}pt; padding: 2px 8px; "
            "color: #555; border: 1px solid #ccc; border-radius: 3px; background: #f5f5f5; }}"
            "QPushButton:hover { background: #e8e8e8; color: #000; }"
        )
        action_refresh_btn.clicked.connect(self._populate_action_tree)
        action_header_row.addWidget(action_refresh_btn, 0)
        action_col_layout.addLayout(action_header_row)
        self._action_tree = self._build_action_tree()
        action_col_layout.addWidget(self._action_tree, 1)
        self._splitter.addWidget(action_col_container)

        self._splitter.setStretchFactor(0, 5)
        self._splitter.setStretchFactor(1, 3)
        self._splitter.setStretchFactor(2, 4)
        self._splitter.setSizes([500, 300, 400])

        layout.addWidget(self._splitter, 1)

        # Reflect counts in the per-column headers.
        self._update_column_headers()

        # Footer hint
        hint = QLabel(
            "Click to paste  •  Right-click a clip to delete it or save it "
            "as a snippet  •  Right-click the Menu column to add / edit "
            "snippets  •  Pasted items shown in grey  •  ← / → switches "
            "columns  •  Up / Down navigates within a column")
        hint.setStyleSheet(
            f"color: #999; font-size: {scaled_pt(7):.1f}pt; padding: 2px 4px; border: none;"
        )
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(hint)

    # -- column / list construction helpers -----------------------------

    def _make_list_widget(self, row_height_hint: int = 24):
        lst = QListWidget()
        lst.setStyleSheet(self._LIST_STYLESHEET)
        lst.setIconSize(self._THUMB_SIZE)
        lst.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        # ScrollPerPixel + a singleStep tuned to one row height. ScrollPerItem
        # made mouse-wheel and precision-trackpad scrolling feel "stuck" on
        # Windows because sub-row deltas were dropped rather than accumulated.
        # ScrollPerPixel handles those deltas smoothly. Arrow-key navigation
        # still feels row-wise because Qt's scroll-into-view on QListWidget
        # isn't animated – it scrolls just enough pixels to make the new
        # current row visible, in one step.
        lst.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        lst.verticalScrollBar().setSingleStep(row_height_hint)
        lst.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        lst.setWordWrap(False)
        # Tell Qt every row has the same height (single-line text items, or
        # all 48x48-icon image items). Lets Qt cache item geometry and skip
        # measure-each-row passes during paint and scroll. Safe because the
        # widget enforces setWordWrap(False) above and image-list items are
        # all sized to the icon dimensions.
        lst.setUniformItemSizes(True)
        # Batched layout dramatically reduces work when the user holds an
        # arrow key – Qt processes layout in chunks instead of per-row.
        lst.setLayoutMode(QListView.LayoutMode.Batched)
        lst.setBatchSize(50)
        lst.itemActivated.connect(self._on_item_activated)
        lst.itemClicked.connect(self._on_item_activated)
        lst.installEventFilter(self)
        return lst

    @staticmethod
    def _make_empty_label(text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setWordWrap(True)
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl.setStyleSheet(
            f"color: #999; font-size: {scaled_pt(8):.1f}pt; padding: 20px;"
            " font-style: italic; background: white;"
            " border: 1px solid #E0E0E0; border-radius: 4px;"
        )
        return lbl

    # Properties (not class constants) so they pick up the current global UI
    # font scale at the moment the clipboard widget is constructed/refreshed.
    @property
    def _COL_HEADER_INACTIVE(self) -> str:
        return (
            f"font-weight: bold; font-size: {scaled_pt(8):.1f}pt; color: #555;"
            " padding: 2px 4px; border: none;"
        )

    @property
    def _COL_HEADER_ACTIVE(self) -> str:
        return (
            f"font-weight: bold; font-size: {scaled_pt(8):.1f}pt; color: #1976D2;"
            " padding: 2px 4px; border: none;"
            " border-bottom: 2px solid #1976D2;"
        )

    def _make_column(self, header_label: QLabel,
                      list_widget: QListWidget,
                      empty_label: QLabel) -> QWidget:
        container = QWidget()
        cl = QVBoxLayout(container)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(2)

        header_label.setStyleSheet(self._COL_HEADER_INACTIVE)
        cl.addWidget(header_label)

        # Stack so list and placeholder share the same area, and only
        # one is ever visible.
        stack_widget = QWidget()
        stack = QStackedLayout(stack_widget)
        stack.addWidget(list_widget)   # index 0
        stack.addWidget(empty_label)   # index 1
        list_widget._sv_stack = stack  # so _update_empty_state can switch
        cl.addWidget(stack_widget, 1)
        return container

    def _update_empty_state(self, list_widget: QListWidget):
        stack = getattr(list_widget, '_sv_stack', None)
        if stack is None:
            return
        stack.setCurrentIndex(0 if list_widget.count() > 0 else 1)

    # ------------------------------------------------------------------
    # Focus & keyboard handling
    # ------------------------------------------------------------------

    def showEvent(self, event):
        super().showEvent(event)
        # Default focus is the text list – that's where most clips live.
        self._text_list.setFocus()
        # Always highlight the latest entry (row 0) on show, not just when
        # there's no selection. The latest clip is what users overwhelmingly
        # want to paste when they open the manager – preserving a stale
        # selection from a previous session means an extra arrow-up keystroke
        # in the common case.
        if self._text_list.count() > 0:
            self._text_list.setCurrentRow(0)

    def hideEvent(self, event):
        # Tab switch / Esc dismissal / Workbench hide: the preview is a
        # top-level ToolTip window, so it would happily outlive us
        # on-screen unless hidden explicitly.
        self._hide_image_preview()
        super().hideEvent(event)

    def eventFilter(self, obj, event):
        # Image-preview lifecycle: entering the Images column arms the
        # pause timer for the already-current item; leaving it hides
        # any visible preview. getattr (not a bare attribute read):
        # this filter is installed on the TEXT list before _image_list
        # / _preview_timer exist during _init_ui, and construction-time
        # events (ParentChange etc.) arrive in that window – an
        # AttributeError inside an event-filter override is fatal in
        # PyQt6 (qFatal), it doesn't surface as a Python traceback.
        img_list = getattr(self, '_image_list', None)
        if img_list is not None and obj is img_list \
                and getattr(self, '_preview_timer', None) is not None:
            if event.type() == QEvent.Type.FocusIn:
                self._preview_timer.start()
            elif event.type() == QEvent.Type.FocusOut:
                self._hide_image_preview()
        if event.type() != QEvent.Type.KeyPress:
            return super().eventFilter(obj, event)
        if obj is self._text_list:
            return self._handle_list_key(self._text_list, event,
                                          right_neighbour=self._image_list,
                                          left_neighbour=None)
        if obj is self._image_list:
            # Right past the image list now lands on the local action
            # tree (the 3rd column), not Sidekick's right pane.
            return self._handle_list_key(self._image_list, event,
                                          right_neighbour=self._action_tree,
                                          left_neighbour=self._text_list)
        if obj is self._action_tree:
            # Tree-column key handling: defer to Qt's defaults for
            # expand-on-Right / collapse-on-Left when the current item
            # supports it. Only intercept the boundary cases:
            #   • Right on a leaf or already-expanded category → swallow
            #     (nothing to the right of the action tree)
            #   • Left on a leaf or already-collapsed category →
            #     move focus to the image list (one column left)
            # That way navigation still works *and* category nodes
            # expand/collapse with the arrow keys as in any QTreeWidget.
            if event.type() == QEvent.Type.KeyPress:
                current = self._action_tree.currentItem()
                if event.key() == Qt.Key.Key_Right:
                    if current is not None and current.childCount() > 0 \
                            and not current.isExpanded():
                        return False  # let Qt expand
                    return True  # swallow
                if event.key() == Qt.Key.Key_Left:
                    if current is not None and current.childCount() > 0 \
                            and current.isExpanded():
                        return False  # let Qt collapse
                    self._focus_list(self._image_list)
                    return True
        return super().eventFilter(obj, event)

    def _handle_list_key(self, list_widget, event, *,
                         right_neighbour, left_neighbour):
        """Custom key behaviour for either column.

        - Enter / Return  → paste the selected item
        - Right           → focus right_neighbour (other list, or right-pane
                            Menu if at the rightmost column)
        - Left            → focus left_neighbour (other list, or no-op at
                            the leftmost column)
        """
        key = event.key()
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            item = list_widget.currentItem()
            if item:
                self._on_item_activated(item)
            return True

        if key == Qt.Key.Key_Delete:
            item = list_widget.currentItem()
            if item:
                self._delete_item(item, list_widget)
            return True

        if key == Qt.Key.Key_Right:
            if right_neighbour is not None:
                # right_neighbour can be either a QListWidget (text /
                # image column) or the QTreeWidget (action menu). Both
                # accept setFocus(); _focus_list also auto-selects row
                # 0 for empty lists, which doesn't apply to the tree.
                if isinstance(right_neighbour, QTreeWidget):
                    self._focus_action_tree(right_neighbour)
                else:
                    self._focus_list(right_neighbour)
            # No right neighbour means we're already on the action
            # tree (rightmost column in the Workbench top-tab layout)
            # – nothing to do.
            return True

        if key == Qt.Key.Key_Left:
            if left_neighbour is not None:
                self._focus_list(left_neighbour)
                return True
            # Already on the leftmost column – Left is a no-op (per design).
            return False

        return False

    def _focus_action_tree(self, tree: QTreeWidget):
        """Give focus to the action tree, selecting the first leaf if
        nothing is currently selected."""
        tree.setFocus(Qt.FocusReason.OtherFocusReason)
        # Auto-select something so Up/Down can navigate from a defined
        # starting point. Prefer an existing selection; otherwise pick
        # the first activatable leaf.
        if tree.currentItem() is None:
            it = QTreeWidgetItem(tree)  # type-hint helper, replaced below
            it = None
            root = tree.invisibleRootItem()
            for i in range(root.childCount()):
                cat = root.child(i)
                if cat.childCount() > 0:
                    cat.setExpanded(True)
                    tree.setCurrentItem(cat.child(0))
                    return
                # No children – just highlight the category itself
                tree.setCurrentItem(cat)
                return

    def _focus_list(self, list_widget: QListWidget):
        list_widget.setFocus(Qt.FocusReason.OtherFocusReason)
        if list_widget.count() > 0 and list_widget.currentRow() < 0:
            list_widget.setCurrentRow(0)

    def _refresh_focus_styles(self, old=None, new=None):
        """Highlight the column header whose widget currently has
        focus. Connected to QApplication.focusChanged in __init__ so
        it fires automatically as the user arrow-keys between
        columns; ``old`` and ``new`` are the QApplication-supplied
        previous and new focus widgets (we only care about ``new``).

        Pre-v1.10.16 this only handled the two list columns and
        relied on Sidekick to call it manually. Sidekick's been gone
        since v1.10.4 and the action-tree (3rd column) was never
        wired up. v1.10.16 fixes both: connects to focusChanged so
        no external trigger is needed, and adds the _action_header
        to the rotation so all three columns light up consistently.
        """
        # Resolve the focused widget. The signal passes (old, new);
        # manual callers pass new as the first arg.
        if new is None and old is not None and not isinstance(old, QApplication):
            # Single-arg invocation: treat `old` as the new focus.
            focused = old
        else:
            focused = new

        # A focus event on a child of one of our lists / the tree
        # (e.g. an internal editor) should still light up the
        # parent column. Walk up the parent chain.
        active_text = active_image = active_action = False
        w = focused
        while w is not None:
            if w is self._text_list:
                active_text = True
                break
            if w is self._image_list:
                active_image = True
                break
            if w is self._action_tree:
                active_action = True
                break
            try:
                w = w.parent()
            except Exception:
                break

        self._text_header.setStyleSheet(
            self._COL_HEADER_ACTIVE if active_text else self._COL_HEADER_INACTIVE
        )
        self._image_header.setStyleSheet(
            self._COL_HEADER_ACTIVE if active_image else self._COL_HEADER_INACTIVE
        )
        self._action_header.setStyleSheet(
            self._COL_HEADER_ACTIVE if active_action else self._COL_HEADER_INACTIVE
        )

        # Focus left the Images column (any destination, including other
        # windows): retire the image preview.
        if not active_image:
            self._hide_image_preview()

    # ------------------------------------------------------------------
    # Clipboard monitoring
    # ------------------------------------------------------------------

    def _start_monitoring(self):
        QApplication.clipboard().dataChanged.connect(self._on_clipboard_changed)

    def _on_clipboard_changed(self):
        if self._suppress_next:
            self._suppress_next = False
            return

        # Privacy gate (issue #246). Deliberately the FIRST thing after the
        # self-copy guard: when capture is off or the foreground app is
        # excluded, the clip must never be read, hashed, displayed or written
        # to the database. Filtering later - after reading the text - would
        # still pull a password into this process's memory.
        if not self._privacy.get('capture_enabled', True):
            return
        if self._foreground_app_is_excluded():
            return

        clip = QApplication.clipboard()
        mime = clip.mimeData()

        # Prefer images: many "copy from screenshot tool" actions put both an
        # image and a text representation (e.g. file path) on the clipboard;
        # the image is the more specialised payload.
        if mime is not None and mime.hasImage():
            image = clip.image()
            if not image.isNull():
                self._handle_new_image(image)
                return

        text = clip.text()
        if text and text.strip():
            self._handle_new_text(text)

    def _handle_new_text(self, text: str):
        # Skip if identical to the most recent TEXT item (avoid duplicate on re-copy)
        top = self._top_text_item()
        if top is not None and top.data(_ROLE_TEXT) == text:
            return
        self._add_text_clip(text, item_id=None, pasted=False, save_to_db=True)

    def _handle_new_image(self, qimage: QImage):
        png_bytes = self._encode_png(qimage)
        if not png_bytes:
            return
        digest = hashlib.sha1(png_bytes).digest()

        # Skip if the most-recent image is byte-identical
        top = self._top_image_item()
        if top is not None and self._last_image_hash == digest:
            return
        self._last_image_hash = digest

        label = f"🖼 Image {qimage.width()}×{qimage.height()} ({self._fmt_size(len(png_bytes))})"
        self._add_image_clip(label, png_bytes, item_id=None,
                             pasted=False, save_to_db=True)

    # ------------------------------------------------------------------
    # Privacy controls (issue #246)
    # ------------------------------------------------------------------

    def _load_privacy_settings(self):
        """Pull the persisted privacy settings from the parent app, falling
        back to defaults. Never raises: a settings problem must not stop the
        clipboard tab from loading."""
        try:
            loader = getattr(self._parent_app, 'load_clipboard_privacy_settings', None)
            if callable(loader):
                stored = loader() or {}
                merged = dict(self.DEFAULT_PRIVACY)
                merged.update({k: v for k, v in stored.items()
                               if k in self.DEFAULT_PRIVACY})
                self._privacy = merged
        except Exception as e:
            print(f"[ClipboardManagerWidget] privacy settings load failed: {e}")

    def refresh_privacy_settings(self):
        """Re-read the settings and apply them live. Called by the Settings
        page so a change takes effect without restarting Workbench - the
        whole point of a privacy switch is that it acts NOW."""
        self._load_privacy_settings()
        self._apply_auto_delete_timer()
        self._update_capture_state_ui()
        if self._privacy.get('auto_delete_enabled'):
            # Apply the new window immediately rather than up to a minute
            # later, so shortening it visibly does something at once.
            self._purge_expired_items()

    def _apply_auto_delete_timer(self):
        timer = getattr(self, '_purge_timer', None)
        if timer is None:
            return
        if self._privacy.get('auto_delete_enabled'):
            if not timer.isActive():
                timer.start()
        elif timer.isActive():
            timer.stop()

    def _purge_expired_items(self):
        """Drop history older than the configured window, from both the
        database and the two lists."""
        try:
            minutes = int(self._privacy.get('auto_delete_minutes', 60) or 0)
        except (TypeError, ValueError):
            return
        if minutes <= 0:
            return

        db = self._get_db()
        if not db:
            return
        purge = getattr(db, 'purge_clipboard_items_older_than', None)
        if not callable(purge):
            return
        try:
            removed_ids = set(purge(minutes) or [])
        except Exception as e:
            print(f"[ClipboardManagerWidget] auto-delete failed: {e}")
            return
        if not removed_ids:
            return

        for list_widget in (self._text_list, self._image_list):
            for row in range(list_widget.count() - 1, -1, -1):
                item = list_widget.item(row)
                if item is not None and item.data(_ROLE_DB_ID) in removed_ids:
                    list_widget.takeItem(row)

        # An in-memory hash of an image that no longer exists would suppress
        # the next legitimate copy of that same image.
        self._last_image_hash = None
        self._update_empty_state(self._text_list)
        self._update_empty_state(self._image_list)
        self._update_count()

    def _foreground_app_is_excluded(self) -> bool:
        """True when the window that currently has focus belongs to a process
        the user has excluded. Windows-only; other platforms always return
        False (the master switch still works everywhere)."""
        names = self._privacy.get('excluded_apps') or []
        if not names:
            return False
        current = self._foreground_process_name()
        if not current:
            return False
        current = current.lower()
        for name in names:
            if not name:
                continue
            candidate = str(name).strip().lower()
            if not candidate:
                continue
            # Match with or without the .exe the user may or may not type.
            if current == candidate or current == candidate + ".exe":
                return True
        return False

    @staticmethod
    def _foreground_process_name():
        """Process name of the foreground window, or None when it cannot be
        determined (non-Windows, permissions, race with a closing window)."""
        import sys
        if not sys.platform.startswith("win"):
            return None
        try:
            import ctypes
            user32 = ctypes.windll.user32
            hwnd = user32.GetForegroundWindow()
            if not hwnd:
                return None
            pid = ctypes.c_ulong()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if not pid.value:
                return None
            import psutil
            return psutil.Process(pid.value).name()
        except Exception:
            return None

    def _update_capture_state_ui(self):
        """Reflect the capture state in the header. Without this a user who
        switched capture off would later see an empty history and reasonably
        conclude the feature was broken."""
        label = getattr(self, '_capture_state_label', None)
        if label is None:
            return
        if self._privacy.get('capture_enabled', True):
            label.hide()
            return
        label.setText("⏸ Capture off")
        label.setToolTip(
            "Clipboard capture is switched off in Settings → Clipboard.\n"
            "Existing history is still shown and can be pasted.")
        label.show()

    @staticmethod
    def _encode_png(qimage: QImage) -> bytes:
        buf = QBuffer()
        buf.open(QIODevice.OpenModeFlag.WriteOnly)
        qimage.save(buf, "PNG")
        data = bytes(buf.data())
        buf.close()
        return data

    @staticmethod
    def _fmt_size(n: int) -> str:
        if n < 1024:
            return f"{n} B"
        if n < 1024 * 1024:
            return f"{n / 1024:.0f} KB"
        return f"{n / (1024 * 1024):.1f} MB"

    # ------------------------------------------------------------------
    # List management – TEXT
    # ------------------------------------------------------------------

    def _add_text_clip(self, text: str, *, item_id=None,
                       pasted: bool = False, save_to_db: bool = False):
        item = QListWidgetItem(self._format_display(text))
        item.setData(_ROLE_DB_ID, item_id)
        item.setData(_ROLE_KIND, 'text')
        item.setData(_ROLE_TEXT, text)
        item.setData(_ROLE_IMG,  None)
        item.setData(_ROLE_PASTED, pasted)
        item.setToolTip(text[:500] if len(text) > 500 else text)
        self._apply_style(item, pasted)
        self._text_list.insertItem(0, item)

        # Late-clip selection-follow: if this clip arrived while the list
        # is on screen and the user hasn't navigated below the top (the
        # typical case when a slow source app's copy lands after the
        # summon's 250 ms floor), snap the selection to the new row 0 so
        # Enter pastes the clip the user just copied, not the previous
        # one. insertItem shifts a previously-selected row 0 to row 1, so
        # current <= 1 means "was at the top, or nothing selected".
        if save_to_db and self._text_list.isVisible() \
                and self._text_list.currentRow() <= 1:
            self._text_list.setCurrentRow(0)

        if save_to_db:
            db = self._get_db()
            if db:
                new_id = db.add_clipboard_item(text, self.MAX_TEXT_ITEMS,
                                               self.MAX_IMAGE_ITEMS)
                item.setData(_ROLE_DB_ID, new_id)

        self._trim_list(self._text_list, self.MAX_TEXT_ITEMS)
        self._update_empty_state(self._text_list)
        self._update_count()

    # ------------------------------------------------------------------
    # List management – IMAGE
    # ------------------------------------------------------------------

    def _add_image_clip(self, label: str, png_bytes: bytes, *,
                        item_id=None, pasted: bool = False,
                        save_to_db: bool = False):
        item = QListWidgetItem(label)
        item.setData(_ROLE_DB_ID, item_id)
        item.setData(_ROLE_KIND, 'image')
        item.setData(_ROLE_TEXT, None)
        item.setData(_ROLE_IMG,  png_bytes)
        item.setData(_ROLE_PASTED, pasted)

        thumb = self._make_thumbnail(png_bytes)
        if thumb is not None:
            item.setIcon(QIcon(thumb))

        item.setToolTip(label)
        self._apply_style(item, pasted)
        self._image_list.insertItem(0, item)

        # Same late-clip selection-follow as _add_text_clip: a copy that
        # lands after the summon opened should be what Enter pastes.
        if save_to_db and self._image_list.isVisible() \
                and self._image_list.currentRow() <= 1:
            self._image_list.setCurrentRow(0)

        if save_to_db:
            db = self._get_db()
            if db:
                new_id = db.add_clipboard_image(label, png_bytes,
                                                self.MAX_IMAGE_ITEMS)
                item.setData(_ROLE_DB_ID, new_id)

        self._trim_list(self._image_list, self.MAX_IMAGE_ITEMS)
        self._update_empty_state(self._image_list)
        self._update_count()

    # ------------------------------------------------------------------
    # Image preview popup (v1.10.351)
    # ------------------------------------------------------------------

    def _on_image_current_changed(self, _current=None, _previous=None):
        """Selection moved in the Images column: hide any visible
        preview immediately and re-arm the pause timer, so the preview
        follows the user's navigation with a ~350 ms settle delay."""
        if self._preview_popup is not None and self._preview_popup.isVisible():
            self._preview_popup.hide()
        if self._image_list.hasFocus():
            self._preview_timer.start()

    def _hide_image_preview(self):
        self._preview_timer.stop()
        if self._preview_popup is not None:
            self._preview_popup.hide()

    def _ensure_preview_popup(self) -> QLabel:
        if self._preview_popup is None:
            popup = QLabel(
                None,
                Qt.WindowType.ToolTip | Qt.WindowType.FramelessWindowHint)
            popup.setStyleSheet(
                "background-color: white; border: 1px solid #888;")
            popup.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._preview_popup = popup
        return self._preview_popup

    def _show_image_preview(self):
        """Pause timer fired: show the preview for the current image.

        Guards re-check focus and visibility at fire time – the timer
        may outlive a fast Alt+Tab or tab switch."""
        lst = self._image_list
        if not self.isVisible() or not lst.hasFocus():
            return
        item = lst.currentItem()
        if item is None or item.data(_ROLE_KIND) != 'image':
            return
        png = item.data(_ROLE_IMG)
        if not png:
            db = self._get_db()
            item_id = item.data(_ROLE_DB_ID)
            if db and item_id is not None:
                png = db.get_clipboard_image_data(item_id)
        if not png:
            return
        pixmap = QPixmap()
        if not pixmap.loadFromData(png, "PNG") or pixmap.isNull():
            return

        screen = self.screen()
        if screen is None:
            from PyQt6.QtGui import QGuiApplication
            screen = QGuiApplication.primaryScreen()
        avail = screen.availableGeometry()
        # "Decent-sized": up to ~40 % of the screen's width / 55 % of
        # its height, never upscaled beyond the image's natural size.
        max_w = max(320, int(avail.width() * 0.40))
        max_h = max(240, int(avail.height() * 0.55))
        if pixmap.width() > max_w or pixmap.height() > max_h:
            pixmap = pixmap.scaled(
                max_w, max_h,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation)

        popup = self._ensure_preview_popup()
        popup.setPixmap(pixmap)
        popup.setFixedSize(pixmap.width() + 2, pixmap.height() + 2)

        # Place the preview to the LEFT of the Images column, aligned
        # with the current row (the Menu column sits to the right, and
        # covering the transient Text column is the least disruptive
        # option). Clamped to the available screen area.
        rect = lst.visualItemRect(item)
        anchor = lst.viewport().mapToGlobal(rect.topLeft())
        x = anchor.x() - popup.width() - 12
        y = anchor.y()
        x = max(avail.left() + 8, x)
        y = max(avail.top() + 8,
                min(y, avail.bottom() - popup.height() - 8))
        popup.move(x, y)
        popup.show()

    def _make_thumbnail(self, png_bytes: bytes):
        pixmap = QPixmap()
        if not pixmap.loadFromData(png_bytes, "PNG"):
            return None
        return pixmap.scaled(
            self._THUMB_SIZE,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )

    # ------------------------------------------------------------------
    # Styling / display helpers
    # ------------------------------------------------------------------

    def _apply_style(self, item: QListWidgetItem, pasted: bool):
        if pasted:
            item.setForeground(self._COLOUR_PASTED)
            item.setBackground(self._BG_PASTED)
        else:
            item.setForeground(self._COLOUR_NORMAL)
            item.setBackground(self._BG_NORMAL)

    @staticmethod
    def _format_display(text: str, max_len: int = 120) -> str:
        display = re.sub(r'\s+', ' ', text).strip()
        if len(display) > max_len:
            return display[:max_len] + "…"
        return display

    # ------------------------------------------------------------------
    # Activation (paste)
    # ------------------------------------------------------------------

    def _on_item_activated(self, item: QListWidgetItem):
        """Enter / click on a clipboard-history item. Sets the
        clipboard to the item's contents and – if a source window was
        captured via Ctrl+Alt+C – hides Workbench and pastes back
        into the source app.

        v1.10.25 fix: prior versions called only ``_paste_text_callback``
        / ``_paste_image_callback`` here, and the Workbench-supplied
        callbacks since the Sidekick→Workbench migration in v1.10.0
        only put the text/pixmap on the clipboard and did nothing
        else. So pressing Enter on a clipboard item silently lost
        the paste-back step. Snippets / Special Characters / Text
        Conversions in the action-menu column still worked because
        they always called ``_paste_to_source`` directly, bypassing
        the callbacks. We now route the top-level item activation
        through the same path. The callback parameters remain in
        the constructor for backward compatibility but are no
        longer load-bearing.
        """
        self._hide_image_preview()
        kind = item.data(_ROLE_KIND)
        if kind == 'text':
            text = item.data(_ROLE_TEXT)
            if not text:
                return
            self._mark_pasted(item)
            self._paste_to_source(text)
            # Notify external observers (no-op for the current
            # Workbench-supplied stub, but kept as a hook so any
            # future host can subscribe to "user picked a clipboard
            # item" events without us reintroducing a parallel
            # paste-back code path).
            if self._paste_text_callback:
                try:
                    self._paste_text_callback(text)
                except Exception:
                    pass

        elif kind == 'image':
            png = item.data(_ROLE_IMG)
            if not png:
                # Fall back to lazy DB fetch (e.g. after a future memory-light load)
                db = self._get_db()
                item_id = item.data(_ROLE_DB_ID)
                if db and item_id is not None:
                    png = db.get_clipboard_image_data(item_id)
            if not png:
                return
            pixmap = QPixmap()
            if not pixmap.loadFromData(png, "PNG"):
                return
            self._mark_pasted(item)
            self._paste_pixmap_to_source(pixmap)
            if self._paste_image_callback:
                try:
                    self._paste_image_callback(pixmap)
                except Exception:
                    pass

    def _mark_pasted(self, item: QListWidgetItem):
        item.setData(_ROLE_PASTED, True)
        self._apply_style(item, True)
        item_id = item.data(_ROLE_DB_ID)
        if item_id is not None:
            db = self._get_db()
            if db:
                db.mark_clipboard_item_pasted(item_id)

    # ------------------------------------------------------------------
    # Context menu & item deletion
    # ------------------------------------------------------------------

    def _on_context_menu(self, pos, list_widget: QListWidget):
        item = list_widget.itemAt(pos)
        menu = QMenu(self)
        act_delete = None
        act_type = None
        act_snip_personal = None
        act_snip_special = None
        if item is not None:
            act_delete = menu.addAction("🗑 Delete")
            # Per-item one-off: force the typing path for this paste,
            # handy when a specific target ignores Ctrl+V and the global
            # method is left on Ctrl+V / Auto. Text clips only.
            if item.data(_ROLE_KIND) == 'text':
                act_type = menu.addAction("⌨ Paste by typing")
                # v1.10.349: promote a clip straight into the snippet
                # library (Menu column) without leaving the Clipboard
                # Manager – previously this meant hand-creating an .md
                # file under snippet_library/ and hitting Refresh.
                menu.addSeparator()
                act_snip_personal = menu.addAction(
                    "\U0001F4C7 Save to Personal Snippets…")
                act_snip_special = menu.addAction(
                    "✨ Save to Special Characters…")
            menu.addSeparator()
        act_clear = menu.addAction("Clear all")

        # Persisted global paste-method chooser.
        menu.addSeparator()
        method_menu = menu.addMenu("Paste method")
        current_method = self._resolve_paste_method()
        method_actions = {}
        for key, label in (
            ('auto',   "Auto – type into terminals, else Ctrl+V"),
            ('ctrl_v', "Always Ctrl+V"),
            ('type',   "Always type the text"),
        ):
            a = method_menu.addAction(label)
            a.setCheckable(True)
            a.setChecked(current_method == key)
            method_actions[a] = key

        action = menu.exec(list_widget.viewport().mapToGlobal(pos))
        if action is None:
            return
        if action == act_delete and item is not None:
            self._delete_item(item, list_widget)
        elif action == act_type and item is not None:
            self._paste_item_by_typing(item)
        elif action == act_snip_personal and item is not None:
            self._add_clip_as_snippet(item, "Personal Snippets")
        elif action == act_snip_special and item is not None:
            self._add_clip_as_snippet(item, "Special Characters")
        elif action == act_clear:
            self._clear_all()
        elif action in method_actions:
            self._set_paste_method(method_actions[action])

    def _paste_item_by_typing(self, item: QListWidgetItem):
        """Paste a text clip by typing it out, regardless of the global
        paste-method setting. Mirrors the normal activation path but
        arms the one-shot ``_force_typing_once`` override first."""
        if item.data(_ROLE_KIND) != 'text':
            return
        text = item.data(_ROLE_TEXT)
        if not text:
            return
        self._mark_pasted(item)
        self._force_typing_once = True
        self._paste_to_source(text)

    def _delete_item(self, item: QListWidgetItem, list_widget: QListWidget):
        """Remove a single clip from the list and from the database."""
        row = list_widget.row(item)
        list_widget.takeItem(row)
        item_id = item.data(_ROLE_DB_ID)
        if item_id is not None:
            db = self._get_db()
            if db:
                db.delete_clipboard_item(item_id)
        self._update_empty_state(list_widget)
        self._update_count()

    # ------------------------------------------------------------------
    # Trimming, clearing, count
    # ------------------------------------------------------------------

    def _trim_list(self, list_widget: QListWidget, max_items: int):
        """Remove the oldest items in ``list_widget`` beyond ``max_items``."""
        while list_widget.count() > max_items:
            list_widget.takeItem(list_widget.count() - 1)

    def _clear_all(self):
        self._text_list.clear()
        self._image_list.clear()
        self._last_image_hash = None
        db = self._get_db()
        if db:
            db.clear_clipboard_history()
        self._update_empty_state(self._text_list)
        self._update_empty_state(self._image_list)
        self._update_count()

    def _top_text_item(self):
        return self._text_list.item(0) if self._text_list.count() > 0 else None

    def _top_image_item(self):
        return self._image_list.item(0) if self._image_list.count() > 0 else None

    def _update_count(self):
        text_n = self._text_list.count()
        image_n = self._image_list.count()
        total = text_n + image_n
        self._count_label.setText(
            f"Clipboard History ({total})" if total else "Clipboard History"
        )
        self._update_column_headers(text_n, image_n)

    def _update_column_headers(self, text_n=None, image_n=None):
        if text_n is None:
            text_n = self._text_list.count()
        if image_n is None:
            image_n = self._image_list.count()
        self._text_header.setText(
            f"📝 Text ({text_n})" if text_n else "📝 Text")
        self._image_header.setText(
            f"🖼 Images ({image_n})" if image_n else "🖼 Images")

    # ------------------------------------------------------------------
    # DB loading – called lazily once db_manager is ready
    # ------------------------------------------------------------------

    def ensure_db_loaded(self):
        if self._db_loaded:
            return
        db = self._get_db()
        if not db:
            # Don't latch: the db wasn't ready yet (e.g. an early warm-up
            # call). Stay un-loaded so a later call can still populate the
            # history instead of silently showing an empty list forever.
            return
        self._db_loaded = True
        # Enforce the retention window BEFORE loading (issue #246). Workbench
        # may have been closed for hours; without this, clips that expired
        # while it was shut would reappear in the list and sit there until
        # the first timer sweep a minute later.
        if self._privacy.get('auto_delete_enabled'):
            try:
                minutes = int(self._privacy.get('auto_delete_minutes', 60) or 0)
                purge = getattr(db, 'purge_clipboard_items_older_than', None)
                if minutes > 0 and callable(purge):
                    purge(minutes)
            except Exception as e:
                print(f"[ClipboardManager] startup purge failed: {e}")
        try:
            # Pull both kinds; per-kind caps are enforced separately on the
            # widget side so the DB query just needs to be generous enough
            # to cover both budgets.
            limit = self.MAX_TEXT_ITEMS + self.MAX_IMAGE_ITEMS
            items = db.get_clipboard_items(limit)
            # DB returns newest-first.  insertItem(0, …) puts each new item at
            # the top, so iterate oldest-first to leave newest at row 0.
            for row in reversed(items):
                kind = row.get('kind') or 'text'
                if kind == 'image':
                    self._add_image_clip(
                        row.get('text') or "🖼 Image",
                        row['image_data'] or b"",
                        item_id=row['id'],
                        pasted=bool(row['pasted']),
                        save_to_db=False,
                    )
                else:
                    self._add_text_clip(
                        row.get('text') or "",
                        item_id=row['id'],
                        pasted=bool(row['pasted']),
                        save_to_db=False,
                    )
            self._update_count()
        except Exception as e:
            print(f"[ClipboardManager] Failed to load history from db: {e}")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get_db(self):
        return getattr(self._parent_app, 'db_manager', None)

    # ------------------------------------------------------------------
    # Action tree (3rd column) – Snippets / Special Characters / Text
    # Conversions / QuickLauncher Prompts. v1.10.2 (Phase 3 of issue
    # #199). Mirrors the structure Sidekick built in its right pane,
    # but lives inside the Clipboard tab so the whole experience –
    # text snippets, images, snippets, conversions, prompts – sits
    # under one navigable surface in Workbench.
    # ------------------------------------------------------------------

    _CATEGORY_SENTINEL = "__category__"  # marks tree items that are categories
    _LEAF_ICON = "•"

    def _build_action_tree(self) -> QTreeWidget:
        """Create the QTreeWidget that hosts the 3rd column's content.

        Populated by ``_populate_action_tree`` shortly after construction
        so the snippets / prompts that depend on the parent app's
        ``user_data_path`` and ``prompt_manager_qt`` have time to wire up.
        """
        tree = QTreeWidget()
        tree.setHeaderHidden(True)
        tree.setRootIsDecorated(True)
        tree.setAnimated(True)
        tree.setIndentation(16)
        tree.setStyleSheet(f"""
            QTreeWidget {{
                border: 1px solid #DDD;
                background: #FAFAFA;
                font-size: {scaled_pt(9):.1f}pt; outline: none;
            }}
            QTreeWidget::item {{
                padding: 4px 6px; border-radius: 4px;
            }}
            QTreeWidget::item:selected {{
                background-color: #D6E4F0; color: #1E1E1E;
            }}
            QTreeWidget::item:hover {{
                background-color: #FFFFFF;
            }}
        """)
        tree.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        tree.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        tree.itemActivated.connect(self._on_action_tree_activated)
        tree.itemClicked.connect(self._on_action_tree_clicked)
        tree.itemExpanded.connect(self._update_expand_indicators)
        tree.itemCollapsed.connect(self._update_expand_indicators)
        tree.installEventFilter(self)
        # v1.10.349: right-click menu for managing snippets in place
        # (new / edit / delete / open folder) instead of hand-editing
        # .md files under snippet_library/ and hitting Refresh.
        tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        tree.customContextMenuRequested.connect(
            self._on_action_tree_context_menu)
        # Assign self._action_tree *before* _populate_action_tree runs –
        # the populate helpers (_make_action_category etc.) reference
        # self._action_tree to addTopLevelItem onto it. Without this
        # the first populate call raises AttributeError because
        # _build_action_tree hasn't returned yet.
        self._action_tree = tree
        # Defer populate to a separate method so we can rebuild on
        # demand (e.g. after the user edits the snippet library).
        self._populate_action_tree()
        return tree

    def _populate_action_tree(self):
        """Fill the action tree with categories + entries.

        Called once during widget construction and again whenever the
        user clicks the Refresh button on the Menu column header.
        Always reads file-backed sources fresh from disk so external
        edits to snippet .md files or QuickLauncher prompt .md files
        are reflected immediately.
        """
        tree = self._action_tree
        tree.clear()
        self._action_items.clear()

        # Reload the unified prompt library from disk before populating
        # the Prompts category — its in-memory cache is shared with the
        # AI tab's Prompt Manager and is only refreshed by explicit
        # reload calls.  Without this, Refresh would rebuild the tree
        # from the stale cache and external prompt edits wouldn't show.
        # _populate_snippet_library constructs a fresh SnippetLibrary
        # each call so it's already disk-fresh.
        try:
            pm = getattr(self._parent_app, 'prompt_manager_qt', None)
            lib = getattr(pm, 'library', None) if pm else None
            if lib and hasattr(lib, 'load_all_prompts'):
                lib.load_all_prompts()
        except Exception as e:
            # Don't let a prompt-library reload failure block the
            # snippet refresh — log and continue with whatever's cached.
            print(f"[ClipboardManagerWidget] Prompt library reload failed during Refresh: {e}")

        # 1. Snippets (file-backed; includes "Special Characters" and
        #    "Personal Snippets" by default, plus any user-created
        #    folders under <user_data>/snippet_library/).
        self._populate_snippet_library()

        # 2. Text Conversions (clipboard-text transformations).
        self._populate_text_conversions()

        # 3. QuickLauncher Prompts (from the unified prompt library;
        #    in v1.10.2 these just copy the prompt body to the
        #    clipboard. Phase-4 follow-up: act on the user's selection
        #    when activated, per issue #199's longer-term vision).
        self._populate_prompt_library()

        self._update_expand_indicators()

    def _make_action_category(self, label: str, expanded: bool = False) -> QTreeWidgetItem:
        """Create a bold category node in the action tree."""
        bold_font = QFont("Segoe UI", round(scaled_pt(9)), QFont.Weight.Bold)
        cat_color = QBrush(QColor("#3D5A80"))
        cat = QTreeWidgetItem([label])
        cat.setFont(0, bold_font)
        cat.setForeground(0, cat_color)
        cat.setData(0, Qt.ItemDataRole.UserRole, self._CATEGORY_SENTINEL)
        self._action_tree.addTopLevelItem(cat)
        cat.setExpanded(expanded)
        return cat

    def _add_action_leaf(self, parent: QTreeWidgetItem, text: str, callback):
        """Add a leaf item with a callback that runs on activation."""
        item = QTreeWidgetItem([text])
        idx = len(self._action_items)
        item.setData(0, Qt.ItemDataRole.UserRole, idx)
        parent.addChild(item)
        self._action_items.append(callback)
        return item

    def _update_expand_indicators(self, *_args):
        """Refresh ▶ / ▼ indicators on each top-level category."""
        root = self._action_tree.invisibleRootItem()
        for i in range(root.childCount()):
            item = root.child(i)
            if item.childCount() > 0:
                text = item.text(0)
                for prefix in ("▶ ", "▼ "):
                    if text.startswith(prefix):
                        text = text[2:]
                        break
                indicator = "▼ " if item.isExpanded() else "▶ "
                item.setText(0, indicator + text)

    def _on_action_tree_activated(self, item: QTreeWidgetItem, _col: int = 0):
        """Enter / double-click on a tree item.

        Categories: toggle expand/collapse. Leaves: fire the callback
        stored in ``self._action_items`` at the item's UserRole index.
        """
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if data == self._CATEGORY_SENTINEL:
            item.setExpanded(not item.isExpanded())
            return
        if isinstance(data, int) and 0 <= data < len(self._action_items):
            try:
                self._action_items[data]()
            except Exception as e:
                print(f"[ClipboardManagerWidget] Action handler error: {e}")

    def _on_action_tree_clicked(self, item: QTreeWidgetItem, _col: int = 0):
        """Single-click on a tree item.

        Single-click activates leaves immediately (matches the existing
        clipboard text/image columns, which paste on single click). For
        categories, single-click toggles expansion, matching the
        intuition that the entire row is the click target – not just
        the disclosure triangle.
        """
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if data == self._CATEGORY_SENTINEL:
            item.setExpanded(not item.isExpanded())
            return
        if isinstance(data, int) and 0 <= data < len(self._action_items):
            try:
                self._action_items[data]()
            except Exception as e:
                print(f"[ClipboardManagerWidget] Action handler error: {e}")

    # ---- Snippets -----------------------------------------------------

    def _populate_snippet_library(self):
        """Add file-backed snippets, grouped by their top-level folder.

        Reads .md files from ``<user_data>/snippet_library/`` via
        :class:`SnippetLibrary`. Top-level folders become tree
        categories ("Special Characters", "Personal Snippets" by
        default; any user-created folder gets a generic icon).
        """
        try:
            from modules.snippet_library import SnippetLibrary, DEFAULT_SNIPPETS

            user_data_path = getattr(self._parent_app, 'user_data_path', None)
            if not user_data_path:
                return

            library_dir = Path(user_data_path) / "snippet_library"
            lib = SnippetLibrary(library_dir=str(library_dir))
            lib.ensure_defaults(DEFAULT_SNIPPETS)
            lib.load_all()

            if not lib.snippets:
                return

            from collections import defaultdict
            by_category = defaultdict(list)
            for snip in lib.snippets:
                cat = snip['category'] or "Snippets"
                by_category[cat].append(snip)

            category_icons = {
                "Special Characters": "✨",        # ✨
                "Personal Snippets": "\U0001F4C7",     # 📇
            }
            default_icon = "\U0001F4C1"                # 📁

            # v1.10.350: enumerate folders straight from disk (not just
            # from loaded snippet files) so nested subfolders render as
            # sub-nodes – and a freshly created EMPTY folder appears on
            # the next Refresh too, instead of silently not showing up
            # until it contains a snippet.
            all_categories = set(by_category.keys())
            disk_subfolders = {}   # cat name -> set of subpath tuples
            try:
                for cat_dir in library_dir.iterdir():
                    if not cat_dir.is_dir() or cat_dir.name.startswith('.'):
                        continue
                    all_categories.add(cat_dir.name)
                    subs = set()
                    for d in cat_dir.rglob('*'):
                        if d.is_dir() and not d.name.startswith('.'):
                            subs.add(d.relative_to(cat_dir).parts)
                    disk_subfolders[cat_dir.name] = subs
            except Exception as e:
                print(f"[ClipboardManagerWidget] snippet folder scan "
                      f"failed: {e}")

            for cat_name in sorted(all_categories, key=str.lower):
                icon = category_icons.get(cat_name, default_icon)
                cat_item = self._make_action_category(f"{icon} {cat_name}")
                cat_item.setData(0, _ROLE_TREE_SNIPPET,
                                 ('category', cat_name, ''))

                # Sub-nodes on demand, parents created recursively. The
                # right-click metadata carries the folder path RELATIVE
                # to snippet_library/ (e.g. "Personal Snippets/Trados"),
                # so "New snippet in …" lands new files in that exact
                # subfolder. Default-arg binding pins this iteration's
                # category/node-map (no late-binding across the loop).
                node_for = {(): cat_item}

                def _folder_node(subpath, _cat=cat_name, _nodes=node_for):
                    if subpath in _nodes:
                        return _nodes[subpath]
                    parent = _folder_node(subpath[:-1], _cat, _nodes)
                    sub = QTreeWidgetItem(
                        [f"{default_icon} {subpath[-1]}"])
                    sub.setData(0, Qt.ItemDataRole.UserRole,
                                self._CATEGORY_SENTINEL)
                    rel = _cat + '/' + '/'.join(subpath)
                    sub.setData(0, _ROLE_TREE_SNIPPET,
                                ('category', rel, ''))
                    parent.addChild(sub)
                    _nodes[subpath] = sub
                    return sub

                # Folders first (sorted, parents before children), then
                # leaves per folder – file-manager-style ordering.
                for subpath in sorted(
                        disk_subfolders.get(cat_name, ()),
                        key=lambda p: tuple(x.lower() for x in p)):
                    _folder_node(subpath)

                for snip in sorted(
                        by_category.get(cat_name, []),
                        key=lambda s: (
                            tuple(x.lower()
                                  for x in s.get('subfolders', ())),
                            s['label'].lower())):
                    subf = tuple(s for s in snip.get('subfolders', ()))
                    parent = _folder_node(subf)
                    rel_folder = (cat_name if not subf
                                  else cat_name + '/' + '/'.join(subf))
                    body = snip['body']
                    leaf = self._add_action_leaf(
                        parent, snip['label'],
                        lambda t=body: self._copy_to_clipboard(t),
                    )
                    leaf.setData(0, _ROLE_TREE_SNIPPET,
                                 ('snippet', rel_folder, str(snip['path'])))

        except Exception as e:
            print(f"[ClipboardManagerWidget] Snippet population error: {e}")

    # ---- Snippet management (v1.10.349) --------------------------------
    #
    # Everything below lets the user create / edit / delete snippets from
    # inside the Clipboard Manager via right-click, instead of hand-editing
    # .md files under <user_data>/snippet_library/ and pressing Refresh.
    # The on-disk format is unchanged: one .md file per snippet, filename
    # = label, body = inserted text, top-level folder = category.

    def _snippet_library_dir(self):
        """Path of <user_data>/snippet_library, or None if the host app
        doesn't expose a user_data_path (headless tests)."""
        user_data_path = getattr(self._parent_app, 'user_data_path', None)
        if not user_data_path:
            return None
        return Path(user_data_path) / "snippet_library"

    def _notify(self, msg: str):
        """Small confirmation to app log + status bar (no ⚠ prefix –
        these are successes, unlike _paste_diag's warnings)."""
        print(f"[ClipboardManagerWidget] {msg}")
        app = self._parent_app
        try:
            if hasattr(app, 'log'):
                app.log(msg)
        except Exception:
            pass
        try:
            sb = getattr(app, 'status_bar', None)
            if sb is not None:
                sb.showMessage(msg, 4000)
        except Exception:
            pass

    @staticmethod
    def _unique_snippet_path(folder: Path, label: str) -> Path:
        """Filename for ``label`` in ``folder`` that doesn't collide with
        an existing snippet – appends " (2)", " (3)", … when needed."""
        from modules.snippet_library import SnippetLibrary
        stem = SnippetLibrary._safe_filename(label)
        path = folder / f"{stem}.md"
        n = 2
        while path.exists():
            path = folder / f"{stem} ({n}).md"
            n += 1
        return path

    def _on_action_tree_context_menu(self, pos):
        """Right-click on the Menu column: snippet management actions.

        Snippet leaves get Edit / Delete / New-in-category; snippet
        category headers get New-in-category; anywhere else (including
        empty space) offers a new Personal Snippet. Text Conversions and
        Prompts nodes aren't editable here – they have their own
        file-backed workflows – but the "open folder" escape hatch is
        always available.
        """
        tree = self._action_tree
        item = tree.itemAt(pos)
        meta = item.data(0, _ROLE_TREE_SNIPPET) if item is not None else None

        menu = QMenu(self)
        act_edit = act_delete = None
        if meta and meta[0] == 'snippet':
            target_category = meta[1]
            act_edit = menu.addAction("✏ Edit snippet…")
            act_delete = menu.addAction("🗑 Delete snippet")
            menu.addSeparator()
            act_new = menu.addAction(
                f"➕ New snippet in \"{target_category}\"…")
        elif meta and meta[0] == 'category':
            target_category = meta[1]
            act_new = menu.addAction(
                f"➕ New snippet in \"{target_category}\"…")
        else:
            target_category = "Personal Snippets"
            act_new = menu.addAction("➕ New Personal Snippet…")
        menu.addSeparator()
        act_open = menu.addAction("📂 Open snippets folder")

        action = menu.exec(tree.viewport().mapToGlobal(pos))
        if action is None:
            return
        if action == act_new:
            self._new_snippet_dialog(target_category)
        elif act_edit is not None and action == act_edit:
            self._edit_snippet_dialog(meta[2])
        elif act_delete is not None and action == act_delete:
            self._delete_snippet(meta[2])
        elif action == act_open:
            self._open_snippets_folder()

    def _open_snippets_folder(self):
        lib_dir = self._snippet_library_dir()
        if lib_dir is None:
            return
        try:
            lib_dir.mkdir(parents=True, exist_ok=True)
            from modules.platform_helpers import open_folder
            open_folder(str(lib_dir))
        except Exception as e:
            print(f"[ClipboardManagerWidget] open snippets folder failed: {e}")

    def _add_clip_as_snippet(self, item: QListWidgetItem, category: str):
        """Right-click on a text clip → seed the new-snippet dialog with
        the clip's text. The label prefill is a squashed preview the user
        can overwrite; the body is the clip verbatim."""
        text = item.data(_ROLE_TEXT)
        if not text:
            return
        label = re.sub(r'\s+', ' ', text).strip()[:40]
        self._new_snippet_dialog(category, prefill_label=label,
                                 prefill_body=text)

    def _new_snippet_dialog(self, category: str, *,
                            prefill_label: str = "",
                            prefill_body: str = ""):
        lib_dir = self._snippet_library_dir()
        if lib_dir is None:
            return
        dlg = SnippetEditDialog(
            self, title=f"New snippet – {category}",
            label_text=prefill_label, body_text=prefill_body)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            folder = lib_dir / category
            folder.mkdir(parents=True, exist_ok=True)
            path = self._unique_snippet_path(folder, dlg.result_label)
            path.write_text(dlg.result_body.rstrip('\n') + '\n',
                            encoding='utf-8')
        except Exception as e:
            self._notify(f"⚠ Could not save snippet: {e}")
            return
        self._populate_action_tree()
        self._notify(f"✓ Snippet \"{dlg.result_label}\" added to {category}")

    def _edit_snippet_dialog(self, path_str: str):
        """Edit an existing snippet's label and/or body.

        Always writes the result as ``<safe(label)>.md`` with a bare
        body – i.e. a label change renames the file, and a legacy
        front-matter file gets normalised to the current filename-is-
        the-label format on first edit (same as the v1.9.459 default
        migration did for shipped snippets).
        """
        from modules.snippet_library import SnippetLibrary, _split_front_matter
        path = Path(path_str)
        try:
            raw = path.read_text(encoding='utf-8')
        except Exception as e:
            self._notify(f"⚠ Could not read snippet file: {e}")
            return
        meta, body = _split_front_matter(raw)
        label = meta.get('name') or path.stem

        dlg = SnippetEditDialog(self, title="Edit snippet",
                                label_text=label,
                                body_text=body.rstrip('\n'))
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            target = path.parent / (
                SnippetLibrary._safe_filename(dlg.result_label) + '.md')
            if target != path and target.exists():
                target = self._unique_snippet_path(path.parent,
                                                   dlg.result_label)
            target.write_text(dlg.result_body.rstrip('\n') + '\n',
                              encoding='utf-8')
            if target != path:
                path.unlink(missing_ok=True)
        except Exception as e:
            self._notify(f"⚠ Could not save snippet: {e}")
            return
        self._populate_action_tree()
        self._notify(f"✓ Snippet \"{dlg.result_label}\" saved")

    def _delete_snippet(self, path_str: str):
        path = Path(path_str)
        answer = QMessageBox.question(
            self, "Delete snippet",
            f"Delete the snippet \"{path.stem}\"?\n\n"
            f"This removes the file\n{path}\nand cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            path.unlink()
        except Exception as e:
            self._notify(f"⚠ Could not delete snippet: {e}")
            return
        self._populate_action_tree()
        self._notify(f"✓ Snippet \"{path.stem}\" deleted")

    # ---- Text Conversions ---------------------------------------------

    def _populate_text_conversions(self):
        """Conversions that act on whatever's currently on the clipboard.

        Loaded from the file-backed
        ``<user_data>/text_conversion_library/`` folder.  Each ``.md``
        file declares one conversion via YAML frontmatter (see
        :mod:`text_conversion_library` for the schema).  Default
        conversions are seeded on first launch so a fresh install
        behaves identically to the pre-library hardcoded list.

        Result lands back on the system clipboard; the user pastes with
        Ctrl+V wherever they're focused.  Sidekick used a paste-and-
        return flow because it floated over another app; in the
        Workbench top tab the user is already where they want to be,
        so "modify clipboard, you paste" is the simpler contract.
        """
        try:
            from modules.text_conversion_library import (
                TextConversionLibrary, DEFAULT_CONVERSIONS,
            )

            user_data_path = getattr(self._parent_app, 'user_data_path', None)
            if not user_data_path:
                return

            library_dir = Path(user_data_path) / "text_conversion_library"
            lib = TextConversionLibrary(library_dir=str(library_dir))
            lib.ensure_defaults(DEFAULT_CONVERSIONS)
            lib.load_all()

            if not lib.conversions:
                return

            cat = self._make_action_category("\U0001F524 Text Conversions")

            # Sort by category then label so the list is stable and
            # disk re-organisation (moving files between folders) is
            # immediately visible.  Disabled conversions are dropped
            # entirely — `enabled: false` in the YAML hides without
            # deleting.
            visible = [c for c in lib.conversions if c.enabled]
            visible.sort(key=lambda c: (c.category.lower(), c.label.lower()))

            for conv in visible:
                # Capture conv by default-arg so the lambda doesn't
                # close over the loop variable.
                self._add_action_leaf(
                    cat, conv.label,
                    lambda c=conv: self._transform_clipboard(c.apply),
                )

        except Exception as e:
            print(f"[ClipboardManagerWidget] Text Conversions population error: {e}")

    def set_source_window(self, hwnd):
        """Tell the widget which window the user arrived from (via a
        global hotkey). Snippet / conversion activations will then do
        the paste-and-return dance instead of just setting the
        clipboard. ``None`` clears the source – use that when the user
        navigates to the Clipboard tab manually rather than via
        Ctrl+Alt+C, since there's no "elsewhere" to return to."""
        self._source_window = hwnd

    def _paste_to_source(self, text: str):
        """Set clipboard to ``text``, then – if a source window was
        captured – hide Workbench, refocus the source window, and send
        Ctrl+V. Without a source, just set the clipboard and stay put.

        Mirrors Sidekick's ``_paste_and_return`` semantics so users
        who Ctrl+Alt+C from Trados, pick a snippet / conversion, and
        expect the result back in Trados get exactly that.
        """
        if text is None:
            return
        self._pending_paste_text = text
        self._suppress_next = True
        # Prefer a native, materialised clipboard write (Windows): Qt's OLE
        # clipboard hands data over via a main-thread callback, so a consumer
        # reading while our event loop is busy gets nothing. A real Win32 copy
        # survives regardless of our event loop. The native write retries
        # OpenClipboard internally (contention from Win+V history / OneDrive /
        # Trados hooks is routine) and verifies with a read-back, so a False
        # return means the clipboard genuinely couldn't be written for ~1 s.
        # Pasting then would deliver STALE content – abort loudly instead of
        # falling back to the Qt clipboard, whose delayed rendering is the
        # exact failure mode the native write exists to avoid. Qt remains the
        # write path off-Windows.
        try:
            from modules.platform_helpers import IS_WINDOWS, set_clipboard_text
            if IS_WINDOWS:
                if not set_clipboard_text(text):
                    self._suppress_next = False
                    self._pending_paste_text = None
                    self._force_typing_once = False
                    self._paste_diag(
                        "Clipboard write failed – another application is "
                        "holding the clipboard. Paste cancelled; please try "
                        "again.")
                    return
            else:
                QApplication.clipboard().setText(text)
        except Exception:
            QApplication.clipboard().setText(text)
        self._activate_source_then_paste()

    def _paste_pixmap_to_source(self, pixmap):
        """Image-clip parallel of :meth:`_paste_to_source`. Set the
        clipboard to ``pixmap``, then – if a source window was
        captured – hide Workbench, refocus the source window, and send
        Ctrl+V. Without a source, just set the clipboard and stay put.

        Added in v1.10.25 to fix the image-clip Enter-to-paste path.
        Prior to v1.10.25 the image case (and the text case) ran
        through ``self._paste_image_callback`` / ``_paste_text_callback``,
        which the Workbench-supplied implementation had stubbed to
        only-set-the-clipboard – so the paste-back never fired for the
        primary clipboard-list activation path. Snippets / Special
        Characters / Text Conversions had always called
        ``_paste_to_source`` directly, so those still worked.
        """
        if pixmap is None or pixmap.isNull():
            return
        # Images can only be delivered via Ctrl+V – there's no "type an
        # image" path – so clear any pending text so _dispatch_paste
        # doesn't try to type.
        self._pending_paste_text = None
        self._suppress_next = True
        QApplication.clipboard().setPixmap(pixmap)
        self._activate_source_then_paste()

    def _activate_source_then_paste(self):
        """Shared post-clipboard paste-back: refocus the source window,
        hide Workbench, send Ctrl+V on a short delay. Both
        ``_paste_to_source`` (text) and ``_paste_pixmap_to_source``
        (images) end here.

        The text vs pixmap difference is purely the clipboard-write
        upstream; everything from "now make the source app
        foreground" onwards is identical, so v1.10.25 factored it
        out to share between the two.

        Order matters here. Original v1.10.1 code did
          1) hide Workbench  →  2) wait 100ms  →  3) activate source
        which looked symmetric to Sidekick but is in fact broken for
        a *non-Tool* top-level window:

          Windows' SetForegroundWindow() only honours calls from the
          process that *currently* owns the foreground (or has been
          attached via AttachThreadInput to the foreground thread).
          Once we hide() Workbench, Workbench is no longer the
          foreground process, so the deferred activate_foreground_
          window() 100ms later silently no-ops – the OS refuses the
          switch. Result: Trados never regains focus, the cursor is
          "nowhere", and the Ctrl+V keystroke we eventually send
          either evaporates or hits the desktop.

        New ordering (v1.10.3):
          1) Activate source *while Workbench still owns foreground*
             – the OS happily grants the switch in that state.
          2) Hide Workbench. Workbench isn't foreground any more, so
             hide() can't disturb the foreground state.
          3) After a short delay, send Ctrl+V to the now-foreground
             source window.

        Sidekick (``Qt.WindowType.Tool``) got away with the old order
        because Tool windows ride on the parent's foreground slot and
        never own foreground in their own right – the post-hide
        foreground state was always the source window, so the timing
        window didn't bite. Workbench is a regular top-level so we
        have to be explicit.
        """
        # Consume the one-shot "type this paste" override now, before any
        # early return, so it can't leak into a later paste-back.
        force_typing_once = self._force_typing_once
        self._force_typing_once = False

        source = self._source_window
        if source is None:
            # v1.10.201: in-Workbench return path. When Ctrl+Alt+C was
            # pressed from inside Workbench (e.g. the user was on the
            # Editor tab), ``_open_clipboard_after_copy`` detects that
            # the captured source HWND matches Workbench's own and
            # passes None as source_window so the standard activate-
            # and-hide flow doesn't fire (it would hide Workbench
            # entirely). Instead, the prior tab index is parked on
            # the parent app as ``_clipboard_prior_workbench_tab``.
            # Here we honour that: switch back to the prior tab and
            # synthesise Ctrl+V so the now-foreground widget receives
            # the paste. The 80 ms delay gives Qt time to re-focus the
            # restored tab before the keystroke goes out.
            try:
                parent_app = self._parent_app
                prior_tab = getattr(parent_app, '_clipboard_prior_workbench_tab', None)
                if prior_tab is not None:
                    main_tabs = getattr(parent_app, 'main_tabs', None)
                    if main_tabs is not None and prior_tab != main_tabs.currentIndex():
                        main_tabs.setCurrentIndex(prior_tab)

                    # v1.10.202 captured the focused widget here and
                    # called setFocus on it; that worked for plain
                    # widget hierarchies but not for QTableWidget cell
                    # editors, which get committed-and-destroyed when
                    # focus leaves them. The synthetic Ctrl+V then
                    # landed on a destroyed reference / wrong widget
                    # and nothing pasted.
                    #
                    # v1.10.203: take a more direct route. Instead of
                    # restoring focus + relying on synthetic Ctrl+V
                    # reaching the right widget, INSERT the clipboard
                    # text directly into the captured widget via Qt
                    # API. Works whether the widget is currently
                    # focused or hidden; falls back to focus + send_paste
                    # for widget types we don't recognise.
                    prior_focus = getattr(
                        parent_app, '_clipboard_prior_focused_widget', None
                    )
                    parent_app._clipboard_prior_workbench_tab = None
                    parent_app._clipboard_prior_focused_widget = None

                    # Captured clipboard text — used by both the direct
                    # insert path and the synthetic-paste fallback.
                    from PyQt6.QtWidgets import (
                        QApplication, QLineEdit, QTextEdit, QPlainTextEdit,
                    )
                    clipboard_text = QApplication.clipboard().text()

                    def _direct_insert_then_focus():
                        """Schedule on next event-loop turn so the tab
                        change has settled. Try direct text insertion
                        on the captured widget. If that fails (widget
                        destroyed, unknown type), fall through to
                        synthetic Ctrl+V."""
                        inserted_directly = False
                        try:
                            from PyQt6.QtCore import Qt as _QtConst
                            target_widget = prior_focus
                            if target_widget is not None and clipboard_text:
                                # Bring widget into focus for the visual
                                # cursor cue, even if we end up inserting
                                # programmatically below.
                                try:
                                    target_widget.setFocus(
                                        _QtConst.FocusReason.OtherFocusReason
                                    )
                                except (RuntimeError, AttributeError):
                                    pass

                                # Direct insertion paths per widget type.
                                # These bypass keystroke routing entirely,
                                # so the paste lands correctly regardless
                                # of foreground state, focus state, or
                                # whether the table's edit session was
                                # torn down by the tab switch.
                                try:
                                    if isinstance(target_widget,
                                                  (QTextEdit, QPlainTextEdit)):
                                        target_widget.insertPlainText(clipboard_text)
                                        inserted_directly = True
                                    elif isinstance(target_widget, QLineEdit):
                                        target_widget.insert(clipboard_text)
                                        inserted_directly = True
                                except (RuntimeError, AttributeError):
                                    # Captured widget was destroyed
                                    # between Ctrl+Alt+C and Enter
                                    # (project closed, tab rebuilt,
                                    # etc.). Fall through to synthetic
                                    # paste as a last resort.
                                    pass
                        except Exception as insert_err:
                            print(
                                f"[ClipboardManagerWidget] direct-insert "
                                f"failed, falling back: {insert_err}"
                            )

                        if inserted_directly:
                            return

                        # Fallback: synthetic Ctrl+V. Only used when
                        # the captured widget isn't a QLineEdit /
                        # QTextEdit / QPlainTextEdit, or was destroyed.
                        try:
                            from modules.platform_helpers import CrossPlatformKeySender
                            CrossPlatformKeySender().send_paste()
                        except Exception as paste_err:
                            print(
                                f"[ClipboardManagerWidget] in-Workbench "
                                f"paste fallback failed: {paste_err}"
                            )

                    # 80 ms gives the tab switch / focus restore time
                    # to process Qt events before we touch widgets.
                    from PyQt6.QtCore import QTimer
                    QTimer.singleShot(80, _direct_insert_then_focus)
            except Exception as e:
                print(
                    f"[ClipboardManagerWidget] in-Workbench return path "
                    f"failed: {e}"
                )
            return  # No external source – we're done here.

        # One-shot: clear after we've used it. The next activation
        # without a fresh hotkey trip just sets the clipboard and
        # stays in Workbench, which is the right default.
        self._source_window = None

        from PyQt6.QtCore import QTimer
        from modules.platform_helpers import (
            activate_foreground_window, CrossPlatformKeySender,
        )

        try:
            ok = activate_foreground_window(source)
            if not ok:
                print(
                    f"[ClipboardManagerWidget] activate_foreground_window"
                    f"({source!r}) returned False – source window may have"
                    f" closed; paste will land wherever focus is."
                )
        except Exception as e:
            print(f"[ClipboardManagerWidget] activate error: {e}")

        # Hide Workbench AFTER the activate call. The source is already
        # foreground by this point, so hide() is purely cosmetic – it
        # just removes our taskbar entry.
        try:
            parent_window = self._parent_app
            if parent_window is not None and hasattr(parent_window, 'hide'):
                parent_window.hide()
        except Exception as e:
            print(f"[ClipboardManagerWidget] Could not hide Workbench: {e}")

        def _do_paste():
            try:
                self._dispatch_paste(source, force_typing_once)
            except Exception as e:
                print(f"[ClipboardManagerWidget] Paste error: {e}")

        # Fire the paste once the foreground switch has demonstrably
        # settled, instead of after a fixed 150 ms grace. A constant
        # delay races window-manager bookkeeping on a loaded machine
        # (first activation after Workbench launch is notoriously
        # slower); the poll fires as soon as the source actually owns
        # the foreground – typically well under 150 ms – and diagnoses
        # loudly on timeout instead of pasting into whatever happens to
        # be focused. Windows-only (the handle is an HWND there);
        # mac/linux handles are window titles with no cheap foreground
        # query, so they keep the fixed grace period.
        if isinstance(source, int):
            self._paste_when_foreground_stable(source, _do_paste)
        else:
            QTimer.singleShot(150, _do_paste)

    def _paste_when_foreground_stable(self, source_hwnd: int, do_paste, *,
                                      interval_ms: int = 25,
                                      timeout_ms: int = 1200,
                                      stable_ticks: int = 2):
        """Run ``do_paste`` once ``source_hwnd`` has been the foreground
        window for ``stable_ticks`` consecutive polls, or after
        ``timeout_ms`` with a diagnostic (the paste path retries
        activation itself, so we still attempt the paste on timeout).

        QTimer-based rather than a sleep loop so the Qt event loop keeps
        running while we wait – the UI stays responsive and clipboard
        reads by the target app remain serviceable.
        """
        from PyQt6.QtCore import QTimer
        from modules.platform_helpers import get_foreground_window

        state = {'stable': 0, 'elapsed': 0}
        timer = QTimer(self)
        timer.setInterval(interval_ms)

        def _tick():
            state['elapsed'] += interval_ms
            try:
                fg = get_foreground_window()
            except Exception:
                fg = None
            state['stable'] = state['stable'] + 1 if fg == source_hwnd else 0

            if state['stable'] >= stable_ticks:
                timer.stop()
                timer.deleteLater()
                do_paste()
                return
            if state['elapsed'] >= timeout_ms:
                timer.stop()
                timer.deleteLater()
                self._paste_diag(
                    f"Clipboard paste: source window (hwnd={source_hwnd}) "
                    f"not foreground after {timeout_ms} ms – attempting the "
                    f"paste anyway.")
                do_paste()

        timer.timeout.connect(_tick)
        timer.start()

    # ------------------------------------------------------------------
    # Paste delivery strategy
    # ------------------------------------------------------------------

    _PASTE_METHODS = ('auto', 'ctrl_v', 'type')

    def _resolve_paste_method(self) -> str:
        """Return the persisted paste method for the paste-back step.

        One of:
          * ``'auto'``   – Ctrl+V everywhere, except type the text out
                           for detected console/terminal windows (which
                           ignore Ctrl+V). This is the default.
          * ``'ctrl_v'`` – always send a synthetic Ctrl+V.
          * ``'type'``   – always type the text character-by-character
                           (AHK SendText), for text clips.

        Stored in the app's unified settings under the ``features``
        section as ``clipboard_paste_method``. Falls back to ``'auto'``
        if the setting is missing or the host doesn't expose the
        settings accessors.
        """
        try:
            loader = getattr(self._parent_app, '_load_settings_section', None)
            if loader:
                val = (loader('features') or {}).get('clipboard_paste_method')
                if val in self._PASTE_METHODS:
                    return val
        except Exception as e:
            print(f"[ClipboardManagerWidget] paste-method read failed: {e}")
        return 'auto'

    def _set_paste_method(self, method: str):
        """Persist the paste method to the app's unified settings."""
        if method not in self._PASTE_METHODS:
            return
        try:
            loader = getattr(self._parent_app, '_load_settings_section', None)
            saver = getattr(self._parent_app, '_save_settings_section', None)
            if loader and saver:
                feats = loader('features') or {}
                feats['clipboard_paste_method'] = method
                saver('features', feats)
        except Exception as e:
            print(f"[ClipboardManagerWidget] paste-method save failed: {e}")

    def _dispatch_paste(self, source_hwnd, force_typing_once: bool = False):
        """Deliver the pending clip to the now-foreground source window.

        Chooses between a synthetic Ctrl+V and typing the text out,
        based on the resolved paste method (and the one-shot
        ``force_typing_once`` override from the "Paste by typing"
        context-menu action). Typing only applies to text clips – image
        clips always go via Ctrl+V because there's no "type an image"
        path. Falls back to Ctrl+V if the typing backend reports it
        couldn't run.
        """
        from modules.platform_helpers import (
            CrossPlatformKeySender, is_terminal_like_window,
            paste_target_needs_elevation,
        )
        sender = CrossPlatformKeySender()
        text = self._pending_paste_text

        # UIPI guard (Windows): if the target window runs at a higher
        # integrity level than Workbench (e.g. an app started with "Run
        # as administrator" while Workbench is not), Windows silently
        # drops BOTH synthetic Ctrl+V and typed keystrokes. Nothing we
        # can do in software fixes that – only running Workbench elevated
        # does – so surface a clear reason instead of a silent no-op. We
        # still attempt the paste afterwards in case the detection is a
        # false positive.
        if paste_target_needs_elevation(source_hwnd):
            self._warn_paste_blocked_by_elevation()

        # Last-moment focus guard (Windows). Between the verified activation and
        # this keystroke there was a 150 ms wait plus Workbench's hide(); if
        # focus drifted in that gap, re-activate the source now so the paste
        # can't land in the wrong window or nowhere. Only runs when the source
        # is an HWND (int); mac/linux handles are titles and are left alone.
        try:
            if isinstance(source_hwnd, int):
                from modules.platform_helpers import (
                    get_foreground_window, activate_foreground_window,
                )
                if get_foreground_window() != source_hwnd:
                    activate_foreground_window(source_hwnd)
        except Exception as e:
            print(f"[ClipboardManagerWidget] pre-paste refocus check failed: {e}")

        use_typing = False
        if text is not None:  # typing is a text-only capability
            method = self._resolve_paste_method()
            if force_typing_once or method == 'type':
                use_typing = True
            elif method == 'auto':
                use_typing = is_terminal_like_window(source_hwnd)

        if use_typing:
            try:
                if sender.type_text(text):
                    return
            except Exception as e:
                print(f"[ClipboardManagerWidget] type_text failed, "
                      f"falling back to Ctrl+V: {e}")
        # Pass the target HWND so AHK re-activates that exact window and waits
        # for it to be active before sending ^v – atomic activation+keystroke,
        # immune to a foreground wobble during the Python→AHK handoff. on_diag
        # surfaces an activation failure into the Workbench log so a missed
        # paste isn't a silent mystery.
        sender.send_paste(
            source_hwnd if isinstance(source_hwnd, int) else None,
            on_diag=self._paste_diag,
        )

    def _paste_diag(self, msg: str):
        """Surface a paste-path diagnostic to console + Workbench log + status bar."""
        print(f"[ClipboardManagerWidget] {msg}")
        app = self._parent_app
        try:
            if hasattr(app, 'log'):
                app.log(f"⚠ {msg}")
        except Exception:
            pass
        try:
            sb = getattr(app, 'status_bar', None)
            if sb is not None:
                sb.showMessage(f"⚠ {msg}", 6000)
        except Exception:
            pass

    def _warn_paste_blocked_by_elevation(self):
        """Tell the user why a paste into an elevated window will fail.

        Routed to the app log and status bar (both main-thread safe here,
        since _dispatch_paste runs from a QTimer on the GUI thread) so the
        UIPI block isn't an invisible no-op. Best-effort – any missing
        host hook is ignored."""
        msg = ("⚠ Clipboard paste may be blocked: the target window is running "
               "at a higher privilege level (e.g. an app started with 'Run as "
               "administrator') while Workbench is not. Windows blocks pasting "
               "into it. Run Supervertaler Workbench as administrator to paste "
               "into elevated apps.")
        print(f"[ClipboardManagerWidget] {msg}")
        app = self._parent_app
        try:
            if hasattr(app, 'log'):
                app.log(msg)
        except Exception:
            pass
        try:
            sb = getattr(app, 'status_bar', None)
            if sb is not None:
                sb.showMessage(
                    "⚠ Paste blocked – run Workbench as administrator to paste "
                    "into elevated apps", 6000)
        except Exception:
            pass

    def _transform_clipboard(self, fn):
        """Read clipboard text, apply ``fn``, paste back to source.

        If we have a source window (user arrived via Ctrl+Alt+C), do
        the paste-and-return flow. Otherwise just set the clipboard
        and stay in Workbench.
        """
        text = (QApplication.clipboard().text() or "").strip()
        if not text:
            return
        try:
            result = fn(text)
        except Exception as e:
            print(f"[ClipboardManagerWidget] Transform error: {e}")
            return
        self._paste_to_source(result)

    def _wrap_clipboard(self, prefix: str, suffix: str):
        """Read clipboard text, wrap, paste back to source."""
        text = (QApplication.clipboard().text() or "").strip()
        if not text:
            return
        self._paste_to_source(prefix + text + suffix)

    def _copy_to_clipboard(self, text: str):
        """Copy a snippet's body to the clipboard and paste back to
        source if one was captured."""
        if not text:
            return
        self._paste_to_source(text)

    # ---- Prompts ------------------------------------------------------

    def _populate_prompt_library(self):
        """Add QuickLauncher prompts from the unified prompt library,
        grouped by their folder structure.

        v1.10.2 behaviour: activating a prompt copies its body to the
        clipboard. A later iteration (per issue #199) will make this
        operate on the user's current selection – "select text, call
        up Clipboard, navigate to prompt, Enter, prompt runs on the
        selection".
        """
        try:
            pm = getattr(self._parent_app, 'prompt_manager_qt', None)
            if not pm:
                return
            lib = getattr(pm, 'library', None)
            if not lib or not hasattr(lib, 'get_quicklauncher_grid_prompts'):
                return

            items = lib.get_quicklauncher_grid_prompts() or []
            if not items:
                return

            from collections import defaultdict
            folders = defaultdict(list)
            for rel_path, label in items:
                parts = rel_path.replace('\\', '/').split('/')
                folder = parts[0] if len(parts) > 1 else "Prompts"
                display = label or parts[-1].replace('.md', '')
                folders[folder].append((rel_path, display))

            prompts_cat = self._make_action_category("\U0001F4DD Prompts", expanded=False)

            for folder, folder_items in sorted(folders.items()):
                if len(folders) == 1 and folder == "Prompts":
                    parent = prompts_cat
                else:
                    sub_cat = QTreeWidgetItem([f"\U0001F4C1 {folder}"])
                    sub_cat.setData(0, Qt.ItemDataRole.UserRole, self._CATEGORY_SENTINEL)
                    prompts_cat.addChild(sub_cat)
                    parent = sub_cat

                for rel_path, display in sorted(folder_items, key=lambda x: x[1].lower()):
                    self._add_action_leaf(
                        parent,
                        f"{self._LEAF_ICON} {display}",
                        lambda p=rel_path: self._activate_prompt(p),
                    )
        except Exception as e:
            print(f"[ClipboardManagerWidget] Prompt population error: {e}")

    def _activate_prompt(self, rel_path: str):
        """Look up a prompt by its relative path and copy its body to
        the system clipboard. v1.10.2 placeholder; v1.10.x will replace
        this with "fire the prompt against the user's current
        selection" once the cross-app capture plumbing is in place.

        Uses the same ``lib.prompts.get(rel_path)`` lookup Sidekick's
        ``_on_prompt_action`` uses – the prompt library indexes prompts
        by their relative path and returns a dict with ``content`` /
        ``name`` keys. Falls back gracefully if the prompt is missing.
        """
        try:
            pm = getattr(self._parent_app, 'prompt_manager_qt', None)
            if not pm:
                return
            lib = getattr(pm, 'library', None)
            if not lib:
                return
            prompts = getattr(lib, 'prompts', None)
            if prompts is None:
                return
            prompt_data = prompts.get(rel_path)
            if not prompt_data:
                return
            body = (prompt_data.get('content') or "").strip()
            if body:
                self._copy_to_clipboard(body)
        except Exception as e:
            print(f"[ClipboardManagerWidget] Prompt activation error: {e}")
