"""The two providers hands/vps buys from, behind one shape.

Each provider answers its plans as the same rows (`Plan`), creates a server
and reads one back. What differs stays here: the base URL and key setting,
how an order is paid (Vultr bills the account by the hour; EQVPS debits a
prepaid balance), and the paths.

  eqvps  https://api.eqvps.com/api/v1/eqvps   EQVPS_API_TOKEN  prepaid, crypto checkout
  vultr  https://api.vultr.com/v2             VULTR_API_KEY    hourly against credit
"""

from __future__ import annotations

import urllib.parse
from dataclasses import dataclass, field
from typing import Any, Mapping

from .. import hand
from ..rest import RestApi, quote

PROVIDERS = ("vultr", "eqvps")
_LOCAL = ("127.0.0.1", "localhost", "::1")

#: Keys whose values never leave this hand: a root password shown once, a
#: fresh token, anything that unlocks a server or the account.
SECRET_KEYS = frozenset({"default_password", "password", "root_password", "api_key", "token", "access_token", "private_key"})


@dataclass(frozen=True)
class Plan:
    provider: str
    plan: str
    monthly_usd: float
    vcpu: Any
    memory_mb: Any
    disk_gb: Any
    public_ipv4: bool
    regions: tuple[str, ...]
    #: system name -> the provider's id for it
    systems: Mapping[str, int] = field(default_factory=dict)

    def shown(self) -> dict[str, Any]:
        return {"provider": self.provider, "plan": self.plan, "monthly_usd": self.monthly_usd, "vcpu": self.vcpu,
                "memory_mb": self.memory_mb, "disk_gb": self.disk_gb, "public_ipv4": self.public_ipv4}  # fmt: skip


def provider(value: str | None) -> str:
    name = (value or hand.setting("VPS_PROVIDER", "vultr") or "vultr").strip().lower()
    if name not in PROVIDERS:
        raise hand.HandError(f"--provider is one of {', '.join(PROVIDERS)}")
    return name


def _base(setting: str, default: str) -> str:
    value = hand.setting(setting, default) or default
    parts = urllib.parse.urlsplit(value)
    if parts.scheme != "https" and (parts.hostname or "") not in _LOCAL:
        raise hand.HandError(f"{setting} must be https")
    return value


def api(name: str, auth: bool = True) -> RestApi:
    if name == "vultr":
        return RestApi("Vultr", _base("VULTR_API_BASE", "https://api.vultr.com/v2"),
                       hand.require("VULTR_API_KEY") if auth else None, "teeny-hands-vps")  # fmt: skip
    return RestApi("EQVPS", _base("EQVPS_API_BASE", "https://api.eqvps.com/api/v1/eqvps"),
                   hand.require("EQVPS_API_TOKEN") if auth else None, "teeny-hands-vps")  # fmt: skip


def _unwrap(answer: Any) -> Any:
    """EQVPS answers some calls as {"data": ...} and some bare."""
    if isinstance(answer, Mapping) and "data" in answer and len(answer) <= 3:
        return answer["data"]
    return answer


def _cost(value: Any) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
        return float(value)
    return None


# --- plans ------------------------------------------------------------------------------------


def plans(name: str, api_: RestApi) -> list[Plan]:
    if name == "vultr":
        answer = api_.call("GET", "/plans", query=[("per_page", "500")], auth=False)
        systems_answer = api_.call("GET", "/os", query=[("per_page", "500")], auth=False)
        systems = {o["name"]: o["id"] for o in (systems_answer.get("os") or []) if isinstance(o, Mapping)
                   and isinstance(o.get("name"), str) and isinstance(o.get("id"), int)}  # fmt: skip
        rows = answer.get("plans") if isinstance(answer, Mapping) else None
        if not isinstance(rows, list):
            raise hand.HandError("Vultr sent no plan list")
        out = []
        for p in rows:
            if not isinstance(p, Mapping) or not isinstance(p.get("id"), str) or _cost(p.get("monthly_cost")) is None:
                continue
            out.append(Plan("vultr", p["id"], _cost(p["monthly_cost"]), p.get("vcpu_count"), p.get("ram"), p.get("disk"),
                            True, tuple(p.get("locations") or ()), systems))  # fmt: skip
        return out
    rows = _unwrap(api_.call("GET", "/products", auth=False))
    if not isinstance(rows, list):
        raise hand.HandError("EQVPS sent no product list")
    out = []
    for p in rows:
        if not isinstance(p, Mapping) or not isinstance(p.get("slug"), str):
            continue
        price = p.get("price") if isinstance(p.get("price"), Mapping) else {}
        if price.get("period") != "month" or price.get("currency", "USD") != "USD" or _cost(price.get("amount")) is None:
            continue
        specs = p.get("specs") if isinstance(p.get("specs"), Mapping) else {}
        systems = {o["name"]: o["id"] for o in (p.get("available_os") or []) if isinstance(o, Mapping)
                   and isinstance(o.get("name"), str) and isinstance(o.get("id"), int)}  # fmt: skip
        # EQVPS places a server itself (Germany or Finland); there is no region to pick.
        out.append(Plan("eqvps", p["slug"], _cost(price["amount"]), specs.get("vcpu"), specs.get("memory_mb"),
                        specs.get("disk_gb"), bool(specs.get("ipv4")), ("",), systems))  # fmt: skip
    return out


# --- servers ------------------------------------------------------------------------------------


def create(name: str, api_: RestApi, plan: Plan, region: str, os_id: int, pubkey: str, label: str) -> dict[str, Any]:
    """One order. Answers {"id": ..., "raw": <the provider's answer>} or, for
    an unfunded EQVPS order, {"invoice": ..., "raw": ...}."""
    if name == "vultr":
        key_id = _vultr_key(api_, pubkey, label)
        answer = api_.call("POST", "/instances", body={
            "region": region, "plan": plan.plan, "os_id": os_id, "label": label, "hostname": label,
            "sshkey_id": [key_id], "backups": "disabled", "enable_ipv6": True, "tags": [label],
        })  # fmt: skip
        instance = answer.get("instance") if isinstance(answer, Mapping) else None
        if isinstance(instance, Mapping) and instance.get("id"):
            return {"id": str(instance["id"]), "raw": instance}
        return {"raw": answer}
    answer = _unwrap(api_.call("POST", "/orders", body={"product": plan.plan, "os_id": os_id, "ssh_key": pubkey}))
    answer = answer if isinstance(answer, Mapping) else {}
    if answer.get("service_id") is not None:
        return {"id": str(answer["service_id"]), "raw": answer}
    invoice = answer.get("invoice") if isinstance(answer.get("invoice"), Mapping) else None
    if invoice is not None and invoice.get("id") is not None:
        return {"invoice": str(invoice["id"]), "raw": answer}
    return {"raw": answer}


def _vultr_key(api_: RestApi, pubkey: str, label: str) -> str:
    """The id of Teeny's key on the Vultr account, adding it once."""
    body = " ".join(pubkey.split()[:2])
    answer = api_.call("GET", "/ssh-keys", query=[("per_page", "500")])
    for key in (answer.get("ssh_keys") or []) if isinstance(answer, Mapping) else []:
        if isinstance(key, Mapping) and " ".join(str(key.get("ssh_key", "")).split()[:2]) == body and key.get("id"):
            return str(key["id"])
    made = api_.call("POST", "/ssh-keys", body={"name": label, "ssh_key": pubkey})
    key = made.get("ssh_key") if isinstance(made, Mapping) else None
    if not isinstance(key, Mapping) or not key.get("id"):
        raise hand.HandError("Vultr did not add the SSH key")
    return str(key["id"])


def get(name: str, api_: RestApi, server_id: str) -> Mapping[str, Any]:
    if name == "vultr":
        answer = api_.call("GET", f"/instances/{quote(server_id)}")
        found = answer.get("instance") if isinstance(answer, Mapping) else None
    else:
        found = _unwrap(api_.call("GET", f"/services/{quote(server_id)}"))
    if not isinstance(found, Mapping):
        raise hand.HandError(f"{api_.name} sent no server")
    return found


def address(name: str, server: Mapping[str, Any]) -> str | None:
    """Where to SSH to, as the provider says it."""
    if name == "vultr":
        ip = server.get("main_ip")
        return ip if isinstance(ip, str) and ip and ip != "0.0.0.0" else None
    access = server.get("access") if isinstance(server.get("access"), Mapping) else {}
    host, port = access.get("host"), access.get("port")
    return f"{host}:{port}" if isinstance(host, str) and host else None


def destroy(name: str, api_: RestApi, server_id: str) -> None:
    if name != "vultr":
        raise hand.Refused("EQVPS has no documented cancel over its API: cancel it in its console, then `release`",
                           rule="no_cancel")  # fmt: skip
    api_.call("DELETE", f"/instances/{quote(server_id)}")


def scrub(value: Any) -> tuple[Any, dict[str, str]]:
    """`value` with every secret key taken out, and what was taken (by dotted
    path), for the caller to store rather than print."""
    taken: dict[str, str] = {}

    def walk(node: Any, path: str) -> Any:
        if isinstance(node, Mapping):
            out = {}
            for key, item in node.items():
                where = f"{path}.{key}" if path else str(key)
                if str(key).lower() in SECRET_KEYS and isinstance(item, str) and item:
                    taken[where] = item
                    continue
                out[key] = walk(item, where)
            return out
        if isinstance(node, list):
            return [walk(item, f"{path}[{i}]") for i, item in enumerate(node)]
        return node

    return walk(value, ""), taken
