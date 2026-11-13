"""hands/wallet swap and bridge: only to Teeny's own accounts, only between
pinned assets, and only on a quote (LI.FI's by default, deBridge's when the
operator or the call says so) that says exactly what was asked and loses no
more than the cap allows. Nothing is approved or executed before every rule
has passed. The core and the chains are faked; the LI.FI answers are
recorded ones (fixtures/lifi), quoted for the test addresses."""

from __future__ import annotations

import base64
import copy
import json
from pathlib import Path

from wallet.support import OWN, TINY, USDC_MAINNET, Scratch, token_accounts
from wallet.test_evm import EVM_OWN, MAINNET, balance_of_call, evm_balances
from teeny_hands import hand  # noqa: E402
from teeny_hands.wallet import chains, convert, store  # noqa: E402

BASE_USDC = chains.NETWORKS["base"].usdc["mainnet"]
DLN = "0xeF4fB24aD0916217251F553c0596F8Edc630EB66"
DIAMOND = "0x1231DEB6f5749EF6cE6943a275A1D3E7486F4EaE"
NOW_MS = 1_789_369_200_000
MOVING = ("openhuman.web3_swap_execute", "openhuman.web3_bridge_execute", "openhuman.web3_dapp_call", "openhuman.web3_dapp_execute")
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "lifi"
LIFI_SOLANA = 1151111081099710
SOL_PRE = 10 * 10**9
SOL_USDC_HELD = 1000 * 10**6


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


def bridge_quote(params, *, recommended=None, fix_fee="200000000000000", quote_id="bq-1", expires=NOW_MS + 60_000):
    """A DLN order answer for USDC on Base -> USDC on Solana: 0.25% less
    out, and a 0.0002 ETH flat fee ($0.80 at the fixed price)."""
    amount = int(params["srcChainTokenInAmount"])
    recommended = recommended or str(amount * 9975 // 10000)
    return {
        "quoteId": quote_id,
        "kind": "bridge",
        "expiresAtMs": expires,
        "signingChain": "evm",
        "quote": {
            "estimation": {
                "srcChainTokenIn": {"address": params["srcChainTokenIn"], "amount": params["srcChainTokenInAmount"], "decimals": 6, "approximateUsdValue": amount / 1e6},
                "dstChainTokenOut": {"address": params["dstChainTokenOut"], "amount": str(amount * 9990 // 10000), "recommendedAmount": recommended, "decimals": 6},
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


class DeBridgeConvertTest(Scratch):
    settings = {**MAINNET, "WALLET_MAX_CONVERT_PER_DAY_USD": "80", "WALLET_APPROVAL_POLL_SECONDS": "0", "WALLET_CONVERT_PROVIDER": "debridge"}

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
        self.assertEqual(params["slippage"], "debridge:1", "the spine routes on the tag and takes it off")
        self.assertEqual(self.core.params("openhuman.web3_swap_execute"), [{"quoteId": "sq-1", "confirmed": True}])
        self.assertNotIn("openhuman.web3_dapp_call", self.core.methods(), "native ETH needs no allowance")
        [row] = self.rows()
        self.assertEqual((row["status"], row["usd_micro"], row["signature"]), ("sent", 40_000_000, "0xswap"))

    def test_a_bridge_from_base_usdc_approves_exactly_then_executes(self):
        out = convert.convert("bridge", {"from_chain": "base", "from": "USDC", "to_chain": "solana", "to": "USDC", "amount": "40"})["sent"]
        [quote] = self.core.params("openhuman.web3_bridge_quote")
        self.assertEqual((quote["srcChainId"], quote["dstChainId"]), (8453, chains.DEBRIDGE_SOLANA))
        self.assertEqual((quote["dstChainTokenOutRecipient"], quote["dstChainOrderAuthorityAddress"]), (OWN, OWN))
        self.assertEqual(quote["dstChainTokenOut"], USDC_MAINNET)
        self.assertEqual(quote["dstChainTokenOutAmount"], "debridge:auto")
        [approve] = self.core.params("openhuman.web3_dapp_call")
        self.assertEqual(approve["contractAddress"], BASE_USDC)
        self.assertEqual(approve["calldata"], chains.approve_calldata(DLN, 40_000_000))
        methods = self.core.methods()
        self.assertLess(methods.index("openhuman.web3_dapp_execute"), methods.index("openhuman.web3_bridge_execute"))
        self.assertEqual((out["approval_tx"], out["order_id"]), ("0xapprove", "0xorder"))
        self.assertTrue(out["track"].endswith("0xorder"))
        [row] = self.rows()
        self.assertEqual((row["provider"], row["order_id"]), ("debridge", "0xorder"))

    def test_dry_run_quotes_and_checks_but_moves_nothing(self):
        self.configure(EVM_RPC_URL_BASE=self.chain.url, DRY_RUN="1")
        out = convert.convert("bridge", {"from_chain": "base", "from": "USDC", "to_chain": "solana", "to": "USDC", "amount": "40"})
        self.assertTrue(out["dry_run"])
        self.assertEqual(out["would_convert"]["minimum"], "39.9")
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
        # 0.002 ETH ($8 at the fixed price) on a $40 bridge
        self.core.answers["openhuman.web3_bridge_quote"] = lambda p: bridge_quote(p, fix_fee="2000000000000000")
        self.refused("bridge", {"from_chain": "base", "from": "USDC", "to_chain": "solana", "to": "USDC", "amount": "40"}, "quote")

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
            out = convert.convert("bridge", {"from_chain": "base", "from": "USDC", "to_chain": "solana", "to": "USDC", "amount": "40"})
        finally:
            convert._now_ms = convert_now
        self.assertEqual(self.core.params("openhuman.web3_bridge_execute"), [{"quoteId": "bq-2", "confirmed": True}])
        self.assertEqual(out["sent"]["conversion_id"], 1)

    def test_a_failed_approval_never_executes(self):
        self.core.answers["openhuman.wallet_tx_status"] = {"chain": "evm", "hash": "0xapprove", "state": "failed"}
        with self.assertRaises(hand.HandError):
            convert.convert("bridge", {"from_chain": "base", "from": "USDC", "to_chain": "solana", "to": "USDC", "amount": "40"})
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


# --- LI.FI ------------------------------------------------------------------------------


def recorded(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text())


def envelope(raw: dict, kind: str, quote_id: str = "lq-1") -> dict:
    """What the core answers for a LI.FI quote: the spine's translation
    (spine/src/lifi.rs: `transactionRequest` becomes the `tx` the module
    signs, Solana base64 as 0x hex, EVM value in decimal wei) inside the
    module's envelope."""
    raw = copy.deepcopy(raw)
    request = raw.pop("transactionRequest")
    solana = raw["action"]["fromChainId"] == LIFI_SOLANA
    if solana:
        tx = {"data": "0x" + base64.b64decode(request["data"]).hex()}
    else:
        tx = {"to": request["to"], "data": request["data"], "value": str(int(request.get("value") or "0x0", 16))}
    raw.update(tx=tx, provider="lifi")
    return {"quoteId": quote_id, "kind": kind, "expiresAtMs": NOW_MS + 60_000, "signingChain": "solana" if solana else "evm", "quote": raw}


def with_tx_bytes(raw: dict, change) -> dict:
    """The recorded Solana transaction, its bytes changed."""
    raw = copy.deepcopy(raw)
    wire = bytearray(base64.b64decode(raw["transactionRequest"]["data"]))
    change(wire)
    raw["transactionRequest"]["data"] = base64.b64encode(bytes(wire)).decode()
    return raw


class LifiTest(Scratch):
    settings = {**MAINNET, "WALLET_MAX_CONVERT_PER_DAY_USD": "80", "WALLET_APPROVAL_POLL_SECONDS": "0"}

    def setUp(self):
        super().setUp()
        self.configure(EVM_RPC_URL_BASE=self.chain.url)
        self.core.answers.update({
            "openhuman.wallet_balances": evm_balances(10**18),
            "openhuman.web3_swap_execute": lambda p: {"quoteId": p["quoteId"], "kind": "swap", "transactionHash": "5swapSig"},
            "openhuman.web3_bridge_execute": lambda p: {"quoteId": p["quoteId"], "kind": "bridge", "transactionHash": "5bridgeSig"},
            "openhuman.web3_dapp_call": dapp_call,
            "openhuman.web3_dapp_execute": lambda p: {"quoteId": p["quoteId"], "transactionHash": "0xapprove"},
            "openhuman.wallet_tx_status": {"chain": "evm", "hash": "0xapprove", "state": "confirmed"},
        })
        self.chain.answers["getTokenAccountsByOwner"] = token_accounts(SOL_USDC_HELD, USDC_MAINNET)
        self.chain.answers["eth_call"] = balance_of_call(500 * 10**6)
        self.chain.answers["getBalance"] = {"context": {"slot": 1}, "value": SOL_PRE}

    def lifi(self, kind: str, raw: dict) -> None:
        self.core.answers[f"openhuman.web3_{kind}_quote"] = lambda p: envelope(raw, kind)

    def simulate(self, debit: int, usdc_taken: int = 0, err=None) -> None:
        """The Solana simulation: Teeny's account `debit` lamports lighter,
        its USDC account `usdc_taken` lighter."""
        def answer(params):
            out = []
            for address in params[1]["accounts"]["addresses"]:
                if address == OWN:
                    out.append({"lamports": SOL_PRE - debit, "data": ["", "base64"], "owner": chains.SOL_NATIVE})
                else:
                    out.append({"lamports": 2039280, "data": {"parsed": {"info": {
                        "mint": USDC_MAINNET, "owner": OWN, "tokenAmount": {"amount": str(SOL_USDC_HELD - usdc_taken), "decimals": 6}}}}})
            return {"context": {"slot": 1}, "value": {"err": err, "accounts": out, "logs": []}}
        self.chain.answers["simulateTransaction"] = answer

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

    SOL_TO_BASE = {"from_chain": "solana", "from": "SOL", "to_chain": "base", "to": "USDC", "amount": "0.1"}
    SOL_SWAP = {"chain": "solana", "from": "SOL", "to": "USDC", "amount": "0.1"}
    BASE_TO_ARB = {"from_chain": "base", "from": "USDC", "to_chain": "arbitrum", "to": "USDC", "amount": "20"}
    SOL_USDC_TO_BASE = {"from_chain": "solana", "from": "USDC", "to_chain": "base", "to": "USDC", "amount": "20"}

    # --- what goes through --------------------------------------------------------------

    def test_a_bridge_from_solana_lands_at_teenys_own_base_address(self):
        raw = recorded("sol_to_base_usdc")
        self.lifi("bridge", raw)
        self.simulate(100_000_000 + 146_122)  # the input and the network fee
        out = convert.convert("bridge", self.SOL_TO_BASE)["sent"]
        [params] = self.core.params("openhuman.web3_bridge_quote")
        self.assertEqual((params["srcChainId"], params["dstChainId"]), (chains.DEBRIDGE_SOLANA, 8453))
        self.assertEqual(params["dstChainTokenOutAmount"], "lifi:auto")
        self.assertEqual((params["srcChainOrderAuthorityAddress"], params["dstChainTokenOutRecipient"]), (OWN, EVM_OWN))
        [sim] = self.chain.params("simulateTransaction")
        self.assertEqual(sim[0], raw["transactionRequest"]["data"], "the very transaction the module signs")
        self.assertEqual((sim[1]["sigVerify"], sim[1]["replaceRecentBlockhash"]), (False, True))
        self.assertEqual(self.core.params("openhuman.web3_bridge_execute"), [{"quoteId": "lq-1", "confirmed": True}])
        self.assertEqual((out["provider"], out["tool"], out["minimum"]), ("lifi", "mayanFastMCTP", "7.894619"))
        self.assertEqual(out["track"], "https://scan.li.fi/tx/5bridgeSig")
        [row] = self.rows()
        self.assertEqual((row["status"], row["provider"], row["tool"], row["signature"]), ("sent", "lifi", "mayanFastMCTP", "5bridgeSig"))
        listed = convert.conversions(5)["conversions"][0]
        self.assertEqual(listed["track"], "https://scan.li.fi/tx/5bridgeSig")

    def test_a_swap_on_solana_goes_through_lifi_with_the_capped_slippage(self):
        self.lifi("swap", recorded("sol_to_usdc_swap"))
        self.simulate(100_000_000 + 179_997)
        out = convert.convert("swap", self.SOL_SWAP)["sent"]
        [params] = self.core.params("openhuman.web3_swap_quote")
        self.assertEqual((params["chainId"], params["tokenOutRecipient"], params["senderAddress"]), (chains.DEBRIDGE_SOLANA, OWN, OWN))
        self.assertEqual(params["slippage"], "lifi:1")
        self.assertEqual((out["signature"], out["tool"]), ("5swapSig", "jupiter"))
        self.assertNotIn("track", out, "a swap has nothing to track")

    def test_solana_usdc_with_a_cosigner_who_already_signed_goes_through(self):
        # Mayan's USDC route adds a key of its own, signed before it is sent.
        self.lifi("bridge", recorded("sol_usdc_to_base_usdc"))
        self.simulate(5_000 + 10_000, usdc_taken=20 * 10**6)
        out = convert.convert("bridge", self.SOL_USDC_TO_BASE)["sent"]
        self.assertEqual(out["tool"], "mayanFastMCTP")
        [sim] = self.chain.params("simulateTransaction")
        self.assertEqual(sim[1]["accounts"]["addresses"], [OWN, "TokenAcct1111111111111111111111111111111111"])

    def test_an_evm_bridge_approves_exactly_the_diamond_then_executes(self):
        self.lifi("bridge", recorded("base_usdc_to_arbitrum"))
        out = convert.convert("bridge", self.BASE_TO_ARB)["sent"]
        [approve] = self.core.params("openhuman.web3_dapp_call")
        self.assertEqual(approve["contractAddress"], BASE_USDC)
        self.assertEqual(approve["calldata"], chains.approve_calldata(DIAMOND, 20_000_000))
        methods = self.core.methods()
        self.assertLess(methods.index("openhuman.web3_dapp_execute"), methods.index("openhuman.web3_bridge_execute"))
        self.assertEqual((out["approval_tx"], out["tool"], out["minimum"]), ("0xapprove", "polymerStandard", "19.95"))
        self.assertNotIn("simulateTransaction", self.chain.methods(), "an EVM source is not simulated on Solana")

    def test_an_eth_swap_sends_exactly_the_input_to_the_diamond(self):
        self.lifi("swap", recorded("base_eth_to_usdc_swap"))
        out = convert.convert("swap", {"chain": "base", "from": "ETH", "to": "USDC", "amount": "0.005"})["sent"]
        self.assertEqual((out["signature"], out["tool"]), ("5swapSig", "nordstern"))
        self.assertNotIn("openhuman.web3_dapp_call", self.core.methods(), "native ETH needs no allowance")

    def test_dry_run_quotes_checks_and_simulates_but_moves_nothing(self):
        self.configure(EVM_RPC_URL_BASE=self.chain.url, DRY_RUN="1")
        self.lifi("bridge", recorded("sol_to_base_usdc"))
        self.simulate(100_000_000 + 146_122)
        out = convert.convert("bridge", self.SOL_TO_BASE)
        self.assertTrue(out["dry_run"])
        self.assertEqual((out["would_convert"]["provider"], out["would_convert"]["tool"]), ("lifi", "mayanFastMCTP"))
        self.assertEqual(self.chain.methods().count("simulateTransaction"), 1)
        self.assertEqual(self.moved(), [])
        self.assertEqual(self.rows(), [])

    # --- the quote ----------------------------------------------------------------------

    def test_a_transaction_to_a_contract_that_is_not_the_diamond_is_refused(self):
        raw = recorded("base_eth_to_usdc_swap")
        raw["transactionRequest"]["to"] = "0x" + "9" * 40
        self.lifi("swap", raw)
        error = self.refused("swap", {"chain": "base", "from": "ETH", "to": "USDC", "amount": "0.005"}, "quote")
        self.assertIn("LI.FI", error.reason)

    def test_an_approval_to_anything_but_the_diamond_is_refused(self):
        raw = recorded("base_usdc_to_arbitrum")
        raw["estimate"]["approvalAddress"] = "0x" + "9" * 40
        self.lifi("bridge", raw)
        error = self.refused("bridge", self.BASE_TO_ARB, "quote")
        self.assertIn("approval", error.reason)

    def test_another_recipient_is_refused(self):
        raw = recorded("base_usdc_to_arbitrum")
        raw["action"]["toAddress"] = "0x" + "2" * 40
        self.lifi("bridge", raw)
        self.refused("bridge", self.BASE_TO_ARB, "quote")

    def test_a_transaction_that_does_not_name_the_recipient_is_refused(self):
        # NEAR Intents: a deposit address, the recipient kept off-chain.
        self.lifi("bridge", recorded("sol_usdc_to_base_usdc_near"))
        self.simulate(5_000, usdc_taken=20 * 10**6)
        error = self.refused("bridge", self.SOL_USDC_TO_BASE, "quote")
        self.assertIn("recipient", error.reason)

    def test_a_quote_for_another_amount_or_token_is_refused(self):
        for path, value in ((("action", "fromAmount"), "1"), (("action", "toToken", "address"), "0x" + "7" * 40),
                            (("action", "fromToken", "address"), "0x" + "7" * 40), (("action", "fromChainId"), 42161)):
            raw = recorded("base_usdc_to_arbitrum")
            node = raw
            for key in path[:-1]:
                node = node[key]
            node[path[-1]] = value
            self.lifi("bridge", raw)
            self.refused("bridge", self.BASE_TO_ARB, "quote")

    def test_a_quote_that_loses_too_much_is_refused(self):
        raw = recorded("base_usdc_to_arbitrum")
        raw["estimate"]["toAmountMin"] = "19000000"  # 5% on $20
        self.lifi("bridge", raw)
        error = self.refused("bridge", self.BASE_TO_ARB, "quote")
        self.assertIn("lose", error.reason)

    def test_a_fee_charged_on_top_counts_as_a_loss(self):
        raw = recorded("base_usdc_to_arbitrum")
        raw["estimate"]["feeCosts"][0].update(included=False, amountUSD="0.9")  # 4.5% on $20
        self.lifi("bridge", raw)
        self.refused("bridge", self.BASE_TO_ARB, "quote")

    def test_eth_sent_beyond_the_input_counts_as_a_loss(self):
        raw = recorded("base_usdc_to_arbitrum")
        raw["transactionRequest"]["value"] = hex(10**15)  # 0.001 ETH, about $1.93
        self.lifi("bridge", raw)
        self.refused("bridge", self.BASE_TO_ARB, "quote")

    def test_sol_the_quote_does_not_report_counts_as_a_loss(self):
        # Live, this bridge took 3.37m lamports more than the input and the fee:
        # accounts it opened, paid by Teeny, that LI.FI's costs do not list.
        self.lifi("bridge", recorded("sol_to_base_usdc"))
        self.simulate(100_000_000 + 3_372_720)
        error = self.refused("bridge", self.SOL_TO_BASE, "quote")
        self.assertIn("lose", error.reason)

    def test_a_simulation_that_fails_or_takes_more_usdc_is_refused(self):
        self.lifi("bridge", recorded("sol_to_base_usdc"))
        self.simulate(0, err={"InstructionError": [2, "Custom"]})
        self.refused("bridge", self.SOL_TO_BASE, "quote")
        self.lifi("bridge", recorded("sol_usdc_to_base_usdc"))
        self.simulate(15_000, usdc_taken=21 * 10**6)
        self.refused("bridge", self.SOL_USDC_TO_BASE, "quote")

    def test_a_solana_transaction_teeny_does_not_pay_for_or_another_must_still_sign_is_refused(self):
        raw = recorded("sol_usdc_to_base_usdc")
        # Mayan's own signature wiped: the module would sign and send it half-signed.
        self.lifi("bridge", with_tx_bytes(raw, lambda wire: wire.__setitem__(slice(65, 129), bytes(64))))
        self.simulate(15_000, usdc_taken=20 * 10**6)
        self.refused("bridge", self.SOL_USDC_TO_BASE, "quote")
        # Someone else's key as the fee payer, Teeny's nowhere.
        swap = recorded("sol_to_usdc_swap")
        def stranger(wire):
            at = wire.index(base_bytes(OWN))
            wire[at:at + 32] = bytes(range(32))
        self.lifi("swap", with_tx_bytes(swap, stranger))
        self.simulate(100_000_000)
        self.refused("swap", self.SOL_SWAP, "quote")

    def test_an_answer_that_is_not_lifis_is_refused(self):
        self.core.answers["openhuman.web3_bridge_quote"] = bridge_quote
        self.refused("bridge", self.BASE_TO_ARB, "quote")


def base_bytes(address: str) -> bytes:
    from teeny_hands.wallet import solana
    return solana.b58decode(address)


class ProviderTest(LifiTest):
    """Who quotes: WALLET_CONVERT_PROVIDER, LI.FI unless it says deBridge,
    and a `provider` field for one call."""

    def setUp(self):
        super().setUp()
        self.lifi("swap", recorded("base_eth_to_usdc_swap"))
        self.core.answers["openhuman.web3_bridge_quote"] = bridge_quote

    ETH_SWAP = {"chain": "base", "from": "ETH", "to": "USDC", "amount": "0.005"}
    BRIDGE = {"from_chain": "base", "from": "USDC", "to_chain": "solana", "to": "USDC", "amount": "40"}

    def test_lifi_is_the_default(self):
        convert.convert("swap", self.ETH_SWAP)
        self.assertEqual(self.core.params("openhuman.web3_swap_quote")[0]["slippage"], "lifi:1")
        self.assertEqual(self.rows()[0]["provider"], "lifi")

    def test_the_operator_can_make_debridge_the_default(self):
        self.configure(EVM_RPC_URL_BASE=self.chain.url, WALLET_CONVERT_PROVIDER="debridge")
        convert.convert("bridge", self.BRIDGE)
        self.assertEqual(self.core.params("openhuman.web3_bridge_quote")[0]["dstChainTokenOutAmount"], "debridge:auto")
        self.assertEqual(self.rows()[0]["provider"], "debridge")

    def test_one_call_can_name_the_other_provider(self):
        out = convert.convert("bridge", {**self.BRIDGE, "provider": "debridge"})["sent"]
        self.assertEqual(out["provider"], "debridge")
        self.configure(EVM_RPC_URL_BASE=self.chain.url, WALLET_CONVERT_PROVIDER="debridge")
        convert.convert("swap", {**self.ETH_SWAP, "provider": "lifi"})
        self.assertEqual(self.core.params("openhuman.web3_swap_quote")[0]["slippage"], "lifi:1")

    def test_an_unknown_provider_is_refused_before_anything_is_quoted(self):
        for provider in ("jupiter", "LIFI", 1):
            self.refused("swap", {**self.ETH_SWAP, "provider": provider}, "fields")
        self.configure(EVM_RPC_URL_BASE=self.chain.url, WALLET_CONVERT_PROVIDER="jupiter")
        self.refused("swap", self.ETH_SWAP, "caps")
        self.assertEqual([m for m in self.core.methods() if m.endswith("_quote")], [])
