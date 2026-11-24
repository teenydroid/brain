"""The unresolved review threads of a pull request that its author owes an
answer: those a review bot (policy.REVIEW_BOTS) or a trusted person opened.
A stranger's thread is nobody's to answer and holds nothing: anybody may
open one. Reads only; the words in it are other people's.
"""

from __future__ import annotations

import re
from typing import Any, Callable

from . import policy

Json = dict[str, Any]

MAX_PAGES = 5
#: The most of one comment passed on.
CLIP = 1500
QUERY = """
query($owner: String!, $name: String!, $number: Int!, $after: String) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      reviewThreads(first: 100, after: $after) {
        pageInfo { hasNextPage endCursor }
        nodes { id isResolved isOutdated path line
          opening: comments(first: 1) { nodes { id author { __typename login } body } }
          latest: comments(last: 30) { nodes { id author { __typename login } body } } }
      }
    }
  }
}
"""
_COMMENT = re.compile(r"<!--.*?(?:-->|\Z)", re.DOTALL)


def words(text: Any) -> str:
    """One text for the brief: no hidden comments, clipped."""
    text = _COMMENT.sub("", text if isinstance(text, str) else "").strip()
    return text if len(text) <= CLIP else text[:CLIP] + "…"


def heard(login: str | None, tier: Callable[[str | None], str]) -> str | None:
    """Whose words the babysitter's agent is shown: `bot` (a review bot),
    `trusted`, or None."""
    if not login:
        return None
    if login.lower() in policy.REVIEW_BOTS:
        return "bot"
    return "trusted" if tier(login) in policy.TRUSTED else None


def _who(node: Any) -> str | None:
    author = node.get("author") if isinstance(node, dict) else None
    if not isinstance(author, dict) or not isinstance(author.get("login"), str):
        return None
    # GraphQL names an app without the [bot] only an app's login ends with.
    return author["login"] + ("[bot]" if author.get("__typename") == "Bot" else "")


def _nodes(value: Any) -> list[Json]:
    nodes = value.get("nodes") if isinstance(value, dict) else None
    return [n for n in nodes if isinstance(n, dict)] if isinstance(nodes, list) else []


def unresolved(gh: Any, owner: str, name: str, number: int,
               tier: Callable[[str | None], str]) -> tuple[list[Json], bool]:
    """Each `{id, path, line, outdated, opened_by, who, last, comments}`, and
    whether there were more threads than were read. `who` is `trusted` when a
    trusted person opened it or spoke in it, else `bot`; `last` is the id of
    the newest comment by either (a stranger's counts for nothing)."""
    out: list[Json] = []
    after = None
    for _ in range(MAX_PAGES):
        data = gh.graphql(QUERY, {"owner": owner, "name": name, "number": number, "after": after})
        pull = (data.get("repository") or {}).get("pullRequest") or {}
        page = pull.get("reviewThreads") or {}
        for t in _nodes(page):
            opening = _nodes(t.get("opening"))
            if t.get("isResolved") or not opening or not isinstance(t.get("id"), str):
                continue
            opener = _who(opening[0])
            if heard(opener, tier) is None:
                continue
            seen = {opening[0].get("id")}
            said = opening + [c for c in _nodes(t.get("latest")) if c.get("id") not in seen]
            shown = [{"author": _who(c), "body": words(c.get("body"))} for c in said
                     if heard(_who(c), tier) or tier(_who(c)) == policy.TEENY]
            counted = [(c.get("id"), kind) for c in said if (kind := heard(_who(c), tier))]
            out.append({"id": t["id"], "path": t.get("path"), "line": t.get("line"),
                        "outdated": bool(t.get("isOutdated")), "opened_by": opener,
                        "who": "trusted" if any(k == "trusted" for _, k in counted) else "bot",
                        "last": counted[-1][0], "comments": shown})
        info = page.get("pageInfo") or {}
        if not info.get("hasNextPage"):
            return out, False
        after = info.get("endCursor")
    return out, True
