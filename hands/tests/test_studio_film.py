"""bin/lib/formats/film.py: the studio's story film format (docs/video.md §12).
Offered only on a film day; every text a machine is told past the guard's
task screen, then the film check, before any money; made inline by
hands/film; the viewer on its frames; then the upload and the post. The
hands are scripted (test_studio_skit.Scripted); nothing is bought."""

from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO / "bin" / "lib"))
sys.path.insert(0, str(REPO / "hands" / "lib"))
sys.path.insert(0, str(HERE))
import formats  # noqa: E402
import studio  # noqa: E402
from teeny_hands.guard import film as guard_film  # noqa: E402

from test_studio_skit import CARD, Scripted, calls_of  # noqa: E402

ACTION = ("Wide anamorphic shot of a showroom on the moon: Elon Musk pulls a velvet sheet off a chrome robotaxi and "
          "it honks a long wet fart at the crowd of twelve.")
RAW = {"figures": ["Elon Musk"], "logline": "elon musk unveils a car that drives itself, and it drives off without him",
       "look": {"Elon Musk": "silver tuxedo, a velvet sheet"}, "setting": "a marble showroom on the moon",
       "score": "a pompous brass fanfare", "aspect": "9:16",
       "beats": [{"subject": "figure", "action": ACTION, "narration": "this man says his car drives itself",
                  "sfx": ["a long wet honk"]},
                 {"subject": "figure", "action": ACTION.replace("pulls", "chases"),
                  "narration": "it does. away from him."},
                 {"subject": "teeny", "action": "Macro shot: Teeny, the small yellow robot, watches from a moon "
                                                "crater and slowly raises a tiny paddle that scores it a ten.",
                  "narration": "ten out of ten. no notes."}]}
WORDS = "tesla says its robotaxi drives itself, so i staged the launch on the moon. a parody film"
CUT = {"path": "state/media/film-9.mp4", "frames": ["state/media/film-9-frame-0.jpg"],
       "label": "AI parody · @teenydroid", "seconds": 24.0, "stills": []}
MADE = {"ok": True, "id": 9, "status": "done", "cut": CUT, "spent_usd": 6.2, "stills": []}


def film_post(raw=None):
    kept, lost = studio.validate_posts(copy.deepcopy([{"card": CARD["id"], "format": "film", "text": WORDS,
                                                       "film": raw or RAW}]), [CARD], ("post", "film"))
    assert kept, lost
    post = kept[0]
    post["label"] = "P1"
    return post


def room(dry=False, **script):
    cfg = studio.Config(formats=("post", "film"), writer_model="w", editor_model="e", judge_model="j",
                        budget_secs=3600)
    r = Scripted(cfg, REPO, REPO / "hands", REPO / "state", REPO / "brain", "run-1", dry=dry)
    r.seen = []
    r.script = {
        "film due": (0, {"ok": True, "due": True}),
        "guard check": lambda body: (0, {"ok": True, "text": body["text"]}),
        "guard film": (0, {"ok": True, "rule": "none", "reason": "allowed", "screen": [], "judged": True}),
        "film make": (0, MADE),
        "guard view-skit": (0, {"ok": True, "labelled": True, "reason": "ok", "judged": True}),
        "x upload": (0, {"ok": True, "media_id": "m-9"}),
        "x post": (0, {"ok": True, "id": "post-1"}),
        **script,
    }
    return r


class FilmFormatTest(unittest.TestCase):
    def go(self, r, post=None):
        post = post or film_post()
        attempt: dict = {"label": "P1", "card": post["card"], "format": "film"}
        return studio.try_post(r, "P1", post, attempt), attempt, calls_of(r)

    def test_the_film_is_registered_after_the_other_videos(self):
        self.assertIs(formats.REGISTRY["film"], formats.film.FORMAT)
        self.assertEqual(list(formats.REGISTRY)[-1], "film")

    def test_a_film_goes_through_every_check_in_order_then_out(self):
        r = room()
        result, attempt, calls = self.go(r)
        self.assertEqual(result, "posted")
        self.assertEqual([c for c in calls if c != "guard check"],
                         ["guard film", "film make", "guard view-skit", "x upload", "x post"])
        made = next(b for a, b, *_ in r.seen if a[:2] == ["film", "make"])
        self.assertEqual(made["film"]["text"], WORDS)
        self.assertEqual(made["story"], studio.story_of(CARD))
        self.assertEqual((attempt["film"]["media_id"], attempt["film"]["cost_usd"]), ("m-9", 6.2))

    def test_every_text_a_machine_is_told_is_past_the_task_screen_first(self):
        r = room()
        self.go(r)
        checked = [b["text"] for a, b, *_ in r.seen if a[:2] == ["guard", "check"]]
        kept, _ = guard_film.shape({**RAW, "text": WORDS})
        for i in range(3):
            self.assertIn(guard_film.beat_prompt(kept, i, guard_film.PROMPT_SECONDS), checked)
        for line in ("this man says his car drives itself", "a long wet honk", RAW["logline"], RAW["score"]):
            self.assertIn(line, checked)
        first_paid = calls_of(r).index("film make")
        self.assertTrue(all(c == "guard check" for c in calls_of(r)[:first_paid - 1]))

    def test_a_film_the_hard_lines_refuse_buys_nothing(self):
        r = room(**{"guard film": (0, {"ok": False, "rule": "screen", "reason": "no", "judged": True})})
        result, attempt, calls = self.go(r)
        self.assertEqual(result, "next")
        self.assertNotIn("film make", calls)

    def test_a_dry_room_plans_the_film_and_buys_nothing(self):
        r = room(dry=True)
        _, attempt, calls = self.go(r)
        self.assertNotIn("film make", calls)
        self.assertTrue(attempt["film"].get("planned"))

    def test_a_film_with_no_label_or_a_failed_viewer_is_never_posted(self):
        for over in ({"film make": (0, {**MADE, "cut": {**CUT, "label": "nothing"}})},
                     {"guard view-skit": (0, {"ok": False, "reason": "looks real", "judged": True})}):
            with self.subTest(over=list(over)):
                result, _, calls = self.go(room(**over))
                self.assertEqual(result, "next")
                self.assertNotIn("x upload", calls)

    def test_a_film_is_offered_only_when_one_is_due(self):
        self.assertTrue(formats.film.offered(room()))
        self.assertFalse(formats.film.offered(room(**{"film due": (0, {"ok": True, "due": False})})))
        self.assertFalse(formats.film.offered(room(**{"film due": (1, "Traceback")})))

    def test_a_beat_with_teeny_and_the_figure_together_is_not_a_film(self):
        bad = copy.deepcopy(RAW)
        bad["beats"][0]["subject"] = "both"
        kept, lost = studio.validate_posts([{"card": CARD["id"], "format": "film", "text": WORDS, "film": bad}],
                                           [CARD], ("post", "film"))
        self.assertEqual(kept, [])
        self.assertIn("never meets Teeny", str(lost))


if __name__ == "__main__":
    unittest.main()
