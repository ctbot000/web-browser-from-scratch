"""Fetching a URL, whatever its scheme, and turning the bytes into text."""

from __future__ import annotations

import os

from .encoding import decode
from .http import HTTPClient, HTTPError, parse_content_type
from .url import URL, URLError, percent_decode


class Resource:
    __slots__ = ("url", "data", "mime", "charset", "status", "headers",
                 "from_cache", "error")

    def __init__(self, url, data=b"", mime="", charset=None, status=200,
                 headers=None, from_cache=False, error=None):
        self.url = url
        self.data = data
        self.mime = mime
        self.charset = charset
        self.status = status
        self.headers = headers
        self.from_cache = from_cache
        self.error = error

    @property
    def ok(self):
        return self.error is None and 200 <= self.status < 300

    def text(self, sniff_html=True):
        text, _ = decode(self.data, self.charset,
                         sniff=sniff_html and self.mime in
                         ("text/html", "application/xhtml+xml", ""))
        return text

    def __repr__(self):
        return "<Resource %s %s %d bytes>" % (self.url, self.mime,
                                              len(self.data))


class Loader:
    """Scheme dispatch in front of the HTTP client."""

    def __init__(self, client=None):
        self.client = client if client is not None else HTTPClient()

    def close(self):
        self.client.close()

    def fetch(self, url, referrer=None, accept=None):
        if isinstance(url, str):
            try:
                url = URL.parse(url)
            except URLError as exc:
                return Resource(url, error=str(exc), status=0)
        scheme = url.scheme or "http"
        handler = getattr(self, "_fetch_" + scheme.replace("-", "_"), None)
        if handler is None:
            return Resource(url, error="unsupported scheme %r" % scheme,
                            status=0)
        try:
            return handler(url, referrer)
        except (HTTPError, OSError, URLError, ValueError) as exc:
            return Resource(url, error=str(exc), status=0)

    # -- schemes ----------------------------------------------------------

    def _fetch_http(self, url, referrer):
        response = self.client.get(url, referrer=referrer)
        mime, charset = parse_content_type(
            response.headers.get("content-type", ""))
        return Resource(response.url, response.body, mime, charset,
                        response.status, response.headers, response.from_cache)

    _fetch_https = _fetch_http

    def _fetch_file(self, url, referrer):
        path = percent_decode(url.path).decode("utf-8", "replace")
        if os.path.isdir(path):
            return Resource(url, _directory_listing(path).encode("utf-8"),
                            "text/html", "utf-8")
        with open(path, "rb") as handle:
            data = handle.read()
        return Resource(url, data, _guess_mime(path), None)

    def _fetch_data(self, url, referrer):
        payload = url.opaque or ""
        header, _, body = payload.partition(",")
        mime = header.split(";")[0] or "text/plain"
        charset = None
        for parameter in header.split(";")[1:]:
            if parameter.lower().startswith("charset="):
                charset = parameter.split("=", 1)[1]
        if header.lower().endswith(";base64"):
            import base64
            mime = header[:-7].split(";")[0] or "text/plain"
            padded = body + "=" * (-len(body) % 4)
            data = base64.b64decode(padded.encode("ascii"), validate=False)
        else:
            data = percent_decode(body)
        return Resource(url, data, mime, charset)

    def _fetch_about(self, url, referrer):
        page = (url.opaque or "").strip().lower()
        if page in ("", "blank"):
            return Resource(url, b"<html><body></body></html>", "text/html",
                            "utf-8")
        from .about import about_page
        body = about_page(page)
        if body is None:
            return Resource(url, error="no such about: page", status=404)
        return Resource(url, body.encode("utf-8"), "text/html", "utf-8")


_MIME_BY_EXTENSION = {
    ".html": "text/html", ".htm": "text/html", ".xhtml": "application/xhtml+xml",
    ".css": "text/css", ".js": "text/javascript", ".json": "application/json",
    ".txt": "text/plain", ".md": "text/plain", ".xml": "text/xml",
    ".png": "image/png", ".gif": "image/gif", ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg", ".webp": "image/webp", ".svg": "image/svg+xml",
    ".ico": "image/x-icon", ".bmp": "image/bmp",
}


def _guess_mime(path):
    _, extension = os.path.splitext(path.lower())
    return _MIME_BY_EXTENSION.get(extension, "application/octet-stream")


def _directory_listing(path):
    from .htmlparse import HTMLParser  # noqa: F401  (documentation of intent)
    entries = sorted(os.listdir(path))
    rows = []
    parent = os.path.dirname(path.rstrip("/"))
    if parent and parent != path:
        rows.append('<li><a href="file://%s/">../</a></li>' % parent)
    for name in entries:
        full = os.path.join(path, name)
        suffix = "/" if os.path.isdir(full) else ""
        rows.append('<li><a href="file://%s%s">%s%s</a></li>'
                    % (full, suffix, _escape(name), suffix))
    return ("<!doctype html><html><head><title>Index of %s</title></head>"
            "<body><h1>Index of %s</h1><ul>%s</ul></body></html>"
            % (_escape(path), _escape(path), "".join(rows)))


def _escape(text):
    return (text.replace("&", "&amp;").replace("<", "&lt;")
                .replace(">", "&gt;"))
