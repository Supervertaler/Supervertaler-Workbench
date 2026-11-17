r"""Okapi sidecar: only this Workbench can use it.

Up to sidecar 0.1.9 the sidecar listened on every network interface on the
fixed port 8090, with no password, and saved uploads under whatever name the
request gave (so a name like "..\..\x.bat" could land outside its temp
folder). From 0.2.0 it listens on 127.0.0.1 only and wants a token on every
request but the health check. These tests cover the Workbench side: a free
port, a fresh token, the token passed in the environment (not on the command
line) and sent with every request.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import modules.okapi_sidecar as osc  # noqa: E402
from modules.okapi_sidecar import OkapiSidecar  # noqa: E402


def test_each_client_gets_its_own_free_port_and_token():
    a, b = OkapiSidecar(sidecar_dir="."), OkapiSidecar(sidecar_dir=".")
    assert a.port != 8090 and 1024 < a.port < 65536
    assert a.base_url == f"http://127.0.0.1:{a.port}"
    assert a._token and a._token != b._token
    assert len(a._token) >= 32


def test_an_explicit_port_is_still_honoured():
    assert OkapiSidecar(port=8097, sidecar_dir=".").port == 8097


def test_the_token_goes_in_the_environment_not_the_command_line(tmp_path, monkeypatch):
    (tmp_path / "okapi-sidecar.jar").write_bytes(b"")
    sc = OkapiSidecar(sidecar_dir=str(tmp_path))
    seen = {}

    class FakeProc:
        pid = 1
        stdout = None

        def poll(self):
            return None

    def fake_popen(cmd, **kw):
        seen["cmd"], seen["env"] = cmd, kw.get("env")
        return FakeProc()

    monkeypatch.setattr(osc.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(sc, "_find_java", lambda: "java")
    monkeypatch.setattr(sc, "is_running", lambda: False)
    monkeypatch.setattr(sc, "_wait_for_ready", lambda: True)
    monkeypatch.setattr(sc, "get_version", lambda: OkapiSidecar.EXPECTED_VERSION)
    monkeypatch.setattr(osc.atexit, "register", lambda f: None)
    assert sc.start()
    assert seen["env"]["SUPERVERTALER_SIDECAR_TOKEN"] == sc._token
    assert not any(sc._token in part for part in seen["cmd"])
    assert f"--port={sc.port}" in seen["cmd"]


def test_every_request_but_health_carries_the_token(tmp_path, monkeypatch):
    sc = OkapiSidecar(sidecar_dir=str(tmp_path))
    calls = []

    class Resp:
        status_code = 200
        content = b""

        def json(self):
            return {"segments": [], "language": "en", "segment_count": 0}

    def fake(method):
        def call(url, **kw):
            calls.append((method, url.rsplit("/", 1)[-1], kw.get("headers")))
            return Resp()
        return call

    monkeypatch.setattr(osc.requests, "post", fake("post"))
    monkeypatch.setattr(osc.requests, "get", fake("get"))
    doc = tmp_path / "a.html"
    doc.write_text("<p>x</p>", encoding="utf-8")
    for action in (lambda: sc.extract(str(doc)),
                   lambda: sc.read_tmx(str(doc)),
                   lambda: sc.validate_tmx(str(doc)),
                   lambda: sc.segment("x"),
                   lambda: sc.get_supported_filters(),
                   lambda: sc._stop_foreign_sidecar()):
        try:
            action()
        except Exception:
            pass   # the fake reply doesn't fit every call; the request is what counts
    sent = [c for c in calls if c[1] != "health"]
    assert {c[1] for c in sent} >= {"extract", "read", "validate", "segment", "filters", "shutdown"}
    for method, endpoint, headers in sent:
        assert headers == {"Authorization": f"Bearer {sc._token}"}, endpoint
