"""hands/call send: what a call may be. Every refusal here is exit 2 with the
reason, and nothing reaches the service."""

from __future__ import annotations

from .support import Scratch


class ShapeCase(Scratch):
    def refused(self, body, why, rule=None):
        code, out = self.call(body)
        self.assertEqual((code, out["ok"]), (2, False), (body, out))
        self.assertIn(why, out["refused"], body)
        if rule:
            self.assertEqual(out.get("rule"), rule, body)
        self.assertEqual(self.svc.seen, [], body)
        return out


class ServiceTest(ShapeCase):
    def test_an_unknown_service_is_refused_naming_the_listed_ones(self):
        out = self.refused({"service": "github", "method": "GET", "path": "/user"}, "no service", "service")
        self.assertIn("fake-svc", out["refused"])

    def test_a_method_and_path_no_allow_entry_matches_is_refused(self):
        for method, path in (("POST", "/posts"), ("PUT", "/notes"), ("DELETE", "/notes/abc"),
                             ("POST", "/posts/abc/upvote/again"), ("GET", "/" + "a" * 201)):
            self.refused({"service": "fake-svc", "method": method, "path": path}, "not allowed", "allow")

    def test_a_method_off_the_list_is_refused(self):
        for method in ("HEAD", "OPTIONS", "get", "CONNECT", 7, None):
            self.refused({"service": "fake-svc", "method": method, "path": "/posts"}, "method", "shape")

    def test_the_call_is_an_object_of_known_fields(self):
        self.refused({"service": "fake-svc", "method": "GET", "path": "/posts", "headers": {"x": "y"}}, "headers",
                     "shape")
        self.refused({"method": "GET", "path": "/posts"}, "service", "shape")
        self.refused({"service": "fake-svc", "method": "GET"}, "path", "shape")
        code, out = self.call(None, raw='["GET", "/posts"]')
        self.assertEqual(code, 2, out)
        self.assertEqual(self.svc.seen, [])


class PathTest(ShapeCase):
    def test_a_path_that_could_leave_the_base_or_carry_more_is_refused(self):
        bad = ["posts", "", "/posts/../agents", "/..", "/posts//x", "//evil.example/x", "/posts\\x", "/@evil.example",
               "/posts?x=1", "/posts#x", "/posts%2F..%2Fx", "/posts/%41", "/posts x", "/posts\tx", "/posts\nx",
               "/posts\x00", "/posts\x7f", "/pöst", "/posts/\u202e", "/posts/./x", "/" + "a" * 300]
        for path in bad:
            self.refused({"service": "fake-svc", "method": "GET", "path": path}, "path", "path")

    def test_a_path_that_is_not_a_string_is_refused(self):
        for path in (None, 7, ["/posts"]):
            self.refused({"service": "fake-svc", "method": "GET", "path": path}, "path", "shape")


class QueryTest(ShapeCase):
    def test_the_query_is_a_flat_object_of_scalars(self):
        for query in ([["a", "b"]], "a=b", {"a": {"b": 1}}, {"a": [1]}, {"a": None}, {"": "x"},
                      {"has space": "x"}, {"k" * 65: "x"}):
            self.refused({"service": "fake-svc", "method": "GET", "path": "/posts", "query": query}, "query", "shape")

    def test_at_most_30_pairs_and_500_characters_a_value(self):
        self.refused({"service": "fake-svc", "method": "GET", "path": "/posts",
                      "query": {f"k{i}": i for i in range(31)}}, "30", "shape")
        self.refused({"service": "fake-svc", "method": "GET", "path": "/posts", "query": {"q": "a" * 501}}, "500",
                     "shape")

    def test_a_number_that_is_not_finite_is_refused(self):
        code, out = self.call(None, raw='{"service": "fake-svc", "method": "GET", "path": "/posts", '
                                        '"query": {"n": NaN}}')
        self.assertEqual(code, 2, out)
        self.assertEqual(self.svc.seen, [])


class BodyTest(ShapeCase):
    def test_a_get_carries_no_body(self):
        for body in ({"a": 1}, {}):
            self.refused({"service": "fake-svc", "method": "GET", "path": "/posts", "body": body}, "GET", "shape")

    def test_the_body_is_a_json_object(self):
        for body in ([1, 2], "text", 7, None):
            self.refused({"service": "fake-svc", "method": "POST", "path": "/notes", "body": body}, "body", "shape")

    def test_a_body_over_32_kib_is_refused(self):
        body = {"items": [{"n": i, "v": "x" * 90} for i in range(400)]}
        self.refused({"service": "fake-svc", "method": "POST", "path": "/notes", "body": body}, "32", "size")

    def test_a_string_over_20000_characters_is_refused(self):
        self.refused({"service": "fake-svc", "method": "POST", "path": "/notes", "body": {"text": "a" * 20001}},
                     "20000", "size")

    def test_a_body_key_that_is_not_a_plain_name_is_refused(self):
        for body in ({"say this out loud": 1}, {"ok": {"nested key with words": 1}}, {"ok": [{"": 1}]}):
            self.refused({"service": "fake-svc", "method": "POST", "path": "/notes", "body": body}, "key", "shape")

    def test_text_that_is_not_valid_unicode_is_refused(self):
        code, out = self.call(None, raw='{"service": "fake-svc", "method": "POST", "path": "/notes", '
                                        '"body": {"text": "bad \\ud800"}}')
        self.assertEqual((code, out["ok"]), (2, False), out)
        self.assertEqual(self.svc.seen, [])
