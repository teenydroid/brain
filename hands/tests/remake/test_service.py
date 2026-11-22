"""The hand's real `Ingest` client (hands/lib/teeny_hands/remake/service.py)
against an in-process HTTP server on 127.0.0.1: what it does with a body
that is too big, a redirect, an error envelope and a fetch id that is not
one. No network beyond loopback."""

from __future__ import annotations

import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .support import FETCH_ID
from teeny_hands.remake.service import Ingest, IngestError


class Server:
    """A server whose answer to each request is `answer(handler)`; every
    path asked is kept in `paths`."""

    def __init__(self, answer):
        self.paths = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _go(self):
                outer.paths.append((self.command, self.path))
                length = int(self.headers.get("content-length") or 0)
                if length:
                    self.rfile.read(length)
                answer(self)

            do_GET = do_POST = do_DELETE = _go

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


def send(handler, status, body=b"", headers=None):
    handler.send_response(status)
    for key, value in (headers or {}).items():
        handler.send_header(key, value)
    handler.send_header("content-length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


class ClientTest(unittest.TestCase):
    def serve(self, answer):
        server = Server(answer)
        self.addCleanup(server.close)
        return server

    def test_a_body_over_the_limit_is_a_413_error_not_a_crash(self):
        server = self.serve(lambda h: send(h, 200, b"x" * 5000))
        with self.assertRaises(IngestError) as caught:
            Ingest(server.url).file(FETCH_ID, "shot-1.jpg", 1024)
        self.assertEqual(caught.exception.status, 413)
        self.assertIn("more than", str(caught.exception))

    def test_a_body_within_the_limit_is_returned_whole(self):
        server = self.serve(lambda h: send(h, 200, b"\xff\xd8\xff" + b"x" * 100))
        self.assertEqual(len(Ingest(server.url).file(FETCH_ID, "shot-1.jpg", 1024)), 103)

    def test_a_redirect_is_not_followed(self):
        target = self.serve(lambda h: send(h, 200, json.dumps({"status": 200, "data": {"entries": []}}).encode()))
        server = self.serve(lambda h: send(h, 302, b"", {"location": target.url + "/teeny/v1/listings"}))
        with self.assertRaises(IngestError) as caught:
            Ingest(server.url).listing("https://www.tiktok.com/@someone")
        self.assertEqual(caught.exception.status, 302)
        self.assertEqual(target.paths, [])

    def test_an_error_envelope_carries_its_message_cut_to_300_characters(self):
        message = "a" * 299 + "BC" + "z" * 300
        server = self.serve(lambda h: send(h, 422, json.dumps({"status": 422, "message": message}).encode()))
        with self.assertRaises(IngestError) as caught:
            Ingest(server.url).probe("https://youtu.be/dQw4w9WgXcQ")
        self.assertEqual(caught.exception.status, 422)
        text = str(caught.exception)
        self.assertIn("answered 422: " + "a" * 299 + "B", text)
        self.assertNotIn("BC", text)
        self.assertNotIn("z", text)

    def test_an_error_with_no_readable_envelope_says_only_the_status(self):
        server = self.serve(lambda h: send(h, 500, b"<html>oops</html>"))
        with self.assertRaises(IngestError) as caught:
            Ingest(server.url).ping()
        self.assertEqual((caught.exception.status, str(caught.exception)), (500, "the ingest service answered 500"))

    def test_a_service_that_is_not_there_is_an_error_with_no_status(self):
        server = Server(lambda h: send(h, 200))
        url = server.url
        server.close()
        with self.assertRaises(IngestError) as caught:
            Ingest(url).ping()
        self.assertIsNone(caught.exception.status)

    def test_a_fetch_whose_id_is_not_one_is_refused_and_nothing_else_is_asked(self):
        bad = {"status": 200, "data": {"fetch_id": "../../etc", "files": []}}
        server = self.serve(lambda h: send(h, 200, json.dumps(bad).encode()))
        client = Ingest(server.url)
        with self.assertRaises(IngestError):
            client.fetch({"url": "https://youtu.be/dQw4w9WgXcQ"})
        asked = len(server.paths)
        for call in (lambda: client.file("../../etc", "source.mp4", 100), lambda: client.delete("../x"),
                     lambda: client.file("ABCDEF", "source.mp4", 100), lambda: client.file(FETCH_ID, "../x.mp4", 100),
                     lambda: client.file(FETCH_ID, "dl.mp4", 100)):
            with self.assertRaises(IngestError):
                call()
        self.assertEqual(len(server.paths), asked)

    def test_a_good_fetch_is_passed_through_and_the_routes_are_the_documented_ones(self):
        good = {"status": 200, "data": {"fetch_id": FETCH_ID, "shots": []}}
        server = self.serve(lambda h: send(h, 200, json.dumps(good).encode()))
        client = Ingest(server.url)
        self.assertEqual(client.fetch({"url": "u"})["fetch_id"], FETCH_ID)
        client.file(FETCH_ID, "audio.m4a", 100)
        client.delete(FETCH_ID)
        self.assertEqual(server.paths, [("POST", "/teeny/v1/fetches"), ("GET", f"/teeny/v1/fetches/{FETCH_ID}/audio.m4a"),
                                        ("DELETE", f"/teeny/v1/fetches/{FETCH_ID}")])


if __name__ == "__main__":
    unittest.main()
