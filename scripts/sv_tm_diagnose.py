r"""Supervertaler Workbench - "no TM matches" diagnostic.

Usage
    python sv_tm_diagnose.py
    python sv_tm_diagnose.py "C:\path\to\supervertaler.db" "C:\path\to\project.svproj"

Both arguments are optional; the script looks in the usual places.
It opens the database read-only and changes nothing.
Please send the entire output back.
"""
import glob
import json
import os
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


def visible(row_lang, project_lang):
    """Replicate the matcher's SQL filter: '=' is case-sensitive in SQLite,
    LIKE 'code-%' is case-insensitive."""
    row_lang = row_lang or ""
    base = (project_lang or "").split("-")[0].lower()
    if not base:
        return True
    return row_lang == base or row_lang.lower().startswith(base + "-")


def main():
    db_path = find_db(sys.argv[1] if len(sys.argv) > 1 else None)
    proj_paths = find_projects(sys.argv[2] if len(sys.argv) > 2 else None)

    print("=" * 70)
    print("SUPERVERTALER TM DIAGNOSTIC")
    print("=" * 70)

    src_lang = tgt_lang = None
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
        print("      path            : %s" % p)
        print("      source_lang     : %r" % s)
        print("      target_lang     : %r" % t)
        print("      project id      : %r" % d.get("id"))
        print("      activated_tm_ids: %r" % act)
        if src_lang is None:
            src_lang, tgt_lang = s, t

    if not db_path or not os.path.isfile(db_path):
        print("\nCould not find supervertaler.db. Pass its path as the 1st argument.")
        return
    print("\n--- 2. Database ---")
    print("  path: %s" % db_path)
    print("  size: %d bytes" % os.path.getsize(db_path))

    con = sqlite3.connect("file:%s?mode=ro" % db_path.replace("\\", "/"), uri=True)
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
         "SELECT id, tm_id, name, source_lang, target_lang, entry_count, read_only "
         "FROM translation_memories")

    unit_rows = show("4. Language pairs actually stored on TM entries",
                     "SELECT tm_id, source_lang, target_lang, COUNT(*) AS units "
                     "FROM translation_units GROUP BY tm_id, source_lang, target_lang "
                     "ORDER BY units DESC")

    # tm_activation column name changed across versions.
    cols = [r[1] for r in cur.execute("PRAGMA table_info(tm_activation)").fetchall()]
    if cols:
        idcol = "tm_db_id" if "tm_db_id" in cols else "tm_id"
        show("5. TM activation (project_id 0 = global; 'Read' tickbox)",
             "SELECT %s AS tm_ref, project_id, is_active FROM tm_activation "
             "ORDER BY project_id" % idcol)
    else:
        print("\n--- 5. TM activation ---\n  (table missing)")

    print("\n--- 6. Verdict: can this project see these TM entries? ---")
    if src_lang is None:
        print("  No project languages known, skipping.")
    else:
        print("  Project is %r -> %r" % (src_lang, tgt_lang))
        print("  A row is visible only if its language equals the project's base code")
        print("  EXACTLY (case-sensitive) or starts with 'code-'.")
        print()
        any_visible = False
        for r in unit_rows:
            s, t = r["source_lang"], r["target_lang"]
            ok = visible(s, src_lang) and visible(t, tgt_lang)
            any_visible = any_visible or ok
            print("    %-14s -> %-14s  %7d units   %s"
                  % (s, t, r["units"], "VISIBLE" if ok else "HIDDEN"))
        print()
        print("  => %s" % ("At least one TM language pair is visible; the cause is"
                           " elsewhere (most likely the TM's Read tickbox, section 5)."
                           if any_visible else
                           "NO TM entry is visible to this project. This is the bug."))

    con.close()
    print("\nDone.")


if __name__ == "__main__":
    main()
