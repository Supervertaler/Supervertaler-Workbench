"""
Extract translatable strings from Supervertaler source files into XLIFF 1.2.

Walks the AST of every requested Python file, finds calls to ``self.tr(...)``
and ``QCoreApplication.translate(...)`` with literal string arguments, and
emits a single XLIFF 1.2 file with one ``<trans-unit>`` per unique
``(context, source)`` pair.

We chose XLIFF over the more conventional Qt ``.ts`` (Qt Linguist's XML) and
GNU gettext ``.po`` because Supervertaler's audience is professional
translators – every CAT tool in regular use (Trados, memoQ, Phrase, OmegaT,
Workbench itself) eats standard XLIFF 1.2 natively as a source-file format,
where ``.ts`` is essentially Qt-only and ``.po`` ergonomics vary by tool.

The runtime loader (``modules/i18n.py`` ``XliffTranslator``) reads the same
XLIFF format, so the round-trip is symmetrical: extract → translate in any
CAT tool → save back as XLIFF → load on next launch.

# Usage

    python tools/extract_strings.py

Produces ``translations/supervertaler_template.xlf`` with all strings
collected from ``Supervertaler.py`` and ``modules/i18n.py``.

# Notes

- Only literal-string ``tr()`` arguments are extracted. f-strings, name
  references, and string concatenations are silently skipped – they
  require runtime-side ``.format()`` plumbing that's a separate audit.
- Existing translations in target locale files (``supervertaler_*.xlf``)
  are NOT touched by this tool. Re-running it regenerates the template
  only. A small ``merge_template.py`` helper handles propagating new
  strings into existing translated files while preserving completed
  translations – run that step manually after extraction.
- Context is taken from the *enclosing class name* (matching Qt's tr()
  convention), or "<module>" for module-level calls. This appears in the
  XLIFF ``trans-unit/@id`` and the ``context-group`` element so
  translators can disambiguate when a generic word like "Save" needs
  different translations in different places.
"""

from __future__ import annotations

import ast
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable


# ─── Configuration ────────────────────────────────────────────────────────

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SOURCES: list[Path] = [
    REPO_ROOT / "Supervertaler.py",
    REPO_ROOT / "modules" / "i18n.py",
    # Moved out of Supervertaler.py (Nov 2026); their classes keep their
    # names, so the Qt contexts of their strings are unchanged.
    REPO_ROOT / "modules" / "tag_text.py",
    REPO_ROOT / "modules" / "segment_model.py",
    REPO_ROOT / "modules" / "grid_editors.py",
    REPO_ROOT / "modules" / "main_dialogs.py",
    REPO_ROOT / "modules" / "translation_workers.py",
    REPO_ROOT / "modules" / "superlookup_tab.py",
    REPO_ROOT / "modules" / "colour_checkboxes.py",
]
DEFAULT_TEMPLATE_PATH = REPO_ROOT / "translations" / "supervertaler_template.xlf"

# Source language for the XLIFF <file> header. ISO 639-1.
SOURCE_LANG = "en"

# Tool identification baked into the generated header for traceability.
TOOL_ID = "supervertaler-extract-strings"
TOOL_NAME = "Supervertaler i18n string extractor"
TOOL_VERSION = "1.0"


# ─── Data classes ─────────────────────────────────────────────────────────


@dataclass
class ExtractedString:
    """One translatable string from the source code.

    ``context`` is the enclosing class name (or ``"<module>"`` for top-level
    code).  ``locations`` accumulates every file:line where the same
    (context, source) pair appears – we coalesce duplicates so translators
    see one entry per unique string, not one per call site.
    """

    context: str
    source: str
    locations: list[tuple[str, int]] = field(default_factory=list)
    disambiguation: str | None = None  # second positional arg to tr(), rare


# ─── AST walker ───────────────────────────────────────────────────────────


class _TrCallVisitor(ast.NodeVisitor):
    """Visitor that collects ``self.tr(...)`` and ``QCoreApplication.translate(...)``
    calls.  Class context is tracked so each call is recorded under the name
    of the enclosing class.
    """

    def __init__(self, source_path: Path):
        self._source_path = source_path
        self._class_stack: list[str] = []
        self.entries: list[ExtractedString] = []

    # Class context tracking ----------------------------------------------

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._class_stack.append(node.name)
        try:
            self.generic_visit(node)
        finally:
            self._class_stack.pop()

    def _current_context(self) -> str:
        return self._class_stack[-1] if self._class_stack else "<module>"

    # Function call inspection --------------------------------------------

    def visit_Call(self, node: ast.Call) -> None:
        # Match self.tr("...")  – the workhorse pattern
        if self._is_self_tr(node):
            self._capture_self_tr(node)
        # Match QCoreApplication.translate("Context", "...", ...)  – rarer,
        # used in module-level or static contexts where there's no QObject
        # to host a tr() method.
        elif self._is_qcoreapplication_translate(node):
            self._capture_qcoreapplication_translate(node)
        # Always descend – nested calls / lambdas still count.
        self.generic_visit(node)

    @staticmethod
    def _is_self_tr(node: ast.Call) -> bool:
        func = node.func
        return (
            isinstance(func, ast.Attribute)
            and func.attr == "tr"
            and isinstance(func.value, ast.Name)
            and func.value.id == "self"
        )

    @staticmethod
    def _is_qcoreapplication_translate(node: ast.Call) -> bool:
        func = node.func
        return (
            isinstance(func, ast.Attribute)
            and func.attr == "translate"
            and isinstance(func.value, ast.Name)
            and func.value.id in ("QCoreApplication", "QApplication")
        )

    # Capture helpers -----------------------------------------------------

    def _capture_self_tr(self, node: ast.Call) -> None:
        if not node.args:
            return
        source_arg = node.args[0]
        if not (isinstance(source_arg, ast.Constant) and isinstance(source_arg.value, str)):
            return  # f-strings, names, concatenations – out of scope here
        disambiguation: str | None = None
        if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant):
            if isinstance(node.args[1].value, str):
                disambiguation = node.args[1].value
        self._record(
            context=self._current_context(),
            source=source_arg.value,
            disambiguation=disambiguation,
            lineno=node.lineno,
        )

    def _capture_qcoreapplication_translate(self, node: ast.Call) -> None:
        # Signature: QCoreApplication.translate(context, sourceText, [disambig, [n]])
        if len(node.args) < 2:
            return
        ctx_arg, src_arg = node.args[0], node.args[1]
        if not (isinstance(ctx_arg, ast.Constant) and isinstance(ctx_arg.value, str)):
            return
        if not (isinstance(src_arg, ast.Constant) and isinstance(src_arg.value, str)):
            return
        disambiguation: str | None = None
        if len(node.args) >= 3 and isinstance(node.args[2], ast.Constant):
            if isinstance(node.args[2].value, str):
                disambiguation = node.args[2].value
        self._record(
            context=ctx_arg.value,
            source=src_arg.value,
            disambiguation=disambiguation,
            lineno=node.lineno,
        )

    def _record(
        self,
        *,
        context: str,
        source: str,
        disambiguation: str | None,
        lineno: int,
    ) -> None:
        # Coalesce duplicates by (context, source, disambiguation)
        rel_path = str(self._source_path.relative_to(REPO_ROOT)).replace("\\", "/")
        for existing in self.entries:
            if (
                existing.context == context
                and existing.source == source
                and existing.disambiguation == disambiguation
            ):
                existing.locations.append((rel_path, lineno))
                return
        self.entries.append(
            ExtractedString(
                context=context,
                source=source,
                locations=[(rel_path, lineno)],
                disambiguation=disambiguation,
            )
        )


# ─── Extraction entry point ───────────────────────────────────────────────


def extract_from_files(paths: Iterable[Path]) -> list[ExtractedString]:
    """Walk each path's AST and return the merged list of strings.

    Within-file duplicates are coalesced by ``_TrCallVisitor._record``.
    Cross-file duplicates are merged here.
    """

    merged: list[ExtractedString] = []
    by_key: dict[tuple[str, str, str | None], ExtractedString] = {}

    for path in paths:
        source = path.read_text(encoding="utf-8")
        try:
            tree = ast.parse(source, filename=str(path))
        except SyntaxError as e:
            print(f"  ! Syntax error in {path}: {e}", file=sys.stderr)
            continue
        visitor = _TrCallVisitor(path)
        visitor.visit(tree)
        for entry in visitor.entries:
            key = (entry.context, entry.source, entry.disambiguation)
            if key in by_key:
                by_key[key].locations.extend(entry.locations)
            else:
                by_key[key] = entry
                merged.append(entry)

    return merged


# ─── XLIFF emission ───────────────────────────────────────────────────────


def _make_trans_unit_id(context: str, source: str, disambiguation: str | None) -> str:
    """Stable, human-readable id for a trans-unit.

    Format: ``<context>.<short_source_slug>[+<disambig_slug>]``.  The full
    source text is what's authoritative for matching – this id is purely a
    UI affordance for translators scanning the file.
    """

    def slug(s: str) -> str:
        # Keep alphanumerics, dashes and dots; collapse everything else to "_".
        out = []
        for ch in s.strip():
            if ch.isalnum() or ch in "-.":
                out.append(ch)
            else:
                out.append("_")
        result = "".join(out).strip("_")[:60]
        return result or "string"

    base = f"{slug(context)}.{slug(source)}"
    if disambiguation:
        base += f"+{slug(disambiguation)}"
    return base


def emit_xliff(
    entries: list[ExtractedString],
    *,
    target_lang: str | None = None,
    source_path: str = "Supervertaler.py",
) -> str:
    """Build an XLIFF 1.2 document as a pretty-printed string.

    Set ``target_lang=None`` for a source-only template (no target-language
    attribute on the <file> element, no <target> elements – every translator
    locale starts from this).  Set ``target_lang="zh-CN"`` etc. when emitting
    a locale-specific file.
    """

    XLIFF_NS = "urn:oasis:names:tc:xliff:document:1.2"
    ET.register_namespace("", XLIFF_NS)

    xliff = ET.Element(
        f"{{{XLIFF_NS}}}xliff",
        attrib={"version": "1.2"},
    )

    file_attrs = {
        "original": source_path,
        "source-language": SOURCE_LANG,
        "datatype": "plaintext",
    }
    if target_lang:
        file_attrs["target-language"] = target_lang

    file_el = ET.SubElement(xliff, f"{{{XLIFF_NS}}}file", attrib=file_attrs)

    header = ET.SubElement(file_el, f"{{{XLIFF_NS}}}header")
    ET.SubElement(
        header,
        f"{{{XLIFF_NS}}}tool",
        attrib={"tool-id": TOOL_ID, "tool-name": TOOL_NAME, "tool-version": TOOL_VERSION},
    )

    body = ET.SubElement(file_el, f"{{{XLIFF_NS}}}body")

    for entry in entries:
        tu_id = _make_trans_unit_id(entry.context, entry.source, entry.disambiguation)
        tu_attrs = {"id": tu_id}
        trans_unit = ET.SubElement(body, f"{{{XLIFF_NS}}}trans-unit", attrib=tu_attrs)

        src_el = ET.SubElement(trans_unit, f"{{{XLIFF_NS}}}source")
        src_el.text = entry.source

        # Always emit a <target> (even in templates) so CAT tools that
        # require a target element to open the file don't complain. State
        # "needs-translation" makes the empty target explicit.
        tgt_attrs = {"state": "needs-translation"}
        ET.SubElement(trans_unit, f"{{{XLIFF_NS}}}target", attrib=tgt_attrs)

        if entry.disambiguation:
            note = ET.SubElement(
                trans_unit,
                f"{{{XLIFF_NS}}}note",
                attrib={"from": "developer"},
            )
            note.text = f"Disambiguation: {entry.disambiguation}"

        # Context group: Qt context (class name) + source-code references.
        # Translators read this to understand WHERE in the UI the string
        # appears, which often matters more than the literal text alone.
        ctx_group = ET.SubElement(
            trans_unit,
            f"{{{XLIFF_NS}}}context-group",
            attrib={"purpose": "location"},
        )
        qt_ctx = ET.SubElement(
            ctx_group,
            f"{{{XLIFF_NS}}}context",
            attrib={"context-type": "x-qt-context"},
        )
        qt_ctx.text = entry.context
        for src_path, lineno in entry.locations:
            src_ref = ET.SubElement(
                ctx_group,
                f"{{{XLIFF_NS}}}context",
                attrib={"context-type": "sourcefile"},
            )
            src_ref.text = src_path
            line_ref = ET.SubElement(
                ctx_group,
                f"{{{XLIFF_NS}}}context",
                attrib={"context-type": "linenumber"},
            )
            line_ref.text = str(lineno)

    # Pretty-print via ET.indent (Python 3.9+). The older minidom.toprettyxml
    # path was bug-ridden: it doubled every whitespace text node, producing
    # files with two blank lines between every element. ET.indent is the
    # cleaner stdlib option introduced specifically to fix that misbehaviour.
    ET.indent(xliff, space="  ")
    return ET.tostring(xliff, encoding="unicode", xml_declaration=True)


# ─── CLI ──────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    argv = argv or sys.argv[1:]

    out_path = DEFAULT_TEMPLATE_PATH
    sources = DEFAULT_SOURCES

    # Trivial argparse without importing argparse: --out PATH overrides.
    i = 0
    while i < len(argv):
        if argv[i] in ("--out", "-o") and i + 1 < len(argv):
            out_path = Path(argv[i + 1])
            i += 2
        elif argv[i].startswith("--"):
            print(f"Unknown flag: {argv[i]}", file=sys.stderr)
            return 2
        else:
            # Positional = additional source file
            sources.append(Path(argv[i]))
            i += 1

    print(f"Extracting from {len(sources)} source file(s):")
    for s in sources:
        print(f"  - {s.relative_to(REPO_ROOT) if s.is_relative_to(REPO_ROOT) else s}")

    entries = extract_from_files(sources)
    print(f"Found {len(entries)} unique translatable string(s).")

    if not entries:
        print("Nothing to write.", file=sys.stderr)
        return 1

    xliff_text = emit_xliff(entries, target_lang=None, source_path="Supervertaler.py")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    # Ensure a trailing newline + UTF-8 with no BOM. ET.tostring(encoding="unicode")
    # returns text without a trailing newline, which most diff tools and editors
    # complain about, hence the explicit + "\n".
    out_path.write_text(xliff_text.rstrip() + "\n", encoding="utf-8", newline="\n")
    print(f"Wrote: {out_path.relative_to(REPO_ROOT)}  ({out_path.stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
