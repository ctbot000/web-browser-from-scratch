import unittest

from mote.cssparse import MediaContext, evaluate_media_query, parse_stylesheet
from mote.csstoken import serialize, tokenize
from mote.htmlparse import parse_html
from mote.selectors import parse_selector_list
from mote.style import StyleEngine, collect_stylesheets
from mote.values import Lengths, parse_color, parse_length


def styled(source, viewport=800):
    document = parse_html(source)
    engine = StyleEngine(collect_stylesheets(document),
                         media=MediaContext(width=viewport))
    return document, engine.compute(document, viewport, 600)


def style_of(source, tag, index=0, viewport=800):
    document, styles = styled(source, viewport)
    matches = [e for e in document.elements() if e.tag == tag]
    return styles[id(matches[index])]


class Tokenizer(unittest.TestCase):
    def test_kinds(self):
        kinds = [t.kind for t in tokenize("a.b#c { x: 1.5em }") if
                 t.kind not in ("whitespace", "eof")]
        self.assertEqual(kinds, ["ident", "delim", "ident", "hash", "{",
                                 "ident", "colon", "dimension", "}"])

    def test_comments_and_strings(self):
        tokens = [t for t in tokenize('/* c */ "a}b" url( x.png )')
                  if t.kind not in ("whitespace", "eof")]
        self.assertEqual([t.kind for t in tokens], ["string", "url"])
        self.assertEqual(tokens[1].value, "x.png")

    def test_round_trip(self):
        self.assertEqual(serialize(tokenize("margin:0 auto 1.5em -3px")),
                         "margin:0 auto 1.5em -3px")


class Selectors(unittest.TestCase):
    def setUp(self):
        self.document = parse_html(
            '<ul id=m class=list><li class="a b">x</li>'
            '<li><a href=/ lang=en-GB>y</a></li><li>z</li></ul>')
        self.elements = list(self.document.elements())

    def match(self, selector):
        parsed = parse_selector_list(selector)[0]
        return [e.tag + ("." + e.get("class") if e.get("class") else "")
                for e in self.elements if parsed.matches(e)]

    def test_combinators(self):
        self.assertEqual(self.match("ul#m > li.a"), ["li.a b"])
        self.assertEqual(self.match("li + li a[href]"), ["a"])
        self.assertEqual(len(self.match("ul li")), 3)
        self.assertEqual(len(self.match("li ~ li")), 2)

    def test_attribute_operators(self):
        self.assertEqual(self.match("[lang|=en]"), ["a"])
        self.assertEqual(self.match("[class~=b]"), ["li.a b"])
        self.assertEqual(self.match("[id^=m]"), ["ul.list"])
        self.assertEqual(self.match("[class$=st]"), ["ul.list"])
        self.assertEqual(self.match("[class*=is]"), ["ul.list"])

    def test_structural_pseudo_classes(self):
        self.assertEqual(self.match("li:first-child"), ["li.a b"])
        self.assertEqual(self.match("li:last-child"), ["li"])
        self.assertEqual(len(self.match("li:nth-child(odd)")), 2)
        self.assertEqual(len(self.match("li:not(.a)")), 2)

    def test_specificity(self):
        def spec(text):
            return parse_selector_list(text)[0].specificity
        self.assertEqual(spec("*"), (0, 0, 0))
        self.assertEqual(spec("li"), (0, 0, 1))
        self.assertEqual(spec(".a"), (0, 1, 0))
        self.assertEqual(spec("#a"), (1, 0, 0))
        self.assertEqual(spec("ul#m > li.a:hover"), (1, 2, 2))
        self.assertLess(spec("li.a"), spec("#a"))

    def test_invalid_selector_is_rejected(self):
        from mote.selectors import SelectorError
        with self.assertRaises(SelectorError):
            parse_selector_list("a >> b")


class Values(unittest.TestCase):
    def test_colours(self):
        self.assertEqual(parse_color("#abc"), (170, 187, 204, 1.0))
        self.assertEqual(parse_color("rebeccapurple"), (102, 51, 153, 1.0))
        self.assertEqual(parse_color("rgb(1, 2, 3)"), (1, 2, 3, 1.0))
        self.assertEqual(parse_color("rgba(0,0,0,.5)")[3], 0.5)
        self.assertEqual(parse_color("hsl(120, 100%, 50%)"), (0, 255, 0, 1.0))
        self.assertEqual(parse_color("transparent")[3], 0.0)
        self.assertIsNone(parse_color("not-a-colour"))

    def test_lengths(self):
        context = Lengths(font_size=20, root_font_size=16,
                          viewport_width=1000, percent_base=200)
        self.assertEqual(parse_length("12px", context), 12)
        self.assertEqual(parse_length("1.5em", context), 30)
        self.assertEqual(parse_length("2rem", context), 32)
        self.assertEqual(parse_length("50%", context), 100)
        self.assertEqual(parse_length("1in", context), 96)
        self.assertEqual(parse_length("10vw", context), 100)
        self.assertIsNone(parse_length("auto", context))

    def test_calc(self):
        context = Lengths(font_size=20, percent_base=200)
        self.assertEqual(parse_length("calc(100% - 2em)", context), 160)
        self.assertEqual(parse_length("calc(50px*2 + 10px)", context), 110)
        self.assertEqual(parse_length("calc((10px + 5px) / 3)", context), 5)


class MediaQueries(unittest.TestCase):
    def test_width_features(self):
        narrow, wide = MediaContext(width=400), MediaContext(width=1200)
        self.assertTrue(evaluate_media_query("(max-width: 500px)", narrow))
        self.assertFalse(evaluate_media_query("(max-width: 500px)", wide))
        self.assertTrue(evaluate_media_query("screen and (min-width: 1000px)",
                                             wide))

    def test_type_and_negation(self):
        screen = MediaContext(media_type="screen")
        self.assertFalse(evaluate_media_query("print", screen))
        self.assertTrue(evaluate_media_query("not print", screen))
        self.assertTrue(evaluate_media_query("print, screen", screen))

    def test_rules_are_filtered_by_media(self):
        sheet = parse_stylesheet(
            "@media (max-width: 500px) { .m { display: none } } a { color: red }")
        self.assertEqual(len(list(sheet.rules_for(MediaContext(width=400)))), 2)
        self.assertEqual(len(list(sheet.rules_for(MediaContext(width=900)))), 1)


class Cascade(unittest.TestCase):
    def test_specificity_wins(self):
        style = style_of("<style>p{color:red} .k{color:blue} p.k{color:green}"
                         "</style><p class=k>x", "p")
        self.assertEqual(style.color, (0, 128, 0, 1.0))

    def test_important_beats_specificity(self):
        style = style_of("<style>#i{color:orange} p{color:green!important}"
                         "</style><p id=i>x", "p")
        self.assertEqual(style.color, (0, 128, 0, 1.0))

    def test_inline_style_beats_stylesheet(self):
        style = style_of("<style>p{color:red}</style>"
                         "<p style='color:blue'>x", "p")
        self.assertEqual(style.color, (0, 0, 255, 1.0))

    def test_author_beats_user_agent(self):
        self.assertEqual(style_of("<style>p{display:inline}</style><p>x",
                                  "p").display, "inline")

    def test_inheritance_and_initial_values(self):
        document, styles = styled("<style>div{color:red;border:1px solid}"
                                  "</style><div><span>x</span></div>")
        span = [e for e in document.elements() if e.tag == "span"][0]
        self.assertEqual(styles[id(span)].color, (255, 0, 0, 1.0))
        self.assertEqual(styles[id(span)].keyword("border-top-style"), "none")

    def test_em_lengths_use_the_elements_own_font_size(self):
        style = style_of("<style>p{font-size:20px;margin:2em 0}</style><p>x",
                         "p")
        self.assertEqual(style.props["margin-top"], "40px")

    def test_font_shorthand(self):
        style = style_of(
            "<style>p{font:italic bold 20px/1.5 sans-serif}</style><p>x", "p")
        self.assertEqual(style.font, ("sans-serif", 20.0, True, True))
        self.assertEqual(style.line_height(Lengths(20.0)), 30.0)

    def test_margin_shorthand_expansion(self):
        style = style_of("<style>p{margin:1px 2px 3px}</style><p>x", "p")
        self.assertEqual([style.props["margin-" + s] for s in
                          ("top", "right", "bottom", "left")],
                         ["1px", "2px", "3px", "2px"])

    def test_presentational_attributes_are_lowest_priority(self):
        self.assertEqual(style_of("<body bgcolor=red>x", "body")
                         .color_of("background-color"), (255, 0, 0, 1.0))
        self.assertEqual(
            style_of("<style>body{background:blue}</style><body bgcolor=red>x",
                     "body").color_of("background-color"), (0, 0, 255, 1.0))

    def test_media_query_applies_at_the_right_width(self):
        source = ("<style>@media (max-width:500px){p{display:none}}</style>"
                  "<p>x")
        self.assertEqual(style_of(source, "p", viewport=400).display, "none")
        self.assertEqual(style_of(source, "p", viewport=900).display, "block")

    def test_malformed_declarations_do_not_lose_the_rule(self):
        style = style_of("<style>p{ ; color: ; font-size:12px }</style><p>x",
                         "p")
        self.assertEqual(style.font_size, 12.0)


if __name__ == "__main__":
    unittest.main()
