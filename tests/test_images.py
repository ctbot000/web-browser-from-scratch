import os
import struct
import unittest
import zlib

from mote.images import Bitmap, ImageError, decode_image, encode_png, sniff_format
from mote.images.gif import _lzw_decode
from mote.images.png import decode_png

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


def fixture(name):
    with open(os.path.join(FIXTURES, name), "rb") as handle:
        return handle.read()


def gradient(width=16, height=16):
    """The pattern the fixtures encode: red = x, green = y, blue = x xor y."""
    bitmap = Bitmap(width, height)
    for y in range(height):
        for x in range(width):
            offset = (y * width + x) * 4
            bitmap.pixels[offset] = x * 16
            bitmap.pixels[offset + 1] = y * 16
            bitmap.pixels[offset + 2] = (x ^ y) * 16
            bitmap.pixels[offset + 3] = 255
    return bitmap


def mean_error(a, b, channels=3):
    self_total = 0
    count = a.width * a.height
    for i in range(count):
        for channel in range(channels):
            self_total += abs(a.pixels[i * 4 + channel] -
                              b.pixels[i * 4 + channel])
    return self_total / float(count * channels)


def build_png(width, height, depth, color_type, raw_rows, palette=None,
              transparency=None, interlace=0):
    """Assemble a PNG by hand, to test colour types we cannot encode."""
    def chunk(tag, payload):
        return (struct.pack(">I", len(payload)) + tag + payload +
                struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF))

    header = struct.pack(">IIBBBBB", width, height, depth, color_type, 0, 0,
                         interlace)
    out = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header)
    if palette is not None:
        out += chunk(b"PLTE", palette)
    if transparency is not None:
        out += chunk(b"tRNS", transparency)
    raw = b"".join(b"\x00" + row for row in raw_rows)
    return out + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


class Sniffing(unittest.TestCase):
    def test_formats_are_recognised_by_signature(self):
        self.assertEqual(sniff_format(fixture("gradient.png")), "png")
        self.assertEqual(sniff_format(fixture("gradient.jpg")), "jpeg")
        self.assertEqual(sniff_format(fixture("gradient.gif")), "gif")
        self.assertIsNone(sniff_format(b"not an image"))

    def test_unknown_format_raises(self):
        with self.assertRaises(ImageError):
            decode_image(b"nonsense bytes")


class PNG(unittest.TestCase):
    def test_round_trip_through_our_own_encoder(self):
        original = gradient()
        decoded = decode_png(encode_png(original))
        self.assertEqual((decoded.width, decoded.height), (16, 16))
        self.assertEqual(bytes(decoded.pixels), bytes(original.pixels))

    def test_fixture_matches_the_pattern(self):
        decoded = decode_image(fixture("gradient.png"))
        self.assertEqual(bytes(decoded.pixels), bytes(gradient().pixels))

    def test_greyscale_and_alpha_colour_types(self):
        rows = [bytes([x * 16 for x in range(8)]) for _ in range(2)]
        grey = decode_png(build_png(8, 2, 8, 0, rows))
        self.assertEqual(grey.pixel(3, 0), (48, 48, 48, 255))

        rows = [bytes([10, 255, 20, 0]) for _ in range(2)]
        grey_alpha = decode_png(build_png(2, 2, 8, 4, rows))
        self.assertEqual(grey_alpha.pixel(0, 0), (10, 10, 10, 255))
        self.assertEqual(grey_alpha.pixel(1, 0)[3], 0)

    def test_palette_and_transparency(self):
        palette = bytes([255, 0, 0, 0, 255, 0, 0, 0, 255])
        rows = [bytes([0, 1, 2, 0]) for _ in range(2)]
        decoded = decode_png(build_png(4, 2, 8, 3, rows, palette=palette,
                                       transparency=bytes([255, 128, 0])))
        self.assertEqual(decoded.pixel(0, 0), (255, 0, 0, 255))
        self.assertEqual(decoded.pixel(1, 0), (0, 255, 0, 128))
        self.assertEqual(decoded.pixel(2, 0)[3], 0)

    def test_low_bit_depths_are_unpacked(self):
        # Four 2-bit samples packed into one byte: indices 0, 1, 2, 3.
        palette = bytes([0, 0, 0, 85, 85, 85, 170, 170, 170, 255, 255, 255])
        rows = [bytes([0b00011011])]
        decoded = decode_png(build_png(4, 1, 2, 3, rows, palette=palette))
        self.assertEqual([decoded.pixel(x, 0)[0] for x in range(4)],
                         [0, 85, 170, 255])

    def test_filters_are_reversed(self):
        # Sub-filtered row: each byte is the difference from the pixel left.
        def chunk(tag, payload):
            return (struct.pack(">I", len(payload)) + tag + payload +
                    struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF))
        header = struct.pack(">IIBBBBB", 3, 1, 8, 2, 0, 0, 0)
        raw = bytes([1, 10, 20, 30, 5, 5, 5, 5, 5, 5])   # filter 1 then deltas
        data = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) +
                chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))
        decoded = decode_png(data)
        self.assertEqual(decoded.pixel(0, 0)[:3], (10, 20, 30))
        self.assertEqual(decoded.pixel(1, 0)[:3], (15, 25, 35))
        self.assertEqual(decoded.pixel(2, 0)[:3], (20, 30, 40))

    def test_corrupt_stream_is_reported(self):
        data = bytearray(fixture("gradient.png"))
        data[40] ^= 0xFF
        with self.assertRaises(ImageError):
            decode_png(bytes(data))


class GIF(unittest.TestCase):
    def test_fixture_decodes_close_to_the_original(self):
        decoded = decode_image(fixture("gradient.gif"))
        self.assertEqual((decoded.width, decoded.height), (16, 16))
        # GIF is limited to 256 palette entries, so expect a small error.
        self.assertLess(mean_error(decoded, gradient()), 12.0)

    def test_lzw_stream_decodes(self):
        # Minimum code size 2: clear, 0, 1, end.
        stream = bytes([0x8C, 0x2D, 0x99, 0x87, 0x2A, 0x1C, 0xDC, 0x33,
                        0xA0, 0x02, 0x75, 0xEC, 0x95, 0xFA, 0xA8, 0xDE,
                        0x60, 0x8C, 0x04, 0x91, 0x4C, 0x01, 0x00])
        out = _lzw_decode(stream, 2, 100)
        self.assertTrue(out)
        self.assertTrue(all(value < 4 for value in out))


class JPEG(unittest.TestCase):
    def test_fixture_decodes_close_to_the_original(self):
        decoded = decode_image(fixture("gradient.jpg"))
        self.assertEqual((decoded.width, decoded.height), (16, 16))
        # Lossy, and chroma is subsampled, so allow a larger tolerance.
        self.assertLess(mean_error(decoded, gradient()), 26.0)

    def test_progressive_jpeg_is_reported_clearly(self):
        data = bytearray(fixture("gradient.jpg"))
        index = data.find(b"\xff\xc0")
        self.assertGreater(index, 0)
        data[index + 1] = 0xC2                         # pretend SOF2
        with self.assertRaises(ImageError) as caught:
            decode_image(bytes(data))
        self.assertIn("progressive", str(caught.exception))


class BitmapOperations(unittest.TestCase):
    def test_resizing(self):
        small = gradient().resized(8, 8)
        self.assertEqual((small.width, small.height), (8, 8))
        self.assertEqual(small.pixel(0, 0), (0, 0, 0, 255))

    def test_ppm_header_and_size(self):
        ppm = gradient(4, 2).to_ppm()
        self.assertTrue(ppm.startswith(b"P6\n4 2\n255\n"))
        self.assertEqual(len(ppm), len(b"P6\n4 2\n255\n") + 4 * 2 * 3)

    def test_alpha_is_composited_onto_the_background(self):
        bitmap = Bitmap(1, 1, bytearray([0, 0, 0, 128]))
        self.assertEqual(bitmap.composite((255, 255, 255)), bytes([127] * 3))

    def test_data_uri_is_a_png(self):
        self.assertTrue(gradient(2, 2).data_uri.startswith(
            "data:image/png;base64,iVBOR"))


if __name__ == "__main__":
    unittest.main()
