"""Shared by the hands/vps tests: a scratch TEENY_ROOT and a fake Vultr and
fake EQVPS on 127.0.0.1 (hands/x's fake server). Nothing here talks to
either provider.

The fixtures are the public answers of 2026-11-12, trimmed: Vultr's
`GET /v2/plans` (four plans in three regions) and `GET /v2/os` (two
systems), and EQVPS's `GET /products` (four plans, two systems each)."""

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
VULTR_KEY = "VULTRFAKEkeyFAKEkeyFAKEkeyFAKEkey00"
EQVPS_TOKEN = "eqvps_FAKEtokenFAKEtokenFAKEtoken00"
PASSWORD = "rootpw_FAKEpasswordFAKEpassword00"
PUBKEY = "ssh-ed25519 " + "A" * 68 + " teeny@vps"
IID = "cb676a46-66fd-4dfb-b839-443f2e6c0b60"
T0 = 1_789_369_200.0
OPEN = {"VPS_ORDERS": "1", "VPS_MAX_MONTHLY_USD": "24", "VPS_TOPUP_MAX_USD": "40"}


def fixture(name: str):
    return json.loads((HERE / "fixtures" / f"{name}.json").read_text())


def instance(status="pending", main_ip="0.0.0.0", **more):
    return {"id": IID, "label": "teeny", "status": status, "power_status": "running", "server_status": "none",
            "main_ip": main_ip, "region": "bom", "plan": "vc2-2c-4gb", "os": "Ubuntu 24.04 LTS x64", **more}  # fmt: skip


class Scratch(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        (self.root / "secrets").mkdir()
        self._env = dict(os.environ)
        os.environ["TEENY_ROOT"] = str(self.root)
        os.environ["TEENY_NOW"] = str(T0)
        os.environ.pop("DRY_RUN", None)
        self.vultr = FakeX()
        self.eq = FakeX()
        self.addCleanup(self.vultr.close)
        self.addCleanup(self.eq.close)
        self.vultr.route("GET", "/plans", fixture("plans"))
        self.vultr.route("GET", "/os", fixture("os"))
        self.vultr.route("GET", "/ssh-keys", {"ssh_keys": [], "meta": {"total": 0}})
        self.vultr.route("POST", "/ssh-keys", {"ssh_key": {"id": "key-1", "name": "teeny", "ssh_key": PUBKEY}}, status=201)
        self.eq.route("GET", "/products", fixture("products"))
        self.configure()

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)
        hand._ENV_CACHE = None
        self.tmp.cleanup()

    def configure(self, **settings):
        values = {"VULTR_API_BASE": self.vultr.base, "VULTR_API_KEY": VULTR_KEY, "EQVPS_API_BASE": self.eq.base,
                  "EQVPS_API_TOKEN": EQVPS_TOKEN, "VPS_SSH_PUBKEY": PUBKEY, "DRY_RUN": "0", **settings}  # fmt: skip
        (self.root / "secrets" / "teeny.env").write_text("".join(f"{k}={v}\n" for k, v in values.items() if v is not None))
        hand._ENV_CACHE = None

    def cli(self, *argv):
        done = subprocess.run([sys.executable, "-I", str(VPS), *argv], capture_output=True, text=True,
                              env={**os.environ, "TEENY_ROOT": str(self.root)}, timeout=60)  # fmt: skip
        for secret in (VULTR_KEY, EQVPS_TOKEN, PASSWORD):
            self.assertNotIn(secret, done.stdout + done.stderr)
        return done.returncode, json.loads(done.stdout)

    def orders(self):
        return [dict(r) for r in hand.open_db(actions.SCHEMA).execute("SELECT * FROM vps_orders ORDER BY id")]

    def vultr_sells(self):
        self.vultr.route("POST", "/instances", {"instance": instance(default_password=PASSWORD)}, status=202)

    def eqvps_sells(self, sid="SV-9"):
        self.eq.route("POST", "/orders", {"paid_from_balance": True, "service_id": sid})

    def bills(self):
        return [dict(r) for r in hand.db().execute("SELECT name, usd_micro FROM bills ORDER BY name")]
