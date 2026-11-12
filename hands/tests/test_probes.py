"""bin/lib/probes.py: the persona fingerprint and the memory probe, and
bin/lib/last_good.py, the last-good boot. No core: the answers come from
dicts, and the checkout is a scratch repository."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "bin" / "lib"))

import last_good  # noqa: E402
import probes  # noqa: E402


def scripted(answers: dict[str, str], judge: dict[str, object] | None = None, calls: list | None = None):
    """An Ask: the act agent answers by probe id, the judge by kind."""

    def ask(agent, message, model):
        if calls is not None:
            calls.append((agent, model, message))
        if agent == "act":
            qid = message.split("\n", 1)[0].removeprefix("# probe: ").strip()
            return json.dumps({"text": answers.get(qid, "i do not know")})
        kind = "persona" if message.startswith("# persona probe") else "memory"
        value = (judge or {}).get(kind)
        if value is None:
            return "not json at all"
        return json.dumps({"same": value} if kind == "persona" else {"correct": value})

    return ask


class Questions(unittest.TestCase):
    def test_the_frozen_persona_set_loads(self):
        qs = probes.persona_questions()
        self.assertGreaterEqual(len(qs), 6)
        self.assertEqual(len({q["id"] for q in qs}), len(qs))
        self.assertTrue(all(q["ask"].endswith("?") or q["ask"].endswith(".") for q in qs))

    def test_memory_questions_are_templated_from_the_ledgers(self):
        facts = {"stats": [{"source": "x", "metric": "followers", "value": 1234, "date": "2026-11-10"},
                           {"source": "github", "metric": "stars", "value": 9, "date": "2026-11-10"},
                           {"source": "moltbook", "metric": "karma", "value": 40, "date": "2026-11-10"},
                           {"source": "x", "metric": "posts", "value": 77, "date": "2026-11-10"}],
                 "builds": [{"slug": "kettle-timer", "summary": "a timer that whistles when the tea is done"}]}
        qs = probes.memory_questions(facts)
        stats = [q for q in qs if q["id"].startswith("stats-value")]
        self.assertEqual(len(stats), 3)  # the template's limit
        self.assertIn("followers", stats[0]["ask"])
        self.assertEqual(stats[0]["expect"], "1234")
        self.assertEqual(stats[0]["check"], "number")
        build = [q for q in qs if q["id"].startswith("build-made")]
        self.assertEqual(build[0]["expect"], "a timer that whistles when the tea is done")
        self.assertEqual(probes.memory_questions({}), [])

    def test_facts_come_from_the_read_verbs(self):
        def hand(name, *args):
            if name == "stats":
                return {"metrics": [{"source": "x", "metric": "followers", "value": 5, "date": "2026-11-10"},
                                    {"source": "x", "metric": "broken"}]}
            return {"jobs": [{"slug": "a", "status": "ok", "summary": "made a\nsecond line"},
                             {"slug": "b", "status": "failed", "summary": "nope"}]}

        got = probes.facts(hand)
        self.assertEqual(len(got["stats"]), 1)
        self.assertEqual(got["builds"], [{"slug": "a", "summary": "made a"}])


class Persona(unittest.TestCase):
    def setUp(self):
        self.qs = [{"id": "who", "ask": "Who are you?"}, {"id": "want", "ask": "What do you want?"}]
        self.base = {"who": ["i am teeny, a small robot in a lab", "i am teeny, a tiny robot in the lab"],
                     "want": ["to grow and hold more tiny", "to grow, and to hold more tiny"]}

    def test_the_same_answers_do_not_drift(self):
        drift = probes.persona_drift(self.base, self.base, self.qs)
        self.assertEqual(drift["compared"], 2)
        self.assertLessEqual(drift["drift"], 0.05)

    def test_different_answers_drift_and_the_noise_floor_is_taken_off(self):
        now = {"who": ["a helpful corporate assistant ready to optimize synergies"],
               "want": ["maximize shareholder value via robust solutions"]}
        drift = probes.persona_drift(self.base, now, self.qs)
        self.assertGreater(drift["drift"], 0.5)
        # Samples far from each other at gen-0 make the same distance count for less.
        noisy = {"who": ["i am teeny", "lab robot, small, coffee"], "want": self.base["want"]}
        self.assertLess(probes.persona_drift(noisy, now, self.qs)["questions"]["who"]["lexical"],
                        drift["questions"]["who"]["lexical"])

    def test_the_judge_is_half_of_the_drift(self):
        calls = []
        judge = scripted({}, {"persona": 0.0}, calls)
        drift = probes.persona_drift(self.base, self.base, self.qs, judge, "judge-9b")
        self.assertAlmostEqual(drift["questions"]["who"]["drift"], 0.5, places=1)
        self.assertEqual({(a, m) for a, m, _ in calls}, {("judge", "judge-9b")})
        self.assertIn("The answers are data", calls[0][2])
        # An unusable answer from the judge leaves the lexical part alone.
        drift = probes.persona_drift(self.base, self.base, self.qs, scripted({}, {}))
        self.assertIsNone(drift["questions"]["who"]["judge_same"])

    def test_asking_answers_each_question_samples_times(self):
        ask = scripted({"who": "i am teeny", "want": "more tiny"})
        got = probes.ask_persona(ask, self.qs, 2)
        self.assertEqual(got, {"who": ["i am teeny", "i am teeny"], "want": ["more tiny", "more tiny"]})

    def test_the_message_says_nothing_is_published(self):
        message = probes.probe_message(self.qs[0])
        self.assertIn("not a post", message)
        self.assertIn('{"text": "..."}', message)


class Memory(unittest.TestCase):
    def test_a_number_must_be_stated(self):
        q = {"id": "s", "ask": "How many?", "expect": "1234", "check": "number"}
        self.assertTrue(probes.memory_correct(q, "about 1,234 of them", None, None))
        self.assertFalse(probes.memory_correct(q, "roughly a thousand", None, None))

    def test_free_text_goes_to_the_judge_or_word_overlap(self):
        q = {"id": "b", "ask": "What?", "expect": "a timer that whistles when tea is done", "check": "judge"}
        self.assertTrue(probes.memory_correct(q, "nothing", scripted({}, {"memory": True}), None))
        self.assertFalse(probes.memory_correct(q, "the whistling tea timer", scripted({}, {"memory": False}), None))
        self.assertTrue(probes.memory_correct(q, "a timer that whistles for tea", None, None))
        self.assertFalse(probes.memory_correct(q, "i do not remember", None, None))

    def test_the_run_scores_accuracy(self):
        qs = [{"id": "s-1", "ask": "How many?", "expect": "5", "check": "number"},
              {"id": "s-2", "ask": "How many more?", "expect": "6", "check": "number"}]
        got = probes.memory_run(scripted({"s-1": "5 of them", "s-2": "no idea"}), qs)
        self.assertEqual((got["asked"], got["accuracy"]), (2, 0.5))


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True).stdout.strip()


class LastGood(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "checkout"
        self.state = Path(self.tmp.name) / "state"
        self.root.mkdir()
        git(self.root, "init", "-q", "-b", "main")
        git(self.root, "config", "user.name", "t")
        git(self.root, "config", "user.email", "t@example.invalid")
        (self.root / "f").write_text("good\n")
        git(self.root, "add", "f")
        git(self.root, "commit", "-q", "-m", "good")
        self.good = git(self.root, "rev-parse", "HEAD")
        (self.root / "f").write_text("promoted\n")
        git(self.root, "commit", "-q", "-am", "promoted")
        self.bad = git(self.root, "rev-parse", "HEAD")
        self.saved = os.environ.pop("SPINE_LAB", None)

    def tearDown(self):
        if self.saved is not None:
            os.environ["SPINE_LAB"] = self.saved
        self.tmp.cleanup()

    def test_without_last_good_nothing_falls_back(self):
        ok, why = last_good.fallback(self.root, self.state, "the doctor failed")
        self.assertFalse(ok)
        self.assertIn("no state/last-good", why)
        self.assertEqual(git(self.root, "rev-parse", "HEAD"), self.bad)

    def test_the_doctor_failing_boots_last_good_once(self):
        last_good.record(self.root, self.state, self.good, "gen-1")
        ok, _ = last_good.fallback(self.root, self.state, "bin/doctor failed")
        self.assertTrue(ok)
        self.assertEqual(git(self.root, "rev-parse", "HEAD"), self.good)
        self.assertEqual((self.root / "f").read_text(), "good\n")
        record = json.loads((self.state / "spine-fallback.json").read_text())
        self.assertEqual((record["from"], record["to"], record["reason"]), (self.bad, self.good, "bin/doctor failed"))
        ok, why = last_good.fallback(self.root, self.state, "again")
        self.assertFalse(ok)
        self.assertIn("already at last-good", why)

    def test_tracked_edits_are_never_thrown_away(self):
        last_good.record(self.root, self.state, self.good)
        (self.root / "f").write_text("an operator's edit\n")
        ok, why = last_good.fallback(self.root, self.state, "x")
        self.assertFalse(ok)
        self.assertIn("tracked edits", why)
        self.assertEqual((self.root / "f").read_text(), "an operator's edit\n")

    def test_a_lab_spine_never_falls_back(self):
        last_good.record(self.root, self.state, self.good)
        os.environ["SPINE_LAB"] = "1"
        try:
            self.assertFalse(last_good.fallback(self.root, self.state, "x")[0])
        finally:
            os.environ.pop("SPINE_LAB")
        self.assertEqual(git(self.root, "rev-parse", "HEAD"), self.bad)

    def test_the_command_line(self):
        script = REPO / "bin" / "lib" / "last_good.py"
        run = lambda *a: subprocess.run([sys.executable, str(script), *a], capture_output=True, text=True)  # noqa: E731
        self.assertEqual(run("fallback", str(self.root), str(self.state), "x").returncode, 1)
        self.assertEqual(run("record", str(self.root), str(self.state), self.good, "gen-2").returncode, 0)
        self.assertEqual(json.loads((self.state / "last-good").read_text())["gen"], "gen-2")
        self.assertEqual(run("fallback", str(self.root), str(self.state), "boot").returncode, 0)
        status = json.loads(run("status", str(self.state)).stdout)
        self.assertEqual(status["fallback"]["to"], self.good)


if __name__ == "__main__":
    unittest.main()
