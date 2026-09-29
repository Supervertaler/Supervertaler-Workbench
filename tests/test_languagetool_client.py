"""LanguageTool checking of target segments (issue #233).

The server is faked: it flags every "de de" and "teh", reporting offsets in the
joined request text exactly as LanguageTool does, so the mapping back to
segments is what's tested.
"""

from __future__ import annotations

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from modules.languagetool_client import (
    PUBLIC_API_URL, LTFinding, apply_replacement, build_batches, check_texts, lt_language,
)


def fake_server(calls):
    def request(base_url, text, language, **kw):
        calls.append((base_url, text, language))
        matches = []
        for m in re.finditer(r"\bde de\b|\bteh\b", text):
            matches.append({"offset": m.start(), "length": m.end() - m.start(),
                            "message": f"Check “{m.group(0)}”.",
                            "replacements": [{"value": "de"}, {"value": "the"}],
                            "rule": {"id": "DOUBLE", "issueType": "grammar",
                                     "category": {"name": "Grammar"}}})
        return matches
    return request


TEXTS = ["Controleer de de klep.", "", "Alles goed.", "Zet teh pomp aan en de de rest."]


def test_findings_map_back_to_their_segments():
    calls = []
    found = check_texts(TEXTS, "nl", "http://localhost:8081", request=fake_server(calls))
    assert len(calls) == 1 and calls[0][2] == "nl"
    assert [(f.segment_index, f.offset, f.text) for f in found] == [
        (0, 11, "de de"), (3, 4, "teh"), (3, 20, "de de")]
    assert found[0].context == "Controleer [de de] klep."
    assert found[0].replacements == ["de", "the"] and found[0].category == "Grammar"


def test_batches_split_by_size_and_the_public_api_is_paced():
    texts = [f"Zin nummer {i} met de de fout." for i in range(40)]
    calls, pauses = [], []
    found = check_texts(texts, "nl", PUBLIC_API_URL, request=fake_server(calls),
                        sleep=pauses.append, max_chars=200)
    assert len(calls) > 1 and len(pauses) == len(calls) - 1
    assert sorted({f.segment_index for f in found}) == list(range(40))
    assert all(texts[f.segment_index][f.offset:f.offset + f.length] == "de de" for f in found)
    # A private server (or a Premium key) is not slowed down
    pauses.clear()
    check_texts(texts, "nl", "http://localhost:8081", request=fake_server([]),
                sleep=pauses.append, max_chars=200)
    assert pauses == []


def test_progress_can_stop_the_run():
    texts = [f"Zin {i} de de." for i in range(10)]
    calls = []
    check_texts(texts, "nl", "http://x", request=fake_server(calls), max_chars=20,
                progress=lambda done, total: done < 2)
    assert len(calls) == 2


def test_batch_offsets():
    batches = list(build_batches(["abc", "", "defg", "hi"], max_chars=9))
    assert batches == [("abc\n\ndefg", [(0, 0), (2, 5)]), ("hi", [(3, 0)])]


def test_applying_a_suggestion_checks_the_text_is_unchanged():
    finding = LTFinding(segment_index=0, offset=11, length=5, text="de de", message="")
    assert apply_replacement("Controleer de de klep.", finding, "de") == "Controleer de klep."
    assert apply_replacement("Controleer de klep nu.", finding, "de") is None


@pytest.mark.parametrize("value, code", [("nl", "nl"), ("Dutch", "nl"), ("nl-BE", "nl-BE"),
                                         ("English", "en-US"), ("en-GB", "en-GB"),
                                         ("de", "de-DE"), ("", "auto")])
def test_language_codes(value, code):
    assert lt_language(value) == code
