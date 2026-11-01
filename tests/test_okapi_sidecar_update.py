"""Okapi sidecar: pip users actually get a new sidecar version.

Users who installed Workbench with pip have the sidecar downloaded into
their user folder, stamped with the version Workbench expected then
(okapi-sidecar.version). When a newer Workbench expects a newer sidecar,
_ensure_okapi_sidecar() is meant to download the update before a Word
import. It never did: start-up launches the downloaded JAR before anything
checks the stamp, and _ensure_okapi_sidecar() returned at once for a running
sidecar, so the update was never reached. (The download URLs are pinned in
test_okapi_sidecar_urls.py.)

The behaviour tests import Supervertaler.py (about 20 s) and run the real
_ensure_okapi_sidecar against a stand-in window object.
"""

from __future__ import annotations

import os
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from modules.okapi_sidecar import OkapiSidecar  # noqa: E402


@pytest.fixture(scope="module")
def S():
    from PyQt6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    saved = sys.stdout, sys.stderr
    import Supervertaler   # re-wraps stdout/stderr on Windows at import
    S._wrappers = sys.stdout, sys.stderr   # keep alive: closing them closes pytest's capture
    sys.stdout, sys.stderr = saved
    return Supervertaler


@pytest.fixture
def children():
    procs = []
    yield procs
    for p in procs:                 # never leak a stand-in JVM if a test fails
        if p.poll() is None:
            p.kill()
            p.wait(5)


def _jvm(children):
    """A real child process standing in for the sidecar's JVM."""
    p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    children.append(p)
    return p


def make_window(S, tmp_path, monkeypatch, children, *, stamp, download_ok=True):
    """A user-downloaded sidecar (stamped `stamp`) that start-up has launched."""
    folder = tmp_path / "okapi-sidecar"
    folder.mkdir()
    (folder / "okapi-sidecar.jar").write_bytes(b"jar")
    (folder / "okapi-sidecar.version").write_text(stamp, encoding="utf-8")

    sc = OkapiSidecar(sidecar_dir=str(folder))
    monkeypatch.setattr(sc, "_user_data_sidecar_dir", lambda: folder)
    sc._process, sc._started_by_us = _jvm(children), True
    w = SimpleNamespace(okapi_sidecar=sc, log=lambda msg: None, tr=lambda s: s,
                        events=[], launched=sc._process, foreign=None)

    # "Running" means a JVM of ours, or someone else's, is alive.
    def is_running():
        ours = sc._process is not None and sc._process.poll() is None
        return ours or (w.foreign is not None and w.foreign.poll() is None)

    def download_install(progress_callback=None):
        w.events.append(("download", w.launched.poll() is not None))  # stale JVM gone?
        if download_ok:
            (folder / "okapi-sidecar.version").write_text(OkapiSidecar.EXPECTED_VERSION)
        return download_ok

    def start():
        w.events.append(("start",))
        sc._process, sc._started_by_us = _jvm(children), True
        return True

    monkeypatch.setattr(sc, "is_running", is_running)
    monkeypatch.setattr(sc, "download_install", download_install)
    monkeypatch.setattr(sc, "start", start)
    monkeypatch.setattr(S, "QProgressDialog", MagicMock())
    return w


def test_running_outdated_download_is_stopped_then_updated(S, tmp_path, monkeypatch, children):
    w = make_window(S, tmp_path, monkeypatch, children, stamp="0.1.8")
    assert S.SupervertalerQt._ensure_okapi_sidecar(w) is True
    # The stale JVM was stopped before the update replaced its files.
    assert w.events == [("download", True), ("start",)]
    assert w.okapi_sidecar.installed_version_hint() == OkapiSidecar.EXPECTED_VERSION
    assert w.okapi_sidecar.is_running()


def test_up_to_date_running_sidecar_is_left_alone(S, tmp_path, monkeypatch, children):
    w = make_window(S, tmp_path, monkeypatch, children, stamp=OkapiSidecar.EXPECTED_VERSION)
    assert S.SupervertalerQt._ensure_okapi_sidecar(w) is True
    assert w.events == []
    assert w.launched.poll() is None


def test_failed_update_restarts_the_existing_jar(S, tmp_path, monkeypatch, children):
    # Offline: the old JAR must come back up, or DOCX import stops working.
    w = make_window(S, tmp_path, monkeypatch, children, stamp="0.1.8", download_ok=False)
    assert S.SupervertalerQt._ensure_okapi_sidecar(w) is True
    assert w.events == [("download", True), ("start",)]
    assert w.okapi_sidecar.installed_version_hint() == "0.1.8"   # retried next time
    assert w.okapi_sidecar.is_running()


def test_sidecar_we_did_not_start_is_not_replaced(S, tmp_path, monkeypatch, children):
    # E.g. another Workbench window started it: stop() can't stop it, so
    # replacing its files would pull them out from under a running JVM.
    w = make_window(S, tmp_path, monkeypatch, children, stamp="0.1.8")
    w.okapi_sidecar._process, w.okapi_sidecar._started_by_us = None, False
    w.foreign = _jvm(children)
    assert S.SupervertalerQt._ensure_okapi_sidecar(w) is True
    assert w.events == []
    assert w.foreign.poll() is None
