"""Deciding what bytes mean: the character-encoding sniffing algorithm."""

from __future__ import annotations

import codecs
import re

_BOMS = (
    (codecs.BOM_UTF8, "utf-8-sig"),
    (codecs.BOM_UTF32_LE, "utf-32-le"),
    (codecs.BOM_UTF32_BE, "utf-32-be"),
    (codecs.BOM_UTF16_LE, "utf-16-le"),
    (codecs.BOM_UTF16_BE, "utf-16-be"),
)

_ALIASES = {
    "utf8": "utf-8", "utf-8": "utf-8", "iso-8859-1": "windows-1252",
    "latin1": "windows-1252", "latin-1": "windows-1252",
    "iso8859-1": "windows-1252", "ascii": "windows-1252",
    "us-ascii": "windows-1252", "cp1252": "windows-1252",
    "euc-kr": "euc_kr", "ks_c_5601-1987": "cp949", "ksc5601": "cp949",
    "shift_jis": "shift_jis", "sjis": "shift_jis", "euc-jp": "euc_jp",
    "gb2312": "gbk", "gb_2312": "gbk", "x-gbk": "gbk", "gb18030": "gb18030",
    "big5": "big5", "utf-16": "utf-16", "utf-16le": "utf-16-le",
    "utf-16be": "utf-16-be",
}

_META_CHARSET = re.compile(
    rb"""<meta[^>]*?charset\s*=\s*["']?\s*([a-zA-Z0-9_\-:.]+)""", re.I)
_META_HTTP_EQUIV = re.compile(
    rb"""<meta[^>]+http-equiv\s*=\s*["']?content-type["']?[^>]*>""", re.I)


def normalize(name):
    if not name:
        return None
    name = name.strip().strip("\"'").lower()
    name = _ALIASES.get(name, name)
    try:
        codecs.lookup(name)
    except (LookupError, TypeError):
        return None
    return name


def sniff_meta_charset(data, limit=2048):
    """Look for a <meta charset> declaration in the first bytes of a page."""
    head = data[:limit]
    match = _META_CHARSET.search(head)
    if match:
        found = normalize(match.group(1).decode("ascii", "replace"))
        if found:
            return found
    for tag in _META_HTTP_EQUIV.finditer(head):
        inner = _META_CHARSET.search(tag.group(0))
        if inner:
            found = normalize(inner.group(1).decode("ascii", "replace"))
            if found:
                return found
    return None


def decode(data, declared=None, default="utf-8", sniff=True):
    """Return ``(text, encoding_used)``."""
    for bom, name in _BOMS:
        if data.startswith(bom):
            return data.decode(name, "replace"), name

    candidates = []
    declared = normalize(declared)
    if declared:
        candidates.append(declared)
    if sniff:
        sniffed = sniff_meta_charset(data)
        if sniffed and sniffed not in candidates:
            candidates.append(sniffed)
    for name in (default, "utf-8", "windows-1252"):
        name = normalize(name)
        if name and name not in candidates:
            candidates.append(name)

    for name in candidates:
        try:
            return data.decode(name), name
        except (UnicodeDecodeError, LookupError):
            continue
    fallback = candidates[0] if candidates else "utf-8"
    return data.decode(fallback, "replace"), fallback
