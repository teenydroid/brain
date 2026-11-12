"""The verbs of hands/ladder (docs/hands.md, "ladder"; docs/roadmap.md, G3).

  R  report     each capability's level on the autonomy ladder and what the
                next one still needs (bin/lib/autonomy.py, frozen)
  R  fences     the fence ids docs/governance.md names, with each fence
  A  propose    stdin {fence, title, ask, evidence, would_do, risk}: one fence
                proposal, as an issue on Teeny's own repository labelled
                `fence-proposal`, at most one in PROPOSAL_DAYS

A proposal asks; it never changes anything. Teeny can't send a diff to a
frozen path (the frozen-path gate refuses it), so this is the only way it
asks for more freedom, and the operator answers in the issue
(docs/governance.md, "How a fence changes"). The body is built here, not by
the model: the model's words go in their own sections, and the ladder's
numbers for the capability come from frozen code, so a proposal can't
overstate its evidence. Every word goes through the guard (`gh open-issue`).
Under DRY_RUN it answers with the issue it would open and records nothing.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any, Mapping

from .. import hand

LABEL = "fence-proposal"
#: One proposal a week at most (DIRECTIVE.md, Level 5; docs/roadmap.md, G3).
PROPOSAL_DAYS = 7
DEFAULT_REPO = "teenydroid/brain"
FIELDS = {"fence": 60, "title": 120, "ask": 1500, "evidence": 3000, "would_do": 1500, "risk": 1500}
_SCHEMA = """
CREATE TABLE IF NOT EXISTS fence_proposals (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    at      REAL NOT NULL,
    fence   TEXT NOT NULL,
    repo    TEXT NOT NULL,
    number  INTEGER
);
"""


def _autonomy():
    lib = Path(__file__).resolve().parents[3].parent / "bin" / "lib"
    if str(lib) not in sys.path:
        sys.path.insert(0, str(lib))
    import autonomy  # noqa: PLC0415

    return autonomy


def report(_args: Any = None, _body: Any = None) -> Mapping[str, Any]:
    a = _autonomy()
    root = hand.root()
    try:
        out = a.report(root, hand.secrets_dir(), hand.state_dir())
    except a.LadderError as exc:
        raise hand.HandError(str(exc)) from None
    # What Teeny reads: levels, drops by kind and day only, what is still needed.
    return {"capabilities": [{
        "capability": c["capability"], "level": c["level"], "effective": c["effective"], "next": c["next"],
        "dropped_by": sorted({d["event"] for d in c["drops"]}), "still_needs": c["still_needs"],
        "evidence": [{k: e[k] for k in ("proof", "has", "need", "met")} for e in c["evidence"]],
    } for c in out["capabilities"]]}


def fence_table() -> dict[str, str]:
    """docs/governance.md's fences: id -> the fence, in its words."""
    try:
        text = (hand.root() / "docs" / "governance.md").read_text(encoding="utf-8")
    except OSError:
        return {}
    section = text.split("\n## The fences\n", 1)[-1].split("\n## ", 1)[0]
    out: dict[str, str] = {}
    for line in section.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) == 4 and re.fullmatch(r"[a-z][a-z0-9-]*", cells[0]):
            out[cells[0]] = cells[1]
    return out


def fences(_args: Any = None, _body: Any = None) -> Mapping[str, Any]:
    a = _autonomy()
    return {"fences": [{"id": k, "fence": v} for k, v in fence_table().items()],
            "capabilities": [f"ladder:{c}" for c in a.CAPABILITIES]}


def _clean(body: Any) -> dict[str, str]:
    if not isinstance(body, dict):
        raise hand.Refused("a proposal is a JSON object", rule="shape")
    extra = set(body) - set(FIELDS)
    if extra:
        raise hand.Refused(f"unknown fields: {', '.join(sorted(extra))}", rule="shape")
    out = {}
    for name, limit in FIELDS.items():
        value = body.get(name)
        if not isinstance(value, str) or not value.strip():
            raise hand.Refused(f"a proposal needs {name}", rule="shape")
        if len(value) > limit:
            raise hand.Refused(f"{name} is over {limit} characters", rule="shape")
        out[name] = value.strip()
    return out


def build(fields: Mapping[str, str]) -> dict[str, str]:
    """The issue: the fence named, the ask, Teeny's evidence, the ladder's own
    numbers, what it would do, the risk."""
    table = fence_table()
    fence = fields["fence"]
    lines: list[str]
    if fence.startswith("ladder:"):
        cap = fence.split(":", 1)[1]
        got = {c["capability"]: c for c in report()["capabilities"]}.get(cap)
        if got is None:
            raise hand.Refused(f"no capability {cap!r} on the ladder", rule="fence")
        named = f"the autonomy ladder: `{cap}`, at L{got['level']}"
        ladder = [f"- level: L{got['level']}" + (f" (dropped to L{got['effective']})" if got["effective"] != got["level"]
                                                 else ""),
                  "- still needed for the next level, by the ladder's own count: "
                  + ("; ".join(got["still_needs"]) or "nothing; the evidence is in")]
    elif fence in table:
        named = f"`{fence}`: {table[fence]}"
        ladder = ["- this is a fence, not a level: it moves only by the operator's pull request"]
    else:
        raise hand.Refused(f"no fence {fence!r} in docs/governance.md (hands/ladder fences)", rule="fence")
    lines = [f"**Fence:** {named}", "", "## What I am asking for", "", fields["ask"], "",
             "## My evidence", "", fields["evidence"], "", "## What the ladder says (frozen code, not me)", "", *ladder,
             "", "## What I would do with it", "", fields["would_do"], "", "## What could go wrong", "", fields["risk"],
             "", "---", "A fence proposal from Teeny (docs/governance.md, \"How a fence changes\"). It changes nothing "
             "by itself: the operator answers here, and lands any change in a pull request of their own."]
    return {"title": f"fence-proposal: {fields['title']}", "body": "\n".join(lines)}


def _recent(conn) -> int:
    since = hand.now() - PROPOSAL_DAYS * 86400
    return conn.execute("SELECT COUNT(*) FROM fence_proposals WHERE at >= ?", (since,)).fetchone()[0]


def propose(_args: Any, body: Any, gh: Any = None) -> Mapping[str, Any]:
    from ..gh import actions, client  # noqa: PLC0415

    fields = _clean(body)
    issue = build(fields)
    repo = hand.setting("LADDER_ISSUE_REPO", DEFAULT_REPO) or DEFAULT_REPO
    conn = hand.open_db(_SCHEMA)
    try:
        if _recent(conn):
            raise hand.Refused(f"one fence proposal in {PROPOSAL_DAYS} days; the last is still recent", rule="weekly")
    finally:
        conn.close()
    with hand.lock("fence-proposal", timeout=30):
        conn = hand.open_db(_SCHEMA)
        try:
            if _recent(conn):
                raise hand.Refused(f"one fence proposal in {PROPOSAL_DAYS} days", rule="weekly")
            got = actions.open_issue({"repo": repo, **issue}, gh)
            if got.get("dry_run"):
                return {"dry_run": True, "would_open_issue": repo, "label": LABEL, "fence": fields["fence"],
                        "title": got["title"], "body": got["body"]}
            with hand.transaction(conn):
                conn.execute("INSERT INTO fence_proposals (at, fence, repo, number) VALUES (?, ?, ?, ?)",
                             (hand.now(), fields["fence"], repo, got.get("number")))
        finally:
            conn.close()
    labelled = False
    if got.get("number"):
        owner, name = client.split_repo(repo)
        try:
            api = gh or client.GitHubApi.from_settings()
            api.call("POST", f"/repos/{owner}/{name}/issues/{int(got['number'])}/labels", body={"labels": [LABEL]})
            labelled = True
        except Exception:  # noqa: BLE001 - the issue is open either way; the title says what it is
            labelled = False
    return {"opened_issue": repo, "number": got.get("number"), "html_url": got.get("html_url"), "label": labelled,
            "fence": fields["fence"]}


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="hands/ladder", description="The autonomy ladder, and fence proposals.")
    verbs = p.add_subparsers(dest="verb")
    verbs.add_parser("report").set_defaults(handler=report)
    verbs.add_parser("fences").set_defaults(handler=fences)
    verbs.add_parser("propose").set_defaults(handler=lambda a, b: propose(a, b), takes_body=True)
    return p


def main(argv: list[str]) -> int:
    return hand.run("ladder", parser(), argv)
