"""The bridge's read-only tools for desktop chat agents (Nov 2026).

The Supervertaler MCP Server forwards tool calls to Workbench's localhost
bridge. Two layers are tested here:

* ``modules.bridge_tools`` – the tools themselves, against a stand-in window
  (no Qt needed): what they return, paging, and what they say when the call
  cannot be answered.
* ``modules.supervertaler_bridge_server`` – over real HTTP: the token is
  required, a call runs on the GUI thread, a busy GUI thread gives a clean
  "busy" instead of a hang (and the call then never runs late), and the
  per-process handshake is written, updated, and cleaned up – also after a
  crash left one behind.

Nothing here touches the real data folder: the runtime folder is redirected
to a temp folder.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtCore import QObject
from PyQt6.QtWidgets import QApplication

from modules import bridge_tools
from modules import supervertaler_bridge_server as server_module
from modules.segment_model import Comment, Project, Segment


def make_project(count: int = 10) -> Project:
    segments = [
        Segment(id=i, source=f"Source sentence {i}", target=f"Doelzin {i}" if i % 2 else "",
                status="confirmed" if i % 2 else "not_started")
        for i in range(1, count + 1)
    ]
    return Project(name="Acme PROJ-001", source_lang="en", target_lang="nl", segments=segments)


def make_window(project=None, active_id=None):
    prompts = {
        "Translate\\Patent.md": {"name": "Patent", "description": "For patents", "content": "Translate carefully.",
                                 "_folder": "Translate", "default": True},
        "QuickLauncher\\Explain.md": {"name": "Explain", "description": "", "content": "Explain this.",
                                      "_folder": "QuickLauncher", "quicklauncher": True},
    }
    return SimpleNamespace(
        current_project=project,
        project_file_path=r"D:\Projects\acme.svproj",
        project_modified=True,
        _get_current_segment_id=lambda: active_id,
        prompt_manager_qt=SimpleNamespace(library=SimpleNamespace(prompts=prompts)),
    )


# ── The tools ──────────────────────────────────────────────────────────────


def test_project_summary_counts_statuses_and_names_the_active_segment():
    result = bridge_tools.get_active_project(make_window(make_project(10), active_id=4), {})
    assert result["name"] == "Acme PROJ-001"
    assert result["totalSegments"] == 10
    assert result["segmentsByStatus"] == {"confirmed": 5, "not_started": 5}
    assert result["sourceWordsApprox"] == 30
    assert result["unsavedChanges"] is True
    assert result["activeSegmentId"] == 4


@pytest.mark.parametrize("tool", [bridge_tools.get_active_project, bridge_tools.get_segments,
                                  bridge_tools.get_files, bridge_tools.get_active_segment,
                                  bridge_tools.get_comments])
def test_without_a_project_every_project_tool_says_so(tool):
    with pytest.raises(bridge_tools.BridgeToolError) as error:
        tool(make_window(None), {"id": "1"})
    assert error.value.status == 503
    assert "No project is open" in str(error.value)


def test_segments_are_paged_and_the_page_size_is_capped():
    window = make_window(make_project(1200))
    first = bridge_tools.get_segments(window, {"limit": "100000"})
    assert first["returned"] == bridge_tools.MAX_SEGMENTS_PER_CALL == 500
    assert first["truncated"] is True and first["nextOffset"] == 500

    last = bridge_tools.get_segments(window, {"offset": "1000", "limit": "500"})
    assert last["returned"] == 200
    assert last["truncated"] is False and "nextOffset" not in last
    assert last["segments"][-1]["id"] == 1200


def test_segment_filters():
    window = make_window(make_project(20))
    assert bridge_tools.get_segments(window, {"status": "confirmed"})["totalMatching"] == 10
    one = bridge_tools.get_segments(window, {"fromNumber": "7", "toNumber": "7"})
    assert [s["id"] for s in one["segments"]] == [7]
    assert bridge_tools.get_segments(window, {"contains": "DOELZIN 1"})["totalMatching"] == 6  # 1, 11..19 odd
    # An offset past the end is an empty page, not an error.
    assert bridge_tools.get_segments(window, {"offset": "999"})["returned"] == 0


@pytest.mark.parametrize("params, expected", [
    ({"status": "Translated"}, "Unknown status"),
    ({"limit": "ten"}, "must be a whole number"),
    ({"file": "nosuchfile.docx"}, "No file of this project matches"),
])
def test_bad_segment_parameters_are_explained_not_ignored(params, expected):
    with pytest.raises(bridge_tools.BridgeToolError) as error:
        bridge_tools.get_segments(make_window(make_project(5)), params)
    assert expected in str(error.value)
    assert error.value.status == 200  # reaches the agent as ordinary JSON


def test_file_filter_and_file_list_for_a_multifile_project():
    project = make_project(6)
    project.files = [{"id": 1, "name": "manual.docx"}, {"id": 2, "name": "annex.docx"}]
    for segment in project.segments:
        segment.file_id = 1 if segment.id <= 4 else 2
        segment.file_name = "manual.docx" if segment.id <= 4 else "annex.docx"
    window = make_window(project, active_id=5)

    files = bridge_tools.get_files(window, {})["files"]
    assert [(f["name"], f["segmentCount"], f["isActive"]) for f in files] == [
        ("manual.docx", 4, False), ("annex.docx", 2, True)]
    assert bridge_tools.get_segments(window, {"file": "ANNEX"})["totalMatching"] == 2
    assert bridge_tools.get_segments(window, {"file": "1"})["totalMatching"] == 4


def test_active_segment_comes_with_its_neighbours():
    result = bridge_tools.get_active_segment(make_window(make_project(10), active_id=1), {})
    assert result["active"]["id"] == 1
    assert result["before"] == []
    assert [s["id"] for s in result["after"]] == [2, 3]


def test_no_selected_segment_is_reported_rather_than_guessed():
    result = bridge_tools.get_active_segment(make_window(make_project(3), active_id=None), {})
    assert result["active"] is None and "note" in result


def test_comments_show_what_a_range_comment_is_attached_to():
    project = make_project(3)
    project.segments[1].comments = [
        Comment(text="Whole segment", author="MB"),
        Comment(text="This word", author="MB", anchor_field="source", anchor_start=0, anchor_end=6),
    ]
    result = bridge_tools.get_comments(make_window(project), {"id": "2"})
    assert result["comments"][0]["scope"] == "segment"
    assert result["comments"][1]["scope"] == "range"
    assert result["comments"][1]["on"] == "Source" and result["comments"][1]["side"] == "source"

    with pytest.raises(bridge_tools.BridgeToolError, match="no segment 99"):
        bridge_tools.get_comments(make_window(project), {"id": "99"})


def test_prompts_are_listed_with_forward_slashes_and_read_by_either_spelling():
    window = make_window()
    listed = bridge_tools.list_prompts(window, {})
    assert [p["relativePath"] for p in listed["prompts"]] == ["QuickLauncher/Explain.md", "Translate/Patent.md"]
    assert bridge_tools.list_prompts(window, {"category": "translate"})["count"] == 1

    for path in ("Translate/Patent.md", "Translate\\Patent.md"):
        assert bridge_tools.get_prompt(window, {"path": path})["content"] == "Translate carefully."
    assert bridge_tools.get_prompt(window, {"name": "Explain"})["isQuickLauncher"] is True
    with pytest.raises(bridge_tools.BridgeToolError, match="No prompt found"):
        bridge_tools.get_prompt(window, {"path": "Nope.md"})


def test_the_registry_is_json_and_every_tool_is_read_only_and_routed():
    registry = bridge_tools.registry()
    json.dumps(registry)  # the handlers must not leak into it
    assert len(registry["tools"]) == len(bridge_tools.ROUTES) == 8
    for tool in registry["tools"]:
        assert tool["access"] == "read" and tool["method"] == "GET"
        assert tool["path"] in bridge_tools.ROUTES


def test_a_large_project_is_answered_quickly():
    # Tested at 50,000 segments; a real project is a few thousand.
    window = make_window(make_project(50_000), active_id=49_999)
    started = time.perf_counter()
    bridge_tools.get_active_project(window, {})
    bridge_tools.get_segments(window, {"contains": "sentence 4999", "limit": "500"})
    bridge_tools.get_active_segment(window, {})
    assert time.perf_counter() - started < 2.0


# ── The server, over HTTP ──────────────────────────────────────────────────


class Window(QObject):
    """Stands in for the main window as the bridge's parent."""

    def __init__(self):
        super().__init__()
        self.current_project = make_project(10)
        self.project_file_path = None
        self.project_modified = False

    def _get_current_segment_id(self):
        return 3


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def bridge(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(server_module, "_resolve_user_data_root", lambda: tmp_path)
    window = Window()
    bridge = server_module.SupervertalerBridgeServer(window)
    assert bridge.start()
    yield bridge
    bridge.stop()


def http_get(bridge, path, token="right", pump=True):
    """GET from a second thread (as the MCP server would) while this thread
    plays the GUI thread. Returns (status, parsed JSON)."""
    outcome = {}

    def request():
        req = urllib.request.Request(f"http://127.0.0.1:{bridge.port}{path}")
        if token:
            req.add_header("Authorization", f"Bearer {bridge.token if token == 'right' else token}")
        try:
            with urllib.request.urlopen(req, timeout=20) as response:
                outcome["result"] = (response.status, json.loads(response.read()))
        except urllib.error.HTTPError as error:
            outcome["result"] = (error.code, json.loads(error.read()))

    thread = threading.Thread(target=request)
    thread.start()
    while thread.is_alive():
        if pump:
            QApplication.processEvents()
        time.sleep(0.005)
    return outcome["result"]


def test_calls_without_the_right_token_are_refused(bridge):
    assert http_get(bridge, "/v1/segments", token=None)[0] == 401
    assert http_get(bridge, "/v1/segments", token="wrong")[0] == 403
    assert http_get(bridge, "/v1/tools", token=None)[0] == 401
    assert http_get(bridge, "/v1/nonsense")[0] == 404


def test_the_tool_list_is_served(bridge):
    status, body = http_get(bridge, "/v1/tools")
    assert status == 200
    assert {t["name"] for t in body["tools"]} >= {"get_segments", "get_active_project", "help"}


def test_a_tool_call_runs_on_the_gui_thread_and_returns_its_answer(bridge, monkeypatch):
    seen = {}
    real = bridge_tools.get_segments

    def spy(window, params):
        seen["thread"] = threading.current_thread()
        return real(window, params)

    monkeypatch.setitem(bridge_tools.ROUTES, "/v1/segments", spy)
    status, body = http_get(bridge, "/v1/segments?status=confirmed&contains=Doelzin%203")
    assert status == 200 and [s["id"] for s in body["segments"]] == [3]
    assert seen["thread"] is threading.main_thread()


def test_no_project_open_is_a_503_with_a_message_for_the_user(bridge):
    bridge.parent().current_project = None
    status, body = http_get(bridge, "/v1/project")
    assert status == 503 and "No project is open" in body["error"]


def test_a_tool_that_crashes_gives_an_error_not_a_hang(bridge, monkeypatch):
    def broken(window, params):
        raise RuntimeError("boom")

    monkeypatch.setitem(bridge_tools.ROUTES, "/v1/project", broken)
    status, body = http_get(bridge, "/v1/project")
    assert status == 500 and "boom" in body["error"]
    # ...and the bridge still works afterwards.
    assert http_get(bridge, "/v1/files")[0] == 200


def test_a_busy_gui_thread_gives_busy_and_the_call_never_runs_late(bridge, monkeypatch):
    ran = []
    monkeypatch.setitem(bridge_tools.ROUTES, "/v1/project", lambda window, params: ran.append(1) or {"ok": True})
    monkeypatch.setattr(bridge, "UI_CALL_TIMEOUT_SECONDS", 0.2)

    # pump=False: the GUI thread is "busy" and handles nothing while we wait.
    status, body = http_get(bridge, "/v1/project", pump=False)
    assert status == 503 and "busy" in body["error"]

    QApplication.processEvents()  # the GUI thread is free again
    assert ran == []              # the abandoned call was not run after all


def test_the_instance_handshake_names_the_project_and_goes_when_the_bridge_stops(bridge, tmp_path):
    path = tmp_path / "workbench" / "runtime" / "instances" / f"bridge-{os.getpid()}.json"
    handshake = json.loads(path.read_text(encoding="utf-8"))
    assert handshake["port"] == bridge.port and handshake["token"] == bridge.token
    assert handshake["pid"] == os.getpid()
    assert handshake["startedAt"].endswith("+00:00")  # UTC: the MCP server compares it with process start
    assert handshake["projectName"] == ""

    bridge.note_state("Acme PROJ-001", "1.2.3")
    handshake = json.loads(path.read_text(encoding="utf-8"))
    assert (handshake["projectName"], handshake["pluginVersion"]) == ("Acme PROJ-001", "1.2.3")

    legacy = tmp_path / "workbench" / "runtime" / "sidekick-bridge.json"
    assert legacy.exists()  # the Trados plugin still finds Workbench the old way
    bridge.stop()
    assert not path.exists() and not legacy.exists()


def test_a_handshake_left_by_a_crashed_workbench_is_removed_at_the_next_start(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(server_module, "_resolve_user_data_root", lambda: tmp_path)
    finished = subprocess.Popen([sys.executable, "-c", "pass"])
    finished.wait()
    instances = tmp_path / "workbench" / "runtime" / "instances"
    instances.mkdir(parents=True)
    dead = instances / f"bridge-{finished.pid}.json"
    dead.write_text("{}", encoding="utf-8")
    unrelated = instances / "bridge-notanumber.json"
    unrelated.write_text("{}", encoding="utf-8")

    bridge = server_module.SupervertalerBridgeServer(Window())
    try:
        assert bridge.start()
        assert not dead.exists()
        assert unrelated.exists()  # not ours to judge
        assert (instances / f"bridge-{os.getpid()}.json").exists()
    finally:
        bridge.stop()
