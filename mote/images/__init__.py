"""Image decoding.

Tk 8.5 can only display GIF and PPM, and no Python in the standard library
decodes PNG or JPEG, so the decoders here are written from scratch on top of
``zlib``.  Every decoder produces the same :class:`Bitmap`.
"""

from __future__ import annotations

import base64
import struct
import zlib


class Bitmap:
    """An RGBA image, one byte per channel, row-major from the top left."""

    __slots__ = ("width", "height", "pixels", "_cache")

    def __init__(self, width, height, pixels=None):
        self.width = int(width)
        self.height = int(height)
        self.pixels = pixels if pixels is not None else \
            bytearray(self.width * self.height * 4)
        self._cache = {}

    def pixel(self, x, y):
        offset = (y * self.width + x) * 4
        return tuple(self.pixels[offset:offset + 4])

    def composite(self, background=(255, 255, 255)):
        """Flatten alpha onto a solid colour."""
        out = bytearray(self.width * self.height * 3)
        pixels = self.pixels
        br, bg, bb = background
        for i in range(self.width * self.height):
            r, g, b, a = pixels[i * 4:i * 4 + 4]
            if a == 255:
                out[i * 3], out[i * 3 + 1], out[i * 3 + 2] = r, g, b
            elif a == 0:
                out[i * 3], out[i * 3 + 1], out[i * 3 + 2] = br, bg, bb
            else:
                alpha = a / 255.0
                out[i * 3] = int(r * alpha + br * (1 - alpha))
                out[i * 3 + 1] = int(g * alpha + bg * (1 - alpha))
                out[i * 3 + 2] = int(b * alpha + bb * (1 - alpha))
        return bytes(out)

    def resized(self, width, height):
        """Nearest-neighbour scaling: enough for laying an image into a box."""
        width, height = max(1, int(width)), max(1, int(height))
        if (width, height) == (self.width, self.height):
            return self
        key = ("resized", width, height)
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        out = bytearray(width * height * 4)
        source = self.pixels
        for y in range(height):
            src_y = min(self.height - 1, y * self.height // height)
            row = src_y * self.width
            for x in range(width):
                src_x = min(self.width - 1, x * self.width // width)
                si = (row + src_x) * 4
                di = (y * width + x) * 4
                out[di:di + 4] = source[si:si + 4]
        result = Bitmap(width, height, out)
        self._cache[key] = result
        return result

    def to_ppm(self, background=(255, 255, 255)):
        """Binary PPM, the one raster format every Tk build can read."""
        header = ("P6\n%d %d\n255\n" % (self.width, self.height)).encode("ascii")
        return header + self.composite(background)

    def to_png(self):
        return encode_png(self)

    @property
    def data_uri(self):
        cached = self._cache.get("data_uri")
        if cached is None:
            cached = "data:image/png;base64," + \
                base64.b64encode(self.to_png()).decode("ascii")
            self._cache["data_uri"] = cached
        return cached

    def __repr__(self):
        return "<Bitmap %dx%d>" % (self.width, self.height)


def encode_png(bitmap):
    """Write a PNG. The encoder is trivial next to the decoder: filter type 0
    on every row, then one deflate stream."""
    raw = bytearray()
    stride = bitmap.width * 4
    for y in range(bitmap.height):
        raw.append(0)
        raw.extend(bitmap.pixels[y * stride:(y + 1) * stride])
    compressed = zlib.compress(bytes(raw), 6)

    def chunk(tag, payload):
        return (struct.pack(">I", len(payload)) + tag + payload +
                struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF))

    header = struct.pack(">IIBBBBB", bitmap.width, bitmap.height, 8, 6, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) +
            chunk(b"IDAT", compressed) + chunk(b"IEND", b""))


class ImageError(ValueError):
    pass


def sniff_format(data):
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"GIF87a") or data.startswith(b"GIF89a"):
        return "gif"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if data.startswith(b"BM"):
        return "bmp"
    if data.startswith(b"P6") or data.startswith(b"P5"):
        return "pnm"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    if b"<svg" in data[:512].lower():
        return "svg"
    return None


def decode_image(data, mime=None):
    """Decode *data* into a :class:`Bitmap`, or raise :class:`ImageError`."""
    if not data:
        raise ImageError("empty image")
    kind = sniff_format(data)
    if kind == "png":
        from .png import decode_png
        return decode_png(data)
    if kind == "gif":
        from .gif import decode_gif
        return decode_gif(data)
    if kind == "jpeg":
        from .jpeg import decode_jpeg
        return decode_jpeg(data)
    if kind == "bmp":
        from .bmp import decode_bmp
        return decode_bmp(data)
    if kind == "pnm":
        from .bmp import decode_pnm
        return decode_pnm(data)
    raise ImageError("unsupported image format%s" %
                     (" (%s)" % mime if mime else ""))
