"""
Theme Manager
=============
Manages UI themes and color schemes for Supervertaler Qt.
Allows users to customize the appearance of the entire application.

Features:
- Predefined themes (Light, Dark, Sepia, High Contrast)
- Custom theme creation and editing
- Save/load user themes
- Apply themes to all UI elements
"""

from PyQt6.QtGui import QColor, QPalette
from PyQt6.QtWidgets import QApplication
from dataclasses import dataclass, asdict
import json
from pathlib import Path
from typing import Dict, Optional


@dataclass
class Theme:
    """Theme definition with all UI colors"""
    name: str
    
    # Main window colors
    window_bg: str = "#F5F5F5"  # Main background
    alternate_bg: str = "#EBEBEB"  # Alternate row color
    
    # Text colors
    text: str = "#212121"  # Primary text
    text_disabled: str = "#9E9E9E"  # Disabled text
    text_placeholder: str = "#BDBDBD"  # Placeholder text
    
    # Control colors
    base: str = "#FFFFFF"  # Input fields, text areas
    button: str = "#E0E0E0"  # Button background
    button_hover: str = "#D5D5D5"  # Button hover
    
    # Highlight colors
    highlight: str = "#2196F3"  # Selection highlight
    highlight_text: str = "#FFFFFF"  # Selected text
    
    # Border and separator colors
    border: str = "#CCCCCC"  # Borders
    separator: str = "#E0E0E0"  # Separators
    
    # Status colors
    success: str = "#4CAF50"  # Green for success
    warning: str = "#FF9800"  # Orange for warnings
    error: str = "#F44336"  # Red for errors
    info: str = "#2196F3"  # Blue for info
    
    # Grid colors
    grid_header: str = "#E8E8E8"  # Table headers
    grid_line: str = "#E0E0E0"  # Grid lines
    
    # Tab colors
    tab_bg: str = "#F5F5F5"  # Tab background
    tab_selected: str = "#FFFFFF"  # Selected tab
    
    # TM match colors (for colored match percentages)
    tm_exact: str = "#C8E6C9"  # 100% match (light green)
    tm_high: str = "#FFF9C4"  # 95-99% (light yellow)
    tm_medium: str = "#FFE0B2"  # 85-94% (light orange)
    tm_low: str = "#F5F5F5"  # <85% (default)

    # Action button colors (for buttons that need specific semantic colors)
    button_success: str = "#4CAF50"  # Green for success actions (save, apply, etc.)
    button_info: str = "#2196F3"  # Blue for info actions
    button_warning: str = "#FF9800"  # Orange for warning actions
    button_danger: str = "#F44336"  # Red for danger actions (delete, etc.)
    button_neutral: str = "#607D8B"  # Blue-gray for neutral actions
    button_purple: str = "#9C27B0"  # Purple for special actions

    # Panel/info box backgrounds
    panel_info: str = "#F0F7FF"  # Light blue info panels
    panel_warning: str = "#FFF3CD"  # Light yellow warning panels
    panel_neutral: str = "#F3F4F6"  # Gray neutral panels
    panel_preview: str = "#F9F9F9"  # Preview areas
    panel_accent: str = "#FFF3E0"  # Accent panels

    # TM results display colors
    tm_source_label: str = "#1976D2"  # Blue for source language label
    tm_target_label: str = "#388E3C"  # Green for target language label
    tm_highlight_bg: str = "#FFFF00"  # Yellow background for search term highlight
    tm_highlight_text: str = "#000000"  # Black text for highlighted terms

    def to_dict(self) -> Dict:
        """Convert theme to dictionary"""
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: Dict) -> 'Theme':
        """Create theme from dictionary"""
        return cls(**data)


class ThemeManager:
    """Manages application themes"""
    
    # Predefined themes
    PREDEFINED_THEMES = {
        "Light (Default)": Theme(
            name="Light (Default)",
            window_bg="#F5F5F5",
            alternate_bg="#EBEBEB",
            text="#212121",
            base="#FFFFFF",
            button="#E0E0E0",
            highlight="#e3f2fd",
            highlight_text="#000000",
        ),
        
        "Soft Gray": Theme(
            name="Soft Gray",
            window_bg="#E8E8E8",
            alternate_bg="#DEDEDE",
            text="#1A1A1A",
            base="#F8F8F8",
            button="#D5D5D5",
            highlight="#1976D2",
        ),
        
        "Warm Cream": Theme(
            name="Warm Cream",
            window_bg="#F5F0E8",
            alternate_bg="#EBE6DE",
            text="#3E2723",
            base="#FFFEF8",
            button="#E8DED0",
            highlight="#6D4C41",
            grid_header="#EBE6DE",
        ),
        
        "Dark": Theme(
            name="Dark",
            window_bg="#2B2B2B",
            alternate_bg="#353535",
            text="#E0E0E0",
            text_disabled="#757575",
            text_placeholder="#616161",
            base="#1E1E1E",
            button="#404040",
            button_hover="#4A4A4A",
            highlight="#0D47A1",
            highlight_text="#FFFFFF",
            border="#505050",
            separator="#404040",
            grid_header="#353535",
            grid_line="#404040",
            tab_bg="#2B2B2B",
            tab_selected="#1E1E1E",
            # TM match colors (darker versions for dark mode)
            tm_exact="#2E5C35",  # Dark green
            tm_high="#5C5424",  # Dark yellow
            tm_medium="#5C4224",  # Dark orange
            tm_low="#2B2B2B",  # Match window background
            # Action button colors (keep vibrant for visibility in dark mode)
            button_success="#388E3C",  # Darker green
            button_info="#1976D2",  # Darker blue
            button_warning="#F57C00",  # Darker orange
            button_danger="#D32F2F",  # Darker red
            button_neutral="#455A64",  # Darker blue-gray
            button_purple="#7B1FA2",  # Darker purple
            # Panel/info box backgrounds (dark versions)
            panel_info="#1A2F3A",  # Dark blue
            panel_warning="#3A3020",  # Dark yellow
            panel_neutral="#323232",  # Dark gray
            panel_preview="#252525",  # Dark preview
            panel_accent="#3A2F1A",  # Dark accent
            # TM results display colors (brighter for visibility in dark mode)
            tm_source_label="#64B5F6",  # Brighter blue for source language label
            tm_target_label="#81C784",  # Brighter green for target language label
            tm_highlight_bg="#FFD54F",  # Softer yellow for dark mode
            tm_highlight_text="#000000",  # Black text for highlighted terms
        ),
        
        "Sepia": Theme(
            name="Sepia",
            window_bg="#F4ECD8",
            alternate_bg="#E8E0CE",
            text="#3E2723",
            base="#FFFEF5",
            button="#E0D8C8",
            highlight="#8D6E63",
            grid_header="#E8E0CE",
        ),
        
        "High Contrast": Theme(
            name="High Contrast",
            window_bg="#FFFFFF",
            alternate_bg="#F0F0F0",
            text="#000000",
            base="#FFFFFF",
            button="#E0E0E0",
            highlight="#0000FF",
            highlight_text="#FFFFFF",
            border="#000000",
        ),
    }
    
    def __init__(self, user_data_path: Path):
        """
        Initialize theme manager
        
        Args:
            user_data_path: Path to user_data folder for saving custom themes
        """
        self.user_data_path = user_data_path
        self.themes_file = user_data_path / "workbench" / "settings" / "themes.json"
        self.current_theme: Theme = self.PREDEFINED_THEMES["Light (Default)"]
        self.custom_themes: Dict[str, Theme] = {}

        # Global UI font scale (50-200%, default 100%)
        self.font_scale: int = 100

        # Load custom themes
        self.load_custom_themes()
    
    def get_all_themes(self) -> Dict[str, Theme]:
        """Get all available themes (predefined + custom)"""
        all_themes = self.PREDEFINED_THEMES.copy()
        all_themes.update(self.custom_themes)
        return all_themes
    
    def get_theme(self, name: str) -> Optional[Theme]:
        """Get theme by name"""
        all_themes = self.get_all_themes()
        return all_themes.get(name)
    
    def set_theme(self, name: str) -> bool:
        """
        Set current theme
        
        Args:
            name: Theme name
            
        Returns:
            True if theme was found and applied
        """
        theme = self.get_theme(name)
        if theme:
            self.current_theme = theme
            return True
        return False
    
    def save_custom_theme(self, theme: Theme):
        """Save a custom theme"""
        self.custom_themes[theme.name] = theme
        self._save_themes()
    
    def delete_custom_theme(self, name: str) -> bool:
        """Delete a custom theme"""
        if name in self.custom_themes:
            del self.custom_themes[name]
            self._save_themes()
            return True
        return False
    
    def load_custom_themes(self):
        """Load custom themes from file"""
        if self.themes_file.exists():
            try:
                with open(self.themes_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    for theme_data in data.get('themes', []):
                        theme = Theme.from_dict(theme_data)
                        self.custom_themes[theme.name] = theme
                    
                    # Load last used theme
                    current_theme_name = data.get('current_theme')
                    if current_theme_name:
                        self.set_theme(current_theme_name)
            except Exception as e:
                print(f"Error loading themes: {e}")
    
    def _save_themes(self):
        """Save custom themes to file"""
        try:
            data = {
                'current_theme': self.current_theme.name,
                'themes': [theme.to_dict() for theme in self.custom_themes.values()]
            }
            
            with open(self.themes_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            print(f"Error saving themes: {e}")
    
    def apply_theme(self, app: QApplication):
        """
        Apply current theme to application

        Args:
            app: QApplication instance
        """
        theme = self.current_theme

        # Calculate scaled font sizes based on font_scale (default 100%)
        base_font_size = int(10 * self.font_scale / 100)  # Base: 10pt at 100%
        small_font_size = max(7, int(9 * self.font_scale / 100))  # Small text (status bar)

        # Font scaling rules (only applied if scale != 100%)
        font_rules = ""
        if self.font_scale != 100:
            font_rules = f"""
            /* Global font scaling ({self.font_scale}%) */
            QWidget {{ font-size: {base_font_size}pt; }}
            QMenuBar {{ font-size: {base_font_size}pt; }}
            QMenuBar::item {{ font-size: {base_font_size}pt; }}
            QMenu {{ font-size: {base_font_size}pt; }}
            QMenu::item {{ font-size: {base_font_size}pt; }}
            QStatusBar {{ font-size: {small_font_size}pt; }}
            QTabBar::tab {{ font-size: {base_font_size}pt; }}
            QToolBar {{ font-size: {base_font_size}pt; }}
            QLabel {{ font-size: {base_font_size}pt; }}
            QCheckBox {{ font-size: {base_font_size}pt; }}
            QRadioButton {{ font-size: {base_font_size}pt; }}
            QComboBox {{ font-size: {base_font_size}pt; }}
            QSpinBox {{ font-size: {base_font_size}pt; }}
            QDoubleSpinBox {{ font-size: {base_font_size}pt; }}
            QLineEdit {{ font-size: {base_font_size}pt; }}
            QPushButton {{ font-size: {base_font_size}pt; }}
            QGroupBox {{ font-size: {base_font_size}pt; }}
            QGroupBox::title {{ font-size: {base_font_size}pt; }}
            QTextEdit {{ font-size: {base_font_size}pt; }}
            QPlainTextEdit {{ font-size: {base_font_size}pt; }}
            QListWidget {{ font-size: {base_font_size}pt; }}
            QTreeWidget {{ font-size: {base_font_size}pt; }}
            QHeaderView::section {{ font-size: {base_font_size}pt; }}
            """

        # Create and apply stylesheet - COLORS ONLY, preserves native sizes/spacing
        stylesheet = font_rules + f"""
            /* Main window background */
            QMainWindow, QWidget {{
                background-color: {theme.window_bg};
                color: {theme.text};
            }}
            
            /* Input fields and text areas */
            QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox {{
                background-color: {theme.base};
                color: {theme.text};
                border: 1px solid {theme.border};
            }}
            
            QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus {{
                border: 1px solid {theme.highlight};
            }}
            
            /* Buttons */
            QPushButton {{
                background-color: {theme.button};
                color: {theme.text};
                border: 1px solid {theme.border};
            }}
            
            QPushButton:hover {{
                background-color: {theme.button_hover};
            }}
            
            QPushButton:pressed {{
                background-color: {theme.highlight};
                color: {theme.highlight_text};
            }}
            
            QPushButton:disabled {{
                color: {theme.text_disabled};
            }}

            /* Remove focus rectangles from buttons */
            QPushButton:focus {{
                outline: none;
            }}

            QToolButton:focus {{
                outline: none;
            }}

            /* Combo boxes */
            QComboBox {{
                background-color: {theme.base};
                color: {theme.text};
                border: 1px solid {theme.border};
            }}
            
            QComboBox:hover {{
                border: 1px solid {theme.highlight};
            }}

            QComboBox QAbstractItemView {{
                background-color: {theme.base};
                color: {theme.text};
                border: 1px solid {theme.border};
                selection-background-color: {theme.highlight};
                selection-color: {theme.base};
            }}

            /* Tables */
            QTableWidget {{
                background-color: {theme.base};
                alternate-background-color: {theme.alternate_bg};
                color: {theme.text};
                gridline-color: {theme.grid_line};
                border: 1px solid {theme.border};
            }}
            
            QTableWidget::item:selected {{
                background-color: {theme.highlight};
                color: {theme.highlight_text};
            }}
            
            QHeaderView::section {{
                background-color: {theme.grid_header};
                color: {theme.text};
                border: 1px solid {theme.border};
            }}
            
            /* Tabs */
            QTabWidget::pane {{
                border: 1px solid {theme.border};
                background-color: {theme.base};
            }}
            
            QTabBar::tab {{
                background-color: {theme.tab_bg};
                color: {theme.text};
                border: 1px solid {theme.border};
                padding: 6px 12px;
            }}
            
            QTabBar::tab:selected {{
                background-color: {theme.tab_selected};
                border-bottom: 1px solid {theme.highlight};
            }}
            
            QTabBar::tab:hover {{
                background-color: {theme.button_hover};
            }}
            
            /* Splitter */
            QSplitter::handle {{
                background-color: {theme.separator};
            }}
            
            /* Scrollbars */
            QScrollBar:vertical {{
                background-color: {theme.window_bg};
            }}
            
            QScrollBar::handle:vertical {{
                background-color: {theme.button};
            }}
            
            QScrollBar::handle:vertical:hover {{
                background-color: {theme.button_hover};
            }}
            
            QScrollBar:horizontal {{
                background-color: {theme.window_bg};
            }}
            
            QScrollBar::handle:horizontal {{
                background-color: {theme.button};
            }}
            
            QScrollBar::handle:horizontal:hover {{
                background-color: {theme.button_hover};
            }}
            
            /* Checkboxes and Radio buttons */
            QCheckBox, QRadioButton {{
                color: {theme.text};
            }}
            
            QCheckBox::indicator, QRadioButton::indicator {{
                border: 1px solid {theme.border};
                background-color: {theme.base};
            }}
            
            QCheckBox::indicator:checked {{
                background-color: {theme.highlight};
            }}
            
            /* Menu bar */
            QMenuBar {{
                background-color: {theme.window_bg};
                color: {theme.text};
            }}
            
            QMenuBar::item:selected {{
                background-color: {theme.highlight};
                color: {theme.highlight_text};
            }}
            
            QMenu {{
                background-color: {theme.base};
                color: {theme.text};
                border: 1px solid {theme.border};
            }}
            
            QMenu::item:selected {{
                background-color: {theme.highlight};
                color: {theme.highlight_text};
            }}
            
            /* Status bar */
            QStatusBar {{
                background-color: {theme.window_bg};
                color: {theme.text};
            }}
            
            /* Group boxes - minimal styling to avoid title rendering issues */
            QGroupBox {{
                color: {theme.text};
                border: 1px solid {theme.border};
                padding: 18px 10px 10px 10px;
                margin-top: 12px;
            }}
            
            QGroupBox::title {{
                color: {theme.text};
                subcontrol-origin: margin;
                subcontrol-position: top left;
                padding: 2px 5px;
                background-color: {theme.window_bg};
            }}
            
            /* Dialogs */
            QDialog {{
                background-color: {theme.window_bg};
                color: {theme.text};
            }}

            /* Tooltips - ensure readable in all themes */
            QToolTip {{
                background-color: #f5f5f5;
                color: #333333;
                border: 1px solid #d0d0d0;
                padding: 4px;
            }}
            
            /* Labels */
            QLabel {{
                color: {theme.text};
                padding: 3px 2px;
            }}
            
            /* Form layouts need extra spacing */
            QFormLayout {{
                vertical-spacing: 8px;
            }}
        """
        
        # Dark themes (issue #78): trees and lists (the prompt library, the
        # clipboard columns…) had no rules, so their selected row was drawn
        # white; and natively painted areas followed the light default palette.
        dark = QColor(theme.window_bg).lightnessF() < 0.3
        if dark:
            stylesheet += f"""
            QTreeView, QListView {{
                background-color: {theme.base};
                alternate-background-color: {theme.alternate_bg};
                color: {theme.text};
                selection-background-color: {theme.highlight};
                selection-color: {theme.highlight_text};
            }}
            """

        app.setStyleSheet(stylesheet)

        # Palette: derived from the theme for dark themes, the style's standard
        # one otherwise (so light themes look exactly as before).
        palette = QPalette(app.style().standardPalette()) if not dark else QPalette(app.palette())
        if dark:
            for role, value in (
                (QPalette.ColorRole.Window, theme.window_bg),
                (QPalette.ColorRole.WindowText, theme.text),
                (QPalette.ColorRole.Base, theme.base),
                (QPalette.ColorRole.AlternateBase, theme.alternate_bg),
                (QPalette.ColorRole.Text, theme.text),
                (QPalette.ColorRole.Button, theme.button),
                (QPalette.ColorRole.ButtonText, theme.text),
                (QPalette.ColorRole.Highlight, theme.highlight),
                (QPalette.ColorRole.HighlightedText, theme.highlight_text),
                (QPalette.ColorRole.PlaceholderText, theme.text_placeholder),
            ):
                palette.setColor(role, QColor(value))
        # Set tooltip colors via palette (some Qt versions ignore stylesheet for tooltips)
        palette.setColor(QPalette.ColorRole.ToolTipBase, QColor("#f5f5f5"))
        palette.setColor(QPalette.ColorRole.ToolTipText, QColor("#333333"))
        app.setPalette(palette)

        # Save current theme
        self._save_themes()
