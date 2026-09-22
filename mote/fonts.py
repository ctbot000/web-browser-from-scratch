"""Text measurement.

Layout cannot place a word without knowing how wide it is, and the answer
depends on the font.  Two implementations are provided: one backed by real
font metrics from the windowing toolkit, and one backed by built-in width
tables so that layout also works headlessly and deterministically.
"""

from __future__ import annotations

import unicodedata

# Advance widths in 1/1000 em for the two core font families, in ASCII order
# from U+0020 to U+007E.  These are the standard PostScript core-font metrics.
_HELVETICA = (
    "278 278 355 556 556 889 667 191 333 333 389 584 278 333 278 278 "
    "556 556 556 556 556 556 556 556 556 556 278 278 584 584 584 556 "
    "1015 667 667 722 722 667 611 778 722 278 500 667 556 833 722 778 "
    "667 778 722 667 611 722 667 944 667 667 611 278 278 278 469 556 "
    "333 556 556 500 556 556 278 556 556 222 222 500 222 833 556 556 "
    "556 556 333 500 278 556 500 722 500 500 500 334 260 334 584"
)
_TIMES = (
    "250 333 408 500 500 833 778 180 333 333 500 564 250 333 250 278 "
    "500 500 500 500 500 500 500 500 500 500 278 278 564 564 564 444 "
    "921 722 667 667 722 611 556 722 722 333 389 722 611 889 722 722 "
    "556 722 667 556 611 722 722 944 722 722 611 333 278 333 469 500 "
    "333 444 500 444 500 444 333 500 500 278 278 500 278 778 500 500 "
    "500 500 333 389 278 500 500 722 500 500 444 480 200 480 541"
)

_WIDTH_TABLES = {
    "sans-serif": [int(w) for w in _HELVETICA.split()],
    "serif": [int(w) for w in _TIMES.split()],
}
_WIDTH_TABLES["monospace"] = [600] * len(_WIDTH_TABLES["serif"])

# How much bold and italic faces differ from the roman in average advance.
_BOLD_FACTOR = {"sans-serif": 1.075, "serif": 1.06, "monospace": 1.0}
_ITALIC_FACTOR = {"sans-serif": 1.0, "serif": 0.965, "monospace": 1.0}

_GENERIC = {
    "sans-serif": "sans-serif", "sans": "sans-serif", "helvetica": "sans-serif",
    "arial": "sans-serif", "verdana": "sans-serif", "tahoma": "sans-serif",
    "segoe ui": "sans-serif", "roboto": "sans-serif", "system-ui": "sans-serif",
    "-apple-system": "sans-serif", "inter": "sans-serif", "ui-sans-serif": "sans-serif",
    "serif": "serif", "times": "serif", "times new roman": "serif",
    "georgia": "serif", "garamond": "serif", "ui-serif": "serif",
    "monospace": "monospace", "mono": "monospace", "courier": "monospace",
    "courier new": "monospace", "consolas": "monospace", "menlo": "monospace",
    "monaco": "monospace", "ui-monospace": "monospace", "sf mono": "monospace",
}


def generic_family(family_list):
    """Reduce a CSS font-family list to one of our three generic families."""
    for name in (family_list or "").split(","):
        name = name.strip().strip("'\"").lower()
        if name in _GENERIC:
            return _GENERIC[name]
    return "serif"


def is_wide(char):
    """East Asian characters occupy a full em rather than about half."""
    return unicodedata.east_asian_width(char) in ("W", "F")


class Font:
    """The resolved font a run of text is drawn with."""

    __slots__ = ("family", "size", "bold", "italic", "generic")

    def __init__(self, family, size, bold=False, italic=False):
        self.family = family
        self.size = size
        self.bold = bold
        self.italic = italic
        self.generic = generic_family(family)

    @property
    def key(self):
        return (self.generic, round(self.size, 2), self.bold, self.italic)

    def __repr__(self):
        return "Font(%s %gpx%s%s)" % (self.generic, self.size,
                                      " bold" if self.bold else "",
                                      " italic" if self.italic else "")


class FontMetrics:
    """Interface layout uses to ask how big text is."""

    def measure(self, text, font):
        raise NotImplementedError

    def ascent(self, font):
        return font.size * 0.8

    def descent(self, font):
        return font.size * 0.2

    def line_spacing(self, font):
        return self.ascent(font) + self.descent(font)


class BuiltinMetrics(FontMetrics):
    """Measurement from the built-in width tables. No toolkit required."""

    def __init__(self):
        self._cache = {}

    def measure(self, text, font):
        if not text:
            return 0.0
        key = (text, font.key)
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        table = _WIDTH_TABLES[font.generic]
        factor = 1.0
        if font.bold:
            factor *= _BOLD_FACTOR[font.generic]
        if font.italic:
            factor *= _ITALIC_FACTOR[font.generic]
        total = 0
        for char in text:
            code = ord(char)
            if 0x20 <= code <= 0x7E:
                total += table[code - 0x20]
            elif char == "\t":
                total += table[0] * 8
            elif code < 0x20:
                continue
            elif is_wide(char):
                total += 1000
            elif unicodedata.combining(char):
                continue
            else:
                total += 500
        width = total * font.size * factor / 1000.0
        if len(self._cache) > 50000:
            self._cache.clear()
        self._cache[key] = width
        return width

    def ascent(self, font):
        return font.size * (0.75 if font.generic != "monospace" else 0.78)

    def descent(self, font):
        return font.size * (0.25 if font.generic != "monospace" else 0.22)


class TkMetrics(FontMetrics):
    """Measurement through Tk, so the GUI lays out with the real font."""

    def __init__(self, root=None):
        import tkinter.font as tkfont
        self._tkfont = tkfont
        self._root = root
        self._fonts = {}
        self._cache = {}

    def tk_font(self, font):
        key = font.key
        existing = self._fonts.get(key)
        if existing is not None:
            return existing
        family = {"serif": "Times", "sans-serif": "Helvetica",
                  "monospace": "Courier"}[font.generic]
        created = self._tkfont.Font(
            root=self._root, family=family, size=-max(1, int(round(font.size))),
            weight="bold" if font.bold else "normal",
            slant="italic" if font.italic else "roman")
        self._fonts[key] = created
        return created

    def measure(self, text, font):
        if not text:
            return 0.0
        key = (text, font.key)
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        width = float(self.tk_font(font).measure(text))
        if len(self._cache) > 50000:
            self._cache.clear()
        self._cache[key] = width
        return width

    def ascent(self, font):
        return float(self.tk_font(font).metrics("ascent"))

    def descent(self, font):
        return float(self.tk_font(font).metrics("descent"))


def font_for_style(style):
    family, size, bold, italic = style.font
    return Font(family, size, bold, italic)
