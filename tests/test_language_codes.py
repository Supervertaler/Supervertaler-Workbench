"""Canonical language-normalisation authority + back-compat shim contracts.

This guards the fix for the recurring "no results when From/To is set" bugs:
all language normalisation now flows through modules.language_codes, and the
legacy helpers (tmx_generator, lang_to_code) delegate to it. If anyone
reintroduces a divergent name↔code table or breaks a helper's contract, this
test fails.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules import language_codes as lc
from modules.tmx_generator import (
    get_base_lang_code,
    get_lang_match_variants,
    get_simple_lang_code,
    languages_are_compatible,
    normalize_lang_variant,
)
from modules.bilingual_markdown_handler import lang_to_code


# Every one of these spellings of Dutch must collapse to the same base code.
DUTCH_FORMS = [
    "nl", "NL", "nl-NL", "nl-BE", "nl_BE", "NL-be",
    "Dutch", "dutch", "Dutch (Netherlands)", "Dutch (Belgium)",
    "Flemish", "nld", "dut",
]


def test_all_dutch_spellings_share_a_base_code():
    for form in DUTCH_FORMS:
        assert lc.base_code(form) == "nl", form


def test_canonical_preserves_region_but_normalises_form():
    assert lc.canonical("Dutch") == "nl"
    assert lc.canonical("nl_be") == "nl-BE"
    assert lc.canonical("NL-be") == "nl-BE"
    assert lc.canonical("Dutch (Belgium)") == "nl-BE"
    assert lc.canonical("Dutch (Netherlands)") == "nl-NL"
    assert lc.canonical("en-us") == "en-US"
    assert lc.canonical("English") == "en"
    assert lc.canonical("") == ""


def test_same_language_matches_on_base_but_can_be_region_strict():
    # Base matching (default) — the whole point: variants never miss.
    assert lc.same_language("nl-BE", "nl-NL")
    assert lc.same_language("nl-BE", "Dutch")
    assert lc.same_language("Dutch (Belgium)", "nl")
    assert lc.same_language("en-GB", "en-US")
    # Different languages never match.
    assert not lc.same_language("nl", "en")
    assert not lc.same_language("Dutch", "German")
    # Region-strict when explicitly requested.
    assert lc.same_language("nl-BE", "nl-BE", region=True)
    assert not lc.same_language("nl-BE", "nl-NL", region=True)
    # Empty never matches anything.
    assert not lc.same_language("", "nl")


def test_match_variants_cover_code_and_name():
    v = lc.match_variants("Dutch (Belgium)")
    assert "nl" in v and "Dutch" in v and "nl-BE" in v
    assert lc.match_variants("") == []


def test_english_and_display_names():
    assert lc.english_name("nl-BE") == "Dutch"
    assert lc.english_name("Dutch") == "Dutch"
    assert lc.english_name("zh-TW") == "Chinese (Traditional)"
    assert lc.english_name("xx-YY") is None
    assert lc.display_name("nl-BE") == "Dutch (BE)"
    assert lc.display_name("nl") == "Dutch"


# --- Back-compat shim contracts (the ~hundreds of existing call sites) -------

def test_get_base_lang_code_contract():
    assert get_base_lang_code("Dutch") == "nl"
    assert get_base_lang_code("en-US") == "en"
    assert get_base_lang_code("nl-BE") == "nl"
    assert get_base_lang_code("") == "en"   # legacy default


def test_get_simple_lang_code_contract():
    assert get_simple_lang_code("Dutch") == "nl"
    assert get_simple_lang_code("nl") == "nl"
    assert get_simple_lang_code("en-US") == "en-US"
    assert get_simple_lang_code("nl_BE") == "nl-BE"
    assert get_simple_lang_code("") == "en"   # legacy default


def test_get_lang_match_variants_contract():
    v = get_lang_match_variants("nl")
    assert "nl" in v and "Dutch" in v
    assert get_lang_match_variants("") == ["en", "English"]


def test_normalize_lang_variant_contract():
    assert normalize_lang_variant("nl-be") == "nl-BE"
    assert normalize_lang_variant("NL-NL") == "nl-NL"
    assert normalize_lang_variant("nl") == "nl"


def test_languages_are_compatible_contract():
    assert languages_are_compatible("nl-BE", "Dutch")
    assert languages_are_compatible("en-GB", "en-US")
    assert not languages_are_compatible("nl", "en")


def test_lang_to_code_contract():
    assert lang_to_code("English (US)") == "EN"
    assert lang_to_code("en-GB") == "EN"
    assert lang_to_code("Dutch") == "NL"
    assert lang_to_code("") == "??"


def test_the_actual_bug_that_started_this():
    # SuperLookup passed 'Dutch'/'English' (or 'nl-BE') while TM rows were
    # tagged 'nl'/'en'. Base-code matching must bridge them.
    assert lc.same_language("Dutch", "nl")
    assert lc.same_language("nl-BE", "nl")
    assert lc.base_code("Dutch") == lc.base_code("nl-NL") == "nl"


def test_available_pairs_covers_picker_languages():
    """Guards the import-dialog language fix (v1.10.261): the canonical picker
    set must include the languages the old hardcoded 12-item list dropped, and
    base_code must resolve code/name/region forms to the same picker code, so a
    project's language can't silently fall back to English on import."""
    from modules.language_codes import available_pairs, base_code
    pairs = available_pairs()
    codes = {c for _, c in pairs}
    for code in ("sk", "cs", "uk", "ru", "en", "nl"):
        assert code in codes, f"{code} missing from available_pairs()"
    assert ("Slovak", "sk") in pairs
    assert [n for n, _ in pairs] == sorted((n for n, _ in pairs), key=str.lower)
    assert base_code("sk") == base_code("Slovak") == base_code("sk-SK") == "sk"


def test_zulu_resolves_from_every_form():
    """Zulu (PR 257): the two-letter code, the region form, the ISO 639-2
    code a TMX header may carry, and the picker names all mean one language."""
    for form in ("zu", "zu-ZA", "zul", "Zulu", "Zulu (South Africa)"):
        assert lc.base_code(form) == "zu", form
        assert lc.english_name(form) == "Zulu", form
    assert lc.canonical("Zulu (South Africa)") == "zu-ZA"
    assert lc.iso_to_english_name("zul") == "Zulu"
    assert lc.same_language("zul", "zu-ZA")
    assert ("Zulu", "zu") in lc.available_pairs()


def test_iso3_fallback_keeps_unknown_codes_unknown():
    """The three-letter fallback in iso_to_english_name only maps codes it
    knows; anything else still returns None rather than a guess."""
    assert lc.iso_to_english_name("fra") == "French"
    assert lc.iso_to_english_name("xyz") is None
    assert lc.iso_to_english_name("") is None
    assert lc.iso_to_english_name(None) is None


# --- One list of languages ---------------------------------------------------
# Every picker and the DOCX export read modules/language_codes.py. These tests
# fail if a language is added to one table and not the other, or if a private
# list creeps back into the files that used to keep their own.

def test_every_language_has_a_word_locale():
    missing = sorted(set(lc._ISO_TO_ENGLISH) - set(lc._DEFAULT_LOCALE))
    extra = sorted(set(lc._DEFAULT_LOCALE) - set(lc._ISO_TO_ENGLISH))
    assert not missing, f"no Word locale for: {missing} (add to _DEFAULT_LOCALE)"
    assert not extra, f"Word locale for a language not in the list: {extra}"
    for code, loc in lc._DEFAULT_LOCALE.items():
        assert lc.docx_locale(code) == lc.docx_locale(lc._ISO_TO_ENGLISH[code]) \
            == ("nb-NO" if code == "no" else loc), code


def test_picker_names_cover_every_language_once():
    names = lc.picker_names()
    assert len(names) == len(set(names))
    assert set(names) == set(lc._ISO_TO_ENGLISH.values()) | {"Chinese (Traditional)"}
    assert names == sorted(names, key=str.lower)
    for name, code in lc.available_pairs():
        assert lc.english_name(code) == name, (name, code)


def test_docx_locale_keeps_regions_and_falls_back_to_english():
    assert lc.docx_locale("Dutch (Belgium)") == "nl-BE"
    assert lc.docx_locale("Flemish") == "nl-BE"
    assert lc.docx_locale("English (UK)") == "en-GB"
    assert lc.docx_locale("British English") == "en-GB"
    assert lc.docx_locale("Spanish (Latin America)") == "es-419"
    assert lc.docx_locale("pt-BR") == "pt-BR"
    assert lc.docx_locale("nl_be") == "nl-BE"
    assert lc.docx_locale("Chinese (Traditional)") == "zh-TW"
    assert lc.docx_locale("Norwegian") == lc.docx_locale("no") == "nb-NO"
    assert lc.docx_locale("kk-KZ") == "kk-KZ"      # unlisted, but a locale
    for unknown in ("", None, "Klingon", "xx"):
        assert lc.docx_locale(unknown) == "en-US", unknown


def test_chinese_traditional_name_resolves():
    assert lc.english_name("Chinese (Traditional)") == "Chinese (Traditional)"
    assert lc.english_name("Traditional Chinese") == "Chinese (Traditional)"
    assert lc.english_name("Chinese (Simplified)") == "Chinese (Simplified)"


def test_norwegian_written_standards_are_norwegian():
    assert lc.same_language("nb-NO", "Norwegian")
    assert lc.same_language("nn", "no")
    assert lc.english_name("nb") == "Norwegian"


def test_no_private_language_lists_return():
    """The files that used to keep their own list must not grow one again."""
    import re
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for rel in ("Supervertaler.py", "modules/quicktrans.py",
                "modules/unified_prompt_manager_qt.py"):
        with open(os.path.join(root, rel), encoding="utf-8") as f:
            text = f.read()
        hits = re.findall(r'"Swahili",\s*"Swedish"|"swahili":\s*"sw|"sw":\s*"Swahili"', text)
        assert not hits, f"{rel} has its own language list again: use modules/language_codes.py"
