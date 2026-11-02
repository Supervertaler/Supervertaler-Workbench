"""Input-device enumeration + name → index resolution for the Voice surface.

The voice-command listener (Always-On) and the Voice tab's
microphone picker all need to ask sounddevice the same two questions:

  1. What input devices does the user have? – for the dropdown
  2. Given a saved device name, what's the current device index? – at
     record time, because indices can shuffle between sessions (USB
     devices added / removed, default device changed, etc.)

PortAudio (sounddevice's underlying lib) exposes the same physical mic
through several Windows host APIs (MME, DirectSound, WASAPI, WDM-KS),
which means ``sd.query_devices()`` returns ~12 entries for what Windows
Settings shows as 2 mics. MME also truncates device names to 32 chars,
which makes the user pick between "Microphone (BRIO 4K Stream Edit"
and "Microphone (BRIO 4K Stream Edition)" without knowing they're the
same device. To match what the OS's own sound settings show, filter to
the platform's modern host API: WASAPI on Windows, Core Audio on
macOS, ALSA on Linux. The selected host API also gives us clean,
non-truncated device names.
"""
from __future__ import annotations

import sys
from typing import List, Optional


# Sentinel value the Voice tab uses for "let the OS pick" – stored in
# dictation_settings.mic_device when the user wants the system default.
# Resolving this to ``None`` at record time tells sounddevice to use
# whichever input is currently the OS default.
DEFAULT_SENTINEL = "__default__"


def _preferred_hostapi_index() -> Optional[int]:
    """Return the sounddevice host-API index that matches the OS's own
    sound settings, or ``None`` if we can't find it (fall back to no
    filter, the same behaviour as before this filter existed).
    """
    try:
        import sounddevice as sd
        hostapis = sd.query_hostapis()
    except Exception:
        return None

    if sys.platform == 'win32':
        preferred = 'Windows WASAPI'
    elif sys.platform == 'darwin':
        preferred = 'Core Audio'
    else:
        preferred = 'ALSA'

    for idx, api in enumerate(hostapis):
        try:
            if api.get('name') == preferred:
                return idx
        except Exception:
            continue
    return None


def list_input_devices() -> List[str]:
    """Return the list of available *input* devices' names, filtered
    to the platform's modern host API so the user sees the same set
    as their OS sound settings (no MME-truncated duplicates, no
    DirectSound virtual entries, no WDM-KS low-level handles).
    """
    try:
        import sounddevice as sd
        devices = sd.query_devices()
    except Exception:
        return []

    hostapi_idx = _preferred_hostapi_index()
    seen = set()
    names: List[str] = []
    for dev in devices:
        try:
            if dev.get('max_input_channels', 0) <= 0:
                continue
            if hostapi_idx is not None and dev.get('hostapi') != hostapi_idx:
                continue
        except Exception:
            continue
        name = dev.get('name')
        if not name or name in seen:
            continue
        seen.add(name)
        names.append(name)
    return names


def _is_wasapi_device(device_idx: Optional[int]) -> bool:
    """True iff ``device_idx`` is a Windows WASAPI device.

    Used by ``wasapi_autoconvert_settings`` to decide whether to attach
    an auto-resampling flag. WASAPI in shared mode forces the OS
    mixer's sample rate (typically 48000 Hz) on any capture stream,
    while our pipeline asks for Vosk's 16000 Hz. Without
    the auto-convert flag, opening a capture stream at 16000 Hz on a
    WASAPI device fails with "Invalid sample rate"; with it, WASAPI
    transparently resamples between the device's mixer rate and the
    rate we ask for.
    """
    if device_idx is None:
        return False
    try:
        import sounddevice as sd
        info = sd.query_devices(device_idx)
        hostapis = sd.query_hostapis()
        return hostapis[info['hostapi']].get('name') == 'Windows WASAPI'
    except Exception:
        return False


def wasapi_autoconvert_settings(device_idx: Optional[int]):
    """Return an ``extra_settings`` kwarg for ``sd.rec`` / ``sd.InputStream``
    that enables WASAPI's automatic sample-rate conversion for the given
    device, or ``None`` if the device isn't WASAPI (no special settings
    needed; MME handles resampling on its own).

    Must be called fresh per recording – the underlying object isn't
    safe to re-use across streams in some sounddevice builds.
    """
    if not _is_wasapi_device(device_idx):
        return None
    try:
        import sounddevice as sd
        return sd.WasapiSettings(auto_convert=True)
    except Exception:
        return None


def resolve_device_index(saved_name: Optional[str]) -> Optional[int]:
    """Map a saved device name to the current sounddevice device index.

    Filters by the same host API as ``list_input_devices`` so the index
    we return matches the device the user actually saw + picked. Without
    this filter we'd resolve to the first match across *all* host APIs,
    which can be the MME-truncated variant of the same physical mic –
    works, but uses a legacy API path with worse latency and dropped
    audio characteristics.

    Returns:
        - ``None`` if ``saved_name`` is empty, the default sentinel, or
          the named device is no longer attached. In all of those cases
          sounddevice falls back to the OS default input, which is the
          right thing.
        - An integer index suitable for the ``device=`` kwarg of
          ``sd.rec`` / ``sd.InputStream`` otherwise.
    """
    if not saved_name or saved_name == DEFAULT_SENTINEL:
        return None
    try:
        import sounddevice as sd
        devices = sd.query_devices()
    except Exception:
        return None
    hostapi_idx = _preferred_hostapi_index()
    for idx, dev in enumerate(devices):
        try:
            if dev.get('max_input_channels', 0) <= 0:
                continue
            if hostapi_idx is not None and dev.get('hostapi') != hostapi_idx:
                continue
            if dev.get('name') == saved_name:
                return idx
        except Exception:
            continue
    # Saved device is gone – fall back to OS default rather than raising.
    return None
