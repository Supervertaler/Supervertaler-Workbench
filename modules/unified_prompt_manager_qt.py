"""
Unified Prompt Manager Module - Qt Edition (powers the AI tab)
Simplified 2-Layer Architecture:

1. System Prompts (in Settings) - mode-specific, auto-selected based on document type
2. Prompt Manager (main UI) - unified workspace with folders, multi-attach

This replaces the old 4-layer system (System/Domain/Project/Style Guides).
"""

import os
import re
import json
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Optional
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTreeWidget, QTreeWidgetItem,
    QTextEdit, QPlainTextEdit, QSplitter, QGroupBox, QMessageBox, QFileDialog,
    QInputDialog, QLineEdit, QFrame, QMenu, QCheckBox, QSizePolicy, QScrollArea, QTabWidget,
    QListWidget, QListWidgetItem, QStyledItemDelegate, QStyleOptionViewItem, QApplication, QDialog,
    QAbstractItemView, QTableWidget, QTableWidgetItem, QHeaderView,
    # v1.10.157: AutoPrompt progress + save dialogs
    QProgressDialog, QFormLayout, QComboBox, QDialogButtonBox,
)
from PyQt6.QtCore import Qt, QSettings, pyqtSignal, QThread, QSize, QRect, QRectF
from PyQt6.QtGui import QFont, QColor, QAction, QIcon, QPainter, QPen, QBrush, QPainterPath, QLinearGradient

from modules.unified_prompt_library import UnifiedPromptLibrary
from modules.llm_clients import LLMClient, load_api_keys
from modules.prompt_library_migration import migrate_prompt_library
from modules.ai_attachment_manager import AttachmentManager
from modules.ai_file_viewer_dialog import FileViewerDialog, FileRemoveConfirmDialog
from modules.ai_actions import AIActionSystem
from modules.shortcut_display import format_shortcut_for_display
from modules.document_analyzer import DocumentAnalyzer
from modules.chat_backend import ChatBackend
from modules.chat_view_widget import ChatViewWidget


def _term_contains_cjk(text: str) -> bool:
    """True if any character is in a space-less script (Chinese, Japanese,
    Korean, Thai), so term matching must use substring rather than word
    boundaries (mirrors the term-recognition logic in Supervertaler.py)."""
    if not text:
        return False
    for ch in text:
        o = ord(ch)
        if (0x4E00 <= o <= 0x9FFF or 0x3400 <= o <= 0x4DBF or
                0x3040 <= o <= 0x30FF or 0xAC00 <= o <= 0xD7AF or
                0x0E00 <= o <= 0x0E7F or 0xF900 <= o <= 0xFAFF or
                0x20000 <= o <= 0x2FA1F):
            return True
    return False


def filter_relevant_glossary_terms(glossary_terms: list, source_text: str) -> list:
    """Keep only the glossary terms whose source term actually appears in the
    segment being translated, so the prompt isn't crowded with terminology
    irrelevant to this segment (which dilutes the AI and costs tokens). Mirrors
    the relevance filter in Supervertaler for Trados (PromptGenerator
    .FilterRelevantTerms): whole-word/phrase match for spaced languages,
    substring match for CJK/Thai, and single-character Latin terms are skipped
    (a one-letter abbreviation would match stray lone letters in the text)."""
    if not glossary_terms:
        return glossary_terms or []
    if not source_text:
        return []

    text_upper = source_text.upper()
    relevant = []
    for term in glossary_terms:
        st = (term.get('source_term') or '').strip()
        if not st:
            continue
        is_cjk = _term_contains_cjk(st)
        if not is_cjk and len(st) < 2:
            continue  # lone Latin letters match incidental characters
        st_upper = st.upper()
        if is_cjk:
            if st_upper in text_upper:
                relevant.append(term)
        else:
            try:
                if re.search(r'\b' + re.escape(st_upper) + r'\b', text_upper):
                    relevant.append(term)
            except re.error:
                if st_upper in text_upper:
                    relevant.append(term)
    return relevant


# ============================================================================
# AutoPrompt: worker thread + save dialog (v1.10.157)
# ============================================================================
# The AutoPrompt button used to call chat_backend.send_ai_request directly on
# the main thread. That call is synchronous and I/O-bound — with
# reasoning-capable models (Opus 4.8, GPT-5 etc.) it can take 1-3 minutes,
# during which the whole window froze and Windows put up "Not Responding".
# The worker class below moves that call off the main thread so the progress
# dialog stays responsive (and the user can cancel without killing the app).
#
# The save dialog (run after the LLM responds) gives the user a chance to
# name the prompt and pick a folder before the file is written, instead of
# the previous silent auto-save into a hard-coded folder.
# ============================================================================


class _AutoPromptWorker(QThread):
    """Background worker that runs an AutoPrompt LLM call off the main thread.

    Emits ``finished_ok(response_text, metadata)`` on success or
    ``failed(error_message)`` on exception. There's no real way to abort an
    in-flight HTTP request to most LLM providers from the outside, so the
    "Cancel" button on the progress dialog detaches the signal connections
    rather than killing the thread — the call runs to completion but its
    result is ignored.
    """

    finished_ok = pyqtSignal(str, dict)
    failed = pyqtSignal(str)

    def __init__(self, chat_backend, prompt: str, system_prompt: str,
                 images=None):
        super().__init__()
        self._chat_backend = chat_backend
        self._prompt = prompt
        self._system_prompt = system_prompt
        # v1.10.178: optional list of figure images to ship with the
        # AutoPrompt request when the user has opted into vision-aware
        # analysis. None / empty list means text-only as before.
        self._images = images

    def run(self):
        try:
            response_text, metadata = self._chat_backend.send_ai_request(
                self._prompt, self._system_prompt, is_analysis=True,
                images=self._images
            )
            self.finished_ok.emit(response_text or "", metadata or {})
        except Exception as e:
            import traceback
            self.failed.emit(f"{e}\n\n{traceback.format_exc()}")


class _AutoPromptSaveDialog(QDialog):
    """Dialog shown after AutoPrompt generation completes.

    Lets the user choose the prompt's name and folder before the file is
    written, with a preview of the generated content above. Pre-fills name
    with the current project name (falling back to the auto-detected
    domain pattern) and folder with "Translate" (falling back to the
    first available folder).
    """

    def __init__(self, generated_content: str, suggested_name: str,
                 available_folders: list, default_folder: str,
                 parent=None):
        super().__init__(parent)
        self.setWindowTitle("Save AutoPrompt")
        self.setMinimumWidth(640)
        self.setMinimumHeight(480)

        layout = QVBoxLayout(self)

        # Preview pane (read-only, monospace) so the user can see what was
        # generated before committing to a name/folder.
        layout.addWidget(QLabel("<b>Generated prompt preview:</b>"))
        preview = QTextEdit()
        preview.setPlainText(generated_content)
        preview.setReadOnly(True)
        mono = QFont("Consolas", 9)
        preview.setFont(mono)
        preview.setMinimumHeight(240)
        layout.addWidget(preview, 1)

        # Name + folder fields
        form = QFormLayout()

        self.name_edit = QLineEdit(suggested_name)
        self.name_edit.selectAll()
        form.addRow("Name:", self.name_edit)

        # Folder dropdown is editable so the user can type a new folder name
        # that doesn't exist yet — _action_create_prompt creates intermediate
        # directories on save.
        self.folder_combo = QComboBox()
        self.folder_combo.setEditable(True)
        self.folder_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        for f in available_folders:
            self.folder_combo.addItem(f)
        # Set default — prefer exact match, else fall back to setting raw text.
        idx = self.folder_combo.findText(default_folder)
        if idx >= 0:
            self.folder_combo.setCurrentIndex(idx)
        else:
            self.folder_combo.setEditText(default_folder)
        form.addRow("Folder:", self.folder_combo)

        layout.addLayout(form)

        # Save / Cancel
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Save).setDefault(True)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def get_name(self) -> str:
        return self.name_edit.text().strip()

    def get_folder(self) -> str:
        return self.folder_combo.currentText().strip()


class _AutoPromptContextDialog(QDialog):
    """Dialog shown BEFORE AutoPrompt generation, so the translator can
    steer the result.

    Document-domain detection is automatic and keyword-based, so it
    occasionally guesses wrong (e.g. a creative text with a stray "Fig. 1"
    and "comprising" getting read as a patent). This dialog surfaces the
    auto-detected domain and lets the translator:

      * confirm it, or pick a different domain from the dropdown, and
      * add a short free-text briefing (e.g. "Marketing copy for a watch
        brand, playful tone") that is passed to the AI as authoritative
        context for the generated prompt.

    Returns via get_domain() / get_context_hint(); the caller treats a
    rejected dialog as "cancel generation".
    """

    # Keep in sync with DOMAIN_TEMPLATES keys. Ordered general-first since
    # that's the safe fallback the user is most likely to reach for when
    # correcting an over-specific auto-guess.
    _DOMAINS = ['general', 'patent', 'legal', 'medical',
                'technical', 'financial', 'marketing']

    def __init__(self, detected_domain: str, description: str = "",
                 parent=None):
        super().__init__(parent)
        self.setWindowTitle("AutoPrompt – confirm context")
        self.setMinimumWidth(520)

        layout = QVBoxLayout(self)

        intro = QLabel(
            "Supervertaler had the AI read the document and detect the "
            "context below. Confirm it, or correct it and add a short "
            "briefing so the generated prompt matches your document."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        detected = detected_domain if detected_domain else "general"
        summary = QLabel(
            f"<b>Detected context:</b> {detected.title()}"
            + (f" &nbsp;– {description}" if description else "")
        )
        summary.setTextFormat(Qt.TextFormat.RichText)
        summary.setWordWrap(True)
        layout.addWidget(summary)

        form = QFormLayout()

        # Domain override. Pre-select the detected domain; if it's somehow
        # not in the known list, fall back to 'general' so the combo is
        # never empty.
        self.domain_combo = QComboBox()
        for d in self._DOMAINS:
            self.domain_combo.addItem(d.title(), d)
        idx = self.domain_combo.findData(detected.lower())
        self.domain_combo.setCurrentIndex(idx if idx >= 0 else 0)
        form.addRow("Domain:", self.domain_combo)

        layout.addLayout(form)

        layout.addWidget(QLabel("Context briefing (optional):"))
        self.hint_edit = QPlainTextEdit()
        self.hint_edit.setPlaceholderText(
            "e.g. Creative marketing copy for a watch brand – playful, "
            "not technical. Keep product names untranslated."
        )
        self.hint_edit.setMaximumHeight(90)
        layout.addWidget(self.hint_edit)

        # Generate / Cancel. Default to Generate so pressing Enter after a
        # quick confirmation just proceeds.
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        ok_btn = buttons.button(QDialogButtonBox.StandardButton.Ok)
        ok_btn.setText("Generate")
        ok_btn.setDefault(True)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def get_domain(self) -> str:
        return self.domain_combo.currentData()

    def get_context_hint(self) -> str:
        return self.hint_edit.toPlainText().strip()


# Language code → full name mapping (matches Supervertaler.py available_langs)
_LANG_CODE_TO_NAME = {
    "af": "Afrikaans", "sq": "Albanian", "ar": "Arabic", "hy": "Armenian",
    "eu": "Basque", "bn": "Bengali", "bg": "Bulgarian", "ca": "Catalan",
    "zh-cn": "Chinese (Simplified)", "zh-tw": "Chinese (Traditional)",
    "hr": "Croatian", "cs": "Czech", "da": "Danish", "nl": "Dutch",
    "en": "English", "et": "Estonian", "fi": "Finnish", "fr": "French",
    "gl": "Galician", "ka": "Georgian", "de": "German", "el": "Greek",
    "he": "Hebrew", "hi": "Hindi", "hu": "Hungarian", "is": "Icelandic",
    "id": "Indonesian", "ga": "Irish", "it": "Italian", "ja": "Japanese",
    "ko": "Korean", "lv": "Latvian", "lt": "Lithuanian", "mk": "Macedonian",
    "ms": "Malay", "no": "Norwegian", "nb": "Norwegian", "fa": "Persian",
    "pl": "Polish", "pt": "Portuguese", "ro": "Romanian", "ru": "Russian",
    "sr": "Serbian", "sk": "Slovak", "sl": "Slovenian", "es": "Spanish",
    "sw": "Swahili", "sv": "Swedish", "th": "Thai", "tr": "Turkish",
    "uk": "Ukrainian", "ur": "Urdu", "vi": "Vietnamese", "cy": "Welsh",
}


def _resolve_lang_name(code_or_name):
    """Convert a language code to its full name; pass through if already a name."""
    if not code_or_name:
        return code_or_name
    return _LANG_CODE_TO_NAME.get(code_or_name.lower().strip(), code_or_name)


from modules.styled_widgets import CheckmarkCheckBox, HelpButton  # noqa: E402
from modules.help_system import Topics as HelpTopics  # noqa: E402


class PromptLibraryTreeWidget(QTreeWidget):
    """Tree widget that supports drag-and-drop moves for prompt files."""

    def __init__(self, manager, parent=None):
        super().__init__(parent)
        self._manager = manager

        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)

    def dropEvent(self, event):
        """Handle prompt/folder moves via filesystem operations."""
        try:
            selected = self.selectedItems()
            if not selected:
                event.ignore()
                return

            source_item = selected[0]
            source_data = source_item.data(0, Qt.ItemDataRole.UserRole)
            if not source_data:
                event.ignore()
                return

            src_type = source_data.get('type')
            src_path = source_data.get('path')
            if src_type not in {'prompt', 'folder'} or not src_path:
                event.ignore()
                return

            pos = event.position().toPoint() if hasattr(event, 'position') else event.pos()
            target_item = self.itemAt(pos)
            target_data = target_item.data(0, Qt.ItemDataRole.UserRole) if target_item else None

            # Determine destination folder.
            dest_folder = ''
            if target_data:
                if target_data.get('type') == 'folder':
                    dest_folder = target_data.get('path', '')
                elif target_data.get('type') == 'prompt':
                    dest_folder = str(Path(target_data.get('path', '')).parent)
                    if dest_folder == '.':
                        dest_folder = ''
                else:
                    # Special nodes like Quick Run: ignore.
                    event.ignore()
                    return

            moved = False
            if src_type == 'prompt' and self._manager and hasattr(self._manager, '_move_prompt_to_folder'):
                moved = self._manager._move_prompt_to_folder(src_path, dest_folder)

            if src_type == 'folder' and self._manager and hasattr(self._manager, '_move_folder_to_folder'):
                moved = self._manager._move_folder_to_folder(src_path, dest_folder)

            if moved:
                event.acceptProposedAction()
            else:
                event.ignore()

        except Exception:
            event.ignore()
            return


class ChatMessageDelegate(QStyledItemDelegate):
    """Custom delegate for rendering chat messages with proper bubble styling"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.padding = 4
        self.bubble_padding = 6
        self.avatar_size = 18
        self.avatar_margin = 4
        self.max_bubble_width_ratio = 0.85  # 85% of available width

    def _markdown_to_html(self, text: str, color: str = "#1a1a1a") -> str:
        """Convert simple markdown to HTML for rich text rendering"""
        import re

        # Escape HTML special characters first
        text = text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')

        # Convert markdown to HTML
        # Bold: **text** or __text__
        text = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', text)
        text = re.sub(r'__(.+?)__', r'<b>\1</b>', text)

        # Italic: *text* or _text_
        text = re.sub(r'\*(.+?)\*', r'<i>\1</i>', text)
        text = re.sub(r'_(.+?)_', r'<i>\1</i>', text)

        # Code: `code`
        text = re.sub(r'`(.+?)`', r'<code style="background-color: #f0f0f0; padding: 2px 4px; border-radius: 3px; font-family: Consolas, monospace;">\1</code>', text)

        # Bullet points: lines starting with • or - or *
        lines = text.split('\n')
        html_lines = []
        in_list = False

        for line in lines:
            stripped = line.strip()
            if stripped.startswith('•') or stripped.startswith('- ') or (stripped.startswith('* ') and len(stripped) > 2):
                if not in_list:
                    html_lines.append('<ul style="margin: 2px 0; padding-left: 16px;">')
                    in_list = True
                content = stripped[2:].strip() if stripped.startswith('- ') or stripped.startswith('* ') else stripped[1:].strip()
                html_lines.append(f'<li>{content}</li>')
            else:
                if in_list:
                    html_lines.append('</ul>')
                    in_list = False
                if stripped:
                    html_lines.append(line + '<br/>')
                else:
                    # Empty line = paragraph break – use small spacer instead of full line break
                    html_lines.append('<div style="height: 4px;"></div>')

        if in_list:
            html_lines.append('</ul>')

        html_text = ''.join(html_lines)

        # Wrap in styled div
        return f'<div style="color: {color}; line-height: 1.2; font-size: 9pt;">{html_text}</div>'

    def sizeHint(self, option: QStyleOptionViewItem, index):
        """Calculate size needed for this message"""
        from PyQt6.QtGui import QTextDocument

        message_data = index.data(Qt.ItemDataRole.UserRole)
        if not message_data:
            return QSize(0, 0)

        role = message_data.get('role', 'system')
        message = message_data.get('content', '')

        # Calculate text width
        width = option.rect.width() if option.rect.width() > 0 else 800
        max_bubble_width = int(width * self.max_bubble_width_ratio)

        font = QFont("Segoe UI", 9 if role != "system" else 8)

        if role == "system":
            # System messages are centered and smaller (with markdown formatting)
            text_width = int(width * 0.8) - (self.bubble_padding * 2)

            # Use QTextDocument to measure height with markdown
            doc = QTextDocument()
            doc.setDefaultFont(font)
            doc.setHtml(self._markdown_to_html(message, "#5f6368"))
            doc.setTextWidth(text_width)

            text_height = doc.size().height()
            height = text_height + self.bubble_padding + self.padding
        else:
            # User/assistant messages - use QTextDocument for accurate height with markdown
            text_width = max_bubble_width - (self.bubble_padding * 2) - self.avatar_size - self.avatar_margin - self.padding

            # Create text document to measure actual rendered height
            doc = QTextDocument()
            doc.setDefaultFont(font)
            doc.setHtml(self._markdown_to_html(message, "#1a1a1a"))
            doc.setTextWidth(text_width)

            # Get actual document height
            text_height = doc.size().height()
            bubble_height = text_height + self.bubble_padding * 2
            height = bubble_height + self.padding

        return QSize(width, int(height))

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index):
        """Paint the chat message bubble"""
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
            self._paint_user_message(painter, rect, message)
        elif role == "assistant":
            self._paint_assistant_message(painter, rect, message)
        else:  # system
            self._paint_system_message(painter, rect, message)

        painter.restore()

    def _paint_user_message(self, painter: QPainter, rect: QRect, message: str):
        """Paint user message (right-aligned, blue gradient)"""
        from PyQt6.QtGui import QTextDocument

        # Calculate dimensions
        max_bubble_width = int(rect.width() * self.max_bubble_width_ratio)

        # Calculate text size using QTextDocument for accurate height
        font = QFont("Segoe UI", 9)
        painter.setFont(font)

        text_width = max_bubble_width - (self.bubble_padding * 2) - self.avatar_size - self.avatar_margin - self.padding

        # Create text document to measure actual rendered size
        doc = QTextDocument()
        doc.setDefaultFont(font)
        doc.setHtml(self._markdown_to_html(message, "white"))
        doc.setTextWidth(text_width)

        # Get actual document size
        doc_size = doc.size()
        bubble_width = min(doc_size.width() + self.bubble_padding * 2, max_bubble_width - self.avatar_size - self.avatar_margin)
        bubble_height = doc_size.height() + self.bubble_padding * 2

        # Position bubble on right side (leaving room for avatar)
        bubble_x = rect.right() - bubble_width - self.avatar_size - self.avatar_margin - self.padding
        bubble_y = rect.top() + self.padding // 2

        # Draw bubble with gradient
        bubble_rect = QRectF(bubble_x, bubble_y, bubble_width, bubble_height)
        path = QPainterPath()
        path.addRoundedRect(bubble_rect, 10, 10)

        # Supervertaler blue gradient
        gradient = QLinearGradient(bubble_rect.topLeft(), bubble_rect.bottomRight())
        gradient.setColorAt(0, QColor("#5D7BFF"))
        gradient.setColorAt(1, QColor("#4F6FFF"))

        painter.fillPath(path, QBrush(gradient))

        # Draw shadow
        painter.setPen(QPen(QColor(93, 123, 255, 76), 0))
        painter.drawRoundedRect(bubble_rect.adjusted(0, 2, 0, 2), 18, 18)

        # Draw text with markdown formatting (reuse doc from above)
        from PyQt6.QtGui import QAbstractTextDocumentLayout
        text_draw_rect = bubble_rect.adjusted(
            self.bubble_padding, self.bubble_padding,
            -self.bubble_padding, -self.bubble_padding
        )

        # Translate painter to text position and draw
        painter.save()
        painter.translate(text_draw_rect.topLeft())
        ctx = QAbstractTextDocumentLayout.PaintContext()
        doc.documentLayout().draw(painter, ctx)
        painter.restore()

        # Draw avatar (right side)
        avatar_x = rect.right() - self.avatar_size - self.padding
        avatar_y = bubble_y
        avatar_rect = QRectF(avatar_x, avatar_y, self.avatar_size, self.avatar_size)

        # Avatar gradient background
        avatar_gradient = QLinearGradient(avatar_rect.topLeft(), avatar_rect.bottomRight())
        avatar_gradient.setColorAt(0, QColor("#667eea"))
        avatar_gradient.setColorAt(1, QColor("#764ba2"))

        painter.setBrush(QBrush(avatar_gradient))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(avatar_rect)

        # Draw avatar emoji
        painter.setPen(QPen(QColor("white")))
        painter.setFont(QFont("Segoe UI Emoji", 9))
        painter.drawText(avatar_rect, Qt.AlignmentFlag.AlignCenter, "👤")

    def _paint_assistant_message(self, painter: QPainter, rect: QRect, message: str):
        """Paint assistant message (left-aligned, gray)"""
        from PyQt6.QtGui import QTextDocument

        # Calculate dimensions
        max_bubble_width = int(rect.width() * self.max_bubble_width_ratio)

        # Calculate text size using QTextDocument for accurate height
        font = QFont("Segoe UI", 9)
        painter.setFont(font)

        text_width = max_bubble_width - (self.bubble_padding * 2) - self.avatar_size - self.avatar_margin - self.padding

        # Create text document to measure actual rendered size
        doc = QTextDocument()
        doc.setDefaultFont(font)
        doc.setHtml(self._markdown_to_html(message, "#1a1a1a"))
        doc.setTextWidth(text_width)

        # Get actual document size
        doc_size = doc.size()
        bubble_width = min(doc_size.width() + self.bubble_padding * 2, max_bubble_width - self.avatar_size - self.avatar_margin)
        bubble_height = doc_size.height() + self.bubble_padding * 2

        # Position bubble on left side (leaving room for avatar)
        bubble_x = rect.left() + self.avatar_size + self.avatar_margin + self.padding
        bubble_y = rect.top() + self.padding // 2

        # Draw bubble
        bubble_rect = QRectF(bubble_x, bubble_y, bubble_width, bubble_height)
        path = QPainterPath()
        path.addRoundedRect(bubble_rect, 10, 10)

        painter.fillPath(path, QBrush(QColor("#F5F5F7")))

        # Draw border
        painter.setPen(QPen(QColor("#E8E8EA"), 1))
        painter.drawRoundedRect(bubble_rect, 10, 10)

        # Draw shadow
        painter.setPen(QPen(QColor(0, 0, 0, 20), 0))
        painter.drawRoundedRect(bubble_rect.adjusted(0, 2, 0, 2), 18, 18)

        # Draw text with markdown formatting (reuse doc from above)
        from PyQt6.QtGui import QAbstractTextDocumentLayout
        text_draw_rect = bubble_rect.adjusted(
            self.bubble_padding, self.bubble_padding,
            -self.bubble_padding, -self.bubble_padding
        )

        # Translate painter to text position and draw
        painter.save()
        painter.translate(text_draw_rect.topLeft())
        ctx = QAbstractTextDocumentLayout.PaintContext()
        doc.documentLayout().draw(painter, ctx)
        painter.restore()

        # Draw avatar (left side)
        avatar_x = rect.left() + self.padding
        avatar_y = bubble_y
        avatar_rect = QRectF(avatar_x, avatar_y, self.avatar_size, self.avatar_size)

        # Avatar gradient background
        avatar_gradient = QLinearGradient(avatar_rect.topLeft(), avatar_rect.bottomRight())
        avatar_gradient.setColorAt(0, QColor("#667eea"))
        avatar_gradient.setColorAt(1, QColor("#764ba2"))

        painter.setBrush(QBrush(avatar_gradient))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(avatar_rect)

        # Draw avatar emoji
        painter.setPen(QPen(QColor("white")))
        painter.setFont(QFont("Segoe UI Emoji", 15))
        painter.drawText(avatar_rect, Qt.AlignmentFlag.AlignCenter, "🤖")

    def _paint_system_message(self, painter: QPainter, rect: QRect, message: str):
        """Paint system message (centered, subtle, with markdown formatting)"""
        from PyQt6.QtGui import QTextDocument, QAbstractTextDocumentLayout

        # Create text document with markdown converted to HTML
        font = QFont("Segoe UI", 9)
        doc = QTextDocument()
        doc.setDefaultFont(font)
        doc.setHtml(self._markdown_to_html(message, "#5f6368"))

        # Set max width (80% of available width)
        max_width = int(rect.width() * 0.8) - (self.bubble_padding * 2)
        doc.setTextWidth(max_width)

        # Calculate bubble dimensions
        text_height = doc.size().height()
        bubble_width = max_width + self.bubble_padding * 2
        bubble_height = text_height + self.bubble_padding

        # Center horizontally
        bubble_x = (rect.width() - bubble_width) / 2
        bubble_y = rect.top() + self.padding // 2

        # Draw bubble
        bubble_rect = QRectF(bubble_x, bubble_y, bubble_width, bubble_height)
        path = QPainterPath()
        path.addRoundedRect(bubble_rect, 16, 16)

        painter.fillPath(path, QBrush(QColor("#F8F9FA")))

        # Draw border
        painter.setPen(QPen(QColor("#E8EAED"), 1))
        painter.drawRoundedRect(bubble_rect, 16, 16)

        # Draw text with markdown formatting
        text_draw_rect = bubble_rect.adjusted(
            self.bubble_padding, self.bubble_padding // 2,
            -self.bubble_padding, -self.bubble_padding // 2
        )

        # Translate painter to text position and draw
        painter.save()
        painter.translate(text_draw_rect.topLeft())
        ctx = QAbstractTextDocumentLayout.PaintContext()
        doc.documentLayout().draw(painter, ctx)
        painter.restore()


# Domain-specific templates for AI prompt generation
# Each domain defines the role, rules, mandatory sections, and special instructions
# that the LLM must include when generating a translation prompt for that domain.
DOMAIN_TEMPLATES = {
    'patent': {
        'role': (
            'Senior patent translator specializing in intellectual property, '
            'patent prosecution, and technical patent documentation. '
            'Deep expertise in EPO/PCT filings, claim drafting conventions, '
            'and mechanical/electromechanical/chemical patent terminology.'
        ),
        'rules': [
            'Translate claims exactly, preserving dependency chains (independent/dependent claim relationships)',
            'Maintain patent-specific open-ended language: "comprising" (open-ended, from "omvattende"), never "consisting of" unless source explicitly uses limiting language',
            'Preserve all reference numerals, figure references (Fig. 1, Figure 2A), and part numbers exactly as written',
            'Never paraphrase, simplify, or improve source text – patents require exact semantic equivalence',
            'Preserve formal patent register: "wherein", "thereof", "hereinafter", "person skilled in the art"',
            'Maintain claim numbering, cross-references, and dependency structure without alteration',
            'Use gerund constructions naturally: "An example is replacing..." NOT "An example is the replacing of..."',
            'Preserve all prior art document references verbatim (e.g., US 20130183090, EP 2923344)',
            'Maintain the hierarchical structure: TECHNICAL FIELD > PRIOR ART > SUMMARY > DRAWINGS > DETAILED DESCRIPTION > CLAIMS > ABSTRACT',
            'When source is long, repetitive, or awkward, reproduce it faithfully – every word in a patent is legally operative',
        ],
        'sections': [
            'ROLE (senior patent translator with specific expertise areas)',
            'SCOPE OF APPLICATION (project context: invention type, technology field, patent number if known)',
            'TRANSLATION MANDATE (NON-NEGOTIABLE) – pure translation only, explicitly forbid improvement, simplification, harmonization, correction, streamlining',
            'HARD CONSTRAINT: NO HALLUCINATED TRUNCATION – never omit repetitive phrases, collapse clauses, shorten lists, simplify enumerations, or "fix" grammar',
            'CORE EXECUTION PRINCIPLES – with ABSOLUTE REQUIREMENTS (checkmarks) and ABSOLUTE PROHIBITIONS (crosses)',
            'SUPERVERTALER INPUT HANDLING – batched segment delivery: translate every delivered segment, keep count/order/boundaries aligned, use only in-batch context',
            'TRANSLATION STYLE (LOCKED) – mandatory term mappings (omvattende>comprising, waarbij>wherein, met het kenmerk dat>characterized in that, conclusie>claim, stand der techniek>prior art, uitvoeringsvorm>embodiment, bij voorkeur>preferably, inrichting>device, werkwijze>method)',
            'CLAIM TRANSLATION STYLE – preserve dependency structure, maintain "according to any one of the preceding claims" phrasing, avoid stylistic smoothing',
            'GERUND STYLE RULE – prefer natural English gerund over "the [verb]ing of" construction',
            'TERMINOLOGY CONSISTENCY HIERARCHY – (1) Previous correct translations, (2) Project-specific termbase, (3) General mandatory mappings',
            'TECHNICAL AND MECHANICAL FORMATTING RULES – dimensions, figure refs, prior art numbers, sensor designations, standard abbreviations',
            'PREFLIGHT SELF-CHECK (MANDATORY) – verify every word translated, no compression, all values intact, all references intact, no claim restructuring',
            'POST-TRANSLATION INTEGRITY ASSERTION (MANDATORY) – assert completeness, literalness, structural faithfulness',
            'PROJECT CONTEXT (for model understanding only – do not output) – comprehensive invention description',
            'PROJECT-SPECIFIC TERMBASE (MANDATORY, LOCKED) – all terms organized by category',
            'PREVIOUS CORRECT TRANSLATIONS – validated TM pairs as style anchors',
            'OUTPUT FORMAT – translation only, preserve line breaks, no markdown, no commentary, UTF-8',
        ],
        'special': (
            'Patent translation demands ABSOLUTE fidelity. Every word, repetition, structure, '
            'dimension, and cross-reference is legally operative. Deviation from literal structure '
            'constitutes a critical error. If the Dutch text is long, repetitive, or awkward, '
            'reproduce it faithfully in English.'
        ),
    },
    'legal': {
        'role': (
            'Senior legal translator specializing in comparative law, contract law, '
            'corporate law, and cross-jurisdictional legal translation. '
            'Deep expertise in civil law and common law systems, notarial acts, and regulatory texts.'
        ),
        'rules': [
            'Maintain exact legal terminology – never substitute informal equivalents',
            'Preserve legal entity types and abbreviations (BV, NV, GmbH, Ltd, Inc., SA, SARL) without translation',
            'Maintain "Meester" + surname format for Belgian/Dutch notaries',
            'Preserve statutory references, article numbers, and legal citations exactly as written',
            'Maintain formal legal register: "hereby", "pursuant to", "notwithstanding", "whereas"',
            'Preserve all dates, deadlines, and procedural time limits without alteration',
            'Distinguish between common law and civil law terminology as appropriate for the target jurisdiction',
            'Preserve Latin legal terms (bona fide, inter alia, prima facie) unless target convention replaces them',
            'Never translate proper names of laws, statutes, or regulations – retain original with optional translation in parentheses',
            'Maintain contractual numbering, clause references, and article structure exactly',
        ],
        'sections': [
            'ROLE (senior legal translator with jurisdiction expertise)',
            'LEGAL FRAMEWORK (jurisdiction, legal system type, document type)',
            'TRANSLATION MANDATE (NON-NEGOTIABLE) – faithful legal translation, no interpretation or simplification',
            'HARD CONSTRAINT: NO HALLUCINATED TRUNCATION – every clause, proviso, and exception is legally operative',
            'CORE EXECUTION PRINCIPLES – absolute requirements and prohibitions',
            'LEGAL REGISTER REQUIREMENTS – formality, precision, no colloquial language',
            'LEGAL ENTITY AND TITLE HANDLING – preservation rules for entities, titles, proper names',
            'STATUTORY REFERENCE PRESERVATION – article numbers, law names, citations',
            'TERMINOLOGY CONSISTENCY HIERARCHY – (1) Previous correct translations, (2) Project termbase, (3) Domain conventions',
            'NUMBER, DATE & LOCALISATION RULES – date formats, currency, number formatting',
            'PREFLIGHT SELF-CHECK (MANDATORY)',
            'PROJECT CONTEXT – document type, parties, jurisdiction, subject matter',
            'PROJECT-SPECIFIC TERMBASE (MANDATORY, LOCKED)',
            'PREVIOUS CORRECT TRANSLATIONS',
            'OUTPUT FORMAT',
        ],
        'special': (
            'Legal translation demands EXACT fidelity. Every clause, proviso, condition, '
            'and exception carries legal weight. Never simplify, merge, or "improve" legal drafting. '
            'Ambiguity in the source must be preserved as ambiguity in the target.'
        ),
    },
    'medical': {
        'role': (
            'Senior medical translator specializing in clinical documentation, '
            'pharmaceutical texts, regulatory submissions, and medical device documentation. '
            'Deep expertise in pharmacology, clinical trials, and medical terminology standards.'
        ),
        'rules': [
            'Use INN (International Nonproprietary Names) for drug names unless source uses brand names',
            'Preserve all dosages, measurements, and units exactly (mg, ml, IU, mmol/L)',
            'Maintain ICD codes, ATC codes, and clinical classification numbers verbatim',
            'Never alter, omit, or simplify safety warnings, contraindications, or adverse effects',
            'Use target-language anatomical nomenclature (Terminologia Anatomica standard)',
            'Preserve all clinical trial identifiers, study numbers, and regulatory references',
            'Maintain distinction between generic and brand drug names as used in source',
            'Preserve all statistical values, confidence intervals, and p-values exactly',
        ],
        'sections': [
            'ROLE (senior medical translator with clinical and regulatory expertise)',
            'CLINICAL CONTEXT (document type, therapeutic area, regulatory framework)',
            'TRANSLATION MANDATE (NON-NEGOTIABLE) – patient safety paramount, faithful translation',
            'HARD CONSTRAINT: NO HALLUCINATED TRUNCATION – every dosage, warning, and specification is safety-critical',
            'CORE EXECUTION PRINCIPLES – absolute requirements and prohibitions',
            'PHARMACOLOGICAL TERM HANDLING – drug names, dosages, routes of administration',
            'ANATOMICAL NOMENCLATURE RULES – standardized anatomical terminology',
            'DOSAGE AND MEASUREMENT PRESERVATION – exact reproduction of all numerical medical data',
            'SAFETY-CRITICAL CONTENT RULES – warnings, contraindications, adverse effects must be complete',
            'TERMINOLOGY CONSISTENCY HIERARCHY',
            'PREFLIGHT SELF-CHECK (SAFETY-FOCUSED) – verify all dosages, warnings, and measurements intact',
            'PROJECT CONTEXT – document type, therapeutic area, patient population',
            'PROJECT-SPECIFIC TERMBASE (MANDATORY, LOCKED)',
            'PREVIOUS CORRECT TRANSLATIONS',
            'OUTPUT FORMAT',
        ],
        'special': (
            'Medical translation is SAFETY-CRITICAL. Any error in dosages, warnings, '
            'contraindications, or drug names could directly harm patients. Double-check all '
            'numerical values and safety-related content.'
        ),
    },
    'technical': {
        'role': (
            'Senior technical translator specializing in engineering documentation, '
            'IT/software localization, and industrial/manufacturing texts. '
            'Deep expertise in technical specifications, user documentation, and standards.'
        ),
        'rules': [
            'Preserve all technical specifications, model numbers, and part references exactly',
            'Maintain consistent terminology for UI elements, menu items, and software terms',
            'Preserve code snippets, file paths, command syntax, and API names without translation',
            'Maintain measurement units as specified – do not convert unless explicitly required',
            'Preserve camelCase, snake_case, and PascalCase identifiers verbatim',
            'Maintain the distinction between similar technical terms (do not conflate related but distinct concepts)',
        ],
        'sections': [
            'ROLE (senior technical translator with domain expertise)',
            'TECHNICAL DOMAIN (field, technology, product/system)',
            'TRANSLATION MANDATE (NON-NEGOTIABLE) – precise technical translation, no interpretation',
            'HARD CONSTRAINT: NO HALLUCINATED TRUNCATION',
            'CORE EXECUTION PRINCIPLES – absolute requirements and prohibitions',
            'TECHNICAL IDENTIFIER HANDLING – product names, API names, code, file paths',
            'MEASUREMENT AND SPECIFICATION RULES – units, tolerances, dimensions',
            'UI/SOFTWARE STRING RULES – menu items, button labels, error messages',
            'TERMINOLOGY CONSISTENCY HIERARCHY',
            'NUMBER, DATE & LOCALISATION RULES',
            'PREFLIGHT SELF-CHECK (MANDATORY)',
            'PROJECT CONTEXT – product/system, technical domain, target audience',
            'PROJECT-SPECIFIC TERMBASE (MANDATORY, LOCKED)',
            'PREVIOUS CORRECT TRANSLATIONS',
            'OUTPUT FORMAT',
        ],
        'special': (
            'Technical translation requires absolute precision. Never translate product names, '
            'API names, or technical identifiers. Preserve all formatting in code blocks and '
            'technical specifications.'
        ),
    },
    'financial': {
        'role': (
            'Senior financial translator specializing in banking, investment, audit, '
            'and regulatory financial documentation. Deep expertise in IFRS/GAAP conventions, '
            'financial instruments, and regulatory compliance language.'
        ),
        'rules': [
            'Preserve all financial figures, percentages, exchange rates, and calculations exactly',
            'Use target-market financial terminology (IFRS vs GAAP conventions as appropriate)',
            'Maintain all regulatory references, compliance language, and risk disclosures verbatim',
            'Preserve currency codes (EUR, USD, GBP) and financial instrument names',
            'Never alter or omit risk warnings, disclaimers, or regulatory obligations',
            'Maintain all table structures, balance sheet formatting, and numerical alignment',
        ],
        'sections': [
            'ROLE (senior financial translator with regulatory expertise)',
            'FINANCIAL CONTEXT (document type, regulatory framework, jurisdiction)',
            'TRANSLATION MANDATE (NON-NEGOTIABLE) – faithful financial translation, no interpretation',
            'HARD CONSTRAINT: NO HALLUCINATED TRUNCATION – every figure and disclaimer is regulatory',
            'CORE EXECUTION PRINCIPLES – absolute requirements and prohibitions',
            'FINANCIAL DATA PRESERVATION RULES – figures, percentages, calculations',
            'REGULATORY AND COMPLIANCE LANGUAGE – risk warnings, disclaimers, obligations',
            'CURRENCY AND NUMBER FORMAT RULES – currency codes, decimal/thousands separators',
            'TERMINOLOGY CONSISTENCY HIERARCHY',
            'PREFLIGHT SELF-CHECK (MANDATORY) – verify all figures, calculations, and disclosures',
            'PROJECT CONTEXT – document type, financial instrument, jurisdiction',
            'PROJECT-SPECIFIC TERMBASE (MANDATORY, LOCKED)',
            'PREVIOUS CORRECT TRANSLATIONS',
            'OUTPUT FORMAT',
        ],
        'special': (
            'Financial data integrity is paramount. Any altered figure could constitute a '
            'regulatory violation. Preserve all numerical data, risk warnings, and compliance '
            'language with absolute fidelity.'
        ),
    },
    'marketing': {
        'role': (
            'Senior marketing and creative translator specializing in brand communication, '
            'transcreation, and cultural adaptation. Deep expertise in advertising copy, '
            'digital content, and brand voice preservation.'
        ),
        'rules': [
            'Prioritize cultural resonance and emotional impact over literal accuracy where appropriate',
            'Adapt slogans, taglines, and CTAs for target market effectiveness',
            'Maintain brand voice consistency (tone, personality, register) throughout',
            'Adapt cultural references, humor, and idioms for target audience',
            'Preserve brand names, product names, and trademarked terms unchanged',
            'Maintain SEO keyword effectiveness in target language where applicable',
        ],
        'sections': [
            'ROLE (senior marketing translator/transcreator)',
            'BRAND CONTEXT (brand, audience, campaign, tone of voice)',
            'CREATIVE MANDATE – cultural adaptation and persuasive effectiveness prioritized',
            'HARD CONSTRAINT: NO HALLUCINATED TRUNCATION',
            'BRAND VOICE RULES (LOCKED) – tone, personality, register specifications',
            'CULTURAL ADAPTATION GUIDELINES – when to adapt vs. preserve',
            'CALL-TO-ACTION AND TAGLINE RULES – effectiveness over literalness',
            'TERMINOLOGY CONSISTENCY HIERARCHY',
            'PREFLIGHT SELF-CHECK (MANDATORY)',
            'PROJECT CONTEXT – brand, campaign, target audience, key messages',
            'PROJECT-SPECIFIC TERMBASE (MANDATORY, LOCKED)',
            'PREVIOUS CORRECT TRANSLATIONS',
            'OUTPUT FORMAT',
        ],
        'special': (
            'Marketing translation permits creative freedom – prioritize persuasive effectiveness '
            'and cultural fit over word-for-word fidelity. However, brand names, product names, '
            'and trademarked terms must never be altered.'
        ),
    },
    'general': {
        'role': (
            'Professional translator with broad expertise across multiple domains, '
            'strong command of both source and target languages, and deep understanding '
            'of cultural and register differences.'
        ),
        'rules': [
            'Maintain the tone and register of the source text faithfully',
            'Preserve all formatting, tags, placeholders, and structural elements exactly',
            'Ensure terminology consistency throughout the entire document',
            'Adapt cultural references appropriately for the target audience',
            'Preserve all numbers, dates, measurements, and special formatting',
        ],
        'sections': [
            'ROLE (professional translator)',
            'DOCUMENT CONTEXT (type, domain, subject matter)',
            'TRANSLATION MANDATE (NON-NEGOTIABLE) – faithful translation, no improvement or simplification',
            'HARD CONSTRAINT: NO HALLUCINATED TRUNCATION',
            'CORE EXECUTION PRINCIPLES – absolute requirements and prohibitions',
            'TRANSLATION STYLE RULES – register, tone, formality',
            'TERMINOLOGY CONSISTENCY HIERARCHY',
            'NUMBER, DATE & LOCALISATION RULES – appropriate for language pair',
            'PREFLIGHT SELF-CHECK (MANDATORY)',
            'PROJECT CONTEXT – document description and subject matter',
            'PROJECT-SPECIFIC TERMBASE (MANDATORY, LOCKED)',
            'PREVIOUS CORRECT TRANSLATIONS',
            'OUTPUT FORMAT',
        ],
        'special': (
            'Analyze the document to identify the most appropriate domain and apply '
            'domain-appropriate conventions. When in doubt, prioritize faithfulness to '
            'the source text over stylistic preferences.'
        ),
    },
}


class UnifiedPromptManagerQt:
    """
    Unified Prompt Manager - Single-tab interface with:
    - Tree view with nested folders
    - QuickLauncher
    - Multi-attach capability
    - Active prompt configuration panel
    """
    
    def __init__(self, parent_app, standalone=False):
        """
        Initialize Unified Prompt Manager
        
        Args:
            parent_app: Reference to main application (needs .user_data_path, .log() method)
            standalone: If True, running standalone. If False, embedded in Supervertaler
        """
        self.parent_app = parent_app
        self.standalone = standalone
        
        # Get user_data path
        if hasattr(parent_app, 'user_data_path'):
            self.user_data_path = Path(parent_app.user_data_path)
        else:
            self.user_data_path = Path("user_data")
        
        # Initialize logging
        self.log = parent_app.log if hasattr(parent_app, 'log') else print
        
        # Paths
        self.prompt_library_dir = self.user_data_path / "prompt_library"
        # Use prompt_library directly, not prompt_library/Library
        self.unified_library_dir = self.prompt_library_dir

        # Run migration if needed
        self._check_and_migrate()

        # Initialize unified prompt library
        self.library = UnifiedPromptLibrary(
            library_dir=str(self.unified_library_dir),
            log_callback=self.log_message
        )
        
        # Load prompts
        self.library.load_all_prompts()
        
        # System Prompts (stored separately, loaded from settings/files)
        self.system_templates = {}
        self.current_mode = "single"  # single, batch_docx, batch_bilingual
        self._load_system_templates()
        
        # UI will be created by create_tab()
        self.main_widget = None
        self.tree_widget = None
        self.editor_content = None
        self.active_config_widget = None
        
        # AI Assistant state
        self.ai_conversation_file = self.user_data_path / "workbench" / "ai_assistant" / "conversation.json"
        self._cached_document_markdown: Optional[str] = None  # Cached markdown conversion of current document

        # Chat backend (shared across all chat views)
        self.chat_backend = ChatBackend(
            parent_app=self.parent_app,
            conversation_file=self.ai_conversation_file,
            log_callback=self.log_message
        )

        # Backward-compat property aliases
        self.attached_files: List[Dict] = []  # DEPRECATED, use attachment_manager

        # Initialize Attachment Manager
        ai_assistant_dir = self.user_data_path / "workbench" / "ai_assistant"
        self.attachment_manager = AttachmentManager(
            base_dir=str(ai_assistant_dir),
            log_callback=self.log_message
        )
        # Set initial session based on current date/time
        session_id = datetime.now().strftime("%Y%m%d")
        self.attachment_manager.set_session(session_id)

        # Initialize AI Action System (Phase 2)
        self.ai_action_system = AIActionSystem(
            prompt_library=self.library,
            parent_app=self.parent_app,
            log_callback=self.log_message
        )

        # Context inclusion toggles for AI Assistant
        self.include_tm_data = False
        self.include_termbase_data = False

        # Escape-to-return state
        self._assistant_return_external = False

        # Chat view references (set during create_tab / _create_ai_assistant_tab)
        self._grid_chat_view: Optional[ChatViewWidget] = None
        self._ai_tab_chat_view: Optional[ChatViewWidget] = None

        self._load_persisted_attachments()

    # ------------------------------------------------------------------
    # Backward-compat properties (delegate to ChatBackend / ChatViewWidget)
    # ------------------------------------------------------------------

    @property
    def llm_client(self):
        return self.chat_backend.llm_client

    @llm_client.setter
    def llm_client(self, value):
        self.chat_backend.llm_client = value

    @property
    def chat_history(self):
        return self.chat_backend.chat_history

    @chat_history.setter
    def chat_history(self, value):
        self.chat_backend.chat_history = value

    @property
    def chat_display(self):
        if self._grid_chat_view:
            return self._grid_chat_view._chat_display
        return None

    @property
    def chat_input(self):
        if self._grid_chat_view:
            return self._grid_chat_view._chat_input
        return None

    def _check_and_migrate(self):
        """Check if migration is needed and perform it, then ensure default folders exist"""
        try:
            needs_migration = migrate_prompt_library(
                str(self.prompt_library_dir),
                log_callback=self.log_message
            )

            if needs_migration:
                self.log_message("✓ Prompt library migration completed successfully")

        except Exception as e:
            self.log_message(f"⚠ Migration check failed: {e}")

        # Ensure default folders exist (for new users and existing users alike)
        self._ensure_default_folders()

    def _ensure_default_folders(self):
        """Create default prompt library folders if they don't exist"""
        default_folders = [
            "Translate",
            "Proofread",
            "QuickLauncher",
        ]
        try:
            for folder in default_folders:
                folder_path = self.prompt_library_dir / folder
                if not folder_path.exists():
                    folder_path.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            self.log_message(f"⚠ Failed to create default folders: {e}")

    def log_message(self, message):
        """Log a message through parent app or print"""
        self.log(message)
    
    def create_tab(self, parent_widget):
        """
        Create the Prompt Manager tab UI with sub-tabs
        
        Args:
            parent_widget: Widget to add the tab to (will set its layout)
        """
        main_layout = QVBoxLayout(parent_widget)
        main_layout.setContentsMargins(5, 5, 5, 5)
        main_layout.setSpacing(2)

        # Sub-tabs: Prompt Manager, Supervertaler Sidekick, Variables
        self.sub_tabs = QTabWidget()
        self.sub_tabs.tabBar().setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.sub_tabs.tabBar().setDrawBase(False)
        self.sub_tabs.setStyleSheet("QTabBar::tab { outline: 0; } QTabBar::tab:focus { outline: none; } QTabBar::tab:selected { border-bottom: 1px solid #2196F3; background-color: rgba(33, 150, 243, 0.08); }")

        # Tab 1: Prompt Manager
        library_tab = self._create_prompt_library_tab()
        self.sub_tabs.addTab(library_tab, "📋 Prompt Manager")

        # Tab 2: Variables Reference (was Placeholders)
        variables_tab = self._create_placeholders_tab()
        self.sub_tabs.addTab(variables_tab, "📝 Variables")

        # Supervertaler Sidekick – created here but added to the right panel
        # in Supervertaler.py so it's visible alongside the translation grid.
        self.assistant_tab = self._create_ai_assistant_tab()

        # Tab 3: AI Assistant (full-width view, for use without a project open).
        # AutoPrompt is no longer shown here — it now lives in the Prompt
        # Library toolbar (Prompt Manager tab) where the created prompt
        # immediately appears and can be edited.
        self._ai_tab_chat_view = ChatViewWidget(self.chat_backend)
        self._ai_tab_chat_view._do_send = self._context_aware_send
        self._ai_tab_chat_view.escape_pressed.connect(self._return_from_assistant)
        self.sub_tabs.addTab(self._ai_tab_chat_view, "💬 Chat")

        main_layout.addWidget(self.sub_tabs, 1)  # 1 = stretch

    def _on_assistant_shown(self):
        """Handle AI Assistant becoming visible – update context sidebar"""
        self._update_context_sidebar()

    def receive_text_for_assistant(self, text: str, from_external: bool = False):
        """
        Switch to the Supervertaler Sidekick sub-tab, insert text into the chat input,
        and focus the input field. Called from QuickLauncher "Supervertaler Sidekick".

        Pressing Escape while in the Assistant tab will return the user to the Grid tab,
        or re-activate the external app if launched via the global hotkey.
        """
        # Remember where to return on Escape
        self._assistant_return_external = from_external

        # Insert text into the grid-side chat view
        if self._grid_chat_view:
            self._grid_chat_view.insert_text(text)
            self._grid_chat_view.focus_input()

    def _return_from_assistant(self):
        """Return to the Grid tab (or external app) after Escape in the Assistant."""
        pa = self.parent_app
        if getattr(self, '_assistant_return_external', False):
            # Try to re-activate the external app that launched the QuickLauncher
            try:
                source_window = getattr(pa, '_quicklauncher_source_window', None)
                if source_window:
                    from modules.platform_helpers import activate_foreground_window
                    activate_foreground_window(source_window)
                    print("[Assistant] Returned focus to external app")
                    # Also switch back to Grid so Supervertaler isn't left on the Assistant
                    if hasattr(pa, 'main_tabs'):
                        pa.main_tabs.setCurrentIndex(0)
                    return
            except Exception as e:
                print(f"[Assistant] Could not reactivate external app: {e}")

        # Default: switch right panel back to Match Panel and ensure Grid is visible
        if hasattr(pa, 'right_tabs'):
            pa.right_tabs.setCurrentIndex(0)  # Match Panel
        if hasattr(pa, 'main_tabs'):
            pa.main_tabs.setCurrentIndex(0)
            print("[Assistant] Returned to Grid tab")

    def refresh_context(self):
        """
        Public method to refresh AI Assistant context.
        Call this from the main app when document/project changes.
        """
        # Reload cached document markdown from disk
        if hasattr(self.parent_app, 'current_document_path') and self.parent_app.current_document_path:
            doc_path = Path(self.parent_app.current_document_path)
            # Try to load existing markdown
            markdown_dir = self.user_data_path / "workbench" / "ai_assistant" / "current_document"
            markdown_file = markdown_dir / f"{doc_path.stem}.md"
            if markdown_file.exists():
                try:
                    with open(markdown_file, 'r', encoding='utf-8') as f:
                        self._cached_document_markdown = f.read()
                    self.log_message(f"✓ Loaded cached markdown: {markdown_file.name}")
                except Exception as e:
                    self.log_message(f"⚠ Failed to load cached markdown: {e}")
                    self._cached_document_markdown = None
            else:
                self._cached_document_markdown = None
        else:
            self._cached_document_markdown = None

        self._update_context_sidebar()
    
    def _create_main_header(self) -> QWidget:
        """Create main AI tab header"""
        header_container = QWidget()
        layout = QVBoxLayout(header_container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(5)

        # Title
        title = QLabel("✨ AI")
        title.setStyleSheet("font-size: 16pt; font-weight: bold; color: #1976D2;")
        layout.addWidget(title, 0)

        # Description
        desc = QLabel(
            "Manage AI instructions and get AI assistance for your translation projects.\n"
            "Create custom prompts, organize them in folders, and use AI to analyze documents."
        )
        desc.setWordWrap(True)
        desc.setStyleSheet("color: #666; padding: 5px; background-color: #E3F2FD; border-radius: 3px;")
        layout.addWidget(desc, 0)
        
        return header_container
    
    def _create_prompt_library_tab(self) -> QWidget:
        """Create the Prompt Library sub-tab"""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(0, 5, 0, 0)
        layout.setSpacing(5)
        
        # Main content: Horizontal splitter (left: config+buttons+tree | right: editor)
        main_splitter = QSplitter(Qt.Orientation.Horizontal)
        main_splitter.setHandleWidth(3)
        
        # Left panel container (not a splitter - fixed layout)
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(5)

        # v1.10.166: top-right contextual "?" help link — follows the
        # convention of help affordances sitting in the top-right corner
        # of the section they describe. One click opens the Prompt
        # Manager help page in the user's default browser.
        header_row = QHBoxLayout()
        header_row.setContentsMargins(0, 0, 0, 0)
        header_row.addStretch()
        help_btn = HelpButton(
            HelpTopics.AI_PROMPT_MANAGER,
            tooltip="Open the Prompt Manager help page",
        )
        header_row.addWidget(help_btn)
        left_layout.addLayout(header_row)

        # Active Configuration: sections 1-4 (System / Custom / Attached
        # / Image Context) – all styled numbered group boxes from
        # _create_active_config_panel.
        config_group = self._create_active_config_panel()
        # v1.10.260: no artificial minimum-height floor here. An explicit
        # setMinimumHeight overrides the layout's content-driven minimum, so a
        # 150px floor let this box shrink below its content and clip sections
        # 2-4 on shorter laptop screens. Let the content drive the minimum; the
        # surrounding QScrollArea (below) absorbs any overflow by scrolling.
        left_layout.addWidget(config_group)

        # Section 5: Prompt Library (styled group containing button row
        # + tree). v1.10.162 merged the previously-separate library
        # buttons toolbar and library tree panel into one numbered
        # section so the heading carries the same visual weight as 1-4.
        library_section = self._create_library_section()
        library_section.setMinimumHeight(220)
        left_layout.addWidget(library_section, 1)  # stretch — fills

        # Preview Combined: bottom of the left panel, on its own row, so
        # there's exactly one place to find "what will actually be sent
        # to the AI". v1.10.162 moved it here from the old utility row
        # that sat awkwardly between the prompt sections and the
        # library.
        preview_row = QHBoxLayout()
        preview_row.setContentsMargins(0, 4, 0, 0)
        preview_row.addStretch()
        btn_preview = QPushButton("👁 Preview Combined")
        btn_preview.setToolTip(
            "Preview the complete assembled prompt that will be sent to the AI\n"
            "(System Prompt + Custom Prompt + Attached Prompts + your text)"
        )
        btn_preview.clicked.connect(self._preview_combined_prompt)
        preview_row.addWidget(btn_preview)
        preview_row.addStretch()
        left_layout.addLayout(preview_row)
        
        left_panel.setMinimumWidth(300)

        # v1.10.260: wrap the whole left column in a vertical scroll area so the
        # numbered sections keep their natural size on shorter screens and the
        # column scrolls instead of compressing/clipping. widgetResizable keeps
        # it filling the splitter pane on tall screens (the library tree still
        # stretches to fill); the scrollbar only appears when the viewport is
        # too short to show everything.
        left_scroll = QScrollArea()
        left_scroll.setWidgetResizable(True)
        left_scroll.setFrameShape(QFrame.Shape.NoFrame)
        left_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        left_scroll.setWidget(left_panel)
        left_scroll.setMinimumWidth(300)
        main_splitter.addWidget(left_scroll)
        
        # Right: stack of Prompt Editor (default) + Image Context viewer.
        # v1.10.176: the previously-separate "🎯 Image Context" sub-tab
        # is folded into this right panel via a QStackedWidget. Page 0 is
        # the Prompt Editor (the canonical view); page 1 holds the
        # Image Context widget, installed later by the parent app via
        # set_image_context_widget(). Section 4 on the left has an
        # "Open ▸" button that switches the stack to the image-context
        # page; clicking on a prompt in the library tree switches back
        # to the editor page.
        editor_group = self._create_editor_panel()
        editor_group.setMinimumWidth(400)
        editor_group.setMinimumHeight(300)

        from PyQt6.QtWidgets import QStackedWidget
        self._right_stack = QStackedWidget()
        self._right_stack.addWidget(editor_group)            # page 0
        self._right_stack_editor_index = 0

        # Page 1 placeholder until the parent app installs the real
        # Image Context widget via set_image_context_widget(). Empty
        # placeholder makes the stack well-formed even before the
        # parent gets around to populating page 1.
        _placeholder = QWidget()
        self._right_stack.addWidget(_placeholder)            # page 1
        self._right_stack_image_index = 1

        main_splitter.addWidget(self._right_stack)

        # Set main splitter proportions (40% left, 60% editor)
        main_splitter.setSizes([400, 600])
        main_splitter.setStretchFactor(0, 1)
        main_splitter.setStretchFactor(1, 2)

        layout.addWidget(main_splitter, 1)
        
        # Load initial tree content
        self._refresh_tree()
        
        return tab
    
    def _create_library_buttons(self) -> QWidget:
        """Create the action-button row for the Prompt Library section.

        v1.10.162: AutoPrompt moved out of here into Section 2 (Custom
        Prompt) where it directly populates the slot it fills.
        v1.10.165: "⚙️ System Prompts" removed — Section 1's
        "View System Prompt" button opens a dialog with its own
        "Edit in Settings" button that goes to the same place; keeping
        a duplicate here just cluttered the toolbar.

        The remaining buttons are pure library-level actions: creating
        / finding / reordering library entries.
        """
        container = QWidget()
        btn_layout = QHBoxLayout(container)
        btn_layout.setContentsMargins(0, 0, 0, 0)
        btn_layout.setSpacing(5)

        btn_new = QPushButton("+ New")
        btn_new.clicked.connect(self._new_prompt)
        btn_layout.addWidget(btn_new)

        btn_folder = QPushButton("📁 New Folder")
        btn_folder.clicked.connect(self._new_folder)
        btn_layout.addWidget(btn_folder)

        btn_refresh = QPushButton("🔄 Refresh")
        btn_refresh.clicked.connect(self._refresh_library)
        btn_layout.addWidget(btn_refresh)

        btn_collapse_all = QPushButton("▸ Collapse all")
        btn_collapse_all.setToolTip("Collapse all folders in the Prompt Library tree")
        btn_collapse_all.clicked.connect(self._collapse_prompt_library_tree)
        btn_layout.addWidget(btn_collapse_all)

        btn_expand_all = QPushButton("▾ Expand all")
        btn_expand_all.setToolTip("Expand all folders in the Prompt Library tree")
        btn_expand_all.clicked.connect(self._expand_prompt_library_tree)
        btn_layout.addWidget(btn_expand_all)

        btn_layout.addStretch()

        return container

    def _create_library_section(self) -> QGroupBox:
        """v1.10.162: wrap the library button row + tree in a numbered
        styled section so it reads as 'Section 5' in the prompt-stack
        sequence rather than as a loose toolbar floating above a bare
        tree. Buttons sit INSIDE the section, below the styled heading.
        """
        section = self._styled_section_group(
            "5. Prompt Library — saved prompts you can pick from"
        )
        v = QVBoxLayout()
        v.setContentsMargins(6, 4, 6, 4)
        v.setSpacing(6)

        v.addWidget(self._create_library_buttons())

        tree_panel = self._create_library_tree_panel()
        v.addWidget(tree_panel, 1)

        section.setLayout(v)
        return section
    
    def _create_ai_assistant_tab(self) -> QWidget:
        """Create the AI Assistant tab for the grid-side right panel."""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(5, 5, 5, 5)
        layout.setSpacing(5)

        # Chat view widget (context chips). AutoPrompt was previously
        # shown here too as a header-styled button — moved to the Prompt
        # Library toolbar where it sits with related actions and the
        # generated prompt immediately appears in-place.
        self._grid_chat_view = ChatViewWidget(self.chat_backend)
        self._grid_chat_view._do_send = self._context_aware_send
        self._grid_chat_view.escape_pressed.connect(self._return_from_assistant)
        layout.addWidget(self._grid_chat_view, 1)

        return tab
    
    def _create_placeholders_tab(self) -> QWidget:
        """Create the Variables Reference sub-tab"""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(5)

        # Header (matches standard tool style: PDF Rescue, TMX Editor)
        header = QLabel("📝 Available Variables")
        header.setStyleSheet("font-size: 16pt; font-weight: bold; color: #1976D2;")
        layout.addWidget(header, 0)

        # Description box (matches standard tool style)
        description = QLabel(
            "Use these variables in your prompts. They will be replaced with actual values when the prompt runs."
        )
        description.setWordWrap(True)
        description.setStyleSheet("color: #666; padding: 5px; background-color: #E3F2FD; border-radius: 3px;")
        layout.addWidget(description, 0)
        
        # Horizontal splitter for table and tips
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(3)
        
        # Left: Table with variables
        table = QTableWidget()
        table.setColumnCount(3)
        table.setHorizontalHeaderLabels(["Variable", "Description", "Example"])
        table.horizontalHeader().setStretchLastSection(True)
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        table.setAlternatingRowColors(True)
        
        # Variable data
        variables = [
            (
                "{{SELECTION}}",
                "Currently selected text in the grid (source or target cell)",
                "If you select 'translation memory' in the grid, this will contain that text"
            ),
            (
                "{{SOURCE_TEXT}}",
                "Full text of the current source segment",
                "The complete source sentence/paragraph from the active segment"
            ),
            (
                "{{TARGET_TEXT}}",
                "Current target (translation) of the active segment. Empty if not yet translated.",
                "Use in review/proofreading prompts: 'Review: {{SOURCE_TEXT}} → {{TARGET_TEXT}}'"
            ),
            (
                "{{SOURCE_LANGUAGE}}",
                "Project's source language",
                "Dutch, English, German, French, etc."
            ),
            (
                "{{TARGET_LANGUAGE}}",
                "Project's target language",
                "English, Spanish, Portuguese, etc."
            ),
            (
                "{{SOURCE+TARGET_CONTEXT}}",
                "Project segments with BOTH source and target text. Use for proofreading prompts.",
                "[1] Source text\\n    → Target text\\n\\n[2] Source text\\n    → Target text\\n\\n..."
            ),
            (
                "{{SOURCE_CONTEXT}}",
                "Project segments with SOURCE ONLY. Use for translation/terminology questions.",
                "[1] Source text\\n\\n[2] Source text\\n\\n[3] Source text\\n\\n..."
            ),
            (
                "{{TARGET_CONTEXT}}",
                "Project segments with TARGET ONLY. Use for consistency/style analysis.",
                "[1] Target text\\n\\n[2] Target text\\n\\n[3] Target text\\n\\n..."
            )
        ]
        
        table.setRowCount(len(variables))
        for row, (variable, description, example) in enumerate(variables):
            # Variable column (monospace, bold)
            item_variable = QTableWidgetItem(variable)
            item_variable.setFont(QFont("Courier New", 10, QFont.Weight.Bold))
            table.setItem(row, 0, item_variable)
            
            # Description column
            item_desc = QTableWidgetItem(description)
            item_desc.setToolTip(description)
            table.setItem(row, 1, item_desc)
            
            # Example column (monospace, italic)
            item_example = QTableWidgetItem(example)
            item_example.setFont(QFont("Courier New", 9))
            item_example.setToolTip(example)
            table.setItem(row, 2, item_example)
        
        # Set column widths
        table.setColumnWidth(0, 200)
        table.setColumnWidth(1, 300)
        header = table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        
        # Adjust row heights for readability
        for row in range(table.rowCount()):
            table.setRowHeight(row, 60)
        
        splitter.addWidget(table)
        
        # Right: Usage tips panel
        tips_panel = QWidget()
        tips_layout = QVBoxLayout(tips_panel)
        tips_layout.setContentsMargins(10, 0, 0, 0)
        
        tips_header = QLabel("💡 Usage Tips")
        tips_header.setStyleSheet("font-weight: bold; font-size: 11pt; color: #2196F3; margin-bottom: 8px;")
        tips_layout.addWidget(tips_header)
        
        tips_intro = QLabel(
            "Use these variables in your prompts. They will be replaced with actual values when the prompt runs."
        )
        tips_intro.setWordWrap(True)
        tips_intro.setStyleSheet("color: #666; margin-bottom: 15px; font-style: italic;")
        tips_layout.addWidget(tips_intro)
        
        tips_text = QLabel(
            "• Variables are case-sensitive (use UPPERCASE)\n\n"
            "• Surround variables with double curly braces: {{ }}\n\n"
            "• You can combine multiple variables in one prompt\n\n"
            "• {{TARGET_TEXT}} is useful for review/proofreading prompts\n\n"
            "• Context variables (SOURCE_CONTEXT etc.) use the percentage set in Settings → AI Settings"
        )
        tips_text.setWordWrap(True)
        tips_text.setStyleSheet("color: #666; line-height: 1.6;")
        tips_layout.addWidget(tips_text)
        
        tips_layout.addStretch()
        
        tips_panel.setMinimumWidth(280)
        tips_panel.setMaximumWidth(400)
        splitter.addWidget(tips_panel)
        
        # Set splitter proportions (75% table, 25% tips)
        splitter.setSizes([750, 250])
        splitter.setStretchFactor(0, 1)  # Table expands
        splitter.setStretchFactor(1, 0)  # Tips panel fixed-ish
        
        layout.addWidget(splitter, 1)  # 1 = stretch to fill all available space
        
        return tab
    
    def _create_context_sidebar(self) -> QWidget:
        """Create collapsible context sidebar showing available resources"""
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(5, 2, 5, 0)
        layout.setSpacing(0)

        # Clickable header to toggle collapse
        header = QPushButton("▼ Available Context")
        header.setStyleSheet("""
            QPushButton {
                font-weight: bold; font-size: 9pt; color: #1976D2;
                text-align: left; border: none; padding: 4px 2px;
                background: transparent;
            }
            QPushButton:hover { background-color: rgba(25, 118, 210, 0.08); border-radius: 3px; }
            QPushButton:focus { outline: none; }
        """)
        header.setCursor(Qt.CursorShape.PointingHandCursor)
        header.setToolTip("Click to collapse/expand context panel")
        layout.addWidget(header)
        self._context_header_btn = header

        # Collapsible content area
        context_body = QWidget()
        body_layout = QVBoxLayout(context_body)
        body_layout.setContentsMargins(0, 5, 0, 5)
        body_layout.setSpacing(8)

        # Current Project Document
        self.context_current_doc = self._create_context_section(
            "📄 Current Document",
            "No document loaded"
        )
        body_layout.addWidget(self.context_current_doc)

        # Attached Files (expandable section)
        self.context_attached_files_frame = self._create_attached_files_section()
        body_layout.addWidget(self.context_attached_files_frame)

        # Prompts from Library
        prompt_count = len(self.library.prompts)
        self.context_prompts = self._create_context_section(
            f"💡 Prompt Library ({prompt_count})",
            f"{prompt_count} prompts available\nClick to select specific prompts"
        )
        self.context_prompts.setCursor(Qt.CursorShape.PointingHandCursor)
        body_layout.addWidget(self.context_prompts)

        # Translation Memories
        self.context_tms = self._create_context_section(
            "💾 Translation Memories",
            "Click to include TM data"
        )
        self.context_tms.setCursor(Qt.CursorShape.PointingHandCursor)
        self.context_tms.mousePressEvent = lambda e: self._toggle_tm_inclusion()
        body_layout.addWidget(self.context_tms)

        # Termbases
        self.context_termbases = self._create_context_section(
            "📚 Termbases",
            "Click to include termbase data"
        )
        self.context_termbases.setCursor(Qt.CursorShape.PointingHandCursor)
        self.context_termbases.mousePressEvent = lambda e: self._toggle_termbase_inclusion()
        body_layout.addWidget(self.context_termbases)

        # Wrap in a scroll area with a max height so it doesn't steal all chat space
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(context_body)
        scroll.setMaximumHeight(320)
        scroll.setStyleSheet("QScrollArea { border: none; }")

        self._context_body = scroll
        layout.addWidget(scroll)

        # Start collapsed to give chat maximum space
        self._context_collapsed = True
        scroll.setVisible(False)
        header.setText("▶ Available Context")

        header.clicked.connect(self._toggle_context_sidebar)

        return panel

    def _toggle_context_sidebar(self):
        """Toggle the Available Context section open/closed"""
        self._context_collapsed = not self._context_collapsed
        self._context_body.setVisible(not self._context_collapsed)
        self._context_header_btn.setText(
            "▶ Available Context" if self._context_collapsed else "▼ Available Context"
        )
    
    def _create_context_section(self, title: str, description: str) -> QFrame:
        """Create a context section widget"""
        frame = QFrame()
        frame.setStyleSheet("""
            QFrame {
                background-color: #F5F5F5;
                border: 1px solid #E0E0E0;
                border-radius: 5px;
                padding: 8px;
            }
            QFrame:hover {
                background-color: #EEEEEE;
                border: 1px solid #BDBDBD;
            }
        """)
        
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(5, 5, 5, 5)
        layout.setSpacing(3)
        
        title_label = QLabel(title)
        title_label.setStyleSheet("font-weight: bold; font-size: 9pt;")
        layout.addWidget(title_label)
        
        desc_label = QLabel(description)
        desc_label.setStyleSheet("color: #666; font-size: 8pt;")
        desc_label.setWordWrap(True)
        layout.addWidget(desc_label)

        return frame

    def _toggle_tm_inclusion(self):
        """Toggle inclusion of TM data in AI context"""
        self.include_tm_data = not self.include_tm_data
        self._update_tm_section_display()

        # Show feedback
        status = "enabled" if self.include_tm_data else "disabled"
        self._add_chat_message("system", f"💾 Translation Memory inclusion **{status}**")

    def _toggle_termbase_inclusion(self):
        """Toggle inclusion of termbase data in AI context"""
        self.include_termbase_data = not self.include_termbase_data
        self._update_termbase_section_display()

        # Show feedback
        status = "enabled" if self.include_termbase_data else "disabled"
        self._add_chat_message("system", f"📚 Termbase inclusion **{status}**")

    def _update_tm_section_display(self):
        """Update TM section visual state"""
        if self.include_tm_data:
            self.context_tms.setStyleSheet("""
                QFrame {
                    background-color: #E3F2FD;
                    border: 2px solid #1976D2;
                    border-radius: 5px;
                    padding: 8px;
                }
            """)
            # Update description label
            for child in self.context_tms.findChildren(QLabel):
                if "Click to" in child.text() or "✓" in child.text():
                    child.setText("✓ TM data will be included")
                    child.setStyleSheet("color: #1976D2; font-size: 8pt; font-weight: bold;")
                    break
        else:
            self.context_tms.setStyleSheet("""
                QFrame {
                    background-color: #F5F5F5;
                    border: 1px solid #E0E0E0;
                    border-radius: 5px;
                    padding: 8px;
                }
                QFrame:hover {
                    background-color: #EEEEEE;
                    border: 1px solid #BDBDBD;
                }
            """)
            for child in self.context_tms.findChildren(QLabel):
                if "✓" in child.text() or "Click to" in child.text():
                    child.setText("Click to include TM data")
                    child.setStyleSheet("color: #666; font-size: 8pt;")
                    break

    def _update_termbase_section_display(self):
        """Update termbase section visual state"""
        if self.include_termbase_data:
            self.context_termbases.setStyleSheet("""
                QFrame {
                    background-color: #E8F5E9;
                    border: 2px solid #4CAF50;
                    border-radius: 5px;
                    padding: 8px;
                }
            """)
            for child in self.context_termbases.findChildren(QLabel):
                if "Click to" in child.text() or "✓" in child.text():
                    child.setText("✓ Termbase data will be included")
                    child.setStyleSheet("color: #4CAF50; font-size: 8pt; font-weight: bold;")
                    break
        else:
            self.context_termbases.setStyleSheet("""
                QFrame {
                    background-color: #F5F5F5;
                    border: 1px solid #E0E0E0;
                    border-radius: 5px;
                    padding: 8px;
                }
                QFrame:hover {
                    background-color: #EEEEEE;
                    border: 1px solid #BDBDBD;
                }
            """)
            for child in self.context_termbases.findChildren(QLabel):
                if "✓" in child.text() or "Click to" in child.text():
                    child.setText("Click to include termbase data")
                    child.setStyleSheet("color: #666; font-size: 8pt;")
                    break

    def _create_attached_files_section(self) -> QFrame:
        """Create expandable attached files section with view/remove buttons"""
        frame = QFrame()
        frame.setStyleSheet("""
            QFrame {
                background-color: #F5F5F5;
                border: 1px solid #E0E0E0;
                border-radius: 5px;
                padding: 8px;
            }
        """)

        layout = QVBoxLayout(frame)
        layout.setContentsMargins(5, 5, 5, 5)
        layout.setSpacing(5)

        # Header with expand/collapse button
        header_layout = QHBoxLayout()
        header_layout.setSpacing(5)

        self.attached_files_expand_btn = QPushButton("▼")
        self.attached_files_expand_btn.setFixedSize(20, 20)
        self.attached_files_expand_btn.setStyleSheet("""
            QPushButton {
                background-color: transparent;
                border: none;
                font-size: 10pt;
            }
            QPushButton:hover {
                background-color: #E0E0E0;
            }
            QPushButton:focus {
                outline: none;
            }
        """)
        self.attached_files_expand_btn.clicked.connect(self._toggle_attached_files)
        header_layout.addWidget(self.attached_files_expand_btn)

        self.attached_files_title = QLabel("📎 Attached Files (0)")
        self.attached_files_title.setStyleSheet("font-weight: bold; font-size: 9pt;")
        header_layout.addWidget(self.attached_files_title, 1)

        # Attach button
        attach_btn = QPushButton("+")
        attach_btn.setFixedSize(20, 20)
        attach_btn.setToolTip("Attach file")
        attach_btn.setStyleSheet("""
            QPushButton {
                background-color: #1976D2;
                color: white;
                border: none;
                border-radius: 3px;
                font-size: 12pt;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #1565C0;
            }
            QPushButton:focus {
                outline: none;
            }
        """)
        attach_btn.clicked.connect(self._attach_file)
        header_layout.addWidget(attach_btn)

        layout.addLayout(header_layout)

        # File list container (collapsible)
        self.attached_files_container = QWidget()
        self.attached_files_list_layout = QVBoxLayout(self.attached_files_container)
        self.attached_files_list_layout.setContentsMargins(5, 5, 5, 5)
        self.attached_files_list_layout.setSpacing(5)

        # Initially empty
        no_files_label = QLabel("No files attached")
        no_files_label.setStyleSheet("color: #999; font-size: 8pt; font-style: italic;")
        self.attached_files_list_layout.addWidget(no_files_label)

        layout.addWidget(self.attached_files_container)

        # Initially expanded
        self.attached_files_expanded = True

        return frame

    def _toggle_attached_files(self):
        """Toggle attached files section expansion"""
        self.attached_files_expanded = not self.attached_files_expanded
        self.attached_files_container.setVisible(self.attached_files_expanded)
        self.attached_files_expand_btn.setText("▼" if self.attached_files_expanded else "▶")

    def _refresh_attached_files_list(self):
        """Refresh the attached files list display"""
        # Clear current list
        while self.attached_files_list_layout.count():
            item = self.attached_files_list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        # Update title count
        count = len(self.attached_files)
        self.attached_files_title.setText(f"📎 Attached Files ({count})")

        # If no files, show placeholder
        if count == 0:
            no_files_label = QLabel("No files attached")
            no_files_label.setStyleSheet("color: #999; font-size: 8pt; font-style: italic;")
            self.attached_files_list_layout.addWidget(no_files_label)
            return

        # Add each file
        for file_data in self.attached_files:
            file_widget = self._create_file_item_widget(file_data)
            self.attached_files_list_layout.addWidget(file_widget)

    def _create_file_item_widget(self, file_data: dict) -> QFrame:
        """Create widget for a single attached file"""
        item_frame = QFrame()
        item_frame.setStyleSheet("""
            QFrame {
                background-color: white;
                border: 1px solid #E0E0E0;
                border-radius: 3px;
                padding: 4px;
            }
            QFrame:hover {
                border: 1px solid #1976D2;
            }
        """)

        layout = QVBoxLayout(item_frame)
        layout.setContentsMargins(5, 5, 5, 5)
        layout.setSpacing(3)

        # Filename
        name_label = QLabel(file_data.get('name', 'Unknown'))
        name_label.setStyleSheet("font-weight: bold; font-size: 8pt;")
        name_label.setWordWrap(True)
        layout.addWidget(name_label)

        # Size and type
        size = file_data.get('size', 0)
        size_kb = size / 1024 if size > 0 else 0
        file_type = file_data.get('type', '')
        info_label = QLabel(f"{file_type} • {size_kb:.1f} KB")
        info_label.setStyleSheet("color: #666; font-size: 7pt;")
        layout.addWidget(info_label)

        # Buttons
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(3)

        view_btn = QPushButton("👁 View")
        view_btn.setStyleSheet("""
            QPushButton {
                background-color: #1976D2;
                color: white;
                border: none;
                border-radius: 2px;
                padding: 2px 6px;
                font-size: 7pt;
            }
            QPushButton:hover {
                background-color: #1565C0;
            }
            QPushButton:focus {
                outline: none;
            }
        """)
        view_btn.clicked.connect(lambda: self._view_file(file_data))
        btn_layout.addWidget(view_btn)

        remove_btn = QPushButton("❌")
        remove_btn.setStyleSheet("""
            QPushButton {
                background-color: #d32f2f;
                color: white;
                border: none;
                border-radius: 2px;
                padding: 2px 6px;
                font-size: 7pt;
            }
            QPushButton:hover {
                background-color: #b71c1c;
            }
            QPushButton:focus {
                outline: none;
            }
        """)
        remove_btn.clicked.connect(lambda: self._remove_file(file_data))
        btn_layout.addWidget(remove_btn)

        btn_layout.addStretch()

        layout.addLayout(btn_layout)

        return item_frame

    def _view_file(self, file_data: dict):
        """View an attached file"""
        try:
            file_id = file_data.get('file_id')
            if file_id:
                # Load from AttachmentManager
                full_data = self.attachment_manager.get_file(file_id)
                if full_data:
                    dialog = FileViewerDialog(full_data, self.main_widget)
                    dialog.exec()
                else:
                    QMessageBox.warning(
                        self.main_widget,
                        "File Not Found",
                        "File data not found in storage."
                    )
            else:
                # Fallback: use in-memory data
                dialog = FileViewerDialog(file_data, self.main_widget)
                dialog.exec()
        except Exception as e:
            QMessageBox.warning(
                self.main_widget,
                "View Error",
                f"Failed to view file:\n{e}"
            )

    def _remove_file(self, file_data: dict):
        """Remove an attached file"""
        try:
            filename = file_data.get('name', 'Unknown')

            # Confirm removal
            dialog = FileRemoveConfirmDialog(filename, self.main_widget)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return

            file_id = file_data.get('file_id')

            # Remove from AttachmentManager
            if file_id:
                self.attachment_manager.remove_file(file_id)

            # Remove from in-memory list
            if file_data in self.attached_files:
                self.attached_files.remove(file_data)

            # Update UI
            self._refresh_attached_files_list()
            self._save_conversation_history()

            # Add system message
            self._add_chat_message(
                "system",
                f"🗑️ Removed file: **{filename}**"
            )

        except Exception as e:
            QMessageBox.warning(
                self.main_widget,
                "Remove Error",
                f"Failed to remove file:\n{e}"
            )

    def _create_chat_interface(self) -> QWidget:
        """Legacy: returns the grid chat view widget.
        Kept for backward compatibility – _create_ai_assistant_tab now
        creates the ChatViewWidget directly.
        """
        if self._grid_chat_view:
            return self._grid_chat_view
        # Fallback: create a new view
        view = ChatViewWidget(self.chat_backend)
        view._do_send = self._context_aware_send
        return view

    def _create_library_tree_panel(self) -> QWidget:
        """Create left panel with folder tree.

        v1.10.162: the tree's own "Prompt Library" header is now redundant
        with the surrounding section title from _create_library_section,
        so it's hidden.
        """
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)

        # Tree widget
        self.tree_widget = PromptLibraryTreeWidget(self)
        self.tree_widget.setHeaderLabels(["Prompt Library"])
        self.tree_widget.setHeaderHidden(True)  # section title already says this
        self.tree_widget.setAlternatingRowColors(True)
        self.tree_widget.itemClicked.connect(self._on_tree_item_clicked)
        self.tree_widget.itemDoubleClicked.connect(self._on_tree_item_double_clicked)
        self.tree_widget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree_widget.customContextMenuRequested.connect(self._show_tree_context_menu)

        layout.addWidget(self.tree_widget)

        return panel
    
    # ------------------------------------------------------------------
    # Section-group styling (v1.10.162)
    # ------------------------------------------------------------------
    # Numbered prompt-stack sections share a consistent visual style: a
    # coloured title strip so the title carries weight and the sections
    # read as distinct stacked blocks rather than blurring together. Pure
    # cosmetic helper — no behaviour changes.
    # ------------------------------------------------------------------
    _SECTION_STYLE = (
        "QGroupBox {"
        "  font-weight: bold;"
        "  margin-top: 14px;"
        "  border: 1px solid #B3D4FC;"
        "  border-radius: 4px;"
        "  padding: 14px 6px 6px 6px;"
        "}"
        "QGroupBox::title {"
        "  subcontrol-origin: margin;"
        "  subcontrol-position: top left;"
        "  padding: 3px 10px;"
        "  background-color: #E3F2FD;"
        "  color: #1565C0;"
        "  border-radius: 3px;"
        "  left: 8px;"
        "}"
    )

    def _styled_section_group(self, title: str) -> QGroupBox:
        """Return a QGroupBox with the standard numbered-section style."""
        g = QGroupBox(title)
        g.setStyleSheet(self._SECTION_STYLE)
        return g

    def _create_active_config_panel(self) -> QWidget:
        """Create the active prompt configuration panel.

        v1.10.162 restructure (follow-up to v1.10.159):
          1. System Prompt    — built-in instructions, edited in Settings
          2. Custom Prompt    — project-specific instructions; ✨ AutoPrompt
                                button now lives HERE (was on the library
                                toolbar) since it directly populates this
                                slot — that's the action that matters
                                most for translation work
          3. Attached Prompts — optional extras; "Clear All Attachments"
                                button moved INTO this section (it only
                                affects this section, so it doesn't
                                belong in a separate utility row)
          4. Image Context    — numbered too now (was hovering loose under
                                the three numbered sections)
        Each section has a coloured title strip via _styled_section_group
        so the numbering reads as five distinct stacked blocks (section
        5 is the Prompt Library, styled identically further down).
        Preview Combined moves to a single button at the very bottom of
        the left panel.
        """
        container = QWidget()
        outer = QVBoxLayout(container)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(8)

        # Helper for the consistent bottom caption styling — every numbered
        # section ends with a small italic info-icon-prefixed explanation.
        # v1.10.163: standardised position (always at the bottom) and icon
        # so the captions read as part of the section frame rather than
        # mixed in with the actionable controls.
        def _section_info(text: str) -> QLabel:
            lbl = QLabel(f"<span style='color:#1976D2;'>ⓘ</span> {text}")
            lbl.setStyleSheet("color: #666; font-size: 8pt;")
            lbl.setWordWrap(True)
            lbl.setTextFormat(Qt.TextFormat.RichText)
            return lbl

        # ----------------------------------------------------------------
        # 1. System Prompt
        # ----------------------------------------------------------------
        system_group = self._styled_section_group(
            "1. System Prompt — built-in instructions for the AI"
        )
        system_layout = QVBoxLayout()
        system_layout.setSpacing(4)

        system_row = QHBoxLayout()
        mode_label = QLabel(f"🔧 Current Mode: {self._get_mode_display_name()}")
        mode_label.setFont(QFont("Segoe UI", 9))
        system_row.addWidget(mode_label)
        system_row.addStretch()

        btn_view_template = QPushButton("View System Prompt")
        btn_view_template.clicked.connect(self._view_current_system_template)
        btn_view_template.setMaximumWidth(160)
        system_row.addWidget(btn_view_template)
        system_layout.addLayout(system_row)

        system_layout.addStretch(0)
        system_layout.addWidget(_section_info(
            "Auto-selected for the current mode. Edit in "
            "<i>Settings → 📝 System Prompts</i>."
        ))

        system_group.setLayout(system_layout)
        outer.addWidget(system_group)

        # ----------------------------------------------------------------
        # 2. Custom Prompt — two-column split:
        #    LEFT: pick from library / load external / clear (the
        #          currently-active prompt slot)
        #    RIGHT: ✨ AutoPrompt button (the other way to set this slot)
        # Vertical separator between them. The section caption stays at
        # the bottom of the whole section, spanning both columns.
        # ----------------------------------------------------------------
        custom_group = self._styled_section_group(
            "2. Custom Prompt — your project-specific instructions"
        )
        custom_layout = QVBoxLayout()
        custom_layout.setSpacing(6)

        columns_row = QHBoxLayout()
        columns_row.setSpacing(10)

        # --- LEFT column: active prompt + manual controls ---
        left_col = QVBoxLayout()
        left_col.setSpacing(4)

        left_heading = QLabel("<b>Active Custom Prompt</b>")
        left_heading.setStyleSheet("color: #333; font-size: 9pt;")
        left_col.addWidget(left_heading)

        primary_layout = QHBoxLayout()
        primary_label = QLabel("⭐")
        primary_label.setFont(QFont("Segoe UI", 11))
        primary_layout.addWidget(primary_label)

        self.primary_prompt_label = QLabel("[None selected]")
        self.primary_prompt_label.setStyleSheet("color: #999;")
        primary_layout.addWidget(self.primary_prompt_label, 1)
        left_col.addLayout(primary_layout)

        left_buttons = QHBoxLayout()
        btn_load_external = QPushButton("Load External...")
        btn_load_external.clicked.connect(self._load_external_primary_prompt)
        btn_load_external.setToolTip("Load a prompt file from anywhere on your computer")
        left_buttons.addWidget(btn_load_external)

        btn_clear_primary = QPushButton("Clear")
        btn_clear_primary.clicked.connect(self._clear_primary_prompt)
        btn_clear_primary.setMaximumWidth(70)
        left_buttons.addWidget(btn_clear_primary)
        left_buttons.addStretch()
        left_col.addLayout(left_buttons)
        left_col.addStretch()

        columns_row.addLayout(left_col, 3)

        # Vertical separator between the two columns
        vsep = QFrame()
        vsep.setFrameShape(QFrame.Shape.VLine)
        vsep.setFrameShadow(QFrame.Shadow.Sunken)
        vsep.setStyleSheet("color: #B3D4FC;")
        columns_row.addWidget(vsep)

        # --- RIGHT column: AutoPrompt action ---
        right_col = QVBoxLayout()
        right_col.setSpacing(4)

        right_heading = QLabel("<b>Generate one automatically</b>")
        right_heading.setStyleSheet("color: #333; font-size: 9pt;")
        right_col.addWidget(right_heading)

        btn_autoprompt = QPushButton("✨ AutoPrompt")
        btn_autoprompt.setStyleSheet("font-weight: bold; color: #1976D2;")
        btn_autoprompt.setToolTip(
            "Analyse the current document (domain, tone, terminology) and "
            "auto-generate a tailored translation prompt, then save it as "
            "the Custom Prompt for this project."
        )
        btn_autoprompt.clicked.connect(self._analyze_and_generate)
        right_col.addWidget(btn_autoprompt)

        # v1.10.178: opt-in to ship the loaded figure images with the
        # AutoPrompt meta-prompt. Off by default — turning it on adds
        # ~$0.05–$0.30 to the run cost depending on figure count and
        # provider. Only useful when the project has figures loaded in
        # Section 4 AND the active model supports vision.
        # v1.10.180: switched from plain QCheckBox to the project-
        # standard CheckmarkCheckBox so the box renders a proper green
        # checkmark when ticked instead of the platform's default
        # light-blue square (which on Windows was barely visible).
        self._autoprompt_include_images = CheckmarkCheckBox(
            "🖼️ Include loaded figure images"
        )
        self._autoprompt_include_images.setChecked(False)
        self._autoprompt_include_images.setToolTip(
            "When ticked, the figure images loaded in Section 4 are sent\n"
            "alongside the AutoPrompt meta-prompt so the LLM can visually\n"
            "ground its terminology decisions (labelled parts, captions,\n"
            "visible components, etc.).\n\n"
            "Requirements:\n"
            "  • Figure images must already be loaded in Section 4.\n"
            "  • The active model must support vision (Claude Sonnet/Opus,\n"
            "    GPT-4o and later, Gemini). Text-only models silently skip\n"
            "    the images.\n\n"
            "Cost impact: roughly +$0.05–$0.30 per AutoPrompt run with a\n"
            "Sonnet-class model and 10–20 figures; more with Opus-class.\n"
            "Negligible vs. the project's translation value, but off by\n"
            "default so you opt in deliberately."
        )
        right_col.addWidget(self._autoprompt_include_images)

        right_col.addStretch()

        columns_row.addLayout(right_col, 2)

        custom_layout.addLayout(columns_row)
        custom_layout.addWidget(_section_info(
            "Domain / project context layered on top of the System Prompt. "
            "Set one from the library below, use <b>Load External…</b> for "
            "an out-of-library file, or click <b>✨ AutoPrompt</b> to have "
            "the AI generate one tailored to the current document."
        ))

        custom_group.setLayout(custom_layout)
        outer.addWidget(custom_group)

        # ----------------------------------------------------------------
        # 3. Attached Prompts
        # ----------------------------------------------------------------
        attached_group = self._styled_section_group(
            "3. Attached Prompts — optional extras"
        )
        attached_layout = QVBoxLayout()
        attached_layout.setSpacing(4)

        self.attached_list_widget = QTreeWidget()
        self.attached_list_widget.setHeaderLabels(["Name", ""])
        self.attached_list_widget.setMaximumHeight(100)
        self.attached_list_widget.setRootIsDecorated(False)
        self.attached_list_widget.setColumnWidth(0, 200)
        attached_layout.addWidget(self.attached_list_widget)

        attached_actions = QHBoxLayout()
        attached_actions.addStretch()
        btn_clear_all = QPushButton("Clear All Attachments")
        btn_clear_all.clicked.connect(self._clear_all_attachments)
        attached_actions.addWidget(btn_clear_all)
        attached_layout.addLayout(attached_actions)

        attached_layout.addWidget(_section_info(
            "Additional prompts stacked on top of the Custom Prompt — "
            "right-click any prompt in the library to attach it."
        ))

        attached_group.setLayout(attached_layout)
        outer.addWidget(attached_group)

        # ----------------------------------------------------------------
        # 4. Image Context
        # ----------------------------------------------------------------
        image_group = self._styled_section_group(
            "4. Image Context — visual references for the AI"
        )
        image_layout = QVBoxLayout()
        image_layout.setSpacing(4)

        image_row = QHBoxLayout()
        image_emoji = QLabel("🖼️")
        image_emoji.setFont(QFont("Segoe UI", 11))
        image_row.addWidget(image_emoji)

        self.image_context_label = QLabel("[None loaded]")
        self.image_context_label.setStyleSheet("color: #999;")
        image_row.addWidget(self.image_context_label, 1)

        # v1.10.176: button that swaps the right-hand panel from the
        # Prompt Editor to the Image Context viewer (extract + load +
        # preview). The viewer used to live in its own AI sub-tab; it
        # now lives inside this Prompt Manager tab on the right.
        self._image_context_open_btn = QPushButton("Open ▸")
        self._image_context_open_btn.setToolTip(
            "Open the Image Context viewer in the right panel (extract images "
            "from a DOCX, load a folder, preview)."
        )
        self._image_context_open_btn.setStyleSheet(
            "QPushButton { padding: 2px 10px; border-radius: 3px; "
            "background-color: #4CAF50; color: white; font-weight: bold; } "
            "QPushButton:hover { background-color: #45a049; } "
            "QPushButton:focus { outline: none; }"
        )
        self._image_context_open_btn.clicked.connect(self.show_image_context_view)
        image_row.addWidget(self._image_context_open_btn)

        image_layout.addLayout(image_row)

        image_layout.addWidget(_section_info(
            "Images here are sent as binary data alongside your prompt "
            "when figure references (Fig. 1, Figure 2A, …) are detected "
            "in a segment. Click <b>Open ▸</b> to extract images from a "
            "DOCX or load a pre-existing Images folder."
        ))

        image_group.setLayout(image_layout)
        outer.addWidget(image_group)

        return container
    
    def _create_editor_panel(self) -> QGroupBox:
        """Create prompt editor panel"""
        group = QGroupBox("Prompt Editor")
        layout = QVBoxLayout()
        
        # Toolbar
        toolbar = QHBoxLayout()
        
        self.editor_name_label = QLabel("Select a prompt to edit")
        self.editor_name_label.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        toolbar.addWidget(self.editor_name_label)
        
        toolbar.addStretch()
        
        self.btn_save_prompt = QPushButton("💾 Save")
        self.btn_save_prompt.clicked.connect(self._save_current_prompt)
        self.btn_save_prompt.setEnabled(False)
        toolbar.addWidget(self.btn_save_prompt)
        
        layout.addLayout(toolbar)
        
        # External file path display (hidden by default)
        self.external_path_frame = QFrame()
        external_path_layout = QHBoxLayout(self.external_path_frame)
        external_path_layout.setContentsMargins(0, 0, 0, 4)
        external_path_layout.addWidget(QLabel("📂 Location:"))
        self.external_path_label = QLabel()
        self.external_path_label.setStyleSheet("color: #0066cc; text-decoration: underline;")
        self.external_path_label.setCursor(Qt.CursorShape.PointingHandCursor)
        self.external_path_label.setToolTip("Click to open containing folder")
        self.external_path_label.mousePressEvent = self._open_external_prompt_folder
        external_path_layout.addWidget(self.external_path_label, 1)
        self.btn_open_folder = QPushButton("📁 Open Folder")
        self.btn_open_folder.setMaximumWidth(100)
        self.btn_open_folder.clicked.connect(lambda: self._open_external_prompt_folder(None))
        external_path_layout.addWidget(self.btn_open_folder)
        self.external_path_frame.setVisible(False)
        layout.addWidget(self.external_path_frame)
        
        # Metadata fields
        metadata_layout = QHBoxLayout()
        
        # Name
        metadata_layout.addWidget(QLabel("Name:"))
        self.editor_name_input = QLineEdit()
        self.editor_name_input.setPlaceholderText("Prompt name")
        metadata_layout.addWidget(self.editor_name_input, 2)
        
        # Description
        metadata_layout.addWidget(QLabel("Description:"))
        self.editor_desc_input = QLineEdit()
        self.editor_desc_input.setPlaceholderText("Brief description")
        metadata_layout.addWidget(self.editor_desc_input, 3)
        
        layout.addLayout(metadata_layout)

        # QuickLauncher fields
        quicklauncher_layout = QHBoxLayout()

        quicklauncher_layout.addWidget(QLabel("QuickLauncher label:"))
        self.editor_quicklauncher_label_input = QLineEdit()
        self.editor_quicklauncher_label_input.setPlaceholderText("Label shown in QuickLauncher")
        quicklauncher_layout.addWidget(self.editor_quicklauncher_label_input, 2)

        self.editor_quicklauncher_in_grid_cb = CheckmarkCheckBox("Show in QuickLauncher (in-app)")
        quicklauncher_layout.addWidget(self.editor_quicklauncher_in_grid_cb, 2)

        self.editor_quicklauncher_in_quicklauncher_cb = CheckmarkCheckBox("Show in QuickLauncher (global)")
        quicklauncher_layout.addWidget(self.editor_quicklauncher_in_quicklauncher_cb, 1)

        layout.addLayout(quicklauncher_layout)

        # App selector + Read-only indicator row
        app_readonly_layout = QHBoxLayout()

        app_readonly_layout.addWidget(QLabel("App:"))
        from PyQt6.QtWidgets import QComboBox
        self.editor_app_combo = QComboBox()
        self.editor_app_combo.addItems(["Both", "Workbench only", "Trados only"])
        self.editor_app_combo.setToolTip("Which app(s) should show this prompt")
        self.editor_app_combo.setMaximumWidth(160)
        app_readonly_layout.addWidget(self.editor_app_combo)

        app_readonly_layout.addSpacing(16)

        self.editor_read_only_cb = CheckmarkCheckBox("Read-only")
        self.editor_read_only_cb.setToolTip("Read-only prompts cannot be edited. Uncheck to allow editing.")
        self.editor_read_only_cb.stateChanged.connect(self._on_read_only_toggled)
        self.editor_read_only_cb.setVisible(False)  # Only shown for prompts that have read_only=true
        app_readonly_layout.addWidget(self.editor_read_only_cb)

        app_readonly_layout.addStretch()
        layout.addLayout(app_readonly_layout)

        # Content editor
        self.editor_content = QPlainTextEdit()
        self.editor_content.setPlaceholderText("Enter prompt content here...")
        self.editor_content.setFont(QFont("Consolas", 10))
        layout.addWidget(self.editor_content)
        
        group.setLayout(layout)
        
        return group
    
    def _get_mode_display_name(self) -> str:
        """Get display name for current mode"""
        mode_names = {
            "single": "Single Segment",
            "batch_docx": "Batch DOCX",
            "batch_bilingual": "Batch Bilingual"
        }
        return mode_names.get(self.current_mode, "Single Segment")
    
    def _refresh_tree(self):
        """Refresh the library tree view"""
        tree_state = self._capture_prompt_tree_state()
        self.tree_widget.clear()
        
        self._build_tree_recursive(None, self.unified_library_dir, "")

        # Preserve user's expansion state across refreshes.
        if tree_state is None and not getattr(self, "_prompt_tree_state_initialized", False):
            self.tree_widget.collapseAll()
            self._prompt_tree_state_initialized = True
        else:
            self._restore_prompt_tree_state(tree_state)

    def _capture_prompt_tree_state(self) -> Optional[Dict[str, object]]:
        """Capture expansion + selection state for the Prompt Library tree."""
        if not hasattr(self, 'tree_widget') or not self.tree_widget:
            return None

        try:
            expanded_folders = set()
            expanded_special = {}

            current = self.tree_widget.currentItem()
            current_sel = None
            if current is not None:
                data = current.data(0, Qt.ItemDataRole.UserRole)
                if data and data.get('type') in {'prompt', 'folder'}:
                    current_sel = (data.get('type'), data.get('path'))

            # Scroll position (best-effort)
            scroll_val = None
            try:
                sb = self.tree_widget.verticalScrollBar()
                scroll_val = sb.value() if sb is not None else None
            except Exception:
                scroll_val = None

            def iter_items(parent: QTreeWidgetItem):
                for i in range(parent.childCount()):
                    child = parent.child(i)
                    yield child
                    yield from iter_items(child)

            root = self.tree_widget.invisibleRootItem()
            for item in iter_items(root):
                data = item.data(0, Qt.ItemDataRole.UserRole)
                if not data:
                    continue

                if data.get('type') == 'folder' and item.isExpanded():
                    path = data.get('path')
                    if path:
                        expanded_folders.add(path)

                if data.get('type') == 'special':
                    kind = data.get('kind')
                    if kind:
                        expanded_special[kind] = item.isExpanded()

            return {
                'expanded_folders': expanded_folders,
                'expanded_special': expanded_special,
                'current_sel': current_sel,
                'scroll_val': scroll_val,
            }
        except Exception:
            return None

    def _restore_prompt_tree_state(self, state: Optional[Dict[str, object]]):
        """Restore expansion + selection state for the Prompt Library tree."""
        if not state or not hasattr(self, 'tree_widget') or not self.tree_widget:
            return

        expanded_folders = state.get('expanded_folders', set()) or set()
        expanded_special = state.get('expanded_special', {}) or {}
        current_sel = state.get('current_sel')
        scroll_val = state.get('scroll_val')

        def iter_items(parent: QTreeWidgetItem):
            for i in range(parent.childCount()):
                child = parent.child(i)
                yield child
                yield from iter_items(child)

        # Restore expansions
        root = self.tree_widget.invisibleRootItem()
        for item in iter_items(root):
            data = item.data(0, Qt.ItemDataRole.UserRole)
            if not data:
                continue

            if data.get('type') == 'folder':
                p = data.get('path')
                if p in expanded_folders:
                    item.setExpanded(True)

            if data.get('type') == 'special':
                kind = data.get('kind')
                if kind in expanded_special:
                    item.setExpanded(bool(expanded_special[kind]))

        # Restore selection (prefer the main library tree item over Quick Run shortcuts)
        if current_sel and len(current_sel) == 2:
            sel_type, sel_path = current_sel
            if sel_type == 'prompt' and sel_path:
                self._select_and_reveal_prompt(sel_path, prefer_library_tree=True)
            elif sel_type == 'folder' and sel_path:
                self._select_and_reveal_folder(sel_path)

        # Restore scroll position (best-effort)
        if scroll_val is not None:
            try:
                sb = self.tree_widget.verticalScrollBar()
                if sb is not None:
                    sb.setValue(int(scroll_val))
            except Exception:
                pass

    def _get_selected_folder_for_new_prompt(self) -> str:
        """Return folder path where a new prompt should be created based on current selection."""
        try:
            item = self.tree_widget.currentItem() if hasattr(self, 'tree_widget') else None
            if not item:
                return "Project Prompts"

            data = item.data(0, Qt.ItemDataRole.UserRole)
            if not data:
                return "Project Prompts"

            if data.get('type') == 'folder':
                return data.get('path', 'Project Prompts') or 'Project Prompts'

            if data.get('type') == 'prompt':
                folder = str(Path(data.get('path', '')).parent)
                if folder and folder != '.':
                    return folder
                return "Project Prompts"

        except Exception:
            pass

        return "Project Prompts"

    def _select_and_reveal_prompt(self, relative_path: str, prefer_library_tree: bool = False):
        """Expand parent folders (if needed) and select the prompt item in the tree."""
        if not hasattr(self, 'tree_widget') or not self.tree_widget:
            return

        def iter_items(parent: QTreeWidgetItem):
            for i in range(parent.childCount()):
                child = parent.child(i)
                yield child
                yield from iter_items(child)

        def is_under_special(it: QTreeWidgetItem) -> bool:
            p = it.parent()
            while p is not None:
                d = p.data(0, Qt.ItemDataRole.UserRole)
                if d and d.get('type') == 'special':
                    return True
                p = p.parent()
            return False

        root = self.tree_widget.invisibleRootItem()
        best = None
        for item in iter_items(root):
            data = item.data(0, Qt.ItemDataRole.UserRole)
            if data and data.get('type') == 'prompt' and data.get('path') == relative_path:
                if prefer_library_tree and is_under_special(item):
                    continue
                best = item
                break

        if best is None and prefer_library_tree:
            # Fall back to any match
            for item in iter_items(root):
                data = item.data(0, Qt.ItemDataRole.UserRole)
                if data and data.get('type') == 'prompt' and data.get('path') == relative_path:
                    best = item
                    break

        if best is not None:
            p = best.parent()
            while p is not None:
                p.setExpanded(True)
                p = p.parent()
            self.tree_widget.setCurrentItem(best)
            self.tree_widget.scrollToItem(best)

    def _select_and_reveal_folder(self, folder_path: str):
        """Select a folder item in the tree and expand its ancestors."""
        if not hasattr(self, 'tree_widget') or not self.tree_widget:
            return

        def iter_items(parent: QTreeWidgetItem):
            for i in range(parent.childCount()):
                child = parent.child(i)
                yield child
                yield from iter_items(child)

        root = self.tree_widget.invisibleRootItem()
        for item in iter_items(root):
            data = item.data(0, Qt.ItemDataRole.UserRole)
            if data and data.get('type') == 'folder' and data.get('path') == folder_path:
                p = item.parent()
                while p is not None:
                    p.setExpanded(True)
                    p = p.parent()
                self.tree_widget.setCurrentItem(item)
                self.tree_widget.scrollToItem(item)
                return

    def _make_unique_filename(self, folder: str, filename: str) -> str:
        """Generate a unique filename in a folder by appending (copy N) to the stem."""
        folder = folder or ""
        base = Path(filename).stem
        suffix = Path(filename).suffix or ".md"

        def build(name_stem: str) -> str:
            return f"{folder}/{name_stem}{suffix}" if folder else f"{name_stem}{suffix}"

        candidate_stem = f"{base} (copy)"
        candidate = build(candidate_stem)
        if candidate not in self.library.prompts:
            return candidate

        n = 2
        while True:
            candidate_stem = f"{base} (copy {n})"
            candidate = build(candidate_stem)
            if candidate not in self.library.prompts:
                return candidate
            n += 1

    def _make_unique_folder_path(self, dest_folder: str, folder_name: str) -> str:
        """Generate a unique folder path under dest_folder by appending (moved N)."""
        dest_folder = dest_folder or ""

        def build(name: str) -> str:
            return f"{dest_folder}/{name}" if dest_folder else name

        candidate = build(folder_name)
        if not (self.library.library_dir / candidate).exists():
            return candidate

        n = 2
        while True:
            candidate = build(f"{folder_name} (moved {n})")
            if not (self.library.library_dir / candidate).exists():
                return candidate
            n += 1

    def _move_folder_to_folder(self, src_folder: str, dest_folder: str) -> bool:
        """Move a folder (directory) to another folder (drag-and-drop backend)."""
        if not src_folder:
            return False

        src_folder = src_folder.strip("/\\")
        dest_folder = (dest_folder or "").strip("/\\")

        src_name = Path(src_folder).name
        new_folder = f"{dest_folder}/{src_name}" if dest_folder else src_name

        # Prevent moving into itself/descendant
        if dest_folder and (dest_folder == src_folder or dest_folder.startswith(src_folder + "/")):
            QMessageBox.warning(self.main_widget, "Move not allowed", "You can't move a folder into itself.")
            return False

        if new_folder == src_folder:
            return False

        # Handle destination conflict
        if (self.library.library_dir / new_folder).exists():
            new_folder = self._make_unique_folder_path(dest_folder, src_name)

        if not self.library.move_folder(src_folder, new_folder):
            QMessageBox.warning(self.main_widget, "Move failed", "Could not move the folder.")
            return False

        # Reload prompts so keys/filepaths match new layout.
        self.library.load_all_prompts()

        # Ensure active prompt content is still valid after path rewrite
        if self.library.active_primary_prompt_path and self.library.active_primary_prompt_path in self.library.prompts:
            self.library.active_primary_prompt = self.library.prompts[self.library.active_primary_prompt_path].get('content')

        # Remove attachments that no longer exist (shouldn't happen, but safe)
        self.library.attached_prompt_paths = [p for p in self.library.attached_prompt_paths if p in self.library.prompts]

        self._refresh_tree()
        return True

    def _move_prompt_to_folder(self, src_path: str, dest_folder: str) -> bool:
        """Move a prompt file to another folder (drag-and-drop backend)."""
        if src_path not in self.library.prompts:
            return False

        filename = Path(src_path).name
        dest_folder = dest_folder or ""
        new_path = f"{dest_folder}/{filename}" if dest_folder else filename

        if new_path == src_path:
            return False

        # Handle name conflicts
        if new_path in self.library.prompts:
            new_path = self._make_unique_filename(dest_folder, filename)

        if not self.library.move_prompt(src_path, new_path):
            QMessageBox.warning(self.main_widget, "Move failed", "Could not move the prompt.")
            return False

        # Update folder metadata and rewrite frontmatter in the moved file.
        try:
            prompt_data = self.library.prompts.get(new_path, {}).copy()
            prompt_data['folder'] = dest_folder
            prompt_data['modified'] = datetime.now().strftime('%Y-%m-%d')
            self.library.save_prompt(new_path, prompt_data)
        except Exception:
            pass

        self.library.load_all_prompts()
        self._refresh_tree()
        self._select_and_reveal_prompt(new_path)
        return True

    def _collapse_prompt_library_tree(self):
        """Collapse all folders in the Prompt Library tree."""
        if hasattr(self, 'tree_widget') and self.tree_widget:
            self.tree_widget.collapseAll()

    def _expand_prompt_library_tree(self):
        """Expand all folders in the Prompt Library tree."""
        if hasattr(self, 'tree_widget') and self.tree_widget:
            self.tree_widget.expandAll()
    
    def _build_tree_recursive(self, parent_item, directory: Path, relative_path: str):
        """Recursively build tree structure"""
        if not directory.exists():
            return
            return
        
        # Get items sorted (folders first, then files)
        try:
            items = sorted(directory.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower()))
            pass  # items counted
        except Exception as e:
            self.log_message(f"❌ ERROR listing directory {directory}: {e}")
            return
        
        for item in items:
            if item.name.startswith('.') or item.name == '__pycache__':
                continue
            
            if item.is_dir():
                # Folder
                rel_path = str(Path(relative_path) / item.name) if relative_path else item.name
                folder_item = QTreeWidgetItem([f"📁 {item.name}"])
                folder_item.setData(0, Qt.ItemDataRole.UserRole, {'type': 'folder', 'path': rel_path})
                folder_item.setFlags(folder_item.flags() | Qt.ItemFlag.ItemIsDragEnabled | Qt.ItemFlag.ItemIsDropEnabled)
                
                if parent_item:
                    parent_item.addChild(folder_item)
                else:
                    self.tree_widget.addTopLevelItem(folder_item)
                
                # Recurse
                self._build_tree_recursive(folder_item, item, rel_path)
            
            elif item.suffix.lower() in ['.md', '.svprompt', '.txt']:
                # Prompt file (.md is preferred format, .svprompt/.txt legacy)
                rel_path = str(Path(relative_path) / item.name) if relative_path else item.name
                
                # Debug logging removed – was flooding terminal/session log
                
                if rel_path in self.library.prompts:
                    prompt_data = self.library.prompts[rel_path]
                    # Show full filename with extension in tree
                    name = item.name  # e.g., "prompt.md"
                    
                    prompt_item = QTreeWidgetItem([name])
                    prompt_item.setData(0, Qt.ItemDataRole.UserRole, {'type': 'prompt', 'path': rel_path})
                    prompt_item.setFlags(prompt_item.flags() | Qt.ItemFlag.ItemIsDragEnabled)
                    
                    # Visual indicators
                    indicators = []
                    if prompt_data.get('quicklauncher', prompt_data.get('quick_run', False)):
                        indicators.append("⚡")
                    if prompt_data.get('quicklauncher_grid', False):
                        indicators.append("🖱️")
                    if indicators:
                        prompt_item.setText(0, f"{' '.join(indicators)} {name}")

                    # Grey out default prompts
                    if prompt_data.get('default', False):
                        prompt_item.setForeground(0, QBrush(QColor(130, 130, 130)))
                    
                    if parent_item:
                        parent_item.addChild(prompt_item)
                    else:
                        self.tree_widget.addTopLevelItem(prompt_item)
                    
                else:
                    pass  # Prompt file not in library (may have invalid frontmatter)
    
    # v1.10.176: public API for the right-panel QStackedWidget that
    # hosts both the Prompt Editor (page 0) and the Image Context viewer
    # (page 1, installed by the parent app via set_image_context_widget).
    # See _create_prompt_library_tab for the stack construction.

    def set_image_context_widget(self, widget):
        """Install the parent app's Image Context widget as page 1 of
        the right-panel stack. Called once at startup from Supervertaler
        after the prompt manager is created."""
        if not hasattr(self, '_right_stack') or self._right_stack is None:
            return
        # Replace the placeholder at page 1 with the real widget.
        try:
            old = self._right_stack.widget(self._right_stack_image_index)
            if old is not None:
                self._right_stack.removeWidget(old)
                old.deleteLater()
        except Exception:
            pass
        self._right_stack.insertWidget(self._right_stack_image_index, widget)

    def show_prompt_editor_view(self):
        """Switch the right panel to the Prompt Editor page."""
        if hasattr(self, '_right_stack') and self._right_stack is not None:
            self._right_stack.setCurrentIndex(self._right_stack_editor_index)

    def show_image_context_view(self):
        """Switch the right panel to the Image Context page."""
        if hasattr(self, '_right_stack') and self._right_stack is not None:
            self._right_stack.setCurrentIndex(self._right_stack_image_index)

    def _on_tree_item_clicked(self, item, column):
        """Handle tree item click"""
        data = item.data(0, Qt.ItemDataRole.UserRole)

        if data and data.get('type') == 'prompt':
            self._load_prompt_in_editor(data['path'])
            # v1.10.176: clicking a prompt always brings the editor back
            # into view — so the user doesn't lose the prompt's content
            # behind the image-context viewer.
            self.show_prompt_editor_view()
    
    def _on_tree_item_double_clicked(self, item, column):
        """Handle tree item double-click - set as primary"""
        data = item.data(0, Qt.ItemDataRole.UserRole)
        
        if data and data.get('type') == 'prompt':
            self._set_primary_prompt(data['path'])
    
    def _show_tree_context_menu(self, position):
        """Show context menu for tree items"""
        item = self.tree_widget.itemAt(position)
        if not item:
            return
        
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if not data:
            return
        
        menu = QMenu()
        
        if data['type'] == 'prompt':
            path = data['path']
            
            # Set as custom prompt
            action_primary = menu.addAction("⭐ Set as Custom Prompt")
            action_primary.triggered.connect(lambda: self._set_primary_prompt(path))
            
            # Attach/detach
            if path in self.library.attached_prompt_paths:
                action_attach = menu.addAction("❌ Detach from Active")
                action_attach.triggered.connect(lambda: self._detach_prompt(path))
            else:
                action_attach = menu.addAction("📎 Attach to Active")
                action_attach.triggered.connect(lambda: self._attach_prompt(path))
            
            menu.addSeparator()

            # Toggle QuickLauncher (legacy: quick_run)
            prompt_data = self.library.prompts.get(path, {})
            if prompt_data.get('quicklauncher', prompt_data.get('quick_run', False)):
                action_qr = menu.addAction("⚡ Remove from QuickLauncher")
            else:
                action_qr = menu.addAction("⚡ Add to QuickLauncher")
            action_qr.triggered.connect(lambda: self._toggle_quick_run(path))

            # Toggle Grid right-click QuickLauncher
            if prompt_data.get('quicklauncher_grid', False):
                action_grid = menu.addAction("🖱️ Remove from Grid QuickLauncher")
            else:
                action_grid = menu.addAction("🖱️ Add to Grid QuickLauncher")
            action_grid.triggered.connect(lambda: self._toggle_quicklauncher_grid(path))
            
            menu.addSeparator()
            
            # Edit, duplicate, delete
            action_edit = menu.addAction("✏️ Edit")
            action_edit.triggered.connect(lambda: self._load_prompt_in_editor(path))
            
            action_dup = menu.addAction("📋 Duplicate")
            action_dup.triggered.connect(lambda: self._duplicate_prompt(path))

            action_del = menu.addAction("🗑️ Delete")
            action_del.triggered.connect(lambda: self._delete_prompt(path))
            # Disable delete for default prompts (they get recreated anyway)
            if prompt_data.get('default', False):
                action_del.setEnabled(False)
        
        elif data['type'] == 'folder':
            # Folder operations
            action_new_prompt = menu.addAction("+ New Prompt in Folder")
            action_new_prompt.triggered.connect(lambda: self._new_prompt_in_folder(data['path']))
            
            action_new_folder = menu.addAction("📁 New Subfolder")
            action_new_folder.triggered.connect(lambda: self._new_subfolder(data['path']))

            menu.addSeparator()

            action_del_folder = menu.addAction("🗑️ Delete Folder")
            action_del_folder.triggered.connect(lambda: self._delete_folder(data['path']))

        menu.exec(self.tree_widget.viewport().mapToGlobal(position))
    
    def _load_prompt_in_editor(self, relative_path: str):
        """Load prompt into editor for viewing/editing"""
        if relative_path not in self.library.prompts:
            return
        
        prompt_data = self.library.prompts[relative_path]
        
        # Show full filename with extension (e.g., "prompt.md")
        filename = Path(relative_path).name
        self.editor_name_label.setText(f"Editing: {filename}")
        self.editor_name_input.setText(filename)
        self.editor_desc_input.setText(prompt_data.get('description', ''))
        if hasattr(self, 'editor_quicklauncher_label_input'):
            self.editor_quicklauncher_label_input.setText(prompt_data.get('quicklauncher_label', '') or prompt_data.get('name', ''))
        if hasattr(self, 'editor_quicklauncher_in_grid_cb'):
            self.editor_quicklauncher_in_grid_cb.setChecked(bool(prompt_data.get('quicklauncher_grid', False)))
        if hasattr(self, 'editor_quicklauncher_in_quicklauncher_cb'):
            self.editor_quicklauncher_in_quicklauncher_cb.setChecked(bool(prompt_data.get('quicklauncher', prompt_data.get('quick_run', False))))
        # App selector
        if hasattr(self, 'editor_app_combo'):
            app_val = str(prompt_data.get('app', 'both')).lower().strip()
            idx_map = {'both': 0, 'workbench': 1, 'trados': 2}
            self.editor_app_combo.setCurrentIndex(idx_map.get(app_val, 0))
        self.editor_content.setPlainText(prompt_data.get('content', ''))

        # Handle read-only / default state
        is_default = bool(prompt_data.get('default', False))
        is_read_only = bool(prompt_data.get('read_only', False)) or is_default
        if hasattr(self, 'editor_read_only_cb'):
            self.editor_read_only_cb.blockSignals(True)
            self.editor_read_only_cb.setChecked(is_read_only)
            self.editor_read_only_cb.setVisible(not is_default)  # hide checkbox for defaults
            self.editor_read_only_cb.blockSignals(False)
        self._apply_read_only_state(is_read_only)

        if is_default:
            self.editor_name_label.setText(f"Editing: {filename}  (default prompt \u2013 use Duplicate to modify)")

        # Store current path for saving
        self.editor_current_path = relative_path
        self.btn_save_prompt.setEnabled(not is_read_only)

        # Hide external path display (this is a library prompt, not external)
        self.external_path_frame.setVisible(False)
        self._current_external_file_path = None
    
    def _save_current_prompt(self):
        """Save currently edited prompt"""
        try:
            name = self.editor_name_input.text().strip()
            description = self.editor_desc_input.text().strip()
            content = self.editor_content.toPlainText().strip()
            
            # Name field now represents the complete filename with extension
            # No stripping needed - user sees and edits the full filename

            quicklauncher_label = ''
            quicklauncher_grid = False
            quicklauncher_flag = False
            app_value = 'both'
            if hasattr(self, 'editor_quicklauncher_label_input'):
                quicklauncher_label = self.editor_quicklauncher_label_input.text().strip()
            if hasattr(self, 'editor_quicklauncher_in_grid_cb'):
                quicklauncher_grid = bool(self.editor_quicklauncher_in_grid_cb.isChecked())
            if hasattr(self, 'editor_quicklauncher_in_quicklauncher_cb'):
                quicklauncher_flag = bool(self.editor_quicklauncher_in_quicklauncher_cb.isChecked())
            if hasattr(self, 'editor_app_combo'):
                app_map = {0: 'both', 1: 'workbench', 2: 'trados'}
                app_value = app_map.get(self.editor_app_combo.currentIndex(), 'both')

            if not name or not content:
                QMessageBox.warning(self.main_widget, "Error", "Name and content are required")
                return

            # Block saving read-only prompts
            if hasattr(self, 'editor_read_only_cb') and self.editor_read_only_cb.isChecked():
                QMessageBox.information(self.main_widget, "Read-Only", "This prompt is read-only. Uncheck 'Read-only' to allow editing.")
                return

            # Check if this is a new prompt or editing existing
            if hasattr(self, 'editor_current_path') and self.editor_current_path:
                path = self.editor_current_path
                
                # Handle external prompts (save back to external file)
                if path.startswith("[EXTERNAL] "):
                    external_file_path = path[11:]  # Remove "[EXTERNAL] " prefix
                    self._save_external_prompt(external_file_path, name, description, content)
                    return
                
                # Editing existing library prompt
                if path not in self.library.prompts:
                    QMessageBox.warning(self.main_widget, "Error", "Prompt no longer exists")
                    return

                prompt_data = self.library.prompts[path].copy()
                old_filename = Path(path).name
                
                # Extract name without extension for metadata
                name_without_ext = Path(name).stem
                
                prompt_data['name'] = name_without_ext
                prompt_data['description'] = description
                prompt_data['content'] = content
                prompt_data['quicklauncher_label'] = quicklauncher_label or name_without_ext
                prompt_data['quicklauncher_grid'] = quicklauncher_grid
                prompt_data['quicklauncher'] = quicklauncher_flag
                prompt_data['app'] = app_value
                # Keep legacy field in sync
                prompt_data['quick_run'] = quicklauncher_flag
                
                # Check if filename changed - need to rename file
                if old_filename != name:
                    old_path = Path(path)
                    folder = str(old_path.parent) if old_path.parent != Path('.') else ''
                    new_path = f"{folder}/{name}" if folder else name
                    
                    # Delete old file and save to new location
                    if self.library.delete_prompt(path):
                        if self.library.save_prompt(new_path, prompt_data):
                            self.library.load_all_prompts()
                            self._refresh_tree()
                            self._select_and_reveal_prompt(new_path)
                            self.editor_current_path = new_path  # Update to new path
                            QMessageBox.information(self.main_widget, "Saved", f"Prompt renamed to '{name}' successfully!")
                            self.log_message(f"✓ Renamed prompt: {old_filename} → {name}")
                        else:
                            QMessageBox.warning(self.main_widget, "Error", "Failed to rename prompt")
                    else:
                        QMessageBox.warning(self.main_widget, "Error", "Failed to delete old prompt file")
                else:
                    # Name unchanged, just update in place
                    if self.library.save_prompt(path, prompt_data):
                        # Refresh active prompts if this prompt is currently active or attached
                        # This ensures "Preview Combined" shows the updated content immediately
                        if self.library.active_primary_prompt_path == path:
                            # Update cached primary prompt content
                            self.library.active_primary_prompt = self.library.prompts[path]['content']

                        if path in self.library.attached_prompt_paths:
                            # Update cached attached prompt content
                            idx = self.library.attached_prompt_paths.index(path)
                            self.library.attached_prompts[idx] = self.library.prompts[path]['content']

                        QMessageBox.information(self.main_widget, "Saved", "Prompt updated successfully!")
                        self._refresh_tree()
                        self._update_attached_list()  # Refresh attached list to show updated names
                    else:
                        QMessageBox.warning(self.main_widget, "Error", "Failed to save prompt")
            else:
                # Creating new prompt
                folder = getattr(self, 'editor_target_folder', 'Project Prompts')

                # Create new prompt data
                from datetime import datetime
                prompt_data = {
                    'name': name,
                    'description': description,
                    'content': content,
                    'category': '',
                    # QuickLauncher
                    'quicklauncher_label': quicklauncher_label or name,
                    'quicklauncher_grid': quicklauncher_grid,
                    'quicklauncher': quicklauncher_flag,
                    # Legacy
                    'quick_run': quicklauncher_flag,
                    'created': datetime.now().strftime('%Y-%m-%d'),
                    'modified': datetime.now().strftime('%Y-%m-%d')
                }

                # Create the prompt file (save_prompt creates new file if it doesn't exist)
                relative_path = f"{folder}/{name}.md"
                if self.library.save_prompt(relative_path, prompt_data):
                    QMessageBox.information(self.main_widget, "Created", f"Prompt '{name}' created successfully!")
                    self.library.load_all_prompts()  # Reload to get new prompt in memory
                    self._refresh_tree()
                    self.editor_current_path = relative_path  # Now editing this prompt
                else:
                    QMessageBox.warning(self.main_widget, "Error", "Failed to create prompt")
        
        except Exception as e:
            import traceback
            error_msg = f"Prompt save error: {str(e)}\n{traceback.format_exc()}"
            print(f"[ERROR] {error_msg}")
            self.log_message(f"❌ Prompt save error: {str(e)}")
            QMessageBox.critical(self.main_widget, "Save Error", f"Failed to save prompt:\n\n{str(e)}")
            return
    
    def _save_external_prompt(self, file_path: str, name: str, description: str, content: str):
        """Save changes to an external prompt file"""
        from pathlib import Path
        import json
        
        path = Path(file_path)
        
        try:
            if file_path.lower().endswith('.svprompt'):
                # Save as JSON format
                data = {
                    'name': name,
                    'description': description,
                    'content': content,
                    'version': '1.0'
                }
                path.write_text(json.dumps(data, indent=2), encoding='utf-8')
            else:
                # Save as plain text
                path.write_text(content, encoding='utf-8')
            
            # Update the library's active primary prompt content
            self.library.active_primary_prompt = content
            
            QMessageBox.information(self.main_widget, "Saved", f"External prompt '{name}' saved successfully!")
            self.log_message(f"✓ Saved external prompt: {name}")
            
        except Exception as e:
            QMessageBox.warning(self.main_widget, "Error", f"Failed to save external prompt: {e}")
    
    def _set_primary_prompt(self, relative_path: str):
        """Set prompt as primary"""
        if self.library.set_primary_prompt(relative_path):
            prompt_data = self.library.prompts[relative_path]
            self.primary_prompt_label.setText(prompt_data.get('name', 'Unnamed'))
            self.primary_prompt_label.setStyleSheet("color: #000; font-weight: bold;")
            self.log_message(f"✓ Set Custom Prompt ⭐: {prompt_data.get('name')}")
            # Also display in the editor
            self._load_prompt_in_editor(relative_path)
    
    def _attach_prompt(self, relative_path: str):
        """Attach prompt to active configuration"""
        if self.library.attach_prompt(relative_path):
            self._update_attached_list()
            prompt_data = self.library.prompts[relative_path]
            self.log_message(f"✓ Attached: {prompt_data.get('name')}")
    
    def _detach_prompt(self, relative_path: str):
        """Detach prompt from active configuration"""
        if self.library.detach_prompt(relative_path):
            self._update_attached_list()
            self.log_message(f"✓ Detached prompt")
    
    def _update_attached_list(self):
        """Update the attached prompts list widget"""
        self.attached_list_widget.clear()
        
        for path in self.library.attached_prompt_paths:
            if path in self.library.prompts:
                prompt_data = self.library.prompts[path]
                name = prompt_data.get('name', 'Unnamed')
                
                item = QTreeWidgetItem([name, "×"])
                item.setData(0, Qt.ItemDataRole.UserRole, path)
                
                self.attached_list_widget.addTopLevelItem(item)
    
    def _clear_primary_prompt(self):
        """Clear primary prompt selection"""
        self.library.active_primary_prompt = None
        self.library.active_primary_prompt_path = None
        self.primary_prompt_label.setText("[None selected]")
        self.primary_prompt_label.setStyleSheet("color: #999;")
        self.log_message("✓ Cleared custom prompt")
    
    def _load_external_primary_prompt(self):
        """Load an external prompt file (not in library) as primary"""
        file_path, _ = QFileDialog.getOpenFileName(
            self.main_widget,
            "Select External Prompt File",
            "",
            "Prompt Files (*.md *.svprompt *.txt);;Markdown Prompts (*.md);;Supervertaler Prompts (*.svprompt);;Text Files (*.txt);;All Files (*.*)"
        )
        
        if not file_path:
            return  # User cancelled
        
        success, result = self.library.set_external_primary_prompt(file_path)
        
        if success:
            # result is the display name
            self.primary_prompt_label.setText(f"📁 {result}")
            self.primary_prompt_label.setStyleSheet("color: #0066cc; font-weight: bold;")
            self.primary_prompt_label.setToolTip(f"External file: {file_path}")
            self.log_message(f"✓ Loaded external prompt: {result}")
            
            # Display the external prompt in the editor
            self._display_external_prompt_in_editor(file_path, result)
        else:
            # result is the error message
            QMessageBox.warning(self.main_widget, "Error", f"Could not load file: {result}")
    
    def _display_external_prompt_in_editor(self, file_path: str, display_name: str):
        """Display an external prompt file in the editor (read-only view)"""
        from pathlib import Path
        import json
        
        path = Path(file_path)
        
        try:
            content = path.read_text(encoding='utf-8')
            description = ""
            
            # Try to parse as JSON (.svprompt format)
            if file_path.lower().endswith('.svprompt'):
                try:
                    data = json.loads(content)
                    # Extract content and description from svprompt
                    content = data.get('content', content)
                    description = data.get('description', '')
                except json.JSONDecodeError:
                    pass  # Keep raw content
            
            # Update editor fields
            self.editor_name_label.setText(f"📁 External: {display_name}")
            self.editor_name_input.setText(display_name)
            self.editor_desc_input.setText(description)
            self.editor_content.setPlainText(content)

            # External prompts are never read-only in the editor
            if hasattr(self, 'editor_read_only_cb'):
                self.editor_read_only_cb.blockSignals(True)
                self.editor_read_only_cb.setChecked(False)
                self.editor_read_only_cb.setVisible(False)
                self.editor_read_only_cb.blockSignals(False)
            self._apply_read_only_state(False)

            # Store the external path for potential save operations
            self.editor_current_path = f"[EXTERNAL] {file_path}"
            self._current_external_file_path = file_path  # Store for folder opening
            self.btn_save_prompt.setEnabled(True)
            
            # Show the external path with clickable link
            self.external_path_label.setText(file_path)
            self.external_path_frame.setVisible(True)
            
        except Exception as e:
            self.log_message(f"⚠ Could not display prompt in editor: {e}")
    
    def _open_external_prompt_folder(self, event):
        """Open the folder containing the current external prompt file"""
        import subprocess
        import platform
        from pathlib import Path
        
        if not hasattr(self, '_current_external_file_path') or not self._current_external_file_path:
            return
        
        folder_path = Path(self._current_external_file_path).parent
        
        if not folder_path.exists():
            QMessageBox.warning(self.main_widget, "Folder Not Found", f"The folder no longer exists:\n{folder_path}")
            return
        
        try:
            if platform.system() == 'Windows':
                # Open folder and select the file
                subprocess.run(['explorer', '/select,', str(self._current_external_file_path)])
            elif platform.system() == 'Darwin':  # macOS
                subprocess.run(['open', '-R', str(self._current_external_file_path)])
            else:  # Linux
                subprocess.run(['xdg-open', str(folder_path)])
        except Exception as e:
            QMessageBox.warning(self.main_widget, "Error", f"Could not open folder: {e}")

    def _clear_all_attachments(self):
        """Clear all attached prompts"""
        self.library.clear_attachments()
        self._update_attached_list()
        self.log_message("✓ Cleared all attachments")
    
    def _on_read_only_toggled(self, state):
        """Handle read-only checkbox toggle in editor"""
        is_read_only = bool(state)
        self._apply_read_only_state(is_read_only)

        # Save the read_only flag immediately
        if hasattr(self, 'editor_current_path') and self.editor_current_path:
            path = self.editor_current_path
            if path in self.library.prompts:
                self.library.prompts[path]['read_only'] = is_read_only
                self.library.save_prompt(path, self.library.prompts[path])
                self.log_message(f"{'🔒' if is_read_only else '🔓'} Prompt read-only: {is_read_only}")

    def _apply_read_only_state(self, is_read_only: bool):
        """Enable or disable editor fields based on read-only state"""
        self.editor_name_input.setReadOnly(is_read_only)
        self.editor_desc_input.setReadOnly(is_read_only)
        self.editor_content.setReadOnly(is_read_only)
        self.btn_save_prompt.setEnabled(not is_read_only)
        if hasattr(self, 'editor_quicklauncher_label_input'):
            self.editor_quicklauncher_label_input.setReadOnly(is_read_only)
        if hasattr(self, 'editor_quicklauncher_in_grid_cb'):
            self.editor_quicklauncher_in_grid_cb.setEnabled(not is_read_only)
        if hasattr(self, 'editor_quicklauncher_in_quicklauncher_cb'):
            self.editor_quicklauncher_in_quicklauncher_cb.setEnabled(not is_read_only)

        # Visual feedback
        readonly_style = "background-color: #f5f5f5;" if is_read_only else ""
        self.editor_content.setStyleSheet(readonly_style)

    def _toggle_quick_run(self, relative_path: str):
        """Toggle QuickLauncher (future app menu) status (legacy name: quick_run)."""
        if self.library.toggle_quick_run(relative_path):
            self._refresh_tree()

    def _toggle_quicklauncher_grid(self, relative_path: str):
        """Toggle whether this prompt appears in the Grid right-click QuickLauncher."""
        if self.library.toggle_quicklauncher_grid(relative_path):
            self._refresh_tree()
    
    def _new_prompt(self):
        """Create new prompt in the currently selected folder."""
        self._new_prompt_in_folder(self._get_selected_folder_for_new_prompt())
    
    def _new_folder(self):
        """Create new folder"""
        name, ok = QInputDialog.getText(self.main_widget, "New Folder", "Enter folder name:")
        if ok and name:
            if self.library.create_folder(name):
                self._refresh_tree()
    
    def _new_prompt_in_folder(self, folder_path: str):
        """Create new prompt in specific folder"""
        name, ok = QInputDialog.getText(self.main_widget, "New Prompt", "Enter prompt filename with extension (e.g., prompt.md):")
        if not ok or not name:
            return

        # Ensure .md extension
        if not name.endswith(('.md', '.svprompt', '.txt')):
            name = f"{name}.md"
        
        # Extract name without extension for metadata
        name_without_ext = Path(name).stem
        
        # Create the prompt immediately
        from datetime import datetime
        prompt_data = {
            'name': name_without_ext,
            'description': '',
            'content': '# Your prompt content here\n\nProvide translation instructions...',
            'category': '',
            'quicklauncher_label': name_without_ext,
            'quicklauncher_grid': False,
            'quicklauncher': False,
            'quick_run': False,
            'created': datetime.now().strftime('%Y-%m-%d'),
            'modified': datetime.now().strftime('%Y-%m-%d')
        }

        # Create prompt file (name already includes extension)
        relative_path = f"{folder_path}/{name}" if folder_path else name
        
        if self.library.save_prompt(relative_path, prompt_data):
            self.library.load_all_prompts()  # Reload to get new prompt in memory
            self._refresh_tree()
            self._select_and_reveal_prompt(relative_path)
            self._load_prompt_in_editor(relative_path)
            self.btn_save_prompt.setEnabled(True)  # Ensure Save button is enabled for new prompt
            self.log_message(f"✓ Created new prompt '{name}' in folder: {folder_path}")
        else:
            QMessageBox.warning(self.main_widget, "Error", "Failed to create prompt")
    
    def _new_subfolder(self, parent_folder: str):
        """Create subfolder"""
        name, ok = QInputDialog.getText(self.main_widget, "New Subfolder", "Enter folder name:")
        if ok and name:
            full_path = str(Path(parent_folder) / name)
            if self.library.create_folder(full_path):
                self._refresh_tree()
    
    def _duplicate_prompt(self, relative_path: str):
        """Duplicate a prompt into the same folder with a unique filename."""
        if relative_path not in self.library.prompts:
            return

        src_data = self.library.prompts[relative_path].copy()
        src_name = src_data.get('name', Path(relative_path).stem)

        folder = str(Path(relative_path).parent)
        if folder == '.':
            folder = ''

        filename = Path(relative_path).name
        new_path = self._make_unique_filename(folder, filename)
        new_name = Path(new_path).stem

        src_data['name'] = new_name
        src_data['quick_run'] = False
        src_data['quicklauncher_grid'] = False
        src_data['quicklauncher'] = False
        src_data['folder'] = folder
        src_data['created'] = datetime.now().strftime('%Y-%m-%d')
        src_data['modified'] = datetime.now().strftime('%Y-%m-%d')

        if self.library.save_prompt(new_path, src_data):
            self.library.load_all_prompts()
            self._refresh_tree()
            self._select_and_reveal_prompt(new_path)
            self._load_prompt_in_editor(new_path)
            self.log_message(f"✓ Duplicated: {src_name} → {new_name}")
        else:
            QMessageBox.warning(self.main_widget, "Duplicate failed", "Failed to duplicate the prompt.")
    
    def _delete_prompt(self, relative_path: str):
        """Delete a prompt"""
        reply = QMessageBox.question(
            self.main_widget,
            "Delete Prompt",
            "Are you sure you want to delete this prompt?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )

        if reply == QMessageBox.StandardButton.Yes:
            if self.library.delete_prompt(relative_path):
                self._refresh_tree()
                self.log_message("✓ Prompt deleted")

    def _delete_folder(self, relative_path: str):
        """Delete a folder and all its contents"""
        folder_name = os.path.basename(relative_path)
        reply = QMessageBox.warning(
            self.main_widget,
            "Delete Folder",
            f"Are you sure you want to delete the folder '{folder_name}' and all prompts inside it?\n\nThis cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )

        if reply == QMessageBox.StandardButton.Yes:
            if self.library.delete_folder(relative_path):
                self._refresh_tree()
                self._update_attached_list()
                self.log_message(f"✓ Folder deleted: {folder_name}")
    
    def _refresh_library(self):
        """Reload library and refresh UI"""
        self.library.load_all_prompts()
        self._refresh_tree()
        self._update_attached_list()
        self.log_message("✓ Library refreshed")
    
    def _preview_combined_prompt(self):
        """Preview the combined prompt with actual segment text"""
        from PyQt6.QtWidgets import QDialog, QVBoxLayout, QTextEdit, QPushButton, QHBoxLayout, QMessageBox
        
        # Get current segment from the app
        current_segment = None
        current_segment_id = "Preview"
        source_lang = "Source Language"
        target_lang = "Target Language"
        
        # Try to get segment from main app
        if hasattr(self, 'parent_app') and self.parent_app:
            # Get languages if project loaded
            if hasattr(self.parent_app, 'current_project') and self.parent_app.current_project:
                source_lang = _resolve_lang_name(getattr(self.parent_app.current_project, 'source_lang', '')) or 'Source Language'
                target_lang = _resolve_lang_name(getattr(self.parent_app.current_project, 'target_lang', '')) or 'Target Language'
                
                # Try to get selected segment
                if hasattr(self.parent_app, 'table') and self.parent_app.table:
                    current_row = self.parent_app.table.currentRow()
                    if current_row >= 0:
                        # Map display row to actual segment index
                        actual_index = current_row
                        if hasattr(self.parent_app, 'grid_row_to_segment_index') and self.parent_app.grid_row_to_segment_index:
                            if current_row in self.parent_app.grid_row_to_segment_index:
                                actual_index = self.parent_app.grid_row_to_segment_index[current_row]
                        
                        # Get segment
                        if actual_index < len(self.parent_app.current_project.segments):
                            current_segment = self.parent_app.current_project.segments[actual_index]
                            current_segment_id = f"Segment {current_segment.id}"
                
                # Fallback to first segment if none selected
                if not current_segment and len(self.parent_app.current_project.segments) > 0:
                    current_segment = self.parent_app.current_project.segments[0]
                    current_segment_id = f"Example: Segment {current_segment.id}"
        
        # Get source text
        if current_segment:
            source_text = current_segment.source
        else:
            source_text = "{{SOURCE_TEXT}}"
            QMessageBox.information(
                self.main_widget,
                "No Segment Selected",
                "No segment is currently selected. Showing template with placeholder text.\n\n"
                "To see the actual prompt with your text, please select a segment first."
            )
        
        # Build combined prompt
        combined = self.build_final_prompt(source_text, source_lang, target_lang)
        
        # Build composition info
        composition_parts = []
        composition_parts.append(f"📍 Segment: {current_segment_id}")
        composition_parts.append(f"🌐 Languages: {source_lang} → {target_lang}")
        composition_parts.append(f"📏 Total prompt length: {len(combined):,} characters")
        
        if self.library.active_primary_prompt:
            composition_parts.append(f"✓ Custom prompt attached")
        
        if self.library.attached_prompts:
            composition_parts.append(f"✓ {len(self.library.attached_prompts)} additional prompt(s) attached")
        
        composition_text = "\n".join(composition_parts)
        
        # Create custom dialog with proper text editor
        dialog = QDialog(self.main_widget)
        dialog.setWindowTitle("🧪 Combined Prompt Preview")
        dialog.resize(900, 700)  # Larger default size
        
        layout = QVBoxLayout(dialog)
        
        # Info label
        info_label = QLabel(
            "<b>Complete Assembled Prompt</b><br>"
            "This is what will be sent to the AI (System Prompt + Custom Prompts + your text)<br><br>" +
            composition_text.replace("\n", "<br>")
        )
        info_label.setTextFormat(Qt.TextFormat.RichText)
        info_label.setWordWrap(True)
        info_label.setStyleSheet("padding: 10px; background-color: #e3f2fd; border-radius: 4px; margin-bottom: 10px;")
        layout.addWidget(info_label)
        
        # Text editor for preview
        text_edit = QTextEdit()
        text_edit.setPlainText(combined)
        text_edit.setReadOnly(True)
        text_edit.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        text_edit.setStyleSheet("font-family: 'Consolas', 'Courier New', monospace; font-size: 9pt;")
        layout.addWidget(text_edit, 1)  # Stretch factor 1
        
        # Close button
        button_layout = QHBoxLayout()
        button_layout.addStretch()
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(dialog.accept)
        close_btn.setStyleSheet("padding: 8px 20px; font-weight: bold;")
        button_layout.addWidget(close_btn)
        layout.addLayout(button_layout)
        
        dialog.exec()
    
    def _view_current_system_template(self):
        """View the current system prompt with option to edit in Settings.

        v1.10.160: replaced QMessageBox.setDetailedText with a proper
        resizable QDialog. QMessageBox's detail pane is a fixed-size
        widget that can't be enlarged, so a multi-page system prompt was
        unreadable inside a tiny scroll box. The new dialog opens at a
        sensible default size, can be resized freely, and shows the
        prompt content immediately rather than hidden behind a
        "Show Details" button.
        """
        template = self.get_system_template(self.current_mode)
        mode_name = self._get_mode_display_name()

        dialog = QDialog(self.main_widget)
        dialog.setWindowTitle(f"System Prompt: {mode_name}")
        dialog.resize(820, 600)

        v = QVBoxLayout(dialog)

        header = QLabel(
            f"<b>Current system prompt for {mode_name} mode.</b><br>"
            "Read-only here — use <b>Edit in Settings</b> below to change it."
        )
        header.setWordWrap(True)
        v.addWidget(header)

        viewer = QTextEdit()
        viewer.setReadOnly(True)
        viewer.setFont(QFont("Consolas", 9))
        viewer.setPlainText(template)
        v.addWidget(viewer, 1)

        buttons = QDialogButtonBox()
        edit_btn = buttons.addButton("Edit in Settings", QDialogButtonBox.ButtonRole.ActionRole)
        close_btn = buttons.addButton(QDialogButtonBox.StandardButton.Close)
        close_btn.setDefault(True)
        v.addWidget(buttons)

        # Wire up — Close just closes the dialog; Edit closes it and
        # navigates to the Settings → System Prompts tab.
        close_btn.clicked.connect(dialog.accept)
        edit_btn.clicked.connect(lambda: (dialog.accept(),
                                          self._open_system_prompts_settings()))

        dialog.exec()

    def _open_system_prompts_settings(self):
        """Open the Settings tab and switch to the System Prompts sub-tab.

        v1.10.160: replaced the hard-coded ``main_tabs.setCurrentIndex(4)``
        with a label-based lookup. Index 4 *was* Settings when this code
        was written, but the top-level tab list has since had SuperLookup,
        Clipboard Manager, and Voice inserted between AI and Settings.
        Settings is now at index 7 on the current build, but any future
        insertion would silently re-break the same way — so we look it
        up by label and stop hard-coding the index.
        """
        try:
            if hasattr(self.parent_app, 'main_tabs') and hasattr(self.parent_app, 'settings_tabs'):
                # 1. Locate the top-level Settings tab by label, not index.
                settings_idx = -1
                main_tabs = self.parent_app.main_tabs
                for i in range(main_tabs.count()):
                    if "Settings" in main_tabs.tabText(i):
                        settings_idx = i
                        break
                if settings_idx < 0:
                    QMessageBox.warning(
                        self.main_widget,
                        "Navigation Issue",
                        "Could not find the top-level Settings tab.\n\n"
                        "Please open Settings manually."
                    )
                    return
                main_tabs.setCurrentIndex(settings_idx)

                # 2. Switch to the System Prompts sub-tab (already
                #    label-based here; just kept consistent).
                target_index = -1
                for i in range(self.parent_app.settings_tabs.count()):
                    tab_text = self.parent_app.settings_tabs.tabText(i)
                    if "System Prompt" in tab_text:
                        target_index = i
                        break
                if target_index >= 0:
                    self.parent_app.settings_tabs.setCurrentIndex(target_index)
                else:
                    QMessageBox.warning(
                        self.main_widget,
                        "Navigation Issue",
                        "Found the Settings tab but not the System Prompts sub-tab.\n\n"
                        "Please switch to it manually."
                    )
            else:
                # Fallback message
                QMessageBox.information(
                    self.main_widget,
                    "System Prompts",
                    "System Prompts (Layer 1) are configured in Settings → System Prompts tab.\n\n"
                    "They are automatically selected based on the document type you're processing."
                )
        except Exception as e:
            import traceback
            error_msg = f"Error opening System Prompts settings: {str(e)}"
            print(f"[ERROR] {error_msg}")
            print(traceback.format_exc())
            QMessageBox.critical(
                self.main_widget,
                "Error",
                f"Failed to open System Prompts settings:\n\n{str(e)}\n\n"
                "Please manually navigate to Settings → System Prompts tab."
            )
    
    # === System Prompts Management ===
    
    def _load_system_templates(self):
        """Load system prompts from files"""
        import json

        # Priority 1: Load from system_prompts_layer1.json (user-saved edits)
        system_prompts_file = self.prompt_library_dir / "system_prompts_layer1.json"
        if system_prompts_file.exists():
            try:
                with open(system_prompts_file, 'r', encoding='utf-8') as f:
                    saved_prompts = json.load(f)
                for mode in ["single", "batch_docx", "batch_bilingual", "fuzzy_fixer", "autotagger"]:
                    if mode in saved_prompts and saved_prompts[mode].strip():
                        self.system_templates[mode] = saved_prompts[mode]
            except Exception as e:
                print(f"[WARNING] Failed to load system_prompts_layer1.json: {e}")

        # Priority 2: Load from old location if exists (migration support)
        system_templates_dir = self.prompt_library_dir / "1_System_Prompts"
        if system_templates_dir.exists():
            file_map = {
                "Single Segment Translation (system prompt).md": "single",
                "Batch DOCX Translation (system prompt).md": "batch_docx",
                "Batch Bilingual Translation (system prompt).md": "batch_bilingual"
            }

            for filename, mode in file_map.items():
                if mode not in self.system_templates:
                    filepath = system_templates_dir / filename
                    if filepath.exists():
                        self.system_templates[mode] = filepath.read_text(encoding='utf-8')

        # Priority 3: Fill missing with defaults
        for mode in ["single", "batch_docx", "batch_bilingual"]:
            if mode not in self.system_templates:
                self.system_templates[mode] = self._get_default_system_template(mode)

        # FuzzyFixer instruction (editable, separate from the per-document
        # system prompts). Filled with its own default when not user-edited.
        if "fuzzy_fixer" not in self.system_templates:
            self.system_templates["fuzzy_fixer"] = self._get_default_fuzzy_fixer_template()

        # AutoTagger instruction (editable). Used to place inline tags into an
        # already-translated target; filled with its default when not user-edited.
        if "autotagger" not in self.system_templates:
            self.system_templates["autotagger"] = self._get_default_autotagger_template()

    def _get_default_autotagger_template(self) -> str:
        """Default editable instruction used by the AutoTagger feature.

        Placeholders: {{SOURCE_TEXT}} (source WITH tags), {{TARGET_TEXT}} (target
        with no tags), {{TAG_LIST}} (the ordered list of source tags).
        """
        return (
            "The source segment below contains inline tags. Insert these exact "
            "tags into the target translation at the positions that match the "
            "source meaning (a paired tag must wrap the translated word(s) it "
            "wrapped in the source).\n\n"
            "STRICT RULES:\n"
            "- Use every tag exactly once; keep each tag's text, id and count "
            "identical to the source.\n"
            "- Keep opening/closing tags correctly paired; never output an empty "
            "pair (e.g. <1></1>).\n"
            "- DO NOT change, add, remove, translate, reorder, or re-spell any "
            "WORDS of the target. Only insert tags between the existing words.\n"
            "- Output ONLY the tagged target text, with no labels or commentary.\n\n"
            "Tags to place: {{TAG_LIST}}\n\n"
            "Source (with tags):\n{{SOURCE_TEXT}}\n\n"
            "Target (no tags):\n{{TARGET_TEXT}}"
        )

    def get_autotagger_template(self) -> str:
        """Get the (possibly user-edited) AutoTagger instruction template."""
        return self.system_templates.get("autotagger") or self._get_default_autotagger_template()

    def _get_default_fuzzy_fixer_template(self) -> str:
        """Default editable instruction used by the FuzzyFixer feature.

        Placeholders: {{TM_SOURCE}}, {{TM_TARGET}}, {{MATCH_PCT}}, {{TM_NAME}},
        {{SOURCE_TEXT}}.
        """
        return (
            "A {{MATCH_PCT}}% fuzzy match was found in TM '{{TM_NAME}}'. The current "
            "source differs slightly from the matched source, so the existing target "
            "is not an exact translation.\n\n"
            "Adapt the existing target to the current source. Make the MINIMUM edits "
            "needed for a correct, fluent translation: keep the existing wording, "
            "terminology, tags and formatting wherever the source is unchanged, and "
            "only change the parts that the source change requires. Output ONLY the "
            "adapted translation, with no labels or commentary.\n\n"
            "Matched source:\n{{TM_SOURCE}}\n\n"
            "Existing target (to adapt):\n{{TM_TARGET}}"
        )

    def get_fuzzy_fixer_template(self) -> str:
        """Get the (possibly user-edited) FuzzyFixer instruction template."""
        return self.system_templates.get("fuzzy_fixer") or self._get_default_fuzzy_fixer_template()

    def _get_default_system_template(self, mode: str) -> str:
        """Get default system prompt for a mode"""
        # Comprehensive system prompt with detailed CAT tag instructions
        return """# SYSTEM PROMPT

⚠️ **PROFESSIONAL TRANSLATION CONTEXT:**
You are performing professional translation work. The source text may contain specialized terminology from any domain (medical, legal, technical, financial, etc.). Translate all content faithfully and accurately regardless of subject matter.

You are an expert {{SOURCE_LANGUAGE}} to {{TARGET_LANGUAGE}} translator with deep understanding of context and nuance.

**YOUR TASK**: Translate the text below.

**IMPORTANT INSTRUCTIONS**:
- Provide ONLY the translated text
- Do NOT include numbering, labels, or commentary
- Do NOT repeat the source text
- Maintain accuracy and natural fluency

**CRITICAL: INLINE FORMATTING TAG PRESERVATION**:
- Source text may contain simple HTML-style formatting tags: <b>bold</b>, <i>italic</i>, <u>underline</u>
- These tags represent text formatting that MUST be preserved in the translation
- Place the tags around the CORRESPONDING translated words, not necessarily in the same position
- Example: "Click the <b>Save</b> button" → "Klik op de knop <b>Opslaan</b>"
- Ensure every opening tag has a matching closing tag
- Never omit, add, or modify tags - preserve the exact same tags from source

**CRITICAL: CAT TOOL TAG PRESERVATION**:
- Source may contain CAT tool formatting tags in various formats:
  • memoQ: [1}, {2], [3}, {4] (asymmetric bracket-brace pairs)
  • Trados Studio: <410>text</410>, <434>text</434> (XML-style opening/closing tags)
  • CafeTran: |formatted text| (pipe symbols mark formatted text - bold, italic, underline, etc.)
  • Other CAT tools: various bracketed or special character sequences
- These are placeholder tags representing formatting (bold, italic, links, etc.)
- PRESERVE ALL tags - if source has N tags, target must have exactly N tags
- Keep tags with their content and adjust position for natural target language word order
- Never translate, omit, or modify the tags themselves - only reposition them
- Examples:
  • memoQ: '[1}De uitvoer{2]' → '[1}The exports{2]'
  • Trados: '<410>De uitvoer van machines</410>' → '<410>Exports of machinery</410>'
  • CafeTran: 'He debuted against |Juventus FC| in 2001' → 'Hij debuteerde tegen |Juventus FC| in 2001'
  • Multiple: '[1}De uitvoer{2] [3}stelt niets voor{4]' → '[1}Exports{2] [3}mean nothing{4]'

**LANGUAGE-SPECIFIC NUMBER FORMATTING**:
- If the target language is **Dutch**, **French**, **German**, **Italian**, **Spanish**, or another **continental European language**, use a **comma** as the decimal separator and a **space or non-breaking space** between the number and unit (e.g., 17,1 cm).
- If the target language is **English** or **Irish**, use a **full stop (period)** as the decimal separator and **no space** before the unit (e.g., 17.1 cm).
- Always follow the **number formatting conventions** of the target language.

If the text refers to figures (e.g., 'Figure 1A'), relevant images may be provided for visual context.

{{SOURCE_LANGUAGE}} text:
{{SOURCE_TEXT}}"""
    
    def get_system_template(self, mode: str) -> str:
        """Get system prompt for specified mode"""
        return self.system_templates.get(mode, self._get_default_system_template(mode))
    
    def set_mode(self, mode: str):
        """Set current translation mode (single, batch_docx, batch_bilingual)"""
        if mode in ["single", "batch_docx", "batch_bilingual"]:
            self.current_mode = mode
            if hasattr(self, 'mode_label'):
                self.mode_label.setText(f"Mode: {self._get_mode_display_name()}")
    
    def update_image_context_display(self):
        """Update the Image Context label in Active Configuration panel"""
        if not hasattr(self, 'image_context_label'):
            return
            
        # Check if parent app has figure_context
        if hasattr(self, 'parent_app') and self.parent_app:
            if hasattr(self.parent_app, 'figure_context') and self.parent_app.figure_context:
                fc = self.parent_app.figure_context
                if fc.has_images():
                    count = fc.get_image_count()
                    folder_name = fc.get_folder_name() or "folder"
                    self.image_context_label.setText(f"✅ {count} image{'s' if count != 1 else ''} from: {folder_name}")
                    self.image_context_label.setStyleSheet("color: #4CAF50; font-weight: bold;")
                    return
        
        # No images loaded
        self.image_context_label.setText("[None loaded]")
        self.image_context_label.setStyleSheet("color: #999;")
    
    # === Prompt Composition (for translation) ===
    
    def build_final_prompt(self, source_text: str, source_lang: str, target_lang: str,
                           mode: str = None, glossary_terms: list = None,
                           target_text: str = "", fuzzy_match: dict = None) -> str:
        """
        Build final prompt for translation using 2-layer architecture:
        1. System Prompt (auto-selected by mode)
        2. Combined prompts from library (primary + attached)
        3. Glossary terms (optional, injected before translation delimiter)
        4. FuzzyFixer reference (optional, injected before translation delimiter)

        Args:
            source_text: Text to translate
            source_lang: Source language
            target_lang: Target language
            mode: Override mode (if None, uses self.current_mode)
            glossary_terms: Optional list of term dicts with 'source_term' and 'target_term' keys
            target_text: Optional current target text (for {{TARGET_TEXT}} placeholder)
            fuzzy_match: Optional dict with 'source', 'target', 'match_pct', 'tm_name'.
                When provided, the editable FuzzyFixer instruction is rendered and
                appended so the AI adapts the existing TM target to the new source.

        Returns:
            Complete prompt ready for LLM
        """
        if mode is None:
            mode = self.current_mode

        # Layer 1: System Prompt
        system_template = self.get_system_template(mode)

        # Replace placeholders in system prompt
        system_template = system_template.replace("{{SOURCE_LANGUAGE}}", source_lang)
        system_template = system_template.replace("{{TARGET_LANGUAGE}}", target_lang)
        system_template = system_template.replace("{{SOURCE_TEXT}}", source_text)
        system_template = system_template.replace("{{TARGET_TEXT}}", target_text or "")

        # Layer 2: Library prompts (primary + attached)
        library_prompts = ""

        if self.library.active_primary_prompt:
            library_prompts += "\n\n# CUSTOM PROMPT\n\n"
            library_prompts += self.library.active_primary_prompt

        for attached_content in self.library.attached_prompts:
            library_prompts += "\n\n# ADDITIONAL INSTRUCTIONS\n\n"
            library_prompts += attached_content

        # Combine
        final_prompt = system_template + library_prompts

        # Glossary injection (if terms provided). Only inject terms that are
        # actually present in this segment — sending an entire termbase with
        # every request crowds the prompt with irrelevant terminology, which
        # dilutes the AI and wastes tokens (mirrors the Trados plugin).
        if glossary_terms:
            glossary_terms = filter_relevant_glossary_terms(glossary_terms, source_text)
        if glossary_terms:
            final_prompt += "\n\n# TERMBASE\n\n"
            final_prompt += "Use these approved terms in your translation:\n\n"
            for term in glossary_terms:
                source_term = term.get('source_term', '')
                target_term = term.get('target_term', '')
                if source_term and target_term:
                    # Mark forbidden terms
                    if term.get('forbidden'):
                        final_prompt += f"- {source_term} → ⚠️ DO NOT USE: {target_term}\n"
                    else:
                        final_prompt += f"- {source_term} → {target_term}\n"

        # FuzzyFixer injection (if a fuzzy TM match is provided). Renders the
        # editable fuzzy_fixer instruction template with the match details so the
        # AI adapts the existing translation to the new source with minimal edits.
        if fuzzy_match and fuzzy_match.get('source') and fuzzy_match.get('target'):
            instruction = self.get_fuzzy_fixer_template()
            try:
                match_pct = int(round(float(fuzzy_match.get('match_pct', 0) or 0)))
            except (TypeError, ValueError):
                match_pct = 0
            instruction = instruction.replace("{{TM_SOURCE}}", fuzzy_match.get('source', ''))
            instruction = instruction.replace("{{TM_TARGET}}", fuzzy_match.get('target', ''))
            instruction = instruction.replace("{{MATCH_PCT}}", str(match_pct))
            instruction = instruction.replace("{{TM_NAME}}", fuzzy_match.get('tm_name', '') or 'TM')
            instruction = instruction.replace("{{SOURCE_TEXT}}", source_text)
            final_prompt += "\n\n# FUZZY TM MATCH\n\n"
            final_prompt += instruction

        # Add translation delimiter
        final_prompt += "\n\n**YOUR TRANSLATION (provide ONLY the translated text, no numbering or labels):**\n"

        return final_prompt
    
    # ============================================================================
    # AI ASSISTANT METHODS
    # ============================================================================
    
    def _init_llm_client(self):
        """Initialize LLM client – delegates to ChatBackend."""
        self.chat_backend.init_llm_client()

    def _load_conversation_history(self):
        """Load conversation – handled by ChatBackend on init."""
        pass

    def _save_conversation_history(self):
        """Save conversation – handled by ChatBackend."""
        self.chat_backend._save_history()

    def _load_persisted_attachments(self):
        """Load attached files from AttachmentManager"""
        try:
            # Load files from current session
            files = self.attachment_manager.list_session_files()

            # Populate attached_files for backward compatibility
            for file_meta in files:
                # Get full file data including content
                file_data = self.attachment_manager.get_file(file_meta['file_id'])
                if file_data:
                    # Convert to old format for compatibility
                    self.attached_files.append({
                        'path': file_data.get('original_path', ''),
                        'name': file_data.get('original_name', ''),
                        'content': file_data.get('content', ''),
                        'type': file_data.get('file_type', ''),
                        'size': file_data.get('size_chars', 0),
                        'attached_at': file_data.get('attached_at', ''),
                        'file_id': file_data.get('file_id', '')  # Keep ID for reference
                    })

            if files:
                self.log_message(f"✓ Loaded {len(files)} attached files from session")

        except Exception as e:
            self.log_message(f"⚠ Failed to load persisted attachments: {e}")

    def _analyze_and_generate(self):
        """Analyze current project and generate a comprehensive domain-specific translation prompt."""
        if not self.llm_client:
            self._add_chat_message(
                "system",
                "⚠ AI Assistant not available. Please configure API keys in Settings."
            )
            return

        self._add_chat_message(
            "system",
            "🔍 Analyzing project and generating prompt...\n\n"
            "Phase 1: Document analysis (domain, tone, structure)\n"
            "Phase 2: Gathering terminology and TM data\n"
            "Phase 3: Building domain-specific prompt template\n"
            "Phase 4: Sending to AI for prompt generation"
        )

        # Phase 1: Document Analysis. DocumentAnalyzer gives us the factual
        # signals (word counts, structure, currencies/dates, terminology);
        # the document's DOMAIN and text-type description now come from the
        # AI (Phase 1a), which reads the actual text instead of counting
        # keywords — far more reliable and language-agnostic.
        analysis = self._run_document_analysis()

        # Phase 1a: AI context detection. Ask the model to read a sample of
        # the source and classify the domain + describe the text type. This
        # replaces the old keyword heuristics for BOTH domain and tone (the
        # keyword tone read used to contradict the domain, e.g. calling a
        # marketing campaign "technical, formal" because it's figure-heavy).
        detected_domain, context_description = self._classify_document_context()
        self.log_message(
            f"[AI Assistant] AI-detected domain: {detected_domain}"
            + (f" ({context_description})" if context_description else "")
        )

        # Build the analysis summary for the meta-prompt: the AI's context
        # description (authoritative text-type read) plus the factual stats
        # DocumentAnalyzer still provides. No keyword tone line — the AI's
        # description now carries the tone/register signal.
        analysis_summary = ""
        if analysis.get('success'):
            stats = analysis.get('statistics', {})
            structure = analysis.get('structure', {})
            special = analysis.get('special_elements', {})
            summary_lines = []
            if context_description:
                summary_lines.append(f"Context: {context_description}")
            summary_lines.append(
                f"Words: {stats.get('total_words', 0):,}, Unique words: {stats.get('unique_words', 0):,}"
            )
            summary_lines.append(
                f"Avg segment length: {stats.get('average_words_per_segment', 0):.1f} words"
            )
            summary_lines.append(
                f"Structure: {structure.get('list_items', 0)} list items, "
                f"{structure.get('potential_headings', 0)} headings, "
                f"{structure.get('figure_references', 0)} figure refs"
            )
            summary_lines.append(
                f"Special: {special.get('measurements', 0)} measurements, "
                f"{special.get('currencies', 0)} currencies, {special.get('dates', 0)} dates"
            )
            analysis_summary = "\n".join(summary_lines)

        # Phase 1b: Optional steering. The detection is automatic; this
        # lightweight dialog just lets a translator who wants to correct the
        # domain or add a short briefing do so. Most users press Generate
        # and move on. Cancel aborts.
        ctx_dialog = _AutoPromptContextDialog(
            detected_domain, context_description, parent=self.main_widget
        )
        if ctx_dialog.exec() != QDialog.DialogCode.Accepted:
            self._add_chat_message("system", "AutoPrompt cancelled.")
            return
        chosen_domain = ctx_dialog.get_domain()
        user_context_hint = ctx_dialog.get_context_hint()
        if chosen_domain and chosen_domain != detected_domain:
            self.log_message(
                f"[AI Assistant] Domain overridden by user: "
                f"{detected_domain} -> {chosen_domain}"
            )
            detected_domain = chosen_domain
        if user_context_hint:
            self.log_message("[AI Assistant] Translator context briefing supplied")

        # Phase 1c: Multi-file analysis (per-file tone detection)
        file_manifest = ""
        project = getattr(self.parent_app, 'current_project', None)
        if project:
            file_manifest = self._build_file_manifest(project)
            if file_manifest:
                self.log_message(f"[AI Assistant] Multi-file project: {len(project.files)} files analyzed")

        # Phase 2: Gather data
        context = self._build_project_context()
        terminology_table, term_count, has_forbidden = self._gather_full_terminology()
        tm_pairs = self._gather_tm_reference_pairs()
        self.log_message(f"[AI Assistant] Gathered {term_count} terms, TM pairs ready")

        # Phase 3: Get domain template
        template = self._get_domain_template(detected_domain)

        # Phase 4: Get language info
        source_lang = "Source Language"
        target_lang = "Target Language"
        segment_count = 0

        if project:
            if hasattr(project, 'source_lang') and project.source_lang:
                source_lang = _resolve_lang_name(project.source_lang)
            elif hasattr(project, 'source_language') and project.source_language:
                source_lang = _resolve_lang_name(project.source_language)
            if hasattr(project, 'target_lang') and project.target_lang:
                target_lang = _resolve_lang_name(project.target_lang)
            elif hasattr(project, 'target_language') and project.target_language:
                target_lang = _resolve_lang_name(project.target_language)
            if hasattr(project, 'segments') and project.segments:
                segment_count = len(project.segments)

        # Phase 4.5: Run source-aware pre-generation passes.  These extract
        # concrete document-specific data (real defects, real cascades, real
        # collisions) so the generated prompt is anchored in this source
        # rather than generic patent-translation scaffolding.  Each pass
        # returns an empty string when it finds nothing, in which case
        # _build_enhanced_analysis_prompt simply omits the corresponding
        # section from the meta-prompt.
        source_text = self._get_source_text_for_analysis()
        terminology_collisions = self._detect_terminology_collisions(source_text)
        source_defects = self._detect_source_defects(source_text)
        source_cascades = self._extract_source_cascades(source_text)
        include_legal_entity_scaffolding = self._detect_legal_entity_markers(source_text)

        if terminology_collisions:
            self.log_message(f"[AI Assistant] Terminology collisions detected — injecting into meta-prompt")
        if source_defects:
            self.log_message(f"[AI Assistant] Source defects detected — injecting verbatim examples")
        if source_cascades:
            self.log_message(f"[AI Assistant] Preference cascades extracted from source")
        if detected_domain == 'patent':
            self.log_message(f"[AI Assistant] Patent domain — applying patent framing")
        if not include_legal_entity_scaffolding:
            self.log_message(f"[AI Assistant] No legal-entity markers in source — omitting BV/NV/Meester scaffolding")

        # Phase 5: Build enhanced meta-prompt and send
        analysis_prompt = self._build_enhanced_analysis_prompt(
            context=context,
            analysis_summary=analysis_summary,
            detected_domain=detected_domain,
            template=template,
            terminology_table=terminology_table,
            term_count=term_count,
            has_forbidden=has_forbidden,
            tm_pairs=tm_pairs,
            source_lang=source_lang,
            target_lang=target_lang,
            segment_count=segment_count,
            file_manifest=file_manifest,
            terminology_collisions=terminology_collisions,
            source_defects=source_defects,
            source_cascades=source_cascades,
            include_legal_entity_scaffolding=include_legal_entity_scaffolding,
            user_context_hint=user_context_hint,
        )

        # v1.10.178: optional vision-aware AutoPrompt. If the user has
        # ticked "Include loaded figure images" in Section 2 AND there
        # are images loaded in Section 4 AND the active model supports
        # vision, attach the figure images to the meta-prompt so the
        # LLM can ground its terminology decisions visually.
        autoprompt_images = self._collect_autoprompt_images(analysis_prompt)
        if autoprompt_images is None:
            # User didn't opt in (or pre-conditions failed silently) —
            # run text-only as before.
            self._send_ai_request(analysis_prompt, is_analysis=True)
        elif not autoprompt_images.get("images"):
            # Sentinel: user explicitly cancelled in the confirmation
            # dialog. Abort AutoPrompt entirely.
            self.log_message("[AI Assistant] AutoPrompt aborted by user (image-context confirmation cancelled).")
            self._add_chat_message(
                "system",
                "⚠ AutoPrompt cancelled. Re-run after switching to a vision-capable "
                "model, or untick 'Include loaded figure images' to run text-only."
            )
        else:
            # Augment the meta-prompt with a section telling the LLM
            # that the images are present and how to use them.
            analysis_prompt = self._inject_image_context_directive(
                analysis_prompt, autoprompt_images["refs"]
            )
            self._send_ai_request(
                analysis_prompt, is_analysis=True,
                images=autoprompt_images["images"]
            )

    def _collect_autoprompt_images(self, analysis_prompt: str):
        """v1.10.178: Gather figure images for vision-aware AutoPrompt.

        Returns ``None`` if the user hasn't opted in, no images are
        loaded, or the active model can't see images. Otherwise returns
        a dict ``{"images": [...], "refs": [...]}`` with the payload in
        the provider-appropriate format and a sorted list of figure
        references for the meta-prompt directive.

        The user-facing flow:
        - User ticks the "🖼️ Include loaded figure images" checkbox in
          Section 2 (off by default).
        - User clicks "✨ AutoPrompt".
        - Before the LLM call, this method runs and surfaces a single
          confirmation dialog summarising what's about to be sent (image
          count, vision model name, rough cost ballpark). Cancel here
          aborts AutoPrompt entirely; OK proceeds with images attached.

        On any pre-condition failure (no parent_app, no figure_context,
        no images loaded, no llm_client, non-vision model) we silently
        return None and let AutoPrompt run text-only as before.
        """
        # Gate 1: user opted in?
        try:
            checkbox = getattr(self, "_autoprompt_include_images", None)
            if checkbox is None or not checkbox.isChecked():
                return None
        except Exception:
            return None

        # Gate 2: Section 4 has images loaded?
        parent_app = getattr(self, "parent_app", None)
        figure_context = getattr(parent_app, "figure_context", None) if parent_app else None
        if not figure_context or not figure_context.has_images():
            QMessageBox.information(
                self.main_widget,
                "No Figure Images Loaded",
                "The 'Include loaded figure images' option is ticked but no "
                "images are currently loaded.\n\n"
                "Use Section 4 → 'Open ▸' to load images first, or untick "
                "the option to run AutoPrompt text-only."
            )
            return None

        # Gate 3: active model supports vision?
        try:
            from modules.llm_clients import LLMClient
            llm_client = getattr(self.chat_backend, "llm_client", None)
            if not llm_client:
                return None
            provider = llm_client.provider
            model = llm_client.model
            if not LLMClient.model_supports_vision(provider, model):
                resp = QMessageBox.warning(
                    self.main_widget,
                    "Active model doesn't support vision",
                    f"The active AI model — <b>{provider} / {model}</b> — does "
                    "not accept image input.\n\n"
                    "AutoPrompt will run text-only (images skipped).\n\n"
                    "To send images, switch to a vision-capable model "
                    "(Claude Sonnet/Opus 4.x, GPT-4o or newer, Gemini) in "
                    "Settings first, then re-run AutoPrompt.",
                    QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel
                )
                if resp == QMessageBox.StandardButton.Cancel:
                    # User wants to abort and switch models first.
                    return {"images": [], "refs": []}  # sentinel: don't run at all
                return None  # proceed text-only
        except Exception as e:
            self.log_message(f"[AI Assistant] Vision-model check failed: {e} — running text-only")
            return None

        # Collect images in provider-appropriate format.
        try:
            image_map = figure_context.figure_context_map  # ref -> PIL.Image
            refs = sorted(image_map.keys(), key=lambda r: (len(r), r))
            if not refs:
                return None

            if provider == "gemini":
                # Gemini accepts PIL.Image directly.
                images_payload = [(ref, image_map[ref]) for ref in refs]
            else:
                # OpenAI / Claude: base64 PNG.
                from modules.figure_context_manager import pil_image_to_base64_png
                images_payload = [
                    (ref, pil_image_to_base64_png(image_map[ref]))
                    for ref in refs
                ]
        except Exception as e:
            self.log_message(f"[AI Assistant] Could not encode figure images: {e} — running text-only")
            return None

        # Confirmation dialog with a rough cost ballpark. Image-token
        # cost varies by provider — these are realistic order-of-
        # magnitude figures, not invoices.
        n = len(refs)
        # Per-image input-token estimate × per-million input price.
        # Sonnet ~1.5k tok/img @ $3/M → $0.0045/img.
        # Opus  ~1.5k tok/img @ $15/M → $0.0225/img.
        # GPT-4o ~1.2k tok/img @ $2.50/M → $0.0030/img.
        # Gemini Pro very cheap.
        provider_lc = provider.lower() if provider else ""
        model_lc = model.lower() if model else ""
        if "opus" in model_lc:
            per_img_cost = 0.0225
        elif "sonnet" in model_lc or "claude" in provider_lc:
            per_img_cost = 0.0045
        elif "gemini" in provider_lc:
            per_img_cost = 0.001
        else:  # GPT-4o family + generic fallback
            per_img_cost = 0.0030
        est_lo = max(0.01, per_img_cost * n * 0.7)
        est_hi = per_img_cost * n * 1.5
        cost_blurb = f"${est_lo:.2f}–${est_hi:.2f}"

        confirm = QMessageBox.question(
            self.main_widget,
            "Include figure images in AutoPrompt?",
            f"<p>About to send <b>{n} figure image{'s' if n != 1 else ''}</b> "
            f"to <b>{provider} / {model}</b> alongside the AutoPrompt meta-prompt.</p>"
            f"<p><b>Extra cost (approximate):</b> {cost_blurb}<br>"
            f"<small>On top of the regular AutoPrompt text-token cost. "
            f"Real cost depends on image resolution and provider pricing.</small></p>"
            f"<p>The LLM will use the images to visually ground its terminology "
            f"decisions — labelled parts, captions, visible components, "
            f"figure cross-references.</p>"
            f"<p>Continue?</p>",
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Ok
        )
        if confirm != QMessageBox.StandardButton.Ok:
            return {"images": [], "refs": []}  # sentinel: user cancelled

        self.log_message(
            f"[AI Assistant] Vision-aware AutoPrompt: shipping {n} image(s) "
            f"to {provider}/{model} (est. extra ~{cost_blurb})"
        )
        return {"images": images_payload, "refs": refs}

    def _inject_image_context_directive(self, analysis_prompt: str, refs: list) -> str:
        """v1.10.178: prepend a section to the meta-prompt telling the
        LLM that figure images are attached and how to use them. Goes
        near the top so the model reads it before getting deep into the
        textual analysis instructions.
        """
        ref_list = ", ".join(f"Figure {r.upper()}" for r in refs[:10])
        if len(refs) > 10:
            ref_list += f", … ({len(refs)} total)"
        directive = (
            "\n\n## ATTACHED FIGURE IMAGES\n\n"
            f"The user has attached **{len(refs)} figure image(s)** from this "
            f"document to the request: {ref_list}.\n\n"
            "When generating the translation prompt, use the visual content of "
            "these figures to ground your terminology decisions:\n\n"
            "- **Identify labelled parts and reference numerals** visible in the "
            "drawings; weave any unambiguous component-name → reference-numeral "
            "anchors into the generated prompt's terminology table.\n"
            "- **Cross-check the textual figure references** in the source "
            "(Figure 1, Fig. 2A, …) against what each figure actually depicts. "
            "If the source describes 'the cylindrical sleeve (7)' and Figure 1 "
            "labels item 7 as a sleeve, lock that mapping in the generated prompt.\n"
            "- **Use figure captions and visible text** as a source of "
            "authoritative terminology for the generated prompt's domain "
            "vocabulary.\n"
            "- **Do NOT translate text inside the figures** — figures stay in "
            "the source language. The generated prompt should reflect this if "
            "the document type warrants it (patents, technical specifications).\n\n"
            "If a figure is ambiguous or unreadable, ignore it and rely on the "
            "textual analysis below.\n"
        )
        # Inject right after the first heading, or at the very top if
        # the prompt has no obvious anchor.
        return directive + analysis_prompt

    def _build_project_context(self) -> str:
        """Build context from current project"""
        context_parts = []

        # Current document info
        if hasattr(self.parent_app, 'current_document_path'):
            doc_path = self.parent_app.current_document_path
            if doc_path:
                context_parts.append(f"**Document:** {Path(doc_path).name}")

        # Language pair
        if hasattr(self.parent_app, 'current_project') and self.parent_app.current_project:
            project = self.parent_app.current_project
            if hasattr(project, 'source_language') and hasattr(project, 'target_language'):
                src = _resolve_lang_name(project.source_language) or project.source_language
                tgt = _resolve_lang_name(project.target_language) or project.target_language
                context_parts.append(f"**Language Pair:** {src} → {tgt}")

        # Full document content
        if hasattr(self.parent_app, 'current_project') and self.parent_app.current_project:
            project = self.parent_app.current_project
            if hasattr(project, 'segments') and project.segments:
                total = len(project.segments)
                context_parts.append(f"\n**Project Size:** {total} segments")

                # Multi-file: structure content by file with headers
                is_multifile = getattr(project, 'is_multifile', False)
                files = getattr(project, 'files', None)

                if is_multifile and files and len(files) > 1:
                    # Show segments grouped by file (sample from each)
                    max_segs_per_file = max(10, 100 // len(files))
                    context_parts.append(f"\n**Document Content (by file, ~{max_segs_per_file} segments each):**")
                    for file_info in files:
                        file_id = file_info['id']
                        file_name = file_info.get('name', f'File {file_id}')
                        file_segs = [s for s in project.segments if s.file_id == file_id]
                        context_parts.append(f"\n--- {file_name} ({len(file_segs)} segments) ---")
                        for i, seg in enumerate(file_segs[:max_segs_per_file], 1):
                            context_parts.append(f"\n{i}. {seg.source}")
                            if seg.target:
                                context_parts.append(f"   → {seg.target}")
                elif self._cached_document_markdown:
                    # Single file: use cached markdown
                    doc_content = self._cached_document_markdown[:50000]
                    context_parts.append(f"\n**Full Document Content:**\n{doc_content}")
                else:
                    # Fallback: Construct from segments (first 100 segments)
                    context_parts.append(f"\n**Document Content (first 100 segments):**")
                    for i, seg in enumerate(project.segments[:100], 1):
                        context_parts.append(f"\n{i}. {seg.source}")
                        if seg.target:
                            context_parts.append(f"   → {seg.target}")

        # Attached files
        if self.attached_files:
            context_parts.append(f"\n**Attached Files ({len(self.attached_files)}):**")
            for file in self.attached_files:
                context_parts.append(f"- {file['name']}: {len(file.get('content', ''))} chars")
                # Show preview of file content
                if file.get('content'):
                    preview = file['content'][:200].replace('\n', ' ')
                    context_parts.append(f"  Preview: {preview}...")

        # Translation Memory data (if enabled)
        if self.include_tm_data:
            tm_data = self._get_tm_context_data()
            if tm_data:
                context_parts.append(f"\n**Translation Memory Matches:**\n{tm_data}")

        # Termbase data (if enabled)
        if self.include_termbase_data:
            tb_data = self._get_termbase_context_data()
            if tb_data:
                context_parts.append(f"\n**Termbase Entries:**\n{tb_data}")

        return "\n".join(context_parts) if context_parts else "No context available"

    # --- Enhanced prompt generation helpers ---

    # Domains the generation templates understand. The AI classifier is
    # constrained to these so its answer maps straight onto a template;
    # anything else falls back to 'general'.
    _CLASSIFY_DOMAINS = ['patent', 'legal', 'medical', 'technical',
                         'financial', 'marketing', 'general']

    def _classify_document_context(self) -> tuple:
        """Ask the AI to read a sample of the source and classify the domain.

        Returns ``(domain, description)`` where ``domain`` is one of
        ``_CLASSIFY_DOMAINS`` and ``description`` is a short free-text
        characterisation of the text type (may be empty). Falls back to
        ``('general', '')`` on any error, so AutoPrompt still runs.

        This replaces the old keyword heuristic: a model reading the actual
        text classifies far more reliably and across languages, instead of
        counting domain keywords (which misread e.g. a creative text with a
        stray "Fig. 1" and "comprising" as a patent).
        """
        try:
            sample = (self._get_source_text_for_analysis() or "").strip()
        except Exception:
            sample = ""
        if not sample:
            return ('general', '')
        # A few thousand characters is plenty to establish text type without
        # bloating the classification call.
        sample = sample[:4000]

        domains_csv = ", ".join(self._CLASSIFY_DOMAINS)
        system_prompt = (
            "You are a classifier for a professional translation tool. "
            "You read a document sample and identify its domain and text "
            "type. Reply with ONLY a JSON object, no prose, no code fences."
        )
        prompt = (
            "Classify the following document that is about to be translated.\n\n"
            f"Choose the single best-fitting domain from this list: {domains_csv}.\n"
            "Use 'general' only if none of the specific domains clearly fit.\n"
            "Also give a short (max ~12 words) description of the text type "
            "and register (e.g. 'creative marketing copy, playful tone' or "
            "'mechanical patent, formal').\n\n"
            'Return exactly: {"domain": "<one of the list>", "description": "<short phrase>"}\n\n'
            "=== DOCUMENT SAMPLE ===\n"
            f"{sample}"
        )

        # Run the classification off the main thread so the UI doesn't freeze
        # (the call is quick, but a synchronous HTTP request still blocks the
        # event loop and makes the window flash "not responding"). A small
        # busy dialog covers the gap until the confirm-context dialog opens.
        from PyQt6.QtCore import QEventLoop

        progress = QProgressDialog(
            "Reading the document to detect its context…", None, 0, 0,
            self.main_widget,
        )
        progress.setWindowTitle("AutoPrompt")
        progress.setWindowModality(Qt.WindowModality.ApplicationModal)
        progress.setMinimumDuration(0)
        progress.setAutoClose(False)
        progress.setAutoReset(False)

        result = {'text': None, 'error': None}
        worker = _AutoPromptWorker(self.chat_backend, prompt, system_prompt)
        loop = QEventLoop()
        worker.finished_ok.connect(
            lambda text, _meta: (result.__setitem__('text', text), loop.quit())
        )
        worker.failed.connect(
            lambda msg: (result.__setitem__('error', msg), loop.quit())
        )
        worker.start()
        progress.show()
        loop.exec()
        worker.wait(2000)
        progress.close()

        if result['error'] is not None:
            self.log_message(
                f"[AI Assistant] Context classification failed: "
                f"{result['error']} — defaulting to 'general'"
            )
            return ('general', '')
        return self._parse_classification(result['text'])

    def _parse_classification(self, response_text: str) -> tuple:
        """Parse the classifier's JSON reply into ``(domain, description)``.

        Tolerant of surrounding prose / code fences: extracts the first
        JSON object. Invalid or unknown domains fall back to 'general'.
        """
        if not response_text:
            return ('general', '')
        domain = 'general'
        description = ''
        parsed_ok = False
        try:
            match = re.search(r'\{.*\}', response_text, re.DOTALL)
            if match:
                data = json.loads(match.group(0))
                raw_domain = str(data.get('domain', '')).strip().lower()
                if raw_domain in self._CLASSIFY_DOMAINS:
                    domain = raw_domain
                    parsed_ok = True
                description = str(data.get('description', '')).strip()[:120]
        except Exception:
            pass
        if not parsed_ok:
            # No clean JSON domain: look for a bare domain word in the reply.
            low = response_text.lower()
            for d in self._CLASSIFY_DOMAINS:
                if d != 'general' and d in low:
                    domain = d
                    break
        return (domain, description)

    def _run_document_analysis(self) -> dict:
        """Run DocumentAnalyzer on current project segments (tone, structure, terminology)."""
        try:
            if not hasattr(self.parent_app, 'current_project') or not self.parent_app.current_project:
                return {'success': False, 'error': 'No project loaded'}
            project = self.parent_app.current_project
            if not hasattr(project, 'segments') or not project.segments:
                return {'success': False, 'error': 'No segments'}
            analyzer = DocumentAnalyzer()
            return analyzer.analyze_segments(project.segments)
        except Exception as e:
            self.log_message(f"[AI Assistant] Document analysis failed: {e}")
            return {'success': False, 'error': str(e)}

    def _run_document_analysis_for_segments(self, segments: list) -> dict:
        """Run DocumentAnalyzer on a specific segment list (e.g. one file's segments)."""
        try:
            if not segments:
                return {'success': False, 'error': 'No segments'}
            analyzer = DocumentAnalyzer()
            return analyzer.analyze_segments(segments)
        except Exception as e:
            self.log_message(f"[AI Assistant] Per-file document analysis failed: {e}")
            return {'success': False, 'error': str(e)}

    def _build_file_manifest(self, project) -> str:
        """Build a file manifest with per-file domain/tone analysis for multi-file projects.

        Returns a manifest string describing each file's characteristics, or empty string
        if the project is not multi-file.
        """
        if not getattr(project, 'is_multifile', False) or not getattr(project, 'files', None):
            return ""
        if len(project.files) <= 1:
            return ""

        lines = [
            "=== MULTI-FILE PROJECT ===",
            f"This project contains {len(project.files)} files. Each file may have a different domain and register.",
            "Adapt your translation style to match each file's content type.",
            ""
        ]

        for file_info in project.files:
            file_id = file_info['id']
            file_name = file_info.get('name', f'File {file_id}')
            seg_count = file_info.get('segment_count', 0)

            # Run per-file analysis
            file_segments = [s for s in project.segments if s.file_id == file_id]
            analysis = self._run_document_analysis_for_segments(file_segments)

            tone = "neutral"
            formality = "neutral"
            word_count = 0

            if analysis.get('success'):
                tone = analysis.get('tone', {}).get('tone', 'neutral')
                formality = analysis.get('tone', {}).get('formality', 'neutral')
                word_count = analysis.get('statistics', {}).get('total_words', 0)

            lines.append(
                f"File {file_id}: {file_name} ({seg_count} segments)\n"
                f"  Tone: {tone.title()} | "
                f"Formality: {formality.title()} | {word_count:,} words"
            )

        return "\n".join(lines)

    def _gather_full_terminology(self, max_terms: int = 500) -> tuple:
        """Gather all termbase terms for the current project.

        Returns:
            (terms_table_str, term_count, has_forbidden_terms)
        """
        all_terms = []

        try:
            # Method 1: AI-inject terms (preferred – respects activation + ai_inject flag)
            if hasattr(self.parent_app, 'termbase_manager') and self.parent_app.termbase_manager:
                project_id = None
                if hasattr(self.parent_app, 'current_project') and self.parent_app.current_project:
                    project_id = getattr(self.parent_app.current_project, 'id', None)
                ai_terms = self.parent_app.termbase_manager.get_ai_inject_terms(project_id or 0)
                if ai_terms:
                    all_terms.extend(ai_terms)

            # Method 2: Fallback – iterate active termbases directly
            if not all_terms and hasattr(self.parent_app, 'termbase_manager') and self.parent_app.termbase_manager:
                project_id = None
                if hasattr(self.parent_app, 'current_project') and self.parent_app.current_project:
                    project_id = getattr(self.parent_app.current_project, 'id', None)
                if project_id:
                    try:
                        active_tbs = self.parent_app.termbase_manager.get_active_termbases_for_project(project_id)
                        for tb in active_tbs:
                            terms = self.parent_app.termbase_manager.get_terms(tb['id'])
                            for t in terms:
                                t['termbase_name'] = tb['name']
                            all_terms.extend(terms)
                    except Exception:
                        pass

            # Method 3: Legacy in-memory termbases
            if not all_terms and hasattr(self.parent_app, 'termbases') and self.parent_app.termbases:
                for tb_name, tb in self.parent_app.termbases.items():
                    if hasattr(tb, 'terms'):
                        for source, target in tb.terms.items():
                            all_terms.append({
                                'source_term': source,
                                'target_term': target,
                                'forbidden': False,
                                'termbase_name': tb_name,
                            })

            if not all_terms:
                return ("No termbase terms available.", 0, False)

            # Cap to max_terms
            if len(all_terms) > max_terms:
                self.log_message(f"[AI Assistant] Termbase has {len(all_terms)} terms, capping to {max_terms}")
            all_terms = all_terms[:max_terms]

            # Separate regular and forbidden terms
            regular = [t for t in all_terms if not t.get('forbidden')]
            forbidden = [t for t in all_terms if t.get('forbidden')]

            # Build markdown table
            lines = ["| Source Term | Target Term | Notes |",
                     "|------------|-------------|-------|"]
            for t in regular:
                source = t.get('source_term', '')
                target = t.get('target_term', '')
                notes = t.get('notes', '') or t.get('termbase_name', '')
                if source and target:
                    lines.append(f"| {source.replace('|', '/')} | {target.replace('|', '/')} | {notes.replace('|', '/')} |")

            result = "\n".join(lines)

            if forbidden:
                result += "\n\n**FORBIDDEN TERMS (DO NOT USE – these translations are explicitly rejected):**\n"
                for t in forbidden:
                    result += f"- {t.get('source_term', '')} -> {t.get('target_term', '')} [FORBIDDEN]\n"

            return (result, len(all_terms), len(forbidden) > 0)

        except Exception as e:
            self.log_message(f"[AI Assistant] Terminology gathering failed: {e}")
            return ("Error gathering terminology.", 0, False)

    def _gather_confirmed_segment_pairs(self, max_pairs: int = 15) -> list:
        """Pull confirmed source→target translations from the current project's segments.

        Treats anything the user has already translated in this project as
        TM anchors of the highest authority — the project-confirmed title
        and any committed segments establish terminology and register that
        the LLM-generated prompt should respect.  Earlier code only looked
        at separately-loaded TM databases, so projects without an attached
        .tm file produced "No TM reference pairs available" even when the
        user had translated the title.
        """
        try:
            project = getattr(self.parent_app, 'current_project', None)
            if not project or not hasattr(project, 'segments') or not project.segments:
                return []

            pairs = []
            for seg in project.segments:
                if not hasattr(seg, 'source') or not hasattr(seg, 'target'):
                    continue
                src = (seg.source or '').strip()
                tgt = (seg.target or '').strip()
                # Skip empties and untranslated (target identical to source
                # usually means placeholder, not a real translation choice)
                if src and tgt and src != tgt:
                    pairs.append((src, tgt, 'Project (confirmed segment)'))
                    if len(pairs) >= max_pairs:
                        break

            return pairs
        except Exception as e:
            self.log_message(f"[AI Assistant] Confirmed segment gathering failed: {e}")
            return []

    def _gather_tm_reference_pairs(self, max_pairs: int = 30) -> str:
        """Gather reference translation pairs as TM anchors.

        Sources, in priority order:
        1. Confirmed translations already committed in project.segments
           (e.g. the project-confirmed title) — these are the highest-
           authority anchors because they're locked decisions for THIS
           document.
        2. Separately-loaded TM database entries — broader corpus.

        Returns markdown-formatted reference pairs.  Returns a string
        containing "no translation" if both sources are empty so the
        caller's existing `tm_has_data` check still works.
        """
        all_pairs = []

        # 1. Confirmed translations from current project (highest authority)
        confirmed = self._gather_confirmed_segment_pairs(max_pairs=15)
        all_pairs.extend(confirmed)

        # 2. TM database entries
        try:
            if hasattr(self.parent_app, 'tm_databases') and self.parent_app.tm_databases:
                for tm_name, tm_db in self.parent_app.tm_databases.items():
                    if not hasattr(tm_db, 'entries') or not tm_db.entries:
                        continue
                    entries = list(tm_db.entries.items())
                    if not entries:
                        continue
                    budget = max(0, max_pairs - len(all_pairs))
                    if budget == 0:
                        break
                    step = max(1, len(entries) // budget)
                    sampled = entries[::step][:budget]
                    for source, target in sampled:
                        if source.strip() and target.strip():
                            all_pairs.append((source.strip(), target.strip(), tm_name))
        except Exception as e:
            self.log_message(f"[AI Assistant] TM database read failed: {e}")

        if not all_pairs:
            # Keep "no translation" substring so the existing caller-side
            # `tm_has_data` check (string-based) still resolves to False.
            return "No translation memory data and no confirmed segment translations available."

        all_pairs = all_pairs[:max_pairs]
        lines = []
        for source, target, source_label in all_pairs:
            source_esc = source.replace('|', '/')
            target_esc = target.replace('|', '/')
            lines.append(f"{source_esc}\n-> {target_esc}\n   [source: {source_label}]\n")

        return "\n".join(lines)

    # ───────────────────────────────────────────────────────────────────
    #  Source-aware pre-generation passes
    #
    #  Each method below runs against the full source text BEFORE the
    #  meta-prompt is built, and produces a short Markdown block that
    #  gets injected into the meta-prompt so the LLM has concrete,
    #  document-specific examples to work from instead of generic
    #  scaffolding.  Brief input to add these (web-Claude analysis of
    #  the BRANTS test case) noted that the prior Workbench output had
    #  invented hypothetical defects and missed real collisions in the
    #  source; these passes fix that by extracting the real examples.
    # ───────────────────────────────────────────────────────────────────

    def _get_source_text_for_analysis(self) -> str:
        """Return the full source text to feed into the pre-generation passes.

        Prefers the cached document markdown (full prose, as ingested);
        falls back to concatenating segment sources.
        """
        if hasattr(self, '_cached_document_markdown') and self._cached_document_markdown:
            return self._cached_document_markdown
        project = getattr(self.parent_app, 'current_project', None)
        if project and hasattr(project, 'segments') and project.segments:
            return '\n'.join(
                (s.source or '') for s in project.segments if hasattr(s, 'source')
            )
        return ""

    def _detect_terminology_collisions(self, source_text: str) -> str:
        """Flag Dutch source terms whose natural English targets collide.

        Returns a Markdown-formatted warnings section, or empty string if
        no collisions are detected.  Each collision is presented with the
        EPO-conventional resolution so the LLM-generated prompt can lock
        the correct mapping instead of picking arbitrarily.

        The known-collisions list is small and curated for mechanical /
        patent NL→EN translation (the highest-traffic domain in the
        Workbench user base).  Adding more collision sets for other
        domains is cheap — just append entries.
        """
        if not source_text:
            return ""

        text_lower = source_text.lower()

        # Each entry: {source_terms: [...], resolution: 'EPO guidance text'}.
        # Triggers when at least 2 of the listed source terms appear in
        # the document body — one term alone isn't a collision, it's just
        # a translation choice.
        known_collisions = [
            {
                'source_terms': [
                    'mantel', 'huls', 'beschermhuls', 'hulzelement',
                    'mantelbuis', 'beschermbuis',
                ],
                'resolution': (
                    "EPO mechanical convention: `mantel` → **casing** (the device's own body); "
                    "`huls` / `hulzelement` → **sleeve** / **sleeve element**; "
                    "`beschermhuls` → **protective sheath**; "
                    "`mantelbuis` → **sleeve pipe** (the surrounding installed pipe); "
                    "`beschermbuis` → **protective tube**. "
                    "Never map `mantel` to \"sleeve\" — it conflates the device body with the surrounding pipe "
                    "and makes any inventive-step argument that depends on distinguishing them incoherent."
                ),
            },
            {
                'source_terms': ['pijp', 'buis', 'flexibele buis'],
                'resolution': (
                    "Patent convention: `pijp` → **pipe** (rigid); "
                    "`buis` → **pipe** by default, **tube** only where the source explicitly contrasts "
                    "`buis` with `pijp`; "
                    "`flexibele buis` → **flexible hose** (NOT \"flexible tube\")."
                ),
            },
            {
                'source_terms': ['voorzijde', 'voorvlak', 'achterzijde'],
                'resolution': (
                    "Distinguish carefully: `voorzijde` → **front side**; "
                    "`voorvlak` → **front face** (a specific surface, not the side); "
                    "`achterzijde` → **rear side** (NOT \"back side\" or \"rear face\")."
                ),
            },
        ]

        # Special-case: the `as` homograph (wheel axle vs geometrical axis).
        # Detection: `as` appears at least twice in the source AND at least
        # one geometrical-axis context word also appears.
        as_present = bool(re.search(r'\bas\b|\bassen\b', text_lower))
        axle_context = any(
            w in text_lower for w in ('wiel', 'wielen', 'rolt', 'draait', 'rotatie')
        )
        axis_context = any(
            w in text_lower for w in ('longitudinale', 'haaks', 'evenwijdig', 'loodrecht', 'coaxiaal')
        )

        findings = []
        for entry in known_collisions:
            present = [t for t in entry['source_terms'] if t in text_lower]
            if len(present) >= 2:
                findings.append({
                    'present_terms': present,
                    'resolution': entry['resolution'],
                })

        if as_present and axle_context and axis_context:
            findings.append({
                'present_terms': ['as (homograph)'],
                'resolution': (
                    "Dutch `as` is a homograph in this document. Render as **shaft** when it refers "
                    "to the rotational axle of a wheel; render as **axis** when it refers to a geometrical "
                    "axis (e.g. `longitudinale as`, `loodrechte assen`). Two distinct glossary entries are "
                    "required, disambiguated by the surrounding context or by parenthetical reference numeral."
                ),
            })

        if not findings:
            return ""

        lines = []
        for f in findings:
            terms_list = ', '.join(f"`{t.strip()}`" for t in f['present_terms'])
            lines.append(f"- The source contains {terms_list}. {f['resolution']}")

        return '\n'.join(lines)

    def _detect_source_defects(self, source_text: str, max_examples: int = 5) -> str:
        """Scan the source for common defect patterns and quote verbatim examples.

        Concrete examples from the actual source beat generic instructions
        — the LLM-generated prompt's "preserve defects faithfully" rule is
        much more effective when it cites the real surface forms the
        translator AI will encounter.

        Returns a Markdown bullet list, or empty string if no defects found.
        """
        if not source_text:
            return ""

        findings = []

        # 1. Hanging mid-sentence breaks — sentences ending in a Dutch
        #    subordinating conjunction or preposition without a complement.
        hanging_conjunctions = (
            r'doordat|waarbij|dewelke|omdat|terwijl|hoewel|'
            r'zodat|nadat|opdat|voordat|alvorens|tenzij'
        )
        hanging_pattern = re.compile(
            r'([^\n]{30,200}?\b(?:' + hanging_conjunctions + r')\b\s*\.{0,3}\s*?)(?=\n\n|\n[A-Z]|$)',
            re.IGNORECASE | re.MULTILINE,
        )
        for m in list(hanging_pattern.finditer(source_text))[:2]:
            snippet = m.group(1).strip()
            if len(snippet) < 220 and len(snippet) >= 30:
                findings.append(('Hanging mid-sentence break', snippet))

        # 2. Doubled spaces inside running text
        doubled_space_pattern = re.compile(r'(\b\w[^\n]{0,60}?  \w[^\n]{0,40}\b)')
        for m in list(doubled_space_pattern.finditer(source_text))[:1]:
            findings.append(('Doubled space inside text', m.group(1).strip()))

        # 3. Verb-ending typos (Dutch -d / -t mismatch).  Heuristic only:
        #    flag obvious cases like "het verkleind dat" or "dit bied" where
        #    the verb stem suggests indicative -t was intended.  Filtered
        #    against a small whitelist of legit -d forms (was, werd, etc.).
        verb_pattern = re.compile(
            r'\b(?:hij|zij|het|de|deze|dat|dit|men|wat)\s+(\w{4,})\b',
            re.IGNORECASE,
        )
        verb_whitelist = {
            'werd', 'wordt', 'word', 'kwam', 'doet', 'gaat', 'staat', 'kreeg',
            'maakte', 'maakt', 'liet', 'laat', 'heeft', 'hebben', 'zijn', 'was',
            'waren', 'biedt', 'verkleint', 'verkleind', 'verzekerd', 'verzekert',
        }
        verb_typo_count = 0
        for m in verb_pattern.finditer(source_text):
            verb = m.group(1).lower()
            if not verb.endswith('d'):
                continue
            if verb in verb_whitelist:
                continue
            # Only flag if the verb is a plausible candidate for -t/-d confusion
            if len(verb) >= 5 and verb[-2] in 'aeiou':
                # Show with a tiny window of surrounding context
                start = max(0, m.start() - 20)
                end = min(len(source_text), m.end() + 30)
                snippet = source_text[start:end].strip()
                findings.append((f'Possible -d/-t verb-ending typo ("{verb}")', snippet))
                verb_typo_count += 1
                if verb_typo_count >= 2:
                    break

        # 4. Broken compound words — a Dutch word followed by an unexpected
        #    space and a short fragment that looks like the second half.
        #    Conservative: only flag short fragments to avoid false positives.
        broken_compound_pattern = re.compile(r'(\b[a-z]{4,12})  ([a-z]{3,8}\b)')
        for m in list(broken_compound_pattern.finditer(source_text))[:1]:
            findings.append(('Possible broken compound (double space mid-word)', m.group(0)))

        if not findings:
            return ""

        # Cap at max_examples, prioritising hanging breaks (highest signal)
        findings = findings[:max_examples]

        lines = []
        for defect_type, snippet in findings:
            # Trim and clean
            snippet_clean = snippet.replace('`', "'").replace('\n', ' / ')
            if len(snippet_clean) > 200:
                snippet_clean = snippet_clean[:200] + "…"
            lines.append(f"- **{defect_type}**: `{snippet_clean}`")

        return '\n'.join(lines)

    def _extract_source_cascades(self, source_text: str, max_examples: int = 3) -> str:
        """Extract preference cascades from the source for anti-truncation guidance.

        Patent prose typically contains `bij voorkeur ... bij nog meer
        voorkeur ...` cascades that the LLM is tempted to collapse into a
        single value.  Quoting the real ones in the meta-prompt makes the
        anti-truncation rule concrete: "preserve THIS pattern, here is an
        example from your own document".

        Returns a Markdown bullet list, or empty string if no cascades.
        """
        if not source_text:
            return ""

        cascade_keywords = (
            r'bij\s+voorkeur|bij\s+nog\s+meer\s+voorkeur|'
            r'preferably|more\s+preferably|even\s+more\s+preferably'
        )
        cascade_pattern = re.compile(
            r'([^\n.]{10,80}?\b(?:' + cascade_keywords + r')\b[^\n.]{5,120}?)(?=[.\n])',
            re.IGNORECASE,
        )

        findings = []
        seen = set()
        for m in cascade_pattern.finditer(source_text):
            snippet = m.group(1).strip()
            key = snippet[:60].lower()
            if key in seen:
                continue
            seen.add(key)
            findings.append(snippet)
            if len(findings) >= max_examples:
                break

        if not findings:
            return ""

        lines = []
        for snippet in findings:
            snippet_clean = snippet.replace('`', "'").replace('\n', ' ')
            if len(snippet_clean) > 180:
                snippet_clean = snippet_clean[:180] + "…"
            lines.append(f"- `{snippet_clean}`")

        return '\n'.join(lines)

    def _detect_legal_entity_markers(self, source_text: str) -> bool:
        """Return True iff the source mentions any legal-entity markers.

        Used to decide whether the LLM-generated prompt should include the
        legal-entity / notarial-title scaffolding.  For a mechanical patent
        body with no entity names in running text, that scaffolding is
        noise that wastes prompt tokens.
        """
        if not source_text:
            return False
        # Conservative whole-word check for common Belgian/Dutch/EU entity
        # suffixes and notarial titles.  Word boundaries prevent matching
        # inside other words.
        entity_markers = (
            r'B\.?V\.?|N\.?V\.?|GmbH|Ltd\.?|Inc\.?|S\.?A\.?|S\.?A\.?R\.?L\.?|'
            r'SE|SPRL|BVBA|Meester|Mr\.?|Mevr\.?|Mw\.?|notaris'
        )
        pattern = re.compile(r'\b(?:' + entity_markers + r')\b')
        return bool(pattern.search(source_text))

    def _get_domain_template(self, domain: str) -> dict:
        """Get domain-specific template for prompt generation."""
        return DOMAIN_TEMPLATES.get(domain, DOMAIN_TEMPLATES['general'])

    def _build_enhanced_analysis_prompt(self, *, context, analysis_summary, detected_domain,
                                        template, terminology_table, term_count,
                                        has_forbidden, tm_pairs, source_lang, target_lang,
                                        segment_count, file_manifest="",
                                        terminology_collisions="", source_defects="",
                                        source_cascades="", include_legal_entity_scaffolding=True,
                                        user_context_hint="") -> str:
        """Build the enhanced meta-prompt that instructs the LLM to generate a rich translation prompt.

        Newer source-aware kwargs (all optional, all default to empty/safe):
            terminology_collisions: Markdown bullet list of detected
                cross-term collisions (e.g. mantel/huls/beschermhuls
                conflicts), with EPO-conventional resolutions.
            source_defects: Markdown bullet list of verbatim defect
                examples extracted from the actual source.
            source_cascades: Markdown bullet list of preference cascades
                (`bij voorkeur ... bij nog meer voorkeur`) extracted from
                the actual source — anchors the anti-truncation rule in
                concrete document-specific examples.
            include_legal_entity_scaffolding: when False, the meta-prompt
                instructs the LLM to OMIT the BV/NV/Meester/notarial-title
                section that's noise for documents (e.g. pure mechanical
                patent bodies) where no entity names appear in running text.
            user_context_hint: optional free-text briefing the translator
                supplied in the AutoPrompt context dialog; injected as an
                authoritative context block so it overrides inferred domain.
        """

        # Filter the section list: drop the legal-entity / statutory-
        # reference sections from the LEGAL template when no entity
        # markers are present in the source (mechanical patent bodies
        # don't need BV/NV/Meester guidance).
        sections = list(template['sections'])
        if not include_legal_entity_scaffolding:
            sections = [
                s for s in sections
                if 'LEGAL ENTITY' not in s.upper() and 'STATUTORY REFERENCE' not in s.upper()
            ]
        # Build the mandatory sections list
        sections_instruction = "\n".join(f"  {i+1}. {s}" for i, s in enumerate(sections))

        # Build domain rules — same legal-entity filter applied
        rules = template['rules']
        if not include_legal_entity_scaffolding:
            rules = [
                r for r in rules
                if not ('legal entity' in r.lower() or 'meester' in r.lower() or
                        'statutory' in r.lower() or 'notari' in r.lower())
            ]
        domain_rules = "\n".join(f"  - {r}" for r in rules)

        # Terminology section
        if term_count > 0:
            terminology_instruction = (
                f"The project has {term_count} termbase entries. Include ALL of them in a "
                f"PROJECT-SPECIFIC TERMBASE (MANDATORY, LOCKED) section, organized by semantic category "
                f"(e.g., structural terms, electronic terms, process terms, boilerplate terms).\n"
                f"Mark the termbase as LOCKED and state: 'No substitutions or variants are permitted.'\n"
            )
            if has_forbidden:
                terminology_instruction += (
                    "Include a FORBIDDEN TERMS section – the model must NEVER use these translations.\n"
                )
            terminology_instruction += f"\nTERMBASE DATA:\n{terminology_table}"
        else:
            terminology_instruction = (
                "No termbase entries available. Instruct the model to extract key terms from the "
                "document content and maintain strict internal consistency. Include a section asking "
                "the model to build its own termbase from context and apply it uniformly."
            )

        # TM reference section
        tm_has_data = ("empty" not in tm_pairs.lower() and "no translation" not in tm_pairs.lower()
                       and "error" not in tm_pairs.lower())
        if tm_has_data:
            tm_instruction = (
                "Include a PREVIOUS CORRECT TRANSLATIONS section with these validated translation pairs from TM.\n"
                "These serve as style anchors – the model must match the style, register, and terminology "
                "patterns visible in these validated translations. State that previous correct translations "
                "have HIGHEST priority in the terminology consistency hierarchy.\n\n"
                f"TM REFERENCE PAIRS:\n{tm_pairs}"
            )
        else:
            tm_instruction = (
                "No TM reference pairs available. Omit the PREVIOUS CORRECT TRANSLATIONS section, but still "
                "include the TERMINOLOGY CONSISTENCY HIERARCHY with the remaining priority levels."
            )

        # Translator-supplied briefing (from the AutoPrompt context dialog).
        # Surfaced as authoritative because the human knows the document's
        # real purpose better than the keyword heuristic — this is what lets
        # a user rescue a misclassified text (e.g. "this is creative copy,
        # not a patent") instead of getting a wrongly-scoped prompt.
        if user_context_hint:
            context_hint_block = (
                "=== TRANSLATOR-PROVIDED CONTEXT (AUTHORITATIVE) ===\n"
                "The translator supplied the following briefing about this "
                "document. Treat it as authoritative: where it conflicts with "
                "the auto-detected domain or any inference below, follow the "
                "briefing.\n\n"
                f"{user_context_hint}\n"
            )
        else:
            context_hint_block = ""

        prompt = f"""You are a prompt engineering specialist for professional translation. Your task is to generate
a comprehensive, expert-level translation prompt and save it using the ACTION system.

This prompt will be used in Supervertaler, a CAT (Computer-Assisted Translation) tool. Supervertaler
delivers the source text as NUMBERED BATCHES of segments (typically dozens of segments per request;
in some contexts a single segment). The prompt must account for this batched, segment-numbered
delivery - do NOT describe delivery as "one segment at a time" or "in isolation".

=== ANALYSIS RESULTS ===
DETECTED DOMAIN: {detected_domain.upper()}
LANGUAGE PAIR: {source_lang} -> {target_lang}
SEGMENT COUNT: {segment_count}

{analysis_summary}

{context_hint_block}
{f'''=== MULTI-FILE PROJECT INFORMATION ===
{file_manifest}

IMPORTANT: This is a multi-file project. Your generated prompt MUST include a MULTI-FILE GUIDANCE
section that lists each file with its detected domain and register, and instructs the translator AI
to adapt its translation style, terminology choices, and register when moving between files.
Each file may have a different domain (legal, technical, marketing, etc.) – the prompt must
acknowledge this and provide file-specific guidance.
''' if file_manifest else ""}=== DOMAIN-SPECIFIC ROLE ===
{template['role']}

=== PROJECT CONTEXT (document content) ===
{context}

=== SOURCE-AWARE ANALYSIS REQUIRED (PERFORM BEFORE GENERATING) ===

Before writing the generated prompt, scan the source content above and perform the
following three analyses YOURSELF. Use the source language ({source_lang}) and the
detected domain ({detected_domain}) to choose the right patterns to look for —
these analyses must work for every domain (medical, legal, marketing, financial,
technical, patent, etc.) and every source language, not just the one example
below.

1. **Terminology-collision detection.**
   Identify any groups of source-language terms whose natural target-language
   candidates would collide (i.e. multiple distinct source concepts mapping to
   the same target word, or one source word with two distinct technical
   meanings). Common patterns across domains and languages:
   - **Container/enclosure cluster** (mechanical/patent): source language often
     has 3–5 distinct words for casings, sleeves, sheaths, tubes, pipes that
     all map to "sleeve" or "tube" in English by default. Resolve using the
     domain convention so each source word gets a distinct, locked target.
   - **Homographs**: a source word with two distinct technical meanings
     disambiguated only by context (e.g. Dutch `as` = axle OR axis; German
     `Strom` = electrical current OR river; French `temps` = time OR weather).
   - **Anatomical/medical near-synonyms** that source distinguishes but English
     conflates (e.g. arteria vs vena, ligament vs tendon variants).
   - **Legal near-synonyms** carrying different scope (e.g. agreement vs
     contract vs covenant; liability vs responsibility vs obligation).
   - **Financial near-synonyms** (revenue vs turnover vs sales).
   For every collision you detect IN THIS source, embed an explicit resolution in
   the generated prompt's PROJECT-SPECIFIC TERMBASE section: list each member,
   its locked target, and a one-line note explaining why the choice was made
   (e.g. "domain convention", "preserves inventive-step distinction",
   "regulatory mandate").

2. **Defect-detection pass.**
   Scan the source for surface defects the translator AI will encounter and need
   to preserve faithfully (not silently correct). Patterns to look for vary by
   source language:
   - **Hanging mid-sentence breaks**: sentences ending in subordinating
     conjunctions / prepositions without a complement (e.g. Dutch `doordat`,
     `waarbij`; German `dass`, `weil`; French `parce que`, `lorsque`;
     Spanish `porque`, `cuando`; Italian `perché`, `quando`).
   - **Verb-ending typos** appropriate to the source language morphology
     (e.g. Dutch -d/-t confusion; German missing umlauts; French accent slips;
     Spanish/Italian conjugation typos).
   - **Doubled spaces and broken compound words** (language-agnostic).
   - **Reference-numeral mismatches** where a numeral introduced earlier refers
     to a different antecedent than the surrounding paragraph.
   - **Inconsistent capitalisation** of proper terms.
   Quote AT LEAST TWO verbatim defect examples from THIS source in the generated
   prompt's TRANSLATION MANDATE / "preserve defects faithfully" section so the
   translator AI sees the actual surface forms it will encounter, not abstract
   rules.

3. **Preference-cascade extraction.**
   Many domains (especially patent, technical specifications, regulatory text)
   use cascades like "preferably X, more preferably Y, even more preferably Z"
   that the translator AI is tempted to collapse into a single value.
   Source-language equivalents:
   - Dutch: `bij voorkeur`, `bij nog meer voorkeur`
   - German: `vorzugsweise`, `besonders bevorzugt`, `ganz besonders bevorzugt`
   - French: `de préférence`, `plus préférablement`, `encore plus préférablement`
   - Spanish: `preferiblemente`, `más preferiblemente`, `aún más preferiblemente`
   - Italian: `preferibilmente`, `più preferibilmente`, `ancora più preferibilmente`
   - Portuguese: `preferencialmente`, `mais preferencialmente`
   Quote AT LEAST ONE real cascade from THIS source in the generated prompt's
   anti-truncation section so the rule is anchored in a concrete example
   ("preserve THIS cascade, here is one from your own document").

If a particular scan finds nothing in this source (e.g. no defects, no cascades,
no collisions for this domain/language combination), omit the corresponding
subsection from the generated prompt rather than padding it with hypothetical
examples or generic prose. Hypothetical examples are worse than nothing.

=== PROMPT GENERATION INSTRUCTIONS ===

Generate a COMPREHENSIVE translation prompt (2000-5000 words) that a senior {detected_domain} translator
would consider authoritative and complete. The prompt must be SPECIFIC to THIS document and domain,
not generic. Use NON-NEGOTIABLE, LOCKED, and ABSOLUTE language for critical rules.

THE PROMPT MUST CONTAIN THESE SECTIONS (in this order):
{sections_instruction}

DOMAIN-SPECIFIC RULES TO EMBED IN THE PROMPT:
{domain_rules}

SPECIAL DOMAIN INSTRUCTIONS:
{template['special']}

=== UNIVERSAL RULES (embed in EVERY prompt) ===

1. TRANSLATION MANDATE (NON-NEGOTIABLE):
   "This is a professional translation task. Every word, repetition, structure, and cross-reference
   in the source is intentional. You must perform PURE TRANSLATION ONLY. You are explicitly forbidden
   from: improving clarity, simplifying descriptions, harmonizing terminology, correcting perceived
   drafting issues, streamlining enumerations, removing redundancies. If the source is long, repetitive,
   or awkward, reproduce it faithfully."

2. HARD CONSTRAINT - NO HALLUCINATED TRUNCATION:
   "You must assume that every element of the source text is deliberate. You are strictly forbidden from:
   omitting repetitive phrases, collapsing coordinated or parallel clauses, shortening component lists,
   simplifying enumerations or method steps, 'fixing' grammar or perceived defects. If uncertain,
   default to literal surface structure – never interpretation."

3. SUPERVERTALER INPUT HANDLING:
   "Supervertaler delivers the source text as a numbered batch of segments (in some contexts a
   single segment). You must: translate EVERY delivered segment, keep segment count and order
   exactly aligned with the input, and preserve segment boundaries. You MAY use context visible
   within the delivered batch (e.g. resolving an antecedent that appears a few segments earlier),
   but batch boundaries are arbitrary: never assume the batch is the whole document, and leave
   document-wide checks (e.g. reference-numeral consistency across the full text) to a separate
   QA pass. There is no memory between requests, so terminology must come from the termbase and
   reference translations below - never from choices made in an earlier batch. If a segment
   appears incomplete, translate exactly what is provided without comment."

4. TERMINOLOGY CONSISTENCY HIERARCHY:
   "(1) Previous correct translations from TM (highest priority), (2) Project-specific termbase terms
   (LOCKED), (3) Domain-specific conventions, (4) General language knowledge. Never mix competing
   variants once established. Because there is no memory between batches, the prompt itself must
   LOCK every recurring term to a single translation - never leave an open choice ('X or Y') for
   the translator AI to resolve, as consistency cannot carry across batches."

5. PREFLIGHT SELF-CHECK (MANDATORY INTERNAL STEP):
   "Before producing output, internally verify: every word and clause translated, no compression or
   optimization occurred, all values/references intact, no restructuring occurred, segment boundaries
   preserved. If any check fails, revise internally before output."

6. POST-TRANSLATION INTEGRITY ASSERTION (MANDATORY INTERNAL STEP):
   "Before finalizing output, internally assert: 'This translation is complete, literal, and
   structurally faithful. No content has been omitted, merged, compressed, inferred, harmonized,
   corrected, or stylistically optimized.' If this cannot be truthfully asserted, revise internally."

7. Number/date/currency localization rules appropriate for {source_lang} -> {target_lang}:
   - If translating FROM a European language (Dutch/French/German/etc.) TO English: convert decimal
     comma to decimal point, convert period thousands separator to comma
   - If translating FROM English TO a European language: reverse the above
   - Currency symbols directly against the number with no space
   - Date format adaptation as appropriate

8. OUTPUT FORMAT:
   - Translation only, no commentary, no explanations, no markdown formatting
   - Preserve original line breaks and paragraph structure
   - UTF-8 text, straight quotation marks only

=== TERMINOLOGY DATA ===
{terminology_instruction}

=== REFERENCE TRANSLATIONS FROM TM ===
{tm_instruction}

{f'''=== PRE-FLAGGED COLLISION HINTS (Dutch mechanical / patent helper) ===
A small built-in helper scanned the source for a specific known-collision pattern
(Dutch mechanical / patent vocabulary, the highest-traffic source-language case in
the Workbench user base). The hints below are CONFIRMED — they are in this source,
not hypothetical. INCORPORATE these resolutions into the generated prompt's
PROJECT-SPECIFIC TERMBASE section AND ALSO perform your own collision scan (per the
SOURCE-AWARE ANALYSIS section above) — collisions can occur in any domain / source
language, and this helper only covers one specific case.

{terminology_collisions}
''' if terminology_collisions else ""}{f'''=== PRE-FLAGGED DEFECT HINTS (Dutch source helper) ===
A small built-in helper scanned the source for a few Dutch-specific defect patterns
(hanging conjunctions, -d/-t verb typos) plus some language-agnostic ones (doubled
spaces, broken compounds). The hints below are CONFIRMED — they appear verbatim in
this source. Quote them in the generated prompt's "preserve defects faithfully"
section AND ALSO perform your own defect scan (per the SOURCE-AWARE ANALYSIS
section above) using patterns appropriate for the source language — this helper
only covers one specific case.

{source_defects}
''' if source_defects else ""}{f'''=== PRE-FLAGGED CASCADE HINTS (Dutch / English helper) ===
A small built-in helper extracted the following real "preferably / more preferably"
cascades from this source (Dutch `bij voorkeur` / English `preferably`). Quote at
least one in the generated prompt's anti-truncation section. ALSO perform your own
cascade scan (per the SOURCE-AWARE ANALYSIS section above) using the source
language's actual preference vocabulary — this helper only covers Dutch and English.

{source_cascades}
''' if source_cascades else ""}{f'''=== PATENT DOMAIN ===
The document was classified as a PATENT text. The generated prompt's ROLE should
frame itself as a PATENT translator (not a generic legal or technical one) and apply
EPO drafting conventions throughout (claim structure, "comprising"/"omvattende"
conventions, reference-numeral handling, embodiment language, etc.).
''' if detected_domain == 'patent' else ""}=== TRANSLATOR-COMMENT METHODOLOGY (REQUIRED IN EVERY GENERATED PROMPT) ===

Every prompt you generate MUST embed the following silent-correction-with-flagged-
comment methodology. This is a project-wide Supervertaler standard for all
AutoPrompt-generated prompts, required regardless of source language or domain.
Do not omit it because the source looks clean — defects appear in nearly every
real document, and the methodology must be in the prompt so the translator AI
knows how to handle them when (not if) they do.

**The methodology, in brief:**

The translator AI silently corrects obvious mechanical defects in the source
(typos, broken words across whitespace, hanging mid-sentence breaks, doubled
spaces, stray punctuation, missing inflections, reference-numeral mismatches
that are unambiguous in context, missing diacritics, etc. — the translator AI
identifies the categories appropriate to the actual source language).

For every silent correction, the translator AI appends ONE concise comment at
the very end of the segment, in this exact format:

    ⟦TC: short factual description of the fix(es)⟧

- Multiple fixes in one segment are joined with semicolons inside ONE marker.
  Never more than one ⟦TC: ...⟧ per segment.
- Segments with no defects emit NO marker. Do not emit empty ⟦TC: ⟧.
- The opening and closing delimiters MUST be U+27E6 (MATHEMATICAL LEFT WHITE
  SQUARE BRACKET) and U+27E7 (MATHEMATICAL RIGHT WHITE SQUARE BRACKET). These
  characters do not occur in source documents, so they are safe as out-of-band
  markers and can be reliably extracted in post-processing.
- Where the silent correction inserts a word or short phrase the translator
  supplied to fill a clear gap, that supplied text is wrapped in standard
  ASCII square brackets [like this] INSIDE the running translation. The
  trailing ⟦TC: ...⟧ marker then references this, e.g.
  ⟦TC: [bracketed text] supplied to close hanging sentence⟧.
- The comment body is concise — typically 5 to 20 words. Noun-phrase / sentence-
  fragment style; avoid full sentences, first-person ("I", "the translator",
  "the LLM"), or apologetic hedging.
- The marker is the FINAL content of the segment, separated from the running
  text by exactly one regular space, with no line break, no full stop, and no
  other punctuation between.
- Markers attach to THEIR OWN segment's end. Never pool markers at the end of
  the batch or response - each fix stays with the segment it describes.

**What the methodology MUST NOT silently correct** (the generated prompt MUST
state these as hard exclusions, regardless of domain):

- Numerical values, dates, currency figures, dosages (legal / regulatory weight).
- Anything that changes legal scope (claim language, contract terms, statutory
  references, etc.) — preserve faithfully even if awkward.
- Long, repetitive, or awkward source prose — length and repetition are not
  defects.
- Synonym variation that may be deliberate (the drafter may have varied for
  effect; preserve unless clearly an error).
- Headings, identifiers, proper names, citations — preserve verbatim.
- Anything the AI cannot resolve unambiguously from immediate context. In case
  of doubt, translate faithfully and use:
  ⟦TC: source ambiguous — possible defect at "..." but preserved as written⟧

**How to embed this in the generated prompt:**

1. The generated prompt's TRANSLATION MANDATE section MUST describe the silent-
   correction methodology in terms appropriate to the source language and
   domain (the translator AI needs to know which defect categories are
   relevant — e.g. -d/-t verb-ending typos for Dutch, missing umlauts for
   German, accent slips for French, conjugation typos for Spanish/Italian).
2. The generated prompt MUST include a dedicated section titled
   "TRANSLATOR COMMENT FORMAT" (or equivalent) near the end with the exact
   ⟦TC: ...⟧ spec verbatim, plus 4–6 example comment bodies adapted to the
   source language and domain. Example bodies for reference (the LLM should
   produce equivalents for the actual source language):

       ⟦TC: "verzekerd" corrected to "verzekert"⟧
       ⟦TC: stray space before full stop closed⟧
       ⟦TC: doubled space inside sentence collapsed⟧
       ⟦TC: hanging mid-sentence break reconstructed; [bracketed text] supplied⟧
       ⟦TC: "achterzijde (6)" corrected to (5) per antecedent in same paragraph⟧
       ⟦TC: source ambiguous — possible defect at "..." but preserved as written⟧

3. The generated prompt's PREFLIGHT SELF-CHECK and POST-TRANSLATION INTEGRITY
   sections MUST include a check that any silent correction has its
   corresponding ⟦TC: ...⟧ marker at the segment end, and that segments
   without corrections have no marker.
4. The generated prompt's OUTPUT FORMAT section MUST note that ⟦ and ⟧
   (U+27E6 / U+27E7) are the sole exception to the "ASCII output only" rule —
   they are the deliberate out-of-band comment delimiter.

The translator's comments appear inline in the target text as ⟦TC: ...⟧.
They can be extracted programmatically in downstream tooling (e.g. into Trados
Studio comments) but the prompt itself does not need to address extraction —
it just produces the markers reliably.

=== CONSTRAINT LANGUAGE REQUIREMENTS ===
Use strong, unambiguous language throughout the generated prompt:
- "NON-NEGOTIABLE" for translation mandate and core rules
- "LOCKED" and "MANDATORY" for termbase and style rules
- "ABSOLUTE" for formatting preservation requirements
- "MUST" and "MUST NOT" throughout (never "should", "try to", or "consider")
- Describe violations as critical errors

=== PROJECT CONTEXT SECTION ===
Analyze the document content above and write a 3-8 sentence PROJECT CONTEXT section that describes:
- What the document is about (invention, contract, product, procedure, etc.)
- The specific technology/domain/subject matter
- Key components, parties, or concepts involved
This section is marked "FOR MODEL UNDERSTANDING ONLY – DO NOT OUTPUT" in the final prompt.

=== OUTPUT INSTRUCTIONS ===
1. The prompt content must be ready to use – NO placeholders like [Translation] or [Source Language]
2. Use actual values: {source_lang} and {target_lang}
3. Include ALL termbase terms in the termbase section (do not summarize or sample)
4. The prompt should be comprehensive (2000-5000 words)
5. Output the prompt content between the delimiters shown below – NOTHING else

=== FORMATTING: USE PROPER MARKDOWN ===
The generated prompt is written to a `.md` file in the user's shared prompt library and is
read both by humans (in Markdown-aware editors) and by the LLM at translation time. Format
it as PROPER MARKDOWN, not plain text dressed up as a numbered list:

- Open with a `# H1` heading for the prompt title and one or two `## H2` subtitles.
- Each major numbered section MUST be a `## H2` heading (e.g. `## 1. ROLE`,
  `## 2. TRANSLATION MANDATE`, `## 13. PROJECT-SPECIFIC TERMBASE`).
- Use `### H3` for subsections inside a major section (e.g. `### Absolute requirements`).
- Use `-` bullet lists for absolute-requirements, absolute-prohibitions, rule lists, and
  any other enumerable content. One item per line. No prose paragraphs masquerading as lists.
- Use `**bold**` for emphasised terms, locked glossary keywords, and section labels.
- Render the PROJECT-SPECIFIC TERMBASE as a proper Markdown table:

      | Dutch (source) | English (locked target) | Notes |
      |---|---|---|
      | inrichting | device | EPO standard; never "apparatus" |

- Use `---` horizontal rules to separate major sections where it aids scanability.
- Use fenced code blocks (```) only for actual code / file-path / API-name examples.

IMPORTANT: this Markdown formatting requirement applies to the GENERATED PROMPT (what you
write between the delimiters below). It does NOT change the inner "OUTPUT FORMAT" rule that
the generated prompt itself imposes on the translator AI ("translation only, no markdown
formatting in the translation output") – that rule governs what the translator's per-segment
output looks like, and must remain in the generated prompt unchanged.

===PROMPT_START===
(Your full prompt content here as proper Markdown – no JSON escaping needed)
===PROMPT_END===

Output ONLY the delimiters and prompt content. No text before ===PROMPT_START=== or after ===PROMPT_END===."""

        return prompt

    def _list_available_prompts(self) -> str:
        """List all prompts in library"""
        if not self.library.prompts:
            return "No prompts in library"
        
        lines = []
        for path, data in list(self.library.prompts.items())[:20]:  # First 20
            name = data.get('name', Path(path).stem)
            folder = Path(path).parent.name
            lines.append(f"- {folder}/{name}")
        
        if len(self.library.prompts) > 20:
            lines.append(f"... and {len(self.library.prompts) - 20} more")
        
        return "\n".join(lines)

    def _context_current_segment(self):
        """The segment selected in the grid, or None."""
        app = self.parent_app
        try:
            row = app.table.currentRow()
            if row >= 0 and hasattr(app, '_segment_for_grid_row'):
                return app._segment_for_grid_row(row)[0]
        except Exception:
            pass
        return None

    def _get_tm_context_data(self) -> str:
        """TM data for the AI context: the project's active TMs, and the TM
        matches for the segment selected in the grid (issue #111).

        Reads the real stores (tm_metadata_mgr / tm_database). The previous
        version looked for a ``tm_databases`` attribute that the main window
        has never had, so switching TM data on only ever sent the AI
        "No translation memories loaded"."""
        try:
            app = self.parent_app
            project = getattr(app, 'current_project', None)
            mgr = getattr(app, 'tm_metadata_mgr', None)
            tm_db = getattr(app, 'tm_database', None)
            if not project or not mgr or not tm_db:
                return "No translation memories loaded"
            project_id = getattr(project, 'id', None)
            tm_ids = mgr.get_active_tm_ids(project_id) if project_id else []
            if not tm_ids:
                return "No translation memories are switched on for this project"

            lines = ["Translation memories switched on for this project:"]
            names = {}
            for tm in mgr.get_all_tms():
                if tm.get('tm_id') in tm_ids:
                    names[tm['tm_id']] = tm.get('name') or tm['tm_id']
                    lines.append(f"- {names[tm['tm_id']]} "
                                 f"({tm.get('entry_count', 0) or 0:,} entries)")

            seg = self._context_current_segment()
            if seg is not None and (seg.source or '').strip():
                matches = tm_db.search_all(seg.source, tm_ids=tm_ids, max_matches=5)
                lines.append(f"\nTM matches for the current segment ({seg.id}): {seg.source}")
                if matches:
                    for m in matches:
                        pct = m.get('match_pct', 0)
                        tm_name = names.get(m.get('tm_id'), m.get('tm_name', ''))
                        lines.append(f"- {pct}% [{tm_name}] "
                                     f"{m.get('source', '')} → {m.get('target', '')}")
                else:
                    lines.append("- (no TM matches)")
            return "\n".join(lines)

        except Exception as e:
            return f"Error loading TM data: {e}"

    # Upper bounds that keep the termbase context a reasonable size in the prompt.
    _CONTEXT_TERM_SEGMENTS = 2000
    _CONTEXT_TERM_LIMIT = 150

    def _get_termbase_context_data(self) -> str:
        """Termbase data for the AI context: the terms from the project's active
        termbases that actually occur in the document (issue #111).

        Uses the main window's in-memory termbase index – the same lookup that
        feeds TermLens – so forbidden and non-translatable terms are marked as
        such. The previous version looked for ``termbases`` /
        ``termbase_manager`` attributes that do not exist, so it only ever
        sent "No termbases loaded"."""
        try:
            app = self.parent_app
            project = getattr(app, 'current_project', None)
            if not project or not getattr(project, 'segments', None) \
                    or not hasattr(app, '_search_termbase_in_memory'):
                return "No termbases loaded"

            found = {}
            for seg in project.segments[:self._CONTEXT_TERM_SEGMENTS]:
                if not (seg.source or '').strip():
                    continue
                for match in (app._search_termbase_in_memory(seg.source) or {}).values():
                    source = match.get('source', '')
                    if not source or source.lower() in found:
                        continue
                    found[source.lower()] = match
                    if len(found) >= self._CONTEXT_TERM_LIMIT:
                        break
                if len(found) >= self._CONTEXT_TERM_LIMIT:
                    break

            if not found:
                return "No termbase terms occur in this document"

            lines = [f"{len(found)} termbase terms that occur in this document "
                     "(source | target):"]
            for match in found.values():
                note = ""
                if match.get('is_nontranslatable'):
                    note = "  (non-translatable: keep as is)"
                elif match.get('forbidden'):
                    note = "  (FORBIDDEN translation: do not use)"
                lines.append(f"| {match.get('source', '')} | {match.get('translation', '')} |{note}")
            if len(found) >= self._CONTEXT_TERM_LIMIT:
                lines.append(f"(first {self._CONTEXT_TERM_LIMIT} terms only)")
            return "\n".join(lines)

        except Exception as e:
            return f"Error loading termbase data: {e}"

    def _attach_file(self):
        """Attach a file to the conversation"""
        file_path, _ = QFileDialog.getOpenFileName(
            None,
            "Attach File",
            "",
            "Documents (*.pdf *.docx *.txt *.md);;All Files (*.*)"
        )
        if not file_path:
            return
        
        try:
            file_path_obj = Path(file_path)
            
            # Read file content based on type
            content = ""
            file_type = file_path_obj.suffix.lower()
            conversion_note = ""
            
            if file_type == '.txt' or file_type == '.md':
                with open(file_path, 'r', encoding='utf-8') as f:
                    content = f.read()
            elif file_type in ['.pdf', '.docx', '.pptx', '.xlsx']:
                # Use markitdown for document conversion
                try:
                    from markitdown import MarkItDown
                    md = MarkItDown()
                    result = md.convert(file_path)
                    content = result.text_content
                    conversion_note = f" (converted to markdown: {len(content):,} chars)"
                except ImportError:
                    content = f"[{file_type.upper()} file: {file_path_obj.name}]\n(markitdown not installed - run: pip install markitdown)"
                    conversion_note = " (conversion unavailable)"
                except Exception as e:
                    content = f"[{file_type.upper()} file: {file_path_obj.name}]\n(Conversion error: {e})"
                    conversion_note = f" (conversion failed: {e})"
            else:
                try:
                    with open(file_path, 'r', encoding='utf-8') as f:
                        content = f.read()
                except:
                    content = f"[Binary file: {file_path_obj.name}]"
            
            # Save to AttachmentManager (persistent storage)
            file_id = self.attachment_manager.attach_file(
                original_path=str(file_path),
                markdown_content=content,
                original_name=file_path_obj.name,
                conversation_id=None  # Optional conversation tracking
            )

            if file_id:
                # Add to attached files for backward compatibility
                file_data = {
                    'path': str(file_path),
                    'name': file_path_obj.name,
                    'content': content,
                    'type': file_type,
                    'size': len(content),
                    'attached_at': datetime.now().isoformat(),
                    'file_id': file_id  # Store ID for later reference
                }
                self.attached_files.append(file_data)

                # Update UI
                self._update_context_sidebar()

                # Add message
                self._add_chat_message(
                    "system",
                    f"📎 File attached: **{file_path_obj.name}**\n"
                    f"Type: {file_type}, Size: {len(content):,} chars{conversion_note}\n\n"
                    f"You can now ask questions about this file."
                )

                self._save_conversation_history()
            else:
                QMessageBox.warning(None, "Attachment Error", "Failed to save attachment to disk.")
            
        except Exception as e:
            QMessageBox.warning(None, "Attachment Error", f"Failed to attach file:\n{e}")
    
    def _update_context_sidebar(self):
        """Update the context sidebar with current state"""
        # Update current document display
        self._update_current_document_display()

        # Update attached files list
        if hasattr(self, 'attached_files_list_layout'):
            self._refresh_attached_files_list()

    def _update_current_document_display(self):
        """Update the current document section in the sidebar"""
        if not hasattr(self, 'context_current_doc'):
            return

        # Get document info from parent app
        doc_info = "No document loaded"

        if hasattr(self.parent_app, 'current_project') and self.parent_app.current_project:
            project = self.parent_app.current_project
            # Get project name
            project_name = getattr(project, 'name', 'Unnamed Project')

            # Get document info
            if hasattr(self.parent_app, 'current_document_path') and self.parent_app.current_document_path:
                doc_path = Path(self.parent_app.current_document_path)
                doc_info = f"{project_name}\n{doc_path.name}"
            elif hasattr(project, 'source_file') and project.source_file:
                doc_path = Path(project.source_file)
                doc_info = f"{project_name}\n{doc_path.name}"
            elif getattr(project, 'is_multifile', False) and getattr(project, 'files', None):
                file_count = len(project.files)
                doc_info = f"{project_name}\n{file_count} file{'s' if file_count != 1 else ''}"
            elif getattr(project, 'sdlxliff_source_paths', None):
                file_count = len(project.sdlxliff_source_paths)
                doc_info = f"{project_name}\n{file_count} SDLXLIFF file{'s' if file_count != 1 else ''}"
            else:
                # Project loaded but no specific file info – show segment count if available
                seg_count = len(project.segments) if getattr(project, 'segments', None) else 0
                if seg_count > 0:
                    doc_info = f"{project_name}\n{seg_count} segments"
                else:
                    doc_info = f"{project_name}"

        # Update the label (find the description label in the section)
        # The section has a QVBoxLayout with [title_label, desc_label]
        layout = self.context_current_doc.layout()
        if layout and layout.count() >= 2:
            desc_label = layout.itemAt(1).widget()
            if isinstance(desc_label, QLabel):
                desc_label.setText(doc_info)

    def _get_document_content_preview(self, max_chars=3000):
        """
        Get a preview of the current document content.

        Tries multiple methods to access document content:
        1. From parent_app segments (if available)
        2. From project source_segments or target_segments
        3. Direct file read if needed

        Returns:
            String with document preview (first max_chars characters) or None
        """
        try:
            # Method 1: Try to get segments from parent app
            if hasattr(self.parent_app, 'segments') and self.parent_app.segments:
                segments = self.parent_app.segments
                # Combine segment source text
                lines = []
                for seg in segments[:50]:  # First 50 segments
                    if hasattr(seg, 'source'):
                        lines.append(seg.source)
                    elif isinstance(seg, dict) and 'source' in seg:
                        lines.append(seg['source'])

                if lines:
                    content = '\n'.join(lines)
                    return content[:max_chars]

            # Method 2: Try to get from current project's segments
            if hasattr(self.parent_app, 'current_project') and self.parent_app.current_project:
                project = self.parent_app.current_project

                # Check for source_segments attribute
                if hasattr(project, 'source_segments') and project.source_segments:
                    lines = []
                    for seg in project.source_segments[:50]:
                        if isinstance(seg, str):
                            lines.append(seg)
                        elif hasattr(seg, 'text'):
                            lines.append(seg.text)
                        elif isinstance(seg, dict) and 'text' in seg:
                            lines.append(seg['text'])

                    if lines:
                        content = '\n'.join(lines)
                        return content[:max_chars]

            # Method 3: Check if we have a cached markdown conversion
            if hasattr(self, '_cached_document_markdown') and self._cached_document_markdown:
                return self._cached_document_markdown[:max_chars]

            # Method 4: Try converting the source document file with markitdown
            if hasattr(self.parent_app, 'current_document_path') and self.parent_app.current_document_path:
                doc_path = Path(self.parent_app.current_document_path)
                if doc_path.exists():
                    # Try to convert with markitdown
                    converted = self._convert_document_to_markdown(doc_path)
                    if converted:
                        # Cache for future use
                        self._cached_document_markdown = converted
                        # Also save to disk for user access
                        self._save_document_markdown(doc_path, converted)
                        return converted[:max_chars]

            return None

        except Exception as e:
            self.log_message(f"⚠ Error getting document content preview: {e}")
            return None

    def _convert_document_to_markdown(self, file_path: Path) -> str:
        """
        Convert a document to markdown using markitdown.

        Args:
            file_path: Path to the document file

        Returns:
            Markdown content or None if conversion fails
        """
        try:
            from markitdown import MarkItDown

            md = MarkItDown()
            result = md.convert(str(file_path))

            if result and hasattr(result, 'text_content'):
                return result.text_content
            elif isinstance(result, str):
                return result

            return None

        except Exception as e:
            self.log_message(f"⚠ Error converting document to markdown: {e}")
            return None

    def _save_document_markdown(self, original_path: Path, markdown_content: str):
        """
        Save the markdown conversion of the current document.

        Saves to: user_data_private/ai_assistant/current_document/

        Args:
            original_path: Original document file path
            markdown_content: Converted markdown content
        """
        try:
            # Create directory for current document markdown
            doc_dir = self.user_data_path / "workbench" / "ai_assistant" / "current_document"
            doc_dir.mkdir(parents=True, exist_ok=True)

            # Create filename based on original
            md_filename = original_path.stem + ".md"
            md_path = doc_dir / md_filename

            # Save markdown content
            md_path.write_text(markdown_content, encoding='utf-8')

            # Save metadata
            metadata = {
                "original_file": str(original_path),
                "original_name": original_path.name,
                "converted_at": datetime.now().isoformat(),
                "markdown_file": str(md_path),
                "size_chars": len(markdown_content)
            }

            meta_path = doc_dir / (original_path.stem + ".meta.json")
            meta_path.write_text(json.dumps(metadata, indent=2), encoding='utf-8')

            self.log_message(f"✓ Saved document markdown: {md_filename}")

        except Exception as e:
            self.log_message(f"⚠ Error saving document markdown: {e}")

    def generate_markdown_for_current_document(self) -> bool:
        """
        Public method to generate markdown for the current document.
        Called by main app when auto-markdown is enabled.

        Returns:
            True if markdown was generated successfully, False otherwise
        """
        try:
            # Check if we have a current document
            if not hasattr(self.parent_app, 'current_document_path') or not self.parent_app.current_document_path:
                return False

            doc_path = Path(self.parent_app.current_document_path)
            if not doc_path.exists():
                return False

            # Convert to markdown
            markdown_content = self._convert_document_to_markdown(doc_path)
            if not markdown_content:
                return False

            # Save markdown and metadata
            self._save_document_markdown(doc_path, markdown_content)

            # Cache for session
            self._cached_document_markdown = markdown_content

            self.log_message(f"✓ Generated markdown for {doc_path.name}")
            return True

        except Exception as e:
            self.log_message(f"⚠ Error generating markdown: {e}")
            return False

    def _get_segment_info(self) -> str:
        """
        Get structured segment information for AI context.

        Returns:
            Formatted string with segment count and ALL segments, or None if no segments available
        """
        try:
            segments = None

            # Try to get segments from parent app (preferred - most current)
            if hasattr(self.parent_app, 'current_project') and self.parent_app.current_project:
                project = self.parent_app.current_project
                if hasattr(project, 'segments') and project.segments:
                    segments = project.segments

            if not segments:
                return None

            total_count = len(segments)

            # Build segment overview
            lines = []
            lines.append(f"- Total segments: {total_count}")

            # Add statistics
            translated_count = sum(1 for seg in segments if seg.target and seg.target.strip())
            lines.append(f"- Translated: {translated_count}/{total_count}")

            # Include ALL segments (up to 500 to stay within token limits)
            # This allows the AI to search and answer questions about the full document
            max_segments = min(500, total_count)
            lines.append(f"\nDocument segments ({max_segments} of {total_count}):")

            for seg in segments[:max_segments]:
                # Use full source text (not truncated) so AI can search for terms
                source_text = seg.source.replace('\n', ' ')  # Normalize newlines
                target_text = ""
                if seg.target:
                    target_text = seg.target.replace('\n', ' ')

                lines.append(f"\nSegment {seg.id}: Source:{source_text}; Target:{target_text}; Status:{seg.status}")

            if total_count > max_segments:
                lines.append(f"\n... and {total_count - max_segments} more segments (not shown)")

            return "\n".join(lines)

        except Exception as e:
            self.log_message(f"⚠ Error getting segment info: {e}")
            return None

    def _send_chat_message(self):
        """Legacy: send chat message. Now handled by ChatViewWidget._send_message."""
        # This is only called if old code still references it directly.
        if self._grid_chat_view:
            self._grid_chat_view._send_message()
    
    def _build_ai_context(self, user_message: str) -> str:
        """Build full context for AI request"""
        parts = []

        # System context
        parts.append("You are an AI assistant for Supervertaler, a professional translation tool.")
        parts.append("\nAVAILABLE RESOURCES:")

        # Current document/project info
        if hasattr(self.parent_app, 'current_project') and self.parent_app.current_project:
            project = self.parent_app.current_project
            project_name = getattr(project, 'name', 'Unnamed Project')
            parts.append(f"- Current Project: {project_name}")

            if hasattr(self.parent_app, 'current_document_path') and self.parent_app.current_document_path:
                doc_path = Path(self.parent_app.current_document_path)
                parts.append(f"- Current Document: {doc_path.name}")
            elif hasattr(project, 'source_file') and project.source_file:
                doc_path = Path(project.source_file)
                parts.append(f"- Current Document: {doc_path.name}")

            # Add language pair info if available
            if hasattr(project, 'source_lang') and hasattr(project, 'target_lang'):
                parts.append(f"- Language Pair: {project.source_lang} → {project.target_lang}")

            # Add segment information if available
            segment_info = self._get_segment_info()
            if segment_info:
                parts.append(f"\nDOCUMENT SEGMENTS:")
                parts.append(segment_info)

            # Add document content preview if available (only if no segments)
            elif not segment_info:
                doc_content = self._get_document_content_preview()
                if doc_content:
                    parts.append(f"\nCURRENT DOCUMENT CONTENT (first 3000 characters):")
                    parts.append(doc_content)

        parts.append(f"- Prompt Library: {len(self.library.prompts)} prompts")
        parts.append(f"- Attached Files: {len(self.attached_files)} files")

        # TM / termbase data, when switched on in Available Context or with the
        # chat's TM / Termbase chips (issue #111). Chat messages never carried
        # these before, even with the toggles on.
        if getattr(self, 'include_tm_data', False):
            parts.append("\nTRANSLATION MEMORY:")
            parts.append(self._get_tm_context_data())
        if getattr(self, 'include_termbase_data', False):
            parts.append("\nTERMBASE:")
            parts.append(self._get_termbase_context_data())

        # Add action system instructions (Phase 2)
        parts.append(self.ai_action_system.get_system_prompt_addition())
        
        # Recent conversation (last 5 messages)
        if len(self.chat_history) > 1:
            parts.append("\nRECENT CONVERSATION:")
            for msg in self.chat_history[-5:]:
                if msg['role'] in ['user', 'assistant']:
                    parts.append(f"{msg['role'].upper()}: {msg['content'][:200]}")
        
        # Attached files content
        if self.attached_files:
            parts.append("\nATTACHED FILES CONTENT:")
            for file in self.attached_files[-3:]:  # Last 3 files
                parts.append(f"\n--- {file['name']} ---")
                parts.append(file['content'][:2000])  # First 2000 chars
        
        # User's current message
        parts.append(f"\nUSER QUESTION:\n{user_message}")
        
        return "\n".join(parts)
    
    def refresh_llm_client(self):
        """Refresh LLM client when settings change"""
        self.chat_backend.refresh_llm_client()

    def _context_aware_send(self, user_text: str, images=None):
        """
        Context-aware send: builds AI context, calls backend, handles action system.
        This method is monkey-patched onto each ChatViewWidget._do_send.
        """
        context = self._build_ai_context(user_text)

        # Trados-aware mode: prepend the active Trados project context
        # (fetched via the localhost Supervertaler Bridge) when the bridge is
        # reachable AND the user hasn't toggled the Trados chip off.
        # Failure here is silently swallowed – the message is sent anyway
        # without Trados grounding rather than being blocked on a
        # transient network/bridge issue.
        trados_block = ""
        try:
            from modules.trados_bridge_client import TradosBridgeClient, format_context_for_prompt
            pref = getattr(self.parent_app, "_trados_chip_pref", "auto")
            if pref != "off":
                # Always use the shared singleton so connection pooling
                # and the cached-availability flag (kept fresh by
                # TradosBridgePoller) work across every call site.
                client = TradosBridgeClient.shared()
                self.parent_app._trados_bridge_client = client
                if client.is_available():
                    ctx = client.fetch_active_context()
                    if ctx:
                        trados_block = format_context_for_prompt(ctx) or ""
        except Exception:
            trados_block = ""

        # Choose system prompt
        system_prompt = """You are an AI assistant for Supervertaler, a professional translation workbench.

You can execute actions using a special format. When you need to create, modify, or manage prompts, output ACTION blocks in this EXACT format:

ACTION:function_name PARAMS:{"param1": "value1", "param2": "value2"}

Available actions:
- create_prompt: Create a new prompt. Required params: name, content. Optional: folder, description, activate
- update_prompt: Update an existing prompt. Required params: name. Optional: content, folder, description
- delete_prompt: Delete a prompt. Required params: name
- list_prompts: List all prompts. Optional params: folder
- activate_prompt: Set a prompt as active. Required params: name

IMPORTANT:
1. Output ONLY the ACTION block when asked to create/modify prompts - no explanatory text
2. The ACTION must be on a single line (PARAMS JSON can be multiline if needed)
3. Use valid JSON for PARAMS (double quotes for strings, escape special characters)
4. Do not wrap in code fences or add any markdown formatting"""

        if trados_block:
            system_prompt = trados_block + "\n" + system_prompt

        try:
            response, metadata = self.chat_backend.send_ai_request(
                context, system_prompt, images=images
            )

            if response and response.strip():
                # Parse and execute actions
                cleaned_response, action_results = self.ai_action_system.parse_and_execute(response)

                if cleaned_response and cleaned_response.strip():
                    self.chat_backend.add_message("assistant", cleaned_response, metadata=metadata)

                if action_results:
                    formatted_results = self.ai_action_system.format_action_results(action_results)
                    self.chat_backend.add_message("system", formatted_results)
                elif not (cleaned_response and cleaned_response.strip()):
                    self.chat_backend.add_message("system", "\u26A0 AI responded but no actions were found.")

                # Reload prompt library if prompts were modified
                if action_results and any(
                    r['action'] in ('create_prompt', 'update_prompt', 'delete_prompt', 'activate_prompt')
                    for r in action_results if r['success']
                ):
                    self.library.load_all_prompts()
                    if hasattr(self, 'tree_widget') and self.tree_widget:
                        self._refresh_tree()
                    if hasattr(self, '_update_active_prompt_display'):
                        self._update_active_prompt_display()
            else:
                self.chat_backend.add_message("system", "\u26A0 Received empty response from AI.")

        except Exception as e:
            import traceback
            self.log_message(f"[AI Assistant] \u274C ERROR: {traceback.format_exc()}")
            self.chat_backend.add_message(
                "system",
                f"\u26A0 Error communicating with AI: {e}\n\nCheck the log for details.",
            )

    def _send_ai_request(self, prompt: str, is_analysis: bool = False,
                         images=None):
        """Send request to AI and handle response.

        Used by _analyze_and_generate (AutoPrompt) and legacy code paths.
        New chat messages go through _context_aware_send instead.

        v1.10.157: the AutoPrompt (is_analysis=True) path now dispatches
        the LLM call to a QThread + progress dialog instead of running it
        synchronously on the main thread. The non-analysis path stays
        synchronous \u2014 those calls are short enough (seconds, not minutes)
        that worker-thread overhead would be net-negative.

        v1.10.178: ``images`` (optional) is the list of figure images
        the user opted into via the new "Include loaded figure images"
        checkbox in Section 2. Forwarded to the AutoPrompt worker so the
        LLM can see the document's figures during meta-prompt analysis.
        Non-analysis chat calls ignore it (text-only).
        """
        if not self.llm_client:
            self._add_chat_message(
                "system",
                "\u26A0 AI Assistant not available. Please configure API keys in Settings."
            )
            return

        if is_analysis:
            # AutoPrompt path: long, blocking, worth a worker thread.
            self._run_autoprompt_with_progress(prompt, images=images)
            return

        # Non-analysis path: keep synchronous (existing behaviour).
        try:
            self.log_message(f"[AI Assistant] Sending request ({len(prompt)} chars)")

            ai_system_prompt = "You are an AI assistant for Supervertaler, a professional translation workbench."

            response_text, metadata = self.chat_backend.send_ai_request(
                prompt, ai_system_prompt, is_analysis=False
            )

            if response_text and response_text.strip():
                cleaned_response, action_results = self.ai_action_system.parse_and_execute(response_text)
                if cleaned_response and cleaned_response.strip():
                    self._add_chat_message("assistant", cleaned_response)
                if action_results:
                    formatted_results = self.ai_action_system.format_action_results(action_results)
                    self._add_chat_message("system", formatted_results)

                if action_results and any(
                    r['action'] in ('create_prompt', 'update_prompt', 'delete_prompt', 'activate_prompt')
                    for r in action_results if r['success']
                ):
                    self.library.load_all_prompts()
                    if hasattr(self, 'tree_widget') and self.tree_widget:
                        self._refresh_tree()
            else:
                self._add_chat_message("system", "\u26A0 Received empty response from AI.")

        except Exception as e:
            import traceback
            self.log_message(f"[AI Assistant] \u274C ERROR: {traceback.format_exc()}")
            self._add_chat_message(
                "system",
                f"\u26A0 Error communicating with AI: {e}\n\nCheck the log for details."
            )

    def _run_autoprompt_with_progress(self, prompt: str, images=None):
        """Run the AutoPrompt LLM call in a worker thread with a progress dialog.

        v1.10.157: replaces the previous synchronous main-thread call that
        froze the window for 1-3 minutes with reasoning-capable models.

        v1.10.178: ``images`` (optional) is forwarded to the worker so
        vision-aware AutoPrompt runs ship the figure images as part of
        the request.
        """
        ai_system_prompt = (
            "You are a prompt engineering specialist for professional translation. "
            "Generate the requested translation prompt and wrap it in "
            "===PROMPT_START=== and ===PROMPT_END=== delimiters. "
            "Output ONLY the delimiters and prompt content, nothing else."
        )

        self.log_message(f"[AI Assistant] Sending AutoPrompt request ({len(prompt)} chars) via worker thread")

        # Try to surface the active provider name in the dialog text so the
        # user knows what they're waiting on. Best-effort \u2014 falls back to a
        # generic label if the chat backend doesn't expose it.
        provider_label = "the AI"
        try:
            cb = self.chat_backend
            for attr in ('provider_display_name', 'provider_name',
                         'active_provider_name', 'provider'):
                v = getattr(cb, attr, None)
                if v:
                    provider_label = str(v)
                    break
        except Exception:
            pass

        progress = QProgressDialog(
            f"Generating prompt with {provider_label}\u2026\n\n"
            "Reasoning-capable models (Opus, GPT-5, etc.) can take 1-3 minutes.\n"
            "You can cancel at any time; the in-flight request will still\n"
            "complete on the server but its result will be discarded.",
            "Cancel", 0, 0, self.main_widget
        )
        progress.setWindowTitle("Generating AutoPrompt")
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        progress.show()
        QApplication.processEvents()

        worker = _AutoPromptWorker(
            self.chat_backend, prompt, ai_system_prompt,
            images=images
        )
        # Hold a reference so the worker isn't GC'd mid-flight.
        self._autoprompt_worker = worker
        self._autoprompt_progress = progress

        def _close_progress():
            try:
                progress.close()
            except Exception:
                pass

        def _on_finished(response_text, _metadata):
            _close_progress()
            if not response_text or not response_text.strip():
                self._add_chat_message("system", "\u26A0 Received empty response from AI.")
                return
            try:
                self._handle_analysis_response(response_text)
            except Exception as e:
                import traceback
                self.log_message(f"[AI Assistant] \u274C ERROR handling AutoPrompt response: {traceback.format_exc()}")
                self._add_chat_message(
                    "system",
                    f"\u26A0 Error processing AutoPrompt response: {e}\n\nCheck the log for details."
                )

        def _on_failed(error_message):
            _close_progress()
            self.log_message(f"[AI Assistant] \u274C AutoPrompt ERROR: {error_message}")
            self._add_chat_message(
                "system",
                f"\u26A0 Error generating prompt: {error_message.splitlines()[0]}\n\n"
                "Check the log for the full traceback."
            )

        def _on_cancel():
            # We can't abort the HTTP request, but we can stop caring about
            # the result \u2014 disconnect the signals so the eventual completion
            # is silently ignored.
            try:
                worker.finished_ok.disconnect()
                worker.failed.disconnect()
            except Exception:
                pass
            self.log_message("[AI Assistant] AutoPrompt cancelled by user (in-flight request continues server-side).")
            self._add_chat_message("system", "\u26A0 AutoPrompt cancelled. Any in-flight request will still complete on the server.")

        worker.finished_ok.connect(_on_finished)
        worker.failed.connect(_on_failed)
        progress.canceled.connect(_on_cancel)

        worker.start()
    
    def _handle_analysis_response(self, response: str):
        """Handle delimiter-based response from analysis prompt generation.

        Parses ===PROMPT_START=== / ===PROMPT_END=== delimiters and creates the prompt
        programmatically, avoiding fragile JSON-in-ACTION parsing.
        """
        START_DELIM = "===PROMPT_START==="
        END_DELIM = "===PROMPT_END==="

        start_idx = response.find(START_DELIM)
        end_idx = response.find(END_DELIM)

        if start_idx == -1 or end_idx == -1 or end_idx <= start_idx:
            # Fallback: try ACTION block parsing in case LLM ignored delimiter instructions
            self.log_message("[AI Assistant] No delimiters found, trying ACTION block fallback...")
            cleaned_response, action_results = self.ai_action_system.parse_and_execute(response)
            if action_results:
                formatted_results = self.ai_action_system.format_action_results(action_results)
                self._add_chat_message("system", formatted_results)
                self._post_prompt_creation_refresh(action_results)
            else:
                # Last resort: treat the entire response as prompt content
                self.log_message("[AI Assistant] No delimiters or ACTION blocks found – using full response as prompt")
                self._create_prompt_from_content(response.strip())
            return

        # Extract content between delimiters
        prompt_content = response[start_idx + len(START_DELIM):end_idx].strip()

        if not prompt_content:
            self._add_chat_message("system", "⚠ AI returned empty prompt content. Please try again.")
            return

        self.log_message(f"[AI Assistant] Extracted prompt content: {len(prompt_content)} characters")
        self._create_prompt_from_content(prompt_content)

    def _create_prompt_from_content(self, content: str):
        """Create a prompt in the library from raw content string.

        v1.10.157: instead of silently auto-saving into a hard-coded folder,
        opens a save dialog where the user can name the prompt and pick a
        folder. Suggested name is the current project name; suggested
        folder is "Translate".
        """
        # Detect domain + language pair so we have a sensible fallback name
        # if the project has no name (e.g. unsaved fresh import).
        source_lang = "Source"
        target_lang = "Target"
        detected_domain = "general"

        project = getattr(self.parent_app, 'current_project', None)
        if project:
            if hasattr(project, 'source_lang') and project.source_lang:
                source_lang = _resolve_lang_name(project.source_lang)
            elif hasattr(project, 'source_language') and project.source_language:
                source_lang = _resolve_lang_name(project.source_language)
            if hasattr(project, 'target_lang') and project.target_lang:
                target_lang = _resolve_lang_name(project.target_lang)
            elif hasattr(project, 'target_language') and project.target_language:
                target_lang = _resolve_lang_name(project.target_language)

        # Try to detect domain from first few lines of content
        content_lower = content[:500].lower()
        for domain in ['patent', 'legal', 'medical', 'technical', 'financial', 'marketing']:
            if domain in content_lower:
                detected_domain = domain
                break

        # Check if multi-file project (used in description)
        is_multifile = False
        file_count = 0
        if project:
            is_multifile = getattr(project, 'is_multifile', False)
            file_count = len(getattr(project, 'files', []) or [])

        # Suggested name = project name if we have one, else fall back to
        # the detected pattern. The project name is what the user already
        # thinks of this work as ("BRANTS (URSU-008-BE-EP)"), so it's the
        # most useful default. The pattern fallback ("Patent Translation
        # Dutch-English") still applies when there's no current project.
        if project and getattr(project, 'name', None):
            suggested_name = project.name
        elif is_multifile and file_count > 1:
            suggested_name = f"{detected_domain.title()} Translation {source_lang}-{target_lang} ({file_count} files)"
        else:
            suggested_name = f"{detected_domain.title()} Translation {source_lang}-{target_lang}"

        if is_multifile and file_count > 1:
            description = (f"AI-generated {detected_domain} domain prompt for "
                           f"{file_count}-file project with per-file guidance")
        else:
            description = (f"AI-generated {detected_domain} domain prompt with "
                           f"anti-truncation controls and self-verification")

        # Folder dropdown: list existing top-level folders in the library +
        # the canonical defaults. Sort alphabetically but float "Translate"
        # to the top because it's the default and the most-likely target
        # for an AutoPrompt-generated translation prompt.
        #
        # v1.10.158 fix: library keys are filesystem-joined paths, which use
        # '\\' on Windows. The previous '/'-only check matched nothing on
        # Windows and the dropdown only showed the hardcoded "Translate".
        # Path.parts handles both separators consistently.
        folders = set()
        for relative_path in self.library.prompts.keys():
            parts = Path(relative_path).parts
            if len(parts) > 1:
                folders.add(parts[0])
        # Make sure Translate is always offered even on a fresh install.
        folders.add('Translate')
        sorted_folders = sorted(folders, key=lambda s: s.lower())
        if 'Translate' in sorted_folders:
            sorted_folders.remove('Translate')
            sorted_folders = ['Translate'] + sorted_folders

        dialog = _AutoPromptSaveDialog(
            generated_content=content,
            suggested_name=suggested_name,
            available_folders=sorted_folders,
            default_folder='Translate',
            parent=self.main_widget,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            self._add_chat_message(
                "system",
                "⚠ AutoPrompt save cancelled. The generated content is in the chat "
                "log above if you'd like to copy it out manually."
            )
            return

        chosen_name = dialog.get_name()
        chosen_folder = dialog.get_folder()

        if not chosen_name:
            QMessageBox.warning(
                self.main_widget, "Name required",
                "Please provide a name for the prompt."
            )
            return

        # Use the ai_action_system to create the prompt (reuses existing save/activate logic).
        params = {
            'name': chosen_name,
            'content': content,
            'folder': chosen_folder,
            'description': description,
            'activate': True,
        }

        try:
            result = self.ai_action_system._action_create_prompt(params)
            if result.get('success'):
                self._add_chat_message("system", f"✅ {result['message']}")
                # Refresh library
                self.library.load_all_prompts()
                if hasattr(self, 'tree_widget') and self.tree_widget:
                    self._refresh_tree()
                if hasattr(self, '_update_active_prompt_display'):
                    self._update_active_prompt_display()
                # v1.10.156: explicitly drive the UI-aware activate path so the
                # "Custom Prompt:" label, editor pane, and the in-library
                # ⭐ marker actually refresh. _action_create_prompt already
                # called library.set_primary_prompt() (because activate=True
                # in params), but that only updated in-memory library state —
                # it didn't touch the QT-side UI elements. Calling
                # _set_primary_prompt() here is idempotent on the library
                # side (set to the same path) but pulls the UI into sync.
                new_path = result.get('path')
                if new_path and new_path in self.library.prompts:
                    self._set_primary_prompt(new_path)
                # v1.10.156: persist the new active-prompt selection to the
                # .svproj NOW, not on the next auto-save. Without this, a
                # restart inside the auto-save window (default 5 min)
                # silently lost the AutoPrompt activation — exactly what
                # users reported when they saw "[None selected]" after
                # relaunching minutes after running AutoPrompt.
                try:
                    if (hasattr(self.parent_app, 'save_project')
                            and getattr(self.parent_app, 'current_project', None)
                            and getattr(self.parent_app, 'project_file_path', None)):
                        self.parent_app.save_project()
                except Exception as save_err:
                    self.log_message(f"[AI Assistant] ⚠ Could not save project after AutoPrompt: {save_err}")
                # Navigate the user to the newly-created prompt so they can
                # immediately see and edit it (rather than leaving them in
                # the chat view with just a confirmation message).
                self._navigate_to_created_prompt(new_path)
            else:
                self._add_chat_message("system", f"⚠ Failed to create prompt: {result.get('message', 'Unknown error')}")
        except Exception as e:
            self.log_message(f"[AI Assistant] ❌ Failed to create prompt: {e}")
            self._add_chat_message("system", f"⚠ Failed to create prompt: {e}")

    def _post_prompt_creation_refresh(self, action_results):
        """Refresh UI after prompt creation via ACTION blocks."""
        if any(r['action'] in ['create_prompt', 'update_prompt', 'delete_prompt', 'activate_prompt']
               for r in action_results if r.get('success')):
            self.library.load_all_prompts()
            if hasattr(self, 'tree_widget') and self.tree_widget:
                self._refresh_tree()
            if hasattr(self, '_update_active_prompt_display'):
                self._update_active_prompt_display()
            # If a prompt was created, navigate the user to it so they can
            # immediately see and edit it.
            for r in action_results:
                if r.get('success') and r.get('action') == 'create_prompt':
                    res = r.get('result') or {}
                    path = res.get('path')
                    if path:
                        self._navigate_to_created_prompt(path)
                        break

    def _navigate_to_created_prompt(self, relative_path: str):
        """After a prompt is auto-created (AutoPrompt), bring the user to it.

        Switches the AI tab to the Prompt Manager sub-tab, selects the new
        prompt in the library tree, scrolls it into view, and loads it into
        the Prompt Editor pane. Best-effort — silently no-ops if any of the
        UI pieces aren't available (e.g. headless contexts).
        """
        if not relative_path:
            return
        try:
            # 1. Switch to Prompt Manager sub-tab
            if hasattr(self, 'sub_tabs') and self.sub_tabs is not None:
                for i in range(self.sub_tabs.count()):
                    if "Prompt Manager" in self.sub_tabs.tabText(i):
                        self.sub_tabs.setCurrentIndex(i)
                        break

            # 2. Select + reveal the prompt in the library tree
            if hasattr(self, '_select_and_reveal_prompt'):
                self._select_and_reveal_prompt(relative_path, prefer_library_tree=True)

            # 3. Load it into the Prompt Editor pane
            if hasattr(self, '_load_prompt_in_editor'):
                self._load_prompt_in_editor(relative_path)
        except Exception as e:
            self.log_message(f"[AI Assistant] Could not navigate to new prompt: {e}")

    def _reload_chat_display(self):
        """Reload chat display from history – now handled by ChatViewWidget via signals."""
        # No-op: ChatViewWidget views auto-update via ChatBackend signals.
        # Kept for backward compatibility with callers that still reference it.
        pass
    
    def _clear_chat(self):
        """Clear chat history – delegates to ChatBackend.
        ChatViewWidgets handle their own clear confirmation dialogs.
        """
        self.chat_backend.clear_history()
        self.attached_files = []
        self._update_context_sidebar()

    def _show_chat_context_menu(self, position):
        """Legacy context menu – now handled by ChatViewWidget internally."""
        pass

    def _copy_message_to_clipboard(self, text: str):
        """Legacy copy – now handled by ChatViewWidget internally."""
        pass

    def _add_chat_message(self, role: str, message: str, save: bool = True):
        """Add a message to the chat display (delegates to ChatBackend).

        All connected ChatViewWidgets update automatically via signals.
        """
        self.chat_backend.add_message(role, message, save=save)
