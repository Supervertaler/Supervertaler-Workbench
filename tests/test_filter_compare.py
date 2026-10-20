"""tools/filter_compare.py: Workbench vs Trados/memoQ extraction comparison.

The tool's value is in not crying wolf: a difference it reports must be a real
difference in what the programs extracted, and a broken input file must never
stop the run. These tests pin the readers (Workbench tagged text, SDLXLIFF,
MQXLIFF), the alignment and its gap sliding, the classification of each kind
of difference, the failure paths (missing counterpart, unreadable file,
sidecar down) and that the alignment stays fast at real-document size.
"""

from __future__ import annotations

import os
import random
import sys
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, 'tools'))

import pytest

import filter_compare as fc
from modules.okapi_import_options import DEFAULT_IMPORT_OPTIONS, okapi_sidecar_options


# ── helpers ─────────────────────────────────────────────────────────

SDLXLIFF = """<?xml version="1.0" encoding="utf-8"?>
<xliff xmlns:sdl="http://sdl.com/FileTypes/SdlXliff/1.0" xmlns="urn:oasis:names:tc:xliff:document:1.2" version="1.2">
<file original="t.docx" datatype="x-sdlfilterframework2" source-language="en-GB" target-language="nl-NL">
<header>
<tag-defs xmlns="http://sdl.com/FileTypes/SdlXliff/1.0">
<tag id="8"><bpt name="cf">&lt;cf bold=True&gt;</bpt><ept name="cf">&lt;/cf&gt;</ept><fmt id="3"/></tag>
<tag id="9"><bpt name="cf">&lt;cf italic=True&gt;</bpt><ept name="cf">&lt;/cf&gt;</ept></tag>
<tag id="10"><bpt name="cf">&lt;cf bold=False&gt;</bpt><ept name="cf">&lt;/cf&gt;</ept></tag>
<tag id="5"><ph name="picture">&lt;picture/&gt;</ph></tag>
</tag-defs>
<fmt-defs xmlns="http://sdl.com/FileTypes/SdlXliff/1.0"><fmt-def id="3"><value key="Bold">True</value></fmt-def></fmt-defs>
</header>
<body>
{units}
</body></file></xliff>
"""

SEGMENTED_UNITS = """<trans-unit id="tu1"><source>x</source><seg-source><mrk mtype="seg" mid="1">R&amp;D is <g id="8">bold</g>.</mrk> <mrk mtype="seg" mid="2">Then <g id="9">it</g><x id="5"/> ends <mrk mtype="x-sdl-deleted">gone</mrk>here, <g id="10">plain</g>.</mrk></seg-source><target><mrk mtype="seg" mid="1"/> <mrk mtype="seg" mid="2"/></target></trans-unit>"""

UNSEGMENTED_UNITS = """<trans-unit id="tu1"><source>Only <g id="8">source</g>.</source><seg-source>Only <g id="8">source</g>.</seg-source><target/></trans-unit>"""

MQXLIFF = """<?xml version="1.0" encoding="utf-8"?>
<xliff xmlns="urn:oasis:names:tc:xliff:document:1.2" xmlns:mq="MQXliff" version="1.2">
<file original="t.docx" source-language="nl-NL" target-language="en-GB" datatype="x-mq"><body>
<trans-unit id="1"><source xml:space="preserve">Dit is <bpt id="1" ctype="bold">{}</bpt>vet<ept id="1">{}</ept> en <ph id="2">{}</ph>klaar.</source><target xml:space="preserve"></target></trans-unit>
<trans-unit id="2" mq:nosplitjoin="true"><source>https://example.com</source><target/></trans-unit>
</body></file></xliff>
"""


def write(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(text, encoding='utf-8')
    return str(p)


def ex(tool, *segs):
    """Build an Extraction from Workbench-style tagged strings."""
    out = []
    for s in segs:
        where = 'body'
        if isinstance(s, tuple):
            s, where = s
        seg = fc.parse_workbench_tagged(s)
        seg.where = where
        out.append(seg)
    return fc.Extraction(tool, f'{tool}.file', out)


def kinds(findings):
    return {(f.category, f.detail): (len(f.segments), f.count) for f in findings.values()}


class FakeSidecar:
    def __init__(self, segments=(), exc=None, running=True, can_start=True):
        self.segments = list(segments)
        self.exc = exc
        self.running = running
        self.can_start = can_start
        self.calls = []

    def is_running(self):
        return self.running

    def start(self):
        self.running = self.can_start
        return self.can_start

    def stop(self):
        self.running = False

    def extract(self, path, source_lang, target_lang, segment, options):
        self.calls.append(dict(path=path, source_lang=source_lang, target_lang=target_lang,
                               segment=segment, options=options))
        if self.exc:
            raise self.exc
        return {'segments': self.segments}


# ── readers ─────────────────────────────────────────────────────────

def test_workbench_tagged_text():
    seg = fc.parse_workbench_tagged('<b>Bold</b> and <i>it</i>, a < b <x3/> <g12>x</g12> <cf color="#f00">red</cf>')
    assert seg.text == 'Bold and it, a < b  x red'
    pairs = {(min(t.labels), seg.text[t.start:t.end]) for t in seg.tags if t.kind == 'pair'}
    assert pairs == {('bold', 'Bold'), ('italic', 'it'), ('other', 'x'), ('colour', 'red')}
    assert [t.start for t in seg.tags if t.kind == 'single'] == [len('Bold and it, a < b ')]


def test_workbench_unclosed_and_stray_closing_tags_cover_to_the_edge():
    seg = fc.parse_workbench_tagged('start</b> middle <i>to the end')
    spans = {min(t.labels): seg.text[t.start:t.end] for t in seg.tags}
    assert spans == {'bold': 'start', 'italic': 'to the end'}


def test_read_workbench_mirrors_the_import(tmp_path):
    sc = FakeSidecar([
        {'source': 'Hello <world>', 'sourceWithTags': '<b>Hello</b> <world>', 'subDocument': 'word/document.xml'},
        {'source': '   ', 'sourceWithTags': None, 'subDocument': 'word/document.xml'},
        {'source': 'Page 1', 'sourceWithTags': None, 'subDocument': 'word/header1.xml'},
    ])
    out = fc.read_workbench(sc, 'x.docx', 'en-GB', 'nl-NL')
    assert [s.text for s in out.segments] == ['Hello <world>', 'Page 1']   # empty one skipped
    assert [s.where for s in out.segments] == ['body', 'header']
    assert sc.calls[0]['segment'] is True
    assert sc.calls[0]['options'] == okapi_sidecar_options(dict(DEFAULT_IMPORT_OPTIONS))


def test_read_trados_segments_tags_and_escaped_text(tmp_path):
    path = write(tmp_path, 't.docx.sdlxliff', SDLXLIFF.format(units=SEGMENTED_UNITS))
    out = fc.read_trados(path)
    assert (out.source_lang, out.target_lang) == ('en-GB', 'nl-NL')
    first, second = out.segments
    assert first.text == 'R&D is bold.'                      # leading "&" survives
    assert [(set(t.labels), first.text[t.start:t.end]) for t in first.tags] == [({'bold'}, 'bold')]
    assert second.text == 'Then it ends here, plain.'         # tracked deletion left out
    labels = {(t.kind, min(t.labels)) for t in second.tags if t.labels}
    assert ('pair', 'italic') in labels and ('single', 'picture') in labels
    # "bold=False" is still a tag, but not bold: it is "other", which is
    # also what Okapi's unnamed <gN> for the same run is called.
    plain = [t for t in second.tags if second.text[t.start:t.end] == 'plain']
    assert plain and plain[0].labels == {'other'}


def test_read_trados_unsegmented_file_says_what_to_do(tmp_path):
    path = write(tmp_path, 'u.docx.sdlxliff', SDLXLIFF.format(units=UNSEGMENTED_UNITS))
    with pytest.raises(ValueError, match='target-language folder'):
        fc.read_trados(path)


def test_read_trados_corrupt_file(tmp_path):
    path = write(tmp_path, 'bad.docx.sdlxliff', '<xliff><file><body><trans-unit>')
    with pytest.raises(ValueError):
        fc.read_trados(path)


def test_read_memoq(tmp_path):
    out = fc.read_memoq(write(tmp_path, 't.docx_eng-GB.mqxliff', MQXLIFF))
    assert [s.text for s in out.segments] == ['Dit is vet en klaar.']   # nosplitjoin unit skipped
    seg = out.segments[0]
    assert [(t.kind, min(t.labels), seg.text[t.start:t.end]) for t in seg.tags] == [
        ('pair', 'bold', 'vet'), ('single', 'placeholder', '')]


def test_read_memoq_corrupt_file(tmp_path):
    with pytest.raises(ValueError):
        fc.read_memoq(write(tmp_path, 'bad.mqxliff', 'not xml at all'))


# ── alignment ───────────────────────────────────────────────────────

def _check_monotonic(a2b):
    js = [j for j in a2b if j >= 0]
    assert js == sorted(js) and len(js) == len(set(js))


@pytest.mark.parametrize('a, b, matched', [
    ('the cat sat on the mat'.split(), 'the cat sat on the mat'.split(), 6),
    ('the cat sat on the mat'.split(), 'the big cat sat on the mat'.split(), 6),
    ('the big cat sat on the mat'.split(), 'the cat sat on the mat'.split(), 6),
    ('a b c'.split(), 'x y z'.split(), 0),
    ([], 'x y'.split(), 0),
])
def test_align_basic(a, b, matched):
    a2b, given_up = fc.align(a, b)
    _check_monotonic(a2b)
    assert sum(j >= 0 for j in a2b) == matched and not given_up
    assert all(a[i] == b[j] for i, j in enumerate(a2b) if j >= 0)


def test_align_gives_up_on_huge_repetitive_regions_instead_of_hanging(monkeypatch):
    monkeypatch.setattr(fc, 'SMALL_REGION', 100)
    a2b, given_up = fc.align(['x', 'y'] * 50, ['y', 'x'] * 50 + ['z'])
    assert given_up
    _check_monotonic(a2b)


def test_align_scales_to_large_documents():
    """10,000 segments of ~15 words with scattered edits: must stay fast
    (an O(n^2) diff would take minutes here)."""
    rng = random.Random(7)
    vocab = [f'w{k}' for k in range(3000)]
    a = []
    for s in range(10_000):
        a += [rng.choice(vocab[:200]) if rng.random() < 0.6 else rng.choice(vocab) for _ in range(15)]
        a.append(str(s))                              # a unique number per segment
    b = list(a)
    for _ in range(400):
        p = rng.randrange(len(b))
        if rng.random() < 0.5:
            del b[p]
        else:
            b.insert(p, rng.choice(vocab))
    t0 = time.perf_counter()
    a2b, given_up = fc.align(a, b)
    elapsed = time.perf_counter() - t0
    _check_monotonic(a2b)
    assert sum(j >= 0 for j in a2b) > 0.98 * len(a)
    assert elapsed < 20, f'alignment of {len(a)} tokens took {elapsed:.1f}s'


# ── differences ─────────────────────────────────────────────────────

def test_identical_extractions_have_no_differences():
    a = ex('Trados', '<b>One</b> two.', 'Three <x1/>four.')
    b = ex('Workbench', '<b>One</b> two.', 'Three <x1/>four.')
    findings, notes = fc.compare(a, b)
    assert findings == {} and notes == []


def test_list_number_split_is_reported_and_grouped():
    a = ex('Trados', '1.', 'This is first.', '2.', 'This is second.')
    b = ex('Workbench', '1. This is first.', '2. This is second.')
    found = kinds(fc.compare(a, b)[0])
    assert found == {('ref_splits', 'a list number such as “1.”' + fc._SEP + 'a capitalised word'): (2, 2)}
    text = fc.describe('Trados', 'ref_splits', next(iter(found))[1])
    assert text.startswith('Trados starts a new segment after a list number')


def test_workbench_split_after_abbreviation():
    a = ex('Trados', 'See Fig. 3 for details.')
    b = ex('Workbench', 'See Fig.', '3 for details.')
    assert kinds(fc.compare(a, b)[0]) == {('wb_splits', '“Fig.”' + fc._SEP + 'a number'): (1, 1)}


def test_dropped_formatting_and_placeholder():
    a = ex('Trados', 'The <b><i>bold italic</i></b> words<x1/> end.')
    b = ex('Workbench', 'The <b>bold italic</b> words end.')
    found = kinds(fc.compare(a, b)[0])
    assert found == {('fmt_missing', 'italic'): (1, 1), ('ph_missing', 'placeholder'): (1, 1)}


def test_formatting_inside_a_word_counts():
    # The subscript 4 in "CH4" covers part of one token; it must not be
    # invisible just because the tag doesn't span the whole word.
    a = ex('Trados', 'Methane is CH<sub>4</sub> here.')
    b = ex('Workbench', 'Methane is CH<g1>4</g1> here.')
    found = kinds(fc.compare(a, b)[0])
    assert found == {('fmt_missing', 'subscript'): (1, 1), ('fmt_extra', 'other'): (1, 1)}


def test_placeholder_on_neighbouring_word_still_counts_as_present():
    a = ex('Trados', 'Figure <x1/>(lifeboat)')
    b = ex('Workbench', 'Figure<x9/> (lifeboat)')
    assert fc.compare(a, b)[0] == {}


def test_text_only_on_one_side_with_location():
    a = ex('Trados', 'Body text here.', 'https://example.com')
    b = ex('Workbench', ('Page 1 of 3', 'header'), 'Body text here.')
    found = kinds(fc.compare(a, b)[0])
    assert ('only_ref', '') in found and ('only_wb', 'header') in found
    assert 'header' in fc.describe('Trados', 'only_wb', 'header')


def test_moved_text_is_recognised_even_when_the_diff_reads_it_shifted():
    # Okapi lists footnotes at the end; Trados in place. Without gap sliding
    # the extra run on the Workbench side starts with the body's final "."
    # and no longer matches the Trados run.
    a = ex('Trados', 'It has a note.', 'Footnote text here.', 'Last one.')
    b = ex('Workbench', 'It has a note.', 'Last one.', 'Footnote text here.')
    found = kinds(fc.compare(a, b)[0])
    assert found == {('moved', ''): (1, 1)}


def test_different_documents_are_flagged():
    a = ex('Trados', 'Just a few words.')
    b = ex('Workbench', 'Just a few words.', 'And a great deal more text that Trados never saw at all.')
    notes = fc.compare(a, b)[1]
    assert any('may not come from this source document' in n for n in notes)


def test_report_escapes_document_text():
    a = ex('Trados', 'Text <script>alert(1)</script> here.')
    b = ex('Workbench', 'Text here.')
    res = fc.FileResult(fc.Job('s.docx', 't.sdlxliff'), {'Trados': a, 'Workbench': b})
    res.findings['Trados'] = fc.compare(a, b)[0]
    page = fc.render_report([res], time.time())
    assert '<script>' not in page and '&lt;script&gt;' in page


# ── pairing and failure paths ──────────────────────────────────────

def test_find_jobs_prefers_segmented_sdlxliff_and_pairs_memoq(tmp_path):
    (tmp_path / 'en-GB').mkdir()
    (tmp_path / 'nl-NL').mkdir()
    (tmp_path / 'en-GB' / 'Spec.docx').write_bytes(b'')
    (tmp_path / 'en-GB' / '~$Spec.docx').write_bytes(b'')
    write(tmp_path / 'en-GB', 'Spec.docx.sdlxliff', SDLXLIFF.format(units=UNSEGMENTED_UNITS))
    seg = write(tmp_path / 'nl-NL', 'Spec.docx.sdlxliff', SDLXLIFF.format(units=SEGMENTED_UNITS))
    mq = write(tmp_path, 'Spec.docx_eng-GB.mqxliff', MQXLIFF)
    (tmp_path / 'Other.docx').write_bytes(b'')
    jobs = {os.path.basename(j.source): j for j in fc.find_jobs(tmp_path)}
    assert set(jobs) == {'Spec.docx', 'Other.docx'}
    assert jobs['Spec.docx'].trados == seg and jobs['Spec.docx'].memoq == mq
    assert jobs['Other.docx'].trados is None and jobs['Other.docx'].memoq is None


def test_run_job_without_counterpart_is_skipped_not_fatal():
    res = fc.run_job(fc.Job('x.docx'), FakeSidecar(), None)
    assert res.errors and 'skipped' in res.errors[0] and not res.findings


def test_run_job_unreadable_trados_still_compares_memoq(tmp_path):
    bad = write(tmp_path, 'x.docx.sdlxliff', '<<<')
    mq = write(tmp_path, 'x.docx_eng-GB.mqxliff', MQXLIFF)
    sc = FakeSidecar([{'source': 'Dit is vet en klaar.', 'sourceWithTags': 'Dit is <b>vet</b> en <x1/>klaar.'}])
    res = fc.run_job(fc.Job('x.docx', bad, mq), sc, None)
    assert len(res.errors) == 1 and 'Trados' in res.errors[0]
    assert res.findings['memoQ'] == {}
    assert sc.calls[0]['source_lang'] == 'nl-NL'          # taken from the MQXLIFF


def test_run_job_sidecar_failure_is_reported(tmp_path):
    mq = write(tmp_path, 'x.docx_eng-GB.mqxliff', MQXLIFF)
    res = fc.run_job(fc.Job('x.docx', None, mq), FakeSidecar(exc=ConnectionError('refused')), None)
    assert any('Okapi' in e and 'refused' in e for e in res.errors)
    res = fc.run_job(fc.Job('x.docx', None, mq), FakeSidecar(running=False, can_start=False), None)
    assert any('could not be restarted' in e for e in res.errors)


def test_main_exits_2_when_sidecar_cannot_start(tmp_path, monkeypatch, capsys):
    (tmp_path / 'a.docx').write_bytes(b'')
    write(tmp_path, 'a.docx_eng-GB.mqxliff', MQXLIFF)
    import modules.okapi_sidecar as osc

    class DeadSidecar(FakeSidecar):
        sidecar_dir = 'nowhere'

        def __init__(self):
            super().__init__(running=False, can_start=False)
    monkeypatch.setattr(osc, 'OkapiSidecar', DeadSidecar)
    assert fc.main([str(tmp_path)]) == 2
    assert 'could not be started' in capsys.readouterr().out
    assert not (tmp_path / 'filter-compare-report.html').exists()


def test_main_exits_2_when_no_source_files(tmp_path):
    assert fc.main([str(tmp_path)]) == 2


def test_main_writes_report(tmp_path, monkeypatch):
    (tmp_path / 'a.docx').write_bytes(b'')
    write(tmp_path, 'a.docx_eng-GB.mqxliff', MQXLIFF)
    import modules.okapi_sidecar as osc

    class GoodSidecar(FakeSidecar):
        def __init__(self):
            super().__init__([{'source': 'Dit is vet en klaar.', 'sourceWithTags': 'Dit is vet en klaar.'}])
    monkeypatch.setattr(osc, 'OkapiSidecar', GoodSidecar)
    assert fc.main([str(tmp_path)]) == 0
    page = (tmp_path / 'filter-compare-report.html').read_text(encoding='utf-8')
    assert 'Bold that memoQ keeps as a tag but Workbench drops' in page
