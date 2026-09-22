"""The HTTP client is exercised against a small server running in-process."""

import gzip
import socket
import threading
import unittest

from mote.http import (CookieJar, HTTPCache, HTTPClient, Headers,
                       decode_content, parse_content_type)
from mote.url import URL


class TestServer(threading.Thread):
    """A deliberately literal HTTP/1.1 server: it writes the bytes we want to
    make the client read."""

    daemon = True

    def __init__(self):
        super().__init__()
        self.socket = socket.socket()
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.socket.bind(("127.0.0.1", 0))
        self.socket.listen(8)
        self.port = self.socket.getsockname()[1]
        self.connections = 0
        self.requests = []
        self.running = True

    @property
    def base(self):
        return "http://127.0.0.1:%d" % self.port

    def run(self):
        while self.running:
            try:
                client, _ = self.socket.accept()
            except OSError:
                return
            self.connections += 1
            threading.Thread(target=self._serve, args=(client,),
                             daemon=True).start()

    def stop(self):
        self.running = False
        try:
            self.socket.close()
        except OSError:
            pass

    def _serve(self, client):
        client.settimeout(5)
        buffer = b""
        try:
            while True:
                while b"\r\n\r\n" not in buffer:
                    chunk = client.recv(4096)
                    if not chunk:
                        return
                    buffer += chunk
                head, buffer = buffer.split(b"\r\n\r\n", 1)
                lines = head.decode("latin-1").split("\r\n")
                method, target, _ = lines[0].split(" ")
                headers = {}
                for line in lines[1:]:
                    name, _, value = line.partition(":")
                    headers[name.strip().lower()] = value.strip()
                self.requests.append((method, target, headers))
                keep = self._respond(client, target, headers)
                if not keep:
                    return
        except (OSError, ValueError):
            return
        finally:
            try:
                client.close()
            except OSError:
                pass

    def _respond(self, client, target, headers):
        def send(status, extra, body, close=False):
            lines = ["HTTP/1.1 " + status]
            lines += extra
            lines.append("Connection: " + ("close" if close else "keep-alive"))
            client.sendall(("\r\n".join(lines) + "\r\n\r\n").encode("latin-1"))
            if body:
                client.sendall(body)
            return not close

        if target == "/plain":
            body = b"hello world"
            return send("200 OK", ["Content-Type: text/plain; charset=utf-8",
                                   "Content-Length: %d" % len(body)], body)
        if target == "/chunked":
            client.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\n"
                           b"Transfer-Encoding: chunked\r\n"
                           b"Connection: keep-alive\r\n\r\n")
            for piece in (b"<html>", b"chunked ", b"body</html>"):
                client.sendall(b"%x\r\n%s\r\n" % (len(piece), piece))
            client.sendall(b"0\r\nX-Trailer: yes\r\n\r\n")
            return True
        if target == "/gzip":
            body = gzip.compress(b"compressed payload" * 20)
            return send("200 OK", ["Content-Encoding: gzip",
                                   "Content-Type: text/plain",
                                   "Content-Length: %d" % len(body)], body)
        if target == "/redirect":
            return send("302 Found", ["Location: /plain",
                                      "Content-Length: 0"], b"")
        if target == "/loop":
            return send("302 Found", ["Location: /loop",
                                      "Content-Length: 0"], b"")
        if target == "/setcookie":
            return send("200 OK", ["Set-Cookie: a=1; Path=/",
                                   "Set-Cookie: b=2; Path=/deep",
                                   "Content-Length: 2"], b"ok")
        if target.startswith("/echo"):
            body = headers.get("cookie", "none").encode()
            return send("200 OK", ["Content-Type: text/plain",
                                   "Content-Length: %d" % len(body)], body)
        if target == "/cached":
            body = b"cache me"
            return send("200 OK", ["Cache-Control: max-age=60",
                                   "Content-Type: text/plain",
                                   "Content-Length: %d" % len(body)], body)
        if target == "/etag":
            if headers.get("if-none-match") == '"v1"':
                return send("304 Not Modified", ['ETag: "v1"'], b"")
            body = b"first version"
            return send("200 OK", ['ETag: "v1"', "Content-Type: text/plain",
                                   "Content-Length: %d" % len(body)], body)
        if target == "/nolength":
            client.sendall(b"HTTP/1.0 200 OK\r\nContent-Type: text/plain"
                           b"\r\n\r\nuntil eof")
            return False
        body = b"not found"
        return send("404 Not Found", ["Content-Length: %d" % len(body)], body)


class HeaderParsing(unittest.TestCase):
    def test_case_insensitive_multi_valued(self):
        headers = Headers([("Set-Cookie", "a=1"), ("SET-COOKIE", "b=2")])
        self.assertEqual(headers.get("set-cookie"), "a=1")
        self.assertEqual(len(headers.get_all("Set-Cookie")), 2)
        self.assertIn("SET-cookie", headers)

    def test_content_type(self):
        self.assertEqual(parse_content_type("text/HTML; charset=UTF-8"),
                         ("text/html", "utf-8"))
        self.assertEqual(parse_content_type("text/plain")[1], None)

    def test_content_decoding(self):
        self.assertEqual(decode_content(gzip.compress(b"x" * 100), "gzip"),
                         b"x" * 100)
        self.assertEqual(decode_content(b"plain", None), b"plain")


class Cookies(unittest.TestCase):
    def test_domain_and_path_matching(self):
        jar = CookieJar()
        url = URL.parse("http://example.com/a/b")
        jar.set_from_header("session=xyz; Path=/a", url)
        jar.set_from_header("other=1; Path=/z", url)
        self.assertEqual(jar.header_for(URL.parse("http://example.com/a/b")),
                         "session=xyz")
        self.assertIsNone(jar.header_for(URL.parse("http://other.com/a/b")))

    def test_secure_cookies_stay_on_https(self):
        jar = CookieJar()
        jar.set_from_header("s=1; Secure", URL.parse("https://example.com/"))
        self.assertIsNone(jar.header_for(URL.parse("http://example.com/")))
        self.assertEqual(jar.header_for(URL.parse("https://example.com/")),
                         "s=1")

    def test_expired_cookie_is_dropped(self):
        jar = CookieJar()
        url = URL.parse("http://example.com/")
        jar.set_from_header("a=1", url)
        jar.set_from_header("a=1; Max-Age=-1", url)
        self.assertIsNone(jar.header_for(url))


class ClientAgainstServer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = TestServer()
        cls.server.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.stop()

    def setUp(self):
        self.client = HTTPClient()

    def tearDown(self):
        self.client.close()

    def url(self, path):
        return self.server.base + path

    def test_simple_get(self):
        response = self.client.get(self.url("/plain"))
        self.assertEqual(response.status, 200)
        self.assertEqual(response.body, b"hello world")
        self.assertEqual(response.content_type, ("text/plain", "utf-8"))

    def test_chunked_body_is_reassembled(self):
        response = self.client.get(self.url("/chunked"))
        self.assertEqual(response.body, b"<html>chunked body</html>")

    def test_gzip_is_decoded(self):
        response = self.client.get(self.url("/gzip"))
        self.assertEqual(response.body, b"compressed payload" * 20)

    def test_redirects_are_followed(self):
        response = self.client.get(self.url("/redirect"))
        self.assertEqual(response.body, b"hello world")
        self.assertEqual(response.url.path, "/plain")

    def test_redirect_loop_is_detected(self):
        from mote.http import HTTPError
        with self.assertRaises(HTTPError):
            self.client.get(self.url("/loop"))

    def test_body_without_content_length_reads_to_eof(self):
        response = self.client.get(self.url("/nolength"))
        self.assertEqual(response.body, b"until eof")

    def test_cookies_round_trip(self):
        self.client.get(self.url("/setcookie"))
        response = self.client.get(self.url("/echo"))
        self.assertIn("a=1", response.body.decode())
        self.assertNotIn("b=2", response.body.decode())

    def test_cache_serves_the_second_request(self):
        before = len(self.server.requests)
        first = self.client.get(self.url("/cached"))
        second = self.client.get(self.url("/cached"))
        self.assertFalse(first.from_cache)
        self.assertTrue(second.from_cache)
        self.assertEqual(second.body, b"cache me")
        self.assertEqual(len(self.server.requests) - before, 1)

    def test_etag_revalidation(self):
        first = self.client.get(self.url("/etag"))
        self.client.cache._entries[str(URL.parse(self.url("/etag")))] \
            .expires = 0
        second = self.client.get(self.url("/etag"))
        self.assertEqual(first.body, second.body)
        self.assertTrue(second.from_cache)

    def test_connections_are_reused(self):
        client = HTTPClient(cache=HTTPCache(capacity=0))
        try:
            before = self.server.connections
            for _ in range(4):
                client.get(self.url("/plain"))
            self.assertEqual(self.server.connections - before, 1)
        finally:
            client.close()

    def test_404_is_returned_not_raised(self):
        response = self.client.get(self.url("/missing"))
        self.assertEqual(response.status, 404)
        self.assertFalse(response.ok)


if __name__ == "__main__":
    unittest.main()
