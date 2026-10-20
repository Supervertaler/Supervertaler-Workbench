"""
Supervertaler Okapi Sidecar Client
===================================

Manages the lifecycle of the local Okapi sidecar process and provides
a clean Python API for document extraction, merge, TMX handling, and
SRX segmentation.

The sidecar is a small Java-based REST service (okapi-sidecar.jar) that
wraps Okapi Framework filters.  It runs on localhost and communicates
via HTTP – no files ever leave the user's machine.

Usage:
    sidecar = OkapiSidecar()
    sidecar.start()                         # starts Java process
    result = sidecar.extract_docx(path)     # extract segments
    sidecar.stop()                          # kill Java process

    # Or use as a context manager:
    with OkapiSidecar() as sidecar:
        result = sidecar.extract_docx(path)
"""

import atexit
import json
import logging
import os
import platform
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    import requests
except ImportError:
    requests = None  # Will fail gracefully at runtime

logger = logging.getLogger(__name__)


class OkapiSidecarError(Exception):
    """Raised when the sidecar returns an error or is unreachable."""
    pass


class OkapiSidecar:
    """
    Client for the Supervertaler Okapi Sidecar REST service.

    The sidecar runs as a local subprocess on 127.0.0.1 (never exposed
    to the network).  All document processing happens on the user's
    machine – nothing is sent over the internet.
    """

    DEFAULT_PORT = 8090
    STARTUP_TIMEOUT = 20  # seconds
    SHUTDOWN_TIMEOUT = 5  # seconds

    # Sidecar version this client expects to talk to. If a sidecar from
    # a previous session is still running on the port with a different
    # version, start() will ask it to shut down and spawn a fresh one.
    # Bump this whenever the sidecar JAR is rebuilt with meaningful
    # changes (keep it in sync with pom.xml / App.java).
    EXPECTED_VERSION = "0.1.9"

    # URLs for lazy-downloading the sidecar when running from a pip
    # install (no JAR + JRE bundled next to the application). Pinned to
    # specific GitHub release assets for reproducibility – bump these
    # whenever the sidecar JAR version changes.
    #
    # The Windows asset is a ZIP that extracts to a folder containing
    # the JAR + a bundled Windows JRE, so Windows pip users don't need
    # to install Java themselves.
    #
    # The cross-platform JAR-only asset is used on macOS (Intel and Apple
    # Silicon) and Linux. Those users must have a system Java available
    # (JAVA_HOME or `java` on PATH). We intentionally don't ship
    # per-platform JRE bundles for them — see INSTALLER_URL_JAR_ONLY below.
    INSTALLER_URL_WINDOWS = (
        "https://github.com/Supervertaler/Supervertaler-Workbench/"
        "releases/download/v1.10.223/okapi-sidecar-windows-v0.1.8.zip"
    )
    # All non-Windows platforms (macOS — Intel and Apple Silicon — and
    # Linux) use the cross-platform JAR-only asset with the user's system
    # Java. We deliberately do NOT ship per-platform JRE bundles for them:
    # a macOS-arm64 bundle would need an arm64 JRE built on a Mac, so every
    # sidecar update would require a Mac. JAR-only keeps sidecar releases
    # buildable from any OS. (The macOS desktop .app still bundles its own
    # JRE at build time — that's separate from this lazy-download path.)
    INSTALLER_URL_JAR_ONLY = (
        "https://github.com/Supervertaler/Supervertaler-Workbench/"
        "releases/download/v1.10.223/okapi-sidecar-v0.1.8.jar"
    )

    def __init__(self, port: int = DEFAULT_PORT,
                 sidecar_dir: Optional[str] = None):
        """
        Args:
            port:        TCP port for the sidecar (default 8090).
            sidecar_dir: Path to the directory containing the sidecar JAR
                         and bundled JRE.  If None, auto-detected relative
                         to the Supervertaler installation.
        """
        self.port = port
        self.base_url = f"http://127.0.0.1:{port}"
        self._process: Optional[subprocess.Popen] = None
        self._started_by_us = False
        # Set by start(): True when the JAR that came up reports a version other
        # than EXPECTED_VERSION. Used by the app to decide whether to refresh a
        # lazy-downloaded JAR (see needs_update()).
        self.version_mismatch = False

        # Locate the sidecar directory
        if sidecar_dir:
            self.sidecar_dir = Path(sidecar_dir)
        else:
            self.sidecar_dir = self._find_sidecar_dir()

    # ═══════════════════════════════════════════════════════════════
    #  Lifecycle
    # ═══════════════════════════════════════════════════════════════

    def start(self) -> bool:
        """Start the sidecar process.  Returns True if started (or
        already running), False if the sidecar is not available."""

        if requests is None:
            logger.error("'requests' package not installed – "
                         "cannot communicate with Okapi sidecar")
            return False

        # Check if already running (e.g. started by a previous session)
        if self.is_running():
            running_version = self.get_version()
            if running_version == self.EXPECTED_VERSION:
                logger.info(
                    "Okapi sidecar v%s already running on port %d – reusing",
                    running_version, self.port,
                )
                return True

            # Version mismatch – almost certainly a stale sidecar from
            # before a JAR rebuild. Ask it to exit and spawn a fresh one
            # so the new bytecode actually takes effect.
            logger.info(
                "Sidecar version mismatch on port %d (running=%s, expected=%s)"
                " – restarting", self.port,
                running_version or "unknown", self.EXPECTED_VERSION,
            )
            self._stop_foreign_sidecar()

            # If we couldn't free the port, fall back to using whatever's
            # there. We deliberately don't loop – if the JAR on disk also
            # reports the wrong version (e.g. rebuild not run yet), this
            # avoids thrashing kill/respawn forever.
            if self.is_running():
                logger.warning(
                    "Couldn't restart stale sidecar on port %d – continuing"
                    " with the existing process. Rebuild the sidecar JAR"
                    " (cd okapi-sidecar && bash build.sh) if you expected"
                    " new behaviour.", self.port,
                )
                return True
            # Otherwise fall through to the spawn block.

        # Find the JAR
        jar_path = self.sidecar_dir / "okapi-sidecar.jar"
        if not jar_path.exists():
            logger.warning("Okapi sidecar JAR not found at %s", jar_path)
            return False

        # Find Java
        java_path = self._find_java()
        if java_path is None:
            logger.warning("Java runtime not found – Okapi sidecar unavailable")
            return False

        # Launch the process
        cmd = [
            str(java_path),
            "-jar", str(jar_path),
            f"--port={self.port}"
        ]
        logger.info("Starting Okapi sidecar: %s", " ".join(cmd))

        try:
            self._process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                cwd=str(self.sidecar_dir),
                # Don't let the child inherit our console on Windows
                creationflags=(subprocess.CREATE_NO_WINDOW
                               if platform.system() == "Windows" else 0),
            )
            self._started_by_us = True
            # Drain the merged stdout/stderr pipe in a daemon thread.
            # Without this the OS pipe buffer (~64 KB on Windows) eventually
            # fills and the sidecar blocks on its next write – a latent
            # hang that's hard to diagnose without a console.
            if self._process.stdout is not None:
                threading.Thread(
                    target=self._drain_pipe,
                    args=(self._process.stdout,),
                    name="okapi-stdout-drain",
                    daemon=True,
                ).start()
            atexit.register(self.stop)
        except Exception as e:
            logger.error("Failed to start Okapi sidecar: %s", e)
            return False

        # Wait for the service to become ready
        if not self._wait_for_ready():
            logger.error("Okapi sidecar failed to start within %ds",
                         self.STARTUP_TIMEOUT)
            self.stop()
            return False

        logger.info("Okapi sidecar started on port %d (PID %d)",
                     self.port, self._process.pid)

        # Sanity check: confirm the freshly spawned sidecar matches the
        # version this Python client expects. If not, the JAR on disk is
        # out of date and the developer needs to rebuild it.
        actual_version = self.get_version()
        self.version_mismatch = bool(actual_version and actual_version != self.EXPECTED_VERSION)
        if self.version_mismatch:
            logger.warning(
                "Sidecar JAR reports v%s but client expects v%s – rebuild"
                " required (cd okapi-sidecar && bash build.sh)",
                actual_version, self.EXPECTED_VERSION,
            )
        return True

    def stop(self):
        """Stop the sidecar process if we started it."""
        if self._process and self._started_by_us:
            logger.info("Stopping Okapi sidecar (PID %d)", self._process.pid)
            self._process.terminate()
            try:
                self._process.wait(timeout=self.SHUTDOWN_TIMEOUT)
            except subprocess.TimeoutExpired:
                logger.warning("Sidecar didn't stop gracefully – killing")
                self._process.kill()
                self._process.wait(timeout=2)
            self._process = None
            self._started_by_us = False

    @staticmethod
    def _drain_pipe(pipe):
        """Read the sidecar's stdout/stderr line-by-line into the log.

        Runs on a daemon thread for the lifetime of the subprocess.
        Each line is forwarded through the stdlib ``logging`` module so
        it lands in supervertaler.log via the diagnostic-log tee.
        """
        try:
            for raw in iter(pipe.readline, b""):
                if not raw:
                    break
                try:
                    text = raw.decode("utf-8", errors="replace").rstrip()
                except Exception:
                    text = repr(raw)
                if text:
                    logger.info("[sidecar] %s", text)
        except Exception:
            pass
        finally:
            try:
                pipe.close()
            except Exception:
                pass

    def is_running(self) -> bool:
        """Check if the sidecar is responding on its health endpoint."""
        if requests is None:
            return False
        try:
            resp = requests.get(f"{self.base_url}/health", timeout=2)
            return resp.status_code == 200
        except (requests.ConnectionError, requests.Timeout):
            return False

    def get_version(self) -> Optional[str]:
        """Return the sidecar version string, or None if not running."""
        try:
            resp = requests.get(f"{self.base_url}/health", timeout=2)
            if resp.status_code == 200:
                return resp.json().get("version")
        except Exception:
            pass
        return None

    # ═══════════════════════════════════════════════════════════════
    #  Stale-sidecar handling
    # ═══════════════════════════════════════════════════════════════

    def _stop_foreign_sidecar(self) -> None:
        """Stop a sidecar we did not spawn (typically a stale JVM left
        running from a previous Supervertaler session, holding the port
        with an outdated JAR loaded into memory).

        Tries the polite POST /shutdown endpoint first; falls back to
        finding and killing whatever process is listening on the port.
        Returns once the port is free or after a few seconds of waiting.
        """
        # 1) Try polite shutdown (only present on sidecars >= 0.1.1).
        try:
            requests.post(f"{self.base_url}/shutdown", timeout=2)
        except Exception:
            pass

        # 2) Wait for the port to free up.
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if not self.is_running():
                logger.info("Stale sidecar exited cleanly")
                return
            time.sleep(0.1)

        # 3) Force-kill the process holding the port (older sidecars
        #    that don't have /shutdown end up here).
        pid = self._find_port_pid(self.port)
        if pid is None:
            logger.warning(
                "Couldn't identify the process holding port %d – "
                "the new sidecar may fail to start", self.port,
            )
            return

        logger.info("Killing stale sidecar process (PID %d)", pid)
        try:
            if platform.system() == "Windows":
                subprocess.run(
                    ["taskkill", "/F", "/PID", str(pid)],
                    capture_output=True, timeout=5,
                )
            else:
                import signal
                os.kill(pid, signal.SIGKILL)
        except Exception as e:
            logger.warning("Failed to kill PID %d: %s", pid, e)
            return

        # Give the OS a moment to release the socket.
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            if not self.is_running():
                return
            time.sleep(0.1)

    @staticmethod
    def _find_port_pid(port: int) -> Optional[int]:
        """Return the PID of the process listening on TCP `port`, or None."""
        try:
            if platform.system() == "Windows":
                # netstat -ano lists local addr, foreign addr, state, PID.
                # Look for LISTENING rows on our port.
                out = subprocess.run(
                    ["netstat", "-ano", "-p", "TCP"],
                    capture_output=True, text=True, timeout=5,
                ).stdout
                needle = f":{port}"
                for line in out.splitlines():
                    if needle not in line or "LISTENING" not in line:
                        continue
                    parts = line.split()
                    # The local-address column ends in ":<port>" – verify
                    # we matched on local, not foreign or just a substring.
                    if not any(p.endswith(needle) for p in parts):
                        continue
                    try:
                        return int(parts[-1])
                    except ValueError:
                        continue
            else:
                out = subprocess.run(
                    ["lsof", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"],
                    capture_output=True, text=True, timeout=5,
                ).stdout.strip()
                if out:
                    return int(out.splitlines()[0])
        except Exception as e:
            logger.debug("Port-PID lookup failed: %s", e)
        return None

    # Context manager support
    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop()

    # ═══════════════════════════════════════════════════════════════
    #  Document extraction
    # ═══════════════════════════════════════════════════════════════

    def extract(self, file_path: str,
                source_lang: str = "en",
                target_lang: str = "fr",
                segment: bool = True,
                options: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Extract segments from a document file.

        Args:
            file_path:   Path to the document (DOCX, XLSX, HTML, etc.)
            source_lang: Source language code (e.g., "nl", "en")
            target_lang: Target language code (e.g., "en", "fr")
            segment:     Apply SRX segmentation (default True)
            options:     Optional per-file-type import toggles (dict of
                         booleans, e.g. {"word_comments": False,
                         "word_hidden": False}). Absent keys fall back to
                         the sidecar's Supervertaler defaults. See
                         FilterService.applyFilterParameters for the keys.

        Returns:
            Dict with keys: filename, sourceLang, targetLang, filterUsed,
            textUnitCount, segmentCount, segments (list of dicts with
            id, segmentIndex, source, type, isReferent, subDocument).
        """
        file_path = Path(file_path)
        if not file_path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")

        data = {
            'source_lang': source_lang,
            'target_lang': target_lang,
            'segment': str(segment).lower(),
        }
        if options:
            data['options'] = json.dumps(options)

        with open(file_path, 'rb') as f:
            resp = requests.post(
                f"{self.base_url}/extract",
                files={'file': (file_path.name, f)},
                data=data,
                timeout=120,
            )

        return self._handle_response(resp)

    def extract_docx(self, file_path: str,
                     source_lang: str = "en",
                     target_lang: str = "fr",
                     segment: bool = True,
                     options: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Convenience alias for extract() with DOCX files."""
        return self.extract(file_path, source_lang, target_lang, segment, options)

    # ═══════════════════════════════════════════════════════════════
    #  Document merge (create translated document)
    # ═══════════════════════════════════════════════════════════════

    def merge(self, original_path: str,
              translations: List[Dict[str, Any]],
              source_lang: str = "en",
              target_lang: str = "fr",
              output_path: Optional[str] = None,
              options: Optional[Dict[str, Any]] = None) -> str:
        """
        Create a translated version of the original document.

        Args:
            original_path: Path to the original (untranslated) document.
            translations:  List of dicts with keys: id, segmentIndex,
                           translation.
            source_lang:   Source language code.
            target_lang:   Target language code.
            output_path:   Where to save the translated file.  If None,
                           auto-generates a path.
            options:       Optional per-file-type import toggles. MUST match
                           the options used at extract time so the text-unit
                           set lines up for the round-trip.

        Returns:
            Path to the saved translated document.
        """
        original = Path(original_path)
        if not original.exists():
            raise FileNotFoundError(f"Original file not found: {original}")

        if output_path is None:
            stem = original.stem
            suffix = original.suffix
            output_path = str(original.parent / f"{stem}_{target_lang}{suffix}")

        data = {
            'translations': json.dumps(translations),
            'source_lang': source_lang,
            'target_lang': target_lang,
        }
        if options:
            data['options'] = json.dumps(options)

        with open(original, 'rb') as f:
            resp = requests.post(
                f"{self.base_url}/merge",
                files={'original': (original.name, f)},
                data=data,
                timeout=120,
            )

        if resp.status_code != 200:
            self._handle_response(resp)  # will raise

        # Save the binary response
        with open(output_path, 'wb') as out:
            out.write(resp.content)

        logger.info("Merged document saved to %s", output_path)
        return output_path

    # ═══════════════════════════════════════════════════════════════
    #  TMX handling
    # ═══════════════════════════════════════════════════════════════

    def read_tmx(self, tmx_path: str) -> Dict[str, Any]:
        """
        Parse a TMX file and return all translation units.

        Returns:
            Dict with keys: filename, tuCount, translationUnits (list of
            dicts with id, source, targets {lang: text}, properties).
        """
        tmx_path = Path(tmx_path)
        if not tmx_path.exists():
            raise FileNotFoundError(f"TMX file not found: {tmx_path}")

        with open(tmx_path, 'rb') as f:
            resp = requests.post(
                f"{self.base_url}/tmx/read",
                files={'file': (tmx_path.name, f)},
                timeout=120,
            )

        return self._handle_response(resp)

    def validate_tmx(self, tmx_path: str) -> Dict[str, Any]:
        """
        Validate a TMX file and return a report of any issues.

        Returns:
            Dict with keys: filename, valid (bool), tuCount, languages,
            emptySourceCount, emptyTargetCount, issues (list of dicts
            with level, message, tuIndex).
        """
        tmx_path = Path(tmx_path)
        if not tmx_path.exists():
            raise FileNotFoundError(f"TMX file not found: {tmx_path}")

        with open(tmx_path, 'rb') as f:
            resp = requests.post(
                f"{self.base_url}/tmx/validate",
                files={'file': (tmx_path.name, f)},
                timeout=60,
            )

        return self._handle_response(resp)

    # ═══════════════════════════════════════════════════════════════
    #  SRX Segmentation
    # ═══════════════════════════════════════════════════════════════

    def segment(self, text: str, language: str = "en") -> List[str]:
        """
        Segment text using Okapi's SRX engine.

        Args:
            text:     The text to segment.
            language: Language code for segmentation rules.

        Returns:
            List of sentence-level segments.
        """
        resp = requests.post(
            f"{self.base_url}/segment",
            json={'text': text, 'language': language},
            timeout=30,
        )
        data = self._handle_response(resp)
        return data.get("segments", [])

    # ═══════════════════════════════════════════════════════════════
    #  Supported formats
    # ═══════════════════════════════════════════════════════════════

    def get_supported_filters(self) -> List[Dict[str, str]]:
        """Return list of supported file format dicts."""
        resp = requests.get(f"{self.base_url}/filters", timeout=5)
        return self._handle_response(resp)

    # ═══════════════════════════════════════════════════════════════
    #  Internal helpers
    # ═══════════════════════════════════════════════════════════════

    def _handle_response(self, resp) -> Any:
        """Parse JSON response and raise on errors."""
        if resp.status_code != 200:
            try:
                data = resp.json()
                msg = data.get("message", f"HTTP {resp.status_code}")
            except Exception:
                msg = f"HTTP {resp.status_code}: {resp.text[:500]}"
            raise OkapiSidecarError(msg)

        data = resp.json()
        if isinstance(data, dict) and data.get("error"):
            raise OkapiSidecarError(data.get("message", "Unknown error"))
        return data

    def _wait_for_ready(self) -> bool:
        """Poll the health endpoint until the sidecar is ready."""
        deadline = time.time() + self.STARTUP_TIMEOUT
        while time.time() < deadline:
            # Check if the process died
            if self._process and self._process.poll() is not None:
                logger.error("Sidecar process exited with code %d",
                             self._process.returncode)
                return False
            try:
                resp = requests.get(f"{self.base_url}/health", timeout=1)
                if resp.status_code == 200:
                    return True
            except (requests.ConnectionError, requests.Timeout):
                pass
            time.sleep(0.2)
        return False

    def _find_sidecar_dir(self) -> Path:
        """Locate the sidecar directory relative to the installation."""
        # Check several locations in order of priority:
        candidates = []

        # 1. Next to the main script / frozen exe
        if getattr(sys, 'frozen', False):
            # Running as compiled exe (PyInstaller)
            app_dir = Path(sys.executable).parent

            # PyInstaller --onedir places bundled data files under
            # _internal/ next to the exe (or inside sys._MEIPASS for
            # --onefile). Both candidates need checking before falling
            # back to the EXE-adjacent layout that source builds use.
            meipass = getattr(sys, '_MEIPASS', None)
            if meipass:
                candidates.append(Path(meipass) / "okapi-sidecar")
            candidates.append(app_dir / "_internal" / "okapi-sidecar")
        else:
            # Running from source
            app_dir = Path(__file__).parent.parent

        candidates.append(app_dir / "okapi-sidecar")
        candidates.append(app_dir / "okapi-sidecar" / "dist")
        candidates.append(app_dir / "okapi-sidecar" / "target")

        # 2. In user data directory
        if platform.system() == "Windows":
            appdata = os.environ.get("LOCALAPPDATA", "")
            if appdata:
                candidates.append(
                    Path(appdata) / "Supervertaler" / "okapi-sidecar")
        elif platform.system() == "Darwin":
            candidates.append(
                Path.home() / "Library" / "Application Support" /
                "Supervertaler" / "okapi-sidecar")
        else:
            candidates.append(
                Path.home() / ".local" / "share" /
                "supervertaler" / "okapi-sidecar")

        for candidate in candidates:
            jar = candidate / "okapi-sidecar.jar"
            if jar.exists():
                return candidate

        # Fallback: return first existing directory even without JAR
        for candidate in candidates:
            if candidate.exists():
                return candidate

        # Default to the source-tree location even if it doesn't exist yet
        return app_dir / "okapi-sidecar"

    def is_installed(self) -> bool:
        """True when the sidecar JAR can be found on disk.

        Distinct from ``is_running()`` – the JAR may exist but the JVM
        not yet be launched. ``start()`` returns False if not installed.
        """
        return (self.sidecar_dir / "okapi-sidecar.jar").exists()

    # ── Lazy-download version tracking ─────────────────────────────
    # A small ".version" stamp written next to a lazy-downloaded JAR lets us
    # detect — without launching Java — that a cached JAR from an older app
    # version is now out of date, so we can refresh it automatically instead
    # of warning forever.

    def _version_stamp_path(self) -> Path:
        return self.sidecar_dir / "okapi-sidecar.version"

    def _write_version_stamp(self, target_dir: Path) -> None:
        try:
            (target_dir / "okapi-sidecar.version").write_text(
                self.EXPECTED_VERSION, encoding="utf-8")
        except Exception as e:
            logger.warning("Could not write sidecar version stamp: %s", e)

    def installed_version_hint(self) -> Optional[str]:
        """Best-effort version of the installed JAR from its stamp (no JVM
        launch). None if there's no stamp (e.g. a pre-stamp download)."""
        try:
            p = self._version_stamp_path()
            if p.exists():
                return p.read_text(encoding="utf-8").strip() or None
        except Exception:
            pass
        return None

    def is_user_downloaded(self) -> bool:
        """True when the active JAR is the per-user lazy-downloaded copy (as
        opposed to a dev source build or a bundled-exe install, which are
        managed manually and must not be auto-overwritten)."""
        ud = self._user_data_sidecar_dir()
        try:
            return (ud is not None and self.is_installed()
                    and self.sidecar_dir.resolve() == ud.resolve())
        except Exception:
            return False

    def needs_update(self) -> bool:
        """True when the active JAR is a lazy-downloaded copy whose version is
        not the one this client expects, so it should be re-downloaded. Always
        False for dev/source builds and bundled installs. A downloaded JAR with
        no version stamp (downloaded before stamping existed) is treated as
        stale so it gets refreshed once."""
        if not self.is_user_downloaded():
            return False
        return self.installed_version_hint() != self.EXPECTED_VERSION

    def _user_data_sidecar_dir(self) -> Optional[Path]:
        """Where lazy-downloaded sidecar files go. Per-user, persists
        across reinstalls. None on unsupported platforms."""
        if platform.system() == "Windows":
            appdata = os.environ.get("LOCALAPPDATA", "")
            if appdata:
                return Path(appdata) / "Supervertaler" / "okapi-sidecar"
        elif platform.system() == "Darwin":
            return (Path.home() / "Library" / "Application Support" /
                    "Supervertaler" / "okapi-sidecar")
        else:
            return (Path.home() / ".local" / "share" /
                    "supervertaler" / "okapi-sidecar")
        return None

    def download_install(self, progress_callback=None) -> bool:
        """Lazy-download the sidecar for the current platform.

        Used by pip-installed copies of Supervertaler that don't ship the
        sidecar bundled alongside the application (the desktop EXE release
        ships JAR + JRE in `_internal/okapi-sidecar/`).

        Two strategies depending on platform:

        * **Windows** – fetch a ZIP bundle containing the JAR plus a
          bundled Windows JRE, extract under the per-user install
          location. Self-contained; no system Java required.
        * **macOS / Linux** – fetch the JAR alone, drop it into the
          per-user install location. Requires the user to already have
          Java available (JAVA_HOME or `java` on PATH). Caller is expected
          to have verified this via `_find_java()` first.

        progress_callback: optional callable(bytes_done, bytes_total).

        Returns True on success. On success ``self.sidecar_dir`` is
        updated and ``start()`` can be called to launch the JVM.
        """
        if requests is None:
            logger.error("'requests' package not installed – can't download sidecar")
            return False

        target_dir = self._user_data_sidecar_dir()
        if target_dir is None:
            logger.error("Could not determine a per-user install location")
            return False

        target_dir.parent.mkdir(parents=True, exist_ok=True)

        if platform.system() == "Windows":
            return self._download_install_zip_bundle(
                self.INSTALLER_URL_WINDOWS, target_dir, progress_callback)
        else:
            # macOS (Intel or Apple Silicon) and Linux: JAR only, system
            # Java required. (No per-platform JRE bundles for these — keeps
            # sidecar updates buildable without a Mac.)
            return self._download_install_jar_only(
                self.INSTALLER_URL_JAR_ONLY, target_dir, progress_callback)

    def _download_install_zip_bundle(self, url: str, target_dir: Path,
                                     progress_callback) -> bool:
        """Fetch a ZIP containing JAR + JRE and extract it."""
        tmp_zip = target_dir.parent / "okapi-sidecar-download.zip"
        try:
            logger.info("Downloading Okapi sidecar bundle from %s", url)
            self._stream_to_file(url, tmp_zip, progress_callback)

            # Wipe any previous (possibly partial) install before extracting.
            if target_dir.exists():
                import shutil as _sh
                _sh.rmtree(target_dir, ignore_errors=True)

            import zipfile
            with zipfile.ZipFile(tmp_zip) as zf:
                zf.extractall(target_dir.parent)

            tmp_zip.unlink(missing_ok=True)

            jar = target_dir / "okapi-sidecar.jar"
            if not jar.exists():
                logger.error(
                    "Sidecar bundle did not contain okapi-sidecar.jar at %s",
                    jar)
                return False

            self.sidecar_dir = target_dir
            self._write_version_stamp(target_dir)
            logger.info("Sidecar installed to %s", target_dir)
            return True

        except Exception as e:
            logger.error("Failed to download/install sidecar bundle: %s", e)
            try:
                tmp_zip.unlink(missing_ok=True)
            except Exception:
                pass
            return False

    def _download_install_jar_only(self, url: str, target_dir: Path,
                                   progress_callback) -> bool:
        """Fetch the JAR alone (macOS/Linux). System Java required."""
        target_dir.mkdir(parents=True, exist_ok=True)
        jar_path = target_dir / "okapi-sidecar.jar"
        tmp_path = target_dir / "okapi-sidecar.jar.download"
        try:
            logger.info("Downloading Okapi sidecar JAR from %s", url)
            self._stream_to_file(url, tmp_path, progress_callback)

            # Atomic-ish replace once download completes.
            if jar_path.exists():
                jar_path.unlink()
            tmp_path.replace(jar_path)

            self.sidecar_dir = target_dir
            self._write_version_stamp(target_dir)
            logger.info("Sidecar JAR installed to %s", jar_path)
            return True

        except Exception as e:
            logger.error("Failed to download sidecar JAR: %s", e)
            try:
                tmp_path.unlink(missing_ok=True)
            except Exception:
                pass
            return False

    @staticmethod
    def _stream_to_file(url: str, dest: Path, progress_callback) -> None:
        """Stream a URL to disk, calling progress_callback if provided.

        Raises on HTTP errors or network failures. dest is overwritten.
        """
        with requests.get(url, stream=True, timeout=60) as resp:
            resp.raise_for_status()
            total = int(resp.headers.get("Content-Length", 0))
            downloaded = 0
            with open(dest, "wb") as f:
                for chunk in resp.iter_content(chunk_size=64 * 1024):
                    if not chunk:
                        continue
                    f.write(chunk)
                    downloaded += len(chunk)
                    if progress_callback:
                        try:
                            progress_callback(downloaded, total)
                        except Exception:
                            pass

    def _find_java(self) -> Optional[Path]:
        """Locate a Java runtime.  Prefers the bundled JRE."""
        # 1. Bundled JRE in the sidecar directory
        if platform.system() == "Windows":
            bundled = self.sidecar_dir / "jre" / "bin" / "java.exe"
        else:
            bundled = self.sidecar_dir / "jre" / "bin" / "java"

        if bundled.exists():
            return bundled

        # 2. JAVA_HOME environment variable
        java_home = os.environ.get("JAVA_HOME")
        if java_home:
            java_bin = Path(java_home) / "bin" / (
                "java.exe" if platform.system() == "Windows" else "java")
            if java_bin.exists():
                return java_bin

        # 3. java on PATH
        java_on_path = shutil.which("java")
        if java_on_path:
            return Path(java_on_path)

        return None

    @property
    def is_available(self) -> bool:
        """Check if the sidecar infrastructure is present (JAR + Java)."""
        jar = self.sidecar_dir / "okapi-sidecar.jar"
        return jar.exists() and self._find_java() is not None

    @staticmethod
    def platform_bundles_jre() -> bool:
        """True on platforms whose sidecar download bundles a private JRE.

        Currently only Windows (any arch). All Macs (Intel and Apple
        Silicon) and Linux use the JAR-only download and must rely on a
        system Java install.
        """
        return platform.system() == "Windows"

    def needs_system_java_install(self) -> bool:
        """True iff this platform requires the user to install Java AND
        no Java runtime is currently reachable.

        Used by the GUI to surface a proactive startup warning on Intel
        Macs and Linux, where the sidecar can't run until the user has
        installed a JDK themselves.
        """
        if self.platform_bundles_jre():
            return False
        return self._find_java() is None
