#!/usr/bin/env python3
"""
=============================================================================
MODULE: Superbrowser - Multi-Chat AI Browser
=============================================================================
Display multiple AI chat pages side by side in a single interface.
Supports ChatGPT, Claude, and Gemini in a three-column resizable layout.

Author: Michael Beijer
Date: November 18, 2025
Version: 1.0.0
=============================================================================
"""

import os
import shutil
from PyQt6.QtCore import QUrl, Qt
from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QLabel, 
    QSplitter, QPushButton, QLineEdit, QComboBox,
    QGroupBox, QFormLayout
)
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWebEngineCore import QWebEngineProfile
from PyQt6.QtGui import QPalette, QColor


def _clear_corrupted_cache(storage_path: str):
    """
    Clear potentially corrupted Chromium cache folders.
    These can cause 'wrong file structure on disk' errors on startup.
    """
    problematic_dirs = [
        os.path.join(storage_path, "Shared Dictionary"),
        os.path.join(storage_path, "cache", "Shared Dictionary"),
    ]
    for dir_path in problematic_dirs:
        if os.path.exists(dir_path):
            try:
                shutil.rmtree(dir_path)
            except Exception:
                pass  # Silently ignore - may be in use


class ChatColumn(QWidget):
    """A column containing a chat interface with web browser"""

    def __init__(self, title, url, header_color, parent=None, user_data_path=None):
        super().__init__(parent)
        self.title = title
        self.url = url
        self.header_color = header_color
        self.user_data_path = user_data_path  # Store user data path
        self.init_ui()

    def init_ui(self):
        """Initialize the chat column UI"""
        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Tiny header label with provider name
        header = QLabel(self.title)
        header.setAlignment(Qt.AlignmentFlag.AlignCenter)
        header.setStyleSheet(f"""
            QLabel {{
                background-color: {self.header_color};
                color: white;
                padding: 0px;
                margin: 0px;
                font-weight: bold;
                font-size: 9px;
                max-height: 20px;
                min-height: 20px;
            }}
        """)

        # URL bar for navigation
        url_layout = QHBoxLayout()
        url_layout.setContentsMargins(1, 1, 1, 1)
        
        self.url_input = QLineEdit()
        self.url_input.setText(self.url)
        self.url_input.setPlaceholderText("Enter URL...")
        self.url_input.returnPressed.connect(self.load_url)
        
        reload_btn = QPushButton("↻")
        reload_btn.setMaximumWidth(40)
        reload_btn.setToolTip("Reload page")
        reload_btn.clicked.connect(self.reload_page)
        
        home_btn = QPushButton("⌂")
        home_btn.setMaximumWidth(40)
        home_btn.setToolTip("Go to home URL")
        home_btn.clicked.connect(self.go_home)
        
        url_layout.addWidget(self.url_input)
        url_layout.addWidget(reload_btn)
        url_layout.addWidget(home_btn)

        # Web view with persistent profile
        # Create or use persistent profile to save cookies and session data
        profile_name = f"superbrowser_{self.title.lower()}"
        self.profile = QWebEngineProfile(profile_name, self)
        
        # Set persistent storage path using user_data_path from parent
        if self.user_data_path:
            storage_path = os.path.join(str(self.user_data_path), "workbench", "superbrowser_profiles", profile_name)
        else:
            # Fallback to script directory if user_data_path not provided
            dev_mode_marker = os.path.join(os.path.dirname(__file__), "..", ".supervertaler.local")
            base_folder = "user_data_private" if os.path.exists(dev_mode_marker) else "user_data"
            storage_path = os.path.join(os.path.dirname(__file__), "..", base_folder, "superbrowser_profiles", profile_name)
        os.makedirs(storage_path, exist_ok=True)
        
        # Clear potentially corrupted cache to prevent Chromium errors
        _clear_corrupted_cache(storage_path)
        
        self.profile.setPersistentStoragePath(storage_path)
        self.profile.setCachePath(os.path.join(storage_path, "cache"))
        
        # Enable persistent cookies
        self.profile.setPersistentCookiesPolicy(QWebEngineProfile.PersistentCookiesPolicy.AllowPersistentCookies)
        
        # Create web view with this profile
        from PyQt6.QtWebEngineWidgets import QWebEngineView
        from PyQt6.QtWebEngineCore import QWebEnginePage
        
        page = QWebEnginePage(self.profile, self)
        self.web_view = QWebEngineView()
        self.web_view.setPage(page)
        self.web_view.setUrl(QUrl(self.url))
        
        # Update URL bar when page changes
        self.web_view.urlChanged.connect(self.update_url_bar)

        # Add to layout (tiny header, URL bar, then browser)
        layout.addWidget(header)
        layout.addLayout(url_layout)
        layout.addWidget(self.web_view)

        self.setLayout(layout)

    def load_url(self):
        """Load URL from input field"""
        url_text = self.url_input.text().strip()
        if not url_text.startswith(('http://', 'https://')):
            url_text = 'https://' + url_text
        self.web_view.setUrl(QUrl(url_text))

    def reload_page(self):
        """Reload the current page"""
        self.web_view.reload()

    def go_home(self):
        """Go back to the home URL"""
        self.web_view.setUrl(QUrl(self.url))

    def update_url_bar(self, url):
        """Update URL bar when page changes"""
        self.url_input.setText(url.toString())

    def cleanup(self):
        """Clean up web engine resources before deletion"""
        try:
            from PyQt6.QtCore import QUrl
            if hasattr(self, 'web_view'):
                self.web_view.stop()
                self.web_view.setPage(None)
                self.web_view.setUrl(QUrl('about:blank'))
                self.web_view.deleteLater()
            if hasattr(self, 'profile'):
                self.profile.deleteLater()
        except:
            pass


class SuperbrowserWidget(QWidget):
    """
    Superbrowser - Multi-Chat AI Browser Widget
    
    Displays multiple AI chat interfaces side by side for easy comparison
    and concurrent interaction with different AI models.
    """

    def __init__(self, parent=None, user_data_path=None):
        super().__init__(parent)
        self.parent_window = parent
        self.user_data_path = user_data_path  # Store user data path for profiles
        
        # Default URLs for AI chat interfaces
        self.chatgpt_url = "https://chatgpt.com/"
        self.claude_url = "https://claude.ai/"
        self.gemini_url = "https://gemini.google.com/"
        
        self.init_ui()

    def init_ui(self):
        """Initialize the Superbrowser UI"""
        main_layout = QVBoxLayout()
        main_layout.setContentsMargins(5, 5, 5, 5)
        main_layout.setSpacing(5)

        # Compact title bar with toggle button
        title_bar_layout = QHBoxLayout()
        title_bar_layout.setContentsMargins(0, 0, 0, 0)
        
        title_label = QLabel("🌐 Superbrowser - Multi-Chat AI Browser")
        title_label.setStyleSheet("""
            QLabel {
                font-size: 12px;
                font-weight: bold;
                color: #2c3e50;
                padding: 3px;
            }
        """)
        
        description = QLabel(
            "View and interact with ChatGPT, Claude, and Gemini side by side. "
            "Perfect for comparing responses or maintaining multiple conversation threads."
        )
        description.setWordWrap(True)
        description.setStyleSheet("color: #7f8c8d; padding: 3px; font-size: 10px;")
        
        # Toggle button for configuration section
        self.toggle_config_btn = QPushButton("▼ Hide Configuration")
        self.toggle_config_btn.setMaximumWidth(150)
        self.toggle_config_btn.setStyleSheet("""
            QPushButton {
                background-color: #3498db;
                color: white;
                border: none;
                padding: 4px 8px;
                font-size: 10px;
                border-radius: 3px;
            }
            QPushButton:hover {
                background-color: #2980b9;
            }
        """)
        self.toggle_config_btn.clicked.connect(self.toggle_configuration)
        
        title_bar_layout.addWidget(title_label)
        title_bar_layout.addWidget(description, stretch=1)
        title_bar_layout.addWidget(self.toggle_config_btn)
        
        main_layout.addLayout(title_bar_layout)

        # URL Configuration section (collapsible)
        self.config_group = QGroupBox("🔧 Configuration")
        self.config_group.setStyleSheet("QGroupBox { font-size: 10px; font-weight: bold; }")
        config_layout = QFormLayout()
        
        self.chatgpt_url_input = QLineEdit(self.chatgpt_url)
        self.chatgpt_url_input.setStyleSheet("font-size: 10px;")
        self.claude_url_input = QLineEdit(self.claude_url)
        self.claude_url_input.setStyleSheet("font-size: 10px;")
        self.gemini_url_input = QLineEdit(self.gemini_url)
        self.gemini_url_input.setStyleSheet("font-size: 10px;")
        
        config_layout.addRow("ChatGPT URL:", self.chatgpt_url_input)
        config_layout.addRow("Claude URL:", self.claude_url_input)
        config_layout.addRow("Gemini URL:", self.gemini_url_input)
        
        update_btn = QPushButton("Update URLs")
        update_btn.setStyleSheet("font-size: 10px; padding: 3px;")
        update_btn.clicked.connect(self.update_urls)
        config_layout.addRow("", update_btn)
        
        self.config_group.setLayout(config_layout)
        self.config_group.setMaximumHeight(150)
        main_layout.addWidget(self.config_group)

        # Splitter for resizable columns
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(3)

        # Create chat columns - pass user_data_path for profile storage
        self.chatgpt_column = ChatColumn("ChatGPT", self.chatgpt_url, "#10a37f", self, user_data_path=self.user_data_path)
        self.claude_column = ChatColumn("Claude", self.claude_url, "#c17c4f", self, user_data_path=self.user_data_path)
        self.gemini_column = ChatColumn("Gemini", self.gemini_url, "#4285f4", self, user_data_path=self.user_data_path)

        # Add columns to splitter
        splitter.addWidget(self.chatgpt_column)
        splitter.addWidget(self.claude_column)
        splitter.addWidget(self.gemini_column)

        # Set equal sizes for all columns
        splitter.setSizes([600, 600, 600])

        # Add splitter to main layout (takes most of the space)
        main_layout.addWidget(splitter, stretch=1)

        self.setLayout(main_layout)

    def toggle_configuration(self):
        """Toggle visibility of configuration section"""
        if self.config_group.isVisible():
            self.config_group.setVisible(False)
            self.toggle_config_btn.setText("▶ Show Configuration")
        else:
            self.config_group.setVisible(True)
            self.toggle_config_btn.setText("▼ Hide Configuration")

    def update_urls(self):
        """Update the URLs for all chat columns"""
        self.chatgpt_url = self.chatgpt_url_input.text().strip() or self.chatgpt_url
        self.claude_url = self.claude_url_input.text().strip() or self.claude_url
        self.gemini_url = self.gemini_url_input.text().strip() or self.gemini_url
        
        # Update the columns
        self.chatgpt_column.url = self.chatgpt_url
        self.claude_column.url = self.claude_url
        self.gemini_column.url = self.gemini_url
        
        # Reload to new URLs
        self.chatgpt_column.go_home()
        self.claude_column.go_home()
        self.gemini_column.go_home()

    def cleanup(self):
        """Clean up all web engine resources before widget deletion"""
        try:
            for column in self.chat_columns:
                column.cleanup()
        except:
            pass


# ============================================================================
# STANDALONE USAGE
# ============================================================================

if __name__ == "__main__":
    import sys
    from PyQt6.QtWidgets import QApplication, QMainWindow

    app = QApplication(sys.argv)
    app.setApplicationName("Superbrowser")

    # Create main window for testing
    window = QMainWindow()
    window.setWindowTitle("Superbrowser - Multi-Chat AI Browser")
    window.setGeometry(100, 100, 1800, 1000)
    
    # Create and set central widget
    browser = SuperbrowserWidget()
    window.setCentralWidget(browser)
    
    window.show()
    sys.exit(app.exec())
