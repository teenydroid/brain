"""hands/wallet on Bitcoin: the recovery phrase's native segwit account is
named and read, never paid from. The core is faked (support.py)."""

from __future__ import annotations

from wallet.support import BTC_OWN, OWN, Scratch, balances, status_with, token_accounts, USDC_MAINNET
from teeny_hands.wallet import chains, pay, reads

MAINNET = {"WALLET_CLUSTER": "mainnet", "WALLET_ETH_USD": "4000"}


def btc_balances(sats: int, status: str = "ready") -> list[dict]:
    rows = [r for r in balances(10 * 10**9) if r["chain"] != "btc"]
    rows.append({"chain": "btc", "address": BTC_OWN, "assetSymbol": "BTC", "decimals": 8, "raw": str(sats),
                 "formatted": "x", "providerStatus": status})
    return rows


class BitcoinAddressTest(Scratch):
    def test_address_names_the_bitcoin_account_from_the_same_phrase(self):
        out = reads.address()
        self.assertEqual(out["address"], OWN)
        self.assertEqual(out["btc"], {"address": BTC_OWN, "url": f"https://mempool.space/address/{BTC_OWN}"})
        self.assertEqual(self.moved(), [])

    def test_own_address_on_bitcoin(self):
        self.assertEqual(pay.own_address(chain=chains.BITCOIN), BTC_OWN)

    def test_a_wallet_without_a_bitcoin_account_still_answers(self):
        status = status_with()
        status["accounts"] = [a for a in status["accounts"] if a["chain"] != "btc"]
        self.core.answers["openhuman.wallet_status"] = status
        self.assertNotIn("btc", reads.address())

    def test_only_a_native_segwit_mainnet_address_is_taken_as_bitcoin(self):
        self.assertTrue(chains.is_btc_address(BTC_OWN))
        for bad in ("", "bc1", "tb1qw508d6qejxtdg4y5r3zarvary0c5xw7kxpjzsx", "1BvBMSEYstWetqTFn5Au4m4GFg7xJaNVN2",
                    BTC_OWN.upper(), BTC_OWN + "b", "0x" + "1" * 40, None):
            self.assertFalse(chains.is_btc_address(bad), bad)

    def test_bitcoin_is_not_a_chain_the_hand_pays_swaps_or_bridges_on(self):
        self.assertNotIn(chains.BITCOIN, chains.CHAINS)


class BitcoinBalanceTest(Scratch):
    settings = {**MAINNET, "EVM_RPC_URL_BASE": None}

    def setUp(self):
        super().setUp()
        self.chain.answers["getTokenAccountsByOwner"] = token_accounts(0, USDC_MAINNET)
        self.chain.answers["eth_call"] = "0x" + "0" * 64

    def test_balance_reads_btc_from_the_core(self):
        self.core.answers["openhuman.wallet_balances"] = btc_balances(12_345_678)
        out = reads.balance()
        self.assertEqual(out["btc"], {"address": BTC_OWN, "btc": {"raw": "12345678", "display": "0.12345678"}})
        self.assertEqual(self.moved(), [])

    def test_an_unreadable_btc_balance_does_not_hide_the_rest(self):
        self.core.answers["openhuman.wallet_balances"] = btc_balances(0, status="missing")
        out = reads.balance()
        self.assertIn("error", out["btc"])
        self.assertEqual(out["sol"]["display"], "10")
