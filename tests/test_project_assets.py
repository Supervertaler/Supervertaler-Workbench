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

from modules.project_assets import (
    SOURCE_SUBDIR,
    TARGET_SUBDIR,
    bundle_source,
    ensure_target_dir,
    nest_in_own_folder,
    resolve_source_path,
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


# --- safe saving (issue #228) -------------------------------------------------

def _leftover_temp_files(folder):
    return [n for n in os.listdir(folder) if n.endswith(".tmp")]


def test_write_project_file_keeps_the_previous_version_as_bak():
    from modules.project_assets import read_backup, write_project_file
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "Job.svproj")
        assert write_project_file(path, '{"segments": [1]}') is None      # first save: nothing to back up
        backup = write_project_file(path, '{"segments": [1, 2]}')
        assert backup == path + ".bak"
        assert open(backup, encoding="utf-8").read() == '{"segments": [1]}'
        assert open(path, encoding="utf-8").read() == '{"segments": [1, 2]}'
        assert _leftover_temp_files(tmp) == []
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
        assert write_project_file(path, '{"segments": [1, 2, 3]}') is None
        assert open(path + ".bak", encoding="utf-8").read() == '{"segments": [1]}'
        assert open(path, encoding="utf-8").read() == '{"segments": [1, 2, 3]}'


def test_a_failed_write_leaves_the_project_untouched():
    from modules import project_assets
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "Job.svproj")
        project_assets.write_project_file(path, '{"segments": [1]}')
        original_replace = os.replace
        def boom(*a, **k):
            raise OSError("disk full")
        os.replace = boom
        try:
            try:
                project_assets.write_project_file(path, '{"segments": [9]}')
                raise AssertionError("the failed write was not reported")
            except OSError as e:
                assert "disk full" in str(e)
        finally:
            os.replace = original_replace
        assert open(path, encoding="utf-8").read() == '{"segments": [1]}'
        assert _leftover_temp_files(tmp) == []


def test_a_brief_lock_on_the_project_file_is_waited_out():
    # Windows: a virus scanner or sync client holding the .svproj for a moment
    from modules import project_assets
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "Job.svproj")
        project_assets.write_project_file(path, '{"segments": [1]}')
        original_replace, original_sleep = os.replace, project_assets.time.sleep
        calls = []
        def locked_twice(src, dst):
            calls.append(dst)
            if len(calls) <= 2:
                raise PermissionError(13, "Access is denied", dst)
            original_replace(src, dst)
        os.replace, project_assets.time.sleep = locked_twice, lambda s: None
        try:
            project_assets.write_project_file(path, '{"segments": [1, 2]}')
        finally:
            os.replace, project_assets.time.sleep = original_replace, original_sleep
        assert len(calls) == 3
        assert open(path, encoding="utf-8").read() == '{"segments": [1, 2]}'
        assert _leftover_temp_files(tmp) == []


def test_a_lasting_lock_fails_the_save_and_keeps_the_project():
    from modules import project_assets
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "Job.svproj")
        project_assets.write_project_file(path, '{"segments": [1]}')
        original_replace, original_sleep = os.replace, project_assets.time.sleep
        def always_locked(src, dst):
            raise PermissionError(13, "Access is denied", dst)
        os.replace, project_assets.time.sleep = always_locked, lambda s: None
        try:
            try:
                project_assets.write_project_file(path, '{"segments": [9]}')
                raise AssertionError("the failed save was not reported")
            except PermissionError:
                pass
        finally:
            os.replace, project_assets.time.sleep = original_replace, original_sleep
        assert open(path, encoding="utf-8").read() == '{"segments": [1]}'
        assert _leftover_temp_files(tmp) == []


def test_a_backup_that_cannot_be_written_does_not_stop_the_save():
    from modules import project_assets
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "Job.svproj")
        project_assets.write_project_file(path, '{"segments": [1]}')
        original_copy = project_assets.shutil.copy2
        def copy_fails(*a, **k):
            raise PermissionError(13, "Access is denied")
        project_assets.shutil.copy2 = copy_fails
        try:
            assert project_assets.write_project_file(path, '{"segments": [1, 2]}') is None
        finally:
            project_assets.shutil.copy2 = original_copy
        assert open(path, encoding="utf-8").read() == '{"segments": [1, 2]}'


def test_read_backup_ignores_missing_or_broken_backups():
    from modules.project_assets import read_backup
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "Job.svproj")
        assert read_backup(path) is None
        with open(path + ".bak", "w", encoding="utf-8") as f:
            f.write("{not json")
        assert read_backup(path) is None
        with open(path + ".bak", "w", encoding="utf-8") as f:
            f.write('{"name": "not a project"}')                           # valid JSON, no segments
        assert read_backup(path) is None


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

