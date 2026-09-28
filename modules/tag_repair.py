"""
Tag Repair
==========

Puts drifted numbered inline tags in an AI translation back into their
canonical form before the translation reaches the grid (issue #226).

Some models return a tag that has drifted from the ``<1>`` / ``</1>`` /
``<1/>`` it was given – ``< 1>``, ``</ 1 >``, ``<1 />``, or HTML-escaped as
``&lt;1&gt;``. Every parser downstream expects the exact canonical form, so a
drifted tag used to end up in the delivered target as literal text.

Rather than loosening the several tag regexes that also drive import and
export (the risk the issue warns about), this repairs the text once, where AI
output enters the project, and only ever rewrites a variant into a tag that
**the source segment actually contains**. So ``< 5 mm`` or ``<2>`` in a
segment whose source has no ``<2>`` are left alone.

An empty pair (``<1></1>`` with the text outside it) cannot be repaired
without guessing which words it should wrap, so it is left for the tag check.
"""

import re

_CANONICAL = re.compile(r'</?\d+/?>')

# A numbered tag with stray whitespace and/or HTML-escaped brackets.
_DRIFTED = re.compile(
    r'(?:<|&lt;)\s*(?P<close>/)?\s*(?P<num>\d+)\s*(?P<self>/)?\s*(?:>|&gt;)')


def repair_drifted_tags(source: str, translation: str) -> str:
    """Return ``translation`` with drifted numbered tags that match a tag in
    ``source`` rewritten to their canonical form. Anything else is untouched."""
    if not translation or not source:
        return translation
    wanted = set(_CANONICAL.findall(source))
    if not wanted:
        return translation

    def fix(match):
        num = match.group('num')
        if match.group('close'):
            canonical = f'</{num}>'
        elif match.group('self'):
            canonical = f'<{num}/>'
        else:
            canonical = f'<{num}>'
        if canonical in wanted:
            return canonical
        return match.group(0)

    return _DRIFTED.sub(fix, translation)
