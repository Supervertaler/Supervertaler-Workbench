"""Guard the project-folder asset helpers (modules/project_assets.py, issue #228).

These pin down the portability contract: a project's source file is copied into
a `source/` subfolder and referenced by a path relative to the project folder,
so moving/renaming the folder never breaks resolution and a project can never
bind to a file outside itself.
"""

from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from types import SimpleNamespace

from modules import project_assets
from modules.project_assets import (
    SOURCE_SUBDIR,
    TARGET_SUBDIR,
    bundle_round_trip_sources,
    bundle_source,
    ensure_target_dir,
    is_in_source_dir,
    nest_in_own_folder,
    resolve_source_path,
    restore_round_trip_sources,
    to_project_relative,
)


def _make_file(path, content=b"hello"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(content)
    return path


# ── bundle_source ──

def test_bundle_copies_external_source_into_source_subdir():
    with tempfile.TemporaryDirectory() as tmp:
        external = _make_file(os.path.join(tmp, "elsewhere", "US123.docx"), b"DOC")
        proj = os.path.join(tmp, "MyProject")
        os.makedirs(proj)
        rel = bundle_source(external, proj)
        assert rel == "source/US123.docx"
        bundled = os.path.join(proj, SOURCE_SUBDIR, "US123.docx")
        assert os.path.isfile(bundled)
        assert open(bundled, "rb").read() == b"DOC"


def test_bundle_is_noop_when_already_bundled():
    with tempfile.TemporaryDirectory() as tmp:
        proj = os.path.join(tmp, "MyProject")
        bundled = _make_file(os.path.join(proj, SOURCE_SUBDIR, "x.docx"), b"A")
        # Calling bundle on the already-bundled file must not error or duplicate.
        rel = bundle_source(bundled, proj)
        assert rel == "source/x.docx"
        assert open(bundled, "rb").read() == b"A"  # untouched


def test_bundle_returns_none_for_missing_file():
    with tempfile.TemporaryDirectory() as tmp:
        assert bundle_source(os.path.join(tmp, "nope.docx"), tmp) is None
        assert bundle_source(None, tmp) is None


def test_bundle_returns_none_for_directory():
    with tempfile.TemporaryDirectory() as tmp:
        d = os.path.join(tmp, "adir")
        os.makedirs(d)
        assert bundle_source(d, tmp) is None


# ── resolve_source_path ──

def test_resolve_relative_against_project_dir():
    with tempfile.TemporaryDirectory() as tmp:
        proj = os.path.join(tmp, "MyProject")
        target = _make_file(os.path.join(proj, "source", "US123.docx"))
        resolved = resolve_source_path("source/US123.docx", proj)
        assert os.path.normpath(resolved) == os.path.normpath(target)
        assert os.path.exists(resolved)


def test_resolve_absolute_is_returned_as_is():
    with tempfile.TemporaryDirectory() as tmp:
        abs_path = _make_file(os.path.join(tmp, "legacy.docx"))
        assert os.path.normpath(resolve_source_path(abs_path, tmp)) == os.path.normpath(abs_path)


def test_resolve_none_for_empty():
    assert resolve_source_path("", "/whatever") is None
    assert resolve_source_path(None, "/whatever") is None


def test_relative_resolution_survives_folder_move():
    # The whole point: a relative path resolves correctly no matter where the
    # project folder is, so moving/renaming it never breaks the binding.
    with tempfile.TemporaryDirectory() as tmp:
        proj_a = os.path.join(tmp, "A")
        _make_file(os.path.join(proj_a, "source", "doc.docx"))
        rel = "source/doc.docx"
        # Pretend the folder was moved to B by resolving the same relative path
        # against a different project dir that also has the file.
        proj_b = os.path.join(tmp, "B")
        _make_file(os.path.join(proj_b, "source", "doc.docx"))
        assert os.path.exists(resolve_source_path(rel, proj_a))
        assert os.path.exists(resolve_source_path(rel, proj_b))


# ── ensure_target_dir ──

def test_ensure_target_dir_creates_and_returns():
    with tempfile.TemporaryDirectory() as tmp:
        proj = os.path.join(tmp, "MyProject")
        os.makedirs(proj)
        target = ensure_target_dir(proj)
        assert os.path.isdir(target)
        assert os.path.basename(target) == TARGET_SUBDIR
        # idempotent
        assert ensure_target_dir(proj) == target


# ── nest_in_own_folder ──

def test_nest_creates_named_folder():
    with tempfile.TemporaryDirectory() as tmp:
        chosen = os.path.join(tmp, "My Project.svproj")
        nested = nest_in_own_folder(chosen)
        assert nested == os.path.join(tmp, "My Project", "My Project.svproj")
        assert os.path.isdir(os.path.join(tmp, "My Project"))


def test_nest_is_noop_when_already_in_own_folder():
    with tempfile.TemporaryDirectory() as tmp:
        own = os.path.join(tmp, "My Project")
        os.makedirs(own)
        chosen = os.path.join(own, "My Project.svproj")
        assert nest_in_own_folder(chosen) == os.path.abspath(chosen)


def test_nest_keeps_the_filename():
    with tempfile.TemporaryDirectory() as tmp:
        nested = nest_in_own_folder(os.path.join(tmp, "abc.svproj"))
        assert os.path.basename(nested) == "abc.svproj"


# ── to_project_relative ──

def test_to_project_relative_inside():
    with tempfile.TemporaryDirectory() as tmp:
        inside = os.path.join(tmp, "source", "f.docx")
        assert to_project_relative(inside, tmp) == "source/f.docx"


def test_to_project_relative_outside_returns_none():
    with tempfile.TemporaryDirectory() as tmp:
        outside = os.path.join(os.path.dirname(tmp.rstrip(os.sep)), "other", "f.docx")
        assert to_project_relative(outside, tmp) is None


if __name__ == "__main__":
    import traceback

    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS {fn.__name__}")
        except Exception:
            failed += 1
            print(f"FAIL {fn.__name__}")
            traceback.print_exc()
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)


# --- safe saving (issue #228) -------------------------------------------------

def test_write_project_file_keeps_the_previous_version_as_bak():
    from modules.project_assets import read_backup, write_project_file
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "Job.svproj")
        assert write_project_file(path, '{"segments": [1]}') is None      # first save: nothing to back up
        backup = write_project_file(path, '{"segments": [1, 2]}')
        assert backup == path + ".bak"
        assert open(backup, encoding="utf-8").read() == '{"segments": [1]}'
        assert open(path, encoding="utf-8").read() == '{"segments": [1, 2]}'
        assert not os.path.exists(path + ".tmp")
        data, saved_at = read_backup(path)
        assert data == {"segments": [1]} and saved_at > 0


def test_a_truncated_project_never_overwrites_a_good_backup():
    from modules.project_assets import write_project_file
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "Job.svproj")
        write_project_file(path, '{"segments": [1]}')
        write_project_file(path, '{"segments": [1, 2]}')                  # .bak = [1]
        with open(path, "w", encoding="utf-8") as f:
            f.write('{"segments": [1, 2')                                  # damaged by something else
        write_project_file(path, '{"segments": [1, 2, 3]}')
        assert open(path + ".bak", encoding="utf-8").read() == '{"segments": [1]}'


def test_a_failed_write_leaves_the_project_untouched():
    from modules import project_assets
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "Job.svproj")
        project_assets.write_project_file(path, '{"segments": [1]}')
        original_replace = os.replace
        try:
            def boom(*a, **k):
                raise OSError("disk full")
            os.replace = boom
            try:
                project_assets.write_project_file(path, '{"segments": [9]}')
            except OSError:
                pass
        finally:
            os.replace = original_replace
        assert open(path, encoding="utf-8").read() == '{"segments": [1]}'
        assert not os.path.exists(path + ".tmp")


def test_read_backup_ignores_missing_or_broken_backups():
    from modules.project_assets import read_backup
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "Job.svproj")
        assert read_backup(path) is None
        with open(path + ".bak", "w", encoding="utf-8") as f:
            f.write("{not json")
        assert read_backup(path) is None


# ── Round-trip sources: memoQ, Trados, CafeTran, SDLPPX, … (issue #228) ──

def _project(**paths):
    fields = dict.fromkeys(project_assets.ROUND_TRIP_SOURCE_FIELDS)
    fields.update(paths)
    return SimpleNamespace(**fields)


def test_round_trip_sources_are_copied_into_source(tmp_path):
    job = tmp_path / "job"
    mq = _make_file(str(job / "file.docx"), b"MEMOQ")
    x1 = _make_file(str(job / "a.sdlxliff"), b"X1")
    x2 = _make_file(str(job / "b.sdlxliff"), b"X2")
    proj = str(tmp_path / "Proj")
    project = _project(memoq_source_path=mq, sdlxliff_source_paths=[x1, x2])

    bundled = bundle_round_trip_sources(project, proj)

    assert bundled == {"memoq_source_path": "source/file.docx",
                       "sdlxliff_source_paths": ["source/a.sdlxliff", "source/b.sdlxliff"]}
    assert open(os.path.join(proj, "source", "file.docx"), "rb").read() == b"MEMOQ"
    assert project.memoq_source_path == mq  # the project keeps using the original


def test_an_unchanged_source_is_not_copied_again(tmp_path, monkeypatch):
    mq = _make_file(str(tmp_path / "job" / "file.docx"), b"MEMOQ")
    proj = str(tmp_path / "Proj")
    project = _project(memoq_source_path=mq)
    bundle_round_trip_sources(project, proj)

    copies = []
    monkeypatch.setattr(project_assets.shutil, "copy2", lambda *a, **k: copies.append(a))
    bundle_round_trip_sources(project, proj)
    assert copies == []

    with open(mq, "ab") as f:  # the original changed: copied again
        f.write(b" v2")
    bundle_round_trip_sources(project, proj)
    assert len(copies) == 1


def test_a_missing_original_keeps_its_copy(tmp_path):
    proj = str(tmp_path / "Proj")
    project = _project(cafetran_source_path=str(tmp_path / "gone.docx"))
    previous = {"cafetran_source_path": "source/gone.docx"}
    assert bundle_round_trip_sources(project, proj, previous=previous) == previous


def test_different_files_with_the_same_name_get_their_own_copies(tmp_path):
    a = _make_file(str(tmp_path / "en" / "doc.sdlxliff"), b"A")
    b = _make_file(str(tmp_path / "nl" / "doc.sdlxliff"), b"BB")
    proj = str(tmp_path / "Proj")
    bundled = bundle_round_trip_sources(_project(sdlxliff_source_paths=[a, b]), proj)
    assert bundled["sdlxliff_source_paths"] == ["source/doc.sdlxliff", "source/doc_2.sdlxliff"]
    assert open(os.path.join(proj, "source", "doc_2.sdlxliff"), "rb").read() == b"BB"


def test_the_main_documents_copy_is_never_overwritten(tmp_path):
    proj = str(tmp_path / "Proj")
    main = _make_file(str(tmp_path / "doc" / "job.docx"), b"MAIN DOCUMENT")
    main_copy = os.path.join(proj, bundle_source(main, proj))
    other = _make_file(str(tmp_path / "memoq" / "job.docx"), b"MEMOQ TABLE")

    bundled = bundle_round_trip_sources(_project(memoq_source_path=other), proj, main_copy=main_copy)
    assert bundled == {"memoq_source_path": "source/job_2.docx"}
    assert open(main_copy, "rb").read() == b"MAIN DOCUMENT"

    # The main document itself, bundled as a round-trip source too, shares the copy
    bundled = bundle_round_trip_sources(_project(memoq_source_path=main), proj, main_copy=main_copy)
    assert bundled == {"memoq_source_path": "source/job.docx"}


def test_restore_uses_the_copy_only_when_the_original_is_gone(tmp_path):
    proj = str(tmp_path / "Proj")
    mq = _make_file(str(tmp_path / "job" / "file.mqxliff"), b"MQ")
    project = _project(mqxliff_source_path=mq)
    bundled = bundle_round_trip_sources(project, proj)

    assert restore_round_trip_sources(project, proj, bundled) == []
    assert project.mqxliff_source_path == mq

    os.remove(mq)  # e.g. the project folder was moved to another computer
    assert restore_round_trip_sources(project, proj, bundled) == ["mqxliff_source_path"]
    assert project.mqxliff_source_path == os.path.join(proj, "source", "file.mqxliff")


def test_restore_after_moving_the_whole_project_folder(tmp_path):
    job = tmp_path / "job"
    x1 = _make_file(str(job / "a.sdlxliff"))
    x2 = _make_file(str(job / "b.sdlxliff"))
    old = str(tmp_path / "Old")
    project = _project(sdlxliff_source_paths=[x1, x2], po_source_path=str(tmp_path / "never.po"))
    bundled = bundle_round_trip_sources(project, old)

    new = str(tmp_path / "Moved")
    os.rename(old, new)
    os.remove(x1)
    os.remove(x2)
    switched = restore_round_trip_sources(project, new, bundled)
    assert switched == ["sdlxliff_source_paths"]
    assert project.sdlxliff_source_paths == [os.path.join(new, "source", "a.sdlxliff"),
                                             os.path.join(new, "source", "b.sdlxliff")]
    assert project.po_source_path == str(tmp_path / "never.po")  # no copy: left as it was


def test_is_in_source_dir(tmp_path):
    proj = str(tmp_path / "Proj")
    assert is_in_source_dir(os.path.join(proj, "source", "x.docx"), proj)
    assert not is_in_source_dir(os.path.join(proj, "target", "x.docx"), proj)
    assert not is_in_source_dir(str(tmp_path / "x.docx"), proj)
    assert not is_in_source_dir(None, proj)
