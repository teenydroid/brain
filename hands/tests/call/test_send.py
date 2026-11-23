"""hands/call send: the one call, with the key, and what comes back."""

from __future__ import annotations

import json
import urllib.error
from unittest import mock

from teeny_hands import rest

from .support import KEY, Scratch


class SendTest(Scratch):
    def test_a_read_is_sent_with_the_key_and_answers_its_status_and_json(self):
        self.svc.route("GET", "/api/v1/posts", {"posts": [{"id": "p1"}]})
        code, out = self.get("/posts", query={"sort": "new", "limit": 5, "brief": True})
        self.assertEqual(code, 0, out)
        self.assertEqual(out, {"ok": True, "status": 200, "json": {"posts": [{"id": "p1"}]}})
        (seen,) = self.svc.seen
        self.assertEqual((seen.method, seen.path), ("GET", "/api/v1/posts"))
        self.assertEqual(seen.query, {"sort": "new", "limit": "5", "brief": "true"})
        self.assertEqual(seen.authorization, f"Bearer {KEY}")
        self.assertEqual(seen.headers["user-agent"], "teeny-call-fake")
        self.assertEqual(seen.body, b"")

    def test_a_write_answers_the_services_own_status(self):
        self.svc.route("POST", "/api/v1/notes", {"id": 7}, status=201)
        code, out = self.write(body={"n": 1, "done": False, "tags": None})
        self.assertEqual((code, out), (0, {"ok": True, "status": 201, "json": {"id": 7}}))
        self.assertEqual(self.svc.seen[0].json(), {"n": 1, "done": False, "tags": None})

    def test_an_empty_answer_is_an_empty_object(self):
        self.svc.route("DELETE", "/api/v1/notes/3", raw=b"", status=204)
        code, out = self.write("DELETE", "/notes/3")
        self.assertEqual((code, out["status"], out["json"]), (0, 204, {}))

    def test_a_429_is_refused_with_the_wait(self):
        self.svc.route("GET", "/api/v1/posts", {"error": "slow down"}, status=429, headers={"Retry-After": "42"})
        code, out = self.get("/posts")
        self.assertEqual((code, out["ok"], out["status"], out["retry_after"]), (2, False, 429, 42.0))
        self.assertIn("429", out["refused"])

    def test_an_error_status_is_an_error_with_the_status_and_never_the_request(self):
        self.svc.route("GET", "/api/v1/posts", {"error": "no such thing"}, status=404)
        code, out = self.get("/posts", query={"q": "secret-ish"})
        self.assertEqual((code, out["ok"], out["status"]), (1, False, 404))
        self.assertIn("no such thing", out["error"])
        text = json.dumps(out)
        for never in ("127.0.0.1", "/api/v1", "secret-ish", "Bearer", "authorization"):
            self.assertNotIn(never, text)

    def test_a_redirect_is_not_followed_and_the_key_does_not_go_with_it(self):
        self.svc.route("GET", "/api/v1/posts", raw=b"", status=302,
                       headers={"Location": f"{self.svc.base}/elsewhere"})
        self.svc.route("GET", "/elsewhere", {"took": "the key"})
        code, out = self.get("/posts")
        self.assertEqual((code, out["ok"], out["status"]), (1, False, 302))
        self.assertIn("redirect", out["error"])
        self.assertEqual([s.path for s in self.svc.seen], ["/api/v1/posts"])

    def test_an_unreachable_service_is_an_error_naming_no_url(self):
        self.services_file.write_text(self.services_file.read_text().replace(self.svc.base, "http://127.0.0.1:9"))
        code, out = self.get("/posts")
        self.assertEqual((code, out["ok"]), (1, False))
        self.assertNotIn("127.0.0.1", json.dumps(out))

    def test_no_more_of_an_answer_is_read_than_may_be_passed_on(self):
        from teeny_hands import rest
        self.svc.route("GET", "/api/v1/posts", {"pad": "x" * (2 * 1024 * 1024)})
        read: list[int] = []
        real = rest._OPENER.open

        def watched(request, timeout=None):
            response = real(request, timeout=timeout)
            inner = response.read

            def counted(*args):
                data = inner(*args)
                read.append(len(data))
                return data

            response.read = counted
            return response

        with mock.patch.object(rest._OPENER, "open", watched):
            code, out = self.get()
        self.assertEqual((code, out["rule"]), (2, "size"), out)
        self.assertLessEqual(sum(read), 256 * 1024 + 1)

    def test_an_answer_over_256_kib_is_refused(self):
        self.svc.route("GET", "/api/v1/posts", {"posts": ["x" * 1000] * 300})
        code, out = self.get("/posts")
        self.assertEqual((code, out["ok"]), (2, False))
        self.assertIn("256 KiB", out["refused"])
        self.assertNotIn("json", out)

    def test_an_error_body_over_256_kib_is_not_read_whole(self):
        self.svc.route("GET", "/api/v1/posts", raw=b"x" * (3 * 1024 * 1024), status=500, content_type="text/plain")
        read: list[int] = []
        real = rest._OPENER.open

        def watched(request, timeout=None):
            try:
                return real(request, timeout=timeout)
            except urllib.error.HTTPError as exc:
                inner = exc.read

                def counted(*args):
                    data = inner(*args)
                    read.append(len(data))
                    return data

                exc.read = counted
                raise

        with mock.patch.object(rest._OPENER, "open", watched):
            code, out = self.get("/posts")
        self.assertEqual((code, out["rule"]), (2, "size"), out)
        self.assertEqual(sum(read), 256 * 1024 + 1)

    def test_an_answer_that_carries_the_key_is_not_returned(self):
        self.svc.route("GET", "/api/v1/agents/me", {"agent": {"name": "teeny", "api_key": KEY}})
        code, out = self.get("/agents/me")
        self.assertEqual((code, out["ok"]), (2, False))
        self.assertIn("key", out["refused"])
        self.assertNotIn("json", out)

    def test_an_error_that_echoes_the_key_does_not_pass_it_on(self):
        self.svc.route("GET", "/api/v1/posts", {"error": f"bad key {KEY}"}, status=401)
        code, out = self.get("/posts")  # self.call asserts the key is in no output
        self.assertEqual((code, out["ok"], out["status"]), (1, False, 401))

    def test_an_error_echoing_the_key_across_the_clip_does_not_leak_a_piece(self):
        # The service's message is clipped to 200 characters; the key must go
        # before the clip, or a key straddling it is left as an unmatchable stub.
        self.svc.route("GET", "/api/v1/posts", {"error": "x" * 190 + KEY}, status=401)
        code, out = self.get("/posts")
        self.assertEqual((code, out["ok"], out["status"]), (1, False, 401))
        self.assertNotIn(KEY[:6], json.dumps(out))

    def test_without_the_key_in_secrets_nothing_is_sent(self):
        self.configure(FAKE_SVC_API_KEY=None)
        code, out = self.get("/posts")
        self.assertEqual(code, 1, out)
        self.assertIn("FAKE_SVC_API_KEY", out["error"])
        self.assertEqual(self.svc.seen, [])
