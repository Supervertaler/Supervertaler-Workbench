"""
Chat Backend for Supervertaler
================================

Shared conversation state and LLM communication layer.
Multiple ChatViewWidget instances connect to one ChatBackend
and stay in sync via Qt signals.
"""

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Callable

from PyQt6.QtCore import QObject, pyqtSignal

from modules.llm_clients import LLMClient, load_api_keys
from modules.llm_pricing import estimate_cost
from modules import usage_log


class ChatBackend(QObject):
    """
    Owns chat_history, handles LLM calls, emits signals for all views.

    Signals:
        message_added(dict): Emitted when a message is added.
            Dict keys: role, content, timestamp, metadata (optional)
        chat_cleared(): Emitted when history is cleared.
        thinking_started(): Emitted when an AI request begins.
        thinking_finished(): Emitted when an AI request completes (success or error).
    """

    message_added = pyqtSignal(dict)
    chat_cleared = pyqtSignal()
    thinking_started = pyqtSignal()
    thinking_finished = pyqtSignal()

    def __init__(self, parent_app, conversation_file: Path,
                 log_callback: Optional[Callable] = None, parent=None):
        super().__init__(parent)
        self._parent_app = parent_app
        self._conversation_file = conversation_file
        self._log = log_callback or (lambda msg: print(msg))

        self.chat_history: List[Dict] = []
        self.llm_client: Optional[LLMClient] = None

        self.init_llm_client()
        self._load_history()

    # ------------------------------------------------------------------
    # State management
    # ------------------------------------------------------------------

    def add_message(self, role: str, content: str,
                    metadata: Optional[Dict] = None,
                    save: bool = True) -> int:
        """
        Add a message, emit message_added, optionally persist.
        Returns the index of the new message.
        """
        msg = {
            'role': role,
            'content': content,
            'timestamp': datetime.now().isoformat(),
        }
        if metadata:
            msg['metadata'] = metadata

        self.chat_history.append(msg)
        if save:
            self._save_history()

        self.message_added.emit(msg)
        return len(self.chat_history) - 1

    def clear_history(self) -> None:
        """Clear all history, emit chat_cleared, persist."""
        self.chat_history.clear()
        self._save_history()
        self.chat_cleared.emit()

    def get_history(self) -> List[Dict]:
        """Return a copy of the chat history."""
        return list(self.chat_history)

    def get_recent_history(self, n: int = 10) -> List[Dict]:
        """Return the last n messages."""
        return self.chat_history[-n:]

    # ------------------------------------------------------------------
    # LLM client management
    # ------------------------------------------------------------------

    def init_llm_client(self) -> None:
        """(Re)initialise the LLM client from parent app settings."""
        try:
            if hasattr(self._parent_app, 'load_api_keys'):
                api_keys = self._parent_app.load_api_keys()
            else:
                api_keys = load_api_keys()

            provider = None
            model = None

            if hasattr(self._parent_app, 'current_provider'):
                provider = self._parent_app.current_provider
                if hasattr(self._parent_app, 'current_model'):
                    model = self._parent_app.current_model

            if not provider:
                if api_keys.get("openai"):
                    provider = "openai"
                elif api_keys.get("claude"):
                    provider = "claude"
                elif api_keys.get("google") or api_keys.get("gemini"):
                    provider = "gemini"

            if provider:
                key_name = "google" if provider == "gemini" else provider
                api_key = (api_keys.get(key_name) or api_keys.get("gemini")
                           or api_keys.get("openai") or api_keys.get("claude")
                           or api_keys.get("google"))

                # custom_openai / ollama don't require a key in api_keys.txt:
                # the key (if any) lives on the active custom profile, and the
                # factory supplies a 'not-needed' placeholder otherwise.
                keyless_ok = provider in ('custom_openai', 'ollama')

                # Prefer the main app's factory: it resolves base_url, the active
                # custom-endpoint profile (endpoint + per-profile model + key),
                # OpenRouter's base_url, and proxy handling. The old hand-rolled
                # LLMClient here never passed base_url, so custom_openai endpoints
                # silently failed to construct and the chat showed "No model"
                # even though QuickTrans (which uses this factory) worked.
                if hasattr(self._parent_app, 'create_llm_client') and (api_key or keyless_ok):
                    settings = (self._parent_app.load_llm_settings()
                                if hasattr(self._parent_app, 'load_llm_settings') else None)
                    self.llm_client = self._parent_app.create_llm_client(
                        provider, model, api_keys, settings=settings)
                    resolved = getattr(self.llm_client, 'model', model) or 'default'
                    self._log(f"[ChatBackend] LLM client: {provider}/{resolved}")
                elif api_key:
                    # Legacy direct path (factory unavailable). Standard providers
                    # only – custom_openai needs the factory for its base_url.
                    http_proxy = None
                    if provider != 'gemini' and hasattr(self._parent_app, '_get_proxy_url'):
                        http_proxy = self._parent_app._get_proxy_url()
                    self.llm_client = LLMClient(
                        api_key=api_key,
                        provider=provider,
                        model=model,
                        max_tokens=16384,
                        http_proxy=http_proxy
                    )
                    self._log(f"[ChatBackend] LLM client: {provider}/{model or 'default'}")
                else:
                    self._log("[ChatBackend] No API keys found")
            else:
                self._log("[ChatBackend] No LLM provider configured")

        except Exception as e:
            self._log(f"[ChatBackend] Failed to init LLM client: {e}")

    def refresh_llm_client(self) -> None:
        """Public alias for init_llm_client."""
        self.init_llm_client()

    def get_model_display_name(self) -> str:
        """Return a human-readable model name, e.g. 'claude / claude-sonnet-4-6'."""
        if self.llm_client:
            return f"{self.llm_client.provider} / {self.llm_client.model}"
        return "No model"

    # ------------------------------------------------------------------
    # AI requests
    # ------------------------------------------------------------------

    def send_ai_request(self, prompt: str, system_prompt: str,
                        is_analysis: bool = False,
                        images: Optional[List] = None) -> tuple:
        """
        Send a prompt to the LLM and return (response_text, metadata_dict).

        metadata_dict: {model, provider, tokens_in, tokens_out, cost_usd, duration_s}
        Returns ("", {}) on failure (caller should handle errors).
        """
        self.refresh_llm_client()

        if not self.llm_client:
            return "", {}

        self._log(f"[ChatBackend] Sending to {self.llm_client.provider}/{self.llm_client.model}")
        self.thinking_started.emit()

        try:
            start = time.time()
            # Tag these calls as "Chat" in the usage ledger (the default task is
            # "Translate"). is_analysis covers the chat panel's analysis actions,
            # which are still chat-panel AI usage.
            with usage_log.UsageContext(task="Chat"):
                response_text, usage = self.llm_client.translate_with_usage(
                    text="",
                    source_lang="en",
                    target_lang="en",
                    custom_prompt=prompt,
                    system_prompt=system_prompt,
                    skip_cleaning=is_analysis,
                    images=images,
                )
            elapsed = time.time() - start

            tokens_in = usage.get('input_tokens', 0)
            tokens_out = usage.get('output_tokens', 0)
            cost = estimate_cost(
                self.llm_client.provider,
                self.llm_client.model,
                tokens_in,
                tokens_out
            )

            metadata = {
                'model': self.llm_client.model,
                'provider': self.llm_client.provider,
                'tokens_in': tokens_in,
                'tokens_out': tokens_out,
                'cost_usd': cost,
                'duration_s': round(elapsed, 1),
            }

            # cost is None for a model with no pricing entry (e.g. a local
            # KoboldCPP/LM Studio model behind the custom OpenAI provider),
            # so it must not be passed to a float format spec.
            cost_text = "cost unknown" if cost is None else f"~${cost:.4f}"
            self._log(
                f"[ChatBackend] Response: {len(response_text)} chars, "
                f"{tokens_in} in / {tokens_out} out, "
                f"{cost_text}, {elapsed:.1f}s"
            )

            return response_text, metadata

        except Exception as e:
            self._log(f"[ChatBackend] Error: {e}")
            import traceback
            traceback.print_exc()
            raise
        finally:
            self.thinking_finished.emit()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _save_history(self) -> None:
        """Persist chat_history to JSON."""
        try:
            self._conversation_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self._conversation_file, 'w', encoding='utf-8') as f:
                json.dump({
                    'history': self.chat_history,
                    'updated': datetime.now().isoformat()
                }, f, indent=2)
        except Exception as e:
            self._log(f"[ChatBackend] Failed to save history: {e}")

    def _load_history(self) -> None:
        """Load chat_history from JSON."""
        try:
            if self._conversation_file.exists():
                with open(self._conversation_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self.chat_history = data.get('history', [])
                    self._log(f"[ChatBackend] Loaded {len(self.chat_history)} messages")
        except Exception as e:
            self._log(f"[ChatBackend] Failed to load history: {e}")
