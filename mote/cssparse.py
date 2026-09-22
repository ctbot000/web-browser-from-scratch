"""Parsing CSS source into rules, and evaluating media queries."""

from __future__ import annotations

from .csstoken import serialize, tokenize
from .selectors import SelectorError, parse_selector_list


class Declaration:
    __slots__ = ("name", "tokens", "important")

    def __init__(self, name, tokens, important=False):
        self.name = name
        self.tokens = tokens
        self.important = important

    @property
    def value(self):
        return serialize(self.tokens)

    def __repr__(self):
        return "%s: %s%s" % (self.name, self.value,
                             " !important" if self.important else "")


class StyleRule:
    __slots__ = ("selectors", "declarations", "media")

    def __init__(self, selectors, declarations, media=None):
        self.selectors = selectors
        self.declarations = declarations
        self.media = media

    def __repr__(self):
        return "%s { %d declarations }" % (
            ", ".join(repr(s) for s in self.selectors), len(self.declarations))


class Stylesheet:
    def __init__(self, rules=None, imports=None, url=None, origin="author"):
        self.rules = rules or []
        self.imports = imports or []     # [(url_text, media_query)]
        self.url = url
        self.origin = origin             # user-agent | user | author

    def rules_for(self, media):
        for rule in self.rules:
            if rule.media is None or evaluate_media_query(rule.media, media):
                yield rule

    def __repr__(self):
        return "<Stylesheet %s %d rules>" % (self.url or "", len(self.rules))


def parse_stylesheet(source, url=None, origin="author"):
    tokens = tokenize(source)
    sheet = Stylesheet(url=url, origin=origin)
    _parse_rules(tokens, 0, sheet, media=None)
    return sheet


def parse_declaration_block(source):
    """Parse the contents of a ``style="..."`` attribute."""
    return _parse_declarations(tokenize(source))


def _skip_whitespace(tokens, i):
    while i < len(tokens) and tokens[i].kind == "whitespace":
        i += 1
    return i


def _matching_brace(tokens, i):
    """Given the index of a ``{``, return the index of its ``}``."""
    depth = 0
    while i < len(tokens):
        kind = tokens[i].kind
        if kind == "{":
            depth += 1
        elif kind == "}":
            depth -= 1
            if depth == 0:
                return i
        elif kind == "eof":
            break
        i += 1
    return len(tokens) - 1


def _parse_rules(tokens, i, sheet, media):
    while i < len(tokens) and tokens[i].kind != "eof":
        i = _skip_whitespace(tokens, i)
        if i >= len(tokens) or tokens[i].kind == "eof":
            break
        token = tokens[i]

        if token.kind == "}":
            i += 1
            continue

        if token.kind == "at-keyword":
            i = _parse_at_rule(tokens, i, sheet, media)
            continue

        prelude_start = i
        while i < len(tokens) and tokens[i].kind not in ("{", "eof"):
            i += 1
        if i >= len(tokens) or tokens[i].kind == "eof":
            break
        close = _matching_brace(tokens, i)
        prelude = tokens[prelude_start:i]
        body = tokens[i + 1:close]
        try:
            selectors = parse_selector_list(prelude)
        except SelectorError:
            selectors = None
        if selectors:
            declarations = _parse_declarations(body)
            if declarations:
                sheet.rules.append(StyleRule(selectors, declarations, media))
        i = close + 1
    return i


def _parse_at_rule(tokens, i, sheet, media):
    name = tokens[i].value
    start = i
    i += 1
    prelude_start = i
    while i < len(tokens) and tokens[i].kind not in ("{", "semicolon", "eof"):
        i += 1
    prelude = serialize(tokens[prelude_start:i]).strip()

    if i < len(tokens) and tokens[i].kind == "semicolon":
        if name == "import":
            target, _, rest = _split_import(prelude)
            if target:
                sheet.imports.append((target, rest or media))
        return i + 1

    if i >= len(tokens) or tokens[i].kind == "eof":
        return len(tokens)

    close = _matching_brace(tokens, i)
    body = tokens[i + 1:close]

    if name == "media":
        combined = prelude if media is None else "%s and %s" % (media, prelude)
        nested = Stylesheet(url=sheet.url, origin=sheet.origin)
        _parse_rules(body + [tokens[-1]], 0, nested, combined)
        sheet.rules.extend(nested.rules)
        sheet.imports.extend(nested.imports)
    elif name == "supports":
        # We cannot evaluate feature queries, so take the contents: a page
        # that guards modern CSS this way still renders its base layout.
        nested = Stylesheet(url=sheet.url, origin=sheet.origin)
        _parse_rules(body + [tokens[-1]], 0, nested, media)
        sheet.rules.extend(nested.rules)
    # @font-face, @keyframes, @page and friends are parsed and dropped.
    return close + 1


def _split_import(prelude):
    prelude = prelude.strip()
    if prelude.startswith("url(") and prelude.endswith(")"):
        return prelude[4:-1].strip("\"' "), None, None
    if prelude[:1] in "\"'":
        quote = prelude[0]
        end = prelude.find(quote, 1)
        if end < 0:
            return None, None, None
        return prelude[1:end], None, prelude[end + 1:].strip() or None
    parts = prelude.split(None, 1)
    if not parts:
        return None, None, None
    target = parts[0]
    if target.startswith("url(") and target.endswith(")"):
        target = target[4:-1].strip("\"' ")
    return target, None, (parts[1] if len(parts) > 1 else None)


def _parse_declarations(tokens):
    declarations = []
    i = 0
    while i < len(tokens):
        i = _skip_whitespace(tokens, i)
        if i >= len(tokens) or tokens[i].kind == "eof":
            break
        if tokens[i].kind == "semicolon":
            i += 1
            continue
        if tokens[i].kind != "ident":
            i = _skip_to_semicolon(tokens, i)
            continue
        name = tokens[i].value.lower()
        i = _skip_whitespace(tokens, i + 1)
        if i >= len(tokens) or tokens[i].kind != "colon":
            i = _skip_to_semicolon(tokens, i)
            continue
        i += 1
        value_start = i
        depth = 0
        while i < len(tokens):
            kind = tokens[i].kind
            if kind in ("(", "function", "["):
                depth += 1
            elif kind in (")", "]"):
                depth -= 1
            elif kind == "semicolon" and depth <= 0:
                break
            elif kind in ("eof", "}"):
                break
            i += 1
        value_tokens = tokens[value_start:i]
        important = False
        trimmed = [t for t in value_tokens if t.kind != "whitespace"]
        if len(trimmed) >= 2 and trimmed[-1].kind == "ident" and \
                trimmed[-1].value.lower() == "important" and \
                trimmed[-2].is_delim("!"):
            important = True
            cut = value_tokens.index(trimmed[-2])
            value_tokens = value_tokens[:cut]
        value_tokens = _trim(value_tokens)
        if value_tokens:
            declarations.append(Declaration(name, value_tokens, important))
        if i < len(tokens) and tokens[i].kind == "semicolon":
            i += 1
    return declarations


def _skip_to_semicolon(tokens, i):
    depth = 0
    while i < len(tokens):
        kind = tokens[i].kind
        if kind in ("(", "function", "[", "{"):
            depth += 1
        elif kind in (")", "]", "}"):
            depth -= 1
            if depth < 0:
                return i
        elif kind == "semicolon" and depth <= 0:
            return i + 1
        elif kind == "eof":
            break
        i += 1
    return i


def _trim(tokens):
    start, end = 0, len(tokens)
    while start < end and tokens[start].kind == "whitespace":
        start += 1
    while end > start and tokens[end - 1].kind == "whitespace":
        end -= 1
    return tokens[start:end]


# ------------------------------------------------------------ media queries

class MediaContext:
    """What a media query is evaluated against."""

    def __init__(self, width=800, height=600, media_type="screen",
                 color=True, prefers_dark=False):
        self.width = width
        self.height = height
        self.media_type = media_type
        self.color = color
        self.prefers_dark = prefers_dark


def evaluate_media_query(query, context):
    if not query:
        return True
    if context is None:
        context = MediaContext()
    return any(_evaluate_one(part, context) for part in query.split(","))


def _evaluate_one(query, context):
    query = query.strip().lower()
    if not query:
        return True
    negated = False
    if query.startswith("not "):
        negated = True
        query = query[4:].strip()
    elif query.startswith("only "):
        query = query[5:].strip()
    result = all(_evaluate_term(term.strip(), context)
                 for term in _split_and(query))
    return not result if negated else result


def _split_and(query):
    parts, depth, current = [], 0, []
    i = 0
    while i < len(query):
        if query[i] == "(":
            depth += 1
        elif query[i] == ")":
            depth -= 1
        if depth == 0 and query[i:i + 5] == " and ":
            parts.append("".join(current))
            current = []
            i += 5
            continue
        current.append(query[i])
        i += 1
    parts.append("".join(current))
    return [p for p in parts if p.strip()]


def _evaluate_term(term, context):
    if not term.startswith("("):
        if term in ("all", "screen"):
            return context.media_type in ("screen", "all")
        if term in ("print", "speech", "tty", "tv", "projection", "handheld"):
            return context.media_type == term
        return False
    inner = term.strip("()").strip()
    if ":" not in inner:
        feature, value = inner, None
    else:
        feature, _, value = inner.partition(":")
        feature, value = feature.strip(), value.strip()

    if feature in ("min-width", "max-width", "width",
                   "min-device-width", "max-device-width", "device-width"):
        pixels = _length_px(value)
        if pixels is None:
            return False
        actual = context.width
        if feature.endswith("width") and feature.startswith("min"):
            return actual >= pixels
        if feature.startswith("max"):
            return actual <= pixels
        return abs(actual - pixels) < 0.5
    if feature in ("min-height", "max-height", "height"):
        pixels = _length_px(value)
        if pixels is None:
            return False
        if feature.startswith("min"):
            return context.height >= pixels
        if feature.startswith("max"):
            return context.height <= pixels
        return abs(context.height - pixels) < 0.5
    if feature == "prefers-color-scheme":
        return value == ("dark" if context.prefers_dark else "light")
    if feature in ("color", "any-hover", "hover", "pointer"):
        return True
    if feature == "orientation":
        landscape = context.width >= context.height
        return value == ("landscape" if landscape else "portrait")
    return False


def _length_px(text):
    if not text:
        return None
    text = text.strip().lower()
    for unit, factor in (("px", 1.0), ("em", 16.0), ("rem", 16.0),
                         ("pt", 96.0 / 72.0), ("cm", 96 / 2.54), ("in", 96.0)):
        if text.endswith(unit):
            try:
                return float(text[:-len(unit)]) * factor
            except ValueError:
                return None
    try:
        return float(text)
    except ValueError:
        return None
