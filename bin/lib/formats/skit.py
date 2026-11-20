"""The `skit` format: a real story, a public figure in it, an impossible
scene, on video (docs/architecture.md, "The studio"). Its prompt blocks, its
shape and hard lines in code before the tournament (`skit_of`,
`skit_screen`), its producer (`_make_skit`) and what the record keeps of it
(`skit_outcome`), as `FORMAT`. Moved out of bin/lib/studio.py, which still
answers to each of these names."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

from formats import Format
from studio_room import Json, _brief, _judge, _words

if TYPE_CHECKING:
    from studio_room import Room

#: The repository (bin/lib/formats/skit.py is three below it).
REPO = Path(__file__).resolve().parents[3]


# --- skits: a real story, a public figure in it, an impossible scene, on video --------
# (hands/video skit, cut and labelled on the reel service; docs/architecture.md,
# "The studio"). Off unless STUDIO_FORMATS names skit, and hands/video refuses
# every skit until the operator sets MEDIA_SKIT_MAX_PER_DAY. The hard lines for
# real people (hands/lib/teeny_hands/guard/skit.py) run three times: in code on
# every draft before the tournament (`skit_screen`), in code and through the
# judge before any money is spent (`hands/guard skit`), and on the cut's frames
# after it is rendered (`hands/guard view-skit`). A skit that cannot be made is
# no post: the room falls back to the next draft, and in a skit slot
# (STUDIO_SKIT_SLOTS) a skit's failure does not use up the fallback budget.

SKIT_PITCH = ("A skit is a post with a short AI-made parody video: take one of the trending stories and stage the "
              "public figure it is about (the executive, the politician, the celebrity in the headline) in an "
              "obviously impossible, absurd, expensive-looking scene, three or four cinematic shots. Its premise is "
              "what that figure really did or claimed in the story, pushed to an impossible end: \"X did Y, so X "
              "now does Z\", never a pun on a word in the headline. Pitch one when a story has a famous face and a "
              "picture in it so ridiculous a stranger would stop scrolling. A skit always has a hook.")
SKIT_SLOT_LINE = ("This is a skit slot. Pitch at least three skits on the biggest trending stories that have a famous "
                  "face in them, rank the best skit first, and keep at least one pitch in another format in your "
                  "top three: when the video cannot be made, that is what goes out.")
SKIT_SHAPE = ('{"text": "<the post\'s own words>", "skit": {"figures": ["<the public figure(s) from the story, '
              'as the story names them>"], "premise": "<one line: what they really did or claimed in the story, '
              'so what they do in the video>", "logline": "<one line: the impossible scene and its joke>", '
              '"look": {"<each figure, as in figures>": "<one line: hair, glasses, wardrobe, the prop they hold, '
              'the same in every shot>"}, "setting": "<one line: the one place, its light, the lens and the film '
              'look>", "score": "<one line: the one piece of music or sound bed under the whole film>", '
              '"shots": [{"prompt": "<one shot: the framing, the figure\'s full name and the one thing they do, '
              'at most one short spoken line in quotes, spot sound effects; never the look, the setting or any '
              'music again>", "seconds": <3 to 6, optional>}, "... 3 or 4 shots: set-up, consequence, '
              'payoff"], "aspect": "16:9"}, "meaning": "...", "open_question": "<optional>"}')
SKIT_BODY = ("one post of at most 280 characters as X counts them, with a short parody video of three or four shots "
             "(each 3 to 6 seconds) that stages the public figure from the news in an obviously impossible scene. "
             "The video is made as one take: one film of up to 20 seconds in all, its shots joined by hard cuts, "
             "with the same face, the same set and one continuous score throughout. Start from the premise, not a "
             "pun: what the figure really did or claimed in the story, pushed to its impossible conclusion, said "
             "in one sentence (\"X did Y, so X now does Z\"). The figure drives every shot: they act, never stand "
             "and watch the scenery. The impossible thing is in frame in every shot: never a plain close-up of the "
             "figure, and never an ordinary room or street that could pass for real footage. Three beats with cause and effect: the first shot sets up the real thing so "
             "plainly that someone with the sound off and no caption gets what the story is; the second is its "
             "consequence, escalating; the last is the payoff that follows from the first two, never a new "
             "unrelated picture. What holds across the film is said once, as data: `look`, one line a figure "
             "(hair, glasses, wardrobe, the prop they hold); `setting`, one line (the one place, its light, the "
             "lens, the film look); `score`, one line (the single piece of music or sound bed under the whole "
             "film). The shots then say only what changes: the framing, the figure's full name and the one thing "
             "they do, at most one short spoken line (about six words), and spot sound effects. No shot brings "
             "its own music: the score is the only music. One action a shot that fits its seconds. The video is "
             "where the money is: write the setting and each shot as a film director briefs a cinematographer, "
             "in rich concrete words: the set (expensive, specific, absurd: a marble boardroom on the ocean "
             "floor, a gold-plated launch pad in a cathedral), the lens and the light (anamorphic wide, macro, "
             "dolly in, low angle, golden hour, neon rim light, film grain, shallow depth of field), the action "
             "and its visual gag, and the sound it makes. The shots escalate: the second is bigger than the "
             "first, the last is the most absurd, loud and lavish. The post's own words say what the story is "
             "in a clause and frame the video as your parody, never as real footage")
SKIT_RULES = (
    "The skit's own rules, on top of those (a skit is the one place a real person is staged, and only like this):\n"
    "- Only the public figure the story is about: the executive, the politician, the celebrity in the "
    "headline, named as the story names them. Never a private person, a child, a family member, or "
    "someone who is only a victim or a bystander in it.\n"
    "- Impossible on its face, in every shot: nobody could take any of it for real footage, a real "
    "statement or a real event. No news framing, no phone video, no press conference, no 'leaked'.\n"
    "- Crude, loud, ridiculous and undignified in a slapstick way is fine. Nothing sexual, nothing violent "
    "done to them, nothing hateful, and no crime, drugs, illness or death shown as theirs.\n"
    "- The figure never meets you, $TINY, a token, a wallet, a product or an investment, and never says "
    "a line that sounds like a real quote on a real issue (an election, a policy, money, a product claim): "
    "their lines are about the impossible scene.\n"
    "- No logos or brands shown as endorsing anything; no @-mentions and no links.\n"
    "- The video carries an 'AI parody' label the whole way through; the post's words never call it real, "
    "leaked or caught on camera.")
#: What a skit leaves of the run's time for what comes after its render (the
#: viewer, the upload, the post, and a fallback when it fails), and the most
#: its cut may take on the reel service.
SKIT_RESERVE_SECS = 600
SKIT_CUT_SECS = 300
#: The longest shot the studio hands on, in seconds (`skit_of`). A longer
#: `seconds` is cut to this, never refused: on kling-v3-pro a 6 s shot
#: quoted $0.7215 (about $1.44 reserved, under MEDIA_SKIT_MAX_USD_PER_SHOT's
#: $1.60) while an 8 s one needed $1.924 reserved, which
#: hands/lib/teeny_hands/media/skits.py cancels, failing the whole skit to a
#: text fallback; every other model is priced about the same (2026-11-20).
SKIT_SHOT_MAX_SECS = 6
#: The longest premise kept (`skit_of`): one line.
SKIT_PREMISE_MAX = 300
#: What a skit holds as data across its whole film (`skit_of` requires them;
#: the guard's shape checks and keeps them): each figure's look, the
#: setting, the score.
SKIT_HELD = ("look", "setting", "score")

#: What the editor is told wherever it meets a skit (the comparisons and the
#: slot question). 2026-11-19, the first live skit slot: with no word on
#: skits, the editor said no to one for "giving a real person a role the
#: report never gave her" and "the pun is too thin to be worth that risk",
#: holding a labelled parody to the rules for an ordinary post. The lines
#: are the writer's (SKIT_RULES, writer.md "skits") and the code screen's
#: (`skit_screen`, hands/guard skit), which still run on every skit.
SKIT_EDITOR = (
    "## judging a skit\n\n"
    "A skit is a post with a short AI-made parody video, labelled \"AI parody\" on screen from start to end. It "
    "is the one place a real person is staged: a public figure named in the story it joins, in an obviously "
    "impossible scene, in a role the story never gave them, with the joke landing on them (crude, loud, "
    "undignified slapstick). That is the whole point of the format and is not a reason to say none, or to rank "
    "a skit lower. \"Anything made up about a real person as if it happened\" does not apply to a scene nobody "
    "could take as real; the post's own words are still held to the news like any draft's. `premise` is what "
    "the writer says the figure really did, and what they do in the video because of it. Judge a skit as a "
    "stranger with the sound off meets it: do they get what the story is from the first shot, does the figure "
    "drive every shot, does each shot follow from the one before, is it absurd enough to stop for.\n\n"
    "A skit loses outright, and is a none in the slot question, for these and only these:\n"
    "- anything sexual;\n"
    "- violence done to the figure;\n"
    "- hate;\n"
    "- crime, drugs, illness or death shown as theirs;\n"
    "- a private person, a child, a family member, or someone who is only a victim or a bystander in the story;\n"
    "- the figure near Teeny, $TINY, a token, a wallet, an investment, or a product or brand they are seen to "
    "endorse (the thing the story is about, their own car or chip, may be in the scene);\n"
    "- a scene or a line that could pass for real footage or a real quote on a real issue;\n"
    "- a skit a stranger could not follow at all.")
#: And in the slot question on a skit: taste is for the comparisons.
SKIT_FINAL = ("Taste alone is not a none for a skit: \"the pun is thin\", \"the joke is not on teeny\", \"it gives "
              "them a role the story did not\", \"not worth the risk\" are for the comparisons, which it has "
              "been through. When you say none to a skit, name the line it crosses in `line`.")
#: The slot question's opening on a skit asked first because it is a skit slot.
SKIT_SLOT_OPENING = ("This is a skit slot: the best skit is asked first, wherever the comparisons ranked it. Is it "
                     "worth a slot on X tonight?")


def _skit_rules():
    """The guard's skit module (hands/lib/teeny_hands/guard/skit.py): the
    shape and the hard lines in code, the same code `hands/guard skit` runs."""
    lib = str(REPO / "hands" / "lib")
    if lib not in sys.path:
        sys.path.insert(0, lib)
    from teeny_hands.guard import skit as rules

    return rules


def skit_of(raw: Any, pitch: Json) -> tuple[Json, str]:
    """A draft's `skit` as kept ({story, figures, premise, logline, shots,
    aspect}), and why it is unusable ("" when it is usable). The story is
    the pitch's hook, set here, never the writer's. The premise (what the
    figure really did, so what they do in the video) is the studio's, not
    the guard's: it is shown to the editor and never rendered or posted."""
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
    if pitch.get("hook"):
        skit["story"] = pitch["hook"]
    elif not bad:
        bad = "a skit stages a trending story, and its pitch has no hook"
    return skit, bad


def skit_screen(skit: Json, words: str, hook: Json | None) -> list[str]:
    return _skit_rules().screen(skit, words, hook)


def _make_skit(room: "Room", draft: Json, note: Json) -> list[str] | None:
    skit = draft.get("skit") or {}
    shots = [s.get("prompt") for s in skit.get("shots") or [] if isinstance(s, dict)]
    note.update(story=skit.get("story"), figures=skit.get("figures"), logline=skit.get("logline"), shots=len(shots))
    hook = next((f for f in draft["truth"]["facts"] if isinstance(f, dict) and f.get("id") == skit.get("story")), None)
    if not shots or not skit.get("logline") or hook is None:
        note["failed"] = "the draft has no skit, or no story it stages"
        return None
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


def check_pitch(pitch: Json, sources: set[str]) -> str:
    """A skit stages a trending story: a pitch with no hook is dropped."""
    if pitch.get("hook"):
        return ""
    demoted = pitch.get("unhooked") or ""
    return (f"{demoted}: " if demoted else "") + "a skit stages a trending story, and it has no hook"


def shape(raw: Any, pitch: Json, study: Json | None) -> tuple[Json, str]:
    skit, bad = skit_of(raw, pitch)
    return {"skit": skit}, bad


def screen(draft: Json, words: str, story: Json | None) -> list[str]:
    return skit_screen(draft.get("skit"), words, story)


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
    name="skit", pitch=SKIT_PITCH, slot_line=SKIT_SLOT_LINE, draft_shape=SKIT_SHAPE, body=SKIT_BODY, rules=SKIT_RULES,
    judge_line="`skit` is the short parody video that would go with it, shot by shot",
    editor=SKIT_EDITOR, final=SKIT_FINAL, opening=SKIT_SLOT_OPENING,
    check_pitch=check_pitch, shape=shape, screen=screen, shown=shown, make=_make_skit, outcome=skit_outcome,
    time=video_secs, min_secs=SKIT_MIN_SECS, spent=("asset",))
