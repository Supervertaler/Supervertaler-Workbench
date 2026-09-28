"""
Resource Backup
===============

Writes every translation resource in the Supervertaler database out to open,
tool-neutral files in one go (issue #52):

    <folder>/Supervertaler resources <date time>/
        TMs/<TM name>.tmx            one TMX 1.4 file per translation memory
        Termbases/<termbase>.tsv     one TSV per termbase (non-translatables
                                     are termbase entries flagged in the
                                     "Non-translatable" column)
        README.txt                   what was written, with counts

Unlike the database itself, these files can be opened, checked and imported
by any CAT tool, which is what makes them a real backup.

TMX entries carry their OWN language pair (a TM can hold entries in both
directions), creation/change dates, author and notes, and are streamed from
the database in chunks, so very large TMs don't need to fit in memory.

Kept free of Qt: the caller supplies the database / manager objects.
"""

import os
import re
from datetime import datetime
from typing import Callable, Dict, List, Optional
from xml.sax.saxutils import escape, quoteattr

# Characters XML 1.0 forbids; a stray control character in a TM would
# otherwise make the whole TMX file unreadable.
_INVALID_XML = re.compile('[\x00-\x08\x0b\x0c\x0e-\x1f￾￿]')


def _xml_text(text: str) -> str:
    return escape(_INVALID_XML.sub('', text or ''))


def _tmx_date(value) -> Optional[str]:
    """'2026-09-28 21:05:00' (SQLite CURRENT_TIMESTAMP) -> '20260928T210500Z'."""
    if not value:
        return None
    text = str(value).strip().replace('T', ' ').rstrip('Z')
    for fmt in ('%Y-%m-%d %H:%M:%S.%f', '%Y-%m-%d %H:%M:%S', '%Y-%m-%d'):
        try:
            return datetime.strptime(text, fmt).strftime('%Y%m%dT%H%M%SZ')
        except ValueError:
            continue
    return None


def safe_file_name(name: str, taken: set) -> str:
    """A file-system-safe, unique (case-insensitively) base name."""
    base = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', '_', (name or '').strip()).strip(' .') or 'untitled'
    base = base[:120]
    candidate, n = base, 2
    while candidate.lower() in taken:
        candidate = f"{base} ({n})"
        n += 1
    taken.add(candidate.lower())
    return candidate


def write_tm_tmx(cursor, tm: Dict, path: str, tool_version: str = "",
                 chunk_size: int = 2000) -> int:
    """Stream one TM's translation units into a TMX 1.4 file. Returns the
    number of units written."""
    cursor.execute("""
        SELECT source_text, target_text, source_lang, target_lang,
               created_date, modified_date, created_by, notes
        FROM translation_units WHERE tm_id = ? ORDER BY id
    """, (tm['tm_id'],))
    srclang = tm.get('source_lang') or '*all*'
    count = 0
    with open(path, 'w', encoding='utf-8', newline='\n') as f:
        f.write('<?xml version="1.0" encoding="UTF-8"?>\n')
        f.write('<tmx version="1.4">\n')
        f.write('  <header creationtool="Supervertaler Workbench" '
                f'creationtoolversion={quoteattr(tool_version or "")} '
                'datatype="plaintext" segtype="sentence" adminlang="en" '
                f'srclang={quoteattr(srclang)} o-tmf="Supervertaler">\n')
        f.write(f'    <prop type="x-tm-name">{_xml_text(tm.get("name", ""))}</prop>\n')
        f.write('  </header>\n  <body>\n')
        while True:
            rows = cursor.fetchmany(chunk_size)
            if not rows:
                break
            for source, target, src_lang, tgt_lang, created, modified, author, notes in rows:
                attrs = ''
                created, modified = _tmx_date(created), _tmx_date(modified)
                if created:
                    attrs += f' creationdate="{created}"'
                if modified:
                    attrs += f' changedate="{modified}"'
                if author:
                    attrs += f' creationid={quoteattr(_INVALID_XML.sub("", str(author)))}'
                f.write(f'    <tu{attrs}>\n')
                if notes:
                    f.write(f'      <note>{_xml_text(notes)}</note>\n')
                f.write(f'      <tuv xml:lang={quoteattr(src_lang or srclang)}>'
                        f'<seg>{_xml_text(source)}</seg></tuv>\n')
                f.write(f'      <tuv xml:lang={quoteattr(tgt_lang or tm.get("target_lang") or "")}>'
                        f'<seg>{_xml_text(target)}</seg></tuv>\n')
                f.write('    </tu>\n')
                count += 1
        f.write('  </body>\n</tmx>\n')
    return count


def backup_all_resources(db_manager, tm_metadata_mgr, termbase_mgr, out_folder: str,
                         tool_version: str = "",
                         progress: Optional[Callable[[str], None]] = None) -> Dict:
    """Write every TM and termbase in the database to ``out_folder``.

    Returns a summary dict: ``folder``, ``tms`` and ``termbases`` (lists of
    ``(name, file name, count)``) and ``errors`` (list of messages). One
    resource failing never stops the others from being written.
    """
    from modules.termbase_import_export import TermbaseExporter

    stamp = datetime.now().strftime('%Y-%m-%d %H%M')
    folder = os.path.join(out_folder, f"Supervertaler resources {stamp}")
    tm_dir = os.path.join(folder, 'TMs')
    tb_dir = os.path.join(folder, 'Termbases')
    os.makedirs(tm_dir, exist_ok=True)
    os.makedirs(tb_dir, exist_ok=True)
    say = progress or (lambda _msg: None)
    summary = {'folder': folder, 'tms': [], 'termbases': [], 'errors': []}

    taken: set = set()
    for tm in tm_metadata_mgr.get_all_tms() or []:
        name = tm.get('name') or tm.get('tm_id')
        file_name = safe_file_name(name, taken) + '.tmx'
        say(f"TM: {name}")
        try:
            cursor = db_manager.connection.cursor()
            try:
                n = write_tm_tmx(cursor, tm, os.path.join(tm_dir, file_name), tool_version)
            finally:
                cursor.close()
            summary['tms'].append((name, file_name, n))
        except Exception as e:
            summary['errors'].append(f"TM '{name}': {e}")

    taken = set()
    exporter = TermbaseExporter(db_manager, termbase_mgr)
    for tb in termbase_mgr.get_all_termbases() or []:
        name = tb.get('name') or f"termbase {tb.get('id')}"
        file_name = safe_file_name(name, taken) + '.tsv'
        say(f"Termbase: {name}")
        try:
            terms = termbase_mgr.get_terms(tb['id']) or []
            if terms:
                ok, message = exporter.export_tsv(tb['id'], os.path.join(tb_dir, file_name),
                                                  include_metadata=True)
                if not ok:
                    raise RuntimeError(message)
            else:
                # Keep empty termbases visible in the backup too.
                with open(os.path.join(tb_dir, file_name), 'w', encoding='utf-8-sig') as f:
                    f.write('Term UUID\tSource\tTarget\tDomain\tNotes\tProject\tClient\t'
                            'Forbidden\tNon-translatable\n')
            nt = sum(1 for t in terms if t.get('is_nontranslatable'))
            summary['termbases'].append((name, file_name, len(terms), nt,
                                         tb.get('source_lang') or '', tb.get('target_lang') or ''))
        except Exception as e:
            summary['errors'].append(f"Termbase '{name}': {e}")

    _write_readme(summary, tool_version)
    return summary


def _write_readme(summary: Dict, tool_version: str) -> None:
    lines: List[str] = [
        "Supervertaler resource backup",
        f"Written {datetime.now().strftime('%Y-%m-%d %H:%M')} by Supervertaler Workbench {tool_version}".rstrip(),
        "",
        "TMs/        one TMX 1.4 file per translation memory (each entry keeps its own",
        "            language pair, dates, author and note)",
        "Termbases/  one tab-separated file per termbase; synonyms are |-separated,",
        "            forbidden synonyms are written [!like this], and non-translatables",
        "            are marked TRUE in the Non-translatable column",
        "",
        "To restore: import the TMX files as TMs and the TSV files as termbases in",
        "Supervertaler (or any CAT tool that reads TMX and tab-separated glossaries).",
        "",
        f"Translation memories ({len(summary['tms'])}):",
    ]
    for name, file_name, n in summary['tms']:
        lines.append(f"  {file_name}  –  {n:,} entries  ({name})")
    lines.append("")
    lines.append(f"Termbases ({len(summary['termbases'])}):")
    for name, file_name, n, nt, src, tgt in summary['termbases']:
        pair = f", {src} → {tgt}" if src or tgt else ""
        nt_text = f", {nt:,} non-translatable" if nt else ""
        lines.append(f"  {file_name}  –  {n:,} terms{nt_text}{pair}  ({name})")
    if summary['errors']:
        lines.append("")
        lines.append("Problems:")
        lines.extend(f"  {e}" for e in summary['errors'])
    with open(os.path.join(summary['folder'], 'README.txt'), 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines) + '\n')
