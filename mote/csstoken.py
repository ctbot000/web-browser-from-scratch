"""A CSS tokenizer following the shape of CSS Syntax Level 3."""

from __future__ import annotations

WHITESPACE = " \t\n\r\f"


class CSSToken:
    __slots__ = ("kind", "value", "unit", "start")

    def __init__(self, kind, value=None, unit=None, start=0):
        self.kind = kind
        self.value = value
        self.unit = unit
        self.start = start

    def __repr__(self):
        if self.unit:
            return "%s(%r%s)" % (self.kind, self.value, self.unit)
        return "%s(%r)" % (self.kind, self.value)

    def is_delim(self, char):
        return self.kind == "delim" and self.value == char


def _is_name_start(char):
    return char.isalpha() or char == "_" or ord(char) > 0x7F


def _is_name(char):
    return _is_name_start(char) or char.isdigit() or char == "-"


def tokenize(source):
    """Turn CSS source into a flat list of tokens."""
    tokens = []
    i = 0
    length = len(source)
    while i < length:
        char = source[i]

        if char in WHITESPACE:
            start = i
            while i < length and source[i] in WHITESPACE:
                i += 1
            tokens.append(CSSToken("whitespace", " ", start=start))
            continue

        if char == "/" and source[i + 1:i + 2] == "*":
            end = source.find("*/", i + 2)
            i = length if end < 0 else end + 2
            continue

        if char in "\"'":
            value, i = _consume_string(source, i)
            tokens.append(CSSToken("string", value, start=i))
            continue

        if char == "#":
            if i + 1 < length and (_is_name(source[i + 1]) or
                                   source[i + 1] == "\\"):
                name, i = _consume_name(source, i + 1)
                tokens.append(CSSToken("hash", name, start=i))
                continue
            tokens.append(CSSToken("delim", "#", start=i))
            i += 1
            continue

        if char == "@":
            if i + 1 < length and _is_name_start(source[i + 1]):
                name, i = _consume_name(source, i + 1)
                tokens.append(CSSToken("at-keyword", name.lower(), start=i))
                continue
            tokens.append(CSSToken("delim", "@", start=i))
            i += 1
            continue

        if char.isdigit() or (char in "+-." and i + 1 < length and
                              (source[i + 1].isdigit() or
                               (source[i + 1] == "." and
                                source[i + 2:i + 3].isdigit()))):
            token, i = _consume_numeric(source, i)
            tokens.append(token)
            continue

        if _is_name_start(char) or char == "-" or char == "\\":
            name, j = _consume_name(source, i)
            if name:
                if source[j:j + 1] == "(":
                    if name.lower() == "url":
                        token, i = _consume_url(source, j + 1)
                        tokens.append(token)
                        continue
                    tokens.append(CSSToken("function", name.lower(), start=i))
                    i = j + 1
                    continue
                tokens.append(CSSToken("ident", name, start=i))
                i = j
                continue

        simple = {"(": "(", ")": ")", "[": "[", "]": "]", "{": "{", "}": "}",
                  ",": "comma", ":": "colon", ";": "semicolon"}
        if char in simple:
            kind = simple[char]
            tokens.append(CSSToken(kind if len(kind) > 1 else kind,
                                   char, start=i))
            i += 1
            continue

        tokens.append(CSSToken("delim", char, start=i))
        i += 1

    tokens.append(CSSToken("eof", None, start=length))
    return tokens


def _consume_string(source, i):
    quote = source[i]
    i += 1
    out = []
    while i < len(source):
        char = source[i]
        if char == quote:
            return "".join(out), i + 1
        if char == "\\":
            if i + 1 < len(source):
                escaped, i = _consume_escape(source, i + 1)
                out.append(escaped)
                continue
            i += 1
            continue
        if char == "\n":
            return "".join(out), i          # bad string: stop at the newline
        out.append(char)
        i += 1
    return "".join(out), i


def _consume_escape(source, i):
    char = source[i]
    hexdigits = "0123456789abcdefABCDEF"
    if char in hexdigits:
        start = i
        while i < len(source) and i - start < 6 and source[i] in hexdigits:
            i += 1
        codepoint = int(source[start:i], 16)
        if i < len(source) and source[i] in WHITESPACE:
            i += 1
        if codepoint == 0 or codepoint > 0x10FFFF:
            return "�", i
        return chr(codepoint), i
    return char, i + 1


def _consume_name(source, i):
    out = []
    while i < len(source):
        char = source[i]
        if _is_name(char):
            out.append(char)
            i += 1
        elif char == "\\" and i + 1 < len(source):
            escaped, i = _consume_escape(source, i + 1)
            out.append(escaped)
        else:
            break
    return "".join(out), i


def _consume_numeric(source, i):
    start = i
    if source[i] in "+-":
        i += 1
    while i < len(source) and source[i].isdigit():
        i += 1
    if source[i:i + 1] == "." and source[i + 1:i + 2].isdigit():
        i += 1
        while i < len(source) and source[i].isdigit():
            i += 1
    if source[i:i + 1] in ("e", "E"):
        j = i + 1
        if source[j:j + 1] in "+-":
            j += 1
        if source[j:j + 1].isdigit():
            i = j
            while i < len(source) and source[i].isdigit():
                i += 1
    number = float(source[start:i])
    if source[i:i + 1] == "%":
        return CSSToken("percentage", number, "%", start), i + 1
    if i < len(source) and (_is_name_start(source[i]) or source[i] == "\\"):
        unit, i = _consume_name(source, i)
        return CSSToken("dimension", number, unit.lower(), start), i
    return CSSToken("number", number, None, start), i


def _consume_url(source, i):
    while i < len(source) and source[i] in WHITESPACE:
        i += 1
    if i < len(source) and source[i] in "\"'":
        value, i = _consume_string(source, i)
        while i < len(source) and source[i] in WHITESPACE:
            i += 1
        if i < len(source) and source[i] == ")":
            i += 1
        return CSSToken("url", value, start=i), i
    out = []
    while i < len(source) and source[i] != ")":
        if source[i] == "\\" and i + 1 < len(source):
            escaped, i = _consume_escape(source, i + 1)
            out.append(escaped)
            continue
        out.append(source[i])
        i += 1
    if i < len(source):
        i += 1
    return CSSToken("url", "".join(out).strip(), start=i), i


def serialize(tokens):
    """Reconstruct source text from tokens, for component values."""
    out = []
    for token in tokens:
        kind = token.kind
        if kind == "whitespace":
            out.append(" ")
        elif kind == "string":
            out.append('"%s"' % token.value)
        elif kind == "hash":
            out.append("#" + token.value)
        elif kind == "at-keyword":
            out.append("@" + token.value)
        elif kind == "function":
            out.append(token.value + "(")
        elif kind == "url":
            out.append("url(%s)" % token.value)
        elif kind in ("number",):
            out.append(_format_number(token.value))
        elif kind == "percentage":
            out.append(_format_number(token.value) + "%")
        elif kind == "dimension":
            out.append(_format_number(token.value) + token.unit)
        elif kind == "eof":
            pass
        else:
            out.append(str(token.value))
    return "".join(out).strip()


def _format_number(value):
    if value == int(value):
        return str(int(value))
    return repr(value)
