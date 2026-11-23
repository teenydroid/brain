"""bin/lib/webfetch.py: the one GET the broker's /fetch makes for Teeny's
jailed code. Each rule is a test. Nothing here reaches the internet: the
resolver and the connection are Python seams (webfetch._resolve and
webfetch._connect, patched), and every request lands on a plain HTTP server
on 127.0.0.1 that stands in for any host."""

from __future__ import annotations

import http.server
import socket
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest import mock
from urllib.parse import unquote

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "bin" / "lib"))

import webfetch  # noqa: E402

PUBLIC = "93.184.216.34"


class Site(http.server.BaseHTTPRequestHandler):
    """Every host's pages: the path says what to answer."""

    seen: list[dict] = []

    def log_message(self, *args):
        pass

    def answer(self, code, body=b"", headers=()):
        self.send_response(code)
        for name, value in headers:
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        Site.seen.append({"path": self.path, "headers": dict(self.headers), "command": self.command})
        path, _, query = self.path.partition("?")
        if path == "/ok":
            self.answer(200, b"hello world", [("Content-Type", "text/html; charset=utf-8")])
        elif path == "/type":
            self.answer(200, b'{"a": 1}', [("Content-Type", unquote(query))])
        elif path == "/notype":
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"hi")
        elif path == "/latin":
            self.answer(200, "café".encode("latin-1") + b"\xff", [("Content-Type", "text/plain; charset=iso-8859-1")])
        elif path == "/broken":
            self.answer(200, b"ok \xff\xfe bad", [("Content-Type", "text/plain; charset=utf-8")])
        elif path == "/big":
            body = b"a" * (webfetch.MAX_BYTES + 5000)
            self.answer(200, body, [("Content-Type", "text/plain")])
        elif path == "/to":
            self.answer(302, b"", [("Location", query)])
        elif path.startswith("/hop/"):
            n = int(path.rsplit("/", 1)[1])
            if n:
                self.answer(301, b"", [("Location", f"/hop/{n - 1}")])
            else:
                self.answer(200, b"landed", [("Content-Type", "text/plain")])
        elif path == "/slow":
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", "100")
            self.end_headers()
            for _ in range(20):
                try:
                    self.wfile.write(b"x")
                    self.wfile.flush()
                except OSError:
                    return
                time.sleep(0.2)
        else:
            self.answer(404, b"no", [("Content-Type", "text/plain")])


class FetchCase(unittest.TestCase):
    def setUp(self):
        Site.seen = []
        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Site)
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.names: dict[str, list[str]] = {"example.com": [PUBLIC], "other.example.org": ["93.184.216.35"]}
        self.resolved: list[str] = []
        self.connected: list[tuple[str, str]] = []
        port = self.server.server_address[1]

        def resolve(host):
            self.resolved.append(host)
            if host not in self.names:
                raise socket.gaierror(socket.EAI_NONAME, "no such name")
            return list(self.names[host])

        def connect(ip, host, timeout):
            self.connected.append((ip, host))
            return socket.create_connection(("127.0.0.1", port), timeout=timeout)

        for name, fn in (("_resolve", resolve), ("_connect", connect)):
            patcher = mock.patch.object(webfetch, name, fn)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.sent = 0

    def fetch(self, url, **kw):
        def count():
            self.sent += 1
        return webfetch.fetch(url, on_send=count, **kw)

    def refused(self, url, why=None, **kw):
        with self.assertRaises(webfetch.Refused, msg=url) as caught:
            self.fetch(url, **kw)
        if why:
            self.assertIn(why, str(caught.exception))
        return str(caught.exception)


class UrlTest(FetchCase):
    def test_a_public_https_page_is_read(self):
        got = self.fetch("https://example.com/ok")
        self.assertEqual((got["http"], got["url"], got["text"], got["truncated"]),
                         (200, "https://example.com/ok", "hello world", False))
        self.assertEqual(got["content_type"], "text/html; charset=utf-8")
        self.assertEqual(self.sent, 1)

    def test_only_https(self):
        for url in ("http://example.com/ok", "ftp://example.com/ok", "file:///etc/passwd", "gopher://example.com/",
                    "//example.com/ok", "example.com/ok"):
            self.refused(url)
        self.assertEqual((self.sent, Site.seen), (0, []))

    def test_no_credentials_in_the_url(self):
        for url in ("https://user:pw@example.com/ok", "https://user@example.com/ok", "https://:pw@example.com/ok"):
            self.refused(url, "credentials")
        self.assertEqual(Site.seen, [])

    def test_port_443_or_none(self):
        self.assertEqual(self.fetch("https://example.com:443/ok")["http"], 200)
        for url in ("https://example.com:8443/ok", "https://example.com:80/ok", "https://example.com:22/ok",
                    "https://example.com:0/ok", "https://example.com:99999/ok", "https://example.com:x/ok"):
            self.refused(url)
        self.assertEqual(len(Site.seen), 1)

    def test_a_url_over_2000_characters_is_refused(self):
        self.refused("https://example.com/" + "a" * 1981, "2000")
        self.assertEqual(Site.seen, [])
        self.assertEqual(self.fetch("https://example.com/ok?" + "a" * (2000 - 23))["http"], 200)

    def test_an_address_or_a_local_name_is_not_a_public_host(self):
        for url in ("https://127.0.0.1/ok", "https://[::1]/ok", f"https://{PUBLIC}/ok", "https://localhost/ok",
                    "https://intranet/ok", "https://box.local/ok", "https://metadata.google.internal/ok",
                    "https://169.254.169.254/latest/meta-data/"):
            self.refused(url)
        self.assertEqual(self.resolved, [])

    def test_a_url_that_is_not_a_string_is_refused(self):
        for url in (None, 7, ["https://example.com/ok"], ""):
            self.refused(url)


class AddressTest(FetchCase):
    NOT_PUBLIC = ("127.0.0.1", "127.8.9.1", "10.1.2.3", "172.16.0.1", "192.168.1.1", "169.254.169.254",
                  "169.254.1.1", "100.64.0.1", "100.100.100.200", "0.0.0.0", "224.0.0.1", "239.1.1.1",
                  "255.255.255.255", "198.18.0.1", "240.0.0.1", "::1", "::", "fe80::1", "fc00::1",
                  "fd00:ec2::254", "ff02::1", "ff0e::1", "::ffff:127.0.0.1", "::ffff:10.0.0.1",
                  "::ffff:93.184.216.34", "64:ff9b::a00:1", "64:ff9b::7f00:1", "2002:a00:1::1", "2001::1")

    def test_a_host_that_resolves_to_an_address_that_is_not_public_is_refused(self):
        for address in self.NOT_PUBLIC:
            self.names["example.com"] = [address]
            self.refused("https://example.com/ok", "public")
        self.assertEqual((self.connected, Site.seen, self.sent), ([], [], 0))

    def test_one_private_answer_among_public_ones_refuses_the_host(self):
        self.names["example.com"] = [PUBLIC, "10.0.0.1"]
        self.refused("https://example.com/ok", "public")
        self.assertEqual(self.connected, [])

    def test_public_v6_and_nat64_of_a_public_address_are_fine(self):
        for address in ("2606:4700::1111", "64:ff9b::5db8:d822"):
            self.names["example.com"] = [address]
            self.assertEqual(self.fetch("https://example.com/ok")["http"], 200)

    def test_it_connects_to_the_address_it_checked_named_by_its_host(self):
        # Resolved once; the connection goes to that address, so a second
        # answer from the name's server (a rebinding) is never asked for.
        self.fetch("https://example.com/ok")
        self.assertEqual(self.resolved, ["example.com"])
        self.assertEqual(self.connected, [(PUBLIC, "example.com")])
        self.assertEqual(Site.seen[-1]["headers"]["Host"], "example.com")

    def test_a_name_that_does_not_resolve_is_refused(self):
        self.refused("https://nowhere.example.net/ok")
        self.assertEqual(self.connected, [])


class DenyTest(FetchCase):
    def test_a_host_on_the_deny_list_and_its_subdomains_are_refused(self):
        self.names.update({"x.com": [PUBLIC], "mobile.x.com": [PUBLIC], "mail.google.com": [PUBLIC]})
        for url in ("https://x.com/ok", "https://mobile.x.com/ok", "https://X.COM./ok", "https://mail.google.com/"):
            self.refused(url, "deny")
        self.assertEqual((self.resolved, Site.seen), ([], []))

    def test_the_operators_list_is_read_from_deploy(self):
        self.names["example.com"] = [PUBLIC]
        self.refused("https://example.com/ok", "deny", deny=["example.com"])
        with mock.patch.object(webfetch.websites, "load_deny", side_effect=FileNotFoundError("gone")):
            self.refused("https://example.com/ok", "deny list")
        self.assertEqual(Site.seen, [])


class RedirectTest(FetchCase):
    def test_up_to_five_redirects_are_followed_and_the_final_url_answered(self):
        got = self.fetch("https://example.com/hop/5")
        self.assertEqual((got["http"], got["text"], got["url"]), (200, "landed", "https://example.com/hop/0"))
        self.assertEqual(self.sent, 6)

    def test_a_sixth_redirect_ends_the_fetch(self):
        self.refused("https://example.com/hop/6", "redirect")
        self.assertEqual(self.sent, 6)

    def test_every_hop_is_checked_again(self):
        self.names["inside.example.org"] = ["10.0.0.5"]
        self.names["x.com"] = [PUBLIC]
        for target in ("http://other.example.org/ok", "https://inside.example.org/ok", "https://x.com/ok",
                       "https://127.0.0.1/ok", "https://other.example.org:8443/ok", "https://u:p@other.example.org/ok",
                       "file:///etc/passwd"):
            Site.seen = []
            self.refused(f"https://example.com/to?{target}")
            self.assertEqual([s["path"] for s in Site.seen], [f"/to?{target}"], target)

    def test_a_redirect_to_another_public_host_is_followed_there(self):
        got = self.fetch("https://example.com/to?https://other.example.org/ok")
        self.assertEqual(got["url"], "https://other.example.org/ok")
        self.assertEqual(self.connected, [(PUBLIC, "example.com"), ("93.184.216.35", "other.example.org")])


class AnswerTest(FetchCase):
    def test_one_bare_get_with_a_fixed_user_agent(self):
        self.fetch("https://example.com/ok")
        seen = Site.seen[-1]
        self.assertEqual(seen["command"], "GET")
        self.assertEqual(seen["headers"]["User-Agent"], webfetch.USER_AGENT)
        self.assertEqual(seen["headers"].get("Accept-Encoding"), "identity")
        for header in ("Cookie", "Authorization", "Content-Length", "Content-Type", "Proxy-Authorization"):
            self.assertNotIn(header, seen["headers"])

    def test_text_types_are_read(self):
        for kind in ("text/plain", "text/csv", "application/json", "application/xml", "application/ld%2Bjson",
                     "application/rss%2Bxml", "application/json;%20charset=utf-8"):
            self.assertEqual(self.fetch(f"https://example.com/type?{kind}")["text"], '{"a": 1}', kind)

    def test_a_binary_type_is_refused_by_name(self):
        for kind in ("image/png", "application/octet-stream", "application/pdf", "video/mp4",
                     "application/x-json-but-not"):
            why = self.refused(f"https://example.com/type?{kind}")
            self.assertIn(kind, why)
        self.refused("https://example.com/notype", "content type")

    def test_the_text_is_decoded_by_its_charset_and_bad_bytes_replaced(self):
        self.assertEqual(self.fetch("https://example.com/latin")["text"], "caféÿ")
        self.assertEqual(self.fetch("https://example.com/broken")["text"], "ok �� bad")

    def test_past_2_mib_the_text_is_cut_and_says_so(self):
        got = self.fetch("https://example.com/big")
        self.assertEqual((len(got["text"]), got["truncated"]), (webfetch.MAX_BYTES, True))

    def test_a_page_that_takes_too_long_ends_the_fetch(self):
        with mock.patch.object(webfetch, "TOTAL_SECS", 1):
            started = time.monotonic()
            with self.assertRaises(webfetch.Failed):
                self.fetch("https://example.com/slow")
            self.assertLess(time.monotonic() - started, 3)

    def test_an_error_page_is_an_answer_with_its_status(self):
        got = self.fetch("https://example.com/missing")
        self.assertEqual((got["http"], got["text"]), (404, "no"))


class FrozenTest(unittest.TestCase):
    def test_the_fetcher_is_frozen(self):
        import frozen
        patterns = frozen.parse_patterns((REPO / "evals" / "frozen.txt").read_text())
        self.assertIsNotNone(frozen.frozen_pattern("bin/lib/webfetch.py", patterns))


if __name__ == "__main__":
    unittest.main()
