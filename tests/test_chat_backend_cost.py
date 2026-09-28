"""Guard the chat panel against models with no known price (issue #250).

``estimate_cost`` deliberately returns ``None`` for a model that has no entry in
the pricing table, so the UI can say "cost unknown" rather than wrongly implying
"free". A local model served through the custom OpenAI-compatible provider
(KoboldCPP, LM Studio, llama.cpp …) is exactly that case. ChatBackend used to
feed that ``None`` straight into a ``:.4f`` format spec, so every chat message
to such a model failed with "unsupported format string passed to
NoneType.__format__" – even though translation with the same model worked.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

pytest.importorskip("PyQt6")

from modules.chat_backend import ChatBackend  # noqa: E402


class _FakeClient:
    def __init__(self, provider: str, model: str):
        self.provider = provider
        self.model = model

    def translate_with_usage(self, **kwargs):
        return "Hello back", {"input_tokens": 12, "output_tokens": 3}


def _backend(monkeypatch, tmp_path, client):
    monkeypatch.setattr(ChatBackend, "init_llm_client", lambda self: None)
    logs = []
    backend = ChatBackend(None, tmp_path / "chat.json", log_callback=logs.append)
    backend.llm_client = client
    return backend, logs


def test_unpriced_local_model_does_not_crash(monkeypatch, tmp_path):
    client = _FakeClient("custom_openai", "koboldcpp/some-local-model")
    backend, logs = _backend(monkeypatch, tmp_path, client)

    text, metadata = backend.send_ai_request("hi", "system")

    assert text == "Hello back"
    assert metadata["cost_usd"] is None
    assert any("cost unknown" in line for line in logs)


def test_free_provider_still_logs_zero_cost(monkeypatch, tmp_path):
    client = _FakeClient("ollama", "llama3")
    backend, logs = _backend(monkeypatch, tmp_path, client)

    _, metadata = backend.send_ai_request("hi", "system")

    assert metadata["cost_usd"] == 0.0
    assert any("~$0.0000" in line for line in logs)
