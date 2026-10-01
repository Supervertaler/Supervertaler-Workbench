"""
Supervertaler project packages (.svpkg) – issue #156
=====================================================

One file that holds everything needed to carry on with a project elsewhere:
another computer, or a colleague.

A ``.svpkg`` is a zip archive:

    manifest.json                 what's inside (format, project, resources)
    project/<name>.svproj         the project file
    project/source/ target/ tm/   the project folder's own subfolders
            glossary/ reports/ qa/
    resources/tm/<TM>.tmx         the TMs switched on (Read) for the project
    resources/glossary/<TB>.tsv   the glossaries switched on for the project
    resources/prompts/<name>.md   the project's custom prompt and attachments

Only the project file and those known subfolders are packed – never other
files that happen to sit next to the project file. The ``resources`` are
exported from (and on opening imported back into) Supervertaler's central
database, where TMs, glossaries and prompts live.

This module only builds, reads and unpacks archives; the app does the
exporting and importing of resources (see ``SupervertalerQt``).
"""

import json
import os
import re
import shutil
import zipfile
from datetime import datetime
from typing import Dict, List, Optional, Tuple

FORMAT = "svpkg"
VERSION = 1
MANIFEST = "manifest.json"
PROJECT_DIR = "project"
# The project folder's own subfolders (issue #228); everything else next to
# the .svproj is left out
PROJECT_SUBDIRS = ("source", "target", "tm", "glossary", "reports", "qa")
_SKIP_SUFFIXES = (".svproj.tmp", ".svproj.bak", ".svpkg")


class PackageError(Exception):
    pass


def safe_name(name: str, default: str = "resource") -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", (name or "").strip()).strip(" .")
    return name or default


def project_files(project_dir: str, svproj_path: str) -> List[Tuple[str, str]]:
    """``(absolute path, archive path)`` for the project file and the files
    in its known subfolders."""
    files = [(svproj_path, f"{PROJECT_DIR}/{os.path.basename(svproj_path)}")]
    for sub in PROJECT_SUBDIRS:
        root = os.path.join(project_dir, sub)
        if not os.path.isdir(root):
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames.sort()
            for fn in sorted(filenames):
                if fn.endswith(_SKIP_SUFFIXES):
                    continue
                full = os.path.join(dirpath, fn)
                rel = os.path.relpath(full, project_dir).replace(os.sep, "/")
                files.append((full, f"{PROJECT_DIR}/{rel}"))
    return files


def build_package(out_path: str, project_dir: str, svproj_path: str,
                  resources: List[Dict], project_info: Dict, app_version: str = "") -> Dict:
    """Write the package. ``resources`` are dicts with ``kind`` ("tm",
    "glossary" or "prompt"), ``name``, ``path`` (a local file to pack) and any
    further metadata to record (languages, original ids, …).

    Returns the manifest. The archive is written to a temporary file and
    moved into place, so a failed export never leaves half a package.
    """
    manifest = {
        "format": FORMAT, "version": VERSION,
        "created": datetime.now().isoformat(timespec="seconds"),
        "app": f"Supervertaler Workbench {app_version}".strip(),
        "project": dict(project_info, file=f"{PROJECT_DIR}/{os.path.basename(svproj_path)}"),
        "resources": [],
    }
    folders = {"tm": "resources/tm", "glossary": "resources/glossary", "prompt": "resources/prompts"}
    used = set()
    entries = []
    for res in resources:
        folder = folders[res["kind"]]
        ext = os.path.splitext(res["path"])[1] or {"tm": ".tmx", "glossary": ".tsv", "prompt": ".md"}[res["kind"]]
        base = safe_name(res.get("name"))
        arc = f"{folder}/{base}{ext}"
        n = 2
        while arc.lower() in used:
            arc = f"{folder}/{base} ({n}){ext}"
            n += 1
        used.add(arc.lower())
        meta = {k: v for k, v in res.items() if k != "path"}
        meta["file"] = arc
        manifest["resources"].append(meta)
        entries.append((res["path"], arc))

    tmp = out_path + ".tmp"
    try:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
            zf.writestr(MANIFEST, json.dumps(manifest, ensure_ascii=False, indent=2))
            for full, arc in project_files(project_dir, svproj_path):
                zf.write(full, arc)
            for full, arc in entries:
                zf.write(full, arc)
        os.replace(tmp, out_path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    return manifest


def read_manifest(pkg_path: str) -> Dict:
    try:
        with zipfile.ZipFile(pkg_path) as zf:
            manifest = json.loads(zf.read(MANIFEST).decode("utf-8"))
    except (zipfile.BadZipFile, KeyError, ValueError, OSError) as exc:
        raise PackageError(f"Not a Supervertaler package: {exc}") from exc
    if manifest.get("format") != FORMAT:
        raise PackageError("Not a Supervertaler package (no svpkg manifest).")
    if int(manifest.get("version", 0)) > VERSION:
        raise PackageError(f"This package was made by a newer Supervertaler (package version "
                           f"{manifest.get('version')}). Update Supervertaler to open it.")
    return manifest


def _free_folder(parent: str, name: str) -> str:
    path = os.path.join(parent, name)
    n = 2
    while os.path.exists(path):
        path = os.path.join(parent, f"{name} ({n})")
        n += 1
    return path


def _safe_target(root: str, arc_path: str) -> Optional[str]:
    """Where an archive entry goes under ``root``, or None if it would land
    outside it (absolute paths, ``..``)."""
    if arc_path.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:", arc_path):
        return None
    target = os.path.normpath(os.path.join(root, arc_path))
    if os.path.commonpath([os.path.abspath(root), os.path.abspath(target)]) != os.path.abspath(root):
        return None
    return target


def extract_package(pkg_path: str, parent_dir: str) -> Tuple[str, str, Dict]:
    """Unpack into a new folder under ``parent_dir`` (named after the
    project; "(2)" etc. if taken). Returns ``(project_dir, svproj_path,
    manifest)``. Resources land in ``<project_dir>/.svpkg-resources/`` for the
    app to import; entries that would escape the folder are refused."""
    manifest = read_manifest(pkg_path)
    name = safe_name(manifest.get("project", {}).get("name") or
                     os.path.splitext(os.path.basename(pkg_path))[0], "Project")
    project_dir = _free_folder(parent_dir, name)
    os.makedirs(project_dir)
    try:
        return _extract_into(pkg_path, project_dir, manifest)
    except Exception:
        shutil.rmtree(project_dir, ignore_errors=True)  # no half-unpacked project
        raise


def _extract_into(pkg_path: str, project_dir: str, manifest: Dict) -> Tuple[str, str, Dict]:
    res_dir = os.path.join(project_dir, ".svpkg-resources")
    with zipfile.ZipFile(pkg_path) as zf:
        for info in zf.infolist():
            if info.is_dir() or info.filename == MANIFEST:
                continue
            if info.filename.startswith(PROJECT_DIR + "/"):
                target = _safe_target(project_dir, info.filename[len(PROJECT_DIR) + 1:])
            elif info.filename.startswith("resources/"):
                target = _safe_target(res_dir, info.filename[len("resources/"):])
            else:
                continue
            if target is None:
                raise PackageError(f"Unsafe path in package: {info.filename}")
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with zf.open(info) as src, open(target, "wb") as dst:
                while True:
                    chunk = src.read(1 << 20)
                    if not chunk:
                        break
                    dst.write(chunk)
    proj_file = manifest.get("project", {}).get("file", "")
    svproj = _safe_target(project_dir, proj_file[len(PROJECT_DIR) + 1:]) if proj_file.startswith(PROJECT_DIR + "/") else None
    if not svproj or not os.path.isfile(svproj):
        raise PackageError("The package has no project file.")
    for res in manifest.get("resources", []):
        f = res.get("file", "")
        res["local_path"] = _safe_target(res_dir, f[len("resources/"):]) if f.startswith("resources/") else None
    return project_dir, svproj, manifest


def remap_project_settings(svproj_path: str, tm_ids: Dict[str, str], termbase_ids: Dict[int, int],
                           prompt_paths: Dict[str, str]) -> None:
    """Point the unpacked project at this computer's TMs, glossaries and
    prompts: their ids differ from the ones on the computer that packed it."""
    with open(svproj_path, encoding="utf-8") as f:
        data = json.load(f)
    tm = data.get("tm_settings") or {}
    if tm.get("activated_tm_ids"):
        tm["activated_tm_ids"] = [tm_ids.get(t, t) for t in tm["activated_tm_ids"]]
        ro = tm.get("tm_read_only_status") or {}
        tm["tm_read_only_status"] = {tm_ids.get(k, k): v for k, v in ro.items()}
        data["tm_settings"] = tm
    tb = data.get("termbase_settings") or {}
    if tb.get("active_termbase_ids"):
        tb["active_termbase_ids"] = [termbase_ids.get(int(i), i) for i in tb["active_termbase_ids"]]
        prio = tb.get("termbase_priorities") or {}
        tb["termbase_priorities"] = {str(termbase_ids.get(int(k), k)): v for k, v in prio.items()}
        data["termbase_settings"] = tb
    ps = data.get("prompt_settings") or {}
    if ps.get("active_primary_prompt_path") in prompt_paths:
        ps["active_primary_prompt_path"] = prompt_paths[ps["active_primary_prompt_path"]]
    if ps.get("attached_prompt_paths"):
        ps["attached_prompt_paths"] = [prompt_paths.get(p, p) for p in ps["attached_prompt_paths"]]
    if ps:
        data["prompt_settings"] = ps
    tmp = svproj_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, svproj_path)
