"""
Keyboard Shortcut Manager for Supervertaler Qt
Centralized management of all keyboard shortcuts
"""

import json
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from PyQt6.QtGui import QKeySequence
from PyQt6.QtCore import QSettings
from modules.shortcut_display import format_shortcut_for_display, format_shortcuts_in_text

class ShortcutManager:
    """Manages all keyboard shortcuts for Supervertaler"""
    
    # Define all shortcuts with their categories, descriptions, and defaults
    DEFAULT_SHORTCUTS = {
        # File Operations
        "file_new": {
            "category": "File",
            "description": "New Project",
            "default": "",
            "action": "new_project"
        },
        "file_open": {
            "category": "File",
            "description": "Open Project",
            "default": "Ctrl+O",
            "action": "open_project"
        },
        "file_save": {
            "category": "File",
            "description": "Save Project",
            "default": "Ctrl+S",
            "action": "save_project"
        },

        "file_quit": {
            "category": "File",
            "description": "Quit Application",
            "default": "Alt+F4",
            "action": "close"
        },
        
        # Edit Operations
        "edit_undo": {
            "category": "Edit",
            "description": "Undo",
            "default": "Ctrl+Z",
            "action": "undo"
        },
        "edit_redo": {
            "category": "Edit",
            "description": "Redo",
            "default": "Ctrl+Y",
            "action": "redo"
        },
        "edit_find": {
            "category": "Edit",
            "description": "Find",
            "default": "Ctrl+F",
            "action": "show_find_replace_dialog"
        },
        "edit_replace": {
            "category": "Edit",
            "description": "Replace",
            "default": "Ctrl+H",
            "action": "show_find_replace_dialog"
        },
        "edit_goto": {
            "category": "Edit",
            "description": "Go to Segment",
            "default": "Ctrl+G",
            "action": "show_goto_dialog"
        },
        "editor_add_comment": {
            "category": "Editor",
            "description": "Add Comment from Selection",
            "default": "Ctrl+M",
            "action": "add_comment_from_selection"
        },
        "editor_split_segment": {
            "category": "Editor",
            "description": "Split Segment at Cursor (in Source)",
            "default": "Ctrl+Alt+S",
            "action": "split_current_segment"
        },
        "editor_merge_segment": {
            "category": "Editor",
            "description": "Merge Segment with Next",
            "default": "Ctrl+Alt+M",
            "action": "merge_current_segment"
        },

        # Translation Operations
        "translate_current": {
            "category": "Translation",
            "description": "Translate Current Segment",
            "default": "Ctrl+T",
            "action": "translate_current_segment"
        },
        "translate_batch": {
            "category": "Translation",
            "description": "Translate Multiple Segments",
            "default": "Ctrl+Shift+T",
            "action": "translate_batch"
        },
        
        # View Operations
        # NB: the old "Switch to Grid/List/Document View" entries (Ctrl+1/2/3,
        # actions switch_to_*_view) were removed in v1.10.109 — those methods
        # no longer exist (Workbench has a single grid view), so the entries
        # were dead: they showed in the shortcut list but did nothing.
        "view_toggle_tags": {
            # Default moved Ctrl+Alt+T → Ctrl+Shift+H in v1.9.476 to free
            # Ctrl+Alt+T for "Add term to glossary" (which matches the
            # Trados plugin shortcut). Ctrl+Shift+H is Trados Studio's own
            # default for the analogous "Show formatting characters / Tag
            # display" toggle, so the rename keeps Workbench muscle memory
            # aligned with Trados rather than introducing a new convention.
            "category": "View",
            "description": "Toggle Tag View",
            "default": "Ctrl+Shift+H",
            "action": "toggle_tag_view"
        },
        "toggle_preview": {
            "category": "View",
            "description": "Toggle Document Preview panel",
            "default": "Ctrl+Alt+P",
            "action": "toggle_preview_panel"
        },

        # Grid Text Zoom
        "grid_zoom_in": {
            "category": "View",
            "description": "Grid Zoom In",
            "default": "Ctrl++",
            "action": "increase_font_size"
        },
        "grid_zoom_out": {
            "category": "View",
            "description": "Grid Zoom Out",
            "default": "Ctrl+-",
            "action": "decrease_font_size"
        },
        
        # Results Pane Zoom
        "results_zoom_in": {
            "category": "View",
            "description": "Results Pane Zoom In",
            "default": "Ctrl+Shift+=",
            "action": "results_pane_zoom_in"
        },
        "results_zoom_out": {
            "category": "View",
            "description": "Results Pane Zoom Out",
            "default": "Ctrl+Shift+-",
            "action": "results_pane_zoom_out"
        },
        
        # Resources & Tools
        "tools_tm_manager_window": {
            "category": "Resources",
            "description": "TM Manager (Separate window)",
            "default": "Ctrl+Shift+M",
            "action": "show_tm_manager"
        },
        "tools_concordance_search": {
            "category": "Resources",
            "description": "Quick Concordance Search",
            "default": "Ctrl+K",
            "action": "show_concordance_search"
        },
        "tools_universal_lookup": {
            "category": "Resources",
            "description": "Superlookup",
            "default": "Ctrl+Alt+L",
            "action": "show_universal_lookup",
            "global": True,
        },
        "tools_force_refresh": {
            "category": "Resources",
            "description": "Force Refresh Matches (clear cache)",
            "default": "F5",
            "action": "force_refresh_matches"
        },

        # Special
        "voice_dictate": {
            "category": "Special",
            "description": "Voice dictation / push-to-talk",
            "default": "Ctrl+Shift+Space",
            "action": "start_voice_dictation",
            "global": True,
        },
        # v1.10.193: push-to-talk for voice COMMANDS (next segment, add
        # term, etc.) so users can pair Supervertaler's command listener
        # with an external dictation app (Wispr Flow, etc.) without the
        # always-on mic open all the time. Holding the chord temporarily
        # starts the ContinuousVoiceListener; releasing stops it. Same
        # code path as the Ctrl+Alt+O toggle, just gated by a hold.
        "voice_command_ptt": {
            "category": "Special",
            "description": "Voice commands push-to-talk (hold to listen for commands)",
            "default": "Ctrl+Alt+V",
            "action": "voice_command_ptt",
            "global": True,
        },

        # Match Insertion (Direct)
        "match_insert_1": {
            "category": "Match Insertion",
            "description": "Insert Match #1",
            "default": "",
            "action": "insert_match_1",
            "context": "editor"
        },
        "match_insert_2": {
            "category": "Match Insertion",
            "description": "Insert Match #2",
            "default": "",
            "action": "insert_match_2",
            "context": "editor"
        },
        "match_insert_3": {
            "category": "Match Insertion",
            "description": "Insert Match #3",
            "default": "",
            "action": "insert_match_3",
            "context": "editor"
        },
        "match_insert_4": {
            "category": "Match Insertion",
            "description": "Insert Match #4",
            "default": "",
            "action": "insert_match_4",
            "context": "editor"
        },
        "match_insert_5": {
            "category": "Match Insertion",
            "description": "Insert Match #5",
            "default": "",
            "action": "insert_match_5",
            "context": "editor"
        },
        "match_insert_6": {
            "category": "Match Insertion",
            "description": "Insert Match #6",
            "default": "",
            "action": "insert_match_6",
            "context": "editor"
        },
        "match_insert_7": {
            "category": "Match Insertion",
            "description": "Insert Match #7",
            "default": "",
            "action": "insert_match_7",
            "context": "editor"
        },
        "match_insert_8": {
            "category": "Match Insertion",
            "description": "Insert Match #8",
            "default": "",
            "action": "insert_match_8",
            "context": "editor"
        },
        "match_insert_9": {
            "category": "Match Insertion",
            "description": "Insert Match #9",
            "default": "",
            "action": "insert_match_9",
            "context": "editor"
        },

        # Compare Panel Insertion
        "compare_insert_alt0": {
            "category": "Compare Panel",
            "description": "Insert Compare Panel MT (Alt+0) / TM Target (Alt+0,0)",
            "default": "Alt+0",
            "action": "insert_compare_panel_alt0",
            "context": "editor"
        },

        # Compare Panel Navigation
        "compare_nav_mt_prev": {
            "category": "Compare Panel",
            "description": "Compare Panel: Previous MT result",
            "default": "Ctrl+Alt+Left",
            "action": "compare_panel_nav_mt_prev",
            "context": "editor"
        },
        "compare_nav_mt_next": {
            "category": "Compare Panel",
            "description": "Compare Panel: Next MT result",
            "default": "Ctrl+Alt+Right",
            "action": "compare_panel_nav_mt_next",
            "context": "editor"
        },
        "compare_nav_tm_prev": {
            "category": "Compare Panel",
            "description": "Compare Panel: Previous TM match",
            "default": "Ctrl+Alt+Up",
            "action": "compare_panel_nav_tm_prev",
            "context": "editor"
        },
        "compare_nav_tm_next": {
            "category": "Compare Panel",
            "description": "Compare Panel: Next TM match",
            "default": "Ctrl+Alt+Down",
            "action": "compare_panel_nav_tm_next",
            "context": "editor"
        },
        
        # TermLens Insertion (Alt+0-9, double-tap for 00-99)
        "termlens_insert_0": {
            "category": "TermLens Insertion",
            "description": "Insert TermLens Term [0] (or [00] if double-tap)",
            "default": "",
            "action": "insert_termlens_0",
            "context": "editor"
        },
        "termlens_insert_1": {
            "category": "TermLens Insertion",
            "description": "Insert TermLens Term [1] (or [11] if double-tap)",
            "default": "Alt+1",
            "action": "insert_termlens_1",
            "context": "editor"
        },
        "termlens_insert_2": {
            "category": "TermLens Insertion",
            "description": "Insert TermLens Term [2] (or [22] if double-tap)",
            "default": "Alt+2",
            "action": "insert_termlens_2",
            "context": "editor"
        },
        "termlens_insert_3": {
            "category": "TermLens Insertion",
            "description": "Insert TermLens Term [3] (or [33] if double-tap)",
            "default": "Alt+3",
            "action": "insert_termlens_3",
            "context": "editor"
        },
        "termlens_insert_4": {
            "category": "TermLens Insertion",
            "description": "Insert TermLens Term [4] (or [44] if double-tap)",
            "default": "Alt+4",
            "action": "insert_termlens_4",
            "context": "editor"
        },
        "termlens_insert_5": {
            "category": "TermLens Insertion",
            "description": "Insert TermLens Term [5] (or [55] if double-tap)",
            "default": "Alt+5",
            "action": "insert_termlens_5",
            "context": "editor"
        },
        "termlens_insert_6": {
            "category": "TermLens Insertion",
            "description": "Insert TermLens Term [6] (or [66] if double-tap)",
            "default": "Alt+6",
            "action": "insert_termlens_6",
            "context": "editor"
        },
        "termlens_insert_7": {
            "category": "TermLens Insertion",
            "description": "Insert TermLens Term [7] (or [77] if double-tap)",
            "default": "Alt+7",
            "action": "insert_termlens_7",
            "context": "editor"
        },
        "termlens_insert_8": {
            "category": "TermLens Insertion",
            "description": "Insert TermLens Term [8] (or [88] if double-tap)",
            "default": "Alt+8",
            "action": "insert_termlens_8",
            "context": "editor"
        },
        "termlens_insert_9": {
            "category": "TermLens Insertion",
            "description": "Insert TermLens Term [9] (or [99] if double-tap)",
            "default": "Alt+9",
            "action": "insert_termlens_9",
            "context": "editor"
        },
        
        # Match Navigation
        "match_next": {
            "category": "Match Navigation",
            "description": "Next Match (in results panel)",
            "default": "Down",
            "action": "next_match",
            "context": "match_panel"
        },
        "match_previous": {
            "category": "Match Navigation",
            "description": "Previous Match (in results panel)",
            "default": "Up",
            "action": "previous_match",
            "context": "match_panel"
        },
        "match_cycle_next": {
            "category": "Match Navigation",
            "description": "Cycle to Next Match (from grid) [legacy]",
            "default": "",
            "action": "select_next_match",
            "context": "grid",
            # Hidden: legacy slot with no key binding path — select_next_match
            # is never wired to a shortcut, so this can't be invoked or rebound.
            "hidden": True,
        },
        "match_cycle_previous": {
            "category": "Match Navigation",
            "description": "Cycle to Previous Match (from grid) [legacy]",
            "default": "",
            "action": "select_previous_match",
            "context": "grid",
            "hidden": True,  # see match_cycle_next
        },
        "match_insert_selected": {
            "category": "Match Navigation",
            "description": "Insert Selected Match",
            "default": "Space or Enter",
            "action": "insert_selected_match",
            "context": "match_panel"
        },
        "match_insert_selected_ctrl": {
            "category": "Match Navigation",
            "description": "Insert Selected Match (from grid)",
            "default": "Ctrl+Space",
            "action": "insert_selected_match",
            "context": "grid"
        },
        
        # Grid Navigation
        "segment_next": {
            "category": "Grid Navigation",
            "description": "Next Segment",
            "default": "Ctrl+Down",
            "action": "go_to_next_segment"
        },
        "segment_previous": {
            "category": "Grid Navigation",
            "description": "Previous Segment",
            "default": "Ctrl+Up",
            "action": "go_to_previous_segment"
        },
        "segment_go_to_top": {
            "category": "Grid Navigation",
            "description": "Go to First Segment",
            "default": "Ctrl+Home",
            "action": "go_to_first_segment"
        },
        "segment_go_to_bottom": {
            "category": "Grid Navigation",
            "description": "Go to Last Segment",
            "default": "Ctrl+End",
            "action": "go_to_last_segment"
        },
        "page_prev": {
            "category": "Grid Navigation",
            "description": "Previous Page (pagination)",
            "default": "PgUp",
            "action": "go_to_prev_page"
        },
        "page_next": {
            "category": "Grid Navigation",
            "description": "Next Page (pagination)",
            "default": "PgDown",
            "action": "go_to_next_page"
        },
        "select_range_up": {
            "category": "Grid Navigation",
            "description": "Select Range Upward (one page)",
            "default": "Shift+PgUp",
            "action": "select_range_page_up"
        },
        "select_range_down": {
            "category": "Grid Navigation",
            "description": "Select Range Downward (one page)",
            "default": "Shift+PgDown",
            "action": "select_range_page_down"
        },
        
        # Editor Operations
        "editor_save_and_next": {
            "category": "Editor",
            "description": "Save & Next Segment",
            "default": "Ctrl+Enter",
            "action": "save_and_next",
            "context": "editor"
        },
        "editor_confirm_selected": {
            "category": "Editor",
            "description": "Confirm All Selected Segments",
            "default": "Ctrl+Shift+Enter",
            "action": "confirm_selected_segments",
            "context": "editor"
        },
        "editor_line_break": {
            "category": "Editor",
            "description": "Insert Line Break",
            # v1.10.237: was wrongly "Ctrl+Enter" — that collided with
            # "Save & Next Segment" and showed two identical Ctrl+Enter rows in
            # the shortcuts list (reported by a user). The actual line-break
            # binding in the editor is Shift+Enter (handled in keyPressEvent),
            # so the listed default now matches reality.
            "default": "Shift+Enter",
            "action": "insert_line_break",
            "context": "editor_alt"
        },
        "editor_cycle_source_target": {
            "category": "Editor",
            "description": "Cycle between Source/Target cells",
            "default": "Tab",
            "action": "cycle_source_target",
            "context": "grid_editor"
        },
        "editor_insert_tab": {
            "category": "Editor",
            "description": "Insert Tab character",
            "default": "Ctrl+Tab",
            "action": "insert_tab",
            "context": "grid_editor"
        },
        "editor_add_to_termbase": {
            "category": "Editor",
            "description": "Add selected term pair to termbase (with dialogue)",
            "default": "Ctrl+Alt+T",
            "action": "add_to_termbase",
            "context": "grid_editor"
        },
        "editor_quick_add_priority_1": {
            "category": "Editor",
            "description": "Quick add term pair with Priority 1",
            "default": "Ctrl+Shift+1",
            "action": "quick_add_term_priority_1",
            "context": "grid_editor"
        },
        "editor_quick_add_priority_2": {
            "category": "Editor",
            "description": "Quick add term pair with Priority 2",
            "default": "Ctrl+Shift+2",
            "action": "quick_add_term_priority_2",
            "context": "grid_editor"
        },
        "editor_quick_add_to_glossary_priority_1": {
            "category": "Editor",
            "description": "Quick add term pair to Priority 1 termbase",
            "default": "Alt+Up",
            "action": "quick_add_to_glossary_priority_1",
            "context": "grid_editor"
        },
        "editor_quick_add_to_glossary_priority_2": {
            "category": "Editor",
            "description": "Quick add term pair to Priority 2 termbase",
            "default": "Alt+Down",
            "action": "quick_add_to_glossary_priority_2",
            "context": "grid_editor"
        },
        "editor_add_to_non_translatables": {
            "category": "Editor",
            "description": "Add selected text to non-translatables list",
            "default": "Ctrl+Alt+N",
            "action": "add_to_non_translatables",
            "context": "grid_editor"
        },
        "editor_insert_next_tag": {
            "category": "Editor",
            "description": "Insert next tag (memoQ/CafeTran) or wrap selection",
            "default": "Ctrl+,",
            "action": "insert_next_tag",
            "context": "grid_editor"
        },
        "editor_copy_source_to_target": {
            "category": "Editor",
            "description": "Copy source text to target",
            "default": "Ctrl+Shift+S",
            "action": "copy_source_to_target",
            "context": "grid_editor"
        },
        "editor_add_to_dictionary": {
            "category": "Editor",
            "description": "Add word at cursor to custom dictionary",
            "default": "Alt+D",
            "action": "add_word_to_dictionary",
            "context": "grid_editor"
        },
        "editor_open_quicklauncher": {
            "category": "Editor",
            "description": "Open QuickLauncher for AI prompt actions",
            "default": "Ctrl+Q",
            "action": "open_quicklauncher",
            "context": "grid_editor"
        },
        # Despite the legacy IDs (sidekick_open*, kept so existing
        # user customisations survive), these shortcuts now target
        # Workbench top tabs – Sidekick was retired in v1.10.4 and
        # the floating-assistant window no longer exists. The
        # `sidekick_open` entry is no longer a *global* hotkey
        # (Ctrl+Alt+K was unbound at the OS level in v1.10.4 to
        # free the chord for other apps); it survives only as an
        # in-app QShortcut (Alt+K) that opens the SuperLookup top
        # tab with the user's current selection seeded.
        "sidekick_open": {
            "category": "Hotkeys",
            "description": "Open SuperLookup (with current selection)",
            "default": "Alt+K",
            "action": "open_quicklauncher",
        },
        "sidekick_open_clipboard": {
            "category": "Hotkeys",
            "description": "Open Clipboard manager",
            "default": "Ctrl+Alt+C",
            "action": "open_clipboard_tab",
            "global": True,
        },
        # Ctrl+Alt+O ("always-On"), not Ctrl+Alt+A. This is an OS-level global
        # hotkey, so it fires whatever application is in front - and Supervertaler
        # for Trados uses Ctrl+Alt+A for "Add term with abbreviation". Many people
        # run both at once, so the two would have fought over every press.
        "voice_alwayson_toggle": {
            "category": "Special",
            "description": "Voice Always-On (toggle)",
            "default": "Ctrl+Alt+O",
            "action": "toggle_alwayson",
            "global": True,
        },
        "editor_show_context_menu_double_shift": {
            "category": "Editor",
            "description": "Show context menu (double-tap Shift)",
            "default": "",  # Requires AutoHotkey script: supervertaler_hotkeys.ahk
            "action": "show_context_menu_double_shift",
            "context": "grid_editor",
            "note": "Requires AutoHotkey. Run supervertaler_hotkeys.ahk for this feature."
        },
        
        # Filter Operations
        "filter_selected_text": {
            "category": "Filter",
            "description": "Filter on selected text / Clear filter (toggle)",
            "default": "Ctrl+Shift+F",
            "action": "filter_on_selected_text"
        },
        "clear_filter": {
            "category": "Filter",
            "description": "Clear filter (same as above - toggle behavior)",
            "default": "Ctrl+Shift+F",
            "action": "filter_on_selected_text",
            # Hidden: exact duplicate of filter_selected_text (same key + action,
            # which is the entry actually wired up). Shown twice was confusing.
            "hidden": True,
        },

        # QuickTrans (GT4T-style instant translation popup)
        "mt_quick_lookup": {
            "category": "Translation",
            "description": "QuickTrans (instant translation popup)",
            "default": "Ctrl+Alt+Q",
            "action": "show_mt_quick_popup",
            "global": True,
        },
    }
    
    def __init__(self, settings_file: Optional[Path] = None):
        """
        Initialize ShortcutManager
        
        Args:
            settings_file: Path to JSON file for storing custom shortcuts
        """
        self.settings_file = settings_file or Path("user_data/workbench/settings/shortcuts.json")
        self.custom_shortcuts = {}
        self.disabled_shortcuts = set()  # Set of disabled shortcut IDs
        # One-time default-value migrations already applied on this
        # install. Persisted so they fire at most once and never wipe a
        # user's deliberate re-selection of an old default value.
        self.applied_migrations = set()
        self.load_shortcuts()
    
    def load_shortcuts(self):
        """Load custom shortcuts from file"""
        if self.settings_file.exists():
            try:
                with open(self.settings_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    # Support both old format (dict of shortcuts) and new format (dict with shortcuts + disabled)
                    if isinstance(data, dict):
                        if "shortcuts" in data:
                            # New format: {"shortcuts": {...}, "disabled": [...]}
                            self.custom_shortcuts = data.get("shortcuts", {})
                            self.disabled_shortcuts = set(data.get("disabled", []))
                            self.applied_migrations = set(
                                data.get("applied_migrations", []))
                        else:
                            # Old format: just the shortcuts dict
                            self.custom_shortcuts = data
                            self.disabled_shortcuts = set()
                    else:
                        self.custom_shortcuts = {}
                        self.disabled_shortcuts = set()
            except Exception as e:
                print(f"Error loading shortcuts: {e}")
                self.custom_shortcuts = {}
                self.disabled_shortcuts = set()

        # Migrate termview_insert_* → termlens_insert_* (v1.9.347+)
        migrated = {}
        needs_save = False
        for key, val in self.custom_shortcuts.items():
            if key.startswith('termview_insert_'):
                migrated[key.replace('termview_insert_', 'termlens_insert_')] = val
                needs_save = True
            else:
                migrated[key] = val
        if needs_save:
            self.custom_shortcuts = migrated
            old_disabled = set()
            for d in self.disabled_shortcuts:
                if d.startswith('termview_insert_'):
                    old_disabled.add(d.replace('termview_insert_', 'termlens_insert_'))
                else:
                    old_disabled.add(d)
            self.disabled_shortcuts = old_disabled
            self.save_shortcuts()

        # Migrate autofingers_alwayson_toggle → voice_alwayson_toggle (v1.9.491+).
        # The internal feature was renamed from "AutoFingers" to "Voice".
        if 'autofingers_alwayson_toggle' in self.custom_shortcuts:
            if 'voice_alwayson_toggle' not in self.custom_shortcuts:
                self.custom_shortcuts['voice_alwayson_toggle'] = \
                    self.custom_shortcuts['autofingers_alwayson_toggle']
            del self.custom_shortcuts['autofingers_alwayson_toggle']
            self.save_shortcuts()
        if 'autofingers_alwayson_toggle' in self.disabled_shortcuts:
            self.disabled_shortcuts.discard('autofingers_alwayson_toggle')
            self.disabled_shortcuts.add('voice_alwayson_toggle')
            self.save_shortcuts()

        # Migrate quickmenu → quicklauncher → sidekick shortcut IDs.
        # The global hotkey was renamed to "sidekick" to match the user-facing
        # feature name; the editor-only QuickLauncher kept its name.
        _QM_RENAMES = {
            'editor_open_quickmenu': 'editor_open_quicklauncher',
            'global_quickmenu': 'global_sidekick',
            'global_quicklauncher': 'global_sidekick',
        }
        qm_migrated = {}
        qm_needs_save = False
        for key, val in self.custom_shortcuts.items():
            new_key = _QM_RENAMES.get(key, key)
            if new_key != key:
                qm_needs_save = True
            qm_migrated[new_key] = val
        if qm_needs_save:
            self.custom_shortcuts = qm_migrated
            new_disabled = set()
            for d in self.disabled_shortcuts:
                new_disabled.add(_QM_RENAMES.get(d, d))
            self.disabled_shortcuts = new_disabled
            self.save_shortcuts()

        # Default-value upgrade for global_sidekick: Ctrl+Shift+A → Alt+K.
        # Users on the previous default had it persisted as a "custom" entry
        # (matching the old default). Drop that entry so the new default
        # takes effect; explicit user overrides to anything other than the
        # old default are preserved.
        sk = self.custom_shortcuts.get('global_sidekick')
        if sk and sk.lower() == 'ctrl+shift+a':
            del self.custom_shortcuts['global_sidekick']
            self.save_shortcuts()

        # Default-value upgrade for voice_alwayson_toggle: Ctrl+Alt+A → Ctrl+Alt+O.
        # Ctrl+Alt+A is registered as an OS-level global hotkey, so it fired no
        # matter which application was in front - including Trados Studio, where
        # Supervertaler for Trados now uses Ctrl+Alt+A for "Add term with
        # abbreviation". Without this, anyone whose binding had been persisted at
        # the old default would go on colliding, which is precisely the case the
        # move exists to fix. An override to anything else is left alone.
        av = self.custom_shortcuts.get('voice_alwayson_toggle')
        if av and av.lower() == 'ctrl+alt+a':
            del self.custom_shortcuts['voice_alwayson_toggle']
            self.save_shortcuts()

        # Merge the old `global_*` entries into the corresponding action
        # entries marked `global: True`. There used to be one local and one
        # global shortcut per action; users now manage one entry that
        # registers both the OS-level global hotkey and the in-app
        # QShortcut. If the user customised the old global ID, transfer it
        # to the merged ID (unless the merged ID already has its own
        # customisation, which we don't want to clobber).
        _GLOBAL_TO_MERGED = {
            'global_superlookup':       'tools_universal_lookup',
            'global_quicktrans':        'mt_quick_lookup',
            'global_sidekick':          'sidekick_open',
            'global_clipboard':         'sidekick_open_clipboard',
            'global_pushtotalk':        'voice_dictate',
            'global_alwayson_toggle':   'voice_alwayson_toggle',
        }
        merge_changed = False
        for old_id, new_id in _GLOBAL_TO_MERGED.items():
            if old_id in self.custom_shortcuts:
                if new_id not in self.custom_shortcuts:
                    self.custom_shortcuts[new_id] = self.custom_shortcuts[old_id]
                del self.custom_shortcuts[old_id]
                merge_changed = True
            if old_id in self.disabled_shortcuts:
                # Old global was disabled. Don't propagate the disable to
                # the merged entry – the user may still want the in-app
                # shortcut, and they can disable explicitly if they want
                # neither.
                self.disabled_shortcuts.discard(old_id)
                merge_changed = True
        if merge_changed:
            self.save_shortcuts()

        # v1.10.10: the previous `sidekick_open` default-value upgrade
        # (Alt+K → Ctrl+Alt+K to dodge the Mac ⌥K dead-key issue) has
        # been removed. With Sidekick retired the global Ctrl+Alt+K
        # binding is gone; sidekick_open is now an in-app-only
        # shortcut and Alt+K is fine again.

        # Default-value upgrade for sidekick_open_clipboard: Ctrl+Shift+C
        # → Ctrl+Alt+C. The old default fired unreliably on Windows
        # (Ctrl+Shift+C is widely claimed by browsers / DevTools and
        # other apps), so users who were on the previous default are
        # bumped to Ctrl+Alt+C.
        #
        # This is a ONE-TIME migration and must be gated: it can't tell a
        # legacy user still on the old default from one who has just
        # *deliberately* re-selected Ctrl+Shift+C. Running it on every
        # load wiped that deliberate choice on the next restart (the
        # binding silently snapped back to Ctrl+Alt+C). Gate it behind a
        # persisted marker so it fires at most once per install; after
        # that Ctrl+Shift+C is a freely selectable binding again.
        _CLIP_MIGRATION = 'clipboard_default_ctrlaltc_v1'
        if _CLIP_MIGRATION not in self.applied_migrations:
            sk_clip = self.custom_shortcuts.get('sidekick_open_clipboard')
            if sk_clip and sk_clip.lower() == 'ctrl+shift+c':
                del self.custom_shortcuts['sidekick_open_clipboard']
            self.applied_migrations.add(_CLIP_MIGRATION)
            self.save_shortcuts()

    def save_shortcuts(self):
        """Save custom shortcuts to file"""
        try:
            self.settings_file.parent.mkdir(parents=True, exist_ok=True)
            # Save in new format that includes both shortcuts and disabled list
            save_data = {
                "shortcuts": self.custom_shortcuts,
                "disabled": list(self.disabled_shortcuts),
                "applied_migrations": list(self.applied_migrations),
            }
            with open(self.settings_file, 'w', encoding='utf-8') as f:
                json.dump(save_data, f, indent=2)
        except Exception as e:
            print(f"Error saving shortcuts: {e}")
    
    def get_shortcut(self, shortcut_id: str) -> str:
        """
        Get the current shortcut for a given ID
        
        Args:
            shortcut_id: The shortcut identifier
            
        Returns:
            The key sequence string (e.g., "Ctrl+T")
        """
        # Backward compat: accept old shortcut IDs
        _LEGACY_IDS = {
            'editor_open_quickmenu': 'editor_open_quicklauncher',
            'global_quickmenu': 'sidekick_open',
            'global_quicklauncher': 'sidekick_open',
            # Merged: action-pair IDs replaced by single entry with `global: True`
            'global_superlookup':     'tools_universal_lookup',
            'global_quicktrans':      'mt_quick_lookup',
            'global_sidekick':        'sidekick_open',
            'global_clipboard':       'sidekick_open_clipboard',
            'global_pushtotalk':      'voice_dictate',
            'global_alwayson_toggle':       'voice_alwayson_toggle',
            # Voice rename (v1.9.491): AutoFingers → Voice
            'autofingers_alwayson_toggle':  'voice_alwayson_toggle',
        }
        shortcut_id = _LEGACY_IDS.get(shortcut_id, shortcut_id)

        if shortcut_id in self.custom_shortcuts:
            return self.custom_shortcuts[shortcut_id]

        if shortcut_id in self.DEFAULT_SHORTCUTS:
            return self.DEFAULT_SHORTCUTS[shortcut_id]["default"]

        return ""
    
    def is_global(self, shortcut_id: str) -> bool:
        """Return True if this shortcut should also register as an OS-level
        global hotkey (works from any application)."""
        return bool(self.DEFAULT_SHORTCUTS.get(shortcut_id, {}).get("global", False))

    def is_enabled(self, shortcut_id: str) -> bool:
        """
        Check if a shortcut is enabled
        
        Args:
            shortcut_id: The shortcut identifier
            
        Returns:
            True if enabled (not in disabled set), False if disabled
        """
        return shortcut_id not in self.disabled_shortcuts
    
    def enable_shortcut(self, shortcut_id: str):
        """
        Enable a previously disabled shortcut
        
        Args:
            shortcut_id: The shortcut identifier
        """
        self.disabled_shortcuts.discard(shortcut_id)
    
    def disable_shortcut(self, shortcut_id: str):
        """
        Disable a shortcut
        
        Args:
            shortcut_id: The shortcut identifier
        """
        self.disabled_shortcuts.add(shortcut_id)
    
    def set_shortcut(self, shortcut_id: str, key_sequence: str):
        """
        Set a custom shortcut
        
        Args:
            shortcut_id: The shortcut identifier
            key_sequence: The new key sequence string
        """
        if key_sequence:
            self.custom_shortcuts[shortcut_id] = key_sequence
        elif shortcut_id in self.custom_shortcuts:
            del self.custom_shortcuts[shortcut_id]
    
    def reset_shortcut(self, shortcut_id: str):
        """Reset a shortcut to its default value"""
        if shortcut_id in self.custom_shortcuts:
            del self.custom_shortcuts[shortcut_id]
    
    def reset_all_shortcuts(self):
        """Reset all shortcuts to defaults"""
        self.custom_shortcuts = {}
    
    def get_all_shortcuts(self, include_hidden: bool = False) -> Dict:
        """
        Get all shortcuts with their current values

        Args:
            include_hidden: when False (default), entries flagged
                ``"hidden": True`` in DEFAULT_SHORTCUTS are omitted. Hidden
                entries are actions that the user cannot actually invoke or
                rebind (dead legacy slots, redundant duplicates), so they are
                kept out of the settings list, the cheatsheet, and conflict
                checks. Pass True to get the raw set including them.

        Returns:
            Dictionary of all shortcuts with metadata
        """
        result = {}
        for shortcut_id, data in self.DEFAULT_SHORTCUTS.items():
            if data.get("hidden") and not include_hidden:
                continue
            result[shortcut_id] = {
                **data,
                "current": self.get_shortcut(shortcut_id),
                "is_custom": shortcut_id in self.custom_shortcuts,
                "is_enabled": self.is_enabled(shortcut_id)
            }
        return result
    
    def get_shortcuts_by_category(self) -> Dict[str, List[Tuple[str, Dict]]]:
        """
        Get shortcuts organized by category
        
        Returns:
            Dictionary with categories as keys, list of (id, data) tuples as values
        """
        categories = {}
        all_shortcuts = self.get_all_shortcuts()
        
        for shortcut_id, data in all_shortcuts.items():
            category = data["category"]
            if category not in categories:
                categories[category] = []
            categories[category].append((shortcut_id, data))
        
        return categories
    
    def find_conflicts(self, shortcut_id: str, key_sequence: str) -> List[str]:
        """
        Find conflicts with a proposed shortcut

        Args:
            shortcut_id: The shortcut being changed
            key_sequence: The proposed new key sequence

        Returns:
            List of conflicting shortcut IDs (only enabled shortcuts)
        """
        conflicts = []
        for other_id, data in self.get_all_shortcuts().items():
            if other_id != shortcut_id and data["current"] == key_sequence:
                # Skip disabled shortcuts - they don't count as conflicts
                # (their key combination is freed up for other uses)
                if not self.is_enabled(other_id):
                    continue

                # Check if they're in different contexts (context-specific shortcuts don't conflict)
                this_context = self.DEFAULT_SHORTCUTS.get(shortcut_id, {}).get("context")
                other_context = self.DEFAULT_SHORTCUTS.get(other_id, {}).get("context")

                # Only conflict if same context or no context specified
                if this_context == other_context or not this_context or not other_context:
                    conflicts.append(other_id)

        return conflicts
    
    def export_shortcuts(self, file_path: Path):
        """
        Export shortcuts to a JSON file
        
        Args:
            file_path: Path to export file
        """
        export_data = {
            "version": "1.0",
            "shortcuts": self.custom_shortcuts,
            "disabled": list(self.disabled_shortcuts)
        }
        
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(export_data, f, indent=2)
    
    def import_shortcuts(self, file_path: Path) -> bool:
        """
        Import shortcuts from a JSON file
        
        Args:
            file_path: Path to import file
            
        Returns:
            True if successful, False otherwise
        """
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                import_data = json.load(f)
            
            if "shortcuts" in import_data:
                self.custom_shortcuts = import_data["shortcuts"]
                self.disabled_shortcuts = set(import_data.get("disabled", []))
                return True
            return False
        except Exception as e:
            print(f"Error importing shortcuts: {e}")
            return False
    
    def export_html_cheatsheet(self, file_path: Path):
        """
        Export shortcuts as an HTML cheatsheet
        
        Args:
            file_path: Path to export HTML file
        """
        categories = self.get_shortcuts_by_category()
        
        html = """
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>Supervertaler - Keyboard Shortcuts</title>
    <style>
        body {
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            max-width: 1200px;
            margin: 40px auto;
            padding: 20px;
            background-color: #f5f5f5;
        }
        h1 {
            color: #2196F3;
            text-align: center;
            border-bottom: 3px solid #2196F3;
            padding-bottom: 20px;
        }
        h2 {
            color: #1976D2;
            margin-top: 40px;
            margin-bottom: 20px;
            border-left: 5px solid #2196F3;
            padding-left: 15px;
        }
        table {
            width: 100%;
            border-collapse: collapse;
            background-color: white;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
            margin-bottom: 30px;
        }
        th {
            background-color: #2196F3;
            color: white;
            padding: 12px;
            text-align: left;
            font-weight: 600;
        }
        td {
            padding: 10px 12px;
            border-bottom: 1px solid #e0e0e0;
        }
        tr:hover {
            background-color: #f5f5f5;
        }
        .shortcut {
            font-family: 'Courier New', monospace;
            background-color: #e3f2fd;
            padding: 4px 8px;
            border-radius: 4px;
            font-weight: 600;
            color: #1976D2;
        }
        .custom {
            color: #4CAF50;
            font-weight: 600;
        }
        .footer {
            text-align: center;
            margin-top: 40px;
            color: #666;
            font-size: 0.9em;
        }
        @media print {
            body {
                background-color: white;
            }
            table {
                box-shadow: none;
                page-break-inside: avoid;
            }
        }
    </style>
</head>
<body>
    <h1>🌐 Supervertaler - Keyboard Shortcuts</h1>
    <div class="footer" style="text-align: center; margin-bottom: 30px;">
        <p>The Ultimate Translation Workbench</p>
    </div>
"""
        
        # Add each category
        for category in sorted(categories.keys()):
            shortcuts = categories[category]
            html += f"    <h2>{category}</h2>\n"
            html += "    <table>\n"
            html += "        <tr><th>Action</th><th>Shortcut</th></tr>\n"
            
            for shortcut_id, data in sorted(shortcuts, key=lambda x: x[1]["description"]):
                description = format_shortcuts_in_text(data["description"])
                current = format_shortcut_for_display(data["current"])
                is_custom = data["is_custom"]

                custom_mark = " <span class='custom'>(Custom)</span>" if is_custom else ""

                html += f"        <tr>\n"
                html += f"            <td>{description}{custom_mark}</td>\n"
                html += f"            <td><span class='shortcut'>{current}</span></td>\n"
                html += f"        </tr>\n"
            
            html += "    </table>\n"
        
        html += """
    <div class="footer">
        <p>Generated by Supervertaler Qt Edition</p>
        <p>For more information, visit <a href="https://supervertaler.com">supervertaler.com</a></p>
    </div>
</body>
</html>
"""
        
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(html)

