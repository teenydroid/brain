"""hands/x402 fund: USDC on Base is topped up from Teeny's own pinned SOL, ETH
and USDC, only through hands/wallet's swap and bridge (every rule and cap of
convert.py still applies), in a fixed order with reserves kept, never twice
while one is in flight, and never past X402_FUND_MAX_PER_DAY_USD. The core,
Solana and the EVM networks are faked; nothing is signed."""

from __future__ import annotations

from wallet.support import T0, USDC_MAINNET, Scratch, balances, token_accounts
from teeny_hands import hand, stimuli  # noqa: E402
from teeny_hands.wallet import chains, store  # noqa: E402
from teeny_hands.x402 import fund  # noqa: E402

EVM_OWN = "0x" + "1" * 40
DLN = "0xeF4fB24aD0916217251F553c0596F8Edc630EB66"
NOW_MS = int(T0 * 1000)
PRICE = {"SOL": 200, "ETH": 4000, "USDC": 1}
DECIMALS = {"SOL": 9, "ETH": 18, "USDC": 6}
MOVING = ("openhuman.web3_swap_execute", "openhuman.web3_bridge_execute", "openhuman.web3_dapp_call",
          "openhuman.web3_dapp_execute")
QUOTES = ("openhuman.web3_swap_quote", "openhuman.web3_bridge_quote")
USDC = {key: n.usdc["mainnet"].lower() for key, n in chains.NETWORKS.items()}


def _asset(token: str) -> str:
    if token == chains.EVM_NATIVE:
        return "ETH"
    if token == chains.SOL_NATIVE:
        return "SOL"
    return "USDC"


def _usd(asset: str, raw: int) -> float:
    return raw * PRICE[asset] / 10 ** DECIMALS[asset]


def swap_quote(p):
    amount = int(p["tokenInAmount"])
    usd = _usd(_asset(p["tokenIn"]), amount)
    out = int(usd * 0.998 * 1e6)
    return {"quoteId": "sq", "kind": "swap", "expiresAtMs": NOW_MS + 60_000, "signingChain": "evm", "quote": {
        "tokenIn": {"address": p["tokenIn"], "amount": str(amount), "approximateUsdValue": usd},
        "tokenOut": {"address": p["tokenOut"], "amount": str(out), "minAmount": str(out), "approximateUsdValue": out / 1e6},
        "tx": {"to": DLN, "data": "0x", "value": str(amount) if p["tokenIn"] == chains.EVM_NATIVE else "0"}}}


def bridge_quote(p):
    amount = int(p["srcChainTokenInAmount"])
    asset = _asset(p["srcChainTokenIn"])
    usd = _usd(asset, amount)
    out = int(usd * 0.9975 * 1e6)
    solana = p["srcChainId"] == chains.DEBRIDGE_SOLANA
    tx = {"data": "base64tx"} if solana else {"to": DLN, "data": "0x", "value": str(amount) if asset == "ETH" else "0"}
    return {"quoteId": "bq", "kind": "bridge", "expiresAtMs": NOW_MS + 60_000, "signingChain": "solana" if solana else "evm",
            "quote": {"estimation": {
                "srcChainTokenIn": {"address": p["srcChainTokenIn"], "amount": str(amount), "approximateUsdValue": usd},
                "dstChainTokenOut": {"address": p["dstChainTokenOut"], "amount": str(out), "recommendedAmount": str(out)}},
                "tx": tx, "orderId": "0xorder", "fixFee": "0"}}


def dapp_call(p):
    return {"quoteId": "dq", "kind": "dapp_call", "expiresAtMs": NOW_MS + 60_000, "signingChain": "evm",
            "quote": {"contractAddress": p["contractAddress"], "calldata": p["calldata"], "valueRaw": "0"}}


class FundTest(Scratch):
    settings = {
        "WALLET_CLUSTER": "mainnet", "WALLET_ETH_USD": "4000", "WALLET_MAX_CONVERT_PER_DAY_USD": "100",
        "WALLET_APPROVAL_POLL_SECONDS": "0",
        "X402_FUND": "1", "X402_FUND_FLOOR_USD": "1", "X402_FUND_TARGET_USD": "5", "X402_FUND_MAX_PER_DAY_USD": "20",
    }

    def setUp(self):
        super().setUp()
        self.configure()
        self.eth = {"base": 0, "arbitrum": 0, "ethereum": 0, "optimism": 0}
        self.usdc = {"base": 500_000, "arbitrum": 0, "ethereum": 0, "optimism": 0}  # $0.50 on Base, under the floor
        self.sol = 0
        self.sol_usdc = 0
        self.core.answers.update({
            "openhuman.wallet_balances": lambda p: self._balances(),
            "openhuman.web3_swap_quote": swap_quote,
            "openhuman.web3_bridge_quote": bridge_quote,
            "openhuman.web3_swap_execute": lambda p: {"quoteId": p["quoteId"], "transactionHash": "0xswap"},
            "openhuman.web3_bridge_execute": lambda p: {"quoteId": p["quoteId"], "transactionHash": "sig-bridge"},
            "openhuman.web3_dapp_call": dapp_call,
            "openhuman.web3_dapp_execute": lambda p: {"quoteId": p["quoteId"], "transactionHash": "0xapprove"},
            "openhuman.wallet_tx_status": {"state": "confirmed"},
        })
        self.chain.answers["getTokenAccountsByOwner"] = lambda p: token_accounts(self.sol_usdc, USDC_MAINNET)
        self.chain.answers["eth_call"] = lambda p: self._erc20(p[0]["to"])

    def configure(self, **overrides):
        evm = {network.env: self.chain.url for network in chains.NETWORKS.values()} if hasattr(self, "chain") else {}
        super().configure(**{**evm, **overrides})

    def _balances(self):
        rows = [r for r in balances(self.sol) if r["chain"] == "solana"]
        for key, network in chains.NETWORKS.items():
            rows.append({"chain": "evm", "evmNetwork": network.evm_network, "address": EVM_OWN, "assetSymbol": "ETH",
                         "decimals": 18, "raw": str(self.eth[key]), "formatted": "x", "providerStatus": "ready"})
        return rows

    def _erc20(self, contract):
        network = next(k for k, v in USDC.items() if v == contract.lower())
        return "0x" + format(self.usdc[network], "064x")

    def moved(self):
        return [m for m in self.core.methods() if m in MOVING]

    def quoted(self):
        return [m for m in self.core.methods() if m in QUOTES]

    def runs(self):
        conn = fund.db()
        try:
            return [dict(r) for r in conn.execute("SELECT * FROM x402_fund_runs ORDER BY id")]
        finally:
            conn.close()

    def topups(self):
        conn = stimuli.db()
        try:
            return [dict(r) for r in conn.execute("SELECT * FROM stimuli WHERE surface = 'wallet' AND kind = 'topup'")]
        finally:
            conn.close()

    def refused(self, rule):
        with self.assertRaises(hand.Refused) as caught:
            fund.fund()
        self.assertEqual(caught.exception.detail.get("rule"), rule, caught.exception.reason)
        return caught.exception

    # --- when it does nothing -------------------------------------------------------------

    def test_off_by_default_and_asks_nothing(self):
        self.configure(X402_FUND=None)
        self.assertIn("off", fund.fund()["skipped"])
        self.assertEqual(self.core.methods(), [])

    def test_above_the_floor_is_a_no_op(self):
        self.usdc["base"] = 1_000_000
        self.eth["base"] = 10**18
        out = fund.fund()
        self.assertEqual((out["kind"], out["base_usdc"]), ("funded", "1"))
        self.assertEqual(self.quoted(), [])
        self.assertEqual(self.runs(), [])

    def test_a_conversion_in_flight_toward_base_usdc_waits(self):
        self.sol_usdc = 1000 * 10**6
        conn = store.db()
        try:
            cid = store.insert_row(conn, "wallet_conversions", kind="bridge", from_chain="solana", from_asset="USDC",
                                   amount_raw="4000000", usd_micro=4_000_000, to_chain="base", to_asset="USDC")
        finally:
            conn.close()
        self.at(T0 + 30 * 60)
        out = fund.fund()
        self.assertEqual((out["kind"], out["conversion_id"]), ("in_flight", cid))
        self.assertEqual(self.quoted(), [])
        self.at(T0 + 61 * 60)  # past X402_FUND_COOLDOWN_MIN (60): it may have landed short
        self.assertEqual(fund.fund()["kind"], "sent")

    def test_devnet_and_insane_settings_are_refused(self):
        self.configure(WALLET_CLUSTER="devnet")
        self.refused("cluster")
        self.configure(X402_FUND_FLOOR_USD="5")
        self.refused("caps")
        self.configure(X402_FUND_MAX_PER_DAY_USD=None)
        self.refused("caps")
        self.assertEqual(self.moved(), [])

    # --- the source -----------------------------------------------------------------------

    def test_base_eth_comes_first_as_a_swap(self):
        self.eth["base"] = 10**18
        self.sol_usdc = 1000 * 10**6
        out = fund.fund()
        self.assertEqual(out["kind"], "sent")
        [params] = self.core.params("openhuman.web3_swap_quote")
        self.assertEqual((params["chainId"], params["tokenIn"]), (8453, chains.EVM_NATIVE))
        self.assertEqual(params["tokenInAmount"], str(1125 * 10**12))  # $4.50 at $4000 a ETH, rounded down to wei
        self.assertNotIn("openhuman.web3_bridge_quote", self.core.methods())
        self.assertEqual(out["plan"]["source"], "base:ETH")

    def test_a_source_under_its_reserve_is_passed_over(self):
        self.eth["base"] = 6 * 10**14  # $2.40, and $2 of it is kept for gas
        self.sol_usdc = 1000 * 10**6
        out = fund.fund()
        self.assertEqual(out["plan"]["source"], "solana:USDC")
        [params] = self.core.params("openhuman.web3_bridge_quote")
        self.assertEqual((params["srcChainId"], params["srcChainTokenInAmount"]), (chains.DEBRIDGE_SOLANA, "4500000"))
        self.assertEqual((params["dstChainId"], params["dstChainTokenOutRecipient"]), (8453, EVM_OWN))

    def test_sol_keeps_its_fee_reserve(self):
        self.sol = 6 * 10**7  # 0.06 SOL, 0.05 kept: $2 free, half the balance is $6
        self.usdc["arbitrum"] = 100 * 10**6
        out = fund.fund()
        self.assertEqual(out["plan"]["source"], "arbitrum:USDC")
        self.sol = 10 * 10**9
        self.at(T0 + 2 * 3600)
        out = fund.fund()
        self.assertEqual(out["plan"]["source"], "solana:SOL")
        self.assertEqual(self.core.params("openhuman.web3_bridge_quote")[-1]["srcChainTokenInAmount"], str(225 * 10**5))

    def test_with_no_single_source_enough_the_largest_tops_up_part(self):
        self.sol_usdc = 7 * 10**6  # half is $3.50
        self.usdc["optimism"] = 4 * 10**6  # half is $2
        out = fund.fund()
        self.assertTrue(out["plan"]["partial"])
        self.assertEqual((out["plan"]["source"], out["plan"]["amount"]), ("solana:USDC", "3.5"))

    def test_nothing_to_top_up_from_is_said_once_a_day(self):
        self.refused("no_source")
        self.refused("no_source")
        self.assertEqual(len(self.topups()), 1)
        self.assertEqual([r["outcome"] for r in self.runs()], ["no_source", "no_source"])
        self.assertEqual(self.quoted(), [])

    # --- the limits -----------------------------------------------------------------------

    def test_the_day_is_clamped_to_its_own_cap(self):
        self.configure(X402_FUND_MAX_PER_DAY_USD="3")
        self.sol_usdc = 1000 * 10**6
        out = fund.fund()
        self.assertEqual((out["plan"]["amount"], out["plan"]["clamped_by"]), ("3", "X402_FUND_MAX_PER_DAY_USD"))
        self.at(T0 + 2 * 3600)
        self.refused("fund_day")
        self.at(T0 + 25 * 3600)
        self.assertEqual(fund.fund()["kind"], "sent")

    def test_a_wallet_refusal_comes_back_with_its_rule(self):
        self.configure(WALLET_MAX_CONVERT_PER_DAY_USD="2")
        self.sol_usdc = 1000 * 10**6
        error = self.refused("per_day_convert")
        self.assertEqual(self.moved(), [])
        [run] = self.runs()
        self.assertEqual((run["outcome"], run["rule"]), ("refused", "per_day_convert"))
        [topup] = self.topups()
        self.assertIn("did not go", topup["text"])
        self.assertEqual(error.detail["run_id"], run["id"])

    def test_an_execute_that_does_not_come_back_is_in_doubt_and_counted(self):
        from wallet.support import Failure

        self.core.answers["openhuman.web3_bridge_execute"] = Failure("timeout")
        self.sol_usdc = 1000 * 10**6
        with self.assertRaises(hand.HandError):
            fund.fund()
        [run] = self.runs()
        self.assertEqual(run["outcome"], "in_doubt")
        self.assertIsNotNone(run["conversion_id"])
        self.assertEqual(run["usd_micro"], 4_500_000)
        self.at(T0 + 2 * 3600)
        out = fund.fund()  # the cooldown is over; the day still counts the one in doubt
        self.assertEqual(out["plan"]["amount"], "4.5")

    # --- what it leaves behind ------------------------------------------------------------

    def test_a_top_up_is_recorded_and_told_to_the_mind(self):
        self.sol_usdc = 1000 * 10**6
        out = fund.fund()
        [run] = self.runs()
        self.assertEqual((run["outcome"], run["source"], run["amount"], run["base_before_micro"]),
                         ("sent", "solana:USDC", "4.5", 500_000))
        self.assertEqual(run["conversion_id"], out["sent"]["conversion_id"])
        [topup] = self.topups()
        self.assertIn("4.5 USDC", topup["text"])
        self.assertEqual(topup["ref"], f"run-{run['id']}")

    def test_dry_run_quotes_and_checks_but_moves_and_records_nothing(self):
        self.configure(DRY_RUN="1")
        self.sol_usdc = 1000 * 10**6
        out = fund.fund()
        self.assertTrue(out["dry_run"])
        self.assertEqual(out["would_fund"]["source"], "solana:USDC")
        self.assertIn("quote", [c["rule"] for c in out["checks"]])
        self.assertEqual(self.moved(), [])
        self.assertEqual((self.runs(), self.topups()), ([], []))

    def test_status_reads_base_usdc_and_the_runs(self):
        self.sol_usdc = 1000 * 10**6
        fund.fund()
        out = fund.status()
        self.assertEqual((out["on"], out["floor_usd"], out["target_usd"], out["base_usdc"]), (True, "1", "5", "0.5"))
        self.assertEqual(len(out["runs"]), 1)
        self.assertEqual(out["topped_up_24h_usd"], "4.5")
        self.assertEqual(self.moved()[-1:], ["openhuman.web3_bridge_execute"])
