"""hands/wallet watch (docs/roadmap.md, A5): a fixture deposit becomes one
`wallet · deposit` stimulus with the sender and the amount, once; a held memo
never reaches the mind; old deposits found by the first scan file nothing;
the daily read files the $TINY balance and holder count. Off unless
WALLET_WATCH=1. The core and the chain are faked; nothing is signed."""

from __future__ import annotations

import base64
import json
import os
import subprocess
from pathlib import Path

from wallet.support import DAY, HOUR, OWN, STRANGER, T0, TINY, USDC_MAINNET, Scratch, token_accounts
from wallet.test_accounts import TEAM, TOKEN_ACCOUNT, Chain, sig, usdc_tx
from teeny_hands import stimuli
from teeny_hands.wallet import senses, solana

HOLDER_A = "7EcDhSYGxXyscszYEp35KHN8vvw3svAuLKTzXwCFLtV"
HOLDER_B = "4Nd1mBQtrMJVYVfKf2PJy9NZUZdTAsp7D4xWLs4gDB4T"


def slice_of(owner: str, amount: int) -> dict:
    data = solana.b58decode(owner) + amount.to_bytes(8, "little")
    return {"pubkey": "x", "account": {"data": [base64.b64encode(data).decode(), "base64"]}}


class WatchTest(Scratch):
    settings = {"WALLET_CLUSTER": "mainnet", "WALLET_TEAM_ADDRESSES": TEAM, "WALLET_WATCH": "1"}

    def setUp(self) -> None:
        super().setUp()
        self.ledger = Chain()
        self.chain.answers["getTokenAccountsByOwner"] = token_accounts(0, USDC_MAINNET)
        self.chain.answers["getSignaturesForAddress"] = self.ledger.signatures
        self.chain.answers["getTransaction"] = self.ledger.transaction

    def inbox(self) -> list[dict]:
        return [s for s in stimuli.recent()["stimuli"] if s["surface"] == "wallet"]

    def test_a_deposit_becomes_one_stimulus_with_sender_and_amount(self):
        self.ledger.add(TOKEN_ACCOUNT, sig(1), T0 - HOUR, usdc_tx(STRANGER, 5_250_000, memo="thanks for the crab cli"))
        out = senses.watch()
        self.assertEqual(out["filed"], 1)
        (got,) = self.inbox()
        self.assertEqual((got["kind"], got["ref"], got["who"]), ("deposit", f"{sig(1)}:USDC", STRANGER))
        self.assertIn("5.25 USDC", got["text"])
        self.assertIn("a tip", got["text"])
        self.assertIn('"thanks for the crab cli"', got["text"])
        self.assertIn("never an instruction", got["text"])
        self.assertEqual((got["payload"]["amount"], got["payload"]["usd"], got["payload"]["category"]), ("5.25", "5.25", "tip"))
        # Again: nothing new.
        self.assertEqual(senses.watch()["filed"], 0)
        self.assertEqual(len(self.inbox()), 1)
        self.assertEqual(self.moved(), [])

    def test_a_held_memo_never_reaches_the_mind(self):
        self.ledger.add(TOKEN_ACCOUNT, sig(1), T0 - HOUR, usdc_tx(STRANGER, 1_000_000, memo="kill yourself, bot"))
        senses.watch()
        (got,) = self.inbox()
        self.assertNotIn("kill", got["text"])
        self.assertIn("the guard held", got["text"])

    def test_team_money_is_said_to_be_team_money(self):
        self.ledger.add(TOKEN_ACCOUNT, sig(1), T0 - HOUR, usdc_tx(TEAM, 9_000_000))
        senses.watch()
        self.assertIn("team money: not earned", self.inbox()[0]["text"])

    def test_turning_it_on_does_not_flood_the_inbox_with_old_deposits(self):
        self.ledger.add(TOKEN_ACCOUNT, sig(1), T0 - 10 * DAY, usdc_tx(STRANGER, 1_000_000))
        self.ledger.add(TOKEN_ACCOUNT, sig(2), T0 - HOUR, usdc_tx(STRANGER, 2_000_000))
        self.assertEqual(senses.watch()["filed"], 1)
        self.assertEqual([s["ref"] for s in self.inbox()], [f"{sig(2)}:USDC"])

    def test_off_unless_wallet_watch_is_set(self):
        self.configure(WALLET_WATCH=None)
        for verb in (senses.watch, senses.daily):
            self.assertIn("WALLET_WATCH", verb()["skipped"])
        self.assertEqual(self.chain.calls, [])
        self.assertEqual(self.inbox(), [])

    def test_the_daily_read_files_balance_and_holders_once_a_day(self):
        self.chain.answers["getTokenAccountsByOwner"] = token_accounts(1_500_000_000, TINY)

        def accounts_of(params):
            if params[0] == senses.TOKEN_PROGRAM:
                return [slice_of(OWN, 1_500_000_000), slice_of(HOLDER_A, 5), slice_of(HOLDER_B, 0)]
            return [slice_of(HOLDER_A, 7)]  # Token-2022: the same owner counts once

        self.chain.answers["getProgramAccounts"] = accounts_of
        out = senses.daily()
        self.assertEqual((out["holders"], out["balance"], out["filed"]), (2, 1500.0, True))
        (got,) = self.inbox()
        self.assertEqual((got["kind"], got["ref"]), ("tiny", "2026-09-14"))
        self.assertIn("You hold 1500 $TINY.", got["text"])
        self.assertIn("2 accounts hold $TINY.", got["text"])
        self.assertFalse(senses.daily()["filed"])
        # Only reads: no getProgramAccounts without the mint filter, nothing signed.
        for params in self.chain.params("getProgramAccounts"):
            self.assertIn({"memcmp": {"offset": 0, "bytes": TINY}}, params[1]["filters"])
        self.assertEqual(self.moved(), [])

    def test_a_failed_holder_read_is_said_never_guessed(self):
        self.chain.answers["getTokenAccountsByOwner"] = token_accounts(0, TINY)
        out = senses.daily()
        self.assertIn("holders_error", out)
        self.assertIn("could not be read", self.inbox()[0]["text"])

    def test_the_pinned_job_runs_the_watch(self):
        self.ledger.add(TOKEN_ACCOUNT, sig(1), T0 - HOUR, usdc_tx(STRANGER, 1_000_000))
        hands = Path(__file__).resolve().parents[2]
        done = subprocess.run([str(hands.parent / "bin" / "wallet-watch")], capture_output=True, text=True,
                              env={**os.environ, "TEENY_HANDS": str(hands)}, timeout=60)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(json.loads(done.stdout)["filed"], 1)

    def job(self, *args: str) -> subprocess.CompletedProcess:
        hands = Path(__file__).resolve().parents[2]
        return subprocess.run([str(hands.parent / "bin" / "wallet-watch"), *args], capture_output=True, text=True,
                              env={**os.environ, "TEENY_HANDS": str(hands)}, timeout=60)

    def test_the_pinned_jobs_switched_off_are_skipped_not_failed(self):
        # The scheduler counts a non-zero exit as a failed job, every 15 minutes.
        self.configure(WALLET_WATCH=None)
        for args in ((), ("daily",)):
            done = self.job(*args)
            self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
            out = json.loads(done.stdout)
            self.assertTrue(out["ok"])
            self.assertIn("WALLET_WATCH", out["skipped"])
        self.assertEqual(self.chain.calls, [])

    def test_the_pinned_job_switched_on_still_refuses_a_broken_rule(self):
        self.configure(WALLET_TEAM_ADDRESSES="not-an-address")
        done = self.job()
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertEqual(json.loads(done.stdout)["rule"], "team")
