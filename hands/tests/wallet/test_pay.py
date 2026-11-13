"""hands/wallet pay: every rule refuses before anything moves, the ledger
row is written before the core is asked, and a payment in doubt is never
tried again. The core and the chain are faked (support.py)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from wallet.support import (
    DAY,
    HOUR,
    MOVING,
    OWN,
    STRANGER,
    T0,
    TINY,
    USDC_DEVNET,
    USDC_MAINNET,
    VENDOR,
    Failure,
    HttpStatus,
    Scratch,
    Sequence,
    balances,
    token_accounts,
)
from teeny_hands import hand  # noqa: E402 - after support puts hands/lib on the path
from teeny_hands.wallet import caps, pay, reads, store  # noqa: E402

WALLET = Path(__file__).resolve().parents[2] / "wallet"


def vendor_pay(**overrides):
    body = {"to": VENDOR, "amount_usdc": "10", "purpose": "vendor", "ref": "inv-1"}
    body.update(overrides)
    return {k: v for k, v in body.items() if v is not None}


class PayTest(Scratch):
    def setUp(self):
        super().setUp()
        reads.vendors_add("hosting", VENDOR, "the server")

    def rows(self):
        conn = store.db()
        try:
            return [dict(r) for r in conn.execute("SELECT * FROM wallet_payments ORDER BY id")]
        finally:
            conn.close()

    def refused(self, body, rule):
        before = len(self.moved())
        with self.assertRaises(hand.Refused) as caught:
            pay.pay(body)
        self.assertEqual(caught.exception.detail.get("rule"), rule, caught.exception.reason)
        self.assertEqual(len(self.moved()), before, "a refusal must not reach prepare or execute")
        return caught.exception

    # --- a payment that goes out --------------------------------------------------------------

    def test_a_vendor_payment_goes_out_and_is_recorded(self):
        out = pay.pay(vendor_pay())
        paid = out["paid"]
        self.assertEqual((paid["asset"], paid["amount_raw"], paid["usd"], paid["vendor"]), ("USDC", "10000000", "10.00", "hosting"))
        self.assertTrue(paid["url"].startswith("https://solscan.io/tx/5igSig"))
        self.assertTrue(paid["url"].endswith("?cluster=devnet"))
        self.assertEqual(self.core.params("openhuman.wallet_prepare_transfer"),
                         [{"chain": "solana", "toAddress": VENDOR, "amountRaw": "10000000", "assetSymbol": "USDC"}])
        self.assertEqual(self.core.params("openhuman.wallet_execute_prepared"), [{"quoteId": "q-1", "confirmed": True}])
        [row] = self.rows()
        self.assertEqual((row["status"], row["quote_id"], row["signature"][:6]), ("paid", "q-1", "5igSig"))

    def test_a_sol_payment_is_native_and_counted_at_the_fixed_price(self):
        out = pay.pay(vendor_pay(amount_usdc=None, amount_sol="0.1"))
        self.assertEqual((out["paid"]["asset"], out["paid"]["usd"]), ("SOL", "20.00"))  # 0.1 SOL at WALLET_SOL_USD=200
        self.assertEqual(self.core.params("openhuman.wallet_prepare_transfer"),
                         [{"chain": "solana", "toAddress": VENDOR, "amountRaw": "100000000"}])

    def test_the_pending_row_is_written_before_the_core_is_asked(self):
        seen = {}

        def prepare(params):
            seen["prepare"] = [r["status"] for r in self.rows()]
            from wallet.support import quote_for
            return quote_for(params)

        def execute(params):
            seen["execute"] = [(r["status"], r["quote_id"]) for r in self.rows()]
            from wallet.support import executed
            return executed(params)

        self.core.answers["openhuman.wallet_prepare_transfer"] = prepare
        self.core.answers["openhuman.wallet_execute_prepared"] = execute
        pay.pay(vendor_pay())
        self.assertEqual(seen["prepare"], ["pending"])
        self.assertEqual(seen["execute"], [("pending", "q-1")])
        self.assertEqual(self.rows()[0]["status"], "paid")

    # --- dry run ---------------------------------------------------------------------------------------

    def test_dry_run_evaluates_every_rule_and_moves_nothing(self):
        self.configure(DRY_RUN="1")
        out = pay.pay(vendor_pay())
        self.assertTrue(out["dry_run"])
        self.assertEqual(out["would_pay"]["to"], VENDOR)
        rules = [c["rule"] for c in out["checks"]]
        for rule in ("asset", "address", "caps", "per_tx", "own_address", "vendor", "duplicate", "per_day", "per_recipient_week", "half_balance"):
            self.assertIn(rule, rules)
        self.assertEqual(self.moved(), [])
        self.assertEqual(self.rows(), [])

    def test_the_environment_can_force_a_dry_run(self):
        os.environ["DRY_RUN"] = "1"
        self.assertTrue(pay.pay(vendor_pay())["dry_run"])
        self.assertEqual(self.moved(), [])

    def test_dry_run_still_refuses(self):
        self.configure(DRY_RUN="1")
        self.refused(vendor_pay(amount_usdc="51"), "per_tx")

    # --- the asset ---------------------------------------------------------------------------------------

    def test_tiny_is_never_sent(self):
        self.refused(vendor_pay(mint=TINY), "tiny")
        self.refused(vendor_pay(asset="TINY"), "tiny")
        self.refused(vendor_pay(asset="$tiny"), "tiny")
        self.refused(vendor_pay(to=TINY), "tiny")

    def test_any_other_mint_is_refused(self):
        self.refused(vendor_pay(mint=STRANGER), "asset")
        # Mainnet's USDC is not USDC on devnet.
        self.refused(vendor_pay(mint=USDC_MAINNET), "asset")
        self.refused(vendor_pay(asset="BONK"), "asset")
        self.refused(vendor_pay(amount_usdc=None, amount_sol="0.1", mint=USDC_DEVNET), "asset")
        # Naming the cluster's own USDC mint is fine.
        self.assertIn("paid", pay.pay(vendor_pay(mint=USDC_DEVNET)))

    def test_unknown_fields_are_refused(self):
        self.refused(vendor_pay(token="BONK"), "fields")
        self.refused(vendor_pay(amount_tokens="5"), "fields")

    def test_the_amount_is_exactly_one_positive_amount(self):
        self.refused(vendor_pay(amount_sol="1"), "amount")
        self.refused(vendor_pay(amount_usdc=None), "amount")
        self.refused(vendor_pay(amount_usdc="0"), "amount")
        self.refused(vendor_pay(amount_usdc="-1"), "amount")
        self.refused(vendor_pay(amount_usdc="1.0000001"), "amount")
        self.refused(vendor_pay(amount_usdc="NaN"), "amount")
        self.refused(vendor_pay(amount_usdc=True), "amount")

    def test_purpose_and_ref(self):
        self.refused(vendor_pay(purpose="tip"), "purpose")
        self.refused(vendor_pay(ref=""), "ref")

    # --- the address ---------------------------------------------------------------------------------

    def test_an_invalid_address_is_refused(self):
        for bad in ("", "not-an-address", "0" * 44, VENDOR[:-4], VENDOR + "1", "0x" + "1" * 40):
            self.refused(vendor_pay(to=bad), "address")

    def test_a_mint_is_not_a_recipient(self):
        self.refused(vendor_pay(to=USDC_DEVNET), "address")

    def test_never_to_its_own_address(self):
        reads.vendors_add("me", OWN, None)
        self.refused(vendor_pay(to=OWN), "own_address")

    def test_vendor_payments_only_to_the_allowlist(self):
        self.refused(vendor_pay(to=STRANGER), "vendor")
        reads.vendors_remove("hosting")
        self.refused(vendor_pay(), "vendor")

    # --- the caps ------------------------------------------------------------------------------------

    def test_per_transaction_cap(self):
        self.refused(vendor_pay(amount_usdc="50.000001"), "per_tx")
        self.refused(vendor_pay(amount_usdc=None, amount_sol="0.26"), "per_tx")  # $52 at 200
        self.assertIn("paid", pay.pay(vendor_pay(amount_usdc="50")))

    def test_daily_cap_over_a_rolling_24_hours(self):
        reads.vendors_add("api", STRANGER, None)
        pay.pay(vendor_pay(amount_usdc="50", ref="a"))
        self.at(T0 + HOUR)
        pay.pay(vendor_pay(to=STRANGER, amount_usdc="40", ref="b"))
        self.at(T0 + 2 * HOUR)
        err = self.refused(vendor_pay(to=STRANGER, amount_usdc="10.01", ref="c"), "per_day")
        self.assertIn("100.00", err.reason)
        self.at(T0 + DAY + 1)  # the first payment has left the window
        self.assertIn("paid", pay.pay(vendor_pay(to=STRANGER, amount_usdc="10", ref="c")))

    def test_daily_cap_counts_sol_and_usdc_together(self):
        reads.vendors_add("api", STRANGER, None)
        pay.pay(vendor_pay(amount_usdc="50", ref="a"))
        pay.pay(vendor_pay(to=STRANGER, amount_usdc=None, amount_sol="0.2", ref="b"))  # $40
        self.refused(vendor_pay(to=STRANGER, amount_usdc="10.01", ref="c"), "per_day")

    def test_per_recipient_weekly_cap(self):
        pay.pay(vendor_pay(amount_usdc="40", ref="a"))
        self.at(T0 + 2 * DAY)
        self.refused(vendor_pay(amount_usdc="20.01", ref="b"), "per_recipient_week")
        self.assertIn("paid", pay.pay(vendor_pay(amount_usdc="20", ref="b")))
        self.at(T0 + 7 * DAY + 1)
        self.assertIn("paid", pay.pay(vendor_pay(amount_usdc="40", ref="c")))

    def test_never_more_than_half_the_balance(self):
        self.chain.answers["getTokenAccountsByOwner"] = token_accounts(19_999_999)
        self.refused(vendor_pay(amount_usdc="10"), "half_balance")
        self.chain.answers["getTokenAccountsByOwner"] = token_accounts(20_000_000)
        self.assertIn("paid", pay.pay(vendor_pay(amount_usdc="10")))

    def test_half_balance_for_sol_reads_the_core(self):
        self.core.answers["openhuman.wallet_balances"] = balances(10**8)  # 0.1 SOL
        self.refused(vendor_pay(amount_usdc=None, amount_sol="0.06"), "half_balance")

    def test_a_usdc_balance_of_another_mint_does_not_count(self):
        self.chain.answers["getTokenAccountsByOwner"] = token_accounts(10**12, mint=STRANGER)
        self.refused(vendor_pay(), "half_balance")

    def test_an_unreadable_sol_balance_is_an_error_not_a_zero(self):
        self.core.answers["openhuman.wallet_balances"] = balances(10**10, status="missing")
        with self.assertRaises(hand.HandError):
            pay.pay(vendor_pay(amount_usdc=None, amount_sol="0.01"))
        self.assertEqual(self.moved(), [])

    def test_caps_must_be_set_and_sane(self):
        for key in caps.CAP_KEYS:
            self.configure(**{key: None})
            self.refused(vendor_pay(), "caps")
            self.configure(**{key: "0"})
            self.refused(vendor_pay(), "caps")
            self.configure(**{key: "1e9"})
            self.refused(vendor_pay(), "caps")
            self.configure(**{key: "lots"})
            self.refused(vendor_pay(), "caps")
        self.configure(WALLET_MAX_PER_TX_USD="150", WALLET_MAX_PER_DAY_USD="100")
        self.refused(vendor_pay(), "caps")
        self.configure(WALLET_CLUSTER="testnet")
        self.refused(vendor_pay(), "caps")

    # --- what can go wrong at the core ---------------------------------------------------------------------

    def test_a_payment_in_doubt_is_never_retried(self):
        self.core.answers["openhuman.wallet_execute_prepared"] = HttpStatus(502)
        with self.assertRaises(hand.HandError) as caught:
            pay.pay(vendor_pay())
        self.assertIn("in doubt", str(caught.exception))
        self.assertEqual(self.rows()[0]["status"], "in_doubt")
        self.core.answers["openhuman.wallet_execute_prepared"] = lambda p: {"transactionHash": "x" * 80}
        err = self.refused(vendor_pay(), "duplicate")
        self.assertEqual(err.detail["status"], "in_doubt")
        self.assertEqual(self.core.methods().count("openhuman.wallet_execute_prepared"), 1)

    def test_a_timeout_after_execute_is_in_doubt(self):
        from teeny_hands.wallet import pay as pay_module

        def slow_core(method, params=None, timeout=300.0):
            if method == "wallet_execute_prepared":
                timeout = 0.2
            return hand.core(method, params, timeout=timeout)

        self.core.delay["openhuman.wallet_execute_prepared"] = 1.0
        with self.assertRaises(hand.HandError):
            pay_module.pay(vendor_pay(), core=slow_core)
        self.assertEqual(self.rows()[0]["status"], "in_doubt")

    def test_an_execute_with_no_signature_is_in_doubt(self):
        self.core.answers["openhuman.wallet_execute_prepared"] = {"status": "broadcasted"}
        with self.assertRaises(hand.HandError):
            pay.pay(vendor_pay())
        self.assertEqual(self.rows()[0]["status"], "in_doubt")

    def test_in_doubt_counts_against_the_caps(self):
        reads.vendors_add("api", STRANGER, None)
        self.core.answers["openhuman.wallet_execute_prepared"] = Sequence([HttpStatus(500), lambda p: {"transactionHash": "s" * 80}])
        with self.assertRaises(hand.HandError):
            pay.pay(vendor_pay(amount_usdc="50", ref="a"))
        pay.pay(vendor_pay(to=STRANGER, amount_usdc="50", ref="b"))
        self.refused(vendor_pay(to=STRANGER, amount_usdc="1", ref="c"), "per_day")

    def test_the_cores_policy_refusal_is_a_refusal_and_not_counted(self):
        self.core.answers["openhuman.wallet_execute_prepared"] = Failure("WALLET_POLICY_REFUSED: over the daily cap for USDC")
        with self.assertRaises(hand.Refused) as caught:
            pay.pay(vendor_pay())
        self.assertEqual(caught.exception.detail["rule"], "core_policy")
        self.assertEqual(self.rows()[0]["status"], "refused")
        self.core.answers["openhuman.wallet_execute_prepared"] = lambda p: {"transactionHash": "t" * 80}
        self.assertIn("paid", pay.pay(vendor_pay()))  # a refused attempt moved nothing

    def test_a_failed_prepare_moved_nothing(self):
        self.core.answers["openhuman.wallet_prepare_transfer"] = Failure("no route")
        with self.assertRaises(hand.HandError):
            pay.pay(vendor_pay())
        self.assertEqual(self.rows()[0]["status"], "failed")
        self.assertNotIn("openhuman.wallet_execute_prepared", self.core.methods())

    def test_a_quote_that_does_not_match_is_never_executed(self):
        self.core.answers["openhuman.wallet_prepare_transfer"] = lambda p: {"quoteId": "q", "toAddress": STRANGER, "amountRaw": p["amountRaw"], "assetSymbol": "USDC"}
        with self.assertRaises(hand.Refused) as caught:
            pay.pay(vendor_pay())
        self.assertEqual(caught.exception.detail["rule"], "quote")
        self.assertNotIn("openhuman.wallet_execute_prepared", self.core.methods())
        self.assertEqual(self.rows()[0]["status"], "failed")

    def test_no_wallet_is_an_error(self):
        self.core.answers["openhuman.wallet_status"] = {"configured": False, "accounts": []}
        with self.assertRaises(hand.HandError) as caught:
            pay.pay(vendor_pay())
        self.assertIn("bin/wallet-setup", str(caught.exception))

    def test_no_wallet_still_fails_a_payment_loudly(self):
        # The observing jobs skip when there is no wallet yet (pay.NoWallet); moving money never does.
        self.core.answers["openhuman.wallet_status"] = {"configured": False, "accounts": []}
        with self.assertRaises(pay.NoWallet):
            pay.pay(vendor_pay())
        done = subprocess.run([sys.executable, "-I", "-B", str(WALLET), "pay"], input=json.dumps(vendor_pay()),
                              capture_output=True, text=True, env=os.environ, timeout=60)
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        out = json.loads(done.stdout)
        self.assertFalse(out["ok"])
        self.assertNotIn("skipped", out)
        self.assertIn("no Solana wallet", out["error"])
        self.assertEqual(self.moved(), [])

    # --- the command line ----------------------------------------------------------------------------

    def test_cli_pay_and_exit_codes(self):
        def run(*argv, body=None):
            done = subprocess.run([sys.executable, "-I", "-B", str(WALLET), *argv], input=json.dumps(body) if body else None,
                                  capture_output=True, text=True, env=os.environ, timeout=60,
                                  stdin=None if body else subprocess.DEVNULL)
            return done.returncode, json.loads(done.stdout)

        code, out = run("pay", body=vendor_pay())
        self.assertEqual((code, out["ok"]), (0, True))
        code, out = run("pay", body=vendor_pay(mint=TINY))
        self.assertEqual((code, out["refused"][:5], out["rule"]), (2, "Teeny", "tiny"))
        code, out = run("receipt", "1")
        self.assertEqual((code, out["receipt"]["for"]), (0, "hosting (inv-1)"))
        self.assertNotIn("core-token-FAKE", json.dumps(out))
