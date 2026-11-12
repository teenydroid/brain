"""What hands/vps does, on either provider (client.py).

Buying is capped, and every cap is closed until the operator opens it in
secrets/teeny.env: `VPS_ORDERS` (whether it may buy at all),
`VPS_MAX_MONTHLY_USD` (a plan's monthly price) and `VPS_MAX_SERVERS`
(orders that still hold a slot, on both providers together). An EQVPS
top-up is capped by `VPS_TOPUP_MAX_USD`, one top-up and every top-up in 30
days together; it only makes a checkout and pays nothing.

An order is reserved in `vps_orders` before the provider is called, under a
lock, so two runs cannot both pass the server cap. A call that never got an
answer is `in_doubt` and still counts: it may have bought a server. A person
reads it and releases it (`release`); the hand never retries an order. Only
the operator destroys a server.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Mapping

from .. import hand
from ..rest import RestApi, RestError
from . import client

Json = dict[str, Any]

SCHEMA = """
CREATE TABLE IF NOT EXISTS vps_orders (
    id INTEGER PRIMARY KEY,
    at REAL NOT NULL,
    provider TEXT NOT NULL,
    plan TEXT NOT NULL,
    region TEXT NOT NULL,
    monthly_usd REAL NOT NULL,
    os_id INTEGER NOT NULL,
    os TEXT NOT NULL,
    reason TEXT NOT NULL,
    status TEXT NOT NULL,
    server_id TEXT,
    invoice_id TEXT,
    address TEXT,
    note TEXT,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS vps_topups (
    id INTEGER PRIMARY KEY,
    at REAL NOT NULL,
    provider TEXT NOT NULL,
    usd INTEGER NOT NULL,
    reason TEXT NOT NULL,
    checkout_url TEXT
);
"""

#: Orders that hold a slot under VPS_MAX_SERVERS.
HOLDING = ("ordering", "ordered", "unfunded", "in_doubt")
MONTH = 30 * 86400.0
_PLAN = re.compile(r"^[a-z0-9][a-z0-9-]{0,60}$")
_REGION = re.compile(r"^[a-z]{3}$")
_SERVER = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_PUBKEY = re.compile(r"^(ssh-ed25519|ecdsa-sha2-nistp(256|384|521)|ssh-rsa) [A-Za-z0-9+/=]{40,}( [^\s]{1,100})?$")
_DEFAULT_OS = {"vultr": "Ubuntu 24.04 LTS x64", "eqvps": "Ubuntu 24.04"}
_DEFAULT_REGION = "bom"
_LABEL = "teeny"
_VULTR_SHOWN = ("id", "label", "status", "power_status", "server_status", "main_ip", "v6_main_ip", "region", "plan",
                "os", "ram", "disk", "vcpu_count", "date_created", "tags")  # fmt: skip


def _db():
    return hand.open_db(SCHEMA)


def _reason(value: str | None) -> str:
    text = (value or "").strip()
    if not 3 <= len(text) <= 300:
        raise hand.HandError("--reason is 3-300 characters: why Teeny needs this")
    return text


def _region(name: str, value: str | None) -> str:
    if name == "eqvps":
        if value:
            raise hand.HandError("EQVPS places a server itself: --region is for Vultr")
        return ""
    region = (value or hand.setting("VPS_REGION", _DEFAULT_REGION) or _DEFAULT_REGION).strip()
    if not _REGION.match(region):
        raise hand.HandError("a region is Vultr's three-letter id (bom, fra, ewr, ...)")
    return region


def _server_id(value: str) -> str:
    if not _SERVER.match(value or ""):
        raise hand.HandError("a server id is letters, digits, - and _")
    return value


def caps() -> Json:
    return {
        "provider": client.provider(None),
        "orders": hand.flag("VPS_ORDERS", False),
        "max_monthly_usd": hand.number("VPS_MAX_MONTHLY_USD", 0, 0, 100),
        "max_servers": hand.number("VPS_MAX_SERVERS", 1, 0, 3),
        "topup_max_usd": hand.number("VPS_TOPUP_MAX_USD", 0, 0, 200),
    }


# --- reads ----------------------------------------------------------------------------------


def plans(provider: str | None, max_usd: float | None = None, region: str | None = None, api: RestApi | None = None) -> Json:
    """One provider's plans (in `region`, for Vultr), cheapest first."""
    name = client.provider(provider)
    where = _region(name, region)
    rows = client.plans(name, api or client.api(name, auth=False))
    shown = [p.shown() for p in rows if where in p.regions and (max_usd is None or p.monthly_usd <= max_usd)]
    shown.sort(key=lambda p: (p["monthly_usd"], p["plan"]))
    return {"provider": name, **({"region": where} if where else {}), "plans": shown, "caps": caps()}


def account(api: RestApi | None = None) -> Json:
    """Vultr's credit and this month's charges (a negative balance is credit)."""
    answer = (api or client.api("vultr")).call("GET", "/account")
    acct = answer.get("account") if isinstance(answer, Mapping) else None
    if not isinstance(acct, Mapping):
        raise hand.HandError("Vultr sent no account")
    return {"provider": "vultr", "balance": acct.get("balance"), "pending_charges": acct.get("pending_charges"),
            "last_payment_date": acct.get("last_payment_date")}  # fmt: skip


def servers() -> Json:
    conn = _db()
    try:
        rows = conn.execute("SELECT * FROM vps_orders ORDER BY id DESC").fetchall()
        topups = conn.execute("SELECT id, at, provider, usd, reason FROM vps_topups ORDER BY id DESC LIMIT 20").fetchall()
    finally:
        conn.close()
    return {"servers": [dict(r) for r in rows], "topups": [dict(t) for t in topups], "caps": caps()}


def _store_secrets(provider: str, server_id: str, taken: Mapping[str, str]) -> str:
    """What a provider showed once (a root password), kept for the operator
    in secrets/vps/<provider>-<server>.json, mode 600."""
    folder = hand.secrets_dir() / "vps"
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = folder / f"{provider}-{server_id}.json"
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
    return f"secrets/vps/{path.name}"


def server(provider: str | None, server_id: str, api: RestApi | None = None) -> Json:
    """One server's status and address. A password in the answer is stored,
    never printed."""
    name = client.provider(provider)
    server_id = _server_id(server_id)
    found = client.get(name, api or client.api(name), server_id)
    shown, taken = client.scrub(found)
    if name == "vultr":
        shown = {k: shown[k] for k in _VULTR_SHOWN if k in shown}
    where = client.address(name, found)
    out: Json = {"provider": name, "server": shown, "address": where}
    if taken:
        out["stored_secrets"] = {"file": _store_secrets(name, server_id, taken), "keys": sorted(taken)}
    conn = _db()
    try:
        conn.execute(
            "UPDATE vps_orders SET address = COALESCE(?, address), note = ?, updated_at = ? WHERE provider = ? AND server_id = ?",
            (where, f"{name} status {found.get('status')}"[:80], hand.now(), name, server_id),
        )
    finally:
        conn.close()
    return out


# --- acting ----------------------------------------------------------------------------------


def topup(usd: int, reason: str, api: RestApi | None = None) -> Json:
    """An EQVPS checkout for `usd` dollars. Nothing is paid here: the
    operator pays the checkout (or hands/wallet, under the wallet's caps).
    Vultr has no top-up over its API; the operator funds it in its console."""
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
            answer = client._unwrap((api or client.api("eqvps")).call("POST", "/balance/topup", body={"amount": usd}))
            url = answer.get("checkout_url") if isinstance(answer, Mapping) else None
            if not isinstance(url, str) or not url.startswith("https://"):
                raise hand.HandError("EQVPS sent no checkout URL")
            conn.execute(
                "INSERT INTO vps_topups (at, provider, usd, reason, checkout_url) VALUES (?, 'eqvps', ?, ?, ?)",
                (hand.now(), usd, reason, url),
            )
        finally:
            conn.close()
    return {
        "provider": "eqvps",
        "topup_usd": usd,
        "checkout_url": url,
        "next": "Pay it in USDC or USDT (Base, Ethereum or Tron). The browser stops at payment pages; "
        "the operator pays it, or hands/wallet under its caps.",
    }


def order(provider: str | None, plan_id: str, reason: str, region: str | None = None, os_name: str | None = None,
          api: RestApi | None = None) -> Json:  # fmt: skip
    """Buys one server, under every cap."""
    name = client.provider(provider)
    reason = _reason(reason)
    where = _region(name, region)
    if not _PLAN.match(plan_id or ""):
        raise hand.HandError("--plan is a plan id (see `plans`)")
    if not hand.flag("VPS_ORDERS", False):
        raise hand.Refused("VPS_ORDERS is off: the operator has not opened ordering", rule="orders_off")
    pubkey = (hand.setting("VPS_SSH_PUBKEY") or "").strip()
    if not _PUBKEY.match(pubkey):
        raise hand.HandError("VPS_SSH_PUBKEY in secrets/teeny.env must be one OpenSSH public key line")
    ceiling = hand.number("VPS_MAX_MONTHLY_USD", 0, 0, 100)
    most = hand.number("VPS_MAX_SERVERS", 1, 0, 3)
    api = api or client.api(name)
    plan = next((p for p in client.plans(name, api) if p.plan == plan_id), None)
    if plan is None:
        raise hand.Refused(f"{name} has no plan {plan_id!r}", rule="unknown")
    if where not in plan.regions:
        raise hand.Refused(f"{plan_id} is not sold in {where}", rule="region")
    if plan.monthly_usd > ceiling:
        raise hand.Refused(
            f"{plan_id} is ${plan.monthly_usd:g}/month, over VPS_MAX_MONTHLY_USD (${ceiling})",
            rule="price_cap", monthly_usd=plan.monthly_usd, cap_usd=ceiling,
        )  # fmt: skip
    os_label = os_name or hand.setting("VPS_OS") or _DEFAULT_OS[name]
    os_id = plan.systems.get(os_label)
    if os_id is None:
        raise hand.Refused(f"{name} {plan_id} has no system {os_label!r}", rule="os", available=sorted(plan.systems))
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
                    return {"dry_run": True, "provider": name, "would_order": plan_id, "region": where,
                            "monthly_usd": plan.monthly_usd, "os": os_label, "reason": reason}  # fmt: skip
                at = hand.now()
                row = conn.execute(
                    "INSERT INTO vps_orders (at, provider, plan, region, monthly_usd, os_id, os, reason, status, updated_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'ordering', ?)",
                    (at, name, plan_id, where, plan.monthly_usd, os_id, os_label, reason, at),
                ).lastrowid
            try:
                made = client.create(name, api, plan, where, os_id, pubkey, _LABEL)
            except RestError as exc:
                status = "in_doubt" if exc.status is None or exc.status >= 500 else "failed"
                conn.execute("UPDATE vps_orders SET status = ?, note = ?, updated_at = ? WHERE id = ?", (status, str(exc)[:300], hand.now(), row))
                if status == "in_doubt":
                    raise hand.HandError(f"{exc}; order {row} is in_doubt: a person checks {name} before anything else") from None
                raise
            if "id" in made:
                conn.execute("UPDATE vps_orders SET status = 'ordered', server_id = ?, updated_at = ? WHERE id = ?", (made["id"], hand.now(), row))
            elif "invoice" in made:
                conn.execute("UPDATE vps_orders SET status = 'unfunded', invoice_id = ?, updated_at = ? WHERE id = ?", (made["invoice"], hand.now(), row))
            else:
                conn.execute("UPDATE vps_orders SET status = 'in_doubt', note = 'unreadable answer', updated_at = ? WHERE id = ?", (hand.now(), row))
                raise hand.HandError(f"{name} answered the order unreadably; order {row} is in_doubt")
        finally:
            conn.close()
    if "invoice" in made:
        return {"provider": name, "unfunded": plan_id, "order": row, "invoice_id": made["invoice"],
                "next": "The balance did not cover it. `topup`, then the operator pays the invoice."}  # fmt: skip
    shown, taken = client.scrub(made["raw"])
    out: Json = {"provider": name, "ordered": plan_id, "order": row, "server_id": made["id"], "region": where,
                 "monthly_usd": plan.monthly_usd, "bill": _bill(name, made["id"], plan.monthly_usd, plan_id),
                 "next": f"hands/vps server --provider {name} {made['id']} until it is active and has its address"}  # fmt: skip
    if taken:
        out["stored_secrets"] = {"file": _store_secrets(name, made["id"], taken), "keys": sorted(taken)}
    return out


def _bill_name(provider: str, server_id: str) -> str:
    return f"{provider} {server_id[:12]}"


def _bill(provider: str, server_id: str, monthly: float, plan: str) -> Json | None:
    """The server as a monthly bill, so `hands/wallet costs` counts it."""
    from ..wallet import reads as wallet_reads

    try:
        return wallet_reads.bills_add(_bill_name(provider, server_id), f"{monthly:.2f}", "month", f"{provider} {plan}")["bill"]
    except Exception:  # noqa: BLE001 - a bill never undoes an order
        return None


def _drop_bill(provider: str, server_id: str) -> None:
    from ..wallet import reads as wallet_reads

    try:
        wallet_reads.bills_remove(_bill_name(provider, server_id))
    except (hand.HandError, hand.Refused):
        pass


def release(order_id: int, note: str) -> Json:
    """The operator: an order no longer holds a slot (an in_doubt a person
    has read, a server cancelled in a provider's console). Its bill goes."""
    note = _reason(note)
    conn = _db()
    try:
        row = conn.execute("SELECT provider, server_id FROM vps_orders WHERE id = ? AND status != 'released'", (order_id,)).fetchone()
        if row is None:
            raise hand.Refused(f"there is no held order {order_id}", rule="unknown")
        conn.execute("UPDATE vps_orders SET status = 'released', note = ?, updated_at = ? WHERE id = ?", (note, hand.now(), order_id))
    finally:
        conn.close()
    if row["server_id"]:
        _drop_bill(row["provider"], row["server_id"])
    return {"released": order_id}


def destroy(provider: str | None, server_id: str, note: str, api: RestApi | None = None) -> Json:
    """The operator: destroys one of this hand's servers (it stops billing),
    releases its order and drops its bill. Never a server it did not buy."""
    name = client.provider(provider)
    server_id = _server_id(server_id)
    note = _reason(note)
    conn = _db()
    try:
        row = conn.execute(
            "SELECT id FROM vps_orders WHERE provider = ? AND server_id = ? AND status = 'ordered'", (name, server_id)
        ).fetchone()
        if row is None:
            raise hand.Refused(f"{server_id} is not a {name} server this hand holds", rule="unknown")
        if hand.dry_run():
            return {"dry_run": True, "provider": name, "would_destroy": server_id}
        client.destroy(name, api or client.api(name), server_id)
        conn.execute("UPDATE vps_orders SET status = 'released', note = ?, updated_at = ? WHERE id = ?", (f"destroyed: {note}", hand.now(), row["id"]))
    finally:
        conn.close()
    _drop_bill(name, server_id)
    return {"provider": name, "destroyed": server_id, "order": row["id"]}


def register(first: str, last: str, email: str, api: RestApi | None = None) -> Json:
    """The operator: a new EQVPS account. Its token is printed once, for
    secrets/teeny.env; this hand never writes it anywhere. (A Vultr account
    and key are made by a person in Vultr's console.)"""
    for value, what in ((first, "--first"), (last, "--last")):
        if not 1 <= len((value or "").strip()) <= 40:
            raise hand.HandError(f"{what} is 1-40 characters")
    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email or ""):
        raise hand.HandError("--email must be an e-mail address")
    if hand.dry_run():
        return {"dry_run": True, "would_register": email}
    api = api or client.api("eqvps", auth=False)
    got = api.call("POST", "/auth/register", body={"first_name": first.strip(), "last_name": last.strip(), "email": email}, auth=False)
    token = got.get("token") if isinstance(got, Mapping) else None
    if not isinstance(token, str) or not token:
        raise hand.HandError("EQVPS did not return a token")
    return {
        "provider": "eqvps",
        "registered": email,
        "token": token,
        "next": "Shown once and not saved. Put it in secrets/teeny.env as EQVPS_API_TOKEN=<token>",
    }
