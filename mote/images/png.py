"""PNG decoding: chunks, scanline filters, palettes and Adam7 interlacing."""

from __future__ import annotations

import struct
import zlib

from . import Bitmap, ImageError

SIGNATURE = b"\x89PNG\r\n\x1a\n"
CHANNELS = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}

# Adam7: (x offset, y offset, x step, y step) for each of the seven passes.
ADAM7 = ((0, 0, 8, 8), (4, 0, 8, 8), (0, 4, 4, 8), (2, 0, 4, 4),
         (0, 2, 2, 4), (1, 0, 2, 2), (0, 1, 1, 2))


def decode_png(data):
    if not data.startswith(SIGNATURE):
        raise ImageError("not a PNG")
    position = len(SIGNATURE)
    header = None
    palette = None
    transparency = None
    idat = bytearray()
    background = None

    while position + 8 <= len(data):
        length, tag = struct.unpack(">I4s", data[position:position + 8])
        payload = data[position + 8:position + 8 + length]
        position += 12 + length
        if tag == b"IHDR":
            header = _Header(*struct.unpack(">IIBBBBB", payload[:13]))
            if header.compression != 0 or header.filter != 0:
                raise ImageError("unsupported PNG compression or filter")
            if header.color_type not in CHANNELS:
                raise ImageError("unknown PNG colour type %d" %
                                 header.color_type)
        elif tag == b"PLTE":
            palette = payload
        elif tag == b"tRNS":
            transparency = payload
        elif tag == b"bKGD":
            background = payload
        elif tag == b"IDAT":
            idat.extend(payload)
        elif tag == b"IEND":
            break
    if header is None:
        raise ImageError("PNG has no header")
    if not idat:
        raise ImageError("PNG has no image data")

    try:
        raw = zlib.decompress(bytes(idat))
    except zlib.error as exc:
        raise ImageError("corrupt PNG stream: %s" % exc)

    channels = CHANNELS[header.color_type]
    depth = header.depth
    if depth not in (1, 2, 4, 8, 16):
        raise ImageError("unsupported PNG bit depth %d" % depth)

    if header.interlace == 1:
        samples = _deinterlace(raw, header, channels)
    else:
        samples = _unfilter_pass(raw, header.width, header.height, channels,
                                 depth)
    return _to_bitmap(samples, header, channels, palette, transparency)


class _Header:
    __slots__ = ("width", "height", "depth", "color_type", "compression",
                 "filter", "interlace")

    def __init__(self, width, height, depth, color_type, compression,
                 filter_method, interlace):
        self.width, self.height = width, height
        self.depth, self.color_type = depth, color_type
        self.compression, self.filter = compression, filter_method
        self.interlace = interlace


def _unfilter_pass(raw, width, height, channels, depth):
    """Reverse the per-scanline filters, returning unpacked samples."""
    if width == 0 or height == 0:
        return []
    bits_per_pixel = channels * depth
    stride = (width * bits_per_pixel + 7) // 8
    step = max(1, bits_per_pixel // 8)

    rows = []
    previous = bytearray(stride)
    offset = 0
    for _ in range(height):
        if offset >= len(raw):
            break
        filter_type = raw[offset]
        offset += 1
        line = bytearray(raw[offset:offset + stride])
        if len(line) < stride:
            line.extend(b"\x00" * (stride - len(line)))
        offset += stride

        if filter_type == 1:
            for i in range(step, stride):
                line[i] = (line[i] + line[i - step]) & 0xFF
        elif filter_type == 2:
            for i in range(stride):
                line[i] = (line[i] + previous[i]) & 0xFF
        elif filter_type == 3:
            for i in range(stride):
                left = line[i - step] if i >= step else 0
                line[i] = (line[i] + ((left + previous[i]) >> 1)) & 0xFF
        elif filter_type == 4:
            for i in range(stride):
                left = line[i - step] if i >= step else 0
                up = previous[i]
                upper_left = previous[i - step] if i >= step else 0
                line[i] = (line[i] + _paeth(left, up, upper_left)) & 0xFF
        elif filter_type != 0:
            raise ImageError("unknown PNG filter %d" % filter_type)
        rows.append(_unpack(line, width, channels, depth))
        previous = line
    return rows


def _paeth(a, b, c):
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    return b if pb <= pc else c


def _unpack(line, width, channels, depth):
    count = width * channels
    if depth == 8:
        return list(line[:count])
    if depth == 16:
        return [line[i * 2] << 8 | line[i * 2 + 1] for i in range(count)]
    out = []
    mask = (1 << depth) - 1
    per_byte = 8 // depth
    for index in range(count):
        byte = line[index // per_byte]
        shift = 8 - depth * (index % per_byte + 1)
        out.append((byte >> shift) & mask)
    return out


def _deinterlace(raw, header, channels):
    full = [[0] * (header.width * channels) for _ in range(header.height)]
    offset = 0
    for x_start, y_start, x_step, y_step in ADAM7:
        pass_width = (header.width - x_start + x_step - 1) // x_step
        pass_height = (header.height - y_start + y_step - 1) // y_step
        if pass_width <= 0 or pass_height <= 0:
            continue
        stride = (pass_width * channels * header.depth + 7) // 8
        consumed = pass_height * (stride + 1)
        rows = _unfilter_pass(raw[offset:offset + consumed], pass_width,
                              pass_height, channels, header.depth)
        offset += consumed
        for row_index, row in enumerate(rows):
            target_y = y_start + row_index * y_step
            if target_y >= header.height:
                break
            for column in range(pass_width):
                target_x = x_start + column * x_step
                if target_x >= header.width:
                    break
                for channel in range(channels):
                    full[target_y][target_x * channels + channel] = \
                        row[column * channels + channel]
    return full


def _to_bitmap(rows, header, channels, palette, transparency):
    width, height = header.width, header.height
    bitmap = Bitmap(width, height)
    pixels = bitmap.pixels
    depth = header.depth
    maximum = (1 << depth) - 1
    color_type = header.color_type

    transparent_index = set()
    palette_alpha = []
    if color_type == 3 and transparency:
        palette_alpha = list(transparency)
    single_transparent = None
    if color_type in (0, 2) and transparency and len(transparency) >= 2:
        values = [transparency[i] << 8 | transparency[i + 1]
                  for i in range(0, len(transparency), 2)]
        single_transparent = values

    for y in range(height):
        row = rows[y] if y < len(rows) else []
        base = y * width * 4
        for x in range(width):
            index = x * channels
            if index + channels > len(row):
                break
            out = base + x * 4
            if color_type == 3:
                entry = row[index]
                if palette is None or entry * 3 + 2 >= len(palette):
                    pixels[out:out + 4] = b"\x00\x00\x00\xff"
                    continue
                pixels[out] = palette[entry * 3]
                pixels[out + 1] = palette[entry * 3 + 1]
                pixels[out + 2] = palette[entry * 3 + 2]
                pixels[out + 3] = palette_alpha[entry] \
                    if entry < len(palette_alpha) else 255
                continue
            if color_type == 0:
                value = _scale(row[index], maximum)
                alpha = 255
                if single_transparent and row[index] == single_transparent[0]:
                    alpha = 0
                pixels[out] = pixels[out + 1] = pixels[out + 2] = value
                pixels[out + 3] = alpha
            elif color_type == 4:
                value = _scale(row[index], maximum)
                pixels[out] = pixels[out + 1] = pixels[out + 2] = value
                pixels[out + 3] = _scale(row[index + 1], maximum)
            elif color_type == 2:
                alpha = 255
                if single_transparent and len(single_transparent) >= 3 and \
                        list(row[index:index + 3]) == single_transparent[:3]:
                    alpha = 0
                pixels[out] = _scale(row[index], maximum)
                pixels[out + 1] = _scale(row[index + 1], maximum)
                pixels[out + 2] = _scale(row[index + 2], maximum)
                pixels[out + 3] = alpha
            else:                                   # colour type 6: RGBA
                pixels[out] = _scale(row[index], maximum)
                pixels[out + 1] = _scale(row[index + 1], maximum)
                pixels[out + 2] = _scale(row[index + 2], maximum)
                pixels[out + 3] = _scale(row[index + 3], maximum)
    del transparent_index
    return bitmap


def _scale(value, maximum):
    if maximum == 255:
        return value
    return (value * 255 + maximum // 2) // maximum
