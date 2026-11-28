"""A slow move between segments says where its time went (Nov 2026).

On a real 242-segment project every move took 235 to 330 ms, and the log
named only the TermLens update: the other two thirds sat in steps under the
50 ms that a named block needs before it is logged. Every stretch of
_on_cell_selected_full is now timed, and a move over 100 ms logs one extra
line with the steps of 1 ms or more. A fast move logs nothing, as before.
"""

from __future__ import annotations

import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from modules.main_window.grid_navigation import GridNavigationMixin, format_move_steps


def test_steps_are_named_slowest_first_and_add_up_to_the_total():
    line = format_move_steps([("toolbar", 3.0), ("TermLens", 70.94), ("preview scroll", 41.2)], 120.0)
    assert line == "TermLens 70.9, preview scroll 41.2, toolbar 3.0, rest 4.9"


def test_steps_under_a_millisecond_go_into_the_rest():
    assert format_move_steps([("toolbar", 0.4), ("TermLens", 100.0), ("find segment", 0.9)], 101.3) == "TermLens 100.0, rest 1.3"


def test_a_label_that_ran_twice_is_added_up():
    assert format_move_steps([("TermLens", 30.0), ("TermLens", 25.0)], 55.0) == "TermLens 55.0, rest 0.0"


def test_no_steps_at_all_is_all_rest_and_never_negative():
    assert format_move_steps([], 12.0) == "rest 12.0"
    assert format_move_steps([("TermLens", 50.0)], 49.99) == "TermLens 50.0, rest 0.0"


class Table:
    """Just enough of the grid for the first stretch of a move."""

    def __init__(self, delay=0.0, fail=False):
        self.delay, self.fail = delay, fail

    def rowCount(self):
        return 5

    def cellWidget(self, row, column):
        time.sleep(self.delay)
        return None

    def item(self, row, column):
        if self.fail:
            raise RuntimeError("the grid is gone")
        return None


def move(table):
    class Window(GridNavigationMixin):
        pass

    win = Window()
    win.lines = []
    win.log = win.lines.append
    win.debug_mode_enabled = False
    win.current_project = None
    win.table = table
    win._on_cell_selected_full(2, 3, 1, 3)
    return win.lines


def test_a_fast_move_logs_nothing():
    assert move(Table()) == []


def test_a_slow_move_logs_its_total_and_then_its_steps():
    lines = move(Table(delay=0.08))  # two cells of the old row: 160 ms
    assert len(lines) == 2
    assert lines[0].startswith("⏱️ [cell-select] TOTAL: ")
    assert lines[1].startswith("⏱️ [cell-select] steps: clear old selection 1")
    assert ", rest " in lines[1]


def test_a_move_that_fails_part_way_still_logs_its_steps():
    lines = move(Table(delay=0.08, fail=True))
    assert lines[0] == "Critical error in on_cell_selected: the grid is gone"
    assert lines[1].startswith("⏱️ [cell-select] TOTAL: ")
    assert lines[2].startswith("⏱️ [cell-select] steps: clear old selection 1")
