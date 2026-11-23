"""hands/call send: one call to a listed service, after every rule."""

from __future__ import annotations

import json
from typing import Any

from .. import hand
from ..rest import RestApi, RestError
from . import request, services

Json = dict[str, Any]

#: The most of a service's answer passed back, as JSON.
MAX_ANSWER = 256 * 1024


def _service(name: str) -> services.Service:
    listed = services.configured()
    if name not in listed:
        raise hand.Refused(f"no service {name[:40]!r} in evals/services.toml; the services are "
                           f"{', '.join(sorted(listed)) or 'none'}", rule="service")
    return listed[name]


def _scrub(text: str, key: str) -> str:
    """TEXT with the key cut out, should a service have echoed it."""
    return text.replace(key, "[the key]") if key else text


def _send(svc: services.Service, call: request.Call, key: str) -> Json:
    """The call itself, and its answer as the hand's."""
    api = RestApi(svc.name, svc.base, key, svc.agent)
    try:
        status, parsed, _ = api.request(call.method, call.path, call.query, call.body)
    except RestError as exc:
        message = _scrub(str(exc), key)
        if exc.status == 429:
            raise hand.Refused(message, rule="rate_limited", status=429, retry_after=exc.retry_after) from None
        raise hand.HandError(message, status=exc.status) from None
    text = json.dumps(parsed, ensure_ascii=False)
    if key in text:
        raise hand.Refused(f"{svc.name}'s answer carries the key; it is not passed on", rule="key")
    size = len(text.encode())
    if size > MAX_ANSWER:
        raise hand.Refused(f"{svc.name} answered {size} bytes of JSON; at most 256 KiB is passed on. Ask for "
                           "less (a smaller page, a narrower query)", rule="size", status=status)
    return {"status": status, "json": parsed}


def _carry_out(raw: Any, row: Json) -> Json:
    svc = _service(request.service_of(raw))
    row["service"] = svc.name
    call = request.parse(raw)
    kind = "read" if call.is_read else "write"
    row.update(method=call.method, path=call.path, kind=kind)
    if not svc.allows(call.method, call.path):
        raise hand.Refused(f"{call.method} {call.path} is not allowed on {svc.name} (evals/services.toml)",
                           rule="allow")
    counts.check(svc, kind)
    key = hand.require(svc.key)
    row["key"] = key
    row["id"] = counts.reserve(svc, kind, call.method, call.path)
    return _send(svc, call, key)


def send(raw: Any) -> Json:
    """One call (request.py has its shape), after every rule, and one row
    in the call log whatever became of it."""
    row: Json = {"service": raw.get("service") if isinstance(raw, dict) else None}
    try:
        answer = _carry_out(raw, row)
    except hand.Refused as exc:
        counts.record(row, "refused", _scrub(exc.reason, row.get("key", "")), exc.detail.get("status"))
        raise
    except hand.HandError as exc:
        counts.record(row, "error", _scrub(str(exc), row.get("key", "")), exc.detail.get("status"))
        raise
    except Exception as exc:
        counts.record(row, "error", type(exc).__name__)
        raise
    counts.record(row, "ok", None, answer["status"])
    return answer
