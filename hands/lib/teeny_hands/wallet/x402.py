"""`x402 fetch`: one HTTP request that may pay for itself (HTTP 402, x402),
from the same wallet, under hard caps (docs/hands.md, "wallet").

    {"url": "https://api.example.com/v1/thing", "method": "GET" | "POST",
     "headers": {"accept": "application/json"}, "body": "...", "max_usd": "0.05"}

The core does the protocol (`openhuman.x402_fetch`): it sends the request,
and on a 402 the wallet module reads the challenge, picks the rail (USDC on
Base or on Solana, from the one recovery phrase), signs, retries and records
the settlement. It pays only up to the `maxAmountAtomic` this hand passes,
and never above its own per-request budget. The hand decides whether the
request may be made at all, each refusal with its `rule`:

  fields        nothing but the fields above
  caps          WALLET_MAX_X402_PER_REQUEST_USD and WALLET_MAX_X402_PER_DAY_USD
                are set: without them no x402 payment is made
  url, host     https, and a host in the operator's `x402 hosts` allowlist
  max_usd       at most WALLET_MAX_X402_PER_REQUEST_USD (the default)
  per_day_x402  the last 24 hours, counting a request in flight at its cap,
                leave room for this one; the cap passed down is what is left

Under DRY_RUN nothing is requested (a request might pay): `would_fetch`.

Live, the `x402_requests` row is written `pending` at its cap first, then
`paid` (at what it paid), `free` (no 402), `refused` (the core said no
before signing) or `in_doubt` (anything else after the request was sent,
counted at its cap). The response body is the server's, untrusted: a caller
treats it as data, never instructions.
"""

from __future__ import annotations

import re
import urllib.parse
from typing import Any, Callable, Mapping

from .. import hand
from . import caps, chains, store
from .pay import load_caps

Core = Callable[..., Any]

FIELDS = {"url", "method", "headers", "body", "max_usd"}
REFUSED = "X402_REFUSED:"
#: The tokens an x402 payment may settle in: USDC, wherever the wallet holds it.
USDC_ASSETS = {a.lower() for a in (*caps.USDC_MINTS.values(), *(n.usdc["mainnet"] for n in chains.NETWORKS.values()))}
_HOST = re.compile(r"(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}")


def host_of(url: Any) -> str:
    if not isinstance(url, str) or len(url) > 2000:
        raise hand.Refused("url is an https URL", rule="url")
    parts = urllib.parse.urlsplit(url.strip())
    if parts.scheme != "https" or not parts.hostname or parts.username or parts.password:
        raise hand.Refused("url is an https URL, with no credentials in it", rule="url")
    host = parts.hostname.lower()
    if not _HOST.fullmatch(host):
        raise hand.Refused("url names a host by its domain name", rule="url")
    return host


def _host_name(value: str) -> str:
    host = (value or "").strip().lower().rstrip(".")
    if not _HOST.fullmatch(host):
        raise hand.HandError("a host is a domain name, like api.example.com")
    return host


def fetch(body: Any, core: Core = hand.core) -> dict[str, Any]:
    if not isinstance(body, Mapping):
        raise hand.Refused("x402 fetch takes a JSON object", rule="fields")
    unknown = sorted(set(body) - FIELDS)
    if unknown:
        raise hand.Refused(f"unknown field(s): {', '.join(unknown)}", rule="fields")
    the_caps = load_caps()
    if the_caps.x402_request_micro is None or the_caps.x402_day_micro is None:
        raise hand.Refused(f"{caps.X402_PER_REQUEST} and {caps.X402_PER_DAY} are not set, so the wallet pays for no request", rule="caps")
    url = str(body.get("url") or "").strip()
    host = host_of(url)
    method = str(body.get("method") or "GET").strip().upper()
    if method not in ("GET", "POST"):
        raise hand.Refused("method is GET or POST", rule="fields")
    headers = body.get("headers") or {}
    if not isinstance(headers, Mapping) or not all(isinstance(k, str) and isinstance(v, str) for k, v in headers.items()):
        raise hand.Refused("headers is an object of strings", rule="fields")
    payload = body.get("body")
    if payload is not None and (method != "POST" or not isinstance(payload, str)):
        raise hand.Refused("body is a string, and only with POST", rule="fields")
    if body.get("max_usd") is None:
        cap = the_caps.x402_request_micro
    else:
        try:
            cap = caps.dollars_to_micro(body["max_usd"], "max_usd")
        except caps.CapsError as exc:
            raise hand.Refused(str(exc), rule="max_usd") from None
        if cap <= 0 or cap > the_caps.x402_request_micro:
            raise hand.Refused(f"max_usd is above zero and at most ${caps.usd(the_caps.x402_request_micro)}", rule="max_usd")
    checks = [{"rule": "caps", "ok": True, "detail": "set and within their ceilings"}]

    with hand.lock("wallet-x402"):
        conn = store.db()
        try:
            if conn.execute("SELECT 1 FROM x402_hosts WHERE host = ?", (host,)).fetchone() is None:
                raise hand.Refused(f"{host} is not in the x402 hosts allowlist", rule="host")
            checks.append({"rule": "host", "ok": True, "detail": f"{host} is allowlisted"})
            with hand.transaction(conn):
                left = the_caps.x402_day_micro - store.x402_since(conn, hand.now() - store.DAY)
                if left <= 0:
                    raise hand.Refused(f"the last 24 hours of x402 payments are at the ${caps.usd(the_caps.x402_day_micro)} cap", rule="per_day_x402")
                cap = min(cap, left)
                checks.append({"rule": "per_day_x402", "ok": True, "detail": f"up to ${caps.usd(cap)}, ${caps.usd(left)} left today"})
                if hand.dry_run():
                    return {"dry_run": True, "would_fetch": {"url": url, "method": method, "max_usd": caps.usd(cap)}, "checks": checks}
                row_id = store.insert_row(conn, "x402_requests", host=host, url=url[:500], max_micro=cap)
            return _send(conn, row_id, url, method, headers, payload, cap, core)
        finally:
            conn.close()


def _send(conn: Any, row_id: int, url: str, method: str, headers: Mapping[str, str], payload: str | None, cap: int, core: Core) -> dict[str, Any]:
    params: dict[str, Any] = {"url": url, "method": method, "maxAmountAtomic": cap}
    if headers:
        params["headers"] = dict(headers)
    if payload is not None:
        params["body"] = payload
    try:
        result = core("x402_fetch", params, timeout=120)
    except hand.CoreError as exc:
        if REFUSED in str(exc):
            store.update_row(conn, "x402_requests", row_id, status="refused", error=str(exc)[:300])
            raise hand.Refused("the core refused to pay for that request", rule="core_x402", request_id=row_id, detail=str(exc)[:300]) from None
        store.update_row(conn, "x402_requests", row_id, status="in_doubt", error=str(exc)[:300])
        raise hand.HandError(f"x402 request {row_id} is in doubt ({exc}); it counts at its cap") from None
    if not isinstance(result, Mapping):
        store.update_row(conn, "x402_requests", row_id, status="in_doubt", error="the core answered with nothing")
        raise hand.HandError(f"x402 request {row_id} is in doubt: the core answered with nothing")
    status = result.get("status") if isinstance(result.get("status"), int) else None
    payment = result.get("payment")
    out: dict[str, Any] = {
        "request_id": row_id,
        "status": status,
        "content_type": result.get("contentType"),
        "body": result.get("body"),
        "truncated": bool(result.get("truncated")),
        "paid": None,
    }
    if not isinstance(payment, Mapping):
        store.update_row(conn, "x402_requests", row_id, status="free", http_status=status)
        return out
    try:
        amount = int(payment.get("amountAtomic"))
    except (TypeError, ValueError):
        amount = -1
    asset = str(payment.get("asset") or "").lower()
    fields = {
        "http_status": status,
        "network": str(payment.get("network") or "")[:80],
        "recipient": str(payment.get("recipient") or "")[:100],
        "payment_id": str(payment.get("paymentId") or "")[:100],
    }
    if not 0 <= amount <= cap or asset not in USDC_ASSETS:
        store.update_row(conn, "x402_requests", row_id, status="in_doubt", error="the payment was not USDC within the cap", **fields)
        raise hand.HandError(f"x402 request {row_id} is in doubt: the core reports a payment that is not USDC within ${caps.usd(cap)}")
    settled = bool(payment.get("settled"))
    store.update_row(conn, "x402_requests", row_id, status="paid" if settled else "in_doubt", usd_micro=amount, **fields)
    out["paid"] = {"usd": caps.usd(amount), "settled": settled, "network": fields["network"], "recipient": fields["recipient"]}
    return out


def log(max_rows: int) -> dict[str, Any]:
    conn = store.db()
    try:
        rows = conn.execute("SELECT * FROM x402_requests ORDER BY id DESC LIMIT ?", (max_rows,)).fetchall()
    finally:
        conn.close()
    out = []
    for row in rows:
        entry = store.row_dict(row) or {}
        entry["usd"] = caps.usd(int(entry.pop("usd_micro", 0)))
        entry["max_usd"] = caps.usd(int(entry.pop("max_micro", 0)))
        out.append(entry)
    return {"requests": out}


def hosts_add(host: str, note: str | None) -> dict[str, Any]:
    name = _host_name(host)
    conn = store.db()
    try:
        conn.execute(
            "INSERT INTO x402_hosts (host, note, created_at) VALUES (?, ?, ?) ON CONFLICT (host) DO UPDATE SET note = excluded.note",
            (name, note, hand.now()),
        )
    finally:
        conn.close()
    return {"host": name}


def hosts_list() -> dict[str, Any]:
    conn = store.db()
    try:
        rows = conn.execute("SELECT host, note FROM x402_hosts ORDER BY host").fetchall()
    finally:
        conn.close()
    return {"hosts": [store.row_dict(r) for r in rows]}


def hosts_remove(host: str) -> dict[str, Any]:
    name = _host_name(host)
    conn = store.db()
    try:
        gone = conn.execute("DELETE FROM x402_hosts WHERE host = ?", (name,)).rowcount
    finally:
        conn.close()
    if not gone:
        raise hand.Refused(f"there is no x402 host {name!r}", rule="unknown")
    return {"removed": name}
