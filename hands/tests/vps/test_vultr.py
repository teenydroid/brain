"""hands/vps on Vultr, against a fake: every cap closed by default, an order
reserved before the call and in_doubt when it never got an answer, only the
operator destroys, and no key or root password in anything printed."""

from __future__ import annotations

import json
import unittest

from vps.support import IID, OPEN, PASSWORD, PUBKEY, VULTR_KEY, Scratch, instance  # noqa: F401


class VultrTest(Scratch):
    # --- reads ----------------------------------------------------------------------------

    def test_plans_are_the_regions_cheapest_first_and_need_no_key(self):
        self.configure(VULTR_API_KEY=None)
        code, out = self.cli("plans", "--provider", "vultr", "--max-usd", "24", "--region", "fra")
        self.assertEqual(code, 0)
        self.assertEqual([p["plan"] for p in out["plans"]], ["vc2-1c-1gb", "vc2-2c-4gb", "vhp-2c-4gb-amd"])
        self.assertEqual(out["plans"][1]["memory_mb"], 4096)
        self.assertFalse(out["caps"]["orders"])
        self.assertNotIn("authorization", self.vultr.requests("GET", "/plans")[0].headers)

    def test_account_reads_the_credit_with_the_key(self):
        self.vultr.route("GET", "/account", {"account": {"balance": -25.5, "pending_charges": 1.2, "email": "x@y.z"}})
        code, out = self.cli("account")
        self.assertEqual((code, out["balance"], out["pending_charges"]), (0, -25.5, 1.2))
        self.assertNotIn("email", out)
        self.assertEqual(self.vultr.requests("GET", "/account")[0].authorization, f"Bearer {VULTR_KEY}")

    def test_server_stores_a_password_and_never_prints_it(self):
        self.vultr.route("GET", f"/instances/{IID}", {"instance": instance("active", "203.0.113.7", default_password=PASSWORD)})
        code, out = self.cli("server", "--provider", "vultr", IID)
        self.assertEqual(code, 0)
        self.assertEqual(out["server"]["main_ip"], "203.0.113.7")
        stored = self.root / out["stored_secrets"]["file"]
        self.assertEqual(json.loads(stored.read_text()), {"default_password": PASSWORD})
        self.assertEqual(stored.name, f"vultr-{IID}.json")
        self.assertEqual(stored.stat().st_mode & 0o777, 0o600)

    # --- orders ---------------------------------------------------------------------------

    def test_order_is_refused_while_ordering_is_off(self):
        self.configure(VPS_MAX_MONTHLY_USD="24")
        code, out = self.cli("order", "--provider", "vultr", "--plan", "vc2-2c-4gb", "--reason", "move off the box")
        self.assertEqual((code, out["rule"]), (2, "orders_off"))
        self.assertEqual(self.vultr.writes(), [])

    def test_order_over_the_monthly_cap_is_refused(self):
        self.configure(**OPEN)
        code, out = self.cli("order", "--provider", "vultr", "--plan", "vc2-4c-8gb", "--reason", "move off the box")
        self.assertEqual((code, out["rule"], out["monthly_usd"]), (2, "price_cap", 40.0))
        self.assertEqual(self.vultr.writes(), [])

    def test_order_in_a_region_the_plan_is_not_sold_in_is_refused(self):
        self.configure(**OPEN)
        code, out = self.cli("order", "--provider", "vultr", "--plan", "vc2-2c-4gb", "--region", "sjc", "--reason", "move off the box")
        self.assertEqual((code, out["rule"]), (2, "region"))

    def test_dry_run_orders_nothing(self):
        self.configure(**OPEN, DRY_RUN="1")
        code, out = self.cli("order", "--provider", "vultr", "--plan", "vc2-2c-4gb", "--reason", "move off the box")
        self.assertEqual((code, out["would_order"], out["region"]), (0, "vc2-2c-4gb", "bom"))
        self.assertEqual(self.vultr.writes(), [])
        self.assertEqual(self.orders(), [])

    def test_an_order_adds_the_key_once_records_the_server_and_its_bill(self):
        self.configure(**OPEN)
        self.vultr_sells()
        code, out = self.cli("order", "--provider", "vultr", "--plan", "vc2-2c-4gb", "--reason", "move off the box")
        self.assertEqual((code, out["server_id"]), (0, IID))
        sent = self.vultr.requests("POST", "/instances")[0].json()
        self.assertEqual((sent["plan"], sent["region"], sent["os_id"], sent["sshkey_id"]), ("vc2-2c-4gb", "bom", 2284, ["key-1"]))
        self.assertEqual([(r["status"], r["server_id"]) for r in self.orders()], [("ordered", IID)])
        self.assertEqual(out["bill"], {"name": "vultr cb676a46-66f", "usd": "20.00", "period": "month"})
        self.assertEqual(json.loads((self.root / out["stored_secrets"]["file"]).read_text()), {"default_password": PASSWORD})
        # A key already on the account is reused, not added again.
        self.vultr.route("GET", "/ssh-keys", {"ssh_keys": [{"id": "key-1", "ssh_key": PUBKEY.rsplit(" ", 1)[0]}]})
        self.configure(**OPEN, VPS_MAX_SERVERS="2")
        self.assertEqual(self.cli("order", "--provider", "vultr", "--plan", "vc2-1c-1gb", "--reason", "a second one")[0], 0)
        self.assertEqual(len(self.vultr.requests("POST", "/ssh-keys")), 1)

    def test_the_server_cap_holds_until_the_operator_releases(self):
        self.configure(**OPEN)
        self.vultr_sells()
        self.assertEqual(self.cli("order", "--provider", "vultr", "--plan", "vc2-1c-1gb", "--reason", "first")[0], 0)
        code, out = self.cli("order", "--provider", "vultr", "--plan", "vc2-1c-1gb", "--reason", "second")
        self.assertEqual((code, out["rule"]), (2, "server_cap"))
        self.assertEqual(len(self.vultr.requests("POST", "/instances")), 1)
        self.assertEqual(self.cli("release", "1", "--note", "destroyed by hand")[0], 0)
        self.assertEqual(self.cli("order", "--provider", "vultr", "--plan", "vc2-1c-1gb", "--reason", "second")[0], 0)

    def test_an_order_without_an_answer_is_in_doubt_and_still_holds_its_slot(self):
        self.configure(**OPEN)
        self.vultr.route("POST", "/instances", {"error": "boom"}, status=502)
        code, out = self.cli("order", "--provider", "vultr", "--plan", "vc2-1c-1gb", "--reason", "first")
        self.assertEqual(code, 1)
        self.assertIn("in_doubt", out["error"])
        self.assertEqual([r["status"] for r in self.orders()], ["in_doubt"])
        code, out = self.cli("order", "--provider", "vultr", "--plan", "vc2-1c-1gb", "--reason", "again")
        self.assertEqual((code, out["rule"]), (2, "server_cap"))
        self.assertEqual(len(self.vultr.requests("POST", "/instances")), 1)

    def test_a_rejected_order_frees_its_slot(self):
        self.configure(**OPEN)
        self.vultr.route("POST", "/instances", {"error": "Insufficient account balance"}, status=400)
        code, out = self.cli("order", "--provider", "vultr", "--plan", "vc2-1c-1gb", "--reason", "first")
        self.assertEqual(code, 1)
        self.assertIn("Insufficient", out["error"])
        self.assertEqual([r["status"] for r in self.orders()], ["failed"])

    # --- the operator -----------------------------------------------------------------------

    def test_destroy_stops_only_a_server_this_hand_holds_and_drops_its_bill(self):
        self.configure(**OPEN)
        code, out = self.cli("destroy", "--provider", "vultr", IID, "--note", "moved back")
        self.assertEqual((code, out["rule"]), (2, "unknown"))
        self.vultr_sells()
        self.assertEqual(self.cli("order", "--provider", "vultr", "--plan", "vc2-2c-4gb", "--reason", "first")[0], 0)
        self.vultr.route("DELETE", f"/instances/{IID}", None, status=204, raw=b"")
        code, out = self.cli("destroy", "--provider", "vultr", IID, "--note", "moved back")
        self.assertEqual((code, out["destroyed"]), (0, IID))
        self.assertEqual([r["status"] for r in self.orders()], ["released"])
        self.assertEqual(self.bills(), [])


if __name__ == "__main__":
    unittest.main()
