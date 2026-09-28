"""Post-export word-count safeguard for every Okapi output format (issue #219).

Each counter must find all the words a clean export contains (so a clean
round-trip is never flagged) and fewer when text has been dropped. Counting too
much is harmless; counting too little would raise false alarms, so the
generous cases (HTML attributes, untranslated XLIFF/PO units) are pinned too.
"""

from __future__ import annotations

import os
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from modules.export_word_count import count_exported_words, count_words, supported


def _zip(path, parts):
    with zipfile.ZipFile(path, 'w') as z:
        for name, data in parts.items():
            z.writestr(name, data)
    return str(path)


def test_count_words_ignores_tags_and_placeholders():
    assert count_words("The <b>quick</b> {1}fox[2} jumps") == 4
    assert count_words("") == 0


def test_docx(tmp_path):
    docx = pytest.importorskip("docx")
    d = docx.Document()
    d.add_paragraph("Het pomphuis is van staal.")
    d.sections[0].header.paragraphs[0].text = "Koptekst hier"
    path = tmp_path / "out.docx"
    d.save(path)
    assert count_exported_words(str(path)) == 7


def test_pptx_counts_slides_notes_and_masters(tmp_path):
    a = '<p:sld xmlns:a="x"><a:t>Drie woorden hier</a:t><a:t>en twee</a:t></p:sld>'
    path = _zip(tmp_path / "out.pptx", {
        "ppt/slides/slide1.xml": a,
        "ppt/notesSlides/notesSlide1.xml": '<a:t>notitie</a:t>',
        "ppt/slideMasters/slideMaster1.xml": '<a:t>Titel &amp; stijl</a:t>',
        "docProps/core.xml": '<a:t>not counted</a:t>',
    })
    assert count_exported_words(path) == 5 + 1 + 3


def test_xlsx_real_workbook(tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    wb = openpyxl.Workbook()
    ws = wb.active
    ws["A1"] = "Eerste cel tekst"
    ws["A2"] = "Tweede"
    ws["B1"] = 1234  # numbers are not text units
    path = tmp_path / "out.xlsx"
    wb.save(path)
    assert count_exported_words(str(path)) == 4


def test_idml_stories(tmp_path):
    path = _zip(tmp_path / "out.idml", {
        "Stories/Story_u1.xml": '<Story><Content>Een verhaal</Content><Content>met tekst</Content></Story>',
        "Stories/Story_u2.xml": '<Story><Content>Nog één</Content></Story>',
        "Spreads/Spread_1.xml": '<Content>not a story</Content>',
    })
    assert count_exported_words(path) == 6


def test_html_counts_text_and_translated_attributes(tmp_path):
    path = tmp_path / "out.html"
    path.write_text(
        '<html><head><title>Mijn pagina</title><style>p { color: red }</style>'
        '<script>var x = "not text";</script></head>'
        '<body><!-- hidden comment --><p>Hallo&nbsp;wereld &amp; meer</p>'
        '<img src="a.png" alt="Een pomp"><a title="Klik hier">link</a></body></html>',
        encoding='utf-8')
    # Mijn pagina | Hallo wereld & meer | link  +  alt (2) + title (2)
    assert count_exported_words(str(path)) == 2 + 4 + 1 + 2 + 2


def test_xliff_12_uses_source_for_untranslated_units(tmp_path):
    path = tmp_path / "out.xlf"
    path.write_text(
        '<xliff version="1.2" xmlns="urn:oasis:names:tc:xliff:document:1.2"><file><body>'
        '<trans-unit id="1"><source>Two words</source><target>Twee <g id="1">woorden</g></target></trans-unit>'
        '<trans-unit id="2"><source>Not translated yet</source><target/></trans-unit>'
        '<trans-unit id="3"><source>No target element</source></trans-unit>'
        '</body></file></xliff>', encoding='utf-8')
    assert count_exported_words(str(path)) == 2 + 3 + 3


def test_xliff_20_segments(tmp_path):
    path = tmp_path / "out.xliff"
    path.write_text(
        '<xliff version="2.0" xmlns="urn:oasis:names:tc:xliff:document:2.0"><file id="f">'
        '<unit id="u"><segment><source>A b c</source><target>X y</target></segment>'
        '<segment><source>Left alone</source></segment></unit></file></xliff>', encoding='utf-8')
    assert count_exported_words(str(path)) == 2 + 2


def test_po_with_plurals_and_untranslated(tmp_path):
    path = tmp_path / "out.po"
    path.write_text(
        'msgid ""\nmsgstr ""\n"Content-Type: text/plain; charset=UTF-8\\n"\n\n'
        'msgid "Save file"\nmsgstr "Bestand opslaan"\n\n'
        'msgid "Not yet translated"\nmsgstr ""\n\n'
        'msgid "One file"\nmsgid_plural "%d files"\nmsgstr[0] "Eén bestand"\nmsgstr[1] "%d bestanden"\n',
        encoding='utf-8')
    assert count_exported_words(str(path)) == 2 + 3 + 2 + 2


def test_dropped_text_gives_a_lower_count(tmp_path):
    full = _zip(tmp_path / "full.pptx", {"ppt/slides/slide1.xml": "<a:t>een twee drie vier</a:t>"})
    short = _zip(tmp_path / "short.pptx", {"ppt/slides/slide1.xml": "<a:t>een</a:t>"})
    assert count_exported_words(short) < count_exported_words(full)


def test_unknown_formats_are_skipped(tmp_path):
    path = tmp_path / "out.rtf"
    path.write_text("{\\rtf1 hello}")
    assert count_exported_words(str(path)) is None
    assert not supported(str(path))
    assert count_exported_words("") is None


def test_unreadable_supported_file_raises(tmp_path):
    path = tmp_path / "broken.pptx"
    path.write_bytes(b"not a zip")
    with pytest.raises(Exception):
        count_exported_words(str(path))
