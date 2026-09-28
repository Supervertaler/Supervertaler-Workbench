"""Tests for adding pasted source text to an existing project (issue #173).

segment_split_merge.split_source_text turns pasted text into (paragraph,
sentence) pairs. Paragraphs matter: plain-text export writes one line per
paragraph_id, so new text must start its own paragraphs rather than being
glued onto the project's last line.
"""
from modules import segment_split_merge as ssm
from modules.simple_segmenter import SimpleSegmenter

SEG = SimpleSegmenter().segment_text


def test_each_line_is_a_paragraph_and_sentences_are_split():
    text = "First one. Second one.\n\n  Next paragraph here! And more?\n"
    assert ssm.split_source_text(text, SEG) == [
        (1, "First one."), (1, "Second one."),
        (2, "Next paragraph here!"), (2, "And more?"),
    ]


def test_windows_and_old_mac_line_endings():
    assert ssm.split_source_text("One.\r\nTwo.\rThree.", SEG) == [
        (1, "One."), (2, "Two."), (3, "Three."),
    ]


def test_blank_input_adds_nothing():
    assert ssm.split_source_text("", SEG) == []
    assert ssm.split_source_text(" \n\t\n", SEG) == []
    assert ssm.split_source_text(None, SEG) == []
