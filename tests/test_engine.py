import os
import unittest

from mote.backends.svgout import render_svg
from mote.backends.textout import GridMetrics, render_text
from mote.browser import Engine, History
from mote.encoding import decode, normalize, sniff_meta_charset
from mote.fonts import BuiltinMetrics, Font, generic_family
from mote.resource import Loader


class Encoding(unittest.TestCase):
    def test_bom_wins(self):
        text, name = decode("﻿hi".encode("utf-8"), declared="windows-1252")
        self.assertEqual(text, "hi")
        self.assertEqual(name, "utf-8-sig")

    def test_meta_charset_is_sniffed(self):
        html = b'<html><head><meta charset="euc-kr"><title>x'
        self.assertEqual(sniff_meta_charset(html), "euc_kr")

    def test_http_equiv_form(self):
        html = (b'<meta http-equiv="Content-Type" '
                b'content="text/html; charset=shift_jis">')
        self.assertEqual(sniff_meta_charset(html), "shift_jis")

    def test_declared_charset_is_preferred_over_the_default(self):
        text, name = decode("café".encode("windows-1252"),
                            declared="iso-8859-1")
        self.assertEqual(text, "café")
        self.assertEqual(name, "windows-1252")

    def test_undecodable_bytes_never_raise(self):
        text, _ = decode(b"\xff\xfe\x00broken", declared="utf-8")
        self.assertIsInstance(text, str)

    def test_alias_normalisation(self):
        self.assertEqual(normalize("UTF8"), "utf-8")
        self.assertEqual(normalize("ISO-8859-1"), "windows-1252")
        self.assertIsNone(normalize("no-such-encoding"))


class Schemes(unittest.TestCase):
    def setUp(self):
        self.loader = Loader()

    def tearDown(self):
        self.loader.close()

    def test_data_url_plain_and_base64(self):
        plain = self.loader.fetch("data:text/html,<b>hi</b>")
        self.assertEqual(plain.data, b"<b>hi</b>")
        self.assertEqual(plain.mime, "text/html")
        encoded = self.loader.fetch("data:text/plain;base64,aGVsbG8=")
        self.assertEqual(encoded.data, b"hello")

    def test_file_scheme_reads_this_file(self):
        path = os.path.abspath(__file__)
        resource = self.loader.fetch("file://" + path)
        self.assertTrue(resource.ok)
        self.assertIn(b"test_file_scheme_reads_this_file", resource.data)

    def test_missing_file_is_an_error_not_a_crash(self):
        resource = self.loader.fetch("file:///no/such/path/at/all")
        self.assertFalse(resource.ok)
        self.assertIsNotNone(resource.error)

    def test_about_pages(self):
        self.assertTrue(self.loader.fetch("about:blank").ok)
        self.assertIn(b"Mote", self.loader.fetch("about:mote").data)
        self.assertEqual(self.loader.fetch("about:nope").status, 404)

    def test_unsupported_scheme_is_reported(self):
        resource = self.loader.fetch("gopher://example.com/")
        self.assertIn("scheme", resource.error)


class Fonts(unittest.TestCase):
    def test_family_lists_reduce_to_a_generic(self):
        self.assertEqual(generic_family("Helvetica Neue, Arial, sans-serif"),
                         "sans-serif")
        self.assertEqual(generic_family("'Times New Roman', serif"), "serif")
        self.assertEqual(generic_family("Menlo, monospace"), "monospace")
        self.assertEqual(generic_family("Unknown Face"), "serif")

    def test_measurement_scales_with_size_and_weight(self):
        metrics = BuiltinMetrics()
        small = metrics.measure("hamburgefonstiv", Font("serif", 12))
        large = metrics.measure("hamburgefonstiv", Font("serif", 24))
        self.assertAlmostEqual(large, small * 2, places=5)
        bold = metrics.measure("hamburgefonstiv", Font("serif", 12, bold=True))
        self.assertGreater(bold, small)

    def test_empty_string_measures_zero(self):
        self.assertEqual(BuiltinMetrics().measure("", Font("serif", 16)), 0.0)


class EngineBehaviour(unittest.TestCase):
    def setUp(self):
        self.engine = Engine(metrics=BuiltinMetrics(), width=600, height=400)

    def tearDown(self):
        self.engine.close()

    def load(self, html):
        return self.engine.load("data:text/html," + html)

    def test_plain_text_is_shown_as_preformatted(self):
        page = self.engine.load("data:text/plain,line one")
        self.assertIn("line one", page.document.body().text_content)

    def test_unreachable_host_produces_an_error_page(self):
        page = self.engine.load("http://no-such-host.invalid./")
        self.assertIn("Cannot reach", page.document.body().text_content)

    def test_title_and_links_are_collected(self):
        page = self.load("<title>T</title><a href=/a>x</a><a href=/b>y</a>")
        self.assertEqual(page.document.title, "T")
        self.assertEqual(len(page.links), 2)

    def test_canvas_background_comes_from_the_body(self):
        page = self.load("<style>body{background:%23ff0000}</style><p>x")
        self.assertEqual(page.canvas_color, (255, 0, 0, 1.0))

    def test_relayout_at_a_new_width_changes_line_count(self):
        page = self.load("<p>" + "word%20" * 60 + "</p>")
        self.engine.render(page, 1200, 400)
        wide = sum(len(b.lines) for b in page.root_box.descendants())
        self.engine.render(page, 300, 400)
        narrow = sum(len(b.lines) for b in page.root_box.descendants())
        self.assertGreater(narrow, wide)

    def test_view_source_shows_the_markup(self):
        page = self.engine.load("view-source:data:text/html,<b>bold</b>")
        self.assertIn("<b>bold</b>", page.document.body().text_content)


class Backends(unittest.TestCase):
    def test_terminal_rendering_places_text_on_a_grid(self):
        engine = Engine(metrics=GridMetrics(), width=60 * 8, height=30 * 19.2)
        try:
            page = engine.load(
                "data:text/html,<h1>Heading</h1><p>Body text here.</p>")
            text = render_text(page, columns=60)
            self.assertIn("Heading", text)
            self.assertIn("Body text here.", text)
            self.assertLess(max(len(line) for line in text.splitlines()), 61)
        finally:
            engine.close()

    def test_svg_output_is_well_formed_and_contains_the_text(self):
        engine = Engine(metrics=BuiltinMetrics(), width=400, height=200)
        try:
            page = engine.load("data:text/html,<p>Hello &amp; goodbye</p>")
            svg = render_svg(page.display_list, 400, 200, title="t")
            self.assertTrue(svg.startswith("<?xml"))
            self.assertTrue(svg.rstrip().endswith("</svg>"))
            self.assertIn("Hello", svg)
            self.assertIn("&amp;", svg)      # escaped, not raw
            import xml.etree.ElementTree as ElementTree
            ElementTree.fromstring(svg)      # raises if malformed
        finally:
            engine.close()


class SessionHistory(unittest.TestCase):
    def test_back_forward_and_truncation(self):
        history = History()
        for url in ("a", "b", "c"):
            history.visit(url)
        self.assertEqual(history.back(), "b")
        self.assertEqual(history.back(), "a")
        self.assertFalse(history.can_go_back())
        self.assertEqual(history.forward(), "b")
        history.visit("d")
        self.assertFalse(history.can_go_forward())
        self.assertEqual(history.entries, ["a", "b", "d"])


if __name__ == "__main__":
    unittest.main()
