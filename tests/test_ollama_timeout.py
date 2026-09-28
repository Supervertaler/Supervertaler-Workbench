"""User-configurable Ollama request timeout (issue #180).

Local models on hardware without a dedicated GPU can need far longer than the
automatic 3-10 minutes for a single request. A user-set timeout must win over
the automatic one in both directions, and clearing it must restore the
automatic behaviour exactly.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from modules import llm_clients
from modules.llm_clients import _resolve_ollama_timeout, set_ollama_timeout


@pytest.fixture(autouse=True)
def _reset_override():
    set_ollama_timeout(None)
    yield
    set_ollama_timeout(None)


@pytest.mark.parametrize("model, expected", [
    ("translategemma:27b", 600),
    ("translategemma:12b", 300),
    ("qwen3:8b", 300),
    ("translategemma:4b", 180),
    ("mistral", 300),  # size unknown
])
def test_automatic_timeout_scales_with_model_size(model, expected):
    assert _resolve_ollama_timeout(model, 100)[0] == expected


def test_automatic_timeout_raised_for_large_prompts():
    assert _resolve_ollama_timeout("translategemma:4b", 6000)[0] == 600


def test_user_timeout_overrides_upwards_and_downwards():
    set_ollama_timeout(45 * 60)
    assert _resolve_ollama_timeout("translategemma:4b", 100)[0] == 2700
    assert _resolve_ollama_timeout("translategemma:27b", 6000)[0] == 2700

    set_ollama_timeout(60)
    assert _resolve_ollama_timeout("translategemma:27b", 6000)[0] == 60


@pytest.mark.parametrize("cleared", [0, None])
def test_clearing_restores_automatic(cleared):
    set_ollama_timeout(3600)
    set_ollama_timeout(cleared)
    assert llm_clients._ollama_timeout_override is None
    assert _resolve_ollama_timeout("qwen3:8b", 100)[0] == 300


def test_model_size_still_reported_with_override():
    set_ollama_timeout(3600)
    assert _resolve_ollama_timeout("translategemma:12b", 100) == (3600, 12.0)


@pytest.mark.parametrize("max_tokens, expected", [
    (1024, 2700),         # short request: a single blocking call
    (8192, (120, 2700)),  # long request: streamed, override is the read timeout
])
def test_call_ollama_uses_the_override(monkeypatch, max_tokens, expected):
    """The timeout actually handed to requests.post is the user's value."""
    import json as json_module
    import requests

    seen = {}

    class _Resp:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"message": {"content": "Hallo"}}

        def iter_lines(self):
            yield json_module.dumps({"message": {"content": "Hallo"}, "done": True}).encode()

    def fake_post(url, json=None, timeout=None, proxies=None, stream=False):
        seen["timeout"] = timeout
        return _Resp()

    monkeypatch.setattr(requests, "post", fake_post)
    set_ollama_timeout(45 * 60)
    client = llm_clients.LLMClient(provider="ollama", model="translategemma:4b")
    assert client._call_ollama("Hello", max_tokens=max_tokens) == "Hallo"
    assert seen["timeout"] == expected
