"""
Chat Message Delegate for Supervertaler
==========================================

Custom QStyledItemDelegate for rendering chat message bubbles
with markdown support, timestamps, avatar labels, and model info.

Visual style ported from Supervertaler for Trados plugin.
"""

import re
from datetime import datetime

from PyQt6.QtCore import Qt, QSize, QRectF
from PyQt6.QtGui import (
    QPainter, QFont, QColor, QPen, QBrush,
    QPainterPath, QLinearGradient, QTextDocument,
    QAbstractTextDocumentLayout,
)
from PyQt6.QtWidgets import QStyledItemDelegate, QStyleOptionViewItem


class ChatMessageDelegate(QStyledItemDelegate):
    """Custom delegate for rendering chat messages with Trados-style bubble styling."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.padding = 4
        self.bubble_padding = 8
        self.avatar_size = 18
        self.avatar_margin = 4
        self.max_bubble_width_ratio = 0.85
        self.label_height = 16       # "You" / "Supervertaler Sidekick"
        self.timestamp_height = 14   # "HH:mm"
        self.meta_height = 14        # model/token/cost info

    # ------------------------------------------------------------------
    # Markdown
    # ------------------------------------------------------------------

    def _markdown_to_html(self, text: str, color: str = "#1a1a1a") -> str:
        """Convert simple markdown to HTML for rich text rendering."""
        text = text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')

        # Bold
        text = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', text)
        text = re.sub(r'__(.+?)__', r'<b>\1</b>', text)
        # Italic
        text = re.sub(r'\*(.+?)\*', r'<i>\1</i>', text)
        text = re.sub(r'(?<!\w)_(.+?)_(?!\w)', r'<i>\1</i>', text)
        # Inline code
        text = re.sub(
            r'`(.+?)`',
            r'<code style="background-color: #f0f0f0; padding: 2px 4px; '
            r'border-radius: 3px; font-family: Consolas, monospace;">\1</code>',
            text,
        )

        lines = text.split('\n')
        html_lines = []
        in_list = False

        for line in lines:
            stripped = line.strip()

            # Horizontal rule: ---, ***, ___
            if re.match(r'^[-*_]{3,}\s*$', stripped):
                if in_list:
                    html_lines.append('</ul>')
                    in_list = False
                html_lines.append('<hr style="border: none; border-top: 1px solid #ddd; margin: 6px 0;"/>')
                continue

            # Headings: # H1, ## H2, ### H3
            heading_match = re.match(r'^(#{1,3})\s+(.+)$', stripped)
            if heading_match:
                if in_list:
                    html_lines.append('</ul>')
                    in_list = False
                level = len(heading_match.group(1))
                heading_text = heading_match.group(2)
                sizes = {1: '12pt', 2: '10.5pt', 3: '9.5pt'}
                size = sizes.get(level, '9pt')
                html_lines.append(
                    f'<div style="height: 4px;"></div>'
                    f'<b style="font-size: {size};">{heading_text}</b><br/>'
                )
                continue

            # Numbered lists: 1. item, 2. item
            num_match = re.match(r'^(\d+)\.\s+(.+)$', stripped)
            if num_match:
                if not in_list:
                    html_lines.append('<ul style="margin: 2px 0; padding-left: 16px;">')
                    in_list = True
                html_lines.append(f'<li>{num_match.group(2)}</li>')
                continue

            # Bullet lists
            if stripped.startswith(('\u2022', '- ', '* ')) and (
                not stripped.startswith('* ') or len(stripped) > 2
            ):
                if not in_list:
                    html_lines.append('<ul style="margin: 2px 0; padding-left: 16px;">')
                    in_list = True
                if stripped.startswith(('- ', '* ')):
                    content = stripped[2:].strip()
                else:
                    content = stripped[1:].strip()
                html_lines.append(f'<li>{content}</li>')
            else:
                if in_list:
                    html_lines.append('</ul>')
                    in_list = False
                if stripped:
                    html_lines.append(line + '<br/>')
                else:
                    html_lines.append('<div style="height: 4px;"></div>')

        if in_list:
            html_lines.append('</ul>')

        html_text = ''.join(html_lines)
        return f'<div style="color: {color}; line-height: 1.2; font-size: 9pt;">{html_text}</div>'

    # ------------------------------------------------------------------
    # Size calculation
    # ------------------------------------------------------------------

    def sizeHint(self, option: QStyleOptionViewItem, index):
        message_data = index.data(Qt.ItemDataRole.UserRole)
        if not message_data:
            return QSize(0, 0)

        role = message_data.get('role', 'system')
        message = message_data.get('content', '')

        width = option.rect.width() if option.rect.width() > 0 else 800
        max_bubble_width = int(width * self.max_bubble_width_ratio)
        font = QFont("Segoe UI", 9 if role != "system" else 8)

        if role == "system":
            text_width = int(width * 0.8) - (self.bubble_padding * 2)
            doc = QTextDocument()
            doc.setDefaultFont(font)
            doc.setHtml(self._markdown_to_html(message, "#5f6368"))
            doc.setTextWidth(text_width)
            text_height = doc.size().height()
            height = text_height + self.bubble_padding + self.padding
        else:
            text_width = (
                max_bubble_width
                - self.bubble_padding * 2
                - self.avatar_size
                - self.avatar_margin
                - self.padding
            )
            doc = QTextDocument()
            doc.setDefaultFont(font)
            doc.setHtml(self._markdown_to_html(message, "#1a1a1a"))
            doc.setTextWidth(text_width)

            text_height = doc.size().height()
            bubble_height = text_height + self.bubble_padding * 2
            height = bubble_height + self.padding

            # Space for avatar label ("You" / "Supervertaler Sidekick")
            height += self.label_height

            # Space for timestamp
            height += self.timestamp_height

            # Space for model info (assistant only, when metadata present)
            if role == "assistant" and message_data.get('metadata'):
                height += self.meta_height

        return QSize(width, int(height))

    # ------------------------------------------------------------------
    # Paint dispatch
    # ------------------------------------------------------------------

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index):
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        message_data = index.data(Qt.ItemDataRole.UserRole)
        if not message_data:
            painter.restore()
            return

        role = message_data.get('role', 'system')
        message = message_data.get('content', '')

        rect = option.rect

        if role == "user":
            self._paint_user_message(painter, rect, message, message_data)
        elif role == "assistant":
            self._paint_assistant_message(painter, rect, message, message_data)
        else:
            self._paint_system_message(painter, rect, message)

        painter.restore()

    # ------------------------------------------------------------------
    # User message
    # ------------------------------------------------------------------

    def _paint_user_message(self, painter: QPainter, rect, message: str, msg_data: dict):
        max_bubble_width = int(rect.width() * self.max_bubble_width_ratio)
        font = QFont("Segoe UI", 9)
        painter.setFont(font)

        text_width = (
            max_bubble_width
            - self.bubble_padding * 2
            - self.avatar_size
            - self.avatar_margin
            - self.padding
        )

        doc = QTextDocument()
        doc.setDefaultFont(font)
        doc.setHtml(self._markdown_to_html(message, "#1E1E1E"))
        doc.setTextWidth(text_width)

        doc_size = doc.size()
        bubble_width = min(
            doc_size.width() + self.bubble_padding * 2,
            max_bubble_width - self.avatar_size - self.avatar_margin,
        )
        bubble_height = doc_size.height() + self.bubble_padding * 2

        # Label "You" above bubble
        label_y = rect.top() + self.padding // 2
        bubble_x = rect.right() - bubble_width - self.avatar_size - self.avatar_margin - self.padding
        avatar_x = rect.right() - self.avatar_size - self.padding

        painter.setPen(QPen(QColor("#646464")))
        painter.setFont(QFont("Segoe UI", 7))
        label_rect = QRectF(bubble_x, label_y, bubble_width, self.label_height)
        painter.drawText(label_rect, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, "You")

        bubble_y = label_y + self.label_height

        # Draw bubble – light blue (Trados style)
        bubble_rect = QRectF(bubble_x, bubble_y, bubble_width, bubble_height)
        path = QPainterPath()
        path.addRoundedRect(bubble_rect, 10, 10)
        painter.fillPath(path, QBrush(QColor("#D6EBFF")))

        # Subtle border
        painter.setPen(QPen(QColor("#B4C8DC"), 1))
        painter.drawRoundedRect(bubble_rect, 10, 10)

        # Draw text
        text_draw_rect = bubble_rect.adjusted(
            self.bubble_padding, self.bubble_padding,
            -self.bubble_padding, -self.bubble_padding,
        )
        painter.save()
        painter.translate(text_draw_rect.topLeft())
        ctx = QAbstractTextDocumentLayout.PaintContext()
        doc.documentLayout().draw(painter, ctx)
        painter.restore()

        # Avatar (right side)
        avatar_rect = QRectF(avatar_x, bubble_y, self.avatar_size, self.avatar_size)
        avatar_gradient = QLinearGradient(avatar_rect.topLeft(), avatar_rect.bottomRight())
        avatar_gradient.setColorAt(0, QColor("#4A90D9"))
        avatar_gradient.setColorAt(1, QColor("#3A7BC8"))
        painter.setBrush(QBrush(avatar_gradient))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(avatar_rect)
        painter.setPen(QPen(QColor("white")))
        painter.setFont(QFont("Segoe UI Emoji", 9))
        painter.drawText(avatar_rect, Qt.AlignmentFlag.AlignCenter, "\U0001F464")

        # Timestamp below bubble
        self._draw_timestamp(painter, msg_data, bubble_rect)

    # ------------------------------------------------------------------
    # Assistant message
    # ------------------------------------------------------------------

    def _paint_assistant_message(self, painter: QPainter, rect, message: str, msg_data: dict):
        max_bubble_width = int(rect.width() * self.max_bubble_width_ratio)
        font = QFont("Segoe UI", 9)
        painter.setFont(font)

        text_width = (
            max_bubble_width
            - self.bubble_padding * 2
            - self.avatar_size
            - self.avatar_margin
            - self.padding
        )

        doc = QTextDocument()
        doc.setDefaultFont(font)
        doc.setHtml(self._markdown_to_html(message, "#1a1a1a"))
        doc.setTextWidth(text_width)

        doc_size = doc.size()
        bubble_width = min(
            doc_size.width() + self.bubble_padding * 2,
            max_bubble_width - self.avatar_size - self.avatar_margin,
        )
        bubble_height = doc_size.height() + self.bubble_padding * 2

        avatar_x = rect.left() + self.padding
        bubble_x = rect.left() + self.avatar_size + self.avatar_margin + self.padding

        # Label "Supervertaler" above bubble (Sidekick was retired in v1.10.4)
        label_y = rect.top() + self.padding // 2

        painter.setPen(QPen(QColor("#646464")))
        painter.setFont(QFont("Segoe UI", 7))
        label_rect = QRectF(bubble_x, label_y, bubble_width, self.label_height)
        painter.drawText(
            label_rect,
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            "Supervertaler",
        )

        bubble_y = label_y + self.label_height

        # Draw bubble – light grey
        bubble_rect = QRectF(bubble_x, bubble_y, bubble_width, bubble_height)
        path = QPainterPath()
        path.addRoundedRect(bubble_rect, 10, 10)
        painter.fillPath(path, QBrush(QColor("#F0F0F0")))

        # Border
        painter.setPen(QPen(QColor("#E8E8EA"), 1))
        painter.drawRoundedRect(bubble_rect, 10, 10)

        # Draw text
        text_draw_rect = bubble_rect.adjusted(
            self.bubble_padding, self.bubble_padding,
            -self.bubble_padding, -self.bubble_padding,
        )
        painter.save()
        painter.translate(text_draw_rect.topLeft())
        ctx = QAbstractTextDocumentLayout.PaintContext()
        doc.documentLayout().draw(painter, ctx)
        painter.restore()

        # Avatar (left side)
        avatar_rect = QRectF(avatar_x, bubble_y, self.avatar_size, self.avatar_size)
        painter.setBrush(QBrush(QColor("#6B7280")))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(avatar_rect)
        painter.setPen(QPen(QColor("white")))
        painter.setFont(QFont("Segoe UI", 6, QFont.Weight.Bold))
        painter.drawText(avatar_rect, Qt.AlignmentFlag.AlignCenter, "AI")

        # Timestamp
        bottom_y = self._draw_timestamp(painter, msg_data, bubble_rect)

        # Model / token / cost info
        metadata = msg_data.get('metadata')
        if metadata:
            self._draw_metadata(painter, metadata, bubble_rect, bottom_y)

    # ------------------------------------------------------------------
    # System message
    # ------------------------------------------------------------------

    def _paint_system_message(self, painter: QPainter, rect, message: str):
        font = QFont("Segoe UI", 9)
        doc = QTextDocument()
        doc.setDefaultFont(font)
        doc.setHtml(self._markdown_to_html(message, "#5f6368"))

        max_width = int(rect.width() * 0.8) - (self.bubble_padding * 2)
        doc.setTextWidth(max_width)

        text_height = doc.size().height()
        bubble_width = max_width + self.bubble_padding * 2
        bubble_height = text_height + self.bubble_padding

        bubble_x = (rect.width() - bubble_width) / 2
        bubble_y = rect.top() + self.padding // 2

        bubble_rect = QRectF(bubble_x, bubble_y, bubble_width, bubble_height)
        path = QPainterPath()
        path.addRoundedRect(bubble_rect, 16, 16)

        painter.fillPath(path, QBrush(QColor("#F8F9FA")))
        painter.setPen(QPen(QColor("#E8EAED"), 1))
        painter.drawRoundedRect(bubble_rect, 16, 16)

        text_draw_rect = bubble_rect.adjusted(
            self.bubble_padding, self.bubble_padding // 2,
            -self.bubble_padding, -self.bubble_padding // 2,
        )

        painter.save()
        painter.translate(text_draw_rect.topLeft())
        ctx = QAbstractTextDocumentLayout.PaintContext()
        doc.documentLayout().draw(painter, ctx)
        painter.restore()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _draw_timestamp(self, painter: QPainter, msg_data: dict, bubble_rect: QRectF) -> float:
        """Draw HH:mm timestamp below bubble. Returns the y position after the timestamp."""
        ts = msg_data.get('timestamp', '')
        if not ts:
            return bubble_rect.bottom() + 2

        try:
            dt = datetime.fromisoformat(ts)
            time_str = dt.strftime("%H:%M")
        except (ValueError, TypeError):
            return bubble_rect.bottom() + 2

        painter.setPen(QPen(QColor("#8C8C8C")))
        painter.setFont(QFont("Segoe UI", 7))
        ts_rect = QRectF(
            bubble_rect.right() - 40,
            bubble_rect.bottom() + 1,
            40,
            self.timestamp_height,
        )
        painter.drawText(ts_rect, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop, time_str)
        return bubble_rect.bottom() + 1 + self.timestamp_height

    def _draw_metadata(self, painter: QPainter, metadata: dict, bubble_rect: QRectF, y: float):
        """Draw model/token/cost info line below the timestamp."""
        parts = []
        model = metadata.get('model', '')
        if model:
            parts.append(model)
        tok_in = metadata.get('tokens_in', 0)
        tok_out = metadata.get('tokens_out', 0)
        if tok_in or tok_out:
            parts.append(f"{tok_in:,} in / {tok_out:,} out")
        # cost_usd: float for known cost, 0.0 for genuinely free (Ollama),
        # None for unknown (model not in pricing table). Distinguishing
        # "free" from "unknown" prevents users mistaking a non-curated
        # OpenRouter model (e.g. deepseek/deepseek-v4-pro) for free.
        cost = metadata.get('cost_usd', 0)
        if cost is None:
            parts.append("cost unknown")
        elif cost:
            parts.append(f"~${cost:.2f}")
        duration = metadata.get('duration_s', 0)
        if duration:
            parts.append(f"{duration:.1f}s")

        if not parts:
            return

        info_text = " \u2022 ".join(parts)

        painter.setPen(QPen(QColor("#8C8C8C")))
        painter.setFont(QFont("Segoe UI", 7))
        meta_rect = QRectF(
            bubble_rect.left(),
            y,
            bubble_rect.width(),
            self.meta_height,
        )
        painter.drawText(
            meta_rect,
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
            info_text,
        )
