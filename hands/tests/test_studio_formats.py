"""bin/lib/formats/, the studio's video formats behind one interface: the
prompts the writer and the editor are sent are byte for byte what they were
before the formats moved out of bin/lib/studio.py (goldens captured from main
before the move, hands/tests/fixtures/studio_prompts.json).

    python3 hands/tests/test_studio_formats.py --write-goldens   # recapture

Recapture only on purpose: a changed golden is a changed prompt."""

from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "bin" / "lib"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import studio  # noqa: E402

import test_studio_remake as remake_t  # noqa: E402
import test_studio_skit as skit_t  # noqa: E402

GOLDENS = Path(__file__).resolve().parent / "fixtures" / "studio_prompts.json"

ALL = ("post", "thread", "image", "meme", "skit", "remake")
TRENDING = {"id": "trend:t1", "title": "a lab hired a scientist", "summary": "the lab says so", "topic": "ai",
            "trend": {"source": "digg", "score": 556, "rank": 1, "sources": 71, "authors": 55, "age_hours": 6.0},
            "trending": True, "untrusted": True}
QUIET = {"id": "x-agents:9", "title": "someone said agents", "trending": False, "untrusted": True}
INPUTS = {"hours": 24, "happened": [{"id": "evt:1", "what": "a bill came", "stake": "rent"}],
          "stories": [{"key": "rent", "title": "the rent", "premise": "p", "state": "open"}], "due": [], "ideas": [],
          "outside": {"trend": [TRENDING], "x-agents": [QUIET]}}
SAMPLED = {"pairs": ["Confession", "Bet", "Scene"], "lore": ["the kettle owes me"],
           "best": [{"text": "b", "format": "post", "impressions": 10}], "worst": [], "explore": False}
EXPLORE = {**SAMPLED, "explore": True, "explore_with": "tall tale"}
MENU = {"templates": [{"id": "drake", "name": "Drake", "lines": 2, "keywords": ["prefer"], "example": ["a", "b"]}],
        "pictures": ["brain/assets/tiny/tiny-love.jpg"]}
STORY = {"key": "rent", "title": "the rent", "premise": "p", "state": "open"}
ITEM = {"id": "evt:1", "what": "a bill came"}
FIX = [("the bill was $40", 'unsourced: ["$40"]')]
BOARD = {"best": [{"text": "b", "format": "post"}], "worst": []}
STORIES = [{"key": "rent", "title": "the rent"}]


def pitch(fmt: str, **over) -> dict:
    return {"id": "p1", "item": "evt:1", "hook": None, "storyline": "rent", "shape": "scene", "format": fmt,
            "angle": "an angle", "why_now": "tonight", **over}


def drafts() -> dict:
    """One draft of every format, as validate_drafts keeps them."""
    out = {
        "post": {"pitch": "p1", "storyline": None, "shape": "scene", "format": "post", "meaning": "m", "text": "a post"},
        "thread": {"pitch": "p2", "storyline": "rent", "shape": "bet", "format": "thread", "meaning": "m",
                   "posts": ["one", "two"]},
        "image": {"pitch": "p3", "storyline": None, "shape": "scene", "format": "image", "meaning": "m",
                  "text": "a picture post", "image": "teeny on a bus", "alone": False,
                  "joins": {"story": TRENDING["title"], "trend": TRENDING["trend"]}},
        "meme": {"pitch": "p4", "storyline": None, "shape": "scene", "format": "meme", "meaning": "m", "text": "meme",
                 "meme": {"template": "drake", "lines": ["a", "b"], "teeny": "brain/assets/tiny/tiny-love.jpg"},
                 "alone": False},
        "skit": skit_t.skit_draft(),
        "remake": remake_t.remake_draft(),
    }
    return copy.deepcopy(out)


def build() -> dict:
    """Every message the studio sends on fixed inputs, by a name that says
    which inputs, plus what the editor is shown of each draft."""
    out: dict = {}
    every = studio.Config(formats=ALL)
    for name, cfg, kw in (
            ("plain", studio.Config(), {}),
            ("plain-no-outside", studio.Config(), {"inputs": {k: v for k, v in INPUTS.items() if k != "outside"}}),
            ("all", every, {}),
            ("all-video-slot", every, {"skit_slot": True}),
            ("all-video-slot-charts", every, {"skit_slot": True, "charts": [remake_t.CHART, remake_t.CHART2]}),
            ("all-charts", every, {"charts": [remake_t.CHART]}),
            ("remake-only-charts", studio.Config(formats=("post", "remake")), {"charts": [remake_t.CHART]}),
            ("skit-video-slot", studio.Config(formats=("post", "skit")), {"skit_slot": True}),
            ("explore-cut", every, {"sampled": EXPLORE, "cut": ["happened", "trend"]}),
            ("invented-two", studio.Config(formats=ALL, draft_invented=2, pitches=2), {})):
        inputs = kw.pop("inputs", INPUTS)
        sampled = kw.pop("sampled", SAMPLED)
        cut = kw.pop("cut", [])
        out[f"pitch:{name}"] = studio.pitch_message(cfg, inputs, cut, sampled, **kw)
    for fmt in ALL:
        out[f"draft:{fmt}"] = studio.draft_message(every, pitch(fmt), ITEM, None)
        out[f"draft:{fmt}:hooked"] = studio.draft_message(every, pitch(fmt, hook="trend:t1"), ITEM, STORY,
                                                          hook=TRENDING)
        out[f"draft:{fmt}:hook-only"] = studio.draft_message(every, pitch(fmt, item=None, hook="trend:t1"), None,
                                                             None, hook=TRENDING)
        out[f"draft:{fmt}:fix"] = studio.draft_message(every, pitch(fmt), ITEM, STORY, fix=FIX, menu=MENU)
    out["draft:meme:menu"] = studio.draft_message(every, pitch("meme"), ITEM, None, menu=MENU)
    out["draft:skit:fixture"] = studio.draft_message(every, skit_t.PITCH, None, None, hook=skit_t.HOOK)
    out["draft:remake:study"] = studio.draft_message(every, remake_t.PITCH, remake_t.CHART, None, hook=remake_t.HOOK,
                                                     study=remake_t.STUDY)
    out["draft:remake:study-no-hook"] = studio.draft_message(every, remake_t.PITCH, remake_t.CHART, STORY,
                                                             study=remake_t.STUDY)
    d = drafts()
    labels = {f"D{n}": d[fmt] for n, fmt in enumerate(ALL, 1)}
    for name, batch in (("all", [("P1", "D1", "D5"), ("P2", "D6", "D2"), ("P3", "D3", "D4")]),
                        ("posts", [("P1", "D1", "D2")]),
                        ("skit", [("P1", "D5", "D1")]),
                        ("remake", [("P1", "D2", "D6")]),
                        ("videos", [("P1", "D5", "D6")])):
        out[f"judge:{name}"] = studio.judge_message(batch, labels, BOARD, STORIES)
    for fmt in ALL:
        out[f"final:{fmt}"] = studio.final_message("D1", d[fmt], BOARD, STORIES)
        out[f"final:{fmt}:fallback"] = studio.final_message("D2", d[fmt], BOARD, STORIES, fallback=True)
    out["final:skit:opening"] = studio.final_message("D3", d["skit"], BOARD, STORIES, opening=studio.SKIT_SLOT_OPENING)
    out["final:remake:opening"] = studio.final_message("D3", d["remake"], BOARD, STORIES,
                                                       opening=studio.REMAKE_SLOT_OPENING)
    for fmt in ALL:
        out[f"shown:{fmt}"] = json.dumps(studio.shown(d[fmt]), ensure_ascii=False, sort_keys=False)
    out.update(results())
    return out


PITCHES = [
    {"id": "p1", "format": "remake", "item": "chart:tt:v1", "hook": "trend:t1", "angle": "a"},
    {"id": "p2", "format": "remake", "item": "chart:tt:v1", "angle": "a"},
    {"id": "p3", "format": "remake", "item": "evt:1", "hook": "trend:t1", "angle": "a"},
    {"id": "p4", "format": "remake", "hook": "trend:old", "item": "chart:tt:v1", "angle": "a"},
    {"id": "p5", "format": "post", "item": "chart:tt:v1", "hook": "trend:t1", "angle": "a"},
    {"id": "p6", "format": "skit", "item": "evt:1", "angle": "a"},
    {"id": "p7", "format": "skit", "item": "evt:1", "hook": "trend:old", "angle": "a"},
    {"id": "p8", "format": "skit", "hook": "trend:t1", "angle": "a", "shape": "Tall-Tale"},
    {"id": "p9", "format": "skit", "item": "chart:tt:v1", "hook": "trend:t1", "angle": "a"},
    {"id": "p10", "format": "song", "item": "evt:1", "angle": "a"},
    {"id": "p11", "format": "post", "item": "trend:t1", "angle": "a", "storyline": "rent"},
    {"id": "p12", "format": "thread", "item": "evt:1", "angle": "a"},
]


def results() -> dict:
    """What the per-format checks decide on fixed inputs: the pitches kept
    and dropped with why, each format's drafts kept and dropped, the screen,
    the drafted pitches of a video slot, and the record of a video."""
    known = {"evt:1", "trend:t1", "chart:tt:v1"}
    kept, dropped, ranked = studio.validate_pitches(
        {"pitches": PITCHES, "ranking": ["p8", "p1"]}, known, {"rent"}, ALL, 12, {"evt:1": "rent"},
        outside={"trend:t1"}, charts={"chart:tt:v1"})
    out = {"validate_pitches": json.dumps([kept, dropped, ranked], ensure_ascii=False)}
    raws = {"skit": [{"text": skit_t.WORDS, "skit": skit_t.RAW, "meaning": "m", "open_question": "q"},
                     {"text": skit_t.WORDS, "skit": {**skit_t.RAW, "premise": ""}},
                     {"text": skit_t.WORDS, "skit": {**skit_t.RAW, "score": ""}},
                     {"text": skit_t.WORDS}],
            "remake": [{"text": remake_t.WORDS, "remake": remake_t.RAW, "meaning": "m"},
                       {"text": remake_t.WORDS, "remake": {**remake_t.RAW, "shots": remake_t.SHOTS[:1]}},
                       {"remake": remake_t.RAW}]}
    for fmt, study in (("skit", None), ("remake", remake_t.STUDY), ("remake-no-study", None)):
        base = fmt.split("-")[0]
        got = studio.validate_drafts({"drafts": copy.deepcopy(raws[base])},
                                     {**(skit_t.PITCH if base == "skit" else remake_t.PITCH)}, 2, study)
        out[f"validate_drafts:{fmt}"] = json.dumps(got, ensure_ascii=False)
    d = drafts()
    near = copy.deepcopy(d["remake"])
    near["remake"]["shots"][0]["teeny"] = "Teeny hands Jensen Huang a spatula from the shelf"
    tiny = copy.deepcopy(d["skit"])
    tiny["text"] = skit_t.WORDS + " buy $TINY"
    screened = {fmt: studio.screen(draft, draft.get("truth", {}).get("facts") or [ITEM], [])
                for fmt, draft in {**d, "remake-near": near, "skit-tiny": tiny}.items()}
    out["screen"] = json.dumps(screened, ensure_ascii=False)
    by_id = {"p1": {"format": "post", "hook": "trend:a"}, "p2": {"format": "post", "hook": "trend:b"},
             "p3": {"format": "thread", "hook": None}, "p4": {"format": "skit", "hook": "trend:c"},
             "p5": {"format": "remake", "hook": "trend:d"}, "p6": {"format": "remake", "hook": "trend:e"}}
    chosen = [studio.choose_drafted(order, by_id, top, True, video_first=True)
              for order, top in ((["p1", "p2", "p3", "p6", "p4", "p5"], 3), (["p1", "p2", "p3"], 3),
                                 (["p5", "p1"], 2), (["p4", "p1", "p2"], 3))]
    out["choose_drafted"] = json.dumps(chosen, ensure_ascii=False)
    note = {"remake_id": "r1", "asset": 9, "models": ["m"], "cost_usd": 1.58, "label": "AI parody", "planned": False}
    out["outcome:skit"] = json.dumps(studio.skit_outcome(d["skit"], note, "post-1"), ensure_ascii=False)
    out["outcome:remake"] = json.dumps(studio.remake_outcome(d["remake"], note, None), ensure_ascii=False)
    return out


class RegistryTest(unittest.TestCase):
    def test_the_registry_holds_skit_then_remake_and_drives_the_video_formats(self):
        import formats

        self.assertEqual(list(formats.REGISTRY), ["skit", "remake"])
        for name, fmt in formats.REGISTRY.items():
            self.assertIsInstance(fmt, formats.Format)
            self.assertEqual(fmt.name, name)
        self.assertIs(formats.REGISTRY["skit"], formats.skit.FORMAT)
        self.assertIs(formats.REGISTRY["remake"], formats.remake.FORMAT)
        self.assertEqual(studio.VIDEO_FORMATS, tuple(formats.REGISTRY))
        self.assertEqual(studio.KNOWN_FORMATS, ("post", "thread", "image", "meme", *formats.REGISTRY))

    def test_the_room_is_one_class_whichever_module_names_it(self):
        import studio_room

        self.assertIs(studio.Room, studio_room.Room)
        self.assertIs(studio.Stop, studio_room.Stop)
        self.assertIs(studio._judge, studio_room._judge)

    def test_the_old_names_still_answer_from_the_studio(self):
        import formats

        for name in ("skit_of", "_make_skit", "skit_screen", "skit_outcome", "_skit_rules", "SKIT_PITCH",
                     "SKIT_RESERVE_SECS", "SKIT_CUT_SECS", "SKIT_HELD"):
            self.assertIs(getattr(studio, name), getattr(formats.skit, name), name)
        for name in ("remake_of", "_make_remake", "remake_screen", "remake_outcome", "study_source", "load_charts",
                     "chart_items", "study_for_writer", "REMAKE_PITCH", "REMAKE_MIN_SECS", "REMAKE_STORY_MAX",
                     "STUDY_KEYS"):
            self.assertIs(getattr(studio, name), getattr(formats.remake, name), name)


def _khaby():
    """A throwaway third format: the skit's, renamed, with hooks that say
    they ran."""
    import dataclasses

    import formats

    made: list = []

    def check_pitch(pitch, sources):
        return "" if pitch.get("hook") else "a khaby reacts to a trending story, and it has no hook"

    def make(room, draft, note):
        made.append(draft["khaby"])
        note["made"] = True
        return ["m-khaby"]

    def shape(raw, pitch, study):
        return {"khaby": {"logline": (raw or {}).get("logline"), "story": pitch.get("hook")}}, ""

    fmt = dataclasses.replace(formats.REGISTRY["skit"], name="khaby", pitch="KHABY PITCH", slot_line="KHABY SLOT",
                              editor="## judging a khaby", check_pitch=check_pitch, make=make, shape=shape,
                              screen=lambda draft, words, story: [], shown=lambda piece: {"logline": piece["logline"]},
                              time=lambda room: 100, min_secs=50,
                              outcome=lambda draft, note, post_id: {"made": note.get("made"), "post_id": post_id})
    return fmt, made


class ThirdFormatTest(unittest.TestCase):
    """A format registered on a copy of the registry reaches every path the
    video formats take, with no edit to bin/lib/studio.py."""

    def setUp(self):
        from unittest import mock

        import formats

        self.fmt, self.made = _khaby()
        patch = mock.patch.object(formats, "REGISTRY", {**formats.REGISTRY, "khaby": self.fmt})
        patch.start()
        self.addCleanup(patch.stop)

    def test_its_pitches_face_its_own_check(self):
        kept, dropped, _ = studio.validate_pitches({"pitches": [
            {"id": "p1", "format": "khaby", "hook": "trend:t1", "angle": "a"},
            {"id": "p2", "format": "khaby", "item": "evt:1", "angle": "a"}]},
            {"evt:1", "trend:t1"}, set(), ("post", "khaby"), 12, outside={"trend:t1"})
        self.assertEqual([p["id"] for p in kept], ["p1"])
        self.assertEqual(dropped[0]["why"], "a khaby reacts to a trending story, and it has no hook")

    def test_it_is_known_offered_to_the_writer_and_shown_to_the_editor(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            cfg = studio.Config.load({"STUDIO_FORMATS": "post,khaby"}, Path(tmp) / "none.env")
        self.assertEqual(cfg.formats, ("post", "khaby"))
        message = studio.pitch_message(cfg, INPUTS, [], SAMPLED, skit_slot=True)
        self.assertIn("KHABY PITCH", message)
        self.assertIn("KHABY SLOT", message)
        got, lost = studio.validate_drafts({"drafts": [{"text": "w", "khaby": {"logline": "l"}}]},
                                           pitch("khaby", hook="trend:t1"), 1)
        self.assertEqual((lost, got[0]["khaby"]), ([], {"logline": "l", "story": "trend:t1"}))
        draft = dict(got[0], id="d1")
        self.assertEqual(studio.shown(draft)["khaby"], {"logline": "l"})
        self.assertIn("## judging a khaby", studio.judge_message([("P1", "D1", "D2")],
                                                                 {"D1": draft, "D2": drafts()["post"]}, {}, []))
        self.assertIn('"line": "<with none: the khaby line it crosses>"', studio.final_message("D1", draft, {}, []))

    def test_its_video_is_made_by_its_own_producer_and_timed_by_its_own_floor(self):
        room = skit_t.Scripted(studio.Config(formats=("post", "khaby"), writer_model="w", editor_model="e",
                                             judge_model="j", budget_secs=3600),
                               REPO, REPO / "hands", REPO / "state", REPO / "brain", "run-1", dry=False)
        room.seen = []
        room.script = {"guard truth": (0, {"truthful": True, "unsupported": [], "reason": "ok", "judged": True}),
                       "guard clarity": (0, {"clear": True, "reason": "ok", "judged": True}),
                       "x post": (0, {"ok": True, "id": "post-1"})}
        draft = {"id": "d1", "pitch": "p1", "storyline": None, "shape": "scene", "format": "khaby", "text": "w",
                 "khaby": {"logline": "l", "story": "trend:t1"}, "truth": {"text": "w", "facts": []}}
        attempt: dict = {}
        self.assertEqual(studio.picture(room, draft, attempt), ["m-khaby"])
        self.assertEqual(attempt["picture"]["scene"], json.dumps(draft["khaby"], sort_keys=True))
        self.assertEqual(self.made, [draft["khaby"]])
        slot = studio.Slot(order=["D1"], blind={"D1": draft}, board={}, stories_now=[], pitches={"p1": pitch("khaby")},
                           items={}, stories={}, thread="t")
        attempt = {"label": "D1"}
        self.assertEqual(studio.try_draft(room, dict(draft, khaby={"logline": "m"}), attempt, slot, []), "posted")
        self.assertTrue(attempt["khaby"]["made"])
        post = next(b for a, b, *_ in room.seen if a[:2] == ["x", "post"])
        self.assertEqual(post["media_ids"], ["m-khaby"])
        self.assertTrue(studio.video_time(room, "khaby"))
        from unittest import mock

        import formats

        with mock.patch.dict(formats.REGISTRY, {"khaby": dataclasses_replace(self.fmt, min_secs=101)}):
            self.assertFalse(studio.video_time(room, "khaby"))


def dataclasses_replace(obj, **over):
    import dataclasses

    return dataclasses.replace(obj, **over)


class RemakeDropTest(unittest.TestCase):
    """A remake whose keys were bought but that will not be rendered lets
    go of its reservation at once (`hands/video remake-drop`), instead of
    holding it until release_stale two hours later."""

    def go(self, room):
        slot = studio.Slot(order=["D1"], blind={"D1": remake_t.remake_draft()}, board={}, stories_now=[],
                           pitches={"p1": remake_t.PITCH}, items={"trend:t1": remake_t.HOOK,
                                                                  "chart:tt:v1": remake_t.CHART},
                           stories={}, thread="t")
        attempt: dict = {"label": "D1"}
        result = studio.try_draft(room, remake_t.remake_draft(), attempt, slot, [])
        return result, attempt, [(a, b, d) for a, b, d, _ in room.seen if " ".join(a[:2]) == "video remake-drop"]

    def test_keys_the_viewer_refuses_are_dropped_so_their_reservation_is_freed_at_once(self):
        refused = (0, {"ok": False, "teeny": 1, "interacts": False, "reason": "Teeny in 1 of 2", "judged": True})
        result, attempt, drops = self.go(remake_t._producer_room(**{"guard view-remake": refused}))
        self.assertEqual(result, "next")
        self.assertEqual(drops, [(["video", "remake-drop"], {"remake_id": "r1"}, False)])
        self.assertIn("keyframes", attempt["remake"]["failed"])

    def test_keys_that_came_back_short_are_dropped(self):
        short = (0, {**remake_t.KEYS, "keys": remake_t.KEYS["keys"][:1]})
        _, _, drops = self.go(remake_t._producer_room(**{"video remake-keys": short}))
        self.assertEqual(drops, [(["video", "remake-drop"], {"remake_id": "r1"}, False)])

    def test_keys_that_ate_the_time_are_dropped_before_the_video(self):
        room = remake_t._producer_room()

        def keys(body):
            room.started -= 3600 - (studio.SKIT_RESERVE_SECS + studio.SKIT_CUT_SECS + 100)
            return 0, remake_t.KEYS

        room.script["video remake-keys"] = keys
        result, attempt, drops = self.go(room)
        self.assertIn("not enough of the run's time", attempt["remake"]["failed"])
        self.assertEqual(drops, [(["video", "remake-drop"], {"remake_id": "r1"}, False)])

    def test_a_drop_that_fails_is_a_warning_and_the_slot_falls_back_as_before(self):
        refused = (0, {"ok": False, "teeny": 1, "interacts": False, "reason": "Teeny in 1 of 2", "judged": True})
        room = remake_t._producer_room(**{"guard view-remake": refused,
                                          "video remake-drop": (1, {"error": "the media lock is busy"})})
        result, attempt, drops = self.go(room)
        self.assertEqual((result, len(drops)), ("next", 1))
        self.assertIn("keyframes", attempt["remake"]["failed"])
        self.assertTrue(any("remake-drop" in w and "r1" in w for w in room.warnings), room.warnings)

    def test_nothing_is_dropped_when_no_keys_were_bought_or_the_video_was_asked_for(self):
        for script in ({"video remake-keys": (2, {"ok": False, "refused": "off", "rule": "off"})},
                       {"video remake-keys": (0, {**remake_t.KEYS, "remake_id": None})},
                       {"video remake-keys": (0, {"dry_run": True})},
                       {"video remake": (1, {"ok": False, "error": "the cut failed"})},
                       {}):
            with self.subTest(script=script):
                _, _, drops = self.go(remake_t._producer_room(**script))
                self.assertEqual(drops, [])


class PromptGoldenTest(unittest.TestCase):
    """The prompts are byte for byte main's, from before the formats moved."""

    def test_every_prompt_is_byte_for_byte_the_golden(self):
        want = json.loads(GOLDENS.read_text(encoding="utf-8"))
        got = build()
        self.assertEqual(sorted(got), sorted(want))
        for name in want:
            with self.subTest(name=name):
                self.assertEqual(got[name], want[name])


if __name__ == "__main__":
    if sys.argv[1:] == ["--write-goldens"]:
        GOLDENS.parent.mkdir(parents=True, exist_ok=True)
        GOLDENS.write_text(json.dumps(build(), ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        print(f"wrote {GOLDENS}")
    else:
        unittest.main()
