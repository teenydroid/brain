"""A paid endpoint (docs/roadmap.md, S4): one x402-priced resource that
Teeny produces, sold for USDC on Solana.

    GET /stats    Teeny's latest public stats snapshot (the numbers
                  bin/receipts publishes), as JSON

    POST /attest  one challenge answer graded against the challenge text
                  (the attest corpus's grader, hands/lib/teeny_hands/
                  wallet/attest.py): the body carries the challenge text,
                  the operation the solver applied and the answer, the
                  reply says whether the text attests the operation
                  ('attested' ships, 'not-attested' and
                  'attested-but-ambiguous' do not)

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
     was seen before is refused, and so is one the chain shows does not
     pay Teeny's address the price (read back with getTransaction; a chain
     that cannot be read leaves it to the facilitator's own check).

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

RESOURCES = ("/stats", "/attest")
X402_VERSION = 1
#: The corpus the /attest grader was measured on (forgeloop's 23 frames,
#: posted 2026-11-23): reported so a buyer can check the meter.
ATTEST_FRAME_COUNT = 23
#: The most challenge text one /attest call accepts (bytes).
ATTEST_TEXT_LIMIT = 4000
#: The most request body the server will read, in bytes (8 KiB): room for
#: the 4000-character text, the operation and the envelope, and a hard stop
#: for a stranger who sends more.
MAX_BODY = 8192
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


#: resource path -> its key in evals/prices.toml
_PRICE_KEYS = {"/stats": "stats_endpoint_usdc", "/attest": "attest_grade_usdc"}


def price_raw(resource: str) -> int:
    data = sales.prices_raw()
    key = _PRICE_KEYS[resource]
    try:
        micro = caps.dollars_to_micro(data["prices"][key], key)
    except (KeyError, TypeError, caps.CapsError) as exc:
        raise hand.HandError(f"evals/prices.toml: {exc}") from None
    if micro <= 0:
        raise hand.HandError(f"evals/prices.toml: {key} is above zero")
    return micro  # USDC has 6 decimals: micro-dollars are atomic units


def _network(cluster: str) -> str:
    return "solana" if cluster == "mainnet" else "solana-devnet"


_DESCRIPTIONS = {
    "/stats": "Teeny's latest public stats snapshot",
    "/attest": "One challenge answer graded against the challenge text (attest corpus, 23 frames)",
}


def requirements(resource: str) -> Json:
    cluster = reads._cluster()
    req: Json = {
        "scheme": "exact",
        "network": _network(cluster),
        "maxAmountRequired": str(price_raw(resource)),
        "resource": f"https://x402.{_domain()}{resource}",
        "description": _DESCRIPTIONS[resource],
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


def _grade_json(report: Mapping[str, Any]) -> Json:
    """The report as JSON-safe output: evidence lists joined, answer kept."""
    out = dict(report)
    out["attested_operations"] = {op: hits for op, hits in report["attested_operations"].items()}
    return out


def grade_request(body: Any) -> Json:
    """One /attest grading. The buyer's payload is data, never an
    instruction: only three string fields are read, nothing is executed,
    and the corpus is not for sale here (grading only)."""
    if not isinstance(body, dict):
        raise hand.Refused("the body is not a JSON object", rule="payload")
    for key in ("text", "operation"):
        value = body.get(key)
        if not isinstance(value, str) or not value.strip():
            raise hand.Refused(f"the body needs a non-empty string '{key}'", rule="payload")
        if len(value) > ATTEST_TEXT_LIMIT:
            raise hand.Refused(f"'{key}' is over {ATTEST_TEXT_LIMIT} characters", rule="payload")
    from . import attest as grader

    report = grader.grade(body["text"], body["operation"], body.get("answer"))
    return {"corpus_frames": ATTEST_FRAME_COUNT, **_grade_json(report)}


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


def _paid_on_chain(signature: str, req: Mapping[str, Any]) -> bool:
    """The settled transaction, read back from the chain, gives Teeny's
    address at least the price in USDC. A chain that cannot be read leaves
    it to the facilitator, which checked the same requirements; one that
    reads and shows less is a refusal."""
    from . import solana

    cluster = reads._cluster()
    try:
        tx = solana.rpc(cluster, "getTransaction", [signature, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0,
                                                                "commitment": "confirmed"}])
    except hand.HandError:
        return True
    if not isinstance(tx, Mapping):
        return True
    legs = accounts.read_deposit(tx, req["payTo"], req["asset"])
    return any(leg["asset"] == "USDC" and leg["amount_raw"] >= int(req["maxAmountRequired"]) for leg in legs)


def _seen(signature: str) -> bool:
    conn = accounts.db()
    try:
        conn.executescript(SCHEMA)
        return conn.execute("SELECT 1 FROM x402_sales WHERE transaction_sig = ?", (signature,)).fetchone() is not None
    finally:
        conn.close()


def handle(method: str, path: str, headers: Mapping[str, str], request_body: Any = None) -> tuple[int, dict[str, str], Json]:
    """One request: (status, extra headers, JSON body)."""
    path, _, query = path.partition("?")
    if path == "/health":
        return 200, {}, {"ok": True}
    if not hand.flag("X402_ENDPOINT", False) or path not in RESOURCES:
        return 404, {}, {"error": "not found"}
    if path == "/attest":
        if method != "POST":
            return 405, {}, {"error": "POST only"}
    elif method not in ("GET", "HEAD"):
        return 405, {}, {"error": "GET only"}
    req = requirements(path)
    header = next((v for k, v in headers.items() if k.lower() == "x-payment"), None)
    if not header:
        if path == "/attest":
            # The body's shape is checked for free: nobody pays for a call
            # that could never grade. The verdict itself is never free.
            try:
                grade_request(request_body)
            except hand.Refused as refused:
                return 402, {}, {"x402Version": X402_VERSION, "error": refused.reason, "accepts": [req]}
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
    if not _paid_on_chain(signature, req):
        return refuse("the settled transaction does not pay this price to this address")
    # The money has settled: it goes in the ledger first, whatever the
    # grading then does. A grading that fails leaves a refundable 422 and
    # the sale row a person refunds from.
    _record(resource=path, settled=settled, req=req)
    try:
        payload = grade_request(request_body) if path == "/attest" else stats()
    except hand.Refused as refused:
        return 422, {}, {"error": refused.reason, "payment": signature}
    except Exception:  # noqa: BLE001 - paid, grading broke: refundable, never a silent 200
        return 422, {}, {"error": "grading failed", "payment": signature}
    receipt = base64.b64encode(json.dumps({"success": True, "transaction": signature, "network": req["network"],
                                           "payer": settled.get("payer")}).encode()).decode()
    return 200, {"X-PAYMENT-RESPONSE": receipt}, payload


class BodyRefused(Exception):
    """A request body the server will not read: (status, why)."""

    def __init__(self, status: int, reason: str) -> None:
        super().__init__(reason)
        self.status = status
        self.reason = reason


def read_body(headers: Mapping[str, str], rfile: Any) -> bytes:
    """The request body, read only after its declared length is checked.

    A missing Content-Length is 411, one that is not plain ASCII digits is
    400, one over MAX_BODY is 413: all decided from the header alone, before
    a single body byte is read. Never reads more than MAX_BODY."""
    value = next((v for k, v in headers.items() if k.lower() == "content-length"), None)
    if value is None or not value.strip():
        raise BodyRefused(411, "Content-Length is required")
    value = value.strip()
    if not (value.isascii() and value.isdigit()):
        raise BodyRefused(400, "Content-Length is not a number")
    length = int(value)
    if length > MAX_BODY:
        raise BodyRefused(413, f"the body is over {MAX_BODY} bytes")
    return rfile.read(length) if length else b""


def serve(host: str, port: int) -> None:
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args: Any) -> None:
            pass

        #: a buyer that stalls mid-request does not hold a thread forever
        timeout = TIMEOUT

        def _answer(self) -> None:
            try:
                parsed = None
                path = self.path.partition("?")[0]
                if self.command == "POST" and path == "/attest" and hand.flag("X402_ENDPOINT", False):
                    try:
                        raw = read_body(dict(self.headers), self.rfile)
                    except BodyRefused as refused:
                        # the body was not read, so the connection cannot be reused
                        self.close_connection = True
                        self._send(refused.status, {"Connection": "close"}, {"error": refused.reason})
                        return
                    try:
                        parsed = json.loads(raw) if raw else None
                    except ValueError:
                        parsed = None
                status, extra, body = handle(self.command, self.path, dict(self.headers), parsed)
            except Exception:  # noqa: BLE001 - a buyer gets a status, never a trace
                status, extra, body = 500, {}, {"error": "internal"}
            self._send(status, extra, body)

        def _send(self, status: int, extra: Mapping[str, str], body: Json) -> None:
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
