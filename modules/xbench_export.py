"""
Open a project in ApSIC Xbench (issue #146)
===========================================

Xbench is the QA tool many translators already run next to memoQ and Trados,
which both offer "open this project in Xbench". This module writes what Xbench
needs for the same:

- the project's segments as an XLIFF 1.2 file, marked in the Xbench project as
  the *ongoing translation* that the QA checks run on;
- the terms of the project's glossaries as a tab-delimited *key terms* file,
  for Xbench's Key Term Mismatch check;
- an Xbench project file (``.xbp``) listing both, in the format ApSIC documents
  at https://github.com/xbench/xbench-file-formats (paths relative to the
  ``.xbp``, file type 13 = XLIFF, 1 = tab-delimited text).

Opening the ``.xbp`` – as a double-click would – starts Xbench with it loaded.
"""

import os
import re
from typing import Dict, Iterable, List, Optional, Sequence, Tuple
from xml.sax.saxutils import escape

XBENCH_FOLDER = os.path.join("qa", "xbench")  # inside the project folder
XBP_TYPE_TAB_DELIMITED = 1
XBP_TYPE_XLIFF = 13

# Characters XML 1.0 does not allow; a stray one would make Xbench reject the file.
_INVALID_XML = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f￾￿]")

_APPROVED = {"approved", "proofread"}
_CONFIRMED = {"confirmed", "translated"}


def _xml(text) -> str:
    return escape(_INVALID_XML.sub("", str(text or "")))


def xliff_state(status: str, target: str) -> Tuple[str, bool]:
    """XLIFF ``state`` for a Workbench status, and whether it is approved."""
    if not (target or "").strip():
        return "needs-translation", False
    status = (status or "").lower()
    if status in _APPROVED:
        return "signed-off", True
    if status in _CONFIRMED:
        return "translated", False
    return "needs-review-translation", False


def build_xliff(segments: Iterable, source_lang: str, target_lang: str,
                original: str) -> str:
    """XLIFF 1.2 with one trans-unit per segment, ``id`` = the segment number.

    Inline tags stay in the text as Supervertaler shows them, so Xbench sees
    the same segment text as the grid.
    """
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<xliff version="1.2" xmlns="urn:oasis:names:tc:xliff:document:1.2">',
        f'  <file original="{_xml(original)}" source-language="{_xml(source_lang)}" '
        f'target-language="{_xml(target_lang)}" datatype="plaintext">',
        '    <body>',
    ]
    for index, seg in enumerate(segments, start=1):
        source = getattr(seg, "source", "") or ""
        if not source.strip():
            continue
        target = getattr(seg, "target", "") or ""
        state, approved = xliff_state(getattr(seg, "status", ""), target)
        attrs = f'id="{_xml(getattr(seg, "id", index))}"'
        if approved:
            attrs += ' approved="yes"'
        if getattr(seg, "locked", False):
            attrs += ' translate="no"'
        lines.append(f'      <trans-unit {attrs}>')
        lines.append(f'        <source xml:space="preserve">{_xml(source)}</source>')
        lines.append(f'        <target xml:space="preserve" state="{state}">{_xml(target)}</target>')
        lines.append('      </trans-unit>')
    lines += ['    </body>', '  </file>', '</xliff>', '']
    return "\n".join(lines)


def build_key_terms(terms: Iterable[Tuple[str, str]]) -> str:
    """Tab-delimited key terms: one ``source<TAB>target`` line per term.

    Tabs and line breaks inside a term would break the columns, so they become
    spaces; empty and duplicate pairs are left out.
    """
    seen, lines = set(), []
    for source, target in terms:
        source = re.sub(r"[\t\r\n]+", " ", source or "").strip()
        target = re.sub(r"[\t\r\n]+", " ", target or "").strip()
        if not source or not target or (source, target) in seen:
            continue
        seen.add((source, target))
        lines.append(f"{source}\t{target}")
    return "\r\n".join(lines) + ("\r\n" if lines else "")


def build_xbp(xbp_name: str, files: Sequence[Dict]) -> str:
    """An Xbench project listing ``files``: dicts with ``filename`` (relative to
    the ``.xbp``), ``type``, and optional ``keyterms``, ``newtranslations``,
    ``level`` (1–3) and ``comments``."""
    out = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<xbench version="2.9.438" savedwith="3.0.1374">',
        '  <project>',
        f'    <filename xml:space="preserve">{_xml(xbp_name)}</filename>',
        '    <maxcols>0</maxcols>',
        '    <showfilename>1</showfilename>',
        '    <showkeytermsattop>1</showkeytermsattop>',
        '    <checklistgroup>',
        '      <checklist name="$project"></checklist>',
        '    </checklistgroup>',
        '    <glossarylist>',
    ]
    for ident, f in enumerate(files):
        out += [
            f'      <glossary type="{int(f["type"])}">',
            f'        <ident>{ident}</ident>',
            f'        <filename xml:space="preserve">{_xml(f["filename"])}</filename>',
            f'        <level>{int(f.get("level", 1))}</level>',
            f'        <keyterms>{1 if f.get("keyterms") else 0}</keyterms>',
            f'        <newtranslations>{1 if f.get("newtranslations") else 0}</newtranslations>',
        ]
        if f.get("comments"):
            out.append(f'        <comments xml:space="preserve">{_xml(f["comments"])}</comments>')
        out.append('      </glossary>')
    out += ['    </glossarylist>', '  </project>', '</xbench>', '']
    return "\n".join(out)


def _safe_name(name: str) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", (name or "").strip()).strip(" .")
    return name or "project"


def write_xbench_project(folder: str, project_name: str, segments: Sequence,
                         source_lang: str, target_lang: str,
                         key_terms: Optional[Iterable[Tuple[str, str]]] = None) -> Dict:
    """Write the XLIFF, the key terms (if any) and the ``.xbp`` into ``folder``.

    Returns ``{"xbp": path, "xliff": path, "key_terms": path or None,
    "segments": n, "terms": n}``. Existing files of the same project are
    replaced, so running it again refreshes what Xbench sees.
    """
    os.makedirs(folder, exist_ok=True)
    base = _safe_name(project_name)
    xliff_name = f"{base}.xlf"
    xliff_text = build_xliff(segments, source_lang, target_lang, project_name)
    with open(os.path.join(folder, xliff_name), "w", encoding="utf-8", newline="\n") as f:
        f.write(xliff_text)
    files = [{"filename": xliff_name, "type": XBP_TYPE_XLIFF, "newtranslations": True,
              "comments": "Exported from Supervertaler Workbench"}]

    terms_path, term_count = None, 0
    terms_text = build_key_terms(key_terms or [])
    if terms_text:
        terms_name = f"{base} - key terms.txt"
        terms_path = os.path.join(folder, terms_name)
        # UTF-8 with a byte-order mark, so Xbench on Windows reads it as UTF-8
        with open(terms_path, "w", encoding="utf-8-sig", newline="") as f:
            f.write(terms_text)
        term_count = terms_text.count("\r\n")
        files.append({"filename": terms_name, "type": XBP_TYPE_TAB_DELIMITED, "keyterms": True,
                      "level": 3, "comments": "Glossary terms from Supervertaler Workbench"})

    xbp_name = f"{base}.xbp"
    xbp_path = os.path.join(folder, xbp_name)
    with open(xbp_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(build_xbp(xbp_name, files))
    return {"xbp": xbp_path, "xliff": os.path.join(folder, xliff_name),
            "key_terms": terms_path, "segments": xliff_text.count("<trans-unit "),
            "terms": term_count}


def oriented_terms(terms: Iterable[Dict], termbase_source: str, project_source: str,
                   same_language) -> List[Tuple[str, str]]:
    """``(source, target)`` pairs in the project's direction.

    A termbase made the other way round (Dutch → English for an English →
    Dutch project) is flipped. Forbidden terms are left out: as key terms Xbench
    would demand them in the translation.
    """
    flip = bool(termbase_source) and bool(project_source) and \
        not same_language(termbase_source, project_source)
    pairs = []
    for term in terms:
        if term.get("forbidden"):
            continue
        source, target = term.get("source_term") or "", term.get("target_term") or ""
        if term.get("is_nontranslatable") and not target:
            target = source  # stays as it is in the translation
        pairs.append((target, source) if flip else (source, target))
    return pairs
