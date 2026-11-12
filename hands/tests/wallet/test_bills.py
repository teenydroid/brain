"""Teeny pays its own server (docs/roadmap.md, S5): a recorded invoice that is
due is paid once from the wallet, to the allowlisted address on it, its exact
amount, under the caps and the frozen bill limits, with a receipt; an
in-doubt payment is never retried; an unknown recipient, a replayed invoice,
an amount over a cap or a limit is refused and moves nothing. The core and
the chain are faked (support.py)."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from wallet.support import DAY, STRANGER, T0, USDC_MAINNET, VENDOR, Failure, Scratch, token_accounts
from teeny_hands import hand
from teeny_hands.vps import bills
from teeny_hands.wallet import pay, reads, store

DUE = "2026-09-15"  # T0 is 2026-09-14T07:00Z


class BillsTest(Scratch):
    settings = {"WALLET_CLUSTER": "mainnet", "VPS_PAY_BILLS": "1"}

    def setUp(self) -> None:
        super().setUp()
        self.chain.answers["getTokenAccountsByOwner"] = token_accounts(1000 * 10**6, USDC_MAINNET)
        reads.vendors_add("eqvps", VENDOR, "their USDC address")

    def invoice(self, invoice: str = "INV-1", usd: str = "24", due: str = DUE, pay_to: str = VENDOR) -> Json:
        return bills.add("eqvps", invoice, usd, "solana", pay_to, due, None)

    def rows(self) -> list[dict]:
        return bills.listing()["invoices"]

    def executes(self) -> list[dict]:
        return self.core.params("openhuman.wallet_execute_prepared")

    def test_a_due_invoice_is_paid_once_with_a_receipt(self):
        self.invoice()
        out = bills.pay_due()
        self.assertIn("paid", out["bills"][0], out)
        (prepared,) = self.core.params("openhuman.wallet_prepare_transfer")
        self.assertEqual((prepared["toAddress"], prepared["amountRaw"], prepared["assetSymbol"]), (VENDOR, "24000000", "USDC"))
        (row,) = self.rows()
        self.assertEqual(row["status"], "paid")
        # Again: nothing more.
        bills.pay_due()
        self.assertEqual(len(self.executes()), 1)
        # A receipt, like any payment: owed a post, and saying what it was for.
        due = reads.receipts_due()["due"]
        self.assertEqual(len(due), 1)
        self.assertEqual(due[0]["purpose"], "bill")
        self.assertEqual(reads.receipt(due[0]["id"])["receipt"]["for"], "Teeny's own server: eqvps invoice INV-1")

    def test_an_invoice_not_yet_due_waits(self):
        self.invoice(due="2026-09-30")
        self.assertEqual(bills.pay_due()["bills"], [])
        self.assertEqual(self.moved(), [])

    def test_in_doubt_is_never_retried(self):
        self.invoice()
        self.core.answers["openhuman.wallet_execute_prepared"] = Failure("timeout")
        out = bills.pay_due()
        self.assertIn("error", out["bills"][0])
        self.assertEqual(self.rows()[0]["status"], "in_doubt")
        self.core.answers["openhuman.wallet_execute_prepared"] = lambda p: {"transactionHash": "x" * 88}
        bills.pay_due()
        self.assertEqual(len(self.executes()), 1)
        # Not even by hand: the ref is taken.
        with self.assertRaises(hand.Refused) as no:
            pay.pay({"to": VENDOR, "amount_usdc": "24", "purpose": "bill", "ref": "vps:eqvps:INV-1"})
        self.assertIn(no.exception.detail["rule"], ("bill", "duplicate"))

    def test_an_unknown_recipient_cannot_be_put_on_an_invoice(self):
        with self.assertRaises(hand.Refused) as no:
            self.invoice(pay_to=STRANGER)
        self.assertEqual(no.exception.detail["rule"], "vendor")

    def test_a_replayed_invoice_is_refused(self):
        self.invoice()
        with self.assertRaises(hand.Refused) as no:
            self.invoice()
        self.assertEqual(no.exception.detail["rule"], "duplicate")
        bills.pay_due()
        with self.assertRaises(hand.Refused) as no:
            pay.pay({"to": VENDOR, "amount_usdc": "24", "purpose": "bill", "ref": "vps:eqvps:INV-1"})
        self.assertEqual(no.exception.detail["rule"], "bill")
        self.assertEqual(len(self.executes()), 1)

    def test_a_bill_to_another_address_or_amount_is_refused(self):
        self.invoice()
        reads.vendors_add("other", STRANGER, None)
        for body, rule in (({"to": STRANGER, "amount_usdc": "24"}, "bill_address"),
                           ({"to": VENDOR, "amount_usdc": "25"}, "bill_amount"),
                           ({"to": VENDOR, "amount_sol": "0.1"}, "bill_amount")):
            with self.subTest(rule=rule):
                with self.assertRaises(hand.Refused) as no:
                    pay.pay({**body, "purpose": "bill", "ref": "vps:eqvps:INV-1"})
                self.assertEqual(no.exception.detail["rule"], rule)
        self.assertEqual(self.moved(), [])

    def test_a_vendor_removed_since_the_invoice_is_not_paid(self):
        self.invoice()
        reads.vendors_remove("eqvps")
        out = bills.pay_due()
        self.assertEqual(out["bills"][0]["rule"], "vendor")
        self.assertEqual(self.rows()[0]["status"], "due")
        self.assertEqual(self.moved(), [])

    def test_over_a_cap_or_a_limit_it_waits_for_a_person(self):
        self.invoice("BIG", usd="60")  # over WALLET_MAX_PER_TX_USD (50)
        out = bills.pay_due()
        self.assertEqual(out["bills"][0]["rule"], "per_tx")
        self.configure(WALLET_MAX_PER_TX_USD="100", WALLET_MAX_PER_DAY_USD="200", WALLET_MAX_PER_RECIPIENT_WEEK_USD="200")
        out = bills.pay_due()
        self.assertEqual(out["bills"][0]["rule"], "bill_max")  # 60 > the frozen 50 a bill
        self.assertEqual(self.moved(), [])
        self.assertEqual(self.rows()[0]["status"], "due")

    def test_thirty_days_of_bills_stay_under_the_frozen_limit(self):
        self.configure(WALLET_MAX_PER_DAY_USD="200", WALLET_MAX_PER_RECIPIENT_WEEK_USD="200")
        for n in range(3):
            self.invoice(f"INV-{n}", usd="40")
        out = bills.pay_due()
        rules = [b.get("rule") for b in out["bills"]]
        self.assertEqual(rules, [None, None, "bill_month"])  # 40 + 40 + 40 > 100
        self.assertEqual(len(self.executes()), 2)

    def test_off_by_default_nothing_moves(self):
        self.invoice()
        self.configure(VPS_PAY_BILLS=None)
        with self.assertRaises(hand.Refused) as no:
            bills.pay_due()
        self.assertEqual(no.exception.detail["rule"], "off")
        with self.assertRaises(hand.Refused) as no:
            pay.pay({"to": VENDOR, "amount_usdc": "24", "purpose": "bill", "ref": "vps:eqvps:INV-1"})
        self.assertEqual(no.exception.detail["rule"], "off")
        self.assertEqual(self.moved(), [])

    def test_dry_run_pays_nothing(self):
        self.invoice()
        self.configure(DRY_RUN="1")
        out = bills.pay_due()
        self.assertEqual(out["bills"][0]["would_pay"]["purpose"], "bill")
        self.assertEqual(self.moved(), [])
        self.assertEqual(self.rows()[0]["status"], "due")

    def test_the_pinned_job_pays_it(self):
        self.invoice()
        hands = Path(__file__).resolve().parents[2]
        done = subprocess.run([str(hands.parent / "bin" / "pay-bills")], capture_output=True, text=True,
                              env={**os.environ, "TEENY_HANDS": str(hands)}, timeout=60)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("paid", json.loads(done.stdout)["bills"][0])
        conn = store.db()
        purposes = [r[0] for r in conn.execute("SELECT purpose FROM wallet_payments")]
        conn.close()
        self.assertEqual(purposes, ["bill"])


Json = dict
