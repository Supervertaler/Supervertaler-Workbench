"""
Settings → Segmentation Rules (issue #191)
==========================================

Edits the user's segmentation rules (``modules.segmentation_rules``): the line
break and built-in-rule switches, extra abbreviations, and an ordered table of
SRX-style break / exception rules that can be imported from and exported to
SRX files (OmegaT, Okapi …). A test box shows the result live.

Like the other newer settings tabs there is no Save button – every change is
written at once, because the rules are only read when text is next segmented.
"""

import re
from typing import Callable, List

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QAbstractItemView, QComboBox, QFileDialog, QGroupBox, QHBoxLayout, QHeaderView,
    QInputDialog, QLabel, QLineEdit, QListWidget, QMessageBox, QPlainTextEdit,
    QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from modules.segmentation_rules import (Rule, SegmentationRules, build_srx,
                                        parse_abbreviations, parse_srx, rule_error)
from modules.simple_segmenter import SimpleSegmenter
from modules.styled_widgets import CheckmarkCheckBox

SAMPLE_TEXT = ("Mr. Smith went to Dr. Jones. The meeting was short! Was it useful? "
               "Nobody knows.\nOne part<>another part<>a third part")

COL_ON, COL_TYPE, COL_BEFORE, COL_AFTER, COL_COMMENT = range(5)
_TYPES = [("Break", True), ("No break (exception)", False)]
_ERROR_BACKGROUND = QColor(220, 60, 60, 90)


class SegmentationRulesWidget(QWidget):
    def __init__(self, load: Callable[[], SegmentationRules],
                 save: Callable[[SegmentationRules], None], parent=None):
        super().__init__(parent)
        self._save = save
        self._loading = True

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(15)

        info = QLabel(
            "These rules decide where Supervertaler splits text into segments when it does "
            "the splitting itself: plain-text and Markdown imports with “Split lines into "
            "sentences” ticked, text pasted into New Project, and Add Segments. DOCX and the "
            "other document formats are split by the Okapi engine with its own rules and are "
            "not affected.\n\nChanges apply immediately – there's no Save button on this tab. "
            "Text that is already segmented stays as it is.")
        info.setWordWrap(True)
        info.setStyleSheet("color: #666; font-size: 9pt; padding: 5px;")
        layout.addWidget(info)

        # ── Options ──────────────────────────────────────────────────────
        options = QGroupBox("Options")
        olayout = QVBoxLayout(options)
        self.line_breaks_cb = CheckmarkCheckBox("Start a new segment at every line break")
        self.line_breaks_cb.setToolTip(
            "Each line of pasted text becomes at least one segment.\n"
            "When this is off, line breaks inside pasted text count as spaces.\n"
            "(Plain-text imports always keep one line per paragraph.)")
        olayout.addWidget(self.line_breaks_cb)
        self.builtin_cb = CheckmarkCheckBox("Use the built-in sentence rules")
        self.builtin_cb.setToolTip(
            "Split after . ! or ? followed by a space and a capital letter or opening quote,\n"
            "keeping common abbreviations (Mr., Dr., e.g., etc.) with the sentence.\n"
            "Untick to let only your own rules below decide.")
        olayout.addWidget(self.builtin_cb)
        olayout.addWidget(QLabel("Extra abbreviations – a full stop after these never ends a segment:"))
        self.abbreviations_edit = QLineEdit()
        self.abbreviations_edit.setPlaceholderText("e.g. np, itd, tzn, m.in, bzw, usw")
        self.abbreviations_edit.setToolTip("Separate with commas or spaces. Case does not matter.")
        olayout.addWidget(self.abbreviations_edit)
        layout.addWidget(options)

        # ── Custom rules ─────────────────────────────────────────────────
        rules_group = QGroupBox("Custom rules")
        rlayout = QVBoxLayout(rules_group)
        hint = QLabel(
            "Checked from top to bottom, before the built-in rules – at each position the first "
            "rule that matches decides. Patterns are regular expressions, as in SRX and OmegaT: "
            "“Before” must match the text just before the break, “After” the text just after it; "
            "either may be empty. Example: a break rule with Before <code>&lt;&gt;</code> splits "
            "after every &lt;&gt;; an exception with Before <code>\\bnp\\.</code> and After "
            "<code>\\s</code> keeps “np.” in its sentence.")
        hint.setWordWrap(True)
        hint.setTextFormat(Qt.TextFormat.RichText)
        hint.setStyleSheet("color: #666; font-size: 9pt;")
        rlayout.addWidget(hint)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["On", "Type", "Before the break", "After the break", "Comment"])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setMinimumHeight(180)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(COL_ON, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(COL_TYPE, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(COL_BEFORE, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(COL_AFTER, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(COL_COMMENT, QHeaderView.ResizeMode.Stretch)
        self.table.itemChanged.connect(self._on_table_changed)
        rlayout.addWidget(self.table)

        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        rlayout.addWidget(self.status_label)

        row1 = QHBoxLayout()
        for text, tip, slot in (
                ("➕ Break rule", "Add a rule that splits where its patterns match", lambda: self._add_rule(True)),
                ("➕ Exception", "Add a rule that prevents a split where its patterns match", lambda: self._add_rule(False)),
                ("➕ Break after text…", "Split after every occurrence of a delimiter such as <> or |", self._add_delimiter_rule)):
            btn = QPushButton(text)
            btn.setToolTip(tip)
            btn.clicked.connect(slot)
            row1.addWidget(btn)
        row1.addStretch()
        self.up_btn = QPushButton("▲")
        self.up_btn.setToolTip("Move the selected rule up (earlier rules win)")
        self.up_btn.clicked.connect(lambda: self._move(-1))
        self.down_btn = QPushButton("▼")
        self.down_btn.setToolTip("Move the selected rule down")
        self.down_btn.clicked.connect(lambda: self._move(1))
        self.remove_btn = QPushButton("Remove")
        self.remove_btn.clicked.connect(self._remove)
        for btn in (self.up_btn, self.down_btn, self.remove_btn):
            row1.addWidget(btn)
        rlayout.addLayout(row1)

        row2 = QHBoxLayout()
        import_btn = QPushButton("📥 Import SRX…")
        import_btn.setToolTip("Add rules from an SRX file, e.g. OmegaT's segmentation.srx")
        import_btn.clicked.connect(self._import_srx)
        export_btn = QPushButton("📤 Export SRX…")
        export_btn.setToolTip("Save the custom rules as an SRX file for other tools")
        export_btn.clicked.connect(self._export_srx)
        row2.addWidget(import_btn)
        row2.addWidget(export_btn)
        row2.addStretch()
        rlayout.addLayout(row2)
        layout.addWidget(rules_group)

        # ── Test ─────────────────────────────────────────────────────────
        test_group = QGroupBox("Test")
        tlayout = QVBoxLayout(test_group)
        tlayout.addWidget(QLabel("Type or paste text – the segments below update as you change the rules:"))
        self.test_input = QPlainTextEdit(SAMPLE_TEXT)
        self.test_input.setMaximumHeight(90)
        tlayout.addWidget(self.test_input)
        self.test_output = QListWidget()
        self.test_output.setMaximumHeight(160)
        tlayout.addWidget(self.test_output)
        layout.addWidget(test_group)
        layout.addStretch()

        try:
            current = load()
        except Exception:
            current = SegmentationRules()
        self.line_breaks_cb.setChecked(current.split_at_line_breaks)
        self.builtin_cb.setChecked(current.use_builtin_rules)
        self.abbreviations_edit.setText(", ".join(current.extra_abbreviations))
        for rule in current.rules:
            self._append_row(rule)
        self._loading = False

        self.line_breaks_cb.toggled.connect(self._changed)
        self.builtin_cb.toggled.connect(self._changed)
        self.abbreviations_edit.textChanged.connect(self._changed)
        self.test_input.textChanged.connect(self._refresh_test)
        self.table.itemSelectionChanged.connect(self._update_buttons)
        self._validate()
        self._refresh_test()
        self._update_buttons()

    # ── model ↔ table ────────────────────────────────────────────────────

    def rules(self) -> SegmentationRules:
        return SegmentationRules(
            split_at_line_breaks=self.line_breaks_cb.isChecked(),
            use_builtin_rules=self.builtin_cb.isChecked(),
            extra_abbreviations=parse_abbreviations(self.abbreviations_edit.text()),
            rules=[self._row_rule(r) for r in range(self.table.rowCount())])

    def _row_rule(self, row: int) -> Rule:
        def text(col):
            item = self.table.item(row, col)
            return item.text() if item else ""
        on = self.table.item(row, COL_ON)
        combo = self.table.cellWidget(row, COL_TYPE)
        return Rule(is_break=bool(combo.currentData()) if combo else True,
                    before=text(COL_BEFORE), after=text(COL_AFTER),
                    enabled=on is None or on.checkState() == Qt.CheckState.Checked,
                    comment=text(COL_COMMENT))

    def _append_row(self, rule: Rule, row: int = None):
        was_loading, self._loading = self._loading, True
        row = self.table.rowCount() if row is None else row
        self.table.insertRow(row)
        on = QTableWidgetItem()
        on.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsSelectable)
        on.setCheckState(Qt.CheckState.Checked if rule.enabled else Qt.CheckState.Unchecked)
        self.table.setItem(row, COL_ON, on)
        combo = QComboBox()
        for label, value in _TYPES:
            combo.addItem(label, value)
        combo.setCurrentIndex(0 if rule.is_break else 1)
        combo.currentIndexChanged.connect(self._changed)
        self.table.setCellWidget(row, COL_TYPE, combo)
        for col, value in ((COL_BEFORE, rule.before), (COL_AFTER, rule.after), (COL_COMMENT, rule.comment)):
            self.table.setItem(row, col, QTableWidgetItem(value))
        self._loading = was_loading

    def _set_rules(self, rules: List[Rule]):
        self.table.setRowCount(0)
        for rule in rules:
            self._append_row(rule)
        self._changed()

    # ── actions ──────────────────────────────────────────────────────────

    def _add_rule(self, is_break: bool, before: str = "", comment: str = ""):
        self._append_row(Rule(is_break=is_break, before=before, comment=comment))
        row = self.table.rowCount() - 1
        self.table.selectRow(row)
        self._changed()
        if not before:
            self.table.editItem(self.table.item(row, COL_BEFORE))

    def _add_delimiter_rule(self):
        text, ok = QInputDialog.getText(self, "Break after text",
                                        "Start a new segment after every occurrence of:")
        if ok and text:
            self._add_rule(True, re.escape(text), f"Break after {text}")

    def _remove(self):
        row = self.table.currentRow()
        if row >= 0:
            self.table.removeRow(row)
            self._changed()

    def _move(self, step: int):
        row = self.table.currentRow()
        target = row + step
        if row < 0 or not 0 <= target < self.table.rowCount():
            return
        rule = self._row_rule(row)
        self.table.removeRow(row)
        self._append_row(rule, target)
        self.table.selectRow(target)
        self._changed()

    def _update_buttons(self):
        row = self.table.currentRow()
        has = 0 <= row < self.table.rowCount()
        self.remove_btn.setEnabled(has)
        self.up_btn.setEnabled(has and row > 0)
        self.down_btn.setEnabled(has and row < self.table.rowCount() - 1)

    def _import_srx(self):
        path, _ = QFileDialog.getOpenFileName(self, "Import SRX rules", "",
                                              "SRX files (*.srx);;XML files (*.xml);;All files (*)")
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8-sig") as f:
                groups = parse_srx(f.read())
        except Exception as e:
            QMessageBox.warning(self, "Import SRX", f"Could not read this SRX file:\n\n{e}")
            return
        groups = {name: rules for name, rules in groups.items() if rules}
        if not groups:
            QMessageBox.information(self, "Import SRX", "The file contains no segmentation rules.")
            return
        names = list(groups)
        if len(names) > 1:
            labels = [f"{name} ({len(groups[name])} rules)" for name in names]
            label, ok = QInputDialog.getItem(
                self, "Import SRX",
                "This file has rules for several languages. Import which set?\n"
                "(Most files end with a general set, often called “Default”.)",
                labels, len(labels) - 1, False)
            if not ok:
                return
            name = names[labels.index(label)]
        else:
            name = names[0]
        imported = groups[name]

        existing = [self._row_rule(r) for r in range(self.table.rowCount())]
        if existing:
            box = QMessageBox(self)
            box.setWindowTitle("Import SRX")
            box.setText(f"Add the {len(imported)} imported rules after your {len(existing)} "
                        "existing rules, or replace them?")
            add_btn = box.addButton("Add", QMessageBox.ButtonRole.AcceptRole)
            replace_btn = box.addButton("Replace", QMessageBox.ButtonRole.DestructiveRole)
            box.addButton(QMessageBox.StandardButton.Cancel)
            box.exec()
            if box.clickedButton() is replace_btn:
                existing = []
            elif box.clickedButton() is not add_btn:
                return
        self._set_rules(existing + imported)
        invalid = sum(1 for rule in imported if rule_error(rule))
        message = f"Imported {len(imported)} rules from “{name}”."
        if invalid:
            message += f"\n\n{invalid} of them use a pattern Supervertaler cannot read and are ignored (marked in red)."
        if self.builtin_cb.isChecked():
            message += ("\n\nTip: untick “Use the built-in sentence rules” to let the imported "
                        "rules decide on their own.")
        QMessageBox.information(self, "Import SRX", message)

    def _export_srx(self):
        rules = [self._row_rule(r) for r in range(self.table.rowCount())]
        usable = [r for r in rules if r.enabled and not rule_error(r)]
        if not usable:
            QMessageBox.information(self, "Export SRX", "There are no usable custom rules to export.")
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export SRX rules", "segmentation.srx",
                                              "SRX files (*.srx);;All files (*)")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(build_srx(usable))
        except OSError as e:
            QMessageBox.warning(self, "Export SRX", f"Could not save the file:\n\n{e}")
            return
        QMessageBox.information(self, "Export SRX", f"Exported {len(usable)} rules.")

    # ── change handling ──────────────────────────────────────────────────

    def _on_table_changed(self, item):
        if not self._loading:
            self._changed()

    def _changed(self, *args):
        if self._loading:
            return
        self._validate()
        self._refresh_test()
        self._update_buttons()
        self._save(self.rules())

    def _validate(self):
        was_loading, self._loading = self._loading, True
        problems = 0
        for row in range(self.table.rowCount()):
            rule = self._row_rule(row)
            error = rule_error(rule) if rule.enabled else None
            problems += bool(error)
            for col in (COL_ON, COL_BEFORE, COL_AFTER, COL_COMMENT):
                item = self.table.item(row, col)
                if item is None:
                    continue
                item.setData(Qt.ItemDataRole.BackgroundRole, _ERROR_BACKGROUND if error else None)
                item.setToolTip(f"⚠ {error}" if error else "")
        self._loading = was_loading
        self.status_label.setText(
            f"⚠ {problems} rule{'s' if problems != 1 else ''} with an unusable pattern "
            "(marked in red) will be ignored – hover over it to see why." if problems else "")

    def _refresh_test(self):
        self.test_output.clear()
        try:
            segments = SimpleSegmenter(self.rules()).segment_text(self.test_input.toPlainText())
        except Exception as e:
            self.test_output.addItem(f"⚠ {e}")
            return
        for n, segment in enumerate(segments, 1):
            self.test_output.addItem(f"{n}. {segment}")
