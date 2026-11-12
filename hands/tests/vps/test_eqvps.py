"""hands/vps on EQVPS, against a fake: top-ups capped over 30 days and never
paid by the hand, orders from the prepaid balance under the same caps as
Vultr's, and the server cap shared by both providers."""

from __future__ import annotations

import json
import os
import unittest

from vps.support import EQVPS_TOKEN, OPEN, PASSWORD, PUBKEY, T0, Scratch


class EqvpsTest(Scratch):
    def eq_order(self, plan="ai-agent-ip", *more):
        return self.cli("order", "--provider", "eqvps", "--plan", plan, "--reason", "move off the box", *more)

    def test_plans_are_cheapest_first_with_their_public_address(self):
        code, out = self.cli("plans", "--provider", "eqvps", "--max-usd", "15")
        self.assertEqual(code, 0)
        self.assertEqual([p["plan"] for p in out["plans"]], ["nano", "micro-ip", "ai-agent-ip"])
        self.assertEqual([p["public_ipv4"] for p in out["plans"]], [False, True, True])
        self.assertNotIn("authorization", self.eq.requests("GET", "/products")[0].headers)

    def test_eqvps_takes_no_region(self):
        code, out = self.cli("plans", "--provider", "eqvps", "--region", "fra")
        self.assertEqual(code, 1)
        self.assertIn("--region is for Vultr", out["error"])

    def test_service_stores_the_one_time_password_and_never_prints_it(self):
        self.eq.route("GET", "/services/SV-1", {"data": {"status": "active", "password": PASSWORD,
                      "access": {"host": "1.2.3.4", "port": 20123}}})  # fmt: skip
        code, out = self.cli("server", "--provider", "eqvps", "SV-1")
        self.assertEqual((code, out["address"]), (0, "1.2.3.4:20123"))
        self.assertNotIn("password", out["server"])
        stored = self.root / out["stored_secrets"]["file"]
        self.assertEqual(json.loads(stored.read_text()), {"password": PASSWORD})
        self.assertEqual(stored.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.eq.requests("GET", "/services/SV-1")[0].authorization, f"Bearer {EQVPS_TOKEN}")

    # --- top-ups ------------------------------------------------------------------------------

    def test_topup_is_refused_until_the_operator_sets_a_cap(self):
        code, out = self.cli("topup", "--usd", "10", "--reason", "a server to move to")
        self.assertEqual((code, out["rule"]), (2, "topup_cap"))
        self.assertEqual(self.eq.writes(), [])

    def test_topups_in_thirty_days_share_one_cap(self):
        self.configure(**OPEN)
        self.eq.route("POST", "/balance/topup", {"checkout_url": "https://payram.eqvps.com/c/1"})
        code, out = self.cli("topup", "--usd", "30", "--reason", "a server to move to")
        self.assertEqual((code, out["checkout_url"]), (0, "https://payram.eqvps.com/c/1"))
        self.assertEqual(self.eq.requests("POST", "/balance/topup")[0].json(), {"amount": 30})
        code, out = self.cli("topup", "--usd", "20", "--reason", "a second top-up")
        self.assertEqual((code, out["rule"], out["spent_usd"]), (2, "topup_month", 30))
        os.environ["TEENY_NOW"] = str(T0 + 31 * 86400)
        self.assertEqual(self.cli("topup", "--usd", "20", "--reason", "next month")[0], 0)

    def test_dry_run_tops_up_nothing(self):
        self.configure(**OPEN, DRY_RUN="1")
        code, out = self.cli("topup", "--usd", "10", "--reason", "a server to move to")
        self.assertEqual((code, out["would_topup_usd"]), (0, 10))
        self.assertEqual(self.eq.writes(), [])

    # --- orders ---------------------------------------------------------------------------------

    def test_order_is_refused_while_ordering_is_off(self):
        self.configure(VPS_MAX_MONTHLY_USD="24")
        self.assertEqual(self.eq_order()[1]["rule"], "orders_off")
        self.assertEqual(self.eq.writes(), [])

    def test_order_over_the_monthly_cap_is_refused(self):
        self.configure(**OPEN)
        code, out = self.eq_order("pro-32")
        self.assertEqual((code, out["rule"], out["monthly_usd"]), (2, "price_cap", 40.0))

    def test_an_order_sends_the_key_and_records_the_server_and_its_bill(self):
        self.configure(**OPEN)
        self.eqvps_sells()
        code, out = self.eq_order("ai-agent-ip", "--os", "Debian 12")
        self.assertEqual((code, out["server_id"]), (0, "SV-9"))
        self.assertEqual(self.eq.requests("POST", "/orders")[0].json(), {"product": "ai-agent-ip", "os_id": 5, "ssh_key": PUBKEY})
        self.assertEqual([(r["provider"], r["status"], r["server_id"], r["region"]) for r in self.orders()],
                         [("eqvps", "ordered", "SV-9", "")])  # fmt: skip
        self.assertEqual(out["bill"], {"name": "eqvps SV-9", "usd": "15.00", "period": "month"})

    def test_an_unfunded_order_keeps_its_invoice_and_its_slot(self):
        self.configure(**OPEN)
        self.eq.route("POST", "/orders", {"invoice": {"id": 77}})
        code, out = self.eq_order("nano")
        self.assertEqual((code, out["invoice_id"]), (0, "77"))
        self.assertEqual([r["status"] for r in self.orders()], ["unfunded"])
        self.assertEqual(self.eq_order("nano")[1]["rule"], "server_cap")

    def test_an_order_without_an_answer_is_in_doubt(self):
        self.configure(**OPEN)
        self.eq.route("POST", "/orders", {"error": "boom"}, status=503)
        code, out = self.eq_order("nano")
        self.assertEqual(code, 1)
        self.assertIn("in_doubt", out["error"])
        self.assertEqual([r["status"] for r in self.orders()], ["in_doubt"])

    def test_the_server_cap_spans_both_providers(self):
        self.configure(**OPEN)
        self.vultr_sells()
        self.eqvps_sells()
        self.assertEqual(self.cli("order", "--provider", "vultr", "--plan", "vc2-2c-4gb", "--reason", "first")[0], 0)
        code, out = self.eq_order("nano")
        self.assertEqual((code, out["rule"]), (2, "server_cap"))
        self.assertEqual(self.eq.writes(), [])

    def test_destroy_is_refused_on_eqvps_and_release_drops_the_bill(self):
        self.configure(**OPEN)
        self.eqvps_sells()
        self.assertEqual(self.eq_order("nano")[0], 0)
        code, out = self.cli("destroy", "--provider", "eqvps", "SV-9", "--note", "moving back")
        self.assertEqual((code, out["rule"]), (2, "no_cancel"))
        self.assertEqual(self.eq.writes(), self.eq.requests("POST", "/orders"))
        self.assertEqual(self.cli("release", "1", "--note", "cancelled in the console")[0], 0)
        self.assertEqual(self.bills(), [])

    # --- the operator -------------------------------------------------------------------------

    def test_register_prints_the_token_once_and_stores_it_nowhere(self):
        self.configure(EQVPS_API_TOKEN=None)
        self.eq.route("POST", "/auth/register", {"token": "eqvps_NEWtoken", "token_type": "Bearer"})
        code, out = self.cli("register", "--first", "Teeny", "--last", "Droid", "--email", "teeny@example.com")
        self.assertEqual((code, out["token"]), (0, "eqvps_NEWtoken"))
        self.assertNotIn("authorization", self.eq.requests("POST", "/auth/register")[0].headers)
        leaked = [p for p in self.root.rglob("*") if p.is_file() and b"eqvps_NEWtoken" in p.read_bytes()]
        self.assertEqual(leaked, [])


if __name__ == "__main__":
    unittest.main()
