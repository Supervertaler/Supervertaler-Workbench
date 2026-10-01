"""
Platform Helpers for Supervertaler
===================================
Cross-platform utilities for file operations, window management,
subprocess flags, global hotkeys, and keystroke automation.

Replaces scattered platform-specific code with a single, consistent API.
"""

import sys
import os
import subprocess
import shutil
import contextlib
import time
from pathlib import Path
from typing import Optional, Callable, Dict, List


# ---------------------------------------------------------------------------
# Platform detection constants
# ---------------------------------------------------------------------------
IS_WINDOWS = sys.platform == 'win32'
IS_MACOS = sys.platform == 'darwin'
IS_LINUX = sys.platform.startswith('linux')


# ---------------------------------------------------------------------------
# Cross-platform file/folder opening
# ---------------------------------------------------------------------------
def open_file(path: str) -> bool:
    """Open a file with the system's default application.

    Cross-platform replacement for os.startfile().
    Returns True on success, False on failure.
    """
    try:
        path_str = str(path)
        if IS_WINDOWS:
            os.startfile(path_str)
        elif IS_MACOS:
            subprocess.run(['open', path_str], check=True)
        else:
            subprocess.run(['xdg-open', path_str], check=True)
        return True
    except Exception as e:
        print(f"[platform_helpers] Failed to open file {path}: {e}")
        return False


def open_folder(path: str) -> bool:
    """Open a folder in the system file manager.

    If *path* is a file, opens its containing folder.
    """
    path_obj = Path(path)
    folder = path_obj.parent if path_obj.is_file() else path_obj
    return open_file(str(folder))


def open_folder_and_select(path: str) -> bool:
    """Open a folder in the file manager and highlight/select the given file.

    Falls back to open_folder() on unsupported platforms.
    """
    try:
        path_str = str(path)
        if IS_WINDOWS:
            subprocess.run(['explorer', '/select,', path_str])
            return True
        elif IS_MACOS:
            subprocess.run(['open', '-R', path_str])
            return True
        else:
            # xdg-open on the parent folder (no select support)
            return open_folder(path_str)
    except Exception as e:
        print(f"[platform_helpers] Failed to open folder for {path}: {e}")
        return False


# ---------------------------------------------------------------------------
# Cross-platform subprocess creation flags
# ---------------------------------------------------------------------------
def get_hidden_subprocess_flags() -> dict:
    """Return subprocess kwargs to hide the console window on Windows.

    Usage::

        subprocess.Popen([...], **get_hidden_subprocess_flags())
    """
    if IS_WINDOWS and hasattr(subprocess, 'CREATE_NO_WINDOW'):
        return {'creationflags': subprocess.CREATE_NO_WINDOW}
    return {}


@contextlib.contextmanager
def hide_subprocess_console_windows():
    """Context manager that suppresses console flashes from any subprocess
    spawned by code running inside the ``with`` block on Windows.

    Wraps ``subprocess.Popen`` so any process started while the manager is
    active gets ``CREATE_NO_WINDOW`` added to its creationflags. Restored
    on exit. No-op on non-Windows platforms.

    Use this when calling third-party libraries that internally spawn
    helpers via ``subprocess.run([...])`` without giving us a way to pass
    creationflags ourselves – e.g. OpenAI's whisper library shelling out
    to ffmpeg for audio decoding. With Supervertaler running console-less
    (Supervertaler.exe / pythonw.exe), every such helper would otherwise
    flash a black cmd window for ~100 ms.

    Usage::

        with hide_subprocess_console_windows():
            result = whisper_model.transcribe(audio_path)
    """
    if not (IS_WINDOWS and hasattr(subprocess, "CREATE_NO_WINDOW")):
        yield
        return

    _orig_popen = subprocess.Popen
    _no_window = subprocess.CREATE_NO_WINDOW

    class _NoFlashPopen(_orig_popen):
        def __init__(self, *args, **kwargs):
            kwargs["creationflags"] = (kwargs.get("creationflags", 0) or 0) | _no_window
            super().__init__(*args, **kwargs)

    subprocess.Popen = _NoFlashPopen
    try:
        yield
    finally:
        subprocess.Popen = _orig_popen


# ---------------------------------------------------------------------------
# Cross-platform window activation
# ---------------------------------------------------------------------------
def activate_window_by_title(title: str) -> bool:
    """Best-effort attempt to bring a window with *title* to the foreground.

    Returns True if the operation was attempted (not guaranteed to succeed).
    On Windows we fall back to Qt's own raise/activate methods via the caller.
    """
    try:
        if IS_WINDOWS:
            # Use ctypes to find and activate the window.
            # Plain SetForegroundWindow fails when our app is in the background
            # (Windows prevents background apps from stealing focus).  The
            # workaround is AttachThreadInput: temporarily attach our thread to
            # the foreground window's thread so the OS treats our call as coming
            # from the foreground process.
            import ctypes
            from ctypes import wintypes

            user32 = ctypes.windll.user32
            kernel32 = ctypes.windll.kernel32
            EnumWindows = user32.EnumWindows
            GetWindowTextW = user32.GetWindowTextW
            GetWindowTextLengthW = user32.GetWindowTextLengthW
            IsWindowVisible = user32.IsWindowVisible
            SetForegroundWindow = user32.SetForegroundWindow
            ShowWindow = user32.ShowWindow
            GetForegroundWindow = user32.GetForegroundWindow
            GetWindowThreadProcessId = user32.GetWindowThreadProcessId
            GetCurrentThreadId = kernel32.GetCurrentThreadId
            AttachThreadInput = user32.AttachThreadInput
            BringWindowToTop = user32.BringWindowToTop
            SW_RESTORE = 9
            SW_SHOW = 5

            WNDENUMPROC = ctypes.WINFUNCTYPE(
                ctypes.c_bool, wintypes.HWND, wintypes.LPARAM
            )
            target_hwnd = None

            def _enum_cb(hwnd, _lparam):
                nonlocal target_hwnd
                if IsWindowVisible(hwnd):
                    length = GetWindowTextLengthW(hwnd)
                    if length > 0:
                        buf = ctypes.create_unicode_buffer(length + 1)
                        GetWindowTextW(hwnd, buf, length + 1)
                        if title.lower() in buf.value.lower():
                            target_hwnd = hwnd
                            return False  # stop enumeration
                return True

            EnumWindows(WNDENUMPROC(_enum_cb), 0)
            if target_hwnd:
                # Attach to the foreground thread so SetForegroundWindow succeeds
                fg_hwnd = GetForegroundWindow()
                fg_thread = GetWindowThreadProcessId(fg_hwnd, None)
                our_thread = GetCurrentThreadId()
                attached = False
                if fg_thread != our_thread:
                    attached = AttachThreadInput(fg_thread, our_thread, True)

                ShowWindow(target_hwnd, SW_RESTORE)
                BringWindowToTop(target_hwnd)
                SetForegroundWindow(target_hwnd)

                if attached:
                    AttachThreadInput(fg_thread, our_thread, False)
                return True
            return False

        elif IS_MACOS:
            subprocess.run(
                ['osascript', '-e',
                 f'tell application "System Events" to set frontmost of '
                 f'(first process whose name contains "{title}") to true'],
                capture_output=True
            )
            return True

        else:
            # Linux – try wmctrl, then xdotool
            wmctrl = shutil.which('wmctrl')
            if wmctrl:
                subprocess.run([wmctrl, '-a', title], capture_output=True)
                return True
            xdotool = shutil.which('xdotool')
            if xdotool:
                subprocess.run(
                    [xdotool, 'search', '--name', title, 'windowactivate'],
                    capture_output=True
                )
                return True
            return False

    except Exception as e:
        print(f"[platform_helpers] Window activation failed: {e}")
        return False


def get_foreground_window():
    """Return an opaque handle for the current foreground window.

    On Windows returns HWND (int), on macOS/Linux returns the window title
    (str) or None if detection fails.  Pass the result to
    ``activate_foreground_window()`` to restore focus later.
    """
    try:
        if IS_WINDOWS:
            import ctypes
            return ctypes.windll.user32.GetForegroundWindow()
        elif IS_MACOS:
            result = subprocess.run(
                ['osascript', '-e',
                 'tell application "System Events" to get name of '
                 'first process whose frontmost is true'],
                capture_output=True, text=True
            )
            name = result.stdout.strip()
            return name if name else None
        else:
            xdotool = shutil.which('xdotool')
            if xdotool:
                result = subprocess.run(
                    [xdotool, 'getactivewindow'], capture_output=True, text=True
                )
                wid = result.stdout.strip()
                return wid if wid else None
            return None
    except Exception:
        return None


def activate_foreground_window(handle):
    """Re-activate a window previously captured by ``get_foreground_window()``.

    Uses the same AttachThreadInput trick on Windows for reliable activation.
    Does NOT call ShowWindow(SW_RESTORE) – that would un-maximize a maximized
    window.  The target window is already visible; we just need to give it focus.
    """
    if handle is None:
        return False
    try:
        if IS_WINDOWS:
            import ctypes
            import time
            user32 = ctypes.windll.user32
            kernel32 = ctypes.windll.kernel32

            SW_RESTORE = 9
            VK_MENU = 0x12          # ALT
            KEYEVENTF_KEYUP = 0x0002

            def _is_foreground():
                # Both values come from GetForegroundWindow with ctypes' default
                # (signed 32-bit) marshalling, so they compare consistently.
                return user32.GetForegroundWindow() == handle

            # Already focused? Nothing to do.
            if _is_foreground():
                return True

            # Un-minimize ONLY if the target is minimized – never SW_RESTORE an
            # already-visible window, which would un-maximize a maximized editor.
            try:
                if user32.IsIconic(handle):
                    user32.ShowWindow(handle, SW_RESTORE)
            except Exception:
                pass

            our_thread = kernel32.GetCurrentThreadId()

            # Windows silently refuses SetForegroundWindow from a background
            # process under the foreground-lock timeout / a failed attach /
            # timing. So attempt it, VERIFY the switch actually took, and retry
            # with escalating nudges. This replaces the old fire-once-and-always-
            # return-True behaviour that made intermittent paste-into-nowhere
            # failures invisible to the caller.
            for attempt in range(3):
                fg_hwnd = user32.GetForegroundWindow()
                fg_thread = user32.GetWindowThreadProcessId(fg_hwnd, None)
                attached = False
                if fg_thread and fg_thread != our_thread:
                    attached = user32.AttachThreadInput(fg_thread, our_thread, True)

                # On retries, a stray ALT tap releases the foreground lock so the
                # OS honours SetForegroundWindow from the background. (Benign for
                # the target app; the key is pressed and released immediately.)
                if attempt > 0:
                    try:
                        user32.keybd_event(VK_MENU, 0, 0, 0)
                        user32.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, 0)
                    except Exception:
                        pass

                user32.BringWindowToTop(handle)
                user32.SetForegroundWindow(handle)

                if attached:
                    user32.AttachThreadInput(fg_thread, our_thread, False)

                if _is_foreground():
                    return True
                time.sleep(0.03)  # let the async switch settle, then re-check

            # Honestly report failure so the caller can react (e.g. retry the
            # whole paste) instead of firing Ctrl+V into nowhere.
            return _is_foreground()

        elif IS_MACOS:
            # handle is the process name
            subprocess.run(
                ['osascript', '-e',
                 f'tell application "System Events" to set frontmost of '
                 f'(first process whose name is "{handle}") to true'],
                capture_output=True
            )
            return True

        else:
            # handle is xdotool window ID
            xdotool = shutil.which('xdotool')
            if xdotool:
                subprocess.run(
                    [xdotool, 'windowactivate', str(handle)],
                    capture_output=True
                )
                return True
            return False

    except Exception as e:
        print(f"[platform_helpers] activate_foreground_window failed: {e}")
        return False


# ---------------------------------------------------------------------------
# Native clipboard access (Windows) – event-loop-independent
# ---------------------------------------------------------------------------
#
# The Win32 clipboard is a single globally-contended resource: OpenClipboard
# fails whenever ANY other process momentarily holds it, and on a typical
# Windows 10/11 machine something frequently does – the Win+V clipboard
# history, OneDrive, TeamViewer, Office, Trados' own clipboard hooks. Every
# access here therefore retries OpenClipboard with a short backoff instead of
# giving up on the first BUSY, the same strategy dedicated clipboard managers
# (Ditto, CopyQ) use.

_CF_UNICODETEXT = 13
_GMEM_MOVEABLE = 0x0002


def _open_clipboard_with_retry(user32, retries: int = 15,
                               delay_s: float = 0.02) -> bool:
    """OpenClipboard with retry/backoff. ~300 ms worst case at defaults."""
    for _ in range(retries):
        if user32.OpenClipboard(None):
            return True
        time.sleep(delay_s)
    return False


def get_clipboard_sequence_number() -> Optional[int]:
    """Windows' kernel-maintained clipboard change counter, or None off-
    Windows / on failure.

    Incremented on every clipboard content change. Capture it before a
    synthetic Ctrl+C, then poll until it moves to *know* the copy landed –
    a deterministic replacement for hoping a fixed sleep was long enough.
    Same trick verifies our own writes.
    """
    if not IS_WINDOWS:
        return None
    try:
        import ctypes
        return int(ctypes.windll.user32.GetClipboardSequenceNumber())
    except Exception:
        return None


def get_clipboard_text_native() -> Optional[str]:
    """Read CF_UNICODETEXT straight from the Win32 clipboard.

    Returns None off-Windows, when no text is on the clipboard, or when the
    clipboard stayed locked through the (short) retry window – callers should
    treat None as "couldn't verify", not "empty".
    """
    if not IS_WINDOWS:
        return None
    try:
        import ctypes
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        user32.GetClipboardData.restype = ctypes.c_void_p
        user32.GetClipboardData.argtypes = [ctypes.c_uint]
        kernel32.GlobalLock.restype = ctypes.c_void_p
        kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
        kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]

        if not _open_clipboard_with_retry(user32, retries=5):
            return None
        try:
            handle = user32.GetClipboardData(_CF_UNICODETEXT)
            if not handle:
                return None
            ptr = kernel32.GlobalLock(handle)
            if not ptr:
                return None
            try:
                return ctypes.wstring_at(ptr)
            finally:
                kernel32.GlobalUnlock(handle)
        finally:
            user32.CloseClipboard()
    except Exception:
        return None


def set_clipboard_text(text) -> bool:
    """Put ``text`` on the Windows clipboard as a MATERIALISED copy via the
    native Win32 API (CF_UNICODETEXT), retrying through contention and
    verifying the write with a read-back.

    Why this exists: Qt's ``QClipboard.setText`` uses OLE delayed rendering –
    the data stays owned by our process and is only handed over when a consumer
    reads it, via a callback that must be serviced by our Qt main thread. If
    that thread is busy at the moment a consumer reads, the consumer pastes
    nothing – intermittently. Writing a real copy with SetClipboardData removes
    the callback entirely: the OS holds the bytes, so a read succeeds no matter
    what our event loop is doing.

    Hardened (Phase 1 of the clipboard-reliability work): the old version
    tried OpenClipboard exactly once, so any transient holder (Win+V history,
    OneDrive, Trados hooks – i.e. precisely the machines that need the native
    write most) bounced us to the Qt fallback and its delayed-rendering
    failure mode. Now OpenClipboard is retried with backoff, and after a
    successful write the text is read back; a mismatch (another writer raced
    us) triggers a rewrite. Returns True only for a verified-or-unverifiable
    successful write; False means the clipboard genuinely could not be
    written and callers must NOT paste (they'd paste stale content).
    """
    if not IS_WINDOWS or text is None:
        return False
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32

        user32.OpenClipboard.argtypes = [wintypes.HWND]
        user32.OpenClipboard.restype = wintypes.BOOL
        user32.EmptyClipboard.restype = wintypes.BOOL
        user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
        user32.SetClipboardData.restype = wintypes.HANDLE
        user32.CloseClipboard.restype = wintypes.BOOL
        kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
        kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
        kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
        kernel32.GlobalFree.argtypes = [ctypes.c_void_p]
        kernel32.GlobalLock.restype = ctypes.c_void_p
        kernel32.GlobalLock.argtypes = [ctypes.c_void_p]

        buf = ctypes.create_unicode_buffer(text)  # UTF-16, NUL-terminated
        size = ctypes.sizeof(buf)                  # bytes, incl. terminator

        for _write_attempt in range(3):
            if not _open_clipboard_with_retry(user32):
                continue
            wrote = False
            try:
                user32.EmptyClipboard()
                h_mem = kernel32.GlobalAlloc(_GMEM_MOVEABLE, size)
                if h_mem:
                    ptr = kernel32.GlobalLock(h_mem)
                    if ptr:
                        ctypes.memmove(ptr, buf, size)
                        kernel32.GlobalUnlock(h_mem)
                        # On success the system takes ownership of h_mem;
                        # on failure we free it.
                        if user32.SetClipboardData(_CF_UNICODETEXT, h_mem):
                            wrote = True
                        else:
                            kernel32.GlobalFree(h_mem)
                    else:
                        kernel32.GlobalFree(h_mem)
            finally:
                user32.CloseClipboard()

            if wrote:
                # Read-back verification. None = clipboard locked again
                # before we could re-open it – the write itself succeeded,
                # so treat unverifiable as success rather than looping.
                readback = get_clipboard_text_native()
                if readback is None or readback == text:
                    return True
                # Mismatch: another writer raced us between our close and
                # the read-back. Loop and write again.
            time.sleep(0.02)
        return False
    except Exception as e:
        print(f"[platform_helpers] set_clipboard_text failed: {e}")
        return False


# ---------------------------------------------------------------------------
# Direct synthetic keystrokes (Windows) – in-process SendInput
# ---------------------------------------------------------------------------
#
# Replaces the AutoHotkey-subprocess paste path. Writing a fresh .ahk file to
# %TEMP% and executing it per paste invited Defender scan latency (freshly
# written script files get scanned at execution time), 50–200 ms of process
# spawn, a PowerShell SendKeys fallback when AHK isn't installed, and a
# Python→AHK hand-off gap during which the foreground window could drift.
# SendInput injects the keystroke in-process, immediately after Python itself
# has verified the target window is foreground – no hand-off, no temp file,
# no external dependency.

if IS_WINDOWS:
    import ctypes as _ct
    from ctypes import wintypes as _wt

    _ULONG_PTR = _ct.c_size_t  # pointer-sized on both 32/64-bit
    _INPUT_KEYBOARD = 1
    _KEYEVENTF_KEYUP = 0x0002

    class _KEYBDINPUT(_ct.Structure):
        _fields_ = (("wVk", _wt.WORD), ("wScan", _wt.WORD),
                    ("dwFlags", _wt.DWORD), ("time", _wt.DWORD),
                    ("dwExtraInfo", _ULONG_PTR))

    class _MOUSEINPUT(_ct.Structure):
        # Needed in the union even though we never send mouse input:
        # MOUSEINPUT is the largest member, and SendInput validates cbSize
        # against the full INPUT struct.
        _fields_ = (("dx", _wt.LONG), ("dy", _wt.LONG),
                    ("mouseData", _wt.DWORD), ("dwFlags", _wt.DWORD),
                    ("time", _wt.DWORD), ("dwExtraInfo", _ULONG_PTR))

    class _HARDWAREINPUT(_ct.Structure):
        _fields_ = (("uMsg", _wt.DWORD), ("wParamL", _wt.WORD),
                    ("wParamH", _wt.WORD))

    class _INPUTUNION(_ct.Union):
        _fields_ = (("ki", _KEYBDINPUT), ("mi", _MOUSEINPUT),
                    ("hi", _HARDWAREINPUT))

    class _INPUT(_ct.Structure):
        _fields_ = (("type", _wt.DWORD), ("union", _INPUTUNION))


VK_SHIFT, VK_CONTROL, VK_MENU = 0x10, 0x11, 0x12
VK_LWIN, VK_RWIN = 0x5B, 0x5C
VK_V = 0x56
_MODIFIER_VKS = (VK_SHIFT, VK_CONTROL, VK_MENU, VK_LWIN, VK_RWIN)


def send_key_events(events) -> bool:
    """Inject ``events`` – a sequence of ``(vk_code, is_keyup)`` tuples – as
    ONE SendInput batch, so no real keystroke can interleave between them.

    Returns True only if every event was injected. 0 injected usually means
    input to the foreground window is blocked (UIPI/elevation mismatch, or a
    secure desktop is up).
    """
    if not IS_WINDOWS or not events:
        return False
    try:
        arr = (_INPUT * len(events))()
        for i, (vk, is_up) in enumerate(events):
            arr[i].type = _INPUT_KEYBOARD
            arr[i].union.ki.wVk = vk
            arr[i].union.ki.dwFlags = _KEYEVENTF_KEYUP if is_up else 0
        injected = _ct.windll.user32.SendInput(
            len(events), arr, _ct.sizeof(_INPUT))
        return injected == len(events)
    except Exception as e:
        print(f"[platform_helpers] SendInput failed: {e}")
        return False


def physically_held_modifiers() -> list:
    """VK codes of the modifier keys the user is physically holding right
    now (GetAsyncKeyState). Empty list off-Windows or on failure."""
    if not IS_WINDOWS:
        return []
    try:
        user32 = _ct.windll.user32
        return [vk for vk in _MODIFIER_VKS
                if user32.GetAsyncKeyState(vk) & 0x8000]
    except Exception:
        return []


_KEYEVENTF_UNICODE = 0x0004


def send_unicode_text(text: str) -> bool:
    """Type ``text`` into the foreground window as Unicode key events
    (VK_PACKET), independent of the keyboard layout. One SendInput batch."""
    if not IS_WINDOWS or not text:
        return False
    try:
        raw = text.encode('utf-16-le')
        units = [int.from_bytes(raw[i:i + 2], 'little') for i in range(0, len(raw), 2)]
        arr = (_INPUT * (2 * len(units)))()
        for i, unit in enumerate(units):
            for j, key_up in enumerate((False, True)):
                ev = arr[2 * i + j]
                ev.type = _INPUT_KEYBOARD
                ev.union.ki.wVk = 0
                ev.union.ki.wScan = unit
                ev.union.ki.dwFlags = _KEYEVENTF_UNICODE | (_KEYEVENTF_KEYUP if key_up else 0)
        injected = _ct.windll.user32.SendInput(len(arr), arr, _ct.sizeof(_INPUT))
        return injected == len(arr)
    except Exception as e:
        print(f"[platform_helpers] SendInput (unicode) failed: {e}")
        return False


# ---------------------------------------------------------------------------
# AltGr and Ctrl+Alt global hotkeys (Windows) – issue #243
# ---------------------------------------------------------------------------
#
# Windows reports the AltGr key as Ctrl+Alt, so a hotkey registered for
# Ctrl+Alt+L with RegisterHotKey also fires on AltGr+L – and swallows the key.
# On a Polish keyboard AltGr+L types "ł", AltGr+C "ć", AltGr+O "ó"; on a German
# one AltGr+Q types "@". With Supervertaler running, those characters could not
# be typed in ANY application. When such a hotkey fires while the right Alt key
# is physically down and the foreground window's keyboard layout turns
# AltGr+key into a character, that character is typed instead of running the
# hotkey. Left Ctrl + left Alt still runs it, and so does AltGr on a layout
# (such as US English) where AltGr+key types nothing.

VK_RMENU, VK_LCONTROL, VK_CAPITAL = 0xA5, 0xA2, 0x14


def is_altgr_chord(mods: int) -> bool:
    """RegisterHotKey modifiers that AltGr also produces: exactly Ctrl+Alt."""
    return (mods & (_MOD_CONTROL | _MOD_ALT)) == (_MOD_CONTROL | _MOD_ALT) and \
        not mods & (_MOD_SHIFT | _MOD_WIN)


class _WinKeyboard:
    """The two questions altgr_character() asks Windows, on private function
    prototypes (a separate user32 handle, so no shared argtypes change)."""

    def __init__(self):
        from ctypes import wintypes as wt
        u = _ct.WinDLL('user32')
        u.GetForegroundWindow.restype = wt.HWND
        u.GetWindowThreadProcessId.argtypes = (wt.HWND, _ct.c_void_p)
        u.GetWindowThreadProcessId.restype = wt.DWORD
        u.GetKeyboardLayout.argtypes = (wt.DWORD,)
        u.GetKeyboardLayout.restype = _ct.c_void_p
        u.MapVirtualKeyExW.argtypes = (wt.UINT, wt.UINT, _ct.c_void_p)
        u.MapVirtualKeyExW.restype = wt.UINT
        u.ToUnicodeEx.argtypes = (wt.UINT, wt.UINT, _ct.POINTER(_ct.c_ubyte), wt.LPWSTR,
                                  _ct.c_int, wt.UINT, _ct.c_void_p)
        u.ToUnicodeEx.restype = _ct.c_int
        u.GetAsyncKeyState.argtypes = (_ct.c_int,)
        u.GetAsyncKeyState.restype = _ct.c_short
        u.GetKeyState.argtypes = (_ct.c_int,)
        u.GetKeyState.restype = _ct.c_short
        self._u = u

    def right_alt_down(self) -> bool:
        return bool(self._u.GetAsyncKeyState(VK_RMENU) & 0x8000)

    def altgr_text(self, vk: int) -> str:
        """What AltGr+``vk`` types in the foreground window's layout ('' if nothing)."""
        u = self._u
        tid = u.GetWindowThreadProcessId(u.GetForegroundWindow(), None)
        hkl = u.GetKeyboardLayout(tid)
        state = (_ct.c_ubyte * 256)()
        for key in (VK_CONTROL, VK_LCONTROL, VK_MENU, VK_RMENU):
            state[key] = 0x80
        if u.GetKeyState(VK_CAPITAL) & 1:
            state[VK_CAPITAL] = 0x01
        buf = _ct.create_unicode_buffer(8)
        scan = u.MapVirtualKeyExW(vk, 0, hkl)  # MAPVK_VK_TO_VSC
        # wFlags 0x4: leave the kernel keyboard state (dead keys) untouched.
        n = u.ToUnicodeEx(vk, scan, state, buf, len(buf), 0x4, hkl)
        return buf.value[:n] if n > 0 else ''


_win_keyboard = None


def altgr_character(mods: int, vk: int, keyboard=None):
    """The character to type instead of running a Ctrl+Alt global hotkey that
    was really AltGr+key (see above), or None to run the hotkey as usual.
    ``keyboard`` is for tests; it defaults to the live Windows keyboard."""
    global _win_keyboard
    if vk is None or not is_altgr_chord(mods):
        return None
    try:
        if keyboard is None:
            if not IS_WINDOWS:
                return None
            if _win_keyboard is None:
                _win_keyboard = _WinKeyboard()
            keyboard = _win_keyboard
        if not keyboard.right_alt_down():
            return None
        text = keyboard.altgr_text(vk)
    except Exception as e:
        print(f"[GlobalHotkeyManager] AltGr check failed: {e}")
        return None
    if len(text) == 1 and text.isprintable() and not text.isspace():
        return text
    return None


def wait_for_modifier_release(timeout_ms: int = 1000) -> bool:
    """Block until no modifier key (Ctrl/Alt/Shift/Win) is physically held,
    or the timeout expires. Returns True when all are up.

    A synthetic Ctrl+V sent while the user still holds Alt (from the
    Ctrl+Alt+C hotkey) arrives as Ctrl+Alt+V and most apps ignore it.
    Waiting for the physical release – usually well under 200 ms once the
    user has picked a clip – is more robust than injecting key-ups, because
    a still-held key's auto-repeat immediately re-presses it.
    """
    if not IS_WINDOWS:
        return True
    deadline = time.monotonic() + timeout_ms / 1000.0
    while time.monotonic() < deadline:
        if not physically_held_modifiers():
            return True
        time.sleep(0.015)
    return not physically_held_modifiers()


# ---------------------------------------------------------------------------
# Window-class inspection (Windows) – used to pick a paste strategy
# ---------------------------------------------------------------------------
#
# Console / terminal emulators typically do NOT paste on Ctrl+V (they use
# Ctrl+Shift+V, right-click, or Enter). The clipboard manager's "Auto"
# paste mode consults ``is_terminal_like_window`` and types the text out
# character-by-character for these targets instead of firing a Ctrl+V that
# would silently do nothing.
_TERMINAL_WINDOW_CLASSES = (
    'consolewindowclass',             # conhost: cmd.exe, powershell.exe
    'cascadia_hosting_window_class',  # Windows Terminal
    'virtualconsoleclass',            # ConEmu / Cmder
    'mintty',                         # Git Bash, Cygwin, MSYS2
    'putty',                          # PuTTY / KiTTY
)


def get_window_class(handle) -> str:
    """Return the Win32 window-class name for *handle* (an HWND).

    Empty string on non-Windows, on failure, or for a ``None`` handle.
    """
    if not IS_WINDOWS or handle is None:
        return ''
    try:
        import ctypes
        buf = ctypes.create_unicode_buffer(256)
        ctypes.windll.user32.GetClassNameW(handle, buf, 256)
        return buf.value or ''
    except Exception:
        return ''


def is_terminal_like_window(handle) -> bool:
    """True if *handle* is a console/terminal-style window that usually
    ignores a plain Ctrl+V paste.

    Windows-only; returns False on other platforms (where the clipboard
    manager's paste-back handle is a window title, not an HWND) and for
    any window whose class we don't recognise. A caller that has
    explicitly chosen "always type" still gets typing regardless.
    """
    cls = get_window_class(handle).lower()
    if not cls:
        return False
    return any(term in cls for term in _TERMINAL_WINDOW_CLASSES)


# ---------------------------------------------------------------------------
# Windows integrity levels (UIPI) – can we inject input into this window?
# ---------------------------------------------------------------------------
#
# Windows' User Interface Privilege Isolation blocks a process from sending
# synthetic input (SendInput / typed keystrokes, incl. Ctrl+C / Ctrl+V) to a
# window whose process runs at a HIGHER integrity level. So a non-elevated
# Workbench cannot paste into an app started with "Run as administrator"
# (e.g. Trados Studio 2026 under its admin-activation workaround) – the OS
# silently drops the keystroke, and there is NO software workaround: only
# running Workbench elevated too fixes it. The clipboard manager uses
# ``paste_target_needs_elevation`` to detect this and surface a clear reason
# instead of failing invisibly.
#
# Integrity RIDs: 0x1000 low, 0x2000 medium (normal user app),
# 0x3000 high (elevated / admin), 0x4000 system.

def get_process_integrity_level(pid) -> Optional[int]:
    """Return the Windows integrity-level RID for process *pid*
    (e.g. 0x2000 medium, 0x3000 high/elevated), or None if it can't be
    determined. Windows-only."""
    if not IS_WINDOWS or not pid:
        return None
    try:
        import ctypes
        from ctypes import wintypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        TOKEN_QUERY = 0x0008
        TokenIntegrityLevel = 25

        kernel32 = ctypes.windll.kernel32
        advapi32 = ctypes.windll.advapi32

        # Pin arg/return types – on 64-bit Python, unpinned HANDLE/pointer
        # values are truncated to 32-bit ints, which corrupts the handles
        # and the SID pointers below.
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        advapi32.OpenProcessToken.argtypes = [
            wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
        advapi32.GetTokenInformation.argtypes = [
            wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
            wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
        advapi32.GetSidSubAuthorityCount.restype = ctypes.POINTER(ctypes.c_ubyte)
        advapi32.GetSidSubAuthorityCount.argtypes = [ctypes.c_void_p]
        advapi32.GetSidSubAuthority.restype = ctypes.POINTER(wintypes.DWORD)
        advapi32.GetSidSubAuthority.argtypes = [ctypes.c_void_p, wintypes.DWORD]

        h_proc = kernel32.OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
        if not h_proc:
            return None
        try:
            h_token = wintypes.HANDLE()
            if not advapi32.OpenProcessToken(
                    h_proc, TOKEN_QUERY, ctypes.byref(h_token)):
                return None
            try:
                size = wintypes.DWORD(0)
                # First call sizes the buffer (returns FALSE / insufficient buffer).
                advapi32.GetTokenInformation(
                    h_token, TokenIntegrityLevel, None, 0, ctypes.byref(size))
                if not size.value:
                    return None
                buf = ctypes.create_string_buffer(size.value)
                if not advapi32.GetTokenInformation(
                        h_token, TokenIntegrityLevel, buf, size, ctypes.byref(size)):
                    return None
                # buf holds TOKEN_MANDATORY_LABEL { SID_AND_ATTRIBUTES Label };
                # its first pointer-sized field is the PSID.
                sid = ctypes.cast(buf, ctypes.POINTER(ctypes.c_void_p))[0]
                if not sid:
                    return None
                count = advapi32.GetSidSubAuthorityCount(sid)[0]
                rid = advapi32.GetSidSubAuthority(sid, count - 1)[0]
                return int(rid)
            finally:
                kernel32.CloseHandle(h_token)
        finally:
            kernel32.CloseHandle(h_proc)
    except Exception:
        return None


def get_window_integrity_level(handle) -> Optional[int]:
    """Integrity-level RID of the process owning window *handle* (HWND),
    or None. Windows-only."""
    if not IS_WINDOWS or not handle:
        return None
    try:
        import ctypes
        from ctypes import wintypes
        pid = wintypes.DWORD()
        ctypes.windll.user32.GetWindowThreadProcessId(handle, ctypes.byref(pid))
        if not pid.value:
            return None
        return get_process_integrity_level(pid.value)
    except Exception:
        return None


def get_current_process_integrity_level() -> Optional[int]:
    """Integrity-level RID of the current (Workbench) process, or None."""
    if not IS_WINDOWS:
        return None
    return get_process_integrity_level(os.getpid())


def paste_target_needs_elevation(handle) -> bool:
    """True if window *handle* belongs to a HIGHER-integrity process than
    ours, so UIPI will silently block synthetic input (Ctrl+V or typed
    text) to it unless Workbench is also elevated.

    Windows-only. Returns False on any failure or ambiguity so a paste is
    never suppressed on a false positive – this is a diagnostic hint, not
    a gate.
    """
    if not IS_WINDOWS or not handle:
        return False
    try:
        ours = get_current_process_integrity_level()
        theirs = get_window_integrity_level(handle)
        if ours is None or theirs is None:
            return False
        return theirs > ours
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Cross-platform Global Hotkey Manager
# ---------------------------------------------------------------------------
#
# On Windows: uses the native RegisterHotKey API (runs its own message pump
#   in a background thread – works perfectly alongside PyQt6).
# On macOS/Linux: uses pynput GlobalHotKeys.
# ---------------------------------------------------------------------------

# Windows virtual-key codes for RegisterHotKey
_VK_MAP = {
    'a': 0x41, 'b': 0x42, 'c': 0x43, 'd': 0x44, 'e': 0x45, 'f': 0x46,
    'g': 0x47, 'h': 0x48, 'i': 0x49, 'j': 0x4A, 'k': 0x4B, 'l': 0x4C,
    'm': 0x4D, 'n': 0x4E, 'o': 0x4F, 'p': 0x50, 'q': 0x51, 'r': 0x52,
    's': 0x53, 't': 0x54, 'u': 0x55, 'v': 0x56, 'w': 0x57, 'x': 0x58,
    'y': 0x59, 'z': 0x5A,
    '0': 0x30, '1': 0x31, '2': 0x32, '3': 0x33, '4': 0x34,
    '5': 0x35, '6': 0x36, '7': 0x37, '8': 0x38, '9': 0x39,
    'f1': 0x70, 'f2': 0x71, 'f3': 0x72, 'f4': 0x73, 'f5': 0x74,
    'f6': 0x75, 'f7': 0x76, 'f8': 0x77, 'f9': 0x78, 'f10': 0x79,
    'f11': 0x7A, 'f12': 0x7B,
    'space': 0x20, 'enter': 0x0D, 'return': 0x0D, 'tab': 0x09,
    'escape': 0x1B, 'esc': 0x1B, 'backspace': 0x08, 'delete': 0x2E,
    # Numpad keys – distinguished from their main-keyboard counterparts
    # by the "num" prefix when captured via KeySequenceEdit (Qt's
    # KeypadModifier). RegisterHotKey treats numpad as a separate VK
    # family from the main row, so binding "Num+" alone only fires for
    # the numpad + key, not for Shift+= on the main keyboard.
    'num+': 0x6B, 'numadd': 0x6B, 'numplus': 0x6B,
    'numpad+': 0x6B, 'keypad+': 0x6B,
    'num-': 0x6D, 'numsub': 0x6D, 'numminus': 0x6D,
    'numpad-': 0x6D, 'keypad-': 0x6D,
    'num*': 0x6A, 'nummul': 0x6A, 'numpad*': 0x6A,
    'num/': 0x6F, 'numdiv': 0x6F, 'numpad/': 0x6F,
    'num.': 0x6E, 'numdot': 0x6E, 'numdecimal': 0x6E,
    'num0': 0x60, 'num1': 0x61, 'num2': 0x62, 'num3': 0x63, 'num4': 0x64,
    'num5': 0x65, 'num6': 0x66, 'num7': 0x67, 'num8': 0x68, 'num9': 0x69,
    'numenter': 0x0D,
}

_MOD_ALT = 0x0001
_MOD_CONTROL = 0x0002
_MOD_SHIFT = 0x0004
_MOD_WIN = 0x0008
# MOD_NOREPEAT (Win7+) makes RegisterHotKey fire WM_HOTKEY *only* on the
# initial press, not at the system keyboard-repeat rate. Critical for
# push-to-talk hold-to-talk dictation – without it, holding the hotkey
# spawns a fresh recording every ~500 ms (the system's keyboard repeat
# delay), each ~200 ms long, which Whisper then hallucinates over.
_MOD_NOREPEAT = 0x4000
_WM_HOTKEY = 0x0312


class GlobalHotkeyManager:
    """Cross-platform global hotkey registration.

    Backends:

    * Windows: native ``RegisterHotKey`` API (works with PyQt6).
    * macOS:   ``NSEvent.addGlobalMonitorForEventsMatchingMask:handler:``
               via PyObjC. Avoids pynput's TSM-on-background-thread crash
               on macOS 26+ by running handlers on the main runloop.
    * Linux:   ``pynput.keyboard.GlobalHotKeys``.

    Usage::

        manager = GlobalHotkeyManager()
        manager.register('ctrl+alt+l', on_superlookup)
        manager.register('ctrl+alt+q', on_quicktrans)
        success = manager.start()
        ...
        manager.stop()
    """

    def __init__(self):
        self._hotkeys: Dict[str, Callable] = {}  # shortcut string -> callback
        self._running = False
        self._backend = None  # 'winapi' | 'pynput' | 'nsevent'

        # Windows-specific
        self._win_thread = None
        self._win_thread_id = None
        self._win_hotkey_ids: Dict[int, Callable] = {}  # hotkey_id -> callback
        self._win_hotkey_keys: Dict[int, tuple] = {}  # hotkey_id -> (mods, vk), for AltGr
        self._next_id = 1
        self.failed_hotkeys: list = []  # Shortcuts that failed to register

        # pynput-specific
        self._listener = None
        self._pynput_hotkeys: Dict[str, Callable] = {}

        # macOS-specific (NSEvent monitor)
        self._mac_backend = None  # _MacNSEventHotkey instance
        # True when the monitor is installed but macOS hasn't granted
        # Accessibility, so no keystrokes from other apps will reach it (#188)
        self.permission_missing = False

    # -- public API ----------------------------------------------------------

    @property
    def is_available(self) -> bool:
        if IS_WINDOWS:
            return True  # RegisterHotKey is always available
        if IS_MACOS:
            return _MacNSEventHotkey.is_available()
        # Linux: pynput
        try:
            from pynput.keyboard import GlobalHotKeys  # noqa: F401
            return True
        except ImportError:
            return False

    @property
    def running(self) -> bool:
        return self._running

    def last_trigger_keycode(self) -> Optional[int]:
        """macOS: the virtual keycode of the key that fired the most recent
        hotkey, so push-to-talk can watch that physical key for its release
        whatever the keyboard layout. None elsewhere."""
        if self._mac_backend is not None:
            return self._mac_backend.last_keycode
        return None

    def register(self, shortcut: str, callback: Callable) -> bool:
        """Register a global hotkey.

        *shortcut* uses the format ``'ctrl+alt+l'``.  The *callback* will
        be invoked from a **background thread** – callers must dispatch to
        the Qt main thread themselves (e.g. via ``QTimer.singleShot``).
        """
        self._hotkeys[shortcut.lower()] = callback
        return True

    def start(self) -> bool:
        """Start listening for registered hotkeys.  Returns ``True`` on success."""
        if not self._hotkeys:
            return False

        if IS_WINDOWS:
            return self._start_winapi()
        if IS_MACOS:
            return self._start_macos()
        return self._start_pynput()  # Linux

    def stop(self):
        """Stop listening for hotkeys."""
        if self._backend == 'winapi':
            self._stop_winapi()
        elif self._backend == 'pynput':
            self._stop_pynput()
        elif self._backend == 'nsevent':
            if self._mac_backend is not None:
                try:
                    self._mac_backend.stop()
                except Exception as e:
                    print(f"[GlobalHotkeyManager] NSEvent stop error: {e}")
                self._mac_backend = None
        self._running = False

    # -- macOS NSEvent backend -----------------------------------------------

    def _start_macos(self) -> bool:
        """Register hotkeys via NSEvent monitors.

        Bypasses pynput because pynput's macOS listener calls Carbon's
        TSMGetInputSourceProperty from a background CFRunLoop thread,
        which macOS 26+ aborts with EXC_BREAKPOINT. NSEvent monitors
        installed from the Qt main thread fire on the main runloop, so
        no TSM violation occurs.
        """
        if not _MacNSEventHotkey.is_available():
            print("[GlobalHotkeyManager] PyObjC (pyobjc-framework-Cocoa) "
                  "not available – install it to enable global hotkeys "
                  "on macOS:  pip install pyobjc-framework-Cocoa")
            return False

        backend = _MacNSEventHotkey()
        for shortcut, callback in self._hotkeys.items():
            if not backend.register(shortcut, callback):
                self.failed_hotkeys.append(shortcut)
        if not backend.start():
            return False
        self._mac_backend = backend
        self._running = True
        self._backend = 'nsevent'
        # The monitor installs fine without Accessibility – it just never
        # receives keystrokes – so ask macOS directly
        self.permission_missing = mac_accessibility_trusted() is False
        return True

    # -- Windows RegisterHotKey backend --------------------------------------

    def _start_winapi(self) -> bool:
        """Register hotkeys using the Windows RegisterHotKey API."""
        import ctypes
        import ctypes.wintypes
        import threading

        # Parse shortcuts and assign IDs
        self._win_hotkey_ids.clear()
        self._win_hotkey_keys.clear()
        registrations = []
        for shortcut, callback in self._hotkeys.items():
            mods, vk = self._parse_shortcut_winapi(shortcut)
            if vk is None:
                print(f"[GlobalHotkeyManager] Unknown key in shortcut: {shortcut}")
                continue
            hk_id = self._next_id
            self._next_id += 1
            self._win_hotkey_ids[hk_id] = callback
            self._win_hotkey_keys[hk_id] = (mods, vk)
            registrations.append((hk_id, mods, vk, shortcut))

        if not registrations:
            return False

        ready_event = threading.Event()
        success_flag = [False]

        def _message_pump():
            user32 = ctypes.windll.user32
            self._win_thread_id = ctypes.windll.kernel32.GetCurrentThreadId()

            all_ok = True
            for hk_id, mods, vk, shortcut in registrations:
                # Always OR in MOD_NOREPEAT – we never want WM_HOTKEY to
                # auto-fire while a key is held. Push-to-talk dictation
                # depends on a single press = single recording session;
                # toggle-style hotkeys (Ctrl+Alt+K etc.) don't benefit
                # from auto-repeat either.
                mods_norepeat = mods | _MOD_NOREPEAT
                if not user32.RegisterHotKey(None, hk_id, mods_norepeat, vk):
                    print(f"[GlobalHotkeyManager] Failed to register {shortcut} "
                          f"(may be in use by another application)")
                    self.failed_hotkeys.append(shortcut)
                    all_ok = False
                else:
                    print(f"[GlobalHotkeyManager] Registered {shortcut} (WinAPI)")

            success_flag[0] = all_ok or len(self._win_hotkey_ids) > 0
            ready_event.set()

            if not success_flag[0]:
                return

            # Message loop – blocks until PostThreadMessage(WM_QUIT)
            msg = ctypes.wintypes.MSG()
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                if msg.message == _WM_HOTKEY:
                    hk_id = msg.wParam
                    cb = self._win_hotkey_ids.get(hk_id)
                    # AltGr+key arrives as this Ctrl+Alt hotkey: type the
                    # character the user meant instead (issue #243).
                    char = altgr_character(*self._win_hotkey_keys.get(hk_id, (0, None)))
                    if char:
                        send_unicode_text(char)
                        continue
                    if cb:
                        try:
                            cb()
                        except Exception as e:
                            print(f"[GlobalHotkeyManager] Callback error: {e}")

            # Unregister on exit
            for hk_id in self._win_hotkey_ids:
                user32.UnregisterHotKey(None, hk_id)

        self._win_thread = threading.Thread(target=_message_pump, daemon=True,
                                            name="GlobalHotkeyManager-WinAPI")
        self._win_thread.start()
        ready_event.wait(timeout=2)

        if success_flag[0]:
            self._running = True
            self._backend = 'winapi'
            return True
        return False

    def _stop_winapi(self):
        """Stop the WinAPI message pump thread."""
        if self._win_thread_id:
            import ctypes
            # Post WM_QUIT to the thread's message queue
            ctypes.windll.user32.PostThreadMessageW(
                self._win_thread_id, 0x0012, 0, 0  # WM_QUIT
            )
            if self._win_thread:
                self._win_thread.join(timeout=2)
            self._win_thread = None
            self._win_thread_id = None

    @staticmethod
    def _parse_shortcut_winapi(shortcut: str):
        """Parse ``'ctrl+alt+l'`` into (modifiers, vk_code) for RegisterHotKey.

        Handles a trailing ``+`` as part of the last key, so "Num+" stays
        as a single token rather than splitting into ['Num', ''] and
        losing the symbol that disambiguates "numpad plus" from "numpad"
        alone. Same trick for "Numpad+" / "Keypad+" if written that way.
        """
        # Split, then re-attach a trailing empty part as a '+' suffix on
        # the previous token. "Num+" → ['Num', ''] → ['Num+'].
        parts = [p.strip() for p in shortcut.lower().split('+')]
        while len(parts) >= 2 and parts[-1] == '':
            parts.pop()
            if parts:
                parts[-1] = parts[-1] + '+'

        mods = 0
        vk = None
        for part in parts:
            if part in ('ctrl', 'control'):
                mods |= _MOD_CONTROL
            elif part == 'alt':
                mods |= _MOD_ALT
            elif part == 'shift':
                mods |= _MOD_SHIFT
            elif part in ('win', 'super', 'cmd'):
                mods |= _MOD_WIN
            else:
                vk = _VK_MAP.get(part)
        return mods, vk

    # -- pynput backend (macOS / Linux) --------------------------------------

    def _start_pynput(self) -> bool:
        """Register hotkeys using pynput GlobalHotKeys (Linux only).

        macOS used to share this path but was switched to ``_start_macos``
        because pynput's listener calls Carbon's TSMGetInputSourceProperty
        from a background CFRunLoop thread, which macOS 26+ aborts with
        EXC_BREAKPOINT (TSM hard-asserts main-thread).
        """
        try:
            from pynput.keyboard import GlobalHotKeys
        except ImportError:
            print("[GlobalHotkeyManager] pynput not installed – "
                  "global hotkeys unavailable")
            return False

        self._pynput_hotkeys.clear()
        for shortcut, callback in self._hotkeys.items():
            pynput_key = self._convert_shortcut_pynput(shortcut)
            self._pynput_hotkeys[pynput_key] = callback

        try:
            self._listener = GlobalHotKeys(self._pynput_hotkeys)
            self._listener.daemon = True
            self._listener.start()
            self._running = True
            self._backend = 'pynput'
            registered = ', '.join(self._pynput_hotkeys.keys())
            print(f"[GlobalHotkeyManager] Started (pynput) – hotkeys: {registered}")
            if IS_MACOS:
                print("[GlobalHotkeyManager] macOS note: global hotkeys require "
                      "Accessibility permission (System Settings → Privacy & Security "
                      "→ Accessibility). If hotkeys don't work, check this setting.")
            return True
        except Exception as e:
            print(f"[GlobalHotkeyManager] pynput failed to start: {e}")
            if IS_MACOS:
                print("[GlobalHotkeyManager] macOS: grant Accessibility permission "
                      "to this app in System Settings → Privacy & Security → Accessibility")
            return False

    def _stop_pynput(self):
        """Stop the pynput listener."""
        if self._listener:
            try:
                self._listener.stop()
            except Exception:
                pass
            self._listener = None

    @staticmethod
    def _convert_shortcut_pynput(shortcut: str) -> str:
        """Convert ``'ctrl+alt+l'`` to pynput ``'<ctrl>+<alt>+l'``."""
        parts = shortcut.lower().split('+')
        converted: List[str] = []
        for part in parts:
            part = part.strip()
            if part in ('ctrl', 'control'):
                converted.append('<ctrl>')
            elif part == 'alt':
                converted.append('<alt>')
            elif part == 'shift':
                converted.append('<shift>')
            elif part in ('cmd', 'win', 'super'):
                converted.append('<cmd>')
            else:
                converted.append(part)
        return '+'.join(converted)


# ---------------------------------------------------------------------------
# macOS NSEvent global hotkey backend
# ---------------------------------------------------------------------------
# macOS keys by name → the character NSEvent reports for them
_MAC_NAMED_KEYS = {
    'space': ' ', 'tab': '\t', 'enter': '\r', 'return': '\r',
    'escape': '\x1b', 'esc': '\x1b', 'backspace': '\x7f',
    'up': '\uf700', 'down': '\uf701', 'left': '\uf702', 'right': '\uf703',
    'insert': '\uf727', 'ins': '\uf727', 'delete': '\uf728', 'del': '\uf728',
    'home': '\uf729', 'end': '\uf72b', 'pageup': '\uf72c', 'pagedown': '\uf72d',
    **{f'f{i}': chr(0xF703 + i) for i in range(1, 21)},   # NSF1FunctionKey = U+F704
}

# Virtual keycodes of the US (ANSI) layout → the character on that key.
# Used only when the active layout types a non-Latin character (Russian,
# Greek, Hebrew …), so ⌘⌥L still means the key where L is on a US
# keyboard – as macOS itself does for ⌘ shortcuts on those layouts.
_MAC_ANSI_KEYCODES = {
    0x00: 'a', 0x01: 's', 0x02: 'd', 0x03: 'f', 0x04: 'h', 0x05: 'g', 0x06: 'z',
    0x07: 'x', 0x08: 'c', 0x09: 'v', 0x0B: 'b', 0x0C: 'q', 0x0D: 'w', 0x0E: 'e',
    0x0F: 'r', 0x10: 'y', 0x11: 't', 0x12: '1', 0x13: '2', 0x14: '3', 0x15: '4',
    0x16: '6', 0x17: '5', 0x18: '=', 0x19: '9', 0x1A: '7', 0x1B: '-', 0x1C: '8',
    0x1D: '0', 0x1E: ']', 0x1F: 'o', 0x20: 'u', 0x21: '[', 0x22: 'i', 0x23: 'p',
    0x25: 'l', 0x26: 'j', 0x27: "'", 0x28: 'k', 0x29: ';', 0x2A: '\\', 0x2B: ',',
    0x2C: '/', 0x2D: 'n', 0x2E: 'm', 0x2F: '.', 0x32: '`',
}

MAC_ACCESSIBILITY_HINT = (
    "Allow Supervertaler (or Terminal / iTerm2, when you start it from a "
    "terminal) in System Settings → Privacy & Security → Accessibility, "
    "then restart Supervertaler.")
MAC_ACCESSIBILITY_SETTINGS_URL = (
    "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility")


def _event_chars(event) -> str:
    """The key's character on the current layout, without Shift or other
    modifiers applied (so ⌘⇧1 reads as "1", not "!"). Falls back to
    charactersIgnoringModifiers, which keeps Shift, before macOS 10.15."""
    try:
        chars = event.charactersByApplyingModifiers_(0)
        if chars:
            return str(chars)
    except Exception:
        pass
    chars = event.charactersIgnoringModifiers()
    return str(chars) if chars else ""


def mac_event_key(chars: str, keycode: int) -> Optional[str]:
    """The key to compare with a registered shortcut's key: the typed
    character, lower-cased – or, when the layout types a non-Latin
    letter, the character on that key of a US keyboard."""
    if not chars:
        return _MAC_ANSI_KEYCODES.get(keycode)
    ch = chars.lower()
    if len(ch) == 1 and ord(ch) > 0x7F and not 0xF700 <= ord(ch) <= 0xF8FF:
        return _MAC_ANSI_KEYCODES.get(keycode, ch)
    return ch


def mac_accessibility_trusted(prompt: bool = False) -> Optional[bool]:
    """Whether macOS lets this process watch keystrokes in other apps
    (Accessibility). None when it can't be determined (not macOS, or the
    framework can't be loaded). ``prompt`` also has macOS show its own
    dialog and list the app in the Accessibility settings."""
    if not IS_MACOS:
        return None
    if prompt:
        try:
            from ApplicationServices import (  # type: ignore
                AXIsProcessTrustedWithOptions, kAXTrustedCheckOptionPrompt)
            return bool(AXIsProcessTrustedWithOptions({kAXTrustedCheckOptionPrompt: True}))
        except Exception:
            pass
    try:
        import ctypes
        ax = ctypes.cdll.LoadLibrary(
            '/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices')
        if not prompt:
            ax.AXIsProcessTrusted.restype = ctypes.c_bool
            ax.AXIsProcessTrusted.argtypes = []
            return bool(ax.AXIsProcessTrusted())
        # The same with plain ctypes when PyObjC's ApplicationServices
        # wrapper isn't installed: {kAXTrustedCheckOptionPrompt: kCFBooleanTrue}
        cf = ctypes.cdll.LoadLibrary(
            '/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation')
        vp = ctypes.c_void_p
        cf.CFDictionaryCreate.restype = vp
        cf.CFDictionaryCreate.argtypes = [vp, vp, vp, ctypes.c_long, vp, vp]
        cf.CFRelease.argtypes = [vp]
        keys = (vp * 1)(vp.in_dll(ax, 'kAXTrustedCheckOptionPrompt').value)
        values = (vp * 1)(vp.in_dll(cf, 'kCFBooleanTrue').value)
        options = cf.CFDictionaryCreate(
            None, keys, values, 1,
            ctypes.addressof(ctypes.c_char.in_dll(cf, 'kCFTypeDictionaryKeyCallBacks')),
            ctypes.addressof(ctypes.c_char.in_dll(cf, 'kCFTypeDictionaryValueCallBacks')))
        ax.AXIsProcessTrustedWithOptions.restype = ctypes.c_bool
        ax.AXIsProcessTrustedWithOptions.argtypes = [vp]
        try:
            return bool(ax.AXIsProcessTrustedWithOptions(options))
        finally:
            cf.CFRelease(options)
    except Exception:
        return None


class _MacNSEventHotkey:
    """macOS global hotkey backend using NSEvent monitors via PyObjC.

    Why this exists: pynput's macOS listener invokes Carbon's
    TSMGetInputSourceProperty from a background CFRunLoop thread, and
    macOS 26+ hard-asserts that TSM calls happen on the main thread,
    crashing the process with EXC_BREAKPOINT. NSEvent monitors installed
    on the Qt main thread fire on the main runloop, sidestepping the
    issue entirely.

    Limitations:
      * Monitor-only — does not consume the keystroke. Other apps
        receiving the same hotkey still respond (a non-issue for our
        ⌃⌘L/M/K combos because nothing else binds them).
      * Only the *global* monitor is installed (fires when Supervertaler
        is NOT frontmost). When Supervertaler IS frontmost, Qt's
        QShortcut handles the same key via the local-shortcut path. This
        avoids double-firing.

    Requires:
      * ``pyobjc-framework-Cocoa`` installed.
      * Accessibility permission on whichever binary launched Python.
        For Terminal launches, that's Terminal.app (or iTerm2.app).
        For the bundled .app, it's Supervertaler itself.
    """

    def __init__(self):
        self._hotkeys: Dict[str, tuple] = {}  # shortcut_lower -> (mods_int, char_lower, callback)
        self._global_monitor = None
        self.last_keycode: Optional[int] = None  # keyCode of the last hotkey that fired

    @staticmethod
    def is_available() -> bool:
        try:
            import AppKit  # noqa: F401
            return True
        except ImportError:
            try:
                import Cocoa  # noqa: F401
                return True
            except ImportError:
                return False

    def register(self, shortcut: str, callback: Callable) -> bool:
        flags, char = self._parse(shortcut)
        if char is None or flags == 0:
            print(f"[MacNSEvent] Could not parse shortcut: {shortcut!r}")
            return False
        self._hotkeys[shortcut.lower()] = (flags, char, callback)
        return True

    def start(self) -> bool:
        try:
            from AppKit import (
                NSEvent,
                NSEventMaskKeyDown,
                NSEventModifierFlagShift,
                NSEventModifierFlagControl,
                NSEventModifierFlagOption,
                NSEventModifierFlagCommand,
            )
        except ImportError:
            try:
                from Cocoa import (  # type: ignore
                    NSEvent,
                    NSEventMaskKeyDown,
                    NSEventModifierFlagShift,
                    NSEventModifierFlagControl,
                    NSEventModifierFlagOption,
                    NSEventModifierFlagCommand,
                )
            except ImportError:
                print("[MacNSEvent] PyObjC not available")
                return False

        mod_mask = (NSEventModifierFlagShift |
                    NSEventModifierFlagControl |
                    NSEventModifierFlagOption |
                    NSEventModifierFlagCommand)
        hotkeys = self._hotkeys

        def _global_handler(event):
            try:
                event_mods = int(event.modifierFlags()) & mod_mask
                if not any(flags == event_mods for flags, _c, _cb in hotkeys.values()):
                    return
                keycode = int(event.keyCode())
                ch = mac_event_key(_event_chars(event), keycode)
                if ch is None:
                    return
                for shortcut, (flags, target_char, callback) in hotkeys.items():
                    if event_mods == flags and ch == target_char:
                        self.last_keycode = keycode
                        try:
                            callback()
                        except Exception as e:
                            print(f"[MacNSEvent] callback error for {shortcut}: {e}")
                        return
            except Exception as e:
                print(f"[MacNSEvent] dispatch error: {e}")

        self._global_monitor = NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(
            NSEventMaskKeyDown, _global_handler
        )
        if self._global_monitor is None:
            print("[MacNSEvent] Failed to install global monitor. " + MAC_ACCESSIBILITY_HINT)
            return False

        registered = ', '.join(self._hotkeys.keys())
        print(f"[MacNSEvent] Started — global hotkeys: {registered}")
        if mac_accessibility_trusted() is False:
            # The monitor is in place but gets no keystrokes until allowed
            print("[MacNSEvent] Accessibility permission not granted – the hotkeys "
                  "won't fire from other apps. " + MAC_ACCESSIBILITY_HINT)
        return True

    def stop(self):
        try:
            from AppKit import NSEvent
        except ImportError:
            try:
                from Cocoa import NSEvent  # type: ignore
            except ImportError:
                return
        if self._global_monitor is not None:
            try:
                NSEvent.removeMonitor_(self._global_monitor)
            except Exception:
                pass
            self._global_monitor = None

    @staticmethod
    def _parse(shortcut: str):
        """Parse e.g. ``'ctrl+cmd+l'`` → ``(modifier_flags, 'l')``.

        Returns ``(0, None)`` on failure.
        """
        try:
            from AppKit import (
                NSEventModifierFlagShift,
                NSEventModifierFlagControl,
                NSEventModifierFlagOption,
                NSEventModifierFlagCommand,
            )
        except ImportError:
            try:
                from Cocoa import (  # type: ignore
                    NSEventModifierFlagShift,
                    NSEventModifierFlagControl,
                    NSEventModifierFlagOption,
                    NSEventModifierFlagCommand,
                )
            except ImportError:
                return 0, None

        # Named keys → the character NSEvent reports for them: function
        # and arrow keys come as Unicode private-use characters
        # (NSF1FunctionKey = U+F704 …).
        named_keys = _MAC_NAMED_KEYS

        flags = 0
        char = None
        for raw in shortcut.lower().split('+'):
            part = raw.strip()
            # Modifier mapping follows Qt's macOS convention so that the
            # global hotkey fires on the SAME physical keystroke that
            # local QShortcut binds to. Qt swaps Ctrl↔Cmd on macOS so a
            # cross-platform "Ctrl+L" QShortcut fires on ⌘L on Mac; we
            # mirror that here so a stored "Ctrl+L" registers as ⌘L
            # globally on Mac too. "Meta" maps the other way (Control ⌃),
            # again matching Qt's native handling.
            if part in ('ctrl', 'control'):
                flags |= NSEventModifierFlagCommand          # Qt Ctrl = Mac ⌘
            elif part == 'meta':
                flags |= NSEventModifierFlagControl          # Qt Meta = Mac ⌃
            elif part in ('alt', 'option'):
                flags |= NSEventModifierFlagOption           # Qt Alt = Mac ⌥
            elif part == 'shift':
                flags |= NSEventModifierFlagShift
            elif part in ('cmd', 'super', 'win'):
                # Explicit "cmd" / cross-platform aliases also mean Mac ⌘.
                flags |= NSEventModifierFlagCommand
            elif len(part) == 1:
                char = part
            elif part in named_keys:
                char = named_keys[part]
            elif part:
                print(f"[MacNSEvent] Unsupported key in shortcut: {part!r}")
                return 0, None
        return flags, char


# ---------------------------------------------------------------------------
# Cross-platform Keystroke Sender (using pynput)
# ---------------------------------------------------------------------------
class CrossPlatformKeySender:
    """Send keystrokes programmatically.

    On Windows, uses AutoHotkey (proven reliable for cross-process keystroke
    injection) with a PowerShell ``SendKeys`` fallback.

    On macOS/Linux, uses ``pynput.keyboard.Controller``.
    """

    _ahk_exe: Optional[str] = None   # cached AHK path (class-level)
    _ahk_searched: bool = False

    def __init__(self):
        self._controller = None
        self._Key = None
        # Only need pynput Controller on Linux (macOS uses osascript,
        # Windows uses AHK/PowerShell)
        if IS_LINUX:
            try:
                from pynput.keyboard import Controller, Key
                self._controller = Controller()
                self._Key = Key
            except ImportError:
                print("[CrossPlatformKeySender] pynput not installed")
            except Exception as e:
                print(f"[CrossPlatformKeySender] pynput init error: {e}")

    @property
    def is_available(self) -> bool:
        if IS_WINDOWS or IS_MACOS:
            return True  # AHK/PowerShell on Windows, osascript on macOS
        return self._controller is not None  # Linux needs pynput

    # -- AHK path discovery (Windows) ----------------------------------------

    @classmethod
    def _find_ahk(cls) -> Optional[str]:
        """Locate AutoHotkey on Windows.  Result is cached."""
        if cls._ahk_searched:
            return cls._ahk_exe
        cls._ahk_searched = True

        # shutil.which covers PATH
        found = shutil.which('AutoHotkey')
        if found:
            cls._ahk_exe = found
            print(f"[CrossPlatformKeySender] AHK found on PATH: {found}")
            return cls._ahk_exe

        # Common installation directories
        username = os.environ.get('USERNAME', '')
        candidates = [
            r"C:\Program Files\AutoHotkey\v2\AutoHotkey.exe",
            r"C:\Program Files\AutoHotkey\v2\AutoHotkey64.exe",
            r"C:\Program Files\AutoHotkey\AutoHotkey.exe",
            r"C:\Program Files (x86)\AutoHotkey\AutoHotkey.exe",
            fr"C:\Users\{username}\AppData\Local\Programs\AutoHotkey\AutoHotkey.exe",
            r"C:\Program Files\AutoHotkey\v1.1\AutoHotkeyU64.exe",
            r"C:\Program Files\AutoHotkey\v1.1\AutoHotkeyU32.exe",
        ]
        for path in candidates:
            if os.path.isfile(path):
                cls._ahk_exe = path
                print(f"[CrossPlatformKeySender] AHK found: {path}")
                return cls._ahk_exe

        print("[CrossPlatformKeySender] AHK not found – will use PowerShell fallback")
        return None

    # -- Public API ----------------------------------------------------------

    def send_copy(self, wait: bool = True):
        """Send Ctrl+C (or Cmd+C on macOS) to copy the current selection.

        ``wait=False`` (Windows only): fire-and-forget. The AHK keystroke
        sender is launched without blocking the calling (Qt main) thread on
        its spawn + in-script Sleep + teardown (~150–400 ms). Callers must
        detect the copy landing themselves – e.g. by polling
        ``get_clipboard_sequence_number()``, as the Ctrl+Alt+C summon path
        does. Callers that read the clipboard after a fixed delay should
        keep the default blocking behaviour.

        Each platform uses a proven external mechanism:
        - Windows: AHK subprocess (or PowerShell fallback)
        - macOS: ``osascript`` (AppleScript via System Events)
        - Linux: pynput Controller
        """
        if IS_WINDOWS:
            self._send_copy_win32(wait=wait)
        elif IS_MACOS:
            self._send_copy_macos()
        elif self._controller:
            Key = self._Key
            with self._controller.pressed(Key.ctrl):
                self._controller.tap('c')

    # -- macOS-specific implementation ---------------------------------------

    @staticmethod
    def _send_copy_macos():
        """Send Cmd+C via osascript (macOS native automation).

        Uses AppleScript ``System Events`` to inject a keystroke into the
        foreground application – equivalent to AHK on Windows.  Runs as a
        separate process, so it's thread-safe.
        """
        try:
            subprocess.run(
                ['osascript', '-e',
                 'tell application "System Events" to keystroke "c" using command down'],
                timeout=3,
                capture_output=True,
            )
        except Exception as e:
            print(f"[CrossPlatformKeySender] osascript Cmd+C failed: {e}")

    # -- Windows-specific implementation -------------------------------------

    @classmethod
    def _send_copy_win32(cls, wait: bool = True):
        """Send Ctrl+C to the foreground app on Windows.

        Strategy:
        1. AHK inline script  – ``Send ^c``  (works perfectly, proven)
        2. PowerShell SendKeys – ``[System.Windows.Forms.SendKeys]::SendWait('^c')``
        """
        ahk = cls._find_ahk()
        if ahk:
            cls._send_copy_via_ahk(ahk, wait=wait)
        else:
            cls._send_copy_via_powershell()

    # Cached static Ctrl+C script paths, keyed 'v1'/'v2'. The script content
    # never varies, so writing a fresh temp file per invocation was pure
    # overhead – worse, Defender re-scans a freshly WRITTEN script file at
    # execution time, adding jittery latency to every Ctrl+Alt+C summon. A
    # stable file is written once and reused for the process lifetime.
    _copy_script_cache: Dict[str, str] = {}

    @classmethod
    def _get_copy_script(cls, ahk_exe: str) -> Optional[str]:
        """Return the path of the cached Ctrl+C script for this AHK version,
        writing it on first use. None if the file can't be created."""
        is_v2 = 'v2' in ahk_exe.lower()
        key = 'v2' if is_v2 else 'v1'
        cached = cls._copy_script_cache.get(key)
        if cached and os.path.isfile(cached):
            return cached
        # Sleep keeps the AHK process alive briefly after the Send so the
        # blocking (wait=True) path retains its historical "returns after
        # the copy has had time to land" semantics.
        if is_v2:
            script = '#Requires AutoHotkey v2.0\nSend "^c"\nSleep 100\n'
        else:
            script = 'Send, ^c\nSleep, 100\n'
        try:
            import tempfile
            path = os.path.join(tempfile.gettempdir(),
                                f'supervertaler_send_copy_{key}.ahk')
            need_write = True
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    need_write = f.read() != script
            except OSError:
                pass
            if need_write:
                with open(path, 'w', encoding='utf-8') as f:
                    f.write(script)
            cls._copy_script_cache[key] = path
            return path
        except Exception as e:
            print(f"[CrossPlatformKeySender] copy-script cache failed: {e}")
            return None

    @classmethod
    def _send_copy_via_ahk(cls, ahk_exe: str, wait: bool = True):
        """Run a minimal AHK script that sends Ctrl+C.

        ``wait=False`` launches AHK fire-and-forget (Popen) and returns
        immediately – used by the Ctrl+Alt+C summon path, which detects the
        copy landing via clipboard-sequence-number polling, so blocking the
        Qt main thread through spawn + Sleep 100 + teardown bought nothing.
        """
        try:
            script_path = cls._get_copy_script(ahk_exe)
            if script_path is None:
                raise OSError("copy-script cache unavailable")
            if wait:
                subprocess.run(
                    [ahk_exe, '/ErrorStdOut', script_path],
                    timeout=5,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
            else:
                subprocess.Popen(
                    [ahk_exe, '/ErrorStdOut', script_path],
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
        except Exception as e:
            print(f"[CrossPlatformKeySender] AHK Ctrl+C failed: {e}")
            # Fall back to PowerShell
            CrossPlatformKeySender._send_copy_via_powershell()

    @staticmethod
    def _send_copy_via_powershell():
        """Send Ctrl+C via PowerShell SendKeys (fallback if AHK unavailable)."""
        try:
            subprocess.run(
                [
                    'powershell', '-NoProfile', '-NonInteractive', '-Command',
                    'Add-Type -AssemblyName System.Windows.Forms; '
                    '[System.Windows.Forms.SendKeys]::SendWait("^c")'
                ],
                timeout=5,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        except Exception as e:
            print(f"[CrossPlatformKeySender] PowerShell SendKeys failed: {e}")

    def send_paste(self, hwnd=None, on_diag=None) -> bool:
        """Send Ctrl+V (or Cmd+V on macOS) to paste.

        ``hwnd`` (Windows only): the target window handle. When given, the
        paste path verifies that exact window is foreground (re-activating
        and waiting if needed) immediately before injecting ^v, so the
        keystroke can't miss on a foreground wobble. Ignored on mac/Linux.

        ``on_diag`` (optional): a callable that receives a human-readable
        diagnostic string when the paste path detects a problem (e.g. the
        target window never became active). Falls back to ``print``.

        Platform backends:
        - Windows: in-process SendInput (legacy AHK/PowerShell only as an
          emergency fallback – see ``_send_paste_win32``)
        - macOS: osascript (AppleScript via System Events)
        - Linux: pynput Controller

        Returns True if a backend was invoked without raising. A True
        result does NOT guarantee the target app accepted the paste –
        synthetic Ctrl+V is fire-and-forget – it only means the
        keystroke was dispatched. Callers that need reliability in apps
        which don't bind Ctrl+V (terminals, some Java/Electron apps)
        should prefer ``type_text`` for those targets.
        """
        if IS_WINDOWS:
            return self._send_paste_win32(hwnd, on_diag)
        elif IS_MACOS:
            return self._send_paste_macos()
        elif self._controller:
            Key = self._Key
            try:
                with self._controller.pressed(Key.ctrl):
                    self._controller.tap('v')
                return True
            except Exception as e:
                print(f"[CrossPlatformKeySender] linux paste failed: {e}")
                return False
        return False

    def _send_paste_win32(self, hwnd=None, on_diag=None) -> bool:
        """Send Ctrl+V on Windows via in-process SendInput.

        Phase 1 of the clipboard-reliability work: replaces the
        AutoHotkey-subprocess path (kept below as
        ``_send_paste_win32_legacy``, used only if SendInput itself
        raises). Sequence:

          1. Wait (≤1 s) for physically held modifiers to be released –
             a Ctrl+V sent while Alt is still down from the Ctrl+Alt+C
             hotkey arrives as Ctrl+Alt+V and gets ignored. By click/
             Enter time the keys are normally long up, so this is
             usually a 0 ms no-op. Injected key-ups are the last resort
             on timeout because auto-repeat re-presses a held key.
          2. If ``hwnd`` is given and isn't foreground, re-activate and
             poll (≤1 s) for the switch; report via ``on_diag`` if it
             never lands, then send anyway (matches the old AHK
             WinWaitActive behaviour – detection may be stale).
          3. Inject Ctrl-down, V-down, V-up, Ctrl-up as ONE SendInput
             batch so no real keystroke can interleave.

        Unlike the AHK path there is no Python→subprocess hand-off gap:
        the keystroke goes out the instant after the foreground check,
        from the same process that performed it.
        """
        diag = on_diag or print
        try:
            import ctypes
            user32 = ctypes.windll.user32

            if not wait_for_modifier_release(1000):
                held = physically_held_modifiers()
                if held:
                    send_key_events([(vk, True) for vk in held])

            if isinstance(hwnd, int):
                if user32.GetForegroundWindow() != hwnd:
                    activate_foreground_window(hwnd)
                    deadline = time.monotonic() + 1.0
                    while time.monotonic() < deadline:
                        if user32.GetForegroundWindow() == hwnd:
                            break
                        time.sleep(0.02)
                    if user32.GetForegroundWindow() != hwnd:
                        diag(f"Clipboard paste: target window (hwnd={hwnd}) "
                             f"did not become active within 1s — "
                             f"focus/activation failure.")
                    else:
                        time.sleep(0.06)  # let the target restore edit focus

            if send_key_events([(VK_CONTROL, False), (VK_V, False),
                                (VK_V, True), (VK_CONTROL, True)]):
                return True
            diag("Clipboard paste: SendInput injected 0 events — input to "
                 "this window is blocked (elevated target or secure "
                 "desktop).")
            return False
        except Exception as e:
            print(f"[CrossPlatformKeySender] SendInput paste failed ({e}); "
                  f"falling back to AHK/PowerShell")
            return self._send_paste_win32_legacy(hwnd, on_diag)

    def _send_paste_win32_legacy(self, hwnd=None, on_diag=None) -> bool:
        """LEGACY: send Ctrl+V on Windows via AHK or PowerShell. Only used
        when the SendInput path raises unexpectedly.

        Hardened for cross-app reliability:

          * **Physically-held modifiers are released first.** A paste
            triggered while the Ctrl+Alt+C hotkey is still down would
            otherwise arrive as Ctrl+Alt+V (or Ctrl+Shift+V, …) and be
            ignored by the target. ``{Ctrl up}{Alt up}{Shift up}{LWin
            up}{RWin up}`` clears them; releasing an already-up key is a
            harmless no-op. This is the change that actually fixed the
            original "won't paste" reports – stray modifiers, not send
            mode, were the culprit.
          * **Explicit ``SendMode "Input"``.** SendInput is AHK's most
            broadly-compatible backend and is what Chromium/Chrome,
            Electron, and ordinary Win32 apps expect. An earlier attempt
            used ``SendMode "Event"`` to help RDP/VM sessions, but Event
            mode regressed paste into Chrome (Gmail compose), so we send
            via Input. Terminals – which ignore Ctrl+V regardless of
            send mode – are handled separately by the clipboard
            manager's typing fallback, not here.
          * **The v2 script carries the ``#Requires`` header** the
            keystroke path already uses, so a mis-detected launcher
            (e.g. a bare ``AutoHotkey.exe`` dispatcher) can't silently
            feed v1 comma-syntax to a v2 interpreter and no-op.

        Uses the same temp-file approach as ``_send_copy_via_ahk``
        because AHK does not reliably accept scripts via stdin.

        Returns True if a backend was invoked without raising.
        """
        ahk = self._find_ahk()
        if ahk:
            try:
                import tempfile
                is_v2 = 'v2' in ahk.lower()
                # Unsigned HWND string for AHK's ahk_id (ctypes may hand us a
                # sign-extended int; AHK wants the raw unsigned handle).
                hwnd_id = (hwnd & 0xFFFFFFFF) if isinstance(hwnd, int) else None
                if is_v2:
                    activate = (
                        f'if WinExist("ahk_id {hwnd_id}") {{\n'
                        f'    WinActivate "ahk_id {hwnd_id}"\n'
                        f'    if !WinWaitActive("ahk_id {hwnd_id}", , 1)\n'
                        f'        FileAppend "notactive", "*"\n'
                        f'    Sleep 60\n'   # let the target restore edit/DOM focus
                        f'}}\n'
                    ) if hwnd_id else ''
                    script = (
                        '#Requires AutoHotkey v2.0\n'
                        'SendMode "Input"\n'
                        + activate +
                        'Send "{Ctrl up}{Alt up}{Shift up}{LWin up}{RWin up}"\n'
                        'Send "^v"\n'
                        'ExitApp\n'
                    )
                else:
                    activate = (
                        f'IfWinExist, ahk_id {hwnd_id}\n'
                        f'{{\n'
                        f'    WinActivate, ahk_id {hwnd_id}\n'
                        f'    WinWaitActive, ahk_id {hwnd_id}, , 1\n'
                        f'    if ErrorLevel\n'
                        f'        FileAppend, notactive, *\n'
                        f'    Sleep, 60\n'
                        f'}}\n'
                    ) if hwnd_id else ''
                    script = (
                        'SendMode Input\n'
                        + activate +
                        'Send, {Ctrl up}{Alt up}{Shift up}{LWin up}{RWin up}\n'
                        'Send, ^v\n'
                    )

                with tempfile.NamedTemporaryFile(
                    mode='w', suffix='.ahk', delete=False, encoding='utf-8'
                ) as f:
                    f.write(script)
                    tmp_path = f.name

                result = subprocess.run(
                    [ahk, '/ErrorStdOut', tmp_path],
                    timeout=5,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                    capture_output=True,
                    text=True,
                )

                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

                # Diagnostic: AHK writes "notactive" to stdout when the target
                # window never became active within 1 s. If a paste fails and you
                # see this, the problem is focus/activation; if you DON'T see it,
                # the window was active but the app (e.g. a browser rich-text
                # editor in an iframe) didn't route Ctrl+V to its edit field.
                out = (result.stdout or '') + (result.stderr or '')
                if 'notactive' in out:
                    msg = (f"Clipboard paste: target window (hwnd={hwnd_id}) did not "
                           f"become active within 1s — focus/activation failure.")
                    (on_diag or print)(msg)
                return True
            except Exception as e:
                print(f"[CrossPlatformKeySender] AHK paste failed: {e}")

        # Fallback: PowerShell SendKeys
        try:
            subprocess.run(
                [
                    'powershell', '-NoProfile', '-NonInteractive', '-Command',
                    'Add-Type -AssemblyName System.Windows.Forms; '
                    '[System.Windows.Forms.SendKeys]::SendWait("^v")'
                ],
                timeout=5,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            return True
        except Exception as e:
            print(f"[CrossPlatformKeySender] PowerShell paste failed: {e}")
            return False

    @staticmethod
    def _send_paste_macos() -> bool:
        """Send Cmd+V via osascript (macOS)."""
        try:
            subprocess.run(
                ['osascript', '-e',
                 'tell application "System Events" to keystroke "v" '
                 'using command down'],
                capture_output=True, timeout=5,
            )
            return True
        except Exception as e:
            print(f"[CrossPlatformKeySender] macOS paste failed: {e}")
            return False

    def send_keystroke(self, keystroke: str):
        """Send a compound keystroke like ``'ctrl+s'``, ``'ctrl+shift+l'``,
        ``'f10'``, ``'shift+f10'``, etc.

        Supports modifiers: ctrl, alt, shift, cmd/win.
        Supports special keys: enter, tab, escape, backspace, delete,
        f1–f12, arrow keys, home, end, page up/down, space, insert.

        Uses the same platform-native approach as ``send_copy()``:
        - Windows: AHK subprocess (or PowerShell fallback)
        - macOS: osascript (AppleScript via System Events)
        - Linux: pynput Controller
        """
        if IS_WINDOWS:
            self._send_keystroke_win32(keystroke)
            return
        if IS_MACOS:
            self._send_keystroke_macos(keystroke)
            return

        # Linux: use pynput
        if not self._controller:
            return
        Key = self._Key

        parts = keystroke.lower().split('+')
        modifiers: List = []
        key = None

        modifier_map = {
            'ctrl': Key.ctrl, 'control': Key.ctrl,
            'alt': Key.alt,
            'shift': Key.shift,
            'cmd': Key.cmd, 'win': Key.cmd, 'super': Key.cmd,
        }

        special_map = {
            'enter': Key.enter, 'return': Key.enter,
            'tab': Key.tab,
            'escape': Key.esc, 'esc': Key.esc,
            'space': Key.space,
            'backspace': Key.backspace,
            'delete': Key.delete, 'del': Key.delete,
            'insert': Key.insert,
            'home': Key.home, 'end': Key.end,
            'pageup': Key.page_up, 'page_up': Key.page_up,
            'pagedown': Key.page_down, 'page_down': Key.page_down,
            'up': Key.up, 'down': Key.down,
            'left': Key.left, 'right': Key.right,
            'f1': Key.f1, 'f2': Key.f2, 'f3': Key.f3, 'f4': Key.f4,
            'f5': Key.f5, 'f6': Key.f6, 'f7': Key.f7, 'f8': Key.f8,
            'f9': Key.f9, 'f10': Key.f10, 'f11': Key.f11, 'f12': Key.f12,
        }

        for part in parts:
            part = part.strip()
            if part in modifier_map:
                modifiers.append(modifier_map[part])
            elif part in special_map:
                key = special_map[part]
            else:
                key = part  # single character key

        with contextlib.ExitStack() as stack:
            for mod in modifiers:
                stack.enter_context(self._controller.pressed(mod))
            if key is not None:
                if isinstance(key, str):
                    self._controller.tap(key)
                else:
                    self._controller.tap(key)

    def _keystroke_to_ahk(self, keystroke: str) -> str:
        """Convert a keystroke string like ``'ctrl+alt+p'`` to AHK Send format."""
        modifiers = {
            'ctrl': '^', 'control': '^',
            'alt': '!',
            'shift': '+',
            'win': '#', 'windows': '#',
        }
        special_keys = {
            'enter': '{Enter}', 'return': '{Enter}',
            'tab': '{Tab}',
            'escape': '{Esc}', 'esc': '{Esc}',
            'space': '{Space}',
            'backspace': '{Backspace}',
            'delete': '{Delete}', 'del': '{Delete}',
            'insert': '{Insert}', 'ins': '{Insert}',
            'home': '{Home}', 'end': '{End}',
            'pageup': '{PgUp}', 'pgup': '{PgUp}',
            'pagedown': '{PgDn}', 'pgdn': '{PgDn}',
            'up': '{Up}', 'down': '{Down}',
            'left': '{Left}', 'right': '{Right}',
            'f1': '{F1}', 'f2': '{F2}', 'f3': '{F3}', 'f4': '{F4}',
            'f5': '{F5}', 'f6': '{F6}', 'f7': '{F7}', 'f8': '{F8}',
            'f9': '{F9}', 'f10': '{F10}', 'f11': '{F11}', 'f12': '{F12}',
        }
        parts = keystroke.lower().replace(' ', '').split('+')
        result = ''
        for part in parts:
            if part in modifiers:
                result += modifiers[part]
            elif part in special_keys:
                result += special_keys[part]
            else:
                result += part
        return result

    @staticmethod
    def _keystroke_to_applescript(keystroke: str) -> str:
        """Convert a keystroke string to an osascript command.

        Follows Qt's macOS convention so a stored ``ctrl+s`` fires ⌘S on
        Mac (the platform-native Save shortcut), matching what the user
        actually pressed in the press-to-capture editor and what other Mac
        apps do. ``meta`` maps to literal Control (the ⌃ key), ``cmd`` /
        ``command`` map to Command directly. ``alt`` is Option, ``shift``
        is Shift, ``win`` is treated as Command for cross-platform recipes.
        """
        modifier_map = {
            'ctrl': 'command down', 'control': 'command down',
            'meta': 'control down',
            'alt': 'option down', 'option': 'option down',
            'shift': 'shift down',
            'cmd': 'command down', 'command': 'command down',
            'super': 'command down', 'win': 'command down',
        }
        special_keys = {
            'enter': 'return', 'return': 'return',
            'tab': 'tab', 'escape': 'escape', 'esc': 'escape',
            'space': 'space', 'delete': 'delete', 'backspace': 'delete',
            'up': 'up arrow', 'down': 'down arrow',
            'left': 'left arrow', 'right': 'right arrow',
            'home': 'home', 'end': 'end',
            'pageup': 'page up', 'pagedown': 'page down',
            'f1': 'F1', 'f2': 'F2', 'f3': 'F3', 'f4': 'F4',
            'f5': 'F5', 'f6': 'F6', 'f7': 'F7', 'f8': 'F8',
            'f9': 'F9', 'f10': 'F10', 'f11': 'F11', 'f12': 'F12',
        }
        parts = keystroke.lower().replace(' ', '').split('+')
        mods = []
        key = None
        for part in parts:
            if part in modifier_map:
                mods.append(modifier_map[part])
            elif part in special_keys:
                key = special_keys[part]
            else:
                key = part
        if key is None:
            return ''
        # Determine if we need 'keystroke' (character) or 'key code' (special)
        is_special = keystroke.lower().replace(' ', '').split('+')[-1] in special_keys
        if is_special:
            action = f'key code (ASCII number of "{key}")'
            # For special keys, use 'keystroke' with the key name isn't ideal;
            # AppleScript uses 'keystroke' for characters. For special keys we
            # still use keystroke with the key name – AppleScript handles most.
        using = ''
        if mods:
            using = ' using {' + ', '.join(mods) + '}'
        return (f'tell application "System Events" to keystroke "{key}"'
                f'{using}')

    def _send_keystroke_win32(self, keystroke: str):
        """Send an arbitrary keystroke on Windows via AHK or PowerShell.

        Uses the same proven pattern as ``VoiceCommandManager._run_ahk_code``:
        AHK v2 script with ``#Requires``, ``Popen`` (non-blocking).
        """
        ahk = self._find_ahk()
        ahk_keys = self._keystroke_to_ahk(keystroke)
        if ahk:
            try:
                import tempfile
                # Always use AHK v2 syntax with #Requires header
                # (matches the proven pattern in voice_commands._run_ahk_code)
                script = (
                    f'#Requires AutoHotkey v2.0\n'
                    f'#SingleInstance Force\n'
                    f'Send "{ahk_keys}"\n'
                    f'ExitApp\n'
                )
                with tempfile.NamedTemporaryFile(
                    mode='w', suffix='.ahk', delete=False, encoding='utf-8'
                ) as f:
                    f.write(script)
                    tmp_path = f.name
                subprocess.Popen(
                    [ahk, tmp_path],
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
                return
            except Exception as e:
                print(f"[CrossPlatformKeySender] AHK keystroke failed: {e}")

        # Fallback: PowerShell SendKeys (limited – no Alt support)
        ps_keys = self._keystroke_to_powershell_sendkeys(keystroke)
        if ps_keys:
            try:
                subprocess.run(
                    [
                        'powershell', '-NoProfile', '-NonInteractive',
                        '-Command',
                        'Add-Type -AssemblyName System.Windows.Forms; '
                        f'[System.Windows.Forms.SendKeys]::SendWait("{ps_keys}")'
                    ],
                    timeout=5,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
            except Exception as e:
                print(f"[CrossPlatformKeySender] PowerShell keystroke failed: {e}")

    @staticmethod
    def _keystroke_to_powershell_sendkeys(keystroke: str) -> str:
        """Convert keystroke to PowerShell SendKeys format (best-effort)."""
        mod_map = {'ctrl': '^', 'control': '^', 'alt': '%', 'shift': '+'}
        special = {
            'enter': '{ENTER}', 'return': '{ENTER}', 'tab': '{TAB}',
            'escape': '{ESC}', 'esc': '{ESC}', 'backspace': '{BACKSPACE}',
            'delete': '{DELETE}', 'del': '{DELETE}',
            'up': '{UP}', 'down': '{DOWN}', 'left': '{LEFT}',
            'right': '{RIGHT}', 'home': '{HOME}', 'end': '{END}',
            'pageup': '{PGUP}', 'pagedown': '{PGDN}',
            'f1': '{F1}', 'f2': '{F2}', 'f3': '{F3}', 'f4': '{F4}',
            'f5': '{F5}', 'f6': '{F6}', 'f7': '{F7}', 'f8': '{F8}',
            'f9': '{F9}', 'f10': '{F10}', 'f11': '{F11}', 'f12': '{F12}',
        }
        parts = keystroke.lower().replace(' ', '').split('+')
        prefix = ''
        key = ''
        for part in parts:
            if part in mod_map:
                prefix += mod_map[part]
            elif part in special:
                key = special[part]
            else:
                key = part
        if not key:
            return ''
        return prefix + key

    def _send_keystroke_macos(self, keystroke: str):
        """Send an arbitrary keystroke on macOS via osascript."""
        try:
            script = self._keystroke_to_applescript(keystroke)
            if script:
                subprocess.run(
                    ['osascript', '-e', script],
                    capture_output=True, timeout=5,
                )
        except Exception as e:
            print(f"[CrossPlatformKeySender] macOS keystroke failed: {e}")

    # -- Direct text typing (for dictation) ---------------------------------

    def type_text(self, text: str) -> bool:
        """Type ``text`` into the foreground window character-by-character.

        Unlike clipboard+paste, this works in apps that don't bind Ctrl+V the
        standard way – Windows Terminal, cmd, PowerShell, VSCode terminals,
        some Java apps, and any other app that accepts keyboard input but
        not the system paste shortcut. The trade-off is speed: typing is
        per-character, so very long passages take noticeably longer than a
        paste would.

        Returns True if typing was attempted on a supported backend.
        """
        if not text:
            return True
        if IS_WINDOWS:
            return self._type_text_win32(text)
        if IS_MACOS:
            return self._type_text_macos(text)
        if self._controller:
            try:
                self._controller.type(text)
                return True
            except Exception as e:
                print(f"[CrossPlatformKeySender] linux type_text failed: {e}")
        return False

    def _type_text_win32(self, text: str) -> bool:
        """Type ``text`` on Windows via AHK SendText.

        The payload is written to a UTF-8 temp file and read back inside the
        AHK script. This avoids escape-sequence pitfalls with quotes,
        backslashes, and arbitrary Unicode (CJK, emoji, etc.) that
        translators routinely deal with.
        """
        ahk = self._find_ahk()
        if not ahk:
            return False

        is_v2 = 'v2' in ahk.lower()
        txt_path = None
        ahk_path = None
        try:
            import tempfile

            with tempfile.NamedTemporaryFile(
                mode='w', suffix='.txt', delete=False,
                encoding='utf-8', newline='',
            ) as f:
                f.write(text)
                txt_path = f.name

            if is_v2:
                # AHK v2: SendText with text loaded from the temp file.
                # Double the backslashes for the AHK string literal.
                escaped = txt_path.replace('\\', '\\\\')
                script = (
                    f'txt := FileRead("{escaped}", "UTF-8")\n'
                    f'SendText txt\n'
                )
            else:
                # AHK v1: SendInput with {Text} literal mode.
                script = (
                    f'FileRead, txt, *p65001 {txt_path}\n'
                    f'SendInput, {{Text}}%txt%\n'
                )

            with tempfile.NamedTemporaryFile(
                mode='w', suffix='.ahk', delete=False, encoding='utf-8',
            ) as f:
                f.write(script + '\n')
                ahk_path = f.name

            subprocess.run(
                [ahk, '/ErrorStdOut', ahk_path],
                timeout=10,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            return True
        except Exception as e:
            print(f"[CrossPlatformKeySender] AHK type_text failed: {e}")
            return False
        finally:
            for path in (ahk_path, txt_path):
                if path:
                    try:
                        os.unlink(path)
                    except OSError:
                        pass

    def _type_text_macos(self, text: str) -> bool:
        """Type ``text`` on macOS via osascript keystroke."""
        try:
            escaped = text.replace('\\', '\\\\').replace('"', '\\"')
            subprocess.run(
                ['osascript', '-e',
                 f'tell application "System Events" to keystroke "{escaped}"'],
                capture_output=True, timeout=10,
            )
            return True
        except Exception as e:
            print(f"[CrossPlatformKeySender] macOS type_text failed: {e}")
            return False
