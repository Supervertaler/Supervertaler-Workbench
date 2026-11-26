"""Inline formatting tags: tagged text <-> Word runs, compact tags, tag
transfer and checks. Moved unchanged from Supervertaler.py.
"""

import re
from modules import inline_codes as _inline_codes


def runs_to_tagged_text(paragraphs) -> str:
    """
    Convert Word document paragraphs with run formatting to HTML-tagged text.

    Args:
        paragraphs: List of python-docx Paragraph objects

    Returns:
        String with HTML tags for formatting (e.g., "<b>bold</b> normal <sub>V</sub>")
    """
    result_parts = []

    for paragraph in paragraphs:
        for run in paragraph.runs:
            text = run.text
            if not text:
                continue

            # Determine which tags to apply
            is_bold = run.bold == True
            is_italic = run.italic == True
            is_underline = run.underline == True
            is_subscript = getattr(run.font, 'subscript', None) == True
            is_superscript = getattr(run.font, 'superscript', None) == True

            # Build tagged text
            if is_bold or is_italic or is_underline or is_subscript or is_superscript:
                # Open tags (order: bold, italic, underline, sub/sup)
                if is_bold:
                    text = f"<b>{text}"
                if is_italic:
                    text = f"<i>{text}" if not is_bold else text.replace("<b>", "<b><i>", 1)
                if is_underline:
                    if is_bold and is_italic:
                        text = text.replace("<b><i>", "<b><i><u>", 1)
                    elif is_bold:
                        text = text.replace("<b>", "<b><u>", 1)
                    elif is_italic:
                        text = text.replace("<i>", "<i><u>", 1)
                    else:
                        text = f"<u>{text}"
                if is_subscript:
                    text = f"<sub>{text}"
                if is_superscript:
                    text = f"<sup>{text}"

                # Close tags (reverse order: sup, sub, underline, italic, bold)
                if is_superscript:
                    text = f"{text}</sup>"
                if is_subscript:
                    text = f"{text}</sub>"
                if is_underline:
                    text = f"{text}</u>"
                if is_italic:
                    text = f"{text}</i>"
                if is_bold:
                    text = f"{text}</b>"

            result_parts.append(text)

    return ''.join(result_parts)


def strip_formatting_tags(text: str) -> str:
    """
    Remove HTML formatting tags from text, leaving plain text.

    Args:
        text: Text with HTML tags like <b>, </b>, <i>, </i>, <u>, </u>, <sub>, </sub>, <sup>, </sup>

    Returns:
        Plain text without tags
    """
    import re
    return re.sub(r'</?(?:[biu]|sub|sup)>', '', text)


def strip_outer_wrapping_tags(text: str) -> tuple:
    """
    Strip outer wrapping tags from text if the entire segment is wrapped in a single tag pair.

    This handles structural tags like <li-o>...</li-o>, <p>...</p>, <td>...</td>,
    as well as formatting tags like <b>...</b> when they wrap the *entire* segment.
    Inner formatting (e.g. "Some <b>bold</b> text") is never affected.

    Args:
        text: Text that may be wrapped in outer tags

    Returns:
        Tuple of (stripped_text, tag_name) where tag_name is the outer tag that was stripped,
        or (original_text, None) if no outer wrapping tag was found

    Examples:
        "<li-o>Hello <b>world</b></li-o>" -> ("Hello <b>world</b>", "li-o")
        "<p>Simple text</p>" -> ("Simple text", "p")
        "No tags here" -> ("No tags here", None)
        "<b>Bold text</b>" -> ("Bold text", "b")  # <b> wraps entire segment → stripped
    """
    import re

    if not text or not text.strip():
        return (text, None)

    text = text.strip()

    # Tags that wrap entire segments – structural AND formatting.
    # Formatting tags (b, i, u, …) are included because when they wrap
    # the *entire* segment they act as structural wrappers; the function
    # already guarantees it only strips the single outermost pair, so
    # inner formatting like "Some <b>bold</b> text" is never affected.
    strippable_tags = {
        # Structural / layout
        'li-o', 'li-b', 'li', 'p', 'td', 'th', 'tr', 'div', 'span',
        'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'title', 'caption',
        'blockquote', 'pre', 'code', 'dt', 'dd', 'header', 'footer',
        'article', 'section', 'aside', 'nav', 'main', 'figure', 'figcaption',
        # Formatting (only stripped when wrapping the entire segment)
        'b', 'i', 'u', 'em', 'strong', 's', 'strike', 'sub', 'sup', 'mark',
    }

    # Pattern to match opening tag at start: <tag> or <tag attr="...">
    opening_pattern = r'^<([a-zA-Z][a-zA-Z0-9-]*)(?:\s+[^>]*)?>(.*)$'
    opening_match = re.match(opening_pattern, text, re.DOTALL)

    if not opening_match:
        return (text, None)

    tag_name = opening_match.group(1).lower()
    rest = opening_match.group(2)

    # Only strip recognised tags
    if tag_name not in strippable_tags:
        return (text, None)

    # Check if text ends with matching closing tag
    closing_pattern = rf'^(.*)</{re.escape(tag_name)}>$'
    closing_match = re.match(closing_pattern, rest, re.DOTALL | re.IGNORECASE)

    if not closing_match:
        return (text, None)

    inner_content = closing_match.group(1)

    # Verify this is truly a wrapping pair (no other occurrences of this tag inside)
    # Count opening and closing tags of this type in the inner content
    inner_opening_count = len(re.findall(rf'<{re.escape(tag_name)}(?:\s+[^>]*)?>',
                                          inner_content, re.IGNORECASE))
    inner_closing_count = len(re.findall(rf'</{re.escape(tag_name)}>', inner_content, re.IGNORECASE))

    # If there are nested tags of the same type, don't strip
    if inner_opening_count > 0 or inner_closing_count > 0:
        return (text, None)

    return (inner_content, tag_name)


def get_docx_language_code(lang_name_or_code: str) -> str:
    """
    Convert language name or code to DOCX locale format (e.g., 'nl-NL', 'en-US').

    DOCX files use BCP 47 / RFC 5646 language tags with region codes.

    Args:
        lang_name_or_code: Language name (e.g., 'Dutch', 'English') or code (e.g., 'nl', 'en')

    Returns:
        DOCX-compatible language code with region (e.g., 'nl-NL', 'en-US')
    """
    from modules import language_codes as _lc
    return _lc.docx_locale(lang_name_or_code)


def set_docx_language(doc, lang_code: str):
    """
    Set the language for all content in a DOCX document.

    This modifies the document's default paragraph style and existing
    paragraphs to use the specified language for spellcheck and proofing.

    Args:
        doc: A python-docx Document object
        lang_code: Language code in BCP 47 format (e.g., 'nl-NL', 'en-US')
    """
    from docx.oxml.ns import qn
    from docx.oxml import OxmlElement

    # Set language on document's default paragraph properties (styles.xml)
    try:
        # Access the styles part
        styles = doc.styles

        # Get or create Normal style's rPr (run properties)
        normal_style = styles['Normal']

        # Set language on the style's font/run properties
        if normal_style._element.rPr is None:
            rPr = OxmlElement('w:rPr')
            normal_style._element.append(rPr)
        else:
            rPr = normal_style._element.rPr

        # Remove existing lang element if present
        existing_lang = rPr.find(qn('w:lang'))
        if existing_lang is not None:
            rPr.remove(existing_lang)

        # Create and add new lang element
        lang_elem = OxmlElement('w:lang')
        lang_elem.set(qn('w:val'), lang_code)
        lang_elem.set(qn('w:eastAsia'), lang_code)
        lang_elem.set(qn('w:bidi'), lang_code)
        rPr.append(lang_elem)

    except Exception:
        pass  # Style manipulation failed, continue with paragraph-level setting

    # Also set language on all existing paragraphs' runs for immediate effect
    for para in doc.paragraphs:
        for run in para.runs:
            try:
                # Access run's XML element
                run_elem = run._element

                # Get or create rPr
                rPr = run_elem.rPr
                if rPr is None:
                    rPr = OxmlElement('w:rPr')
                    run_elem.insert(0, rPr)

                # Remove existing lang element if present
                existing_lang = rPr.find(qn('w:lang'))
                if existing_lang is not None:
                    rPr.remove(existing_lang)

                # Create and add new lang element
                lang_elem = OxmlElement('w:lang')
                lang_elem.set(qn('w:val'), lang_code)
                rPr.append(lang_elem)
            except Exception:
                continue  # Skip runs that can't be modified

    # Also handle table cells
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                for para in cell.paragraphs:
                    for run in para.runs:
                        try:
                            run_elem = run._element
                            rPr = run_elem.rPr
                            if rPr is None:
                                rPr = OxmlElement('w:rPr')
                                run_elem.insert(0, rPr)

                            existing_lang = rPr.find(qn('w:lang'))
                            if existing_lang is not None:
                                rPr.remove(existing_lang)

                            lang_elem = OxmlElement('w:lang')
                            lang_elem.set(qn('w:val'), lang_code)
                            rPr.append(lang_elem)
                        except Exception:
                            continue


def has_formatting_tags(text: str) -> bool:
    """
    Check if text contains any formatting tags.

    Args:
        text: Text to check

    Returns:
        True if text contains <b>, <i>, <u>, <sub>, or <sup> tags
    """
    import re
    return bool(re.search(r'</?(?:[biu]|sub|sup)>', text))


def apply_formatting_tags(text: str, tag: str) -> str:
    """
    Wrap text with the specified formatting tag.
    
    Args:
        text: Text to wrap
        tag: Tag name ('b', 'i', or 'u')
        
    Returns:
        Tagged text like "<b>text</b>"
    """
    if tag in ('b', 'i', 'u'):
        return f"<{tag}>{text}</{tag}>"
    return text


# Phrase (Memsource) inline tags, as in MXLIFF and the Phrase bilingual DOCX:
# formatting pairs {b>…<b}, {bi>…<bi}, {i>, {u>, {sup>, {sub>, and numbered
# codes {1}, {2>…<2}. Compiled once: WYSIWYG rendering runs per grid cell.
_PHRASE_TAG_RE = re.compile(
    r'(\{(?:[biu]{1,3}|sup|sub)>|<(?:[biu]{1,3}|sup|sub)\}|\{\d+[}>]|<\d+\})')


_PHRASE_MARKER_STYLE = 'color:#888888; font-size:small;'


def has_phrase_tags(text: str) -> bool:
    """True when ``text`` contains Phrase inline tags (``{b>``, ``{1}``, …)."""
    return bool(text) and '{' in text and _PHRASE_TAG_RE.search(text) is not None


def phrase_tags_to_html(text: str) -> str:
    """HTML for WYSIWYG display of text with Phrase tags.

    Formatting pairs become real bold/italic/underline/sub/superscript;
    numbered codes stay visible as small grey markers, so they are literal
    text in the editor and come back unchanged when the cell is edited.
    Anything else – including a literal ``<b>`` – is shown as written.
    """
    import html
    out = []
    for i, part in enumerate(_PHRASE_TAG_RE.split(text)):
        if not part:
            continue
        if i % 2 == 0:                       # text between tags
            out.append(html.escape(part))
        elif part[0] == '{' and part[-1] == '>' and not part[1].isdigit():
            out.append(''.join(f'<{t}>' for t in _phrase_tag_names(part[1:-1])))
        elif part[0] == '<' and part[-1] == '}' and not part[1].isdigit():
            out.append(''.join(f'</{t}>' for t in reversed(_phrase_tag_names(part[1:-1]))))
        else:                                # {1}, {2>, <2}
            out.append(f'<span style="{_PHRASE_MARKER_STYLE}">{html.escape(part)}</span>')
    return f'<span style="white-space: pre-wrap;">{"".join(out)}</span>'


def _phrase_tag_names(name: str) -> list:
    """``'bi'`` → ``['b', 'i']``; ``'sup'`` → ``['sup']``."""
    return [name] if name in ('sup', 'sub') else list(name)


def wysiwyg_runs_to_phrase_text(runs) -> str:
    """Serialise ``[(text, active_tag_names)]`` runs (as read back from a
    WYSIWYG cell) into Phrase tag syntax: ``{b>…<b}``, ``{bi>…<bi}``,
    ``{sup>…<sup}``. Phrase writes combined formatting as one flat tag, so
    adjacent runs with the same formatting are merged first."""
    merged = []
    for text, active in runs:
        active = list(active)
        if merged and merged[-1][1] == active:
            merged[-1][0] += text
        else:
            merged.append([text, active])
    parts = []
    for text, active in merged:
        key = ''.join(t for t in ('b', 'i', 'u') if t in active)
        script = 'sup' if 'sup' in active else ('sub' if 'sub' in active else '')
        if key:
            parts.append(f'{{{key}>')
        if script:
            parts.append(f'{{{script}>')
        parts.append(text)
        if script:
            parts.append(f'<{script}}}')
        if key:
            parts.append(f'<{key}}}')
    return ''.join(parts)


def is_phrase_project(project) -> bool:
    """True for a project imported from Phrase (MXLIFF or bilingual DOCX),
    whose formatting tags are Phrase's {b>…<b}, not Workbench's <b>."""
    return bool(project is not None and (getattr(project, 'mxliff_source_path', None)
                                         or getattr(project, 'phrase_source_path', None)))


_PHRASE_OPEN_START_RE = re.compile(r'\{([biu]{1,3})>')
_PHRASE_OPEN_END_RE = re.compile(r'\{([biu]{1,3})>$')
_PHRASE_CLOSE_START_RE = re.compile(r'<([biu]{1,3})\}')
_PHRASE_CLOSE_END_RE = re.compile(r'<([biu]{1,3})\}$')
_PHRASE_FMT_ONLY_RE = re.compile(r'\{[biu]{1,3}>|<[biu]{1,3}\}')


def toggle_phrase_tag(text: str, start: int, end: int, tag: str):
    """Ctrl+B/I/U in Tags view of a Phrase project.

    Toggles ``tag`` ('b', 'i' or 'u') on ``text[start:end]`` in Phrase
    syntax. A Phrase tag pair right around the selection – or the selection
    being exactly such a pair – is changed rather than nested:
    ``{b>x<b}`` + Ctrl+I → ``{bi>x<bi}``, ``{bi>x<bi}`` + Ctrl+B →
    ``{i>x<i}``, ``{b>x<b}`` + Ctrl+B → ``x``.

    Returns ``(replace_start, replace_end, replacement)``: the span of
    ``text`` to replace and what to put there.
    """
    sel = text[start:end]
    letters, inner, rep_start, rep_end = '', sel, start, end
    open_m = _PHRASE_OPEN_START_RE.match(sel)
    close_m = _PHRASE_CLOSE_END_RE.search(sel)
    if (open_m and close_m and open_m.group(1) == close_m.group(1)
            and open_m.end() <= close_m.start()
            and not _PHRASE_FMT_ONLY_RE.search(sel, open_m.end(), close_m.start())):
        # The selection is a whole tag pair: {b>x<b}
        letters, inner = open_m.group(1), sel[open_m.end():close_m.start()]
    else:
        before = _PHRASE_OPEN_END_RE.search(text, 0, start)
        after = _PHRASE_CLOSE_START_RE.match(text, end)
        if before and after and before.group(1) == after.group(1):
            # A tag pair right around the selection: {b>[x]<b}
            letters, rep_start, rep_end = before.group(1), before.start(), after.end()
    wanted = set(letters) ^ {tag}
    new = ''.join(t for t in 'biu' if t in wanted)
    if not new:
        return rep_start, rep_end, inner
    return rep_start, rep_end, f'{{{new}>{inner}<{new}}}'


def get_formatted_html_display(text: str) -> str:
    """
    Convert our simple tags to HTML for rich text display.

    Args:
        text: Text with <b>, <i>, <u>, <sub>, <sup> tags

    Returns:
        HTML string suitable for QTextEdit.setHtml()
    """
    if has_phrase_tags(text):
        return phrase_tags_to_html(text)

    # Escape HTML entities first (except our tags)
    import html

    # Temporarily replace our tags with placeholders
    text = text.replace('<b>', '\x00B_OPEN\x00')
    text = text.replace('</b>', '\x00B_CLOSE\x00')
    text = text.replace('<i>', '\x00I_OPEN\x00')
    text = text.replace('</i>', '\x00I_CLOSE\x00')
    text = text.replace('<u>', '\x00U_OPEN\x00')
    text = text.replace('</u>', '\x00U_CLOSE\x00')
    text = text.replace('<sub>', '\x00SUB_OPEN\x00')
    text = text.replace('</sub>', '\x00SUB_CLOSE\x00')
    text = text.replace('<sup>', '\x00SUP_OPEN\x00')
    text = text.replace('</sup>', '\x00SUP_CLOSE\x00')

    # Escape other HTML
    text = html.escape(text)

    # Restore our tags as real HTML
    text = text.replace('\x00B_OPEN\x00', '<b>')
    text = text.replace('\x00B_CLOSE\x00', '</b>')
    text = text.replace('\x00I_OPEN\x00', '<i>')
    text = text.replace('\x00I_CLOSE\x00', '</i>')
    text = text.replace('\x00U_OPEN\x00', '<u>')
    text = text.replace('\x00U_CLOSE\x00', '</u>')
    text = text.replace('\x00SUB_OPEN\x00', '<sub>')
    text = text.replace('\x00SUB_CLOSE\x00', '</sub>')
    text = text.replace('\x00SUP_OPEN\x00', '<sup>')
    text = text.replace('\x00SUP_CLOSE\x00', '</sup>')

    # Preserve whitespace in HTML rendering (prevents collapsing spaces/indentation)
    text = f'<span style="white-space: pre-wrap;">{text}</span>'

    return text


def tagged_text_to_runs(text: str) -> list:
    """
    Parse text with HTML formatting tags and return a list of runs with formatting info.

    Args:
        text: Text with <b>, <i>, <u>, <sub>, <sup> tags (can be nested)

    Returns:
        List of dicts with keys: text, bold, italic, underline, subscript, superscript
    """
    import re

    runs = []

    # Track current formatting state
    is_bold = False
    is_italic = False
    is_underline = False
    is_subscript = False
    is_superscript = False

    # Pattern to match opening/closing tags
    tag_pattern = re.compile(r'(</?(?:[biu]|sub|sup)>)')

    # Split text by tags, keeping the tags as delimiters
    parts = tag_pattern.split(text)

    current_text = ""

    def _flush():
        nonlocal current_text
        if current_text:
            runs.append({
                'text': current_text,
                'bold': is_bold,
                'italic': is_italic,
                'underline': is_underline,
                'subscript': is_subscript,
                'superscript': is_superscript,
            })
            current_text = ""

    for part in parts:
        if part == '<b>':
            _flush()
            is_bold = True
        elif part == '</b>':
            _flush()
            is_bold = False
        elif part == '<i>':
            _flush()
            is_italic = True
        elif part == '</i>':
            _flush()
            is_italic = False
        elif part == '<u>':
            _flush()
            is_underline = True
        elif part == '</u>':
            _flush()
            is_underline = False
        elif part == '<sub>':
            _flush()
            is_subscript = True
        elif part == '</sub>':
            _flush()
            is_subscript = False
        elif part == '<sup>':
            _flush()
            is_superscript = True
        elif part == '</sup>':
            _flush()
            is_superscript = False
        else:
            # Regular text - accumulate it
            current_text += part

    # Don't forget any remaining text
    _flush()

    return runs


def extract_memoq_tags(text: str) -> list:
    """
    Extract all memoQ-style tags from text in order of appearance.
    
    memoQ uses several tag types:
    - Paired opening tags: [1}, [2}, [3} etc.
    - Paired closing tags: {1], {2], {3] etc.
    - Standalone tags: [1], [2], [3] etc. (e.g., for tabs, special characters)
    
    Args:
        text: Source text containing tags
        
    Returns:
        List of tag strings in order of appearance: ['[1}', '{1]', '[2]', ...]
    """
    import re
    # Match:
    # - Opening paired tags: [N}
    # - Closing paired tags: {N]
    # - Standalone tags: [N]
    pattern = r'(\[\d+\}|\{\d+\]|\[\d+\])'
    return re.findall(pattern, text)


def extract_html_tags(text: str) -> list:
    """
    Extract all HTML/XML tags from text in order of appearance.

    Supports common formatting tags used in translation:
    - Opening tags: <b>, <i>, <u>, <li>, <p>, <span>, etc.
    - Closing tags: </b>, </i>, </u>, </li>, </p>, </span>, etc.
    - Self-closing tags: <br/>, <hr/>, etc.
    - Trados/SDLXLIFF numeric tags: <92>, </92>, etc.

    Args:
        text: Source text containing HTML tags

    Returns:
        List of tag strings in order of appearance: ['<li>', '</li>', '<b>', '</b>', ...]
    """
    import re
    # Match HTML/XML tags: <tagname>, </tagname>, <tagname/>, <tagname attr="value">
    # Also match Trados/SDLXLIFF numeric tags: <N>, </N>
    pattern = r'(</?[a-zA-Z][a-zA-Z0-9]*(?:\s+[^>]*)?>|</?\d+>)'
    return re.findall(pattern, text)


def compact_tags(text: str, tag_map: dict = None) -> str:
    """
    Replace verbose XML/HTML tags with short numbered placeholders for display.

    Simple formatting tags (<b>, <i>, <u>, <sub>, <sup> and their closing tags)
    are left untouched. Only verbose tags (those with attributes or long names
    like <bmk id="0" name="_Toc219208699" transform="open">) are shortened.

    Opening tags get {1}, closing tags get {/1}, self-closing tags get {1/}.
    Each unique tag name gets a stable number (first seen = 1, etc.).

    Args:
        text: Text containing verbose tags.
        tag_map: Optional dict to populate with placeholder→full-tag mappings
                 (for reversing later). If None, no map is built.

    Returns:
        Text with verbose tags replaced by compact numbered placeholders.
    """
    import re

    # Tags to leave untouched (simple formatting – already short)
    _PASSTHROUGH = {'b', 'i', 'u', 'sub', 'sup', 'em', 'strong', 's'}

    tag_pattern = re.compile(
        r'<(/?)([a-zA-Z][a-zA-Z0-9-]*)(\s[^>]*)?(/?)\s*>'  # named tags with optional attrs
        r'|'
        r'</(\d+)>'          # Trados numeric closing: </92>
        r'|'
        r'<(\d+)>'           # Trados numeric opening: <92>
    )

    tag_name_to_num = {}
    counter = [0]

    def _get_num(name: str) -> int:
        if name not in tag_name_to_num:
            counter[0] += 1
            tag_name_to_num[name] = counter[0]
        return tag_name_to_num[name]

    def _replace(m):
        full_tag = m.group(0)

        # Trados numeric closing </N>
        if m.group(5) is not None:
            n = _get_num(f"trados_{m.group(5)}")
            placeholder = f"{{/{n}}}"
            if tag_map is not None:
                tag_map[placeholder] = full_tag
            return placeholder
        # Trados numeric opening <N>
        if m.group(6) is not None:
            n = _get_num(f"trados_{m.group(6)}")
            placeholder = f"{{{n}}}"
            if tag_map is not None:
                tag_map[placeholder] = full_tag
            return placeholder

        # Named tag
        slash = m.group(1)      # '/' for closing, '' for opening
        name = m.group(2)       # tag name
        attrs = m.group(3)      # attributes (may be None)
        self_close = m.group(4) # '/' for self-closing

        # Pass through simple formatting tags without attributes
        if name.lower() in _PASSTHROUGH and not attrs:
            return full_tag

        # Only shorten if the tag is verbose (has attributes or name > 3 chars)
        if not attrs and len(name) <= 3:
            return full_tag

        n = _get_num(name.lower())
        if slash:
            placeholder = f"{{/{n}}}"
        elif self_close:
            placeholder = f"{{{n}/}}"
        else:
            placeholder = f"{{{n}}}"

        if tag_map is not None:
            tag_map[placeholder] = full_tag
        return placeholder

    return tag_pattern.sub(_replace, text)


# Word Joiner (U+2060): a zero-width character that forbids a line break on
# either side of itself. Used to stop the grid word-wrap from splitting a tag
# mid-way (see protect_tags_from_linebreak). Stripped again on read-back by
# reverse_invisible_replacements, so it never reaches saved segment text.
WORD_JOINER = chr(0x2060)  # U+2060 WORD JOINER (zero-width, no-break)


# Recognised tag forms that can contain a "/" and would otherwise wrap after it.
# The Unicode line-breaking algorithm (UAX #14) treats SOLIDUS as class SY —
# "break opportunity after" — so "</i>" is allowed to wrap as "</" + "i>" even
# in word-boundary wrap mode. We only need to guard tags that actually contain
# a slash: HTML/XML closing & self-closing tags, Trados numeric closing tags,
# and the compact {/n}/{n/} placeholders.
_TAG_LINEBREAK_PROTECT_RE = re.compile(
    r'</?[a-zA-Z][a-zA-Z0-9-]*/?(?:\s[^>]*)?>'  # <i>, </i>, <t2/>, <tag attr="x">
    r'|</?\d+>'                                  # Trados numeric: <1>, </1>
    r'|\{/?\d+/?\}'                              # compact placeholders: {/1}, {1/}
)


def protect_tags_from_linebreak(text: str) -> str:
    """Insert a WORD JOINER after every "/" inside a recognised tag so the grid
    word-wrap can't split the tag across two lines (e.g. "</i>" wrapping as
    "</" + "i>"). Display-only: reverse_invisible_replacements strips the joiner
    before any text is written back, so the model text is unaffected. A no-op
    when the text contains no slash."""
    if not text or '/' not in text:
        return text
    return _TAG_LINEBREAK_PROTECT_RE.sub(
        lambda m: m.group(0).replace('/', '/' + WORD_JOINER), text)


def expand_compact_tags(text: str, tag_map: dict) -> str:
    """
    Reverse compact_tags() – replace numbered placeholders back to full tags.

    Args:
        text: Text with compact placeholders like {1}, {/1}.
        tag_map: Dict mapping placeholder → full tag string.

    Returns:
        Text with full tags restored.
    """
    if not tag_map:
        return text
    # Sort by longest placeholder first to avoid partial replacements
    for placeholder, full_tag in sorted(tag_map.items(), key=lambda x: -len(x[0])):
        text = text.replace(placeholder, full_tag)
    return text


# Combined pattern for memoQ tags, HTML tags, and Trados/SDLXLIFF numeric tags
# memoQ: [N}, {N], [N]
# HTML/XML: <tag>, </tag>, <tag/>, <tag attr="value"> - includes hyphenated tags
#   like li-o, li-b, and namespaced ones like memoQ's <mq:ch val="→" />
# Trados/SDLXLIFF: <N>, </N>, and standalone <N/>
# Self-closing forms without a space (<2/>, <br/>) used to be missed, so the
# standalone tags of every SDLXLIFF/Trados import were never offered by Insert
# next tag (Ctrl+,) and a lost one went unreported by the QA tag check.
_ALL_TAGS_PATTERN = r'(\[\d+\}|\{\d+\]|\[\d+\]|</?\d+/?>|</?[a-zA-Z][a-zA-Z0-9._:-]*(?:\s+[^>]*)?/?>)'


def extract_all_tags(text: str) -> list:
    """
    Extract all tags (memoQ, HTML, and Trados numeric) from text in order of appearance,
    plus the user's own inline codes from Settings → Inline Codes (issue #194).

    Args:
        text: Source text containing tags

    Returns:
        List of all tag strings in order of appearance
    """
    import re
    found = [(m.start(), m.end(), m.group(0)) for m in re.finditer(_ALL_TAGS_PATTERN, text or '')]
    for start, end, code in _inline_codes.find_codes(text):
        if not any(s < end and start < e for s, e, _ in found):
            found.append((start, end, code))
    return [tag for _, _, tag in sorted(found)]


# AutoTagger's pattern – now the same as extract_all_tags'. It matches
# self-closing forms (numbered <2/> and HTML <x1/>) which the SDLXLIFF/Trados
# standalone tags use.
# The name part allows XML QName characters — letters, digits, '.', '_', '-'
# and ':'. The colon matters: memoQ bilingual files carry NAMESPACED tags such
# as <mq:ch val="→" />, and a name class of [a-zA-Z0-9-] stopped at the colon,
# so those tags were not recognised as tags at all. AutoTagger then reported
# "the source segment has no inline tags" on most segments of a real memoQ
# file and did nothing.
_AUTOTAG_TAG_PATTERN = r'(\[\d+\}|\{\d+\]|\[\d+\]|</?\d+/?>|</?[a-zA-Z][a-zA-Z0-9._:-]*(?:\s+[^>]*)?/?>)'


def autotag_extract_tags(text: str) -> list:
    """Extract all inline tags (incl. self-closing) for AutoTagger, in order."""
    import re
    return re.findall(_AUTOTAG_TAG_PATTERN, text or '')


def strip_all_tags(text: str) -> str:
    """Remove every inline tag (incl. self-closing) from text, leaving the words.

    Used by AutoTagger to get the tag-free target and to verify the AI only
    moved tags (never changed wording)."""
    import re
    return re.sub(_AUTOTAG_TAG_PATTERN, '', text or '')


def _normalize_ws_for_compare(text: str) -> str:
    """Collapse runs of whitespace so a tags-only diff ignores spacing changes."""
    import re
    return re.sub(r'\s+', ' ', (text or '')).strip()


# Cosmetic character variants LLMs routinely swap (curly↔straight quotes,
# NBSP↔space, en/em dash, ellipsis). AutoTagger's "words unchanged" guard folds
# these so a tags-only placement isn't rejected just because the model
# normalised a quote — the real target text is still preserved on write via
# _reinsert_tags_into_target().
_WORD_COMPARE_FOLD = {
    '“': '"', '”': '"', '„': '"', '‟': '"', '«': '"', '»': '"', '″': '"',
    '‘': "'", '’': "'", '‚': "'", '‛': "'", '′': "'", '`': "'",
    ' ': ' ', ' ': ' ', ' ': ' ', '​': '',
    '–': '-', '—': '-', '‑': '-', '…': '...',
}


def _normalize_for_word_compare(text: str) -> str:
    """Whitespace- and punctuation-folded form for the 'words unchanged' check."""
    import re
    import unicodedata
    t = unicodedata.normalize('NFKC', text or '')
    for a, b in _WORD_COMPARE_FOLD.items():
        t = t.replace(a, b)
    return re.sub(r'\s+', ' ', t).strip()


def _reinsert_tags_into_target(candidate: str, clean_target: str):
    """Re-insert the tags found in ``candidate`` into the EXACT ``clean_target``.

    The AI may return the right tag positions but in a cosmetically different
    copy of the target (e.g. straight quotes). This maps each tag's position
    onto the user's original text so the written result keeps the user's exact
    wording/punctuation. Returns the reconstructed string, or None if the
    tag-free candidate can't be aligned to clean_target."""
    import re
    import difflib
    tag_re = re.compile(_AUTOTAG_TAG_PATTERN)
    pieces = []          # (offset_in_stripped_candidate, tag)
    cand_stripped_parts = []
    idx = 0
    pos = 0
    for m in tag_re.finditer(candidate):
        chunk = candidate[idx:m.start()]
        cand_stripped_parts.append(chunk)
        pos += len(chunk)
        pieces.append((pos, m.group(0)))
        idx = m.end()
    cand_stripped_parts.append(candidate[idx:])
    cand_stripped = ''.join(cand_stripped_parts)

    sm = difflib.SequenceMatcher(None, cand_stripped, clean_target, autojunk=False)
    opcodes = sm.get_opcodes()

    def map_offset(o):
        # opcodes tile [0, len(cand_stripped)] contiguously, so every offset is
        # covered. Equal regions map 1:1; for changed regions (e.g. a swapped
        # quote) map a boundary offset to the matching clean boundary.
        for op, a1, a2, b1, b2 in opcodes:
            if a1 <= o <= a2:
                if op == 'equal':
                    return b1 + (o - a1)
                if o >= a2:
                    return b2
                return b1
        return len(clean_target)

    from collections import defaultdict
    by_off = defaultdict(list)
    for off, tag in pieces:
        by_off[map_offset(off)].append(tag)

    out = []
    for i in range(len(clean_target) + 1):
        if i in by_off:
            out.extend(by_off[i])
        if i < len(clean_target):
            out.append(clean_target[i])
    return ''.join(out)


def _classify_tag(tag: str):
    """Classify a tag as ('open'|'close'|'self', key) for nesting checks."""
    import re
    # memoQ paired tags are keyed on a CONSTANT, not their number: memoQ numbers
    # tags sequentially across a segment, so an opener and its closer normally
    # carry DIFFERENT numbers ("[1}De uitvoer{2]"). Keying on the number made
    # every memoQ pair look mismatched, and AutoTagger refused the placement
    # with "unpaired or out of order". A constant key also still pairs the
    # same-number convention ("[1}…{1]") correctly, so it is right either way.
    for pat, kind, key in (
        (r'\[\d+\}', 'open', 'mq'), (r'\{\d+\]', 'close', 'mq'),
    ):
        if re.fullmatch(pat, tag):
            return (kind, key)
    for pat, kind, pre in (
        (r'\[(\d+)\]', 'self', 'mq'),
        (r'<(\d+)/>', 'self', 'n'), (r'<(\d+)>', 'open', 'n'), (r'</(\d+)>', 'close', 'n'),
    ):
        m = re.fullmatch(pat, tag)
        if m:
            return (kind, pre + m.group(1))
    m = re.fullmatch(r'<([a-zA-Z][a-zA-Z0-9._:-]*)(?:\s+[^>]*)?/>', tag)
    if m:
        return ('self', 'h' + m.group(1).lower())
    # Closing tags may carry ATTRIBUTES. Okapi-extracted DOCX content uses
    # forms like <rpr id="4">…</rpr id="4" transform="close"> and
    # <bmk id="3" …>…</bmk id="3" transform="close">. The old pattern allowed
    # only </name>, so these closers fell through to the ('self', tag)
    # fallback, their opener was never popped, and validation rejected a
    # perfectly good placement with "unclosed tag(s): hrpr" — reported as
    # "AutoTagger does nothing" (Workbench issue #244).
    m = re.fullmatch(r'</([a-zA-Z][a-zA-Z0-9._:-]*)(?:\s+[^>]*)?>', tag)
    if m:
        return ('close', 'h' + m.group(1).lower())
    m = re.fullmatch(r'<([a-zA-Z][a-zA-Z0-9._:-]*)(?:\s+[^>]*)?>', tag)
    if m:
        return ('open', 'h' + m.group(1).lower())
    return ('self', tag)


def _tags_well_formed(text: str) -> tuple:
    """True if paired tags open before they close and nest properly."""
    stack = []
    for tag in autotag_extract_tags(text):
        kind, key = _classify_tag(tag)
        if kind == 'open':
            stack.append(key)
        elif kind == 'close':
            if not stack or stack[-1] != key:
                return False, f"tag {tag} is unpaired or out of order"
            stack.pop()
    if stack:
        return False, "unclosed tag(s): " + ", ".join(stack)
    return True, ""


def validate_tag_transfer(source_text: str, candidate_target: str, clean_target: str) -> tuple:
    """Validate an AutoTagger result. Returns (ok: bool, reason: str).

    Checks: (1) the candidate's tag multiset equals the source's, (2) the words
    are unchanged versus the tag-free target, (3) tags are well-formed."""
    from collections import Counter
    src_tags = Counter(autotag_extract_tags(source_text))
    cand_tags = Counter(autotag_extract_tags(candidate_target))
    if src_tags != cand_tags:
        missing = src_tags - cand_tags
        extra = cand_tags - src_tags
        parts = []
        if missing:
            parts.append("missing " + ", ".join(f"{t}×{n}" for t, n in missing.items()))
        if extra:
            parts.append("unexpected " + ", ".join(f"{t}×{n}" for t, n in extra.items()))
        return False, "tag set does not match source (" + "; ".join(parts) + ")"
    if _normalize_for_word_compare(strip_all_tags(candidate_target)) != _normalize_for_word_compare(clean_target):
        return False, "the target wording changed (AutoTagger must only move tags)"
    ok, why = _tags_well_formed(candidate_target)
    if not ok:
        return False, why
    return True, ""


def place_tags_via_llm(client, prompt_manager, source_text: str, clean_target: str,
                       source_lang: str, target_lang: str, max_attempts: int = 2) -> tuple:
    """Ask the LLM to insert the source's inline tags into a tag-free target.

    Returns (new_target | None, reason). Retries up to max_attempts; only returns
    a result that passes validate_tag_transfer, so it never yields broken tags."""
    tags = autotag_extract_tags(source_text)
    if not tags:
        return clean_target, ""  # nothing to place
    template = prompt_manager.get_autotagger_template()
    prompt = (template
              .replace("{{SOURCE_TEXT}}", source_text)
              .replace("{{TARGET_TEXT}}", clean_target)
              .replace("{{TAG_LIST}}", " ".join(tags)))
    from modules import usage_log as _usage_log
    last_reason = "no result"
    for _ in range(max(1, max_attempts)):
        try:
            # Attribute these calls to the "AutoTagger" task in the usage ledger
            # (covers both the single-segment command and the batch worker).
            with _usage_log.UsageContext(task="AutoTagger"):
                result = client.translate(
                    text=clean_target,
                    source_lang=source_lang,
                    target_lang=target_lang,
                    custom_prompt=prompt,
                )
        except Exception as e:
            last_reason = f"LLM error: {e}"
            continue
        candidate = (result or "").strip()
        ok, reason = validate_tag_transfer(source_text, candidate, clean_target)
        if ok:
            # Prefer re-inserting the tags into the user's EXACT target so any
            # cosmetic differences the model introduced (e.g. straight quotes)
            # don't reach the saved segment. Fall back to the candidate if the
            # reconstruction can't be aligned or doesn't validate.
            recon = _reinsert_tags_into_target(candidate, clean_target)
            if recon is not None:
                rok, _ = validate_tag_transfer(source_text, recon, clean_target)
                if rok:
                    return recon, ""
            return candidate, ""
        last_reason = reason
    return None, last_reason


def count_pipe_symbols(text: str) -> int:
    """Count the number of CafeTran pipe symbols in text."""
    return text.count('|')


def get_next_pipe_count_needed(source_text: str, target_text: str) -> int:
    """
    Get how many more pipe symbols are needed in target to match source.
    
    Args:
        source_text: Source segment text with pipe symbols
        target_text: Current target text
        
    Returns:
        Number of additional pipe symbols needed (0 if target has enough or more)
    """
    source_pipes = count_pipe_symbols(source_text)
    target_pipes = count_pipe_symbols(target_text)
    return max(0, source_pipes - target_pipes)


def get_tag_pair(tag_number: int) -> tuple:
    """
    Get opening and closing tag pair for a given number.
    
    Args:
        tag_number: The tag number (1, 2, 3, etc.)
        
    Returns:
        Tuple of (opening_tag, closing_tag) e.g. ('[1}', '{1]')
    """
    return (f'[{tag_number}}}', f'{{{tag_number}]')


def find_next_unused_tag(source_text: str, target_text: str) -> str:
    """
    Find the next tag from source that hasn't been used in target yet.
    
    Supports both memoQ tags ([1}, {1], [1]) and HTML tags (<li>, </li>, <b>, etc.)
    
    Args:
        source_text: Source segment text with tags
        target_text: Current target text (may have some tags already)
        
    Returns:
        The next tag to insert, or empty string if all tags are used
    """
    # Use combined extraction for both memoQ and HTML 	
    source_tags = extract_all_tags(source_text)
    target_tags = extract_all_tags(target_text)
    
    # Count occurrences in target
    from collections import Counter
    target_tag_counts = Counter(target_tags)
    source_tag_counts = Counter(source_tags)
    
    # Find first tag that needs more occurrences in target
    for tag in source_tags:
        source_count = source_tag_counts[tag]
        target_count = target_tag_counts.get(tag, 0)
        if target_count < source_count:
            return tag
    
    return ""  # All tags already in target


def get_wrapping_tag_pair(source_text: str, target_text: str) -> tuple:
    """
    Get the next available tag pair for wrapping selected text.
    
    Finds the first tag number from source where either opening or closing
    tag is not yet in target.
    
    Args:
        source_text: Source segment text with tags
        target_text: Current target text
        
    Returns:
        Tuple of (opening_tag, closing_tag) or (None, None) if no pairs available
    """
    import re
    
    # Extract all tag numbers from source
    source_tags = extract_memoq_tags(source_text)
    if not source_tags:
        return (None, None)
    
    # Get unique tag numbers in order
    tag_numbers = []
    for tag in source_tags:
        match = re.search(r'\d+', tag)
        if match:
            num = int(match.group())
            if num not in tag_numbers:
                tag_numbers.append(num)
    
    target_tags = extract_memoq_tags(target_text)
    
    # Find first tag number where pair is not complete in target
    for num in tag_numbers:
        opening, closing = get_tag_pair(num)
        if opening not in target_tags or closing not in target_tags:
            return (opening, closing)

    return (None, None)


def get_html_wrapping_tag_pair(source_text: str, target_text: str) -> tuple:
    """
    Get the next available HTML tag pair for wrapping selected text.

    Finds paired HTML tags (opening + closing) from source that are not yet
    complete in target. Supports common formatting tags: b, i, u, em, strong,
    span, li, p, a, sub, sup, etc. Also supports Trados/SDLXLIFF numeric tags
    like <92>...</92>.

    Args:
        source_text: Source segment text with HTML tags
        target_text: Current target text

    Returns:
        Tuple of (opening_tag, closing_tag) or (None, None) if no pairs available
    """
    import re

    # Find all HTML tags in source
    # Match: <tag>, <tag attr="...">, </tag>, and Trados numeric tags <N>, </N>
    tag_pattern = r'<(/?)([a-zA-Z][a-zA-Z0-9-]*|\d+)(?:\s+[^>]*)?>'
    source_matches = re.findall(tag_pattern, source_text)
    target_matches = re.findall(tag_pattern, target_text)

    if not source_matches:
        return (None, None)

    # Build lists of opening and closing tags in source
    source_opening = []  # [(full_tag, tag_name), ...]
    source_closing = []

    for match in re.finditer(tag_pattern, source_text):
        is_closing = match.group(1) == '/'
        tag_name = match.group(2).lower()
        full_tag = match.group(0)

        if is_closing:
            source_closing.append((full_tag, tag_name))
        else:
            source_opening.append((full_tag, tag_name))

    # Build sets of tag names already in target
    target_opening_names = set()
    target_closing_names = set()

    for is_closing, tag_name in target_matches:
        if is_closing == '/':
            target_closing_names.add(tag_name.lower())
        else:
            target_opening_names.add(tag_name.lower())

    # Find first tag pair where both opening and closing exist in source
    # but at least one is missing from target
    seen_tags = set()
    for full_tag, tag_name in source_opening:
        if tag_name in seen_tags:
            continue
        seen_tags.add(tag_name)

        # Check if there's a matching closing tag in source
        has_closing = any(name == tag_name for _, name in source_closing)
        if not has_closing:
            continue

        # Check if pair is incomplete in target
        opening_in_target = tag_name in target_opening_names
        closing_in_target = tag_name in target_closing_names

        if not opening_in_target or not closing_in_target:
            # Find the actual opening and closing tags from source
            opening_tag = full_tag
            closing_tag = f"</{tag_name}>"
            return (opening_tag, closing_tag)

    return (None, None)


def strip_invisible_markers(text: str) -> str:
    """Remove all Show-Invisibles display markers from a string.

    These markers are inserted by apply_invisible_replacements() for on-screen
    display only and must never appear in segment.target or on disk.

    Handles:
      · + U+200B  → regular space   (space marker)
      ·           → regular space   (fallback, lone middle-dot)
      → + U+200B  → tab             (tab marker)
      →           → tab             (fallback, lone right-arrow)
      ↵ + \\n     → \\n              (line-break marker before newline)
      ↵           → (removed)       (bare line-break marker)
      U+200B      → (removed)       (stray zero-width space)

    NOTE: The degree sign ° is intentionally NOT reversed here because ° is a
    legitimate Unicode character that appears in normal translation text.  The
    NBSP substitution is handled separately by the main-window method that has
    project-level context.
    """
    if not text:
        return text
    result = text
    result = result.replace('\u00B7\u200B', ' ')   # middle-dot + ZWSP → space
    result = result.replace('\u00B7', ' ')          # lone middle-dot → space
    result = result.replace('\u2192\u200B', '\t')   # arrow + ZWSP → tab
    result = result.replace('\u2192', '\t')          # lone arrow → tab
    result = result.replace('\u21B5\n', '\n')        # ↵ + newline → newline (current marker)
    result = result.replace('\u21B5', '')            # bare ↵ → nothing
    result = result.replace('\u00B6\n', '\n')        # pilcrow + newline → newline (legacy, keep for old files)
    result = result.replace('\u00B6', '')            # bare pilcrow → nothing (legacy)
    result = result.replace('\u200B', '')            # stray ZWSP → nothing
    return result
