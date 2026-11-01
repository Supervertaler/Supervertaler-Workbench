"""
The API keys every Supervertaler product shares, in two copies during step 1
of "API keys locked to the computer" (Plugin Core,
docs/design/api-key-protection.md). Plugin Core's ``ApiKeyStore`` is the
reference: the same files, the same format, the same lock, the same rules.

**The plain file**, ``<data root>/settings/api-keys.json``: one key per
provider in clear text, shared with Supervertaler for Trados, for memoQ and
Sidekick. The data folder it sits in is portable - synced, backed up, copied
whole - and every key in it travels with it. It is still written exactly as
before, only so that products not yet updated keep working; step 2 imports it
into the locked copy and removes it.

**The locked copy**, outside the data folder. On Windows
``%APPDATA%/Supervertaler/api-keys.locked.json``: the same ids, each value
locked by Windows to this Windows account (DPAPI), so a copy of it opens for
no other account. On a Mac one Keychain item (Linux: the Secret Service),
service ``Supervertaler``, account ``api-keys``, holding the same map with
plain values; when there is none, the plain copies stay as before (M7).

**Step 1** (design note §5): a read takes the plain file's key when it has
one, else the locked copy's; a save writes the plain file, then the locked
copy; and ``sync_locked()``, at start-up, copies the plain keys into the
locked copy - adding and replacing, never removing, so a damaged or
half-written plain file cannot empty it. Always the plain file first: one
order everywhere means no two products wait on each other, and a crash
between the two writes leaves the plain file the newer one, which reads take
and the next sync copies.

**Three answers, not two.** A key this account cannot unlock is
``UNREADABLE``: it is reported by name with what to do (core's sentences),
never shown as an empty box, never sent to a provider blank, never replaced by
an older copy, and it stays in the file until a new key replaces it - some
causes pass (a profile loaded oddly, a domain controller not yet reachable).

Nothing is cached. A lookup reads the files and unlocks the keys asked for,
about a millisecond, once per action - and a cache would go stale when another
product changes a key.

Windows needs nothing beyond the standard library (``ctypes``); ``keyring`` is
imported only on a Mac or Linux.
"""

import base64
import json
import logging
import os
import re
import sys
import threading
import time
import uuid
from datetime import datetime
from typing import Callable, Dict, Iterable, List, NamedTuple, Optional, Tuple

_log = logging.getLogger(__name__)

PRESENT, ABSENT, UNREADABLE = "present", "absent", "unreadable"

# Why a key is unreadable.
_FINE, _REFUSED, _DAMAGED, _UNAVAILABLE = "fine", "refused", "damaged", "unavailable"

# A writer holds a key file exclusively for a few milliseconds; a second
# reader or writer waits up to a second for it (core: 20 x 50 ms).
_RETRIES = 20
_RETRY_SECONDS = 0.05

# Every write in this process takes it, and the sync holds it throughout, so
# a thread saving a key waits for the sync instead of timing out on a held file.
_gate = threading.RLock()

_ALIASES = {"anthropic": "claude", "xai": "grok", "x.ai": "grok", "custom": "custom_openai"}

_PLAIN_COMMENT = ("API keys shared by Supervertaler for Trados, Supervertaler for memoQ and "
                  "Supervertaler Sidekick. One key per provider; edit by hand or in any product's settings.")
_LOCKED_COMMENT = ("Supervertaler API keys, locked by Windows to this Windows account. They cannot be "
                   "read on another computer or by another account. Change them in any product's settings.")

# The names users know the providers by: core's, then the ones only
# Workbench (and Sidekick) have.
_NAMES = {
    "claude": "Anthropic", "openai": "OpenAI", "gemini": "Gemini", "grok": "xAI",
    "openrouter": "OpenRouter", "mistral": "Mistral", "deepseek": "DeepSeek",
    "custom_openai": "Custom OpenAI", "deepl": "DeepL", "google": "Google Translate",
    "microsoft": "Microsoft Translator", "modernmt": "ModernMT", "mymemory": "MyMemory",
    "amazon_translate": "Amazon Translate", "amazon_translate_secret": "Amazon Translate secret",
}


# ─── Where the files are ─────────────────────────────────────────────────────

def config_dir() -> str:
    """Where config.json lives - the pointer every product reads to find the data root."""
    if sys.platform == "win32":
        return os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "Supervertaler")
    if sys.platform == "darwin":
        return os.path.join(os.path.expanduser("~"), "Library", "Application Support", "Supervertaler")
    return os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"), "Supervertaler")


def data_root() -> str:
    """The shared data folder: what config.json points at, else ~/Supervertaler (core's rule)."""
    try:
        with open(os.path.join(config_dir(), "config.json"), "r", encoding="utf-8-sig") as f:
            path = json.load(f).get("user_data_path")
        if isinstance(path, str) and path.strip():
            return path
    except Exception:
        pass
    return os.path.join(os.path.expanduser("~"), "Supervertaler")


def plain_file_path() -> str:
    return os.path.join(data_root(), "settings", "api-keys.json")


def locked_file_path() -> Optional[str]:
    """Windows: ``%APPDATA%/Supervertaler/api-keys.locked.json``, beside config.json,
    in the roaming part of the profile - where Windows keeps the key that unlocks
    it, so a roaming profile carries both. None elsewhere (the Keychain is used)."""
    return os.path.join(config_dir(), "api-keys.locked.json") if sys.platform == "win32" else None


# ─── Ids ─────────────────────────────────────────────────────────────────────

def canonical(provider: str) -> str:
    k = (provider or "").strip().lower()
    return _ALIASES.get(k, k)


def private_id(product: str, kind: str, name: str) -> str:
    """One product's own key, such as a named OpenAI-compatible profile:
    ``workbench:custom_openai:Acme server``. Quotes and backslashes are dropped
    from the name, as every product does (§3). Kept in the locked copy only."""
    return f"{product}:{kind}:" + (name or "").replace('"', "").replace("\\", "")


def _is_private(key_id: str) -> bool:
    return ":" in (key_id or "")


def _id(provider: str) -> str:
    return provider if _is_private(provider) else canonical(provider)


def display_name(key_id: str) -> str:
    """The name a user knows a provider by; for a private id, the profile's own name."""
    key_id = _id(key_id)
    if _is_private(key_id):
        parts = key_id.split(":", 2)
        return parts[2] if len(parts) == 3 else key_id
    return _NAMES.get(key_id, key_id)


def _fold(s: str) -> str:
    # Core compares ids ordinally ignoring case, one character at a time.
    return "".join(c.upper() if len(c.upper()) == 1 else c for c in s)


class _Ids:
    """id → value, ids compared ignoring case and kept as first spelt, as
    core's dictionaries are, so an id another product wrote survives a rewrite."""

    def __init__(self):
        self._d: Dict[str, Tuple[str, str]] = {}

    def get(self, key_id: str) -> Optional[str]:
        e = self._d.get(_fold(key_id))
        return e[1] if e else None

    def has(self, key_id: str) -> bool:
        return _fold(key_id) in self._d

    def set(self, key_id: str, value: str) -> None:
        k = _fold(key_id)
        self._d[k] = (self._d[k][0] if k in self._d else key_id, value)

    def remove(self, key_id: str) -> bool:
        return self._d.pop(_fold(key_id), None) is not None

    def items(self) -> List[Tuple[str, str]]:
        return sorted(self._d.values(), key=lambda e: _fold(e[0]))

    def __len__(self):
        return len(self._d)


# ─── The two formats ─────────────────────────────────────────────────────────

def _decode(raw: bytes) -> str:
    """UTF-8 unless a byte-order mark says otherwise, which is then consumed."""
    for bom, enc in ((b"\xef\xbb\xbf", "utf-8"), (b"\xff\xfe\x00\x00", "utf-32-le"),
                     (b"\x00\x00\xfe\xff", "utf-32-be"), (b"\xff\xfe", "utf-16-le"), (b"\xfe\xff", "utf-16-be")):
        if raw.startswith(bom):
            return raw[len(bom):].decode(enc, "replace")
    return raw.decode("utf-8", "replace")


_PLAIN_ENTRY = re.compile(r'"(?P<k>[^"\\]+)"\s*:\s*"(?P<v>(?:[^"\\]|\\.)*)"')


def _parse_plain(text: str) -> _Ids:
    """The plain file, read forgivingly as core reads it (it is edited by hand):
    a flat object of strings; anything else is ignored."""
    ids = _Ids()
    for m in _PLAIN_ENTRY.finditer(text or ""):
        k = m.group("k")
        if k.startswith("_"):
            continue  # "_comment" and the like
        ids.set(canonical(k), m.group("v").replace('\\"', '"').replace("\\\\", "\\"))
    return ids


def _render_plain(ids: _Ids) -> str:
    def esc(s):
        return (s or "").replace("\\", "\\\\").replace('"', '\\"')
    entries = ids.items()
    lines = ["{", f'  "_comment": "{_PLAIN_COMMENT}",']
    for i, (k, v) in enumerate(entries):
        lines.append(f'  "{esc(k)}": "{esc(v)}"' + ("," if i < len(entries) - 1 else ""))
    lines.append("}")
    return "\r\n".join(lines) + "\r\n"


def _parse_locked(text: str) -> Optional[_Ids]:
    """The locked copy, read strictly - other products write it too, so a
    truncated or garbled one must be recognised as damaged (None), never read
    as fewer keys. Empty or blank counts as no entries. Ids starting with "_"
    are ignored; an id another product wrote is kept exactly as spelt."""
    ids = _Ids()
    if not (text or "").strip():
        return ids
    try:
        data = json.loads(text)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    for k, v in data.items():
        if k.startswith("_") or not isinstance(v, str):
            continue
        ids.set(_ALIASES.get(k.strip().lower(), k), v)
    return ids


def _json_string(s: str) -> str:
    # As core's serializer (JavaScriptSerializer) writes a string: the short
    # escapes, \u for other control characters, for ' < > & and for the three
    # Unicode line breaks. Base64 needs none of them; profile names might.
    short = {'"': '\\"', "\\": "\\\\", "\b": "\\b", "\f": "\\f", "\n": "\\n", "\r": "\\r", "\t": "\\t"}
    out = []
    for c in s or "":
        if c in short:
            out.append(short[c])
        elif ord(c) < 0x20 or c in "'<>&\u0085\u2028\u2029":
            out.append("\\u%04x" % ord(c))
        else:
            out.append(c)
    return '"' + "".join(out) + '"'


def _render_locked(ids: _Ids) -> str:
    """Byte for byte as core renders it: the comment first, then the ids in
    core's order, CRLF line ends (written as UTF-8 without a byte-order mark)."""
    out = '{\r\n  "_comment": "' + _LOCKED_COMMENT + '"'
    for k, v in ids.items():
        out += ",\r\n  " + _json_string(k) + ": " + _json_string(v)
    return out + "\r\n}\r\n"


# ─── Windows: DPAPI, and files held the way core holds them ──────────────────

if sys.platform == "win32":
    import ctypes
    import msvcrt
    from ctypes import wintypes

    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)

    class _BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]

    _CryptProtectData = _crypt32.CryptProtectData
    _CryptProtectData.argtypes = [ctypes.POINTER(_BLOB), wintypes.LPCWSTR, ctypes.POINTER(_BLOB),
                                  ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_BLOB)]
    _CryptProtectData.restype = wintypes.BOOL
    _CryptUnprotectData = _crypt32.CryptUnprotectData
    _CryptUnprotectData.argtypes = [ctypes.POINTER(_BLOB), ctypes.c_void_p, ctypes.POINTER(_BLOB),
                                    ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_BLOB)]
    _CryptUnprotectData.restype = wintypes.BOOL
    _LocalFree = _k32.LocalFree
    _LocalFree.argtypes = [ctypes.c_void_p]
    _LocalFree.restype = ctypes.c_void_p

    _CreateFileW = _k32.CreateFileW
    _CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                             wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    _CreateFileW.restype = wintypes.HANDLE
    _CloseHandle = _k32.CloseHandle
    _CloseHandle.argtypes = [wintypes.HANDLE]
    _SetFileInformationByHandle = _k32.SetFileInformationByHandle
    _SetFileInformationByHandle.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    _SetFileInformationByHandle.restype = wintypes.BOOL
    _MoveFileExW = _k32.MoveFileExW
    _MoveFileExW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD]
    _MoveFileExW.restype = wintypes.BOOL

    _INVALID_HANDLE = wintypes.HANDLE(-1).value
    _GENERIC_READ, _GENERIC_WRITE = 0x80000000, 0x40000000
    _DELETE, _SYNCHRONIZE = 0x00010000, 0x00100000
    _SHARE_READ, _SHARE_WRITE, _SHARE_DELETE = 1, 2, 4
    _OPEN_EXISTING, _OPEN_ALWAYS = 3, 4
    _CRYPTPROTECT_UI_FORBIDDEN = 0x1
    _FILE_RENAME_INFO_EX = 22
    _RENAME_REPLACE_IF_EXISTS, _RENAME_POSIX_SEMANTICS = 0x1, 0x2
    _MOVEFILE_REPLACE_EXISTING, _MOVEFILE_WRITE_THROUGH = 0x1, 0x8
    _ERROR_FILE_EXISTS, _ERROR_ALREADY_EXISTS = 80, 183
    _IN_USE = (5, 32, 33)  # access denied (delete pending), sharing violation, lock violation

    def _crypt(fn, data: bytes) -> bytes:
        """CryptProtectData or CryptUnprotectData, current user, never a prompt,
        no description, no entropy. Raises OSError with Windows' code."""
        buf = ctypes.create_string_buffer(data, len(data))   # exactly these bytes: no terminating zero
        blob_in = _BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_ubyte)))
        blob_out = _BLOB()
        if not fn(ctypes.byref(blob_in), None, None, None, None, _CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(blob_out)):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            return ctypes.string_at(blob_out.pbData, blob_out.cbData)
        finally:
            _LocalFree(blob_out.pbData)

    def _hresult(err: int) -> int:
        err &= 0xFFFFFFFF
        return err if err & 0x80000000 or err == 0 else 0x80070000 | (err & 0xFFFF)

    def _open_fd(path: str, access: int, share: int, disposition: int) -> int:
        """CreateFileW with core's sharing, as a file descriptor. Raises OSError
        (FileNotFoundError, PermissionError for a held file...)."""
        h = _CreateFileW(path, access, share, None, disposition, 0x80, None)
        if h is None or h == _INVALID_HANDLE:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            return msvcrt.open_osfhandle(h, (os.O_RDWR if access & _GENERIC_WRITE else os.O_RDONLY) | os.O_BINARY)
        except Exception:
            _CloseHandle(h)
            raise

    def _busy(e: OSError) -> bool:
        return getattr(e, "winerror", None) in _IN_USE

    _fallback_said = False

    def _rename_replacing(src: str, dst: str) -> int:
        """Renames src over dst in one step; 0 or a Windows error. By handle with
        POSIX semantics, which may replace a file whose handles all share delete -
        so the writer can rename over the file while it still holds it, and
        readers keep the old file meanwhile (core's AtomicFile)."""
        global _fallback_said
        h = _CreateFileW(src, _DELETE | _SYNCHRONIZE, _SHARE_READ | _SHARE_WRITE | _SHARE_DELETE,
                         None, _OPEN_EXISTING, 0, None)
        if h is None or h == _INVALID_HANDLE:
            return ctypes.get_last_error()
        try:
            name = os.path.abspath(dst).encode("utf-16-le")
            ptr = ctypes.sizeof(ctypes.c_void_p)
            offset = 2 * ptr + 4          # Flags, RootDirectory (pointer-aligned), FileNameLength
            size = offset + len(name) + 2
            buf = ctypes.create_string_buffer(size)
            ctypes.c_uint32.from_buffer(buf, 0).value = _RENAME_REPLACE_IF_EXISTS | _RENAME_POSIX_SEMANTICS
            ctypes.c_uint32.from_buffer(buf, 2 * ptr).value = len(name)
            ctypes.memmove(ctypes.addressof(buf) + offset, name, len(name))
            if _SetFileInformationByHandle(h, _FILE_RENAME_INFO_EX, buf, size):
                return 0
            error = ctypes.get_last_error()
        finally:
            _CloseHandle(h)
        if error in _IN_USE:
            return error
        # Not supported here (an older Windows, a volume without POSIX renames).
        if not _fallback_said:
            _fallback_said = True
            _log.info("Rename by handle unavailable (Windows error %s); using MoveFileEx, which has to wait for readers.", error)
        return 0 if _MoveFileExW(src, dst, _MOVEFILE_REPLACE_EXISTING | _MOVEFILE_WRITE_THROUGH) else ctypes.get_last_error()

    def _rename_new(src: str, dst: str) -> int:
        return 0 if _MoveFileExW(src, dst, _MOVEFILE_WRITE_THROUGH) else ctypes.get_last_error()

    def protect(plain: str) -> str:
        """The key locked to this Windows account, in base64; "" when Windows refuses.
        The contract every product keeps (§3): the text as UTF-8 without a byte-order
        mark or terminating zero; CryptProtectData, current user,
        CRYPTPROTECT_UI_FORBIDDEN, no entropy; standard base64 with padding."""
        if not plain:
            return ""
        try:
            return base64.b64encode(_crypt(_CryptProtectData, plain.encode("utf-8"))).decode("ascii")
        except OSError as e:
            _log.warning("Windows would not lock a key (error 0x%08X).", _hresult(e.winerror or 0))
            return ""

    def try_unprotect(value: str) -> Tuple[bool, Optional[str], int]:
        """(True, key, 0), or (False, None, Windows' error code - 0 when the value
        is not base64 and never reached Windows)."""
        if not value:
            return False, None, 0
        try:
            raw = base64.b64decode(value, validate=True)
        except (ValueError, TypeError):
            return False, None, 0
        try:
            return True, _crypt(_CryptUnprotectData, raw).decode("utf-8", "replace"), 0
        except OSError as e:
            return False, None, _hresult(e.winerror or 0)


# ─── Reading and writing one file under the lock ─────────────────────────────

def _read_all(fd: int) -> bytes:
    chunks = []
    while True:
        b = os.read(fd, 65536)
        if not b:
            return b"".join(chunks)
        chunks.append(b)


def _read_shared(path: str) -> Optional[bytes]:
    """The file's bytes, or None when there is none. Opened so that no writer can
    change it meanwhile (others may read); a writer's hold is waited out for up
    to a second. Raises OSError when it cannot be opened."""
    for attempt in range(_RETRIES + 1):
        try:
            if sys.platform == "win32":
                fd = _open_fd(path, _GENERIC_READ, _SHARE_READ, _OPEN_EXISTING)
                try:
                    return _read_all(fd)
                finally:
                    os.close(fd)
            with open(path, "rb") as f:   # POSIX writers replace the file in one rename
                return f.read()
        except FileNotFoundError:
            return None
        except OSError as e:
            if sys.platform == "win32" and _busy(e) and attempt < _RETRIES:
                time.sleep(_RETRY_SECONDS)
                continue
            raise
    return None


def _atomic_write(path: str, data: bytes, replace: bool = True) -> str:
    """Writes the file in one step: a temporary file beside it, then renamed over
    it, so the file is the old bytes or the new ones, never neither. Returns
    "written", "exists" (create-only, someone got there first) or "failed"."""
    temp = f"{path}.{uuid.uuid4().hex}.tmp"
    try:
        with open(temp, "xb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        for delay in (0.01, 0.02, 0.04, 0.08, 0.16, None):
            if sys.platform != "win32":
                if replace:
                    os.replace(temp, path)
                    return "written"
                try:
                    os.link(temp, path)   # refuses to overwrite, as a create-only write must
                    return "written"
                except FileExistsError:
                    return "exists"
            error = _rename_replacing(temp, path) if replace else _rename_new(temp, path)
            if error == 0:
                return "written"
            if not replace and error in (_ERROR_FILE_EXISTS, _ERROR_ALREADY_EXISTS):
                return "exists"
            if error not in _IN_USE or delay is None:
                _log.warning("%s not written (Windows error %s); the previous file is unchanged.", os.path.basename(path), error)
                return "failed"
            time.sleep(delay)
    except Exception as e:
        _log.warning("%s not written: %s: %s", os.path.basename(path), type(e).__name__, e)
        return "failed"
    finally:
        try:
            os.remove(temp)   # after a rename there is nothing here; otherwise the cleanup
        except OSError:
            pass
    return "failed"


def _sweep_abandoned(path: str) -> None:
    """Removes temporary files left by a process killed in the middle of a write,
    once they are old enough that no live write can still own them."""
    try:
        folder, name = os.path.split(path)
        cutoff = time.time() - 300
        for entry in os.scandir(folder):
            if entry.name.startswith(name + ".") and entry.name.endswith(".tmp"):
                try:
                    if entry.stat().st_mtime < cutoff:
                        os.remove(entry.path)
                except OSError:
                    pass
    except OSError:
        pass


def _update_file(path: str, parse: Callable[[str], Optional[_Ids]], render: Callable[[_Ids], str],
                 mutate: Callable[[_Ids], bool], set_aside: Optional[Callable[[bytes], Optional[str]]] = None):
    """One read-modify-write under an exclusive open: several products share these
    files, and two of them saving at once must not lose a write. A lock on the
    file, not the process; a second writer waits up to a second.

    Returns (outcome, set_aside_path): outcome True written, False nothing
    needed writing, None could not be written. A file ``parse`` rejects is
    damaged: ``set_aside`` copies its exact bytes beside it first, and nothing
    is written unless that copy is."""
    if not path:
        return None, None
    with _gate:
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
        except OSError:
            return None, None
        _sweep_abandoned(path)
        for attempt in range(_RETRIES + 1):
            try:
                fd = _hold(path)
            except OSError as e:
                if _held_elsewhere(e) and attempt < _RETRIES:
                    time.sleep(_RETRY_SECONDS)
                    continue
                _log.warning("%s not written: %s: %s", os.path.basename(path), type(e).__name__, e)
                return None, None
            if fd is None:          # replaced while we waited (POSIX): open the new one
                continue
            try:
                raw = _read_all(fd)
                ids = parse(_decode(raw))
                damaged = ids is None
                if damaged:
                    ids = _Ids()
                if not mutate(ids):
                    # Nothing to write. An empty file - the one just created on a
                    # computer that had none - is removed while still held, so no
                    # other product can open it in between and lose a write.
                    if not raw:
                        try:
                            os.remove(path)
                        except OSError:
                            pass
                    return False, None
                aside = None
                if damaged:
                    aside = set_aside(raw) if set_aside else None
                    if aside is None:
                        return None, None
                    _log.warning("%s was damaged and has been set aside as %s.", os.path.basename(path), aside)
                data = render(ids).encode("utf-8")
                if _atomic_write(path, data) == "written":
                    return True, aside
                # A volume without POSIX renames cannot replace a file that is
                # open, even by this process: let go and try once more.
                os.close(fd)
                fd = None
                return (True if _atomic_write(path, data) == "written" else None), aside
            except Exception as e:
                _log.warning("%s not written: %s: %s", os.path.basename(path), type(e).__name__, e)
                return None, None
            finally:
                if fd is not None:
                    os.close(fd)
        return None, None


if sys.platform == "win32":
    def _hold(path: str) -> int:
        # Sharing only delete: exclusive against every reader and writer, and it
        # lets the file be renamed over or deleted while this handle holds it.
        return _open_fd(path, _GENERIC_READ | _GENERIC_WRITE, _SHARE_DELETE, _OPEN_ALWAYS)

    def _held_elsewhere(e: OSError) -> bool:
        return _busy(e)
else:
    import fcntl

    def _hold(path: str) -> Optional[int]:
        # An advisory lock on the file itself, waited for. Writers replace the
        # file by rename, so a lock taken on a file that has since been replaced
        # locks nothing: then let go (None) and open the new one.
        fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            for attempt in range(_RETRIES + 1):
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if attempt == _RETRIES:
                        raise
                    time.sleep(_RETRY_SECONDS)
            if os.fstat(fd).st_ino != os.stat(path).st_ino:
                os.close(fd)
                return None
            return fd
        except BaseException:
            try:
                os.close(fd)
            except OSError:
                pass
            raise

    def _held_elsewhere(e: OSError) -> bool:
        return isinstance(e, BlockingIOError)


# ─── The locked copy: a Windows file, or one Keychain item ───────────────────

class _Read(NamedTuple):
    ids: _Ids
    why: str = _FINE
    raw: Optional[str] = None    # a damaged Keychain item's text, to set aside


class _LockedFile:
    """Windows: each value locked by DPAPI, in a file beside config.json."""

    def __init__(self, path: str):
        self.path = path
        self.where = path
        self.holder = "the file holding your locked API keys"

    def read(self) -> _Read:
        try:
            raw = _read_shared(self.path)
        except OSError as e:
            _log.warning("%s could not be opened: %s: %s", os.path.basename(self.path), type(e).__name__, e)
            return _Read(_Ids(), _UNAVAILABLE)
        if raw is None:
            return _Read(_Ids())
        ids = _parse_locked(_decode(raw))
        if ids is None:
            _log.warning("%s is damaged (not valid JSON): every key in it counts as unreadable "
                         "until the next save sets it aside.", os.path.basename(self.path))
            return _Read(_Ids(), _DAMAGED)
        return _Read(ids)

    def update(self, mutate: Callable[[_Ids], bool]):
        return _update_file(self.path, _parse_locked, _render_locked, mutate, self._set_aside)

    def _set_aside(self, raw: bytes) -> Optional[str]:
        """Copies a damaged file's exact bytes beside it, under a name no writer
        uses, while it is still held - never a rename, which would leave a moment
        with no file. Returns that path, or None."""
        stem = os.path.splitext(self.path)[0] + ".damaged-" + datetime.now().strftime("%Y%m%d-%H%M%S")
        for n in range(1, 10):
            aside = stem + ("" if n == 1 else f"-{n}") + ".json"
            outcome = _atomic_write(aside, raw, replace=False)
            if outcome == "written":
                return aside
            if outcome == "failed":
                return None
        return None

    @staticmethod
    def open(value: str) -> Tuple[bool, Optional[str], int]:
        return try_unprotect(value)

    @staticmethod
    def seal(key: str) -> str:
        return protect(key)


class _Keychain:
    """A Mac's Keychain (Linux: the Secret Service), through keyring: one item,
    service "Supervertaler", account "api-keys", holding the whole map with plain
    values. One item rather than one per provider, so that macOS asks at most
    one "allow?" question, not one per key (§3)."""

    SERVICE, MAIN, NEW = "Supervertaler", "api-keys", "api-keys.new"

    def __init__(self, backend):
        self.backend = backend
        self.where = f"{self.SERVICE} / {self.MAIN}"
        self.holder = ("the Keychain item holding your API keys" if sys.platform == "darwin"
                       else "the secret-store item holding your API keys")

    def read(self) -> _Read:
        try:
            raw = self.backend.get_password(self.SERVICE, self.MAIN)
            if raw is None:
                # A save stopped between deleting the main item and adding it
                # again (keyring saves by delete-then-add): the new map is here.
                raw = self.backend.get_password(self.SERVICE, self.NEW)
        except Exception as e:
            _log.warning("The API key item could not be read: %s", type(e).__name__)
            return _Read(_Ids(), _UNAVAILABLE)
        if raw is None:
            return _Read(_Ids())
        ids = _parse_locked(raw)
        if ids is None:
            _log.warning("The API key item is damaged (not valid JSON): every key in it counts as unreadable.")
            return _Read(_Ids(), _DAMAGED, raw)
        return _Read(ids)

    def update(self, mutate: Callable[[_Ids], bool]):
        """Saved the way a file is replaced: the new map first into a second item,
        then the main item, then the second item deleted. Killed in between, the
        keys still read from one of the two."""
        with _gate:
            current = self.read()
            if current.why == _UNAVAILABLE:
                return None, None
            ids = current.ids
            if not mutate(ids):
                return False, None
            aside = None
            try:
                if current.why == _DAMAGED:
                    aside = self.MAIN + ".damaged-" + datetime.now().strftime("%Y%m%d-%H%M%S")
                    self.backend.set_password(self.SERVICE, aside, current.raw or "")
                text = json.dumps({k: v for k, v in ids.items()}, ensure_ascii=False)
                self.backend.set_password(self.SERVICE, self.NEW, text)
                self.backend.set_password(self.SERVICE, self.MAIN, text)
            except Exception as e:
                _log.warning("The API key item was not written: %s", type(e).__name__)
                return None, None
            try:
                self.backend.delete_password(self.SERVICE, self.NEW)
            except Exception:
                pass   # a leftover second item is only read when the main one is missing
            return True, aside

    @staticmethod
    def open(value: str) -> Tuple[bool, Optional[str], int]:
        return True, value, 0

    @staticmethod
    def seal(key: str) -> str:
        return key


def _vault_backend():
    """The Mac's Keychain or Linux's Secret Service through keyring, or None when
    this computer has none Workbench can use. The backend is named rather than
    looked up, so a frozen build that has lost keyring's entry points still finds
    it, and a plain-text "keyring" some other package installed is never used."""
    try:
        if sys.platform == "darwin":
            from keyring.backends import macOS as module
        else:
            from keyring.backends import SecretService as module
        backend = module.Keyring()
        backend.priority     # raises when this computer has no such store
        return backend
    except Exception as e:
        _log.info("No keychain for API keys: %s: %s", type(e).__name__, e)
        return None


def _store():
    """The locked copy on this computer, or None when there is none (no Keychain)."""
    if sys.platform == "win32":
        path = locked_file_path()
        return _LockedFile(path) if path else None
    backend = _vault_backend()
    return _Keychain(backend) if backend is not None else None


def keychain_missing_notice() -> Optional[str]:
    """M7, when this Mac (or Linux computer) has no store for the locked copy; else None."""
    if sys.platform == "win32" or _store() is not None:
        return None
    if sys.platform == "darwin":
        return ("Your Mac's Keychain isn't available to Supervertaler, so your keys stay in "
                "Supervertaler's settings file as before.")
    return ("Your computer's secret store isn't available to Supervertaler, so your keys stay in "
            "Supervertaler's settings file as before.")


# ─── Reading ─────────────────────────────────────────────────────────────────

class Found(NamedTuple):
    status: str = ABSENT
    key: Optional[str] = None
    why: str = _FINE
    where: str = ""
    holder: str = ""


def _read_plain() -> _Ids:
    """The plain file as id → key; empty when absent or it cannot be read."""
    try:
        raw = _read_shared(plain_file_path())
    except Exception as e:
        _log.warning("api-keys.json could not be read: %s", type(e).__name__)
        return _Ids()
    return _parse_plain(_decode(raw)) if raw else _Ids()


def lookup_many(providers: Iterable[str]) -> Dict[str, Found]:
    """Each provider's key by the step-1 read rule, reading each file once.
    Never raises: if something unexpected does, "unreadable" is the honest answer -
    there may well be a key, and "none set" would send the user to paste one
    that is already there."""
    providers = list(providers)
    store = None
    try:
        plain = _read_plain()
        locked = None
        result = {}
        for provider in providers:
            key_id = _id(provider)
            if not key_id:
                result[provider] = Found()
                continue
            have = None if _is_private(key_id) else plain.get(key_id)
            if have and have.strip():
                result[provider] = Found(PRESENT, have)
                continue
            if locked is None:
                store = _store()
                locked = store.read() if store else _Read(_Ids())
            if locked.why != _FINE:
                result[provider] = Found(UNREADABLE, None, locked.why, store.where, store.holder)
                continue
            value = locked.ids.get(key_id)
            if not value or not value.strip():
                result[provider] = Found()
                continue
            ok, key, error = store.open(value)
            if ok:
                result[provider] = Found(PRESENT, key) if key else Found()
                continue
            _log.warning("%s: the %s key cannot be unlocked by this Windows account (%s).",
                         os.path.basename(store.where), key_id,
                         "not base64" if error == 0 else "Windows error 0x%08X" % error)
            result[provider] = Found(UNREADABLE, None, _REFUSED, store.where, store.holder)
        return result
    except Exception as e:
        _log.warning("API key lookup failed: %s: %s", type(e).__name__, e)
        where, holder = (store.where, store.holder) if store else (locked_file_path() or "", "the file holding your locked API keys")
        return {p: Found(UNREADABLE, None, _UNAVAILABLE, where, holder) for p in providers}


def lookup(provider: str) -> Found:
    return lookup_many([provider])[provider]


def get(provider: str) -> Optional[str]:
    """The key, or None when there is none this computer can use (absent or unreadable)."""
    found = lookup(provider)
    return found.key if found.status == PRESENT else None


def status(provider: str) -> str:
    return lookup(provider).status


# ─── What the user is told (core's sentences, design note §7) ────────────────

def when_used(found: Found, provider: str, settings_place: str) -> str:
    """The sentence when an action needs a key it cannot use: M2 for an unreadable
    one, "No API key set ..." for none (the wording that was there before)."""
    name = display_name(provider)
    place = (settings_place or "").strip() or "the settings"
    if found.why == _REFUSED:
        return (f"Can't use your {name} key – it can't be read on this computer (saved under another Windows "
                f"sign-in, or your Windows password was reset). Paste it in again in {place}.")
    if found.why == _DAMAGED:
        return f"Can't use your {name} key – {found.holder} is damaged: {found.where}. Paste the key in again in {place}."
    if found.why == _UNAVAILABLE:
        return f"Can't use your {name} key – {found.holder} could not be opened: {found.where}. Try again in a moment."
    return f"No API key set for {name}. Add one in {place}."


def missing_key_message(provider: str, settings_place: str) -> Optional[str]:
    """M2 or "No API key set ..."; None when the key is there."""
    found = lookup(provider)
    return None if found.status == PRESENT else when_used(found, provider, settings_place)


def unreadable_note(found: Found, provider: str) -> Optional[str]:
    """M1, beside the key box, when the key cannot be unlocked here; else None.
    The box stays empty, and the stored key is kept until a new one is pasted."""
    if found.status != UNREADABLE:
        return None
    name = display_name(provider)
    if found.why == _DAMAGED:
        return (f"Your {name} key can't be read – {found.holder} is damaged: {found.where}. "
                f"Paste the key in again; the damaged file will be set aside, not deleted.")
    if found.why == _UNAVAILABLE:
        return f"Your {name} key can't be read just now – {found.holder} could not be opened: {found.where}."
    return (f"Your {name} key can't be read on this computer – it was saved under another Windows sign-in, "
            f"or your Windows password was reset. Paste the key in again; nothing else is affected.")


def _damaged_notice(store, aside: str) -> str:
    """M6, core's wording."""
    holder = store.holder[0].upper() + store.holder[1:]
    return f"{holder} was damaged and has been set aside as {aside}. Please paste your keys in again."


# ─── Writing ─────────────────────────────────────────────────────────────────

def _update_plain(mutate: Callable[[_Ids], bool]):
    return _update_file(plain_file_path(), _parse_plain, _render_plain, mutate)[0]


def set_key(provider: str, key: Optional[str]) -> bool:
    """Saves one key (empty removes it): the plain file, then the locked copy.
    False only when the save did not take.

    A key already stored with this exact value needs no write, and that is a
    success. In step 1 the plain file decides, so a key written there is saved
    even if the locked write fails: that is logged, and the next start's sync
    repeats it. A removal needs both, because a key left in the locked copy
    would still be read. A private id is in the locked copy only.

    An empty save never removes a key this account cannot unlock: the user was
    never shown it - its box was empty - and it stays until a new key replaces it."""
    key_id = _id(provider)
    if not key_id:
        return False
    wanted = (key or "").strip() or None
    shared = not _is_private(key_id)

    if shared:
        def plain_mutate(ids: _Ids) -> bool:
            if wanted is None:
                return ids.remove(key_id)
            if ids.get(key_id) == wanted:
                return False
            ids.set(key_id, wanted)
            return True
        if _update_plain(plain_mutate) is None:
            return False

    store = _store()
    if store is None:
        return shared         # no Keychain: the plain copies are all there is (M7)
    if _set_locked(store, key_id, wanted):
        return True
    if not shared or wanted is None:
        return False
    _log.warning("The %s key is saved in api-keys.json but not yet locked to this computer; "
                 "the next start locks it.", key_id)
    return True


def _set_locked(store, key_id: str, wanted: Optional[str]) -> bool:
    refused = [False]

    def mutate(ids: _Ids) -> bool:
        refused[0] = False
        value = ids.get(key_id)
        ok, current, _ = store.open(value) if value else (False, None, 0)
        if wanted is None:
            return ok and ids.remove(key_id)   # an unreadable entry is kept
        if ok and current == wanted:
            return False
        sealed = store.seal(wanted)
        if not sealed:
            refused[0] = True
            return False
        ids.set(key_id, sealed)
        return True

    written, _ = store.update(mutate)
    if refused[0]:
        _log.warning("Windows would not lock the %s key.", key_id)
    return written is not None and not refused[0]


class Sync(NamedTuple):
    locked: int = 0
    failed: bool = False
    notice: Optional[str] = None   # M6, when a damaged locked copy was set aside


def sync_locked(extra: Optional[Dict[str, str]] = None) -> Sync:
    """The step-1 start-up sync (§5): every key in the plain file - and in
    ``extra``, a product's own private keys - is copied into the locked copy
    where that lacks it or holds something else (a different key, or one this
    account cannot unlock). It adds and replaces, never removes, so a damaged,
    empty or half-written plain file cannot empty the locked copy.

    The plain file is held - readable by others, not writable - until the
    locked copy is written. Otherwise a key another product saves in between
    (plain file, then locked copy) could be overwritten in the locked copy by
    the older value read here. Plain file first, then locked copy: the order
    every writer takes, so none waits on another."""
    store = _store()
    path = plain_file_path()
    if store is None:
        return Sync()
    with _gate:
        try:
            fd = _hold_for_sync(path)
        except OSError as e:
            _log.warning("Keys not locked to this computer: %s: %s", type(e).__name__, e)
            return Sync(failed=True)
        try:
            wanted = _Ids()
            if fd is not None:
                for k, v in _parse_plain(_decode(_read_all(fd))).items():
                    wanted.set(k, v)
            for k, v in (extra or {}).items():
                wanted.set(k, v)
            if not len(wanted):
                return Sync()
            counted = [0, False]

            def mutate(ids: _Ids) -> bool:
                counted[0], counted[1] = 0, False
                for key_id, value in wanted.items():
                    want = (value or "").strip()
                    if not want:
                        continue
                    have = ids.get(key_id)
                    if have:
                        ok, current, _ = store.open(have)
                        if ok and current == want:
                            continue
                    sealed = store.seal(want)
                    if not sealed:
                        counted[1] = True
                        continue
                    ids.set(key_id, sealed)
                    counted[0] += 1
                return counted[0] > 0

            written, aside = store.update(mutate)
            failed = counted[1] or written is None
            if failed:
                _log.warning("Not every key in api-keys.json could be locked to this computer; the next start tries again.")
            return Sync(counted[0] if written else 0, failed, _damaged_notice(store, aside) if aside else None)
        finally:
            if fd is not None:
                os.close(fd)


def _hold_for_sync(path: str) -> Optional[int]:
    """The plain file, open so that others may read it but not write it; None
    when there is none."""
    for attempt in range(_RETRIES + 1):
        try:
            if sys.platform == "win32":
                return _open_fd(path, _GENERIC_READ, _SHARE_READ, _OPEN_EXISTING)
            fd = os.open(path, os.O_RDONLY)
            try:
                fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
            except BaseException:
                os.close(fd)
                raise
            return fd
        except FileNotFoundError:
            return None
        except OSError as e:
            if _held_elsewhere(e) and attempt < _RETRIES:
                time.sleep(_RETRY_SECONDS)
                continue
            raise
    return None


def migrate_from(own: Dict[str, str]) -> Optional[Tuple[int, List[str]]]:
    """Workbench joining the shared keys (decision 1): adds each of its own keys
    that neither copy has yet, to the plain file (the sync then locks it). Never
    overwrites - once either copy holds a key for a provider, readable here or
    not, that one wins. Returns (added, ids whose own key differs from the
    shared one) - for M8 - or None when nothing could be decided: a locked copy
    this account cannot read may hold newer keys, so nothing is added past it.

    ``own``: shared id → key."""
    store = _store()
    locked = store.read() if store else _Read(_Ids())
    if locked.why != _FINE:
        return None
    added = [0]
    differing: List[str] = []

    def mutate(ids: _Ids) -> bool:
        added[0] = 0
        differing.clear()
        for key_id, key in own.items():
            key = (key or "").strip()
            if not key:
                continue
            have = (ids.get(key_id) or "").strip()
            if have:
                if have != key:
                    differing.append(key_id)
                continue
            value = locked.ids.get(key_id)
            if value is not None:
                ok, current, _ = store.open(value)
                if ok and current and current != key:
                    differing.append(key_id)
                continue
            ids.set(key_id, key)
            added[0] += 1
        return added[0] > 0

    if _update_plain(mutate) is None:
        return None
    return added[0], list(differing)


def set_private_keys(prefix: str, keys: Dict[str, str]) -> bool:
    """One product's own keys, such as its named profiles' (ids starting with
    ``prefix``, made by ``private_id``): afterwards the locked copy holds exactly
    ``keys`` under that prefix - new and changed ones written, others removed
    (a profile removed, or its key cleared). As with ``set_key``, an entry this
    account cannot unlock is kept until a new key replaces it. One write for
    all of them. False when the locked copy could not be written."""
    store = _store()
    if store is None:
        return True    # no Keychain: settings.json's copy is all there is (M7)
    wanted = _Ids()
    for k, v in keys.items():
        if (v or "").strip():
            wanted.set(k, v.strip())
    refused = [False]

    def mutate(ids: _Ids) -> bool:
        refused[0] = False
        changed = False
        for key_id, value in ids.items():
            if _fold(key_id).startswith(_fold(prefix)) and not wanted.has(key_id):
                if store.open(value)[0]:
                    changed = ids.remove(key_id) or changed
        for key_id, want in wanted.items():
            have = ids.get(key_id)
            if have:
                ok, current, _ = store.open(have)
                if ok and current == want:
                    continue
            sealed = store.seal(want)
            if not sealed:
                refused[0] = True
                continue
            ids.set(key_id, sealed)
            changed = True
        return changed

    written, _ = store.update(mutate)
    return written is not None and not refused[0]
