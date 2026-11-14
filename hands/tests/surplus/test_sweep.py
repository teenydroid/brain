"""hands/surplus sweep: USDC on Base above the x402 rail's reserve goes to the
surplus wallet through wallet.pay (an allowlisted vendor on base, every cap),
and SOL above its reserve is bridged to Teeny's OWN USDC on Base through
wallet.convert for the next run to pay on. The core, Solana and the EVM
networks are faked; nothing is signed."""

from __future__ import annotations

from wallet.support import T0, USDC_MAINNET, Scratch, balances, token_accounts
from wallet.test_evm import evm_quote
from x402 import test_fund as fakes
from teeny_hands import hand, stimuli  # noqa: E402
from teeny_hands.surplus import sweep  # noqa: E402
from teeny_hands.wallet import chains, reads, store  # noqa: E402

SURPLUS = "0xb8750da63a6f9e65062e33604356f3dfe620e563"
MOVING = ("openhuman.wallet_prepare_transfer", "openhuman.wallet_execute_prepared", *fakes.MOVING)
GAS = 10**15  # 0.001 ETH on Base


class SweepTest(Scratch):
    settings = {
        "WALLET_CLUSTER": "mainnet", "WALLET_MAX_CONVERT_PER_DAY_USD": "100",
        "WALLET_APPROVAL_POLL_SECONDS": "0", "WALLET_CONVERT_PROVIDER": "debridge",
        "SURPLUS_SWEEP": "1", "SURPLUS_ADDRESS": SURPLUS,
    }

    def setUp(self):
        super().setUp()
        self.configure()
        self.eth = {"base": GAS, "arbitrum": 0, "ethereum": 0, "optimism": 0}
        self.usdc = {"base": 25 * 10**6, "arbitrum": 0, "ethereum": 0, "optimism": 0}
        self.sol = 0
        self.core.answers.update({
            "openhuman.wallet_balances": lambda p: self._balances(),
            "openhuman.wallet_prepare_transfer": evm_quote,
            "openhuman.wallet_execute_prepared": lambda p: {"quoteId": p["quoteId"], "transactionHash": "0xpaid"},
            "openhuman.web3_bridge_quote": fakes.bridge_quote,
            "openhuman.web3_bridge_execute": lambda p: {"quoteId": p["quoteId"], "transactionHash": "sig-bridge"},
        })
        self.chain.answers["getTokenAccountsByOwner"] = lambda p: token_accounts(0, USDC_MAINNET)
        self.chain.answers["eth_call"] = lambda p: self._erc20(p[0]["to"])
        reads.vendors_add("surplus", SURPLUS, "the surplus wallet", chain="base")

    def configure(self, **overrides):
        evm = {network.env: self.chain.url for network in chains.NETWORKS.values()} if hasattr(self, "chain") else {}
        super().configure(**{**evm, **overrides})

    def _balances(self):
        rows = [r for r in balances(self.sol) if r["chain"] == "solana"]
        for key, network in chains.NETWORKS.items():
            rows.append({"chain": "evm", "evmNetwork": network.evm_network, "address": fakes.EVM_OWN, "assetSymbol": "ETH",
                         "decimals": 18, "raw": str(self.eth[key]), "formatted": "x", "providerStatus": "ready"})
        return rows

    def _erc20(self, contract):
        network = next(k for k, v in fakes.USDC.items() if v == contract.lower())
        return "0x" + format(self.usdc[network], "064x")

    def moved(self):
        return [m for m in self.core.methods() if m in MOVING]

    def runs(self):
        conn = sweep.db()
        try:
            return [dict(r) for r in conn.execute("SELECT * FROM surplus_runs ORDER BY id")]
        finally:
            conn.close()

    def told(self):
        conn = stimuli.db()
        try:
            return [dict(r) for r in conn.execute("SELECT * FROM stimuli WHERE surface = 'wallet' AND kind = 'surplus'")]
        finally:
            conn.close()

    # --- off and idle ---------------------------------------------------------------------

    def test_off_by_default_and_asks_nothing(self):
        self.configure(SURPLUS_SWEEP=None)
        self.assertIn("off", sweep.sweep()["skipped"])
        self.assertEqual(self.core.methods(), [])

    def test_nothing_above_either_reserve_moves_nothing(self):
        self.usdc["base"] = 10_500_000  # $0.50 over the $10 kept, under the $1 minimum
        self.sol = 12 * 10**7  # 0.12 SOL, 0.1 kept
        out = sweep.sweep()
        self.assertEqual((out["payout"]["outcome"], out["bridge"]["outcome"]), ("nothing", "nothing"))
        self.assertEqual(self.moved(), [])
        self.assertEqual(self.runs(), [])

    def test_devnet_and_insane_settings_are_refused(self):
        self.configure(WALLET_CLUSTER="devnet")
        with self.assertRaises(hand.Refused) as caught:
            sweep.sweep()
        self.assertEqual(caught.exception.detail["rule"], "cluster")
        self.configure(SURPLUS_ADDRESS="not-an-address")
        with self.assertRaises(hand.Refused) as caught:
            sweep.sweep()
        self.assertEqual(caught.exception.detail["rule"], "caps")
        self.assertEqual(self.moved(), [])

    # --- the payout -----------------------------------------------------------------------

    def test_usdc_above_the_reserve_goes_to_the_surplus_wallet_on_base(self):
        out = sweep.sweep()["payout"]
        # $15 over the $10 kept, but pay never sends more than half: $12.50.
        self.assertEqual((out["outcome"], out["amount"], out["clamped_by"]), ("paid", "12.5", "half_balance"))
        [params] = self.core.params("openhuman.wallet_prepare_transfer")
        self.assertEqual(params, {"chain": "evm", "evmNetwork": "base_mainnet", "toAddress": SURPLUS,
                                  "amountRaw": "12500000", "assetSymbol": "USDC"})
        [run] = self.runs()
        self.assertEqual((run["step"], run["outcome"], run["usd_micro"]), ("payout", "paid", 12_500_000))
        [told] = self.told()
        self.assertIn("surplus wallet", told["text"])
        conn = store.db()
        try:
            [paid] = conn.execute("SELECT purpose, chain, status FROM wallet_payments").fetchall()
        finally:
            conn.close()
        self.assertEqual(tuple(paid), ("vendor", "base", "paid"))

    def test_the_weekly_cap_to_the_surplus_wallet_clamps_then_waits(self):
        self.assertEqual(sweep.sweep()["payout"]["amount"], "12.5")
        self.usdc["base"] = 200 * 10**6  # $190 over the reserve; $60 a week to one address, $12.50 gone
        self.at(T0 + 2 * 3600)
        out = sweep.sweep()["payout"]
        self.assertEqual((out["outcome"], out["amount"], out["clamped_by"]),
                         ("paid", "47.5", "WALLET_MAX_PER_RECIPIENT_WEEK_USD"))
        self.at(T0 + 4 * 3600)
        self.assertEqual(sweep.sweep()["payout"]["outcome"], "waiting")
        self.assertEqual(len(self.core.params("openhuman.wallet_prepare_transfer")), 2)
        self.at(T0 + 8 * 86400)
        self.assertEqual(sweep.sweep()["payout"]["outcome"], "paid")

    def test_no_gas_on_base_pays_nothing(self):
        self.eth["base"] = 0
        out = sweep.sweep()["payout"]
        self.assertEqual((out["outcome"], out["rule"]), ("refused", "gas"))
        self.assertEqual(self.moved(), [])

    def test_an_address_off_the_vendor_allowlist_is_refused_before_pay(self):
        reads.vendors_remove("surplus")
        out = sweep.sweep()["payout"]
        self.assertEqual((out["outcome"], out["rule"]), ("refused", "vendor"))
        self.assertIn("vendors add surplus", out["why"])
        self.assertEqual(self.moved(), [])

    def test_a_payout_whose_execute_does_not_come_back_is_in_doubt_and_told(self):
        from wallet.support import Failure

        self.core.answers["openhuman.wallet_execute_prepared"] = Failure("timeout")
        out = sweep.sweep()["payout"]
        self.assertEqual(out["outcome"], "in_doubt")
        self.assertEqual([r["outcome"] for r in self.runs()], ["in_doubt"])
        self.assertIn("in doubt", self.told()[0]["text"])

    # --- the bridge -----------------------------------------------------------------------

    def test_spare_sol_is_bridged_to_teenys_own_usdc_on_base(self):
        self.usdc["base"] = 10 * 10**6
        self.sol = 10 * 10**9  # 9.9 free, half is 5; $50 per tx at $200 a SOL is 0.25
        out = sweep.sweep()["bridge"]
        self.assertEqual((out["outcome"], out["amount"], out["clamped_by"]), ("sent", "0.25", "WALLET_MAX_PER_TX_USD"))
        [params] = self.core.params("openhuman.web3_bridge_quote")
        self.assertEqual((params["srcChainId"], params["srcChainTokenIn"], params["srcChainTokenInAmount"]),
                         (chains.DEBRIDGE_SOLANA, chains.SOL_NATIVE, "250000000"))
        # The bridge lands on Teeny's own address, never the surplus wallet's.
        self.assertEqual((params["dstChainId"], params["dstChainTokenOutRecipient"]), (8453, fakes.EVM_OWN))
        self.assertEqual([(r["step"], r["outcome"]) for r in self.runs()], [("bridge", "sent")])

    def test_a_bridge_in_flight_is_not_doubled(self):
        self.usdc["base"] = 10 * 10**6
        self.sol = 10 * 10**9
        sweep.sweep()
        self.at(T0 + 30 * 60)
        self.assertEqual(sweep.sweep()["bridge"]["outcome"], "in_flight")
        self.at(T0 + 61 * 60)
        self.assertEqual(sweep.sweep()["bridge"]["outcome"], "sent")
        self.assertEqual(len(self.core.params("openhuman.web3_bridge_quote")), 2)

    def test_the_daily_bridge_cap_clamps_the_bridge(self):
        self.configure(WALLET_MAX_CONVERT_PER_DAY_USD="30")
        self.sol = 10 * 10**9
        out = sweep.sweep()["bridge"]
        self.assertEqual((out["amount"], out["clamped_by"]), ("0.15", "WALLET_MAX_CONVERT_PER_DAY_USD"))

    def test_sol_under_the_bridge_minimum_waits(self):
        self.sol = 14 * 10**7  # 0.14: 0.04 above the reserve, under the 0.05 minimum
        self.assertEqual(sweep.sweep()["bridge"]["outcome"], "nothing")
        self.assertNotIn("openhuman.web3_bridge_quote", self.core.methods())

    # --- dry run --------------------------------------------------------------------------

    def test_a_dry_run_rehearses_both_steps_and_moves_nothing(self):
        self.configure(DRY_RUN="1")
        self.sol = 10 * 10**9
        out = sweep.sweep()
        self.assertEqual((out["payout"]["outcome"], out["bridge"]["outcome"]), ("would_pay", "would_bridge"))
        self.assertEqual(out["payout"]["would_pay"]["to"], SURPLUS)
        self.assertEqual(self.moved(), [])
        self.assertEqual(self.runs(), [])
        self.assertEqual(self.told(), [])

    def test_status_says_what_went_out(self):
        sweep.sweep()
        out = sweep.status()
        self.assertEqual((out["on"], out["to"], out["paid_7d_usd"], out["paid_total_usd"]), (True, SURPLUS, "12.50", "12.50"))
        self.assertEqual(len(out["runs"]), 1)
