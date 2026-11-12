"""hands/wallet x402 fetch: a paid HTTP request goes only to an allowlisted
https host, only with the x402 caps set, and never for more than is left of
the day; the core is told the most it may pay. The core is faked."""

from __future__ import annotations

from wallet.support import Failure, Scratch
from teeny_hands import hand  # noqa: E402
from teeny_hands.wallet import caps, store, x402  # noqa: E402

BASE_USDC = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
URL = "https://api.example.com/v1/answer?q=1"


def paid(params, amount=50_000, asset=BASE_USDC, settled=True):
    return {
        "status": 200, "contentType": "application/json", "body": '{"answer": 42}', "truncated": False,
        "payment": {"paymentId": "pay-1", "amountAtomic": amount, "asset": asset, "recipient": "0x" + "3" * 40,
                    "network": "eip155:8453", "settled": settled},
    }


class X402Test(Scratch):
    settings = {"WALLET_MAX_X402_PER_REQUEST_USD": "0.10", "WALLET_MAX_X402_PER_DAY_USD": "0.25"}

    def setUp(self):
        super().setUp()
        self.core.answers["openhuman.x402_fetch"] = paid
        x402.hosts_add("api.example.com", "answers")

    def rows(self):
        conn = store.db()
        try:
            return [dict(r) for r in conn.execute("SELECT * FROM x402_requests ORDER BY id")]
        finally:
            conn.close()

    def refused(self, body, rule):
        with self.assertRaises(hand.Refused) as caught:
            x402.fetch(body)
        self.assertEqual(caught.exception.detail.get("rule"), rule, caught.exception.reason)
        self.assertNotIn("openhuman.x402_fetch", self.core.methods())

    def test_a_paid_request_passes_its_cap_down_and_is_recorded(self):
        out = x402.fetch({"url": URL, "max_usd": "0.08"})
        self.assertEqual((out["status"], out["body"], out["paid"]["usd"]), (200, '{"answer": 42}', "0.05"))
        self.assertEqual(self.core.params("openhuman.x402_fetch"), [{"url": URL, "method": "GET", "maxAmountAtomic": 80_000}])
        [row] = self.rows()
        self.assertEqual((row["status"], row["usd_micro"], row["max_micro"], row["host"]), ("paid", 50_000, 80_000, "api.example.com"))

    def test_a_free_response_costs_nothing(self):
        self.core.answers["openhuman.x402_fetch"] = {"status": 200, "body": "ok", "payment": None}
        self.assertIsNone(x402.fetch({"url": URL})["paid"])
        self.assertEqual(self.rows()[0]["status"], "free")

    def test_without_the_caps_nothing_is_paid(self):
        self.configure(WALLET_MAX_X402_PER_REQUEST_USD=None, WALLET_MAX_X402_PER_DAY_USD=None)
        self.refused({"url": URL}, "caps")

    def test_only_allowlisted_https_hosts(self):
        self.refused({"url": "https://evil.example.net/x"}, "host")
        self.refused({"url": "http://api.example.com/x"}, "url")
        self.refused({"url": "https://user:pw@api.example.com/x"}, "url")
        self.refused({"url": "https://127.0.0.1/x"}, "url")

    def test_max_usd_cannot_lift_the_per_request_cap(self):
        self.refused({"url": URL, "max_usd": "0.50"}, "max_usd")

    def test_the_day_counts_requests_in_flight_at_their_cap_and_shrinks_the_last(self):
        self.core.answers["openhuman.x402_fetch"] = Failure("connection reset")
        with self.assertRaises(hand.HandError):
            x402.fetch({"url": URL})  # in doubt: counts its full 0.10
        self.core.answers["openhuman.x402_fetch"] = paid
        x402.fetch({"url": URL})  # pays 0.05; 0.15 counted
        x402.fetch({"url": URL})
        self.assertEqual(self.core.params("openhuman.x402_fetch")[-1]["maxAmountAtomic"], 100_000)
        x402.fetch({"url": URL})  # 0.20 counted: only 0.05 left
        self.assertEqual(self.core.params("openhuman.x402_fetch")[-1]["maxAmountAtomic"], 50_000)
        self.refused_after = None
        with self.assertRaises(hand.Refused) as caught:
            x402.fetch({"url": URL})
        self.assertEqual(caught.exception.detail["rule"], "per_day_x402")

    def test_a_core_refusal_is_a_refusal(self):
        self.core.answers["openhuman.x402_fetch"] = Failure("X402_REFUSED: amount 200000 is over maxAmountAtomic 100000")
        with self.assertRaises(hand.Refused) as caught:
            x402.fetch({"url": URL})
        self.assertEqual(caught.exception.detail["rule"], "core_x402")
        self.assertEqual(self.rows()[0]["status"], "refused")

    def test_a_payment_over_the_cap_or_not_in_usdc_is_in_doubt(self):
        for answer in (lambda p: paid(p, amount=200_000), lambda p: paid(p, asset="0x" + "4" * 40)):
            self.core.answers["openhuman.x402_fetch"] = answer
            with self.assertRaises(hand.HandError):
                x402.fetch({"url": URL})
        self.assertEqual([r["status"] for r in self.rows()], ["in_doubt", "in_doubt"])

    def test_dry_run_requests_nothing(self):
        self.configure(DRY_RUN="1")
        out = x402.fetch({"url": URL})
        self.assertEqual(out["would_fetch"]["max_usd"], "0.10")
        self.assertNotIn("openhuman.x402_fetch", self.core.methods())
        self.assertEqual(self.rows(), [])

    def test_body_only_with_post_and_no_unknown_fields(self):
        self.refused({"url": URL, "body": "x"}, "fields")
        self.refused({"url": URL, "pay_to": "me"}, "fields")
        out = x402.fetch({"url": URL, "method": "post", "body": "{}", "headers": {"content-type": "application/json"}})
        self.assertEqual(self.core.params("openhuman.x402_fetch")[0]["method"], "POST")
        self.assertEqual(out["paid"]["usd"], "0.05")

    def test_hosts_are_domain_names(self):
        with self.assertRaises(hand.HandError):
            x402.hosts_add("http://x.com", None)
        self.assertEqual([h["host"] for h in x402.hosts_list()["hosts"]], ["api.example.com"])


class X402BudgetTest(Scratch):
    def test_the_module_budget_follows_the_hand_or_closes(self):
        import importlib.util
        from pathlib import Path

        path = Path(__file__).resolve().parents[3] / "bin" / "lib" / "wallet_config.py"
        spec = importlib.util.spec_from_file_location("wallet_config", path)
        wallet_config = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(wallet_config)
        self.assertEqual(wallet_config.x402_budget(caps.load(hand.secrets())), (1, 1, 1))
        self.configure(WALLET_MAX_X402_PER_REQUEST_USD="0.10", WALLET_MAX_X402_PER_DAY_USD="2")
        self.assertEqual(wallet_config.x402_budget(caps.load(hand.secrets())), (100_000, 2_000_000, 62_000_000))
        lines = wallet_config.env_lines({"EVM_RPC_URL_BASE": "https://rpc.example/key"}, "mainnet", caps.load(hand.secrets()))
        self.assertIn("export OPENHUMAN_WALLET_RPC_BASE=https://rpc.example/key", lines)
        self.assertIn("export OPENHUMAN_X402_PER_REQUEST_MAX=100000", lines)
