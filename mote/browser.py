"""The engine: fetch a URL, build a page, and keep it laid out."""

from __future__ import annotations

import time

from .cssparse import MediaContext
from .dom import Element
from .encoding import decode
from .fonts import BuiltinMetrics
from .htmlparse import parse_html
from .images import ImageError, decode_image
from .layout import LayoutContext, layout_document
from .paint import build_display_list, document_height
from .resource import Loader, Resource
from .style import StyleEngine, collect_stylesheets
from .url import URL, URLError

TEXT_MIMES = {"text/plain", "text/css", "text/javascript",
              "application/javascript", "application/json", "text/xml",
              "application/xml", "text/markdown", "application/x-sh"}
HTML_MIMES = {"text/html", "application/xhtml+xml", "application/xml+xhtml", ""}


class PageError(Exception):
    pass


class Page:
    """One loaded document, styled, laid out and painted."""

    def __init__(self, url, document, resource=None):
        self.url = url
        self.document = document
        self.resource = resource
        self.styles = {}
        self.root_box = None
        self.display_list = []
        self.width = 0.0
        self.height = 0.0
        self.load_time = 0.0
        self.stylesheet_count = 0
        self.image_count = 0
        self.canvas_color = None
        self.links = []

    @property
    def title(self):
        return self.document.title or str(self.url)

    def link_at(self, x, y):
        """The anchor under a document-space point, if any."""
        best = None
        for command in self.display_list:
            element = getattr(command, "element", None)
            if element is None:
                continue
            cx, cy, width, height = command.rect
            if cx <= x <= cx + width and cy <= y <= cy + height:
                anchor = _enclosing_link(element)
                if anchor is not None:
                    best = anchor
        return best

    def __repr__(self):
        return "<Page %s %d commands>" % (self.url, len(self.display_list))


def _enclosing_link(element):
    node = element
    while isinstance(node, Element):
        if node.tag in ("a", "area") and node.has("href"):
            return node
        node = node.parent
    return None


class Engine:
    def __init__(self, loader=None, metrics=None, width=800.0, height=600.0,
                 load_images=True, on_status=None):
        self.loader = loader if loader is not None else Loader()
        self.metrics = metrics if metrics is not None else BuiltinMetrics()
        self.width = width
        self.height = height
        self.load_images = load_images
        self.on_status = on_status
        self.visited = set()
        self._image_cache = {}
        self._style_cache = {}

    def close(self):
        self.loader.close()

    def status(self, message):
        if self.on_status:
            self.on_status(message)

    # -- loading ----------------------------------------------------------

    def load(self, url, referrer=None):
        started = time.time()
        if isinstance(url, str):
            url = URL.parse(url)

        if url.scheme == "view-source":
            return self._view_source(url, referrer)

        self.status("Loading %s" % url)
        resource = self.loader.fetch(url, referrer=referrer)
        if resource.error:
            document = parse_html(_error_document(url, resource.error), url)
            page = Page(url, document, resource)
        else:
            document = self._document_for(resource)
            page = Page(resource.url, document, resource)
        self.visited.add(str(page.url.without_fragment()))
        self.render(page)
        page.load_time = time.time() - started
        self.status("Done: %s in %.0f ms" % (page.title, page.load_time * 1000))
        return page

    def _document_for(self, resource):
        mime = resource.mime
        if mime in HTML_MIMES or mime.endswith("+xml") and "svg" not in mime:
            text, _ = decode(resource.data, resource.charset)
            return parse_html(text, resource.url)
        if mime.startswith("image/"):
            return parse_html(_image_document(resource), resource.url)
        if mime.startswith("text/") or mime in TEXT_MIMES:
            text, _ = decode(resource.data, resource.charset, sniff=False)
            return parse_html(_plain_document(resource.url, text),
                              resource.url)
        return parse_html(_binary_document(resource), resource.url)

    def _view_source(self, url, referrer):
        try:
            target = URL.parse(url.opaque or "")
        except URLError as exc:
            raise PageError(str(exc))
        resource = self.loader.fetch(target, referrer=referrer)
        text, _ = decode(resource.data, resource.charset)
        document = parse_html(_plain_document(target, text, "Source of %s"),
                              url)
        page = Page(url, document, resource)
        self.render(page)
        return page

    # -- styling and layout ----------------------------------------------

    def render(self, page, width=None, height=None):
        """(Re)compute styles, layout and the display list for *page*."""
        self.width = width or self.width
        self.height = height or self.height
        document = page.document
        media = MediaContext(width=self.width, height=self.height)

        base = _base_url(document, page.url)
        sheets = collect_stylesheets(
            document, fetch=lambda href, base_url=None:
            self._fetch_stylesheet(href, base_url or base), media=media)
        page.stylesheet_count = len(sheets) - 1

        engine = StyleEngine(sheets, media=media,
                             context={"visited": self._is_visited})
        page.styles = engine.compute(document, self.width, self.height)

        context = LayoutContext(self.metrics, self.width, self.height,
                                image_loader=lambda src:
                                self._load_image(src, base))
        page.root_box = layout_document(document, page.styles, context)
        if page.root_box is None:
            page.display_list = []
            page.width, page.height = self.width, 0.0
            return page
        source, canvas_color = _canvas_background(document, page.styles)
        page.display_list = build_display_list(
            page.root_box, skip_background=(id(source),) if source else ())
        if canvas_color is not None:
            from .paint import DrawRect
            bottom = max(self.height, page.root_box.margin_box[1] +
                         page.root_box.margin_box[3])
            page.display_list.insert(
                0, DrawRect((0.0, 0.0, self.width, bottom), canvas_color))
        page.canvas_color = canvas_color
        page.width = self.width
        page.height = max(document_height(page.display_list, page.root_box),
                          self.height)
        page.links = [element for element in document.elements()
                      if element.tag == "a" and element.has("href")]
        return page

    def _is_visited(self, element):
        href = element.get("href")
        if not href:
            return False
        try:
            base = element.layout_box.style.element if False else None
            del base
            target = URL.parse(href)
        except URLError:
            return False
        return str(target) in self.visited

    # -- subresources -----------------------------------------------------

    def _fetch_stylesheet(self, href, base):
        try:
            target = base.join(href) if base is not None else URL.parse(href)
        except URLError:
            return None, None
        key = str(target)
        if key in self._style_cache:
            return self._style_cache[key], target
        self.status("Fetching stylesheet %s" % target)
        resource = self.loader.fetch(target, referrer=base)
        if resource.error or not resource.ok:
            self._style_cache[key] = None
            return None, target
        text, _ = decode(resource.data, resource.charset, sniff=False)
        self._style_cache[key] = text
        return text, resource.url

    def _load_image(self, source, base):
        if not self.load_images or not source:
            return None
        try:
            target = base.join(source) if base is not None else URL.parse(source)
        except URLError:
            return None
        key = str(target)
        if key in self._image_cache:
            return self._image_cache[key]
        resource = self.loader.fetch(target, referrer=base)
        bitmap = None
        if resource.ok and resource.data:
            try:
                bitmap = decode_image(resource.data, resource.mime)
            except (ImageError, Exception):
                bitmap = None
        self._image_cache[key] = bitmap
        return bitmap


def _canvas_background(document, styles):
    """Which element's background paints the canvas, and in what colour.

    The root element's background covers the whole canvas rather than just its
    own box; when <html> has none, <body>'s is used instead.  That is the rule
    that stops a page with a coloured body showing white down the margins.
    """
    from .values import TRANSPARENT

    for element in (document.document_element, document.body()):
        if element is None:
            continue
        style = styles.get(id(element))
        if style is None:
            continue
        color = style.color_of("background-color", TRANSPARENT)
        if color is not None and color[3] > 0:
            return element, color
    return None, None


def _base_url(document, fallback):
    for element in document.elements():
        if element.tag == "base" and element.has("href"):
            try:
                return fallback.join(element.get("href")) if fallback \
                    else URL.parse(element.get("href"))
            except URLError:
                break
    return fallback


def resolve(base, href):
    try:
        return base.join(href) if base is not None else URL.parse(href)
    except URLError:
        return None


# ----------------------------------------------------------- generated pages

def _escape(text):
    return (text.replace("&", "&amp;").replace("<", "&lt;")
                .replace(">", "&gt;"))


_CHROME_STYLE = """
body { font-family: sans-serif; margin: 0; color: #24292f }
.bar { background: #f6f8fa; border-bottom: 1px solid #d8dee4;
       padding: 10px 18px; font-size: 0.85em; color: #57606a }
.body { padding: 22px 26px }
h1 { font-size: 1.3em; margin: 0 0 10px }
pre { font-family: monospace; font-size: 0.86em; white-space: pre-wrap;
      background: #f6f8fa; border: 1px solid #e1e4e8; padding: 12px;
      margin: 0 }
"""


def _plain_document(url, text, heading="%s"):
    return ("<!doctype html><html><head><title>%s</title><style>%s</style>"
            "</head><body><div class=bar>%s</div><div class=body><pre>%s</pre>"
            "</div></body></html>"
            % (_escape(str(url)), _CHROME_STYLE,
               _escape(heading % str(url)), _escape(text)))


def _image_document(resource):
    return ("<!doctype html><html><head><title>%s</title>"
            "<style>body{margin:0;background:#2b2b2b;text-align:center}"
            "img{margin:24px auto;display:inline-block;"
            "background:#ffffff}</style></head><body>"
            "<img src=\"%s\" alt=\"%s\"></body></html>"
            % (_escape(str(resource.url)), _escape(str(resource.url)),
               _escape(resource.mime)))


def _binary_document(resource):
    return ("<!doctype html><html><head><title>%s</title><style>%s</style>"
            "</head><body><div class=body><h1>Cannot display this file</h1>"
            "<p>%s is <code>%s</code>, %d bytes. Mote renders HTML, plain "
            "text and images.</p></div></body></html>"
            % (_escape(str(resource.url)), _CHROME_STYLE,
               _escape(str(resource.url)), _escape(resource.mime or "unknown"),
               len(resource.data)))


def _error_document(url, message):
    return ("<!doctype html><html><head><title>Cannot load page</title>"
            "<style>%s</style></head><body><div class=body>"
            "<h1>Cannot reach %s</h1><pre>%s</pre></div></body></html>"
            % (_CHROME_STYLE, _escape(str(url)), _escape(str(message))))


class History:
    """Per-tab session history."""

    def __init__(self):
        self.entries = []
        self.index = -1

    def visit(self, url):
        del self.entries[self.index + 1:]
        self.entries.append(url)
        self.index = len(self.entries) - 1

    def can_go_back(self):
        return self.index > 0

    def can_go_forward(self):
        return self.index < len(self.entries) - 1

    def back(self):
        if not self.can_go_back():
            return None
        self.index -= 1
        return self.entries[self.index]

    def forward(self):
        if not self.can_go_forward():
            return None
        self.index += 1
        return self.entries[self.index]

    @property
    def current(self):
        if 0 <= self.index < len(self.entries):
            return self.entries[self.index]
        return None
