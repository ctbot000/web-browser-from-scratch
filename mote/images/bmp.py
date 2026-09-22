"""BMP and PNM decoding: simple formats, useful as a sanity check."""

from __future__ import annotations

import struct

from . import Bitmap, ImageError


def decode_bmp(data):
    if data[:2] != b"BM":
        raise ImageError("not a BMP")
    pixel_offset = struct.unpack("<I", data[10:14])[0]
    header_size = struct.unpack("<I", data[14:18])[0]
    if header_size < 12:
        raise ImageError("unsupported BMP header")
    width, height = struct.unpack("<ii", data[18:26])
    planes, depth = struct.unpack("<HH", data[26:30])
    compression = struct.unpack("<I", data[30:34])[0] if header_size >= 20 else 0
    if compression not in (0, 3):
        raise ImageError("compressed BMPs are not supported")
    if depth not in (24, 32):
        raise ImageError("only 24- and 32-bit BMPs are supported")
    del planes

    bottom_up = height > 0
    height = abs(height)
    bitmap = Bitmap(width, height)
    stride = ((width * depth + 31) // 32) * 4
    bytes_per_pixel = depth // 8
    for row in range(height):
        source_row = (height - 1 - row) if bottom_up else row
        start = pixel_offset + source_row * stride
        for x in range(width):
            index = start + x * bytes_per_pixel
            if index + bytes_per_pixel > len(data):
                break
            out = (row * width + x) * 4
            bitmap.pixels[out] = data[index + 2]
            bitmap.pixels[out + 1] = data[index + 1]
            bitmap.pixels[out + 2] = data[index]
            bitmap.pixels[out + 3] = data[index + 3] if depth == 32 else 255
    return bitmap


def decode_pnm(data):
    magic = data[:2]
    if magic not in (b"P5", b"P6"):
        raise ImageError("unsupported PNM type")
    fields, position = [], 2
    while len(fields) < 3 and position < len(data):
        while position < len(data) and data[position:position + 1].isspace():
            position += 1
        if data[position:position + 1] == b"#":
            while position < len(data) and data[position] != 0x0A:
                position += 1
            continue
        start = position
        while position < len(data) and not data[position:position + 1].isspace():
            position += 1
        fields.append(int(data[start:position]))
    position += 1
    width, height, _ = fields
    channels = 1 if magic == b"P5" else 3
    bitmap = Bitmap(width, height)
    for i in range(width * height):
        index = position + i * channels
        if index + channels > len(data):
            break
        out = i * 4
        if channels == 1:
            value = data[index]
            bitmap.pixels[out] = bitmap.pixels[out + 1] = \
                bitmap.pixels[out + 2] = value
        else:
            bitmap.pixels[out] = data[index]
            bitmap.pixels[out + 1] = data[index + 1]
            bitmap.pixels[out + 2] = data[index + 2]
        bitmap.pixels[out + 3] = 255
    return bitmap
