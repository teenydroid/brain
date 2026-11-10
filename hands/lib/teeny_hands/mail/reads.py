"""The read verbs' answers. Listing, a message and a thread go through the
read cache; `codes` always reads fresh, since a code is wanted the minute it
lands. Nothing here writes anywhere but the cache."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from .. import hand
from ..readcache import ReadCache
from ..rest import RestApi
from . import client, codes

MINUTE = 60.0

CACHE = ReadCache(
    "mail_cache",
    "AGENTMAIL_API_KEY",
    {"whoami": 10 * MINUTE, "list": MINUTE, "get": 10 * MINUTE, "thread": 2 * MINUTE},
    "MAIL_CACHE_ENABLED",
)

Json = dict[str, Any]


def _api(api: RestApi | None) -> RestApi:
    return api or client.api()


def iso(at: float) -> str:
    return datetime.fromtimestamp(at, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def whoami(api: RestApi | None = None) -> Json:
    me = CACHE.cached("whoami", (), lambda: _api(api).call("GET", "/auth/me"))
    inbox = client.inbox()
    scoped = me.get("inbox_id") if isinstance(me, Mapping) else None
    return {
        "auth": me,
        "inbox": inbox,
        "matches": None if not scoped else scoped.lower() == inbox.lower(),
    }


def list_page(api: RestApi | None, limit: int, labels: list[str] = (), after: str | None = None, ascending: bool = False) -> list[Json]:
    query: list[tuple[str, str]] = [("limit", str(limit))]
    query += [("labels", label) for label in labels]
    if after:
        query.append(("after", after))
    if ascending:
        query.append(("ascending", "true"))
    got = _api(api).call("GET", client.inbox_path("messages"), query)
    messages = got.get("messages") if isinstance(got, Mapping) else None
    return [m for m in messages or [] if isinstance(m, Mapping)]


def listing(unread: bool, max_results: int, label: str | None, api: RestApi | None = None) -> Json:
    labels = (["unread"] if unread else []) + ([client.label(label)] if label else [])
    got = CACHE.cached("list", (client.inbox(), tuple(labels), max_results), lambda: [client.summary(m) for m in list_page(api, max_results, labels)])
    return {"messages": got}


def fetch(message_id: str, api: RestApi | None = None) -> Json:
    """One message as AgentMail sends it (uncached; for the hand's own rules)."""
    got = _api(api).call("GET", client.inbox_path("messages", client.message_id(message_id)))
    if not isinstance(got, Mapping):
        raise hand.HandError("AgentMail sent something unreadable")
    return dict(got)


def get(message_id: str, api: RestApi | None = None) -> Json:
    mid = client.message_id(message_id, "ID")
    return {"message": CACHE.cached("get", (client.inbox(), mid), lambda: client.full(fetch(mid, api)))}


def thread(thread_id: str, api: RestApi | None = None) -> Json:
    tid = client.message_id(thread_id, "ID")

    def load() -> Json:
        got = _api(api).call("GET", client.inbox_path("threads", tid))
        got = got if isinstance(got, Mapping) else {}
        return {
            "thread_id": got.get("thread_id", tid),
            "subject": got.get("subject"),
            "message_count": got.get("message_count"),
            "messages": [client.full(m) for m in got.get("messages") or [] if isinstance(m, Mapping)],
        }

    return {"thread": CACHE.cached("thread", (client.inbox(), tid), load)}


def recent_codes(since_minutes: int, from_domain: str | None, api: RestApi | None = None) -> Json:
    """Codes and allowlisted verification links from mail received in the
    last `since_minutes`, newest first."""
    domain = from_domain.strip().lower().lstrip("@") if from_domain else None
    since = iso(hand.now() - timedelta(minutes=since_minutes).total_seconds())
    api = _api(api)
    found: list[Json] = []
    withheld = 0
    for item in list_page(api, 50, after=since):
        if client.is_own(item):
            continue
        sender = client.address_of(item.get("from")) or ""
        if domain and not (sender.endswith("@" + domain) or sender.endswith("." + domain)):
            continue
        message = fetch(str(item.get("message_id")), api)
        text = f"{message.get('subject') or ''}\n{client.body_text(message)}"
        got = codes.extract(text)
        withheld += got["withheld_links"]
        if got["codes"] or got["links"]:
            found.append(
                {
                    "message_id": message.get("message_id"),
                    "from": sender,
                    "subject": message.get("subject"),
                    "received_at": message.get("timestamp"),
                    "codes": got["codes"],
                    "links": got["links"],
                }
            )
    return {"since": since, "from_domain": domain, "found": found, "withheld_links": withheld}
