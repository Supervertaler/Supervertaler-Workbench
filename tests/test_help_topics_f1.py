"""F1 opens the help page of the Segmentation Rules and Inline Codes settings
pages and of the QA Checks and LanguageTool windows. Those pages exist on the
Help site, but F1 there opened the general page."""

from __future__ import annotations

import os
import sys
from types import SimpleNamespace

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtWidgets import QApplication, QLineEdit, QWidget

from modules.help_system import Topics, _HelpEventFilter

# The Help repository beside this checkout (or beside the _worktrees folder)
HELP_REPO = next((p for p in (os.path.join(os.path.dirname(ROOT), "Supervertaler-Help"),
                              os.path.join(os.path.dirname(os.path.dirname(ROOT)), "Supervertaler-Help"))
                  if os.path.isdir(p)), "")


@pytest.fixture(scope="module")
def S():
    S._app = QApplication.instance() or QApplication([])   # unreferenced, it is deleted at once
    saved = sys.stdout, sys.stderr
    import Supervertaler   # re-wraps stdout/stderr on Windows at import
    S._wrappers = sys.stdout, sys.stderr   # keep alive: closing them closes pytest's capture
    sys.stdout, sys.stderr = saved
    return Supervertaler


def _bare_window(S, **attrs):
    class Bare(S.SupervertalerQt):
        def __getattr__(self, name):
            raise AttributeError(name)
    win = Bare.__new__(Bare)
    win.logged = []
    win.log = win.logged.append
    win.load_general_settings = lambda: {}
    win.save_general_settings = lambda settings: None
    for name, value in attrs.items():
        setattr(win, name, value)
    return win


def _topic_inside(widget):
    """What F1 opens with the focus on a field inside ``widget``."""
    field = QLineEdit(widget)
    return _HelpEventFilter._resolve_topic(field)


def test_f1_on_the_segmentation_rules_page(S):
    widget = _bare_window(S).create_segmentation_rules_tab()
    assert _topic_inside(widget) == Topics.SETTINGS_SEGMENTATION


def test_f1_on_the_inline_codes_page(S):
    widget = _bare_window(S)._create_inline_codes_tab()
    assert _topic_inside(widget) == Topics.SETTINGS_INLINE_CODES


class _FakeDialog(QWidget):
    def __init__(self, *args, **kwargs):
        super().__init__()

    def refresh_sets(self):
        pass


@pytest.mark.parametrize("module, cls, method, topic", [
    ("modules.qa_checks_dialog", "QAChecksDialog", "show_qa_checks_dialog", Topics.QA_CHECKS),
    ("modules.languagetool_dialog", "LanguageToolDialog", "show_languagetool_dialog", Topics.QA_LANGUAGETOOL),
])
def test_f1_in_the_qa_windows(S, monkeypatch, module, cls, method, topic):
    monkeypatch.setattr(f"{module}.{cls}", _FakeDialog)
    win = _bare_window(S, user_data_path=".", current_project=None)
    getattr(win, method)()
    dlg = win._qa_checks_dialog if cls == "QAChecksDialog" else win._languagetool_dialog
    assert _topic_inside(dlg) == topic
    dlg.close()


@pytest.mark.skipif(not HELP_REPO, reason="no Supervertaler-Help checkout beside this repo")
@pytest.mark.parametrize("topic", [Topics.SETTINGS_SEGMENTATION, Topics.SETTINGS_INLINE_CODES,
                                   Topics.QA_CHECKS, Topics.QA_LANGUAGETOOL])
def test_the_pages_exist_on_the_help_site(topic):
    assert os.path.isfile(os.path.join(HELP_REPO, topic.strip("/") + ".md"))
