"""Baseline JPEG decoding: Huffman, dequantisation, IDCT and upsampling.

This covers the sequential DCT profile that virtually every photograph on the
web uses.  Progressive JPEGs are detected and reported rather than decoded.
"""

from __future__ import annotations

import math

from . import Bitmap, ImageError

ZIGZAG = (
    0, 1, 8, 16, 9, 2, 3, 10, 17, 24, 32, 25, 18, 11, 4, 5,
    12, 19, 26, 33, 40, 48, 41, 34, 27, 20, 13, 6, 7, 14, 21, 28,
    35, 42, 49, 56, 57, 50, 43, 36, 29, 22, 15, 23, 30, 37, 44, 51,
    58, 59, 52, 45, 38, 31, 39, 46, 53, 60, 61, 54, 47, 55, 62, 63,
)

# The 1-D DCT basis, precomputed once: COS[u][x] = C(u) cos((2x+1)u pi / 16).
_COS = []
for _u in range(8):
    _scale = (1.0 / math.sqrt(2.0)) if _u == 0 else 1.0
    _COS.append([_scale * math.cos((2 * _x + 1) * _u * math.pi / 16.0)
                 for _x in range(8)])
del _u, _scale

_CLAMP = bytes(max(0, min(255, value - 256)) for value in range(768))


class _Component:
    __slots__ = ("identifier", "h", "v", "quant", "dc_table", "ac_table",
                 "plane", "plane_width", "plane_height", "prediction")

    def __init__(self, identifier, h, v, quant):
        self.identifier = identifier
        self.h, self.v = h, v
        self.quant = quant
        self.dc_table = self.ac_table = 0
        self.plane = None
        self.plane_width = self.plane_height = 0
        self.prediction = 0


class _BitReader:
    """Reads entropy-coded bits, undoing the 0xFF00 byte stuffing."""

    __slots__ = ("data", "position", "bits", "count", "marker")

    def __init__(self, data, position):
        self.data = data
        self.position = position
        self.bits = 0
        self.count = 0
        self.marker = None

    def read_bit(self):
        if self.count == 0:
            if self.position >= len(self.data):
                return 0
            byte = self.data[self.position]
            self.position += 1
            if byte == 0xFF:
                following = self.data[self.position] \
                    if self.position < len(self.data) else 0
                if following == 0x00:
                    self.position += 1
                elif 0xD0 <= following <= 0xD7:
                    self.marker = following
                else:
                    self.marker = following
                    return 0
            self.bits = byte
            self.count = 8
        self.count -= 1
        return (self.bits >> self.count) & 1

    def receive(self, length):
        value = 0
        for _ in range(length):
            value = (value << 1) | self.read_bit()
        return value

    def align(self):
        self.count = 0

    def skip_restart(self):
        """Move past an RSTn marker in the stream."""
        while self.position + 1 < len(self.data):
            if self.data[self.position] == 0xFF and \
                    0xD0 <= self.data[self.position + 1] <= 0xD7:
                self.position += 2
                self.count = 0
                return True
            self.position += 1
        return False


def _extend(value, length):
    """Convert a JPEG magnitude-category value to a signed coefficient."""
    if length == 0:
        return 0
    return value if value >= (1 << (length - 1)) else value - (1 << length) + 1


def _build_huffman(counts, symbols):
    """Map (bit length, code) to a symbol, canonical-code style."""
    table = {}
    code = 0
    index = 0
    for length in range(1, 17):
        for _ in range(counts[length - 1]):
            table[(length, code)] = symbols[index]
            index += 1
            code += 1
        code <<= 1
    return table


def _decode_huffman(reader, table):
    code = 0
    for length in range(1, 17):
        code = (code << 1) | reader.read_bit()
        symbol = table.get((length, code))
        if symbol is not None:
            return symbol
    raise ImageError("invalid Huffman code in JPEG stream")


def _idct(block, output):
    """Two-pass separable inverse DCT on one 8x8 block."""
    if not any(block[1:]):
        value = block[0] * 0.125 + 128.0
        clamped = _CLAMP[int(value) + 256] if -256 <= value < 512 else \
            (0 if value < 0 else 255)
        for i in range(64):
            output[i] = clamped
        return

    intermediate = [0.0] * 64
    for y in range(8):
        row = y * 8
        if not any(block[row + 1:row + 8]):
            value = block[row] * 0.35355339059327373
            for x in range(8):
                intermediate[row + x] = value
            continue
        for x in range(8):
            total = 0.0
            for u in range(8):
                coefficient = block[row + u]
                if coefficient:
                    total += coefficient * _COS[u][x]
            intermediate[row + x] = total * 0.5

    for x in range(8):
        column = [intermediate[y * 8 + x] for y in range(8)]
        for y in range(8):
            total = 0.0
            for v in range(8):
                coefficient = column[v]
                if coefficient:
                    total += coefficient * _COS[v][y]
            value = total * 0.5 + 128.0
            index = int(value + 0.5)
            output[y * 8 + x] = _CLAMP[index + 256] if -256 <= index < 512 \
                else (0 if index < 0 else 255)


def decode_jpeg(data):
    if data[:2] != b"\xff\xd8":
        raise ImageError("not a JPEG")

    quantization = {}
    huffman_dc = {}
    huffman_ac = {}
    components = []
    width = height = 0
    restart_interval = 0
    progressive = False
    adobe_transform = None

    position = 2
    while position < len(data) - 1:
        if data[position] != 0xFF:
            position += 1
            continue
        marker = data[position + 1]
        position += 2
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            continue
        if marker == 0xD9:
            break
        if position + 2 > len(data):
            break
        length = (data[position] << 8) | data[position + 1]
        segment = data[position + 2:position + length]

        if marker == 0xDB:                                   # quantisation
            offset = 0
            while offset < len(segment):
                precision, identifier = segment[offset] >> 4, segment[offset] & 15
                offset += 1
                table = [0] * 64
                for i in range(64):
                    if precision:
                        table[ZIGZAG[i]] = (segment[offset] << 8) | \
                            segment[offset + 1]
                        offset += 2
                    else:
                        table[ZIGZAG[i]] = segment[offset]
                        offset += 1
                quantization[identifier] = table
        elif marker == 0xC4:                                 # Huffman tables
            offset = 0
            while offset < len(segment):
                table_class, identifier = segment[offset] >> 4, segment[offset] & 15
                offset += 1
                counts = list(segment[offset:offset + 16])
                offset += 16
                total = sum(counts)
                symbols = list(segment[offset:offset + total])
                offset += total
                table = _build_huffman(counts, symbols)
                if table_class == 0:
                    huffman_dc[identifier] = table
                else:
                    huffman_ac[identifier] = table
        elif marker in (0xC0, 0xC1, 0xC2):                   # frame header
            progressive = marker == 0xC2
            height = (segment[1] << 8) | segment[2]
            width = (segment[3] << 8) | segment[4]
            count = segment[5]
            components = []
            for i in range(count):
                base = 6 + i * 3
                components.append(_Component(
                    segment[base], segment[base + 1] >> 4,
                    segment[base + 1] & 15, segment[base + 2]))
        elif marker == 0xDD:                                 # restart interval
            restart_interval = (segment[0] << 8) | segment[1]
        elif marker == 0xEE and segment[:5] == b"Adobe":     # Adobe APP14
            adobe_transform = segment[11] if len(segment) > 11 else None
        elif marker == 0xDA:                                 # start of scan
            if progressive:
                raise ImageError("progressive JPEG is not supported")
            if not components or not width or not height:
                raise ImageError("JPEG scan before frame header")
            count = segment[0]
            for i in range(count):
                identifier = segment[1 + i * 2]
                tables = segment[2 + i * 2]
                for component in components:
                    if component.identifier == identifier:
                        component.dc_table = tables >> 4
                        component.ac_table = tables & 15
            scan_start = position + length
            return _decode_scan(data, scan_start, components, quantization,
                                huffman_dc, huffman_ac, width, height,
                                restart_interval, adobe_transform)
        position += length
    raise ImageError("JPEG has no scan data")


def _decode_scan(data, position, components, quantization, huffman_dc,
                 huffman_ac, width, height, restart_interval, adobe_transform):
    max_h = max(c.h for c in components)
    max_v = max(c.v for c in components)
    mcu_width, mcu_height = max_h * 8, max_v * 8
    mcus_x = (width + mcu_width - 1) // mcu_width
    mcus_y = (height + mcu_height - 1) // mcu_height

    for component in components:
        component.plane_width = mcus_x * component.h * 8
        component.plane_height = mcus_y * component.v * 8
        component.plane = bytearray(component.plane_width *
                                    component.plane_height)
        component.prediction = 0

    reader = _BitReader(data, position)
    block = [0] * 64
    output = [0] * 64
    decoded = 0

    for mcu_y in range(mcus_y):
        for mcu_x in range(mcus_x):
            if restart_interval and decoded and decoded % restart_interval == 0:
                reader.align()
                reader.skip_restart()
                for component in components:
                    component.prediction = 0
            for component in components:
                dc_table = huffman_dc.get(component.dc_table)
                ac_table = huffman_ac.get(component.ac_table)
                table = quantization.get(component.quant)
                if dc_table is None or ac_table is None or table is None:
                    raise ImageError("JPEG refers to a missing table")
                for by in range(component.v):
                    for bx in range(component.h):
                        _decode_block(reader, component, dc_table, ac_table,
                                      table, block, output)
                        _blit(component, output,
                              (mcu_x * component.h + bx) * 8,
                              (mcu_y * component.v + by) * 8)
            decoded += 1

    return _to_rgb(components, width, height, max_h, max_v, adobe_transform)


def _decode_block(reader, component, dc_table, ac_table, quant, block, output):
    for i in range(64):
        block[i] = 0
    magnitude = _decode_huffman(reader, dc_table)
    difference = _extend(reader.receive(magnitude), magnitude) if magnitude else 0
    component.prediction += difference
    block[0] = component.prediction * quant[0]

    index = 1
    while index < 64:
        symbol = _decode_huffman(reader, ac_table)
        run, size = symbol >> 4, symbol & 15
        if size == 0:
            if run == 15:
                index += 16
                continue
            break
        index += run
        if index > 63:
            break
        value = _extend(reader.receive(size), size)
        position = ZIGZAG[index]
        block[position] = value * quant[position]
        index += 1
    _idct(block, output)


def _blit(component, output, x0, y0):
    plane = component.plane
    stride = component.plane_width
    for y in range(8):
        target = (y0 + y) * stride + x0
        if target + 8 <= len(plane):
            plane[target:target + 8] = bytes(output[y * 8:y * 8 + 8])


def _to_rgb(components, width, height, max_h, max_v, adobe_transform):
    bitmap = Bitmap(width, height)
    pixels = bitmap.pixels

    if len(components) == 1:
        component = components[0]
        plane, stride = component.plane, component.plane_width
        for y in range(height):
            row = y * stride
            base = y * width * 4
            for x in range(width):
                value = plane[row + x]
                out = base + x * 4
                pixels[out] = pixels[out + 1] = pixels[out + 2] = value
                pixels[out + 3] = 255
        return bitmap

    if len(components) not in (3, 4):
        raise ImageError("unsupported JPEG component count %d" % len(components))
    if len(components) == 4:
        raise ImageError("CMYK JPEG is not supported")

    luma, blue, red = components
    ly_ratio = max_v // luma.v
    lx_ratio = max_h // luma.h
    by_ratio = max_v // blue.v
    bx_ratio = max_h // blue.h
    ry_ratio = max_v // red.v
    rx_ratio = max_h // red.h

    for y in range(height):
        base = y * width * 4
        luma_row = (y // ly_ratio) * luma.plane_width
        blue_row = (y // by_ratio) * blue.plane_width
        red_row = (y // ry_ratio) * red.plane_width
        for x in range(width):
            Y = luma.plane[luma_row + x // lx_ratio]
            Cb = blue.plane[blue_row + x // bx_ratio] - 128
            Cr = red.plane[red_row + x // rx_ratio] - 128
            out = base + x * 4
            r = Y + 1.402 * Cr
            g = Y - 0.344136 * Cb - 0.714136 * Cr
            b = Y + 1.772 * Cb
            pixels[out] = 0 if r < 0 else (255 if r > 255 else int(r))
            pixels[out + 1] = 0 if g < 0 else (255 if g > 255 else int(g))
            pixels[out + 2] = 0 if b < 0 else (255 if b > 255 else int(b))
            pixels[out + 3] = 255
    del adobe_transform
    return bitmap
