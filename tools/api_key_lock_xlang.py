"""Workbench's side of the cross-language test for the locked API key file.

Not run on its own: tools/api_key_lock_test.ps1 runs one phase at a time, with
Plugin Core (and Sidekick) in between. The first argument is the scratch
APPDATA; it is set here, before Workbench's modules are imported, so the data
root (its config.json) and the locked file are the scratch folder's. Prints
PASS / FAIL / INFO lines, never a key: keys are compared, not printed.

    python tools/api_key_lock_xlang.py <scratch appdata> <phase> [<arg>]
"""

import logging
import os
import sys
import time

# What the store logs (Windows error codes, set-asides - never a key) as INFO
# lines on stdout, where the runner reads them.
logging.basicConfig(stream=sys.stdout, level=logging.INFO, format="INFO  [python log] %(message)s")

APPDATA = sys.argv[1]
PHASE = sys.argv[2]
ARG = sys.argv[3] if len(sys.argv) > 3 else ""
os.environ["APPDATA"] = APPDATA
os.environ["USERPROFILE"] = os.path.join(os.path.dirname(APPDATA), "home")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules import api_key_store as store          # noqa: E402
from modules import llm_clients                      # noqa: E402

# The same made-up keys as the runner's.
K1 = "sk-ant-test-K1-PROJ-001"
UNI = "sk-test-Acmé-Über-PROJ-001"
GT = "gt-test-Acme-PROJ-001"
GEM = "AIza-test-Acme-PROJ-001"
MI = "mistral-test-Acme-PROJ-001"
SK = "deepseek-test-Sidekick-PROJ-001"
KEY_WB = "sk-test-Workbench-PROJ-001"
KEY_ACME = "sk-test-AcmeServer-PROJ-001"
PROFILE = "Acme & <PROJ-001>'s server"
PLACE = "Settings → AI Settings"

failed = 0


def check(ok, name):
    global failed
    print(("PASS  " if ok else "FAIL  ") + "[python] " + name)
    failed += 0 if ok else 1


root = os.path.dirname(APPDATA)
if not (len(root) > 10 and all(os.path.abspath(p).startswith(root + os.sep)
                               for p in (store.plain_file_path(), store.locked_file_path()))):
    print(f"ABORT  [python] key files not in the scratch folder: {store.plain_file_path()} / {store.locked_file_path()}")
    sys.exit(2)

if PHASE == "lock":
    check(store.set_key("anthropic", K1) and llm_clients.save_api_key("openai", UNI)
          and llm_clients.save_api_key("google_translate", GT),
          "Workbench saves three keys, one not plain ASCII, one under its MT name")
    check(llm_clients.save_profile_keys([{"name": PROFILE, "api_key": KEY_WB}]), "and a profile key, under its private id")

elif PHASE == "read":
    keys = llm_clients.load_api_keys(os.path.join(root, "no-settings.json"))
    check(keys.get("gemini") == GEM and keys.get("mistral") == MI, "Workbench reads the keys core locked")
    check(store.get(store.private_id("trados", "custom_openai", PROFILE)) == KEY_ACME, "and core's Trados profile key")
    check(keys.get("claude") == K1 and keys.get("openai") == UNI and keys.get("google_translate") == GT,
          "and its own, after core rewrote the file")
    check(store.get(store.private_id("workbench", "custom_openai", PROFILE)) == KEY_WB,
          "its profile key survived the other products' rewrites")
    if ARG == "sidekick":
        check(keys.get("deepseek") == SK, "and the key Sidekick locked")
    check(llm_clients.save_api_key("openrouter", "sk-or-test-PROJ-001"), "Workbench saves another (rewriting the file)")

elif PHASE == "render":
    with open(store.locked_file_path(), "rb") as f:
        ids = store._parse_locked(store._decode(f.read()))
    with open(ARG, "wb") as f:
        f.write(store._render_locked(ids).encode("utf-8"))

elif PHASE == "messages":
    ids = ["claude", "openai", "gemini", "mistral", "deepseek", "openrouter", "deepl", "google", "microsoft",
           "custom_openai", store.private_id("trados", "custom_openai", PROFILE)]
    found = store.lookup_many(ids)
    with open(ARG, "w", encoding="utf-8", newline="\n") as f:
        for key_id in ids:
            f.write(f"{key_id}\tM1\t{store.unreadable_note(found[key_id], key_id) or ''}\n")
            f.write(f"{key_id}\tM2\t{store.missing_key_message(key_id, PLACE) or ''}\n")

elif PHASE == "notice":
    with open(ARG, "w", encoding="utf-8") as f:
        f.write(store.sync_locked().notice or "")

elif PHASE == "race":
    start = float(open(ARG).read())
    while time.time() < start:
        time.sleep(0.001)
    for i in range(40):
        if not store.set_key(f"python-{i}", f"value-python-{i}-PROJ-001"):
            check(False, f"save {i} during the race")

else:
    print(f"FAIL  [python] unknown phase {PHASE!r}")
    failed += 1

sys.exit(1 if failed else 0)
