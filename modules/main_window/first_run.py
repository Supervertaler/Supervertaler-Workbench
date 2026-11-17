"""The data folder dialog and first-run setup: welcome and the setup wizard.

Part of the main window (SupervertalerQt), moved here unchanged. The
methods run as the main window's own: `self` is the SupervertalerQt.
"""

import traceback
from PyQt6.QtCore import QTimer, QUrl, Qt
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)
from modules.find_replace_qt import FindReplaceHistory
from modules.shortcut_manager import ShortcutManager
from modules.spellcheck_manager import get_spellcheck_manager
from modules.styled_widgets import CheckmarkCheckBox
from modules.user_data_location import get_default_user_data_path, save_user_data_path
from modules.voice_commands import VoiceCommandManager
from pathlib import Path


class FirstRunMixin:
    def _show_data_location_dialog(self):
        """Show dialog to let user choose their data folder location on first run."""
        try:
            from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, 
                                         QPushButton, QLineEdit, QFileDialog, QDialogButtonBox)
            
            dialog = QDialog(self)
            dialog.setWindowTitle(self.tr("Choose Data Folder Location"))
            dialog.setMinimumWidth(550)
            dialog.setModal(True)
            
            layout = QVBoxLayout(dialog)
            layout.setSpacing(15)
            
            # Title
            title_label = QLabel(self.tr("<h2>📁 Choose Your Data Folder</h2>"))
            layout.addWidget(title_label)
            
            # Explanation
            msg_label = QLabel(
                "Supervertaler stores your data in a folder of your choice, which contains things like:<br><br>"
                "• API keys<br>"
                "• Translation memories<br>"
                "• Termbases<br>"
                "• Prompts<br>"
                "• Settings<br><br>"
                "Choose a location that's easy to find and backup.<br>"
                "You can change this later in Settings → General."
            )
            msg_label.setWordWrap(True)
            layout.addWidget(msg_label)
            
            # Path input with browse button
            path_layout = QHBoxLayout()
            
            path_edit = QLineEdit()
            default_path = get_default_user_data_path()
            path_edit.setText(str(default_path))
            path_edit.setMinimumWidth(350)
            path_layout.addWidget(path_edit)
            
            browse_btn = QPushButton(self.tr("Browse..."))
            def browse_folder():
                folder = QFileDialog.getExistingDirectory(
                    dialog, 
                    "Choose Data Folder",
                    str(Path.home())
                )
                if folder:
                    # Append "Supervertaler" if user didn't include it
                    folder_path = Path(folder)
                    if folder_path.name != "Supervertaler":
                        folder_path = folder_path / "Supervertaler"
                    path_edit.setText(str(folder_path))
            
            browse_btn.clicked.connect(browse_folder)
            path_layout.addWidget(browse_btn)
            
            layout.addLayout(path_layout)
            
            # Tip
            tip_label = QLabel(
                "💡 <b>Tip:</b> The default location is in your home folder, "
                "making it easy to find and backup."
            )
            tip_label.setWordWrap(True)
            tip_label.setStyleSheet("color: #666;")
            layout.addWidget(tip_label)
            
            # Buttons
            button_layout = QHBoxLayout()
            
            default_btn = QPushButton(self.tr("Use Default"))
            default_btn.clicked.connect(lambda: path_edit.setText(str(default_path)))
            button_layout.addWidget(default_btn)
            
            button_layout.addStretch()
            
            ok_btn = QPushButton(self.tr("OK"))
            ok_btn.setDefault(True)
            ok_btn.clicked.connect(dialog.accept)
            button_layout.addWidget(ok_btn)
            
            layout.addLayout(button_layout)
            
            # Show dialog
            if dialog.exec() == QDialog.DialogCode.Accepted:
                chosen_path = Path(path_edit.text())
                
                # Create the folder
                chosen_path.mkdir(parents=True, exist_ok=True)
                
                # Save the choice to config pointer
                save_user_data_path(chosen_path)
                
                # Update our path if different from what we initialized with
                if chosen_path != self.user_data_path:
                    self.user_data_path = chosen_path
                    # Re-initialize managers with new path
                    self._reinitialize_with_new_data_path()
                else:
                    # Same path, but we still need to connect database (it was deferred)
                    if hasattr(self, 'db_manager') and self.db_manager and not self.db_manager.connection:
                        self.db_manager.connect()
                
                self.log(f"📁 Data folder set to: {chosen_path}")
                
                # Now show the features welcome dialog
                from PyQt6.QtCore import QTimer
                QTimer.singleShot(300, self._show_first_run_welcome)
            else:
                # User cancelled - use default anyway
                default_path.mkdir(parents=True, exist_ok=True)
                save_user_data_path(default_path)
                self.log(f"📁 Using default data folder: {default_path}")
                
        except Exception as e:
            self.log(f"⚠️ Data location dialog error: {e}")
            import traceback
            traceback.print_exc()

    def _reinitialize_with_new_data_path(self):
        """Re-initialize managers after user changes data path."""
        try:
            # Migrate settings if needed for the new data path
            self._migrate_settings_to_unified()
            self._migrate_to_workbench_layout()

            # Close existing database connection
            if hasattr(self, 'db_manager') and self.db_manager:
                self.db_manager.close()
            
            # Re-initialize database manager
            from modules.database_manager import DatabaseManager
            self.db_manager = DatabaseManager(
                db_path=str(self.user_data_path / "resources" / "supervertaler.db"),
                log_callback=self.log
            )
            self.db_manager.connect()
            
            # Re-initialize TM database
            from modules.translation_memory import TMDatabase
            self.tm_database = TMDatabase(
                source_lang=None,
                target_lang=None,
                db_path=str(self.user_data_path / "resources" / "supervertaler.db"),
                log_callback=self.log
            )
            
            # Re-initialize TM metadata manager
            from modules.tm_metadata_manager import TMMetadataManager
            self.tm_metadata_mgr = TMMetadataManager(self.db_manager, self.log)
            
            # Update other managers
            self.spellcheck_manager = get_spellcheck_manager(str(self.user_data_path))
            self.fr_history = FindReplaceHistory(str(self.user_data_path))
            self.shortcut_manager = ShortcutManager(Path(self.user_data_path) / "workbench" / "settings" / "shortcuts.json")
            self.voice_command_manager = VoiceCommandManager(self.user_data_path, main_window=self)
            
            # Update theme manager
            from modules.theme_manager import ThemeManager
            self.theme_manager = ThemeManager(self.user_data_path)
            # Apply saved global UI font scale
            saved_font_scale = self._get_global_ui_font_scale()
            self.theme_manager.font_scale = saved_font_scale
            self.theme_manager.apply_theme(QApplication.instance())
            
            # Update recent projects file path
            self.recent_projects_file = self.user_data_path / "workbench" / "settings" / "recent_projects.json"
            
            self.log(f"✅ Re-initialized all managers with new data path")
            
        except Exception as e:
            self.log(f"⚠️ Error re-initializing managers: {e}")
            import traceback
            traceback.print_exc()

    def _show_first_run_welcome(self):
        """No-op since v1.9.474.

        This used to pop up an intro dialog about Supervertaler's modular
        feature architecture and route the user to Settings → Features.
        Both the modular-extras system and the Features tab were retired
        when every feature module became a core dependency. The setup
        wizard handles the data-folder confirmation; nothing else needs
        to nag the user on first run. Kept as a stub so any pending
        QTimer.singleShot() callers don't blow up.
        """
        try:
            settings = self.load_general_settings()
            settings['first_run_completed'] = True
            self.save_general_settings(settings)
        except Exception:
            pass

    def _show_setup_wizard(self, is_first_run: bool = False):
        """
        Show unified setup wizard that combines data folder selection and features intro.

        Args:
            is_first_run: If True, this is an automatic first-run trigger. If False, user
                          manually invoked from menu (skip data folder if already configured).
        """
        try:
            from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel,
                                         QPushButton, QLineEdit, QFileDialog, QStackedWidget,
                                         QWidget, QFrame, QCheckBox)
            from PyQt6.QtCore import Qt

            dialog = QDialog(self)
            dialog.setWindowTitle(self.tr("Supervertaler Workbench Setup Wizard"))
            dialog.setMinimumWidth(600)
            dialog.setMinimumHeight(450)
            dialog.setModal(True)

            main_layout = QVBoxLayout(dialog)
            main_layout.setSpacing(15)
            main_layout.setContentsMargins(20, 20, 20, 20)

            # Stacked widget for wizard pages
            stacked = QStackedWidget()

            # Determine if we need to show data folder page
            show_data_folder_page = is_first_run and self._needs_data_location_dialog

            # ==================== PAGE 1: Data Folder Selection ====================
            page1 = QWidget()
            page1_layout = QVBoxLayout(page1)
            page1_layout.setSpacing(15)

            # Step indicator
            step1_indicator = QLabel(self.tr("<span style='color: #888;'>Step 1 of 2</span>"))
            page1_layout.addWidget(step1_indicator)

            # Title
            page1_title = QLabel(self.tr("<h2>📁 Choose Your Data Folder</h2>"))
            page1_layout.addWidget(page1_title)

            # Explanation
            page1_msg = QLabel(
                "Supervertaler stores your data in a folder of your choice:<br><br>"
                "• <b>API keys</b> – Your LLM provider credentials<br>"
                "• <b>Translation memories</b> – Reusable translation pairs<br>"
                "• <b>Termbases</b> – Terminology databases<br>"
                "• <b>Prompts</b> – Custom AI prompts<br>"
                "• <b>Settings</b> – Application configuration<br><br>"
                "Choose a location that's easy to find and backup."
            )
            page1_msg.setWordWrap(True)
            page1_layout.addWidget(page1_msg)

            # Path input with browse button
            path_layout = QHBoxLayout()
            path_edit = QLineEdit()
            default_path = get_default_user_data_path()
            path_edit.setText(str(default_path))
            path_edit.setMinimumWidth(350)
            path_layout.addWidget(path_edit)

            browse_btn = QPushButton(self.tr("Browse..."))
            def browse_folder():
                folder = QFileDialog.getExistingDirectory(
                    dialog,
                    "Choose Data Folder",
                    str(Path.home())
                )
                if folder:
                    folder_path = Path(folder)
                    if folder_path.name != "Supervertaler":
                        folder_path = folder_path / "Supervertaler"
                    path_edit.setText(str(folder_path))

            browse_btn.clicked.connect(browse_folder)
            path_layout.addWidget(browse_btn)
            page1_layout.addLayout(path_layout)

            # Tip
            page1_tip = QLabel(
                "💡 <b>Tip:</b> The default location is in your home folder, "
                "making it easy to find and backup."
            )
            page1_tip.setWordWrap(True)
            page1_tip.setStyleSheet("color: #666;")
            page1_layout.addWidget(page1_tip)

            page1_layout.addStretch()
            stacked.addWidget(page1)

            # ==================== PAGE 2: Features Introduction ====================
            page2 = QWidget()
            page2_layout = QVBoxLayout(page2)
            page2_layout.setSpacing(15)

            # Step indicator
            step2_label = "Step 2 of 2" if show_data_folder_page else "Setup"
            step2_indicator = QLabel(f"<span style='color: #888;'>{step2_label}</span>")
            page2_layout.addWidget(step2_indicator)

            # Data folder info (shown when skipping page 1)
            if not show_data_folder_page:
                from PyQt6.QtGui import QDesktopServices
                from PyQt6.QtCore import QUrl

                data_folder_path = str(self.user_data_path)
                data_folder_info = QLabel(
                    f"<b>📁 Data Folder:</b> <a href='file:///{data_folder_path}' "
                    f"style='color: #3b82f6;'>{data_folder_path}</a><br>"
                    "<span style='color: #666; font-size: 0.9em;'>"
                    "Your settings, TMs, termbases and prompts are stored here. "
                    "Change in Settings → General.</span>"
                )
                data_folder_info.setWordWrap(True)
                data_folder_info.setTextFormat(Qt.TextFormat.RichText)
                data_folder_info.setOpenExternalLinks(False)  # Handle clicks ourselves
                data_folder_info.linkActivated.connect(
                    lambda url: QDesktopServices.openUrl(QUrl.fromLocalFile(data_folder_path))
                )
                data_folder_info.setStyleSheet(
                    "background: #f0f4ff; padding: 12px; border-radius: 6px; "
                    "border-left: 4px solid #3b82f6; margin-bottom: 10px;"
                )
                page2_layout.addWidget(data_folder_info)

            # Title
            page2_title = QLabel(self.tr("<h2>✨ You're all set!</h2>"))
            page2_layout.addWidget(page2_title)

            # Message – kept deliberately short. Earlier versions of this
            # page explained Supervertaler's modular-features architecture
            # and pointed users at Settings → Features. Both are gone in
            # v1.9.474: every feature module that used to be optional is
            # now a core dependency, so there is nothing for the user to
            # install or toggle.
            page2_msg = QLabel(
                "Your data folder is ready and Supervertaler is set up.<br><br>"
                "Have a look around the <b>Editor</b> tab to start translating, "
                "or visit <b>Settings</b> to configure API keys, prompts, theme, "
                "and more."
            )
            page2_msg.setWordWrap(True)
            page2_msg.setTextFormat(Qt.TextFormat.RichText)
            page2_layout.addWidget(page2_msg)

            # Checkbox
            dont_show_checkbox = CheckmarkCheckBox(self.tr("Don't show this wizard on startup"))
            dont_show_checkbox.setChecked(True)
            page2_layout.addWidget(dont_show_checkbox)

            page2_layout.addStretch()
            stacked.addWidget(page2)

            main_layout.addWidget(stacked)

            # ==================== Navigation Buttons ====================
            nav_layout = QHBoxLayout()

            back_btn = QPushButton(self.tr("← Back"))
            back_btn.setVisible(False)  # Hidden on first page

            next_btn = QPushButton(self.tr("Next →"))
            finish_btn = QPushButton(self.tr("Finish"))
            finish_btn.setVisible(False)
            finish_btn.setDefault(True)

            # Use Default button (only on page 1)
            default_btn = QPushButton(self.tr("Use Default"))
            default_btn.clicked.connect(lambda: path_edit.setText(str(default_path)))

            nav_layout.addWidget(default_btn)
            nav_layout.addStretch()
            nav_layout.addWidget(back_btn)
            nav_layout.addWidget(next_btn)
            nav_layout.addWidget(finish_btn)

            main_layout.addLayout(nav_layout)

            # Track chosen path for later
            chosen_path_holder = [None]

            def go_to_page(page_index):
                stacked.setCurrentIndex(page_index)
                if page_index == 0:
                    back_btn.setVisible(False)
                    next_btn.setVisible(True)
                    finish_btn.setVisible(False)
                    default_btn.setVisible(True)
                else:
                    back_btn.setVisible(show_data_folder_page)
                    next_btn.setVisible(False)
                    finish_btn.setVisible(True)
                    default_btn.setVisible(False)

            def on_next():
                # Save the data folder choice
                chosen_path = Path(path_edit.text())
                chosen_path_holder[0] = chosen_path

                # Create the folder and save config
                chosen_path.mkdir(parents=True, exist_ok=True)
                save_user_data_path(chosen_path)

                # Update our path if different
                if chosen_path != self.user_data_path:
                    self.user_data_path = chosen_path
                    self._reinitialize_with_new_data_path()
                else:
                    if hasattr(self, 'db_manager') and self.db_manager and not self.db_manager.connection:
                        self.db_manager.connect()

                self.log(f"📁 Data folder set to: {chosen_path}")
                go_to_page(1)

            def on_back():
                go_to_page(0)

            def on_finish():
                # Save first_run preference
                if dont_show_checkbox.isChecked():
                    settings = self.load_general_settings()
                    settings['first_run_completed'] = True
                    self.save_general_settings(settings)
                    self.log("✅ Setup wizard completed (won't show again on startup)")
                else:
                    self.log("✅ Setup wizard shown (will show again next time)")

                dialog.accept()

            back_btn.clicked.connect(on_back)
            next_btn.clicked.connect(on_next)
            finish_btn.clicked.connect(on_finish)

            # Start on appropriate page
            if show_data_folder_page:
                go_to_page(0)
            else:
                # Skip to features page if data folder already configured
                go_to_page(1)
                step2_indicator.setText(self.tr("<span style='color: #888;'>Supervertaler Setup</span>"))

            dialog.exec()

        except Exception as e:
            self.log(f"⚠️ Setup wizard error: {e}")
            import traceback
            traceback.print_exc()
