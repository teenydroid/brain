"""The `skit` format: a public figure from a card, an impossible scene, on
video (docs/architecture.md, "The studio"). Its words for the write turn,
its structural check (`skit_of`), its producer (`_make_skit`) and what the
record keeps of it (`skit_outcome`), as `FORMAT`."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

from formats import Format
from studio_room import Json, _brief, _judge, _words

if TYPE_CHECKING:
    from studio_room import Room

#: The repository (bin/lib/formats/skit.py is three below it).
REPO = Path(__file__).resolve().parents[3]


def _guard_lib() -> None:
    lib = str(REPO / "hands" / "lib")
    if lib not in sys.path:
        sys.path.insert(0, lib)


def _skit_rules():
    """The guard's skit module (hands/lib/teeny_hands/guard/skit.py): the
    shape and the hard lines in code, the same code `hands/guard skit` runs."""
    _guard_lib()
    from teeny_hands.guard import skit as rules

    return rules


def _task_limit() -> int:
    """The guard's hard cap on a text a machine is sent (the `task` surface,
    hands/lib/teeny_hands/guard/gate.py LIMITS): what `hands/guard check`
    refuses the one-take script over. A fence: read here, never raised."""
    _guard_lib()
    from teeny_hands.guard.gate import LIMITS

    return LIMITS["task"]["text"]


def _length(text: str) -> int:
    """A text's length as the guard counts it (UTF-16 code units)."""
    _guard_lib()
    from teeny_hands.guard.style import post_length

    return post_length(text)


#: The longest shot the studio hands on, in seconds (`skit_of`). A longer
#: `seconds` is cut to this, never refused: on kling-v3-pro a 6 s shot
#: quoted $0.7215 (about $1.44 reserved, under MEDIA_SKIT_MAX_USD_PER_SHOT's
#: $1.60) while an 8 s one needed $1.924 reserved, which
#: hands/lib/teeny_hands/media/skits.py cancels, failing the whole skit to a
#: text fallback; every other model is priced about the same (2026-11-20).
SKIT_SHOT_MAX_SECS = 6
#: The most the one-take script (and so each per-shot prompt, a part of it)
#: may be: the guard's `task` cap. The live 23:00 slot of 2026-11-21 (run
#: 20261121T173009Z-2300-c1f2f2) lost its skit to a 1569-character script,
#: as had the 23:00 dry run of 2026-11-20: the writer was never told the cap.
SKIT_SCRIPT_LIMIT = _task_limit()


def _written(skit: Json) -> int:
    """What the writer wrote of a skit's script: each figure's name and look,
    the setting, the score and every shot's prompt."""
    look = skit.get("look") or {}
    return sum(_length(str(t)) for t in (*look.keys(), *look.values(), skit.get("setting") or "",
                                         skit.get("score") or "",
                                         *(s.get("prompt") or "" for s in skit.get("shots") or [])))


def skit_budget(figures: int) -> int:
    """The characters a skit of `figures` figures may spend on its names,
    looks, setting, score and shot prompts together, so that its one-take
    script fits the guard's cap in the most shots at the longest length: the
    cap less what the script's own frame adds (its header, the "the same in
    every shot" lines, each shot's time range)."""
    rules = _skit_rules()
    names = [f"f{n}" for n in range(max(1, figures))]
    frame = {"figures": names, "look": {n: "x" for n in names}, "setting": "x", "score": "x",
             "shots": [{"prompt": "x", "seconds": SKIT_SHOT_MAX_SECS} for _ in range(rules.MAX_SHOTS)]}
    return SKIT_SCRIPT_LIMIT - (_length(rules.script(frame)) - _written(frame))


# --- skits: a public figure from a card, an impossible scene, on video --------------
# (hands/video skit, cut and labelled on the reel service; docs/architecture.md,
# "The studio"). Off unless STUDIO_FORMATS names skit, and hands/video refuses
# every skit until the operator sets MEDIA_SKIT_MAX_PER_DAY. The studio checks
# only a skit's structure (`skit_of`); the guard's hard lines run in code and
# through the judge before any money is spent (`hands/guard skit`), and on the
# cut's frames after it is rendered (`hands/guard view-skit`). The cast is the
# card's `people`: the guard is given the card, people and all, as the story
# the skit stages (`_make_skit`). A skit that cannot be made is no post: the
# slot falls through to the next ranked post.

SKIT_ASK = ("A skit is a post with a short AI-made parody video: one or two of the card's `people` staged in an "
            "obviously impossible, absurd, expensive-looking scene, three or four shots (each 3 to 6 seconds), made as "
            "one take: one film of up to 20 seconds, hard cuts, the same face, the same set and one continuous score. "
            "The premise is what the figure did or claimed on the card, pushed to an impossible end (\"X did Y, so X "
            "now does Z\"). The figure drives every shot and the impossible thing is in frame in every shot. First shot "
            "sets up the real thing so plainly that someone with the sound off gets it; second escalates; last is the "
            "payoff and ends on a held still image. Say once, as data, what holds across the film: `look` (one line a "
            "figure: hair, glasses, wardrobe, prop), `setting` (one line: the place, its light, the lens, the film "
            "look), `score` (one line: the one piece of music under it all). Each shot then says only what changes: "
            "the framing, the figure's full name and the one thing they do, what reacts, at most one short spoken "
            "line (about six words) and its sound. No sign, screen or banner with writing on it. The tone of every "
            "skit: defiant (nobody is sorry or embarrassed; the figure does it at the people watching, on purpose, "
            "and the contempt points up, at the board, the regulator, the rule), extreme (the idea goes all the way "
            "to its limit, never half), classy (champagne, velvet, chandeliers, tailoring: the more expensive it "
            "looks, the ruder it reads) and horny for the thing the story is about (the chip, the money, the merger). "
            "That thing is the shameless one: it flirts, it is dressed for it, and it gets the heart eyes and the "
            "kiss. The lust is never for a person and nothing sexual is shown about a real person. And it makes "
            "sense: each shot is caused by the one before it, starting from the fact on the card. The post's own words "
            "say what happened in a clause and frame the video as your parody. The video model is sent one script "
            f"and the guard refuses one over {SKIT_SCRIPT_LIMIT} characters, so the names, looks, setting, score and "
            f"every shot's prompt come to at most {skit_budget(1)} characters with one figure ({skit_budget(2)} with "
            f"two, {skit_budget(3)} with three).")
SKIT_SHAPE = ('{"figures": ["<people from the card, as the card names them>"], "premise": "<one line: what they '
              'did or claimed on the card, so what they do in the video>", "logline": "<one line: the impossible '
              'scene and its joke>", "look": {"<each figure>": "<one line>"}, "setting": "<one line>", "score": '
              '"<one line>", "shots": [{"prompt": "<one shot>", "seconds": <3 to 6>}, "... 3 or 4 shots"], '
              '"aspect": "16:9"}')
SKIT_RULES = ("A skit's mechanics: it carries an 'AI parody' label the whole way through, so the post's words never "
              "call it real, leaked or caught on camera; no logo or brand shown endorsing anything; no @-mentions and "
              "no links.")
#: What a skit leaves of the run's time for what comes after its render (the
#: viewer, the upload, the post, and a fallback when it fails), and the most
#: its cut may take on the reel service.
SKIT_RESERVE_SECS = 600
SKIT_CUT_SECS = 300
#: The longest premise kept (`skit_of`): one line.
SKIT_PREMISE_MAX = 300
#: What a skit holds as data across its whole film (`skit_of` requires them;
#: the guard's shape checks and keeps them): each figure's look, the
#: setting, the score.
SKIT_HELD = ("look", "setting", "score")

def skit_of(raw: Any, card: Json) -> tuple[Json, str]:
    """A post's `skit` as kept ({story, figures, premise, logline, shots,
    aspect}), and why it cannot be made ("" when it can): structure only.
    The story is the card's id, set here, never the writer's. The premise
    (what the figure did, so what they do in the video) is shown to the
    editor and never rendered or posted."""
    skit, bad = _skit_rules().shape(raw)
    for shot in skit.get("shots") or []:
        if shot.get("seconds", 0) > SKIT_SHOT_MAX_SECS:
            shot["seconds"] = SKIT_SHOT_MAX_SECS
    premise = raw.get("premise") if isinstance(raw, dict) else None
    premise = " ".join(premise.split()) if isinstance(premise, str) else ""
    if premise:
        skit["premise"] = premise[:SKIT_PREMISE_MAX]
    if not bad and not premise:
        bad = ("a skit needs its premise: one line, what the figure really did or claimed in the story, so what "
               "they do in the video")
    elif not bad and len(premise) > SKIT_PREMISE_MAX:
        bad = f"a skit's premise is one line of at most {SKIT_PREMISE_MAX} characters"
    if not bad:
        missing = [f for f in skit.get("figures") or [] if f not in (skit.get("look") or {})]
        if missing or not skit.get("look"):
            bad = ("a skit needs a look for each figure: one line, hair, glasses, wardrobe and prop, the same in "
                   "every shot" + (f" (none for {', '.join(missing)})" if missing else ""))
        elif not skit.get("setting"):
            bad = "a skit needs its setting: one line, the one place, its light and lens, for the whole film"
        elif not skit.get("score"):
            bad = "a skit needs its score: one line, the one piece of music under the whole film"
    if card.get("id"):
        skit["story"] = card["id"]
    elif not bad:
        bad = "a skit stages a card, and it names none"
    return skit, bad


def skit_script_length(skit: Json) -> int:
    """The longest text a video model would be told of SKIT, as the guard
    counts it: the one-take script (each per-shot prompt is a part of it)."""
    rules = _skit_rules()
    return max(_length(t) for t in (rules.script(skit),
                                    *(rules.shot_prompt(skit, n) for n in range(len(skit.get("shots") or [])))))


def skit_over(skit: Json) -> str:
    """Why SKIT's one-take script is too long for the guard, or ""."""
    length = skit_script_length(skit)
    if length <= SKIT_SCRIPT_LIMIT:
        return ""
    return (f"its one-take script is {length} characters, over the guard's {SKIT_SCRIPT_LIMIT}: the names, looks, "
            f"setting, score and shots come to {_written(skit)}, and {skit_budget(len(skit.get('figures') or []))} "
            "is the most they may")


def skit_shorten_message(skit: Json, why: str) -> str:
    """The writer's one cut of a skit whose one-take script is over the
    guard's cap: the same film in fewer words (`run`'s shorten turn, for a
    video)."""
    budget = skit_budget(len(skit.get("figures") or []))
    shown_ = {"figures": skit.get("figures") or [], "premise": skit.get("premise"), "logline": skit.get("logline"),
              **{k: skit[k] for k in SKIT_HELD if skit.get(k)},
              "shots": [{k: s[k] for k in ("prompt", "seconds") if k in s} for s in skit.get("shots") or []]}
    return ("# studio: cut the skit to fit\n\nThe editor picked this skit, but it is too long to film: "
            f"{why}. The video model is sent one script of the whole film and the guard refuses one over "
            f"{SKIT_SCRIPT_LIMIT} characters, so nothing would be made. Cut it to fit: the figures' names and "
            f"`look`s, the `setting`, the `score` and every shot's `prompt` together at most {budget} characters. "
            "The same film: the same figures, premise and logline, the same beats in the same order, the "
            "impossible thing still in frame in every shot, still rich concrete words; leave out what a "
            "camera would not miss, never the gag. Three shots are enough.\n\n"
            "```json\n" + json.dumps(shown_, ensure_ascii=False) + "\n```\n\n## your answer\n\nJSON only, no "
            'prose and no code fence: {"skit": {"look": {"<each figure>": "..."}, "setting": "...", "score": '
            '"...", "shots": [{"prompt": "...", "seconds": <3 to 6>}, ...]}}, or {"skip": "<why>"} when it '
            "cannot be cut.")


def _fit_skit(room: "Room", draft: Json, skit: Json, hook: Json, note: Json) -> Json | None:
    """SKIT as it fits the guard's cap on the one-take script: itself when it
    does; else the writer's one cut of it (`skit_shorten_message`), shaped
    and screened as a first draft is; else None, with `note.failed` saying
    why. What happened is `note.shortened`: the lengths before and after,
    and whether the cut was kept."""
    why = skit_over(skit)
    if not why:
        return skit
    before = skit_script_length(skit)
    log: Json = {"before": before, "kept": False}
    note["shortened"] = log
    failed = f"the skit's one-take script could not be made to fit: {why}"
    if room.left() - 5 < room.cfg.turn_min_secs:
        log["why"] = "no time left for the writer to cut it"
        note["failed"] = failed
        return None
    # A post is known by its label (P1...) in the studio's three steps; it has no `id`.
    which = draft.get("label") or draft.get("card") or "?"
    answer = room.turn("writer", f"shorten skit {which}", skit_shorten_message(skit, why),
                       room.writer_thread, soft=True)
    cut = answer.get("skit") if isinstance(answer, dict) else None
    if not isinstance(cut, dict):
        log["why"] = (str(answer.get("skip") or "no cut")[:300] if isinstance(answer, dict)
                      else "no usable answer, twice")
        note["failed"] = failed
        return None
    raw = {**{k: skit.get(k) for k in ("figures", "premise", "logline", "aspect", *SKIT_HELD)},
           **{k: cut[k] for k in (*SKIT_HELD, "shots") if k in cut}}
    raw.setdefault("shots", skit.get("shots"))
    again, bad = skit_of(raw, {"id": skit.get("story")})
    if bad:
        log["why"] = bad[:300]
        note["failed"] = failed
        return None
    log["after"] = skit_script_length(again)
    still = skit_over(again)
    if still:
        log["why"] = still
        note["failed"] = f"the skit's one-take script could not be made to fit, even after one cut: {still}"
        return None
    log["kept"] = True
    draft["skit"] = again
    note["shots"] = len(again.get("shots") or [])
    return again


def _make_skit(room: "Room", draft: Json, note: Json) -> list[str] | None:
    skit = draft.get("skit") or {}
    shots = [s.get("prompt") for s in skit.get("shots") or [] if isinstance(s, dict)]
    note.update(story=skit.get("story"), figures=skit.get("figures"), logline=skit.get("logline"), shots=len(shots))
    # The card as the guard reads a story: its people are named in it, so
    # they are the cast (`studio.story_of`).
    hook = draft.get("story") if isinstance(draft.get("story"), dict) else None
    if not shots or not skit.get("logline") or hook is None:
        note["failed"] = "the draft has no skit, or no story it stages"
        return None
    # The guard refuses a one-take script over its `task` cap: one over it
    # is cut once by the writer here, before the guard sees it, rather than
    # the slot losing its video (the live 23:00 slot of 2026-11-21).
    skit = _fit_skit(room, draft, skit, hook, note)
    if skit is None:
        return None
    shots = [s.get("prompt") for s in skit.get("shots") or [] if isinstance(s, dict)]
    # Every word a video model is told is past the guard's screen and judge,
    # as a scene is (the `task` surface): the logline, each constant, each
    # shot, and the prompts hands/video composes from them (the one-take
    # script, with the shots' own lengths, and each per-shot prompt, its
    # fallback), built by the same guard code it uses.
    rules = _skit_rules()
    try:
        composed = [rules.script(skit), *(rules.shot_prompt(skit, n) for n in range(len(shots)))]
    except (KeyError, TypeError, AttributeError):
        note["failed"] = "the draft's skit could not be composed into a script"
        return None
    texts = [("logline", skit["logline"]), *((f"look of {k}", v) for k, v in (skit.get("look") or {}).items()),
             *((k, skit[k]) for k in ("setting", "score") if skit.get(k)),
             *((f"shot {n}", t) for n, t in enumerate(shots, 1)), ("one-take script", composed[0]),
             *((f"shot {n} prompt", t) for n, t in enumerate(composed[1:], 1))]
    for what, text in texts:
        code, checked = room.budgeted(["guard", "check"], {"text": text, "surface": "task"}, 120)
        if not (code == 0 and isinstance(checked, dict) and isinstance(checked.get("text"), str)):
            note.update(failed=f"the guard held its {what}", guard=_brief(checked, "refused", "rule", "category",
                                                                           "error"))
            return None
    # The hard lines for real people: code's screen, then the skit judge, on
    # the logline, every shot and the words that would go out with it. Before
    # any money; a judge that cannot answer is a no.
    code, got = room.budgeted(["guard", "skit"], {"skit": skit, "text": _words(draft), "story": hook}, 200)
    note["hard_lines"] = _brief(got, "ok", "rule", "reason", "screen", "error") if isinstance(got, dict) else {
        "error": str(got)[:300]}
    if not (code == 0 and isinstance(got, dict) and got.get("ok") is True and got.get("judged") is True):
        note["failed"] = "the skit's hard lines did not pass it"
        return None
    if room.dry:
        note["planned"] = True
        return []
    wait = min(room.cfg.skit_secs, room.left() - SKIT_RESERVE_SECS - SKIT_CUT_SECS)
    if wait < 120:
        note["failed"] = f"not enough of the run's time left to render a skit ({room.left()} s)"
        return None
    body = {"shots": skit["shots"], "aspect": skit.get("aspect") or "16:9", "story": skit["story"],
            "figures": skit.get("figures") or [], "logline": skit["logline"], "wait_seconds": int(wait),
            **{k: skit[k] for k in SKIT_HELD if skit.get(k)}}
    code, made = room.budgeted(["video", "skit"], body, int(wait) + SKIT_CUT_SECS + 300, dry=False)
    if code != 0 or not isinstance(made, dict):
        note.update(failed="refused" if code == 2 else "not made",
                    result=_brief(made, "refused", "rule", "error", "reason", "asset_id", "spent_usd")
                    if isinstance(made, dict) else str(made)[:300])
        return None
    if made.get("dry_run"):
        # This room is live (a dry room returned above), so a hand under
        # DRY_RUN made no video: the words never go out without it.
        note.update(dry_run=True, failed="the video hand is in DRY_RUN: no video was made")
        return None
    asset = made.get("asset") or {}
    detail = asset.get("detail") if isinstance(asset.get("detail"), dict) else {}
    note.update(asset=asset.get("id"), cost_usd=made.get("cost_usd"), models=made.get("models"),
                label=made.get("label"))
    frames = made.get("frames") if isinstance(made.get("frames"), list) else []
    # The producer hands over only a labelled file: the hand set the label
    # and the reel service reports it burned in, for the whole cut.
    if not (asset.get("path") and made.get("labelled") is True and detail.get("labelled") is True
            and str(made.get("label") or "").startswith("AI parody") and frames):
        note["failed"] = "made, but no labelled file came back"
        return None
    # The viewer on the cut's frames: the label read, nothing a skit may never
    # show, nothing that passes for real footage, the logline taken from it.
    ok, judged, note["viewer"] = _judge(room, "viewer", ["guard", "view-skit"],
                                        {"frames": frames, "label": made["label"], "intent": skit["logline"][:400]},
                                        240, "ok", ("ok", "sees", "labelled", "prohibited", "looks_real", "reason",
                                                    "judged"))
    if not ok:
        note["failed"] = "the viewer check did not pass it"
        return None
    code, up = room.budgeted(["x", "upload", "--file", str(asset["path"])], None, 300, dry=False)
    if not (code == 0 and isinstance(up, dict) and isinstance(up.get("media_id"), str)):
        note.update(failed="upload failed", result=_brief(up, "refused", "rule", "error"))
        return None
    note["media_id"] = up["media_id"]
    return [up["media_id"]]


def skit_outcome(draft: Json, note: Json, post_id: str | None) -> Json:
    """What the record keeps of a skit that went out (or would have), so
    the reach feedback can set skits beside the other formats: the story
    it staged, the figures, the models, what it cost, the post."""
    skit = draft.get("skit") or {}
    return {"story": skit.get("story"), "figures": skit.get("figures") or [], "logline": skit.get("logline"),
            "shots": len(skit.get("shots") or []), "asset": note.get("asset"), "models": note.get("models"),
            "cost_usd": note.get("cost_usd"), "label": note.get("label"), "post_id": post_id,
            "planned": bool(note.get("planned"))}


def shape(raw: Any, card: Json, study: Json | None) -> tuple[Json, str]:
    skit, bad = skit_of(raw, card)
    return {"skit": skit}, bad


def shown(skit: Json) -> Json:
    """What the editor sees of a skit: who, the premise, the logline, what
    holds across the film, and each shot's prompt."""
    return {"figures": skit.get("figures") or [], "premise": skit.get("premise"), "logline": skit.get("logline"),
            **{k: skit[k] for k in SKIT_HELD if skit.get(k)},
            "shots": [s.get("prompt") for s in skit.get("shots") or []]}


def video_secs(room: "Room") -> int:
    """What a skit's render may take from now: never into the last
    SKIT_RESERVE_SECS + SKIT_CUT_SECS of the run."""
    return room.left() - SKIT_RESERVE_SECS - SKIT_CUT_SECS


#: The least render a skit is started with (`video_secs`).
SKIT_MIN_SECS = 120

FORMAT = Format(
    name="skit", ask=SKIT_ASK, draft_shape=SKIT_SHAPE, rules=SKIT_RULES,
    shape=shape, shown=shown, make=_make_skit, outcome=skit_outcome,
    time=video_secs, min_secs=SKIT_MIN_SECS, spent=("asset",))
