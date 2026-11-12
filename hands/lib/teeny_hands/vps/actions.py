"""What hands/vps does. Spending is capped twice: a top-up by
`VPS_TOPUP_MAX_USD` (one top-up, and every top-up in 30 days together), an
order by `VPS_ORDERS`, `VPS_MAX_MONTHLY_USD` (the plan's monthly price) and
`VPS_MAX_SERVERS` (orders not yet released). All of them default to nothing
allowed, so the operator opens each one.

The order is reserved in `vps_orders` before EQVPS is called, under a lock,
so two runs cannot both pass the server cap. A call that never got an answer
is `in_doubt` and still counts: it may have bought a server. A person reads
it and releases it (`release`); the hand never retries an order.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Mapping

from .. import hand
from ..rest import RestApi, RestError, quote
from . import client

Json = dict[str, Any]

SCHEMA = """
CREATE TABLE IF NOT EXISTS vps_topups (
    id INTEGER PRIMARY KEY,
    at REAL NOT NULL,
    usd INTEGER NOT NULL,
    reason TEXT NOT NULL,
    checkout_url TEXT
);
CREATE TABLE IF NOT EXISTS vps_orders (
    id INTEGER PRIMARY KEY,
    at REAL NOT NULL,
    product TEXT NOT NULL,
    monthly_usd REAL NOT NULL,
    os_id INTEGER NOT NULL,
    os TEXT NOT NULL,
    reason TEXT NOT NULL,
    status TEXT NOT NULL,
    service_id TEXT,
    invoice_id TEXT,
    note TEXT,
    updated_at REAL NOT NULL
);
"""

#: Orders that hold a slot under VPS_MAX_SERVERS.
HOLDING = ("ordering", "ordered", "unfunded", "in_doubt")
MONTH = 30 * 86400.0
_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,40}$")
_SERVICE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_PUBKEY = re.compile(r"^(ssh-ed25519|ecdsa-sha2-nistp(256|384|521)|ssh-rsa) [A-Za-z0-9+/=]{40,}( [^\s]{1,100})?$")
_DEFAULT_OS = "Ubuntu 24.04"


def _db():
    return hand.open_db(SCHEMA)


def _reason(value: str | None) -> str:
    text = (value or "").strip()
    if not 3 <= len(text) <= 300:
        raise hand.HandError("--reason is 3-300 characters: why Teeny needs this")
    return text


# --- reads ----------------------------------------------------------------------------------


def _catalog(api: RestApi | None = None) -> list[Mapping[str, Any]]:
    api = api or client.api(auth=False)
    data = client.unwrap(api.call("GET", "/products", auth=False))
    if not isinstance(data, list):
        raise hand.HandError("EQVPS sent no product list")
    return [p for p in data if isinstance(p, Mapping) and isinstance(p.get("slug"), str)]


def _monthly(product: Mapping[str, Any]) -> float | None:
    price = product.get("price")
    if isinstance(price, Mapping) and price.get("period") == "month" and price.get("currency", "USD") == "USD":
        amount = price.get("amount")
        if isinstance(amount, (int, float)) and not isinstance(amount, bool) and amount > 0:
            return float(amount)
    return None


def _shape(product: Mapping[str, Any]) -> Json:
    specs = product.get("specs") if isinstance(product.get("specs"), Mapping) else {}
    return {
        "slug": product["slug"],
        "name": product.get("name"),
        "monthly_usd": _monthly(product),
        "vcpu": specs.get("vcpu"),
        "memory_mb": specs.get("memory_mb"),
        "disk_gb": specs.get("disk_gb"),
        "public_ipv4": bool(specs.get("ipv4")),
        "network": specs.get("network"),
        "os": [o.get("name") for o in product.get("available_os") or [] if isinstance(o, Mapping)],
    }


def products(max_usd: float | None = None, api: RestApi | None = None) -> Json:
    """Every plan, cheapest first; `max_usd` keeps those at or under it."""
    shaped = [_shape(p) for p in _catalog(api)]
    shaped = [p for p in shaped if p["monthly_usd"] is not None and (max_usd is None or p["monthly_usd"] <= max_usd)]
    shaped.sort(key=lambda p: (p["monthly_usd"], p["slug"]))
    return {"products": shaped, "caps": caps()}


def caps() -> Json:
    return {
        "orders": hand.flag("VPS_ORDERS", False),
        "max_monthly_usd": hand.number("VPS_MAX_MONTHLY_USD", 0, 0, 100),
        "max_servers": hand.number("VPS_MAX_SERVERS", 1, 0, 3),
        "topup_max_usd": hand.number("VPS_TOPUP_MAX_USD", 0, 0, 200),
    }


def servers() -> Json:
    conn = _db()
    try:
        rows = conn.execute("SELECT * FROM vps_orders ORDER BY id DESC").fetchall()
        topups = conn.execute("SELECT id, at, usd, reason FROM vps_topups ORDER BY id DESC LIMIT 20").fetchall()
    finally:
        conn.close()
    return {"servers": [dict(r) for r in rows], "topups": [dict(t) for t in topups], "caps": caps()}


def _store_secrets(service_id: str, taken: Mapping[str, str]) -> str:
    """What EQVPS showed once (a root password), kept for the operator in
    secrets/vps/<service>.json, mode 600. Returns the path, relative."""
    folder = hand.secrets_dir() / "vps"
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = folder / f"{service_id}.json"
    old: dict[str, str] = {}
    if path.exists():
        try:
            old = json.loads(path.read_text())
        except ValueError:
            old = {}
    old.update(taken)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump(old, fh, sort_keys=True)
    os.chmod(path, 0o600)
    return f"secrets/vps/{service_id}.json"


def service(service_id: str, api: RestApi | None = None) -> Json:
    """One server's status and how to reach it. A password EQVPS shows once
    is stored, never printed."""
    if not _SERVICE.match(service_id or ""):
        raise hand.HandError("a service id is letters, digits, - and _")
    api = api or client.api()
    data = client.unwrap(api.call("GET", f"/services/{quote(service_id)}"))
    shown, taken = client.scrub(data)
    out: Json = {"service": shown}
    if taken:
        out["stored_secrets"] = {"file": _store_secrets(service_id, taken), "keys": sorted(taken)}
    conn = _db()
    try:
        status = shown.get("status") if isinstance(shown, Mapping) else None
        if isinstance(status, str):
            conn.execute(
                "UPDATE vps_orders SET note = ?, updated_at = ? WHERE service_id = ?",
                (f"eqvps status {status[:40]}", hand.now(), service_id),
            )
    finally:
        conn.close()
    return out


# --- acting ----------------------------------------------------------------------------------


def topup(usd: int, reason: str, api: RestApi | None = None) -> Json:
    """A checkout for `usd` dollars. Nothing is paid here: the checkout URL
    is for the operator (or a wallet payment under the wallet's caps)."""
    reason = _reason(reason)
    cap = hand.number("VPS_TOPUP_MAX_USD", 0, 0, 200)
    if not 1 <= usd <= 200:
        raise hand.HandError("--usd is 1..200 whole dollars")
    if usd > cap:
        raise hand.Refused(f"a top-up of ${usd} is over VPS_TOPUP_MAX_USD (${cap})", rule="topup_cap", cap_usd=cap)
    with hand.lock("vps"):
        conn = _db()
        try:
            spent = conn.execute("SELECT COALESCE(SUM(usd), 0) FROM vps_topups WHERE at > ?", (hand.now() - MONTH,)).fetchone()[0]
            if spent + usd > cap:
                raise hand.Refused(
                    f"${spent} topped up in the last 30 days; ${usd} more is over VPS_TOPUP_MAX_USD (${cap})",
                    rule="topup_month", spent_usd=spent, cap_usd=cap,
                )  # fmt: skip
            if hand.dry_run():
                return {"dry_run": True, "would_topup_usd": usd, "reason": reason}
            api = api or client.api()
            answer = client.unwrap(api.call("POST", "/balance/topup", body={"amount": usd}))
            url = answer.get("checkout_url") if isinstance(answer, Mapping) else None
            if not isinstance(url, str) or not url.startswith("https://"):
                raise hand.HandError("EQVPS sent no checkout URL")
            conn.execute(
                "INSERT INTO vps_topups (at, usd, reason, checkout_url) VALUES (?, ?, ?, ?)",
                (hand.now(), usd, reason, url),
            )
        finally:
            conn.close()
    return {
        "topup_usd": usd,
        "checkout_url": url,
        "next": "Pay it in USDC or USDT (Base, Ethereum or Tron). The browser stops at payment pages; "
        "the operator pays it, or hands/wallet under its caps.",
    }


def _pick_os(product: Mapping[str, Any], wanted: str) -> tuple[int, str]:
    for option in product.get("available_os") or []:
        if isinstance(option, Mapping) and option.get("name") == wanted and isinstance(option.get("id"), int):
            return option["id"], wanted
    raise hand.Refused(f"{product['slug']} has no {wanted!r}", rule="os", available=_shape(product)["os"])


def order(product_slug: str, reason: str, os_name: str | None = None, api: RestApi | None = None) -> Json:
    """Buys one server from the prepaid balance, under every cap."""
    reason = _reason(reason)
    if not _SLUG.match(product_slug or ""):
        raise hand.HandError("--product is a plan slug (see `products`)")
    if not hand.flag("VPS_ORDERS", False):
        raise hand.Refused("VPS_ORDERS is off: the operator has not opened ordering", rule="orders_off")
    pubkey = (hand.setting("VPS_SSH_PUBKEY") or "").strip()
    if not _PUBKEY.match(pubkey):
        raise hand.HandError("VPS_SSH_PUBKEY in secrets/teeny.env must be one OpenSSH public key line")
    ceiling = hand.number("VPS_MAX_MONTHLY_USD", 0, 0, 100)
    most = hand.number("VPS_MAX_SERVERS", 1, 0, 3)
    api = api or client.api()
    found = next((p for p in _catalog(api) if p["slug"] == product_slug), None)
    if found is None:
        raise hand.Refused(f"EQVPS has no plan {product_slug!r}", rule="unknown")
    monthly = _monthly(found)
    if monthly is None:
        raise hand.Refused(f"{product_slug} has no monthly USD price", rule="price")
    if monthly > ceiling:
        raise hand.Refused(
            f"{product_slug} is ${monthly:g}/month, over VPS_MAX_MONTHLY_USD (${ceiling})",
            rule="price_cap", monthly_usd=monthly, cap_usd=ceiling,
        )  # fmt: skip
    os_id, os_label = _pick_os(found, os_name or hand.setting("VPS_OS", _DEFAULT_OS) or _DEFAULT_OS)
    with hand.lock("vps"):
        conn = _db()
        try:
            with hand.transaction(conn):
                holding = conn.execute(
                    f"SELECT COUNT(*) FROM vps_orders WHERE status IN ({','.join('?' * len(HOLDING))})", HOLDING
                ).fetchone()[0]
                if holding >= most:
                    raise hand.Refused(
                        f"{holding} server(s) already held; VPS_MAX_SERVERS is {most}",
                        rule="server_cap", holding=holding, cap=most,
                    )  # fmt: skip
                if hand.dry_run():
                    return {"dry_run": True, "would_order": product_slug, "monthly_usd": monthly, "os": os_label, "reason": reason}
                at = hand.now()
                row = conn.execute(
                    "INSERT INTO vps_orders (at, product, monthly_usd, os_id, os, reason, status, updated_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, 'ordering', ?)",
                    (at, product_slug, monthly, os_id, os_label, reason, at),
                ).lastrowid
            try:
                answer = client.unwrap(api.call("POST", "/orders", body={"product": product_slug, "os_id": os_id, "ssh_key": pubkey}))
            except RestError as exc:
                status = "in_doubt" if exc.status is None or exc.status >= 500 else "failed"
                conn.execute("UPDATE vps_orders SET status = ?, note = ?, updated_at = ? WHERE id = ?", (status, str(exc)[:300], hand.now(), row))
                if status == "in_doubt":
                    raise hand.HandError(f"{exc}; order {row} is in_doubt: a person checks EQVPS before anything else") from None
                raise
            answer = answer if isinstance(answer, Mapping) else {}
            sid = answer.get("service_id")
            invoice = answer.get("invoice") if isinstance(answer.get("invoice"), Mapping) else None
            if sid is not None:
                conn.execute(
                    "UPDATE vps_orders SET status = 'ordered', service_id = ?, updated_at = ? WHERE id = ?",
                    (str(sid), hand.now(), row),
                )
                bill = _bill(str(sid), monthly, product_slug)
                return {"ordered": product_slug, "order": row, "service_id": str(sid), "monthly_usd": monthly, "bill": bill,
                        "next": f"hands/vps service {sid} until it is active"}  # fmt: skip
            if invoice is not None and invoice.get("id") is not None:
                conn.execute(
                    "UPDATE vps_orders SET status = 'unfunded', invoice_id = ?, updated_at = ? WHERE id = ?",
                    (str(invoice["id"]), hand.now(), row),
                )
                return {"unfunded": product_slug, "order": row, "invoice_id": str(invoice["id"]),
                        "next": "The balance did not cover it. Top up, then the operator pays the invoice."}  # fmt: skip
            conn.execute("UPDATE vps_orders SET status = 'in_doubt', note = 'unreadable answer', updated_at = ? WHERE id = ?", (hand.now(), row))
            raise hand.HandError(f"EQVPS answered the order unreadably; order {row} is in_doubt")
        finally:
            conn.close()


def _bill(service_id: str, monthly: float, product: str) -> Json | None:
    """The server as a monthly bill, so `hands/wallet costs` counts it."""
    from ..wallet import reads as wallet_reads

    try:
        return wallet_reads.bills_add(f"eqvps {service_id}", f"{monthly:.2f}", "month", f"EQVPS {product}")["bill"]
    except (hand.HandError, OSError, Exception):  # noqa: BLE001 - a bill never undoes an order
        return None


def release(order_id: int, note: str) -> Json:
    """The operator: an order no longer holds a slot (cancelled at EQVPS, a
    failed in_doubt read by a person)."""
    note = _reason(note)
    conn = _db()
    try:
        changed = conn.execute(
            "UPDATE vps_orders SET status = 'released', note = ?, updated_at = ? WHERE id = ? AND status != 'released'",
            (note, hand.now(), order_id),
        ).rowcount
    finally:
        conn.close()
    if not changed:
        raise hand.Refused(f"there is no held order {order_id}", rule="unknown")
    return {"released": order_id}


def register(first: str, last: str, email: str, api: RestApi | None = None) -> Json:
    """The operator: a new EQVPS account. Its token is printed once, for
    secrets/teeny.env; this hand never writes it anywhere."""
    for value, what in ((first, "--first"), (last, "--last")):
        if not 1 <= len((value or "").strip()) <= 40:
            raise hand.HandError(f"{what} is 1-40 characters")
    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email or ""):
        raise hand.HandError("--email must be an e-mail address")
    if hand.dry_run():
        return {"dry_run": True, "would_register": email}
    api = api or client.api(auth=False)
    got = api.call("POST", "/auth/register", body={"first_name": first.strip(), "last_name": last.strip(), "email": email}, auth=False)
    token = got.get("token") if isinstance(got, Mapping) else None
    if not isinstance(token, str) or not token:
        raise hand.HandError("EQVPS did not return a token")
    return {
        "registered": email,
        "token": token,
        "next": "Shown once and not saved. Put it in secrets/teeny.env as EQVPS_API_TOKEN=<token>",
    }
