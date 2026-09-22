import unittest

from mote.url import URL, remove_dot_segments


class ReferenceResolution(unittest.TestCase):
    """The normative examples from RFC 3986 section 5.4."""

    base = "http://a/b/c/d;p?q"

    def resolve(self, reference):
        return str(URL.parse(self.base).join(reference))

    def test_normal_examples(self):
        cases = {
            "g:h": "g:h", "g": "http://a/b/c/g", "./g": "http://a/b/c/g",
            "g/": "http://a/b/c/g/", "/g": "http://a/g", "//g": "http://g",
            "?y": "http://a/b/c/d;p?y", "g?y": "http://a/b/c/g?y",
            "#s": "http://a/b/c/d;p?q#s", "g#s": "http://a/b/c/g#s",
            "g?y#s": "http://a/b/c/g?y#s", ";x": "http://a/b/c/;x",
            "g;x": "http://a/b/c/g;x", "": "http://a/b/c/d;p?q",
            ".": "http://a/b/c/", "./": "http://a/b/c/",
            "..": "http://a/b/", "../": "http://a/b/",
            "../g": "http://a/b/g", "../..": "http://a/",
            "../../g": "http://a/g",
        }
        for reference, expected in cases.items():
            with self.subTest(reference=reference):
                self.assertEqual(self.resolve(reference), expected)

    def test_abnormal_examples(self):
        self.assertEqual(self.resolve("../../../g"), "http://a/g")
        self.assertEqual(self.resolve("/./g"), "http://a/g")
        self.assertEqual(self.resolve("g."), "http://a/b/c/g.")
        self.assertEqual(self.resolve(".g"), "http://a/b/c/.g")


class Parsing(unittest.TestCase):
    def test_components(self):
        url = URL.parse("https://user@Example.COM:8443/a/b?x=1&y=2#frag")
        self.assertEqual(url.scheme, "https")
        self.assertEqual(url.host, "example.com")
        self.assertEqual(url.port, 8443)
        self.assertEqual(url.userinfo, "user")
        self.assertEqual(url.path, "/a/b")
        self.assertEqual(url.query, "x=1&y=2")
        self.assertEqual(url.fragment, "frag")
        self.assertEqual(url.origin, ("https", "example.com", 8443))

    def test_default_port_is_not_serialised(self):
        self.assertEqual(str(URL.parse("http://example.com:80/a")),
                         "http://example.com/a")
        self.assertEqual(URL.parse("https://example.com/").effective_port, 443)

    def test_request_target_escapes_and_keeps_query(self):
        url = URL.parse("http://h/a b/c?q=x y")
        self.assertEqual(url.request_target, "/a%20b/c?q=x%20y")
        self.assertEqual(URL.parse("http://h").request_target, "/")

    def test_ipv6_literal(self):
        url = URL.parse("http://[2001:db8::1]:8080/x")
        self.assertEqual(url.host, "[2001:db8::1]")
        self.assertEqual(url.port, 8080)

    def test_opaque_schemes_keep_their_payload(self):
        url = URL.parse("data:text/html;base64,PGI+aGk8L2I+")
        self.assertEqual(url.scheme, "data")
        self.assertEqual(url.opaque, "text/html;base64,PGI+aGk8L2I+")

    def test_fragment_is_stripped_from_opaque_urls_too(self):
        self.assertEqual(URL.parse("about:mote#x").opaque, "mote")

    def test_user_input(self):
        self.assertEqual(str(URL.from_user_input("example.com/x")),
                         "http://example.com/x")
        self.assertEqual(str(URL.from_user_input("localhost:8000")),
                         "http://localhost:8000")
        self.assertEqual(
            str(URL.from_user_input("two words", "https://s/?q=%s")),
            "https://s/?q=two%20words")

    def test_tabs_and_newlines_are_removed(self):
        self.assertEqual(str(URL.parse("http://ex\tamp\nle.com/a")),
                         "http://example.com/a")

    def test_dot_segments(self):
        self.assertEqual(remove_dot_segments("/a/b/../c/./d"), "/a/c/d")
        self.assertEqual(remove_dot_segments("/../a"), "/a")
        self.assertEqual(remove_dot_segments("/a/b/"), "/a/b/")


if __name__ == "__main__":
    unittest.main()
