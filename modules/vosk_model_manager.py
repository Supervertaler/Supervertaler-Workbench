"""
Vosk model lifecycle: locate, download (one-time), extract, and load.

Vosk distributes its models as ZIP archives from alphacephei.com – there's
no auto-download in the Python package itself. We mirror the pattern used
for the Okapi sidecar: stream the ZIP to a temp file with progress, extract
to ``<user_data>/vosk-models/<model-name>/``, then point Vosk at the
extracted directory.

Models are tiny by speech-recognition standards. The default English small
model is ~40 MB and runs in real-time on a single CPU core.

Usage::

    from modules.vosk_model_manager import get_vosk_model
    model_dir = get_vosk_model("small-en-us", user_data_path, progress_cb)
    if model_dir:
        from vosk import Model
        model = Model(str(model_dir))
"""

from __future__ import annotations

import logging
import shutil
import zipfile
from pathlib import Path
from typing import Callable, Dict, Optional

logger = logging.getLogger(__name__)


# Model registry. Key is the short identifier we expose in the UI; value is
# the (URL, extracted-folder-name) pair. Keep the URLs pinned to a specific
# version so users on different machines end up with the same model.
MODELS: Dict[str, Dict[str, str]] = {
    "small-en-us": {
        "display_name": "English (small, ~40 MB)",
        "url": "https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip",
        "extracted_dir": "vosk-model-small-en-us-0.15",
        "size_mb": 40,
    },
    # Voice commands are English (as in Supervertaler for Trados), so this
    # is the only model. The small one is enough for command recognition.
}


DEFAULT_MODEL_KEY = "small-en-us"


def models_root(user_data_path: Path) -> Path:
    """Where Vosk models live on this user's machine."""
    return Path(user_data_path) / "vosk-models"


def is_model_installed(model_key: str, user_data_path: Path) -> bool:
    """True if the given model is already downloaded + extracted."""
    spec = MODELS.get(model_key)
    if not spec:
        return False
    target = models_root(user_data_path) / spec["extracted_dir"]
    # Vosk model dirs always contain a 'README' or 'conf' subfolder; we
    # just check existence + non-emptiness as a sanity proxy.
    return target.is_dir() and any(target.iterdir())


def get_model_path(model_key: str, user_data_path: Path) -> Optional[Path]:
    """Return path to extracted model dir, or None if not installed."""
    spec = MODELS.get(model_key)
    if not spec:
        return None
    target = models_root(user_data_path) / spec["extracted_dir"]
    if target.is_dir():
        return target
    return None


def download_and_extract(
    model_key: str,
    user_data_path: Path,
    progress_callback: Optional[Callable[[int, int], None]] = None,
) -> Optional[Path]:
    """Download (if needed) and extract a Vosk model.

    Args:
        model_key: One of the keys in :data:`MODELS`.
        user_data_path: User's Supervertaler data folder. Models go in
            ``<user_data>/vosk-models/``.
        progress_callback: Optional ``callback(bytes_done, bytes_total)``
            invoked during the download. Both values are -1 if the server
            doesn't report ``Content-Length``.

    Returns:
        Path to the extracted model directory on success, ``None`` on
        failure (errors logged via the standard logger).
    """
    spec = MODELS.get(model_key)
    if not spec:
        logger.error("Unknown Vosk model key: %r", model_key)
        return None

    try:
        import requests  # part of supervertaler's core deps already
    except ImportError:
        logger.error("'requests' not installed; cannot fetch Vosk model")
        return None

    root = models_root(user_data_path)
    root.mkdir(parents=True, exist_ok=True)

    target_dir = root / spec["extracted_dir"]
    if target_dir.is_dir() and any(target_dir.iterdir()):
        # Already there – idempotent on repeat calls.
        return target_dir

    tmp_zip = root / f".{spec['extracted_dir']}.zip.partial"
    try:
        logger.info("Downloading Vosk model from %s", spec["url"])
        with requests.get(spec["url"], stream=True, timeout=60) as resp:
            resp.raise_for_status()
            total = int(resp.headers.get("Content-Length") or -1)
            done = 0
            with open(tmp_zip, "wb") as f:
                for chunk in resp.iter_content(chunk_size=64 * 1024):
                    if not chunk:
                        continue
                    f.write(chunk)
                    done += len(chunk)
                    if progress_callback:
                        try:
                            progress_callback(done, total)
                        except Exception:
                            pass

        logger.info("Extracting Vosk model to %s", root)
        # Extract with the archive's own top-level directory preserved – this
        # is how the model expects to be referenced (Vosk reads conf/, am/,
        # graph/ etc. relative to the model dir's root).
        with zipfile.ZipFile(tmp_zip) as zf:
            zf.extractall(root)

        tmp_zip.unlink(missing_ok=True)

        if not target_dir.is_dir():
            logger.error(
                "Vosk archive extracted but expected directory %s is missing",
                target_dir,
            )
            return None

        logger.info("Vosk model installed at %s", target_dir)
        return target_dir

    except Exception as e:
        logger.error("Failed to install Vosk model %s: %s", model_key, e)
        # Clean up half-written temp file so a retry isn't blocked.
        try:
            tmp_zip.unlink(missing_ok=True)
        except Exception:
            pass
        # Clean up partially-extracted dir as well, for the same reason.
        if target_dir.is_dir():
            try:
                shutil.rmtree(target_dir, ignore_errors=True)
            except Exception:
                pass
        return None
