"""Turning CSS component values into numbers and colours."""

from __future__ import annotations

_NAMED_COLOR_SOURCE = """
aliceblue f0f8ff antiquewhite faebd7 aqua 00ffff aquamarine 7fffd4 azure f0ffff
beige f5f5dc bisque ffe4c4 black 000000 blanchedalmond ffebcd blue 0000ff
blueviolet 8a2be2 brown a52a2a burlywood deb887 cadetblue 5f9ea0 chartreuse 7fff00
chocolate d2691e coral ff7f50 cornflowerblue 6495ed cornsilk fff8dc crimson dc143c
cyan 00ffff darkblue 00008b darkcyan 008b8b darkgoldenrod b8860b darkgray a9a9a9
darkgreen 006400 darkgrey a9a9a9 darkkhaki bdb76b darkmagenta 8b008b
darkolivegreen 556b2f darkorange ff8c00 darkorchid 9932cc darkred 8b0000
darksalmon e9967a darkseagreen 8fbc8f darkslateblue 483d8b darkslategray 2f4f4f
darkslategrey 2f4f4f darkturquoise 00ced1 darkviolet 9400d3 deeppink ff1493
deepskyblue 00bfff dimgray 696969 dimgrey 696969 dodgerblue 1e90ff firebrick b22222
floralwhite fffaf0 forestgreen 228b22 fuchsia ff00ff gainsboro dcdcdc
ghostwhite f8f8ff gold ffd700 goldenrod daa520 gray 808080 green 008000
greenyellow adff2f grey 808080 honeydew f0fff0 hotpink ff69b4 indianred cd5c5c
indigo 4b0082 ivory fffff0 khaki f0e68c lavender e6e6fa lavenderblush fff0f5
lawngreen 7cfc00 lemonchiffon fffacd lightblue add8e6 lightcoral f08080
lightcyan e0ffff lightgoldenrodyellow fafad2 lightgray d3d3d3 lightgreen 90ee90
lightgrey d3d3d3 lightpink ffb6c1 lightsalmon ffa07a lightseagreen 20b2aa
lightskyblue 87cefa lightslategray 778899 lightslategrey 778899
lightsteelblue b0c4de lightyellow ffffe0 lime 00ff00 limegreen 32cd32 linen faf0e6
magenta ff00ff maroon 800000 mediumaquamarine 66cdaa mediumblue 0000cd
mediumorchid ba55d3 mediumpurple 9370db mediumseagreen 3cb371
mediumslateblue 7b68ee mediumspringgreen 00fa9a mediumturquoise 48d1cc
mediumvioletred c71585 midnightblue 191970 mintcream f5fffa mistyrose ffe4e1
moccasin ffe4b5 navajowhite ffdead navy 000080 oldlace fdf5e6 olive 808000
olivedrab 6b8e23 orange ffa500 orangered ff4500 orchid da70d6
palegoldenrod eee8aa palegreen 98fb98 paleturquoise afeeee palevioletred db7093
papayawhip ffefd5 peachpuff ffdab9 peru cd853f pink ffc0cb plum dda0dd
powderblue b0e0e6 purple 800080 rebeccapurple 663399 red ff0000 rosybrown bc8f8f
royalblue 4169e1 saddlebrown 8b4513 salmon fa8072 sandybrown f4a460 seagreen 2e8b57
seashell fff5ee sienna a0522d silver c0c0c0 skyblue 87ceeb slateblue 6a5acd
slategray 708090 slategrey 708090 snow fffafa springgreen 00ff7f steelblue 4682b4
tan d2b48c teal 008080 thistle d8bfd8 tomato ff6347 turquoise 40e0d0 violet ee82ee
wheat f5deb3 white ffffff whitesmoke f5f5f5 yellow ffff00 yellowgreen 9acd32
"""

_parts = _NAMED_COLOR_SOURCE.split()
NAMED_COLORS = {
    _parts[i]: (int(_parts[i + 1][0:2], 16), int(_parts[i + 1][2:4], 16),
                int(_parts[i + 1][4:6], 16), 1.0)
    for i in range(0, len(_parts), 2)
}
NAMED_COLORS["transparent"] = (0, 0, 0, 0.0)
del _parts

TRANSPARENT = (0, 0, 0, 0.0)
BLACK = (0, 0, 0, 1.0)
WHITE = (255, 255, 255, 1.0)

# Absolute unit conversions, with the CSS reference pixel as the base.
ABSOLUTE_UNITS = {
    "px": 1.0,
    "pt": 96.0 / 72.0,
    "pc": 16.0,
    "in": 96.0,
    "cm": 96.0 / 2.54,
    "mm": 96.0 / 25.4,
    "q": 96.0 / 101.6,
}

ABSOLUTE_FONT_SIZES = {
    "xx-small": 9.0, "x-small": 10.0, "small": 13.0, "medium": 16.0,
    "large": 18.0, "x-large": 24.0, "xx-large": 32.0, "xxx-large": 48.0,
}


def clamp_byte(value):
    return 0 if value < 0 else (255 if value > 255 else int(round(value)))


def parse_color(text, current_color=None):
    """Parse any colour syntax we support; ``None`` when it is not a colour."""
    if not text:
        return None
    text = text.strip()
    lowered = text.lower()

    if lowered == "currentcolor":
        return current_color
    if lowered in NAMED_COLORS:
        return NAMED_COLORS[lowered]

    if text.startswith("#"):
        digits = text[1:]
        if not all(c in "0123456789abcdefABCDEF" for c in digits):
            return None
        if len(digits) == 3 or len(digits) == 4:
            values = [int(c * 2, 16) for c in digits]
        elif len(digits) == 6 or len(digits) == 8:
            values = [int(digits[i:i + 2], 16) for i in range(0, len(digits), 2)]
        else:
            return None
        alpha = values[3] / 255.0 if len(values) == 4 else 1.0
        return (values[0], values[1], values[2], alpha)

    if "(" in lowered and lowered.endswith(")"):
        name, _, argument = lowered.partition("(")
        name = name.strip()
        argument = argument[:-1]
        pieces = [p.strip() for p in argument.replace("/", " ").replace(",", " ").split()]
        if not pieces:
            return None
        try:
            if name in ("rgb", "rgba"):
                channels = [_channel(p) for p in pieces[:3]]
                alpha = _alpha(pieces[3]) if len(pieces) > 3 else 1.0
                if any(c is None for c in channels):
                    return None
                return (channels[0], channels[1], channels[2], alpha)
            if name in ("hsl", "hsla"):
                hue = _angle(pieces[0])
                saturation = _percent(pieces[1])
                lightness = _percent(pieces[2])
                if None in (hue, saturation, lightness):
                    return None
                alpha = _alpha(pieces[3]) if len(pieces) > 3 else 1.0
                red, green, blue = hsl_to_rgb(hue, saturation, lightness)
                return (red, green, blue, alpha)
        except (ValueError, IndexError):
            return None
    return None


def _channel(text):
    text = text.strip()
    if text.endswith("%"):
        return clamp_byte(float(text[:-1]) * 255.0 / 100.0)
    try:
        return clamp_byte(float(text))
    except ValueError:
        return None


def _alpha(text):
    text = text.strip()
    try:
        value = float(text[:-1]) / 100.0 if text.endswith("%") else float(text)
    except ValueError:
        return 1.0
    return 0.0 if value < 0 else (1.0 if value > 1 else value)


def _percent(text):
    text = text.strip()
    try:
        return float(text[:-1]) / 100.0 if text.endswith("%") else float(text)
    except ValueError:
        return None


def _angle(text):
    text = text.strip()
    for unit, factor in (("deg", 1.0), ("grad", 0.9), ("rad", 57.29577951308232),
                         ("turn", 360.0)):
        if text.endswith(unit):
            return float(text[:-len(unit)]) * factor
    return float(text)


def hsl_to_rgb(hue, saturation, lightness):
    hue = (hue % 360.0) / 360.0
    saturation = max(0.0, min(1.0, saturation))
    lightness = max(0.0, min(1.0, lightness))
    if saturation == 0:
        value = clamp_byte(lightness * 255)
        return value, value, value
    q = lightness * (1 + saturation) if lightness < 0.5 else \
        lightness + saturation - lightness * saturation
    p = 2 * lightness - q
    return tuple(clamp_byte(_hue_to_channel(p, q, hue + offset) * 255)
                 for offset in (1.0 / 3.0, 0.0, -1.0 / 3.0))


def _hue_to_channel(p, q, t):
    t = t % 1.0
    if t < 1.0 / 6.0:
        return p + (q - p) * 6 * t
    if t < 0.5:
        return q
    if t < 2.0 / 3.0:
        return p + (q - p) * (2.0 / 3.0 - t) * 6
    return p


def blend(top, bottom):
    """Composite a translucent colour over an opaque one."""
    if top is None:
        return bottom
    alpha = top[3]
    if alpha >= 1.0:
        return (top[0], top[1], top[2], 1.0)
    if alpha <= 0.0:
        return bottom
    return tuple(
        [clamp_byte(top[i] * alpha + bottom[i] * (1 - alpha)) for i in range(3)]
        + [1.0])


def to_hex(color):
    if color is None:
        return "#000000"
    return "#%02x%02x%02x" % (clamp_byte(color[0]), clamp_byte(color[1]),
                              clamp_byte(color[2]))


class Lengths:
    """The context a length resolves against."""

    __slots__ = ("font_size", "root_font_size", "viewport_width",
                 "viewport_height", "percent_base")

    def __init__(self, font_size=16.0, root_font_size=16.0,
                 viewport_width=800.0, viewport_height=600.0, percent_base=None):
        self.font_size = font_size
        self.root_font_size = root_font_size
        self.viewport_width = viewport_width
        self.viewport_height = viewport_height
        self.percent_base = percent_base


def parse_length(text, context, allow_percent=True):
    """Resolve a length to pixels. Returns ``None`` if it is not a length."""
    if text is None:
        return None
    text = str(text).strip().lower()
    if not text:
        return None
    if text == "0":
        return 0.0
    if text.startswith("calc(") and text.endswith(")"):
        return _evaluate_calc(text[5:-1], context, allow_percent)

    if text.endswith("%"):
        if not allow_percent:
            return None
        try:
            fraction = float(text[:-1]) / 100.0
        except ValueError:
            return None
        base = context.percent_base
        return None if base is None else fraction * base

    for unit, factor in ABSOLUTE_UNITS.items():
        if text.endswith(unit):
            try:
                return float(text[:-len(unit)]) * factor
            except ValueError:
                return None
    relative = {
        "em": context.font_size,
        "rem": context.root_font_size,
        "ex": context.font_size * 0.52,
        "ch": context.font_size * 0.5,
        "vw": context.viewport_width / 100.0,
        "vh": context.viewport_height / 100.0,
        "vmin": min(context.viewport_width, context.viewport_height) / 100.0,
        "vmax": max(context.viewport_width, context.viewport_height) / 100.0,
    }
    for unit in ("vmin", "vmax", "rem", "em", "ex", "ch", "vw", "vh"):
        if text.endswith(unit):
            try:
                return float(text[:-len(unit)]) * relative[unit]
            except ValueError:
                return None
    try:
        return float(text)          # unitless: treated as pixels
    except ValueError:
        return None


def _evaluate_calc(expression, context, allow_percent):
    """Evaluate a calc() expression over lengths, by hand."""
    tokens = _calc_tokens(expression)
    if tokens is None:
        return None
    try:
        value, index = _calc_sum(tokens, 0, context, allow_percent)
    except (ValueError, IndexError):
        return None
    return value if index == len(tokens) else None


def _calc_tokens(expression):
    out = []
    i = 0
    while i < len(expression):
        char = expression[i]
        if char.isspace():
            i += 1
            continue
        if char in "()+*/":
            out.append(char)
            i += 1
            continue
        if char == "-" and (not out or out[-1] in "(+-*/"):
            pass                       # part of a negative number
        elif char == "-":
            out.append("-")
            i += 1
            continue
        start = i
        if expression[i] in "+-":
            i += 1
        while i < len(expression) and (expression[i].isalnum() or
                                       expression[i] in ".%"):
            i += 1
        if i == start:
            return None
        out.append(expression[start:i])
    return out


def _calc_sum(tokens, i, context, allow_percent):
    value, i = _calc_product(tokens, i, context, allow_percent)
    while i < len(tokens) and tokens[i] in "+-":
        operator = tokens[i]
        right, i = _calc_product(tokens, i + 1, context, allow_percent)
        value = value + right if operator == "+" else value - right
    return value, i


def _calc_product(tokens, i, context, allow_percent):
    value, i = _calc_atom(tokens, i, context, allow_percent)
    while i < len(tokens) and tokens[i] in "*/":
        operator = tokens[i]
        right, i = _calc_atom(tokens, i + 1, context, allow_percent)
        if operator == "*":
            value *= right
        else:
            if right == 0:
                raise ValueError("division by zero in calc()")
            value /= right
    return value, i


def _calc_atom(tokens, i, context, allow_percent):
    if i >= len(tokens):
        raise ValueError("truncated calc()")
    token = tokens[i]
    if token == "(":
        value, i = _calc_sum(tokens, i + 1, context, allow_percent)
        if i >= len(tokens) or tokens[i] != ")":
            raise ValueError("unbalanced calc()")
        return value, i + 1
    resolved = parse_length(token, context, allow_percent)
    if resolved is None:
        raise ValueError("not a length: %r" % token)
    return resolved, i + 1
