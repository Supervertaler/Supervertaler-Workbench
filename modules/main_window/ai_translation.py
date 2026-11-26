"""The main window's AI and machine translation: translating one segment, a selection or a batch
with an LLM or an MT engine, the API keys and LLM settings they use, the MT and LLM matches
shown beside the grid, the prompt manager tab, QuickLauncher prompts and proofreading.

Moved out of Supervertaler.py unchanged (Nov 2026). SupervertalerQt inherits this class, so the
methods still run as its own.

Part of the main window (SupervertalerQt), moved here unchanged. The
methods run as the main window's own: `self` is the SupervertalerQt.
"""

import os
import re
import threading
import time
import traceback
from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QProgressDialog,
    QPushButton,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)
from datetime import datetime
from modules import inline_codes as _inline_codes
from modules.grid_editors import EditableGridTextEditor
from modules.help_system import Topics as HelpTopics, set_topic as set_help_topic
from modules.main_dialogs import LiveProgressDialog
from modules.segment_model import Segment
from modules.statuses import TRANSLATABLE_STATUSES, get_status
from modules.styled_widgets import CheckmarkCheckBox, CheckmarkRadioButton
from modules.tag_text import strip_outer_wrapping_tags
from modules.translation_workers import PreTranslationWorker, ProofreadWorker, TMSearchWorker
from typing import (
    Dict,
    List,
    Optional,
    Tuple,
)


class AITranslationMixin:
    def _on_bridge_prompt_request(self, expanded: str, display_prompt: str, prompt_name: str):
        """Handle a QuickLauncher prompt forwarded by the Trados plugin.

        Connected via Qt::QueuedConnection (cross-thread emit from the
        bridge's HTTP handler thread) so this always runs on the GUI
        thread. We:

          1. Bring Workbench forward (full hammer chain – Trados is
             the source of these requests and aggressive about
             foreground retention).
          2. Switch to the ✨ AI top tab and select its 💬 Chat sub-
             tab. There are two Chat surfaces in Workbench – one in
             the right panel next to the editor (translation
             workflow), and one as a full-width sub-tab inside the
             AI tab (general AI conversation). QuickLauncher prompts
             from Trados are explicitly the *general* kind, so they
             land on the latter where the user has more screen real
             estate to read the response.
          3. Echo the *display* version of the prompt as a "user"
             message (the Trados plugin builds a redacted display
             version – e.g. "[source document – N segments]" instead
             of the full project text – so the chat doesn't get
             spammed with kilobytes of context the LLM needs but the
             user has already seen).
          4. Send the fully-expanded prompt to the LLM directly via
             ``ChatBackend.send_ai_request``. We bypass the chat
             view's ``_context_aware_send`` override because the
             Trados side has already done all the context
             substitution; running it again would prepend Workbench's
             idea of the active context, which would be wrong for a
             Trados-originated prompt.

        Pre-v1.10.4 this lived on FloatingAssistant. v1.10.10 moved
        it to the main window targeting the right-panel Chat;
        v1.10.24 retargeted at the AI tab's Chat sub-tab per user
        request – the right panel is for translating, the AI tab's
        Chat is for general conversation.

        Both Chat surfaces share the same ChatBackend, so the user
        message + LLM response appear in *both* places automatically
        – we just need to make the AI tab's Chat the *visible* one
        when the prompt arrives.
        """
        try:
            self._bring_workbench_forward()
        except Exception:
            pass

        try:
            # Switch the main top tabs to ✨ AI. The index isn't
            # stored on self, so look it up by tab text – cheap, and
            # robust if future builds shift indices.
            if hasattr(self, 'main_tabs'):
                ai_tab_index = -1
                for i in range(self.main_tabs.count()):
                    if "AI" in self.main_tabs.tabText(i) and "✨" in self.main_tabs.tabText(i):
                        ai_tab_index = i
                        break
                if ai_tab_index >= 0:
                    self.main_tabs.setCurrentIndex(ai_tab_index)

            # Switch the AI tab's inner sub-tabs to 💬 Chat. The
            # ai_subtabs property is set up in create_main_layout
            # and points at prompt_manager_qt.sub_tabs.
            ai_subtabs = getattr(self, 'ai_subtabs', None)
            if ai_subtabs is not None:
                for i in range(ai_subtabs.count()):
                    if "Chat" in ai_subtabs.tabText(i):
                        ai_subtabs.setCurrentIndex(i)
                        break
        except Exception:
            pass

        backend = getattr(
            getattr(self, 'prompt_manager_qt', None), 'chat_backend', None
        )
        if backend is None:
            self.log("⚠ Trados bridge: chat backend unavailable")
            return

        label = f"[{prompt_name}] " if prompt_name else ""
        try:
            backend.add_message("user", label + (display_prompt or expanded))
            system_prompt = "You are an AI assistant. Follow the instructions precisely."
            response, metadata = backend.send_ai_request(expanded, system_prompt)
            if response and response.strip():
                backend.add_message("assistant", response, metadata=metadata)
            else:
                backend.add_message("system", "⚠ No response received.")
        except Exception as e:
            try:
                backend.add_message("system", f"⚠ Error: {e}")
            except Exception:
                print(f"[Trados bridge] Error handling prompt: {e}")

    def show_mt_quick_popup(self, text_override: str = None):
        """Show GT4T-style MT Quick Lookup popup with translations from all enabled MT engines.

        Triggered by Ctrl+Shift+Q or right-click menu. Shows machine translation
        suggestions from all configured and enabled MT providers in a popup window.

        Args:
            text_override: Optional text to translate. If None, uses selected text
                          in the current cell, or falls back to the full source text.

        Features:
        - Displays source text at top
        - Shows numbered list of MT suggestions from each provider
        - Press 1-9 to quickly insert a translation
        - Arrow keys to navigate, Enter to insert selected
        - Escape to dismiss
        """
        try:
            # Get current segment
            current_row = self.table.currentRow()
            if current_row < 0:
                self.log("⚠️ No segment selected")
                return

            # Determine what text to translate
            text_to_translate = text_override

            if not text_to_translate:
                # Check for selected text in the currently focused widget
                focus_widget = QApplication.focusWidget()
                if focus_widget and hasattr(focus_widget, 'textCursor'):
                    cursor = focus_widget.textCursor()
                    if cursor.hasSelection():
                        text_to_translate = cursor.selectedText().strip()

            if not text_to_translate:
                # Fall back to full source text – use clean segment.source to avoid
                # invisible markers (·, →, °, ↵) being sent to MT providers
                if current_row >= 0 and current_row < len(self.current_project.segments):
                    text_to_translate = self.current_project.segments[current_row].source.strip()
                else:
                    source_widget = self.table.cellWidget(current_row, 2)
                    if not source_widget or not hasattr(source_widget, 'toPlainText'):
                        self.log("⚠️ Could not get source text")
                        return
                    text_to_translate = source_widget.toPlainText().strip()

            # v1.9.306: Strip any invisible character markers from text before translation
            if hasattr(self, 'reverse_invisible_replacements'):
                text_to_translate = self.reverse_invisible_replacements(text_to_translate)
                text_to_translate = text_to_translate.strip()

            if not text_to_translate:
                self.log("⚠️ No text to translate")
                return

            # Import and create the popup
            from modules.quicktrans import MTQuickPopup

            # Resolve direction: sticky manual override (chosen in the popup's
            # language selector) → else per-text auto-detect → else project pair.
            source_lang, target_lang = self.resolve_quicktrans_direction(text_to_translate)

            # Create popup
            popup = MTQuickPopup(
                parent_app=self,
                source_text=text_to_translate,
                source_lang=source_lang,
                target_lang=target_lang,
                parent=self
            )

            # Connect signal to insert translation into target cell
            def insert_translation(translation: str):
                # Get the target widget
                target_widget = self.table.cellWidget(current_row, 3)
                if not target_widget or not hasattr(target_widget, 'toPlainText'):
                    return

                # Check if there was a selection in the target - replace just that
                focus_widget = QApplication.focusWidget()
                if focus_widget == target_widget and hasattr(focus_widget, 'textCursor'):
                    cursor = focus_widget.textCursor()
                    if cursor.hasSelection():
                        # Replace selection only
                        cursor.insertText(translation)
                        self.log(f"✅ Replaced selection with MT translation")
                    else:
                        # No selection in target, replace entire target
                        target_widget.setPlainText(translation)
                        self.log(f"✅ Inserted MT translation")
                else:
                    # Focus was elsewhere, replace entire target
                    target_widget.setPlainText(translation)
                    self.log(f"✅ Inserted MT translation")

                # Mark segment as modified
                if hasattr(self, 'segments') and current_row < len(self.segments):
                    new_target = target_widget.toPlainText()
                    # v1.9.306: Strip invisible markers before saving to segment data
                    if hasattr(self, 'reverse_invisible_replacements'):
                        new_target = self.reverse_invisible_replacements(new_target)
                    self.segments[current_row].target = new_target
                    self.mark_segment_modified(current_row)

            popup.translation_selected.connect(insert_translation)

            # Show the popup (it positions itself near cursor)
            popup.show()

        except ImportError as e:
            self.log(f"❌ MT Quick Popup module not found: {e}")
        except Exception as e:
            self.log(f"❌ Error showing MT Quick Popup: {e}")

    def _resolve_provider_model(self, settings, provider, default='gpt-6.1-sol'):
        """Resolve the model name for a provider.

        For ``custom_openai`` the active profile's model is the single source
        of truth (it drives the API call's base_url too). The flat
        ``custom_openai_model`` field is legacy and goes stale as soon as a
        user keeps several named profiles or switches the active one without
        re-saving AI Settings – which is exactly what produced the
        "status bar says X, QuickTrans says Y, and the model can't be
        changed" inconsistency. Always prefer the active profile's model so
        every surface (status bar, QuickTrans panel, the actual request)
        agrees.
        """
        model = settings.get(f'{provider}_model', default)
        if provider == 'custom_openai':
            profile = self._get_active_custom_profile(settings)
            if profile and (profile.get('model') or '').strip():
                model = profile.get('model').strip()
        return model

    def _estimate_batch_ai_cost(self, segments, provider, model, per_segment=False):
        """Estimated tokens and cost of AI-translating ``segments`` the way batch
        translation sends them: current prompt + glossary, configured batch
        size (one segment per call for FuzzyFixer). Issue #8."""
        from modules.cost_estimate import estimate_batch_translation
        segments = [s for s in segments if (s.source or '').strip()]
        system_chars = 0
        pm = getattr(self, 'prompt_manager_qt', None)
        proj = self.current_project
        if pm and segments and proj:
            try:
                prompt = pm.build_final_prompt(
                    source_text=segments[0].source, source_lang=proj.source_lang,
                    target_lang=proj.target_lang, mode="single")
                system_chars = len(prompt.split("**SOURCE TEXT:**")[0])
            except Exception:
                system_chars = 0
        try:
            batch_size = int(self.load_general_settings().get('batch_size', 20) or 20)
        except Exception:
            batch_size = 20
        return estimate_batch_translation(
            provider, model, [s.source for s in segments],
            batch_size=1 if per_segment else batch_size,
            system_prompt_chars=system_chars)

    def _update_llm_indicator(self):
        """Update the LLM provider/model indicator in the status bar"""
        try:
            settings = self.load_llm_settings()
            provider = settings.get('provider', 'openai')
            model = self._resolve_provider_model(settings, provider, 'gpt-6.1-sol')
            
            # Format nicely
            if provider == 'ollama':
                icon = "🖥️"
                display = f"{icon} Local: {model}"
            elif provider == 'openai':
                icon = "🤖"
                display = f"{icon} {model}"
            elif provider == 'claude':
                icon = "🟣"
                display = f"{icon} {model}"
            elif provider == 'gemini':
                icon = "💎"
                display = f"{icon} {model}"
            elif provider == 'mistral':
                icon = "🌀"
                display = f"{icon} {model}"
            elif provider == 'deepseek':
                icon = "🐋"
                display = f"{icon} {model}"
            elif provider == 'openrouter':
                icon = "🌐"
                display = f"{icon} {model}"
            elif provider == 'custom_openai':
                icon = "🔌"
                profile = self._get_active_custom_profile(settings)
                profile_name = profile.get('name', '') if profile else ''
                if profile_name:
                    display = f"{icon} {profile_name}: {model}"
                else:
                    display = f"{icon} Custom: {model}"
            else:
                display = f"{provider}: {model}"
            
            if hasattr(self, 'llm_indicator_label'):
                self.llm_indicator_label.setText(display)
                self.llm_indicator_label.setToolTip(f"LLM Provider: {provider.title()}\nModel: {model}")
        except Exception as e:
            if hasattr(self, 'llm_indicator_label'):
                self.llm_indicator_label.setText("")

    def _setup_ollama_keepwarm(self):
        """Setup timer to keep Ollama model warm (loaded in memory)"""
        from PyQt6.QtCore import QTimer
        
        # Check settings to see if keep-warm is enabled
        general_settings = self.load_general_settings()
        keepwarm_enabled = general_settings.get('ollama_keepwarm', False)
        
        if keepwarm_enabled:
            self._start_ollama_keepwarm_timer()

    def _apply_ollama_timeout_setting(self, llm_settings=None):
        """Push the user's Ollama request timeout (0 = automatic) to llm_clients."""
        try:
            from modules.llm_clients import set_ollama_timeout
            if llm_settings is None:
                llm_settings = self.load_llm_settings()
            minutes = int(llm_settings.get('ollama_timeout_minutes', 0) or 0)
            set_ollama_timeout(minutes * 60)
        except Exception as e:
            self.log(f"⚠ Could not apply Ollama timeout setting: {e}")

    def _start_ollama_keepwarm_timer(self):
        """Start the Ollama keep-warm timer (pings every 4 minutes)"""
        from PyQt6.QtCore import QTimer
        
        if self.ollama_keepwarm_timer is None:
            self.ollama_keepwarm_timer = QTimer(self)
            self.ollama_keepwarm_timer.timeout.connect(self._ping_ollama_keepwarm)
        
        # Ping every 4 minutes (Ollama unloads after 5 min of inactivity)
        self.ollama_keepwarm_timer.start(4 * 60 * 1000)  # 4 minutes in ms
        self.log("🔥 Ollama keep-warm enabled (pinging every 4 minutes)")

    def _stop_ollama_keepwarm_timer(self):
        """Stop the Ollama keep-warm timer"""
        if self.ollama_keepwarm_timer:
            self.ollama_keepwarm_timer.stop()
            self.log("❄️ Ollama keep-warm disabled")

    def _ping_ollama_keepwarm(self):
        """Send a minimal request to Ollama to keep the model loaded"""
        import threading
        
        def ping():
            try:
                import requests
                settings = self.load_llm_settings()
                if settings.get('provider') != 'ollama':
                    return
                
                model = settings.get('ollama_model', 'translategemma:12b')
                endpoint = os.environ.get('OLLAMA_ENDPOINT', 'http://localhost:11434').rstrip('/')
                for _sfx in ('/api', '/v1'):
                    if endpoint.endswith(_sfx):
                        endpoint = endpoint[:-len(_sfx)]

                # Send minimal request to keep model warm
                response = requests.post(
                    f"{endpoint}/api/generate",
                    json={
                        "model": model,
                        "prompt": "hi",
                        "options": {"num_predict": 1}  # Generate just 1 token
                    },
                    timeout=30
                )
                if response.status_code == 200:
                    print(f"🔥 Ollama keep-warm ping successful ({model})")
            except Exception as e:
                print(f"⚠️ Ollama keep-warm ping failed: {e}")
        
        # Run in background thread to not block UI
        threading.Thread(target=ping, daemon=True).start()

    def create_prompt_manager_tab(self) -> QWidget:
        """Create the AI tab (Prompt Manager, Supervertaler Sidekick, Variables)"""
        from modules.unified_prompt_manager_qt import UnifiedPromptManagerQt
        
        # Create Unified Prompt Manager widget (embedded mode, not standalone)
        prompt_widget = QWidget()
        self.prompt_manager_qt = UnifiedPromptManagerQt(self, standalone=False)
        self.prompt_manager_qt.create_tab(prompt_widget)
        set_help_topic(prompt_widget, HelpTopics.AI_PROMPT_MANAGER)

        return prompt_widget

    def _build_image_extract_ai_callback(self, docx_files):
        """Return a callback (signature ``(image_bytes, surrounding_text,
        media_path) -> Optional[str]``) suitable for passing as
        ``ai_label_fn=`` to the Image Extractor.

        Returns:
            - ``None`` if the user didn't tick "🤖 AI label", or if all
              pre-conditions silently failed (no vision model, no chat
              backend, etc.). In that case extraction proceeds with the
              free text-pattern detection only.
            - ``False`` if the user explicitly cancelled at the
              confirmation dialog. The caller aborts the whole
              extraction in that case.
            - A callable on success. The callable is called once per
              image that the text-pattern detector couldn't label.
        """
        # Gate 1: checkbox ticked?
        checkbox = getattr(self, 'image_extractor_ai_label', None)
        if checkbox is None or not checkbox.isChecked():
            return None

        # Gate 2: chat backend + LLM client available?
        prompt_mgr = getattr(self, 'prompt_manager_qt', None)
        chat_backend = getattr(prompt_mgr, 'chat_backend', None) if prompt_mgr else None
        llm_client = getattr(chat_backend, 'llm_client', None) if chat_backend else None
        if llm_client is None:
            QMessageBox.warning(
                self,
                "AI label unavailable",
                "AI label detection requires a configured AI provider, but "
                "no chat backend is available.\n\n"
                "Configure your API keys in Settings → API Keys and try again, "
                "or untick '🤖 AI label' to run the free text-pattern detector "
                "on its own."
            )
            return False

        provider = getattr(llm_client, 'provider', '')
        model = getattr(llm_client, 'model', '')

        # Gate 3: model supports vision?
        try:
            from modules.llm_clients import LLMClient
            if not LLMClient.model_supports_vision(provider, model):
                resp = QMessageBox.question(
                    self,
                    "Active model doesn't support vision",
                    f"The active AI model — <b>{provider} / {model}</b> — does "
                    "not accept image input, so the 🤖 AI label step can't run.\n\n"
                    "Switch to a vision-capable model (Claude Sonnet/Opus 4.x, "
                    "GPT-4o or newer, Gemini) in Settings and try again, or "
                    "click <b>OK</b> to proceed with text-pattern detection only.",
                    QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
                    QMessageBox.StandardButton.Ok,
                )
                if resp == QMessageBox.StandardButton.Cancel:
                    return False
                return None  # proceed text-only
        except Exception as e:
            self.log(f"[Image Extractor] AI vision-model check failed: {e}")
            return None

        # Gate 4: peek all DOCXes to count how many images would need AI.
        # Doing this BEFORE the extraction loop means we can show one
        # cost-estimate dialog covering the whole job, rather than
        # surprising the user mid-extract.
        try:
            all_peeks = self.image_extractor.peek_labels_multiple(docx_files)
        except Exception as e:
            self.log(f"[Image Extractor] Peek failed: {e}")
            return None

        unlabelled_count = 0
        total_count = 0
        for _path, peek in all_peeks:
            for _media, label, _surrounding in peek:
                total_count += 1
                if not label:
                    unlabelled_count += 1

        if unlabelled_count == 0:
            # Every image already has a text-detected label — no AI work
            # needed. Skip the dialog and silently return None so the
            # extraction proceeds text-only (which is what would happen
            # anyway, but this saves a no-op confirmation).
            return None

        # Cost estimate — same per-image rates as the AutoPrompt vision
        # feature uses (see _collect_autoprompt_images for the same
        # heuristic). These are order-of-magnitude figures, not
        # invoices.
        model_lc = (model or '').lower()
        provider_lc = (provider or '').lower()
        if 'opus' in model_lc:
            per_img = 0.0225
        elif 'sonnet' in model_lc or 'claude' in provider_lc:
            per_img = 0.0045
        elif 'gemini' in provider_lc:
            per_img = 0.001
        else:  # GPT-4o family / generic fallback
            per_img = 0.0030
        est_lo = max(0.01, per_img * unlabelled_count * 0.7)
        est_hi = per_img * unlabelled_count * 1.5
        cost_blurb = f"${est_lo:.2f}–${est_hi:.2f}"

        # Gate 5: confirmation dialog.
        confirm = QMessageBox.question(
            self,
            "Run AI label detection?",
            f"<p>Out of <b>{total_count}</b> images across the queued DOCX "
            f"files, <b>{unlabelled_count}</b> couldn't be labelled by the "
            f"text-pattern detector and would benefit from AI labelling.</p>"
            f"<p><b>Provider:</b> {provider} / {model}<br>"
            f"<b>Estimated extra cost:</b> {cost_blurb}<br>"
            f"<small>One vision API call per unlabelled image. The remaining "
            f"{total_count - unlabelled_count} image(s) already have text-"
            f"detected labels and won't trigger an API call.</small></p>"
            f"<p>Continue with AI labelling?</p>",
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Ok,
        )
        if confirm != QMessageBox.StandardButton.Ok:
            return False

        self.log(
            f"[Image Extractor] AI label fallback: will call {provider}/{model} "
            f"for {unlabelled_count} image(s) (est. extra ~{cost_blurb})"
        )

        # Build and return the actual callback. Closes over chat_backend
        # and provider so each call ships an image in the correct format
        # for the provider (PIL for Gemini, base64 PNG for the rest).
        def _ai_label_fn(image_bytes: bytes, surrounding_text: str,
                         media_path: str):
            try:
                from io import BytesIO
                from PIL import Image as _PILImage
                from modules.figure_context_manager import pil_image_to_base64_png

                pil_img = _PILImage.open(BytesIO(image_bytes))
                # Normalise to RGB for predictable encoding.
                if pil_img.mode not in ('RGB', 'RGBA'):
                    pil_img = pil_img.convert('RGB')

                if provider == 'gemini':
                    images_payload = [('figure', pil_img)]
                else:
                    images_payload = [
                        ('figure', pil_image_to_base64_png(pil_img))
                    ]

                snippet = (surrounding_text or '').strip()
                if len(snippet) > 1500:
                    snippet = snippet[:1500] + '…'

                prompt = (
                    "The following image appears in a document. Some text "
                    "from the paragraphs immediately around the image is "
                    "shown below for context.\n\n"
                    "--- surrounding text ---\n"
                    f"{snippet}\n"
                    "--- end surrounding text ---\n\n"
                    "Question: what label or caption does this figure carry "
                    "in the document?\n\n"
                    "Reply with ONLY the label text, nothing else. Examples "
                    "of valid replies:\n"
                    "  Figure 7\n"
                    "  FIG. 6a\n"
                    "  Table 3\n"
                    "  Plate IV\n"
                    "  Diagram 2\n"
                    "  Photograph 12\n"
                    "  Illustration 4\n\n"
                    "If you genuinely cannot see or infer a label, reply "
                    "with exactly: unlabelled"
                )
                system_prompt = (
                    "You are an expert at identifying figure labels and "
                    "captions in documents. Reply with just the label, no "
                    "preamble, no explanation, no quotation marks."
                )

                response_text, _meta = chat_backend.send_ai_request(
                    prompt, system_prompt, is_analysis=False,
                    images=images_payload,
                )

                if not response_text:
                    return None
                resp = response_text.strip().strip('"\'').strip()
                # Strip any single trailing period.
                if resp.endswith('.') and not resp.endswith('..'):
                    pass  # keep — common in "FIG. 6"
                # Reject obvious "I can't" responses.
                if not resp:
                    return None
                low = resp.lower()
                if low == 'unlabelled' or low.startswith('i cannot') or \
                   low.startswith("i can't") or low.startswith('no label'):
                    return None
                # Cap length.
                if len(resp) > 80:
                    resp = resp[:80].strip()
                # Reject multi-line / contains newlines.
                if '\n' in resp:
                    resp = resp.split('\n', 1)[0].strip()
                return resp or None
            except Exception as e:
                self.log(f"[Image Extractor] AI label call failed for {media_path}: {e}")
                return None

        return _ai_label_fn

    def _get_api_keys(self) -> dict:
        """Get API keys from settings"""
        from modules.llm_clients import load_api_keys
        return load_api_keys()

    def _insert_quicktrans_translation(self, translation: str):
        """Insert a QuickTrans-panel result into the current segment's target
        cell. Replaces the selection if there is one, otherwise the whole
        target. Mirrors the popup's insert behaviour."""
        try:
            row = self.table.currentRow()
            if row < 0:
                return
            target_widget = self.table.cellWidget(row, 3)
            if not target_widget or not hasattr(target_widget, 'toPlainText'):
                return
            focus_widget = QApplication.focusWidget()
            if (focus_widget is target_widget and hasattr(focus_widget, 'textCursor')
                    and focus_widget.textCursor().hasSelection()):
                cursor = focus_widget.textCursor()
                cursor.insertText(translation)
            else:
                target_widget.setPlainText(translation)
            if hasattr(self, 'segments') and row < len(self.segments):
                new_target = target_widget.toPlainText()
                if hasattr(self, 'reverse_invisible_replacements'):
                    new_target = self.reverse_invisible_replacements(new_target)
                self.segments[row].target = new_target
                self.mark_segment_modified(row)
            self.log("✅ Inserted QuickTrans translation")
        except Exception as e:
            self.log(f"Error inserting QuickTrans translation: {e}")

    def _preview_combined_prompt_from_grid(self):
        """Preview combined prompt with the currently selected segment from grid"""
        from PyQt6.QtWidgets import QDialog, QVBoxLayout, QTextEdit, QPushButton, QHBoxLayout, QMessageBox
        
        # Check if project is loaded
        if not self.current_project:
            QMessageBox.warning(
                self,
                "No Project",
                "Please load a project first to preview prompts."
            )
            return
        
        # Check if prompt_manager_qt exists
        if not hasattr(self, 'prompt_manager_qt'):
            QMessageBox.warning(
                self,
                "Feature Not Available",
                "AI features are not available."
            )
            return
        
        # Get current segment from grid
        current_segment = None
        current_segment_id = "Preview"
        
        if hasattr(self, 'table') and self.table:
            current_row = self.table.currentRow()
            if current_row >= 0:
                # Map display row to actual segment index (for pagination)
                actual_index = current_row
                if hasattr(self, 'grid_row_to_segment_index') and self.grid_row_to_segment_index:
                    if current_row in self.grid_row_to_segment_index:
                        actual_index = self.grid_row_to_segment_index[current_row]
                
                # Get segment
                if actual_index < len(self.current_project.segments):
                    current_segment = self.current_project.segments[actual_index]
                    current_segment_id = f"Segment {current_segment.id}"
        
        # Fallback to first segment if none selected
        if not current_segment:
            if len(self.current_project.segments) > 0:
                current_segment = self.current_project.segments[0]
                current_segment_id = f"Example: Segment {current_segment.id}"
                QMessageBox.information(
                    self,
                    "No Segment Selected",
                    "No segment is currently selected. Using the first segment as an example.\n\n"
                    "To preview with a specific segment, please select a row in the grid first."
                )
            else:
                QMessageBox.warning(
                    self,
                    "No Segments",
                    "This project has no segments to preview."
                )
                return
        
        # Get languages
        source_lang = getattr(self.current_project, 'source_lang', 'Source Language')
        target_lang = getattr(self.current_project, 'target_lang', 'Target Language')
        
        # Get source and target text
        source_text = current_segment.source
        target_text = current_segment.target or ""

        # Get glossary terms for AI injection
        glossary_terms = self.get_ai_inject_glossary_terms()

        # Build combined prompt
        combined = self.prompt_manager_qt.build_final_prompt(
            source_text, source_lang, target_lang, glossary_terms=glossary_terms,
            target_text=target_text
        )
        
        # Check for figure/image context
        figure_info = ""
        figure_images = []
        if hasattr(self, 'figure_context') and self.figure_context:
            if self.figure_context.has_images():
                # Detect figure references in current segment
                figure_refs = self.figure_context.detect_figure_references(source_text)
                if figure_refs:
                    figure_images = self.figure_context.get_images_for_text(source_text)
                    if figure_images:
                        image_names = [ref for ref, _ in figure_images]
                        figure_info = f"🖼️ {len(figure_images)} image(s) will be sent with this prompt: {', '.join(f'Figure {ref}' for ref in image_names)}"
                    else:
                        figure_info = f"⚠️ Figure references detected ({', '.join(figure_refs)}) but images not found in loaded context"
                else:
                    total_images = len(self.figure_context.figure_context_map)
                    figure_info = f"ℹ️ {total_images} image(s) loaded but no figure references detected in this segment"
        
        # Build composition info
        composition_parts = []
        composition_parts.append(f"📍 {current_segment_id}")
        composition_parts.append(f"🌐 {source_lang} → {target_lang}")
        composition_parts.append(f"📏 Total prompt: {len(combined):,} characters")
        
        if self.prompt_manager_qt.library.active_primary_prompt:
            composition_parts.append(f"✓ Custom prompt attached")
        
        if self.prompt_manager_qt.library.attached_prompts:
            composition_parts.append(f"✓ {len(self.prompt_manager_qt.library.attached_prompts)} additional prompt(s) attached")

        if glossary_terms:
            composition_parts.append(f"📚 {len(glossary_terms)} termbase term(s) injected")

        if figure_info:
            composition_parts.append(figure_info)
        
        composition_text = "\n".join(composition_parts)
        
        # Create dialog
        dialog = QDialog(self)
        dialog.setWindowTitle(self.tr("🧪 Combined Prompt Preview"))
        dialog.resize(900, 700)
        
        layout = QVBoxLayout(dialog)
        
        # Info label
        info_label = QLabel(
            "<b>Complete Assembled Prompt</b><br>"
            "This is what will be sent to the AI (System Prompt + Custom Prompts + segment text)<br><br>" +
            composition_text.replace("\n", "<br>")
        )
        info_label.setTextFormat(Qt.TextFormat.RichText)
        info_label.setWordWrap(True)
        info_label.setStyleSheet("padding: 10px; background-color: #e3f2fd; border-radius: 4px; margin-bottom: 10px;")
        layout.addWidget(info_label)
        
        # Additional image context notice if images are being sent
        if figure_images:
            image_notice = QLabel(
                f"📸 <b>Visual Context:</b> {len(figure_images)} image(s) will be sent alongside this text prompt as binary data<br>"
                f"<small>Images: {', '.join(f'Figure {ref}' for ref, _ in figure_images)}</small>"
            )
            image_notice.setTextFormat(Qt.TextFormat.RichText)
            image_notice.setWordWrap(True)
            image_notice.setStyleSheet("padding: 10px; border-radius: 4px; margin-bottom: 10px; border-left: 4px solid #ff9800;")
            layout.addWidget(image_notice)
        
        # Text editor for preview with syntax highlighting
        text_edit = QTextEdit()
        text_edit.setReadOnly(True)
        text_edit.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        text_edit.setStyleSheet("font-family: 'Consolas', 'Courier New', monospace; font-size: 9pt;")

        # Format the prompt with color highlighting
        import html
        import re

        # Escape HTML entities first
        formatted_html = html.escape(combined)

        # Replace newlines with <br> for HTML
        formatted_html = formatted_html.replace('\n', '<br>')

        # Make "# SYSTEM PROMPT" bold and red
        formatted_html = re.sub(
            r'(#\s*SYSTEM\s*PROMPT)',
            r'<span style="color: #d32f2f; font-weight: bold; font-size: 11pt;">\1</span>',
            formatted_html,
            flags=re.IGNORECASE
        )

        # Make "# CUSTOM PROMPT" bold and red
        formatted_html = re.sub(
            r'(#\s*CUSTOM\s*PROMPT)',
            r'<span style="color: #d32f2f; font-weight: bold; font-size: 11pt;">\1</span>',
            formatted_html,
            flags=re.IGNORECASE
        )

        # Make "# TERMBASE" bold and orange
        formatted_html = re.sub(
            r'(#\s*TERMBASE)',
            r'<span style="color: #FF9800; font-weight: bold; font-size: 11pt;">\1</span>',
            formatted_html,
            flags=re.IGNORECASE
        )

        # Make source text section blue (pattern: "XX text:<br>..." until double line break or # header)
        # Match language code + " text:" followed by content until "# " or end
        formatted_html = re.sub(
            r'(\w{2,5}\s+text:)(<br>)(.*?)(<br><br>(?:#|\*\*YOUR TRANSLATION)|$)',
            r'<span style="color: #1565c0; font-weight: bold;">\1</span>\2<span style="color: #1565c0;">\3</span>\4',
            formatted_html,
            flags=re.DOTALL
        )

        # Wrap in pre-like styling div
        formatted_html = f'<div style="font-family: Consolas, Courier New, monospace; white-space: pre-wrap;">{formatted_html}</div>'

        text_edit.setHtml(formatted_html)
        layout.addWidget(text_edit, 1)
        
        # Close button
        button_layout = QHBoxLayout()
        button_layout.addStretch()
        close_btn = QPushButton(self.tr("Close"))
        close_btn.clicked.connect(dialog.accept)
        close_btn.setStyleSheet("padding: 8px 20px; font-weight: bold;")
        button_layout.addWidget(close_btn)
        layout.addLayout(button_layout)
        
        dialog.exec()

    def show_proofread_dialog(self):
        """Show dialog to configure and start proofreading"""
        if not self.current_project:
            QMessageBox.information(self, "No Project", "Please open or create a project first.")
            return
        
        # Count segments for each option
        confirmed_count = sum(1 for seg in self.current_project.segments if seg.status == 'confirmed')
        translated_count = sum(1 for seg in self.current_project.segments if seg.status in ['draft', 'confirmed'])
        
        # Count selected rows (not cells)
        selected_count = 0
        if hasattr(self, 'table'):
            selected_rows = set(item.row() for item in self.table.selectedItems())
            selected_count = len(selected_rows)
        
        all_count = len(self.current_project.segments)
        
        dialog = QDialog(self)
        dialog.setWindowTitle(self.tr("✅ Proofread Translation"))
        dialog.setModal(True)
        dialog.setMinimumWidth(600)
        
        layout = QVBoxLayout(dialog)
        
        # Header
        header = QLabel(self.tr("<h3>✅ AI-Enhanced Translation Proofreading</h3>"))
        layout.addWidget(header)
        
        info_label = QLabel(
            "Use AI to verify translation accuracy, completeness, terminology, and style.\n"
            "Results are stored in the Proofreading comments tab, keyed by LLM model."
        )
        info_label.setWordWrap(True)
        info_label.setStyleSheet("color: #666; padding: 10px 0;")
        layout.addWidget(info_label)
        
        layout.addSpacing(10)
        
        # Segment selection
        segment_group = QGroupBox(self.tr("Segments to Proofread"))
        segment_layout = QVBoxLayout()
        
        confirmed_radio = CheckmarkRadioButton(f"✅ Confirmed only ({confirmed_count} segments)")
        confirmed_radio.setChecked(True)
        segment_layout.addWidget(confirmed_radio)
        
        translated_radio = CheckmarkRadioButton(f"📝 Translated + Confirmed ({translated_count} segments)")
        segment_layout.addWidget(translated_radio)
        
        selected_radio = CheckmarkRadioButton(f"🔹 Selected ({selected_count} segments)")
        selected_radio.setEnabled(selected_count > 0)
        if selected_count == 0:
            selected_radio.setToolTip(self.tr("Select one or more rows in the grid to enable this option"))
        segment_layout.addWidget(selected_radio)
        
        all_radio = CheckmarkRadioButton(f"🌐 All segments ({all_count} segments)")
        segment_layout.addWidget(all_radio)
        
        segment_group.setLayout(segment_layout)
        layout.addWidget(segment_group)
        
        layout.addSpacing(10)
        
        # LLM provider info
        settings = self.load_llm_settings()
        llm_provider = settings.get('provider', 'openai')
        model_key = f'{llm_provider}_model'
        llm_model = settings.get(model_key, 'gpt-6.1-sol')
        
        llm_info = QLabel(f"📊 Using: {llm_provider.title()} ({llm_model})")
        llm_info.setStyleSheet("color: #666; font-size: 9pt; padding: 5px 0;")
        layout.addWidget(llm_info)
        
        layout.addSpacing(10)
        
        # Custom prompt override
        prompt_group = QGroupBox(self.tr("Proofreading Prompt"))
        prompt_layout = QVBoxLayout()

        # Load prompts from Bulk Operations/ folder in Prompt Library
        library_prompts = []
        default_prompt_name = "Default Proofreading Prompt"
        if hasattr(self, 'prompt_manager_qt') and self.prompt_manager_qt:
            lib = getattr(self.prompt_manager_qt, 'library', None)
            if lib:
                # Reload from disk to pick up any recently created prompts
                lib.load_all_prompts()
                for rel_path, data in lib.prompts.items():
                    folder = data.get('_folder', '')
                    # Only include prompts under Bulk Operations/
                    if not folder.startswith('Bulk Operations'):
                        continue
                    # Skip the default built-in prompt (it's the "Default" entry)
                    if data.get('name', '') == default_prompt_name:
                        continue
                    label = data.get('name', rel_path)
                    library_prompts.append((rel_path, label, data.get('content', '')))
                library_prompts.sort(key=lambda x: x[1].lower())

        prompt_source_layout = QHBoxLayout()
        prompt_source_label = QLabel(self.tr("Prompt:"))
        prompt_source_layout.addWidget(prompt_source_label)

        prompt_combo = QComboBox()
        prompt_combo.addItem("Default (built-in)", "")
        for rel_path, label, content in library_prompts:
            prompt_combo.addItem(label, rel_path)
        prompt_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        prompt_source_layout.addWidget(prompt_combo)
        prompt_layout.addLayout(prompt_source_layout)

        prompt_text = QTextEdit()
        prompt_text.setPlaceholderText(
            "Default checks:\n"
            "1. Accuracy – Does target correctly convey source meaning?\n"
            "2. Completeness – Is anything missing or added?\n"
            "3. Terminology – Are technical terms correct and consistent?\n"
            "4. Grammar & Style – Is the text natural and error-free?\n\n"
            "Select a prompt from the library above, or type a custom prompt here."
        )
        prompt_text.setMaximumHeight(150)
        prompt_layout.addWidget(prompt_text)

        # When combo changes, load prompt content into text area
        def on_prompt_source_changed(index):
            rel_path = prompt_combo.itemData(index)
            if rel_path:
                # Load from library
                for rp, label, content in library_prompts:
                    if rp == rel_path:
                        prompt_text.setPlainText(content)
                        break
            else:
                prompt_text.clear()
        prompt_combo.currentIndexChanged.connect(on_prompt_source_changed)

        prompt_group.setLayout(prompt_layout)
        layout.addWidget(prompt_group)
        
        layout.addSpacing(20)
        
        # Buttons
        button_layout = QHBoxLayout()
        button_layout.addStretch()
        
        cancel_btn = QPushButton(self.tr("Cancel"))
        cancel_btn.clicked.connect(dialog.reject)
        button_layout.addWidget(cancel_btn)
        
        start_btn = QPushButton(self.tr("Start Proofreading"))
        start_btn.setDefault(True)
        start_btn.setStyleSheet("background-color: #4CAF50; color: white; font-weight: bold;")
        start_btn.clicked.connect(dialog.accept)
        button_layout.addWidget(start_btn)
        
        layout.addLayout(button_layout)
        
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        
        # Determine which segments to proofread
        segments_to_check = []
        if confirmed_radio.isChecked():
            segments_to_check = [(i, seg) for i, seg in enumerate(self.current_project.segments) if seg.status == 'confirmed']
        elif translated_radio.isChecked():
            segments_to_check = [(i, seg) for i, seg in enumerate(self.current_project.segments) if seg.status in ['draft', 'confirmed']]
        elif selected_radio.isChecked():
            selected_rows = set(item.row() for item in self.table.selectedItems())
            segments_to_check = [(i, seg) for i, seg in enumerate(self.current_project.segments) if i in selected_rows]
        else:  # all_radio
            segments_to_check = list(enumerate(self.current_project.segments))
        
        if not segments_to_check:
            QMessageBox.information(self, "No Segments", "No segments match the selected criteria.")
            return
        
        custom_prompt = prompt_text.toPlainText().strip()
        
        # Start proofreading
        self._run_proofreading(segments_to_check, llm_provider, llm_model, custom_prompt, settings)

    def _run_proofreading(self, segments_to_check, provider, model, custom_prompt="", settings=None):
        """Run proofreading on selected segments using a background thread"""
        # Create progress dialog
        progress_dialog = QDialog(self)
        progress_dialog.setWindowTitle(self.tr("Proofreading in Progress"))
        progress_dialog.setModal(True)
        progress_dialog.setMinimumWidth(500)

        dialog_layout = QVBoxLayout(progress_dialog)
        dialog_layout.setContentsMargins(12, 12, 12, 12)
        dialog_layout.setSpacing(8)

        current_label = QLabel(self.tr("Initializing..."))
        dialog_layout.addWidget(current_label)

        progress_bar = QProgressBar()
        progress_bar.setMaximum(len(segments_to_check))
        progress_bar.setValue(0)
        dialog_layout.addWidget(progress_bar)

        stats_label = QLabel(self.tr("Checked: 0 | Issues Found: 0 | OK: 0"))
        dialog_layout.addWidget(stats_label)

        button_layout = QHBoxLayout()
        button_layout.addStretch()

        cancel_btn = QPushButton(self.tr("Cancel"))
        cancel_btn.setStyleSheet("background-color: #e74c3c; color: white; font-weight: bold; padding: 6px 20px;")
        button_layout.addWidget(cancel_btn)

        close_btn = QPushButton(self.tr("Close"))
        close_btn.setEnabled(False)
        close_btn.clicked.connect(progress_dialog.accept)
        button_layout.addWidget(close_btn)

        dialog_layout.addLayout(button_layout)

        # Resolve custom_openai settings before spawning thread
        api_keys = self.load_api_keys()
        base_url = None
        custom_api_key = None
        if provider == 'custom_openai' and settings:
            profile = self._get_active_custom_profile(settings)
            if profile:
                profile_key = (profile.get('api_key') or '').strip()
                if profile_key:
                    custom_api_key = profile_key
                base_url = profile.get('endpoint') or None

        # Create worker thread
        worker = ProofreadWorker(
            segments_to_check=segments_to_check,
            provider=provider,
            model=model,
            custom_prompt=custom_prompt,
            api_keys=api_keys,
            source_lang=self.current_project.source_lang,
            target_lang=self.current_project.target_lang,
            base_url=base_url,
            custom_api_key=custom_api_key,
            http_proxy=self._get_proxy_url() if provider != 'gemini' else None,
        )

        # Keep reference to prevent GC
        self._proofread_worker = worker

        # Connect cancel button
        def on_cancel():
            worker.cancel()
            cancel_btn.setEnabled(False)
            cancel_btn.setText(self.tr("Cancelling..."))
            current_label.setText(self.tr("Cancelling after current batch..."))

        cancel_btn.clicked.connect(on_cancel)

        # Also cancel if dialog is closed via X button
        def on_dialog_rejected():
            worker.cancel()
        progress_dialog.rejected.connect(on_dialog_rejected)

        # Connect worker signals
        def on_progress(checked_count, message):
            progress_bar.setValue(checked_count)
            current_label.setText(message)
            self.log(f"📤 {message}")

        def on_stats(checked, issues, ok):
            progress_bar.setValue(checked)
            stats_label.setText(f"Checked: {checked} | Issues Found: {issues} | OK: {ok}")

        def on_segment_issue(row_idx, issue_text, model_name):
            segment = self.current_project.segments[row_idx]
            if not isinstance(getattr(segment, 'proofreading_notes', None), dict):
                segment.proofreading_notes = {}
            # Overwrite per-LLM: same model replaces its previous note, different models accumulate
            segment.proofreading_notes[model_name] = issue_text
            self.project_modified = True

        def on_batch_error(batch_start, batch_end, error_msg):
            if batch_start == 0 and batch_end == 0:
                # LLM init error
                current_label.setText(f"Error: {error_msg}")
            else:
                self.log(f"⚠️ Proofreading error for batch {batch_start}-{batch_end}: {error_msg}")

        def on_finished(checked, issues, ok):
            cancel_btn.setEnabled(False)
            cancel_btn.hide()
            close_btn.setEnabled(True)

            if worker._cancelled:
                current_label.setText(f"Proofreading cancelled. Checked: {checked} segments before cancellation.")
            else:
                current_label.setText(self.tr("✓ Proofreading Complete!"))

            self.load_segments_to_grid()
            self.update_window_title()
            # Refresh the Proofreading-comments list with the new AI results (#234)
            self._refresh_proofreading_comments_list()

            self.log(f"═══════════════════════════════════════════════════════════")
            self.log(f"{'⊘ Proofreading Cancelled' if worker._cancelled else '✓ Proofreading Complete'}")
            self.log(f"   Checked: {checked} | Issues: {issues} | OK: {ok}")
            self.log(f"═══════════════════════════════════════════════════════════")

            self._proofread_worker = None

        worker.progress_update.connect(on_progress)
        worker.stats_update.connect(on_stats)
        worker.segment_issue.connect(on_segment_issue)
        worker.batch_error.connect(on_batch_error)
        worker.finished_proofreading.connect(on_finished)

        # Start worker and show dialog
        worker.start()
        progress_dialog.exec()

    def show_proofreading_results_dialog(self):
        """Show dialog with all proofreading results (from proofreading_notes dict).

        NOTE (v1.10.327, issue #234): no longer wired to any menu — the
        Proofreading comments tab supersedes it (real navigation + per-entry
        delete). Retained (not deleted) as a starting point for a future
        "export proofreading report" feature. The row double-click navigation
        below is stale (uses jump_to_segment with a raw index).
        """
        if not self.current_project:
            QMessageBox.information(self, "No Project", "Please open or create a project first.")
            return

        # Find segments with proofreading notes
        proofread_segments = []
        for i, segment in enumerate(self.current_project.segments):
            if getattr(segment, 'proofreading_notes', None):
                proofread_segments.append((i, segment))

        if not proofread_segments:
            QMessageBox.information(
                self,
                "No Proofreading Results",
                "No segments have proofreading comments.\n\n"
                "Run Edit → Bulk Operations → ✅ Proofread Translation to analyze your translation."
            )
            return

        dialog = QDialog(self)
        dialog.setWindowTitle(self.tr("✅ Proofreading Results"))
        dialog.setModal(False)
        dialog.setMinimumWidth(900)
        dialog.setMinimumHeight(600)

        layout = QVBoxLayout(dialog)

        # Header
        header = QLabel(f"<h3>⚠️ {len(proofread_segments)} Segment{'s' if len(proofread_segments) != 1 else ''} with Proofreading Comments</h3>")
        layout.addWidget(header)

        info_label = QLabel(self.tr("Double-click any row to navigate to that segment in the grid."))
        info_label.setStyleSheet("color: #666; padding: 5px 0;")
        layout.addWidget(info_label)

        # Results table
        table = QTableWidget()
        table.setColumnCount(5)
        table.setHorizontalHeaderLabels(["Seg #", "Source", "Target", "LLM", "Issue"])
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.horizontalHeader().setStretchLastSection(True)
        table.setColumnWidth(0, 60)
        table.setColumnWidth(1, 180)
        table.setColumnWidth(2, 180)
        table.setColumnWidth(3, 120)

        for row_idx, segment in proofread_segments:
            proofread = getattr(segment, 'proofreading_notes', {})
            for model_name, issue_text in proofread.items():
                table_row = table.rowCount()
                table.insertRow(table_row)

                # Segment number
                table.setItem(table_row, 0, QTableWidgetItem(str(row_idx + 1)))

                # Source (truncated)
                source_text = segment.source[:80] + "..." if len(segment.source) > 80 else segment.source
                table.setItem(table_row, 1, QTableWidgetItem(source_text))

                # Target (truncated)
                target_text = segment.target[:80] + "..." if len(segment.target) > 80 else segment.target
                table.setItem(table_row, 2, QTableWidgetItem(target_text))

                # LLM model name
                table.setItem(table_row, 3, QTableWidgetItem(model_name))

                # Issue text (truncated)
                display_issue = issue_text[:100] + "..." if len(issue_text) > 100 else issue_text
                table.setItem(table_row, 4, QTableWidgetItem(display_issue))

                # Store segment index in row data
                table.item(table_row, 0).setData(Qt.ItemDataRole.UserRole, row_idx)

        # Double-click to navigate
        def on_row_double_clicked(row):
            segment_idx = table.item(row, 0).data(Qt.ItemDataRole.UserRole)
            self.jump_to_segment(segment_idx)
            dialog.lower()  # Send dialog to back so grid is visible

        table.cellDoubleClicked.connect(lambda row, col: on_row_double_clicked(row))

        layout.addWidget(table)

        # Buttons
        button_layout = QHBoxLayout()
        button_layout.addStretch()

        close_btn = QPushButton(self.tr("Close"))
        close_btn.clicked.connect(dialog.accept)
        button_layout.addWidget(close_btn)

        layout.addLayout(button_layout)

        dialog.show()

    def _restore_active_prompt(self, prompt_type: str, prompt_name: str):
        """Restore an active prompt by name"""
        if not hasattr(self, 'prompt_manager_qt') or not self.prompt_manager_qt:
            return
        
        try:
            if prompt_type == 'translate':
                # Find and activate domain prompt
                for i in range(self.prompt_manager_qt.domain_tree.topLevelItemCount()):
                    item = self.prompt_manager_qt.domain_tree.topLevelItem(i)
                    filename = item.data(0, Qt.ItemDataRole.UserRole)
                    if filename:
                        prompt_data = self.prompt_manager_qt.prompt_library.get_prompt(filename)
                        if prompt_data and prompt_data.get('name') == prompt_name:
                            self.prompt_manager_qt.domain_tree.setCurrentItem(item)
                            self.prompt_manager_qt._activate_domain_expertise('translate')
                            break
            elif prompt_type == 'proofread':
                # Find and activate domain prompt
                for i in range(self.prompt_manager_qt.domain_tree.topLevelItemCount()):
                    item = self.prompt_manager_qt.domain_tree.topLevelItem(i)
                    filename = item.data(0, Qt.ItemDataRole.UserRole)
                    if filename:
                        prompt_data = self.prompt_manager_qt.prompt_library.get_prompt(filename)
                        if prompt_data and prompt_data.get('name') == prompt_name:
                            self.prompt_manager_qt.domain_tree.setCurrentItem(item)
                            self.prompt_manager_qt._activate_domain_expertise('proofread')
                            break
            elif prompt_type == 'project':
                # Find and activate project prompt
                for i in range(self.prompt_manager_qt.project_tree.topLevelItemCount()):
                    item = self.prompt_manager_qt.project_tree.topLevelItem(i)
                    filename = item.data(0, Qt.ItemDataRole.UserRole)
                    if filename:
                        prompt_data = self.prompt_manager_qt.prompt_library.get_prompt(filename)
                        if prompt_data and prompt_data.get('name') == prompt_name:
                            self.prompt_manager_qt.project_tree.setCurrentItem(item)
                            self.prompt_manager_qt._activate_project_guideline()
                            break
        except Exception as e:
            self.log(f"⚠ Could not restore {prompt_type} prompt '{prompt_name}': {e}")

    def _update_proofreading_notes_for_segment(self, segment):
        """Selecting a segment scrolls + highlights the Proofreading-comments
        list to that segment's entries (issue #234 pts 3/5), mirroring the
        Segment-Comments behaviour. The list content itself is (re)built by
        _refresh_proofreading_comments_list() when the underlying data changes."""
        seg_id = getattr(segment, 'id', None) if segment else None
        if seg_id is not None:
            self._sync_proofreading_comments_to_active(seg_id)

    def _refresh_proofreading_comments_list(self):
        """Rebuild the all-project Proofreading-comments list (issue #234 pt.4):
        one entry per (segment, LLM-model) note, each with a clickable
        Segment #N header, the model name, the text, and a delete button.
        Idempotent; cheap to call whenever the proofreading data changes."""
        if not hasattr(self, '_proofreading_comments_list_layout'):
            return  # Sub-tab not built yet (early init)

        from PyQt6.QtWidgets import QFrame as _QFrame

        layout = self._proofreading_comments_list_layout
        self._pc_list_seg_btns = {}  # seg_id → [header buttons] (for highlight)

        while layout.count() > 0:
            item = layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

        # Collect (seg_id, segment, model, text) in document order.
        entries = []
        if self.current_project and self.current_project.segments:
            for seg in self.current_project.segments:
                pn = getattr(seg, 'proofreading_notes', None) or {}
                for model_name, issue_text in pn.items():
                    if issue_text and str(issue_text).strip():
                        entries.append((seg.id, seg, model_name, issue_text))

        if not entries:
            empty = QLabel(
                "(No proofreading comments yet — run <b>QA ▸ Proofreading ▸ "
                "Proofread Translation…</b> to generate AI review comments.)")
            empty.setTextFormat(Qt.TextFormat.RichText)
            empty.setStyleSheet("color: #999; padding: 8px;")
            empty.setWordWrap(True)
            layout.addWidget(empty)
            layout.addStretch()
            return

        # Per-engine colours so different LLMs (GPT vs Claude vs …) are visually
        # distinct in the list. Each distinct model gets a stable colour triple
        # (accent, light tint, dark text) in first-seen order; ≥8 models cycle.
        _pc_palette = [
            ("#ff9800", "#fff3e0", "#e65100"),  # orange
            ("#2196f3", "#e3f2fd", "#0d47a1"),  # blue
            ("#4caf50", "#e8f5e9", "#1b5e20"),  # green
            ("#9c27b0", "#f3e5f5", "#4a148c"),  # purple
            ("#f44336", "#ffebee", "#b71c1c"),  # red
            ("#00897b", "#e0f2f1", "#004d40"),  # teal
            ("#5c6bc0", "#e8eaf6", "#283593"),  # indigo
            ("#6d4c41", "#efebe9", "#3e2723"),  # brown
        ]
        _model_colors = {}
        for _sid, _seg, _m, _t in entries:
            if _m not in _model_colors:
                _model_colors[_m] = _pc_palette[len(_model_colors) % len(_pc_palette)]

        for idx, (seg_id, segment, model_name, issue_text) in enumerate(entries):
            accent, tint, _textcol = _model_colors.get(model_name, _pc_palette[0])
            if idx > 0:
                divider = _QFrame()
                divider.setFrameShape(_QFrame.Shape.HLine)
                divider.setStyleSheet("color: #e0e0e0; max-height: 1px;")
                layout.addWidget(divider)

            # ── Header row: "Segment #N · model"  +  delete button ──
            row = QWidget()
            row_l = QHBoxLayout(row)
            row_l.setContentsMargins(0, 0, 0, 0)
            row_l.setSpacing(0)

            header_btn = QPushButton(f"Segment #{seg_id}  ·  {model_name}")
            header_btn.setFlat(True)
            header_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            _qss = (
                "QPushButton { color: %s; font-weight: bold; font-size: 10pt; "
                "text-align: left; padding: 6px 8px 2px 8px; border: none; "
                "background: transparent; } "
                "QPushButton:hover { background-color: %s; "
                "text-decoration: underline; }" % (accent, tint)
            )
            header_btn.setStyleSheet(_qss)
            header_btn._base_qss = _qss
            header_btn.clicked.connect(
                lambda _c, sid=seg_id: self._navigate_to_segment_by_id(sid))
            row_l.addWidget(header_btn, 1)
            self._pc_list_seg_btns.setdefault(seg_id, []).append(header_btn)

            del_btn = QPushButton("🗑")
            del_btn.setFlat(True)
            del_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            del_btn.setToolTip(f"Delete this proofreading comment ({model_name})")
            del_btn.setFixedWidth(28)
            del_btn.setStyleSheet(
                "QPushButton { border: none; background: transparent; padding: 4px; } "
                "QPushButton:hover { background-color: rgba(244, 67, 54, 0.12); "
                "border-radius: 3px; }")
            del_btn.clicked.connect(
                lambda _c, sid=seg_id, m=model_name: self._delete_proofreading_comment(sid, m))
            row_l.addWidget(del_btn, 0)
            layout.addWidget(row)

            # ── Body: the proofreading text (read-only) ──
            body = QLabel(str(issue_text))
            body.setWordWrap(True)
            body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            body.setStyleSheet(
                f"padding: 4px 12px 8px 12px; font-size: 10pt; color: #333; "
                f"background: {tint}; border-left: 3px solid {accent}; "
                f"margin-left: 6px;")
            layout.addWidget(body)

        layout.addStretch()

        # Re-apply the active-segment highlight after the rebuild. (pt.3/5)
        _active = getattr(self, '_active_pc_seg_id', None)
        if _active is not None:
            self._sync_proofreading_comments_to_active(_active, do_scroll=False)

    def _sync_proofreading_comments_to_active(self, seg_id, do_scroll: bool = True):
        """Scroll + highlight the Proofreading-comments list to the active
        segment's entries (issue #234 pts 3/5). Mirrors the SC list behaviour."""
        self._active_pc_seg_id = seg_id
        for btn in getattr(self, '_active_pc_highlight_btns', []):
            if self._widget_is_alive(btn):
                base = getattr(btn, '_base_qss', None)
                if base is not None:
                    btn.setStyleSheet(base)
        self._active_pc_highlight_btns = []

        btns = getattr(self, '_pc_list_seg_btns', {}).get(seg_id, [])
        if not btns:
            return
        for btn in btns:
            if self._widget_is_alive(btn):
                base = getattr(btn, '_base_qss', btn.styleSheet())
                btn.setStyleSheet(
                    base + "\nQPushButton { background-color: rgba(37, 99, 235, 0.14); "
                    "border-radius: 3px; }")
                self._active_pc_highlight_btns.append(btn)
        if do_scroll:
            scroll = getattr(self, '_proofreading_comments_list_scroll', None)
            first = btns[0]

            def _reveal():
                try:
                    if (scroll is not None and self._widget_is_alive(scroll)
                            and self._widget_is_alive(first)):
                        scroll.ensureWidgetVisible(first)
                except Exception:
                    pass

            QTimer.singleShot(0, _reveal)

    def _delete_proofreading_comment(self, seg_id, model_name):
        """Delete one proofreading comment (a single LLM entry) from a segment
        (issue #234 pt.2). Re-running Proofread Translation regenerates it."""
        if not self.current_project:
            return
        seg = next((s for s in self.current_project.segments if s.id == seg_id), None)
        if seg is None:
            return
        pn = getattr(seg, 'proofreading_notes', None)
        if not pn or model_name not in pn:
            return
        del pn[model_name]
        self.project_modified = True
        try:
            self._refresh_segment_status(seg)
        except Exception:
            pass
        self._refresh_proofreading_comments_list()

    def delete_all_proofreading_comments(self):
        """Delete every proofreading comment in the project (QA ▸ Proofreading).
        Confirms first; re-running Proofread Translation regenerates them."""
        from PyQt6.QtWidgets import QMessageBox as _QMessageBox
        if not self.current_project or not self.current_project.segments:
            _QMessageBox.information(self, "Delete proofreading comments",
                                     "There is no open project.")
            return
        affected = [s for s in self.current_project.segments
                    if getattr(s, 'proofreading_notes', None)]
        if not affected:
            _QMessageBox.information(self, "Delete proofreading comments",
                                     "There are no proofreading comments to delete.")
            return
        total = sum(len(s.proofreading_notes) for s in affected)
        resp = _QMessageBox.question(
            self, "Delete all proofreading comments",
            f"Delete all {total} proofreading comment(s) across {len(affected)} "
            f"segment(s)?\n\nThis cannot be undone, but running Proofread "
            f"Translation again will regenerate them.",
            _QMessageBox.StandardButton.Yes | _QMessageBox.StandardButton.No,
            _QMessageBox.StandardButton.No)
        if resp != _QMessageBox.StandardButton.Yes:
            return
        for s in affected:
            s.proofreading_notes = {}
            try:
                self._refresh_segment_status(s)
            except Exception:
                pass
        self.project_modified = True
        self._refresh_proofreading_comments_list()
        self.log(f"Deleted all proofreading comments ({total} across {len(affected)} segments).")

    def load_llm_settings(self) -> Dict[str, str]:
        """Load LLM settings from user preferences"""
        defaults = {
            'provider': 'openai',
            'openai_model': 'gpt-6.1-sol',
            'claude_model': 'claude-sonnet-5-5',
            'gemini_model': 'gemini-3.8-flash',
            'ollama_model': 'translategemma:12b',
            'custom_openai_model': '',
            'custom_openai_endpoint': '',
            'custom_openai_profiles': [],
            'custom_openai_active_profile': '',
            # Custom MT endpoint(s): a dedicated, separate set of OpenAI-compatible
            # endpoints used as MT engines (e.g. a local MT proxy), independent of
            # the AI custom endpoint above so MT and AI can point at different
            # services at the same time.
            'custom_mt_profiles': [],
            'custom_mt_active_profile': ''
        }

        try:
            prefs = self._load_settings_section("ui")
            saved = prefs.get('llm_settings', defaults)
            # Ensure new keys exist for older configs
            for k, v in defaults.items():
                saved.setdefault(k, v)
            # Auto-migrate: old single-field config → profiles
            if not saved.get('custom_openai_profiles') and saved.get('custom_openai_endpoint'):
                api_keys = self.load_api_keys() if hasattr(self, 'load_api_keys') else {}
                saved['custom_openai_profiles'] = [{
                    'name': 'Custom Endpoint',
                    'endpoint': saved['custom_openai_endpoint'],
                    'model': saved.get('custom_openai_model', ''),
                    'api_key': api_keys.get('custom_openai', '')
                }]
                saved['custom_openai_active_profile'] = 'Custom Endpoint'
            return saved
        except:
            return defaults

    def save_llm_settings(self, settings: Dict[str, str]):
        """Save LLM settings to user preferences"""
        try:
            all_settings = self._load_unified_settings()
            all_settings.setdefault("ui", {})['llm_settings'] = settings
            self._save_unified_settings(all_settings)
        except Exception as e:
            self.log(f"⚠ Could not save LLM settings: {str(e)}")

    def _apply_gemini_proxy(self):
        """
        Apply (or clear) the proxy for Google Gemini by setting/unsetting the
        HTTPS_PROXY environment variable for this process.
        Called on startup and whenever proxy settings are saved.
        """
        import os
        url = self._get_proxy_url()
        if url:
            os.environ["HTTPS_PROXY"] = url
            os.environ["HTTP_PROXY"] = url
        else:
            os.environ.pop("HTTPS_PROXY", None)
            os.environ.pop("HTTP_PROXY", None)

    def _get_custom_mt_profiles(self, settings=None):
        """Return the list of configured Custom MT endpoint profiles."""
        if settings is None:
            settings = self.load_llm_settings()
        return settings.get('custom_mt_profiles', []) or []

    def _get_active_custom_mt_profile(self, settings=None):
        """Get the active Custom MT endpoint profile dict (name, endpoint, model, api_key).

        Mirrors _get_active_custom_profile but for the dedicated MT endpoint set,
        which is independent of the AI custom endpoint. Returns None if none set.
        """
        if settings is None:
            settings = self.load_llm_settings()
        profiles = self._get_custom_mt_profiles(settings)
        active_name = settings.get('custom_mt_active_profile', '')
        for p in profiles:
            if p.get('name') == active_name:
                return p
        return profiles[0] if profiles else None

    def call_custom_mt(self, text: str, source_lang: str, target_lang: str, profile=None) -> str:
        """Translate via a Custom MT endpoint (OpenAI-compatible, e.g. a local MT
        proxy). `profile` is a {name, endpoint, model, api_key} dict; when omitted
        the active Custom MT profile is used. Returns the translated string, or a
        bracketed error message on failure (the QuickTrans convention)."""
        try:
            from modules.llm_clients import LLMClient

            if profile is None:
                profile = self._get_active_custom_mt_profile()
            if not profile:
                return "[Custom MT: no endpoint configured]"

            # Strip whitespace AND any control/non-printable characters (a stray
            # NUL or similar pasted into the field would otherwise make requests
            # reject the URL with "Invalid non-printable ASCII character").
            def _clean(s):
                return ''.join(ch for ch in (s or '') if ord(ch) >= 0x20).strip()

            endpoint = _clean(profile.get('endpoint'))
            if not endpoint:
                return "[Custom MT: no endpoint configured]"

            model = _clean(profile.get('model')) or 'custom-model'
            api_keys = self.load_api_keys()
            api_key = ((profile.get('api_key') or '').strip()
                       or api_keys.get('custom_mt', '')
                       or api_keys.get('custom_openai', '')
                       or 'not-needed')
            http_proxy = self._get_proxy_url() if hasattr(self, '_get_proxy_url') else None

            client = LLMClient(
                api_key=api_key,
                provider='custom_openai',
                model=model,
                base_url=endpoint,
                http_proxy=http_proxy,
            )

            # Raw MT mode (default for MT endpoints): send ONLY the source text as
            # the user message, with the language direction in the system message.
            # A bare MT proxy that simply translates the user message then receives
            # clean text instead of the instruction wrapper, while still learning
            # the target language from the system message. Turn it off for an
            # endpoint that is really an instruction-following LLM.
            raw_mode = bool(profile.get('raw_mode', True))
            if raw_mode:
                prompt = text
                system_prompt = (
                    f"Translate from {source_lang} to {target_lang}. "
                    "Output only the translated text — no quotes, notes, or explanations."
                )
            else:
                prompt = (
                    f"Translate the following text from {source_lang} to {target_lang}.\n"
                    f"Output ONLY the translation, nothing else. "
                    f"No explanations, no alternatives, no notes, no quotation marks.\n\n"
                    f"{text}"
                )
                system_prompt = (
                    "You are a translation engine. Output only the translated text. "
                    "Never add explanations, alternatives, notes, or commentary. "
                    "Never wrap the output in quotes. "
                    "If the text is already in the target language, output it unchanged."
                )

            result = client.translate(
                text="",
                source_lang=source_lang,
                target_lang=target_lang,
                custom_prompt=prompt,
                system_prompt=system_prompt,
            )
            if result:
                result = result.strip()
                if ((result.startswith('"') and result.endswith('"'))
                        or (result.startswith("'") and result.endswith("'"))):
                    result = result[1:-1]
            return result or "[Custom MT: no translation returned]"
        except Exception as e:
            msg = str(e)
            low = msg.lower()
            is_conn = ('connection' in low or 'refused' in low
                       or 'max retries' in low or 'failed to establish' in low
                       or 'connection' in type(e).__name__.lower())
            if is_conn:
                host = locals().get('endpoint', '') or ''
                if any(h in host for h in ('127.0.0.1', 'localhost', '0.0.0.0')):
                    return "[MT proxy not running. Start it.]"
                return "[Custom MT: endpoint unreachable.]"
            return f"[Custom MT error: {msg}]"

    def create_llm_client(self, provider, model, api_keys, settings=None):
        """Create an LLMClient with proper base_url and proxy handling."""
        from modules.llm_clients import LLMClient
        api_key = api_keys.get(provider) or (api_keys.get('google') if provider == 'gemini' else None)
        base_url = None
        if provider == 'openrouter':
            base_url = 'https://openrouter.ai/api/v1'
        elif provider == 'custom_openai':
            if settings is None:
                settings = self.load_llm_settings()
            profile = self._get_active_custom_profile(settings)
            if profile:
                base_url = profile.get('endpoint') or None
                # Active profile's model is the source of truth; only fall
                # back to a passed-in model if the profile has none set.
                model = (profile.get('model') or '').strip() or model or 'custom-model'
                # Profile API key takes priority, then api_keys.txt fallback
                profile_key = (profile.get('api_key') or '').strip()
                api_key = profile_key or api_keys.get('custom_openai', '') or 'not-needed'
            elif not api_key:
                api_key = api_keys.get('custom_openai', '') or 'not-needed'
        # Pass proxy URL to client (None if proxy disabled or not configured)
        # Gemini uses env vars instead (set by _apply_gemini_proxy at startup/settings save)
        http_proxy = self._get_proxy_url() if provider != 'gemini' else None
        return LLMClient(api_key=api_key, provider=provider, model=model, base_url=base_url, http_proxy=http_proxy)

    def load_provider_enabled_states(self) -> Dict[str, bool]:
        """Load provider enable/disable states from user preferences"""
        defaults = {
            'llm_openai': True,
            'llm_claude': True,
            'llm_gemini': True,
            'llm_mistral': True,
            'llm_openrouter': True,
            'llm_ollama': True,
            'llm_custom_openai': True,
            'mt_google_translate': True,
            'mt_deepl': True,
            'mt_microsoft': True,
            'mt_amazon': True,
            'mt_modernmt': True,
            'mt_mymemory': True
        }

        try:
            prefs = self._load_settings_section("ui")
            saved = prefs.get('provider_enabled_states', defaults)
            # Ensure new keys exist for older configs
            for k, v in defaults.items():
                saved.setdefault(k, v)
            return saved
        except:
            return defaults

    def save_provider_enabled_states(self, states: Dict[str, bool]):
        """Save provider enable/disable states to user preferences"""
        try:
            all_settings = self._load_unified_settings()
            all_settings.setdefault("ui", {})['provider_enabled_states'] = states
            self._save_unified_settings(all_settings)
        except Exception as e:
            self.log(f"⚠ Could not save provider enabled states: {str(e)}")

    def translate_current_segment(self, fuzzy_match=None):
        """Translate currently selected segment using LLM (Ctrl+T).

        When ``fuzzy_match`` is a dict (source/target/match_pct/tm_name), the
        FuzzyFixer flow is used: the match is injected into the prompt so the
        AI adapts the existing TM target to the current source, and a
        target-side diff is shown afterwards. See fuzzy_fix_current_segment().
        """
        # QAction.triggered passes a bool ('checked') – normalise it away so the
        # plain Ctrl+T action never looks like a fuzzy match.
        if isinstance(fuzzy_match, bool):
            fuzzy_match = None

        current_row = self.table.currentRow()
        if current_row < 0 or not self.current_project:
            QMessageBox.warning(self, "No Selection", "Please select a segment to translate.")
            return
        
        segment = self.current_project.segments[current_row]
        
        if not segment.source.strip():
            QMessageBox.warning(self, "Empty Source", "Cannot translate empty source text.")
            return
        
        # Load LLM settings first to check provider
        settings = self.load_llm_settings()
        provider = settings.get('provider', 'openai')
        
        # Get model based on provider (active profile wins for custom_openai)
        model = self._resolve_provider_model(settings, provider, 'gpt-6.1-sol')

        # Ollama and custom_openai don't strictly need API keys
        if provider == 'ollama':
            api_keys = {'ollama': 'not-needed'}  # Placeholder - Ollama doesn't use API keys
        elif provider == 'custom_openai':
            api_keys = self.load_api_keys()  # Key is optional for custom endpoints
        else:
            # Load API keys for cloud providers
            api_keys = self.load_api_keys()

            # Check if API key exists for selected provider
            # Note: 'gemini' and 'google' are aliases for the same API key
            has_api_key = provider in api_keys or (provider == 'gemini' and 'google' in api_keys)
            if not has_api_key:
                reply = QMessageBox.question(
                    self, f"{provider.title()} API Key Missing",
                    f"{self._api_key_problem(provider) or f'No API key set for {provider.title()}.'}\n\n"
                    f"Would you like to configure it now?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
                )
                if reply == QMessageBox.StandardButton.Yes:
                    self._go_to_settings_tab()
                return
        
        try:
            self.status_bar.showMessage(f"Translating segment #{segment.id} with {provider} ({model})...")
            QApplication.processEvents()  # Update UI
            
            # For Ollama (local LLM), show a progress dialog since it can take a while
            progress_dialog = None
            elapsed_timer = None
            start_time = None
            
            if provider == 'ollama':
                from PyQt6.QtCore import QTimer, QElapsedTimer
                import time
                
                progress_dialog = QDialog(self)
                progress_dialog.setWindowTitle(self.tr("Local LLM Translation"))
                progress_dialog.setModal(False)  # Non-modal so we can update it
                progress_dialog.setMinimumWidth(400)
                progress_layout = QVBoxLayout(progress_dialog)
                
                progress_header = QLabel(f"<h3>🖥️ Translating with Local LLM</h3>")
                progress_layout.addWidget(progress_header)
                
                progress_info = QLabel(
                    f"<b>Model:</b> {model}<br>"
                    f"<b>Segment:</b> #{segment.id}<br><br>"
                    f"<i>Local LLM translation may take 10-60 seconds depending on your hardware...</i>"
                )
                progress_info.setWordWrap(True)
                progress_layout.addWidget(progress_info)
                
                # Animated progress bar (indeterminate)
                progress_bar = QProgressBar()
                progress_bar.setRange(0, 0)  # Indeterminate mode
                progress_bar.setTextVisible(False)
                progress_layout.addWidget(progress_bar)
                
                # Elapsed time label
                elapsed_label = QLabel(self.tr("⏱️ Elapsed: 0 seconds"))
                elapsed_label.setStyleSheet("color: #333; font-weight: bold; padding: 5px 0;")
                progress_layout.addWidget(elapsed_label)
                
                progress_status = QLabel(self.tr("⏳ Processing... (CPU usage is normal)"))
                progress_status.setStyleSheet("color: #666; padding: 5px 0;")
                progress_layout.addWidget(progress_status)
                
                # Store references
                progress_dialog.status_label = progress_status
                progress_dialog.elapsed_label = elapsed_label
                
                # Setup elapsed time counter
                start_time = time.time()
                
                def update_elapsed():
                    if start_time and hasattr(progress_dialog, 'elapsed_label'):
                        elapsed = int(time.time() - start_time)
                        progress_dialog.elapsed_label.setText(f"⏱️ Elapsed: {elapsed} seconds")
                        QApplication.processEvents()
                
                elapsed_timer = QTimer()
                elapsed_timer.timeout.connect(update_elapsed)
                elapsed_timer.start(1000)  # Update every second
                
                progress_dialog.show()
                QApplication.processEvents()
            
            # Use modular LLM client with user's settings
            client = self.create_llm_client(provider, model, api_keys, settings=settings)

            # Check TM before API call if enabled
            general_prefs = self.load_general_settings()
            check_tm_before_api = general_prefs.get('check_tm_before_api', True)
            check_tm_exact_only = general_prefs.get('check_tm_exact_only', False)
            tm_match = None
            
            if check_tm_before_api and self.tm_database:
                # Get activated TM IDs for current project
                tm_ids = None
                if hasattr(self, 'tm_metadata_mgr') and self.tm_metadata_mgr and self.current_project:
                    project_id = self.current_project.id if hasattr(self.current_project, 'id') else None
                    if project_id:
                        tm_ids = self.tm_metadata_mgr.get_active_tm_ids(project_id)
                
                try:
                    # Strip outer structural tags if setting is enabled (for cleaner TM matching)
                    search_source = segment.source
                    if self.hide_outer_wrapping_tags:
                        search_source, _ = strip_outer_wrapping_tags(search_source)

                    if check_tm_exact_only:
                        # Use exact match only (faster - O(1) hash lookup)
                        exact_match = self.tm_database.get_exact_match(search_source, tm_ids=tm_ids)
                        if exact_match:
                            tm_match = exact_match.get('target_text', '')
                            self.log(f"✓ Found 100% TM match (exact) for segment #{segment.id}")
                    else:
                        # Use fuzzy search (includes 100% matches)
                        matches = self.tm_database.search_all(search_source, tm_ids=tm_ids, enabled_only=False, max_matches=1)
                        if matches and matches[0].get('match_pct', 0) == 100:
                            tm_match = matches[0].get('target', '')
                            self.log(f"✓ Found 100% TM match for segment #{segment.id}")
                except Exception as e:
                    self.log(f"⚠ TM check error: {e}")
            
            # If we have a 100% match and auto-insert is enabled, use it.
            # Skip when running FuzzyFixer – the user explicitly wants the AI to
            # adapt a fuzzy match, not silently drop in an exact TM hit.
            auto_insert_100 = general_prefs.get('auto_insert_100', False)
            if tm_match and auto_insert_100 and not fuzzy_match:
                # Auto-insert the TM match
                _undo_old_target = segment.target
                _undo_old_status = segment.status
                segment.target = tm_match
                segment.status = "draft"
                try:
                    self.record_undo_state(segment.id, _undo_old_target, tm_match, _undo_old_status, "draft")
                except Exception:
                    pass  # never let undo failure break the actual operation
                self.project_modified = True
                self.update_window_title()

                # Update grid (using cell widget)
                target_widget = self.table.cellWidget(current_row, 3)
                if target_widget and isinstance(target_widget, EditableGridTextEditor):
                    target_widget.setPlainText(tm_match)
                else:
                    # Fallback if widget doesn't exist
                    self.table.setItem(current_row, 3, QTableWidgetItem(tm_match))
                self.update_status_icon(current_row, "draft")
                
                # Auto-resize the row to fit the new content
                self._auto_resize_single_row(current_row)
                
                # Add to TM if not already there
                if self.tm_database:
                    try:
                        self.tm_database.add_to_project_tm(segment.source, tm_match)
                    except:
                        pass
                
                self.log(f"✓ Auto-inserted 100% TM match for segment #{segment.id}")
                self.status_bar.showMessage(f"✓ Segment #{segment.id} translated from TM (100% match)", 3000)
                return
            
            # Build prompt - use optimized prompt for local LLM (Ollama)
            custom_prompt = None
            
            if provider == 'ollama':
                # Use a focused but context-aware prompt for local LLMs
                source_lang = self.current_project.source_lang
                target_lang = self.current_project.target_lang
                
                # Get 2-3 surrounding segments for context (not 5+ like cloud LLMs)
                context_lines = []
                surrounding_count = 2  # Fewer segments for faster inference
                
                try:
                    start_idx = max(0, current_row - surrounding_count)
                    end_idx = min(len(self.current_project.segments), current_row + surrounding_count + 1)
                    
                    for i in range(start_idx, end_idx):
                        seg = self.current_project.segments[i]
                        if i == current_row:
                            continue  # Skip current segment
                        if seg.target and seg.target.strip():
                            # Show already translated segments as reference
                            context_lines.append(f"• {seg.source[:80]}{'...' if len(seg.source) > 80 else ''}")
                            context_lines.append(f"  → {seg.target[:80]}{'...' if len(seg.target) > 80 else ''}")
                except:
                    pass
                
                # Build optimized prompt
                prompt_parts = [f"You are a professional translator ({source_lang} → {target_lang})."]
                
                
                # Add surrounding segment context
                if context_lines:
                    context_section = "\n".join(context_lines)
                    prompt_parts.append("")
                    prompt_parts.append("Context from surrounding segments:")
                    prompt_parts.append(context_section)
                
                # Add the segment to translate
                prompt_parts.append("")
                # Inline-tag preservation – only when the segment carries numbered
                # <N> markers, so plain segments aren't burdened with the rule.
                if re.search(r'</?\d+/?>', segment.source or ''):
                    prompt_parts.append(
                        "Keep any numbered tags (<1>…</1> paired, <2/> standalone) exactly as "
                        "written, with the same numbers. A paired tag MUST wrap the translated "
                        "word(s) it wrapped in the source – never output an empty pair like <1></1> "
                        "with the text left outside it.")
                _codes_note = _inline_codes.prompt_note([segment.source or ''])
                if _codes_note:
                    prompt_parts.append(_codes_note)
                # FuzzyFixer reference for local LLMs (rendered from the same
                # editable template used for cloud providers).
                if fuzzy_match and fuzzy_match.get('source') and fuzzy_match.get('target') \
                        and hasattr(self, 'prompt_manager_qt') and self.prompt_manager_qt:
                    try:
                        instruction = self.prompt_manager_qt.get_fuzzy_fixer_template()
                        try:
                            _ff_pct = int(round(float(fuzzy_match.get('match_pct', 0) or 0)))
                        except (TypeError, ValueError):
                            _ff_pct = 0
                        instruction = instruction.replace("{{TM_SOURCE}}", fuzzy_match.get('source', ''))
                        instruction = instruction.replace("{{TM_TARGET}}", fuzzy_match.get('target', ''))
                        instruction = instruction.replace("{{MATCH_PCT}}", str(_ff_pct))
                        instruction = instruction.replace("{{TM_NAME}}", fuzzy_match.get('tm_name', '') or 'TM')
                        instruction = instruction.replace("{{SOURCE_TEXT}}", segment.source)
                        prompt_parts.append("")
                        prompt_parts.append(instruction)
                    except Exception as e:
                        self.log(f"⚠ Could not add FuzzyFixer reference to Ollama prompt: {e}")

                prompt_parts.append("Translate this segment accurately. Output ONLY the translation:")
                prompt_parts.append(segment.source)
                prompt_parts.append("")
                prompt_parts.append("Translation:")

                custom_prompt = "\n".join(prompt_parts)
                
                self.log(f"  Using optimized Ollama prompt ({len(custom_prompt)} chars, {len(context_lines)//2} context segs)")
            
            elif hasattr(self, 'prompt_manager_qt') and self.prompt_manager_qt:
                try:
                    # Get surrounding segments if enabled
                    surrounding_segments = general_prefs.get('surrounding_segments', 5)
                    surrounding_context = ""
                    
                    if surrounding_segments > 0:
                        try:
                            current_idx = current_row
                            start_idx = max(0, current_idx - surrounding_segments)
                            end_idx = min(len(self.current_project.segments), current_idx + surrounding_segments + 1)
                            surrounding = self.current_project.segments[start_idx:end_idx]
                            
                            if len(surrounding) > 1:  # Only add if we have surrounding segments
                                context_parts = []
                                for i, seg in enumerate(surrounding):
                                    actual_idx = start_idx + i
                                    if actual_idx == current_idx:
                                        context_parts.append(f">>> {seg.id}. {seg.source} <<<  [TRANSLATE THIS]")
                                    else:
                                        context_parts.append(f"{seg.id}. {seg.source}")
                                        if seg.target:
                                            context_parts.append(f"    → {seg.target}")
                                
                                surrounding_context = "\n\n**SURROUNDING SEGMENTS FOR CONTEXT:**\n"
                                surrounding_context += "(The segment marked with >>> <<< is the one to translate)\n\n"
                                surrounding_context += "\n".join(context_parts)
                                self.log(f"  Including {len(surrounding)} surrounding segments ({surrounding_segments} before/after)")
                        except Exception as e:
                            self.log(f"⚠ Could not add surrounding segments: {e}")
                    
                    # Get glossary terms for AI injection
                    glossary_terms = self.get_ai_inject_glossary_terms()

                    custom_prompt = self.prompt_manager_qt.build_final_prompt(
                        source_text=segment.source,
                        source_lang=self.current_project.source_lang,
                        target_lang=self.current_project.target_lang,
                        mode="single",
                        glossary_terms=glossary_terms,
                        target_text=segment.target or "",
                        fuzzy_match=fuzzy_match
                    )

                    # Add surrounding context before the translation delimiter
                    if surrounding_context:
                        # Insert before the "YOUR TRANSLATION" delimiter
                        if "**YOUR TRANSLATION" in custom_prompt:
                            custom_prompt = custom_prompt.replace(
                                "**YOUR TRANSLATION",
                                surrounding_context + "\n\n**YOUR TRANSLATION"
                            )
                        else:
                            custom_prompt += surrounding_context

                    # The user's own inline codes (Settings → Inline Codes, issue #194)
                    _codes_note = _inline_codes.prompt_note([segment.source or ''])
                    if _codes_note:
                        if "**YOUR TRANSLATION" in custom_prompt:
                            custom_prompt = custom_prompt.replace(
                                "**YOUR TRANSLATION", _codes_note + "\n\n**YOUR TRANSLATION", 1)
                        else:
                            custom_prompt += "\n\n" + _codes_note
                            
                except Exception as e:
                    self.log(f"⚠ Could not build prompt from manager: {e}")
            
            # Check for figure references and prepare images if available
            images = None
            if self.figure_context and self.figure_context.has_images():
                try:
                    # Detect figure references in source text
                    figure_refs = self.figure_context.detect_figure_references(segment.source)
                    if figure_refs:
                        self.log(f"  🖼️ Detected figure references in segment #{segment.id}: {', '.join(figure_refs)}")
                        # Get images for detected figures
                        images_for_text = self.figure_context.get_images_for_text(segment.source)
                        if images_for_text:
                            # Check if model supports vision
                            if LLMClient.model_supports_vision(provider, model):
                                # Convert PIL images to appropriate format for provider
                                if provider == "gemini":
                                    # Gemini uses PIL.Image directly
                                    images = images_for_text
                                else:
                                    # OpenAI and Claude use base64 PNG
                                    images = [
                                        (ref, self.figure_context.pil_image_to_base64_png(img))
                                        for ref, img in images_for_text
                                    ]
                                self.log(f"  ✅ Including {len(images)} figure images: {', '.join(figure_refs)}")
                            else:
                                self.log(f"  ⚠️ Figures detected ({', '.join(figure_refs)}) but model '{model}' doesn't support vision")
                        else:
                            self.log(f"  ⚠️ Figure references detected but no matching images found in loaded folder")
                except Exception as e:
                    self.log(f"  ⚠️ Could not load figures: {e}")
            
            # Translate using the module
            from modules import usage_log as _usage_log
            with _usage_log.UsageContext(task=("FuzzyFixer" if fuzzy_match else "Translate")):
                translation = client.translate(
                    text=segment.source,
                    source_lang=self.current_project.source_lang,
                    target_lang=self.current_project.target_lang,
                    custom_prompt=custom_prompt,
                    images=images
                )

            if translation:
                # Drifted numbered tags (< 1>, &lt;1&gt; …) back to canonical (#226)
                from modules.tag_repair import repair_drifted_tags
                translation = repair_drifted_tags(segment.source, translation)
                # Update segment
                _undo_old_target = segment.target
                _undo_old_status = segment.status
                segment.target = translation
                segment.status = "draft"
                try:
                    self.record_undo_state(segment.id, _undo_old_target, translation, _undo_old_status, "draft")
                except Exception:
                    pass  # never let undo failure break the actual operation

                # Update grid - Column 3 is Target (using cell widget)
                target_widget = self.table.cellWidget(current_row, 3)
                if target_widget and isinstance(target_widget, EditableGridTextEditor):
                    target_widget.setPlainText(translation)
                else:
                    # Fallback: create new widget if none exists
                    self.table.setItem(current_row, 3, QTableWidgetItem(translation))
                self.update_status_icon(current_row, "draft")
                
                # Auto-resize the row to fit the new content
                self._auto_resize_single_row(current_row)
                
                # Add to Translation Memory
                if self.tm_database:
                    try:
                        self.tm_database.add_to_project_tm(segment.source, translation)
                        self.log(f"✓ Added to TM: {segment.source[:30]}... → {translation[:30]}...")
                    except Exception as tm_error:
                        self.log(f"⚠ Could not add to TM: {str(tm_error)}")
                
                # Mark project as modified
                self.project_modified = True
                self.update_window_title()

                # FuzzyFixer: show what the AI changed vs the original TM target
                if fuzzy_match and fuzzy_match.get('target'):
                    try:
                        self._show_fuzzy_fix_diff(fuzzy_match.get('target', ''), translation)
                    except Exception as e:
                        self.log(f"⚠ Could not show FuzzyFixer diff: {e}")
                    self.log(f"🔧 FuzzyFixer adapted #{segment.id} from {fuzzy_match.get('match_pct', '?')}% match")
                    self.status_bar.showMessage(f"🔧 FuzzyFixer adapted segment #{segment.id}", 3000)
                else:
                    self.log(f"✓ Segment #{segment.id} translated with {provider}/{model}")
                    self.status_bar.showMessage(f"✓ Segment #{segment.id} translated", 3000)
                
                # Stop elapsed timer and close progress dialog
                if elapsed_timer:
                    elapsed_timer.stop()
                if progress_dialog:
                    progress_dialog.close()
            else:
                self.log(f"✗ Translation failed for segment #{segment.id}")
                # Stop elapsed timer and close progress dialog
                if elapsed_timer:
                    elapsed_timer.stop()
                if progress_dialog:
                    progress_dialog.close()
                QMessageBox.warning(self, "Translation Failed", "No translation received from LLM.")
                
        except Exception as e:
            # Stop elapsed timer and close progress dialog
            if 'elapsed_timer' in locals() and elapsed_timer:
                elapsed_timer.stop()
            if 'progress_dialog' in locals() and progress_dialog:
                progress_dialog.close()
            self.log(f"✗ Translation error: {str(e)}")
            QMessageBox.critical(self, "Translation Error", f"Failed to translate segment:\n\n{str(e)}")
            self.status_bar.showMessage("Translation failed", 3000)

    def _quicklauncher_build_custom_prompt(self, prompt_relative_path: str, source_text: str, source_lang: str, target_lang: str, target_text: str = "") -> str:
        """Build a prompt for QuickLauncher using the selected prompt as instructions.

        This is a GENERIC prompt builder (not translation-specific) that allows QuickLauncher prompts
        to do anything: explain, define, search, translate, analyze, etc.

        Supports placeholders:
        - {{SELECTION}} or {{SOURCE_TEXT}} - The selected text
        - {{TARGET_TEXT}} - Current target (translation) of the active segment
        - {{SOURCE_LANGUAGE}} - Source language name
        - {{TARGET_LANGUAGE}} - Target language name
        - {{SOURCE+TARGET_CONTEXT}} - Project segments with both source and target (for proofreading)
        - {{SOURCE_CONTEXT}} - Project segments with source only (for translation questions)
        - {{TARGET_CONTEXT}} - Project segments with target only (for consistency/style analysis)
        """
        if not hasattr(self, 'prompt_manager_qt') or not self.prompt_manager_qt:
            raise RuntimeError("Prompt manager not available")

        pm = self.prompt_manager_qt
        lib = getattr(pm, 'library', None)
        if not lib:
            raise RuntimeError("Prompt library not available")

        prompt_data = lib.prompts.get(prompt_relative_path)
        if not prompt_data:
            raise RuntimeError(f"Prompt not found: {prompt_relative_path}")

        prompt_content = (prompt_data.get('content') or "").strip()
        if not prompt_content:
            raise RuntimeError("Prompt content is empty")

        # Build document context if requested (three variants)
        source_target_context = ""
        source_only_context = ""
        target_only_context = ""
        
        has_source_target = "{{SOURCE+TARGET_CONTEXT}}" in prompt_content
        has_source_only = "{{SOURCE_CONTEXT}}" in prompt_content
        has_target_only = "{{TARGET_CONTEXT}}" in prompt_content
        
        if (has_source_target or has_source_only or has_target_only) and hasattr(self, 'current_project') and self.current_project:
            if has_source_target:
                source_target_context = self._build_quicklauncher_document_context(mode="both")
                self.log(f"🔍 QuickLauncher: Built SOURCE+TARGET context ({len(source_target_context)} chars)")
            if has_source_only:
                source_only_context = self._build_quicklauncher_document_context(mode="source")
                self.log(f"🔍 QuickLauncher: Built SOURCE_ONLY context ({len(source_only_context)} chars)")
            if has_target_only:
                target_only_context = self._build_quicklauncher_document_context(mode="target")
                self.log(f"🔍 QuickLauncher: Built TARGET_ONLY context ({len(target_only_context)} chars)")
        else:
            if has_source_target:
                self.log("⚠️ QuickLauncher: {{SOURCE+TARGET_CONTEXT}} requested but no project loaded")
            if has_source_only:
                self.log("⚠️ QuickLauncher: {{SOURCE_CONTEXT}} requested but no project loaded")
            if has_target_only:
                self.log("⚠️ QuickLauncher: {{TARGET_CONTEXT}} requested but no project loaded")

        # Replace placeholders in the prompt content
        prompt_content = prompt_content.replace("{{SOURCE_LANGUAGE}}", source_lang)
        prompt_content = prompt_content.replace("{{TARGET_LANGUAGE}}", target_lang)
        prompt_content = prompt_content.replace("{{SOURCE_TEXT}}", source_text)
        prompt_content = prompt_content.replace("{{TARGET_TEXT}}", target_text or "")
        prompt_content = prompt_content.replace("{{SELECTION}}", source_text)  # Alternative placeholder
        prompt_content = prompt_content.replace("{{SOURCE+TARGET_CONTEXT}}", source_target_context)
        prompt_content = prompt_content.replace("{{SOURCE_CONTEXT}}", source_only_context)
        prompt_content = prompt_content.replace("{{TARGET_CONTEXT}}", target_only_context)
        
        # Debug: Log the final prompt being sent
        self.log(f"📝 QuickLauncher: Final prompt ({len(prompt_content)} chars):")
        self.log("─" * 80)
        self.log(prompt_content[:500] + ("..." if len(prompt_content) > 500 else ""))
        self.log("─" * 80)
        
        # If the prompt doesn't contain the selection/text, append it
        if "{{SOURCE_TEXT}}" not in prompt_data.get('content', '') and "{{SELECTION}}" not in prompt_data.get('content', ''):
            prompt_content += f"\n\nText:\n{source_text}"
        
        return prompt_content

    def run_grid_quicklauncher_prompt(self, prompt_relative_path: str, origin_widget: QTextEdit, behavior: str = "show"):
        """Run a QuickLauncher prompt on the current selection and either show the result or replace selection/target."""
        from PyQt6.QtWidgets import QMessageBox, QApplication
        from modules.llm_clients import LLMClient

        if not origin_widget:
            return

        input_text = self._quicklauncher_get_selection_text(origin_widget)
        if not input_text:
            QMessageBox.information(self, "QuickLauncher", "Please select some text first (or click inside a non-empty cell).")
            return

        # Get current segment's target text for {{TARGET_TEXT}} placeholder
        current_target_text = ""
        if hasattr(self, 'table') and self.table and hasattr(self, 'current_project') and self.current_project:
            current_row = self.table.currentRow()
            if 0 <= current_row < len(self.current_project.segments):
                current_target_text = self.current_project.segments[current_row].target or ""

        # Determine project languages
        source_lang = "English"
        target_lang = "Dutch"
        if hasattr(self, 'current_project') and self.current_project:
            source_lang = getattr(self.current_project, 'source_lang', source_lang) or source_lang
            target_lang = getattr(self.current_project, 'target_lang', target_lang) or target_lang

        # Copy-to-clipboard: expand the prompt against the current segment and
        # copy it, ready to paste into an online LLM (Claude.ai, ChatGPT, etc.).
        # No API call is made and no API key is needed.
        if behavior == "clipboard":
            try:
                expanded = self._quicklauncher_build_custom_prompt(
                    prompt_relative_path=prompt_relative_path,
                    source_text=input_text,
                    source_lang=source_lang,
                    target_lang=target_lang,
                    target_text=current_target_text,
                )
            except Exception as e:
                QMessageBox.critical(self, "QuickLauncher", f"Could not build prompt:\n\n{e}")
                return
            QApplication.clipboard().setText(expanded)
            try:
                self.status_bar.showMessage("⚡ QuickLauncher: prompt copied to clipboard", 4000)
            except Exception:
                pass
            return

        # Determine provider/model (active profile wins for custom_openai)
        settings = self.load_llm_settings()
        provider = settings.get('provider', 'openai')
        model = self._resolve_provider_model(settings, provider, None)

        # Load API keys
        api_keys = self.load_api_keys()
        if provider == 'ollama':
            api_key = api_keys.get('ollama', '') or 'not-needed'
        elif provider == 'custom_openai':
            profile = self._get_active_custom_profile(settings)
            profile_key = (profile.get('api_key') or '').strip() if profile else ''
            api_key = profile_key or api_keys.get('custom_openai', '') or 'not-needed'
        else:
            if not api_keys:
                QMessageBox.warning(self, "QuickLauncher", "No API keys found. Configure them in Settings first.")
                return
            api_key = api_keys.get(provider) or (api_keys.get('google') if provider == 'gemini' else None)
            if not api_key:
                QMessageBox.warning(self, "QuickLauncher", f"No API key found for provider '{provider}'.")
                return

        try:
            self.status_bar.showMessage(f"⚡ QuickLauncher: running '{prompt_relative_path}'…")
            QApplication.processEvents()

            custom_prompt = self._quicklauncher_build_custom_prompt(
                prompt_relative_path=prompt_relative_path,
                source_text=input_text,
                source_lang=source_lang,
                target_lang=target_lang,
                target_text=current_target_text
            )

            base_url = None
            if provider == 'custom_openai':
                profile = self._get_active_custom_profile(settings)
                base_url = profile.get('endpoint') or None if profile else None
            http_proxy = self._get_proxy_url() if provider != 'gemini' else None
            client = LLMClient(api_key=api_key, provider=provider, model=model, base_url=base_url, http_proxy=http_proxy)

            # Use translate() with empty text and custom_prompt for generic AI completion
            # This allows QuickLauncher prompts to do anything (explain, define, search, etc.)
            # not just translation. Same pattern as AI Assistant.
            output_text = client.translate(
                text="",  # Empty - we're using custom_prompt for everything
                source_lang="en",  # Dummy values
                target_lang="en",
                custom_prompt=custom_prompt
            )

            if not output_text:
                QMessageBox.warning(self, "QuickLauncher", "No response received from the LLM.")
                return

        except Exception as e:
            QMessageBox.critical(self, "QuickLauncher", f"QuickLauncher failed:\n\n{e}")
            return
        finally:
            try:
                self.status_bar.clearMessage()
            except Exception:
                pass

        # Decide where to apply replacements
        target_widget = None
        row = getattr(origin_widget, 'row', -1)
        table = getattr(origin_widget, 'table', None) or getattr(origin_widget, 'table_ref', None)

        # If originating from target editor, replace in-place. If from source editor, target is column 3.
        if isinstance(origin_widget, EditableGridTextEditor):
            target_widget = origin_widget
        elif table is not None and row is not None and row >= 0:
            try:
                target_widget = table.cellWidget(row, 3)
            except Exception:
                target_widget = None

        def apply_replacement():
            if not target_widget or not isinstance(target_widget, QTextEdit):
                return
            cursor = target_widget.textCursor()
            cursor.beginEditBlock()
            try:
                if cursor.hasSelection():
                    cursor.insertText(output_text)
                else:
                    cursor.select(cursor.SelectionType.Document)
                    cursor.insertText(output_text)
                target_widget.setTextCursor(cursor)
            finally:
                cursor.endEditBlock()

        if behavior == "replace":
            if not target_widget or not isinstance(target_widget, QTextEdit):
                self._quicklauncher_show_result_dialog("Supervertaler QuickLauncher", output_text)
                return

            apply_replacement()
            return

        # Default: show dialog (with optional Replace button)
        can_apply = target_widget is not None and isinstance(target_widget, QTextEdit)
        title = "Supervertaler QuickLauncher"
        self._quicklauncher_show_result_dialog(title, output_text, apply_callback=apply_replacement if can_apply else None)

    def translate_multiple_segments(self, scope: str):
        """Translate segments by scope (selected, status-based, or all)."""
        if not self.current_project:
            QMessageBox.warning(self, "No Project", "Please load or create a project first.")
            return

        segments_with_rows: List[Tuple[int, Segment]] = []
        description = ""

        if scope == "selected_not_started":
            selected = self._get_selected_segments_with_rows()
            if not selected:
                QMessageBox.information(
                    self,
                    "No Selection",
                    "Please select one or more segments in the grid or list view first."
                )
                return
            target_statuses = {"not_started"}
            segments_with_rows = [(idx, seg) for idx, seg in selected if seg.status in target_statuses]
            if not segments_with_rows:
                QMessageBox.information(
                    self,
                    "Nothing to Translate",
                    "The selected segments are not marked as 'Not started'."
                )
                return
            description = "selected not-started segment(s)"
        elif scope == "all_not_started":
            target_statuses = {"not_started"}
            segments_with_rows = [
                (idx, seg) for idx, seg in enumerate(self.current_project.segments)
                if seg.status in target_statuses
            ]
            description = "not-started segment(s)"
        elif scope == "all_pretranslated":
            target_statuses = {"pretranslated"}
            segments_with_rows = [
                (idx, seg) for idx, seg in enumerate(self.current_project.segments)
                if seg.status in target_statuses
            ]
            description = "pre-translated segment(s)"
        elif scope == "all_not_started_pretranslated":
            target_statuses = {"not_started", "pretranslated"}
            segments_with_rows = [
                (idx, seg) for idx, seg in enumerate(self.current_project.segments)
                if seg.status in target_statuses
            ]
            description = "not-started and pre-translated segment(s)"
        elif scope == "all_translatable":
            target_statuses = TRANSLATABLE_STATUSES
            segments_with_rows = [
                (idx, seg) for idx, seg in enumerate(self.current_project.segments)
                if seg.status in target_statuses
            ]
            labels = ", ".join(sorted({get_status(s).label for s in target_statuses}))
            description = f"{labels} segment(s)"
        elif scope == "all_segments":
            segments_with_rows = list(enumerate(self.current_project.segments))
            description = "segment(s)"
        elif scope == "all_empty":
            # NEW: Translate only segments with empty target (useful after partial batch translation)
            segments_with_rows = [
                (idx, seg) for idx, seg in enumerate(self.current_project.segments)
                if not seg.target or not seg.target.strip()
            ]
            description = "empty segment(s)"
        elif scope == "filtered_segments":
            # NEW: Translate only currently filtered/visible segments
            segments_with_rows = self._get_filtered_segments_with_rows()
            if not segments_with_rows:
                QMessageBox.information(
                    self,
                    "No Filtered Segments",
                    "No segments are currently visible after filtering.\nClear filters or adjust your filter criteria."
                )
                return
            description = "filtered segment(s)"
        else:
            return

        if not segments_with_rows:
            QMessageBox.information(
                self,
                "Nothing to Translate",
                f"No {description} available for translation."
            )
            return

        segments_with_rows = sorted(segments_with_rows, key=lambda item: item[0])
        self.translate_batch(segments_with_rows=segments_with_rows, scope_description=description)

    def translate_batch(self, segments_with_rows: Optional[List[Tuple[int, Segment]]] = None, scope_description: Optional[str] = None):
        """
        Translate ALL segments in the project using LLM provider.
        
        CRITICAL REQUIREMENTS for memoQ bilingual files:
        - Column 2 (source): Source text to translate
        - Column 3 (target): MUST BE COMPLETELY EMPTY (no pre-translations!)
        - Column 5 (status): Completely ignored by system
        
        The system will translate EVERY segment in the file.
        Users MUST ensure target column is 100% empty before export.
        """
        if not self.current_project:
            QMessageBox.warning(self, "No Project", "Please load or create a project first.")
            return

        if segments_with_rows is None:
            # Translate ALL segments in the project
            # Users must ensure memoQ export has completely empty target column
            segments_with_rows = [
                (idx, seg) for idx, seg in enumerate(self.current_project.segments)
                if seg.source.strip()  # Skip empty structural segments
            ]
            scope_description = scope_description or "segment(s)"
        else:
            valid_segments: List[Tuple[int, Segment]] = []
            for row_index, segment in segments_with_rows:
                if 0 <= row_index < len(self.current_project.segments) and segment.source.strip():
                    valid_segments.append((row_index, segment))
            segments_with_rows = valid_segments
            scope_description = scope_description or "segment(s)"

        if not segments_with_rows:
            QMessageBox.information(
                self,
                "Nothing to Translate",
                f"No {scope_description} available for translation."
            )
            return

        segments_to_translate = sorted(segments_with_rows, key=lambda item: item[0])
        total_segments = len(segments_to_translate)
        
        # Log what we found
        self.log(f"📊 Project has {total_segments} segments → Will translate ALL of them")
        
        # VALIDATION: Check if any segments already have target text
        segments_with_target = [(idx, seg) for idx, seg in segments_to_translate if seg.target and seg.target.strip()]
        
        if segments_with_target:
            # Show examples of problematic segments
            examples = segments_with_target[:3]
            example_text = "\n".join([
                f"• Segment {seg.id}: \"{seg.target[:50]}{'...' if len(seg.target) > 50 else ''}\""
                for idx, seg in examples
            ])
            
            warning_msg = (
                f"⚠️ CRITICAL ERROR: {len(segments_with_target)} of {total_segments} segments have target text!\n\n"
                f"Examples:\n{example_text}\n\n"
                f"❌ This file is NOT ready for batch translation!\n\n"
                f"REQUIRED: Target column (column 3) must be 100% EMPTY:\n"
                f"  • No pre-translations\n"
                f"  • No confirmed segments\n"
                f"  • No partial translations\n"
                f"  • Completely blank target cells\n\n"
                f"The system will translate ALL {total_segments} segments.\n"
                f"Existing translations will cause MAJOR synchronization problems!\n\n"
                f"💡 TIP: You can clear all targets without re-importing:\n"
                f"   1. Right-click in the grid → Select All\n"
                f"   2. Right-click again → Clear Target (Selected)\n\n"
                f"Or: Cancel → Clean target column in memoQ → Re-export\n\n"
                f"Proceed anyway? (STRONGLY NOT recommended - will cause errors!)"
            )
            reply = QMessageBox.warning(
                self,
                "Target Text Detected",
                warning_msg,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No  # Default to No for safety
            )
            if reply != QMessageBox.StandardButton.Yes:
                self.log("✓ Batch translation cancelled - user chose to clean file first")
                return
            else:
                self.log("⚠️ User proceeded with batch translation despite existing target text")

        # Check if this is a retry pass (skip dialog if so)
        is_retry_pass = hasattr(self, '_batch_retry_pass') and self._batch_retry_pass > 0
        
        if is_retry_pass:
            # Use stored settings from previous pass - skip dialog entirely
            translation_provider_type = getattr(self, '_batch_provider_type', 'LLM')
            translation_provider_name = getattr(self, '_batch_provider_name', 'openai')
            model = getattr(self, '_batch_model', 'gpt-6.1-sol')
            tm_exact_only = getattr(self, '_batch_tm_exact_only', False)  # Retrieve TM exact-only setting
            api_keys = self.load_api_keys()
            settings = self.load_llm_settings()
            llm_provider = settings.get('provider', 'openai')
            llm_model = model
        else:
            # Show provider selection dialog (only on first pass)
            provider_dialog = QDialog(self)
            provider_dialog.setWindowTitle(self.tr("Select Translation Provider"))
            provider_dialog.setModal(True)
            provider_dialog.setMinimumWidth(500)

            dialog_layout = QVBoxLayout(provider_dialog)

            # Header
            header = QLabel(f"<h3>🚀 Batch Translate {total_segments} Segment{'s' if total_segments != 1 else ''}</h3>")
            dialog_layout.addWidget(header)

            info_label = QLabel(
                "Choose which translation provider to use for batch translation.\n"
                "This may take several minutes and consume API credits."
            )
            info_label.setWordWrap(True)
            info_label.setStyleSheet("color: #666; padding: 10px 0;")
            dialog_layout.addWidget(info_label)

            dialog_layout.addSpacing(10)

            # Provider selection checkboxes (mutually exclusive)
            provider_group = QGroupBox(self.tr("Translation Provider"))
            provider_layout = QVBoxLayout()

            # Translation Memory option
            tm_checkbox = CheckmarkCheckBox(self.tr("📖 TM (Translation Memory) - Pre-translate from activated TMs (exact + fuzzy ≥75%)"))
            tm_checkbox.setChecked(False)
            provider_layout.addWidget(tm_checkbox)

            # TM Exact Matches Only sub-option (indented to show it's related to TM)
            tm_exact_only_checkbox = CheckmarkCheckBox(self.tr("       ⚡ Exact matches only (100% matches only - fastest)"))
            tm_exact_only_checkbox.setEnabled(False)  # Disabled until TM is selected
            tm_exact_only_checkbox.setToolTip(
                "Only search for 100% exact matches using fast hash lookup.\n"
                "Skips fuzzy matching (75–99% similar segments).\n"
                "Recommended for large documents when you have a good TM."
            )
            # Add left margin to create visual hierarchy
            tm_exact_only_checkbox.setStyleSheet("QCheckBox { margin-left: 20px; }")
            provider_layout.addWidget(tm_exact_only_checkbox)
            
            # Enable/disable exact-only option based on TM selection
            def on_tm_selection_changed(checked):
                tm_exact_only_checkbox.setEnabled(checked)
                if not checked:
                    tm_exact_only_checkbox.setChecked(False)
            
            tm_checkbox.toggled.connect(on_tm_selection_changed)

            llm_checkbox = CheckmarkCheckBox(self.tr("🤖 LLM (AI) - Use configured LLM provider (GPT, Claude, Gemini)"))
            llm_checkbox.setChecked(True)  # Default to LLM
            provider_layout.addWidget(llm_checkbox)

            # Load API keys to check what's available
            api_keys = self.load_api_keys()
            enabled_providers = self.load_provider_enabled_states()

            # Show available MT providers
            mt_providers_available = []
            if enabled_providers.get('mt_google_translate', True) and api_keys.get('google_translate'):
                mt_providers_available.append('Google Translate')
            if enabled_providers.get('mt_deepl', True) and api_keys.get('deepl'):
                mt_providers_available.append('DeepL')
            if enabled_providers.get('mt_microsoft', True) and (api_keys.get('microsoft_translate') or api_keys.get('azure_translate')):
                mt_providers_available.append('Microsoft Translator')
            if enabled_providers.get('mt_amazon', True) and (api_keys.get('amazon_translate') or api_keys.get('aws_translate')):
                mt_providers_available.append('Amazon Translate')
            if enabled_providers.get('mt_modernmt', True) and api_keys.get('modernmt'):
                mt_providers_available.append('ModernMT')
            if enabled_providers.get('mt_mymemory', True):
                mt_providers_available.append('MyMemory')

            mt_label_text = "🌐 MT (Machine Translation)"
            if mt_providers_available:
                mt_label_text += f" - {', '.join(mt_providers_available)}"
            else:
                mt_label_text += " - No MT providers configured"

            mt_checkbox = CheckmarkCheckBox(mt_label_text)
            mt_checkbox.setEnabled(len(mt_providers_available) > 0)
            provider_layout.addWidget(mt_checkbox)

            # Make checkboxes mutually exclusive
            def on_tm_toggled(checked):
                if checked:
                    llm_checkbox.setChecked(False)
                    mt_checkbox.setChecked(False)
        
            def on_llm_toggled(checked):
                if checked:
                    tm_checkbox.setChecked(False)
                    mt_checkbox.setChecked(False)

            def on_mt_toggled(checked):
                if checked:
                    tm_checkbox.setChecked(False)
                    llm_checkbox.setChecked(False)

            tm_checkbox.toggled.connect(on_tm_toggled)
            llm_checkbox.toggled.connect(on_llm_toggled)
            mt_checkbox.toggled.connect(on_mt_toggled)

            if not mt_providers_available:
                mt_warning = QLabel(self.tr("⚠️ Configure MT API keys in Settings → MT Settings to enable"))
                mt_warning.setStyleSheet("color: #ff6b6b; font-size: 9pt; padding-left: 25px;")
                provider_layout.addWidget(mt_warning)

            provider_group.setLayout(provider_layout)
            dialog_layout.addWidget(provider_group)

            dialog_layout.addSpacing(10)

            # Show current LLM settings
            settings = self.load_llm_settings()
            llm_provider = settings.get('provider', 'openai')
            model_key = f'{llm_provider}_model'
            llm_model = settings.get(model_key, 'gpt-6.1-sol')

            if llm_provider == 'custom_openai':
                profile = self._get_active_custom_profile(settings)
                profile_name = profile.get('name', 'Custom') if profile else 'Custom'
                provider_display = f"Custom: {profile_name}"
            else:
                provider_display = llm_provider.title()
            llm_info = QLabel(f"📊 Current LLM: {provider_display} ({llm_model})")
            llm_info.setStyleSheet("color: #666; font-size: 9pt; padding: 5px 0;")
            dialog_layout.addWidget(llm_info)

            # Up-front cost estimate for the LLM option (issue #8)
            cost_estimate_label = QLabel()
            cost_estimate_label.setWordWrap(True)
            cost_estimate_label.setStyleSheet("color: #666; font-size: 9pt; padding: 0 0 5px 0;")
            cost_estimate_label.setToolTip(self.tr(
                "Estimated from the source text length (about 4 characters per token),\n"
                "your current prompt and glossary, and the batch size, priced with the\n"
                "shared price list (pricing.json). Actual costs are logged per call –\n"
                "see Tools → Token Usage & Costs."))
            dialog_layout.addWidget(cost_estimate_label)

            dialog_layout.addSpacing(10)
        
            # Options group
            options_group = QGroupBox(self.tr("Options"))
            options_layout = QVBoxLayout()
        
            # Retry until complete option
            retry_checkbox = CheckmarkCheckBox(self.tr("🔄 Retry until all segments are translated (recommended)"))
            retry_checkbox.setToolTip(
                "If some segments fail or are empty after the first pass,\n"
                "automatically retry translating just those segments.\n"
                "This continues until all segments have translations\n"
                "or a maximum of 5 retries is reached."
            )
            retry_checkbox.setChecked(True)  # Default to enabled
            options_layout.addWidget(retry_checkbox)

            # Auto-confirm TM matches option
            auto_confirm_checkbox = CheckmarkCheckBox(self.tr("✔ Auto-confirm 100% TM matches"))
            auto_confirm_checkbox.setToolTip(
                "Automatically set 100% TM matches to 'Confirmed' status\n"
                "instead of the default 'TM 100%' status.\n\n"
                "This is useful when you trust your TM and want to skip\n"
                "manual review of exact matches."
            )
            auto_confirm_checkbox.setChecked(False)
            auto_confirm_checkbox.setEnabled(False)  # Only enabled when TM is selected
            options_layout.addWidget(auto_confirm_checkbox)

            # Enable/disable auto-confirm based on TM selection
            def on_tm_autoconfirm_toggle(checked):
                auto_confirm_checkbox.setEnabled(checked)
                if not checked:
                    auto_confirm_checkbox.setChecked(False)

            tm_checkbox.toggled.connect(on_tm_autoconfirm_toggle)

            # FuzzyFixer option (applies to AI/LLM translation)
            fuzzy_fixer_checkbox = CheckmarkCheckBox(self.tr("🔧 Use FuzzyFixer (adapt fuzzy TM matches with AI)"))
            fuzzy_fixer_checkbox.setToolTip(
                "AI translation only. For each segment that has a fuzzy TM match\n"
                f"within your configured range ({getattr(self, 'fuzzy_fixer_min_pct', 75)}–{getattr(self, 'fuzzy_fixer_max_pct', 99)}%),\n"
                "the existing TM target is fed to the AI as a reference so it adapts\n"
                "it to the new source instead of translating from scratch.\n\n"
                "Note: segments are processed individually (no batching) in this mode."
            )
            fuzzy_fixer_checkbox.setChecked(getattr(self, 'fuzzy_fixer_enabled', False))
            options_layout.addWidget(fuzzy_fixer_checkbox)

            _estimates = {}

            def update_cost_estimate(*_):
                """Refresh the estimate: LLM only, one call per segment with FuzzyFixer."""
                if not llm_checkbox.isChecked():
                    cost_estimate_label.setVisible(False)
                    return
                per_segment = fuzzy_fixer_checkbox.isChecked()
                try:
                    if per_segment not in _estimates:
                        _estimates[per_segment] = self._estimate_batch_ai_cost(
                            [seg for _row, seg in segments_to_translate], llm_provider,
                            self._resolve_provider_model(settings, llm_provider, llm_model),
                            per_segment=per_segment)
                    est = _estimates[per_segment]
                    from modules.cost_estimate import format_cost
                    currency, rate = self._cost_display_settings()
                    if est['cost_usd'] is None:
                        cost_text = self.tr("unknown – this model is not in the price list")
                    elif llm_provider == 'ollama':
                        cost_text = self.tr("free (local model)")
                    else:
                        cost_text = "~" + format_cost(est['cost_usd'], currency, rate)
                    cost_estimate_label.setText(self.tr(
                        "💰 Estimated cost: {} · ~{:,} tokens in / ~{:,} out in {:,} call(s)").format(
                            cost_text, est['input_tokens'], est['output_tokens'], est['batches']))
                    cost_estimate_label.setVisible(True)
                except Exception as e:
                    self.log(f"⚠ Cost estimate unavailable: {e}")
                    cost_estimate_label.setVisible(False)

            llm_checkbox.toggled.connect(update_cost_estimate)
            fuzzy_fixer_checkbox.toggled.connect(update_cost_estimate)
            update_cost_estimate()

            # Note: the old "Fix tags with AutoTagger after translating" toggle was
            # removed (Workbench #229) — Translate already places inline tags, so it
            # was a redundant second pass. Use Bulk Operations → "Auto-tag Segments"
            # to fix tags on already-translated segments.

            options_group.setLayout(options_layout)
            dialog_layout.addWidget(options_group)

            dialog_layout.addSpacing(20)

            # Buttons
            button_layout = QHBoxLayout()
            button_layout.addStretch()

            cancel_btn = QPushButton(self.tr("Cancel"))
            cancel_btn.clicked.connect(provider_dialog.reject)
            button_layout.addWidget(cancel_btn)

            ok_btn = QPushButton(self.tr("Start Translation"))
            ok_btn.setDefault(True)
            ok_btn.clicked.connect(provider_dialog.accept)
            button_layout.addWidget(ok_btn)

            dialog_layout.addLayout(button_layout)

            # Show dialog
            if provider_dialog.exec() != QDialog.DialogCode.Accepted:
                return

            # Determine which provider was selected
            use_tm = tm_checkbox.isChecked()
            use_mt = mt_checkbox.isChecked()
            retry_until_complete = retry_checkbox.isChecked()
            tm_exact_only = tm_exact_only_checkbox.isChecked()
            auto_confirm_tm = auto_confirm_checkbox.isChecked()
            use_fuzzy_fixer = fuzzy_fixer_checkbox.isChecked()

            # Store retry setting for recursive calls
            self._batch_retry_enabled = retry_until_complete
            self._batch_tm_exact_only = tm_exact_only  # Store for retry passes
            self._batch_auto_confirm_tm = auto_confirm_tm  # Store for auto-confirm
            self._batch_use_fuzzy_fixer = use_fuzzy_fixer  # Store for retry passes

            # Persist FuzzyFixer toggle as default for next time
            try:
                _gp = self.load_general_settings()
                _gp['fuzzy_fixer_enabled'] = use_fuzzy_fixer
                self.save_general_settings(_gp)
                self.fuzzy_fixer_enabled = use_fuzzy_fixer
            except Exception:
                pass
            
            # Initialize model variable (will be set to actual model if LLM is selected)
            model = None

            if use_tm:
                # Use Translation Memory
                translation_provider_type = 'TM'
                translation_provider_name = 'Translation Memory'
            
                # Check if TM database is available
                if not self.tm_database:
                    QMessageBox.critical(
                        self, "No TM Database",
                        "Translation Memory database is not initialized. Please load a project first."
                    )
                    return
            
                mode_str = " (Exact matches only)" if tm_exact_only else " (with fuzzy matching)"
                confirm_str = ", auto-confirm 100%" if auto_confirm_tm else ""
                self.log(f"📖 Using Translation Memory for batch pre-translation{mode_str}{confirm_str}")
            
            elif use_mt:
                # Use MT provider
                translation_provider_type = 'MT'
                translation_provider_name = mt_providers_available[0] if mt_providers_available else None
                if not translation_provider_name:
                    QMessageBox.critical(
                        self, "No MT Provider",
                        "No Machine Translation provider available. Please configure MT API keys in Settings."
                    )
                    return
            else:
                # Use LLM provider
                translation_provider_type = 'LLM'
            
                # Ollama doesn't need API keys - it's local
                if llm_provider == 'ollama':
                    api_keys = {'ollama': 'not-needed'}  # Placeholder - Ollama doesn't use API keys
                elif llm_provider == 'custom_openai':
                    pass  # Key is optional for custom endpoints
                elif not api_keys:
                    QMessageBox.critical(
                        self, "API Keys Missing",
                        "Please configure your API keys in Settings first."
                    )
                    return
                # Note: 'gemini' and 'google' are aliases for the same API key
                elif llm_provider not in api_keys and not (llm_provider == 'gemini' and 'google' in api_keys):
                    QMessageBox.critical(
                        self, f"{llm_provider.title()} API Key Missing",
                        f"Please configure your {llm_provider.title()} API key in Settings."
                    )
                    return

                translation_provider_name = llm_provider
                model = llm_model
            
                # Store settings for potential retry passes
                self._batch_provider_type = translation_provider_type
                self._batch_provider_name = translation_provider_name
                self._batch_model = model

        # All segments go to translation - TM pre-translation should be done separately
        # via "Edit → Batch Operations → Pre-translate from TM" before AI translation
        segments_needing_translation = segments_to_translate

        # Create provider info string for dialog
        if translation_provider_type == 'MT':
            provider_info = f"Provider: {translation_provider_name} (Machine Translation)"
        elif translation_provider_type == 'TM':
            mode = "Exact matches only" if tm_exact_only else "with fuzzy matching"
            provider_info = f"Provider: Translation Memory ({mode})"
        else:
            provider_info = f"Provider: {translation_provider_name.title()} | Model: {model}"

        # Get retry settings
        retry_pass = getattr(self, '_batch_retry_pass', 0)
        retry_enabled = getattr(self, '_batch_retry_enabled', False)
        
        # =====================================================================
        # TM PRE-TRANSLATION - Run on main thread (no SQLite threading issues)
        # Uses the same database methods as the Compare Panel
        # =====================================================================
        if translation_provider_type == 'TM':
            # Get activated TM IDs for TM pre-translation
            tm_ids = None
            if hasattr(self, 'tm_metadata_mgr') and self.tm_metadata_mgr and self.current_project:
                project_id = self.current_project.id if hasattr(self.current_project, 'id') else None
                if project_id:
                    tm_ids = self.tm_metadata_mgr.get_active_tm_ids(project_id)
            
            if not tm_ids:
                QMessageBox.warning(
                    self, "No TMs Activated",
                    "No Translation Memories are activated for reading.\n\n"
                    "Please go to the 💾 TMs tab and enable the 'Read' "
                    "checkbox for the TMs you want to use."
                )
                return
            
            self.log(f"📖 Pre-translate from TM: Using activated TMs: {tm_ids}")

            # ── Batch TM pre-translation (optimized) ──
            import time
            start_time = time.time()
            total_segments = len(segments_needing_translation)

            progress = QProgressDialog(
                f"Pre-translating {total_segments} segments from TM...",
                "Cancel", 0, 100, self
            )
            progress.setWindowTitle(self.tr("🔍 TM Pre-Translation"))
            progress.setWindowModality(Qt.WindowModality.WindowModal)
            progress.setMinimumDuration(0)
            progress.setMinimumWidth(450)
            progress.show()
            QApplication.processEvents()

            # Step 1: Prepare search sources (strip outer tags if needed, deduplicate)
            progress.setLabelText(f"Preparing {total_segments} segments...")
            progress.setValue(5)
            QApplication.processEvents()

            # Build mapping: search_source -> list of (row_index, segment) pairs
            source_to_segments = {}
            for row_index, segment in segments_needing_translation:
                search_source = segment.source
                if self.hide_outer_wrapping_tags:
                    search_source, _ = strip_outer_wrapping_tags(search_source)
                source_to_segments.setdefault(search_source, []).append((row_index, segment))

            unique_sources = list(source_to_segments.keys())
            unique_count = len(unique_sources)
            self.log(f"   {total_segments} segments → {unique_count} unique source texts")

            if progress.wasCanceled():
                progress.close()
                return

            # Step 2: Batch TM lookup
            try:
                if tm_exact_only:
                    progress.setLabelText(
                        f"Batch exact-matching {unique_count} unique segments...\n\n"
                        f"This uses a single optimized database query."
                    )
                    progress.setValue(20)
                    QApplication.processEvents()

                    batch_results = self.tm_database.get_exact_matches_batch(
                        sources=unique_sources,
                        tm_ids=tm_ids
                    )

                    # Convert to common format: source -> {target, match_pct}
                    match_results = {}
                    for source_text, match in batch_results.items():
                        match_results[source_text] = {
                            'target': match.get('target_text', ''),
                            'match_pct': 100
                        }
                else:
                    progress.setLabelText(
                        f"Batch matching {unique_count} unique segments (exact + fuzzy)...\n\n"
                        f"Phase 1: Exact matching..."
                    )
                    progress.setValue(20)
                    QApplication.processEvents()

                    def fuzzy_progress(current, total):
                        """Update progress during fuzzy matching phase (20% to 80%)."""
                        if total > 0:
                            pct = 20 + int(60 * current / total)
                            progress.setValue(pct)
                            progress.setLabelText(
                                f"Fuzzy matching {current}/{total} unmatched segments...\n\n"
                                f"Phase 2: Finding best fuzzy matches (≥75%)."
                            )
                        QApplication.processEvents()
                        if progress.wasCanceled():
                            raise InterruptedError("Canceled by user")

                    batch_results = self.tm_database.search_all_batch(
                        sources=unique_sources,
                        tm_ids=tm_ids,
                        enabled_only=False,
                        progress_callback=fuzzy_progress
                    )

                    match_results = {}
                    for source_text, match in batch_results.items():
                        match_pct = match.get('match_pct', 0)
                        if match_pct >= 75:
                            match_results[source_text] = {
                                'target': match.get('target', ''),
                                'match_pct': match_pct,
                                'source': match.get('source', source_text),
                                'tm_name': match.get('tm_name', ''),
                                'tm_id': match.get('tm_id', '')
                            }

            except Exception as e:
                self.log(f"   ✗ Batch TM search error: {e}")
                match_results = {}

            if progress.wasCanceled():
                progress.close()
                return

            # Step 3: Apply results to segments and update grid
            progress.setLabelText(f"Applying {len(match_results)} matches to segments...")
            progress.setValue(80)
            QApplication.processEvents()

            success_count = 0
            no_match_count = 0
            auto_confirmed_count = 0

            for search_source, seg_list in source_to_segments.items():
                match = match_results.get(search_source)
                for row_index, segment in seg_list:
                    if match:
                        segment.target = match['target']
                        match_pct = match.get('match_pct', 100)
                        segment.match_percent = match_pct
                        if match_pct >= 100 and getattr(self, '_batch_auto_confirm_tm', False):
                            segment.status = "confirmed"
                            auto_confirmed_count += 1
                        elif match_pct >= 100:
                            segment.status = "tm_100"
                        else:
                            segment.status = "tm_fuzzy"
                        # Store batch match info on segment for Match Panel display
                        segment._batch_tm_match = {
                            'source': match.get('source', search_source),
                            'target': match['target'],
                            'match_pct': match_pct,
                            'tm_name': match.get('tm_name', ''),
                            'tm_id': match.get('tm_id', ''),
                            # v1.10.50: flag reverse-direction hits so the
                            # Match Panel can show a "⇄ reversed" chip.
                            'reverse_match': match.get('reverse_match', False),
                        }
                        success_count += 1
                    else:
                        no_match_count += 1

            # Invalidate prefetch cache so Match Panel does fresh lookups
            if hasattr(self, 'translation_matches_cache') and self.translation_matches_cache:
                with self.translation_matches_cache_lock:
                    self.translation_matches_cache.clear()
                self.log(f"   Cache cleared after batch pre-translation")

            elapsed = time.time() - start_time
            elapsed_str = f"{elapsed:.1f}s"

            # Step 4: Reload entire grid from updated segment data.
            # The grid build is the slowest step on large projects (it
            # creates a custom widget per row and sometimes ran for 30s+
            # with the title bar saying "Not Responding"). Switch the
            # dialog to indeterminate, hand the row loop a progress
            # callback so it yields to the Qt event loop every 25 rows,
            # and close only when the rebuild is actually done.
            progress.setRange(0, 0)
            progress.setLabelText(
                f"Reloading grid ({success_count} translated)…")
            QApplication.processEvents()

            def _grid_progress(done, total):
                progress.setLabelText(
                    f"Reloading grid… ({done:,} / {total:,})"
                )
                QApplication.processEvents()

            self.load_segments_to_grid(progress_callback=_grid_progress)
            progress.close()

            # Show completion message
            self.log(f"═══════════════════════════════════════════════════════════")
            self.log(f"✓ TM Pre-Translation Complete ({elapsed_str})")
            confirm_note = f" ({auto_confirmed_count} auto-confirmed)" if auto_confirmed_count > 0 else ""
            self.log(f"   Translated: {success_count}{confirm_note} | No match: {no_match_count}")
            self.log(f"   Unique sources: {unique_count} | Total segments: {total_segments}")
            self.log(f"═══════════════════════════════════════════════════════════")

            confirm_msg = f"\n✔ Auto-confirmed: {auto_confirmed_count}" if auto_confirmed_count > 0 else ""
            QMessageBox.information(
                self, "TM Pre-Translation Complete",
                f"Pre-translation from TM complete in {elapsed_str}.\n\n"
                f"✓ Translated: {success_count}\n"
                f"⊘ No match: {no_match_count}"
                f"{confirm_msg}"
            )

            self.project_modified = True
            self.update_window_title()
            self.update_progress_stats()
            return  # Exit - TM pre-translation is complete
        
        # =====================================================================
        # LLM/MT TRANSLATION - Uses background worker thread
        # =====================================================================
        
        # Create worker and dialog
        # Note: TM pre-translation is now handled on main thread above,
        # so we don't pass tm_ids to the worker anymore
        # Pre-fetch glossary terms on main thread (SQLite is not thread-safe)
        glossary_terms = self.get_ai_inject_glossary_terms()

        # FuzzyFixer: pre-fetch the best fuzzy match per segment on the main
        # thread (SQLite is not thread-safe) so the worker can inject each
        # segment's match into its prompt. LLM provider only.
        fuzzy_match_map = {}
        use_fuzzy_fixer = getattr(self, '_batch_use_fuzzy_fixer', False)
        if translation_provider_type == 'LLM' and use_fuzzy_fixer and self.tm_database:
            try:
                ff_tm_ids = None
                if hasattr(self, 'tm_metadata_mgr') and self.tm_metadata_mgr and self.current_project:
                    _pid = getattr(self.current_project, 'id', None)
                    if _pid:
                        ff_tm_ids = self.tm_metadata_mgr.get_active_tm_ids(_pid)
                ff_min = int(getattr(self, 'fuzzy_fixer_min_pct', 75))
                ff_max = int(getattr(self, 'fuzzy_fixer_max_pct', 99))
                ff_source_to_segs = {}
                for _row_index, _segment in segments_needing_translation:
                    _ssrc = _segment.source
                    if self.hide_outer_wrapping_tags:
                        _ssrc, _ = strip_outer_wrapping_tags(_ssrc)
                    ff_source_to_segs.setdefault(_ssrc, []).append(_segment)
                ff_results = self.tm_database.search_all_batch(
                    sources=list(ff_source_to_segs.keys()),
                    tm_ids=ff_tm_ids,
                    enabled_only=False,
                )
                for _ssrc, _match in ff_results.items():
                    _pct = _match.get('match_pct', 0)
                    if ff_min <= _pct <= ff_max and _match.get('target'):
                        _fm = {
                            'source': _match.get('source', _ssrc),
                            'target': _match.get('target', ''),
                            'match_pct': _pct,
                            'tm_name': _match.get('tm_name', ''),
                        }
                        for _seg in ff_source_to_segs.get(_ssrc, []):
                            fuzzy_match_map[_seg.id] = _fm
                self.log(f"🔧 FuzzyFixer: {len(fuzzy_match_map)} segment(s) have a {ff_min}-{ff_max}% match to adapt")
            except Exception as e:
                self.log(f"⚠ FuzzyFixer: could not build fuzzy map: {e}")

        # Get base_url and api_key for custom_openai provider (from active profile)
        custom_base_url = None
        custom_api_key = None
        if translation_provider_name == 'custom_openai':
            profile = self._get_active_custom_profile(settings)
            if profile:
                custom_base_url = profile.get('endpoint') or None
                profile_key = (profile.get('api_key') or '').strip()
                if profile_key:
                    custom_api_key = profile_key

        # Warn-only monthly-budget pre-flight (advisory; never blocks). LLM only.
        if translation_provider_type == 'LLM':
            try:
                from modules import usage_log
                _budget = float(getattr(self, 'monthly_budget_usd', 0.0) or 0.0)
                if _budget > 0:
                    _spent = usage_log.month_to_date_cost()
                    if _spent >= _budget:
                        from PyQt6.QtWidgets import QMessageBox
                        _n = len(segments_needing_translation)
                        _reply = QMessageBox.question(
                            self, self.tr("Monthly budget reached"),
                            self.tr(
                                "You have used ${:.2f} of your ${:.2f} monthly AI budget.\n\n"
                                "Start this batch of {} segment(s) anyway?").format(_spent, _budget, _n),
                            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
                        if _reply != QMessageBox.StandardButton.Yes:
                            return
            except Exception:
                pass

        worker = PreTranslationWorker(
            self,
            segments_needing_translation,  # Use filtered segments after TM pre-check
            translation_provider_type,
            translation_provider_name,
            model if translation_provider_type == 'LLM' else None,
            tm_exact_only=tm_exact_only,
            prompt_manager=self.prompt_manager_qt if hasattr(self, 'prompt_manager_qt') else None,
            retry_enabled=retry_enabled,
            retry_pass=retry_pass,
            tm_ids=None,  # TM pre-translation now on main thread
            glossary_terms=glossary_terms,
            base_url=custom_base_url,
            custom_api_key=custom_api_key,
            http_proxy=self._get_proxy_url() if translation_provider_name != 'gemini' else None,
            use_fuzzy_fixer=use_fuzzy_fixer,
            fuzzy_match_map=fuzzy_match_map,
        )
        dialog = LiveProgressDialog(self, len(segments_needing_translation), provider_info)
        
        # Track success/error counts for dialog updates
        success_count = 0
        error_count = 0
        
        # Connect signals
        def handle_progress_update(current, total, message, success, elapsed_time):
            nonlocal success_count, error_count
            if success:
                success_count += 1
            else:
                error_count += 1
            dialog.add_console_line(message, success)
            dialog.update_progress(current, total, elapsed_time, success_count, error_count)
            
            # Update grid immediately for successful translations
            if success and current <= len(segments_needing_translation):
                row_index, segment = segments_needing_translation[current - 1]
                if row_index < self.table.rowCount():
                    # Strip outer wrapping tags if setting is enabled
                    display_text = segment.target
                    if self.hide_outer_wrapping_tags:
                        display_text, _ = strip_outer_wrapping_tags(display_text)
                    target_widget = self.table.cellWidget(row_index, 3)
                    if target_widget and isinstance(target_widget, EditableGridTextEditor):
                        target_widget.setPlainText(display_text)
                    else:
                        self.table.setItem(row_index, 3, QTableWidgetItem(display_text))
                    self.update_status_icon(row_index, segment.status)
        
        def handle_translation_complete(final_success_count, final_error_count):
            dialog.show_completion_message(final_success_count, final_error_count)
            self.project_modified = True
            self.update_window_title()
            self.auto_resize_rows()
            self.update_progress_stats()

            # v1.10.204: push the worker's collected undo entries onto the
            # app undo stack so Ctrl+Z can roll back a pre-translation batch.
            try:
                entries = getattr(worker, 'undo_entries', None) or []
                if entries:
                    self.record_undo_states_batch(entries)
            except Exception as undo_err:
                self.log(f"⚠ Undo recording failed for batch translation: {undo_err}")
            
            # Check if this is the final pass (no retry pending)
            if not hasattr(self, '_pending_retry'):
                # Log completion
                self.log(f"═══════════════════════════════════════════════════════════")
                self.log(f"✓ Batch Translation Complete")
                self.log(f"   Translated: {final_success_count} | Failed: {final_error_count}")
                self.log(f"═══════════════════════════════════════════════════════════")
                
                # Clear retry settings
                if hasattr(self, '_batch_retry_pass'):
                    delattr(self, '_batch_retry_pass')
                if hasattr(self, '_batch_retry_enabled'):
                    delattr(self, '_batch_retry_enabled')
        
        def handle_retry_needed(empty_segments_after):
            retry_count = len(empty_segments_after)
            current_pass = worker.retry_pass
            next_pass = current_pass + 2  # +2 because pass 0 is "first pass", so pass 1 is "retry 2"
            
            # Log retry
            self.log(f"🔄 Retry pass {next_pass}: {retry_count} segment(s) still empty, retrying...")
            
            # Update retry pass counter
            self._batch_retry_pass = current_pass + 1
            self._pending_retry = True  # Flag to prevent completion message
            
            # Close current dialog
            dialog.accept()
            
            # Recursively call translate_batch with only empty segments
            # Clear the pending flag before recursion
            if hasattr(self, '_pending_retry'):
                delattr(self, '_pending_retry')
            
            self.translate_batch(
                segments_with_rows=empty_segments_after,
                scope_description=f"empty segment(s) (retry {next_pass})"
            )
        
        worker.progress_update.connect(handle_progress_update)
        worker.translation_complete.connect(handle_translation_complete)
        worker.retry_needed.connect(handle_retry_needed)
        worker.translation_error.connect(
            lambda msg: self.log(f"❌ Batch translation failed: {msg}"))

        # Connect cancel button
        dialog.rejected.connect(worker.cancel)
        
        # Start worker and show dialog
        worker.start()
        dialog.exec()
        
        # Wait for worker to finish
        worker.wait()
        
        return  # batch translation runs on PreTranslationWorker (above)

    def _fetch_llm_translation_async(self, source_text: str, segment, row_index: int):
        """Fetch LLM translation asynchronously and add to results panel"""
        if not source_text or not source_text.strip():
            return
        
        # Check if we have API keys and LLM settings configured
        api_keys = self.load_api_keys()
        if not api_keys:
            return  # No API keys configured, skip LLM

        settings = self.load_llm_settings()
        provider = settings.get('provider', 'openai')
        
        # Check if provider is enabled
        enabled_providers = self.load_provider_enabled_states()
        enabled_key = f"llm_{provider}"
        if not enabled_providers.get(enabled_key, True):
            return  # Provider is disabled, skip LLM

        # Get API key for provider
        api_key_value = api_keys.get(provider) or (api_keys.get('google') if provider == 'gemini' else None)
        if not api_key_value and provider not in ('ollama', 'custom_openai'):
            return  # No API key for selected provider
        # Resolve custom_openai profile for API key and base_url
        _custom_profile = None
        if provider == 'custom_openai':
            _custom_profile = self._get_active_custom_profile(settings)
            profile_key = (_custom_profile.get('api_key') or '').strip() if _custom_profile else ''
            api_key_value = profile_key or api_key_value or 'not-needed'

        # Get model based on provider (active profile wins for custom_openai)
        model = self._resolve_provider_model(settings, provider, 'gpt-6.1-sol')

        # Get languages
        source_lang = getattr(self.current_project, 'source_lang', 'en')
        target_lang = getattr(self.current_project, 'target_lang', 'nl')

        # Use QTimer to run in background (non-blocking)
        def fetch_and_update():
            try:
                from modules.llm_clients import LLMClient
                from modules.translation_results_panel import TranslationMatch

                base_url = _custom_profile.get('endpoint') or None if _custom_profile else None
                http_proxy = self._get_proxy_url() if provider != 'gemini' else None
                client = LLMClient(
                    api_key=api_key_value,
                    provider=provider,
                    model=model,
                    base_url=base_url,
                    http_proxy=http_proxy
                )

                # Build full prompt using prompt manager
                custom_prompt = None
                try:
                    # Access parent through closure
                    parent = self
                    if hasattr(parent, 'prompt_manager_qt') and parent.prompt_manager_qt:
                        # Get glossary terms for AI injection
                        glossary_terms = parent.get_ai_inject_glossary_terms() if hasattr(parent, 'get_ai_inject_glossary_terms') else []
                        custom_prompt = parent.prompt_manager_qt.build_final_prompt(
                            source_text=source_text,
                            source_lang=source_lang,
                            target_lang=target_lang,
                            mode="single",
                            glossary_terms=glossary_terms,
                            target_text=getattr(segment, 'target', '') or ""
                        )
                except Exception as e:
                    self.log(f"⚠ Could not build LLM prompt from manager: {e}")
                
                translation = client.translate(
                    text=source_text,
                    source_lang=source_lang,
                    target_lang=target_lang,
                    custom_prompt=custom_prompt
                )
                
                if translation and hasattr(self, 'assistance_widget') and self.assistance_widget:
                    # Check if this segment is still selected
                    current_row = self.table.currentRow()
                    if current_row == row_index:
                        # Map provider to code
                        provider_code_map = {
                            'openai': 'GPT',
                            'claude': 'CL',
                            'gemini': 'GEM'
                        }
                        provider_code = provider_code_map.get(provider, 'LLM')
                        
                        # Create LLM match
                        llm_match = TranslationMatch(
                            source=source_text,
                            target=translation,
                            relevance=98,  # High relevance for LLM
                            metadata={
                                'model': model,
                                'provider': provider,
                                'timestamp': datetime.now().isoformat()
                            },
                            match_type='LLM',
                            compare_source=source_text,
                            provider_code=provider_code
                        )
                        
                        # Update all results panels with LLM match
                        if hasattr(self, 'results_panels'):
                            current_matches = {"LLM": [llm_match]}
                            for panel in self.results_panels:
                                try:
                                    # Get current matches and add LLM
                                    if hasattr(panel, 'matches_by_type'):
                                        existing_matches = panel.matches_by_type.copy()
                                        existing_matches["LLM"] = [llm_match]
                                        panel.set_matches(existing_matches)
                                    else:
                                        panel.set_matches(current_matches)
                                except Exception as e:
                                    self.log(f"Error updating panel with LLM: {e}")
                        self.log(f"✓ LLM translation added to results pane")
            except Exception as e:
                # Silently fail - don't interrupt workflow
                self.log(f"LLM translation failed (silent): {str(e)}")
        
        # Use QTimer to run async (small delay to avoid blocking)
        QTimer.singleShot(100, fetch_and_update)

    def _fetch_mt_translation_async(self, source_text: str, segment, row_index: int):
        """Fetch Machine Translation asynchronously and add to results panel"""
        if not source_text or not source_text.strip():
            return
        
        # Check if we have MT API keys configured
        api_keys = self.load_api_keys()
        if not api_keys:
            return  # No API keys configured, skip MT
        
        # Check enabled providers
        enabled_providers = self.load_provider_enabled_states()
        
        # Try MT providers in priority order (first available and enabled wins)
        # Priority: Google Translate > DeepL > Microsoft > Amazon > ModernMT > MyMemory
        mt_provider = None
        mt_api_key = None
        
        if enabled_providers.get('mt_google_translate', True) and api_keys.get('google_translate'):
            mt_provider = 'google_translate'
            mt_api_key = api_keys['google_translate']
        elif enabled_providers.get('mt_deepl', True) and api_keys.get('deepl'):
            mt_provider = 'deepl'
            mt_api_key = api_keys['deepl']
        elif enabled_providers.get('mt_microsoft', True) and (api_keys.get('microsoft_translate') or api_keys.get('azure_translate')):
            mt_provider = 'microsoft'
            mt_api_key = api_keys.get('microsoft_translate') or api_keys.get('azure_translate')
        elif enabled_providers.get('mt_amazon', True) and (api_keys.get('amazon_translate') or api_keys.get('aws_translate')):
            mt_provider = 'amazon'
            mt_api_key = api_keys.get('amazon_translate') or api_keys.get('aws_translate')
        elif enabled_providers.get('mt_modernmt', True) and api_keys.get('modernmt'):
            mt_provider = 'modernmt'
            mt_api_key = api_keys['modernmt']
        elif enabled_providers.get('mt_mymemory', True) and api_keys.get('mymemory'):
            mt_provider = 'mymemory'
            mt_api_key = api_keys.get('mymemory')  # Optional, MyMemory works without key
        # Note: MyMemory can work without a key, but we only use it if explicitly configured
        else:
            return  # No MT API keys available or all providers disabled
        
        # Get languages
        source_lang = getattr(self.current_project, 'source_lang', 'en')
        target_lang = getattr(self.current_project, 'target_lang', 'nl')
        
        # Use QTimer to run in background (non-blocking)
        def fetch_and_update():
            try:
                from modules.translation_results_panel import TranslationMatch
                
                # Call appropriate MT service
                if mt_provider == 'google_translate':
                    translation = self.call_google_translate(source_text, source_lang, target_lang, mt_api_key)
                    provider_name = 'Google Translate'
                elif mt_provider == 'deepl':
                    translation = self.call_deepl(source_text, source_lang, target_lang, mt_api_key)
                    provider_name = 'DeepL'
                elif mt_provider == 'microsoft':
                    translation = self.call_microsoft_translate(source_text, source_lang, target_lang, mt_api_key)
                    provider_name = 'Microsoft Translator'
                elif mt_provider == 'amazon':
                    # Amazon requires region, check for amazon_translate_region or use default
                    region = api_keys.get('amazon_translate_region', 'us-east-1')
                    translation = self.call_amazon_translate(source_text, source_lang, target_lang, mt_api_key, region)
                    provider_name = 'Amazon Translate'
                elif mt_provider == 'modernmt':
                    translation = self.call_modernmt(source_text, source_lang, target_lang, mt_api_key)
                    provider_name = 'ModernMT'
                elif mt_provider == 'mymemory':
                    translation = self.call_mymemory(source_text, source_lang, target_lang, mt_api_key)
                    provider_name = 'MyMemory'
                else:
                    return
                
                if translation and not translation.startswith('['):  # Skip error messages
                    if hasattr(self, 'assistance_widget') and self.assistance_widget:
                        # Check if this segment is still selected
                        current_row = self.table.currentRow()
                        if current_row == row_index:
                            # Map provider to code
                            provider_code_map = {
                                'Google Translate': 'GT',
                                'DeepL': 'DL',
                                'Microsoft Translator': 'MS',
                                'Amazon Translate': 'AT',
                                'ModernMT': 'MMT',
                                'MyMemory': 'MM'
                            }
                            provider_code = provider_code_map.get(provider_name, 'MT')
                            
                            # Create MT match
                            mt_match = TranslationMatch(
                                source=source_text,
                                target=translation,
                                relevance=95,  # High relevance for MT
                                metadata={
                                    'provider': provider_name,
                                    'timestamp': datetime.now().isoformat()
                                },
                                match_type='MT',
                                compare_source=source_text,
                                provider_code=provider_code
                            )
                            
                            # Update all results panels with MT match
                            if hasattr(self, 'results_panels'):
                                current_matches = {"MT": [mt_match]}
                                for panel in self.results_panels:
                                    try:
                                        # Get current matches and add MT
                                        if hasattr(panel, 'matches_by_type'):
                                            existing_matches = panel.matches_by_type.copy()
                                            existing_matches["MT"] = [mt_match]
                                            panel.set_matches(existing_matches)
                                        else:
                                            panel.set_matches(current_matches)
                                    except Exception as e:
                                        self.log(f"Error updating panel with MT: {e}")
                            self.log(f"✓ MT translation ({provider_name}) added to results pane")
            except Exception as e:
                # Silently fail - don't interrupt workflow
                self.log(f"MT translation failed (silent): {str(e)}")
        
        # Use QTimer to run async (small delay to avoid blocking, slightly after LLM)
        QTimer.singleShot(150, fetch_and_update)

    def call_google_translate(self, text: str, source_lang: str, target_lang: str, api_key: str = None) -> str:
        """Call Google Cloud Translation API using REST API"""
        try:
            import requests
            import html
            
            if not api_key:
                api_keys = self.load_api_keys()
                api_key = api_keys.get("google_translate")
            
            if not api_key:
                return f"[{self._api_key_problem('google_translate') or 'Google Cloud Translation requires API key'}]"
            
            # Map full language names to ISO codes
            lang_name_to_code = {
                'english': 'en', 'dutch': 'nl', 'german': 'de', 'french': 'fr',
                'spanish': 'es', 'italian': 'it', 'portuguese': 'pt', 'russian': 'ru',
                'chinese': 'zh', 'japanese': 'ja', 'korean': 'ko', 'arabic': 'ar',
                'polish': 'pl', 'swedish': 'sv', 'norwegian': 'no', 'danish': 'da',
                'finnish': 'fi', 'greek': 'el', 'turkish': 'tr', 'czech': 'cs',
                'hungarian': 'hu', 'romanian': 'ro', 'bulgarian': 'bg', 'ukrainian': 'uk',
                'hebrew': 'he', 'thai': 'th', 'vietnamese': 'vi', 'indonesian': 'id',
                'malay': 'ms', 'hindi': 'hi', 'bengali': 'bn', 'tamil': 'ta',
            }
            
            # Convert language - try name mapping first, then code extraction
            src_lower = source_lang.lower().strip()
            tgt_lower = target_lang.lower().strip()
            
            src_code = lang_name_to_code.get(src_lower, src_lower.split('-')[0].split('_')[0])
            tgt_code = lang_name_to_code.get(tgt_lower, tgt_lower.split('-')[0].split('_')[0])
            
            # Call REST API directly
            url = "https://translation.googleapis.com/language/translate/v2"
            params = {
                'key': api_key,
                'q': text,
                'source': src_code,
                'target': tgt_code,
                'format': 'text'
            }
            
            response = requests.post(url, params=params, timeout=10, proxies=self._get_proxy_dict())
            response.raise_for_status()

            result = response.json()
            translated_text = result['data']['translations'][0]['translatedText']
            # Unescape HTML entities like &quot; &amp; etc.
            return html.unescape(translated_text)

        except ImportError:
            return "[Google Translate requires: pip install requests]"
        except Exception as e:
            return f"[Google Translate error: {str(e)}]"

    def call_deepl(self, text: str, source_lang: str, target_lang: str, api_key: str = None) -> str:
        """Call DeepL API"""
        try:
            import deepl

            if not api_key:
                api_keys = self.load_api_keys()
                api_key = api_keys.get("deepl")

            if not api_key:
                return f"[{self._api_key_problem('deepl') or 'DeepL requires API key'}]"

            translator = deepl.Translator(api_key, proxy=self._get_proxy_dict())

            # Map full language names to ISO codes
            lang_name_to_code = {
                'english': 'en', 'dutch': 'nl', 'german': 'de', 'french': 'fr',
                'spanish': 'es', 'italian': 'it', 'portuguese': 'pt', 'russian': 'ru',
                'chinese': 'zh', 'japanese': 'ja', 'korean': 'ko', 'arabic': 'ar',
                'polish': 'pl', 'swedish': 'sv', 'norwegian': 'no', 'danish': 'da',
                'finnish': 'fi', 'greek': 'el', 'turkish': 'tr', 'czech': 'cs',
                'hungarian': 'hu', 'romanian': 'ro', 'bulgarian': 'bg', 'ukrainian': 'uk',
            }

            # Convert source language - try name mapping first, then code extraction
            src_lower = source_lang.lower().strip()
            src_base = lang_name_to_code.get(src_lower, src_lower.split('-')[0].split('_')[0])
            src_code = src_base.upper()

            # Convert target language - try name mapping first, then handle DeepL variants
            tgt_lower = target_lang.lower().strip()
            tgt_base = lang_name_to_code.get(tgt_lower, tgt_lower)
            tgt_upper = tgt_base.upper().replace('_', '-')

            # DeepL target language mapping - some require specific variants
            deepl_target_map = {
                # English variants (EN alone is deprecated)
                'EN': 'EN-US',      # Default to US English
                'EN-US': 'EN-US',
                'EN-GB': 'EN-GB',
                'EN-AU': 'EN-GB',   # Map Australian to British
                'EN-CA': 'EN-US',   # Map Canadian to US
                # Portuguese variants
                'PT': 'PT-PT',      # Default to European Portuguese
                'PT-PT': 'PT-PT',
                'PT-BR': 'PT-BR',
                # Chinese variants
                'ZH': 'ZH-HANS',    # Default to Simplified
                'ZH-CN': 'ZH-HANS',
                'ZH-TW': 'ZH-HANT',
                'ZH-HANS': 'ZH-HANS',
                'ZH-HANT': 'ZH-HANT',
            }

            # Check if full code matches first, then base code
            if tgt_upper in deepl_target_map:
                tgt_code = deepl_target_map[tgt_upper]
            else:
                # Extract base code and check
                base_code = tgt_upper.split('-')[0]
                if base_code in deepl_target_map:
                    tgt_code = deepl_target_map[base_code]
                else:
                    # Use base code as-is for other languages
                    tgt_code = base_code

            result = translator.translate_text(text, source_lang=src_code, target_lang=tgt_code)
            return result.text

        except ImportError:
            return "[DeepL requires: pip install deepl]"
        except Exception as e:
            return f"[DeepL error: {str(e)}]"

    def call_microsoft_translate(self, text: str, source_lang: str, target_lang: str, api_key: str = None, region: str = None) -> str:
        """Call Microsoft Azure Translator API"""
        try:
            import requests

            api_keys = self.load_api_keys()
            if not api_key:
                api_key = api_keys.get("microsoft_translate") or api_keys.get("azure_translate")
            if not region:
                region = api_keys.get("microsoft_translate_region") or api_keys.get("azure_region") or "global"

            if not api_key:
                return f"[{self._api_key_problem('microsoft_translate') or 'Microsoft Translator requires API key'}]"

            # Map full language names to ISO codes
            lang_name_to_code = {
                'english': 'en', 'dutch': 'nl', 'german': 'de', 'french': 'fr',
                'spanish': 'es', 'italian': 'it', 'portuguese': 'pt', 'russian': 'ru',
                'chinese': 'zh', 'japanese': 'ja', 'korean': 'ko', 'arabic': 'ar',
                'polish': 'pl', 'swedish': 'sv', 'norwegian': 'no', 'danish': 'da',
                'finnish': 'fi', 'greek': 'el', 'turkish': 'tr', 'czech': 'cs',
                'hungarian': 'hu', 'romanian': 'ro', 'bulgarian': 'bg', 'ukrainian': 'uk',
                'hebrew': 'he', 'thai': 'th', 'vietnamese': 'vi', 'indonesian': 'id',
            }

            # Convert language - try name mapping first, then code extraction
            src_lower = source_lang.lower().strip()
            tgt_lower = target_lang.lower().strip()

            src_code = lang_name_to_code.get(src_lower, src_lower.split('-')[0].split('_')[0])
            tgt_code = lang_name_to_code.get(tgt_lower, tgt_lower.split('-')[0].split('_')[0])
            
            # Microsoft Translator API v3.0
            endpoint = f"https://api.cognitive.microsofttranslator.com/translate"
            params = {
                'api-version': '3.0',
                'from': src_code,
                'to': tgt_code
            }
            headers = {
                'Ocp-Apim-Subscription-Key': api_key,
                'Ocp-Apim-Subscription-Region': region,
                'Content-Type': 'application/json'
            }
            body = [{'text': text}]
            
            response = requests.post(endpoint, params=params, headers=headers, json=body, timeout=10, proxies=self._get_proxy_dict())
            response.raise_for_status()

            result = response.json()
            return result[0]['translations'][0]['text']

        except ImportError:
            return "[Microsoft Translator requires: pip install requests]"
        except Exception as e:
            return f"[Microsoft Translator error: {str(e)}]"

    def call_amazon_translate(self, text: str, source_lang: str, target_lang: str, api_key: str = None, region: str = 'us-east-1') -> str:
        """Call Amazon Translate API (AWS)"""
        try:
            import boto3
            from botocore.exceptions import ClientError
            
            api_keys = self.load_api_keys()
            if not api_key:
                api_key = api_keys.get("amazon_translate") or api_keys.get("aws_translate")
            
            # AWS also needs secret key
            secret_key = api_keys.get("amazon_translate_secret") or api_keys.get("aws_secret_key")
            
            if not api_key or not secret_key:
                return "[Amazon Translate requires API key and secret key]"
            
            # Get region from settings if not provided
            if not region or region == 'us-east-1':
                region = api_keys.get("amazon_translate_region", "us-east-1")
            
            # Map full language names to ISO codes
            lang_name_to_code = {
                'english': 'en', 'dutch': 'nl', 'german': 'de', 'french': 'fr',
                'spanish': 'es', 'italian': 'it', 'portuguese': 'pt', 'russian': 'ru',
                'chinese': 'zh', 'japanese': 'ja', 'korean': 'ko', 'arabic': 'ar',
                'polish': 'pl', 'swedish': 'sv', 'norwegian': 'no', 'danish': 'da',
                'finnish': 'fi', 'greek': 'el', 'turkish': 'tr', 'czech': 'cs',
                'hungarian': 'hu', 'romanian': 'ro', 'bulgarian': 'bg', 'ukrainian': 'uk',
            }
            
            # Convert language - try name mapping first, then code extraction
            src_lower = source_lang.lower().strip()
            tgt_lower = target_lang.lower().strip()
            
            src_code = lang_name_to_code.get(src_lower, src_lower.split('-')[0].split('_')[0])
            tgt_code = lang_name_to_code.get(tgt_lower, tgt_lower.split('-')[0].split('_')[0])
            
            translate_client = boto3.client(
                'translate',
                aws_access_key_id=api_key,
                aws_secret_access_key=secret_key,
                region_name=region
            )
            
            result = translate_client.translate_text(
                Text=text,
                SourceLanguageCode=src_code,
                TargetLanguageCode=tgt_code
            )
            
            return result['TranslatedText']
            
        except ImportError:
            return "[Amazon Translate requires: pip install boto3]"
        except Exception as e:
            return f"[Amazon Translate error: {str(e)}]"

    def load_api_keys(self) -> Dict[str, str]:
        """Every API key Workbench uses: the keys shared with Supervertaler for
        Trados, memoQ and Sidekick, then Workbench's own settings - through the
        one reader in modules/llm_clients.py."""
        from modules.llm_clients import load_api_keys
        return load_api_keys(str(self._get_unified_settings_path()))

    def _api_key_problem(self, name: str):
        """Why an action has no key to use: "can't be read on this computer" (M2)
        or "No API key set for …"; None when the key is there."""
        from modules.llm_clients import api_key_problem
        return api_key_problem(name, settings_path=str(self._get_unified_settings_path()))

    def _prepare_api_keys(self):
        """Once per start, before any key is read (step 1 of "API keys locked to
        the computer"): Workbench's own keys join the shared ones - at its first
        such start only - and the shared keys are locked to this computer.
        Never stops Workbench starting."""
        try:
            from modules.llm_clients import prepare_api_keys
            general = self._load_settings_section("general")
            state = dict(general.get("api_key_lock") or {})
            llm = self.load_llm_settings()
            prepared = prepare_api_keys(str(self._get_unified_settings_path()),
                                        join=not state.get("joined"),
                                        profiles=llm.get('custom_openai_profiles', []),
                                        mt_profiles=llm.get('custom_mt_profiles', []))
            notices = list(prepared.notices)
            if prepared.joined:
                state["joined"] = datetime.now().strftime("%Y-%m-%d")
            if prepared.keychain_missing:
                print(f"[API keys] {prepared.keychain_missing}")
                if not state.get("keychain_missing_said"):
                    state["keychain_missing_said"] = True
                    notices.append(prepared.keychain_missing)
            if state != (general.get("api_key_lock") or {}):
                general["api_key_lock"] = state
                self._save_settings_section("general", general)
            if notices:
                # Once, in the status line and the log, when the window is up.
                from PyQt6.QtCore import QTimer
                QTimer.singleShot(3000, lambda: [self.log(n) for n in notices])
        except Exception as e:
            print(f"[API keys] Start-up step skipped: {type(e).__name__}: {e}")

    def save_api_keys(self, api_keys: Dict[str, str]):
        """Save API keys to unified settings"""
        self._save_settings_section("api_keys", api_keys)

    def _schedule_mt_and_llm_matches(self, segment, termbase_matches=None):
        """Schedule MT and LLM matches with debouncing - only call APIs when user stops clicking"""
        try:
            # Cancel any previous MT/LLM requests
            if hasattr(self, '_mt_llm_timer'):
                self._mt_llm_timer.stop()
            
            # Store segment and termbase matches for delayed lookup
            self._pending_mt_llm_segment = segment
            self._pending_termbase_matches = termbase_matches or []
            
            # Start debounced timer - only call APIs after user stops navigating
            from PyQt6.QtCore import QTimer
            self._mt_llm_timer = QTimer()
            self._mt_llm_timer.setSingleShot(True)
            self._mt_llm_timer.timeout.connect(lambda: self._execute_mt_llm_lookup())
            self._mt_llm_timer.start(150)  # Wait 150ms of inactivity before external API calls
                
        except Exception as e:
            self.log(f"Error scheduling MT/LLM search: {e}")

    def _execute_mt_llm_lookup(self):
        """Execute the actual MT/LLM lookup after debounce delay"""
        try:
            if not hasattr(self, '_pending_mt_llm_segment'):
                return
                
            segment = self._pending_mt_llm_segment

            # Call the actual lookup method (now includes TM, MT, and LLM)
            with self._pcs("_search_mt_and_llm_matches (TM+MT+LLM lookup)"):
                self._search_mt_and_llm_matches(segment)
                
        except Exception as e:
            self.log(f"Error executing MT/LLM lookup: {e}")
            import traceback
            self.log(f"Delayed lookup error traceback: {traceback.format_exc()}")

    def _search_mt_and_llm_matches(self, segment):
        """Search for TM, MT and LLM matches - called only after debounce delay"""
        try:
            from modules.translation_results_panel import TranslationMatch

            # v1.9.182: Validate we're still on the same segment before displaying results
            # This prevents stale results from showing when user navigates quickly
            current_row = self.table.currentRow() if hasattr(self, 'table') and self.table else -1
            if current_row >= 0:
                id_item = self.table.item(current_row, 0)
                if id_item:
                    try:
                        current_segment_id = int(id_item.text())
                        if current_segment_id != segment.id:
                            # User has moved to a different segment - abort this lookup
                            return
                    except (ValueError, AttributeError):
                        pass

            # Get current project languages for all translation services
            source_lang = getattr(self.current_project, 'source_lang', None) if self.current_project else None
            target_lang = getattr(self.current_project, 'target_lang', None) if self.current_project else None
            
            # Convert language names to codes if needed
            source_lang_code = None
            target_lang_code = None
            if source_lang:
                source_lang_code = self._convert_language_to_code(source_lang)
            if target_lang:
                target_lang_code = self._convert_language_to_code(target_lang)
            
            # Initialize Compare Panel data
            self._compare_panel_tm_source = ''
            self._compare_panel_tm_target = ''
            self._compare_panel_tm_percent = 0
            self._compare_panel_mt = ''
            self._compare_panel_tm_matches = []  # Reset TM matches list for navigation
            self._compare_panel_mt_matches = []  # Reset MT matches list for navigation
            
            # Re-assert the exact (100%) TM match for the new segment instead of
            # blanking the panel — otherwise the instant match shown on selection
            # flickers away here while fuzzy loads. Only clear when there is no
            # exact match (fuzzy then streams in via the worker below).
            if not self._show_instant_tm_match(segment):
                self.set_compare_panel_matches(segment.id, segment.source, [], [])
            
            # Prepare matches dict - use the stored termbase matches from immediate display
            matches_dict = {
                "LLM": [],
                "NT": [],
                "MT": [],
                "TM": [],
                "Termbases": getattr(self, '_pending_termbase_matches', [])
            }
            
            # 🔥 THREADED TM SEARCH: Run expensive fuzzy matching off the main thread
            if self.enable_tm_matching and hasattr(self, 'tm_database') and self.tm_database:
                try:
                    # Get activated TM IDs for current project
                    tm_ids = None
                    project_id = None
                    if hasattr(self, 'tm_metadata_mgr') and self.tm_metadata_mgr and self.current_project:
                        project_id = self.current_project.id if hasattr(self.current_project, 'id') else None
                        if project_id:
                            tm_ids = self.tm_metadata_mgr.get_active_tm_ids(project_id)

                    # Skip TM search if no TMs are activated.
                    # Say so once per project rather than returning an empty
                    # pane in silence: importing a document deliberately clears
                    # every TM's Read tickbox, and "my TM is loaded but nothing
                    # ever matches" is the commonest support question there is.
                    if tm_ids is not None and isinstance(tm_ids, list) and len(tm_ids) == 0:
                        if getattr(self, '_warned_no_active_tm_for_project', None) != project_id:
                            self._warned_no_active_tm_for_project = project_id
                            self.log(
                                "ℹ️ No TM is switched on for this project, so no TM "
                                "matches can be shown. Tick 'Read' for the TM you want on "
                                "the 💾 TMs tab. (A new project starts with every TM off.)"
                            )
                    else:
                        # Strip outer structural tags if setting is enabled (for cleaner TM matching)
                        search_source = segment.source
                        if self.hide_outer_wrapping_tags:
                            search_source, _ = strip_outer_wrapping_tags(search_source)

                        # Cancel any previous TM search worker
                        if hasattr(self, '_tm_search_worker') and self._tm_search_worker is not None:
                            self._tm_search_worker.cancel()
                            self._tm_search_worker.wait(100)  # Brief wait, don't block UI

                        # Launch TM search in background thread
                        self._tm_search_worker = TMSearchWorker(
                            db_path=self.tm_database.db.db_path,
                            source_text=search_source,
                            segment_id=segment.id,
                            tm_ids=tm_ids,
                            source_lang=self.tm_database.source_lang,
                            target_lang=self.tm_database.target_lang,
                            fuzzy_threshold=self.tm_database.fuzzy_threshold,
                            max_matches=10,
                            tm_metadata=self.tm_database.tm_metadata
                        )
                        # Store segment reference for result handler
                        self._tm_search_pending_segment = segment
                        self._tm_search_worker.results_ready.connect(self._on_tm_search_results)
                        self._tm_search_worker.search_failed.connect(self._on_tm_search_failed)
                        self._tm_search_worker.start()
                except Exception as e:
                    self.log(f"Error launching TM search worker: {e}")
            
            # Add MT and LLM matches progressively
            self._add_mt_and_llm_matches_progressive(segment, source_lang, target_lang, source_lang_code, target_lang_code)
                
        except Exception as e:
            self.log(f"Error in MT/LLM search: {e}")

    def _add_mt_and_llm_matches_progressive(self, segment, source_lang, target_lang, source_lang_code, target_lang_code):
        """DEPRECATED: Translation Results panel has been removed.

        MT/LLM translations are now only available via QuickTrans
        (Ctrl+Alt+Q global hotkey). This function is kept as a stub
        for backwards compatibility.
        """
        pass  # Translation Results panel removed - use QuickTrans (Ctrl+Alt+Q) for MT/LLM

    def _add_mt_and_llm_matches(self, segment, matches_dict, source_lang, target_lang, source_lang_code, target_lang_code):
        """Add MT and LLM matches to the matches dictionary"""
        from modules.translation_results_panel import TranslationMatch
        
        # 🤖 DIRECT MT SEARCH: Machine Translation matches
        if self.enable_mt_matching:
            try:
                self.log(f"🤖 DIRECT MT SEARCH: Getting machine translation for '{segment.source[:50]}...'")
                
                # Use Google Translate via the wrapper function
                from modules.llm_clients import get_google_translation
                
                self.log(f"🤖 DIRECT MT SEARCH: Calling get_google_translation with source_lang='{source_lang_code or 'auto'}', target_lang='{target_lang_code or 'en'}'")
                
                google_result = get_google_translation(
                    segment.source, 
                    source_lang_code or 'auto',  # Auto-detect if no source lang 
                    target_lang_code or 'en'     # Default to English if no target lang
                )
                
                self.log(f"🤖 DIRECT MT SEARCH: Google Translate result: {google_result}")
                
                if google_result and google_result.get('translation'):
                    match = TranslationMatch(
                        source=segment.source,
                        target=google_result['translation'],
                        relevance=95,  # High confidence for MT
                        metadata=google_result.get('metadata', {}),
                        match_type="MT",
                        compare_source=segment.source,
                        provider_code='GT'
                    )
                    matches_dict["MT"].append(match)
                    self.log(f"✓ Added Google Translate match: {google_result['translation'][:50]}...")
                elif google_result and google_result.get('error'):
                    self.log(f"⚠ Google Translate error: {google_result.get('error')}")
                else:
                    self.log(f"⚠ Google Translate returned no translation")
                    
            except Exception as e:
                self.log(f"⚠ Error getting Google Translate: {e}")
                import traceback
                self.log(f"⚠ Traceback: {traceback.format_exc()}")
        
        # 🧠 DIRECT LLM SEARCH: Large Language Model matches  
        if self.enable_llm_matching:
            try:
                self.log(f"🧠 DIRECT LLM SEARCH: Getting LLM translations for '{segment.source[:50]}...'")
                self.log(f"🧠 DIRECT LLM SEARCH: LLM matching is enabled: {self.enable_llm_matching}")
                
                # Import LLM wrapper functions
                from modules.llm_clients import get_openai_translation, get_claude_translation
                self.log("🧠 DIRECT LLM SEARCH: Successfully imported LLM wrapper functions")
                
                # Use existing LLM infrastructure from Prompt Manager
                api_keys = self.load_api_keys()
                self.log(f"🧠 DIRECT LLM SEARCH: Loaded API keys: {list(api_keys.keys()) if api_keys else 'None'}")
                if not api_keys:
                    self.log("⚠ No API keys found for LLM translation")
                    return
                
                settings = self.load_llm_settings()
                
                # Check if we should use prompts for grid translations (configurable)
                use_prompts_in_grid = settings.get('use_prompts_in_grid', False)  # Default: simple translations
                self.log(f"🧠 LLM Grid Translations - Use Prompts: {use_prompts_in_grid}")
                
                # Get OpenAI translation using existing infrastructure
                if 'openai' in api_keys:
                    try:
                        self.log("🧠 DIRECT LLM SEARCH: Attempting OpenAI translation...")
                        from modules.llm_clients import LLMClient
                        self.log("🧠 DIRECT LLM SEARCH: LLMClient imported for OpenAI")
                        
                        openai_model = settings.get('openai_model', 'gpt-6.1-sol')
                        self.log(f"🧠 DIRECT LLM SEARCH: Using OpenAI model: {openai_model}")
                        client = LLMClient(
                            api_key=api_keys['openai'],
                            provider='openai',
                            model=openai_model,
                            http_proxy=self._get_proxy_url()
                        )
                        self.log("🧠 DIRECT LLM SEARCH: LLMClient initialized for OpenAI")
                        
                        translation = client.translate(
                            text=segment.source,
                            source_lang=source_lang_code or 'nl',
                            target_lang=target_lang_code or 'en'
                        )
                        self.log(f"🧠 DIRECT LLM SEARCH: OpenAI translation result: {translation[:100] if translation else 'None'}...")
                        
                        if translation and translation.strip():
                            match = TranslationMatch(
                                source=segment.source,
                                target=translation.strip(),
                                relevance=88,  # Good confidence for LLM
                                metadata={'model': openai_model, 'provider': 'OpenAI'},
                                match_type="LLM",
                                compare_source=segment.source,
                                provider_code='OA'
                            )
                            matches_dict["LLM"].append(match)
                            self.log(f"✓ Added OpenAI match: {translation[:50]}...")
                        else:
                            self.log("⚠ OpenAI translation was empty or None")
                            
                    except Exception as e:
                        self.log(f"⚠ Error getting OpenAI translation: {e}")
                        import traceback
                        self.log(f"⚠ OpenAI error traceback: {traceback.format_exc()}")
                
                # Get Claude translation using existing infrastructure
                if 'claude' in api_keys:
                    try:
                        from modules.llm_clients import LLMClient
                        
                        claude_model = settings.get('claude_model', 'claude-sonnet-5-5')
                        client = LLMClient(
                            api_key=api_keys['claude'],
                            provider='claude',
                            model=claude_model,
                            http_proxy=self._get_proxy_url()
                        )
                        
                        translation = client.translate(
                            text=segment.source,
                            source_lang=source_lang_code or 'nl',
                            target_lang=target_lang_code or 'en'
                        )
                        
                        if translation and translation.strip():
                            match = TranslationMatch(
                                source=segment.source,
                                target=translation.strip(),
                                relevance=90,  # High confidence for Claude
                                metadata={'model': claude_model, 'provider': 'Claude'},
                                match_type="LLM",
                                compare_source=segment.source,
                                provider_code='CL'
                            )
                            matches_dict["LLM"].append(match)
                            self.log(f"✓ Added Claude match: {translation[:50]}...")
                            
                    except Exception as e:
                        self.log(f"⚠ Error getting Claude translation: {e}")
                
                # Get Gemini translation using existing infrastructure
                # Check for both 'gemini' and 'google' keys for backward compatibility
                gemini_api_key = api_keys.get('gemini') or api_keys.get('google')
                if gemini_api_key:
                    try:
                        self.log("🧠 DIRECT LLM SEARCH: Attempting Gemini translation...")
                        from modules.llm_clients import LLMClient
                        self.log("🧠 DIRECT LLM SEARCH: LLMClient imported for Gemini")
                        
                        gemini_model = settings.get('gemini_model', 'gemini-3.8-flash')
                        self.log(f"🧠 DIRECT LLM SEARCH: Using Gemini model: {gemini_model}")
                        client = LLMClient(
                            api_key=gemini_api_key,
                            provider='gemini',
                            model=gemini_model
                        )
                        self.log("🧠 DIRECT LLM SEARCH: LLMClient initialized for Gemini")
                        
                        translation = client.translate(
                            text=segment.source,
                            source_lang=source_lang_code or 'nl',
                            target_lang=target_lang_code or 'en'
                        )
                        
                        if translation and translation.strip():
                            match = TranslationMatch(
                                source=segment.source,
                                target=translation.strip(),
                                relevance=85,  # Good confidence for Gemini
                                metadata={'model': gemini_model, 'provider': 'Gemini'},
                                match_type="LLM",
                                compare_source=segment.source,
                                provider_code='GM'
                            )
                            matches_dict["LLM"].append(match)
                            self.log(f"✓ Added Gemini match: {translation[:50]}...")
                            
                    except Exception as e:
                        self.log(f"⚠ Error getting Gemini translation: {e}")
                    
            except Exception as e:
                self.log(f"⚠ Error in LLM translation process: {e}")

    def _clean_provider_prefix(self, translation):
        """Remove provider prefixes like [OpenAI], [Claude], etc. from translations"""
        import re
        # Remove patterns like [OpenAI], [Claude], [ChatGPT], [Anthropic] at the start
        cleaned = re.sub(r'^\[(?:OpenAI|Claude|ChatGPT|Anthropic|GPT-\d+)\]\s*', '', translation.strip())
        return cleaned
