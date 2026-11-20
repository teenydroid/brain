"""The `khaby` format: a Khaby Lame video with its silly setup kept exactly as
filmed and his wordless deadpan reaction re-made with Teeny standing in his
place, the post's words joining it to the day's trending story
(docs/architecture.md, "The studio", "Formats"). Its prompt blocks, the
videos of his it reads (`load_finds`) and the dissect of one before its
writer turn (`dissect_source`), its shape and hard lines in code before the
tournament (`khaby_of`, `khaby_screen`), its producer (`_make_khaby`) and
what the record keeps of it (`khaby_outcome`), as `FORMAT`.

hands/khaby finds and dissects the source; hands/video khaby keys, render
and drop make it, cut with the original's sound and labelled on the reel
service. Off unless STUDIO_FORMATS names khaby (and the video hand's
MEDIA_KHABY_MAX_PER_DAY is set), and only in a video slot
(STUDIO_SKIT_SLOTS): one video a day between the video formats. The hard
lines (hands/lib/teeny_hands/guard/khaby.py) run in code on every draft
before the tournament, in code and through the judge before any money
(`hands/guard khaby`), on the keyframes before any video money and on the
cut's frames after (`hands/guard view-khaby`)."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from formats import Format
from formats.remake import REMAKE_KEYS_SECS, REMAKE_KEYS_SPARE, REMAKE_MIN_SECS, REMAKE_STORY_MAX, chart_items
from formats.skit import SKIT_CUT_SECS, SKIT_RESERVE_SECS, _skit_rules
from studio_room import DATA_LINE, Json, _brief, _judge, _str, _words

if TYPE_CHECKING:
    from studio_room import Room


KHABY_PITCH = ("A khaby is a post with a short AI-made parody video that answers one of Khaby Lame's videos below "
               "(`khaby`): his setup, someone's silly, over-complicated clip, is kept exactly as it was filmed, and "
               "his wordless deadpan reaction is re-made with you standing in his place, doing the plain, obvious "
               "thing with a twist of your own. Pitch one when one of his videos and a trending story fit each "
               "other: the story is the over-complicated thing, your deadpan answer the obvious one. `item` is the "
               "Khaby video's id and `hook` the trending story. A khaby always has both.")
KHABY_SLOT_LINE = ("Khaby videos share this slot with the other videos: one video goes out, whichever the editor likes "
                   "best. Pitch a khaby when one of his videos fits a trending story, and keep your best video, of "
                   "any kind, at the top of your ranking.")
KHABY_SHAPE = ('{"text": "<the post\'s own words: the story in a clause, the original by its title and the account '
               'it came from by its name, in words, no link, no @>", "khaby": {"logline": "<one line: the setup, your '
               'deadpan answer and its twist>", "segments": [{"n": <a reaction segment\'s n>, "edit": "<how you '
               'stand in the man\'s place in its first frame: the same framing and spot, the man gone, nobody '
               'added, what you hold>", "motion": "<your deadpan beats, in his order: the look to camera, the plain '
               'fix with your twist, the open hands>"}, "... one for every reaction segment, in its order"]}, '
               '"meaning": "...", "open_question": "<optional>"}')
KHABY_BODY = ("one post of at most 280 characters as X counts them, with a short parody video that answers the Khaby "
              "Lame video below: its setup, someone's silly, over-complicated clip, plays exactly as it was filmed, "
              "then his wordless deadpan reaction is re-made with you in his place. An image model edits the "
              "reaction's first frame so you stand where the man stood (`edit`), a video model animates you "
              "(`motion`), and the original's own sound plays under it. For every reaction segment, in order: say "
              "how you stand in his place (the same framing and spot, the man gone, nobody added) and write your "
              "deadpan beats from his (the look to camera, the plain fix, the open hands), with one twist of your "
              "own that answers the story. The setup, the lengths and the frames are the timeline's, not yours. The "
              "post's words say what the story is in a clause, credit the original by its title and the account it "
              "came from by its name, in words (no link, no @), and frame the video as your parody, never as real "
              "footage")
KHABY_RULES = (
    "The khaby's own rules, on top of those (the setup is a stranger's footage, kept, and only the reaction is "
    "yours):\n"
    "- You are the only one who reacts: you alone stand in the man's place, and nobody else is drawn into the "
    "reaction.\n"
    "- No real person is named or drawn: never Khaby himself (he is taken out of the frame, and is never named in an "
    "edit, a motion or the logline), and never anyone the story names.\n"
    "- Nothing sexual, violent or degrading about anyone, Khaby included, and no crime, drugs, illness or death.\n"
    "- No token, coin, wallet, price or product, in the video or in the post's words; no logos and no endorsement.\n"
    "- The video carries an 'AI parody' label the whole way through; the post's words never call it real, carry no "
    "link and no @, and credit the account the setup came from by its name, in words.")
#: What the editor is told wherever it meets a khaby (SKIT_EDITOR's twin).
KHABY_EDITOR = (
    "## judging a khaby\n\n"
    "A khaby is a post with a short AI-made parody video, labelled \"AI parody\" on screen from start to end, that "
    "answers a Khaby Lame video (`source`): its setup, someone's silly, over-complicated clip, plays as it was filmed "
    "and is credited, and his wordless deadpan reaction is re-made with Teeny standing in his place (`reactions`). "
    "Keeping a stranger's setup is the whole point of the format and is not a reason to say none. Judge it as a "
    "stranger with the sound off meets it: do they get the setup, does Teeny's plain answer land, does the twist tie "
    "it to the story, is it funny.\n\n"
    "A khaby loses outright, and is a none in the slot question, for these and only these:\n"
    "- a real person named or drawn in the reaction, Khaby himself included;\n"
    "- anything sexual, violent or degrading;\n"
    "- crime, drugs, illness or death;\n"
    "- a token, a wallet, a price, a product or a brand;\n"
    "- words that call it real, or that do not credit the account the setup came from;\n"
    "- a setup that is news, tragedy or real harm, or shows a child;\n"
    "- a khaby a stranger could not follow at all.")
KHABY_FINAL = ("Taste alone is not a none for a khaby: \"the setup is someone else's\" is the format, not a line, and "
               "\"not worth the risk\" is for the comparisons, which it has been through. When you say none to a "
               "khaby, name the line it crosses in `line`.")
KHABY_SLOT_OPENING = ("This is a video slot: the best khaby is asked first, wherever the comparisons ranked it. Is it "
                      "worth a slot on X tonight?")
#: A khaby's time (STUDIO_KHABY_SECS covers its keys and its video): the
#: remake's floor, keys wait and spare, on the same machinery.
KHABY_MIN_SECS = REMAKE_MIN_SECS
KHABY_KEYS_SECS = REMAKE_KEYS_SECS
KHABY_KEYS_SPARE = REMAKE_KEYS_SPARE
#: The most sources dissected a run (each a paid look at a video), and how long one may take.
KHABY_DISSECTS = 2
KHABY_DISSECT_SECS = 240
#: How long `hands/video khaby drop` may take: it only releases rows.
KHABY_DROP_SECS = 60
#: What of a timeline the draft keeps (the screen and the producer read it).
TIMELINE_KEYS = ("dissect_id", "source", "aspect", "video", "audio", "segments", "beats", "joke", "flags", "usable",
                 "why")


def _khaby_rules():
    """The guard's khaby module (hands/lib/teeny_hands/guard/khaby.py)."""
    _skit_rules()
    from teeny_hands.guard import khaby as rules

    return rules


def load_finds(room: "Room", slot_id: str | None = None) -> tuple[list[Json], Json]:
    """His videos a khaby may answer (`hands/khaby find`, a read, kept by
    the hand per hour), and what came of asking. Never fails the run: none
    is no khaby."""
    code, got = room.hand_code(["khaby", "find"], timeout=120)
    items = chart_items(got) if code == 0 else []
    note: Json = {"candidates": len(items)}
    if isinstance(got, dict):
        note.update({k: got[k] for k in ("unavailable", "cached", "key", "fetched_at", "dropped_by_screen") if k in got})
    if code != 0:
        note["error"] = (str(got.get("error") or got.get("refused") or got) if isinstance(got, dict) else str(got))[:300]
    return items, note


def timeline_for_writer(timeline: Json) -> Json:
    """What the writer is told of the original: its title, the account it
    came from, the joke, each segment (its role, how long, what happens,
    whether it is the writer's to re-make) and his beats. Never a path or a
    link. Strangers' words, and a model's reading of them."""
    source = timeline.get("source") if isinstance(timeline.get("source"), dict) else {}
    segments = [{"n": s.get("n"), "role": s.get("role"),
                 "seconds": round(float(s["end"]) - float(s["start"]), 3)
                 if isinstance(s.get("start"), (int, float)) and isinstance(s.get("end"), (int, float)) else None,
                 "what": s.get("what"), "yours": s.get("editable") is True}
                for s in timeline.get("segments") or [] if isinstance(s, dict)]
    beats = [{"at": b.get("at"), "what": b.get("what")} for b in timeline.get("beats") or [] if isinstance(b, dict)]
    return {"title": source.get("title"), "by": source.get("uploader"), "on": source.get("source"),
            "joke": timeline.get("joke"), "segments": segments, "beats": beats, "untrusted": True}


def dissect_source(room: "Room", pitch: Json) -> tuple[Json | None, str]:
    """`hands/khaby dissect` on a khaby pitch's video (a read, inside the
    run's time): the timeline, or None and why it cannot be answered."""
    code, got = room.budgeted(["khaby", "dissect"], {"id": pitch["item"]}, KHABY_DISSECT_SECS)
    if code != 0 or not isinstance(got, dict):
        rule = got.get("rule") if isinstance(got, dict) else None
        why = (got.get("refused") or got.get("error")) if isinstance(got, dict) else got
        return None, f"its source could not be dissected ({rule or 'failed'}: {str(why)[:200]})"
    refused = _khaby_rules().timeline_gate(got)
    if refused:
        return got, "its source cannot be answered: " + "; ".join(refused)
    return got, ""


def khaby_of(raw: Any, pitch: Json, timeline: Json | None) -> tuple[Json, str]:
    """A draft's `khaby` as kept ({logline, segments: [{n, edit, motion,
    seconds}], setup, source, dissect, aspect, story}), and why it is
    unusable ("" when it is usable). The writer gives the words; the studio,
    never the writer, sets the source, the dissect, the story (the pitch's
    hook), the aspect, the setup and each reaction's length, from the
    timeline."""
    if not isinstance(timeline, dict):
        return {}, "a khaby needs its timeline, and its source was not dissected"
    piece, bad = _khaby_rules().shape(raw, timeline)
    source = timeline.get("source") if isinstance(timeline.get("source"), dict) else {}
    piece["source"] = {k: source.get(k) for k in ("video_id", "url", "title", "uploader", "source")}
    piece["dissect"] = timeline.get("dissect_id")
    piece["aspect"] = timeline.get("aspect") or "9:16"
    segments = [s for s in timeline.get("segments") or [] if isinstance(s, dict)]
    piece["setup"] = [s.get("what") for s in segments if s.get("role") == "setup"]
    by_n = {s.get("n"): s for s in segments}
    for seg in piece.get("segments") or []:
        cut = by_n.get(seg["n"], {})
        seg["seconds"] = round(float(cut["end"]) - float(cut["start"]), 3)
    if pitch.get("hook"):
        piece["story"] = pitch["hook"]
    elif not bad:
        bad = "a khaby joins a trending story, and its pitch has no hook"
    return piece, bad


def khaby_screen(piece: Json, words: str, hook: Json | None, timeline: Json | None) -> list[str]:
    return _khaby_rules().screen(piece, words, hook, timeline)


def _khaby_time(room: "Room") -> int:
    """What a khaby may take from now: STUDIO_KHABY_SECS, and never into the
    last SKIT_RESERVE_SECS + SKIT_CUT_SECS of the run."""
    return min(room.cfg.khaby_secs, room.left() - SKIT_RESERVE_SECS - SKIT_CUT_SECS)


def _make_khaby(room: "Room", draft: Json, note: Json) -> list[str] | None:
    piece = draft.get("khaby") or {}
    timeline = draft.get("timeline") or {}
    segments = [s for s in piece.get("segments") or [] if isinstance(s, dict)]
    source = piece.get("source") or {}
    note.update(story=piece.get("story"), logline=piece.get("logline"), source=source.get("url"),
                dissect=piece.get("dissect"), segments=len(segments))
    hook = next((f for f in draft["truth"]["facts"] if isinstance(f, dict) and f.get("id") == piece.get("story")), None)
    if not segments or not piece.get("logline") or hook is None or not timeline.get("dissect_id"):
        note["failed"] = "the draft has no khaby, no timeline of its source, or no story it joins"
        return None
    # Every word an image or a video model is told is past the guard's screen
    # and judge, as a scene is (the `task` surface).
    told = [("logline", piece["logline"])] + [(f"segment {s['n']}'s {k}", s.get(k) or "") for s in segments
                                              for k in ("edit", "motion")]
    for what, text in told:
        code, checked = room.budgeted(["guard", "check"], {"text": text, "surface": "task"}, 120)
        if not (code == 0 and isinstance(checked, dict) and isinstance(checked.get("text"), str)):
            note.update(failed=f"the guard held its {what}", guard=_brief(checked, "refused", "rule", "category", "error"))
            return None
    # The hard lines: code's screen, then the khaby judge, on every prompt,
    # the timeline and the words. Before any money; a judge that cannot
    # answer is a no.
    code, got = room.budgeted(["guard", "khaby"], {"khaby": piece, "text": _words(draft), "story": hook,
                                                  "timeline": timeline}, 200)
    note["hard_lines"] = _brief(got, "ok", "rule", "reason", "screen", "error") if isinstance(got, dict) else {
        "error": str(got)[:300]}
    if not (code == 0 and isinstance(got, dict) and got.get("ok") is True and got.get("judged") is True):
        note["failed"] = "the khaby's hard lines did not pass it"
        return None
    if room.dry:
        note["planned"] = True
        return []
    total = _khaby_time(room)
    if total < KHABY_MIN_SECS:
        note["failed"] = f"not enough of the run's time left to make a khaby video ({room.left()} s)"
        return None
    keys_wait = min(KHABY_KEYS_SECS, total - KHABY_KEYS_SPARE)
    story = ": ".join(x for x in (_str(hook.get("title"), 2000), _str(hook.get("summary"), 2000)) if x)
    body = {"dissect": piece["dissect"], "story": (story or str(piece["story"]))[:REMAKE_STORY_MAX],
            "logline": piece["logline"], "reactor": "teeny",
            "segments": [{k: s.get(k) for k in ("n", "edit", "motion")} for s in segments],
            "wait_seconds": int(keys_wait)}
    code, keyed = room.budgeted(["video", "khaby", "keys"], body, int(keys_wait) + 120, dry=False)
    if code != 0 or not isinstance(keyed, dict):
        note.update(failed="its keyframes were refused" if code == 2 else "its keyframes were not made",
                    result=_brief(keyed, "refused", "rule", "error", "reason", "khaby_id", "spent_usd")
                    if isinstance(keyed, dict) else str(keyed)[:300])
        return None
    if keyed.get("dry_run"):
        note.update(dry_run=True, failed="the video hand is in DRY_RUN: no keyframes were made")
        return None
    keys = [k for k in keyed.get("keys") or [] if isinstance(k, dict) and isinstance(k.get("path"), str)]
    note.update(khaby_id=keyed.get("khaby_id"), keys_cost_usd=keyed.get("cost_usd"),
                reserve_usd=keyed.get("reserve_usd"))
    if keyed.get("khaby_id"):
        # Keys bought: the source is spent, so neither pipeline uses it again
        # (hands/khaby used). A mark that fails is a warning, not a lost video.
        mark = {"video_id": source.get("video_id"), "source": source.get("source"), "url": source.get("url"),
                "title": source.get("title"), "study_id": piece["dissect"]}
        code, used = room.budgeted(["khaby", "used"], mark, 60, dry=False)
        note["marked_used"] = code == 0
        if code != 0:
            room.warnings.append(f"the khaby's source {source.get('url')} could not be marked used ({str(used)[:200]}): "
                                 "it could be chosen again")
    if not keyed.get("khaby_id") or len(keys) != len(segments):
        note["failed"] = f"{len(keys)} keyframes came back for {len(segments)} reactions"
        _drop_khaby(room, note, keyed.get("khaby_id"))
        return None
    # The viewer on the keyframes, before any video money: Teeny on model in
    # every one, nothing prohibited, nothing real-looking in what was made.
    ok, judged, note["keys_viewer"] = _judge(
        room, "viewer", ["guard", "view-khaby"],
        {"frames": [k["path"] for k in keys], "label": None, "intent": piece["logline"][:400], "stage": "keys",
         "shots": len(segments)}, 240, "ok",
        ("ok", "sees", "teeny", "interacts", "prohibited", "looks_real", "reason", "judged"))
    if not ok:
        note["failed"] = "the viewer check did not pass its keyframes"
        _drop_khaby(room, note, keyed["khaby_id"])
        return None
    wait = min(room.cfg.skit_secs, room.left() - SKIT_RESERVE_SECS - SKIT_CUT_SECS)
    if wait < 120:
        note["failed"] = f"not enough of the run's time left to animate the khaby video ({room.left()} s)"
        _drop_khaby(room, note, keyed["khaby_id"])
        return None
    code, made = room.budgeted(["video", "khaby", "render"], {"khaby_id": keyed["khaby_id"], "wait_seconds": int(wait)},
                               int(wait) + SKIT_CUT_SECS + 300, dry=False)
    if code != 0 or not isinstance(made, dict):
        note.update(failed="refused" if code == 2 else "not made",
                    result=_brief(made, "refused", "rule", "error", "reason", "asset_id", "spent_usd")
                    if isinstance(made, dict) else str(made)[:300])
        return None
    if made.get("dry_run"):
        note.update(dry_run=True, failed="the video hand is in DRY_RUN: no video was made")
        return None
    path = made.get("asset") if isinstance(made.get("asset"), str) and made["asset"].strip() else ""
    note.update(asset=made.get("asset_id"), path=path or None, cost_usd=made.get("cost_usd"),
                models=made.get("models"), label=made.get("label"), seconds=made.get("seconds"))
    frames = made.get("frames") if isinstance(made.get("frames"), list) else []
    # Only a labelled file is handed over: the hand reports the label burned
    # in, for the whole cut, and it is the parody label.
    if not (path and made.get("labelled") is True and str(made.get("label") or "").startswith("AI parody")
            and frames):
        note["failed"] = "made, but no labelled file came back"
        return None
    ok, judged, note["viewer"] = _judge(
        room, "viewer", ["guard", "view-khaby"],
        {"frames": frames, "label": made["label"], "intent": piece["logline"][:400], "stage": "cut"}, 240, "ok",
        ("ok", "sees", "labelled", "teeny", "interacts", "prohibited", "looks_real", "reason", "judged"))
    if not ok:
        note["failed"] = "the viewer check did not pass it"
        return None
    code, up = room.budgeted(["x", "upload", "--file", path], None, 300, dry=False)
    if not (code == 0 and isinstance(up, dict) and isinstance(up.get("media_id"), str)):
        note.update(failed="upload failed", result=_brief(up, "refused", "rule", "error"))
        return None
    note["media_id"] = up["media_id"]
    return [up["media_id"]]


def _drop_khaby(room: "Room", note: Json, khaby_id: Any) -> None:
    """A khaby video whose keys were bought and that will not be rendered:
    let go of its reservations now (`hands/video khaby drop`, live), rather
    than hold them until `release_stale` two hours later. A drop that fails
    is a warning: release_stale still frees it then."""
    if not khaby_id:
        return
    code, got = room.budgeted(["video", "khaby", "drop"], {"khaby_id": khaby_id}, KHABY_DROP_SECS, dry=False)
    note["dropped"] = _brief(got, "released_usd", "refused", "rule", "error") if isinstance(got, dict) else {
        "error": str(got)[:300]}
    if code != 0:
        room.warnings.append(f"the khaby video {khaby_id} could not be dropped (hands/video khaby drop: "
                             f"{str(got)[:200]}): its reservation is held until it expires")


def khaby_outcome(draft: Json, note: Json, post_id: str | None) -> Json:
    """What the record keeps of a khaby that went out (or would have): the
    original it answered, the dissect, the story, the models, what it cost,
    the post."""
    piece = draft.get("khaby") or {}
    source = piece.get("source") or {}
    return {"source": {k: source.get(k) for k in ("title", "uploader", "url", "source")},
            "dissect": piece.get("dissect"), "story": piece.get("story"), "logline": piece.get("logline"),
            "segments": len(piece.get("segments") or []), "khaby_id": note.get("khaby_id"), "asset": note.get("asset"),
            "path": note.get("path"), "models": note.get("models"), "cost_usd": note.get("cost_usd"),
            "label": note.get("label"), "post_id": post_id, "planned": bool(note.get("planned"))}


def check_pitch(pitch: Json, sources: set[str]) -> str:
    """A khaby names both: its item one of his videos found (`sources`), its
    hook the trending story its words join."""
    item, demoted = pitch.get("item") or "", pitch.get("unhooked") or ""
    if item not in sources:
        return f"a khaby answers a Khaby Lame video: its item {item or None!r} is not one of the Khaby videos found"
    if not pitch.get("hook"):
        return (f"{demoted}: " if demoted else "") + "a khaby joins a trending story, and it has no hook"
    return ""


def foreign_item(item: str) -> str:
    return f"its item {item!r} is a Khaby video, which is only a khaby's to answer"


def sources(room: "Room", slot_id: str | None) -> tuple[list[Json], Json, str] | None:
    """His videos, read only in a video slot (None in any other: no khaby
    this run). None found (a failure, an empty list) only takes the khaby
    option away."""
    if not room.skit_slot:
        return None
    finds, note = load_finds(room, slot_id)
    warning = "" if finds else ("no Khaby video could be found for a khaby (hands/khaby find: " +
                                (note.get("error") or "none found") + "): no khaby this run")
    return finds, note, warning


def finds_part(finds: list[Json]) -> str:
    return ("## Khaby Lame videos you could answer\n\nHis short videos, each with its title, the account that posted "
            "it (`by`), where (`source`), how long (`seconds`), its `views`, `age_hours` and `rank`. Each `id` is a "
            f"khaby's `item`. {DATA_LINE}\n\n```json\n" +
            json.dumps(finds, ensure_ascii=False, separators=(",", ":")) + "\n```")


def prepare(room: "Room", pitch: Json) -> tuple[Json | None, str, Json]:
    """The pitch's video dissected before its writer turn (a paid read), and
    what the record keeps of the dissect."""
    timeline, why = dissect_source(room, pitch)
    kept = ({k: timeline.get(k) for k in ("dissect_id", "usable", "why", "flags", "cost_usd", "cached")}
            if isinstance(timeline, dict) else {})
    return timeline, why, kept


def timeline_part(timeline: Json) -> str:
    return ("## the Khaby video you are answering\n\nHis video, as a model watched it: its title, the account it came "
            "from (`by`, which the post's words credit by its name), the joke, each segment in order (`n`, its role, "
            "how long, what happens; `yours` is a reaction you re-make, the rest is kept as filmed) and his beats in "
            "the reaction (`at` seconds: the look, the plain fix, the open hands). Your twist is in how you do his "
            f"beats. {DATA_LINE}\n\n```json\n" + json.dumps(timeline_for_writer(timeline), ensure_ascii=False) +
            "\n```")


def shape(raw: Any, pitch: Json, study: Json | None) -> tuple[Json, str]:
    """The khaby as kept, and what the draft keeps of its source's timeline
    (the screen and the producer read it)."""
    piece, bad = khaby_of(raw, pitch, study)
    fields: Json = {"khaby": piece}
    if isinstance(study, dict):
        fields["timeline"] = {k: study[k] for k in TIMELINE_KEYS if k in study}
    return fields, bad


def screen(draft: Json, words: str, story: Json | None) -> list[str]:
    return khaby_screen(draft.get("khaby"), words, story, draft.get("timeline"))


def shown(piece: Json) -> Json:
    """What the editor sees of a khaby: the logline, the original by its
    title and account, what its setup shows, and each reaction's edit and
    motion."""
    source = piece.get("source") or {}
    return {"logline": piece.get("logline"),
            "source": ", by ".join(str(x) for x in (source.get("title"), source.get("uploader")) if x),
            "setup": piece.get("setup") or [],
            "reactions": [{k: s.get(k) for k in ("edit", "motion")} for s in piece.get("segments") or []]}


FORMAT = Format(
    name="khaby", pitch=KHABY_PITCH, slot_line=KHABY_SLOT_LINE, draft_shape=KHABY_SHAPE, body=KHABY_BODY,
    rules=KHABY_RULES,
    judge_line=("`khaby` is the short parody video answering a Khaby Lame video (`source`): his setup kept as "
                "filmed, his deadpan reaction re-made with Teeny in his place (`reactions`)"),
    editor=KHABY_EDITOR, final=KHABY_FINAL, opening=KHABY_SLOT_OPENING,
    check_pitch=check_pitch, shape=shape, screen=screen, shown=shown, make=_make_khaby, outcome=khaby_outcome,
    time=_khaby_time, min_secs=KHABY_MIN_SECS, spent=("asset", "khaby_id"),
    sources=sources, sources_name="finds", sources_part=finds_part, foreign_item=foreign_item,
    item_is_source=True, prepare=prepare, prepare_max=KHABY_DISSECTS,
    prepare_full=f"the run already dissected two sources ({KHABY_DISSECTS} a run)", study_part=timeline_part)
