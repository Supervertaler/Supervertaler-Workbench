"""memoQ XLIFF import/export (issue #110).

A memoQ *view* exported as MQXLIFF holds one <file> per document; only the
first used to be read. Inline codes leaked their native content into the
segment text, and the export put translations back by string replacement,
which lost them whenever the text was split by a code.
"""

import os
import sys
from xml.etree import ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules import mqxliff_handler as mq

NS = {"x": mq.XLIFF_NS}

VIEW = """<?xml version="1.0" encoding="utf-8"?>
<xliff xmlns="urn:oasis:names:tc:xliff:document:1.2" xmlns:mq="MQXliff" version="1.2">
<file original="Manual.docx" source-language="en-gb" target-language="nl-nl" datatype="x-docx">
<body>
<trans-unit id="1" mq:status="NotStarted"><source xml:space="preserve">Press <bpt id="1" ctype="bold">{}</bpt>Start<ept id="1">{}</ept> now.</source><target xml:space="preserve"></target></trans-unit>
<trans-unit id="2" mq:status="ManuallyConfirmed"><source xml:space="preserve">See figure</source><target xml:space="preserve">Zie figuur</target></trans-unit>
<trans-unit id="3" mq:nosplitjoin="true"><source xml:space="preserve">https://example.com</source><target xml:space="preserve"></target></trans-unit>
<trans-unit id="4" mq:status="PartiallyEdited" mq:locked="locked"><source xml:space="preserve">(<ph id="1">&lt;x id="1164" mq:catalogvalue="&amp;lt;xr id=&amp;quot;u18dc7&amp;quot;/&amp;gt;" mq:shortcatalogvalue="xr" /&gt;</ph>).</source><target xml:space="preserve">(<ph id="1">&lt;x id="1164" mq:catalogvalue="&amp;lt;xr id=&amp;quot;u18dc7&amp;quot;/&amp;gt;" mq:shortcatalogvalue="xr" /&gt;</ph>).</target></trans-unit>
</body>
</file>
<file original="Guide.docx" source-language="en-gb" target-language="nl-nl" datatype="x-docx">
<body>
<trans-unit id="1" mq:status="Pretranslated" mq:percent="100"><source xml:space="preserve">Close the valve.</source><target xml:space="preserve">Sluit de klep.</target></trans-unit>
<trans-unit id="2" mq:status="Reviewer1Confirmed"><source xml:space="preserve"><bpt id="1" ctype="italic">{}</bpt>Warning<ept id="1">{}</ept></source><target xml:space="preserve"><bpt id="1" ctype="italic">{}</bpt>Waarschuwing<ept id="1">{}</ept></target></trans-unit>
<trans-unit id="3" mq:status="NotStarted"><source xml:space="preserve"><ph id="1">&lt;mq:ch val="&amp;#9;" /&gt;</ph>Tighten the bolt.</source><target xml:space="preserve"></target></trans-unit>
</body>
</file>
</xliff>
"""


def _load(tmp_path, text=VIEW):
    path = tmp_path / "view.mqxliff"
    path.write_text(text, encoding="utf-8")
    handler = mq.MQXLIFFHandler()
    assert handler.load(str(path))
    return handler


def _units(path):
    return ET.parse(path).getroot().findall(".//x:trans-unit", NS)


def test_every_document_of_a_view_is_read(tmp_path):
    h = _load(tmp_path)
    segs = h.extract_bilingual_segments()
    assert [s["file"] for s in segs] == ["Manual.docx"] * 3 + ["Guide.docx"] * 3
    assert h.get_segment_count() == 6
    assert (h.source_lang, h.target_lang) == ("en-gb", "nl-nl")
    assert len(h.extract_source_segments()) == 6


def test_inline_codes_become_tags(tmp_path):
    segs = _load(tmp_path).extract_bilingual_segments()
    assert segs[0]["source"] == "Press <1>Start</1> now."
    assert segs[2]["source"] == "(<1/>)."                  # no leaked <x id="1164" …>
    assert segs[2]["target"] == "(<1/>)."
    assert segs[4]["target"] == "<1>Waarschuwing</1>"
    assert segs[5]["source"] == "<1/>Tighten the bolt."
    assert _load(tmp_path).extract_source_segments()[0].plain_text == "Press Start now."


def test_statuses_and_locks(tmp_path):
    segs = _load(tmp_path).extract_bilingual_segments()
    assert [s["status"] for s in segs] == [
        "not_started", "confirmed", "draft", "pretranslated", "proofread", "not_started"]
    assert segs[3]["match_percent"] == 100
    assert [s["locked"] for s in segs] == [False, False, True, False, False, False]


def test_export_rebuilds_codes_from_tags(tmp_path):
    h = _load(tmp_path)
    segs = h.extract_bilingual_segments()
    translations = [s["target"] for s in segs]
    statuses = [s["status"] for s in segs]
    translations[0] = "Druk nu op <1>Start</1>."
    statuses[0] = "confirmed"
    translations[5] = "<1/>Draai de bout vast."
    statuses[5] = "draft"
    assert h.update_target_segments(translations, statuses) == 2   # the rest is unchanged
    out = tmp_path / "out.mqxliff"
    assert h.save(str(out))

    units = _units(out)
    first = units[0]
    assert first.get("{MQXliff}status") == "ManuallyConfirmed"
    target = first.find("x:target", NS)
    assert ET.tostring(target, encoding="unicode").count("bpt") == 2
    assert mq.tagged_content(target)[0] == "Druk nu op <1>Start</1>."
    assert target.find("x:bpt", NS).get("ctype") == "bold"
    assert target.find("x:bpt", NS).text == "{}"
    # memoQ's own statuses stay on the units that weren't changed (units[2]
    # is the hyperlink unit, which isn't a segment)
    assert units[4].get("{MQXliff}status") == "Pretranslated"
    assert units[5].get("{MQXliff}status") == "Reviewer1Confirmed"
    last = units[6]
    assert last.get("{MQXliff}status") == "PartiallyEdited"
    assert mq.tagged_content(last.find("x:target", NS))[0] == "<1/>Draai de bout vast."
    assert "mq:ch" in last.find("x:target/x:ph", NS).text
    # and it reads back the same
    again = mq.MQXLIFFHandler()
    assert again.load(str(out))
    assert [s["target"] for s in again.extract_bilingual_segments()] == translations


def test_empty_translations_leave_the_unit_alone(tmp_path):
    h = _load(tmp_path)
    assert h.update_target_segments([""] * 6, ["confirmed"] * 6) == 0
    out = tmp_path / "out.mqxliff"
    h.save(str(out))
    assert _units(out)[0].get("{MQXliff}status") == "NotStarted"


def test_fill_target_edge_cases():
    source = ET.fromstring(
        '<source xmlns="urn:oasis:names:tc:xliff:document:1.2">A <bpt id="1">{}</bpt>b<ept id="1">{}</ept>'
        ' <ph id="2">&lt;tab/&gt;</ph> c</source>')

    def fill(translation):
        target = ET.fromstring('<target xmlns="urn:oasis:names:tc:xliff:document:1.2">old</target>')
        mq.fill_target(source, target, translation)
        return mq.tagged_content(target)[0]

    assert fill("X <1>y</1> <2/> z") == "X <1>y</1> <2/> z"
    assert fill("X <1>y z") == "X <1>y z</1>"          # missing closing code is added at the end
    assert fill("X <7>y</7> <2/><2/>") == "X y <1/>"    # unknown tag dropped, a code used once
    assert fill("no tags at all") == "no tags at all"   # text split by codes: plain text

    bold = ET.fromstring('<source xmlns="urn:oasis:names:tc:xliff:document:1.2">'
                         '<bpt id="1">{}</bpt>Warning<ept id="1">{}</ept></source>')
    target = ET.fromstring('<target xmlns="urn:oasis:names:tc:xliff:document:1.2"/>')
    mq.fill_target(bold, target, "Waarschuwing")        # all text in one place: codes kept
    assert mq.tagged_content(target)[0] == "<1>Waarschuwing</1>"


def test_g_containers_round_trip():
    source = ET.fromstring('<source xmlns="urn:oasis:names:tc:xliff:document:1.2">'
                           'Open <g id="5" ctype="link">the <x id="6"/>page</g>.</source>')
    tagged, table = mq.tagged_content(source)
    assert tagged == "Open <1>the <2/>page</1>."
    assert table[1]["kind"] == "container"
    target = ET.fromstring('<target xmlns="urn:oasis:names:tc:xliff:document:1.2"/>')
    mq.fill_target(source, target, "Open <1>de <2/>pagina</1>.")
    g = target.find("x:g", NS)
    assert g.get("ctype") == "link" and g.find("x:x", NS).get("id") == "6"
    assert mq.tagged_content(target)[0] == "Open <1>de <2/>pagina</1>."
