"""The 16 Nov review's leftovers: AI actions ask first and stay inside the
prompt library; a Trados package's temporary folder is removed when loading
fails; the Okapi download must match its SHA-256; Superlookup escapes what it
shows as HTML; the Windows build uses pinned versions.
"""

from __future__ import annotations

import ast
import hashlib
import io
import os
import sys
import tempfile
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from modules.ai_actions import AIActionSystem  # noqa: E402
from modules.unified_prompt_library import UnifiedPromptLibrary  # noqa: E402


# ── AI actions ────────────────────────────────────────────────────────────

def _system(tmp_path, confirm=None):
    lib = UnifiedPromptLibrary(library_dir=tmp_path / "library", log_callback=lambda *_: None)
    lib.load_all_prompts()  # writes the default prompts on first load
    system = AIActionSystem(lib, log_callback=lambda *_: None, confirm_callback=confirm)
    system.files_before = _files(tmp_path)
    return system, lib


CREATE = 'ACTION:create_prompt PARAMS:{"name": "Medical", "content": "Be careful."}'


def _files(folder: Path):
    return sorted(p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_file())


def test_without_a_way_to_ask_nothing_changes(tmp_path):
    system, lib = _system(tmp_path, confirm=None)
    _, results = system.parse_and_execute(CREATE)
    assert results[0]["success"] is False
    assert _files(tmp_path) == system.files_before


def test_a_no_changes_nothing_but_reads_still_run(tmp_path):
    asked = []
    system, lib = _system(tmp_path, confirm=lambda d: asked.append(d) or False)
    reply = CREATE + "\nACTION:list_prompts PARAMS:{}"
    cleaned, results = system.parse_and_execute(reply)
    assert asked == [["Create the prompt “Medical”"]]
    assert [r["success"] for r in results] == [False, True]
    assert _files(tmp_path) == system.files_before
    assert "ACTION:" not in cleaned


def test_a_yes_asked_once_for_all_changes(tmp_path):
    asked = []
    system, lib = _system(tmp_path, confirm=lambda d: asked.append(d) or True)
    reply = CREATE + '\nACTION:create_folder PARAMS:{"path": "Legal"}'
    _, results = system.parse_and_execute(reply)
    assert len(asked) == 1 and len(asked[0]) == 2
    assert all(r["success"] for r in results)
    assert "Medical.md" in _files(lib.library_dir)
    assert (lib.library_dir / "Legal").is_dir()


def test_a_failing_question_changes_nothing(tmp_path):
    def boom(_):
        raise RuntimeError("no window")
    system, lib = _system(tmp_path, confirm=boom)
    _, results = system.parse_and_execute(CREATE)
    assert results[0]["success"] is False
    assert _files(tmp_path) == system.files_before


@pytest.mark.parametrize("folder", [
    "../../outside", "..\\outside", "C:/Users/x/AppData/Roaming/Microsoft/Windows/Start Menu/Programs/Startup",
    "/etc", "a/../../b",
])
def test_folders_outside_the_library_are_refused(tmp_path, folder):
    system, lib = _system(tmp_path, confirm=lambda d: True)
    reply = ('ACTION:create_prompt PARAMS:{"name": "x", "content": "y", "folder": %s}\n'
             'ACTION:create_folder PARAMS:{"path": %s}') % (_json(folder), _json(folder))
    _, results = system.parse_and_execute(reply)
    assert [r["success"] for r in results] == [False, False]
    assert _files(tmp_path) == system.files_before
    assert [p.name for p in tmp_path.iterdir()] == ["library"]


def test_the_library_itself_refuses_paths_that_leave_it(tmp_path):
    lib = UnifiedPromptLibrary(library_dir=tmp_path / "library", log_callback=lambda *_: None)
    assert lib.save_prompt("../escaped.md", {"name": "x", "content": "y"}) is False
    assert lib.create_folder("../escaped") is False
    assert not (tmp_path / "escaped.md").exists() and not (tmp_path / "escaped").exists()
    # Ordinary nested folders still work.
    assert lib.save_prompt("Domain/Sub/ok.md", {"name": "ok", "content": "y"}) is True


def _json(value):
    import json
    return json.dumps(value)


# ── Trados package temporary folders ─────────────────────────────────────

def _sdlppx_dirs():
    return {p for p in Path(tempfile.gettempdir()).glob("sdlppx_*")}


def test_a_package_that_fails_to_load_leaves_no_temp_folder(tmp_path):
    from modules.sdlppx_handler import TradosPackageHandler
    pkg = tmp_path / "broken.sdlppx"
    with zipfile.ZipFile(pkg, "w") as zf:
        zf.writestr("readme.txt", "no project file in here")
    before = _sdlppx_dirs()
    handler = TradosPackageHandler(log_callback=lambda *_: None)
    assert handler.load_package(str(pkg)) is None
    assert _sdlppx_dirs() == before


def test_a_cancelled_load_leaves_no_temp_folder(tmp_path):
    from modules.sdlppx_handler import TradosPackageHandler
    pkg = tmp_path / "any.sdlppx"
    with zipfile.ZipFile(pkg, "w") as zf:
        zf.writestr("readme.txt", "x")
    before = _sdlppx_dirs()
    handler = TradosPackageHandler(log_callback=lambda *_: None)
    assert handler.load_package(str(pkg), progress_callback=lambda *a: False) is None
    assert _sdlppx_dirs() == before


def test_a_given_extract_folder_is_never_removed(tmp_path):
    from modules.sdlppx_handler import TradosPackageHandler
    pkg = tmp_path / "broken.sdlppx"
    with zipfile.ZipFile(pkg, "w") as zf:
        zf.writestr("readme.txt", "x")
    mine = tmp_path / "mine"
    handler = TradosPackageHandler(log_callback=lambda *_: None)
    assert handler.load_package(str(pkg), extract_dir=str(mine)) is None
    assert mine.is_dir()


# ── Okapi download checksum ───────────────────────────────────────────────

class _FakeResponse:
    def __init__(self, data):
        self.data = data
        self.headers = {"Content-Length": str(len(data))}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def raise_for_status(self):
        pass

    def iter_content(self, chunk_size):
        for i in range(0, len(self.data), chunk_size):
            yield self.data[i:i + chunk_size]


def _sidecar_with_download(monkeypatch, data):
    from modules import okapi_sidecar
    monkeypatch.setattr(okapi_sidecar.requests, "get", lambda *a, **k: _FakeResponse(data))
    return okapi_sidecar.OkapiSidecar


def test_a_download_with_the_wrong_checksum_is_refused(monkeypatch, tmp_path):
    cls = _sidecar_with_download(monkeypatch, b"not the real sidecar")
    with pytest.raises(ValueError):
        cls._stream_to_file("https://example.invalid/x", tmp_path / "x", None, "0" * 64)


def test_the_right_checksum_passes(monkeypatch, tmp_path):
    data = b"the real sidecar"
    cls = _sidecar_with_download(monkeypatch, data)
    cls._stream_to_file("https://example.invalid/x", tmp_path / "x", None,
                        hashlib.sha256(data).hexdigest())
    assert (tmp_path / "x").read_bytes() == data


def test_a_bad_jar_download_keeps_the_installed_one(monkeypatch, tmp_path):
    cls = _sidecar_with_download(monkeypatch, b"swapped")
    sidecar = cls.__new__(cls)
    (tmp_path / "okapi-sidecar.jar").write_bytes(b"working jar")
    assert sidecar._download_install_jar_only("https://example.invalid/x", tmp_path, None) is False
    assert (tmp_path / "okapi-sidecar.jar").read_bytes() == b"working jar"
    assert not (tmp_path / "okapi-sidecar.jar.download").exists()


def test_a_bad_bundle_download_keeps_the_installed_one(monkeypatch, tmp_path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("okapi-sidecar/okapi-sidecar.jar", "swapped")
    cls = _sidecar_with_download(monkeypatch, buf.getvalue())
    sidecar = cls.__new__(cls)
    installed = tmp_path / "okapi-sidecar"
    installed.mkdir()
    (installed / "okapi-sidecar.jar").write_bytes(b"working jar")
    assert sidecar._download_install_zip_bundle("https://example.invalid/x", installed, None) is False
    assert (installed / "okapi-sidecar.jar").read_bytes() == b"working jar"
    assert not (tmp_path / "okapi-sidecar-download.zip").exists()


def test_each_download_url_has_a_checksum():
    from modules.okapi_sidecar import OkapiSidecar
    for digest in (OkapiSidecar.INSTALLER_SHA256_WINDOWS, OkapiSidecar.INSTALLER_SHA256_JAR_ONLY):
        assert len(digest) == 64 and int(digest, 16) >= 0


# ── Superlookup escaping ──────────────────────────────────────────────────

def _highlighter():
    with open(os.path.join(ROOT, "modules", "superlookup_tab.py"), encoding="utf-8") as f:
        tree = ast.parse(f.read())
    [cls] = [n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "SuperlookupTab"]
    [fn] = [n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "_highlight_search_term"]
    ns = {}
    exec(compile(ast.Module(body=[fn], type_ignores=[]), "superlookup_tab.py", "exec"), ns)
    return lambda text, term: ns["_highlight_search_term"](SimpleNamespace(theme_manager=None), text, term)


def test_highlight_escapes_text_without_a_term():
    hl = _highlighter()
    assert hl("<b>x</b>", "") == "&lt;b&gt;x&lt;/b&gt;"


def test_highlight_escapes_and_still_finds_special_characters():
    hl = _highlighter()
    out = hl("Smith & <Wesson>", "& <w")
    assert "<Wesson" not in out
    assert ">&amp; &lt;W</span>" in out


# ── Pinned build ──────────────────────────────────────────────────────────

def test_the_windows_build_installs_pinned_versions():
    script = Path(ROOT, "build_windows_release.ps1").read_text(encoding="utf-8")
    assert '-c build-constraints.txt -e "."' in script
    assert "-U pyinstaller" not in script
    pins = Path(ROOT, "build-constraints.txt").read_text(encoding="utf-8").splitlines()
    pins = [p for p in pins if p and not p.startswith("#")]
    assert pins and all("==" in p for p in pins)
    names = {p.split("==")[0].lower() for p in pins}
    assert {"pyinstaller", "pyqt6", "requests"} <= names
