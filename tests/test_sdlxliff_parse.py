"""SDLXLIFF parsing (issue #185).

The parser used to serialise every segment's source and target XML with
ElementTree while importing – a quarter of the parse time, for values nothing
reads during an import. They are now serialised the first time they are read.
These tests pin the parsed values and that deferral.
"""

import os
import sys
from xml.etree import ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules import sdlppx_handler as sdl

SDLXLIFF = """<?xml version="1.0" encoding="utf-8"?>
<xliff xmlns:sdl="http://sdl.com/FileTypes/SdlXliff/1.0" xmlns="urn:oasis:names:tc:xliff:document:1.2" version="1.2">
<file original="C:\\docs\\guide.md" source-language="en-US" target-language="nl-NL">
<header/>
<body><group>
<trans-unit id="tu1">
<source>Press <g id="5">Save</g> now.<x id="6"/> Done.</source>
<seg-source><mrk mtype="seg" mid="1">Press <g id="5">Save</g> now.<x id="6"/></mrk> <mrk mtype="seg" mid="2">Done.</mrk></seg-source>
<target><mrk mtype="seg" mid="1">Druk nu op <g id="5">Opslaan</g>.<x id="6"/></mrk> <mrk mtype="seg" mid="2"/></target>
<sdl:seg-defs><sdl:seg id="1" conf="Translated" origin="tm" percent="100" text-match="SourceAndTarget"/><sdl:seg id="2"/></sdl:seg-defs>
</trans-unit>
<trans-unit id="tu2" translate="no"><source>Code</source><target>Code</target></trans-unit>
</group></body></file></xliff>
"""


def _parse(tmp_path, parser=None):
    path = tmp_path / "guide.md.sdlxliff"
    path.write_text(SDLXLIFF, encoding="utf-8")
    parser = parser or sdl.SDLXLIFFParser(log_callback=lambda _m: None)
    return parser.parse_file(str(path))


def _inner_xml(elem):
    return (elem.text or "") + "".join(ET.tostring(c, encoding="unicode") for c in elem)


def test_segments_text_tags_and_status(tmp_path):
    segs = _parse(tmp_path).segments
    assert [s.segment_id for s in segs] == ["tu1_1", "tu1_2", "tu2"]

    first, second, locked = segs
    assert first.source_text == "Press <5>Save</5> now.<6/>"
    assert first.target_text == "Druk nu op <5>Opslaan</5>.<6/>"
    assert (first.status, first.match_percent, first.origin, first.text_match) == (
        "translated", 100, "tm", "SourceAndTarget")
    assert second.source_text == "Done." and second.target_text == ""
    assert second.status == "not_translated"
    assert locked.locked and locked.target_text == "Code"


def test_xml_matches_the_elements(tmp_path):
    xliff = _parse(tmp_path)
    ns = sdl.NAMESPACES
    tu = xliff.root.find(".//xliff:trans-unit", ns)
    src_mrk = tu.find("xliff:seg-source/xliff:mrk", ns)
    tgt_mrk = tu.find("xliff:target/xliff:mrk", ns)
    first = xliff.segments[0]
    assert first.source_xml == _inner_xml(src_mrk)
    assert first.target_xml == _inner_xml(tgt_mrk)
    assert "<g " in first.source_xml and "Save</g>" in first.source_xml
    locked = xliff.segments[2]
    assert locked.source_xml.startswith("<source") and "Code" in locked.source_xml
    assert xliff.segments[1].target_xml == ""


def test_xml_is_serialised_only_when_read(tmp_path):
    calls = []

    class CountingParser(sdl.SDLXLIFFParser):
        def _element_inner_xml(self, elem):
            calls.append(elem)
            return super()._element_inner_xml(elem)

    xliff = _parse(tmp_path, CountingParser(log_callback=lambda _m: None))
    assert calls == []                       # nothing serialised during the import
    first = xliff.segments[0]
    value = first.source_xml
    assert len(calls) == 1
    assert first.source_xml == value
    assert len(calls) == 1                   # kept after the first read


def test_plain_strings_and_assignment_still_work():
    seg = sdl.SDLSegment(segment_id="1", trans_unit_id="1", source_text="a", target_text="",
                         source_xml="<x/>", target_xml="", status="draft")
    assert seg.source_xml == "<x/>" and seg.target_xml == ""
    seg.target_xml = "<g/>"
    assert seg.target_xml == "<g/>"
    seg.source_xml = lambda: "<late/>"
    assert seg.source_xml == "<late/>"
    assert "source_xml='<late/>'" in repr(seg)
