"""The story film check (docs/video.md §12): a 60-90 s narrated film of
three to eight beats, each its own clip, staging a public figure from a real
news story. It is the skit check (guard/skit.py) applied to every beat, plus
what a film adds: Teeny's narration, the sound effects and a whole-film
judge.

A beat shows one of two things:

- `figure`: the public figure in the impossible scene, as a skit shot does.
  Every skit rule holds, and the skit screen runs on the beat as a one-shot
  skit (`as_skit`). Teeny is not in frame: it narrates off screen.
- `teeny`: Teeny alone. The skit screen's Teeny words would refuse it, so
  it gets the screen a Teeny-only frame gets (HARM_TEENY: nothing sexual, no
  hate), no money words, no real-footage framing, no link, and none of the
  film's figures named in it.

A beat with both is refused: a real figure never meets Teeny (skit rule d),
and moving that line is the operator's call (docs/video.md §8.2).

What code adds for the film as a whole, before any money is spent:

- the narration is Teeny's words about the figure, so it is screened as the
  post's words are (no real-footage framing, no link, no token pitch) and for
  (a) and the sexual and hate words of (c);
- every sound effect cue is screened as a shot is;
- every beat's video prompt (`beat_prompt`, exactly what is sent) fits the
  guard's `task` cap on its own: the cap is per clip, never per film.

Then `check` asks the skit judge once, with every beat as a shot and the
narration beside the post's words. It fails closed, as the skit judge does.
"""

from __future__ import annotations

from typing import Any

from .. import hand
from . import skit as rules
from .gate import LIMITS
from .judge import JUDGE_BUDGET, JUDGE_TIMEOUT, _answer, ask

Json = dict[str, Any]

BEATS = (3, 8)
SUBJECTS = ("figure", "teeny")
MAX_NARRATION = 400
#: The whole narration is read in one call; ElevenLabs v3 takes 2,500
#: characters on Surplus (media/audio.py).
MAX_NARRATION_TOTAL = 2500
MAX_SFX = 3
MAX_SFX_CHARS = 120
HOLD = (1, 8)
#: The longest clip a beat may ask for (media/narration.py BEAT_MAX).
PROMPT_SECONDS = 15

#: Said to the skit judge before its own rules: what a film is, and whose
#: words the narration is.
FILM_NOTE = ("This is a story film, not a single short: each shot below is one beat of a 60-90 second film, "
             "rendered as its own clip and cut together. The narration is spoken off screen by Teeny, the "
             "account's own cartoon robot, in its own voice: it is Teeny's words about the figure, never the "
             "figure's. It is given with the post's words. Judge the film as a whole by the rules that follow.")


def task_cap() -> int:
    return int(LIMITS["task"]["text"])


def _strings(value: Any, cap: int, most: int) -> list[str] | None:
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > most:
        return None
    out = []
    for item in value:
        clean = rules._line(item, cap + 1)
        if not clean or len(clean) > cap:
            return None
        out.append(clean)
    return out


def shape(raw: Any) -> tuple[Json, str]:
    """A film as kept ({figures, logline, look?, setting?, score?, aspect,
    text, beats: [{id, subject, action, narration, sfx, hold?}]}) and why it
    is unusable ("" when it is usable). The figures, logline, look, setting,
    score and aspect are kept by the skit's own `shape`."""
    if not isinstance(raw, dict):
        return {}, "a film is an object with its beats"
    beats_raw = raw.get("beats")
    if not isinstance(beats_raw, list) or not BEATS[0] <= len(beats_raw) <= BEATS[1]:
        return {}, f"a film is {BEATS[0]} to {BEATS[1]} beats"
    head, bad = rules.shape({k: raw.get(k) for k in ("figures", "logline", "look", "setting", "score", "aspect")}
                            | {"shots": [{"prompt": "x" * rules.MIN_SHOT_CHARS}]})
    head.pop("shots", None)
    if bad:
        return head, bad
    film: Json = {**head, "text": raw.get("text") if isinstance(raw.get("text"), str) else "", "beats": []}
    total = 0
    for n, beat in enumerate(beats_raw, 1):
        if not isinstance(beat, dict):
            return film, f"beat {n} is not an object"
        subject = beat.get("subject")
        if subject == "both":
            return film, (f"beat {n} puts the figure and Teeny in one frame: a real figure never meets Teeny "
                          "(docs/video.md §8.2 is the operator's call)")
        if subject not in SUBJECTS:
            return film, f"beat {n}'s subject is one of {', '.join(SUBJECTS)}"
        if subject == "figure" and not film["figures"]:
            return film, f"beat {n} shows a figure, and the film names none"
        action = rules._line(beat.get("action"), rules.MAX_SHOT_CHARS + 1)
        if not rules.MIN_SHOT_CHARS <= len(action) <= rules.MAX_SHOT_CHARS:
            return film, (f"beat {n}'s action is a rich shot of {rules.MIN_SHOT_CHARS} to "
                          f"{rules.MAX_SHOT_CHARS} characters")
        narration = rules._line(beat.get("narration"), MAX_NARRATION + 1)
        if len(narration) > MAX_NARRATION:
            return film, f"beat {n}'s narration is over {MAX_NARRATION} characters"
        total += len(narration)
        sfx = _strings(beat.get("sfx"), MAX_SFX_CHARS, MAX_SFX)
        if sfx is None:
            return film, f"beat {n}'s sound effects are at most {MAX_SFX} lines of {MAX_SFX_CHARS} characters"
        kept: Json = {"id": f"b{n}", "subject": subject, "action": action, "narration": narration, "sfx": sfx}
        hold = beat.get("hold")
        if hold is not None:
            if isinstance(hold, bool) or not isinstance(hold, (int, float)) or not HOLD[0] <= hold <= HOLD[1]:
                return film, f"beat {n}'s hold is {HOLD[0]} to {HOLD[1]} seconds"
            kept["hold"] = hold
        if not narration and hold is None:
            kept["hold"] = 3
        film["beats"].append(kept)
    if not total:
        return film, "a film has no narration"
    if total > MAX_NARRATION_TOTAL:
        return film, f"the narration is over {MAX_NARRATION_TOTAL} characters in all"
    return film, ""


def as_skit(film: Json, index: int) -> Json:
    """A figure beat as a one-shot skit, for the skit's own screen."""
    skit = {k: film[k] for k in ("figures", "logline", "look", "setting", "score", "aspect") if k in film}
    skit["shots"] = [{"prompt": film["beats"][index]["action"]}]
    return skit


def beat_prompt(film: Json, index: int, seconds: int) -> str:
    """The one prompt a beat's clip is made from. No music: the film's score
    is laid under the whole cut, and a clip's own sound is kept only as an
    effects stem. A Teeny beat names no figure's look."""
    beat = film["beats"][index]
    held = rules._held(film) if beat["subject"] == "figure" else (
        [f"Location: {film['setting'].rstrip('.')}, the same in every shot."] if film.get("setting") else [])
    parts = [f"A {seconds}-second shot from a cinematic comedy film, one continuous take.", *held, beat["action"],
             "No music: only the sounds the action makes.", rules.LOCKS]
    return " ".join(parts)


def _teeny_reasons(film: Json, beat: Json, text: str) -> list[str]:
    name = beat["id"]
    reasons = []
    if (hit := rules._found(rules.HARM_TEENY, text)):
        reasons.append(f"{name} shows {hit!r}: nothing sexual and no hate symbol or slur, even for Teeny")
    if (hit := rules._found(rules.re.compile(r"\b(" + rules.MONEY_WORDS + r")\b", rules.re.I), text)):
        reasons.append(f"{name} brings in {hit!r}: no token, coin, wallet or investment on screen")
    for figure in film["figures"]:
        if rules.named_in(figure, text.lower()):
            reasons.append(f"{name} is Teeny's, and names {figure}: a real figure never meets Teeny")
    return reasons


def screen(raw: Any, story: Any) -> list[str]:
    """Why code refuses the film, every reason (an empty list: code lets it
    through to the judge)."""
    film, bad = shape(raw)
    if bad:
        return [bad]
    words = film["text"]
    if not words.strip():
        return ["a film goes out with the post's own words, and there are none"]
    reasons: list[str] = []
    cap = task_cap()
    for i, beat in enumerate(film["beats"]):
        name = beat["id"]
        prompt = beat_prompt(film, i, PROMPT_SECONDS)
        if len(prompt) > cap:
            reasons.append(f"{name}'s video prompt is {len(prompt)} characters, over the {cap} a clip may send")
        if beat["subject"] == "figure":
            reasons += [f"{name}: {why}" for why in rules.screen(as_skit(film, i), words, story)]
        else:
            if not rules.story_text(story):
                reasons.append("a film stages a real news story, and none was given")
            for pattern in (rules.NOT_A_FIGURE, rules.REAL_CLAIM, rules.LINKS):
                if (hit := rules._found(pattern, prompt)):
                    reasons.append(f"{name} shows {hit!r}")
            reasons += _teeny_reasons(film, beat, prompt)
        cues = " ".join(beat["sfx"])
        for pattern, why in ((rules.HARM, "nothing sexual and no hate in a sound"),
                             (rules.ENDORSE_SHOT, "no Teeny, token or money in a sound"),
                             (rules.LINKS, "no link or handle in a sound")):
            if cues and (hit := rules._found(pattern, cues)):
                reasons.append(f"{name}'s sound effects say {hit!r}: {why}")
    told = " ".join(b["narration"] for b in film["beats"])
    for pattern, why in ((rules.NOT_A_FIGURE, "never a minor, a family member, a victim or a private person"),
                         (rules.REAL_CLAIM, "a film is an obvious parody, never footage"),
                         (rules.LINKS, "no link or handle"),
                         (rules.HARM, "nothing sexual about a real person, and no hate symbol or slur"),
                         (rules.ENDORSE_WORDS, "no token pitch beside a real figure (the scam pattern)")):
        if (hit := rules._found(pattern, told)):
            reasons.append(f"the narration says {hit!r}: {why}")
    return list(dict.fromkeys(reasons))


def judged_words(film: Json) -> str:
    """The post's words and Teeny's narration, as the judge reads them."""
    told = " ".join(b["narration"] for b in film["beats"] if b["narration"])
    return f"{film['text']}\n\nTeeny's narration over the film: {told}"


def check(raw: Any, story: Any) -> Json:
    """{ok, rule, reason, screen, judged}: ok only when code finds nothing and
    the judge clearly allows the whole film. Never raises on what it is
    given; an unreachable judge is a no with its reason. Every check is a row
    in `skit_checks`, its logline marked as a film's."""
    film, _ = shape(raw)
    words = judged_words(film) if film.get("beats") else ""
    marked = {**film, "logline": f"[film] {film.get('logline', '')}"}
    reasons = screen(raw, story)
    if reasons:
        verdict = {"ok": False, "rule": "screen", "reason": "; ".join(reasons), "screen": reasons, "judged": True}
        rules._keep(marked, words, story, "screen", verdict)
        return verdict
    shots = {**{k: film[k] for k in ("figures", "logline", "look", "setting", "score") if k in film},
             "shots": [{"prompt": f"[{b['subject']}] {b['action']}" + (f" Sounds: {'; '.join(b['sfx'])}."
                                                                       if b["sfx"] else "")}
                       for b in film["beats"]]}
    params: Json = {"message": f"{FILM_NOTE}\n\n{rules.prompt(shots, words, story)}"}
    model = hand.setting("SAFETY_JUDGE_MODEL")
    if model:
        params["model_override"] = model
    got, error = ask(params, lambda reply: rules.read(_answer(reply)), budget=JUDGE_BUDGET, timeout=JUDGE_TIMEOUT)
    if got is None:
        verdict = {"ok": False, "rule": "unknown",
                   "reason": rules.UNREADABLE if error is None else f"{rules.UNREACHABLE}: {error}",
                   "screen": [], "judged": True}
    else:
        verdict = {**got, "screen": [], "judged": True}
    rules._keep(marked, words, story, "judge", verdict)
    return verdict
