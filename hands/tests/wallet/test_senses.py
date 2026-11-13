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

from wallet.support import DAY, HOUR, OWN, STRANGER, T0, TINY, USDC_MAINNET, Failure, Scratch, token_accounts
from wallet.test_accounts import TEAM, TOKEN_ACCOUNT, V1_REFUSED, Chain, sig, sol_tx, usdc_tx
from teeny_hands import hand, stimuli
from teeny_hands.wallet import accounts, senses, solana

HOLDER_A = "7EcDhSYGxXyscszYEp35KHN8vvw3svAuLKTzXwCFLtV"
HOLDER_B = "4Nd1mBQtrMJVYVfKf2PJy9NZUZdTAsp7D4xWLs4gDB4T"
#: Address poisoners: lookalikes of real counterparties (the same first and
#: last characters), sending dust so the victim later copies one from history.
POISON_A = "HN7cPoisonAAAAAAAAAAAAAAAAAAAAAAAAAAAAAe4YWrH"
POISON_B = "7EcDPoisonBBBBBBBBBBBBBBBBBBBBBBBBBBBBBzXwCFLtV"
POISON_C = "9xQePoisonCCCCCCCCCCCCCCCCCCCCCCCCCCCCC9PusVFin"


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

    def test_dust_deposits_reach_no_stimulus_and_never_name_their_sender(self):
        # Address poisoning, as seen live: three lookalikes send 0.000001 SOL
        # and a hundredth of a cent of USDC within the hour; the operator's
        # 1 SOL arrives beside them and must still reach the mind.
        self.ledger.add(OWN, sig(1), T0 - 3 * HOUR, sol_tx(POISON_A, 1_000))
        self.ledger.add(TOKEN_ACCOUNT, sig(2), T0 - 2 * HOUR, usdc_tx(POISON_B, 100, memo="see you at the airdrop"))
        self.ledger.add(OWN, sig(3), T0 - 2 * HOUR + 60, sol_tx(POISON_C, 1_000))
        self.ledger.add(OWN, sig(4), T0 - HOUR, sol_tx(STRANGER, 10**9))
        out = senses.watch()
        self.assertEqual([s["ref"] for s in self.inbox()], [f"{sig(4)}:SOL"])
        self.assertEqual((out["filed"], out.get("dust")), (1, 3))
        # Many dust deposits in one run file no aggregate note either: any
        # stimulus costs the mind a beat, and the sender sets how many.
        everything = json.dumps(stimuli.recent()["stimuli"])
        for poisoner in (POISON_A, POISON_B, POISON_C):
            self.assertNotIn(poisoner, everything)
            self.assertNotIn(senses._short(poisoner), everything)
        self.assertNotIn("airdrop", everything)
        # Still in the wallet's own ledger, flagged, so nothing is lost.
        listed = {row["signature"]: row for row in accounts.inflows()["inflows"]}
        self.assertEqual(len(listed), 4)
        self.assertEqual([s for s, row in sorted(listed.items()) if row.get("dust")], [sig(1), sig(2), sig(3)])
        self.assertEqual(listed[sig(2)]["sender"], POISON_B)
        # The next run does not reconsider them.
        again = senses.watch()
        self.assertEqual((again["filed"], again["dust"]), (0, 0))
        self.assertEqual(len(self.inbox()), 1)
        self.assertEqual(self.moved(), [])

    def test_a_deposit_at_the_dust_threshold_still_reaches_the_mind(self):
        self.ledger.add(OWN, sig(1), T0 - HOUR, sol_tx(STRANGER, 100_000))  # 0.0001 SOL
        self.ledger.add(TOKEN_ACCOUNT, sig(2), T0 - HOUR + 60, usdc_tx(STRANGER, 10_000))  # 0.01 USDC
        out = senses.watch()
        self.assertEqual((out["filed"], out.get("dust")), (2, 0))
        self.assertEqual(sorted(s["ref"] for s in self.inbox()), [f"{sig(1)}:SOL", f"{sig(2)}:USDC"])
        self.assertEqual({s["who"] for s in self.inbox()}, {STRANGER})

    def test_the_dust_threshold_is_the_operators_to_set(self):
        self.configure(WALLET_DUST_SOL="0.01", WALLET_DUST_USDC="0")
        self.ledger.add(OWN, sig(1), T0 - HOUR, sol_tx(POISON_A, 5_000_000))  # 0.005 SOL
        self.ledger.add(TOKEN_ACCOUNT, sig(2), T0 - HOUR + 60, usdc_tx(STRANGER, 1))  # 0 switches the USDC floor off
        out = senses.watch()
        self.assertEqual([s["ref"] for s in self.inbox()], [f"{sig(2)}:USDC"])
        self.assertEqual((out["filed"], out.get("dust")), (1, 1))

    def test_a_dust_threshold_that_is_not_an_amount_is_refused_never_guessed(self):
        for bad in ("lots", "-1", "0.0000000001"):
            self.configure(WALLET_DUST_SOL=bad)
            with self.assertRaises(hand.Refused) as caught:
                senses.watch()
            self.assertEqual(caught.exception.detail.get("rule"), "dust")
        self.assertEqual(self.inbox(), [])

    def test_a_transaction_the_rpc_will_not_return_does_not_stop_the_watch(self):
        # Live, 2026-11-13: a version-1 transaction failed every run from 16:07 UTC.
        self.ledger.add(TOKEN_ACCOUNT, sig(1), T0 - 2 * HOUR, Failure(V1_REFUSED))
        self.ledger.add(TOKEN_ACCOUNT, sig(2), T0 - HOUR, usdc_tx(STRANGER, 1_000_000))
        with self.assertLogs("teeny_hands.wallet.accounts", "WARNING"):
            out = senses.watch()
        self.assertEqual((out["scanned"], out["filed"], out["unreadable"]), (1, 1, 1))
        self.assertEqual([s["ref"] for s in self.inbox()], [f"{sig(2)}:USDC"])

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

    def test_with_no_wallet_yet_the_watch_and_daily_are_skipped_not_failed(self):
        # bin/wallet-setup has not run: there is nothing to watch, which is not a failure.
        self.core.answers["openhuman.wallet_status"] = {"configured": False, "accounts": []}
        for verb in (senses.watch, senses.daily):
            out = verb()
            self.assertEqual(out["skipped"], "no wallet yet (the operator runs bin/wallet-setup once)")
        self.assertEqual(self.inbox(), [])
        self.assertEqual(self.chain.calls, [])
        self.assertEqual(self.moved(), [])

    def test_the_pinned_jobs_with_no_wallet_yet_are_skipped_not_failed(self):
        # The scheduler counts a non-zero exit as a failed job, every 15 minutes.
        self.core.answers["openhuman.wallet_status"] = {"configured": False, "accounts": []}
        for args in ((), ("daily",)):
            done = self.job(*args)
            self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
            out = json.loads(done.stdout)
            self.assertTrue(out["ok"])
            self.assertIn("no wallet yet", out["skipped"])

    def test_the_pinned_job_switched_on_still_refuses_a_broken_rule(self):
        self.configure(WALLET_TEAM_ADDRESSES="not-an-address")
        done = self.job()
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertEqual(json.loads(done.stdout)["rule"], "team")
