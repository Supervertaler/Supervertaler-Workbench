#!/usr/bin/env python3
"""
=============================================================================
MODULE: Superbrowser - Multi-Chat AI Browser
=============================================================================
Display multiple AI chat pages side by side in a single interface.
Supports ChatGPT, Claude, and Gemini in a three-column resizable layout.

The box at the bottom asks all three the same question at once, and
"Private chats" opens each service's own temporary chat first (ChatGPT
Temporary Chat, Claude incognito chat, Gemini Temporary chat), so a
question can be asked in a fresh chat that is not saved to the history
and does not use or update the AI's memory of the user - for instance
to see how each AI describes you or your business to a stranger.

Author: Michael Beijer
Date: November 18, 2025
Version: 1.1.0
=============================================================================
"""

from __future__ import annotations

import json
import os
import shutil
from PyQt6.QtCore import QUrl, Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QApplication, QWidget, QHBoxLayout, QVBoxLayout, QLabel,
    QSplitter, QPushButton, QLineEdit, QCheckBox, QPlainTextEdit,
    QGroupBox, QFormLayout, QSizePolicy
)
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWebEngineCore import QWebEngineProfile, QWebEnginePage


# =============================================================================
# HOW SUPERBROWSER DRIVES EACH CHAT SITE
# =============================================================================
# None of this is a published API. The sites change their pages without
# notice, so every selector lives here, tried in order, with generic
# fallbacks after them (see _PAGE_SCRIPT). When "Send to all three" or
# private chats stop working on one site, this table is the place to fix it.
#
#   private_url      Where a new private chat starts.
#   private_toggle   The site's own private-chat button. Pressed only when
#                    it reads as off (or, with private_toggle_required, when
#                    its state can't be read on a freshly loaded page, which
#                    always starts as an ordinary chat).
#   input / send     The message box and its send button.
CHAT_SERVICES = {
    "ChatGPT": {
        "home_url": "https://chatgpt.com/",
        "color": "#10a37f",
        # Not documented by OpenAI, but long-standing and widely used.
        "private_url": "https://chatgpt.com/?temporary-chat=true",
        "private_name": "Temporary Chat",
        "private_toggle": [],
        "private_toggle_required": False,
        "input": ['#prompt-textarea[contenteditable="true"]',
                  'div.ProseMirror[contenteditable="true"]'],
        "send": ['button[data-testid="send-button"]',
                 '#composer-submit-button'],
    },
    "Claude": {
        "home_url": "https://claude.ai/",
        "color": "#c17c4f",
        # The address Claude's own ghost (incognito) button leads to; not
        # documented either, so the button is checked as well.
        "private_url": "https://claude.ai/new?incognito",
        "private_name": "incognito chat",
        "private_toggle": ['button[aria-label*="incognito" i]',
                           '[data-testid*="incognito" i]'],
        "private_toggle_required": False,
        "input": ['[data-testid="chat-input"][contenteditable="true"]',
                  'div.ProseMirror[contenteditable="true"]'],
        "send": ['button[aria-label="Send message"]'],
    },
    "Gemini": {
        "home_url": "https://gemini.google.com/",
        "color": "#4285f4",
        # Gemini has no address for a temporary chat: open a new chat and
        # press its Temporary chat button.
        "private_url": "https://gemini.google.com/app",
        "private_name": "Temporary chat",
        "private_toggle": ['[data-test-id*="temp-chat" i]',
                           'button[aria-label*="temporary chat" i]',
                           '[mattooltip*="temporary chat" i]'],
        "private_toggle_required": True,
        "input": ['rich-textarea .ql-editor[contenteditable="true"]',
                  '.ql-editor[contenteditable="true"]'],
        "send": ['button.send-button',
                 'button[aria-label="Send message"]'],
    },
}


# One script for every step, run in the page. Python fills in __CFG__ (with
# json.dumps, so the question needs no further escaping) and the script
# answers with a short status word. It never waits: runJavaScript can't
# return the result of a promise, so all waiting and retrying is done by
# _ColumnJob on Qt timers.
_PAGE_SCRIPT = r"""
(function (cfg) {
  'use strict';
  var GENERIC_INPUT = ['div[contenteditable="true"]', 'textarea'];
  // Only looked for next to the message box, so a "Send feedback" button
  // elsewhere on the page can't be mistaken for the send button.
  var GENERIC_SEND = ['button[aria-label*="send" i]', 'button[type="submit"]'];

  function visible(el) {
    if (!el || !el.getBoundingClientRect) return false;
    var r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  }
  // The last match wins: the message box and its button come after
  // anything else on the page that might match.
  function find(root, selectors, mustBeVisible) {
    for (var i = 0; i < selectors.length; i++) {
      var all;
      try { all = root.querySelectorAll(selectors[i]); } catch (e) { continue; }
      for (var j = all.length - 1; j >= 0; j--) {
        if (!mustBeVisible || visible(all[j])) return all[j];
      }
    }
    return null;
  }
  function findNear(el, selectors) {
    for (var node = el && el.parentElement, depth = 0; node && depth < 12;
         node = node.parentElement, depth++) {
      var hit = find(node, selectors, true);
      if (hit) return hit;
    }
    return null;
  }
  function findInput() { return find(document, cfg.input.concat(GENERIC_INPUT), true); }
  function isField(el) { return el.tagName === 'TEXTAREA' || el.tagName === 'INPUT'; }
  function norm(s) { return String(s || '').replace(/\s+/g, ' ').trim(); }
  function holdsQuestion(el) {
    var probe = norm(cfg.text).slice(0, 40);
    return probe !== '' && norm(isField(el) ? el.value : el.innerText).indexOf(probe) >= 0;
  }
  function switchState(el) {
    var attrs = ['aria-pressed', 'aria-checked', 'aria-selected'];
    for (var i = 0; i < attrs.length; i++) {
      var v = el.getAttribute(attrs[i]);
      if (v === 'true') return 'on';
      if (v === 'false') return 'off';
    }
    var ds = (el.getAttribute('data-state') || '').toLowerCase();
    if (ds === 'on' || ds === 'checked' || ds === 'active') return 'on';
    if (ds === 'off' || ds === 'unchecked' || ds === 'inactive') return 'off';
    var label = (el.getAttribute('aria-label') || el.getAttribute('mattooltip') ||
                 el.title || '').toLowerCase();
    if (/\b(turn off|exit|leave|stop)\b/.test(label)) return 'on';
    if (/\bturn on\b/.test(label)) return 'off';
    return 'unknown';
  }
  function typeInto(el, text) {
    el.focus();
    if (isField(el)) {
      // The native setter: React-style pages ignore a plain assignment.
      var proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype
                                            : HTMLInputElement.prototype;
      Object.getOwnPropertyDescriptor(proto, 'value').set.call(el, text);
      el.dispatchEvent(new Event('input', { bubbles: true }));
      return;
    }
    // Rich editors (ProseMirror on ChatGPT and Claude, Quill on Gemini) keep
    // their own copy of the text and only take edits that arrive as real
    // editing, which is what execCommand produces. Selecting the box's
    // contents first replaces any draft left in it.
    var range = document.createRange();
    range.selectNodeContents(el);
    var sel = window.getSelection();
    sel.removeAllRanges();
    sel.addRange(range);
    var lines = text.split('\n');
    for (var i = 0; i < lines.length; i++) {
      if (i > 0) document.execCommand('insertParagraph', false, null);
      if (lines[i]) document.execCommand('insertText', false, lines[i]);
    }
  }

  var el;
  switch (cfg.action) {
    case 'ready':
      return findInput() ? 'ready' : 'waiting';

    case 'private':
      el = find(document, cfg.toggle, false);
      if (!el) return 'no-toggle';
      var state = switchState(el);
      if (state === 'on') return 'on';
      if (state === 'off' || cfg.clickUnknown) { el.click(); return 'clicked'; }
      return 'unknown';

    case 'private-state':
      el = find(document, cfg.toggle, false);
      return el ? switchState(el) : 'unknown';

    case 'insert':
      el = findInput();
      if (!el) return 'no-input';
      typeInto(el, cfg.text);
      return holdsQuestion(el) ? 'inserted' : 'not-inserted';

    case 'send':
      el = findInput();
      var button = find(document, cfg.send, true) || findNear(el, GENERIC_SEND);
      if (!button) return 'no-button';
      if (button.disabled || button.getAttribute('aria-disabled') === 'true') return 'disabled';
      button.click();
      return 'clicked';

    case 'enter':
      el = findInput();
      if (!el) return 'no-input';
      el.focus();
      var key = { key: 'Enter', code: 'Enter', keyCode: 13, which: 13,
                  bubbles: true, cancelable: true };
      el.dispatchEvent(new KeyboardEvent('keydown', key));
      el.dispatchEvent(new KeyboardEvent('keypress', key));
      el.dispatchEvent(new KeyboardEvent('keyup', key));
      return 'pressed';

    case 'sent?':
      // Once a question is sent, the site empties its message box (or swaps
      // it for a new one).
      el = findInput();
      return el && holdsQuestion(el) ? 'still-there' : 'sent';
  }
  return 'unknown-action';
})(__CFG__)
"""


def build_page_script(action: str, service: dict, text: str = "") -> str:
    """The page script for one step against one chat service."""
    cfg = {
        "action": action,
        "text": text,
        "input": service.get("input", []),
        "send": service.get("send", []),
        "toggle": service.get("private_toggle", []),
        "clickUnknown": bool(service.get("private_toggle_required")),
    }
    return _PAGE_SCRIPT.replace("__CFG__", json.dumps(cfg))


def _clear_corrupted_cache(storage_path: str):
    """
    Clear potentially corrupted Chromium cache folders.
    These can cause 'wrong file structure on disk' errors on startup.
    """
    problematic_dirs = [
        os.path.join(storage_path, "Shared Dictionary"),
        os.path.join(storage_path, "cache", "Shared Dictionary"),
    ]
    for dir_path in problematic_dirs:
        if os.path.exists(dir_path):
            try:
                shutil.rmtree(dir_path)
            except Exception:
                pass  # Silently ignore - may be in use


class _ColumnJob:
    """Open a new chat and/or ask a question in one column.

    Every step runs on a Qt timer or a runJavaScript reply, so the window
    stays responsive while the three sites load. Starting another job in the
    same column makes this one stale, and a stale job quietly stops at its
    next step.
    """

    POLL_MS = 500
    READY_TRIES = 40          # ~20 s for the message box to appear
    TOGGLE_TRIES = 10         # ~5 s more for a private-chat button to appear
    SETTLE_MS = 1000          # let a new page wire up its editor once it shows
    TOGGLE_SETTLE_MS = 1200   # let the page switch over after pressing its button
    SEND_DELAY_MS = 400       # let the send button notice the typed question
    SEND_TRIES = 8
    CHECK_DELAY_MS = 1500

    def __init__(self, column, text, fresh, private, on_done):
        self.column = column
        self.service = column.service
        self.text = text
        self.fresh = fresh
        self.private = private and fresh
        self.on_done = on_done
        self.load_seq = None
        self.pressed_enter = False

    # -- plumbing ---------------------------------------------------------

    def _alive(self):
        return self.column._job is self

    def _later(self, ms, step, *args):
        QTimer.singleShot(ms, lambda: self._alive() and step(*args))

    def _js(self, action, reply):
        script = build_page_script(action, self.service, self.text or "")

        def on_reply(result):
            if self._alive():
                reply(result)

        try:
            self.column.web_view.page().runJavaScript(script, 0, on_reply)
        except RuntimeError:
            # The window was closed while this job was running.
            self.column._job = None

    # -- steps ------------------------------------------------------------

    def start(self):
        if self.fresh:
            url = self.service.get("private_url") if self.private else self.column.url
            # Until the new page has loaded, the old one is still on screen and
            # would answer for it.
            self.load_seq = self.column._load_seq
            self.column.web_view.setUrl(QUrl(url or self.column.url))
            self._later(self.POLL_MS, self._wait_ready, 1)
        else:
            self._wait_ready(0)

    def _wait_ready(self, tries):
        if self.load_seq is not None and self.column._load_seq == self.load_seq:
            if tries < self.READY_TRIES:
                self._later(self.POLL_MS, self._wait_ready, tries + 1)
                return
            self.load_seq = None   # no load signal; look anyway
        self._js("ready", lambda result: self._on_ready(result, tries))

    def _on_ready(self, result, tries):
        if result == "ready":
            settle = self.SETTLE_MS if self.load_seq is not None else 0
            self.load_seq = None
            self._later(settle, self._after_ready)
        elif tries < self.READY_TRIES:
            self._later(self.POLL_MS, self._wait_ready, tries + 1)
        else:
            self._fail(f"Couldn't find {self.column.title}'s message box. "
                       f"Are you signed in?")

    def _after_ready(self):
        if self.private and self.column.private_note is None:
            self._activate_private(0)
        elif self.text:
            self._js("insert", self._on_insert)
        else:
            self._finish(True)

    def _activate_private(self, tries):
        if not self.service.get("private_toggle"):
            self._private_done(confirmed=False)   # the private address did it
            return
        self._js("private", lambda result: self._on_private(result, tries))

    def _on_private(self, result, tries):
        if result == "on":
            self._private_done(confirmed=True)
        elif result == "clicked":
            self._later(self.TOGGLE_SETTLE_MS, self._js, "private-state",
                        self._on_private_state)
        elif result == "no-toggle" and self.service.get("private_toggle_required"):
            if tries < self.TOGGLE_TRIES:
                self._later(self.POLL_MS, self._activate_private, tries + 1)
            else:
                self._private_failed()
        else:
            # 'unknown', or no button where the private address already did it.
            self._private_done(confirmed=False)

    def _on_private_state(self, result):
        if result == "off":
            self._private_failed()
        else:
            self._private_done(confirmed=(result == "on"))

    def _private_done(self, confirmed):
        name = self.service.get("private_name", "private chat")
        if confirmed:
            self.column.private_note = f"🕶 New {name} – not saved, no memory"
        else:
            self.column.private_note = (f"🕶 Opened a new {name} – check "
                                        f"{self.column.title} shows it")
        if self.text:
            # The page may have redrawn its message box.
            self._later(self.POLL_MS, self._wait_ready, 0)
        else:
            self._finish(True)

    def _private_failed(self):
        title = self.column.title
        name = self.service.get("private_name", "private chat")
        if self.text:
            self._fail(f"Couldn't switch on {title}'s {name}, so your question "
                       f"wasn't sent here. Switch it on with {title}'s own "
                       f"button, then paste your question in.")
        else:
            self._fail(f"Couldn't switch on {title}'s {name}: switch it on with "
                       f"the button on the page.")

    def _on_insert(self, result):
        if result == "inserted":
            self._later(self.SEND_DELAY_MS, self._send, 0)
        elif result == "no-input":
            self._fail(f"Couldn't find {self.column.title}'s message box.")
        else:
            self._fail(f"Couldn't type into {self.column.title}'s message box.")

    def _send(self, tries):
        self._js("send", lambda result: self._on_send(result, tries))

    def _on_send(self, result, tries):
        if result == "clicked":
            self._later(self.CHECK_DELAY_MS, self._check_sent, 0)
        elif result == "disabled" and tries < self.SEND_TRIES:
            self._later(self.POLL_MS, self._send, tries + 1)
        else:
            # No send button found, or it never became clickable.
            self._press_enter()

    def _press_enter(self):
        self.pressed_enter = True
        self._js("enter", lambda result: self._later(self.CHECK_DELAY_MS,
                                                     self._check_sent, 0))

    def _check_sent(self, tries):
        self._js("sent?", lambda result: self._on_check(result, tries))

    def _on_check(self, result, tries):
        if result == "sent":
            self._finish(True)
        elif tries < 1:
            self._later(self.CHECK_DELAY_MS, self._check_sent, tries + 1)
        elif not self.pressed_enter:
            self._press_enter()
        else:
            self._fail("Your question is typed in, but it couldn't be sent: "
                       "press Enter in this column.")

    # -- outcome ----------------------------------------------------------

    def _finish(self, ok, message=""):
        self.column._job = None
        if ok:
            if self.text:
                message = "✓ Question sent"
            elif not self.private:
                message = "New chat"
        self.column.show_status(message)
        self.on_done(self.column.title, ok, message)

    def _fail(self, message):
        self._finish(False, "⚠ " + message)


class ChatColumn(QWidget):
    """A column containing a chat interface with web browser"""

    def __init__(self, title, url, header_color, parent=None, user_data_path=None,
                 service=None):
        super().__init__(parent)
        self.title = title
        self.url = url
        self.header_color = header_color
        self.user_data_path = user_data_path  # Store user data path
        self.service = service if service is not None else CHAT_SERVICES.get(title, {})
        self.private_note = None   # set while the open chat is one we made private
        self._job = None           # the running _ColumnJob, if any
        self._load_seq = 0         # counts finished page loads, for _ColumnJob
        self.init_ui()

    def init_ui(self):
        """Initialize the chat column UI"""
        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Tiny header label with provider name
        header = QLabel(self.title)
        header.setAlignment(Qt.AlignmentFlag.AlignCenter)
        header.setStyleSheet(f"""
            QLabel {{
                background-color: {self.header_color};
                color: white;
                padding: 0px;
                margin: 0px;
                font-weight: bold;
                font-size: 9px;
                max-height: 20px;
                min-height: 20px;
            }}
        """)

        # URL bar for navigation
        url_layout = QHBoxLayout()
        url_layout.setContentsMargins(1, 1, 1, 1)

        self.url_input = QLineEdit()
        self.url_input.setText(self.url)
        self.url_input.setPlaceholderText("Enter URL...")
        self.url_input.returnPressed.connect(self.load_url)

        reload_btn = QPushButton("↻")
        reload_btn.setMaximumWidth(40)
        reload_btn.setToolTip("Reload page")
        reload_btn.clicked.connect(self.reload_page)

        home_btn = QPushButton("⌂")
        home_btn.setMaximumWidth(40)
        home_btn.setToolTip("Go to home URL")
        home_btn.clicked.connect(self.go_home)

        url_layout.addWidget(self.url_input)
        url_layout.addWidget(reload_btn)
        url_layout.addWidget(home_btn)

        # What "Send to all three" / "Private chats" last did in this column
        self.status_label = QLabel()
        self.status_label.setWordWrap(True)
        self.status_label.setStyleSheet("font-size: 10px; padding: 1px 4px;")
        self.status_label.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        self.status_label.setVisible(False)

        # Web view with persistent profile
        # Create or use persistent profile to save cookies and session data
        profile_name = f"superbrowser_{self.title.lower()}"
        self.profile = QWebEngineProfile(profile_name, self)

        # Set persistent storage path using user_data_path from parent
        if self.user_data_path:
            storage_path = os.path.join(str(self.user_data_path), "workbench", "superbrowser_profiles", profile_name)
        else:
            # Fallback to script directory if user_data_path not provided
            dev_mode_marker = os.path.join(os.path.dirname(__file__), "..", ".supervertaler.local")
            base_folder = "user_data_private" if os.path.exists(dev_mode_marker) else "user_data"
            storage_path = os.path.join(os.path.dirname(__file__), "..", base_folder, "superbrowser_profiles", profile_name)
        os.makedirs(storage_path, exist_ok=True)

        # Clear potentially corrupted cache to prevent Chromium errors
        _clear_corrupted_cache(storage_path)

        self.profile.setPersistentStoragePath(storage_path)
        self.profile.setCachePath(os.path.join(storage_path, "cache"))

        # Enable persistent cookies
        self.profile.setPersistentCookiesPolicy(QWebEngineProfile.PersistentCookiesPolicy.AllowPersistentCookies)

        # Create web view with this profile
        page = QWebEnginePage(self.profile, self)
        self.web_view = QWebEngineView()
        self.web_view.setPage(page)
        self.web_view.setUrl(QUrl(self.url))

        # Update URL bar when page changes
        self.web_view.urlChanged.connect(self.update_url_bar)
        self.web_view.loadFinished.connect(self._on_load_finished)

        # Add to layout (tiny header, URL bar, status, then browser)
        layout.addWidget(header)
        layout.addLayout(url_layout)
        layout.addWidget(self.status_label)
        layout.addWidget(self.web_view, 1)

        self.setLayout(layout)

    def load_url(self):
        """Load URL from input field"""
        url_text = self.url_input.text().strip()
        if not url_text.startswith(('http://', 'https://')):
            url_text = 'https://' + url_text
        self._leave_chat()
        self.web_view.setUrl(QUrl(url_text))

    def reload_page(self):
        """Reload the current page"""
        self.web_view.reload()

    def go_home(self):
        """Go back to the home URL"""
        self._leave_chat()
        self.web_view.setUrl(QUrl(self.url))

    def update_url_bar(self, url):
        """Update URL bar when page changes"""
        self.url_input.setText(url.toString())

    def _on_load_finished(self, ok):
        # Only completed loads: a load cut short by the next one also reports
        # in, and a job would then go looking in the page on its way out.
        if ok:
            self._load_seq += 1

    def _leave_chat(self):
        """The user navigated away: stop any job, forget the private note."""
        self._job = None
        self.private_note = None
        self.show_status("")

    # -- "Send to all three" / "Private chats" ----------------------------

    def run(self, text=None, fresh=False, private=False, on_done=None):
        """Open a new chat (fresh), privately if asked, and/or ask a question.

        on_done(title, ok, message) is called once the column is done.
        """
        if fresh:
            # Opening a chat replaces whatever was open, private or not.
            self.private_note = None
        self._job = _ColumnJob(self, text, fresh, private,
                               on_done or (lambda *args: None))
        self.show_status("⏳ Asking…" if text else "⏳ Opening a new chat…")
        self._job.start()

    def show_status(self, message):
        """Show what the last job did, after the private-chat note if any."""
        text = " · ".join(part for part in (self.private_note, message) if part)
        self.status_label.setText(text)
        self.status_label.setVisible(bool(text))

    def cleanup(self):
        """Clean up web engine resources before deletion"""
        self._job = None
        try:
            from PyQt6.QtCore import QUrl
            if hasattr(self, 'web_view'):
                self.web_view.stop()
                self.web_view.setPage(None)
                self.web_view.setUrl(QUrl('about:blank'))
                self.web_view.deleteLater()
            if hasattr(self, 'profile'):
                self.profile.deleteLater()
        except:
            pass


class _QuestionBox(QPlainTextEdit):
    """Question box: Enter sends, Shift+Enter starts a new line."""

    submitted = pyqtSignal()

    def keyPressEvent(self, event):
        if (event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
                and not event.modifiers() & Qt.KeyboardModifier.ShiftModifier):
            self.submitted.emit()
            return
        super().keyPressEvent(event)


class SuperbrowserWidget(QWidget):
    """
    Superbrowser - Multi-Chat AI Browser Widget

    Displays multiple AI chat interfaces side by side for easy comparison
    and concurrent interaction with different AI models.
    """

    def __init__(self, parent=None, user_data_path=None):
        super().__init__(parent)
        self.parent_window = parent
        self.user_data_path = user_data_path  # Store user data path for profiles

        # Default URLs for AI chat interfaces
        self.chatgpt_url = CHAT_SERVICES["ChatGPT"]["home_url"]
        self.claude_url = CHAT_SERVICES["Claude"]["home_url"]
        self.gemini_url = CHAT_SERVICES["Gemini"]["home_url"]

        self._batch = 0          # identifies the latest "all three" run
        self._results = {}

        self.init_ui()

    def init_ui(self):
        """Initialize the Superbrowser UI"""
        main_layout = QVBoxLayout()
        main_layout.setContentsMargins(5, 5, 5, 5)
        main_layout.setSpacing(5)

        # Compact title bar with toggle button
        title_bar_layout = QHBoxLayout()
        title_bar_layout.setContentsMargins(0, 0, 0, 0)

        title_label = QLabel("🌐 Superbrowser - Multi-Chat AI Browser")
        title_label.setStyleSheet("""
            QLabel {
                font-size: 12px;
                font-weight: bold;
                color: #2c3e50;
                padding: 3px;
            }
        """)

        description = QLabel(
            "ChatGPT, Claude, and Gemini side by side. Ask all three the same "
            "question from the box at the bottom; switch on 🕶 Private chats to "
            "ask in fresh chats that aren't saved or remembered."
        )
        description.setWordWrap(True)
        description.setStyleSheet("color: #7f8c8d; padding: 3px; font-size: 10px;")

        # Toggle button for configuration section
        self.toggle_config_btn = QPushButton("▼ Hide Configuration")
        self.toggle_config_btn.setMaximumWidth(150)
        self.toggle_config_btn.setStyleSheet("""
            QPushButton {
                background-color: #3498db;
                color: white;
                border: none;
                padding: 4px 8px;
                font-size: 10px;
                border-radius: 3px;
            }
            QPushButton:hover {
                background-color: #2980b9;
            }
        """)
        self.toggle_config_btn.clicked.connect(self.toggle_configuration)

        title_bar_layout.addWidget(title_label)
        title_bar_layout.addWidget(description, stretch=1)
        title_bar_layout.addWidget(self.toggle_config_btn)

        main_layout.addLayout(title_bar_layout)

        # URL Configuration section (collapsible)
        self.config_group = QGroupBox("🔧 Configuration")
        self.config_group.setStyleSheet("QGroupBox { font-size: 10px; font-weight: bold; }")
        config_layout = QFormLayout()

        self.chatgpt_url_input = QLineEdit(self.chatgpt_url)
        self.chatgpt_url_input.setStyleSheet("font-size: 10px;")
        self.claude_url_input = QLineEdit(self.claude_url)
        self.claude_url_input.setStyleSheet("font-size: 10px;")
        self.gemini_url_input = QLineEdit(self.gemini_url)
        self.gemini_url_input.setStyleSheet("font-size: 10px;")

        config_layout.addRow("ChatGPT URL:", self.chatgpt_url_input)
        config_layout.addRow("Claude URL:", self.claude_url_input)
        config_layout.addRow("Gemini URL:", self.gemini_url_input)

        update_btn = QPushButton("Update URLs")
        update_btn.setStyleSheet("font-size: 10px; padding: 3px;")
        update_btn.clicked.connect(self.update_urls)
        config_layout.addRow("", update_btn)

        self.config_group.setLayout(config_layout)
        self.config_group.setMaximumHeight(150)
        main_layout.addWidget(self.config_group)

        # Splitter for resizable columns
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(3)

        # Create chat columns - pass user_data_path for profile storage
        self.chatgpt_column = ChatColumn("ChatGPT", self.chatgpt_url, CHAT_SERVICES["ChatGPT"]["color"], self, user_data_path=self.user_data_path)
        self.claude_column = ChatColumn("Claude", self.claude_url, CHAT_SERVICES["Claude"]["color"], self, user_data_path=self.user_data_path)
        self.gemini_column = ChatColumn("Gemini", self.gemini_url, CHAT_SERVICES["Gemini"]["color"], self, user_data_path=self.user_data_path)
        self.chat_columns = [self.chatgpt_column, self.claude_column, self.gemini_column]

        # Add columns to splitter
        for column in self.chat_columns:
            splitter.addWidget(column)

        # Set equal sizes for all columns
        splitter.setSizes([600, 600, 600])

        # Add splitter to main layout (takes most of the space)
        main_layout.addWidget(splitter, stretch=1)

        main_layout.addLayout(self._build_ask_bar())

        self.setLayout(main_layout)

    def _build_ask_bar(self):
        """The bar under the columns: private chats, new chats, question box."""
        bar = QVBoxLayout()
        bar.setContentsMargins(0, 0, 0, 0)
        bar.setSpacing(3)

        options = QHBoxLayout()
        options.setContentsMargins(0, 0, 0, 0)

        self.private_btn = QPushButton("🕶 Private chats: Off")
        self.private_btn.setCheckable(True)
        # Unmistakable when on, in light and dark themes alike.
        self.private_btn.setStyleSheet(
            "QPushButton:checked { background-color: #5b4b8a; color: white; "
            "border: 1px solid #3f3366; border-radius: 3px; padding: 3px 8px; }")
        self.private_btn.setToolTip(
            "Ask in each service's own private chat, which isn't saved to your\n"
            "history and doesn't use or update the AI's memory of you:\n"
            "  • ChatGPT – Temporary Chat\n"
            "  • Claude – incognito chat\n"
            "  • Gemini – Temporary chat\n"
            "Useful for seeing how each AI describes you or your business to\n"
            "someone it doesn't know. You stay signed in, so some account\n"
            "settings (such as ChatGPT's custom instructions) may still apply.\n\n"
            "Switching this on opens a new private chat in all three columns."
        )
        self.private_btn.toggled.connect(self._on_private_toggled)

        self.new_chats_btn = QPushButton("＋ New chats")
        self.new_chats_btn.setToolTip(
            "Open a new, empty chat in all three columns\n"
            "(private ones while Private chats is on).")
        self.new_chats_btn.clicked.connect(lambda: self.open_new_chats())

        self.fresh_check = QCheckBox("New chat for each question")
        self.fresh_check.setChecked(True)
        self.fresh_check.setToolTip(
            "On: every question starts in a new, empty chat, so earlier\n"
            "questions can't colour the answer.\n"
            "Off: questions go into the chats already open, for follow-ups.")

        self.ask_status = QLabel()
        self.ask_status.setWordWrap(True)
        self.ask_status.setStyleSheet("font-size: 10px; padding: 0px 4px;")

        options.addWidget(self.private_btn)
        options.addWidget(self.new_chats_btn)
        options.addWidget(self.fresh_check)
        options.addWidget(self.ask_status, stretch=1)
        bar.addLayout(options)

        ask_row = QHBoxLayout()
        ask_row.setContentsMargins(0, 0, 0, 0)
        self.question_box = _QuestionBox()
        self.question_box.setPlaceholderText(
            "Ask all three at once – e.g. “What is Supervertaler?” or “Who is "
            "<your name>?”  (Enter sends, Shift+Enter for a new line)")
        self.question_box.setFixedHeight(self.question_box.fontMetrics().lineSpacing() * 3 + 14)
        self.question_box.submitted.connect(self.ask_all)

        self.send_btn = QPushButton("Send to all three ▶")
        self.send_btn.setToolTip(
            "Type the question into ChatGPT, Claude and Gemini and send it.\n"
            "This works by operating the three web pages, so a change to one of\n"
            "the sites can break it there: the column then says so, and your\n"
            "question is put on the clipboard to paste in by hand.")
        self.send_btn.clicked.connect(self.ask_all)

        ask_row.addWidget(self.question_box, stretch=1)
        ask_row.addWidget(self.send_btn)
        bar.addLayout(ask_row)
        return bar

    def toggle_configuration(self):
        """Toggle visibility of configuration section"""
        if self.config_group.isVisible():
            self.config_group.setVisible(False)
            self.toggle_config_btn.setText("▶ Show Configuration")
        else:
            self.config_group.setVisible(True)
            self.toggle_config_btn.setText("▼ Hide Configuration")

    def update_urls(self):
        """Update the URLs for all chat columns"""
        self.chatgpt_url = self.chatgpt_url_input.text().strip() or self.chatgpt_url
        self.claude_url = self.claude_url_input.text().strip() or self.claude_url
        self.gemini_url = self.gemini_url_input.text().strip() or self.gemini_url

        # Update the columns
        self.chatgpt_column.url = self.chatgpt_url
        self.claude_column.url = self.claude_url
        self.gemini_column.url = self.gemini_url

        # Reload to new URLs
        self.chatgpt_column.go_home()
        self.claude_column.go_home()
        self.gemini_column.go_home()

    # -- "Send to all three" / "Private chats" ----------------------------

    def ask_all(self):
        """Ask the question in the box in all three columns."""
        text = self.question_box.toPlainText().strip()
        if not text:
            return
        self._run_all(text=text, fresh=self.fresh_check.isChecked())
        # Keep the question, selected: asking again (answers vary from run to
        # run) is one key, and typing replaces it.
        self.question_box.selectAll()

    def open_new_chats(self):
        """Open a new chat in all three columns (private if switched on)."""
        self._run_all(text=None, fresh=True)

    def _on_private_toggled(self, on):
        self.private_btn.setText("🕶 Private chats: On" if on else "🕶 Private chats: Off")
        if on:
            self.open_new_chats()
        else:
            # Leave open chats alone: a temporary chat is lost once you leave it.
            self.ask_status.setText(
                "Private chats off: new chats are ordinary ones again. The chats "
                "already open stay as they are.")

    def _run_all(self, text, fresh):
        self._batch += 1
        batch = self._batch
        self._results = {}
        private = self.private_btn.isChecked()
        if text:
            self.ask_status.setText("Asking all three…")
        else:
            self.ask_status.setText("Opening new private chats…" if private
                                    else "Opening new chats…")
        for column in self.chat_columns:
            column.run(text=text, fresh=fresh, private=private,
                       on_done=lambda title, ok, message, b=batch, t=text:
                           self._column_done(b, t, title, ok))

    def _column_done(self, batch, text, title, ok):
        if batch != self._batch:
            return
        self._results[title] = ok
        if len(self._results) < len(self.chat_columns):
            return
        failed = [column.title for column in self.chat_columns
                  if not self._results.get(column.title)]
        where = " and ".join(failed)
        if text is None:
            if failed:
                self.ask_status.setText(f"⚠ {where}: see the note at the top of the column.")
            elif self.private_btn.isChecked():
                # Not "not saved": two of the three can't be confirmed from
                # here, and the column notes say which.
                self.ask_status.setText("Private chats opened in all three. Check "
                                        "each site shows its private-chat sign.")
            else:
                self.ask_status.setText("New chats open in all three.")
        elif failed:
            QApplication.clipboard().setText(text)
            sent = len(self.chat_columns) - len(failed)
            self.ask_status.setText(
                f"⚠ Sent to {sent} of {len(self.chat_columns)}. {where}: see the "
                f"note at the top of the column. Your question is on the clipboard "
                f"to paste in by hand (Ctrl+V).")
        else:
            self.ask_status.setText("✓ Sent to all three.")

    def cleanup(self):
        """Clean up all web engine resources before widget deletion"""
        try:
            for column in self.chat_columns:
                column.cleanup()
        except:
            pass


# ============================================================================
# STANDALONE USAGE
# ============================================================================

if __name__ == "__main__":
    import sys
    from PyQt6.QtWidgets import QMainWindow

    app = QApplication(sys.argv)
    app.setApplicationName("Superbrowser")

    # Create main window for testing
    window = QMainWindow()
    window.setWindowTitle("Superbrowser - Multi-Chat AI Browser")
    window.setGeometry(100, 100, 1800, 1000)

    # Create and set central widget
    browser = SuperbrowserWidget()
    window.setCentralWidget(browser)

    window.show()
    sys.exit(app.exec())
