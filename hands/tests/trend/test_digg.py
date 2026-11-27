"""teeny_hands.trend.digg: the two public reads, how they are asked and every
way Digg's answer can be something other than what was asked for."""

from __future__ import annotations

import json

from .support import Scratch, cluster, http_error, post, row, sse, tool_result
from teeny_hands import hand
from teeny_hands.trend import digg


class FeedTest(Scratch):
    def test_a_topic_is_one_get_of_the_feed_with_nothing_but_a_generic_agent(self):
        self.board("ai", row(1, "ai", 500, "a title"), row(2, "ai", 90))
        got = digg.top("ai", 10)
        (sent,) = self.calls()
        self.assertEqual((sent["method"], sent["body"]), ("GET", None))
        self.assertEqual(sent["query"], {"topic": "ai", "view": "top", "range": "24h", "published": "1"})
        self.assertTrue(sent["url"].startswith("https://digg.com/api/feed/continuous?"))
        self.assertEqual(sent["headers"], {"User-agent": digg.USER_AGENT, "Accept": "application/json"})
        self.assertEqual(got[0], {"rank": 1, "clusterId": cluster(1), "clusterUrlId": "s1", "url": "https://digg.com/ai/s1",
                                  "topic": "ai", "title": "a title", "description": "what story 1 says",
                                  "diggScore": 500, "postCount": 10, "uniqueAuthors": 8,
                                  "publishedAt": "2026-11-18T05:00:00+00:00"})
        self.assertEqual([r["clusterUrlId"] for r in got], ["s1", "s2"])

    def test_pages_are_followed_until_the_limit_and_cut_to_it(self):
        self.pages("ai", (row(1, "ai", 9), row(2, "ai", 8)), (row(3, "ai", 7), row(4, "ai", 6)), (row(5, "ai", 5),))
        self.assertEqual([r["clusterUrlId"] for r in digg.top("ai", 3)], ["s1", "s2", "s3"])
        self.assertEqual([c["query"].get("cursor") for c in self.calls()], [None, "1"])

    def test_the_last_page_ends_the_read_and_a_topic_is_a_slug(self):
        self.pages("ai", (row(1, "ai", 9),))
        self.assertEqual(len(digg.top("ai", 30)), 1)
        self.assertEqual(len(self.calls()), 1)
        for bad in ("ai&view=x", "A I", "", "ai/../x"):
            with self.assertRaises(hand.HandError):
                digg.top(bad, 5)

    def test_a_feed_with_no_posts_list_is_bad_json(self):
        self.fixtures["feed:ai"] = {"nothing": 1}
        # the fake adds nextCursor only; the feed answer has no `posts`
        with self.assertRaises(digg.DiggError) as caught:
            digg.top("ai", 5)
        self.assertEqual(caught.exception.reason, "bad_json")


class PostsTest(Scratch):
    def test_a_story_is_one_digg_get_post_call_and_its_sources_reshaped(self):
        self.sources(7, post("Ana", "on x", n=70), post("Page", "a page", kind="webpage"))
        self.fixtures[f"post:{cluster(7)}"]["sources"][0]["url"] = "https://x.com/ana_x/status/2126359767362748777"
        self.fixtures[f"post:{cluster(7)}"]["sources"][1].update(url="https://example.com/a", title="A page",
                                                                  author=None)
        meta, got = digg.posts(cluster(7))
        (sent,) = self.calls()
        self.assertEqual((sent["method"], sent["url"]), ("POST", "https://digg.com/mcp"))
        self.assertEqual(sent["body"], {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
            "name": "digg_get_post", "arguments": {"id": cluster(7), "sourceOffset": 0, "sourceLimit": 20}}})
        self.assertEqual(sent["headers"], {"User-agent": digg.USER_AGENT, "Content-type": "application/json",
                                           "Accept": "application/json, text/event-stream"})
        self.assertEqual(got[0], {"kind": "x", "author": {"display_name": "Ana", "username": "ana_x"},
                                  "body": "on x", "post_x_id": "2126359767362748777",
                                  "posted_at": "2026-11-18T05:30:00.000Z",
                                  "xUrl": "https://x.com/ana_x/status/2126359767362748777"})
        self.assertEqual(got[1], {"kind": "webpage", "author": {}, "body": "a page", "posted_at": "2026-11-18T05:30:00.000Z",
                                  "xUrl": "https://example.com/a", "title": "A page"})
        self.assertEqual(meta, {"clusterId": cluster(7), "clusterUrlId": "s7", "topic": "ai", "url": "https://digg.com/ai/s7",
                                "title": "story 7", "source": "live", "by": "rank", "count": 2,
                                "sourcesTruncated": False})

    def test_every_source_is_read_a_page_at_a_time(self):
        self.sources(1, *[post(f"P{i}", f"text {i}", n=i) for i in range(45)])
        _, got = digg.posts(cluster(1))
        self.assertEqual([r["body"] for r in got], [f"text {i}" for i in range(45)])
        self.assertEqual([c["body"]["params"]["arguments"]["sourceOffset"] for c in self.calls()], [0, 20, 40])

    def test_a_cluster_is_a_uuid_and_nothing_is_asked_otherwise(self):
        with self.assertRaises(hand.HandError):
            digg.posts("s7")
        self.assertEqual(self.calls(), [])

    def test_a_result_without_its_story_or_sources_is_bad_json(self):
        for shape in ({"sources": []}, {"story": {}}, {"story": {}, "sources": None}, [1]):
            with self.subTest(shape=shape):
                self.sent.clear()
                self.fixtures[f"post:{cluster(2)}"] = None
                got = tool_result(shape)
                with self._answers(got), self.assertRaises(digg.DiggError) as caught:
                    digg.posts(cluster(2))
                self.assertEqual(caught.exception.reason, "bad_json")

    def _answers(self, structured):
        from unittest import mock

        from .support import Reply

        return mock.patch("urllib.request.OpenerDirector.open", return_value=Reply(sse(structured)))


class UnwrapTest(Scratch):
    """digg.unwrap(): an SSE frame, plain JSON, and every failure."""

    def test_an_sse_event_message_frame(self):
        raw = b"event: message\r\ndata: " + json.dumps(tool_result({"a": 1})).encode() + b"\r\n\r\n"
        self.assertEqual(digg.unwrap("t", raw), {"a": 1})

    def test_data_lines_of_one_event_are_joined(self):
        text = json.dumps(tool_result({"a": [1, 2]}))
        raw = f"event: message\ndata: {text[:20]}\ndata: {text[20:]}\n\n".encode()
        self.assertEqual(digg.unwrap("t", raw), {"a": [1, 2]})

    def test_plain_json(self):
        self.assertEqual(digg.unwrap("t", json.dumps(tool_result({"a": 1})).encode()), {"a": 1})

    def test_the_text_content_when_there_is_no_structured_content(self):
        raw = json.dumps({"result": {"content": [{"type": "text", "text": ' {"a": 2} '}]}}).encode()
        self.assertEqual(digg.unwrap("t", raw), {"a": 2})
        raw = json.dumps({"result": {"content": [{"type": "text", "text": "prose"}]}}).encode()
        with self.assertRaises(digg.DiggError) as caught:
            digg.unwrap("t", raw)
        self.assertEqual(caught.exception.reason, "bad_json")

    def test_a_json_rpc_error_is_failed_with_its_message(self):
        with self.assertRaises(digg.DiggError) as caught:
            digg.unwrap("digg_get_post", sse({"error": {"code": -32602, "message": "Post not found."}}))
        self.assertEqual(caught.exception.reason, "failed")
        self.assertIn("Post not found.", str(caught.exception))
        self.assertIn("-32602", str(caught.exception))

    def test_an_is_error_result_is_failed_with_its_text(self):
        raw = sse({"result": {"isError": True, "content": [{"type": "text", "text": "invalid query"}],
                              "structuredContent": {"ignored": True}}})
        with self.assertRaises(digg.DiggError) as caught:
            digg.unwrap("digg_get_post", raw)
        self.assertEqual((caught.exception.reason, "invalid query" in str(caught.exception)), ("failed", True))
        with self.assertRaises(digg.DiggError) as caught:
            digg.unwrap("t", sse({"result": {"isError": True}}))
        self.assertIn("no message", str(caught.exception))

    def test_a_tool_that_says_to_come_back_later_is_rate_limited(self):
        raw = sse({"result": {"isError": True, "content": [{"type": "text", "text": "search is temporarily unavailable, retry after 60s"}]}})
        with self.assertRaises(digg.DiggError) as caught:
            digg.unwrap("t", raw)
        self.assertEqual(caught.exception.reason, "rate_limited")

    def test_bad_json_and_the_wrong_shape(self):
        for raw in (b"<html>no</html>", b"", b"event: message\ndata: {oops\n\n", b"[1]", b'{"jsonrpc": "2.0"}',
                    b'{"result": 3}'):
            with self.subTest(raw=raw), self.assertRaises(digg.DiggError) as caught:
                digg.unwrap("t", raw)
            self.assertEqual(caught.exception.reason, "bad_json")


class FailureTest(Scratch):
    def setUp(self) -> None:
        super().setUp()
        self.sources(1, post("Ana", "hi"))

    def reason(self, how: str) -> digg.DiggError:
        self.behave_keys({f"posts:{cluster(1)}": how})
        with self.assertRaises(digg.DiggError) as caught:
            digg.posts(cluster(1))
        return caught.exception

    def test_http_429_is_rate_limited(self):
        err = self.reason("rate")
        self.assertEqual(err.reason, "rate_limited")
        self.assertIn("429", str(err))
        self.assertEqual(len(self.calls()), 1, "no retry: the studio comes back at its next slot")

    def test_a_timeout_is_a_timeout_however_urllib_says_it(self):
        self.configure(TREND_TIMEOUT_SECS="3")
        self.assertEqual(self.reason("sleep").reason, "timeout")
        self.assertEqual(self.reason("urlsleep").reason, "timeout")
        self.assertEqual({c["timeout"] for c in self.calls()}, {3})

    def test_the_timeout_is_bounded_one_to_120_and_defaults_to_30(self):
        for value, want in (("500", 120), ("0", 1), ("x", 30), (None, 30), ("45", 45)):
            self.configure(TREND_TIMEOUT_SECS=value)
            self.sent.clear()
            digg.posts(cluster(1))
            self.assertEqual(self.calls()[0]["timeout"], want, value)

    def test_garbage_is_bad_json(self):
        self.assertEqual(self.reason("garbage").reason, "bad_json")

    def test_a_json_rpc_error_and_an_is_error_result_are_failed(self):
        self.assertEqual(self.reason("error").reason, "failed")
        self.assertEqual(self.reason("fail").reason, "failed")

    def test_other_http_statuses_and_a_dead_network_are_failed(self):
        for how in ("http500", "down", "redirect"):
            self.assertEqual(self.reason(how).reason, "failed", how)

    def test_a_json_rpc_error_on_an_http_error_status_is_its_message(self):
        from unittest import mock

        body = json.dumps({"error": {"code": -32602, "message": "bad id"}}).encode()
        with mock.patch("urllib.request.OpenerDirector.open", side_effect=http_error("https://digg.com/mcp", 400, body)):
            with self.assertRaises(digg.DiggError) as caught:
                digg.posts(cluster(1))
        self.assertEqual(caught.exception.reason, "failed")
        self.assertIn("bad id", str(caught.exception))
        self.assertIn("HTTP 400", str(caught.exception))

    def test_a_plain_json_answer_reads_as_a_frame_does(self):
        self.behave_keys({f"posts:{cluster(1)}": "plain"})
        self.assertEqual(digg.posts(cluster(1))[1][0]["body"], "hi")

    def test_missing_stays_in_the_vocabulary_and_is_never_raised(self):
        self.assertEqual(digg.DiggError("missing", "x").reason, "missing")


class BaseTest(Scratch):
    def test_the_base_is_digg_unless_an_https_origin_is_set(self):
        self.assertEqual(digg.base(), "https://digg.com")
        self.configure(TREND_DIGG_BASE="https://digg.test:8443/")
        self.assertEqual(digg.base(), "https://digg.test:8443")
        self.board("ai", row(1, "ai", 5))
        digg.top("ai", 1)
        self.assertTrue(self.calls()[0]["url"].startswith("https://digg.test:8443/api/feed/continuous?"))

    def test_anything_else_is_refused_before_a_request(self):
        for bad in ("http://digg.test", "ftp://x", "digg.test", "https://", "https://digg.test/mcp",
                    "https://u:p@digg.test", "https://digg.test?x=1", "file:///etc/passwd"):
            self.configure(TREND_DIGG_BASE=bad)
            with self.assertRaises(digg.DiggError, msg=bad) as caught:
                digg.top("ai", 1)
            self.assertEqual(caught.exception.reason, "failed")
        self.assertEqual(self.calls(), [])

    def test_the_process_environment_cannot_set_it(self):
        import os

        os.environ["TREND_DIGG_BASE"] = "https://evil.test"
        self.assertEqual(digg.base(), "https://digg.com")
