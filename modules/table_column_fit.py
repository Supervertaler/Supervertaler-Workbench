"""Give a table's main text columns the spare width, never less than a minimum.

QHeaderView's Stretch mode shares out whatever the other columns leave over and
never scrolls sideways, so when the other columns are wide the stretched ones
shrink to almost nothing: the Termbases tab's English and Dutch columns were
about 40 px wide, the term text cut down to "a ...". FillColumns keeps the named
columns Interactive and sizes them itself: they take the spare width when there
is some, keep ``minimum`` when there isn't, and the table scrolls sideways
instead.
"""

from PyQt6.QtCore import QEvent, QObject
from PyQt6.QtWidgets import QHeaderView, QTableView


class FillColumns(QObject):
    """Size ``columns`` of ``table`` to share its spare width, each at least
    ``minimum`` px. Refits when the table is resized or another column is
    resized by hand. Owned by the table, so it lives as long as the table does."""

    def __init__(self, table: QTableView, columns, minimum: int):
        super().__init__(table)
        self._table = table
        self._columns = tuple(columns)
        self._minimum = minimum
        header = table.horizontalHeader()
        for column in self._columns:
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Interactive)
        table.viewport().installEventFilter(self)
        header.sectionResized.connect(self._on_section_resized)
        self.fit()

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.Resize:
            self.fit()
        return False

    def _on_section_resized(self, index, _old_size, _new_size):
        # Our own resizes come back through here too: ignore those columns,
        # or each fit would trigger the next.
        if index not in self._columns:
            self.fit()

    def fit(self):
        header = self._table.horizontalHeader()
        others = sum(header.sectionSize(c) for c in range(header.count())
                     if c not in self._columns and not header.isSectionHidden(c))
        spare = self._table.viewport().width() - others
        width = max(self._minimum, spare // len(self._columns))
        for column in self._columns:
            if header.sectionSize(column) != width:
                header.resizeSection(column, width)
