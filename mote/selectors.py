"""CSS selector parsing, matching and specificity."""

from __future__ import annotations

from .csstoken import CSSToken, tokenize
from .dom import Element

DESCENDANT, CHILD, ADJACENT, SIBLING = " ", ">", "+", "~"


class SelectorError(ValueError):
    pass


class Compound:
    """One compound selector: ``div.note#lead[lang|=en]:first-child``."""

    __slots__ = ("tag", "ids", "classes", "attrs", "pseudos", "pseudo_element")

    def __init__(self):
        self.tag = None
        self.ids = []
        self.classes = []
        self.attrs = []          # (name, operator, value)
        self.pseudos = []        # (name, argument)
        self.pseudo_element = None

    def matches(self, element, context):
        if not isinstance(element, Element):
            return False
        if self.tag is not None and element.tag != self.tag:
            return False
        if self.ids and any(element.get("id") != i for i in self.ids):
            return False
        if self.classes:
            classes = set(element.classes)
            if not all(name in classes for name in self.classes):
                return False
        for name, operator, value in self.attrs:
            if not _attr_matches(element, name, operator, value):
                return False
        for name, argument in self.pseudos:
            if not _pseudo_matches(element, name, argument, context):
                return False
        return True

    def __repr__(self):
        out = self.tag or ("*" if not (self.ids or self.classes or self.attrs
                                       or self.pseudos) else "")
        out += "".join("#" + i for i in self.ids)
        out += "".join("." + c for c in self.classes)
        out += "".join("[%s%s%s]" % (n, o or "", v or "") for n, o, v in self.attrs)
        out += "".join(":" + n + ("(%s)" % a if a else "")
                       for n, a in self.pseudos)
        if self.pseudo_element:
            out += "::" + self.pseudo_element
        return out


class Selector:
    """A full complex selector plus its specificity."""

    __slots__ = ("parts", "specificity", "source")

    def __init__(self, parts, source=""):
        self.parts = parts       # [(combinator_or_None, Compound), ...]
        self.source = source
        self.specificity = self._specificity()

    def _specificity(self):
        a = b = c = 0
        for _, compound in self.parts:
            a += len(compound.ids)
            b += len(compound.classes) + len(compound.attrs)
            for name, argument in compound.pseudos:
                if name == "not" and argument:
                    try:
                        inner = max(parse_selector_list(argument),
                                    key=lambda s: s.specificity)
                        ia, ib, ic = inner.specificity
                        a, b, c = a + ia, b + ib, c + ic
                    except SelectorError:
                        pass
                else:
                    b += 1
            if compound.tag is not None:
                c += 1
            if compound.pseudo_element:
                c += 1
        return (a, b, c)

    @property
    def pseudo_element(self):
        return self.parts[-1][1].pseudo_element if self.parts else None

    def matches(self, element, context=None):
        context = context or {}
        combinator, compound = self.parts[-1]
        if not compound.matches(element, context):
            return False
        return self._match_prefix(element, len(self.parts) - 1, context)

    def _match_prefix(self, element, index, context):
        if index == 0:
            return True
        combinator, compound = self.parts[index]
        target, previous = compound, self.parts[index - 1][1]
        if combinator == CHILD:
            parent = element.parent
            if not isinstance(parent, Element) or not previous.matches(parent, context):
                return False
            return self._match_prefix(parent, index - 1, context)
        if combinator == ADJACENT:
            sibling = _previous_element(element)
            if sibling is None or not previous.matches(sibling, context):
                return False
            return self._match_prefix(sibling, index - 1, context)
        if combinator == SIBLING:
            sibling = _previous_element(element)
            while sibling is not None:
                if previous.matches(sibling, context) and \
                        self._match_prefix(sibling, index - 1, context):
                    return True
                sibling = _previous_element(sibling)
            return False
        ancestor = element.parent
        while isinstance(ancestor, Element):
            if previous.matches(ancestor, context) and \
                    self._match_prefix(ancestor, index - 1, context):
                return True
            ancestor = ancestor.parent
        return False

    def __repr__(self):
        out = []
        for combinator, compound in self.parts:
            if combinator and combinator != DESCENDANT:
                out.append(" %s " % combinator)
            elif combinator == DESCENDANT:
                out.append(" ")
            out.append(repr(compound))
        return "".join(out)


def _previous_element(node):
    sibling = node.previous_sibling
    while sibling is not None and not isinstance(sibling, Element):
        sibling = sibling.previous_sibling
    return sibling


def _attr_matches(element, name, operator, value):
    actual = element.get(name)
    if actual is None:
        return False
    if operator is None:
        return True
    if operator == "=":
        return actual == value
    if operator == "~=":
        return value in actual.split()
    if operator == "|=":
        return actual == value or actual.startswith(value + "-")
    if operator == "^=":
        return bool(value) and actual.startswith(value)
    if operator == "$=":
        return bool(value) and actual.endswith(value)
    if operator == "*=":
        return bool(value) and value in actual
    return False


def _element_index(element, of_type=False, from_end=False):
    parent = element.parent
    if parent is None:
        return 1
    siblings = [c for c in parent.children if isinstance(c, Element)
                and (not of_type or c.tag == element.tag)]
    if from_end:
        siblings = list(reversed(siblings))
    return siblings.index(element) + 1


def _parse_nth(argument):
    """Parse ``2n+1``/``odd``/``even``/``3`` into ``(a, b)``."""
    text = (argument or "").strip().lower().replace(" ", "")
    if text == "odd":
        return 2, 1
    if text == "even":
        return 2, 0
    if "n" not in text:
        try:
            return 0, int(text)
        except ValueError:
            return 0, 0
    coefficient, _, offset = text.partition("n")
    if coefficient in ("", "+"):
        a = 1
    elif coefficient == "-":
        a = -1
    else:
        try:
            a = int(coefficient)
        except ValueError:
            a = 0
    try:
        b = int(offset) if offset else 0
    except ValueError:
        b = 0
    return a, b


def _nth_matches(index, argument):
    a, b = _parse_nth(argument)
    if a == 0:
        return index == b
    remainder = index - b
    return remainder % a == 0 and remainder // a >= 0


def _pseudo_matches(element, name, argument, context):
    if name == "root":
        return element.parent is not None and element.parent.parent is None
    if name == "first-child":
        return _element_index(element) == 1
    if name == "last-child":
        return _element_index(element, from_end=True) == 1
    if name == "only-child":
        return _element_index(element) == 1 and \
            _element_index(element, from_end=True) == 1
    if name == "first-of-type":
        return _element_index(element, of_type=True) == 1
    if name == "last-of-type":
        return _element_index(element, of_type=True, from_end=True) == 1
    if name == "nth-child":
        return _nth_matches(_element_index(element), argument)
    if name == "nth-last-child":
        return _nth_matches(_element_index(element, from_end=True), argument)
    if name == "nth-of-type":
        return _nth_matches(_element_index(element, of_type=True), argument)
    if name == "empty":
        from .dom import Text
        return not any(isinstance(c, Element) or
                       (isinstance(c, Text) and c.data.strip())
                       for c in element.children)
    if name == "not":
        try:
            return not any(s.matches(element, context)
                           for s in parse_selector_list(argument or ""))
        except SelectorError:
            return False
    if name in ("is", "where", "matches", "any"):
        try:
            return any(s.matches(element, context)
                       for s in parse_selector_list(argument or ""))
        except SelectorError:
            return False
    if name == "link":
        return element.tag in ("a", "area") and element.has("href") and \
            not context.get("visited", lambda e: False)(element)
    if name == "visited":
        return element.tag in ("a", "area") and element.has("href") and \
            context.get("visited", lambda e: False)(element)
    if name == "any-link":
        return element.tag in ("a", "area") and element.has("href")
    if name == "hover":
        return element is context.get("hover")
    if name == "focus":
        return element is context.get("focus")
    if name == "active":
        return element is context.get("active")
    if name == "checked":
        return element.has("checked") or element.has("selected")
    if name == "disabled":
        return element.has("disabled")
    if name == "enabled":
        return not element.has("disabled")
    if name in ("lang",):
        for node in [element] + list(element.ancestors()):
            if isinstance(node, Element) and node.has("lang"):
                return node.get("lang", "").lower().startswith(
                    (argument or "").lower())
        return False
    return False


# ------------------------------------------------------------------ parsing

def parse_selector_list(text):
    tokens = tokenize(text) if isinstance(text, str) else text
    groups = [[]]
    depth = 0
    for token in tokens:
        if token.kind == "eof":
            break
        if token.kind == "comma" and depth == 0:
            groups.append([])
            continue
        if token.kind in ("(", "function"):
            depth += 1
        elif token.kind == ")":
            depth -= 1
        groups[-1].append(token)
    selectors = []
    for group in groups:
        selector = _parse_complex(group)
        if selector is not None:
            selectors.append(selector)
    if not selectors:
        raise SelectorError("no valid selector in %r" % (text,))
    return selectors


def _parse_complex(tokens):
    from .csstoken import serialize
    parts = []
    compound = Compound()
    started = False
    combinator = None
    i = 0

    def flush():
        nonlocal compound, started, combinator
        if started:
            parts.append((combinator, compound))
            compound = Compound()
            started = False
            combinator = None

    while i < len(tokens):
        token = tokens[i]
        kind = token.kind

        if kind == "whitespace":
            j = i + 1
            while j < len(tokens) and tokens[j].kind == "whitespace":
                j += 1
            if j < len(tokens) and tokens[j].kind == "delim" and \
                    tokens[j].value in (">", "+", "~"):
                i = j
                continue
            if started and j < len(tokens):
                flush()
                combinator = DESCENDANT
            i = j
            continue

        if kind == "delim" and token.value in (">", "+", "~"):
            if not started and combinator is not None:
                return None            # two combinators in a row, e.g. "a >> b"
            flush()
            combinator = token.value
            i += 1
            while i < len(tokens) and tokens[i].kind == "whitespace":
                i += 1
            continue

        if kind == "delim" and token.value == "*":
            started = True
            i += 1
            continue

        if kind == "ident":
            if started and compound.tag is None and not (
                    compound.ids or compound.classes or compound.attrs
                    or compound.pseudos):
                compound.tag = token.value.lower()
            elif not started:
                compound.tag = token.value.lower()
            else:
                return None
            started = True
            i += 1
            continue

        if kind == "hash":
            compound.ids.append(token.value)
            started = True
            i += 1
            continue

        if kind == "delim" and token.value == ".":
            if i + 1 < len(tokens) and tokens[i + 1].kind == "ident":
                compound.classes.append(tokens[i + 1].value)
                started = True
                i += 2
                continue
            return None

        if kind == "[":
            end = i + 1
            depth = 1
            while end < len(tokens) and depth:
                if tokens[end].kind == "[":
                    depth += 1
                elif tokens[end].kind == "]":
                    depth -= 1
                    if depth == 0:
                        break
                end += 1
            inner = tokens[i + 1:end]
            attribute = _parse_attribute(inner)
            if attribute is None:
                return None
            compound.attrs.append(attribute)
            started = True
            i = end + 1
            continue

        if kind == "colon":
            double = (i + 1 < len(tokens) and tokens[i + 1].kind == "colon")
            j = i + (2 if double else 1)
            if j >= len(tokens):
                return None
            nxt = tokens[j]
            if nxt.kind == "ident":
                name = nxt.value.lower()
                if double or name in ("before", "after", "first-line",
                                      "first-letter", "marker", "placeholder",
                                      "selection"):
                    compound.pseudo_element = name
                else:
                    compound.pseudos.append((name, None))
                started = True
                i = j + 1
                continue
            if nxt.kind == "function":
                depth = 1
                end = j + 1
                while end < len(tokens) and depth:
                    if tokens[end].kind in ("(", "function"):
                        depth += 1
                    elif tokens[end].kind == ")":
                        depth -= 1
                        if depth == 0:
                            break
                    end += 1
                argument = serialize(tokens[j + 1:end])
                compound.pseudos.append((nxt.value.lower(), argument))
                started = True
                i = end + 1
                continue
            return None

        # Anything else (an unknown delimiter, a stray brace) invalidates the
        # whole selector, as the standard requires.
        return None

    flush()
    if not parts:
        return None
    return Selector(parts, serialize(tokens))


def _parse_attribute(tokens):
    tokens = [t for t in tokens if t.kind != "whitespace"]
    if not tokens or tokens[0].kind != "ident":
        return None
    name = tokens[0].value.lower()
    if len(tokens) == 1:
        return (name, None, None)
    operator = None
    index = 1
    if tokens[index].kind == "delim" and tokens[index].value in "~|^$*":
        operator = tokens[index].value
        index += 1
        if index >= len(tokens) or not tokens[index].is_delim("="):
            return None
        operator += "="
        index += 1
    elif tokens[index].is_delim("="):
        operator = "="
        index += 1
    else:
        return None
    if index >= len(tokens):
        return None
    value_token = tokens[index]
    if value_token.kind in ("ident", "string"):
        value = value_token.value
    elif value_token.kind == "number":
        value = ("%g" % value_token.value)
    else:
        return None
    return (name, operator, value)
