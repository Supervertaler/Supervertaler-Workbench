"""
The tools the Supervertaler Bridge offers to desktop chat agents
================================================================

The Supervertaler MCP Server (the small program Claude Desktop or ChatGPT
Desktop starts) is a forwarder: it asks the bridge for its tool list
(``GET /v1/tools``) and passes each tool call on to the bridge path that list
names. This module is Workbench's side of that: the tool list, and the
functions that answer the calls.

Every function here takes the main window and the call's parameters (strings,
straight from the query string) and returns a JSON-ready dict. They read
``window.current_project`` and the grid, so they MUST run on the GUI thread;
``SupervertalerBridgeServer.call_tool`` sees to that. None of them changes the
project, a termbase or a TM. One acts on the window: go_to_segment moves the
cursor, and is marked "write" so the MCP server refuses it when it cannot tell
which Workbench is meant.

A tool whose real work is a database search returns ``RunOffGuiThread``: the
GUI thread only collects what the search needs (which TMs are active), and the
search itself then runs on the bridge's own thread with its own read-only
connection, so a large TM can never freeze the window.

Scale: a segment call walks the project's segment list once (get_segments
twice, the second time only over the page it returns). Designed for projects
of tens of thousands of segments; results are paged so a reply never carries
more than ``MAX_SEGMENTS_PER_CALL`` segments. lookup_term makes one pass over
the in-memory term index Workbench already keeps for TermLens (tested at
50,000 terms). search_tm uses the TM's full-text index, at most 100 rows.

Errors: raise ``BridgeToolError``. Status 200 (the default) reaches the agent
as ordinary JSON ``{"ok": false, "error": ...}``; status 503 is for "not now"
(no project open), whose message the MCP server passes on word for word.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from modules.statuses import STATUSES

DEFAULT_SEGMENTS_PER_CALL = 200
MAX_SEGMENTS_PER_CALL = 500
CONTEXT_SEGMENTS = 2  # segments returned before and after the active one
MAX_TERM_MATCHES = 100
DEFAULT_TM_RESULTS = 20
MAX_TM_RESULTS = 100  # database_manager.concordance_search returns at most 100

Params = Dict[str, str]


class BridgeToolError(Exception):
    """A tool call that cannot be answered. ``status`` is the HTTP status."""

    def __init__(self, message: str, status: int = 200) -> None:
        super().__init__(message)
        self.status = status


class RunOffGuiThread:
    """Returned by a tool to say: the GUI-thread part is done, now call
    ``function()`` on the bridge's own thread and return its result."""

    def __init__(self, function: Callable[[], Dict[str, Any]]) -> None:
        self.function = function


# ── Helpers ────────────────────────────────────────────────────────────────


def _project(window):
    project = getattr(window, "current_project", None)
    if project is None:
        raise BridgeToolError(
            "No project is open in Supervertaler Workbench. Ask the user to open one.",
            status=503,
        )
    return project


def _int(params: Params, name: str, default: Optional[int]) -> Optional[int]:
    raw = (params.get(name) or "").strip()
    if not raw or raw.lower() == "null":
        return default
    try:
        return int(raw)
    except ValueError:
        raise BridgeToolError(f"'{name}' must be a whole number, got '{raw}'.")


def _segment_dict(segment) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "id": segment.id,
        "source": segment.source,
        "target": segment.target,
        "status": segment.status,
        "locked": bool(segment.locked),
    }
    if segment.file_name:
        out["file"] = segment.file_name
    if segment.match_percent is not None:
        out["matchPercent"] = segment.match_percent
    if segment.comments:
        out["commentCount"] = len(segment.comments)
    return out


def _files(project) -> List[Dict[str, Any]]:
    """The project's files with their segment counts, counted from the segments
    themselves so the figure is right after splits, merges and deletions."""
    counts: Dict[Any, int] = {}
    for segment in project.segments:
        counts[segment.file_id] = counts.get(segment.file_id, 0) + 1

    if project.files:
        return [
            {"id": f.get("id"), "name": f.get("name", ""), "segmentCount": counts.get(f.get("id"), 0)}
            for f in project.files
        ]
    return [{"id": None, "name": project.name, "segmentCount": len(project.segments)}]


def _active_segment_id(window) -> Optional[int]:
    getter = getattr(window, "_get_current_segment_id", None)
    return getter() if getter else None


# ── Tools ──────────────────────────────────────────────────────────────────


def get_active_project(window, params: Params) -> Dict[str, Any]:
    project = _project(window)
    status_counts: Dict[str, int] = {}
    source_words = 0
    for segment in project.segments:
        status_counts[segment.status] = status_counts.get(segment.status, 0) + 1
        source_words += len(segment.source.split())

    return {
        "ok": True,
        "application": "Supervertaler Workbench",
        "name": project.name,
        "sourceLanguage": project.source_lang,
        "targetLanguage": project.target_lang,
        "projectFile": getattr(window, "project_file_path", None),
        "unsavedChanges": bool(getattr(window, "project_modified", False)),
        "totalSegments": len(project.segments),
        "segmentsByStatus": status_counts,
        "sourceWordsApprox": source_words,
        "fileCount": len(project.files) if project.files else 1,
        "activeSegmentId": _active_segment_id(window),
    }


def get_files(window, params: Params) -> Dict[str, Any]:
    project = _project(window)
    active_id = _active_segment_id(window)
    active_file_id = next((s.file_id for s in project.segments if s.id == active_id), None)
    files = _files(project)
    for f in files:
        f["isActive"] = active_id is not None and f["id"] == active_file_id
    return {"ok": True, "files": files}


def get_segments(window, params: Params) -> Dict[str, Any]:
    project = _project(window)

    status = (params.get("status") or "").strip()
    if status and status not in STATUSES:
        raise BridgeToolError(
            f"Unknown status '{status}'. Use one of: {', '.join(STATUSES)}."
        )
    contains = (params.get("contains") or "").casefold()
    file_filter = (params.get("file") or "").strip().casefold()
    from_number = _int(params, "fromNumber", None)
    to_number = _int(params, "toNumber", None)
    offset = max(0, _int(params, "offset", 0))
    limit = min(MAX_SEGMENTS_PER_CALL, max(1, _int(params, "limit", DEFAULT_SEGMENTS_PER_CALL)))

    if file_filter:
        files = _files(project)
        if not any(file_filter == str(f["id"]).casefold() or file_filter in f["name"].casefold() for f in files):
            names = "; ".join(f["name"] for f in files)
            raise BridgeToolError(f"No file of this project matches '{params.get('file')}'. Files: {names}.")

    def matches(segment) -> bool:
        if status and segment.status != status:
            return False
        if from_number is not None and segment.id < from_number:
            return False
        if to_number is not None and segment.id > to_number:
            return False
        if file_filter and not (
            file_filter == str(segment.file_id).casefold()
            or file_filter in (segment.file_name or "").casefold()
        ):
            return False
        if contains and contains not in segment.source.casefold() and contains not in segment.target.casefold():
            return False
        return True

    matching = [s for s in project.segments if matches(s)]
    page = matching[offset:offset + limit]
    truncated = offset + len(page) < len(matching)

    result: Dict[str, Any] = {
        "ok": True,
        "project": project.name,
        "totalSegments": len(project.segments),
        "totalMatching": len(matching),
        "offset": offset,
        "returned": len(page),
        "truncated": truncated,
        "segments": [_segment_dict(s) for s in page],
    }
    if truncated:
        result["nextOffset"] = offset + len(page)
    return result


def get_active_segment(window, params: Params) -> Dict[str, Any]:
    project = _project(window)
    active_id = _active_segment_id(window)
    index = next((i for i, s in enumerate(project.segments) if s.id == active_id), None)
    if index is None:
        return {
            "ok": True,
            "active": None,
            "note": "No segment is selected in the grid. Ask the user to click into one.",
        }

    segments = project.segments
    return {
        "ok": True,
        "project": project.name,
        "sourceLanguage": project.source_lang,
        "targetLanguage": project.target_lang,
        "active": _segment_dict(segments[index]),
        "before": [_segment_dict(s) for s in segments[max(0, index - CONTEXT_SEGMENTS):index]],
        "after": [_segment_dict(s) for s in segments[index + 1:index + 1 + CONTEXT_SEGMENTS]],
    }


def get_comments(window, params: Params) -> Dict[str, Any]:
    project = _project(window)
    segment_id = _int(params, "id", None)
    if segment_id is None:
        raise BridgeToolError("'id' is required: the segment id from get_segments.")
    segment = next((s for s in project.segments if s.id == segment_id), None)
    if segment is None:
        raise BridgeToolError(f"This project has no segment {segment_id}.")

    comments = []
    for index, comment in enumerate(segment.comments):
        entry: Dict[str, Any] = {
            "index": index,
            "text": comment.text,
            "author": comment.author,
            "created": comment.created,
            "scope": "range" if comment.is_anchored else "segment",
            "fromSourceDocument": bool(comment.imported),
        }
        if comment.is_anchored:
            text = segment.source if comment.anchor_field == "source" else segment.target
            entry["side"] = comment.anchor_field
            entry["on"] = text[comment.anchor_start:comment.anchor_end]
        comments.append(entry)
    return {"ok": True, "id": segment.id, "comments": comments}


def _prompt_library(window):
    library = getattr(getattr(window, "prompt_manager_qt", None), "library", None)
    if library is None:
        raise BridgeToolError("Workbench's prompt library is not loaded yet. Try again in a moment.", status=503)
    return library


def _prompt_summary(relative_path: str, data: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "name": data.get("name", ""),
        "relativePath": relative_path.replace("\\", "/"),
        "category": str(data.get("_folder", "")).replace("\\", "/"),
        "description": data.get("description", ""),
        "isDefault": bool(data.get("default")),
        "isQuickLauncher": bool(data.get("quicklauncher")),
    }


def list_prompts(window, params: Params) -> Dict[str, Any]:
    library = _prompt_library(window)
    category = (params.get("category") or "").casefold()
    query = (params.get("query") or "").casefold()

    prompts = []
    for relative_path, data in library.prompts.items():
        summary = _prompt_summary(relative_path, data)
        if category and category not in summary["category"].casefold():
            continue
        if query and query not in summary["name"].casefold() and query not in summary["description"].casefold():
            continue
        prompts.append(summary)
    prompts.sort(key=lambda p: p["relativePath"].casefold())
    return {"ok": True, "count": len(prompts), "prompts": prompts}


def get_prompt(window, params: Params) -> Dict[str, Any]:
    library = _prompt_library(window)
    path = (params.get("path") or "").strip().replace("\\", "/")
    name = (params.get("name") or "").strip()
    if not path and not name:
        raise BridgeToolError("Pass 'path' (the relativePath from list_prompts) or 'name'.")

    for relative_path, data in library.prompts.items():
        if (path and relative_path.replace("\\", "/") == path) or (not path and data.get("name") == name):
            result = _prompt_summary(relative_path, data)
            result["ok"] = True
            result["content"] = data.get("content", "")
            return result
    raise BridgeToolError(f"No prompt found for '{path or name}'. Call list_prompts for the ones there are.")


def lookup_term(window, params: Params) -> Dict[str, Any]:
    _project(window)
    text = (params.get("text") or "").strip()
    if not text:
        raise BridgeToolError("'text' is required: the text to look for terms in.")

    found = window.find_termbase_matches_in_source(text)
    matches = []
    for info in found.values():
        entry: Dict[str, Any] = {
            "source": info.get("source", ""),
            "target": info.get("translation", ""),
            "termbase": info.get("termbase_name", ""),
        }
        if info.get("is_project_termbase"):
            entry["projectTermbase"] = True
        if info.get("forbidden"):
            entry["forbidden"] = True
        if info.get("is_nontranslatable"):
            entry["nonTranslatable"] = True
        synonyms = [s for s in info.get("target_synonyms") or [] if isinstance(s, str) and s]
        if synonyms:
            entry["targetSynonyms"] = synonyms
        for key, name in (("definition", "definition"), ("notes", "notes"), ("domain", "domain")):
            if info.get(key):
                entry[name] = info[key]
        matches.append(entry)

    # Longest source term first: the most specific match is the one to follow.
    matches.sort(key=lambda m: (-len(m["source"]), m["source"].casefold()))
    result: Dict[str, Any] = {
        "ok": True,
        "count": len(matches),
        "matches": matches[:MAX_TERM_MATCHES],
        "truncated": len(matches) > MAX_TERM_MATCHES,
    }
    if not matches and not getattr(window, "termbase_index", None):
        result["note"] = ("No termbase with terms is active for this project, so nothing can match. "
                          "Termbases are ticked per project in Workbench's Termbases tab.")
    return result


def _tm_rows(window) -> List[Dict[str, Any]]:
    """Every TM with whether it is active for the open project. GUI thread."""
    project = _project(window)
    manager = getattr(window, "tm_metadata_mgr", None)
    database = getattr(window, "db_manager", None)
    if manager is None or database is None:
        raise BridgeToolError("Workbench's translation memories are not loaded yet. Try again in a moment.",
                              status=503)
    project_id = getattr(project, "id", None)
    active = set(manager.get_active_tm_ids(project_id))
    # Not tm_metadata_mgr.get_all_tms(): that counts every unit of every TM,
    # which is a slow way to learn six names.
    cursor = database.get_reader_connection().cursor()
    try:
        cursor.execute("SELECT name, tm_id, source_lang, target_lang, is_project_tm, read_only, project_id "
                       "FROM translation_memories ORDER BY name COLLATE NOCASE")
        return [{
            "name": row[0],
            "tmId": row[1],
            "sourceLanguage": row[2],
            "targetLanguage": row[3],
            # "Project TM" is a flag a TM keeps from the project it was made
            # for. Only say it for the project that is open, or the assistant
            # reports another project's TM as this one's.
            "projectTm": bool(row[4]) and project_id is not None and row[6] == project_id,
            "readOnly": bool(row[5]),
            "activeForProject": row[1] in active,
        } for row in cursor.fetchall()]
    finally:
        cursor.close()


def list_tms(window, params: Params) -> Dict[str, Any]:
    tms = _tm_rows(window)
    return {"ok": True, "count": len(tms), "tms": tms}


def search_tm(window, params: Params):
    text = (params.get("text") or "").strip()
    if not text:
        raise BridgeToolError("'text' is required: the words to look for.")
    limit = min(MAX_TM_RESULTS, max(1, _int(params, "limit", DEFAULT_TM_RESULTS)))
    all_tms = (params.get("allTms") or "").strip().lower() == "true"

    tms = _tm_rows(window)
    names = {tm["tmId"]: tm["name"] for tm in tms}
    tm_ids = None if all_tms else [tm["tmId"] for tm in tms if tm["activeForProject"]]
    if tm_ids is not None and not tm_ids:
        return {"ok": True, "count": 0, "matches": [],
                "note": "No TM is active for this project. Pass allTms=true to search every TM, "
                        "or tick TMs for the project in Workbench's TMs tab."}
    database = window.db_manager

    def search() -> Dict[str, Any]:
        rows = database.concordance_search(text, tm_ids=tm_ids, connection=database.get_reader_connection())
        matches = [{
            "source": row.get("source", row.get("source_text", "")),
            "target": row.get("target", row.get("target_text", "")),
            "tm": names.get(row.get("tm_id"), row.get("tm_id", "")),
            "modified": row.get("modified_date", ""),
        } for row in rows[:limit]]
        return {
            "ok": True,
            "searched": "all TMs" if all_tms else "the TMs active for this project",
            "count": len(matches),
            "truncated": len(rows) > limit or len(rows) >= MAX_TM_RESULTS,
            "matches": matches,
        }

    return RunOffGuiThread(search)


def go_to_segment(window, params: Params) -> Dict[str, Any]:
    project = _project(window)
    segment_id = _int(params, "id", None)
    if segment_id is None:
        raise BridgeToolError("'id' is required: the segment id from get_segments.")
    if not any(s.id == segment_id for s in project.segments):
        raise BridgeToolError(f"This project has no segment {segment_id}.")

    # Switches to the right grid page and the Editor tab, and asks for focus
    # in the target cell.
    window._navigate_to_segment_by_id(segment_id)

    table = getattr(window, "table", None)
    row = window._find_row_for_segment(segment_id) if hasattr(window, "_find_row_for_segment") else -1
    hidden = bool(table is not None and row >= 0 and table.isRowHidden(row))

    # That is not enough when Workbench is not the window in front, which is
    # the normal case here (the user is typing in the chat app). The grid's
    # current row only follows the target cell's focus: the cell under the
    # target editor is a disabled item, which Qt refuses to make current, and
    # a window in the background is given no focus. So make the row current
    # through its number cell, which is an ordinary item. The focus asked for
    # above is still granted when the user comes back to Workbench.
    if table is not None and row >= 0 and not hidden and table.currentRow() != row:
        table.setCurrentCell(row, 0)

    # Say what happened, not what was asked.
    landed = _active_segment_id(window)
    if landed == segment_id:
        return {"ok": True, "activeSegmentId": segment_id}

    # Why not? Tell a filter (the user can clear it) from a fault (they cannot),
    # with what the grid itself says rather than a guess.
    filtered = getattr(window, "_active_text_filter_rows", None) is not None
    if hidden and filtered:
        raise BridgeToolError(
            f"Segment {segment_id} is hidden by the filter that is set in Workbench's grid, so the cursor "
            "was not moved. Ask the user to clear the filter, then try again.")
    raise BridgeToolError(
        f"Workbench did not move to segment {segment_id} (the cursor is on segment {landed}). No text "
        f"filter explains it: grid row {row}, row hidden: {hidden}, text filter set: {filtered}. Tell the "
        "user this looks like a fault in Workbench and give them these details.")


HELP_CARD = """\
## Supervertaler Workbench – what you can ask

I can look at the project that is open in Workbench and move the cursor for you, but I **cannot change** your translations, termbases or TMs.

- **Where am I?** – the open project, its languages, progress per status, and the segment you are in.
- **Show me segments** – all of them or a selection: by status, by number range, by file, or containing some text.
- **Review my translation** – I read source and target and point out problems, citing segment numbers.
- **Take me to segment 120** – I move Workbench's cursor there.
- **Which terms apply here?** – I look up the terms your active termbases have for a piece of text.
- **How did I translate this before?** – I search your translation memories for a word or phrase.
- **What do the comments say?** – the comments on a segment.
- **Which prompts do I have?** – list the prompt library and read a prompt.

To change anything, do it in Workbench itself. Writing from the assistant comes in a later version.
"""


def help_card(window, params: Params) -> Dict[str, Any]:
    return {"ok": True, "markdown": HELP_CARD}


# ── The registry the MCP server reads ──────────────────────────────────────

_STRING = {"type": "string"}
_INTEGER = {"type": ["integer", "null"]}


def _tool(name: str, path: str, handler: Callable, description: str,
          properties: Optional[Dict[str, Any]] = None, required: Optional[List[str]] = None,
          method: str = "GET", access: str = "read") -> Dict[str, Any]:
    schema: Dict[str, Any] = {"type": "object", "properties": properties or {}}
    if required:
        schema["required"] = required
    return {
        "name": name,
        "access": access,
        "description": description,
        "inputSchema": schema,
        "method": method,
        "path": path,
        "paramMap": {},
        "fixedQuery": {},
        "fixedBody": {},
        "_handler": handler,
    }


_TOOLS: List[Dict[str, Any]] = [
    _tool("help", "/v1/help", help_card,
          "Show the user a curated menu of what they can ask this Supervertaler Workbench assistant to do. "
          "ALWAYS call this when the user asks what they can do, what you can do, which commands or tools "
          "there are, or asks for help - then present the returned card verbatim (it is Markdown)."),
    _tool("get_active_project", "/v1/project", get_active_project,
          "Get the project open in Supervertaler Workbench: name, source and target language, the project "
          "file, whether it has unsaved changes, segment counts per status, an approximate source word "
          "count and the id of the segment the translator is in. Call this first to orient yourself. "
          "Translations, termbases and TMs cannot be changed through this connection."),
    _tool("get_files", "/v1/files", get_files,
          "List the files of the project open in Workbench: id, name, segment count, and which one the "
          "translator is in. Most projects have one file. Use a name or id with get_segments' 'file' filter."),
    _tool("get_segments", "/v1/segments", get_segments,
          "List segments of the project open in Workbench. Each has 'id' - the segment number the user sees "
          "in the first column of Workbench's grid; ALWAYS cite it when you refer to a segment, never invent "
          "numbers - plus source, target, status, locked, and where they apply file, matchPercent and "
          "commentCount. Inline formatting appears as tags such as <b>…</b> or <t1>…</t1>: they are part of "
          "the text, so keep them in any translation you propose. Use the filters to fetch only what you "
          "need. Results are paged with limit/offset: when 'truncated' is true, call again with "
          "offset=nextOffset. To read ONE segment the user mentions by number, pass fromNumber and toNumber "
          "with that same number.",
          {
              "status": {**_STRING, "description":
                         "Only segments with this status: " + ", ".join(STATUSES) + ". Omit for all."},
              "contains": {**_STRING, "description":
                           "Only segments whose source or target contains this text (case-insensitive)."},
              "file": {**_STRING, "description":
                       "Only segments from this file: a file id or (part of) a file name from get_files."},
              "fromNumber": {**_INTEGER, "description": "First segment number to include."},
              "toNumber": {**_INTEGER, "description": "Last segment number to include (inclusive)."},
              "limit": {**_INTEGER, "description":
                        f"Maximum segments to return (default {DEFAULT_SEGMENTS_PER_CALL}, "
                        f"at most {MAX_SEGMENTS_PER_CALL})."},
              "offset": {**_INTEGER, "description": "Number of matching segments to skip, for paging (default 0)."},
          }),
    _tool("get_active_segment", "/v1/active-context", get_active_segment,
          "Get the segment the translator is in right now in Workbench, with the two segments before and "
          "after it and the project's languages. Use this when the user says 'this segment' or asks about "
          "what they are editing. 'active' is null when no segment is selected."),
    _tool("get_comments", "/v1/comments", get_comments,
          "Read the comments on one segment (text, author, date). 'scope' is 'segment' for a comment on the "
          "whole segment or 'range' for one attached to part of it; a range comment also has 'side' "
          "(source or target) and 'on', the exact words it is attached to. fromSourceDocument marks a "
          "comment that came in with the imported document.",
          {"id": {"type": "integer", "description": "Segment id from get_segments."}},
          ["id"]),
    _tool("go_to_segment", "/v1/go-to-segment", go_to_segment,
          "Move Workbench's cursor to a segment and bring it into view, as if the translator had clicked "
          "it. Use it when the user asks to be taken to a segment, or to show them the one you are "
          "talking about. It changes no text. When the cursor does not end up on that segment it fails "
          "and says why: relay the reason as given, do not guess one.",
          {"id": {"type": "integer", "description": "Segment id from get_segments."}},
          ["id"], method="POST", access="write"),
    _tool("lookup_term", "/v1/term-lookup", lookup_term,
          "Find the terms that the termbases active for this project have for a piece of text: pass a "
          "segment, a phrase or a single word. Returns each match with its source term, target term and "
          "termbase, and where they apply targetSynonyms, definition, notes, domain, projectTermbase, "
          "nonTranslatable and forbidden. A forbidden term must never appear in a translation. Matching "
          "is the same as Workbench's own TermLens (whole words, synonyms and abbreviations included).",
          {"text": {**_STRING, "description": "Text to scan for termbase terms."}},
          ["text"]),
    _tool("list_tms", "/v1/tms", list_tms,
          "List Workbench's translation memories: name, languages, projectTm (it is the open project's "
          "own TM), readOnly, and activeForProject (ticked for the open project, so it is searched and "
          "used for matches)."),
    _tool("search_tm", "/v1/tm-search", search_tm,
          "Search the translation memories for a word or phrase (a concordance search): finds units whose "
          "source or target contains it, newest first, with the TM each came from. Use it for 'how was "
          "this translated before?'. It is a text search, not a fuzzy match on a whole segment. By default "
          "only the TMs active for the open project are searched.",
          {
              "text": {**_STRING, "description": "The word or phrase to look for."},
              "limit": {**_INTEGER, "description":
                        f"Maximum units to return (default {DEFAULT_TM_RESULTS}, at most {MAX_TM_RESULTS})."},
              "allTms": {"type": ["boolean", "null"], "description":
                         "true searches every TM, not only those active for the project."},
          },
          ["text"]),
    _tool("list_prompts", "/v1/prompts", list_prompts,
          "List the prompts in Supervertaler Workbench's prompt library: name, description, category "
          "(folder), relativePath (pass it to get_prompt), isDefault (built in) and isQuickLauncher.",
          {
              "category": {**_STRING, "description": "Only prompts whose folder contains this text."},
              "query": {**_STRING, "description": "Only prompts whose name or description contains this text."},
          }),
    _tool("get_prompt", "/v1/prompt", get_prompt,
          "Get the full text of one prompt from Workbench's prompt library, by 'path' (the relativePath "
          "from list_prompts, preferred) or by exact 'name'.",
          {
              "path": {**_STRING, "description": "The prompt's relativePath from list_prompts."},
              "name": {**_STRING, "description": "Exact prompt name. Used only if 'path' is omitted."},
          }),
]

ROUTES: Dict[str, Callable] = {t["path"]: t["_handler"] for t in _TOOLS if t["method"] == "GET"}
POST_ROUTES: Dict[str, Callable] = {t["path"]: t["_handler"] for t in _TOOLS if t["method"] == "POST"}


def registry() -> Dict[str, Any]:
    """The tool list in the shape the MCP server's ToolRegistry parses."""
    return {
        "schemaVersion": 1,
        "tools": [{k: v for k, v in tool.items() if k != "_handler"} for tool in _TOOLS],
    }
