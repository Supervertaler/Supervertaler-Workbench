"""
LanguageTool dialog (issue #233): check the project's target segments for
grammar, spelling and style with LanguageTool, go through the findings, and
apply a suggestion with one click. Non-modal, so you can edit in the grid
while the list stays open.
"""

import html
import time

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QAbstractItemView, QApplication, QComboBox, QDialog, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QMenu, QMessageBox, QProgressBar, QPushButton, QTableWidget, QTableWidgetItem,
    QVBoxLayout,
)

from modules import languagetool_client as ltc

SERVER_KEY = "languagetool_server_url"


class LanguageToolDialog(QDialog):
    COLUMNS = ["Segment", "Issue", "Found", "Suggestions", "Context"]

    def __init__(self, parent, *, get_segments, get_target_language, navigate, apply_target,
                 load_settings, save_settings, get_proxies=None):
        super().__init__(parent)
        self.setWindowTitle("LanguageTool")
        self.resize(980, 560)
        self._get_segments = get_segments
        self._get_target_language = get_target_language
        self._navigate = navigate
        self._apply_target = apply_target
        self._load_settings = load_settings
        self._save_settings = save_settings
        self._get_proxies = get_proxies
        self._findings = []
        self._cancelled = False

        intro = QLabel(
            "Checks the <b>target</b> text of every segment for grammar, spelling and style "
            "mistakes with LanguageTool. Double-click a finding to go to it; "
            "right-click (or <b>Apply</b>) to use a suggestion.")
        intro.setWordWrap(True)

        server_row = QHBoxLayout()
        server_row.addWidget(QLabel("Server:"))
        self.server_combo = QComboBox()
        self.server_combo.setEditable(True)
        self.server_combo.addItem(ltc.PUBLIC_API_URL)
        self.server_combo.addItem(ltc.LOCAL_SERVER_URL)
        self.server_combo.setMinimumWidth(280)
        self.server_combo.setCurrentText(self._saved_server())
        self.server_combo.setToolTip(
            "The free public LanguageTool API, or the address of your own LanguageTool\n"
            "server. A local server is free, unlimited and keeps the text on your computer.")
        server_row.addWidget(self.server_combo)
        server_row.addSpacing(12)
        server_row.addWidget(QLabel("Language:"))
        self.language_edit = QLineEdit(ltc.lt_language(get_target_language()))
        self.language_edit.setMaximumWidth(90)
        self.language_edit.setToolTip("LanguageTool language code, e.g. nl, nl-BE, de-DE, en-GB, "
                                      "fr, or auto to let LanguageTool decide")
        server_row.addWidget(self.language_edit)
        self.check_btn = QPushButton("▶ Check target segments")
        self.check_btn.setDefault(True)
        self.check_btn.clicked.connect(self.run)
        server_row.addWidget(self.check_btn)
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self._cancel)
        server_row.addWidget(self.cancel_btn)
        server_row.addStretch(1)

        self.privacy = QLabel()
        self.privacy.setWordWrap(True)
        self.privacy.setStyleSheet("color: #666;")
        self.server_combo.currentTextChanged.connect(self._update_privacy)
        self._update_privacy()

        self.progress = QProgressBar()
        self.progress.setVisible(False)

        self.table = QTableWidget(0, len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels(self.COLUMNS)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        header = self.table.horizontalHeader()
        for c in (0, 2, 3):
            header.setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self.table.setColumnWidth(1, 280)
        self.table.cellDoubleClicked.connect(lambda r, _c: self._go_to(r))
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._context_menu)

        actions = QHBoxLayout()
        self.apply_btn = QPushButton("✔ Apply first suggestion")
        self.apply_btn.clicked.connect(lambda: self._apply_selected(0))
        actions.addWidget(self.apply_btn)
        self.ignore_btn = QPushButton("Ignore")
        self.ignore_btn.setToolTip("Remove the selected finding from this list")
        self.ignore_btn.clicked.connect(self._ignore_selected)
        actions.addWidget(self.ignore_btn)
        actions.addStretch(1)

        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.RichText)

        layout = QVBoxLayout(self)
        layout.addWidget(intro)
        layout.addLayout(server_row)
        layout.addWidget(self.privacy)
        layout.addWidget(self.progress)
        layout.addWidget(self.table, stretch=1)
        layout.addLayout(actions)
        layout.addWidget(self.status)

    # ── Settings ────────────────────────────────────────────────────────────
    def _saved_server(self):
        try:
            return (self._load_settings() or {}).get(SERVER_KEY) or ltc.PUBLIC_API_URL
        except Exception:
            return ltc.PUBLIC_API_URL

    def _server(self):
        return self.server_combo.currentText().strip().rstrip("/") or ltc.PUBLIC_API_URL

    def _update_privacy(self, *_):
        if self._server() == ltc.PUBLIC_API_URL:
            self.privacy.setText(
                "The free public API sends the text to LanguageTool's servers and allows about "
                "20 checks a minute, so a large project takes a while. For confidential work, "
                "run your own LanguageTool server (free, unlimited, offline) and enter its "
                "address, e.g. http://localhost:8081.")
        else:
            self.privacy.setText("Using your own LanguageTool server – the text stays there.")

    # ── Checking ────────────────────────────────────────────────────────────
    def _cancel(self):
        self._cancelled = True

    def _sleep(self, seconds):
        end = time.monotonic() + seconds
        while time.monotonic() < end and not self._cancelled:
            QApplication.processEvents()
            time.sleep(0.05)

    def _on_progress(self, done, total):
        self.progress.setMaximum(max(total, 1))
        self.progress.setValue(done)
        QApplication.processEvents()
        return not self._cancelled

    def run(self):
        segments = list(self._get_segments() or [])
        if not segments:
            QMessageBox.information(self, "LanguageTool", "Open a project first.")
            return
        texts = [s.target or "" for s in segments]
        if not any(t.strip() for t in texts):
            QMessageBox.information(self, "LanguageTool", "There is no target text to check yet.")
            return
        server = self._server()
        try:
            settings = self._load_settings() or {}
            settings[SERVER_KEY] = server
            self._save_settings(settings)
        except Exception:
            pass
        proxies = None
        if self._get_proxies and not server.startswith(("http://localhost", "http://127.0.0.1")):
            proxies = self._get_proxies()

        self._cancelled = False
        self.check_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)
        self.progress.setVisible(True)
        self.status.setText("Checking…")
        try:
            findings = ltc.check_texts(texts, self.language_edit.text().strip() or "auto", server,
                                       proxies=proxies, progress=self._on_progress,
                                       sleep=self._sleep)
        except Exception as e:
            self.status.setText(f"<span style='color:#c62828;'>LanguageTool could not be "
                                f"reached or refused the request: {html.escape(str(e))}</span>")
            return
        finally:
            self.check_btn.setEnabled(True)
            self.cancel_btn.setEnabled(False)
            self.progress.setVisible(False)
        self._segments = segments
        self._findings = findings
        self._fill()
        n_seg = len({f.segment_index for f in findings})
        note = " (stopped early)" if self._cancelled else ""
        self.status.setText(f"{len(findings)} finding{'s' if len(findings) != 1 else ''} in "
                            f"{n_seg} segment{'s' if n_seg != 1 else ''}{note}.")

    def _fill(self):
        self.table.setRowCount(len(self._findings))
        for r, f in enumerate(self._findings):
            seg = self._segments[f.segment_index]
            issue = f.message + (f" ({f.category})" if f.category else "")
            cells = [str(getattr(seg, 'id', f.segment_index + 1)), issue, f.text,
                     " | ".join(f.replacements) or "–", f.context]
            for c, value in enumerate(cells):
                item = QTableWidgetItem(value)
                if c == 1:
                    item.setToolTip(f.message + (f"\n\nRule: {f.rule_id}" if f.rule_id else ""))
                self.table.setItem(r, c, item)

    # ── Acting on findings ──────────────────────────────────────────────────
    def _selected(self):
        r = self.table.currentRow()
        return r if 0 <= r < len(self._findings) else None

    def _go_to(self, row):
        if 0 <= row < len(self._findings) and self._navigate:
            seg = self._segments[self._findings[row].segment_index]
            self._navigate(getattr(seg, 'id', None))

    def _context_menu(self, pos):
        row = self.table.rowAt(pos.y())
        if not 0 <= row < len(self._findings):
            return
        self.table.selectRow(row)
        menu = QMenu(self)
        for i, replacement in enumerate(self._findings[row].replacements):
            menu.addAction(f"Replace with “{replacement}”",
                           lambda i=i: self._apply_selected(i))
        if self._findings[row].replacements:
            menu.addSeparator()
        menu.addAction("Go to segment", lambda: self._go_to(row))
        menu.addAction("Ignore", self._ignore_selected)
        menu.exec(self.table.viewport().mapToGlobal(pos))

    def _apply_selected(self, choice):
        row = self._selected()
        if row is None:
            return
        finding = self._findings[row]
        if choice >= len(finding.replacements):
            self.status.setText("LanguageTool has no suggestion for this one – fix it in the grid.")
            return
        replacement = finding.replacements[choice]
        seg = self._segments[finding.segment_index]
        new_text = ltc.apply_replacement(seg.target or "", finding, replacement)
        if new_text is None:
            self.status.setText("That segment has changed since the check – run the check again.")
            return
        self._apply_target(finding.segment_index, new_text)
        # Later findings in the same segment move with the edit
        shift = len(replacement) - finding.length
        for other in self._findings:
            if other is not finding and other.segment_index == finding.segment_index \
                    and other.offset > finding.offset:
                other.offset += shift
        self._remove(row)
        self.status.setText(f"Applied “{html.escape(replacement)}” in segment "
                            f"{html.escape(str(getattr(seg, 'id', '')))}.")

    def _ignore_selected(self):
        row = self._selected()
        if row is not None:
            self._remove(row)

    def _remove(self, row):
        del self._findings[row]
        self.table.removeRow(row)
