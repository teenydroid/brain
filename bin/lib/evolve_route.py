"""Findings that are code's to fix leave the text proposer
(docs/evolve.md, "1b. Findings", "Routed to code").

On 2026-11-22, 18 of the 19 evolve pull requests were 1 to 5 line prompt
rules, and `f:refusals:upvote_moltbook:duplicate` stood at 116 events after
three candidates aimed at duplicate refusals of the moltbook hand (g16c3
scored `hurt`, g17c3 parked, g18c3 `worked` at 45 and grew back). Refusing a
duplicate upvote is a hand's answer to a call the playbook should not make,
or should treat as done: code, which the proposer cannot write (it changes
text under brain/ only).

A finding is **routed** on first sight when the ledgers say no playbook's
words are behind it (`Finding.cause`, evolve_findings: a refusal of a beat's
action, or runs that all failed at an input script). Any other is when its
family has had EVOLVE_ROUTE_AFTER (K)
prompt-level candidates and they did not help: its number has not fallen
under where it stood at the latest of them on its own id, or every one of
them that merged scored no-effect, no-data or hurt. Its family:

  refusals     refusals:<hand>:<rule>, the hand from the beat verb's tail
               (`upvote_moltbook` -> moltbook) or the playbook's head
               (`moltbook-curate` -> moltbook); a learning's candidate whose
               check counts the rule with no verb or playbook counts for
               every hand
  act_failed   act_failed:<playbook>
  guard_holds  guard_holds:<surface>:<category>

A candidate counts when it was built and judged (ATTEMPTED): a skip, a
deferral, a crash or one still in flight does not. Growth findings are never
routed: what Teeny writes is the proposer's to change.

Where it goes depends on the code a fix would touch (`code_paths`): the
refusing hand (`hands/<hand>`, `hands/lib/teeny_hands/<hand>/`), the hands a
failing playbook calls (or `bin/act` when it calls none), the guard for a
hold. When none of them is frozen (evals/frozen.txt) it becomes a want of
kind `finding` for the agentic mind (bin/wants hands it over as a note in
the mind's inbox, as it does a memory want; `want_id`). When any is, Teeny
cannot ship it either, so it becomes one public GitHub issue (`file_issue`),
labelled for contributors, filed once per family (state/evolve/routed.jsonl).
Until 2026-11-24 the first kind went to the builder, retired that day.

Moltbook's code is left alone (the operator, 2026-11-25): a finding that is
the moltbook hand's refusal, or that would go to code and is a moltbook
playbook's, is IGNORED (`ignored`): never proposed on, routed or filed. It is
told by the hand the check's family or the playbook's own `output` names,
never by the word in a text.

Only counts, Teeny's own candidate titles and the hands' own words (already
scrubbed by evolve_findings) reach a brief or an issue; never a stranger's
text, never a secret.

Frozen with bin/: a candidate cannot change what is routed or where.
"""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

import evolve_findings
import evolve_outcomes
import frozen

Json = dict[str, Any]

ROUTABLE = ("refusals", "act_failed", "guard_holds")
#: The verdicts of a candidate that was built and judged: a prompt-level try.
ATTEMPTED = ("opened", "parked", "rejected", "regressed", "pr-error", "inconclusive", "unevaluated", "duplicate",
             "flagged", "revising", "lost")
#: Outcomes of a merged try that did not help.
FLAT = ("no-effect", "no-data", "hurt")
MIND, ISSUE = "mind", "issue"
#: Where a want went before the builder was retired (2026-11-24): read back
#: from old filings only.
BUILDER = "builder"
#: Labels a public issue asks contributors with, when the repository has them.
CONTRIBUTOR_LABELS = ("help wanted",)
#: The want id prefix of a routed finding (`^w:[a-z0-9:_-]{1,60}$`).
WANT_PREFIX = "w:f:"
BRIEF_CHARS = 2000
MAX_TRIED = 5
_HAND = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
#: Moltbook's code: the hand, its package, their tests, the box's tool.
MOLTBOOK = "moltbook"
MOLTBOOK_CODE = ("hands/moltbook", "hands/lib/teeny_hands/moltbook", "hands/tests/moltbook",
                 "hands/tests/test_moltbook", "bin/moltbook-")
#: Where a finding about it stands (`bin/evolve status`).
IGNORED = "ignored: moltbook code"


def is_moltbook_code(path: str) -> bool:
    return path.startswith(MOLTBOOK_CODE)


def _hand_of(check: Mapping[str, Any]) -> str:
    """The hand that refused, as the family names it: '*' when the check
    names no verb or playbook."""
    verb, book = str(check.get("verb") or ""), str(check.get("playbook") or "")
    if verb:
        return verb.rsplit("_", 1)[-1]
    if book:
        return book.split("-", 1)[0]
    return "*"


def family_of(check: Any) -> str | None:
    """The family of a check (a finding's, or a learning candidate's)."""
    if not isinstance(check, Mapping):
        return None
    kind = check.get("kind")
    if kind == "refusals" and check.get("rule"):
        return f"refusals:{_hand_of(check)}:{check['rule']}"
    if kind == "act_failed" and check.get("playbook"):
        return f"act_failed:{check['playbook']}"
    if kind == "guard_holds" and check.get("category"):
        return f"guard_holds:{check.get('surface') or '*'}:{check['category']}"
    return None


def _same(mine: str, other: str | None) -> bool:
    if other is None:
        return False
    a, b = mine.split(":"), other.split(":")
    return len(a) == len(b) and all(x == y or y == "*" for x, y in zip(a, b))


def _entry_family(entry: Mapping[str, Any]) -> str | None:
    learning = entry.get("learning") if isinstance(entry.get("learning"), Mapping) else {}
    if evolve_findings.is_finding(learning):
        return family_of(learning.get("check")) or family_of(entry.get("check"))
    return family_of(entry.get("check"))


def attempts(entries: Mapping[str, Json], item: Any) -> list[Json]:
    """The prompt-level candidates on ITEM's family, oldest first."""
    mine = family_of(getattr(item, "check", None))
    if mine is None:
        return []
    out = [e for e in entries.values() if e.get("verdict") in ATTEMPTED and isinstance(e.get("learning"), Mapping)
           and ((e["learning"].get("id") == item.id) or _same(mine, _entry_family(e)))]
    return sorted(out, key=lambda e: (str(e.get("at") or ""), e.get("gen") or 0, e.get("cand") or 0))


def decide(entries: Mapping[str, Json], item: Any, after: int, spans: Iterable[int]) -> Json | None:
    """Why ITEM goes to code, or None while prompts may still fix it."""
    if after <= 0 or getattr(item, "kind", None) not in ROUTABLE or evolve_findings.is_growth(item):
        return None
    tried = attempts(entries, item)
    if getattr(item, "cause", None):
        return {"family": family_of(item.check), "tried": len(tried), "why": item.cause}
    if len(tried) < after:
        return None
    levels = [e["learning"]["events"] for e in tried
              if e["learning"].get("id") == item.id and isinstance(e["learning"].get("events"), int)]
    fallen = bool(levels) and item.events < levels[-1]
    spans = tuple(spans)
    merged = [e for e in tried if e.get("merged_at")]
    outcomes = [evolve_outcomes.outcome_of(e, spans) for e in merged]
    flat = bool(merged) and all(o in FLAT for o in outcomes)
    if fallen and not flat:
        return None
    return {"family": family_of(item.check), "tried": len(tried),
            "why": (f"{len(tried)} prompt-level candidate(s) on its family and "
                    + ("its number has not fallen under where it stood at the latest of them"
                       if not fallen else "every one that merged scored " + "/".join(sorted(set(outcomes)))))}


# --- what a fix would touch -----------------------------------------------------------------------------


def _head(tree: Path, book: str) -> Json:
    try:
        text = (tree / "brain" / "playbooks" / f"{book}.md").read_text(encoding="utf-8")
        return tomllib.loads(text.split("+++", 2)[1]) if text.startswith("+++") else {}
    except (OSError, ValueError, IndexError):
        return {}


def _output_hand(head: Mapping[str, Any]) -> str:
    output = head.get("output")
    return output.split()[0].split(":", 1)[1] if isinstance(output, str) and output.startswith("hand:") else ""


def _called_hands(tree: Path, book: str) -> list[str]:
    """The hands a playbook's front matter calls (inputs `run = "hands/<h>
    ..."`, output `hand:<h> ...`)."""
    head = _head(tree, book)
    names = []
    for i in head.get("inputs") or []:
        run = i.get("run") if isinstance(i, Mapping) else None
        first = run.split()[0] if isinstance(run, str) and run.split() else ""
        if first.startswith("hands/"):
            names.append(first.split("/", 1)[1])
    names.append(_output_hand(head))
    return [n for n in dict.fromkeys(names) if _HAND.match(n)]


def _hand_paths(tree: Path, name: str) -> list[str]:
    return [p for p in (f"hands/{name}", f"hands/lib/teeny_hands/{name}") if (tree / p).exists()]


def ignored(item: Any, tree: Path, routed: bool) -> bool:
    """Whether ITEM is Moltbook's code to fix, which is left alone: a refusal
    by the moltbook hand, whoever asked (its words could only write around
    the refusal), or a failing playbook of that hand's once it is ROUTED
    (until then its failures are its words' to fix)."""
    book = str(item.check.get("playbook") or "")
    if book:
        mine = _output_hand(_head(tree, book)) == MOLTBOOK
    else:
        mine = _hand_of(item.check) == MOLTBOOK
    return mine and (item.kind == "refusals" or (item.kind == "act_failed" and routed))


def code_paths(item: Any, tree: Path) -> list[str]:
    """The code a fix to ITEM would touch, besides the words it points at;
    never Moltbook's."""
    check = item.check
    if item.kind == "refusals":
        hand = _hand_of(check)
        paths = _hand_paths(tree, hand) if _HAND.match(hand) else []
        return paths or ["bin/act" if check.get("playbook") else "bin/lib/tick.py"]
    if item.kind == "act_failed":
        paths = [p for h in _called_hands(tree, check["playbook"]) for p in _hand_paths(tree, h)
                 if not is_moltbook_code(p)]
        return paths or ["bin/act"]
    return ["hands/guard", "hands/lib/teeny_hands/guard"]


def destination(item: Any, tree: Path, patterns: Sequence[str]) -> tuple[str, list[str], list[str]]:
    """(mind | issue, the code paths, the frozen ones among them)."""
    paths = code_paths(item, tree)
    stuck = [p for p in paths if frozen.frozen_pattern(p, patterns)]
    return (ISSUE if stuck else MIND), paths, stuck


def patterns(tree: Path) -> list[str]:
    try:
        return frozen.parse_patterns((tree / "evals" / "frozen.txt").read_text(encoding="utf-8"))
    except OSError:
        return ["**"]  # no list: everything is frozen, so nothing goes to the mind by mistake


# --- the briefs -------------------------------------------------------------------------------------------


def want_id(family: str) -> str:
    """A routed family's want id: `w:f:` and the family (a-z 0-9 : _ -, at
    most 60 after `w:`)."""
    return (WANT_PREFIX + re.sub(r"[^a-z0-9:_-]", "-", family.lower()))[:62]


def _tried_lines(tried: list[Json], spans: Iterable[int]) -> list[str]:
    spans = tuple(spans)
    out = []
    for e in tried[-MAX_TRIED:]:
        title = evolve_findings.scrub(e.get("title") or "untitled", 100)
        if e.get("merged_at"):
            fate = f"merged, scored {evolve_outcomes.outcome_of(e, spans)}"
        else:
            fate = str(e.get("verdict"))
        level = (e.get("learning") or {}).get("events")
        out.append(f"- {e.get('id')} ({fate}{f', at {level} events' if isinstance(level, int) else ''}): {title}"
                   + (f" ({e['pr_url']})" if e.get("pr_url") else ""))
    return out


def brief(family: str, items: list[Any], tried: list[Json], paths: list[str], stuck: list[str],
          spans: Iterable[int], *, issue: bool) -> tuple[str, str]:
    """The title and the text of a routed family's want or issue: fixed
    words, the findings' own (scrubbed) statements, the numbers, the tries."""
    lead = items[0]
    total = sum(f.events for f in items)
    files = sorted({p for f in items for p in f.files})
    title = (f"Teeny keeps hitting `{family}`: {total} events in {lead.days} days"
             + (f" after {len(tried)} prompt fixes" if tried else ""))[:120]
    cause = str(getattr(lead, "cause", None) or "")
    lines = ([] if issue else [f"want: {want_id(family)}", ""]) + [
        f"bin/evolve counts `{family}` in Teeny's own ledgers (no model read them), and "
        f"{cause or 'prompt rules have not fixed it'}, so this is a code change{' Teeny cannot make itself' if issue else ''}.",
        "",
        "## the findings",
        *[f"- [{f.id}] {f.events} over {f.days} days: {evolve_findings.scrub(f.text, 420)}" for f in items],
        "",
        "## prompt-level fixes already tried",
        *(_tried_lines(tried, spans) or [f"- none: {cause}"]),
        "",
        "## where",
        f"- the words: {', '.join(files) or 'none'}",
        f"- the code: {', '.join(paths)}" + (f" (frozen: {', '.join(stuck)})" if stuck else ""),
        "",
        "## done when",
        f"- `{evolve_outcomes.describe(lead.check)}` falls: the same count over the {lead.days} days after the "
        "merge against the days before. A fix makes the hand or the playbook check before it calls (or treat an "
        "answer that it is already done as done), with a test that reproduces the refusal.",
    ]
    if issue:
        lines += ["", "Teeny cannot change frozen paths (evals/frozen.txt) itself, so this is open for anyone to "
                  "pick up. The numbers come from Teeny's run logs; nothing here is a person's words."]
    else:
        lines += ["", "This is a fix, not a feature: no new hand is needed. Touch no frozen path."]
    return title, "\n".join(lines)[:BRIEF_CHARS]


def routes(entries: Mapping[str, Json], findings: Sequence[Any], after: int, spans: Iterable[int], tree: Path
           ) -> list[Json]:
    """Every routed family among FINDINGS, with its destination and brief."""
    spans = tuple(spans)
    pats = patterns(tree)
    grouped: dict[str, list[Any]] = {}
    for f in findings:
        if decide(entries, f, after, spans) is not None:
            grouped.setdefault(family_of(f.check) or f.id, []).append(f)
    out = []
    for family, items in grouped.items():
        items = sorted(items, key=lambda f: (-f.events, f.id))
        dest, paths, stuck = destination(items[0], tree, pats)
        tried = attempts(entries, items[0])
        title, text = brief(family, items, tried, paths, stuck, spans, issue=dest == ISSUE)
        out.append({"family": family, "to": dest, "findings": [f.id for f in items], "want": want_id(family),
                    "check": dict(items[0].check), "paths": paths, "frozen": stuck, "tried": [e["id"] for e in tried],
                    "title": title, "text": text})
    return out


# --- the issue ---------------------------------------------------------------------------------------------


def read_log(path: Path) -> list[Json]:
    out = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                value = json.loads(line)
            except ValueError:
                continue
            if isinstance(value, dict):
                out.append(value)
    except OSError:
        pass
    return out


def filed_issue(log: list[Json], family: str) -> Json | None:
    return next((r for r in reversed(log) if r.get("family") == family and r.get("issue")), None)


def file_issue(route: Json, repo: str, publisher: Any, log_path: Path, at: str) -> Json:
    """ROUTE's one public issue, never two: an issue already filed for its
    family is named, not filed again. The labels are asked for only
    (hands/gh adds them after the issue opens, and one the repository
    refuses leaves it unlabelled)."""
    log = read_log(log_path)
    seen = filed_issue(log, route["family"])
    if seen is not None:
        return {"family": route["family"], "issue": seen["issue"], "url": seen.get("url"), "already": True}
    record: Json = {"family": route["family"], "to": ISSUE, "findings": route["findings"], "at": at}
    try:
        try:
            opened = publisher.open_issue(repo, route["title"], route["text"], judge=True,
                                          labels=list(CONTRIBUTOR_LABELS))
        except TypeError:  # a publisher that takes no labels
            opened = publisher.open_issue(repo, route["title"], route["text"], judge=True)
        record.update(issue=opened.get("number"), url=opened.get("html_url"))
    except Exception as exc:  # noqa: BLE001 - the finding stays routed; the next run tries again
        record["error"] = evolve_findings.scrub(str(getattr(exc, "reason", None) or exc), 300)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, sort_keys=True) + "\n")
    return record


def where(route: Mapping[str, Any], log: list[Json], filed: list[Json], job_of: Callable[[Any], Any]) -> Json:
    """Where a routed family is now, for `bin/evolve status`: its issue, its
    want's note for the agentic mind, or (a filing from before 2026-11-24)
    its build request and job."""
    if route["to"] == ISSUE:
        seen = filed_issue(log, route["family"])
        if seen is None:
            return {"to": ISSUE, "said": "routed to an issue (not filed yet)"}
        return {"to": ISSUE, "issue": seen["issue"], "url": seen.get("url"),
                "said": f"routed to an issue (#{seen['issue']})"}
    wid = want_id(route["family"])
    mine = [f for f in filed if f.get("want") == wid]
    if not mine:
        return {"to": MIND, "said": "routed to the agentic mind (not handed over yet)"}
    if mine[-1].get("note"):
        return {"to": MIND, "note": mine[-1]["note"], "said": f"handed to the agentic mind ({mine[-1]['note']})"}
    request = mine[-1].get("request_id")
    job = job_of(request)
    return {"to": BUILDER, "request": request, **({"job": job} if job is not None else {}),
            "said": f"routed to builder (job {job})" if job is not None else
            f"routed to builder (request {request}, not picked yet)"}
