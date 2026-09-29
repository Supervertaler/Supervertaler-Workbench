"""
Custom Web Resources
====================

User-defined lookup sites for the SuperLookup Web Resources sidebar (one of
the requests collected in issue #208). Each is a name plus a URL template using
the same placeholders as the built-in resources, e.g.
``https://www.dwds.de/?q={query}``. They are stored in the general settings and
appended below the built-in resources.
"""

import html
import uuid
from typing import Dict, List, Optional

from PyQt6.QtWidgets import (
    QAbstractItemView, QDialog, QDialogButtonBox, QHBoxLayout, QHeaderView, QLabel,
    QMessageBox, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout,
)

SETTINGS_KEY = "superlookup_custom_web_resources"

PLACEHOLDERS = [
    ("{query}", "the search term (required)"),
    ("{sl}", "source language code, e.g. en"),
    ("{tl}", "target language code, e.g. nl"),
    ("{sl_upper}", "source language code in capitals, e.g. EN"),
    ("{tl_upper}", "target language code in capitals, e.g. NL"),
    ("{sl_full}", "source language name in lower case, e.g. english"),
    ("{tl_full}", "target language name in lower case, e.g. dutch"),
]


def validate(name: str, url: str) -> Optional[str]:
    """What is wrong with this entry, or None if it can be used."""
    name, url = (name or "").strip(), (url or "").strip()
    if not name:
        return "Give the resource a name."
    if not url.lower().startswith(("https://", "http://")):
        return f"“{name}”: the address must start with https:// or http://."
    if "{query}" not in url:
        return f"“{name}”: the address must contain {{query}} where the search term goes."
    if any(ch.isspace() for ch in url):
        return f"“{name}”: the address must not contain spaces."
    return None


def normalise(entries) -> List[Dict]:
    """Stored entries → clean list of ``{'id', 'name', 'url'}``. Invalid or
    malformed entries are dropped; an id is assigned where missing, so an
    entry keeps its browser view (and log-in) across renames."""
    out, seen = [], set()
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or "").strip()
        url = str(entry.get("url") or "").strip()
        if validate(name, url):
            continue
        rid = str(entry.get("id") or "").strip() or uuid.uuid4().hex[:8]
        while rid in seen:
            rid = uuid.uuid4().hex[:8]
        seen.add(rid)
        out.append({"id": rid, "name": name, "url": url})
    return out


def to_resources(entries) -> List[Dict]:
    """Resource dicts in the shape SuperlookupTab.web_resources uses."""
    return [{
        "id": f"custom_{e['id']}",
        "name": e["name"],
        "icon": "🔗",
        "description": f"Custom resource: {e['url']}",
        "url_template": e["url"],
        "lang_format": "iso2",
        "bidirectional": False,
        "custom": True,
    } for e in normalise(entries)]


class CustomWebResourcesDialog(QDialog):
    """Add, edit and remove custom web resources."""

    def __init__(self, entries, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Custom Web Resources")
        self.resize(760, 420)
        self._ids = []

        intro = QLabel(
            "Add your own lookup sites to the Web Resources list. Search for something on "
            "the site, copy the address of the results page, and replace the search term "
            "in it with <b>{query}</b>.<br>"
            "For example: <code>https://www.dwds.de/?q={query}</code>")
        intro.setWordWrap(True)

        placeholders = QLabel("<br>".join(
            f"<code>{html.escape(p)}</code> – {html.escape(d)}" for p, d in PLACEHOLDERS))
        placeholders.setStyleSheet("color: #666;")

        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["Name", "Address (URL template)"])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.setColumnWidth(0, 180)
        for entry in normalise(entries):
            self._append(entry["name"], entry["url"], entry["id"])

        add_btn = QPushButton("➕ Add")
        add_btn.clicked.connect(self._add)
        remove_btn = QPushButton("🗑 Remove")
        remove_btn.clicked.connect(self._remove)
        up_btn = QPushButton("▲")
        up_btn.setToolTip("Move up")
        up_btn.clicked.connect(lambda: self._move(-1))
        down_btn = QPushButton("▼")
        down_btn.setToolTip("Move down")
        down_btn.clicked.connect(lambda: self._move(1))
        row = QHBoxLayout()
        for b in (add_btn, remove_btn, up_btn, down_btn):
            row.addWidget(b)
        row.addStretch(1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                   | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(intro)
        layout.addWidget(self.table, stretch=1)
        layout.addLayout(row)
        layout.addWidget(placeholders)
        layout.addWidget(buttons)

    def _append(self, name="", url="", rid=""):
        r = self.table.rowCount()
        self.table.insertRow(r)
        self.table.setItem(r, 0, QTableWidgetItem(name))
        self.table.setItem(r, 1, QTableWidgetItem(url))
        self._ids.append(rid)
        return r

    def _add(self):
        r = self._append("", "https://")
        self.table.setCurrentCell(r, 0)
        self.table.editItem(self.table.item(r, 0))

    def _remove(self):
        rows = sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True)
        for r in rows:
            self.table.removeRow(r)
            del self._ids[r]

    def _move(self, step):
        r = self.table.currentRow()
        t = r + step
        if r < 0 or not 0 <= t < self.table.rowCount():
            return
        for c in range(2):
            a, b = self.table.takeItem(r, c), self.table.takeItem(t, c)
            self.table.setItem(r, c, b)
            self.table.setItem(t, c, a)
        self._ids[r], self._ids[t] = self._ids[t], self._ids[r]
        self.table.setCurrentCell(t, self.table.currentColumn())

    def _rows(self):
        for r in range(self.table.rowCount()):
            name = (self.table.item(r, 0).text() if self.table.item(r, 0) else "").strip()
            url = (self.table.item(r, 1).text() if self.table.item(r, 1) else "").strip()
            yield r, name, url

    def entries(self) -> List[Dict]:
        """The rows as entries; blank rows are skipped."""
        return normalise([{"id": self._ids[r], "name": name, "url": url}
                          for r, name, url in self._rows() if name or url not in ("", "https://")])

    def _accept(self):
        for r, name, url in self._rows():
            if not name and url in ("", "https://"):
                continue  # an untouched new row
            problem = validate(name, url)
            if problem:
                self.table.setCurrentCell(r, 1 if name else 0)
                QMessageBox.warning(self, "Custom Web Resources", problem)
                return
        self.accept()
