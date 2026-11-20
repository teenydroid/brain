"""The viewer on a remake (`hands/guard view-remake`): on the keyframes,
before any video money, and on the cut's frames after. Teeny on model
(every keyframe; at least half the cut's frames), nobody in a frame meeting
it, nothing prohibited, nothing real-looking, the logline said back, and on
the cut the label read. Fails closed. The vision model is faked at the
transport; nothing here calls one. Only the picture's encoding is stood in
for (`viewer.data_uri`, which needs Pillow), so these run without it."""

from __future__ import annotations

from unittest import mock

from . import support
from teeny_hands import hand
from teeny_hands.guard import cli, viewer
from teeny_hands.media.surplus import SurplusError

from .test_viewer import FakeVision, chat

LABEL = "AI parody · @teenydroid"
INTENT = "a tech boss cracks his chip into a pan and the pan floats away"
SEEN = "a man in a leather jacket cooks in a kitchen where the pan floats and graphics cards hang in the air"
SAME = {"reason": "both are about the chip, the pan and the float", "same": True}


def frames(texts, prohibited=False, looks_real=False, sees=SEEN):
    return chat({"sees": sees, "texts": texts, "reason": "an obvious parody", "prohibited": prohibited,
                 "looks_real": looks_real})


def teeny(on, interacts=False):
    return chat({"reason": "the yellow figure on the shelf", "teeny": on, "interacts": interacts})


class ViewRemakeBase(support.Scratch):
    def setUp(self):
        super().setUp()
        (self.root / "brain" / "assets").mkdir(parents=True)
        (self.root / "brain/assets/teeny-reference.png").write_bytes(b"sheet")
        (self.root / "state" / "media").mkdir(parents=True)
        self.keys, self.cut = [], []
        for n in range(1, 4):
            (self.root / f"state/media/remake-r1-key-{n}.png").write_bytes(b"key %d" % n)
            self.keys.append(f"state/media/remake-r1-key-{n}.png")
        for n in range(4):
            (self.root / f"state/media/remake-r1-frame-{n}.jpg").write_bytes(b"frame %d" % n)
            self.cut.append(f"state/media/remake-r1-frame-{n}.jpg")
        encode = mock.patch.object(viewer, "data_uri", lambda data: "data:image/jpeg;base64," + data.hex())
        encode.start()
        self.addCleanup(encode.stop)

    def check(self, *replies, paths=None, label=None, stage="keys", intent=INTENT, shots=None):
        self.api = FakeVision(*replies)
        paths = (self.keys if stage == "keys" else self.cut) if paths is None else paths
        return viewer.view_remake(paths, label, intent, stage, shots=shots, api=self.api, sleep=lambda _: None)

    def checks(self):
        conn = hand.db()
        try:
            conn.executescript(viewer._SCHEMA)
            return conn.execute("SELECT * FROM viewer_checks ORDER BY id").fetchall()
        finally:
            conn.close()


class KeysTest(ViewRemakeBase):
    def test_keyframes_pass_without_a_label_with_teeny_in_every_one(self):
        out = self.check(frames(["", "", ""]), teeny([True, True, True]), chat(SAME), shots=3)
        self.assertEqual((out["ok"], out["teeny"], out["interacts"], out["prohibited"], out["looks_real"],
                          out["labelled"], out["judged"]), (True, 3, False, False, False, None, True), out["reason"])
        # The frames pass, then the character sheet beside every keyframe, then the say-back.
        self.assertEqual([len(self.api.images(n)) for n in range(3)], [3, 4, 0])
        self.assertIn("looks_real", self.api.text(0))
        self.assertIn(viewer.FRAMES.splitlines()[0][:40], self.api.text(0))
        self.assertIn("character sheet", self.api.text(1))
        self.assertIn("looks at, touches or holds", self.api.text(1))
        self.assertEqual(self.api.images(1)[0]["image_url"]["url"], "data:image/jpeg;base64," + b"sheet".hex())
        row = self.checks()[-1]
        self.assertEqual((row["stage"], row["ok"], row["on_model"]), ("done", 1, 1))

    def test_teeny_missing_or_off_model_in_one_keyframe_is_refused(self):
        out = self.check(frames(["", "", ""]), teeny([True, False, True]), chat(SAME))
        self.assertFalse(out["ok"])
        self.assertEqual(out["teeny"], 2)
        self.assertIn("Teeny on model in 2 of 3 keyframes", out["reason"])

    def test_anyone_meeting_teeny_is_refused(self):
        out = self.check(frames(["", "", ""]), teeny([True, True, True], interacts=True), chat(SAME))
        self.assertFalse(out["ok"])
        self.assertTrue(out["interacts"])
        self.assertIn("looks at, touches or holds", out["reason"])

    def test_a_real_looking_or_prohibited_keyframe_is_refused(self):
        out = self.check(frames(["", "", ""], looks_real=True), teeny([True] * 3), chat(SAME))
        self.assertFalse(out["ok"])
        self.assertIn("real footage", out["reason"])
        out = self.check(frames(["", "", ""], prohibited=True), teeny([True] * 3), chat(SAME))
        self.assertFalse(out["ok"])
        self.assertIn("may never show", out["reason"])

    def test_a_missed_point_is_refused(self):
        out = self.check(frames(["", "", ""]), teeny([True] * 3), chat({"reason": "no", "same": False}))
        self.assertFalse(out["ok"])
        self.assertIn("not what was meant", out["reason"])

    def test_an_answer_without_a_verdict_for_every_frame_fails_closed(self):
        out = self.check(frames(["", "", ""]), teeny([True, True]))
        self.assertFalse(out["ok"])
        self.assertEqual(out["reason"], viewer.UNREADABLE)

    def test_an_unreachable_viewer_fails_closed(self):
        out = self.check(SurplusError("Surplus answered 503", 503))
        self.assertFalse(out["ok"])
        self.assertTrue(out["reason"].startswith(viewer.UNREACHABLE), out["reason"])

    def test_inputs_it_cannot_take_ask_nothing(self):
        for kwargs in ({"paths": []}, {"paths": ["/etc/passwd"]}, {"intent": ""}, {"stage": "rough"},
                       {"paths": self.keys * 3}, {"shots": 2}, {"stage": "cut", "label": None},
                       {"stage": "cut", "label": "x" * 81}):
            with self.subTest(kwargs=kwargs):
                out = self.check(frames([""] * 3), **kwargs)
                self.assertFalse(out["ok"])
                self.assertEqual(self.api.bodies, [])

    def test_off_passes_unjudged_which_the_studio_never_counts(self):
        self.secrets("VIEWER_JUDGE=0\n")
        out = self.check(frames([""] * 3))
        self.assertEqual((out["ok"], out["judged"]), (True, False))
        self.assertEqual(self.api.bodies, [])

    def test_it_counts_against_the_viewers_day_cap(self):
        self.secrets("VIEWER_MAX_PER_DAY=0\n")
        out = self.check(frames([""] * 3))
        self.assertFalse(out["ok"])
        self.assertIn("VIEWER_MAX_PER_DAY", out["reason"])
        self.assertEqual(self.api.bodies, [])


class CutTest(ViewRemakeBase):
    def test_the_cut_needs_its_label_on_half_the_frames_and_teeny_on_half(self):
        out = self.check(frames([LABEL, LABEL, "", ""]), teeny([True, False, True, False]), chat(SAME),
                         stage="cut", label=LABEL)
        self.assertEqual((out["ok"], out["labelled"], out["teeny"]), (True, True, 2), out["reason"])
        self.assertEqual(self.checks()[-1]["caption"], LABEL)

    def test_the_label_read_on_too_few_frames_is_refused(self):
        out = self.check(frames([LABEL, "", "", ""]), teeny([True] * 4), chat(SAME), stage="cut", label=LABEL)
        self.assertFalse(out["ok"])
        self.assertIn("read on 1 of 4 frames", out["reason"])

    def test_teeny_on_fewer_than_half_the_cuts_frames_is_refused(self):
        out = self.check(frames([LABEL] * 4), teeny([True, False, False, False]), chat(SAME), stage="cut",
                         label=LABEL)
        self.assertFalse(out["ok"])
        self.assertIn("Teeny on model in 1 of 4 frames", out["reason"])


class VerbTest(ViewRemakeBase):
    def test_the_verb(self):
        self.api = FakeVision(frames([""] * 3), teeny([True] * 3), chat(SAME))
        original = viewer.view_remake
        viewer.view_remake = lambda f, l, i, s, shots=None: original(f, l, i, s, shots=shots, api=self.api,
                                                                      sleep=lambda _: None)
        try:
            out = cli.view_remake_check(None, {"frames": self.keys, "label": None, "intent": INTENT, "stage": "keys",
                                               "shots": 3})
            self.assertTrue(out["ok"], out["reason"])
        finally:
            viewer.view_remake = original
        with self.assertRaises(hand.HandError):
            cli.view_remake_check(None, {"frames": self.keys, "label": None, "intent": INTENT, "stage": "keys",
                                         "image": "x"})
