"""Who Teeny has really talked to on Moltbook, from its own ledger: the
input of the daily follow (docs/hands.md, "moltbook exchanges").

Moltbook's own rule is that following should be rare: follow an agent after
a real exchange with it, not on sight. An exchange here is one of:
- the agent replied under one of Teeny's comments (`answering_teeny` in the
  inbox's stimulus), or
- Teeny answered one of its comments (a `moltbook_comments` row whose
  parent_id is that comment) and it commented to Teeny again afterwards.

The comments come from the mind's inbox (`moltbook inbox` puts every comment
said to Teeny there, after the inbound guard; a held one never is), so this
reads only the ledger and asks Moltbook nothing. Agents Teeny already
followed or unfollowed (`moltbook_actions`) are left out, and so is Teeny.
`peers` is the peer list (`brain/moltbook-peers.txt`) less the same.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from .. import hand, stimuli
from ..rest import RestApi
from . import client, reads, rules

Json = dict[str, Any]


def _iso(at: float) -> str:
    return datetime.fromtimestamp(at, timezone.utc).isoformat().replace("+00:00", "Z")


def _payload(raw: Any) -> dict[str, Any]:
    try:
        got = json.loads(raw) if isinstance(raw, str) else {}
    except ValueError:
        return {}
    return got if isinstance(got, dict) else {}


def exchanges(days: int = 14, max_results: int = 10, api: RestApi | None = None) -> Json:
    """`{exchanges: [...], peers: [...]}`: the agents of the last `days` with
    a real exchange, most talk first (at most `max_results`), and the peers,
    none of them followed or unfollowed yet. Both empty means nobody to follow."""
    conn = rules.db()
    me = reads.me_name(api).lower()
    since = hand.now() - days * rules.DAY
    done = {str(r[0]).lower() for r in conn.execute("SELECT target FROM moltbook_actions WHERE verb IN ('follow', 'unfollow')")}
    marks = ",".join("?" * len(rules.COUNTED))
    answered_at: dict[str, float] = {
        str(r[0]): float(r[1])
        for r in conn.execute(
            f"SELECT parent_id, MIN(at) FROM moltbook_comments WHERE parent_id IS NOT NULL AND status IN ({marks}) GROUP BY parent_id",
            rules.COUNTED,
        )
    }
    said = stimuli.db().execute(
        "SELECT who, ref, at, payload FROM stimuli WHERE surface = 'moltbook' AND kind = 'comment' AND at >= ? ORDER BY at",
        (since,),
    ).fetchall()

    agents: dict[str, Json] = {}
    for who, ref, at, raw in said:
        try:
            name = client.agent_name(who)
        except hand.HandError:
            continue
        key = name.lower()
        if key == me or key in done:
            continue
        one = agents.setdefault(key, {"name": name, "replied_to_teeny": 0, "comments": 0, "teeny_answered": 0,
                                      "first_answer": None, "came_back": False, "last": 0.0})
        one["comments"] += 1
        if _payload(raw).get("answering_teeny"):
            one["replied_to_teeny"] += 1
        if one["first_answer"] is not None and float(at) > one["first_answer"]:
            one["came_back"] = True
        answer = answered_at.get(str(ref))
        if answer is not None:
            one["teeny_answered"] += 1
            if one["first_answer"] is None or answer < one["first_answer"]:
                one["first_answer"] = answer
        one["last"] = max(one["last"], float(at))

    real = [a for a in agents.values() if a["replied_to_teeny"] or (a["teeny_answered"] and a["came_back"])]
    real.sort(key=lambda a: (a["replied_to_teeny"] + a["comments"], a["last"]), reverse=True)
    out = [
        {"name": a["name"], "replied_to_teeny": a["replied_to_teeny"], "comments": a["comments"],
         "teeny_answered": a["teeny_answered"], "came_back": a["came_back"], "last_at": _iso(a["last"])}
        for a in real[:max_results]
    ]
    names, _ = reads.peer_names()
    peers = [n for n in names if n.lower() not in done and n.lower() != me]
    return {"exchanges": out, "peers": peers}
