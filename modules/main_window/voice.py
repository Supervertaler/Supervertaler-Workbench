"""Voice commands and Always-On listening: the command table, starting and
stopping the listener, its tray icon, and what happens when it hears something.

Part of the main window (SupervertalerQt), moved here unchanged. The
methods run as the main window's own: `self` is the SupervertalerQt.
"""

import subprocess
import traceback
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QBrush, QColor, QIcon
from PyQt6.QtWidgets import QMenu, QMessageBox, QPushButton
from modules.voice_commands import ContinuousVoiceListener


class VoiceMixin:
    def _populate_voice_commands_table(self):
        """Refresh the Voice command table in Sidekick (if open).

        Called whenever the underlying voice_command_manager has changed
        (commands added, edited, removed, or reset).
        """
        if not hasattr(self, 'voice_command_manager'):
            return
        # Refresh the in-Workbench Voice top tab's command table if
        # it's been built (it's lazy-constructed on first activation).
        # Pre-v1.10.4 this also refreshed Sidekick's voice tab; with
        # Sidekick retired only the top tab needs poking.
        voice_widget = getattr(self, '_voice_top_widget', None)
        if voice_widget is not None and hasattr(voice_widget, '_populate_table'):
            try:
                voice_widget._populate_table()
            except Exception:
                pass

    def _reset_voice_commands(self):
        """Reset voice commands to defaults"""
        reply = QMessageBox.question(
            self, "Reset Commands",
            "Reset all voice commands to defaults?\n\nThis will remove any custom commands you've added.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        
        if reply == QMessageBox.StandardButton.Yes:
            self.voice_command_manager.commands = self.voice_command_manager._default_commands()
            self.voice_command_manager.save_commands()
            self._populate_voice_commands_table()
            QMessageBox.information(self, "Reset Complete", "Voice commands have been reset to defaults.")

    def _check_ahk_installed(self) -> str:
        """Check if AutoHotkey is installed"""
        ahk_exe = self.voice_command_manager._find_ahk_executable() if hasattr(self, 'voice_command_manager') else None
        if ahk_exe:
            return "✅ AutoHotkey detected"
        else:
            return "⚠️ AutoHotkey not found - install from autohotkey.com for system commands"

    def _open_voice_scripts_folder(self):
        """Open the voice scripts folder"""
        import subprocess
        scripts_folder = self.user_data_path / "workbench" / "voice_scripts"
        scripts_folder.mkdir(parents=True, exist_ok=True)
        subprocess.Popen(['explorer', str(scripts_folder)])

    def _toggle_alwayson_listening(self):
        """Toggle always-on voice listening mode"""
        if self.voice_listener and self.voice_listener.is_listening:
            # Stop listening
            self.voice_listener.stop()
            self.voice_listener = None
            self._update_alwayson_ui("stopped")
            self.log("🔇 Always-on listening stopped")
            self.status_bar.showMessage("🔇 Always-on listening stopped", 3000)
        else:
            # Start listening
            try:
                # The voice settings still live under the historical
                # ``dictation_settings`` key in the unified settings file.
                voice_settings = self.load_dictation_settings()
                self.voice_listener = ContinuousVoiceListener(
                    command_manager=self.voice_command_manager,
                    user_data_path=str(self.user_data_path) if hasattr(self, 'user_data_path') else None,
                    mic_device=voice_settings.get('mic_device'),
                )

                # Set sensitivity from persisted settings (the Voice tab
                # writes this key directly via _set_voice_settings).
                sensitivity = voice_settings.get('alwayson_sensitivity', 'medium')
                if sensitivity in ('low', 'medium', 'high'):
                    self.voice_listener.set_sensitivity(sensitivity)

                # Connect signals
                self.voice_listener.speech_detected.connect(self._on_alwayson_speech)
                self.voice_listener.command_detected.connect(self._on_alwayson_command)
                self.voice_listener.no_command_matched.connect(self._on_alwayson_no_match)
                self.voice_listener.status_update.connect(self._on_alwayson_status)
                self.voice_listener.error_occurred.connect(self._on_alwayson_error)
                self.voice_listener.vad_status_changed.connect(self._on_alwayson_vad_status)
                self.voice_listener.listening_started.connect(lambda: self._update_alwayson_ui("listening"))
                self.voice_listener.listening_stopped.connect(lambda: self._update_alwayson_ui("stopped"))
                self.voice_listener.stopped_unexpectedly.connect(self._on_alwayson_listener_died)

                self.voice_listener.start()
                self.log("🎧 Always-on listening started (Vosk – offline, commands only)")

            except Exception as e:
                import traceback
                self.log(f"❌ Failed to start always-on listening: {e}")
                self.log(traceback.format_exc())
                QMessageBox.critical(self, "Always-On Error", f"Failed to start always-on listening:\n\n{e}")

    def _update_alwayson_ui(self, status: str):
        """Update the always-on UI elements"""
        # Update settings panel UI (if visible)
        if hasattr(self, 'alwayson_status_label'):
            if status == "listening" or status == "waiting":
                self.alwayson_status_label.setText(self.tr("🟢 Listening for speech..."))
                self.alwayson_status_label.setStyleSheet("font-size: 9pt; padding: 5px; color: #2E7D32;")
                self.alwayson_toggle_btn.setText(self.tr("⏹️ Stop Always-On Listening"))
                self.alwayson_toggle_btn.setStyleSheet("padding: 8px 15px; background-color: #FFCDD2;")
            elif status == "recording":
                self.alwayson_status_label.setText(self.tr("🔴 Recording..."))
                self.alwayson_status_label.setStyleSheet("font-size: 9pt; padding: 5px; color: #C62828;")
            elif status == "processing":
                self.alwayson_status_label.setText(self.tr("⏳ Processing..."))
                self.alwayson_status_label.setStyleSheet("font-size: 9pt; padding: 5px; color: #F57C00;")
            else:  # stopped or other
                self.alwayson_status_label.setText(self.tr("⚪ Not active"))
                self.alwayson_status_label.setStyleSheet("font-size: 9pt; padding: 5px;")
                self.alwayson_toggle_btn.setText(self.tr("▶️ Start Always-On Listening"))
                self.alwayson_toggle_btn.setStyleSheet("padding: 8px 15px;")
        
        # Update status bar indicator (always visible when active)
        if hasattr(self, 'alwayson_indicator_label'):
            if status == "listening" or status == "waiting":
                self.alwayson_indicator_label.setText(self.tr("🎤 ALWAYS-ON"))
                self.alwayson_indicator_label.setStyleSheet("font-size: 11px; font-weight: bold; color: #2E7D32; background-color: #C8E6C9; padding: 2px 6px; border-radius: 3px;")
                self.alwayson_indicator_label.setToolTip(self.tr("Always-on voice listening ACTIVE\nClick to stop"))
                self.alwayson_indicator_label.show()
            elif status == "recording":
                self.alwayson_indicator_label.setText(self.tr("🔴 REC"))
                self.alwayson_indicator_label.setStyleSheet("font-size: 11px; font-weight: bold; color: white; background-color: #C62828; padding: 2px 6px; border-radius: 3px;")
                self.alwayson_indicator_label.setToolTip(self.tr("Recording speech..."))
                self.alwayson_indicator_label.show()
            elif status == "processing":
                self.alwayson_indicator_label.setText("⏳ ...")
                self.alwayson_indicator_label.setStyleSheet("font-size: 11px; font-weight: bold; color: #F57C00; background-color: #FFF3E0; padding: 2px 6px; border-radius: 3px;")
                self.alwayson_indicator_label.setToolTip(self.tr("Processing speech..."))
                self.alwayson_indicator_label.show()
            else:  # stopped or other
                self.alwayson_indicator_label.hide()
        
        # Update grid toolbar button (if exists)
        if hasattr(self, 'grid_alwayson_btn'):
            if status == "listening" or status == "waiting":
                self.grid_alwayson_btn.setText(self.tr("🎧 Always-On: ON"))
                self.grid_alwayson_btn.setChecked(True)
                self.grid_alwayson_btn.setStyleSheet("""
                    QPushButton {
                        background-color: #2E7D32;
                        color: white;
                        font-weight: bold;
                        padding: 3px 5px;
                        border-radius: 3px;
                    }
                    QPushButton:checked {
                        background-color: #2E7D32;
                    }
                """)
            elif status == "recording":
                self.grid_alwayson_btn.setText(self.tr("🔴 REC"))
                self.grid_alwayson_btn.setStyleSheet("""
                    QPushButton {
                        background-color: #C62828;
                        color: white;
                        font-weight: bold;
                        padding: 3px 5px;
                        border-radius: 3px;
                    }
                    QPushButton:checked {
                        background-color: #C62828;
                    }
                """)
            elif status == "processing":
                self.grid_alwayson_btn.setText("⏳ ...")
                self.grid_alwayson_btn.setStyleSheet("""
                    QPushButton {
                        background-color: #F57C00;
                        color: white;
                        font-weight: bold;
                        padding: 3px 5px;
                        border-radius: 3px;
                    }
                    QPushButton:checked {
                        background-color: #F57C00;
                    }
                """)
            else:  # stopped or other
                self.grid_alwayson_btn.setText(self.tr("🎧 Always-On: OFF"))
                self.grid_alwayson_btn.setChecked(False)
                self.grid_alwayson_btn.setStyleSheet("""
                    QPushButton {
                        background-color: #757575;
                        color: white;
                        font-weight: bold;
                        padding: 3px 5px;
                        border-radius: 3px;
                    }
                    QPushButton:checked {
                        background-color: #2E7D32;
                    }
                """)

        # Update the in-Workbench Voice top tab's status indicator if
        # it has been built (lazy on first activation). Pre-v1.10.4
        # this also poked Sidekick's voice widget; Sidekick is gone.
        voice_widget = getattr(self, '_voice_top_widget', None)
        if voice_widget is not None and hasattr(voice_widget, 'set_alwayson_status'):
            try:
                voice_widget.set_alwayson_status(status)
            except Exception:
                pass

        # System tray icon – visible only while Always-On is active.
        # Gives the user a persistent "the mic is hot" signal even when
        # Workbench is hidden.
        self._update_alwayson_tray_icon(status)

    @staticmethod
    def _draw_mic_icon(color):
        """Draw a small microphone glyph as a QIcon, in the given QColor.

        Used by the Always-On tray indicator. Two visual states share the
        same shape but differ in colour: grey when Always-On is off, red
        when it's actively listening. Drawing programmatically avoids
        bundling an extra asset and keeps colour-swaps trivial.
        """
        from PyQt6.QtGui import QIcon, QPixmap, QPainter, QColor, QPen, QBrush
        size = 32
        pix = QPixmap(size, size)
        pix.fill(QColor(0, 0, 0, 0))
        p = QPainter(pix)
        try:
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            qcol = QColor(color) if not isinstance(color, QColor) else color
            stroke = max(1.5, size * 0.06)

            # Mic body – vertical capsule centred horizontally
            body_w = size * 0.42
            body_h = size * 0.50
            body_x = (size - body_w) / 2
            body_y = size * 0.10
            p.setBrush(QBrush(qcol))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawRoundedRect(
                int(body_x), int(body_y), int(body_w), int(body_h),
                int(body_w / 2), int(body_w / 2),
            )

            # U-shaped pickup arc cradling the body
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(qcol, stroke, Qt.PenStyle.SolidLine,
                          Qt.PenCapStyle.RoundCap))
            arc_x = size * 0.16
            arc_y = size * 0.30
            arc_w = size * 0.68
            arc_h = size * 0.50
            # Qt uses 1/16ths of a degree; 0° is at 3 o'clock, going CCW.
            p.drawArc(int(arc_x), int(arc_y), int(arc_w), int(arc_h),
                      200 * 16, 140 * 16)

            # Stand (vertical) + base (horizontal)
            cx = size / 2
            p.drawLine(int(cx), int(size * 0.80), int(cx), int(size * 0.90))
            p.drawLine(int(cx - size * 0.18), int(size * 0.90),
                       int(cx + size * 0.18), int(size * 0.90))
        finally:
            p.end()
        return QIcon(pix)

    def _ensure_alwayson_tray_icon(self):
        """Lazily create the QSystemTrayIcon used as a global Always-On
        toggle and visual state indicator.

        A small microphone glyph drawn programmatically – grey when
        Always-On is off, red when actively listening. Convention follows
        OBS / Zoom / Discord (grey = ready/off, red = live).

        The icon is created once and stays visible for the lifetime of
        the Workbench window. Showing/hiding a tray slot causes Windows
        to reflow the tray (the "icons bounce" effect), so the slot
        stays allocated and only the pixmap, tooltip, and menu wording
        change as state changes.

        Single-click toggles Always-On. Right-click shows a small menu
        with the same toggle plus an "Open Voice in Sidekick"
        shortcut.
        """
        if hasattr(self, '_alwayson_tray_icon'):
            return
        from PyQt6.QtWidgets import QSystemTrayIcon, QMenu
        from PyQt6.QtGui import QColor

        if not QSystemTrayIcon.isSystemTrayAvailable():
            self._alwayson_tray_icon = None
            return

        # Material grey 600 (off) and red 800 (live).
        self._alwayson_tray_icon_normal = self._draw_mic_icon(QColor(0x75, 0x75, 0x75))
        self._alwayson_tray_icon_red = self._draw_mic_icon(QColor(0xC6, 0x28, 0x28))

        tray = QSystemTrayIcon(self._alwayson_tray_icon_normal, self)
        tray.setToolTip(self.tr("Voice – Always-On is OFF. Click to start."))

        menu = QMenu(self)
        toggle_action = menu.addAction("▶ Start Always-On")
        toggle_action.triggered.connect(self._toggle_alwayson_listening)
        menu.addSeparator()
        open_action = menu.addAction("🎤 Open Voice")
        open_action.triggered.connect(self._open_voice_in_workbench)
        tray.setContextMenu(menu)
        self._alwayson_tray_toggle_action = toggle_action

        tray.activated.connect(self._on_alwayson_tray_activated)
        self._alwayson_tray_icon = tray
        tray.show()

    def _on_alwayson_tray_activated(self, reason):
        """Tray-icon click handler – single-click toggles Always-On."""
        from PyQt6.QtWidgets import QSystemTrayIcon
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self._toggle_alwayson_listening()

    def _update_alwayson_tray_icon(self, status: str):
        """Switch the tray icon between inactive/active visuals based on
        the current listener status. The icon stays visible at all times
        – only the pixmap, tooltip, and menu wording change."""
        self._ensure_alwayson_tray_icon()
        tray = getattr(self, '_alwayson_tray_icon', None)
        if tray is None:
            return

        active = status in ("listening", "waiting", "recording", "processing")

        # Skip every tray API call when the active/inactive state hasn't changed.
        # setIcon() and setToolTip() both trigger a Windows notification-area
        # repaint that briefly hides the icon. The VAD cycle emits three status
        # changes per spoken command (recording → processing → listening), all of
        # which map to the same "active" state, so we can safely ignore them.
        if getattr(self, '_alwayson_tray_active', None) == active:
            return
        self._alwayson_tray_active = active

        tray.setIcon(
            self._alwayson_tray_icon_red if active
            else self._alwayson_tray_icon_normal
        )
        tray.setToolTip(
            "Voice – Always-On listening. Click to stop." if active
            else "Voice – Always-On is OFF. Click to start."
        )
        action = getattr(self, '_alwayson_tray_toggle_action', None)
        if action is not None:
            action.setText("⏹ Stop Always-On" if active else "▶ Start Always-On")

    def _on_alwayson_speech(self, text: str):
        """Handle raw speech from always-on listener"""
        self.log(f"🎤 Heard: {text}")

    def _on_alwayson_command(self, heard: str, result: str):
        """A command ran (or failed to): log it, flash it in the status
        bar and list it under "What I heard" on the Voice tab."""
        self.log(f"🎤 Command: {heard} → {result}")
        self.status_bar.showMessage(f"🎤 {result}", 3000)
        self._add_to_voice_heard_list(heard, result, ok=not result.startswith("✗"))

    def _on_alwayson_no_match(self, text: str):
        """Speech from the always-on listener that matched no command.

        It is reported and never typed. Voice only runs commands;
        running text belongs to a dictation tool (Wispr Flow, Windows
        voice typing). Up to v1.10.373 such speech was typed into the
        active app, so Vosk's leftovers ("[unk] project tm next add
        [unk]") landed in whatever window had focus.
        """
        self.log(f"💬 No command matched, not typed: {text!r}")
        self.status_bar.showMessage(f"🎤 No command matched: “{text}”", 3000)
        self._add_to_voice_heard_list(text, "no command matched", ok=None)

    def _add_to_voice_heard_list(self, heard: str, outcome: str, ok):
        """Feed the Voice tab's "What I heard" list, if the tab is built."""
        voice_widget = getattr(self, '_voice_top_widget', None)
        if voice_widget is not None:
            voice_widget.add_heard(heard, outcome, ok)

    def _on_alwayson_status(self, message: str):
        """Handle status updates from always-on listener"""
        self.log(message)

    def _on_alwayson_error(self, error: str):
        """Handle errors from always-on listener"""
        self.log(f"❌ Always-on error: {error}")
        QMessageBox.warning(self, "Always-On Error", f"Voice listener error:\n\n{error}")

    def _on_alwayson_listener_died(self):
        """The listener stopped without being asked to; it has already
        reported why through _on_alwayson_error and set the UI to
        stopped through listening_stopped."""
        self.log("🔇 Always-on listener stopped unexpectedly")

    def _on_alwayson_vad_status(self, status: str):
        """Handle VAD status changes (loading/waiting/recording/processing)."""
        self._update_alwayson_ui(status)

    def _toggle_alwayson_from_statusbar(self):
        """Toggle always-on listening when clicking the status bar indicator"""
        self._toggle_alwayson_listening()

    def _toggle_alwayson_from_grid_btn(self, checked: bool, btn: QPushButton):
        """Toggle always-on listening from the grid toolbar button"""
        self._toggle_alwayson_listening()

    def _open_voice_in_workbench(self):
        """Open Workbench's Voice top tab from a Settings link / tray menu.

        Method was renamed from _open_voice_in_sidekick in v1.10.10
        when Sidekick was retired. The two existing call sites
        (Voice settings link and Always-On tray menu) were updated
        to match.
        """
        try:
            if hasattr(self, '_ensure_voice_top_tab'):
                self._ensure_voice_top_tab()
            self._bring_workbench_forward()
            if hasattr(self, 'voice_tab_index') and hasattr(self, 'main_tabs'):
                self.main_tabs.setCurrentIndex(self.voice_tab_index)
        except Exception as e:
            QMessageBox.warning(self, "Could not open Voice", str(e))
