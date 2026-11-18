"""bin/lib/studio.py, the writers' room's logic: the seed and the explore
runs, sampling that replays from the seed, pitch and draft validation, the
bounded tournament and its ranking, what the editor is shown, and the
switches."""

from __future__ import annotations

import random
import sys
import tempfile
import unittest
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "bin" / "lib"))
import studio  # noqa: E402

DESK = {
    "items": [{"id": "a:1", "what": "x"}],
    "storylines": [{"key": "rent", "beats": [{"shape": "confession", "format": "post"}]}],
    "scoreboard": {"formats": [{"format": "(post)"}, {"format": "take"}],
                   "best": [{"id": f"b{i}", "format": "take"} for i in range(5)],
                   "worst": [{"id": f"w{i}", "format": "proof"} for i in range(5)]},
}


class SeedTest(unittest.TestCase):
    def test_the_seed_comes_from_the_run_id(self):
        self.assertEqual(studio.seed_of("r-1"), studio.seed_of("r-1"))
        self.assertNotEqual(studio.seed_of("r-1"), studio.seed_of("r-2"))

    def test_one_seed_in_four_explores(self):
        share = sum(studio.is_explore(studio.seed_of(f"r-{i}")) for i in range(4000)) / 4000
        self.assertAlmostEqual(share, 0.25, delta=0.03)
        self.assertTrue(studio.is_explore(8))
        self.assertFalse(studio.is_explore(9))

    def test_sampling_replays_from_the_seed(self):
        with tempfile.TemporaryDirectory() as tmp:
            brain = Path(tmp)
            (brain / "narrative").mkdir()
            (brain / "narrative" / "LORE.md").write_text(
                "# lore\n\nnote.\n\n## cast\n\n- a\n- b\n- c\n- d\n\n## running bits\n\n- e\n- f\n")
            one = studio.sample(random.Random(5), 5, DESK, brain, ("post", "thread"))
            two = studio.sample(random.Random(5), 5, DESK, brain, ("post", "thread"))
            self.assertEqual(one, two)
            self.assertEqual(len(one["lore"]), 3)
            self.assertTrue(set(one["lore"]) <= {"a", "b", "c", "d", "e", "f"})
            self.assertEqual((len(one["pairs"]), len(one["best"]), len(one["worst"])), (3, 3, 2))

    def test_an_explore_run_asks_for_something_untried(self):
        with tempfile.TemporaryDirectory() as tmp:
            got = studio.sample(random.Random(1), 8, DESK, Path(tmp), ("post", "thread", "image"))
        self.assertTrue(got["explore"])
        # Neither the beat's confession and post, nor the scoreboard's take.
        self.assertNotIn(got["explore_with"], ("confession", "post", "take", "proof"))
        self.assertIn(got["explore_with"], (*studio.SHAPES, "thread", "image"))


class ValidateTest(unittest.TestCase):
    def test_pitches_on_unknown_items_or_formats_are_dropped_with_why(self):
        answer = {"pitches": [
            {"id": "p1", "item": "a:1", "format": "post", "angle": "ok", "storyline": "rent"},
            {"id": "p2", "item": "nope", "format": "post", "angle": "x"},
            {"id": "p3", "item": "a:1", "format": "song", "angle": "x"},
            {"id": "p1", "item": "a:1", "format": "post", "angle": "twice"},
            {"id": "p5", "item": "a:1", "format": "post", "angle": ""},
            {"id": "p6", "item": "a:1", "format": "thread", "angle": "y", "storyline": "made-up"},
        ], "ranking": ["p6", "ghost", "p6"]}
        kept, dropped, ranked = studio.validate_pitches(answer, {"a:1"}, {"rent"}, ("post", "thread"), 12)
        self.assertEqual([p["id"] for p in kept], ["p1", "p6"])
        self.assertEqual(kept[1]["storyline"], None)
        whys = [d["why"] for d in dropped]
        self.assertIn("its item 'nope' is not in the inputs", whys)
        self.assertTrue(any("format 'song'" in w for w in whys))
        self.assertTrue(any("used twice" in w for w in whys))
        self.assertIn("it has no angle", whys)
        self.assertEqual(ranked, ["p6", "p1"])

    def test_pitches_over_the_limit_are_dropped(self):
        answer = [{"id": f"p{i}", "item": "a:1", "format": "post", "angle": "a"} for i in range(5)]
        kept, dropped, _ = studio.validate_pitches(answer, {"a:1"}, set(), ("post",), 3)
        self.assertEqual((len(kept), len(dropped)), (3, 2))

    def test_an_answer_with_no_pitches_list_is_unusable(self):
        with self.assertRaises(ValueError):
            studio.validate_pitches({"angles": []}, set(), set(), ("post",), 3)

    def test_drafts_follow_their_pitch_format(self):
        thread = {"id": "p1", "storyline": None, "shape": "bet", "format": "thread"}
        got, lost = studio.validate_drafts({"drafts": [{"posts": ["a", "b"]}, {"posts": ["only one"]}, {"text": "t"}]},
                                           thread, 3)
        self.assertEqual([d["posts"] for d in got], [["a", "b"]])
        self.assertEqual(len(lost), 2)
        image = {"id": "p2", "storyline": "rent", "shape": "scene", "format": "image"}
        got, lost = studio.validate_drafts([{"text": "t", "image": "on a bus"}, {"text": "no scene"}], image, 3)
        self.assertEqual((got[0]["image"], got[0]["storyline"]), ("on a bus", "rent"))
        self.assertEqual(lost[0]["why"], "an image piece needs a scene")

    def test_the_limit_is_the_guards_own_count(self):
        self.assertEqual(studio.over_limit({"text": "x" * 280}), "")
        self.assertEqual(studio.over_limit({"text": "x" * 281}), "it is 281 characters, over the 280 limit")
        self.assertEqual(studio.over_limit({"posts": ["ok", "y" * 300]}), "post 2 is 300 characters, over the 280 limit")


class TournamentTest(unittest.TestCase):
    def test_each_draft_meets_at_most_three_others_each_pair_once(self):
        labels = [f"D{i}" for i in range(1, 10)]
        for seed in range(50):
            pairs = studio.pairings(labels, 3, random.Random(seed))
            met = Counter(x for p in pairs for x in p)
            self.assertTrue(all(n <= 3 for n in met.values()), seed)
            self.assertEqual(len({frozenset(p) for p in pairs}), len(pairs), seed)
            self.assertGreaterEqual(len(pairs), 11, seed)

    def test_the_order_in_a_pair_alternates(self):
        pairs = studio.pairings(["D1", "D2", "D3", "D4"], 3, random.Random(3))
        every = [(a, b) for i, a in enumerate(["D1", "D2", "D3", "D4"]) for b in ["D1", "D2", "D3", "D4"][i + 1:]]
        rng = random.Random(3)
        rng.shuffle(every)
        self.assertEqual(pairs[0], every[0])
        self.assertEqual(pairs[1], every[1][::-1])

    def test_the_same_seed_gives_the_same_pairs(self):
        labels = [f"D{i}" for i in range(1, 8)]
        self.assertEqual(studio.pairings(labels, 3, random.Random(9)), studio.pairings(labels, 3, random.Random(9)))

    def test_ranking_is_by_wins_then_fewer_losses(self):
        verdicts = [{"winner": "D2", "loser": "D1"}, {"winner": "D2", "loser": "D3"}, {"winner": "D3", "loser": "D1"},
                    {"winner": None, "loser": None}]
        got = studio.ranking(["D1", "D2", "D3", "D4"], verdicts)
        self.assertEqual([r["label"] for r in got], ["D2", "D3", "D4", "D1"])
        self.assertEqual(got[0], {"label": "D2", "wins": 2, "losses": 0})

    def test_the_editor_sees_the_words_and_the_picture_only(self):
        draft = {"id": "d4", "pitch": "p2", "storyline": "rent", "shape": "bet", "format": "image",
                 "meaning": "what it means", "text": "words", "image": "on a bus"}
        self.assertEqual(studio.shown(draft), {"format": "image", "text": "words", "picture": "on a bus"})
        message = studio.judge_message([("P1", "D2", "D1")], {"D1": draft, "D2": draft}, {}, [])
        for hidden in ("d4", "p2", "what it means", "bet"):
            self.assertNotIn(hidden, message)


class OutsideTest(unittest.TestCase):
    def test_outside_items_get_ids_the_pitches_can_name(self):
        got = studio.outside_items("hn-top", {"ok": True, "stories": [{"id": 42, "title": "t", "url": "u", "time": 1}]})
        self.assertEqual(got, [{"id": "hn-top:42", "title": "t", "url": "u"}])
        heard = studio.outside_items("heard", {"heard": {"hn": [{"ref": "9", "title": "t", "summary": "s"}]}})
        self.assertEqual(heard, [{"id": "heard:9", "title": "t", "summary": "s", "untrusted": True}])


class ConfigTest(unittest.TestCase):
    def load(self, env: dict, filed: str = "") -> "studio.Config":
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "teeny.env"
            path.write_text(filed)
            return studio.Config.load(env, path)

    def test_off_and_dry_by_default(self):
        cfg = self.load({})
        self.assertEqual((cfg.on, cfg.dry, cfg.formats, cfg.pitches, cfg.draft_top, cfg.drafts_each),
                         (False, True, ("post", "thread", "image"), 12, 3, 3))

    def test_the_environment_wins_over_the_file(self):
        cfg = self.load({"STUDIO_WRITER_MODEL": "a"}, "STUDIO=1\nSTUDIO_WRITER_MODEL=b\nSTUDIO_EDITOR_MODEL=c\n")
        self.assertEqual((cfg.on, cfg.writer_model, cfg.editor_model), (True, "a", "c"))

    def test_one_model_for_both_warns(self):
        self.assertTrue(any("same model" in w for w in self.load({"STUDIO_WRITER_MODEL": "m", "STUDIO_EDITOR_MODEL": "m"}).warnings))
        self.assertEqual(self.load({"STUDIO_WRITER_MODEL": "m"}).warnings, [])

    def test_this_version_never_posts(self):
        cfg = self.load({"STUDIO_DRY": "0"})
        self.assertTrue(cfg.dry)
        self.assertTrue(any("never posts" in w for w in cfg.warnings))
        self.assertEqual(studio.publish({"text": "x"}), {"dry": True})

    def test_unknown_formats_are_left_out_with_a_warning(self):
        cfg = self.load({"STUDIO_FORMATS": "post,song"})
        self.assertEqual(cfg.formats, ("post",))
        self.assertTrue(any("song" in w for w in cfg.warnings))


if __name__ == "__main__":
    unittest.main()
