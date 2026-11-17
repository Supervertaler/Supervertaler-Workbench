"""The main tabs beside the grid: creating them when first needed, the log
and Superdocs tabs, and detaching and reattaching Superlookup.

Part of the main window (SupervertalerQt), moved here unchanged. The
methods run as the main window's own: `self` is the SupervertalerQt.
"""

import os as _os
import sys as _sys
import threading
import traceback
from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)
from modules.main_dialogs import DetachedLogWindow
from modules.superlookup_tab import SuperlookupTab


class MainTabsMixin:
    def create_superdocs_tab(self) -> QWidget:
        """Create the Help tab - Online Documentation Viewer"""
        # The embedded docs viewer was removed in favor of online documentation.
        placeholder = QWidget()
        layout = QVBoxLayout(placeholder)
        label = QLabel(self.tr("📚 Supervertaler Help is now available online.\n\nVisit https://docs.supervertaler.com/workbench/ to view the documentation."))
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setStyleSheet("color: #888; font-size: 12px;")
        layout.addWidget(label)
        return placeholder

    def create_log_tab(self) -> QWidget:
        """Create the Log tab - Session Log"""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)

        # Top toolbar with detach button
        toolbar = QWidget()
        toolbar_layout = QHBoxLayout(toolbar)
        toolbar_layout.setContentsMargins(0, 0, 0, 0)

        detach_btn = QPushButton(self.tr("🪟 Detach Log Window"))
        detach_btn.setToolTip(self.tr("Open log in a separate window that can be moved to another screen"))
        detach_btn.clicked.connect(self.detach_log_window)
        toolbar_layout.addWidget(detach_btn)

        clear_btn = QPushButton(self.tr("🗑️ Clear Log"))
        clear_btn.setToolTip(self.tr("Clear all log messages"))
        clear_btn.clicked.connect(self.clear_log)
        toolbar_layout.addWidget(clear_btn)

        toolbar_layout.addStretch()
        layout.addWidget(toolbar)

        # Log display area
        self.session_log = QPlainTextEdit()
        self.session_log.setReadOnly(True)
        self.session_log.setStyleSheet("""
            QPlainTextEdit {
                font-family: 'Consolas', 'Courier New', monospace;
                font-size: 9px;
                border: 1px solid #ccc;
            }
        """)
        layout.addWidget(self.session_log)

        # Initialize with welcome message
        self.session_log.setPlainText("Session Log - Ready\n" + "="*50 + "\n")

        # List to track detached log windows
        self.detached_log_windows = []

        return tab

    def detach_log_window(self):
        """Create a detached log window"""
        # The list is normally created in create_log_tab() (Settings ▸ Log),
        # but this entry point can fire before Settings has been built, so
        # initialise on demand.
        if not hasattr(self, 'detached_log_windows'):
            self.detached_log_windows = []
        detached_window = DetachedLogWindow(self)
        detached_window.show()
        detached_window.raise_()
        detached_window.activateWindow()
        self.detached_log_windows.append(detached_window)
        self.log("🪟 Log window detached")

    def clear_log(self):
        """Clear the log"""
        reply = QMessageBox.question(
            self,
            "Clear Log",
            "Are you sure you want to clear the log?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.session_log.setPlainText("Session Log - Ready\n" + "="*50 + "\n")
            # Also clear detached windows
            for window in self.detached_log_windows:
                if window and not window.isHidden():
                    window.log_display.setPlainText("Session Log - Ready\n" + "="*50 + "\n")

    def _on_main_tab_changed(self, index: int):
        """Handle main tab changes (Grid/Project resources/Tools/Settings)"""
        try:
            # Lazy-construct the v1.10.0 SuperLookup / Voice
            # top tabs on first activation. Each helper is idempotent –
            # safe to call again on subsequent activations (it short-
            # circuits if already built). Keeps cold start fast by
            # deferring the heavy SuperLookup / Voice widget construction
            # until the user actually visits the tab.
            if hasattr(self, 'superlookup_tab_index') and index == self.superlookup_tab_index:
                self._ensure_superlookup_top_tab()
            elif hasattr(self, 'voice_tab_index') and index == self.voice_tab_index:
                self._ensure_voice_top_tab()

            if index == 0:  # Grid
                # Grid refreshes automatically when segments change
                pass
            elif index == 1:  # Project resources
                # Refresh AI Assistant LLM client when switching to Project resources tab
                # Prompts tab is inside Project resources, so refresh LLM settings when going there
                if hasattr(self, 'prompt_manager_qt'):
                    llm_settings = self.load_llm_settings()
                    self.current_provider = llm_settings.get('provider', 'openai')
                    provider_key = f"{self.current_provider}_model"
                    self.current_model = llm_settings.get(provider_key)
                    # Reinitialize AI Assistant's LLM client
                    self.prompt_manager_qt._init_llm_client()
        except Exception as e:
            self.log(f"⚠️ Error switching main tabs: {e}")
            import traceback
            traceback.print_exc()

    def _warm_up_top_tabs(self):
        """Build the new SuperLookup / Voice top tabs in the
        background a few seconds after launch.

        Each ``_ensure_*_top_tab`` is idempotent – calling it here just
        forces eager construction before the user's first hotkey press
        instead of paying the cost on that press. Wrapped in
        per-helper try/except so a failure in one doesn't skip the
        others.

        Also kicks off an AHK pre-warm so the very first synthetic
        keystroke (SuperLookup's copy, a voice command) doesn't lose
        it to AHK's cold-spawn latency (Windows + AV scan can stretch the
        first ``AutoHotkey64.exe`` launch to 1–2 s; subsequent
        launches hit a hot disk cache and run in ~150 ms).
        """
        for helper in ('_ensure_superlookup_top_tab',
                       '_ensure_voice_top_tab'):
            try:
                fn = getattr(self, helper, None)
                if callable(fn):
                    fn()
            except Exception as e:
                self.log(f"⚠ Warm-up of {helper} failed (lazy path still works): {e}")
        try:
            self._prewarm_ahk()
        except Exception as e:
            self.log(f"⚠ AHK pre-warm failed (first paste may be slow): {e}")

    def _prewarm_ahk(self):
        """Spawn AHK once with a no-op script so the binary is in disk
        cache and the first real Ctrl+V / Ctrl+C from
        :class:`CrossPlatformKeySender` doesn't take an extra second
        on cold disk. No-op when AHK isn't installed or we're not on
        Windows. Runs in a background thread so it doesn't block the
        warm-up timer.
        """
        import sys as _sys
        if _sys.platform != 'win32':
            return
        import threading

        def _do_prewarm():
            try:
                from modules.platform_helpers import CrossPlatformKeySender
                ahk = CrossPlatformKeySender()._find_ahk()
                if not ahk:
                    return
                import subprocess as _sp
                import tempfile as _tf
                # Empty script body; just exits. AHK's spin-up cost
                # is paid here so the first user-triggered paste is fast.
                with _tf.NamedTemporaryFile(
                        mode='w', suffix='.ahk', delete=False,
                        encoding='utf-8') as f:
                    f.write('ExitApp\n')
                    tmp = f.name
                try:
                    _sp.run([ahk, '/ErrorStdOut', tmp],
                            timeout=10,
                            creationflags=getattr(_sp, 'CREATE_NO_WINDOW', 0))
                finally:
                    import os as _os
                    try:
                        _os.unlink(tmp)
                    except OSError:
                        pass
            except Exception as e:
                print(f"[AHK pre-warm] {e}")

        t = threading.Thread(target=_do_prewarm, daemon=True,
                             name="AHK-prewarm")
        t.start()

    def _ensure_superlookup_top_tab(self):
        """Build the SuperLookup top tab the first time the user opens it.

        Heavy: SuperlookupTab.__init__ brings up TM / termbase / MT and
        web-resource state. Deferring until first activation keeps cold
        start fast. The hotkey-side-effect SuperLookup instance from
        _setup_superlookup_hotkeys already owns the global hotkeys; this
        separate instance powers the visible top tab and passes
        register_hotkeys=False so it does NOT re-attempt registration
        (which used to fail with a misleading "claimed by another app"
        warning against our own owner instance).
        """
        if self._superlookup_top_widget is not None:
            return
        try:
            widget = SuperlookupTab(
                self,
                user_data_path=getattr(self, 'user_data_path', None),
                register_hotkeys=False,
            )
            self._superlookup_top_widget = widget
            placeholder = self.main_tabs.widget(self.superlookup_tab_index)
            layout = QVBoxLayout(placeholder)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.addWidget(widget)
        except Exception as e:
            self.log(f"⚠ Could not build SuperLookup top tab: {e}")
            import traceback
            traceback.print_exc()

    def _ensure_voice_top_tab(self):
        """Build the Voice top tab on first activation.

        The widget (modules.voice_tab.VoiceTab) reads parent_app
        properties (shortcut_manager, voice_listener,
        load_dictation_settings).
        """
        if self._voice_top_widget is not None:
            return
        try:
            from modules.voice_tab import VoiceTab
            widget = VoiceTab(self)
            self._voice_top_widget = widget
            placeholder = self.main_tabs.widget(self.voice_tab_index)
            layout = QVBoxLayout(placeholder)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.addWidget(widget)
        except Exception as e:
            self.log(f"⚠ Could not build Voice top tab: {e}")
            import traceback
            traceback.print_exc()

    def detach_superlookup(self):
        """Detach Superlookup into a separate window for second screen use"""
        from PyQt6.QtWidgets import QDialog, QVBoxLayout, QPushButton
        
        try:
            # If already detached, just show the window
            if hasattr(self, 'lookup_detached_window') and self.lookup_detached_window and self.lookup_detached_window.isVisible():
                self.lookup_detached_window.raise_()
                self.lookup_detached_window.activateWindow()
                return
            
            # Create detached window
            self.lookup_detached_window = QDialog(self)
            self.lookup_detached_window.setWindowTitle(self.tr("🔍 SuperLookup - Supervertaler Workbench"))
            self.lookup_detached_window.setMinimumSize(600, 700)
            self.lookup_detached_window.resize(700, 800)
            
            # Set window flags to ensure it appears as a proper window
            self.lookup_detached_window.setWindowFlags(
                Qt.WindowType.Window | 
                Qt.WindowType.WindowCloseButtonHint | 
                Qt.WindowType.WindowMinimizeButtonHint |
                Qt.WindowType.WindowMaximizeButtonHint
            )
            
            # Position window to the right of main window, or center on same screen if no space
            main_geometry = self.geometry()
            
            # Get the screen that contains the main window (handles multi-monitor setups)
            app = QApplication.instance()
            screen = app.screenAt(main_geometry.center())
            if not screen:
                # Fallback to primary screen
                screen = app.primaryScreen()
            
            screen_geometry = screen.geometry()
            
            # Try to place to the right of main window
            new_x = main_geometry.right() + 20
            new_y = main_geometry.top()
            
            # If window would go off-screen to the right, center it on the main window's screen
            if new_x + 700 > screen_geometry.right():
                # Center horizontally on screen, place above main window
                new_x = screen_geometry.left() + (screen_geometry.width() - 700) // 2
                new_y = max(screen_geometry.top() + 50, main_geometry.top() - 100)
                
            # Ensure window stays on screen
            new_x = max(screen_geometry.left() + 10, min(new_x, screen_geometry.right() - 710))
            new_y = max(screen_geometry.top() + 10, min(new_y, screen_geometry.bottom() - 810))
            
            self.lookup_detached_window.move(int(new_x), int(new_y))
            
            # Make sure window is raised and activated - use QTimer to ensure it's after window is shown
            QTimer.singleShot(100, lambda: (
                self.lookup_detached_window.raise_(),
                self.lookup_detached_window.activateWindow(),
                self.lookup_detached_window.setFocus()
            ))
            
            layout = QVBoxLayout(self.lookup_detached_window)
            layout.setContentsMargins(10, 10, 10, 10)
            layout.setSpacing(5)
            
            # Header with reattach button
            header_layout = QVBoxLayout()
            
            header_title = QLabel(self.tr("🔍 SuperLookup"))
            header_title.setStyleSheet("font-size: 16px; font-weight: bold; color: #333;")
            header_layout.addWidget(header_title)
            
            button_layout = QVBoxLayout()
            reattach_btn = QPushButton(self.tr("📥 Attach to Main Window"))
            reattach_btn.setToolTip(self.tr("Re-attach SuperLookup to the Home tab"))
            reattach_btn.setStyleSheet("font-size: 9pt; padding: 4px 12px; max-width: 200px;")
            reattach_btn.clicked.connect(self.reattach_superlookup)
            button_layout.addWidget(reattach_btn, alignment=Qt.AlignmentFlag.AlignRight)
            header_layout.addLayout(button_layout)
            
            layout.addLayout(header_layout)
            
            # Create new Superlookup instance for detached window
            # Or move the existing one - better to create new to avoid widget parenting issues.
            # register_hotkeys=False: the hidden owner instance (self.lookup_tab)
            # already holds the global hotkeys; a detached instance re-registering
            # would just collide with it and log a spurious warning.
            detached_lookup = SuperlookupTab(
                self.lookup_detached_window,
                user_data_path=self.user_data_path,
                register_hotkeys=False,
            )
            
            # Explicitly copy theme_manager reference
            detached_lookup.theme_manager = self.theme_manager
            
            # Copy TM database reference if available
            if hasattr(self, 'tm_database') and self.tm_database:
                detached_lookup.tm_database = self.tm_database
                if detached_lookup.engine:
                    detached_lookup.engine.set_tm_database(self.tm_database)
            
            # Copy home lookup state if it exists
            if hasattr(self, 'home_lookup_widget') and self.home_lookup_widget:
                # Copy source text
                source_text = self.home_lookup_widget.source_text.toPlainText()
                detached_lookup.source_text.setPlainText(source_text)
                
                # Copy TM database reference
                if hasattr(self.home_lookup_widget, 'tm_database'):
                    detached_lookup.tm_database = self.home_lookup_widget.tm_database
            
            layout.addWidget(detached_lookup, stretch=1)
            
            # Store reference for cleanup
            self.lookup_detached_widget = detached_lookup
            
            # Handle window close
            def on_close():
                self.lookup_detached_window = None
                self.lookup_detached_widget = None
            
            self.lookup_detached_window.finished.connect(on_close)
            
            # Show window (non-modal)
            self.lookup_detached_window.setWindowModality(Qt.WindowModality.NonModal)
            self.lookup_detached_window.show()
            self.log("Superlookup detached to separate window")
        except Exception as e:
            import traceback
            error_msg = f"Error detaching Superlookup: {str(e)}\n{traceback.format_exc()}"
            self.log(error_msg)
            QMessageBox.warning(self, "Error", f"Could not detach Superlookup:\n{str(e)}")

    def reattach_superlookup(self):
        """Re-attach Superlookup to the Home tab"""
        if not self.lookup_detached_window:
            return
        
        # Copy state back to home widget if it exists
        if (hasattr(self, 'home_lookup_widget') and self.home_lookup_widget and 
            hasattr(self, 'lookup_detached_widget') and self.lookup_detached_widget):
            # Copy source text
            source_text = self.lookup_detached_widget.source_text.toPlainText()
            self.home_lookup_widget.source_text.setPlainText(source_text)
        
        # Close detached window
        self.lookup_detached_window.close()
        self.lookup_detached_window = None
        if hasattr(self, 'lookup_detached_widget'):
            self.lookup_detached_widget = None
        
        self.log("Superlookup re-attached to Home tab")

    def _setup_superlookup_hotkeys(self):
        """Construct the (hidden) SuperlookupTab so its __init__ registers
        the global hotkeys (Ctrl+Alt+L, Ctrl+Alt+Q, Alt+K).

        Pre-v1.9.467 this lived inside create_specialised_tools_tab(),
        which also built the embedded Tools-tab UI. The Tools tab has
        been retired; the SuperlookupTab construction stays because the
        global hotkey registration happens as a side-effect of __init__.

        The Voice tab itself is built separately (_ensure_voice_top_tab).
        """
        lookup_tab = SuperlookupTab(self, user_data_path=self.user_data_path)
        lookup_tab.hide()  # Never shown – purely for hotkey side effects
        self.lookup_tab = lookup_tab

        if hasattr(lookup_tab, 'hotkey_registered') and lookup_tab.hotkey_registered:
            failed = getattr(getattr(lookup_tab, '_hotkey_manager', None), 'failed_hotkeys', [])
            if failed:
                self.log(f"⚠ Global hotkeys: some failed to register: {', '.join(failed)}")
            else:
                self.log("⌨ Global hotkeys registered (Ctrl+Alt+L, Ctrl+Alt+Q, Alt+K)")
        else:
            self.log("⚠ Global hotkeys NOT registered – check console for errors")
