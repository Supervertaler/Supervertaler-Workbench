"""The translation grid's cell editors, highlighters, delegates and keyboard
event filters. Moved unchanged from Supervertaler.py.
"""

import os
import re
from PyQt6.QtCore import (
    QObject,
    QRectF,
    QSize,
    QTimer,
    Qt,
)
from PyQt6.QtGui import (
    QAction,
    QColor,
    QFont,
    QPalette,
    QSyntaxHighlighter,
    QTextCharFormat,
    QTextCursor,
    QTextOption,
)
from PyQt6.QtWidgets import (
    QAbstractItemDelegate,
    QApplication,
    QLabel,
    QMenu,
    QMessageBox,
    QStyle,
    QStyleOptionViewItem,
    QStyledItemDelegate,
    QTableWidget,
    QTextEdit,
)
from dataclasses import field
from modules import inline_codes as _inline_codes
from modules.platform_helpers import IS_MACOS
from modules.shortcut_display import format_shortcut_for_display
from typing import Dict, Optional
from modules.tag_text import WORD_JOINER, expand_compact_tags, extract_html_tags, extract_memoq_tags, find_next_unused_tag, get_formatted_html_display, get_html_wrapping_tag_pair, get_next_pipe_count_needed, get_wrapping_tag_pair, has_formatting_tags, has_phrase_tags, strip_invisible_markers


def _cleaned_modifiers(event):
    """Return event modifiers with KeypadModifier stripped.

    macOS Qt sets KeypadModifier on arrow keys (and Home/End/PageUp/PageDown)
    even on MacBook keyboards without a numpad. Bare equality checks like
    `event.modifiers() == Qt.KeyboardModifier.NoModifier` therefore fail on
    macOS, breaking arrow-key segment navigation in the grid. This helper
    returns the modifiers with KeypadModifier removed so equality checks
    work cross-platform.
    """
    return event.modifiers() & ~Qt.KeyboardModifier.KeypadModifier


class _QuitEventFilter(QObject):
    """App-level filter that distinguishes a real quit from a window close.

    v1.10.238: The main window's closeEvent backgrounds the app to the system
    tray / dock (the deliberate "close to tray" behaviour) instead of quitting,
    so clicking the window's X (Windows) or red close button (macOS) keeps
    Supervertaler running for the SuperLookup global hotkey.

    But an *explicit* quit — macOS "Quit Supervertaler" / ⌘Q, or a system
    shutdown — must genuinely quit. Those arrive as a ``QEvent.Quit`` on the
    application object; we set ``main_window._really_quit`` when we see one, so
    the subsequent closeEvent accepts and terminates instead of hiding. We do
    not consume the event, so Qt's normal quit sequence proceeds.
    """

    def __init__(self, main_window):
        super().__init__(main_window)
        self._main_window = main_window

    def eventFilter(self, obj, event):
        from PyQt6.QtCore import QEvent
        if event.type() == QEvent.Type.Quit:
            try:
                self._main_window._really_quit = True
            except Exception:
                pass
        return False


class _CtrlReturnEventFilter(QObject):
    """App-level event filter to catch Ctrl+Return/Ctrl+Enter for grid confirm.

    This is a workaround for cases where the main keyboard Return key (Key_Return)
    is swallowed before it reaches the source cell editor or QShortcut.
    """

    def __init__(self, main_window):
        super().__init__(main_window)
        self._main_window = main_window

    def eventFilter(self, obj, event):
        from PyQt6.QtCore import QEvent
        from PyQt6.QtWidgets import QApplication

        if event.type() != QEvent.Type.KeyPress:
            return False

        # Only handle Ctrl+Return/Ctrl+Enter (ignore Shift/Alt variants)
        mods = event.modifiers()
        if not (mods & Qt.KeyboardModifier.ControlModifier):
            return False
        if mods & (Qt.KeyboardModifier.ShiftModifier | Qt.KeyboardModifier.AltModifier):
            return False
        if event.key() not in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            return False

        mw = self._main_window
        if not mw or not mw.isActiveWindow():
            return False

        table = getattr(mw, 'table', None)
        if table is None:
            return False

        focus = QApplication.focusWidget()
        if focus is None:
            return False

        # Allow triggering when focus is in the grid itself OR in the grid's filter boxes.
        # This keeps the shortcut useful while typing filters (e.g. "Filter Source" box).
        source_filter = getattr(mw, 'source_filter', None)
        target_filter = getattr(mw, 'target_filter', None)
        if focus in (source_filter, target_filter) or focus is table or focus is table.viewport() or table.isAncestorOf(focus) or isinstance(focus, (ReadOnlyGridTextEditor, EditableGridTextEditor)):
            if hasattr(mw, 'confirm_selected_or_next'):
                mw.confirm_selected_or_next()
                return True

        return False


class _GridArrowKeyEventFilter(QObject):
    """App-level event filter to catch Alt+Up/Down and Ctrl+Up/Down in grid cells.

    QAbstractItemView (parent of QTableWidget) intercepts Up/Down arrow keys at
    the viewport level for row navigation, preventing them from ever reaching the
    cell widget's keyPressEvent or event() methods.  Alt+Left is NOT intercepted
    by Qt (horizontal arrow keys are not used for row navigation), which is why
    that shortcut works fine without this filter.

    This filter sits at the application level – above the viewport – and catches
    the four affected key combos when focus is inside a grid cell, then calls the
    appropriate handler directly.
    """

    def __init__(self, main_window):
        super().__init__(main_window)
        self._main_window = main_window

    def eventFilter(self, obj, event):
        from PyQt6.QtCore import QEvent
        from PyQt6.QtWidgets import QApplication

        if event.type() != QEvent.Type.KeyPress:
            return False

        mw = self._main_window
        if not mw or not mw.isActiveWindow():
            return False

        key = event.key()
        mods = _cleaned_modifiers(event)  # strip macOS KeypadModifier from arrow keys

        # Ctrl+Shift+S with 2+ rows selected: copy source → target for every
        # selected segment. The per-cell editor handles the single-cell case in
        # its own keyPressEvent, but a multi-row selection means focus is on the
        # table (not a cell editor), so that handler never fires – which is why
        # Ctrl+Shift+S appeared to do nothing on a multi-segment selection. We
        # intercept here only when the grid has focus and more than one row is
        # selected, leaving the single-cell path completely untouched.
        if (mods == (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier)
                and key == Qt.Key.Key_S):
            if (hasattr(mw, 'get_selected_segments_from_grid')
                    and hasattr(mw, '_copy_source_to_target_selected')):
                table = getattr(mw, 'table', None)
                focus = QApplication.focusWidget()
                focus_in_grid = (
                    table is not None and focus is not None and (
                        focus is table
                        or table.isAncestorOf(focus)
                        or isinstance(focus, (ReadOnlyGridTextEditor, EditableGridTextEditor))
                    )
                )
                if focus_in_grid:
                    selected = mw.get_selected_segments_from_grid()
                    if len(selected) > 1:
                        mw._copy_source_to_target_selected(selected)
                        return True
            return False

        # v1.10.229: Plain F2 on a focused source cell → enter ephemeral
        # source-edit mode (memoQ / Trados convention). QAbstractItemView
        # intercepts F2 at the viewport level for "edit current item", so
        # the cell widget's own keyPressEvent never sees it — we have to
        # catch F2 here, at app level, before the table swallows it.
        # Disqualify any modifier so Shift+F2 / Ctrl+F2 stay free for
        # other bindings.
        _disqualifying = (
            Qt.KeyboardModifier.ControlModifier
            | Qt.KeyboardModifier.ShiftModifier
            | Qt.KeyboardModifier.AltModifier
            | Qt.KeyboardModifier.MetaModifier
        )
        if key == Qt.Key.Key_F2 and not (mods & _disqualifying):
            focus = QApplication.focusWidget()
            if isinstance(focus, ReadOnlyGridTextEditor):
                # Locked segments stay read-only no matter what.
                if getattr(focus, '_segment_locked', False):
                    return True
                # If the global setting is already on, F2 is a deliberate
                # no-op — the cell is permanently editable and we don't
                # want focus-out to silently revert it later.
                if getattr(mw, 'allow_replace_in_source', False):
                    return True
                if getattr(focus, '_ephemeral_source_edit', False):
                    focus._disable_ephemeral_source_edit()
                else:
                    focus._enable_ephemeral_source_edit()
                return True
            return False

        # v1.10.229: Esc exits ephemeral source-edit mode (changes are kept —
        # the textChanged handler has been writing them to segment.source
        # live as the user types, just like click-away does). Only consume
        # Esc when ephemeral edit is actually active on the focused source
        # cell; otherwise let it pass through so it can still cancel
        # dialogs, clear selections, etc.
        if key == Qt.Key.Key_Escape and not (mods & _disqualifying):
            focus = QApplication.focusWidget()
            if (isinstance(focus, ReadOnlyGridTextEditor)
                    and getattr(focus, '_ephemeral_source_edit', False)):
                focus._disable_ephemeral_source_edit()
                return True
            return False

        # Only handle the four combos we care about
        alt_only = mods == Qt.KeyboardModifier.AltModifier
        ctrl_only = mods == Qt.KeyboardModifier.ControlModifier

        is_our_combo = (
            (alt_only  and key in (Qt.Key.Key_Up, Qt.Key.Key_Down)) or
            (ctrl_only and key in (Qt.Key.Key_Up, Qt.Key.Key_Down))
        )
        if not is_our_combo:
            return False

        # Only act when focus is inside a grid cell widget
        focus = QApplication.focusWidget()
        if not isinstance(focus, (ReadOnlyGridTextEditor, EditableGridTextEditor)):
            return False

        if alt_only:
            if key == Qt.Key.Key_Up:
                focus._handle_quick_add_to_glossary_priority(1)
            else:
                focus._handle_quick_add_to_glossary_priority(2)
            return True

        if ctrl_only:
            # Use the focused widget's stored row for reliable navigation.
            # table.currentRow() can be stale after rapid Ctrl+Up/Down because
            # setCurrentCell + setFocus triggers asynchronous Qt signals.
            table = getattr(mw, 'table', None)
            if table is None:
                return False
            widget_row = getattr(focus, 'row', -1)
            actual_row = widget_row if widget_row >= 0 else table.currentRow()

            if key == Qt.Key.Key_Up:
                new_row = actual_row - 1
                while new_row > 0 and table.isRowHidden(new_row):
                    new_row -= 1
                if new_row >= 0 and not table.isRowHidden(new_row):
                    table.clearSelection()
                    table.setCurrentCell(new_row, 3)
                    target_widget = table.cellWidget(new_row, 3)
                    if target_widget:
                        target_widget.setFocus()
                        from PyQt6.QtGui import QTextCursor
                        target_widget.moveCursor(QTextCursor.MoveOperation.End)
            elif key == Qt.Key.Key_Down:
                new_row = actual_row + 1
                while new_row < table.rowCount() - 1 and table.isRowHidden(new_row):
                    new_row += 1
                if new_row < table.rowCount() and not table.isRowHidden(new_row):
                    table.clearSelection()
                    table.setCurrentCell(new_row, 3)
                    target_widget = table.cellWidget(new_row, 3)
                    if target_widget:
                        target_widget.setFocus()
                        from PyQt6.QtGui import QTextCursor
                        target_widget.moveCursor(QTextCursor.MoveOperation.End)
            return True

        return False


class _WheelGuard(QObject):
    """Stops a hovered slider / spinbox / combo from eating the mouse wheel.

    By default ``QSlider``, ``QAbstractSpinBox`` and ``QComboBox`` have
    ``Qt.FocusPolicy.WheelFocus`` and respond to wheel events whenever the
    cursor is over them – even without a click – which means scrolling a
    settings page can silently move a slider you didn't mean to touch. We
    install this filter on those widgets and ignore their wheel events
    *unless* the widget actually has keyboard focus (i.e. the user clicked
    or tabbed into it). Result: scrolling the page works as expected;
    explicit interaction still does.
    """

    def eventFilter(self, obj, event):
        from PyQt6.QtCore import QEvent
        if event.type() == QEvent.Type.Wheel:
            if not (hasattr(obj, "hasFocus") and obj.hasFocus()):
                event.ignore()
                return True
        return False


class _LoneCtrlEventFilter(QObject):
    """App-level event filter: triggers the term-insert popup when Ctrl is tapped alone.

    A 'lone Ctrl tap' is defined as:
      - Ctrl KeyPress  (non-repeat)
      - Ctrl KeyRelease (non-repeat)
      - with NO other key pressed, wheel-scrolled, or shortcut fired in between

    This mimics memoQ's behaviour where pressing Ctrl alone opens the glossary /
    non-translatable insert list.  Any other key pressed while Ctrl is held
    (e.g. Ctrl+C, Ctrl+Shift+Q) cancels the detection and lets the normal
    shortcut fire as usual.

    Three cancellation layers guard against false positives:
      1. KeyPress of any non-Ctrl key → immediate cancel  (catches normal shortcuts)
      2. WheelEvent while Ctrl is held → cancel            (catches Ctrl+scroll zoom)
      3. ShortcutOverride event → cancel                   (catches registered QShortcuts
                                                            whose key event may be consumed
                                                            before reaching layer 1)
      4. queryKeyboardModifiers() on Ctrl release → cancel if any other modifier is
         currently held (catches OS-level shortcuts that swallow intermediate keys,
         e.g. Ctrl+Alt+Dash input-method shortcuts on Windows)
    """

    def __init__(self, main_window):
        super().__init__(main_window)
        self._main_window = main_window
        self._ctrl_pressed_alone = False

    def eventFilter(self, obj, event):
        from PyQt6.QtCore import QEvent
        from PyQt6.QtWidgets import QApplication

        try:
            if not self._main_window or not self._main_window.isActiveWindow():
                return False

            etype = event.type()

            # ── Layer 2: Ctrl+scroll-wheel (zoom, etc.) ───────────────────────
            if etype == QEvent.Type.Wheel:
                self._ctrl_pressed_alone = False
                return False

            # ── Layer 3: a registered QShortcut is about to fire ─────────────
            # ShortcutOverride is sent to the focus widget just before a shortcut
            # activates; it fires even when the underlying KeyPress event is later
            # consumed, so we catch Ctrl+= / Ctrl+- / Ctrl+Alt+… shortcuts here.
            if etype == QEvent.Type.ShortcutOverride:
                self._ctrl_pressed_alone = False
                return False

            # ── Layer 1: key presses ──────────────────────────────────────────
            if etype == QEvent.Type.KeyPress:
                if event.isAutoRepeat():
                    self._ctrl_pressed_alone = False
                    return False
                if event.key() == Qt.Key.Key_Control:
                    self._ctrl_pressed_alone = True
                else:
                    self._ctrl_pressed_alone = False
                return False

            # ── Ctrl release: fire only when all layers agree ─────────────────
            if etype == QEvent.Type.KeyRelease:
                if event.key() == Qt.Key.Key_Control and not event.isAutoRepeat():
                    if self._ctrl_pressed_alone:
                        # Layer 4: live OS modifier state – catches OS-swallowed shortcuts
                        # (e.g. AltGr/Ctrl+Alt combinations that consume intermediate keys)
                        live = QApplication.queryKeyboardModifiers()
                        other = live & ~Qt.KeyboardModifier.ControlModifier
                        if other == Qt.KeyboardModifier.NoModifier:
                            self._ctrl_pressed_alone = False
                            self._main_window.show_term_insert_popup()
                            return True   # consume this release event
                    self._ctrl_pressed_alone = False
                return False

            return False

        except RuntimeError:
            # The main window's C++ object has been deleted (app is shutting down).
            # Uninstall this filter so it stops firing, then go silent.
            app = QApplication.instance()
            if app:
                app.removeEventFilter(self)
            self._main_window = None
            self._ctrl_pressed_alone = False
            return False


class GridTableEventFilter:
    """Mixin to pass keyboard shortcuts from editor to table"""
    pass


def _anchored_comments_at(main_window, row, field, char_pos):
    """Return a list of (segment, comment) for anchored comments whose character
    range covers ``char_pos`` in the given ``field`` ('source'/'target') of the
    segment shown at grid ``row``.

    Used by the source/target cell editors to power the hover tooltip and the
    "Open comment" right-click item over the yellow anchored-comment highlight.
    Returns an empty list on any miss (no project, bad row, no covering comment).
    """
    result = []
    try:
        if not main_window or not getattr(main_window, 'current_project', None):
            return result
        table = getattr(main_window, 'table', None)
        if table is None or row is None or row < 0:
            return result
        id_item = table.item(row, 0)
        if not id_item:
            return result
        seg_id = int(id_item.text())
        segment = next(
            (s for s in main_window.current_project.segments if s.id == seg_id),
            None,
        )
        if not segment:
            return result
        for c in getattr(segment, 'comments', []) or []:
            if (getattr(c, 'anchor_field', '') == field and c.is_anchored
                    and c.anchor_start <= char_pos < c.anchor_end):
                result.append((segment, c))
    except Exception:
        pass
    return result


def _segment_comments_for_row(main_window, row):
    """Return [(segment, comment), ...] for every comment on the segment shown
    at grid ``row`` — anchored AND segment-level — in stored order. Empty on miss.

    Used for the "Open comment(s)" cell/status right-click actions so
    segment-level comments (which have no highlighted range to click on) are
    still reachable from the grid.
    """
    result = []
    try:
        if not main_window or not getattr(main_window, 'current_project', None):
            return result
        table = getattr(main_window, 'table', None)
        if table is None or row is None or row < 0:
            return result
        id_item = table.item(row, 0)
        if not id_item:
            return result
        seg_id = int(id_item.text())
        segment = next(
            (s for s in main_window.current_project.segments if s.id == seg_id),
            None,
        )
        if not segment:
            return result
        for c in getattr(segment, 'comments', []) or []:
            if c.text and c.text.strip():
                result.append((segment, c))
    except Exception:
        pass
    return result


def _format_comment_tooltip(pairs):
    """Build the hover-tooltip HTML for the anchored comment(s) under the cursor."""
    import html as _html
    blocks = []
    for _seg, c in pairs:
        author = _html.escape(c.author) if getattr(c, 'author', '') else ''
        text = _html.escape(c.text or '').replace('\n', '<br>')
        head = f"<b>💬 {author}</b><br>" if author else "<b>💬 Comment</b><br>"
        blocks.append(head + text)
    body = "<hr>".join(blocks)
    return (body +
            "<br><span style='color:#888;'><i>Right-click → Open comment</i></span>")


def _add_open_comment_actions(editor, menu, field, click_qpoint):
    """Add "Open comment(s)" items to a grid cell editor's context menu.

    If the click lands on an anchored range, add one "Open comment" per covering
    comment (each opens that specific comment). Otherwise, if the segment has any
    comments, add a single "Open comment(s)" that opens the segment's first one —
    this is how segment-level comments (no highlighted range) are reached.

    Returns True if any action was added (so the caller can add a separator).
    """
    from PyQt6.QtGui import QAction
    mw = editor._get_main_window()
    row = getattr(editor, 'row', -1)
    try:
        click_pos = editor.cursorForPosition(click_qpoint).position()
    except Exception:
        click_pos = -1

    def _open(cid):
        if mw and hasattr(mw, '_open_comment_in_panel'):
            mw._open_comment_in_panel(cid)

    anchored = _anchored_comments_at(mw, row, field, click_pos) if click_pos >= 0 else []
    if anchored:
        for _seg, c in anchored:
            act = QAction("💬 Open comment", editor)
            act.triggered.connect(lambda _checked=False, cid=c.id: _open(cid))
            menu.addAction(act)
        return True

    all_pairs = _segment_comments_for_row(mw, row)
    if all_pairs:
        label = ("💬 Open comment" if len(all_pairs) == 1
                 else f"💬 Open comments ({len(all_pairs)})")
        first_cid = all_pairs[0][1].id
        act = QAction(label, editor)
        act.triggered.connect(lambda _checked=False, cid=first_cid: _open(cid))
        menu.addAction(act)
        return True
    return False


class GridTextEditor(QTextEdit):
    """Custom QTextEdit for grid cells that passes special shortcuts to parent"""
    
    table_widget = None  # Will be set by delegate
    assistance_panel = None  # Will be set by delegate
    current_row = None  # Track which row this editor is in
    
    def __init__(self, parent=None):
        super().__init__(parent)
        # Prevent Tab from changing focus - we handle it manually
        self.setTabChangesFocus(False)
    
    def keyPressEvent(self, event):
        """Override keyPressEvent to handle Tab - this is called AFTER event()"""
        # Ctrl+Tab: Insert actual tab character
        if event.key() == Qt.Key.Key_Tab and event.modifiers() == Qt.KeyboardModifier.ControlModifier:
            self.insertPlainText('\t')
            event.accept()
            return
        
        # Tab (without Ctrl): Cycle to source cell
        if event.key() == Qt.Key.Key_Tab:
            if self.table_widget and self.current_row is not None:
                # Close current editor
                self.table_widget.closeEditor(self, QAbstractItemDelegate.EndEditHint.NoHint)
                # Open editor for source cell in same row (column 2)
                source_index = self.table_widget.model().index(self.current_row, 2)
                self.table_widget.setCurrentIndex(source_index)
                self.table_widget.edit(source_index)
                event.accept()
                return
        
        # Handle other Ctrl+ shortcuts
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            # Ctrl+1 through Ctrl+9: Insert match by number
            # TODO: Update to work with new results_panels system
            if event.key() >= Qt.Key.Key_1 and event.key() <= Qt.Key.Key_9:
                # Disabled for now - needs update to work with results_panels
                pass
            # Ctrl+Up/Down: Navigate to previous/next segment
            elif event.key() in (Qt.Key.Key_Up, Qt.Key.Key_Down):
                if self.main_window:
                    if event.key() == Qt.Key.Key_Up and hasattr(self.main_window, 'go_to_previous_segment'):
                        self.main_window.go_to_previous_segment()
                    elif event.key() == Qt.Key.Key_Down and hasattr(self.main_window, 'go_to_next_segment'):
                        self.main_window.go_to_next_segment()
                event.accept()
                return
            # Ctrl+Enter: Confirm & Next (call main window method directly)
            elif event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                if self.main_window and hasattr(self.main_window, 'confirm_selected_or_next'):
                    self.main_window.confirm_selected_or_next()
                event.accept()
                return
            # Ctrl+, (comma): Insert next memoQ tag or wrap selection with tag pair
            elif event.key() == Qt.Key.Key_Comma:
                self._insert_next_tag_or_wrap_selection()
                event.accept()
                return
        
        # Shift+Enter: Insert line break (for multi-line content)
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                super().keyPressEvent(event)
                event.accept()
                return
        
        # Enter/Return: Don't insert, let delegate handle it (will close editor)
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            # Just ignore it - the table's default behavior will close the editor
            event.ignore()
            return
        
        # All other keys: Handle normally (including ESC for closing editor)
        print(f"🔑 GridTextEditor: Passing to super().keyPressEvent")
        super().keyPressEvent(event)
    
    def _insert_next_tag_or_wrap_selection(self):
        """
        Insert the next memoQ tag, HTML tag, or CafeTran pipe symbol from source, or wrap selection.

        Behavior:
        - If text is selected: Wrap it with the next available tag pair [N}selection{N] or <tag>selection</tag> or |selection|
        - If no selection: Insert the next unused tag/pipe from source at cursor position

        Supports:
        - memoQ tags: [1}, {1], [2}, {2], etc.
        - HTML/XML tags: <li>, </li>, <b>, </b>, <i>, </i>, etc.
        - CafeTran pipe symbols: |

        Shortcut: Ctrl+, (comma)
        """
        # Get the main window and current segment
        if not self.table_widget or self.current_row is None:
            return

        # Navigate up to find main window
        main_window = self.table_widget.parent()
        while main_window and not hasattr(main_window, 'current_project'):
            main_window = main_window.parent()

        if not main_window or not hasattr(main_window, 'current_project'):
            return

        if not main_window.current_project or self.current_row >= len(main_window.current_project.segments):
            return

        segment = main_window.current_project.segments[self.current_row]
        source_text = segment.source
        current_target = self.toPlainText()

        # Check what type of tags are in the source
        has_memoq_tags = bool(extract_memoq_tags(source_text))
        has_html_tags = bool(extract_html_tags(source_text))
        has_any_tags = has_memoq_tags or has_html_tags or bool(_inline_codes.find_codes(source_text))
        has_pipe_symbols = '|' in source_text

        # Check if there's a selection
        cursor = self.textCursor()
        if cursor.hasSelection():
            selected_text = cursor.selectedText()

            # Try memoQ tag pair first
            if has_memoq_tags:
                opening_tag, closing_tag = get_wrapping_tag_pair(source_text, current_target)
                if opening_tag and closing_tag:
                    wrapped_text = f"{opening_tag}{selected_text}{closing_tag}"
                    cursor.insertText(wrapped_text)
                    if hasattr(main_window, 'log'):
                        main_window.log(f"🏷️ Wrapped selection with {opening_tag}...{closing_tag}")
                    return

            # Try HTML tag pairs (e.g., <b>...</b>, <i>...</i>)
            if has_html_tags:
                opening_tag, closing_tag = get_html_wrapping_tag_pair(source_text, current_target)
                if opening_tag and closing_tag:
                    wrapped_text = f"{opening_tag}{selected_text}{closing_tag}"
                    cursor.insertText(wrapped_text)
                    if hasattr(main_window, 'log'):
                        main_window.log(f"🏷️ Wrapped selection with {opening_tag}...{closing_tag}")
                    return

            # Try CafeTran pipe symbols
            if has_pipe_symbols:
                pipes_needed = get_next_pipe_count_needed(source_text, current_target)
                if pipes_needed >= 2:
                    # Wrap with pipes
                    wrapped_text = f"|{selected_text}|"
                    cursor.insertText(wrapped_text)
                    if hasattr(main_window, 'log'):
                        main_window.log(f"🏷️ Wrapped selection with |...|")
                    return

            if hasattr(main_window, 'log'):
                main_window.log("⚠️ No tag pairs available from source")
        else:
            # No selection - insert next unused tag or pipe at cursor

            # Try memoQ tags and HTML tags (find_next_unused_tag handles both)
            if has_any_tags:
                # In compact mode, expand target placeholders before comparison
                compact_map = getattr(self, '_compact_tag_map', None)
                compare_target = expand_compact_tags(current_target, compact_map) if compact_map else current_target
                next_tag = find_next_unused_tag(source_text, compare_target)
                if next_tag:
                    # In compact mode, insert the compact placeholder instead of full tag
                    insert_text = next_tag
                    if compact_map:
                        for placeholder, full_tag in compact_map.items():
                            if full_tag == next_tag:
                                insert_text = placeholder
                                break
                    cursor.insertText(insert_text)
                    if hasattr(main_window, 'log'):
                        main_window.log(f"🏷️ Inserted tag: {insert_text}")
                    return

            # Try CafeTran pipe symbols
            if has_pipe_symbols:
                pipes_needed = get_next_pipe_count_needed(source_text, current_target)
                if pipes_needed > 0:
                    cursor.insertText('|')
                    if hasattr(main_window, 'log'):
                        main_window.log(f"🏷️ Inserted pipe symbol (|)")
                    return

            if hasattr(main_window, 'log'):
                main_window.log("✓ All tags from source already in target")


# Characters that may sit *inside* a word and must never be trimmed from the
# edges of a double-click selection (apostrophes, hyphens – incl. their Unicode
# variants). Decimal separators (. ,) are handled by the trim logic only
# trimming at the edges, so "7,5" / "55.5" keep their internal separators.
_WORD_INTERNAL_PUNCT = {"'", "’", "ʼ", "-", "‐", "‑"}


def _is_edge_trim_char(ch: str) -> bool:
    """True if `ch` is punctuation (or a zero-width marker) that should be
    trimmed from the start/end of a double-clicked word selection."""
    import unicodedata
    if ch in _WORD_INTERNAL_PUNCT:
        return False
    if ch == '​':  # zero-width space (part of '·​' space markers)
        return True
    return unicodedata.category(ch).startswith('P')


def _is_cjk_char(ch: str) -> bool:
    """True for characters in scripts that don't put spaces between words
    (Chinese, Japanese, Korean, Thai). Word-by-word selection expansion must
    not run across these – each character is effectively its own token, so
    expanding would swallow the whole run (``str.isalnum()`` is True for them,
    which is exactly what made drag-selecting one Chinese character grab the
    entire cell)."""
    o = ord(ch)
    return (
        0x4E00 <= o <= 0x9FFF or    # CJK Unified Ideographs
        0x3400 <= o <= 0x4DBF or    # CJK Extension A
        0x3040 <= o <= 0x30FF or    # Hiragana + Katakana
        0xAC00 <= o <= 0xD7AF or    # Hangul syllables
        0x0E00 <= o <= 0x0E7F or    # Thai
        0xF900 <= o <= 0xFAFF or    # CJK Compatibility Ideographs
        0x20000 <= o <= 0x2FA1F     # CJK Extension B and beyond
    )


def _contains_cjk(text: str) -> bool:
    """True if any character is in a space-less script (Chinese, Japanese,
    Korean, Thai). Used to switch term matching from word-boundary (\\bterm\\b)
    to substring: those scripts write words with no separating spaces and
    attach particles/compounds directly to a term (값으로, 第二电压値), so a
    word-boundary never fires inside a run and the term would never match.
    Substring matching is the right behaviour for such terms; European terms
    keep word boundaries because they don't contain these characters."""
    return bool(text) and any(_is_cjk_char(c) for c in text)


def _canon_path(p: str) -> str:
    """Canonical path for case/separator-insensitive comparison (on Windows the
    same file can be spelled with different drive-letter case or slashes)."""
    try:
        return os.path.normcase(os.path.normpath(os.path.abspath(p or '')))
    except Exception:
        return os.path.normcase(os.path.normpath(p or ''))


def select_word_under_cursor(widget, event, doc_pos=None) -> bool:
    """Select the word under the double-click position, excluding adjacent
    punctuation (e.g. a trailing period) and invisible-character markers.

    Used by both source and target grid cells so selection behaves identically
    whether or not invisible characters are displayed. Word boundaries come from
    Qt's Unicode segmenter (QTextBoundaryFinder, UAX #29), so selection is
    correct for space-less scripts such as Chinese, Japanese and Thai too – the
    previous whitespace-delimiter scan found no delimiter in CJK text and ran to
    both ends, selecting the entire cell. Word-internal apostrophes and hyphens
    are preserved (don't, well-known); decimal separators inside a number are
    kept because only the *edges* of the selection are trimmed.

    ``doc_pos`` (issue #240): a character offset captured at *press* time, before
    activating the cell can reflow/scroll its text. When given we use it instead
    of mapping the double-click's pixel position — so the correct word is picked
    even if the text scooted between the two clicks. Falls back to the live
    pixel position when None.

    Returns True if a selection was made, False if the caller should fall back
    to the default handler (e.g. empty cell).
    """
    from PyQt6.QtGui import QTextCursor
    from PyQt6.QtCore import QTextBoundaryFinder

    text = widget.toPlainText()
    n = len(text)
    if n == 0:
        return False

    if doc_pos is not None:
        pos = min(max(0, doc_pos), n)
    else:
        pos = min(widget.cursorForPosition(event.pos()).position(), n)
    cursor = widget.textCursor()

    # Unicode word boundaries (UAX #29) around the click position. For CJK and
    # other space-less scripts this picks a sensible character/word boundary
    # instead of running to both ends of the text.
    bf = QTextBoundaryFinder(QTextBoundaryFinder.BoundaryType.Word, text)
    bf.setPosition(pos)
    start = pos if bf.isAtBoundary() else bf.toPreviousBoundary()
    if start < 0:
        start = 0
    bf.setPosition(start)
    end = bf.toNextBoundary()
    if end < 0:
        end = n

    # Trim punctuation / zero-width markers from both edges, but never trim away
    # the entire token (e.g. a double-click directly on a standalone "(").
    ts, te = start, end
    while ts < te and _is_edge_trim_char(text[ts]):
        ts += 1
    while te > ts and _is_edge_trim_char(text[te - 1]):
        te -= 1
    if ts < te:
        start, end = ts, te

    # Nothing left to select (e.g. double-click past the end of the text, or a
    # run that trimmed away entirely) – let the default handler deal with it.
    if start >= end:
        return False

    cursor.setPosition(start)
    cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
    widget.setTextCursor(cursor)
    return True


def select_whole_segment(widget) -> bool:
    """Select the entire text of a grid cell – the triple-click gesture, which
    (like triple-clicking a paragraph in a text editor) grabs the whole segment
    in one go for replacing or copying. Returns False on an empty cell so the
    caller can fall through to the default handler. (#240 follow-up.)"""
    from PyQt6.QtGui import QTextCursor
    if not widget.toPlainText():
        return False
    cursor = widget.textCursor()
    cursor.select(QTextCursor.SelectionType.Document)
    widget.setTextCursor(cursor)
    return True


def is_triple_click(widget, event) -> bool:
    """True when ``event`` is the third click of a triple-click: a left press
    that arrives within the double-click interval of this widget's last
    double-click (whose timestamp is stashed in ``_last_dblclick_ts``)."""
    try:
        from PyQt6.QtWidgets import QApplication
        last = getattr(widget, '_last_dblclick_ts', 0)
        return (event.button() == Qt.MouseButton.LeftButton and bool(last)
                and (event.timestamp() - last) <= QApplication.doubleClickInterval())
    except Exception:
        return False


class _GridStyleBatch:
    """Coalesces grid-cell stylesheet rebuilds while a row is built (issue #185).

    setStyleSheet re-polishes the widget, and building a row used to rebuild
    each editor's sheet three times (constructor, font size, row colour) –
    a third of the time it takes to fill a page of the grid. Inside
    ``with _GridStyleBatch():`` a rebuild is only noted, and each editor gets
    its final sheet once, on exit.
    """
    depth = 0
    pending = {}  # id(widget) → widget

    def __enter__(self):
        _GridStyleBatch.depth += 1
        return self

    def __exit__(self, *exc):
        _GridStyleBatch.depth -= 1
        if _GridStyleBatch.depth == 0:
            widgets = list(_GridStyleBatch.pending.values())
            _GridStyleBatch.pending.clear()
            for widget in widgets:
                try:
                    widget._rebuild_grid_stylesheet()
                except RuntimeError:
                    pass  # deleted before the row was finished
        return False

    @classmethod
    def defer(cls, widget) -> bool:
        """True (and the rebuild noted) while a batch is open."""
        if cls.depth:
            cls.pending[id(widget)] = widget
            return True
        return False


class ReadOnlyGridTextEditor(QTextEdit):
    """Read-only QTextEdit for source cells - allows easy text selection"""
    
    # Class variable for tag highlight color (shared across all instances)
    tag_highlight_color = '#7f0001'  # Default memoQ dark red
    
    table_widget = None  # Will be set by delegate
    current_row = None  # Track which row this editor is in
    allow_source_edit = False  # Will be set by delegate based on settings

    def __init__(self, text: str = "", parent=None, row: int = -1, allow_edit: bool = False):
        super().__init__(parent)
        self.row = row  # Store row number for Tab cycling
        self.table_ref = parent  # Store table reference (parent is the table)
        # v1.10.229: Source cells become editable when the "Allow Replace in
        # Source Text" setting is on. Prior to this fix the cell widget was
        # always read-only because setReadOnly(True) was unconditional here —
        # the delegate-based source-edit path in WordWrapDelegate was dead
        # code (cell widgets pre-empt the delegate's createEditor), so the
        # warning banner appeared but typing into the source did nothing.
        self.allow_source_edit = allow_edit
        # v1.10.229: per-cell ephemeral F2 edit mode (memoQ / Trados style).
        # Distinct from allow_source_edit which mirrors the global setting.
        self._ephemeral_source_edit = False
        self.setReadOnly(not allow_edit)
        self.setPlainText(text)

        # CRITICAL: Enable keyboard focus and text selection
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        # Enable text interaction: when editable, allow the full editor
        # interaction set so typing works; otherwise keep it limited to
        # selection so the cell still behaves like a read-only source.
        if allow_edit:
            self.setTextInteractionFlags(Qt.TextInteractionFlag.TextEditorInteraction)
        else:
            self.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByKeyboard |
                Qt.TextInteractionFlag.TextSelectableByMouse
            )

        # Make inactive selections stay visible with same color
        from PyQt6.QtGui import QPalette
        palette = self.palette()
        palette.setColor(QPalette.ColorGroup.Active, QPalette.ColorRole.Highlight, QColor("#000000"))
        palette.setColor(QPalette.ColorGroup.Active, QPalette.ColorRole.HighlightedText, QColor("#FFFFFF"))
        palette.setColor(QPalette.ColorGroup.Inactive, QPalette.ColorRole.Highlight, QColor("#000000"))
        palette.setColor(QPalette.ColorGroup.Inactive, QPalette.ColorRole.HighlightedText, QColor("#FFFFFF"))
        self.setPalette(palette)

        # Style to look like a normal cell with subtle selection.
        # Background and text colors now managed by theme system.
        # Font size lives in this widget's own stylesheet (not just setFont) so
        # it wins over ThemeManager's app-level `QTextEdit { font-size }` rule
        # that gets emitted whenever the global UI scale != 100% – without this
        # override, grid zoom and per-cell setFont would silently lose to that
        # app-level rule at 125% / 150% etc.
        self._grid_font_pt: float | None = None
        self._grid_bg_color: str | None = None
        self._rebuild_grid_stylesheet()

        # Set document margins to 0 for compact display
        doc = self.document()
        doc.setDocumentMargin(0)

        # Configure text option for minimal line spacing
        # With zero-width spaces inserted after invisible character markers,
        # normal WordWrap will work correctly
        wrap_mode = QTextOption.WrapMode.WordWrap

        self.setWordWrapMode(wrap_mode)
        self.setAcceptRichText(False)
        # v1.10.96 — see EditableGridTextEditor for the rationale.
        # Source cell uses the same row-auto-resize machinery so a
        # spurious scrollbar from QTextEdit's AsNeeded heuristic was
        # showing on the source side too.
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        text_option = QTextOption()
        text_option.setWrapMode(wrap_mode)
        doc.setDefaultTextOption(text_option)

        # Set minimum height to 0 - let content determine size
        self.setMinimumHeight(0)
        self.setMaximumHeight(16777215)  # Qt's max int

        # Store termbase matches for this cell (for tooltip and double-click)
        self.termbase_matches = {}
        
        # Enable mouse tracking for hover tooltips
        self.setMouseTracking(True)

        # Add syntax highlighter for tags (no spellcheck for source cells)
        # Get invisible char color and tag color from main window (theme-aware)
        main_window = self._get_main_window()
        invisible_char_color = main_window.invisible_char_color if main_window and hasattr(main_window, 'invisible_char_color') else '#999999'

        # Use theme-aware tag color (light pink in dark mode, dark red in light mode)
        is_dark = main_window and hasattr(main_window, 'theme_manager') and main_window.theme_manager and main_window.theme_manager.current_theme.name == "Dark"
        tag_color = '#FFB6C1' if is_dark else self.tag_highlight_color  # Light pink in dark mode

        self.highlighter = TagHighlighter(self.document(), tag_color, invisible_char_color, enable_spellcheck=False)

        # Store raw text (with tags) for mode switching
        self._raw_text = text
    
    def update_display_mode(self, text: str, show_tags: bool):
        """
        Update the display based on tag view mode.
        
        Args:
            text: The raw text (with HTML tags like <b>bold</b>)
            show_tags: If True, show raw tags. If False, show formatted WYSIWYG.
        """
        self._raw_text = text
        
        if show_tags:
            # Show raw tags as plain text
            self.setPlainText(text)
        else:
            # Show WYSIWYG - convert tags to actual formatting
            html = get_formatted_html_display(text)
            self.setHtml(html)
    
    def get_raw_text(self) -> str:
        """Get the raw text with tags, regardless of display mode."""
        return getattr(self, '_raw_text', self.toPlainText())

    def highlight_termbase_matches(self, matches_dict: Dict):
        """
        Highlight termbase matches in the text using the configured style.
        Does NOT change the widget - just adds formatting to existing text.
        
        Supported styles (configured in Settings > View Settings):
        - 'background': Pastel background colors for project/background glossary (default)
        - 'dotted': Subtle dotted underline (IDE/code editor style)
        - 'semibold': Slightly bolder text with tinted color (typographic)

        Args:
            matches_dict: Dictionary of {term: {'translation': str, ...}} or {term: str}
        """
        from PyQt6.QtGui import QTextCursor, QTextCharFormat, QColor, QFont

        # Remember what we highlighted so the hover tooltip (mouseMoveEvent)
        # always reflects the *currently highlighted* terms. Previously only one
        # render path stored this separately, so background/prefetch and other
        # re-highlight paths left self.termbase_matches stale or empty: the green
        # highlight showed but the tooltip found no match at the cursor and
        # cleared itself, so it appeared once then vanished on the next move.
        self.termbase_matches = matches_dict if matches_dict else {}

        # Get the document and create a cursor
        doc = self.document()
        display_text = self.toPlainText()

        # Build a mapping from clean-text positions to display-text positions
        # so term searches work even when invisible markers (·, →, °, ↵, \u200B) are present.
        MARKER_CHARS = frozenset('·→↵\u200B')
        clean_chars = []
        clean_to_display = []  # clean_to_display[i] = display position of clean char i
        for di, ch in enumerate(display_text):
            if ch not in MARKER_CHARS:
                clean_to_display.append(di)
                clean_chars.append(ch)
        # Sentinel for end-of-text mapping
        clean_to_display.append(len(display_text))
        text = ''.join(clean_chars)
        text_lower = text.lower()
        
        # IMPORTANT: Always clear all previous formatting first to prevent inconsistent highlighting
        cursor = QTextCursor(doc)
        cursor.select(QTextCursor.SelectionType.Document)
        default_fmt = QTextCharFormat()
        cursor.setCharFormat(default_fmt)
        
        # If no matches, we're done (highlighting has been cleared)
        if not matches_dict:
            return
        
        # Get highlight style and hide_shorter setting from main window
        # v1.10.211: default switched from 'background' to 'semibold' to
        # match the rest of the codebase (runtime default has been 'semibold'
        # for several versions; these fallbacks were stragglers).
        highlight_style = 'semibold'  # default
        dotted_color = '#808080'  # default medium gray (more visible)
        hide_shorter = False  # default: show all glossary matches
        parent = self.parent()
        while parent:
            if hasattr(parent, 'termbase_highlight_style'):
                highlight_style = getattr(parent, 'termbase_highlight_style', 'semibold')
                dotted_color = getattr(parent, 'termbase_dotted_color', '#808080')
                hide_shorter = getattr(parent, 'termbase_hide_shorter_matches', False)
                break
            elif hasattr(parent, 'load_general_settings'):
                settings = parent.load_general_settings()
                highlight_style = settings.get('termbase_highlight_style', 'semibold')
                dotted_color = settings.get('termbase_dotted_color', '#808080')
                hide_shorter = settings.get('termbase_hide_shorter_matches', False)
                break
            parent = parent.parent() if hasattr(parent, 'parent') else None

        # Sort matches by source term length (longest first) to avoid partial matches
        # Since dict keys are now term_ids, we need to extract source terms first
        term_entries = []
        for term_id, match_info in matches_dict.items():
            if isinstance(match_info, dict):
                source_term = match_info.get('source', '')
                if source_term:
                    term_entries.append((source_term, term_id, match_info))

        # Sort by source term length (longest first)
        term_entries.sort(key=lambda x: len(x[0]), reverse=True)
        
        # Track positions we've already highlighted to avoid overlaps
        highlighted_ranges = []
        found_count = 0
        
        for term, term_id, match_info in term_entries:
            # Get ranking, forbidden status, and termbase type
            ranking = match_info.get('ranking', None)
            forbidden = match_info.get('forbidden', False)
            is_project_termbase = match_info.get('is_project_termbase', False)
            translation = match_info.get('target', match_info.get('translation', ''))
            notes = match_info.get('notes', '')
            termbase_name = match_info.get('termbase_name', '')
            
            # IMPORTANT: Treat ranking #1 as project termbase (even if flag not set)
            is_effective_project = is_project_termbase or (ranking == 1)
            
            # Build tooltip text
            tooltip_text = ""
            if translation:
                tooltip_text = f"Termbase: {translation}"
                if termbase_name:
                    tooltip_text += f"\nFrom: {termbase_name}"
                if notes:
                    tooltip_text += f"\nNote: {notes}"
            
            # Find all occurrences of this term (case-insensitive)
            term_lower = term.lower()
            start = 0
            while True:
                idx = text_lower.find(term_lower, start)
                if idx == -1:
                    break
                
                end_idx = idx + len(term)
                
                # Decide whether to skip this occurrence based on overlap and the
                # "Hide shorter glossary matches" setting (General Settings checkbox).
                # hide_shorter OFF (default): shorter terms highlight even inside a longer phrase.
                # hide_shorter ON: any overlap with an already-highlighted range → skip.
                overlaps = any(
                    (idx < h_end and end_idx > h_start)
                    for h_start, h_end in highlighted_ranges
                )
                exact_duplicate = (idx, end_idx) in highlighted_ranges
                skip = exact_duplicate or (hide_shorter and overlaps)

                if not skip:
                    # Map clean-text positions back to display-text positions
                    display_start = clean_to_display[idx]
                    display_end = clean_to_display[end_idx]

                    # Create cursor for this position (using display positions)
                    cursor = QTextCursor(doc)
                    cursor.setPosition(display_start)
                    cursor.setPosition(display_end, QTextCursor.MoveMode.KeepAnchor)

                    # Create format based on style
                    fmt = QTextCharFormat()
                    
                    is_project_term = (ranking == 1)

                    if highlight_style == 'dotted':
                        # DOTTED UNDERLINE STYLE
                        fmt.setUnderlineStyle(QTextCharFormat.UnderlineStyle.DotLine)
                        if forbidden:
                            fmt.setUnderlineColor(QColor(0, 0, 0))  # Black for forbidden
                        elif is_project_term:
                            fmt.setUnderlineColor(QColor('#CC0000'))  # Red for project glossary
                        else:
                            fmt.setUnderlineColor(QColor(dotted_color))  # User-configured color
                        if tooltip_text:
                            fmt.setToolTip(tooltip_text)

                    elif highlight_style == 'semibold':
                        # SEMIBOLD TEXT STYLE
                        fmt.setFontWeight(QFont.Weight.DemiBold)
                        if forbidden:
                            fmt.setForeground(QColor(180, 0, 0))  # Dark red for forbidden
                        elif is_project_term:
                            fmt.setForeground(QColor('#1B5E20'))  # Dark green for project glossary
                        else:
                            fmt.setForeground(QColor('#43A047'))  # Lighter green for background
                        if tooltip_text:
                            fmt.setToolTip(tooltip_text)

                    else:
                        # BACKGROUND COLOR STYLE (default)
                        if forbidden:
                            color = QColor(0, 0, 0)  # Black for forbidden terms
                            fmt.setForeground(QColor("white"))
                        else:
                            # Project glossary = medium green, Background = light green
                            color = QColor(165, 214, 167) if is_project_term else QColor(200, 230, 201)
                            fmt.setForeground(QColor("black"))
                        fmt.setBackground(color)
                        if tooltip_text:
                            fmt.setToolTip(tooltip_text)
                    
                    # Apply format
                    cursor.setCharFormat(fmt)
                    found_count += 1
                    
                    # Track this range as highlighted
                    highlighted_ranges.append((idx, end_idx))
                
                start = end_idx
    
    def highlight_non_translatables(self, nt_matches: list, highlighted_ranges: list = None):
        """
        Highlight non-translatable matches in the text using pastel yellow background.
        
        Args:
            nt_matches: List of dicts with 'text', 'start', 'end' keys from NT manager
            highlighted_ranges: Optional list of already-highlighted ranges to avoid overlap
        """
        from PyQt6.QtGui import QTextCursor, QTextCharFormat, QColor
        
        if not nt_matches:
            return
        
        doc = self.document()
        
        # Track ranges we've highlighted (to avoid overlap with termbase matches)
        if highlighted_ranges is None:
            highlighted_ranges = []
        
        # Pastel yellow for non-translatables
        nt_color = QColor(255, 253, 208)  # Pastel yellow (#FFFDD0)
        
        for match in nt_matches:
            start_pos = match.get('start', 0)
            end_pos = match.get('end', 0)
            
            if start_pos >= end_pos:
                continue
            
            # Check for overlap with existing highlights
            overlaps = any(
                (start_pos < h_end and end_pos > h_start)
                for h_start, h_end in highlighted_ranges
            )
            
            if overlaps:
                continue
            
            # Create cursor for this position
            cursor = QTextCursor(doc)
            cursor.setPosition(start_pos)
            cursor.setPosition(end_pos, QTextCursor.MoveMode.KeepAnchor)
            
            # Create format with pastel yellow background
            fmt = QTextCharFormat()
            fmt.setBackground(nt_color)
            fmt.setForeground(QColor("#5D4E37"))  # Dark brown text for contrast
            
            cursor.setCharFormat(fmt)
            highlighted_ranges.append((start_pos, end_pos))
    
    def event(self, event):
        """Override event() to catch Tab and Ctrl+T keys before Qt's default handling"""
        # Catch Tab key at event level (before keyPressEvent)
        if event.type() == event.Type.KeyPress:
            key_event = event
            
            # Ctrl+Alt+T: Add selected terms to termbase (with dialog).
            # Matches the shortcut used by the Trados plugin so muscle
            # memory transfers between products.  Was Ctrl+E before
            # v1.9.475; the old binding is now free for Qt's stock "edit"
            # default if anyone wants it back.
            #
            # Modifier mask: strip KeypadModifier (macOS arrow-key quirk)
            # and GroupSwitchModifier (Windows AltGr) before comparing.
            # Without this, on Windows AltGr-tolerant keyboards the
            # Ctrl+Alt combo carries an extra GroupSwitchModifier bit,
            # the strict equality check fails, the event falls through
            # to the QTextEdit, and the letter T just gets typed into
            # the cell instead of opening the dialog (v1.9.477 fix).
            if key_event.key() == Qt.Key.Key_T:
                _mod_mask = (Qt.KeyboardModifier.ControlModifier
                             | Qt.KeyboardModifier.AltModifier
                             | Qt.KeyboardModifier.ShiftModifier
                             | Qt.KeyboardModifier.MetaModifier)
                _mods = key_event.modifiers() & _mod_mask
                if _mods == (Qt.KeyboardModifier.ControlModifier
                             | Qt.KeyboardModifier.AltModifier):
                    self._handle_add_to_termbase()
                    return True  # Event handled
            
            # Alt+Up: Quick add selected terms to Project glossary (no dialog)
            if key_event.key() == Qt.Key.Key_Up and _cleaned_modifiers(key_event) == Qt.KeyboardModifier.AltModifier:
                self._handle_quick_add_to_glossary_priority(1)
                return True  # Event handled

            # Alt+Down: Quick add selected terms to Background glossary (no dialog)
            if key_event.key() == Qt.Key.Key_Down and _cleaned_modifiers(key_event) == Qt.KeyboardModifier.AltModifier:
                self._handle_quick_add_to_glossary_priority(2)
                return True  # Event handled
            
            # Ctrl+Alt+N: Add selected text to non-translatables
            if key_event.key() == Qt.Key.Key_N and key_event.modifiers() == (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier):
                self._handle_add_to_nt()
                return True  # Event handled
            
            # Ctrl+Up: Navigate to previous segment
            if key_event.key() == Qt.Key.Key_Up and _cleaned_modifiers(key_event) == Qt.KeyboardModifier.ControlModifier:
                main_window = self._get_main_window()
                if main_window and hasattr(main_window, 'go_to_previous_segment'):
                    main_window.go_to_previous_segment()
                return True  # Event handled

            # Ctrl+Down: Navigate to next segment
            if key_event.key() == Qt.Key.Key_Down and _cleaned_modifiers(key_event) == Qt.KeyboardModifier.ControlModifier:
                main_window = self._get_main_window()
                if main_window and hasattr(main_window, 'go_to_next_segment'):
                    main_window.go_to_next_segment()
                return True  # Event handled

            # Ctrl+Home: Navigate to first segment (pass to main window)
            if key_event.key() == Qt.Key.Key_Home and _cleaned_modifiers(key_event) == Qt.KeyboardModifier.ControlModifier:
                main_window = self._get_main_window()
                if main_window and hasattr(main_window, 'go_to_first_segment'):
                    main_window.go_to_first_segment()
                return True  # Event handled

            # Ctrl+End: Navigate to last segment (pass to main window)
            if key_event.key() == Qt.Key.Key_End and _cleaned_modifiers(key_event) == Qt.KeyboardModifier.ControlModifier:
                main_window = self._get_main_window()
                if main_window and hasattr(main_window, 'go_to_last_segment'):
                    main_window.go_to_last_segment()
                return True  # Event handled
            
            if key_event.key() == Qt.Key.Key_Tab:
                # Ctrl+Tab: Insert actual tab character (if editing is allowed)
                if key_event.modifiers() == Qt.KeyboardModifier.ControlModifier:
                    if self.allow_source_edit:
                        self.insertPlainText('\t')
                        return True  # Event handled
                
                # Tab alone: Cycle to target cell (column 3) in same row
                if key_event.modifiers() == Qt.KeyboardModifier.NoModifier:
                    if self.table_ref and self.row >= 0:
                        # Get target cell widget (column 3)
                        target_widget = self.table_ref.cellWidget(self.row, 3)
                        if target_widget:
                            target_widget.setFocus()
                            self.table_ref.setCurrentCell(self.row, 3)
                            return True  # Event handled, don't propagate
        
        # Let base class handle all other events
        return super().event(event)
    
    def keyPressEvent(self, event):
        """Copy and word jumps when invisible characters are shown.

        Every other key is handled earlier: Tab, Ctrl+Up/Down, Alt+Up/Down,
        Ctrl+Alt+T/N and Ctrl+Home/End in event(); F2 and Esc, Ctrl+Enter and
        the arrow keys by the application-wide filters. (Until Nov 2026 a
        second keyPressEvent further down silently replaced this one, so the
        two blocks below never ran in the source cell.)
        """
        from PyQt6.QtCore import Qt
        from PyQt6.QtWidgets import QApplication
        from PyQt6.QtGui import QTextCursor

        # Check for Ctrl+C (copy)
        if event.modifiers() == Qt.KeyboardModifier.ControlModifier and event.key() == Qt.Key.Key_C:
            # Get selected text
            cursor = self.textCursor()
            if cursor.hasSelection():
                selected_text = cursor.selectedText()
                # Reverse invisible character replacements before copying
                main_window = self._get_main_window()
                if main_window and hasattr(main_window, 'reverse_invisible_replacements'):
                    clean_text = main_window.reverse_invisible_replacements(selected_text)
                    # Also replace paragraph separator with newline (Qt uses U+2029)
                    clean_text = clean_text.replace('\u2029', '\n')
                    # Set clipboard with clean text
                    clipboard = QApplication.clipboard()
                    clipboard.setText(clean_text)
                    return  # Don't call parent - we handled it
        
        # Handle Ctrl+Arrow word navigation when invisibles are shown
        main_window = self._get_main_window()
        if main_window and hasattr(main_window, 'invisible_display_settings'):
            if main_window.invisible_display_settings.get('spaces', False):
                ctrl_only = event.modifiers() == Qt.KeyboardModifier.ControlModifier
                ctrl_shift = event.modifiers() == (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier)
                
                if (ctrl_only or ctrl_shift) and event.key() in (Qt.Key.Key_Left, Qt.Key.Key_Right):
                    cursor = self.textCursor()
                    text = self.toPlainText()
                    pos = cursor.position()
                    word_chars = ('·', '\u200B', '\n', '\t', '→', '↵', ' ')
                    
                    if event.key() == Qt.Key.Key_Right:
                        # Move to end of current word, then skip delimiters to start of next word
                        # First skip any delimiters we're on
                        while pos < len(text) and text[pos] in word_chars:
                            pos += 1
                        # Then skip to end of word (next delimiter)
                        while pos < len(text) and text[pos] not in word_chars:
                            pos += 1
                    else:  # Key_Left
                        # Move backwards: skip delimiters, then find start of word
                        if pos > 0:
                            pos -= 1
                        # Skip any delimiters
                        while pos > 0 and text[pos] in word_chars:
                            pos -= 1
                        # Find start of word
                        while pos > 0 and text[pos - 1] not in word_chars:
                            pos -= 1
                    
                    # Apply cursor movement
                    if ctrl_shift:
                        cursor.setPosition(pos, QTextCursor.MoveMode.KeepAnchor)
                    else:
                        cursor.setPosition(pos)
                    self.setTextCursor(cursor)
                    return

        super().keyPressEvent(event)
    
    def sizeHint(self):
        """Return compact size based on content"""
        doc = self.document()
        ideal_width = self.width() if self.width() > 0 else 200
        doc.setTextWidth(ideal_width)
        height = int(doc.size().height())
        # Add minimal padding (2px total)
        height = max(height + 2, 1)
        current_width = self.width() if self.width() > 0 else 200
        return QSize(current_width, height)
    
    def _handle_add_to_termbase(self):
        """Handle Ctrl+E: Add selected source and target terms to glossary (with dialogue)"""
        if not self.table_ref or self.row < 0:
            return

        # Get source selection (from this widget) – strip invisible markers before saving
        source_text = strip_invisible_markers(self.textCursor().selectedText()).strip()

        # Get target cell widget and its selection – strip invisible markers before saving
        target_widget = self.table_ref.cellWidget(self.row, 3)
        target_text = ""
        if target_widget and hasattr(target_widget, 'textCursor'):
            target_text = strip_invisible_markers(target_widget.textCursor().selectedText()).strip()
        
        # Validate we have both selections
        if not source_text or not target_text:
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.warning(
                self,
                "Selection Required",
                "Please select text in both Source and Target cells before adding to termbase.\n\n"
                "Workflow:\n"
                "1. Select term in source cell\n"
                "2. Press Tab to cycle to target cell\n"
                "3. Select corresponding translation\n"
                f"4. Press {format_shortcut_for_display('Ctrl+Alt+T')} (or right-click) to add to termbase"
            )
            return
        
        # Find main window and call add_to_termbase method
        main_window = self.table_ref.parent()
        while main_window and not hasattr(main_window, 'add_term_pair_to_termbase'):
            main_window = main_window.parent()
        
        if main_window and hasattr(main_window, 'add_term_pair_to_termbase'):
            main_window.add_term_pair_to_termbase(source_text, target_text)
    
    def _handle_quick_add_to_glossary_priority(self, priority: int):
        """Handle Alt+Up/Down: Quick add selected terms to the Project/Background glossary (no dialog)"""
        if not self.table_ref or self.row < 0:
            return

        # Strip invisible markers before saving to glossary
        source_text = strip_invisible_markers(self.textCursor().selectedText()).strip()

        target_widget = self.table_ref.cellWidget(self.row, 3)
        target_text = ""
        if target_widget and hasattr(target_widget, 'textCursor'):
            target_text = strip_invisible_markers(target_widget.textCursor().selectedText()).strip()

        if not source_text or not target_text:
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.warning(
                self,
                "Selection Required",
                f"Please select text in both Source and Target cells before quick-adding to the termbase.\n\n"
                f"Tip: Use {format_shortcut_for_display('Ctrl+Alt+T')} to add with a dialogue where you can choose a termbase and add metadata."
            )
            return

        main_window = self.table_ref.parent()
        while main_window and not hasattr(main_window, '_quick_add_term_with_priority'):
            main_window = main_window.parent()

        if main_window and hasattr(main_window, '_quick_add_term_with_priority'):
            main_window._quick_add_term_with_priority(priority)
    
    def _handle_superlookup_search(self):
        """Handle right-click: Search selected text in Superlookup"""
        # Get selected text
        selected_text = self.textCursor().selectedText().strip()
        
        if not selected_text:
            return
        
        # Find main window
        main_window = self._get_main_window()
        if not main_window:
            return
        
        # Get project languages
        source_lang = None
        target_lang = None
        if hasattr(main_window, 'current_project') and main_window.current_project:
            source_lang = main_window.current_project.source_lang
            target_lang = main_window.current_project.target_lang
        
        main_window._go_to_superlookup(
            query=selected_text,
            source_lang=source_lang,
            target_lang=target_lang,
        )

    def mouseMoveEvent(self, event):
        """Show tooltip when hovering over highlighted termbase matches"""
        super().mouseMoveEvent(event)

        # Get cursor position
        cursor = self.cursorForPosition(event.pos())
        cursor_pos = cursor.position()

        # Anchored-comment highlight takes priority: if the cursor is over the
        # yellow range of an anchored comment, show the comment as the tooltip.
        comment_pairs = _anchored_comments_at(
            self._get_main_window(), getattr(self, 'row', -1), 'source', cursor_pos)
        if comment_pairs:
            self.setToolTip(_format_comment_tooltip(comment_pairs))
            return

        # Check if cursor is over a termbase match
        for term_id, match_info in self.termbase_matches.items():
            # Extract source term from match_info
            term = match_info.get('source', '') if isinstance(match_info, dict) else str(term_id)
            # Find term position in text
            text = self.toPlainText()
            text_lower = text.lower()
            term_lower = term.lower()
            
            start = 0
            while True:
                idx = text_lower.find(term_lower, start)
                if idx == -1:
                    break
                
                end_idx = idx + len(term)
                
                # Check if cursor is within this term's range
                if idx <= cursor_pos <= end_idx:
                    # Get translation and other info
                    if isinstance(match_info, dict):
                        translation = match_info.get('translation', '')
                        forbidden = match_info.get('forbidden', False)

                        # Build tooltip (show only target translation)
                        tooltip = f"<b>{translation}</b>"
                        if forbidden:
                            tooltip += "<br><span style='color: red;'>⚠️ FORBIDDEN TERM</span>"
                        tooltip += "<br><span style='color: #666;'><i>Double-click to insert</i></span>"
                    else:
                        tooltip = f"<b>{match_info}</b><br><span style='color: #666;'><i>Double-click to insert</i></span>"
                    
                    self.setToolTip(tooltip)
                    return
                
                start = end_idx
        
        # No match found at cursor position
        self.setToolTip("")
    
    def mouseDoubleClickEvent(self, event):
        """Insert termbase translation on double-click"""
        # A double-click must never make the grid jump: cancel the recenter
        # that the preceding single press scheduled (issue #240).
        main_window = self._get_main_window()
        if main_window is not None and hasattr(main_window, '_cancel_pending_click_center'):
            main_window._cancel_pending_click_center()
        # Remember when this double-click happened so a quick third press is
        # recognised as a triple-click (whole-segment select) in mousePressEvent.
        self._last_dblclick_ts = event.timestamp()
        # Cursor position: prefer the offset captured at press time (immune to
        # any reflow/scroll the first click triggered — issue #240).
        _pdp = getattr(self, '_press_doc_pos', None)
        if _pdp is not None:
            cursor_pos = min(max(0, _pdp), len(self.toPlainText()))
        else:
            cursor_pos = self.cursorForPosition(event.pos()).position()

        # Check if double-clicked on a termbase match
        for term_id, match_info in self.termbase_matches.items():
            # Extract source term from match_info
            term = match_info.get('source', '') if isinstance(match_info, dict) else str(term_id)
            text = self.toPlainText()
            text_lower = text.lower()
            term_lower = term.lower()
            
            start = 0
            while True:
                idx = text_lower.find(term_lower, start)
                if idx == -1:
                    break
                
                end_idx = idx + len(term)
                
                # Check if cursor is within this term's range
                if idx <= cursor_pos <= end_idx:
                    # Get translation
                    if isinstance(match_info, dict):
                        translation = match_info.get('translation', '')
                    else:
                        translation = match_info
                    
                    # Insert translation into target cell at cursor position
                    if self.table_ref and self.row >= 0:
                        target_widget = self.table_ref.cellWidget(self.row, 3)
                        if target_widget and hasattr(target_widget, 'textCursor'):
                            # Insert at current cursor position in target
                            target_cursor = target_widget.textCursor()
                            target_cursor.insertText(translation)
                            target_widget.setFocus()
                    return
                
                start = end_idx
        
        # Not on a termbase match: select the word under the cursor, excluding
        # adjacent punctuation (same word-selection behaviour as the target cell).
        if select_word_under_cursor(self, event, _pdp):
            return
        super().mouseDoubleClickEvent(event)

    def mousePressEvent(self, event):
        """Allow text selection on click and trigger row selection"""
        # Capture the clicked character offset BEFORE super()/activation can
        # reflow or scroll the cell, so a following double-click selects the
        # word we actually clicked even if the text scoots (issue #240).
        try:
            self._press_doc_pos = self.cursorForPosition(event.pos()).position()
        except Exception:
            self._press_doc_pos = None
        # Triple-click selects the whole segment (finalised in mouseReleaseEvent,
        # so Qt's own release doesn't collapse the selection).
        _triple = is_triple_click(self, event)
        super().mousePressEvent(event)
        if _triple:
            self._last_dblclick_ts = 0  # consume so a 4th press doesn't re-trigger
            self._triple_pending = True
            return

        # Use stored table reference and row number
        if self.table_ref and self.row >= 0:
            try:
                # Check for Shift or Ctrl modifier - let Qt handle native multi-selection
                modifiers = event.modifiers()
                is_shift = modifiers & Qt.KeyboardModifier.ShiftModifier
                is_ctrl = modifiers & Qt.KeyboardModifier.ControlModifier

                # NOTE: activation + auto-centering for a click is handled in
                # focusInEvent (which fires first, from inside the super() call
                # above), including the deferred, double-click-cancellable
                # recenter for issue #240. This handler only covers clicks within
                # the already-focused cell (no focus change, so no focusInEvent).
                if is_shift or is_ctrl:
                    # For Shift+click (range) or Ctrl+click (toggle), just set current cell
                    # but don't call selectRow() which would clear the selection
                    self.table_ref.setCurrentCell(self.row, 2)
                else:
                    # Normal click - select just this row
                    self.table_ref.selectRow(self.row)
                    self.table_ref.setCurrentCell(self.row, 2)

                    # CRITICAL: Manually trigger on_cell_selected since signals aren't firing
                    # Find the main window and call the method directly
                    main_window = self.table_ref.parent()
                    while main_window and not hasattr(main_window, 'on_cell_selected'):
                        main_window = main_window.parent()
                    if main_window and hasattr(main_window, 'on_cell_selected'):
                        main_window.on_cell_selected(self.row, 2, -1, -1)
            except Exception as e:
                print(f"Error triggering manual cell selection: {e}")

    def mouseReleaseEvent(self, event):
        """Smart word selection - expand partial selections to full words
        
        Works across multiple lines - if you select text spanning several lines,
        partial words at the START and END of your selection will be expanded.
        """
        super().mouseReleaseEvent(event)

        # Triple-click: apply the whole-segment selection now, after Qt's own
        # release handling, so it isn't collapsed. Skip the smart-word expansion.
        if getattr(self, '_triple_pending', False):
            self._triple_pending = False
            select_whole_segment(self)
            return

        # Check if smart selection is enabled
        main_window = self._get_main_window()
        if main_window and hasattr(main_window, 'enable_smart_word_selection'):
            if not main_window.enable_smart_word_selection:
                return  # Feature disabled

        # Get the current cursor
        cursor = self.textCursor()

        # Only expand if there's a selection
        if cursor.hasSelection():
            # Get selection boundaries
            start = cursor.selectionStart()
            end = cursor.selectionEnd()

            # Get the full text
            text = self.toPlainText()

            # Helper function to check if character is part of a word
            # Includes alphanumeric, underscore, hyphen, and apostrophe
            def is_word_char(char):
                # CJK / Thai etc. have no inter-word spaces; treat each such
                # character as its own token so expansion doesn't swallow the
                # whole run (str.isalnum() is True for them).
                return (char.isalnum() and not _is_cjk_char(char)) or char in "_-'"

            # Track if we need to update the selection
            selection_changed = False

            # Expand START boundary if we're in the middle of a word
            # (i.e., the character before the selection is a word character)
            if start > 0 and is_word_char(text[start - 1]):
                # Also check that the first selected character is a word char
                # (to avoid expanding when selecting from whitespace)
                if start < len(text) and is_word_char(text[start]):
                    while start > 0 and is_word_char(text[start - 1]):
                        start -= 1
                    selection_changed = True

            # Expand END boundary if we're in the middle of a word
            # (i.e., the character after the selection is a word character)
            if end < len(text) and is_word_char(text[end]):
                # Also check that the last selected character is a word char
                # (to avoid expanding when selecting to whitespace)
                if end > 0 and is_word_char(text[end - 1]):
                    while end < len(text) and is_word_char(text[end]):
                        end += 1
                    selection_changed = True

            # Set the new selection if boundaries changed
            if selection_changed:
                cursor.setPosition(start)
                cursor.setPosition(end, cursor.MoveMode.KeepAnchor)
                self.setTextCursor(cursor)

    def _get_main_window(self):
        """Get the main application window by traversing the parent hierarchy"""
        if not self.parent():
            return None
        main_window = self.parent()
        while main_window and not hasattr(main_window, 'go_to_first_segment'):
            main_window = main_window.parent()
        return main_window
    
    def focusInEvent(self, event):
        """Select text when focused for easy copying and trigger row selection"""
        super().focusInEvent(event)
        # Don't auto-select - let user select manually

        # Use stored table reference and row number
        if self.table_ref and self.row >= 0:
            try:
                # Check for Shift or Ctrl modifier - let Qt handle native multi-selection
                from PyQt6.QtWidgets import QApplication
                modifiers = QApplication.keyboardModifiers()
                is_shift = modifiers & Qt.KeyboardModifier.ShiftModifier
                is_ctrl = modifiers & Qt.KeyboardModifier.ControlModifier

                main_window = self._get_main_window()

                # Is this focus change caused by a mouse click (vs Tab /
                # arrow-nav / programmatic focus)? Only clicks need the anti-jump
                # treatment.
                is_mouse_focus = (main_window is not None
                                  and event.reason() == Qt.FocusReason.MouseFocusReason
                                  and not (is_shift or is_ctrl))

                # Defer centering only for mouse-driven focus, so a following
                # double-click can cancel it before the grid jumps (issue #240).
                # Keyboard / programmatic focus still centers immediately. This
                # focusInEvent is the real activation path for a click — it fires
                # from inside super().mousePressEvent, before the widget's own
                # mousePressEvent runs.
                defer_center = (is_mouse_focus
                                and getattr(main_window, 'auto_center_active_segment', False)
                                and not getattr(main_window, 'filtering_active', False))
                if defer_center:
                    main_window._click_activation = True

                # Suppress Qt's own "ensure visible" auto-scroll during click
                # activation so a double-click on a tall, partially-visible cell
                # doesn't shift the text out from under the cursor between the two
                # clicks (issue #240).
                _prev_autoscroll = None
                if is_mouse_focus:
                    _prev_autoscroll = self.table_ref.hasAutoScroll()
                    self.table_ref.setAutoScroll(False)
                try:
                    if is_shift or is_ctrl:
                        # For Shift+click (range) or Ctrl+click (toggle), just set current cell
                        # but don't call selectRow() which would clear the selection
                        self.table_ref.setCurrentCell(self.row, 2)
                    else:
                        # Normal focus - select just this row
                        self.table_ref.selectRow(self.row)
                        self.table_ref.setCurrentCell(self.row, 2)

                        # CRITICAL: Manually trigger on_cell_selected since signals aren't firing
                        if main_window and hasattr(main_window, 'on_cell_selected'):
                            main_window.on_cell_selected(self.row, 2, -1, -1)
                finally:
                    if _prev_autoscroll is not None:
                        self.table_ref.setAutoScroll(_prev_autoscroll)

                if defer_center and hasattr(main_window, '_schedule_click_center'):
                    main_window._schedule_click_center(self.row)
            except Exception as e:
                print(f"Error triggering manual cell selection: {e}")

    def contextMenuEvent(self, event):
        """Show context menu with Add to Glossary and Add to Non-Translatables options"""
        from PyQt6.QtWidgets import QMenu
        from PyQt6.QtGui import QAction

        menu = QMenu(self)

        # "Open comment(s)" – jump to the relevant comment in the Comments tab.
        # Right-clicking a yellow anchored range opens that specific comment;
        # right-clicking anywhere else on a segment that has comments opens the
        # segment's first comment (this is how segment-level comments — which
        # have no highlighted range — are reached from the grid).
        if _add_open_comment_actions(self, menu, 'source', event.pos()):
            menu.addSeparator()

        # Segment split / merge (Trados / memoQ style). Only offered when the
        # project's segmentation is owned by Supervertaler (monolingual DOCX /
        # Okapi / TXT-MD); hidden for bilingual CAT round-trip formats whose
        # segment slots belong to the external tool.
        try:
            _mw_sm = self._get_main_window()
            if _mw_sm is not None and hasattr(_mw_sm, '_split_segment_at_row'):
                from modules import segment_split_merge as _ssm
                _proj_sm = getattr(_mw_sm, 'current_project', None)
                if _proj_sm is not None and _ssm.structure_editable(_proj_sm):
                    _seg_sm, _idx_sm = _mw_sm._segment_for_grid_row(self.row)
                    # Split/merge restructure the document, so they're only
                    # allowed in Document Order (no active grid sort).
                    _doc_order = getattr(_mw_sm, 'current_sort', None) is None
                    if _seg_sm is not None:
                        # Map the clicked caret (display space) back to an index
                        # into segment.source. This is only well-defined when the
                        # displayed text differs from the source by invisible-
                        # character markers alone — not when tags are compacted or
                        # outer wrapping tags are hidden. In those modes the split
                        # action is shown but disabled with an explanatory tip.
                        _clean = (not getattr(self, '_compact_tag_map', None)
                                  and not getattr(self, '_stripped_outer_tag', None))
                        _src_off = -1
                        if _clean:
                            _caret = self.cursorForPosition(event.pos()).position()
                            _prefix = self.toPlainText()[:_caret]
                            if hasattr(_mw_sm, 'reverse_invisible_replacements'):
                                _prefix = _mw_sm.reverse_invisible_replacements(_prefix)
                            _src_off = len(_prefix)
                        split_act = QAction("✂ Split segment here", self)
                        split_act.setEnabled(_doc_order and _clean and _ssm.can_split(_seg_sm, _src_off))
                        if not _doc_order:
                            split_act.setToolTip(
                                "Switch to Document Order (Sort menu) to split or merge segments.")
                        elif not _clean:
                            split_act.setToolTip(
                                "Switch to full tag view (turn off compact / hidden tags) to split here.")
                        split_act.triggered.connect(
                            lambda checked=False, r=self.row, o=_src_off:
                            _mw_sm._split_segment_at_row(r, o))
                        menu.addAction(split_act)

                        _merge_ok, _merge_reason = _ssm.can_merge(_proj_sm.segments, _idx_sm)
                        merge_act = QAction("🔗 Merge with next segment", self)
                        merge_act.setEnabled(_doc_order and _merge_ok)
                        if not _doc_order:
                            merge_act.setToolTip(
                                "Switch to Document Order (Sort menu) to split or merge segments.")
                        elif not _merge_ok:
                            merge_act.setToolTip(_merge_reason)
                        merge_act.triggered.connect(
                            lambda checked=False, r=self.row: _mw_sm._merge_segment_at_row(r))
                        menu.addAction(merge_act)

                        # 🗑 Delete segment(s) — narrower than split/merge: only
                        # for paste / plain-text / Start-Empty projects, where a
                        # segment isn't backed by a source-file slot. Shown but
                        # disabled (with a why) on DOCX / Okapi projects.
                        try:
                            _sel_rows = sorted({it.row() for it in _mw_sm.table.selectedItems()})
                        except Exception:
                            _sel_rows = []
                        _del_rows = (_sel_rows if (self.row in _sel_rows and len(_sel_rows) > 1)
                                     else [self.row])
                        _del_label = (f"🗑 Delete {len(_del_rows)} selected segments"
                                      if len(_del_rows) > 1 else "🗑 Delete segment")
                        del_act = QAction(_del_label, self)
                        _can_delete = _ssm.deletable(_proj_sm)
                        del_act.setEnabled(_doc_order and _can_delete)
                        if not _can_delete:
                            del_act.setToolTip(
                                "Deleting whole segments isn't available for DOCX / Okapi / "
                                "bilingual-CAT projects — it would leave a gap in the exported "
                                "file. Available for pasted / plain-text / Start-Empty projects.")
                        elif not _doc_order:
                            del_act.setToolTip(
                                "Switch to Document Order (Sort menu) to delete segments.")
                        del_act.triggered.connect(
                            lambda checked=False, rr=list(_del_rows):
                            _mw_sm._delete_segments_at_rows(rr))
                        menu.addAction(del_act)
                        menu.addSeparator()
        except Exception:
            pass

        # Add standard actions
        if self.textCursor().hasSelection():
            copy_action = QAction("Copy", self)
            copy_action.triggered.connect(self.copy)
            menu.addAction(copy_action)
            menu.addSeparator()

        # Superlookup search action
        if self.textCursor().hasSelection():
            superlookup_action = QAction(f"🔍 Search in SuperLookup ({format_shortcut_for_display('Ctrl+K')})", self)
            superlookup_action.triggered.connect(self._handle_superlookup_search)
            menu.addAction(superlookup_action)

        # Add comment (v1.10.142) – mirrors the Ctrl+M shortcut. Anchors the
        # comment to the selected source text, or attaches a segment-level
        # comment if nothing is selected. add_comment_from_selection() reads
        # QApplication.focusWidget(), so re-focus this editor before calling
        # (the QMenu may otherwise hold focus when the action fires).
        _mw_for_comment = self._get_main_window()
        if _mw_for_comment and hasattr(_mw_for_comment, 'add_comment_from_selection'):
            _sel = self.textCursor().hasSelection()
            add_comment_action = QAction(
                f"💬 Add comment ({format_shortcut_for_display('Ctrl+M')})"
                if _sel else
                f"💬 Add segment comment ({format_shortcut_for_display('Ctrl+M')})",
                self,
            )
            add_comment_action.triggered.connect(
                lambda checked=False, w=self, mw=_mw_for_comment: (
                    w.setFocus(),
                    QTimer.singleShot(0, mw.add_comment_from_selection),
                )
            )
            menu.addAction(add_comment_action)

        # Segment operations (confirm / status / lock / AutoTagger / clear),
        # from the same helper the row and target-cell menus use, so all three
        # context menus offer the same segment actions. Previously each menu
        # had its own subset and which one you got depended on which column you
        # happened to right-click.
        try:
            _at_mw = self._get_main_window()
            if (_at_mw is not None
                    and hasattr(_at_mw, 'add_segment_actions_to_menu')
                    and getattr(_at_mw, 'current_project', None)):
                _row = getattr(self, 'row', None)
                if isinstance(_row, int) and 0 <= _row < len(_at_mw.current_project.segments):
                    try:
                        _sel_rows = {ix.row() for ix in _at_mw.table.selectionModel().selectedIndexes()}
                    except Exception:
                        _sel_rows = set()
                    if _row not in _sel_rows:
                        _at_mw.table.selectRow(_row)
                    _segs_for_menu = _at_mw.get_selected_segments_from_grid()
                    if not _segs_for_menu:
                        _segs_for_menu = [_at_mw.current_project.segments[_row]]
                    menu.addSeparator()
                    _at_mw.add_segment_actions_to_menu(menu, _segs_for_menu, _row)
        except Exception:
            pass   # a menu extra must never break the context menu

        # QuickLauncher (prompt-based actions + QuickTrans)
        try:
            main_window = self._get_main_window()
            quicklauncher_items = []
            if main_window and hasattr(main_window, 'prompt_manager_qt') and main_window.prompt_manager_qt:
                lib = getattr(main_window.prompt_manager_qt, 'library', None)
                if lib and hasattr(lib, 'get_quicklauncher_grid_prompts'):
                    quicklauncher_items = lib.get_quicklauncher_grid_prompts() or []

            qm_menu = menu.addMenu("⚡ QuickLauncher")

            # Bold heading at the top of the submenu
            from PyQt6.QtWidgets import QWidgetAction, QLabel
            _heading_lbl = QLabel(self.tr("  Supervertaler QuickLauncher"))
            _heading_lbl.setStyleSheet("font-weight: bold; padding: 4px 8px; color: #1565C0;")
            _heading_act = QWidgetAction(qm_menu)
            _heading_act.setDefaultWidget(_heading_lbl)
            qm_menu.addAction(_heading_act)
            qm_menu.addSeparator()

            # Capture selected text NOW before menu takes focus
            _sel_text = self.textCursor().selectedText() if self.textCursor().hasSelection() else None

            # QuickTrans at the top of the QuickLauncher
            # v1.10.61: was "(Ctrl+M)" — that was a stale display hint
            # from an earlier version; no in-app binding ever existed.
            # The actual hotkey is the GLOBAL pynput chord (Ctrl+Alt+Q
            # on Windows/Linux, Meta+Ctrl+Q on macOS) which works
            # system-wide, including inside Workbench.
            _qt_hotkey = 'Meta+Ctrl+Q' if IS_MACOS else 'Ctrl+Alt+Q'
            qt_action = QAction(f"⚡ QuickTrans ({format_shortcut_for_display(_qt_hotkey)})", self)
            qt_action.triggered.connect(self._handle_mt_quick_lookup)
            qm_menu.addAction(qt_action)

            # Chat – defer with QTimer so menu fully closes first.
            # Despite the legacy method name (`show_supervertaler_assistant`)
            # this navigates to the in-Workbench Chat tab; the standalone
            # Sidekick window was retired in v1.10.4.
            assistant_action = QAction("💬 Chat", self)
            assistant_action.triggered.connect(
                lambda: QTimer.singleShot(0, lambda: main_window.show_supervertaler_assistant(
                    initial_text=_sel_text
                ))
            )
            qm_menu.addAction(assistant_action)

            if quicklauncher_items:
                qm_menu.addSeparator()
                for rel_path, label in sorted(quicklauncher_items, key=lambda x: (x[1] or x[0]).lower()):
                    prompt_menu = qm_menu.addMenu(label or rel_path)

                    run_show = QAction("▶ Run (show response)…", self)
                    run_show.triggered.connect(
                        lambda checked=False, p=rel_path: main_window.run_grid_quicklauncher_prompt(p, origin_widget=self, behavior="show")
                    )
                    prompt_menu.addAction(run_show)

                    run_replace = QAction("↺ Run and replace target selection", self)
                    run_replace.triggered.connect(
                        lambda checked=False, p=rel_path: main_window.run_grid_quicklauncher_prompt(p, origin_widget=self, behavior="replace")
                    )
                    prompt_menu.addAction(run_replace)

                    copy_clip = QAction("📋 Copy to clipboard", self)
                    copy_clip.triggered.connect(
                        lambda checked=False, p=rel_path: main_window.run_grid_quicklauncher_prompt(p, origin_widget=self, behavior="clipboard")
                    )
                    prompt_menu.addAction(copy_clip)

                menu.addSeparator()
        except Exception:
            # Never break the normal context menu due to QuickLauncher errors
            pass

        # Add to glossary action (with dialogue)
        add_to_tb_action = QAction(f"📖 Add to Termbase ({format_shortcut_for_display('Ctrl+Alt+T')})", self)
        add_to_tb_action.triggered.connect(self._handle_add_to_termbase)
        menu.addAction(add_to_tb_action)

        # Quick add to Project glossary (no dialog)
        quick_add_p1_action = QAction(f"⚡ Quick Add to Project Termbase ({format_shortcut_for_display('Alt+Up')})", self)
        quick_add_p1_action.triggered.connect(lambda: self._handle_quick_add_to_glossary_priority(1))
        menu.addAction(quick_add_p1_action)

        # Quick add to Background glossary (no dialog)
        quick_add_p2_action = QAction(f"⚡ Quick Add to Background Termbase ({format_shortcut_for_display('Alt+Down')})", self)
        quick_add_p2_action.triggered.connect(lambda: self._handle_quick_add_to_glossary_priority(2))
        menu.addAction(quick_add_p2_action)

        # Add to non-translatables action
        add_to_nt_action = QAction(f"🚫 Add to Non-Translatables ({format_shortcut_for_display('Ctrl+Alt+N')})", self)
        add_to_nt_action.triggered.connect(self._handle_add_to_nt)
        menu.addAction(add_to_nt_action)

        menu.exec(event.globalPos())

    def _handle_add_to_nt(self):
        """Handle Ctrl+Alt+N: Add selected text to active non-translatable list(s)"""
        # Get selected text
        selected_text = self.textCursor().selectedText().strip()

        if not selected_text:
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.warning(
                self,
                "Selection Required",
                "Please select text in the Source cell before adding to non-translatables."
            )
            return

        # Find main window and call add_to_nt method
        table = self.table_ref if hasattr(self, 'table_ref') else self.parent()
        if table:
            main_window = table.parent()
            while main_window and not hasattr(main_window, 'add_text_to_non_translatables'):
                main_window = main_window.parent()

            if main_window and hasattr(main_window, 'add_text_to_non_translatables'):
                main_window.add_text_to_non_translatables(selected_text)
            else:
                from PyQt6.QtWidgets import QMessageBox
                QMessageBox.warning(
                    self,
                    "Feature Not Available",
                    "Non-translatables functionality not available."
                )

    def _handle_mt_quick_lookup(self):
        """Handle right-click: Open MT Quick Lookup popup for selected text or full source"""
        # Get selected text (if any)
        selected_text = self.textCursor().selectedText().strip() if self.textCursor().hasSelection() else None

        # Find main window and call show_mt_quick_popup
        main_window = self._get_main_window()
        if main_window and hasattr(main_window, 'show_mt_quick_popup'):
            main_window.show_mt_quick_popup(text_override=selected_text)

    def set_file_boundary(self, is_boundary: bool):
        """Mark this row as a file boundary. Kept for API compatibility; visual handled by banner overlay."""
        self._file_boundary = is_boundary

    def set_background_color(self, color: str, text_color: str = None):
        """Set the background color (and optional forced text color) for this cell.

        text_color is passed for the selection tint so the editor's text stays
        readable on the pale-blue selection background — without it, dark themes
        render light text on light blue (unreadable). Alternating-row callers
        pass no text_color, leaving the theme's default editor colour in place.
        """
        self._grid_bg_color = color
        self._grid_text_color = text_color
        self._rebuild_grid_stylesheet()

    def apply_grid_font_size(self, size_pt: float):
        """Set the displayed font size for this grid cell.

        Sets the size in this widget's own stylesheet (not just setFont) so
        the value wins over ThemeManager's app-level `QTextEdit { font-size }`
        rule emitted at non-100% UI scale. Without this, grid zoom and global
        scale would silently lose the per-cell font-size battle to that rule.
        """
        self._grid_font_pt = size_pt
        self._rebuild_grid_stylesheet()

    def _rebuild_grid_stylesheet(self):
        """Compose this cell's stylesheet from current bg colour + font size."""
        if _GridStyleBatch.defer(self):
            return
        # v1.10.229: When "Allow Replace in Source Text" is on, override the
        # cell background with a soft amber so the user can see at a glance
        # which cells are dangerous to touch. Mirrors the inline-editor
        # styling that the (currently dead) delegate path used to apply.
        if getattr(self, 'allow_source_edit', False):
            bg_rule = "background-color: #FFF9E6;"
            focus_border = "1px solid #FF9800"
        else:
            bg_rule = f"background-color: {self._grid_bg_color};" if self._grid_bg_color else ""
            focus_border = "1px solid #2196F3"
        font_rule = f"font-size: {self._grid_font_pt:.1f}pt;" if self._grid_font_pt else ""
        _tc = getattr(self, '_grid_text_color', None)
        color_rule = f"color: {_tc};" if _tc else ""
        # Same-thickness transparent border when unfocused so gaining focus
        # changes only the colour, not the content width — a `none` → `1px`
        # jump would re-wrap long text and make it scoot (issue #240).
        self.setStyleSheet(f"""
            QTextEdit {{
                border: 1px solid transparent;
                padding: 0px 4px 0px 0px;
                {bg_rule}
                {font_rule}
                {color_rule}
            }}
            QTextEdit:focus {{
                border: {focus_border};
            }}
            QTextEdit::selection {{
                background-color: #000000;
                color: #FFFFFF;
            }}
        """)

    def set_source_editable(self, enabled: bool):
        """Toggle the editable/read-only state of this source cell at runtime.

        v1.10.229: Called by the main window's update_grid_delegate() whenever
        the "Allow Replace in Source Text" setting is toggled in Settings, so
        the live grid reflects the new state without a full reload. Flips
        readOnly, interaction flags, and stylesheet (which paints the amber
        warning background when editing is on).
        """
        self.allow_source_edit = enabled
        # Turning the global setting off also clears any per-cell ephemeral
        # state (otherwise we'd leak an "editable" flag that nothing else
        # knows about).
        if not enabled:
            self._ephemeral_source_edit = False
        self.setReadOnly(not enabled)
        if enabled:
            self.setTextInteractionFlags(Qt.TextInteractionFlag.TextEditorInteraction)
        else:
            self.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByKeyboard |
                Qt.TextInteractionFlag.TextSelectableByMouse
            )
        self._rebuild_grid_stylesheet()

    def _enable_ephemeral_source_edit(self):
        """v1.10.229: Per-cell F2 source-edit mode (memoQ / Trados convention).

        Distinct from the global "Allow Replace in Source Text" setting —
        flips the same per-widget flags so the existing textChanged handler
        picks up the edits and writes them back to segment.source, but only
        for THIS cell, and only until focus leaves the widget.
        """
        self._ephemeral_source_edit = True
        self.allow_source_edit = True
        self.setReadOnly(False)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.TextEditorInteraction)
        self._rebuild_grid_stylesheet()
        # Make sure the editor actually has focus and the cursor is in the
        # text — when F2 is pressed via the table's key forwarding, focus
        # may still sit on the table viewport.
        self.setFocus(Qt.FocusReason.ShortcutFocusReason)

    def _disable_ephemeral_source_edit(self):
        """Counterpart to _enable_ephemeral_source_edit. Auto-called on
        focus-out so the user gets the "click away to exit" behaviour for
        free."""
        if not getattr(self, '_ephemeral_source_edit', False):
            return
        self._ephemeral_source_edit = False
        self.allow_source_edit = False
        self.setReadOnly(True)
        self.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByKeyboard |
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self._rebuild_grid_stylesheet()

    def focusOutEvent(self, event):
        """v1.10.229: Auto-exit per-cell F2 source-edit on focus-out.

        Only revert if the GLOBAL setting is off — when the user has
        permanently enabled source editing in Settings, we don't want
        focus-out to silently disable it for that one cell.
        """
        if getattr(self, '_ephemeral_source_edit', False):
            mw = self._get_main_window()
            globally_on = bool(getattr(mw, 'allow_replace_in_source', False)) if mw else False
            if not globally_on:
                self._disable_ephemeral_source_edit()
        super().focusOutEvent(event)


class TagHighlighter(QSyntaxHighlighter):
    """Syntax highlighter for HTML/XML tags, CAT tool tags, CafeTran pipe symbols, Markdown syntax, and spell checking in text editors"""

    # Class-level reference to spellcheck manager (shared across all instances)
    _spellcheck_manager = None
    _spellcheck_enabled = False
    _is_cafetran_project = False  # Only highlight pipe symbols for CafeTran projects
    _is_markdown_project = False  # Highlight Markdown syntax for Markdown imports
    _show_nbsp = False  # Shade real non-breaking spaces (U+00A0 / U+202F) with a background box

    def __init__(self, document, tag_color='#7f0001', invisible_char_color='#999999', enable_spellcheck=False):
        super().__init__(document)
        self.tag_color = tag_color
        self.invisible_char_color = invisible_char_color
        self._local_spellcheck_enabled = enable_spellcheck
        self.update_tag_format()

    @classmethod
    def set_spellcheck_manager(cls, manager):
        """Set the shared spellcheck manager instance"""
        cls._spellcheck_manager = manager

    @classmethod
    def set_spellcheck_enabled(cls, enabled: bool):
        """Enable or disable spellchecking globally"""
        cls._spellcheck_enabled = enabled

    def update_tag_format(self):
        """Update the tag format with current color"""
        from PyQt6.QtGui import QTextCharFormat, QColor
        self.tag_format = QTextCharFormat()
        self.tag_format.setForeground(QColor(self.tag_color))

        # CafeTran pipe symbols - red and bold like in CafeTran
        self.pipe_format = QTextCharFormat()
        self.pipe_format.setForeground(QColor('#FF0000'))  # Red
        self.pipe_format.setFontWeight(700)  # Bold

        # Invisible character symbols - use configured color
        self.invisible_format = QTextCharFormat()
        self.invisible_format.setForeground(QColor(self.invisible_char_color))

        # Non-breaking-space shading - a faint coloured background box drawn behind the
        # real NBSP character (we no longer substitute a marker glyph for it). We derive a
        # low-alpha tint from the configured invisible-char colour so the box stays subtle
        # and the text remains readable even when invisible_char_color is very dark.
        self.nbsp_format = QTextCharFormat()
        _nbsp_bg = QColor(self.invisible_char_color)
        _nbsp_bg.setAlpha(70)  # faint highlight, not a solid block
        self.nbsp_format.setBackground(_nbsp_bg)

        # Spellcheck format - red wavy underline
        self.spellcheck_format = QTextCharFormat()
        self.spellcheck_format.setUnderlineColor(QColor('#FF0000'))
        self.spellcheck_format.setUnderlineStyle(QTextCharFormat.UnderlineStyle.WaveUnderline)

        # Markdown syntax formats
        # Bold/Italic markers: ** __ * _
        self.md_bold_format = QTextCharFormat()
        self.md_bold_format.setForeground(QColor('#C71585'))  # Medium violet red
        self.md_bold_format.setFontWeight(700)  # Bold to make it stand out
        
        # Heading markers: # ## ### etc.
        self.md_heading_format = QTextCharFormat()
        self.md_heading_format.setForeground(QColor('#0066CC'))  # Blue
        self.md_heading_format.setFontWeight(700)
        
        # Code markers: ` ``` 
        self.md_code_format = QTextCharFormat()
        self.md_code_format.setForeground(QColor('#D2691E'))  # Chocolate/Orange
        self.md_code_format.setFontWeight(700)
        
        # Link/Image syntax: []() ![]()
        self.md_link_format = QTextCharFormat()
        self.md_link_format.setForeground(QColor('#6A5ACD'))  # Slate blue/Purple
        
        # Blockquote markers: >
        self.md_quote_format = QTextCharFormat()
        self.md_quote_format.setForeground(QColor('#228B22'))  # Forest green
        self.md_quote_format.setFontWeight(700)
        
        # List markers: - * + 1. 2.
        self.md_list_format = QTextCharFormat()
        self.md_list_format.setForeground(QColor('#FF6600'))  # Orange
        self.md_list_format.setFontWeight(700)

    def set_tag_color(self, color: str):
        """Update tag highlight color"""
        self.tag_color = color
        self.update_tag_format()
        self.rehighlight()

    def set_invisible_char_color(self, color: str):
        """Update invisible character color"""
        self.invisible_char_color = color
        self.update_tag_format()
        self.rehighlight()

    def set_local_spellcheck(self, enabled: bool):
        """Enable or disable spellcheck for this specific highlighter"""
        self._local_spellcheck_enabled = enabled
        self.rehighlight()
    
    def highlightBlock(self, text):
        """Highlight all tags, pipe symbols, invisible chars, and misspelled words in the text block"""
        import re
        # Combined pattern for ALL CAT tool tag types:
        # 1. HTML/XML: <tag>, </tag>, <tag/>, <tag attr="val">
        # 2. Trados numeric: <1>, </1>
        # 3. memoQ numeric bracket tags:
        #    - Opening: [1}, [2} etc.
        #    - Closing: {1], {2] etc.
        #    - Standalone: [1], [2] etc.
        # 4. memoQ content tags with text/attributes (from bilingual DOCX):
        #    - [uicontrol id="GUID-..."], [image cid="..." href="..."], etc.
        #    - {uicontrol}, {image}, etc. (closing tags)
        #    NOTE: Opening [tag] MUST have attributes (space+content) to avoid matching
        #          placeholders like [Company] or [Bedrijf]. Closing {tag} doesn't need attrs.
        # `wj?` after each "/" tolerates the display-only WORD JOINER that
        # protect_tags_from_linebreak inserts (to stop tags wrapping mid-way).
        # It's optional, so tags without the joiner still match.
        wj = WORD_JOINER
        tag_patterns = [
            r'</?' + wj + r'?[a-zA-Z][a-zA-Z0-9-]*/?' + wj + r'?(?:\s[^>]*)?>',  # HTML/XML tags
            r'</?' + wj + r'?\d+>',                       # Trados numeric: <1>, </1>
            r'\{/?' + wj + r'?\d+/?' + wj + r'?\}',       # Compact tag placeholders: {1}, {/1}, {1/}
            r'\[\d+[}\]]',                                # memoQ numeric: [1}, [1]
            r'\{\d+[}\]]',                                # memoQ numeric: {1}, {1]
            r'\[[^}\]]+\}',                               # memoQ mixed: [anything} (exclude } and ])
            r'\{[^\[\]]+\]',                              # memoQ mixed: {anything] (exclude [ and ])
            r'\[[a-zA-Z][^}\]]*\s[^}\]]*\]',              # memoQ content: [tag attr...] (exclude } and ])
            r'\{[a-zA-Z][a-zA-Z0-9_-]*\}',                # memoQ closing: {uicontrol}, {MQ}
            r'\{\d{5}\}',                                 # Déjà Vu tags: {00108}, {00109}, etc.
        ]
        combined_pattern = re.compile('|'.join(tag_patterns))

        matches_found = list(combined_pattern.finditer(text))

        for match in matches_found:
            start = match.start()
            length = match.end() - start
            self.setFormat(start, length, self.tag_format)

        # The user's own inline codes ({playerName}, %s, \n …) look like tags too
        for start, end, _code in _inline_codes.find_codes(text):
            self.setFormat(start, end - start, self.tag_format)

        # Match invisible character symbols (light blue)
        for i, char in enumerate(text):
            if char in '·→↵':  # Invisible character replacement symbols
                self.setFormat(i, 1, self.invisible_format)

        # Shade real non-breaking spaces (U+00A0 / U+202F) with a faint background box.
        # NBSP is no longer substituted with a marker glyph, so we colour it in place.
        if TagHighlighter._show_nbsp:
            for i, ch in enumerate(text):
                if ch in (' ', ' '):
                    self.setFormat(i, 1, self.nbsp_format)
        
        # CafeTran pipe symbols (red and bold) - ONLY for CafeTran projects
        if TagHighlighter._is_cafetran_project:
            for i, char in enumerate(text):
                if char == '|':
                    self.setFormat(i, 1, self.pipe_format)

        # Markdown syntax highlighting - ONLY for Markdown projects
        if TagHighlighter._is_markdown_project:
            self._highlight_markdown_syntax(text)

        # Spell checking - only for target editors when enabled
        if self._local_spellcheck_enabled and TagHighlighter._spellcheck_enabled and TagHighlighter._spellcheck_manager:
            self._highlight_misspelled_words(text)

    def _highlight_misspelled_words(self, text):
        """Highlight misspelled words with red wavy underline"""
        # Safety check - if spellcheck manager is not available or disabled, skip
        if not TagHighlighter._spellcheck_manager:
            return
        
        # Check if spellcheck manager detected a crash - if so, skip entirely
        if hasattr(TagHighlighter._spellcheck_manager, '_crash_detected') and TagHighlighter._spellcheck_manager._crash_detected:
            return
        
        import re
        # Find all words (letters only, including accented characters)
        # Skip words inside tags or that look like technical content
        word_pattern = re.compile(r'\b([a-zA-ZÀ-ÿ\']+)\b', re.UNICODE)
        
        try:
            for match in word_pattern.finditer(text):
                word = match.group(1)
                start = match.start(1)
                length = len(word)
                
                # Skip very short words and words with apostrophes at start/end
                if len(word) < 2:
                    continue
                
                # Check if this word is inside ANY type of tag:
                # - HTML/XML: < ... >
                # - memoQ/DITA: [ ... } or [ ... ] or { ... ]
                before_text = text[:start]
                
                # Check for HTML tags
                last_html_open = before_text.rfind('<')
                last_html_close = before_text.rfind('>')
                if last_html_open > last_html_close:
                    continue  # Inside HTML tag
                
                # Check for bracket tags: [ ... } or [ ... ]
                last_square_open = before_text.rfind('[')
                last_square_close = max(before_text.rfind('}'), before_text.rfind(']'))
                if last_square_open > last_square_close:
                    continue  # Inside [tag...} or [tag...]
                
                # Check for curly tags: { ... ] or { ... }
                last_curly_open = before_text.rfind('{')
                last_curly_close = max(before_text.rfind(']'), before_text.rfind('}'))
                if last_curly_open > last_curly_close:
                    continue  # Inside {tag...] or {tag...}
                
                # Check spelling
                if not TagHighlighter._spellcheck_manager.check_word(word):
                    self.setFormat(start, length, self.spellcheck_format)
        except Exception as e:
            # If anything goes wrong during spellcheck highlighting, disable it
            print(f"Spellcheck highlighting error: {e}")
            if TagHighlighter._spellcheck_manager:
                TagHighlighter._spellcheck_manager._crash_detected = True
                TagHighlighter._spellcheck_manager.enabled = False

    def _highlight_markdown_syntax(self, text):
        """Highlight Markdown syntax elements to make them visually distinct"""
        import re
        
        # 1. Heading markers at start of line: # ## ### #### ##### ######
        #    Only match at start of line (or after only whitespace)
        heading_pattern = re.compile(r'^(#{1,6})\s', re.MULTILINE)
        for match in heading_pattern.finditer(text):
            self.setFormat(match.start(1), len(match.group(1)), self.md_heading_format)
        
        # 2. Bold markers: **text** or __text__
        #    Highlight just the markers, not the content
        bold_pattern = re.compile(r'(\*\*|__)(?=\S)(.+?)(?<=\S)\1')
        for match in bold_pattern.finditer(text):
            # Opening marker
            self.setFormat(match.start(), 2, self.md_bold_format)
            # Closing marker
            self.setFormat(match.end() - 2, 2, self.md_bold_format)
        
        # 3. Italic markers: *text* or _text_ (but not ** or __)
        #    Must not be preceded/followed by same character
        italic_pattern = re.compile(r'(?<!\*)\*(?!\*)(?=\S)(.+?)(?<=\S)\*(?!\*)|(?<!_)_(?!_)(?=\S)(.+?)(?<=\S)_(?!_)')
        for match in italic_pattern.finditer(text):
            # Opening marker (1 char)
            self.setFormat(match.start(), 1, self.md_bold_format)
            # Closing marker (1 char)
            self.setFormat(match.end() - 1, 1, self.md_bold_format)
        
        # 4. Inline code: `code`
        code_inline_pattern = re.compile(r'(`+)([^`]+)\1')
        for match in code_inline_pattern.finditer(text):
            # Highlight entire code span including backticks
            self.setFormat(match.start(), len(match.group(0)), self.md_code_format)
        
        # 5. Code fence markers: ``` or ~~~
        code_fence_pattern = re.compile(r'^(`{3,}|~{3,}).*$', re.MULTILINE)
        for match in code_fence_pattern.finditer(text):
            self.setFormat(match.start(), len(match.group(0)), self.md_code_format)
        
        # 6. Links: [text](url) - highlight brackets and parens, not the text
        link_pattern = re.compile(r'\[([^\]]+)\]\(([^\)]+)\)')
        for match in link_pattern.finditer(text):
            full_start = match.start()
            text_part = match.group(1)
            url_part = match.group(2)
            # [ bracket
            self.setFormat(full_start, 1, self.md_link_format)
            # ] bracket
            self.setFormat(full_start + 1 + len(text_part), 1, self.md_link_format)
            # ( and url and )
            url_start = full_start + 1 + len(text_part) + 1
            self.setFormat(url_start, len(url_part) + 2, self.md_link_format)
        
        # 7. Images: ![alt](url) - same as links but with !
        image_pattern = re.compile(r'!\[([^\]]*)\]\(([^\)]+)\)')
        for match in image_pattern.finditer(text):
            full_start = match.start()
            alt_part = match.group(1)
            url_part = match.group(2)
            # ! and [ 
            self.setFormat(full_start, 2, self.md_link_format)
            # ]
            self.setFormat(full_start + 2 + len(alt_part), 1, self.md_link_format)
            # ( and url and )
            url_start = full_start + 2 + len(alt_part) + 1
            self.setFormat(url_start, len(url_part) + 2, self.md_link_format)
        
        # 8. Blockquote markers: > at start of line
        quote_pattern = re.compile(r'^(>+)\s?', re.MULTILINE)
        for match in quote_pattern.finditer(text):
            self.setFormat(match.start(1), len(match.group(1)), self.md_quote_format)
        
        # 9. Unordered list markers: - * + at start of line (with space after)
        ul_pattern = re.compile(r'^(\s*)([-*+])\s', re.MULTILINE)
        for match in ul_pattern.finditer(text):
            marker_start = match.start(2)
            self.setFormat(marker_start, 1, self.md_list_format)
        
        # 10. Ordered list markers: 1. 2. 3. etc. at start of line
        ol_pattern = re.compile(r'^(\s*)(\d+\.)\s', re.MULTILINE)
        for match in ol_pattern.finditer(text):
            marker_start = match.start(2)
            marker_len = len(match.group(2))
            self.setFormat(marker_start, marker_len, self.md_list_format)
        
        # 11. Horizontal rules: --- or *** or ___ (3+ chars)
        hr_pattern = re.compile(r'^([-*_]{3,})\s*$', re.MULTILINE)
        for match in hr_pattern.finditer(text):
            self.setFormat(match.start(1), len(match.group(1)), self.md_heading_format)


class EditableGridTextEditor(QTextEdit):
    """Editable QTextEdit for target cells - allows text selection and editing"""
    
    # Class variable for tag highlight color (shared across all instances)
    tag_highlight_color = '#7f0001'  # Default memoQ dark red
    
    # Class variables for focus border customization
    focus_border_color = '#f1b79a'  # Default peach/salmon
    focus_border_thickness = 2  # Default 2px (slightly thicker than before)
    
    def __init__(self, text: str = "", parent=None, row: int = -1, table=None):
        super().__init__(parent)
        self.row = row  # Store row number for auto-selection
        self.table = table  # Store table reference
        self.setReadOnly(False)

        # v1.10.230: Backspace-undo state for the AutoCorrect engine. Set
        # by _maybe_apply_autocorrect() right after a conversion fires;
        # consumed by the Backspace branch in keyPressEvent (one-shot —
        # any other keystroke clears it).
        self._pending_autocorrect_undo: Optional[dict] = None

        # CRITICAL: Track initial load state to ignore spurious textChanged events
        # Qt queues document change events during setPlainText() even with blockSignals(True).
        # When signals are unblocked later, Qt delivers these queued events, causing false
        # textChanged signals. This flag allows us to ignore the first event after unblocking.
        self._initial_load_complete = False

        # CRITICAL: Block ALL signals during initialization to prevent textChanged from firing
        # when setText is called. Signals will be unblocked AFTER signal handler is connected
        # in load_segments_to_grid. This prevents ANY textChanged events during grid loading.
        self.blockSignals(True)
        self.setPlainText(text)
        # The initial population must NOT be an undoable step. Otherwise the
        # first Ctrl+Z in a freshly-built cell undoes the cell's own setPlainText
        # (reverting it toward empty) instead of falling through to the app-level
        # segment/structural undo — which is why undoing a split needed two
        # presses when the cursor sat in the target box. Clearing the document
        # undo stack here means the cell has nothing of its own to undo until the
        # user actually types, so Ctrl+Z falls through on the first press.
        self.document().clearUndoRedoStacks()
        # DO NOT unblock signals here - they will be unblocked after handler connection

        # CRITICAL: Enable strong focus to receive Tab key events
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        # Make inactive selections stay visible with same color
        from PyQt6.QtGui import QPalette
        palette = self.palette()
        palette.setColor(QPalette.ColorGroup.Active, QPalette.ColorRole.Highlight, QColor("#000000"))
        palette.setColor(QPalette.ColorGroup.Active, QPalette.ColorRole.HighlightedText, QColor("#FFFFFF"))
        palette.setColor(QPalette.ColorGroup.Inactive, QPalette.ColorRole.Highlight, QColor("#000000"))
        palette.setColor(QPalette.ColorGroup.Inactive, QPalette.ColorRole.HighlightedText, QColor("#FFFFFF"))
        self.setPalette(palette)

        # Add syntax highlighter for tags (with spellcheck enabled for target cells)
        # Get invisible char color and tag color from main window (theme-aware)
        main_window = self._get_main_window()
        invisible_char_color = main_window.invisible_char_color if main_window and hasattr(main_window, 'invisible_char_color') else '#999999'

        # Use theme-aware tag color (light pink in dark mode, dark red in light mode)
        is_dark = main_window and hasattr(main_window, 'theme_manager') and main_window.theme_manager and main_window.theme_manager.current_theme.name == "Dark"
        tag_color = '#FFB6C1' if is_dark else self.tag_highlight_color  # Light pink in dark mode

        self.highlighter = TagHighlighter(self.document(), tag_color, invisible_char_color, enable_spellcheck=True)

        # Style to look like a normal cell with subtle selection.
        # Background and text colors now managed by theme system.
        # Font size lives in this widget's own stylesheet (not just setFont) so
        # it wins over ThemeManager's app-level `QTextEdit { font-size }` rule
        # that gets emitted whenever the global UI scale != 100%.
        self._grid_font_pt: float | None = None
        self._grid_bg_color: str | None = None
        self._rebuild_grid_stylesheet()

        # Set document margins to 0 for compact display
        doc = self.document()
        doc.setDocumentMargin(0)

        # Configure text option for minimal line spacing
        # With zero-width spaces inserted after invisible character markers,
        # normal WordWrap will work correctly
        wrap_mode = QTextOption.WrapMode.WordWrap

        self.setWordWrapMode(wrap_mode)
        self.setAcceptRichText(False)
        # v1.10.96 — AlwaysOff instead of AsNeeded. Reported by a user
        # on a laptop screen: "no matter what I do with the resizing
        # of the text size, I always have a scroll bar in the target
        # box." Cause: QTextEdit's AsNeeded policy compares document
        # height (which includes sub-pixel line metrics + the
        # DocumentMargin we set to 0 + any zero-width-space padding
        # from the invisible-char machinery) against the viewport
        # height; a 1-px off-by-one between those two on certain font
        # rendering paths is enough to surface the scrollbar even when
        # the text visually fits. The row auto-resize machinery
        # (resizeRowToContents called after every confirm + on grid
        # load) keeps the cell tall enough for the content, so
        # AlwaysOff is safe — content never gets clipped in practice.
        # The actively-being-edited delegate editor (at the createEditor
        # site in the table delegate) keeps AsNeeded so very long
        # in-progress edits still get a usable scrollbar.
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        text_option = QTextOption()
        text_option.setWrapMode(wrap_mode)
        doc.setDefaultTextOption(text_option)

        # Set minimum height to 0 - let content determine size
        self.setMinimumHeight(0)
        self.setMaximumHeight(16777215)  # Qt's max int

    def mouseDoubleClickEvent(self, event):
        """Handle double-click to select words properly when invisibles are shown.

        When any invisible-character substitutions are active the text contains
        marker characters (·, →, °, ↵) and zero-width spaces (\u200B).
        Qt's built-in double-click word selection treats \u200B as a word
        character, causing it to include the zero-width space in the selection
        and breaking copy-to-clipboard / add-to-glossary workflows.

        We override double-click to use only the *visible* marker characters
        (·, →, °, ↵, \n, \t) as word delimiters.  \u200B is intentionally
        excluded – it is a transparent word-wrap hint, not a delimiter.
        """
        # A double-click must never make the grid jump: cancel the recenter
        # that the preceding single press scheduled (issue #240).
        main_window = self._get_main_window()
        if main_window is not None and hasattr(main_window, '_cancel_pending_click_center'):
            main_window._cancel_pending_click_center()
        # Remember when this double-click happened so a quick third press is
        # recognised as a triple-click (whole-segment select) in mousePressEvent.
        self._last_dblclick_ts = event.timestamp()
        # Use the offset captured at press time (immune to any reflow/scroll
        # the first click triggered) so we select the word actually clicked.
        if select_word_under_cursor(self, event, getattr(self, '_press_doc_pos', None)):
            return
        super().mouseDoubleClickEvent(event)
    
    def _get_main_window(self):
        """Get the main application window by traversing the parent hierarchy"""
        if not self.table:
            return None
        main_window = self.table.parent()
        while main_window and not hasattr(main_window, 'go_to_first_segment'):
            main_window = main_window.parent()
        return main_window

    def _cycle_case_at_cursor(self):
        """v1.10.229: Shift+F3 case toggler.

        Cycles the case of the selected text (or, if there is no selection,
        the word under the cursor) through:

            lowercase  →  Sentence case  →  UPPERCASE  →  lowercase

        Mirrors Word's behaviour. The undo stack still works because we mutate
        the text through QTextCursor.insertText, which the document records as
        a single undoable edit.
        """
        from PyQt6.QtGui import QTextCursor

        cursor = self.textCursor()

        # No selection → select the word at the cursor first, so the user
        # doesn't have to double-click before pressing the shortcut.
        if not cursor.hasSelection():
            cursor.select(QTextCursor.SelectionType.WordUnderCursor)
            if not cursor.hasSelection():
                return  # cursor not on a word — nothing to do

        text = cursor.selectedText()
        if not text:
            return

        # Decide the next case in the cycle. Use simple, case-fold-free
        # heuristics so that Unicode-y strings (German ß / Turkish i / etc.)
        # still cycle predictably.
        if text == text.lower():
            # lowercase → Sentence case (first cased letter capitalised,
            # rest left alone — preserves any internal capitals like
            # proper nouns inside a longer selection).
            new_text = text
            for i, ch in enumerate(text):
                if ch.isalpha():
                    new_text = text[:i] + ch.upper() + text[i + 1:]
                    break
        elif text == text.upper():
            # UPPERCASE → lowercase
            new_text = text.lower()
        else:
            # Anything else (Sentence case, Title Case, MiXeD) → UPPERCASE.
            # That keeps the three-step cycle predictable from any starting
            # point: one extra Shift+F3 always lands you on lowercase next.
            new_text = text.upper()

        if new_text == text:
            return  # nothing actually changed (e.g. all-digit selection)

        # Replace the selection in a single undoable operation, then
        # re-select the new text so a follow-up Shift+F3 keeps cycling.
        start = cursor.selectionStart()
        cursor.insertText(new_text)
        end = cursor.position()
        cursor.setPosition(start)
        cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
        self.setTextCursor(cursor)

    def _maybe_apply_autocorrect(self, typed_char: str) -> None:
        """v1.10.230: Run the AutoCorrect engine after the user types a single
        printable character. If a rule fires, replace the relevant text
        window and remember enough state to undo it on the next Backspace.

        Called from :meth:`keyPressEvent` immediately after the parent
        ``QTextEdit.keyPressEvent`` has inserted ``typed_char`` into the
        document. The engine itself sees the post-insertion text and
        decides what (if anything) to replace.
        """
        main_window = self._get_main_window()
        if main_window is None:
            return
        engine = getattr(main_window, '_autocorrect_engine', None)
        if engine is None:
            return
        # Cheap early-out without building the full settings snapshot.
        if not getattr(main_window, 'autocorrect_enabled', True):
            return
        # Locked segments: nothing to do.
        if self.isReadOnly():
            return
        text = self.toPlainText()
        cursor = self.textCursor()
        # Cursor sits one past the last typed character.
        pos = cursor.position() - 1
        if pos < 0 or pos >= len(text):
            return
        if text[pos] != typed_char:
            # The text we observe doesn't end where the keystroke landed —
            # something else mutated the document between insert and now
            # (paste, programmatic set, dictation engine). Bail out.
            return
        try:
            settings = main_window.get_autocorrect_settings()
        except Exception:
            return
        target_lang = getattr(main_window, 'target_language', None)
        conversion = engine.apply_after_typed_char(text, pos, target_lang, settings)
        if conversion is None:
            return
        self._apply_autocorrect_conversion(conversion)

    def _apply_autocorrect_conversion(self, conversion) -> None:
        """Apply a :class:`Conversion` to this editor and record a one-shot
        undo so the next Backspace can revert it (Word-style)."""
        from PyQt6.QtGui import QTextCursor
        cursor = self.textCursor()
        cursor.beginEditBlock()
        try:
            cursor.setPosition(conversion.start)
            cursor.setPosition(conversion.end, QTextCursor.MoveMode.KeepAnchor)
            cursor.insertText(conversion.replacement)
            # Move cursor to the rule's preferred resting position.
            cursor.setPosition(conversion.new_cursor_pos)
            self.setTextCursor(cursor)
        finally:
            cursor.endEditBlock()
        # Stash undo info — one Backspace will revert this exact edit.
        self._pending_autocorrect_undo = {
            'start': conversion.start,
            'inserted_len': len(conversion.replacement),
            'undo_text': conversion.undo_text,
            # Final cursor position the rule chose; we use this to detect
            # whether the user has moved the caret since the conversion.
            'cursor_after': conversion.new_cursor_pos,
            'rule_id': conversion.rule_id,
        }

    def _undo_last_autocorrect_if_applicable(self) -> bool:
        """Backspace handler: revert the last autocorrect conversion if the
        user hasn't moved the cursor away from it. Returns True if the
        revert happened (and the caller should consume the keystroke)."""
        from PyQt6.QtGui import QTextCursor
        pending = self._pending_autocorrect_undo
        if pending is None:
            return False
        cursor = self.textCursor()
        if cursor.hasSelection():
            # User has selected something — let Backspace do its normal
            # selection-delete job.
            self._pending_autocorrect_undo = None
            return False
        # Only revert if the caret hasn't drifted. Some rules leave the
        # caret past trailing whitespace they preserved (e.g. en-dash
        # eats "-- " and leaves the space), so we accept the rule's chosen
        # cursor_after position exactly.
        if cursor.position() != pending['cursor_after']:
            self._pending_autocorrect_undo = None
            return False
        cursor.beginEditBlock()
        try:
            cursor.setPosition(pending['start'])
            cursor.setPosition(
                pending['start'] + pending['inserted_len'],
                QTextCursor.MoveMode.KeepAnchor,
            )
            cursor.insertText(pending['undo_text'])
            cursor.setPosition(pending['start'] + len(pending['undo_text']))
            self.setTextCursor(cursor)
        finally:
            cursor.endEditBlock()
        self._pending_autocorrect_undo = None
        return True

    def _handle_add_to_termbase(self):
        """Handle Ctrl+E: Add selected source and target terms to glossary (with dialogue)"""
        if not self.table or self.row < 0:
            return
        
        # Get target selection (from this widget)
        target_text = self.textCursor().selectedText().strip()
        
        # Get source cell widget and its selection
        source_widget = self.table.cellWidget(self.row, 2)
        source_text = ""
        if source_widget and hasattr(source_widget, 'textCursor'):
            source_text = source_widget.textCursor().selectedText().strip()
        
        # Validate we have both selections
        if not source_text or not target_text:
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.warning(
                self,
                "Selection Required",
                "Please select text in both Source and Target cells before adding to glossary.\n\n"
                "Workflow:\n"
                "1. Select term in source/target cell\n"
                "2. Press Tab to cycle to other cell\n"
                "3. Select corresponding term\n"
                f"4. Press {format_shortcut_for_display('Ctrl+Alt+T')} (or right-click) to add to glossary"
            )
            return
        
        # Find main window and call add_to_termbase method
        main_window = self.table.parent()
        while main_window and not hasattr(main_window, 'add_term_pair_to_termbase'):
            main_window = main_window.parent()
        
        if main_window and hasattr(main_window, 'add_term_pair_to_termbase'):
            main_window.add_term_pair_to_termbase(source_text, target_text)
    
    def _handle_quick_add_to_glossary_priority(self, priority: int):
        """Handle Alt+Up/Down: Quick add selected terms to the Project/Background glossary (no dialog)"""
        if not self.table or self.row < 0:
            return

        target_text = self.textCursor().selectedText().strip()

        source_widget = self.table.cellWidget(self.row, 2)
        source_text = ""
        if source_widget and hasattr(source_widget, 'textCursor'):
            source_text = source_widget.textCursor().selectedText().strip()

        if not source_text or not target_text:
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.warning(
                self,
                "Selection Required",
                f"Please select text in both Source and Target cells before quick-adding to the termbase.\n\n"
                f"Tip: Use {format_shortcut_for_display('Ctrl+Alt+T')} to add with a dialogue where you can choose a termbase and add metadata."
            )
            return

        main_window = self.table.parent()
        while main_window and not hasattr(main_window, '_quick_add_term_with_priority'):
            main_window = main_window.parent()

        if main_window and hasattr(main_window, '_quick_add_term_with_priority'):
            main_window._quick_add_term_with_priority(priority)
    
    def _handle_superlookup_search(self):
        """Handle right-click: Search selected text in Superlookup"""
        # Get selected text
        selected_text = self.textCursor().selectedText().strip()
        
        if not selected_text:
            return
        
        # Find main window
        main_window = self.table.parent() if self.table else None
        while main_window and not hasattr(main_window, '_go_to_superlookup'):
            main_window = main_window.parent()
        
        if not main_window:
            return
        
        # Get project languages
        source_lang = None
        target_lang = None
        if hasattr(main_window, 'current_project') and main_window.current_project:
            source_lang = main_window.current_project.source_lang
            target_lang = main_window.current_project.target_lang
        
        main_window._go_to_superlookup(
            query=selected_text,
            source_lang=source_lang,
            target_lang=target_lang,
        )

    def mouseMoveEvent(self, event):
        """Show the anchored comment as a tooltip when hovering its yellow range."""
        super().mouseMoveEvent(event)
        try:
            cursor_pos = self.cursorForPosition(event.pos()).position()
            comment_pairs = _anchored_comments_at(
                self._get_main_window(), getattr(self, 'row', -1), 'target', cursor_pos)
            if comment_pairs:
                self.setToolTip(_format_comment_tooltip(comment_pairs))
            else:
                self.setToolTip("")
        except Exception:
            pass

    def contextMenuEvent(self, event):
        """Show context menu with Add to Glossary, Non-Translatables, and Spellcheck options"""
        from PyQt6.QtWidgets import QMenu
        from PyQt6.QtGui import QAction

        menu = QMenu(self)

        # "Open comment(s)" – anchored range opens that specific comment;
        # otherwise, a segment with comments offers its first (covers
        # segment-level comments, which have no highlighted range).
        if _add_open_comment_actions(self, menu, 'target', event.pos()):
            menu.addSeparator()

        # Check if cursor is on a misspelled word for spellcheck suggestions
        cursor_pos = self.cursorForPosition(event.pos())
        misspelled_word, word_start, word_end = self._get_misspelled_word_at_cursor(cursor_pos)
        
        if misspelled_word:
            # Get spellcheck suggestions
            if TagHighlighter._spellcheck_manager:
                suggestions = TagHighlighter._spellcheck_manager.get_suggestions(misspelled_word)
                
                if suggestions:
                    # Add suggestions at the top
                    for suggestion in suggestions[:5]:  # Limit to 5 suggestions
                        suggestion_action = QAction(f"✓ {suggestion}", self)
                        # Use default argument to capture current value
                        suggestion_action.triggered.connect(
                            lambda checked, s=suggestion, start=word_start, end=word_end: 
                            self._replace_word(start, end, s)
                        )
                        suggestion_action.setFont(menu.font())
                        menu.addAction(suggestion_action)
                    menu.addSeparator()
                
                # Add to dictionary action
                add_to_dict_action = QAction(
                    f"📖 Add '{misspelled_word}' to Dictionary ({format_shortcut_for_display('Alt+D')})",
                    self,
                )
                add_to_dict_action.triggered.connect(
                    lambda checked, w=misspelled_word: self._add_to_dictionary(w)
                )
                menu.addAction(add_to_dict_action)
                
                # Ignore this session action
                ignore_action = QAction(f"🔇 Ignore '{misspelled_word}' (this session)", self)
                ignore_action.triggered.connect(
                    lambda checked, w=misspelled_word: self._ignore_word(w)
                )
                menu.addAction(ignore_action)
                menu.addSeparator()
        
        # Add standard edit actions
        if self.textCursor().hasSelection():
            copy_action = QAction("Copy", self)
            copy_action.triggered.connect(self.copy)
            menu.addAction(copy_action)
            
            cut_action = QAction("Cut", self)
            cut_action.triggered.connect(self.cut)
            menu.addAction(cut_action)
            menu.addSeparator()
        
        paste_action = QAction("Paste", self)
        paste_action.triggered.connect(self.paste)
        menu.addAction(paste_action)
        menu.addSeparator()
        
        # Superlookup search action
        if self.textCursor().hasSelection():
            superlookup_action = QAction(f"🔍 Search in SuperLookup ({format_shortcut_for_display('Ctrl+K')})", self)
            superlookup_action.triggered.connect(self._handle_superlookup_search)
            menu.addAction(superlookup_action)

        # Add comment (v1.10.142) – mirrors the Ctrl+M shortcut. Anchors the
        # comment to the selected target text, or attaches a segment-level
        # comment if nothing is selected. add_comment_from_selection() reads
        # QApplication.focusWidget(), so re-focus this editor before calling
        # (the QMenu may otherwise hold focus when the action fires).
        _mw_for_comment = self.table.parent() if self.table else None
        while _mw_for_comment and not hasattr(_mw_for_comment, 'add_comment_from_selection'):
            _mw_for_comment = _mw_for_comment.parent()
        if _mw_for_comment:
            _sel = self.textCursor().hasSelection()
            add_comment_action = QAction(
                f"💬 Add comment ({format_shortcut_for_display('Ctrl+M')})"
                if _sel else
                f"💬 Add segment comment ({format_shortcut_for_display('Ctrl+M')})",
                self,
            )
            add_comment_action.triggered.connect(
                lambda checked=False, w=self, mw=_mw_for_comment: (
                    w.setFocus(),
                    QTimer.singleShot(0, mw.add_comment_from_selection),
                )
            )
            menu.addAction(add_comment_action)

        # Segment operations (confirm / status / lock / AutoTagger / clear).
        # Built from the SAME helper as the row context menu: these used to live
        # only in that menu, so they were invisible to anyone who right-clicked
        # in the target cell - which is where you actually are while working.
        try:
            _at_mw = self.table.parent() if self.table else None
            while _at_mw and not hasattr(_at_mw, 'add_segment_actions_to_menu'):
                _at_mw = _at_mw.parent()
            if _at_mw is not None and getattr(_at_mw, 'current_project', None):
                _row = getattr(self, 'row', None)
                if isinstance(_row, int) and 0 <= _row < len(_at_mw.current_project.segments):
                    # Right-clicking in a cell doesn't change the table's row
                    # selection, so act on the selection when this row is part
                    # of it, and on this row alone when it isn't.
                    try:
                        _sel_rows = {ix.row() for ix in _at_mw.table.selectionModel().selectedIndexes()}
                    except Exception:
                        _sel_rows = set()
                    if _row not in _sel_rows:
                        _at_mw.table.selectRow(_row)
                    _segs_for_menu = _at_mw.get_selected_segments_from_grid()
                    if not _segs_for_menu:
                        _segs_for_menu = [_at_mw.current_project.segments[_row]]
                    menu.addSeparator()
                    _at_mw.add_segment_actions_to_menu(menu, _segs_for_menu, _row)
        except Exception:
            pass   # a menu extra must never break the context menu

        # QuickLauncher (prompt-based actions + QuickTrans)
        try:
            main_window = self.table.parent() if self.table else None
            while main_window and not hasattr(main_window, 'run_grid_quicklauncher_prompt'):
                main_window = main_window.parent()

            quicklauncher_items = []
            if main_window and hasattr(main_window, 'prompt_manager_qt') and main_window.prompt_manager_qt:
                lib = getattr(main_window.prompt_manager_qt, 'library', None)
                if lib and hasattr(lib, 'get_quicklauncher_grid_prompts'):
                    quicklauncher_items = lib.get_quicklauncher_grid_prompts() or []

            qm_menu = menu.addMenu("⚡ QuickLauncher")

            # Bold heading at the top of the submenu
            from PyQt6.QtWidgets import QWidgetAction, QLabel
            _heading_lbl = QLabel(self.tr("  Supervertaler QuickLauncher"))
            _heading_lbl.setStyleSheet("font-weight: bold; padding: 4px 8px; color: #1565C0;")
            _heading_act = QWidgetAction(qm_menu)
            _heading_act.setDefaultWidget(_heading_lbl)
            qm_menu.addAction(_heading_act)
            qm_menu.addSeparator()

            # Capture selected text NOW before menu takes focus
            _sel_text = self.textCursor().selectedText() if self.textCursor().hasSelection() else None

            # QuickTrans at the top of the QuickLauncher
            # v1.10.61: was "(Ctrl+M)" — that was a stale display hint
            # from an earlier version; no in-app binding ever existed.
            # The actual hotkey is the GLOBAL pynput chord (Ctrl+Alt+Q
            # on Windows/Linux, Meta+Ctrl+Q on macOS) which works
            # system-wide, including inside Workbench.
            _qt_hotkey = 'Meta+Ctrl+Q' if IS_MACOS else 'Ctrl+Alt+Q'
            qt_action = QAction(f"⚡ QuickTrans ({format_shortcut_for_display(_qt_hotkey)})", self)
            qt_action.triggered.connect(self._handle_mt_quick_lookup)
            qm_menu.addAction(qt_action)

            # Chat – defer with QTimer so menu fully closes first.
            # Despite the legacy method name (`show_supervertaler_assistant`)
            # this navigates to the in-Workbench Chat tab; the standalone
            # Sidekick window was retired in v1.10.4.
            assistant_action = QAction("💬 Chat", self)
            assistant_action.triggered.connect(
                lambda: QTimer.singleShot(0, lambda: main_window.show_supervertaler_assistant(
                    initial_text=_sel_text
                ))
            )
            qm_menu.addAction(assistant_action)

            if quicklauncher_items:
                qm_menu.addSeparator()
                for rel_path, label in sorted(quicklauncher_items, key=lambda x: (x[1] or x[0]).lower()):
                    prompt_menu = qm_menu.addMenu(label or rel_path)

                    run_show = QAction("▶ Run (show response)…", self)
                    run_show.triggered.connect(
                        lambda checked=False, p=rel_path: main_window.run_grid_quicklauncher_prompt(p, origin_widget=self, behavior="show")
                    )
                    prompt_menu.addAction(run_show)

                    run_replace = QAction("↺ Run and replace selection", self)
                    run_replace.triggered.connect(
                        lambda checked=False, p=rel_path: main_window.run_grid_quicklauncher_prompt(p, origin_widget=self, behavior="replace")
                    )
                    prompt_menu.addAction(run_replace)

                    copy_clip = QAction("📋 Copy to clipboard", self)
                    copy_clip.triggered.connect(
                        lambda checked=False, p=rel_path: main_window.run_grid_quicklauncher_prompt(p, origin_widget=self, behavior="clipboard")
                    )
                    prompt_menu.addAction(copy_clip)

                menu.addSeparator()
        except Exception:
            # Never break the normal context menu due to QuickLauncher errors
            pass

        # Add to glossary action (with dialogue)
        add_to_tb_action = QAction(f"📖 Add to Termbase ({format_shortcut_for_display('Ctrl+Alt+T')})", self)
        add_to_tb_action.triggered.connect(self._handle_add_to_termbase)
        menu.addAction(add_to_tb_action)

        # Quick add to Project glossary (no dialog)
        quick_add_p1_action = QAction(f"⚡ Quick Add to Project Termbase ({format_shortcut_for_display('Alt+Up')})", self)
        quick_add_p1_action.triggered.connect(lambda: self._handle_quick_add_to_glossary_priority(1))
        menu.addAction(quick_add_p1_action)

        # Quick add to Background glossary (no dialog)
        quick_add_p2_action = QAction(f"⚡ Quick Add to Background Termbase ({format_shortcut_for_display('Alt+Down')})", self)
        quick_add_p2_action.triggered.connect(lambda: self._handle_quick_add_to_glossary_priority(2))
        menu.addAction(quick_add_p2_action)

        # Add to non-translatables action
        add_to_nt_action = QAction(f"🚫 Add to Non-Translatables ({format_shortcut_for_display('Ctrl+Alt+N')})", self)
        add_to_nt_action.triggered.connect(self._handle_add_to_nt)
        menu.addAction(add_to_nt_action)

        menu.exec(event.globalPos())

    def _get_misspelled_word_at_cursor(self, cursor):
        """Get the misspelled word at the cursor position, if any"""
        import re
        
        if not TagHighlighter._spellcheck_enabled or not TagHighlighter._spellcheck_manager:
            return None, 0, 0
        
        text = self.toPlainText()
        pos = cursor.position()
        
        # Find word boundaries around cursor position
        word_pattern = re.compile(r'\b([a-zA-ZÀ-ÿ\']+)\b', re.UNICODE)
        
        for match in word_pattern.finditer(text):
            start = match.start(1)
            end = match.end(1)
            
            if start <= pos <= end:
                word = match.group(1)
                # Check if this word is misspelled
                if not TagHighlighter._spellcheck_manager.check_word(word):
                    return word, start, end
                break
        
        return None, 0, 0

    def _replace_word(self, start: int, end: int, replacement: str):
        """Replace a word at the given position with the replacement"""
        cursor = self.textCursor()
        cursor.setPosition(start)
        cursor.setPosition(end, cursor.MoveMode.KeepAnchor)
        cursor.insertText(replacement)

    def _add_to_dictionary(self, word: str):
        """Add word to the custom dictionary"""
        if TagHighlighter._spellcheck_manager:
            TagHighlighter._spellcheck_manager.add_to_dictionary(word)
            
            # Show confirmation in main window log
            main_window = self._get_main_window()
            if main_window and hasattr(main_window, 'log'):
                main_window.log(f"✓ Added '{word}' to custom dictionary")

            # Refresh all highlighters to update other occurrences
            if main_window and hasattr(main_window, '_refresh_all_highlighters'):
                main_window._refresh_all_highlighters()
            else:
                # Fallback to local rehighlight
                self.highlighter.rehighlight()

    def _ignore_word(self, word: str):
        """Ignore word for this session"""
        if TagHighlighter._spellcheck_manager:
            TagHighlighter._spellcheck_manager.ignore_word(word)
            
            # Show confirmation in main window log
            main_window = self._get_main_window()
            if main_window and hasattr(main_window, 'log'):
                main_window.log(f"🔇 Ignoring '{word}' for this session")

            # Refresh all highlighters to update other occurrences
            if main_window and hasattr(main_window, '_refresh_all_highlighters'):
                main_window._refresh_all_highlighters()
            else:
                # Fallback to local rehighlight
                self.highlighter.rehighlight()
    
    def sizeHint(self):
        """Return compact size based on content"""
        doc = self.document()
        ideal_width = self.width() if self.width() > 0 else 200
        doc.setTextWidth(ideal_width)
        height = int(doc.size().height())
        # Add minimal padding (2px total)
        height = max(height + 2, 1)
        current_width = self.width() if self.width() > 0 else 200
        return QSize(current_width, height)
    
    def mousePressEvent(self, event):
        """Allow text selection on click and auto-select row"""
        # Capture the clicked character offset BEFORE super()/activation can
        # reflow or scroll the cell, so a following double-click selects the
        # word we actually clicked even if the text scoots (issue #240).
        try:
            self._press_doc_pos = self.cursorForPosition(event.pos()).position()
        except Exception:
            self._press_doc_pos = None
        # Triple-click selects the whole segment (like selecting a paragraph).
        # We finalise the selection in mouseReleaseEvent — doing it here would be
        # undone when Qt's own release collapses the (zero-length) drag.
        _triple = is_triple_click(self, event)
        super().mousePressEvent(event)
        if _triple:
            self._last_dblclick_ts = 0  # consume so a 4th press doesn't re-trigger
            self._triple_pending = True
            return
        # Auto-select the row when clicking in the target cell
        if self.table and self.row >= 0:
            # Check for Shift or Ctrl modifier - let Qt handle native multi-selection
            modifiers = event.modifiers()
            is_shift = modifiers & Qt.KeyboardModifier.ShiftModifier
            is_ctrl = modifiers & Qt.KeyboardModifier.ControlModifier

            # NOTE: activation + auto-centering for a click is handled in
            # focusInEvent (which fires first, from inside the super() call
            # above), including the deferred, double-click-cancellable recenter
            # for issue #240. This handler only covers clicks within the
            # already-focused cell (no focus change, so no focusInEvent).
            if is_shift or is_ctrl:
                # For Shift+click (range) or Ctrl+click (toggle), just set current cell
                # but don't call selectRow() which would clear the selection
                self.table.setCurrentCell(self.row, 3)  # Column 3 is Target
            else:
                # Normal click - select just this row
                self.table.selectRow(self.row)
                self.table.setCurrentCell(self.row, 3)  # Column 3 is Target

                # CRITICAL: Manually trigger on_cell_selected since signals aren't firing
                # Find the main window and call the method directly
                try:
                    main_window = self.table.parent()
                    while main_window and not hasattr(main_window, 'on_cell_selected'):
                        main_window = main_window.parent()
                    if main_window and hasattr(main_window, 'on_cell_selected'):
                        main_window.on_cell_selected(self.row, 3, -1, -1)
                except Exception as e:
                    print(f"Error triggering manual cell selection: {e}")

    def mouseReleaseEvent(self, event):
        """Smart word selection - expand partial selections to full words
        
        Works across multiple lines - if you select text spanning several lines,
        partial words at the START and END of your selection will be expanded.
        """
        super().mouseReleaseEvent(event)

        # Triple-click: apply the whole-segment selection now, after Qt's own
        # release handling, so it isn't collapsed. Skip the smart-word expansion.
        if getattr(self, '_triple_pending', False):
            self._triple_pending = False
            select_whole_segment(self)
            return

        # Check if smart selection is enabled
        main_window = self._get_main_window()
        if main_window and hasattr(main_window, 'enable_smart_word_selection'):
            if not main_window.enable_smart_word_selection:
                return  # Feature disabled

        # Get the current cursor
        cursor = self.textCursor()

        # Only expand if there's a selection
        if cursor.hasSelection():
            # Get selection boundaries
            start = cursor.selectionStart()
            end = cursor.selectionEnd()

            # Get the full text
            text = self.toPlainText()

            # Helper function to check if character is part of a word
            # Includes alphanumeric, underscore, hyphen, and apostrophe
            def is_word_char(char):
                # CJK / Thai etc. have no inter-word spaces; treat each such
                # character as its own token so expansion doesn't swallow the
                # whole run (str.isalnum() is True for them).
                return (char.isalnum() and not _is_cjk_char(char)) or char in "_-'"

            # Track if we need to update the selection
            selection_changed = False

            # Expand START boundary if we're in the middle of a word
            # (i.e., the character before the selection is a word character)
            if start > 0 and is_word_char(text[start - 1]):
                # Also check that the first selected character is a word char
                # (to avoid expanding when selecting from whitespace)
                if start < len(text) and is_word_char(text[start]):
                    while start > 0 and is_word_char(text[start - 1]):
                        start -= 1
                    selection_changed = True

            # Expand END boundary if we're in the middle of a word
            # (i.e., the character after the selection is a word character)
            if end < len(text) and is_word_char(text[end]):
                # Also check that the last selected character is a word char
                # (to avoid expanding when selecting to whitespace)
                if end > 0 and is_word_char(text[end - 1]):
                    while end < len(text) and is_word_char(text[end]):
                        end += 1
                    selection_changed = True

            # Set the new selection if boundaries changed
            if selection_changed:
                cursor.setPosition(start)
                cursor.setPosition(end, cursor.MoveMode.KeepAnchor)
                self.setTextCursor(cursor)

    def focusInEvent(self, event):
        """Ensure text remains visible when focused and auto-select row"""
        # If the user hasn't actually typed in this cell yet, drop any
        # programmatic undo steps (initial setPlainText, tag/comment-anchor
        # formatting) so the FIRST Ctrl+Z here falls through to app-level
        # (segment / structural) undo instead of undoing the cell's own
        # rendering. Once the user has edited the cell we keep its history so
        # Ctrl+Z undoes their typing. (Fixes split-undo needing two presses
        # when the cursor is parked in the target box.)
        if not getattr(self, '_user_has_edited', False):
            self.document().clearUndoRedoStacks()
        super().focusInEvent(event)
        # Ensure the widget is properly visible
        self.setVisible(True)
        self.show()
        # Auto-select the row when focusing the target cell
        if self.table and self.row >= 0:
            # Check for Shift or Ctrl modifier - let Qt handle native multi-selection
            from PyQt6.QtWidgets import QApplication
            modifiers = QApplication.keyboardModifiers()
            is_shift = modifiers & Qt.KeyboardModifier.ShiftModifier
            is_ctrl = modifiers & Qt.KeyboardModifier.ControlModifier

            main_window = self._get_main_window()

            # Is this focus change caused by a mouse click (vs Tab / arrow-nav /
            # programmatic focus)? Only clicks need the anti-jump treatment.
            is_mouse_focus = (main_window is not None
                              and event.reason() == Qt.FocusReason.MouseFocusReason
                              and not (is_shift or is_ctrl))

            # Defer centering only for mouse-driven focus, so a following
            # double-click can cancel it before the grid jumps (issue #240).
            # Keyboard / programmatic focus still centers immediately. This
            # focusInEvent is the real activation path for a click — it fires
            # from inside super().mousePressEvent, before the widget's own
            # mousePressEvent runs, which is why suppressing the center in
            # mousePressEvent alone did nothing.
            defer_center = (is_mouse_focus
                            and getattr(main_window, 'auto_center_active_segment', False)
                            and not getattr(main_window, 'filtering_active', False))
            if defer_center:
                main_window._click_activation = True

            # Suppress Qt's own "ensure visible" auto-scroll during click
            # activation. On a tall, partially-visible cell selectRow/
            # setCurrentCell would otherwise scroll the row into view, shifting
            # the text out from under the cursor between the two clicks of a
            # double-click so the second click hits the wrong word (issue #240).
            _prev_autoscroll = None
            if is_mouse_focus:
                _prev_autoscroll = self.table.hasAutoScroll()
                self.table.setAutoScroll(False)
            try:
                if is_shift or is_ctrl:
                    # For Shift+click (range) or Ctrl+click (toggle), just set current cell
                    # but don't call selectRow() which would clear the selection
                    self.table.setCurrentCell(self.row, 3)  # Column 3 is Target
                else:
                    # Normal focus - select just this row
                    self.table.selectRow(self.row)
                    self.table.setCurrentCell(self.row, 3)  # Column 3 is Target

                    # CRITICAL: Manually trigger on_cell_selected since signals aren't firing
                    try:
                        if main_window and hasattr(main_window, 'on_cell_selected'):
                            main_window.on_cell_selected(self.row, 3, -1, -1)
                    except Exception as e:
                        print(f"Error triggering manual cell selection: {e}")
            finally:
                if _prev_autoscroll is not None:
                    self.table.setAutoScroll(_prev_autoscroll)

            if defer_center and hasattr(main_window, '_schedule_click_center'):
                main_window._schedule_click_center(self.row)

    def keyPressEvent(self, event):
        """Handle Tab and Ctrl+E keys to cycle between source and target cells"""
        from PyQt6.QtWidgets import QApplication
        from PyQt6.QtGui import QTextCursor

        # v1.10.230: Backspace immediately after an AutoCorrect conversion
        # undoes just the conversion (restores the user's literal typing).
        # Word / memoQ / Trados all do this; users expect it.
        if (event.key() == Qt.Key.Key_Backspace
                and event.modifiers() == Qt.KeyboardModifier.NoModifier
                and getattr(self, '_pending_autocorrect_undo', None) is not None):
            if self._undo_last_autocorrect_if_applicable():
                event.accept()
                return

        # v1.10.229: Shift+F3 — toggle case of the selection (or the word at
        # the cursor if there is no selection). Cycles:
        #   lowercase → Sentence case → UPPERCASE → lowercase
        # Matches Word / memoQ / Trados behaviour.
        if event.key() == Qt.Key.Key_F3 and event.modifiers() == Qt.KeyboardModifier.ShiftModifier:
            self._cycle_case_at_cursor()
            event.accept()
            return

        # Ctrl+Z / Ctrl+Y: undo or redo this cell's own typing first; once the
        # cell has nothing left to undo/redo, fall through to the app-level
        # operation undo (Replace All, Copy Source → Target, etc.) so Ctrl+Z
        # works even with the cursor sitting in the target box.
        if event.key() == Qt.Key.Key_Z and event.modifiers() == Qt.KeyboardModifier.ControlModifier:
            if self.document().isUndoAvailable():
                super().keyPressEvent(event)  # native per-cell text undo
            else:
                mw = self._get_main_window()
                if mw and hasattr(mw, 'undo_action_handler'):
                    mw.undo_action_handler()
            event.accept()
            return
        if ((event.key() == Qt.Key.Key_Y and event.modifiers() == Qt.KeyboardModifier.ControlModifier)
                or (event.key() == Qt.Key.Key_Z
                    and event.modifiers() == (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier))):
            if self.document().isRedoAvailable():
                super().keyPressEvent(event)  # native per-cell text redo
            else:
                mw = self._get_main_window()
                if mw and hasattr(mw, 'redo_action_handler'):
                    mw.redo_action_handler()
            event.accept()
            return

        # Ctrl+C: Fix clipboard when copying with invisible characters shown
        if event.key() == Qt.Key.Key_C and event.modifiers() == Qt.KeyboardModifier.ControlModifier:
            cursor = self.textCursor()
            if cursor.hasSelection():
                selected_text = cursor.selectedText()
                # Reverse invisible character replacements before copying
                main_window = self._get_main_window()
                if main_window and hasattr(main_window, 'reverse_invisible_replacements'):
                    clean_text = main_window.reverse_invisible_replacements(selected_text)
                    # Also replace paragraph separator with newline (Qt uses U+2029)
                    clean_text = clean_text.replace('\u2029', '\n')
                    # Set clipboard with clean text
                    clipboard = QApplication.clipboard()
                    clipboard.setText(clean_text)
                    event.accept()
                    return
        
        # Handle Ctrl+Arrow word navigation when invisibles are shown
        main_window = self._get_main_window()
        if main_window and hasattr(main_window, 'invisible_display_settings'):
            if main_window.invisible_display_settings.get('spaces', False):
                ctrl_only = event.modifiers() == Qt.KeyboardModifier.ControlModifier
                ctrl_shift = event.modifiers() == (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier)
                
                if (ctrl_only or ctrl_shift) and event.key() in (Qt.Key.Key_Left, Qt.Key.Key_Right):
                    cursor = self.textCursor()
                    text = self.toPlainText()
                    pos = cursor.position()
                    word_chars = ('·', '\u200B', '\n', '\t', '→', '↵', ' ')
                    
                    if event.key() == Qt.Key.Key_Right:
                        # Move to end of current word, then skip delimiters to start of next word
                        # First skip any delimiters we're on
                        while pos < len(text) and text[pos] in word_chars:
                            pos += 1
                        # Then skip to end of word (next delimiter)
                        while pos < len(text) and text[pos] not in word_chars:
                            pos += 1
                    else:  # Key_Left
                        # Move backwards: skip delimiters, then find start of word
                        if pos > 0:
                            pos -= 1
                        # Skip any delimiters
                        while pos > 0 and text[pos] in word_chars:
                            pos -= 1
                        # Find start of word
                        while pos > 0 and text[pos - 1] not in word_chars:
                            pos -= 1
                    
                    # Apply cursor movement
                    if ctrl_shift:
                        cursor.setPosition(pos, QTextCursor.MoveMode.KeepAnchor)
                    else:
                        cursor.setPosition(pos)
                    self.setTextCursor(cursor)
                    event.accept()
                    return
        
        # Ctrl+Alt+T: Add selected terms to termbase (with dialog).
        # Was Ctrl+E in this widget before v1.9.475 (a leftover binding I
        # missed when migrating the source-side editor), and went through a
        # strict-equality modifier check that broke on Windows AltGr; fix
        # is the same as in ReadOnlyGridTextEditor – mask away
        # KeypadModifier / GroupSwitchModifier before comparing.
        if event.key() == Qt.Key.Key_T:
            _mod_mask = (Qt.KeyboardModifier.ControlModifier
                         | Qt.KeyboardModifier.AltModifier
                         | Qt.KeyboardModifier.ShiftModifier
                         | Qt.KeyboardModifier.MetaModifier)
            _mods = event.modifiers() & _mod_mask
            if _mods == (Qt.KeyboardModifier.ControlModifier
                         | Qt.KeyboardModifier.AltModifier):
                self._handle_add_to_termbase()
                event.accept()
                return
        
        # Ctrl+Up: Navigate to previous segment
        if event.key() == Qt.Key.Key_Up and _cleaned_modifiers(event) == Qt.KeyboardModifier.ControlModifier:
            main_window = self._get_main_window()
            if main_window and hasattr(main_window, 'go_to_previous_segment'):
                main_window.go_to_previous_segment()
            event.accept()
            return

        # Ctrl+Down: Navigate to next segment
        if event.key() == Qt.Key.Key_Down and _cleaned_modifiers(event) == Qt.KeyboardModifier.ControlModifier:
            main_window = self._get_main_window()
            if main_window and hasattr(main_window, 'go_to_next_segment'):
                main_window.go_to_next_segment()
            event.accept()
            return

        # Alt+Up: Quick add selected terms to Project glossary (no dialog)
        if event.key() == Qt.Key.Key_Up and _cleaned_modifiers(event) == Qt.KeyboardModifier.AltModifier:
            self._handle_quick_add_to_glossary_priority(1)
            event.accept()
            return

        # Alt+Down: Quick add selected terms to Background glossary (no dialog)
        if event.key() == Qt.Key.Key_Down and _cleaned_modifiers(event) == Qt.KeyboardModifier.AltModifier:
            self._handle_quick_add_to_glossary_priority(2)
            event.accept()
            return

        # Ctrl+Alt+N: Add selected text to non-translatables
        if event.key() == Qt.Key.Key_N and event.modifiers() == (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier):
            self._handle_add_to_nt()
            event.accept()
            return
        
        # Ctrl+Home: Navigate to first segment (pass to main window)
        if event.key() == Qt.Key.Key_Home and _cleaned_modifiers(event) == Qt.KeyboardModifier.ControlModifier:
            main_window = self._get_main_window()
            if main_window and hasattr(main_window, 'go_to_first_segment'):
                main_window.go_to_first_segment()
            event.accept()
            return
        
        # Ctrl+End: Navigate to last segment (pass to main window)
        if event.key() == Qt.Key.Key_End and _cleaned_modifiers(event) == Qt.KeyboardModifier.ControlModifier:
            main_window = self._get_main_window()
            if main_window and hasattr(main_window, 'go_to_last_segment'):
                main_window.go_to_last_segment()
            event.accept()
            return
        
        # Ctrl+, (comma): Insert next memoQ tag or wrap selection with tag pair
        if event.key() == Qt.Key.Key_Comma and event.modifiers() == Qt.KeyboardModifier.ControlModifier:
            self._insert_next_tag_or_wrap_selection()
            event.accept()
            return
        
        # Ctrl+Shift+S: Copy source text to target
        if event.key() == Qt.Key.Key_S and event.modifiers() == (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier):
            self._copy_source_to_target()
            event.accept()
            return

        # Ctrl+1..9: insert the numbered QuickTrans result into the target while
        # editing. Delegated to the main window so it works even though QTextEdit
        # would otherwise swallow the digit before the window-level QShortcut.
        if (_cleaned_modifiers(event) == Qt.KeyboardModifier.ControlModifier
                and Qt.Key.Key_1 <= event.key() <= Qt.Key.Key_9):
            main_window = self._get_main_window()
            if main_window and hasattr(main_window, 'insert_match_by_number'):
                main_window.insert_match_by_number(event.key() - Qt.Key.Key_1 + 1)
                event.accept()
                return
        
        # Ctrl+B: Apply bold formatting to selection
        if event.key() == Qt.Key.Key_B and event.modifiers() == Qt.KeyboardModifier.ControlModifier:
            self._apply_formatting_tag('b')
            event.accept()
            return
        
        # Ctrl+I: Apply italic formatting to selection
        if event.key() == Qt.Key.Key_I and event.modifiers() == Qt.KeyboardModifier.ControlModifier:
            self._apply_formatting_tag('i')
            event.accept()
            return
        
        # Ctrl+U: Apply underline formatting to selection
        if event.key() == Qt.Key.Key_U and event.modifiers() == Qt.KeyboardModifier.ControlModifier:
            self._apply_formatting_tag('u')
            event.accept()
            return
        
        # Ctrl+Enter: Confirm & Next (call main window method directly)
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and event.modifiers() == Qt.KeyboardModifier.ControlModifier:
            main_window = self._get_main_window()
            if main_window and hasattr(main_window, 'confirm_selected_or_next'):
                main_window.confirm_selected_or_next()
            event.accept()
            return
        
        # Shift+Enter: Insert line break (for multi-line content)
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and event.modifiers() == Qt.KeyboardModifier.ShiftModifier:
            super().keyPressEvent(event)
            event.accept()
            return
        
        # Plain Enter: Don't insert newline, just accept
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and event.modifiers() == Qt.KeyboardModifier.NoModifier:
            event.accept()
            return
        
        # Ctrl+Tab: Insert actual tab character
        if event.key() == Qt.Key.Key_Tab and event.modifiers() == Qt.KeyboardModifier.ControlModifier:
            self.insertPlainText('\t')
            event.accept()
            return
        
        # Tab alone: Cycle to source cell (column 2) in same row
        if event.key() == Qt.Key.Key_Tab and event.modifiers() == Qt.KeyboardModifier.NoModifier:
            if self.table and self.row >= 0:
                # Get source cell widget (column 2)
                source_widget = self.table.cellWidget(self.row, 2)
                if source_widget:
                    source_widget.setFocus()
                    self.table.setCurrentCell(self.row, 2)
                    event.accept()
                    return
        
        # Arrow Up/Down: memoQ-style segment navigation at cell boundaries
        # When cursor is at top VISUAL line and Up is pressed, go to previous segment
        # When cursor is at bottom VISUAL line and Down is pressed, go to next segment
        if event.key() in (Qt.Key.Key_Up, Qt.Key.Key_Down) and _cleaned_modifiers(event) == Qt.KeyboardModifier.NoModifier:
            cursor = self.textCursor()
            current_block = cursor.block()
            doc = self.document()
            first_block = doc.firstBlock()
            last_block = doc.lastBlock()
            
            # Get the visual line number within the current block
            layout = current_block.layout()
            if layout:
                pos_in_block = cursor.positionInBlock()
                current_visual_line = layout.lineForTextPosition(pos_in_block)
                current_line_num = current_visual_line.lineNumber() if current_visual_line.isValid() else 0
                total_lines_in_block = layout.lineCount()
            else:
                current_line_num = 0
                total_lines_in_block = 1
            
            if event.key() == Qt.Key.Key_Up:
                # Only navigate to previous segment if we're on the FIRST visual line of the FIRST block
                is_first_visual_line = (current_block == first_block and current_line_num == 0)
                if is_first_visual_line:
                    main_window = self._get_main_window()
                    if main_window and hasattr(main_window, 'go_to_previous_segment'):
                        # Get cursor column within current visual line
                        if layout and current_visual_line.isValid():
                            col_in_line = pos_in_block - int(current_visual_line.textStart())
                        else:
                            col_in_line = cursor.positionInBlock()
                        # Signal rapid navigation for performance optimization
                        main_window._arrow_key_navigation = True
                        main_window.go_to_previous_segment(target_column=col_in_line, to_last_line=True)
                        event.accept()
                        return
            
            elif event.key() == Qt.Key.Key_Down:
                # Only navigate to next segment if we're on the LAST visual line of the LAST block
                is_last_visual_line = (current_block == last_block and current_line_num >= total_lines_in_block - 1)
                if is_last_visual_line:
                    main_window = self._get_main_window()
                    if main_window and hasattr(main_window, 'go_to_next_segment'):
                        # Get cursor column within current visual line
                        if layout and current_visual_line.isValid():
                            col_in_line = pos_in_block - int(current_visual_line.textStart())
                        else:
                            col_in_line = cursor.positionInBlock()
                        # Signal rapid navigation for performance optimization
                        main_window._arrow_key_navigation = True
                        main_window.go_to_next_segment(target_column=col_in_line, to_first_line=True)
                        event.accept()
                        return
        
        # All other keys: Handle normally.
        #
        # v1.10.230: After Qt inserts the typed character (super() below),
        # consult the AutoCorrect engine. The engine sees the text post-
        # insertion and can decide to replace a 1-3-char window ending at
        # the cursor (e.g. typing the third dot promotes "..." → "…").
        #
        # We only hand the engine a character that the user actually
        # entered as printable text. Modifier-only events, function keys,
        # and dead-key compositions get filtered out by .text() being
        # empty or longer than one char.
        autocorrect_char: Optional[str] = None
        ev_text = event.text()
        if ev_text and len(ev_text) == 1 and ev_text.isprintable():
            autocorrect_char = ev_text
        super().keyPressEvent(event)
        if autocorrect_char is not None:
            # Clearing on every typed char first guarantees the
            # Backspace-undo memory only ever survives one keystroke.
            had_pending = self._pending_autocorrect_undo is not None
            self._pending_autocorrect_undo = None
            self._maybe_apply_autocorrect(autocorrect_char)
            # If we cleared a pending undo without applying a new one, that
            # means the user has typed past their window for reverting the
            # previous conversion. Fine — leave _pending_autocorrect_undo
            # as None so a subsequent Backspace deletes a character
            # normally.
            _ = had_pending  # marker var for future debugging

    def _handle_add_to_nt(self):
        """Handle Ctrl+Alt+N: Add selected text to active non-translatable list(s)"""
        # Get selected text from source cell (for NT, we typically add from source)
        # But if this is target and source is available, use source
        selected_text = self.textCursor().selectedText().strip()
        
        # If no selection in target, try getting from source
        if not selected_text and self.table and self.row >= 0:
            source_widget = self.table.cellWidget(self.row, 2)
            if source_widget and hasattr(source_widget, 'textCursor'):
                selected_text = source_widget.textCursor().selectedText().strip()
        
        if not selected_text:
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.warning(
                self,
                "Selection Required",
                "Please select text before adding to non-translatables."
            )
            return
        
        # Find main window and call add_to_nt method
        if self.table:
            main_window = self.table.parent()
            while main_window and not hasattr(main_window, 'add_text_to_non_translatables'):
                main_window = main_window.parent()
            
            if main_window and hasattr(main_window, 'add_text_to_non_translatables'):
                main_window.add_text_to_non_translatables(selected_text)
            else:
                from PyQt6.QtWidgets import QMessageBox
                QMessageBox.warning(
                    self,
                    "Feature Not Available",
                    "Non-translatables functionality not available."
                )

    def _handle_mt_quick_lookup(self):
        """Handle right-click: Open MT Quick Lookup popup for selected text or full source"""
        # Get selected text (if any) - prefer from target, then try source
        selected_text = self.textCursor().selectedText().strip() if self.textCursor().hasSelection() else None

        if not selected_text and self.table and self.row >= 0:
            # Try getting selected text from source cell
            source_widget = self.table.cellWidget(self.row, 2)
            if source_widget and hasattr(source_widget, 'textCursor'):
                cursor = source_widget.textCursor()
                if cursor.hasSelection():
                    selected_text = cursor.selectedText().strip()

        # Find main window and call show_mt_quick_popup
        main_window = self._get_main_window()
        if main_window and hasattr(main_window, 'show_mt_quick_popup'):
            main_window.show_mt_quick_popup(text_override=selected_text)

    def _insert_next_tag_or_wrap_selection(self):
        """
        Insert the next memoQ tag, HTML tag, or CafeTran pipe symbol from source, or wrap selection.
        
        Behavior:
        - If text is selected: Wrap it with the next available tag pair [N}selection{N] or |selection|
        - If no selection: Insert the next unused tag/pipe from source at cursor position
        
        Supports:
        - memoQ tags: [1}, {1], [2}, {2], etc.
        - HTML/XML tags: <li>, </li>, <b>, </b>, <i>, </i>, etc.
        - CafeTran pipe symbols: |
        
        Shortcut: Ctrl+, (comma)
        """
        if not self.table or self.row < 0:
            return
        
        # Navigate up to find main window
        main_window = self.table.parent()
        while main_window and not hasattr(main_window, 'current_project'):
            main_window = main_window.parent()
        
        if not main_window or not hasattr(main_window, 'current_project'):
            return
        
        if not main_window.current_project or self.row >= len(main_window.current_project.segments):
            return
        
        segment = main_window.current_project.segments[self.row]
        source_text = segment.source
        current_target = self.toPlainText()
        
        # Check what type of tags are in the source
        has_memoq_tags = bool(extract_memoq_tags(source_text))
        has_html_tags = bool(extract_html_tags(source_text))
        has_any_tags = has_memoq_tags or has_html_tags or bool(_inline_codes.find_codes(source_text))
        has_pipe_symbols = '|' in source_text
        
        # Check if there's a selection
        cursor = self.textCursor()
        if cursor.hasSelection():
            selected_text = cursor.selectedText()

            # Try memoQ tag pair first
            if has_memoq_tags:
                opening_tag, closing_tag = get_wrapping_tag_pair(source_text, current_target)
                if opening_tag and closing_tag:
                    wrapped_text = f"{opening_tag}{selected_text}{closing_tag}"
                    cursor.insertText(wrapped_text)
                    if hasattr(main_window, 'log'):
                        main_window.log(f"🏷️ Wrapped selection with {opening_tag}...{closing_tag}")
                    return

            # Try HTML tag pairs (e.g., <b>...</b>, <i>...</i>)
            if has_html_tags:
                opening_tag, closing_tag = get_html_wrapping_tag_pair(source_text, current_target)
                if opening_tag and closing_tag:
                    wrapped_text = f"{opening_tag}{selected_text}{closing_tag}"
                    cursor.insertText(wrapped_text)
                    if hasattr(main_window, 'log'):
                        main_window.log(f"🏷️ Wrapped selection with {opening_tag}...{closing_tag}")
                    return

            # Try CafeTran pipe symbols
            if has_pipe_symbols:
                pipes_needed = get_next_pipe_count_needed(source_text, current_target)
                if pipes_needed >= 2:
                    # Wrap with pipes
                    wrapped_text = f"|{selected_text}|"
                    cursor.insertText(wrapped_text)
                    if hasattr(main_window, 'log'):
                        main_window.log(f"🏷️ Wrapped selection with |...|")
                    return

            if hasattr(main_window, 'log'):
                main_window.log("⚠️ No tag pairs available from source")
        else:
            # No selection - insert next unused tag or pipe at cursor

            # Try memoQ tags and HTML tags (find_next_unused_tag handles both)
            if has_any_tags:
                # In compact mode, expand target placeholders before comparison
                compact_map = getattr(self, '_compact_tag_map', None)
                compare_target = expand_compact_tags(current_target, compact_map) if compact_map else current_target
                next_tag = find_next_unused_tag(source_text, compare_target)
                if next_tag:
                    # In compact mode, insert the compact placeholder instead of full tag
                    insert_text = next_tag
                    if compact_map:
                        for placeholder, full_tag in compact_map.items():
                            if full_tag == next_tag:
                                insert_text = placeholder
                                break
                    cursor.insertText(insert_text)
                    if hasattr(main_window, 'log'):
                        main_window.log(f"🏷️ Inserted tag: {insert_text}")
                    return

            # Try CafeTran pipe symbols
            if has_pipe_symbols:
                pipes_needed = get_next_pipe_count_needed(source_text, current_target)
                if pipes_needed > 0:
                    cursor.insertText('|')
                    if hasattr(main_window, 'log'):
                        main_window.log(f"🏷️ Inserted pipe symbol (|)")
                    return

            if hasattr(main_window, 'log'):
                main_window.log("✓ All tags from source already in target")

    def _copy_source_to_target(self):
        """
        Copy source text to target cell(s).

        Shortcut: Ctrl+Shift+S (macOS: Cmd+Shift+S)

        Smart behavior:
        - Multiple segments selected → copy source to target for all selected
        - Single segment → copy source to target for current segment only
        """
        if not self.table or self.row < 0:
            return

        # Navigate up to find main window
        main_window = self._get_main_window()

        if not main_window or not hasattr(main_window, 'current_project'):
            return

        if not main_window.current_project or self.row >= len(main_window.current_project.segments):
            return

        # Check if multiple segments are selected – if so, delegate to bulk
        if hasattr(main_window, 'get_selected_segments_from_grid'):
            selected = main_window.get_selected_segments_from_grid()
            if len(selected) > 1 and hasattr(main_window, '_copy_source_to_target_selected'):
                main_window._copy_source_to_target_selected(selected)
                return

        # Single segment: copy source to target for current cell
        segment = main_window.current_project.segments[self.row]
        source_text = segment.source
        old_target = getattr(segment, 'target', '') or ''
        old_status = getattr(segment, 'status', '') or ''

        # Record undo BEFORE overwriting so Ctrl+Z restores the previous
        # translation (v1.10.204). Without this, accidentally hitting
        # Ctrl+Shift+S over a completed translation was unrecoverable.
        if old_target != source_text and hasattr(main_window, 'record_undo_state'):
            try:
                main_window.record_undo_state(
                    getattr(segment, 'id', None) or getattr(segment, 'segment_id', None),
                    old_target,
                    source_text,
                    old_status,
                    old_status,
                )
            except Exception:
                pass

        # Use a select-all + insertPlainText so Qt's own per-cell undo stack
        # is preserved too (setPlainText would clear it). Belt-and-braces with
        # the app-level undo above.
        try:
            cursor = self.textCursor()
            cursor.select(cursor.SelectionType.Document)
            cursor.insertText(source_text)
            self.setTextCursor(cursor)
        except Exception:
            # Fallback – worst case, old behaviour (no per-cell undo but
            # app-level undo above still works).
            self.setPlainText(source_text)

        # Keep segment object in sync so a focus-out doesn't undo our work
        try:
            segment.target = source_text
        except Exception:
            pass

        if hasattr(main_window, 'log'):
            main_window.log(f"📋 Copied source to target (segment {self.row + 1})")

    def _apply_formatting_tag(self, tag: str):
        """
        Apply or toggle a formatting tag on the selected text.
        
        Args:
            tag: The tag to apply ('b', 'i', or 'u')
        """
        cursor = self.textCursor()
        if not cursor.hasSelection():
            return
        
        selected_text = cursor.selectedText()
        
        # Check if the text is already wrapped with this tag
        open_tag = f"<{tag}>"
        close_tag = f"</{tag}>"
        
        # Get the full text and selection position
        full_text = self.toPlainText()
        start = cursor.selectionStart()
        end = cursor.selectionEnd()
        
        # Check for existing tags just before/after selection
        prefix_has_tag = start >= len(open_tag) and full_text[start - len(open_tag):start] == open_tag
        suffix_has_tag = end + len(close_tag) <= len(full_text) and full_text[end:end + len(close_tag)] == close_tag
        
        if prefix_has_tag and suffix_has_tag:
            # Remove the tags (toggle off)
            new_text = full_text[:start - len(open_tag)] + selected_text + full_text[end + len(close_tag):]
            cursor.setPosition(start - len(open_tag))
            cursor.setPosition(end + len(close_tag), cursor.MoveMode.KeepAnchor)
            cursor.insertText(selected_text)
        else:
            # Add the tags (toggle on)
            wrapped_text = f"{open_tag}{selected_text}{close_tag}"
            cursor.insertText(wrapped_text)
            
            # Re-select the wrapped text (including tags)
            cursor.setPosition(start)
            cursor.setPosition(start + len(wrapped_text), cursor.MoveMode.KeepAnchor)
            self.setTextCursor(cursor)

    def update_display_mode(self, text: str, show_tags: bool):
        """
        Update the display based on tag view mode.
        
        Args:
            text: The raw text (with HTML tags like <b>bold</b>)
            show_tags: If True, show raw tags. If False, show formatted WYSIWYG.
        """
        # Store raw text as property for later retrieval
        self._raw_text = text
        
        self.blockSignals(True)
        if show_tags:
            # Tag view: Show plain text with visible tags
            # The TagHighlighter will colorize the tags
            self.setPlainText(text)
        else:
            # WYSIWYG view: Apply formatting. Remember whether the formatting
            # came from Phrase tags, so an edit saves it back as {b>…<b}.
            self._phrase_tags = has_phrase_tags(text)
            if self._phrase_tags or has_formatting_tags(text):
                html = get_formatted_html_display(text)
                self.setHtml(html)
            else:
                # No tags, just plain text
                self.setPlainText(text)
        self.blockSignals(False)
    
    def get_raw_text(self) -> str:
        """Get the raw text with tags, regardless of display mode."""
        return getattr(self, '_raw_text', self.toPlainText())

    def set_file_boundary(self, is_boundary: bool):
        """Mark this row as a file boundary (shows a top separator line)."""
        self._file_boundary = is_boundary

    def set_background_color(self, color: str, text_color: str = None):
        """Set the background color (and optional forced text color) for this cell.

        text_color is passed for the selection tint so the editor's text stays
        readable on the pale-blue selection background — without it, dark themes
        render light text on light blue (unreadable). Alternating-row callers
        pass no text_color, leaving the theme's default editor colour in place.
        """
        self._grid_bg_color = color
        self._grid_text_color = text_color
        self._rebuild_grid_stylesheet()

    def apply_grid_font_size(self, size_pt: float):
        """Set the displayed font size for this grid cell.

        Sets the size in this widget's own stylesheet (not just setFont) so
        the value wins over ThemeManager's app-level `QTextEdit { font-size }`
        rule emitted at non-100% UI scale. Without this, grid zoom and global
        scale would silently lose the per-cell font-size battle to that rule.
        """
        self._grid_font_pt = size_pt
        self._rebuild_grid_stylesheet()

    def _rebuild_grid_stylesheet(self):
        """Compose this cell's stylesheet from current bg colour + font size."""
        if _GridStyleBatch.defer(self):
            return
        border_color = EditableGridTextEditor.focus_border_color
        border_thickness = EditableGridTextEditor.focus_border_thickness
        bg_rule = f"background-color: {self._grid_bg_color};" if self._grid_bg_color else ""
        font_rule = f"font-size: {self._grid_font_pt:.1f}pt;" if self._grid_font_pt else ""
        _tc = getattr(self, '_grid_text_color', None)
        color_rule = f"color: {_tc};" if _tc else ""
        # Keep the border the SAME thickness in both states (transparent when
        # unfocused) so gaining focus changes only its colour, never the content
        # width. A `border: none` → `border: 2px` jump on focus re-wraps a long
        # paragraph into a narrower box, making the text scoot vertically — which
        # broke double-click word selection on big segments (issue #240).
        self.setStyleSheet(f"""
            QTextEdit {{
                border: {border_thickness}px solid transparent;
                padding: 0px 4px 0px 0px;
                {bg_rule}
                {font_rule}
                {color_rule}
            }}
            QTextEdit:focus {{
                border: {border_thickness}px solid {border_color};
            }}
            QTextEdit::selection {{
                background-color: #000000;
                color: #FFFFFF;
            }}
        """)


class SearchHighlightDelegate(QStyledItemDelegate):
    """
    Custom delegate that highlights search terms in cells while preserving full editability.
    Uses custom painting to draw highlights underneath the text.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.search_terms = {}  # Dict of {(row, col): search_term}
        self.highlight_color = QColor(255, 255, 0, 180)  # Yellow with some transparency

    def set_highlight(self, row: int, col: int, search_term: str):
        """Set a search term to highlight for a specific cell"""
        self.search_terms[(row, col)] = search_term

    def clear_highlight(self, row: int, col: int):
        """Clear highlight for a specific cell"""
        if (row, col) in self.search_terms:
            del self.search_terms[(row, col)]

    def clear_all_highlights(self):
        """Clear all highlights"""
        self.search_terms.clear()

    def paint(self, painter, option, index):
        """Custom paint method that highlights search terms"""
        row = index.row()
        col = index.column()

        # Check if this cell has a search term to highlight
        if (row, col) in self.search_terms:
            search_term = self.search_terms[(row, col)]
            text = index.data(Qt.ItemDataRole.DisplayRole) or ""

            if text and search_term:
                # Save painter state
                painter.save()

                # Draw the background first (handles selection, etc.)
                option_copy = QStyleOptionViewItem(option)
                self.initStyleOption(option_copy, index)

                # Get the style
                style = option.widget.style() if option.widget else QApplication.style()

                # Draw background
                style.drawPrimitive(QStyle.PrimitiveElement.PE_PanelItemViewItem, option_copy, painter, option.widget)

                # Calculate text rect
                text_rect = style.subElementRect(QStyle.SubElement.SE_ItemViewItemText, option_copy, option.widget)

                # Get font metrics
                fm = painter.fontMetrics()

                # Find and highlight all occurrences of the search term
                text_lower = text.lower()
                search_lower = search_term.lower()
                pos = 0

                while True:
                    found = text_lower.find(search_lower, pos)
                    if found == -1:
                        break

                    # Calculate the position of this occurrence
                    prefix = text[:found]
                    prefix_width = fm.horizontalAdvance(prefix)
                    term_width = fm.horizontalAdvance(text[found:found + len(search_term)])

                    # Draw highlight rectangle
                    highlight_rect = QRectF(
                        text_rect.left() + prefix_width,
                        text_rect.top(),
                        term_width,
                        text_rect.height()
                    )
                    painter.fillRect(highlight_rect, self.highlight_color)

                    pos = found + len(search_term)

                # Draw the text on top
                painter.setPen(option_copy.palette.color(QPalette.ColorRole.Text))
                painter.drawText(text_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, text)

                painter.restore()
                return

        # Default painting for non-highlighted cells
        super().paint(painter, option, index)


class ClickableHighlightLabel(QLabel):
    """
    Custom QLabel for highlighted search results that allows double-click to edit.
    When double-clicked, it removes itself from the table cell and triggers editing.

    Mouse events are transparent by default - the table's viewport handles them.
    We use installEventFilter on the table's viewport to catch double-clicks.
    """

    def __init__(self, table, row: int, col: int, plain_text: str, parent=None):
        super().__init__(parent)
        self.table = table
        self.row = row
        self.col = col
        self.plain_text = plain_text  # Store the original plain text for restoration
        # Make mouse events pass through to the table underneath
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)


class TermbaseHighlightWidget(QLabel):
    """Custom label widget that displays source text with termbase highlights and tooltips"""
    
    # Signal for term double-click
    term_double_clicked = None
    
    def __init__(self, source_text: str, termbase_matches: Optional[Dict[str, str]] = None, parent=None):
        """
        Args:
            source_text: The full source text to display
            termbase_matches: Dict of {term: translation} for highlighted terms
            parent: Parent widget
        """
        super().__init__(parent)
        self.source_text = source_text
        self.termbase_matches = termbase_matches if termbase_matches is not None else {}
        self.term_ranges = {}  # Store {term: (start_pos, end_pos)}
        self.setWordWrap(True)
        self.setup_display()
        self.setMouseTracking(True)
    
    def setup_display(self):
        """Create rich text display with highlighted terms"""
        html = self._create_highlighted_html()
        self.setText(html)
        self.setTextFormat(Qt.TextFormat.RichText)
    
    def _create_highlighted_html(self) -> str:
        """Create HTML with highlighted termbase matches using priority colors"""
        text = self.source_text
        
        # Sort matches by source term length (longest first) to avoid overlaps
        # Since dict keys are now term_ids, extract source terms first
        term_entries = []
        for term_id, match_info in self.termbase_matches.items():
            if isinstance(match_info, dict):
                source_term = match_info.get('source', '')
                if source_term:
                    term_entries.append((source_term, match_info))
        
        term_entries.sort(key=lambda x: len(x[0]), reverse=True)
        
        # Build HTML with highlights
        html = text
        offset = 0
        
        for term, match_info in term_entries:
            # Case-insensitive search
            search_term = term.lower()
            search_html = html.lower()
            
            # Determine color based on project/background glossary
            if isinstance(match_info, dict):
                translation = match_info.get('translation', '')
                ranking = match_info.get('ranking', 0)
                is_project = (ranking == 1) or match_info.get('is_project_termbase', False)
            else:
                translation = match_info if isinstance(match_info, str) else ''
                is_project = False
            # Project glossary = medium green, Background = light green
            color = "#A5D6A7" if is_project else "#C8E6C9"
            
            # Find all occurrences
            start = 0
            while True:
                idx = search_html.find(search_term, start)
                if idx == -1:
                    break
                
                # Extract original casing
                original_term = html[idx:idx + len(term)]
                
                # Replace with highlighted version
                tb_label = "Project" if is_project else "Background"
                highlighted = f'<span style="background-color: {color}; color: black; padding: 1px 3px; border-radius: 2px; font-weight: bold; cursor: pointer;" class="termbase-match" data-term="{term}" title="{translation} ({tb_label})">{original_term}</span>'
                
                html = html[:idx] + highlighted + html[idx + len(term):]
                search_html = html.lower()
                
                start = idx + len(highlighted)
        
        return html
    
    def mouseMoveEvent(self, ev):
        """Show tooltip when hovering over highlighted terms"""
        # Find if cursor is over a highlighted term using text format
        # This is a simplified approach - shows generic tooltip
        super().mouseMoveEvent(ev)
    
    def mouseDoubleClickEvent(self, ev):
        """Handle double-click on highlighted terms"""
        # Find which term was clicked by getting the word at the position
        # Since QLabel doesn't provide cursor position easily, we look at the click
        # and try to extract the clicked word from the text
        
        # Get position in text (approximate)
        pos = ev.pos()
        
        # Try to find which term is near the click point
        # This is a simplified approach - in production would need better handling
        for term_id, match_info in self.termbase_matches.items():
            term = match_info.get('source', '') if isinstance(match_info, dict) else str(term_id)
            translation = match_info.get('translation', match_info) if isinstance(match_info, dict) else match_info
            # Check if this term contains the click area (very approximate)
            if self.term_double_clicked is not None and callable(self.term_double_clicked):
                # For now, if we get a double-click and there are matches,
                # this is a signal to insert the first/only match
                # Better implementation would need proper hit detection
                pass
        
        super().mouseDoubleClickEvent(ev)


class WordWrapDelegate(QStyledItemDelegate):
    """Custom delegate to enable word wrap when editing cells, with search term highlighting"""

    def __init__(self, assistance_panel=None, table_widget=None, allow_source_edit=False):
        super().__init__()
        self.assistance_panel = assistance_panel
        self.table_widget = table_widget
        self.allow_source_edit = allow_source_edit  # Controls whether source can be edited
        self.search_terms = {}  # Dict of {(row, col): search_term} for highlighting
        self.highlight_color = QColor(255, 255, 0, 180)  # Yellow with some transparency

    def set_highlight(self, row: int, col: int, search_term: str):
        """Set a search term to highlight for a specific cell"""
        self.search_terms[(row, col)] = search_term

    def clear_highlight(self, row: int, col: int):
        """Clear highlight for a specific cell"""
        if (row, col) in self.search_terms:
            del self.search_terms[(row, col)]

    def clear_all_highlights(self):
        """Clear all highlights"""
        self.search_terms.clear()

    def paint(self, painter, option, index):
        """Custom paint method that highlights search terms while keeping cells editable.

        Uses QTextDocument with HTML for reliable cross-line highlighting.
        Instead of storing per-cell terms, we store a global search term and check
        if the cell's text contains it.
        """
        row = index.row()
        col = index.column()

        # Check if this cell has a search term to highlight
        # Use global search term if set, otherwise check per-cell dict
        search_term = None
        if col == 3 and hasattr(self, 'global_search_term') and self.global_search_term:
            # For target column, use global search term
            search_term = self.global_search_term
        elif col == 2 and hasattr(self, 'global_source_search_term') and self.global_source_search_term:
            # For source column, use global source search term
            search_term = self.global_source_search_term
        elif (row, col) in self.search_terms:
            search_term = self.search_terms[(row, col)]

        # Debug: Log once per column 3 cell when global_search_term is set
        if col == 3 and hasattr(self, 'global_search_term') and self.global_search_term and row == 0:
            print(f"[PAINT DEBUG] Row 0, Col 3: global_search_term='{self.global_search_term}', search_term='{search_term}'")

        if search_term:
            text = index.data(Qt.ItemDataRole.DisplayRole) or ""

            # Only highlight if the search term is actually in the text
            if text and search_term.lower() in text.lower():
                from PyQt6.QtGui import QColor, QTextDocument, QAbstractTextDocumentLayout, QPalette
                from PyQt6.QtCore import QRectF, QSizeF
                import html as html_module

                # Get style and text rect
                option_copy = QStyleOptionViewItem(option)
                self.initStyleOption(option_copy, index)
                style = option.widget.style() if option.widget else QApplication.style()

                # Draw background first (handles selection state etc.)
                style.drawPrimitive(QStyle.PrimitiveElement.PE_PanelItemViewItem, option_copy, painter, option.widget)

                # Get the text rectangle
                text_rect = style.subElementRect(QStyle.SubElement.SE_ItemViewItemText, option_copy, option.widget)

                # Add small padding like default delegate
                text_rect = text_rect.adjusted(3, 0, -3, 0)

                # Create HTML with highlighted search terms
                # Escape HTML special characters first
                escaped_text = html_module.escape(text)
                escaped_term = html_module.escape(search_term)

                # Case-insensitive replacement with highlighting
                import re
                pattern = re.compile(re.escape(escaped_term), re.IGNORECASE)

                def replace_with_highlight(match):
                    return f'<span style="background-color: #FFFF00;">{match.group(0)}</span>'

                html_text = pattern.sub(replace_with_highlight, escaped_text)

                # Create QTextDocument with the highlighted HTML
                doc = QTextDocument()
                doc.setDefaultFont(option.font)
                doc.setTextWidth(text_rect.width())
                doc.setHtml(html_text)

                painter.save()
                painter.translate(text_rect.topLeft())

                # Clip to text rect
                painter.setClipRect(QRectF(0, 0, text_rect.width(), text_rect.height()))

                # Draw the document
                doc.drawContents(painter)

                painter.restore()
                return

        # Default painting for non-highlighted cells
        super().paint(painter, option, index)
    
    def createEditor(self, parent, option, index):
        """Create a QTextEdit for multi-line editing with word wrap"""
        # Target column (column 3) - always editable
        if index.column() == 3:
            editor = GridTextEditor(parent)
            editor.assistance_panel = self.assistance_panel
            editor.table_widget = self.table_widget
            editor.current_row = index.row()
            editor.setWordWrapMode(QTextOption.WrapMode.WordWrap)
            editor.setAcceptRichText(False)  # Plain text only
            editor.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
            editor.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            
            # Ensure the row is tall enough for editing
            table = parent.parent()
            if hasattr(table, 'resizeRowToContents'):
                # Schedule row resize after editor is shown
                from PyQt6.QtCore import QTimer
                QTimer.singleShot(0, lambda: table.resizeRowToContents(index.row()))
            
            return editor
        
        # Source column (column 2) - editable for selection/dual selection, optionally editable for content
        elif index.column() == 2:
            editor = ReadOnlyGridTextEditor("", parent)
            editor.table_widget = self.table_widget
            editor.current_row = index.row()
            editor.allow_source_edit = self.allow_source_edit
            
            # If allow_source_edit is True, make it actually editable (with warning color)
            if self.allow_source_edit:
                editor.setReadOnly(False)
                editor.setStyleSheet("""
                    QTextEdit {
                        border: none;
                        background-color: #FFF9E6;
                        padding: 0px;
                        color: black;
                    }
                    QTextEdit:focus {
                        border: 1px solid #FF9800;
                        background-color: #FFFACD;
                        color: black;
                    }
                    QTextEdit::selection {
                        background-color: #000000;
                        color: #FFFFFF;
                    }
                """)
            
            # Ensure the row is tall enough for editing
            table = parent.parent()
            if hasattr(table, 'resizeRowToContents'):
                from PyQt6.QtCore import QTimer
                QTimer.singleShot(0, lambda: table.resizeRowToContents(index.row()))
            
            return editor
        else:
            return super().createEditor(parent, option, index)
    
    def setEditorData(self, editor, index):
        """Load data into the editor"""
        if isinstance(editor, (QTextEdit, ReadOnlyGridTextEditor)):
            text = index.model().data(index, Qt.ItemDataRole.EditRole)
            editor.setPlainText(text or "")
            # Ensure cursor is at start
            cursor = editor.textCursor()
            cursor.movePosition(cursor.MoveOperation.Start)
            editor.setTextCursor(cursor)
        else:
            super().setEditorData(editor, index)
    
    def setModelData(self, editor, model, index):
        """Save data from editor back to model"""
        if isinstance(editor, ReadOnlyGridTextEditor):
            # Only save if editing is allowed
            if editor.allow_source_edit:
                text = editor.toPlainText()
                model.setData(index, text, Qt.ItemDataRole.EditRole)
            # Otherwise, just close without saving (was for selection only)
        elif isinstance(editor, QTextEdit):
            text = editor.toPlainText()
            model.setData(index, text, Qt.ItemDataRole.EditRole)
        else:
            super().setModelData(editor, model, index)
    
    def updateEditorGeometry(self, editor, option, index):
        """Set the editor geometry to match the cell size"""
        if isinstance(editor, (QTextEdit, ReadOnlyGridTextEditor)):
            # Make the editor fill the cell properly with some padding
            rect = option.rect
            editor.setGeometry(rect)
        else:
            super().updateEditorGeometry(editor, option, index)
