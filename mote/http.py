"""A minimal HTTP/1.1 client built directly on sockets and TLS.

There is no ``http.client``, ``urllib`` or ``requests`` here: requests are
written onto the socket by hand and responses are parsed byte by byte.
Supports keep-alive with a connection pool, chunked transfer coding,
gzip/deflate content coding, redirects and cookies.
"""

from __future__ import annotations

import gzip
import socket
import ssl
import time
import zlib

from .url import URL, URLError

USER_AGENT = "Mote/0.1 (+https://github.com/ctbot000/web-browser-from-scratch)"
MAX_BODY = 32 * 1024 * 1024
MAX_REDIRECTS = 10
CONNECT_TIMEOUT = 10.0
READ_TIMEOUT = 20.0


class HTTPError(Exception):
    def __init__(self, message, status=None, url=None):
        super().__init__(message)
        self.status = status
        self.url = url


class Headers:
    """Case-insensitive, order-preserving, multi-valued header store."""

    def __init__(self, pairs=()):
        self._pairs = []
        for name, value in pairs:
            self.add(name, value)

    def add(self, name, value):
        self._pairs.append((name, value))

    def set(self, name, value):
        lowered = name.lower()
        self._pairs = [p for p in self._pairs if p[0].lower() != lowered]
        self._pairs.append((name, value))

    def get(self, name, default=None):
        lowered = name.lower()
        for key, value in self._pairs:
            if key.lower() == lowered:
                return value
        return default

    def get_all(self, name):
        lowered = name.lower()
        return [v for k, v in self._pairs if k.lower() == lowered]

    def __contains__(self, name):
        return self.get(name) is not None

    def __iter__(self):
        return iter(self._pairs)

    def __repr__(self):
        return "Headers(%r)" % (self._pairs,)


class Response:
    def __init__(self, status, reason, headers, body, url, version="HTTP/1.1"):
        self.status = status
        self.reason = reason
        self.headers = headers
        self.body = body                 # bytes, already decoded from any coding
        self.url = url                   # final URL after redirects
        self.version = version
        self.from_cache = False

    @property
    def ok(self):
        return 200 <= self.status < 300

    @property
    def content_type(self):
        return parse_content_type(self.headers.get("content-type", ""))

    def __repr__(self):
        return "<Response %d %s %s %d bytes>" % (
            self.status, self.reason, self.url, len(self.body))


def parse_content_type(value):
    """Return ``(mime, charset)`` from a Content-Type header value."""
    mime, _, params = value.partition(";")
    mime = mime.strip().lower()
    charset = None
    for part in params.split(";"):
        key, _, val = part.partition("=")
        if key.strip().lower() == "charset":
            charset = val.strip().strip('"').strip("'").lower() or None
    return mime, charset


# --------------------------------------------------------------------- socket

class _Reader:
    """Buffered reader over a socket, with line and exact-count reads."""

    def __init__(self, sock):
        self.sock = sock
        self.buf = bytearray()
        self.eof = False

    def _fill(self):
        if self.eof:
            return 0
        chunk = self.sock.recv(65536)
        if not chunk:
            self.eof = True
            return 0
        self.buf.extend(chunk)
        return len(chunk)

    def read_line(self, limit=65536):
        while True:
            index = self.buf.find(b"\n")
            if index >= 0:
                line = bytes(self.buf[:index + 1])
                del self.buf[:index + 1]
                return line
            if len(self.buf) > limit:
                raise HTTPError("header line too long")
            if not self._fill():
                line = bytes(self.buf)
                self.buf.clear()
                return line

    def read_exactly(self, count):
        while len(self.buf) < count:
            if not self._fill():
                break
        data = bytes(self.buf[:count])
        del self.buf[:count]
        return data

    def read_until_eof(self, limit=MAX_BODY):
        while not self.eof and len(self.buf) < limit:
            self._fill()
        data = bytes(self.buf)
        self.buf.clear()
        return data


class Connection:
    """One TCP (optionally TLS) connection, reusable across requests."""

    def __init__(self, scheme, host, port, verify=True):
        self.scheme, self.host, self.port = scheme, host, port
        self.sock = None
        self.reader = None
        self.verify = verify
        self.last_used = 0.0

    def connect(self):
        sock = socket.create_connection((self.host, self.port), CONNECT_TIMEOUT)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        if self.scheme == "https":
            context = ssl.create_default_context()
            if not self.verify:
                context.check_hostname = False
                context.verify_mode = ssl.CERT_NONE
            # We speak HTTP/1.1 only, so never negotiate h2.
            try:
                context.set_alpn_protocols(["http/1.1"])
            except NotImplementedError:
                pass
            sock = context.wrap_socket(sock, server_hostname=self.host)
        sock.settimeout(READ_TIMEOUT)
        self.sock = sock
        self.reader = _Reader(sock)
        self.last_used = time.time()

    def close(self):
        """Shut down cleanly so an unread body cannot turn FIN into RST."""
        if self.sock is None:
            return
        try:
            self.sock.shutdown(socket.SHUT_WR)
            self.sock.settimeout(0.2)
            while self.sock.recv(65536):
                pass
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass
        self.sock = None
        self.reader = None

    def send(self, data):
        self.sock.sendall(data)


class ConnectionPool:
    def __init__(self, idle_timeout=30.0):
        self._idle = {}
        self.idle_timeout = idle_timeout

    def acquire(self, scheme, host, port, verify=True):
        key = (scheme, host, port)
        while self._idle.get(key):
            conn = self._idle[key].pop()
            if time.time() - conn.last_used < self.idle_timeout:
                return conn
            conn.close()
        conn = Connection(scheme, host, port, verify)
        conn.connect()
        return conn

    def release(self, conn):
        key = (conn.scheme, conn.host, conn.port)
        conn.last_used = time.time()
        self._idle.setdefault(key, []).append(conn)

    def close_all(self):
        for conns in self._idle.values():
            for conn in conns:
                conn.close()
        self._idle.clear()


# -------------------------------------------------------------------- cookies

class Cookie:
    __slots__ = ("name", "value", "domain", "path", "expires", "secure",
                 "http_only", "host_only")

    def __init__(self, name, value, domain, path, expires=None, secure=False,
                 http_only=False, host_only=True):
        self.name, self.value = name, value
        self.domain, self.path = domain, path
        self.expires, self.secure = expires, secure
        self.http_only, self.host_only = http_only, host_only

    def matches(self, url):
        host = url.host.lower()
        if self.host_only:
            if host != self.domain:
                return False
        elif not (host == self.domain or host.endswith("." + self.domain)):
            return False
        if self.secure and url.scheme != "https":
            return False
        path = url.path or "/"
        if not (path == self.path or path.startswith(self.path.rstrip("/") + "/")
                or self.path == "/"):
            return False
        if self.expires is not None and self.expires < time.time():
            return False
        return True


class CookieJar:
    def __init__(self):
        self._cookies = {}

    def set_from_header(self, header_value, url):
        parts = header_value.split(";")
        if not parts or "=" not in parts[0]:
            return
        name, _, value = parts[0].partition("=")
        cookie = Cookie(name.strip(), value.strip(), url.host.lower(),
                        _default_cookie_path(url))
        for attr in parts[1:]:
            key, _, val = attr.partition("=")
            key, val = key.strip().lower(), val.strip()
            if key == "domain" and val:
                cookie.domain = val.lstrip(".").lower()
                cookie.host_only = False
            elif key == "path" and val.startswith("/"):
                cookie.path = val
            elif key == "secure":
                cookie.secure = True
            elif key == "httponly":
                cookie.http_only = True
            elif key == "max-age":
                try:
                    cookie.expires = time.time() + int(val)
                except ValueError:
                    pass
            elif key == "expires" and cookie.expires is None:
                cookie.expires = _parse_http_date(val)
        if cookie.expires is not None and cookie.expires < time.time():
            self._cookies.pop((cookie.domain, cookie.path, cookie.name), None)
            return
        self._cookies[(cookie.domain, cookie.path, cookie.name)] = cookie

    def header_for(self, url):
        matches = [c for c in self._cookies.values() if c.matches(url)]
        matches.sort(key=lambda c: -len(c.path))
        if not matches:
            return None
        return "; ".join("%s=%s" % (c.name, c.value) for c in matches)

    def __len__(self):
        return len(self._cookies)


def _default_cookie_path(url):
    path = url.path or "/"
    if not path.startswith("/"):
        return "/"
    return path.rsplit("/", 1)[0] or "/"


_MONTHS = {m: i + 1 for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun",
     "jul", "aug", "sep", "oct", "nov", "dec"])}


def _parse_http_date(text):
    """Parse the three date formats RFC 7231 allows, without ``email.utils``."""
    import calendar
    import re
    text = text.strip()
    match = re.search(r"(\d{1,2})[ \-]([A-Za-z]{3})[ \-](\d{2,4})"
                      r"\s+(\d{2}):(\d{2}):(\d{2})", text)
    if not match:
        return None
    day, mon, year, hour, minute, second = match.groups()
    month = _MONTHS.get(mon.lower())
    if month is None:
        return None
    year = int(year)
    if year < 100:
        year += 2000 if year < 70 else 1900
    try:
        return calendar.timegm((year, month, int(day), int(hour),
                                int(minute), int(second), 0, 0, 0))
    except ValueError:
        return None


# ---------------------------------------------------------------------- cache

class _CacheEntry:
    __slots__ = ("response", "expires", "etag", "last_modified")

    def __init__(self, response, expires, etag, last_modified):
        self.response, self.expires = response, expires
        self.etag, self.last_modified = etag, last_modified


class HTTPCache:
    """A small in-memory cache honouring max-age, Expires and revalidation."""

    def __init__(self, capacity=200):
        self.capacity = capacity
        self._entries = {}

    def get(self, url):
        return self._entries.get(str(url.without_fragment()))

    def store(self, url, response):
        cache_control = (response.headers.get("cache-control") or "").lower()
        if "no-store" in cache_control or "private" in cache_control:
            return
        if response.status not in (200, 203, 300, 301, 308, 404, 410):
            return
        expires = None
        for directive in cache_control.split(","):
            directive = directive.strip()
            if directive.startswith("max-age="):
                try:
                    expires = time.time() + int(directive.split("=", 1)[1])
                except ValueError:
                    pass
        if expires is None and response.headers.get("expires"):
            expires = _parse_http_date(response.headers.get("expires"))
        etag = response.headers.get("etag")
        last_modified = response.headers.get("last-modified")
        if expires is None and not etag and not last_modified:
            return
        if "no-cache" in cache_control:
            expires = 0
        if len(self._entries) >= self.capacity:
            self._entries.pop(next(iter(self._entries)))
        self._entries[str(url.without_fragment())] = _CacheEntry(
            response, expires, etag, last_modified)

    def clear(self):
        self._entries.clear()


# --------------------------------------------------------------------- client

class HTTPClient:
    def __init__(self, cookies=None, cache=None, verify_tls=True,
                 on_request=None):
        self.pool = ConnectionPool()
        self.cookies = cookies if cookies is not None else CookieJar()
        self.cache = cache if cache is not None else HTTPCache()
        self.verify_tls = verify_tls
        self.on_request = on_request     # hook for logging / the UI status line

    def close(self):
        self.pool.close_all()

    def get(self, url, referrer=None, headers=None):
        return self.request("GET", url, referrer=referrer, headers=headers)

    def request(self, method, url, body=None, referrer=None, headers=None,
                max_redirects=MAX_REDIRECTS):
        if isinstance(url, str):
            url = URL.parse(url)
        seen = []
        for _ in range(max_redirects + 1):
            if url.scheme not in ("http", "https"):
                raise URLError("unsupported scheme %r" % url.scheme)
            entry = self.cache.get(url) if method == "GET" else None
            fresh = entry and entry.expires and entry.expires > time.time()
            if fresh:
                cached = entry.response
                hit = Response(cached.status, cached.reason, cached.headers,
                               cached.body, url, cached.version)
                hit.from_cache = True
                return hit

            response = self._one_request(method, url, body, referrer, headers,
                                         entry)
            if response.status == 304 and entry is not None:
                revalidated = Response(entry.response.status,
                                       entry.response.reason,
                                       entry.response.headers,
                                       entry.response.body, url)
                revalidated.from_cache = True
                return revalidated

            if response.status in (301, 302, 303, 307, 308):
                location = response.headers.get("location")
                if not location:
                    return response
                target = url.join(location)
                if str(target) in seen:
                    raise HTTPError("redirect loop at %s" % target, url=target)
                seen.append(str(target))
                if response.status == 303 or (
                        response.status in (301, 302) and method == "POST"):
                    method, body = "GET", None
                referrer, url = url, target
                continue

            if method == "GET":
                self.cache.store(response.url, response)
            return response
        raise HTTPError("too many redirects", url=url)

    # -- one round trip ---------------------------------------------------

    def _one_request(self, method, url, body, referrer, extra_headers, entry):
        if self.on_request:
            self.on_request(method, url)
        request_headers = Headers()
        host_header = url.host
        if url.port is not None and url.port != (443 if url.scheme == "https" else 80):
            host_header += ":%d" % url.port
        request_headers.set("Host", host_header)
        request_headers.set("User-Agent", USER_AGENT)
        request_headers.set("Accept",
                            "text/html,application/xhtml+xml,image/png,"
                            "image/gif,image/jpeg,*/*;q=0.8")
        request_headers.set("Accept-Encoding", "gzip, deflate")
        request_headers.set("Accept-Language", "en-US,en;q=0.9")
        request_headers.set("Connection", "keep-alive")
        if referrer is not None:
            request_headers.set("Referer", str(URL.parse(str(referrer)).without_fragment()))
        cookie_header = self.cookies.header_for(url)
        if cookie_header:
            request_headers.set("Cookie", cookie_header)
        if entry is not None:
            if entry.etag:
                request_headers.set("If-None-Match", entry.etag)
            elif entry.last_modified:
                request_headers.set("If-Modified-Since", entry.last_modified)
        if body is not None:
            request_headers.set("Content-Length", str(len(body)))
        for name, value in (extra_headers or {}).items():
            request_headers.set(name, value)

        conn = self.pool.acquire(url.scheme, url.host, url.effective_port,
                                 self.verify_tls)
        try:
            response = self._exchange(conn, method, url, request_headers, body)
        except (OSError, HTTPError):
            # A pooled connection the server closed in the meantime: retry once
            # on a brand-new socket.
            conn.close()
            conn = Connection(url.scheme, url.host, url.effective_port,
                              self.verify_tls)
            conn.connect()
            response = self._exchange(conn, method, url, request_headers, body)

        keep_alive = (response.version == "HTTP/1.1" and
                      "close" not in (response.headers.get("connection") or "").lower())
        if keep_alive:
            self.pool.release(conn)
        else:
            conn.close()

        for value in response.headers.get_all("set-cookie"):
            self.cookies.set_from_header(value, url)
        return response

    def _exchange(self, conn, method, url, headers, body):
        lines = ["%s %s HTTP/1.1" % (method, url.request_target)]
        for name, value in headers:
            lines.append("%s: %s" % (name, value))
        raw = ("\r\n".join(lines) + "\r\n\r\n").encode("latin-1")
        if body:
            raw += body
        conn.send(raw)

        status_line = conn.reader.read_line().decode("latin-1").rstrip("\r\n")
        if not status_line:
            raise HTTPError("empty response from %s" % url, url=url)
        parts = status_line.split(" ", 2)
        if len(parts) < 2 or not parts[1].isdigit():
            raise HTTPError("malformed status line %r" % status_line, url=url)
        version, status = parts[0], int(parts[1])
        reason = parts[2] if len(parts) > 2 else ""

        response_headers = Headers()
        last_name = None
        while True:
            line = conn.reader.read_line().decode("latin-1")
            if line in ("\r\n", "\n", ""):
                break
            if line[0] in " \t" and last_name is not None:      # obs-fold
                pairs = response_headers._pairs
                pairs[-1] = (pairs[-1][0], pairs[-1][1] + " " + line.strip())
                continue
            name, _, value = line.partition(":")
            if not _:
                continue
            last_name = name.strip()
            response_headers.add(last_name, value.strip())

        raw_body = self._read_body(conn, method, status, response_headers)
        decoded = decode_content(raw_body, response_headers.get("content-encoding"))
        return Response(status, reason, response_headers, decoded, url, version)

    def _read_body(self, conn, method, status, headers):
        if method == "HEAD" or status in (204, 304) or 100 <= status < 200:
            return b""
        transfer = (headers.get("transfer-encoding") or "").lower()
        if "chunked" in transfer:
            return self._read_chunked(conn)
        length = headers.get("content-length")
        if length is not None and length.strip().isdigit():
            return conn.reader.read_exactly(min(int(length), MAX_BODY))
        return conn.reader.read_until_eof()

    def _read_chunked(self, conn):
        out = bytearray()
        while True:
            line = conn.reader.read_line().strip()
            if not line:
                break
            size_text = line.split(b";", 1)[0]
            try:
                size = int(size_text, 16)
            except ValueError:
                raise HTTPError("bad chunk size %r" % size_text)
            if size == 0:
                while True:                      # trailers
                    trailer = conn.reader.read_line()
                    if trailer in (b"\r\n", b"\n", b""):
                        break
                break
            out.extend(conn.reader.read_exactly(size))
            conn.reader.read_exactly(2)          # CRLF after the chunk
            if len(out) > MAX_BODY:
                raise HTTPError("response body too large")
        return bytes(out)


def decode_content(data, encoding):
    if not encoding or not data:
        return data
    encoding = encoding.lower().strip()
    try:
        if encoding == "gzip":
            return gzip.decompress(data)
        if encoding == "deflate":
            try:
                return zlib.decompress(data)
            except zlib.error:
                return zlib.decompress(data, -zlib.MAX_WBITS)
        if encoding == "identity":
            return data
    except (OSError, zlib.error) as exc:
        raise HTTPError("could not decode %s body: %s" % (encoding, exc))
    return data
