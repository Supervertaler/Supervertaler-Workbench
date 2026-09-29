"""
Find & Replace in translation memories
======================================

Lets Replace All change the project's writable TMs along with the project
(issue #68), so a terminology change doesn't keep coming back as TM matches
that still use the old term.

:func:`make_replacer` is the single description of what one Find & Replace
operation does to a piece of text – it is used for the project grid and for TM
entries alike. :func:`replace_in_tms` applies it to TM entries: first as a
dry run that only counts, then for real.
"""

import hashlib
import re
from typing import Callable, Dict, Iterable, List, Optional

MATCH_ANYTHING, MATCH_WHOLE_WORDS, MATCH_ENTIRE_SEGMENT = 0, 1, 2


def apply_case_pattern(original: str, replacement: str) -> str:
    """Adjust *replacement* to the case pattern of *original* (UPPER, lower,
    Title, Sentence); anything else leaves the replacement as typed."""
    if not original or not replacement:
        return replacement
    if original.isupper():
        return replacement.upper()
    if original.islower():
        return replacement.lower()
    if original.istitle():
        return replacement.title()
    if original[0].isupper() and original[1:].islower():
        return replacement[0].upper() + replacement[1:]
    return replacement


def make_replacer(find_text: str, replace_text: str, *, case_sensitive: bool = False,
                  auto_case: bool = False, match_mode: int = MATCH_ANYTHING,
                  use_regex: bool = False, count: int = 0) -> Callable[[str], str]:
    """``text -> new text`` for one Find & Replace operation.

    ``count`` limits the number of replacements (0 = all), as re.sub does.
    Raises ``re.error`` for an invalid pattern or replacement template.
    """
    flags = 0 if case_sensitive else re.IGNORECASE

    if use_regex:
        pattern = re.compile(find_text, flags)
        return lambda text: pattern.sub(replace_text, text, count=count)

    if match_mode == MATCH_ENTIRE_SEGMENT:
        def whole(text):
            if case_sensitive:
                return replace_text if text == find_text else text
            if text.lower() != find_text.lower():
                return text
            return apply_case_pattern(text, replace_text) if auto_case else replace_text
        return whole

    body = re.escape(find_text)
    if match_mode == MATCH_WHOLE_WORDS:
        body = r'\b' + body + r'\b'
    pattern = re.compile(body, flags)
    if auto_case and not case_sensitive:
        return lambda text: pattern.sub(lambda m: apply_case_pattern(m.group(0), replace_text),
                                        text, count=count)
    # A function, so backslashes in the replacement are taken literally.
    return lambda text: pattern.sub(lambda _m: replace_text, text, count=count)


def _hashes(source: str, target: str):
    """(source_hash, target_hash) exactly as add_translation_unit() writes them."""
    from modules.database_manager import _normalize_for_matching
    return (hashlib.md5(_normalize_for_matching(source).encode('utf-8')).hexdigest(),
            hashlib.md5(_normalize_for_matching(target).encode('utf-8')).hexdigest())


def replace_in_tms(db_manager, tm_ids: Iterable[str], replacer: Callable[[str], str], *,
                   in_source: bool = False, in_target: bool = True, apply: bool = False,
                   progress: Optional[Callable[[int], None]] = None) -> Dict:
    """Run ``replacer`` over the entries of ``tm_ids``.

    With ``apply=False`` nothing is written: the result says how many entries
    would change (and gives a few examples). With ``apply=True`` the entries
    are updated in one transaction, keeping their hashes and the full-text
    index in step. An entry that becomes identical to one already in the same
    TM is merged into it (the TM allows no duplicates).

    Returns ``{'changed': n, 'merged': n, 'per_tm': {tm_id: n}, 'examples': [(old, new)]}``.
    """
    tm_ids = [t for t in tm_ids if t]
    result = {'changed': 0, 'merged': 0, 'per_tm': {}, 'examples': []}
    if not tm_ids or not (in_source or in_target):
        return result

    conn = db_manager.connection
    read = conn.cursor()
    write = conn.cursor()
    marks = ",".join("?" * len(tm_ids))
    read.execute(f"SELECT id, tm_id, source_text, target_text FROM translation_units "
                 f"WHERE tm_id IN ({marks}) ORDER BY id", tm_ids)
    updates = []
    scanned = 0
    while True:
        rows = read.fetchmany(2000)
        if not rows:
            break
        for row in rows:
            entry_id, tm_id, source, target = row[0], row[1], row[2] or "", row[3] or ""
            new_source = replacer(source) if in_source else source
            new_target = replacer(target) if in_target else target
            if new_source != source or new_target != target:
                updates.append((entry_id, tm_id, new_source.strip(), new_target.strip()))
                result['per_tm'][tm_id] = result['per_tm'].get(tm_id, 0) + 1
                if len(result['examples']) < 5:
                    result['examples'].append(
                        (target if in_target else source, new_target if in_target else new_source))
        scanned += len(rows)
        if progress:
            progress(scanned)
    result['changed'] = len(updates)
    if not apply or not updates:
        return result

    import sqlite3
    try:
        # One transaction for the lot. Without an explicit BEGIN the per-row
        # savepoint would be the outermost one, and releasing it commits.
        if not conn.in_transaction:
            write.execute("BEGIN")
        for entry_id, tm_id, new_source, new_target in updates:
            source_hash, target_hash = _hashes(new_source, new_target)
            write.execute("SAVEPOINT tm_replace_row")
            try:
                write.execute("""
                    UPDATE translation_units
                    SET source_text = ?, target_text = ?, source_hash = ?, target_hash = ?,
                        modified_date = CURRENT_TIMESTAMP
                    WHERE id = ?""", (new_source, new_target, source_hash, target_hash, entry_id))
                try:
                    write.execute("UPDATE tm_fts SET source_text = ?, target_text = ? WHERE rowid = ?",
                                  (new_source, new_target, entry_id))
                except sqlite3.OperationalError:
                    pass  # no full-text index in this database
            except sqlite3.IntegrityError:
                # The same source/target pair already exists in this TM: merge.
                write.execute("ROLLBACK TO tm_replace_row")
                write.execute("DELETE FROM translation_units WHERE id = ?", (entry_id,))
                try:
                    write.execute("DELETE FROM tm_fts WHERE rowid = ?", (entry_id,))
                except sqlite3.OperationalError:
                    pass
                result['merged'] += 1
            write.execute("RELEASE tm_replace_row")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return result
