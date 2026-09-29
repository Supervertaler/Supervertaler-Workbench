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


def bundle_source(original_path, project_dir, subdir=SOURCE_SUBDIR):
    """Copy ``original_path`` into ``<project_dir>/<subdir>/`` and return its
    path relative to ``project_dir`` (POSIX-style, e.g. ``source/file.docx``).

    If the file is already the bundled copy, no copy is made. Returns ``None``
    if ``original_path`` is missing or not a regular file (callers then keep
    their previous behaviour).
    """
    if not original_path or not os.path.isfile(original_path):
        return None
    project_dir = os.path.abspath(project_dir)
    source_dir = os.path.join(project_dir, subdir)
    os.makedirs(source_dir, exist_ok=True)
    bundled = os.path.join(source_dir, os.path.basename(original_path))
    if os.path.abspath(original_path) != os.path.abspath(bundled):
        shutil.copy2(original_path, bundled)
    return os.path.relpath(bundled, project_dir).replace(os.sep, "/")


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
