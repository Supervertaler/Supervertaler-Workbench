"""
Translation Results Panel
Compact memoQ-style right-side panel for displaying translation matches
Supports stacked match sections, drag/drop, and compare boxes with diff highlighting

Keyboard Shortcuts:
- ↑/↓ arrows: Navigate through matches (cycle through sections)
- Spacebar/Enter: Insert currently selected match into target cell
- Ctrl+1-9: Insert specific match directly (by number, global across all sections)
- Escape: Deselect match (when focus on panel)

Compare boxes: Vertical stacked with resizable splitter
Text display: Supports long segments with text wrapping
"""

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, 
    QFrame, QScrollArea, QTextEdit, QSplitter, QTabWidget
)
from PyQt6.QtCore import Qt, pyqtSignal, QMimeData
from PyQt6.QtGui import QDrag, QCursor, QFont, QColor, QTextCharFormat, QTextCursor
from dataclasses import dataclass
from typing import List, Optional, Dict, Any
import difflib


@dataclass
class TranslationMatch:
    """Represents a single translation match"""
    source: str
    target: str
    relevance: int  # 0-100
    metadata: Dict[str, Any]  # Context, domain, timestamp, etc.
    match_type: str  # "NT", "MT", "TM", "Termbase", "LLM"
    compare_source: Optional[str] = None  # For TM compare boxes
    provider_code: Optional[str] = None  # Provider code: "GT", "AT", "MMT", "CL", "GPT", "GEM", etc.


class CompactMatchItem(QFrame):
    """Compact match display (like memoQ) with source and target in separate columns"""
    
    match_selected = pyqtSignal(TranslationMatch)
    
    # Class variables (can be changed globally)
    font_size_pt = 9
    show_tags = False  # When False, HTML/XML tags are hidden
    tag_highlight_color = '#7f0001'  # Default memoQ dark red for tag highlighting
    badge_text_color = '#333333'  # Dark gray for badge text (readable without being harsh)
    theme_manager = None  # Class-level theme manager reference
    
    def __init__(self, match: TranslationMatch, match_number: int = 0, parent=None):
        super().__init__(parent)
        self.match = match
        self.match_number = match_number
        self.is_selected = False
        self.num_label_ref = None  # Initialize FIRST before update_styling()
        self.source_label = None
        self.target_label = None
        
        self.setFrameStyle(QFrame.Shape.NoFrame)  # No frame border
        self.setMinimumHeight(20)  # Minimum height (can expand)
        self.setMaximumHeight(100)  # Allow up to 100px if text wraps
        
        # Vertical layout with 2 rows: number+relevance on left, then source and target on right
        main_layout = QHBoxLayout(self)
        main_layout.setContentsMargins(2, 1, 2, 1)  # Minimal padding
        main_layout.setSpacing(3)
        
        # Left side: Match number box (small colored box)
        if match_number > 0:
            num_label = QLabel(f"{match_number}")
            num_label.setStyleSheet("""
                QLabel {
                    font-weight: bold;
                    font-size: 9px;
                    padding: 1px;
                    border-radius: 2px;
                    margin: 0px;
                }
            """)
            num_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            num_label.setFixedWidth(22)
            num_label.setFixedHeight(18)
            
            # Add tooltip based on match type
            match_type_tooltips = {
                "LLM": "LLM Translation (AI-generated)",
                "TM": "Translation Memory (Previously approved)",
                "Termbase": "Termbase",
                "MT": "Machine Translation",
                "NT": "New Translation",
                "NonTrans": "🚫 Non-Translatable (do not translate)"
            }
            tooltip_text = match_type_tooltips.get(match.match_type, "Translation Match")
            num_label.setToolTip(tooltip_text)
            
            self.num_label_ref = num_label  # Set BEFORE calling update_styling()
            main_layout.addWidget(num_label, 0, Qt.AlignmentFlag.AlignTop)
        
        # Get theme color for secondary text
        secondary_text_color = "#666"
        if CompactMatchItem.theme_manager:
            secondary_text_color = CompactMatchItem.theme_manager.current_theme.text_disabled
        
        # Middle: Relevance % (vertical)
        rel_label = QLabel(f"{match.relevance}%")
        rel_label.setStyleSheet(f"font-size: 7px; color: {secondary_text_color}; padding: 0px; margin: 0px;")
        rel_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        rel_label.setFixedWidth(32)
        rel_label.setFixedHeight(18)
        main_layout.addWidget(rel_label, 0, Qt.AlignmentFlag.AlignTop)
        
        # Right side: Source and Target in a horizontal layout (like spreadsheet columns)
        content_layout = QHBoxLayout()
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(6)
        
        # Get theme colors for text
        if CompactMatchItem.theme_manager:
            theme = CompactMatchItem.theme_manager.current_theme
            source_color = theme.text
            # Use slightly dimmer text for target, but not as dim as text_disabled
            # For dark themes, use a color between text and text_disabled for better readability
            is_dark = theme.name == "Dark"
            target_color = "#B0B0B0" if is_dark else "#555"
        else:
            source_color = "#333"
            target_color = "#555"
        
        # Source column - NO truncation, allow wrapping
        self.source_label = QLabel(self._format_text(match.source))
        self.source_label.setWordWrap(True)  # Allow wrapping
        # Always use RichText when tags are shown (for highlighting), otherwise RichText for rendering
        self.source_label.setTextFormat(Qt.TextFormat.RichText)
        self.source_label.setStyleSheet(f"font-size: {self.font_size_pt}px; color: {source_color}; padding: 0px; margin: 0px;")
        self.source_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self.source_label.setMinimumWidth(150)  # Much wider minimum
        content_layout.addWidget(self.source_label, 1)
        
        # Target column - NO truncation, allow wrapping
        self.target_label = QLabel(self._format_text(match.target))
        self.target_label.setWordWrap(True)  # Allow wrapping
        # Always use RichText when tags are shown (for highlighting), otherwise RichText for rendering
        self.target_label.setTextFormat(Qt.TextFormat.RichText)
        self.target_label.setStyleSheet(f"font-size: {self.font_size_pt}px; color: {target_color}; padding: 0px; margin: 0px;")
        self.target_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self.target_label.setMinimumWidth(150)  # Much wider minimum
        content_layout.addWidget(self.target_label, 1)
        
        # Provider code column (tiny, after target text) - always reserve space for alignment
        # Determine provider code text and styling
        provider_code_text = match.provider_code if match.provider_code else ""

        # Determine if this is a project termbase or project TM
        # For termbases: explicit flag OR ranking #1 = project termbase
        is_project_tb_flag = match.match_type == 'Termbase' and match.metadata.get('is_project_termbase', False)
        is_ranking_1 = match.match_type == 'Termbase' and match.metadata.get('ranking') == 1
        is_project_tb = is_project_tb_flag or is_ranking_1
        is_project_tm = match.match_type == 'TM' and match.metadata.get('is_project_tm', False)

        provider_label = QLabel(provider_code_text)

        # Use bold font for project termbases/TMs, normal font for background resources
        font_weight = "bold" if (is_project_tb or is_project_tm) else "normal"
        # Use theme color for text
        provider_text_color = secondary_text_color  # Reuse the secondary text color from above
        provider_label.setStyleSheet(f"font-size: 7px; color: {provider_text_color}; padding: 0px; margin: 0px; font-weight: {font_weight};")
        provider_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        provider_label.setFixedWidth(28)  # Tiny column, just wide enough for "GPT", "MMT", etc.
        provider_label.setFixedHeight(18)
        # Add tooltip with full provider name (only if code exists)
        if match.provider_code:
            provider_tooltips = {
                "GT": "Google Translate",
                "AT": "Amazon Translate",
                "MMT": "ModernMT",
                "DL": "DeepL",
                "MS": "Microsoft Translator",
                "MM": "MyMemory",
                "CL": "Claude",
                "GPT": "OpenAI",
                "GEM": "Gemini"
            }
            # Add any custom termbase codes to tooltips (they'll show termbase name from metadata)
            if match.match_type == 'Termbase' and match.metadata.get('termbase_name'):
                provider_tooltips[match.provider_code] = match.metadata.get('termbase_name', match.provider_code)
            full_name = provider_tooltips.get(match.provider_code, match.provider_code)
            provider_label.setToolTip(full_name)
        content_layout.addWidget(provider_label, 0, Qt.AlignmentFlag.AlignTop)
        
        main_layout.addLayout(content_layout, 1)  # Expand to fill remaining space
        
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        
        # NOW call update_styling() after num_label_ref is set
        self.update_styling()
    
    def _format_text(self, text: str) -> str:
        """Format text based on show_tags setting"""
        if self.show_tags:
            # Show tags with text color
            import re
            # Escape HTML entities first to prevent double-escaping
            text = text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
            # Convert newlines to <br> so QLabel (RichText mode) renders them as line breaks
            text = text.replace('\n', '<br>')
            # Now color the escaped tags
            tag_pattern = re.compile(r'&lt;/?[a-zA-Z][a-zA-Z0-9]*/?&gt;')
            text = tag_pattern.sub(lambda m: f'<span style="color: {self.tag_highlight_color};">{m.group()}</span>', text)
            return text
        else:
            # QLabel interprets this as HTML; convert newlines to <br> so they render as line breaks
            text = text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
            text = text.replace('\n', '<br>')
            return text
    
    def update_tag_color(self, color: str):
        """Update tag highlight color for this item"""
        self.tag_highlight_color = color
        # Refresh text if tags are shown
        if self.show_tags and self.source_label and self.target_label:
            self.source_label.setText(self._format_text(self.match.source))
            self.target_label.setText(self._format_text(self.match.target))
    
    @classmethod
    def set_font_size(cls, size: int):
        """Set the font size for all match items"""
        cls.font_size_pt = size
    
    def update_font_size(self):
        """Update font size for this item"""
        if self.source_label:
            self.source_label.setStyleSheet(f"font-size: {self.font_size_pt}px; color: #333; padding: 0px; margin: 0px;")
        if self.target_label:
            self.target_label.setStyleSheet(f"font-size: {self.font_size_pt}px; color: #555; padding: 0px; margin: 0px;")
    
    def mousePressEvent(self, event):
        """Emit signal when clicked"""
        if event.button() == Qt.MouseButton.LeftButton:
            self.match_selected.emit(self.match)
            self.select()
        elif event.button() == Qt.MouseButton.RightButton:
            self._show_context_menu(event.globalPosition().toPoint())
    
    def _show_context_menu(self, pos):
        """Show context menu for this match item"""
        from PyQt6.QtWidgets import QMenu
        from PyQt6.QtGui import QAction

        if self.match.match_type == "Termbase":
            menu = QMenu()
            edit_action = QAction("✏️ Edit Termbase Entry", menu)
            edit_action.triggered.connect(self._edit_termbase_entry)
            menu.addAction(edit_action)
            delete_action = QAction("🗑️ Delete Termbase Entry", menu)
            delete_action.triggered.connect(self._delete_termbase_entry)
            menu.addAction(delete_action)
            menu.exec(pos)

        elif self.match.match_type == "TM":
            menu = QMenu()
            edit_action = QAction("✏️ Edit TM Entry", menu)
            edit_action.triggered.connect(self._edit_tm_entry)
            menu.addAction(edit_action)
            delete_action = QAction("🗑️ Delete TM Entry", menu)
            delete_action.triggered.connect(self._delete_tm_entry)
            menu.addAction(delete_action)
            menu.exec(pos)
    
    def _edit_termbase_entry(self):
        """Open termbase entry editor for this match"""
        if self.match.match_type != "Termbase":
            return
        
        # Get term_id and termbase_id from metadata
        term_id = self.match.metadata.get('term_id')
        termbase_id = self.match.metadata.get('termbase_id')
        
        if term_id and termbase_id:
            from modules.termbase_entry_editor import TermbaseEntryEditor
            
            # Get parent window (main application)
            parent_window = self.window()
            
            dialog = TermbaseEntryEditor(
                parent=parent_window,
                db_manager=getattr(parent_window, 'db_manager', None),
                termbase_id=termbase_id,
                term_id=term_id
            )
            
            if dialog.exec():
                # Entry was edited, refresh if needed
                # Signal could be emitted here to refresh the translation results panel
                pass
    
    def _delete_termbase_entry(self):
        """Delete this termbase entry"""
        from PyQt6.QtWidgets import QMessageBox
        
        if self.match.match_type != "Termbase":
            return
        
        # Get term_id and termbase_id from metadata
        term_id = self.match.metadata.get('term_id')
        termbase_id = self.match.metadata.get('termbase_id')
        source_term = self.match.source
        target_term = self.match.target
        
        if term_id and termbase_id:
            # Confirm deletion
            parent_window = self.window()
            reply = QMessageBox.question(
                parent_window,
                "Confirm Deletion",
                f"Delete termbase entry?\n\nSource: {source_term}\nTarget: {target_term}\n\nThis action cannot be undone.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No
            )
            
            if reply == QMessageBox.StandardButton.Yes:
                db_manager = getattr(parent_window, 'db_manager', None)
                if db_manager:
                    try:
                        # Log database path for debugging
                        if hasattr(parent_window, 'log'):
                            db_path = getattr(db_manager, 'db_path', 'unknown')
                            parent_window.log(f"🗑️ Deleting term ID {term_id} from database: {db_path}")
                        
                        cursor = db_manager.cursor
                        # First verify the term exists
                        cursor.execute("SELECT source_term, target_term FROM termbase_terms WHERE id = ?", (term_id,))
                        existing = cursor.fetchone()
                        if hasattr(parent_window, 'log'):
                            if existing:
                                parent_window.log(f"   Found term to delete: {existing[0]} → {existing[1]}")
                            else:
                                parent_window.log(f"   ⚠️ Term ID {term_id} not found in database!")
                        
                        # Delete the term
                        cursor.execute("DELETE FROM termbase_terms WHERE id = ?", (term_id,))
                        rows_deleted = cursor.rowcount
                        db_manager.connection.commit()
                        
                        if hasattr(parent_window, 'log'):
                            parent_window.log(f"   ✅ Deleted {rows_deleted} row(s) from database")
                        
                        # v1.10.64: route through the main window's
                        # consolidated post-delete refresh helper.
                        # Previously this clear-cache-and-re-search
                        # ran without rebuilding the in-memory
                        # termbase_index, so find_termbase_matches_in_source
                        # would hit the stale index and keep returning
                        # the just-deleted term — TermLens showed it as
                        # a "match" until the next full project reload.
                        # The helper does cache clear + index rebuild +
                        # segment refresh + termbase-tab refresh.
                        if hasattr(parent_window, '_post_termbase_delete_refresh'):
                            try:
                                parent_window._post_termbase_delete_refresh()
                            except Exception as refresh_err:
                                if hasattr(parent_window, 'log'):
                                    parent_window.log(f"   ⚠️ post-delete refresh failed: {refresh_err}")

                        # Reset the last selected row to force re-highlighting when returning to this segment
                        if hasattr(parent_window, '_last_selected_row'):
                            parent_window._last_selected_row = None

                        # Update this card's source-cell highlight too
                        # (the helper above handles TermLens but not the
                        # cell-level termbase highlighting on the source
                        # widget). Kept as a defensive belt-and-braces
                        # pass — overrides any stale highlight from
                        # before the helper ran.
                        if hasattr(parent_window, 'table') and hasattr(parent_window, 'find_termbase_matches_in_source'):
                            current_row = parent_window.table.currentRow()
                            if current_row >= 0:
                                source_widget = parent_window.table.cellWidget(current_row, 2)
                                if source_widget and hasattr(source_widget, 'toPlainText'):
                                    source_text = source_widget.toPlainText()
                                    termbase_matches = parent_window.find_termbase_matches_in_source(source_text)
                                    if hasattr(source_widget, 'highlight_termbase_matches'):
                                        source_widget.highlight_termbase_matches(termbase_matches)
                                    if hasattr(source_widget, 'termbase_matches'):
                                        source_widget.termbase_matches = termbase_matches
                        
                        QMessageBox.information(parent_window, "Success", "Termbase entry deleted")
                        # Hide this match card since it's been deleted
                        self.hide()
                    except Exception as e:
                        QMessageBox.critical(parent_window, "Error", f"Failed to delete entry: {e}")

    # ------------------------------------------------------------------
    # TM entry edit / delete
    # ------------------------------------------------------------------

    def _edit_tm_entry(self):
        """Open an inline editor dialog to edit this TM entry's target text."""
        if self.match.match_type != "TM":
            return

        from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout,
                                     QLabel, QTextEdit, QDialogButtonBox,
                                     QMessageBox)

        tm_id = self.match.metadata.get('tm_id', '')
        tm_name = self.match.metadata.get('tm_name', tm_id or 'TM')
        old_source = self.match.source
        old_target = self.match.target

        if not tm_id:
            QMessageBox.warning(self.window(), "Cannot Edit",
                                "TM identifier is not available for this entry.")
            return

        parent_window = self.window()

        # Build a simple edit dialog
        dialog = QDialog(parent_window)
        dialog.setWindowTitle(f"Edit TM Entry – {tm_name}")
        dialog.setMinimumWidth(520)
        layout = QVBoxLayout(dialog)
        layout.setSpacing(8)

        # Source (read-only for display context only)
        layout.addWidget(QLabel("Source (read-only):"))
        source_edit = QTextEdit()
        source_edit.setPlainText(old_source)
        source_edit.setReadOnly(True)
        source_edit.setMaximumHeight(90)
        layout.addWidget(source_edit)

        # Target (editable)
        layout.addWidget(QLabel("Target:"))
        target_edit = QTextEdit()
        target_edit.setPlainText(old_target)
        target_edit.setMaximumHeight(120)
        layout.addWidget(target_edit)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        new_target = target_edit.toPlainText()
        if new_target == old_target:
            return  # Nothing changed

        # Perform update via tm_manager or db_manager
        tm_manager = getattr(parent_window, 'tm_manager', None)
        db_manager = getattr(parent_window, 'db_manager', None)

        updated = False
        try:
            if tm_manager and hasattr(tm_manager, 'update_entry'):
                updated = tm_manager.update_entry(tm_id, old_source, old_target,
                                                  old_source, new_target)
            elif db_manager and hasattr(db_manager, 'update_entry'):
                updated = db_manager.update_entry(tm_id, old_source, old_target,
                                                  old_source, new_target)
        except Exception as e:
            QMessageBox.critical(parent_window, "Error", f"Failed to update TM entry:\n{e}")
            return

        if updated:
            if hasattr(parent_window, 'log'):
                parent_window.log(f"✏️ Updated TM entry in '{tm_name}'")
            # Update the in-memory match object so the card reflects the change immediately
            self.match.target = new_target
            if self.target_label:
                self.target_label.setText(self._format_text(new_target))
            # Invalidate TM cache so the next lookup picks up the new value
            if hasattr(parent_window, '_last_selected_row'):
                parent_window._last_selected_row = None
        else:
            QMessageBox.warning(parent_window, "Not Found",
                                "The entry could not be found in the database.\n"
                                "It may have already been changed or deleted.")

    def _delete_tm_entry(self):
        """Delete this TM entry from the database."""
        if self.match.match_type != "TM":
            return

        from PyQt6.QtWidgets import QMessageBox

        tm_id = self.match.metadata.get('tm_id', '')
        tm_name = self.match.metadata.get('tm_name', tm_id or 'TM')
        source_text = self.match.source
        target_text = self.match.target

        if not tm_id:
            QMessageBox.warning(self.window(), "Cannot Delete",
                                "TM identifier is not available for this entry.")
            return

        parent_window = self.window()

        reply = QMessageBox.question(
            parent_window,
            "Confirm Deletion",
            f"Delete TM entry from '{tm_name}'?\n\n"
            f"Source: {source_text[:120]}\n"
            f"Target: {target_text[:120]}\n\n"
            "This action cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )

        if reply != QMessageBox.StandardButton.Yes:
            return

        tm_manager = getattr(parent_window, 'tm_manager', None)
        db_manager = getattr(parent_window, 'db_manager', None)

        try:
            if tm_manager and hasattr(tm_manager, 'delete_entry'):
                tm_manager.delete_entry(tm_id, source_text, target_text)
            elif db_manager and hasattr(db_manager, 'delete_entry'):
                db_manager.delete_entry(tm_id, source_text, target_text)
            else:
                QMessageBox.critical(parent_window, "Error",
                                     "No TM manager available to perform deletion.")
                return
        except Exception as e:
            QMessageBox.critical(parent_window, "Error", f"Failed to delete TM entry:\n{e}")
            return

        if hasattr(parent_window, 'log'):
            parent_window.log(f"🗑️ Deleted TM entry from '{tm_name}'")

        # Invalidate row cache so the next navigation re-runs the TM search
        if hasattr(parent_window, '_last_selected_row'):
            parent_window._last_selected_row = None

        # Hide this card – it no longer exists in the DB
        self.hide()

    def select(self):
        """Select this match"""
        self.is_selected = True
        self.update_styling()
    
    def deselect(self):
        """Deselect this match"""
        self.is_selected = False
        self.update_styling()
    
    def update_styling(self):
        """Update visual styling based on selection state and match type"""
        # Color code by match type: LLM=purple, TM=red, Termbase=green, MT=orange, NT=gray, NonTrans=yellow
        base_color_map = {
            "LLM": "#9c27b0",  # Purple for LLM translations
            "TM": "#ff6b6b",  # Red for Translation Memory
            "Termbase": "#4CAF50",  # Green for all termbase matches (Material Design Green 500)
            "MT": "#ff9800",  # Orange for Machine Translation
            "NT": "#adb5bd",  # Gray for New Translation
            "NonTrans": "#E6C200"  # Pastel yellow for Non-Translatables
        }

        base_color = base_color_map.get(self.match.match_type, "#adb5bd")
        
        # Special styling for Non-Translatables
        if self.match.match_type == "NonTrans":
            type_color = "#FFFDD0"  # Pastel yellow background
        # For termbase matches, apply project/background green shading
        elif self.match.match_type == "Termbase":
            is_forbidden = self.match.metadata.get('forbidden', False)
            is_project_termbase_flag = self.match.metadata.get('is_project_termbase', False)
            termbase_ranking = self.match.metadata.get('ranking', None)

            # EFFECTIVE project termbase = explicit flag OR ranking == 1
            is_effective_project = is_project_termbase_flag or (termbase_ranking == 1)
            is_project_termbase = is_effective_project  # For later use in background styling

            if is_forbidden:
                type_color = "#000000"  # Forbidden terms: black
            else:
                # Project glossary = medium green, Background = light green
                type_color = "#A5D6A7" if is_effective_project else "#C8E6C9"
        else:
            type_color = base_color
        
        # Update styling only for the number label, not the entire item
        if hasattr(self, 'num_label_ref') and self.num_label_ref:
            if self.is_selected:
                # Selected: darker shade of type color with black text and outline
                darker_color = self._darken_color(type_color)
                self.num_label_ref.setStyleSheet(f"""
                    QLabel {{
                        background-color: {darker_color};
                        color: black;
                        font-weight: bold;
                        font-size: 10px;
                        min-width: 22px;
                        padding: 2px;
                        border-radius: 2px;
                        border: 1px solid rgba(255, 255, 255, 0.3);
                    }}
                """)
                # Add background to the entire item only when selected
                self.setStyleSheet(f"""
                    CompactMatchItem {{
                        background-color: {self._lighten_color(type_color, 0.95)};
                        border: 1px solid {type_color};
                    }}
                """)
            else:
                # Unselected: number badge colored with customizable text color and subtle outline
                self.num_label_ref.setStyleSheet(f"""
                    QLabel {{
                        background-color: {type_color};
                        color: {CompactMatchItem.badge_text_color};
                        font-weight: bold;
                        font-size: 10px;
                        min-width: 22px;
                        padding: 1px;
                        border-radius: 2px;
                        border: 1px solid rgba(255, 255, 255, 0.4);
                    }}
                """)
                # Use theme colors for background if available
                if CompactMatchItem.theme_manager:
                    theme = CompactMatchItem.theme_manager.current_theme
                    bg_color = theme.base
                    hover_color = theme.alternate_bg
                else:
                    bg_color = "white"
                    hover_color = "#f5f5f5"
                
                self.setStyleSheet(f"""
                    CompactMatchItem {{
                        background-color: {bg_color};
                        border: none;
                    }}
                    CompactMatchItem:hover {{
                        background-color: {hover_color};
                    }}
                """)
    
    @staticmethod
    def _lighten_color(hex_color: str, factor: float) -> str:
        """Lighten a hex color"""
        hex_color = hex_color.lstrip('#')
        r, g, b = tuple(int(hex_color[i:i+2], 16) for i in (0, 2, 4))
        r = int(r + (255 - r) * (1 - factor))
        g = int(g + (255 - g) * (1 - factor))
        b = int(b + (255 - b) * (1 - factor))
        return f'#{r:02x}{g:02x}{b:02x}'
    
    @staticmethod
    def _darken_color(hex_color: str, factor: float = 0.7) -> str:
        """Darken a hex color"""
        hex_color = hex_color.lstrip('#')
        r, g, b = tuple(int(hex_color[i:i+2], 16) for i in (0, 2, 4))
        r = int(r * factor)
        g = int(g * factor)
        b = int(b * factor)
        return f'#{r:02x}{g:02x}{b:02x}'
    
    def mouseMoveEvent(self, event):
        """Support drag/drop"""
        if event.buttons() == Qt.MouseButton.LeftButton:
            drag = QDrag(self)
            mime_data = QMimeData()
            mime_data.setText(self.match.target)
            mime_data.setData("application/x-match", str(self.match.target).encode())
            drag.setMimeData(mime_data)
            drag.exec(Qt.DropAction.CopyAction)


class MatchSection(QWidget):
    """Stacked section for a match type (NT/MT/TM/Termbases)"""
    
    match_selected = pyqtSignal(TranslationMatch)
    
    def __init__(self, title: str, matches: List[TranslationMatch], parent=None, global_number_start: int = 1):
        super().__init__(parent)
        self.title = title
        self.matches = matches
        self.is_expanded = True
        self.match_items = []
        self.selected_index = -1
        self.global_number_start = global_number_start  # For global numbering across sections
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        
        # Section header (collapsible)
        header = self._create_header()
        layout.addWidget(header)
        
        # Matches container
        self.matches_container = QWidget()
        self.matches_layout = QVBoxLayout(self.matches_container)
        self.matches_layout.setContentsMargins(0, 0, 0, 0)  # No margins
        self.matches_layout.setSpacing(0)  # No spacing between matches
        
        # Populate with matches
        self._populate_matches()
        
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidget(self.matches_container)
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setStyleSheet("QScrollArea { border: none; background-color: white; }")
        layout.addWidget(self.scroll_area)
    
    def _create_header(self) -> QWidget:
        """Create collapsible header"""
        header = QWidget()
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(4, 2, 4, 2)
        
        # Toggle button
        self.toggle_btn = QPushButton("▼" if self.is_expanded else "▶")
        self.toggle_btn.setMaximumWidth(20)
        self.toggle_btn.setMaximumHeight(20)
        self.toggle_btn.setFlat(True)
        self.toggle_btn.clicked.connect(self._toggle_section)
        header_layout.addWidget(self.toggle_btn)
        
        # Title + match count
        title_text = f"{self.title}"
        if self.matches:
            title_text += f" ({len(self.matches)})"
        
        title_label = QLabel(title_text)
        title_label.setStyleSheet("font-weight: bold; font-size: 10px; color: #333;")
        header_layout.addWidget(title_label)
        
        header_layout.addStretch()
        
        header.setStyleSheet("""
            background-color: #f0f0f0;
            border-bottom: 1px solid #ddd;
            padding: 2px;
        """)
        
        return header
    
    def _populate_matches(self):
        """Populate section with matches using global numbering"""
        for local_idx, match in enumerate(self.matches):
            global_number = self.global_number_start + local_idx
            item = CompactMatchItem(match, match_number=global_number)
            item.match_selected.connect(lambda m, i=local_idx: self._on_match_selected(m, i))
            self.matches_layout.addWidget(item)
            self.match_items.append(item)
        
        self.matches_layout.addStretch()
    
    def _toggle_section(self):
        """Toggle section expansion"""
        self.is_expanded = not self.is_expanded
        self.toggle_btn.setText("▼" if self.is_expanded else "▶")
        self.scroll_area.setVisible(self.is_expanded)
    
    def _on_match_selected(self, match: TranslationMatch, index: int):
        """Handle match selection"""
        # Deselect previous
        if 0 <= self.selected_index < len(self.match_items):
            self.match_items[self.selected_index].deselect()
        
        # Select new
        self.selected_index = index
        if 0 <= index < len(self.match_items):
            self.match_items[index].select()
        
        self.match_selected.emit(match)
    
    def select_by_number(self, number: int):
        """Select match by number (1-based)"""
        if 1 <= number <= len(self.match_items):
            self._on_match_selected(self.matches[number-1], number-1)
            # Scroll to visible
            self.scroll_area.ensureWidgetVisible(self.match_items[number-1])
    
    def navigate(self, direction: int):
        """Navigate matches: direction=1 for next, -1 for previous"""
        new_index = self.selected_index + direction
        if 0 <= new_index < len(self.match_items):
            self._on_match_selected(self.matches[new_index], new_index)
            self.scroll_area.ensureWidgetVisible(self.match_items[new_index])
            return True
        return False


class TranslationResultsPanel(QWidget):
    """
    Main translation results panel (right side of editor)
    Compact memoQ-style design with stacked match sections
    
    Features:
    - Keyboard navigation: Up/Down arrows to cycle through matches
    - Insert selected match: Press Enter
    - Quick insert by number: Ctrl+1 through Ctrl+9 (1-based index)
    - Vertical compare boxes with resizable splitter
    - Match numbering display
    - Zoom controls for both match list and compare boxes
    """
    
    match_selected = pyqtSignal(TranslationMatch)
    match_inserted = pyqtSignal(str)  # Emitted when user wants to insert match into target
    
    # Class variables for font sizes
    compare_box_font_size = 9
    
    def __init__(self, parent=None, parent_app=None):
        super().__init__(parent)
        self.parent_app = parent_app  # Reference to main app for settings access
        self.theme_manager = parent_app.theme_manager if parent_app and hasattr(parent_app, 'theme_manager') else None
        self.matches_by_type: Dict[str, List[TranslationMatch]] = {}
        self.current_selection: Optional[TranslationMatch] = None
        self.all_matches: List[TranslationMatch] = []
        self.match_sections: Dict[str, MatchSection] = {}
        self.match_items: List[CompactMatchItem] = []
        self.selected_index = -1
        self.compare_text_edits = []  # Track compare boxes for font size updates
        self.setup_ui()
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)  # Ensure widget receives keyboard events

    
    def setup_ui(self):
        """Setup the UI"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(2)
        
        # Set class-level theme_manager for CompactMatchItem
        CompactMatchItem.theme_manager = self.theme_manager
        
        # Header with segment info
        # Get theme colors
        if self.theme_manager:
            theme = self.theme_manager.current_theme
            bg_color = theme.base
            border_color = theme.border
            separator_color = theme.separator
            title_color = theme.text_disabled
            frame_bg = theme.alternate_bg
        else:
            bg_color = "white"
            border_color = "#ddd"
            separator_color = "#e0e0e0"
            title_color = "#666"
            frame_bg = "#f5f5f5"
        
        self.segment_label = QLabel("No segment selected")
        self.segment_label.setStyleSheet(f"font-weight: bold; font-size: 10px; color: {title_color};")
        layout.addWidget(self.segment_label)
        
        # Use splitter for resizable sections (matches vs compare boxes)
        self.main_splitter = QSplitter(Qt.Orientation.Vertical)

        self.main_splitter.setStyleSheet(f"QSplitter::handle {{ background-color: {separator_color}; }}")

        # Matches scroll area
        self.matches_scroll = QScrollArea()
        self.matches_scroll.setWidgetResizable(True)
        self.matches_scroll.setStyleSheet(f"""
            QScrollArea {{
                border: 1px solid {border_color};
                background-color: {bg_color};
                border-radius: 3px;
            }}
        """)
        
        self.matches_container = QWidget()
        self.matches_container.setStyleSheet(f"background-color: {bg_color};")
        self.main_layout = QVBoxLayout(self.matches_container)
        self.main_layout.setContentsMargins(0, 0, 0, 0)
        self.main_layout.setSpacing(2)
        
        self.matches_scroll.setWidget(self.matches_container)
        self.main_splitter.addWidget(self.matches_scroll)
        
        # Compare box (shown when TM match selected) - VERTICAL STACKED LAYOUT
        self.compare_frame = self._create_compare_box()
        self.main_splitter.addWidget(self.compare_frame)
        self.compare_frame.hide()  # Hidden by default
        
        # Termbase data viewer (shown when termbase match selected)
        self.termbase_frame = self._create_termbase_viewer()
        self.main_splitter.addWidget(self.termbase_frame)
        self.termbase_frame.hide()  # Hidden by default
        
        # Tabbed widget for TM Info and Notes (always visible, compact)
        self.info_tabs = QTabWidget()
        self.info_tabs.setStyleSheet(f"""
            QTabWidget::pane {{
                border: 1px solid {border_color};
                background-color: {bg_color};
                border-radius: 3px;
            }}
            QTabBar::tab {{
                background-color: {frame_bg};
                border: 1px solid {border_color};
                border-bottom: none;
                padding: 3px 8px;
                font-size: 9px;
                min-width: 60px;
            }}
            QTabBar::tab:selected {{
                background-color: {bg_color};
                font-weight: bold;
            }}
        """)
        self.info_tabs.setMaximumHeight(100)
        
        # TM Info tab
        self.tm_info_frame = self._create_tm_info_panel()
        self.info_tabs.addTab(self.tm_info_frame, "💾 TM Info")
        
        # Notes tab
        self.notes_widget = QWidget()
        notes_layout = QVBoxLayout(self.notes_widget)
        notes_layout.setContentsMargins(4, 4, 4, 4)
        notes_layout.setSpacing(2)
        
        self.notes_edit = QTextEdit()
        self.notes_edit.setPlaceholderText("Add notes about this segment, context, or translation concerns...")
        self.notes_edit.setStyleSheet(f"font-size: 9px; padding: 4px; background-color: {bg_color}; color: {theme.text if self.theme_manager else '#333'}; border: none;")
        notes_layout.addWidget(self.notes_edit)
        
        self.info_tabs.addTab(self.notes_widget, "📝 Segment note")
        
        self.main_splitter.addWidget(self.info_tabs)
        
        # Set splitter proportions for all 4 widgets:
        # [matches_scroll, compare_frame, termbase_frame, info_tabs]
        # Compare/Termbase are hidden by default, give them reasonable starting sizes
        self.main_splitter.setSizes([300, 200, 150, 100])
        self.main_splitter.setCollapsible(0, False)  # matches_scroll
        self.main_splitter.setCollapsible(1, False)  # compare_frame - don't allow collapsing
        self.main_splitter.setCollapsible(2, True)   # termbase_frame  
        self.main_splitter.setCollapsible(3, False)  # info_tabs
        
        layout.addWidget(self.main_splitter)
    
    def apply_theme(self):
        """Refresh all theme-dependent colors when theme changes"""
        if not self.theme_manager:
            return
        
        theme = self.theme_manager.current_theme
        
        bg_color = theme.base
        border_color = theme.border
        separator_color = theme.separator
        frame_bg = theme.alternate_bg
        title_color = theme.text_disabled
        text_color = theme.text
        
        # Update class-level theme_manager for CompactMatchItem
        CompactMatchItem.theme_manager = self.theme_manager
        
        # Update main scroll area
        if hasattr(self, 'matches_scroll'):
            self.matches_scroll.setStyleSheet(f"""
                QScrollArea {{
                    border: 1px solid {border_color};
                    background-color: {bg_color};
                    border-radius: 3px;
                }}
            """)
        
        # Update matches container background
        if hasattr(self, 'matches_container'):
            self.matches_container.setStyleSheet(f"background-color: {bg_color};")
        
        # Update compare frame
        if hasattr(self, 'compare_frame') and self.compare_frame:
            self.compare_frame.setStyleSheet(f"""
                QFrame {{
                    background-color: {frame_bg};
                    border: 1px solid {border_color};
                    border-radius: 3px;
                    padding: 4px;
                }}
            """)
        
        # Update compare text boxes backgrounds using QPalette (more reliable than stylesheet)
        box_colors = [theme.panel_info, theme.panel_warning, theme.panel_neutral]
        for i, text_edit in enumerate(self.compare_text_edits):
            if text_edit and i < len(box_colors):
                bg_color = box_colors[i]
                
                # Clear existing stylesheet first, then set new one
                text_edit.setStyleSheet("")
                new_style = f"""
                    QTextEdit {{
                        font-size: {self.compare_box_font_size}px;
                        padding: 3px;
                        background-color: {bg_color};
                        border: 1px solid {border_color};
                        border-radius: 2px;
                        color: {text_color};
                    }}
                """
                text_edit.setStyleSheet(new_style)
                
                # Also set palette for reliability
                palette = text_edit.palette()
                palette.setColor(palette.ColorRole.Base, QColor(bg_color))
                palette.setColor(palette.ColorRole.Text, QColor(text_color))
                text_edit.setPalette(palette)
                text_edit.setAutoFillBackground(True)
                
                # Force update
                text_edit.style().unpolish(text_edit)
                text_edit.style().polish(text_edit)
                text_edit.update()
        
        # Update segment label
        if hasattr(self, 'segment_label'):
            self.segment_label.setStyleSheet(f"font-weight: bold; font-size: 10px; color: {title_color};")
        
        # Update notes section
        if hasattr(self, 'notes_edit'):
            self.notes_edit.setStyleSheet(f"font-size: 9px; padding: 4px; background-color: {bg_color}; color: {text_color}; border: none;")
        
        # Update info_tabs (TM Info + Notes tabs)
        if hasattr(self, 'info_tabs'):
            self.info_tabs.setStyleSheet(f"""
                QTabWidget::pane {{
                    border: 1px solid {border_color};
                    background-color: {bg_color};
                    border-radius: 3px;
                }}
                QTabBar::tab {{
                    background-color: {frame_bg};
                    border: 1px solid {border_color};
                    border-bottom: none;
                    padding: 3px 8px;
                    font-size: 9px;
                    min-width: 60px;
                }}
                QTabBar::tab:selected {{
                    background-color: {bg_color};
                    font-weight: bold;
                }}
            """)
        
        # Update TM Info panel (now inside tab)
        if hasattr(self, 'tm_info_frame') and self.tm_info_frame:
            self.tm_info_frame.setStyleSheet(f"""
                QFrame {{
                    background-color: {bg_color};
                    border: none;
                    padding: 2px;
                }}
            """)
        
        if hasattr(self, 'tm_info_title'):
            self.tm_info_title.setStyleSheet(f"font-weight: bold; font-size: 9px; color: {title_color}; margin-bottom: 2px;")
        
        if hasattr(self, 'tm_name_label'):
            self.tm_name_label.setStyleSheet(f"font-size: 9px; color: {text_color}; font-weight: bold;")
        
        if hasattr(self, 'tm_languages_label'):
            self.tm_languages_label.setStyleSheet(f"font-size: 8px; color: {title_color};")
        
        if hasattr(self, 'tm_stats_label'):
            self.tm_stats_label.setStyleSheet(f"font-size: 8px; color: {title_color};")
        
        if hasattr(self, 'tm_description_label'):
            self.tm_description_label.setStyleSheet(f"""
                QLabel {{
                    font-size: 8px;
                    color: {title_color};
                    background-color: {bg_color};
                    padding: 3px;
                    border: 1px solid {border_color};
                    border-radius: 2px;
                }}
            """)
        
        # Update Termbase viewer panel
        source_bg = theme.panel_info
        target_bg = theme.panel_neutral
        metadata_bg = theme.panel_warning
        
        if hasattr(self, 'termbase_frame') and self.termbase_frame:
            self.termbase_frame.setStyleSheet(f"""
                QFrame {{
                    background-color: {frame_bg};
                    border: 1px solid {border_color};
                    border-radius: 3px;
                    padding: 4px;
                }}
            """)
        
        if hasattr(self, 'termbase_title'):
            self.termbase_title.setStyleSheet(f"font-weight: bold; font-size: 9px; color: {title_color};")
        
        if hasattr(self, 'termbase_source_label'):
            self.termbase_source_label.setStyleSheet(f"font-weight: bold; font-size: 8px; color: {title_color};")
        
        if hasattr(self, 'termbase_source'):
            self.termbase_source.setStyleSheet(f"""
                QLabel {{
                    background-color: {source_bg};
                    border: 1px solid {border_color};
                    border-radius: 2px;
                    font-size: 10px;
                    padding: 6px;
                    margin: 0px;
                    color: {text_color};
                }}
            """)
        
        if hasattr(self, 'termbase_target_label'):
            self.termbase_target_label.setStyleSheet(f"font-weight: bold; font-size: 8px; color: {title_color};")
        
        if hasattr(self, 'termbase_target'):
            self.termbase_target.setStyleSheet(f"""
                QLabel {{
                    background-color: {target_bg};
                    border: 1px solid {border_color};
                    border-radius: 2px;
                    font-size: 10px;
                    padding: 6px;
                    margin: 0px;
                    color: {text_color};
                }}
            """)
        
        if hasattr(self, 'termbase_metadata_label'):
            self.termbase_metadata_label.setStyleSheet(f"font-weight: bold; font-size: 8px; color: {title_color};")
        
        if hasattr(self, 'termbase_metadata'):
            self.termbase_metadata.setStyleSheet(f"""
                QTextBrowser {{
                    background-color: {metadata_bg};
                    border: 1px solid {border_color};
                    border-radius: 2px;
                    font-size: {self.compare_box_font_size}px;
                    padding: 4px;
                    margin: 0px;
                    color: {text_color};
                }}
            """)

    def _apply_compare_box_theme(self):
        """Apply theme colors to compare boxes - called when boxes become visible"""
        if not self.theme_manager:
            return
        
        theme = self.theme_manager.current_theme
        border_color = theme.border
        text_color = theme.text
        box_colors = [theme.panel_info, theme.panel_warning, theme.panel_neutral]
        
        for i, text_edit in enumerate(self.compare_text_edits[:3]):  # Only the 3 compare boxes
            if text_edit:
                bg_color = box_colors[i]
                # Clear and set stylesheet
                text_edit.setStyleSheet("")
                text_edit.setStyleSheet(f"""
                    QTextEdit {{
                        font-size: {self.compare_box_font_size}px;
                        padding: 3px;
                        background-color: {bg_color};
                        border: 1px solid {border_color};
                        border-radius: 2px;
                        color: {text_color};
                    }}
                """)
                # Also set palette for reliability
                palette = text_edit.palette()
                palette.setColor(palette.ColorRole.Base, QColor(bg_color))
                palette.setColor(palette.ColorRole.Text, QColor(text_color))
                text_edit.setPalette(palette)
                text_edit.setAutoFillBackground(True)
                # Force visual update
                text_edit.style().unpolish(text_edit)
                text_edit.style().polish(text_edit)
                text_edit.update()

    def _create_compare_box(self) -> QFrame:
        """Create compare box frame with VERTICAL stacked layout - all boxes resize together"""
        # Get theme colors - try to get from parent_app if self.theme_manager is not set yet
        theme_manager = self.theme_manager
        if not theme_manager and hasattr(self, 'parent_app') and self.parent_app:
            theme_manager = getattr(self.parent_app, 'theme_manager', None)
        
        if theme_manager:
            theme = theme_manager.current_theme
            frame_bg = theme.alternate_bg
            border_color = theme.border
            title_color = theme.text_disabled
            box1_bg = theme.panel_info
            box2_bg = theme.panel_warning
            box3_bg = theme.panel_neutral
        else:
            frame_bg = "#fafafa"
            border_color = "#ddd"
            title_color = "#666"
            box1_bg = "#e3f2fd"
            box2_bg = "#fff3cd"
            box3_bg = "#d4edda"

        frame = QFrame()
        frame.setStyleSheet(f"""
            QFrame {{
                background-color: {frame_bg};
                border: 1px solid {border_color};
                border-radius: 3px;
                padding: 4px;
            }}
        """)

        layout = QVBoxLayout(frame)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(2)

        # Title
        title = QLabel("📊 Compare Box")
        title.setStyleSheet(f"font-weight: bold; font-size: 9px; color: {title_color};")
        layout.addWidget(title)

        # Box 1: Current Source
        box1 = self._create_compare_text_box("Current Source:", box1_bg)
        self.compare_current = box1[1]
        self.compare_current_label = box1[2]
        layout.addWidget(box1[0], 1)  # stretch factor 1

        # Box 2: TM Source (with diff highlighting capability)
        box2 = self._create_compare_text_box("TM Source:", box2_bg)
        self.compare_tm_source = box2[1]
        self.compare_source_label = box2[2]
        self.compare_source_container = box2[0]
        layout.addWidget(box2[0], 1)  # stretch factor 1

        # Box 3: TM Target
        box3 = self._create_compare_text_box("TM Target:", box3_bg)
        self.compare_tm_target = box3[1]
        self.compare_target_label = box3[2]
        layout.addWidget(box3[0], 1)  # stretch factor 1

        return frame
    
    def _create_compare_text_box(self, label: str, bg_color: str) -> tuple:
        """Create a single compare text box"""
        # Get theme colors
        if self.theme_manager:
            theme = self.theme_manager.current_theme
            label_color = theme.text_disabled
            border_color = theme.border
            text_color = theme.text
        else:
            label_color = "#666"
            border_color = "#ccc"
            text_color = "#333"

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(2)

        label_widget = QLabel(label)
        label_widget.setStyleSheet(f"font-weight: bold; font-size: 8px; color: {label_color};")
        layout.addWidget(label_widget)

        text_edit = QTextEdit()
        text_edit.setReadOnly(True)
        text_edit.setStyleSheet(f"""
            QTextEdit {{
                background-color: {bg_color};
                border: 1px solid {border_color};
                border-radius: 2px;
                font-size: {self.compare_box_font_size}px;
                padding: 4px;
                margin: 0px;
                color: {text_color};
            }}
        """)
        # Enable custom context menu so we can inject TM edit/delete actions
        text_edit.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        text_edit.customContextMenuRequested.connect(self._show_compare_box_context_menu)
        layout.addWidget(text_edit)

        # Track this text edit for font size updates
        self.compare_text_edits.append(text_edit)

        return (container, text_edit, label_widget)
    
    def _create_termbase_viewer(self) -> QFrame:
        """Create termbase data viewer frame"""
        # Get theme colors
        if self.theme_manager:
            theme = self.theme_manager.current_theme
            frame_bg = theme.alternate_bg
            border_color = theme.border
            title_color = theme.text_disabled
            text_color = theme.text
            source_bg = theme.panel_info
            target_bg = theme.panel_neutral
            metadata_bg = theme.panel_warning
        else:
            frame_bg = "#fafafa"
            border_color = "#ddd"
            title_color = "#666"
            text_color = "#333"
            source_bg = "#e3f2fd"
            target_bg = "#d4edda"
            metadata_bg = "#fff3cd"
        
        frame = QFrame()
        frame.setStyleSheet(f"""
            QFrame {{
                background-color: {frame_bg};
                border: 1px solid {border_color};
                border-radius: 3px;
                padding: 4px;
            }}
        """)
        
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)
        
        # Title with termbase name (will be updated dynamically)
        header_layout = QHBoxLayout()
        self.termbase_title = QLabel("📖 Term Info")
        self.termbase_title.setStyleSheet(f"font-weight: bold; font-size: 9px; color: {title_color};")
        header_layout.addWidget(self.termbase_title)
        header_layout.addStretch()
        
        # Refresh button
        self.termbase_refresh_btn = QPushButton("🔄 Refresh data")
        self.termbase_refresh_btn.setStyleSheet("""
            QPushButton {
                font-size: 8px;
                padding: 2px 6px;
                background-color: #2196F3;
                color: white;
                border: none;
                border-radius: 2px;
            }
            QPushButton:hover {
                background-color: #0b7dda;
            }
            QPushButton:focus {
                outline: none;
            }
        """)
        self.termbase_refresh_btn.setFixedHeight(20)
        self.termbase_refresh_btn.setToolTip("Refresh entry from database")
        self.termbase_refresh_btn.clicked.connect(self._on_refresh_termbase_entry)
        header_layout.addWidget(self.termbase_refresh_btn)
        
        # Edit button
        self.termbase_edit_btn = QPushButton("✏️ Edit")
        self.termbase_edit_btn.setStyleSheet("""
            QPushButton {
                font-size: 8px;
                padding: 2px 6px;
                background-color: #4CAF50;
                color: white;
                border: none;
                border-radius: 2px;
            }
            QPushButton:hover {
                background-color: #45a049;
            }
            QPushButton:focus {
                outline: none;
            }
        """)
        self.termbase_edit_btn.setFixedHeight(20)
        self.termbase_edit_btn.clicked.connect(self._on_edit_termbase_entry)
        header_layout.addWidget(self.termbase_edit_btn)
        
        layout.addLayout(header_layout)
        
        # Source and Target terms
        terms_container = QWidget()
        terms_layout = QVBoxLayout(terms_container)
        terms_layout.setContentsMargins(2, 2, 2, 2)
        terms_layout.setSpacing(3)
        
        # Source term
        self.termbase_source_label = QLabel("Source Term:")
        self.termbase_source_label.setStyleSheet(f"font-weight: bold; font-size: 8px; color: {title_color};")
        terms_layout.addWidget(self.termbase_source_label)
        
        self.termbase_source = QLabel()
        self.termbase_source.setStyleSheet(f"""
            QLabel {{
                background-color: {source_bg};
                border: 1px solid {border_color};
                border-radius: 2px;
                font-size: 10px;
                padding: 6px;
                margin: 0px;
                color: {text_color};
            }}
        """)
        self.termbase_source.setWordWrap(True)
        terms_layout.addWidget(self.termbase_source)
        
        # Target term
        self.termbase_target_label = QLabel("Target Term:")
        self.termbase_target_label.setStyleSheet(f"font-weight: bold; font-size: 8px; color: {title_color};")
        terms_layout.addWidget(self.termbase_target_label)
        
        self.termbase_target = QLabel()
        self.termbase_target.setStyleSheet(f"""
            QLabel {{
                background-color: {target_bg};
                border: 1px solid {border_color};
                border-radius: 2px;
                font-size: 10px;
                padding: 6px;
                margin: 0px;
                color: {text_color};
            }}
        """)
        self.termbase_target.setWordWrap(True)
        terms_layout.addWidget(self.termbase_target)
        
        layout.addWidget(terms_container)
        
        # Metadata area
        self.termbase_metadata_label = QLabel("Metadata:")
        self.termbase_metadata_label.setStyleSheet(f"font-weight: bold; font-size: 8px; color: {title_color};")
        layout.addWidget(self.termbase_metadata_label)
        
        from PyQt6.QtWidgets import QTextBrowser
        self.termbase_metadata = QTextBrowser()
        self.termbase_metadata.setReadOnly(True)
        self.termbase_metadata.setMaximumHeight(80)
        self.termbase_metadata.setStyleSheet(f"""
            QTextBrowser {{
                background-color: {metadata_bg};
                border: 1px solid {border_color};
                border-radius: 2px;
                font-size: {self.compare_box_font_size}px;
                padding: 4px;
                margin: 0px;
                color: {text_color};
            }}
        """)
        # Enable clickable links
        self.termbase_metadata.setOpenExternalLinks(True)
        layout.addWidget(self.termbase_metadata)
        
        # Track metadata text edit for font size updates
        self.compare_text_edits.append(self.termbase_metadata)
        
        return frame
    
    def _create_tm_info_panel(self) -> QFrame:
        """Create TM metadata info panel (memoQ-style) - shown in TM Info tab"""
        # Get theme colors
        if self.theme_manager:
            theme = self.theme_manager.current_theme
            frame_bg = theme.base
            border_color = theme.border
            title_color = theme.text_disabled
            text_color = theme.text
            desc_bg = theme.alternate_bg
        else:
            frame_bg = "#fff"
            border_color = "#ddd"
            title_color = "#666"
            text_color = "#333"
            desc_bg = "#f5f5f5"
        
        frame = QFrame()
        frame.setStyleSheet(f"""
            QFrame {{
                background-color: {frame_bg};
                border: none;
                padding: 2px;
            }}
        """)
        
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(2)
        
        # Info grid (compact layout - no title needed, it's in the tab)
        info_container = QWidget()
        info_layout = QVBoxLayout(info_container)
        info_layout.setContentsMargins(0, 0, 0, 0)
        info_layout.setSpacing(2)
        
        # TM Name
        self.tm_name_label = QLabel()
        self.tm_name_label.setStyleSheet(f"font-size: 9px; color: {text_color}; font-weight: bold;")
        self.tm_name_label.setWordWrap(True)
        info_layout.addWidget(self.tm_name_label)
        
        # Languages (smaller)
        self.tm_languages_label = QLabel()
        self.tm_languages_label.setStyleSheet(f"font-size: 8px; color: {title_color};")
        info_layout.addWidget(self.tm_languages_label)
        
        # Entry count and modified date in single line
        self.tm_stats_label = QLabel()
        self.tm_stats_label.setStyleSheet(f"font-size: 8px; color: {title_color};")
        self.tm_stats_label.setWordWrap(True)
        info_layout.addWidget(self.tm_stats_label)
        
        # Description (if available)
        self.tm_description_label = QLabel()
        self.tm_description_label.setStyleSheet(f"""
            QLabel {{
                font-size: 8px;
                color: {title_color};
                background-color: {desc_bg};
                padding: 3px;
                border: 1px solid {border_color};
                border-radius: 2px;
            }}
        """)
        self.tm_description_label.setWordWrap(True)
        self.tm_description_label.hide()  # Hidden if no description
        info_layout.addWidget(self.tm_description_label)
        
        layout.addWidget(info_container)
        
        return frame
    
    def _on_edit_termbase_entry(self):
        """Handle edit button click - open termbase entry editor dialog"""
        if not self.current_selection or self.current_selection.match_type != "Termbase":
            return
        
        # Get term_id from metadata if available
        term_id = self.current_selection.metadata.get('term_id')
        termbase_id = self.current_selection.metadata.get('termbase_id')
        
        if term_id and termbase_id:
            # Import and show editor dialog
            from modules.termbase_entry_editor import TermbaseEntryEditor
            
            # Get parent window (main application)
            parent_window = self.window()
            
            dialog = TermbaseEntryEditor(
                parent=parent_window,
                db_manager=getattr(parent_window, 'db_manager', None),
                termbase_id=termbase_id,
                term_id=term_id
            )
            
            if dialog.exec():
                # Entry was edited, refresh the display
                # Get updated term data and refresh the termbase viewer
                self._refresh_termbase_viewer()
    
    def _on_refresh_termbase_entry(self):
        """Handle refresh button click - reload entry from database"""
        if not self.current_selection or self.current_selection.match_type != "Termbase":
            return
        
        # Get term_id from metadata
        term_id = self.current_selection.metadata.get('term_id')
        if not term_id:
            return
        
        # Get parent window and database manager
        parent_window = self.window()
        db_manager = getattr(parent_window, 'db_manager', None)
        
        if not db_manager:
            return
        
        try:
            # Fetch fresh data from database
            cursor = db_manager.cursor
            cursor.execute("""
                SELECT source_term, target_term, domain, notes,
                       project, client, forbidden, termbase_id
                FROM termbase_terms
                WHERE id = ?
            """, (term_id,))

            row = cursor.fetchone()
            if row:
                # Update the current selection metadata with fresh data
                self.current_selection.source = row[0]
                self.current_selection.target = row[1]
                self.current_selection.metadata['domain'] = row[2] or ''
                self.current_selection.metadata['notes'] = row[3] or ''
                self.current_selection.metadata['project'] = row[4] or ''
                self.current_selection.metadata['client'] = row[5] or ''
                self.current_selection.metadata['forbidden'] = row[6] or False
                self.current_selection.metadata['termbase_id'] = row[7]
                
                # Re-display with updated data
                self._display_termbase_data(self.current_selection)
                
        except Exception as e:
            print(f"Error refreshing termbase entry: {e}")
    
    def _refresh_termbase_viewer(self):
        """Refresh termbase viewer with latest data from database"""
        if not self.current_selection or self.current_selection.match_type != "Termbase":
            return
        
        # Use the refresh handler to fetch and display fresh data
        self._on_refresh_termbase_entry()
    
    def _display_termbase_data(self, match: TranslationMatch):
        """Display termbase entry data in the viewer"""
        # Keep consistent "Term Info" title
        self.termbase_title.setText("📖 Term Info")
        
        # Display source and target terms
        self.termbase_source.setText(match.source)
        
        # Include synonyms in target display if available
        target_synonyms = match.metadata.get('target_synonyms', [])
        if target_synonyms:
            # Show main term with synonyms
            synonyms_text = ", ".join(target_synonyms)
            self.termbase_target.setText(f"{match.target} | {synonyms_text}")
        else:
            self.termbase_target.setText(match.target)
        
        # Build metadata text
        metadata_parts = []
        
        # Termbase name
        termbase_name = match.metadata.get('termbase_name', 'Unknown')
        metadata_parts.append(f"<b>Termbase:</b> {termbase_name}")
        
        # Domain
        domain = match.metadata.get('domain', '')
        if domain:
            metadata_parts.append(f"<b>Domain:</b> {domain}")
        
        # Notes
        notes = match.metadata.get('notes', '')
        if notes:
            # Truncate long notes for display
            if len(notes) > 200:
                notes = notes[:200] + "..."
            # Convert URLs to clickable links
            import re
            url_pattern = r'(https?://[^\s]+)'
            notes = re.sub(url_pattern, r'<a href="\1">\1</a>', notes)
            metadata_parts.append(f"<b>Notes:</b> {notes}")
        
        # Project
        project = match.metadata.get('project', '')
        if project:
            metadata_parts.append(f"<b>Project:</b> {project}")
        
        # Client
        client = match.metadata.get('client', '')
        if client:
            metadata_parts.append(f"<b>Client:</b> {client}")
        
        # Forbidden status
        forbidden = match.metadata.get('forbidden', False)
        if forbidden:
            metadata_parts.append("<b><span style='color: red;'>⚠️ FORBIDDEN TERM</span></b>")
        
        # Term ID (for debugging)
        term_id = match.metadata.get('term_id', '')
        if term_id:
            metadata_parts.append(f"<span style='color: #888; font-size: 7px;'>Term ID: {term_id}</span>")
        
        metadata_html = "<br>".join(metadata_parts) if metadata_parts else "<i>No metadata</i>"
        self.termbase_metadata.setHtml(metadata_html)
    
    def _display_tm_metadata(self, match: TranslationMatch):
        """Display TM metadata in the info panel (memoQ-style)"""
        # Get TM metadata from match
        tm_name = match.metadata.get('tm_name', 'Unknown TM')
        tm_id = match.metadata.get('tm_id', '')
        source_lang = match.metadata.get('source_lang', '')
        target_lang = match.metadata.get('target_lang', '')
        entry_count = match.metadata.get('entry_count', 0)
        modified_date = match.metadata.get('modified_date', '')
        description = match.metadata.get('description', '')
        
        # Update TM name
        self.tm_name_label.setText(f"📝 {tm_name}")
        
        # Update languages
        if source_lang and target_lang:
            self.tm_languages_label.setText(f"🌐 {source_lang} → {target_lang}")
        else:
            self.tm_languages_label.setText("")
        
        # Update stats (entry count + modified date)
        stats_parts = []
        if entry_count:
            stats_parts.append(f"📊 {entry_count:,} entries")
        if modified_date:
            # Format date nicely if it's ISO format
            try:
                from datetime import datetime
                dt = datetime.fromisoformat(modified_date)
                formatted_date = dt.strftime("%Y-%m-%d %H:%M")
                stats_parts.append(f"🕒 Modified: {formatted_date}")
            except:
                stats_parts.append(f"🕒 {modified_date}")
        
        self.tm_stats_label.setText(" • ".join(stats_parts) if stats_parts else "")
        
        # Update description (show/hide based on content)
        if description and description.strip():
            self.tm_description_label.setText(f"💬 {description}")
            self.tm_description_label.show()
        else:
            self.tm_description_label.hide()
    
    def add_matches(self, new_matches_dict: Dict[str, List[TranslationMatch]]):
        """
        Add new matches to existing matches (for progressive loading)
        Merges new matches with existing ones and re-renders the display
        Includes deduplication to prevent showing identical matches
        
        Args:
            new_matches_dict: Dict with keys like "NT", "MT", "TM", "Termbases"
        """
        # Merge new matches with existing matches_by_type
        if not hasattr(self, 'matches_by_type') or not self.matches_by_type:
            # No existing matches, just set them
            self.set_matches(new_matches_dict)
            return
        
        # Merge: Update existing match types with new matches (with deduplication)
        for match_type, new_matches in new_matches_dict.items():
            if new_matches:  # Only merge non-empty lists
                if match_type in self.matches_by_type:
                    # Deduplicate: Only add matches that don't already exist
                    existing_targets = {match.target for match in self.matches_by_type[match_type]}
                    unique_new_matches = [m for m in new_matches if m.target not in existing_targets]
                    if unique_new_matches:
                        self.matches_by_type[match_type].extend(unique_new_matches)
                else:
                    # New match type, add it
                    self.matches_by_type[match_type] = new_matches
        
        # Re-render with merged matches
        self.set_matches(self.matches_by_type)

    def _filter_shorter_matches(self, matches: List[TranslationMatch]) -> List[TranslationMatch]:
        """
        Filter out shorter termbase matches that are substrings of longer matches.

        Args:
            matches: List of termbase matches

        Returns:
            Filtered list with shorter substring matches removed
        """
        if not self.parent_app:
            return matches  # No filtering if no parent app

        hide_shorter = getattr(self.parent_app, 'termbase_hide_shorter_matches', False)

        if not hide_shorter:
            return matches

        # Create a list to track which matches to keep
        filtered_matches = []

        for i, match in enumerate(matches):
            # Check if this match's source is a substring of any other match's source
            is_substring = False
            source_lower = match.source.lower()

            for j, other_match in enumerate(matches):
                if i == j:
                    continue
                other_source_lower = other_match.source.lower()

                # Check if current match is a substring of the other match
                # and is shorter than the other match
                if (source_lower in other_source_lower and
                    len(source_lower) < len(other_source_lower)):
                    is_substring = True
                    break

            # Keep the match if it's not a substring of a longer match
            if not is_substring:
                filtered_matches.append(match)

        return filtered_matches

    def _with_adapted_codes(self, matches_dict: Dict[str, List[TranslationMatch]]):
        """TM matches that differ from the current segment only in the
        user's inline codes get the current source's codes (issue #194):
        ``{PK}{MN} muszą być różne.`` becomes ``{PKMN} muszą być różne.``
        when the segment says ``{PKMN}``."""
        try:
            from dataclasses import replace
            from modules import inline_codes
            if inline_codes.active_pattern() is None or not matches_dict.get("TM"):
                return matches_dict
            getter = getattr(self.parent_app, '_current_segment_source', None)
            current = getter() if getter else None
            if not current:
                return matches_dict
            adapted = []
            for match in matches_dict["TM"]:
                new_target = inline_codes.adapt_codes(match.source, match.target, current)
                if new_target is not None:
                    metadata = dict(match.metadata or {})
                    metadata["codes_adapted_from"] = match.target
                    match = replace(match, target=new_target, metadata=metadata)
                adapted.append(match)
            result = dict(matches_dict)
            result["TM"] = adapted
            return result
        except Exception:
            return matches_dict

    def set_matches(self, matches_dict: Dict[str, List[TranslationMatch]]):
        """
        Set matches from different sources in unified flat list with GLOBAL consecutive numbering
        (memoQ-style: single grid, color coding only identifies match type)
        
        Args:
            matches_dict: Dict with keys like "NT", "MT", "TM", "Termbases"
        """
        # Ensure CompactMatchItem has current theme_manager
        if self.theme_manager:
            CompactMatchItem.theme_manager = self.theme_manager

        matches_dict = self._with_adapted_codes(matches_dict)

        # Store current matches for delayed search access
        self._current_matches = matches_dict.copy()
        self.matches_by_type = matches_dict
        self.all_matches = []
        self.match_items = []  # Track all match items for navigation
        self.selected_index = -1
        
        # Clear existing matches
        while self.main_layout.count() > 0:
            item = self.main_layout.takeAt(0)
            if item and item.widget():
                item.widget().deleteLater()
        
        # Apply match limits per type (configurable, defaults provided)
        match_limits = getattr(self, 'match_limits', {
            "LLM": 3,
            "NT": 5,
            "MT": 3,
            "TM": 5,
            "Termbases": 10,
            "NonTrans": 20  # Non-translatables (show more since they're important)
        })
        
        # Build flat list of all matches with global numbering
        global_number = 1
        order = ["LLM", "NonTrans", "NT", "MT", "TM", "Termbases"]  # LLM first, NonTrans early (important for translator)
        
        for match_type in order:
            if match_type in matches_dict and matches_dict[match_type]:
                # Get matches for this type
                type_matches = matches_dict[match_type]

                # Apply filtering for termbase matches
                if match_type == "Termbases":
                    # Filter out shorter substring matches (if enabled)
                    type_matches = self._filter_shorter_matches(type_matches)

                # Apply limit for this match type
                limit = match_limits.get(match_type, 5)
                limited_matches = type_matches[:limit]

                for match in limited_matches:
                    self.all_matches.append(match)
                    
                    # Create match item with global number
                    item = CompactMatchItem(match, match_number=global_number)
                    item.match_selected.connect(lambda m, idx=len(self.match_items): self._on_match_item_selected(m, idx))
                    self.main_layout.addWidget(item)
                    self.match_items.append(item)
                    
                    global_number += 1
        
        self.main_layout.addStretch()
    
    def _on_match_item_selected(self, match: TranslationMatch, index: int):
        """Handle match item selection"""
        # Deselect previous
        if 0 <= self.selected_index < len(self.match_items):
            self.match_items[self.selected_index].deselect()
        
        # Select new
        self.selected_index = index
        if 0 <= index < len(self.match_items):
            self.match_items[index].select()
        
        self._on_match_selected(match)
    
    def _on_match_selected(self, match: TranslationMatch):
        """Handle match selection"""
        print(f"🔍 DEBUG: _on_match_selected called with match_type='{match.match_type}'")
        self.current_selection = match
        self.match_selected.emit(match)
        
        # Show appropriate viewer based on match type
        if match.match_type == "TM" and match.compare_source:
            # Show TM compare box
            print("📊 DEBUG: Showing TM compare box")
            self.compare_frame.show()
            self.termbase_frame.hide()
            
            # Switch to TM Info tab
            if hasattr(self, 'info_tabs'):
                self.info_tabs.setCurrentIndex(0)  # TM Info tab
            
            # Ensure compare box has reasonable size in splitter
            if hasattr(self, 'main_splitter'):
                sizes = self.main_splitter.sizes()
                # If compare_frame (index 1) has 0 or very small size, redistribute
                if len(sizes) >= 4 and sizes[1] < 100:
                    # Give compare box 200px, take from matches
                    total = sum(sizes)
                    sizes[1] = 200  # compare_frame
                    sizes[0] = max(100, total - 200 - sizes[2] - sizes[3])  # matches_scroll
                    self.main_splitter.setSizes(sizes)
            
            # Apply theme colors to compare boxes (needed because they might not apply when hidden)
            self._apply_compare_box_theme()
            
            # Update labels for TM
            self.compare_source_label.setText("TM Source:")
            self.compare_target_label.setText("TM Target:")
            self.compare_source_container.show()  # Show TM source box
            
            # Get current source text for diff comparison
            current_source = self.compare_current.toPlainText()
            tm_source = match.compare_source
            
            # Apply diff highlighting between current source and TM source
            self._apply_diff_highlighting(current_source, tm_source)
            
            # Set TM target (no diff highlighting needed)
            self.compare_tm_target.setText(match.target)
            
            # Populate TM metadata panel
            self._display_tm_metadata(match)
            
        elif match.match_type in ("MT", "LLM") and match.compare_source:
            # Show MT/LLM compare box (simplified - just current source and translation)
            print(f"🤖 DEBUG: Showing {match.match_type} compare box")
            self.compare_frame.show()
            self.termbase_frame.hide()
            
            # Ensure compare box has reasonable size in splitter
            if hasattr(self, 'main_splitter'):
                sizes = self.main_splitter.sizes()
                # If compare_frame (index 1) has 0 or very small size, redistribute
                if len(sizes) >= 4 and sizes[1] < 100:
                    total = sum(sizes)
                    sizes[1] = 200  # compare_frame
                    sizes[0] = max(100, total - 200 - sizes[2] - sizes[3])  # matches_scroll
                    self.main_splitter.setSizes(sizes)
            
            # Apply theme colors to compare boxes (needed because they might not apply when hidden)
            self._apply_compare_box_theme()
            
            # Update labels for MT/LLM
            provider_name = match.metadata.get('provider', match.match_type)
            self.compare_source_container.hide()  # Hide source box for MT/LLM (source = current)
            self.compare_target_label.setText(f"{match.match_type} Translation ({provider_name}):")
            
            # Set target text
            self.compare_tm_target.setText(match.target)
            
        elif match.match_type == "Termbase":
            # Show termbase data viewer
            print("📖 DEBUG: Showing termbase viewer!")
            self.compare_frame.hide()
            self.termbase_frame.show()
            self._display_termbase_data(match)
        else:
            # Hide all viewers
            print(f"❌ DEBUG: Match type '{match.match_type}' - hiding all viewers")
            self.compare_frame.hide()
            self.termbase_frame.hide()
    
    def set_segment_info(self, segment_num: int, source_text: str):
        """Update segment info display"""
        self.segment_label.setText(f"Segment {segment_num}: {source_text[:50]}...")
        self.compare_current.setText(source_text)

    def _show_compare_box_context_menu(self, pos):
        """Context menu for the TM Source / TM Target compare boxes.

        Augments the standard QTextEdit context menu with Edit / Delete actions
        when the currently selected match is a TM entry.
        """
        from PyQt6.QtWidgets import QMenu
        from PyQt6.QtGui import QAction

        sender = self.sender()  # the QTextEdit that was right-clicked

        # Build the standard copy/select-all menu first
        menu = sender.createStandardContextMenu()

        match = self.current_selection
        if match is None or match.match_type != "TM":
            menu.exec(sender.mapToGlobal(pos))
            return

        tm_id = match.metadata.get('tm_id', '')
        tm_name = match.metadata.get('tm_name', tm_id or 'TM')

        if tm_id:
            menu.addSeparator()
            edit_action = QAction(f"✏️ Edit TM Entry ({tm_name})", menu)
            edit_action.triggered.connect(self._edit_current_tm_entry)
            menu.addAction(edit_action)

            delete_action = QAction(f"🗑️ Delete TM Entry ({tm_name})", menu)
            delete_action.triggered.connect(self._delete_current_tm_entry)
            menu.addAction(delete_action)

        menu.exec(sender.mapToGlobal(pos))

    def _edit_current_tm_entry(self):
        """Edit the TM entry currently shown in the compare boxes."""
        match = self.current_selection
        if match is None or match.match_type != "TM":
            return

        from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QLabel,
                                     QTextEdit, QDialogButtonBox, QMessageBox)

        tm_id = match.metadata.get('tm_id', '')
        tm_name = match.metadata.get('tm_name', tm_id or 'TM')
        old_source = match.source
        old_target = match.target

        if not tm_id:
            QMessageBox.warning(self.window(), "Cannot Edit",
                                "TM identifier is not available for this entry.")
            return

        parent_window = self.window()

        dialog = QDialog(parent_window)
        dialog.setWindowTitle(f"Edit TM Entry – {tm_name}")
        dialog.setMinimumWidth(520)
        layout = QVBoxLayout(dialog)
        layout.setSpacing(8)

        layout.addWidget(QLabel("Source (read-only):"))
        source_edit = QTextEdit()
        source_edit.setPlainText(old_source)
        source_edit.setReadOnly(True)
        source_edit.setMaximumHeight(90)
        layout.addWidget(source_edit)

        layout.addWidget(QLabel("Target:"))
        target_edit = QTextEdit()
        target_edit.setPlainText(old_target)
        target_edit.setMaximumHeight(120)
        layout.addWidget(target_edit)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        new_target = target_edit.toPlainText()
        if new_target == old_target:
            return

        tm_manager = getattr(parent_window, 'tm_manager', None)
        db_manager = getattr(parent_window, 'db_manager', None)

        updated = False
        try:
            if tm_manager and hasattr(tm_manager, 'update_entry'):
                updated = tm_manager.update_entry(tm_id, old_source, old_target,
                                                  old_source, new_target)
            elif db_manager and hasattr(db_manager, 'update_entry'):
                updated = db_manager.update_entry(tm_id, old_source, old_target,
                                                  old_source, new_target)
        except Exception as e:
            QMessageBox.critical(parent_window, "Error", f"Failed to update TM entry:\n{e}")
            return

        if updated:
            if hasattr(parent_window, 'log'):
                parent_window.log(f"✏️ Updated TM entry in '{tm_name}'")
            # Update the live match object and compare box so the change is visible immediately
            match.target = new_target
            self.compare_tm_target.setPlainText(new_target)
            if hasattr(parent_window, '_last_selected_row'):
                parent_window._last_selected_row = None
        else:
            QMessageBox.warning(parent_window, "Not Found",
                                "The entry could not be found in the database.\n"
                                "It may have already been changed or deleted.")

    def _delete_current_tm_entry(self):
        """Delete the TM entry currently shown in the compare boxes."""
        match = self.current_selection
        if match is None or match.match_type != "TM":
            return

        from PyQt6.QtWidgets import QMessageBox

        tm_id = match.metadata.get('tm_id', '')
        tm_name = match.metadata.get('tm_name', tm_id or 'TM')
        source_text = match.source
        target_text = match.target

        if not tm_id:
            QMessageBox.warning(self.window(), "Cannot Delete",
                                "TM identifier is not available for this entry.")
            return

        parent_window = self.window()

        reply = QMessageBox.question(
            parent_window,
            "Confirm Deletion",
            f"Delete TM entry from '{tm_name}'?\n\n"
            f"Source: {source_text[:120]}\n"
            f"Target: {target_text[:120]}\n\n"
            "This action cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        tm_manager = getattr(parent_window, 'tm_manager', None)
        db_manager = getattr(parent_window, 'db_manager', None)

        try:
            if tm_manager and hasattr(tm_manager, 'delete_entry'):
                tm_manager.delete_entry(tm_id, source_text, target_text)
            elif db_manager and hasattr(db_manager, 'delete_entry'):
                db_manager.delete_entry(tm_id, source_text, target_text)
            else:
                QMessageBox.critical(parent_window, "Error",
                                     "No TM manager available to perform deletion.")
                return
        except Exception as e:
            QMessageBox.critical(parent_window, "Error", f"Failed to delete TM entry:\n{e}")
            return

        if hasattr(parent_window, 'log'):
            parent_window.log(f"🗑️ Deleted TM entry from '{tm_name}'")

        if hasattr(parent_window, '_last_selected_row'):
            parent_window._last_selected_row = None

        # Hide the compare frame since this entry no longer exists
        self.compare_frame.hide()
        self.current_selection = None

    def _apply_diff_highlighting(self, current_source: str, tm_source: str):
        """
        Apply diff highlighting between current source and TM source.
        Shows differences memoQ-style in the TM Source box:
        - Red strikethrough for text in TM that's not in current segment (will need to be removed)
        - Red underline for text in current that's not in TM (translator needs to add this)

        The Current Source box shows the plain text without highlighting.
        This helps translators quickly see what changed between the fuzzy match and the current segment.
        """
        import re

        def tokenize(text):
            """Split text into tokens, preserving newlines as separate '\n' tokens."""
            # Split on whitespace, keeping newlines as distinct tokens
            tokens = []
            for part in re.split(r'(\n)', text):
                if part == '\n':
                    tokens.append('\n')
                else:
                    tokens.extend(part.split())
            return tokens

        def insert_tokens(cursor, tokens, fmt, normal_fmt, is_first):
            """Insert a list of tokens into cursor with the given format, handling newlines."""
            for tok in tokens:
                if tok == '\n':
                    cursor.insertBlock()
                    is_first = True  # no leading space after a newline
                else:
                    if not is_first:
                        cursor.insertText(' ', normal_fmt)
                    cursor.insertText(tok, fmt)
                    is_first = False
            return is_first

        # Use difflib's SequenceMatcher to find differences at word level
        current_words = tokenize(current_source)
        tm_words = tokenize(tm_source)

        # Get opcodes that describe how to transform tm_source into current_source
        matcher = difflib.SequenceMatcher(None, tm_words, current_words)
        opcodes = matcher.get_opcodes()

        # Define formatting styles
        # Red strikethrough for deletions (text in TM but not in current)
        delete_format = QTextCharFormat()
        delete_format.setForeground(QColor("#CC0000"))  # Red text
        delete_format.setFontStrikeOut(True)

        # Red underline for additions (text in current but not in TM)
        insert_format = QTextCharFormat()
        insert_format.setForeground(QColor("#CC0000"))  # Red text
        insert_format.setFontUnderline(True)

        # Normal format (for unchanged text)
        normal_format = QTextCharFormat()
        if self.theme_manager:
            normal_format.setForeground(QColor(self.theme_manager.current_theme.text))
        else:
            normal_format.setForeground(QColor("#333333"))

        # Current Source box: just show plain text (already set by set_segment_info, but reset to ensure no formatting)
        self.compare_current.setText(current_source)

        # TM Source box: show with diff highlighting
        # Red strikethrough = text in TM but not in current (needs to be removed/changed)
        # Red underline = text in current but not in TM (needs to be added to translation)
        self.compare_tm_source.clear()
        tm_cursor = self.compare_tm_source.textCursor()

        first_word = True
        for tag, i1, i2, j1, j2 in opcodes:
            if tag == 'equal':
                first_word = insert_tokens(tm_cursor, tm_words[i1:i2], normal_format, normal_format, first_word)
            elif tag == 'replace':
                first_word = insert_tokens(tm_cursor, tm_words[i1:i2], delete_format, normal_format, first_word)
                first_word = insert_tokens(tm_cursor, current_words[j1:j2], insert_format, normal_format, first_word)
            elif tag == 'delete':
                first_word = insert_tokens(tm_cursor, tm_words[i1:i2], delete_format, normal_format, first_word)
            elif tag == 'insert':
                first_word = insert_tokens(tm_cursor, current_words[j1:j2], insert_format, normal_format, first_word)
    
    def clear(self):
        """Clear all matches (but NOT notes - those are managed separately)"""
        self.matches_by_type = {}
        self.current_selection = None
        self.all_matches = []
        self.compare_frame.hide()
        self.termbase_frame.hide()
        # NOTE: Do NOT clear notes_edit here - notes are loaded/saved separately
        # and clear() is called multiple times during segment navigation
        
        while self.main_layout.count() > 0:
            item = self.main_layout.takeAt(0)
            if item and item.widget():
                item.widget().deleteLater()
    
    def get_selected_match(self) -> Optional[TranslationMatch]:
        """Get currently selected match"""
        return self.current_selection
    
    def set_font_size(self, size: int):
        """Set font size for all match items (for zoom control)"""
        CompactMatchItem.set_font_size(size)
        # Update all currently displayed items
        for item in self.match_items:
            item.update_font_size()
            item.adjustSize()
        self.matches_scroll.update()
    
    def set_compare_box_font_size(self, size: int):
        """Set font size for compare boxes"""
        TranslationResultsPanel.compare_box_font_size = size
        for text_edit in self.compare_text_edits:
            text_edit.setStyleSheet(f"""
                QTextEdit {{
                    background-color: {self._get_box_color(text_edit)};
                    border: 1px solid #ccc;
                    border-radius: 2px;
                    font-size: {size}px;
                    padding: 4px;
                    margin: 0px;
                }}
            """)
    
    def set_show_tags(self, show: bool):
        """Set whether to show HTML/XML tags in matches"""
        CompactMatchItem.show_tags = show
        # Refresh all match items
        for item in self.match_items:
            if hasattr(item, 'source_label') and hasattr(item, 'target_label'):
                # Always use RichText (needed for both tag rendering and highlighting)
                item.source_label.setTextFormat(Qt.TextFormat.RichText)
                item.target_label.setTextFormat(Qt.TextFormat.RichText)
                # Refresh text
                item.source_label.setText(item._format_text(item.match.source))
                item.target_label.setText(item._format_text(item.match.target))
    
    def set_tag_color(self, color: str):
        """Set tag highlight color for all match items"""
        CompactMatchItem.tag_highlight_color = color
        # Refresh all match items to apply new color
        for item in self.match_items:
            if hasattr(item, 'update_tag_color'):
                item.update_tag_color(color)
    
    def _get_box_color(self, text_edit) -> str:
        """Get background color for a compare box (mapping hack)"""
        # This is a workaround - in production, store colors with the widgets
        colors = ["#e3f2fd", "#fff3cd", "#d4edda"]
        if text_edit in self.compare_text_edits:
            return colors[self.compare_text_edits.index(text_edit) % len(colors)]
        return "#fafafa"
    
    def zoom_in(self):
        """Increase font size for both match list and compare boxes"""
        new_size = CompactMatchItem.font_size_pt + 1
        if new_size <= 16:  # Max 16pt
            self.set_font_size(new_size)
            # Also increase compare boxes
            compare_size = TranslationResultsPanel.compare_box_font_size + 1
            if compare_size <= 14:
                self.set_compare_box_font_size(compare_size)
            return new_size
        return CompactMatchItem.font_size_pt
    
    def zoom_out(self):
        """Decrease font size for both match list and compare boxes"""
        new_size = CompactMatchItem.font_size_pt - 1
        if new_size >= 7:  # Min 7pt
            self.set_font_size(new_size)
            # Also decrease compare boxes
            compare_size = TranslationResultsPanel.compare_box_font_size - 1
            if compare_size >= 7:
                self.set_compare_box_font_size(compare_size)
            return new_size
        return CompactMatchItem.font_size_pt
    
    def reset_zoom(self):
        """Reset font size to defaults"""
        self.set_font_size(9)
        self.set_compare_box_font_size(9)
        return 9
    
    def select_previous_match(self):
        """Navigate to previous match (Ctrl+Up from main window)"""
        if self.selected_index > 0:
            new_index = self.selected_index - 1
            self._on_match_item_selected(self.all_matches[new_index], new_index)
            # Scroll to make it visible
            if 0 <= new_index < len(self.match_items):
                self.matches_scroll.ensureWidgetVisible(self.match_items[new_index])
        elif self.selected_index == -1 and self.all_matches:
            # No selection, select last match
            new_index = len(self.all_matches) - 1
            self._on_match_item_selected(self.all_matches[new_index], new_index)
            if 0 <= new_index < len(self.match_items):
                self.matches_scroll.ensureWidgetVisible(self.match_items[new_index])
    
    def select_next_match(self):
        """Navigate to next match (Ctrl+Down from main window)"""
        if self.selected_index < len(self.all_matches) - 1:
            new_index = self.selected_index + 1
            self._on_match_item_selected(self.all_matches[new_index], new_index)
            # Scroll to make it visible
            if 0 <= new_index < len(self.match_items):
                self.matches_scroll.ensureWidgetVisible(self.match_items[new_index])
        elif self.selected_index == -1 and self.all_matches:
            # No selection, select first match
            new_index = 0
            self._on_match_item_selected(self.all_matches[new_index], new_index)
            if 0 <= new_index < len(self.match_items):
                self.matches_scroll.ensureWidgetVisible(self.match_items[new_index])
    
    def insert_match_by_number(self, match_number: int):
        """Insert match by its number (1-based index) - for Ctrl+1-9 shortcuts"""
        if 0 < match_number <= len(self.all_matches):
            match = self.all_matches[match_number - 1]
            # Select it visually
            self._on_match_item_selected(match, match_number - 1)
            # Scroll to it
            if 0 <= match_number - 1 < len(self.match_items):
                self.matches_scroll.ensureWidgetVisible(self.match_items[match_number - 1])
            # Emit insert signal
            self.match_inserted.emit(match.target)
            return True
        return False
    
    def insert_selected_match(self):
        """Insert currently selected match (Ctrl+Space)"""
        if self.current_selection:
            self.match_inserted.emit(self.current_selection.target)
            return True
        return False
    
    def keyPressEvent(self, event):
        """
        Handle keyboard events for navigation and insertion
        
        Shortcuts:
        - Up/Down arrows: Navigate matches (plain arrows, no Ctrl)
        - Spacebar: Insert selected match into target
        - Return/Enter: Insert selected match into target
        - Ctrl+Space: Insert selected match (alternative)
        - Ctrl+1 to Ctrl+9: Insert specific match by number (global)
        
        Note: Ctrl+Up/Down are handled at main window level for grid navigation
        """
        # Ctrl+Space: Insert currently selected match
        if (event.modifiers() & Qt.KeyboardModifier.ControlModifier and 
            event.key() == Qt.Key.Key_Space):
            if self.insert_selected_match():
                event.accept()
                return
        
        # Ctrl+1 through Ctrl+9: Insert match by number
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            if event.key() >= Qt.Key.Key_1 and event.key() <= Qt.Key.Key_9:
                match_num = event.key() - Qt.Key.Key_0  # Convert key to number
                if self.insert_match_by_number(match_num):
                    event.accept()
                    return
        
        # Up/Down arrows: Navigate matches (plain arrows only, NOT Ctrl+Up/Down)
        if event.key() == Qt.Key.Key_Up:
            if not (event.modifiers() & Qt.KeyboardModifier.ControlModifier):
                self.select_previous_match()
                event.accept()
                return
        elif event.key() == Qt.Key.Key_Down:
            if not (event.modifiers() & Qt.KeyboardModifier.ControlModifier):
                self.select_next_match()
                event.accept()
                return
        
        # Spacebar or Return/Enter: Insert selected match
        elif event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            if self.current_selection:
                self.match_inserted.emit(self.current_selection.target)
                event.accept()
                return
        
        super().keyPressEvent(event)

