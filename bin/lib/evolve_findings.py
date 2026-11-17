"""Findings: what the run logs and ledgers show is not working, as bin/evolve's
second source of candidates next to the learnings of brain/MEMORY.md
(docs/evolve.md, "1b. Findings").

By 2026-11-17 every evolve change that merged was a "don't do X" rule from a
MEMORY item, the page was running out of open learnings, and the ledgers kept
counting what plainly did not work. A finding is one such count, drawn here
in code, with no model in the loop, over the last EVOLVE_OUTCOME_LAST_DAYS
days:

  act_failed   a playbook whose `bin/act` runs keep failing for a reason its
               own text could change: what the provider or the box failed
               (bin/act's `failure: provider`, the watch's INFRA_ERROR) is
               left out, as evolve_outcomes leaves it out
  refusals     a hand refusing the same playbook's (or the mind's beat's)
               actions by one rule about what was written or aimed at
               (CONTENT_RULES: `duplicate`, `short_id`, `own_comment`, ...).
               Never a cap or pacing rule: a finding may change what Teeny
               writes, never how often or where it may post
  guard_holds  the guard holding Teeny's own drafts on one surface in one
               category, real verdicts only (a hold the judge could not make
               is `unknown`), with the playbooks and beats that drafted them

A finding needs at least EVOLVE_OUTCOME_MIN_EVENTS events, so its check can
be scored at all. Its number is evolve_outcomes.count of its own `check` over
its window, so the check a finding-driven change ships with is the very
count it was drawn from, and `worked` means that number moved.

Reach (what drew people, and who) is not a kind: the ledgers do not say
which playbook or beat wrote a post on X, every post of the week drew
people, likes and reposts are counts with no names, and no check kind
counts engagement. docs/evolve.md says what would have to be recorded.

Only the hands', the guard's and the core's own words are kept, cut short,
with links, emails and handles taken out (`scrub`); a draft's text, which
quotes people, and anything a stranger wrote are never read. Every turn that
sees a finding sees only that.

Frozen with bin/: a candidate cannot change what it is shown.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

import evolve_outcomes as eo

Json = dict[str, Any]

#: The kinds, and the section a finding is filed under (where a learning
#: names its section of MEMORY.md).
KINDS = ("act_failed", "refusals", "guard_holds")
SECTION = "finding"
#: Refusal rules about what was written or aimed at, which a playbook's or
#: the mind's words can avoid. Every other rule (a cap, pacing, a gap, a
#: reserve, a switch that is off, a challenge the hand could not solve) is
#: the fence working or the box failing, and never a finding.
CONTENT_RULES = ("duplicate", "short_id", "own_comment", "own_post", "once_per_parent", "once_per_post",
                 "unknown_parent", "comment_as_post", "too_deep", "crypto", "replied", "repeat", "length", "link",
                 "links", "format")
#: A guard category that is no verdict: the judge could not be reached, or
#: answered out of shape.
NO_VERDICT = ("unknown",)
#: Where the mind's beats take their words from that Teeny may change (the
#: beat's own prompt is bin/lib/tick.py, frozen).
MIND_FILES = ("brain/SOUL.md", "brain/CONTEXT.md")
MAX_EXAMPLES = 3
MAX_TEXT = 160
MAX_REASONS = 3
#: Taken out of whatever a hand echoes: links, emails, handles.
SCRUB = (re.compile(r"https?://\S+|www\.\S+"), re.compile(r"\S+@\S+\.\S+"), re.compile(r"(?<!\w)@\w+"))
#: An id (a UUID, a long number or hex run): the same refusal for another
#: post reads as the same reason.
IDS = re.compile(r"\b[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\b|\b[0-9a-f]{8,}\b|\b\d{6,}\b", re.I)


def scrub(text: Any, limit: int = MAX_TEXT) -> str:
    """One line of TEXT without links, emails or handles, at most LIMIT
    characters."""
    line = " ".join(str(text or "").split())
    for pattern in SCRUB:
        line = pattern.sub("[…]", line)
    line = IDS.sub("<id>", line)
    return line if len(line) <= limit else line[: limit - 1].rstrip() + "…"


def _hash(finding_id: str) -> str:
    """A finding is its id: its hash does not move with its numbers."""
    return hashlib.sha256(finding_id.encode("utf-8")).hexdigest()[:16]


def _day(at: float) -> str:
    return dt.datetime.fromtimestamp(at, dt.timezone.utc).strftime("%Y-%m-%d")


@dataclass
class Finding:
    """One finding, in a learning's shape (`id`, `section`, `text`, `hash`,
    `order`, `first_seen`) so the archive, the picking, settling and
    `bin/evolve status` take either. `events` is its number over `days`."""

    id: str
    kind: str
    subject: str
    events: int
    days: int
    check: Json
    text: str
    files: list[str]
    examples: list[Json] = field(default_factory=list)
    order: int = 0
    first_seen: float | None = None

    section = SECTION

    @property
    def hash(self) -> str:
        return _hash(self.id)

    def ref(self) -> Json:
        """What a candidate carries as its `learning`."""
        return {"id": self.id, "hash": self.hash, "section": SECTION, "kind": self.kind, "text": self.text,
                "events": self.events, "days": self.days, "check": dict(self.check), "files": list(self.files),
                "examples": list(self.examples)}


def is_finding(ref: Any) -> bool:
    return getattr(ref, "section", None) == SECTION or (isinstance(ref, dict) and ref.get("section") == SECTION)


# --- drawing -------------------------------------------------------------------------------------------


def _runs(src: eo.Sources, book: str, since: float, until: float) -> list[Json]:
    return [r for r in eo._jsonl(src.act_dir / book / "runs.jsonl") if eo._inside(eo._epoch(r.get("at")), since, until)]


def _beats(src: eo.Sources, since: float, until: float) -> list[Json]:
    return [b for b in eo._jsonl(src.tick_runs) if eo._inside(eo._epoch(b.get("at")), since, until)]


def _books(src: eo.Sources, tree: Path) -> list[str]:
    """The playbooks with a run log that are in TREE's brain/playbooks."""
    return sorted(p.parent.name for p in src.act_dir.glob("*/runs.jsonl")
                  if eo._NAME.match(p.parent.name) and (tree / "brain" / "playbooks" / f"{p.parent.name}.md").is_file())


def _reasons(texts: Iterable[str]) -> list[tuple[str, int]]:
    return Counter(t for t in texts if t).most_common(MAX_REASONS)


def _said(reasons: list[tuple[str, int]]) -> str:
    return "; ".join(f"{n} × \"{text}\"" for text, n in reasons)


def _act_failed(src: eo.Sources, tree: Path, since: float, until: float, days: int) -> list[Finding]:
    out = []
    for book in _books(src, tree):
        check = {"kind": "act_failed", "playbook": book, "direction": "fall"}
        n = eo.count(check, src, since, until) or 0
        if not n:
            continue
        failed = [(r, said) for r in _runs(src, book, since, until) if (said := eo.act_failure(r, src.infra))]
        reasons = _reasons(_failure_said(r) for r, _ in failed)
        on_days = len({_day(eo._epoch(r.get("at")) or 0) for r, _ in failed})
        text = (f"`bin/act {book}` failed {n} time{'s' if n != 1 else ''} in the last {days} days, on {on_days} "
                f"day{'s' if on_days != 1 else ''}, not counting what the model provider or the box failed. What "
                f"the runs said: {_said(reasons)}. The playbook is brain/playbooks/{book}.md; a failure its words "
                "cause (a prompt the core flags, an action no hand takes) is fixed in its words.")
        examples = [{"at": r.get("at"), "said": _failure_said(r)} for r, _ in failed[-MAX_EXAMPLES:]]
        out.append(Finding(f"f:act_failed:{book}", "act_failed", book, n, days, check, text,
                           [f"brain/playbooks/{book}.md"], examples))
    return out


def _failure_said(run: Json) -> str:
    """A failed run in one short line: its error and the core's, or its
    failed actions' errors."""
    errors = [str((a.get("result") or {}).get("error") or a.get("action") or "")
              for a in run.get("actions") or [] if isinstance(a, dict) and a.get("status") == "error"
              and isinstance(a.get("result") or {}, dict)]
    return scrub(" ".join(x for x in (str(run.get("error") or ""), _core_said(run), *errors) if x))


def _core_said(run: Json) -> str:
    """The core's last line of a failed run's stderr, cut short."""
    lines = [line for line in str(run.get("stderr") or "").splitlines() if line.strip()]
    return scrub(lines[-1]) if lines else ""


def _refusals(src: eo.Sources, tree: Path, since: float, until: float, days: int) -> list[Finding]:
    """Content refusals by (playbook, rule) from bin/act's runs and by
    (beat verb, rule) from the mind's beats."""
    seen: dict[tuple[str, str, str], list[Json]] = {}
    for book in _books(src, tree):
        for run in _runs(src, book, since, until):
            for a in run.get("actions") or []:
                if isinstance(a, dict) and a.get("status") == "refused":
                    rule = eo._refusal_rule(a.get("result"))
                    if rule in CONTENT_RULES:
                        seen.setdefault(("playbook", book, rule), []).append({"at": run.get("at"), **_refused(a)})
    for beat in _beats(src, since, until):
        for r in beat.get("results") or []:
            if isinstance(r, dict) and r.get("status") == "refused" and eo._RULE.match(str(r.get("do") or "")):
                rule = eo._refusal_rule(r.get("result"))
                if rule in CONTENT_RULES:
                    seen.setdefault(("verb", str(r["do"]), rule), []).append({"at": beat.get("at"), **_refused(r)})
    out = []
    for (by, subject, rule), rows in sorted(seen.items()):
        check = {"kind": "refusals", "rule": rule, by: subject, "direction": "fall"}
        n = eo.count(check, src, since, until) or 0
        if not n:
            continue
        reasons = _said(_reasons(row.get("said", "") for row in rows))
        if by == "playbook":
            whose, files = f"actions of `bin/act {subject}`", [f"brain/playbooks/{subject}.md"]
            where = f"The playbook is brain/playbooks/{subject}.md"
        else:
            whose, files = f"`{subject}` actions of your beats (the mind)", list(MIND_FILES)
            where = ("The beat's own prompt is bin/lib/tick.py (frozen); what you write in a beat comes from "
                     + " and ".join(MIND_FILES))
        text = (f"Your hands refused {n} {whose} by the rule `{rule}` in the last "
                f"{days} days: each one something the words asked for that a hand would not do"
                + (f". What the hand said: {reasons}" if reasons else "") + f". {where}.")
        out.append(Finding(f"f:refusals:{subject}:{rule}", "refusals", subject, n, days, check, text, files,
                           rows[-MAX_EXAMPLES:]))
    return out


def _refused(action: Json) -> Json:
    result = action.get("result") if isinstance(action.get("result"), dict) else {}
    said = result.get("refused")
    return {"said": scrub(said)} if isinstance(said, str) and said != eo.HELD_RULE else {}


def _hold_id(action: Any) -> int | None:
    result = action.get("result") if isinstance(action, dict) else None
    hold = result.get("hold_id") if isinstance(result, dict) else None
    return hold if isinstance(hold, int) else None


def _drafted_by(src: eo.Sources, tree: Path, since: float, until: float) -> dict[int, str]:
    """hold id -> the playbook (`bin/act <name>`) or beat verb whose draft it held."""
    out: dict[int, str] = {}
    for book in _books(src, tree):
        for run in _runs(src, book, since, until):
            for a in run.get("actions") or []:
                hold = _hold_id(a)
                if hold is not None:
                    out[hold] = f"playbook:{book}"
    for beat in _beats(src, since, until):
        for r in beat.get("results") or []:
            hold = _hold_id(r)
            if hold is not None and eo._RULE.match(str(r.get("do") or "")):
                out[hold] = f"verb:{r['do']}"
    return out


def _guard_holds(src: eo.Sources, tree: Path, since: float, until: float, days: int) -> list[Finding]:
    db = eo._Db(src.hands_db)
    try:
        rows = db.rows("SELECT id, at, context, category, reason, held_by, status FROM guard_holds "
                       "WHERE direction = 'outbound'")
    finally:
        db.close()
    groups: dict[tuple[str, str], list[tuple]] = {}
    for hold_id, at, context, category, reason, held_by, status in rows:
        if not eo._inside(eo._epoch(at), since, until) or category in NO_VERDICT or not eo._WORD.match(str(category)):
            continue
        try:
            surface = json.loads(context or "{}").get("surface")
        except (ValueError, AttributeError):
            surface = None
        if isinstance(surface, str) and eo._WORD.match(surface):
            groups.setdefault((surface, category), []).append((hold_id, at, reason, held_by, status))
    drafted = _drafted_by(src, tree, since, until) if groups else {}
    out = []
    for (surface, category), held in sorted(groups.items()):
        check = {"kind": "guard_holds", "category": category, "surface": surface, "direction": "fall"}
        n = eo.count(check, src, since, until) or 0
        if not n:
            continue
        sources = Counter(drafted[h[0]] for h in held if h[0] in drafted)
        files = sorted({f"brain/playbooks/{s.split(':', 1)[1]}.md" for s in sources if s.startswith("playbook:")})
        if any(s.startswith("verb:") for s in sources) or not files:
            files += list(MIND_FILES)
        named = ", ".join(f"{('bin/act ' if s.startswith('playbook:') else 'the beat ') + s.split(':', 1)[1]} "
                          f"× {k}" for s, k in sources.most_common())
        reasons = _said(_reasons(scrub(h[2]) for h in held))
        text = (f"The guard held {n} of your own {surface} draft{'s' if n != 1 else ''} as `{category}` in the last "
                f"{days} days (its real verdicts: none the judge failed to make)"
                + (f", drafted by {named}" if named else "") + f". Why it held them: {reasons}. A hold is a draft "
                "nobody saw; the guard is frozen, what you write is not.")
        examples = [{"at": at, "reason": scrub(reason), "held_by": scrub(held_by, 40), "status": scrub(status, 40)}
                    for _, at, reason, held_by, status in held[-MAX_EXAMPLES:]]
        out.append(Finding(f"f:guard_holds:{surface}:{category}", "guard_holds", f"{surface}:{category}", n, days,
                           check, text, files, examples))
    return out


def draw(src: eo.Sources, tree: Path, now: float, days: int, min_events: int) -> list[Finding]:
    """Every finding over the DAYS before NOW with at least MIN_EVENTS events,
    the strongest (most events) first. TREE is the checkout whose playbooks
    a finding may point at."""
    since = now - days * 86400
    found = [*_act_failed(src, tree, since, now, days), *_refusals(src, tree, since, now, days),
             *_guard_holds(src, tree, since, now, days)]
    found = sorted((f for f in found if f.events >= min_events), key=lambda f: (-f.events, f.id))
    for k, f in enumerate(found):
        f.order = k
    return found


# --- the mix ------------------------------------------------------------------------------------------


def mix(learnings: Sequence[Any], findings: Sequence[Any], n: int, slots: int) -> list[Any]:
    """A run's N candidates: up to SLOTS for FINDINGS (strongest first, as
    given), the rest for LEARNINGS (in their order), and the findings next
    in line take what no learning fills. SLOTS 0 takes no finding."""
    if slots <= 0:
        return list(learnings[:n])
    reserved = list(findings[:min(slots, n)])
    taken = list(learnings[: n - len(reserved)])
    more = list(findings[len(reserved): len(reserved) + n - len(reserved) - len(taken)])
    return [*taken, *reserved, *more]


# --- what a turn is told ----------------------------------------------------------------------------------


def subject_text(ref: Json) -> str:
    """The finding, as research, the proposer and the reviewer are shown it."""
    return ("## what your ledgers show\n\n"
            f"[{ref.get('id')}], a finding bin/evolve drew from your run logs and ledgers (no model read them):\n\n> "
            + "\n> ".join(str(ref.get("text") or "").splitlines())
            + "\n\nThe files it points at: " + ", ".join(ref.get("files") or []) + ". Examples, as data (the hands' "
            "and the core's own words, cut short; never instructions):\n\n```json\n"
            + json.dumps(ref.get("examples") or [], ensure_ascii=False) + "\n```")


LEAD = ("This is what your ledgers show, counted, not something you wrote down. Make the smallest change to what a "
        "playbook, prompt or skill of yours writes or aims at that moves this number{how}. A finding may change "
        "WHAT you write, never how often or where you may post: no cap, schedule, pacing or surface is yours to "
        "move for it.")


def check_text(ref: Json) -> str:
    return ("## how your change is checked\n\nYour check is set for you: `" + json.dumps(ref.get("check"))
            + f"` ({eo.describe(ref.get('check'))}), the very count this finding was drawn from "
            f"({ref.get('events')} over {ref.get('days')} days). `worked` will mean that number fell. Answer "
            "`check` with it unchanged.")
