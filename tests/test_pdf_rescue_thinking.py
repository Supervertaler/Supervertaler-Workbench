"""PDF Rescue: thinking models are held back, and given room to answer (Nov 2026).

PDF Rescue called the Gemini SDK itself, so 3.5 Flash, 3.1 Pro and 4 Argon
thought at Google's default on every page, billed as output. It now goes
through the shared client, which pins the level (see test_gemini_thinking.py).
OpenAI's reasoning models had 4,000 output tokens per page, their thinking
included, so a dense page could come back short or empty.

Nothing here goes to Google or OpenAI: requests.post, the Gemini SDK and the
OpenAI client are stand-ins.
"""

from __future__ import annotations

import json
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from PIL import Image

from modules import pdf_rescue_Qt
from modules.pdf_rescue_Qt import PDFRescueQt


class Reply:
    status_code = 200

    def __init__(self, data):
        self._data, self.text = data, json.dumps(data)

    def json(self):
        return self._data


@pytest.fixture
def page(tmp_path):
    path = tmp_path / "page.png"
    Image.new("RGB", (40, 20), "white").save(path)
    return str(path)


@pytest.fixture
def google(monkeypatch):
    sent = types.SimpleNamespace(rest=[], sdk=[])

    def post(url, headers=None, data=None, timeout=None):
        sent.rest.append({"url": url, "body": json.loads(data), "key": headers["x-goog-api-key"]})
        return Reply({"candidates": [{"content": {"parts": [
            {"text": "thinking", "thought": True}, {"text": "Page text"}]}}]})

    class Model:
        def __init__(self, name, system_instruction=None):
            self.name = name

        def generate_content(self, content):
            sent.sdk.append((self.name, content))
            return types.SimpleNamespace(text="SDK page text", usage_metadata=None)

    fake_genai = types.ModuleType("google.generativeai")
    fake_genai.configure = lambda **k: None
    fake_genai.GenerativeModel = Model
    fake_google = types.ModuleType("google")
    fake_google.generativeai = fake_genai
    monkeypatch.setitem(sys.modules, "google", fake_google)
    monkeypatch.setitem(sys.modules, "google.generativeai", fake_genai)
    import requests
    monkeypatch.setattr(requests, "post", post)
    return sent


def rescue(**attrs):
    # The extract methods need only these; the window itself isn't built.
    return types.SimpleNamespace(_gemini_key="test-key", **attrs)


@pytest.mark.parametrize("model, level", [("gemini-4-argon", "low"), ("gemini-3.1-pro-preview", "low")])
def test_a_thinking_gemini_model_is_sent_its_level_and_the_page(google, page, model, level):
    text = PDFRescueQt._extract_with_gemini(rescue(), page, model, "Extract the text")
    assert text == "Page text"                                   # the thought part left out
    [call] = google.rest
    assert google.sdk == []
    assert call["key"] == "test-key"
    assert call["url"].endswith(f"/models/{model}:generateContent")
    config = call["body"]["generationConfig"]
    assert config["thinkingConfig"] == {"thinkingLevel": level}
    assert "maxOutputTokens" not in config                       # Google's own maximum
    parts = call["body"]["contents"][0]["parts"]
    assert parts[0] == {"text": "Extract the text"}
    assert parts[1]["inline_data"]["mime_type"] == "image/png" and parts[1]["inline_data"]["data"]


def test_a_model_without_a_level_still_uses_the_sdk(google, page):
    text = PDFRescueQt._extract_with_gemini(rescue(), page, "gemini-2.5-pro", "Extract the text")
    assert text == "SDK page text" and google.rest == []
    [(name, content)] = google.sdk
    assert name == "gemini-2.5-pro" and content[0] == "Extract the text" and len(content) == 2


def test_an_error_from_google_reaches_the_page_as_an_error(google, page, monkeypatch):
    import requests

    class Bad(Reply):
        status_code = 400

    monkeypatch.setattr(requests, "post", lambda *a, **k: Bad({"error": {"message": "bad level"}}))
    with pytest.raises(RuntimeError, match="Gemini REST error 400"):
        PDFRescueQt._extract_with_gemini(rescue(), page, "gemini-4-argon", "Extract the text")


def openai_stand_in():
    calls = []

    def create(**params):
        calls.append(params)
        msg = types.SimpleNamespace(content="Page text")
        return types.SimpleNamespace(choices=[types.SimpleNamespace(message=msg)])

    chat = types.SimpleNamespace(completions=types.SimpleNamespace(create=create))
    return {"openai": types.SimpleNamespace(chat=chat)}, calls


def test_an_openai_reasoning_model_gets_room_for_its_thinking(page):
    clients, calls = openai_stand_in()
    PDFRescueQt._extract_with_openai(rescue(clients=clients, _encode_image=lambda p: "aGk="),
                                     page, "gpt-6.1-sol", "Extract the text")
    assert calls[0]["max_completion_tokens"] == pdf_rescue_Qt.REASONING_OUTPUT_TOKENS >= 16000
    assert "max_tokens" not in calls[0]


def test_an_older_openai_model_keeps_its_limit(page):
    clients, calls = openai_stand_in()
    PDFRescueQt._extract_with_openai(rescue(clients=clients, _encode_image=lambda p: "aGk="),
                                     page, "gpt-4o", "Extract the text")
    assert calls[0]["max_tokens"] == 4000
