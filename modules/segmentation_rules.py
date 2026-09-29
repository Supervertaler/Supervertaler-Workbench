"""
Segmentation rules
==================

User-definable rules for the segmenter Supervertaler runs itself (issue #191):
plain-text and Markdown imports with "Split lines into sentences" ticked, text
pasted into New Project, and Add Segments from pasted text. DOCX and the other
document formats are split by the Okapi engine with its own SRX rules and are
not affected.

The model is the one SRX uses (the exchange format of OmegaT, Okapi and other
CAT tools): a rule is either a *break* or an *exception* (no break), with one
regular expression for the text before the break point and one for the text
after it. At every position the first rule that matches decides – custom rules
before the built-in sentence rules – so an exception can protect "np." and a
break rule can split at "<>" or any other delimiter. With the built-in rules
switched off, the custom rules alone decide.

SRX files are written for Java regular expressions. The one difference that
matters in practice, Unicode property classes such as ``\\p{Lu}``, is translated
into an equivalent Python character class (Basic Multilingual Plane).
"""

from __future__ import annotations

import re
import unicodedata
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Dict, List, Optional, Pattern, Tuple
from xml.sax.saxutils import escape

SETTINGS_KEY = "segmentation_rules"

SRX_NAMESPACE = "http://www.lisa.org/srx20"


@dataclass
class Rule:
    is_break: bool = True
    before: str = ""
    after: str = ""
    enabled: bool = True
    comment: str = ""

    def to_dict(self) -> dict:
        return {"break": self.is_break, "before": self.before, "after": self.after,
                "enabled": self.enabled, "comment": self.comment}

    @classmethod
    def from_dict(cls, data) -> "Rule":
        data = data if isinstance(data, dict) else {}
        return cls(is_break=bool(data.get("break", True)),
                   before=str(data.get("before") or ""),
                   after=str(data.get("after") or ""),
                   enabled=bool(data.get("enabled", True)),
                   comment=str(data.get("comment") or ""))


@dataclass
class SegmentationRules:
    split_at_line_breaks: bool = False
    use_builtin_rules: bool = True
    extra_abbreviations: List[str] = field(default_factory=list)
    rules: List[Rule] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"split_at_line_breaks": self.split_at_line_breaks,
                "use_builtin_rules": self.use_builtin_rules,
                "extra_abbreviations": list(self.extra_abbreviations),
                "rules": [r.to_dict() for r in self.rules]}

    @classmethod
    def from_dict(cls, data) -> "SegmentationRules":
        data = data if isinstance(data, dict) else {}
        abbreviations = data.get("extra_abbreviations") or []
        if isinstance(abbreviations, str):
            abbreviations = parse_abbreviations(abbreviations)
        return cls(split_at_line_breaks=bool(data.get("split_at_line_breaks", False)),
                   use_builtin_rules=bool(data.get("use_builtin_rules", True)),
                   extra_abbreviations=[str(a) for a in abbreviations if str(a).strip()],
                   rules=[Rule.from_dict(r) for r in data.get("rules") or []])


def parse_abbreviations(text: str) -> List[str]:
    """``"np., itd, m.in."`` → ``["np", "itd", "m.in"]`` (the full stop that
    follows an abbreviation is implied)."""
    out = []
    for part in re.split(r"[,;\s]+", text or ""):
        part = part.strip().rstrip(".").lower()
        if part and part not in out:
            out.append(part)
    return out


# ── Java → Python regular expressions ─────────────────────────────────────

_PROPERTY = re.compile(r"\\([pP])(?:\{(?:Is)?([A-Za-z_]+)\}|([LMNPSZC]))")
_CATEGORIES = {"L", "Lu", "Ll", "Lt", "Lm", "Lo", "M", "Mn", "Mc", "Me",
               "N", "Nd", "Nl", "No", "P", "Pc", "Pd", "Ps", "Pe", "Pi", "Pf", "Po",
               "S", "Sm", "Sc", "Sk", "So", "Z", "Zs", "Zl", "Zp",
               "C", "Cc", "Cf", "Co", "Cn"}
_ALIASES = {"Upper": "Lu", "Uppercase": "Lu", "javaUpperCase": "Lu",
            "Lower": "Ll", "Lowercase": "Ll", "javaLowerCase": "Ll",
            "Alpha": "L", "Alphabetic": "L", "Letter": "L", "javaLetter": "L",
            "Digit": "Nd", "javaDigit": "Nd", "Punct": "P", "Punctuation": "P"}


@lru_cache(maxsize=None)
def _category_ranges(category: str) -> str:
    """Character-class body (no brackets) matching one Unicode general
    category, or a whole group such as ``L``."""
    ranges: List[Tuple[int, int]] = []
    for cp in range(0x10000):
        if unicodedata.category(chr(cp)).startswith(category):
            if ranges and ranges[-1][1] == cp - 1:
                ranges[-1] = (ranges[-1][0], cp)
            else:
                ranges.append((cp, cp))
    return "".join(f"\\u{a:04x}" if a == b else f"\\u{a:04x}-\\u{b:04x}" for a, b in ranges)


def to_python_regex(pattern: str) -> str:
    """Translate the Java-only parts of an SRX pattern (``\\p{Lu}``,
    ``\\P{L}``, ``\\pL``…) into Python ``re`` syntax."""
    out: List[str] = []
    i, n, in_class = 0, len(pattern or ""), False
    while i < n:
        ch = pattern[i]
        if ch == "\\" and i + 1 < n:
            m = _PROPERTY.match(pattern, i)
            if m:
                name = m.group(2) or m.group(3)
                category = _ALIASES.get(name, name)
                if category not in _CATEGORIES:
                    raise ValueError(f"Unsupported Unicode property \\p{{{name}}}")
                body = _category_ranges(category)
                negate = m.group(1) == "P"
                if in_class:
                    if negate:
                        raise ValueError("\\P{…} inside […] is not supported")
                    out.append(body)
                else:
                    out.append(("[^" if negate else "[") + body + "]")
                i = m.end()
                continue
            out.append(pattern[i:i + 2])
            i += 2
            continue
        if ch == "[" and not in_class:
            in_class = True
            out.append(ch)
            i += 1
            if i < n and pattern[i] == "^":
                out.append("^")
                i += 1
            if i < n and pattern[i] == "]":      # a "]" straight after "[" is literal
                out.append("\\]")
                i += 1
            continue
        if ch == "]" and in_class:
            in_class = False
        out.append(ch)
        i += 1
    return "".join(out)


def _python_part(pattern: str, label: str) -> str:
    try:
        translated = to_python_regex(pattern)
        if translated:
            re.compile(translated)
    except (re.error, ValueError) as e:
        raise ValueError(f"{label}: {getattr(e, 'msg', None) or e}") from None
    return translated


def compile_rule(rule: Rule) -> Pattern:
    """One pattern whose match *end* is the break position: the "before"
    expression, then the "after" expression as a lookahead. Raises
    ``ValueError`` for an unusable rule."""
    before = _python_part(rule.before, "Before the break")
    after = _python_part(rule.after, "After the break")
    if not before and not after:
        raise ValueError("Enter a pattern before or after the break.")
    try:
        return re.compile((f"(?:{before})" if before else "") + (f"(?=(?:{after}))" if after else ""))
    except re.error as e:
        raise ValueError(e.msg) from None


def rule_error(rule: Rule) -> Optional[str]:
    try:
        compile_rule(rule)
    except ValueError as e:
        return str(e)
    return None


def compile_rules(rules: List[Rule]) -> List[Tuple[Pattern, bool]]:
    """The enabled, valid rules in order, as ``(pattern, is_break)``."""
    compiled = []
    for rule in rules:
        if not rule.enabled:
            continue
        try:
            compiled.append((compile_rule(rule), rule.is_break))
        except ValueError:
            continue
    return compiled


def rule_positions(pattern: Pattern, text: str) -> List[int]:
    """Every position where ``pattern`` matches, including overlapping
    matches (a search restarts one character after each match start)."""
    found = []
    pos, n = 0, len(text)
    while pos <= n:
        m = pattern.search(text, pos)
        if not m:
            break
        found.append(m.end())
        pos = m.start() + 1
    return found


# ── SRX import / export ───────────────────────────────────────────────────

def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def parse_srx(data: str) -> Dict[str, List[Rule]]:
    """``{language rule name: [Rule, …]}`` in file order, from SRX 1.0/2.0."""
    root = ET.fromstring(data.encode("utf-8") if isinstance(data, str) else data)
    result: Dict[str, List[Rule]] = {}
    for element in root.iter():
        if _local(element.tag) != "languagerule":
            continue
        name = element.get("languagerulename") or f"Rules {len(result) + 1}"
        rules = []
        for rule_el in element:
            if _local(rule_el.tag) != "rule":
                continue
            before = after = ""
            for part in rule_el:
                if _local(part.tag) == "beforebreak":
                    before = part.text or ""
                elif _local(part.tag) == "afterbreak":
                    after = part.text or ""
            is_break = (rule_el.get("break") or "yes").strip().lower() != "no"
            rules.append(Rule(is_break=is_break, before=before, after=after))
        result[name] = rules
    return result


def build_srx(rules: List[Rule], name: str = "Supervertaler") -> str:
    """An SRX 2.0 document with the enabled rules as one language rule that
    applies to every language."""
    lines = ['<?xml version="1.0" encoding="UTF-8"?>',
             f'<srx xmlns="{SRX_NAMESPACE}" version="2.0">',
             '  <header segmentsubflows="yes" cascade="no"/>',
             '  <body>',
             '    <languagerules>',
             f'      <languagerule languagerulename="{escape(name, {chr(34): "&quot;"})}">']
    for rule in rules:
        if not rule.enabled or rule_error(rule):
            continue
        if rule.comment:
            lines.append(f"        <!-- {rule.comment.replace('--', '- -')} -->")
        lines.append(f'        <rule break="{"yes" if rule.is_break else "no"}">')
        if rule.before:
            lines.append(f"          <beforebreak>{escape(rule.before)}</beforebreak>")
        if rule.after:
            lines.append(f"          <afterbreak>{escape(rule.after)}</afterbreak>")
        lines.append("        </rule>")
    lines += ['      </languagerule>',
              '    </languagerules>',
              '    <maprules>',
              f'      <languagemap languagepattern=".*" languagerulename="{escape(name, {chr(34): "&quot;"})}"/>',
              '    </maprules>',
              '  </body>',
              '</srx>', '']
    return "\n".join(lines)
