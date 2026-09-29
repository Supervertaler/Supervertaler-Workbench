"""
Dark Style Adapter
==================

Makes widgets with their own light-coloured stylesheets readable in a dark
theme (issue #78).

The theme itself is one application-wide stylesheet, but many widgets carry an
inline stylesheet with fixed light colours (``background-color: #f5f5f5``,
``color: #333``, ``background: white`` …), which overrides the theme. In a dark
theme that produced white panels, and light-grey text on light-grey
backgrounds. Rather than patch hundreds of stylesheets one by one, this
rewrites the colours in inline stylesheets when a dark theme is active:

* light backgrounds become dark – neutral ones to the theme's own base or
  window colours, tinted ones (pale blue info boxes, pale yellow warnings…) to
  a dark shade of the same hue;
* dark text becomes light, keeping its hue (dark blue → light blue);
* light borders become the theme's border colour.

Saturated colours that already work on dark (buttons in #2196F3, white text)
are left alone. The original stylesheet is kept on the widget, so switching back
to a light theme restores it exactly.
"""

import colorsys
import re
from typing import Optional

from PyQt6.QtCore import QEvent, QObject
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QApplication, QWidget

_ORIGINAL = "_sv_light_stylesheet"
_ADAPTED = "_sv_dark_stylesheet"

_NAMED = ("white|black|gray|grey|lightgray|lightgrey|silver|whitesmoke|gainsboro|beige|ivory|"
          "lavender|aliceblue|honeydew|mintcream|azure|seashell|snow|linen|oldlace|floralwhite|"
          "ghostwhite|lightyellow|lightcyan|lightblue|lightgreen|lightpink|darkgray|darkgrey|dimgray|"
          "dimgrey|navy|darkblue|darkgreen|darkred|maroon")
_COLOR = re.compile(r"#[0-9a-fA-F]{3,8}\b|\brgba?\([^)]*\)|\b(?:" + _NAMED + r")\b", re.IGNORECASE)
# property: value   (value up to ; or } – Qt stylesheets have no nested braces in values)
_DECL = re.compile(r"(?<![\w-])([\w-]*(?:background(?:-color)?|color|border(?:-[\w-]+)?))(\s*:\s*)([^;{}]+)",
                   re.IGNORECASE)


def _luminance(c: QColor) -> float:
    def ch(v):
        v /= 255.0
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    return 0.2126 * ch(c.red()) + 0.7152 * ch(c.green()) + 0.0722 * ch(c.blue())


def _with_lightness(c: QColor, lightness: float, max_saturation: float) -> QColor:
    h, _l, s = colorsys.rgb_to_hls(c.redF(), c.greenF(), c.blueF())
    r, g, b = colorsys.hls_to_rgb(h, lightness, min(s, max_saturation))
    out = QColor.fromRgbF(r, g, b)
    out.setAlpha(c.alpha())
    return out


def _saturation(c: QColor) -> float:
    return colorsys.rgb_to_hls(c.redF(), c.greenF(), c.blueF())[2]


def _parse(token: str) -> Optional[QColor]:
    t = token.strip()
    if t.lower().startswith("rgb"):
        nums = re.findall(r"[\d.]+%?", t)
        try:
            vals = [float(n.rstrip('%')) * (2.55 if n.endswith('%') else 1) for n in nums[:3]]
            c = QColor(int(vals[0]), int(vals[1]), int(vals[2]))
            if len(nums) > 3:
                a = float(nums[3].rstrip('%'))
                c.setAlphaF(a / 100 if nums[3].endswith('%') else (a if a <= 1 else a / 255))
            return c
        except (ValueError, IndexError):
            return None
    c = QColor(t)
    return c if c.isValid() else None


def _fmt(c: QColor) -> str:
    if c.alpha() < 255:
        return f"rgba({c.red()}, {c.green()}, {c.blue()}, {c.alpha()})"
    return c.name()


class _Palette:
    def __init__(self, theme):
        self.base = QColor(getattr(theme, 'base', '#1E1E1E'))
        self.window = QColor(getattr(theme, 'window_bg', '#2B2B2B'))
        self.alternate = QColor(getattr(theme, 'alternate_bg', '#353535'))
        self.text = QColor(getattr(theme, 'text', '#E0E0E0'))
        self.border = QColor(getattr(theme, 'border', '#505050'))
        self.muted = QColor("#A8A8A8")


def _lightness(c: QColor) -> float:
    return colorsys.rgb_to_hls(c.redF(), c.greenF(), c.blueF())[1]


def _map_background(c: QColor, p: _Palette) -> QColor:
    # Only pale and pastel colours break a dark theme: its light text can't be
    # read on them (e.g. the amber #FFE082 Replace buttons). Strong colours –
    # #FF9800 orange, #2196F3 blue, #4CAF50 green, #F44336 red, all with a
    # lightness of 0.6 or less – read fine and are part of the design.
    lightness = _lightness(c)
    if lightness <= 0.6:
        return c
    lum = _luminance(c)
    if _saturation(c) < 0.12 or lum > 0.97:
        # neutral: white → base, pale greys → window, light greys → alternate
        mapped = QColor(p.base if lum > 0.97 else (p.window if lum > 0.8 else p.alternate))
    else:
        # pale/pastel tint → dark tint of the same hue (paler stays a bit lighter)
        mapped = _with_lightness(c, 0.12 + (lightness - 0.6) * 0.3, 0.45)
    mapped.setAlpha(c.alpha())
    return mapped


def _map_text(c: QColor, p: _Palette) -> QColor:
    lum = _luminance(c)
    if lum >= 0.35:
        return c  # light enough to read on dark
    if _saturation(c) < 0.15:
        mapped = QColor(p.text if lum < 0.1 else p.muted)
    else:
        mapped = _with_lightness(c, 0.72, 0.8)  # dark blue/green/red → light variant
    mapped.setAlpha(c.alpha())
    return mapped


def _map_border(c: QColor, p: _Palette) -> QColor:
    if _luminance(c) > 0.45 and _saturation(c) < 0.2:
        mapped = QColor(p.border)
        mapped.setAlpha(c.alpha())
        return mapped
    return c


def is_dark_theme(theme) -> bool:
    try:
        return _luminance(QColor(theme.window_bg)) < 0.2
    except Exception:
        return False


def adapt_stylesheet(css: str, theme) -> str:
    """``css`` with its colours made readable on ``theme`` (a dark theme)."""
    if not css:
        return css
    p = _Palette(theme)

    def decl(m):
        prop, sep, value = m.group(1), m.group(2), m.group(3)
        low = prop.lower()
        if "background" in low:
            fn = _map_background
        elif low.startswith("border") or low.endswith("border-color"):
            fn = _map_border
        elif low.endswith("color"):
            fn = _map_text
        else:
            return m.group(0)

        def tok(t):
            c = _parse(t.group(0))
            if c is None:
                return t.group(0)
            mapped = fn(c, p)
            return t.group(0) if mapped.rgba() == c.rgba() else _fmt(mapped)
        return prop + sep + _COLOR.sub(tok, value)

    return _DECL.sub(decl, css)


def _adapt_widget(w: QWidget, theme, dark: bool) -> None:
    css = w.styleSheet()
    original = w.property(_ORIGINAL)
    adapted = w.property(_ADAPTED)
    if original is not None and css == adapted:
        if not dark:  # back to a light theme: restore
            w.setStyleSheet(original)
            w.setProperty(_ADAPTED, None)
            w.setProperty(_ORIGINAL, None)
        return
    if not dark or not css:
        return
    # First time, or the app set a new (light) stylesheet since we adapted it
    new = adapt_stylesheet(css, theme)
    w.setProperty(_ORIGINAL, css)
    w.setProperty(_ADAPTED, new)
    if new != css:
        w.setStyleSheet(new)


def apply_to_tree(root: QWidget, theme) -> None:
    """Adapt (or restore) ``root`` and every widget below it."""
    dark = is_dark_theme(theme)
    for w in [root] + root.findChildren(QWidget):
        try:
            _adapt_widget(w, theme, dark)
        except RuntimeError:
            continue  # deleted while we walked


class DarkStyleAdapter(QObject):
    """Application event filter: adapts widgets as they are shown, so dialogs
    and lazily built tabs are covered too. ``get_theme()`` returns the current
    theme object."""

    def __init__(self, get_theme, parent=None):
        super().__init__(parent)
        self._get_theme = get_theme

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.Show and isinstance(obj, QWidget):
            try:
                theme = self._get_theme()
                if theme is not None and is_dark_theme(theme):
                    _adapt_widget(obj, theme, True)
                    if obj.isWindow():
                        apply_to_tree(obj, theme)
            except Exception:
                pass
        return False

    def refresh_all(self):
        """Re-run over every top-level window (after a theme change)."""
        theme = self._get_theme()
        if theme is None:
            return
        for top in QApplication.topLevelWidgets():
            apply_to_tree(top, theme)
