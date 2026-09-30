"""Project-folder asset handling (issue #228).

Supervertaler projects live in their own folder (the `.svproj` plus the files
it references). To make a project portable — movable, renamable, zippable,
emailable — and to make it impossible for a project to bind to an unrelated
document, the source file is **bundled** into a `source/` subfolder and the
`.svproj` stores it as a path **relative** to the project folder.

This module is the small, pure-ish core (copy + path math), so it can be
unit-tested without the GUI. `Supervertaler.py` calls:

- :func:`bundle_source` when saving — copy the source into `source/` and get
  back the relative path to store in the project.
- :func:`resolve_source_path` when loading — turn the stored path (relative for
  new projects, absolute for legacy ones) back into an absolute path.
- :func:`bundle_round_trip_sources` / :func:`restore_round_trip_sources` for the
  bilingual files and packages a project exports back into (memoQ, Trados,
  CafeTran, …): a copy goes into `source/` too, used when the original is gone.
"""

from __future__ import annotations

import os
import shutil

# Subfolders inside a project folder. Plain names (not language codes) by
# design — see issue #228.
SOURCE_SUBDIR = "source"
TARGET_SUBDIR = "target"


def resolve_source_path(stored_path, project_dir):
    """Resolve a project's stored source path to an absolute path.

    Relative paths resolve against ``project_dir`` (the folder containing the
    ``.svproj``); absolute paths (legacy projects) are returned normalised.
    Returns ``None`` for an empty/None ``stored_path``.
    """
    if not stored_path:
        return None
    if os.path.isabs(stored_path):
        return os.path.normpath(stored_path)
    return os.path.normpath(os.path.join(project_dir, stored_path))


def to_project_relative(abs_path, project_dir):
    """Return ``abs_path`` as a POSIX path relative to ``project_dir`` if it
    lives inside it, otherwise ``None`` (different folder/drive)."""
    try:
        rel = os.path.relpath(abs_path, project_dir)
    except ValueError:
        return None  # e.g. different drive on Windows
    if rel == os.pardir or rel.startswith(os.pardir + os.sep):
        return None
    return rel.replace(os.sep, "/")


def nest_in_own_folder(svproj_path):
    """Return a path that places ``<name>.svproj`` inside a folder named
    ``<name>`` (creating the folder), so a project gets its own tidy home and
    its `source/`/`target/` subfolders don't clutter a shared folder.

    If the file is already in a same-named folder, it's returned unchanged. The
    project file name itself is never changed — only its parent folder.
    """
    svproj_path = os.path.abspath(svproj_path)
    parent = os.path.dirname(svproj_path)
    fname = os.path.basename(svproj_path)
    stem = os.path.splitext(fname)[0]
    if os.path.basename(parent) == stem:
        return svproj_path  # already lives in its own folder
    own = os.path.join(parent, stem)
    os.makedirs(own, exist_ok=True)
    return os.path.join(own, fname)


def ensure_target_dir(project_dir, subdir=TARGET_SUBDIR):
    """Create ``<project_dir>/<subdir>/`` if needed and return its absolute path.

    This is where generated translations (exports) are written by default, so a
    project keeps its outputs alongside its sources.
    """
    target = os.path.join(os.path.abspath(project_dir), subdir)
    os.makedirs(target, exist_ok=True)
    return target


def _is_current_copy(original_path, copy_path):
    """True when ``copy_path`` already holds this version of ``original_path``
    (same size and modification time – ``shutil.copy2`` keeps the time)."""
    try:
        a, b = os.stat(original_path), os.stat(copy_path)
    except OSError:
        return False
    return a.st_size == b.st_size and abs(a.st_mtime - b.st_mtime) < 2


def bundle_source(original_path, project_dir, subdir=SOURCE_SUBDIR, name=None):
    """Copy ``original_path`` into ``<project_dir>/<subdir>/`` and return its
    path relative to ``project_dir`` (POSIX-style, e.g. ``source/file.docx``).

    ``name`` is the file name inside ``subdir`` (default: the original's). If
    the file is already the bundled copy, or an up-to-date copy is already
    there, nothing is copied. Returns ``None`` if ``original_path`` is missing
    or not a regular file (callers then keep their previous behaviour).
    """
    if not original_path or not os.path.isfile(original_path):
        return None
    project_dir = os.path.abspath(project_dir)
    source_dir = os.path.join(project_dir, subdir)
    os.makedirs(source_dir, exist_ok=True)
    bundled = os.path.join(source_dir, name or os.path.basename(original_path))
    if (os.path.abspath(original_path) != os.path.abspath(bundled)
            and not _is_current_copy(original_path, bundled)):
        shutil.copy2(original_path, bundled)
    return os.path.relpath(bundled, project_dir).replace(os.sep, "/")


def is_in_source_dir(path, project_dir, subdir=SOURCE_SUBDIR):
    """True when ``path`` lies inside the project's ``source/`` folder."""
    if not path or not project_dir:
        return False
    rel = to_project_relative(os.path.abspath(path), os.path.abspath(project_dir))
    return bool(rel) and rel.split("/", 1)[0] == subdir


# ── Round-trip sources (issue #228) ─────────────────────────────────────────

# The files a project exports back into, besides its main document
# (``original_docx_path``, bundled by the caller): bilingual tables, XLIFFs and
# packages from other CAT tools. The project keeps pointing at the original –
# exports still land next to it – and the copy in ``source/`` stands in only
# when the original is gone, e.g. after the project folder was moved or sent.
ROUND_TRIP_SOURCE_FIELDS = (
    "trados_source_path",
    "memoq_source_path",
    "mqxliff_source_path",
    "cafetran_source_path",
    "sdlppx_source_path",
    "sdlxliff_source_paths",  # a list
    "original_txt_path",
    "dejavu_source_path",
    "po_source_path",
)


def bundle_round_trip_sources(project, project_dir, previous=None, main_copy=None):
    """Copy every round-trip source of ``project`` into ``source/``.

    Returns ``{field: "source/<name>"}`` (a list for list fields, with ``None``
    for a file that could not be copied). A source that is missing now keeps
    its entry from ``previous`` – the map saved last time – so a project
    opened without its originals doesn't lose track of the bundled copies.
    Two different files with the same name get distinct copies
    (``name_2.ext``); ``main_copy`` is the main document's copy in ``source/``,
    so a different file of the same name never overwrites it.
    """
    previous = previous or {}
    project_dir = os.path.abspath(project_dir)
    bundled, used = {}, {}  # used: name in source/ → the original copied there
    main_name = None
    if main_copy and is_in_source_dir(main_copy, project_dir):
        main_copy = os.path.abspath(main_copy)
        main_name = os.path.basename(main_copy).lower()
    for field in ROUND_TRIP_SOURCE_FIELDS:
        value = getattr(project, field, None)
        if not value:
            continue
        is_list = isinstance(value, (list, tuple))
        paths = list(value) if is_list else [value]
        before = previous.get(field)
        before = (list(before) if isinstance(before, (list, tuple)) else [before]) if before else []
        rels = []
        for i, stored in enumerate(paths):
            path = resolve_source_path(stored, project_dir) if isinstance(stored, str) else None
            rel = None
            if path and os.path.isfile(path):
                name = os.path.basename(path)
                stem, ext = os.path.splitext(name)
                n = 1
                while True:
                    owner = used.get(name.lower())
                    if owner is None and name.lower() == main_name:
                        # The main document's copy: share it only if this is
                        # the same file (the copy is up to date with it).
                        if _is_current_copy(path, main_copy):
                            break
                    elif owner is None or os.path.normcase(owner) == os.path.normcase(path):
                        break
                    n += 1
                    name = f"{stem}_{n}{ext}"
                used.setdefault(name.lower(), path)
                if is_in_source_dir(path, project_dir):
                    rel = to_project_relative(path, project_dir)
                else:
                    rel = bundle_source(path, project_dir, name=name)
            if rel is None and i < len(before):
                rel = before[i]
            rels.append(rel)
        if is_list:
            if any(rels):
                bundled[field] = rels
        elif rels[0]:
            bundled[field] = rels[0]
    return bundled


def restore_round_trip_sources(project, project_dir, bundled):
    """Point each round-trip source whose file is missing at its bundled copy.

    Relative paths are resolved against ``project_dir``. Returns the fields that
    were switched to a copy in ``source/``.
    """
    bundled = bundled or {}
    switched = []
    for field in ROUND_TRIP_SOURCE_FIELDS:
        value = getattr(project, field, None)
        if not value:
            continue
        is_list = isinstance(value, (list, tuple))
        paths = list(value) if is_list else [value]
        copies = bundled.get(field)
        copies = (list(copies) if isinstance(copies, (list, tuple)) else [copies]) if copies else []
        changed = False
        for i, stored in enumerate(paths):
            if not isinstance(stored, str):
                continue
            path = resolve_source_path(stored, project_dir)
            if path and os.path.isfile(path):
                if path != stored:
                    paths[i] = path  # a relative path, made absolute
                continue
            copy = resolve_source_path(copies[i], project_dir) if i < len(copies) and copies[i] else None
            if copy and os.path.isfile(copy):
                paths[i] = copy
                changed = True
        setattr(project, field, paths if is_list else paths[0])
        if changed:
            switched.append(field)
    return switched


# ── Safe saving (issue #228) ────────────────────────────────────────────────

BACKUP_SUFFIX = ".bak"


def _looks_complete(path):
    """Cheap check that a saved project file was written to the end (a JSON
    object ends with "}"), so a damaged file never overwrites a good backup."""
    try:
        size = os.path.getsize(path)
        if size == 0:
            return False
        with open(path, "rb") as f:
            f.seek(max(0, size - 64))
            return f.read().rstrip().endswith(b"}")
    except OSError:
        return False


def write_project_file(path, text):
    """Write a project file safely.

    The previous version is kept as ``<name>.svproj.bak`` (only when it was
    itself complete), and the new content goes to a temporary file next to
    the project that then replaces it in one step – so a save that fails
    part-way (full disk, crash, a value that will not serialise) can no
    longer leave a truncated ``.svproj``. Returns the backup path or None.
    """
    backup = None
    if os.path.exists(path) and _looks_complete(path):
        backup = path + BACKUP_SUFFIX
        shutil.copy2(path, backup)
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    return backup


def read_backup(path):
    """``(data, modified_timestamp)`` from ``<path>.bak`` when it holds a valid
    project, else None – used to offer recovery when a project will not open."""
    import json

    backup = path + BACKUP_SUFFIX
    if not os.path.exists(backup):
        return None
    try:
        with open(backup, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or "segments" not in data:
        return None
    return data, os.path.getmtime(backup)
