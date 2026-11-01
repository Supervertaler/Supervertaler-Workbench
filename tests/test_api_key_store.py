"""API keys locked to the computer, step 1 (Plugin Core, docs/design/api-key-protection.md §8).

Every test runs against a scratch APPDATA, home folder, data root and
config.json, checked before the test starts; made-up keys only, and nothing
is sent to a provider. The user's own key files are never opened here:
tools/api_key_lock_test.ps1, which also runs the cross-language checks against
Plugin Core, compares them by name, size and times around the whole run.

The Mac and Linux logic (one Keychain item, saved through a second one) runs
on any computer, against an in-memory keyring backend.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import random
import subprocess
import sys
import threading
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

import pytest
from keyring.backend import KeyringBackend
from keyring.errors import KeyringError, PasswordDeleteError

from modules import api_key_store as store
from modules import llm_clients

WINDOWS = sys.platform == "win32"
windows_only = pytest.mark.skipif(not WINDOWS, reason="DPAPI and the locked file are Windows'")

K1 = "sk-ant-test-K1-PROJ-001"
K2 = "sk-ant-test-K2-PROJ-001"
OAI_OWN = "sk-test-own-Acme-PROJ-001"
OAI_SHARED = "sk-test-shared-Acme-PROJ-001"
DL = "deepl-test-Acme-PROJ-001:fx"
GT = "gt-test-Acme-PROJ-001"
GEM = "AIza-test-Acme-PROJ-001"
UNI = "sk-test-Acmé-Über-PROJ-001"
PLACE = "Settings → AI Settings"

ALL_KEYS = [K1, K2, OAI_OWN, OAI_SHARED, DL, GT, GEM, UNI]


# ─── Scratch folders, checked before anything runs ───────────────────────────

class Scratch:
    pass


@pytest.fixture(autouse=True)
def scratch(tmp_path, monkeypatch):
    s = Scratch()
    s.root = tmp_path
    appdata, home, data = tmp_path / "appdata", tmp_path / "home", tmp_path / "data"
    for d in (appdata, home, data / "settings", data / "workbench" / "settings"):
        d.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("APPDATA", str(appdata))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(appdata))
    os.makedirs(store.config_dir(), exist_ok=True)
    with open(os.path.join(store.config_dir(), "config.json"), "w", encoding="utf-8") as f:
        json.dump({"user_data_path": str(data)}, f)
    s.plain = store.plain_file_path()
    s.locked = store.locked_file_path()
    s.settings = str(data / "workbench" / "settings" / "settings.json")
    for p in [store.config_dir(), store.data_root(), s.plain, s.locked or "", *llm_clients._own_settings_paths()[:4]]:
        if p and not os.path.abspath(p).startswith(str(tmp_path)):
            pytest.exit(f"ABORT: {p} is not in the scratch folder; nothing was run", returncode=2)
    return s


# ─── Helpers ─────────────────────────────────────────────────────────────────

def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(text.encode("utf-8") if isinstance(text, str) else text)


def read_bytes(path):
    with open(path, "rb") as f:
        return f.read()


def locked_map(path):
    return {k: v for k, v in json.loads(read_bytes(path).decode("utf-8")).items() if not k.startswith("_")}


def plain_map(path):
    return store._parse_plain(read_bytes(path).decode("utf-8")) if os.path.exists(path) else store._Ids()


def write_plain(s, **keys):
    ids = store._Ids()
    for k, v in keys.items():
        ids.set(k, v)
    write(s.plain, store._render_plain(ids))


def write_locked(s, entries):
    write(s.locked, json.dumps(entries))


def write_settings(s, api_keys=None, profiles=None):
    data = {"api_keys": api_keys or {}, "general": {}, "ui": {}, "features": {}}
    if profiles is not None:
        data["ui"]["llm_settings"] = {"custom_openai_profiles": profiles}
    write(s.settings, json.dumps(data))


if WINDOWS:
    import ctypes

    def _raw(fn, data, entropy=None):
        blob = store._BLOB
        buf = ctypes.create_string_buffer(data, len(data))
        din = blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_ubyte)))
        ent = None
        if entropy:
            ebuf = ctypes.create_string_buffer(entropy, len(entropy))
            ent = ctypes.byref(blob(len(entropy), ctypes.cast(ebuf, ctypes.POINTER(ctypes.c_ubyte))))
        out = blob()
        assert fn(ctypes.byref(din), None, ent, None, None, 1, ctypes.byref(out))
        try:
            return ctypes.string_at(out.pbData, out.cbData)
        finally:
            store._LocalFree(out.pbData)

    def lock_with_entropy(key):
        """A value Windows locked with an entropy value, so the products' call
        without one is refused: an unreadable entry, made for real."""
        return base64.b64encode(_raw(store._CryptProtectData, key.encode("utf-8"), b"PROJ-001")).decode()

    def raw_lock(key):
        return base64.b64encode(_raw(store._CryptProtectData, key.encode("utf-8"))).decode()

    def raw_unlock(value):
        return _raw(store._CryptUnprotectData, base64.b64decode(value))


def no_key_text_in(s, extra_text=""):
    """No made-up key appears, in UTF-8 or UTF-16, in any scratch file other than
    the plain copies (the plain file and Workbench's settings) or in extra_text."""
    plain_copies = {os.path.normcase(s.plain), os.path.normcase(s.settings)}
    for folder, _, files in os.walk(s.root):
        for name in files:
            path = os.path.join(folder, name)
            if os.path.normcase(path) in plain_copies or name.startswith("api-keys.json"):
                continue
            raw = read_bytes(path)
            for key in ALL_KEYS:
                assert key.encode("utf-8") not in raw and key.encode("utf-16-le") not in raw, f"{key!r} in {path}"
    for key in ALL_KEYS:
        assert key not in extra_text


# ─── The locked file (Windows) ───────────────────────────────────────────────

@windows_only
def test_round_trip_and_the_key_appears_nowhere_in_the_locked_file(scratch):
    assert store.set_key("anthropic", K1)                      # alias → claude
    assert store.set_key("openai", UNI)
    assert store.get("claude") == K1 and store.get("openai") == UNI
    os.remove(scratch.plain)                                   # now only the locked file has them
    assert store.get("claude") == K1 and store.get("openai") == UNI
    assert set(locked_map(scratch.locked)) == {"claude", "openai"}
    no_key_text_in(scratch)
    assert read_bytes(scratch.locked).startswith(b'{\r\n  "_comment": "Supervertaler API keys, locked by Windows')


@windows_only
def test_contract_matches_the_raw_windows_call(scratch):
    """The key, as UTF-8 with no byte-order mark and no terminating zero; no
    entropy; standard base64 - each way round against the raw Windows call."""
    assert store.set_key("claude", UNI)
    value = locked_map(scratch.locked)["claude"]
    assert base64.b64encode(base64.b64decode(value)).decode() == value
    assert raw_unlock(value) == UNI.encode("utf-8")
    write_locked(scratch, {"openai": raw_lock(K2)})
    assert store.get("openai") == K2


@windows_only
def test_an_unreadable_entry_is_reported_kept_and_only_replaced_by_a_new_key(scratch):
    write_locked(scratch, {"claude": lock_with_entropy(K1)})
    found = store.lookup("claude")
    assert found.status == store.UNREADABLE and store.get("claude") is None
    assert store.unreadable_note(found, "claude") == (
        "Your Anthropic key can't be read on this computer – it was saved under another Windows sign-in, "
        "or your Windows password was reset. Paste the key in again; nothing else is affected.")
    assert store.missing_key_message("claude", PLACE) == (
        "Can't use your Anthropic key – it can't be read on this computer (saved under another Windows "
        "sign-in, or your Windows password was reset). Paste it in again in Settings → AI Settings.")
    before = locked_map(scratch.locked)["claude"]
    assert store.set_key("claude", "")                          # an empty box saved: nothing to remove
    assert locked_map(scratch.locked)["claude"] == before
    assert store.set_key("claude", K2)                          # a new key replaces it
    assert store.get("claude") == K2 and store.status("claude") == store.PRESENT


@windows_only
@pytest.mark.parametrize("bad", ["flipped", "not base64"])
def test_tampered_or_garbled_entries_are_unreadable(scratch, bad):
    value = raw_lock(K1)
    if bad == "flipped":
        raw = bytearray(base64.b64decode(value))
        raw[len(raw) // 2] ^= 0xFF
        value = base64.b64encode(bytes(raw)).decode()
    else:
        value = "not base64 at all!"
    write_locked(scratch, {"claude": value})
    assert store.status("claude") == store.UNREADABLE and store.get("claude") is None


def test_a_key_stored_nowhere_says_no_key_is_set(scratch):
    assert store.status("mistral") == store.ABSENT
    assert store.missing_key_message("mistral", PLACE) == "No API key set for Mistral. Add one in Settings → AI Settings."
    assert llm_clients.api_key_problem("deepl") == "No API key set for DeepL. Add one in Settings → MT Settings."
    assert llm_clients.api_key_problem("ollama_endpoint") is None


@windows_only
def test_a_damaged_locked_file_is_unreadable_set_aside_byte_for_byte_and_reported(scratch):
    assert store.set_key("claude", K1)
    damaged = read_bytes(scratch.locked)[:-20]                 # half-written
    write(scratch.locked, damaged)
    os.remove(scratch.plain)
    found = store.lookup("claude")
    assert found.status == store.UNREADABLE
    assert store.missing_key_message("claude", PLACE) == (
        f"Can't use your Anthropic key – the file holding your locked API keys is damaged: {scratch.locked}. "
        "Paste the key in again in Settings → AI Settings.")
    write_plain(scratch, claude=K2)
    sync = store.sync_locked()
    asides = [n for n in os.listdir(os.path.dirname(scratch.locked)) if n.startswith("api-keys.locked.damaged-")]
    assert len(asides) == 1
    aside = os.path.join(os.path.dirname(scratch.locked), asides[0])
    assert read_bytes(aside) == damaged
    assert sync.notice == (f"The file holding your locked API keys was damaged and has been set aside as {aside}. "
                           "Please paste your keys in again.")
    os.remove(scratch.plain)
    assert store.get("claude") == K2


# The mixed-version and crash test: every plain file against every locked file.
PLAIN_STATES = ["absent", "K1", "K2", "damaged", "empty"]
LOCKED_STATES = ["absent", "K1", "K2", "unreadable", "damaged"]


@windows_only
@pytest.mark.parametrize("plain_state", PLAIN_STATES)
@pytest.mark.parametrize("locked_state", LOCKED_STATES)
def test_state_table_step_1(scratch, plain_state, locked_state):
    keys = {"K1": K1, "K2": K2}
    if plain_state in keys:
        write_plain(scratch, claude=keys[plain_state])
    elif plain_state == "damaged":
        write(scratch.plain, '{\r\n  "claude": ')
    elif plain_state == "empty":
        write(scratch.plain, b"")
    if locked_state in keys:
        write_locked(scratch, {"claude": raw_lock(keys[locked_state])})
    elif locked_state == "unreadable":
        write_locked(scratch, {"claude": lock_with_entropy(K1)})
    elif locked_state == "damaged":
        write(scratch.locked, '{"claude": "AQAA')

    # Reads: the plain file whenever it has the key, else the locked file.
    found = store.lookup("claude")
    if plain_state in keys:
        assert (found.status, found.key) == (store.PRESENT, keys[plain_state])
    elif locked_state in keys:
        assert (found.status, found.key) == (store.PRESENT, keys[locked_state])
    elif locked_state in ("unreadable", "damaged"):
        assert found.status == store.UNREADABLE
    else:
        assert found.status == store.ABSENT

    # The sync: the plain key replaces whatever the locked file holds; without
    # one, the locked file is left exactly as it was.
    before = read_bytes(scratch.locked) if os.path.exists(scratch.locked) else None
    sync = store.sync_locked()
    if plain_state in keys:
        os.remove(scratch.plain)
        assert store.get("claude") == keys[plain_state]
        assert sync.locked == (0 if locked_state == plain_state else 1)
        assert (sync.notice is not None) == (locked_state == "damaged")
    else:
        after = read_bytes(scratch.locked) if os.path.exists(scratch.locked) else None
        assert after == before and sync.locked == 0 and not sync.failed


@windows_only
def test_the_sync_never_removes(scratch):
    assert store.set_key("claude", K1) and store.set_key("openai", OAI_SHARED)
    before = read_bytes(scratch.locked)
    for plain in ("", "{ damaged", '{"_comment": "nothing"}'):
        write(scratch.plain, plain)
        store.sync_locked()
        assert read_bytes(scratch.locked) == before


@windows_only
def test_an_abandoned_temporary_file_is_swept_and_the_file_is_intact(scratch):
    assert store.set_key("claude", K1)
    folder = os.path.dirname(scratch.locked)
    old = os.path.join(folder, "api-keys.locked.json.0123456789abcdef0123456789abcdef.tmp")
    fresh = os.path.join(folder, "api-keys.locked.json.fedcba9876543210fedcba9876543210.tmp")
    write(old, b"half a fi")
    write(fresh, b"a write in progress")
    os.utime(old, (time.time() - 600, time.time() - 600))
    assert store.set_key("openai", OAI_SHARED)
    assert not os.path.exists(old) and os.path.exists(fresh)
    os.remove(scratch.plain)
    assert store.get("claude") == K1 and store.get("openai") == OAI_SHARED


@windows_only
def test_entries_workbench_does_not_know_survive_its_rewrite(scratch):
    other = raw_lock(DL)
    write_locked(scratch, {"_comment": "x", "deepl": other, "trados:custom_openai:Acme & <PROJ-001>'s server": other,
                           "Some_Product:Key": other})
    assert store.set_key("claude", K1)
    after = locked_map(scratch.locked)
    assert after["deepl"] == other and after["Some_Product:Key"] == other
    assert after["trados:custom_openai:Acme & <PROJ-001>'s server"] == other
    assert b"Acme \\u0026 \\u003cPROJ-001\\u003e\\u0027s server" in read_bytes(scratch.locked)   # escaped as core escapes


@windows_only
def test_a_locked_file_held_by_another_product(scratch):
    assert store.set_key("claude", K1)
    os.remove(scratch.plain)
    fd = store._open_fd(scratch.locked, store._GENERIC_READ, 0, store._OPEN_EXISTING)   # no sharing at all
    try:
        started = time.perf_counter()
        found = store.lookup("claude")
        waited = time.perf_counter() - started
        assert found.status == store.UNREADABLE and 0.8 < waited < 3
        assert store.unreadable_note(found, "claude") == (
            f"Your Anthropic key can't be read just now – the file holding your locked API keys could not be opened: "
            f"{scratch.locked}.")
        assert store.set_key("openai", OAI_SHARED)              # saved: the plain file decides in step 1
        assert not store.set_key("claude", "")                   # a removal needs both files
    finally:
        os.close(fd)
    store.sync_locked()                                          # the next start catches up
    os.remove(scratch.plain)
    assert store.get("openai") == OAI_SHARED


@windows_only
def test_a_writer_waits_for_a_hold_that_ends(scratch):
    assert store.set_key("claude", K1)
    fd = store._open_fd(scratch.locked, store._GENERIC_READ, 0, store._OPEN_EXISTING)
    threading.Timer(0.3, os.close, [fd]).start()
    assert store.set_key("openai", OAI_SHARED)
    os.remove(scratch.plain)
    assert store.get("openai") == OAI_SHARED and store.get("claude") == K1


CHILD = r"""
import os, sys, time
sys.path.insert(0, sys.argv[1])
os.environ["APPDATA"] = sys.argv[2]; os.environ["USERPROFILE"] = sys.argv[3]
from modules import api_key_store as s
assert s.locked_file_path().startswith(sys.argv[2])
mode, tag = sys.argv[4], sys.argv[5]
if mode == "many":
    while time.time() < float(sys.argv[6]):
        time.sleep(0.001)
    for i in range(20):
        assert s.set_key(f"{tag}-{i}", f"value-{tag}-{i}-PROJ-001")
else:
    i = 0
    while True:
        s.set_key(f"{tag}-{i % 5}", f"value-{tag}-{i}-PROJ-001")
        i += 1
"""


def _child(scratch, *args):
    script = os.path.join(scratch.root, "child.py")
    if not os.path.exists(script):
        write(script, CHILD)
    return subprocess.Popen([sys.executable, script, REPO, os.environ["APPDATA"], os.environ["USERPROFILE"], *args])


@windows_only
def test_two_writers_at_once_lose_nothing(scratch):
    start = str(time.time() + 1.5)
    children = [_child(scratch, "many", tag, start) for tag in ("a", "b")]
    assert all(c.wait(60) == 0 for c in children)
    plain = plain_map(scratch.plain)
    locked = locked_map(scratch.locked)
    for tag in ("a", "b"):
        for i in range(20):
            assert plain.get(f"{tag}-{i}") == f"value-{tag}-{i}-PROJ-001"
            assert raw_unlock(locked[f"{tag}-{i}"]).decode() == f"value-{tag}-{i}-PROJ-001"


@windows_only
def test_killed_in_the_middle_of_saving_leaves_both_files_whole(scratch):
    for n in range(10):
        child = _child(scratch, "loop", f"kill{n}")
        time.sleep(0.4 + random.random() * 0.4)
        child.kill()
        child.wait(10)
        assert os.path.exists(scratch.locked)
        assert store._parse_locked(read_bytes(scratch.locked).decode("utf-8")) is not None
        assert len(plain_map(scratch.plain)) > 0
        assert all(store.status(f"kill{n}-{i}") != store.UNREADABLE for i in range(5))


# ─── Workbench joins the shared keys (decision 1, M8) ────────────────────────

@windows_only
def test_first_start_adds_own_keys_where_the_shared_store_has_none_and_m8_names_the_differing_one(scratch):
    write_plain(scratch, openai=OAI_SHARED)
    write_settings(scratch, {"openai": OAI_OWN, "deepl": DL, "google_translate": GT, "google": GEM,
                             "ollama_endpoint": "http://localhost:11434", "microsoft_translate_region": "westeurope"})
    prepared = llm_clients.prepare_api_keys(scratch.settings, join=True)
    assert prepared.joined
    assert prepared.notices == [
        "Workbench now uses the same API keys as Supervertaler for Trados, memoQ and Sidekick. "
        "Your Workbench key for OpenAI was different; the shared one is used from now on."]
    plain = plain_map(scratch.plain)
    # Never overwrites; Workbench's ids map to the shared ones; its old "google"
    # (Gemini's key) goes to gemini, never to the shared google (Google Translate).
    assert plain.get("openai") == OAI_SHARED and plain.get("deepl") == DL
    assert plain.get("google") == GT and plain.get("gemini") == GEM
    assert plain.get("google_translate") is None and plain.get("ollama_endpoint") is None
    keys = llm_clients.load_api_keys(scratch.settings)
    assert keys["openai"] == OAI_SHARED and keys["google_translate"] == GT and keys["gemini"] == GEM
    assert "google" not in keys and keys["microsoft_translate_region"] == "westeurope"
    os.remove(scratch.plain)                                    # all of it was locked by the sync
    assert llm_clients.load_api_keys(scratch.settings)["deepl"] == DL
    # A second start does not join again, so M8 is said once.
    write_plain(scratch, openai=OAI_SHARED)
    assert llm_clients.prepare_api_keys(scratch.settings, join=False).notices == []


@windows_only
def test_m8_names_several_providers_and_counts_a_locked_only_shared_key(scratch):
    write_locked(scratch, {"openai": raw_lock(OAI_SHARED), "deepl": raw_lock("deepl-shared-PROJ-001")})
    write_settings(scratch, {"openai": OAI_OWN, "deepl": DL, "claude": K1})
    prepared = llm_clients.prepare_api_keys(scratch.settings, join=True)
    assert prepared.notices == [
        "Workbench now uses the same API keys as Supervertaler for Trados, memoQ and Sidekick. "
        "Your Workbench keys for OpenAI and DeepL were different; the shared ones are used from now on."]
    plain = plain_map(scratch.plain)
    assert plain.get("openai") is None and plain.get("claude") == K1


@windows_only
def test_nothing_joins_past_a_locked_file_this_account_cannot_read(scratch):
    write(scratch.locked, "{ damaged")
    write_settings(scratch, {"openai": OAI_OWN})
    prepared = llm_clients.prepare_api_keys(scratch.settings, join=True)
    assert not prepared.joined and plain_map(scratch.plain).get("openai") is None


@windows_only
def test_an_unreadable_shared_key_ends_the_search(scratch):
    """No older own copy, no blank key: the action is told why (M2)."""
    write_locked(scratch, {"openai": lock_with_entropy(OAI_SHARED)})
    write_settings(scratch, {"openai": OAI_OWN})
    assert "openai" not in llm_clients.load_api_keys(scratch.settings)
    m2 = ("Can't use your OpenAI key – it can't be read on this computer (saved under another Windows sign-in, "
          "or your Windows password was reset). Paste it in again in Settings → AI Settings.")
    assert llm_clients.api_key_problem("openai", settings_path=scratch.settings) == m2
    assert llm_clients.unreadable_key_notes()["openai"].startswith("Your OpenAI key can't be read on this computer")
    write_settings(scratch, {})
    with pytest.raises(ValueError) as e:
        llm_clients.LLMClient(api_key="", provider="openai")
    assert str(e.value) == m2


@windows_only
def test_mixed_versions(scratch):
    # Step-0 Workbench saved a key in its own settings only: step 1 reads it.
    write_settings(scratch, {"mistral": "mistral-test-PROJ-001"})
    assert llm_clients.load_api_keys(scratch.settings)["mistral"] == "mistral-test-PROJ-001"
    # Step 1 saves: the plain file too, so a step-0 Trados, memoQ or Sidekick sees it.
    assert llm_clients.save_api_key("google_translate", GT)
    assert plain_map(scratch.plain).get("google") == GT
    # Step 2 has imported (plain file gone): step 1 reads the locked copy.
    os.remove(scratch.plain)
    assert llm_clients.load_api_keys(scratch.settings)["google_translate"] == GT
    # Step 1 saves after the import: it recreates the plain file with that key.
    assert llm_clients.save_api_key("deepl", DL)
    assert dict(plain_map(scratch.plain).items()) == {"deepl": DL}
    keys = llm_clients.load_api_keys(scratch.settings)
    assert keys["deepl"] == DL and keys["google_translate"] == GT
    # Another product saves in the plain file only (a crash before its locked
    # write): reads take the plain file, the next sync locks it.
    write_plain(scratch, deepl="deepl-newer-PROJ-001")
    assert llm_clients.load_api_keys(scratch.settings)["deepl"] == "deepl-newer-PROJ-001"
    store.sync_locked()
    os.remove(scratch.plain)
    assert llm_clients.load_api_keys(scratch.settings)["deepl"] == "deepl-newer-PROJ-001"
    # Removing in Workbench removes from both shared files.
    assert llm_clients.save_api_key("deepl", "")
    assert "deepl" not in locked_map(scratch.locked) and store.status("deepl") == store.ABSENT


@windows_only
def test_profile_keys_are_locked_under_their_own_ids_and_go_with_their_profile(scratch):
    profiles = [{"name": 'Acme "server"\\1', "api_key": K1}, {"name": "PROJ-001 proxy", "api_key": K2},
                {"name": "No key", "api_key": ""}]
    assert llm_clients.save_profile_keys(profiles)
    locked = locked_map(scratch.locked)
    assert set(locked) == {"workbench:custom_openai:Acme server1", "workbench:custom_openai:PROJ-001 proxy"}
    assert not os.path.exists(scratch.plain)                     # private ids are never in the shared plain file
    assert store.get("workbench:custom_openai:Acme server1") == K1
    write_locked(scratch, {**locked_map(scratch.locked), "workbench:custom_openai:Unreadable": lock_with_entropy(K1)})
    assert llm_clients.save_profile_keys(profiles[1:])          # the first profile removed
    assert set(locked_map(scratch.locked)) == {"workbench:custom_openai:PROJ-001 proxy",
                                               "workbench:custom_openai:Unreadable"}   # kept: can't be read here
    # The start-up sync locks profile keys it does not have yet.
    os.remove(scratch.locked)
    write_settings(scratch, {})
    llm_clients.prepare_api_keys(scratch.settings, join=False, profiles=profiles)
    assert store.get("workbench:custom_openai:PROJ-001 proxy") == K2


@windows_only
def test_the_batch_offload_settings_path_reads_through_the_same_function(scratch):
    from modules import batch_offload
    write_settings(scratch, {"claude": K2, "gemini": ""})
    job = {"provider": "claude", "settingsPath": scratch.settings}
    assert batch_offload._resolve_api_key(job) == K2               # own copy, the shared store having none
    assert store.set_key("claude", K1)
    assert batch_offload._resolve_api_key(job) == K1               # the shared key first
    assert batch_offload._resolve_api_key({**job, "apiKey": "sk-job-PROJ-001"}) == "sk-job-PROJ-001"
    write_plain(scratch)
    write_locked(scratch, {"claude": lock_with_entropy(K1)})
    assert batch_offload._resolve_api_key(job) == ""
    with pytest.raises(RuntimeError) as e:
        batch_offload._build_client(job)
    assert str(e.value).startswith("Can't use your Anthropic key – it can't be read on this computer")


@windows_only
def test_no_key_reaches_the_log(scratch, caplog):
    caplog.set_level(logging.DEBUG)
    write_settings(scratch, {"openai": OAI_OWN, "deepl": DL}, profiles=[{"name": "Acme", "api_key": K2}])
    llm_clients.prepare_api_keys(scratch.settings, join=True, profiles=[{"name": "Acme", "api_key": K2}])
    write_locked(scratch, {**locked_map(scratch.locked), "claude": lock_with_entropy(K1)})
    llm_clients.load_api_keys(scratch.settings)
    llm_clients.api_key_problem("claude")
    write(scratch.locked, read_bytes(scratch.locked)[:-30])
    llm_clients.load_api_keys(scratch.settings)
    store.set_key("gemini", GEM)
    store.sync_locked()
    assert caplog.records, "the failure paths above are logged"
    no_key_text_in(scratch, "\n".join(r.getMessage() for r in caplog.records))


@windows_only
def test_keyring_is_not_needed_on_windows(scratch):
    code = ("import os, sys; sys.path.insert(0, sys.argv[1]); os.environ['APPDATA'] = sys.argv[2]; "
            "os.environ['USERPROFILE'] = sys.argv[3]; from modules import api_key_store as s, llm_clients as l; "
            "s.set_key('claude', 'sk-ant-test-PROJ-001'); s.sync_locked(); l.load_api_keys(); "
            "print('keyring' in sys.modules)")
    out = subprocess.run([sys.executable, "-c", code, REPO, os.environ["APPDATA"], os.environ["USERPROFILE"]],
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "False"


@windows_only
def test_timings_at_15_and_1500_keys(scratch):
    """Not a pass/fail on speed beyond a generous bound: the figures go in the hand-off."""
    report = []
    for count in (15, 1500):
        ids = store._Ids()
        for i in range(count):
            ids.set(f"provider{i}", raw_lock(f"value-{i}-PROJ-001"))
        write(scratch.locked, store._render_locked(ids))
        write(scratch.plain, b"")
        started = time.perf_counter()
        for _ in range(20):
            store.lookup("provider7")
        lookup_ms = (time.perf_counter() - started) / 20 * 1000
        plain = store._Ids()
        for i in range(count):
            plain.set(f"provider{i}", f"value-{i}-PROJ-001")
        write(scratch.plain, store._render_plain(plain))
        started = time.perf_counter()
        store.sync_locked()
        sync_ms = (time.perf_counter() - started) * 1000
        started = time.perf_counter()
        for _ in range(20):
            llm_clients.load_api_keys(scratch.settings)
        load_ms = (time.perf_counter() - started) / 20 * 1000
        report.append(f"{count} keys: one lookup from the locked file {lookup_ms:.2f} ms, "
                      f"start-up sync with nothing to change {sync_ms:.0f} ms, load_api_keys {load_ms:.2f} ms")
        assert lookup_ms < (50 if count == 15 else 2000)
    print("\nTIMINGS " + " | ".join(report))


# ─── The Mac and Linux logic, against an in-memory keyring ───────────────────

class MemoryKeyring(KeyringBackend):
    """Saves the way keyring's macOS backend does: the item deleted, then added."""
    priority = 1

    def __init__(self):
        super().__init__()
        self.items = {}
        self.stop_after_deleting = None
        self.fail = False

    def get_password(self, service, username):
        if self.fail:
            raise KeyringError("The keychain could not be opened")
        return self.items.get((service, username))

    def set_password(self, service, username, password):
        self.items.pop((service, username), None)
        if self.stop_after_deleting == username:
            raise SystemExit("killed between delete and add")
        self.items[(service, username)] = password

    def delete_password(self, service, username):
        if (service, username) not in self.items:
            raise PasswordDeleteError(username)
        del self.items[(service, username)]


@pytest.fixture
def keychain(monkeypatch):
    backend = MemoryKeyring()
    monkeypatch.setattr(store, "_store", lambda: store._Keychain(backend))
    return backend


def test_keychain_round_trip_one_item_with_plain_values(scratch, keychain):
    assert store.set_key("claude", K1) and store.set_key("google", GT)
    os.remove(scratch.plain)
    assert store.get("claude") == K1 and store.get("google") == GT
    assert set(keychain.items) == {("Supervertaler", "api-keys")}       # one item; the second one deleted
    assert json.loads(keychain.items[("Supervertaler", "api-keys")]) == {"claude": K1, "google": GT}


def test_keychain_save_stopped_after_the_main_item_is_deleted_still_reads(scratch, keychain):
    assert store.set_key("claude", K1)
    keychain.stop_after_deleting = "api-keys"
    with pytest.raises(SystemExit):
        store.set_key("openai", OAI_SHARED)
    assert ("Supervertaler", "api-keys") not in keychain.items          # the main item is gone...
    keychain.stop_after_deleting = None
    os.remove(scratch.plain)
    assert store.get("claude") == K1 and store.get("openai") == OAI_SHARED   # ...and api-keys.new holds them
    assert store.set_key("gemini", GEM)                                   # the next save puts things right
    assert set(keychain.items) == {("Supervertaler", "api-keys")}
    assert store.get("openai") == OAI_SHARED


def test_keychain_sync_join_and_profiles(scratch, keychain):
    write_plain(scratch, openai=OAI_SHARED)
    write_settings(scratch, {"openai": OAI_OWN, "deepl": DL})
    prepared = llm_clients.prepare_api_keys(scratch.settings, join=True, profiles=[{"name": "Acme", "api_key": K2}])
    assert prepared.joined and "OpenAI" in prepared.notices[0] and prepared.keychain_missing is None
    os.remove(scratch.plain)
    keys = llm_clients.load_api_keys(scratch.settings)
    assert keys["openai"] == OAI_SHARED and keys["deepl"] == DL
    assert store.get("workbench:custom_openai:Acme") == K2


def test_a_damaged_keychain_item_is_unreadable_then_set_aside(scratch, keychain):
    keychain.items[("Supervertaler", "api-keys")] = '{"claude": "sk-ant'
    found = store.lookup("claude")
    assert found.status == store.UNREADABLE
    holder = "Keychain item" if sys.platform == "darwin" else "secret-store item"
    assert store.when_used(found, "claude", PLACE) == (
        f"Can't use your Anthropic key – the {holder} holding your API keys is damaged: Supervertaler / api-keys. "
        "Paste the key in again in Settings → AI Settings.")
    write_plain(scratch, claude=K2)
    sync = store.sync_locked()
    asides = [u for (_, u) in keychain.items if u.startswith("api-keys.damaged-")]
    assert len(asides) == 1 and keychain.items[("Supervertaler", asides[0])] == '{"claude": "sk-ant'
    assert sync.notice.endswith(f"was damaged and has been set aside as {asides[0]}. Please paste your keys in again.")
    os.remove(scratch.plain)
    assert store.get("claude") == K2


def test_a_keychain_that_cannot_be_opened_is_unreadable_not_absent(scratch, keychain):
    keychain.fail = True
    found = store.lookup("claude")
    assert found.status == store.UNREADABLE
    assert store.when_used(found, "claude", PLACE).endswith(
        "could not be opened: Supervertaler / api-keys. Try again in a moment.")
    assert store.set_key("claude", K1)               # saved in the plain file; the next start locks it
    assert store.get("claude") == K1


@pytest.mark.parametrize("platform, module, notice", [
    ("darwin", "keyring.backends.macOS",
     "Your Mac's Keychain isn't available to Supervertaler, so your keys stay in Supervertaler's settings file as before."),
    ("linux", "keyring.backends.SecretService",
     "Your computer's secret store isn't available to Supervertaler, so your keys stay in Supervertaler's settings file as before."),
])
def test_m7_when_a_frozen_build_has_lost_its_keychain_backend(scratch, monkeypatch, platform, module, notice):
    import keyring.backends
    monkeypatch.setattr(sys, "platform", platform)
    monkeypatch.setitem(sys.modules, module, None)           # as if the build had not bundled it
    monkeypatch.delattr(keyring.backends, module.rsplit(".", 1)[1], raising=False)
    assert store._store() is None
    assert store.keychain_missing_notice() == notice
    assert store.set_key("claude", K1)                        # the plain copies, as before
    assert store.get("claude") == K1 and store.sync_locked() == store.Sync()
    assert llm_clients.prepare_api_keys(scratch.settings, join=True).keychain_missing == notice


def test_the_real_mac_backend_is_not_taken_for_one_elsewhere(monkeypatch):
    if sys.platform == "darwin":
        pytest.skip("this is a Mac")
    monkeypatch.setattr(sys, "platform", "darwin")
    assert store._vault_backend() is None                     # keyring's macOS backend refuses off a Mac
