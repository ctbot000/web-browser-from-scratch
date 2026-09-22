"""The document object model: the tree the parser builds and layout reads."""

from __future__ import annotations


class Node:
    __slots__ = ("parent", "children")

    def __init__(self):
        self.parent = None
        self.children = []

    # -- tree surgery -----------------------------------------------------

    def append(self, child):
        if child.parent is not None:
            child.parent.remove(child)
        child.parent = self
        self.children.append(child)
        return child

    def insert_before(self, child, reference):
        if child.parent is not None:
            child.parent.remove(child)
        child.parent = self
        self.children.insert(self.children.index(reference), child)
        return child

    def remove(self, child):
        self.children.remove(child)
        child.parent = None
        return child

    # -- traversal --------------------------------------------------------

    def descendants(self):
        for child in self.children:
            yield child
            for node in child.descendants():
                yield node

    def ancestors(self):
        node = self.parent
        while node is not None:
            yield node
            node = node.parent

    def elements(self):
        for node in self.descendants():
            if isinstance(node, Element):
                yield node

    @property
    def previous_sibling(self):
        if self.parent is None:
            return None
        index = self.parent.children.index(self)
        return self.parent.children[index - 1] if index else None

    @property
    def next_sibling(self):
        if self.parent is None:
            return None
        siblings = self.parent.children
        index = siblings.index(self)
        return siblings[index + 1] if index + 1 < len(siblings) else None

    @property
    def text_content(self):
        parts = []
        for node in self.descendants():
            if isinstance(node, Text):
                parts.append(node.data)
        return "".join(parts)


class Document(Node):
    __slots__ = ("url", "doctype", "quirks", "title", "stylesheets", "errors")

    def __init__(self, url=None):
        super().__init__()
        self.url = url
        self.doctype = None
        self.quirks = False
        self.title = ""
        self.stylesheets = []
        self.errors = []

    @property
    def document_element(self):
        for child in self.children:
            if isinstance(child, Element) and child.tag == "html":
                return child
        for child in self.children:
            if isinstance(child, Element):
                return child
        return None

    def body(self):
        root = self.document_element
        if root is None:
            return None
        for child in root.children:
            if isinstance(child, Element) and child.tag in ("body", "frameset"):
                return child
        return root

    def head(self):
        root = self.document_element
        if root is None:
            return None
        for child in root.children:
            if isinstance(child, Element) and child.tag == "head":
                return child
        return None

    def __repr__(self):
        return "<Document %s>" % (self.url,)


class Element(Node):
    __slots__ = ("tag", "attrs", "namespace", "_style_cache", "layout_box")

    def __init__(self, tag, attrs=None, namespace="html"):
        super().__init__()
        self.tag = tag
        self.attrs = attrs or {}
        self.namespace = namespace
        self._style_cache = None
        self.layout_box = None

    def get(self, name, default=None):
        return self.attrs.get(name, default)

    def has(self, name):
        return name in self.attrs

    @property
    def id(self):
        return self.attrs.get("id")

    @property
    def classes(self):
        return (self.attrs.get("class") or "").split()

    def find_all(self, *tags):
        wanted = set(tags)
        for element in self.elements():
            if element.tag in wanted:
                yield element

    def __repr__(self):
        bits = "".join(' %s="%s"' % (k, v) for k, v in list(self.attrs.items())[:3])
        return "<%s%s>" % (self.tag, bits)


class Text(Node):
    __slots__ = ("data",)

    def __init__(self, data):
        super().__init__()
        self.data = data

    def __repr__(self):
        snippet = self.data if len(self.data) <= 30 else self.data[:27] + "..."
        return "#text(%r)" % snippet


class Comment(Node):
    __slots__ = ("data",)

    def __init__(self, data):
        super().__init__()
        self.data = data

    def __repr__(self):
        return "<!--%s-->" % self.data[:30]


def dump(node, indent=0, out=None):
    """Render the tree the way a devtools panel would, for debugging."""
    lines = [] if out is None else out
    pad = "  " * indent
    if isinstance(node, Document):
        lines.append("#document")
    elif isinstance(node, Element):
        attrs = "".join(' %s="%s"' % (k, v) for k, v in sorted(node.attrs.items()))
        lines.append("%s<%s%s>" % (pad, node.tag, attrs))
    elif isinstance(node, Text):
        stripped = node.data.strip()
        if not stripped:
            return lines
        shown = stripped if len(stripped) <= 60 else stripped[:57] + "..."
        lines.append('%s"%s"' % (pad, shown))
    elif isinstance(node, Comment):
        lines.append("%s<!-- %s -->" % (pad, node.data.strip()[:40]))
    for child in node.children:
        dump(child, indent + 1, lines)
    return lines
