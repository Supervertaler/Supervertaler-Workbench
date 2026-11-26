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
from modules.database_manager import DatabaseManager
from modules.main_window.match_prefetch import MatchPrefetchMixin
from modules.main_window.termbases import TermbasesMixin
from modules.main_window.translation_memories import TranslationMemoriesMixin
from modules.segment_model import Comment, Project, Segment
from modules.termbase_manager import TermbaseManager
from modules.tm_metadata_manager import TMMetadataManager


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


def test_the_registry_is_json_and_every_tool_is_routed():
    registry = bridge_tools.registry()
    json.dumps(registry)  # the handlers must not leak into it
    assert len(registry["tools"]) == len(bridge_tools.ROUTES) + len(bridge_tools.POST_ROUTES) == 12
    for tool in registry["tools"]:
        routes = bridge_tools.ROUTES if tool["method"] == "GET" else bridge_tools.POST_ROUTES
        assert tool["path"] in routes
    # Only moving the cursor acts on the window; nothing changes data. "write"
    # makes the MCP server refuse it when two Workbenches are running and the
    # call does not say which.
    assert {t["name"] for t in registry["tools"] if t["access"] != "read"} == {"go_to_segment"}


def test_a_large_project_is_answered_quickly():
    # Tested at 50,000 segments; a real project is a few thousand.
    window = make_window(make_project(50_000), active_id=49_999)
    started = time.perf_counter()
    bridge_tools.get_active_project(window, {})
    bridge_tools.get_segments(window, {"contains": "sentence 4999", "limit": "500"})
    bridge_tools.get_active_segment(window, {})
    assert time.perf_counter() - started < 2.0


# ── Go to segment ──────────────────────────────────────────────────────────


def navigating_window(count=10, lands=True):
    window = make_window(make_project(count), active_id=1)
    state = {"active": 1}
    window._get_current_segment_id = lambda: state["active"]
    window._navigate_to_segment_by_id = lambda segment_id: state.update(active=segment_id) if lands else None
    return window, state


def test_go_to_segment_moves_the_cursor():
    window, state = navigating_window()
    assert bridge_tools.go_to_segment(window, {"id": "7"}) == {"ok": True, "activeSegmentId": 7}
    assert state["active"] == 7


def test_go_to_a_segment_that_does_not_exist_or_cannot_be_reached_says_so():
    window, state = navigating_window()
    with pytest.raises(bridge_tools.BridgeToolError, match="no segment 99"):
        bridge_tools.go_to_segment(window, {"id": "99"})
    assert state["active"] == 1

    # Hidden by a grid filter: Workbench does not move. Reporting success
    # would have the assistant talk about a segment the user is not looking at.
    window, state = navigating_window(lands=False)
    with pytest.raises(bridge_tools.BridgeToolError, match="could not move"):
        bridge_tools.go_to_segment(window, {"id": "7"})


# ── Terms and TMs, against a real (temporary) database ─────────────────────

PROJECT_ID = 42


class DataWindow(TermbasesMixin, MatchPrefetchMixin, TranslationMemoriesMixin):
    """Only what the term index and the TM tools touch."""

    def __init__(self, db):
        self.db_manager = db
        self.tm_metadata_mgr = TMMetadataManager(db, lambda *a, **k: None)
        self.current_project = SimpleNamespace(id=PROJECT_ID, source_lang="Dutch", target_lang="English",
                                               segments=[], tm_settings={})
        self.termbase_index, self.termbase_index_lock = [], threading.Lock()
        self.termbase_index_generation = 0
        self._termbase_index_built = False
        self.termbase_cache, self.termbase_cache_lock = {}, threading.Lock()
        self.translation_matches_cache, self.translation_matches_cache_lock = {}, threading.Lock()

    def log(self, message):
        pass

    def _convert_language_to_code(self, name):
        return {"Dutch": "nl", "English": "en"}.get(name, (name or "").lower())


@pytest.fixture
def db(tmp_path):
    d = DatabaseManager(db_path=str(tmp_path / "t.db"), log_callback=lambda *a, **k: None)
    d.connect()
    return d


@pytest.fixture
def terms(db):
    manager = TermbaseManager(db, log_callback=lambda *a, **k: None)
    termbase = manager.create_termbase("Patents", "nl", "en", project_id=None, is_global=True)
    manager.add_term(termbase, "stand der techniek", "prior art", notes="Fixed phrase")
    manager.add_term(termbase, "klep", "valve")
    manager.add_term(termbase, "afsluiter", "tap", forbidden=True)
    return manager, termbase


def test_lookup_term_finds_what_termlens_finds(db, terms):
    manager, termbase = terms
    window = DataWindow(db)

    nothing = bridge_tools.lookup_term(window, {"text": "De klep volgens de stand der techniek."})
    assert nothing["count"] == 0 and "No termbase" in nothing["note"]  # not ticked for the project yet

    manager.activate_termbase(termbase, PROJECT_ID)
    window._build_termbase_index()
    found = bridge_tools.lookup_term(window, {"text": "De klep volgens de stand der techniek, geen afsluiter."})
    assert [(m["source"], m["target"]) for m in found["matches"]] == [
        ("stand der techniek", "prior art"), ("afsluiter", "tap"), ("klep", "valve")]  # longest first
    assert found["matches"][0]["notes"] == "Fixed phrase" and found["matches"][0]["termbase"] == "Patents"
    assert found["matches"][1]["forbidden"] is True
    assert "note" not in found

    # Whole words only, as in TermLens: "kleppen" is not "klep".
    assert bridge_tools.lookup_term(window, {"text": "Twee kleppen."})["count"] == 0
    with pytest.raises(bridge_tools.BridgeToolError, match="'text' is required"):
        bridge_tools.lookup_term(window, {"text": "  "})


def test_lookup_term_is_quick_with_fifty_thousand_terms(db, terms):
    # Tested at 50,000 index entries (a real termbase: 40,000+). The index is
    # the one TermLens scans on every move between segments.
    manager, termbase = terms
    manager.activate_termbase(termbase, PROJECT_ID)
    window = DataWindow(db)
    window._build_termbase_index()
    window.termbase_index = window.termbase_index * (50_000 // len(window.termbase_index))
    assert len(window.termbase_index) >= 49_998

    started = time.perf_counter()
    found = bridge_tools.lookup_term(window, {"text": "De klep volgens de stand der techniek. " * 5})
    assert found["count"] == 2
    assert time.perf_counter() - started < 2.0


@pytest.fixture
def tms(db):
    manager = TMMetadataManager(db, lambda *a, **k: None)
    active = manager.create_tm("Acme TM", "acme_tm", "nl", "en")
    manager.create_tm("Other client", "other_tm", "nl", "en")
    db.add_translation_units_batch([("De klep is open.", "The valve is open."),
                                    ("De pomp draait.", "The pump is running.")], "nl", "en", "acme_tm")
    db.add_translation_units_batch([("Sluit de klep.", "Close the valve.")], "nl", "en", "other_tm")
    manager.activate_tm(active, PROJECT_ID)
    return manager


def run_search(window, params):
    """As call_tool does it: the GUI part here, the search on another thread."""
    prepared = bridge_tools.search_tm(window, params)
    assert isinstance(prepared, bridge_tools.RunOffGuiThread)
    out = {}
    thread = threading.Thread(target=lambda: out.update(result=prepared.function()))
    thread.start()
    thread.join()
    return out["result"]


def test_list_tms_says_which_are_active_for_the_project(db, tms):
    listed = bridge_tools.list_tms(DataWindow(db), {})
    assert [(t["name"], t["activeForProject"]) for t in listed["tms"]] == [
        ("Acme TM", True), ("Other client", False)]


def test_search_tm_searches_the_active_tms_off_the_gui_thread(db, tms):
    window = DataWindow(db)
    found = run_search(window, {"text": "klep"})
    assert [(m["source"], m["target"], m["tm"]) for m in found["matches"]] == [
        ("De klep is open.", "The valve is open.", "Acme TM")]
    assert found["searched"] == "the TMs active for this project"

    # The target side is searched too, and allTms widens it.
    everywhere = run_search(window, {"text": "valve", "allTms": "true"})
    assert sorted(m["tm"] for m in everywhere["matches"]) == ["Acme TM", "Other client"]
    assert run_search(window, {"text": "valve", "allTms": "true", "limit": "1"})["truncated"] is True
    assert run_search(window, {"text": "nietbestaand"})["count"] == 0


def test_search_tm_with_no_active_tm_explains_instead_of_searching_everything(db, tms):
    window = DataWindow(db)
    window.current_project.id = 999  # a project with nothing ticked
    result = bridge_tools.search_tm(window, {"text": "klep"})
    assert result["count"] == 0 and "allTms=true" in result["note"]
    with pytest.raises(bridge_tools.BridgeToolError, match="'text' is required"):
        bridge_tools.search_tm(window, {})


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


def http_post(bridge, path, body, token=True):
    outcome = {}

    def request():
        req = urllib.request.Request(f"http://127.0.0.1:{bridge.port}{path}", data=json.dumps(body).encode("utf-8"),
                                     headers={"Content-Type": "application/json"})
        if token:
            req.add_header("Authorization", f"Bearer {bridge.token}")
        try:
            with urllib.request.urlopen(req, timeout=20) as response:
                outcome["result"] = (response.status, json.loads(response.read()))
        except urllib.error.HTTPError as error:
            outcome["result"] = (error.code, json.loads(error.read()))

    thread = threading.Thread(target=request)
    thread.start()
    while thread.is_alive():
        QApplication.processEvents()
        time.sleep(0.005)
    return outcome["result"]


def test_go_to_segment_over_http_needs_the_token_and_takes_a_json_number(bridge):
    window = bridge.parent()
    moved = []
    window._navigate_to_segment_by_id = moved.append
    window._get_current_segment_id = lambda: moved[-1] if moved else 3

    assert http_post(bridge, "/v1/go-to-segment", {"id": 7}, token=False)[0] == 401
    assert moved == []
    assert http_post(bridge, "/v1/go-to-segment", {"id": 7}) == (200, {"ok": True, "activeSegmentId": 7})
    assert http_post(bridge, "/v1/go-to-segment", [7])[0] == 400
    assert http_post(bridge, "/v1/nonsense", {"id": 7})[0] == 404


def test_a_search_handed_off_the_gui_thread_runs_on_the_bridge_thread(bridge, monkeypatch):
    seen = {}

    def tool(window, params):
        seen["prepare"] = threading.current_thread()

        def search():
            seen["search"] = threading.current_thread()
            return {"ok": True}
        return bridge_tools.RunOffGuiThread(search)

    monkeypatch.setitem(bridge_tools.ROUTES, "/v1/tm-search", tool)
    assert http_get(bridge, "/v1/tm-search?text=x") == (200, {"ok": True})
    assert seen["prepare"] is threading.main_thread()
    assert seen["search"] is not threading.main_thread()


def test_a_search_that_fails_off_the_gui_thread_is_an_error_not_a_hang(bridge, monkeypatch):
    def search():
        raise RuntimeError("database is locked")

    monkeypatch.setitem(bridge_tools.ROUTES, "/v1/tm-search", lambda w, p: bridge_tools.RunOffGuiThread(search))
    status, body = http_get(bridge, "/v1/tm-search?text=x")
    assert status == 500 and "database is locked" in body["error"]


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
    started = handshake["startedAt"]
    handshake = json.loads(path.read_text(encoding="utf-8"))
    assert (handshake["projectName"], handshake["pluginVersion"]) == ("Acme PROJ-001", "1.2.3")
    assert handshake["startedAt"] == started  # opening a project does not make this Workbench "newer"

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
