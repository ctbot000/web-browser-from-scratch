import unittest

from mote.dom import Comment, Element, Text
from mote.entities import decode_reference
from mote.htmlparse import Tokenizer, parse_html


def tags(node):
    return [e.tag for e in node.elements()]


def tree(source):
    from mote.dom import dump
    return "\n".join(dump(parse_html(source)))


class Tokenizing(unittest.TestCase):
    def kinds(self, source):
        return [(t.kind, t.name or t.data) for t in Tokenizer(source).tokens()]

    def test_attributes_in_every_quoting_style(self):
        token = next(iter(Tokenizer("<a href=/x class='a b' id=\"c\" hidden>")
                          .tokens()))
        self.assertEqual(token.attrs, {"href": "/x", "class": "a b",
                                       "id": "c", "hidden": ""})

    def test_raw_text_elements_swallow_markup(self):
        kinds = self.kinds("<script>if (a<b) { x = '</div>' }</script>")
        self.assertEqual(kinds[1][0], "text")
        self.assertIn("</div>", kinds[1][1])

    def test_comment_and_doctype(self):
        kinds = self.kinds("<!doctype html><!-- hi --><p>")
        self.assertEqual(kinds[0][0], "doctype")
        self.assertEqual(kinds[1], ("comment", " hi "))

    def test_self_closing_tag(self):
        token = next(iter(Tokenizer("<br/>").tokens()))
        self.assertTrue(token.self_closing)

    def test_stray_less_than_is_text(self):
        self.assertEqual(self.kinds("a < b")[0][0], "text")


class Entities(unittest.TestCase):
    def test_named_numeric_and_hex(self):
        self.assertEqual(decode_reference("&amp;", 0), ("&", 5))
        self.assertEqual(decode_reference("&#65;", 0), ("A", 5))
        self.assertEqual(decode_reference("&#x41;", 0), ("A", 6))

    def test_longest_match_wins(self):
        # "&notit;" is "not" followed by "it;", not an unknown reference.
        self.assertEqual(decode_reference("&notit;", 0), ("¬", 4))

    def test_windows_1252_replacement(self):
        self.assertEqual(decode_reference("&#150;", 0)[0], "–")

    def test_unknown_reference_is_left_alone(self):
        self.assertEqual(decode_reference("&nope;", 0), (None, 0))

    def test_two_codepoint_reference(self):
        self.assertEqual(len(decode_reference("&NotEqualTilde;", 0)[0]), 2)

    def test_parser_decodes_in_text_and_attributes(self):
        document = parse_html('<p title="a&amp;b">x&nbsp;y &lt;tag&gt;</p>')
        paragraph = next(document.document_element.find_all("p"))
        self.assertEqual(paragraph.get("title"), "a&b")
        self.assertEqual(paragraph.text_content, "x\xa0y <tag>")


class TreeBuilding(unittest.TestCase):
    def test_html_head_body_are_created(self):
        document = parse_html("hello")
        self.assertEqual(tags(document)[:3], ["html", "head", "body"])
        self.assertEqual(document.body().text_content, "hello")

    def test_title_is_recorded(self):
        self.assertEqual(parse_html("<title>Hi &amp; bye</title>").title,
                         "Hi & bye")

    def test_paragraphs_close_each_other(self):
        body = parse_html("<p>one<p>two").body()
        self.assertEqual([c.tag for c in body.children], ["p", "p"])
        self.assertEqual(body.children[0].text_content, "one")

    def test_list_items_close_each_other(self):
        items = list(parse_html("<ul><li>a<li>b</ul>").document_element
                     .find_all("li"))
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0].parent.tag, "ul")

    def test_inline_end_tag_does_not_close_its_block(self):
        # A </b> must not take the enclosing <p> with it.
        body = parse_html("<p>hi <b>there</b></p><h1>H</h1>").body()
        self.assertEqual([c.tag for c in body.children], ["p", "h1"])

    def test_implicit_tbody_and_rows(self):
        document = parse_html("<table><tr><td>a<td>b</table>")
        table = next(document.document_element.find_all("table"))
        self.assertEqual(table.children[0].tag, "tbody")
        row = table.children[0].children[0]
        self.assertEqual([c.tag for c in row.children], ["td", "td"])

    def test_text_in_a_table_is_foster_parented(self):
        document = parse_html("<table>stray<tr><td>a</table>")
        table = next(document.document_element.find_all("table"))
        self.assertNotIn("stray", table.text_content)
        self.assertIn("stray", document.body().text_content)

    def test_head_elements_go_in_the_head(self):
        document = parse_html("<meta charset=utf-8><link rel=x><p>body")
        head_tags = [c.tag for c in document.head().children]
        self.assertEqual(head_tags, ["meta", "link"])

    def test_void_elements_do_not_nest(self):
        body = parse_html("<div><img src=a><br>text</div>").body()
        div = body.children[0]
        self.assertEqual([type(c).__name__ for c in div.children],
                         ["Element", "Element", "Text"])

    def test_unclosed_tags_are_closed_at_eof(self):
        document = parse_html("<div><span><b>text")
        self.assertIn("text", document.body().text_content)

    def test_comments_are_kept(self):
        document = parse_html("<p><!-- note -->x")
        self.assertTrue(any(isinstance(n, Comment)
                            for n in document.descendants()))

    def test_stray_end_tag_is_recorded_not_fatal(self):
        document = parse_html("<p>a</span>b")
        self.assertIn("ab", document.body().text_content.replace("\n", ""))
        self.assertTrue(document.errors)

    def test_headings_close_headings(self):
        body = parse_html("<h1>a<h2>b").body()
        self.assertEqual([c.tag for c in body.children], ["h1", "h2"])

    def test_definition_lists(self):
        body = parse_html("<dl><dt>a<dd>b<dt>c<dd>d</dl>").body()
        dl = body.children[0]
        self.assertEqual([c.tag for c in dl.children],
                         ["dt", "dd", "dt", "dd"])


if __name__ == "__main__":
    unittest.main()
