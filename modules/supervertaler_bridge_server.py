"""
Supervertaler Bridge Server (formerly "Sidekick Bridge")
========================================================

Localhost HTTP listener that lets the Supervertaler for Trados plugin
*push* a QuickLauncher prompt into Workbench's Chat panel. The inverse
of ``trados_bridge_client``: there Workbench reads context from Trados;
here Trados sends a prompt for Workbench to run.

The feature was renamed from "Sidekick Bridge" to "Supervertaler Bridge".
Only the on-disk handshake filename ``sidekick-bridge.json`` and the log
``sidekick-bridge.log`` keep the old name: they are a wire-protocol
surface that already-deployed Trados plugins look up by exactly that
name, so renaming them would break the bridge for users who update one
product but not the other. Class/module names were renamed freely.
Internally the consumer is now Workbench's Chat tab (the floating
Sidekick window was retired in v1.10.4); the prompt is handed off to
``SupervertalerQt.show_supervertaler_assistant`` instead of the
defunct ``FloatingAssistant``.

Lifecycle:

  * Started by ``Supervertaler.py`` once the main window is ready.
  * Binds to ``http://127.0.0.1:<random-port>/`` – never accepts
    non-loopback connections.
  * Generates a per-session bearer token; clients must present it as
    ``Authorization: Bearer <token>``.
  * Writes a handshake file at
    ``<root>/workbench/runtime/sidekick-bridge.json``
    with port + token + PID + timestamp so the Trados plugin can
    discover it. The filename is fixed for wire-protocol reasons.
  * Deletes the handshake on stop.

Endpoints (URL path versioned for forward compatibility):

  POST /v1/run-prompt
       body: {"prompt": "...", "displayPrompt": "...", "promptName": "..."}
       Hands the prompt off to the GUI thread via a Qt signal so the
       Chat tab actually runs it on the GUI thread.

Threading: the listener runs on a dedicated daemon thread; per-request
handlers run on whatever thread the stdlib HTTPServer hands them. We
marshal back to the GUI thread by emitting a Qt signal connected with
Qt::QueuedConnection (the default for cross-thread emissions).
"""

from __future__ import annotations

import json
import os
import secrets
import socket
import sys
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import QObject, pyqtSignal


# ── Logging ────────────────────────────────────────────────────────────────


def _log_path() -> Path:
    return _runtime_dir() / "sidekick-bridge.log"


_LOG_LOCK = threading.Lock()
_LOG_TRUNCATED_THIS_SESSION = False


def _log(message: str) -> None:
    """Append-only log file with a session-start header. Mirrors
    Core/SupervertalerBridge.cs::BridgeLog on the Trados side so users can
    diagnose discovery failures from either end.
    """
    global _LOG_TRUNCATED_THIS_SESSION
    line = f"[{datetime.now():%Y-%m-%d %H:%M:%S.%f}] {message}\n"
    with _LOG_LOCK:
        try:
            log = _log_path()
            log.parent.mkdir(parents=True, exist_ok=True)

            header: Optional[str] = None
            if not _LOG_TRUNCATED_THIS_SESSION:
                _LOG_TRUNCATED_THIS_SESSION = True
                header = (
                    f"--- Supervertaler Bridge session started at {datetime.now().isoformat()} "
                    f"(PID {os.getpid()}) ---\n"
                    f"runtime_dir = {_runtime_dir()}\n"
                    f"handshake   = {_handshake_path()}\n"
                )

            mode = "w" if header else "a"
            with open(log, mode, encoding="utf-8") as f:
                if header:
                    f.write(header)
                f.write(line)
        except Exception:  # never let logging break the caller
            pass


# ── Path resolution ────────────────────────────────────────────────────────


def _resolve_user_data_root() -> Path:
    """Read the same shared config pointer Trados writes to find the
    user-data root. Mirrors trados_bridge_client._resolve_user_data_root.
    Falls back to ``~/Supervertaler`` when no pointer is present.
    """
    if sys.platform == "win32":
        pointer = Path(os.environ.get("APPDATA", os.path.expanduser("~"))) / "Supervertaler" / "config.json"
    elif sys.platform == "darwin":
        pointer = Path.home() / "Library" / "Application Support" / "Supervertaler" / "config.json"
    else:
        xdg = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config")))
        pointer = xdg / "Supervertaler" / "config.json"

    if pointer.exists():
        try:
            data = json.loads(pointer.read_text(encoding="utf-8-sig"))   # a BOM is allowed
            configured = data.get("user_data_path")
            if configured:
                return Path(configured)
        except Exception:
            pass

    return Path.home() / "Supervertaler"


def _runtime_dir() -> Path:
    return _resolve_user_data_root() / "workbench" / "runtime"


def _handshake_path() -> Path:
    return _runtime_dir() / "sidekick-bridge.json"


# ── HTTP handler ───────────────────────────────────────────────────────────


class _BridgeRequestHandler(BaseHTTPRequestHandler):
    """One handler instance per request. Validates auth + path and hands
    payloads to the bridge instance attached to the server.
    """

    # Suppress the default per-request stderr access log – we route our
    # own messages through _log() instead. Set BaseHTTPRequestHandler's
    # log_message to a no-op.
    def log_message(self, format: str, *args) -> None:  # noqa: A003
        pass

    def _send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except Exception:
            pass

    def _check_auth(self) -> bool:
        bridge: SupervertalerBridgeServer = self.server.bridge  # type: ignore[attr-defined]
        header = self.headers.get("Authorization", "")
        prefix = "Bearer "
        if not header.startswith(prefix):
            self._send_json(401, {"ok": False, "error": "missing bearer token"})
            return False
        token = header[len(prefix):].strip()
        if not secrets.compare_digest(token, bridge.token):
            self._send_json(403, {"ok": False, "error": "bad token"})
            return False
        return True

    def do_POST(self) -> None:  # noqa: N802 (stdlib API name)
        try:
            if self.path != "/v1/run-prompt":
                self._send_json(404, {"ok": False, "error": "unknown endpoint"})
                return

            if not self._check_auth():
                return

            length = int(self.headers.get("Content-Length", "0") or "0")
            if length <= 0:
                self._send_json(400, {"ok": False, "error": "empty body"})
                return
            raw = self.rfile.read(length).decode("utf-8", errors="replace")
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                self._send_json(400, {"ok": False, "error": "body is not JSON"})
                return

            prompt = (data.get("prompt") or "").strip()
            if not prompt:
                self._send_json(400, {"ok": False, "error": "missing 'prompt'"})
                return

            display_prompt = data.get("displayPrompt") or prompt
            prompt_name = data.get("promptName") or ""

            bridge: SupervertalerBridgeServer = self.server.bridge  # type: ignore[attr-defined]
            _log(f"POST /v1/run-prompt accepted (name='{prompt_name}', "
                 f"prompt={len(prompt)} chars, displayPrompt={len(display_prompt)} chars)")
            bridge.run_prompt_requested.emit(prompt, display_prompt, prompt_name)

            self._send_json(200, {"ok": True})
        except Exception as exc:
            _log(f"do_POST threw: {type(exc).__name__}: {exc}")
            try:
                self._send_json(500, {"ok": False, "error": str(exc)})
            except Exception:
                pass

    def do_GET(self) -> None:  # noqa: N802
        # Health check – useful for clients that want to verify the bridge
        # is alive before posting. No auth required because the response
        # contains no privileged information.
        if self.path == "/v1/ping":
            self._send_json(200, {"ok": True})
            return
        self._send_json(404, {"ok": False, "error": "unknown endpoint"})


# ── Bridge ─────────────────────────────────────────────────────────────────


class SupervertalerBridgeServer(QObject):
    """Owns the HTTP listener thread and exposes a Qt signal that fires
    on every accepted prompt. Owners (FloatingAssistant) connect to
    ``run_prompt_requested`` to actually inject the prompt into chat.
    """

    # (prompt, displayPrompt, promptName). Cross-thread emit so the
    # default Qt::QueuedConnection marshals back to the GUI thread.
    run_prompt_requested = pyqtSignal(str, str, str)

    _BIND_HOST = "127.0.0.1"
    _MAX_BIND_ATTEMPTS = 10

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self.token: str = ""
        self._port: int = 0
        self._server: Optional[HTTPServer] = None
        self._thread: Optional[threading.Thread] = None
        self._started_at: Optional[datetime] = None

    # ── Lifecycle ─────────────────────────────────────────────────────

    def start(self) -> bool:
        """Bind a random high port, start the listener thread, and write
        the handshake file. Returns True on success.
        """
        if self._server is not None:
            _log("start() called but bridge already running – ignored.")
            return True

        self.token = secrets.token_hex(16)
        self._started_at = datetime.now()

        last_err: Optional[Exception] = None
        for attempt in range(1, self._MAX_BIND_ATTEMPTS + 1):
            try:
                # Port 0 → kernel picks a free high port. Re-pick on each
                # attempt in case the chosen port races something else.
                server = HTTPServer((self._BIND_HOST, 0), _BridgeRequestHandler)
                server.bridge = self  # type: ignore[attr-defined]
                self._server = server
                self._port = server.server_address[1]
                _log(f"HTTPServer bound on port {self._port} (attempt {attempt})")
                break
            except OSError as exc:
                last_err = exc
                _log(f"HTTPServer bind failed (attempt {attempt}): {exc}")
                time.sleep(0.05)
        if self._server is None:
            _log(f"HTTPServer failed to bind after {self._MAX_BIND_ATTEMPTS} attempts; "
                 f"last error: {last_err}")
            return False

        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="SupervertalerBridge",
            daemon=True,
        )
        self._thread.start()
        _log("listener thread started")

        try:
            self._write_handshake()
            _log(f"handshake file written at {_handshake_path()}")
        except Exception as exc:
            _log(f"failed to write handshake file: {exc}")

        _log(f"start() complete. Bridge live on http://{self._BIND_HOST}:{self._port}/ "
             f"with token {self.token[:8]}…")
        return True

    def stop(self) -> None:
        """Shut the listener down, delete the handshake file. Safe to call
        multiple times.
        """
        if self._server is None:
            return
        try:
            self._server.shutdown()
        except Exception:
            pass
        try:
            self._server.server_close()
        except Exception:
            pass
        self._server = None
        self._thread = None
        try:
            handshake = _handshake_path()
            if handshake.exists():
                handshake.unlink()
        except Exception:
            pass
        _log("stop() complete; handshake removed.")

    # ── Handshake ─────────────────────────────────────────────────────

    def _write_handshake(self) -> None:
        runtime = _runtime_dir()
        runtime.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "port": self._port,
            "token": self.token,
            "pid": os.getpid(),
            "startedAt": (self._started_at or datetime.now()).isoformat(),
        }
        path = _handshake_path()
        # Write to a temp file then rename so readers never see a
        # partial handshake.
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        os.replace(tmp, path)

    # ── Diagnostics ───────────────────────────────────────────────────

    @property
    def port(self) -> int:
        return self._port

    @property
    def is_running(self) -> bool:
        return self._server is not None
