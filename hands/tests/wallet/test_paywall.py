"""The paid endpoint (docs/roadmap.md, S4): a request without payment gets 402
and the frozen price to Teeny's address; a fixture x402 payment the
facilitator verifies and settles returns the resource and writes one inflow
row, a `sale`; a replayed payment, a wrong network, a failed verify or a
settlement the chain shows underpaid get nothing. Off until X402_ENDPOINT=1.
The core, the chain and the facilitator are faked; Teeny's wallet signs
nothing."""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from wallet.support import MOVING, OWN, STRANGER, T0, USDC_MAINNET, Scratch
from wallet.test_accounts import usdc_tx
from teeny_hands import hand
from teeny_hands.wallet import accounts, paywall

PRICE = 10_000
TX = "5x402Settle" + "D" * 77


class FakeFacilitator:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.verify: dict[str, Any] = {"isValid": True, "payer": STRANGER}
        self.settle: dict[str, Any] = {"success": True, "transaction": TX, "network": "solana", "payer": STRANGER}
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: Any) -> None:
                pass

            def do_POST(self) -> None:
                body = json.loads(self.rfile.read(int(self.headers["content-length"])))
                fake.calls.append((self.path, body))
                data = json.dumps(fake.verify if self.path == "/verify" else fake.settle).encode()
                self.send_response(200)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True).start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_port}"

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


def header(network: str = "solana") -> str:
    payload = {"x402Version": 1, "scheme": "exact", "network": network, "payload": {"transaction": "base64-signed-by-the-buyer"}}
    return base64.b64encode(json.dumps(payload).encode()).decode()


class PaywallTest(Scratch):
    settings = {"WALLET_CLUSTER": "mainnet", "X402_ENDPOINT": "1"}

    def setUp(self) -> None:
        super().setUp()
        self.facilitator = FakeFacilitator()
        self.addCleanup(self.facilitator.close)
        self.configure(X402_FACILITATOR_URL=self.facilitator.url)
        self.chain.answers["getTransaction"] = lambda p: usdc_tx(STRANGER, PRICE)
        conn = hand.db()
        conn.execute("CREATE TABLE IF NOT EXISTS stats (date TEXT, source TEXT, metric TEXT, value REAL, at REAL)")
        conn.executemany("INSERT INTO stats VALUES (?, ?, ?, ?, 1)", [
            ("2026-09-14", "x", "followers", 120), ("2026-09-14", "x", "impressions", 5000), ("2026-09-13", "x", "followers", 90)])
        conn.commit()
        conn.close()

    def get(self, payment: str | None = None):
        return paywall.handle("GET", "/stats", {"X-PAYMENT": payment} if payment else {})

    def inflows(self) -> list[dict]:
        conn = accounts.db()
        try:
            return [dict(r) for r in conn.execute("SELECT * FROM wallet_inflows")]
        finally:
            conn.close()

    def test_no_payment_gets_the_frozen_price_to_teenys_address(self):
        status, _, body = self.get()
        self.assertEqual(status, 402)
        (req,) = body["accepts"]
        self.assertEqual((req["scheme"], req["network"], req["maxAmountRequired"], req["payTo"], req["asset"]),
                         ("exact", "solana", str(PRICE), OWN, USDC_MAINNET))
        self.assertEqual(self.facilitator.calls, [])

    def test_a_settled_payment_returns_the_resource_and_writes_an_inflow(self):
        status, extra, body = self.get(header())
        self.assertEqual(status, 200, body)
        self.assertEqual(body, {"date": "2026-09-14", "x": {"followers": 120}})
        receipt = json.loads(base64.b64decode(extra["X-PAYMENT-RESPONSE"]))
        self.assertEqual(receipt["transaction"], TX)
        self.assertEqual([p for p, _ in self.facilitator.calls], ["/verify", "/settle"])
        sent = self.facilitator.calls[0][1]["paymentRequirements"]
        self.assertEqual((sent["payTo"], sent["maxAmountRequired"]), (OWN, str(PRICE)))
        (row,) = self.inflows()
        self.assertEqual((row["signature"], row["category"], row["ref"], row["amount_raw"], row["sender"]),
                         (TX, "sale", "x402:stats", PRICE, STRANGER))
        # Teeny's wallet moved nothing and signed nothing.
        self.assertEqual(self.moved(), [])

    def test_the_scan_seeing_the_same_transaction_keeps_it_a_sale(self):
        self.get(header())
        conn = accounts.db()
        conn.execute("INSERT OR IGNORE INTO wallet_inflows (signature, cluster, asset, amount_raw, usd_micro, at, seen_at, category)"
                     " VALUES (?, 'mainnet', 'USDC', ?, ?, ?, ?, 'tip')", (TX, PRICE, PRICE, T0, T0))
        conn.commit()
        conn.close()
        self.assertEqual([r["category"] for r in self.inflows()], ["sale"])

    def test_a_replayed_payment_gets_nothing(self):
        self.assertEqual(self.get(header())[0], 200)
        status, _, body = self.get(header())
        self.assertEqual((status, body["error"]), (402, "that payment was already used"))
        self.assertEqual(len(self.inflows()), 1)

    def test_refusals_get_402_and_no_inflow(self):
        cases = [
            ("garbage", None, "X-PAYMENT is not base64 JSON"),
            (header("solana-devnet"), None, "wrong network"),
            (header(), {"verify": {"isValid": False}}, "the payment did not verify"),
            (header(), {"settle": {"success": False}}, "the payment did not settle"),
        ]
        for payment, fake, error in cases:
            with self.subTest(error=error):
                for key, value in (fake or {}).items():
                    setattr(self.facilitator, key, value)
                status, _, body = self.get(payment)
                self.assertEqual((status, body["error"]), (402, error))
        self.assertEqual(self.inflows(), [])

    def test_a_settlement_the_chain_shows_underpaid_is_refused(self):
        self.chain.answers["getTransaction"] = lambda p: usdc_tx(STRANGER, PRICE - 1)
        status, _, body = self.get(header())
        self.assertEqual(status, 402)
        self.assertIn("does not pay", body["error"])
        self.assertEqual(self.inflows(), [])

    def test_off_until_switched_on(self):
        self.configure(X402_ENDPOINT=None, X402_FACILITATOR_URL=self.facilitator.url)
        self.assertEqual(self.get(header())[0], 404)
        self.assertEqual(paywall.handle("GET", "/other", {})[0], 404)
        self.assertEqual(self.facilitator.calls, [])

    def test_a_facilitator_that_is_not_https_is_never_called(self):
        self.configure(X402_FACILITATOR_URL="http://facilitator.example")
        self.assertEqual(self.get(header())[0], 502)

    def test_the_service_answers_over_http(self):
        port = 39000 + os.getpid() % 500
        server = subprocess.Popen([sys.executable, str(Path(__file__).resolve().parents[3] / "bin" / "paywall"),
                                   "--host", "127.0.0.1", "--port", str(port)], env=os.environ,
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(server.kill)
        for _ in range(100):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=1)
                break
            except (urllib.error.URLError, OSError):
                time.sleep(0.05)
        with self.assertRaises(urllib.error.HTTPError) as no:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/stats", timeout=5)
        self.assertEqual(no.exception.code, 402)
        got = urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{port}/stats", headers={"X-PAYMENT": header()}), timeout=10)
        self.assertEqual(json.loads(got.read())["x"], {"followers": 120})
