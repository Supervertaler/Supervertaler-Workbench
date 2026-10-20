"""Okapi sidecar: Word formatting becomes the right tags, and comes back out.

Regression (sidecar 0.1.8): Okapi 1.47 describes a run's formatting as
"x-bold;italic;underline:single;sub;" - one "x-" prefix for the lot - but
the sidecar expected one per property ("x-bold;x-italic;x-underlined").
Only the first property was recognised, so bold+italic imported as <b>
alone, and underline, subscript and superscript as anonymous <gN> tags.

These tests run the real sidecar on a small DOCX built here, so they are
skipped when Java or the sidecar JAR is missing, or when the JAR is not the
version the client expects (rebuild it: okapi-sidecar/build.ps1). They pin:

  1. the tags each kind of formatting imports as;
  2. that exporting those tags reproduces the formatting;
  3. that a project imported by the old sidecar - holding <b> for
     bold+italic and <gN> for underline/sub/sup - still exports with all
     of its formatting.
"""

from __future__ import annotations

import os
import re
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

docx = pytest.importorskip('docx')

from modules.okapi_sidecar import OkapiSidecar  # noqa: E402

PORT = 8097   # not 8090, so a running Workbench keeps its own sidecar


@pytest.fixture(scope='module')
def sidecar():
    sc = OkapiSidecar(port=PORT)
    if not sc.is_installed() or sc._find_java() is None:
        pytest.skip('Okapi sidecar JAR or Java not available')
    if not sc.start():
        pytest.skip('Okapi sidecar could not be started')
    try:
        if sc.get_version() != OkapiSidecar.EXPECTED_VERSION:
            pytest.skip(f'sidecar JAR is v{sc.get_version()}, client expects '
                        f'v{OkapiSidecar.EXPECTED_VERSION}: rebuild it')
        yield sc
    finally:
        sc.stop()


@pytest.fixture(scope='module')
def source(tmp_path_factory):
    path = tmp_path_factory.mktemp('okapi') / 'formatting.docx'
    d = docx.Document()
    p = d.add_paragraph('Plain ')
    r = p.add_run('both')
    r.bold = r.italic = True
    p.add_run(' then ')
    p.add_run('under').underline = True
    p.add_run(' CH')
    p.add_run('4').font.subscript = True
    p.add_run(' m')
    p.add_run('2').font.superscript = True
    p.add_run(' and ')
    p.add_run('bold').bold = True
    p.add_run(', ')
    p.add_run('italic').italic = True
    p.add_run('.')
    d.save(str(path))
    return str(path)


def _extract(sc, path):
    res = sc.extract(path, 'en', 'nl', True, None)
    return [(s['id'], (s.get('sourceWithTags') or s['source']).strip())
            for s in res['segments'] if s['source'].strip()]


def _export(sc, source, segments, out):
    sc.merge(source, [{'id': i, 'segmentIndex': 0, 'translation': t} for i, t in segments],
             'en', 'nl', str(out), None)
    xml = zipfile.ZipFile(out).read('word/document.xml').decode('utf-8')
    runs = {}
    for run in re.findall(r'<w:r[ >].*?</w:r>', xml, re.S):
        text = ''.join(re.findall(r'<w:t[^>]*>([^<]*)</w:t>', run)).strip()
        props = re.search(r'<w:rPr>(.*?)</w:rPr>', run, re.S)
        props = props.group(1) if props else ''
        found = set()
        if re.search(r'<w:b(?: w:val="(?:true|1)")?/>', props):
            found.add('bold')
        if re.search(r'<w:i(?: w:val="(?:true|1)")?/>', props):
            found.add('italic')
        if re.search(r'<w:u w:val="(?!none)', props):
            found.add('underline')
        m = re.search(r'<w:vertAlign w:val="(\w+)"', props)
        if m:
            found.add(m.group(1))
        if text:
            runs[text] = found
    return runs


EXPECTED_TAGS = ('Plain <b><i>both</i></b> then <u>under</u> CH<sub>4</sub> '
                 'm<sup>2</sup> and <b>bold</b>, <i>italic</i>.')

EXPECTED_RUNS = {
    'both': {'bold', 'italic'}, 'under': {'underline'}, '4': {'subscript'},
    '2': {'superscript'}, 'bold': {'bold'}, 'italic': {'italic'},
}


def test_formatting_imports_as_named_tags(sidecar, source):
    assert [t for _, t in _extract(sidecar, source)] == [EXPECTED_TAGS]


def test_exported_tags_reproduce_the_formatting(sidecar, source, tmp_path):
    runs = _export(sidecar, source, _extract(sidecar, source), tmp_path / 'out.docx')
    assert {k: runs.get(k) for k in EXPECTED_RUNS} == EXPECTED_RUNS


def test_translated_and_moved_tags_land_on_the_right_words(sidecar, source, tmp_path):
    # The lone <i> now comes first. It must take the italic run, not the
    # bold+italic run that precedes it in the source (which is also queued
    # under "i"). Runs sharing a tag name are still matched in source order:
    # swapping "both" and "bold" would swap their formatting, as in 0.1.8.
    (tu_id, _), = _extract(sidecar, source)
    moved = ('<i>italic</i> eerst, m<sup>2</sup>, CH<sub>4</sub>, <u>under</u>, '
             '<b><i>both</i></b> en <b>bold</b>.')
    runs = _export(sidecar, source, [(tu_id, moved)], tmp_path / 'moved.docx')
    assert {k: runs.get(k) for k in EXPECTED_RUNS} == EXPECTED_RUNS


def test_project_imported_by_old_sidecar_still_exports_its_formatting(sidecar, source, tmp_path):
    (tu_id, tagged), = _extract(sidecar, source)
    # What sidecar 0.1.8 stored: <b> alone for bold+italic, and the code's
    # generic <gN> for underline, subscript and superscript.
    legacy = tagged.replace('<b><i>both</i></b>', '<b>both</b>')
    for n, tag in ((2, 'u'), (3, 'sub'), (4, 'sup')):
        legacy = legacy.replace(f'<{tag}>', f'<g{n}>', 1).replace(f'</{tag}>', f'</g{n}>', 1)
    assert legacy == ('Plain <b>both</b> then <g2>under</g2> CH<g3>4</g3> '
                      'm<g4>2</g4> and <b>bold</b>, <i>italic</i>.')
    runs = _export(sidecar, source, [(tu_id, legacy)], tmp_path / 'legacy.docx')
    assert {k: runs.get(k) for k in EXPECTED_RUNS} == EXPECTED_RUNS
