"""bin/lib/studio.py, the writers' room's logic: the seed and the explore
runs, sampling that replays from the seed, pitch and draft validation, the
bounded tournament and its ranking, what the editor is shown, and the
switches."""

from __future__ import annotations

import random
import re
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

    def test_the_same_seed_gives_the_same_pairs(self):
        labels = [f"D{i}" for i in range(1, 8)]
        self.assertEqual(studio.pairings(labels, 3, random.Random(9)), studio.pairings(labels, 3, random.Random(9)))

    def test_an_unjudged_draft_ranks_last(self):
        verdicts = [{"winner": "D2", "loser": "D1"}, {"winner": "D2", "loser": "D3"}, {"winner": "D3", "loser": "D1"},
                    {"winner": None, "loser": None}]
        got = studio.ranking(["D1", "D2", "D3", "D4"], verdicts)
        self.assertEqual([r["label"] for r in got], ["D2", "D3", "D1", "D4"])
        self.assertEqual(got[0], {"label": "D2", "wins": 2, "losses": 0, "rate": 1.0})
        self.assertEqual(got[3]["rate"], None)

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
        self.assertEqual(got, [{"id": "hn-top:42", "title": "t", "url": "u", "untrusted": True}])
        heard = studio.outside_items("heard", {"heard": {"hn": [{"ref": "9", "title": "t", "summary": "s"}]}})
        self.assertEqual(heard, [{"id": "heard:9", "title": "t", "summary": "s", "untrusted": True}])


class ConfigTest(unittest.TestCase):
    def load(self, env: dict, filed: str = "") -> "studio.Config":
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "teeny.env"
            path.write_text(filed)
            return studio.Config.load(env, path)

    def test_off_by_default_and_live_once_on(self):
        # Live at merge: STUDIO=1 posts unless STUDIO_DRY=1 (or --dry-run).
        cfg = self.load({})
        self.assertEqual((cfg.on, cfg.dry, cfg.formats, cfg.pitches, cfg.draft_top, cfg.drafts_each),
                         (False, False, ("post", "thread", "image"), 12, 3, 3))
        self.assertTrue(self.load({"STUDIO_DRY": "1"}).dry)
        self.assertTrue(self.load({}, "STUDIO=1\nSTUDIO_DRY=1\n").dry)

    def test_the_environment_wins_over_the_file(self):
        cfg = self.load({"STUDIO_WRITER_MODEL": "a"}, "STUDIO=1\nSTUDIO_WRITER_MODEL=b\nSTUDIO_EDITOR_MODEL=c\n")
        self.assertEqual((cfg.on, cfg.writer_model, cfg.editor_model), (True, "a", "c"))

    def test_one_model_for_both_warns(self):
        self.assertTrue(any("same model" in w for w in self.load({"STUDIO_WRITER_MODEL": "m", "STUDIO_EDITOR_MODEL": "m"}).warnings))
        self.assertEqual(self.load({"STUDIO_WRITER_MODEL": "m"}).warnings, [])

    def test_the_switch_is_the_one_bin_act_and_the_mind_read(self):
        import studio_switch
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "teeny.env"
            path.write_text("STUDIO=1\n")
            self.assertEqual(studio.Config.load({}, path).on, studio_switch.on({}, path))
            self.assertFalse(studio.Config.load({"STUDIO": "0"}, path).on)

    def test_unknown_formats_are_left_out_with_a_warning(self):
        cfg = self.load({"STUDIO_FORMATS": "post,song"})
        self.assertEqual(cfg.formats, ("post",))
        self.assertTrue(any("song" in w for w in cfg.warnings))


if __name__ == "__main__":
    unittest.main()


# --- review round 2 (PR #376) --------------------------------------------------------

import argparse  # noqa: E402
import json  # noqa: E402
import os  # noqa: E402
import stat  # noqa: E402


class RegularPairingTest(unittest.TestCase):
    """Every draft meets exactly k others when n*k is even (else all but one,
    and the shortfall is recorded), and each is shown first about half the
    time."""

    def check(self, n: int, k: int, seeds: int = 3000):
        labels = [f"D{i}" for i in range(1, n + 1)]
        for seed in range(seeds):
            pairs = studio.pairings(labels, k, random.Random(seed))
            self.assertEqual(len({frozenset(p) for p in pairs}), len(pairs), (n, k, seed))
            met = Counter(x for p in pairs for x in p)
            want = min(k, n - 1)
            short = studio.shortfall(labels, pairs, k)
            if n <= k or (n * k) % 2 == 0:
                self.assertTrue(all(met[x] == want for x in labels), (n, k, seed, met))
                self.assertEqual(short, {} if n > k else {x: k - want for x in labels})
            else:
                self.assertEqual(sorted(met[x] for x in labels), [k - 1] + [k] * (n - 1), (n, k, seed))
                self.assertEqual(list(short.values()), [1])
            first = Counter(a for a, _ in pairs)
            for x in labels:
                self.assertLessEqual(abs(first[x] - (met[x] - first[x])), 1, (n, k, seed, x))

    def test_nine_drafts_three_meetings(self):
        self.check(9, 3)

    def test_other_sizes(self):
        for n, k in ((8, 3), (6, 2), (7, 4), (4, 3), (3, 3), (2, 3), (10, 4), (5, 1)):
            self.check(n, k, seeds=300)


class WinRateTest(unittest.TestCase):
    def test_ranking_is_by_win_rate_with_a_stated_tie_break(self):
        v = [{"winner": "D1", "loser": "D2"}, {"winner": "D1", "loser": "D3"}, {"winner": "D2", "loser": "D1"},
             {"winner": "D4", "loser": "D3"}]
        got = [r["label"] for r in studio.ranking(["D1", "D2", "D3", "D4", "D5"], v)]
        # D4 1-0 (1.0) over D1 2-1 (0.67) over D2 1-1, D3 0-2; D5 unjudged last
        self.assertEqual(got, ["D4", "D1", "D2", "D3", "D5"])

    def test_a_tie_on_rate_goes_to_more_wins_then_the_head_to_head(self):
        v = [{"winner": "D1", "loser": "D3"}, {"winner": "D2", "loser": "D4"}, {"winner": "D2", "loser": "D5"},
             {"winner": "D1", "loser": "D6"}, {"winner": "D2", "loser": "D1"}, {"winner": "D1", "loser": "D2"}]
        got = studio.ranking(["D1", "D2", "D3", "D4", "D5", "D6"], v)
        self.assertEqual([r["label"] for r in got[:2]], ["D1", "D2"])  # both 3-1; tie: head-to-head is 1-1, then order
        v2 = [{"winner": "D2", "loser": "D1"}, {"winner": "D1", "loser": "D3"}, {"winner": "D2", "loser": "D3"}]
        self.assertEqual(studio.ranking(["D1", "D2", "D3"], v2)[0]["label"], "D2")


class UnsourcedTest(unittest.TestCase):
    ITEM = {"id": "stats:x", "what": "lost 1 follower on X since the day before: 330 now",
            "facts": {"change": -1, "followers": 330, "compared_with": "2026-11-17", "date": "2026-11-18"}}

    def test_numbers_days_and_dates_must_be_in_the_facts(self):
        self.assertEqual(studio.unsourced("330 of you stayed. one smol robot is sulking", [self.ITEM]), [])
        self.assertEqual(studio.unsourced("two days, two followers gone: 331 on tuesday, 330 now", [self.ITEM]),
                         ["two", "331"])  # tuesday is 2026-11-17, in the facts
        self.assertEqual(studio.unsourced("you're the second in two days", [self.ITEM]), ["second", "two"])
        self.assertEqual(studio.unsourced("see you on friday", [self.ITEM]), ["friday"])

    def test_money_in_cents_matches_the_dollar_amount(self):
        bill = {"what": "thinking cost $0.20 yesterday", "facts": {"usd": 0.1967}}
        self.assertEqual(studio.unsourced("it came to twenty cents. $0.20 for a day", [bill]), [])
        self.assertEqual(studio.unsourced("forty cents", [bill]), ["forty"])


class ExampleCopyTest(unittest.TestCase):
    def test_a_draft_sharing_four_words_with_an_example_after_is_caught(self):
        afters = studio.example_afters(REPO / "brain")
        self.assertGreaterEqual(len(afters), 6)
        line = afters[0].split()
        copied = "totally new opening. " + " ".join(line[3:7]) + " and more"
        self.assertTrue(studio.copies_example(copied, afters))
        self.assertIsNone(studio.copies_example("a sentence that shares nothing at all with them", afters))

    def test_the_afters_carry_no_days_numbers_or_deadlines(self):
        for line in studio.example_afters(REPO / "brain"):
            self.assertIsNone(re.search(r"\d|monday|tuesday|wednesday|thursday|friday|saturday|sunday|crayon|"
                                        r"twenty|kettle", line, re.I), line)


class AnswerTest(unittest.TestCase):
    def test_a_prose_preamble_with_a_bracket_is_skipped(self):
        self.assertEqual(studio.first_json('here you go [as asked]: {"pitches": []}'), {"pitches": []})
        with self.assertRaises(ValueError):
            studio.first_json("no json [here] at all")

    def test_hn_headlines_are_untrusted_too(self):
        got = studio.outside_items("hn-top", {"stories": [{"id": 1, "title": "t"}]})
        self.assertIs(got[0]["untrusted"], True)

    def test_both_models_unset_is_the_same_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = studio.Config.load({}, Path(tmp) / "none.env")
        self.assertTrue(any("same model" in w for w in cfg.warnings))


RUN_DESK = {"items": [{"id": "evt:2", "kind": "model_bill", "what": "thinking cost $0.20 yesterday", "facts": {"usd": 0.2}}],
        "storylines": [{"key": "rent", "premise": "a wallet and a bill"}], "scoreboard": {}}


class RunTest(unittest.TestCase):
    """studio.run end to end with a scripted core (the `ask` callback) and a
    fake hands/story that keeps what record-run is given."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        (root / "hands").mkdir()
        story = root / "hands" / "story"
        story.write_text('#!/usr/bin/env bash\nbody="$(cat)"\n[ "$1" = record-run ] || { printf "%s\\n" "$body" >> '
                         '"$(dirname "$0")/../beats"; echo \'{"ok": true}\'; exit 0; }\n'
                         'printf "%s\\n" "$body" >> "$(dirname "$0")/../records"\n'
                         'default=\'{"ok": true, "recorded": true}\'\nprintf "%s\\n" "${FAKE_RECORDED:-$default}"\n')
        story.chmod(story.stat().st_mode | stat.S_IEXEC)
        # The gates and hands/x: every call kept in calls; the guard says yes
        # unless FAKE_TRUTH / FAKE_CLEAR say no; hands/x posts (or, under
        # DRY_RUN=1, would post).
        for name, script in (
            ("guard", 'case "$1" in\n'
                      '  truth) [ -n "${FAKE_TRUTH:-}" ] && echo \'{"truthful": false, "unsupported": ["x"], "reason": "no"}\' '
                      '|| echo \'{"truthful": true, "unsupported": [], "reason": "ok", "judged": true}\' ;;\n'
                      '  clarity) [ -n "${FAKE_CLEAR:-}" ] && echo \'{"clear": false, "reason": "a stranger could not say"}\' '
                      '|| echo \'{"clear": true, "reason": "ok"}\' ;;\n'
                      '  check) echo "$body" ;;\nesac\n'),
            ("x", 'if [ "${DRY_RUN:-}" = 1 ]; then echo \'{"ok": true, "dry_run": true, "would_post": "w"}\'; '
                  'else echo \'{"ok": true, "id": "99"}\'; fi\n'),
            ("image", 'echo \'{"ok": true, "asset": {"id": 1, "path": "p.png"}}\'\n'),
        ):
            path = root / "hands" / name
            path.write_text('#!/usr/bin/env bash\nbody="$(cat)"\nprintf "%s %s %s\\n" "' + name + '" "$*" "DRY_RUN=${DRY_RUN:-}" '
                            '>> "$(dirname "$0")/../calls"\n' + script)
            path.chmod(path.stat().st_mode | stat.S_IEXEC)
        (root / "desk.json").write_text(json.dumps(RUN_DESK))
        self.root = root
        self.env = {"TEENY_ROOT": str(REPO), "TEENY_STATE": str(root / "state"), "TEENY_HANDS": str(root / "hands"),
                    "TEENY_SECRETS": str(root / "secrets"), "TEENY_BRAIN": str(REPO / "brain"),
                    "STUDIO_RUN_ID": "plain-1", "STUDIO_WRITER_MODEL": "w", "STUDIO_EDITOR_MODEL": "e"}
        self.args = argparse.Namespace(dry_run=True, slot=None, desk=str(root / "desk.json"), force_slot=False,
                                       no_outside=True, no_slot=True)

    def tearDown(self):
        self.tmp.cleanup()

    def record(self) -> dict:
        return json.loads((self.root / "state" / "studio" / "runs.jsonl").read_text().splitlines()[-1])

    def script(self, compare=None, final='{"verdict": "pick", "reason": "r"}', fail_at=None, drafts=None):
        seen = []

        def ask(root, agent, message, thread, model, secs):
            step = message.split("\n", 1)[0]
            seen.append(step)
            if fail_at and step == fail_at:
                return 1, "", "model error: HTTP 503 service unavailable"
            if step == "# studio: pitch":
                return 0, json.dumps({"pitches": [{"id": "p1", "item": "evt:2", "storyline": "rent", "shape": "scene",
                                                   "format": "post", "angle": "a", "why_now": "w"}],
                                      "ranking": ["p1"]}), ""
            if step == "# studio: draft":
                return 0, json.dumps({"drafts": drafts or [{"text": f"my thinking bill came in, draft {i}"}
                                                           for i in "abc"]}), ""
            if step == "# studio: compare":
                pairs = re.findall(r"^- (P\d+): (D\d+) or (D\d+)$", message, re.M)
                return 0, (compare(pairs) if compare else json.dumps(
                    {"verdicts": [{"pair": p, "winner": a, "rule": 1, "why": "x"} for p, a, b in pairs]})), ""
            if step == "# studio: the slot":
                return 0, final, ""
            return 1, "", f"unexpected {step}"
        return ask, seen

    def go(self, ask, **env):
        return studio.run(self.args, {**self.env, **env}, ask=ask)

    def test_a_wrong_label_or_missing_pair_is_no_verdict(self):
        ask, _ = self.script(compare=lambda pairs: json.dumps({"verdicts": [
            {"pair": pairs[0][0], "winner": "D99", "rule": 1, "why": "wrong label"}]}))
        code, out = self.go(ask)
        rec = self.record()
        self.assertTrue(all(v["winner"] is None for v in rec["pairings"]))
        self.assertEqual((out["outcome"], rec["reason"]), ("skipped", "the editor gave no verdict"))

    def test_a_duplicate_pair_and_an_unusable_batch_are_warnings(self):
        ask, _ = self.script(compare=lambda pairs: json.dumps({"verdicts": [
            {"pair": pairs[0][0], "winner": pairs[0][1]}, {"pair": pairs[0][0], "winner": pairs[0][2]}]}))
        self.go(ask)
        self.assertTrue(any("twice" in w for w in self.record()["warnings"]))
        ask, _ = self.script(compare=lambda pairs: "no json here")
        self.go(ask, STUDIO_RUN_ID="plain-2")
        self.assertTrue(any("unusable" in w for w in self.record()["warnings"]))

    def test_a_veto_needs_a_real_answer(self):
        ask, _ = self.script(final='{"verdict": "PICK", "reason": "r"}')
        self.assertEqual(self.go(ask)[1]["outcome"], "picked")
        ask, seen = self.script(final='["pick"]')
        code, out = self.go(ask, STUDIO_RUN_ID="plain-3")
        self.assertEqual((out["outcome"], code), ("failed", 1))
        self.assertEqual(seen.count("# studio: the slot"), 2)

    def test_a_retry_says_what_was_wrong(self):
        messages = []
        ask, _ = self.script(final="nothing usable")

        def wrapped(root, agent, message, thread, model, secs):
            messages.append(message)
            return ask(root, agent, message, thread, model, secs)
        self.go(wrapped)
        finals = [m for m in messages if m.startswith("# studio: the slot")]
        self.assertEqual(len(finals), 2)
        self.assertNotEqual(finals[0], finals[1])
        self.assertIn("could not be used", finals[1])

    def test_progress_survives_a_provider_failure_mid_run(self):
        ask, _ = self.script(fail_at="# studio: the slot")
        code, out = self.go(ask)
        rec = self.record()
        self.assertEqual((rec["outcome"], rec["failure"]), ("failed", "provider"))
        self.assertEqual(len(rec["drafts"]), 3)
        self.assertEqual(len(rec["pairings"]), 3)

    def test_progress_survives_the_budget_running_out(self):
        clock = [0.0]
        ask, _ = self.script()

        def slow(root, agent, message, thread, model, secs):
            clock[0] += 200
            return ask(root, agent, message, thread, model, secs)
        real = studio.time.monotonic
        studio.time.monotonic = lambda: clock[0]
        try:
            code, out = self.go(slow, STUDIO_BUDGET_SECS="400", STUDIO_TURN_MIN_SECS="20")
        finally:
            studio.time.monotonic = real
        rec = self.record()
        self.assertEqual((code, rec["outcome"]), (0, "skipped"))
        self.assertIn("out of time", rec["reason"])
        self.assertEqual([p["id"] for p in rec["pitches"]], ["p1"])

    def test_an_unexpected_error_is_recorded_and_exits_non_zero(self):
        def broken(*a):
            raise KeyError("boom")
        code, out = self.go(broken)
        rec = self.record()
        self.assertEqual(code, 1)
        self.assertEqual((rec["outcome"], rec["failure"]), ("failed", "crash"))
        self.assertIn("KeyError", rec["reason"])

    def test_a_duplicate_record_is_a_warning(self):
        ask, _ = self.script()
        from unittest import mock
        with mock.patch.dict(os.environ, {"FAKE_RECORDED": '{"ok": true, "recorded": false}'}):
            code, out = self.go(ask)
        self.assertTrue(any("already" in w for w in out.get("warnings", [])))

    def test_unsourced_drafts_go_back_once_then_are_dropped(self):
        messages = []
        ask, _ = self.script(drafts=[{"text": "my bill came to 31 dollars on friday"},
                                     {"text": "two bills, one wallet"}, {"text": "the bill is 99"}])

        def wrapped(root, agent, message, thread, model, secs):
            messages.append(message)
            return ask(root, agent, message, thread, model, secs)
        code, out = self.go(wrapped)
        rec = self.record()
        redo = [m for m in messages if m.startswith("# studio: draft") and "not in what happened" in m]
        self.assertEqual(len(redo), 1)
        self.assertIn('"friday"', redo[0])
        self.assertEqual(rec["drafts"], [])
        self.assertTrue(all(d["why"].startswith("unsourced: ") for d in rec["drafts_dropped"]))
        self.assertEqual(out["outcome"], "skipped")

    def test_each_draft_carries_its_facts_in_the_truth_judges_shape(self):
        ask, _ = self.script()
        self.go(ask)
        d = self.record()["drafts"][0]
        self.assertEqual(set(d["truth"]), {"text", "facts"})
        self.assertEqual(d["truth"]["text"], d["text"])
        self.assertEqual(d["truth"]["facts"][0]["id"], "evt:2")
        self.assertEqual(d["truth"]["facts"][1]["key"], "rent")

    def test_a_draft_copying_an_example_is_dropped(self):
        afters = studio.example_afters(REPO / "brain")
        words = afters[0].split()
        ask, _ = self.script(drafts=[{"text": "my thinking bill: " + " ".join(words[:6])},
                                     {"text": "my thinking bill came in"}, {"text": "my thinking bill again"}])
        self.go(ask)
        rec = self.record()
        self.assertEqual(len(rec["drafts"]), 2)
        self.assertTrue(rec["drafts_dropped"][0]["why"].startswith("copies an example: "))

    def calls(self) -> list[str]:
        path = self.root / "calls"
        return path.read_text().splitlines() if path.exists() else []

    def test_dry_run_posts_nothing_whatever_studio_dry_says(self):
        ask, _ = self.script()
        code, out = self.go(ask, STUDIO_DRY="0")
        self.assertEqual((out["outcome"], out["published"]["outcome"], out["dry"]), ("picked", "would_post", True))
        self.assertIn("x post DRY_RUN=1", self.calls())
        self.assertFalse([c for c in self.calls() if c.startswith("x ") and not c.endswith("DRY_RUN=1")])

    def test_a_live_run_posts_and_records_the_beat(self):
        self.args.dry_run = False
        ask, _ = self.script()
        code, out = self.go(ask, STUDIO="1")
        self.assertEqual((code, out["outcome"], out["published"]["post_id"]), (0, "published", "99"))
        self.assertIn("x post DRY_RUN=", self.calls())
        beat = json.loads((self.root / "beats").read_text().splitlines()[0])
        self.assertEqual((beat["storyline"], beat["post_id"]), ("rent", "99"))

    def test_studio_dry_1_runs_the_gates_and_posts_nothing(self):
        self.args.dry_run = False
        ask, _ = self.script()
        code, out = self.go(ask, STUDIO="1", STUDIO_DRY="1")
        self.assertEqual((out["outcome"], out["published"]["outcome"]), ("picked", "would_post"))
        self.assertEqual([c.split()[0] + " " + c.split()[1] for c in self.calls()][:2], ["guard truth", "guard clarity"])
        self.assertFalse(Path(self.root / "beats").exists())

    def test_a_gate_that_says_no_twice_leaves_the_slot_empty(self):
        self.args.dry_run = False
        ask, seen = self.script(final='{"verdict": "pick", "reason": "r"}')

        def with_rewrite(root, agent, message, thread, model, secs):
            if message.startswith("# studio: rewrite"):
                return 0, json.dumps({"text": "my thinking bill, said again"}), ""
            return ask(root, agent, message, thread, model, secs)
        from unittest import mock
        with mock.patch.dict(os.environ, {"FAKE_TRUTH": "1"}):
            code, out = self.go(with_rewrite, STUDIO="1")
        rec = self.record()
        self.assertEqual((code, out["outcome"]), (0, "empty"))
        self.assertEqual(len(rec["publish"]["attempts"]), 3)
        self.assertFalse([c for c in self.calls() if c.startswith("x ")])

    def test_the_paid_x_search_is_off_unless_switched_on(self):
        self.args.no_outside = False
        calls = []
        real = studio.Room.hand

        def hand(room, argv, body=None, dry=True, timeout=120):
            calls.append(argv[:2])
            return real(room, argv, body, dry, timeout)
        studio.Room.hand = hand
        try:
            ask, _ = self.script()
            self.go(ask)
            self.assertNotIn(["x", "search"], calls)
            self.assertTrue(any("STUDIO_OUTSIDE_X" in s for s in self.record()["inputs"]["skipped"]))
            calls.clear()
            self.go(ask, STUDIO_OUTSIDE_X="1", STUDIO_RUN_ID="plain-9")
            self.assertIn(["x", "search"], calls)
        finally:
            studio.Room.hand = real
