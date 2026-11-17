"""The main window's settings: the Settings dialog and each of its tabs (general, view, AI, MT,
MT Quick Lookup, system prompts, language pair, backups, autocorrect, file types, identity,
debug), what Save does on each, and where settings are kept: loading, saving and migrating
the settings files, proxy and spellcheck settings.

Moved out of Supervertaler.py unchanged (Nov 2026). SupervertalerQt inherits this class, so the
methods still run as its own.

Part of the main window (SupervertalerQt), moved here unchanged. The
methods run as the main window's own: `self` is the SupervertalerQt.
"""

import json
import os
import os as _os
import re
from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSlider,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)
from dataclasses import field
from datetime import datetime
from modules.colour_checkboxes import CustomRadioButton
from modules.grid_editors import EditableGridTextEditor, ReadOnlyGridTextEditor, TagHighlighter
from modules.help_system import Topics as HelpTopics, open_help, set_topic as set_help_topic
from modules.model_combo import _select_saved_model
from modules.okapi_import_options import DEFAULT_IMPORT_OPTIONS
from modules.platform_helpers import IS_MACOS, open_file, open_folder
from modules.shortcut_display import format_shortcut_for_display
from modules.styled_widgets import CheckmarkCheckBox, CheckmarkRadioButton
from modules.user_data_location import save_user_data_path
from pathlib import Path
from typing import Any, Dict


class SettingsMixin:
    def _cost_display_settings(self):
        """``(currency, usd_to_eur_rate)`` for showing costs."""
        from modules.cost_estimate import currency_settings
        try:
            return currency_settings(self.load_general_settings())
        except Exception:
            return currency_settings({})

    def create_settings_tab(self):
        """Create the Settings tab with vertical sidebar navigation"""
        from PyQt6.QtWidgets import QScrollArea
        from modules.settings_sidebar import SettingsSidebar

        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Create sidebar navigation (replaces horizontal QTabWidget)
        settings_tabs = SettingsSidebar()
        self.settings_tabs = settings_tabs  # Store for reference
        set_help_topic(tab, HelpTopics.SETTINGS_GENERAL)

        # Scroll area wrapper for each tab (for long content)
        scroll_area_wrapper = lambda widget: self._wrap_in_scroll(widget)

        # ===== TAB 1: General Settings =====
        general_tab = self._create_general_settings_tab()
        settings_tabs.addTab(scroll_area_wrapper(general_tab), self.tr("⚙️ General"))

        # ===== TAB: Backup (auto-backup + timestamped project backups) =====
        # Promoted to its own tab (v1.10.307) so users can find the backup
        # controls instead of hunting for them inside General.
        backup_tab = self._create_backup_settings_tab()
        settings_tabs.addTab(scroll_area_wrapper(backup_tab), self.tr("💾 Backup"))

        # ===== TAB: AutoCorrect (v1.10.230, issue #213) =====
        # Lives just below General because that's where the feature was
        # initially prototyped; promoted to its own tab so the per-rule
        # toggles are findable.
        autocorrect_tab = self._create_autocorrect_settings_tab()
        settings_tabs.addTab(scroll_area_wrapper(autocorrect_tab), self.tr("✍️ AutoCorrect"))

        # ===== TAB 2: User Identity =====
        identity_tab = self._create_user_identity_tab()
        settings_tabs.addTab(scroll_area_wrapper(identity_tab), self.tr("👤 User Identity"))

        # ===== TAB 3: AI Settings (LLM, Ollama) =====
        ai_tab = self._create_ai_settings_tab()
        ai_scroll = scroll_area_wrapper(ai_tab)
        settings_tabs.addTab(ai_scroll, self.tr("🤖 AI Settings"))
        self.ai_settings_scroll = ai_scroll  # Store reference for scrolling to API keys

        # ===== TAB 3: Language Pair Settings =====
        lang_tab = self._create_language_pair_tab()
        settings_tabs.addTab(scroll_area_wrapper(lang_tab), self.tr("🌐 Language Pair"))

        # ===== TAB 4: MT Settings =====
        mt_tab = self._create_mt_settings_tab()
        settings_tabs.addTab(scroll_area_wrapper(mt_tab), self.tr("🌐 MT Settings"))

        # ===== TAB 5: QuickTrans Settings =====
        # Prior to v1.10.10 this was a stub pointing users to
        # Sidekick → SuperLookup → SuperLookup Settings → QuickTrans
        # (a sub-sub-tab Sidekick mounted via the is_sidekick=True
        # branch on SuperlookupTab). With Sidekick retired the real
        # QuickTrans settings widget now lives directly here, where
        # users would have looked for it in the first place.
        mt_quick_tab = self._create_mt_quick_lookup_settings_tab()
        settings_tabs.addTab(scroll_area_wrapper(mt_quick_tab), self.tr("⚡ QuickTrans"))
        self.mt_quick_lookup_tab_index = settings_tabs.count() - 1  # Store index for opening

        # ===== TAB 6: View Settings =====
        view_tab = self._create_view_settings_tab()
        settings_tabs.addTab(scroll_area_wrapper(view_tab), self.tr("🔍 View Settings"))

        # ===== TAB 7: System Prompts (Layer 1) =====
        system_prompts_tab = self._create_system_prompts_tab()
        settings_tabs.addTab(scroll_area_wrapper(system_prompts_tab), self.tr("📝 System Prompts"))

        # ===== TAB 8: Debug Settings =====
        debug_tab = self._create_debug_settings_tab()
        settings_tabs.addTab(scroll_area_wrapper(debug_tab), self.tr("🐛 Debug"))

        # The "📦 Features" tab was retired in v1.9.474. The Workbench used
        # to ship feature modules (voice, web, pdf, mt, hunspell, …) as
        # optional pip extras and exposed an install-status panel here. All
        # of those packages are now part of the core dependency set, so the
        # tab no longer had any toggles to offer – everything is always
        # installed and ready. The pyproject [project.optional-dependencies]
        # section is also gone; ``pip install supervertaler`` gives you the
        # full surface area in one step.

        # ===== TAB 11: Segmentation Rules =====
        seg_tab = self.create_segmentation_rules_tab()
        settings_tabs.addTab(scroll_area_wrapper(seg_tab), self.tr("📏 Segmentation Rules"))

        # ===== Inline Codes (issue #194) =====
        codes_tab = self._create_inline_codes_tab()
        settings_tabs.addTab(scroll_area_wrapper(codes_tab), self.tr("🏷️ Inline Codes"))

        # ===== File Types (Okapi import options) =====
        file_types_tab = self._create_file_types_settings_tab()
        settings_tabs.addTab(scroll_area_wrapper(file_types_tab), self.tr("📄 File Types"))

        # ===== TAB 12: Keyboard Shortcuts =====
        from modules.keyboard_shortcuts_widget import KeyboardShortcutsWidget
        shortcuts_tab = KeyboardShortcutsWidget(self)
        settings_tabs.addTab(shortcuts_tab, self.tr("⌨️ Keyboard Shortcuts"))
        # Index stored so the Voice tab's "Change in Settings → Keyboard
        # Shortcuts" link can jump straight here.
        self.keyboard_shortcuts_tab_index = settings_tabs.count() - 1

        # ===== TAB 13: Log (moved from main tabs) =====
        log_tab = self.create_log_tab()
        settings_tabs.addTab(log_tab, self.tr("📋 Log"))

        layout.addWidget(settings_tabs)

        # Issue #214: pages with a "💾 Save …" button now save themselves a
        # moment after each change – the button makes way for a note.
        try:
            from modules import settings_autosave
            self._settings_autosavers = settings_autosave.install(
                [settings_tabs.widget(i) for i in range(settings_tabs.count())],
                notify=lambda message: self.statusBar().showMessage(message, 2500))
        except Exception as e:
            self.log(f"⚠ Settings auto-save not available: {e}")

        # Apply saved UI font scale on startup
        saved_scale = self._get_global_ui_font_scale()
        if saved_scale != 100:
            # Defer application to ensure widgets are fully created
            from PyQt6.QtCore import QTimer
            QTimer.singleShot(100, lambda: self._apply_global_ui_font_scale(saved_scale))

        # Apply saved Compact UI chrome preference on startup.
        # Defaults to compact (v1.9.386+); use 0ms timer so it lands in the same event
        # loop iteration the widgets were constructed in – no visible flash of
        # non-compact chrome before the restyle.
        if self._get_ui_chrome_compact():
            from PyQt6.QtCore import QTimer
            QTimer.singleShot(0, lambda: self._apply_ui_chrome_compactness(True, persist=False))

        # Apply dark sidebar style if current theme is dark
        self._update_settings_sidebar_theme()

        return tab

    def _update_settings_sidebar_theme(self):
        """Update the settings sidebar styling based on the current theme."""
        if not hasattr(self, 'settings_tabs'):
            return
        from modules.settings_sidebar import SettingsSidebar
        if not isinstance(self.settings_tabs, SettingsSidebar):
            return
        is_dark = (hasattr(self, 'theme_manager') and self.theme_manager
                   and 'dark' in self.theme_manager.current_theme.name.lower())
        if is_dark:
            self.settings_tabs.apply_dark_style()
        else:
            self.settings_tabs._apply_style()

    def _create_language_pair_tab(self):
        """Create Language Pair Settings tab content"""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(15)
        
        # Language Pair Settings group
        lang_group = QGroupBox(self.tr("Translation Language Pair"))
        lang_layout = QVBoxLayout()
        
        info_label = QLabel(
            self.tr("Set your default source and target languages for translation projects.")
        )
        info_label.setWordWrap(True)
        info_label.setStyleSheet("font-size: 9pt; padding: 8px; border-radius: 3px;")
        lang_layout.addWidget(info_label)
        
        # Language selection layout
        lang_select_layout = QHBoxLayout()
        
        # Source language
        source_label = QLabel(self.tr("Source Language:"))
        source_combo = QComboBox()
        
        # Canonical language list, shared with the QuickTrans popup selector.
        from modules.quicktrans import AVAILABLE_LANGUAGES as available_languages
        source_combo.addItems(available_languages)
        source_combo.setCurrentText(self.source_language)
        
        lang_select_layout.addWidget(source_label)
        lang_select_layout.addWidget(source_combo, 1)
        
        # Swap button
        swap_btn = QPushButton(self.tr("🔄 Swap"))
        swap_btn.setToolTip(self.tr("Swap source and target languages"))
        lang_select_layout.addWidget(swap_btn)
        
        # Target language
        target_label = QLabel(self.tr("Target Language:"))
        target_combo = QComboBox()
        target_combo.addItems(available_languages)
        target_combo.setCurrentText(self.target_language)
        
        lang_select_layout.addWidget(target_label)
        lang_select_layout.addWidget(target_combo, 1)
        
        lang_layout.addLayout(lang_select_layout)
        
        # Swap functionality
        def on_swap():
            source_val = source_combo.currentText()
            target_val = target_combo.currentText()
            source_combo.setCurrentText(target_val)
            target_combo.setCurrentText(source_val)

        swap_btn.clicked.connect(on_swap)

        # Auto-save on any dropdown change so the chosen pair persists
        # across restarts without requiring the user to click Save.
        # Triggers for typed entries and swap clicks alike via currentTextChanged.
        #
        # IMPORTANT: only save if the value actually differs from what's
        # already in self.source_language / self.target_language. This
        # prevents a spurious startup-time currentTextChanged (from
        # setCurrentText, or from Qt laying out the combo) from clobbering
        # the loaded-from-disk values with whatever happens to be on the
        # widget at that moment.
        def _autosave_lang(_=None):
            new_src = source_combo.currentText()
            new_tgt = target_combo.currentText()
            if not new_src or not new_tgt:
                return  # combo not fully populated yet
            if new_src == self.source_language and new_tgt == self.target_language:
                return  # no real change
            self.source_language = new_src
            self.target_language = new_tgt
            self.save_language_settings(self.source_language, self.target_language)
            self.log(f"✓ Language settings auto-saved: {self.source_language} → {self.target_language}")

        source_combo.currentTextChanged.connect(_autosave_lang)
        target_combo.currentTextChanged.connect(_autosave_lang)

        lang_group.setLayout(lang_layout)
        layout.addWidget(lang_group)

        # Save button – hidden by the Settings auto-saver (issue #214), which
        # clicks it for the user; the combos also save themselves above.
        save_btn = QPushButton(self.tr("💾 Save Language Settings"))
        save_btn.setProperty("settings_save_button", True)  # clicked for the user by the auto-saver (issue #214)
        save_btn.setStyleSheet("font-weight: bold; padding: 8px;")
        save_btn.clicked.connect(lambda: self._save_language_settings_from_ui(source_combo, target_combo))
        layout.addWidget(save_btn)
        
        layout.addStretch()
        
        return tab

    def _create_ai_settings_tab(self):
        """Create unified AI Settings tab - combines LLM, Ollama, and AI Assistant settings"""
        from PyQt6.QtWidgets import QCheckBox, QGroupBox, QPushButton
        
        # The width-cap + left-anchor layout is applied centrally by
        # _wrap_in_scroll so every settings tab gets it consistently.
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(15)

        # Load current settings
        settings = self.load_llm_settings()
        enabled_providers = self.load_provider_enabled_states()
        general_settings = self.load_general_settings()
        general_prefs = self.load_general_settings()

        # Page title (replaces the old "Free vs Paid API Access" info panel
        # that was repeating information already covered in the help docs and
        # added visual noise on every Settings visit).
        title = QLabel(self.tr("AI Settings"))
        title.setStyleSheet("font-size: 16pt; font-weight: bold; padding: 0 0 4px 0;")
        layout.addWidget(title)
        
        # ========== SECTION 1: LLM Provider Selection ==========
        provider_group = QGroupBox(self.tr("🤖 LLM Provider Selection"))
        provider_layout = QVBoxLayout()
        
        provider_label = QLabel(self.tr("Select your preferred AI translation provider:"))
        provider_layout.addWidget(provider_label)
        
        # Provider radio buttons (custom styled)
        provider_button_group = QButtonGroup(tab)
        
        openai_radio = CustomRadioButton("OpenAI")
        openai_radio.setChecked(settings.get('provider', 'openai') == 'openai')
        provider_button_group.addButton(openai_radio)
        provider_layout.addWidget(openai_radio)

        claude_radio = CustomRadioButton("Anthropic Claude")
        claude_radio.setChecked(settings.get('provider', 'openai') == 'claude')
        provider_button_group.addButton(claude_radio)
        provider_layout.addWidget(claude_radio)

        gemini_radio = CustomRadioButton("Google Gemini")
        gemini_radio.setChecked(settings.get('provider', 'openai') == 'gemini')
        provider_button_group.addButton(gemini_radio)
        provider_layout.addWidget(gemini_radio)

        mistral_radio = CustomRadioButton("Mistral AI")
        mistral_radio.setChecked(settings.get('provider', 'openai') == 'mistral')
        provider_button_group.addButton(mistral_radio)
        provider_layout.addWidget(mistral_radio)

        deepseek_radio = CustomRadioButton("DeepSeek")
        deepseek_radio.setChecked(settings.get('provider', 'openai') == 'deepseek')
        provider_button_group.addButton(deepseek_radio)
        provider_layout.addWidget(deepseek_radio)

        openrouter_radio = CustomRadioButton("🌐 OpenRouter (200+ models)")
        openrouter_radio.setChecked(settings.get('provider', 'openai') == 'openrouter')
        provider_button_group.addButton(openrouter_radio)
        provider_layout.addWidget(openrouter_radio)

        # Local LLM option (Ollama)
        ollama_radio = CustomRadioButton("🖥️ Local LLM (Ollama - runs on your computer)")
        ollama_radio.setChecked(settings.get('provider', 'openai') == 'ollama')
        provider_button_group.addButton(ollama_radio)
        provider_layout.addWidget(ollama_radio)

        # Custom OpenAI-compatible endpoint
        custom_radio = CustomRadioButton("🔌 Custom (OpenAI-Compatible API)")
        custom_radio.setChecked(settings.get('provider', 'openai') == 'custom_openai')
        provider_button_group.addButton(custom_radio)
        provider_layout.addWidget(custom_radio)

        provider_group.setLayout(provider_layout)
        layout.addWidget(provider_group)
        
        # ========== SECTION 2: Model Selection ==========
        model_group = QGroupBox(self.tr("📦 Model Selection"))
        model_layout = QVBoxLayout()
        
        model_label = QLabel(self.tr("Choose the specific model to use:"))
        model_layout.addWidget(model_label)
        
        # OpenAI models
        openai_model_label = QLabel(self.tr("<b>OpenAI Models:</b>"))
        model_layout.addWidget(openai_model_label)
        
        openai_combo = QComboBox()
        # OpenAI's three current tiers, most capable first; GPT-6.1 Sol is the
        # default (Michael, 2026-10-07), as in the Trados and memoQ plugins.
        openai_combo.addItems([
            "gpt-6-astra (Premium)",
            "gpt-6.1-sol (Recommended)",
            "gpt-6-luna (Budget)"
        ])
        openai_combo.setToolTip(
            "GPT-6 Astra: OpenAI's most capable model, for the hardest work ($10/$50 per million tokens).\n"
            "GPT-6.1 Sol: close to Astra's quality at a fifth of its price ($2/$10).\n"
            "GPT-6 Luna: the cheapest, for high-volume work ($0.10/$0.50)."
        )
        current_openai_model = settings.get('openai_model', 'gpt-6.1-sol')
        # A saved model that has left the list is shown rather than replaced: the
        # combo cannot be typed in, so falling back to the first entry meant the
        # next save switched the user to Astra, the dearest.
        openai_idx = next((i for i in range(openai_combo.count())
                           if openai_combo.itemText(i).split()[0] == current_openai_model), -1)
        if openai_idx < 0 and current_openai_model:
            openai_combo.addItem(f"{current_openai_model} (your current model)")
            openai_idx = openai_combo.count() - 1
        openai_combo.setCurrentIndex(max(openai_idx, 0))
        openai_combo.setEnabled(openai_radio.isChecked())
        model_layout.addWidget(openai_combo)
        
        model_layout.addSpacing(10)
        
        # Claude models
        claude_model_label = QLabel(self.tr("<b>Claude Models:</b>"))
        model_layout.addWidget(claude_model_label)
        
        claude_combo = QComboBox()
        # The newest Sonnet, Opus and Fable (Michael, 2026-09-28), as in the Trados
        # and memoQ plugins.
        claude_combo.addItems([
            "claude-sonnet-5-5 (Recommended)",
            "claude-opus-5-5 (Premium - Highest Quality)",
            "claude-fable-5-2 (Maximum - Deepest Reasoning, 2x Opus Price)"
        ])
        claude_combo.setToolTip(
            "Claude Sonnet 5.5: Recommended - near-Opus quality at Sonnet cost ($2/$10, 1M context).\n"
            "Claude Opus 5.5: Anthropic's newest Opus - highest quality for hard\n"
            "  legal/technical work ($4/$20, 1M context).\n"
            "Claude Fable 5.2: Anthropic's most capable model overall - always-on reasoning,\n"
            "  double Opus pricing ($8/$40); for the hardest work when cost is secondary."
        )
        current_claude_model = settings.get('claude_model', 'claude-sonnet-5-5')
        # Exact match on the model ID. A substring test found "claude-sonnet-5"
        # inside "claude-sonnet-5-5"; and a saved model that is no longer listed
        # (Sonnet 5, Haiku 4.5, Opus 5...) fell back to the first entry, which
        # saving Settings then wrote over the user's choice. It is shown instead.
        for i in range(claude_combo.count()):
            if claude_combo.itemText(i).split()[0] == current_claude_model:
                claude_combo.setCurrentIndex(i)
                break
        else:
            if current_claude_model:
                claude_combo.addItem(f"{current_claude_model} (your current model)")
                claude_combo.setCurrentIndex(claude_combo.count() - 1)
        claude_combo.setEnabled(claude_radio.isChecked())
        model_layout.addWidget(claude_combo)
        
        model_layout.addSpacing(10)
        
        # Gemini models
        gemini_model_label = QLabel(self.tr("<b>Gemini Models:</b>"))
        model_layout.addWidget(gemini_model_label)
        
        gemini_combo = QComboBox()
        # Gemini 4 Argon as in the Trados and memoQ plugins (since release 203).
        gemini_combo.addItems([
            "gemini-3.1-flash-lite (Recommended - Fast & Economical)",
            "gemini-3.5-flash (Premium Flash - Higher Cost)",
            "gemini-2.5-pro (Premium - Complex Reasoning)",
            "gemini-3.1-pro-preview (Premium - Previous Pro)",
            "gemini-4-argon (Top Quality - Smartest Gemini)",
            "gemma-4-26b-a4b-it (Open Model - Lightweight)"
        ])
        gemini_combo.setToolTip(
            "Gemini 3.1 Flash-Lite: Fast and economical – great for routine translation.\n"
            "Gemini 3.5 Flash: Highest-quality Flash – ~6x the cost of Flash-Lite; for hard segments.\n"
            "Gemini 2.5 Pro: Premium for complex problems.\n"
            "Gemini 3.1 Pro Preview: the Pro before Gemini 4 Argon.\n"
            "Gemini 4 Argon: Google's most capable model ($4/$20 per million tokens,\n"
            "  $2/$10 until 30 Nov 2026), 1M context.\n"
            "Gemma 4 26B MoE: Google's open model, lightweight and capable."
        )
        _select_saved_model(gemini_combo, settings.get('gemini_model', 'gemini-3.1-flash-lite'))
        gemini_combo.setEnabled(gemini_radio.isChecked())
        model_layout.addWidget(gemini_combo)

        model_layout.addSpacing(10)

        # Mistral models
        mistral_model_label = QLabel(self.tr("<b>Mistral AI Models:</b>"))
        model_layout.addWidget(mistral_model_label)

        mistral_combo = QComboBox()
        mistral_combo.addItems([
            "mistral-large-latest (Recommended - Flagship)",
            "mistral-small-latest (Fast & Cost-Effective)"
        ])
        mistral_combo.setToolTip(
            "Mistral Large: Flagship model, top-tier reasoning and multilingual quality.\n"
            "Mistral Small: Fast and cost-effective, great for high-volume translation."
        )
        current_mistral_model = settings.get('mistral_model', 'mistral-large-latest')
        for i in range(mistral_combo.count()):
            if current_mistral_model in mistral_combo.itemText(i):
                mistral_combo.setCurrentIndex(i)
                break
        mistral_combo.setEnabled(mistral_radio.isChecked())
        model_layout.addWidget(mistral_combo)

        model_layout.addSpacing(10)

        # DeepSeek models
        deepseek_model_label = QLabel(self.tr("<b>DeepSeek Models:</b>"))
        model_layout.addWidget(deepseek_model_label)

        deepseek_combo = QComboBox()
        deepseek_combo.addItems([
            "deepseek-v4-pro (Recommended - Flagship)",
            "deepseek-v4-flash (Fast & Cost-Effective)"
        ])
        deepseek_combo.setToolTip(
            "DeepSeek V4 Pro: Flagship – top-tier reasoning and multilingual quality.\n"
            "DeepSeek V4 Flash: Fast and cost-effective, great for high-volume translation.\n\n"
            "Get an API key at platform.deepseek.com."
        )
        current_deepseek_model = settings.get('deepseek_model', 'deepseek-v4-pro')
        for i in range(deepseek_combo.count()):
            if current_deepseek_model in deepseek_combo.itemText(i):
                deepseek_combo.setCurrentIndex(i)
                break
        deepseek_combo.setEnabled(deepseek_radio.isChecked())
        model_layout.addWidget(deepseek_combo)

        model_layout.addSpacing(10)

        # OpenRouter models
        openrouter_model_label = QLabel(self.tr("<b>🌐 OpenRouter Models:</b>"))
        model_layout.addWidget(openrouter_model_label)

        openrouter_combo = QComboBox()
        openrouter_combo.setEditable(True)
        # The same models as LLMClient.OPENROUTER_MODELS and the Trados and
        # memoQ plugins' OpenRouter list, in the same order.
        openrouter_combo.addItems([
            "anthropic/claude-sonnet-5.5 (Recommended)",
            "anthropic/claude-opus-5.5 (Premium)",
            "anthropic/claude-fable-5.2 (Maximum - 2x Opus Price)",
            "openai/gpt-6-astra (OpenAI's most capable)",
            "openai/gpt-6.1-sol (OpenAI recommended)",
            "openai/gpt-6-luna (OpenAI's cheapest)",
            "google/gemini-3.8-flash (Fast Gemini)",
            "google/gemini-4-argon (Smartest Gemini)",
            "google/gemma-4-31b-it (Open Model)",
            "google/gemma-4-26b-a4b-it (Open Model - Lightweight)",
            "mistralai/mistral-small-2603 (European Languages)",
            "qwen/qwen3.8-27b:free (Free - Not for Confidential Work)",
            "deepseek/deepseek-v4-pro (DeepSeek Flagship)",
            "deepseek/deepseek-v4-flash (DeepSeek Fast)"
        ])
        openrouter_combo.setToolTip(
            "OpenRouter gives you access to 200+ models with a single API key.\n"
            "Select a model from the dropdown or type any model ID from openrouter.ai/models.\n\n"
            "Pricing varies per model – see openrouter.ai for details.\n"
            "Providers of free models may keep what you send: not for confidential work."
        )
        _select_saved_model(openrouter_combo,
                            settings.get('openrouter_model', 'anthropic/claude-sonnet-5.5'))
        openrouter_combo.setEnabled(openrouter_radio.isChecked())
        model_layout.addWidget(openrouter_combo)

        model_layout.addSpacing(10)

        # Local LLM (Ollama) models
        ollama_model_label = QLabel(self.tr("<b>🖥️ Local LLM Models (Ollama):</b>"))
        model_layout.addWidget(ollama_model_label)
        
        # Import the LocalLLMStatusWidget for embedded status
        try:
            from modules.local_llm_setup import LocalLLMStatusWidget, check_ollama_status, RECOMMENDED_MODELS
            
            # Create embedded status widget
            ollama_status_widget = LocalLLMStatusWidget()
            ollama_status_widget.setEnabled(ollama_radio.isChecked())
            model_layout.addWidget(ollama_status_widget)
            
            # Store reference for saving settings
            tab.ollama_status_widget = ollama_status_widget
            
        except ImportError as e:
            # Fallback if module not available
            ollama_fallback = QLabel(self.tr("⚠️ Local LLM module not available"))
            ollama_fallback.setStyleSheet("color: orange;")
            model_layout.addWidget(ollama_fallback)
            ollama_status_widget = None
            tab.ollama_status_widget = None
        
        # Custom OpenAI-compatible endpoint settings (with profiles)
        model_layout.addSpacing(10)
        custom_model_label = QLabel(self.tr("<b>🔌 Custom (OpenAI-Compatible) Settings:</b>"))
        model_layout.addWidget(custom_model_label)

        # Profile selector row
        profile_row = QHBoxLayout()
        profile_label = QLabel(self.tr("Profile:"))
        profile_label.setStyleSheet("font-size: 9pt; margin-top: 2px;")
        profile_row.addWidget(profile_label)

        custom_profile_combo = QComboBox()
        custom_profile_combo.setMinimumWidth(200)
        profiles = settings.get('custom_openai_profiles', [])
        active_profile_name = settings.get('custom_openai_active_profile', '')
        for p in profiles:
            custom_profile_combo.addItem(p.get('name', 'Unnamed'))
        if active_profile_name:
            idx = custom_profile_combo.findText(active_profile_name)
            if idx >= 0:
                custom_profile_combo.setCurrentIndex(idx)
        custom_profile_combo.setEnabled(custom_radio.isChecked())
        profile_row.addWidget(custom_profile_combo)

        add_profile_btn = QPushButton("+")
        add_profile_btn.setFixedWidth(30)
        add_profile_btn.setToolTip(self.tr("Add a new custom API profile"))
        add_profile_btn.setEnabled(custom_radio.isChecked())
        profile_row.addWidget(add_profile_btn)

        remove_profile_btn = QPushButton("−")
        remove_profile_btn.setFixedWidth(30)
        remove_profile_btn.setToolTip(self.tr("Remove the selected profile"))
        remove_profile_btn.setEnabled(custom_radio.isChecked() and custom_profile_combo.count() > 0)
        profile_row.addWidget(remove_profile_btn)

        profile_row.addStretch()
        model_layout.addLayout(profile_row)

        custom_endpoint_label = QLabel(self.tr("API Endpoint URL:"))
        custom_endpoint_label.setStyleSheet("font-size: 9pt; margin-top: 2px;")
        model_layout.addWidget(custom_endpoint_label)

        custom_endpoint_input = QLineEdit()
        custom_endpoint_input.setPlaceholderText(self.tr("https://api.example.com/v1"))
        custom_endpoint_input.setEnabled(custom_radio.isChecked())
        model_layout.addWidget(custom_endpoint_input)

        custom_model_name_label = QLabel(self.tr("Model Name or Endpoint ID:"))
        custom_model_name_label.setStyleSheet("font-size: 9pt; margin-top: 2px;")
        model_layout.addWidget(custom_model_name_label)

        custom_model_input = QLineEdit()
        custom_model_input.setPlaceholderText(self.tr("e.g. qwen-plus, ep-xxxxx, deepseek-chat"))
        custom_model_input.setEnabled(custom_radio.isChecked())
        model_layout.addWidget(custom_model_input)

        custom_key_label = QLabel(self.tr("API Key:"))
        custom_key_label.setStyleSheet("font-size: 9pt; margin-top: 2px;")
        model_layout.addWidget(custom_key_label)

        custom_key_row = QHBoxLayout()
        custom_key_input = QLineEdit()
        custom_key_input.setPlaceholderText(self.tr("API key for this endpoint (leave empty if it needs none)"))
        custom_key_input.setEchoMode(QLineEdit.EchoMode.Password)
        custom_key_input.setEnabled(custom_radio.isChecked())
        custom_key_row.addWidget(custom_key_input)

        custom_key_toggle = QPushButton("👁")
        custom_key_toggle.setFixedWidth(30)
        custom_key_toggle.setCheckable(True)
        custom_key_toggle.setToolTip(self.tr("Show/hide API key"))
        custom_key_toggle.toggled.connect(
            lambda checked: custom_key_input.setEchoMode(
                QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password
            )
        )
        custom_key_row.addWidget(custom_key_toggle)
        model_layout.addLayout(custom_key_row)

        # Populate fields from active profile
        def _populate_profile_fields(index):
            """Populate endpoint/model/key fields from the selected profile."""
            _profiles = settings.get('custom_openai_profiles', [])
            if 0 <= index < len(_profiles):
                p = _profiles[index]
                custom_endpoint_input.setText(p.get('endpoint', ''))
                custom_model_input.setText(p.get('model', ''))
                custom_key_input.setText(p.get('api_key', ''))
            else:
                custom_endpoint_input.clear()
                custom_model_input.clear()
                custom_key_input.clear()

        # Initial population
        if custom_profile_combo.count() > 0:
            _populate_profile_fields(custom_profile_combo.currentIndex())

        custom_profile_combo.currentIndexChanged.connect(_populate_profile_fields)

        # Save current field values back into the profiles list when fields change
        def _sync_fields_to_profile():
            """Write current field values back into the profiles list."""
            idx = custom_profile_combo.currentIndex()
            _profiles = settings.get('custom_openai_profiles', [])
            if 0 <= idx < len(_profiles):
                _profiles[idx]['endpoint'] = custom_endpoint_input.text().strip()
                _profiles[idx]['model'] = custom_model_input.text().strip()
                _profiles[idx]['api_key'] = custom_key_input.text().strip()

        custom_endpoint_input.editingFinished.connect(_sync_fields_to_profile)
        custom_model_input.editingFinished.connect(_sync_fields_to_profile)
        custom_key_input.editingFinished.connect(_sync_fields_to_profile)

        # Add profile handler
        def _add_custom_profile():
            from PyQt6.QtWidgets import QInputDialog
            name, ok = QInputDialog.getText(
                self, "New Custom API Profile",
                "Profile name (e.g. 'Volcengine Doubao', 'DeepSeek'):"
            )
            if ok and name.strip():
                name = name.strip()
                _profiles = settings.get('custom_openai_profiles', [])
                # Check for duplicate name
                if any(p.get('name') == name for p in _profiles):
                    from PyQt6.QtWidgets import QMessageBox
                    QMessageBox.warning(self, "Duplicate Name", f"A profile named '{name}' already exists.")
                    return
                new_profile = {'name': name, 'endpoint': '', 'model': '', 'api_key': ''}
                _profiles.append(new_profile)
                settings['custom_openai_profiles'] = _profiles
                custom_profile_combo.addItem(name)
                custom_profile_combo.setCurrentIndex(custom_profile_combo.count() - 1)
                remove_profile_btn.setEnabled(True)

        add_profile_btn.clicked.connect(_add_custom_profile)

        # Remove profile handler
        def _remove_custom_profile():
            idx = custom_profile_combo.currentIndex()
            if idx < 0:
                return
            from PyQt6.QtWidgets import QMessageBox
            name = custom_profile_combo.currentText()
            reply = QMessageBox.question(
                self, "Remove Profile",
                f"Remove profile '{name}'?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            if reply == QMessageBox.StandardButton.Yes:
                _profiles = settings.get('custom_openai_profiles', [])
                if idx < len(_profiles):
                    _profiles.pop(idx)
                    settings['custom_openai_profiles'] = _profiles
                custom_profile_combo.removeItem(idx)
                remove_profile_btn.setEnabled(custom_profile_combo.count() > 0)

        remove_profile_btn.clicked.connect(_remove_custom_profile)

        custom_info = QLabel(
            "💡 Works with any OpenAI-compatible API: Volcengine (Doubao), "
            "Alibaba Tongyi (Qwen), DeepSeek, Mistral, Groq, etc.\n"
            "Create multiple profiles to switch between different endpoints."
        )
        custom_info.setWordWrap(True)
        custom_info.setStyleSheet("color: #666; font-size: 9pt; padding: 5px;")
        model_layout.addWidget(custom_info)

        # Helper to extract friendly model name from combo text and update radio label
        def _friendly_model_name(combo_text: str) -> str:
            """Extract model ID from combo text like 'gpt-6.1-sol (Recommended)' → 'GPT-6.1 Sol'"""
            model_id = combo_text.split()[0] if combo_text else ""
            friendly = {
                "gpt-6-astra": "GPT-6 Astra", "gpt-6.1-sol": "GPT-6.1 Sol", "gpt-6-luna": "GPT-6 Luna",
                "gpt-5.5": "GPT-5.5", "gpt-5.4-mini": "GPT-5.4 Mini",
                "claude-sonnet-5-5": "Claude Sonnet 5.5", "claude-opus-5-5": "Claude Opus 5.5", "claude-fable-5-2": "Claude Fable 5.2", "claude-fable-5-1": "Claude Fable 5.1",
                "claude-sonnet-5": "Claude Sonnet 5", "claude-opus-5": "Claude Opus 5", "claude-fable-5": "Claude Fable 5",
                "claude-haiku-4-5-20251001": "Claude Haiku 4.5",
                "gemini-3.1-flash-lite": "Gemini 3.1 Flash-Lite", "gemini-3.5-flash": "Gemini 3.5 Flash",
                "gemini-2.5-pro": "Gemini 2.5 Pro",
                "gemini-3.1-pro-preview": "Gemini 3.1 Pro", "gemini-4-argon": "Gemini 4 Argon",
                "gemma-4-26b-a4b-it": "Gemma 4 26B MoE",
                "mistral-large-latest": "Mistral Large", "mistral-small-latest": "Mistral Small",
                "deepseek-v4-pro": "DeepSeek V4 Pro", "deepseek-v4-flash": "DeepSeek V4 Flash",
            }
            return friendly.get(model_id, model_id)

        def _update_provider_label(radio, base_name, combo):
            """Update radio button label to show selected model: 'Provider (Model)'"""
            model_name = _friendly_model_name(combo.currentText())
            radio.setText(f"{base_name} ({model_name})")

        # Connect combo changes to update radio button labels dynamically
        openai_combo.currentIndexChanged.connect(
            lambda: _update_provider_label(openai_radio, "OpenAI", openai_combo))
        claude_combo.currentIndexChanged.connect(
            lambda: _update_provider_label(claude_radio, "Anthropic Claude", claude_combo))
        gemini_combo.currentIndexChanged.connect(
            lambda: _update_provider_label(gemini_radio, "Google Gemini", gemini_combo))
        mistral_combo.currentIndexChanged.connect(
            lambda: _update_provider_label(mistral_radio, "Mistral AI", mistral_combo))
        deepseek_combo.currentIndexChanged.connect(
            lambda: _update_provider_label(deepseek_radio, "DeepSeek", deepseek_combo))
        openrouter_combo.currentIndexChanged.connect(
            lambda: _update_provider_label(openrouter_radio, "🌐 OpenRouter (200+ models)", openrouter_combo))

        # Set initial labels based on current combo selections
        _update_provider_label(openai_radio, "OpenAI", openai_combo)
        _update_provider_label(claude_radio, "Anthropic Claude", claude_combo)
        _update_provider_label(gemini_radio, "Google Gemini", gemini_combo)
        _update_provider_label(mistral_radio, "Mistral AI", mistral_combo)
        _update_provider_label(deepseek_radio, "DeepSeek", deepseek_combo)
        _update_provider_label(openrouter_radio, "🌐 OpenRouter (200+ models)", openrouter_combo)

        # Connect radio buttons to enable/disable combos
        def update_combo_states():
            openai_combo.setEnabled(openai_radio.isChecked())
            claude_combo.setEnabled(claude_radio.isChecked())
            gemini_combo.setEnabled(gemini_radio.isChecked())
            mistral_combo.setEnabled(mistral_radio.isChecked())
            deepseek_combo.setEnabled(deepseek_radio.isChecked())
            openrouter_combo.setEnabled(openrouter_radio.isChecked())
            if ollama_status_widget:
                ollama_status_widget.setEnabled(ollama_radio.isChecked())
            _custom_enabled = custom_radio.isChecked()
            custom_profile_combo.setEnabled(_custom_enabled)
            add_profile_btn.setEnabled(_custom_enabled)
            remove_profile_btn.setEnabled(_custom_enabled and custom_profile_combo.count() > 0)
            custom_endpoint_input.setEnabled(_custom_enabled)
            custom_model_input.setEnabled(_custom_enabled)
            custom_key_input.setEnabled(_custom_enabled)
        
        # Handler to show setup dialog when Ollama is selected but not running
        def on_ollama_selected(checked):
            if checked:
                try:
                    from modules.local_llm_setup import check_ollama_status, LocalLLMSetupDialog
                    status = check_ollama_status()
                    
                    if not status.get('running', False):
                        from PyQt6.QtWidgets import QMessageBox
                        msg = QMessageBox(self)
                        msg.setWindowTitle(self.tr("Local LLM Setup Required"))
                        msg.setIcon(QMessageBox.Icon.Information)
                        msg.setText(
                            "<b>🖥️ Local LLM (Ollama) Selected</b><br><br>"
                            "Ollama is not currently running on your computer.<br><br>"
                            "Would you like to open the setup wizard?"
                        )
                        msg.setStandardButtons(
                            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
                        )
                        msg.setDefaultButton(QMessageBox.StandardButton.Yes)
                        
                        if msg.exec() == QMessageBox.StandardButton.Yes:
                            setup_dialog = LocalLLMSetupDialog(self)
                            setup_dialog.exec()
                            if ollama_status_widget:
                                ollama_status_widget.refresh_status()
                except Exception as e:
                    self.log(f"Error checking Ollama status: {e}")
        
        openai_radio.toggled.connect(update_combo_states)
        claude_radio.toggled.connect(update_combo_states)
        gemini_radio.toggled.connect(update_combo_states)
        mistral_radio.toggled.connect(update_combo_states)
        deepseek_radio.toggled.connect(update_combo_states)
        openrouter_radio.toggled.connect(update_combo_states)
        ollama_radio.toggled.connect(update_combo_states)
        ollama_radio.toggled.connect(on_ollama_selected)
        custom_radio.toggled.connect(update_combo_states)
        
        model_group.setLayout(model_layout)
        layout.addWidget(model_group)

        # ========== SECTION 2c: API Keys ==========
        api_keys_group = QGroupBox(self.tr("🔑 LLM API Keys"))
        api_keys_form = QFormLayout()
        api_keys_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        current_keys = self.load_api_keys()
        from modules.llm_clients import unreadable_key_notes
        key_notes = unreadable_key_notes()
        if not hasattr(self, '_api_key_inputs'):
            self._api_key_inputs = {}

        llm_key_fields = [
            ("OpenAI:", "openai", "sk-proj-..."),
            ("Claude (Anthropic):", "claude", "sk-ant-api03-..."),
            ("Gemini (Google AI):", "gemini", "AIza..."),
            ("Mistral AI:", "mistral", "..."),
            ("DeepSeek:", "deepseek", "sk-..."),
            ("OpenRouter:", "openrouter", "sk-or-..."),
            ("Ollama Endpoint:", "ollama_endpoint", "http://localhost:11434"),
        ]

        for label, key_name, placeholder in llm_key_fields:
            row = QHBoxLayout()
            key_input = QLineEdit()
            key_input.setPlaceholderText(placeholder)
            key_input.setText(current_keys.get(key_name, ''))
            if key_name != 'ollama_endpoint':
                key_input.setEchoMode(QLineEdit.EchoMode.Password)
            row.addWidget(key_input)

            if key_name != 'ollama_endpoint':
                toggle_btn = QPushButton(self.tr("Show"))
                toggle_btn.setFixedWidth(50)
                toggle_btn.setCheckable(True)
                toggle_btn.clicked.connect(
                    lambda checked, inp=key_input, btn=toggle_btn:
                        (inp.setEchoMode(QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password),
                         btn.setText("Hide" if checked else "Show"))
                )
                row.addWidget(toggle_btn)

            container = QWidget()
            container.setLayout(row)
            api_keys_form.addRow(label, self._with_key_note(container, key_name, key_input, key_notes))
            self._api_key_inputs[key_name] = key_input

        api_keys_group.setLayout(api_keys_form)
        layout.addWidget(api_keys_group)

        # ========== SECTION 3: Enable/Disable LLM Providers ==========
        provider_enable_group = QGroupBox(self.tr("✅ Enable/Disable LLM Providers"))
        provider_enable_layout = QVBoxLayout()
        
        provider_enable_info = QLabel(
            self.tr("Uncheck providers you don't want to use. Only enabled providers will be available.")
        )
        provider_enable_info.setWordWrap(True)
        provider_enable_info.setStyleSheet("font-size: 9pt; color: #666; padding: 5px;")
        provider_enable_layout.addWidget(provider_enable_info)
        
        openai_enable_cb = CheckmarkCheckBox(self.tr("Enable OpenAI"))
        openai_enable_cb.setChecked(enabled_providers.get('llm_openai', True))
        provider_enable_layout.addWidget(openai_enable_cb)
        
        claude_enable_cb = CheckmarkCheckBox(self.tr("Enable Claude"))
        claude_enable_cb.setChecked(enabled_providers.get('llm_claude', True))
        provider_enable_layout.addWidget(claude_enable_cb)
        
        gemini_enable_cb = CheckmarkCheckBox(self.tr("Enable Gemini"))
        gemini_enable_cb.setChecked(enabled_providers.get('llm_gemini', True))
        provider_enable_layout.addWidget(gemini_enable_cb)

        mistral_enable_cb = CheckmarkCheckBox(self.tr("Enable Mistral AI"))
        mistral_enable_cb.setChecked(enabled_providers.get('llm_mistral', True))
        provider_enable_layout.addWidget(mistral_enable_cb)

        openrouter_enable_cb = CheckmarkCheckBox(self.tr("Enable OpenRouter"))
        openrouter_enable_cb.setChecked(enabled_providers.get('llm_openrouter', True))
        provider_enable_layout.addWidget(openrouter_enable_cb)

        ollama_enable_cb = CheckmarkCheckBox(self.tr("Enable Local LLM (Ollama)"))
        ollama_enable_cb.setChecked(enabled_providers.get('llm_ollama', True))
        provider_enable_layout.addWidget(ollama_enable_cb)

        custom_enable_cb = CheckmarkCheckBox(self.tr("Enable Custom (OpenAI-Compatible)"))
        custom_enable_cb.setChecked(enabled_providers.get('llm_custom_openai', True))
        provider_enable_layout.addWidget(custom_enable_cb)

        provider_enable_group.setLayout(provider_enable_layout)
        layout.addWidget(provider_enable_group)

        # ========== SECTION 3b: HTTP Proxy Settings ==========
        proxy_settings = self.load_proxy_settings()

        proxy_group = QGroupBox(self.tr("🌐 HTTP Proxy Settings"))
        proxy_layout = QVBoxLayout()
        proxy_layout.setSpacing(8)

        proxy_info = QLabel(
            "Route all AI and MT API requests through an HTTP proxy.\n"
            "Useful if direct access to AI/MT services is restricted in your region."
        )
        proxy_info.setWordWrap(True)
        proxy_info.setStyleSheet("font-size: 9pt; color: #666; padding: 5px;")
        proxy_layout.addWidget(proxy_info)

        self.proxy_enabled_cb = CheckmarkCheckBox(self.tr("Enable HTTP proxy"))
        self.proxy_enabled_cb.setChecked(proxy_settings.get('enabled', False))
        proxy_layout.addWidget(self.proxy_enabled_cb)

        # Host + port row
        proxy_addr_row = QHBoxLayout()
        proxy_addr_row.addWidget(QLabel(self.tr("Host:")))
        self.proxy_host_input = QLineEdit()
        self.proxy_host_input.setPlaceholderText(self.tr("e.g. 127.0.0.1"))
        self.proxy_host_input.setText(proxy_settings.get('host', ''))
        self.proxy_host_input.setMinimumWidth(180)
        proxy_addr_row.addWidget(self.proxy_host_input)
        proxy_addr_row.addSpacing(12)
        proxy_addr_row.addWidget(QLabel(self.tr("Port:")))
        self.proxy_port_spin = QSpinBox()
        self.proxy_port_spin.setMinimum(1)
        self.proxy_port_spin.setMaximum(65535)
        self.proxy_port_spin.setValue(proxy_settings.get('port', 8080))
        self.proxy_port_spin.setFixedWidth(80)
        proxy_addr_row.addWidget(self.proxy_port_spin)
        proxy_addr_row.addStretch()
        proxy_layout.addLayout(proxy_addr_row)

        # Optional credentials row
        proxy_cred_row = QHBoxLayout()
        proxy_cred_row.addWidget(QLabel(self.tr("Username (optional):")))
        self.proxy_user_input = QLineEdit()
        self.proxy_user_input.setPlaceholderText(self.tr("leave blank if not required"))
        self.proxy_user_input.setText(proxy_settings.get('username', ''))
        self.proxy_user_input.setMinimumWidth(150)
        proxy_cred_row.addWidget(self.proxy_user_input)
        proxy_cred_row.addSpacing(12)
        proxy_cred_row.addWidget(QLabel(self.tr("Password:")))
        self.proxy_pass_input = QLineEdit()
        self.proxy_pass_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.proxy_pass_input.setPlaceholderText(self.tr("leave blank if not required"))
        self.proxy_pass_input.setText(proxy_settings.get('password', ''))
        self.proxy_pass_input.setMinimumWidth(130)
        proxy_cred_row.addWidget(self.proxy_pass_input)
        proxy_cred_row.addStretch()
        proxy_layout.addLayout(proxy_cred_row)

        proxy_note = QLabel(
            "ⓘ  Applies to: OpenAI, Claude, Gemini, Mistral, Ollama, Google Translate, DeepL,\n"
            "    Microsoft Translator, ModernMT, and MyMemory."
        )
        proxy_note.setStyleSheet("font-size: 9pt; color: #888; padding-left: 4px;")
        proxy_layout.addWidget(proxy_note)

        proxy_group.setLayout(proxy_layout)
        layout.addWidget(proxy_group)

        # ========== SECTION 4: Local LLM (Ollama) Advanced Settings ==========
        ollama_group = QGroupBox(self.tr("🖥️ Local LLM (Ollama) Advanced Settings"))
        ollama_layout = QVBoxLayout()
        
        ollama_keepwarm_cb = CheckmarkCheckBox(self.tr("Keep model warm (prevent unloading)"))
        ollama_keepwarm_cb.setChecked(general_settings.get('ollama_keepwarm', False))
        ollama_keepwarm_cb.setToolTip(
            "When enabled, sends a small ping to Ollama every 4 minutes\n"
            "to keep the model loaded in memory for faster translations.\n\n"
            "Drawback: Model keeps using RAM even when not translating."
        )
        ollama_layout.addWidget(ollama_keepwarm_cb)

        # Request timeout (issue #180): slow CPU-only machines can need far
        # longer than the automatic 3-10 minutes for a single batch request.
        ollama_timeout_row = QHBoxLayout()
        ollama_timeout_row.addWidget(QLabel(self.tr("Request timeout:")))
        ollama_timeout_spin = QSpinBox()
        ollama_timeout_spin.setRange(0, 1440)
        ollama_timeout_spin.setSuffix(self.tr(" min"))
        ollama_timeout_spin.setSpecialValueText(self.tr("Automatic"))
        ollama_timeout_spin.setValue(int(settings.get('ollama_timeout_minutes', 0) or 0))
        ollama_timeout_spin.setToolTip(
            "How long to wait for Ollama to answer a single request.\n\n"
            "Automatic: 3–10 minutes, depending on model size and prompt length.\n"
            "Raise it if translations time out on a slow computer (for example\n"
            "one without a dedicated GPU). Up to 1440 minutes (24 hours)."
        )
        ollama_timeout_row.addWidget(ollama_timeout_spin)
        ollama_timeout_row.addStretch()
        ollama_layout.addLayout(ollama_timeout_row)

        ollama_info = QLabel(
            "💡 <b>Tip:</b> Ollama normally unloads models after 5 minutes of inactivity.\n"
            "Enable 'Keep warm' for faster translations if you translate frequently."
        )
        ollama_info.setWordWrap(True)
        ollama_info.setStyleSheet("color: #666; font-size: 9pt; padding: 5px;")
        ollama_layout.addWidget(ollama_info)
        
        ollama_group.setLayout(ollama_layout)
        layout.addWidget(ollama_group)
        
        # ========== SECTION 5: AI Translation Preferences ==========
        prefs_group = QGroupBox(self.tr("⚙️ AI Translation Preferences"))
        prefs_layout = QVBoxLayout()
        prefs_layout.setSpacing(10)

        # Load current preferences
        batch_size = general_prefs.get('batch_size', 20)
        surrounding_segments = general_prefs.get('surrounding_segments', 5)
        use_full_context = general_prefs.get('use_full_context', True)
        check_tm_before_api = general_prefs.get('check_tm_before_api', True)

        # --- Single-Segment Translation (Ctrl+T) ---
        single_header = QLabel(
            f"<b>Single-Segment Translation ({format_shortcut_for_display('Ctrl+T')}):</b>"
        )
        prefs_layout.addWidget(single_header)

        surrounding_layout = QHBoxLayout()
        surrounding_label = QLabel(self.tr("Context segments:"))
        surrounding_layout.addWidget(surrounding_label)
        surrounding_spin = QSpinBox()
        surrounding_spin.setMinimum(0)
        surrounding_spin.setMaximum(20)
        surrounding_spin.setValue(surrounding_segments)
        surrounding_layout.addWidget(surrounding_spin)
        surrounding_segments_label = QLabel(self.tr("segments before/after"))
        surrounding_layout.addWidget(surrounding_segments_label)
        surrounding_layout.addStretch()
        prefs_layout.addLayout(surrounding_layout)
        surrounding_info = QLabel(self.tr("  ⓘ Surrounding segments sent to the AI for context. 0 = no context. Default: 5"))
        surrounding_info.setStyleSheet("font-size: 9pt; color: #666; padding-left: 20px;")
        prefs_layout.addWidget(surrounding_info)

        prefs_layout.addSpacing(10)

        # --- Batch Translation ---
        batch_header = QLabel(self.tr("<b>Batch Translation:</b>"))
        prefs_layout.addWidget(batch_header)

        batch_size_layout = QHBoxLayout()
        batch_size_label = QLabel(self.tr("Batch size:"))
        batch_size_layout.addWidget(batch_size_label)
        batch_size_spin = QSpinBox()
        batch_size_spin.setMinimum(1)
        batch_size_spin.setMaximum(500)
        batch_size_spin.setValue(batch_size)
        batch_size_spin.setToolTip(self.tr("Larger batches = faster but higher API cost per call"))
        batch_size_layout.addWidget(batch_size_spin)
        batch_size_layout.addWidget(QLabel(self.tr("segments per API call")))
        batch_size_layout.addStretch()
        prefs_layout.addLayout(batch_size_layout)
        batch_size_info = QLabel(self.tr("  ⓘ Larger batches = faster but higher API cost. Default: 20"))
        batch_size_info.setStyleSheet("font-size: 9pt; color: #666; padding-left: 20px;")
        prefs_layout.addWidget(batch_size_info)

        prefs_layout.addSpacing(5)

        full_context_cb = CheckmarkCheckBox(self.tr("Include surrounding context in batch translation"))
        full_context_cb.setChecked(use_full_context)
        prefs_layout.addWidget(full_context_cb)

        # Context window size slider
        context_window_size = general_prefs.get('context_window_size', 50)
        context_layout = QHBoxLayout()
        context_label = QLabel(self.tr("  Context window:"))
        context_layout.addWidget(context_label)
        context_slider = QSlider(Qt.Orientation.Horizontal)
        context_slider.setMinimum(0)
        context_slider.setMaximum(200)
        context_slider.setValue(context_window_size)
        context_slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        context_slider.setTickInterval(25)
        context_slider.setEnabled(use_full_context)
        context_layout.addWidget(context_slider)
        context_value_label = QLabel(f"{context_window_size} segments")
        context_value_label.setMinimumWidth(100)
        context_layout.addWidget(context_value_label)
        context_layout.addStretch()
        prefs_layout.addLayout(context_layout)

        # Update label when slider changes
        def update_context_label(value):
            if value == 0:
                context_value_label.setText(self.tr("0 (disabled)"))
            else:
                total_segs = len(self.current_project.segments) if self.current_project else 300
                context_segs = min(value * 2 + batch_size_spin.value(), total_segs)
                percentage = int((context_segs / total_segs) * 100) if total_segs > 0 else 0
                context_value_label.setText(f"{value} seg (~{percentage}% of doc)")

        context_slider.valueChanged.connect(update_context_label)
        full_context_cb.stateChanged.connect(lambda: context_slider.setEnabled(full_context_cb.isChecked()))
        update_context_label(context_window_size)

        prefs_layout.addSpacing(10)

        # --- AI Cost Monitoring ---
        cost_header = QLabel(self.tr("<b>AI Cost Monitoring:</b>"))
        prefs_layout.addWidget(cost_header)

        self._usage_log_cb = CheckmarkCheckBox(self.tr("Keep a persistent token-usage log (for cost monitoring)"))
        self._usage_log_cb.setChecked(general_prefs.get('persist_usage_log', True))
        self._usage_log_cb.setToolTip(self.tr(
            "Append every AI call's tokens and cost to a monthly log file under your\n"
            "workbench/usage folder. Metadata only — never the prompt or response text.\n"
            "View it via Tools → Token Usage & Costs."))
        prefs_layout.addWidget(self._usage_log_cb)

        budget_row = QHBoxLayout()
        budget_row.addWidget(QLabel(self.tr("  Monthly budget (USD, 0 = none):")))
        self._budget_spin = QDoubleSpinBox()
        self._budget_spin.setDecimals(2)
        self._budget_spin.setMinimum(0.0)
        self._budget_spin.setMaximum(100000.0)
        self._budget_spin.setValue(float(general_prefs.get('monthly_budget_usd', 0.0) or 0.0))
        self._budget_spin.setToolTip(self.tr(
            "A soft monthly spend limit. When this month's logged AI cost reaches it,\n"
            "starting a batch translation warns you (it never blocks). 0 disables it."))
        budget_row.addWidget(self._budget_spin)
        budget_row.addStretch()
        prefs_layout.addLayout(budget_row)

        prefs_layout.addSpacing(10)

        # --- Translation Memory ---
        tm_header = QLabel(self.tr("<b>Translation Memory:</b>"))
        prefs_layout.addWidget(tm_header)

        # Get current settings
        check_tm_exact_only = general_prefs.get('check_tm_exact_only', False)

        # TM check before AI - applies to both single and batch
        tm_check_label = QLabel(self.tr("Check TM before calling AI (saves API costs):"))
        prefs_layout.addWidget(tm_check_label)

        # Radio buttons for TM check mode
        tm_no_check_rb = CheckmarkRadioButton(self.tr("Don't check TM - always call AI"))
        tm_fuzzy_rb = CheckmarkRadioButton(self.tr("Check TM first (including fuzzy matches)"))
        tm_exact_rb = CheckmarkRadioButton(self.tr("Check TM first (only 100% matches - faster)"))

        # Set initial state
        if not check_tm_before_api:
            tm_no_check_rb.setChecked(True)
        elif check_tm_exact_only:
            tm_exact_rb.setChecked(True)
        else:
            tm_fuzzy_rb.setChecked(True)

        prefs_layout.addWidget(tm_no_check_rb)
        prefs_layout.addWidget(tm_fuzzy_rb)
        prefs_layout.addWidget(tm_exact_rb)

        prefs_layout.addSpacing(5)

        # (The "Auto-propagate 100% TM matches" checkbox was removed here — its
        # behaviour is now consolidated into the single "Auto-fill empty segments
        # with 100% TM matches" setting on the General tab → TM settings.)

        # TM/Termbase lookup delay
        lookup_delay = general_prefs.get('lookup_delay', 1500)
        delay_layout = QHBoxLayout()
        delay_label = QLabel(self.tr("TM/Termbase lookup delay:"))
        delay_layout.addWidget(delay_label)
        delay_spin = QSpinBox()
        delay_spin.setMinimum(0)
        delay_spin.setMaximum(10000)
        delay_spin.setSingleStep(100)
        delay_spin.setValue(lookup_delay)
        delay_spin.setSuffix(" ms")
        delay_layout.addWidget(delay_spin)
        delay_layout.addStretch()
        prefs_layout.addLayout(delay_layout)
        delay_info = QLabel(self.tr("  ⓘ Prevents searches while navigating quickly. Default: 1500ms"))
        delay_info.setStyleSheet("font-size: 9pt; color: #666; padding-left: 20px;")
        prefs_layout.addWidget(delay_info)

        prefs_layout.addSpacing(10)

        # --- Document Context (for AI Prompts) ---
        ql_context_label = QLabel(self.tr("<b>Document Context (for QuickLauncher AI prompts):</b>"))
        prefs_layout.addWidget(ql_context_label)

        ql_context_percent = general_prefs.get('quicklauncher_context_percent',
                                               general_prefs.get('quickmenu_context_percent', 50))
        ql_context_layout = QHBoxLayout()
        ql_context_layout.addWidget(QLabel(self.tr("  Document context size:")))
        quicklauncher_context_slider = QSlider(Qt.Orientation.Horizontal)
        quicklauncher_context_slider.setMinimum(0)
        quicklauncher_context_slider.setMaximum(100)
        quicklauncher_context_slider.setValue(ql_context_percent)
        quicklauncher_context_slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        quicklauncher_context_slider.setTickInterval(10)
        ql_context_layout.addWidget(quicklauncher_context_slider)
        ql_context_value_label = QLabel(f"{ql_context_percent}%")
        ql_context_value_label.setMinimumWidth(60)
        ql_context_layout.addWidget(ql_context_value_label)
        ql_context_layout.addStretch()
        prefs_layout.addLayout(ql_context_layout)

        ql_context_info = QLabel(
            "  ⓘ When using {{SOURCE+TARGET_CONTEXT}} or {{SOURCE_CONTEXT}} placeholders in QuickLauncher prompts.\n"
            "  0% = disabled, 50% = half the document (default), 100% = entire document.\n"
            "  Limit: maximum 100 segments for performance."
        )
        ql_context_info.setStyleSheet("font-size: 9pt; color: #666; padding-left: 20px;")
        ql_context_info.setWordWrap(True)
        prefs_layout.addWidget(ql_context_info)

        def update_ql_context_label(value):
            if value == 0:
                ql_context_value_label.setText(self.tr("0% (disabled)"))
            else:
                ql_context_value_label.setText(f"{value}%")

        quicklauncher_context_slider.valueChanged.connect(update_ql_context_label)

        prefs_group.setLayout(prefs_layout)
        layout.addWidget(prefs_group)
        
        # ========== SECTION 6: AI Behavior Settings ==========
        behavior_group = QGroupBox(self.tr("🎯 AI Behavior Settings"))
        behavior_layout = QVBoxLayout()
        
        # LLM matching toggle
        llm_matching_cb = CheckmarkCheckBox(self.tr("Enable LLM (AI) matching on segment selection"))
        llm_matching_cb.setChecked(self.enable_llm_matching)
        llm_matching_cb.setToolTip(
            "⚠️ WARNING: LLM translations take 10-20 seconds per segment!\n\n"
            "When enabled, AI translations are automatically generated when you select a segment.\n"
            "RECOMMENDED: Keep DISABLED. Use 'Translate with AI' button instead."
        )
        behavior_layout.addWidget(llm_matching_cb)
        self.llm_matching_checkbox = llm_matching_cb

        # v1.10.155: "Auto-generate markdown for imported documents" moved to
        # Settings → ⚙️ General → 📥 Document Import. It's triggered at import
        # time (a filesystem write at document-open), so it belongs with
        # other import-time toggles, not under AI behaviour.

        # LLM match limits
        behavior_layout.addSpacing(10)
        llm_limits_label = QLabel(self.tr("<b>LLM Match Limits:</b>"))
        behavior_layout.addWidget(llm_limits_label)
        
        current_limits = general_settings.get('match_limits', {"LLM": 3, "MT": 3, "TM": 5, "Termbases": 10})
        
        llm_limit_layout = QHBoxLayout()
        llm_limit_layout.addWidget(QLabel(self.tr("🧠 Maximum LLM matches to display:")))
        llm_spin = QSpinBox()
        llm_spin.setRange(1, 10)
        llm_spin.setValue(current_limits.get("LLM", 3))
        llm_spin.setSuffix(" matches")
        llm_limit_layout.addWidget(llm_spin)
        llm_limit_layout.addStretch()
        behavior_layout.addLayout(llm_limit_layout)

        # FuzzyFixer thresholds
        behavior_layout.addSpacing(10)
        fuzzy_fixer_label = QLabel(self.tr("<b>🔧 FuzzyFixer:</b> AI adapts fuzzy TM matches to the current source"))
        fuzzy_fixer_header = QHBoxLayout()
        fuzzy_fixer_header.addWidget(fuzzy_fixer_label)
        fuzzy_fixer_header.addStretch()
        ff_help_btn = QPushButton("?")
        ff_help_btn.setFixedSize(22, 22)
        ff_help_btn.setToolTip(self.tr("Open FuzzyFixer help (online)"))
        ff_help_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        ff_help_btn.clicked.connect(lambda: open_help(HelpTopics.AI_FUZZY_FIXER))
        fuzzy_fixer_header.addWidget(ff_help_btn)
        behavior_layout.addLayout(fuzzy_fixer_header)

        fuzzy_range_layout = QHBoxLayout()
        fuzzy_range_layout.addWidget(QLabel(self.tr("Act on matches between")))
        self.fuzzy_fixer_min_spin = QSpinBox()
        self.fuzzy_fixer_min_spin.setRange(1, 99)
        self.fuzzy_fixer_min_spin.setSuffix("%")
        self.fuzzy_fixer_min_spin.setValue(getattr(self, 'fuzzy_fixer_min_pct', 75))
        self.fuzzy_fixer_min_spin.setToolTip(self.tr("Minimum match % the FuzzyFixer will repair (below this, nothing is offered)."))
        fuzzy_range_layout.addWidget(self.fuzzy_fixer_min_spin)
        fuzzy_range_layout.addWidget(QLabel(self.tr("and")))
        self.fuzzy_fixer_max_spin = QSpinBox()
        self.fuzzy_fixer_max_spin.setRange(1, 99)
        self.fuzzy_fixer_max_spin.setSuffix("%")
        self.fuzzy_fixer_max_spin.setValue(getattr(self, 'fuzzy_fixer_max_pct', 99))
        self.fuzzy_fixer_max_spin.setToolTip(self.tr("Maximum match % the FuzzyFixer will repair (100% exact matches are never altered)."))
        fuzzy_range_layout.addWidget(self.fuzzy_fixer_max_spin)
        fuzzy_range_layout.addStretch()
        behavior_layout.addLayout(fuzzy_range_layout)

        behavior_group.setLayout(behavior_layout)
        layout.addWidget(behavior_group)

        # ========== SAVE BUTTON ==========
        save_btn = QPushButton(self.tr("💾 Save AI Settings"))
        save_btn.setProperty("settings_save_button", True)  # clicked for the user by the auto-saver (issue #214)
        save_btn.setStyleSheet("font-weight: bold; padding: 8px; outline: none;")
        save_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        save_btn.clicked.connect(lambda: self._save_ai_settings_from_ui(
            openai_radio, claude_radio, gemini_radio, ollama_radio,
            openai_combo, claude_combo, gemini_combo,
            tab.ollama_status_widget,
            openai_enable_cb, claude_enable_cb, gemini_enable_cb, ollama_enable_cb,
            batch_size_spin, surrounding_spin, full_context_cb, context_slider,
            tm_no_check_rb, tm_fuzzy_rb, tm_exact_rb, None, delay_spin,
            ollama_keepwarm_cb,
            llm_matching_cb, llm_spin,
            quicklauncher_context_slider,
            custom_radio=custom_radio, custom_endpoint_input=custom_endpoint_input,
            custom_model_input=custom_model_input, custom_enable_cb=custom_enable_cb,
            custom_profile_combo=custom_profile_combo, custom_key_input=custom_key_input,
            mistral_radio=mistral_radio, mistral_combo=mistral_combo,
            mistral_enable_cb=mistral_enable_cb,
            deepseek_radio=deepseek_radio, deepseek_combo=deepseek_combo,
            openrouter_radio=openrouter_radio, openrouter_combo=openrouter_combo,
            openrouter_enable_cb=openrouter_enable_cb,
            ollama_timeout_spin=ollama_timeout_spin
        ))
        layout.addWidget(save_btn)
        
        layout.addStretch()
        
        return tab

    def _create_mt_settings_tab(self):
        """Create MT Settings tab content"""
        from PyQt6.QtWidgets import QCheckBox, QGroupBox, QPushButton
        
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(15)
        
        enabled_providers = self.load_provider_enabled_states()
        
        # Enable/Disable MT Providers
        mt_provider_group = QGroupBox(self.tr("Machine Translation Providers"))
        mt_provider_layout = QVBoxLayout()
        
        mt_info = QLabel(
            "Enable or disable individual MT providers. Supervertaler will use the first available enabled provider in priority order:\n"
            "1. Google Translate, 2. DeepL, 3. Microsoft Translator, 4. Amazon Translate, 5. ModernMT, 6. MyMemory"
        )
        mt_info.setWordWrap(True)
        mt_info.setStyleSheet("font-size: 9pt; color: #666; padding: 5px;")
        mt_provider_layout.addWidget(mt_info)
        
        google_translate_enable_cb = CheckmarkCheckBox(self.tr("Enable Google Translate"))
        google_translate_enable_cb.setChecked(enabled_providers.get('mt_google_translate', True))
        mt_provider_layout.addWidget(google_translate_enable_cb)
        
        deepl_enable_cb = CheckmarkCheckBox(self.tr("Enable DeepL"))
        deepl_enable_cb.setChecked(enabled_providers.get('mt_deepl', True))
        mt_provider_layout.addWidget(deepl_enable_cb)
        
        microsoft_enable_cb = CheckmarkCheckBox(self.tr("Enable Microsoft Translator"))
        microsoft_enable_cb.setChecked(enabled_providers.get('mt_microsoft', True))
        mt_provider_layout.addWidget(microsoft_enable_cb)
        
        amazon_enable_cb = CheckmarkCheckBox(self.tr("Enable Amazon Translate"))
        amazon_enable_cb.setChecked(enabled_providers.get('mt_amazon', True))
        mt_provider_layout.addWidget(amazon_enable_cb)
        
        modernmt_enable_cb = CheckmarkCheckBox(self.tr("Enable ModernMT"))
        modernmt_enable_cb.setChecked(enabled_providers.get('mt_modernmt', True))
        mt_provider_layout.addWidget(modernmt_enable_cb)
        
        mymemory_enable_cb = CheckmarkCheckBox(self.tr("Enable MyMemory (Free tier)"))
        mymemory_enable_cb.setChecked(enabled_providers.get('mt_mymemory', True))
        mt_provider_layout.addWidget(mymemory_enable_cb)
        
        mt_provider_group.setLayout(mt_provider_layout)
        layout.addWidget(mt_provider_group)
        
        # Inline MT API Keys
        mt_api_keys_group = QGroupBox(self.tr("🔑 MT API Keys"))
        mt_keys_form = QFormLayout()
        mt_keys_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        current_keys = self.load_api_keys()
        from modules.llm_clients import unreadable_key_notes
        key_notes = unreadable_key_notes()
        if not hasattr(self, '_api_key_inputs'):
            self._api_key_inputs = {}

        mt_key_fields = [
            ("Google Translate:", "google_translate", "AIza...", True),
            ("DeepL:", "deepl", "your-deepl-key", True),
            ("Microsoft Translate:", "microsoft_translate", "your-azure-key", True),
            ("Microsoft Region:", "microsoft_translate_region", "global", False),
            ("Amazon Access Key:", "amazon_translate", "AKIA...", True),
            ("Amazon Secret Key:", "amazon_translate_secret", "your-secret", True),
            ("Amazon Region:", "amazon_translate_region", "us-east-1", False),
            ("ModernMT:", "modernmt", "your-modernmt-key", True),
            ("MyMemory:", "mymemory", "your-mymemory-key", True),
        ]

        for label, key_name, placeholder, is_secret in mt_key_fields:
            row = QHBoxLayout()
            key_input = QLineEdit()
            key_input.setPlaceholderText(placeholder)
            key_input.setText(current_keys.get(key_name, ''))
            if is_secret:
                key_input.setEchoMode(QLineEdit.EchoMode.Password)
            row.addWidget(key_input)

            if is_secret:
                toggle_btn = QPushButton(self.tr("Show"))
                toggle_btn.setFixedWidth(50)
                toggle_btn.setCheckable(True)
                toggle_btn.clicked.connect(
                    lambda checked, inp=key_input, btn=toggle_btn:
                        (inp.setEchoMode(QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password),
                         btn.setText("Hide" if checked else "Show"))
                )
                row.addWidget(toggle_btn)

            container = QWidget()
            container.setLayout(row)
            mt_keys_form.addRow(label, self._with_key_note(container, key_name, key_input, key_notes))
            self._api_key_inputs[key_name] = key_input

        mt_api_keys_group.setLayout(mt_keys_form)
        layout.addWidget(mt_api_keys_group)
        
        # Save button
        save_btn = QPushButton(self.tr("💾 Save MT Settings"))
        save_btn.setProperty("settings_save_button", True)  # clicked for the user by the auto-saver (issue #214)
        save_btn.setStyleSheet("font-weight: bold; padding: 8px;")
        save_btn.clicked.connect(lambda: self._save_mt_settings_from_ui(
            google_translate_enable_cb, deepl_enable_cb, microsoft_enable_cb,
            amazon_enable_cb, modernmt_enable_cb, mymemory_enable_cb
        ))
        layout.addWidget(save_btn)
        
        layout.addStretch()

        return tab

    def _create_mt_quick_lookup_settings_tab(self):
        """Create MT Quick Lookup settings tab content"""
        from PyQt6.QtWidgets import QCheckBox, QGroupBox, QPushButton, QComboBox

        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(15)

        # Load current settings
        general_settings = self.load_general_settings()
        mt_quick_settings = general_settings.get('mt_quick_lookup', {})
        api_keys = self.load_api_keys()
        enabled_providers = self.load_provider_enabled_states()

        # Header info
        header_info = QLabel(
            f"⚡ <b>QuickTrans</b> - Configure which providers appear in the QuickTrans popup ({format_shortcut_for_display('Meta+Ctrl+Q') if IS_MACOS else format_shortcut_for_display('Ctrl+Alt+Q')} — system-wide hotkey, works inside Workbench too).<br>"
            "Enable MT engines and/or LLMs to get instant translation suggestions."
        )
        header_info.setTextFormat(Qt.TextFormat.RichText)
        header_info.setStyleSheet("font-size: 9pt; color: #444; padding: 10px; background-color: #E3F2FD; border-radius: 4px;")
        header_info.setWordWrap(True)
        layout.addWidget(header_info)

        # ===== MT Providers Group =====
        mt_group = QGroupBox(self.tr("🌐 Machine Translation Providers"))
        mt_layout = QVBoxLayout()

        mt_info = QLabel(self.tr("Select which MT engines to query. Only enabled providers with valid API keys are shown."))
        mt_info.setWordWrap(True)
        mt_info.setStyleSheet("font-size: 8pt; color: #666; padding-bottom: 8px;")
        mt_layout.addWidget(mt_info)

        # MT provider checkboxes
        self._mtql_checkboxes = {}

        mt_providers = [
            ("gt", "Google Translate", "mt_google_translate", "google_translate"),
            ("dl", "DeepL", "mt_deepl", "deepl"),
            ("ms", "Microsoft Translator", "mt_microsoft", "microsoft_translate"),
            ("at", "Amazon Translate", "mt_amazon", "amazon_translate"),
            ("mmt", "ModernMT", "mt_modernmt", "modernmt"),
            ("mm", "MyMemory (Free)", "mt_mymemory", None),
        ]

        for code, name, enabled_key, api_key_name in mt_providers:
            # Check if provider is available (has API key or doesn't need one)
            has_key = api_key_name is None or bool(api_keys.get(api_key_name))
            is_enabled_globally = enabled_providers.get(enabled_key, True)

            checkbox = CheckmarkCheckBox(name)
            # Default: use global MT enabled state
            checkbox.setChecked(mt_quick_settings.get(f"mtql_{code}", is_enabled_globally and has_key))
            checkbox.setEnabled(has_key)

            if not has_key:
                checkbox.setToolTip(f"API key not configured for {name}")
                checkbox.setStyleSheet("color: #999;")
            else:
                checkbox.setToolTip(f"Include {name} in QuickTrans results")

            self._mtql_checkboxes[f"mtql_{code}"] = checkbox
            mt_layout.addWidget(checkbox)

        # ----- Custom MT endpoint(s) -----
        # A dedicated OpenAI-compatible MT endpoint (e.g. a local MT proxy),
        # independent of the AI custom endpoint, so MT and AI can point at
        # different services at once. Each profile becomes its own QuickTrans
        # result. self._custom_mt_profiles is the source of truth so the save
        # handler can persist without closure/reload mismatches.
        from PyQt6.QtWidgets import QComboBox, QPushButton, QLineEdit, QHBoxLayout

        _mt_settings = self.load_llm_settings()
        self._custom_mt_profiles = [dict(p) for p in (_mt_settings.get('custom_mt_profiles') or [])]
        _active_mt_name = _mt_settings.get('custom_mt_active_profile', '')

        _cmt_header = QLabel(self.tr("Custom MT endpoint"))
        _cmt_header.setStyleSheet("font-weight: bold; padding-top: 8px;")
        mt_layout.addWidget(_cmt_header)

        cmt_enable = CheckmarkCheckBox(self.tr("Custom MT endpoint (OpenAI-compatible)"))
        cmt_enable.setChecked(mt_quick_settings.get("mtql_custom_mt", False))
        cmt_enable.setToolTip(
            "Query a custom OpenAI-compatible MT endpoint (e.g. a local MT proxy).\n"
            "Each profile below appears as its own QuickTrans result.")
        self._mtql_checkboxes["mtql_custom_mt"] = cmt_enable
        mt_layout.addWidget(cmt_enable)

        _prof_row = QHBoxLayout()
        _prof_lbl = QLabel(self.tr("Profile:")); _prof_lbl.setMinimumWidth(90)
        _prof_row.addWidget(_prof_lbl)
        self._custom_mt_profile_combo = QComboBox()
        self._custom_mt_profile_combo.setMinimumWidth(180)
        for _p in self._custom_mt_profiles:
            self._custom_mt_profile_combo.addItem(_p.get('name', 'Unnamed'))
        if _active_mt_name:
            _ai = self._custom_mt_profile_combo.findText(_active_mt_name)
            if _ai >= 0:
                self._custom_mt_profile_combo.setCurrentIndex(_ai)
        _prof_row.addWidget(self._custom_mt_profile_combo)
        _cmt_add_btn = QPushButton("+"); _cmt_add_btn.setFixedWidth(30)
        _cmt_add_btn.setToolTip(self.tr("Add a new Custom MT endpoint profile"))
        _cmt_del_btn = QPushButton("−"); _cmt_del_btn.setFixedWidth(30)
        _cmt_del_btn.setToolTip(self.tr("Remove the selected profile"))
        _prof_row.addWidget(_cmt_add_btn); _prof_row.addWidget(_cmt_del_btn)
        _prof_row.addStretch()
        mt_layout.addLayout(_prof_row)

        self._custom_mt_endpoint_input = QLineEdit()
        self._custom_mt_endpoint_input.setPlaceholderText(self.tr("http://127.0.0.1:1234/v1"))
        self._custom_mt_model_input = QLineEdit()
        self._custom_mt_model_input.setPlaceholderText(self.tr("model / engine name (e.g. google, sogou, cnpat)"))
        self._custom_mt_key_input = QLineEdit()
        self._custom_mt_key_input.setPlaceholderText(self.tr("API key for this endpoint (optional)"))
        self._custom_mt_key_input.setEchoMode(QLineEdit.EchoMode.Password)
        for _lbl_text, _w in (("Endpoint URL:", self._custom_mt_endpoint_input),
                              ("Model / engine:", self._custom_mt_model_input),
                              ("API key:", self._custom_mt_key_input)):
            _r = QHBoxLayout()
            _rl = QLabel(_lbl_text); _rl.setMinimumWidth(90)
            _r.addWidget(_rl); _r.addWidget(_w)
            mt_layout.addLayout(_r)

        self._custom_mt_show_cb = CheckmarkCheckBox(self.tr("Show this profile in QuickTrans"))
        self._custom_mt_show_cb.setChecked(True)
        self._custom_mt_show_cb.setToolTip(
            "Tick to include THIS profile as a result in the QuickTrans popup.\n"
            "Untick to keep the profile configured but hidden. (The 'Custom MT\n"
            "endpoint' checkbox above is the master on/off for all profiles.)")
        mt_layout.addWidget(self._custom_mt_show_cb)

        self._custom_mt_raw_cb = CheckmarkCheckBox(self.tr("Send raw text only (MT mode)"))
        self._custom_mt_raw_cb.setChecked(True)
        self._custom_mt_raw_cb.setToolTip(
            "On (recommended for MT proxies): send just the source text, with the\n"
            "language direction in the system message, so a bare MT endpoint never\n"
            "translates the instruction wrapper. Turn off if the endpoint is an\n"
            "instruction-following LLM that should see a full 'translate this' prompt.")
        mt_layout.addWidget(self._custom_mt_raw_cb)

        # Remove whitespace AND control/non-printable characters (e.g. a stray
        # NUL pasted into a field, which makes requests reject the URL).
        def _cmt_clean(s):
            return ''.join(ch for ch in (s or '') if ord(ch) >= 0x20).strip()

        def _cmt_populate(index):
            if 0 <= index < len(self._custom_mt_profiles):
                _pp = self._custom_mt_profiles[index]
                self._custom_mt_endpoint_input.setText(_pp.get('endpoint', ''))
                self._custom_mt_model_input.setText(_pp.get('model', ''))
                self._custom_mt_key_input.setText(_pp.get('api_key', ''))
                self._custom_mt_raw_cb.setChecked(bool(_pp.get('raw_mode', True)))
                self._custom_mt_show_cb.setChecked(bool(_pp.get('enabled', True)))
            else:
                self._custom_mt_endpoint_input.clear()
                self._custom_mt_model_input.clear()
                self._custom_mt_key_input.clear()
                self._custom_mt_raw_cb.setChecked(True)
                self._custom_mt_show_cb.setChecked(True)

        def _cmt_sync():
            _i = self._custom_mt_profile_combo.currentIndex()
            if 0 <= _i < len(self._custom_mt_profiles):
                self._custom_mt_profiles[_i]['endpoint'] = _cmt_clean(self._custom_mt_endpoint_input.text())
                self._custom_mt_profiles[_i]['model'] = _cmt_clean(self._custom_mt_model_input.text())
                self._custom_mt_profiles[_i]['api_key'] = _cmt_clean(self._custom_mt_key_input.text())
                self._custom_mt_profiles[_i]['raw_mode'] = self._custom_mt_raw_cb.isChecked()
                self._custom_mt_profiles[_i]['enabled'] = self._custom_mt_show_cb.isChecked()

        def _cmt_add():
            from PyQt6.QtWidgets import QInputDialog, QMessageBox
            name, ok = QInputDialog.getText(
                self, "New Custom MT Profile",
                "Profile name (e.g. 'Local proxy', 'CNPAT patent'):")
            if ok and name.strip():
                name = name.strip()
                if any(p.get('name') == name for p in self._custom_mt_profiles):
                    QMessageBox.warning(self, "Duplicate Name", f"A profile named '{name}' already exists.")
                    return
                self._custom_mt_profiles.append({'name': name, 'endpoint': '', 'model': '', 'api_key': '', 'raw_mode': True, 'enabled': True})
                self._custom_mt_profile_combo.addItem(name)
                self._custom_mt_profile_combo.setCurrentIndex(self._custom_mt_profile_combo.count() - 1)

        def _cmt_del():
            from PyQt6.QtWidgets import QMessageBox
            _i = self._custom_mt_profile_combo.currentIndex()
            if _i < 0 or _i >= len(self._custom_mt_profiles):
                return
            name = self._custom_mt_profile_combo.currentText()
            if QMessageBox.question(
                    self, "Remove Profile", f"Remove profile '{name}'?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            ) == QMessageBox.StandardButton.Yes:
                self._custom_mt_profiles.pop(_i)
                self._custom_mt_profile_combo.removeItem(_i)

        self._custom_mt_profile_combo.currentIndexChanged.connect(_cmt_populate)
        self._custom_mt_endpoint_input.editingFinished.connect(_cmt_sync)
        self._custom_mt_model_input.editingFinished.connect(_cmt_sync)
        self._custom_mt_key_input.editingFinished.connect(_cmt_sync)
        self._custom_mt_raw_cb.toggled.connect(lambda _checked: _cmt_sync())
        self._custom_mt_show_cb.toggled.connect(lambda _checked: _cmt_sync())
        _cmt_add_btn.clicked.connect(_cmt_add)
        _cmt_del_btn.clicked.connect(_cmt_del)
        if self._custom_mt_profile_combo.count() > 0:
            _cmt_populate(self._custom_mt_profile_combo.currentIndex())

        mt_group.setLayout(mt_layout)
        layout.addWidget(mt_group)

        # ===== LLM Providers Group =====
        llm_group = QGroupBox(self.tr("🤖 AI/LLM Providers"))
        llm_layout = QVBoxLayout()

        llm_info = QLabel(
            "Enable AI models for translation suggestions. LLMs may provide more context-aware translations but are slower.<br>"
            "<b>Note:</b> LLM calls cost more than MT APIs. Use sparingly for quick lookups."
        )
        llm_info.setTextFormat(Qt.TextFormat.RichText)
        llm_info.setWordWrap(True)
        llm_info.setStyleSheet("font-size: 8pt; color: #666; padding-bottom: 8px;")
        llm_layout.addWidget(llm_info)

        # Popup AI mode: auto-fetch the enabled AI providers when the QuickTrans
        # popup opens (Ctrl+Shift+Q in-app and Ctrl+Alt+Q system-wide), or show
        # them as on-demand "Fetch" buttons so no billable AI call fires until
        # you ask. (The docked QuickTrans panel always uses Fetch buttons.)
        popup_ai_cb = CheckmarkCheckBox(self.tr("Auto-fetch AI providers in the popup (otherwise show 'Fetch' buttons)"))
        popup_ai_cb.setChecked(mt_quick_settings.get("mtql_popup_ai_autofetch", True))
        popup_ai_cb.setToolTip(
            "On: the QuickTrans popup queries the enabled AI/LLM providers automatically\n"
            "    (costs apply) – including the system-wide Ctrl+Alt+Q popup.\n"
            "Off: AI providers appear as 'Fetch' buttons in the popup; click to call one.")
        self._mtql_checkboxes["mtql_popup_ai_autofetch"] = popup_ai_cb
        llm_layout.addWidget(popup_ai_cb)

        # LLM provider checkboxes with model selection
        self._mtql_llm_combos = {}

        llm_providers = [
            ("claude", "Claude", "claude", [
                ("claude-sonnet-5-5", "Claude Sonnet 5.5 (Recommended)"),
                ("claude-opus-5-5", "Claude Opus 5.5 (Premium)"),
                ("claude-fable-5-2", "Claude Fable 5.2 (Maximum)"),
            ]),
            ("openai", "OpenAI", "openai", [
                ("gpt-6-astra", "GPT-6 Astra (Premium)"),
                ("gpt-6.1-sol", "GPT-6.1 Sol (Recommended)"),
                ("gpt-6-luna", "GPT-6 Luna (Budget)"),
            ]),
            ("gemini", "Gemini (Google AI)", "gemini", [
                ("gemini-3.1-flash-lite", "Gemini 3.1 Flash-Lite (Recommended)"),
                ("gemini-2.5-pro", "Gemini 2.5 Pro (Premium)"),
                ("gemini-3.1-pro-preview", "Gemini 3.1 Pro (Previous Pro)"),
                ("gemini-4-argon", "Gemini 4 Argon (Top Quality)"),
                ("gemma-4-26b-a4b-it", "Gemma 4 26B MoE (Open)"),
            ]),
            ("mistral", "Mistral AI", "mistral", [
                ("mistral-large-latest", "Mistral Large (Recommended)"),
                ("mistral-small-latest", "Mistral Small (Fast)"),
            ]),
            ("deepseek", "DeepSeek", "deepseek", [
                ("deepseek-v4-pro", "DeepSeek V4 Pro (Recommended)"),
                ("deepseek-v4-flash", "DeepSeek V4 Flash (Fast)"),
            ]),
            # The same models as LLMClient.OPENROUTER_MODELS, in the same order.
            ("openrouter", "OpenRouter (200+ models)", "openrouter", [
                ("anthropic/claude-sonnet-5.5", "Claude Sonnet 5.5 (Recommended)"),
                ("anthropic/claude-opus-5.5", "Claude Opus 5.5 (Premium)"),
                ("anthropic/claude-fable-5.2", "Claude Fable 5.2 (Maximum)"),
                ("openai/gpt-6-astra", "GPT-6 Astra (OpenAI's most capable)"),
                ("openai/gpt-6.1-sol", "GPT-6.1 Sol (OpenAI recommended)"),
                ("openai/gpt-6-luna", "GPT-6 Luna (OpenAI's cheapest)"),
                ("google/gemini-3.8-flash", "Gemini 3.8 Flash (Fast Gemini)"),
                ("google/gemini-4-argon", "Gemini 4 Argon (Smartest Gemini)"),
                ("google/gemma-4-31b-it", "Gemma 4 31B (Open)"),
                ("google/gemma-4-26b-a4b-it", "Gemma 4 26B MoE (Open)"),
                ("mistralai/mistral-small-2603", "Mistral Small 4 (European Languages)"),
                ("qwen/qwen3.8-27b:free", "Qwen 3.8 27B (Free)"),
                ("deepseek/deepseek-v4-pro", "DeepSeek V4 Pro"),
                ("deepseek/deepseek-v4-flash", "DeepSeek V4 Flash (Fast)"),
            ]),
        ]

        for code, name, api_key_name, models in llm_providers:
            # Gemini key may be stored as either 'gemini' or 'google' (backward compat)
            if api_key_name == 'gemini':
                has_key = bool(api_keys.get('gemini') or api_keys.get('google'))
            else:
                has_key = bool(api_keys.get(api_key_name))

            # Container for checkbox and model combo
            llm_row = QHBoxLayout()

            checkbox = CheckmarkCheckBox(name)
            # Default: disabled (LLMs are opt-in)
            checkbox.setChecked(mt_quick_settings.get(f"mtql_{code}", False))
            checkbox.setEnabled(has_key)

            if not has_key:
                checkbox.setToolTip(f"API key not configured for {name}. Add it in AI Settings.")
                checkbox.setStyleSheet("color: #999;")
            else:
                checkbox.setToolTip(f"Include {name} translations in QuickTrans")

            self._mtql_checkboxes[f"mtql_{code}"] = checkbox
            llm_row.addWidget(checkbox)

            # Model selection combo
            model_combo = QComboBox()
            model_combo.setMinimumWidth(200)
            for model_id, model_name in models:
                model_combo.addItem(model_name, model_id)

            # Restore saved model selection
            # A saved model that has left the list is shown rather than replaced:
            # falling back to the first entry meant the next save overwrote it.
            saved_model = mt_quick_settings.get(f"mtql_{code}_model")
            if saved_model:
                idx = model_combo.findData(saved_model)
                if idx < 0:
                    model_combo.addItem(f"{saved_model} (your current model)", saved_model)
                    idx = model_combo.count() - 1
                model_combo.setCurrentIndex(idx)

            model_combo.setEnabled(has_key)
            self._mtql_llm_combos[f"mtql_{code}_model"] = model_combo
            llm_row.addWidget(model_combo)

            llm_row.addStretch()
            llm_layout.addLayout(llm_row)

        # Custom OpenAI-compatible provider (shows active profile info)
        custom_row = QHBoxLayout()
        custom_cb = CheckmarkCheckBox(self.tr("Custom (OpenAI-Compatible)"))
        custom_cb.setChecked(mt_quick_settings.get("mtql_custom_openai", False))
        custom_cb.setToolTip(self.tr("Use your custom OpenAI-compatible endpoint (configured in AI Settings)"))
        self._mtql_checkboxes["mtql_custom_openai"] = custom_cb
        custom_row.addWidget(custom_cb)

        llm_settings = self.load_llm_settings()
        profile = self._get_active_custom_profile(llm_settings)
        profile_display = ""
        if profile:
            profile_display = f"{profile.get('name', 'Custom')} – {profile.get('model', '')}"
        else:
            profile_display = mt_quick_settings.get("mtql_custom_openai_model", llm_settings.get('custom_openai_model', ''))

        custom_model_edit = QLineEdit()
        custom_model_edit.setMinimumWidth(200)
        custom_model_edit.setPlaceholderText(self.tr("Uses active profile from AI Settings"))
        custom_model_edit.setText(profile_display)
        custom_model_edit.setReadOnly(True)
        custom_model_edit.setStyleSheet("color: #666; background: #f5f5f5;")
        self._mtql_llm_combos["mtql_custom_openai_model"] = custom_model_edit
        custom_row.addWidget(custom_model_edit)
        custom_row.addStretch()
        llm_layout.addLayout(custom_row)

        # Ollama (local LLM – always available, no API key needed)
        ollama_row = QHBoxLayout()
        ollama_cb = CheckmarkCheckBox(self.tr("🖥️ Ollama (Local LLM)"))
        ollama_cb.setChecked(mt_quick_settings.get("mtql_ollama", False))
        ollama_cb.setToolTip(self.tr("Use a locally-running Ollama model for translation (no API key needed)"))
        self._mtql_checkboxes["mtql_ollama"] = ollama_cb
        ollama_row.addWidget(ollama_cb)

        ollama_model_edit = QLineEdit()
        ollama_model_edit.setMinimumWidth(200)
        ollama_model_edit.setPlaceholderText(self.tr("e.g. translategemma:12b"))
        _ollama_model_saved = mt_quick_settings.get("mtql_ollama_model") or llm_settings.get('ollama_model', 'translategemma:12b')
        ollama_model_edit.setText(_ollama_model_saved)
        ollama_model_edit.setToolTip(self.tr("Ollama model name – must be pulled locally (ollama pull <model>)"))
        self._mtql_llm_combos["mtql_ollama_model"] = ollama_model_edit
        ollama_row.addWidget(ollama_model_edit)
        ollama_row.addStretch()
        llm_layout.addLayout(ollama_row)

        llm_group.setLayout(llm_layout)
        layout.addWidget(llm_group)

        # Save button
        save_btn = QPushButton(self.tr("💾 Save QuickTrans Settings"))
        save_btn.setProperty("settings_save_button", True)  # clicked for the user by the auto-saver (issue #214)
        save_btn.setStyleSheet("font-weight: bold; padding: 8px;")
        save_btn.clicked.connect(self._save_mt_quick_lookup_settings)
        layout.addWidget(save_btn)

        layout.addStretch()

        return tab

    def _save_mt_quick_lookup_settings(self):
        """Save MT Quick Lookup settings"""
        general_settings = self.load_general_settings()

        mt_quick_settings = {}

        # Save MT provider states
        for key, checkbox in self._mtql_checkboxes.items():
            mt_quick_settings[key] = checkbox.isChecked()

        # Save LLM model selections
        for key, widget in self._mtql_llm_combos.items():
            if isinstance(widget, QLineEdit):
                mt_quick_settings[key] = widget.text().strip()
            else:
                mt_quick_settings[key] = widget.currentData()

        general_settings['mt_quick_lookup'] = mt_quick_settings
        self.save_general_settings(general_settings)

        # Persist Custom MT endpoint profiles to llm_settings (kept separate from
        # the AI custom endpoint). The enable flag itself rides in mt_quick_settings
        # via self._mtql_checkboxes['mtql_custom_mt'] above.
        if getattr(self, '_custom_mt_profile_combo', None) is not None:
            idx = self._custom_mt_profile_combo.currentIndex()
            if 0 <= idx < len(self._custom_mt_profiles):
                _clean = lambda s: ''.join(ch for ch in (s or '') if ord(ch) >= 0x20).strip()
                self._custom_mt_profiles[idx]['endpoint'] = _clean(self._custom_mt_endpoint_input.text())
                self._custom_mt_profiles[idx]['model'] = _clean(self._custom_mt_model_input.text())
                self._custom_mt_profiles[idx]['api_key'] = _clean(self._custom_mt_key_input.text())
                self._custom_mt_profiles[idx]['raw_mode'] = self._custom_mt_raw_cb.isChecked()
                self._custom_mt_profiles[idx]['enabled'] = self._custom_mt_show_cb.isChecked()
            llm_settings = self.load_llm_settings()
            llm_settings['custom_mt_profiles'] = self._custom_mt_profiles
            llm_settings['custom_mt_active_profile'] = self._custom_mt_profile_combo.currentText()
            self.save_llm_settings(llm_settings)

        self.log("✓ QuickTrans settings saved")
        QMessageBox.information(self, "Settings Saved", "QuickTrans settings have been saved.")

    def open_mt_quick_lookup_settings(self):
        """Open Settings → ⚡ QuickTrans and bring Workbench forward.

        Called by the gear icon in the QuickTrans always-on-top popup.
        Pre-v1.10.11 this only navigated tabs, on the assumption that
        Workbench was already foreground – but the QuickTrans popup
        often fires from another app (Trados) which retains OS-level
        foreground while the always-on-top popup floats over it.
        Closing the popup and switching tabs in that state leaves
        Workbench updated but invisible behind the source app. v1.10.11
        routes through _bring_workbench_forward() so the same hammer
        chain that makes Ctrl+Alt+C / Ctrl+Alt+L reliable applies here
        too (synthetic Alt-key + AttachThreadInput + BringWindowToTop
        + SetForegroundWindow + SwitchToThisWindow).
        """
        try:
            self._bring_workbench_forward()
        except Exception:
            pass

        # Switch to Settings tab
        if hasattr(self, 'main_tabs'):
            # Find Settings tab index
            for i in range(self.main_tabs.count()):
                if "Settings" in self.main_tabs.tabText(i):
                    self.main_tabs.setCurrentIndex(i)
                    break

            # Navigate to MT Quick Lookup sub-tab
            if hasattr(self, 'settings_tabs') and hasattr(self, 'mt_quick_lookup_tab_index'):
                self.settings_tabs.setCurrentIndex(self.mt_quick_lookup_tab_index)

    def _find_autohotkey_for_settings(self):
        """Find AutoHotkey executable for settings display (doesn't modify state)"""
        # Standard installation paths
        username = os.environ.get('USERNAME', '')
        ahk_paths = [
            r"C:\Program Files\AutoHotkey\v2\AutoHotkey.exe",
            r"C:\Program Files\AutoHotkey\v2\AutoHotkey64.exe",
            r"C:\Program Files\AutoHotkey\AutoHotkey.exe",
            r"C:\Program Files (x86)\AutoHotkey\AutoHotkey.exe",
            fr"C:\Users\{username}\AppData\Local\Programs\AutoHotkey\AutoHotkey.exe",
        ]
        
        for path in ahk_paths:
            if os.path.exists(path):
                return path, 'detected'
        
        return None, None

    def _browse_autohotkey_for_settings(self, line_edit):
        """Browse for AutoHotkey executable and update line edit"""
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Locate AutoHotkey Executable",
            "C:\\Program Files",
            "Executable Files (*.exe);;All Files (*.*)"
        )
        
        if file_path:
            line_edit.setText(file_path)

    def _create_backup_settings_tab(self):
        """Settings → 💾 Backup tab.

        Hosts both the time-based auto-backup (overwrites the working .svproj +
        TMX on a timer) and the every-N-saves *timestamped* project backups
        (immutable snapshots in <user_data>/workbench/backups/). Moved here from
        the General tab so users can find them.
        """
        general_settings = self.load_general_settings()

        tab = QWidget()
        layout = QVBoxLayout(tab)

        # Contextual help "?" at the top-right → online Backup docs (and F1).
        help_row = QHBoxLayout()
        help_row.addStretch()
        backup_help_btn = QPushButton("?")
        backup_help_btn.setFixedSize(22, 22)
        backup_help_btn.setToolTip(self.tr("Open Backup help (online)"))
        backup_help_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        backup_help_btn.clicked.connect(lambda: open_help(HelpTopics.SETTINGS_BACKUP))
        help_row.addWidget(backup_help_btn)
        layout.addLayout(help_row)

        # ── Time-based auto-backup ──────────────────────────────────────
        backup_group = QGroupBox(self.tr("💾 Auto Backup Settings"))
        backup_layout = QVBoxLayout()

        backup_info = QLabel(
            "Supervertaler can automatically back up your project at regular intervals\n"
            "to prevent data loss. Both a project.json and TMX file will be saved."
        )
        backup_info.setWordWrap(True)
        backup_info.setStyleSheet("color: #666; font-size: 9pt; padding: 5px;")
        backup_layout.addWidget(backup_info)

        enable_backup_cb = CheckmarkCheckBox(self.tr("Enable automatic backups"))
        enable_backup_cb.setChecked(general_settings.get('enable_auto_backup', True))
        enable_backup_cb.setToolTip(
            "When enabled, Supervertaler will automatically save your project and export\n"
            "a TMX file at the configured interval. This helps prevent data loss."
        )
        backup_layout.addWidget(enable_backup_cb)

        interval_layout = QHBoxLayout()
        interval_layout.addWidget(QLabel(self.tr("Backup interval:")))
        backup_interval_spin = QSpinBox()
        backup_interval_spin.setRange(1, 60)
        backup_interval_spin.setValue(general_settings.get('backup_interval_minutes', 5))
        backup_interval_spin.setSuffix(" minutes")
        backup_interval_spin.setToolTip(
            "Set how often Supervertaler should automatically save your project.\n"
            "Default: 5 minutes. Range: 1-60 minutes."
        )
        interval_layout.addWidget(backup_interval_spin)
        interval_layout.addStretch()
        backup_layout.addLayout(interval_layout)
        backup_group.setLayout(backup_layout)
        layout.addWidget(backup_group)

        # ── Versioned (every-N-saves) timestamped backups ───────────────
        versioned_group = QGroupBox(self.tr("🕑 Timestamped project backups"))
        versioned_layout = QVBoxLayout()

        versioned_info = QLabel(
            "Keep immutable, timestamped copies of the project file (.svproj) so you\n"
            "can roll back to an earlier state. These are separate from the auto-backup\n"
            "above (which overwrites the working file)."
        )
        versioned_info.setWordWrap(True)
        versioned_info.setStyleSheet("color: #666; font-size: 9pt; padding: 5px;")
        versioned_layout.addWidget(versioned_info)

        versioned_cb = CheckmarkCheckBox(self.tr("Keep timestamped project backups"))
        versioned_cb.setChecked(general_settings.get('enable_versioned_backup', True))
        versioned_cb.setToolTip(
            "Every N saves, keep a timestamped copy of the .svproj file in a\n"
            "dedicated backups folder, so you can roll back to an earlier state.")
        versioned_layout.addWidget(versioned_cb)

        vrow = QHBoxLayout()
        vrow.addWidget(QLabel(self.tr("Back up every")))
        versioned_n_spin = QSpinBox()
        versioned_n_spin.setRange(1, 100)
        versioned_n_spin.setValue(general_settings.get('versioned_backup_every_n_saves', 1))
        versioned_n_spin.setSuffix(self.tr(" save(s)"))
        versioned_n_spin.setToolTip("How often to drop a timestamped backup, counted in save operations\n"
                                    "(manual saves and timed auto-saves both count).")
        vrow.addWidget(versioned_n_spin)
        vrow.addSpacing(16)
        vrow.addWidget(QLabel(self.tr("keep the last")))
        versioned_keep_spin = QSpinBox()
        versioned_keep_spin.setRange(1, 1000)
        versioned_keep_spin.setValue(general_settings.get('versioned_backup_keep', 100))
        versioned_keep_spin.setSuffix(self.tr(" backups"))
        versioned_keep_spin.setToolTip("Older backups beyond this count are pruned automatically (per project).")
        vrow.addWidget(versioned_keep_spin)
        vrow.addStretch()
        versioned_layout.addLayout(vrow)

        frow = QHBoxLayout()
        _backups_dir = str(Path(self.user_data_path) / "workbench" / "backups")
        backups_path_lbl = QLabel(_backups_dir)
        backups_path_lbl.setStyleSheet("color:#888; font-size:8pt;")
        backups_path_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        frow.addWidget(backups_path_lbl, 1)
        open_backups_btn = QPushButton(self.tr("Open folder…"))
        open_backups_btn.clicked.connect(
            lambda: (Path(_backups_dir).mkdir(parents=True, exist_ok=True), open_folder(_backups_dir)))
        frow.addWidget(open_backups_btn)
        versioned_layout.addLayout(frow)

        versioned_group.setLayout(versioned_layout)
        layout.addWidget(versioned_group)

        layout.addStretch()

        # Store references (also read by _save_general_settings_from_ui, which
        # still owns auto-backup persistence when General's Save is clicked).
        self.enable_backup_cb = enable_backup_cb
        self.backup_interval_spin = backup_interval_spin
        self.enable_versioned_backup_cb = versioned_cb
        self.versioned_backup_n_spin = versioned_n_spin
        self.versioned_backup_keep_spin = versioned_keep_spin

        # Live-save on any change (no Save button — matches the AutoCorrect tab).
        enable_backup_cb.toggled.connect(lambda _=False: self._live_save_backup_settings())
        versioned_cb.toggled.connect(lambda _=False: self._live_save_backup_settings())
        backup_interval_spin.valueChanged.connect(lambda _=0: self._live_save_backup_settings())
        versioned_n_spin.valueChanged.connect(lambda _=0: self._live_save_backup_settings())
        versioned_keep_spin.valueChanged.connect(lambda _=0: self._live_save_backup_settings())

        set_help_topic(tab, HelpTopics.SETTINGS_BACKUP)
        return tab

    def _live_save_backup_settings(self):
        """Persist the Backup tab's settings immediately (live-save), so the
        user doesn't have to hunt for a Save button. Updates only the backup
        keys in the general settings section and restarts the auto-backup timer."""
        try:
            gs = self.load_general_settings()
            gs['enable_auto_backup'] = self.enable_backup_cb.isChecked()
            gs['backup_interval_minutes'] = self.backup_interval_spin.value()
            gs['enable_versioned_backup'] = self.enable_versioned_backup_cb.isChecked()
            gs['versioned_backup_every_n_saves'] = self.versioned_backup_n_spin.value()
            gs['versioned_backup_keep'] = self.versioned_backup_keep_spin.value()
            self.save_general_settings(gs)
            self.restart_auto_backup_timer()
        except Exception as e:
            self.log(f"⚠ Could not save backup settings: {e}")

    def _create_general_settings_tab(self):
        """Create General Settings tab content"""
        from PyQt6.QtWidgets import QCheckBox, QGroupBox, QPushButton, QLineEdit, QFileDialog

        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(15)
        
        general_settings = self.load_general_settings()
        
        # Startup Settings group
        startup_group = QGroupBox(self.tr("Startup Settings"))
        startup_layout = QVBoxLayout()
        
        restore_last_project_cb = CheckmarkCheckBox(self.tr("Restore last project on startup"))
        restore_last_project_cb.setChecked(general_settings.get('restore_last_project', False))
        restore_last_project_cb.setToolTip(
            self.tr("When enabled, Supervertaler will automatically open the last project you were working on when the application starts.")
        )
        startup_layout.addWidget(restore_last_project_cb)
        
        auto_open_log_cb = CheckmarkCheckBox(self.tr("Auto-open log window on startup (detached)"))
        auto_open_log_cb.setChecked(general_settings.get('auto_open_log', False))
        auto_open_log_cb.setToolTip(
            self.tr("When enabled, the log window will automatically open in a separate detached window when Supervertaler starts.")
        )
        startup_layout.addWidget(auto_open_log_cb)
        
        startup_group.setLayout(startup_layout)
        layout.addWidget(startup_group)

        # ─── Language (v1.10.207: MVP i18n infrastructure) ───
        # Combo-box only translates the menu bar, toolbar and Settings
        # chrome at this stage – the bulk of dialog body text remains in
        # English until subsequent passes wrap more strings. A restart is
        # required because most widgets are hand-coded and don't currently
        # respond to QEvent.LanguageChange; live re-translation is a
        # follow-up project. The dropdown is populated from
        # modules.i18n.SUPPORTED_LOCALES, which is the canonical list of
        # locale codes the app recognises (regardless of whether a .xlf
        # file actually exists for each).
        from modules.i18n import (
            SUPPORTED_LOCALES,
            TRANSLATION_FILE_SUFFIX,
            translations_dir,
        )
        from PyQt6.QtWidgets import QComboBox

        language_group = QGroupBox(self.tr("🌐 Language"))
        language_layout = QVBoxLayout()

        language_info = QLabel(
            "Interface language. Translations cover the menu bar, toolbar "
            "and Settings labels in this initial pass — dialog bodies and "
            "error messages remain in English for now. Available languages "
            "depend on which translation files have been contributed."
        )
        language_info.setWordWrap(True)
        language_info.setStyleSheet("color: #666; font-size: 9pt;")
        language_layout.addWidget(language_info)

        language_row = QHBoxLayout()
        language_row.addWidget(QLabel(self.tr("Display language:")))

        language_combo = QComboBox()
        language_combo.setToolTip(
            "Restart Supervertaler after changing the language for the new "
            "translations to take effect."
        )
        # "System" entry is the implicit default for new installs – uses
        # whatever QLocale.system() reports, falling back to English when
        # no .xlf file exists for that locale.
        language_combo.addItem("System default", "system")

        # Discover which .xlf files actually exist on disk so we can mark
        # locales without a translation file as "[no translation yet]".
        # v1.10.208 migrated from .ts (Qt Linguist) to .xlf (XLIFF 1.2) for
        # broader CAT-tool compatibility – Trados / memoQ / Phrase / OmegaT
        # / Workbench itself all eat XLIFF natively, where .ts is Qt-only.
        _xlf_dir = translations_dir()
        _available_xlf_codes = set()
        if _xlf_dir.exists():
            for _xlf in _xlf_dir.glob(f"supervertaler_*{TRANSLATION_FILE_SUFFIX}"):
                _code = _xlf.stem.replace("supervertaler_", "")
                _available_xlf_codes.add(_code)

        for _code, _label in SUPPORTED_LOCALES:
            if _code == "en":
                # English needs no .xlf file – it's the source language.
                language_combo.addItem(_label, _code)
            elif _code in _available_xlf_codes:
                language_combo.addItem(_label, _code)
            else:
                language_combo.addItem(f"{_label}  [no translation yet]", _code)

        _current_locale = general_settings.get('ui_locale', 'system')
        _idx = language_combo.findData(_current_locale)
        if _idx >= 0:
            language_combo.setCurrentIndex(_idx)
        language_row.addWidget(language_combo, stretch=1)
        language_layout.addLayout(language_row)

        # Restart-required notice + Save button. The save handler stashes
        # the choice in general.ui_locale; main() reads it on next launch.
        language_status = QLabel("")
        language_status.setStyleSheet("color: #B00; font-size: 9pt;")
        language_status.setWordWrap(True)
        language_layout.addWidget(language_status)

        def _on_language_changed(_index):
            new_code = language_combo.currentData()
            prefs = self.load_general_settings()
            prev = prefs.get('ui_locale', 'system')
            prefs['ui_locale'] = new_code
            try:
                self._save_settings_section('general', prefs)
            except Exception as save_err:
                self.log(f"⚠ Could not save language preference: {save_err}")
                return
            if new_code != prev:
                language_status.setText(
                    self.tr("↻ Restart Supervertaler for the new language to take effect.")
                )
            else:
                language_status.setText("")

        language_combo.currentIndexChanged.connect(_on_language_changed)
        language_group.setLayout(language_layout)
        # F1 anywhere inside the group opens the Language help page
        # (v1.10.208 i18n MVP – explains the .xlf workflow, file location,
        # and how to contribute translations).
        try:
            set_help_topic(language_group, HelpTopics.SETTINGS_LANGUAGE)
        except Exception:
            pass
        layout.addWidget(language_group)

        # Usage Statistics group
        stats_group = QGroupBox(self.tr("Privacy"))
        stats_layout = QVBoxLayout()

        # Load current setting from unified settings file
        from modules.usage_statistics import is_opted_in
        settings_path = self._get_unified_settings_path()

        usage_stats_cb = CheckmarkCheckBox(self.tr("Share anonymous usage statistics (no personal data)"))
        usage_stats_cb.setChecked(is_opted_in(settings_path))
        usage_stats_cb.setToolTip(
            "Sends a single anonymous ping on startup (app version, OS, Python version, locale).\n"
            "No personal data, translation content, or termbase info is ever collected."
        )

        def _on_usage_stats_toggled(checked):
            from modules.usage_statistics import set_opted_in
            set_opted_in(self._get_unified_settings_path(), checked)
            self.log(f"📊 Usage statistics {'enabled' if checked else 'disabled'}.")

        usage_stats_cb.toggled.connect(_on_usage_stats_toggled)
        stats_layout.addWidget(usage_stats_cb)

        stats_group.setLayout(stats_layout)
        layout.addWidget(stats_group)

        # Data Folder Location group
        data_folder_group = QGroupBox(self.tr("📁 Data Folder Location"))
        data_folder_layout = QVBoxLayout()
        
        data_folder_info = QLabel(
            self.tr("Your translation memories, termbases, prompts, and settings are stored here:")
        )
        data_folder_info.setWordWrap(True)
        data_folder_layout.addWidget(data_folder_info)
        
        # Path display with browse button
        path_row = QHBoxLayout()
        
        data_path_edit = QLineEdit()
        data_path_edit.setText(str(self.user_data_path))
        data_path_edit.setReadOnly(True)
        data_path_edit.setToolTip(self.tr("Current data folder location"))
        path_row.addWidget(data_path_edit)
        
        change_path_btn = QPushButton(self.tr("Change..."))
        change_path_btn.setToolTip(self.tr("Choose a different location for your data folder"))
        
        def change_data_path():
            """Let user change their data folder location."""
            from PyQt6.QtWidgets import QMessageBox
            
            folder = QFileDialog.getExistingDirectory(
                self, 
                "Choose New Data Folder Location",
                str(Path.home())
            )
            if folder:
                folder_path = Path(folder)
                # Append "Supervertaler" if user didn't include it
                if folder_path.name != "Supervertaler":
                    folder_path = folder_path / "Supervertaler"
                
                # Warn if switching to a folder with existing data
                if folder_path.exists() and folder_path != self.user_data_path:
                    has_content = any(folder_path.iterdir())
                    if has_content:
                        reply = QMessageBox.question(
                            self,
                            "Existing Data Found",
                            f"The folder already contains data:\n{folder_path}\n\n"
                            "Do you want to use this existing data?",
                            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel
                        )
                        if reply != QMessageBox.StandardButton.Yes:
                            return
                
                # Create the folder
                folder_path.mkdir(parents=True, exist_ok=True)
                
                # Ask if they want to copy existing data
                if self.user_data_path.exists() and self.user_data_path != folder_path:
                    reply = QMessageBox.question(
                        self,
                        "Copy Existing Data?",
                        f"Would you like to copy your existing data to the new location?\n\n"
                        f"From: {self.user_data_path}\n"
                        f"To: {folder_path}\n\n"
                        "This will NOT delete the original data.",
                        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
                    )
                    if reply == QMessageBox.StandardButton.Yes:
                        import shutil
                        try:
                            for item in self.user_data_path.iterdir():
                                dest = folder_path / item.name
                                if item.is_dir():
                                    shutil.copytree(item, dest, dirs_exist_ok=True)
                                else:
                                    shutil.copy2(item, dest)
                            self.log(f"✅ Copied data to new location")
                        except Exception as e:
                            QMessageBox.warning(self, "Copy Error", f"Failed to copy data: {e}")
                
                # Save the new path
                save_user_data_path(folder_path)
                data_path_edit.setText(str(folder_path))
                
                # Update and reinitialize
                self.user_data_path = folder_path
                self._reinitialize_with_new_data_path()
                
                QMessageBox.information(
                    self,
                    "Data Folder Changed",
                    f"Data folder changed to:\n{folder_path}\n\n"
                    "All managers have been reinitialized."
                )
        
        change_path_btn.clicked.connect(change_data_path)
        path_row.addWidget(change_path_btn)
        
        open_folder_btn = QPushButton(self.tr("Open"))
        open_folder_btn.setToolTip(self.tr("Open this folder in your file manager"))
        open_folder_btn.clicked.connect(lambda: open_file(str(self.user_data_path)))
        path_row.addWidget(open_folder_btn)
        
        data_folder_layout.addLayout(path_row)
        
        data_folder_tip = QLabel(
            self.tr("💡 This folder persists across updates. Back it up regularly!")
        )
        data_folder_tip.setStyleSheet("color: #666; font-size: 9pt;")
        data_folder_layout.addWidget(data_folder_tip)
        
        data_folder_group.setLayout(data_folder_layout)
        layout.addWidget(data_folder_group)

        # Sound Effects group
        sound_group = QGroupBox(self.tr("🔊 Sound Effects"))
        sound_layout = QVBoxLayout()

        sound_effects_cb = CheckmarkCheckBox(self.tr("Enable minimalist sound effects"))
        sound_effects_cb.setChecked(general_settings.get('enable_sound_effects', False))
        sound_effects_cb.setToolTip(
            "Plays a subtle system beep for certain operations (e.g. termbase creation, match insertion).\n"
            "Disable if you prefer a silent workflow."
        )
        sound_layout.addWidget(sound_effects_cb)

        # Per-event sound mapping (Windows system beeps)
        sound_effects_map = general_settings.get('sound_effects_map', {})
        sound_choices = [
            ("(None)", "none"),
            ("OK", "ok"),
            ("Asterisk", "asterisk"),
            ("Exclamation", "exclamation"),
            ("Hand", "hand"),
            ("Question", "question"),
            ("Windows Proximity Notification (.wav)", "windows_proximity_wav"),
            ("Windows Restore (.wav)", "windows_restore_wav"),
            ("Windows Navigation Start (.wav)", "windows_navigation_start_wav"),
            ("Speech Disambiguation (.wav)", "speech_disambiguation_wav"),
            ("Windows Startup (.wav)", "windows_startup_wav"),
        ]

        def make_sound_combo(default_key: str) -> QComboBox:
            combo = QComboBox()
            for label, key in sound_choices:
                combo.addItem(label, key)
            # Set initial
            idx = combo.findData(default_key)
            if idx >= 0:
                combo.setCurrentIndex(idx)
            return combo

        event_rows = QFormLayout()
        event_rows.setContentsMargins(0, 0, 0, 0)
        event_rows.setSpacing(6)

        # Store combos so save button can persist them
        sound_event_combos = {}

        # Glossary entry added (term pair)
        default_term_added = sound_effects_map.get('glossary_term_added', 'asterisk')
        sound_event_combos['glossary_term_added'] = make_sound_combo(default_term_added)
        event_rows.addRow("Termbase entry added:", sound_event_combos['glossary_term_added'])

        # Glossary created (collection)
        default_glossary_created = sound_effects_map.get('glossary_created', 'asterisk')
        sound_event_combos['glossary_created'] = make_sound_combo(default_glossary_created)
        event_rows.addRow("Termbase created:", sound_event_combos['glossary_created'])

        # Compare Panel match inserted/replaced
        default_match_inserted = sound_effects_map.get('match_inserted', 'ok')
        sound_event_combos['match_inserted'] = make_sound_combo(default_match_inserted)
        event_rows.addRow("Match inserted/replaced:", sound_event_combos['match_inserted'])

        # Glossary entry duplicate
        default_term_duplicate = sound_effects_map.get('glossary_term_duplicate', 'exclamation')
        sound_event_combos['glossary_term_duplicate'] = make_sound_combo(default_term_duplicate)
        event_rows.addRow("Termbase entry duplicate:", sound_event_combos['glossary_term_duplicate'])

        # Glossary add error
        default_term_error = sound_effects_map.get('glossary_term_error', 'hand')
        sound_event_combos['glossary_term_error'] = make_sound_combo(default_term_error)
        event_rows.addRow("Termbase add error:", sound_event_combos['glossary_term_error'])

        # Segment confirmed (Ctrl+Enter)
        default_segment_confirmed = sound_effects_map.get('segment_confirmed', 'none')
        sound_event_combos['segment_confirmed'] = make_sound_combo(default_segment_confirmed)
        event_rows.addRow("Segment confirmed:", sound_event_combos['segment_confirmed'])

        # 100% TM match found
        default_tm_100_match = sound_effects_map.get('tm_100_percent_match', 'none')
        sound_event_combos['tm_100_percent_match'] = make_sound_combo(default_tm_100_match)
        event_rows.addRow("100% TM match alert:", sound_event_combos['tm_100_percent_match'])

        # Fuzzy TM match found (< 100%)
        default_tm_fuzzy_match = sound_effects_map.get('tm_fuzzy_match', 'none')
        sound_event_combos['tm_fuzzy_match'] = make_sound_combo(default_tm_fuzzy_match)
        event_rows.addRow("Fuzzy TM match found:", sound_event_combos['tm_fuzzy_match'])

        sound_layout.addLayout(event_rows)

        sound_note = QLabel(self.tr("💡 Uses Windows system beeps (no audio files)."))
        sound_note.setStyleSheet("color: #666; font-size: 9pt; padding: 5px;")
        sound_layout.addWidget(sound_note)

        sound_group.setLayout(sound_layout)
        layout.addWidget(sound_group)

        # (💾 Backup settings moved to their own "Backup" tab — see
        # _create_backup_settings_tab. They were here originally but were hard
        # to find buried among the General options.)

        # ──────────────────────────────────────────────────────────────
        # Box 1: 📂 TM settings
        # ──────────────────────────────────────────────────────────────
        tm_termbase_group = QGroupBox(self.tr("📂 TM settings"))
        tm_termbase_layout = QVBoxLayout()

        # Header row with a contextual "?" help button at the top-right.
        tm_header_row = QHBoxLayout()
        tm_header_row.addStretch()
        try:
            from modules.styled_widgets import HelpButton
            tm_header_row.addWidget(HelpButton(
                "workbench/settings/tm-settings/",
                tooltip="Open the TM settings help page",
            ))
        except Exception as e:
            self.log(f"[TM settings] Could not add help button: {e}")
        tm_termbase_layout.addLayout(tm_header_row)

        # --- Auto-fill empty segments with 100% TM matches (consolidated) ---
        auto_propagate_cb = CheckmarkCheckBox(self.tr("Auto-fill empty segments with 100% TM matches"))
        # Back-compat: prefer the new key, fall back to the old one.
        auto_propagate_cb.setChecked(general_settings.get(
            'auto_fill_100_matches',
            general_settings.get('auto_propagate_exact_matches', True)
        ))
        auto_propagate_cb.setToolTip(
            self.tr("When you select an empty segment that has a 100% TM match, its translation is filled in automatically.")
        )
        tm_termbase_layout.addWidget(auto_propagate_cb)

        # Sub-option: mark auto-filled segments as confirmed
        auto_fill_confirm_cb = CheckmarkCheckBox(self.tr("    ↳ Mark auto-filled segments as confirmed"))
        auto_fill_confirm_cb.setChecked(general_settings.get('auto_fill_confirm', False))
        auto_fill_confirm_cb.setToolTip(
            self.tr("Otherwise the auto-filled translation is inserted as a draft for you to review.")
        )
        auto_fill_confirm_cb.setEnabled(auto_propagate_cb.isChecked())
        auto_propagate_cb.toggled.connect(auto_fill_confirm_cb.setEnabled)
        tm_termbase_layout.addWidget(auto_fill_confirm_cb)

        # --- Auto-propagate confirmed translations to identical segments ---
        auto_propagate_confirm_parent_cb = CheckmarkCheckBox(self.tr("Auto-propagate confirmed translations to identical segments"))
        auto_propagate_confirm_parent_cb.setChecked(general_settings.get('auto_propagate_on_confirm', True))
        auto_propagate_confirm_parent_cb.setToolTip(
            "When you confirm a segment, its translation is copied to every other segment with identical source text.\n"
            "This is the memoQ/Trados-style propagate-on-confirm behaviour."
        )
        tm_termbase_layout.addWidget(auto_propagate_confirm_parent_cb)

        # Sub-option: mark propagated segments as confirmed
        auto_propagate_confirm_cb = CheckmarkCheckBox(self.tr("    ↳ Mark propagated segments as confirmed"))
        auto_propagate_confirm_cb.setChecked(general_settings.get('auto_propagate_confirm', False))
        auto_propagate_confirm_cb.setToolTip(
            self.tr("Otherwise propagated translations are inserted as drafts for you to review.")
        )
        auto_propagate_confirm_cb.setEnabled(auto_propagate_confirm_parent_cb.isChecked())
        auto_propagate_confirm_parent_cb.toggled.connect(auto_propagate_confirm_cb.setEnabled)
        tm_termbase_layout.addWidget(auto_propagate_confirm_cb)

        # Sub-option: overwrite existing translations when propagating
        auto_propagate_overwrite_cb = CheckmarkCheckBox(self.tr("    ↳ Overwrite existing translations when propagating"))
        auto_propagate_overwrite_cb.setChecked(general_settings.get('auto_propagate_overwrite', False))
        auto_propagate_overwrite_cb.setToolTip(
            "When enabled, propagation also replaces existing target content in identical segments.\n"
            "When disabled, only empty segments are filled."
        )
        auto_propagate_overwrite_cb.setEnabled(auto_propagate_confirm_parent_cb.isChecked())
        auto_propagate_confirm_parent_cb.toggled.connect(auto_propagate_overwrite_cb.setEnabled)
        tm_termbase_layout.addWidget(auto_propagate_overwrite_cb)

        # --- Auto-confirm 100% matches when navigating with Ctrl+Enter ---
        auto_confirm_100_cb = CheckmarkCheckBox(
            f"Auto-confirm 100% TM matches when navigating ({format_shortcut_for_display('Ctrl+Enter')})"
        )
        auto_confirm_100_cb.setChecked(general_settings.get('auto_confirm_100_percent_matches', False))
        auto_confirm_100_cb.setToolTip(
            f"When enabled, {format_shortcut_for_display('Ctrl+Enter')} will automatically insert, confirm, and skip past\n"
            "segments that have 100% TM matches. This speeds up workflow by automatically\n"
            "handling perfect matches while navigating through unconfirmed segments."
        )
        tm_termbase_layout.addWidget(auto_confirm_100_cb)

        # Sub-option: Allow overwriting existing translations
        auto_confirm_overwrite_cb = CheckmarkCheckBox(self.tr("    ↳ Also overwrite existing translations with 100% TM matches"))
        auto_confirm_overwrite_cb.setChecked(general_settings.get('auto_confirm_overwrite_existing', False))
        auto_confirm_overwrite_cb.setToolTip(
            "When enabled, auto-confirm will also replace existing target content\n"
            "(including pre-translations or machine translations) with 100% TM matches.\n\n"
            "Useful when you've confirmed a segment higher up, and want identical segments\n"
            "lower down to automatically use your confirmed translation."
        )
        auto_confirm_overwrite_cb.setEnabled(auto_confirm_100_cb.isChecked())
        auto_confirm_100_cb.toggled.connect(auto_confirm_overwrite_cb.setEnabled)
        tm_termbase_layout.addWidget(auto_confirm_overwrite_cb)

        # TM Save Mode
        tm_save_label = QLabel(self.tr("TM Save Mode:"))
        tm_save_mode_combo = QComboBox()
        tm_save_mode_combo.addItem("Save all translations (with timestamps)", "all")
        tm_save_mode_combo.addItem("Save only latest translation (overwrite)", "latest")
        current_tm_mode = general_settings.get('tm_save_mode', 'latest')
        tm_save_mode_combo.setCurrentIndex(0 if current_tm_mode == 'all' else 1)
        tm_save_mode_combo.setToolTip(
            "All translations: Keeps all versions of translations for the same source segment with timestamps.\n"
            "The system will prefer the most recent translation when showing matches.\n\n"
            "Latest only: Overwrites old translations - only the most recent translation is kept.\n"
            "This prevents the TM from growing with old/obsolete translations."
        )
        tm_save_layout_h = QHBoxLayout()
        tm_save_layout_h.addWidget(tm_save_label)
        tm_save_layout_h.addWidget(tm_save_mode_combo)
        tm_save_layout_h.addStretch()
        tm_termbase_layout.addLayout(tm_save_layout_h)

        tm_termbase_group.setLayout(tm_termbase_layout)
        layout.addWidget(tm_termbase_group)

        # Store references for saving / updates
        self.auto_propagate_checkbox = auto_propagate_cb
        self.auto_fill_confirm_checkbox = auto_fill_confirm_cb
        self.auto_propagate_on_confirm_checkbox = auto_propagate_confirm_parent_cb
        self.auto_propagate_confirm_checkbox = auto_propagate_confirm_cb
        self.auto_propagate_overwrite_checkbox = auto_propagate_overwrite_cb

        # ──────────────────────────────────────────────────────────────
        # Box 2: 📒 Termbase settings
        # ──────────────────────────────────────────────────────────────
        termbase_group = QGroupBox(self.tr("📒 Termbase settings"))
        termbase_layout = QVBoxLayout()

        # Header row with a contextual "?" help button at the top-right.
        tb_header_row = QHBoxLayout()
        tb_header_row.addStretch()
        try:
            from modules.styled_widgets import HelpButton
            tb_header_row.addWidget(HelpButton(
                "workbench/settings/termbase-settings/",
                tooltip="Open the Termbase settings help page",
            ))
        except Exception as e:
            self.log(f"[Termbase settings] Could not add help button: {e}")
        termbase_layout.addLayout(tb_header_row)

        # Glossary grid highlighting toggle
        tb_highlight_cb = CheckmarkCheckBox(self.tr("Highlight termbase matches in source cells"))
        tb_highlight_cb.setChecked(general_settings.get('enable_termbase_grid_highlighting', True))
        tb_highlight_cb.setToolTip(
            "When enabled, termbase matches are highlighted with colored backgrounds in the source column.\n"
            "Higher priority terms are shown with darker blue, lower priority with lighter blue.\n"
            "This provides visual feedback similar to memoQ's termbase highlighting."
        )
        termbase_layout.addWidget(tb_highlight_cb)

        # Hide shorter matches checkbox
        tb_hide_shorter_cb = CheckmarkCheckBox(self.tr("Hide shorter termbase matches included in longer ones"))
        tb_hide_shorter_cb.setChecked(general_settings.get('termbase_hide_shorter_matches', False))
        tb_hide_shorter_cb.setToolTip(
            "When enabled, shorter terms that are fully contained within longer matched terms are hidden.\n\n"
            "Example: If both 'cooling' and 'cooling system' match, only 'cooling system' is shown.\n"
            "This reduces clutter in the translation results panel."
        )
        termbase_layout.addWidget(tb_hide_shorter_cb)

        self.tb_highlight_checkbox = tb_highlight_cb  # Store reference for updates

        termbase_group.setLayout(termbase_layout)
        layout.addWidget(termbase_group)

        # The old auto_insert_100_cb checkbox was consolidated into
        # auto_propagate_cb above; the save call still receives it as None.
        auto_insert_100_cb = None

        # Editor Settings group
        editor_group = QGroupBox(self.tr("✍️ Editor Settings"))
        editor_layout = QVBoxLayout()

        # Smart word selection toggle
        smart_selection_cb = CheckmarkCheckBox(self.tr("Enable smart word selection"))
        smart_selection_cb.setChecked(general_settings.get('enable_smart_word_selection', True))
        smart_selection_cb.setToolTip(
            "When enabled, selecting part of a word automatically expands to the full word.\n\n"
            "Example: Selecting 'ductiv' in 'productivity' will automatically select the full word 'productivity'.\n\n"
            "This makes word selection faster and less stressful during translation.\n"
            "Works with hyphens (self-contained) and apostrophes (don't, l'homme)."
        )
        editor_layout.addWidget(smart_selection_cb)
        self.smart_selection_checkbox = smart_selection_cb  # Store reference

        editor_group.setLayout(editor_layout)
        layout.addWidget(editor_group)

        # Global Hotkeys section has been moved to the Keyboard Shortcuts tab
        # (see modules/keyboard_shortcuts_widget.py)

        # ----------------------------------------------------------------
        # Document Import group (v1.10.155)
        # ----------------------------------------------------------------
        # Houses behaviour toggles that take effect at import time. Until
        # v1.10.155 the "Auto-generate markdown for imported documents"
        # checkbox lived under "AI Behavior Settings" on the AI Settings
        # tab — its only consumer is AI Assistant, but the trigger is
        # import (a filesystem write at document-open time), not anything
        # AI-related. Moved here so the categorisation matches when the
        # setting actually fires. The setting key (auto_generate_markdown)
        # is unchanged, so existing user preferences carry over without
        # migration.
        # ----------------------------------------------------------------
        doc_import_group = QGroupBox(self.tr("📥 Document Import"))
        doc_import_layout = QVBoxLayout()

        auto_markdown_cb = CheckmarkCheckBox(self.tr("Auto-generate markdown for imported documents"))
        auto_markdown_cb.setChecked(general_settings.get('auto_generate_markdown', False))
        auto_markdown_cb.setToolTip(
            "Automatically convert imported documents to markdown format\n"
            "for AI Assistant access in user_data_private/ai_assistant/current_document/\n\n"
            "(Off by default — touches the filesystem on every import.)"
        )
        doc_import_layout.addWidget(auto_markdown_cb)
        # Store reference so the General save handler can read its state.
        self.auto_markdown_checkbox = auto_markdown_cb

        doc_import_group.setLayout(doc_import_layout)
        layout.addWidget(doc_import_group)

        # Find & Replace settings group
        find_replace_group = QGroupBox(self.tr("Find && Replace Settings"))
        find_replace_layout = QVBoxLayout()
        
        allow_replace_cb = CheckmarkCheckBox(self.tr("Allow Replace in Source Text"))
        allow_replace_cb.setChecked(self.allow_replace_in_source)
        allow_replace_cb.setToolTip(
            "⚠️ WARNING: Enabling this allows replacing text in the source column.\n"
            "This can be dangerous as it modifies your original source text.\n"
            "Use with extreme caution!"
        )
        find_replace_layout.addWidget(allow_replace_cb)
        
        # Add warning label
        warning_label = QLabel(
            "⚠️ <b>Warning:</b> Replacing in source text modifies your original content.\n"
            "This feature is disabled by default for safety."
        )
        warning_label.setWordWrap(True)
        warning_label.setStyleSheet("color: #d97706; padding: 10px; border-radius: 3px;")
        find_replace_layout.addWidget(warning_label)
        
        find_replace_group.setLayout(find_replace_layout)
        layout.addWidget(find_replace_group)

        # v1.10.230: AutoCorrect-while-typing has its own Settings tab
        # (_create_autocorrect_settings_tab). Originally lived here under
        # General but was moved out so users could find it by category
        # rather than scrolling past a dozen unrelated groups.

        # Precision Scroll Settings group
        scroll_group = QGroupBox(self.tr("⬆️⬇️ Precision Scroll Settings"))
        scroll_layout = QVBoxLayout()
        
        scroll_info = QLabel(
            "Configure how much the precision scroll buttons (▲▼) move the grid.\n"
            "Higher values = finer increments (smaller movements)."
        )
        scroll_info.setWordWrap(True)
        scroll_info.setStyleSheet("color: #666; font-size: 9pt; padding: 5px;")
        scroll_layout.addWidget(scroll_info)
        
        # Scroll increment slider/spinbox
        scroll_control_layout = QHBoxLayout()
        scroll_label = QLabel(self.tr("Scroll precision:"))
        scroll_control_layout.addWidget(scroll_label)
        
        precision_spin = QSpinBox()
        precision_spin.setRange(1, 10)
        precision_spin.setValue(general_settings.get('precision_scroll_divisor', 3))
        precision_spin.setSuffix(" (divisor)")
        precision_spin.setToolTip(
            "Control how far each button click scrolls the grid.\n\n"
            "1 = 50 pixels (coarse)\n"
            "3 = 40 pixels (default)\n"
            "5 = 30 pixels (fine)\n"
            "10 = 5 pixels (very fine)\n\n"
            "Higher values = smaller increments = more precise control."
        )
        scroll_control_layout.addWidget(precision_spin)
        
        # Preview label
        preview_label = QLabel()
        def update_preview(value):
            if value == 1:
                preview_label.setText(self.tr("↕️ Coarse (full row)"))
            elif value <= 3:
                preview_label.setText(self.tr("↕️ Medium (default)"))
            elif value <= 6:
                preview_label.setText(self.tr("↕️ Fine"))
            else:
                preview_label.setText(self.tr("↕️ Very fine"))
        update_preview(precision_spin.value())
        precision_spin.valueChanged.connect(update_preview)
        
        scroll_control_layout.addWidget(preview_label)
        scroll_control_layout.addStretch()
        scroll_layout.addLayout(scroll_control_layout)
        
        # Auto-center active segment toggle
        auto_center_cb = CheckmarkCheckBox(self.tr("Keep active segment centered (like memoQ/Trados)"))
        auto_center_cb.setChecked(general_settings.get('auto_center_active_segment', False))
        auto_center_cb.setToolTip(
            "When enabled, the grid automatically scrolls to keep the currently selected segment\n"
            "centered in the viewport when you navigate between segments.\n"
            "This matches the behavior of memoQ, Trados, and other CAT tools."
        )
        scroll_layout.addWidget(auto_center_cb)
        
        scroll_group.setLayout(scroll_layout)
        layout.addWidget(scroll_group)
        
        # Store references for saving
        self.precision_spin = precision_spin
        self.auto_center_cb = auto_center_cb
        
        # 🧪 Experimental Performance group
        experimental_group = QGroupBox(self.tr("🧪 Experimental Performance"))
        experimental_layout = QVBoxLayout()
        
        exp_info = QLabel(
            "⚠️ These options are for testing and debugging performance.\n"
            "Use with caution - they may affect application behavior."
        )
        exp_info.setWordWrap(True)
        exp_info.setStyleSheet("color: #d97706; font-size: 9pt; padding: 5px;")
        experimental_layout.addWidget(exp_info)
        
        # Cache kill switch
        disable_cache_cb = CheckmarkCheckBox(self.tr("Disable ALL caches (direct lookups every time)"))
        disable_cache_cb.setChecked(general_settings.get('disable_all_caches', False))
        disable_cache_cb.setToolTip(
            "When enabled, ALL caching is bypassed:\n"
            "• Termbase cache\n"
            "• Translation matches cache\n"
            "• Prefetch system\n\n"
            "Every segment navigation will perform fresh database lookups.\n"
            "Use this to test if caching is causing issues or to measure\n"
            "baseline performance without any caching."
        )
        experimental_layout.addWidget(disable_cache_cb)
        self.disable_cache_checkbox = disable_cache_cb
        
        experimental_group.setLayout(experimental_layout)
        layout.addWidget(experimental_group)
        
        # Translation Results Match Limits group
        match_limits_group = QGroupBox(self.tr("📊 Translation Results - Match Limits"))
        match_limits_layout = QVBoxLayout()
        
        info_label = QLabel(
            "Configure the maximum number of matches to display for each match type.\n"
            "Reducing limits improves performance and reduces clutter."
        )
        info_label.setWordWrap(True)
        info_label.setStyleSheet("color: #666; font-size: 9pt; padding: 5px;")
        match_limits_layout.addWidget(info_label)
        
        # Create spinboxes for each match type
        limits_grid = QVBoxLayout()
        limits_grid.setSpacing(5)
        
        current_limits = general_settings.get('match_limits', {
            "LLM": 3,
            "MT": 3,
            "TM": 5,
            "Termbases": 10
        })
        
        # LLM limit
        llm_layout = QHBoxLayout()
        llm_layout.addWidget(QLabel(self.tr("🧠 LLM (AI) matches:")))
        llm_spin = QSpinBox()
        llm_spin.setRange(1, 10)
        llm_spin.setValue(current_limits.get("LLM", 3))
        llm_spin.setSuffix(" matches")
        llm_layout.addWidget(llm_spin)
        llm_layout.addStretch()
        limits_grid.addLayout(llm_layout)
        
        # MT limit
        mt_layout = QHBoxLayout()
        mt_layout.addWidget(QLabel(self.tr("🤖 MT (Machine Translation) matches:")))
        mt_spin = QSpinBox()
        mt_spin.setRange(1, 10)
        mt_spin.setValue(current_limits.get("MT", 3))
        mt_spin.setSuffix(" matches")
        mt_layout.addWidget(mt_spin)
        mt_layout.addStretch()
        limits_grid.addLayout(mt_layout)
        
        # TM limit
        tm_limit_layout = QHBoxLayout()
        tm_limit_layout.addWidget(QLabel(self.tr("💾 TM (Translation Memory) matches:")))
        tm_limit_spin = QSpinBox()
        tm_limit_spin.setRange(1, 20)
        tm_limit_spin.setValue(current_limits.get("TM", 5))
        tm_limit_spin.setSuffix(" matches")
        tm_limit_layout.addWidget(tm_limit_spin)
        tm_limit_layout.addStretch()
        limits_grid.addLayout(tm_limit_layout)
        
        # Glossary limit
        tb_layout = QHBoxLayout()
        tb_layout.addWidget(QLabel(self.tr("📚 Termbase matches:")))
        tb_spin = QSpinBox()
        tb_spin.setRange(1, 50)
        tb_spin.setValue(current_limits.get("Termbases", 10))
        tb_spin.setSuffix(" matches")
        tb_layout.addWidget(tb_spin)
        tb_layout.addStretch()
        limits_grid.addLayout(tb_layout)
        
        match_limits_layout.addLayout(limits_grid)
        match_limits_group.setLayout(match_limits_layout)
        layout.addWidget(match_limits_group)
        
        # Save button
        save_btn = QPushButton(self.tr("💾 Save General Settings"))
        save_btn.setProperty("settings_save_button", True)  # clicked for the user by the auto-saver (issue #214)
        save_btn.setStyleSheet("font-weight: bold; padding: 8px;")
        save_btn.clicked.connect(lambda: self._save_general_settings_from_ui(
            restore_last_project_cb, allow_replace_cb, auto_propagate_cb,
            llm_spin, mt_spin, tm_limit_spin, tb_spin,
            auto_open_log_cb, auto_insert_100_cb, tm_save_mode_combo, tb_highlight_cb,
            # Backup controls now live on the dedicated Backup tab; read them
            # from the instance attrs it sets (resolved when Save is clicked).
            getattr(self, 'enable_backup_cb', None), getattr(self, 'backup_interval_spin', None),
            tb_hide_shorter_cb, smart_selection_cb,
            ahk_path_edit=getattr(self, 'ahk_path_edit', None),
            auto_center_cb=auto_center_cb,
            auto_confirm_100_cb=auto_confirm_100_cb,
            auto_confirm_overwrite_cb=auto_confirm_overwrite_cb,
            sound_effects_cb=sound_effects_cb,
            sound_event_combos=sound_event_combos,
            disable_cache_cb=disable_cache_cb,
            auto_fill_confirm_cb=auto_fill_confirm_cb,
            auto_propagate_on_confirm_cb=auto_propagate_confirm_parent_cb,
            auto_propagate_confirm_cb=auto_propagate_confirm_cb,
            auto_propagate_overwrite_cb=auto_propagate_overwrite_cb
        ))
        layout.addWidget(save_btn)
        
        layout.addStretch()
        
        return tab

    def _create_autocorrect_settings_tab(self):
        """v1.10.230: Settings tab for the AutoCorrect-while-typing engine
        (issue #213). Each checkbox lives-saves to ``settings.json`` on
        toggle — no Save button — so changes take effect immediately on
        the very next keystroke in the grid."""
        from PyQt6.QtWidgets import QGroupBox
        from modules.autocorrect import AutoCorrectEngine as _AC_Engine_Cls

        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(15)
        set_help_topic(tab, HelpTopics.SETTINGS_AUTOCORRECT)

        # ── AutoCorrect group ────────────────────────────────────────────
        group = QGroupBox(self.tr("✍️ AutoCorrect while typing"))
        glayout = QVBoxLayout()

        # Help button (top-right corner of the group).
        header_row = QHBoxLayout()
        header_row.addStretch()
        try:
            from modules.styled_widgets import HelpButton as _HelpBtn
            header_row.addWidget(_HelpBtn(
                "workbench/settings/autocorrect/",
                tooltip="Open the AutoCorrect help page",
            ))
        except Exception as _e:
            self.log(f"[AutoCorrect settings] Could not add help button: {_e}")
        glayout.addLayout(header_row)

        info = QLabel(
            "Automatic typographic conversion as you type in the target field.\n"
            "Quote shapes follow the target language (German „…“, French « … », etc.).\n\n"
            "Changes apply immediately — there's no Save button on this tab."
        )
        info.setWordWrap(True)
        info.setStyleSheet("color: #666; font-size: 9pt; padding: 5px;")
        glayout.addWidget(info)

        master_cb = CheckmarkCheckBox(self.tr("Enable AutoCorrect while typing"))
        master_cb.setChecked(bool(getattr(self, 'autocorrect_enabled', True)))
        master_cb.setToolTip(
            "Master switch. Turning this off disables every rule below,\n"
            "regardless of their individual state."
        )
        glayout.addWidget(master_cb)

        # Per-rule checkboxes — same ordering as the engine's default list.
        rule_meta = [
            ("smart_double_quote", 'Smart double quotes  ("…"  →  „…", «…», "…" — by target language)'),
            ("smart_single_quote", "Smart single quotes / apostrophes  ('  →  ’)"),
            ("ellipsis",           "Ellipsis  (...  →  …)"),
            ("en_dash",            "En-dash  (word--word  →  word–word)"),
            ("em_dash",            "Em-dash  (word---word  →  word—word)  — off by default"),
            ("french_nbsp",        "French typographic spacing  (NBSP before  :  ;  !  ?  — fr-* targets)"),
        ]
        rules_by_id = {r.rule_id: r for r in _AC_Engine_Cls().all_rules()}
        rule_cbs: Dict[str, QCheckBox] = {}
        for rule_id, label in rule_meta:
            rule = rules_by_id.get(rule_id)
            if rule is None:
                continue
            cb = CheckmarkCheckBox(label)
            current_state = self.autocorrect_rule_overrides.get(rule_id, rule.default_on)
            cb.setChecked(bool(current_state))
            cb.setEnabled(master_cb.isChecked())
            glayout.addWidget(cb)
            rule_cbs[rule_id] = cb

            def _on_rule_toggle(checked, rid=rule_id):
                self.autocorrect_rule_overrides[rid] = bool(checked)
                self._persist_autocorrect_settings()
            cb.toggled.connect(_on_rule_toggle)

        def _on_master_toggle(checked):
            self.autocorrect_enabled = bool(checked)
            for _cb in rule_cbs.values():
                _cb.setEnabled(bool(checked))
            self._persist_autocorrect_settings()
        master_cb.toggled.connect(_on_master_toggle)

        # Stash refs so a future "Reset to language defaults" button could
        # find them, and so a programmatic settings change can sync the UI.
        self._autocorrect_master_cb = master_cb
        self._autocorrect_rule_cbs = rule_cbs

        group.setLayout(glayout)
        layout.addWidget(group)
        layout.addStretch()
        return tab

    def _persist_autocorrect_settings(self):
        """v1.10.230: Write just the AutoCorrect keys to ``settings.json``,
        leaving every other general setting untouched. Called from every
        toggle handler on the AutoCorrect tab so the user never has to
        click Save."""
        try:
            current = self._load_settings_section("general") or {}
            current['autocorrect_enabled'] = bool(self.autocorrect_enabled)
            current['autocorrect_rule_overrides'] = dict(self.autocorrect_rule_overrides)
            self.save_general_settings(current)
        except Exception as e:
            self.log(f"⚠ Could not persist AutoCorrect settings: {e}")

    def _create_view_settings_tab(self):
        """Create View Settings tab content"""
        from PyQt6.QtWidgets import QGroupBox, QPushButton, QButtonGroup, QColorDialog
        from PyQt6.QtGui import QColor

        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(15)
        
        font_settings = self.load_general_settings()
        
        # Grid Text Font Settings section
        grid_group = QGroupBox(self.tr("📊 Grid Text Font Settings"))
        grid_layout = QVBoxLayout()
        
        grid_size_info = QLabel(
            "Set the default font family and size for the grid (source and target columns).\n"
            "You can also adjust size using View menu → Grid Text Zoom.\n"
            "If your favourite font is missing, contact the developer!"
        )
        grid_size_info.setStyleSheet("font-size: 8pt; padding: 8px; border-radius: 2px;")
        grid_size_info.setWordWrap(True)
        grid_layout.addWidget(grid_size_info)
        
        # Font family dropdown
        grid_font_family_layout = QHBoxLayout()
        grid_font_family_layout.addWidget(QLabel(self.tr("Font Family:")))
        grid_font_family_combo = QComboBox()
        font_families = ["Calibri", "Segoe UI", "Arial", "Consolas", "Verdana", 
                        "Times New Roman", "Georgia", "Courier New", "Tahoma", "Trebuchet MS"]
        grid_font_family_combo.addItems(font_families)
        current_font_family = font_settings.get('grid_font_family', self.default_font_family)
        if current_font_family in font_families:
            grid_font_family_combo.setCurrentText(current_font_family)
        else:
            grid_font_family_combo.setCurrentText("Calibri")
        grid_font_family_combo.setToolTip(self.tr("Select the font family for grid text"))
        grid_font_family_layout.addWidget(grid_font_family_combo)
        grid_font_family_layout.addStretch()
        grid_layout.addLayout(grid_font_family_layout)
        
        # Font size spinbox
        grid_spin_layout = QHBoxLayout()
        grid_spin_layout.addWidget(QLabel(self.tr("Font Size:")))
        grid_font_spin = QSpinBox()
        grid_font_spin.setMinimum(7)
        grid_font_spin.setMaximum(72)
        grid_font_spin.setValue(font_settings.get('grid_font_size', 11))
        grid_font_spin.setSuffix(" pt")
        grid_font_spin.setToolTip(self.tr("Grid font size (7-72 pt)"))
        grid_font_spin.setMinimumHeight(28)
        grid_font_spin.setMinimumWidth(80)
        # Fix spinbox arrow buttons - ensure both up and down work correctly
        grid_font_spin.setStyleSheet("""
            QSpinBox {
                padding-right: 20px;
            }
            QSpinBox::up-button {
                width: 20px;
                height: 14px;
            }
            QSpinBox::down-button {
                width: 20px;
                height: 14px;
            }
        """)
        grid_spin_layout.addWidget(grid_font_spin)
        grid_spin_layout.addStretch()
        grid_layout.addLayout(grid_spin_layout)
        
        # Live preview section
        preview_label = QLabel(self.tr("Preview:"))
        preview_label.setStyleSheet("font-weight: bold; margin-top: 8px;")
        grid_layout.addWidget(preview_label)
        
        # Preview container with border to simulate grid
        preview_container = QFrame()
        preview_container.setFrameStyle(QFrame.Shape.Box | QFrame.Shadow.Sunken)
        preview_container.setStyleSheet("border: 1px solid #ccc;")
        preview_container_layout = QVBoxLayout(preview_container)
        preview_container_layout.setContentsMargins(0, 0, 0, 0)
        preview_container_layout.setSpacing(0)
        
        # Source row (read-only style - light gray background)
        source_preview = QLabel()
        source_preview.setTextFormat(Qt.TextFormat.RichText)
        source_preview.setWordWrap(True)
        source_preview.setStyleSheet("padding: 6px; border-bottom: 1px solid #ddd;")
        source_preview.setToolTip(self.tr("Source text preview"))
        preview_container_layout.addWidget(source_preview)
        
        # Target row (editable style - white background)
        target_preview = QLabel()
        target_preview.setTextFormat(Qt.TextFormat.RichText)
        target_preview.setWordWrap(True)
        target_preview.setStyleSheet("padding: 6px;")
        target_preview.setToolTip(self.tr("Target text preview"))
        preview_container_layout.addWidget(target_preview)
        
        grid_layout.addWidget(preview_container)
        
        # Function to update preview
        def update_font_preview():
            font_family = grid_font_family_combo.currentText()
            font_size = grid_font_spin.value()
            tag_color = font_settings.get('tag_highlight_color', '#7f0001')
            
            # Sample text with a tag
            source_html = f'<span style="font-family: {font_family}; font-size: {font_size}pt;">The <span style="background-color: {tag_color};">&lt;b&gt;</span>quick<span style="background-color: {tag_color};">&lt;/b&gt;</span> brown fox jumps.</span>'
            target_html = f'<span style="font-family: {font_family}; font-size: {font_size}pt;">De <span style="background-color: {tag_color};">&lt;b&gt;</span>snelle<span style="background-color: {tag_color};">&lt;/b&gt;</span> bruine vos springt.</span>'
            
            source_preview.setText(source_html)
            target_preview.setText(target_html)
        
        # Connect signals to update preview
        grid_font_family_combo.currentTextChanged.connect(update_font_preview)
        grid_font_spin.valueChanged.connect(update_font_preview)
        
        # Initial preview update
        update_font_preview()
        
        grid_group.setLayout(grid_layout)
        layout.addWidget(grid_group)

        # Grid Display Options section
        grid_display_group = QGroupBox(self.tr("📊 Grid Display Options"))
        grid_display_layout = QVBoxLayout()

        grid_display_info = QLabel(
            self.tr("Configure how content is displayed in the translation grid.")
        )
        grid_display_info.setStyleSheet("font-size: 8pt; padding: 8px; border-radius: 2px;")
        grid_display_info.setWordWrap(True)
        grid_display_layout.addWidget(grid_display_info)

        # Hide wrapping tags checkbox
        hide_wrapping_tags_layout = QHBoxLayout()
        hide_wrapping_tags_check = CheckmarkCheckBox(self.tr("Hide outer wrapping tags in grid (e.g. <li-o>...</li-o>)"))
        hide_wrapping_tags_check.setChecked(font_settings.get('hide_outer_wrapping_tags', False))
        hide_wrapping_tags_check.setToolTip(
            "When enabled, structural tags that wrap the entire segment (like <li-o>, <p>, <td>) are hidden.\n"
            "Tags are automatically restored when saving. List items show visual prefixes (1. 2. or •).\n"
            "The segment type is shown in the Type column, so outer tags are redundant.\n"
            "Inner formatting tags like <b>bold</b> are still shown."
        )
        hide_wrapping_tags_layout.addWidget(hide_wrapping_tags_check)
        hide_wrapping_tags_layout.addStretch()
        grid_display_layout.addLayout(hide_wrapping_tags_layout)

        # Status column position checkbox
        status_col_layout = QHBoxLayout()
        status_before_target_check = CheckmarkCheckBox(self.tr("Show Status column between Source and Target (uncheck to move it to the far right)"))
        status_before_target_check.setChecked(font_settings.get('status_column_before_target', True))
        status_before_target_check.setToolTip(
            "When enabled (default), the Status column sits between the Source and\n"
            "Target columns.\n"
            "When disabled, the Status column moves to the far right (after Target)."
        )
        status_col_layout.addWidget(status_before_target_check)
        status_col_layout.addStretch()
        grid_display_layout.addLayout(status_col_layout)

        grid_display_group.setLayout(grid_display_layout)
        layout.addWidget(grid_display_group)

        # Status Bar section
        status_bar_group = QGroupBox(self.tr("📊 Status Bar"))
        status_bar_layout = QVBoxLayout()

        status_bar_info = QLabel(
            self.tr("Configure the progress indicators shown in the bottom status bar.")
        )
        status_bar_info.setStyleSheet("font-size: 8pt; padding: 8px; border-radius: 2px;")
        status_bar_info.setWordWrap(True)
        status_bar_layout.addWidget(status_bar_info)

        # Confirmed-percentage basis checkbox. Live-saves via the same single
        # source of truth as clicking the status-bar "Confirmed" label, so the
        # two always agree.
        confirmed_words_layout = QHBoxLayout()
        # Resolve the current basis without depending on UI build order: prefer
        # the in-memory value, fall back to the persisted setting, then default.
        _basis = getattr(self, '_confirmed_progress_basis', None)
        if _basis not in ("segments", "words"):
            try:
                _basis = self._load_settings_section("ui").get("confirmed_progress_basis")
            except Exception:
                _basis = None
            if _basis not in ("segments", "words"):
                _basis = "words"
        self._confirmed_words_checkbox = CheckmarkCheckBox(self.tr("Show confirmed progress by word count (instead of segment count)"))
        self._confirmed_words_checkbox.setChecked(_basis == "words")
        self._confirmed_words_checkbox.setToolTip(self.tr(
            "When enabled (default), the Confirmed percentage in the status bar is the\n"
            "share of source words in confirmed segments. Word-based progress is\n"
            "steadier on short documents, where confirming one long segment would\n"
            "otherwise swing the segment-count percentage a lot.\n"
            "When disabled, it is the share of confirmed segments.\n"
            "You can also switch this by clicking the Confirmed label in the status bar."
        ))
        self._confirmed_words_checkbox.toggled.connect(
            lambda checked: self._set_confirmed_progress_basis("words" if checked else "segments")
        )
        confirmed_words_layout.addWidget(self._confirmed_words_checkbox)
        confirmed_words_layout.addStretch()
        status_bar_layout.addLayout(confirmed_words_layout)

        status_bar_group.setLayout(status_bar_layout)
        layout.addWidget(status_bar_group)

        # Match Panel & Tag Colors section
        results_group = QGroupBox(self.tr("📋 Match Panel && Tag Colors"))
        results_layout = QVBoxLayout()
        
        results_size_info = QLabel(
            self.tr("Set font sizes for the Match Panel (TM/termbase matches) and tag colors.")
        )
        results_size_info.setStyleSheet("font-size: 8pt; padding: 8px; border-radius: 2px;")
        results_size_info.setWordWrap(True)
        results_layout.addWidget(results_size_info)
        
        # Match list font size
        match_spin_layout = QHBoxLayout()
        match_spin_layout.addWidget(QLabel(self.tr("Match List Font Size:")))
        match_font_spin = QSpinBox()
        match_font_spin.setMinimum(7)
        match_font_spin.setMaximum(16)
        match_font_spin.setValue(font_settings.get('results_match_font_size', 9))
        match_font_spin.setSuffix(" pt")
        match_font_spin.setToolTip(self.tr("Match list font size (7-16 pt)"))
        match_spin_layout.addWidget(match_font_spin)
        match_spin_layout.addStretch()
        results_layout.addLayout(match_spin_layout)
        
        # Compare boxes font size
        compare_spin_layout = QHBoxLayout()
        compare_spin_layout.addWidget(QLabel(self.tr("Compare Boxes Font Size:")))
        compare_font_spin = QSpinBox()
        compare_font_spin.setMinimum(7)
        compare_font_spin.setMaximum(14)
        compare_font_spin.setValue(font_settings.get('results_compare_font_size', 9))
        compare_font_spin.setSuffix(" pt")
        compare_font_spin.setToolTip(self.tr("Compare boxes font size (7-14 pt)"))
        compare_spin_layout.addWidget(compare_font_spin)
        compare_spin_layout.addStretch()
        results_layout.addLayout(compare_spin_layout)
        
        # Show tags checkbox
        show_tags_layout = QHBoxLayout()
        show_tags_check = CheckmarkCheckBox(self.tr("Show HTML/XML tags in match text"))
        show_tags_check.setChecked(font_settings.get('results_show_tags', False))
        show_tags_check.setToolTip(self.tr("When enabled, tags like <b>, <li>, etc. are displayed. When disabled, only the text content is shown."))
        show_tags_layout.addWidget(show_tags_check)
        show_tags_layout.addStretch()
        results_layout.addLayout(show_tags_layout)

        # Tag highlight color picker
        tag_color_layout = QHBoxLayout()
        tag_color_layout.addWidget(QLabel(self.tr("Tag Highlight Color:")))
        
        from PyQt6.QtWidgets import QColorDialog
        from PyQt6.QtGui import QColor
        
        # Get current tag color or default to memoQ dark red
        current_color = font_settings.get('tag_highlight_color', '#7f0001')
        tag_color_btn = QPushButton()
        tag_color_btn.setFixedSize(80, 25)
        tag_color_btn.setStyleSheet(f"background-color: {current_color}; border: 1px solid #999;")
        tag_color_btn.setToolTip(self.tr("Color for CAT tool tags (e.g. <b>, [uicontrol], {MQ}) in the grid and results pane"))
        
        def choose_tag_color():
            # Set up preset colors in the color dialog
            preset_colors = [
                '#7f0001',  # memoQ dark red (default)
                '#996666',  # Dusty rose
                '#9AAFC7',  # Slate blue
                '#B8A9C9',  # Dusty lavender
                '#A05050',  # Muted red
                '#FFB6C1',  # Light pink (old default)
                '#FF69B4',  # Hot pink
                '#D4A5A5',  # Muted rose
            ]
            for i, preset in enumerate(preset_colors):
                QColorDialog.setCustomColor(i, QColor(preset))
            
            color = QColorDialog.getColor(QColor(current_color), self, "Choose Tag Highlight Color")
            if color.isValid():
                hex_color = color.name()
                tag_color_btn.setStyleSheet(f"background-color: {hex_color}; border: 1px solid #999;")
                tag_color_btn.setProperty('selected_color', hex_color)
        
        tag_color_btn.clicked.connect(choose_tag_color)
        tag_color_btn.setProperty('selected_color', current_color)
        tag_color_layout.addWidget(tag_color_btn)
        
        # Reset tag color button
        reset_tag_color_btn = QPushButton(self.tr("Reset"))
        reset_tag_color_btn.setFixedSize(50, 25)
        reset_tag_color_btn.setToolTip(self.tr("Reset to default memoQ red (#7f0001)"))
        def reset_tag_color():
            default_color = '#7f0001'
            tag_color_btn.setStyleSheet(f"background-color: {default_color}; border: 1px solid #999;")
            tag_color_btn.setProperty('selected_color', default_color)
        reset_tag_color_btn.clicked.connect(reset_tag_color)
        tag_color_layout.addWidget(reset_tag_color_btn)
        
        tag_color_layout.addStretch()
        results_layout.addLayout(tag_color_layout)

        # Badge text color picker
        badge_text_color_layout = QHBoxLayout()
        badge_text_color_layout.addWidget(QLabel(self.tr("Badge Text Color:")))
        
        # Get current badge text color or default to dark gray
        current_badge_color = font_settings.get('badge_text_color', '#333333')
        badge_text_color_btn = QPushButton()
        badge_text_color_btn.setFixedSize(80, 25)
        badge_text_color_btn.setStyleSheet(f"background-color: {current_badge_color}; border: 1px solid #999;")
        badge_text_color_btn.setToolTip(self.tr("Color for match number badges in Translation Results panel"))
        
        def choose_badge_text_color():
            # Set up preset colors in the color dialog
            preset_colors = [
                '#333333',  # Dark gray (default)
                '#212121',  # Material Design off-black
                '#2C2C2C',  # Charcoal
                '#37474F',  # Slate gray
                '#000000',  # Pure black
                '#444444',  # Medium gray
                '#1A1A1A',  # Near black
                '#505050',  # Lighter gray
            ]
            for i, preset in enumerate(preset_colors):
                QColorDialog.setCustomColor(i, QColor(preset))
            
            color = QColorDialog.getColor(QColor(current_badge_color), self, "Choose Badge Text Color")
            if color.isValid():
                hex_color = color.name()
                badge_text_color_btn.setStyleSheet(f"background-color: {hex_color}; border: 1px solid #999;")
                badge_text_color_btn.setProperty('selected_color', hex_color)
        
        badge_text_color_btn.clicked.connect(choose_badge_text_color)
        badge_text_color_btn.setProperty('selected_color', current_badge_color)
        badge_text_color_layout.addWidget(badge_text_color_btn)
        
        # Reset badge text color button
        reset_badge_text_color_btn = QPushButton(self.tr("Reset"))
        reset_badge_text_color_btn.setFixedSize(50, 25)
        reset_badge_text_color_btn.setToolTip(self.tr("Reset to default dark gray (#333333)"))
        def reset_badge_text_color():
            default_color = '#333333'
            badge_text_color_btn.setStyleSheet(f"background-color: {default_color}; border: 1px solid #999;")
            badge_text_color_btn.setProperty('selected_color', default_color)
        reset_badge_text_color_btn.clicked.connect(reset_badge_text_color)
        badge_text_color_layout.addWidget(reset_badge_text_color_btn)
        
        badge_text_color_layout.addStretch()
        results_layout.addLayout(badge_text_color_layout)

        # Invisible character color picker
        invisible_char_color_layout = QHBoxLayout()
        invisible_char_color_layout.addWidget(QLabel(self.tr("Invisible Char Color:")))

        # Get current invisible character color or default to light gray
        current_invisible_color = font_settings.get('invisible_char_color', '#999999')
        invisible_char_color_btn = QPushButton()
        invisible_char_color_btn.setFixedSize(80, 25)
        invisible_char_color_btn.setStyleSheet(f"background-color: {current_invisible_color}; border: 1px solid #999;")
        invisible_char_color_btn.setToolTip(self.tr("Color for invisible character symbols (·→↵); non-breaking spaces are shaded with this colour"))

        def choose_invisible_char_color():
            color = QColorDialog.getColor(QColor(current_invisible_color), self, "Choose Invisible Character Color")
            if color.isValid():
                hex_color = color.name()
                invisible_char_color_btn.setStyleSheet(f"background-color: {hex_color}; border: 1px solid #999;")
                invisible_char_color_btn.setProperty('selected_color', hex_color)

        invisible_char_color_btn.clicked.connect(choose_invisible_char_color)
        invisible_char_color_btn.setProperty('selected_color', current_invisible_color)
        invisible_char_color_layout.addWidget(invisible_char_color_btn)
        
        # Reset invisible char color button
        reset_invisible_color_btn = QPushButton(self.tr("Reset"))
        reset_invisible_color_btn.setFixedSize(50, 25)
        reset_invisible_color_btn.setToolTip(self.tr("Reset to default gray (#999999)"))
        def reset_invisible_color():
            default_color = '#999999'
            invisible_char_color_btn.setStyleSheet(f"background-color: {default_color}; border: 1px solid #999;")
            invisible_char_color_btn.setProperty('selected_color', default_color)
        reset_invisible_color_btn.clicked.connect(reset_invisible_color)
        invisible_char_color_layout.addWidget(reset_invisible_color_btn)
        
        invisible_char_color_layout.addStretch()
        results_layout.addLayout(invisible_char_color_layout)

        results_group.setLayout(results_layout)
        layout.addWidget(results_group)

        # Grid Row Colors section (memoQ-style alternating row colors)
        row_colors_group = QGroupBox(self.tr("🎨 Grid Row Colors"))
        row_colors_layout = QVBoxLayout()
        
        row_colors_info = QLabel(
            "Configure alternating row colors for the translation grid.\n"
            "This creates a visual distinction between rows (like memoQ)."
        )
        row_colors_info.setStyleSheet("font-size: 8pt; padding: 8px; border-radius: 2px;")
        row_colors_info.setWordWrap(True)
        row_colors_layout.addWidget(row_colors_info)
        
        # Enable alternating colors checkbox
        alt_colors_check = CheckmarkCheckBox(self.tr("Enable alternating row colors"))
        alt_colors_check.setChecked(font_settings.get('enable_alternating_row_colors', True))
        alt_colors_check.setToolTip(self.tr("When enabled, even and odd rows have different background colors"))
        row_colors_layout.addWidget(alt_colors_check)
        
        # Even row color picker (lighter/default)
        even_color_layout = QHBoxLayout()
        even_color_layout.addWidget(QLabel(self.tr("Even Row Color:")))
        
        even_row_color = font_settings.get('even_row_color', '#FFFFFF')
        even_color_btn = QPushButton()
        even_color_btn.setFixedSize(80, 25)
        even_color_btn.setStyleSheet(f"background-color: {even_row_color}; border: 1px solid #999;")
        even_color_btn.setToolTip(self.tr("Background color for even rows (0, 2, 4, ...)"))
        
        def choose_even_color():
            color = QColorDialog.getColor(QColor(even_row_color), self, "Choose Even Row Color")
            if color.isValid():
                hex_color = color.name()
                even_color_btn.setStyleSheet(f"background-color: {hex_color}; border: 1px solid #999;")
                even_color_btn.setProperty('selected_color', hex_color)
        
        even_color_btn.clicked.connect(choose_even_color)
        even_color_btn.setProperty('selected_color', even_row_color)
        even_color_layout.addWidget(even_color_btn)
        even_color_layout.addStretch()
        row_colors_layout.addLayout(even_color_layout)
        
        # Odd row color picker (darker/alternate)
        odd_color_layout = QHBoxLayout()
        odd_color_layout.addWidget(QLabel(self.tr("Odd Row Color:")))
        
        odd_row_color = font_settings.get('odd_row_color', '#F0F0F0')
        odd_color_btn = QPushButton()
        odd_color_btn.setFixedSize(80, 25)
        odd_color_btn.setStyleSheet(f"background-color: {odd_row_color}; border: 1px solid #999;")
        odd_color_btn.setToolTip(self.tr("Background color for odd rows (1, 3, 5, ...)"))
        
        def choose_odd_color():
            color = QColorDialog.getColor(QColor(odd_row_color), self, "Choose Odd Row Color")
            if color.isValid():
                hex_color = color.name()
                odd_color_btn.setStyleSheet(f"background-color: {hex_color}; border: 1px solid #999;")
                odd_color_btn.setProperty('selected_color', hex_color)
        
        odd_color_btn.clicked.connect(choose_odd_color)
        odd_color_btn.setProperty('selected_color', odd_row_color)
        odd_color_layout.addWidget(odd_color_btn)
        odd_color_layout.addStretch()
        row_colors_layout.addLayout(odd_color_layout)
        
        # Reset to defaults button
        reset_colors_btn = QPushButton(self.tr("Reset to Default Colors"))
        reset_colors_btn.setToolTip(self.tr("Reset to white (#FFFFFF) and light gray (#F0F0F0)"))
        def reset_row_colors():
            even_color_btn.setStyleSheet("background-color: #FFFFFF; border: 1px solid #999;")
            even_color_btn.setProperty('selected_color', '#FFFFFF')
            odd_color_btn.setStyleSheet("background-color: #F0F0F0; border: 1px solid #999;")
            odd_color_btn.setProperty('selected_color', '#F0F0F0')
        reset_colors_btn.clicked.connect(reset_row_colors)
        row_colors_layout.addWidget(reset_colors_btn)
        
        row_colors_group.setLayout(row_colors_layout)
        layout.addWidget(row_colors_group)
        
        # Focus Border Settings section
        focus_border_group = QGroupBox(self.tr("🔵 Target Cell Focus Border"))
        focus_border_layout = QVBoxLayout()
        
        focus_border_info = QLabel(
            self.tr("Customize the border that appears around the target cell when it has focus.")
        )
        focus_border_info.setStyleSheet("font-size: 8pt; padding: 8px; border-radius: 2px;")
        focus_border_info.setWordWrap(True)
        focus_border_layout.addWidget(focus_border_info)
        
        # Border color picker
        border_color_layout = QHBoxLayout()
        border_color_layout.addWidget(QLabel(self.tr("Border Color:")))
        
        focus_border_color = font_settings.get('focus_border_color', '#f1b79a')
        border_color_btn = QPushButton()
        border_color_btn.setFixedSize(80, 25)
        border_color_btn.setStyleSheet(f"background-color: {focus_border_color}; border: 1px solid #999;")
        border_color_btn.setToolTip(self.tr("Color of the focus border around target cells"))
        
        def choose_border_color():
            color = QColorDialog.getColor(QColor(focus_border_color), self, "Choose Focus Border Color")
            if color.isValid():
                hex_color = color.name()
                border_color_btn.setStyleSheet(f"background-color: {hex_color}; border: 1px solid #999;")
                border_color_btn.setProperty('selected_color', hex_color)
        
        border_color_btn.clicked.connect(choose_border_color)
        border_color_btn.setProperty('selected_color', focus_border_color)
        border_color_layout.addWidget(border_color_btn)
        
        # Reset to default color button
        reset_border_color_btn = QPushButton(self.tr("Reset"))
        reset_border_color_btn.setFixedWidth(50)
        reset_border_color_btn.setToolTip(self.tr("Reset to default blue (#2196F3)"))
        def reset_border_color():
            border_color_btn.setStyleSheet("background-color: #2196F3; border: 1px solid #999;")
            border_color_btn.setProperty('selected_color', '#2196F3')
        reset_border_color_btn.clicked.connect(reset_border_color)
        border_color_layout.addWidget(reset_border_color_btn)
        border_color_layout.addStretch()
        focus_border_layout.addLayout(border_color_layout)
        
        # Border thickness spinbox
        thickness_layout = QHBoxLayout()
        thickness_layout.addWidget(QLabel(self.tr("Border Thickness:")))
        border_thickness_spin = QSpinBox()
        border_thickness_spin.setMinimum(1)
        border_thickness_spin.setMaximum(10)
        border_thickness_spin.setValue(font_settings.get('focus_border_thickness', 2))
        border_thickness_spin.setSuffix(" px")
        border_thickness_spin.setMinimumWidth(90)
        border_thickness_spin.setMinimumHeight(28)
        border_thickness_spin.setButtonSymbols(QSpinBox.ButtonSymbols.UpDownArrows)
        border_thickness_spin.setToolTip(self.tr("Thickness of the focus border (1-10 pixels)"))
        # Fix spinbox arrow buttons - ensure both up and down work correctly
        border_thickness_spin.setStyleSheet("""
            QSpinBox {
                padding-right: 20px;
            }
            QSpinBox::up-button {
                width: 20px;
                height: 14px;
            }
            QSpinBox::down-button {
                width: 20px;
                height: 14px;
            }
        """)
        thickness_layout.addWidget(border_thickness_spin)
        thickness_layout.addStretch()
        focus_border_layout.addLayout(thickness_layout)
        
        focus_border_group.setLayout(focus_border_layout)
        layout.addWidget(focus_border_group)
        
        # TermLens placement is now controlled live from the View menu
        # ("Show TermLens above grid" / "Show TermLens below grid"), so there is
        # no longer a settings checkbox for it here.

        # Glossary Highlight Style section
        tb_highlight_group = QGroupBox(self.tr("🏷️ Termbase Highlight Style"))
        tb_highlight_layout = QVBoxLayout()
        
        tb_highlight_info = QLabel(
            "Choose how termbase matches are highlighted in the source text.\n"
            "Different styles offer varying levels of visual prominence."
        )
        tb_highlight_info.setStyleSheet("font-size: 8pt; padding: 8px; border-radius: 2px;")
        tb_highlight_info.setWordWrap(True)
        tb_highlight_layout.addWidget(tb_highlight_info)
        
        # Style selection radio buttons
        tb_style_layout = QVBoxLayout()
        
        # Get current setting
        current_tb_style = font_settings.get('termbase_highlight_style', 'semibold')
        
        # Background highlight (pre-v1.10.211 default)
        tb_style_background = CheckmarkRadioButton(self.tr("Background Color - Pastel green background"))
        tb_style_background.setToolTip(self.tr("Traditional highlight with pastel green background colors based on priority"))
        tb_style_background.setChecked(current_tb_style == 'background')
        tb_style_layout.addWidget(tb_style_background)

        # Dotted underline (code editor style)
        tb_style_dotted = CheckmarkRadioButton(self.tr("Dotted Underline - Subtle dotted line below text (IDE style)"))
        tb_style_dotted.setToolTip(self.tr("Unobtrusive dotted underline like code editors use for suggestions"))
        tb_style_dotted.setChecked(current_tb_style == 'dotted')
        tb_style_layout.addWidget(tb_style_dotted)

        # Semibold text (typographic) – v1.10.211+ default
        tb_style_semibold = CheckmarkRadioButton(self.tr("Semibold Text - Slightly bolder text with tinted color (default)"))
        tb_style_semibold.setToolTip(self.tr("Typographic approach: text appears slightly heavier/darker without adding visual elements"))
        tb_style_semibold.setChecked(current_tb_style == 'semibold')
        tb_style_layout.addWidget(tb_style_semibold)
        
        # Button group for mutual exclusion
        tb_style_group = QButtonGroup(self)
        tb_style_group.addButton(tb_style_background, 0)
        tb_style_group.addButton(tb_style_dotted, 1)
        tb_style_group.addButton(tb_style_semibold, 2)
        
        tb_highlight_layout.addLayout(tb_style_layout)
        
        # Dotted underline color picker (only shown when dotted style selected)
        dotted_color_layout = QHBoxLayout()
        dotted_color_label = QLabel(self.tr("Underline Color:"))
        dotted_color_layout.addWidget(dotted_color_label)
        
        dotted_underline_color = font_settings.get('termbase_dotted_color', '#808080')
        dotted_color_btn = QPushButton()
        dotted_color_btn.setFixedSize(80, 25)
        dotted_color_btn.setStyleSheet(f"background-color: {dotted_underline_color}; border: 1px solid #999;")
        dotted_color_btn.setToolTip(self.tr("Color for dotted underline (soft blue-grey recommended)"))
        
        def choose_dotted_color():
            color = QColorDialog.getColor(QColor(dotted_underline_color), self, "Choose Dotted Underline Color")
            if color.isValid():
                hex_color = color.name()
                dotted_color_btn.setStyleSheet(f"background-color: {hex_color}; border: 1px solid #999;")
                dotted_color_btn.setProperty('selected_color', hex_color)
        
        dotted_color_btn.clicked.connect(choose_dotted_color)
        dotted_color_btn.setProperty('selected_color', dotted_underline_color)
        dotted_color_layout.addWidget(dotted_color_btn)
        
        # Reset to default color button
        reset_dotted_btn = QPushButton(self.tr("Reset"))
        reset_dotted_btn.setFixedWidth(50)
        reset_dotted_btn.setToolTip(self.tr("Reset to default color (#808080)"))
        def reset_dotted_color():
            dotted_color_btn.setStyleSheet("background-color: #808080; border: 1px solid #999;")
            dotted_color_btn.setProperty('selected_color', '#808080')
        reset_dotted_btn.clicked.connect(reset_dotted_color)
        dotted_color_layout.addWidget(reset_dotted_btn)
        
        dotted_color_layout.addStretch()
        tb_highlight_layout.addLayout(dotted_color_layout)
        
        # Store references for save function
        self._tb_style_group = tb_style_group
        self._tb_dotted_color_btn = dotted_color_btn
        
        tb_highlight_group.setLayout(tb_highlight_layout)
        layout.addWidget(tb_highlight_group)
        
        # TermLens Font Settings section
        termlens_group = QGroupBox(self.tr("🔍 TermLens Font Settings"))
        termlens_layout = QVBoxLayout()
        
        termlens_info = QLabel(
            self.tr("Configure the font appearance for the TermLens inline terminology display.")
        )
        termlens_info.setStyleSheet("font-size: 8pt; padding: 8px; border-radius: 2px;")
        termlens_info.setWordWrap(True)
        termlens_layout.addWidget(termlens_info)
        
        # Font family dropdown
        termlens_font_family_layout = QHBoxLayout()
        termlens_font_family_layout.addWidget(QLabel(self.tr("Font Family:")))
        termlens_font_family_combo = QComboBox()
        termlens_font_family_combo.addItems(font_families)  # Same fonts as grid
        current_termlens_font_family = font_settings.get('termlens_font_family', 'Segoe UI')
        if current_termlens_font_family in font_families:
            termlens_font_family_combo.setCurrentText(current_termlens_font_family)
        else:
            termlens_font_family_combo.setCurrentText("Segoe UI")
        termlens_font_family_combo.setToolTip(self.tr("Select the font family for TermLens text"))
        termlens_font_family_layout.addWidget(termlens_font_family_combo)
        termlens_font_family_layout.addStretch()
        termlens_layout.addLayout(termlens_font_family_layout)
        
        # Font size spinbox
        termlens_size_layout = QHBoxLayout()
        termlens_size_layout.addWidget(QLabel(self.tr("Font Size:")))
        termlens_font_spin = QSpinBox()
        termlens_font_spin.setMinimum(6)
        termlens_font_spin.setMaximum(16)
        termlens_font_spin.setValue(font_settings.get('termlens_font_size', 10))
        termlens_font_spin.setSuffix(" pt")
        termlens_font_spin.setToolTip(self.tr("TermLens font size (6-16 pt)"))
        termlens_font_spin.setMinimumHeight(28)
        termlens_font_spin.setMinimumWidth(80)
        # Fix spinbox arrow buttons - ensure both up and down work correctly
        termlens_font_spin.setStyleSheet("""
            QSpinBox {
                padding-right: 20px;
            }
            QSpinBox::up-button {
                width: 20px;
                height: 14px;
            }
            QSpinBox::down-button {
                width: 20px;
                height: 14px;
            }
        """)
        termlens_size_layout.addWidget(termlens_font_spin)
        termlens_size_layout.addStretch()
        termlens_layout.addLayout(termlens_size_layout)
        
        # Font weight checkbox
        termlens_bold_layout = QHBoxLayout()
        termlens_bold_check = CheckmarkCheckBox(self.tr("Bold font"))
        termlens_bold_check.setChecked(font_settings.get('termlens_font_bold', False))
        termlens_bold_check.setToolTip(self.tr("Display TermLens text in bold"))
        termlens_bold_layout.addWidget(termlens_bold_check)
        termlens_bold_layout.addStretch()
        termlens_layout.addLayout(termlens_bold_layout)
        
        termlens_group.setLayout(termlens_layout)
        layout.addWidget(termlens_group)

        # ===== Match Panel Font Settings =====
        mp_font_group = QGroupBox(self.tr("📊 Match Panel Font Settings"))
        mp_font_layout = QVBoxLayout()

        mp_font_info = QLabel(
            self.tr("Configure the font appearance for the TM Source and TM Target boxes in the Match Panel.")
        )
        mp_font_info.setStyleSheet("font-size: 8pt; padding: 8px; border-radius: 2px;")
        mp_font_info.setWordWrap(True)
        mp_font_layout.addWidget(mp_font_info)

        # Font family dropdown
        mp_font_family_layout = QHBoxLayout()
        mp_font_family_layout.addWidget(QLabel(self.tr("Font Family:")))
        mp_font_family_combo = QComboBox()
        mp_font_family_combo.addItems(font_families)
        current_mp_font_family = font_settings.get('match_panel_font_family', '')
        if current_mp_font_family and current_mp_font_family in font_families:
            mp_font_family_combo.setCurrentText(current_mp_font_family)
        else:
            # Show placeholder when using system default
            mp_font_family_combo.setCurrentIndex(0)
        mp_font_family_combo.setToolTip(self.tr("Select the font family for TM Source/Target text in the Match Panel"))
        mp_font_family_layout.addWidget(mp_font_family_combo)
        mp_font_family_layout.addStretch()
        mp_font_layout.addLayout(mp_font_family_layout)

        # Font size spinbox
        mp_size_layout = QHBoxLayout()
        mp_size_layout.addWidget(QLabel(self.tr("Font Size:")))
        mp_font_spin = QSpinBox()
        mp_font_spin.setMinimum(6)
        mp_font_spin.setMaximum(18)
        mp_font_spin.setValue(font_settings.get('match_panel_font_size', 10))
        mp_font_spin.setSuffix(" pt")
        mp_font_spin.setToolTip(self.tr("Match Panel TM font size (6-18 pt). Can also be adjusted with View → Match Panel zoom shortcuts."))
        mp_font_spin.setMinimumHeight(28)
        mp_font_spin.setMinimumWidth(80)
        mp_font_spin.setStyleSheet("""
            QSpinBox {
                padding-right: 20px;
            }
            QSpinBox::up-button {
                width: 20px;
                height: 14px;
            }
            QSpinBox::down-button {
                width: 20px;
                height: 14px;
            }
        """)
        mp_size_layout.addWidget(mp_font_spin)
        mp_size_layout.addStretch()
        mp_font_layout.addLayout(mp_size_layout)

        # Font weight checkbox
        mp_bold_layout = QHBoxLayout()
        mp_bold_check = CheckmarkCheckBox(self.tr("Bold font"))
        mp_bold_check.setChecked(font_settings.get('match_panel_font_bold', False))
        mp_bold_check.setToolTip(self.tr("Display TM Source/Target text in bold"))
        mp_bold_layout.addWidget(mp_bold_check)
        mp_bold_layout.addStretch()
        mp_font_layout.addLayout(mp_bold_layout)

        mp_font_group.setLayout(mp_font_layout)
        layout.addWidget(mp_font_group)

        # ===== Global UI Font Scale =====
        ui_scale_group = QGroupBox(self.tr("🖥️ Global UI Font Scale"))
        ui_scale_layout = QVBoxLayout()

        ui_scale_info = QLabel(
            "Adjust the font size for the entire application UI. Useful for Linux/macOS users where\n"
            "Qt applications may render with smaller fonts, or for high-DPI displays.\n"
            "Changes apply immediately. Default is 100%."
        )
        ui_scale_info.setWordWrap(True)
        ui_scale_layout.addWidget(ui_scale_info)

        ui_scale_row = QHBoxLayout()
        ui_scale_row.addWidget(QLabel(self.tr("UI Font Scale:")))
        ui_scale_spin = QSpinBox()
        ui_scale_spin.setMinimum(50)
        ui_scale_spin.setMaximum(200)
        ui_scale_spin.setValue(font_settings.get('global_ui_font_scale', font_settings.get('settings_ui_font_scale', 100)))
        ui_scale_spin.setSuffix("%")
        ui_scale_spin.setSingleStep(10)
        ui_scale_spin.setToolTip(self.tr("Scale entire application UI text (50%-200%)"))
        ui_scale_spin.setMinimumHeight(28)
        ui_scale_spin.setMinimumWidth(90)
        ui_scale_spin.setStyleSheet("""
            QSpinBox {
                padding-right: 20px;
            }
            QSpinBox::up-button {
                width: 20px;
                height: 14px;
            }
            QSpinBox::down-button {
                width: 20px;
                height: 14px;
            }
        """)
        ui_scale_row.addWidget(ui_scale_spin)

        # Apply button for immediate feedback
        apply_scale_btn = QPushButton(self.tr("Apply"))
        apply_scale_btn.setToolTip(self.tr("Apply font scale immediately"))
        apply_scale_btn.clicked.connect(lambda: self._apply_global_ui_font_scale(ui_scale_spin.value()))
        ui_scale_row.addWidget(apply_scale_btn)
        
        ui_scale_row.addStretch()
        ui_scale_layout.addLayout(ui_scale_row)
        
        # Store reference for saving
        self._ui_scale_spin = ui_scale_spin
        
        ui_scale_group.setLayout(ui_scale_layout)
        layout.addWidget(ui_scale_group)

        # ===== Compact UI Chrome =====
        chrome_group = QGroupBox(self.tr("📐 Compact UI Chrome"))
        chrome_layout = QVBoxLayout()

        chrome_info = QLabel(
            "Reduce vertical padding on the menu bar, main tab strip, and right-panel tab "
            "strip to give the translation grid more room. Takes effect immediately."
        )
        chrome_info.setWordWrap(True)
        chrome_layout.addWidget(chrome_info)

        compact_chrome_check = CheckmarkCheckBox(self.tr("Compact UI chrome (tighter menu / tab padding)"))
        compact_chrome_check.setChecked(self._get_ui_chrome_compact())
        compact_chrome_check.setToolTip(self.tr("Tighten padding on the menu bar and tab strips to make more room for the translation grid"))
        compact_chrome_check.toggled.connect(self._apply_ui_chrome_compactness)
        chrome_layout.addWidget(compact_chrome_check)

        chrome_group.setLayout(chrome_layout)
        layout.addWidget(chrome_group)

        # Quick Reference section
        reference_group = QGroupBox(self.tr("⌨️ Font Size Quick Reference"))
        reference_layout = QVBoxLayout()
        
        reference_text = QLabel(
            "<b>All Zoom Controls:</b><br>"
            "View → Grid Text Zoom<br>"
            f"• {format_shortcut_for_display('Ctrl+=')} (or {format_shortcut_for_display('Ctrl+NumPad +')}) - Increase<br>"
            f"• {format_shortcut_for_display('Ctrl+-')} (or {format_shortcut_for_display('Ctrl+NumPad -')}) - Decrease<br><br>"
            "View → Translation Results Pane<br>"
            f"• {format_shortcut_for_display('Ctrl+Shift++')} ({format_shortcut_for_display('Ctrl+Shift+=')}) - Increase<br>"
            f"• {format_shortcut_for_display('Ctrl+Shift+-')} - Decrease<br>"
            "• Results Zoom Reset - Back to default (9pt)<br>"
        )
        reference_text.setTextFormat(Qt.TextFormat.RichText)
        reference_text.setWordWrap(True)
        reference_layout.addWidget(reference_text)
        
        reference_group.setLayout(reference_layout)
        layout.addWidget(reference_group)
        
        # Save button
        save_btn = QPushButton(self.tr("💾 Save View Settings"))
        save_btn.setProperty("settings_save_button", True)  # clicked for the user by the auto-saver (issue #214)
        save_btn.setStyleSheet("font-weight: bold; padding: 8px;")
        
        def save_view_settings_with_scale():
            # Save the UI scale setting first (only if scale actually changed)
            if hasattr(self, '_ui_scale_spin'):
                new_scale = self._ui_scale_spin.value()
                current_scale = self._get_global_ui_font_scale()
                if new_scale != current_scale:
                    self._apply_global_ui_font_scale(new_scale)
            # Then save other view settings
            self._save_view_settings_from_ui(
                grid_font_spin, match_font_spin, compare_font_spin, show_tags_check, tag_color_btn,
                alt_colors_check, even_color_btn, odd_color_btn, invisible_char_color_btn, grid_font_family_combo,
                termlens_font_family_combo, termlens_font_spin, termlens_bold_check,
                border_color_btn, border_thickness_spin, badge_text_color_btn, None,
                hide_wrapping_tags_check, status_before_target_check,
                mp_font_family_combo=mp_font_family_combo, mp_font_spin=mp_font_spin, mp_bold_check=mp_bold_check
            )

        save_btn.clicked.connect(save_view_settings_with_scale)
        layout.addWidget(save_btn)
        
        layout.addStretch()

        return tab

    def _create_system_prompts_tab(self):
        """Create System Prompts (Layer 1) Settings tab content"""
        from PyQt6.QtWidgets import QGroupBox, QPushButton, QTextEdit, QComboBox

        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(15)

        # Header info
        header_group = QGroupBox(self.tr("📝 System Prompts (Layer 1)"))
        header_layout = QVBoxLayout()

        info_label = QLabel(
            "<b>System Prompts are the foundation of Supervertaler's 2-Layer Prompt Architecture.</b><br><br>"
            "These prompts are <b>always applied</b> and contain critical infrastructure instructions:<br>"
            "• CAT tool tag preservation (memoQ, Trados, CafeTran)<br>"
            "• Formatting rules and output standards<br>"
            "• Language-specific conventions (numbers, dates, typography)<br>"
            "• Professional translation context<br><br>"
            "<i>Layer 2 (Custom Prompts) are managed in the Prompts tab and can be freely attached/detached.</i>"
        )
        info_label.setTextFormat(Qt.TextFormat.RichText)
        info_label.setWordWrap(True)
        info_label.setStyleSheet("font-size: 9pt; padding: 12px; border-radius: 3px;")
        header_layout.addWidget(info_label)

        header_group.setLayout(header_layout)
        layout.addWidget(header_group)

        # Mode selector
        mode_group = QGroupBox(self.tr("🎯 Select System Prompt Mode"))
        mode_layout = QVBoxLayout()

        mode_info = QLabel(
            self.tr("Supervertaler uses different system prompts for different translation modes:")
        )
        mode_info.setStyleSheet("font-size: 9pt; color: #666; padding: 8px;")
        mode_info.setWordWrap(True)
        mode_layout.addWidget(mode_info)

        mode_selector_layout = QHBoxLayout()
        mode_selector_layout.addWidget(QLabel(self.tr("Mode:")))
        mode_combo = QComboBox()
        mode_combo.addItems([
            "Single Segment Translation",
            "Batch DOCX Translation",
            "Batch Bilingual Translation",
            "FuzzyFixer Instruction",
            "AutoTagger Instruction"
        ])
        mode_combo.setToolTip(self.tr("Select which system prompt to view/edit"))
        mode_selector_layout.addWidget(mode_combo)
        mode_selector_layout.addStretch()
        sysprompt_help_btn = QPushButton("?")
        sysprompt_help_btn.setFixedSize(22, 22)
        sysprompt_help_btn.setToolTip(self.tr("Open help for the selected system prompt (online)"))
        sysprompt_help_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        def _open_sysprompt_help():
            mode = mode_combo.currentText()
            if mode == "FuzzyFixer Instruction":
                open_help(HelpTopics.AI_FUZZY_FIXER)
            elif mode == "AutoTagger Instruction":
                open_help(HelpTopics.AI_AUTOTAGGER)
            else:
                open_help(HelpTopics.AI_PROMPTS)
        sysprompt_help_btn.clicked.connect(lambda: _open_sysprompt_help())
        mode_selector_layout.addWidget(sysprompt_help_btn)
        mode_layout.addLayout(mode_selector_layout)

        mode_group.setLayout(mode_layout)
        layout.addWidget(mode_group)

        # Editor
        editor_group = QGroupBox(self.tr("✏️ Edit System Prompt"))
        editor_layout = QVBoxLayout()

        editor_info = QLabel(
            self.tr("Edit the system prompt below. Use {{SOURCE_LANGUAGE}}, {{TARGET_LANGUAGE}}, and {{SOURCE_TEXT}} as placeholders. "
                    "The FuzzyFixer Instruction additionally supports {{TM_SOURCE}}, {{TM_TARGET}}, {{MATCH_PCT}} and {{TM_NAME}}. "
                    "The AutoTagger Instruction supports {{SOURCE_TEXT}}, {{TARGET_TEXT}} and {{TAG_LIST}}.")
        )
        editor_info.setStyleSheet("font-size: 8pt; padding: 8px; border-radius: 2px;")
        editor_info.setWordWrap(True)
        editor_layout.addWidget(editor_info)

        system_prompt_editor = QTextEdit()
        system_prompt_editor.setStyleSheet("font-family: 'Consolas', 'Courier New', monospace; font-size: 9pt;")
        system_prompt_editor.setMinimumHeight(500)  # Increased from 400 to 500
        system_prompt_editor.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)  # Enable word wrap
        system_prompt_editor.setAcceptRichText(False)  # Plain text only
        editor_layout.addWidget(system_prompt_editor, 1)  # Added stretch factor of 1 to allow expansion

        editor_group.setLayout(editor_layout)
        layout.addWidget(editor_group, 1)  # Added stretch factor of 1 to allow expansion

        # Buttons
        buttons_layout = QHBoxLayout()

        reset_btn = QPushButton(self.tr("🔄 Reset to Default"))
        reset_btn.setToolTip(self.tr("Restore the default system prompt for this mode"))
        reset_btn.clicked.connect(lambda: self._reset_system_prompt(mode_combo, system_prompt_editor))
        buttons_layout.addWidget(reset_btn)

        buttons_layout.addStretch()

        save_btn = QPushButton(self.tr("💾 Save System Prompt"))
        save_btn.setProperty("settings_save_button", True)  # clicked for the user by the auto-saver (issue #214)
        save_btn.setStyleSheet("font-weight: bold; padding: 8px;")
        save_btn.clicked.connect(lambda: self._save_system_prompt_from_ui(mode_combo, system_prompt_editor))
        buttons_layout.addWidget(save_btn)

        layout.addLayout(buttons_layout)

        # Load initial prompt
        self._load_system_prompt_into_editor(mode_combo, system_prompt_editor)

        # Connect mode change to load new prompt
        mode_combo.currentIndexChanged.connect(lambda: self._load_system_prompt_into_editor(mode_combo, system_prompt_editor))

        return tab

    def _load_system_prompt_into_editor(self, mode_combo, editor):
        """Load the selected system prompt into the editor"""
        mode_map = {
            "Single Segment Translation": "single",
            "Batch DOCX Translation": "batch_docx",
            "Batch Bilingual Translation": "batch_bilingual",
            "FuzzyFixer Instruction": "fuzzy_fixer",
            "AutoTagger Instruction": "autotagger"
        }

        selected_mode = mode_combo.currentText()
        mode_key = mode_map.get(selected_mode, "single")

        # Load from prompt_manager_qt if available
        if hasattr(self, 'prompt_manager_qt'):
            if mode_key == "fuzzy_fixer":
                prompt_text = self.prompt_manager_qt.get_fuzzy_fixer_template()
            elif mode_key == "autotagger":
                prompt_text = self.prompt_manager_qt.get_autotagger_template()
            else:
                prompt_text = self.prompt_manager_qt.get_system_template(mode_key)
        else:
            # Fallback: load from JSON file
            system_prompts_file = self.user_data_path / "prompt_library" / "system_prompts_layer1.json"
            if system_prompts_file.exists():
                import json
                with open(system_prompts_file, 'r', encoding='utf-8') as f:
                    system_prompts = json.load(f)
                prompt_text = system_prompts.get(mode_key, "# SYSTEM PROMPT\n\nNo prompt defined.")
            else:
                prompt_text = "# SYSTEM PROMPT\n\nNo prompt defined."

        editor.setPlainText(prompt_text)

    def _save_system_prompt_from_ui(self, mode_combo, editor):
        """Save the edited system prompt"""
        mode_map = {
            "Single Segment Translation": "single",
            "Batch DOCX Translation": "batch_docx",
            "Batch Bilingual Translation": "batch_bilingual",
            "FuzzyFixer Instruction": "fuzzy_fixer",
            "AutoTagger Instruction": "autotagger"
        }

        selected_mode = mode_combo.currentText()
        mode_key = mode_map.get(selected_mode, "single")
        prompt_text = editor.toPlainText()

        # Save to prompt_manager_qt if available
        if hasattr(self, 'prompt_manager_qt'):
            self.prompt_manager_qt.system_templates[mode_key] = prompt_text

        # Always save to JSON file
        import json
        system_prompts_file = self.user_data_path / "prompt_library" / "system_prompts_layer1.json"
        system_prompts_file.parent.mkdir(parents=True, exist_ok=True)

        # Load existing prompts
        if system_prompts_file.exists():
            with open(system_prompts_file, 'r', encoding='utf-8') as f:
                system_prompts = json.load(f)
        else:
            system_prompts = {}

        # Update and save
        system_prompts[mode_key] = prompt_text
        with open(system_prompts_file, 'w', encoding='utf-8') as f:
            json.dump(system_prompts, f, indent=2, ensure_ascii=False)

        self.log(f"✓ Saved system prompt: {selected_mode}")
        QMessageBox.information(
            self,
            "System Prompt Saved",
            f"System prompt for '{selected_mode}' has been saved successfully."
        )

    def _reset_system_prompt(self, mode_combo, editor):
        """Reset system prompt to default"""
        mode_map = {
            "Single Segment Translation": "single",
            "Batch DOCX Translation": "batch_docx",
            "Batch Bilingual Translation": "batch_bilingual",
            "FuzzyFixer Instruction": "fuzzy_fixer",
            "AutoTagger Instruction": "autotagger"
        }

        selected_mode = mode_combo.currentText()
        mode_key = mode_map.get(selected_mode, "single")

        # Confirm reset
        reply = QMessageBox.question(
            self,
            "Reset System Prompt",
            f"Are you sure you want to reset the system prompt for '{selected_mode}' to its default value?\n\n"
            "This will discard any custom changes you've made.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )

        if reply == QMessageBox.StandardButton.Yes:
            # Get default from prompt_manager_qt
            if hasattr(self, 'prompt_manager_qt'):
                if mode_key == "fuzzy_fixer":
                    default_prompt = self.prompt_manager_qt._get_default_fuzzy_fixer_template()
                elif mode_key == "autotagger":
                    default_prompt = self.prompt_manager_qt._get_default_autotagger_template()
                else:
                    default_prompt = self.prompt_manager_qt._get_default_system_template(mode_key)
            else:
                default_prompt = "# SYSTEM PROMPT\n\nNo default prompt available."
            editor.setPlainText(default_prompt)
            self.log(f"✓ Reset system prompt to default: {selected_mode}")

    def _create_user_identity_tab(self):
        """Create User Identity settings tab – translator name used in file exports."""
        from PyQt6.QtWidgets import QGroupBox, QPushButton

        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(15)

        settings = self.load_general_settings()

        # Header
        header = QLabel(
            "👤 <b>User Identity</b><br>"
            "<span style='color:#666;'>Configure the translator name that appears in exported files. "
            "This name is written to SDLXLIFF comments, Trados return packages, TMX files, and other output.</span>"
        )
        header.setTextFormat(Qt.TextFormat.RichText)
        header.setWordWrap(True)
        header.setStyleSheet("font-size: 9pt; padding: 10px; background-color: #E3F2FD; border-radius: 4px;")
        layout.addWidget(header)

        # Translator name group
        name_group = QGroupBox(self.tr("Translator Name"))
        name_layout = QVBoxLayout()

        name_info = QLabel(
            "Enter your name or alias as you want it to appear in exported files.\n"
            "If left empty, your system username will be used as a fallback."
        )
        name_info.setWordWrap(True)
        name_info.setStyleSheet("font-size: 9pt; color: #555;")
        name_layout.addWidget(name_info)

        name_layout.addSpacing(5)

        name_edit = QLineEdit()
        name_edit.setPlaceholderText(self.tr("(uses system username if empty)"))
        name_edit.setText(settings.get('translator_name', ''))
        name_edit.setMaxLength(100)
        name_layout.addWidget(name_edit)

        # Show current fallback
        import os as _os
        fallback = _os.environ.get('USERNAME', _os.environ.get('USER', ''))
        if fallback:
            fallback_label = QLabel(f"Current system username: <b>{fallback}</b>")
            fallback_label.setTextFormat(Qt.TextFormat.RichText)
            fallback_label.setStyleSheet("font-size: 8pt; color: #888; margin-top: 3px;")
            name_layout.addWidget(fallback_label)

        name_group.setLayout(name_layout)
        layout.addWidget(name_group)

        # Save button
        save_btn = QPushButton(self.tr("💾 Save User Identity"))
        save_btn.setProperty("settings_save_button", True)  # clicked for the user by the auto-saver (issue #214)
        save_btn.setStyleSheet("font-weight: bold; padding: 8px;")
        save_btn.clicked.connect(lambda: self._save_user_identity_from_ui(name_edit))
        layout.addWidget(save_btn)

        layout.addStretch()
        return tab

    def _save_user_identity_from_ui(self, name_edit):
        """Save user identity settings."""
        general_settings = self.load_general_settings()
        general_settings['translator_name'] = name_edit.text().strip()
        self.save_general_settings(general_settings)

        saved_name = name_edit.text().strip()
        display = saved_name if saved_name else "(system username)"
        self.log(f"✓ User identity saved: translator name = {display}")
        QMessageBox.information(self, "Settings Saved", f"User identity saved.\nTranslator name: {display}")

    def open_settings_to_keyboard_shortcuts(self):
        """Jump to Settings → Keyboard Shortcuts.

        Called from the Voice tab's "Change in Settings → Keyboard
        Shortcuts" link. Switches the main window to the Settings page
        and selects the Keyboard Shortcuts sub-tab.
        """
        try:
            main_tabs = getattr(self, 'main_tabs', None)
            settings_tabs = getattr(self, 'settings_tabs', None)
            settings_idx = getattr(self, 'settings_tab_index', None)
            kb_idx = getattr(self, 'keyboard_shortcuts_tab_index', None)
            if main_tabs is not None and settings_idx is not None:
                main_tabs.setCurrentIndex(settings_idx)
            if settings_tabs is not None and kb_idx is not None:
                settings_tabs.setCurrentIndex(kb_idx)
            self._bring_workbench_forward()
        except Exception as e:
            QMessageBox.information(
                self, "Open Keyboard Shortcuts",
                f"Couldn't auto-navigate (\"{e}\"). Open the main "
                "Workbench window → Settings → Keyboard Shortcuts and "
                "find <b>Voice Always-On (toggle)</b> under "
                "<b>Special</b>."
            )

    def _create_debug_settings_tab(self):
        """Create Debug Settings tab content"""
        from PyQt6.QtWidgets import QGroupBox, QPushButton, QTextEdit
        
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(15)
        
        debug_settings = self.load_general_settings()
        
        # Debug Mode section
        debug_group = QGroupBox(self.tr("🐛 Debug Mode"))
        debug_layout = QVBoxLayout()
        
        info_label = QLabel(
            "<b>Debug Mode</b> enables verbose logging to help diagnose performance issues.\n\n"
            "When enabled, detailed logs will show:\n"
            "• Every text change event in the grid\n"
            "• Database update operations (BEFORE/AFTER)\n"
            "• Cell selection events\n"
            "• Signal connections\n\n"
            "⚠️ <b>Warning:</b> Debug mode generates a LOT of log output and may slow down the application.\n"
            "Only enable when troubleshooting specific issues."
        )
        info_label.setWordWrap(True)
        info_label.setStyleSheet("font-size: 9pt; padding: 10px; border-radius: 4px;")
        debug_layout.addWidget(info_label)
        
        debug_layout.addSpacing(10)
        
        # Enable debug mode checkbox
        debug_mode_cb = CheckmarkCheckBox(self.tr("Enable verbose debug logging"))
        debug_mode_cb.setChecked(debug_settings.get('debug_mode_enabled', False))
        debug_mode_cb.setToolTip(
            "When enabled, shows detailed debug messages in the log.\n"
            "This helps diagnose performance issues but generates a lot of output."
        )
        debug_layout.addWidget(debug_mode_cb)
        
        # Auto-export debug logs checkbox
        debug_export_cb = CheckmarkCheckBox(self.tr("Auto-export debug logs to file"))
        debug_export_cb.setChecked(debug_settings.get('debug_auto_export', False))
        debug_export_cb.setToolTip(
            "When enabled, automatically saves debug logs to:\n"
            "supervertaler_debug_log_<timestamp>.txt in the program root.\n"
            "Logs are exported every 5 minutes or when you close the application."
        )
        debug_export_cb.setEnabled(debug_mode_cb.isChecked())  # Only enabled if debug mode is on
        debug_layout.addWidget(debug_export_cb)
        
        # Link the checkboxes
        debug_mode_cb.toggled.connect(lambda checked: debug_export_cb.setEnabled(checked))
        
        debug_layout.addSpacing(10)
        
        # Export log button
        export_btn = QPushButton(self.tr("📁 Export Debug Log Now"))
        export_btn.setToolTip(self.tr("Export the current debug log buffer to a timestamped file"))
        export_btn.clicked.connect(self.export_debug_log_now)
        debug_layout.addWidget(export_btn)
        
        # Clear log buffer button
        clear_btn = QPushButton(self.tr("🗑️ Clear Debug Log Buffer"))
        clear_btn.setToolTip(self.tr("Clear the in-memory debug log buffer"))
        clear_btn.clicked.connect(self.clear_debug_log_buffer)
        debug_layout.addWidget(clear_btn)
        
        debug_group.setLayout(debug_layout)
        layout.addWidget(debug_group)
        
        # Performance Settings section
        perf_group = QGroupBox(self.tr("⚡ Performance Settings"))
        perf_layout = QVBoxLayout()
        
        perf_info = QLabel(
            "<b>Performance Tuning</b> - These settings control UI responsiveness.\n\n"
            "Higher delays = more responsive typing but slower lookups.\n"
            "Lower delays = faster lookups but potential UI freezes during typing."
        )
        perf_info.setWordWrap(True)
        perf_info.setStyleSheet("font-size: 9pt; padding: 10px; border-radius: 4px;")
        perf_layout.addWidget(perf_info)
        
        perf_layout.addSpacing(10)
        
        # Target text debounce delay
        debounce_layout = QHBoxLayout()
        debounce_layout.addWidget(QLabel(self.tr("Target text save delay:")))
        debounce_spin = QSpinBox()
        debounce_spin.setRange(100, 5000)
        debounce_spin.setValue(debug_settings.get('target_debounce_delay', 1000))
        debounce_spin.setSingleStep(100)
        debounce_spin.setSuffix(" ms")
        debounce_spin.setToolTip(
            "Delay before saving target text changes to database.\n"
            "1000ms (default) = good balance between responsiveness and data safety.\n"
            "Higher values = less database overhead but longer delay before saving."
        )
        debounce_layout.addWidget(debounce_spin)
        debounce_layout.addStretch()
        perf_layout.addLayout(debounce_layout)
        
        perf_group.setLayout(perf_layout)
        layout.addWidget(perf_group)
        
        # Save button
        save_btn = QPushButton(self.tr("💾 Save Debug Settings"))
        save_btn.setProperty("settings_save_button", True)  # clicked for the user by the auto-saver (issue #214)
        save_btn.setStyleSheet("font-weight: bold; padding: 8px;")
        save_btn.clicked.connect(lambda: self._save_debug_settings_from_ui(
            debug_mode_cb, debug_export_cb, debounce_spin
        ))
        layout.addWidget(save_btn)
        
        layout.addStretch()
        
        return tab

    def _save_debug_settings_from_ui(self, debug_mode_cb, debug_export_cb, debounce_spin):
        """Save debug settings from UI"""
        self.debug_mode_enabled = debug_mode_cb.isChecked()
        self.debug_auto_export = debug_export_cb.isChecked()
        
        general_settings = self.load_general_settings()
        general_settings['debug_mode_enabled'] = self.debug_mode_enabled
        general_settings['debug_auto_export'] = self.debug_auto_export
        general_settings['target_debounce_delay'] = debounce_spin.value()
        self.save_general_settings(general_settings)
        
        self.log(f"✓ Debug settings saved: Debug Mode={'ON' if self.debug_mode_enabled else 'OFF'}, Auto-export={'ON' if self.debug_auto_export else 'OFF'}")
        QMessageBox.information(self, "Settings Saved", "Debug settings have been saved successfully.")

    def export_debug_log_now(self):
        """Export debug log buffer to file immediately"""
        from datetime import datetime
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"supervertaler_debug_log_{timestamp}.txt"
        
        try:
            with open(filename, 'w', encoding='utf-8') as f:
                f.write(f"Supervertaler Workbench Debug Log\n")
                f.write(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
                f.write("="*80 + "\n\n")
                
                if self.debug_log_buffer:
                    f.write("\n".join(self.debug_log_buffer))
                else:
                    f.write("(No debug logs in buffer)")
            
            self.log(f"✓ Debug log exported to: {filename}")
            QMessageBox.information(
                self,
                "Log Exported",
                f"Debug log exported successfully to:\n{filename}\n\n"
                f"Log entries: {len(self.debug_log_buffer)}"
            )
        except Exception as e:
            self.log(f"❌ Error exporting debug log: {e}")
            QMessageBox.warning(self, "Export Failed", f"Failed to export debug log:\n{e}")

    def clear_debug_log_buffer(self):
        """Clear the debug log buffer"""
        buffer_size = len(self.debug_log_buffer)
        self.debug_log_buffer.clear()
        self.log(f"✓ Debug log buffer cleared ({buffer_size} entries removed)")
        QMessageBox.information(self, "Buffer Cleared", f"Debug log buffer cleared.\n{buffer_size} entries removed.")

    def _save_llm_settings_from_ui(self, openai_radio, claude_radio, gemini_radio, ollama_radio,
                                   openai_combo, claude_combo, gemini_combo,
                                   ollama_status_widget,
                                   openai_enable_cb, claude_enable_cb, gemini_enable_cb, ollama_enable_cb,
                                   batch_size_spin, surrounding_spin, full_context_cb, context_slider,
                                   tm_no_check_rb, tm_fuzzy_rb, tm_exact_rb, auto_propagate_cb,
                                   delay_spin):
        """Save LLM settings from UI"""
        # Determine selected provider
        if openai_radio.isChecked():
            provider = 'openai'
        elif claude_radio.isChecked():
            provider = 'claude'
        elif gemini_radio.isChecked():
            provider = 'gemini'
        elif ollama_radio.isChecked():
            provider = 'ollama'
        else:
            provider = 'openai'  # Default fallback
        
        # Get Ollama model from widget if available
        ollama_model = 'translategemma:12b'  # Default
        if ollama_status_widget and hasattr(ollama_status_widget, 'get_selected_model'):
            selected = ollama_status_widget.get_selected_model()
            if selected:
                ollama_model = selected
        
        new_settings = {
            'provider': provider,
            'openai_model': openai_combo.currentText().split()[0],
            'claude_model': claude_combo.currentText().split()[0],
            'gemini_model': gemini_combo.currentText().split()[0],
            'ollama_model': ollama_model
        }
        self.save_llm_settings(new_settings)

        # Update current provider and model attributes for AI Assistant
        self.current_provider = new_settings['provider']
        provider_key = f"{self.current_provider}_model"
        self.current_model = new_settings.get(provider_key)

        enabled_states = {
            'llm_openai': openai_enable_cb.isChecked(),
            'llm_claude': claude_enable_cb.isChecked(),
            'llm_gemini': gemini_enable_cb.isChecked(),
            'llm_ollama': ollama_enable_cb.isChecked()
        }
        # Merge with existing MT settings
        existing = self.load_provider_enabled_states()
        enabled_states.update({k: v for k, v in existing.items() if k.startswith('mt_')})
        self.save_provider_enabled_states(enabled_states)
        
        # Save translation preferences
        general_prefs = self.load_general_settings()
        general_prefs['batch_size'] = batch_size_spin.value()
        general_prefs['surrounding_segments'] = surrounding_spin.value()
        general_prefs['use_full_context'] = full_context_cb.isChecked()
        if getattr(self, '_usage_log_cb', None) is not None:
            general_prefs['persist_usage_log'] = self._usage_log_cb.isChecked()
        if getattr(self, '_budget_spin', None) is not None:
            general_prefs['monthly_budget_usd'] = float(self._budget_spin.value())
        try:
            from modules import usage_log as _ul
            _ul.set_enabled(general_prefs.get('persist_usage_log', True))
            self.monthly_budget_usd = float(general_prefs.get('monthly_budget_usd', 0.0) or 0.0)
        except Exception:
            pass
        general_prefs['context_window_size'] = context_slider.value()
        # Save TM check mode settings
        general_prefs['check_tm_before_api'] = not tm_no_check_rb.isChecked()
        general_prefs['check_tm_exact_only'] = tm_exact_rb.isChecked()
        # 'auto_propagate_100' is deprecated — auto-fill is now the single
        # 'auto_fill_100_matches' key owned by the General tab → TM settings.
        general_prefs['lookup_delay'] = delay_spin.value()
        self.save_general_settings(general_prefs)
        
        # Update LLM indicator in status bar
        self._update_llm_indicator()
        
        self.log(f"✓ LLM settings saved: Provider={new_settings['provider']}, Batch Size={batch_size_spin.value()}")
        QMessageBox.information(self, "Settings Saved", "LLM settings have been saved successfully.")

    def _save_ai_settings_from_ui(self, openai_radio, claude_radio, gemini_radio, ollama_radio,
                                   openai_combo, claude_combo, gemini_combo,
                                   ollama_status_widget,
                                   openai_enable_cb, claude_enable_cb, gemini_enable_cb, ollama_enable_cb,
                                   batch_size_spin, surrounding_spin, full_context_cb, context_slider,
                                   tm_no_check_rb, tm_fuzzy_rb, tm_exact_rb, auto_propagate_cb, delay_spin,
                                   ollama_keepwarm_cb,
                                   llm_matching_cb, llm_spin,
                                   quicklauncher_context_slider,
                                   custom_radio=None, custom_endpoint_input=None,
                                   custom_model_input=None, custom_enable_cb=None,
                                   custom_profile_combo=None, custom_key_input=None,
                                   mistral_radio=None, mistral_combo=None,
                                   mistral_enable_cb=None,
                                   deepseek_radio=None, deepseek_combo=None,
                                   openrouter_radio=None, openrouter_combo=None,
                                   openrouter_enable_cb=None,
                                   ollama_timeout_spin=None):
        """Save all AI settings from the unified AI Settings tab"""
        # Determine selected provider
        if openai_radio.isChecked():
            provider = 'openai'
        elif claude_radio.isChecked():
            provider = 'claude'
        elif gemini_radio.isChecked():
            provider = 'gemini'
        elif mistral_radio and mistral_radio.isChecked():
            provider = 'mistral'
        elif deepseek_radio and deepseek_radio.isChecked():
            provider = 'deepseek'
        elif openrouter_radio and openrouter_radio.isChecked():
            provider = 'openrouter'
        elif ollama_radio.isChecked():
            provider = 'ollama'
        elif custom_radio and custom_radio.isChecked():
            provider = 'custom_openai'
        else:
            provider = 'openai'  # Default fallback

        # Get Ollama model from widget if available
        ollama_model = 'translategemma:12b'  # Default
        if ollama_status_widget and hasattr(ollama_status_widget, 'get_selected_model'):
            selected = ollama_status_widget.get_selected_model()
            if selected:
                ollama_model = selected

        # Build profiles list from UI state
        existing_settings = self.load_llm_settings()
        profiles = existing_settings.get('custom_openai_profiles', [])
        active_profile_name = ''

        if custom_profile_combo and custom_profile_combo.count() > 0:
            # Sync current field values into the active profile
            idx = custom_profile_combo.currentIndex()
            if 0 <= idx < len(profiles):
                profiles[idx]['endpoint'] = custom_endpoint_input.text().strip() if custom_endpoint_input else ''
                profiles[idx]['model'] = custom_model_input.text().strip() if custom_model_input else ''
                profiles[idx]['api_key'] = custom_key_input.text().strip() if custom_key_input else ''
            active_profile_name = custom_profile_combo.currentText()

        # For legacy compat, also store flat fields from active profile
        active_endpoint = custom_endpoint_input.text().strip() if custom_endpoint_input else ''
        active_model = custom_model_input.text().strip() if custom_model_input else ''

        # Start from what is already saved: llm_settings also holds keys this
        # tab does not own (the QuickTrans custom MT profiles), and replacing
        # the whole dict used to delete them every time AI settings were saved.
        new_settings = dict(existing_settings)
        new_settings.update({
            'provider': provider,
            'openai_model': openai_combo.currentText().split()[0],
            'claude_model': claude_combo.currentText().split()[0],
            'gemini_model': gemini_combo.currentText().split()[0],
            'mistral_model': mistral_combo.currentText().split()[0] if mistral_combo else 'mistral-large-latest',
            'deepseek_model': deepseek_combo.currentText().split()[0] if deepseek_combo else 'deepseek-v4-pro',
            'openrouter_model': openrouter_combo.currentText().split()[0] if openrouter_combo else 'anthropic/claude-sonnet-5.5',
            'ollama_model': ollama_model,
            'custom_openai_model': active_model,
            'custom_openai_endpoint': active_endpoint,
            'custom_openai_profiles': profiles,
            'custom_openai_active_profile': active_profile_name
        })
        if ollama_timeout_spin is not None:
            new_settings['ollama_timeout_minutes'] = ollama_timeout_spin.value()
        self.save_llm_settings(new_settings)
        self._apply_ollama_timeout_setting(new_settings)
        # Each endpoint profile's key is also locked to this computer, under
        # workbench:custom_openai:<name> (step 1; settings.json still decides).
        from modules.llm_clients import save_profile_keys
        if not save_profile_keys(new_settings.get('custom_openai_profiles', [])):
            self.log("⚠ The endpoint profiles' keys could not be locked to this computer; the next start tries again.")

        # Update current provider and model attributes for AI Assistant
        self.current_provider = new_settings['provider']
        provider_key = f"{self.current_provider}_model"
        self.current_model = new_settings.get(provider_key)

        # Save LLM provider enabled states
        enabled_states = {
            'llm_openai': openai_enable_cb.isChecked(),
            'llm_claude': claude_enable_cb.isChecked(),
            'llm_gemini': gemini_enable_cb.isChecked(),
            'llm_mistral': mistral_enable_cb.isChecked() if mistral_enable_cb else True,
            'llm_openrouter': openrouter_enable_cb.isChecked() if openrouter_enable_cb else True,
            'llm_ollama': ollama_enable_cb.isChecked(),
            'llm_custom_openai': custom_enable_cb.isChecked() if custom_enable_cb else True
        }
        # Merge with existing MT settings
        existing = self.load_provider_enabled_states()
        enabled_states.update({k: v for k, v in existing.items() if k.startswith('mt_')})
        self.save_provider_enabled_states(enabled_states)
        
        # Update LLM matching setting
        self.enable_llm_matching = llm_matching_cb.isChecked()

        # v1.10.155: auto_generate_markdown is now owned by the General tab
        # (📥 Document Import group). Don't touch it from here so saving AI
        # settings doesn't clobber a value set on the other tab.

        # Save all general settings including AI-related ones
        general_prefs = self.load_general_settings()
        general_prefs['batch_size'] = batch_size_spin.value()
        general_prefs['surrounding_segments'] = surrounding_spin.value()
        general_prefs['use_full_context'] = full_context_cb.isChecked()
        if getattr(self, '_usage_log_cb', None) is not None:
            general_prefs['persist_usage_log'] = self._usage_log_cb.isChecked()
        if getattr(self, '_budget_spin', None) is not None:
            general_prefs['monthly_budget_usd'] = float(self._budget_spin.value())
        try:
            from modules import usage_log as _ul
            _ul.set_enabled(general_prefs.get('persist_usage_log', True))
            self.monthly_budget_usd = float(general_prefs.get('monthly_budget_usd', 0.0) or 0.0)
        except Exception:
            pass
        general_prefs['context_window_size'] = context_slider.value()
        general_prefs['quicklauncher_context_percent'] = quicklauncher_context_slider.value()
        # Save TM check mode settings
        general_prefs['check_tm_before_api'] = not tm_no_check_rb.isChecked()
        general_prefs['check_tm_exact_only'] = tm_exact_rb.isChecked()
        # 'auto_propagate_100' is deprecated — auto-fill is now the single
        # 'auto_fill_100_matches' key owned by the General tab → TM settings.
        general_prefs['lookup_delay'] = delay_spin.value()
        general_prefs['ollama_keepwarm'] = ollama_keepwarm_cb.isChecked()
        general_prefs['enable_llm_matching'] = llm_matching_cb.isChecked()
        # v1.10.155: auto_generate_markdown is now saved by the General tab,
        # not here — see _create_general_settings_tab → Document Import group.

        # Update LLM match limits
        if 'match_limits' not in general_prefs:
            general_prefs['match_limits'] = {}
        general_prefs['match_limits']['LLM'] = llm_spin.value()

        # Save FuzzyFixer thresholds (clamp so min <= max)
        if getattr(self, 'fuzzy_fixer_min_spin', None) is not None and getattr(self, 'fuzzy_fixer_max_spin', None) is not None:
            ff_min = self.fuzzy_fixer_min_spin.value()
            ff_max = self.fuzzy_fixer_max_spin.value()
            if ff_min > ff_max:
                ff_min, ff_max = ff_max, ff_min
            self.fuzzy_fixer_min_pct = ff_min
            self.fuzzy_fixer_max_pct = ff_max
            general_prefs['fuzzy_fixer_min_pct'] = ff_min
            general_prefs['fuzzy_fixer_max_pct'] = ff_max

        self.save_general_settings(general_prefs)
        
        # Handle Ollama keep-warm timer
        if ollama_keepwarm_cb.isChecked():
            self._start_ollama_keepwarm_timer()
        else:
            self._stop_ollama_keepwarm_timer()
        
        # Apply LLM match limits to results panels
        if hasattr(self, 'results_panels'):
            for panel in self.results_panels:
                if hasattr(panel, 'match_limits'):
                    panel.match_limits['LLM'] = llm_spin.value()
        
        # Update LLM indicator in status bar
        self._update_llm_indicator()
        
        # Save API keys from inline fields
        self._save_api_keys_from_ui()

        # Save proxy settings
        if hasattr(self, 'proxy_enabled_cb'):
            proxy_settings = {
                'enabled': self.proxy_enabled_cb.isChecked(),
                'host': self.proxy_host_input.text().strip(),
                'port': self.proxy_port_spin.value(),
                'username': self.proxy_user_input.text().strip(),
                'password': self.proxy_pass_input.text().strip(),
            }
            self.save_proxy_settings(proxy_settings)
            # Re-apply Gemini env vars immediately
            self._apply_gemini_proxy()

        self.log(f"✓ AI settings saved: Provider={provider}, Model={self.current_model}")
        QMessageBox.information(self, "Settings Saved", "AI settings have been saved successfully.")

    def _save_api_keys_from_ui(self):
        """Save API keys from inline input fields: a changed key into the keys
        every Supervertaler product shares (the plain file, then locked to this
        computer), and all of them into Workbench's own settings as before."""
        if not hasattr(self, '_api_key_inputs') or not self._api_key_inputs:
            return
        from modules.llm_clients import save_api_key, SHARED_KEY_IDS
        from modules.api_key_store import display_name
        api_keys = self._load_settings_section("api_keys")
        for key_name, input_widget in self._api_key_inputs.items():
            value = input_widget.text().strip()
            api_keys[key_name] = value
            # Only a box the user changed goes to the shared keys: the page saves
            # every box, and an untouched one must not put back an older key that
            # another product has replaced meanwhile.
            if value != (input_widget.property("shown_key") or ""):
                if save_api_key(key_name, value):
                    input_widget.setProperty("shown_key", value)
                else:
                    self.log(f"⚠ Your {display_name(SHARED_KEY_IDS.get(key_name, key_name))} key could not be "
                             f"saved to the keys Supervertaler's products share. Try again in a moment.")
        # Migrate legacy 'google' key to canonical 'gemini' key
        if api_keys.get('google') and not api_keys.get('gemini'):
            api_keys['gemini'] = api_keys['google']
        self.save_api_keys(api_keys)

    def _with_key_note(self, field, key_name, key_input, notes):
        """A key row, with M1 under it when that key can't be read on this
        computer; the note goes as soon as a new key is typed. Remembers what
        the box showed, so a save writes only the keys the user changed."""
        key_input.setProperty("shown_key", key_input.text().strip())
        note = notes.get(key_name)
        if not note:
            return field
        box = QWidget()
        box_layout = QVBoxLayout(box)
        box_layout.setContentsMargins(0, 0, 0, 0)
        box_layout.addWidget(field)
        label = QLabel(note)
        label.setWordWrap(True)
        label.setStyleSheet("color: #b35c00; font-size: 9pt;")
        box_layout.addWidget(label)
        key_input.textEdited.connect(lambda _text, lbl=label: lbl.hide())
        return box

    def _save_mt_settings_from_ui(self, google_cb, deepl_cb, microsoft_cb, amazon_cb, modernmt_cb, mymemory_cb):
        """Save MT settings from UI"""
        # Save API keys from inline fields
        self._save_api_keys_from_ui()

        enabled_states = {
            'mt_google_translate': google_cb.isChecked(),
            'mt_deepl': deepl_cb.isChecked(),
            'mt_microsoft': microsoft_cb.isChecked(),
            'mt_amazon': amazon_cb.isChecked(),
            'mt_modernmt': modernmt_cb.isChecked(),
            'mt_mymemory': mymemory_cb.isChecked()
        }
        # Merge with existing LLM settings
        existing = self.load_provider_enabled_states()
        enabled_states.update({k: v for k, v in existing.items() if k.startswith('llm_')})
        self.save_provider_enabled_states(enabled_states)
        
        self.log("✓ MT settings saved")
        QMessageBox.information(self, "Settings Saved", "MT settings have been saved successfully.")

    def _save_general_settings_from_ui(self, restore_cb, allow_replace_cb, auto_propagate_cb,
                                       llm_spin=None, mt_spin=None, tm_limit_spin=None, tb_spin=None,
                                       auto_open_log_cb=None, auto_insert_100_cb=None, tm_save_mode_combo=None, tb_highlight_cb=None,
                                       enable_backup_cb=None, backup_interval_spin=None,
                                       tb_hide_shorter_cb=None, smart_selection_cb=None,
                                       ahk_path_edit=None, auto_center_cb=None, auto_confirm_100_cb=None,
                                       auto_confirm_overwrite_cb=None, sound_effects_cb=None, sound_event_combos=None,
                                       disable_cache_cb=None, auto_fill_confirm_cb=None,
                                       auto_propagate_on_confirm_cb=None, auto_propagate_confirm_cb=None,
                                       auto_propagate_overwrite_cb=None):
        """Save general settings from UI (non-AI settings only)"""
        self.allow_replace_in_source = allow_replace_cb.isChecked()
        self.update_warning_banner()

        # Update consolidated auto-fill state (auto_propagate_cb is now the
        # single "Auto-fill empty segments with 100% TM matches" checkbox).
        self.auto_fill_100_matches = auto_propagate_cb.isChecked()
        # Keep the old attrs in sync for any lingering references.
        self.auto_propagate_exact_matches = self.auto_fill_100_matches
        self.auto_insert_100_percent_matches = self.auto_fill_100_matches

        # Update auto-fill confirm sub-option
        if auto_fill_confirm_cb is not None:
            self.auto_fill_confirm = auto_fill_confirm_cb.isChecked()

        # Update auto-propagate-on-confirm settings
        if auto_propagate_on_confirm_cb is not None:
            self.auto_propagate_on_confirm = auto_propagate_on_confirm_cb.isChecked()
        if auto_propagate_confirm_cb is not None:
            self.auto_propagate_confirm = auto_propagate_confirm_cb.isChecked()
        if auto_propagate_overwrite_cb is not None:
            self.auto_propagate_overwrite = auto_propagate_overwrite_cb.isChecked()

        # Update auto-confirm 100% matches setting
        if auto_confirm_100_cb is not None:
            self.auto_confirm_100_percent_matches = auto_confirm_100_cb.isChecked()
        
        # Update auto-confirm overwrite existing setting
        if auto_confirm_overwrite_cb is not None:
            self.auto_confirm_overwrite_existing = auto_confirm_overwrite_cb.isChecked()
        
        # Update auto-center active segment setting
        if auto_center_cb is not None:
            self.auto_center_active_segment = auto_center_cb.isChecked()
            self.log(f"💾 Saving auto-center setting: {self.auto_center_active_segment}")
        
        # Update termbase grid highlighting setting
        if tb_highlight_cb is not None:
            self.enable_termbase_grid_highlighting = tb_highlight_cb.isChecked()

        # Update termbase display settings
        if tb_hide_shorter_cb is not None:
            self.termbase_hide_shorter_matches = tb_hide_shorter_cb.isChecked()

        # Load existing settings to preserve AI-related ones
        existing_settings = self.load_general_settings()

        # Start from what is saved: the "general" section also holds settings
        # other pages own (AI batch/context options, QuickTrans, FuzzyFixer
        # range, usage log and budget, SuperLookup landing tab, import
        # options, ...). Replacing the section with only the keys below
        # deleted all of those on every Save General Settings.
        general_settings = dict(existing_settings)
        general_settings.update({
            'restore_last_project': restore_cb.isChecked(),
            'auto_open_log': auto_open_log_cb.isChecked() if auto_open_log_cb is not None else False,
            # Consolidated auto-fill setting (new canonical key).
            'auto_fill_100_matches': self.auto_fill_100_matches,
            'auto_fill_confirm': self.auto_fill_confirm,
            # Auto-propagate-on-confirm settings.
            'auto_propagate_on_confirm': self.auto_propagate_on_confirm,
            'auto_propagate_confirm': self.auto_propagate_confirm,
            'auto_propagate_overwrite': self.auto_propagate_overwrite,
            'auto_confirm_100_percent_matches': auto_confirm_100_cb.isChecked() if auto_confirm_100_cb is not None else False,
            'auto_confirm_overwrite_existing': auto_confirm_overwrite_cb.isChecked() if auto_confirm_overwrite_cb is not None else False,
            'tm_save_mode': tm_save_mode_combo.currentData() if tm_save_mode_combo is not None else 'latest',
            # v1.10.155: now owned by the General tab (📥 Document Import group).
            # Reads from the dedicated checkbox stored on self.
            'auto_generate_markdown': (self.auto_markdown_checkbox.isChecked()
                                       if hasattr(self, 'auto_markdown_checkbox')
                                          and self.auto_markdown_checkbox is not None
                                       else existing_settings.get('auto_generate_markdown', False)),
            'enable_termbase_grid_highlighting': tb_highlight_cb.isChecked() if tb_highlight_cb is not None else True,
            'enable_llm_matching': existing_settings.get('enable_llm_matching', False),  # Preserve AI setting
            'termbase_hide_shorter_matches': tb_hide_shorter_cb.isChecked() if tb_hide_shorter_cb is not None else False,
            'enable_smart_word_selection': smart_selection_cb.isChecked() if smart_selection_cb is not None else True,
            'precision_scroll_divisor': self.precision_spin.value() if hasattr(self, 'precision_spin') else 3,
            'auto_center_active_segment': self.auto_center_cb.isChecked() if hasattr(self, 'auto_center_cb') else False,
            'enable_auto_backup': enable_backup_cb.isChecked() if enable_backup_cb is not None else True,
            'backup_interval_minutes': backup_interval_spin.value() if backup_interval_spin is not None else 5,
            'enable_versioned_backup': self.enable_versioned_backup_cb.isChecked() if hasattr(self, 'enable_versioned_backup_cb') else existing_settings.get('enable_versioned_backup', True),
            'versioned_backup_every_n_saves': self.versioned_backup_n_spin.value() if hasattr(self, 'versioned_backup_n_spin') else existing_settings.get('versioned_backup_every_n_saves', 1),
            'versioned_backup_keep': self.versioned_backup_keep_spin.value() if hasattr(self, 'versioned_backup_keep_spin') else existing_settings.get('versioned_backup_keep', 100),
            'ollama_keepwarm': existing_settings.get('ollama_keepwarm', False),  # Preserve AI setting
            'grid_font_size': self.default_font_size,
            'results_match_font_size': 9,
            'results_compare_font_size': 9,
            'autohotkey_path': ahk_path_edit.text().strip() if ahk_path_edit is not None else existing_settings.get('autohotkey_path', ''),
            'enable_sound_effects': sound_effects_cb.isChecked() if sound_effects_cb is not None else existing_settings.get('enable_sound_effects', False),
            'disable_all_caches': disable_cache_cb.isChecked() if disable_cache_cb is not None else existing_settings.get('disable_all_caches', False),
            # v1.10.230: AutoCorrect-while-typing has its own Settings tab
            # with live-save (mutates settings.json on every toggle), so we
            # MUST NOT overwrite those keys here — preserve whatever the
            # disk currently has via existing_settings. Otherwise the
            # General "Save" button would silently revert an unrelated
            # AutoCorrect change the user made elsewhere.
            'autocorrect_enabled': existing_settings.get('autocorrect_enabled', True),
            'autocorrect_rule_overrides': existing_settings.get('autocorrect_rule_overrides', {}) or {},
        })

        # Keep a fast-access instance value
        self.enable_sound_effects = general_settings.get('enable_sound_effects', False)
        
        # Update cache kill switch
        if disable_cache_cb is not None:
            self.disable_all_caches = disable_cache_cb.isChecked()
            if self.disable_all_caches:
                self.log("🧪 EXPERIMENTAL: All caches DISABLED - direct lookups enabled")
                # Stop any running background workers that use the database
                if hasattr(self, 'termbase_batch_stop_event'):
                    self.termbase_batch_stop_event.set()
                if hasattr(self, 'prefetch_stop_event'):
                    self.prefetch_stop_event.set()
            else:
                self.log("✓ Caches enabled (normal mode)")

        # Persist per-event sound mapping
        existing_map = existing_settings.get('sound_effects_map', {}) if isinstance(existing_settings, dict) else {}
        new_map = dict(existing_map) if isinstance(existing_map, dict) else {}
        if isinstance(sound_event_combos, dict):
            for event_key, combo in sound_event_combos.items():
                try:
                    new_map[event_key] = combo.currentData()
                except Exception:
                    pass
        # Ensure key exists even if UI wasn't present
        if 'glossary_term_added' not in new_map:
            new_map['glossary_term_added'] = existing_map.get('glossary_term_added', 'asterisk') if isinstance(existing_map, dict) else 'asterisk'
        if 'glossary_created' not in new_map:
            new_map['glossary_created'] = existing_map.get('glossary_created', 'asterisk') if isinstance(existing_map, dict) else 'asterisk'
        if 'match_inserted' not in new_map:
            new_map['match_inserted'] = existing_map.get('match_inserted', 'ok') if isinstance(existing_map, dict) else 'ok'
        if 'glossary_term_duplicate' not in new_map:
            new_map['glossary_term_duplicate'] = existing_map.get('glossary_term_duplicate', 'exclamation') if isinstance(existing_map, dict) else 'exclamation'
        if 'glossary_term_error' not in new_map:
            new_map['glossary_term_error'] = existing_map.get('glossary_term_error', 'hand') if isinstance(existing_map, dict) else 'hand'

        general_settings['sound_effects_map'] = new_map
        self.sound_effects_map = new_map
        
        # Add match limits if provided (MT, TM, Termbase - LLM is handled in AI Settings)
        if all([mt_spin, tm_limit_spin, tb_spin]):
            existing_limits = existing_settings.get('match_limits', {})
            general_settings['match_limits'] = {
                'LLM': existing_limits.get('LLM', 3),  # Preserve LLM limit from AI settings
                'MT': mt_spin.value(),
                'TM': tm_limit_spin.value(),
                'Termbases': tb_spin.value()
            }
            # Apply to all results panels immediately
            if hasattr(self, 'results_panels'):
                for panel in self.results_panels:
                    panel.match_limits = general_settings['match_limits']
        
        # Update TM save mode
        if tm_save_mode_combo is not None:
            self.tm_save_mode = tm_save_mode_combo.currentData()
        
        self.save_general_settings(general_settings)

        # If a project is active, also save settings to project file (project overrides)
        if self.current_project:
            self.current_project.general_settings_overrides = {
                'auto_center_active_segment': self.auto_center_active_segment,
                'auto_fill_100_matches': self.auto_fill_100_matches,
                'auto_fill_confirm': self.auto_fill_confirm,
                'auto_propagate_on_confirm': self.auto_propagate_on_confirm,
                'auto_propagate_confirm': self.auto_propagate_confirm,
                'auto_propagate_overwrite': self.auto_propagate_overwrite,
                'auto_confirm_100_percent_matches': self.auto_confirm_100_percent_matches,
                'auto_confirm_overwrite_existing': self.auto_confirm_overwrite_existing,
                'enable_termbase_grid_highlighting': self.enable_termbase_grid_highlighting,
                'precision_scroll_divisor': self.precision_scroll_divisor,
                'termbase_hide_shorter_matches': self.termbase_hide_shorter_matches,
                'enable_smart_word_selection': self.enable_smart_word_selection,
                'enable_sound_effects': self.enable_sound_effects,
                'sound_effects_map': getattr(self, 'sound_effects_map', {}),
            }
            self.log("💾 Settings also saved to active project")
            self.project_modified = True  # Mark project as modified

        # Restart auto backup timer if settings changed
        if enable_backup_cb is not None or backup_interval_spin is not None:
            self.restart_auto_backup_timer()

        self.log("✓ General settings saved")
        QMessageBox.information(self, "Settings Saved", "General settings have been saved successfully.")

    def _save_view_settings_from_ui(self, grid_spin, match_spin, compare_spin, show_tags_check=None, tag_color_btn=None,
                                     alt_colors_check=None, even_color_btn=None, odd_color_btn=None, invisible_char_color_btn=None,
                                     grid_font_family_combo=None, termlens_font_family_combo=None, termlens_font_spin=None, termlens_bold_check=None,
                                     border_color_btn=None, border_thickness_spin=None, badge_text_color_btn=None, tabs_above_check=None,
                                     hide_wrapping_tags_check=None, status_before_target_check=None,
                                     mp_font_family_combo=None, mp_font_spin=None, mp_bold_check=None):
        """Save view settings from UI"""
        # CRITICAL: Suppress TM saves during view settings update
        # Grid operations (setStyleSheet, rehighlight, etc.) can trigger textChanged events
        # which would cause mass TM saves for all confirmed segments
        previous_suppression = getattr(self, '_suppress_target_change_handlers', False)
        self._suppress_target_change_handlers = True

        try:
            self._save_view_settings_from_ui_impl(
                grid_spin, match_spin, compare_spin, show_tags_check, tag_color_btn,
                alt_colors_check, even_color_btn, odd_color_btn, invisible_char_color_btn,
                grid_font_family_combo, termlens_font_family_combo, termlens_font_spin, termlens_bold_check,
                border_color_btn, border_thickness_spin, badge_text_color_btn, tabs_above_check,
                hide_wrapping_tags_check, status_before_target_check,
                mp_font_family_combo=mp_font_family_combo, mp_font_spin=mp_font_spin, mp_bold_check=mp_bold_check
            )
        finally:
            self._suppress_target_change_handlers = previous_suppression

    def _save_view_settings_from_ui_impl(self, grid_spin, match_spin, compare_spin, show_tags_check=None, tag_color_btn=None,
                                     alt_colors_check=None, even_color_btn=None, odd_color_btn=None, invisible_char_color_btn=None,
                                     grid_font_family_combo=None, termlens_font_family_combo=None, termlens_font_spin=None, termlens_bold_check=None,
                                     border_color_btn=None, border_thickness_spin=None, badge_text_color_btn=None, tabs_above_check=None,
                                     hide_wrapping_tags_check=None, status_before_target_check=None,
                                     mp_font_family_combo=None, mp_font_spin=None, mp_bold_check=None):
        """Implementation of save view settings (called with TM saves suppressed)"""
        # Load existing settings first to preserve all values, then update with new ones
        general_settings = self.load_general_settings()

        # Snapshot old values for change detection – only run expensive grid loops
        # when the relevant setting actually changed
        _old_tag_color = getattr(EditableGridTextEditor, 'tag_highlight_color', '#7f0001')
        _old_invisible_color = getattr(self, 'invisible_char_color', '#999999')
        _old_border_color = getattr(EditableGridTextEditor, 'focus_border_color', '')
        _old_border_thickness = getattr(EditableGridTextEditor, 'focus_border_thickness', 2)
        _old_alt_colors = getattr(self, 'enable_alternating_row_colors', True)
        _old_even_color = getattr(self, 'even_row_color', '#FFFFFF')
        _old_odd_color = getattr(self, 'odd_row_color', '#F0F0F0')
        _old_hide_tags = getattr(self, 'hide_outer_wrapping_tags', False)
        general_settings.update({
            'auto_propagate_exact_matches': self.auto_propagate_exact_matches,
            'grid_font_size': grid_spin.value(),
            'results_match_font_size': match_spin.value(),
            'results_compare_font_size': compare_spin.value(),
        })
        
        # Add tabs above grid setting if provided
        if tabs_above_check is not None:
            general_settings['tabs_above_grid'] = tabs_above_check.isChecked()
            self.tabs_above_grid = tabs_above_check.isChecked()
        
        # Add font family if provided
        if grid_font_family_combo is not None:
            general_settings['grid_font_family'] = grid_font_family_combo.currentText()
        
        # Add TermLens font settings if provided
        if termlens_font_family_combo is not None:
            general_settings['termlens_font_family'] = termlens_font_family_combo.currentText()
        if termlens_font_spin is not None:
            general_settings['termlens_font_size'] = termlens_font_spin.value()
        if termlens_bold_check is not None:
            general_settings['termlens_font_bold'] = termlens_bold_check.isChecked()

        # Add Match Panel font settings if provided
        if mp_font_family_combo is not None:
            general_settings['match_panel_font_family'] = mp_font_family_combo.currentText()
        if mp_font_spin is not None:
            general_settings['match_panel_font_size'] = mp_font_spin.value()
        if mp_bold_check is not None:
            general_settings['match_panel_font_bold'] = mp_bold_check.isChecked()

        # Add tag color if provided
        if tag_color_btn:
            tag_color = tag_color_btn.property('selected_color')
            if tag_color:
                general_settings['tag_highlight_color'] = tag_color
                EditableGridTextEditor.tag_highlight_color = tag_color

        # Add badge text color if provided
        if badge_text_color_btn:
            badge_text_color = badge_text_color_btn.property('selected_color')
            if badge_text_color:
                general_settings['badge_text_color'] = badge_text_color
                from modules.translation_results_panel import CompactMatchItem
                CompactMatchItem.badge_text_color = badge_text_color

        # Add invisible character color if provided
        if invisible_char_color_btn:
            invisible_char_color = invisible_char_color_btn.property('selected_color')
            if invisible_char_color:
                general_settings['invisible_char_color'] = invisible_char_color
                self.invisible_char_color = invisible_char_color

        # Add hide outer wrapping tags setting if provided
        if hide_wrapping_tags_check is not None:
            hide_tags_value = hide_wrapping_tags_check.isChecked()
            general_settings['hide_outer_wrapping_tags'] = hide_tags_value
            self.hide_outer_wrapping_tags = hide_tags_value

        # Add status column position setting if provided
        if status_before_target_check is not None:
            status_before = status_before_target_check.isChecked()
            general_settings['status_column_before_target'] = status_before
            self.status_column_before_target = status_before
            # Apply column reorder immediately
            if hasattr(self, 'table') and self.table is not None:
                header = self.table.horizontalHeader()
                if status_before:
                    # Move Status (logical 4) to visual position 3 (before Target)
                    header.moveSection(header.visualIndex(4), 3)
                else:
                    # Move Status (logical 4) back to visual position 4 (after Target)
                    header.moveSection(header.visualIndex(4), 4)

        # Add focus border settings if provided
        if border_color_btn is not None:
            border_color = border_color_btn.property('selected_color')
            # Fallback: if property is empty, try to parse from stylesheet
            if not border_color:
                style = border_color_btn.styleSheet()
                if 'background-color:' in style:
                    import re
                    match = re.search(r'background-color:\s*(#[0-9A-Fa-f]{6})', style)
                    if match:
                        border_color = match.group(1)
            if border_color:
                general_settings['focus_border_color'] = border_color
                EditableGridTextEditor.focus_border_color = border_color
        
        if border_thickness_spin is not None:
            thickness = border_thickness_spin.value()
            general_settings['focus_border_thickness'] = thickness
            EditableGridTextEditor.focus_border_thickness = thickness
        
        # Add alternating row color settings
        if alt_colors_check is not None:
            general_settings['enable_alternating_row_colors'] = alt_colors_check.isChecked()
            self.enable_alternating_row_colors = alt_colors_check.isChecked()
        
        if even_color_btn is not None:
            even_color = even_color_btn.property('selected_color')
            if even_color:
                general_settings['even_row_color'] = even_color
                self.even_row_color = even_color
        
        if odd_color_btn is not None:
            odd_color = odd_color_btn.property('selected_color')
            if odd_color:
                general_settings['odd_row_color'] = odd_color
                self.odd_row_color = odd_color
        
        # Save termbase highlight style settings
        if hasattr(self, '_tb_style_group') and self._tb_style_group is not None:
            checked_id = self._tb_style_group.checkedId()
            style_map = {0: 'background', 1: 'dotted', 2: 'semibold'}
            tb_style = style_map.get(checked_id, 'background')
            general_settings['termbase_highlight_style'] = tb_style
            self.termbase_highlight_style = tb_style
        
        if hasattr(self, '_tb_dotted_color_btn') and self._tb_dotted_color_btn is not None:
            dotted_color = self._tb_dotted_color_btn.property('selected_color')
            if dotted_color:
                general_settings['termbase_dotted_color'] = dotted_color
                self.termbase_dotted_color = dotted_color
        
        self.save_general_settings(general_settings)

        # Apply termlens font settings immediately to BOTH termlens widgets
        if hasattr(self, 'termlens_widget') and self.termlens_widget is not None:
            termlens_family = general_settings.get('termlens_font_family', 'Segoe UI')
            termlens_size = general_settings.get('termlens_font_size', 10)
            termlens_bold = general_settings.get('termlens_font_bold', False)
            self.termlens_widget.set_font_settings(termlens_family, termlens_size, termlens_bold)
        
        # Also apply to the Match Panel's TermLens widget
        if hasattr(self, 'termlens_widget_match') and self.termlens_widget_match is not None:
            termlens_family = general_settings.get('termlens_font_family', 'Segoe UI')
            termlens_size = general_settings.get('termlens_font_size', 10)
            termlens_bold = general_settings.get('termlens_font_bold', False)
            self.termlens_widget_match.set_font_settings(termlens_family, termlens_size, termlens_bold)

        # Apply Match Panel TM Source/Target font settings immediately
        if mp_font_spin is not None:
            new_mp_size = mp_font_spin.value()
            if 6 <= new_mp_size <= 18:
                type(self).match_panel_font_size = new_mp_size
        if mp_font_family_combo is not None:
            type(self).match_panel_font_family = mp_font_family_combo.currentText()
        if mp_bold_check is not None:
            type(self).match_panel_font_bold = mp_bold_check.isChecked()
        if mp_font_spin is not None or mp_font_family_combo is not None or mp_bold_check is not None:
            self._apply_match_panel_font_size()


        # Apply font family and size immediately
        font_changed = False
        if grid_font_family_combo is not None and self.default_font_family != grid_font_family_combo.currentText():
            self.default_font_family = grid_font_family_combo.currentText()
            font_changed = True
        if self.default_font_size != grid_spin.value():
            self.default_font_size = grid_spin.value()
            font_changed = True
        if font_changed and hasattr(self, 'table') and self.table is not None:
            self.apply_font_to_grid()
            self.auto_resize_rows()
        

        # Apply results pane font sizes to all panels
        if hasattr(self, 'results_panels'):
            from modules.translation_results_panel import CompactMatchItem
            if CompactMatchItem.font_size_pt != match_spin.value():
                CompactMatchItem.set_font_size(match_spin.value())
                for panel in self.results_panels:
                    if hasattr(panel, 'set_font_size'):
                        panel.set_font_size(match_spin.value())
        
            from modules.translation_results_panel import TranslationResultsPanel
            if TranslationResultsPanel.compare_box_font_size != compare_spin.value():
                TranslationResultsPanel.compare_box_font_size = compare_spin.value()
                for panel in self.results_panels:
                    if hasattr(panel, 'set_compare_box_font_size'):
                        panel.set_compare_box_font_size(compare_spin.value())
            
            # Apply show_tags setting
            if show_tags_check:
                show_tags = show_tags_check.isChecked()
                CompactMatchItem.show_tags = show_tags
                for panel in self.results_panels:
                    if hasattr(panel, 'set_show_tags'):
                        panel.set_show_tags(show_tags)
            
            # Apply tag color setting
            if tag_color_btn:
                tag_color = tag_color_btn.property('selected_color')
                if tag_color:
                    for panel in self.results_panels:
                        if hasattr(panel, 'set_tag_color'):
                            panel.set_tag_color(tag_color)

        # Apply badge text color to all results panels
        if badge_text_color_btn and hasattr(self, 'results_panels'):
            badge_text_color = badge_text_color_btn.property('selected_color')
            if badge_text_color:
                from modules.translation_results_panel import CompactMatchItem
                CompactMatchItem.badge_text_color = badge_text_color


        # --- Expensive grid loops: only run when the relevant setting changed ---

        _has_table = hasattr(self, 'table') and self.table is not None

        # Apply invisible char color to grid cells (only if color changed)
        if invisible_char_color_btn and _has_table:
            invisible_char_color = invisible_char_color_btn.property('selected_color')
            if invisible_char_color and invisible_char_color != _old_invisible_color:
                for row in range(self.table.rowCount()):
                    if row % 50 == 0:
                        QApplication.processEvents()
                    for col in [2, 3]:  # Source and target columns
                        widget = self.table.cellWidget(row, col)
                        if widget and hasattr(widget, 'highlighter'):
                            widget.highlighter.set_invisible_char_color(invisible_char_color)

        # Apply focus border settings to all grid cells (only if border settings changed)
        if (border_color_btn is not None or border_thickness_spin is not None) and _has_table:
            border_color = EditableGridTextEditor.focus_border_color
            border_thickness = EditableGridTextEditor.focus_border_thickness
            if border_color != _old_border_color or border_thickness != _old_border_thickness:
                self.log(f"Applying focus border: color={border_color}, thickness={border_thickness}px")
                for row in range(self.table.rowCount()):
                    if row % 50 == 0:
                        QApplication.processEvents()
                    widget = self.table.cellWidget(row, 3)  # Target column
                    if widget and isinstance(widget, EditableGridTextEditor):
                        widget.setStyleSheet(f"""
                            QTextEdit {{
                                border: none;
                                padding: 0px 4px 0px 0px;
                            }}
                            QTextEdit:focus {{
                                border: {border_thickness}px solid {border_color};
                            }}
                            QTextEdit::selection {{
                                background-color: #D0E7FF;
                                color: black;
                            }}
                        """)

        # Refresh tag colors (only if tag color changed)
        if _has_table:
            new_tag_color = general_settings.get('tag_highlight_color', '#7f0001')
            if new_tag_color != _old_tag_color:
                self.refresh_grid_tag_colors()

        # Refresh alternating row colors (only if row color settings changed)
        if _has_table:
            new_alt = general_settings.get('enable_alternating_row_colors', True)
            new_even = general_settings.get('even_row_color', '')
            new_odd = general_settings.get('odd_row_color', '')
            if new_alt != _old_alt_colors or new_even != _old_even_color or new_odd != _old_odd_color:
                self.apply_alternating_row_colors()

        # Refresh source column if hide_outer_wrapping_tags setting changed
        if hide_wrapping_tags_check is not None and _has_table:
            if hide_wrapping_tags_check.isChecked() != _old_hide_tags:
                self._refresh_source_column_display()


        self.log("✓ View settings saved and applied")
        # Use explicit QMessageBox instance to ensure proper dialog closing
        msg = QMessageBox(self)
        msg.setIcon(QMessageBox.Icon.Information)
        msg.setWindowTitle(self.tr("Settings Saved"))
        msg.setText(self.tr("View settings have been saved and applied successfully."))
        msg.setStandardButtons(QMessageBox.StandardButton.Ok)
        msg.exec()

    def _apply_global_ui_font_scale(self, scale_percent: int):
        """Apply font scale to the entire application UI"""
        general_settings = self.load_general_settings()
        general_settings['global_ui_font_scale'] = scale_percent
        # Remove old key if present (migration)
        if 'settings_ui_font_scale' in general_settings:
            del general_settings['settings_ui_font_scale']
        self.save_general_settings(general_settings)

        # Invalidate the modules.ui_scale cache so newly-constructed
        # widgets see the new value (already-open ones keep their
        # construction-time scale until reopened).
        try:
            from modules.ui_scale import refresh_ui_font_scale
            refresh_ui_font_scale()
        except Exception:
            pass

        # Update ThemeManager and reapply theme
        if hasattr(self, 'theme_manager') and self.theme_manager is not None:
            self.theme_manager.font_scale = scale_percent
            self.theme_manager.apply_theme(QApplication.instance())

        # Update status bar and main tabs fonts
        self._update_status_bar_fonts(scale_percent)
        self._update_main_tabs_fonts(scale_percent)
        self.log(f"✓ Global UI font scale set to {scale_percent}%")

    def _switch_settings_subtab(self, label_contains: str) -> bool:
        """Switch the Settings sub-tabs to the first tab whose label
        contains ``label_contains`` (case-insensitive substring match).
        Returns True on success, False if no matching tab was found.
        """
        if not hasattr(self, 'settings_tabs') or self.settings_tabs is None:
            return False
        needle = label_contains.lower()
        for i in range(self.settings_tabs.count()):
            if needle in self.settings_tabs.tabText(i).lower():
                self.settings_tabs.setCurrentIndex(i)
                return True
        return False

    def _create_file_types_settings_tab(self):
        """Settings → File Types: global defaults for Okapi import options."""
        from PyQt6.QtWidgets import QWidget, QVBoxLayout, QLabel, QPushButton, QHBoxLayout
        page = QWidget()
        v = QVBoxLayout(page)

        # Header row with a contextual "?" help button at the top-right.
        header_row = QHBoxLayout()
        header_row.addStretch()
        try:
            from modules.styled_widgets import HelpButton
            header_row.addWidget(HelpButton(
                "workbench/import-export/import-options/",
                tooltip="Open the Import Options (File Types) help page",
            ))
        except Exception as e:
            self.log(f"[File Types] Could not add help button: {e}")
        v.addLayout(header_row)

        intro = QLabel(
            "Choose which parts of Office documents become translatable "
            "segments when importing via Okapi. These are your defaults — "
            "you can still override them for a single import in the import "
            "dialog. Comments and headers/footers, when imported, are labelled "
            "in the grid's Type column (Cmt / Hdr / Ftr)."
        )
        intro.setWordWrap(True)
        v.addWidget(intro)

        current = self.get_effective_import_options()
        widget, checks = self._build_import_options_widget(current)
        v.addWidget(widget)
        self._settings_import_option_checks = checks

        def _save():
            try:
                gs = self._load_general_settings_from_file() or {}
            except Exception:
                gs = {}
            gs['import_options'] = self._read_import_option_widgets(checks)
            self.save_general_settings(gs)

        from PyQt6.QtWidgets import QComboBox
        for w in checks.values():
            if isinstance(w, QComboBox):
                w.currentIndexChanged.connect(lambda _i=0: _save())
            else:
                w.toggled.connect(lambda _checked=False: _save())

        # Restore-defaults button
        btn_row = QHBoxLayout()
        reset_btn = QPushButton(self.tr("Restore defaults"))

        def _restore_defaults():
            for key, w in checks.items():
                w.blockSignals(True)
                default = DEFAULT_IMPORT_OPTIONS[key]
                if isinstance(w, QComboBox):
                    w.setCurrentIndex(max(0, w.findData(str(default))))
                else:
                    w.setChecked(bool(default))
                w.blockSignals(False)
            _save()

        reset_btn.clicked.connect(_restore_defaults)
        btn_row.addWidget(reset_btn)
        btn_row.addStretch()
        v.addLayout(btn_row)
        v.addStretch()
        return page

    def load_general_settings(self) -> Dict[str, Any]:
        """Load general application settings"""
        # Initialize auto-propagation from loaded settings
        settings = self._load_general_settings_from_file()
        # Consolidated auto-fill setting (new key, with back-compat fallback to
        # the old auto_propagate_exact_matches / auto_insert_100_percent_matches).
        if 'auto_fill_100_matches' in settings:
            self.auto_fill_100_matches = settings['auto_fill_100_matches']
        elif 'auto_propagate_exact_matches' in settings:
            self.auto_fill_100_matches = settings['auto_propagate_exact_matches']
        elif 'auto_insert_100_percent_matches' in settings:
            self.auto_fill_100_matches = settings['auto_insert_100_percent_matches']
        # Keep the old attrs in sync for any lingering references.
        self.auto_propagate_exact_matches = self.auto_fill_100_matches
        self.auto_insert_100_percent_matches = self.auto_fill_100_matches
        if 'auto_fill_confirm' in settings:
            self.auto_fill_confirm = settings['auto_fill_confirm']
        # Auto-propagate-on-confirm settings.
        if 'auto_propagate_on_confirm' in settings:
            self.auto_propagate_on_confirm = settings['auto_propagate_on_confirm']
        if 'auto_propagate_confirm' in settings:
            self.auto_propagate_confirm = settings['auto_propagate_confirm']
        if 'auto_propagate_overwrite' in settings:
            self.auto_propagate_overwrite = settings['auto_propagate_overwrite']
        if 'auto_confirm_100_percent_matches' in settings:
            self.auto_confirm_100_percent_matches = settings['auto_confirm_100_percent_matches']
        if 'auto_confirm_overwrite_existing' in settings:
            self.auto_confirm_overwrite_existing = settings['auto_confirm_overwrite_existing']
        # Load termbase grid highlighting setting
        if 'enable_termbase_grid_highlighting' in settings:
            self.enable_termbase_grid_highlighting = settings['enable_termbase_grid_highlighting']
        # Load auto-markdown setting
        self.auto_generate_markdown = settings.get('auto_generate_markdown', False)
        # Load TM save mode
        self.tm_save_mode = settings.get('tm_save_mode', 'latest')
        # Load debug mode settings
        self.debug_mode_enabled = settings.get('debug_mode_enabled', False)
        self.debug_auto_export = settings.get('debug_auto_export', False)
        # Load LLM matching setting (default: FALSE - too slow!)
        self.enable_llm_matching = settings.get('enable_llm_matching', False)
        # Load FuzzyFixer thresholds (match % range the AI repair acts on)
        self.fuzzy_fixer_min_pct = int(settings.get('fuzzy_fixer_min_pct', 75))
        self.fuzzy_fixer_max_pct = int(settings.get('fuzzy_fixer_max_pct', 99))
        self.fuzzy_fixer_enabled = settings.get('fuzzy_fixer_enabled', False)
        # Load precision scroll divisor setting
        self.precision_scroll_divisor = settings.get('precision_scroll_divisor', 3)
        # Load auto-center active segment setting (default True, like memoQ/Trados)
        self.auto_center_active_segment = settings.get('auto_center_active_segment', True)
        # Load termbase display settings
        self.termbase_hide_shorter_matches = settings.get('termbase_hide_shorter_matches', False)
        # Load invisible character color
        self.invisible_char_color = settings.get('invisible_char_color', '#999999')
        # Load smart word selection setting
        self.enable_smart_word_selection = settings.get('enable_smart_word_selection', True)
        
        # Load alternating row color settings
        self.enable_alternating_row_colors = settings.get('enable_alternating_row_colors', True)
        self.even_row_color = settings.get('even_row_color', '#FFFFFF')
        self.odd_row_color = settings.get('odd_row_color', '#F0F0F0')

        # Load hide outer wrapping tags setting
        self.hide_outer_wrapping_tags = settings.get('hide_outer_wrapping_tags', False)

        # Load termbase highlight style settings
        self.termbase_highlight_style = settings.get('termbase_highlight_style', 'semibold')
        self.termbase_dotted_color = settings.get('termbase_dotted_color', '#808080')
        
        # Load tab position setting
        self.tabs_above_grid = settings.get('tabs_above_grid', False)
        
        # Load panel visibility settings
        self.show_translation_results_pane = settings.get('show_translation_results_pane', False)
        self.show_compare_panel = settings.get('show_compare_panel', True)

        # Load cache kill switch setting (default: False = caches ENABLED for performance)
        self.disable_all_caches = settings.get('disable_all_caches', False)

        # Load LLM provider settings for AI Assistant
        llm_settings = self.load_llm_settings()
        self.current_provider = llm_settings.get('provider', 'openai')
        provider_key = f"{self.current_provider}_model"
        self.current_model = llm_settings.get(provider_key)

        # v1.10.230: AutoCorrect-while-typing settings (issue #213).
        # Master toggle defaults on; per-rule overrides are sparse — missing
        # entries fall back to each rule's own default_on (em-dash off by
        # default, everything else on). Stored under top-level keys in the
        # general section so the save side can mirror them without
        # threading new kwargs through _save_general_settings_from_ui.
        self.autocorrect_enabled = bool(settings.get('autocorrect_enabled', True))
        overrides = settings.get('autocorrect_rule_overrides', {}) or {}
        # Coerce values to bools defensively (JSON may round-trip them oddly)
        self.autocorrect_rule_overrides = {
            str(k): bool(v) for k, v in overrides.items() if isinstance(k, str)
        }

        return settings

    def get_autocorrect_settings(self):
        """Return an :class:`AutoCorrectSettings` snapshot for engine calls.

        v1.10.230: keeps the engine stateless — every keystroke that
        invokes the engine passes a fresh settings snapshot so a Settings
        change takes effect immediately on the next typed character without
        needing to reload the engine or the grid.
        """
        from modules.autocorrect import AutoCorrectSettings
        return AutoCorrectSettings(
            enabled=bool(self.autocorrect_enabled),
            rule_overrides=dict(self.autocorrect_rule_overrides),
        )

    def _get_settings_dir(self) -> Path:
        """Get the settings subdirectory path"""
        return self.user_data_path / "workbench" / "settings"

    def _get_unified_settings_path(self) -> Path:
        """Get the unified settings.json file path"""
        return self._get_settings_dir() / "settings.json"

    def _load_unified_settings(self) -> Dict[str, Any]:
        """Load the entire unified settings file"""
        settings_file = self._get_unified_settings_path()
        if not settings_file.exists():
            return {"api_keys": {}, "general": {}, "ui": {}, "features": {}}
        try:
            with open(settings_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            # Ensure all sections exist
            for section in ("api_keys", "general", "ui", "features"):
                if section not in data:
                    data[section] = {}
            return data
        except Exception:
            return {"api_keys": {}, "general": {}, "ui": {}, "features": {}}

    def _save_unified_settings(self, data: Dict[str, Any]):
        """Save the entire unified settings file"""
        settings_dir = self._get_settings_dir()
        settings_dir.mkdir(parents=True, exist_ok=True)
        settings_file = settings_dir / "settings.json"
        try:
            with open(settings_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            self.log(f"⚠ Could not save settings: {str(e)}")

    def _load_settings_section(self, section: str) -> Dict[str, Any]:
        """Load a specific section from unified settings"""
        return self._load_unified_settings().get(section, {})

    def _save_settings_section(self, section: str, section_data: Dict[str, Any]):
        """Save a specific section to unified settings (preserves other sections)"""
        all_settings = self._load_unified_settings()
        all_settings[section] = section_data
        self._save_unified_settings(all_settings)

    def _migrate_to_workbench_layout(self):
        """One-time migration: move Workbench files into a workbench/ subfolder.

        Old layout:
            ~/Supervertaler/settings/         → workbench/settings/
            ~/Supervertaler/dictionaries/     → workbench/dictionaries/
            ~/Supervertaler/voice_scripts/    → workbench/voice_scripts/
            ~/Supervertaler/ai_assistant/     → workbench/ai_assistant/
            ~/Supervertaler/superbrowser_profiles/ → workbench/superbrowser_profiles/
            ~/Supervertaler/web_cache/        → workbench/web_cache/
            ~/Supervertaler/projects/         → workbench/projects/

        Shared resources (prompt_library/, resources/) stay at the root.
        """
        import shutil

        flag_file = self.user_data_path / "workbench" / ".migrated"
        if flag_file.exists():
            return

        old_settings = self.user_data_path / "settings" / "settings.json"
        if not old_settings.exists():
            # Nothing to migrate – fresh install or already migrated.
            # Just ensure the workbench dir exists and write the flag.
            try:
                (self.user_data_path / "workbench" / "settings").mkdir(parents=True, exist_ok=True)
                flag_file.write_text(datetime.utcnow().isoformat(), encoding='utf-8')
            except Exception:
                pass
            return

        print("[Layout] Migrating to workbench/ subfolder layout ...")
        wb = self.user_data_path / "workbench"

        try:
            # Move settings/ → workbench/settings/
            old_settings_dir = self.user_data_path / "settings"
            new_settings_dir = wb / "settings"
            if old_settings_dir.exists() and not new_settings_dir.exists():
                new_settings_dir.mkdir(parents=True, exist_ok=True)
                for f in old_settings_dir.iterdir():
                    if f.is_file():
                        dest = new_settings_dir / f.name
                        if not dest.exists():
                            shutil.move(str(f), str(dest))
                # Remove old dir if empty
                try:
                    old_settings_dir.rmdir()
                except OSError:
                    pass

            # Move top-level directories → workbench/
            for dirname in ['dictionaries', 'voice_scripts', 'ai_assistant',
                            'superbrowser_profiles', 'web_cache', 'projects']:
                old_dir = self.user_data_path / dirname
                new_dir = wb / dirname
                if old_dir.exists() and not new_dir.exists():
                    try:
                        shutil.move(str(old_dir), str(new_dir))
                    except Exception as e:
                        print(f"[Layout] Warning: could not move {dirname}: {e}")

            flag_file.write_text(datetime.utcnow().isoformat(), encoding='utf-8')
            print("[Layout] Migration to workbench/ layout complete.")
        except Exception as e:
            print(f"[Layout] Warning: migration incomplete: {e}")

    def _load_general_settings_from_file(self) -> Dict[str, Any]:
        """Load general settings from unified settings.json (general section)"""

        defaults = {
            'restore_last_project': False,
            'auto_propagate_exact_matches': True,
            'auto_center_active_segment': True,  # Default to True (like memoQ/Trados)
            'enable_sound_effects': False,
            'sound_effects_map': {
                'glossary_term_added': 'asterisk',
                'glossary_created': 'asterisk',
                'match_inserted': 'ok',
                'glossary_term_duplicate': 'exclamation',
                'glossary_term_error': 'hand'
            },
            'grid_font_size': 11,
            'results_match_font_size': 9,
            'results_compare_font_size': 9
        }

        settings = self._load_settings_section("general")
        if not settings:
            return defaults

        # Merge with defaults to ensure all keys exist
        result = defaults.copy()
        result.update(settings)

        # Migrate termview_* → termlens_* settings keys (v1.9.347+)
        _tv_migrations = {
            'termview_under_grid_visible': 'termlens_under_grid_visible',
            'termview_font_family': 'termlens_font_family',
            'termview_font_size': 'termlens_font_size',
            'termview_font_bold': 'termlens_font_bold',
        }
        for old_key, new_key in _tv_migrations.items():
            if old_key in result and new_key not in result:
                result[new_key] = result.pop(old_key)

        return result

    def save_general_settings(self, settings: Dict[str, Any]):
        """Save general settings to unified settings.json (general section)"""
        try:
            self._save_settings_section("general", settings)
        except Exception as e:
            self.log(f"⚠ Could not save general settings: {str(e)}")

    def load_dictation_settings(self) -> Dict[str, Any]:
        """Load the Voice settings (microphone, sensitivity, tab layout).

        They live under the historical ``dictation_settings`` key, which
        is kept so existing settings carry over; keys left behind by the
        removed dictation (model, language, vocabulary…) are ignored."""
        try:
            prefs = self._load_settings_section("ui")
            return dict(prefs.get('dictation_settings', {}) or {})
        except Exception:
            return {}

    def _load_language_pair_from_disk(self):
        """Read just the source/target language pair from settings.json.

        This must run BEFORE the Language Pair tab UI is built, otherwise
        the combo boxes are populated from the stale hard-coded defaults
        (English / Dutch). Called early in __init__ where spellcheck and
        log may not be set up yet, so we avoid touching them here.
        """
        defaults = ('English', 'Dutch')
        try:
            prefs = self._load_settings_section("ui")
            lang_settings = prefs.get('language_settings', {}) or {}
            src = lang_settings.get('source_language') or defaults[0]
            tgt = lang_settings.get('target_language') or defaults[1]
            self.source_language = src
            self.target_language = tgt
            print(f"[LangSettings] Loaded from settings.json: {src} → {tgt}")
        except Exception as e:
            print(f"[LangSettings] Load failed, keeping defaults: {e!r}")

    def load_language_settings(self):
        """Initialize spellcheck based on the already-loaded target language.

        The source/target pair itself is loaded earlier by
        _load_language_pair_from_disk() so the UI sees the right values.
        This method now handles only the spellcheck side-effects that
        depend on self.spellcheck_manager and self.log existing.
        """
        try:
            prefs = self._load_settings_section("ui")
            spellcheck_settings = prefs.get('spellcheck_settings', {}) or {}
            self.spellcheck_enabled = spellcheck_settings.get('enabled', False)
            TagHighlighter.set_spellcheck_enabled(self.spellcheck_enabled)

            if self.spellcheck_manager and self.spellcheck_enabled:
                if self.spellcheck_manager.set_language(self.target_language):
                    self.log(f"✓ Spellcheck initialized for {self.target_language}")
                else:
                    self.log(f"⚠ Spellcheck dictionary not available for {self.target_language}")
        except Exception as e:
            print(f"[LangSettings] Spellcheck init failed: {e!r}")

    def save_language_settings(self, source_lang: str, target_lang: str):
        """Save language settings to preferences"""
        try:
            all_settings = self._load_unified_settings()
            all_settings.setdefault("ui", {})['language_settings'] = {
                'source_language': source_lang,
                'target_language': target_lang
            }
            self._save_unified_settings(all_settings)
        except Exception as e:
            self.log(f"⚠ Could not save language settings: {str(e)}")

    def _save_language_settings_from_ui(self, source_combo, target_combo):
        """Save language settings from UI"""
        self.source_language = source_combo.currentText()
        self.target_language = target_combo.currentText()
        self.save_language_settings(self.source_language, self.target_language)
        
        self.log(f"✓ Language settings saved: {self.source_language} → {self.target_language}")
        QMessageBox.information(self, "Settings Saved", 
                              f"Language settings have been saved:\n\n"
                              f"Source: {self.source_language}\n"
                              f"Target: {self.target_language}")

    def load_font_sizes_from_preferences(self):
        """Load and apply font sizes from preferences on startup"""
        try:
            general_settings = self.load_general_settings()
            
            # Load grid font family
            font_family = general_settings.get('grid_font_family', 'Calibri')
            if font_family:
                self.default_font_family = font_family
            
            # Load grid font size
            grid_size = general_settings.get('grid_font_size', 11)
            if 7 <= grid_size <= 72:
                self.default_font_size = grid_size
            
            # Apply font to grid if it exists
            if hasattr(self, 'table') and self.table is not None:
                self.apply_font_to_grid()
            
            # Load results pane font sizes
            match_size = general_settings.get('results_match_font_size', 9)
            compare_size = general_settings.get('results_compare_font_size', 9)
            show_tags = general_settings.get('results_show_tags', False)
            
            # Apply to all results panels
            if hasattr(self, 'results_panels'):
                if 7 <= match_size <= 16:
                    from modules.translation_results_panel import CompactMatchItem
                    CompactMatchItem.set_font_size(match_size)
                    for panel in self.results_panels:
                        if hasattr(panel, 'set_font_size'):
                            panel.set_font_size(match_size)
                
                if 7 <= compare_size <= 14:
                    from modules.translation_results_panel import TranslationResultsPanel
                    TranslationResultsPanel.compare_box_font_size = compare_size
                    for panel in self.results_panels:
                        if hasattr(panel, 'set_compare_box_font_size'):
                            panel.set_compare_box_font_size(compare_size)
                
                # Apply show_tags setting
                from modules.translation_results_panel import CompactMatchItem
                CompactMatchItem.show_tags = show_tags
                for panel in self.results_panels:
                    if hasattr(panel, 'set_show_tags'):
                        panel.set_show_tags(show_tags)
                
                # Load and apply tag color
                tag_color = general_settings.get('tag_highlight_color', '#7f0001')
                EditableGridTextEditor.tag_highlight_color = tag_color
                ReadOnlyGridTextEditor.tag_highlight_color = tag_color
                CompactMatchItem.tag_highlight_color = tag_color
                for panel in self.results_panels:
                    if hasattr(panel, 'set_tag_color'):
                        panel.set_tag_color(tag_color)
                
                # Load and apply badge text color
                badge_text_color = general_settings.get('badge_text_color', '#333333')
                CompactMatchItem.badge_text_color = badge_text_color
                
                # Load and apply focus border settings
                focus_border_color = general_settings.get('focus_border_color', '#f1b79a')
                focus_border_thickness = general_settings.get('focus_border_thickness', 2)
                EditableGridTextEditor.focus_border_color = focus_border_color
                EditableGridTextEditor.focus_border_thickness = focus_border_thickness
                
            # Load and apply Match Panel font settings
            match_panel_size = general_settings.get('match_panel_font_size', 10)
            if 7 <= match_panel_size <= 18:
                type(self).match_panel_font_size = match_panel_size
            type(self).match_panel_font_family = general_settings.get('match_panel_font_family', '')
            type(self).match_panel_font_bold = general_settings.get('match_panel_font_bold', False)
            self._apply_match_panel_font_size()
                
            if hasattr(self, 'results_panels'):
                # Load and apply match limits
                match_limits = general_settings.get('match_limits', {
                    'LLM': 3,
                    'MT': 3,
                    'TM': 5,
                    'Termbases': 10
                })
                for panel in self.results_panels:
                    panel.match_limits = match_limits
                    
        except Exception as e:
            self.log(f"⚠ Could not load font sizes: {e}")

    def _save_spellcheck_settings(self):
        """Save spellcheck settings to preferences"""
        try:
            all_settings = self._load_unified_settings()
            all_settings.setdefault("ui", {})['spellcheck_settings'] = {
                'enabled': self.spellcheck_enabled
            }
            self._save_unified_settings(all_settings)
        except Exception as e:
            self.log(f"⚠ Could not save spellcheck settings: {e}")

    def _load_spellcheck_settings(self):
        """Load spellcheck settings from preferences"""
        try:
            prefs = self._load_settings_section("ui")
            settings = prefs.get('spellcheck_settings', {})
            return settings.get('enabled', False)
        except:
            return False

    def show_options_dialog(self):
        """Show application options dialog - DEPRECATED: Redirects to Settings tab for backwards compatibility"""
        # Redirect to Settings tab instead of showing dialog
        self._go_to_settings_tab()

    def _go_to_settings_tab(self, subtab_name: str = None):
        """Navigate to Settings tab (from menu), optionally to a specific sub-tab
        
        Args:
            subtab_name: Name of the sub-tab to navigate to (e.g., "AI Settings")
        """
        if hasattr(self, 'main_tabs'):
            # v1.10.161: label-based (was hard-coded index 4 with stale
            # comment claiming Settings=4; that became SuperLookup when
            # SuperLookup/Clipboard/Voice were inserted between AI and
            # Settings).
            self._switch_main_tab("Settings")

            # Navigate to specific sub-tab if requested
            if subtab_name and hasattr(self, 'settings_tabs'):
                for i in range(self.settings_tabs.count()):
                    if subtab_name.lower() in self.settings_tabs.tabText(i).lower():
                        self.settings_tabs.setCurrentIndex(i)
                        
                        # If navigating to AI Settings, scroll to bottom (API keys section)
                        if "ai settings" in subtab_name.lower() and hasattr(self, 'ai_settings_scroll'):
                            # Use QTimer to ensure the tab is fully rendered before scrolling
                            from PyQt6.QtCore import QTimer
                            QTimer.singleShot(100, lambda: self.ai_settings_scroll.verticalScrollBar().setValue(
                                self.ai_settings_scroll.verticalScrollBar().maximum()
                            ))
                        break

    def load_proxy_settings(self) -> Dict[str, Any]:
        """Load HTTP proxy settings from unified settings store."""
        defaults = {
            'enabled': False,
            'host': '',
            'port': 8080,
            'username': '',
            'password': '',
        }
        try:
            ui = self._load_settings_section("ui")
            saved = ui.get('proxy_settings', defaults)
            for k, v in defaults.items():
                saved.setdefault(k, v)
            return saved
        except Exception:
            return defaults

    def save_proxy_settings(self, proxy_settings: Dict[str, Any]):
        """Save HTTP proxy settings to unified settings store."""
        try:
            all_settings = self._load_unified_settings()
            all_settings.setdefault("ui", {})['proxy_settings'] = proxy_settings
            self._save_unified_settings(all_settings)
        except Exception as e:
            self.log(f"⚠ Could not save proxy settings: {str(e)}")
