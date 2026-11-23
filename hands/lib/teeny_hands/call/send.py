"""hands/call send: one call to a listed service, after every rule."""

from __future__ import annotations

from typing import Any

from .. import hand
from . import request, services

Json = dict[str, Any]


def _service(name: str) -> services.Service:
    listed = services.configured()
    if name not in listed:
        raise hand.Refused(f"no service {name[:40]!r} in evals/services.toml; the services are "
                           f"{', '.join(sorted(listed)) or 'none'}", rule="service")
    return listed[name]


def send(raw: Any) -> Json:
    svc = _service(request.service_of(raw))
    call = request.parse(raw)
    if not svc.allows(call.method, call.path):
        raise hand.Refused(f"{call.method} {call.path} is not allowed on {svc.name} (evals/services.toml)",
                           rule="allow")
    return {}
