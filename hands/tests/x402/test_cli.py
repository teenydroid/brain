"""hands/x402 on the command line: one JSON object, the contract's exit codes,
and a rehearsal that pays nothing. The core is faked (wallet/support.py)."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from wallet.support import Scratch
from teeny_hands.wallet import x402 as wallet_x402  # noqa: E402

HAND = Path(__file__).resolve().parents[2] / "x402"


class CliTest(Scratch):
    settings = {"WALLET_MAX_X402_PER_REQUEST_USD": "0.05", "WALLET_MAX_X402_PER_DAY_USD": "1"}

    def setUp(self):
        super().setUp()
        wallet_x402.hosts_add("api.exa.ai", "search")

    def run_hand(self, *args, env=None):
        done = subprocess.run([str(HAND), *args], capture_output=True, text=True, timeout=60,
                              env={**os.environ, **(env or {})}, stdin=subprocess.DEVNULL)
        return done.returncode, json.loads(done.stdout.strip().splitlines()[-1])

    def test_a_rehearsed_search_says_would_fetch_and_pays_nothing(self):
        code, out = self.run_hand("search", "openhuman agent", "--n", "1", env={"DRY_RUN": "1"})
        self.assertEqual(code, 0, out)
        self.assertTrue(out["dry_run"])
        self.assertEqual(out["would_fetch"]["url"], "https://api.exa.ai/search")
        self.assertNotIn("openhuman.x402_fetch", self.core.methods())

    def test_a_query_that_looks_like_a_flag_is_still_a_query(self):
        code, out = self.run_hand("search", "--n", "2", "--", "--help me", env={"DRY_RUN": "1"})
        self.assertEqual(code, 0, out)

    def test_a_refusal_exits_2_with_its_rule(self):
        code, out = self.run_hand("search", "q", "--n", "11")
        self.assertEqual(code, 2)
        self.assertEqual(out["rule"], "n")
        code, out = self.run_hand("crawl", "q")
        self.assertEqual((code, out["rule"]), (2, "off"))

    def test_contents_takes_urls_and_fund_is_skipped_while_off(self):
        code, out = self.run_hand("contents", "https://example.com/a", "https://example.com/b", env={"DRY_RUN": "1"})
        self.assertEqual(code, 0, out)
        self.assertEqual(out["would_fetch"]["url"], "https://api.exa.ai/contents")
        code, out = self.run_hand("fund")
        self.assertEqual(code, 0, out)
        self.assertIn("off", out["skipped"])

    def test_log_lists_the_paid_requests_and_the_fund_runs(self):
        code, out = self.run_hand("log", "--max", "5")
        self.assertEqual(code, 0, out)
        self.assertEqual((out["requests"], out["fund_runs"]), ([], []))
