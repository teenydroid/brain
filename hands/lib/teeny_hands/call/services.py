"""evals/services.toml: the services the key proxy may call, as the operator
listed them, held strictly to their shape.

Each `[services.<name>]` has exactly:

  base     https://<a named host>[/path]: no userinfo, query, fragment, bare
           IP address or localhost; a call is `base + path`
  key      the setting in secrets/teeny.env that holds its key, sent as
           `Authorization: Bearer`; named like `..._KEY` or `..._TOKEN`
  surface  the guard surface anything said there is checked as
  agent    the user-agent
  allow    `{method, path}` pairs: a call's method must be one of them and
           its whole path must match that pair's regex (re.fullmatch)

Anything else in the file is refused, so a key the loader does not know
(`follow_redirects`, `guard`) can never quietly mean something.

The file is read from this checkout (`SERVICES_FILE`), never from TEENY_ROOT
or a setting: nothing the box or a caller sets can point the hand at another
list. Tests that need a server on 127.0.0.1 call `load(..., loopback=True)`
from Python; nothing on the command line or in secrets/ can pass it.
"""

from __future__ import annotations

import ipaddress
import re
import tomllib
import urllib.parse
from dataclasses import dataclass
from pathlib import Path

from .. import hand
from ..guard import holds

#: The operator's list, in this checkout's evals/ (frozen).
SERVICES_FILE = Path(__file__).resolve().parents[4] / "evals" / "services.toml"

METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")
FIELDS = ("base", "key", "surface", "agent", "allow")
NAME = re.compile(r"[a-z][a-z0-9-]{0,30}")
KEY = re.compile(r"[A-Z][A-Z0-9_]*(KEY|TOKEN)")
AGENT = re.compile(r"[A-Za-z0-9._-]{1,60}")
#: What a base's path may hold: plain segments, never `..` or an escape.
BASE_PATH = re.compile(r"(/[A-Za-z0-9_.~-]+)*/?")
#: The most of either cap a setting may ask for.
MAX_READS_PER_DAY, MAX_WRITES_PER_DAY = 5000, 500


@dataclass(frozen=True)
class Service:
    name: str
    base: str
    key: str
    surface: str
    agent: str
    allow: tuple[tuple[str, re.Pattern[str]], ...]

    def allows(self, method: str, path: str) -> bool:
        return any(method == m and pattern.fullmatch(path) for m, pattern in self.allow)

    @property
    def _setting(self) -> str:
        return "CALL_" + self.name.upper().replace("-", "_")

    @property
    def reads_setting(self) -> str:
        return self._setting + "_READS_PER_DAY"

    @property
    def writes_setting(self) -> str:
        return self._setting + "_WRITES_PER_DAY"


def _bad(name: str, why: str) -> hand.HandError:
    return hand.HandError(f"evals/services.toml: {name}: {why}")


def _base(name: str, value: object, loopback: bool) -> str:
    if not isinstance(value, str) or any(c.isspace() or not c.isprintable() for c in value) or "\\" in value:
        raise _bad(name, "base is an https URL with no spaces, control characters or backslashes (a bad path)")
    parts = urllib.parse.urlsplit(value)
    host = (parts.hostname or "").lower()
    local = loopback and host == "127.0.0.1" and parts.scheme == "http"
    if parts.scheme != "https" and not local:
        raise _bad(name, "base must be https")
    if "@" in parts.netloc:
        raise _bad(name, "base may not carry a user or password")
    if parts.query or "?" in value:
        raise _bad(name, "base may not carry a query")
    if parts.fragment or "#" in value:
        raise _bad(name, "base may not carry a fragment")
    if not BASE_PATH.fullmatch(parts.path) or any(seg in (".", "..") for seg in parts.path.split("/")):
        raise _bad(name, f"base's path {parts.path!r} is not a plain path")
    try:
        parts.port
    except ValueError:
        raise _bad(name, "base's port is not a number") from None
    if not local:
        try:
            ipaddress.ip_address(host.strip("[]"))
            is_ip = True
        except ValueError:
            is_ip = False
        if (is_ip or not host or host == "localhost" or host.endswith(".localhost") or "." not in host
                or not re.fullmatch(r"[a-z0-9.-]+", host)):
            raise _bad(name, f"base's host must be a public name, not {host or 'nothing'!r}")
    return value.rstrip("/")


def _allow(name: str, value: object) -> tuple[tuple[str, re.Pattern[str]], ...]:
    if not isinstance(value, list) or not value:
        raise _bad(name, "allow is a list of at least one {method, path}")
    out = []
    for entry in value:
        if not isinstance(entry, dict):
            raise _bad(name, "each allow entry is {method, path}")
        unknown = sorted(set(entry) - {"method", "path"})
        if unknown:
            raise _bad(name, f"an allow entry has {', '.join(unknown)}; it is only method and path")
        method, path = entry.get("method"), entry.get("path")
        if method not in METHODS:
            raise _bad(name, f"method {method!r} is not one of {', '.join(METHODS)}")
        if not isinstance(path, str) or not path:
            raise _bad(name, "each allow entry's path is a regex")
        try:
            out.append((method, re.compile(path)))
        except re.error as exc:
            raise _bad(name, f"path {path!r} is not a regex: {exc}") from None
    return tuple(out)


def _service(name: object, table: object, loopback: bool) -> Service:
    if not isinstance(name, str) or not NAME.fullmatch(name):
        raise hand.HandError(f"evals/services.toml: a service's name is {NAME.pattern}, not {name!r}")
    if not isinstance(table, dict):
        raise _bad(name, "is a table")
    unknown = sorted(set(table) - set(FIELDS))
    if unknown:
        raise _bad(name, f"{', '.join(unknown)} is not a field (they are {', '.join(FIELDS)})")
    missing = [f for f in FIELDS if f not in table]
    if missing:
        raise _bad(name, f"needs {', '.join(missing)}")
    key = table["key"]
    if not isinstance(key, str) or not KEY.fullmatch(key):
        raise _bad(name, f"key names a setting like {KEY.pattern}, not {key!r}")
    surface = table["surface"]
    if surface not in holds.SURFACES:
        raise _bad(name, f"surface {surface!r} is not one of the guard's")
    agent = table["agent"]
    if not isinstance(agent, str) or not AGENT.fullmatch(agent):
        raise _bad(name, f"agent is {AGENT.pattern}")
    return Service(name, _base(name, table["base"], loopback), key, surface, agent, _allow(name, table["allow"]))


def load(path: Path, *, loopback: bool = False) -> dict[str, Service]:
    """The services in PATH, each held to its shape; HandError naming the
    first thing wrong. `loopback` lets a base be http://127.0.0.1 (tests)."""
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise hand.HandError(f"evals/services.toml: no file at {path}") from None
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise hand.HandError(f"evals/services.toml: {exc}") from None
    unknown = sorted(set(data) - {"services"})
    if unknown:
        raise hand.HandError(f"evals/services.toml: {', '.join(unknown)} is not a table it has (only services)")
    listed = data.get("services", {})
    if not isinstance(listed, dict):
        raise hand.HandError("evals/services.toml: services is a table of services")
    return {name: _service(name, table, loopback) for name, table in listed.items()}


def configured() -> dict[str, Service]:
    """The operator's list (SERVICES_FILE). The tests replace this function."""
    return load(SERVICES_FILE)
