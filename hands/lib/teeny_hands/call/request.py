"""What a call may be, before any service is asked.

A call is `{"service", "method", "path", "query"?, "body"?}` and nothing else.

- The path is a clean absolute path the hand appends to the service's base:
  printable ASCII, starting with `/`, at most MAX_PATH characters, and never
  `..`, `//`, a `.` segment, `\\`, `@`, `?`, `#` or `%`. So it cannot name
  another host, climb out of the base, add a query or a fragment, or say in
  percent-escapes what the allow list's regex did not read.
- The query is a flat object of strings, numbers and booleans (at most
  MAX_PAIRS, a string at most MAX_VALUE characters), its names plain.
- The body is a JSON object of at most MAX_BODY bytes as sent, no string in
  it over MAX_STRING characters, every name in it plain. A GET has none.

A name (a query's or a body's) is NAME: a field name, never a sentence, since
only values are read by the guard (`speech`).
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from typing import Any, Iterator

from .. import hand
from .services import METHODS

FIELDS = ("service", "method", "path", "query", "body")
MAX_PATH = 300
MAX_PAIRS = 30
MAX_VALUE = 500
MAX_BODY = 32 * 1024
MAX_STRING = 20_000
NAME = re.compile(r"[A-Za-z0-9_.\[\]-]{1,64}")
_PATH_FORBIDDEN = ("..", "//", "\\", "@", "?", "#", "%")

Json = dict[str, Any]


@dataclass(frozen=True)
class Call:
    service: str
    method: str
    path: str
    #: The query as sent, each value as text.
    query: tuple[tuple[str, str], ...]
    body: Json | None
    #: Every string value of the body, depth-first, then of the query: what
    #: the call says, for the guard.
    texts: tuple[str, ...]

    @property
    def is_read(self) -> bool:
        return self.method == "GET"


def _shape(reason: str) -> hand.Refused:
    return hand.Refused(reason, rule="shape")


def _path(value: Any) -> str:
    if not isinstance(value, str):
        raise _shape("path is a string, like /posts/abc")
    if (not value.startswith("/") or len(value) > MAX_PATH or not all("\x21" <= c <= "\x7e" for c in value)
            or any(bad in value for bad in _PATH_FORBIDDEN) or "." in value.split("/")):
        raise hand.Refused(
            f"the path is a clean absolute path: printable ASCII from /, at most {MAX_PATH} characters, never "
            "'..', '//', a '.' segment, '\\', '@', '?', '#', '%', a space or a control character. Pass the query "
            "as \"query\", and do not percent-encode", rule="path")
    return value


def _scalar(name: str, value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise _shape(f"query {name!r} is not a finite number")
        return repr(value)
    if isinstance(value, str):
        if len(value) > MAX_VALUE:
            raise _shape(f"query {name!r} is over {MAX_VALUE} characters")
        return value
    raise _shape(f"query {name!r} is a string, a number or a boolean, not {type(value).__name__}")


def _query(value: Any) -> tuple[tuple[tuple[str, str], ...], list[str]]:
    if value is None:
        return (), []
    if not isinstance(value, dict):
        raise _shape("query is a flat object of names to strings, numbers or booleans")
    if len(value) > MAX_PAIRS:
        raise _shape(f"query has {len(value)} pairs; at most {MAX_PAIRS}")
    pairs, said = [], []
    for name, item in value.items():
        if not NAME.fullmatch(name):
            raise _shape(f"a query name is {NAME.pattern}")
        pairs.append((name, _scalar(name, item)))
        if isinstance(item, str):
            said.append(item)
    return tuple(pairs), said


def _leaves(value: Any) -> Iterator[str]:
    """Every string value in VALUE, depth-first in order; names are checked
    to be plain, never read as speech."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for name, item in value.items():
            if not NAME.fullmatch(name):
                raise _shape(f"a body key is {NAME.pattern}: a field name, not words")
            yield from _leaves(item)
    elif isinstance(value, list):
        for item in value:
            yield from _leaves(item)
    elif isinstance(value, float) and not math.isfinite(value):
        raise _shape("the body holds a number that is not finite")


def _body(value: Any, method: str, present: bool) -> tuple[Json | None, list[str]]:
    if not present:
        return None, []
    if method == "GET":
        raise _shape("a GET carries no body; pass what it needs as query")
    if not isinstance(value, dict):
        raise _shape("body is a JSON object")
    said = list(_leaves(value))
    if any(len(s) > MAX_STRING for s in said):
        raise hand.Refused(f"a string in the body is over {MAX_STRING} characters", rule="size")
    size = len(json.dumps(value).encode())
    if size > MAX_BODY:
        raise hand.Refused(f"the body is {size} bytes as sent; at most {MAX_BODY} (32 KiB)", rule="size")
    return value, said


def service_of(raw: Any) -> str:
    """The service a call names, before anything else is read of it."""
    if not isinstance(raw, dict):
        raise _shape(f"a call is a JSON object {{{', '.join(FIELDS)}}}")
    unknown = sorted(str(k)[:40] for k in set(raw) - set(FIELDS))
    if unknown:
        raise _shape(f"a call is {{{', '.join(FIELDS)}}}; not {', '.join(unknown)}")
    service = raw.get("service")
    if not isinstance(service, str) or not service:
        raise _shape("service names a service in evals/services.toml")
    return service


def parse(raw: Any) -> Call:
    """RAW as a call, or Refused saying what is wrong with it."""
    service = service_of(raw)
    method = raw.get("method")
    if method not in METHODS:
        raise _shape(f"method is one of {', '.join(METHODS)}")
    path = _path(raw.get("path"))
    query, query_said = _query(raw.get("query"))
    body, body_said = _body(raw.get("body"), method, "body" in raw)
    try:
        json.dumps(raw, ensure_ascii=False).encode("utf-8")
    except UnicodeEncodeError:
        raise _shape("the call holds text that is not valid Unicode (a lone surrogate)") from None
    return Call(service, method, path, query, body, tuple(body_said + query_said))
