"""Gemini through OpenRouter thinks at the same low level as direct (Nov 2026).

Gemini 3+ models think before answering, and OpenRouter bills that thinking as
output, as Google does. Workbench sent OpenRouter no reasoning setting, so
google/gemini-3.8-flash and google/gemini-4-argon thought at their default.
OpenRouter passes reasoning.effort to Gemini 3 as its thinkingLevel, so they
are now sent the level Workbench sends Google direct. Other models are sent
nothing, as before. Nothing here goes to OpenRouter: the OpenAI library is a
stand-in, and the usage ledger is not written.
"""

from __future__ import annotations

import os
import sys
import types

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

import pytest

from modules import usage_log
from modules.llm_clients import LLMClient, openrouter_reasoning_effort


@pytest.fixture
def openrouter(monkeypatch):
    """A stand-in for the OpenAI library: what each request was sent."""
    sent = []

    class Completions:
        def create(self, **params):
            sent.append(params)
            usage = types.SimpleNamespace(prompt_tokens=100, completion_tokens=40,
                                          prompt_tokens_details=None)
            message = types.SimpleNamespace(content="de vertaling")
            return types.SimpleNamespace(choices=[types.SimpleNamespace(message=message)],
                                         usage=usage)

    class OpenAI:
        def __init__(self, **kwargs):
            self.chat = types.SimpleNamespace(completions=Completions())

    monkeypatch.setitem(sys.modules, "openai", types.SimpleNamespace(OpenAI=OpenAI))
    monkeypatch.setattr(usage_log, "record", lambda *a, **k: None)
    return sent


def translate(model, provider="openrouter"):
    client = LLMClient(api_key="x", provider=provider, model=model)
    return client.translate_with_usage("text", custom_prompt="Translate: text")


@pytest.mark.parametrize("model, effort", [
    ("google/gemini-3.8-flash", "low"),
    ("google/gemini-4-argon", "low"),
    ("google/gemini-3.1-pro-preview", "low"),
    ("google/gemini-3.5-flash", "minimal"),
])
def test_gemini_via_openrouter_is_sent_its_low_level(openrouter, model, effort):
    text, _usage = translate(model)
    assert text == "de vertaling"
    assert openrouter[-1]["extra_body"] == {"reasoning": {"effort": effort}}


@pytest.mark.parametrize("model", [
    "google/gemma-4-31b-it",            # Gemma: no thinking levels
    "google/gemini-2.5-pro",            # 2.5 takes a budget, not a level
    "anthropic/claude-sonnet-5.5",
    "openai/gpt-6.1-sol",
    "deepseek/deepseek-v4-flash",
])
def test_other_models_are_sent_no_reasoning_setting(openrouter, model):
    translate(model)
    assert "extra_body" not in openrouter[-1]


def test_a_google_model_elsewhere_is_not_sent_it(openrouter):
    # Only OpenRouter reads reasoning.effort this way; a custom endpoint that
    # happens to name a Gemini model is left alone.
    translate("google/gemini-3.8-flash", provider="custom_openai")
    assert "extra_body" not in openrouter[-1]


def test_the_level_is_the_one_sent_to_google_direct():
    from modules.llm_clients import gemini_thinking_level
    for model in LLMClient.OPENROUTER_MODELS:
        if model.startswith("google/gemini"):
            assert openrouter_reasoning_effort(model) == gemini_thinking_level(model[len("google/"):])
            assert openrouter_reasoning_effort(model), model   # every listed Gemini is pinned
