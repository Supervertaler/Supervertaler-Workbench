"""The document preview: rendering it, keeping it in step with the grid,
and clicking in it to go to a segment.

Part of the main window (SupervertalerQt), moved here unchanged. The
methods run as the main window's own: `self` is the SupervertalerQt.
"""

import re
from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtGui import (
    QColor,
    QFont,
    QTextCharFormat,
    QTextCursor,
)
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QMainWindow,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)
from typing import Optional


class PreviewMixin:
    def _create_preview_tab(self, with_popout_button: bool = True) -> QWidget:
        """Create the Preview widget with live document preview and click-to-navigate.

        Used for both the in-panel Preview tab and (with_popout_button=False) the
        detached pop-out window. Both register in self.preview_widgets, so they
        stay in sync via refresh_preview / _scroll_preview_to_segment."""
        from PyQt6.QtWidgets import QScrollArea, QHBoxLayout, QPushButton

        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Small top bar: text size (issue #70) and, in the docked tab, "Pop out".
        bar = QHBoxLayout()
        bar.setContentsMargins(6, 4, 6, 2)
        zoom_out_btn = QPushButton("A−")
        zoom_out_btn.setToolTip(self.tr("Smaller text (Ctrl+mouse wheel also zooms)"))
        zoom_label = QPushButton(f"{round(self._get_preview_zoom() * 100)}%")
        zoom_label.setFlat(True)
        zoom_label.setToolTip(self.tr("Text size – click to reset to 100%"))
        zoom_in_btn = QPushButton("A+")
        zoom_in_btn.setToolTip(self.tr("Larger text (Ctrl+mouse wheel also zooms)"))
        for btn, step in ((zoom_out_btn, -1), (zoom_label, 0), (zoom_in_btn, 1)):
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            btn.setMinimumWidth(34)
            btn.clicked.connect(lambda _checked=False, st=step: self._step_preview_zoom(st))
            bar.addWidget(btn)
        widget.preview_zoom_label = zoom_label
        bar.addStretch(1)
        if with_popout_button:
            popout_btn = QPushButton(self.tr("⧉ Pop out"))
            popout_btn.setToolTip(self.tr("Open the preview in a separate, resizable window"))
            popout_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            popout_btn.clicked.connect(self._open_preview_window)
            bar.addWidget(popout_btn)
        layout.addLayout(bar)

        # Create scroll area for the preview
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setStyleSheet("border: none;")

        # Create the preview text widget. A tiny QTextEdit subclass draws a thin
        # outline box around the current segment (memoQ-style) directly in the
        # viewport — Qt char formats can't render a per-run border, so we paint it
        # ourselves. Position is shown as a frame, never a fill, so it never
        # competes with the status tint.
        main_window = self

        class _PreviewTextEdit(QTextEdit):
            current_box_range = None  # (start_pos, end_pos) of the current segment

            def wheelEvent(self, event):
                # Ctrl+wheel zooms the preview text (issue #70)
                if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                    delta = event.angleDelta().y()
                    if delta:
                        main_window._step_preview_zoom(1 if delta > 0 else -1)
                    event.accept()
                    return
                super().wheelEvent(event)

            def paintEvent(self, event):
                super().paintEvent(event)
                rng = self.current_box_range
                if not rng:
                    return
                try:
                    from PyQt6.QtGui import QPainter, QPen
                    start, end = rng
                    c = self.textCursor()
                    c.setPosition(max(0, start))
                    r1 = self.cursorRect(c)
                    c.setPosition(min(end, self.document().characterCount() - 1))
                    r2 = self.cursorRect(c)
                    top = min(r1.top(), r2.top()) - 3
                    bottom = max(r1.bottom(), r2.bottom()) + 3
                    vp = self.viewport().rect()
                    left = vp.left() + 3
                    right = vp.right() - 3
                    painter = QPainter(self.viewport())
                    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
                    pen = QPen(QColor('#2563eb'))  # blue = "you are here" (position, not status)
                    pen.setWidth(1)
                    painter.setPen(pen)
                    painter.drawRoundedRect(left, top, right - left, bottom - top, 4, 4)
                    painter.end()
                except Exception:
                    pass

        preview_text = _PreviewTextEdit()
        preview_text.setReadOnly(True)
        preview_text.setStyleSheet("""
            QTextEdit {
                background-color: white;
                border: none;
                padding: 20px 30px;
                font-family: Georgia, 'Times New Roman', serif;
                font-size: 11pt;
                line-height: 1.5;
            }
        """)

        # Enable click handling for navigation
        preview_text.mousePressEvent = lambda event: self._on_preview_click(event, preview_text)
        preview_text.setCursor(Qt.CursorShape.PointingHandCursor)

        scroll_area.setWidget(preview_text)
        layout.addWidget(scroll_area)

        # Store reference for updates
        widget.preview_text = preview_text
        widget.scroll_area = scroll_area

        # Track segment positions for click navigation
        widget.segment_positions = {}  # {(start_pos, end_pos): segment_id}

        # Track current highlighted segment
        widget.current_highlighted_segment_id = None

        # Hidden previews are not re-rendered on every change (issue #185), so
        # catch up when this one is shown – tab switch, pop-out window, or the
        # main window appearing with a project already loaded.
        widget.preview_sig = None

        def _showEvent(event, _w=widget, _base=widget.showEvent):
            _base(event)
            if getattr(_w, 'preview_sig', None) is None:
                from PyQt6.QtCore import QTimer
                QTimer.singleShot(0, self._refresh_preview_on_show)
        widget.showEvent = _showEvent

        # Store reference in instance for easy access
        if not hasattr(self, 'preview_widgets'):
            self.preview_widgets = []
        self.preview_widgets.append(widget)

        return widget

    def _open_preview_window(self):
        """Open the Document Preview in its own resizable top-level window.

        The window hosts a second preview widget registered in
        self.preview_widgets, so it tracks edits live (refresh_preview) and
        follows the current segment (_scroll_preview_to_segment) just like the
        docked tab — and clicking a sentence in it still jumps the grid there.
        Closing the window unregisters its widget."""
        from PyQt6.QtWidgets import QMainWindow

        existing = getattr(self, '_preview_window', None)
        if existing is not None:
            try:
                existing.raise_()
                existing.activateWindow()
                return
            except RuntimeError:
                self._preview_window = None  # previous window was destroyed

        win = QMainWindow(self)
        win.setWindowTitle(self.tr("Supervertaler — Document Preview"))
        win.setWindowFlag(Qt.WindowType.Window, True)
        pv = self._create_preview_tab(with_popout_button=False)
        win.setCentralWidget(pv)
        win.resize(720, 900)
        self._preview_window = win

        def _closeEvent(ev, _pv=pv):
            try:
                if hasattr(self, 'preview_widgets') and _pv in self.preview_widgets:
                    self.preview_widgets.remove(_pv)
            except Exception:
                pass
            self._preview_window = None
            ev.accept()
        win.closeEvent = _closeEvent

        try:
            self._render_preview(pv)
            pv.preview_sig = self._preview_content_signature()
        except Exception:
            pass
        win.show()
        win.raise_()
        win.activateWindow()

    def _toggle_preview_panel(self):
        """Toggle the right panel to the Document Preview tab and back (Ctrl+Alt+P).

        First press switches to Preview, remembering the tab you were on; pressing
        again returns to it (usually the Match Panel)."""
        if not getattr(self, 'right_tabs', None) or not hasattr(self, '_preview_tab_index'):
            return
        try:
            cur = self.right_tabs.currentIndex()
            if cur == self._preview_tab_index:
                prev = getattr(self, '_pre_preview_tab_index', 0)
                if prev is None or prev == self._preview_tab_index or prev >= self.right_tabs.count():
                    prev = 0  # Match Panel is the first right-panel tab
                self.right_tabs.setCurrentIndex(prev)
            else:
                self._pre_preview_tab_index = cur
                # Just switch — _on_right_tab_changed defers the refresh + highlight
                # to the next tick, so the tab itself appears instantly (and skips
                # re-rendering entirely when nothing has changed since last time).
                self.right_tabs.setCurrentIndex(self._preview_tab_index)
        except Exception:
            pass

    def _on_preview_click(self, event, preview_text: QTextEdit):
        """Handle click on preview text to navigate to corresponding segment"""
        from PyQt6.QtGui import QMouseEvent

        # Get cursor position from click
        cursor = preview_text.cursorForPosition(event.pos())
        click_pos = cursor.position()

        # Find which segment this position belongs to
        parent_widget = preview_text.parent()
        while parent_widget and not hasattr(parent_widget, 'segment_positions'):
            parent_widget = parent_widget.parent()

        if not parent_widget or not hasattr(parent_widget, 'segment_positions'):
            # Call parent's mousePressEvent for default behavior
            QTextEdit.mousePressEvent(preview_text, event)
            return

        segment_positions = parent_widget.segment_positions

        for (start_pos, end_pos), segment_id in segment_positions.items():
            if start_pos <= click_pos <= end_pos:
                # Navigate to this segment in the grid
                self._navigate_to_segment_in_grid(segment_id)
                break

        # Call parent's mousePressEvent for default behavior
        QTextEdit.mousePressEvent(preview_text, event)

    def _navigate_to_segment_in_grid(self, segment_id: int):
        """Navigate to a specific segment by ID in the grid"""
        if not self.current_project or not hasattr(self, 'table') or not self.table:
            return

        # Find the row for this segment ID
        for row in range(self.table.rowCount()):
            id_item = self.table.item(row, 0)
            if id_item:
                try:
                    row_segment_id = int(id_item.text())
                    if row_segment_id == segment_id:
                        # Switch to Grid tab first
                        if hasattr(self, 'main_tabs'):
                            self.main_tabs.setCurrentIndex(0)  # Grid tab
                        
                        # Handle pagination - switch to correct page if needed
                        if hasattr(self, 'page_size_combo') and self.page_size_combo.currentText() != "All":
                            try:
                                page_size = int(self.page_size_combo.currentText())
                                target_page = (row // page_size) + 1
                                if hasattr(self, 'page_number_input'):
                                    self.page_number_input.setText(str(target_page))
                                    self.go_to_page()
                            except ValueError:
                                pass

                        # Select this row and focus the target cell
                        self.table.setCurrentCell(row, 3)  # Column 3 = Target
                        self.table.scrollToItem(self.table.item(row, 0), QTableWidget.ScrollHint.PositionAtCenter)
                        
                        target_widget = self.table.cellWidget(row, 3)
                        if target_widget:
                            target_widget.setFocus()
                            # Place cursor at end of text
                            if isinstance(target_widget, QTextEdit):
                                cursor = target_widget.textCursor()
                                cursor.movePosition(cursor.MoveOperation.End)
                                target_widget.setTextCursor(cursor)

                        self.log(f"📄 Preview: Navigated to segment {segment_id}")
                        return
                except (ValueError, AttributeError):
                    continue

    def _current_segment_source(self) -> Optional[str]:
        """Source text of the segment selected in the grid (used by the
        results panel to adapt inline codes in TM matches, issue #194)."""
        seg_id = self._get_current_segment_id()
        if seg_id is None:
            return None
        segments = self.current_project.segments
        row = self.table.currentRow()
        if 0 <= row < len(segments) and segments[row].id == seg_id:
            return segments[row].source
        for seg in segments:
            if seg.id == seg_id:
                return seg.source
        return None

    def _get_current_segment_id(self) -> Optional[int]:
        """Get the ID of the currently selected segment in the grid"""
        if not self.current_project or not hasattr(self, 'table') or not self.table:
            return None
        
        current_row = self.table.currentRow()
        if current_row < 0:
            return None
        
        id_item = self.table.item(current_row, 0)
        if id_item:
            try:
                return int(id_item.text())
            except (ValueError, AttributeError):
                pass
        
        return None

    def _is_preview_tab_active(self) -> bool:
        """Check if the Preview tab is currently selected in the right panel.
        
        Used to optimize performance by skipping heavy lookups when user is
        navigating in Preview mode (they're likely just reading, not translating).
        """
        if not hasattr(self, 'right_tabs') or not self.right_tabs:
            return False
        if not hasattr(self, '_preview_tab_index'):
            return False
        return self.right_tabs.currentIndex() == self._preview_tab_index

    def _scroll_preview_to_segment(self, segment_id: int):
        """Highlight + scroll to the current segment in every VISIBLE preview.

        Updates whichever previews are actually on screen — the docked Preview
        tab when it's the active tab AND/OR the popped-out window — and skips
        hidden ones, so grid navigation stays cheap. (Previously this was gated
        on the docked tab being active, so the pop-out window never followed the
        cursor once you switched the docked panel to another tab.)
        """
        if not hasattr(self, 'preview_widgets') or not self.preview_widgets:
            return
        if not self.current_project or not self.current_project.segments:
            return

        for widget in self.preview_widgets:
            if not hasattr(widget, 'segment_positions') or not hasattr(widget, 'preview_text'):
                continue
            # Only touch previews that are actually visible (docked tab current,
            # or pop-out window open). Hidden tab pages report isVisible() False.
            try:
                if not widget.isVisible():
                    continue
            except RuntimeError:
                continue

            preview_text = widget.preview_text

            # Reverse map: segment id → (start, end) for the current render.
            id2range = {sid: (s, e) for (s, e), sid in widget.segment_positions.items()}
            target = id2range.get(segment_id)
            if target is None:
                continue
            target_start, target_end = target

            # O(1) highlight. _render_preview already paints every segment with its
            # status tint, so we only repaint TWO segments here: restore the
            # previously-highlighted one to its status tint, then tint the new
            # current one light-blue. (Re-painting all ~500 segments cost ~2s on a
            # large file — on every toggle AND every arrow-key move with the
            # preview open. This brings it down to a couple of cursor operations.)
            def _status_bg(_sid):
                _seg = next((x for x in self.current_project.segments if x.id == _sid), None)
                if _seg and _seg.status in ('confirmed', 'approved', 'proofread'):
                    return QColor('white')
                return QColor('#fff5e0')

            def _paint(_s, _e, _color):
                _c = preview_text.textCursor()
                _c.setPosition(_s)
                _c.setPosition(_e, QTextCursor.MoveMode.KeepAnchor)
                _f = _c.charFormat()
                _f.setBackground(_color)
                _c.mergeCharFormat(_f)

            prev_id = getattr(widget, 'current_highlighted_segment_id', None)
            if prev_id is not None and prev_id != segment_id and prev_id in id2range:
                _ps, _pe = id2range[prev_id]
                _paint(_ps, _pe, _status_bg(prev_id))

            _paint(target_start, target_end, QColor('#cfe3ff'))  # current segment (precise)
            widget.current_highlighted_segment_id = segment_id

            # Disable the old full-width outline box — the precise fill above is
            # now the current-segment indicator.
            if hasattr(preview_text, 'current_box_range'):
                preview_text.current_box_range = None
                preview_text.viewport().update()

            # Now scroll to the target segment, centered in viewport
            scroll_cursor = preview_text.textCursor()
            scroll_cursor.setPosition(target_start)
            preview_text.setTextCursor(scroll_cursor)
            self._center_cursor_in_preview(preview_text)

    def _center_cursor_in_preview(self, preview_text: QTextEdit):
        """Center the current cursor position in the preview viewport"""
        # Get the cursor rectangle (position in document coordinates)
        cursor_rect = preview_text.cursorRect()
        
        # Get viewport height
        viewport_height = preview_text.viewport().height()
        
        # Calculate scroll position to center the cursor
        # cursor_rect.top() is relative to the viewport, we need document position
        scrollbar = preview_text.verticalScrollBar()
        current_scroll = scrollbar.value()
        
        # The cursor rect top is relative to viewport, so add current scroll to get document position
        cursor_doc_y = current_scroll + cursor_rect.top()
        
        # Calculate target scroll to put cursor in center
        target_scroll = cursor_doc_y - (viewport_height // 2)
        
        # Clamp to valid scroll range
        target_scroll = max(0, min(target_scroll, scrollbar.maximum()))
        
        scrollbar.setValue(target_scroll)

    def _schedule_preview_refresh(self, delay_ms: int = 200):
        """Debounced live refresh of the Document Preview.

        Only re-renders while the Preview tab is actually visible — when it's
        hidden the refresh is skipped (it re-renders on show via
        _on_right_tab_changed), so editing stays cheap. Called on every target
        edit and on confirm so the preview tracks the document live: text flips
        from source to translation as you type, and the status background
        colours update as you confirm.
        """
        try:
            if not getattr(self, 'right_tabs', None) or not hasattr(self, '_preview_tab_index'):
                return
            if self.right_tabs.currentIndex() != self._preview_tab_index:
                return  # hidden — will refresh when its tab is next shown
            timer = getattr(self, '_preview_refresh_timer', None)
            if timer is None:
                from PyQt6.QtCore import QTimer
                timer = QTimer(self)
                timer.setSingleShot(True)
                timer.timeout.connect(self.refresh_preview)
                self._preview_refresh_timer = timer
            timer.start(delay_ms)
        except Exception:
            pass

    def _get_preview_zoom(self) -> float:
        """Current preview zoom factor, loaded once from settings (1.0 = 100%)."""
        zoom = getattr(self, '_preview_zoom', None)
        if zoom is None:
            try:
                zoom = float(self.load_general_settings().get('preview_zoom', 1.0) or 1.0)
            except Exception:
                zoom = 1.0
            zoom = min(max(zoom, self.PREVIEW_ZOOM_STEPS[0]), self.PREVIEW_ZOOM_STEPS[-1])
            self._preview_zoom = zoom
        return zoom

    def _step_preview_zoom(self, direction: int):
        """Zoom the preview one step in (+1), out (-1), or back to 100% (0).
        Applies to every open preview (tab and pop-out) and is remembered."""
        current = self._get_preview_zoom()
        steps = self.PREVIEW_ZOOM_STEPS
        if direction == 0:
            zoom = 1.0
        elif direction > 0:
            zoom = next((z for z in steps if z > current + 1e-6), steps[-1])
        else:
            zoom = next((z for z in reversed(steps) if z < current - 1e-6), steps[0])
        if abs(zoom - current) < 1e-6:
            return
        self._preview_zoom = zoom
        try:
            settings = self.load_general_settings()
            settings['preview_zoom'] = zoom
            self.save_general_settings(settings)
        except Exception as e:
            self.log(f"⚠ Could not save preview zoom: {e}")
        for widget in getattr(self, 'preview_widgets', []) or []:
            label = getattr(widget, 'preview_zoom_label', None)
            if label is not None:
                label.setText(f"{round(zoom * 100)}%")
        self.refresh_preview(force=True)
        try:
            seg = self._get_current_segment_id()
            if seg is not None:
                self._scroll_preview_to_segment(seg)
        except Exception:
            pass

    def refresh_preview(self, force: bool = False):
        """Refresh all preview tabs with current document content.

        Skips the expensive full re-render when nothing affecting the preview has
        changed since the last render (compared via a cheap content signature) —
        so toggling the Preview on/off without editing is instant. Pass
        force=True to always re-render.
        """
        if not self.current_project or not self.current_project.segments:
            # Clear previews if no project
            if hasattr(self, 'preview_widgets'):
                for widget in self.preview_widgets:
                    if hasattr(widget, 'preview_text'):
                        widget.preview_text.clear()
                        widget.segment_positions = {}
                        widget.preview_sig = None
            return

        if not hasattr(self, 'preview_widgets') or not self.preview_widgets:
            return

        sig = self._preview_content_signature()
        for widget in [w for w in self.preview_widgets if hasattr(w, 'preview_text')]:
            try:
                visible = widget.isVisible()
            except RuntimeError:
                continue
            if not visible:
                # Issue #185: a hidden preview (the usual case – the Match Panel
                # is in front) is not rendered on every grid load; it renders
                # when it is shown (see showEvent in _create_preview_tab). On a
                # 17,000-segment import this render was most of the wait.
                widget.preview_sig = None
                continue
            if not force and widget.segment_positions and getattr(widget, 'preview_sig', None) == sig:
                continue  # already current – caller re-applies the current-segment highlight
            self._render_preview(widget)
            widget.preview_sig = sig

    def _preview_content_signature(self) -> str:
        """A cheap digest of everything the preview render depends on (segment
        text, status and structure). Lets refresh_preview skip redundant renders."""
        import hashlib
        h = hashlib.md5()
        for s in self.current_project.segments:
            h.update((
                f"{s.id}\x1f{s.source or ''}\x1f{s.target or ''}\x1f{s.status or ''}"
                f"\x1f{getattr(s, 'paragraph_id', '')}\x1f{getattr(s, 'type', '')}"
                f"\x1f{getattr(s, 'style', '')}\n"
            ).encode('utf-8', 'replace'))
        return h.hexdigest()

    def _refresh_preview_on_show(self):
        """Deferred preview refresh, run when the Preview tab becomes visible.

        Runs on the next event-loop tick so the tab switch paints first (the user
        is taken to the Preview immediately), then the content refreshes and the
        current segment is re-highlighted."""
        try:
            self.refresh_preview()
            seg = self._get_current_segment_id()
            if seg is not None:
                self._scroll_preview_to_segment(seg)
        except Exception as e:
            self.log(f"⚠ Preview refresh on show failed: {e}")

    def _render_preview(self, widget):
        """Render the document preview with realistic formatting"""
        if not hasattr(widget, 'preview_text'):
            return

        preview_text = widget.preview_text
        preview_text.clear()
        zoom = self._get_preview_zoom()  # issue #70: A-/A+ in the preview bar
        widget.segment_positions = {}
        # A fresh render repaints every segment with its status tint and clears the
        # highlight, so forget the previous current-segment (the O(1) highlight in
        # _scroll_preview_to_segment relies on this to avoid restoring a stale one).
        widget.current_highlighted_segment_id = None
        if hasattr(preview_text, 'current_box_range'):
            preview_text.current_box_range = None

        if not self.current_project or not self.current_project.segments:
            return

        cursor = preview_text.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.Start)
        # One edit block for the whole render: the document is laid out once at
        # the end instead of after every insert (issue #185).
        cursor.beginEditBlock()
        try:
            self._render_preview_segments(widget, preview_text, cursor, zoom)
        finally:
            cursor.endEditBlock()

        # Scroll to current segment if we have one
        if hasattr(widget, 'current_segment_start_pos') and widget.current_segment_start_pos is not None:
            # Create cursor at the current segment position
            scroll_cursor = preview_text.textCursor()
            scroll_cursor.setPosition(widget.current_segment_start_pos)
            preview_text.setTextCursor(scroll_cursor)
            # Center the segment in the viewport
            # Use QTimer to delay centering until after layout is complete
            from PyQt6.QtCore import QTimer
            QTimer.singleShot(0, lambda pt=preview_text: self._center_cursor_in_preview(pt))
        else:
            # Set cursor to beginning if no current segment
            cursor.movePosition(QTextCursor.MoveOperation.Start)
            preview_text.setTextCursor(cursor)

    def _render_preview_segments(self, widget, preview_text, cursor, zoom):
        """Insert every segment into the preview at ``cursor`` (see _render_preview)."""
        # Pre-calculate list numbers for numbered list items
        import re
        list_numbers = {}  # {segment_id: list_number}
        list_counter = 0
        prev_was_ordered_list = False

        for seg in self.current_project.segments:
            source_text = seg.source.strip() if seg.source else ""

            # Check for list tags (new <li-o>, <li-b> and legacy <li>)
            has_li_ordered = '<li-o>' in source_text or ('<li>' in source_text and '<li-b>' not in source_text)
            has_li_bullet = '<li-b>' in source_text

            if has_li_ordered:
                # Check for explicit number in text
                num_match = re.match(r'^<li(?:-o)?>\s*(\d+)[.)\s]', source_text)
                if num_match:
                    list_numbers[seg.id] = int(num_match.group(1))
                    list_counter = int(num_match.group(1))
                else:
                    # Reset counter if starting a new list (gap since last ordered item)
                    if not prev_was_ordered_list:
                        list_counter = 0
                    list_counter += 1
                    list_numbers[seg.id] = list_counter
                prev_was_ordered_list = True
            else:
                # Not an ordered list item - mark gap for potential new list
                prev_was_ordered_list = False

        # Render all segments
        current_segment_id = self._get_current_segment_id()
        segments = self.current_project.segments
        
        for idx, seg in enumerate(segments):
            style = getattr(seg, 'style', 'Normal') or 'Normal'
            paragraph_id = getattr(seg, 'paragraph_id', 0)
            seg_type = getattr(seg, 'type', 'para') or 'para'
            
            # Normalize type for comparison - grid shows symbols like "¶" for para, "Sub" for subtitle
            # Type field values: "para", "heading", "list_item", "table_cell", "Sub", "¶", etc.
            seg_type_lower = seg_type.lower() if seg_type else 'para'
            
            # Check if this is a list item (need source_text first)
            source_text = seg.source.strip() if seg.source else ""

            # Check if this is a heading/subtitle
            # Also detect bold-wrapped short text as section headings (e.g., <b>TECHNICAL FIELD</b>)
            is_heading = ('Heading' in style or 'Title' in style or 'Subtitle' in style
                          or seg_type_lower in ('heading', 'sub', 'subtitle', 'title'))

            # Detect bold section headings: <b>TEXT</b> where text is short and likely a heading
            if not is_heading and source_text.startswith('<b>') and source_text.endswith('</b>'):
                inner_text = source_text[3:-4].strip()  # Remove <b> and </b>
                # Short text (under 80 chars) that's bold is likely a section heading
                if len(inner_text) < 80 and '<' not in inner_text:
                    is_heading = True
            is_list_item = ('<li-o>' in source_text or '<li-b>' in source_text or 
                           '<li>' in source_text or seg_type_lower == 'list_item')
            
            # Check if this is a new paragraph (¶ symbol or "para" type)
            is_new_paragraph = seg_type == '¶' or seg_type_lower == 'para'
            
            # Determine spacing before this segment
            need_double_break = False  # Empty line (for paragraph separation)
            need_single_break = False  # Line break (for headings, list items)
            need_space = False         # Just a space (running text in same paragraph)
            
            if idx > 0:
                prev_seg = segments[idx - 1]
                prev_style = getattr(prev_seg, 'style', 'Normal') or 'Normal'
                prev_paragraph_id = getattr(prev_seg, 'paragraph_id', 0)
                prev_type = getattr(prev_seg, 'type', 'para') or 'para'
                prev_type_lower = prev_type.lower() if prev_type else 'para'
                
                prev_is_heading = ('Heading' in prev_style or 'Title' in prev_style or
                                   'Subtitle' in prev_style or
                                   prev_type_lower in ('heading', 'sub', 'subtitle', 'title'))
                # Also detect bold section headings in previous segment
                prev_source = prev_seg.source.strip() if prev_seg.source else ""
                if not prev_is_heading and prev_source.startswith('<b>') and prev_source.endswith('</b>'):
                    prev_inner = prev_source[3:-4].strip()
                    if len(prev_inner) < 80 and '<' not in prev_inner:
                        prev_is_heading = True
                prev_is_paragraph = prev_type == '¶' or prev_type_lower == 'para'
                prev_is_list = prev_type_lower == 'list_item' or '<li' in (prev_seg.source or '')
                
                # Rules for spacing:
                # 1. After a heading: double break (empty line)
                if prev_is_heading:
                    need_double_break = True
                # 2. Before a heading: double break
                elif is_heading:
                    need_double_break = True
                # 3. Paragraph flow. Sentences split from the SAME source paragraph
                #    share a paragraph_id, so they must join as running text — only a
                #    genuine paragraph_id change starts a new paragraph. (This mirrors
                #    how the export groups segments by paragraph_id.) When no
                #    paragraph_id is available (all-zero, e.g. some non-DOCX imports),
                #    fall back to treating each ¶ segment as its own paragraph.
                elif is_new_paragraph and prev_is_paragraph:
                    if paragraph_id == prev_paragraph_id and paragraph_id != 0:
                        # Same source paragraph → running text (this is what makes the
                        # preview follow the document's paragraph structure)
                        need_space = True
                    elif paragraph_id != prev_paragraph_id and (paragraph_id != 0 or prev_paragraph_id != 0):
                        # Genuinely different paragraphs
                        need_double_break = True
                    elif prev_type == '¶' and seg_type == '¶':
                        # No paragraph_id info → each ¶ is its own paragraph (fallback)
                        need_double_break = True
                    else:
                        # Same paragraph, running text
                        need_space = True
                # 4. List items: ordered list items (like claims) get paragraph spacing
                elif is_list_item or prev_is_list:
                    # Ordered list items (<li-o>) get double breaks for visual separation
                    is_ordered_list = '<li-o>' in source_text or ('<li>' in source_text and '<li-b>' not in source_text)
                    prev_is_ordered = '<li-o>' in (prev_seg.source or '') or ('<li>' in (prev_seg.source or '') and '<li-b>' not in (prev_seg.source or ''))
                    if is_ordered_list or prev_is_ordered:
                        need_double_break = True  # Paragraph-like spacing for numbered items
                    else:
                        need_single_break = True  # Bullet lists stay compact
                # 5. Default: space (running text)
                else:
                    need_space = True
            
            # Insert spacing/breaks
            if need_double_break:
                cursor.insertText("\n\n")  # Empty line between paragraphs
            elif need_single_break:
                cursor.insertText("\n")    # Line break
            elif need_space:
                cursor.insertText(" ")     # Space between sentences
            
            # Set formatting based on style
            char_format = QTextCharFormat()
            char_format.setFontFamily("Georgia")
            
            # Check type field for heading detection as well
            if is_heading and seg_type_lower in ('sub', 'subtitle'):
                # Subtitle style (like "TECHNISCH DOMEIN")
                char_format.setFontPointSize(13 * zoom)
                char_format.setFontWeight(QFont.Weight.Bold)
                char_format.setForeground(QColor('#1f4068'))
            elif 'Heading 1' in style or 'Heading1' in style or 'Title' in style or seg_type_lower == 'title':
                char_format.setFontPointSize(18 * zoom)
                char_format.setFontWeight(QFont.Weight.Bold)
                char_format.setForeground(QColor('#1a1a2e'))
            elif 'Heading 2' in style or 'Heading2' in style:
                char_format.setFontPointSize(15 * zoom)
                char_format.setFontWeight(QFont.Weight.Bold)
                char_format.setForeground(QColor('#16213e'))
            elif 'Heading 3' in style or 'Heading3' in style or seg_type_lower == 'heading':
                char_format.setFontPointSize(13 * zoom)
                char_format.setFontWeight(QFont.Weight.Bold)
                char_format.setForeground(QColor('#1f4068'))
            elif is_heading:
                # Bold section headings detected from <b>TEXT</b> pattern
                char_format.setFontPointSize(12 * zoom)
                char_format.setFontWeight(QFont.Weight.Bold)
                char_format.setForeground(QColor('#1a365d'))
            else:
                char_format.setFontPointSize(11 * zoom)
                char_format.setForeground(QColor('#2d2d2d'))

            # Determine what text to display
            if seg.target and seg.target.strip():
                display_text = seg.target
                is_translated = True
            else:
                display_text = seg.source if seg.source else ""
                is_translated = False

            # Status background (Option A — memoQ-minimal): a confirmed segment
            # reads as clean final text with no tint; anything unconfirmed gets a
            # single faint amber tint ("still to do"). The current segment is
            # marked by a thin blue outline box drawn in the viewport (see
            # _PreviewTextEdit.paintEvent), not a fill, so position never competes
            # with status.
            if seg.status in ('confirmed', 'approved', 'proofread'):
                char_format.setBackground(QColor('white'))  # done → blends with the page
            else:
                char_format.setBackground(QColor('#fff5e0'))  # unconfirmed → faint amber

            # Gray out untranslated text
            if not is_translated:
                char_format.setForeground(QColor('#888888'))
                char_format.setFontItalic(True)

            # Store position for click navigation
            start_pos = cursor.position()

            # Render text with list formatting
            self._render_formatted_text(cursor, display_text, char_format, 
                                        list_number=list_numbers.get(seg.id))

            end_pos = cursor.position()
            widget.segment_positions[(start_pos, end_pos)] = seg.id
            
            # Track position of current segment for scrolling + the outline box
            if seg.id == current_segment_id:
                widget.current_segment_start_pos = start_pos
                preview_text.current_box_range = (start_pos, end_pos)

            # Note: Line breaks are now handled at the START of each segment based on context
            # This ensures running text flows naturally while paragraphs are separated

        # Add final newline at end of document
        cursor.insertText("\n")

    def _render_formatted_text(self, cursor, text: str, base_format: QTextCharFormat,
                                list_number: Optional[int] = None):
        """Render text with list prefix and inline formatting tags preserved"""
        import re

        # Check for list tags
        has_li_bullet = '<li-b>' in text
        has_li_ordered = '<li-o>' in text or ('<li>' in text and '<li-b>' not in text)

        # Remove list tags from display  
        text = re.sub(r'</?li-b>', '', text)
        text = re.sub(r'</?li-o>', '', text)
        text = re.sub(r'</?li>', '', text)

        # Add list prefix if this is a list item
        if has_li_bullet or has_li_ordered:
            bullet_format = QTextCharFormat(base_format)
            bullet_format.setForeground(QColor('#ff6600'))  # Orange
            bullet_format.setFontWeight(QFont.Weight.Bold)
            bullet_format.setFontItalic(False)

            if has_li_ordered and list_number:
                cursor.insertText(f"{list_number}. ", bullet_format)
            elif has_li_ordered:
                cursor.insertText("• ", bullet_format)  # Fallback if no number
            else:
                cursor.insertText("• ", bullet_format)

        # Parse text for inline formatting tags
        tag_pattern = re.compile(r'(</?(?:b|i|u|bi|sub|sup)>)')
        parts = tag_pattern.split(text)

        is_bold = False
        is_italic = False
        is_underline = False
        is_subscript = False
        is_superscript = False

        for part in parts:
            if not part:
                continue

            if part == '<b>':
                is_bold = True
                continue
            elif part == '</b>':
                is_bold = False
                continue
            elif part == '<i>':
                is_italic = True
                continue
            elif part == '</i>':
                is_italic = False
                continue
            elif part == '<u>':
                is_underline = True
                continue
            elif part == '</u>':
                is_underline = False
                continue
            elif part == '<bi>':
                is_bold = True
                is_italic = True
                continue
            elif part == '</bi>':
                is_bold = False
                is_italic = False
                continue
            elif part == '<sub>':
                is_subscript = True
                continue
            elif part == '</sub>':
                is_subscript = False
                continue
            elif part == '<sup>':
                is_superscript = True
                continue
            elif part == '</sup>':
                is_superscript = False
                continue

            # Apply formatting to text
            text_format = QTextCharFormat(base_format)
            
            if is_bold:
                text_format.setFontWeight(QFont.Weight.Bold)
            if is_italic:
                text_format.setFontItalic(True)
            if is_underline:
                text_format.setUnderlineStyle(QTextCharFormat.UnderlineStyle.SingleUnderline)
            if is_subscript:
                text_format.setVerticalAlignment(QTextCharFormat.VerticalAlignment.AlignSubScript)
            if is_superscript:
                text_format.setVerticalAlignment(QTextCharFormat.VerticalAlignment.AlignSuperScript)

            cursor.insertText(part, text_format)

    def highlight_preview_segment(self, segment_id: Optional[int] = None):
        """Update the preview to highlight the specified segment (or current if None)"""
        if segment_id is None:
            segment_id = self._get_current_segment_id()
        
        if not segment_id:
            return
            
        # Store for later reference
        self._highlighted_segment_id = segment_id
