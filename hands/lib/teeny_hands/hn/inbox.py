"""What people said to Teeny on Hacker News, and new comments on the threads
it watches, into Teeny's mind (docs/hands.md, "hn inbox").

HN has no notifications, so `scan` reads two places:

- Teeny's own items (`HN_USERNAME`'s `submitted`, newest first, the last
  `--max` within `--days`): every direct reply to its story or its comment
  was said to Teeny. Replies further down were said to someone else.
- the threads it watches (`watch`): every comment newer than the watch's
  `last_seen`, which moves on once a whole thread has been read.

Each comment by someone else, neither dead nor deleted, is screened by the
inbound guard (surface `hn`) and, when allowed, added to the stimuli inbox
once: `hn · comment`, ref the comment id, with the story, the parent and
the comment's page. HN takes no answers from Teeny, so the mind answers
elsewhere or not at all.

A run reads at most MAX_FETCH items (one request each), whatever the
threads hold; `truncated` says it stopped there. Reads only; re-running it
is free: the inbox ignores a comment it already has.
"""

from __future__ import annotations

from typing import Any, Mapping

from .. import hand, stimuli
from ..rest import RestApi
from . import reads, watch

#: Items one run reads, at most.
MAX_FETCH = 100
#: How deep a watched thread is read.
WATCH_DEPTH = 20
#: Parents followed up to find a comment's story.
_ROOT_STEPS = 20

Json = dict[str, Any]


def _screen(text: str, context: Mapping[str, Any]) -> Any:
    """The inbound guard's verdict. Imported by name, so a test can stand in
    for the other hand's gate."""
    from ..guard.gate import inbound

    return inbound(text, surface="hn", context=dict(context))


class _Run:
    """One scan: its read budget and its counts."""

    def __init__(self, me: str | None, api: RestApi | None) -> None:
        self.me = (me or "").lower()
        self.api = api
        self.budget = reads.Budget(MAX_FETCH)
        self.truncated = False
        self.counts = {"seen": 0, "added": 0, "held": 0}
        self._stories: dict[int, int | None] = {}

    def fetch(self, item_id: int) -> Any:
        """One item, fresh (its replies may be new), against the budget;
        None once the budget is spent."""
        if self.budget.left <= 0:
            self.truncated = True
            return None
        self.budget.left -= 1
        return reads.raw_item(item_id, self.api, fresh=True)

    def mine(self, item: Mapping[str, Any]) -> bool:
        return bool(self.me) and str(item.get("by", "")).lower() == self.me

    def story_of(self, item: Mapping[str, Any]) -> int | None:
        """The story a comment is on, read up its parents through the cache
        (and the budget); None when that cannot be told."""
        start = item.get("id")
        if start in self._stories:
            return self._stories[start]
        node: Any = item
        for _ in range(_ROOT_STEPS):
            if not isinstance(node, Mapping) or node.get("type") != "comment":
                break
            parent = node.get("parent")
            if not isinstance(parent, int) or self.budget.left <= 0:
                node = None
                break
            self.budget.left -= 1
            node = reads.raw_item(parent, self.api)
        found = node.get("id") if isinstance(node, Mapping) and node.get("type") != "comment" else None
        self._stories[start] = found
        return found

    def file(self, item: Mapping[str, Any], parent: Mapping[str, Any], story_id: int | None, **more: Any) -> None:
        """One comment someone else wrote: screened, then into the inbox."""
        if not reads.alive(item) or self.mine(item):
            return
        text = reads.plain(item.get("text"))
        if not text:
            return
        self.counts["seen"] += 1
        cid, author = str(item["id"]), str(item.get("by") or "")
        verdict = _screen(text, {"comment_id": cid, "story_id": story_id, "parent_id": parent.get("id"),
                                 "author": author})
        if verdict.kind != "allow":
            self.counts["held"] += 1
            return
        payload: Json = {"story_id": story_id, "parent_id": parent.get("id"),
                         "url": f"{reads.ITEM_PAGE}{cid}", "to_teeny": self.mine(parent), **more}
        if stimuli.add("hn", "comment", cid, text, who=author or None, payload=payload, at=item.get("time")):
            self.counts["added"] += 1


def _own(run: _Run, days: int, max_items: int) -> int:
    """Teeny's own recent items, and the direct replies to each; how many
    were looked at."""
    got = reads.raw_user(run.me, run.api) if run.me else None
    submitted = got.get("submitted") if isinstance(got, Mapping) and isinstance(got.get("submitted"), list) else []
    since = hand.now() - days * 86400
    looked = 0
    for item_id in sorted((i for i in submitted if isinstance(i, int)), reverse=True)[:max_items]:
        item = run.fetch(item_id)
        if item is None and run.truncated:
            break
        if not isinstance(item, Mapping):
            continue
        if float(item.get("time") or 0) < since:
            break  # newest first: the rest are older still
        if not reads.alive(item):
            continue
        looked += 1
        story_id = item.get("id") if item.get("type") != "comment" else None
        for kid in item.get("kids") or []:
            reply = run.fetch(kid)
            if reply is None and run.truncated:
                return looked
            if isinstance(reply, Mapping):
                if story_id is None:
                    story_id = run.story_of(item)
                run.file(reply, item, story_id)
    return looked


def _watched(run: _Run, follow: Json) -> None:
    """The comments newer than the watch's `last_seen`, filed; `last_seen`
    moved on only when the whole thread was read."""
    root = run.fetch(follow["item_id"])
    if not reads.alive(root):
        return
    story_id = root["id"] if root.get("type") != "comment" else run.story_of(root)
    found, cut = reads.walk(root, WATCH_DEPTH, run.budget, lambda i: reads.raw_item(i, run.api, fresh=True))
    if cut:
        run.truncated = True
    by_id = {root["id"]: root, **{c["id"]: c for c, _ in found}}
    newest = follow["last_seen"]
    for comment, _ in found:
        at = float(comment.get("time") or 0)
        newest = max(newest, at)
        if at <= follow["last_seen"]:
            continue
        parent = by_id.get(comment.get("parent"), {"id": comment.get("parent")})
        run.file(comment, parent, story_id, watched=follow["item_id"])
    if not cut:
        watch.seen(follow["item_id"], newest)


def scan(days: int = 7, max_items: int = 10, api: RestApi | None = None) -> Json:
    me = hand.setting("HN_USERNAME")
    follows = watch.active()
    if not me and not follows:
        raise hand.HandError("hn inbox needs HN_USERNAME in secrets/teeny.env, or a watched thread (hn watch add)")
    run = _Run(me, api)
    own = _own(run, days, max_items) if me else 0
    for follow in follows:
        if run.budget.left <= 0:
            run.truncated = True
            break
        _watched(run, follow)
    return {"own": own, "watched": len(follows), **run.counts, "truncated": run.truncated}
