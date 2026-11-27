"""hands/trend top and story: the merged leaderboards, the posts behind the
leading stories, the cache a slot, and every way Digg can give nothing."""

from __future__ import annotations

import json
import os

from .support import Scratch, cluster, post, row
from teeny_hands import hand
from teeny_hands.trend import digg, top


class TopTest(Scratch):
    def setUp(self) -> None:
        super().setUp()
        self.board("ai", row(1, "ai", 500, "an agent startup raised money"), row(2, "ai", 90), row(3, "ai", 300))
        self.board("tech", row(4, "tech", 400, "a chip maker doubled revenue"), row(3, "tech", 310))
        # The rest of digg.TOPICS (widened 2026-11-22) list nothing here.
        for topic in digg.TOPICS:
            if topic not in ("ai", "tech"):
                self.board(topic)
        for n in (1, 2, 3, 4):
            self.sources(n, post("Ana", f"first post on {n}", n=10 * n), post("Page", "a web page", kind="webpage"),
                         post("Bo", f"second post on {n}", n=10 * n + 1), post("Cy", f"third post on {n}", n=10 * n + 2))

    def test_the_two_boards_are_merged_by_score_a_shared_story_once(self):
        got = top.top("2026-11-18:morning", 10, 2, 3)
        self.assertEqual([s["id"] for s in got["stories"]], ["s1", "s4", "s3", "s2"])
        shared = got["stories"][2]
        self.assertEqual((shared["score"], shared["topic"]), (310, "tech"))
        self.assertEqual(got["stories"][0]["summary"], "what story 1 says")
        self.assertEqual(got["stories"][0]["cluster"], cluster(1))
        self.assertEqual((got["unavailable"], got["cached"], got["source"]), ([], False, "digg"))

    def test_only_the_leading_stories_carry_their_first_x_posts(self):
        got = top.top("k", 10, 2, 2)
        lead, second, third = got["stories"][:3]
        self.assertEqual([p["text"] for p in lead["posts"]], ["first post on 1", "second post on 1"])
        self.assertEqual([p["by"] for p in lead["posts"]], ["Ana", "Bo"])
        self.assertTrue(all(p["kind"] == "x" for p in lead["posts"] + second["posts"]))
        self.assertNotIn("posts", third)
        posts_calls = [c["body"]["params"] for c in self.mcp_calls()]
        self.assertEqual([c["name"] for c in posts_calls], ["digg_get_post"] * 2)
        self.assertEqual([c["arguments"]["id"] for c in posts_calls], [cluster(1), cluster(4)])

    def test_a_bare_list_answer_reads_as_the_envelope_does(self):
        rows = [row(1, "ai", 5)]
        self.assertEqual(digg.rows(rows, "x"), digg.rows({"meta": {}, "results": rows}, "x"))
        with self.assertRaises(digg.DiggError) as caught:
            digg.rows({"meta": {}}, "x")
        self.assertEqual(caught.exception.reason, "bad_json")
        got = top.top("k", 10, 1, 3)
        self.assertEqual(len(got["stories"]), 4)
        self.assertEqual(len(got["stories"][0]["posts"]), 3)

    def test_a_slot_read_twice_asks_digg_once(self):
        first = top.top("2026-11-18:morning", 10, 1, 3)
        n = len(self.calls())
        again = top.top("2026-11-18:morning", 10, 1, 3)
        self.assertEqual(len(self.calls()), n)
        self.assertTrue(again["cached"])
        self.assertEqual(again["stories"], first["stories"])
        top.top("2026-11-18:evening", 10, 1, 3)
        self.assertGreater(len(self.calls()), n)
        n = len(self.calls())
        self.assertFalse(top.top("2026-11-18:morning", 10, 1, 3, fresh=True)["cached"])
        self.assertGreater(len(self.calls()), n)

    def test_with_no_slot_the_key_is_the_utc_hour(self):
        self.assertEqual(top.top(None, 10, 0, 1)["key"], "2026-11-18T11")
        with self.assertRaises(hand.HandError):
            top.cache_key("../etc/passwd")

    def test_a_topic_that_cannot_be_read_is_named_and_the_other_comes(self):
        self.behave_keys({"top:tech": "rate"})
        got = top.top("k", 10, 1, 3)
        self.assertEqual([s["id"] for s in got["stories"]], ["s1", "s3", "s2"])
        self.assertEqual(got["unavailable"][0]["topic"], "tech")
        self.assertEqual(got["unavailable"][0]["reason"], "rate_limited")

    def test_a_story_whose_posts_fail_is_kept_without_them(self):
        self.behave_keys({f"posts:{cluster(1)}": "fail"})
        got = top.top("k", 10, 2, 3)
        self.assertNotIn("posts", got["stories"][0])
        self.assertEqual(len(got["stories"][1]["posts"]), 3)
        self.assertEqual(got["unavailable"], [{"story": "s1", "reason": "failed",
                                               "error": got["unavailable"][0]["error"]}])

    def test_nothing_read_is_an_error_with_a_reason_and_is_not_kept(self):
        for how, reason in (("rate", "rate_limited"), ("garbage", "bad_json"), ("http500", "failed")):
            self.behave_keys({"top:ai": how, "top:tech": how})
            with self.assertRaises(hand.HandError) as caught:
                top.top("k", 10, 1, 3)
            self.assertEqual(caught.exception.detail["reason"], reason, how)
        self.assertFalse((self.root / "state" / "trend" / "top" / "k.json").exists())

    def test_a_digg_that_cannot_be_reached_is_said_and_never_a_crash(self):
        self.behave_keys({"top:ai": "down", "top:tech": "down", "top:world-business": "down",
                          "top:entertainment": "down", "top:sports": "down", "top:gaming": "down"})
        with self.assertRaises(hand.HandError) as caught:
            top.top("k", 10, 1, 3)
        self.assertEqual(caught.exception.detail["reason"], "failed")
        self.assertIn("could not reach Digg", str(caught.exception))

    def test_no_answer_in_time_is_a_timeout(self):
        self.configure(TREND_TIMEOUT_SECS="1")
        self.behave_keys({"top:ai": "sleep", "top:tech": "urlsleep"})
        with self.assertRaises(hand.HandError) as caught:
            top.top("k", 10, 1, 3)
        self.assertEqual(caught.exception.detail["reason"], "timeout")
        self.assertEqual({c["timeout"] for c in self.calls()}, {1})

    def test_a_request_carries_nothing_of_this_environment(self):
        os.environ["TREND_TEST_LEAK"] = "a key"
        os.environ["OPENROUTER_API_KEY"] = "sk-a-key"
        top.top("k", 10, 1, 3)
        self.assertTrue(self.calls())
        for call in self.calls():
            self.assertEqual(sorted(call["headers"]), sorted(
                ["User-agent", "Accept"] + (["Content-type"] if call["body"] else [])))
            self.assertEqual(call["headers"]["User-agent"], digg.USER_AGENT)
            self.assertNotIn("a key", json.dumps(call))
            self.assertNotIn("sk-a-key", json.dumps(call))
            self.assertTrue(call["url"].startswith("https://digg.com/"))

    def test_a_title_or_post_carrying_a_slur_is_dropped_and_counted(self):
        self.board("ai", row(1, "ai", 500, "founder calls his rivals retards on stage"), row(2, "ai", 90))
        self.board("tech", row(4, "tech", 400))
        self.sources(4, post("Ana", "what a bunch of subhuman idiots"), post("Bo", "a fine post"))
        got = top.top("k", 10, 1, 3)
        self.assertEqual([s["id"] for s in got["stories"]], ["s4", "s2"])
        self.assertEqual([p["text"] for p in got["stories"][0]["posts"]], ["a fine post"])
        self.assertEqual(got["dropped_by_screen"], 2)

    def test_a_profane_violent_or_sexual_story_now_reaches_the_studio(self):
        # 2026-11-22: the whole lexicon used to screen the news, and the
        # studio's material came through thin. Only a slur drops a story now.
        self.board("ai", row(1, "ai", 500, "porn site sues the AI company that scraped it"),
                   row(2, "ai", 90, "the fucking robot vacuum war is here"))
        self.board("tech", row(4, "tech", 400, "how a gun maker's app leaked every buyer"))
        self.sources(1, post("Ana", "this is the most sexual press release i have ever read"),
                     post("Bo", "kill the whole product line"))
        got = top.top("k", 10, 1, 3)
        self.assertEqual([s["id"] for s in got["stories"]], ["s1", "s4", "s2"])
        self.assertEqual(len(got["stories"][0]["posts"]), 2)
        self.assertEqual(got["dropped_by_screen"], 0)

    def test_an_x_post_keeps_its_counts_for_the_studios_heat(self):
        got = top.post_row({**post("Ana", "big news", n=3), "likes": 120, "retweets": 30, "viewCount": 9000,
                            "reposts": True})
        self.assertEqual((got["likes"], got["reposts"], got["views"]), (120, 30, 9000))
        self.assertNotIn("likes", top.post_row(post("Bo", "no counts")))


class StoryTest(Scratch):
    def test_one_story_every_kind_of_source(self):
        self.sources(7, post("Ana", "on x"), post("Page", "a page", kind="webpage"))
        got = top.story(cluster(7), 10)
        self.assertEqual(got["story"]["id"], "s7")
        self.assertEqual([p["kind"] for p in got["posts"]], ["x", "webpage"])

    def test_a_story_is_named_by_its_uuid(self):
        with self.assertRaises(hand.HandError):
            top.story("s7", 10)
        self.assertEqual(self.calls(), [])

    def test_the_pattern_is_digg_s_cluster_id(self):
        self.assertTrue(digg.CLUSTER.match("2febb0c8-0d89-4403-a0d6-9cbc315abcbc"))
        self.assertFalse(digg.CLUSTER.match("1skishvz"))


class CacheRaceTest(Scratch):
    def test_two_reads_keeping_one_slot_at_once_both_succeed(self):
        import threading

        errors: list[BaseException] = []

        def keep(n: int) -> None:
            try:
                for i in range(40):
                    top._keep("2026-11-18:morning", {"stories": [{"id": f"{n}-{i}"}]})
            except BaseException as exc:  # noqa: BLE001 - the test collects every failure
                errors.append(exc)

        threads = [threading.Thread(target=keep, args=(n,)) for n in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        self.assertEqual(sorted(p.suffix for p in (self.root / "state" / "trend" / "top").iterdir()), [".json"])
