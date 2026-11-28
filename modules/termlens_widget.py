"""
TermLens Widget - RYS-style Inline Terminology Display

Displays source text with termbase translations shown directly underneath each word/phrase.
Inspired by the RYS Trados plugin's inline term visualization.

Features:
- Visual mapping: translations appear under their source terms
- Hover tooltips: show synonyms/alternatives
- Click to insert: click any translation to insert into target
- Multi-word term support: handles both single words and phrases
"""

from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QLabel, QFrame, QScrollArea,
                              QHBoxLayout, QPushButton, QToolTip, QLayout, QLayoutItem, QSizePolicy, QStyle,
                              QMenu, QMessageBox, QApplication)
from PyQt6.QtCore import Qt, QPoint, pyqtSignal, QRect, QSize, QTimer
from PyQt6.QtGui import QFont, QFontMetrics, QCursor, QAction, QPainter, QColor, QPen
from typing import Dict, List, Optional, Tuple
import re
from modules.shortcut_display import format_shortcut_for_display


class _ChipContainer(QWidget):
    """QWidget subclass for the coloured target chip background.

    v1.10.83: indicator-painting moved out of this class onto a
    sibling overlay widget (_CornerIndicators below) so the
    indicators can overflow above the chip's top edge — matching
    the Trados TermLens visual where the amber dot sits half-inside
    half-outside the chip's corner. The chip itself stays at its
    original (compact) height regardless of whether indicators are
    present, so every chip in a TermLens row aligns at the same
    vertical baseline. Reported as a UX issue by a user comparing
    the Workbench TermLens with the Trados TermLens side-by-side:
    chips with indicators sat 10-12 px lower than chips without
    them, because the indicator headroom was reserved INSIDE the
    chip layout.

    The class is kept (rather than reverted to plain QWidget)
    because the v1.10.75 hover-state stylesheet still relies on
    the QSS ``:hover`` pseudo-state, and there's no harm in the
    extra type.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        # v1.10.84 fix: enable stylesheet background rendering for a
        # custom QWidget subclass. Without this attribute, Qt's
        # default paint path does NOT apply the QSS
        # ``background-color`` rule on bare QWidget subclasses
        # (only on "natively styled" widget classes like
        # QPushButton). The v1.10.83 removal of the paintEvent
        # override skipped this attribute, which is why all chips
        # rendered with the panel's white background instead of
        # the pink/blue/red/amber/purple chip colours. Reported by
        # a user: "the background colors are now gone".
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        # v1.10.92 — "current chip" highlight state. The ring is
        # painted INSIDE the chip on top of the QSS background. We
        # have to draw inside _ChipContainer rather than on its
        # TermBlock parent because Qt paints children AFTER the
        # parent's paintEvent — anything we drew on the TermBlock
        # would be covered by the chip's background fill. Reported
        # by a user (v1.10.91): "the corners are not outlined in
        # blue". That was the chip background overlaying the ring
        # everywhere except where the rounded-corner cut-outs let
        # the ring poke through.
        self._is_current = False

    def set_current(self, current: bool):
        """Toggle the focus ring drawn around the chip's interior."""
        if self._is_current == current:
            return
        self._is_current = current
        self.update()

    def paintEvent(self, event):
        # Let Qt paint the QSS background first (because WA_StyledBackground
        # is set), then layer the ring on top so it isn't covered up.
        super().paintEvent(event)
        if not self._is_current:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor("#0D47A1"))  # Material-blue 900 — strong contrast on every chip colour
        # v1.10.94 — 1-px stroke instead of 2 px. User feedback after
        # v1.10.93: "Reduce the thickness of the blue outline somewhat."
        # The 2-px ring on a ~25-px-tall chip looked heavy next to the
        # chip's own 1-px native border. 1 px reads as a delicate accent
        # that still wins against every chip background colour.
        pen.setWidth(1)
        painter.setPen(pen)
        # Inset by 0.5 px so the antialiased 1-px stroke sits crisply
        # on a pixel boundary instead of straddling two; ring radius
        # 3 matches the chip's QSS ``border-radius: 3px`` so the ring
        # follows the chip's own corner curve cleanly.
        from PyQt6.QtCore import QRectF
        r = self.rect()
        ring = QRectF(
            r.x() + 0.5,
            r.y() + 0.5,
            r.width() - 1,
            r.height() - 1,
        )
        painter.drawRoundedRect(ring, 3, 3)
        painter.end()


class _NTChipLabel(QLabel):
    """QLabel for the NT "🚫 NT" pill that can paint a focus ring on
    top of its QSS background — counterpart to ``_ChipContainer`` for
    the term chips. Same rationale: parents paint before children, so
    drawing the ring on the NTBlock would be covered by the QLabel's
    own background fill.
    """

    def __init__(self, text="", parent=None):
        super().__init__(text, parent)
        self._is_current = False

    def set_current(self, current: bool):
        if self._is_current == current:
            return
        self._is_current = current
        self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        if not self._is_current:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor("#BF360C"))  # deep amber — solid contrast against the pastel-yellow NT pill
        # v1.10.94 — 1-px stroke to match _ChipContainer; see the
        # longer rationale comment there.
        pen.setWidth(1)
        painter.setPen(pen)
        from PyQt6.QtCore import QRectF
        r = self.rect()
        ring = QRectF(
            r.x() + 0.5,
            r.y() + 0.5,
            r.width() - 1,
            r.height() - 1,
        )
        # NT pill stylesheet uses border-radius: 2 px, so match.
        painter.drawRoundedRect(ring, 2, 2)
        painter.end()


class _CornerIndicators(QWidget):
    """v1.10.83 — overlay widget painted with metadata + synonym
    indicators at the top-right corner of a TermBlock's chip.

    **Why this is a separate widget rather than part of _ChipContainer:**

    Qt clips painting to widget bounds. The Trados TermLens visual
    has the indicators overflowing half-above the chip's top edge,
    sitting in the gap between the source word label and the chip
    itself. We can't replicate that by painting inside the chip
    (clipped) or by reserving space inside the chip (pushes chip
    content down, causing the chip-alignment problem the user
    flagged in v1.10.82).

    The solution is to make the indicators a separate small QWidget
    that's a child of the **outer TermBlock**, positioned
    absolutely at the chip's top-right corner. The indicator
    widget has its own bounds so its paint isn't clipped to the
    chip's; TermBlock sets its position via
    ``_position_corner_indicators()`` in its resizeEvent so the
    indicator follows the chip as the layout changes.

    Translucent background + ``WA_TransparentForMouseEvents`` so
    the user can still click through the indicator to the chip
    underneath (the chip's mouseReleaseEvent fires on click-to-
    insert).
    """

    def __init__(self, has_metadata: bool = False, has_synonyms: bool = False, parent=None):
        super().__init__(parent)
        self._has_metadata = bool(has_metadata)
        self._has_synonyms = bool(has_synonyms)
        # Fixed size: room for both indicators side-by-side, or just
        # one if the chip only has one type of metadata. Heights are
        # whatever the bigger icon needs.
        w = 0
        if has_metadata:
            w += 10
        if has_metadata and has_synonyms:
            w += 3  # gap between
        if has_synonyms:
            w += 9
        if w == 0:
            w = 1  # avoid 0-width
        self.setFixedSize(w, 10)
        # Transparent bg so only the painted circles are visible;
        # the rest of the rect shows whatever's behind (the source
        # label area of the TermBlock).
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        # Don't intercept mouse — click-through to chip below.
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)

        x = 0
        y = 0
        if self._has_synonyms:
            # Drawn LEFT of the metadata dot (so the order
            # left-to-right is: ≡ synonym, ℹ metadata, just like
            # the v1.10.82 version painted them at the chip corner).
            size = 9
            p.setBrush(QColor("#6366F1"))     # indigo
            p.setPen(QPen(QColor("white"), 1.0))
            p.drawEllipse(x, y, size, size)
            cx = x + size / 2.0
            cy = y + size / 2.0
            p.setPen(QPen(QColor("white"), 1.0))
            p.drawLine(int(cx - 2), int(cy - 2), int(cx + 2), int(cy - 2))
            p.drawLine(int(cx - 2), int(cy),     int(cx + 2), int(cy))
            p.drawLine(int(cx - 2), int(cy + 2), int(cx + 2), int(cy + 2))
            x += size + 3  # gap before metadata dot

        if self._has_metadata:
            size = 10
            p.setBrush(QColor("#F59E0B"))     # amber
            p.setPen(QPen(QColor("white"), 1.0))
            p.drawEllipse(x, y, size, size)
            # White bold "i" glyph centred in the circle (the
            # universal "info / more here" affordance).
            i_font = QFont("Segoe UI")
            i_font.setPixelSize(8)
            i_font.setBold(True)
            p.setFont(i_font)
            p.setPen(QPen(QColor("white"), 1.0))
            i_rect = QRect(x, y, size, size)
            p.drawText(i_rect, Qt.AlignmentFlag.AlignCenter, "i")


class TermPopup(QFrame):
    """v1.10.75 (Tier 3d) — sticky floating popup that replaces the
    Qt tooltip on TermBlock chips, giving a bigger, richer surface
    for the metadata lines.

    **Why a popup instead of the built-in QToolTip:**
     - Tooltips auto-hide when the mouse moves at all, even slightly.
       Users couldn't actually READ a long Definition / Notes block
       without keeping the mouse perfectly still.
     - Tooltips have a hard size cap; multi-line metadata gets
       clipped on dense entries.
     - Tooltips can't host clickable links.
     - The Trados TermLens uses the same pattern (its TermPopup
       singleton). Visual + behavioural parity matters across the
       two products.

    **Lifecycle (matches Trados TermPopup):**
     - **Show**: TermBlock.enterEvent on a chip with content calls
       ``show_for``. A 200 ms debounce timer prevents flicker when
       the user is just moving across chips quickly.
     - **Stay open while hovered**: enterEvent on the popup itself
       cancels the pending close, so the user can move the mouse
       from the chip into the popup to read / click links.
     - **Hide**: chip leaveEvent + popup leaveEvent both call
       ``schedule_close``, which starts a 250 ms grace timer.
       The user can re-enter the chip OR the popup within the
       grace period to keep it open.

    **One shared instance** across the whole application — only one
    popup ever visible at a time. ``get_instance()`` lazy-creates it
    on first hover.
    """

    _instance = None

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = TermPopup()
        return cls._instance

    def __init__(self):
        # ToolTip window flag = no taskbar entry, no focus stealing,
        # always-on-top relative to the parent app, no border.
        # WA_ShowWithoutActivating = stays compatible with the
        # currently-focused editor (don't yank focus away from the
        # source/target cell the user is typing in).
        super().__init__(None, Qt.WindowType.ToolTip | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setStyleSheet("""
            TermPopup {
                background-color: #FFFFFF;
                border: 1px solid #CCCCCC;
                border-radius: 6px;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(0)
        self._label = QLabel("")
        self._label.setTextFormat(Qt.TextFormat.RichText)
        self._label.setWordWrap(True)
        self._label.setOpenExternalLinks(True)
        self._label.setMaximumWidth(420)
        layout.addWidget(self._label)

        # Close grace timer — close 250 ms after leaveEvent unless
        # the mouse re-enters the chip / popup in the meantime.
        self._close_timer = QTimer(self)
        self._close_timer.setSingleShot(True)
        self._close_timer.setInterval(250)
        self._close_timer.timeout.connect(self.hide)

        self.hide()

    def show_for(self, anchor_widget, html_content: str):
        """Position and show the popup beneath ``anchor_widget``,
        rendering ``html_content`` (Qt rich-text subset).

        If positioning below would push the popup off the bottom of
        the screen, flips to showing above the anchor. If still too
        wide, clamps to the screen's right edge so the content
        stays on-screen.
        """
        self._close_timer.stop()
        self._label.setText(html_content or "")
        self.adjustSize()

        try:
            screen = QApplication.primaryScreen().availableGeometry()
        except Exception:
            self.show()
            return

        # Default: just below the anchor.
        anchor_pos = anchor_widget.mapToGlobal(QPoint(0, anchor_widget.height() + 4))
        x, y = anchor_pos.x(), anchor_pos.y()
        # Clamp right edge
        if x + self.width() > screen.right():
            x = screen.right() - self.width() - 4
        if x < screen.left():
            x = screen.left() + 4
        # Flip above if no room below
        if y + self.height() > screen.bottom():
            above_pos = anchor_widget.mapToGlobal(QPoint(0, -self.height() - 4))
            if above_pos.y() >= screen.top() + 4:
                y = above_pos.y()
            else:
                # Neither fits cleanly — clamp to top
                y = screen.top() + 4
        self.move(x, y)
        self.show()

    def schedule_close(self):
        """Start the close grace timer. Cancelled by ``enterEvent`` if
        the mouse re-enters the popup (or by ``show_for`` if another
        chip's hover re-opens the popup with new content).
        """
        self._close_timer.start()

    def enterEvent(self, event):
        """Mouse entered the popup itself — keep it open so the
        user can read multi-line content or click links."""
        self._close_timer.stop()
        super().enterEvent(event)

    def leaveEvent(self, event):
        """Mouse left the popup — schedule close. If the user
        re-enters within the grace window, ``enterEvent`` cancels."""
        self._close_timer.start()
        super().leaveEvent(event)


class LineBreakWidget(QWidget):
    """Zero-size sentinel widget that forces FlowLayout to start a new line"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(0, 0)
        self.hide()  # Invisible – only used as a layout hint


class FlowLayout(QLayout):
    """Flow layout that wraps widgets to next line when needed"""
    
    def __init__(self, parent=None, margin=0, spacing=-1):
        super().__init__(parent)
        self.itemList = []
        self.m_hSpace = spacing
        self.m_vSpace = spacing
        self.setContentsMargins(margin, margin, margin, margin)
    
    def __del__(self):
        item = self.takeAt(0)
        while item:
            item = self.takeAt(0)
    
    def addItem(self, item):
        self.itemList.append(item)
    
    def horizontalSpacing(self):
        if self.m_hSpace >= 0:
            return self.m_hSpace
        else:
            return self.smartSpacing(QStyle.PixelMetric.PM_LayoutHorizontalSpacing)
    
    def verticalSpacing(self):
        if self.m_vSpace >= 0:
            return self.m_vSpace
        else:
            return self.smartSpacing(QStyle.PixelMetric.PM_LayoutVerticalSpacing)
    
    def count(self):
        return len(self.itemList)
    
    def itemAt(self, index):
        if 0 <= index < len(self.itemList):
            return self.itemList[index]
        return None
    
    def takeAt(self, index):
        if 0 <= index < len(self.itemList):
            return self.itemList.pop(index)
        return None
    
    def expandingDirections(self):
        return Qt.Orientation(0)
    
    def hasHeightForWidth(self):
        return True
    
    def heightForWidth(self, width):
        height = self.doLayout(QRect(0, 0, width, 0), True)
        return height
    
    def setGeometry(self, rect):
        super().setGeometry(rect)
        self.doLayout(rect, False)
    
    def sizeHint(self):
        return self.minimumSize()
    
    def minimumSize(self):
        size = QSize()
        for item in self.itemList:
            size = size.expandedTo(item.minimumSize())
        margin = self.contentsMargins().left()
        size += QSize(2 * margin, 2 * margin)
        return size
    
    def doLayout(self, rect, testOnly):
        x = rect.x()
        y = rect.y()
        lineHeight = 0
        spacing = self.horizontalSpacing()
        if spacing < 0:
            spacing = 5  # Default spacing

        for item in self.itemList:
            wid = item.widget()

            # LineBreakWidget sentinel → force a new line
            if isinstance(wid, LineBreakWidget):
                if lineHeight > 0:
                    x = rect.x()
                    y = y + lineHeight + spacing
                    lineHeight = 0
                continue  # Don't place the sentinel itself

            spaceX = spacing
            spaceY = spacing

            nextX = x + item.sizeHint().width() + spaceX
            if nextX - spaceX > rect.right() and lineHeight > 0:
                x = rect.x()
                y = y + lineHeight + spaceY
                nextX = x + item.sizeHint().width() + spaceX
                lineHeight = 0

            if not testOnly:
                item.setGeometry(QRect(QPoint(x, y), item.sizeHint()))

            x = nextX
            lineHeight = max(lineHeight, item.sizeHint().height())

        return y + lineHeight - rect.y()
    
    def smartSpacing(self, pm):
        parent = self.parent()
        if not parent:
            return -1
        if parent.isWidgetType():
            return parent.style().pixelMetric(pm, None, parent)
        else:
            return parent.spacing()


class TermBlock(QWidget):
    """Individual term block showing source word and its translation(s)"""
    
    term_clicked = pyqtSignal(str, str)  # source_term, target_term
    edit_requested = pyqtSignal(int, int)  # term_id, termbase_id
    delete_requested = pyqtSignal(int, int, str, str)  # term_id, termbase_id, source_term, target_term
    
    def __init__(self, source_text: str, translations: List[Dict], parent=None, theme_manager=None, font_size: int = 10, font_family: str = "Segoe UI", font_bold: bool = False, shortcut_number: int = None):
        """
        Args:
            source_text: Source word/phrase
            translations: List of dicts with keys: 'target', 'termbase_name', 'ranking', 'term_id', 'termbase_id', etc.
            theme_manager: Optional theme manager for dark mode support
            font_size: Base font size in points (default 10)
            font_family: Font family name (default "Segoe UI")
            font_bold: Whether to use bold font (default False)
            shortcut_number: Optional number (1-9) for Ctrl+N shortcut badge
        """
        super().__init__(parent)
        self.source_text = source_text
        self.translations = translations
        self.theme_manager = theme_manager
        self.font_size = font_size
        self.font_family = font_family
        self.font_bold = font_bold
        self.shortcut_number = shortcut_number
        # Store first translation's IDs for context menu (if available)
        self.term_id = None
        self.termbase_id = None
        self.target_term = None
        if translations:
            first_trans = translations[0]
            self.term_id = first_trans.get('term_id')
            self.termbase_id = first_trans.get('termbase_id')
            self.target_term = first_trans.get('target_term', first_trans.get('target', ''))
        # v1.10.87 — "current chip" highlight for the TermLens popup's
        # keyboard navigation. False by default (chips don't show the
        # ring in the docked panel); set to True from the popup when
        # the user arrows onto this chip. Drawn in paintEvent.
        self._is_current = False
        self.init_ui()

    def set_current(self, current: bool):
        """Toggle the 'current selection' highlight ring around the chip.

        Used by the TermLensPopup keyboard cycle (Right/Left/Tab) to
        indicate which match Enter / E / I will act on. No-op in the
        docked TermLens panel where keyboard cycling isn't a thing.

        v1.10.92: the ring is now painted by ``_ChipContainer`` itself
        (so it draws on top of the chip's background — Qt paints
        children AFTER the parent's paintEvent, so anything drawn on
        TermBlock would be covered by the chip's QSS fill). TermBlock
        just forwards the state to its chip container.
        """
        if self._is_current == current:
            return
        self._is_current = current
        chip = getattr(self, 'target_container', None)
        if chip is not None and hasattr(chip, 'set_current'):
            chip.set_current(current)

    # --- A word without a term: painted, no child widgets -----------------
    # Sizes match the three widgets this replaced: a 1 px line, the word with
    # 3 px beside it and 1 px above and below, then the dot in an 8 px font;
    # 1 px of margin left, right and below.

    def set_plain_font(self, family: str, size: int, bold: bool):
        """Set the word's font and work out the block's size from it."""
        font = QFont(family)
        font.setPointSize(size)
        font.setBold(bold)
        self._plain_font = font
        dot_font = QFont(self.font())
        dot_font.setPixelSize(8)
        self._plain_dot_font = dot_font
        word = QFontMetrics(font)
        self._plain_word_height = word.height() + 2
        self._plain_dot_height = QFontMetrics(dot_font).height()
        width = max(word.horizontalAdvance(self.source_text) + 6,
                    QFontMetrics(dot_font).horizontalAdvance("·")) + 2
        self._plain_size = QSize(width, 1 + self._plain_word_height + self._plain_dot_height + 1)
        self.updateGeometry()
        self.update()

    def sizeHint(self):
        return self._plain_size if self._plain else super().sizeHint()

    def minimumSizeHint(self):
        return self._plain_size if self._plain else super().minimumSizeHint()

    def paintEvent(self, event):
        if not self._plain:
            return super().paintEvent(event)
        line, word, dot = self._plain_colors
        painter = QPainter(self)
        inner = self.rect().adjusted(1, 0, -1, -1)
        painter.fillRect(QRect(inner.left(), 0, inner.width(), 1), line)
        painter.setFont(self._plain_font)
        painter.setPen(word)
        painter.drawText(QRect(inner.left(), 1, inner.width(), self._plain_word_height),
                         Qt.AlignmentFlag.AlignCenter, self.source_text)
        painter.setFont(self._plain_dot_font)
        painter.setPen(dot)
        painter.drawText(QRect(inner.left(), 1 + self._plain_word_height, inner.width(), self._plain_dot_height),
                         Qt.AlignmentFlag.AlignCenter, "·")
        painter.end()

    def init_ui(self):
        """Create the visual layout for this term block - COMPACT RYS-style"""
        # A word without a term is most of a segment, and it is only a line,
        # the word and a dot. Building that from three widgets, each with a
        # stylesheet of its own, was most of the cost of a TermLens redraw
        # (Nov 2026: ~2 ms a word, on every move, in both panels). Such a
        # block is now painted and has no child widgets. See paintEvent.
        self.source_label = None
        self._plain = not self.translations
        if self._plain:
            is_dark = self.theme_manager and self.theme_manager.current_theme.name == "Dark"
            self.bg_color = "#2A2A2A" if is_dark else "#F5F5F5"
            self.is_effective_project = False
            self._plain_colors = (
                QColor("#555555" if is_dark else "#CCCCCC"),   # line
                QColor("#FFFFFF" if is_dark else "#333333"),   # word
                QColor("#666666" if is_dark else "#dddddd"),   # dot
            )
            self.set_plain_font(self.font_family, self.font_size, self.font_bold)
            return

        layout = QVBoxLayout(self)
        layout.setContentsMargins(1, 0, 1, 1)
        layout.setSpacing(0)
        
        # Get theme colors
        is_dark = self.theme_manager and self.theme_manager.current_theme.name == "Dark"
        separator_color = "#555555" if is_dark else "#CCCCCC"
        source_text_color = "#FFFFFF" if is_dark else "#333"
        no_match_color = "#666666" if is_dark else "#ddd"
        no_match_bg = "#2A2A2A" if is_dark else "#F5F5F5"
        
        # Add thin gray separator line at top (like RYS)
        separator = QFrame()
        separator.setFrameShape(QFrame.Shape.HLine)
        separator.setFixedHeight(1)
        separator.setStyleSheet(f"background-color: {separator_color}; border: none;")
        layout.addWidget(separator)
        
        # Determine border color based on whether we have translations
        if self.translations:
            primary_translation = self.translations[0]
            is_project = primary_translation.get('is_project_termbase', False)
            ranking = primary_translation.get('ranking', None)
            
            # IMPORTANT: Treat ranking #1 as project termbase (matches main app logic)
            is_effective_project = is_project or (ranking == 1)
            
            # Background color: pink for project termbase, blue for regular termbase
            self.bg_color = "#FFE5F0" if is_effective_project else "#D6EBFF"
            self.is_effective_project = is_effective_project
        else:
            self.bg_color = no_match_bg  # Theme-aware for no matches
            self.is_effective_project = False
        
        # Source text (top) - compact
        self.source_label = QLabel(self.source_text)
        source_font = QFont(self.font_family)
        source_font.setPointSize(self.font_size)
        source_font.setBold(self.font_bold)
        self.source_label.setFont(source_font)
        self.source_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.source_label.setStyleSheet(f"""
            QLabel {{
                color: {source_text_color};
                padding: 1px 3px;
                background-color: transparent;
                border: none;
            }}
        """)
        # Enable context menu on source label for edit/delete actions (only if we have translations with IDs)
        if self.translations and self.term_id is not None:
            self.source_label.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            self.source_label.customContextMenuRequested.connect(self._show_context_menu)
        layout.addWidget(self.source_label)
        
        # Target translation (bottom) - show first/best match - COMPACT
        if self.translations:
            target_text = primary_translation.get('target_term', primary_translation.get('target', ''))
            termbase_name = primary_translation.get('termbase_name', '')

            # v1.10.75 (Tier 3a + 3c): chip background colour is now a
            # five-way precedence ladder matching Trados TermBlock:
            #   1. Forbidden term            → red bg, white text, strikethrough
            #   2. Non-translatable          → amber bg, dark text (copy-through cue)
            #   3. Abbreviation match         → purple bg ("this chip is the GC → GC pair")
            #   4. Project termbase           → pink bg, blue text
            #   5. Regular termbase           → blue bg, blue text
            # Highest-precedence flag wins; e.g. a forbidden NT shows as
            # forbidden (red) because users absolutely need to know not
            # to use it. Abbreviation match wins over project/regular
            # because the purple chip explains why the chip text is
            # "GC" instead of the full term.
            self.is_forbidden = bool(primary_translation.get('forbidden', False))
            self.is_nontranslatable = bool(primary_translation.get('is_nontranslatable', False))
            self.is_abbreviation_match = bool(primary_translation.get('matched_via_abbreviation', False))

            # Background color based on flags + termbase type (theme-aware).
            is_dark = self.theme_manager and self.theme_manager.current_theme.name == "Dark"
            if self.is_forbidden:
                # Red, same in both themes — forbidden terms must read
                # the same way regardless of theme.
                bg_color = "#E53935"
                hover_color = "#C62828"
            elif self.is_nontranslatable:
                if is_dark:
                    bg_color = "#5A4A1F"   # darker amber for dark mode
                    hover_color = "#6A5A2F"
                else:
                    bg_color = "#FFF3D0"   # light amber matching Trados
                    hover_color = "#FFE8A0"
            elif self.is_abbreviation_match:
                if is_dark:
                    bg_color = "#3E2D5A"   # darker purple for dark mode
                    hover_color = "#4E3D6A"
                else:
                    bg_color = "#E8DAFF"   # light purple matching Trados
                    hover_color = "#D8C8FF"
            elif is_dark:
                # Dark mode pink/blue
                bg_color = "#4A2D3A" if self.is_effective_project else "#2D3E4A"
                hover_color = "#5A3D4A" if self.is_effective_project else "#3D4E5A"
            else:
                # Light mode pink/blue
                bg_color = "#FFE5F0" if self.is_effective_project else "#D6EBFF"
                hover_color = "#FFD0E8" if self.is_effective_project else "#BBDEFB"
            
            # v1.10.75 (Tier 3b): figure out which corner indicators
            # this chip needs BEFORE constructing the container, so
            # the container can paint them inline on top of its
            # stylesheet background. We inspect EVERY entry (not just
            # the primary) so e.g. a chip showing target_term="cable"
            # also lights up the synonym indicator if any of the
            # underlying termbase entries for "kabel" have synonyms,
            # matching Trados TermBlock's _entries.Any(t => …) logic.
            #
            # Metadata = any of {definition, domain, notes, url}.
            # Synonyms = source or target synonyms.
            has_metadata = any(
                bool((t or {}).get('definition')) or
                bool((t or {}).get('domain')) or
                bool((t or {}).get('notes')) or
                bool((t or {}).get('url'))
                for t in self.translations
            )
            # v1.10.85 fix: only flag actual synonyms. target_synonyms
            # are inlined into self.translations with ``is_synonym=True``
            # by build_matches_dict, so we check for that flag in addition
            # to explicit source/target synonym lists. The previous
            # ``or len(self.translations) > 1`` fallback was wrong because
            # alternative translations from *different* termbases (e.g.
            # BRANTS "inrichting → device" + PATENTS "apparatus → inrichting")
            # also produce ``len(translations) > 1`` without any of those
            # entries being synonyms — the +N badge already signals
            # "there are more options here", so the ≡ icon was redundant
            # in that case and misleading when no real synonyms existed.
            # Reported by a user: "inrichting = device has a synonym
            # indicator. However, when I right click on the term and look
            # at it and both of its different term base entries, none of
            # them have a synonym."
            has_synonyms = any(
                bool((t or {}).get('source_synonyms')) or
                bool((t or {}).get('target_synonyms')) or
                bool((t or {}).get('is_synonym'))
                for t in self.translations
            )

            # Create container for target + shortcut badge with the
            # coloured background covering both text and badge.
            # _ChipContainer (custom QWidget subclass) paints the
            # stylesheet background + the corner indicators on top.
            # v1.10.83: _ChipContainer no longer holds the indicator
            # flags — the indicators live in a sibling overlay widget
            # (_CornerIndicators) created below, positioned at the
            # chip's top-right corner so they can overflow above the
            # chip's top edge (Trados-style) without requiring a
            # top margin on the chip itself.
            target_container = _ChipContainer()
            # v1.10.75 (Tier 3d): keep the chip reference on the
            # TermBlock so enterEvent below can anchor the floating
            # TermPopup beneath the chip itself (not under the source
            # label, which is the OUTER widget's top-left).
            self.target_container = target_container
            target_container.setStyleSheet(f"""
                _ChipContainer {{
                    background-color: {bg_color};
                    border-radius: 3px;
                }}
                _ChipContainer:hover {{
                    background-color: {hover_color};
                }}
            """)
            target_layout = QHBoxLayout(target_container)
            # v1.10.83: top margin is back to 1 px unconditionally.
            # In v1.10.80–v1.10.82 we conditionally bumped this to 12
            # so painted indicators (inside the chip's top headroom)
            # didn't get covered by the shortcut badge, but that made
            # chips with indicators sit ~12 px lower than their
            # neighbours — visibly out of alignment in a TermLens
            # row. The v1.10.83 redesign moves the indicators to a
            # sibling overlay widget that overflows above the chip,
            # so the chip itself can stay at the compact 1-px margin
            # and every chip in the row aligns to the same baseline.
            target_layout.setContentsMargins(3, 1, 3, 1)
            target_layout.setSpacing(3)
            
            target_label = QLabel(target_text)
            target_font = QFont(self.font_family)
            target_font.setPointSize(self.font_size)  # Same size as source
            target_font.setBold(self.font_bold)
            # v1.10.75 (Tier 3a): forbidden terms get a strikethrough
            # font effect so they're unmistakable at a glance — same
            # convention as Trados TermBlock (and as memoQ and Trados
            # MultiTerm display forbidden terms).
            if self.is_forbidden:
                target_font.setStrikeOut(True)
            target_label.setFont(target_font)
            target_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            # v1.10.75 (Tier 3a + 3c): text colour follows chip background:
            #  - Forbidden (red bg)          → white text for max contrast
            #  - Non-translatable (amber)     → dark text (matches Trados)
            #  - Abbreviation (purple)        → dark purple text
            #  - Regular (pink/blue)          → blue text (existing behaviour)
            if self.is_forbidden:
                target_text_color = "#FFFFFF"
            elif self.is_nontranslatable:
                # Dark text on amber — reads cleanly in both themes.
                target_text_color = "#3E2A0F" if is_dark else "#5C3F12"
            elif self.is_abbreviation_match:
                # Indigo/purple text on light purple bg, light purple
                # text on dark purple bg. Conveys "this chip is an
                # abbreviation pair" via colour resonance.
                target_text_color = "#C8B8FF" if is_dark else "#4A2D8A"
            else:
                target_text_color = "#B0C4DE" if is_dark else "#0052A3"
            target_label.setStyleSheet(f"""
                QLabel {{
                    color: {target_text_color};
                    padding: 0px;
                    background-color: transparent;
                    border: none;
                }}
            """)
            target_label.setCursor(Qt.CursorShape.PointingHandCursor)
            target_label.mousePressEvent = lambda e: self.on_translation_clicked(target_text)
            
            # Build tooltip with shortcut hint if applicable
            if self.shortcut_number is not None and self.shortcut_number <= 19:
                # Alt+0 (and Alt+0,0) are reserved for the Compare Panel.
                # Do not advertise or display these shortcuts in TermLens.
                if self.shortcut_number in (0, 10):
                    shortcut_hint = ""
                elif self.shortcut_number <= 9:
                    shortcut_hint = (
                        f"<br><i>Press {format_shortcut_for_display(f'Alt+{self.shortcut_number}')} to insert</i>"
                    )
                else:
                    # Double-tap shortcuts (10-19 displayed as 00, 11, 22, etc.)
                    double_digit = (self.shortcut_number - 10)
                    shortcut_hint = (
                        f"<br><i>Press {format_shortcut_for_display(f'Alt+{double_digit},{double_digit}')} to insert</i>"
                    )
            else:
                shortcut_hint = ""
            
            # Helper: extra metadata block (definition, abbreviations, URL,
            # domain, synonyms). Renders in HTML for the QToolTip — the
            # tooltip auto-renders Qt's rich-text subset so <i>, <b>, <br>
            # all work without an explicit format hint.
            #
            # v1.10.73 (Tier 1 + Tier 2): added abbreviation/definition/
            # domain/URL rows (data was always shaped this way; the index
            # builder now actually fills them in via the v1.10.73 SELECT
            # additions) plus a source-synonyms "Also: …" row matching
            # the Trados TermBlock popup format. Target synonyms aren't
            # shown here because update_with_matches splits each one off
            # into its own additional-translation chip — they appear in
            # the "Alternatives:" list further down the tooltip rather
            # than as a metadata line.
            # v1.10.77 — popup HTML now rendered per-entry, matching
            # the Trados TermBlock.BuildMetadataLines layout:
            #
            #   source → target [TermbaseName] (ID 12345)
            #     Notes:
            #       <multi-line notes, line breaks preserved>
            #     Def: <definition>
            #     Domain: <domain>
            #     URL: <clickable link>
            #     Also: <source synonyms comma-separated>
            #   ──────────────────────────────────────────
            #   source → target [TermbaseName2] (ID 67890)
            #     <metadata for second entry>
            #
            # Each entry from each termbase gets its own heading +
            # metadata block, separated by a thin horizontal rule.
            # That makes it obvious WHICH termbase a translation
            # came from when the same source term has entries in
            # multiple termbases (very common for users with both a
            # project termbase and a domain termbase).
            #
            # Previously the popup showed only the primary entry's
            # metadata in detail and collapsed alternatives to a
            # numbered list — the alternatives' notes / definitions /
            # URLs were dropped entirely. Reported by a user
            # comparing the Workbench TermLens to the Trados TermLens
            # side by side. This rewrite closes the gap.
            from html import escape as _html_escape

            popup_chunks = []
            for entry_idx, entry in enumerate(self.translations):
                if entry_idx > 0:
                    # Thin separator between entries (Trados uses a
                    # PopupLineType.Separator; HTML <hr> is the same
                    # affordance).
                    popup_chunks.append(
                        '<hr style="border:none;border-top:1px solid #DDDDDD;'
                        'margin:6px 0;">'
                    )

                # Heading: source → target [TermbaseName] (ID N).
                # source_text is the chip's display text (from the
                # tokenizer) — same word for every entry of the same
                # source key, so it's fine to use the outer variable.
                src_h = _html_escape(self.source_text or '')
                tgt_h = _html_escape(entry.get('target_term', entry.get('target', '')) or '')
                tb_h = _html_escape(entry.get('termbase_name', '') or '')
                tid = entry.get('term_id')

                heading = f"<b>{src_h} → {tgt_h}</b>"
                if tb_h:
                    heading += f" <span style='color:#666;'>[{tb_h}]</span>"
                if tid is not None:
                    heading += f" <span style='color:#999;'>(ID {tid})</span>"
                popup_chunks.append(heading)

                # v1.10.309: when the chip matched a surface form that differs
                # from the underlying term — most importantly an *abbreviation*
                # ("ASR"), but also a source synonym — show the full term so the
                # translator can decode what the abbreviation stands for.
                full_s = (entry.get('full_source_term', '') or '').strip()
                full_t = (entry.get('full_target_term', '') or '').strip()
                disp_s = (self.source_text or '').strip()
                if full_s and full_s.lower() != disp_s.lower():
                    popup_chunks.append(
                        f"<div style='margin-top:2px;'><b>Full term:</b> "
                        f"{_html_escape(full_s)} &rarr; {_html_escape(full_t)}</div>"
                    )

                # Shortcut hint only on the primary entry (the chip's
                # Alt+digit binding inserts the primary's target,
                # not the alternatives').
                if entry_idx == 0 and shortcut_hint:
                    popup_chunks.append(shortcut_hint)

                # Notes — render with "Notes:" bold label + line-break
                # preservation. Notes are user-authored prose; HTML-
                # escape to prevent stray < / > in user text from
                # breaking the popup layout.
                notes = entry.get('notes', '') or ''
                if notes:
                    notes_html = _html_escape(notes).replace('\n', '<br>')
                    popup_chunks.append(
                        f"<div style='margin-top:4px;'>"
                        f"<b>Notes:</b><br>{notes_html}</div>"
                    )

                # Definition — same treatment, separate label.
                definition = entry.get('definition', '') or ''
                if definition:
                    def_html = _html_escape(definition).replace('\n', '<br>')
                    popup_chunks.append(
                        f"<div style='margin-top:4px;'>"
                        f"<b>Def:</b> {def_html}</div>"
                    )

                # Domain — single line.
                domain = entry.get('domain', '') or ''
                if domain:
                    popup_chunks.append(
                        f"<div style='margin-top:2px;'>"
                        f"<b>Domain:</b> {_html_escape(domain)}</div>"
                    )

                # URL — clickable link (popup already enables
                # openExternalLinks). Display as the URL itself
                # rather than truncating, since translators often
                # want to verify the source.
                url = entry.get('url', '') or ''
                if url:
                    url_safe = _html_escape(url)
                    popup_chunks.append(
                        f"<div style='margin-top:2px;'>"
                        f"<b>URL:</b> <a href='{url_safe}'>{url_safe}</a></div>"
                    )

                # Abbreviations.
                src_abbr = entry.get('source_abbreviation', '') or ''
                tgt_abbr = entry.get('target_abbreviation', '') or ''
                if src_abbr or tgt_abbr:
                    pair = " / ".join(_html_escape(x) for x in (src_abbr, tgt_abbr) if x)
                    popup_chunks.append(
                        f"<div style='margin-top:2px;'>"
                        f"<i>Abbr: {pair}</i></div>"
                    )

                # Source synonyms.
                src_syns = entry.get('source_synonyms', []) or []
                if src_syns:
                    safe = ", ".join(_html_escape(s or '') for s in src_syns if s)
                    if safe:
                        popup_chunks.append(
                            f"<div style='margin-top:2px;'>"
                            f"<i>Also: {safe}</i></div>"
                        )

                # v1.10.309: target-language synonyms. For a normal chip these
                # are also split into separate alternative chips, but for an
                # abbreviation chip they aren't — and the user wants to see the
                # target variants in the tooltip either way (e.g. the Dutch
                # synonyms for "auto shredder residu").
                def _syn_text(s):
                    if isinstance(s, dict):
                        return s.get('text') or s.get('target') or s.get('term') or ''
                    return s or ''
                tgt_syns = [t for t in (_syn_text(s) for s in (entry.get('target_synonyms') or [])) if t]
                if tgt_syns:
                    safe = ", ".join(_html_escape(s) for s in tgt_syns)
                    popup_chunks.append(
                        f"<div style='margin-top:2px;'>"
                        f"<i>Target also: {safe}</i></div>"
                    )

            popup_chunks.append(
                "<div style='margin-top:6px;color:#888;font-style:italic;'>"
                "(click chip to insert primary translation)</div>"
            )

            self._popup_html = "".join(popup_chunks)
            
            target_layout.addWidget(target_label)

            # v1.10.77 — "+N" indicator now rendered INLINE on the chip
            # itself (immediately right of the target text, before any
            # shortcut badge), matching Trados TermBlock. Previously
            # the "+1" / "+2" count was a tiny gray label BELOW the
            # chip in 7-pt font, which was easy to miss — users
            # comparing the Workbench TermLens with the Trados one
            # noted the +N was much less prominent. Putting it on
            # the chip surface keeps it tight to the translation it
            # qualifies + makes it large enough to register at
            # normal reading distance.
            # v1.10.86 — +N counts ONLY additional cross-entry termbase
            # matches, not target synonyms inlined from the primary
            # entry. The earlier semantics (count everything in the
            # translation stack including is_synonym=True entries)
            # diverged from the Trados TermLens, where "complete with"
            # has 2 target synonyms but renders without a +N badge
            # because there's only one underlying termbase entry. The
            # ≡ corner indicator already signals "synonyms available
            # in the popup" — the +N is reserved for "another termbase
            # has its own competing translation for this source word".
            non_synonym_extras = sum(
                1 for t in self.translations[1:]
                if not (t or {}).get('is_synonym', False)
            )
            if non_synonym_extras > 0:
                plus_label = QLabel(f"+{non_synonym_extras}")
                plus_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
                # Subtle but legible — slightly darker than the
                # background, smaller than the target text, no
                # decoration. The shortcut badge that follows it
                # has the strong visual weight.
                plus_color = "#7A8B9F" if is_dark else "#555555"
                plus_label.setStyleSheet(f"""
                    QLabel {{
                        color: {plus_color};
                        font-size: 9px;
                        font-weight: 600;
                        padding: 0px 2px;
                        background-color: transparent;
                    }}
                """)
                plus_label.setToolTip(
                    f"{non_synonym_extras} more termbase entr"
                    f"{'ies' if non_synonym_extras != 1 else 'y'} "
                    f"(hover the chip for details)"
                )
                target_layout.addWidget(plus_label)

            # Add shortcut number badge if assigned (0-9 for first 10, 00/11/22/.../99 for 11-20)
            if self.shortcut_number is not None and self.shortcut_number < 20:
                # Alt+0 (and Alt+0,0) are reserved for the Compare Panel.
                # Hide the corresponding TermLens badges (0 and 00).
                if self.shortcut_number in (0, 10):
                    layout.addWidget(target_container)
                    return

                # Badge text: 0-9 for first 10 terms, 00/11/22/.../99 for terms 11-20
                if self.shortcut_number < 10:
                    badge_text = str(self.shortcut_number)
                    shortcut_hint = format_shortcut_for_display(f"Alt+{self.shortcut_number}")
                    badge_width = 14
                else:
                    # Terms 11-20: show as 00, 11, 22, ..., 99
                    digit = self.shortcut_number - 10
                    badge_text = str(digit) * 2  # "00", "11", "22", etc.
                    shortcut_hint = format_shortcut_for_display(f"Alt+{digit},{digit}")
                    badge_width = 20  # Wider for 2 digits
                
                badge_label = QLabel(badge_text)
                badge_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
                badge_label.setFixedSize(badge_width, 14)
                # Theme-aware badge colors
                badge_bg = "#4A90E2" if is_dark else "#1976D2"  # Lighter blue in dark mode
                badge_text_color = "#FFFFFF" if is_dark else "white"
                badge_label.setStyleSheet(f"""
                    QLabel {{
                        background-color: {badge_bg};
                        color: {badge_text_color};
                        font-size: 9px;
                        font-weight: bold;
                        border-radius: 7px;
                        padding: 0px;
                    }}
                """)
                badge_label.setToolTip(f"Press {shortcut_hint} to insert")
                badge_label.setCursor(Qt.CursorShape.PointingHandCursor)
                badge_label.mousePressEvent = lambda e: self.on_translation_clicked(target_text)
                target_layout.addWidget(badge_label)
            
            # v1.10.84 fix: small fixed gap between the source word
            # label and the chip. The v1.10.83 corner indicators
            # overlay overflows ~5 px above the chip's top edge;
            # without this gap the overflow lands directly on the
            # bottom of the source-word text above. User report:
            # "the dots are painted over the text a little bit. If
            # we could move the entire line down just a tad, that
            # would no longer be a problem." A 4 px spacer adds
            # exactly that "just a tad" of clearance.
            #
            # Added unconditionally (not just when indicators are
            # present) because mixing spaced and unspaced chips
            # within the same TermLens row would break the chip
            # baseline alignment the v1.10.83 work just fixed.
            layout.addSpacing(4)
            layout.addWidget(target_container)
            # v1.10.77 — count_label removed from here; the "+N"
            # indicator is rendered INLINE on the chip itself (added
            # to target_layout above) so it stays tight to the
            # translation it qualifies, matching Trados TermBlock.

            # v1.10.83 — corner-indicators overlay widget. Created as
            # a child of `self` (the TermBlock), positioned absolutely
            # at the chip's top-right corner via
            # ``_position_corner_indicators`` (called from resizeEvent
            # + showEvent). The overlay overflows above the chip's
            # top edge so the indicators sit half-inside half-outside
            # the chip's corner — same visual as Trados, and crucially
            # the chip itself stays at its compact 1-px-margin height
            # so all chips in a TermLens row align to the same
            # baseline regardless of whether they have indicators.
            self._corner_indicators = None
            if has_metadata or has_synonyms:
                self._corner_indicators = _CornerIndicators(
                    has_metadata=has_metadata,
                    has_synonyms=has_synonyms,
                    parent=self,
                )
                # raise_() ensures the overlay paints ABOVE its
                # siblings in z-order (specifically above
                # target_container) when their bounds overlap.
                self._corner_indicators.raise_()
        else:
            # No translation found - very subtle (theme-aware)
            is_dark = self.theme_manager and self.theme_manager.current_theme.name == "Dark"
            no_match_dot_color = "#666666" if is_dark else "#ddd"
            no_match_label = QLabel("·")
            no_match_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            no_match_label.setStyleSheet(f"color: {no_match_dot_color}; font-size: 8px;")
            layout.addWidget(no_match_label)
    
    # v1.10.83 — corner-indicators overlay positioning.
    # ``_CornerIndicators`` is a small sibling QWidget anchored at the
    # chip's top-right corner so it can overflow above the chip's top
    # edge (matching the Trados TermLens visual). Qt only finalises
    # the chip's geometry after layout, so positioning happens in
    # resizeEvent + showEvent rather than in init_ui.
    def _position_corner_indicators(self):
        ci = getattr(self, '_corner_indicators', None)
        chip = getattr(self, 'target_container', None)
        if ci is None or chip is None:
            return
        # Top-right of the chip, with the overlay's right edge at the
        # chip's right edge (so the rightmost indicator — the amber
        # ℹ dot — sits flush with the chip's right edge) and the
        # overlay's vertical centre at the chip's TOP edge. Net
        # effect: the indicator straddles the chip's top, half above
        # half inside, matching Trados.
        x = chip.x() + chip.width() - ci.width()
        y = chip.y() - ci.height() // 2
        # Clamp y >= 0 so the overlay never gets clipped against the
        # TermBlock's top edge on chips that happen to be near it.
        if y < 0:
            y = 0
        ci.move(x, y)
        ci.raise_()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._position_corner_indicators()

    def showEvent(self, event):
        super().showEvent(event)
        # showEvent fires after the first layout pass so chip
        # geometry is final. Without this, the indicator would
        # sit at (0,0) on first paint until a subsequent resize.
        self._position_corner_indicators()

    # v1.10.75 (Tier 3d): TermBlock hover handlers drive the floating
    # TermPopup. The popup is a singleton — at most one ever visible
    # across the app — so moving from chip to chip just re-anchors
    # the same popup with new HTML content.
    def enterEvent(self, event):
        super().enterEvent(event)
        # Only show the popup for chips with actual translations.
        # No-translation chips (the "·" dot) have no metadata worth
        # showing, and we don't want a popup over plain unmatched
        # source words.
        if self.translations and getattr(self, '_popup_html', '') and getattr(self, 'target_container', None) is not None:
            try:
                TermPopup.get_instance().show_for(self.target_container, self._popup_html)
            except Exception:
                # Popup is non-critical — if anything goes wrong (rare),
                # just swallow rather than tear down the segment view.
                pass

    def leaveEvent(self, event):
        super().leaveEvent(event)
        # Grace timer in the popup itself handles the "user moved
        # into the popup" case; just kick off the close-pending state.
        try:
            TermPopup.get_instance().schedule_close()
        except Exception:
            pass

    def on_translation_clicked(self, target_text: str):
        """Handle click on translation to insert into target"""
        self.term_clicked.emit(self.source_text, target_text)
    
    def _show_context_menu(self, pos: QPoint):
        """Show context menu with Edit/Delete options for glossary entry"""
        if not self.term_id or not self.termbase_id:
            return
        
        menu = QMenu(self)
        
        # Edit entry action
        edit_action = QAction("✏️ Edit Termbase Entry", menu)
        edit_action.triggered.connect(self._edit_entry)
        menu.addAction(edit_action)
        
        # Delete entry action
        delete_action = QAction("🗑️ Delete Termbase Entry", menu)
        delete_action.triggered.connect(self._delete_entry)
        menu.addAction(delete_action)
        
        menu.exec(self.source_label.mapToGlobal(pos))
    
    def _edit_entry(self):
        """Emit signal to edit glossary entry"""
        if self.term_id and self.termbase_id:
            self.edit_requested.emit(self.term_id, self.termbase_id)
    
    def _delete_entry(self):
        """Emit signal to delete glossary entry"""
        if self.term_id and self.termbase_id:
            self.delete_requested.emit(self.term_id, self.termbase_id, self.source_text, self.target_term or '')


class NTBlock(QWidget):
    """Non-translatable block showing source word with pastel yellow styling.

    Since NTs are now backed by termbase entries flagged
    is_nontranslatable=1, the block carries term_id and termbase_id when
    they are known and emits edit_requested / delete_requested signals
    on right-click – same pattern as TermBlock. Entries without an id
    (e.g. legacy paths that don't propagate them) keep the click-to-
    insert-only behaviour.
    """

    nt_clicked = pyqtSignal(str)  # Emits NT text to insert as-is
    edit_requested = pyqtSignal(int, int)  # term_id, termbase_id
    delete_requested = pyqtSignal(int, int, str, str)  # term_id, termbase_id, source_term, target_term

    def __init__(self, source_text: str, list_name: str = "", parent=None, theme_manager=None, font_size: int = 10, font_family: str = "Segoe UI", font_bold: bool = False, term_id: Optional[int] = None, termbase_id: Optional[int] = None):
        """
        Args:
            source_text: Non-translatable word/phrase
            list_name: Name of the termbase the NT entry lives on
            theme_manager: Optional theme manager for dark mode support
            font_size: Base font size in points (default 10)
            font_family: Font family name (default "Segoe UI")
            font_bold: Whether to use bold font (default False)
            term_id: Termbase entry ID – enables right-click edit/delete
            termbase_id: Termbase ID containing the entry
        """
        super().__init__(parent)
        self.source_text = source_text
        self.list_name = list_name
        self.theme_manager = theme_manager
        self.font_size = font_size
        self.font_family = font_family
        self.font_bold = font_bold
        self.term_id = term_id
        self.termbase_id = termbase_id
        # v1.10.87 — current-chip highlight ring (TermLensPopup keyboard cycle).
        self._is_current = False
        self.nt_label = None
        self.init_ui()

    def set_current(self, current: bool):
        """Toggle the 'current selection' highlight ring (TermLensPopup nav).

        v1.10.92: ring is now painted by ``_NTChipLabel`` itself so it
        draws on top of the pill's background; see the longer comment
        on ``TermBlock.set_current`` for the rationale.
        """
        if self._is_current == current:
            return
        self._is_current = current
        if self.nt_label is not None and hasattr(self.nt_label, 'set_current'):
            self.nt_label.set_current(current)

    def init_ui(self):
        """Create the visual layout for this NT block - pastel yellow styling"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(1, 1, 1, 1)
        layout.setSpacing(0)
        
        # Get theme colors
        is_dark = self.theme_manager and self.theme_manager.current_theme.name == "Dark"
        source_text_color = "#FFFFFF" if is_dark else "#5D4E37"
        
        # Pastel yellow border for non-translatables
        border_color = "#E6C200"  # Darker yellow for border
        
        self.setStyleSheet(f"""
            QWidget {{
                border-top: 2px solid {border_color};
                border-radius: 0px;
            }}
        """)
        
        # Source text (top)
        self.source_label = QLabel(self.source_text)
        source_font = QFont(self.font_family)
        source_font.setPointSize(self.font_size)
        source_font.setBold(self.font_bold)
        self.source_label.setFont(source_font)
        self.source_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.source_label.setStyleSheet(f"""
            QLabel {{
                color: {source_text_color};
                padding: 1px 3px;
                background-color: transparent;
            }}
        """)
        layout.addWidget(self.source_label)
        
        # "Do not translate" indicator with pastel yellow background.
        # v1.10.92: use _NTChipLabel (custom QLabel subclass) so the
        # focus ring can be painted on top of the QSS background when
        # the popup keyboard cycle lands on this pill.
        nt_label = _NTChipLabel("🚫 NT")
        nt_font = QFont()
        nt_font.setPointSize(7)
        nt_label.setFont(nt_font)
        nt_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        nt_label.setStyleSheet("""
            QLabel {
                color: #5D4E37;
                padding: 1px 3px;
                background-color: #FFFDD0;
                border-radius: 2px;
            }
            QLabel:hover {
                background-color: #FFF9B0;
                cursor: pointer;
            }
        """)
        nt_label.setCursor(Qt.CursorShape.PointingHandCursor)
        nt_label.mousePressEvent = lambda e: self.on_nt_clicked()
        # v1.10.87 — stored as attribute so paintEvent's focus ring
        # has a chip rect to draw around when set_current(True) is called.
        self.nt_label = nt_label

        tooltip_extra = ""
        if self.term_id and self.termbase_id:
            tooltip_extra = "<br>(right-click to edit / delete)"
        tooltip = f"<b>🚫 Non-Translatable</b><br>{self.source_text}<br><br>From: {self.list_name}<br>(click to insert as-is){tooltip_extra}"
        nt_label.setToolTip(tooltip)

        layout.addWidget(nt_label)

        # Right-click context menu – only enabled when we know which
        # termbase entry to act on. The matching path through
        # find_nt_matches_in_source populates term_id and termbase_id
        # for every entry it returns, so this should always be live.
        if self.term_id and self.termbase_id:
            self.source_label.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            self.source_label.customContextMenuRequested.connect(self._show_context_menu)
            nt_label.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            nt_label.customContextMenuRequested.connect(
                lambda pos, src=nt_label: self._show_context_menu_from(src, pos)
            )

    def on_nt_clicked(self):
        """Handle click on NT to insert source text as-is"""
        self.nt_clicked.emit(self.source_text)

    def _show_context_menu(self, pos: QPoint):
        """Show edit/delete context menu on the source label."""
        self._show_context_menu_from(self.source_label, pos)

    def _show_context_menu_from(self, src_widget, pos: QPoint):
        """Show edit/delete context menu anchored to ``src_widget``."""
        if not self.term_id or not self.termbase_id:
            return
        menu = QMenu(self)

        edit_action = QAction("✏️ Edit Non-Translatable", menu)
        edit_action.triggered.connect(self._edit_entry)
        menu.addAction(edit_action)

        delete_action = QAction("🗑️ Delete Non-Translatable", menu)
        delete_action.triggered.connect(self._delete_entry)
        menu.addAction(delete_action)

        menu.exec(src_widget.mapToGlobal(pos))

    def _edit_entry(self):
        """Emit signal so the host widget can open the term editor."""
        if self.term_id and self.termbase_id:
            self.edit_requested.emit(self.term_id, self.termbase_id)

    def _delete_entry(self):
        """Emit signal so the host widget can run the delete confirmation."""
        if self.term_id and self.termbase_id:
            # Source == target for NT entries by convention; pass both.
            self.delete_requested.emit(
                self.term_id, self.termbase_id,
                self.source_text, self.source_text,
            )


class TermLensWidget(QWidget):
    """Main TermLens widget showing inline terminology for current segment"""
    
    term_insert_requested = pyqtSignal(str)  # Emits target text to insert
    edit_entry_requested = pyqtSignal(int, int)  # term_id, termbase_id
    delete_entry_requested = pyqtSignal(int, int, str, str)  # term_id, termbase_id, source, target
    font_size_changed = pyqtSignal(int)  # Emits new font size (points) when user clicks A-/A+
    refresh_requested = pyqtSignal()  # v1.10.68: user clicked the refresh button (e.g. after external DB edits via the Trados plugin)

    # Bounds for the inline A-/A+ font zoomer. Match the Settings spin box range
    # (6-16 pt) plus a little extra headroom on both sides.
    MIN_FONT_SIZE = 6
    MAX_FONT_SIZE = 20
    
    def __init__(self, parent=None, db_manager=None, log_callback=None, theme_manager=None):
        super().__init__(parent)
        self.db_manager = db_manager
        self.log = log_callback if log_callback else print
        self.theme_manager = theme_manager
        self.current_source = ""
        self.current_source_lang = None
        self.current_target_lang = None
        self.current_project_id = None  # Store project ID for termbase priority lookup
        
        # Debug mode - disable verbose tokenization logging by default (performance)
        self.debug_tokenize = False
        
        # Default font settings (will be updated from main app settings)
        self.current_font_family = "Segoe UI"
        self.current_font_size = 10
        self.current_font_bold = False
        
        # Track terms by shortcut number for Alt+1-9 insertion
        self.shortcut_terms = {}  # {1: "translation1", 2: "translation2", ...}

        self.init_ui()
    
    def init_ui(self):
        """Initialize the UI"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)
        layout.setSpacing(5)

        # Get theme colors
        if self.theme_manager:
            theme = self.theme_manager.current_theme
            bg_color = theme.base
            border_color = theme.border
            header_bg = theme.panel_info
            header_text = theme.button_info
            info_text = theme.text_disabled
        else:
            # Fallback colors if no theme manager
            bg_color = "white"
            border_color = "#ddd"
            header_bg = "#E3F2FD"
            header_text = "#1565C0"
            info_text = "#999"

        # Header
        header = QLabel("")  # Empty - tab already shows the name
        header.setStyleSheet(f"""
            QLabel {{
                font-weight: bold;
                font-size: 12px;
                color: {header_text};
                padding: 5px;
                background-color: {header_bg};
                border-radius: 4px;
            }}
        """)
        header.hide()  # Hide the header to save space
        layout.addWidget(header)

        # ── A-/A+ font zoomer (right-aligned strip above the scroll area) ──
        # Mirrors the buttons in the Trados plugin TermLens panel: small "A"
        # decreases, large bold "A" increases. Clicks bump the font and emit
        # font_size_changed so the host can persist the new size.
        zoom_row = QHBoxLayout()
        zoom_row.setContentsMargins(0, 0, 0, 0)
        zoom_row.setSpacing(2)
        zoom_row.addStretch()

        # ── Refresh button (v1.10.68) ──
        # Workbench's TermLens display is driven by an in-memory
        # ``termbase_index`` built once on project load. When the same
        # SQLite database is modified by another process — most often
        # the Supervertaler for Trados plugin deleting / adding terms
        # while Workbench is also open — the index goes stale.
        # Symptoms: deleted terms keep appearing as TermLens pills,
        # right-click → Edit opens an empty dialog (stale term_id),
        # newly-added terms don't show up. F5 / re-clicking the
        # segment doesn't help because that just re-searches the
        # stale index; the index itself needs a full rebuild.
        #
        # This button gives the user a one-click way to drop the
        # in-memory state and reread from the database. Emits a
        # ``refresh_requested`` signal — the host (Supervertaler.py)
        # wires it up to ``_post_termbase_delete_refresh`` (cache
        # clear + index rebuild + TermLens refresh for current
        # segment). Cheap (<1 second on typical termbase sizes), so
        # users can hit it whenever they suspect drift.
        self._btn_refresh = QPushButton("🔄")
        refresh_font = QFont("Segoe UI", 9)
        self._btn_refresh.setFont(refresh_font)
        self._btn_refresh.setFixedSize(22, 20)
        self._btn_refresh.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_refresh.setToolTip(
            "Refresh termbase + TM matches  (F5)\n"
            "\n"
            "Same as pressing F5. Re-runs all searches for the\n"
            "current segment and redraws TermLens, the Match Panel,\n"
            "and the source-cell highlights.\n"
            "\n"
            "If the underlying database has been modified by another\n"
            "process (typically the Supervertaler for Trados plugin\n"
            "sharing the same database), the in-memory termbase\n"
            "index is rebuilt from disk first — so cross-process\n"
            "edits are picked up immediately.\n"
            "\n"
            "Auto-refresh runs in the background whenever the\n"
            "database file changes externally, so you rarely need\n"
            "to click this manually; it's here as an explicit\n"
            "'do it now' trigger."
        )
        self._btn_refresh.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._btn_refresh.clicked.connect(self._on_refresh_clicked)
        zoom_row.addWidget(self._btn_refresh)

        # Thin separator between refresh and font zoomer.
        zoom_row.addSpacing(6)

        self._btn_font_down = QPushButton("A")
        font_down_font = QFont("Segoe UI", 7)
        self._btn_font_down.setFont(font_down_font)
        self._btn_font_down.setFixedSize(22, 20)
        self._btn_font_down.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_font_down.setToolTip("Decrease TermLens font size")
        self._btn_font_down.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._btn_font_down.clicked.connect(lambda: self._change_font_size(-1))
        zoom_row.addWidget(self._btn_font_down)

        self._btn_font_up = QPushButton("A")
        font_up_font = QFont("Segoe UI", 11)
        font_up_font.setBold(True)
        self._btn_font_up.setFont(font_up_font)
        self._btn_font_up.setFixedSize(22, 20)
        self._btn_font_up.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_font_up.setToolTip("Increase TermLens font size")
        self._btn_font_up.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._btn_font_up.clicked.connect(lambda: self._change_font_size(+1))
        zoom_row.addWidget(self._btn_font_up)

        # v1.10.99 — contextual help button for the docked TermLens
        # panel. Sits at the far right of the header row, alongside
        # the refresh + font-zoom buttons. Routes through the global
        # ``help_system.open_help`` helper so its behaviour matches
        # F1 + every other in-app ? affordance.
        zoom_row.addSpacing(6)
        self._btn_help = QPushButton("?")
        help_font = QFont("Segoe UI", 9)
        help_font.setBold(True)
        self._btn_help.setFont(help_font)
        self._btn_help.setFixedSize(22, 20)
        self._btn_help.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_help.setToolTip(
            "Open help for TermLens (F1)\n\n"
            "Opens https://docs.supervertaler.com/workbench/termbases/termlens/\n"
            "in your browser."
        )
        self._btn_help.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._btn_help.clicked.connect(self._on_help_clicked)
        zoom_row.addWidget(self._btn_help)

        layout.addLayout(zoom_row)

        # Scroll area for term blocks
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)  # No horizontal scroll
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setStyleSheet(f"""
            QScrollArea {{
                border: 1px solid {border_color};
                border-radius: 4px;
                background-color: {bg_color};
            }}
        """)

        # Container for term blocks (flow layout with wrapping)
        self.terms_container = QWidget()
        self.terms_layout = FlowLayout(self.terms_container, margin=5, spacing=4)

        scroll.setWidget(self.terms_container)
        layout.addWidget(scroll)

        # Info label - use slightly brighter text for dark mode
        self.info_label = QLabel("No segment selected")
        self.info_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        is_dark = self.theme_manager and self.theme_manager.current_theme.name == "Dark"
        info_label_color = "#909090" if is_dark else info_text
        self.info_label.setStyleSheet(f"color: {info_label_color}; font-size: 9pt; padding: 5px;")
        layout.addWidget(self.info_label)
        
        # Store references for theme refresh
        self.header = header
        self.scroll = scroll
    
    def apply_theme(self):
        """Refresh all theme-dependent colors when theme changes"""
        if not self.theme_manager:
            return
        
        theme = self.theme_manager.current_theme
        bg_color = theme.base
        border_color = theme.border
        header_bg = theme.panel_info
        header_text = theme.button_info
        info_text = theme.text_disabled
        
        # Update header
        if hasattr(self, 'header'):
            self.header.setStyleSheet(f"""
                QLabel {{
                    font-weight: bold;
                    font-size: 12px;
                    color: {header_text};
                    padding: 5px;
                    background-color: {header_bg};
                    border-radius: 4px;
                }}
            """)
        
        # Update scroll area
        if hasattr(self, 'scroll'):
            self.scroll.setStyleSheet(f"""
                QScrollArea {{
                    border: 1px solid {border_color};
                    border-radius: 4px;
                    background-color: {bg_color};
                }}
            """)
        
        # Update info label - use slightly brighter text for better visibility in dark mode
        if hasattr(self, 'info_label'):
            is_dark = theme.name == "Dark"
            info_label_color = "#909090" if is_dark else info_text
            self.info_label.setStyleSheet(f"color: {info_label_color}; font-size: 9pt; padding: 5px;")

        # Refresh term blocks to pick up new theme colors
        if hasattr(self, '_last_termbase_matches') and hasattr(self, '_last_nt_matches') and hasattr(self, 'current_source'):
            # Re-render with stored matches to apply new theme colors
            if self.current_source:
                self.update_with_matches(
                    self.current_source,
                    self._last_termbase_matches or [],
                    self._last_nt_matches,
                    self._status_hint if hasattr(self, '_status_hint') else None
                )
    
    def _on_refresh_clicked(self):
        """User clicked the 🔄 refresh button.

        Just emits ``refresh_requested`` and gives a brief visual cue
        (status text + temporary button-disabled state) so the user
        sees the action registered. The host wires the signal to
        ``_post_termbase_delete_refresh`` which does the actual work
        (cache clear + ``_build_termbase_index`` + segment re-search).
        """
        try:
            # Tiny UX: flip the button to a check + disable for half a
            # second so it's obvious the click registered, even though
            # the underlying rebuild is usually instant.
            self._btn_refresh.setEnabled(False)
            self._btn_refresh.setText("✓")
            # Surface the request in the info label too so users on
            # large termbases (where the rebuild takes a beat) get
            # immediate confirmation rather than wondering if the
            # button did anything.
            try:
                if hasattr(self, 'info_label') and self.info_label is not None:
                    self.info_label.setText("Refreshing termbases from disk…")
            except Exception:
                pass

            self.refresh_requested.emit()
            self.log("🔄 TermLens: Refresh requested by user")
        finally:
            # Re-enable after a short delay so the visual cue is
            # noticeable but doesn't block rapid re-clicks.
            from PyQt6.QtCore import QTimer
            def _restore():
                try:
                    self._btn_refresh.setEnabled(True)
                    self._btn_refresh.setText("🔄")
                except Exception:
                    pass
            QTimer.singleShot(500, _restore)

    def _on_help_clicked(self):
        """Open the TermLens help page in the user's default browser.

        Routed through the global ``help_system.open_help`` helper so the
        URL resolution (Topics constant → full docs.supervertaler.com URL)
        stays consistent with F1 and every other in-app ? button. Wrapped
        in try/except because the help-system module is optional from
        the widget's perspective — if it fails to import for any reason,
        the click silently does nothing rather than crashing the widget.
        """
        try:
            from modules.help_system import open_help, Topics
            open_help(Topics.GLOSSARY_TERMLENS)
        except Exception:
            pass

    def _change_font_size(self, delta: int):
        """Bump the TermLens font size by ±delta points (clamped) and refresh.

        Triggered by the inline A-/A+ buttons. Re-renders cached matches so
        block layouts pick up the new size, and emits font_size_changed so
        the host can persist the value and sync the Match Panel copy.
        """
        new_size = max(self.MIN_FONT_SIZE,
                       min(self.MAX_FONT_SIZE, self.current_font_size + delta))
        if new_size == self.current_font_size:
            return  # Already at the boundary

        self.current_font_size = new_size

        # Re-render so blocks/badges/count labels rebuild at the new size –
        # set_font_settings only retags label fonts and won't relayout cleanly.
        if (getattr(self, 'current_source', '') and
                hasattr(self, '_last_termbase_matches')):
            self.update_with_matches(
                self.current_source,
                self._last_termbase_matches or [],
                getattr(self, '_last_nt_matches', None),
                getattr(self, '_status_hint', None),
            )
        else:
            # No cached matches – just update font on whatever's there.
            self.set_font_settings(self.current_font_family,
                                   self.current_font_size,
                                   self.current_font_bold)

        self.font_size_changed.emit(self.current_font_size)

    def set_font_settings(self, font_family: str = "Segoe UI", font_size: int = 10, bold: bool = False):
        """Update font settings for TermLens
        
        Args:
            font_family: Font family name
            font_size: Font size in points
            bold: Whether to use bold font
        """
        self.current_font_family = font_family
        self.current_font_size = font_size
        self.current_font_bold = bold
        
        # Refresh display if we have content
        if hasattr(self, 'current_source') and self.current_source:
            # Get all existing term blocks
            term_blocks = []
            nt_blocks = []
            
            for i in range(self.terms_layout.count()):
                item = self.terms_layout.itemAt(i)
                if item and item.widget():
                    widget = item.widget()
                    if isinstance(widget, TermBlock):
                        term_blocks.append(widget)
                    elif isinstance(widget, NTBlock):
                        nt_blocks.append(widget)
            
            # Update font for all term blocks
            for block in term_blocks:
                if block.source_label is None:      # a painted word: no labels
                    block.set_plain_font(self.current_font_family, self.current_font_size,
                                         self.current_font_bold)
                    continue
                if hasattr(block, 'source_label'):
                    font = QFont(self.current_font_family)
                    font.setPointSize(self.current_font_size)
                    font.setBold(self.current_font_bold)
                    block.source_label.setFont(font)
                
                # Update translation labels
                layout = block.layout()
                if layout:
                    for i in range(layout.count()):
                        item = layout.itemAt(i)
                        if item and item.widget():
                            label = item.widget()
                            if isinstance(label, QLabel) and label != block.source_label:
                                font = QFont(self.current_font_family)
                                font.setPointSize(max(6, self.current_font_size - 2))
                                font.setBold(self.current_font_bold)
                                label.setFont(font)
            
            # Update font for NT blocks
            for block in nt_blocks:
                if hasattr(block, 'source_label'):
                    font = QFont(self.current_font_family)
                    font.setPointSize(self.current_font_size)
                    font.setBold(self.current_font_bold)
                    block.source_label.setFont(font)
    
    def showEvent(self, event):
        """Flush a deferred TermLens render queued while this panel was hidden.

        Pairs with the visibility guard at the top of update_with_matches: when
        the panel was off-screen we stashed the latest segment's data instead of
        building widgets. Now that it's visible, render that pending data so the
        panel reflects the current segment.
        """
        super().showEvent(event)
        pending = getattr(self, '_pending_update', None)
        if pending is not None:
            self._pending_update = None
            self.update_with_matches(*pending)

    def update_with_matches(self, source_text: str, termbase_matches: List[Dict], nt_matches: List[Dict] = None, status_hint: str = None):
        """
        Update the TermLens display with pre-computed termbase and NT matches

        RYS-STYLE DISPLAY: Show source text as tokens with translations underneath

        Args:
            source_text: Source segment text
            termbase_matches: List of termbase match dicts from Translation Results
            nt_matches: Optional list of NT match dicts with 'text', 'start', 'end', 'list_name' keys
            status_hint: Optional hint about why there might be no matches (e.g., 'no_termbases_activated', 'wrong_language')
        """
        # ⚡ PERF (v1.10.283): rendering builds one TermBlock/NTBlock widget per
        # source token and is the dominant per-click cost (~50–280 ms, and it runs
        # for BOTH the under-grid and Match-Panel TermLens on every segment click).
        # When this instance isn't visible (under-grid panel collapsed, or Match
        # Panel on a background tab) there's no point building those widgets now —
        # stash the latest data and render lazily on showEvent. This roughly halves
        # per-click TermLens cost whenever only one of the two panels is on screen.
        if not self.isVisible():
            self._pending_update = (source_text, termbase_matches, nt_matches, status_hint)
            return
        self._pending_update = None

        # Blocks added to a container that is on screen are shown one by one,
        # and Qt lays out all of them again after each: for 33 words, 33
        # passes over 33 blocks. Hidden while it is rebuilt, the container
        # is laid out once. It comes back in the same turn of the event
        # loop, so nothing is painted in between, and it comes back whatever
        # happens while building.
        self.terms_container.hide()
        try:
            self._render_matches(source_text, termbase_matches, nt_matches, status_hint)
        finally:
            self.terms_container.show()

    def _render_matches(self, source_text, termbase_matches, nt_matches, status_hint):
        """Rebuild the blocks. Only update_with_matches calls this."""
        self.current_source = source_text
        # Store matches for theme refresh
        self._last_termbase_matches = termbase_matches
        self._last_nt_matches = nt_matches

        # Clear existing blocks and shortcut mappings
        self.clear_terms()
        self.shortcut_terms = {}  # Reset shortcut mappings

        if not source_text or not source_text.strip():
            self.info_label.setText("No segment selected")
            return

        # Strip HTML/XML tags from source text for display in TermLens
        # This handles CAT tool tags like <b>, </b>, <i>, </i>, <u>, </u>, <bi>, <sub>, <sup>, <li-o>, <li-b>
        # as well as memoQ tags {1}, [2}, {3], Trados tags <1>, </1>, and Déjà Vu tags {00001}
        display_text = re.sub(r'</?(?:b|i|u|bi|sub|sup|li-[ob]|\d+)/?>', '', source_text)  # HTML/XML tags
        display_text = re.sub(r'[\[{]\d+[}\]]', '', display_text)  # memoQ/Phrase numeric tags: {1}, [2}, {3]
        display_text = re.sub(r'\{\d{5}\}', '', display_text)  # Déjà Vu tags: {00001}
        # memoQ content tags: [uicontrol id="..."}  or  {uicontrol]  or  [tagname ...}  or  {tagname]
        display_text = re.sub(r'\[[^\[\]]*\}', '', display_text)  # Opening: [anything}
        display_text = re.sub(r'\{[^\{\}]*\]', '', display_text)  # Closing: {anything]
        # Strip leading/trailing whitespace including newlines at edges,
        # but preserve internal newlines for line-break rendering in the flow layout
        display_text = display_text.strip()

        # If stripping tags leaves nothing, fall back to original
        if not display_text:
            display_text = source_text

        has_termbase = termbase_matches and len(termbase_matches) > 0
        has_nt = nt_matches and len(nt_matches) > 0

        # Store status hint for info label (will be set at the end)
        self._status_hint = status_hint
        self._has_any_matches = has_termbase or has_nt
        
        # Convert termbase matches to dict for easy lookup: {source_term.lower(): [translations]}
        matches_dict = {}
        if termbase_matches:
            for match in termbase_matches:
                source_term = match.get('source_term', match.get('source', ''))
                target_term = match.get('target_term', match.get('translation', ''))
                
                # Ensure source_term and target_term are strings
                if not isinstance(source_term, str):
                    source_term = str(source_term) if source_term else ''
                if not isinstance(target_term, str):
                    target_term = str(target_term) if target_term else ''
                
                if not source_term or not target_term:
                    continue
                
                # Strip punctuation from key to match lookup normalization
                # This ensures "ca." in glossary matches "ca." token stripped to "ca"
                PUNCT_CHARS_FOR_KEY = '.,;:!?\"\'\u201C\u201D\u201E\u00AB\u00BB\u2018\u2019\u201A\u2039\u203A()[]'
                key = source_term.lower().strip(PUNCT_CHARS_FOR_KEY)
                if key not in matches_dict:
                    matches_dict[key] = []
                
                # Add main target term (include term_id and termbase_id
                # for the edit/delete context menu).
                #
                # v1.10.73 (Tier 1 + Tier 2): preserve the metadata
                # fields (``definition``, ``domain``, ``url``,
                # ``source_abbreviation``, ``target_abbreviation``)
                # and ``source_synonyms`` from the upstream match so
                # the TermBlock's _meta_lines tooltip helper sees
                # them. Pre-v1.10.73 this append produced a thinner
                # dict that dropped everything except the bare
                # essentials, which is why the tooltip's metadata
                # rendering — wired up since forever — never
                # actually showed anything.
                matches_dict[key].append({
                    'target_term': target_term,
                    'termbase_name': match.get('termbase_name', ''),
                    'ranking': match.get('ranking', 99),
                    'is_project_termbase': match.get('is_project_termbase', False),
                    'term_id': match.get('term_id'),
                    'termbase_id': match.get('termbase_id'),
                    'notes': match.get('notes', ''),
                    'definition': match.get('definition', ''),
                    'domain': match.get('domain', ''),
                    'url': match.get('url', ''),
                    'source_abbreviation': match.get('source_abbreviation', ''),
                    'target_abbreviation': match.get('target_abbreviation', ''),
                    'source_synonyms': match.get('source_synonyms', []),
                    # v1.10.75 (Tier 3) — forbidden + NT flags drive
                    # the new background-colour treatment in TermBlock.
                    'forbidden': match.get('forbidden', False),
                    'is_nontranslatable': match.get('is_nontranslatable', False),
                    'matched_via_abbreviation': match.get('matched_via_abbreviation', False),
                    # v1.10.81 — primary chip entry; not a synonym.
                    # The sort key below uses this to keep primary
                    # entries above their own synonyms.
                    'is_synonym': False,
                })

                # Add target synonyms as additional translation chips.
                # Each synonym becomes its own row in the +N popup +
                # gets its own shortcut number, so the user can pick
                # the preferred alternative without leaving TermLens.
                # Synonym chips inherit the main entry's metadata —
                # the source synonyms tooltip line is the same; the
                # abbreviation pair, definition, domain, URL are all
                # properties of the term entry, not of the particular
                # surface form, so they apply to the synonym too.
                target_synonyms = match.get('target_synonyms', [])
                for synonym in target_synonyms:
                    matches_dict[key].append({
                        'target_term': synonym,
                        'termbase_name': match.get('termbase_name', '') + ' (syn)',
                        # v1.10.81 — synonym ranking now inherits the
                        # parent entry's ranking unchanged. The
                        # previous "+ 1" was written when ranking was
                        # a higher-is-lower-priority number (99 =
                        # default low); with the v1.10.69 ranking-as-
                        # flag semantics (1 = project-priority, 0 =
                        # background) it had the opposite effect — a
                        # background entry's synonyms became
                        # ranking=1, accidentally promoting them to
                        # project-priority and getting them sorted
                        # ABOVE their own parent's primary entry by
                        # the v1.10.80 sort. End result: a synonym
                        # of a background termbase term displayed in
                        # pink (the project-termbase colour) and
                        # outranked the primary translation. Reported
                        # by a user: "I am wondering why 'Comes With'
                        # is pink, though, since none of these are in
                        # the project glossary." Inheriting the
                        # parent's ranking keeps synonym colour
                        # accurate; the new is_synonym flag below
                        # handles "synonyms sort after primary"
                        # without needing the ranking arithmetic.
                        'ranking': match.get('ranking', 99),
                        'is_project_termbase': match.get('is_project_termbase', False),
                        'term_id': match.get('term_id'),
                        'termbase_id': match.get('termbase_id'),
                        'notes': match.get('notes', ''),
                        'definition': match.get('definition', ''),
                        'domain': match.get('domain', ''),
                        'url': match.get('url', ''),
                        'source_abbreviation': match.get('source_abbreviation', ''),
                        'target_abbreviation': match.get('target_abbreviation', ''),
                        'source_synonyms': match.get('source_synonyms', []),
                        # v1.10.75 (Tier 3) flags inherited from main entry.
                        'forbidden': match.get('forbidden', False),
                        'is_nontranslatable': match.get('is_nontranslatable', False),
                        # Synonym chips are never abbreviation chips —
                        # only the main match position can be one.
                        'matched_via_abbreviation': False,
                        # v1.10.81 — synonym marker. Used by the sort
                        # below to keep synonyms below their parent's
                        # primary entry (parent first, syn1, syn2 …)
                        # within the same ranking tier.
                        'is_synonym': True,
                    })
        
        # Convert NT matches to dict keyed by lowercase text. Each entry
        # carries the originating termbase metadata (list_name, term_id,
        # termbase_id) so the NT block can emit edit/delete signals back
        # to the host. find_nt_matches_in_source populates term_id and
        # termbase_id for every match it returns.
        nt_dict = {}
        if nt_matches:
            for match in nt_matches:
                nt_text = match.get('text', '')
                if nt_text:
                    nt_dict[nt_text.lower()] = {
                        'list_name': match.get('list_name', 'Non-Translatables'),
                        'term_id': match.get('term_id'),
                        'termbase_id': match.get('termbase_id'),
                    }
        
        # v1.10.77 — sort each translation list so the chip's primary
        # entry (translations[0]) matches the Trados TermBlock sort
        # order:
        #   1. Non-forbidden first  (forbidden last — users shouldn't
        #      accidentally insert a "do not use" translation just
        #      because it happens to be from the project termbase).
        #   2. Project / priority-1 entries first (pink chip for the
        #      project-canonical translation).
        #   3. Tiebreak by termbase name for stable ordering.
        #
        # v1.10.80 fix: sort the project-first tier by ``ranking``
        # (the computed flag from _build_termbase_index that's 1
        # for project-termbase / priority-1 entries, 0 for the
        # rest) rather than by the raw ``is_project_termbase``
        # column.
        #
        # The v1.10.77 sort only checked ``is_project_termbase``
        # which is a column on the ``termbases`` table — but the
        # *project termbase* designation in practice comes from
        # ``termbase_activation.priority = 1`` for the current
        # project, not from that column. So a user with BRANTS
        # marked as the project termbase via the activation table
        # (which is how the Termbases tab UI does it) had a
        # BRANTS row with ``is_project_termbase = 0`` AND
        # ``priority = 1`` — Workbench's index builder correctly
        # computed ``ranking = 1`` for it, but the v1.10.77 sort
        # ignored that and ended up putting PATENTS (ranking=0)
        # before BRANTS (ranking=1) because lower ranking sorted
        # first. Result: blue PATENTS chip instead of pink BRANTS
        # chip on a project where BRANTS *was* the project
        # termbase. Reported by the user side-by-side with the
        # Trados TermLens which correctly showed pink. The fix is
        # to invert the ranking comparison (project-first means
        # ranking == 1 wins) and tiebreak by termbase name for
        # stability.
        for _key in matches_dict:
            matches_dict[_key].sort(key=lambda t: (
                bool(t.get('forbidden', False)),               # non-forbidden first
                not (t.get('ranking') == 1),                   # ranking == 1 (project/priority-1) first
                bool(t.get('is_synonym', False)),              # primary entries before synonyms
                t.get('termbase_name', '') or '',              # alphabetical tiebreak
            ))

        # Combine all known multi-word terms for tokenization
        all_terms_dict = dict(matches_dict)
        for nt_key in nt_dict:
            if nt_key not in all_terms_dict:
                all_terms_dict[nt_key] = []  # Empty list = NT only
        
        # Tokenize the tag-stripped display text, respecting multi-word terms
        tokens = self.tokenize_with_multiword_terms(display_text, all_terms_dict)
        
        if not tokens:
            self.info_label.setText("No words to analyze")
            return
        
        # Create blocks for each token
        blocks_with_translations = 0
        blocks_with_nt = 0
        shortcut_counter = 0  # Track shortcut numbers for terms with translations
        
        # Comprehensive set of quote and punctuation characters to strip
        # Using Unicode escapes to avoid encoding issues
        # Include brackets for terms like "(typisch)" to match "typisch"
        PUNCT_CHARS = '.,;:!?\"\'\u201C\u201D\u201E\u00AB\u00BB\u2018\u2019\u201A\u2039\u203A()[]'
        
        # Track which terms have already been assigned shortcuts (avoid duplicates)
        assigned_shortcuts = set()
        
        for token in tokens:
            # Handle newline sentinel tokens – insert a line break in the flow layout
            if token == '\n':
                lb = LineBreakWidget(self.terms_container)
                self.terms_layout.addWidget(lb)
                continue

            # Strip leading and trailing punctuation/quotes for lookup
            token_clean = token.rstrip(PUNCT_CHARS)
            token_clean = token_clean.lstrip(PUNCT_CHARS)
            lookup_key = token_clean.lower()

            # Check if this is a non-translatable
            if lookup_key in nt_dict:
                nt_meta = nt_dict[lookup_key]
                nt_block = NTBlock(
                    token, nt_meta['list_name'], self,
                    theme_manager=self.theme_manager,
                    font_size=self.current_font_size,
                    font_family=self.current_font_family,
                    font_bold=self.current_font_bold,
                    term_id=nt_meta.get('term_id'),
                    termbase_id=nt_meta.get('termbase_id'),
                )
                # nt_clicked emits a single string (the NT text); the host
                # insert handler takes (source_term, target_term). For NT
                # entries those are the same string by convention, so adapt
                # the signal here. Without this adapter, clicking an NT pill
                # would crash with a missing-argument TypeError (a latent
                # bug going back to the original NTBlock implementation).
                nt_block.nt_clicked.connect(
                    lambda txt: self.on_term_insert_requested(txt, txt)
                )
                # Wire edit/delete to the same host handlers used by TermBlock –
                # NT entries are termbase rows, so the existing edit dialog
                # handles them transparently.
                nt_block.edit_requested.connect(self._on_edit_entry_requested)
                nt_block.delete_requested.connect(self._on_delete_entry_requested)
                self.terms_layout.addWidget(nt_block)
                blocks_with_nt += 1
            else:
                # Get termbase translations for this token
                translations = matches_dict.get(lookup_key, [])
                
                # Assign shortcut number only to first occurrence of each term with translations.
                # TermLens numbering starts at 1 (Alt+1..Alt+9), because Alt+0 is reserved for the Compare Panel.
                # After 1-9, we support 11-99 via double-tap Alt+N,N (internally 11-19).
                shortcut_num = None
                if translations and lookup_key not in assigned_shortcuts:
                    if shortcut_counter < 18:  # Support up to 18 terms (1-9 + 11-99)
                        # Map 0-8 -> 1-9, 9-17 -> 11-19
                        shortcut_num = shortcut_counter + 1 if shortcut_counter < 9 else shortcut_counter + 2
                        # Store the first translation for Alt+N insertion
                        first_trans = translations[0]
                        if isinstance(first_trans, dict):
                            self.shortcut_terms[shortcut_num] = first_trans.get('target_term', '')
                        else:
                            self.shortcut_terms[shortcut_num] = str(first_trans)
                    shortcut_counter += 1
                    assigned_shortcuts.add(lookup_key)
                
                # Create term block (even if no translation - shows source word)
                term_block = TermBlock(token, translations, self, theme_manager=self.theme_manager, 
                                       font_size=self.current_font_size, font_family=self.current_font_family, 
                                       font_bold=self.current_font_bold, shortcut_number=shortcut_num)
                term_block.term_clicked.connect(self.on_term_insert_requested)
                term_block.edit_requested.connect(self._on_edit_entry_requested)
                term_block.delete_requested.connect(self._on_delete_entry_requested)
                self.terms_layout.addWidget(term_block)
                
                if translations:
                    blocks_with_translations += 1
        
        # Count only real word tokens (exclude '\n' sentinels)
        word_count = sum(1 for t in tokens if t != '\n')

        info_parts = []
        if blocks_with_translations > 0:
            info_parts.append(f"{blocks_with_translations} terms")
        if blocks_with_nt > 0:
            info_parts.append(f"{blocks_with_nt} NTs")

        if info_parts:
            self.info_label.setText(f"✓ Found {', '.join(info_parts)} in {word_count} words")
        else:
            # Show appropriate message based on status hint when no matches
            status_hint = getattr(self, '_status_hint', None)
            if status_hint == 'no_termbases_activated':
                self.info_label.setText(f"No termbases activated ({word_count} words)")
            elif status_hint == 'wrong_language':
                self.info_label.setText(f"Termbases don't match language pair ({word_count} words)")
            else:
                self.info_label.setText(f"No matches in {word_count} words")
    
    def get_all_termbase_matches(self, text: str) -> Dict[str, List[Dict]]:
        """
        Get all termbase matches for text by using the proper termbase search
        
        This uses the SAME search logic as the Translation Results panel,
        ensuring we only show terms that actually match, not false positives.
        
        Args:
            text: Source text
            
        Returns:
            Dict mapping source term (lowercase) to list of translation dicts
        """
        if not self.db_manager or not self.current_source_lang or not self.current_target_lang:
            return {}
        
        matches = {}
        
        try:
            # Extract all words from the text to search
            # Use the same token pattern as we use for display
            # Includes / for unit-style terms like kg/l, m/s, etc.
            token_pattern = re.compile(r'(?<!\w)[\w.,%-/]+(?!\w)', re.UNICODE)
            tokens = [match.group() for match in token_pattern.finditer(text)]
            
            # Also check for multi-word phrases (up to 8 words)
            words = re.findall(r'\b[\w-]+\b', text, re.UNICODE)
            phrases_to_check = []
            
            # Generate n-grams for multi-word term detection
            for n in range(2, min(9, len(words) + 1)):
                for i in range(len(words) - n + 1):
                    phrase = ' '.join(words[i:i+n])
                    phrases_to_check.append(phrase)
            
            # Search each token and phrase using the database's search_termbases method
            all_search_terms = set(tokens + phrases_to_check)
            
            for search_term in all_search_terms:
                if not search_term or len(search_term) < 2:
                    continue
                
                # Strip trailing punctuation for search (but keep internal punctuation like "gew.%")
                # This handles cases like "edelmetalen." → "edelmetalen"
                search_term_clean = search_term.rstrip('.,;:!?')
                if not search_term_clean or len(search_term_clean) < 2:
                    continue
                
                # Use the SAME search method as translation results panel
                results = self.db_manager.search_termbases(
                    search_term=search_term_clean,
                    source_lang=self.current_source_lang,
                    target_lang=self.current_target_lang,
                    project_id=self.current_project_id,
                    min_length=2
                )
                
                # Add results to matches dict, but ONLY if the source term actually exists in the text
                for result in results:
                    source_term = result.get('source_term', '')
                    if not source_term:
                        continue
                    
                    # CRITICAL FIX: Verify the source term actually exists in the segment
                    # This prevents false positives like "het gebruik van" showing when only "het" exists
                    source_lower = source_term.lower()
                    text_lower = text.lower()
                    
                    # Normalize text: replace ALL quote variants with spaces
                    # Using Unicode escapes to avoid encoding issues
                    normalized_text = text_lower
                    for quote_char in '\"\'\u201C\u201D\u201E\u00AB\u00BB\u2018\u2019\u201A\u2039\u203A':
                        normalized_text = normalized_text.replace(quote_char, ' ')
                    
                    # CRITICAL FIX v1.9.118: Strip punctuation from glossary term before matching
                    # This allows entries like "...problemen." (with period) to match source text
                    # where tokenization strips the period during word splitting
                    # Comprehensive set of quote and punctuation characters to strip
                    PUNCT_CHARS = '.,;:!?\"\'\u201C\u201D\u201E\u00AB\u00BB\u2018\u2019\u201A\u2039\u203A'
                    normalized_term = source_lower.rstrip(PUNCT_CHARS).lstrip(PUNCT_CHARS)
                    
                    # Use word boundaries to match complete words/phrases only
                    if ' ' in source_term:
                        # Multi-word term - must exist as exact phrase
                        pattern = r'\b' + re.escape(normalized_term) + r'\b'
                    else:
                        # Single word
                        pattern = r'\b' + re.escape(normalized_term) + r'\b'
                    
                    # Try matching on normalized text first, then original
                    if not re.search(pattern, normalized_text) and not re.search(pattern, text_lower):
                        continue  # Skip - term not actually in segment
                    
                    key = source_lower
                    if key not in matches:
                        matches[key] = []
                    
                    # DEDUPLICATION: Only add if not already present
                    # Check by target_term to avoid duplicate translations
                    target_term = result.get('target_term', '')
                    already_exists = any(
                        m.get('target_term', '') == target_term 
                        for m in matches[key]
                    )
                    if not already_exists:
                        matches[key].append(result)
            
            return matches
        except Exception as e:
            self.log(f"✗ Error getting termbase matches: {e}")
            import traceback
            traceback.print_exc()
            return {}
    
    def tokenize_with_multiword_terms(self, text: str, matches: Dict[str, List[Dict]]) -> List[str]:
        """
        Tokenize text, preserving multi-word terms found in termbase.
        Newline characters (\n) in the source are preserved as '\n' sentinel tokens.

        Args:
            text: Source text
            matches: Dict of termbase matches (from get_all_termbase_matches)

        Returns:
            List of tokens (words/phrases/numbers/newlines), with multi-word terms kept together
        """
        # Split by newlines, tokenize each line, and insert '\n' sentinels between lines
        lines = text.split('\n')
        all_tokens = []
        for i, line in enumerate(lines):
            if i > 0:
                all_tokens.append('\n')  # sentinel token for line break
            line_tokens = self._tokenize_line(line, matches)
            all_tokens.extend(line_tokens)
        return all_tokens

    def _tokenize_line(self, text: str, matches: Dict[str, List[Dict]]) -> List[str]:
        """
        Tokenize a single line of text, preserving multi-word terms found in termbase.

        Args:
            text: Single line of source text (no newlines)
            matches: Dict of termbase matches (from get_all_termbase_matches)

        Returns:
            List of tokens (words/phrases/numbers), with multi-word terms kept together
        """
        # DEBUG: Log multi-word terms we're looking for (only if debug_tokenize enabled)
        multi_word_terms = [k for k in matches.keys() if ' ' in k]
        if multi_word_terms and self.debug_tokenize:
            self.log(f"🔍 Tokenize: Looking for {len(multi_word_terms)} multi-word terms:")
            for term in sorted(multi_word_terms, key=len, reverse=True)[:3]:
                self.log(f"    - '{term}'")

        # Sort matched terms by length (longest first) to match multi-word terms first
        matched_terms = sorted(matches.keys(), key=len, reverse=True)

        # Track which parts of the text have been matched
        text_lower = text.lower()
        used_positions = set()
        tokens_with_positions = []

        # First pass: find multi-word terms with proper word boundary checking
        for term in matched_terms:
            if ' ' in term:  # Only process multi-word terms in first pass
                # Use regex with word boundaries to find term
                term_escaped = re.escape(term)

                # Check if term has punctuation - use different pattern
                if any(char in term for char in ['.', '%', ',', '-', '/']):
                    pattern = r'(?<!\w)' + term_escaped + r'(?!\w)'
                else:
                    pattern = r'\b' + term_escaped + r'\b'

                # DEBUG: Check if multi-word term is found (only if debug_tokenize enabled)
                found = re.search(pattern, text_lower)
                if self.debug_tokenize:
                    self.log(f"🔍 Tokenize: Pattern '{pattern}' for '{term}' → {'FOUND' if found else 'NOT FOUND'}")
                    if found:
                        self.log(f"    Match at position {found.span()}: '{text[found.start():found.end()]}'")

                # Find all matches using regex
                for match in re.finditer(pattern, text_lower):
                    pos = match.start()

                    # Check if this position overlaps with already matched terms
                    term_positions = set(range(pos, pos + len(term)))
                    if not term_positions.intersection(used_positions):
                        # Extract the original case version
                        original_term = text[pos:pos + len(term)]
                        tokens_with_positions.append((pos, len(term), original_term))
                        used_positions.update(term_positions)
                        if self.debug_tokenize:
                            self.log(f"    ✅ Added multi-word token: '{original_term}' covering positions {pos}-{pos+len(term)}")

        # DEBUG: Log used_positions after first pass (only if debug_tokenize enabled)
        if matches and ' ' in sorted(matches.keys(), key=len, reverse=True)[0] and self.debug_tokenize:
            self.log(f"🔍 After first pass: {len(used_positions)} positions marked as used")
            self.log(f"    Used positions: {sorted(list(used_positions))[:20]}...")

        # Read the "hide shorter matches" setting from the parent app (respects Settings checkbox)
        hide_shorter = False
        p = self.parent()
        while p:
            if hasattr(p, 'termbase_hide_shorter_matches'):
                hide_shorter = p.termbase_hide_shorter_matches
                break
            p = p.parent() if callable(getattr(p, 'parent', None)) else None

        # Second pass: fill in gaps with ALL words/numbers/punctuation combos
        # Enhanced pattern to capture words, numbers, and combinations like "gew.%", "0,1", "kg/l", etc.
        # Use (?<!\w) and (?!\w) instead of \b to handle punctuation properly
        # Includes / for unit-style terms like kg/l, m/s, etc.
        token_pattern = re.compile(r'(?<!\w)[\w.,%-/]+(?!\w)', re.UNICODE)

        PUNCT_CHARS_FOR_KEY = '.,;:!?\"\'\u201C\u201D\u201E\u00AB\u00BB\u2018\u2019\u201A\u2039\u203A()[]'

        for match in token_pattern.finditer(text):
            word_start = match.start()
            word_end = match.end()
            word_positions = set(range(word_start, word_end))
            token = match.group()

            # Check if this word has its own glossary entry
            token_key = token.lower().strip(PUNCT_CHARS_FOR_KEY)
            has_own_match = token_key in matches

            already_covered = bool(word_positions.intersection(used_positions))

            # When hide_shorter is OFF (default): always show a word that has its own glossary
            # entry, even if it sits inside a longer matched phrase.
            # When hide_shorter is ON: honour the overlap suppression unconditionally.
            if not already_covered or (has_own_match and not hide_shorter):
                # Only add once – skip if this exact (start, token) is already present
                if not any(p == word_start and t == token for p, _, t in tokens_with_positions):
                    tokens_with_positions.append((word_start, len(token), token))

            # Mark positions as used only when first claimed (keeps non-glossary filler
            # words inside a long phrase from being duplicated)
            if not already_covered:
                used_positions.update(word_positions)

        # Sort by position and extract tokens
        tokens_with_positions.sort(key=lambda x: x[0])
        tokens = [token for pos, length, token in tokens_with_positions]

        return tokens
    
    def search_term(self, term: str) -> List[Dict]:
        """
        Search termbases for a specific term
        
        Args:
            term: Source term to search
            
        Returns:
            List of translation dicts (filtered to only include terms that exist in current segment)
        """
        if not self.db_manager or not self.current_source_lang or not self.current_target_lang:
            return []
        
        try:
            # Use database manager's search_termbases method
            results = self.db_manager.search_termbases(
                search_term=term,
                source_lang=self.current_source_lang,
                target_lang=self.current_target_lang,
                project_id=self.current_project_id,
                min_length=2
            )
            
            # CRITICAL FIX: Filter out results where the source term doesn't exist in the segment
            # This prevents "het gebruik van" from showing when searching "het" if the phrase isn't in the segment
            filtered_results = []
            segment_lower = self.current_source.lower()
            
            for result in results:
                source_term = result.get('source_term', '')
                if not source_term:
                    continue
                
                # Check if this term actually exists in the current segment
                source_lower = source_term.lower()
                
                # Use word boundaries to match complete words/phrases only
                if ' ' in source_term:
                    # Multi-word term - must exist as exact phrase
                    pattern = r'\b' + re.escape(source_lower) + r'\b'
                else:
                    # Single word
                    pattern = r'\b' + re.escape(source_lower) + r'\b'
                
                if re.search(pattern, segment_lower):
                    filtered_results.append(result)
            
            return filtered_results
        except Exception as e:
            self.log(f"✗ Error searching term '{term}': {e}")
            return []
    
    def clear_terms(self):
        """Clear all term blocks"""
        # Remove all widgets from flow layout
        while self.terms_layout.count() > 0:
            item = self.terms_layout.takeAt(0)
            if item and item.widget():
                item.widget().deleteLater()

    # v1.10.87 — helpers used by the TermLensPopup (the floating Ctrl-tap
    # popup that mirrors the docked panel for keyboard-only insertion).
    def get_term_blocks(self, only_with_matches: bool = False):
        """Return the ordered list of TermBlock / NTBlock children currently
        rendered in the flow layout.

        Mirrors the Trados ``TermLensControl.BuildSegmentBlocks`` factory
        in spirit: the popup walks this list to wire keyboard cycling
        and to know which match a given shortcut number maps to. The
        order is exactly the same as the visual flow (left-to-right,
        top-to-bottom) so Right-arrow / Tab advances in reading order.

        Args:
            only_with_matches: When True, skips TermBlocks that have no
                translations (rendered as bare source words underneath
                the segment text in the docked panel). The popup uses
                this filter so keyboard cycling only visits chips the
                user can actually insert. NTBlocks are always included
                since every NTBlock represents a real non-translatable
                match by construction.
        """
        blocks = []
        for i in range(self.terms_layout.count()):
            item = self.terms_layout.itemAt(i)
            widget = item.widget() if item is not None else None
            if isinstance(widget, NTBlock):
                blocks.append(widget)
            elif isinstance(widget, TermBlock):
                if only_with_matches and not getattr(widget, 'translations', None):
                    continue
                blocks.append(widget)
        return blocks

    def set_popup_mode(self, enabled: bool):
        """Slim the widget for use inside the TermLensPopup.

        Hides the docked-panel chrome (font zoomer, refresh button, info
        label) and reduces outer margins, so the popup feels like just
        the chip grid floating at the cursor. The chips themselves are
        unchanged — same colours, same indicators, same hover popups —
        which is the whole point of reusing the docked widget rather
        than reimplementing the renderer in parallel.

        Idempotent; safe to call repeatedly.
        """
        # Hide the zoom-row controls if they exist (they're created in
        # init_ui via attributes; the row layout itself can't easily be
        # hidden, so we hide the individual buttons instead).
        # v1.10.101 — include _btn_help in the hidden set. Pre-v1.10.101
        # the help button (added in v1.10.99) kept showing up inside
        # the TermLens-popup wrapper because the popup-mode toggle's
        # button list hadn't been updated. Reported by a user testing
        # v1.10.100: "the lens pop-up still has the question mark".
        for attr in ('_btn_refresh', '_btn_font_down', '_btn_font_up', '_btn_help'):
            btn = getattr(self, attr, None)
            if btn is not None:
                btn.setVisible(not enabled)
        if hasattr(self, 'info_label') and self.info_label is not None:
            self.info_label.setVisible(not enabled)
        # Tighter outer margins inside the popup card.
        outer = self.layout()
        if outer is not None:
            if enabled:
                outer.setContentsMargins(2, 2, 2, 2)
                outer.setSpacing(0)
            else:
                outer.setContentsMargins(5, 5, 5, 5)
                outer.setSpacing(5)
        # Drop the scroll area's frame inside the popup so the only
        # visible border is the popup's own outer border.
        if hasattr(self, 'scroll') and self.scroll is not None:
            self.scroll.setFrameShape(QFrame.Shape.NoFrame if enabled else QFrame.Shape.StyledPanel)
    
    def on_term_insert_requested(self, source_term: str, target_term: str):
        """Handle request to insert a translation"""
        self.log(f"💡 TermLens: Inserting '{target_term}' for '{source_term}'")
        self.term_insert_requested.emit(target_term)
    
    def _on_edit_entry_requested(self, term_id: int, termbase_id: int):
        """Forward edit request to parent (main application)"""
        self.log(f"✏️ TermLens: Edit requested for term_id={term_id}, termbase_id={termbase_id}")
        self.edit_entry_requested.emit(term_id, termbase_id)
    
    def _on_delete_entry_requested(self, term_id: int, termbase_id: int, source_term: str, target_term: str):
        """Forward delete request to parent (main application)"""
        self.log(f"🗑️ TermLens: Delete requested for term_id={term_id}, termbase_id={termbase_id}")
        self.delete_entry_requested.emit(term_id, termbase_id, source_term, target_term)
    
    def insert_term_by_number(self, number: int) -> bool:
        """Insert term by shortcut number.

        TermLens numbering starts at 1:
        - Alt+1..Alt+9 insert 1..9
        - Double-tap Alt+N,N inserts 11..99 (internally 11..19)
        
        Args:
            number: Shortcut number (typically 1-9 or 11-19)
        
        Returns:
            True if term was inserted, False if no term at that number
        """
        if number in self.shortcut_terms and self.shortcut_terms[number]:
            target_text = self.shortcut_terms[number]
            # Display badge for logging
            if number < 10:
                badge = str(number)
            else:
                badge = str(number - 10) * 2  # "00", "11", etc.
            self.log(f"💡 TermLens: Inserting term [{badge}]: '{target_text}'")
            self.term_insert_requested.emit(target_text)
            return True
        return False
