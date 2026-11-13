"""rhythm.wander: the idle look at the world (docs/architecture.md, 4a,
"Rhythm"). It takes turns between Moltbook's hot page and Hacker News (top,
show, ask), keeps its place in state/mind.json, screens every title through
the inbound guard, and falls through to the next source when one is down or
empty, so an idle look is never an empty stimulus."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE.parent / "lib"))
sys.path.insert(0, str(REPO / "bin" / "lib"))

from teeny_hands import hand, stimuli  # noqa: E402

import rhythm  # noqa: E402

T0 = 1_789_369_200.0  # 2026-09-14T07:00:00Z

MOLTBOOK = {"ok": True, "posts": [{"id": "p7", "title": "do agents dream of crabs", "author": {"name": "lobster"}}]}


def hn(feed: str, *titles: str) -> dict:
    return {"ok": True, "feed": feed, "stories": [
        {"id": 100 + n, "title": title, "by": "pg", "score": 40 + n, "descendants": 7,
         "url": f"https://example.com/{feed}/{n}"} for n, title in enumerate(titles)]}


class WanderTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "secrets").mkdir()
        self._env = dict(os.environ)
        os.environ.update(TEENY_ROOT=str(self.root), TEENY_NOW=str(T0), CALENDAR_TZ="UTC", TEENY_QUIET_HOURS="off")
        hand._ENV_CACHE = None
        self.calls: list[tuple[str, list[str], object]] = []
        self.answers = {
            ("moltbook", "feed"): (0, MOLTBOOK),
            ("hn", "top"): (0, hn("top", "A tiny agent that runs in public")),
            ("hn", "show"): (0, hn("show", "Show HN: a crab simulator")),
            ("hn", "ask"): (0, hn("ask", "Ask HN: who audits your agent?")),
        }
        self.held: set[str] = set()

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)
        hand._ENV_CACHE = None
        self.tmp.cleanup()

    def run_hand(self, name, argv, body):
        self.calls.append((name, list(argv), body))
        if name == "guard":
            if body["text"] in self.held:
                return 2, {"ok": False, "refused": "held", "category": "profanity"}
            return 0, {"ok": True}
        return self.answers[(name, argv[0])]

    def look(self, n: int) -> bool:
        """The n-th look, each just past the idle interval after the last."""
        os.environ["TEENY_NOW"] = str(T0 + n * (rhythm.IDLE_EVERY_SECS + 1))
        return rhythm.wander(self.run_hand)

    def looks(self) -> list[dict]:
        got = [s for s in stimuli.recent()["stimuli"] if (s["surface"], s["kind"]) == ("world", "idle")]
        return sorted(got, key=lambda s: s["at"])

    def reads(self) -> list[tuple[str, list[str]]]:
        return [(name, argv) for name, argv, _ in self.calls if name != "guard"]

    def test_the_idle_look_takes_turns_between_moltbook_and_hacker_news(self):
        for n in range(5):
            self.assertTrue(self.look(n))
        self.assertEqual(self.reads(), [
            ("moltbook", ["feed", "--sort", "hot", "--max", "5"]),
            ("hn", ["top", "--max", "8"]),
            ("hn", ["show", "--max", "8"]),
            ("hn", ["ask", "--max", "5"]),
            ("moltbook", ["feed", "--sort", "hot", "--max", "5"]),
        ])
        refs = [s["ref"].split(":")[0] for s in self.looks()]
        self.assertEqual(refs, ["moltbook-hot", "hn-top", "hn-show", "hn-ask", "moltbook-hot"])

    def test_a_hacker_news_look_names_each_story_and_where_it_is(self):
        self.look(0)
        self.look(1)
        look = self.looks()[1]
        self.assertIn("Hacker News", look["text"])
        self.assertIn("A tiny agent that runs in public", look["text"])
        self.assertIn("https://news.ycombinator.com/item?id=100", look["text"])
        self.assertEqual(look["payload"]["source"], "hn-top")
        self.assertEqual(look["payload"]["stories"][0]["id"], "100")

    def test_the_moltbook_look_reads_as_before(self):
        self.look(0)
        look = self.looks()[0]
        self.assertIn("do agents dream of crabs (by lobster, post_id p7)", look["text"])
        self.assertEqual(look["payload"]["posts"], [{"post_id": "p7", "title": "do agents dream of crabs", "author": "lobster"}])

    def test_its_place_in_the_rotation_is_kept_in_the_minds_state(self):
        self.look(0)
        self.assertEqual(rhythm.load()["wander_turn"], 1)
        self.look(1)
        self.assertEqual(rhythm.load()["wander_turn"], 2)

    def test_a_source_that_is_down_or_empty_falls_through_to_the_next(self):
        self.look(0)
        self.answers[("hn", "top")] = (1, {"ok": False, "error": "boom"})
        self.answers[("hn", "show")] = (0, {"ok": True, "feed": "show", "stories": []})
        self.assertTrue(self.look(1))
        self.assertEqual([s["ref"].split(":")[0] for s in self.looks()], ["moltbook-hot", "hn-ask"])
        # The next look starts after the one that worked.
        self.assertTrue(self.look(2))
        self.assertEqual(self.reads()[-1][0], "moltbook")

    def test_nothing_to_see_anywhere_adds_nothing(self):
        for key in self.answers:
            self.answers[key] = (1, {"ok": False, "error": "boom"})
        self.assertFalse(self.look(0))
        self.assertEqual(self.looks(), [])
        self.assertEqual(len(self.reads()), 4)  # each source tried once

    def test_every_title_goes_through_the_inbound_guard_on_its_surface(self):
        self.answers[("hn", "top")] = (0, hn("top", "a clean title", "a title the guard holds"))
        self.held.add("a title the guard holds")
        self.look(0)
        self.look(1)
        screened = [(body["surface"], body["text"]) for name, argv, body in self.calls
                    if name == "guard" and argv == ["inbound"]]
        self.assertEqual(screened, [("moltbook", "do agents dream of crabs"), ("hn", "a clean title"),
                                    ("hn", "a title the guard holds")])
        look = self.looks()[1]
        self.assertIn("a clean title", look["text"])
        self.assertNotIn("a title the guard holds", look["text"])

    def test_a_look_whose_every_title_is_held_falls_through(self):
        self.held.add("do agents dream of crabs")
        self.assertTrue(self.look(0))
        self.assertEqual([s["ref"].split(":")[0] for s in self.looks()], ["hn-top"])

    def test_at_most_one_look_every_two_hours(self):
        self.assertTrue(self.look(0))
        os.environ["TEENY_NOW"] = str(T0 + 3600)
        self.assertFalse(rhythm.wander(self.run_hand))
        self.assertEqual(len(self.reads()), 1)


if __name__ == "__main__":
    unittest.main()
