"""Global hotkeys and bringing Workbench to the front (for Superlookup and
voice), including the Windows focus repairs this needs.

Part of the main window (SupervertalerQt), moved here unchanged. The
methods run as the main window's own: `self` is the SupervertalerQt.
"""

import sys
import traceback
from PyQt6.QtCore import QTimer


class WindowFocusMixin:
    def _bring_workbench_forward(self):
        """Bring Workbench to the foreground.

        On Windows the bare show/raise/activateWindow trio isn't
        enough to win the foreground race against another app the
        user has been working in (Trados is a known-aggressive
        host). We escalate through the full documented
        foreground-grab chain: synthetic Alt-key press to satisfy
        SetForegroundWindow's "Alt pressed" exception, then
        AttachThreadInput dance, then BringWindowToTop +
        SetForegroundWindow + SwitchToThisWindow. See v1.10.9
        changelog for the full history of this fix.
        """
        if self.isMinimized():
            self.showNormal()
        self.show()
        self.raise_()
        self.activateWindow()

        import sys
        if sys.platform == 'win32':
            # v1.10.9: belt + braces + suspenders. Three earlier
            # versions (v1.10.6 deferred the button click, v1.10.7
            # reordered ensure vs. bring, v1.10.8 split work across
            # event-loop turns) still left the first Ctrl+Alt+C from
            # a heavy host like Trados painting Workbench *behind*
            # Trados despite the SetForegroundWindow call returning
            # success. That's classic Windows foreground-stealing
            # prevention: the OS accepts the call but quietly refuses
            # to actually bring our window forward, leaving it
            # flashing in the taskbar with no visible activation.
            #
            # The canonical fix is the "Alt key" trick. Windows
            # documents an exception in the foreground-allowed rules
            # (SetForegroundWindow MSDN page): if the Alt key is
            # currently pressed at the moment SetForegroundWindow is
            # called, the OS grants the request unconditionally.
            # By synthesising a brief Alt down + Alt up *immediately
            # before* the call, we satisfy that rule even when our
            # process has no other claim to foreground rights. The
            # Alt key is released within microseconds so the user
            # never sees side effects (no menu activation etc.).
            #
            # Plus three additional hammers, applied in order:
            #   - BringWindowToTop() forces Z-order to the top of
            #     the same-thread-group stack.
            #   - SetForegroundWindow() requests the actual
            #     foreground change. With Alt held + thread input
            #     attached, the OS now accepts it.
            #   - SwitchToThisWindow() is a deprecated-but-functional
            #     API that bypasses some foreground rules entirely;
            #     used as a last-resort hammer for stubborn cases
            #     (Trados being one).
            try:
                import ctypes
                user32 = ctypes.windll.user32
                kernel32 = ctypes.windll.kernel32
                hwnd = int(self.winId())

                # Step 1: Synthetic Alt + F24 chord to satisfy
                # SetForegroundWindow's "Alt key pressed" exception
                # without triggering Qt's QMenuBar menu-activation.
                #
                # v1.10.9 used a bare Alt down/up here. That works
                # for the foreground grab, but Windows + Qt also
                # interpret a *naked* Alt tap (Alt down → Alt up
                # with nothing in between) as "user wants to
                # activate the menu bar". Even though the synthetic
                # tap was meant for the originating foreground app,
                # the AttachThreadInput dance below shares input
                # queues, so the Alt event leaks into Workbench's
                # input stream just as we become foreground. Qt's
                # QMenuBar then activates File-menu navigation and
                # the user's arrow keys steer the menu instead of
                # the Clipboard list / SuperLookup results
                # (v1.10.14 tried to fix this post-hoc by calling
                # menuBar().setActiveAction(None) and setFocus()
                # on the target widget, but that ran *before* Qt
                # processed the queued Alt event, so it cleared
                # state that hadn't been set yet).
                #
                # v1.10.15 inserts a synthetic F24 between the Alt
                # down and Alt up. VK_F24 (0x87) is a defined VK
                # code that no app binds to – it exists in the
                # Windows VK enum mostly for keyboards with extended
                # function-key rows that nobody ships any more. Qt's
                # QMenuBar treats Alt+anything as a chord, not a
                # naked Alt tap, and skips menu activation. The
                # synthetic F24 itself reaches no listener (nothing
                # binds it). The Alt key still satisfies
                # SetForegroundWindow's exception because it's
                # pressed at the moment SetForegroundWindow is
                # called.
                VK_MENU = 0x12
                VK_F24 = 0x87
                KEYEVENTF_KEYUP = 0x0002
                user32.keybd_event(VK_MENU, 0, 0, 0)               # Alt down
                user32.keybd_event(VK_F24, 0, 0, 0)                # F24 down (chord)
                user32.keybd_event(VK_F24, 0, KEYEVENTF_KEYUP, 0)  # F24 up
                user32.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, 0) # Alt up

                # Step 2: AttachThreadInput dance.
                fg = user32.GetForegroundWindow()
                fg_thread = user32.GetWindowThreadProcessId(fg, None)
                our_thread = kernel32.GetCurrentThreadId()
                attached = False
                if fg and fg_thread and fg_thread != our_thread:
                    attached = bool(
                        user32.AttachThreadInput(fg_thread, our_thread, True)
                    )

                # Step 3: Force Z-order to top, then request
                # foreground, then the deprecated-but-effective
                # SwitchToThisWindow hammer.
                try:
                    user32.BringWindowToTop(hwnd)
                    user32.SetForegroundWindow(hwnd)
                    # SwitchToThisWindow(hwnd, fAltTab=True) – second
                    # arg makes the call behave like an Alt+Tab
                    # switch which is more reliable than the bare
                    # foreground-request path.
                    user32.SwitchToThisWindow(hwnd, True)
                finally:
                    if attached:
                        user32.AttachThreadInput(fg_thread, our_thread, False)
            except Exception as e:
                print(f"[Workbench] foreground escalation failed: {e}")

        # v1.10.14: the synthetic Alt-tap above gets the foreground
        # grant we need, but it's also what Windows interprets as
        # "user pressed Alt to activate the menu bar". Even though
        # we sent the Alt to the *originating* foreground (Trados),
        # AttachThreadInput shares input queues for the duration, so
        # the Alt event leaks into Workbench's input stream just as
        # we become foreground. Qt's QMenuBar then activates File-
        # menu navigation mode and the user's arrow keys steer the
        # menu instead of the Clipboard list / SuperLookup results.
        # Dismiss the leaked menu state explicitly so we land in the
        # expected "neutral" focus state.
        self._dismiss_menu_activation()

        # v1.10.344: VERIFY the grab actually took, a beat later. All
        # the hammers above can still lose: BringWindowToTop /
        # SwitchToThisWindow change the Z-ORDER (the window looks
        # summoned) while Windows quietly refuses the KEYBOARD-focus
        # transfer. In that state mouse clicks work (clicking
        # self-activates) but Esc / arrow keys go to the app the user
        # came from – experienced as "Esc does absolutely nothing".
        # Verification + one hardened retry converts that silent
        # failure into either a working window or a logged warning.
        try:
            from PyQt6.QtCore import QTimer
            QTimer.singleShot(120, self._verify_foreground_grab)
        except Exception:
            pass

    def _verify_foreground_grab(self):
        """~120 ms after a summon: confirm Workbench really owns the
        keyboard focus; if not, retry via the hardened
        ``activate_foreground_window`` (AttachThreadInput + ALT-nudge,
        verified, up to 3 attempts) and log honestly if Windows still
        refuses – so a focus-less summon is never a silent mystery."""
        import sys
        if sys.platform != 'win32':
            return
        try:
            import ctypes
            user32 = ctypes.windll.user32
            hwnd = int(self.winId())
            if user32.GetForegroundWindow() == hwnd:
                self._repair_null_keyboard_focus(user32, hwnd)
                return
            from modules.platform_helpers import activate_foreground_window
            if activate_foreground_window(hwnd):
                print("[Workbench] foreground grab needed a verified retry "
                      "(2nd attempt succeeded)")
                self._repair_null_keyboard_focus(user32, hwnd)
                return
        except Exception as e:
            print(f"[Workbench] foreground verification failed: {e}")
            return
        msg = ("⚠ Windows refused to hand Workbench keyboard focus after "
               "the summon – Esc / arrow keys may still act on the "
               "previous app. Click once inside Workbench to fix.")
        print(f"[Workbench] {msg}")
        try:
            self.log(msg)
        except Exception:
            pass

    def _repair_null_keyboard_focus(self, user32, hwnd):
        """Repair the NULL-keyboard-focus state AttachThreadInput can
        leave behind (v1.10.347, the "Esc does nothing when Workbench
        runs elevated" fix).

        The foreground-grab dance attaches our input queue to the old
        foreground thread's and detaches again. A documented side
        effect: the detach can leave OUR queue's raw focus window NULL
        – the window received WM_ACTIVATE (Qt reports active), it IS
        the foreground window, mouse clicks work, but the OS has no
        focus HWND to deliver WM_KEYDOWN to, so every keystroke
        (Esc, arrows, Enter) silently evaporates before Qt sees it.
        Observed specifically when Workbench runs elevated (matching
        an elevated Trados); regular runs take a luckier path.

        GetFocus()/SetFocus operate on the CALLING thread's queue, and
        this runs on the Qt GUI thread that owns the window – exactly
        the thread whose queue needs the repair.
        """
        try:
            if user32.GetFocus():
                return  # some window of ours has focus – healthy
            from ctypes import wintypes
            user32.SetFocus.argtypes = [wintypes.HWND]
            user32.SetFocus(hwnd)
            try:
                self.log("[Focus] keyboard focus was NULL after the "
                         "foreground grab – repaired via SetFocus")
            except Exception:
                pass
        except Exception as e:
            print(f"[Workbench] NULL-focus repair failed: {e}")

    def _dismiss_menu_activation(self):
        """Clear any leaked menu-bar activation after a foreground
        grab. Called from _bring_workbench_forward(); safe to call
        when no menu is active (no-op in that case). The continuation
        callback (`_continue_superlookup`)
        also explicitly setFocus() on its target widget, which
        belt-and-braces clears the menu-activation indirectly – but
        calling setActiveAction(None) here makes the cleanup
        deterministic instead of relying on focus-change side
        effects."""
        try:
            mb = self.menuBar() if hasattr(self, 'menuBar') else None
            if mb is not None:
                mb.setActiveAction(None)
        except Exception:
            pass

    def open_workbench_to_superlookup(self, text: str = ""):
        """Bring Workbench forward, switch to the SuperLookup top tab,
        and optionally seed the search field with ``text``.

        Wired to Ctrl+Alt+L in v1.10.1 (Phase 2 of issue #199). Replaces
        the legacy ``Sidekick.show_superlookup(text)`` route for the
        global hotkey path; Sidekick's own copy still works for users
        who summon Sidekick via Ctrl+Alt+K.
        """
        try:
            # v1.10.7: bring Workbench forward *before* the lazy-tab
            # ensure.
            # v1.10.8: that wasn't enough on a cold first run. Even
            # with bring-forward called first, the SetForegroundWindow
            # call only *queues* WM_ACTIVATE / WM_PAINT for our
            # window – Workbench doesn't visibly become active until
            # Qt's event loop processes those messages. If we then
            # synchronously call _ensure_superlookup_top_tab() and
            # block the GUI thread for 1–2s building the widget, the
            # queued activation events sit waiting; meanwhile Windows
            # may decide to re-evaluate foreground or simply never
            # repaint Workbench as the active window because Qt hasn't
            # acknowledged the WM_ACTIVATE. The user keeps seeing
            # their source app the whole time.
            #
            # Fix: split the work across two event-loop turns.
            #   Turn 1 (now):   _bring_workbench_forward()
            #   Turn 2 (next):  ensure + setCurrentIndex + seed/click
            # QTimer.singleShot(0, …) schedules the continuation for
            # the next iteration of Qt's main loop, between which the
            # activation/paint events get processed and Workbench
            # actually appears on top before we start blocking the
            # GUI thread on widget construction.
            self._bring_workbench_forward()
            from PyQt6.QtCore import QTimer

            def _continue_superlookup():
                try:
                    if hasattr(self, '_ensure_superlookup_top_tab'):
                        self._ensure_superlookup_top_tab()
                    if hasattr(self, 'superlookup_tab_index') and hasattr(self, 'main_tabs'):
                        self.main_tabs.setCurrentIndex(self.superlookup_tab_index)
                    widget = getattr(self, '_superlookup_top_widget', None)
                    if widget is not None:
                        # v1.10.14: explicit focus on the search field
                        # (or the tab root if the search field isn't
                        # available) so keyboard navigation lands here
                        # instead of leaking into the menu bar after
                        # the foreground-grab Alt-tap.
                        try:
                            if hasattr(widget, 'source_text') and widget.source_text is not None:
                                widget.source_text.setFocus()
                            else:
                                widget.setFocus()
                        except Exception:
                            pass
                    if widget is not None and text:
                        try:
                            if hasattr(widget, 'source_text'):
                                widget.source_text.setEditText(text)
                            if hasattr(widget, 'search_btn'):
                                # Still defer the click by another
                                # 150ms past the ensure so the search
                                # doesn't peg the GUI thread before
                                # the tab finishes painting.
                                QTimer.singleShot(150, widget.search_btn.click)
                        except Exception as inner:
                            self.log(f"⚠ Could not seed SuperLookup search: {inner}")
                except Exception as inner:
                    self.log(f"⚠ Could not finish SuperLookup open: {inner}")

            QTimer.singleShot(0, _continue_superlookup)
        except Exception as e:
            self.log(f"⚠ Could not open SuperLookup top tab: {e}")
            import traceback
            traceback.print_exc()

    def reload_global_hotkeys(self):
        """Re-register the global hotkey set with current ShortcutManager values.

        Called after the user changes a ``global_*`` shortcut in
        Settings → Keyboard Shortcuts. Without this call, the OS-level
        hotkey listener keeps firing on the *old* key until the next
        application restart, because pynput / WinAPI registrations don't
        track ShortcutManager state. Idempotent – ``register_global_hotkey``
        stops any previously running manager before binding fresh keys.
        """
        lookup_tab = getattr(self, 'lookup_tab', None)
        if lookup_tab is None or not hasattr(lookup_tab, 'register_global_hotkey'):
            return
        try:
            lookup_tab.register_global_hotkey()
        except Exception as e:
            self.log(f"⚠ Could not reload global hotkeys: {e}")
