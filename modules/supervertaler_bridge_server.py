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

  GET /v1/tools and the read-only tool paths it lists (modules/bridge_tools.py)
       For the Supervertaler MCP Server, which lets a desktop chat agent
       (Claude Desktop, ChatGPT Desktop) look at the open project. The MCP
       server finds this Workbench through a second, per-process handshake:
       ``<root>/workbench/runtime/instances/bridge-<pid>.json``. One file per
       process, so two Workbench windows do not overwrite each other and
       closing one leaves the other reachable. A tool call runs on the GUI
       thread and the HTTP thread waits for its answer (``call_tool``).

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
from concurrent.futures import Future, TimeoutError as FutureTimeout
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Callable, Dict, Optional, Tuple
from urllib.parse import parse_qs, urlsplit

from PyQt6.QtCore import QObject, pyqtSignal

from modules import bridge_tools


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


def _instances_dir() -> Path:
    return _runtime_dir() / "instances"


def _instance_handshake_path() -> Path:
    return _instances_dir() / f"bridge-{os.getpid()}.json"


def _write_json_atomically(path: Path, payload: dict) -> None:
    """Write to a temp file then rename, so a reader never sees half a file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _remove_stale_instance_handshakes() -> None:
    """Delete instance handshakes whose Workbench is gone. ``stop()`` removes
    our own, but a crash or a killed process leaves its file behind for good;
    without this the folder grows by one file per crash. Never raises."""
    try:
        import psutil
        for path in _instances_dir().glob("bridge-*.json"):
            try:
                pid = int(path.stem.split("-", 1)[1])
            except ValueError:
                continue
            if pid != os.getpid() and not psutil.pid_exists(pid):
                try:
                    path.unlink()
                    _log(f"removed stale instance handshake {path.name}")
                except OSError:
                    pass
    except Exception as exc:
        _log(f"stale handshake sweep skipped: {exc}")


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

    # Requests are handled one at a time, so a client that connects and then
    # sends nothing would block every later call. Give up on it instead.
    timeout = 30

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
            tool = bridge_tools.POST_ROUTES.get(self.path)
            if self.path != "/v1/run-prompt" and tool is None:
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
            if not isinstance(data, dict):
                self._send_json(400, {"ok": False, "error": "body is not a JSON object"})
                return

            if tool is not None:
                # Tools read their parameters as strings, as from a query string.
                params = {k: v if isinstance(v, str) else json.dumps(v) for k, v in data.items()}
                bridge = self.server.bridge  # type: ignore[attr-defined]
                status, payload = bridge.call_tool(tool, params)
                self._send_json(status, payload)
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
        try:
            url = urlsplit(self.path)

            # Health check – useful for clients that want to verify the bridge
            # is alive before posting. No auth required because the response
            # contains no privileged information.
            if url.path == "/v1/ping":
                self._send_json(200, {"ok": True})
                return

            if not self._check_auth():
                return

            if url.path == "/v1/tools":
                self._send_json(200, bridge_tools.registry())
                return

            handler = bridge_tools.ROUTES.get(url.path)
            if handler is None:
                self._send_json(404, {"ok": False, "error": "unknown endpoint"})
                return

            params = {k: v[-1] for k, v in parse_qs(url.query, keep_blank_values=True).items()}
            bridge: SupervertalerBridgeServer = self.server.bridge  # type: ignore[attr-defined]
            status, payload = bridge.call_tool(handler, params)
            self._send_json(status, payload)
        except Exception as exc:
            _log(f"do_GET threw: {type(exc).__name__}: {exc}")
            try:
                self._send_json(500, {"ok": False, "error": str(exc)})
            except Exception:
                pass


# ── Bridge ─────────────────────────────────────────────────────────────────


class SupervertalerBridgeServer(QObject):
    """Owns the HTTP listener thread and exposes a Qt signal that fires
    on every accepted prompt. Owners (FloatingAssistant) connect to
    ``run_prompt_requested`` to actually inject the prompt into chat.
    """

    # (prompt, displayPrompt, promptName). Cross-thread emit so the
    # default Qt::QueuedConnection marshals back to the GUI thread.
    run_prompt_requested = pyqtSignal(str, str, str)

    # (future, callable). Emitted from the HTTP thread; because this object
    # lives on the GUI thread the slot runs there. See call_tool.
    _ui_call_requested = pyqtSignal(object)

    _BIND_HOST = "127.0.0.1"
    _MAX_BIND_ATTEMPTS = 10

    # How long a tool call waits for the GUI thread. The tools themselves take
    # milliseconds; this is for a Workbench that is busy with something long
    # (an import, a batch translation that blocks the window).
    UI_CALL_TIMEOUT_SECONDS = 10.0

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self.token: str = ""
        self._port: int = 0
        self._server: Optional[HTTPServer] = None
        self._thread: Optional[threading.Thread] = None
        self._started_at: Optional[datetime] = None
        # What the instance handshake last said, so note_state only rewrites
        # the file when something in it changed.
        self._project_name: str = ""
        self._app_version: str = ""
        self._ui_call_requested.connect(self._run_ui_call)

    # ── Tool calls on the GUI thread ──────────────────────────────────

    def call_tool(self, handler: Callable, params: Dict[str, str]) -> Tuple[int, dict]:
        """Run ``handler(main window, params)`` on the GUI thread and wait for
        it. Called from the HTTP thread. Returns (HTTP status, JSON body).

        If the GUI thread does not get to it in time the call is cancelled, so
        it can never run late, after the caller was told it did not happen
        (that matters once tools write; today they only read).
        """
        future: Future = Future()
        self._ui_call_requested.emit((future, lambda: handler(self.parent(), params)))
        try:
            try:
                result = future.result(timeout=self.UI_CALL_TIMEOUT_SECONDS)
            except FutureTimeout:
                if future.cancel():
                    return 503, {"ok": False, "error":
                                 "Supervertaler Workbench is busy and did not answer in time. "
                                 "Wait until it has finished what it is doing, then try again."}
                # Not cancellable: the GUI thread has already started on it.
                result = future.result(timeout=self.UI_CALL_TIMEOUT_SECONDS)
            # A database search: the GUI thread has handed over what it needs,
            # the search itself runs here, on the HTTP thread.
            if isinstance(result, bridge_tools.RunOffGuiThread):
                result = result.function()
            return 200, result
        except bridge_tools.BridgeToolError as exc:
            return exc.status, {"ok": False, "error": str(exc)}
        except FutureTimeout:
            return 503, {"ok": False, "error": "Supervertaler Workbench did not answer in time."}
        except Exception as exc:
            _log(f"tool call threw: {type(exc).__name__}: {exc}")
            return 500, {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    def _run_ui_call(self, request) -> None:
        future, function = request
        if not future.set_running_or_notify_cancel():
            return  # the caller gave up waiting
        try:
            future.set_result(function())
        except BaseException as exc:  # hand every failure to the waiting thread
            future.set_exception(exc)

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

        _remove_stale_instance_handshakes()
        try:
            self._write_instance_handshake()
            _log(f"instance handshake written at {_instance_handshake_path()}")
        except Exception as exc:
            _log(f"failed to write instance handshake: {exc}")

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
        # Each in its own try: failing to remove one must not leave the other.
        for handshake in (_handshake_path(), _instance_handshake_path()):
            try:
                handshake.unlink(missing_ok=True)
            except Exception:
                pass
        _log("stop() complete; handshakes removed.")

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

    def _write_instance_handshake(self) -> None:
        """The handshake the Supervertaler MCP Server discovers this Workbench
        by. Field names are the ones its BridgeClient reads; ``startedAt`` is
        UTC because it compares it with the process start time, and
        ``processName`` lets it tell a live Workbench from a reused PID."""
        _write_json_atomically(_instance_handshake_path(), {
            "version": 1,
            "port": self._port,
            "token": self.token,
            "pid": os.getpid(),
            # When the bridge started, not when this file was last rewritten:
            # the MCP server takes the newest startedAt as the newest Workbench.
            "startedAt": (self._started_at or datetime.now()).astimezone(timezone.utc).isoformat(),
            "processName": Path(sys.executable).stem,
            "pluginVersion": self._app_version,
            "projectName": self._project_name,
        })

    def note_state(self, project_name: str, app_version: str) -> None:
        """Tell the bridge which project is open, so the instance handshake
        names it (that is how two Workbench windows are told apart). Cheap to
        call often: the file is rewritten only when something changed."""
        if (project_name, app_version) == (self._project_name, self._app_version):
            return
        self._project_name, self._app_version = project_name, app_version
        if self._server is None:
            return
        try:
            self._write_instance_handshake()
        except Exception as exc:
            _log(f"failed to update instance handshake: {exc}")

    # ── Diagnostics ───────────────────────────────────────────────────

    @property
    def port(self) -> int:
        return self._port

    @property
    def is_running(self) -> bool:
        return self._server is not None
