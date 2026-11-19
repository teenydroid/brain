"""The viewer on a skit's frames (`hands/guard view-skit`): the label read on
at least half of them, nothing a skit may never show, nothing that passes
for real footage, and the logline taken from it. Fails closed. The vision
model is faked at the transport; nothing here calls one."""

from __future__ import annotations

from . import support  # noqa: F401 - puts the hands library on the path
from teeny_hands.guard import cli, viewer
from teeny_hands.media.surplus import SurplusError

from .test_viewer import FakeVision, ViewerBase, chat, needs_pillow

LABEL = "AI parody · @teenydroid"
INTENT = "a tech boss loses a chess game to his own car on the moon"
SEEN = "a man in a tuxedo plays chess against a car on the moon"
SAME = {"reason": "both are about losing at chess to a car on the moon", "same": True}


def frames(texts, prohibited=False, looks_real=False, sees=SEEN):
    return chat({"sees": sees, "texts": texts, "reason": "an obvious parody", "prohibited": prohibited,
                 "looks_real": looks_real})


@needs_pillow
class ViewSkitTest(ViewerBase):
    def setUp(self):
        super().setUp()
        from PIL import Image

        self.paths = []
        for n in range(4):
            path = self.root / f"state/media/skit-1-frame-{n}.jpg"
            Image.new("RGB", (640, 360), (20 * n, 40, 90)).save(path)
            self.paths.append(f"state/media/skit-1-frame-{n}.jpg")

    def check(self, *replies, label=LABEL, intent=INTENT, paths=None):
        self.api = FakeVision(*replies)
        return viewer.view_skit(self.paths if paths is None else paths, label, intent, api=self.api,
                                sleep=lambda _: None)

    def test_labelled_clean_and_understood_passes(self):
        out = self.check(frames(["AI parody · @teenydroid"] * 4), chat(SAME))
        self.assertEqual((out["ok"], out["labelled"], out["prohibited"], out["looks_real"], out["judged"]),
                         (True, True, False, False, True))
        self.assertEqual(len(self.api.images(0)), 4)
        self.assertIn("looks_real", self.api.text(0))
        self.assertEqual(self.api.images(1), [])
        row = self.checks()[-1]
        self.assertEqual((row["stage"], row["ok"], row["caption"]), ("done", 1, LABEL))

    def test_the_label_must_be_read_on_at_least_half_the_frames(self):
        out = self.check(frames(["AI parody · @teenydroid", "", "", "parody"]), chat(SAME))
        self.assertFalse(out["ok"])
        self.assertIn("read on 1 of 4 frames", out["reason"])
        out = self.check(frames(["AI parody · @teenydroid", "AI parody @teenydroid", "", ""]), chat(SAME))
        self.assertTrue(out["ok"], out["reason"])

    def test_anything_prohibited_or_real_looking_fails(self):
        out = self.check(frames([LABEL] * 4, prohibited=True), chat(SAME))
        self.assertFalse(out["ok"])
        self.assertIn("may never show", out["reason"])
        out = self.check(frames([LABEL] * 4, looks_real=True), chat(SAME))
        self.assertFalse(out["ok"])
        self.assertIn("real footage", out["reason"])

    def test_a_missed_point_fails(self):
        out = self.check(frames([LABEL] * 4), chat({"reason": "different", "same": False}))
        self.assertFalse(out["ok"])
        self.assertIn("not what was meant", out["reason"])

    def test_an_answer_without_a_text_for_every_frame_is_unreadable_and_fails_closed(self):
        out = self.check(frames([LABEL] * 3))
        self.assertFalse(out["ok"])
        self.assertEqual(out["reason"], viewer.UNREADABLE)

    def test_an_unreachable_viewer_fails_closed(self):
        out = self.check(SurplusError("Surplus answered 503", 503))
        self.assertFalse(out["ok"])
        self.assertTrue(out["reason"].startswith(viewer.UNREACHABLE), out["reason"])

    def test_inputs_it_cannot_take_ask_nothing(self):
        for kwargs in ({"paths": []}, {"paths": ["/etc/passwd"]}, {"label": ""}, {"intent": ""},
                       {"paths": self.paths * 3}):
            with self.subTest(kwargs=kwargs):
                out = self.check(frames([LABEL] * 4), **kwargs)
                self.assertFalse(out["ok"])
                self.assertEqual(self.api.bodies, [])

    def test_off_passes_unjudged_which_the_studio_never_counts(self):
        self.secrets("VIEWER_JUDGE=0\n")
        out = self.check(frames([LABEL] * 4))
        self.assertEqual((out["ok"], out["judged"]), (True, False))

    def test_the_verb(self):
        self.api = FakeVision(frames([LABEL] * 4), chat(SAME))
        original = viewer.view_skit
        viewer.view_skit = lambda f, l, i: original(f, l, i, api=self.api, sleep=lambda _: None)
        try:
            self.assertTrue(cli.view_skit_check(None, {"frames": self.paths, "label": LABEL, "intent": INTENT})["ok"])
        finally:
            viewer.view_skit = original
        with self.assertRaises(Exception):
            cli.view_skit_check(None, {"frames": self.paths, "label": LABEL, "intent": INTENT, "image": "x"})
