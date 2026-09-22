"""GIF decoding, including the LZW variable-width code stream."""

from __future__ import annotations

from . import Bitmap, ImageError


def decode_gif(data):
    if data[:6] not in (b"GIF87a", b"GIF89a"):
        raise ImageError("not a GIF")
    width = data[6] | data[7] << 8
    height = data[8] | data[9] << 8
    flags = data[10]
    position = 13
    global_palette = None
    if flags & 0x80:
        size = 2 << (flags & 0x07)
        global_palette = data[position:position + size * 3]
        position += size * 3

    transparent = None
    while position < len(data):
        block = data[position]
        if block == 0x21:                       # extension
            label = data[position + 1]
            position += 2
            if label == 0xF9:                   # graphic control
                length = data[position]
                packed = data[position + 1]
                if packed & 0x01:
                    transparent = data[position + 4]
                position += length + 1
                while position < len(data) and data[position]:
                    position += data[position] + 1
                position += 1
            else:
                while position < len(data) and data[position]:
                    position += data[position] + 1
                position += 1
            continue
        if block == 0x2C:                       # image descriptor
            left = data[position + 1] | data[position + 2] << 8
            top = data[position + 3] | data[position + 4] << 8
            frame_width = data[position + 5] | data[position + 6] << 8
            frame_height = data[position + 7] | data[position + 8] << 8
            local_flags = data[position + 9]
            position += 10
            palette = global_palette
            if local_flags & 0x80:
                size = 2 << (local_flags & 0x07)
                palette = data[position:position + size * 3]
                position += size * 3
            interlaced = bool(local_flags & 0x40)
            minimum_code_size = data[position]
            position += 1
            stream, position = _read_sub_blocks(data, position)
            indices = _lzw_decode(stream, minimum_code_size,
                                  frame_width * frame_height)
            return _compose(width or frame_width, height or frame_height,
                            left, top, frame_width, frame_height, indices,
                            palette, transparent, interlaced)
        if block == 0x3B:
            break
        position += 1
    raise ImageError("GIF has no image data")


def _read_sub_blocks(data, position):
    out = bytearray()
    while position < len(data):
        size = data[position]
        position += 1
        if size == 0:
            break
        out.extend(data[position:position + size])
        position += size
    return bytes(out), position


def _lzw_decode(stream, minimum_code_size, expected):
    clear_code = 1 << minimum_code_size
    end_code = clear_code + 1
    code_size = minimum_code_size + 1
    dictionary = {i: bytes([i]) for i in range(clear_code)}
    next_code = end_code + 1
    out = bytearray()
    previous = None

    bit_position = 0
    total_bits = len(stream) * 8
    while bit_position + code_size <= total_bits and len(out) < expected:
        byte_index = bit_position >> 3
        chunk = stream[byte_index] | (stream[byte_index + 1] << 8
                                      if byte_index + 1 < len(stream) else 0) \
            | (stream[byte_index + 2] << 16 if byte_index + 2 < len(stream)
               else 0)
        code = (chunk >> (bit_position & 7)) & ((1 << code_size) - 1)
        bit_position += code_size

        if code == clear_code:
            code_size = minimum_code_size + 1
            dictionary = {i: bytes([i]) for i in range(clear_code)}
            next_code = end_code + 1
            previous = None
            continue
        if code == end_code:
            break
        if code in dictionary:
            entry = dictionary[code]
        elif previous is not None:
            entry = previous + previous[:1]
        else:
            break
        out.extend(entry)
        if previous is not None and next_code < 4096:
            dictionary[next_code] = previous + entry[:1]
            next_code += 1
            if next_code == (1 << code_size) and code_size < 12:
                code_size += 1
        previous = entry
    return out


_INTERLACE_PASSES = ((0, 8), (4, 8), (2, 4), (1, 2))


def _compose(width, height, left, top, frame_width, frame_height, indices,
             palette, transparent, interlaced):
    bitmap = Bitmap(width, height)
    pixels = bitmap.pixels
    if palette is None:
        raise ImageError("GIF has no colour table")

    rows = []
    if interlaced:
        order = []
        for start, step in _INTERLACE_PASSES:
            order.extend(range(start, frame_height, step))
        rows = order
    else:
        rows = list(range(frame_height))

    for source_row, target_row in enumerate(rows):
        for x in range(frame_width):
            index = source_row * frame_width + x
            if index >= len(indices):
                break
            entry = indices[index]
            target_x, target_y = left + x, top + target_row
            if not (0 <= target_x < width and 0 <= target_y < height):
                continue
            out = (target_y * width + target_x) * 4
            if transparent is not None and entry == transparent:
                pixels[out + 3] = 0
                continue
            base = entry * 3
            if base + 2 >= len(palette):
                continue
            pixels[out] = palette[base]
            pixels[out + 1] = palette[base + 1]
            pixels[out + 2] = palette[base + 2]
            pixels[out + 3] = 255
    return bitmap
