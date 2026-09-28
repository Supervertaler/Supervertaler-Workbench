"""
Token Usage & Costs report dialog for Supervertaler Workbench.

Reads the JSONL usage ledger (modules.usage_log), totals it grouped by
project/client/model/etc. over a date range, and exports the detailed ledger
to CSV or Excel. Mirrors the Trados plugin's Usage & Costs report.

Costs are stored in USD; the dialog can show them in EUR at a user-set rate
(issue #8). The exported ledger stays in USD so it matches the Trados plugin's.
"""

import datetime

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QComboBox, QDialog, QDoubleSpinBox, QFileDialog, QHBoxLayout, QHeaderView,
    QLabel, QMessageBox, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from modules import usage_log
from modules.cost_estimate import CURRENCIES, DEFAULT_USD_TO_EUR, format_cost


class UsageReportDialog(QDialog):
    def __init__(self, parent=None, budget: float = 0.0, currency: str = "USD",
                 rate: float = DEFAULT_USD_TO_EUR, on_currency_changed=None):
        super().__init__(parent)
        self.setWindowTitle("Token Usage & Costs")
        self.resize(860, 540)
        self._records = []
        self._mtd = 0.0
        self._budget = float(budget or 0.0)
        self._on_currency_changed = on_currency_changed

        top = QHBoxLayout()
        top.addWidget(QLabel("Range:"))
        self.cmb_range = QComboBox()
        self.cmb_range.addItems(["This month", "Last 3 months", "This year", "All time"])
        self.cmb_range.currentIndexChanged.connect(self.reload)
        top.addWidget(self.cmb_range)

        top.addSpacing(12)
        top.addWidget(QLabel("Group by:"))
        self.cmb_group = QComboBox()
        self.cmb_group.addItems(usage_log.DIMENSIONS)
        self.cmb_group.currentIndexChanged.connect(self.rebind)
        top.addWidget(self.cmb_group)

        top.addSpacing(12)
        btn_refresh = QPushButton("Refresh")
        btn_refresh.clicked.connect(self.reload)
        top.addWidget(btn_refresh)
        btn_csv = QPushButton("Export CSV…")
        btn_csv.clicked.connect(lambda: self.export(xlsx=False))
        top.addWidget(btn_csv)
        btn_xlsx = QPushButton("Export Excel…")
        btn_xlsx.clicked.connect(lambda: self.export(xlsx=True))
        top.addWidget(btn_xlsx)
        top.addStretch(1)

        cur_row = QHBoxLayout()
        cur_row.addWidget(QLabel("Show costs in:"))
        self.cmb_currency = QComboBox()
        self.cmb_currency.addItems(list(CURRENCIES))
        self.cmb_currency.setCurrentText(currency if currency in CURRENCIES else "USD")
        cur_row.addWidget(self.cmb_currency)
        self.lbl_rate = QLabel("1 USD =")
        cur_row.addWidget(self.lbl_rate)
        self.spin_rate = QDoubleSpinBox()
        self.spin_rate.setDecimals(4)
        self.spin_rate.setRange(0.0001, 1000.0)
        self.spin_rate.setSingleStep(0.01)
        self.spin_rate.setSuffix(" EUR")
        self.spin_rate.setValue(float(rate or DEFAULT_USD_TO_EUR))
        self.spin_rate.setToolTip(
            "Exchange rate used to show costs in euros. Set it to your bank's or\n"
            "card's rate – it is not looked up online. Prices are USD in the price list.")
        cur_row.addWidget(self.spin_rate)
        cur_row.addStretch(1)
        self.cmb_currency.currentIndexChanged.connect(self._currency_changed)
        self.spin_rate.valueChanged.connect(self._currency_changed)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            ["Group", "Calls", "Input", "Output", "Cost (USD)", "% actual"])
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.verticalHeader().setVisible(False)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)

        self.lbl_totals = QLabel("")
        self.lbl_totals.setStyleSheet("font-weight: bold; padding: 4px;")

        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addLayout(cur_row)
        layout.addWidget(self.table)
        layout.addWidget(self.lbl_totals)

        self._sync_currency_widgets()
        self.reload()

    # ── Currency ─────────────────────────────────────────────────────────────
    def _currency(self):
        return self.cmb_currency.currentText() or "USD", float(self.spin_rate.value())

    def _money(self, usd: float, decimals: int = 2) -> str:
        currency, rate = self._currency()
        return format_cost(usd, currency, rate, decimals)

    def _sync_currency_widgets(self):
        is_eur = self.cmb_currency.currentText() == "EUR"
        self.lbl_rate.setVisible(is_eur)
        self.spin_rate.setVisible(is_eur)
        self.table.setHorizontalHeaderItem(
            4, QTableWidgetItem("Cost (EUR)" if is_eur else "Cost (USD)"))

    def _currency_changed(self, *_):
        self._sync_currency_widgets()
        self.rebind()
        if self._on_currency_changed:
            try:
                self._on_currency_changed(*self._currency())
            except Exception:
                pass

    def _range(self):
        now = datetime.datetime.now(datetime.timezone.utc)
        i = self.cmb_range.currentIndex()
        if i == 0:
            frm = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            to = now
        elif i == 1:
            frm = now - datetime.timedelta(days=90)
            to = now
        elif i == 2:
            frm = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
            to = now
        else:
            frm = datetime.datetime(2000, 1, 1, tzinfo=datetime.timezone.utc)
            to = datetime.datetime(2999, 1, 1, tzinfo=datetime.timezone.utc)
        return frm, to

    def reload(self):
        frm, to = self._range()
        try:
            self._records = usage_log.load(frm, to)
        except Exception:
            self._records = []
        try:
            self._mtd = usage_log.month_to_date_cost()
        except Exception:
            self._mtd = 0.0
        self.rebind()

    def rebind(self):
        dim = self.cmb_group.currentText() or "Project"
        rows = usage_log.group(self._records, dim)
        self.table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            cells = [
                row["group"],
                f"{row['calls']:,}",
                f"{row['input']:,}",
                f"{row['output']:,}",
                self._money(row['cost_usd'], 4),
                f"{row['actual_pct']}%",
            ]
            for c, val in enumerate(cells):
                item = QTableWidgetItem(str(val))
                if c > 0:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                self.table.setItem(r, c, item)

        t = usage_log.totals(self._records)
        if self._budget and self._budget > 0:
            pct = (self._mtd / self._budget * 100.0) if self._budget else 0.0
            month = (f"     |     This month: {self._money(self._mtd)} of "
                     f"{self._money(self._budget)} budget ({pct:.0f}%)")
        else:
            month = f"     |     This month: {self._money(self._mtd)}"
        self.lbl_totals.setText(
            f"Range total: {t['calls']:,} calls · {t['input']:,} in / {t['output']:,} out · "
            f"{self._money(t['cost_usd'])} · {t['actual_pct']}% from provider" + month)

    def export(self, xlsx: bool):
        try:
            default = "supervertaler-usage-" + datetime.date.today().strftime("%Y-%m-%d") + (".xlsx" if xlsx else ".csv")
            flt = "Excel workbook (*.xlsx)" if xlsx else "CSV file (*.csv)"
            path, _ = QFileDialog.getSaveFileName(self, "Export usage ledger", default, flt)
            if not path:
                return
            if xlsx:
                usage_log.export_xlsx(path, self._records)
            else:
                usage_log.export_csv(path, self._records)
            QMessageBox.information(
                self, "Export complete",
                f"Exported {len(self._records):,} record(s) to:\n{path}")
        except Exception as e:
            QMessageBox.warning(self, "Export", f"Export failed: {e}")
