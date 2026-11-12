"""The verbs of hands/upstream (docs/hands.md, "upstream"; docs/roadmap.md, G9).

  R  releases   OpenHuman releases published after the commit Teeny pins
                (vendor/openhuman), each with the gaps in docs/oh-gaps.md its
                notes mention, by issue or pull request number, and those
                lines of the notes
  A  propose    stdin {tag, why}: one issue on Teeny's own repository asking
                the operator to bump the pin to that release, once per
                release, and only for a release `releases` says closes a gap

Bumping the pin is the operator's (vendor/ is frozen; docs/developers.md,
"Bumping OpenHuman"). Teeny notices and asks. The issue is built here: the
release, the pin, each gap it closes with the release-note lines that say so
(from code, not the model), then Teeny's own reasons; every word goes
through the guard (`gh open-issue`). Release notes are text from outside, so
only the lines naming a gap are passed on, each cut short and screened by
the guard's lexicon first. Under DRY_RUN `propose` answers with the issue it
would open and records nothing.
"""

from __future__ import annotations

import argparse
import datetime as dt
import re
import subprocess
from typing import Any, Mapping

from .. import hand

UPSTREAM = "tinyhumansai/openhuman"
PIN_PATH = "vendor/openhuman"
DEFAULT_REPO = "teenydroid/brain"
MAX_RELEASES = 10
MAX_LINE = 300
_ROW = re.compile(r"^\|\s*(G\d+)\b([^|]*)\|")
_NUMBER = re.compile(r"(?:#|/issues/|/pull/)(\d{3,6})\b")
TAG = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,60}")
_SCHEMA = """
CREATE TABLE IF NOT EXISTS pin_proposals (
    tag     TEXT PRIMARY KEY,
    at      REAL NOT NULL,
    repo    TEXT NOT NULL,
    number  INTEGER
);
"""


def gaps() -> dict[str, set[str]]:
    """docs/oh-gaps.md's table: gap id -> the issue and PR numbers its first
    cell names. A gap marked solved or resolved is left out."""
    try:
        text = (hand.root() / "docs" / "oh-gaps.md").read_text(encoding="utf-8")
    except OSError:
        return {}
    out: dict[str, set[str]] = {}
    for line in text.splitlines():
        m = _ROW.match(line)
        if not m:
            continue
        rest = line.split("|")[2] if line.count("|") > 2 else ""
        if re.search(r"\*\*(solved|resolved)\b", rest, re.I) or "resolved" in m.group(2).lower():
            continue
        out[m.group(1)] = set(_NUMBER.findall(m.group(2)))
    return out


def pin() -> str:
    """The commit vendor/openhuman is pinned at, from the checkout's HEAD."""
    got = subprocess.run(["git", "-C", str(hand.root()), "ls-tree", "HEAD", PIN_PATH], capture_output=True, text=True,
                         timeout=30)
    m = re.match(r"160000 commit ([0-9a-f]{40})\t", got.stdout)
    if got.returncode != 0 or not m:
        raise hand.HandError(f"no pinned commit at {PIN_PATH}")
    return m.group(1)


def _api(gh: Any):
    if gh is not None:
        return gh
    from ..gh import client  # noqa: PLC0415

    return client.GitHubApi.from_settings()


def _epoch(text: Any) -> float | None:
    try:
        return dt.datetime.fromisoformat(str(text).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _clean_lines(body: str, numbers: set[str]) -> list[str]:
    from ..guard.screen import screen_text  # noqa: PLC0415

    out = []
    for raw in body.splitlines():
        if not numbers & set(_NUMBER.findall(raw)):
            continue
        line = " ".join(raw.split())[:MAX_LINE]
        if line and screen_text(line, "inbound") is None:
            out.append(line)
    return out


def releases(_args: Any = None, _body: Any = None, gh: Any = None) -> Mapping[str, Any]:
    api = _api(gh)
    owner, name = UPSTREAM.split("/")
    pinned = pin()
    commit = api.call("GET", f"/repos/{owner}/{name}/commits/{pinned}")
    since = _epoch(((commit or {}).get("commit") or {}).get("committer", {}).get("date"))
    if since is None:
        raise hand.HandError("could not read when the pinned commit was made")
    known = gaps()
    out = []
    for rel in api.call("GET", f"/repos/{owner}/{name}/releases", query=[("per_page", str(MAX_RELEASES))]) or []:
        tag = str(rel.get("tag_name") or "")
        at = _epoch(rel.get("published_at"))
        if rel.get("draft") or not TAG.fullmatch(tag) or at is None or at <= since:
            continue
        body = str(rel.get("body") or "")
        closes = []
        for gap, numbers in known.items():
            lines = _clean_lines(body, numbers) if numbers else []
            if lines:
                closes.append({"gap": gap, "lines": lines})
        out.append({"tag": tag, "published_at": rel.get("published_at"), "prerelease": bool(rel.get("prerelease")),
                    "url": f"https://github.com/{UPSTREAM}/releases/tag/{tag}", "closes": closes})
    return {"pin": pinned, "releases": out}


def propose(_args: Any, body: Any, gh: Any = None) -> Mapping[str, Any]:
    from ..gh import actions  # noqa: PLC0415

    if not isinstance(body, dict) or set(body) - {"tag", "why"}:
        raise hand.Refused("a proposal is {tag, why}", rule="shape")
    tag, why = body.get("tag"), body.get("why")
    if not isinstance(tag, str) or not TAG.fullmatch(tag):
        raise hand.Refused("tag is a release tag", rule="shape")
    if not isinstance(why, str) or not why.strip() or len(why) > 1500:
        raise hand.Refused("why is 1 to 1500 characters", rule="shape")
    conn = hand.open_db(_SCHEMA)
    try:
        if conn.execute("SELECT 1 FROM pin_proposals WHERE tag = ?", (tag,)).fetchone():
            raise hand.Refused(f"the pin bump to {tag} was already proposed", rule="once")
    finally:
        conn.close()
    found = releases(gh=gh)
    rel = next((r for r in found["releases"] if r["tag"] == tag), None)
    if rel is None:
        raise hand.Refused(f"{tag} is not an OpenHuman release newer than the pin", rule="release")
    if not rel["closes"]:
        raise hand.Refused(f"{tag}'s notes name no open gap in docs/oh-gaps.md", rule="gap")
    ids = ", ".join(c["gap"] for c in rel["closes"])
    lines = [f"OpenHuman {tag} ({rel['url']}) came out after the commit `vendor/openhuman` is pinned at "
             f"(`{found['pin'][:12]}`), and its notes name gaps in `docs/oh-gaps.md`.", "", "## Gaps it closes", ""]
    for c in rel["closes"]:
        lines.append(f"- **{c['gap']}**")
        lines += [f"  - {line}" for line in c["lines"]]
    lines += ["", "## Why I want it", "", why.strip(), "", "---",
              "Asked by Teeny (docs/roadmap.md, G9). Bumping the pin is the operator's: docs/developers.md, "
              "\"Bumping OpenHuman\", then the live test."]
    repo = hand.setting("LADDER_ISSUE_REPO", DEFAULT_REPO) or DEFAULT_REPO
    issue = {"repo": repo, "title": f"pin bump: openhuman {tag} (closes {ids})", "body": "\n".join(lines)}
    with hand.lock("pin-proposal", timeout=30):
        got = actions.open_issue(issue, gh)
        if got.get("dry_run"):
            return {"dry_run": True, "would_open_issue": repo, "tag": tag, "title": got["title"], "body": got["body"]}
        conn = hand.open_db(_SCHEMA)
        try:
            with hand.transaction(conn):
                conn.execute("INSERT OR IGNORE INTO pin_proposals (tag, at, repo, number) VALUES (?, ?, ?, ?)",
                             (tag, hand.now(), repo, got.get("number")))
        finally:
            conn.close()
    return {"opened_issue": repo, "number": got.get("number"), "html_url": got.get("html_url"), "tag": tag}


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="hands/upstream", description="OpenHuman releases and the pin.")
    verbs = p.add_subparsers(dest="verb")
    verbs.add_parser("releases").set_defaults(handler=lambda a, b: releases(a, b))
    verbs.add_parser("propose").set_defaults(handler=lambda a, b: propose(a, b), takes_body=True)
    return p


def main(argv: list[str]) -> int:
    return hand.run("upstream", parser(), argv)
