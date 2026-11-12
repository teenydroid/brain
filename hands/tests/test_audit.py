"""The weekly audit (bin/lib/audit.py; docs/roadmap.md, G6).

Built from fixture ledgers in a scratch root: guard holds, payments, the
evolve archive and halts, boots and a git history. Checks that each count
is right, that the operator-action count is what the 30-day test reads,
that nothing but numbers and enums reaches the page (a hold's text, a
halt's detail, a commit message), and that publishing is off unless the
operator switches it on."""

from __future__ import annotations

import io
import json
import os
import sqlite3
import subprocess
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

import audit  # noqa: E402
from teeny_hands import hand  # noqa: E402
from teeny_hands.guard import holds as guard_holds  # noqa: E402
from teeny_hands.wallet import store as wallet_store  # noqa: E402

# Monday 2026-11-16 00:00 UTC; the week audited is 2026-W46 (Nov 9 to 16).
NOW = 1794787200.0
START = NOW - 7 * 86400
SECRET_TEXT = "kill yourself said a stranger"


def at(days: float) -> float:
    return START + days * 86400


class AuditTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "state" / "evolve").mkdir(parents=True)
        (self.root / "secrets").mkdir()
        self.env = mock.patch.dict(os.environ, {"TEENY_ROOT": str(self.root), "TEENY_NOW": str(NOW)}, clear=False)
        self.env.start()
        os.environ.pop("TEENY_STATE", None)
        os.environ.pop("DRY_RUN", None)
        hand._ENV_CACHE = None
        self.db = sqlite3.connect(self.root / "state" / "hands.db")
        self.db.executescript(guard_holds._SCHEMA)
        self.db.executescript(wallet_store.SCHEMA)
        self.db.execute("CREATE TABLE guard_reviews (id INTEGER PRIMARY KEY, at REAL, hold_id INTEGER, "
                        "category TEXT, outcome TEXT, sampled INTEGER)")

    def tearDown(self):
        self.db.close()
        self.env.stop()
        hand._ENV_CACHE = None
        self.tmp.cleanup()

    # --- fixtures -------------------------------------------------------------------------

    def hold(self, when: float, category: str, status: str = "pending", decided: float | None = None) -> None:
        self.db.execute("INSERT INTO guard_holds (at, direction, kind, context, category, reason, held_by, text, "
                        "status, decided_at, note) VALUES (?, 'outbound', 'draft', 'x', ?, 'r', 'screen', ?, ?, ?, ?)",
                        (audit.iso(when), category, SECRET_TEXT, status,
                         audit.iso(decided) if decided else None, "an operator note"))
        self.db.commit()

    def pay(self, when: float, usd: float, to: str = "A" * 44, status: str = "paid") -> None:
        self.db.execute("INSERT INTO wallet_payments (created_at, updated_at, cluster, asset, amount_raw, usd_micro, "
                        "to_address, purpose, ref, status) VALUES (?, ?, 'devnet', 'USDC', 1, ?, ?, 'vendor', 'r', ?)",
                        (when, when, int(usd * 1_000_000), to, status))
        self.db.commit()

    def jsonl(self, name: str, *rows: dict) -> None:
        path = self.root / "state" / name
        with path.open("a") as fh:
            for row in rows:
                fh.write(json.dumps(row) + "\n")

    def git(self, *args: str, when: float | None = None) -> str:
        env = {**os.environ, "GIT_AUTHOR_NAME": "op", "GIT_AUTHOR_EMAIL": "op@example.com",
               "GIT_COMMITTER_NAME": "op", "GIT_COMMITTER_EMAIL": "op@example.com"}
        if when is not None:
            stamp = f"@{int(when)} +0000"
            env.update(GIT_AUTHOR_DATE=stamp, GIT_COMMITTER_DATE=stamp)
        return subprocess.run(["git", "-C", str(self.root), *args], env=env, check=True,
                              capture_output=True, text=True).stdout.strip()

    def page(self) -> dict:
        return audit.build()

    # --- the week -------------------------------------------------------------------------

    def test_the_week_is_the_last_whole_iso_week(self):
        self.assertEqual(audit.week_bounds(None, NOW)[0], "2026-W46")
        self.assertEqual(audit.week_bounds("2026-W46", 0)[1:], (START, NOW))
        with self.assertRaises(ValueError):
            audit.week_bounds("last week", NOW)

    # --- holds ----------------------------------------------------------------------------

    def test_holds_by_category_and_outcome_inside_the_week_only(self):
        self.hold(at(1), "self-harm")
        self.hold(at(2), "profanity", "approved", decided=at(2.5))
        self.hold(at(3), "profanity", "rejected", decided=at(3.5))
        self.hold(at(-1), "hate")
        self.hold(at(2), "something a model made up")
        got = self.page()["holds"]
        self.assertEqual(got["total"], 4)
        self.assertEqual(got["by_category"]["self-harm"], {"pending": 1})
        self.assertEqual(got["by_category"]["profanity"], {"approved": 1, "rejected": 1})
        self.assertEqual(got["by_category"]["other"], {"pending": 1})
        self.assertNotIn("hate", got["by_category"])

    def test_reviewer_releases_and_the_sample_are_counted(self):
        self.db.executemany("INSERT INTO guard_reviews (at, hold_id, category, outcome, sampled) VALUES (?, 1, 'profanity', ?, ?)",
                            [(at(1), "released", 1), (at(1), "released", 0), (at(2), "kept", 0)])
        self.db.commit()
        self.assertEqual(self.page()["holds"]["reviewer"], {"released": 2, "kept": 1, "sampled": 1})

    # --- spend ----------------------------------------------------------------------------

    def test_spend_against_each_cap(self):
        (self.root / "secrets" / "teeny.env").write_text(
            "WALLET_CLUSTER=devnet\nWALLET_MAX_PER_TX_USD=50\nWALLET_MAX_PER_DAY_USD=100\n"
            "WALLET_MAX_PER_RECIPIENT_WEEK_USD=60\nWALLET_SOL_USD=200\n")
        self.pay(at(1), 30)
        self.pay(at(1.5), 25)
        self.pay(at(3), 40, to="B" * 44)
        self.pay(at(4), 10, status="in_doubt")
        self.pay(at(5), 99, status="refused")
        self.pay(at(-3), 20)
        got = self.page()["spend"]
        self.assertEqual(got["paid_usd"], 105.0)
        self.assertEqual(got["largest_payment_usd"], 40.0)
        self.assertEqual(got["largest_day_usd"], 55.0)
        # The recipient-week looks back past the start of the week.
        self.assertEqual(got["largest_recipient_week_usd"], 85.0)
        self.assertEqual(got["caps"]["per_recipient_week_usd"], 60.0)
        self.assertTrue(got["over_a_cap"])
        self.assertEqual(got["payments"], {"paid": 3, "in_doubt": 1, "refused": 1})

    def test_no_wallet_is_no_caps(self):
        got = self.page()["spend"]
        self.assertIsNone(got["caps"])
        self.assertEqual(got["paid_usd"], 0.0)

    # --- halts, generations, drift -----------------------------------------------------------

    def test_halts_generations_and_drift(self):
        self.jsonl("evolve/halts.jsonl",
                   {"kind": "persona-drift", "detail": SECRET_TEXT, "at": audit.iso(at(2))},
                   {"kind": "generations", "detail": "x", "at": audit.iso(at(3))},
                   {"kind": "j-rise", "detail": "x", "at": audit.iso(at(-2))})
        self.jsonl("evolve/archive.jsonl",
                   {"id": "gen-4", "verdict": "won", "recorded_at": audit.iso(at(1)), "pr_number": 9,
                    "probe": {"drift": 0.41}},
                   {"id": "gen-4", "merged_at": audit.iso(at(2)), "recorded_at": audit.iso(at(2))},
                   {"id": "gen-5", "verdict": "lost", "recorded_at": audit.iso(at(4)), "probe": {"drift": 0.12}},
                   {"id": "gen-3", "verdict": "won", "recorded_at": audit.iso(at(-1))})
        page = self.page()
        self.assertEqual(page["halts"], {"total": 2, "by_kind": {"generations": 1, "persona-drift": 1}})
        self.assertEqual([g["id"] for g in page["generations"]], ["gen-4", "gen-5"])
        self.assertEqual(page["generations"][0],
                         {"id": "gen-4", "verdict": "won", "pr": True, "merged": True, "promoted": False, "reverted": False})
        self.assertEqual(page["drift"], {"probes": 2, "max": 0.41, "halts": 1})

    # --- operator actions --------------------------------------------------------------------

    def test_operator_actions_are_counted_and_the_loops_own_are_not(self):
        self.git("init", "-q", "-b", "main")
        self.git("commit", "-q", "--allow-empty", "-m", "before", when=at(-2))
        human = self.git("commit", "-q", "--allow-empty", "-m", SECRET_TEXT, when=at(1)) or self.git("rev-parse", "HEAD")
        self.git("commit", "-q", "--allow-empty", "-m", "the loop's merge", when=at(2))
        loop_sha = self.git("rev-parse", "HEAD")
        self.jsonl("evolve/archive.jsonl", {"id": "gen-6", "merge_sha": loop_sha, "promoted_at": audit.iso(at(2)),
                                            "recorded_at": audit.iso(at(-5))})
        self.hold(at(1), "profanity", "approved", decided=at(1.2))
        self.hold(at(1), "profanity", "cleared", decided=at(1.2))
        self.jsonl("boots.jsonl",
                   {"at": audit.iso(at(-1)), "commit": "c0"},
                   {"at": audit.iso(at(1)), "commit": "c1"},            # a deploy
                   {"at": audit.iso(at(2)), "commit": loop_sha},        # the loop's promotion
                   {"at": audit.iso(at(3)), "commit": loop_sha},        # a restart
                   {"at": audit.iso(at(4)), "commit": "c0", "fallback": True})  # last-good, automatic
        del human
        op = self.page()["operator"]
        self.assertEqual(op["commits_on_main"], 1)
        self.assertEqual(op["hold_decisions"], 1)
        self.assertEqual(op["deploys"], 1)
        self.assertEqual(op["restarts"], 1)
        self.assertEqual(op["boots"], 4)
        self.assertEqual(op["total"], 4)

    def test_a_quiet_week_has_zero_operator_actions(self):
        self.git("init", "-q", "-b", "main")
        self.git("commit", "-q", "--allow-empty", "-m", "old", when=at(-3))
        self.jsonl("boots.jsonl", {"at": audit.iso(at(-3)), "commit": "c0"})
        self.assertEqual(self.page()["operator"]["total"], 0)

    def test_no_git_is_unknown_not_zero(self):
        self.assertIsNone(self.page()["operator"]["commits_on_main"])

    # --- what the page may carry -------------------------------------------------------------

    def test_no_free_text_reaches_the_page(self):
        self.hold(at(1), "self-harm", "approved", decided=at(1.5))
        self.jsonl("evolve/halts.jsonl", {"kind": "doctor", "detail": SECRET_TEXT, "at": audit.iso(at(2))})
        self.jsonl("evolve/archive.jsonl", {"id": "gen-7", "verdict": SECRET_TEXT, "title": SECRET_TEXT,
                                            "recorded_at": audit.iso(at(1))},
                   {"id": "evil text id", "verdict": "won", "recorded_at": audit.iso(at(1))})
        page = self.page()
        text = json.dumps(page) + audit.markdown(page)
        self.assertNotIn("kill yourself", text)
        self.assertNotIn("operator note", text)
        self.assertNotIn("evil", text)
        self.assertEqual(page["generations"][0]["verdict"], "other")

    def test_the_markdown_page_names_every_section(self):
        md = audit.markdown(self.page())
        for heading in ("# Audit 2026-W46", "## Operator actions", "## Guard holds", "## Spend against the caps",
                        "## Halts", "## Generations", "## Persona drift"):
            self.assertIn(heading, md)

    # --- the run -----------------------------------------------------------------------------

    def run_main(self, *args: str) -> tuple[int, dict]:
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = audit.main(list(args))
        return code, json.loads(buf.getvalue())

    def test_a_run_writes_the_page_and_publishes_nothing_by_default(self):
        (self.root / "secrets" / "teeny.env").write_text("DRY_RUN=0\n")
        with mock.patch.object(audit, "publish") as publish:
            code, out = self.run_main()
        self.assertEqual(code, 0)
        publish.assert_not_called()
        self.assertFalse(out["publish"])
        self.assertTrue((self.root / "state" / "audit" / "2026-W46.json").is_file())
        self.assertTrue((self.root / "state" / "audit" / "2026-W46.md").is_file())

    def test_publishing_needs_the_switch_and_a_live_run(self):
        (self.root / "secrets" / "teeny.env").write_text("AUDIT_PUBLISH=1\n")
        with mock.patch.object(audit, "publish") as publish:
            self.run_main()
        publish.assert_not_called()  # DRY_RUN is on by default
        hand._ENV_CACHE = None
        (self.root / "secrets" / "teeny.env").write_text("AUDIT_PUBLISH=1\nDRY_RUN=0\n")
        with mock.patch.object(audit, "publish", return_value={"pushed": 1}) as publish:
            _, out = self.run_main()
        publish.assert_called_once()
        self.assertEqual(out["pushed"], 1)

    def test_json_prints_and_writes_nothing(self):
        code, out = self.run_main("--json")
        self.assertEqual((code, out["week"]), (0, "2026-W46"))
        self.assertFalse((self.root / "state" / "audit").exists())

    def test_a_boot_is_recorded(self):
        self.run_main("boot")
        self.run_main("boot")
        rows = [json.loads(line) for line in (self.root / "state" / "boots.jsonl").read_text().splitlines()]
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["at"], audit.iso(NOW))
        self.assertFalse(rows[0]["fallback"])


class PublishTest(AuditTest):
    def test_the_page_goes_beside_the_receipts_through_the_secret_gate(self):
        remote = self.root / "remote.git"
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
        seed = self.root / "seed"
        subprocess.run(["git", "clone", "-q", str(remote), str(seed)], check=True, capture_output=True)
        env = {**os.environ, "GIT_AUTHOR_NAME": "s", "GIT_AUTHOR_EMAIL": "s@e", "GIT_COMMITTER_NAME": "s",
               "GIT_COMMITTER_EMAIL": "s@e"}
        subprocess.run(["git", "-C", str(seed), "commit", "-q", "--allow-empty", "-m", "init"], check=True, env=env)
        subprocess.run(["git", "-C", str(seed), "push", "-q", "origin", "HEAD:main"], check=True, capture_output=True)
        (self.root / "secrets" / "teeny.env").write_text(
            f"RECEIPTS_REMOTE={remote}\nRECEIPTS_GITHUB_TOKEN=tok-not-a-real-one\nGIT_NAME=Teeny\nGIT_EMAIL=t@e\n")
        hand._ENV_CACHE = None
        page = self.page()
        self.assertEqual(audit.publish(page), {"pushed": 1})
        files = subprocess.run(["git", "--git-dir", str(remote), "ls-tree", "-r", "--name-only", "main"],
                               capture_output=True, text=True, check=True).stdout.split()
        self.assertIn("audit/2026-W46.json", files)
        self.assertIn("audit/2026-W46.md", files)
        # The same week again changes nothing.
        self.assertEqual(audit.publish(page), {"pushed": 0})


if __name__ == "__main__":
    unittest.main()
