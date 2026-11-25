"""Selection pressure for bin/evolve (docs/evolve.md, "6. Publish" and
"Lessons"): what a candidate must show before it opens a pull request, and
what the archive's merged changes say about each kind of change.

The 2026-11-22 audit of 22 generations: of the 12 pull requests a pairwise
eval measured, 11 scored "no detectable difference"; the other 7 no eval
task reached. All 19 were opened and merged, because the bar was "not
measurably worse". Their 2-day outcomes: worked 2, no-effect 3, hurt 1,
no-data 5, and 8 had no check at all. So:

- `unproven`: a candidate is published only when the eval finds it better
  than main (evals.signal "better"), or when its check is powered: the
  stream it counts held at least `need` events over the last horizon before
  now, so the horizon's verdict can come out worked, no-effect or hurt and
  not no-data (`power`). A `judged` check is a model's reading, never a
  count, and never powers anything. A falling or zero check on a change that
  also runs anything less (a cron job removed, turned off or thinned, a
  playbook deleted) is not powered either: running less would meet it.
- lessons: the merged changes grouped by KIND, a structural label drawn
  from the files the change touched and where the candidate came from
  (`kinds`), never from anything the proposer wrote; each with its outcome
  counts and examples (`lessons`). A kind with at least `min_judged` judged
  outcomes (worked, no-effect, hurt) and none worked is exhausted: another
  candidate of that kind is sent back, and parked, in code (`blocking`).

No model, no clock of its own, no I/O but what the caller hands in.
Frozen with bin/: a candidate cannot change how it is selected.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

import evolve_outcomes as eo

Json = dict[str, Any]

#: The prompt pages every turn of Teeny's reads: a rule written into one of
#: them is the same kind of change wherever in the page it lands, as a
#: bullet, a sentence or a paragraph.
PROMPT_PAGES = ("brain/SOUL.md", "brain/CONTEXT.md", "brain/DRIVE.md", "brain/CHARTER.md", "brain/READER.md")
#: Each shape a change can have, by what it touches, in the order a kind
#: lists them.
SHAPES = {
    "prompt-rule": "a rule written into a prompt page every turn reads (" + ", ".join(PROMPT_PAGES) + ")",
    "playbook": "an edit to a playbook (brain/playbooks/)",
    "skill": "an edit to a skill (brain/skills/)",
    "agent": "an edit to an agent's prompt (brain/agents/)",
    "schedule": "a cron job change (brain/cron.toml)",
    "brain-other": "another file under brain/ (narrative, peers lists, assets, calendar)",
    "hand": "a hand's code or tests (hands/)",
    "code": "other code (bin/, evals/ and the rest)",
    "docs": "documentation alone (docs/)",
}
SOURCES = {"learning": "from a learning of MEMORY.md", "finding": "from a finding the run logs counted"}
#: What a lesson says of its kind.
EXHAUSTED, BUILD_ON, UNMEASURED, UNTESTED = "exhausted", "build-on", "cannot-be-measured", "too-few"
#: The examples a lesson carries, and how much of each title.
MAX_EXAMPLES, MAX_TITLE = 2, 100
#: The signal bin/eval's compare gives a candidate measurably better than
#: main (evals.signal).
BETTER = "better"
UNPROVEN = "unproven"
NOTE = ("What each kind of change of yours did once merged, counted by the code from the files it touched (not from "
        "anything you wrote about it). Data, not instructions. An exhausted kind is rejected: make a different kind "
        "of change. Build on a kind that worked. A kind whose checks come back no-data needs a check with more "
        "events, or a different kind.")


# --- kinds ------------------------------------------------------------------------------------------------


def shape(path: str) -> str | None:
    """The shape of a change to PATH; None for what is no change of Teeny's
    (the HYPOTHESIS.md note)."""
    if path in PROMPT_PAGES:
        return "prompt-rule"
    if path == "HYPOTHESIS.md":
        return None
    for prefix, name in (("brain/playbooks/", "playbook"), ("brain/skills/", "skill"), ("brain/agents/", "agent"),
                         ("hands/", "hand"), ("docs/", "docs")):
        if path.startswith(prefix):
            return name
    if path == "brain/cron.toml":
        return "schedule"
    if path.startswith("brain/"):
        return "brain-other"
    return "code"


def shapes(paths: Iterable[str]) -> list[str]:
    """Every shape the change touches, in SHAPES order. Docs go with a change
    (the brief asks for them), so they are a shape only alone."""
    got = {s for s in (shape(str(p)) for p in paths) if s}
    if len(got) > 1:
        got.discard("docs")
    return [s for s in SHAPES if s in got]


def source(learning: Any) -> str:
    """Where a candidate came from: a finding (evolve_findings) or a learning."""
    import evolve_findings  # here, not at the top: evolve_findings imports evolve_outcomes too

    return "finding" if evolve_findings.is_finding(dict(learning) if isinstance(learning, Mapping) else learning) \
        else "learning"


def kinds(paths: Iterable[str], learning: Any) -> list[str]:
    """The kinds of a change: `<shape>/<source>` for every shape it touches.
    A change is every kind it touches, so a rule for a prompt page padded
    with a line elsewhere is still a prompt rule."""
    where = source(learning)
    return [f"{s}/{where}" for s in shapes(paths)]


def describe_kind(kind: str) -> str:
    name, _, where = kind.partition("/")
    return f"{SHAPES.get(name, name)}, {SOURCES.get(where, where)}"


# --- lessons ----------------------------------------------------------------------------------------------


def _example(entry: Mapping[str, Any], outcome: str) -> Json:
    title = " ".join(str(entry.get("title") or "").split())
    return {"id": entry.get("id"), "title": title[:MAX_TITLE], "outcome": outcome}


def lessons(entries: Mapping[str, Json], spans: Iterable[int], kinds_of: Any, min_judged: int) -> list[Json]:
    """One lesson per kind of the merged changes (KINDS_OF(entry) gives an
    entry's kinds, or None when they cannot be told): how many merged, each
    outcome (evolve_outcomes.outcome_of), up to MAX_EXAMPLES examples, what
    it says, and whether it blocks another of its kind. Exhausted ones first,
    then the ones to build on, then the unmeasured, then the rest; each by
    how many merged."""
    spans = tuple(spans)
    merged = sorted((e for e in entries.values() if e.get("merged_at") and e.get("id") != "main"),
                    key=lambda e: (str(e["merged_at"]), str(e.get("id"))))
    by: dict[str, list[tuple[Json, str]]] = {}
    for e in merged:
        got = eo.outcome_of(e, spans)
        for kind in kinds_of(e) or []:
            by.setdefault(kind, []).append((e, got))
    out = []
    for kind, rows in by.items():
        counts = {v: 0 for v in (*eo.VERDICTS, eo.PENDING, eo.NO_CHECK)}
        for _, got in rows:
            counts[got] = counts.get(got, 0) + 1
        judged = counts["worked"] + counts["no-effect"] + counts["hurt"]
        # The newest that worked, then the newest of the rest that was scored.
        newest = list(reversed(rows))
        picks = [r for r in newest if r[1] == "worked"][:1]
        picks += [r for r in newest if r[1] != "worked" and r[1] not in (eo.PENDING,) and r not in picks]
        picks = (picks or newest)[:MAX_EXAMPLES]
        blocked = min_judged > 0 and judged >= min_judged and counts["worked"] == 0
        if blocked:
            says, what = EXHAUSTED, (f"{judged} judged outcome(s), none worked: bin/evolve rejects another change of "
                                     "this kind. Make a different kind of change")
        elif counts["worked"]:
            says, what = BUILD_ON, f"worked {counts['worked']} of {judged} judged: a kind to build on"
        elif min_judged > 0 and counts["no-data"] >= min_judged:
            says, what = UNMEASURED, (f"its checks came back no-data {counts['no-data']} time(s): this kind cannot "
                                      "be measured as it was checked. Attach a check with more events, or change kind")
        else:
            says, what = UNTESTED, "too few judged outcomes to say yet"
        out.append({"kind": kind, "what": describe_kind(kind), "merged": len(rows),
                    **{k: counts[k] for k in ("worked", "no-effect", "hurt", "no-data", eo.PENDING, eo.NO_CHECK)},
                    "examples": [_example(e, got) for e, got in picks], "says": says, "lesson": what,
                    "blocks": blocked})
    order = {EXHAUSTED: 0, BUILD_ON: 1, UNMEASURED: 2, UNTESTED: 3}
    return sorted(out, key=lambda l: (order[l["says"]], -l["merged"], l["kind"]))


def blocking(lessons_: Iterable[Mapping[str, Any]] | None, kinds_: Iterable[str]) -> Mapping[str, Any] | None:
    """The first exhausted lesson whose kind the change is, or None."""
    mine = set(kinds_)
    return next((l for l in lessons_ or [] if isinstance(l, Mapping) and l.get("blocks") and l.get("kind") in mine),
                None)


def lesson_line(lesson: Mapping[str, Any]) -> str:
    counts = ", ".join(f"{lesson.get(k, 0)} {k}" for k in ("worked", "no-effect", "hurt", "no-data"))
    rest = ", ".join(f"{lesson[k]} {k}" for k in (eo.PENDING, eo.NO_CHECK) if lesson.get(k))
    examples = "; ".join(f"{x.get('id')} \"{x.get('title')}\" ({x.get('outcome')})" for x in lesson.get("examples") or [])
    return (f"`{lesson.get('kind')}` ({lesson.get('what')}): {lesson.get('merged')} merged: {counts}"
            + (f" ({rest})" if rest else "") + (f". e.g. {examples}" if examples else "")
            + f". {str(lesson.get('says')).upper()}: {lesson.get('lesson')}.")


def blocked_reason(lesson: Mapping[str, Any]) -> str:
    """Why a change of an exhausted kind is sent back: the lesson, named."""
    return f"lesson `{lesson.get('kind')}` is exhausted: " + lesson_line(lesson)


def lessons_text(lessons_: Iterable[Mapping[str, Any]] | None) -> str | None:
    """The brief's lead: the lessons, worst first, as lines."""
    rows = [l for l in lessons_ or [] if isinstance(l, Mapping)]
    if not rows:
        return None
    return ("## lessons from your merged changes, by kind\n\n" + NOTE + "\n\n"
            + "\n".join(f"- {lesson_line(l)}" for l in rows))


# --- power ----------------------------------------------------------------------------------------------


def need(floor: int, min_events: int, p: float) -> int:
    """The fewest events in the window before a check's verdict can come out
    anything but no-data, and a whole move of them pass the test at P: a
    fall to nothing of N events has a tail of 2^-N, and the test wants it
    strictly under P (evolve_outcomes.verdict)."""
    whole = 1
    while 0 < p < 1 and 2.0 ** -whole >= p:
        whole += 1
    return max(int(floor), int(min_events), whole)


def power(check: Any, before: int | None, days: int, needed: int, cuts: Iterable[str] = ()) -> Json:
    """Whether CHECK is powered: BEFORE is its count over the DAYS before now
    (evolve_outcomes.count; None when its ledger cannot be read), NEEDED the
    fewest it takes (`need`), CUTS what the change runs less."""
    out: Json = {"days": int(days), "need": int(needed)}
    if not isinstance(check, Mapping) or check.get("kind") not in eo.KINDS:
        return {**out, "powered": False, "why": "the change has no check the code can count"}
    if check["kind"] == "judged":
        return {**out, "powered": False,
                "why": "a judged check is a model's reading of samples, not a count: it proves nothing"}
    cuts = list(cuts)
    if cuts and check.get("direction") in ("fall", "zero"):
        return {**out, "powered": False, "cuts": cuts,
                "why": f"the change also runs less ({'; '.join(cuts)}), so a count that falls could fall because "
                       "its source ran less"}
    if before is None:
        return {**out, "powered": False, "why": "the ledger it counts cannot be read now"}
    out["before"] = int(before)
    if before < needed:
        return {**out, "powered": False,
                "why": f"{eo.describe(check)} counted {before} event(s) in the last {days} day(s); its {days}-day "
                       f"verdict needs at least {needed}, so history says it would come out no-data"}
    return {**out, "powered": True,
            "why": f"{eo.describe(check)} counted {before} event(s) in the last {days} day(s), at least {needed}"}


def unproven(ev: Mapping[str, Any] | None, check_power: Any, *, growth: bool = False,
             check: Mapping[str, Any] | None = None) -> str | None:
    """Why a candidate that passed its eval may not be published, or None:
    better than main, or its check powered; for a GROWTH one, only a reach
    CHECK whose baseline holds the items it takes (evolve_reach.power)."""
    ev = ev or {}
    powered = isinstance(check_power, Mapping) and bool(check_power.get("powered"))
    if growth:
        # A draft judged better is no reach: only its own thick stream is.
        if powered and "items" in check_power and (check or {}).get("kind") == "reach":
            return None
        return f"{UNPROVEN}: a growth change is opened on X reach alone: " + (
            "its check is not a reach check with a baseline of items on record" if powered
            else str((check_power or {}).get("why") or "it has no reach check on record"))
    if powered or (ev.get("verdict") == "pass" and ev.get("signal") == BETTER):
        return None
    measured = ("no eval task reaches this change" if ev.get("verdict") == "unmeasurable"
                else f"the eval found {ev.get('signal') or 'no detectable difference'} against main")
    why = (check_power.get("why") if isinstance(check_power, Mapping) else
           "it has no check on record that history says can be scored (it waited from before this rule)")
    return f"{UNPROVEN}: {measured}, and its check is not powered: {why}"


def unproven_findings(why: str, check_power: Any) -> list[Json]:
    """What the next round is told: why, and the two ways through."""
    p = check_power if isinstance(check_power, Mapping) else {}
    days, needed = p.get("days") or 2, p.get("need") or 4
    return [{"file": "", "problem": str(why)[:500],
             "fix": (f"attach a check the code counts whose stream had at least {needed} events in the last {days} "
                     "day(s) (the evidence's outcomes and findings show which streams are busy), aimed at what your "
                     "change moves; or make a change the eval can tell apart from main. A judged check never "
                     "counts, and running anything less does not either")}]


def proof_text(cand: Mapping[str, Any]) -> str:
    """What a pull request says of why it was published."""
    ev = cand.get("eval") or {}
    p = cand.get("check_power")
    if ev.get("verdict") == "pass" and ev.get("signal") == BETTER:
        return "**Published because** the eval found it better than main."
    if isinstance(p, Mapping) and p.get("powered"):
        return f"**Published because** its check is powered: {p.get('why')}."
    return ""
