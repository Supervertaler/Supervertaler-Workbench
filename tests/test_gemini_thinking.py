"""Gemini thinking is pinned to a low level (Nov 2026).

Gemini 3+ models think before answering, billed at the output rate. Every
translation goes through translate_with_usage -> _call_gemini_with_usage, which
used the legacy SDK for every model: it cannot send thinkingConfig, so 3.5 Flash,
3.1 Pro and 4 Argon all thought at Google's default ("medium", or "high" for 3.1
Pro). The "minimal" pin for 3.5 lived only in _call_gemini, which nothing calls.

Now the models that think go through REST with the level the Trados plugin
sends; the rest stay on the SDK, unchanged. Nothing here goes to Google:
requests.post and the SDK are stand-ins, and the usage ledger is not written.
"""

from __future__ import annotations

import json
import os
import sys
import types

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

import pytest

from modules import llm_clients, usage_log
from modules.llm_clients import LLMClient, gemini_thinking_level


class Reply:
    def __init__(self, data, status=200):
        self.status_code, self._data = status, data
        self.text = json.dumps(data)

    def json(self):
        return self._data


OK = {"candidates": [{"content": {"parts": [
          {"text": "my reasoning", "thought": True}, {"text": "de vertaling"}]}}],
      "usageMetadata": {"promptTokenCount": 120, "candidatesTokenCount": 8,
                        "thoughtsTokenCount": 30, "cachedContentTokenCount": 0}}


@pytest.fixture
def google(monkeypatch):
    """Stand-ins for Google: what each route was sent."""
    sent = types.SimpleNamespace(rest=[], sdk=[], ledger=[], reply=OK)

    def post(url, headers=None, data=None, timeout=None):
        sent.rest.append({"url": url, "body": json.loads(data), "timeout": timeout})
        return sent.reply if isinstance(sent.reply, Reply) else Reply(sent.reply)

    class Model:
        def __init__(self, name, system_instruction=None):
            self.name = name

        def generate_content(self, content):
            sent.sdk.append(self.name)
            return types.SimpleNamespace(text="sdk vertaling", usage_metadata=None)

    fake_genai = types.ModuleType("google.generativeai")
    fake_genai.configure = lambda **k: None
    fake_genai.GenerativeModel = Model
    fake_google = types.ModuleType("google")
    fake_google.generativeai = fake_genai
    monkeypatch.setitem(sys.modules, "google", fake_google)
    monkeypatch.setitem(sys.modules, "google.generativeai", fake_genai)
    import requests
    monkeypatch.setattr(requests, "post", post)
    monkeypatch.setattr(usage_log, "record", lambda *a, **k: sent.ledger.append((a, k)))
    return sent


def client(model):
    return LLMClient(api_key="test-key", provider="gemini", model=model)


@pytest.mark.parametrize("model, level", [
    ("gemini-3.5-flash", "minimal"),
    ("gemini-3.5-flash-lite", "minimal"),
    ("gemini-3.8-flash", "low"),        # "minimal" is a 400 on these three
    ("gemini-3.1-pro-preview", "low"),
    ("gemini-4-argon", "low"),
])
def test_a_thinking_model_is_sent_its_level(google, model, level):
    text, usage = client(model).translate_with_usage("text", custom_prompt="Translate: text")
    assert google.sdk == []
    [call] = google.rest
    assert call["body"]["generationConfig"]["thinkingConfig"] == {"thinkingLevel": level}
    assert call["url"].endswith(f"/models/{model}:generateContent")
    assert text == "de vertaling"                          # the thought part left out


@pytest.mark.parametrize("model", ["gemini-3.1-flash-lite", "gemini-2.5-pro", "gemini-2.5-flash",
                                   "gemini-5-something-new"])
def test_other_models_stay_on_the_sdk_without_a_level(google, model):
    # 2.5 uses a thinking budget, not a level; a model we don't know is not
    # guessed at (a level it refuses would be an error on every call).
    assert gemini_thinking_level(model) is None
    text, _ = client(model).translate_with_usage("text", custom_prompt="Translate: text")
    assert google.rest == [] and google.sdk == [model]
    assert text == "sdk vertaling"


def test_translate_takes_the_same_route(google):
    assert client("gemini-4-argon").translate("text", custom_prompt="Translate: text") == "de vertaling"
    assert google.rest[0]["body"]["generationConfig"]["thinkingConfig"] == {"thinkingLevel": "low"}


def test_the_thinking_is_counted_in_what_the_call_cost(google):
    _, usage = client("gemini-3.5-flash").translate_with_usage("text", custom_prompt="Translate: text")
    assert usage == {"input_tokens": 120, "output_tokens": 38,
                     "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}
    [(args, _kw)] = google.ledger
    assert args[2]["output_tokens"] == 38


def test_system_prompt_and_max_tokens_are_passed_on(google):
    client("gemini-4-argon").translate_with_usage(
        "text", custom_prompt="Translate: text", system_prompt="You are a translator.", max_tokens=500)
    body = google.rest[0]["body"]
    assert body["systemInstruction"] == {"parts": [{"text": "You are a translator."}]}
    assert body["generationConfig"]["maxOutputTokens"] == 500
    assert body["contents"][0]["parts"][0]["text"] == "Translate: text"
    assert google.rest[0]["timeout"] >= 600                # a whole batch on Argon can take minutes


def test_the_level_is_found_however_the_name_is_written():
    assert gemini_thinking_level("Gemini-4-Argon ") == "low"
    assert gemini_thinking_level("models/gemini-3.5-flash") == "minimal"
    assert gemini_thinking_level("") is None and gemini_thinking_level(None) is None


def test_an_error_from_google_is_reported_not_returned_as_a_translation(google):
    google.reply = Reply({"error": {"message": "Thinking level MINIMAL is not supported"}}, status=400)
    with pytest.raises(RuntimeError, match="Gemini REST error 400"):
        client("gemini-3.5-flash").translate_with_usage("text", custom_prompt="Translate: text")


def test_a_blocked_or_empty_reply_is_reported(google):
    google.reply = {"promptFeedback": {"blockReason": "SAFETY"}}
    with pytest.raises(RuntimeError, match="prompt blocked: SAFETY"):
        client("gemini-4-argon").translate_with_usage("text", custom_prompt="Translate: text")
    google.reply = {"candidates": [{"content": {"parts": [{"text": "only thinking", "thought": True}]}}]}
    with pytest.raises(RuntimeError, match="empty response"):
        client("gemini-4-argon").translate_with_usage("text", custom_prompt="Translate: text")


def test_a_reply_without_usage_still_gives_the_text(google):
    google.reply = {"candidates": [{"content": {"parts": [{"text": "de vertaling"}]}}]}
    text, usage = client("gemini-4-argon").translate_with_usage("text", custom_prompt="Translate: text")
    assert (text, usage) == ("de vertaling", {})


def test_every_gemini_3_or_later_in_settings_is_pinned():
    # A Gemini 3+ model added to the Settings list without a level would think
    # at Google's default and bill for it: this fails until it is given one
    # (or is known to take none, like 3.1 Flash-Lite, as in the Trados plugin).
    import re
    with open(os.path.join(REPO, "modules", "main_window", "settings.py"), encoding="utf-8") as f:
        listed = set(re.findall(r'"(gemini-[0-9][\w.-]*)', f.read()))
    assert "gemini-4-argon" in listed
    for model in sorted(listed - {"gemini-3.1-flash-lite"}):
        if model.startswith(("gemini-3", "gemini-4")):
            assert gemini_thinking_level(model), model


@pytest.mark.parametrize("model", ["gemini-3.8-flash", "gemini-3.5-flash-lite"])
def test_new_gemini_models_are_offered_everywhere(model):
    # Both were pinned for thinking before they were offered; this checks they
    # can now be picked in Settings, QuickTrans, the chat and PDF Rescue, and
    # are allowed to take images.
    assert model in LLMClient.VISION_MODELS["gemini"]
    for rel in (("modules", "main_window", "settings.py"),
                ("modules", "chat_view_widget.py"),
                ("modules", "pdf_rescue_Qt.py")):
        with open(os.path.join(REPO, *rel), encoding="utf-8") as f:
            assert f'"{model}' in f.read(), rel
