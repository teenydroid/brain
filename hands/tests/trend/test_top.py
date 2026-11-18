"""hands/trend top and story: the merged leaderboards, the posts behind the
leading stories, the cache a slot, and every way Digg can give nothing."""

from __future__ import annotations

import os

from .support import Scratch, cluster, post, row
from teeny_hands import hand
from teeny_hands.trend import digg, top


class TopTest(Scratch):
    def setUp(self) -> None:
        super().setUp()
        self.board("ai", row(1, "ai", 500, "an agent startup raised money"), row(2, "ai", 90), row(3, "ai", 300))
        self.board("tech", row(4, "tech", 400, "a chip maker doubled revenue"), row(3, "tech", 310))
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
        posts_calls = [c["argv"] for c in self.calls() if c["argv"][0] == "posts"]
        self.assertEqual(posts_calls, [["posts", cluster(1), "--agent", "--data-source", "live"],
                                       ["posts", cluster(4), "--agent", "--data-source", "live"]])

    def test_a_bare_list_answer_reads_as_the_envelope_does(self):
        self.behave_keys({"top:ai": "bare", "top:tech": "bare", f"posts:{cluster(1)}": "bare"})
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
        self.assertEqual(top.top(None, 10, 0, 1)["key"], "2026-11-18T07")
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
        for how, reason in (("rate", "rate_limited"), ("garbage", "bad_json"), ("fail", "failed")):
            self.behave_keys({"top:ai": how, "top:tech": how})
            with self.assertRaises(hand.HandError) as caught:
                top.top("k", 10, 1, 3)
            self.assertEqual(caught.exception.detail["reason"], reason, how)
        self.assertFalse((self.root / "state" / "trend" / "top" / "k.json").exists())

    def test_a_missing_tool_is_said_and_never_a_crash(self):
        self.configure(TREND_DIGG_BIN=str(self.root / "nowhere"))
        with self.assertRaises(hand.HandError) as caught:
            top.top("k", 10, 1, 3)
        self.assertEqual(caught.exception.detail["reason"], "missing")
        self.assertIn("not installed", str(caught.exception))

    def test_no_answer_in_time_is_a_timeout(self):
        self.configure(TREND_TIMEOUT_SECS="1")
        self.behave_keys({"top:ai": "sleep", "top:tech": "sleep"})
        with self.assertRaises(hand.HandError) as caught:
            top.top("k", 10, 1, 3)
        self.assertEqual(caught.exception.detail["reason"], "timeout")

    def test_the_tool_lives_under_state_and_sees_none_of_this_environment(self):
        os.environ["TREND_TEST_LEAK"] = "a key"
        top.top("k", 10, 1, 3)
        home = str(self.root / "state" / "trend" / "home")
        for call in self.calls():
            self.assertEqual((call["home"], call["cwd"], call["leak"]), (home, home, None))
            self.assertTrue(call["xdg"].startswith(home))

    def test_a_title_or_post_the_screen_holds_is_dropped_and_counted(self):
        self.board("ai", row(1, "ai", 500, "how to kill yourself with agents"), row(2, "ai", 90))
        self.board("tech", row(4, "tech", 400))
        self.sources(4, post("Ana", "how to kill yourself"), post("Bo", "a fine post"))
        got = top.top("k", 10, 1, 3)
        self.assertEqual([s["id"] for s in got["stories"]], ["s4", "s2"])
        self.assertEqual([p["text"] for p in got["stories"][0]["posts"]], ["a fine post"])
        self.assertEqual(got["dropped_by_screen"], 2)


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
