"""
LLM Clients Module for Supervertaler
=====================================

Specialized independent module for interacting with various LLM providers.
Can be used standalone or imported by other applications.

Supported Providers:
- OpenAI (GPT-6 Astra, GPT-6.1 Sol, GPT-6 Luna)
- Anthropic (Claude Sonnet 5.5, Opus 5.5, Fable 5.1)
- Google (Gemini 3.1 Flash-Lite, 2.5 Pro, 3.1 Pro Preview, Gemma 4 26B MoE)
- Mistral AI (Mistral Large, Mistral Small)
- DeepSeek (V4 Pro, V4 Flash)

Claude Models:
- Sonnet 4.6: Best balance - flagship model for general translation ($3/$15 per MTok)
- Haiku 4.5: Fast & affordable - 2x speed, 1/5 cost of Sonnet ($1/$5 per MTok)
- Opus 4.8: Most capable - complex legal/technical translation, 1M context ($5/$25 per MTok)

Temperature Handling:
- Reasoning models (GPT-5, GPT-6, o1, o3): temperature parameter OMITTED (not supported)
- Standard models: temperature=0.3

Usage:
    from modules.llm_clients import LLMClient

    # Use default (Sonnet 4.5)
    client = LLMClient(api_key="your-key", provider="claude")

    # Or specify model
    client = LLMClient(api_key="your-key", provider="claude", model="claude-haiku-4-5-20251001")

    response = client.translate("Hello world", source_lang="en", target_lang="nl")
"""

import os
import sys
import time
from typing import Dict, Optional, Literal, List, NamedTuple, Tuple
from dataclasses import dataclass


# ─── API keys ────────────────────────────────────────────────────────────────
#
# One reader for every key Workbench uses: Supervertaler.py, the AI chat, PDF
# Rescue, QuickTrans and the headless batch offload all come here (design note
# §4: the four readers were funnelled into one before anything else changed).
#
# Workbench shares its keys with Supervertaler for Trados, for memoQ and
# Sidekick (decision 1, 30 Oct 2026): one key per provider for all four, in the
# shared store (modules/api_key_store.py) - the plain api-keys.json in the data
# folder, then the copy locked to this computer. Workbench's own copy,
# settings.json's "api_keys", is read last and still written, for older
# Workbench versions; step 2 removes it.

# Where keys are entered, as the sentences users see name it.
AI_SETTINGS_PLACE = "Settings → AI Settings"
MT_SETTINGS_PLACE = "Settings → MT Settings"

# Workbench's key names → the shared ids they are stored under. Non-secret
# values in the same section (ollama_endpoint, the two regions) stay in
# settings.json only.
SHARED_KEY_IDS = {
    'openai': 'openai', 'claude': 'claude', 'gemini': 'gemini', 'mistral': 'mistral',
    'deepseek': 'deepseek', 'openrouter': 'openrouter',
    'google_translate': 'google', 'deepl': 'deepl', 'microsoft_translate': 'microsoft',
    'modernmt': 'modernmt', 'mymemory': 'mymemory',
    'amazon_translate': 'amazon_translate', 'amazon_translate_secret': 'amazon_translate_secret',
}
_MT_NAMES = {'google_translate', 'deepl', 'microsoft_translate', 'modernmt', 'mymemory',
             'amazon_translate', 'amazon_translate_secret'}

# Older names in Workbench's own copy. Its 'google' was Gemini's key - the
# shared 'google' is Google Translate's, so the two never meet.
_OWN_ALIASES = {'gemini': ('google',), 'microsoft_translate': ('azure_translate',), 'claude': ('anthropic',)}
_RETIRED_NAMES = {'google', 'azure_translate', 'anthropic'}

PROFILE_KEY_PREFIX = "workbench:custom_openai:"


def _own_settings_paths() -> List[str]:
    """Where Workbench's settings.json may be, newest layout first."""
    from modules import api_key_store
    script_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    root = api_key_store.data_root()
    paths = [
        os.path.join(root, "workbench", "settings", "settings.json"),
        os.path.join(root, "settings", "settings.json"),  # old layout
        os.path.join(os.path.expanduser('~'), "Supervertaler", "workbench", "settings", "settings.json"),
        os.path.join(os.path.expanduser('~'), "Supervertaler", "settings", "settings.json"),
        os.path.join(script_dir, "user_data_private", "workbench", "settings", "settings.json"),
        os.path.join(script_dir, "user_data_private", "settings", "settings.json"),
        os.path.join(script_dir, "user_data", "workbench", "settings", "settings.json"),
        os.path.join(script_dir, "user_data", "settings", "settings.json"),
    ]
    return list(dict.fromkeys(paths))


def _read_key_section(path: str) -> Dict[str, str]:
    """A settings.json's "api_keys" section - or a flat file of keys - as name → value."""
    import json
    with open(path, 'r', encoding='utf-8-sig') as f:
        data = json.load(f)
    if not isinstance(data, dict):
        return {}
    section = data.get("api_keys") if isinstance(data.get("api_keys"), dict) else data
    return {k: v for k, v in section.items() if isinstance(v, str)}


def _read_own_keys(settings_path: Optional[str] = None) -> Dict[str, str]:
    """Workbench's own copy. ``settings_path`` names the file; without it the
    usual places are searched, then the legacy api_keys.txt."""
    if settings_path:
        try:
            return _read_key_section(settings_path) if os.path.isfile(settings_path) else {}
        except Exception:
            return {}

    for path in _own_settings_paths():
        if os.path.exists(path):
            try:
                keys = _read_key_section(path)
                if keys:
                    return keys
            except Exception:
                pass

    # Legacy fallback: api_keys.txt (for backward compatibility)
    from modules import api_key_store
    script_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    legacy_paths = [
        os.path.join(api_key_store.data_root(), "api_keys.txt"),
        os.path.join(os.path.expanduser('~'), "Supervertaler", "api_keys.txt"),
        os.path.join(script_dir, "user_data_private", "api_keys.txt"),
        os.path.join(script_dir, "user_data", "api_keys.txt"),
        os.path.join(script_dir, "api_keys.txt"),
    ]
    keys: Dict[str, str] = {}
    for legacy_path in dict.fromkeys(legacy_paths):
        if os.path.exists(legacy_path):
            try:
                with open(legacy_path, 'r', encoding='utf-8') as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith('#') and '=' in line:
                            key, value = line.split('=', 1)
                            keys[key.strip()] = value.strip()
                if keys:
                    break
            except Exception:
                pass
    return keys


def _own_value(own: Dict[str, str], name: str) -> str:
    for n in (name,) + _OWN_ALIASES.get(name, ()):
        value = (own.get(n) or '').strip()
        if value:
            return value
    return ''


def load_api_keys(settings_path: Optional[str] = None) -> Dict[str, str]:
    """Every key Workbench uses, by Workbench's names - the one reader.

    Each comes from the shared store by the step-1 rule (the plain file, then
    the locked copy), else from Workbench's own copy (``settings_path``, or
    found as before). A key the locked copy holds but this computer cannot
    unlock is left out - never replaced by an older own copy, and never a blank
    key; ``api_key_problem`` says why. Non-secret values (Ollama's endpoint,
    the regions) come from the own copy, as before.

    Reads the files on every call, no cache: about a millisecond, and a key
    changed in another product is seen at once."""
    from modules import api_key_store
    own = _read_own_keys(settings_path)
    found = api_key_store.lookup_many(set(SHARED_KEY_IDS.values()))
    api_keys = {name: value for name, value in own.items()
                if name not in SHARED_KEY_IDS and name not in _RETIRED_NAMES}
    for name, key_id in SHARED_KEY_IDS.items():
        f = found[key_id]
        if f.status == api_key_store.PRESENT:
            api_keys[name] = f.key
        elif f.status == api_key_store.ABSENT:
            value = _own_value(own, name)
            if value:
                api_keys[name] = value

    # Set environment variable for Ollama endpoint if configured
    if api_keys.get('ollama_endpoint'):
        os.environ['OLLAMA_ENDPOINT'] = api_keys['ollama_endpoint']

    return api_keys


def api_key_problem(name: str, settings_place: Optional[str] = None,
                    settings_path: Optional[str] = None) -> Optional[str]:
    """What to tell the user when an action needs this key and has none to use:
    core's M2 when one is stored but cannot be unlocked here, "No API key set
    for …" when there is none; None when the key is there, or for a name that is
    not a shared key (Ollama, custom endpoints)."""
    from modules import api_key_store
    name = 'gemini' if name == 'google' else name
    key_id = SHARED_KEY_IDS.get(name)
    if key_id is None:
        return None
    place = settings_place or (MT_SETTINGS_PLACE if name in _MT_NAMES else AI_SETTINGS_PLACE)
    found = api_key_store.lookup(key_id)
    if found.status == api_key_store.PRESENT:
        return None
    if found.status == api_key_store.ABSENT and _own_value(_read_own_keys(settings_path), name):
        return None
    return api_key_store.when_used(found, key_id, place)


def unreadable_key_notes() -> Dict[str, str]:
    """M1 for each Workbench key name whose key cannot be unlocked here, for the
    note under its box in Settings."""
    from modules import api_key_store
    found = api_key_store.lookup_many(set(SHARED_KEY_IDS.values()))
    notes = {}
    for name, key_id in SHARED_KEY_IDS.items():
        note = api_key_store.unreadable_note(found[key_id], key_id)
        if note:
            notes[name] = note
    return notes


def save_api_key(name: str, value: str) -> bool:
    """Saves one key by its Workbench name into the shared store - the plain
    file, then the locked copy; empty removes it. False when the save did not
    take. A non-secret value is not shared (True: nothing to do here); the
    caller writes Workbench's own copy as well."""
    from modules import api_key_store
    key_id = SHARED_KEY_IDS.get(name)
    return True if key_id is None else api_key_store.set_key(key_id, value)


def _profile_keys(profiles) -> Dict[str, str]:
    from modules import api_key_store
    keys = {}
    for p in profiles or []:
        if isinstance(p, dict) and (p.get('api_key') or '').strip():
            keys[api_key_store.private_id("workbench", "custom_openai", p.get('name') or '')] = p['api_key'].strip()
    return keys


def save_profile_keys(profiles) -> bool:
    """Custom OpenAI-compatible profiles: each key also under
    ``workbench:custom_openai:<profile name>`` in the locked copy (step 1 writes
    it there as well; settings.json's copy still decides). A removed profile's
    entry goes."""
    from modules import api_key_store
    return api_key_store.set_private_keys(PROFILE_KEY_PREFIX, _profile_keys(profiles))


class PreparedKeys(NamedTuple):
    joined: bool                     # the first-start join ran (mark it done)
    notices: List[str]               # for the status line and the log: M8, M6
    keychain_missing: Optional[str]  # M7, to be said once


def prepare_api_keys(settings_path: Optional[str], join: bool, profiles=None) -> PreparedKeys:
    """Once per start, before any key is read.

    ``join`` (Workbench's first step-1 start): its own keys go into the shared
    store where that has none - never overwriting (core's MigrateFrom rule).
    Where its own key differs, the shared one wins, and M8 names those
    providers. Then the step-1 sync: the plain keys, and the profile keys,
    copied into the locked copy (adding and replacing, never removing)."""
    from modules import api_key_store
    import logging
    log = logging.getLogger(__name__)
    notices: List[str] = []
    joined = False
    if join:
        own = _read_own_keys(settings_path)
        mine = {key_id: _own_value(own, name) for name, key_id in SHARED_KEY_IDS.items() if _own_value(own, name)}
        result = api_key_store.migrate_from(mine)
        if result is None:
            log.warning("Workbench's own API keys were not added to the shared ones yet; the next start tries again.")
        else:
            joined = True
            added, differing = result
            log.info("API keys: %d of Workbench's own added to the shared ones.", added)
            if differing:
                names = [api_key_store.display_name(k) for k in differing]
                listed = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]
                notices.append(
                    "Workbench now uses the same API keys as Supervertaler for Trados, memoQ and Sidekick. "
                    + (f"Your Workbench key for {listed} was different; the shared one is used from now on."
                       if len(names) == 1 else
                       f"Your Workbench keys for {listed} were different; the shared ones are used from now on."))
    sync = api_key_store.sync_locked(extra=_profile_keys(profiles))
    if sync.locked:
        log.info("API keys: %d locked to this computer.", sync.locked)
    if sync.notice:
        notices.append(sync.notice)
    return PreparedKeys(joined, notices, api_key_store.keychain_missing_notice())


# User-chosen Ollama request timeout in seconds (issue #180), or None for the
# automatic timeout scaled by model size and prompt length. Set from
# Settings → AI Settings via set_ollama_timeout(); held at module level so it
# reaches every LLMClient without threading a parameter through each caller.
_ollama_timeout_override: Optional[int] = None


def set_ollama_timeout(seconds: Optional[int]) -> None:
    """Set the Ollama request timeout in seconds; 0 or None restores automatic."""
    global _ollama_timeout_override
    _ollama_timeout_override = int(seconds) if seconds and int(seconds) > 0 else None


def _resolve_ollama_timeout(model: str, prompt_len: int) -> Tuple[int, float]:
    """Return (timeout_seconds, param_billions) for an Ollama request.

    A user-set timeout wins outright, so it can be shorter as well as longer
    than the automatic one.
    """
    import re
    size_match = re.search(r'(\d+\.?\d*)b', (model or '').lower())
    param_billions = float(size_match.group(1)) if size_match else 0

    if _ollama_timeout_override:
        return _ollama_timeout_override, param_billions

    if param_billions >= 13:
        base_timeout = 600  # 10 minutes for large models (13B+)
    elif param_billions >= 7:
        base_timeout = 300  # 5 minutes for medium models (7B-12B)
    elif param_billions > 0:
        base_timeout = 180  # 3 minutes for small models (<7B)
    else:
        base_timeout = 300  # 5 minutes default if size unknown

    # Boost timeout for large prompts (e.g. AI Assistant prompt generation)
    # Large prompts need more processing time for both input and output
    if prompt_len > 5000:
        return max(base_timeout, 600), param_billions  # At least 10 minutes for large prompts
    return base_timeout, param_billions


def _sanitize_ollama_endpoint(endpoint: str) -> str:
    """Strip trailing slashes and common path suffixes that cause double-path issues."""
    endpoint = endpoint.rstrip('/')
    for suffix in ('/api', '/v1'):
        if endpoint.endswith(suffix):
            endpoint = endpoint[:-len(suffix)]
    return endpoint


@dataclass
class LLMConfig:
    """Configuration for LLM client"""
    provider: Literal["openai", "claude", "gemini", "mistral", "deepseek", "openrouter"]
    model: str
    api_key: str
    temperature: Optional[float] = None  # Auto-detected if None
    max_tokens: int = 16384  # Increased from 4096 for batch translation (100 segments needs ~16K tokens)


class LLMClient:
    """Universal LLM client for translation tasks"""

    # Default models for each provider
    DEFAULT_MODELS = {
        "openai": "gpt-6.1-sol",  # GPT-6.1 Sol - the middle of OPENAI_MODELS, not its first
        "claude": "claude-sonnet-5-5",  # Claude Sonnet 5.5 (older IDs still selectable)
        "gemini": "gemini-3.1-flash-lite",  # Gemini 3.1 Flash-Lite
        "mistral": "mistral-large-latest",  # Mistral Large (flagship)
        "deepseek": "deepseek-v4-pro",  # DeepSeek V4 Pro (flagship)
        "ollama": "translategemma:12b",  # Local LLM via Ollama - purpose-built translation model
        "custom_openai": "custom-model",  # Custom OpenAI-compatible endpoint
        "openrouter": "anthropic/claude-sonnet-5"  # OpenRouter gateway (200+ models)
    }

    # Available Mistral models with descriptions
    MISTRAL_MODELS = {
        "mistral-large-latest": {
            "name": "Mistral Large",
            "description": "Flagship model – top-tier reasoning and multilingual quality",
            "strengths": ["Multilingual", "Complex reasoning", "High accuracy"],
            "use_case": "Recommended for most translation tasks"
        },
        "mistral-small-latest": {
            "name": "Mistral Small",
            "description": "Fast and cost-effective – great for high-volume translation",
            "strengths": ["Fast", "Cost-effective", "Multilingual"],
            "use_case": "Best for large projects where speed and cost matter"
        }
    }
    
    # Available DeepSeek models with descriptions
    DEEPSEEK_MODELS = {
        "deepseek-v4-pro": {
            "name": "DeepSeek V4 Pro",
            "description": "Flagship – top-tier reasoning and multilingual quality",
            "strengths": ["Multilingual", "Complex reasoning", "High accuracy"],
            "use_case": "Recommended for most translation tasks"
        },
        "deepseek-v4-flash": {
            "name": "DeepSeek V4 Flash",
            "description": "Fast and cost-effective – great for high-volume translation",
            "strengths": ["Fast", "Cost-effective", "Multilingual"],
            "use_case": "Best for large projects where speed and cost matter"
        }
    }

    # Available OpenRouter models (curated selection)
    # OpenRouter is an API gateway – users can also type any model ID from openrouter.ai/models
    OPENROUTER_MODELS = {
        "anthropic/claude-sonnet-5": {
            "name": "Claude Sonnet 5",
            "description": "Anthropic flagship Sonnet – near-Opus quality, fast",
            "strengths": ["General translation", "Multilingual", "Fast"],
            "use_case": "Recommended for most translation tasks"
        },
        "anthropic/claude-opus-5": {
            "name": "Claude Opus 5",
            "description": "Anthropic's flagship Opus – 1M context, top-tier reasoning",
            "strengths": ["Legal translation", "Technical documents", "Complex reasoning", "1M context"],
            "use_case": "Specialised legal/technical translation, long-context jobs"
        },
        "openai/gpt-6-astra": {
            "name": "GPT-6 Astra",
            "description": "OpenAI's most capable model – 1M context",
            "strengths": ["Complex reasoning", "Multilingual", "Highest accuracy"],
            "use_case": "The hardest work, when cost is secondary"
        },
        "openai/gpt-6.1-sol": {
            "name": "GPT-6.1 Sol",
            "description": "Close to Astra's quality at a fifth of its price – 1M context",
            "strengths": ["Complex reasoning", "Multilingual", "High accuracy"],
            "use_case": "Recommended OpenAI model for most translation tasks"
        },
        "openai/gpt-6-luna": {
            "name": "GPT-6 Luna",
            "description": "OpenAI's cheapest – for high-volume work",
            "strengths": ["Fast", "Cost-effective", "Multilingual"],
            "use_case": "High-volume translation"
        },
        "google/gemini-3.1-pro-preview": {
            "name": "Gemini 3.1 Pro",
            "description": "Google latest – strong multilingual",
            "strengths": ["Multilingual", "Large context", "High quality"],
            "use_case": "General translation"
        },
        "google/gemini-3-flash-preview": {
            "name": "Gemini 3 Flash",
            "description": "Google fast – great for high volume",
            "strengths": ["Fast", "Cost-effective", "Multilingual"],
            "use_case": "High-volume translation"
        },
        "mistralai/mistral-small-2603": {
            "name": "Mistral Small",
            "description": "Mistral cost-effective – strong European languages",
            "strengths": ["European languages", "Fast", "Cost-effective"],
            "use_case": "European language translation"
        },
        "qwen/qwen3.6-plus:free": {
            "name": "Qwen 3.6 Plus (Free)",
            "description": "Free tier – no cost, good quality",
            "strengths": ["Free", "Multilingual", "100+ languages"],
            "use_case": "Testing or budget-constrained projects"
        },
        "deepseek/deepseek-v4-pro": {
            "name": "DeepSeek V4 Pro",
            "description": "DeepSeek flagship – strong multilingual, competitive pricing",
            "strengths": ["Multilingual", "Complex reasoning", "Cost-effective"],
            "use_case": "General translation via OpenRouter"
        },
        "deepseek/deepseek-v4-flash": {
            "name": "DeepSeek V4 Flash",
            "description": "DeepSeek fast – great for high-volume translation",
            "strengths": ["Fast", "Cost-effective", "Multilingual"],
            "use_case": "High-volume translation via OpenRouter"
        }
    }

    # Available Ollama models with descriptions (for UI display)
    # Last audited: February 2026
    OLLAMA_MODELS = {
        "translategemma:4b": {
            "name": "TranslateGemma 4B",
            "description": "Translation-tuned - fast & lightweight (55 languages)",
            "size_gb": 3.3,
            "ram_required": 6,
            "quality_stars": 4,
            "strengths": ["Purpose-built for translation", "55 languages incl. Dutch", "Fast"],
            "use_case": "Quick translation drafts, low-end hardware"
        },
        "translategemma:12b": {
            "name": "TranslateGemma 12B",
            "description": "Best translation model for size – beats larger general models",
            "size_gb": 8.1,
            "ram_required": 12,
            "quality_stars": 5,
            "strengths": ["Top translation quality", "55 languages incl. Dutch", "Beats Gemma 3 27B on translation"],
            "use_case": "Professional translation – best quality/size ratio"
        },
        "qwen3:4b": {
            "name": "Qwen 3 4B",
            "description": "Fast & lightweight general-purpose (100+ languages)",
            "size_gb": 2.5,
            "ram_required": 4,
            "quality_stars": 3,
            "strengths": ["Fast", "Low memory", "100+ languages"],
            "use_case": "Quick drafts, simple text, low-end hardware"
        },
        "qwen3:8b": {
            "name": "Qwen 3 8B",
            "description": "Excellent general-purpose – strong multilingual",
            "size_gb": 5.2,
            "ram_required": 8,
            "quality_stars": 4,
            "strengths": ["Excellent multilingual", "100+ languages", "Balanced speed/quality"],
            "use_case": "General translation, most European languages"
        },
        "gemma3:12b": {
            "name": "Gemma 3 12B",
            "description": "Google's general-purpose – 140+ languages, multimodal",
            "size_gb": 8.1,
            "ram_required": 12,
            "quality_stars": 5,
            "strengths": ["140+ languages", "High quality", "Multimodal (text+image)"],
            "use_case": "Quality-focused translation, technical content"
        },
        "qwen3:14b": {
            "name": "Qwen 3 14B",
            "description": "Premium quality – excellent for complex text",
            "size_gb": 9.3,
            "ram_required": 16,
            "quality_stars": 5,
            "strengths": ["Excellent quality", "Complex text", "Nuanced translation"],
            "use_case": "Premium translations, complex documents"
        },
        "aya-expanse:8b": {
            "name": "Aya Expanse 8B",
            "description": "Cohere's multilingual model – excellent for Dutch",
            "size_gb": 5.1,
            "ram_required": 8,
            "quality_stars": 5,
            "strengths": ["Top Dutch support", "High fidelity translation", "23 languages"],
            "use_case": "Dutch-English translation (Top Pick for Dutch)"
        }
    }
    
    # Vision-capable models (support image inputs)
    VISION_MODELS = {
        # Superseded models stay here: a saved choice keeps its image support.
        "openai": [
            "gpt-6-astra",
            "gpt-6.1-sol",
            "gpt-6-luna",
            "gpt-5.5",
            "gpt-5.4-mini"
        ],
        "claude": [
            "claude-sonnet-5-5",
            "claude-opus-5-5",
            "claude-fable-5-1",
            "claude-sonnet-5",
            "claude-haiku-4-5-20251001",
            "claude-opus-5",
            "claude-fable-5"
        ],
        "gemini": [
            "gemini-3.1-flash-lite",
            "gemini-3.5-flash",
            "gemini-2.5-pro",
            "gemini-3.1-pro-preview"
        ]
    }

    # OpenAI's three current tiers, most capable to cheapest - the three OpenAI
    # itself offers as its main choices (developers.openai.com/api/docs/models,
    # checked 2026-10-07). Michael's rule (2026-10-07): the best and latest three,
    # most expensive first, the middle one the default - as in the Trados and memoQ
    # plugins. All are reasoning models, sent no temperature. A saved older model
    # (GPT-5.5, GPT-5.4 Mini) still runs and is still costed (pricing.json keeps it).
    OPENAI_MODELS = {
        "gpt-6-astra": {
            "name": "GPT-6 Astra",
            "description": "OpenAI's most capable model ($10/$50), 1M context",
            "released": "2026-09-04",
            "strengths": ["Hardest translation problems", "Complex reasoning", "Highest accuracy", "1M context"],
            "pricing": {"input": 10, "output": 50},  # USD per million tokens
            "use_case": "For the hardest work, when cost is secondary"
        },
        "gpt-6.1-sol": {
            "name": "GPT-6.1 Sol",
            "description": "Close to Astra's quality at a fifth of its price ($2/$10), 1M context",
            "released": "2026-09-29",
            "strengths": ["General translation", "Complex reasoning", "Multilingual", "1M context"],
            "pricing": {"input": 2, "output": 10},  # USD per million tokens
            "use_case": "Recommended for most translation tasks"
        },
        "gpt-6-luna": {
            "name": "GPT-6 Luna",
            "description": "OpenAI's cheapest ($0.10/$0.50), 1M context",
            "released": "2026-09-22",
            "strengths": ["Fast", "Cost-effective", "Multilingual"],
            "pricing": {"input": 0.10, "output": 0.50},  # USD per million tokens
            "use_case": "High-volume work where cost matters most"
        }
    }

    # Available Claude models with descriptions - the chat's model menu. The newest
    # Sonnet, Opus and Fable (Michael, 2026-09-28), as in the Trados and memoQ
    # plugins; prices checked on Anthropic's pricing page the same day. A saved
    # older model still runs and is still costed (pricing.json keeps it).
    CLAUDE_MODELS = {
        "claude-sonnet-5-5": {
            "name": "Claude Sonnet 5.5",
            "description": "Newest Sonnet - near-Opus quality at Sonnet cost ($2/$10), 1M context",
            "released": "2026-09-28",
            "strengths": ["General translation", "Reasoning", "Tool use", "Knowledge work", "Cost-effective"],
            "pricing": {"input": 2, "output": 10},  # USD per million tokens
            "use_case": "Recommended for most translation tasks"
        },
        "claude-opus-5-5": {
            "name": "Claude Opus 5.5",
            "description": "Anthropic's newest Opus - highest quality at $4/$20, 1M context",
            "released": "2026-09-22",
            "strengths": ["Legal translation", "Technical documents", "Complex reasoning", "Highest accuracy", "1M context"],
            "pricing": {"input": 4, "output": 20},  # USD per million tokens
            "use_case": "Top choice for hard legal/technical translation and long-context jobs"
        },
        "claude-fable-5-1": {
            "name": "Claude Fable 5.1",
            "description": "Anthropic's most capable model - deepest reasoning, always-on thinking, 2.5x Opus pricing",
            "released": "2026-09-01",
            "strengths": ["Hardest translation problems", "Whole-document review", "Deepest reasoning", "1M context"],
            "pricing": {"input": 10, "output": 50},  # USD per million tokens
            "use_case": "For the hardest jobs only - always-on thinking adds billed reasoning tokens per call, so overkill for routine segment translation"
        }
    }

    # Reasoning models: sent no temperature, given the long timeout and a large
    # output budget (their thinking is drawn from it). GPT-6 reasons on every
    # request; 6.1 Sol and Astra cannot turn it off at all.
    REASONING_MODELS = ["gpt-5", "gpt-6", "o1", "o3"]

    @classmethod
    def get_claude_model_info(cls, model_id: Optional[str] = None) -> Dict:
        """
        Get information about available Claude models

        Args:
            model_id: Specific model ID to get info for, or None for all models

        Returns:
            Dict with model information

        Example:
            # Get all models
            models = LLMClient.get_claude_model_info()
            for model_id, info in models.items():
                print(f"{info['name']}: {info['description']}")

            # Get specific model
            info = LLMClient.get_claude_model_info("claude-sonnet-5-5")
            print(info['use_case'])
        """
        if model_id:
            return cls.CLAUDE_MODELS.get(model_id, {})
        return cls.CLAUDE_MODELS
    
    @classmethod
    def get_ollama_model_info(cls, model_id: Optional[str] = None) -> Dict:
        """
        Get information about available Ollama models
        
        Args:
            model_id: Specific model ID to get info for, or None for all models
            
        Returns:
            Dict with model information
        """
        if model_id:
            return cls.OLLAMA_MODELS.get(model_id, {})
        return cls.OLLAMA_MODELS
    
    @classmethod
    def check_ollama_status(cls, endpoint: str = None) -> Dict:
        """
        Check if Ollama is running and get available models
        
        Args:
            endpoint: Ollama API endpoint (default: http://localhost:11434)
            
        Returns:
            Dict with:
                - running: bool - whether Ollama is running
                - models: list - available model names
                - error: str - error message if not running
        """
        import requests

        endpoint = endpoint or os.environ.get('OLLAMA_ENDPOINT', 'http://localhost:11434')
        endpoint = _sanitize_ollama_endpoint(endpoint)

        try:
            # Check if Ollama is running
            response = requests.get(f"{endpoint}/api/tags", timeout=5)
            if response.status_code == 200:
                data = response.json()
                models = [m['name'] for m in data.get('models', [])]
                return {
                    'running': True,
                    'models': models,
                    'endpoint': endpoint,
                    'error': None
                }
            else:
                error_msg = f"Ollama returned status {response.status_code}"
                try:
                    root_resp = requests.get(endpoint, timeout=3)
                    if root_resp.status_code == 200 and 'ollama' in root_resp.text.lower():
                        error_msg = (f"Ollama is reachable but /api/tags returned {response.status_code}. "
                                     f"Check your Ollama endpoint – it should be just the base URL "
                                     f"(e.g. http://localhost:11434), not including /api or /v1.")
                except:
                    pass
                return {
                    'running': False,
                    'models': [],
                    'endpoint': endpoint,
                    'error': error_msg
                }
        except requests.exceptions.ConnectionError:
            return {
                'running': False,
                'models': [],
                'endpoint': endpoint,
                'error': "Cannot connect to Ollama. Please ensure Ollama is installed and running."
            }
        except Exception as e:
            return {
                'running': False,
                'models': [],
                'endpoint': endpoint,
                'error': str(e)
            }
    
    @classmethod
    def model_supports_vision(cls, provider: str, model_name: str) -> bool:
        """
        Check if a model supports vision (image) inputs
        
        Args:
            provider: Provider name ("openai", "claude", "gemini")
            model_name: Model identifier
            
        Returns:
            True if model supports vision, False otherwise
        """
        provider = provider.lower()
        vision_models = cls.VISION_MODELS.get(provider, [])
        return model_name in vision_models

    def __init__(self, api_key: str = None, provider: str = "openai", model: Optional[str] = None, max_tokens: int = 16384, base_url: Optional[str] = None, http_proxy: Optional[str] = None):
        """
        Initialize LLM client

        Args:
            api_key: API key for the provider (not required for 'ollama')
            provider: "openai", "claude", "gemini", "ollama", or "custom_openai"
            model: Model name (uses default if None)
            max_tokens: Maximum tokens for responses (default: 16384)
            base_url: Custom API base URL (for custom_openai provider)
            http_proxy: Optional HTTP/HTTPS proxy URL, e.g. "http://user:pass@host:8080"
        """
        self.provider = provider.lower()
        self.api_key = api_key
        self.model = model or self.DEFAULT_MODELS.get(self.provider)
        self.max_tokens = max_tokens
        self.base_url = base_url
        self.http_proxy = http_proxy  # e.g. "http://user:pass@127.0.0.1:8080"

        if not self.model:
            raise ValueError(f"Unknown provider: {provider}")

        # Validate API key for cloud providers (not needed for Ollama)
        if self.provider not in ("ollama", "custom_openai") and not self.api_key:
            # A key that can't be read on this computer says so (M2), so a
            # missing key is never sent to the provider as a blank one.
            raise ValueError(api_key_problem(self.provider) or f"API key required for provider: {provider}")

        # For Mistral, set the base URL if not already specified
        if self.provider == "mistral" and not self.base_url:
            self.base_url = "https://api.mistral.ai/v1"

        # For DeepSeek, set the base URL if not already specified
        if self.provider == "deepseek" and not self.base_url:
            self.base_url = "https://api.deepseek.com/v1"

        # For OpenRouter, set the base URL and extra headers
        if self.provider == "openrouter" and not self.base_url:
            self.base_url = "https://openrouter.ai/api/v1"

        # Auto-detect temperature based on model
        self.temperature = self._get_temperature()
    
    def _clean_translation_response(self, translation: str, prompt: str) -> str:
        """
        Clean translation response to remove any prompt remnants.
        
        Sometimes LLMs translate the entire prompt instead of just the source text.
        This method attempts to extract only the actual translation.
        
        Args:
            translation: Raw translation response from LLM
            prompt: Original prompt sent to LLM
        
        Returns:
            Cleaned translation text
        """
        if not translation:
            return translation
        
        # First, try to find the delimiter we added ("**YOUR TRANSLATION**")
        # Everything after this delimiter should be the actual translation
        delimiter_markers = [
            "**YOUR TRANSLATION (provide ONLY the translated text, no numbering or labels):**",
            "**YOUR TRANSLATION**",
            "**YOUR TRANSLATION (provide ONLY",
            "**JOUW VERTALING**",
            "**TRANSLATION**",
            "**VERTALING**",
            "Translation:",
            "Vertaling:",
            "YOUR TRANSLATION",
            "JOUW VERTALING",
        ]
        
        # Try to split on delimiter first (most reliable)
        import re
        for marker in delimiter_markers:
            # Use word boundary or newline before marker for better matching
            pattern = re.escape(marker)
            # Try with newline before it
            pattern_with_newline = r'\n\s*' + pattern
            match = re.search(pattern_with_newline, translation, re.IGNORECASE | re.MULTILINE)
            if not match:
                # Try without newline requirement
                match = re.search(pattern, translation, re.IGNORECASE)
            
            if match:
                result = translation[match.end():].strip()
                # Clean up any leading/trailing newlines, colons, or whitespace
                result = re.sub(r'^[::\s\n\r]+', '', result)
                result = result.strip()
                if result:
                    # Additional cleanup: remove any remaining prompt patterns
                    result = self._remove_prompt_patterns(result)
                    if result and len(result) < len(translation) * 0.9:  # Must be significantly shorter
                        return result
        
        # Common patterns that indicate the prompt was translated
        # These are translations of common prompt phrases
        prompt_patterns = [
            # Dutch translations of prompt instructions
            "Als een professionele",
            "Als professionele",
            "U bent een expert",
            "Uw taak is om",
            "Tijdens het vertaalproces",
            "De output moet uitsluitend bestaan",
            "Waarschuwingsinformatie:",
            "⚠️ PROFESSIONELE VERTAALCONTEXT:",
            "vertaler",
            "handleidingen",
            "regelgeving",
            "naleving",
            "medische apparaten",
            "professionele doeleinden",
            "medisch advies",
            "volledige documentcontext",
            "tekstsegmenten",
            "CAT-tool tags",
            "memoQ-tags",
            "Trados Studio-tags",
            "CafeTran-tags",
            # English patterns (in case language is mixed)
            "As a professional",
            "You are an expert",
            "Your task is to",
            "During the translation process",
            "The output must consist exclusively",
            "⚠️ PROFESSIONAL TRANSLATION CONTEXT:",
            "professional translation",
            "technical manuals",
            "regulatory compliance",
            "medical devices",
            "professional purposes",
            "medical advice",
            "full document context",
            "text segments",
            "CAT tool tags",
            "memoQ tags",
            "Trados Studio tags",
            "CafeTran tags",
        ]
        
        # Check if translation contains prompt patterns - if so, it's likely a translated prompt
        translation_lower = translation.lower()
        prompt_pattern_count = sum(1 for pattern in prompt_patterns if pattern.lower() in translation_lower)
        
        # If translation is suspiciously long and contains many prompt patterns, it's likely a translated prompt
        if len(translation) > 300 and prompt_pattern_count >= 3:
            # Try to find where actual translation starts
            # Look for the end of the last prompt-like sentence
            lines = translation.split('\n')
            cleaned_lines = []
            found_actual_translation = False
            
            for i, line in enumerate(lines):
                line_stripped = line.strip()
                if not line_stripped:
                    if found_actual_translation:
                        cleaned_lines.append(line)
                    continue
                
                # Check if this line looks like prompt instruction
                is_prompt = any(pattern.lower() in line_stripped.lower() for pattern in prompt_patterns)
                
                # Also check if it's a very long line (likely prompt instructions)
                if len(line_stripped) > 200:
                    prompt_phrases = sum(1 for pattern in prompt_patterns if pattern.lower() in line_stripped.lower())
                    if prompt_phrases >= 2:
                        is_prompt = True
                
                if is_prompt:
                    # Skip prompt lines
                    continue
                else:
                    # This might be actual translation
                    found_actual_translation = True
                    cleaned_lines.append(line)
            
            result = '\n'.join(cleaned_lines).strip()
            if result and len(result) < len(translation) * 0.7:  # Significantly shorter = likely cleaned correctly
                return self._remove_prompt_patterns(result)
        
        # Final cleanup: remove any remaining prompt patterns
        cleaned = self._remove_prompt_patterns(translation)
        
        # If cleaned version is much shorter, it was likely cleaned correctly
        if cleaned != translation and len(cleaned) < len(translation) * 0.8:
            return cleaned
        
        return translation
    
    def _remove_prompt_patterns(self, text: str) -> str:
        """Remove prompt-like patterns from text"""
        prompt_patterns = [
            "Als een professionele", "Als professionele", "U bent een expert",
            "Uw taak is om", "Tijdens het vertaalproces", "De output moet",
            "Waarschuwingsinformatie:", "⚠️ PROFESSIONELE", "vertaler",
            "handleidingen", "regelgeving", "naleving", "medische apparaten",
            "professionele doeleinden", "medisch advies", "volledige documentcontext",
            "tekstsegmenten", "CAT-tool tags", "memoQ-tags", "Trados Studio-tags",
            "CafeTran-tags", "As a professional", "You are an expert",
            "Your task is to", "During the translation process",
            "The output must consist exclusively", "⚠️ PROFESSIONAL",
            "professional translation", "technical manuals", "regulatory compliance",
            "medical devices", "professional purposes", "medical advice",
            "full document context", "text segments", "CAT tool tags",
            "memoQ tags", "Trados Studio tags", "CafeTran tags",
        ]
        
        lines = text.split('\n')
        cleaned_lines = []
        
        for line in lines:
            line_lower = line.lower()
            # Skip lines that contain prompt patterns
            has_prompt = any(pattern.lower() in line_lower for pattern in prompt_patterns)
            # Also skip very long lines that might be prompt instructions
            if not has_prompt and (len(line.strip()) < 300 or len(line.strip().split()) < 50):
                cleaned_lines.append(line)
        
        result = '\n'.join(cleaned_lines).strip()
        return result if result else text
    
    def _get_temperature(self) -> Optional[float]:
        """Determine optimal temperature for model (None means omit parameter)"""
        model_lower = self.model.lower()
        
        # Reasoning models don't support temperature parameter - return None to omit it
        if any(reasoning in model_lower for reasoning in self.REASONING_MODELS):
            return None
        
        # Standard models use 0.3 for consistency
        return 0.3
    
    def translate(
        self,
        text: str,
        source_lang: str = "en",
        target_lang: str = "nl",
        context: Optional[str] = None,
        custom_prompt: Optional[str] = None,
        max_tokens: Optional[int] = None,
        images: Optional[List] = None,
        system_prompt: Optional[str] = None,
        skip_cleaning: bool = False,
        enable_prompt_caching: bool = False,
    ) -> str:
        """
        Translate text using configured LLM

        Args:
            text: Text to translate
            source_lang: Source language code
            target_lang: Target language code
            context: Optional context for translation
            custom_prompt: Optional custom prompt (overrides default simple prompt)
            system_prompt: Optional system prompt for AI behavior context
            skip_cleaning: If True, skip _clean_translation_response post-processing
                (used for prompt generation where translation-related keywords are expected)

        Returns:
            Translated text
        """
        # Delegate to translate_with_usage so that EVERY translation call flows
        # through the single token-usage logging path (translate_with_usage
        # records to the usage ledger). Behaviour is otherwise identical.
        result, _usage = self.translate_with_usage(
            text=text,
            source_lang=source_lang,
            target_lang=target_lang,
            context=context,
            custom_prompt=custom_prompt,
            max_tokens=max_tokens,
            images=images,
            system_prompt=system_prompt,
            skip_cleaning=skip_cleaning,
            enable_prompt_caching=enable_prompt_caching,
        )
        return result

    def translate_with_usage(
        self,
        text: str,
        source_lang: str = "en",
        target_lang: str = "nl",
        context: Optional[str] = None,
        custom_prompt: Optional[str] = None,
        max_tokens: Optional[int] = None,
        images: Optional[List] = None,
        system_prompt: Optional[str] = None,
        skip_cleaning: bool = False,
        enable_prompt_caching: bool = False,
    ) -> Tuple[str, Dict]:
        """
        Same as translate() but returns (text, usage_dict).

        usage_dict contains:
            input_tokens: int
            output_tokens: int

        Returns (text, {}) if usage data is unavailable.
        """
        if custom_prompt:
            prompt = custom_prompt
        else:
            prompt = f"Translate the following text from {source_lang} to {target_lang}:\n\n{text}"
            if context:
                prompt = f"Context: {context}\n\n{prompt}"

        if images and not self.model_supports_vision(self.provider, self.model):
            images = None

        _t0 = time.perf_counter()
        if self.provider in ("openai", "custom_openai", "mistral", "deepseek", "openrouter"):
            result, usage = self._call_openai_with_usage(prompt, max_tokens=max_tokens, images=images if self.provider in ("openai", "custom_openai") else None, system_prompt=system_prompt, enable_prompt_caching=enable_prompt_caching)
        elif self.provider == "claude":
            result, usage = self._call_claude_with_usage(prompt, max_tokens=max_tokens, images=images, system_prompt=system_prompt, enable_prompt_caching=enable_prompt_caching)
        elif self.provider == "gemini":
            result, usage = self._call_gemini_with_usage(prompt, max_tokens=max_tokens, images=images, system_prompt=system_prompt)
        elif self.provider == "ollama":
            result = self._call_ollama(prompt, max_tokens=max_tokens, system_prompt=system_prompt)
            usage = {}
        else:
            raise ValueError(f"Unsupported provider: {self.provider}")

        if not skip_cleaning:
            result = self._clean_translation_response(result, prompt)

        # Record this call to the persistent usage ledger (best-effort, metadata
        # only). When the provider returned no usage, estimate via chars/4.
        try:
            from modules import usage_log as _usage_log
            _est_in = 0 if usage else (len(prompt) + 3) // 4
            _est_out = 0 if usage else (len(result) + 3) // 4
            _usage_log.record(
                self.provider, self.model, usage or {},
                estimated_input=_est_in, estimated_output=_est_out,
                duration_s=time.perf_counter() - _t0,
            )
        except Exception:
            pass

        return result, usage

    def _call_openai_with_usage(self, prompt: str, max_tokens: Optional[int] = None, images: Optional[List] = None, system_prompt: Optional[str] = None, enable_prompt_caching: bool = False) -> Tuple[str, Dict]:
        """Call OpenAI-compatible API and return (text, usage_dict).

        enable_prompt_caching only affects OpenRouter when fronting an
        Anthropic model (anthropic/claude-*). In that case the system
        message is sent as a content array with cache_control:ephemeral
        so OpenRouter passes the marker through to Anthropic. For native
        OpenAI / DeepSeek the flag is a no-op – they auto-cache stable
        prefixes ≥1024 tokens with no marker required.
        """
        try:
            from openai import OpenAI
        except ImportError:
            raise ImportError("OpenAI library not installed. Install with: pip install openai")

        model_lower = self.model.lower()
        is_reasoning_model = any(x in model_lower for x in self.REASONING_MODELS)
        timeout_seconds = 600.0 if is_reasoning_model else 120.0

        client_kwargs = {"api_key": self.api_key, "timeout": timeout_seconds}
        if self.base_url:
            client_kwargs["base_url"] = self.base_url
        if self.provider == "openrouter":
            client_kwargs["default_headers"] = {
                "HTTP-Referer": "https://supervertaler.com",
                "X-Title": "Supervertaler"
            }
        if self.http_proxy:
            import httpx
            client_kwargs["http_client"] = httpx.Client(proxy=self.http_proxy, timeout=timeout_seconds)
        client = OpenAI(**client_kwargs)

        if max_tokens is not None:
            tokens_to_use = max_tokens
        elif is_reasoning_model:
            tokens_to_use = 32768
        else:
            tokens_to_use = self.max_tokens

        content = prompt
        if images:
            content = [{"type": "text", "text": prompt}]
            for img_ref, img_base64 in images:
                content.append({"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img_base64}"}})

        # OpenRouter passes cache_control through to Anthropic when expressed
        # via the OpenAI-compatible content-array form. Bare OpenAI/DeepSeek/
        # Mistral/Grok ignore the marker.
        is_openrouter_anthropic = (
            self.provider == "openrouter"
            and self.model.lower().startswith("anthropic/")
        )

        messages = []
        if system_prompt:
            if enable_prompt_caching and is_openrouter_anthropic:
                messages.append({
                    "role": "system",
                    "content": [{
                        "type": "text",
                        "text": system_prompt,
                        "cache_control": {"type": "ephemeral"},
                    }],
                })
            else:
                messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": content})

        api_params = {"model": self.model, "messages": messages, "timeout": timeout_seconds}
        if is_reasoning_model:
            api_params["max_completion_tokens"] = tokens_to_use
        else:
            api_params["max_tokens"] = tokens_to_use
            api_params["temperature"] = self.temperature

        response = client.chat.completions.create(**api_params)

        if not response.choices or not response.choices[0].message.content:
            raise ValueError(f"OpenAI returned empty response for model {self.model}")

        text = response.choices[0].message.content.strip()
        if not text:
            raise ValueError(f"OpenAI returned empty translation for model {self.model}")

        usage = {}
        if hasattr(response, 'usage') and response.usage:
            cached_tokens = 0
            # OpenAI native: prompt_tokens_details.cached_tokens reports the
            # auto-cache hit count (50% off cached input). Available on GPT-4o,
            # GPT-4-turbo, GPT-5.x and the o-series.
            details = getattr(response.usage, 'prompt_tokens_details', None)
            if details is not None:
                cached_tokens = getattr(details, 'cached_tokens', 0) or 0
            # OpenRouter → Anthropic flavour: passes Anthropic's cache fields
            # through directly on the usage object.
            if not cached_tokens:
                cached_tokens = getattr(response.usage, 'cache_read_input_tokens', 0) or 0
            cache_creation_tokens = (
                getattr(response.usage, 'cache_creation_input_tokens', 0) or 0
            )

            usage = {
                'input_tokens': getattr(response.usage, 'prompt_tokens', 0) or 0,
                'output_tokens': getattr(response.usage, 'completion_tokens', 0) or 0,
                # Tokens served from cache, billed at the provider's cache-read
                # rate (50% for OpenAI auto-cache, 10% for Anthropic via
                # OpenRouter, 10% for DeepSeek auto-cache).
                'cache_read_input_tokens': cached_tokens,
                # Tokens written to the cache on this call (Anthropic only;
                # billed at 1.25× input). Zero for native OpenAI / DeepSeek
                # which auto-cache without a separate write step.
                'cache_creation_input_tokens': cache_creation_tokens,
            }

        return text, usage

    def _call_claude_with_usage(self, prompt: str, max_tokens: Optional[int] = None, images: Optional[List] = None, system_prompt: Optional[str] = None, enable_prompt_caching: bool = False) -> Tuple[str, Dict]:
        """Call Claude API and return (text, usage_dict).

        enable_prompt_caching: caller asserts the system prompt will be reused
        byte-identically across multiple calls within ~5 minutes (e.g. batch
        translate sending one big system prompt with N batches of segments).
        When True, the system prompt is sent as a content block with
        cache_control:ephemeral so cache writes are billed at 1.25× and
        cache reads at 0.1× of the normal input rate.
        """
        try:
            import anthropic
        except ImportError:
            raise ImportError("Anthropic library not installed. Install with: pip install anthropic")

        prompt_length = len(prompt)
        if prompt_length > 50000:
            timeout_seconds = 300.0
        elif prompt_length > 20000:
            timeout_seconds = 180.0
        else:
            timeout_seconds = 120.0

        claude_kwargs = {"api_key": self.api_key, "timeout": timeout_seconds}
        if self.http_proxy:
            import httpx
            claude_kwargs["http_client"] = httpx.Client(proxy=self.http_proxy, timeout=timeout_seconds)
        client = anthropic.Anthropic(**claude_kwargs)

        tokens_to_use = max_tokens if max_tokens is not None else self.max_tokens

        if images:
            content = []
            for img_ref, img_base64 in images:
                content.append({"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": img_base64}})
            content.append({"type": "text", "text": prompt})
        else:
            content = prompt

        api_params = {
            "model": self.model,
            "max_tokens": tokens_to_use,
            "messages": [{"role": "user", "content": content}],
            "timeout": timeout_seconds
        }
        if system_prompt:
            if enable_prompt_caching:
                # Mark the system prompt as ephemeral-cached. First call within a
                # ~5-minute window pays 1.25× the input rate (cache write); subsequent
                # calls with the byte-identical system prompt pay 0.1× (cache read).
                api_params["system"] = [{
                    "type": "text",
                    "text": system_prompt,
                    "cache_control": {"type": "ephemeral"},
                }]
            else:
                api_params["system"] = system_prompt

        response = client.messages.create(**api_params)

        if not response.content:
            raise ValueError("Claude returned an empty response (no content blocks)")
        # With extended thinking enabled, response.content[0] can be a
        # ThinkingBlock (which has no .text). Pull the text block(s)
        # specifically and skip thinking/redacted/tool blocks.
        text = "".join(
            b.text for b in response.content
            if getattr(b, 'type', None) == 'text'
        ).strip()
        if not text:
            raise ValueError("Claude returned no text content (only non-text blocks)")

        usage = {}
        if hasattr(response, 'usage') and response.usage:
            # Anthropic reports `input_tokens` as the UNCACHED portion only;
            # the cached portion is in cache_creation/cache_read fields.
            # Normalise to "total input including cache" so the dict shape
            # matches OpenAI / Gemini conventions and downstream cost
            # estimation code works the same way for every provider.
            regular_in = getattr(response.usage, 'input_tokens', 0) or 0
            cache_creation = getattr(response.usage, 'cache_creation_input_tokens', 0) or 0
            cache_read = getattr(response.usage, 'cache_read_input_tokens', 0) or 0

            usage = {
                'input_tokens': regular_in + cache_creation + cache_read,
                'output_tokens': getattr(response.usage, 'output_tokens', 0) or 0,
                # Tokens served from cache, billed at 0.1× the input rate.
                'cache_read_input_tokens': cache_read,
                # Tokens written to cache on this call, billed at 1.25× input.
                # (Anthropic-only field; OpenAI auto-cache has no separate write.)
                'cache_creation_input_tokens': cache_creation,
            }

        return text, usage

    def _call_gemini_with_usage(self, prompt: str, max_tokens: Optional[int] = None, images: Optional[List] = None, system_prompt: Optional[str] = None) -> Tuple[str, Dict]:
        """Call Gemini API and return (text, usage_dict)."""
        try:
            import google.generativeai as genai
        except ImportError:
            raise ImportError("Google AI library not installed. Install with: pip install google-generativeai")

        genai.configure(api_key=self.api_key)

        if system_prompt:
            model = genai.GenerativeModel(self.model, system_instruction=system_prompt)
        else:
            model = genai.GenerativeModel(self.model)

        if images:
            content = [prompt]
            for img_ref, pil_image in images:
                content.append(pil_image)
        else:
            content = prompt

        response = model.generate_content(content)

        # Defensive .text access: response.text raises ValueError when the
        # candidate was blocked, finish_reason isn't STOP, or a thinking
        # model spent its whole budget on thoughts and left no text part.
        # Surface the actual cause instead of a generic ValueError (mirrors
        # _call_gemini).
        try:
            text = response.text.strip()
        except (ValueError, AttributeError) as e:
            details = []
            try:
                if response.prompt_feedback and response.prompt_feedback.block_reason:
                    details.append(
                        f"prompt blocked: {response.prompt_feedback.block_reason}"
                    )
            except Exception:
                pass
            try:
                for i, cand in enumerate(response.candidates or []):
                    fr = getattr(cand, 'finish_reason', None)
                    if fr is not None:
                        details.append(f"candidate {i} finish_reason={fr}")
                    sr = getattr(cand, 'safety_ratings', None) or []
                    blocked = [
                        f"{getattr(r, 'category', '?')}={getattr(r, 'probability', '?')}"
                        for r in sr if getattr(r, 'blocked', False)
                    ]
                    if blocked:
                        details.append(f"candidate {i} safety: {', '.join(blocked)}")
            except Exception:
                pass
            reason = "; ".join(details) if details else f"{type(e).__name__}: {e}"
            raise RuntimeError(f"Gemini returned no usable text ({reason})") from e

        usage = {}
        if hasattr(response, 'usage_metadata') and response.usage_metadata:
            um = response.usage_metadata
            # Gemini 2.5+ implicit caching: when the prompt prefix is stable
            # and ≥1024 tokens, the API reports the cached portion in
            # cached_content_token_count. Cached tokens are billed at 25% of
            # the input rate (75% discount).
            cached_tokens = getattr(um, 'cached_content_token_count', 0) or 0

            usage = {
                'input_tokens': getattr(um, 'prompt_token_count', 0) or 0,
                'output_tokens': getattr(um, 'candidates_token_count', 0) or 0,
                # Normalised cache fields, matching the Anthropic / OpenAI shape.
                # cache_read = tokens served from cache (75% off for Gemini).
                # cache_creation is N/A for Gemini's implicit cache and stays 0.
                'cache_read_input_tokens': cached_tokens,
                'cache_creation_input_tokens': 0,
            }

        return text, usage

    def _call_openai(self, prompt: str, max_tokens: Optional[int] = None, images: Optional[List] = None, system_prompt: Optional[str] = None, enable_prompt_caching: bool = False) -> str:
        """Call OpenAI API with GPT-5/o1/o3 reasoning model support and vision capability.

        enable_prompt_caching: see _call_openai_with_usage. Only effective for
        OpenRouter→Anthropic; OpenAI / DeepSeek auto-cache without a marker.
        """
        print(f"🔵 _call_openai START: model={self.model}, prompt_len={len(prompt)}, max_tokens={max_tokens}, images={len(images) if images else 0}, has_system={bool(system_prompt)}")

        try:
            from openai import OpenAI
        except ImportError:
            raise ImportError(
                "OpenAI library not installed. Install with: pip install openai"
            )

        # Detect if this is a reasoning model (GPT-5, GPT-6, o1, o3)
        model_lower = self.model.lower()
        is_reasoning_model = any(x in model_lower for x in self.REASONING_MODELS)

        # Reasoning models need MUCH longer timeout (they can take 5-10 minutes for large prompts)
        timeout_seconds = 600.0 if is_reasoning_model else 120.0  # 10 min vs 2 min
        client_kwargs = {"api_key": self.api_key, "timeout": timeout_seconds}
        if self.base_url:
            client_kwargs["base_url"] = self.base_url
        if self.provider == "openrouter":
            client_kwargs["default_headers"] = {
                "HTTP-Referer": "https://supervertaler.com",
                "X-Title": "Supervertaler"
            }
        if self.http_proxy:
            import httpx
            client_kwargs["http_client"] = httpx.Client(proxy=self.http_proxy, timeout=timeout_seconds)
        client = OpenAI(**client_kwargs)
        print(f"🔵 OpenAI client created successfully (timeout: {timeout_seconds}s)")

        # Use provided max_tokens or default
        # IMPORTANT: Reasoning models need MUCH higher limits because they use tokens for:
        # 1. Internal reasoning/thinking (can be thousands of tokens)
        # 2. The actual response content
        # If limit is too low, all tokens get used for reasoning and response is empty!
        if max_tokens is not None:
            tokens_to_use = max_tokens
        elif is_reasoning_model:
            # For reasoning models, use 32K tokens (GPT-5 supports up to 65K)
            # This gives plenty of room for both reasoning and response
            tokens_to_use = 32768
        else:
            tokens_to_use = self.max_tokens

        print(f"🔵 Is reasoning model: {is_reasoning_model}, tokens_to_use: {tokens_to_use}")

        # Build message content (text + optional images)
        if images:
            # Vision API format: content as array with text and image_url objects
            content = [{"type": "text", "text": prompt}]
            for img_ref, img_base64 in images:
                content.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{img_base64}"}
                })
            print(f"🔵 Vision mode: {len(images)} images added to message")
        else:
            # Standard text-only format
            content = prompt

        # OpenRouter → Anthropic: pass cache_control through via OpenAI content array.
        is_openrouter_anthropic = (
            self.provider == "openrouter"
            and self.model.lower().startswith("anthropic/")
        )

        # Build messages list
        messages = []
        if system_prompt:
            if enable_prompt_caching and is_openrouter_anthropic:
                messages.append({
                    "role": "system",
                    "content": [{
                        "type": "text",
                        "text": system_prompt,
                        "cache_control": {"type": "ephemeral"},
                    }],
                })
            else:
                messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": content})

        # Build API call parameters
        api_params = {
            "model": self.model,
            "messages": messages,
            "timeout": timeout_seconds
        }

        if is_reasoning_model:
            # Reasoning models (gpt-5, o1, o3-mini) require specific parameters
            # - Use max_completion_tokens instead of max_tokens
            # - DO NOT include temperature parameter (it's not supported)
            api_params["max_completion_tokens"] = tokens_to_use
            # Note: Temperature parameter is OMITTED for reasoning models (not supported)
            # Note: reasoning_effort is also OMITTED - without it, GPT-5 is much faster
            print(f"🔵 Reasoning model params: max_completion_tokens={tokens_to_use}, no reasoning_effort (faster)")
        else:
            # Standard models (gpt-4o, gpt-4-turbo, etc.)
            api_params["max_tokens"] = tokens_to_use
            api_params["temperature"] = self.temperature
            print(f"🔵 Standard model params: max_tokens={tokens_to_use}, temperature={self.temperature}")

        try:
            print(f"🔵 Calling OpenAI API...")
            response = client.chat.completions.create(**api_params)
            print(f"🔵 OpenAI API call completed")

            # Check if response has content
            if not response.choices or not response.choices[0].message.content:
                error_msg = f"OpenAI returned empty response for model {self.model}"
                print(f"❌ ERROR: {error_msg}")
                raise ValueError(error_msg)

            translation = response.choices[0].message.content.strip()

            # Check if translation is empty after stripping
            if not translation:
                error_msg = f"OpenAI returned empty translation after stripping for model {self.model}"
                print(f"❌ ERROR: {error_msg}")
                print(f"Raw response: {response.choices[0].message.content}")
                raise ValueError(error_msg)

            return translation

        except Exception as e:
            # Log the actual error with context
            print(f"❌ OpenAI API Error (model: {self.model})")
            print(f"   Error type: {type(e).__name__}")
            print(f"   Error message: {str(e)}")
            print(f"   Prompt length: {len(prompt)} characters")
            if hasattr(e, 'response'):
                print(f"   Response: {e.response}")
            raise  # Re-raise to be caught by calling code
    
    def _call_claude(self, prompt: str, max_tokens: Optional[int] = None, images: Optional[List] = None, system_prompt: Optional[str] = None, enable_prompt_caching: bool = False) -> str:
        """Call Anthropic Claude API with vision support.

        enable_prompt_caching: when True, the system prompt is sent as a
        content block with cache_control:ephemeral so subsequent calls
        within ~5 minutes that share the same system prompt pay 0.1× the
        input rate on the cached portion. Pass False for one-shot calls
        (chat, single-segment translate) – the 1.25× write surcharge is
        wasted there.
        """
        try:
            import anthropic
        except ImportError:
            raise ImportError(
                "Anthropic library not installed. Install with: pip install anthropic"
            )
        
        # Use longer timeout for batch operations (detected by large prompts)
        # Opus 4.1 can take longer to process, especially with extended context
        prompt_length = len(prompt)
        if prompt_length > 50000:  # Large batch prompt
            timeout_seconds = 300.0  # 5 minutes for very large prompts
        elif prompt_length > 20000:  # Medium batch prompt
            timeout_seconds = 180.0  # 3 minutes
        else:
            timeout_seconds = 120.0  # 2 minutes for normal operations
        
        claude_kwargs = {"api_key": self.api_key, "timeout": timeout_seconds}
        if self.http_proxy:
            import httpx
            claude_kwargs["http_client"] = httpx.Client(proxy=self.http_proxy, timeout=timeout_seconds)
        client = anthropic.Anthropic(**claude_kwargs)
        
        # Use provided max_tokens or default (Claude uses 4096 as default)
        tokens_to_use = max_tokens if max_tokens is not None else self.max_tokens
        
        # Build message content (text + optional images)
        if images:
            # Claude vision format: content as array with text and image objects
            content = []
            for img_ref, img_base64 in images:
                content.append({
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/png",
                        "data": img_base64
                    }
                })
            # Add text after images
            content.append({"type": "text", "text": prompt})
            print(f"🟣 Claude vision mode: {len(images)} images added to message")
        else:
            # Standard text-only format
            content = prompt
        
        # Build API call parameters
        api_params = {
            "model": self.model,
            "max_tokens": tokens_to_use,
            "messages": [{"role": "user", "content": content}],
            "timeout": timeout_seconds  # Explicit timeout
        }

        # Add system prompt if provided (Claude uses 'system' parameter, not a message)
        if system_prompt:
            if enable_prompt_caching:
                # See _call_claude_with_usage for the rationale on cache_control.
                api_params["system"] = [{
                    "type": "text",
                    "text": system_prompt,
                    "cache_control": {"type": "ephemeral"},
                }]
            else:
                api_params["system"] = system_prompt

        response = client.messages.create(**api_params)

        if not response.content:
            raise ValueError("Claude returned an empty response (no content blocks)")
        # With extended thinking enabled, response.content[0] can be a
        # ThinkingBlock (which has no .text). Pull the text block(s)
        # specifically and skip thinking/redacted/tool blocks.
        translation = "".join(
            b.text for b in response.content
            if getattr(b, 'type', None) == 'text'
        ).strip()

        return translation

    def _call_gemini(self, prompt: str, max_tokens: Optional[int] = None, images: Optional[List] = None, system_prompt: Optional[str] = None) -> str:
        """Call Google Gemini API with vision support."""
        # gemini-3.5+ forces "thinking" (default level=medium), which bills
        # hundreds of reasoning tokens at the output rate even for tiny
        # translation segments (~65x the cost of flash-lite for no quality
        # gain on short text). The thinking level can only be lowered via
        # thinkingConfig, which the legacy google-generativeai 0.8.5 SDK
        # cannot pass. Route 3.5+ models through REST with thinking pinned
        # to "minimal"; all other models stay on the proven SDK path below.
        if self.model.startswith("gemini-3.5"):
            return self._call_gemini_rest(
                prompt, max_tokens=max_tokens, images=images,
                system_prompt=system_prompt, thinking_level="minimal",
            )

        # v1.10.34: preserve the original exception text in the
        # ImportError. The old "Google AI library not installed"
        # blanket message was misleading – it fired even when the
        # package was installed but failed at import time for an
        # unrelated reason (e.g. a transitive dependency tripping
        # on a missing stdlib module in a PyInstaller bundle that
        # excluded ``unittest``). v1.10.33's Windows EXE hit
        # exactly that case: pyparsing.testing imports unittest at
        # module load and the spec file had unittest in excludes,
        # so importing google.generativeai threw
        # ModuleNotFoundError but users saw the misleading message.
        try:
            import google.generativeai as genai
            from PIL import Image
        except ImportError as e:
            raise ImportError(
                f"Could not load Google Gemini SDK: {type(e).__name__}: {e}. "
                "If 'pip list' shows google-generativeai is installed, "
                "this is likely a transitive-import failure (often a "
                "stdlib module excluded from a frozen bundle). Otherwise "
                "install with: pip install google-generativeai pillow"
            )

        genai.configure(api_key=self.api_key)

        # Gemini supports system instructions via GenerativeModel parameter
        if system_prompt:
            model = genai.GenerativeModel(self.model, system_instruction=system_prompt)
        else:
            model = genai.GenerativeModel(self.model)

        # Build content (text + optional images)
        if images:
            # Gemini format: list with prompt text followed by PIL Image objects
            content = [prompt]
            for img_ref, pil_image in images:
                content.append(pil_image)  # Gemini accepts PIL.Image directly
            print(f"🟢 Gemini vision mode: {len(images)} images added to message")
        else:
            # Standard text-only
            content = prompt

        response = model.generate_content(content)

        # Defensive .text access: response.text raises ValueError
        # ("response did not contain a valid Part") when the candidate
        # was blocked or finish_reason isn't STOP. Surface the actual
        # cause instead of letting a generic ValueError bubble up.
        try:
            translation = response.text.strip()
        except (ValueError, AttributeError) as e:
            details = []
            try:
                if response.prompt_feedback and response.prompt_feedback.block_reason:
                    details.append(
                        f"prompt blocked: {response.prompt_feedback.block_reason}"
                    )
            except Exception:
                pass
            try:
                for i, cand in enumerate(response.candidates or []):
                    fr = getattr(cand, 'finish_reason', None)
                    if fr is not None:
                        details.append(f"candidate {i} finish_reason={fr}")
                    sr = getattr(cand, 'safety_ratings', None) or []
                    blocked = [
                        f"{getattr(r, 'category', '?')}={getattr(r, 'probability', '?')}"
                        for r in sr if getattr(r, 'blocked', False)
                    ]
                    if blocked:
                        details.append(f"candidate {i} safety: {', '.join(blocked)}")
            except Exception:
                pass
            reason = "; ".join(details) if details else f"{type(e).__name__}: {e}"
            raise RuntimeError(f"Gemini returned no usable text ({reason})") from e

        return translation

    def _call_gemini_rest(self, prompt: str, max_tokens: Optional[int] = None,
                          images: Optional[List] = None, system_prompt: Optional[str] = None,
                          thinking_level: str = "minimal") -> str:
        """Call Gemini via REST so thinkingConfig can be set (legacy SDK can't).

        Used for gemini-3.5+ to pin the thinking level to "minimal" and avoid
        the large hidden reasoning-token cost on short translation segments.
        """
        import base64
        import io
        import json as json_module
        import requests

        parts = [{"text": prompt}]
        if images:
            from PIL import Image  # noqa: F401  (validates Pillow is present)
            for img_ref, pil_image in images:
                buf = io.BytesIO()
                pil_image.save(buf, format="PNG")
                parts.append({
                    "inline_data": {
                        "mime_type": "image/png",
                        "data": base64.b64encode(buf.getvalue()).decode("ascii"),
                    }
                })
            print(f"🟢 Gemini vision mode (REST): {len(images)} images added to message")

        gen_config: Dict = {"thinkingConfig": {"thinkingLevel": thinking_level}}
        if max_tokens:
            gen_config["maxOutputTokens"] = max_tokens

        body: Dict = {
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": gen_config,
        }
        if system_prompt:
            body["systemInstruction"] = {"parts": [{"text": system_prompt}]}

        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent"
        resp = requests.post(
            url,
            headers={"Content-Type": "application/json", "x-goog-api-key": self.api_key},
            data=json_module.dumps(body),
            timeout=120,
        )
        if resp.status_code != 200:
            raise RuntimeError(f"Gemini REST error {resp.status_code}: {resp.text[:500]}")

        data = resp.json()
        try:
            cand = data["candidates"][0]
            # Skip "thought" parts so a thinking model's reasoning summary
            # never leaks into the returned text (the SDK's .text accessor
            # excludes them too).
            text = "".join(
                p.get("text", "") for p in cand["content"]["parts"]
                if not p.get("thought")
            ).strip()
        except (KeyError, IndexError, TypeError) as e:
            details = []
            fb = data.get("promptFeedback", {})
            if fb.get("blockReason"):
                details.append(f"prompt blocked: {fb['blockReason']}")
            for i, c in enumerate(data.get("candidates", []) or []):
                if c.get("finishReason"):
                    details.append(f"candidate {i} finish_reason={c['finishReason']}")
            reason = "; ".join(details) if details else f"{type(e).__name__}: {e}"
            raise RuntimeError(f"Gemini returned no usable text ({reason})") from e

        if not text:
            raise RuntimeError("Gemini returned an empty response")
        return text

    def _call_ollama(self, prompt: str, max_tokens: Optional[int] = None, system_prompt: Optional[str] = None) -> str:
        """
        Call local Ollama server for translation.

        Ollama provides a simple REST API compatible with local LLM inference.
        Models run entirely on the user's computer - no API keys, no internet required.

        Args:
            prompt: The full prompt to send
            max_tokens: Maximum tokens to generate (default: 4096)
            system_prompt: Optional system prompt for AI behavior context

        Returns:
            Translated text

        Raises:
            ConnectionError: If Ollama is not running
            ValueError: If model is not available
        """
        try:
            import requests
        except ImportError:
            raise ImportError(
                "Requests library not installed. Install with: pip install requests"
            )
        
        # Get Ollama endpoint from environment or use default
        endpoint = _sanitize_ollama_endpoint(
            os.environ.get('OLLAMA_ENDPOINT', 'http://localhost:11434')
        )

        # Use provided max_tokens or default
        tokens_to_use = max_tokens if max_tokens is not None else min(self.max_tokens, 8192)
        
        print(f"🟠 _call_ollama START: model={self.model}, prompt_len={len(prompt)}, max_tokens={tokens_to_use}")
        print(f"🟠 Ollama endpoint: {endpoint}")
        
        # Build messages list
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        # Timeout scales with model size and prompt length unless the user set one
        prompt_len = len(prompt) + (len(system_prompt) if system_prompt else 0)
        timeout_seconds, param_billions = _resolve_ollama_timeout(self.model, prompt_len)

        # Use streaming for large requests to avoid timeout issues
        # Streaming reads tokens as they arrive – only the connection + first token
        # must arrive within the timeout, not the entire response
        use_streaming = prompt_len > 3000 or tokens_to_use > 4096

        # Build request payload
        # Using /api/chat for chat-style interaction (better for translation prompts)
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": use_streaming,
            "options": {
                "temperature": 0.3,  # Low temperature for consistent translations
                "num_predict": tokens_to_use,
                "top_p": 0.9,
                "repeat_penalty": 1.1
            }
        }

        try:
            # Make API call with generous timeout (local models can be slow, especially first load)
            # First call loads model into memory which can take 30-60 seconds
            # Large models on CPU can take 2-10 minutes per request
            model_size_str = f"{param_billions}B" if param_billions > 0 else "unknown size"
            print(f"🟠 Calling Ollama API... (model: {model_size_str}, timeout: {timeout_seconds}s, streaming: {use_streaming})")

            proxies = {"http": self.http_proxy, "https": self.http_proxy} if self.http_proxy else None

            if use_streaming:
                # Streaming mode: read tokens incrementally to avoid timeout on large responses
                # Connection timeout = 120s (model loading), no read timeout (tokens arrive gradually)
                response = requests.post(
                    f"{endpoint}/api/chat",
                    json=payload,
                    timeout=(120, timeout_seconds),  # (connect_timeout, read_timeout for first chunk)
                    proxies=proxies,
                    stream=True
                )

                if response.status_code == 404:
                    raise ValueError(
                        f"Model '{self.model}' not found in Ollama. "
                        f"Please download it first with: ollama pull {self.model}"
                    )

                response.raise_for_status()

                # Read streamed JSON chunks and assemble the full response
                import json as json_module
                chunks = []
                eval_count = 0
                for line in response.iter_lines():
                    if line:
                        try:
                            chunk = json_module.loads(line)
                            if 'message' in chunk and 'content' in chunk['message']:
                                chunks.append(chunk['message']['content'])
                            # Last chunk contains stats
                            if chunk.get('done', False):
                                eval_count = chunk.get('eval_count', 0)
                        except json_module.JSONDecodeError:
                            continue

                translation = ''.join(chunks).strip()
                if not translation:
                    raise ValueError(f"Empty response from Ollama streaming")

                print(f"🟠 Ollama streaming completed")
                if eval_count:
                    print(f"🟠 Ollama stats: {eval_count} tokens generated")

                return translation
            else:
                # Non-streaming mode for small/simple requests
                response = requests.post(
                    f"{endpoint}/api/chat",
                    json=payload,
                    timeout=timeout_seconds,
                    proxies=proxies
                )

                if response.status_code == 404:
                    raise ValueError(
                        f"Model '{self.model}' not found in Ollama. "
                        f"Please download it first with: ollama pull {self.model}"
                    )

                response.raise_for_status()

                result = response.json()
                print(f"🟠 Ollama API call completed")

                # Extract translation from response
                if 'message' in result and 'content' in result['message']:
                    translation = result['message']['content'].strip()
                else:
                    raise ValueError(f"Unexpected Ollama response format: {result}")

                # Log some stats if available
                if 'eval_count' in result:
                    print(f"🟠 Ollama stats: {result.get('eval_count', 0)} tokens generated")

                return translation

        except requests.exceptions.ConnectionError:
            raise ConnectionError(
                f"Cannot connect to Ollama at {endpoint}. "
                "Please ensure Ollama is installed and running.\n\n"
                "To start Ollama:\n"
                "  1. Install from https://ollama.com\n"
                "  2. Run 'ollama serve' in a terminal\n"
                "  3. Try again"
            )
        except requests.exceptions.Timeout:
            model_info = f" ({model_size_str})" if param_billions > 0 else ""
            raise TimeoutError(
                f"Ollama request timed out after {timeout_seconds} seconds{model_info}.\n\n"
                "This usually means:\n"
                "  1. System is low on RAM (check Task Manager)\n"
                "  2. Model is too large for your hardware\n"
                "  3. First-time model loading takes longer\n\n"
                "Solutions:\n"
                "  • Close other applications to free RAM\n"
                "  • Use a smaller model: 'translategemma:4b' or 'qwen3:4b'\n"
                "  • Try again (subsequent runs are faster)\n"
                "  • Allow more time: Settings → AI Settings → Local LLM (Ollama)\n"
                "    Advanced Settings → Request timeout"
            )
        except requests.exceptions.RequestException as e:
            raise RuntimeError(f"Ollama API error: {str(e)}")


# ============================================================================
# STANDALONE USAGE
# ============================================================================

def main():
    """Example standalone usage of LLM client"""
    import sys
    
    if len(sys.argv) < 4:
        print("Usage: python llm_clients.py <provider> <api_key> <text_to_translate>")
        print("Example: python llm_clients.py openai sk-... 'Hello world'")
        sys.exit(1)
    
    provider = sys.argv[1]
    api_key = sys.argv[2]
    text = sys.argv[3]
    
    # Create client
    client = LLMClient(api_key=api_key, provider=provider)
    
    # Translate
    print(f"Translating with {provider} ({client.model})...")
    result = client.translate(text, source_lang="en", target_lang="nl")
    
    print(f"\nOriginal: {text}")
    print(f"Translation: {result}")


# Wrapper functions for easy integration with Supervertaler
def get_openai_translation(text: str, source_lang: str, target_lang: str, context: str = "") -> Dict:
    """
    Get OpenAI translation with metadata
    
    Args:
        text: Text to translate
        source_lang: Source language name
        target_lang: Target language name
        context: Optional context for better translation
    
    Returns:
        Dict with translation, model, and metadata
    """
    try:
        print(f"🔍 [DEBUG] OpenAI: Starting translation for '{text[:30]}...'")
        
        # Load API key from config
        api_key = _load_api_key('openai')
        print(f"🔍 [DEBUG] OpenAI: API key loaded: {'Yes' if api_key else 'No'}")
        if not api_key:
            raise ValueError(api_key_problem('openai') or "No API key set for OpenAI.")
            
        # Create LLM client and get real translation
        print(f"🔍 [DEBUG] OpenAI: Creating LLMClient...")
        client = LLMClient(api_key=api_key, provider="openai")
        print(f"🔍 [DEBUG] OpenAI: Client created, calling translate...")
        
        translation = client.translate(
            text=text,
            source_lang=_convert_lang_name_to_code(source_lang),
            target_lang=_convert_lang_name_to_code(target_lang),
            context=context if context else None
        )
        
        print(f"🔍 [DEBUG] OpenAI: Translation received: '{translation[:30]}...'")
        return {
            'translation': translation,
            'model': client.model,
            'explanation': f"Translation provided with context: {context[:50]}..." if context else "Translation completed",
            'success': True
        }
    except Exception as e:
        print(f"🔍 [DEBUG] OpenAI: ERROR - {str(e)}")
        return {
            'translation': None,
            'error': str(e),
            'success': False
        }


def get_claude_translation(text: str, source_lang: str, target_lang: str, context: str = "") -> Dict:
    """
    Get Claude translation with metadata
    
    Args:
        text: Text to translate
        source_lang: Source language name
        target_lang: Target language name
        context: Optional context for better translation
    
    Returns:
        Dict with translation, model, and metadata
    """
    try:
        print(f"🔍 [DEBUG] Claude: Starting translation for '{text[:30]}...'")
        
        # Load API key from config
        api_key = _load_api_key('claude')
        print(f"🔍 [DEBUG] Claude: API key loaded: {'Yes' if api_key else 'No'}")
        if not api_key:
            raise ValueError(api_key_problem('claude') or "No API key set for Anthropic.")
            
        # Create LLM client and get real translation
        print(f"🔍 [DEBUG] Claude: Creating LLMClient...")
        client = LLMClient(api_key=api_key, provider="claude")
        print(f"🔍 [DEBUG] Claude: Client created, calling translate...")
        
        translation = client.translate(
            text=text,
            source_lang=_convert_lang_name_to_code(source_lang),
            target_lang=_convert_lang_name_to_code(target_lang),
            context=context if context else None
        )
        
        print(f"🔍 [DEBUG] Claude: Translation received: '{translation[:30]}...'")
        return {
            'translation': translation,
            'model': client.model,
            'reasoning': f"High-quality translation considering context: {context[:50]}..." if context else "Translation completed",
            'success': True
        }
    except Exception as e:
        print(f"🔍 [DEBUG] Claude: ERROR - {str(e)}")
        return {
            'translation': None,
            'error': str(e),
            'success': False
        }


def _load_api_key(provider: str) -> str:
    """Load a single API key by provider name from unified settings (with legacy fallback)"""
    try:
        api_keys = load_api_keys()
        # Try exact match first, then case-insensitive
        value = api_keys.get(provider) or api_keys.get(provider.lower())
        return value if value else None
    except Exception:
        return None

def _convert_lang_name_to_code(lang_name: str) -> str:
    """Convert language names to codes for LLM API"""
    lang_map = {
        'Dutch': 'nl',
        'English': 'en', 
        'German': 'de',
        'French': 'fr',
        'Spanish': 'es',
        'Italian': 'it',
        'Portuguese': 'pt',
        'Chinese': 'zh',
        'Japanese': 'ja',
        'Korean': 'ko'
    }
    return lang_map.get(lang_name, lang_name.lower()[:2])

def get_google_translation(text: str, source_lang: str, target_lang: str) -> Dict:
    """
    Get Google Cloud Translation API translation with metadata
    
    Args:
        text: Text to translate
        source_lang: Source language code (e.g., 'en', 'nl', 'auto')
        target_lang: Target language code (e.g., 'en', 'nl')
    
    Returns:
        Dict with translation, confidence, and metadata
    """
    try:
        api_keys = load_api_keys()
        google_api_key = api_keys.get('google_translate')
        
        if not google_api_key:
            return {
                'translation': None,
                'error': api_key_problem('google_translate') or 'No API key set for Google Translate.',
                'success': False
            }
        
        # Use Google Cloud Translation API (Basic/v2) via REST
        try:
            import requests
            
            # Use REST API directly with API key
            url = "https://translation.googleapis.com/language/translate/v2"
            
            # Handle 'auto' source language
            params = {
                'key': google_api_key,
                'q': text,
                'target': target_lang
            }
            
            if source_lang and source_lang != 'auto':
                params['source'] = source_lang
            
            # Make API request
            response = requests.post(url, params=params)
            
            if response.status_code == 200:
                result = response.json()
                if 'data' in result and 'translations' in result['data']:
                    translation_data = result['data']['translations'][0]
                    return {
                        'translation': translation_data['translatedText'],
                        'confidence': 'High',
                        'detected_source_language': translation_data.get('detectedSourceLanguage', source_lang),
                        'provider': 'Google Cloud Translation',
                        'success': True,
                        'metadata': {
                            'model': 'nmt',  # Neural Machine Translation
                            'input': text
                        }
                    }
                else:
                    return {
                        'translation': None,
                        'error': f'Unexpected Google API response format: {result}',
                        'success': False
                    }
            else:
                return {
                    'translation': None,
                    'error': f'Google API error: {response.status_code} - {response.text}',
                    'success': False
                }
                
        except ImportError:
            # Fallback if requests is not installed
            return {
                'translation': None,
                'error': 'Requests library not installed. Install: pip install requests',
                'success': False
            }
    except Exception as e:
        return {
            'translation': None,
            'error': f'Google Translate error: {str(e)}',
            'success': False
        }


if __name__ == "__main__":
    main()
