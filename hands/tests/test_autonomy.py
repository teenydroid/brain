"""The autonomy ladder (bin/lib/autonomy.py, evals/autonomy.toml;
docs/roadmap.md, G2 and G8; docs/governance.md, fence "ladder").

Each capability has a level the operator granted; a switch set above it
fails the doctor; a drop event (a promotion, a model change) after the
level was confirmed drops it a step and warns; the report says what each
capability still needs for the next level. Offline: fixture secrets, state
and ledgers in a scratch root."""

from __future__ import annotations

import io
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO / "bin" / "lib"))
sys.path.insert(0, str(REPO / "hands" / "lib"))

import autonomy  # noqa: E402
from teeny_hands import hand  # noqa: E402
import proofs  # noqa: E402


class Scratch(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "evals").mkdir()
        shutil.copy(REPO / "evals" / "autonomy.toml", self.root / "evals" / "autonomy.toml")
        shutil.copytree(REPO / "brain" / "agents", self.root / "brain" / "agents")
        self.secrets = self.root / "secrets"
        self.state = self.root / "state"
        self.secrets.mkdir()
        self.state.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def env(self, file: str, **values: str) -> None:
        path = self.secrets / file
        old = path.read_text() if path.exists() else ""
        path.write_text(old + "".join(f"{k}={v}\n" for k, v in values.items()))

    def toml(self, old: str, new: str) -> None:
        path = self.root / "evals" / "autonomy.toml"
        text = path.read_text()
        self.assertIn(old, text)
        path.write_text(text.replace(old, new, 1))

    def check(self) -> dict:
        return autonomy.check(self.root, self.secrets, self.state)


class LadderFileTest(unittest.TestCase):
    def test_the_checked_in_ladder_names_every_capability_and_known_proofs(self):
        caps = autonomy.load(REPO / "evals", [p[0] for p in proofs.PROOFS])
        self.assertEqual(set(caps), set(autonomy.CAPABILITIES))
        for cap in caps.values():
            self.assertIn(cap["level"], autonomy.LEVELS)

    def test_merge_is_below_l5_and_auto_promote_needs_l5(self):
        caps = autonomy.load(REPO / "evals")
        self.assertLess(caps["merge"]["level"], 5)
        self.assertIn({"file": "evolve.env", "name": "EVOLVE_AUTO_PROMOTE", "when": "truthy", "needs": 5},
                      caps["merge"]["switch"])

    def test_the_ladder_is_frozen(self):
        import frozen

        patterns = frozen.parse_patterns((REPO / "evals" / "frozen.txt").read_text())
        self.assertIsNotNone(frozen.frozen_pattern("evals/autonomy.toml", patterns))


class LoadTest(Scratch):
    def test_a_level_out_of_range_is_refused(self):
        self.toml("[capability.merge]\nwhat = \"merging its own generations and reverts, and restarting on them\"\nlevel = 4",
                  "[capability.merge]\nwhat = \"x\"\nlevel = 6")
        with self.assertRaisesRegex(autonomy.LadderError, "level must be 1 to 5"):
            autonomy.load(self.root / "evals")

    def test_a_missing_capability_is_refused(self):
        text = (self.root / "evals" / "autonomy.toml").read_text()
        (self.root / "evals" / "autonomy.toml").write_text(text.split("[capability.servers]")[0])
        with self.assertRaisesRegex(autonomy.LadderError, "lacks servers"):
            autonomy.load(self.root / "evals")

    def test_an_unknown_proof_is_refused(self):
        self.toml('{ id = "L3.mainnet" }', '{ id = "L3.made-up" }')
        with self.assertRaisesRegex(autonomy.LadderError, "L3.made-up"):
            autonomy.load(self.root / "evals", [p[0] for p in proofs.PROOFS])

    def test_a_malformed_switch_is_refused(self):
        self.toml('when = "positive", needs = 3 }', 'when = "sometimes", needs = 3 }')
        with self.assertRaisesRegex(autonomy.LadderError, "a switch needs"):
            autonomy.load(self.root / "evals")

    def test_switch_values(self):
        self.assertTrue(autonomy.is_on("1", "truthy"))
        self.assertTrue(autonomy.is_on("Yes", "truthy"))
        self.assertFalse(autonomy.is_on("0", "truthy"))
        self.assertFalse(autonomy.is_on(None, "truthy"))
        self.assertTrue(autonomy.is_on("0", "zero"))
        self.assertFalse(autonomy.is_on("1", "zero"))
        self.assertTrue(autonomy.is_on("2", "positive"))
        self.assertFalse(autonomy.is_on("0", "positive"))
        self.assertTrue(autonomy.is_on("lots", "positive"))


class DoctorCheckTest(Scratch):
    def test_nothing_on_is_ok(self):
        self.assertEqual(self.check()["status"], "ok")

    def test_auto_promote_is_refused_while_merge_is_below_l5(self):
        self.env("evolve.env", EVOLVE="1", EVOLVE_AUTO_PROMOTE="1")
        out = self.check()
        self.assertEqual(out["status"], "fail")
        self.assertIn("EVOLVE_AUTO_PROMOTE=1", out["detail"])
        self.assertIn("merge at L5", out["detail"])

    def test_auto_promote_passes_once_the_operator_grants_merge_l5(self):
        self.toml("[capability.merge]\nwhat = \"merging its own generations and reverts, and restarting on them\"\nlevel = 4",
                  "[capability.merge]\nwhat = \"merging\"\nlevel = 5")
        self.env("evolve.env", EVOLVE="1", EVOLVE_AUTO_PROMOTE="1")
        self.assertEqual(self.check()["status"], "ok")

    def test_every_switch_already_released_for_the_operator_to_turn_on_passes(self):
        # Turning one of these on must not fail a healthy box's boot.
        self.env("teeny.env", DRY_RUN="0", MIND_ISSUES_PER_DAY="2", MIND_TASKS_PER_DAY="2", MIND_MAILS_PER_DAY="1",
                 WALLET_MIND_ACTIONS="1")
        self.env("evolve.env", EVOLVE="1", EVOLVE_AUTO_PROMOTE="0")
        out = self.check()
        self.assertEqual(out["status"], "ok", out["detail"])
        self.assertIn("6 switch(es) on", out["detail"])

    def test_tiny_buy_needs_spend_at_l4(self):
        self.env("teeny.env", TINY_BUY="1")
        out = self.check()
        self.assertEqual(out["status"], "fail")
        self.assertIn("spend at L4", out["detail"])

    def test_the_process_environment_is_not_read(self):
        os.environ["EVOLVE_AUTO_PROMOTE"] = "1"
        try:
            self.assertEqual(self.check()["status"], "ok")
        finally:
            del os.environ["EVOLVE_AUTO_PROMOTE"]

    def test_a_promotion_after_confirmation_drops_self_edit_and_warns(self):
        self.env("evolve.env", EVOLVE="1")
        (self.state / "evolve").mkdir()
        (self.state / "evolve" / "archive.jsonl").write_text(
            json.dumps({"id": "gen-3", "promoted_at": "2026-12-01T10:00:00Z"}) + "\n")
        out = self.check()
        self.assertEqual(out["status"], "warn")
        self.assertIn("self-edit at L4; it dropped to L3", out["detail"])
        self.assertIn("gen gen-3 promoted", out["detail"])

    def test_a_promotion_before_confirmation_does_not_drop(self):
        self.env("evolve.env", EVOLVE="1")
        (self.state / "evolve").mkdir()
        (self.state / "evolve" / "archive.jsonl").write_text(
            json.dumps({"id": "gen-1", "promoted_at": "2026-11-01T10:00:00Z"}) + "\n")
        self.assertEqual(self.check()["status"], "ok")

    def test_the_cli_exits_1_on_a_fail_and_0_on_a_warn(self):
        self.env("evolve.env", EVOLVE_AUTO_PROMOTE="1")
        args = ["check", "--root", str(self.root), "--secrets", str(self.secrets), "--state", str(self.state)]
        with redirect_stdout(io.StringIO()):
            self.assertEqual(autonomy.main(args), 1)

    def test_a_broken_ladder_is_an_error(self):
        (self.root / "evals" / "autonomy.toml").write_text("not = [toml")
        args = ["check", "--root", str(self.root), "--secrets", str(self.secrets), "--state", str(self.state)]
        with redirect_stdout(io.StringIO()):
            self.assertEqual(autonomy.main(args), 2)


class ReportTest(Scratch):
    def setUp(self):
        super().setUp()
        self._env = {k: v for k, v in os.environ.items() if k in ("TEENY_ROOT", "TEENY_STATE", "TEENY_NOW")}
        os.environ["TEENY_ROOT"] = str(self.root)
        os.environ["TEENY_STATE"] = str(self.state)
        os.environ["TEENY_NOW"] = "1795000000"

    def tearDown(self):
        for k in ("TEENY_ROOT", "TEENY_STATE", "TEENY_NOW"):
            os.environ.pop(k, None)
        os.environ.update(self._env)
        super().tearDown()

    def report(self) -> dict:
        return {c["capability"]: c for c in autonomy.report(self.root, self.secrets, self.state)["capabilities"]}

    def test_every_capability_says_what_the_next_level_still_needs(self):
        out = self.report()
        self.assertEqual(set(out), set(autonomy.CAPABILITIES))
        merge = out["merge"]
        self.assertEqual((merge["level"], merge["next"]), (4, 5))
        self.assertEqual(merge["evidence"][0]["proof"], "L5.human_merged")
        self.assertEqual((merge["evidence"][0]["has"], merge["evidence"][0]["need"]), (0, 5))
        self.assertTrue(any("L5.human_merged 0/5" in s for s in merge["still_needs"]))

    def test_a_proof_met_in_the_ledger_leaves_the_list(self):
        (self.state / "evolve").mkdir()
        lines = [json.dumps({"id": f"gen-{i}", "merged_at": "2026-11-10T00:00:00Z", "pr_number": i}) for i in range(5)]
        (self.state / "evolve" / "archive.jsonl").write_text("\n".join(lines) + "\n")
        merge = self.report()["merge"]
        self.assertTrue(merge["evidence"][0]["met"])
        self.assertFalse(any("L5.human_merged" in s for s in merge["still_needs"]))
        # What no ledger counts is still named, for a person to read.
        self.assertTrue(any("evolve-watch" in s for s in merge["still_needs"]))

    def test_a_proofs_own_need_is_never_lowered(self):
        self.toml('{ id = "L2.x_replies" }', '{ id = "L2.x_replies", need = 2 }')
        reply = self.report()["reply"]
        self.assertEqual(reply["evidence"][0]["need"], 50)

    def test_proofs_ladder_prints_the_report(self):
        db = sqlite3.connect(self.state / "hands.db")
        db.close()
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = proofs.main(["--ladder", "--text"])
        self.assertEqual(code, 0)
        self.assertIn("merge", buf.getvalue())
        self.assertIn("for L5: L5.human_merged 0/5", buf.getvalue())


if __name__ == "__main__":
    unittest.main()


class ModelChangeTest(Scratch):
    """G8: a model change drops the levels that run on it and prepares the
    issue that lists the fence tests to rerun."""

    def setUp(self):
        super().setUp()
        shutil.copytree(REPO / "docs", self.root / "docs")
        self.env("spine.env", SPINE_MODEL="model-a")
        self.patch = mock.patch.dict(os.environ, {"TEENY_ROOT": str(self.root)})
        self.patch.start()
        os.environ.pop("DRY_RUN", None)
        hand._ENV_CACHE = None

    def tearDown(self):
        self.patch.stop()
        hand._ENV_CACHE = None
        super().tearDown()

    def change_model(self, model: str) -> dict:
        (self.secrets / "spine.env").write_text(f"SPINE_MODEL={model}\n")
        return autonomy.record_models(self.root, self.secrets, at=1796000000)

    def test_the_first_record_is_a_baseline_and_drops_nothing(self):
        out = autonomy.record_models(self.root, self.secrets, at=1795000000)
        self.assertTrue(out["recorded"])
        self.assertEqual(out["changes"], [])
        self.env("evolve.env", EVOLVE="1")
        self.assertEqual(self.check()["status"], "ok")
        self.assertFalse((self.secrets / "autonomy" / "issue-pending.json").exists())
        self.assertEqual(oct((self.secrets / "autonomy").stat().st_mode & 0o777), "0o700")

    def test_no_model_configured_records_nothing(self):
        (self.secrets / "spine.env").write_text("")
        self.assertFalse(autonomy.record_models(self.root, self.secrets)["recorded"])
        self.assertFalse((self.secrets / "autonomy").exists())

    def test_changing_the_model_drops_self_edit_a_level_and_prepares_the_issue(self):
        autonomy.record_models(self.root, self.secrets, at=1795000000)
        out = self.change_model("model-b")
        self.assertIn({"at": "2026-11-30T20:53:20Z", "kind": "change", "role": "evolver", "from": "model-a",
                       "to": "model-b"}, out["changes"])
        report = {c["capability"]: c for c in autonomy.report(self.root, self.secrets, self.state)["capabilities"]}
        self.assertEqual((report["self-edit"]["level"], report["self-edit"]["effective"]), (4, 3))
        self.assertEqual(report["servers"]["effective"], 1)  # never below L1
        self.env("evolve.env", EVOLVE="1")
        got = self.check()
        self.assertEqual(got["status"], "warn")
        self.assertIn("EVOLVE=1", got["detail"])
        issue = json.loads((self.secrets / "autonomy" / "issue-pending.json").read_text())
        self.assertIn("self-edit: L4 -> L3 (switches held back above L3: EVOLVE)", issue["body"])
        self.assertIn("`evolver`: `model-a` -> `model-b`", issue["body"])
        # Every fence test the charter cites is listed to rerun.
        for ref in autonomy.fence_tests(self.root):
            self.assertIn(ref, issue["body"])
        self.assertIn("hands/tests/test_autonomy.py::DoctorCheckTest.test_auto_promote_is_refused_while_merge_is_below_l5",
                      issue["body"])

    def test_the_same_model_again_is_no_new_change(self):
        autonomy.record_models(self.root, self.secrets, at=1795000000)
        self.change_model("model-b")
        self.assertEqual(self.change_model("model-b")["changes"], [])

    def test_a_role_with_its_own_model_drops_only_what_runs_on_it(self):
        autonomy.record_models(self.root, self.secrets, at=1795000000)
        self.env("evolve.env", EVOLVE_JUDGE_MODEL="judge-b")
        out = autonomy.record_models(self.root, self.secrets, at=1796000000)
        self.assertEqual([c["role"] for c in out["changes"]], ["judge"])
        report = {c["capability"]: c for c in autonomy.report(self.root, self.secrets, self.state)["capabilities"]}
        self.assertEqual(report["merge"]["effective"], 3)
        self.assertEqual(report["post"]["effective"], 4)

    def test_confirming_again_after_the_change_restores_the_level(self):
        autonomy.record_models(self.root, self.secrets, at=1795000000)
        self.change_model("model-b")
        text = (self.root / "evals" / "autonomy.toml").read_text().replace('confirmed = "2026-11-12"', 'confirmed = "2026-12-01"')
        (self.root / "evals" / "autonomy.toml").write_text(text)
        self.env("evolve.env", EVOLVE="1")
        self.assertEqual(self.check()["status"], "ok")

    def test_the_issue_is_off_until_the_operator_switches_it_on(self):
        autonomy.record_models(self.root, self.secrets, at=1795000000)
        self.change_model("model-b")
        out = autonomy.open_issue(self.root, self.secrets)
        self.assertFalse(out["opened"])
        self.assertIn("LADDER_ISSUES", out["why"])

    def test_a_dry_run_says_what_it_would_open_and_keeps_it_pending(self):
        autonomy.record_models(self.root, self.secrets, at=1795000000)
        self.change_model("model-b")
        self.env("teeny.env", LADDER_ISSUES="1")
        hand._ENV_CACHE = None
        out = autonomy.open_issue(self.root, self.secrets)
        self.assertEqual((out["opened"], out["dry_run"], out["would_open_issue"]), (False, True, "teenydroid/brain"))
        self.assertTrue((self.secrets / "autonomy" / "issue-pending.json").exists())

    def test_live_it_opens_once(self):
        autonomy.record_models(self.root, self.secrets, at=1795000000)
        self.change_model("model-b")
        self.env("teeny.env", LADDER_ISSUES="1", DRY_RUN="0", GITHUB_TOKEN="x")
        hand._ENV_CACHE = None
        gh = mock.Mock()
        gh.open_issue.return_value = {"number": 7, "html_url": "https://github.com/teenydroid/brain/issues/7"}
        out = autonomy.open_issue(self.root, self.secrets, gh)
        self.assertEqual((out["opened"], out["number"]), (True, 7))
        owner, name, title, body = gh.open_issue.call_args.args
        self.assertEqual((owner, name), ("teenydroid", "brain"))
        self.assertTrue(title.startswith("Model changed: rerun the fence tests"))
        self.assertEqual(autonomy.open_issue(self.root, self.secrets, gh)["why"], "nothing pending")
        self.assertEqual(gh.open_issue.call_count, 1)
