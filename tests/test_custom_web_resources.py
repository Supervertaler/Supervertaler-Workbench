"""Custom web resources for SuperLookup (issue #208)."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from modules.custom_web_resources import normalise, to_resources, validate


@pytest.mark.parametrize("name, url, ok", [
    ("DWDS", "https://www.dwds.de/?q={query}", True),
    ("Leo", "http://dict.leo.org/{sl_full}-{tl_full}/{query}", True),
    ("", "https://x.org/?q={query}", False),
    ("No query", "https://x.org/search", False),
    ("Script", "javascript:alert({query})", False),
    ("File", "file:///C:/{query}", False),
    ("Spaces", "https://x.org/?q={query} &x=1", False),
])
def test_validate(name, url, ok):
    assert (validate(name, url) is None) is ok


def test_normalise_drops_bad_entries_and_keeps_ids():
    entries = normalise([
        {"id": "a1", "name": " DWDS ", "url": " https://www.dwds.de/?q={query} "},
        {"name": "Broken", "url": "https://x.org"},
        "not a dict",
        {"id": "a1", "name": "Duplicate id", "url": "https://y.org/{query}"},
        {"name": "New", "url": "https://z.org/?s={query}"},
    ])
    assert [e["name"] for e in entries] == ["DWDS", "Duplicate id", "New"]
    assert entries[0] == {"id": "a1", "name": "DWDS", "url": "https://www.dwds.de/?q={query}"}
    assert len({e["id"] for e in entries}) == 3 and all(e["id"] for e in entries)
    assert normalise(None) == []


def test_resources_match_the_builtin_shape():
    (res,) = to_resources([{"id": "a1", "name": "DWDS", "url": "https://www.dwds.de/?q={query}"}])
    assert res["id"] == "custom_a1" and res["url_template"].endswith("{query}")
    assert {"name", "icon", "description", "lang_format", "bidirectional"} <= set(res)
