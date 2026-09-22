"""The cascade: matching rules to elements and computing final values."""

from __future__ import annotations

import os

from .cssparse import (Declaration, MediaContext, parse_declaration_block,
                       parse_stylesheet)
from .dom import Element, Text
from .selectors import parse_selector_list
from .values import (ABSOLUTE_FONT_SIZES, BLACK, Lengths, parse_color,
                     parse_length)

INHERITED = {
    "azimuth", "border-collapse", "border-spacing", "caption-side", "color",
    "cursor", "direction", "empty-cells", "font-family", "font-size",
    "font-style", "font-variant", "font-weight", "letter-spacing",
    "line-height", "list-style-image", "list-style-position",
    "list-style-type", "orphans", "quotes", "text-align", "text-indent",
    "text-transform", "visibility", "white-space", "widows", "word-spacing",
    "word-break", "overflow-wrap", "text-decoration", "font",
}

INITIAL = {
    "display": "inline",
    "position": "static",
    "float": "none",
    "clear": "none",
    "color": "#000000",
    "background-color": "transparent",
    "background-image": "none",
    "font-family": "serif",
    "font-size": "16px",
    "font-style": "normal",
    "font-weight": "normal",
    "font-variant": "normal",
    "line-height": "normal",
    "text-align": "start",
    "text-decoration": "none",
    "text-indent": "0",
    "text-transform": "none",
    "vertical-align": "baseline",
    "white-space": "normal",
    "letter-spacing": "normal",
    "word-spacing": "normal",
    "list-style-type": "disc",
    "list-style-position": "outside",
    "width": "auto",
    "height": "auto",
    "min-width": "0",
    "min-height": "0",
    "max-width": "none",
    "max-height": "none",
    "box-sizing": "content-box",
    "overflow": "visible",
    "visibility": "visible",
    "opacity": "1",
    "border-collapse": "separate",
    "border-spacing": "0",
    "cursor": "auto",
    "direction": "ltr",
}
for _side in ("top", "right", "bottom", "left"):
    INITIAL["margin-" + _side] = "0"
    INITIAL["padding-" + _side] = "0"
    INITIAL["border-%s-width" % _side] = "0"
    INITIAL["border-%s-style" % _side] = "none"
    INITIAL["border-%s-color" % _side] = "currentcolor"
    INITIAL[_side] = "auto"
del _side

SIDES = ("top", "right", "bottom", "left")
BORDER_STYLES = {"none", "hidden", "dotted", "dashed", "solid", "double",
                 "groove", "ridge", "inset", "outset"}
BORDER_WIDTH_KEYWORDS = {"thin": 1.0, "medium": 3.0, "thick": 5.0}

_ORIGIN_ORDER = {"user-agent": 0, "user": 1, "author": 2}


class Style:
    """Computed style for one element."""

    __slots__ = ("props", "font_size", "color", "element")

    def __init__(self, props, font_size, color, element=None):
        self.props = props
        self.font_size = font_size
        self.color = color
        self.element = element

    def get(self, name, default=None):
        return self.props.get(name, default)

    def __getitem__(self, name):
        return self.props[name]

    def __contains__(self, name):
        return name in self.props

    # -- typed accessors --------------------------------------------------

    def keyword(self, name, default=""):
        return (self.props.get(name) or default).strip().lower()

    @property
    def display(self):
        return self.keyword("display", "inline")

    def color_of(self, name, default=None):
        raw = self.props.get(name)
        if raw is None:
            return default
        parsed = parse_color(raw, self.color)
        return default if parsed is None else parsed

    def length(self, name, context, default=0.0, allow_percent=True):
        raw = self.props.get(name)
        if raw is None:
            return default
        value = parse_length(raw, context, allow_percent)
        return default if value is None else value

    def border_width(self, side, context):
        if self.keyword("border-%s-style" % side, "none") in ("none", "hidden"):
            return 0.0
        raw = (self.props.get("border-%s-width" % side) or "medium").strip().lower()
        if raw in BORDER_WIDTH_KEYWORDS:
            return BORDER_WIDTH_KEYWORDS[raw]
        value = parse_length(raw, context, allow_percent=False)
        return 3.0 if value is None else value

    def line_height(self, context):
        raw = self.keyword("line-height", "normal")
        if raw in ("normal", "inherit", ""):
            return self.font_size * 1.2
        try:
            return float(raw) * self.font_size       # unitless multiplier
        except ValueError:
            pass
        local = Lengths(self.font_size, context.root_font_size,
                        context.viewport_width, context.viewport_height,
                        self.font_size)
        value = parse_length(raw, local)
        return self.font_size * 1.2 if value is None else value

    @property
    def font(self):
        weight = self.keyword("font-weight", "normal")
        bold = weight in ("bold", "bolder", "600", "700", "800", "900")
        italic = self.keyword("font-style", "normal") in ("italic", "oblique")
        family = self.keyword("font-family", "serif")
        return (family, self.font_size, bold, italic)

    def __repr__(self):
        return "Style(%s, %gpx)" % (self.display, self.font_size)


# ------------------------------------------------------------------- engine

def _load_ua_stylesheet():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ua.css")
    with open(path, "r", encoding="utf-8") as handle:
        return parse_stylesheet(handle.read(), origin="user-agent")


_UA_SHEET = None


def user_agent_stylesheet():
    global _UA_SHEET
    if _UA_SHEET is None:
        _UA_SHEET = _load_ua_stylesheet()
    return _UA_SHEET


class StyleEngine:
    def __init__(self, sheets, media=None, context=None):
        self.sheets = list(sheets)
        self.media = media or MediaContext()
        self.context = context or {}
        self._matchers = self._index()

    def _index(self):
        """Flatten every sheet into (origin, order, selector, declarations)."""
        flat = []
        order = 0
        for sheet in self.sheets:
            origin_rank = _ORIGIN_ORDER.get(sheet.origin, 2)
            for rule in sheet.rules_for(self.media):
                for selector in rule.selectors:
                    flat.append((origin_rank, order, selector, rule.declarations))
                    order += 1
        return flat

    def compute(self, document, viewport_width=800.0, viewport_height=600.0):
        """Compute styles for the whole tree, top down."""
        root = document.document_element
        if root is None:
            return {}
        styles = {}
        base = Style(dict(INITIAL), 16.0, BLACK)
        self._walk(root, base, styles, viewport_width, viewport_height, 16.0)
        return styles

    def _walk(self, element, parent_style, styles, vw, vh, root_font_size):
        style = self.compute_one(element, parent_style, vw, vh, root_font_size)
        styles[id(element)] = style
        element._style_cache = style
        if element.parent is not None and element.parent.parent is None:
            root_font_size = style.font_size
        for child in element.children:
            if isinstance(child, Element):
                self._walk(child, style, styles, vw, vh, root_font_size)
        return style

    # -- the cascade for one element --------------------------------------

    def compute_one(self, element, parent_style, vw=800.0, vh=600.0,
                    root_font_size=16.0):
        declarations = []       # (important, origin, specificity, order, decl)
        for origin_rank, order, selector, block in self._matchers:
            if selector.pseudo_element:
                continue
            if selector.matches(element, self.context):
                specificity = selector.specificity
                for declaration in block:
                    declarations.append((declaration.important, origin_rank,
                                         specificity, order, declaration))

        inline = element.get("style")
        if inline:
            for order, declaration in enumerate(parse_declaration_block(inline)):
                declarations.append((declaration.important, 3, (1, 0, 0),
                                     10 ** 6 + order, declaration))

        for declaration in presentational_hints(element):
            declarations.append((False, 0, (0, 0, 0), -1, declaration))

        declarations.sort(key=_cascade_key)

        specified = {}
        for _, _, _, _, declaration in declarations:
            _apply(specified, declaration.name, declaration.value)

        return self._computed(element, specified, parent_style, vw, vh,
                              root_font_size)

    def _computed(self, element, specified, parent_style, vw, vh,
                  root_font_size):
        props = {}
        for name in INHERITED:
            if name in parent_style.props:
                props[name] = parent_style.props[name]
        for name, value in INITIAL.items():
            props.setdefault(name, value)

        # font-size first: every other em-based length depends on it.
        font_size = _resolve_font_size(specified.get("font-size"),
                                       parent_style.font_size, root_font_size,
                                       vw, vh)
        props["font-size"] = "%gpx" % font_size
        context = Lengths(font_size, root_font_size, vw, vh, None)

        for name, value in specified.items():
            if name == "font-size":
                continue
            lowered = value.strip().lower()
            if lowered == "inherit":
                if name in parent_style.props:
                    props[name] = parent_style.props[name]
                continue
            if lowered == "initial":
                props[name] = INITIAL.get(name, "")
                continue
            if lowered == "unset":
                if name in INHERITED and name in parent_style.props:
                    props[name] = parent_style.props[name]
                else:
                    props[name] = INITIAL.get(name, "")
                continue
            props[name] = value

        # Resolve em/rem-relative lengths now, so layout only sees pixels or
        # percentages.
        for name in list(props):
            if name in _LENGTH_PROPERTIES:
                props[name] = _to_absolute(props[name], context)

        color = parse_color(props.get("color", "#000000"),
                            parent_style.color) or parent_style.color
        props["color"] = "rgba(%d,%d,%d,%g)" % color

        for side in SIDES:
            key = "border-%s-color" % side
            raw = props.get(key, "currentcolor")
            if raw.strip().lower() == "currentcolor":
                props[key] = "rgba(%d,%d,%d,%g)" % color

        display = props.get("display", "inline").strip().lower()
        if display not in ("none",) and element.tag in ("html", "body"):
            pass
        # An absolutely positioned or floated box is blockified, as in CSS 2.1.
        if props.get("float", "none").strip().lower() != "none" and \
                display in ("inline", "inline-block", "table-cell"):
            props["display"] = "block"

        return Style(props, font_size, color, element)


_LENGTH_PROPERTIES = set()
for _side in SIDES:
    _LENGTH_PROPERTIES.add("margin-" + _side)
    _LENGTH_PROPERTIES.add("padding-" + _side)
    _LENGTH_PROPERTIES.add("border-%s-width" % _side)
    _LENGTH_PROPERTIES.add(_side)
_LENGTH_PROPERTIES.update({"width", "height", "min-width", "min-height",
                           "max-width", "max-height", "text-indent",
                           "border-spacing", "letter-spacing", "word-spacing"})
del _side


def _to_absolute(value, context):
    """Convert em/rem/vw lengths to px; leave percentages and keywords alone."""
    text = value.strip().lower()
    if not text or text.endswith("%") or text in ("auto", "none", "normal",
                                                  "inherit", "initial"):
        return value
    if text.endswith("px") or text.startswith("calc("):
        return value
    resolved = parse_length(text, context, allow_percent=False)
    if resolved is None:
        return value
    return "%gpx" % resolved


def _cascade_key(item):
    important, origin, specificity, order, _ = item
    # CSS 2.1 section 6.4.1, with important declarations flipping the origin
    # order so a user-agent !important rule wins.
    origin_weight = origin if not important else (10 - origin)
    return (important, origin_weight, specificity, order)


def _resolve_font_size(value, parent_size, root_size, vw, vh):
    if value is None:
        return parent_size
    text = value.strip().lower()
    if text in ABSOLUTE_FONT_SIZES:
        return ABSOLUTE_FONT_SIZES[text]
    if text == "larger":
        return parent_size * 1.2
    if text == "smaller":
        return parent_size / 1.2
    if text in ("inherit", "unset"):
        return parent_size
    if text == "initial":
        return 16.0
    context = Lengths(parent_size, root_size, vw, vh, parent_size)
    size = parse_length(text, context)
    if size is None or size <= 0:
        return parent_size
    return size


# -------------------------------------------------------- shorthand handling

def _apply(target, name, value):
    handler = _SHORTHANDS.get(name)
    if handler is None:
        target[name] = value
        return
    handler(target, value)


def _split_values(value):
    """Split on whitespace, but keep ``rgb(1, 2, 3)`` in one piece."""
    parts, depth, current = [], 0, []
    for char in value:
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        if char.isspace() and depth == 0:
            if current:
                parts.append("".join(current))
                current = []
            continue
        current.append(char)
    if current:
        parts.append("".join(current))
    return parts


def _expand_box(target, prefix, value, suffix=""):
    parts = _split_values(value)
    if not parts:
        return
    if len(parts) == 1:
        top = right = bottom = left = parts[0]
    elif len(parts) == 2:
        top = bottom = parts[0]
        right = left = parts[1]
    elif len(parts) == 3:
        top, right, bottom = parts[0], parts[1], parts[2]
        left = right
    else:
        top, right, bottom, left = parts[:4]
    for side, part in zip(SIDES, (top, right, bottom, left)):
        target["%s-%s%s" % (prefix, side, suffix)] = part


def _shorthand_margin(target, value):
    _expand_box(target, "margin", value)


def _shorthand_padding(target, value):
    _expand_box(target, "padding", value)


def _shorthand_border_width(target, value):
    _expand_box(target, "border", value, "-width")


def _shorthand_border_style(target, value):
    _expand_box(target, "border", value, "-style")


def _shorthand_border_color(target, value):
    _expand_box(target, "border", value, "-color")


def _parse_border_parts(value):
    width = style = color = None
    for part in _split_values(value):
        lowered = part.lower()
        if lowered in BORDER_STYLES:
            style = lowered
        elif lowered in BORDER_WIDTH_KEYWORDS or _looks_like_length(lowered):
            width = part
        elif parse_color(part) is not None or lowered == "currentcolor":
            color = part
    return width, style, color


def _looks_like_length(text):
    return bool(text) and (text[0].isdigit() or text[0] in ".-+") and \
        not text.endswith("%")


def _shorthand_border(target, value, sides=SIDES):
    width, style, color = _parse_border_parts(value)
    if value.strip().lower() in ("none", "0"):
        width, style = "0", "none"
    for side in sides:
        target["border-%s-width" % side] = width if width is not None else "medium"
        target["border-%s-style" % side] = style if style is not None else "none"
        if color is not None:
            target["border-%s-color" % side] = color


def _shorthand_border_side(side):
    def handler(target, value):
        _shorthand_border(target, value, sides=(side,))
    return handler


def _shorthand_background(target, value):
    parts = _split_values(value)
    for part in parts:
        lowered = part.lower()
        if lowered.startswith("url(") or lowered.startswith("linear-gradient("):
            target["background-image"] = part
        elif parse_color(part) is not None:
            target["background-color"] = part
        elif lowered in ("no-repeat", "repeat", "repeat-x", "repeat-y"):
            target["background-repeat"] = lowered
        elif lowered in ("fixed", "scroll", "local"):
            target["background-attachment"] = lowered
    if not parts:
        return
    if "background-color" not in target and value.strip().lower() == "none":
        target["background-color"] = "transparent"


def _shorthand_font(target, value):
    parts = _split_values(value)
    index = 0
    while index < len(parts):
        lowered = parts[index].lower()
        if lowered in ("italic", "oblique"):
            target["font-style"] = lowered
        elif lowered in ("bold", "bolder", "lighter", "normal") or \
                (lowered.isdigit() and len(lowered) == 3):
            if lowered != "normal":
                target["font-weight"] = lowered
        elif lowered in ("small-caps",):
            target["font-variant"] = lowered
        else:
            break
        index += 1
    if index < len(parts):
        size_part = parts[index]
        if "/" in size_part:
            size, _, line = size_part.partition("/")
            target["font-size"] = size
            target["line-height"] = line
        else:
            target["font-size"] = size_part
        index += 1
    if index < len(parts):
        target["font-family"] = " ".join(parts[index:])


def _shorthand_list_style(target, value):
    for part in _split_values(value):
        lowered = part.lower()
        if lowered in ("inside", "outside"):
            target["list-style-position"] = lowered
        elif lowered.startswith("url("):
            target["list-style-image"] = part
        elif lowered != "none" or "list-style-type" not in target:
            target["list-style-type"] = lowered


def _shorthand_overflow(target, value):
    parts = _split_values(value)
    target["overflow"] = parts[0]
    target["overflow-x"] = parts[0]
    target["overflow-y"] = parts[1] if len(parts) > 1 else parts[0]


def _shorthand_text_decoration(target, value):
    for part in _split_values(value):
        lowered = part.lower()
        if lowered in ("none", "underline", "overline", "line-through",
                       "blink"):
            target["text-decoration"] = lowered


def _shorthand_flex_flow(target, value):
    for part in _split_values(value):
        lowered = part.lower()
        if lowered in ("row", "row-reverse", "column", "column-reverse"):
            target["flex-direction"] = lowered
        elif lowered in ("wrap", "nowrap", "wrap-reverse"):
            target["flex-wrap"] = lowered


_SHORTHANDS = {
    "margin": _shorthand_margin,
    "padding": _shorthand_padding,
    "border-width": _shorthand_border_width,
    "border-style": _shorthand_border_style,
    "border-color": _shorthand_border_color,
    "border": _shorthand_border,
    "border-top": _shorthand_border_side("top"),
    "border-right": _shorthand_border_side("right"),
    "border-bottom": _shorthand_border_side("bottom"),
    "border-left": _shorthand_border_side("left"),
    "background": _shorthand_background,
    "font": _shorthand_font,
    "list-style": _shorthand_list_style,
    "overflow": _shorthand_overflow,
    "text-decoration": _shorthand_text_decoration,
    "flex-flow": _shorthand_flex_flow,
}


# --------------------------------------------------- presentational markup

_ALIGN_TAGS = {"div", "p", "h1", "h2", "h3", "h4", "h5", "h6", "td", "th",
               "tr", "table", "caption", "col", "thead", "tbody", "tfoot"}


def presentational_hints(element):
    """Old HTML attributes that behave like the lowest-priority CSS."""
    out = []
    tag = element.tag
    attrs = element.attrs

    def add(name, value):
        from .csstoken import tokenize
        out.append(Declaration(name, tokenize(value)[:-1]))

    if "bgcolor" in attrs:
        add("background-color", attrs["bgcolor"])
    if "text" in attrs and tag == "body":
        add("color", attrs["text"])
    if "color" in attrs and tag == "font":
        add("color", attrs["color"])
    if "face" in attrs and tag == "font":
        add("font-family", attrs["face"])
    if "align" in attrs:
        value = attrs["align"].lower()
        if tag == "img" and value in ("left", "right"):
            add("float", value)
        elif tag in _ALIGN_TAGS and value in ("left", "right", "center",
                                              "justify"):
            add("text-align", value)
        elif tag == "table" and value == "center":
            add("margin-left", "auto")
            add("margin-right", "auto")
    if "width" in attrs and tag in ("img", "table", "td", "th", "hr", "col",
                                    "video", "canvas", "iframe", "object"):
        add("width", _dimension(attrs["width"]))
    if "height" in attrs and tag in ("img", "table", "td", "th", "video",
                                     "canvas", "iframe", "object"):
        add("height", _dimension(attrs["height"]))
    if tag == "table":
        if "cellpadding" in attrs:
            add("--cell-padding", _dimension(attrs["cellpadding"]))
        if "cellspacing" in attrs:
            add("border-spacing", _dimension(attrs["cellspacing"]))
        if attrs.get("border") not in (None, "", "0"):
            add("border", "%spx outset #a0a0a0" % attrs["border"])
    if tag == "hr" and "size" in attrs:
        add("height", _dimension(attrs["size"]))
    if tag in ("td", "th") and "valign" in attrs:
        add("vertical-align", attrs["valign"])
    if tag == "body":
        for attribute, prop in (("leftmargin", "margin-left"),
                                ("topmargin", "margin-top"),
                                ("rightmargin", "margin-right"),
                                ("bottommargin", "margin-bottom")):
            if attribute in attrs:
                add(prop, _dimension(attrs[attribute]))
    return out


def _dimension(value):
    value = (value or "").strip()
    if value.endswith("%"):
        return value
    digits = "".join(c for c in value if c.isdigit() or c == ".")
    return (digits + "px") if digits else "auto"


# ------------------------------------------------------------- convenience

def collect_stylesheets(document, fetch=None, media=None, max_imports=8):
    """Gather the UA sheet plus every <style> and <link rel=stylesheet>."""
    sheets = [user_agent_stylesheet()]
    head_and_body = document
    for element in head_and_body.elements():
        if element.tag == "style":
            type_attr = (element.get("type") or "text/css").lower()
            if "css" not in type_attr:
                continue
            sheet = parse_stylesheet(element.text_content, url=document.url)
            sheets.append(sheet)
        elif element.tag == "link" and fetch is not None:
            rel = (element.get("rel") or "").lower().split()
            if "stylesheet" not in rel or not element.get("href"):
                continue
            if element.get("media") and media is not None:
                from .cssparse import evaluate_media_query
                if not evaluate_media_query(element.get("media"), media):
                    continue
            source, url = fetch(element.get("href"))
            if source is None:
                continue
            sheets.append(parse_stylesheet(source, url=url))

    # Follow @import chains, breadth first and bounded.
    if fetch is not None:
        pending = list(sheets)
        seen = set()
        while pending and max_imports > 0:
            sheet = pending.pop(0)
            for target, query in getattr(sheet, "imports", ()):
                if target in seen or max_imports <= 0:
                    continue
                seen.add(target)
                max_imports -= 1
                if query and media is not None:
                    from .cssparse import evaluate_media_query
                    if not evaluate_media_query(query, media):
                        continue
                source, url = fetch(target, base=sheet.url)
                if source is None:
                    continue
                imported = parse_stylesheet(source, url=url)
                sheets.append(imported)
                pending.append(imported)
    return sheets
