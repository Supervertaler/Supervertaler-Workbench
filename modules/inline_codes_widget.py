"""
Settings → Inline Codes (issue #194)
====================================

Edits the user's inline-code patterns (``modules.inline_codes``): a table of
regular expressions, one-click common patterns, and a test box that shows what
the patterns find. Saved on every change, like the other newer settings pages.
"""

import html
from typing import Callable, Dict, List, Optional

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QAbstractItemView, QGroupBox, QHBoxLayout, QHeaderView, QLabel, QMenu, QPlainTextEdit,
    QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from modules import inline_codes

COL_ON, COL_PATTERN, COL_COMMENT = range(3)
_ERROR_BACKGROUND = QColor(220, 60, 60, 90)
SAMPLE_TEXT = ("{PKMN} can't be the same.\nYou have %d new messages from %s.\n"
               "<color=#ff0000>Warning!</color>\\nPress [[KEY_JUMP]] to jump.")


class InlineCodesWidget(QWidget):
    def __init__(self, load: Callable[[], List[Dict]], save: Callable[[List[Dict]], None],
                 parent=None, protect_tags: Optional[bool] = None,
                 on_protect_tags: Optional[Callable[[bool], None]] = None):
        super().__init__(parent)
        self._save = save
        self._loading = True

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(15)

        # Tag protection (issue #113) – about all tags, not only these codes,
        # but it lives here beside the codes it also protects.
        self.protect_cb = None
        if protect_tags is not None:
            from modules.styled_widgets import CheckmarkCheckBox
            protect_group = QGroupBox("Tag protection")
            playout = QVBoxLayout(protect_group)
            self.protect_cb = CheckmarkCheckBox(
                "Protect tags and codes in the target – treat each one as a single unit")
            self.protect_cb.setChecked(bool(protect_tags))
            self.protect_cb.setToolTip(
                "The cursor steps over a tag instead of landing inside it, Backspace just after\n"
                "a tag or Delete just before it removes the whole tag, and typing or pasting over\n"
                "part of a tag replaces the whole tag. Untick to edit tags character by character.")
            if on_protect_tags is not None:
                self.protect_cb.toggled.connect(on_protect_tags)
            playout.addWidget(self.protect_cb)
            layout.addWidget(protect_group)

        info = QLabel(
            "Inline codes are placeholders and markup in the text that must reach the translation "
            "unchanged – <code>{playerName}</code>, <code>%s</code>, <code>\\n</code>, "
            "<code>&lt;color=#f00&gt;</code> and the like, typical of software strings and game "
            "files. Describe them here with regular expressions and Supervertaler treats them "
            "like inline tags:"
            "<ul style='margin-top:4px;'>"
            "<li>they are coloured like tags in the grid;</li>"
            "<li><b>Insert next tag</b> (Ctrl+,) inserts the next code the target is missing;</li>"
            "<li><b>QA → Run QA Checks</b> lists codes a translation lost or gained;</li>"
            "<li>AI translation is told to keep them exactly as written;</li>"
            "<li>a TM match that differs only in its codes (<code>{PK}{MN}</code> vs "
            "<code>{PKMN}</code>) gets the codes of the current segment.</li></ul>"
            "Changes apply immediately – there's no Save button on this tab.")
        info.setWordWrap(True)
        info.setTextFormat(Qt.TextFormat.RichText)
        info.setStyleSheet("color: #666; font-size: 9pt; padding: 5px;")
        layout.addWidget(info)

        group = QGroupBox("Code patterns")
        glayout = QVBoxLayout(group)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["On", "Pattern (regular expression)", "Comment"])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setMinimumHeight(160)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(COL_ON, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(COL_PATTERN, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(COL_COMMENT, QHeaderView.ResizeMode.Stretch)
        self.table.itemChanged.connect(self._on_item_changed)
        self.table.itemSelectionChanged.connect(self._update_buttons)
        glayout.addWidget(self.table)

        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        glayout.addWidget(self.status_label)

        row = QHBoxLayout()
        add_btn = QPushButton("➕ Add pattern")
        add_btn.clicked.connect(self._add_empty)
        row.addWidget(add_btn)
        self.presets_btn = QPushButton("➕ Common patterns ▾")
        self.presets_btn.setToolTip("Add a ready-made pattern for a common kind of placeholder")
        menu = QMenu(self.presets_btn)
        for label, pattern, example in inline_codes.PRESETS:
            action = menu.addAction(f"{label}    e.g. {example}")
            action.triggered.connect(lambda _=False, p=pattern, l=label: self._add(p, l))
        self.presets_btn.setMenu(menu)
        row.addWidget(self.presets_btn)
        row.addStretch()
        self.remove_btn = QPushButton("Remove")
        self.remove_btn.clicked.connect(self._remove)
        row.addWidget(self.remove_btn)
        glayout.addLayout(row)
        layout.addWidget(group)

        test_group = QGroupBox("Test")
        tlayout = QVBoxLayout(test_group)
        tlayout.addWidget(QLabel("Type or paste text – the codes found are marked below:"))
        self.test_input = QPlainTextEdit(SAMPLE_TEXT)
        self.test_input.setMaximumHeight(90)
        self.test_input.textChanged.connect(self._refresh_test)
        tlayout.addWidget(self.test_input)
        self.test_output = QLabel("")
        self.test_output.setWordWrap(True)
        self.test_output.setTextFormat(Qt.TextFormat.RichText)
        self.test_output.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        tlayout.addWidget(self.test_output)
        layout.addWidget(test_group)
        layout.addStretch()

        try:
            entries = inline_codes.normalise(load())
        except Exception:
            entries = []
        for entry in entries:
            self._append_row(entry)
        self._loading = False
        self._validate()
        self._refresh_test()
        self._update_buttons()

    # ── model ↔ table ────────────────────────────────────────────────────

    def entries(self) -> List[Dict]:
        out = []
        for r in range(self.table.rowCount()):
            on = self.table.item(r, COL_ON)
            pattern = self.table.item(r, COL_PATTERN)
            comment = self.table.item(r, COL_COMMENT)
            out.append({"pattern": pattern.text() if pattern else "",
                        "enabled": on is None or on.checkState() == Qt.CheckState.Checked,
                        "comment": comment.text() if comment else ""})
        return out

    def _append_row(self, entry: Dict):
        was_loading, self._loading = self._loading, True
        r = self.table.rowCount()
        self.table.insertRow(r)
        on = QTableWidgetItem()
        on.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsSelectable)
        on.setCheckState(Qt.CheckState.Checked if entry.get("enabled", True) else Qt.CheckState.Unchecked)
        self.table.setItem(r, COL_ON, on)
        self.table.setItem(r, COL_PATTERN, QTableWidgetItem(entry.get("pattern", "")))
        self.table.setItem(r, COL_COMMENT, QTableWidgetItem(entry.get("comment", "")))
        self._loading = was_loading

    # ── actions ──────────────────────────────────────────────────────────

    def _add(self, pattern: str, comment: str = ""):
        if any(e["pattern"] == pattern for e in self.entries()):
            return
        self._append_row({"pattern": pattern, "enabled": True, "comment": comment})
        self.table.selectRow(self.table.rowCount() - 1)
        self._changed()

    def _add_empty(self):
        self._append_row({"pattern": "", "enabled": True, "comment": ""})
        r = self.table.rowCount() - 1
        self.table.selectRow(r)
        self.table.editItem(self.table.item(r, COL_PATTERN))

    def _remove(self):
        r = self.table.currentRow()
        if r >= 0:
            self.table.removeRow(r)
            self._changed()

    def _update_buttons(self):
        self.remove_btn.setEnabled(0 <= self.table.currentRow() < self.table.rowCount())

    # ── change handling ──────────────────────────────────────────────────

    def _on_item_changed(self, item):
        if not self._loading:
            self._changed()

    def _changed(self):
        if self._loading:
            return
        self._validate()
        self._refresh_test()
        self._update_buttons()
        self._save(inline_codes.normalise(self.entries()))

    def _validate(self):
        was_loading, self._loading = self._loading, True
        problems = 0
        for r, entry in enumerate(self.entries()):
            error = inline_codes.pattern_error(entry["pattern"]) if entry["enabled"] and entry["pattern"] else None
            problems += bool(error)
            for c in range(3):
                item = self.table.item(r, c)
                if item is not None:
                    item.setData(Qt.ItemDataRole.BackgroundRole, _ERROR_BACKGROUND if error else None)
                    item.setToolTip(f"⚠ {error}" if error else "")
        self._loading = was_loading
        self.status_label.setText(
            f"⚠ {problems} pattern{'s' if problems != 1 else ''} cannot be used (marked in red) – "
            "hover over it to see why." if problems else "")

    def _refresh_test(self):
        pattern = inline_codes.compile_codes(self.entries())
        text = self.test_input.toPlainText()
        if pattern is None:
            self.test_output.setText("<i>No patterns switched on yet.</i>")
            return
        spans = inline_codes.find_codes(text, pattern)
        out, pos = [], 0
        for start, end, code in spans:
            out.append(html.escape(text[pos:start]))
            out.append(f"<span style='color:#e8590c; font-weight:bold; text-decoration:underline;'>{html.escape(code)}</span>")
            pos = end
        out.append(html.escape(text[pos:]))
        marked = "".join(out).replace("\n", "<br>")
        summary = f"<b>{len(spans)} code{'s' if len(spans) != 1 else ''} found.</b>"
        self.test_output.setText(f"{summary}<br>{marked}")
