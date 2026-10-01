"""Parakeet V3 dictation engine (issue #198): verified model download and
the transcriber, with fake HTTP and a fake onnx_asr."""

import hashlib
import os
import sys
import threading
import types

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.voice_engines import parakeet as pk

FILES = {
    "config.json": b'{"model_type": "nemo-conformer-tdt"}',
    "vocab.txt": b"<unk> 0\n\xe2\x96\x81the 1\n",
    "encoder-model.int8.onnx": os.urandom(3 * pk.CHUNK + 123),
    "decoder_joint-model.int8.onnx": os.urandom(5000),
}
LFS = {"encoder-model.int8.onnx", "decoder_joint-model.int8.onnx"}


def listing(files=FILES):
    out = [{"type": "file", "path": "README.md", "size": 10, "oid": "x"}]
    for name, data in files.items():
        if name in LFS:
            out.append({"type": "file", "path": name, "size": 134, "oid": "pointer",
                        "lfs": {"oid": hashlib.sha256(data).hexdigest(), "size": len(data)}})
        else:
            out.append({"type": "file", "path": name, "size": len(data), "oid": pk.git_blob_sha1(data)})
    return out


class Server:
    def __init__(self, files=FILES, cancel_at=None):
        self.files, self.cancel_at, self.requests = dict(files), cancel_at, []

    def get_json(self, url):
        assert url == "https://huggingface.co/api/models/istupakov/parakeet-tdt-0.6b-v3-onnx/tree/main"
        return listing()

    def open_stream(self, url):
        name = url.rsplit("/", 1)[1]
        assert url == f"https://huggingface.co/istupakov/parakeet-tdt-0.6b-v3-onnx/resolve/main/{name}"
        self.requests.append(name)
        data = self.files[name]
        server = self

        class Ctx:
            def __enter__(self):
                def gen():
                    for i in range(0, len(data), pk.CHUNK):
                        if server.cancel_at and server.cancel_at[0] == name and i >= pk.CHUNK:
                            server.cancel_at[1].set()
                        yield data[i:i + pk.CHUNK]
                return gen()

            def __exit__(self, *a):
                return False
        return Ctx()


def test_git_blob_sha1():
    assert pk.git_blob_sha1(b"hello\n") == "ce013625030ba8dba906f756967f9e9ca394464a"


def test_manifest_from_the_hub_listing():
    m = pk.fetch_manifest(pk.PARAKEET_V3, lambda url: listing())
    assert [f.path for f in m] == list(FILES)
    assert m[2].sha256 == hashlib.sha256(FILES["encoder-model.int8.onnx"]).hexdigest()
    assert m[2].size == len(FILES["encoder-model.int8.onnx"]) and m[0].git_sha1
    with pytest.raises(pk.DownloadError, match="no longer lists vocab.txt"):
        pk.fetch_manifest(pk.PARAKEET_V3, lambda url: [e for e in listing() if e["path"] != "vocab.txt"])
    with pytest.raises(pk.DownloadError, match="file list"):
        pk.fetch_manifest(pk.PARAKEET_V3, lambda url: (_ for _ in ()).throw(OSError("403")))


def test_download_verifies_and_resumes(tmp_path):
    srv = Server()
    seen = []
    d = pk.download_model(tmp_path, get_json=srv.get_json, open_stream=srv.open_stream,
                          progress=lambda done, total, name: seen.append((done, total, name)))
    assert d == tmp_path / "voice-models" / "parakeet-tdt-0.6b-v3-int8"
    assert pk.is_installed(tmp_path)
    assert all((d / n).read_bytes() == data for n, data in FILES.items())
    total = sum(len(x) for x in FILES.values())
    assert seen[-1][:2] == (total, total) and all(t == total for _, t, _ in seen)
    assert not list(d.glob("*.part"))
    assert pk.installed_size(tmp_path) > total
    # again: everything verifies, nothing is fetched
    srv2 = Server()
    pk.download_model(tmp_path, get_json=srv2.get_json, open_stream=srv2.open_stream)
    assert srv2.requests == []
    # one file lost: only that one is fetched again
    (d / "vocab.txt").unlink()
    assert not pk.is_installed(tmp_path)
    srv3 = Server()
    pk.download_model(tmp_path, get_json=srv3.get_json, open_stream=srv3.open_stream)
    assert srv3.requests == ["vocab.txt"] and pk.is_installed(tmp_path)


def test_damaged_file_and_cancel(tmp_path):
    bad = dict(FILES, **{"decoder_joint-model.int8.onnx": b"tampered" * 625})
    srv = Server(files=bad)
    with pytest.raises(pk.DownloadError, match="checksum"):
        pk.download_model(tmp_path, get_json=srv.get_json, open_stream=srv.open_stream)
    d = pk.model_dir(tmp_path)
    assert not pk.is_installed(tmp_path)
    assert not (d / "decoder_joint-model.int8.onnx").exists() and not list(d.glob("*.part"))

    cancel = threading.Event()
    srv = Server(cancel_at=("encoder-model.int8.onnx", cancel))
    pk.remove_model(tmp_path)
    with pytest.raises(pk.DownloadCancelled):
        pk.download_model(tmp_path, get_json=srv.get_json, open_stream=srv.open_stream, cancel=cancel)
    assert not pk.is_installed(tmp_path) and not list(d.glob("*.part"))
    assert (d / "vocab.txt").exists()          # finished files are kept for the next try


def test_load_and_transcribe(tmp_path, monkeypatch):
    srv = Server()
    pk.download_model(tmp_path, get_json=srv.get_json, open_stream=srv.open_stream)
    calls = []

    class Model:
        def recognize(self, wav):
            calls.append(("recognize", wav))
            return "  Hallo wereld.  "

    fake = types.ModuleType("onnx_asr")
    fake.load_model = lambda name, path, quantization=None: calls.append((name, path, quantization)) or Model()
    monkeypatch.setitem(sys.modules, "onnx_asr", fake)
    pk.unload()
    assert pk.transcribe("a.wav", tmp_path) == "Hallo wereld."
    assert pk.transcribe("b.wav", tmp_path) == "Hallo wereld."
    assert calls[0] == ("nemo-parakeet-tdt-0.6b-v3", str(pk.model_dir(tmp_path)), "int8")
    assert [c[0] for c in calls] == ["nemo-parakeet-tdt-0.6b-v3", "recognize", "recognize"]   # loaded once
    pk.remove_model(tmp_path)
    assert not pk.model_dir(tmp_path).exists() and pk._loaded == {}
    with pytest.raises(FileNotFoundError, match="isn't downloaded"):
        pk.load(tmp_path)


def test_dictation_thread_dispatch(tmp_path, monkeypatch):
    from modules.voice_dictation_lite import QuickDictationThread
    t = QuickDictationThread(engine="parakeet", user_data_path=str(tmp_path), replacements=[])
    assert t.engine == "parakeet"
    assert QuickDictationThread(use_api=True, engine="parakeet").engine == "api"
    errors = []
    t.error_occurred.connect(errors.append)
    monkeypatch.setattr(pk, "is_available", lambda: True)
    assert t._transcribe_with_parakeet("x.wav") == "" and "isn't downloaded" in errors[-1]
    srv = Server()
    pk.download_model(tmp_path, get_json=srv.get_json, open_stream=srv.open_stream)
    fake = types.ModuleType("onnx_asr")
    fake.load_model = lambda name, path, quantization=None: types.SimpleNamespace(recognize=lambda wav: "Supervertile werkt")
    monkeypatch.setitem(sys.modules, "onnx_asr", fake)
    pk.unload()
    loading = []
    t.model_loading_started.connect(loading.append)
    t.replacements = [{"heard": "Supervertile", "meant": "Supervertaler"}]
    assert t._transcribe_with_parakeet("x.wav") == "Supervertaler werkt"
    assert loading == ["Parakeet V3"]
    pk.unload()
