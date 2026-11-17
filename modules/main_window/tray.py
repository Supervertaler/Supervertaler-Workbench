"""The system-tray icon: close to tray, start minimised, start with Windows,
and the Quick Lookup that Esc dismisses.

Part of the main window (SupervertalerQt), moved here unchanged. The
methods run as the main window's own: `self` is the SupervertalerQt.
"""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QLineEdit,
    QPlainTextEdit,
    QTextEdit,
)


class TrayMixin:
    def _on_tray_activated(self, reason):
        """Left-click the tray icon → toggle window visibility (Windows/Linux convention)."""
        from PyQt6.QtWidgets import QSystemTrayIcon
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            if self.isVisible():
                self.hide()
            else:
                self._tray_show_window()

    def _tray_show_window(self):
        """Restore, raise, and focus the main window from the tray."""
        self.show()
        # Clear the minimized state if Qt set it during hide.
        self.setWindowState(self.windowState() & ~Qt.WindowState.WindowMinimized)
        self.raise_()
        self.activateWindow()

    def _on_esc_quick_lookup_dismiss(self):
        """Dismiss Workbench when Esc is pressed on a "quick lookup"
        top tab.

        Per-tab dismissal action:

        - **SuperLookup / Voice**: hide to the system tray (requires a
          tray icon – without one, hiding would strand the user in an
          invisible window, so those tabs fail closed).

        Gate that always applies: the current tab is one of
        SuperLookup / Voice. On Editor / TMs / Termbases /
        AI / Settings, Esc keeps its natural editor / dialog /
        combo-box semantics (cancel-edit, close-popup, etc.).

        Per-tab text-input gate (v1.10.19 refinement):

        - **SuperLookup**: always hide regardless of focus. The user
          observed (correctly) that SuperLookup is a single-purpose
          query surface – there's no state to preserve, the search
          field re-seeds on the next summon, and the only thing Esc
          could reasonably mean here is "get me out of here". So we
          skip the focus check and dismiss unconditionally.
        - **Voice**: skip the dismiss if a text-input
          widget has focus (QLineEdit / QTextEdit / QPlainTextEdit /
          QAbstractSpinBox / editable QComboBox), to preserve
          standard Qt-app conventions (Esc clears the field, closes
          a popup, etc.) when the user might plausibly be typing
          something they don't want to lose. Once focus is on the
          list / tree / button / empty area, Esc dismisses to tray.

        Wired in v1.10.17, made focus-aware in v1.10.18, made
        always-hide-on-SuperLookup in v1.10.19.
        """
        if not hasattr(self, 'main_tabs'):
            return
        has_tray = getattr(self, '_tray_icon', None) is not None

        superlookup_idx = getattr(self, 'superlookup_tab_index', None)
        voice_idx = getattr(self, 'voice_tab_index', None)
        current = self.main_tabs.currentIndex()

        # SuperLookup: unconditional hide (tray required).
        if superlookup_idx is not None and current == superlookup_idx:
            if has_tray:
                self.hide()
            return

        # Voice: focus-aware hide.
        if voice_idx is None or current != voice_idx:
            # Some other tab – not a quick-lookup surface, do nothing.
            return

        try:
            from PyQt6.QtWidgets import (
                QLineEdit, QTextEdit, QPlainTextEdit, QComboBox,
                QAbstractSpinBox,
            )
            focused = QApplication.focusWidget()
            if focused is not None:
                text_input_types = (
                    QLineEdit, QTextEdit, QPlainTextEdit,
                    QAbstractSpinBox,
                )
                if isinstance(focused, text_input_types):
                    self.log(f"[Esc] ignored – text input focused "
                             f"({type(focused).__name__})")
                    return
                if isinstance(focused, QComboBox) and focused.isEditable():
                    self.log("[Esc] ignored – editable combo focused")
                    return
                # Walk up: a QLineEdit nested inside a custom
                # composite widget should also count as a text input.
                w = focused.parent()
                hops = 0
                while w is not None and hops < 5:
                    if isinstance(w, text_input_types):
                        return
                    w = w.parent()
                    hops += 1
        except Exception:
            # If the introspection fails for any reason, fall
            # through to dismiss – safer than silently doing nothing.
            pass

        # Voice: hide to tray, as before.
        if has_tray:
            self.hide()

    def _on_toggle_close_to_tray(self, checked: bool):
        prefs = self._load_settings_section("ui")
        prefs["close_to_tray"] = bool(checked)
        self._save_settings_section("ui", prefs)

    def _on_toggle_start_minimized(self, checked: bool):
        prefs = self._load_settings_section("ui")
        prefs["start_minimized_to_tray"] = bool(checked)
        self._save_settings_section("ui", prefs)

    def _on_toggle_autostart(self, checked: bool):
        """Register or unregister Supervertaler with the OS autostart facility."""
        mod = getattr(self, "_autostart_mod", None)
        if mod is None:
            return
        ok = mod.enable() if checked else mod.disable()
        # Re-sync the checkbox with reality – if the registry / plist write
        # failed silently, don't leave the menu showing a stale state.
        actual = mod.is_enabled()
        if actual != checked:
            self._tray_autostart_action.blockSignals(True)
            self._tray_autostart_action.setChecked(actual)
            self._tray_autostart_action.blockSignals(False)
        if ok and getattr(self, "_tray_icon", None) is not None and checked:
            try:
                from PyQt6.QtWidgets import QSystemTrayIcon
                self._tray_icon.showMessage(
                    "Supervertaler",
                    "Will start automatically with your computer. "
                    "Tip: enable 'Start minimized to tray' to skip the window at boot.",
                    QSystemTrayIcon.MessageIcon.Information,
                    4000,
                )
            except Exception:
                pass

    def _tray_quit(self):
        """Real quit from tray menu – bypasses the close-to-tray hide."""
        self._really_quit = True
        # closeEvent does the cleanup and quits. close() returns False when
        # the user cancels (unsaved changes → Cancel); quitting anyway would
        # throw their work away.
        if self.close():
            QApplication.instance().quit()
