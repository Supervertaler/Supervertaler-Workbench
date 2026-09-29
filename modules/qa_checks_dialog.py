"""
QA Checks dialog (issue #209): pick a set of QA checks, run it read-only over
the project, and work through the findings – double-click one to go to its
segment. The dialog stays open while you fix things; Run again refreshes it.
"""

import csv
import html

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QAbstractItemView, QComboBox, QDialog, QFileDialog, QHBoxLayout, QHeaderView, QLabel,
    QMessageBox, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from modules import qa_checks


class QAChecksDialog(QDialog):
    COLUMNS = ["Segment", "Check", "In", "Found", "Context"]

    def __init__(self, parent, sets_dir, get_segments, navigate, on_sets_changed=None):
        """``get_segments()`` returns the project's segments; ``navigate(id)``
        selects a segment in the grid; ``on_sets_changed()`` is called after the
        dialog writes a set (so an open F&R Sets list can reload)."""
        super().__init__(parent)
        self.setWindowTitle("QA Checks")
        self.resize(900, 520)
        self._sets_dir = sets_dir
        self._get_segments = get_segments
        self._navigate = navigate
        self._on_sets_changed = on_sets_changed
        self._findings = []

        intro = QLabel(
            "QA checks are Find &amp; Replace operations marked <b>QA</b> in an F&amp;R Set: "
            "they only find, never replace. Every match is listed below – double-click "
            "one to go to its segment. Nothing in the project is changed.")
        intro.setWordWrap(True)

        top = QHBoxLayout()
        top.addWidget(QLabel("Check set:"))
        self.set_combo = QComboBox()
        self.set_combo.setMinimumWidth(260)
        top.addWidget(self.set_combo)
        self.run_btn = QPushButton("▶ Run")
        self.run_btn.setDefault(True)
        self.run_btn.clicked.connect(self.run)
        top.addWidget(self.run_btn)
        self.basic_btn = QPushButton("➕ Add basic checks")
        self.basic_btn.setToolTip(
            f"Create the set “{qa_checks.BASIC_SET_NAME}” with common checks (double spaces, "
            "doubled words, space before punctuation…). Edit it in Find & Replace → F&R Sets.")
        self.basic_btn.clicked.connect(self._add_basic_set)
        top.addWidget(self.basic_btn)
        top.addStretch(1)
        self.export_btn = QPushButton("💾 Export…")
        self.export_btn.setToolTip("Save the findings as a CSV file")
        self.export_btn.clicked.connect(self._export)
        top.addWidget(self.export_btn)

        self.table = QTableWidget(0, len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels(self.COLUMNS)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSortingEnabled(True)
        self.table.sortByColumn(0, Qt.SortOrder.AscendingOrder)
        header = self.table.horizontalHeader()
        for c in range(4):
            header.setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self.table.cellDoubleClicked.connect(self._go_to)

        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.RichText)

        layout = QVBoxLayout(self)
        layout.addWidget(intro)
        layout.addLayout(top)
        layout.addWidget(self.table, stretch=1)
        layout.addWidget(self.status)

        self._load_sets()

    # ── Sets ────────────────────────────────────────────────────────────────
    def refresh_sets(self):
        """Re-read the sets (they may have been edited in Find & Replace),
        keeping the selected one."""
        i = self.set_combo.currentIndex()
        current = self._sets[i].name if 0 <= i < len(getattr(self, '_sets', [])) else None
        self._load_sets(select=current)

    def _load_sets(self, select=None):
        self.set_combo.clear()
        self._sets = [s for s in qa_checks.load_sets(self._sets_dir) if qa_checks.checks_in(s)]
        for s in self._sets:
            n = len(qa_checks.checks_in(s))
            self.set_combo.addItem(f"{s.name} ({n} check{'s' if n != 1 else ''})")
        has = bool(self._sets)
        self.set_combo.setEnabled(has)
        self.run_btn.setEnabled(has)
        names = [s.name for s in self._sets]
        if select in names:
            self.set_combo.setCurrentIndex(names.index(select))
        if not has:
            self.status.setText(
                "No QA checks yet. Click <b>Add basic checks</b> for a starter set, or tick "
                "<b>QA</b> for operations in Find &amp; Replace → F&amp;R Sets.")
        self.basic_btn.setVisible(qa_checks.BASIC_SET_NAME not in
                                  [s.name for s in qa_checks.load_sets(self._sets_dir)])

    def _add_basic_set(self):
        qa_checks.save_set(self._sets_dir, qa_checks.basic_qa_set())
        if self._on_sets_changed:
            self._on_sets_changed()
        self._load_sets(select=qa_checks.BASIC_SET_NAME)
        self.status.setText(
            f"Created “{html.escape(qa_checks.BASIC_SET_NAME)}”. Some checks in it are off by "
            "default; switch them on in Find &amp; Replace → F&amp;R Sets.")

    # ── Running ─────────────────────────────────────────────────────────────
    def run(self):
        i = self.set_combo.currentIndex()
        if not (0 <= i < len(self._sets)):
            return
        segments = self._get_segments() or []
        if not segments:
            QMessageBox.information(self, "QA Checks", "Open a project first.")
            return
        fr_set = self._sets[i]
        findings, problems = qa_checks.run_checks(segments, qa_checks.checks_in(fr_set))
        self._findings = findings
        self._fill(findings)
        n_segments = len({f.segment_id for f in findings})
        text = (f"{len(findings)} finding{'s' if len(findings) != 1 else ''} in "
                f"{n_segments} segment{'s' if n_segments != 1 else ''} "
                f"({len(segments)} checked with “{html.escape(fr_set.name)}”).")
        if problems:
            text += "<br><span style='color:#c62828;'>" + "<br>".join(
                html.escape(p) for p in problems) + "</span>"
        self.status.setText(text)

    def _fill(self, findings):
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(findings))
        for r, f in enumerate(findings):
            seg_item = QTableWidgetItem()
            seg_item.setData(Qt.ItemDataRole.DisplayRole, f.segment_id)
            seg_item.setData(Qt.ItemDataRole.UserRole, f.segment_id)
            cells = [seg_item, QTableWidgetItem(f.check),
                     QTableWidgetItem("Source" if f.side == "source" else "Target"),
                     QTableWidgetItem(f.text.replace(' ', '·')), QTableWidgetItem(f.context)]
            for c, item in enumerate(cells):
                if c:
                    item.setData(Qt.ItemDataRole.UserRole, f.segment_id)
                self.table.setItem(r, c, item)
        self.table.setSortingEnabled(True)

    def _go_to(self, row, _col):
        item = self.table.item(row, 0)
        if item is not None and self._navigate:
            self._navigate(item.data(Qt.ItemDataRole.UserRole))

    def _export(self):
        if not self._findings:
            QMessageBox.information(self, "QA Checks", "Run the checks first.")
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export QA findings", "qa-findings.csv",
                                              "CSV file (*.csv)")
        if not path:
            return
        with open(path, 'w', encoding='utf-8-sig', newline='') as f:
            w = csv.writer(f)
            w.writerow(self.COLUMNS)
            for fd in self._findings:
                w.writerow([fd.segment_id, fd.check, fd.side, fd.text, fd.context])
        self.status.setText(f"Exported {len(self._findings)} finding(s) to {html.escape(path)}")
