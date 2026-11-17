"""A Python port of the /rewards page's parser (web/src/lib/drops.ts:
parseIndex, parseRound, toAtomic, checkDraw), rule for rule, so the hand's
output is checked against the contract the page enforces
(docs/drops-schema.md). Change both together."""

from __future__ import annotations

import datetime as _dt
import json
import re
from typing import Any

BASE58_KEY = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")
BASE58_SIG = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{64,90}$")
HEX32 = re.compile(r"^[0-9a-f]{64}$")
X_ID = re.compile(r"^\d{1,25}$")
HANDLE = re.compile(r"^[a-z0-9_]{1,15}$")
DECIMAL = re.compile(r"^(0|[1-9]\d{0,24})(\.\d{1,18})?$")
ATOMIC = re.compile(r"^(0|[1-9]\d{0,19})$")
POST_URL = re.compile(r"^https://(x|twitter)\.com/[A-Za-z0-9_]{1,15}/status/\d{1,25}$")
U64_MAX = (1 << 64) - 1
MAX_SAFE = 2**53 - 1
STATUSES = ("open", "drawn", "settled", "clawed_back")
FORMATS = ("drop", "milestone", "challenge")
CLUSTERS = ("devnet", "mainnet")
TOKEN_PROGRAMS = ("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA", "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb")


class DropsError(Exception):
    pass


def _obj(value: Any, where: str) -> dict:
    if not isinstance(value, dict):
        raise DropsError(f"{where}: not an object")
    return value


def _str(o: dict, key: str, pattern: re.Pattern[str] | None = None, max_len: int = 2000) -> str:
    value = o.get(key)
    if not isinstance(value, str) or len(value) > max_len or (pattern and not pattern.search(value)):
        raise DropsError(f"{key}: not a valid string")
    return value


def _int(o: dict, key: str, low: int = 0, high: int = MAX_SAFE) -> int:
    value = o.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise DropsError(f"{key}: not an integer in range")
    return value


def _one_of(o: dict, key: str, allowed: tuple[str, ...]) -> str:
    value = o.get(key)
    if value not in allowed:
        raise DropsError(f"{key}: not one of {allowed}")
    return value


def _iso(o: dict, key: str) -> str:
    value = _str(o, key, None, 40)
    if not re.match(r"^\d{4}-\d{2}-\d{2}T", value):
        raise DropsError(f"{key}: not an ISO time")
    try:
        _dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise DropsError(f"{key}: not an ISO time") from None
    return value


def _array(o: dict, key: str, max_len: int) -> list:
    value = o.get(key)
    if not isinstance(value, list) or len(value) > max_len:
        raise DropsError(f"{key}: not a list")
    return value


def _nullable(o: dict, key: str, read: Any) -> Any:
    return None if o.get(key) is None else read()


def _summary(value: Any) -> dict:
    o = _obj(value, "round")
    rid = _int(o, "id", 1)
    tag = _str(o, "tag")
    if tag != f"drop #{rid}":
        raise DropsError("tag")
    return {"id": rid, "tag": tag, "status": _one_of(o, "status", STATUSES), "deadline": _iso(o, "deadline")}


def parse_index(text: str) -> list[dict]:
    try:
        parsed = json.loads(text)
    except ValueError:
        raise DropsError("index: not JSON") from None
    rounds = [_summary(r) for r in _array(_obj(parsed, "index"), "rounds", 10_000)]
    if len({r["id"] for r in rounds}) != len(rounds):
        raise DropsError("index: duplicate round id")
    return sorted(rounds, key=lambda r: -r["id"])


def to_atomic(tiny: str, decimals: int) -> int | None:
    if not DECIMAL.search(tiny):
        return None
    whole, _, frac = tiny.partition(".")
    if len(frac) > decimals:
        return None
    return int(whole) * 10**decimals + int(frac.ljust(decimals, "0") or "0")


def parse_round(text: str) -> dict:
    try:
        parsed = json.loads(text)
    except ValueError:
        raise DropsError("round: not JSON") from None
    o = _obj(parsed, "round")
    head = _summary(o)
    decimals = _int(o, "decimals", 0, 18)
    pool = _str(o, "pool_tiny", DECIMAL)
    best = _str(o, "best_reply_tiny", DECIMAL)
    pool_atomic, best_atomic = to_atomic(pool, decimals), to_atomic(best, decimals)
    if pool_atomic is None or best_atomic is None:
        raise DropsError("pool_tiny: more decimals than the mint")
    if best_atomic > pool_atomic:
        raise DropsError("best_reply_tiny: more than the pool")

    def seed() -> dict:
        s = _obj(o["seed"], "seed")
        return {"slot": _int(s, "slot"), "blockhash": _str(s, "blockhash", BASE58_KEY), "sha256": _str(s, "sha256", HEX32)}

    entrants = []
    for value in _array(o, "entrants", 100_000):
        e = _obj(value, "entrant")
        if not isinstance(e.get("eligible"), bool):
            raise DropsError("eligible: not a boolean")
        entrants.append({"handle": _str(e, "handle", HANDLE), "eligible": e["eligible"], "reason": _str(e, "reason", None, 200)})
    if len({e["handle"] for e in entrants}) != len(entrants):
        raise DropsError("entrants: one entry per account")
    draw_order = []
    for h in _array(o, "draw_order", 100_000):
        if not isinstance(h, str) or not HANDLE.search(h):
            raise DropsError("draw_order: not a handle")
        draw_order.append(h)
    claims = []
    for value in _array(o, "claims", 10_000):
        c = _obj(value, "claim")
        amount = _str(c, "amount_atomic", ATOMIC)
        if int(amount) == 0 or int(amount) > U64_MAX:
            raise DropsError("amount_atomic: not a positive u64")
        proof = []
        for p in _array(c, "proof", 64):
            if not isinstance(p, str) or not HEX32.search(p):
                raise DropsError("proof: not 32-byte hex")
            proof.append(p)
        claims.append({"index": _int(c, "index"), "handle": _str(c, "handle", HANDLE),
                       "kind": _one_of(c, "kind", ("random", "best_reply")), "address": _str(c, "address", BASE58_KEY),
                       "amount_atomic": amount, "proof": proof})
    if len({c["address"] for c in claims}) != len(claims):
        raise DropsError("claims: one leaf per address")
    if len({c["index"] for c in claims}) != len(claims):
        raise DropsError("claims: duplicate index")
    if sum(int(c["amount_atomic"]) for c in claims) > pool_atomic:
        raise DropsError("claims: more than the pool")
    t = _obj(o.get("txs") if o.get("txs") is not None else {}, "txs")
    txs = {k: _str(t, k, BASE58_SIG) for k in ("create", "fund", "clawback") if t.get(k) is not None}
    round_ = {
        **head,
        "format": _one_of(o, "format", FORMATS),
        "pool_tiny": pool,
        "winners": _int(o, "winners", 0, 10_000),
        "best_reply_tiny": best,
        "prompt": _str(o, "prompt", None, 1000),
        "post_id": _str(o, "post_id", X_ID),
        "post_url": _str(o, "post_url", POST_URL),
        "cluster": _one_of(o, "cluster", CLUSTERS),
        "mint": _str(o, "mint", BASE58_KEY),
        "decimals": decimals,
        "token_program": _one_of(o, "token_program", TOKEN_PROGRAMS),
        "program_id": _str(o, "program_id", BASE58_KEY),
        "authority": _nullable(o, "authority", lambda: _str(o, "authority", BASE58_KEY)),
        "distribution_seed": _nullable(o, "distribution_seed", lambda: _str(o, "distribution_seed", BASE58_KEY)),
        "distributor": _nullable(o, "distributor", lambda: _str(o, "distributor", BASE58_KEY)),
        "vault": _nullable(o, "vault", lambda: _str(o, "vault", BASE58_KEY)),
        "merkle_root": _nullable(o, "merkle_root", lambda: _str(o, "merkle_root", HEX32)),
        "clawback_at": _nullable(o, "clawback_at", lambda: _iso(o, "clawback_at")),
        "seed": _nullable(o, "seed", seed),
        "entrants": entrants,
        "draw_order": draw_order,
        "claims": claims,
        "txs": txs,
    }
    if not round_["post_url"].endswith(f"/status/{round_['post_id']}"):
        raise DropsError("post_url: not the round post")
    if round_["status"] != "open" and not round_["seed"]:
        raise DropsError("seed: required once drawn")
    if round_["status"] in ("settled", "clawed_back") and not all(
            round_[k] for k in ("authority", "distribution_seed", "distributor", "vault", "merkle_root", "clawback_at")):
        raise DropsError("required once settled")
    return round_


def check_draw(round_: dict) -> dict | None:
    """drops.ts checkDraw, with the hand's own draw module standing in for
    the page's sha256 helpers (they are the same formula)."""
    from teeny_hands.giveaway import draw

    if not round_["seed"]:
        return None
    eligible = [e["handle"] for e in round_["entrants"] if e["eligible"]]
    expected = draw.order(round_["seed"]["sha256"], eligible)
    random = sorted(c["handle"] for c in round_["claims"] if c["kind"] == "random")
    first = round_["draw_order"][:round_["winners"]]
    settled = round_["status"] in ("settled", "clawed_back")
    claimed = {c["handle"] for c in round_["claims"]}
    return {
        "seed": draw.seed_hash(round_["id"], round_["seed"]["blockhash"]) == round_["seed"]["sha256"],
        "order": expected == round_["draw_order"],
        "winners": all(h in first for h in random) and len(random) <= round_["winners"]
        and (not settled or all(h in claimed for h in first)),
    }
