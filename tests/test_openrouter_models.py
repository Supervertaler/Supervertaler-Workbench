"""The OpenRouter list matches the plugins', and a saved model stays (v1.10.375).

1. Workbench still offered Qwen 3.6 Plus (Free), which OpenRouter has
   withdrawn, so choosing it failed. The list is now the one in Supervertaler
   for Trados and for memoQ (LlmModels.OpenRouterModels in their shared
   core), in the same order, and its first entry is the default.
2. Settings found the saved OpenRouter model by substring, so after the
   list moved from Claude Sonnet 5 to 5.5 a saved "anthropic/claude-sonnet-5"
   would have selected Sonnet 5.5, and the next save written that over it.
   A saved model is now matched exactly, and one that has left the list is
   shown as "your current model", as the OpenAI list already did.
"""

from __future__ import annotations

import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtWidgets import QApplication, QComboBox

from modules.llm_clients import LLMClient
from modules.llm_pricing import estimate_cost

# The plugins' OpenRouter list as of core 6893544 (Supervertaler for Trados).
PLUGINS = [
    "anthropic/claude-sonnet-5.5", "anthropic/claude-opus-5.5", "anthropic/claude-fable-5.2",
    "openai/gpt-6-astra", "openai/gpt-6.1-sol", "openai/gpt-6-luna",
    "google/gemini-3.8-flash", "google/gemini-4-argon",
    "google/gemma-4-31b-it", "google/gemma-4-26b-a4b-it",
    "mistralai/mistral-small-2603", "qwen/qwen3.8-27b:free",
    "deepseek/deepseek-v4-pro", "deepseek/deepseek-v4-flash",
]
WITHDRAWN = "qwen/qwen3.6-plus:free"


def read(path):
    with open(os.path.join(ROOT, path), encoding="utf-8") as f:
        return f.read()


def settings_list():
    block = re.search(r"openrouter_combo\.addItems\(\[(.*?)\]\)", read("Supervertaler.py"), re.S)
    return [text.split()[0] for text in re.findall(r'"([^"]+)"', block.group(1))]


def quicktrans_list():
    block = re.search(r'\("openrouter", "OpenRouter \(200\+ models\)", "openrouter", \[(.*?)\]\),',
                      read("Supervertaler.py"), re.S)
    return re.findall(r'\("([^"]+)", "[^"]+"\)', block.group(1))


def test_the_list_is_the_plugins_list():
    assert list(LLMClient.OPENROUTER_MODELS) == PLUGINS


def test_the_default_is_the_first_entry():
    assert LLMClient.DEFAULT_MODELS["openrouter"] == PLUGINS[0]
    assert LLMClient(api_key="x", provider="openrouter").model == PLUGINS[0]


def test_settings_and_quicktrans_offer_the_same_list():
    # The AI Assistant's menu reads LLMClient.OPENROUTER_MODELS itself.
    assert settings_list() == PLUGINS
    assert quicktrans_list() == PLUGINS


def test_the_withdrawn_free_model_is_offered_nowhere():
    for path in ("Supervertaler.py", os.path.join("modules", "llm_clients.py"),
                 os.path.join("modules", "chat_view_widget.py")):
        assert f'"{WITHDRAWN}' not in read(path), path


@pytest.mark.parametrize("model", PLUGINS)
def test_every_listed_model_has_a_price(model):
    assert estimate_cost("openrouter", model, 1_000, 1_000) is not None, f"{model} has no price"


# ── a saved model is kept ────────────────────────────────────────────────

@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture(scope="module")
def S(app):
    saved = sys.stdout, sys.stderr
    import Supervertaler   # re-wraps stdout/stderr on Windows at import
    S._wrappers = sys.stdout, sys.stderr   # keep alive: closing them closes pytest's capture
    sys.stdout, sys.stderr = saved
    return Supervertaler


def openrouter_combo():
    combo = QComboBox()
    combo.setEditable(True)     # as in Settings
    combo.addItems(["anthropic/claude-sonnet-5.5 (Recommended)",
                    "anthropic/claude-opus-5.5 (Premium)",
                    "openai/gpt-6.1-sol (OpenAI recommended)"])
    return combo


def what_saving_writes(combo):
    return combo.currentText().split()[0]   # as _save_ai_settings_from_ui does


@pytest.mark.parametrize("saved", [
    "anthropic/claude-sonnet-5",    # the old default: a prefix of Sonnet 5.5's ID
    "anthropic/claude-opus-5",
    WITHDRAWN,
    "x-ai/grok-4.3",                # typed in by the user
])
def test_a_saved_model_that_left_the_list_is_kept(S, saved):
    combo = openrouter_combo()
    S._select_saved_model(combo, saved)
    assert combo.currentText() == f"{saved} (your current model)"
    assert what_saving_writes(combo) == saved
    assert combo.count() == 4


def test_a_listed_model_is_selected_not_added(S):
    combo = openrouter_combo()
    S._select_saved_model(combo, "openai/gpt-6.1-sol")
    assert what_saving_writes(combo) == "openai/gpt-6.1-sol"
    assert combo.count() == 3


def test_no_saved_model_leaves_the_first_entry(S):
    combo = openrouter_combo()
    S._select_saved_model(combo, "")
    assert combo.currentIndex() == 0 and combo.count() == 3


def test_settings_uses_the_exact_match_for_openrouter_and_gemini():
    src = read("Supervertaler.py")
    assert re.search(r"_select_saved_model\(openrouter_combo,", src)
    assert re.search(r"_select_saved_model\(gemini_combo,", src)
    assert not re.search(r"in (openrouter|gemini)_combo\.itemText\(", src)
