"""
DeepL for Supervertaler (issue #135)
====================================

DeepL hands out two kinds of key:

- a **DeepL API** key (API Free, ending in ``:fx``, or API Pro), which works
  with DeepL's current API, v2 – the one the ``deepl`` Python library uses;
- the **authentication key for CAT tools** that comes with a DeepL Pro
  Advanced or Ultimate subscription. DeepL accepts that key only on its older
  API, v1 (``https://api.deepl.com/v1/translate``); v2 refuses it with
  HTTP 403 ("Authorization failure").

Supervertaler used the library alone, so a CAT-tool key never worked. Now a
key that v2 refuses is tried on v1; when v1 accepts it, the key is remembered
(for this session) as a CAT-tool key and goes straight to v1 after that.
"""

import hashlib
from typing import Callable, Optional, Tuple

V1_URL = "https://api.deepl.com/v1/translate"
TIMEOUT = 30

KEY_HELP = (
    "Use a DeepL API key (API Free or API Pro, from deepl.com/pro-api) or the "
    "authentication key for CAT tools that comes with a DeepL Pro Advanced or "
    "Ultimate subscription (DeepL account → Account → Authentication key for "
    "CAT tools)."
)

_LANGUAGE_NAMES = {
    'english': 'en', 'dutch': 'nl', 'german': 'de', 'french': 'fr',
    'spanish': 'es', 'italian': 'it', 'portuguese': 'pt', 'russian': 'ru',
    'chinese': 'zh', 'japanese': 'ja', 'korean': 'ko', 'arabic': 'ar',
    'polish': 'pl', 'swedish': 'sv', 'norwegian': 'no', 'danish': 'da',
    'finnish': 'fi', 'greek': 'el', 'turkish': 'tr', 'czech': 'cs',
    'hungarian': 'hu', 'romanian': 'ro', 'bulgarian': 'bg', 'ukrainian': 'uk',
}

# Target languages DeepL wants as a variant ("EN" alone is deprecated)
_TARGET_VARIANTS = {
    'EN': 'EN-US', 'EN-US': 'EN-US', 'EN-GB': 'EN-GB',
    'EN-AU': 'EN-GB', 'EN-CA': 'EN-US',
    'PT': 'PT-PT', 'PT-PT': 'PT-PT', 'PT-BR': 'PT-BR',
    'ZH': 'ZH-HANS', 'ZH-CN': 'ZH-HANS', 'ZH-TW': 'ZH-HANT',
    'ZH-HANS': 'ZH-HANS', 'ZH-HANT': 'ZH-HANT',
}

# Fingerprints of keys that turned out to be CAT-tool keys (v1 only)
_v1_keys = set()


class DeepLError(Exception):
    def __init__(self, message: str, status: Optional[int] = None):
        super().__init__(message)
        self.status = status


def language_codes(source_lang: str, target_lang: str) -> Tuple[str, str]:
    """DeepL's source and target codes for a language name or code."""
    src_lower = (source_lang or '').lower().strip()
    src = _LANGUAGE_NAMES.get(src_lower, src_lower.split('-')[0].split('_')[0]).upper()

    tgt_lower = (target_lang or '').lower().strip()
    tgt = _LANGUAGE_NAMES.get(tgt_lower, tgt_lower).upper().replace('_', '-')
    if tgt in _TARGET_VARIANTS:
        return src, _TARGET_VARIANTS[tgt]
    base = tgt.split('-')[0]
    return src, _TARGET_VARIANTS.get(base, base)


def is_free_key(api_key: str) -> bool:
    return (api_key or '').strip().endswith(':fx')


def _fingerprint(api_key: str) -> str:
    return hashlib.sha256(api_key.encode('utf-8')).hexdigest()


def _is_authorization_error(exc: Exception) -> bool:
    try:
        import deepl
        if isinstance(exc, deepl.AuthorizationException):
            return True
    except ImportError:
        pass
    return '403' in str(exc) or 'authorization' in str(exc).lower()


def translate_v2(text: str, source: str, target: str, api_key: str, proxies=None,
                 translator_factory: Optional[Callable] = None) -> str:
    """Translate with the ``deepl`` library (API v2)."""
    if translator_factory is None:
        import deepl
        translator_factory = deepl.Translator
    translator = translator_factory(api_key, proxy=proxies)
    return translator.translate_text(text, source_lang=source or None, target_lang=target).text


def translate_v1(text: str, source: str, target: str, api_key: str, proxies=None,
                 post: Optional[Callable] = None) -> str:
    """Translate on API v1, the one DeepL's CAT-tool keys work with."""
    if post is None:
        import requests
        post = requests.post

    def request(target_lang, key_in_body=False):
        data = {'text': text, 'target_lang': target_lang}
        if source:
            data['source_lang'] = source
        headers = {}
        if key_in_body:
            data['auth_key'] = api_key
        else:
            headers['Authorization'] = f'DeepL-Auth-Key {api_key}'
        return post(V1_URL, data=data, headers=headers, proxies=proxies, timeout=TIMEOUT)

    resp = request(target)
    if resp.status_code == 403:
        resp = request(target, key_in_body=True)  # the form v1 was first documented with
    if resp.status_code == 400 and '-' in target:
        resp = request(target.split('-')[0])      # v1 may not know the regional variant
    if resp.status_code != 200:
        try:
            detail = resp.json().get('message') or ''
        except Exception:
            detail = (getattr(resp, 'text', '') or '')[:200]
        raise DeepLError(f"HTTP {resp.status_code}{': ' + detail if detail else ''}", resp.status_code)
    return resp.json()['translations'][0]['text']


def translate(text: str, source_lang: str, target_lang: str, api_key: str, proxies=None,
              translator_factory: Optional[Callable] = None,
              post: Optional[Callable] = None) -> str:
    """Translate ``text`` with DeepL, whichever kind of key ``api_key`` is."""
    source, target = language_codes(source_lang, target_lang)
    api_key = (api_key or '').strip()
    fingerprint = _fingerprint(api_key)
    if fingerprint in _v1_keys:
        return translate_v1(text, source, target, api_key, proxies, post)
    try:
        return translate_v2(text, source, target, api_key, proxies, translator_factory)
    except ImportError:
        raise
    except Exception as exc:
        if is_free_key(api_key) or not _is_authorization_error(exc):
            raise
        try:
            result = translate_v1(text, source, target, api_key, proxies, post)
        except DeepLError as v1_exc:
            if v1_exc.status == 403:
                raise DeepLError(f"DeepL did not accept this key. {KEY_HELP}", 403) from exc
            raise
        _v1_keys.add(fingerprint)
        return result
