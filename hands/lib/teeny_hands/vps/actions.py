"""What hands/vps does. Buying is capped three ways, all closed by default
so the operator opens each one: `VPS_ORDERS` (whether it may buy at all),
`VPS_MAX_MONTHLY_USD` (a plan's monthly price) and `VPS_MAX_SERVERS` (orders
that still hold a slot). Vultr bills by the hour against the account's
credit, so a server costs money until it is destroyed, and only the
operator destroys one.

An order is reserved in `vps_orders` before Vultr is called, under a lock,
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
CREATE TABLE IF NOT EXISTS vps_orders (
    id INTEGER PRIMARY KEY,
    at REAL NOT NULL,
    plan TEXT NOT NULL,
    region TEXT NOT NULL,
    monthly_usd REAL NOT NULL,
    os_id INTEGER NOT NULL,
    os TEXT NOT NULL,
    reason TEXT NOT NULL,
    status TEXT NOT NULL,
    instance_id TEXT,
    main_ip TEXT,
    note TEXT,
    updated_at REAL NOT NULL
);
"""

#: Orders that hold a slot under VPS_MAX_SERVERS.
HOLDING = ("ordering", "ordered", "in_doubt")
_PLAN = re.compile(r"^[a-z0-9][a-z0-9-]{0,60}$")
_REGION = re.compile(r"^[a-z]{3}$")
_INSTANCE = re.compile(r"^[0-9a-f-]{8,64}$")
_PUBKEY = re.compile(r"^(ssh-ed25519|ecdsa-sha2-nistp(256|384|521)|ssh-rsa) [A-Za-z0-9+/=]{40,}( [^\s]{1,100})?$")
_DEFAULT_OS = "Ubuntu 24.04 LTS x64"
_DEFAULT_REGION = "bom"
_LABEL = "teeny"
_SHOWN = ("id", "label", "status", "power_status", "server_status", "main_ip", "v6_main_ip", "region", "plan", "os",
          "ram", "disk", "vcpu_count", "date_created", "tags")  # fmt: skip


def _db():
    return hand.open_db(SCHEMA)


def _reason(value: str | None) -> str:
    text = (value or "").strip()
    if not 3 <= len(text) <= 300:
        raise hand.HandError("--reason is 3-300 characters: why Teeny needs this")
    return text


def _region(value: str | None) -> str:
    region = (value or hand.setting("VPS_REGION", _DEFAULT_REGION) or _DEFAULT_REGION).strip()
    if not _REGION.match(region):
        raise hand.HandError("a region is Vultr's three-letter id (bom, fra, ewr, ...)")
    return region


def caps() -> Json:
    return {
        "orders": hand.flag("VPS_ORDERS", False),
        "max_monthly_usd": hand.number("VPS_MAX_MONTHLY_USD", 0, 0, 100),
        "max_servers": hand.number("VPS_MAX_SERVERS", 1, 0, 3),
        "region": _region(None),
    }


# --- reads ----------------------------------------------------------------------------------


def _catalog(api: RestApi) -> list[Mapping[str, Any]]:
    answer = api.call("GET", "/plans", query=[("per_page", "500")], auth=False)
    plans = answer.get("plans") if isinstance(answer, Mapping) else None
    if not isinstance(plans, list):
        raise hand.HandError("Vultr sent no plan list")
    return [p for p in plans if isinstance(p, Mapping) and isinstance(p.get("id"), str)]


def _monthly(plan: Mapping[str, Any]) -> float | None:
    cost = plan.get("monthly_cost")
    if isinstance(cost, (int, float)) and not isinstance(cost, bool) and cost > 0:
        return float(cost)
    return None


def _shape(plan: Mapping[str, Any]) -> Json:
    return {
        "plan": plan["id"],
        "type": plan.get("type"),
        "monthly_usd": _monthly(plan),
        "vcpu": plan.get("vcpu_count"),
        "memory_mb": plan.get("ram"),
        "disk_gb": plan.get("disk"),
        "bandwidth_gb": plan.get("bandwidth"),
    }


def plans(max_usd: float | None = None, region: str | None = None, api: RestApi | None = None) -> Json:
    """The plans sold in `region`, cheapest first; `max_usd` keeps those at or
    under it. Free and promotional plans (no monthly price) are left out."""
    where = _region(region)
    api = api or client.api(auth=False)
    found = [
        _shape(p) for p in _catalog(api)
        if where in (p.get("locations") or []) and _monthly(p) is not None
        and (max_usd is None or _monthly(p) <= max_usd)
    ]  # fmt: skip
    found.sort(key=lambda p: (p["monthly_usd"], p["plan"]))
    return {"region": where, "plans": found, "caps": caps()}


def account(api: RestApi | None = None) -> Json:
    """The credit and what is owed so far this month (Vultr's own numbers;
    a negative balance is credit)."""
    api = api or client.api()
    answer = api.call("GET", "/account")
    acct = answer.get("account") if isinstance(answer, Mapping) else None
    if not isinstance(acct, Mapping):
        raise hand.HandError("Vultr sent no account")
    return {"balance": acct.get("balance"), "pending_charges": acct.get("pending_charges"),
            "last_payment_date": acct.get("last_payment_date")}  # fmt: skip


def servers() -> Json:
    conn = _db()
    try:
        rows = conn.execute("SELECT * FROM vps_orders ORDER BY id DESC").fetchall()
    finally:
        conn.close()
    return {"servers": [dict(r) for r in rows], "caps": caps()}


def _store_secrets(instance_id: str, taken: Mapping[str, str]) -> str:
    """What Vultr showed once (the root password), kept for the operator in
    secrets/vps/<instance>.json, mode 600. Returns the path, relative."""
    folder = hand.secrets_dir() / "vps"
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = folder / f"{instance_id}.json"
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
    return f"secrets/vps/{instance_id}.json"


def _instance_id(value: str) -> str:
    if not _INSTANCE.match(value or ""):
        raise hand.HandError("an instance id is Vultr's UUID")
    return value


def server(instance_id: str, api: RestApi | None = None) -> Json:
    """One server's status and address. A password in the answer is
    stored, never printed."""
    instance_id = _instance_id(instance_id)
    api = api or client.api()
    answer = api.call("GET", f"/instances/{quote(instance_id)}")
    instance = answer.get("instance") if isinstance(answer, Mapping) else None
    if not isinstance(instance, Mapping):
        raise hand.HandError("Vultr sent no instance")
    shown, taken = client.scrub(instance)
    out: Json = {"server": {k: shown[k] for k in _SHOWN if k in shown}}
    if taken:
        out["stored_secrets"] = {"file": _store_secrets(instance_id, taken), "keys": sorted(taken)}
    conn = _db()
    try:
        conn.execute(
            "UPDATE vps_orders SET main_ip = COALESCE(NULLIF(?, '0.0.0.0'), main_ip), note = ?, updated_at = ? WHERE instance_id = ?",
            (shown.get("main_ip"), f"vultr {shown.get('status')}/{shown.get('server_status')}"[:80], hand.now(), instance_id),
        )
    finally:
        conn.close()
    return out


# --- acting ----------------------------------------------------------------------------------


def _ssh_key(api: RestApi, pubkey: str) -> str:
    """The id of Teeny's key on the account, adding it once."""
    body = " ".join(pubkey.split()[:2])
    answer = api.call("GET", "/ssh-keys", query=[("per_page", "500")])
    for key in (answer.get("ssh_keys") or []) if isinstance(answer, Mapping) else []:
        if isinstance(key, Mapping) and " ".join(str(key.get("ssh_key", "")).split()[:2]) == body and key.get("id"):
            return str(key["id"])
    made = api.call("POST", "/ssh-keys", body={"name": _LABEL, "ssh_key": pubkey})
    key = made.get("ssh_key") if isinstance(made, Mapping) else None
    if not isinstance(key, Mapping) or not key.get("id"):
        raise hand.HandError("Vultr did not add the SSH key")
    return str(key["id"])


def _os_id(api: RestApi, wanted: str) -> int:
    answer = api.call("GET", "/os", query=[("per_page", "500")], auth=False)
    for option in (answer.get("os") or []) if isinstance(answer, Mapping) else []:
        if isinstance(option, Mapping) and option.get("name") == wanted and isinstance(option.get("id"), int):
            return option["id"]
    raise hand.Refused(f"Vultr has no system named {wanted!r}", rule="os")


def order(plan_id: str, reason: str, region: str | None = None, os_name: str | None = None, api: RestApi | None = None) -> Json:
    """Buys one server, under every cap."""
    reason = _reason(reason)
    where = _region(region)
    if not _PLAN.match(plan_id or ""):
        raise hand.HandError("--plan is a Vultr plan id (see `plans`)")
    if not hand.flag("VPS_ORDERS", False):
        raise hand.Refused("VPS_ORDERS is off: the operator has not opened ordering", rule="orders_off")
    pubkey = (hand.setting("VPS_SSH_PUBKEY") or "").strip()
    if not _PUBKEY.match(pubkey):
        raise hand.HandError("VPS_SSH_PUBKEY in secrets/teeny.env must be one OpenSSH public key line")
    ceiling = hand.number("VPS_MAX_MONTHLY_USD", 0, 0, 100)
    most = hand.number("VPS_MAX_SERVERS", 1, 0, 3)
    api = api or client.api()
    found = next((p for p in _catalog(api) if p["id"] == plan_id), None)
    if found is None:
        raise hand.Refused(f"Vultr has no plan {plan_id!r}", rule="unknown")
    if where not in (found.get("locations") or []):
        raise hand.Refused(f"{plan_id} is not sold in {where}", rule="region")
    monthly = _monthly(found)
    if monthly is None:
        raise hand.Refused(f"{plan_id} has no monthly price", rule="price")
    if monthly > ceiling:
        raise hand.Refused(
            f"{plan_id} is ${monthly:g}/month, over VPS_MAX_MONTHLY_USD (${ceiling})",
            rule="price_cap", monthly_usd=monthly, cap_usd=ceiling,
        )  # fmt: skip
    os_label = os_name or hand.setting("VPS_OS", _DEFAULT_OS) or _DEFAULT_OS
    os_id = _os_id(api, os_label)
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
                    return {"dry_run": True, "would_order": plan_id, "region": where, "monthly_usd": monthly,
                            "os": os_label, "reason": reason}  # fmt: skip
                at = hand.now()
                row = conn.execute(
                    "INSERT INTO vps_orders (at, plan, region, monthly_usd, os_id, os, reason, status, updated_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, 'ordering', ?)",
                    (at, plan_id, where, monthly, os_id, os_label, reason, at),
                ).lastrowid
            try:
                key_id = _ssh_key(api, pubkey)
                answer = api.call("POST", "/instances", body={
                    "region": where, "plan": plan_id, "os_id": os_id, "label": _LABEL, "hostname": _LABEL,
                    "sshkey_id": [key_id], "backups": "disabled", "enable_ipv6": True, "tags": [_LABEL],
                })  # fmt: skip
            except RestError as exc:
                status = "in_doubt" if exc.status is None or exc.status >= 500 else "failed"
                conn.execute("UPDATE vps_orders SET status = ?, note = ?, updated_at = ? WHERE id = ?", (status, str(exc)[:300], hand.now(), row))
                if status == "in_doubt":
                    raise hand.HandError(f"{exc}; order {row} is in_doubt: a person checks Vultr before anything else") from None
                raise
            instance = answer.get("instance") if isinstance(answer, Mapping) else None
            if not isinstance(instance, Mapping) or not instance.get("id"):
                conn.execute("UPDATE vps_orders SET status = 'in_doubt', note = 'unreadable answer', updated_at = ? WHERE id = ?", (hand.now(), row))
                raise hand.HandError(f"Vultr answered the order unreadably; order {row} is in_doubt")
            iid = str(instance["id"])
            shown, taken = client.scrub(instance)
            conn.execute(
                "UPDATE vps_orders SET status = 'ordered', instance_id = ?, updated_at = ? WHERE id = ?",
                (iid, hand.now(), row),
            )
        finally:
            conn.close()
    out: Json = {"ordered": plan_id, "order": row, "instance_id": iid, "region": where, "monthly_usd": monthly,
                 "status": shown.get("status"), "bill": _bill(iid, monthly, plan_id),
                 "next": f"hands/vps server {iid} until it is active and has its address"}  # fmt: skip
    if taken:
        out["stored_secrets"] = {"file": _store_secrets(iid, taken), "keys": sorted(taken)}
    return out


def _bill_name(instance_id: str) -> str:
    return f"vultr {instance_id[:8]}"


def _bill(instance_id: str, monthly: float, plan: str) -> Json | None:
    """The server as a monthly bill, so `hands/wallet costs` counts it."""
    from ..wallet import reads as wallet_reads

    try:
        return wallet_reads.bills_add(_bill_name(instance_id), f"{monthly:.2f}", "month", f"Vultr {plan}")["bill"]
    except Exception:  # noqa: BLE001 - a bill never undoes an order
        return None


def release(order_id: int, note: str) -> Json:
    """The operator: an order no longer holds a slot (an in_doubt a person
    has read, a server destroyed outside this hand)."""
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


def destroy(instance_id: str, note: str, api: RestApi | None = None) -> Json:
    """The operator: destroys one of this hand's servers (it stops billing),
    releases its order and drops its bill. Never a server it did not buy."""
    instance_id = _instance_id(instance_id)
    note = _reason(note)
    conn = _db()
    try:
        row = conn.execute("SELECT id FROM vps_orders WHERE instance_id = ? AND status = 'ordered'", (instance_id,)).fetchone()
        if row is None:
            raise hand.Refused(f"{instance_id} is not a server this hand holds", rule="unknown")
        if hand.dry_run():
            return {"dry_run": True, "would_destroy": instance_id}
        (api or client.api()).call("DELETE", f"/instances/{quote(instance_id)}")
        conn.execute("UPDATE vps_orders SET status = 'released', note = ?, updated_at = ? WHERE id = ?", (f"destroyed: {note}", hand.now(), row["id"]))
    finally:
        conn.close()
    from ..wallet import reads as wallet_reads

    try:
        wallet_reads.bills_remove(_bill_name(instance_id))
    except (hand.HandError, hand.Refused):
        pass
    return {"destroyed": instance_id, "order": row["id"]}
