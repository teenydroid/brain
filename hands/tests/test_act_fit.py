"""bin/lib/act_fit.py: a playbook's inputs are cut to fit the draft message the
core will take (it holds one over 64 KiB as "flagged for security review").

On 2026-11-15, -16 and -17 self-improve's inputs made an 86,357-char message
and its draft failed every day. bin/test/act_test.sh runs the cut through
bin/act; these pin the cut itself."""

import json
import subprocess
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "bin" / "lib"))

import act_fit  # noqa: E402
from act_fit import fit, fit_one, size  # noqa: E402


def jobs(n: int, tag: str, pad: int = 200) -> dict:
    return {"ok": True, "jobs": [{"id": f"{tag}-{i}", "summary": "x" * pad} for i in range(n)]}


class FitOneTest(unittest.TestCase):
    def test_a_value_that_fits_is_untouched(self):
        value = jobs(3, "a")
        self.assertEqual(fit_one(json.loads(json.dumps(value)), 10_000), (value, False))

    def test_a_list_loses_items_from_its_end_and_stays_json(self):
        value, cut = fit_one(jobs(100, "a"), 5_000)
        self.assertTrue(cut)
        self.assertLessEqual(size(value), 5_000)
        ids = [job["id"] for job in value["jobs"]]
        self.assertEqual(ids, [f"a-{i}" for i in range(len(ids))])
        self.assertTrue(all(job["summary"] == "x" * 200 for job in value["jobs"]))

    def test_the_longest_list_is_cut_first(self):
        value = {"big": [{"n": i} for i in range(200)], "small": [1, 2, 3]}
        value, _ = fit_one(value, 1_000)
        self.assertEqual(value["small"], [1, 2, 3])

    def test_text_that_is_not_json_is_cut_with_an_ellipsis(self):
        value, cut = fit_one("y" * 50_000, 20_000)
        self.assertTrue(cut)
        self.assertLessEqual(size(value), 20_000)
        self.assertTrue(value.endswith("..."))
        self.assertGreater(len(value), 19_000)

    def test_a_long_string_with_no_list_left_is_cut(self):
        value, _ = fit_one({"ok": True, "body": "z" * 30_000}, 10_000)
        self.assertLessEqual(size(value), 10_000)
        self.assertTrue(value["body"].startswith("zzz"))

    def test_bytes_not_characters_are_counted(self):
        value, _ = fit_one("é" * 20_000, 10_000)
        self.assertLessEqual(size(value), 10_000)

    def test_a_limit_nothing_can_meet_stops(self):
        value, _ = fit_one({"ok": True, "n": 12345}, 2)
        self.assertEqual(value, {"ok": True, "n": 12345})


class FitTest(unittest.TestCase):
    def test_inputs_that_fit_are_not_cut(self):
        inputs = {"a": jobs(3, "a"), "b": "text"}
        fitted, cut = fit(json.loads(json.dumps(inputs)), 20_000, 60_000)
        self.assertEqual((fitted, cut), (inputs, []))

    def test_each_input_is_held_to_its_share(self):
        fitted, cut = fit({"a": jobs(200, "a"), "b": jobs(2, "b")}, 20_000)
        self.assertLessEqual(size(fitted["a"]), 20_000)
        self.assertEqual(len(fitted["b"]["jobs"]), 2)
        self.assertEqual(cut, ["a"])

    def test_all_together_fit_the_total_and_the_biggest_is_cut_first(self):
        # Like self-improve on 2026-11-17: two inputs over 20 KB, one of 18,
        # one of 9, small ones.
        inputs = {"builds": jobs(120, "b"), "archive": jobs(110, "a"), "stimuli": jobs(80, "s"),
                  "people": jobs(40, "p"), "pending": jobs(5, "q"), "lessons": []}
        fitted, cut = fit(inputs, 20_000, 55_000)
        self.assertLessEqual(size(fitted), 55_000)
        self.assertEqual(len(fitted["people"]["jobs"]), 40)
        self.assertEqual(len(fitted["pending"]["jobs"]), 5)
        self.assertEqual(cut, ["archive", "builds", "stimuli"])
        self.assertLessEqual(abs(size(fitted["builds"]) - size(fitted["archive"])), 1_000)

    def test_a_total_nothing_can_meet_stops(self):
        fitted, _ = fit({"a": {"n": 1}, "b": [1]}, 20_000, 1)
        self.assertEqual(fitted, {"a": {"n": 1}, "b": []})


class CliTest(unittest.TestCase):
    def run_cli(self, args, stdin):
        return subprocess.run([sys.executable, str(REPO / "bin" / "lib" / "act_fit.py"), *args],
                              input=stdin, capture_output=True, text=True, check=False)

    def test_prints_the_inputs_and_the_names_cut(self):
        done = self.run_cli(["20000", "30000"], json.dumps({"a": jobs(200, "a"), "b": {"ok": True}}))
        self.assertEqual(done.returncode, 0, done.stderr)
        out = json.loads(done.stdout)
        self.assertEqual(out["cut"], ["a"])
        self.assertEqual(out["inputs"]["b"], {"ok": True})
        self.assertLessEqual(size(out["inputs"]), 30_000)

    def test_refuses_inputs_that_are_not_an_object(self):
        self.assertEqual(self.run_cli(["20000"], "[]").returncode, 2)
        self.assertEqual(self.run_cli([], "{}").returncode, 2)


if __name__ == "__main__":
    unittest.main()
