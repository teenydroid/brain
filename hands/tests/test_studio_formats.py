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
    return out


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
