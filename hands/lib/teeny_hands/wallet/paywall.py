"""A paid endpoint (docs/roadmap.md, S4): one x402-priced resource that
Teeny produces, sold for USDC on Solana.

    GET /stats    Teeny's latest public stats snapshot (the numbers
                  bin/receipts publishes), as JSON

served by `bin/paywall` (the `paywall` service in deploy/compose.yaml) at
https://x402.<DEPLOY_DOMAIN>/stats behind the deploy vhost
(deploy/nginx/sites.teeny.enamakel.com.conf). The price is frozen config
(evals/prices.toml, `stats_endpoint_usdc`).

The x402 exchange, from the seller's side (protocol version 1, scheme
`exact`):

  1. A request with no `X-PAYMENT` header gets 402 and the payment
     requirements: this much USDC (the cluster's pinned mint), to Teeny's own
     Solana address, for this resource.
  2. A request with one: the header (base64 JSON) is sent with the
     requirements to the facilitator's `/verify`, then `/settle`
     (X402_FACILITATOR_URL). The facilitator submits the transaction the
     buyer signed; Teeny's wallet signs nothing and pays nothing.
  3. Settled: 200 with the resource and `X-PAYMENT-RESPONSE`, and one row in
     the inflow ledger (`wallet_inflows`, category `sale`, ref
     `x402:stats`) and one in `x402_sales`. A settlement whose transaction
     was seen before is refused (`replay`), and so is one that does not
     pay Teeny's address the price.

Off until X402_ENDPOINT=1 (404 until then). The buyer's payload is a
stranger's data: it is passed to the facilitator and never read for
anything but its version, scheme and network.
"""

from __future__ import annotations

import base64
import binascii
import json
import sqlite3
import urllib.error
import urllib.request
from typing import Any, Mapping

from .. import hand
from . import accounts, caps, reads, sales, store
from .pay import own_address

Json = dict[str, Any]

RESOURCES = ("/stats",)
X402_VERSION = 1
TIMEOUT = 30.0
#: The stats a buyer gets: the same allowlist bin/receipts publishes.
STATS_ALLOWED: dict[str, tuple[str, ...]] = {
    "x": ("followers", "following", "posts"),
    "moltbook": ("karma", "followers", "following", "posts", "comments"),
    "github": ("followers", "public_repos", "stars"),
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS x402_sales (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    at          REAL NOT NULL,
    resource    TEXT NOT NULL,
    transaction_sig TEXT NOT NULL UNIQUE,
    payer       TEXT,
    amount_raw  INTEGER NOT NULL
);
"""


def price_raw() -> int:
    data = sales.prices_raw()
    try:
        micro = caps.dollars_to_micro(data["prices"]["stats_endpoint_usdc"], "stats_endpoint_usdc")
    except (KeyError, TypeError, caps.CapsError) as exc:
        raise hand.HandError(f"evals/prices.toml: {exc}") from None
    if micro <= 0:
        raise hand.HandError("evals/prices.toml: stats_endpoint_usdc is above zero")
    return micro  # USDC has 6 decimals: micro-dollars are atomic units


def _network(cluster: str) -> str:
    return "solana" if cluster == "mainnet" else "solana-devnet"


def requirements(resource: str) -> Json:
    cluster = reads._cluster()
    req: Json = {
        "scheme": "exact",
        "network": _network(cluster),
        "maxAmountRequired": str(price_raw()),
        "resource": f"https://x402.{_domain()}{resource}",
        "description": "Teeny's latest public stats snapshot",
        "mimeType": "application/json",
        "payTo": own_address(),
        "maxTimeoutSeconds": 60,
        "asset": caps.USDC_MINTS[cluster],
    }
    fee_payer = hand.setting("X402_FEE_PAYER")
    if fee_payer:
        req["extra"] = {"feePayer": fee_payer}
    return req


def _domain() -> str:
    from ..deploy import publish

    return publish.domain()


def stats() -> Json:
    conn = hand.db()
    try:
        row = conn.execute("SELECT max(date) AS date FROM stats").fetchone()
        date = row["date"] if row else None
        out: Json = {"date": date}
        if date:
            for r in conn.execute("SELECT source, metric, value FROM stats WHERE date = ?", (date,)):
                if r["metric"] in STATS_ALLOWED.get(r["source"], ()):
                    out.setdefault(r["source"], {})[r["metric"]] = r["value"]
    except sqlite3.OperationalError:
        out = {"date": None}
    finally:
        conn.close()
    return out


def _facilitator(path: str, body: Json) -> Json:
    base = (hand.setting("X402_FACILITATOR_URL") or "").rstrip("/")
    if not base.startswith("https://") and not base.startswith("http://127.0.0.1"):
        raise hand.HandError("X402_FACILITATOR_URL is not an https URL")
    request = urllib.request.Request(base + path, data=json.dumps(body).encode(), method="POST",
                                     headers={"content-type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as answer:
            got = json.loads(answer.read(1_000_000) or b"{}")
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise hand.HandError(f"the facilitator's {path} did not answer ({type(exc).__name__})") from None
    if not isinstance(got, dict):
        raise hand.HandError(f"the facilitator's {path} sent no object")
    return got


def _payment(header: str) -> Json:
    try:
        payload = json.loads(base64.b64decode(header, validate=True))
    except (binascii.Error, ValueError):
        raise hand.Refused("X-PAYMENT is not base64 JSON", rule="payload") from None
    if not isinstance(payload, dict) or payload.get("x402Version") != X402_VERSION or payload.get("scheme") != "exact":
        raise hand.Refused("X-PAYMENT is not an x402 v1 exact payment", rule="payload")
    return payload


def _record(resource: str, settled: Mapping[str, Any], req: Mapping[str, Any]) -> None:
    signature = str(settled.get("transaction") or "")
    payer = settled.get("payer") if isinstance(settled.get("payer"), str) else None
    raw = int(req["maxAmountRequired"])
    now = hand.now()
    conn = accounts.db()
    try:
        conn.executescript(SCHEMA)
        with hand.transaction(conn):
            conn.execute("INSERT INTO x402_sales (at, resource, transaction_sig, payer, amount_raw) VALUES (?, ?, ?, ?, ?)",
                         (now, resource, signature, payer, raw))
            conn.execute(
                "INSERT INTO wallet_inflows (signature, cluster, asset, amount_raw, usd_micro, sender, at, seen_at, category, ref)"
                " VALUES (?, ?, 'USDC', ?, ?, ?, ?, ?, 'sale', ?)"
                " ON CONFLICT (signature, asset) DO UPDATE SET category = 'sale', ref = excluded.ref",
                (signature, reads._cluster(), raw, raw, payer, now, now, f"x402:{resource.strip('/')}"))
    finally:
        conn.close()


def _seen(signature: str) -> bool:
    conn = accounts.db()
    try:
        conn.executescript(SCHEMA)
        return conn.execute("SELECT 1 FROM x402_sales WHERE transaction_sig = ?", (signature,)).fetchone() is not None
    finally:
        conn.close()


def handle(method: str, path: str, headers: Mapping[str, str]) -> tuple[int, dict[str, str], Json]:
    """One request: (status, extra headers, JSON body)."""
    if path == "/health":
        return 200, {}, {"ok": True}
    if not hand.flag("X402_ENDPOINT", False) or path not in RESOURCES:
        return 404, {}, {"error": "not found"}
    if method not in ("GET", "HEAD"):
        return 405, {}, {"error": "GET only"}
    req = requirements(path)
    header = next((v for k, v in headers.items() if k.lower() == "x-payment"), None)
    if not header:
        return 402, {}, {"x402Version": X402_VERSION, "error": "X-PAYMENT header is required", "accepts": [req]}

    def refuse(why: str) -> tuple[int, dict[str, str], Json]:
        return 402, {}, {"x402Version": X402_VERSION, "error": why, "accepts": [req]}

    try:
        payload = _payment(header)
    except hand.Refused as refused:
        return refuse(refused.reason)
    if payload.get("network") != req["network"]:
        return refuse("wrong network")
    body = {"x402Version": X402_VERSION, "paymentPayload": payload, "paymentRequirements": req}
    try:
        verified = _facilitator("/verify", body)
        if verified.get("isValid") is not True:
            return refuse("the payment did not verify")
        settled = _facilitator("/settle", body)
    except hand.HandError as exc:
        return 502, {}, {"error": str(exc)}
    signature = settled.get("transaction")
    if settled.get("success") is not True or not isinstance(signature, str) or not signature:
        return refuse("the payment did not settle")
    if settled.get("network") not in (None, req["network"]):
        return refuse("settled on the wrong network")
    if _seen(signature):
        return refuse("that payment was already used")
    _record(path, settled, req)
    receipt = base64.b64encode(json.dumps({"success": True, "transaction": signature, "network": req["network"],
                                           "payer": settled.get("payer")}).encode()).decode()
    return 200, {"X-PAYMENT-RESPONSE": receipt}, stats()


def serve(host: str, port: int) -> None:
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args: Any) -> None:
            pass

        def _answer(self) -> None:
            try:
                status, extra, body = handle(self.command, self.path.split("?", 1)[0], dict(self.headers))
            except Exception:  # noqa: BLE001 - a buyer gets a status, never a trace
                status, extra, body = 500, {}, {"error": "internal"}
            data = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(data)))
            self.send_header("cache-control", "no-store")
            for key, value in extra.items():
                self.send_header(key, value)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(data)

        do_GET = do_HEAD = do_POST = _answer

    ThreadingHTTPServer((host, port), Handler).serve_forever()
