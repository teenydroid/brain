"""hands/wallet buy-tiny: buys only, from a share of what Teeny earned, under
the caps; refuses any quote that is not USDC to $TINY, over the slippage or
the price impact; never signs under DRY_RUN or with TINY_BUY off. The core,
the chain and Jupiter are all faked (support.py and FakeJupiter below)."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import threading
import urllib.parse
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

from wallet.support import DAY, HOUR, OWN, STRANGER, T0, TINY, USDC_MAINNET, VENDOR, Failure, Scratch, balances
from teeny_hands import hand  # noqa: E402 - after support puts hands/lib on the path
from teeny_hands.wallet import buy, caps, pay, reads, store  # noqa: E402

WALLET = Path(__file__).resolve().parents[2] / "wallet"
REPO = Path(__file__).resolve().parents[3]

TEAM = "Hx6LbkMHe69DYawhPyVNs8Apa6tyfogfzQV6a7XkwKcE"
WSOL = "So11111111111111111111111111111111111111112"
USDC_ACCOUNT = "UsdcAcct11111111111111111111111111111111111"
TINY_ACCOUNT = "TinyAcct11111111111111111111111111111111111"
SIG = "4buySig" + "B" * 80
#: What moves funds through the core for a buy, or prepares to.
SWAP = ("openhuman.web3_swap_quote", "openhuman.web3_swap_execute")
RATE = 3000  # $TINY atomic units per USDC atomic unit, in the fake


class FakeJupiter:
    """Jupiter's quote API on 127.0.0.1: `answer(query)` builds the reply."""

    def __init__(self) -> None:
        self.queries: list[dict[str, str]] = []
        self.answer: Callable[[dict[str, str]], Any] = good_quote
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: Any) -> None:
                pass

            def do_GET(self) -> None:
                query = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(self.path).query))
                fake.queries.append(query)
                data = json.dumps(fake.answer(query)).encode()
                self.send_response(200)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True).start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_port}/swap/v1/quote"

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


def good_quote(query: dict[str, str], **overrides: Any) -> dict[str, Any]:
    amount = int(query["amount"])
    out = amount * RATE
    slippage = int(query["slippageBps"])
    body = {
        "inputMint": query["inputMint"],
        "inAmount": str(amount),
        "outputMint": query["outputMint"],
        "outAmount": str(out),
        "otherAmountThreshold": str(out * (10_000 - slippage) // 10_000),
        "swapMode": "ExactIn",
        "slippageBps": slippage,
        "priceImpactPct": "0.0005",
        "routePlan": [
            {"swapInfo": {"label": "Kipseli", "inputMint": query["inputMint"], "outputMint": WSOL, "inAmount": str(amount), "outAmount": "1"}, "percent": 100},
            {"swapInfo": {"label": "Pump.fun Amm", "inputMint": WSOL, "outputMint": query["outputMint"], "inAmount": "1", "outAmount": str(out)}, "percent": 100},
        ],
    }
    body.update(overrides)
    return body


def inflow(signature: str, at: float, raw: int, sender: str = STRANGER, signers: tuple[str, ...] | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    """A signature entry and its transaction: `sender` sends `raw` USDC to Teeny."""
    signers = signers if signers is not None else (sender,)
    entry = {"signature": signature, "slot": 1, "err": None, "blockTime": int(at)}
    tx = {
        "meta": {
            "err": None,
            "preTokenBalances": [
                {"accountIndex": 1, "mint": USDC_MAINNET, "owner": sender, "uiTokenAmount": {"amount": str(raw + 1)}},
                {"accountIndex": 2, "mint": USDC_MAINNET, "owner": OWN, "uiTokenAmount": {"amount": "0"}},
            ],
            "postTokenBalances": [
                {"accountIndex": 1, "mint": USDC_MAINNET, "owner": sender, "uiTokenAmount": {"amount": "1"}},
                {"accountIndex": 2, "mint": USDC_MAINNET, "owner": OWN, "uiTokenAmount": {"amount": str(raw)}},
            ],
        },
        "transaction": {"message": {"accountKeys": [{"pubkey": s, "signer": True} for s in signers] + [{"pubkey": OWN, "signer": False}]}},
    }
    return entry, tx


def parsed(raw: int, mint: str) -> dict[str, Any]:
    return {"data": {"parsed": {"info": {"mint": mint, "owner": OWN, "tokenAmount": {"amount": str(raw)}}}}}


class BuyTest(Scratch):
    settings = {
        "WALLET_CLUSTER": "mainnet",
        "TINY_BUY": "1",
        "TINY_BUY_SHARE": "0.5",
        "TINY_BUY_MAX_USDC_PER_DAY": "20",
        "TINY_BUY_MAX_USDC_PER_BUY": "10",
        "TINY_BUY_TEAM_ADDRESSES": TEAM,
    }

    def setUp(self) -> None:
        self.jupiter = FakeJupiter()
        self.addCleanup(self.jupiter.close)
        self.settings = {**self.settings, "JUPITER_QUOTE_URL": self.jupiter.url}
        super().setUp()
        self.usdc = {USDC_ACCOUNT: 1000 * 10**6}
        self.tiny: dict[str, int] = {}
        self.txs: dict[str, dict[str, Any]] = {}
        self.signatures: list[dict[str, Any]] = []
        self.give(inflow("in-1", T0 - HOUR, 30 * 10**6))
        self.sim: Callable[[list[Any]], Any] = self.good_simulation
        self.chain.answers.update(
            {
                "getTokenAccountsByOwner": self.token_accounts,
                "getSignaturesForAddress": lambda params: [s for s in self.signatures if "before" not in params[1]],
                "getTransaction": lambda params: self.txs[params[0]],
                "simulateTransaction": lambda params: self.sim(params),
            }
        )
        self.core.answers.update(
            {
                "openhuman.cost_report": {"totals": {"cost_usd": 0.0, "calls": 0}},
                "openhuman.x402_list_payments": {"payments": []},
                "openhuman.web3_swap_quote": self.prepared,
                "openhuman.web3_swap_execute": lambda p: {"quoteId": p["quoteId"], "kind": "swap", "transactionHash": SIG},
            }
        )

    # --- the fakes' answers ---------------------------------------------------------------------

    def give(self, pair: tuple[dict[str, Any], dict[str, Any]]) -> None:
        entry, tx = pair
        self.signatures.insert(0, entry)
        self.txs[entry["signature"]] = tx

    def token_accounts(self, params: list[Any]) -> dict[str, Any]:
        mint = params[1]["mint"]
        held = self.usdc if mint == USDC_MAINNET else self.tiny if mint == TINY else {}
        return {"value": [{"pubkey": k, "account": parsed(v, mint)} for k, v in held.items()]}

    def prepared(self, params: dict[str, Any], **overrides: Any) -> dict[str, Any]:
        out = int(params["tokenInAmount"]) * RATE
        body = {
            "tokenIn": {"address": params["tokenIn"], "amount": params["tokenInAmount"]},
            "tokenOut": {"address": params["tokenOut"], "amount": str(out), "minAmount": str(out * 9950 // 10_000)},
            "tx": {"data": "0x" + "ab" * 64},
        }
        body.update(overrides)
        return {"quoteId": "swap-q-1", "kind": "swap", "expiresAtMs": 1, "quote": body}

    def good_simulation(self, params: list[Any]) -> dict[str, Any]:
        addresses = params[1]["accounts"]["addresses"]
        amount = int(self.core.params("openhuman.web3_swap_quote")[-1]["tokenInAmount"])
        accounts = []
        for address in addresses:
            if address in self.usdc:
                accounts.append(parsed(self.usdc[address] - amount, USDC_MAINNET))
            else:
                accounts.append(parsed(self.tiny[address] + amount * RATE, TINY))
        return {"context": {"slot": 1}, "value": {"err": None, "logs": [], "accounts": accounts}}

    # --- helpers ---------------------------------------------------------------------------------

    def rows(self) -> list[dict[str, Any]]:
        conn = store.db()
        try:
            return [dict(r) for r in conn.execute("SELECT * FROM wallet_tiny_buys ORDER BY id")]
        finally:
            conn.close()

    def swapped(self) -> list[str]:
        return [m for m in self.core.methods() if m in SWAP]

    def refused(self, rule: str) -> hand.Refused:
        with self.assertRaises(hand.Refused) as caught:
            buy.buy_tiny()
        self.assertEqual(caught.exception.detail.get("rule"), rule, caught.exception.reason)
        self.assertNotIn("openhuman.web3_swap_execute", self.core.methods(), "a refusal never executes")
        return caught.exception

    # --- a buy that goes out -----------------------------------------------------------------------

    def test_a_buy_goes_out_through_the_core_and_is_recorded(self):
        out = buy.buy_tiny()["bought"]
        # 30 earned, nothing spent, half of it is 15, clamped to the 10 a buy.
        self.assertEqual((out["usdc"], out["usd"], out["tiny_out_raw"]), ("10", "10.00", str(10 * 10**6 * RATE)))
        self.assertEqual(out["route"], ["Kipseli", "Pump.fun Amm"])
        self.assertTrue(out["url"].startswith("https://solscan.io/tx/4buySig"))
        self.assertNotIn("cluster=devnet", out["url"])
        [params] = self.core.params("openhuman.web3_swap_quote")
        self.assertEqual(
            params,
            {"chainId": 7565164, "tokenIn": USDC_MAINNET, "tokenInAmount": "10000000", "tokenOut": TINY,
             "tokenOutRecipient": OWN, "senderAddress": OWN, "slippage": "0.5"},
        )
        self.assertEqual(self.core.params("openhuman.web3_swap_execute"), [{"quoteId": "swap-q-1", "confirmed": True}])
        self.assertEqual(self.chain.methods().count("simulateTransaction"), 1)
        sim = self.chain.params("simulateTransaction")[0]
        self.assertEqual(sim[1]["sigVerify"], False)
        self.assertLess(self.chain.methods().index("simulateTransaction"), len(self.chain.methods()))
        self.assertEqual(self.core.methods().index("openhuman.web3_swap_execute"), len(self.core.methods()) - 1)
        [row] = self.rows()
        self.assertEqual(
            (row["status"], row["usdc_raw"], row["usd_micro"], row["output_mint"], row["input_mint"], row["signature"], row["route"]),
            ("bought", 10 * 10**6, 10 * 10**6, TINY, USDC_MAINNET, SIG, "Kipseli > Pump.fun Amm"),
        )
        self.assertEqual((row["earned_micro"], row["spent_micro"], row["share"], row["slippage_bps"]), (30 * 10**6, 0, "0.5", 50))

    def test_the_ledger_and_the_receipts_carry_the_buy(self):
        buy.buy_tiny()
        [ledger_row] = reads.ledger(10)["tiny_buys"]
        self.assertEqual((ledger_row["id"], ledger_row["status"], ledger_row["amount"]), ("buy-1", "bought", "10"))
        [due] = reads.receipts_due()["due"]
        self.assertEqual((due["id"], due["kind"], due["asset"], due["amount"], due["signature"]), ("buy-1", "tiny_buy", "USDC", "10", SIG))
        self.assertIn("never sells", due["for"])
        self.assertEqual(reads.receipt_for("buy-1")["receipt"]["url"], due["url"])
        with self.assertRaises(hand.Refused):
            reads.receipt_for("buy-2")

    def test_a_buy_counts_against_the_wallets_daily_cap_for_payments_too(self):
        buy.buy_tiny()
        conn = store.db()
        try:
            self.assertEqual(store.spent_since(conn, T0 - DAY), 10 * 10**6)
            self.assertEqual(store.spent_since(conn, T0 - DAY, VENDOR), 0)
        finally:
            conn.close()

    # --- off, and the settings --------------------------------------------------------------------

    def test_off_unless_tiny_buy_is_set_and_nothing_is_asked(self):
        for value in (None, "0", "no"):
            self.configure(TINY_BUY=value)
            self.refused("off")
        self.assertEqual(self.core.methods(), [])
        self.assertEqual(self.chain.methods(), [])
        self.assertEqual(self.jupiter.queries, [])
        self.assertEqual(self.rows(), [])

    def test_devnet_is_refused(self):
        self.configure(WALLET_CLUSTER="devnet")
        self.refused("cluster")

    def test_settings_over_their_ceilings_or_missing_are_refused(self):
        for key, value in (
            ("TINY_BUY_SHARE", "0.51"),
            ("TINY_BUY_SHARE", "-0.1"),
            ("TINY_BUY_SHARE", "half"),
            ("TINY_BUY_MAX_USDC_PER_DAY", "101"),
            ("TINY_BUY_MAX_USDC_PER_DAY", None),
            ("TINY_BUY_MAX_USDC_PER_BUY", "51"),
            ("TINY_BUY_MAX_USDC_PER_BUY", "0"),
            ("TINY_BUY_MIN_USDC", "11"),
            ("TINY_BUY_SLIPPAGE_BPS", "101"),
            ("TINY_BUY_SLIPPAGE_BPS", "1.5"),
            ("TINY_BUY_MAX_PRICE_IMPACT_BPS", "500"),
            ("TINY_BUY_TEAM_ADDRESSES", None),
            ("TINY_BUY_TEAM_ADDRESSES", "not-an-address"),
            ("WALLET_MAX_PER_TX_USD", None),
        ):
            with self.subTest(key=key, value=value):
                self.configure(**{key: value})
                self.refused("caps")
        self.assertEqual(self.swapped(), [])

    def test_the_buy_caps_are_clamped_by_the_wallets_caps(self):
        wallet = caps.load({**dict(hand.secrets()), "WALLET_MAX_PER_TX_USD": "5", "WALLET_MAX_PER_DAY_USD": "8",
                            "WALLET_MAX_PER_RECIPIENT_WEEK_USD": "8"})
        config = buy.load_config({**dict(hand.secrets()), "TINY_BUY_MIN_USDC": "1"}, wallet)
        self.assertEqual((config.per_day_micro, config.per_buy_micro), (8 * 10**6, 5 * 10**6))
        self.assertEqual((config.slippage_bps, config.impact_bps, config.min_micro), (50, 50, 10**6))

    # --- the share math ---------------------------------------------------------------------------

    def test_share_of_net_inflows(self):
        self.assertEqual(buy.share_of(30 * 10**6, Decimal("0.5")), 15 * 10**6)
        self.assertEqual(buy.share_of(-5 * 10**6, Decimal("0.5")), 0)
        self.assertEqual(buy.share_of(3, Decimal("0.5")), 1)  # rounded down
        self.assertEqual(buy.share_of(10**6, Decimal("0")), 0)
        self.assertEqual(buy.clamp(15, [("per_buy", 10), ("per_tx", 50)]), (10, "per_buy"))
        self.assertEqual(buy.clamp(15, [("per_buy", 20), ("per_day", -3)]), (0, "per_day"))
        self.assertEqual(buy.clamp(4, [("per_buy", 10)]), (4, "share"))

    def test_running_costs_and_payouts_come_off_before_the_share(self):
        self.core.answers["openhuman.cost_report"] = {"totals": {"cost_usd": 12.0, "calls": 3}}
        self.configure(TINY_BUY_MAX_USDC_PER_BUY="50", TINY_BUY_MAX_USDC_PER_DAY="50")
        out = buy.buy_tiny()["bought"]
        self.assertEqual(out["usd"], "9.00")  # (30 - 12) x 0.5

    def test_team_money_and_teenys_own_transactions_are_not_earned(self):
        self.signatures.clear()
        self.give(inflow("team-sent", T0 - HOUR, 500 * 10**6, sender=TEAM))
        self.give(inflow("team-signed", T0 - HOUR, 500 * 10**6, signers=(TEAM,)))
        self.give(inflow("own-signed", T0 - HOUR, 500 * 10**6, signers=(OWN,)))
        self.give(inflow("earned", T0 - HOUR, 4 * 10**6))
        out = buy.buy_tiny()["bought"]
        self.assertEqual(out["usd"], "2.00")

    def test_only_inflows_since_the_last_buy_count(self):
        buy.buy_tiny()
        self.at(T0 + DAY)
        self.give(inflow("in-2", T0 + HOUR, 6 * 10**6))
        out = buy.buy_tiny()["bought"]
        self.assertEqual(out["usd"], "3.00")  # in-1 was before the last buy

    def test_under_the_minimum_is_skipped_and_recorded_and_carries_over(self):
        self.signatures.clear()
        self.give(inflow("small", T0 - HOUR, 1 * 10**6))
        out = buy.buy_tiny()
        self.assertIn("under the $1.00 minimum", out["skipped"])
        [row] = self.rows()
        self.assertEqual((row["status"], row["usdc_raw"]), ("skipped", 0))
        self.assertIn("minimum", row["reason"])
        self.assertEqual((self.jupiter.queries, self.swapped()), ([], []))
        # A skip does not reset the window: the next day the two add up.
        self.at(T0 + DAY)
        self.give(inflow("small-2", T0 + HOUR, 2 * 10**6))
        self.assertEqual(buy.buy_tiny()["bought"]["usd"], "1.50")

    def test_a_zero_share_is_skipped(self):
        self.configure(TINY_BUY_SHARE="0")
        out = buy.buy_tiny()
        self.assertEqual(out["skipped"], "TINY_BUY_SHARE is 0")
        self.assertEqual(self.swapped(), [])

    def test_costs_that_cannot_be_read_buy_nothing(self):
        self.core.answers.pop("openhuman.cost_report")
        out = buy.buy_tiny()
        self.assertIn("could not be read", out["skipped"])
        self.assertEqual(self.swapped(), [])

    # --- the caps ---------------------------------------------------------------------------------

    def test_the_daily_buy_cap_leaves_only_what_is_left(self):
        buy.buy_tiny()  # 10 of the 20 a day
        self.give(inflow("in-2", T0 + 1, 100 * 10**6))
        self.at(T0 + HOUR)
        self.configure(TINY_BUY_MAX_USDC_PER_BUY="50", TINY_BUY_MAX_USDC_PER_DAY="15")
        out = buy.buy_tiny()["bought"]
        self.assertEqual(out["usd"], "5.00")

    def test_the_wallets_daily_cap_counts_payments_and_buys_together(self):
        conn = store.db()
        try:
            store.insert_pending(conn, cluster="mainnet", asset="USDC", amount_raw=96 * 10**6, usd_micro=96 * 10**6,
                                 to_address=VENDOR, purpose="vendor", ref="inv-9")
        finally:
            conn.close()
        out = buy.buy_tiny()["bought"]
        self.assertEqual(out["usd"], "4.00")  # 100 a day, 96 already out

    def test_never_more_than_half_the_usdc_held(self):
        self.usdc = {USDC_ACCOUNT: 6 * 10**6}
        out = buy.buy_tiny()["bought"]
        self.assertEqual(out["usd"], "3.00")

    # --- the direction guard ----------------------------------------------------------------------

    def test_a_quote_whose_output_is_not_tiny_is_refused(self):
        self.jupiter.answer = lambda q: good_quote(q, outputMint=WSOL)
        self.refused("direction")
        self.assertEqual((self.swapped(), self.rows()), ([], []))

    def test_a_quote_whose_input_is_not_usdc_is_refused(self):
        for mint in (WSOL, TINY, "4zMMC9srt5Ri5X14GAgXhaHii3GnPAEERYPJgZJDncDU"):
            with self.subTest(mint=mint):
                self.jupiter.answer = lambda q, mint=mint: good_quote(q, inputMint=mint)
                self.refused("direction")
        self.assertEqual(self.swapped(), [])

    def test_a_quote_for_another_amount_is_refused(self):
        self.jupiter.answer = lambda q: good_quote(q, inAmount=str(int(q["amount"]) * 2))
        self.refused("direction")

    def test_a_route_that_does_not_end_in_tiny_is_refused(self):
        def leaky(q):
            body = good_quote(q)
            body["routePlan"].append({"swapInfo": {"label": "x", "inputMint": WSOL, "outputMint": VENDOR}, "percent": 10})
            return body

        self.jupiter.answer = leaky
        self.refused("route")
        self.jupiter.answer = lambda q: good_quote(q, routePlan=[])
        self.refused("route")

    def test_the_quote_always_asks_for_usdc_to_tiny(self):
        buy.buy_tiny()
        for query in self.jupiter.queries:
            self.assertEqual((query["inputMint"], query["outputMint"], query["swapMode"]), (USDC_MAINNET, TINY, "ExactIn"))

    def test_the_core_quote_must_match_or_nothing_is_signed(self):
        for overrides in (
            {"tokenOut": {"address": WSOL, "amount": "1", "minAmount": "1"}},
            {"tokenIn": {"address": TINY, "amount": "10000000"}},
            {"tokenIn": {"address": USDC_MAINNET, "amount": "20000000"}},
            {"tokenOut": {"address": TINY, "amount": "1", "minAmount": "1"}},  # below Jupiter's minimum
            {"tokenOutRecipient": STRANGER},
            {"tx": {}},
        ):
            with self.subTest(overrides=overrides):
                self.core.answers["openhuman.web3_swap_quote"] = lambda p, o=overrides: self.prepared(p, **o)
                self.refused("quote")
                self.assertEqual(self.rows()[-1]["status"], "failed")
                self.assertNotIn("simulateTransaction", self.chain.methods())

    def test_a_core_that_cannot_prepare_is_a_failure_and_nothing_moves(self):
        self.core.answers["openhuman.web3_swap_quote"] = Failure("BACKEND_UNAVAILABLE: no wallet backend")
        with self.assertRaises(hand.HandError):
            buy.buy_tiny()
        self.assertEqual(self.rows()[0]["status"], "failed")
        self.assertNotIn("openhuman.web3_swap_execute", self.core.methods())

    # --- slippage and price impact ----------------------------------------------------------------

    def test_slippage_over_the_cap_is_refused(self):
        self.jupiter.answer = lambda q: good_quote(q, slippageBps=300)
        self.refused("slippage")
        self.jupiter.answer = lambda q: good_quote(q, otherAmountThreshold="1")
        self.refused("slippage")
        self.assertEqual(self.swapped(), [])

    def test_the_slippage_asked_for_is_the_capped_one(self):
        self.configure(TINY_BUY_SLIPPAGE_BPS="100")
        buy.buy_tiny()
        self.assertEqual({q["slippageBps"] for q in self.jupiter.queries}, {"100"})
        self.assertEqual(self.core.params("openhuman.web3_swap_quote")[0]["slippage"], "1")

    def test_a_reported_price_impact_over_the_threshold_is_refused(self):
        self.jupiter.answer = lambda q: good_quote(q, priceImpactPct="0.006")  # 60 bps, over 50
        self.refused("price_impact")
        self.jupiter.answer = lambda q: good_quote(q, priceImpactPct=None)
        self.refused("price_impact")
        self.assertEqual(self.swapped(), [])

    def test_a_measured_price_impact_over_the_threshold_is_refused(self):
        # The big buy gets 2% fewer $TINY a USDC than the small reference does,
        # whatever the quote says its impact is.
        def thin(q):
            amount = int(q["amount"])
            body = good_quote(q)
            if amount > 1_000_000:
                out = amount * RATE * 98 // 100
                body.update(outAmount=str(out), otherAmountThreshold=str(out * 9950 // 10_000))
            return body

        self.jupiter.answer = thin
        self.refused("price_impact")
        self.assertEqual(self.swapped(), [])
        self.assertEqual(buy.measured_impact_bps(100, 98, 10, 10), 200)
        self.assertEqual(buy.measured_impact_bps(100, 100, 10, 10), 0)

    # --- the simulation ---------------------------------------------------------------------------

    def test_a_failing_simulation_is_never_signed(self):
        self.sim = lambda params: {"value": {"err": {"InstructionError": [2, "Custom"]}, "accounts": None}}
        self.refused("simulate")
        self.assertEqual(self.rows()[0]["status"], "failed")

    def test_a_simulation_that_takes_more_usdc_than_the_buy_is_never_signed(self):
        self.sim = lambda params: {"value": {"err": None, "accounts": [parsed(0, USDC_MAINNET)]}}
        self.refused("simulate")

    def test_a_simulation_that_gives_too_little_tiny_is_never_signed(self):
        self.tiny = {TINY_ACCOUNT: 5}
        amount = 10 * 10**6
        self.sim = lambda params: {"value": {"err": None, "accounts": [parsed(1000 * 10**6 - amount, USDC_MAINNET), parsed(6, TINY)]}}
        self.refused("simulate")

    def test_an_execute_that_does_not_come_back_is_in_doubt_and_counted(self):
        self.core.answers["openhuman.web3_swap_execute"] = Failure("timeout")
        with self.assertRaises(hand.HandError) as caught:
            buy.buy_tiny()
        self.assertIn("in doubt", str(caught.exception))
        [row] = self.rows()
        self.assertEqual(row["status"], "in_doubt")
        conn = store.db()
        try:
            self.assertEqual(store.bought_since(conn, T0 - DAY), 10 * 10**6)
        finally:
            conn.close()

    # --- DRY_RUN ------------------------------------------------------------------------------------

    def test_dry_run_quotes_only_and_never_signs(self):
        self.configure(DRY_RUN="1")
        out = buy.buy_tiny()
        self.assertTrue(out["dry_run"])
        self.assertEqual(out["would_buy"]["usd"], "10.00")
        self.assertEqual({c["rule"] for c in out["checks"]} >= {"off", "caps", "amount", "direction", "route", "slippage", "price_impact"}, True)
        self.assertEqual(self.swapped(), [])
        self.assertNotIn("simulateTransaction", self.chain.methods())
        self.assertEqual(self.rows(), [])
        self.assertEqual(len(self.jupiter.queries), 2)

    def test_dry_run_from_the_environment_cannot_be_turned_off(self):
        os.environ["DRY_RUN"] = "1"
        self.assertTrue(buy.buy_tiny()["dry_run"])
        self.assertEqual(self.swapped(), [])

    def test_a_dry_run_skip_writes_nothing(self):
        self.configure(DRY_RUN="1", TINY_BUY_SHARE="0")
        self.assertEqual(buy.buy_tiny()["skipped"], "TINY_BUY_SHARE is 0")
        self.assertEqual(self.rows(), [])

    # --- no sell path ------------------------------------------------------------------------------

    def test_the_table_refuses_any_output_but_tiny(self):
        conn = store.db()
        try:
            with self.assertRaises(sqlite3.IntegrityError):
                store.insert_buy(conn, cluster="mainnet", status="pending", input_mint=TINY, output_mint=USDC_MAINNET,
                                 usdc_raw=1, usd_micro=1, since=0, earned_micro=0, spent_micro=0, share="0.5")
        finally:
            conn.close()

    def test_the_verb_takes_no_option_and_no_body(self):
        def run(*argv, body=None):
            done = subprocess.run([sys.executable, "-I", "-B", str(WALLET), *argv], input=body, capture_output=True,
                                  text=True, env=os.environ, timeout=60)
            return done.returncode, json.loads(done.stdout)

        for argv in (("buy-tiny", "--output-mint", USDC_MAINNET), ("buy-tiny", "sell"), ("sell-tiny",)):
            code, out = run(*argv)
            self.assertEqual((code, out["ok"]), (1, False), argv)
        code, out = run("buy-tiny", body=json.dumps({"outputMint": USDC_MAINNET}))
        self.assertEqual((code, out["ok"]), (0, True))
        self.assertEqual(self.rows()[0]["output_mint"], TINY)
        self.assertEqual(self.core.params("openhuman.web3_swap_quote")[0]["tokenOut"], TINY)
        self.assertNotIn("core-token-FAKE", json.dumps(out))

    def test_buy_has_no_sell_path(self):
        source = (REPO / "hands" / "lib" / "teeny_hands" / "wallet" / "buy.py").read_text()
        self.assertNotIn("def sell", source)
        self.assertEqual(buy.OUTPUT_MINT, caps.TINY_MINT)

    def test_pay_still_never_sends_tiny(self):
        with self.assertRaises(hand.Refused) as caught:
            pay.pay({"to": STRANGER, "amount_usdc": "1", "purpose": "vendor", "ref": "x", "mint": TINY})
        self.assertEqual(caught.exception.detail["rule"], "tiny")


class PinnedJobTest(Scratch):
    def test_buy_tiny_is_not_scheduled_for_now(self):
        # The operator took the daily buy off the schedule (2026-11-12); the
        # script stays, so putting it back is the commented block in the file.
        import tomllib

        jobs = tomllib.loads((REPO / "evals" / "cron.pinned.toml").read_text())["jobs"]
        self.assertNotIn("tiny-buy", jobs)
        self.assertFalse(any(j.get("command") == "bin/tiny-buy" for j in jobs.values()))
        script = (REPO / "bin" / "tiny-buy").read_text()
        self.assertIn('"$hands/wallet" buy-tiny', script)
        self.assertTrue(os.access(REPO / "bin" / "tiny-buy", os.X_OK))

    def test_the_playbook_loader_refuses_buy_tiny(self):
        sys.path.insert(0, str(REPO / "bin" / "lib"))
        import playbook

        brain = self.root / "brain"
        (brain / "playbooks").mkdir(parents=True)
        for front in ('output = "hand:wallet buy-tiny"', 'output = "post"\n[[inputs]]\nrun = "hands/wallet buy-tiny"'):
            (brain / "playbooks" / "greedy.md").write_text(f'+++\ndescription = "x"\n{front}\n+++\nbuy')
            with self.assertRaises(playbook.PlaybookError):
                playbook.load(brain, "greedy")
