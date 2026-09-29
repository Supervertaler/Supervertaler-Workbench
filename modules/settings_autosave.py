"""
Settings auto-save (issue #214)
===============================

Most Settings pages were built around an explicit "💾 Save …" button that
reads every control and writes them in one go; forgetting to click it lost the
change. The newer pages (AutoCorrect, Backup, Clipboard, Segmentation Rules)
save on every change instead, and this module gives the older pages the same
behaviour without rewriting them: the page's own Save routine is run for the
user, a moment after they change something, and the button is replaced by a
note saying changes are saved automatically.

Only user actions start a save – ``clicked`` rather than ``toggled``,
``activated`` rather than ``currentIndexChanged``, value changes only while the
control has the keyboard focus – so code that fills a page programmatically
(loading a project, resetting a dialog) never writes settings behind the
user's back. And a save only happens when a snapshot of the page's editable
values differs from the last one saved, so a button that merely *does*
something (Open folder, Export log, Change data folder…) never writes the page
as a side effect. Typing is debounced, and a pending save is written as soon as
the field loses focus, so switching to another item in a list first saves the
text that belonged to the previous one. The routines' "Settings saved" pop-up
is suppressed; a status-bar message takes its place.
"""

from typing import Callable, List, Optional

from PyQt6.QtCore import QEvent, QObject, Qt, QTimer
from PyQt6.QtWidgets import (
    QAbstractButton, QAbstractItemView, QAbstractSlider, QAbstractSpinBox, QApplication,
    QComboBox, QLabel, QLayout, QLineEdit, QMessageBox, QPlainTextEdit, QPushButton,
    QTextEdit, QWidget,
)

SAVE_BUTTON_PROPERTY = "settings_save_button"
NOTE_TEXT = "✓ Changes on this page are saved automatically."


class SettingsAutoSaver(QObject):
    def __init__(self, page: QWidget, save_button: QPushButton,
                 notify: Optional[Callable[[str], None]] = None,
                 delay_ms: int = 600, typing_delay_ms: int = 1200):
        super().__init__(page)
        self.page = page
        self.save_button = save_button
        self._notify = notify
        self._delay_ms = delay_ms
        self._typing_delay_ms = typing_delay_ms
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.save_now)
        self.saves = 0
        self._connect_controls()
        self._replace_button()
        self._saved_snapshot = self.snapshot()

    # ── public ───────────────────────────────────────────────────────────

    @property
    def pending(self) -> bool:
        return self._timer.isActive()

    def schedule(self, *args, delay_ms: Optional[int] = None):
        self._timer.start(self._delay_ms if delay_ms is None else delay_ms)

    def flush(self):
        if self._timer.isActive():
            self.save_now()

    def snapshot(self) -> list:
        """The page's editable values (and the look of its plain buttons,
        which is where colour pickers keep their colour)."""
        values = []
        for w in self.page.findChildren(QWidget):
            if w is self.save_button:
                continue
            if isinstance(w, QAbstractButton):
                values.append(w.isChecked() if w.isCheckable() else w.styleSheet())
            elif isinstance(w, QComboBox):
                values.append((w.currentIndex(), w.currentText()))
            elif isinstance(w, QAbstractSpinBox):
                values.append(w.text())
            elif isinstance(w, QAbstractSlider):
                values.append(w.value())
            elif isinstance(w, QLineEdit):
                if not w.isReadOnly() and not isinstance(w.parentWidget(), (QAbstractSpinBox, QComboBox)):
                    values.append(w.text())
            elif isinstance(w, (QTextEdit, QPlainTextEdit)):
                if not w.isReadOnly():
                    values.append(w.toPlainText())
            elif isinstance(w, QAbstractItemView) and w.model() is not None:
                model = w.model()
                values.append([(model.index(r, c).data(), model.index(r, c).data(Qt.ItemDataRole.CheckStateRole))
                               for r in range(model.rowCount()) for c in range(model.columnCount())])
        return values

    def save_now(self):
        self._timer.stop()
        if self.snapshot() == self._saved_snapshot:
            return
        original = QMessageBox.information
        QMessageBox.information = staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok)
        try:
            self.save_button.click()
        finally:
            QMessageBox.information = original
        self._saved_snapshot = self.snapshot()
        self.saves += 1
        if self._notify:
            self._notify("✓ Settings saved")

    # ── wiring ───────────────────────────────────────────────────────────

    def _connect_controls(self):
        for w in self.page.findChildren(QWidget):
            if w is self.save_button:
                continue
            if isinstance(w, QAbstractButton):
                # check boxes, radio buttons, and buttons that change a value
                # through a dialog (colour pickers, Reset, Swap …) – after
                # their own handler has run; the snapshot decides whether
                # anything actually changed
                w.clicked.connect(self.schedule)
            elif isinstance(w, QComboBox):
                w.activated.connect(self.schedule)
                if w.isEditable() and w.lineEdit() is not None:
                    self._watch_typing(w.lineEdit())
            elif isinstance(w, QAbstractSpinBox):
                w.valueChanged.connect(lambda *a, w=w: self._if_focused(w))
                w.editingFinished.connect(self.flush)
            elif isinstance(w, QAbstractSlider):
                w.sliderReleased.connect(self.schedule)
                w.valueChanged.connect(lambda *a, w=w: self._if_focused(w, w.isSliderDown()))
            elif isinstance(w, QLineEdit):
                if isinstance(w.parentWidget(), (QAbstractSpinBox, QComboBox)):
                    continue
                self._watch_typing(w)
            elif isinstance(w, (QTextEdit, QPlainTextEdit)):
                if w.isReadOnly():
                    continue
                w.textChanged.connect(lambda w=w: self._if_focused(w, typing=True))
                w.installEventFilter(self)

    def _watch_typing(self, edit: QLineEdit):
        edit.textEdited.connect(lambda *a: self.schedule(delay_ms=self._typing_delay_ms))
        edit.editingFinished.connect(self.flush)

    def _if_focused(self, w: QWidget, skip: bool = False, typing: bool = False):
        if skip or not w.hasFocus():
            return
        self.schedule(delay_ms=self._typing_delay_ms if typing else None)

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.FocusOut:
            self.flush()
        return False

    def _replace_button(self):
        note = QLabel(NOTE_TEXT)
        note.setStyleSheet("color: #666; font-size: 9pt; padding: 4px;")
        note.setToolTip("There is no Save button: each change is written a moment after you make it.")
        parent = self.save_button.parentWidget()
        layout = _layout_containing(parent.layout(), self.save_button) if parent else None
        if layout is not None:
            layout.insertWidget(layout.indexOf(self.save_button), note)
        self.note = note
        self.save_button.hide()


def _layout_containing(layout: Optional[QLayout], widget: QWidget) -> Optional[QLayout]:
    if layout is None:
        return None
    if layout.indexOf(widget) >= 0:
        return layout
    for i in range(layout.count()):
        child = layout.itemAt(i).layout()
        found = _layout_containing(child, widget)
        if found is not None:
            return found
    return None


def install(pages: List[QWidget], notify: Optional[Callable[[str], None]] = None) -> List[SettingsAutoSaver]:
    """One auto-saver per page whose Save button carries the
    ``settings_save_button`` property."""
    savers = []
    for page in pages:
        for button in page.findChildren(QPushButton):
            if button.property(SAVE_BUTTON_PROPERTY):
                savers.append(SettingsAutoSaver(page, button, notify))
    app = QApplication.instance()
    if app is not None:
        # typing and then quitting straight away still saves
        app.aboutToQuit.connect(lambda: [saver.flush() for saver in savers])
    return savers
