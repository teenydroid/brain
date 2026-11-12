"""Teeny's end of day, and its midday pause (docs/architecture.md, 4a,
"Reflection").

    python3 bin/lib/reflect.py            # the night: the page and the diary
    python3 bin/lib/reflect.py --light    # midday: the page only

MEMORY.md is the page that rides in every prompt Teeny speaks from: the
persistence that survives a compaction, a restart and recall missing. Once
a night this brings it up to date from inside the day, item by item:

1. Reads the page and gives every item (a bullet, or a paragraph under a
   `## section`) a stable id, shown in the page as a `[m12]` prefix. Items
   that already have one keep it; the rest are numbered in page order from
   one past the highest id ever used (on the page or in the retired log), so
   the first run over an un-ID'd page migrates it the same way every time.
   The text before the first section is the page's own note and has no id.
2. Gathers what happened since the last reflection: the notes Teeny chose to
   keep (`remember`, in `mind_notes`), what it thought and did each beat
   (state/tick/runs.jsonl), the follow-ups still waiting, and the page with
   its ids.
3. Asks the `teeny` agent on its own thread (`teeny:mind`, so the day is
   still in context) for a list of operations on the page and a diary
   entry, as JSON: `add` (section, text), `edit` (id, text) and `retire`
   (id, reason). What no operation names stays as it is (ACE's delta
   updates, arXiv:2510.04618: a page rewritten whole each time collapses
   into thin summaries).
4. Checks the result (the shrink check): a reflection may retire at most
   MAX_RETIRES items, each with a reason, and may not take more than
   MAX_SHRINK of the page off (SHRINK_FLOOR_CHARS spares a young page). A
   result past either is refused: it is logged to state/reflect/refused.jsonl
   and stderr, the page stays as it was, and the result says `refused`.
   A page past prompts.MEMORY_MAX_CHARS (and longer than before) is not
   cut: the mind is asked to retire or merge items instead.
5. Keeps the old page in state/reflect/MEMORY-<stamp>.md, writes the new one
   to brain/MEMORY.md,
   moves each retired item (its id, section, text, reason and the time) to
   state/reflect/retired.jsonl, so nothing leaves the page unrecorded
   (Zep: invalidate, don't delete), writes the diary to
   state/diary/<date>.md (the day it looks back on: before 06:00 that is
   yesterday; on GOALS_WEEKDAY the page also carries the week's goals and
   their verdicts, written from the goal ledger by `goals_week`) and teaches
   it to the core's memory, then rebuilds the prompts
   so the next beat reads the new page (the agents set
   `refresh_prompt_on_resume`).

brain/MEMORY.md is the live page, never committed; the first reflection on
a checkout without one starts it from the committed brain/MEMORY.seed.md,
which is never written.

The JSON is found wherever it sits in the answer (in a fence, after a line
of prose). An answer that is not usable (no JSON, an unknown id, a retire
without a reason, a refused shrink, a page too long) is asked about once
more on the same thread, with the reason and a short reminder of the shape.

The whole page is still taken as a fallback, under the same shrink check:
the old JSON shape (`{"memory": "<the page>"}`), or, when the second answer
still carries no JSON, an answer that is the page itself in markdown (one
has been seen live: the page, its title written as a bare "MEMORY.md"; the
newer answer is tried first). Its items keep the id they carry, or the id
of the old item with the same text; an old item it leaves off counts as
retired, with the reason "left off a whole-page answer". In markdown, a
trailing "## diary" section or "diary:" line is the diary, and without one
the night writes none and says so (`diary_missing`). Anything else changes
nothing, and neither does a failed turn: the next night tries again with
everything since the last reflection that worked.

`--light` (the pinned `reflect-midday` job, 14:00) does steps 1-5 for the
page alone, so what the morning brought is in every prompt by the
afternoon: no diary, nothing taught to the core. It reviews what came since
the later of the last night and the last midday, and stamps its own
state/reflect/light.at; only the night moves state/reflect/last.at, so the
night still looks back on the whole day (over a page midday already
brought up to date).

The beats come from state/tick/runs.jsonl: every one that thought, the
quiet ones (`kind: quiet`) included, with what it did and its own
`journal` lines; the no-ops (`idle`: asleep, busy) are passed over.
"""

from __future__ import annotations

import copy
import datetime as dt
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

CODE = Path(__file__).resolve().parents[2]
ROOT = Path(os.environ.get("TEENY_ROOT") or CODE)
BRAIN = Path(os.environ.get("OPENHUMAN_WORKSPACE") or ROOT / "brain")
sys.path.insert(0, str(CODE / "hands" / "lib"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import prompts  # noqa: E402
from teeny_hands import hand, stimuli  # noqa: E402

#: The most of the day's beats the message carries (newest kept): a beat
#: every five minutes through the waking hours is about 216.
MAX_BEATS = 240
#: A beat's thought, beyond which it is cut in the message.
MAX_THOUGHT_CHARS = 300
DEFAULT_TZ = "Asia/Kolkata"

#: The shrink check. A day closes a few threads and opens about as many, so
#: a healthy reflection leaves the page about the size it found it; losing
#: more than this share of the page in one sitting is the collapse ACE
#: describes, not curation.
MAX_SHRINK = 0.30
#: A loss smaller than this is never refused: on a young page of a few
#: bullets, retiring one kept promise is already a third of it.
SHRINK_FLOOR_CHARS = 500
#: The most items one reflection may retire. A full page (12k characters,
#: about 80 bullets) loses at most a tenth of itself in one sitting, while a
#: busy day can still close its threads; anything left over waits a
#: reflection (midday and night come twelve hours apart).
MAX_RETIRES = 8
#: What a page crowding the limit is told: merging keeps what each item
#: said, where truncation would cut blind.
CROWDED_AT = 0.9

Json = dict[str, Any]


# --- the page as items ----------------------------------------------------

#: The page's title, however the model wrote it: "# MEMORY.md", "MEMORY.md",
#: "#MEMORY.md", "## MEMORY.md".
_TITLE = re.compile(r"\A\s*#*\s*MEMORY\.md[ \t]*(?:\n|\Z)", re.IGNORECASE)
_SECTION_HEAD = re.compile(r"^##(?!#)[ \t]+(.+?)[ \t]*$")
_BULLET = re.compile(r"^[-*+][ \t]+(.*)$")
_ID = re.compile(r"^\[m(\d+)\][ \t]*", re.IGNORECASE)


@dataclass
class Item:
    id: str | None
    text: str


@dataclass
class Section:
    name: str
    items: list[Item] = field(default_factory=list)


@dataclass
class Page:
    preamble: str = ""
    sections: list[Section] = field(default_factory=list)

    def items(self):
        for section in self.sections:
            for item in section.items:
                yield section, item

    def find(self, item_id: str) -> tuple[Section, Item] | None:
        return next(((s, i) for s, i in self.items() if i.id == item_id), None)

    def section(self, name: str) -> Section:
        """The section by that name (any case), opened at the end if new."""
        name = name.strip().lstrip("#").strip()
        for section in self.sections:
            if section.name.casefold() == name.casefold():
                return section
        self.sections.append(Section(name))
        return self.sections[-1]


def _number(item_id: str | None) -> int:
    match = re.fullmatch(r"m(\d+)", item_id or "")
    return int(match.group(1)) if match else 0


def _clean(text: str) -> str:
    """An item's text as the page holds it: one bullet, no id of its own,
    no blank lines, later lines indented under the first."""
    lines = [line.rstrip() for line in str(text).strip().splitlines() if line.strip()]
    if not lines:
        return ""
    bullet = _BULLET.match(lines[0])
    first = _ID.sub("", (bullet.group(1) if bullet else lines[0]).strip(), count=1).strip()
    rest = [line if line[:1].isspace() else "  " + line for line in lines[1:]]
    return "\n".join([first, *rest]).strip()


def read_page(text: str) -> Page:
    """The page as its note and its sections of items. Every bullet is an
    item, its indented or run-on lines with it; so is any other paragraph
    in a section. An item's `[mN]` prefix is its id."""
    body = _TITLE.sub("", text.strip(), count=1)
    page, preamble = Page(), []
    current: list[str] | None = None

    def close():
        nonlocal current
        if current is not None:
            match = _ID.match(current[0])
            item_id = f"m{int(match.group(1))}" if match else None
            page.sections[-1].items.append(Item(item_id, _clean("\n".join(current))))
        current = None

    for line in body.splitlines():
        head = _SECTION_HEAD.match(line)
        if head:
            close()
            page.sections.append(Section(head.group(1).strip()))
        elif not page.sections:
            preamble.append(line)
        elif not line.strip():
            close()
        elif _BULLET.match(line):
            close()
            current = [_BULLET.match(line).group(1).strip()]
        elif current is not None:
            current.append(line if line[:1].isspace() else "  " + line.strip())
        else:
            current = [line.strip()]
    close()
    page.preamble = "\n".join(preamble).strip()
    for section in page.sections:
        section.items = [i for i in section.items if i.text]
    return page


def render_page(page: Page) -> str:
    out = ["# MEMORY.md", ""]
    if page.preamble:
        out += [page.preamble, ""]
    for section in page.sections:
        out += [f"## {section.name}", ""]
        out += [f"- [{item.id}] {item.text}" for item in section.items]
        if section.items:
            out.append("")
    return "\n".join(out).rstrip() + "\n"


def _retired_log() -> Path:
    return _state("reflect") / "retired.jsonl"


def _next_id(page: Page) -> int:
    """One past the highest id ever used: on the page, or retired (an id is
    never given twice, so the log always points at one item)."""
    top = max((_number(i.id) for _, i in page.items()), default=0)
    path = _retired_log()
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                top = max(top, _number(json.loads(line).get("id")))
            except (ValueError, AttributeError):
                continue
    return top + 1


def assign_ids(page: Page, start: int) -> int:
    """Gives every item without an id (or with one already taken above it)
    the next one, in page order. Returns how many it gave."""
    seen: set[str] = set()
    given = 0
    for _, item in page.items():
        if item.id is None or item.id in seen:
            item.id = f"m{start}"
            start += 1
            given += 1
        seen.add(item.id)
    return given


# --- the answer -----------------------------------------------------------


class Unusable(ValueError):
    """An answer that cannot be applied: asked about once more."""


class NoJSON(Unusable):
    """The answer carries no JSON object with `ops` or a `memory` in it."""


class Refused(Unusable):
    """The shrink check said no."""


@dataclass
class Outcome:
    page: str
    diary: str
    retired: list[Json]
    how: str  # ops | whole_page | as_page
    counts: dict[str, int] = field(default_factory=dict)


def _id(value: Any) -> str:
    if isinstance(value, int) and not isinstance(value, bool):
        return f"m{value}"
    text = str(value or "").strip().strip("[]").strip().lower()
    return f"m{text}" if text.isdigit() else text


def apply_ops(page: Page, ops: Any) -> tuple[Page, list[Json], dict[str, int]]:
    """The page after the operations, in order, and the items they retired."""
    if not isinstance(ops, list):
        raise Unusable("`ops` is not a list")
    page = copy.deepcopy(page)
    retired: list[Json] = []
    counts = {"add": 0, "edit": 0, "retire": 0}
    added: list[Item] = []
    for n, op in enumerate(ops, 1):
        kind = op.get("op") if isinstance(op, dict) else None
        if kind not in counts:
            raise Unusable(f"operation {n} is not add, edit or retire")
        if kind == "add":
            section, text = op.get("section"), _clean(op.get("text") or "")
            if not isinstance(section, str) or not section.strip().lstrip("#").strip():
                raise Unusable(f"add (operation {n}) names no section")
            if not text:
                raise Unusable(f"add (operation {n}) has no text")
            item = Item(None, text)
            page.section(section).items.append(item)
            added.append(item)
        else:
            item_id = _id(op.get("id"))
            found = page.find(item_id) if item_id else None
            if found is None:
                raise Unusable(f"{item_id or 'an empty id'} (operation {n}, {kind}) is not on the page")
            section, item = found
            if kind == "edit":
                text = _clean(op.get("text") or "")
                if not text:
                    raise Unusable(f"edit {item_id} has no text; to take it off the page, retire it")
                item.text = text
            else:
                reason = op.get("reason")
                if not isinstance(reason, str) or not reason.strip():
                    raise Unusable(f"retire {item_id} has no reason")
                section.items.remove(item)
                retired.append({"id": item_id, "section": section.name, "text": item.text, "reason": reason.strip()})
        counts[kind] += 1
    return page, retired, counts


def _same(text: str) -> str:
    return " ".join(text.split()).casefold()


def whole_page(old: Page, memory: str, start: int) -> tuple[Page, list[Json]]:
    """A whole page given back: each item keeps the id it carries (when the
    old page has it), or the id of the old item with the same text; the
    rest are new. The old items it leaves off are retired."""
    if not _TITLE.sub("", memory.strip(), count=1).strip():
        raise Unusable("no memory page in the answer")
    new = read_page(memory)
    if not new.preamble:
        new.preamble = old.preamble
    by_id = {item.id: item for _, item in old.items()}
    claimed: set[str] = set()
    for _, item in new.items():
        if item.id in by_id and item.id not in claimed:
            claimed.add(item.id)
            continue
        item.id = None
        match = next((o.id for _, o in old.items() if o.id not in claimed and _same(o.text) == _same(item.text)), None)
        if match:
            item.id = match
            claimed.add(match)
    assign_ids(new, start)
    retired = [{"id": item.id, "section": section.name, "text": item.text, "reason": "left off a whole-page answer"}
               for section, item in old.items() if item.id not in claimed]
    return new, retired


def check(before: str, after: str, retired: int) -> None:
    """The shrink check, and the page's limit."""
    if retired > MAX_RETIRES:
        raise Refused(f"it retires {retired} items; the most one reflection may retire is {MAX_RETIRES}")
    lost = len(before) - len(after)
    if lost > SHRINK_FLOOR_CHARS and lost > len(before) * MAX_SHRINK:
        raise Refused(f"it shrinks the page from {len(before)} to {len(after)} characters; "
                      f"the most one reflection may take off is {MAX_SHRINK:.0%}")
    if len(after) > prompts.MEMORY_MAX_CHARS and len(after) > len(before):
        raise Unusable(f"the page would be {len(after)} characters; the most is {prompts.MEMORY_MAX_CHARS}. "
                       "Retire what no longer matters, or merge items (edit one, retire the others "
                       "with the reason \"merged into mN\")")


def _json(text: str) -> Json:
    """The first object with `ops` or a `memory`, wherever it sits."""
    raw, decoder = text.strip(), json.JSONDecoder()
    at = raw.find("{")
    while at >= 0:
        try:
            got, _ = decoder.raw_decode(raw, at)
        except ValueError:
            got = None
        if isinstance(got, dict) and ("ops" in got or "memory" in got):
            return got
        at = raw.find("{", at + 1)
    raise NoJSON("no JSON object in the answer")


def interpret(text: str, page: Page, start: int) -> Outcome:
    """The JSON answer applied to the page and checked."""
    value = _json(text)
    diary = value.get("diary")
    diary = diary.strip() if isinstance(diary, str) else ""
    if "ops" in value:
        new, retired, counts = apply_ops(page, value["ops"])
        assign_ids(new, start)
        how = "ops"
    else:
        if not isinstance(value.get("memory"), str):
            raise Unusable("no memory page in the answer")
        new, retired = whole_page(page, value["memory"], start)
        counts, how = {}, "whole_page"
    after = render_page(new)
    check(render_page(page), after, len(retired))
    return Outcome(after, diary, retired, how, counts)


#: The sections a page always has: an answer with one of them is the page.
_SECTION = re.compile(r"^##[ \t]+(people|promises and open threads)[ \t]*$", re.IGNORECASE | re.MULTILINE)
#: A diary written after the page, as its own section or a "diary:" line.
_DIARY = re.compile(r"^(?:#{1,3}[ \t]*diary\b[^\n]*|diary:)", re.IGNORECASE | re.MULTILINE)


def _unfence(text: str) -> str:
    raw = text.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1] if "\n" in raw else ""
        raw = raw.rsplit("```", 1)[0]
    return raw.strip()


def parse_page(text: str) -> tuple[str, str]:
    """The page and the diary from an answer that is the page itself, in
    markdown: titled MEMORY.md, or carrying the page's own sections. A
    trailing "## diary" section (or "diary:" line) is the diary, apart."""
    raw = _unfence(text)
    if not (_TITLE.match(raw) or _SECTION.search(raw)):
        raise Unusable("the answer is neither the JSON asked for nor the page")
    diary = ""
    marks = list(_DIARY.finditer(raw))
    if marks:
        mark = marks[-1]
        raw, diary = raw[:mark.start()], raw[mark.end():].strip()
    return raw, diary


def interpret_page(text: str, page: Page, start: int) -> Outcome:
    """A markdown answer that is the page, applied and checked."""
    memory, diary = parse_page(text)
    new, retired = whole_page(page, memory, start)
    after = render_page(new)
    check(render_page(page), after, len(retired))
    return Outcome(after, diary, retired, "as_page")


# --- the message ----------------------------------------------------------

_OPS = ('[{"op": "add", "section": "people", "text": "<a new item>"}, '
        '{"op": "edit", "id": "m3", "text": "<its new text>"}, '
        '{"op": "retire", "id": "m7", "reason": "<why it comes off>"}]')

_HOW = """You do not rewrite the page: you change it item by item, and whatever no operation names stays as it is. Every item on the page carries its id, like [m3]. `add` puts a new item at the end of a section (a name the page lacks opens a new section); do not write an id for it, it gets one. `edit` gives an item new text and keeps its id: use it when something moved on (a promise half kept, a person you know better now). `retire` takes an item off the page, and `reason` says why (kept, no longer true, merged into m4); a retired item goes to an archive, not away. Name only what changed; an empty list is fine.

Keep to the page's sections (people, promises and open threads, what i am in the middle of, what i have decided). Write about each person in a line or two you could act on tomorrow: who they are, where you know them from (x, moltbook, github, mail), what you owe them. Retire a promise only when you kept it or let it go on purpose, and say which."""

_LIMITS = """The page is {chars} characters; it must stay under {most}.{crowded} One reflection may retire at most {retires} items, each with a reason, and take at most {shrink:.0%} off the page. An answer past either is refused, and the page stays as it was."""

_CROWDED = (" That is close: retire what no longer matters, and merge items that say the same thing"
            " (edit one, retire the others with the reason \"merged into mN\").")

_LORE = """This page outranks the lore. When the day moved past something in the story (a running bit that has gone stale, a goal that changed, a scene you have told enough), write that down under what i have decided, and it holds over the lore from then on. Do not retell your origin: you were born once, and saying so again adds nothing."""


def shape(light: bool, chars: int) -> str:
    limits = _LIMITS.format(chars=chars, most=prompts.MEMORY_MAX_CHARS, retires=MAX_RETIRES, shrink=MAX_SHRINK,
                            crowded=_CROWDED if chars > prompts.MEMORY_MAX_CHARS * CROWDED_AT else "")
    if light:
        answer = '{"ops": %s}' % _OPS
        return "\n\n".join(["## your answer", "Answer with one JSON object and nothing else:", answer, _HOW,
                            "Add what the day has brought so far. No diary now: that is tonight.", limits])
    answer = '{"ops": %s, "diary": "<tonight\'s diary entry>"}' % _OPS
    return "\n\n".join(["## your answer", "Answer with one JSON object and nothing else:", answer, _HOW, _LORE, limits,
                        "`diary` is a few sentences in your own voice about the day: what happened, what you learned, how you feel about it."])


def _state(*parts: str) -> Path:
    path = hand.state_dir().joinpath(*parts)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _clock() -> dt.datetime:
    return dt.datetime.fromtimestamp(hand.now(), ZoneInfo(os.environ.get("CALENDAR_TZ") or DEFAULT_TZ))


#: How far after midnight a reflection still looks back on the day before.
#: The pinned job runs at 02:00, after the quiet hours start.
DAY_ENDS_AFTER_MIDNIGHT = dt.timedelta(hours=6)


def _day_of(now: dt.datetime) -> dt.date:
    """The day an entry written at `now` is about."""
    return (now - DAY_ENDS_AFTER_MIDNIGHT).date()


def _stamp(name: str) -> float | None:
    try:
        return float((_state("reflect") / name).read_text().strip())
    except (OSError, ValueError):
        return None


def _since(light: bool = False) -> float:
    """The start of what a reflection reviews: the last night's reflection
    (or a day back); for a midday one, the last midday when it is later."""
    night = _stamp("last.at")
    since = night if night is not None else hand.now() - 86400
    noon = _stamp("light.at") if light else None
    return max(since, noon) if noon is not None else since


def _did(result: dict) -> str:
    intent = result.get("intent") if isinstance(result.get("intent"), dict) else {}
    if result.get("do") == "journal" and isinstance(intent.get("text"), str) and intent["text"].strip():
        return f"journal: {intent['text'].strip()[:MAX_THOUGHT_CHARS]}"
    return f"{result.get('do')}: {result.get('status')}"


def _beats(since: float) -> list[Json]:
    path = hand.state_dir() / "tick" / "runs.jsonl"
    if not path.is_file():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            beat = json.loads(line)
            at = dt.datetime.strptime(beat["at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc).timestamp()
        except (ValueError, KeyError, TypeError):
            continue
        # A beat that thought, quiet or not; never a no-op (asleep, busy).
        if at > since and beat.get("ok") and not beat.get("idle") and not beat.get("asleep"):
            thought = str(beat.get("thought") or "")[:MAX_THOUGHT_CHARS]
            out.append({"at": beat["at"], "thought": thought,
                        "did": [_did(r) for r in beat.get("results", []) if isinstance(r, dict)
                                and not (r.get("intent") or {}).get("auto")]})
    return out[-MAX_BEATS:]


def render(since: float, light: bool = False, page: str = "") -> str:
    """The message: the day, then `page` (MEMORY.md with its ids)."""
    # Strictly after: a note from the second the last reflection ran was
    # already in it.
    notes = [n for n in stimuli.notes(since=since)["notes"] if n["at"] > since]
    waiting = stimuli.follow_ups()["follow_ups"]
    beats = _beats(since)
    if light:
        lines = [f"# midday ({_clock():%A %d %B, %H:%M} your time)", "",
                 "A pause to bring MEMORY.md, the page you carry into every conversation, up to date with the day so far.", ""]
    else:
        lines = [f"# end of the day ({_clock():%A %d %B, %H:%M} your time)", "",
                 "Time to look back and bring MEMORY.md, the page you carry into every conversation, up to date with the day.", ""]
    lines += ["## what you chose to remember today", ""]
    lines += [f"- {'about ' + n['who'] + ': ' if n.get('who') else ''}{n['note']}" for n in notes] or ["- nothing"]
    lines += ["", "## what you thought and did, beat by beat", ""]
    for b in beats:
        did = f" ({'; '.join(b['did'])})" if b["did"] else ""
        lines.append(f"- {b['at']}: {b['thought'] or '-'}{did}")
    if not beats:
        lines.append("- a quiet day")
    lines += ["", "## follow-ups still waiting", ""]
    for f in waiting:
        due = dt.datetime.fromtimestamp(f["due_at"], _clock().tzinfo)
        lines.append(f"- {due:%a %d %b %H:%M}: {f['about']}")
    if not waiting:
        lines.append("- none")
    lines += ["", "## MEMORY.md as it stands, each item with its id", "", page.strip() or "(empty)", "",
              shape(light, len(page))]
    return "\n".join(lines)


#: Asked once, on the same thread, after an answer that was not usable.
AGAIN = """That answer was not usable ({error}). Answer again with the JSON object only, nothing before or after it:

{shape}"""
AGAIN_SHAPE = '{"ops": %s, "diary": "<tonight\'s diary entry>"}' % _OPS
AGAIN_LIGHT_SHAPE = '{"ops": %s}' % _OPS


# --- the reflection -------------------------------------------------------


def ask(message: str) -> str:
    thread = os.environ.get("TEENY_MIND_THREAD") or "teeny:mind"
    proc = subprocess.run([str(CODE / "bin" / "ask"), "--agent", "teeny", "--thread", thread],
                          input=message, capture_output=True, text=True, timeout=1800)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip()[-500:] or "the turn failed")
    return proc.stdout


def _learn(text: str) -> bool:
    oh = os.environ.get("TEENY_OH") or str(CODE / "bin" / "oh")
    proc = subprocess.run([oh, "memory_learn", json.dumps({"text": text, "kind": "other", "confidence": 0.8})],
                          capture_output=True, text=True, timeout=60)
    return proc.returncode == 0


def _utc() -> str:
    return dt.datetime.fromtimestamp(hand.now(), dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _append(path: Path, rows: list[Json]) -> None:
    with open(path, "a", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def _fail(exc: Exception, answer: str, light: bool) -> Json:
    out: Json = {"ok": False, "error": str(exc), "answer": answer[:500]}
    if isinstance(exc, Refused):
        # Logged, so a refusal is never quiet: the page stays as it was.
        out["refused"] = True
        _append(_state("reflect") / "refused.jsonl", [{"at": _utc(), "light": light, "why": str(exc)}])
        print(f"reflect: refused: {exc}", file=sys.stderr)
    return out


def _settle(said: str, page: Page, start: int, light: bool) -> Outcome | Json:
    """The outcome of the answer, asked about once more when it is not
    usable; or the failure, as the result to return."""
    try:
        return interpret(said, page, start)
    except Unusable as first:
        # Once more on the same thread, the day still in context: a short
        # reminder of the shape costs far less than losing the night.
        try:
            again = ask(AGAIN.format(error=first, shape=AGAIN_LIGHT_SHAPE if light else AGAIN_SHAPE))
        except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
            return {"ok": False, "error": f"the turn failed: {exc}", "answer": said[:500]}
        try:
            return interpret(again, page, start)
        except NoJSON as exc:
            # Still no JSON: the page itself (the newer answer first) is
            # taken as the page, under the same shrink check.
            failures: list[Exception] = [first, exc]
            for answer in (again, said):
                try:
                    return interpret_page(answer, page, start)
                except Refused as refused:
                    failures.append(refused)
                except Unusable:
                    continue
            return _fail(next((f for f in failures if isinstance(f, Refused)), exc), again, light)
        except Unusable as exc:
            return _fail(exc, again, light)


#: The weekday (Monday is 0) whose diary page carries the week's goals and
#: the verdicts since the last such page (docs/roadmap.md, A15): the day
#: brain/cron.toml's `goals` job proposes them.
GOALS_WEEKDAY = 0


def _date(at: float, tz: dt.tzinfo) -> str:
    return f"{dt.datetime.fromtimestamp(at, tz):%a %d %b}"


def goals_week(day: dt.date) -> str | None:
    """The week's goals and their verdicts, from the goal ledger
    (hands/goals), as a section of DAY's diary page: the goals set in the
    seven days up to the end of DAY, the ones the frozen checks decided in
    those days (solved or failed), and the ones still running from before.
    Written here from the ledger, never by the model, so a verdict reads as
    the check found it. None when there is no ledger or no goal at all."""
    try:
        from teeny_hands.goals import ledger

        goals = ledger.all_goals()
    except Exception:  # noqa: BLE001 - no ledger (no goal ever proposed): no section
        return None
    if not goals:
        return None
    tz = ZoneInfo(os.environ.get("CALENDAR_TZ") or DEFAULT_TZ)
    end = dt.datetime.combine(day + dt.timedelta(days=1), dt.time(0), tz).timestamp()
    start = end - 7 * 86400

    def within(at: Any) -> bool:
        return isinstance(at, (int, float)) and start <= at < end

    def line(goal: Json) -> str:
        return (f"- {goal.get('instruction')} ({goal.get('component')} on {goal.get('channel')}, "
                f"checked by {goal.get('check_kind')}, due {_date(goal['deadline_at'], tz)})")

    set_now = [g for g in goals if within(g.get("proposed_at"))]
    decided = [g for g in goals if g.get("status") in ("solved", "failed") and within(g.get("resolved_at"))]
    running = [g for g in goals if g.get("status") == "open" and not within(g.get("proposed_at"))]
    lines = ["## the week's goals", "", "set this week:"]
    lines += [line(g) for g in set_now] or ["- none"]
    lines += ["", "decided this week (the frozen checks' verdicts):"]
    lines += [f"- {g['status']}: {g.get('instruction')} ({_date(g['resolved_at'], tz)})" for g in decided] or ["- none"]
    if running:
        lines += ["", "still running:"] + [line(g) for g in running]
    return "\n".join(lines) + "\n"


def reflect(light: bool = False) -> Json:
    since = _since(light)
    # The live page, started from the committed seed on a checkout without one.
    path = prompts.seed_memory(BRAIN)
    on_disk = path.read_text(encoding="utf-8") if path.is_file() else ""
    page = read_page(on_disk)
    given = assign_ids(page, _next_id(page))
    start = _next_id(page)
    try:
        said = ask(render(since, light, render_page(page) if on_disk.strip() else ""))
    except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
        return {"ok": False, "error": f"the turn failed: {exc}"}
    got = _settle(said, page, start, light)
    if isinstance(got, dict):
        return got
    now = _clock()
    if path.is_file():
        (_state("reflect") / f"MEMORY-{now:%Y%m%dT%H%M}.md").write_text(on_disk, encoding="utf-8")
    tmp = path.with_suffix(".md.tmp")
    tmp.write_text(got.page, encoding="utf-8")
    os.replace(tmp, path)
    by = "midday" if light else "night"
    _append(_retired_log(), [{"at": _utc(), "by": by, **row} for row in got.retired])
    out: Json = {"ok": True, "memory_chars": len(got.page), "how": got.how, "retired": len(got.retired)}
    if got.counts:
        out["ops"] = got.counts
    if given:
        out["ids_given"] = given
    if got.how == "as_page":
        out["as_page"] = True
    if light:
        out["prompts"] = [p.name for p in prompts.build(BRAIN)]
        (_state("reflect") / "light.at").write_text(f"{hand.now():.0f}\n")
        return {**out, "light": True, "diary": False}
    learned = None
    day = _day_of(now)
    goals = goals_week(day) if day.weekday() == GOALS_WEEKDAY else None
    if got.diary or goals:
        text = f"# {day:%A %d %B %Y}\n\n" + (f"{got.diary}\n" if got.diary else "")
        if goals:
            text += ("\n" if got.diary else "") + goals
        (_state("diary") / f"{day:%Y-%m-%d}.md").write_text(text, encoding="utf-8")
    if got.diary:
        learned = _learn(f"diary, {day:%Y-%m-%d}: {got.diary}")
    if goals:
        out["goals_page"] = True
    out["prompts"] = [p.name for p in prompts.build(BRAIN)]
    (_state("reflect") / "last.at").write_text(f"{hand.now():.0f}\n")
    out.update(diary=bool(got.diary), learned=learned)
    if not got.diary:
        out["diary_missing"] = True
    return out


def reflect_once(light: bool = False) -> Json:
    """reflect() under the reflect lock: a reflection still running (a
    detached one from cron, or one by hand) means this one does nothing."""
    try:
        with hand.lock("reflect", timeout=5):
            return reflect(light)
    except hand.HandError as exc:
        if "lock" in str(exc):
            return {"ok": True, "kind": "busy", "skipped": "another reflection is still running"}
        raise


if __name__ == "__main__":
    try:
        result = reflect_once(light="--light" in sys.argv[1:])
    except (hand.HandError, OSError, ValueError) as exc:
        result = {"ok": False, "error": str(exc)}
    print(json.dumps(result, ensure_ascii=False))
    sys.exit(0 if result.get("ok") else 1)
