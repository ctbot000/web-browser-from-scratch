import unittest

from mote.browser import Engine
from mote.fonts import BuiltinMetrics
from mote.htmlparse import parse_html
from mote.layout import (BlockBox, LayoutContext, ListItemBox, TableBox,
                         layout_document)
from mote.style import StyleEngine, collect_stylesheets


def lay_out(source, width=800.0, height=600.0):
    document = parse_html(source)
    engine = StyleEngine(collect_stylesheets(document))
    styles = engine.compute(document, width, height)
    context = LayoutContext(BuiltinMetrics(), width, height)
    return document, layout_document(document, styles, context)


def find(box, tag, index=0):
    matches = [b for b in [box] + list(box.descendants())
               if b.element is not None and b.element.tag == tag]
    return matches[index]


def line_texts(box):
    return [[f.text for f in line.fragments if f.kind in ("text", "marker")]
            for line in box.lines]


class BlockLayout(unittest.TestCase):
    def test_block_fills_its_containing_block(self):
        _, root = lay_out("<div>x</div>", width=500)
        body = find(root, "body")
        self.assertEqual(body.width, 500 - 16)          # 8px body margin
        self.assertEqual(find(root, "div").width, body.width)

    def test_padding_and_border_shrink_the_content_box(self):
        _, root = lay_out("<div style='padding:10px;border:5px solid'>x</div>",
                          width=400)
        div = find(root, "div")
        self.assertEqual(div.width, 400 - 16 - 20 - 10)
        self.assertEqual(div.padding.left, 10)
        self.assertEqual(div.border.left, 5)

    def test_auto_margins_centre_a_fixed_width_box(self):
        _, root = lay_out("<div style='width:200px;margin:0 auto'>x</div>",
                          width=600)
        div = find(root, "div")
        self.assertAlmostEqual(div.margin.left, (600 - 16 - 200) / 2)
        self.assertAlmostEqual(div.x, 8 + (600 - 16 - 200) / 2)

    def test_adjacent_margins_collapse(self):
        _, root = lay_out("<div style='margin-bottom:30px'>a</div>"
                          "<div style='margin-top:10px'>b</div>")
        first, second = find(root, "div", 0), find(root, "div", 1)
        # The two margins collapse to the larger, not to their sum.
        self.assertAlmostEqual(second.y - (first.y + first.height), 30.0)

    def test_box_sizing_border_box(self):
        _, root = lay_out("<div style='width:200px;padding:20px;"
                          "box-sizing:border-box'>x</div>")
        self.assertEqual(find(root, "div").width, 160)

    def test_explicit_and_minimum_heights(self):
        _, root = lay_out("<div style='height:120px'>x</div>")
        self.assertEqual(find(root, "div").height, 120)
        _, root = lay_out("<div style='min-height:200px'>x</div>")
        self.assertEqual(find(root, "div").height, 200)


class InlineLayout(unittest.TestCase):
    def test_text_wraps_at_the_available_width(self):
        _, root = lay_out("<p>" + " ".join(["word"] * 40) + "</p>", width=300)
        self.assertGreater(len(find(root, "p").lines), 1)

    def test_nowrap_does_not_break(self):
        _, root = lay_out("<p style='white-space:nowrap'>" +
                          " ".join(["word"] * 40) + "</p>", width=300)
        self.assertEqual(len(find(root, "p").lines), 1)

    def test_br_forces_a_new_line(self):
        _, root = lay_out("<p>a<br>b</p>", width=800)
        self.assertEqual(len(find(root, "p").lines), 2)

    def test_pre_preserves_newlines_and_spaces(self):
        _, root = lay_out("<pre>one\n  two</pre>")
        lines = line_texts(find(root, "pre"))
        self.assertEqual(len(lines), 2)
        self.assertIn("  two", lines[1][0])

    def test_words_keep_their_spaces_across_inline_elements(self):
        _, root = lay_out("<p>It can be <b>bold</b>, <b><i>both at once</i></b>"
                          ", end.</p>", width=800)
        text = "".join(f.text for line in find(root, "p").lines
                       for f in line.fragments)
        self.assertEqual(text, "It can be bold, both at once, end.")

    def test_text_align_centre_and_right(self):
        _, root = lay_out("<p style='text-align:center'>hi</p>", width=400)
        centred = find(root, "p").lines[0].fragments[0].x
        _, root = lay_out("<p style='text-align:right'>hi</p>", width=400)
        right = find(root, "p").lines[0].fragments[0].x
        self.assertLess(8, centred)
        self.assertLess(centred, right)

    def test_line_height_controls_line_box_height(self):
        _, root = lay_out("<p style='line-height:3;font-size:10px'>a<br>b</p>")
        self.assertAlmostEqual(find(root, "p").lines[0].height, 30.0, places=1)

    def test_inline_block_flows_like_a_word(self):
        card = ("<span style='display:inline-block;width:100px'>x</span>")
        _, root = lay_out("<div>%s</div>" % (card * 3), width=400)
        self.assertEqual(len(find(root, "div").lines), 1)
        _, root = lay_out("<div>%s</div>" % (card * 3), width=250)
        self.assertEqual(len(find(root, "div").lines), 2)


class Floats(unittest.TestCase):
    def test_float_shortens_the_lines_beside_it(self):
        source = ("<div style='float:left;width:100px;height:50px'></div>"
                  "<p>%s</p>" % " ".join(["word"] * 30))
        _, root = lay_out(source, width=400)
        paragraph = find(root, "p")
        self.assertGreater(paragraph.lines[0].x, 100)

    def test_clear_moves_below_the_float(self):
        source = ("<div style='float:left;width:80px;height:120px'></div>"
                  "<p style='clear:left'>after</p>")
        _, root = lay_out(source, width=400)
        self.assertGreaterEqual(find(root, "p").y, 120)

    def test_right_float_sits_at_the_right_edge(self):
        source = "<div style='float:right;width:100px;height:20px'></div>"
        _, root = lay_out(source, width=400)
        floated = find(root, "div")
        self.assertAlmostEqual(floated.margin_box[0] + floated.margin_box[2],
                               400 - 8)


class Tables(unittest.TestCase):
    def test_columns_are_sized_from_content(self):
        _, root = lay_out("<table><tr><td>a</td><td>a much longer cell</td>"
                          "</tr></table>", width=600)
        table = find(root, "table")
        self.assertIsInstance(table, TableBox)
        self.assertEqual(len(table.column_widths), 2)
        self.assertLess(table.column_widths[0], table.column_widths[1])

    def test_rows_stack_and_cells_align(self):
        _, root = lay_out("<table><tr><td>a</td><td>b</td></tr>"
                          "<tr><td>c</td><td>d</td></tr></table>")
        cells = [find(root, "td", i) for i in range(4)]
        self.assertAlmostEqual(cells[0].y, cells[1].y)
        self.assertGreater(cells[2].y, cells[0].y)
        self.assertGreater(cells[1].x, cells[0].x)

    def test_colspan_widens_a_cell(self):
        _, root = lay_out("<table><tr><td>a</td><td>b</td></tr>"
                          "<tr><td colspan=2>wide</td></tr></table>")
        narrow, wide = find(root, "td", 0), find(root, "td", 2)
        self.assertGreater(wide.width, narrow.width)


class Lists(unittest.TestCase):
    def test_markers_and_numbering(self):
        _, root = lay_out("<ul><li>a</li><li>b</li></ul>")
        items = [b for b in root.descendants() if isinstance(b, ListItemBox)]
        self.assertEqual([i.marker_text for i in items], ["•", "•"])

    def test_ordered_list_counts(self):
        _, root = lay_out("<ol><li>a</li><li>b</li><li>c</li></ol>")
        items = [b for b in root.descendants() if isinstance(b, ListItemBox)]
        self.assertEqual([i.marker_text for i in items], ["1.", "2.", "3."])

    def test_start_attribute_and_roman_numerals(self):
        _, root = lay_out("<ol start=4 style='list-style-type:lower-roman'>"
                          "<li>a</li><li>b</li></ol>")
        items = [b for b in root.descendants() if isinstance(b, ListItemBox)]
        self.assertEqual([i.marker_text for i in items], ["iv.", "v."])


class Positioning(unittest.TestCase):
    def test_relative_offsets_the_painted_box(self):
        _, root = lay_out("<div style='position:relative;top:10px;left:20px'>"
                          "x</div>")
        div = find(root, "div")
        self.assertEqual(div.relative_offset, (20.0, 10.0))
        self.assertEqual(div.content_box[0], div.x + 20)

    def test_absolute_is_placed_against_its_positioned_ancestor(self):
        _, root = lay_out(
            "<div style='position:relative;height:200px;margin:40px'>"
            "<span style='position:absolute;top:10px;left:15px'>x</span>"
            "</div>", width=500)
        parent, child = find(root, "div"), find(root, "span")
        self.assertAlmostEqual(child.margin_box[0], parent.padding_box[0] + 15)
        self.assertAlmostEqual(child.margin_box[1], parent.padding_box[1] + 10)

    def test_display_none_produces_no_box(self):
        _, root = lay_out("<p style='display:none'>hidden</p><p>shown</p>")
        paragraphs = [b for b in root.descendants()
                      if b.element is not None and b.element.tag == "p"]
        self.assertEqual(len(paragraphs), 1)


class EndToEnd(unittest.TestCase):
    def test_a_data_url_renders_to_a_display_list(self):
        engine = Engine(metrics=BuiltinMetrics(), width=600, height=400)
        try:
            page = engine.load("data:text/html,<h1>Title</h1><p>Body text.</p>")
            self.assertTrue(page.display_list)
            texts = [c.text for c in page.display_list
                     if hasattr(c, "text")]
            self.assertIn("Title", texts)
            self.assertIn("Body", texts)
        finally:
            engine.close()

    def test_link_hit_testing(self):
        engine = Engine(metrics=BuiltinMetrics(), width=600, height=400)
        try:
            page = engine.load(
                "data:text/html,<p><a href='/next'>click here</a></p>")
            command = [c for c in page.display_list
                       if getattr(c, "text", "") == "click"][0]
            x, y, w, h = command.rect
            anchor = page.link_at(x + w / 2, y + h / 2)
            self.assertIsNotNone(anchor)
            self.assertEqual(anchor.get("href"), "/next")
            self.assertIsNone(page.link_at(x + w / 2, y + 400))
        finally:
            engine.close()


if __name__ == "__main__":
    unittest.main()
