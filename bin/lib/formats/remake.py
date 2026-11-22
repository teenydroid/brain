"""The `remake` format: a popular short video re-created shot by shot with
one of a card's people, Teeny looking on (docs/architecture.md, "The
studio"). Its words for the write turn, the chart videos it reads
(`load_charts`) and the study of one before the write turn
(`study_source`), its structural check (`remake_of`), its producer
(`_make_remake`) and what the record keeps of it (`remake_outcome`), as
`FORMAT`."""

from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING, Any

from formats import Format
from formats.skit import SKIT_CUT_SECS, SKIT_RESERVE_SECS, _skit_rules
from studio_room import DATA_LINE, Json, _brief, _judge, _str, _words

if TYPE_CHECKING:
    from studio_room import Room


# --- remakes: a popular video, re-created with the figure from the news, Teeny looking on ---
# (hands/remake charts and study read the source; hands/video remake-keys and
# remake make it, cut with the original's sound and labelled on the reel
# service; docs/architecture.md, "The studio"). Off unless STUDIO_FORMATS names
# remake, and only in a video slot (STUDIO_SKIT_SLOTS), where the charts are
# read: one video a day between the video formats. The hard lines
# (hands/lib/teeny_hands/guard/remake.py) run in code and through the judge
# before any money (`hands/guard remake`), on the keyframes before any video
# money and on the cut's frames after (`hands/guard view-remake`).

REMAKE_ASK = ("A remake is a post with a short AI-made parody video that re-creates the popular video below shot by "
              "shot, with one of the card's `people` standing where its person stood, doing what they did, an "
              "impossible twist in every shot, and you somewhere in every frame as a bystander. It is one continuous "
              "film made from its opening frame: an image model edits the original's first frame (shot 1's `edit`: "
              "who replaces whom by `ref`, each of the original's people cast as a figure or a generic extra, the same "
              "pose, the impossible thing), a video model makes the whole film from it, shot by shot (each `motion`: "
              "what is seen and heard in that shot's seconds, sound effects in plain words, at most one short spoken "
              "or sung line in double quotes, at most 12 words, invented), and the film makes its own sound. A later "
              "shot's `edit` is one sentence on what the scene shows then, with its new place in words when the "
              "original moves. You live only in each shot's `teeny`: where you are and the small thing you do alone. "
              "The post's words say what happened in a clause, name the original by its title and its creator in "
              "words, and frame the video as your parody.")
REMAKE_SHAPE = ('{"figures": ["<people from the card>"], "premise": "<one line>", "logline": "<one line>", '
                '"cast": [{"ref": "<A, B...>", "as": "<a figure, or \\"extra\\">"}], "one_setting": <true only when the '
                'whole original is one place>, "shots": [{"n": 1, "edit": "...", "motion": "...", "teeny": "..."}, '
                '"... one for every shot of the original, in its order"]}')
REMAKE_RULES = ("A remake's mechanics: the impossible thing is in frame in every shot, beyond you being there (a famous "
                "face in an ordinary room could pass for real footage, and the viewer check refuses it); each `edit` "
                "and `motion` never mentions Teeny, the mascot or a yellow robot, and nobody looks at, touches or talks "
                "to you; \"unbranded, no logos\" in every edit; no @-mentions and no links; it carries an 'AI parody' "
                "label the whole way through, so the words never call it real.")
#: A remake's time (STUDIO_REMAKE_SECS covers its keys and its video): the
#: least it starts with, the most its keys may wait, and what of the total the
#: keys leave for the viewer on them and the video's start.
REMAKE_MIN_SECS = 540
REMAKE_KEYS_SECS = 300
REMAKE_KEYS_SPARE = 240
#: What a remake leaves of the run after its video's cut, so a paid video
#: never dies for lack of time: the cut's viewer (240 s), the upload (300 s),
#: the post (300 s) and a margin.
REMAKE_TAIL_SECS = 900
#: A remake's whole length, in seconds (hands/video remake keys refuses any other: `body`).
REMAKE_TOTAL_SECS = (3, 30)
#: The most sources studied a run (each a paid look at a video), and how long one may take.
REMAKE_STUDIES = 2
REMAKE_STUDY_SECS = 240
#: The most of the story's words hands/video remake-keys takes (its `story`).
REMAKE_STORY_MAX = 600
#: The most chart videos read (the first that studies well is the one remade).
REMAKE_CHARTS_MAX = 12
#: What of a study the draft keeps (the producer needs the audio and the frames).
STUDY_KEYS = ("study_id", "source", "excerpt", "aspect", "audio", "shots", "summary", "flags", "usable", "why")


def _remake_rules():
    """The guard's remake module (hands/lib/teeny_hands/guard/remake.py)."""
    _skit_rules()
    from teeny_hands.guard import remake as rules

    return rules


def chart_items(got: Any) -> list[Json]:
    """hands/remake charts' candidates as items a remake may re-create:
    `chart:<provider>:<video id>`, its title, who made it, how long, how
    popular, how old. All of it strangers' words."""
    out: list[Json] = []
    for c in (got.get("candidates") if isinstance(got, dict) else None) or []:
        cid = _str(c.get("id"), 80) if isinstance(c, dict) else ""
        if not (cid.startswith("chart:") and _str(c.get("title"))):
            continue
        item: Json = {"id": cid, "title": _str(c["title"], 300)}
        for key in ("by", "source", "provider", "url"):
            if _str(c.get(key)):
                item[key] = _str(c[key], 300)
        for key in ("seconds", "views", "age_hours", "rank"):
            if isinstance(c.get(key), (int, float)) and not isinstance(c.get(key), bool):
                item[key] = c[key]
        item["untrusted"] = True
        out.append(item)
    return out[:REMAKE_CHARTS_MAX]


def load_charts(room: "Room", slot_id: str | None = None) -> tuple[list[Json], Json]:
    """The chart videos a remake may re-create (`hands/remake charts`, a read,
    kept by the hand per slot), and what came of asking. Never fails the
    run: none is no remake."""
    code, got = room.hand_code(["remake", "charts", *(["--slot", slot_id] if slot_id else [])], timeout=120)
    items = chart_items(got) if code == 0 else []
    note: Json = {"candidates": len(items)}
    if isinstance(got, dict):
        note.update({k: got[k] for k in ("unavailable", "cached", "key", "fetched_at", "dropped_by_screen") if k in got})
    if code != 0:
        note["error"] = (str(got.get("error") or got.get("refused") or got) if isinstance(got, dict) else str(got))[:300]
    return items, note


def study_for_writer(study: Json) -> Json:
    """What the writer is told of the original: its title, who made it, and
    each shot as studied (who is in it by ref, the setting, what happens, the
    camera, anything said). Never a path or a link. Strangers' words."""
    source = study.get("source") if isinstance(study.get("source"), dict) else {}
    shots = [{k: s.get(k) for k in ("n", "seconds", "people", "setting", "motion", "camera", "speech") if k in s}
             for s in _remake_rules().study_shots(study)]
    return {"title": source.get("title"), "by": source.get("uploader"), "on": source.get("source"),
            "summary": study.get("summary"), "aspect": study.get("aspect"), "shots": shots, "untrusted": True}


def study_source(room: "Room", item: str) -> tuple[Json | None, str]:
    """`hands/remake study` on a chart video (a read, inside the run's time):
    the study, or None and why it cannot be remade."""
    code, got = room.budgeted(["remake", "study"], {"id": item}, REMAKE_STUDY_SECS)
    if code != 0 or not isinstance(got, dict):
        rule = got.get("rule") if isinstance(got, dict) else None
        why = (got.get("refused") or got.get("error")) if isinstance(got, dict) else got
        return None, f"its source could not be studied ({rule or 'failed'}: {str(why)[:200]})"
    refused = _remake_rules().study_gate(got)
    if refused:
        return got, "its source cannot be remade: " + "; ".join(refused)
    shots = _remake_rules().study_shots(got)
    if not shots:
        return got, "its source's study has no shots"
    if len(shots) > _remake_rules().MAX_SHOTS:
        return got, f"its source's study has {len(shots)} shots, more than a remake's {_remake_rules().MAX_SHOTS}"
    total = study_seconds(got)
    if not REMAKE_TOTAL_SECS[0] <= total <= REMAKE_TOTAL_SECS[1]:
        return got, (f"its source's shots come to {total:g} s, not {REMAKE_TOTAL_SECS[0]} to {REMAKE_TOTAL_SECS[1]} s "
                     "in all: one take is that long")
    most = room.cfg.remake_max_shot
    for shot in shots:
        secs = shot.get("seconds")
        if not (isinstance(secs, (int, float)) and not isinstance(secs, bool) and 1.0 <= secs <= most):
            return got, (f"its source's shot {shot['n']} is {secs!r} s, not 1 to {most:g} s: each shot is one clip "
                         "(REMAKE_MAX_SHOT_SECONDS)")
    return got, ""


def study_seconds(study: Json) -> float:
    """The study's shots' seconds, added up (a shot with none counts as 0)."""
    return float(sum(s.get("seconds") for s in _remake_rules().study_shots(study)
                     if isinstance(s.get("seconds"), (int, float)) and not isinstance(s.get("seconds"), bool)))


def _setting(shot: Json) -> str:
    return " ".join(str(shot.get("setting") or "").lower().split())


def remake_of(raw: Any, card: Json, study: Json | None) -> tuple[Json, str]:
    """A draft's `remake` as kept ({figures, premise, logline, cast, shots:
    [{n, edit, motion, teeny, seconds, frame}], source, study, story,
    aspect}), and why it is unusable ("" when it is usable). The writer gives
    the words; the studio, never the writer, sets the source, the study, the
    story (the card's id), the aspect and each shot's length and frame,
    from the study. Structure only."""
    if not isinstance(study, dict):
        return {}, "a remake needs its study, and its source was not studied"
    remake, bad = _remake_rules().shape(raw, study)
    source = study.get("source") if isinstance(study.get("source"), dict) else {}
    remake["source"] = {k: source.get(k) for k in ("video_id", "url", "title", "uploader", "source")}
    remake["study"] = study.get("study_id")
    remake["aspect"] = study.get("aspect") or "9:16"
    studied = _remake_rules().study_shots(study)
    by_n = {s["n"]: s for s in studied}
    # One take: the whole remake is one film made from the opening frame.
    # `one_setting` (one place throughout) is said only when it is surely
    # so: a one-shot original, or the writer says so and every studied shot
    # has shot 1's setting. Otherwise a later shot whose setting differs from
    # the one before it is a hard cut (`cuts`).
    same = len({_setting(s) for s in studied}) == 1
    remake["one_setting"] = len(studied) == 1 or (remake.get("one_setting") is True and same)
    previous = None
    for shot in remake.get("shots") or []:
        source = by_n.get(shot["n"], {})
        shot["seconds"] = source.get("seconds")
        shot["frame"] = source.get("frame")
        shot["cuts"] = (not remake["one_setting"] and previous is not None and _setting(source) != _setting(previous))
        previous = source
    total = study_seconds(study)
    if not bad and not REMAKE_TOTAL_SECS[0] <= total <= REMAKE_TOTAL_SECS[1]:
        bad = (f"a remake is {REMAKE_TOTAL_SECS[0]} to {REMAKE_TOTAL_SECS[1]} s in all, and its source's shots come "
               f"to {total:g} s")
    if card.get("id"):
        remake["story"] = card["id"]
    elif not bad:
        bad = "a remake casts the people of a card, and it names none"
    return remake, bad


def _remake_time(room: "Room") -> int:
    """What a remake's keys and video may take from now: STUDIO_REMAKE_SECS,
    and never into the last REMAKE_TAIL_SECS + SKIT_CUT_SECS of the run."""
    return min(room.cfg.remake_secs, room.left() - REMAKE_TAIL_SECS - SKIT_CUT_SECS)


def _make_remake(room: "Room", draft: Json, note: Json) -> list[str] | None:
    remake = draft.get("remake") or {}
    study = draft.get("study") or {}
    shots = [s for s in remake.get("shots") or [] if isinstance(s, dict)]
    note.update(story=remake.get("story"), figures=remake.get("figures"), logline=remake.get("logline"),
                source=(remake.get("source") or {}).get("url"), study=remake.get("study"), shots=len(shots))
    hook = draft.get("story") if isinstance(draft.get("story"), dict) else None
    if not shots or not remake.get("logline") or hook is None or not study.get("study_id"):
        note["failed"] = "the draft has no remake, no study of its source, or no story it casts from"
        return None
    # Every word an image or a video model is told is past the guard's screen
    # and judge, as a scene is (the `task` surface).
    told = [("logline", remake["logline"])] + [(f"shot {s['n']}'s {k}", s.get(k) or "") for s in shots
                                               for k in ("edit", "motion", "teeny")]
    for what, text in told:
        code, checked = room.budgeted(["guard", "check"], {"text": text, "surface": "task"}, 120)
        if not (code == 0 and isinstance(checked, dict) and isinstance(checked.get("text"), str)):
            note.update(failed=f"the guard held its {what}", guard=_brief(checked, "refused", "rule", "category", "error"))
            return None
    # The hard lines: code's screen, then the remake judge, on every prompt,
    # Teeny's part, the study and the words. Before any money; a judge that
    # cannot answer is a no.
    code, got = room.budgeted(["guard", "remake"], {"remake": remake, "text": _words(draft), "story": hook,
                                                   "study": study}, 200)
    note["hard_lines"] = _brief(got, "ok", "rule", "reason", "screen", "error") if isinstance(got, dict) else {
        "error": str(got)[:300]}
    if not (code == 0 and isinstance(got, dict) and got.get("ok") is True and got.get("judged") is True):
        note["failed"] = "the remake's hard lines did not pass it"
        return None
    if room.dry:
        note["planned"] = True
        return []
    total = _remake_time(room)
    if total < REMAKE_MIN_SECS:
        note["failed"] = f"not enough of the run's time left to make a remake ({room.left()} s)"
        return None
    began = time.monotonic()
    keys_wait = min(REMAKE_KEYS_SECS, total - REMAKE_KEYS_SPARE)
    source = remake.get("source") or {}
    story = ": ".join(x for x in (_str(hook.get("title"), 2000), _str(hook.get("summary"), 2000)) if x)
    body = {"study": remake["study"], "source": {k: source.get(k) for k in ("video_id", "url", "title", "source")},
            "story": (story or str(remake["story"]))[:REMAKE_STORY_MAX], "figures": remake.get("figures") or [], "logline": remake["logline"],
            "aspect": remake.get("aspect") or "9:16", "one_setting": remake.get("one_setting") is True,
            "shots": [{k: s.get(k) for k in ("n", "frame", "seconds", "edit", "motion", "teeny")}
                      | {"cuts": s.get("cuts") is True} for s in shots],
            "wait_seconds": int(keys_wait)}
    code, keyed = room.budgeted(["video", "remake-keys"], body, int(keys_wait) + 120, dry=False)
    if code != 0 or not isinstance(keyed, dict):
        note.update(failed="its keyframes were refused" if code == 2 else "its keyframes were not made",
                    result=_brief(keyed, "refused", "rule", "error", "reason", "remake_id", "spent_usd")
                    if isinstance(keyed, dict) else str(keyed)[:300])
        return None
    if keyed.get("dry_run"):
        note.update(dry_run=True, failed="the video hand is in DRY_RUN: no keyframes were made")
        return None
    keys = [k for k in keyed.get("keys") or [] if isinstance(k, dict) and isinstance(k.get("path"), str)]
    note.update(remake_id=keyed.get("remake_id"), keys_cost_usd=keyed.get("cost_usd"),
                reserve_usd=keyed.get("reserve_usd"))
    if keyed.get("remake_id"):
        # Keys bought: the source is spent, so it is never remade twice
        # (hands/remake used). A mark that fails is a warning, not a lost remake.
        mark = {"video_id": source.get("video_id"), "source": source.get("source"), "url": source.get("url"),
                "title": source.get("title"), "study_id": remake["study"]}
        code, used = room.budgeted(["remake", "used"], mark, 60, dry=False)
        note["marked_used"] = code == 0
        if code != 0:
            room.warnings.append(f"the remake's source {source.get('url')} could not be marked used ({str(used)[:200]}): "
                                 "it could be chosen again")
    # One take: one keyframe, the opening one. A key a shot is the hand's
    # shot-by-shot path (MEDIA_REMAKE_PER_SHOT=1); its answer names no mode,
    # so the number of keys says which it was.
    per_shot = len(shots) > 1 and len(keys) == len(shots)
    note["one_take"] = not per_shot
    if not keyed.get("remake_id") or len(keys) not in (1, len(shots)):
        note["failed"] = f"{len(keys)} keyframes came back, not 1 (one take) or {len(shots)} (one a shot)"
        _drop_remake(room, note, keyed.get("remake_id"))
        return None
    # The viewer on the keyframes, before any video money: Teeny on model in
    # every one, nobody meeting it, nothing prohibited or real-looking.
    ok, judged, note["keys_viewer"] = _judge(
        room, "viewer", ["guard", "view-remake"],
        {"frames": [k["path"] for k in keys], "label": None, "intent": remake["logline"][:400], "stage": "keys",
         "shots": len(keys)}, 240, "ok",
        ("ok", "sees", "teeny", "interacts", "marked", "prohibited", "looks_real", "reason", "judged"))
    if not ok:
        note["failed"] = "the viewer check did not pass its keyframes"
        _drop_remake(room, note, keyed["remake_id"])
        return None
    # The video waits what the remake's own time has left after its keys,
    # less its cut, and never into the tail the cut's viewer, the upload and
    # the post need.
    own = total - (time.monotonic() - began)
    wait = min(room.cfg.skit_secs, own - SKIT_CUT_SECS, room.left() - REMAKE_TAIL_SECS - SKIT_CUT_SECS)
    if wait < 120:
        note["failed"] = f"not enough of the run's time left to animate the remake ({room.left()} s)"
        _drop_remake(room, note, keyed["remake_id"])
        return None
    code, made = room.budgeted(["video", "remake"], {"remake_id": keyed["remake_id"], "sound": study.get("audio"),
                                                     "wait_seconds": int(wait)},
                               int(wait) + SKIT_CUT_SECS + 60, dry=False)
    if code != 0 or not isinstance(made, dict):
        note.update(failed="refused" if code == 2 else "not made",
                    result=_brief(made, "refused", "rule", "error", "reason", "asset_id", "spent_usd")
                    if isinstance(made, dict) else str(made)[:300])
        if code == 2:
            # Refused before it rendered: nothing will use the reservation. Any
            # other failure left the render to the hand, which owns it then.
            _drop_remake(room, note, keyed["remake_id"])
        return None
    if made.get("dry_run"):
        note.update(dry_run=True, failed="the video hand is in DRY_RUN: no video was made")
        return None
    if not (isinstance(made.get("asset"), str) and made["asset"].strip()):
        note.update(failed="made, but no labelled file came back", result=_brief(made, "error", "reason", "asset_id"))
        return None
    # hands/video remake answers its cut as a path (`asset`) and its row as
    # `asset_id`, where video skit answers a row.
    path = made.get("asset") if isinstance(made.get("asset"), str) and made["asset"].strip() else ""
    note.update(asset=made.get("asset_id"), path=path or None, cost_usd=made.get("cost_usd"),
                models=made.get("models"), label=made.get("label"), seconds=made.get("seconds"))
    if made.get("sound") in ("model", "source"):
        # Whose sound the film carries: the video model's (the default) or the original's.
        note["sound"] = made["sound"]
    frames = made.get("frames") if isinstance(made.get("frames"), list) else []
    # Only a labelled file is handed over: the hand reports the label burned
    # in, for the whole cut, and it is the parody label.
    if not (path and made.get("labelled") is True and str(made.get("label") or "").startswith("AI parody")
            and frames):
        note["failed"] = "made, but no labelled file came back"
        return None
    ok, judged, note["viewer"] = _judge(
        room, "viewer", ["guard", "view-remake"],
        {"frames": frames, "label": made["label"], "intent": remake["logline"][:400], "stage": "cut"}, 240, "ok",
        ("ok", "sees", "labelled", "teeny", "interacts", "marked", "prohibited", "looks_real", "reason", "judged"))
    if not ok:
        note["failed"] = "the viewer check did not pass it"
        return None
    code, up = room.budgeted(["x", "upload", "--file", path], None, 300, dry=False)
    if not (code == 0 and isinstance(up, dict) and isinstance(up.get("media_id"), str)):
        note.update(failed="upload failed", result=_brief(up, "refused", "rule", "error"))
        return None
    note["media_id"] = up["media_id"]
    return [up["media_id"]]


#: How long `hands/video remake-drop` may take: it only releases rows.
REMAKE_DROP_SECS = 60


def _drop_remake(room: "Room", note: Json, remake_id: Any) -> None:
    """A remake whose keys were bought and that will not be rendered: let
    go of its shots' reservations now (`hands/video remake-drop`, live),
    rather than hold them until `release_stale` two hours later. A drop
    that fails is a warning: release_stale still frees it then."""
    if not remake_id:
        return
    code, got = room.budgeted(["video", "remake-drop"], {"remake_id": remake_id}, REMAKE_DROP_SECS, dry=False)
    note["dropped"] = _brief(got, "released_usd", "refused", "rule", "error") if isinstance(got, dict) else {
        "error": str(got)[:300]}
    if code != 0:
        room.warnings.append(f"the remake {remake_id} could not be dropped (hands/video remake-drop: {str(got)[:200]}): "
                             "its reservation is held until it expires")


def remake_outcome(draft: Json, note: Json, post_id: str | None) -> Json:
    """What the record keeps of a remake that went out (or would have),
    beside `skit_outcome`: the original it re-created, the study, the story
    and figures, the models, what it cost, the post."""
    remake = draft.get("remake") or {}
    source = remake.get("source") or {}
    return {"source": {k: source.get(k) for k in ("title", "uploader", "url", "source")},
            "study": remake.get("study"), "story": remake.get("story"), "figures": remake.get("figures") or [],
            "logline": remake.get("logline"), "shots": len(remake.get("shots") or []),
            "remake_id": note.get("remake_id"), "asset": note.get("asset"), "path": note.get("path"),
            "models": note.get("models"),
            "cost_usd": note.get("cost_usd"), "label": note.get("label"), "post_id": post_id,
            "planned": bool(note.get("planned"))} | ({"sound": note["sound"]} if note.get("sound") else {})


def sources(room: "Room", slot_id: str | None) -> tuple[list[Json], Json, str] | None:
    """The chart videos, read only in a video slot (None in any other: no
    remake this run). None read (a failure, an empty chart) only takes the
    remake option away."""
    if not room.skit_slot:
        return None
    charts, note = load_charts(room, slot_id)
    warning = "" if charts else ("no chart video could be read for a remake (hands/remake charts: " +
                                 (note.get("error") or "an empty chart") + "): no remake this run")
    return charts, note, warning


def prepare(room: "Room", item: str) -> tuple[Json | None, str, Json]:
    """A chart video studied before the write turn (a paid read), and what
    the record keeps of the study."""
    study, why = study_source(room, item)
    kept = ({k: study.get(k) for k in ("study_id", "usable", "why", "flags", "cost_usd", "cached")}
            if isinstance(study, dict) else {})
    return study, why, kept


def study_part(study: Json) -> str:
    return ("## the original you are remaking\n\nThe popular video, as a model studied it: its title, who "
            "made it, and each shot in order (`n`, how long, the people in it by `ref`, the setting, what "
            "happens, the camera, anything said). Name it in the post by its title and its creator, in words. "
            f"{DATA_LINE}\n\n```json\n" + json.dumps(study_for_writer(study), ensure_ascii=False) + "\n```")


def shape(raw: Any, card: Json, study: Json | None) -> tuple[Json, str]:
    """The remake as kept, and what the post keeps of its source's study
    (the producer needs the audio and the frames)."""
    remake, bad = remake_of(raw, card, study)
    fields: Json = {"remake": remake}
    if isinstance(study, dict):
        fields["study"] = {k: study[k] for k in STUDY_KEYS if k in study}
    return fields, bad


def shown(remake: Json) -> Json:
    """What the editor sees of a remake: who, the premise, the logline, the
    original by its title and creator, and each shot's edit, motion and
    Teeny's part."""
    source = remake.get("source") or {}
    return {"figures": remake.get("figures") or [], "premise": remake.get("premise"),
            "logline": remake.get("logline"),
            # Strangers' words, as the writer is told them.
            "source": {"title": source.get("title"), "by": source.get("uploader"), "untrusted": True},
            "shots": [{k: s.get(k) for k in ("edit", "motion", "teeny")} for s in remake.get("shots") or []]}


FORMAT = Format(
    name="remake", ask=REMAKE_ASK, draft_shape=REMAKE_SHAPE, rules=REMAKE_RULES,
    shape=shape, shown=shown, make=_make_remake, outcome=remake_outcome,
    time=_remake_time, min_secs=REMAKE_MIN_SECS, spent=("asset", "remake_id"),
    sources=sources, sources_name="charts", prepare=prepare, prepare_max=REMAKE_STUDIES, study_part=study_part)
