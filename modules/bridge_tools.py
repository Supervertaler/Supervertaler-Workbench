"""
Read-only tools the Supervertaler Bridge offers to desktop chat agents
======================================================================

The Supervertaler MCP Server (the small program Claude Desktop or ChatGPT
Desktop starts) is a forwarder: it asks the bridge for its tool list
(``GET /v1/tools``) and passes each tool call on to the bridge path that list
names. This module is Workbench's side of that: the tool list, and the
functions that answer the calls.

Every function here takes the main window and the call's parameters (strings,
straight from the query string) and returns a JSON-ready dict. They read
``window.current_project`` and the grid, so they MUST run on the GUI thread;
``SupervertalerBridgeServer.call_tool`` sees to that. Nothing here writes.

Scale: a call walks the project's segment list once (get_segments twice, the
second time only over the page it returns). Designed for projects of tens of
thousands of segments; results are paged so a reply never carries more than
``MAX_SEGMENTS_PER_CALL`` segments.

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

Params = Dict[str, str]


class BridgeToolError(Exception):
    """A tool call that cannot be answered. ``status`` is the HTTP status."""

    def __init__(self, message: str, status: int = 200) -> None:
        super().__init__(message)
        self.status = status


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


HELP_CARD = """\
## Supervertaler Workbench – what you can ask

This connection is **read-only** for now: I can look at the project that is open in Workbench, but not change it.

- **Where am I?** – the open project, its languages, progress per status, and the segment you are in.
- **Show me segments** – all of them or a selection: by status, by number range, by file, or containing some text.
- **Review my translation** – I read source and target and point out problems, citing segment numbers.
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
          properties: Optional[Dict[str, Any]] = None, required: Optional[List[str]] = None) -> Dict[str, Any]:
    schema: Dict[str, Any] = {"type": "object", "properties": properties or {}}
    if required:
        schema["required"] = required
    return {
        "name": name,
        "access": "read",
        "description": description,
        "inputSchema": schema,
        "method": "GET",
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
          "This connection is read-only: nothing can be changed in Workbench through it."),
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

ROUTES: Dict[str, Callable] = {tool["path"]: tool["_handler"] for tool in _TOOLS}


def registry() -> Dict[str, Any]:
    """The tool list in the shape the MCP server's ToolRegistry parses."""
    return {
        "schemaVersion": 1,
        "tools": [{k: v for k, v in tool.items() if k != "_handler"} for tool in _TOOLS],
    }
