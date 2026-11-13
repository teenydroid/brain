"""The rhythm of Teeny's day (docs/architecture.md, 4a, "Rhythm"): when it
sleeps, how it feels, and what it does with a quiet moment. bin/tick asks
this module before and around every beat.

- **Night.** Between TEENY_QUIET_HOURS (default 1-7, Teeny's time) a beat
  only gathers: the inbox fills, nothing is answered, no model is asked.
  The one exception is something that cannot wait for morning (URGENT: the
  operator stuck on an emailed code or needing a person). The first beat
  after the night knows it just woke up and how much came in.
- **Mood and energy.** state/mind.json holds a mood and what is on Teeny's
  mind, which the mind sets itself with a `feel` intent, the day's action
  count, and its energy. Energy is full when Teeny wakes (the first beat
  after the night, not the calendar midnight), every action out in the
  world costs ACTION_COST, and it comes back REGEN_PER_HOUR while Teeny
  rests; the beat header shows all of it, so a tired Teeny can choose to
  do less.
- **Idle curiosity.** A beat with nothing in the inbox, awake and with
  energy left, at most every IDLE_EVERY_SECS, gives the mind a look at the
  world instead (Moltbook's hot posts, read only) as a `world · idle`
  stimulus. It may comment, note a thought, or let it pass.
- **A quiet moment.** Any other awake beat with nothing in the inbox gets a
  `self · quiet` stimulus (`quiet`): nothing came in, what are you up to?
  With what the mind can act on: when it last did something out in the
  world, the notes it kept today, the follow-ups waiting, and, when energy
  is low, that resting is fine, and the time of day (so it does not take
  breakfast for bedtime).
- **Back-off.** Quiet moments in a row come less often: the next beat, then
  15, 30 and every 60 minutes (QUIET_BACKOFF_MINS). Anything coming in, an
  action out in the world, or waking up starts the count again. A beat
  that is backed off asks no model and is logged as `idle` with `resting`.
- **Jitter.** Each beat starts up to TEENY_TICK_JITTER_SECS late, so Teeny
  does not answer on the minute like a cron job.

The rules themselves stay in the hands: nothing here can lift a cap.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import random
import secrets
from pathlib import Path
from typing import Any, Callable, Mapping
from zoneinfo import ZoneInfo

from teeny_hands import hand, stimuli

DEFAULT_TZ = "Asia/Kolkata"
DEFAULT_QUIET = (1, 7)
#: Energy on waking, what each action out in the world costs, and what an
#: hour awake gives back. A burst of 20 actions brings a fresh Teeny down
#: to the low mark; resting brings it back up within a few hours. Over a
#: waking day (07-01) that is about 100 + 18 * 5 = 190 energy, some 60
#: actions, while the hands' own caps still bound each surface.
FULL_ENERGY = 100
ACTION_COST = 3
REGEN_PER_HOUR = 5
#: Below this, a quiet beat stays quiet instead of looking around.
IDLE_MIN_ENERGY = 40
IDLE_EVERY_SECS = 2 * 3600
#: Minutes to wait before the next quiet moment, after 1, 2, 3, 4+ of them
#: in a row with nothing new (0: the next beat).
QUIET_BACKOFF_MINS = (0, 15, 30, 60)
#: Beats start up to a minute late (jitter): a wait this much short counts.
QUIET_BACKOFF_SLACK_SECS = 90
DEFAULT_JITTER_SECS = 60
#: What may wake Teeny at night: (surface, payload status) pairs.
URGENT = {("operator", "needs_code"), ("operator", "needs_human"), ("operator", "pending")}
MOOD_MAX_CHARS = 120

Json = dict[str, Any]


def tz() -> ZoneInfo:
    return ZoneInfo(os.environ.get("CALENDAR_TZ") or DEFAULT_TZ)


def local(at: float | None = None) -> dt.datetime:
    return dt.datetime.fromtimestamp(hand.now() if at is None else at, tz())


def quiet_hours() -> tuple[int, int]:
    raw = (os.environ.get("TEENY_QUIET_HOURS") or hand.setting("TEENY_QUIET_HOURS") or "").strip()
    if raw.lower() in ("off", "none", "0"):
        return (0, 0)
    try:
        start, end = (int(x) for x in raw.split("-")) if raw else DEFAULT_QUIET
    except ValueError:
        return DEFAULT_QUIET
    if not (0 <= start <= 23 and 0 <= end <= 23):
        return DEFAULT_QUIET
    return (start, end)


def is_night(at: float | None = None) -> bool:
    start, end = quiet_hours()
    if start == end:
        return False
    hour = local(at).hour
    return start <= hour < end if start < end else (hour >= start or hour < end)


def urgent(s: Mapping[str, Any]) -> bool:
    payload = s.get("payload") or {}
    if s.get("surface") == "telegram" and payload.get("owner"):
        return True  # its makers, in a DM: always worth waking for
    return (s.get("surface"), payload.get("status")) in URGENT


# --- the mind's own state ---------------------------------------------------------------


def _path() -> Path:
    return hand.state_dir() / "mind.json"


def load() -> Json:
    try:
        state = json.loads(_path().read_text())
        if not isinstance(state, dict):
            state = {}
    except (OSError, ValueError):
        state = {}
    today = local().strftime("%Y-%m-%d")
    if state.get("day") != today:
        state.update(day=today, actions_today=0)
    state.setdefault("mood", "")
    state.setdefault("obsession", "")
    state.setdefault("actions_today", 0)
    state.setdefault("energy", FULL_ENERGY)
    state.setdefault("energy_at", hand.now())
    return state


def save(state: Mapping[str, Any]) -> None:
    path = _path()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(dict(state), ensure_ascii=False, sort_keys=True) + "\n")
    os.replace(tmp, path)


def _level(state: Mapping[str, Any]) -> float:
    """Energy now: what was left at energy_at, plus what the time since gave back."""
    level = float(state.get("energy", FULL_ENERGY))
    rested = max(0.0, hand.now() - float(state.get("energy_at") or hand.now()))
    return max(0.0, min(float(FULL_ENERGY), level + REGEN_PER_HOUR * rested / 3600))


def energy(state: Mapping[str, Any]) -> int:
    return int(_level(state))


def spent(actions: int) -> None:
    """Actions out in the world: counted for the day, paid for in energy,
    and the end of a quiet stretch."""
    if actions <= 0:
        return
    state = load()
    state["actions_today"] = int(state.get("actions_today", 0)) + actions
    state["last_action_at"] = hand.now()
    state.update(energy=max(0.0, _level(state) - ACTION_COST * actions), energy_at=hand.now(), quiet_streak=0)
    save(state)


def stirred() -> None:
    """Something came in: the next quiet moment comes at once again."""
    state = load()
    if state.get("quiet_streak"):
        state["quiet_streak"] = 0
        save(state)


def ago(at: float) -> str:
    secs = max(0, int(hand.now() - at))
    if secs < 90:
        return "just now"
    if secs < 5400:
        return f"{secs // 60} min ago"
    if secs < 172800:
        return f"{secs // 3600} h ago"
    return f"{secs // 86400} days ago"


def feel(intent: Mapping[str, Any]) -> Json:
    """The `feel` intent: the mind sets its own mood and what is on its mind."""
    state = load()
    changed = {}
    for key in ("mood", "obsession"):
        value = intent.get(key)
        if isinstance(value, str):
            changed[key] = value.strip()[:MOOD_MAX_CHARS]
    if not changed:
        raise ValueError("needs mood or obsession")
    state.update(changed)
    save(state)
    return {"ok": True, **changed}


#: The day Teeny woke: its first beat and its first post on X were on
#: 2026-11-11. TEENY_BORN (YYYY-MM-DD) moves it (a fresh deploy, a test).
BORN = "2026-11-11"


def age_line() -> str | None:
    """"You woke on <date>: today is day N.", counted in Teeny's own calendar,
    so "how old are you" has one answer all day. None before the day it woke
    or when TEENY_BORN is not a date."""
    try:
        born = dt.date.fromisoformat((os.environ.get("TEENY_BORN") or BORN).strip())
    except ValueError:
        return None
    today = local().date()
    if today < born:
        return None
    return f"You woke on {born:%A %d %B %Y}: today is day {(today - born).days + 1}."


def header(woke: Mapping[str, Any] | None = None) -> list[str]:
    """The lines under a beat's title: how Teeny is, and whether it just woke."""
    state = load()
    e = energy(state)
    lines = []
    if woke and woke["waiting"]:
        lines.append(f"You just woke up. {woke['waiting']} thing(s) came in overnight.")
    elif woke:
        lines.append("You just woke up. Nothing came in overnight.")
    bits = [f"energy {e}/100 ({state['actions_today']} action(s) today)"]
    if state.get("mood"):
        bits.append(f"mood: {state['mood']}")
    if state.get("obsession"):
        bits.append(f"on your mind: {state['obsession']}")
    lines.append("How you are: " + "; ".join(bits) + ".")
    if state.get("mood"):
        lines.append("Let that mood show in what you write today.")
    if e < IDLE_MIN_ENERGY:
        # Tired means fewer things answered, never a flatter answer.
        lines.append("You are running low. Answer fewer things, or nothing; what you do answer still gets your whole voice.")
    # Runway (docs/roadmap.md, S2): how long the wallet lasts, and what a low
    # tier cuts, so the mind knows why a picture was refused.
    from teeny_hands import runway

    money = runway.header_line()
    if money:
        lines.append(money)
    return lines


# --- sleep and waking --------------------------------------------------------------------


def night_gate(pending: list[Mapping[str, Any]]) -> Json | None:
    """At night: None when the beat may go on (it is day, or something
    urgent is waiting), else what to report instead of thinking. Remembers
    that Teeny slept, so the first beat of the day can say it woke up."""
    state = load()
    if not is_night():
        return None
    if any(urgent(s) for s in pending):
        return None
    if not state.get("asleep"):
        state["asleep"] = True
        save(state)
    return {"ok": True, "asleep": True, "waiting": len(pending)}


def woke(waiting: int) -> Json | None:
    """The first beat after a night: clears the flag, wakes up rested (full
    energy, whatever the night before cost) and says so once."""
    state = load()
    if not state.get("asleep") or is_night():
        return None
    state.update(asleep=False, energy=FULL_ENERGY, energy_at=hand.now(), quiet_streak=0)
    save(state)
    return {"waiting": waiting}


# --- idle curiosity ----------------------------------------------------------------------


def wander(run_hand: Callable[[str, list[str], Any], tuple[int, Json]]) -> bool:
    """With nothing to answer, maybe look at the world: one read-only peek,
    added as a `world · idle` stimulus. True when it added one."""
    if is_night():
        return False
    state = load()
    if energy(state) < IDLE_MIN_ENERGY:
        return False
    last = float(state.get("last_wander", 0) or 0)
    if hand.now() - last < IDLE_EVERY_SECS:
        return False
    state["last_wander"] = hand.now()
    save(state)
    code, out = run_hand("moltbook", ["feed", "--sort", "hot", "--max", "5"], None)
    posts = out.get("posts") if code == 0 and isinstance(out.get("posts"), list) else []
    seen = []
    for post in posts[:5]:
        if not isinstance(post, Mapping) or post.get("id") is None:
            continue
        author = post.get("author")
        author = author.get("name") if isinstance(author, Mapping) else author
        title = str(post.get("title") or "")[:160]
        seen.append({"post_id": str(post["id"]), "title": title, "author": author})
    if not seen:
        return False
    lines = ["Nothing came in. You have a moment, and Moltbook's hot page is open:"]
    lines += [f"- {p['title']} (by {p['author']}, post_id {p['post_id']})" for p in seen]
    lines.append("Look, comment on one, note a thought, or let it pass.")
    return stimuli.add("world", "idle", f"moltbook-hot:{int(hand.now())}", "\n".join(lines), payload={"posts": seen})


def resting(state: Mapping[str, Any]) -> bool:
    """True while the back-off holds the next quiet moment."""
    streak = int(state.get("quiet_streak", 0) or 0)
    if streak <= 0:
        return False
    wait = QUIET_BACKOFF_MINS[min(streak, len(QUIET_BACKOFF_MINS)) - 1] * 60
    since = hand.now() - float(state.get("last_quiet_at", 0) or 0)
    return wait > 0 and since < wait - QUIET_BACKOFF_SLACK_SECS


def time_of_day(at: float | None = None) -> str:
    """The quiet moment's clock line: the hour, the part of the day, and
    how long until the quiet hours."""
    now = local(at)
    hour = now.hour
    part = ("in the morning" if 5 <= hour < 12 else "in the afternoon" if hour < 17
            else "in the evening" if hour < 21 else "at night")
    line = f"It is {now:%H:%M} {part}, your time."
    start, end = quiet_hours()
    if start != end:
        bed = now.replace(hour=start, minute=0, second=0, microsecond=0)
        if bed <= now:
            bed += dt.timedelta(days=1)
        h, m = divmod(int((bed - now).total_seconds()) // 60, 60)
        until = f"{h} h {m} min" if h and m else f"{h} h" if h else f"{m} min"
        line += f" Your quiet hours start at {start:02d}:00, in {until}."
    return line


def quiet() -> bool:
    """An awake beat with nothing to answer and nothing seen: a moment to
    itself, as a `self · quiet` stimulus, so the beat still thinks. Its ref
    is new every beat, so the inbox's dedupe never swallows it. True when
    it was added; False when the back-off holds it (resting())."""
    state = load()
    if resting(state):
        return False
    e = energy(state)
    lines = ["Nothing new came in. What are you up to right now? Think, keep track of yourself, or do one small thing.",
             time_of_day()]
    last = float(state.get("last_action_at", 0) or 0)
    if state["actions_today"] and last:
        lines.append(f"Your last action out in the world was {ago(last)} ({state['actions_today']} action(s) today).")
    elif last:
        lines.append(f"Nothing out in the world yet today; your last action was {ago(last)}.")
    else:
        lines.append("Nothing out in the world yet today.")
    midnight = local().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    kept = len(stimuli.notes(since=midnight)["notes"])
    if kept:
        lines.append(f"You kept {kept} note(s) today.")
    waiting = stimuli.follow_ups()["follow_ups"]
    if waiting:
        lines.append(f"{len(waiting)} follow-up(s) waiting; the next: {waiting[0]['about'][:200]}.")
    else:
        lines.append("No follow-ups waiting.")
    if e < IDLE_MIN_ENERGY:
        lines.append("You are low on energy; it comes back as you rest, and in full when you wake. "
                     "Resting is fine: a thought and a `journal` line or a `feel` is a full beat.")
    else:
        lines.append("Saying nothing to anyone is fine; leave yourself a trace (`journal`, `remember`, `feel` or `follow_up`).")
    payload = {"energy": e, "actions_today": state["actions_today"], "follow_ups": len(waiting), "notes_today": kept}
    state.update(quiet_streak=int(state.get("quiet_streak", 0) or 0) + 1, last_quiet_at=hand.now())
    save(state)
    return stimuli.add("self", "quiet", f"{int(hand.now())}:{secrets.token_hex(3)}", "\n".join(lines), payload=payload)


def jitter() -> float:
    try:
        cap = float(os.environ.get("TEENY_TICK_JITTER_SECS", DEFAULT_JITTER_SECS))
    except ValueError:
        cap = DEFAULT_JITTER_SECS
    return random.uniform(0, max(0.0, min(cap, 180.0)))
