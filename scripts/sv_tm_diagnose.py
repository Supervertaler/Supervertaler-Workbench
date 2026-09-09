r"""Supervertaler Workbench - "no TM matches" diagnostic.

Usage
    python sv_tm_diagnose.py
    python sv_tm_diagnose.py "C:\path\to\supervertaler.db" "C:\path\to\project.svproj"
    python sv_tm_diagnose.py --repair        (fixes a dead full-text index in place)

Both paths are optional; the script looks in the usual places. Without
--repair it opens the database read-only and changes nothing.
Please send the entire output back.
"""
import glob
import json
import os
import platform
import re
import sqlite3
import sys

HOME = os.path.expanduser("~")


def find_db(explicit=None):
    if explicit:
        return explicit
    for p in glob.glob(os.path.join(HOME, "Supervertaler", "**", "supervertaler.db"),
                       recursive=True):
        return p
    return None


def find_projects(explicit=None):
    if explicit:
        return [explicit]
    found = []
    for root in (os.path.join(HOME, "Supervertaler"), os.path.join(HOME, "Documents")):
        found += glob.glob(os.path.join(root, "**", "*.svproj"), recursive=True)
    found.sort(key=os.path.getmtime, reverse=True)
    return found[:5]


def find_version():
    """Best effort at the installed Workbench version."""
    here = os.path.dirname(os.path.abspath(__file__))
    for rel in ("..", ".", "../_internal"):
        p = os.path.join(here, rel, "pyproject.toml")
        if os.path.isfile(p):
            try:
                with open(p, "r", encoding="utf-8") as fh:
                    m = re.search(r'version\s*=\s*"([^"]+)"', fh.read())
                if m:
                    return m.group(1)
            except Exception:
                pass
    return "unknown"


def lang_visible(row_lang, project_lang):
    """Replicate the matcher's language filter (v1.10.370+: case-insensitive)."""
    row_lang = (row_lang or "").lower()
    base = (project_lang or "").split("-")[0].lower()
    if not base:
        return True
    return row_lang == base or row_lang.startswith(base + "-")


def probe_term(text):
    """Longest plain word usable as an FTS5 probe, or None."""
    if not text:
        return None
    words = re.findall(r"[^\W\d_]{4,}", text, flags=re.UNICODE)
    return max(words, key=len) if words else None


def check_fts(cur, tm_ids=None):
    """Ask the full-text index to find rows we know exist.

    Row counts cannot answer this: translation_units_fts is an external-content
    table, so COUNT(*) reads translation_units and matches even when the index
    holds nothing at all.
    """
    where, params = "", []
    if tm_ids:
        where = " WHERE tm_id IN (%s)" % ",".join("?" * len(tm_ids))
        params = list(tm_ids)
    rows = []
    for order in ("ASC", "DESC"):
        try:
            rows += cur.execute(
                "SELECT id, source_text FROM translation_units%s ORDER BY id %s LIMIT 3"
                % (where, order), params).fetchall()
        except Exception as e:
            return None, "query failed: %s" % e
    probes = found = 0
    for r in rows:
        term = probe_term(r["source_text"])
        if not term:
            continue
        probes += 1
        try:
            hit = cur.execute(
                "SELECT 1 FROM translation_units_fts "
                "WHERE translation_units_fts MATCH ? AND rowid = ? LIMIT 1",
                ('"%s"' % term, r["id"])).fetchone()
            if hit:
                found += 1
        except Exception as e:
            return None, "MATCH failed: %s" % e
    return (probes, found), None


def main():
    args = [a for a in sys.argv[1:] if a != "--repair"]
    repair = "--repair" in sys.argv
    db_path = find_db(args[0] if len(args) > 0 else None)
    proj_paths = find_projects(args[1] if len(args) > 1 else None)

    print("=" * 70)
    print("SUPERVERTALER TM DIAGNOSTIC")
    print("=" * 70)

    print("\n--- 0. Environment ---")
    print("  Workbench version : %s" % find_version())
    print("  OS                : %s %s (%s)"
          % (platform.system(), platform.release(), platform.machine()))
    print("  Python            : %s" % sys.version.split()[0])
    print("  SQLite            : %s" % sqlite3.sqlite_version)

    src_lang = tgt_lang = None
    proj_id = None
    active_ids = []
    print("\n--- 1. Project files (most recent first) ---")
    if not proj_paths:
        print("  No .svproj found. Pass the project path as the 2nd argument.")
    for p in proj_paths:
        try:
            with open(p, "r", encoding="utf-8") as fh:
                d = json.load(fh)
        except Exception as e:
            print("  %s  (could not read: %s)" % (p, e))
            continue
        s, t = d.get("source_lang"), d.get("target_lang")
        act = (d.get("tm_settings") or {}).get("activated_tm_ids")
        print("  %s" % os.path.basename(p))
        print("      source_lang     : %r" % s)
        print("      target_lang     : %r" % t)
        print("      project id      : %r" % d.get("id"))
        print("      activated_tm_ids: %r" % act)
        if src_lang is None:
            src_lang, tgt_lang, proj_id = s, t, d.get("id")
            active_ids = list(act or [])

    if not db_path or not os.path.isfile(db_path):
        print("\nCould not find supervertaler.db. Pass its path as the 1st argument.")
        return
    print("\n--- 2. Database ---")
    print("  path: %s" % db_path)
    print("  size: %.0f MB" % (os.path.getsize(db_path) / 1e6))

    uri = "file:%s?mode=%s" % (db_path.replace("\\", "/"), "rw" if repair else "ro")
    con = sqlite3.connect(uri, uri=True)
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    def show(title, sql, params=()):
        print("\n--- %s ---" % title)
        try:
            rows = cur.execute(sql, params).fetchall()
        except Exception as e:
            print("  (query failed: %s)" % e)
            return []
        if not rows:
            print("  (no rows)")
            return []
        cols = rows[0].keys()
        print("  " + " | ".join(cols))
        for r in rows:
            print("  " + " | ".join("" if r[c] is None else str(r[c]) for c in cols))
        return rows

    show("3. Registered TMs",
         "SELECT id, tm_id, name, source_lang, target_lang, entry_count "
         "FROM translation_memories")

    unit_rows = show("4. Language pairs actually stored on TM entries",
                     "SELECT tm_id, source_lang, target_lang, COUNT(*) AS units "
                     "FROM translation_units GROUP BY tm_id, source_lang, target_lang "
                     "ORDER BY units DESC")

    # 5. Activation, resolved for THIS project only.
    print("\n--- 5. TMs switched on for this project ('Read' tickbox) ---")
    cols = [r[1] for r in cur.execute("PRAGMA table_info(tm_activation)").fetchall()]
    if not cols:
        print("  (tm_activation table missing)")
    elif proj_id is None:
        print("  (no project id known)")
    else:
        idcol = "tm_db_id" if "tm_db_id" in cols else "tm_id"
        rows = cur.execute(
            "SELECT tm.tm_id, tm.name, ta.is_active FROM tm_activation ta "
            "JOIN translation_memories tm ON tm.id = ta.%s WHERE ta.project_id = ?"
            % idcol, (proj_id,)).fetchall()
        if not rows:
            print("  No activation rows for project %r." % proj_id)
        for r in rows:
            print("  [%s] %s (%s)"
                  % ("ON " if r["is_active"] else "off", r["name"], r["tm_id"]))
        active_ids = [r["tm_id"] for r in rows if r["is_active"]] or active_ids
    print("  => active tm_ids: %r" % active_ids)

    # 6. Full-text index health - the check that matters for fuzzy matches.
    print("\n--- 6. Full-text index (fuzzy matches depend on it) ---")
    res, err = check_fts(cur, active_ids or None)
    fts_ok = None
    if err:
        print("  Could not test the index: %s" % err)
    elif res is None or res[0] == 0:
        print("  No usable sample rows to test with.")
    else:
        probes, found = res
        fts_ok = found > 0
        print("  Asked the index to find %d rows we know exist; it found %d."
              % (probes, found))
        if fts_ok:
            print("  => The full-text index is working.")
        else:
            print("  => THE FULL-TEXT INDEX IS DEAD.")
            print("     Exact (100%) matches still work because they use a hash,")
            print("     but NO fuzzy match can ever be found. This is the bug.")
            print("     Fix: update to v1.10.371+, which detects and rebuilds it")
            print("     automatically on startup - or re-run this script with --repair.")

    # 7. Language visibility.
    print("\n--- 7. Can this project's languages see these entries? ---")
    if src_lang is None:
        print("  No project languages known, skipping.")
        any_visible = True
    else:
        print("  Project is %r -> %r" % (src_lang, tgt_lang))
        any_visible = False
        for r in unit_rows:
            if active_ids and r["tm_id"] not in active_ids:
                continue
            ok = lang_visible(r["source_lang"], src_lang) and \
                lang_visible(r["target_lang"], tgt_lang)
            any_visible = any_visible or ok
            print("    %-14s -> %-14s  %7d units  (%s)  %s"
                  % (r["source_lang"], r["target_lang"], r["units"], r["tm_id"],
                     "visible" if ok else "HIDDEN"))
        if not unit_rows:
            print("    (no translation units at all)")

    print("\n--- 8. Verdict ---")
    if not active_ids:
        print("  No TM is switched on for this project. Tick 'Read' in Resources -> TM.")
    elif fts_ok is False:
        print("  The full-text index is dead: fuzzy matches cannot work, exact ones can.")
        print("  Update to v1.10.371+ (repairs itself on startup), or run --repair.")
    elif not any_visible:
        print("  No active TM entry matches the project's languages.")
    else:
        print("  TMs are on, languages line up and the index works.")
        print("  If the pane is still empty, check the log for a line starting")
        print("  'TM search failed' - v1.10.371+ reports faults instead of")
        print("  showing an empty pane.")

    if repair and fts_ok is False:
        print("\n--- Repairing the full-text index ---")
        try:
            cur.execute(
                "INSERT INTO translation_units_fts(translation_units_fts) VALUES('rebuild')")
            con.commit()
            res2, err2 = check_fts(cur, active_ids or None)
            if res2 and res2[1] > 0:
                print("  Rebuilt. The index now finds %d/%d sample rows." % (res2[1], res2[0]))
                print("  Fuzzy matching should work immediately.")
            else:
                print("  Rebuild ran but the index still finds nothing: %s" % (err2 or ""))
        except Exception as e:
            print("  Repair failed: %s" % e)
    elif repair:
        print("\n  (--repair given, but the index is not the problem; nothing changed.)")

    con.close()
    print("\nDone.")


if __name__ == "__main__":
    main()
