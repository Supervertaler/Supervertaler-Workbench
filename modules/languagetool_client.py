"""
LanguageTool client
===================

Grammar, spelling and style checking of target segments through a LanguageTool
server (issue #233): either the free public API or a LanguageTool server you
run yourself (``java -cp languagetool-server.jar org.languagetool.server.HTTPServer
--port 8081``), which is free, unlimited and keeps the text on your machine.

Segments are sent in batches – many segments per request, separated by blank
lines – and every match is mapped back to its segment and offset. The public
API allows roughly 20 requests and 20 KB per request a minute, so batches stay
under that size and are spaced out.
"""

import time
from dataclasses import dataclass, field
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

PUBLIC_API_URL = "https://api.languagetool.org"
LOCAL_SERVER_URL = "http://localhost:8081"
SEPARATOR = "\n\n"
MAX_BATCH_CHARS = 15000          # the public API refuses requests over ~20 KB
PUBLIC_API_DELAY_S = 3.2         # ~20 requests a minute

# LanguageTool wants a regional variant for a few languages to check spelling.
_DEFAULT_VARIANTS = {
    "en": "en-US", "de": "de-DE", "pt": "pt-PT", "ca": "ca-ES", "zh": "zh-CN",
}


@dataclass
class LTFinding:
    segment_index: int
    offset: int                  # within the segment's text
    length: int
    text: str                    # the flagged text
    message: str
    rule_id: str = ""
    category: str = ""
    issue_type: str = ""         # misspelling / grammar / style / typographical …
    replacements: List[str] = field(default_factory=list)
    context: str = ""


def lt_language(value) -> str:
    """A project language (code or name) as a LanguageTool language code."""
    from modules import language_codes as _lc
    canonical = _lc.canonical(value) if value else ""
    if not canonical:
        return "auto"
    if "-" in canonical:
        return canonical
    return _DEFAULT_VARIANTS.get(canonical, canonical)


def build_batches(texts: Sequence[str], max_chars: int = MAX_BATCH_CHARS):
    """Group non-empty texts into requests. Yields ``(joined_text, spans)``
    where spans are ``(text_index, start_in_joined)``. A text longer than
    ``max_chars`` gets a request of its own."""
    joined, spans, size = [], [], 0
    for index, text in enumerate(texts):
        if not text or not text.strip():
            continue
        extra = len(text) + (len(SEPARATOR) if joined else 0)
        if joined and size + extra > max_chars:
            yield SEPARATOR.join(joined), spans
            joined, spans, size = [], [], 0
            extra = len(text)
        spans.append((index, size + (len(SEPARATOR) if joined else 0)))
        joined.append(text)
        size += extra
    if joined:
        yield SEPARATOR.join(joined), spans


def _locate(spans: List[Tuple[int, int]], texts: Sequence[str], offset: int):
    """Which text a match in the joined request belongs to, and its offset there."""
    for index, start in reversed(spans):
        if offset >= start:
            local = offset - start
            if local < len(texts[index]):
                return index, local
            return None
    return None


def _context(text: str, start: int, end: int, width: int = 30) -> str:
    left = text[max(0, start - width):start]
    right = text[end:end + width]
    return (("…" if start > width else "") + left + "[" + text[start:end] + "]" + right
            + ("…" if end + width < len(text) else ""))


def check_request(base_url: str, text: str, language: str, *, username: str = "",
                  api_key: str = "", mother_tongue: str = "", proxies: Optional[Dict] = None,
                  timeout: float = 60.0, session=None) -> List[Dict]:
    """POST one ``/v2/check`` request and return its ``matches``."""
    import requests
    data = {"text": text, "language": language}
    if username and api_key:
        data.update(username=username, apiKey=api_key)
    if mother_tongue:
        data["motherTongue"] = mother_tongue
    http = session or requests
    response = http.post(base_url.rstrip("/") + "/v2/check", data=data,
                         timeout=timeout, proxies=proxies)
    if response.status_code == 429:
        raise RuntimeError("LanguageTool's free API limit was reached. Wait a minute and "
                           "try again, or use your own LanguageTool server.")
    response.raise_for_status()
    return response.json().get("matches", [])


def check_texts(texts: Sequence[str], language: str, base_url: str = PUBLIC_API_URL, *,
                username: str = "", api_key: str = "", proxies: Optional[Dict] = None,
                progress: Optional[Callable[[int, int], bool]] = None,
                request: Callable = check_request, sleep: Callable = time.sleep,
                max_chars: int = MAX_BATCH_CHARS) -> List[LTFinding]:
    """Check ``texts`` (one per segment) and return the findings, each tied to
    its text's index. ``progress(done, total)`` may return False to stop."""
    batches = list(build_batches(texts, max_chars))
    public = base_url.rstrip("/") == PUBLIC_API_URL and not api_key
    findings: List[LTFinding] = []
    for number, (joined, spans) in enumerate(batches):
        if progress and progress(number, len(batches)) is False:
            break
        if number and public:
            sleep(PUBLIC_API_DELAY_S)
        for match in request(base_url, joined, language, username=username,
                             api_key=api_key, proxies=proxies):
            where = _locate(spans, texts, int(match.get("offset", 0)))
            if where is None:
                continue
            index, local = where
            length = int(match.get("length", 0))
            text = texts[index]
            end = min(len(text), local + length)
            rule = match.get("rule") or {}
            findings.append(LTFinding(
                segment_index=index, offset=local, length=end - local, text=text[local:end],
                message=match.get("message") or match.get("shortMessage") or "",
                rule_id=rule.get("id", ""),
                category=(rule.get("category") or {}).get("name", ""),
                issue_type=rule.get("issueType", ""),
                replacements=[r.get("value", "") for r in (match.get("replacements") or [])[:5]],
                context=_context(text, local, end)))
    if progress:
        progress(len(batches), len(batches))
    return findings


def apply_replacement(text: str, finding: LTFinding, replacement: str) -> Optional[str]:
    """``text`` with the finding's span replaced – or None when the text no
    longer has the flagged words at that spot (it was edited since the check)."""
    end = finding.offset + finding.length
    if text[finding.offset:end] != finding.text:
        return None
    return text[:finding.offset] + replacement + text[end:]
