"""Project packages (.svpkg, issue #156)."""

import json
import os
import sys
import zipfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules import svpkg


def _project(tmp_path):
    d = tmp_path / "Job" / "BRANTS"
    (d / "source").mkdir(parents=True)
    (d / "target").mkdir()
    (d / "tm").mkdir()
    (d / "source" / "patent.docx").write_bytes(b"docx")
    (d / "target" / "patent_nl.docx").write_bytes(b"out")
    (d / "tm" / "BRANTS_backup.tmx").write_text("<tmx/>")
    svproj = d / "BRANTS.svproj"
    svproj.write_text(json.dumps({
        "name": "BRANTS",
        "tm_settings": {"activated_tm_ids": ["client_tm", "other"],
                        "tm_read_only_status": {"client_tm": False}},
        "termbase_settings": {"active_termbase_ids": [7], "termbase_priorities": {"7": 1}},
        "prompt_settings": {"active_primary_prompt_path": "Translate/Patents.md",
                            "attached_prompt_paths": ["Styles/House.md"]},
    }), encoding="utf-8")
    (d / "BRANTS.svproj.bak").write_text("old")
    (d / "notes from client.txt").write_text("not part of the project")   # left out
    (tmp_path / "Job" / "invoice.pdf").write_text("unrelated")
    tmx = tmp_path / "export.tmx"
    tmx.write_text("<tmx>client</tmx>")
    tsv = tmp_path / "terms.tsv"
    tsv.write_text("Source\tTarget\nklep\tvalve\n")
    prompt = tmp_path / "Patents.md"
    prompt.write_text("---\ntype: prompt\n---\nTranslate patents.")
    resources = [
        {"kind": "tm", "name": "Client TM", "path": str(tmx), "tm_id": "client_tm",
         "source_lang": "nl", "target_lang": "en"},
        {"kind": "glossary", "name": "Client: terms", "path": str(tsv), "termbase_id": 7},
        {"kind": "prompt", "name": "Patents", "path": str(prompt), "library_path": "Translate/Patents.md"},
    ]
    return d, svproj, resources


def test_build_and_read(tmp_path):
    d, svproj, resources = _project(tmp_path)
    out = tmp_path / "BRANTS.svpkg"
    manifest = svpkg.build_package(str(out), str(d), str(svproj), resources,
                                   {"name": "BRANTS", "source_lang": "nl", "target_lang": "en"}, "1.10.373")
    names = zipfile.ZipFile(out).namelist()
    assert sorted(names) == sorted([
        "manifest.json", "project/BRANTS.svproj", "project/source/patent.docx",
        "project/target/patent_nl.docx", "project/tm/BRANTS_backup.tmx",
        "resources/tm/Client TM.tmx", "resources/glossary/Client_ terms.tsv",
        "resources/prompts/Patents.md"])
    assert not os.path.exists(str(out) + ".tmp")
    assert svpkg.read_manifest(str(out)) == manifest
    assert manifest["project"]["file"] == "project/BRANTS.svproj"
    assert manifest["app"] == "Supervertaler Workbench 1.10.373"
    assert [r["file"] for r in manifest["resources"]][1] == "resources/glossary/Client_ terms.tsv"
    assert "path" not in manifest["resources"][0]


def test_extract_into_a_free_folder(tmp_path):
    d, svproj, resources = _project(tmp_path)
    out = tmp_path / "BRANTS.svpkg"
    svpkg.build_package(str(out), str(d), str(svproj), resources, {"name": "BRANTS"})
    dest = tmp_path / "other machine"
    dest.mkdir()
    (dest / "BRANTS").mkdir()                       # already taken
    project_dir, svproj2, manifest = svpkg.extract_package(str(out), str(dest))
    assert project_dir == str(dest / "BRANTS (2)")
    assert open(svproj2, encoding="utf-8").read() == svproj.read_text(encoding="utf-8")
    assert (dest / "BRANTS (2)" / "source" / "patent.docx").read_bytes() == b"docx"
    tm = manifest["resources"][0]
    assert open(tm["local_path"]).read() == "<tmx>client</tmx>"
    assert tm["local_path"].startswith(project_dir)


def test_remap_project_settings(tmp_path):
    d, svproj, _ = _project(tmp_path)
    svpkg.remap_project_settings(str(svproj), {"client_tm": "client_tm_2"}, {7: 12},
                                 {"Translate/Patents.md": "Translate/Patents (from package).md"})
    data = json.loads(svproj.read_text(encoding="utf-8"))
    assert data["tm_settings"]["activated_tm_ids"] == ["client_tm_2", "other"]
    assert data["tm_settings"]["tm_read_only_status"] == {"client_tm_2": False}
    assert data["termbase_settings"] == {"active_termbase_ids": [12], "termbase_priorities": {"12": 1}}
    assert data["prompt_settings"]["active_primary_prompt_path"] == "Translate/Patents (from package).md"
    assert data["prompt_settings"]["attached_prompt_paths"] == ["Styles/House.md"]


def test_bad_and_unsafe_packages(tmp_path):
    bad = tmp_path / "x.svpkg"
    bad.write_bytes(b"not a zip")
    with pytest.raises(svpkg.PackageError):
        svpkg.read_manifest(str(bad))
    newer = tmp_path / "newer.svpkg"
    with zipfile.ZipFile(newer, "w") as zf:
        zf.writestr("manifest.json", json.dumps({"format": "svpkg", "version": 99}))
    with pytest.raises(svpkg.PackageError, match="newer Supervertaler"):
        svpkg.read_manifest(str(newer))
    evil = tmp_path / "evil.svpkg"
    with zipfile.ZipFile(evil, "w") as zf:
        zf.writestr("manifest.json", json.dumps({"format": "svpkg", "version": 1,
                                                 "project": {"name": "E", "file": "project/E.svproj"}}))
        zf.writestr("project/E.svproj", "{}")
        zf.writestr("project/../../escaped.txt", "boom")
    with pytest.raises(svpkg.PackageError, match="Unsafe"):
        svpkg.extract_package(str(evil), str(tmp_path / "dest"))
    assert not (tmp_path / "escaped.txt").exists()
    assert not (tmp_path / "dest" / "E").exists()          # nothing half-unpacked left behind
