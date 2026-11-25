"""The grid scrolls by pixels, not by whole rows (Nov 2026).

Qt's default for a table is to scroll a row at a time. In the grid that made
the wheel lurch (one notch was three rows, whatever their height) and a
segment taller than the window could not be scrolled through: the view
jumped from its top straight to the next row. The grid now scrolls by
pixels. These tests run the real create_translation_grid on a stand-in
window, and check that going to a segment still centres it.
"""

from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtWidgets import QAbstractItemView, QApplication, QTableWidgetItem


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def grid(app):
    """The real grid, 40 rows: row 5 is taller than the window."""
    from modules.main_window.grid_navigation import GridNavigationMixin
    from modules.main_window.grid_view import GridViewMixin

    def nothing(self, *args):
        pass

    class Window(GridViewMixin, GridNavigationMixin):
        # what the grid's signals are connected to: not under test here
        on_cell_changed = on_cell_selected = on_cell_clicked = nothing
        on_cell_double_clicked = on_selection_changed = nothing
        show_grid_context_menu = _on_column_resized = _update_row_selection_tint = nothing

    win = Window()
    win.log = lambda *a, **k: None
    win.current_project = None
    win.default_font_family, win.default_font_size = "Segoe UI", 11
    win.allow_replace_in_source = False
    win.load_general_settings = lambda: {}
    win.create_translation_grid()
    table = win.table
    table.resize(900, 400)
    table.setRowCount(40)
    for row in range(40):
        table.setItem(row, 0, QTableWidgetItem(str(row + 1)))
        table.setRowHeight(row, 1200 if row == 5 else 30)
    table.show()
    QApplication.processEvents()
    yield win
    table.close()
    table.deleteLater()


def test_the_grid_scrolls_by_pixels(grid):
    table = grid.table
    assert table.verticalScrollMode() == QAbstractItemView.ScrollMode.ScrollPerPixel
    bar = table.verticalScrollBar()
    assert bar.singleStep() == 40                    # a wheel notch is three of these
    # the scrollbar counts pixels: everything below the window, not 40 rows
    assert bar.maximum() == 39 * 30 + 1200 - table.viewport().height()


def test_the_step_survives_a_resize(grid):
    # Qt sets its own step when the view's size changes, unless one was set.
    table = grid.table
    table.resize(900, 700)
    QApplication.processEvents()
    assert table.verticalScrollBar().singleStep() == 40


def test_a_segment_taller_than_the_window_can_be_scrolled_through(grid):
    table = grid.table
    bar = table.verticalScrollBar()
    bar.setValue(5 * 30 + 500)                       # 500 px into row 5
    QApplication.processEvents()
    assert table.rowAt(0) == 5
    assert table.rowViewportPosition(5) == -500      # its top is above the window
    assert table.rowAt(table.viewport().height() - 1) == 5   # and it fills the window


def test_one_step_moves_the_same_distance_whatever_the_row_height(grid):
    table = grid.table
    bar = table.verticalScrollBar()
    bar.setValue(0)
    bar.triggerAction(bar.SliderAction.SliderSingleStepAdd)
    assert bar.value() == 40                         # among the short rows
    bar.setValue(5 * 30 + 200)
    bar.triggerAction(bar.SliderAction.SliderSingleStepAdd)
    assert bar.value() == 5 * 30 + 240               # and inside the tall one


def test_going_to_a_segment_still_centres_it(grid):
    table = grid.table
    grid._center_row_in_viewport(25)
    QApplication.processEvents()
    middle = table.rowViewportPosition(25) + 15
    assert abs(middle - table.viewport().height() / 2) <= 1


def test_centring_a_row_that_is_not_there_changes_nothing(grid):
    table = grid.table
    table.verticalScrollBar().setValue(300)
    grid._center_row_in_viewport(-1)
    grid._center_row_in_viewport(40)
    assert table.verticalScrollBar().value() == 300


def test_the_first_and_last_rows_can_be_reached(grid):
    table = grid.table
    bar = table.verticalScrollBar()
    bar.setValue(bar.maximum())
    QApplication.processEvents()
    bottom = table.rowViewportPosition(39) + table.rowHeight(39)
    assert bottom == table.viewport().height()       # last row ends at the window's edge
    table.scrollToItem(table.item(0, 0))
    assert bar.value() == 0
