"""URL parsing, resolution and serialisation (RFC 3986, with web quirks).

Written from scratch: nothing in here calls ``urllib``.
"""

from __future__ import annotations

import re
import string

DEFAULT_PORTS = {"http": 80, "https": 443, "ws": 80, "wss": 443, "ftp": 21}

# Schemes the address bar recognises.  Anything else with a colon in it is
# far more likely to be "localhost:8000" than a URL in an unknown scheme.
KNOWN_SCHEMES = {"http", "https", "file", "data", "about", "view-source",
                 "ftp", "mailto", "javascript", "blob", "ws", "wss"}

# Schemes whose payload is opaque -- there is no authority and no path to
# normalise, so everything after the colon is kept verbatim.
OPAQUE_SCHEMES = {"data", "about", "javascript", "mailto", "blob"}

_SCHEME_RE = re.compile(r"^([a-zA-Z][a-zA-Z0-9+.\-]*):")

UNRESERVED = set(string.ascii_letters + string.digits + "-._~")
SUB_DELIMS = set("!$&'()*+,;=")
# Characters that may stay literal inside a path we hand to a server.
PATH_SAFE = UNRESERVED | SUB_DELIMS | set(":@/%")
QUERY_SAFE = PATH_SAFE | set("?")


class URLError(ValueError):
    """Raised for input that cannot be understood as a URL at all."""


def percent_encode(text: str, safe: set) -> str:
    out = []
    for byte in text.encode("utf-8"):
        char = chr(byte)
        if char in safe:
            out.append(char)
        else:
            out.append("%%%02X" % byte)
    return "".join(out)


def percent_decode(text: str) -> bytes:
    out = bytearray()
    i = 0
    while i < len(text):
        char = text[i]
        if char == "%" and i + 2 < len(text) + 1:
            hexpair = text[i + 1:i + 3]
            if len(hexpair) == 2 and all(c in string.hexdigits for c in hexpair):
                out.append(int(hexpair, 16))
                i += 3
                continue
        out.extend(char.encode("utf-8"))
        i += 1
    return bytes(out)


def remove_dot_segments(path: str) -> str:
    """RFC 3986 section 5.2.4."""
    out = []
    for segment in path.split("/"):
        if segment == ".":
            # Drop it, but keep a trailing slash if this was the last segment.
            continue
        if segment == "..":
            if out and out[-1] != "":
                out.pop()
            continue
        out.append(segment)
    result = "/".join(out)
    # A path that ended in "." or ".." denotes a directory.
    if path.endswith(("/.", "/..")) and not result.endswith("/"):
        result += "/"
    if path.startswith("/") and not result.startswith("/"):
        result = "/" + result
    return result


class URL:
    """An absolute or relative URL, kept in decomposed form."""

    __slots__ = ("scheme", "userinfo", "host", "port", "path", "query",
                 "fragment", "opaque")

    def __init__(self, scheme="", userinfo="", host="", port=None, path="",
                 query=None, fragment=None, opaque=None):
        self.scheme = scheme.lower()
        self.userinfo = userinfo
        self.host = host
        self.port = port
        self.path = path
        self.query = query
        self.fragment = fragment
        self.opaque = opaque

    # ---------------------------------------------------------------- parsing

    @classmethod
    def parse(cls, text: str) -> "URL":
        if text is None:
            raise URLError("no URL given")
        text = text.strip().replace("\t", "").replace("\n", "").replace("\r", "")

        scheme = ""
        match = _SCHEME_RE.match(text)
        if match:
            scheme = match.group(1).lower()
            rest = text[match.end():]
        else:
            rest = text

        if scheme in OPAQUE_SCHEMES:
            payload, fragment = _split_once(rest, "#")
            return cls(scheme=scheme, opaque=payload, fragment=fragment)

        fragment = None
        if "#" in rest:
            rest, fragment = rest.split("#", 1)

        query = None
        if "?" in rest:
            rest, query = rest.split("?", 1)

        userinfo = host = ""
        port = None
        if rest.startswith("//"):
            authority, rest = _split_authority(rest[2:])
            userinfo, host, port = _parse_authority(authority)
        elif scheme and not rest.startswith("/"):
            # e.g. "mailto:" style that we did not list, treat as opaque.
            return cls(scheme=scheme, opaque=rest, fragment=fragment)

        return cls(scheme=scheme, userinfo=userinfo, host=host, port=port,
                   path=rest, query=query, fragment=fragment)

    @classmethod
    def from_user_input(cls, text: str, search_template: str = None) -> "URL":
        """Interpret what someone typed into the address bar.

        Adds a missing scheme, and falls back to a web search when the text
        plainly is not a URL.
        """
        text = text.strip()
        if not text:
            raise URLError("empty address")
        match = _SCHEME_RE.match(text)
        if match and match.group(1).lower() in KNOWN_SCHEMES:
            return cls.parse(text)
        if text.startswith("/") or text.startswith("./") or text.startswith("~"):
            import os
            return cls.parse("file://" + os.path.abspath(os.path.expanduser(text)))
        looks_like_host = bool(
            re.match(r"^[\w.\-]+(:\d+)?(/|$|\?)", text)
            and ("." in text.split("/")[0].split(":")[0] or
                 text.split("/")[0].split(":")[0] == "localhost")
        )
        if looks_like_host:
            return cls.parse("http://" + text)
        if search_template:
            return cls.parse(search_template.replace(
                "%s", percent_encode(text, UNRESERVED)))
        return cls.parse("http://" + text)

    # ------------------------------------------------------------- resolution

    def join(self, reference) -> "URL":
        """RFC 3986 section 5.3: resolve *reference* against this base URL."""
        ref = reference if isinstance(reference, URL) else URL.parse(str(reference))

        if ref.scheme and (ref.scheme in OPAQUE_SCHEMES or ref.host or
                           ref.path.startswith("/") or not self.scheme):
            return ref
        if ref.scheme and ref.scheme != self.scheme:
            return ref

        target = URL(scheme=self.scheme)
        if ref.host:
            target.userinfo, target.host, target.port = ref.userinfo, ref.host, ref.port
            target.path = remove_dot_segments(ref.path)
            target.query = ref.query
        else:
            target.userinfo, target.host, target.port = self.userinfo, self.host, self.port
            if not ref.path:
                target.path = self.path
                target.query = self.query if ref.query is None else ref.query
            else:
                if ref.path.startswith("/"):
                    target.path = remove_dot_segments(ref.path)
                else:
                    target.path = remove_dot_segments(_merge(self, ref.path))
                target.query = ref.query
        target.fragment = ref.fragment
        return target

    # ---------------------------------------------------------- serialisation

    def __str__(self) -> str:
        if self.opaque is not None:
            out = "%s:%s" % (self.scheme, self.opaque)
            if self.fragment is not None:
                out += "#" + self.fragment
            return out
        out = ""
        if self.scheme:
            out += self.scheme + ":"
        if self.host or self.scheme == "file":
            out += "//"
            if self.userinfo:
                out += self.userinfo + "@"
            out += self.host
            if self.port is not None and self.port != DEFAULT_PORTS.get(self.scheme):
                out += ":%d" % self.port
        out += self.path
        if self.query is not None:
            out += "?" + self.query
        if self.fragment is not None:
            out += "#" + self.fragment
        return out

    __repr__ = lambda self: "URL(%r)" % str(self)  # noqa: E731

    def __eq__(self, other):
        return isinstance(other, URL) and str(self) == str(other)

    def __hash__(self):
        return hash(str(self))

    # -------------------------------------------------------------- accessors

    @property
    def effective_port(self) -> int:
        if self.port is not None:
            return self.port
        return DEFAULT_PORTS.get(self.scheme, 80)

    @property
    def origin(self):
        return (self.scheme, self.host.lower(), self.effective_port)

    @property
    def request_target(self) -> str:
        """The path+query that goes on the HTTP request line."""
        path = self.path or "/"
        if not path.startswith("/"):
            path = "/" + path
        path = percent_encode(path, PATH_SAFE)
        if self.query is not None:
            path += "?" + percent_encode(self.query, QUERY_SAFE)
        return path

    def without_fragment(self) -> "URL":
        clone = URL(self.scheme, self.userinfo, self.host, self.port, self.path,
                    self.query, None, self.opaque)
        return clone

    def is_absolute(self) -> bool:
        return bool(self.scheme)


def _split_once(text, sep):
    if sep in text:
        left, right = text.split(sep, 1)
        return left, right
    return text, None


def _split_authority(rest):
    for i, char in enumerate(rest):
        if char in "/?#":
            return rest[:i], rest[i:]
    return rest, ""


def _parse_authority(authority):
    userinfo = ""
    if "@" in authority:
        userinfo, authority = authority.rsplit("@", 1)
    port = None
    if authority.startswith("["):          # IPv6 literal
        close = authority.find("]")
        host = authority[:close + 1]
        tail = authority[close + 1:]
        if tail.startswith(":") and tail[1:]:
            port = int(tail[1:])
    elif ":" in authority:
        host, _, portstr = authority.rpartition(":")
        if portstr.isdigit():
            port = int(portstr)
        elif portstr:
            raise URLError("bad port in %r" % authority)
        else:
            port = None
    else:
        host = authority
    return userinfo, host.lower(), port


def _merge(base: URL, relative_path: str) -> str:
    if base.host and not base.path:
        return "/" + relative_path
    head = base.path.rsplit("/", 1)[0]
    return head + "/" + relative_path
