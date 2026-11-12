"""hands/wallet on EVM: the same recovery phrase pays on Base and the other
networks, under the same caps; an older hands.db is brought up to date.
The core and the chains are faked (support.py)."""

from __future__ import annotations

import sqlite3

from wallet.support import OWN, USDC_MAINNET, VENDOR, Scratch, balances, status_with, token_accounts
from teeny_hands import hand  # noqa: E402
from teeny_hands.wallet import caps, chains, pay, reads, store  # noqa: E402

EVM_OWN = "0x" + "1" * 40
EVM_VENDOR = "0x" + "ab" * 20
BASE_USDC = chains.NETWORKS["base"].usdc["mainnet"]

MAINNET = {
    "WALLET_CLUSTER": "mainnet",
    "WALLET_ETH_USD": "4000",
}


def evm_balances(eth_raw: int = 10**18) -> list[dict]:
    rows = balances(10 * 10**9)
    rows.append({"chain": "evm", "evmNetwork": "base_mainnet", "address": EVM_OWN, "assetSymbol": "ETH", "decimals": 18,
                 "raw": str(eth_raw), "formatted": "x", "providerStatus": "ready"})
    return rows


def evm_quote(params):
    return {
        "quoteId": "q-evm", "kind": "token_transfer" if params.get("assetSymbol") else "native_transfer",
        "chain": "evm", "evmNetwork": params.get("evmNetwork"), "fromAddress": EVM_OWN,
        "toAddress": params["toAddress"].lower(),  # the core may answer in another case
        "assetSymbol": params.get("assetSymbol") or "ETH", "amountRaw": params["amountRaw"],
        "amountFormatted": "x", "estimatedFeeRaw": "1", "status": "awaiting_confirmation",
        "createdAtMs": 1, "expiresAtMs": 2, "notes": [],
    }


def balance_of_call(raw: int) -> str:
    return "0x" + format(raw, "064x")


class EvmPayTest(Scratch):
    settings = {**MAINNET, "EVM_RPC_URL_BASE": None}

    def setUp(self):
        super().setUp()
        self.configure(EVM_RPC_URL_BASE=self.chain.url)
        self.core.answers["openhuman.wallet_balances"] = evm_balances()
        self.core.answers["openhuman.wallet_prepare_transfer"] = evm_quote
        self.chain.answers["getTokenAccountsByOwner"] = token_accounts(1000 * 10**6, USDC_MAINNET)
        self.chain.answers["eth_call"] = balance_of_call(500 * 10**6)
        reads.vendors_add("hosting-base", EVM_VENDOR, "the server, on Base", chain="base")

    def body(self, **overrides):
        body = {"to": EVM_VENDOR, "amount_usdc": "10", "chain": "base", "purpose": "vendor", "ref": "inv-evm"}
        body.update(overrides)
        return {k: v for k, v in body.items() if v is not None}

    def refused(self, body, rule):
        with self.assertRaises(hand.Refused) as caught:
            pay.pay(body)
        self.assertEqual(caught.exception.detail.get("rule"), rule, caught.exception.reason)
        self.assertEqual(self.moved(), [])

    def test_usdc_on_base_goes_to_the_core_with_its_network(self):
        out = pay.pay(self.body())["paid"]
        self.assertEqual((out["chain"], out["asset"], out["usd"]), ("base", "USDC", "10.00"))
        self.assertTrue(out["url"].startswith("https://basescan.org/tx/"))
        self.assertEqual(self.core.params("openhuman.wallet_prepare_transfer"),
                         [{"chain": "evm", "evmNetwork": "base_mainnet", "toAddress": EVM_VENDOR, "amountRaw": "10000000", "assetSymbol": "USDC"}])
        [call] = self.chain.params("eth_call")
        self.assertEqual(call[0]["to"], BASE_USDC)
        self.assertTrue(call[0]["data"].startswith("0x70a08231") and call[0]["data"].endswith("1" * 40))

    def test_eth_counts_at_the_fixed_eth_price(self):
        out = pay.pay(self.body(amount_usdc=None, amount_eth="0.005"))["paid"]
        self.assertEqual((out["asset"], out["usd"]), ("ETH", "20.00"))  # 0.005 ETH at 4000

    def test_eth_without_a_price_moves_nothing(self):
        self.configure(EVM_RPC_URL_BASE=self.chain.url, WALLET_ETH_USD=None)
        self.refused(self.body(amount_usdc=None, amount_eth="0.001"), "caps")

    def test_evm_is_refused_on_devnet(self):
        self.configure(EVM_RPC_URL_BASE=self.chain.url, WALLET_CLUSTER="devnet")
        self.refused(self.body(), "cluster")

    def test_sol_is_not_an_evm_asset(self):
        self.refused(self.body(amount_usdc=None, amount_sol="0.1"), "asset")

    def test_a_vendor_on_another_network_is_not_allowlisted_here(self):
        self.refused(self.body(chain="arbitrum"), "vendor")

    def test_a_token_contract_is_not_a_recipient(self):
        self.refused(self.body(to=BASE_USDC), "address")

    def test_a_solana_address_is_not_an_evm_one(self):
        self.refused(self.body(to=VENDOR), "address")

    def test_an_unknown_chain_is_refused(self):
        self.refused(self.body(chain="bsc"), "chain")

    def test_never_to_its_own_evm_address_in_any_case(self):
        reads.vendors_add("self", EVM_OWN.upper().replace("0X", "0x"), chain="base")
        self.refused(self.body(to=EVM_OWN.upper().replace("0X", "0x")), "own_address")

    def test_the_caps_count_every_chain_together(self):
        pay.pay(self.body(amount_usdc="50"))
        reads.vendors_add("hosting", VENDOR, "the server")
        self.chain.answers["getTokenAccountsByOwner"] = token_accounts(1000 * 10**6, USDC_MAINNET)
        self.core.answers["openhuman.wallet_prepare_transfer"] = evm_quote
        with self.assertRaises(hand.Refused) as caught:
            pay.pay({"to": VENDOR, "amount_usdc": "50", "purpose": "vendor", "ref": "inv-sol", "chain": "solana"})
        self.assertEqual(caught.exception.detail["rule"], "per_day")  # 50 on Base + 50 on Solana > 100

    def test_half_balance_reads_usdc_on_the_network(self):
        self.chain.answers["eth_call"] = balance_of_call(15 * 10**6)
        self.refused(self.body(), "half_balance")


class CapsTest(Scratch):
    def test_optional_caps_stay_optional(self):
        loaded = caps.load(hand.secrets())
        self.assertIsNone(loaded.eth_usd_micro)
        self.assertIsNone(loaded.convert_day_micro)
        self.assertIsNone(loaded.x402_request_micro)
        self.assertEqual(loaded.convert_loss_bps, caps.DEFAULT_LOSS_BPS)

    def test_x402_caps_come_together(self):
        self.configure(WALLET_MAX_X402_PER_REQUEST_USD="0.1")
        with self.assertRaises(caps.CapsError):
            caps.load(hand.secrets())

    def test_a_loss_above_its_ceiling_is_refused(self):
        self.configure(WALLET_MAX_CONVERT_LOSS_BPS="5000")
        with self.assertRaises(caps.CapsError):
            caps.load(hand.secrets())

    def test_the_core_policy_pins_evm_usdc_on_mainnet_only(self):
        devnet = caps.policy_assets(caps.load(hand.secrets()))
        self.assertEqual({a["chain"] for a in devnet}, {"solana"})
        self.configure(**MAINNET)
        mainnet = caps.policy_assets(caps.load(hand.secrets()))
        base = [a for a in mainnet if a.get("evm_network") == "base_mainnet"]
        self.assertEqual({a["symbol"] for a in base}, {"USDC", "ETH"})
        usdc = next(a for a in base if a["symbol"] == "USDC")
        self.assertEqual((usdc["token_address"], usdc["per_tx_max_raw"]), (BASE_USDC, 50 * 10**6))


class AddressTest(Scratch):
    def test_address_names_both_accounts_from_the_one_phrase(self):
        out = reads.address()
        self.assertEqual(out["address"], OWN)
        self.assertEqual(out["evm"]["address"], EVM_OWN)
        self.assertFalse(out["evm"]["usable"])  # devnet
        self.assertEqual(out["evm"]["networks"]["base"], f"https://basescan.org/address/{EVM_OWN}")

    def test_a_wallet_without_an_evm_account_still_answers(self):
        status = status_with()
        status["accounts"] = [a for a in status["accounts"] if a["chain"] != "evm"]
        self.core.answers["openhuman.wallet_status"] = status
        self.assertNotIn("evm", reads.address())


class MigrationTest(Scratch):
    def test_an_old_payments_table_is_rebuilt_with_its_rows_on_solana(self):
        path = hand.state_dir() / "hands.db"
        conn = sqlite3.connect(path)
        conn.executescript(
            """
            CREATE TABLE wallet_payments (
                id INTEGER PRIMARY KEY AUTOINCREMENT, created_at REAL NOT NULL, updated_at REAL NOT NULL,
                cluster TEXT NOT NULL, asset TEXT NOT NULL CHECK (asset IN ('SOL', 'USDC')),
                amount_raw INTEGER NOT NULL CHECK (amount_raw > 0), usd_micro INTEGER NOT NULL, to_address TEXT NOT NULL,
                purpose TEXT NOT NULL CHECK (purpose IN ('bounty', 'vendor')), ref TEXT NOT NULL,
                status TEXT NOT NULL CHECK (status IN ('pending', 'paid', 'in_doubt', 'failed', 'refused')),
                quote_id TEXT, signature TEXT, error TEXT);
            CREATE TABLE vendors (name TEXT PRIMARY KEY, address TEXT NOT NULL UNIQUE, note TEXT, created_at REAL NOT NULL);
            INSERT INTO wallet_payments (created_at, updated_at, cluster, asset, amount_raw, usd_micro, to_address, purpose, ref, status, signature)
                VALUES (1, 1, 'devnet', 'USDC', 5, 5, 'x', 'vendor', 'old', 'paid', 'sig');
            INSERT INTO vendors VALUES ('old', 'addr', NULL, 1);
            """
        )
        conn.close()
        conn = store.db()
        try:
            [row] = [dict(r) for r in conn.execute("SELECT * FROM wallet_payments")]
            self.assertEqual((row["chain"], row["ref"], row["signature"]), ("solana", "old", "sig"))
            store.insert_pending(conn, cluster="mainnet", chain="base", asset="ETH", amount_raw=1, usd_micro=1, to_address="0x", purpose="vendor", ref="new")
            self.assertEqual(conn.execute("SELECT chain FROM vendors").fetchone()[0], "solana")
            indexes = {r[1] for r in conn.execute("PRAGMA index_list(wallet_payments)")}
            self.assertIn("wallet_payments_created", indexes)
        finally:
            conn.close()
