"""hands/vps against a fake EQVPS: every cap closed by default, an order
reserved before the call and in_doubt when it never got an answer, and no
token or root password in anything the hand prints.

`fixtures/products.json` is the public `GET /products` answer of 2026-11-12,
trimmed to four plans and two systems each."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from x.support import FakeX
from teeny_hands import hand
from teeny_hands.vps import actions

HERE = Path(__file__).resolve().parent
VPS = HERE.parents[1] / "vps"
TOKEN = "eqvps_FAKEtokenFAKEtokenFAKEtoken00"
PASSWORD = "rootpw_FAKEpasswordFAKEpassword00"
PUBKEY = "ssh-ed25519 " + "A" * 68 + " teeny@vps"
T0 = 1_789_369_200.0
OPEN = {"VPS_ORDERS": "1", "VPS_MAX_MONTHLY_USD": "15", "VPS_TOPUP_MAX_USD": "40"}


class VpsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        (self.root / "secrets").mkdir()
        self._env = dict(os.environ)
        os.environ["TEENY_ROOT"] = str(self.root)
        os.environ["TEENY_NOW"] = str(T0)
        os.environ.pop("DRY_RUN", None)
        self.eq = FakeX()
        self.addCleanup(self.eq.close)
        self.eq.route("GET", "/products", json.loads((HERE / "fixtures" / "products.json").read_text()))
        self.configure()

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)
        hand._ENV_CACHE = None
        self.tmp.cleanup()

    def configure(self, **settings):
        values = {"EQVPS_API_BASE": self.eq.base, "EQVPS_API_TOKEN": TOKEN, "VPS_SSH_PUBKEY": PUBKEY, "DRY_RUN": "0", **settings}
        (self.root / "secrets" / "teeny.env").write_text("".join(f"{k}={v}\n" for k, v in values.items() if v is not None))
        hand._ENV_CACHE = None

    def cli(self, *argv):
        done = subprocess.run([sys.executable, "-I", str(VPS), *argv], capture_output=True, text=True,
                              env={**os.environ, "TEENY_ROOT": str(self.root)}, timeout=60)  # fmt: skip
        self.assertNotIn(TOKEN, done.stdout + done.stderr)
        self.assertNotIn(PASSWORD, done.stdout + done.stderr)
        return done.returncode, json.loads(done.stdout)

    def held(self):
        return [dict(r) for r in hand.open_db(actions.SCHEMA).execute("SELECT * FROM vps_orders ORDER BY id")]

    # --- reads ----------------------------------------------------------------------------

    def test_products_are_cheapest_first_and_filtered_by_price(self):
        code, out = self.cli("products", "--max-usd", "15")
        self.assertEqual(code, 0)
        self.assertEqual([p["slug"] for p in out["products"]], ["nano", "micro-ip", "ai-agent-ip"])
        self.assertEqual(out["products"][2]["memory_mb"], 4096)
        self.assertTrue(out["products"][2]["public_ipv4"])
        self.assertFalse(out["caps"]["orders"])
        self.assertNotIn("authorization", self.eq.requests("GET", "/products")[0].headers)

    def test_service_stores_the_one_time_password_and_never_prints_it(self):
        self.eq.route("GET", "/services/SV-1", {"data": {"status": "active", "password": PASSWORD,
                      "access": {"host": "1.2.3.4", "port": 22}}})  # fmt: skip
        code, out = self.cli("service", "SV-1")
        self.assertEqual(code, 0)
        self.assertEqual(out["service"]["access"]["host"], "1.2.3.4")
        self.assertNotIn("password", out["service"])
        stored = self.root / out["stored_secrets"]["file"]
        self.assertEqual(json.loads(stored.read_text()), {"password": PASSWORD})
        self.assertEqual(stored.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.eq.requests("GET", "/services/SV-1")[0].authorization, f"Bearer {TOKEN}")

    # --- top-ups --------------------------------------------------------------------------

    def test_topup_is_refused_until_the_operator_sets_a_cap(self):
        code, out = self.cli("topup", "--usd", "10", "--reason", "a server to move to")
        self.assertEqual((code, out["rule"]), (2, "topup_cap"))
        self.assertEqual(self.eq.writes(), [])

    def test_topups_in_thirty_days_share_one_cap(self):
        self.configure(**OPEN)
        self.eq.route("POST", "/balance/topup", {"checkout_url": "https://payram.eqvps.com/c/1"})
        code, out = self.cli("topup", "--usd", "30", "--reason", "a server to move to")
        self.assertEqual(code, 0)
        self.assertEqual(out["checkout_url"], "https://payram.eqvps.com/c/1")
        self.assertEqual(self.eq.requests("POST", "/balance/topup")[0].json(), {"amount": 30})
        code, out = self.cli("topup", "--usd", "20", "--reason", "a second top-up")
        self.assertEqual((code, out["rule"], out["spent_usd"]), (2, "topup_month", 30))
        os.environ["TEENY_NOW"] = str(T0 + 31 * 86400)
        code, _ = self.cli("topup", "--usd", "20", "--reason", "next month")
        self.assertEqual(code, 0)

    # --- orders ---------------------------------------------------------------------------

    def test_order_is_refused_while_ordering_is_off(self):
        self.configure(VPS_MAX_MONTHLY_USD="15")
        code, out = self.cli("order", "--product", "ai-agent-ip", "--reason", "move off the box")
        self.assertEqual((code, out["rule"]), (2, "orders_off"))
        self.assertEqual(self.eq.writes(), [])

    def test_order_over_the_monthly_cap_is_refused(self):
        self.configure(**OPEN)
        code, out = self.cli("order", "--product", "pro-32", "--reason", "move off the box")
        self.assertEqual((code, out["rule"], out["monthly_usd"]), (2, "price_cap", 40.0))
        self.assertEqual(self.eq.writes(), [])

    def test_dry_run_orders_nothing(self):
        self.configure(**OPEN, DRY_RUN="1")
        code, out = self.cli("order", "--product", "ai-agent-ip", "--reason", "move off the box")
        self.assertEqual((code, out["would_order"], out["os"]), (0, "ai-agent-ip", "Ubuntu 24.04"))
        self.assertEqual(self.eq.writes(), [])
        self.assertEqual(self.held(), [])

    def test_an_order_sends_the_key_records_the_server_and_its_bill(self):
        self.configure(**OPEN)
        self.eq.route("POST", "/orders", {"paid_from_balance": True, "service_id": "SV-9"})
        code, out = self.cli("order", "--product", "ai-agent-ip", "--os", "Debian 12", "--reason", "move off the box")
        self.assertEqual((code, out["service_id"]), (0, "SV-9"))
        self.assertEqual(self.eq.requests("POST", "/orders")[0].json(), {"product": "ai-agent-ip", "os_id": 5, "ssh_key": PUBKEY})
        self.assertEqual([(r["status"], r["service_id"]) for r in self.held()], [("ordered", "SV-9")])
        self.assertEqual(out["bill"], {"name": "eqvps SV-9", "usd": "15", "period": "month"})

    def test_the_server_cap_holds_until_the_operator_releases(self):
        self.configure(**OPEN)
        self.eq.route("POST", "/orders", {"service_id": "SV-9"})
        self.assertEqual(self.cli("order", "--product", "nano", "--reason", "first")[0], 0)
        code, out = self.cli("order", "--product", "nano", "--reason", "second")
        self.assertEqual((code, out["rule"]), (2, "server_cap"))
        self.assertEqual(len(self.eq.requests("POST", "/orders")), 1)
        self.assertEqual(self.cli("release", "1", "--note", "cancelled at EQVPS")[0], 0)
        self.assertEqual(self.cli("order", "--product", "nano", "--reason", "second")[0], 0)

    def test_an_order_without_an_answer_is_in_doubt_and_still_holds_its_slot(self):
        self.configure(**OPEN)
        self.eq.route("POST", "/orders", {"error": "boom"}, status=502)
        code, out = self.cli("order", "--product", "nano", "--reason", "first")
        self.assertEqual(code, 1)
        self.assertIn("in_doubt", out["error"])
        self.assertEqual([r["status"] for r in self.held()], ["in_doubt"])
        code, out = self.cli("order", "--product", "nano", "--reason", "again")
        self.assertEqual((code, out["rule"]), (2, "server_cap"))

    def test_a_rejected_order_frees_its_slot(self):
        self.configure(**OPEN)
        self.eq.route("POST", "/orders", {"message": "bad os"}, status=422)
        code, out = self.cli("order", "--product", "nano", "--reason", "first")
        self.assertEqual(code, 1)
        self.assertEqual([r["status"] for r in self.held()], ["failed"])

    def test_an_unfunded_order_keeps_its_invoice(self):
        self.configure(**OPEN)
        self.eq.route("POST", "/orders", {"invoice": {"id": 77}})
        code, out = self.cli("order", "--product", "nano", "--reason", "first")
        self.assertEqual((code, out["invoice_id"]), (0, "77"))
        self.assertEqual([r["status"] for r in self.held()], ["unfunded"])

    # --- the operator -----------------------------------------------------------------------

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
