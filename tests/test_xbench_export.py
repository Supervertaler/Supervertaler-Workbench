"""QA → Open in Xbench (issue #146): the XLIFF, key terms and .xbp it writes."""

import os
import sys
from types import SimpleNamespace as Seg
from xml.etree import ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules import language_codes as lc
from modules import xbench_export as xb

NS = {"x": "urn:oasis:names:tc:xliff:document:1.2"}


def _segments():
    return [
        Seg(id=1, source="Press <b>Start</b>.", target="Druk op <b>Start</b>.", status="confirmed"),
        Seg(id=2, source="Open the valve & wait", target="", status="not_started"),
        Seg(id=3, source="Approved one", target="Goedgekeurd", status="approved"),
        Seg(id=4, source="Draft one", target="Concept", status="pretranslated"),
        Seg(id=5, source="  ", target="", status="not_started"),              # empty: left out
        Seg(id=6, source="Locked\x0b text", target="Vergrendeld", status="confirmed", locked=True),
    ]


def test_xliff_units_states_and_escaping():
    root = ET.fromstring(xb.build_xliff(_segments(), "en", "nl", "Guide.docx"))
    f = root.find("x:file", NS)
    assert (f.get("source-language"), f.get("target-language"), f.get("original")) == ("en", "nl", "Guide.docx")
    units = {u.get("id"): u for u in root.iter("{%s}trans-unit" % NS["x"])}
    assert sorted(units) == ["1", "2", "3", "4", "6"]
    assert units["1"].find("x:source", NS).text == "Press <b>Start</b>."   # tags kept as text
    state = {i: u.find("x:target", NS).get("state") for i, u in units.items()}
    assert state == {"1": "translated", "2": "needs-translation", "3": "signed-off",
                     "4": "needs-review-translation", "6": "translated"}
    assert units["3"].get("approved") == "yes" and units["1"].get("approved") is None
    assert units["6"].get("translate") == "no"
    assert units["6"].find("x:source", NS).text == "Locked text"            # control character dropped
    assert units["2"].find("x:source", NS).text == "Open the valve & wait"


def test_key_terms_file_format():
    text = xb.build_key_terms([("valve", "klep"), ("valve", "klep"), ("pump\thousing", "pomp\nhuis"),
                               ("", "leeg"), ("empty", "")])
    assert text == "valve\tklep\r\npump housing\tpomp huis\r\n"
    assert xb.build_key_terms([]) == ""


def test_xbp_lists_the_files_as_xbench_expects():
    xbp = ET.fromstring(xb.build_xbp("Job.xbp", [
        {"filename": "Job.xlf", "type": xb.XBP_TYPE_XLIFF, "newtranslations": True},
        {"filename": "Job - key terms.txt", "type": xb.XBP_TYPE_TAB_DELIMITED, "keyterms": True, "level": 3},
    ]))
    assert xbp.tag == "xbench"
    glossaries = xbp.findall("project/glossarylist/glossary")
    assert [(g.get("type"), g.findtext("ident"), g.findtext("filename"), g.findtext("level"),
             g.findtext("keyterms"), g.findtext("newtranslations")) for g in glossaries] == [
        ("13", "0", "Job.xlf", "1", "0", "1"),
        ("1", "1", "Job - key terms.txt", "3", "1", "0"),
    ]


def test_terms_follow_the_project_direction():
    terms = [{"source_term": "klep", "target_term": "valve"},
             {"source_term": "verboden", "target_term": "forbidden", "forbidden": 1},
             {"source_term": "Supervertaler", "target_term": "", "is_nontranslatable": True}]
    # A Dutch → English glossary in an English → Dutch project is flipped
    assert xb.oriented_terms(terms, "nl", "English", lc.same_language) == [
        ("valve", "klep"), ("Supervertaler", "Supervertaler")]
    assert xb.oriented_terms(terms, "Dutch", "nl", lc.same_language) == [
        ("klep", "valve"), ("Supervertaler", "Supervertaler")]


def test_write_project_files(tmp_path):
    folder = str(tmp_path / "qa" / "xbench")
    result = xb.write_xbench_project(folder, "Job: 12/3", _segments(), "en", "nl",
                                     [("valve", "klep")])
    assert os.path.basename(result["xbp"]) == "Job_ 12_3.xbp"                # safe file name
    assert result["segments"] == 5 and result["terms"] == 1
    with open(result["key_terms"], "rb") as f:
        assert f.read().startswith(b"\xef\xbb\xbfvalve\tklep")
    xbp = ET.parse(result["xbp"]).getroot()
    names = [g.findtext("filename") for g in xbp.findall("project/glossarylist/glossary")]
    assert all(os.path.exists(os.path.join(folder, n)) for n in names)  # relative to the .xbp

    # Without glossary terms only the XLIFF is listed
    result = xb.write_xbench_project(str(tmp_path / "other"), "Job", _segments(), "en", "nl", [])
    assert result["key_terms"] is None
    assert len(ET.parse(result["xbp"]).getroot().findall("project/glossarylist/glossary")) == 1
