"""hands/dripsplan tests: validate and emit, offline, no keys, no network.

Covers: happy path, each failure mode (unreadable file, bad JSON, bad
YAML, empty/short/long/dupe/bad entries, weight length, value, range,
sum, unknown keys, nested list key), determinism of emit, and exit codes
through the hands/dripsplan launcher.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from teeny_hands.dripsplan import config

HANDS = Path(__file__).resolve().parents[1]
DRIPSPLAN = HANDS / "dripsplan"

GOOD = """\
recipient: teeny
note: funding my own repos
entries:
  - teenydroid/refusal-ledger
  - teenydroid/csvstat
weights: [70, 30]
"""
BAD = """\
entries:
  - not-a-slug
  - 0x1234
weights: [60, 50]
"""
BAD_JSON = '{"entries": ["a/b"], "weights": [100], "extra": 1}'
GOOD_JSON = json.dumps({"recipient": "teeny", "note": "funding my own repos",
                        "entries": ["teenydroid/refusal-ledger", "teenydroid/csvstat"],
                        "weights": [70, 30]})


class DripsplanTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, name, text):
        path = self.dir / name
        path.write_text(text, encoding="utf-8")
        return str(path)

    # -- read ---------------------------------------------------------

    def test_a_yaml_config_reads_and_validates(self):
        path = self.write("good.yaml", GOOD)
        got = config.validate(config.read(path), path)
        self.assertEqual(got["entries"],
                         ["teenydroid/refusal-ledger", "teenydroid/csvstat"])
        self.assertEqual(got["weights"], [70, 30])
        self.assertEqual(got["recipient"], "teeny")
        self.assertEqual(got["note"], "funding my own repos")

    def test_json_config_reads_and_validates(self):
        path = self.write("good.json", GOOD_JSON)
        got = config.validate(config.read(path), path)
        self.assertEqual(got["entries"],
                         ["teenydroid/refusal-ledger", "teenydroid/csvstat"])
        self.assertEqual(got["weights"], [70, 30])

    def test_a_missing_file_is_a_clear_error(self):
        with self.assertRaises(config.ConfigError) as ctx:
            config.read(str(self.dir / "nope.yaml"))
        self.assertIn("cannot read", ctx.exception.issues[0])

    def test_broken_json_falls_back_to_yaml_parser(self):
        # JSON parse fails, YAML subset also rejects it: clear parse error.
        path = self.write("broken.json", "{not json, not yaml")
        with self.assertRaises(config.ConfigError) as ctx:
            config.read(path)
        self.assertIn("expected 'key: value'", ctx.exception.issues[0])

    def test_a_json_non_mapping_is_an_error(self):
        path = self.write("list.json", "[1, 2]")
        with self.assertRaises(config.ConfigError) as ctx:
            config.read(path)
        self.assertIn("must be a mapping", ctx.exception.issues[0])

    def test_a_bad_json_file_is_read_as_yaml_with_the_same_errors(self):
        # .yaml files are parsed by the YAML subset even when they look
        # like JSON; a broken one still fails with a named line.
        path = self.write("broken.yaml", '{not json, not yaml')
        with self.assertRaises(config.ConfigError) as ctx:
            config.read(path)
        self.assertIn("line 1", ctx.exception.issues[0])

    # -- validate ------------------------------------------------------

    def test_validate_passes_a_good_config(self):
        got = config.validate(config.read_text(GOOD, "good.yaml"))
        self.assertEqual(got["entries"][0], "teenydroid/refusal-ledger")

    def test_entries_with_a_bad_shape_are_named_by_line(self):
        with self.assertRaises(config.ConfigError) as ctx:
            config.validate(config.read_text(BAD, "bad.yaml"))
        joined = "\n".join(ctx.exception.issues)
        self.assertIn("entries[1]", joined)
        self.assertIn("not a repo slug", joined)
        self.assertIn("entries[2]", joined)
        self.assertIn("0x1234", joined)
        self.assertIn("sum is 110", joined)

    def test_entries_over_200_are_refused(self):
        doc = {"entries": [f"o{i}/r{i}" for i in range(201)],
               "weights": [1] * 200}
        with self.assertRaises(config.ConfigError) as ctx:
            config.validate(doc)
        self.assertIn("max is 200", ctx.exception.issues[0])

    def test_200_entries_pass(self):
        doc = {"entries": [f"o{i}/r{i}" for i in range(200)],
               "weights": [0] * 199 + [100]}
        got = config.validate(doc)
        self.assertEqual(len(got["entries"]), 200)

    def test_duplicate_entries_are_refused(self):
        doc = {"entries": ["a/b", "a/b"], "weights": [50, 50]}
        with self.assertRaises(config.ConfigError) as ctx:
            config.validate(doc)
        self.assertIn("duplicate", ctx.exception.issues[0])

    def test_weights_must_sum_to_100(self):
        doc = {"entries": ["a/b", "c/d"], "weights": [50, 51]}
        with self.assertRaises(config.ConfigError) as ctx:
            config.validate(doc)
        self.assertIn("sum is 101", ctx.exception.issues[0])

    def test_weight_count_mismatch_is_refused(self):
        doc = {"entries": ["a/b", "c/d"], "weights": [100]}
        with self.assertRaises(config.ConfigError) as ctx:
            config.validate(doc)
        self.assertIn("entries has 2", ctx.exception.issues[0])

    def test_a_negative_weight_is_refused_and_zero_is_legal(self):
        doc = {"entries": ["a/b", "c/d"], "weights": [100, -5]}
        with self.assertRaises(config.ConfigError) as ctx:
            config.validate(doc)
        self.assertIn("0 or more", ctx.exception.issues[0])
        got = config.validate({"entries": ["a/b", "c/d"], "weights": [100, 0]})
        self.assertEqual(got["weights"], [100, 0])

    def test_a_non_integer_weight_is_refused(self):
        doc = {"entries": ["a/b"], "weights": [12.5]}
        with self.assertRaises(config.ConfigError) as ctx:
            config.validate(doc)
        self.assertIn("whole number of percent points", ctx.exception.issues[0])

    def test_an_unknown_key_is_refused(self):
        doc = {"entries": ["a/b"], "weights": [100], "amounts": [1]}
        with self.assertRaises(config.ConfigError) as ctx:
            config.validate(doc)
        self.assertIn("unknown key", ctx.exception.issues[0])

    def test_a_bool_weight_is_refused(self):
        doc = {"entries": ["a/b"], "weights": [True]}
        with self.assertRaises(config.ConfigError) as ctx:
            config.validate(doc)
        self.assertIn("whole number of percent points", ctx.exception.issues[0])

    def test_the_same_address_in_two_cases_is_a_duplicate(self):
        # Hex case is not part of an address: the same recipient twice.
        upper = "0xAbCdEf0123456789AbCdEf0123456789AbCdEf01"
        lower = upper.lower()
        doc = {"entries": [upper, lower], "weights": [50, 50]}
        with self.assertRaises(config.ConfigError) as ctx:
            config.validate(doc)
        self.assertIn("duplicate", "\n".join(ctx.exception.issues))

    def test_the_same_repo_slug_in_two_cases_is_a_duplicate(self):
        # GitHub owner and repo names are case-insensitive.
        doc = {"entries": ["Owner/Repo", "owner/repo"], "weights": [50, 50]}
        with self.assertRaises(config.ConfigError) as ctx:
            config.validate(doc)
        self.assertIn("duplicate", "\n".join(ctx.exception.issues))

    def test_a_yaml_block_list_of_weights_reads_as_numbers(self):
        # The block form of the same list the inline form already accepts.
        path = self.write("block.yaml",
                          "entries:\n  - teenydroid/refusal-ledger\n"
                          "  - teenydroid/csvstat\nweights:\n  - 70\n  - 30\n")
        got = config.validate(config.read(path), path)
        self.assertEqual(got["weights"], [70, 30])

    def test_a_list_item_after_a_top_level_key_is_refused(self):
        # `- c/two` belongs to no list once `note:` has begun a new key;
        # it must not be silently appended to `entries`.
        path = self.write("stale.yaml",
                          "entries:\n  - a/one\nnote: hi\n  - c/two\n"
                          "weights: [100]\n")
        with self.assertRaises(config.ConfigError) as ctx:
            config.read(path)
        self.assertIn("line 4", ctx.exception.issues[0])

    def test_a_config_that_is_not_utf8_is_a_named_error(self):
        path = self.dir / "latin1.json"
        path.write_bytes(b'{"entries": ["a/b"], "weights": [100], "note": "caf\xe9"}')
        with self.assertRaises(config.ConfigError) as ctx:
            config.read(str(path))
        self.assertIn("not valid UTF-8", ctx.exception.issues[0])

    def test_a_duplicate_json_key_is_refused(self):
        # json.loads keeps the last value silently; a config must not.
        path = self.write("dupe.json",
                          '{"entries": ["a/b"], "weights": [100], '
                          '"weights": [50]}')
        with self.assertRaises(config.ConfigError) as ctx:
            config.read(path)
        self.assertIn("'weights' given twice", ctx.exception.issues[0])

    # -- emit ----------------------------------------------------------

    def test_emit_is_byte_deterministic(self):
        path = self.write("good.yaml", GOOD)
        one = config.write_json(config.plan(path))
        for _ in range(4):
            self.assertEqual(config.write_json(config.plan(path)), one)
        self.assertEqual(one.count('"action":"create_drip_list"'), 1)
        self.assertEqual(one.count('"action":"set_splits"'), 1)
        self.assertEqual(one.count('"action":"fund"'), 1)

    def test_emit_carries_the_entries_in_order_with_weights(self):
        got = config.plan(self.write("good.yaml", GOOD))
        recipients = got["steps"][1]["recipients"]
        self.assertEqual(recipients[0]["to"], "teenydroid/refusal-ledger")
        self.assertEqual(recipients[0]["weight"], 70)
        self.assertEqual(recipients[1]["to"], "teenydroid/csvstat")
        self.assertEqual(recipients[1]["weight"], 30)
        self.assertTrue(got["dry_run"])
        self.assertEqual(got["steps"][0]["action"], "create_drip_list")
        self.assertEqual(got["steps"][2]["action"], "fund")

    # -- cli -----------------------------------------------------------

    def cli(self, *args):
        return subprocess.run(
            [sys.executable, "-I", str(DRIPSPLAN), *args],
            capture_output=True, text=True, cwd=str(HANDS.parent),
            env={**os.environ, "PYTHONPATH": str(HANDS / "lib")})

    def test_the_bin_validates_and_prints_ok(self):
        path = self.write("good.yaml", GOOD)
        got = self.cli("validate", path)
        self.assertEqual(got.returncode, 0, got.stderr)
        self.assertIn("2 entries", got.stdout)
        self.assertIn("sum to 100", got.stdout)

    def test_the_bin_fails_with_exit_1_and_line_errors(self):
        path = self.write("bad.yaml", BAD)
        got = self.cli("validate", path)
        self.assertEqual(got.returncode, 1)
        self.assertIn("entries[1]", got.stderr)

    def test_the_bin_emits_deterministic_json(self):
        path = self.write("good.yaml", GOOD)
        one = self.cli("emit", path).stdout.splitlines()[-1]
        two = self.cli("emit", path).stdout.splitlines()[-1]
        self.assertEqual(one, two)
        doc = json.loads(one)
        self.assertEqual(doc["steps"][0]["action"], "create_drip_list")

    def test_usage_error_exits_2(self):
        got = self.cli()
        self.assertEqual(got.returncode, 2)
        got = self.cli("frobnicate", "x.yaml")
        self.assertEqual(got.returncode, 2)

    def test_a_sample_config_for_the_two_repos_plans_clean(self):
        path = self.write("teeny.yaml", GOOD)
        ok = self.cli("validate", path)
        plan = self.cli("emit", path)
        self.assertEqual(ok.returncode, 0)
        self.assertEqual(plan.returncode, 0)
        self.assertIn("refusal-ledger", plan.stdout)
        self.assertIn("csvstat", plan.stdout)


if __name__ == "__main__":
    unittest.main()
