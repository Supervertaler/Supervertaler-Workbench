"""Okapi file-type import options shared by Workbench and its dev tools.

Lives outside Supervertaler.py so that tools/filter_compare.py can extract a
document exactly the way a Workbench import does without loading the whole
PyQt application.
"""

# ── Okapi file-type import options ──────────────────────────────────
# Per-file-type toggles controlling which parts of an Office document
# become translatable segments. Sent to the Okapi sidecar's /extract
# (and /merge, for round-trip fidelity). Keys mirror the sidecar's
# FilterService.applyFilterParameters. Defaults follow the principle
# "import real body content; skip incidental metadata" — comments and
# hidden text are OFF by default (translators opt in).
DEFAULT_IMPORT_OPTIONS = {
    # Word / DOCX
    # Comments are three-way: "off" (skip), "comments" (import as anchored,
    # author-tagged comments), or "segments" (import as translatable text).
    "word_comments_mode": "off",
    "word_hidden": False,           # hidden text
    "word_headers_footers": True,   # page headers & footers
    "word_doc_properties": False,   # title/author/keywords (docProps)
    "word_exclude_shapes": True,    # exclude auto-names "Shape 16", "Group …"
    "word_accept_revisions": True,  # auto-accept tracked changes (take final text)
    # Excel / XLSX
    "excel_hidden": False,          # hidden rows/columns/sheets
    "excel_sheet_names": False,     # worksheet tab names
    "excel_drawings": True,         # text in shapes/textboxes
    # PowerPoint / PPTX
    "ppt_notes": False,             # speaker notes
    "ppt_comments": False,          # review comments
    "ppt_hidden": False,            # hidden slides
    "ppt_masters": True,            # slide masters / layouts
}


def okapi_subdoc_category(sub_doc: str) -> str:
    """Map an Okapi sub-document (XML part) name to a segment category.

    Returns one of "comment", "header", "footer", "property", "notes",
    or "" for ordinary body text. Used to label segments in the grid's
    Type column and to let users see at a glance where each came from.
    """
    s = (sub_doc or "").lower().replace("\\", "/")
    if "comments" in s:                              # word/comments.xml, ppt comments
        return "comment"
    if "/footer" in s or s.startswith("footer"):
        return "footer"
    if "/header" in s or s.startswith("header"):
        return "header"
    if "notesslide" in s:                            # ppt/notesSlides/notesSlideN.xml
        return "notes"
    if s.startswith("docprops") or "/docprops" in s \
            or s.endswith("core.xml") or s.endswith("app.xml"):
        return "property"
    return ""


def okapi_sidecar_options(opts):
    """Translate Workbench import options into the flat boolean options the
    Okapi sidecar understands.

    The sidecar only knows a boolean ``word_comments`` (extract comments as
    translatable text units or not). The Workbench exposes a three-way
    ``word_comments_mode`` ("off" / "comments" / "segments") – only the
    "segments" mode asks Okapi to extract them; "comments" handles them
    Workbench-side, so the sidecar must NOT touch them.
    """
    if not opts:
        return opts
    out = dict(opts)
    mode = out.pop("word_comments_mode", None)
    if mode is not None:
        out["word_comments"] = (mode == "segments")
    return out


# UI metadata for the import-options checkboxes, grouped by format.
# (key, label, tooltip). Used by both the Settings → File Types panel and
# the per-import override in the import dialogs.
IMPORT_OPTION_GROUPS = [
    ("word", "Word (DOCX)", [
        ("word_comments_mode",   "Comments",                   "How to handle Word review comments: skip, import as actual (author-tagged) comments, or import as translatable text"),
        ("word_hidden",          "Import hidden text",         "Text formatted as hidden"),
        ("word_headers_footers", "Import headers && footers",  "Page headers and footers"),
        ("word_doc_properties",  "Import document properties", "Title, author, keywords, etc."),
        ("word_exclude_shapes",  "Skip drawing/shape names",   "Exclude auto-generated object names like 'Shape 16', 'Group 574248'"),
        ("word_accept_revisions","Accept tracked changes",     "Use the final (accepted) text of any tracked changes"),
    ]),
    ("excel", "Excel (XLSX)", [
        ("excel_hidden",      "Import hidden rows/columns/sheets", ""),
        ("excel_sheet_names", "Import sheet (tab) names",          ""),
        ("excel_drawings",    "Import text in shapes/text boxes",  ""),
    ]),
    ("ppt", "PowerPoint (PPTX)", [
        ("ppt_notes",    "Import speaker notes",          ""),
        ("ppt_comments", "Import comments",               ""),
        ("ppt_hidden",   "Import hidden slides",           ""),
        ("ppt_masters",  "Import slide masters / layouts", ""),
    ]),
]
