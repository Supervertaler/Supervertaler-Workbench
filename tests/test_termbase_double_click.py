"""Termbases tab: double-clicking a termbase opens its terms (Nov 2026).

Before, only the right-click menu's Edit Terms… did. Builds the real
Termbases tab on the stand-in window of test_add_term_synonym_prompt.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt

from test_add_term_synonym_prompt import app, tab  # noqa: F401  (fixtures)


def double_click(w, name, column):
    table = w.termbase_table
    row = next(r for r in range(table.rowCount()) if table.item(r, 1).text() == name)
    table.cellDoubleClicked.emit(row, column)


def shown(w):
    return w.termbase_terms_window.windowTitle()


def with_two_termbases(w):
    other = w.mgr.create_termbase("OTHER", source_lang="en", target_lang="fr")
    w.mgr.add_term(other, "valve", "soupape")
    w.termbase_tab_refresh_callback()
    return other


def test_double_clicking_a_termbases_name_opens_its_terms(tab):
    with_two_termbases(tab)
    double_click(tab, "OTHER", 1)
    assert shown(tab) == "Terms – OTHER"
    double_click(tab, "BEIJER", 1)
    assert shown(tab) == "Terms – BEIJER"


def test_any_text_column_works_and_sorting_does_not_mix_up_rows(tab):
    with_two_termbases(tab)
    table = tab.termbase_table
    table.sortItems(1, Qt.SortOrder.DescendingOrder)          # OTHER first now
    for column in (0, 2, 3):                                   # Type, Languages, Terms
        double_click(tab, "OTHER", column)
        assert shown(tab) == "Terms – OTHER"
        double_click(tab, "BEIJER", column)
        assert shown(tab) == "Terms – BEIJER"


def mouse_double_click(w, name, column):
    from PyQt6.QtTest import QTest
    table = w.termbase_table
    row = next(r for r in range(table.rowCount()) if table.item(r, 1).text() == name)
    target = table.cellWidget(row, column) or table.viewport()
    if target is table.viewport():
        pos = table.visualRect(table.model().index(row, column)).center()
    else:
        pos = target.rect().center()
    # A real double-click is a press and release, then the double-click:
    # the view acts only on a double-click on the cell it saw pressed.
    QTest.mouseClick(target, Qt.MouseButton.LeftButton, pos=pos)
    QTest.mouseDClick(target, Qt.MouseButton.LeftButton, pos=pos)


def test_a_real_double_click_on_the_name_opens_the_terms(tab):
    with_two_termbases(tab)
    tab.termbases_tab.resize(1200, 600)
    tab.termbases_tab.show()
    mouse_double_click(tab, "OTHER", 1)
    assert shown(tab) == "Terms – OTHER"
    mouse_double_click(tab, "BEIJER", 0)                        # Type: a label of its own
    assert shown(tab) == "Terms – BEIJER"


def test_a_double_click_on_a_checkbox_does_not_open_the_terms(tab, monkeypatch):
    from PyQt6.QtWidgets import QMessageBox
    # Ticking AI asks first; answer No.
    monkeypatch.setattr(QMessageBox, "exec", lambda self: QMessageBox.StandardButton.No)
    with_two_termbases(tab)
    tab.termbases_tab.resize(1200, 600)
    tab.termbases_tab.show()
    assert shown(tab) == "Terms – BEIJER"
    for column in (4, 5, 7, 8):                                 # Read, Write, AI, SuperLookup
        mouse_double_click(tab, "OTHER", column)
        assert shown(tab) == "Terms – BEIJER"
