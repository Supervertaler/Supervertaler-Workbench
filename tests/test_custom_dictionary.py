"""Custom spellcheck dictionary management (issue #109).

parse_word_list must read plain word lists and Hunspell .dic files alike, and
set_custom_words must replace the dictionary in one go.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.spellcheck_manager import SpellcheckManager, parse_word_list


def test_plain_list_is_lowercased_sorted_and_deduplicated():
    words, dupes = parse_word_list("Supervertaler\nmemoQ\n\n  supervertaler  \n# comment\nTrados\n")
    assert words == ["memoq", "supervertaler", "trados"]
    assert dupes == 1


def test_hunspell_dic_count_line_and_flags_are_stripped():
    words, dupes = parse_word_list("3\nPomphuis/S\nbout/SM\nstaal\n")
    assert words == ["bout", "pomphuis", "staal"]
    assert dupes == 0


def test_a_number_is_only_a_count_on_the_first_line():
    words, _ = parse_word_list("M8\n42\n")
    assert words == ["42", "m8"]


def test_empty_input():
    assert parse_word_list("") == ([], 0)
    assert parse_word_list(None) == ([], 0)


def test_set_custom_words_replaces_and_persists(tmp_path):
    mgr = SpellcheckManager(user_data_path=str(tmp_path))
    mgr.add_to_dictionary("oldword")
    assert mgr.set_custom_words(["Nieuw", "woord", "nieuw", "# not a word", ""]) == 2
    assert mgr.get_custom_words() == ["nieuw", "woord"]

    reloaded = SpellcheckManager(user_data_path=str(tmp_path))
    assert reloaded.get_custom_words() == ["nieuw", "woord"]
