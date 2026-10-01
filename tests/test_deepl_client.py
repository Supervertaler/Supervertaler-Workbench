"""DeepL with a DeepL Pro Advanced "authentication key for CAT tools" (issue #135).

Such a key only works on DeepL's API v1; v2 (the ``deepl`` library) refuses it
with 403. A refused key is now tried on v1 and, once accepted there, sent
straight to v1.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

deepl = pytest.importorskip("deepl")
from modules import deepl_client as dc


class Resp:
    def __init__(self, status, payload=None):
        self.status_code = status
        self._payload = payload or {}
        self.text = str(payload)

    def json(self):
        return self._payload


class FakeV1:
    """api.deepl.com/v1/translate that knows one CAT-tool key."""

    def __init__(self, key, variants=True):
        self.key, self.variants, self.calls = key, variants, []

    def __call__(self, url, data, headers, proxies, timeout):
        self.calls.append((url, dict(data), dict(headers)))
        sent = headers.get("Authorization", "").replace("DeepL-Auth-Key ", "") or data.get("auth_key")
        if sent != self.key:
            return Resp(403, {"message": "Authorization failure"})
        if not self.variants and "-" in data["target_lang"]:
            return Resp(400, {"message": "Value for 'target_lang' not supported."})
        return Resp(200, {"translations": [{"text": f"[{data['target_lang']}] {data['text']}"}]})


def refusing_v2(api_key, proxy=None):
    class T:
        def translate_text(self, text, source_lang, target_lang):
            raise deepl.AuthorizationException("Authorization failure, check auth_key")
    return T()


def working_v2(api_key, proxy=None):
    class T:
        def translate_text(self, text, source_lang, target_lang):
            class R:
                pass
            r = R()
            r.text = f"v2 {source_lang}>{target_lang}: {text}"
            return r
    return T()


@pytest.fixture(autouse=True)
def _forget_keys():
    dc._v1_keys.clear()
    yield
    dc._v1_keys.clear()


def test_language_codes():
    assert dc.language_codes("English", "Dutch") == ("EN", "NL")
    assert dc.language_codes("nl-NL", "en") == ("NL", "EN-US")
    assert dc.language_codes("de", "pt_BR") == ("DE", "PT-BR")
    assert dc.language_codes("en", "zh-TW") == ("EN", "ZH-HANT")


def test_api_keys_use_v2():
    v1 = FakeV1("cat-key")
    out = dc.translate("Hello", "English", "Dutch", "api-key", translator_factory=working_v2, post=v1)
    assert out == "v2 EN>NL: Hello" and v1.calls == []


def test_cat_tool_key_falls_back_to_v1_and_is_remembered():
    v1 = FakeV1("cat-key")
    out = dc.translate("Hello", "English", "Dutch", " cat-key ", translator_factory=refusing_v2, post=v1)
    assert out == "[NL] Hello"
    url, data, headers = v1.calls[0]
    assert url == "https://api.deepl.com/v1/translate"
    assert headers == {"Authorization": "DeepL-Auth-Key cat-key"}
    assert data == {"text": "Hello", "target_lang": "NL", "source_lang": "EN"}

    def v2_must_not_be_called(*a, **k):
        raise AssertionError("known CAT-tool key sent to v2")
    assert dc.translate("Bye", "en", "de", "cat-key", translator_factory=v2_must_not_be_called,
                        post=v1) == "[DE] Bye"


def test_v1_without_regional_variant():
    v1 = FakeV1("cat-key", variants=False)
    assert dc.translate("Hi", "nl", "en-GB", "cat-key", translator_factory=refusing_v2,
                        post=v1) == "[EN] Hi"


def test_a_key_neither_api_accepts_gets_a_clear_message():
    v1 = FakeV1("another-key")
    with pytest.raises(dc.DeepLError) as err:
        dc.translate("Hi", "en", "nl", "wrong-key", translator_factory=refusing_v2, post=v1)
    assert "authentication key for CAT tools" in str(err.value)
    assert not dc._v1_keys


def test_free_keys_and_other_errors_are_not_retried_on_v1():
    v1 = FakeV1("x")
    with pytest.raises(deepl.AuthorizationException):
        dc.translate("Hi", "en", "nl", "abc:fx", translator_factory=refusing_v2, post=v1)

    def quota(api_key, proxy=None):
        class T:
            def translate_text(self, *a, **k):
                raise deepl.QuotaExceededException("Quota exceeded")
        return T()
    with pytest.raises(deepl.QuotaExceededException):
        dc.translate("Hi", "en", "nl", "api-key", translator_factory=quota, post=v1)
    assert v1.calls == []
