"""
Trados Bridge Client
====================

Client for the Supervertaler Bridge (a localhost HTTP API exposed by the
Supervertaler for Trados plugin – see Core/SupervertalerBridge.cs in that repo).

Discovery flow:
  1. Resolve the shared user-data root (same `%APPDATA%\\Supervertaler\\config.json`
     pointer that llm_clients.load_api_keys() reads).
  2. Look for the handshake file at  <root>/trados/runtime/bridge.json
     written by the plugin while it's running.  Contains:
         {version, port, token, pid, startedAt}
  3. Cache the handshake until the file's mtime changes.

Liveness:
  We don't bother with PID liveness checks – on every call we just attempt
  the HTTP request with a short timeout.  Connection refused / timeout is
  fast on localhost (~1 ms) and is the only reliable signal; a stale
  handshake file from a hard kill simply produces a clean failure.

API:
  client.is_available()                        -> bool   (cheap network check)
  client.fetch_active_context()                -> dict | None
  client.insert_translation(text: str)         -> (ok: bool, error: str | None)

All methods are synchronous and safe to call from a Qt UI thread because
they always use very short HTTP timeouts (default 1.5 s).  Callers who
need fully async behaviour should drive this from a worker thread.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Optional, Tuple, Dict, Any

try:
    import requests
except ImportError:  # pragma: no cover – Workbench already depends on requests
    requests = None  # type: ignore


# ── Path resolution ────────────────────────────────────────────────────────


def _resolve_user_data_root() -> Path:
    """
    Mirror the resolution rules used by ``llm_clients.load_api_keys`` so this
    client always looks where the plugin is actually writing.  Falls back to
    ``~/Supervertaler`` when no pointer file exists.
    """
    # Pointer file location is OS-specific
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


def _bridge_handshake_path() -> Path:
    """Absolute path to the handshake file the plugin writes."""
    return _resolve_user_data_root() / "trados" / "runtime" / "bridge.json"


# ── Client ─────────────────────────────────────────────────────────────────


class TradosBridgeClient:
    """
    Singleton-friendly client.  One instance per client is fine; the
    handshake parse is cheap (cached against mtime) and there's no per-instance
    network state to leak.
    """

    # How long to cache "no handshake file present" before re-checking the
    # disk.  Keeps `is_available` cheap when the user is rapidly typing.
    _MISSING_HANDSHAKE_TTL_S = 1.0

    # HTTP timeouts.  Localhost is fast; if these are hit, the bridge is sick.
    _HTTP_TIMEOUT_AVAILABILITY = 0.5
    _HTTP_TIMEOUT_GET_CONTEXT = 1.5
    _HTTP_TIMEOUT_INSERT = 2.0

    # Process-wide singleton.  Multiple call sites in the Workbench
    # (each ChatViewWidget plus the per-send context-aware path) used to
    # construct their own TradosBridgeClient and each ran its own HTTP
    # probe on the main thread, producing ~35% main-thread CPU when the
    # bridge was unreachable.  Sharing one client + one connection pool
    # collapses that to a single off-main-thread probe driven by
    # TradosBridgePoller below.
    _shared_instance: Optional["TradosBridgeClient"] = None

    @classmethod
    def shared(cls) -> "TradosBridgeClient":
        """Return the process-wide TradosBridgeClient singleton."""
        if cls._shared_instance is None:
            cls._shared_instance = cls()
        return cls._shared_instance

    def __init__(self) -> None:
        self._handshake: Optional[Dict[str, Any]] = None
        self._handshake_mtime: float = 0.0
        self._last_missing_check: float = 0.0

        # Reused HTTP session so the localhost probe doesn't open a fresh
        # TCP socket on every call (visible in py-spy as the
        # _new_conn → create_connection → endheaders chain).
        self._session = requests.Session() if requests is not None else None

        # Cached availability is updated by probe_blocking() (which is
        # safe to call from any thread).  is_available() returns this
        # value instantly so callers on the UI thread never pay HTTP
        # latency for what is logically a "did we already know?" check.
        self._cached_available: bool = False

    # ── Discovery ─────────────────────────────────────────────────────

    def _load_handshake(self) -> Optional[Dict[str, Any]]:
        """
        Returns the parsed handshake dict, or None if no usable handshake
        is on disk.  Caches the parsed value against the file's mtime so
        repeated calls during typing don't re-parse the file.
        """
        path = _bridge_handshake_path()

        try:
            stat = path.stat()
        except FileNotFoundError:
            # Cache "no file" for a short window so polling stays cheap.
            now = time.monotonic()
            if now - self._last_missing_check < self._MISSING_HANDSHAKE_TTL_S:
                return None
            self._last_missing_check = now
            self._handshake = None
            self._handshake_mtime = 0.0
            return None
        except OSError:
            return None

        if self._handshake is not None and stat.st_mtime == self._handshake_mtime:
            return self._handshake

        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            self._handshake = None
            self._handshake_mtime = 0.0
            return None

        # Schema sanity check – ignore handshakes from a future protocol
        # version we don't understand.
        if not isinstance(data, dict) or data.get("version") != 1:
            self._handshake = None
            self._handshake_mtime = 0.0
            return None
        if not data.get("port") or not data.get("token"):
            self._handshake = None
            self._handshake_mtime = 0.0
            return None

        self._handshake = data
        self._handshake_mtime = stat.st_mtime
        return data

    # ── Availability ──────────────────────────────────────────────────

    def is_available(self) -> bool:
        """
        Returns the cached availability flag — never blocks.  The flag is
        kept fresh by ``probe_blocking()`` running on a worker thread
        (see TradosBridgePoller).  Callers on the UI thread therefore pay
        zero HTTP cost; they only ever see what the last background probe
        observed.
        """
        return self._cached_available

    def probe_blocking(self) -> bool:
        """
        Perform a synchronous HTTP probe against the bridge and update
        the cached availability flag.  Safe to call from any thread.
        Intended to be driven from a worker thread by TradosBridgePoller
        so the UI thread never pays the cost.

        Returns the new availability state.
        """
        if self._session is None:
            self._cached_available = False
            return False

        hs = self._load_handshake()
        if hs is None:
            self._cached_available = False
            return False

        # Use a no-route GET as a cheap liveness probe – the bridge will
        # respond with 404 (= alive but path unknown) or 401 (= alive but
        # auth missing).  Either is fine for "is it up?".
        url = f"http://127.0.0.1:{hs['port']}/_ping"
        try:
            r = self._session.get(url, timeout=self._HTTP_TIMEOUT_AVAILABILITY)
            # Any HTTP response (including 401/404) means the bridge is alive.
            self._cached_available = r.status_code != 0
            return self._cached_available
        except Exception:
            # Connection refused, timeout, etc. – bridge not reachable.
            # Drop the cached handshake so a fresh start is detected on the
            # next poll without waiting for the mtime cache to expire.
            self._handshake = None
            self._handshake_mtime = 0.0
            self._cached_available = False
            return False

    # ── Endpoints ─────────────────────────────────────────────────────

    def fetch_active_context(self) -> Optional[Dict[str, Any]]:
        """
        GET /v1/active-context.  Returns the JSON body as a dict, or None
        if the bridge isn't reachable / didn't respond cleanly.
        """
        if self._session is None:
            return None
        hs = self._load_handshake()
        if hs is None:
            return None
        url = f"http://127.0.0.1:{hs['port']}/v1/active-context"
        headers = {"Authorization": f"Bearer {hs['token']}"}
        try:
            r = self._session.get(url, headers=headers, timeout=self._HTTP_TIMEOUT_GET_CONTEXT)
            if r.status_code == 200:
                return r.json()
            return None
        except Exception:
            return None

    def insert_translation(self, text: str) -> Tuple[bool, Optional[str]]:
        """
        POST /v1/insert-translation.  Returns (ok, error_message_or_None).
        """
        if self._session is None:
            return False, "Python `requests` library not installed"
        if not text:
            return False, "empty text"
        hs = self._load_handshake()
        if hs is None:
            return False, "Trados plugin not detected (no handshake file)"
        url = f"http://127.0.0.1:{hs['port']}/v1/insert-translation"
        headers = {
            "Authorization": f"Bearer {hs['token']}",
            "Content-Type": "application/json; charset=utf-8",
        }
        try:
            r = self._session.post(url, headers=headers, data=json.dumps({"text": text}).encode("utf-8"),
                              timeout=self._HTTP_TIMEOUT_INSERT)
            if r.status_code == 200:
                return True, None
            try:
                body = r.json()
                err = body.get("error") or f"HTTP {r.status_code}"
            except Exception:
                err = f"HTTP {r.status_code}"
            return False, err
        except Exception as e:
            return False, str(e)


# ── Prompt formatting ──────────────────────────────────────────────────────


def format_context_for_prompt(ctx: Dict[str, Any]) -> str:
    """
    Convert a `/v1/active-context` snapshot into a plain-text block suitable
    for prepending to the chat system prompt.  Mirrors the shape of the
    in-Trados ChatPrompt builder so answer quality is comparable.

    Returns "" if the snapshot indicates no active document or no usable
    context.
    """
    if not isinstance(ctx, dict) or not ctx.get("available"):
        return ""

    lines = []
    lines.append("# TRADOS PROJECT CONTEXT")
    lines.append("The user is currently translating in Trados Studio. The following context "
                 "describes the active project and segment. Use this to ground your answer "
                 "in the user's real work.")
    lines.append("")

    project = ctx.get("project") or {}
    if project:
        lines.append("## Project")
        if project.get("name"):
            lines.append(f"- Name: {project['name']}")
        if project.get("fileName"):
            lines.append(f"- File: {project['fileName']}")
        src = project.get("sourceLang")
        tgt = project.get("targetLang")
        if src and tgt:
            lines.append(f"- Language pair: {src} → {tgt}")
        elif src:
            lines.append(f"- Source language: {src}")
        elif tgt:
            lines.append(f"- Target language: {tgt}")
        lines.append("")

    seg = ctx.get("activeSegment") or {}
    if seg:
        lines.append("## Active segment")
        if seg.get("source"):
            lines.append(f"- Source: {seg['source']}")
        if seg.get("target"):
            lines.append(f"- Current target draft: {seg['target']}")
        else:
            lines.append("- Current target draft: (empty)")
        lines.append("")

    surrounding = ctx.get("surroundingSegments") or []
    if surrounding:
        lines.append("## Surrounding segments (immediate context)")
        for s in surrounding:
            src = s.get("source") or ""
            tgt = s.get("target") or ""
            if tgt:
                lines.append(f"- {src}  →  {tgt}")
            else:
                lines.append(f"- {src}  →  (untranslated)")
        lines.append("")

    tm_matches = ctx.get("tmMatches") or []
    if tm_matches:
        lines.append("## Translation Memory matches for the active segment")
        for m in tm_matches:
            score = m.get("score")
            src = m.get("source") or ""
            tgt = m.get("target") or ""
            tm_name = m.get("tmName")
            label = f"{score}%" if score is not None else "?"
            suffix = f"  [{tm_name}]" if tm_name else ""
            lines.append(f"- ({label}) {src}  →  {tgt}{suffix}")
        lines.append("")

    termbase_hits = ctx.get("termbaseHits") or []
    if termbase_hits:
        lines.append("## Termbase hits for the active segment")
        for t in termbase_hits:
            src = t.get("source") or ""
            tgt = t.get("target") or ""
            tb = t.get("termbaseName")
            extra = []
            if t.get("definition"):
                extra.append(f"def: {t['definition']}")
            if t.get("domain"):
                extra.append(f"domain: {t['domain']}")
            if t.get("notes"):
                extra.append(f"notes: {t['notes']}")
            tb_suffix = f"  [{tb}]" if tb else ""
            extras = f"  ({'; '.join(extra)})" if extra else ""
            lines.append(f"- {src}  →  {tgt}{tb_suffix}{extras}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


# ── Background poller ─────────────────────────────────────────────────────
#
# The poller is optional: only widgets that want availability change
# notifications import it.  Importing this module without Qt installed
# still works (the poller class simply isn't defined).


try:
    from PyQt6.QtCore import QObject, QRunnable, QThreadPool, QTimer, pyqtSignal
except ImportError:  # pragma: no cover – Workbench always has PyQt6
    QObject = None  # type: ignore


if QObject is not None:

    class _ProbeRunnable(QRunnable):
        """One-shot worker: probes the bridge and posts the result back."""

        def __init__(self, poller: "TradosBridgePoller") -> None:
            super().__init__()
            self._poller = poller
            self.setAutoDelete(True)

        def run(self) -> None:
            try:
                available = self._poller.client.probe_blocking()
            except Exception:
                available = False
            # Hop back to the GUI thread to emit the signal.  QTimer-from-any-
            # thread isn't safe; instead we use the poller's own
            # _result_ready signal, which is auto-queued because it's
            # connected across threads.
            self._poller._result_ready.emit(available)

    class TradosBridgePoller(QObject):
        """
        Singleton coordinator that drives a single off-main-thread probe
        of the Trados Supervertaler Bridge and broadcasts availability changes
        to any number of subscribers via ``availability_changed``.

        Replaces the previous "every ChatViewWidget runs its own 3 s
        QTimer that calls is_available() on the main thread" pattern,
        which py-spy showed consuming ~35% of MainThread CPU.

        Backoff: probes the bridge every 3 s while available.  When the
        bridge becomes unreachable, the interval ramps to 10, 30, then
        60 s so a missing-Trados scenario costs almost nothing.  Resets
        to 3 s the instant the bridge comes back.
        """

        # Emitted when availability changes (False ↔ True).  Subscribers
        # also receive the initial state via ``current_state()`` on hookup.
        availability_changed = pyqtSignal(bool)

        # Emitted when any widget toggles the user's Trados chip
        # preference, so sibling widgets re-render their chip without
        # having to wait for an availability transition.  Carries no
        # payload — subscribers re-read the pref themselves.
        pref_changed = pyqtSignal()

        # Internal — used by _ProbeRunnable to marshal the probe result
        # from the worker thread back to the GUI thread.
        _result_ready = pyqtSignal(bool)

        _BACKOFF_SCHEDULE_MS = (3000, 10000, 30000, 60000)

        _shared_instance: Optional["TradosBridgePoller"] = None

        @classmethod
        def shared(cls) -> "TradosBridgePoller":
            """Return the process-wide poller singleton.  Lazily started."""
            if cls._shared_instance is None:
                cls._shared_instance = cls()
                cls._shared_instance.start()
            return cls._shared_instance

        def __init__(self, parent: Optional["QObject"] = None) -> None:
            super().__init__(parent)
            self.client = TradosBridgeClient.shared()
            self._consecutive_failures = 0
            self._probe_in_flight = False
            # Track the last value we broadcast so we can detect
            # transitions independently of the failure counter.  Starts
            # as None so the first probe always emits an initial state.
            self._last_emitted: Optional[bool] = None

            self._timer = QTimer(self)
            self._timer.setSingleShot(True)
            self._timer.timeout.connect(self._kick_probe)

            self._result_ready.connect(self._on_probe_result)

        def current_state(self) -> bool:
            """Most recently observed availability (cached, instant)."""
            return self.client.is_available()

        def start(self) -> None:
            """Kick off the first probe immediately."""
            # Fire-and-forget: don't wait, don't block; the result hops
            # back via _result_ready when it's ready.
            QTimer.singleShot(0, self._kick_probe)

        def stop(self) -> None:
            """Stop scheduling further probes.  An in-flight probe still completes."""
            self._timer.stop()

        def notify_pref_changed(self) -> None:
            """
            Call from a widget after the user toggles the Trados chip
            preference so sibling chat views re-render their own chip
            without waiting for an availability transition.
            """
            self.pref_changed.emit()

        def _kick_probe(self) -> None:
            if self._probe_in_flight:
                # The previous probe is still going (e.g. the bridge is
                # hanging through a TCP timeout).  Skip this tick; the
                # result handler will reschedule when it lands.
                return
            self._probe_in_flight = True
            QThreadPool.globalInstance().start(_ProbeRunnable(self))

        def _on_probe_result(self, available: bool) -> None:
            self._probe_in_flight = False

            if available:
                self._consecutive_failures = 0
            else:
                self._consecutive_failures += 1

            # Emit only on actual transitions (plus once at startup) so
            # subscribers don't see a spam of identical-state signals.
            if self._last_emitted is None or available != self._last_emitted:
                self._last_emitted = available
                self.availability_changed.emit(available)

            # Schedule the next probe with backoff.
            if available:
                next_interval = self._BACKOFF_SCHEDULE_MS[0]
            else:
                idx = min(self._consecutive_failures - 1,
                          len(self._BACKOFF_SCHEDULE_MS) - 1)
                next_interval = self._BACKOFF_SCHEDULE_MS[idx]
            self._timer.start(next_interval)
