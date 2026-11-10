"""Phrase (Memsource) MXLIFF import/export.

The synthetic files mimic what Phrase TMS writes: a single-quoted XML
declaration, the m: namespace (mxlf/2.0) with its attributes on every
trans-unit, a large CDATA preview skeleton in the header, <group> wrappers,
<alt-trans> MT/TM suggestions, <m:latest-trans-props>, and inline formatting
as text tags ({b>...<b}, {1}).
"""

import os
import sys
import time

import pytest
from lxml import etree

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.mxliff_handler import MxliffDocument, MxliffError, looks_like_mxliff

M_NS = "http://www.memsource.com/mxlf/2.0"

HEAD = ("<?xml version='1.0' encoding='UTF-8'?>"
        '<xliff xmlns="urn:oasis:names:tc:xliff:document:1.2" xmlns:m="{ns}" '
        'version="1.2" m:version="2.17" m:level="1">\n'
        '<file original="Acme_PROJ-001.docx" source-language="en" target-language="nl-nl" '
        'datatype="x-undefined" m:file-format="DOC" m:task-id="TASK001" '
        'm:job-uid="JOB001">\n'
        '<header>\n<m:in-ctx-preview-skel bilingual="false">\n'
        '<![CDATA[<html><body><p>decoy <trans-unit id="fake"> in the skeleton</p>]]>'
        '<![CDATA[<p>split ]]><![CDATA[CDATA</p></body></html>]]>\n'
        '</m:in-ctx-preview-skel>\n</header>\n<body>\n')
TAIL = ('</body>\n</file>\n<m:extra>\n<m:users>\n'
        '<m:user id="1" uid="U1" username="acme.pm" fullname="Acme PM"/>\n'
        '</m:users>\n</m:extra>\n</xliff>')


def unit(i, source, target=None, confirmed="0", locked="false", origin="tm",
         score="1.0", alt_target=True):
    attrs = (f'id="TASK001:{i}" xml:space="preserve" m:score="{score}" '
             f'm:gross-score="{score}" m:trans-origin="{origin}" '
             f'm:trans-origin-detail="TM1" m:confirmed="{confirmed}" m:locked="{locked}" '
             f'm:para-id="{i}" m:created-at="1794309129837" m:created-by="1" '
             f'm:modified-at="1794309129837" m:modified-by="1" m:level-edited="false"')
    if target is None:
        tgt = ""
    elif target == "":
        tgt = "<target/>\n"
    else:
        tgt = f"<target>{target}</target>\n"
    alt = ('<alt-trans origin="machine-trans" match-quality="0.9" m:mt-id="5">'
           '<target>MT suggestie</target></alt-trans>\n') if alt_target else ''
    return (f'<group id="{i}" m:para-id="{i}">\n<context-group>\n'
            f'<context context-type="x-file-part">word/document.xml</context>\n'
            f'</context-group>\n<trans-unit {attrs}>\n<source>{source}</source>\n{tgt}'
            f'{alt}<alt-trans origin="memsource-tm"><target/></alt-trans>\n'
            f'<m:latest-trans-props>\n  <m:trans-prop name="origin">{origin}</m:trans-prop>\n'
            f'</m:latest-trans-props>\n</trans-unit>\n</group>\n')


UNITS = [
    unit(0, "Acme Widget Manual", "Acme Widget-handleiding", confirmed="1"),
    unit(1, "{b&gt;Warning&lt;b}: read PROJ-001 first.", "{b&gt;Waarschuwing&lt;b}: lees eerst PROJ-001.",
         origin="mt", score="0.93"),
    unit(2, "Do not open the housing.", "", origin="null", score="0.0"),
    unit(3, "Copyright Acme &amp; Co.", "Copyright Acme &amp; Co.", locked="true", score="1.01"),
    unit(4, "Press {1} to start.", None, origin="null", score="0.0", alt_target=False),
]


def make(units=UNITS, ns=M_NS):
    return (HEAD.format(ns=ns) + "".join(units) + TAIL).encode("utf-8")


@pytest.fixture
def sample(tmp_path):
    path = tmp_path / "Acme_PROJ-001-en-nl.mxliff"
    path.write_bytes(make())
    return path


def _all(doc, attr):
    return [getattr(u, attr) for u in doc.units]


# ── import ──────────────────────────────────────────────────────────────────

def test_import_reads_segments_translations_status_and_tags(sample):
    doc = MxliffDocument.load(str(sample))
    assert (doc.source_lang, doc.target_lang) == ("en", "nl-nl")
    assert doc.warnings == []
    assert _all(doc, "id") == [f"TASK001:{i}" for i in range(5)]
    assert doc.units[1].source == "{b>Warning<b}: read PROJ-001 first."   # tags kept as text
    assert doc.units[1].target == "{b>Waarschuwing<b}: lees eerst PROJ-001."
    assert doc.units[3].source == "Copyright Acme & Co."
    assert _all(doc, "status") == ["confirmed", "machine_translated", "not_started",
                                   "pretranslated", "not_started"]
    assert _all(doc, "locked") == [False, False, False, True, False]
    assert _all(doc, "match_percent") == [100, 93, 0, 101, 0]
    # the decoy <trans-unit> inside the CDATA skeleton is not a segment
    assert len(doc.units) == 5
    assert looks_like_mxliff(str(sample))


# ── round trip ──────────────────────────────────────────────────────────────

def test_unchanged_export_is_byte_identical(sample, tmp_path):
    doc = MxliffDocument.load(str(sample))
    out = tmp_path / "out.mxliff"
    changed = doc.save(str(out), _all(doc, "target"), _all(doc, "status"),
                       sources=_all(doc, "source"))
    assert changed == 0
    assert out.read_bytes() == sample.read_bytes()


def test_translations_and_confirmed_flag_are_written(sample, tmp_path):
    doc = MxliffDocument.load(str(sample))
    targets = _all(doc, "target")
    statuses = _all(doc, "status")
    targets[1] = "{b>Let op<b}: lees eerst PROJ-001 & <bijlage>."
    statuses[1] = "confirmed"
    targets[2] = "Open de behuizing niet."                    # <target/> becomes filled
    statuses[2] = "draft"
    targets[4] = "Druk op {1} om te starten."                 # no <target> at all yet
    statuses[4] = "confirmed"
    statuses[0] = "draft"                                     # un-confirm, text unchanged
    targets[3] = "Changed but locked"                         # locked: must not be written
    out = tmp_path / "out.mxliff"
    assert doc.save(str(out), targets, statuses, sources=_all(doc, "source")) == 4

    again = MxliffDocument.load(str(out))
    assert again.units[1].target == "{b>Let op<b}: lees eerst PROJ-001 & <bijlage>."
    assert again.units[2].target == "Open de behuizing niet."
    assert again.units[4].target == "Druk op {1} om te starten."
    assert _all(again, "confirmed") == [False, True, False, False, True]
    assert again.units[3].target == "Copyright Acme & Co."

    # Phrase-specific data is untouched: alt-trans suggestions, latest-trans-props,
    # every m: attribute other than m:confirmed, the CDATA skeleton, m:extra
    root = etree.fromstring(out.read_bytes())
    tus = root.findall(".//{urn:oasis:names:tc:xliff:document:1.2}trans-unit")
    orig = etree.fromstring(sample.read_bytes()).findall(
        ".//{urn:oasis:names:tc:xliff:document:1.2}trans-unit")
    for a, b in zip(orig, tus):
        a_attrs = {k: v for k, v in a.attrib.items() if k != f"{{{M_NS}}}confirmed"}
        b_attrs = {k: v for k, v in b.attrib.items() if k != f"{{{M_NS}}}confirmed"}
        assert a_attrs == b_attrs
        assert list(a.attrib) == list(b.attrib)               # attribute order too
    text = out.read_text(encoding="utf-8")
    assert "<![CDATA[<p>split ]]><![CDATA[CDATA</p></body></html>]]>" in text
    assert text.count("MT suggestie") == 4
    assert '<m:user id="1" uid="U1"' in text
    assert text.startswith("<?xml version='1.0' encoding='UTF-8'?>")
    # the target goes right after <source>, before the <alt-trans>
    assert "</source>\n<target>Druk op {1} om te starten.</target>" in text


def test_empty_translation_keeps_existing_target(sample, tmp_path):
    doc = MxliffDocument.load(str(sample))
    targets = _all(doc, "target")
    targets[0] = ""
    out = tmp_path / "out.mxliff"
    assert doc.save(str(out), targets, _all(doc, "status")) == 0
    assert out.read_bytes() == sample.read_bytes()


def test_illegal_xml_characters_are_dropped(sample, tmp_path):
    doc = MxliffDocument.load(str(sample))
    targets = _all(doc, "target")
    targets[2] = "Open\x0b de\x00 behuizing niet."
    out = tmp_path / "out.mxliff"
    doc.save(str(out), targets, _all(doc, "status"))
    assert MxliffDocument.load(str(out)).units[2].target == "Open de behuizing niet."


# ── failure modes ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("content, message", [
    (b"", "empty"),
    (b"<xliff><file>", "not well-formed"),
    (b"<?xml version='1.0'?><html/>", "not an XLIFF 1.2"),
    (b'<xliff xmlns="urn:oasis:names:tc:xliff:document:1.2" version="1.2"><file/></xliff>',
     r"no Phrase \(Memsource\) namespace"),
    (b'<xliff xmlns="urn:oasis:names:tc:xliff:document:1.2" xmlns:m="' + M_NS.encode()
     + b'" version="1.2"><file><body/></file></xliff>', "No segments"),
])
def test_bad_files_are_refused_with_a_reason(tmp_path, content, message):
    path = tmp_path / "bad.mxliff"
    path.write_bytes(content)
    with pytest.raises(MxliffError, match=message):
        MxliffDocument.load(str(path))


def test_missing_file_is_refused(tmp_path):
    with pytest.raises(MxliffError, match="Could not read"):
        MxliffDocument.load(str(tmp_path / "nope.mxliff"))


def test_external_entities_are_not_resolved(tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("TOP SECRET", encoding="utf-8")
    xxe = make().replace(b"<?xml version='1.0' encoding='UTF-8'?>",
                         b"<?xml version='1.0' encoding='UTF-8'?><!DOCTYPE x [<!ENTITY e SYSTEM '"
                         + secret.as_uri().encode() + b"'>]>")
    xxe = xxe.replace(b"Acme Widget Manual", b"&e;")
    doc = MxliffDocument.from_bytes(xxe)
    assert "TOP SECRET" not in doc.units[0].source


def test_unknown_namespace_version_imports_with_a_warning(tmp_path):
    path = tmp_path / "v3.mxliff"
    path.write_bytes(make(ns="http://www.memsource.com/mxlf/3.0"))
    doc = MxliffDocument.load(str(path))
    assert len(doc.units) == 5
    assert any("mxlf/3.0" in w for w in doc.warnings)
    targets = [u.target for u in doc.units]
    targets[2] = "Open de behuizing niet."
    out = tmp_path / "out.mxliff"
    doc.save(str(out), targets, [u.status for u in doc.units])
    assert MxliffDocument.load(str(out)).units[2].target == "Open de behuizing niet."


def test_other_namespace_prefix_is_respected(tmp_path):
    raw = make().replace(b"xmlns:m=", b"xmlns:ms=").replace(b" m:", b" ms:") \
                .replace(b"<m:", b"<ms:").replace(b"</m:", b"</ms:")
    path = tmp_path / "prefix.mxliff"
    path.write_bytes(raw)
    doc = MxliffDocument.load(str(path))
    statuses = [u.status for u in doc.units]
    statuses[1] = "confirmed"
    out = tmp_path / "out.mxliff"
    doc.save(str(out), [u.target for u in doc.units], statuses)
    assert MxliffDocument.load(str(out)).units[1].confirmed
    assert b' m:confirmed' not in out.read_bytes()


def test_segment_count_mismatch_refuses_and_writes_nothing(sample, tmp_path):
    doc = MxliffDocument.load(str(sample))
    out = tmp_path / "out.mxliff"
    with pytest.raises(MxliffError, match="5"):
        doc.save(str(out), ["x"] * 4, ["draft"] * 4)
    assert not out.exists()
    assert os.listdir(tmp_path) == [sample.name]   # no temp file left behind


def test_source_mismatch_refuses(sample, tmp_path):
    doc = MxliffDocument.load(str(sample))
    sources = _all(doc, "source")
    sources[2] = "Something else entirely"
    with pytest.raises(MxliffError, match="Segment 3"):
        doc.save(str(tmp_path / "out.mxliff"), _all(doc, "target"), _all(doc, "status"),
                 sources=sources)


def test_failed_write_keeps_existing_output_and_leaves_no_temp(sample, tmp_path, monkeypatch):
    doc = MxliffDocument.load(str(sample))
    out = tmp_path / "out.mxliff"
    out.write_bytes(b"previous good export")

    def boom(src, dst):
        raise OSError("disk full")
    monkeypatch.setattr(os, "replace", boom)
    targets = _all(doc, "target")
    targets[2] = "Nieuw"
    with pytest.raises(OSError):
        doc.save(str(out), targets, _all(doc, "status"))
    assert out.read_bytes() == b"previous good export"
    assert sorted(os.listdir(tmp_path)) == sorted([sample.name, "out.mxliff"])


def test_units_with_xliff_inline_elements_are_locked_and_untouched(tmp_path):
    odd = unit(9, 'Click <ph id="1">&lt;b&gt;</ph>here', 'Klik <ph id="1">&lt;b&gt;</ph>hier')
    path = tmp_path / "inline.mxliff"
    path.write_bytes(make(UNITS + [odd]))
    doc = MxliffDocument.load(str(path))
    assert doc.units[5].locked and not doc.units[5].editable
    assert any("inline markup" in w for w in doc.warnings)
    targets = _all(doc, "target")
    targets[5] = "overwritten"
    out = tmp_path / "out.mxliff"
    assert doc.save(str(out), targets, _all(doc, "status")) == 0
    assert out.read_bytes() == path.read_bytes()


# ── scale ───────────────────────────────────────────────────────────────────

def test_twenty_thousand_segments_load_and_export_quickly(tmp_path):
    n = 20_000
    units = [unit(i, f"Acme sentence {i} for PROJ-001 with {{b&gt;bold&lt;b}} text.",
                  f"Acme zin {i}" if i % 2 else "", origin="mt" if i % 3 else "tm")
             for i in range(n)]
    path = tmp_path / "big.mxliff"
    path.write_bytes(make(units))

    t0 = time.perf_counter()
    doc = MxliffDocument.load(str(path))
    load_s = time.perf_counter() - t0
    assert len(doc.units) == n

    targets = [f"Vertaald {i}" for i in range(n)]
    statuses = ["confirmed"] * n
    t0 = time.perf_counter()
    out = tmp_path / "big_out.mxliff"
    assert doc.save(str(out), targets, statuses, sources=_all(doc, "source")) == n
    save_s = time.perf_counter() - t0
    # Generous bounds for a slow CI box; locally both take well under a second.
    assert load_s < 10 and save_s < 10, (load_s, save_s)
    again = MxliffDocument.load(str(out))
    assert again.units[-1].target == f"Vertaald {n - 1}" and again.units[-1].confirmed
