"""Teeny's two reviewed lists of websites, both in deploy/ and owned by the
operator:

  deploy/browser-allow.txt  the hosts OpenHuman's raw `http_request` tool may
                            reach, which bin/lib/core_sections/automation.py
                            writes into the core's config. It no longer
                            bounds the browser.
  deploy/browser-deny.txt   the hosts the browser never opens. The browser
                            may start on any public https page (`open_web`)
                            that is not on it (`denied`); hands/tasks refuses
                            any other start, and the desk hands the list to
                            the operator with every task.

Both lists name plain hosts; a host matches itself and its subdomains."""

from __future__ import annotations

import ipaddress
import re
from pathlib import Path
from urllib.parse import urlsplit

from . import hand

_HOST = re.compile(r"^(?=.{1,253}$)(?!-)[a-z0-9-]{1,63}(?<!-)(\.(?!-)[a-z0-9-]{1,63}(?<!-))+$")


class BadList(ValueError):
    pass


def path(root: Path | None = None) -> Path:
    return (root or hand.root()) / "deploy" / "browser-allow.txt"


def parse(text: str) -> list[str]:
    """One lowercase host per line; blank lines and `#` comments skipped. A
    wildcard or anything that is not a plain host name is an error, not
    something to skip: a typo must not quietly widen or empty the list."""
    hosts: list[str] = []
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.split("#", 1)[0].strip().lower()
        if not line:
            continue
        if not _HOST.match(line):
            raise BadList(f"line {number}: {raw.strip()!r} is not a plain host name")
        if line not in hosts:
            hosts.append(line)
    if not hosts:
        raise BadList("the list is empty")
    return hosts


def load(root: Path | None = None) -> list[str]:
    return parse(path(root).read_text())


def allowed(url: str, hosts: list[str]) -> bool:
    """An https (or http) URL whose host is on the list or under one of its
    hosts. No credentials in the URL, no other scheme."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    if parts.scheme not in ("https", "http") or parts.username or parts.password:
        return False
    host = (parts.hostname or "").lower().rstrip(".")
    return any(host == h or host.endswith("." + h) for h in hosts)


# --- the open web and its deny list (deploy/browser-deny.txt) ---------------------

def deny_path(root: Path | None = None) -> Path:
    return (root or hand.root()) / "deploy" / "browser-deny.txt"


def load_deny(root: Path | None = None) -> list[str]:
    """The deny list. A missing file is an error, not an empty list: without
    it the open web would have no fence at all."""
    return parse(deny_path(root).read_text())


def host_of(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").lower().rstrip(".")
    except ValueError:
        return ""


def denied(url: str, hosts: list[str]) -> bool:
    """Whether the URL's host is a denied host or under one."""
    host = host_of(url)
    return any(host == h or host.endswith("." + h) for h in hosts)


def _not_a_public_name(host: str) -> bool:
    """A bare IP address, a single label, or a local or internal name. The
    open web is reached by name: an address in a link is not a reputed one."""
    if not host or "." not in host or host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
        return True
    try:
        ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return not _HOST.match(host)
    return True


def open_web(url: str) -> str | None:
    """Why a URL is no page the browser may start on, or None when it may:
    https only, no credentials in it, a public host name."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return "it is not a URL"
    if parts.scheme != "https":
        return "only https pages"
    if parts.username or parts.password:
        return "no credentials in the URL"
    if _not_a_public_name(host_of(url)):
        return "not a public host"
    return None
