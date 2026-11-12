"""hands/wallet swap and bridge: only to Teeny's own accounts, only between
pinned assets, and only on a deBridge quote that says exactly what was
asked and loses no more than the cap allows. Nothing is approved or executed
before every rule has passed. The core and the chains are faked."""

from __future__ import annotations

from wallet.support import OWN, TINY, USDC_MAINNET, Scratch, token_accounts
from wallet.test_evm import EVM_OWN, MAINNET, balance_of_call, evm_balances
from teeny_hands import hand  # noqa: E402
from teeny_hands.wallet import chains, convert, store  # noqa: E402

BASE_USDC = chains.NETWORKS["base"].usdc["mainnet"]
DLN = "0xeF4fB24aD0916217251F553c0596F8Edc630EB66"
NOW_MS = 1_789_369_200_000
MOVING = ("openhuman.web3_swap_execute", "openhuman.web3_bridge_execute", "openhuman.web3_dapp_call", "openhuman.web3_dapp_execute")


def swap_quote(params, *, out_raw=None, min_raw=None, to=DLN, value=None, usd_in=40.0, usd_out=None, quote_id="sq-1"):
    """A deBridge same-chain swap answer for ETH->USDC on Base, by default
    0.01 ETH for 39.9 USDC."""
    amount = params["tokenInAmount"]
    out_raw = out_raw if out_raw is not None else "39900000"
    return {
        "quoteId": quote_id,
        "kind": "swap",
        "expiresAtMs": NOW_MS + 60_000,
        "signingChain": "evm",
        "quote": {
            "tokenIn": {"address": params["tokenIn"], "amount": amount, "decimals": 18, "symbol": "ETH", "approximateUsdValue": usd_in},
            "tokenOut": {"address": params["tokenOut"], "amount": out_raw, "minAmount": min_raw or out_raw, "decimals": 6, "symbol": "USDC",
                         "approximateUsdValue": usd_out if usd_out is not None else int(out_raw) / 1e6},
            "tx": {"to": to, "data": "0xdead", "value": value if value is not None else (amount if params["tokenIn"] == chains.EVM_NATIVE else "0")},
        },
    }


def bridge_quote(params, *, recommended="4980000", fix_fee="1000000000000000", quote_id="bq-1", expires=NOW_MS + 60_000):
    """A DLN order answer for USDC on Base -> USDC on Solana."""
    return {
        "quoteId": quote_id,
        "kind": "bridge",
        "expiresAtMs": expires,
        "signingChain": "evm",
        "quote": {
            "estimation": {
                "srcChainTokenIn": {"address": params["srcChainTokenIn"], "amount": params["srcChainTokenInAmount"], "decimals": 6, "approximateUsdValue": 5.0},
                "dstChainTokenOut": {"address": params["dstChainTokenOut"], "amount": "4990000", "recommendedAmount": recommended, "decimals": 6},
            },
            "tx": {"to": DLN, "data": "0xbeef", "value": fix_fee},
            "orderId": "0xorder",
            "fixFee": fix_fee,
        },
    }


def dapp_call(params):
    return {"quoteId": "dq-1", "kind": "dapp_call", "expiresAtMs": NOW_MS + 60_000, "signingChain": "evm",
            "quote": {"type": "dapp_call", "network": params["evmNetwork"], "contractAddress": params["contractAddress"],
                      "calldata": params["calldata"], "valueRaw": params.get("valueRaw", "0")}}


class ConvertTest(Scratch):
    settings = {**MAINNET, "WALLET_MAX_CONVERT_PER_DAY_USD": "80", "WALLET_APPROVAL_POLL_SECONDS": "0"}

    def setUp(self):
        super().setUp()
        self.configure(EVM_RPC_URL_BASE=self.chain.url)
        self.core.answers.update({
            "openhuman.wallet_balances": evm_balances(10**18),
            "openhuman.web3_swap_quote": swap_quote,
            "openhuman.web3_bridge_quote": bridge_quote,
            "openhuman.web3_swap_execute": lambda p: {"quoteId": p["quoteId"], "kind": "swap", "transactionHash": "0xswap"},
            "openhuman.web3_bridge_execute": lambda p: {"quoteId": p["quoteId"], "kind": "bridge", "transactionHash": "0xbridge"},
            "openhuman.web3_dapp_call": dapp_call,
            "openhuman.web3_dapp_execute": lambda p: {"quoteId": p["quoteId"], "transactionHash": "0xapprove"},
            "openhuman.wallet_tx_status": {"chain": "evm", "hash": "0xapprove", "state": "confirmed"},
        })
        self.chain.answers["getTokenAccountsByOwner"] = token_accounts(1000 * 10**6, USDC_MAINNET)
        self.chain.answers["eth_call"] = balance_of_call(500 * 10**6)

    def moved(self):
        return [m for m in self.core.methods() if m in MOVING]

    def refused(self, kind, body, rule):
        with self.assertRaises(hand.Refused) as caught:
            convert.convert(kind, body)
        self.assertEqual(caught.exception.detail.get("rule"), rule, caught.exception.reason)
        self.assertEqual(self.moved(), [], "a refusal must not approve or execute")
        return caught.exception

    def rows(self):
        conn = store.db()
        try:
            return [dict(r) for r in conn.execute("SELECT * FROM wallet_conversions ORDER BY id")]
        finally:
            conn.close()

    # --- what goes through --------------------------------------------------------------

    def test_a_swap_on_base_goes_to_teenys_own_address(self):
        out = convert.convert("swap", {"chain": "base", "from": "ETH", "to": "USDC", "amount": "0.01"})["sent"]
        self.assertEqual((out["kind"], out["signature"], out["recipient"]), ("swap", "0xswap", EVM_OWN))
        self.assertEqual(out["url"], "https://basescan.org/tx/0xswap")
        [params] = self.core.params("openhuman.web3_swap_quote")
        self.assertEqual(params["chainId"], 8453)
        self.assertEqual((params["tokenIn"], params["tokenOut"]), (chains.EVM_NATIVE, BASE_USDC))
        self.assertEqual((params["tokenOutRecipient"], params["senderAddress"]), (EVM_OWN, EVM_OWN))
        self.assertEqual(params["slippage"], "1")
        self.assertEqual(self.core.params("openhuman.web3_swap_execute"), [{"quoteId": "sq-1", "confirmed": True}])
        self.assertNotIn("openhuman.web3_dapp_call", self.core.methods(), "native ETH needs no allowance")
        [row] = self.rows()
        self.assertEqual((row["status"], row["usd_micro"], row["signature"]), ("sent", 40_000_000, "0xswap"))

    def test_a_bridge_from_base_usdc_approves_exactly_then_executes(self):
        out = convert.convert("bridge", {"from_chain": "base", "from": "USDC", "to_chain": "solana", "to": "USDC", "amount": "5"})["sent"]
        [quote] = self.core.params("openhuman.web3_bridge_quote")
        self.assertEqual((quote["srcChainId"], quote["dstChainId"]), (8453, chains.DEBRIDGE_SOLANA))
        self.assertEqual((quote["dstChainTokenOutRecipient"], quote["dstChainOrderAuthorityAddress"]), (OWN, OWN))
        self.assertEqual(quote["dstChainTokenOut"], USDC_MAINNET)
        [approve] = self.core.params("openhuman.web3_dapp_call")
        self.assertEqual(approve["contractAddress"], BASE_USDC)
        self.assertEqual(approve["calldata"], chains.approve_calldata(DLN, 5_000_000))
        methods = self.core.methods()
        self.assertLess(methods.index("openhuman.web3_dapp_execute"), methods.index("openhuman.web3_bridge_execute"))
        self.assertEqual((out["approval_tx"], out["order_id"]), ("0xapprove", "0xorder"))
        self.assertTrue(out["track"].endswith("0xorder"))

    def test_dry_run_quotes_and_checks_but_moves_nothing(self):
        self.configure(EVM_RPC_URL_BASE=self.chain.url, DRY_RUN="1")
        out = convert.convert("bridge", {"from_chain": "base", "from": "USDC", "to_chain": "solana", "to": "USDC", "amount": "5"})
        self.assertTrue(out["dry_run"])
        self.assertEqual(out["would_convert"]["minimum"], "4.98")
        self.assertEqual(self.moved(), [])
        self.assertEqual(self.rows(), [])

    # --- the rules ----------------------------------------------------------------------

    def test_tiny_is_never_traded(self):
        self.refused("swap", {"chain": "solana", "from": "SOL", "to": TINY, "amount": "1"}, "tiny")
        self.refused("swap", {"chain": "solana", "from": "$tiny", "to": "USDC", "amount": "1"}, "tiny")

    def test_an_unpinned_token_is_not_an_asset(self):
        self.refused("swap", {"chain": "base", "from": "ETH", "to": "PEPE", "amount": "0.01"}, "asset")

    def test_a_recipient_cannot_be_named(self):
        self.refused("swap", {"chain": "base", "from": "ETH", "to": "USDC", "amount": "0.01", "recipient": "0x" + "2" * 40}, "fields")

    def test_devnet_converts_nothing(self):
        self.configure(EVM_RPC_URL_BASE=self.chain.url, WALLET_CLUSTER="devnet")
        self.refused("swap", {"chain": "solana", "from": "SOL", "to": "USDC", "amount": "0.1"}, "cluster")

    def test_without_the_convert_cap_nothing_converts(self):
        self.configure(EVM_RPC_URL_BASE=self.chain.url, WALLET_MAX_CONVERT_PER_DAY_USD=None)
        self.refused("swap", {"chain": "base", "from": "ETH", "to": "USDC", "amount": "0.01"}, "caps")

    def test_a_bridge_must_cross_and_a_swap_must_change(self):
        self.refused("bridge", {"from_chain": "base", "from": "ETH", "to_chain": "base", "to": "USDC", "amount": "0.01"}, "kind")
        self.refused("swap", {"chain": "base", "from": "USDC", "to": "USDC", "amount": "1"}, "asset")

    def test_per_tx_counts_the_input(self):
        self.refused("swap", {"chain": "base", "from": "ETH", "to": "USDC", "amount": "0.02"}, "per_tx")  # $80 > $50

    def test_the_daily_convert_cap_counts_earlier_ones(self):
        convert.convert("swap", {"chain": "base", "from": "ETH", "to": "USDC", "amount": "0.01"})
        self.core.answers["openhuman.web3_swap_quote"] = lambda p: swap_quote(p, quote_id="sq-2")
        convert.convert("swap", {"chain": "base", "from": "ETH", "to": "USDC", "amount": "0.01"})
        with self.assertRaises(hand.Refused) as caught:
            convert.convert("swap", {"chain": "base", "from": "ETH", "to": "USDC", "amount": "0.001"})
        self.assertEqual(caught.exception.detail["rule"], "per_day_convert")

    def test_a_quote_that_loses_too_much_is_refused(self):
        self.core.answers["openhuman.web3_swap_quote"] = lambda p: swap_quote(p, out_raw="38000000")  # 5% on $40
        error = self.refused("swap", {"chain": "base", "from": "ETH", "to": "USDC", "amount": "0.01"}, "quote")
        self.assertIn("lose", error.reason)

    def test_the_minimum_after_slippage_is_what_counts(self):
        self.core.answers["openhuman.web3_swap_quote"] = lambda p: swap_quote(p, out_raw="39900000", min_raw="37000000")
        self.refused("swap", {"chain": "base", "from": "ETH", "to": "USDC", "amount": "0.01"}, "quote")

    def test_a_transaction_to_another_contract_is_refused(self):
        self.core.answers["openhuman.web3_swap_quote"] = lambda p: swap_quote(p, to="0x" + "9" * 40)
        error = self.refused("swap", {"chain": "base", "from": "ETH", "to": "USDC", "amount": "0.01"}, "quote")
        self.assertIn("not deBridge", error.reason)

    def test_a_quote_for_another_amount_or_token_is_refused(self):
        def wrong_amount(p):
            q = swap_quote(p)
            q["quote"]["tokenIn"]["amount"] = "1"
            return q

        def wrong_token(p):
            q = swap_quote(p)
            q["quote"]["tokenOut"]["address"] = "0x" + "7" * 40
            return q

        for answer in (wrong_amount, wrong_token):
            self.core.answers["openhuman.web3_swap_quote"] = answer
            self.refused("swap", {"chain": "base", "from": "ETH", "to": "USDC", "amount": "0.01"}, "quote")

    def test_a_flat_fee_counts_as_a_loss(self):
        # 0.002 ETH ($8 at the fixed price) on a $5 bridge
        self.core.answers["openhuman.web3_bridge_quote"] = lambda p: bridge_quote(p, fix_fee="2000000000000000")
        self.refused("bridge", {"from_chain": "base", "from": "USDC", "to_chain": "solana", "to": "USDC", "amount": "5"}, "quote")

    def test_half_balance_protects_the_input(self):
        self.core.answers["openhuman.wallet_balances"] = evm_balances(15 * 10**15)  # 0.015 ETH
        self.refused("swap", {"chain": "base", "from": "ETH", "to": "USDC", "amount": "0.01"}, "half_balance")

    # --- after the rules ----------------------------------------------------------------

    def test_an_expired_quote_is_fetched_again_after_the_approval(self):
        answers = [lambda p: bridge_quote(p, expires=NOW_MS + 5_000 + 10_001), lambda p: bridge_quote(p, quote_id="bq-2")]
        self.core.answers["openhuman.web3_bridge_quote"] = lambda p: answers.pop(0)(p)
        # The first quote passes at check time but is within the margin once the approval is done.
        import os
        os.environ["TEENY_NOW"] = str(NOW_MS / 1000)
        convert_now = convert._now_ms
        times = iter([NOW_MS, NOW_MS + 10_000, NOW_MS + 10_000])
        convert._now_ms = lambda: next(times, NOW_MS + 10_000)
        try:
            out = convert.convert("bridge", {"from_chain": "base", "from": "USDC", "to_chain": "solana", "to": "USDC", "amount": "5"})
        finally:
            convert._now_ms = convert_now
        self.assertEqual(self.core.params("openhuman.web3_bridge_execute"), [{"quoteId": "bq-2", "confirmed": True}])
        self.assertEqual(out["sent"]["conversion_id"], 1)

    def test_a_failed_approval_never_executes(self):
        self.core.answers["openhuman.wallet_tx_status"] = {"chain": "evm", "hash": "0xapprove", "state": "failed"}
        with self.assertRaises(hand.HandError):
            convert.convert("bridge", {"from_chain": "base", "from": "USDC", "to_chain": "solana", "to": "USDC", "amount": "5"})
        self.assertNotIn("openhuman.web3_bridge_execute", self.core.methods())
        self.assertEqual(self.rows()[0]["status"], "failed")

    def test_an_execute_that_does_not_come_back_is_in_doubt(self):
        from wallet.support import Failure
        self.core.answers["openhuman.web3_swap_execute"] = Failure("connection reset")
        with self.assertRaises(hand.HandError):
            convert.convert("swap", {"chain": "base", "from": "ETH", "to": "USDC", "amount": "0.01"})
        [row] = self.rows()
        self.assertEqual(row["status"], "in_doubt")
        with self.assertRaises(hand.Refused) as caught:  # it still counts
            convert.convert("swap", {"chain": "base", "from": "ETH", "to": "USDC", "amount": "0.011"})
        self.assertEqual(caught.exception.detail["rule"], "per_day_convert")
