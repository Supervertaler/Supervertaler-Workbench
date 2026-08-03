#!/usr/bin/env python3
"""Report keyboard-shortcut overlaps between Supervertaler Workbench and
Supervertaler for Trados, and fail if any of them is a real collision.

WHY THIS EXISTS
---------------
The two products deliberately share a lot of chords: Alt+Down adds a term in
both, Alt+1..9 inserts term N in both, Ctrl+Alt+T opens the term dialogue in
both. That parallel is a feature, and it is safe as long as the Workbench side
is an ordinary in-app QShortcut - whichever window has focus wins, which is
what a user expects.

It stops being safe the moment a Workbench shortcut is marked "global": True.
A global hotkey is registered at OS level and fires whichever application is in
front, Trados included. Then one keypress does two things at once.

Two of those shipped on 2026-08-03:

  * Ctrl+Alt+A - Workbench Voice Always-On vs the Trados "Add term with
    abbreviation" action. Moved to Ctrl+Alt+O on the Workbench side.
  * Ctrl+Alt+V - Workbench voice-command push-to-talk vs the Trados voice
    toggle. Worse than a double-trigger: Workbench's is a HOLD and Trados's was
    a TOGGLE, so letting go stopped only Workbench's and left the Trados
    listener running with nothing visible having started it. Moved to
    Ctrl+Alt+D on the Trados side.

Both were found by a user noticing something odd, not by anyone reading the
code. Hence this script.

USAGE
-----
    python tools/shortcut_overlap.py            # table + verdict
    python tools/shortcut_overlap.py --all      # include non-overlapping chords

Exit status is 1 if any overlapping chord is global on the Workbench side,
which is the condition that actually breaks things.

Both repositories are expected to sit side by side, i.e. ../Supervertaler and
../Supervertaler-for-Trados from a common parent. Override with
--trados PATH if your layout differs.

The parsing is deliberately shallow - a regex over the declaration tables in
each product, not an import or a build. It is a canary, not an oracle: if
either product changes how it declares shortcuts, this reports fewer bindings
rather than wrong ones, so treat a sudden drop in the counts as a bug here.
"""

import argparse
import glob
import os
import re
import sys

MOD_ORDER = {"Control": 0, "Alt": 1, "Shift": 2}


def workbench_bindings(repo):
    """id/description/global for every default binding in shortcut_manager."""
    path = os.path.join(repo, "modules", "shortcut_manager.py")
    with open(path, encoding="utf-8") as fh:
        src = fh.read()

    out = {}
    for m in re.finditer(r'"([a-z0-9_]+)":\s*\{(.*?)\n        \}', src, re.S):
        name, body = m.group(1), m.group(2)
        default = re.search(r'"default":\s*"([^"]*)"', body)
        desc = re.search(r'"description":\s*"([^"]*)"', body)
        if not default or not default.group(1):
            continue          # unbound by design (e.g. needs AutoHotkey)
        out[default.group(1)] = {
            "id": name,
            "desc": desc.group(1) if desc else name,
            "global": '"global": True' in body,
        }
    return out


def _format_keys(expr):
    """'Keys.Control | Keys.Alt | Keys.D' -> 'Ctrl+Alt+D'."""
    parts = [p.strip().replace("Keys.", "") for p in expr.split("|")]
    mods = sorted((p for p in parts if p in MOD_ORDER), key=lambda x: MOD_ORDER[x])
    rest = [p for p in parts if p not in MOD_ORDER]
    names = ["Ctrl" if m == "Control" else m for m in mods]
    for r in rest:
        names.append(r[1:] if re.fullmatch(r"D\d", r) else r)
    return "+".join(names)


def trados_bindings(repo):
    """chord -> action Name for every [Shortcut(...)] in the plugin.

    Matched per attribute block - everything from "[Action(" to the "public
    class" it decorates - rather than by allowing N characters between Name and
    Shortcut. The first version of this used a 400-character window and silently
    lost two actions the moment an explanatory comment was added between the
    attributes, which is precisely the failure a checker must not have: it went
    on reporting "no collisions" while a collision existed. A block has no
    length limit, so comments cannot push anything out of range.
    """
    pattern = os.path.join(repo, "src", "Supervertaler.Trados", "*.cs")
    out = {}
    for path in glob.glob(pattern):
        with open(path, encoding="utf-8-sig", errors="replace") as fh:
            text = fh.read()
        for block in re.finditer(r'\[Action\((.*?)\bpublic\s+(?:sealed\s+)?class\b',
                                 text, re.S):
            body = block.group(1)
            name = re.search(r'Name\s*=\s*"([^"]+)"', body)
            shortcut = re.search(r'\[Shortcut\(([^)]+)\)\]', body)
            if name and shortcut:
                out[_format_keys(shortcut.group(1))] = name.group(1)
    return out


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    wb_repo = os.path.dirname(here)
    default_trados = os.path.join(os.path.dirname(wb_repo), "Supervertaler-for-Trados")

    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--trados", default=default_trados,
                    help="path to the Supervertaler-for-Trados checkout")
    ap.add_argument("--all", action="store_true",
                    help="also list chords used by only one product")
    args = ap.parse_args()

    if not os.path.isdir(args.trados):
        sys.exit(f"Trados repo not found: {args.trados}\nPass --trados PATH.")

    wb = workbench_bindings(wb_repo)
    tr = trados_bindings(args.trados)
    shared = sorted(set(wb) & set(tr))
    clashes = [c for c in shared if wb[c]["global"]]

    print(f"Workbench bindings: {len(wb)}   Trados bindings: {len(tr)}   "
          f"shared chords: {len(shared)}\n")

    if shared:
        print(f"{'Chord':<16} {'WB scope':<9} {'Workbench':<44} Trados")
        print("-" * 122)
        for c in shared:
            scope = "GLOBAL" if wb[c]["global"] else "in-app"
            print(f"{c:<16} {scope:<9} {wb[c]['desc'][:43]:<44} {tr[c][:44]}")
        print()

    if args.all:
        print("Workbench only:", ", ".join(sorted(set(wb) - set(tr))) or "-")
        print("Trados only   :", ", ".join(sorted(set(tr) - set(wb))) or "-")
        print()

    print("Workbench global hotkeys (these are the ones that cross apps):")
    for chord, info in sorted(wb.items()):
        if info["global"]:
            mark = "  <-- ALSO IN TRADOS" if chord in tr else ""
            print(f"  {chord:<18} {info['desc']}{mark}")
    print()

    if clashes:
        print("COLLISION: the chords below are global in Workbench AND bound in")
        print("Trados, so one press fires both, whichever app is in front:")
        for c in clashes:
            print(f"  {c}: {wb[c]['desc']}  vs  {tr[c]}")
        print("\nMove one of them. Prefer moving the newer binding - fewer")
        print("users will have it in their fingers.")
        return 1

    print("OK - shared chords are all in-app on the Workbench side, so focus")
    print("decides the winner. No global hotkey collides with Trados.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
