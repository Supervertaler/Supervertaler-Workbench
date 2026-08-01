"""Centralized status vocabulary for Supervertaler segments."""

from dataclasses import dataclass
from typing import Dict, Optional
import re


@dataclass(frozen=True)
class StatusDefinition:
    key: str
    label: str
    icon: str
    color: str
    memoq_label: str
    memoQ_equivalents: tuple[str, ...]
    match_symbol: str = ""
    short_label: str = ""  # Short abbreviation shown inline in the status column (e.g. "CM", "MT", "Rep")
    badge_text: str = ""   # Trados-style coloured text badge shown in place of the icon (e.g. "PM", "CM")
    badge_bg: str = ""     # Badge background colour
    badge_fg: str = "#ffffff"  # Badge text colour
    # Colour for a MONOCHROME (text-glyph) icon, e.g. "#c62828". Empty means the
    # glyph draws in its own colours — which is what a colour-emoji icon does.
    #
    # Why this exists: colour-emoji glyphs (❌, ✅, 🟪 …) are bitmap glyphs that
    # IGNORE font-size and line-height, so they overflow the tight single-line
    # status cell and are clipped at the bottom — the recurring "the red X is
    # cut off" bug. Text glyphs from the Dingbats block (✔ U+2714 / ✘ U+2718)
    # obey font-size and accept a CSS colour, so they scale with the row and
    # cannot clip. The two most common statuses therefore use a matched
    # text-glyph PAIR rather than one glyph of each kind.
    icon_color: str = ""


STATUSES: Dict[str, StatusDefinition] = {
    "not_started": StatusDefinition(
        key="not_started",
        label="Not started",
        icon="✘",  # Heavy ballot X (U+2718) - text glyph, pairs with ✔ U+2714
        icon_color="#d32f2f",  # red, matching the old ❌ emoji's colour
        color="#ffe6e6",
        memoq_label="Not started",
        memoQ_equivalents=("not started", "not translated"),
    ),
    "pretranslated": StatusDefinition(
        key="pretranslated",
        label="Pre-translated",
        icon="⚡",  # Lightning - automatic pre-fill (distinct from MT's robot)
        color="#e8f2ff",
        memoq_label="Pre-translated",
        memoQ_equivalents=("pre-translated", "pretranslated"),
        match_symbol="⚡",
        short_label="Pre",
    ),
    "draft": StatusDefinition(
        key="draft",
        label="Draft",
        icon="✏️",  # Pencil - indicates manual translation work, not yet confirmed
        color="#e6ffe6",
        memoq_label="Edited",
        memoQ_equivalents=("translated", "edited", "draft"),
        match_symbol="✍️",
    ),
    "confirmed": StatusDefinition(
        key="confirmed",
        label="Confirmed",
        icon="✔",  # Heavy check mark (U+2714) - text glyph, pairs with ✘
        icon_color="#2e7d32",  # green (was hard-coded in the grid widget)
        color="#d1ffd6",
        memoq_label="Confirmed",
        memoQ_equivalents=("confirmed",),
        match_symbol="🛡️",
    ),
    "proofread": StatusDefinition(
        key="proofread",
        label="Proofread",
        icon="🟪",
        color="#efe0ff",
        memoq_label="Proofread",
        memoQ_equivalents=("proofread",),
        match_symbol="📘",
        short_label="PR",
    ),
    "rejected": StatusDefinition(
        key="rejected",
        label="Rejected",
        icon="🚫",
        color="#ffe0e0",
        memoq_label="Rejected",
        memoQ_equivalents=("rejected",),
        match_symbol="⛔",
        short_label="Rej",
    ),
    "approved": StatusDefinition(
        key="approved",
        label="Approved",
        icon="⭐",
        color="#e6f3ff",
        memoq_label="Approved",
        memoQ_equivalents=("approved", "final", "proofread confirmed"),
        match_symbol="🏁",
        short_label="App",
    ),
    "pm": StatusDefinition(
        key="pm",
        label="PM (102%)",
        icon="⭐",  # Star - perfect/double context match
        color="#b8daff",  # Light blue - highest confidence
        memoq_label="Pre-translated (102%)",
        memoQ_equivalents=("pre-translated (102%)", "xlt", "double context", "perfect match", "pm"),
        match_symbol="⭐",
        short_label="PM",
        badge_text="PM",
        badge_bg="#546E7A",  # Blue-grey, Trados-like
    ),
    "cm": StatusDefinition(
        key="cm",
        label="CM (101%)",
        icon="💎",  # Diamond - context match
        color="#c3e6cb",  # Darker green - very high confidence
        memoq_label="Pre-translated (101%)",
        memoQ_equivalents=("pre-translated (101%)", "context match", "cm"),
        match_symbol="💎",
        short_label="CM",
        badge_text="CM",
        badge_bg="#2E7D32",  # Green, distinct from PM
    ),
    "tm_100": StatusDefinition(
        key="tm_100",
        label="TM 100%",
        icon="✅",  # Checkmark - exact match
        color="#d4edda",  # Light green - high confidence
        memoq_label="Pre-translated (100%)",
        memoQ_equivalents=("pre-translated (100%)", "exact match"),
        match_symbol="✅",
        short_label="100%",
    ),
    "tm_fuzzy": StatusDefinition(
        key="tm_fuzzy",
        label="TM Fuzzy",
        icon="🔶",  # Orange diamond - partial match
        color="#fff3cd",  # Light yellow/orange - needs review
        memoq_label="Pre-translated (fuzzy)",
        memoQ_equivalents=("fuzzy", "fuzzy match"),
        match_symbol="🔶",
        short_label="Fuz",
    ),
    "repetition": StatusDefinition(
        key="repetition",
        label="Repetition",
        icon="🔁",  # Repeat icon - internal repetition
        color="#e2e3e5",  # Light gray - auto-propagated
        memoq_label="Repetition",
        memoQ_equivalents=("repetition", "rep", "auto-propagated"),
        match_symbol="🔁",
        short_label="Rep",
    ),
    "machine_translated": StatusDefinition(
        key="machine_translated",
        label="MT",
        icon="🤖",  # Robot - machine translation
        color="#ffeaa7",  # Light orange/yellow - needs review
        memoq_label="Machine Translated",
        memoQ_equivalents=("machine translated", "mt", "nmt", "auto-translated"),
        match_symbol="🤖",
        short_label="MT",
    ),
}

DEFAULT_STATUS = STATUSES["not_started"]

# Backward-compatibility aliases for renamed / removed statuses.
# Saved projects may still contain these old keys.
_STATUS_ALIASES = {
    "translated": "draft",          # renamed in v1.9.336
    "tr_confirmed": "confirmed",    # removed in v1.9.336 – merged into confirmed
}


def get_status(key: str) -> StatusDefinition:
    """Return status definition for key, falling back to default."""
    resolved = _STATUS_ALIASES.get(key, key)
    return STATUSES.get(resolved, DEFAULT_STATUS)


def match_memoq_status(status_text: str) -> tuple[StatusDefinition, Optional[int]]:
    """Map memoQ status string to a StatusDefinition plus optional match percent."""
    status_clean = (status_text or "").strip()
    percent: Optional[int] = None

    if status_clean:
        match = re.search(r"(\d+)\s*%", status_clean)
        if match:
            try:
                percent = int(match.group(1))
            except ValueError:
                percent = None

    lower = status_clean.lower()

    # Build (equivalent, definition) pairs sorted by equivalent length descending.
    # This ensures specific matches like "pre-translated (101%)" beat generic
    # ones like "pre-translated", so e.g. CM status isn't lost to pretranslated.
    _eq_pairs: list[tuple[str, StatusDefinition]] = []
    for definition in STATUSES.values():
        for eq in definition.memoQ_equivalents:
            _eq_pairs.append((eq, definition))
    _eq_pairs.sort(key=lambda p: len(p[0]), reverse=True)

    for eq, definition in _eq_pairs:
        if eq in lower:
            return definition, percent

    if "proofread" in lower and "confirm" in lower:
        return STATUSES["approved"], percent
    if "confirm" in lower:
        return STATUSES["confirmed"], percent
    if "lock" in lower:
        return STATUSES["not_started"], percent
    if "reject" in lower:
        return STATUSES["rejected"], percent
    if "proof" in lower:
        return STATUSES["proofread"], percent
    if "translate" in lower or "edit" in lower:
        return STATUSES["draft"], percent

    return DEFAULT_STATUS, percent


def compose_memoq_status(
    status_key: str,
    match_percent: Optional[int] = None,
    existing: Optional[str] = None,
) -> str:
    """Compose a memoQ status string, preserving existing text when provided."""
    if existing and existing.strip():
        return existing.strip()

    status_def = get_status(status_key)
    base = status_def.memoq_label
    if match_percent is not None:
        return f"{base} ({match_percent}%)"
    return base


