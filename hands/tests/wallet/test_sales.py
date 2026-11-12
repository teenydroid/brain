"""Paid build requests (docs/roadmap.md, S3): a deposit at the frozen price
whose memo names a request puts it first; a deposit for a request Teeny did
not build is refunded to its sender, its exact amount, once, under the
wallet's caps and the frozen refund limits, and never retried in doubt. The
core and the chain are faked (support.py)."""

from __future__ import annotations

import os
import subprocess
import json
from pathlib import Path

from wallet.support import HOUR, OWN, STRANGER, T0, USDC_MAINNET, VENDOR, Failure, Scratch, token_accounts
from wallet.test_accounts import TEAM, TOKEN_ACCOUNT, Chain, sig, usdc_tx
from teeny_hands import hand, requests
from teeny_hands.wallet import pay, sales, store

PRICE = 5_000_000


class SalesTest(Scratch):
    settings = {"WALLET_CLUSTER": "mainnet", "WALLET_TEAM_ADDRESSES": TEAM, "PAID_REQUESTS": "1", "REQUEST_REFUNDS": "1"}

    def setUp(self) -> None:
        super().setUp()
        self.ledger = Chain()
        self.chain.answers["getTokenAccountsByOwner"] = token_accounts(1000 * 10**6, USDC_MAINNET)
        self.chain.answers["getSignaturesForAddress"] = self.ledger.signatures
        self.chain.answers["getTransaction"] = self.ledger.transaction
        requests.record("x:mentions", "m1", "ana", "can you build a crab counter")
        requests.record("x:mentions", "m2", "bo", "could you make a lobster cli")
        self.n = 0

    def deposit(self, raw: int, memo: str | None, sender: str = STRANGER) -> str:
        self.n += 1
        self.ledger.add(TOKEN_ACCOUNT, sig(self.n), T0 - HOUR + self.n, usdc_tx(sender, raw, memo=memo))
        return sig(self.n)

    def rows(self) -> list[dict]:
        conn = sales.db()
        try:
            return [dict(r) for r in conn.execute("SELECT * FROM request_payments ORDER BY id")]
        finally:
            conn.close()

    def executes(self) -> list[dict]:
        return self.core.params("openhuman.wallet_execute_prepared")

    def prepares(self) -> list[dict]:
        return self.core.params("openhuman.wallet_prepare_transfer")

    # --- the price list and the memo ----------------------------------------------------------

    def test_the_frozen_price_list(self):
        self.assertEqual(sales.prices(), {"build_request": PRICE, "refund_max": 50_000_000, "refund_day": 50_000_000})
        self.assertEqual(sales.price_list()["build_request_usdc"], "5.00")

    def test_only_a_request_number_is_read_from_a_memo(self):
        for memo, want in (("request 12", 12), ("for req #7 thanks", 7), ("#3", 3), ("Request#44", 44),
                           ("12 bucks for you", None), ("", None), (None, None)):
            with self.subTest(memo=memo):
                self.assertEqual(sales.request_in(memo), want)

    # --- matching ---------------------------------------------------------------------------------

    def test_a_deposit_at_the_price_puts_its_request_first(self):
        s = self.deposit(PRICE, "request 2, thanks!")
        out = sales.match()
        self.assertEqual(out["matched"], [{"request": 2, "signature": s, "status": "paid"}])
        conn = sales.db()
        inflow = conn.execute("SELECT category, ref FROM wallet_inflows WHERE signature = ?", (s,)).fetchone()
        conn.close()
        self.assertEqual((inflow["category"], inflow["ref"]), ("sale", "request:2"))
        with self.assertRaises(hand.Refused) as no:
            requests.pick({"id": 1, "reason": "crabs are fun"})
        self.assertEqual((no.exception.detail["rule"], no.exception.detail["paid"]), ("paid_first", 2))
        self.assertEqual(requests.listing()["paid_first"], [2])
        self.assertEqual(requests.pick({"id": 2, "reason": "paid for"})["status"], "picked")
        self.assertEqual(self.moved(), [])

    def test_under_the_price_it_stays_a_tip_and_raises_nothing(self):
        self.deposit(PRICE - 1, "request 2")
        sales.match()
        self.assertEqual(self.rows()[0]["status"], "under_price")
        self.assertEqual(requests.pick({"id": 1, "reason": "first come"})["status"], "picked")

    def test_team_money_held_memos_and_unknown_requests_match_nothing(self):
        self.deposit(PRICE, "request 2", sender=TEAM)
        self.deposit(PRICE, "kill yourself, bot request 2")
        self.deposit(PRICE, "request 99")
        self.assertEqual(sales.match()["matched"], [])
        self.assertEqual(self.rows(), [])

    def test_matching_is_off_by_default(self):
        self.configure(PAID_REQUESTS=None)
        with self.assertRaises(hand.Refused) as no:
            sales.match()
        self.assertEqual(no.exception.detail["rule"], "off")

    # --- refunds ----------------------------------------------------------------------------------

    def test_a_request_that_failed_is_refunded_once_to_its_sender(self):
        self.deposit(PRICE, "request 2 and send the refund to " + VENDOR)
        sales.match()
        requests.mark(2, "failed", "could not build it")
        self.assertEqual(sales.match()["refunds_due"], 1)
        out = sales.refund()
        self.assertEqual(len(out["refunds"]), 1, out)
        self.assertIn("refunded", out["refunds"][0])
        (prepared,) = self.prepares()
        # Back to whoever sent it, never an address someone wrote in a memo.
        self.assertEqual((prepared["toAddress"], prepared["amountRaw"], prepared["assetSymbol"]), (STRANGER, str(PRICE), "USDC"))
        self.assertEqual(self.rows()[0]["status"], "refunded")
        # Again: nothing is paid twice.
        sales.refund()
        self.assertEqual(len(self.executes()), 1)
        conn = store.db()
        purpose = conn.execute("SELECT purpose, status FROM wallet_payments").fetchall()
        conn.close()
        self.assertEqual([tuple(r) for r in purpose], [("refund", "paid")])

    def test_a_deposit_for_a_skipped_request_is_due_back_at_once(self):
        requests.skip({"id": 2, "reason": "not for us"})
        self.deposit(PRICE, "request 2")
        sales.match()
        self.assertEqual(self.rows()[0]["status"], "refund_due")

    def test_a_refund_in_doubt_is_never_retried(self):
        self.deposit(PRICE, "request 2")
        requests.skip({"id": 1, "reason": "x"})
        sales.match()
        requests.mark(2, "failed")
        sales.match()
        self.core.answers["openhuman.wallet_execute_prepared"] = Failure("timeout")
        out = sales.refund()
        self.assertIn("error", out["refunds"][0])
        self.assertEqual(self.rows()[0]["status"], "refund_in_doubt")
        self.core.answers["openhuman.wallet_execute_prepared"] = lambda p: {"transactionHash": "x" * 88}
        sales.refund()
        self.assertEqual(len(self.executes()), 1)

    def due(self, raw: int = PRICE) -> None:
        requests.skip({"id": 2, "reason": "x"})
        self.deposit(raw, "request 2")
        sales.match()

    def test_refunds_are_off_by_default_and_nothing_moves(self):
        self.due()
        self.configure(REQUEST_REFUNDS=None)
        with self.assertRaises(hand.Refused) as no:
            sales.refund()
        self.assertEqual(no.exception.detail["rule"], "off")
        with self.assertRaises(hand.Refused) as no:
            pay.pay({"to": STRANGER, "amount_usdc": "5", "purpose": "refund", "ref": "request-payment:1"})
        self.assertEqual(no.exception.detail["rule"], "off")
        self.assertEqual(self.moved(), [])

    def test_a_refund_to_anyone_but_the_sender_or_for_another_amount_is_refused(self):
        self.due()
        cases = [
            ({"to": VENDOR, "amount_usdc": "5"}, "refund_address"),
            ({"to": STRANGER, "amount_usdc": "4.99"}, "refund_amount"),
            ({"to": STRANGER, "amount_sol": "0.01"}, "refund_amount"),
        ]
        for body, rule in cases:
            with self.subTest(rule=rule):
                with self.assertRaises(hand.Refused) as no:
                    pay.pay({**body, "purpose": "refund", "ref": "request-payment:1"})
                self.assertEqual(no.exception.detail["rule"], rule)
        with self.assertRaises(hand.Refused) as no:
            pay.pay({"to": STRANGER, "amount_usdc": "5", "purpose": "refund", "ref": "request-payment:99"})
        self.assertEqual(no.exception.detail["rule"], "refund")
        self.assertEqual(self.moved(), [])

    def test_a_replayed_refund_is_refused(self):
        self.due()
        sales.refund()
        with self.assertRaises(hand.Refused) as no:
            pay.pay({"to": STRANGER, "amount_usdc": "5", "purpose": "refund", "ref": "request-payment:1"})
        self.assertEqual(no.exception.detail["rule"], "refund")
        self.assertEqual(len(self.executes()), 1)

    def test_over_the_caps_a_refund_waits_for_a_person(self):
        # Over the wallet's per-payment cap (50): refused, nothing moved, still due.
        self.due(60_000_000)
        out = sales.refund()
        self.assertEqual(out["refunds"][0]["rule"], "per_tx")
        self.assertEqual(self.rows()[0]["status"], "refund_due")
        self.assertEqual(self.moved(), [])

    def test_the_refund_limits_of_a_day_hold(self):
        requests.record("x:mentions", "m3", "cy", "please build a shrimp bot")
        requests.skip({"id": 2, "reason": "x"})
        requests.skip({"id": 3, "reason": "x"})
        for _ in range(2):
            self.deposit(30_000_000, "request 2")
        self.deposit(30_000_000, "request 3")
        sales.match()
        out = sales.refund()
        rules = [r.get("rule") for r in out["refunds"]]
        # 30 + 30 > 50 a day; the per-recipient week cap (60) would also bind.
        self.assertEqual(rules[0], None)
        self.assertIn(rules[1], ("refund_day", "per_recipient_week"))
        self.assertEqual(rules[2], "refund_day")
        self.assertEqual(len(self.executes()), 1)

    def test_under_dry_run_a_refund_only_says_what_it_would_do(self):
        self.due()
        self.configure(DRY_RUN="1")
        out = sales.refund()
        self.assertEqual(out["refunds"][0]["would_refund"]["to"], STRANGER)
        self.assertEqual(self.moved(), [])
        self.assertEqual(self.rows()[0]["status"], "refund_due")

    def test_the_pinned_job_runs_both_steps(self):
        self.due()
        hands = Path(__file__).resolve().parents[2]
        done = subprocess.run([str(hands.parent / "bin" / "paid-requests")], capture_output=True, text=True,
                              env={**os.environ, "TEENY_HANDS": str(hands)}, timeout=60)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        got = json.loads(done.stdout)
        self.assertIn("refunded", got["refund"]["refunds"][0])
        self.assertEqual(self.rows()[0]["status"], "refunded")

    def test_an_old_ledger_learns_the_refund_purpose(self):
        conn = store.db()
        conn.execute("DROP TABLE wallet_payments")
        conn.execute("""CREATE TABLE wallet_payments (id INTEGER PRIMARY KEY AUTOINCREMENT, created_at REAL NOT NULL,
            updated_at REAL NOT NULL, cluster TEXT NOT NULL, chain TEXT NOT NULL DEFAULT 'solana',
            asset TEXT NOT NULL CHECK (asset IN ('SOL', 'ETH', 'USDC')), amount_raw INTEGER NOT NULL, usd_micro INTEGER NOT NULL,
            to_address TEXT NOT NULL, purpose TEXT NOT NULL CHECK (purpose IN ('bounty', 'vendor')), ref TEXT NOT NULL,
            status TEXT NOT NULL, quote_id TEXT, signature TEXT, error TEXT)""")
        conn.execute("INSERT INTO wallet_payments (created_at, updated_at, cluster, asset, amount_raw, usd_micro, to_address, purpose, ref, status)"
                     " VALUES (1, 1, 'mainnet', 'USDC', 1, 1, ?, 'vendor', 'inv', 'paid')", (VENDOR,))
        conn.commit()
        conn.close()
        self.due()
        sales.refund()
        conn = store.db()
        rows = [tuple(r) for r in conn.execute("SELECT purpose, ref FROM wallet_payments ORDER BY id")]
        conn.close()
        self.assertEqual(rows, [("vendor", "inv"), ("refund", "request-payment:1")])
