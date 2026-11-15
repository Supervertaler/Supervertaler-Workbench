"""
ISO 639-1 / BCP-47 language code ↔ English-name mapping.

Used by bilingual-DOCX / RTF handlers to translate the language codes
they extract from file metadata (e.g. Phrase's ``Source (cs) | Target
(de-de)`` header) into the English names Workbench's language pickers
display (``Czech``, ``German``).

Covers the languages in the New-Project dialog's
``available_languages`` list (full Workbench picker set). Codes are
matched case-insensitively against the 2-letter ISO 639-1 prefix, so
inputs like ``cs``, ``cs-CZ``, ``cs-cz``, ``CS-CZ`` all resolve to
``Czech``.

If you add a language to the New-Project picker, also add it here so
the auto-detection round-trips cleanly. Unknown codes return ``None``
and the caller falls back to whatever it does for "couldn't
auto-detect".
"""

from __future__ import annotations

from typing import Optional


# ISO 639-1 → English language name. Keep this in sync with the
# ``available_languages`` list in Supervertaler.py's new-project and
# Phrase / Trados import dialogs.
_ISO_TO_ENGLISH: dict[str, str] = {
    "af": "Afrikaans",
    "ar": "Arabic",
    "bg": "Bulgarian",
    "bn": "Bengali",
    "ca": "Catalan",
    "cs": "Czech",
    "cy": "Welsh",
    "da": "Danish",
    "de": "German",
    "el": "Greek",
    "en": "English",
    "es": "Spanish",
    "et": "Estonian",
    "eu": "Basque",
    "fa": "Persian",
    "fi": "Finnish",
    "fr": "French",
    "ga": "Irish",
    "gl": "Galician",
    "he": "Hebrew",
    "hi": "Hindi",
    "hr": "Croatian",
    "hu": "Hungarian",
    "hy": "Armenian",
    "id": "Indonesian",
    "is": "Icelandic",
    "it": "Italian",
    "ja": "Japanese",
    "ka": "Georgian",
    "ko": "Korean",
    "lt": "Lithuanian",
    "lv": "Latvian",
    "mk": "Macedonian",
    "ms": "Malay",
    "nl": "Dutch",
    "no": "Norwegian",
    "pl": "Polish",
    "pt": "Portuguese",
    "ro": "Romanian",
    "ru": "Russian",
    "sk": "Slovak",
    "sl": "Slovenian",
    "sq": "Albanian",
    "sr": "Serbian",
    "sv": "Swedish",
    "sw": "Swahili",
    "th": "Thai",
    "tr": "Turkish",
    "uk": "Ukrainian",
    "ur": "Urdu",
    "vi": "Vietnamese",
    "zh": "Chinese (Simplified)",  # see note below re: Hans/Hant
    "zu": "Zulu",
}

# Subtags that mean "Traditional Chinese" – when present we prefer the
# Traditional variant in the picker. Phrase / Trados both occasionally
# emit ``zh-TW`` or ``zh-HK`` for Traditional and ``zh-CN`` for
# Simplified, so we honour that distinction.
_ZH_TRADITIONAL_REGIONS = {"tw", "hk", "mo", "hant"}


def iso_to_english_name(code: Optional[str]) -> Optional[str]:
    """Resolve a BCP-47 / ISO 639-1 code to Workbench's English name.

    Args:
        code: A language code like ``"cs"``, ``"cs-CZ"``, ``"de-de"``,
            ``"zh-Hant"``. Whitespace and case are tolerated. ``None``
            or an unknown code returns ``None``.

    Returns:
        The English language name as Workbench's pickers spell it
        (``"Czech"``, ``"German"``, ``"Chinese (Traditional)"``), or
        ``None`` if the code isn't recognised.
    """
    if not code:
        return None
    s = str(code).strip().lower()
    if not s:
        return None
    # Split off the region / script tag (``cs-CZ`` → ``cs``,
    # ``zh-Hant`` → primary ``zh`` + tag ``hant``).
    if "-" in s:
        primary, region = s.split("-", 1)
        # Disambiguate Chinese variants.
        if primary == "zh" and region.split("-")[0] in _ZH_TRADITIONAL_REGIONS:
            return "Chinese (Traditional)"
    else:
        primary = s
    # TMX headers occasionally use ISO 639-2 three-letter codes (e.g. 'zul').
    primary = _ISO3_TO_ISO1.get(primary, primary)
    return _ISO_TO_ENGLISH.get(primary)


# ============================================================================
# Canonical language normalisation — THE single authority.
#
# Every part of the app that needs to normalise or compare a language should
# use these functions (directly, or via the back-compat shims in
# tmx_generator / Supervertaler that now delegate here). Do NOT add another
# private name→code table somewhere — that is exactly what caused years of
# "no results when From/To is set" bugs.
#
# Policy (decided 2026-06-01): KEEP region, MATCH on base. We store/display
# nl-BE and en-GB faithfully, but `same_language()` compares on the base code
# by default, so nl-BE, nl-NL, nl and "Dutch" all match and lookups never miss.
# ============================================================================

# Reverse of _ISO_TO_ENGLISH (name → code), plus common aliases.
_NAME_TO_ISO: dict[str, str] = {name.lower(): code for code, name in _ISO_TO_ENGLISH.items()}
_NAME_TO_ISO.update({
    "chinese": "zh",
    "chinese (simplified)": "zh-CN",
    "chinese (traditional)": "zh-TW",
    "mandarin": "zh",
    "flemish": "nl",          # Belgian Dutch
    "castilian": "es",
    "norwegian bokmål": "no", "norwegian bokmal": "no",
})

# Country/region display names → BCP-47 region subtag, for inputs that spell
# the region out, e.g. "Dutch (Belgium)" or "English (United States)".
_REGION_NAME_TO_CODE: dict[str, str] = {
    "belgium": "BE", "netherlands": "NL", "the netherlands": "NL",
    "united states": "US", "us": "US", "usa": "US", "america": "US",
    "united kingdom": "GB", "uk": "GB", "great britain": "GB", "britain": "GB",
    "canada": "CA", "australia": "AU", "new zealand": "NZ", "ireland": "IE",
    "austria": "AT", "germany": "DE", "switzerland": "CH", "france": "FR",
    "spain": "ES", "mexico": "MX", "brazil": "BR", "portugal": "PT", "italy": "IT",
    "south africa": "ZA",
}

# ISO 639-2 (three-letter) → 639-1, occasionally seen in TMX headers.
_ISO3_TO_ISO1: dict[str, str] = {
    "dut": "nl", "nld": "nl", "eng": "en", "ger": "de", "deu": "de",
    "fre": "fr", "fra": "fr", "spa": "es", "ita": "it", "por": "pt",
    "rus": "ru", "chi": "zh", "zho": "zh", "jpn": "ja", "kor": "ko",
    "ara": "ar", "pol": "pl", "swe": "sv", "dan": "da", "nor": "no", "fin": "fi",
    "zul": "zu",
}


def _split_region(s: str):
    """Split a code-ish string into (primary, region|None). 'nl_BE'→('nl','BE')."""
    t = s.replace("_", "-")
    if "-" in t:
        a, b = t.split("-", 1)
        return a, b.split("-")[0]
    return t, None


def base_code(value) -> str:
    """Any input → base ISO 639-1 code, lowercased ('Dutch'→'nl', 'nl-BE'→'nl',
    'Dutch (Belgium)'→'nl', 'nld'→'nl'). Returns '' for empty/None."""
    if not value:
        return ""
    s = str(value).strip()
    if not s:
        return ""
    low = s.lower()
    if "(" in low:  # "Chinese (Traditional)" / "Dutch (Belgium)"
        if low in _NAME_TO_ISO:
            return _NAME_TO_ISO[low].split("-")[0]
        namepart = low.split("(")[0].strip()
        if namepart in _NAME_TO_ISO:
            return _NAME_TO_ISO[namepart].split("-")[0]
    if low in _NAME_TO_ISO:
        return _NAME_TO_ISO[low].split("-")[0]
    primary, _region = _split_region(low)
    if primary in _ISO3_TO_ISO1:
        return _ISO3_TO_ISO1[primary]
    if primary in _NAME_TO_ISO:
        return _NAME_TO_ISO[primary].split("-")[0]
    if len(primary) >= 2 and primary.isalpha():
        return primary[:2] if len(primary) > 3 else primary
    return ""


def canonical(value) -> str:
    """Any input → canonical BCP-47, preserving region: base code plus
    '-REGION' (uppercased) when a region is present. 'Dutch'→'nl',
    'nl_be'→'nl-BE', 'Dutch (Belgium)'→'nl-BE'. Returns '' for empty/None."""
    if not value:
        return ""
    s = str(value).strip()
    if not s:
        return ""
    low = s.lower()
    if "(" in low:
        if low in _NAME_TO_ISO:
            return _NAME_TO_ISO[low]
        namepart = low.split("(")[0].strip()
        regionpart = low[low.find("(") + 1: low.rfind(")")].strip() if ")" in low else ""
        b = base_code(namepart)
        region = _REGION_NAME_TO_CODE.get(regionpart)
        if b and region:
            return f"{b}-{region}"
        if b:
            return b
    if low in _NAME_TO_ISO:
        return _NAME_TO_ISO[low]
    primary, region = _split_region(s)
    b = base_code(primary)
    if not b:
        return ""
    return f"{b}-{region.upper()}" if region else b


def english_name(value):
    """Base English name as the pickers spell it ('Dutch'), region-aware for
    Chinese. Returns None if unrecognised."""
    b = base_code(value)
    if not b:
        return None
    if b == "zh":
        _p, region = _split_region(str(value).lower())
        if region in _ZH_TRADITIONAL_REGIONS:
            return "Chinese (Traditional)"
    return _ISO_TO_ENGLISH.get(b)


def display_name(value) -> str:
    """Human-facing label: 'Dutch (BE)' when a region is present, else 'Dutch',
    else the original input."""
    name = english_name(value)
    if not name:
        return str(value) if value else ""
    can = canonical(value)
    if "-" in can:
        return f"{name} ({can.split('-', 1)[1]})"
    return name


def same_language(a, b, *, region: bool = False) -> bool:
    """Do two language values refer to the same language? Compares on the base
    code by default (nl-BE == nl-NL == 'Dutch'); pass region=True for a strict
    locale match (nl-BE != nl-NL)."""
    if region:
        ca, cb = canonical(a), canonical(b)
        return bool(ca) and ca.lower() == cb.lower()
    ba = base_code(a)
    return bool(ba) and ba == base_code(b)


def match_variants(value) -> list:
    """Strings that could match this language in a DB text column (for SQL
    IN/LIKE): base code, English name, and the canonical region form. Superset
    of the legacy get_lang_match_variants()."""
    b = base_code(value)
    if not b:
        return []
    out = [b]
    name = _ISO_TO_ENGLISH.get(b)
    if name:
        out.append(name)
        if "(" in name:  # 'Chinese (Simplified)' → also bare 'Chinese'
            out.append(name.split("(")[0].strip())
    can = canonical(value)
    if can and can not in out:
        out.append(can)
    return out


def available_pairs() -> list:
    """(English-name, ISO code) pairs for language pickers — the full canonical
    set, sorted by English name. Single source for the import / new-project
    dialogs so no language is silently missing from a dropdown."""
    return sorted(((name, code) for code, name in _ISO_TO_ENGLISH.items()),
                  key=lambda t: t[0].lower())
