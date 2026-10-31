"""Superbrowser: "Send to all three" and private chats.

The real sites need a signed-in browser, so these drive the real column jobs
through QtWebEngine against small local pages built like the chat sites:
a message box whose send button only wakes up when the page's own input
handling has seen the text (as with ChatGPT's and Claude's editors), a
private-chat button that does or doesn't report its state, and a page that
only sends on Enter. What they can't catch is a site changing its markup;
the selectors for that live in CHAT_SERVICES.
"""

from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# Chromium refuses to start as root (CI containers) without this.
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--no-sandbox")

import pytest

pytest.importorskip("PyQt6.QtWebEngineWidgets")

from PyQt6.QtCore import QEventLoop, QUrl
from PyQt6.QtWidgets import QApplication, QSplitter

from modules import superbrowser as sb


# A chat page. The send button and Enter only see what arrived through
# input events, so text that was merely put into the page doesn't count.
CHAT_PAGE = """<!doctype html><html><head><meta charset="utf-8"></head><body>
<button id="toggle" aria-label="%(toggle_label)s" %(toggle_attr)s>toggle</button>
<button aria-label="Send feedback" onclick="window.feedback = true">Send feedback</button>
<div class="composer">
  %(box)s
  %(send)s
</div>
<script>
  var q = new URLSearchParams(location.search);
  window.sent = []; window.feedback = false; window.model = '';
  window.privateOn = q.has('private');
  var toggle = document.getElementById('toggle');
  if (q.has('notoggle')) toggle.remove();
  if (toggle.hasAttribute('aria-pressed')) toggle.setAttribute('aria-pressed', String(window.privateOn));
  toggle.onclick = function () {
    window.privateOn = !window.privateOn;
    if (toggle.hasAttribute('aria-pressed')) toggle.setAttribute('aria-pressed', String(window.privateOn));
  };
  var box = document.getElementById('box'), send = document.getElementById('send');
  function read() { return box.tagName === 'TEXTAREA' ? box.value : box.innerText; }
  function submit() {
    if (!window.model.trim()) return;
    window.sent.push({text: window.model.trim(), private: window.privateOn});
    window.model = '';
    if (box.tagName === 'TEXTAREA') box.value = ''; else box.textContent = '';
    if (send) send.disabled = true;
  }
  box.addEventListener('input', function () {
    window.model = read();
    if (send) send.disabled = !window.model.trim();
  });
  box.addEventListener('keydown', function (e) {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); submit(); }
  });
  if (send) { send.disabled = true; send.onclick = submit; }
</script></body></html>"""

EDITOR = '<div id="box" contenteditable="true" style="min-height:40px"></div>'


def _write_page(folder, name, box=EDITOR, send=True, toggle_label="Private chat",
                toggle_state=True):
    html = CHAT_PAGE % {
        "box": box,
        "send": ('<button id="send" class="send-button" data-testid="send-button" '
                 'aria-label="Send message">Send</button>') if send else "",
        "toggle_label": toggle_label,
        "toggle_attr": 'aria-pressed="false"' if toggle_state else "",
    }
    path = os.path.join(folder, name)
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    return QUrl.fromLocalFile(path).toString()


# QtWebEngine aborts the whole process when its QApplication was made without
# a program name, which is how the other test files make theirs. So the
# browser tests run on their own, in a fresh interpreter started by
# test_browser_tests_in_own_process.
IN_CHILD = os.environ.get("SUPERBROWSER_BROWSER_TESTS") == "1"
browser_test = pytest.mark.skipif(not IN_CHILD, reason="run by test_browser_tests_in_own_process")


def test_browser_tests_in_own_process():
    if IN_CHILD:
        pytest.skip("already in the child process")
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", __file__],
        env=dict(os.environ, SUPERBROWSER_BROWSER_TESTS="1"),
        capture_output=True, text=True, timeout=600)
    assert result.returncode == 0, result.stdout[-4000:] + result.stderr[-2000:]


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([sys.argv[0] or "pytest"])


@pytest.fixture(autouse=True)
def quick_timing(monkeypatch):
    for name, value in [("POLL_MS", 100), ("SETTLE_MS", 200), ("TOGGLE_SETTLE_MS", 300),
                        ("SEND_DELAY_MS", 150), ("CHECK_DELAY_MS", 400),
                        ("READY_TRIES", 60), ("TOGGLE_TRIES", 5)]:
        monkeypatch.setattr(sb._ColumnJob, name, value)


_windows = []   # kept open until the end: profiles must outlive their pages


def _wait(app, condition, timeout=30):
    end = time.time() + timeout
    while time.time() < end:
        if condition():
            return True
        app.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 50)
    return False


def _columns(app, folder, specs):
    """specs: (title, home_url, service). One window, side by side."""
    split = QSplitter()
    _windows.append(split)
    columns = []
    for title, home, service in specs:
        column = sb.ChatColumn(title, home, "#888888", None,
                               user_data_path=folder, service=service)
        split.addWidget(column)
        columns.append(column)
    split.resize(1500, 700)
    split.show()
    assert _wait(app, lambda: all(c._load_seq for c in columns))
    return columns


def _run(app, columns, **kwargs):
    done = {}
    for column in columns:
        column.run(on_done=lambda title, ok, message: done.__setitem__(title, (ok, message)),
                   **kwargs)
    assert _wait(app, lambda: len(done) == len(columns), 60), done
    return done


def _sent(app, column):
    box = {}
    column.web_view.page().runJavaScript("JSON.stringify(window.sent)", 0,
                                         lambda result: box.setdefault("r", result))
    assert _wait(app, lambda: "r" in box, 10)
    return json.loads(box["r"])


def _service(name, **changes):
    service = copy.deepcopy(sb.CHAT_SERVICES[name])
    service.update(changes)
    return service


def test_page_script_carries_any_question_safely():
    question = 'He said "hi" \\ </script>\n  – Michaël'
    script = sb.build_page_script("insert", sb.CHAT_SERVICES["Claude"], question)
    assert "__CFG__" not in script
    assert json.dumps(question) in script


@browser_test
def test_asks_all_three_in_private_chats(app, tmp_path):
    folder = str(tmp_path)
    home = _write_page(folder, "chat.html")                       # button reads its state
    stateless = _write_page(folder, "gemini.html", toggle_label="Temporary chat",
                            toggle_state=False)                   # button doesn't
    columns = _columns(app, folder, [
        # Private through its address alone, like ChatGPT.
        ("ChatGPT", home, _service("ChatGPT", private_url=home + "?private")),
        # Address not honoured; its button reads "off", so it gets pressed.
        ("Claude", home, _service("Claude", private_url=home,
                                  private_toggle=["#toggle"])),
        # No address and a button that doesn't say whether it's on, like Gemini.
        ("Gemini", stateless, _service("Gemini", private_url=stateless)),
    ])
    question = "Who is Michaël “Mike” O'Brien?\nAnd what is Supervertaler?"

    done = _run(app, columns, text=question, fresh=True, private=True)

    for column in columns:
        assert done[column.title] == (True, "✓ Question sent"), column.title
        assert _sent(app, column) == [{"text": question, "private": True}], column.title
        assert column.private_note.startswith("🕶"), column.title
    assert "not saved" in columns[1].private_note        # confirmed by its button

    # A follow-up goes into the chats already open.
    done = _run(app, columns, text="Tell me more.", fresh=False, private=True)
    for column in columns:
        assert done[column.title][0], column.title
        assert [m["text"] for m in _sent(app, column)] == [question, "Tell me more."]


@browser_test
def test_no_question_sent_where_private_chat_cannot_be_switched_on(app, tmp_path):
    folder = str(tmp_path)
    page = _write_page(folder, "gemini.html", toggle_label="Temporary chat",
                       toggle_state=False)
    (column,) = _columns(app, folder, [
        ("Gemini", page, _service("Gemini", private_url=page + "?notoggle")),
    ])

    done = _run(app, [column], text="Who is X?", fresh=True, private=True)

    ok, message = done["Gemini"]
    assert not ok and "Couldn't switch on Gemini's Temporary chat" in message
    assert _sent(app, column) == []


@browser_test
def test_sends_with_enter_when_there_is_no_send_button(app, tmp_path):
    folder = str(tmp_path)
    textarea = _write_page(folder, "plain.html", send=False,
                           box='<textarea id="box"></textarea>')
    editor = _write_page(folder, "editor.html", send=False)
    columns = _columns(app, folder, [
        ("ChatGPT", textarea, _service("ChatGPT")),
        ("Claude", editor, _service("Claude")),
    ])

    done = _run(app, columns, text="Who is X?", fresh=False)

    for column in columns:
        assert done[column.title] == (True, "✓ Question sent"), column.title
        assert [m["text"] for m in _sent(app, column)] == ["Who is X?"]


@browser_test
def test_reports_a_page_without_a_message_box(app, tmp_path, monkeypatch):
    monkeypatch.setattr(sb._ColumnJob, "READY_TRIES", 3)
    (column,) = _columns(app, str(tmp_path), [("Claude", "about:blank", _service("Claude"))])

    done = _run(app, [column], text="Who is X?", fresh=False)

    ok, message = done["Claude"]
    assert not ok and "Couldn't find Claude's message box" in message
