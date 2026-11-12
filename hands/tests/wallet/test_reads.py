"""hands/wallet reads: address, balance, ledger, receipts, and the cost of
living (models + x402 + bills prorated), from a fake core and chain."""

from __future__ import annotations

from wallet.support import DAY, OWN, T0, TINY, USDC_DEVNET, VENDOR, Failure, Scratch, token_accounts
from teeny_hands import hand  # noqa: E402
from teeny_hands.wallet import pay, reads, store  # noqa: E402


def iso(at: float) -> str:
    import datetime as dt

    return dt.datetime.fromtimestamp(at, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class ReadsTest(Scratch):
    def test_address_and_balance(self):
        address = reads.address()
        self.assertEqual(address["evm"]["address"], "0x" + "1" * 40)  # the same phrase's EVM account
        del address["evm"]
        self.assertEqual(address, {"address": OWN, "cluster": "devnet", "url": f"https://solscan.io/account/{OWN}?cluster=devnet"})
        self.chain.answers["getTokenAccountsByOwner"] = token_accounts(12_345_678)
        out = reads.balance()
        self.assertEqual(out["sol"], {"raw": "10000000000", "display": "10"})
        self.assertEqual(out["usdc"], {"raw": "12345678", "display": "12.345678", "mint": USDC_DEVNET})
        usdc_call, tiny_call = self.chain.calls
        self.assertEqual(usdc_call["params"][:2], [OWN, {"mint": USDC_DEVNET}])
        self.assertEqual(usdc_call["params"][2]["encoding"], "jsonParsed")
        self.assertEqual(tiny_call["params"][:2], [OWN, {"mint": TINY}])
        self.assertEqual(out["tiny"], {"raw": "0", "display": "0", "mint": TINY})
        self.assertEqual(self.moved(), [])

    def test_tiny_reads_the_token_account_and_signs_nothing(self):
        self.chain.answers["getTokenAccountsByOwner"] = token_accounts(1_234_567_890, mint=TINY)
        out = reads.tiny()
        self.assertEqual(out, {"mint": TINY, "owner": OWN, "cluster": "devnet", "raw": "1234567890", "decimals": 6, "amount": 1234.56789})
        [call] = self.chain.calls
        self.assertEqual(call["method"], "getTokenAccountsByOwner")
        self.assertEqual(call["params"][:2], [OWN, {"mint": TINY}])
        self.assertEqual(self.moved(), [])
        self.assertEqual(self.core.methods(), ["openhuman.wallet_status"])

    def test_tiny_is_zero_without_a_token_account(self):
        self.chain.answers["getTokenAccountsByOwner"] = {"context": {"slot": 1}, "value": []}
        out = reads.tiny()
        self.assertEqual((out["raw"], out["amount"], out["decimals"]), ("0", 0.0, 6))

    def test_tiny_sums_accounts_and_honours_the_mint_setting(self):
        other = "So11111111111111111111111111111111111111112"
        accounts = token_accounts(5_000_000, mint=other)
        accounts["value"] += token_accounts(7_000_000, mint=other)["value"]
        self.chain.answers["getTokenAccountsByOwner"] = accounts
        self.configure(TINY_MINT=other)
        out = reads.tiny()
        self.assertEqual((out["mint"], out["raw"], out["amount"]), (other, "12000000", 12.0))
        self.configure(TINY_MINT="not-a-mint")
        with self.assertRaises(hand.HandError):
            reads.tiny()

    def test_balance_survives_a_failed_tiny_read(self):
        self.configure(TINY_MINT="not-a-mint")
        out = reads.balance()
        self.assertIn("error", out["tiny"])
        self.assertEqual(out["sol"]["display"], "10")

    def test_ledger_has_both_ledgers_and_survives_a_missing_core_one(self):
        reads.vendors_add("hosting", VENDOR, None)
        pay.pay({"to": VENDOR, "amount_usdc": "5", "purpose": "vendor", "ref": "inv-9"})
        self.core.answers["openhuman.wallet_ledger_list"] = [{"id": "c1", "status": "broadcast"}]
        out = reads.ledger(10)
        self.assertEqual([(p["status"], p["usd"], p["amount"]) for p in out["payments"]], [("paid", "5.00", "5")])
        self.assertEqual(out["core"], [{"id": "c1", "status": "broadcast"}])
        self.assertEqual(self.core.params("openhuman.wallet_ledger_list"), [{"limit": 10}])
        self.core.answers["openhuman.wallet_ledger_list"] = Failure("wallet disabled")
        self.assertIn("wallet disabled", reads.ledger(10)["core_error"])

    def test_receipts_owed_until_a_post_carries_the_signature(self):
        reads.vendors_add("hosting", VENDOR, None)
        paid = pay.pay({"to": VENDOR, "amount_usdc": "5", "purpose": "vendor", "ref": "inv-9"})["paid"]
        due = reads.receipts_due()
        self.assertEqual(due["count"], 1)
        receipt = due["due"][0]
        self.assertEqual((receipt["for"], receipt["url"]), ("hosting (inv-9)", paid["url"]))
        conn = hand.open_db("CREATE TABLE IF NOT EXISTS x_posts (id TEXT PRIMARY KEY, kind TEXT, text TEXT, in_reply_to TEXT, quote_of TEXT, media TEXT DEFAULT '[]', created_at REAL)")
        conn.execute("INSERT INTO x_posts (id, kind, text, created_at) VALUES ('1', 'post', ?, ?)", (f"paid the server bill {paid['url']}", T0))
        conn.close()
        self.assertEqual(reads.receipts_due()["count"], 0)

    def test_costs_add_models_x402_and_prorated_bills(self):
        self.core.answers["openhuman.cost_report"] = {"totals": {"calls": 120, "cost_usd": 12.345, "charged_usd": 10.0, "estimated_usd": 2.345}, "rows": []}
        self.core.answers["openhuman.x402_list_payments"] = {
            "payments": [
                # newest first; a status change is a new row with the same id
                {"id": "p1", "status": "settled", "asset": USDC_DEVNET, "amountAtomic": 1_500_000, "timestamp": iso(T0 - DAY)},
                {"id": "p1", "status": "pending", "asset": USDC_DEVNET, "amountAtomic": 1_500_000, "timestamp": iso(T0 - DAY - 5)},
                {"id": "p2", "status": "failed", "asset": USDC_DEVNET, "amountAtomic": 9_000_000, "timestamp": iso(T0 - DAY)},
                {"id": "p3", "status": "settled", "asset": USDC_DEVNET, "amountAtomic": 7_000_000, "timestamp": iso(T0 - 40 * DAY)},
                {"id": "p4", "status": "settled", "asset": "SomeOtherMint1111111111111111111111111111", "amountAtomic": 1, "timestamp": iso(T0 - DAY)},
            ]
        }
        reads.bills_add("server", "30", "month", "the box")
        reads.bills_add("domain", "24", "year", None)
        out = reads.costs(30)
        self.assertEqual(self.core.params("openhuman.cost_report"), [{"days": 30}])
        self.assertEqual(out["models"]["usd"], "12.35")
        self.assertEqual(out["models"]["calls"], 120)
        self.assertEqual(out["x402"], {"usd": "1.50", "payments": 1, "not_usdc": 1})
        self.assertEqual(out["bills"]["usd"], "32.00")  # 30 + 24/12 for a 30-day window
        self.assertEqual(out["total_usd"], "45.85")  # 12.345 + 1.50 + 32.00, rounded
        self.assertTrue(out["complete"])
        week = reads.costs(7)
        self.assertEqual(week["bills"]["usd"], "7.47")  # (30 + 2) * 7 / 30, rounded down to the micro-dollar
        self.assertEqual(self.moved(), [])

    def test_costs_say_when_a_source_is_missing(self):
        self.core.answers["openhuman.cost_report"] = Failure("no tracker")
        self.core.answers["openhuman.x402_list_payments"] = {"payments": []}
        reads.bills_add("server", "10", "month", None)
        out = reads.costs(30)
        self.assertIn("error", out["models"])
        self.assertFalse(out["complete"])
        self.assertEqual(out["total_usd"], "10.00")

    def test_costs_list_payouts_beside_the_total(self):
        self.core.answers["openhuman.cost_report"] = {"totals": {"cost_usd": 0}}
        self.core.answers["openhuman.x402_list_payments"] = {"payments": []}
        reads.vendors_add("hosting", VENDOR, None)
        pay.pay({"to": VENDOR, "amount_usdc": "5", "purpose": "vendor", "ref": "inv-9"})
        out = reads.costs(30)
        self.assertEqual(out["paid_out"], {"vendor": {"usd": "5.00", "payments": 1}})
        self.assertEqual(out["total_usd"], "0.00")

    def test_bills_and_vendors(self):
        reads.bills_add("server", "30", "month", None)
        reads.bills_add("server", "35", "month", "price went up")
        self.assertEqual(reads.bills_list()["bills"], [{"name": "server", "usd": "35.00", "period": "month", "note": "price went up"}])
        reads.bills_remove("server")
        with self.assertRaises(hand.Refused):
            reads.bills_remove("server")
        with self.assertRaises(hand.HandError):
            reads.bills_add("x", "-1", "month", None)
        with self.assertRaises(hand.Refused):
            reads.vendors_add("bad", "nope", None)
        with self.assertRaises(hand.Refused):
            reads.vendors_add("tiny", "2AF7CqwieUjUPALL7icuZtL3X7wENdjUjGBMmfV2pump", None)
        reads.vendors_add("hosting", VENDOR, "box")
        self.assertEqual(reads.vendors_list()["vendors"], [{"name": "hosting", "address": VENDOR, "chain": "solana", "note": "box"}])

    def test_receipt_of_an_unknown_payment(self):
        with self.assertRaises(hand.Refused):
            reads.receipt(42)

    def test_store_never_counts_failed_or_refused(self):
        conn = store.db()
        for status in ("failed", "refused"):
            pid = store.insert_pending(conn, cluster="devnet", asset="USDC", amount_raw=1, usd_micro=10**8, to_address=VENDOR, purpose="vendor", ref=status)
            store.update(conn, pid, status=status)
        self.assertEqual(store.spent_since(conn, 0), 0)
        conn.close()
