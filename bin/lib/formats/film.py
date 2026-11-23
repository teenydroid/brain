"""The `film` format: a story film, 60-90 seconds, three acts, Teeny
narrating a public figure's real claim pushed to an impossible end
(docs/video.md §12). Its words for the write turn, its structural check, its
producer (`_make_film`) and what the record keeps of it, as `FORMAT`.

A film is offered only on a film day (`hands/film due`: FILM_EVERY_DAYS since
the last one, and the week's film budget with room for one), and only in a
video slot. It is made inside the run, like a remake: the narration first,
then every clip at once, so a film takes about a quarter of an hour; the
slot runs on STUDIO_VIDEO_BUDGET_SECS. A film that stops part way is carried
on from its manifest by the next `hands/film run`, never bought twice.

Before any money: every text a machine is told (each beat's clip prompt, each
line of narration, each sound cue, the logline, the score) passes the guard's
`task` screen, then the film check (`hands/guard film`: the skit's hard lines
on every beat, then the judge on the whole film). After: the viewer on the
cut's frames, as a skit's, then the upload."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from formats import Format
from formats.skit import _guard_lib
from studio_room import Json, _brief, _judge, _words

if TYPE_CHECKING:
    from studio_room import Room


def _rules():
    """The guard's film module (hands/lib/teeny_hands/guard/film.py)."""
    _guard_lib()
    from teeny_hands.guard import film as rules

    return rules


FILM_ASK = ("A film is a post with a 60 to 90 second story film: one trending story, its public figure's real claim "
            "or deed pushed to an impossible, crude, escalating end, told in three acts by you, Teeny, as the "
            "narrator. You narrate in your own voice, first person, deadpan, short lines; the figure never speaks a "
            "line that could pass for a real quote. Pitch one when a story is big enough to carry a minute: a clear "
            "real claim, a famous face, and a ridiculous place to take it.")
FILM_SHAPE = ('{"text": "<the post\'s own words: the story in a clause, framed as your parody>", "film": {'
              '"figures": ["<the public figure(s), as the story names them>"], "logline": "<one line: the whole '
              'film and its joke>", "look": {"<each figure>": "<one line: hair, wardrobe, the prop they hold>"}, '
              '"setting": "<one line: the one impossible place, its light, the lens>", "score": "<one line: the '
              'one piece of music under the whole film>", "aspect": "9:16", "beats": [{"subject": "figure" or '
              '"teeny", "action": "<one shot: the framing, who does what, what reacts to it, the sound it makes; '
              'no music, no lettering>", "narration": "<your line over this beat, at most 400 characters; empty '
              'for a beat of pure action>", "sfx": ["<at most 3 short sound effects in this beat>"], "hold": '
              '<seconds, only for a beat with no narration, 1 to 8>}, "... 5 to 7 beats"]}}')
FILM_RULES = (
    "A film's own rules, on top of the skit's (every skit rule holds for every beat):\n"
    "- Three acts in five to seven beats: set-up (the real claim, plain enough to get with the sound off, by the "
    "first 3 seconds), escalation (each beat bigger, each following from the last), payoff (the last beat pays off "
    "the first; ending on the first image again is welcome). 60 to 90 seconds in all.\n"
    "- The narration is the clock: every beat lasts as long as you take to say its line (plus a breath), and no beat "
    "may run past 15 seconds, so at most about 35 words a beat. Write the narration to be heard: short sentences, "
    "the joke on the last word. All of it together at most 2,500 characters.\n"
    "- A beat shows the figure or you, never both: a real figure never meets you. Your beats are your reactions "
    "and asides, in the same set.\n"
    "- The crude voice goes on the ceremony, the props and the sounds (a vault that farts, a trophy of a "
    "suspicious shape), never on the figure's body. Name the sounds in `sfx`; nobody on screen speaks.\n"
    "- Every beat's action is a rich shot a cinematographer could film, never a list of lens words; no sign, screen "
    "or banner with writing on it.")
FILM_EDITOR = ("A film is a story: judge whether its first beat makes the claim plain, whether every beat escalates "
               "from the one before, and whether the last pays off the first. A film that is only a list of gags "
               "is weaker than a short skit.")
#: What a film's make may take of the run, and the least it is started with.
FILM_RESERVE_SECS = 600
FILM_MIN_SECS = 900


def film_of(raw: Any, card: Json) -> tuple[Json, str]:
    """The film as the guard's own `shape` keeps it, and why it cannot be
    made ("" when it can). The story is the card's."""
    film, bad = _rules().shape(raw)
    if not bad and isinstance(raw, dict) and isinstance(raw.get("premise"), str):
        film["premise"] = raw["premise"][:300]
    film["story"] = card.get("id")
    return film, bad


def shape(raw: Any, card: Json, study: Json | None) -> tuple[Json, str]:
    film, bad = film_of(raw, card)
    return {"film": film}, bad


def shown(film: Json) -> Json:
    """What the editor sees: who, the logline, the set, and every beat's
    subject, action and line."""
    return {"figures": film.get("figures") or [], "logline": film.get("logline"),
            **{k: film[k] for k in ("look", "setting", "score") if film.get(k)},
            "beats": [{k: b.get(k) for k in ("subject", "action", "narration", "sfx") if b.get(k)}
                      for b in film.get("beats") or []]}


def offered(room: "Room") -> bool:
    """Whether a film is due this run: `hands/film due` says so. A hand that
    does not answer is no film, never a guess."""
    code, due = room.budgeted(["film", "due"], None, 30)
    return code == 0 and isinstance(due, dict) and due.get("due") is True


def _make_film(room: "Room", draft: Json, note: Json) -> list[str] | None:
    film = draft.get("film") or {}
    hook = draft.get("story") if isinstance(draft.get("story"), dict) else None
    beats = film.get("beats") or []
    note.update(story=film.get("story"), figures=film.get("figures"), logline=film.get("logline"), beats=len(beats))
    if not beats or not film.get("logline") or hook is None:
        note["failed"] = "the draft has no film, or no story it stages"
        return None
    rules = _rules()
    try:
        prompts = [rules.beat_prompt(film, i, rules.PROMPT_SECONDS) for i in range(len(beats))]
    except (KeyError, TypeError, AttributeError):
        note["failed"] = "the draft's film could not be composed into prompts"
        return None
    texts = [("logline", film["logline"]), *((k, film[k]) for k in ("setting", "score") if film.get(k)),
             *((f"beat {n} prompt", p) for n, p in enumerate(prompts, 1)),
             *((f"beat {n} narration", b["narration"]) for n, b in enumerate(beats, 1) if b.get("narration")),
             *((f"beat {n} sound", s) for n, b in enumerate(beats, 1) for s in b.get("sfx") or [])]
    for what, text in texts:
        code, checked = room.budgeted(["guard", "check"], {"text": text, "surface": "task"}, 120)
        if not (code == 0 and isinstance(checked, dict) and isinstance(checked.get("text"), str)):
            note.update(failed=f"the guard held its {what}", guard=_brief(checked, "refused", "rule", "category",
                                                                           "error"))
            return None
    body = {"film": {k: v for k, v in film.items() if k not in ("story", "premise")} | {"text": _words(draft)},
            "story": hook}
    code, got = room.budgeted(["guard", "film"], body, 200)
    note["hard_lines"] = _brief(got, "ok", "rule", "reason", "screen", "error") if isinstance(got, dict) else {
        "error": str(got)[:300]}
    if not (code == 0 and isinstance(got, dict) and got.get("ok") is True and got.get("judged") is True):
        note["failed"] = "the film's hard lines did not pass it"
        return None
    if room.dry:
        note["planned"] = True
        return []
    wait = room.left() - FILM_RESERVE_SECS
    if wait < FILM_MIN_SECS:
        note["failed"] = f"not enough of the run's time left to make a film ({room.left()} s)"
        return None
    code, made = room.budgeted(["film", "make"], body, int(wait), dry=False)
    if code != 0 or not isinstance(made, dict):
        note.update(failed="refused" if code == 2 else "not made",
                    result=_brief(made, "refused", "rule", "error", "reason") if isinstance(made, dict)
                    else str(made)[:300])
        return None
    if made.get("dry_run"):
        note.update(dry_run=True, failed="the film hand is in DRY_RUN: no film was made")
        return None
    cut = made.get("cut") if isinstance(made.get("cut"), dict) else {}
    note.update(asset=made.get("id"), cost_usd=made.get("spent_usd"), stills=made.get("stills"),
                label=cut.get("label"))
    frames = cut.get("frames") if isinstance(cut.get("frames"), list) else []
    if not (made.get("status") == "done" and cut.get("path") and str(cut.get("label") or "").startswith("AI parody")
            and frames):
        note["failed"] = "made, but no labelled film came back"
        return None
    ok, judged, note["viewer"] = _judge(room, "viewer", ["guard", "view-skit"],
                                        {"frames": frames, "label": cut["label"], "intent": film["logline"][:400]},
                                        240, "ok", ("ok", "sees", "labelled", "prohibited", "looks_real", "reason",
                                                    "judged"))
    if not ok:
        note["failed"] = "the viewer check did not pass it"
        return None
    code, up = room.budgeted(["x", "upload", "--file", str(cut["path"])], None, 300, dry=False)
    if not (code == 0 and isinstance(up, dict) and isinstance(up.get("media_id"), str)):
        note.update(failed="upload failed", result=_brief(up, "refused", "rule", "error"))
        return None
    note["media_id"] = up["media_id"]
    return [up["media_id"]]


def film_outcome(draft: Json, note: Json, post_id: str | None) -> Json:
    film = draft.get("film") or {}
    return {"story": film.get("story"), "figures": film.get("figures") or [], "logline": film.get("logline"),
            "beats": len(film.get("beats") or []), "asset": note.get("asset"), "stills": note.get("stills"),
            "cost_usd": note.get("cost_usd"), "label": note.get("label"), "post_id": post_id,
            "planned": bool(note.get("planned"))}


def video_secs(room: "Room") -> int:
    return room.left() - FILM_RESERVE_SECS


FORMAT = Format(
    name="film", ask=FILM_ASK, draft_shape=FILM_SHAPE, rules=FILM_RULES,
    shape=shape, shown=shown, make=_make_film, outcome=film_outcome,
    time=video_secs, min_secs=FILM_MIN_SECS, spent=("asset",), offered=offered)
