"""The reviewed list of websites Teeny's browser may open
(deploy/browser-allow.txt): read by bin/lib/core_sections/automation.py to
configure the core, and by hands/tasks to refuse a task that starts anywhere
else."""

from __future__ import annotations

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
