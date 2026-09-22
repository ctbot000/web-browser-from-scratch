"""An error-tolerant HTML parser: tokenizer plus tree construction.

Real HTML is rarely well formed, so the interesting part of a parser is not
the grammar but the recovery rules: implied end tags, elements that may not
nest, content that belongs in the head, and text that has to be lifted out of
a table.  Those rules are what this module implements.
"""

from __future__ import annotations

import re

from .dom import Comment, Document, Element, Text
from .entities import decode_reference

VOID_ELEMENTS = {
    "area", "base", "basefont", "bgsound", "br", "col", "embed", "frame",
    "hr", "img", "input", "keygen", "link", "meta", "param", "source",
    "track", "wbr",
}

# Content of these is taken verbatim; no tags and no character references.
RAW_TEXT_ELEMENTS = {"script", "style", "xmp", "iframe", "noembed", "noframes"}
# Verbatim, but character references still apply.
ESCAPABLE_RAW_TEXT = {"title", "textarea"}

HEAD_ELEMENTS = {"base", "basefont", "bgsound", "link", "meta", "noscript",
                 "script", "style", "template", "title"}

# A <p> is closed by the start of any of these.
CLOSES_P = {
    "address", "article", "aside", "blockquote", "center", "details",
    "dialog", "dir", "div", "dl", "fieldset", "figcaption", "figure",
    "footer", "form", "h1", "h2", "h3", "h4", "h5", "h6", "header",
    "hgroup", "hr", "main", "menu", "nav", "ol", "p", "pre", "section",
    "summary", "table", "ul", "li", "dd", "dt", "blockquote",
}

HEADINGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
TABLE_SECTIONS = {"tbody", "thead", "tfoot"}
TABLE_CONTEXT = {"table", "tbody", "thead", "tfoot", "tr"}
TABLE_ELEMENTS = TABLE_CONTEXT | {"td", "th", "caption", "colgroup", "col"}

# Elements that stop the upward search for an open tag to close.
SCOPE_STOPPERS = {"html", "table", "td", "th", "caption", "template"}
LIST_SCOPE_STOPPERS = SCOPE_STOPPERS | {"ol", "ul"}

_TAG_NAME_RE = re.compile(r"[^\s/>]+")
_ATTR_NAME_RE = re.compile(r"[^\s/>=]+")


class Token:
    __slots__ = ("kind", "name", "data", "attrs", "self_closing")

    def __init__(self, kind, name=None, data=None, attrs=None,
                 self_closing=False):
        self.kind = kind          # start | end | text | comment | doctype
        self.name = name
        self.data = data
        self.attrs = attrs or {}
        self.self_closing = self_closing

    def __repr__(self):
        if self.kind == "text":
            return "text(%r)" % (self.data[:25],)
        return "%s(%s%s)" % (self.kind, self.name,
                             " " + repr(self.attrs) if self.attrs else "")


class Tokenizer:
    def __init__(self, source, on_error=None):
        self.src = source
        self.pos = 0
        self.on_error = on_error or (lambda message, position: None)

    def error(self, message):
        self.on_error(message, self.pos)

    def tokens(self):
        src, length = self.src, len(self.src)
        while self.pos < length:
            index = src.find("<", self.pos)
            if index < 0:
                yield Token("text", data=self._unescape(src[self.pos:]))
                self.pos = length
                break
            if index > self.pos:
                yield Token("text", data=self._unescape(src[self.pos:index]))
                self.pos = index
            token = self._markup()
            if token is not None:
                yield token
                if token.kind == "start" and not token.self_closing:
                    if token.name in RAW_TEXT_ELEMENTS:
                        text = self._raw_text(token.name, escape=False)
                        if text:
                            yield Token("text", data=text)
                        yield Token("end", name=token.name)
                    elif token.name in ESCAPABLE_RAW_TEXT:
                        text = self._raw_text(token.name, escape=True)
                        if text:
                            yield Token("text", data=text)
                        yield Token("end", name=token.name)

    # -- pieces -----------------------------------------------------------

    def _markup(self):
        src = self.src
        start = self.pos
        nxt = src[start + 1:start + 2]

        if nxt == "!":
            if src.startswith("<!--", start):
                return self._comment()
            if src[start + 2:start + 9].lower() == "doctype":
                return self._doctype()
            if src.startswith("<![CDATA[", start):
                end = src.find("]]>", start)
                end = len(src) if end < 0 else end
                text = src[start + 9:end]
                self.pos = min(len(src), end + 3)
                return Token("text", data=text)
            return self._bogus_comment()
        if nxt == "?":
            return self._bogus_comment()
        if nxt == "/":
            match = _TAG_NAME_RE.match(src, start + 2)
            if not match:
                return self._bogus_comment()
            name = match.group(0).lower()
            end = src.find(">", match.end())
            self.pos = len(src) if end < 0 else end + 1
            return Token("end", name=name)
        if nxt.isalpha():
            return self._start_tag()

        # A "<" that begins nothing: literal text.
        self.pos = start + 1
        return Token("text", data="<")

    def _start_tag(self):
        src = self.src
        match = _TAG_NAME_RE.match(src, self.pos + 1)
        name = match.group(0).lower()
        i = match.end()
        attrs = {}
        self_closing = False
        while i < len(src):
            while i < len(src) and src[i] in " \t\n\r\f":
                i += 1
            if i >= len(src):
                break
            if src[i] == ">":
                i += 1
                break
            if src[i] == "/":
                if src[i + 1:i + 2] == ">":
                    self_closing = True
                    i += 2
                    break
                i += 1
                continue
            name_match = _ATTR_NAME_RE.match(src, i)
            if not name_match:
                i += 1
                continue
            attr_name = name_match.group(0).lower()
            i = name_match.end()
            while i < len(src) and src[i] in " \t\n\r\f":
                i += 1
            value = ""
            if i < len(src) and src[i] == "=":
                i += 1
                while i < len(src) and src[i] in " \t\n\r\f":
                    i += 1
                if i < len(src) and src[i] in "\"'":
                    quote = src[i]
                    end = src.find(quote, i + 1)
                    if end < 0:
                        end = len(src)
                    value = src[i + 1:end]
                    i = min(len(src), end + 1)
                else:
                    end = i
                    while end < len(src) and src[end] not in " \t\n\r\f>":
                        end += 1
                    value = src[i:end]
                    i = end
                value = self._unescape(value, in_attribute=True)
            if attr_name not in attrs:
                attrs[attr_name] = value
            else:
                self.error("duplicate attribute %r" % attr_name)
        self.pos = i
        return Token("start", name=name, attrs=attrs, self_closing=self_closing)

    def _comment(self):
        src = self.src
        end = src.find("-->", self.pos + 4)
        if end < 0:
            self.error("unterminated comment")
            data = src[self.pos + 4:]
            self.pos = len(src)
        else:
            data = src[self.pos + 4:end]
            self.pos = end + 3
        return Token("comment", data=data)

    def _bogus_comment(self):
        src = self.src
        end = src.find(">", self.pos)
        if end < 0:
            end = len(src) - 1
        data = src[self.pos + 1:end]
        self.pos = end + 1
        self.error("bogus comment %r" % data[:20])
        return Token("comment", data=data)

    def _doctype(self):
        src = self.src
        end = src.find(">", self.pos)
        if end < 0:
            end = len(src)
        body = src[self.pos + 9:end].strip()
        self.pos = min(len(src), end + 1)
        return Token("doctype", name=body.split()[0].lower() if body else "",
                     data=body)

    def _raw_text(self, tag, escape):
        src = self.src
        lowered = src.lower()
        needle = "</" + tag
        search = self.pos
        while True:
            end = lowered.find(needle, search)
            if end < 0:
                text = src[self.pos:]
                self.pos = len(src)
                self.error("unclosed <%s>" % tag)
                return self._unescape(text) if escape else text
            after = src[end + len(needle):end + len(needle) + 1]
            if after in ("", ">", " ", "\t", "\n", "\r", "\f", "/"):
                text = src[self.pos:end]
                close = src.find(">", end)
                self.pos = len(src) if close < 0 else close + 1
                return self._unescape(text) if escape else text
            search = end + len(needle)

    def _unescape(self, text, in_attribute=False):
        if "&" not in text:
            return text
        out = []
        i = 0
        while True:
            amp = text.find("&", i)
            if amp < 0:
                out.append(text[i:])
                break
            out.append(text[i:amp])
            replacement, consumed = decode_reference(text, amp, in_attribute)
            if consumed:
                out.append(replacement)
                i = amp + consumed
            else:
                out.append("&")
                i = amp + 1
        return "".join(out)


class HTMLParser:
    """Builds a :class:`~mote.dom.Document` from HTML source text."""

    def __init__(self, url=None):
        self.document = Document(url)
        self.stack = []             # open elements, root first
        self.head = None
        self.body = None
        self.saw_body_content = False

    # -- entry point ------------------------------------------------------

    def parse(self, source):
        tokenizer = Tokenizer(source, on_error=self._record_error)
        for token in tokenizer.tokens():
            handler = getattr(self, "_on_" + token.kind)
            handler(token)
        self._finish()
        return self.document

    def _record_error(self, message, position):
        if len(self.document.errors) < 200:
            self.document.errors.append((position, message))

    # -- stack helpers ----------------------------------------------------

    @property
    def current(self):
        return self.stack[-1] if self.stack else self.document

    def _push(self, element):
        self.current.append(element)
        self.stack.append(element)
        return element

    def _pop_until(self, name, stoppers=SCOPE_STOPPERS):
        """Close elements up to and including *name*; report whether found."""
        for index in range(len(self.stack) - 1, -1, -1):
            tag = self.stack[index].tag
            if tag == name:
                del self.stack[index:]
                return True
            if tag in stoppers:
                break
        return False

    def _has_open(self, name, stoppers=SCOPE_STOPPERS):
        for element in reversed(self.stack):
            if element.tag == name:
                return True
            if element.tag in stoppers:
                return False
        return False

    def _close_implied(self, exclude=None):
        """HTML5's "generate implied end tags", optionally sparing one tag.

        The exclusion matters: closing </b> must not also close the <p> the
        bold text sits in, but starting a new <li> must close the open one.
        """
        implied = {"p", "li", "dd", "dt", "option", "optgroup", "rt", "rp"}
        implied.discard(exclude)
        while self.stack and self.stack[-1].tag in implied:
            self.stack.pop()

    # -- scaffolding ------------------------------------------------------

    def _ensure_html(self):
        if not self.stack:
            root = self.document.document_element
            if root is None:
                root = Element("html")
                self.document.append(root)
            self.stack.append(root)
        return self.stack[0]

    def _ensure_head(self):
        self._ensure_html()
        if self.head is None:
            self.head = Element("head")
            self.stack[0].append(self.head)
        return self.head

    def _ensure_body(self):
        self._ensure_html()
        if self.body is None:
            self._ensure_head()
            self.body = Element("body")
            self.stack[0].append(self.body)
            if len(self.stack) == 1:
                self.stack.append(self.body)
        return self.body

    def _in_table_context(self):
        return bool(self.stack) and self.current.tag in TABLE_CONTEXT

    def _foster_parent(self, node):
        """Insert *node* just before the innermost open table."""
        for index in range(len(self.stack) - 1, -1, -1):
            element = self.stack[index]
            if element.tag == "table":
                if element.parent is not None:
                    element.parent.insert_before(node, element)
                    return
                break
        self.current.append(node)

    # -- token handlers ---------------------------------------------------

    def _on_doctype(self, token):
        if self.stack or self.document.document_element is not None:
            return
        self.document.doctype = token.data
        self.document.quirks = token.name != "html"

    def _on_comment(self, token):
        self.current.append(Comment(token.data))

    def _on_text(self, token):
        data = token.data
        if not data:
            return
        if not self.stack or self.current.tag == "html":
            if data.strip() == "":
                return
            self._ensure_body()
        if self.head is not None and self.body is None and not self.stack[1:]:
            if data.strip() == "":
                return
            self._ensure_body()
        if self._in_table_context() and data.strip():
            self._foster_parent(Text(data))
            return
        if self._in_table_context():
            return              # whitespace between table rows is dropped
        self.current.append(Text(data))

    def _on_start(self, token):
        name = token.name
        attrs = token.attrs

        if name == "html":
            root = self._ensure_html()
            for key, value in attrs.items():
                root.attrs.setdefault(key, value)
            return
        if name == "head":
            self._ensure_html()
            if self.head is None:
                self.head = Element("head", attrs)
                self.stack[0].append(self.head)
                self.stack.append(self.head)
            return
        if name == "body":
            self._ensure_html()
            if self.body is None:
                self._ensure_head()
                self.body = Element("body", attrs)
                self.stack[0].append(self.body)
                del self.stack[1:]
                self.stack.append(self.body)
            else:
                for key, value in attrs.items():
                    self.body.attrs.setdefault(key, value)
            return
        if name == "frameset":
            self._ensure_html()
            if self.body is None:
                self.body = Element("frameset", attrs)
                self.stack[0].append(self.body)
                self.stack.append(self.body)
            return

        if name in HEAD_ELEMENTS and self.body is None:
            head = self._ensure_head()
            element = Element(name, attrs)
            head.append(element)
            if name not in VOID_ELEMENTS and not token.self_closing:
                self.stack.append(element)
                if self.stack[-1] is not element:
                    self.stack.append(element)
            return

        self._ensure_body()
        if not any(e is self.body for e in self.stack) and self.body is not None:
            self.stack.append(self.body)

        self._apply_nesting_rules(name)

        if self._in_table_context() and name not in TABLE_ELEMENTS:
            element = Element(name, attrs)
            self._foster_parent(element)
            if name not in VOID_ELEMENTS and not token.self_closing:
                self.stack.append(element)
            return

        if name == "tr" and self.current.tag == "table":
            self._push(Element("tbody"))
        if name in ("td", "th") and self.current.tag in TABLE_SECTIONS:
            self._push(Element("tr"))
        if name in ("td", "th") and self.current.tag == "table":
            self._push(Element("tbody"))
            self._push(Element("tr"))

        element = Element(name, attrs)
        self.current.append(element)
        if name in VOID_ELEMENTS or token.self_closing:
            return
        self.stack.append(element)

    def _apply_nesting_rules(self, name):
        current = self.current.tag
        if name in CLOSES_P and self._has_open("p"):
            self._pop_until("p")
        if name == "li" and self._has_open("li", LIST_SCOPE_STOPPERS):
            self._close_implied()
            self._pop_until("li", LIST_SCOPE_STOPPERS)
        elif name in ("dd", "dt"):
            for tag in ("dd", "dt"):
                if self._has_open(tag):
                    self._close_implied()
                    self._pop_until(tag)
                    break
        elif name == "option" and current == "option":
            self.stack.pop()
        elif name == "optgroup":
            if current == "option":
                self.stack.pop()
            if self.current.tag == "optgroup":
                self.stack.pop()
        elif name in HEADINGS and current in HEADINGS:
            self.stack.pop()
        elif name == "a" and self._has_open("a"):
            self._pop_until("a")
        elif name == "nobr" and self._has_open("nobr"):
            self._pop_until("nobr")
        elif name == "tr":
            while self.current.tag in ("td", "th", "tr"):
                self.stack.pop()
        elif name in ("td", "th"):
            while self.current.tag in ("td", "th"):
                self.stack.pop()
        elif name in TABLE_SECTIONS:
            while self.current.tag in ("td", "th", "tr") or \
                    self.current.tag in TABLE_SECTIONS:
                self.stack.pop()
        elif name == "caption" or name == "colgroup":
            while self.current.tag in ("td", "th", "tr") or \
                    self.current.tag in TABLE_SECTIONS:
                self.stack.pop()

    def _on_end(self, token):
        name = token.name
        if name in VOID_ELEMENTS:
            return
        if name == "body" or name == "html":
            return
        if name == "head":
            if self.stack and self.current.tag == "head":
                self.stack.pop()
            return
        if name == "p" and not self._has_open("p"):
            # </p> with nothing open is an empty paragraph.
            self._ensure_body()
            self.current.append(Element("p"))
            return
        if name in ("td", "th", "tr") or name in TABLE_SECTIONS or name == "table":
            self._close_implied()
            if self._pop_until(name, {"html"}):
                return
            return
        self._close_implied(exclude=name)
        if not self._pop_until(name):
            self._record_error("stray </%s>" % name, 0)

    def _finish(self):
        self._ensure_html()
        if self.head is None:
            self._ensure_head()
        if self.body is None:
            self._ensure_body()
        self.stack = []
        head = self.document.head()
        if head is not None:
            for element in head.find_all("title"):
                self.document.title = element.text_content.strip()
                break


def parse_html(source, url=None):
    return HTMLParser(url).parse(source)
