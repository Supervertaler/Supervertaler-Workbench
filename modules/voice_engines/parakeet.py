"""
NVIDIA Parakeet V3 for push-to-talk dictation (issue #198)
==========================================================

Parakeet TDT 0.6B v3 is an offline speech-recognition model for 25 European
languages. It detects the language by itself, is smaller than
whisper-medium, and transcribes a typical sentence much faster than
faster-whisper on the same CPU. cjpais/Handy uses it as its default.

It runs on ONNX Runtime through the ``onnx-asr`` package, from the ONNX
export on Hugging Face (``istupakov/parakeet-tdt-0.6b-v3-onnx``, int8
quantised). Supervertaler downloads the model itself rather than letting
``onnx-asr`` fetch it, so the download has a progress bar and can be
cancelled, and every file is checked against the SHA-256 (or git blob
hash, for the small text files) that Hugging Face lists for it.

Layout on disk::

    <user data>/voice-models/parakeet-tdt-0.6b-v3-int8/
        config.json  vocab.txt  encoder-model.int8.onnx  decoder_joint-model.int8.onnx
        .complete     written last, once every file is verified

No Qt here; the Voice tab runs ``download_model`` on a worker thread.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

ENGINE_ID = "parakeet"


@dataclass(frozen=True)
class ModelInfo:
    id: str
    name: str
    repo: str                 # Hugging Face repository of the ONNX export
    onnx_asr_name: str        # the name onnx_asr.load_model knows it by
    quantization: Optional[str]
    files: Tuple[str, ...]
    languages: Tuple[str, ...]
    approx_mb: int
    revision: str = "main"


PARAKEET_V3 = ModelInfo(
    id="parakeet-tdt-0.6b-v3-int8",
    name="Parakeet V3",
    repo="istupakov/parakeet-tdt-0.6b-v3-onnx",
    onnx_asr_name="nemo-parakeet-tdt-0.6b-v3",
    quantization="int8",
    files=("config.json", "vocab.txt", "encoder-model.int8.onnx", "decoder_joint-model.int8.onnx"),
    # The 25 European languages of Parakeet TDT 0.6B v3
    languages=("bg", "hr", "cs", "da", "nl", "en", "et", "fi", "fr", "de", "el", "hu", "it",
               "lv", "lt", "mt", "pl", "pt", "ro", "sk", "sl", "es", "sv", "ru", "uk"),
    approx_mb=650,   # int8: about 1 byte per parameter; the exact size comes from the manifest
)

HF_BASE = "https://huggingface.co"
COMPLETE_MARKER = ".complete"
CHUNK = 1 << 20


class DownloadCancelled(Exception):
    pass


class DownloadError(Exception):
    pass


@dataclass
class RemoteFile:
    path: str
    size: int
    sha256: Optional[str] = None   # LFS files
    git_sha1: Optional[str] = None  # small files kept in git itself


# --------------------------------------------------------------- location
def models_root(user_data_path) -> Path:
    return Path(user_data_path) / "voice-models"


def model_dir(user_data_path, model: ModelInfo = PARAKEET_V3) -> Path:
    return models_root(user_data_path) / model.id


def is_installed(user_data_path, model: ModelInfo = PARAKEET_V3) -> bool:
    d = model_dir(user_data_path, model)
    return (d / COMPLETE_MARKER).is_file() and all((d / f).is_file() for f in model.files)


def installed_size(user_data_path, model: ModelInfo = PARAKEET_V3) -> int:
    d = model_dir(user_data_path, model)
    return sum(p.stat().st_size for p in d.iterdir() if p.is_file()) if d.is_dir() else 0


def remove_model(user_data_path, model: ModelInfo = PARAKEET_V3) -> None:
    unload()
    shutil.rmtree(model_dir(user_data_path, model), ignore_errors=True)


def is_available() -> bool:
    """Whether onnx-asr and ONNX Runtime can be imported."""
    try:
        import onnx_asr  # noqa: F401
        import onnxruntime  # noqa: F401
        return True
    except Exception:
        return False


# --------------------------------------------------------------- download
def git_blob_sha1(data: bytes) -> str:
    """The id git (and Hugging Face) give a file that isn't in LFS."""
    h = hashlib.sha1(f"blob {len(data)}\0".encode())
    h.update(data)
    return h.hexdigest()


def fetch_manifest(model: ModelInfo, get_json: Callable[[str], object]) -> List[RemoteFile]:
    """Sizes and hashes of the model's files, from the Hugging Face API."""
    url = f"{HF_BASE}/api/models/{model.repo}/tree/{model.revision}"
    try:
        listing = get_json(url)
    except Exception as exc:
        raise DownloadError(f"Could not get the file list from Hugging Face: {exc}") from exc
    by_path = {e.get("path"): e for e in listing or [] if isinstance(e, dict)}
    out = []
    for name in model.files:
        entry = by_path.get(name)
        if not entry:
            raise DownloadError(f"Hugging Face no longer lists {name} in {model.repo}.")
        lfs = entry.get("lfs") or {}
        if lfs.get("oid"):
            out.append(RemoteFile(name, int(lfs.get("size") or entry.get("size") or 0), sha256=lfs["oid"]))
        else:
            out.append(RemoteFile(name, int(entry.get("size") or 0), git_sha1=entry.get("oid")))
    return out


def _verify(path: Path, rf: RemoteFile) -> bool:
    if rf.size and path.stat().st_size != rf.size:
        return False
    if rf.sha256:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(CHUNK), b""):
                h.update(chunk)
        return h.hexdigest() == rf.sha256
    if rf.git_sha1:
        return git_blob_sha1(path.read_bytes()) == rf.git_sha1
    return False


def download_model(user_data_path, *, model: ModelInfo = PARAKEET_V3,
                   progress: Optional[Callable[[int, int, str], None]] = None,
                   cancel: Optional[threading.Event] = None,
                   get_json: Optional[Callable[[str], object]] = None,
                   open_stream: Optional[Callable[[str], object]] = None) -> Path:
    """Download and verify the model; returns its folder.

    ``progress(bytes_done, bytes_total, file_name)`` is called as data
    arrives; setting ``cancel`` stops the download (DownloadCancelled).
    Files already downloaded and verified are kept, so an interrupted
    download continues where it stopped. A file that fails its hash check
    is deleted and the download fails. ``get_json``/``open_stream`` replace
    the HTTP calls in tests.
    """
    get_json = get_json or _http_get_json
    open_stream = open_stream or _http_stream
    target = model_dir(user_data_path, model)
    target.mkdir(parents=True, exist_ok=True)
    (target / COMPLETE_MARKER).unlink(missing_ok=True)
    manifest = fetch_manifest(model, get_json)
    total = sum(rf.size for rf in manifest)
    done = 0
    for rf in manifest:
        final = target / rf.path
        if final.is_file() and _verify(final, rf):
            done += rf.size
            if progress:
                progress(done, total, rf.path)
            continue
        part = final.with_name(final.name + ".part")
        h_sha256 = hashlib.sha256()
        blob = bytearray() if rf.git_sha1 else None
        try:
            url = f"{HF_BASE}/{model.repo}/resolve/{model.revision}/{rf.path}"
            with open_stream(url) as chunks, open(part, "wb") as out:
                for chunk in chunks:
                    if cancel is not None and cancel.is_set():
                        raise DownloadCancelled()
                    out.write(chunk)
                    h_sha256.update(chunk)
                    if blob is not None:
                        blob.extend(chunk)
                    done += len(chunk)
                    if progress:
                        progress(done, total, rf.path)
            ok = (h_sha256.hexdigest() == rf.sha256) if rf.sha256 else (
                git_blob_sha1(bytes(blob)) == rf.git_sha1)
            if not ok or (rf.size and part.stat().st_size != rf.size):
                raise DownloadError(f"{rf.path} is damaged: its checksum doesn't match. "
                                    f"Try the download again.")
            os.replace(part, final)
        finally:
            part.unlink(missing_ok=True)
    (target / COMPLETE_MARKER).write_text(json.dumps(
        {"repo": model.repo, "revision": model.revision,
         "files": {rf.path: rf.sha256 or rf.git_sha1 for rf in manifest}}, indent=2), encoding="utf-8")
    return target


def _http_get_json(url: str):  # pragma: no cover - network
    import requests
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    return r.json()


class _Stream:  # pragma: no cover - network
    def __init__(self, url: str):
        import requests
        self._r = requests.get(url, stream=True, timeout=60)
        self._r.raise_for_status()

    def __enter__(self):
        return self._r.iter_content(CHUNK)

    def __exit__(self, *exc):
        self._r.close()


def _http_stream(url: str):  # pragma: no cover - network
    return _Stream(url)


# --------------------------------------------------------------- transcribe
_loaded: Dict[str, object] = {}
_load_lock = threading.Lock()


def load(user_data_path, model: ModelInfo = PARAKEET_V3):
    """The loaded model, kept for later dictations (loading takes a few
    seconds; transcribing a sentence well under one)."""
    d = model_dir(user_data_path, model)
    key = str(d)
    with _load_lock:
        if key not in _loaded:
            if not is_installed(user_data_path, model):
                raise FileNotFoundError(f"{model.name} isn't downloaded yet (Voice tab → Dictation).")
            import onnx_asr
            _loaded[key] = onnx_asr.load_model(model.onnx_asr_name, str(d),
                                               quantization=model.quantization)
        return _loaded[key]


def unload() -> None:
    with _load_lock:
        _loaded.clear()


def transcribe(wav_path: str, user_data_path, model: ModelInfo = PARAKEET_V3) -> str:
    """Text of a 16 kHz mono WAV file. Parakeet detects the language."""
    result = load(user_data_path, model).recognize(wav_path)
    text = result if isinstance(result, str) else getattr(result, "text", "") or ""
    return text.strip()
