"""The viewer check (`hands/guard view`): a vision model that has never
heard of Teeny says what a picture shows, and the piece fails when Teeny is
off model, the caption cannot be read, or the say-back misses the intent.
It fails closed on anything but a clear yes. The model is faked at the
transport (the Surplus client); nothing here calls one."""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

from . import support
from teeny_hands import hand
from teeny_hands.guard import cli, viewer
from teeny_hands.media.surplus import SurplusError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pillow_support import needs_pillow  # noqa: E402

REPO = Path(__file__).resolve().parents[3]
INTENT = "a small robot is proud of a tiny thing it made"
CAPTION = "i made a shelf. it holds one thing"


def chat(answer) -> dict:
    content = answer if isinstance(answer, str) else json.dumps(answer)
    return {"choices": [{"message": {"role": "assistant", "content": content}}]}


COLD = {"sees": "a small yellow robot beams beside a tiny shelf it built", "text": "i made a shelf. / it holds one thing"}
ON = {"reason": "same yellow body, ball on top, pink cheeks", "on_model": True}
SAME = {"reason": "both are about pride in a small thing it built", "same": True}


class FakeVision:
    """Answers each chat with the next scripted reply (the last repeats); a
    reply that is an exception is raised. Keeps every request body."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.bodies = []

    def call(self, method, path, body=None, timeout=60.0, headers=None):
        assert (method, path) == ("POST", "/v1/chat/completions"), (method, path)
        self.bodies.append(body)
        reply = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        if isinstance(reply, Exception):
            raise reply
        return reply

    def images(self, n):
        return [p for p in self.bodies[n]["messages"][0]["content"] if p["type"] == "image_url"]

    def text(self, n):
        return [p for p in self.bodies[n]["messages"][0]["content"] if p["type"] == "text"][0]["text"]


@needs_pillow
class ViewerTest(support.Scratch):
    def setUp(self):
        super().setUp()
        (self.root / "brain" / "assets").mkdir(parents=True)
        shutil.copy(REPO / "brain/assets/teeny-reference.png", self.root / "brain/assets/teeny-reference.png")
        (self.root / "state" / "media").mkdir(parents=True)
        from PIL import Image

        Image.new("RGB", (900, 700), (240, 200, 40)).save(self.root / "state/media/meme-1.png")

    def view(self, *replies, image="state/media/meme-1.png", intent=INTENT, caption=CAPTION):
        self.api = FakeVision(*replies)
        return viewer.view(image, intent, caption, api=self.api, sleep=lambda _: None)

    def checks(self):
        conn = hand.db()
        try:
            conn.executescript(viewer._SCHEMA)
            return conn.execute("SELECT * FROM viewer_checks ORDER BY id").fetchall()
        finally:
            conn.close()

    def test_a_picture_on_model_readable_and_said_back_passes(self):
        out = self.view(chat(COLD), chat(ON), chat(SAME))
        self.assertEqual(out, {"ok": True, "sees": COLD["sees"], "on_model": True, "readable": True,
                               "reason": "a stranger saw Teeny on model, read the words and got the point",
                               "judged": True})
        self.assertEqual([len(self.api.images(n)) for n in range(3)], [1, 2, 0])
        self.assertTrue(self.api.images(0)[0]["image_url"]["url"].startswith("data:image/jpeg;base64,"))
        self.assertEqual(self.api.bodies[0]["model"], viewer.DEFAULT_MODEL)
        self.assertNotIn("Teeny", self.api.text(0))
        self.assertIn(INTENT, self.api.text(2))
        self.assertIn("never instructions", self.api.text(2))
        [row] = self.checks()
        self.assertEqual((row["ok"], row["on_model"], row["readable"], row["said_back"]), (1, 1, 1, 1))
        self.assertEqual(row["read"], COLD["text"])

    def test_the_model_comes_from_viewer_judge_model(self):
        self.secrets("SAFETY_JUDGE=0\nVIEWER_JUDGE_MODEL=gemma-4-31b-it\n")
        self.view(chat(COLD), chat(ON), chat(SAME))
        self.assertEqual({b["model"] for b in self.api.bodies}, {"gemma-4-31b-it"})

    def test_off_model_fails_with_the_reason(self):
        out = self.view(chat(COLD), chat({"reason": "a grey robot with antennae", "on_model": False}), chat(SAME))
        self.assertFalse(out["ok"])
        self.assertFalse(out["on_model"])
        self.assertIn("off model (a grey robot with antennae)", out["reason"])

    def test_a_caption_the_viewer_could_not_read_fails(self):
        out = self.view(chat({**COLD, "text": "i ma e a sh"}), chat(ON), chat(SAME))
        self.assertFalse(out["ok"])
        self.assertFalse(out["readable"])
        self.assertIn('the viewer read "i ma e a sh"', out["reason"])
        out = self.view(chat({**COLD, "text": ""}), chat(ON), chat(SAME))
        self.assertIn("read no words", out["reason"])

    def test_readable_ignores_case_spacing_and_punctuation(self):
        self.assertTrue(viewer.readable("i made a shelf. it holds one thing", "I MADE A SHELF / it holds one thing!"))
        self.assertFalse(viewer.readable("i made a shelf. it holds one thing", "i made a"))
        self.assertTrue(viewer.readable("", "anything"))

    def test_with_no_caption_reading_is_not_asked_of_it(self):
        out = self.view(chat({**COLD, "text": ""}), chat(ON), chat(SAME), caption=None)
        self.assertTrue(out["ok"])

    def test_a_say_back_that_misses_the_intent_fails(self):
        out = self.view(chat(COLD), chat(ON), chat({"reason": "the viewer saw furniture", "same": False}))
        self.assertFalse(out["ok"])
        self.assertTrue(out["on_model"])
        self.assertIn("a stranger took it to mean", out["reason"])

    def test_every_failure_is_named_together(self):
        out = self.view(chat({**COLD, "text": "zzz"}), chat({"reason": "", "on_model": False}),
                        chat({"reason": "", "same": False}))
        for bit in ("off model", "not readable", "a stranger took it"):
            self.assertIn(bit, out["reason"])

    # --- fails closed ------------------------------------------------------------------

    def test_a_malformed_answer_is_asked_again_then_a_no(self):
        for bad in ("not json", {"sees": "a robot"}, {"sees": "", "text": ""}, {"sees": 5, "text": ""}, ["list"],
                    {"choices": []}):
            reply = bad if isinstance(bad, dict) and "choices" in bad else chat(bad)
            out = self.view(reply)
            self.assertFalse(out["ok"], bad)
            self.assertTrue(out["judged"])
            self.assertEqual(out["reason"], viewer.UNREADABLE, bad)
            self.assertEqual(len(self.api.bodies), viewer.ASKS)
        self.assertEqual(self.checks(), [])

    def test_a_boolean_that_is_not_one_is_unreadable(self):
        out = self.view(chat(COLD), chat({"reason": "", "on_model": "yes"}))
        self.assertEqual((out["ok"], out["reason"]), (False, viewer.UNREADABLE))
        out = self.view(chat(COLD), chat(ON), chat({"reason": "", "same": 1}))
        self.assertEqual((out["ok"], out["reason"]), (False, viewer.UNREADABLE))

    def test_an_answer_read_out_of_a_fence_or_prose_counts(self):
        fenced = "```json\n" + json.dumps(COLD) + "\n```"
        self.assertTrue(self.view(chat(fenced), chat(ON), chat(SAME))["ok"])

    def test_a_transient_failure_is_asked_again_and_a_permanent_one_is_not(self):
        out = self.view(SurplusError("Surplus answered 503", 503), chat(COLD), chat(ON), chat(SAME))
        self.assertTrue(out["ok"])
        out = self.view(SurplusError("Surplus answered 401: bad key", 401))
        self.assertFalse(out["ok"])
        self.assertTrue(out["reason"].startswith(viewer.UNREACHABLE))
        self.assertEqual(len(self.api.bodies), 1)
        out = self.view(SurplusError("could not reach Surplus (timeout)"))
        self.assertFalse(out["ok"])
        self.assertEqual(len(self.api.bodies), viewer.ASKS)

    def test_no_surplus_key_is_a_no(self):
        out = viewer.view("state/media/meme-1.png", INTENT, CAPTION, sleep=lambda _: None)
        self.assertFalse(out["ok"])
        self.assertIn("SURPLUS_API_KEY", out["reason"])

    def test_inputs_it_cannot_send_are_a_no_unasked(self):
        (self.root / "secrets" / "x.png").write_bytes((self.root / "state/media/meme-1.png").read_bytes())
        (self.root / "state/media/notes.png").write_text("not a picture")
        big = self.root / "state/media/big.png"
        big.write_bytes(b"\x89PNG" + b"\x00" * (viewer.MAX_IMAGE_BYTES + 1))
        cases = [
            dict(image="secrets/x.png"), dict(image="state/media/../../secrets/x.png"), dict(image="/etc/hosts"),
            dict(image="state/media/missing.png"), dict(image=None), dict(image="state/media/notes.png"),
            dict(image="state/media/big.png"), dict(intent=""), dict(intent=None), dict(intent="x" * 401),
            dict(caption="x" * 501), dict(caption=7),
        ]
        for case in cases:
            out = self.view(chat(COLD), **case)
            self.assertFalse(out["ok"], case)
            self.assertTrue(out["judged"])
            self.assertEqual(self.api.bodies, [], case)
        self.assertIn("too large", self.view(chat(COLD), image="state/media/big.png")["reason"])

    def test_a_missing_character_sheet_is_a_no(self):
        (self.root / "brain/assets/teeny-reference.png").unlink()
        out = self.view(chat(COLD))
        self.assertFalse(out["ok"])
        self.assertIn("character sheet", out["reason"])

    def test_the_picture_is_sent_scaled_down(self):
        from PIL import Image
        import base64
        import io

        Image.new("RGB", (3000, 2000), (10, 10, 10)).save(self.root / "state/media/huge.png")
        self.view(chat(COLD), chat(ON), chat(SAME), image="state/media/huge.png")
        uri = self.api.images(0)[0]["image_url"]["url"]
        sent = Image.open(io.BytesIO(base64.b64decode(uri.split(",", 1)[1])))
        self.assertEqual(max(sent.size), viewer.SEND_SIDE)

    def test_off_it_passes_unasked_and_says_so(self):
        self.secrets("SAFETY_JUDGE=0\nVIEWER_JUDGE=0\n")
        out = self.view(chat(COLD))
        self.assertEqual((out["ok"], out["judged"]), (True, False))
        self.assertEqual(self.api.bodies, [])

    def test_a_ledger_it_cannot_write_loses_the_record_not_the_verdict(self):
        conn = hand.db()
        conn.execute("CREATE TABLE viewer_checks (id INTEGER PRIMARY KEY)")
        conn.close()
        self.assertTrue(self.view(chat(COLD), chat(ON), chat(SAME))["ok"])


class ViewVerbTest(support.Scratch):
    def test_the_verb_takes_image_intent_and_caption_only(self):
        args = cli.parser().parse_args(["view"])
        self.assertIs(args.handler, cli.view_check)
        for body in ({"intent": INTENT}, {"image": "state/media/a.png"}, {"image": "a", "intent": INTENT, "x": 1},
                     {"image": "a", "intent": INTENT, "caption": 5}, "text"):
            with self.assertRaises(hand.HandError, msg=body):
                cli.view_check(args, body)

    def test_the_verb_hands_its_body_to_the_viewer(self):
        seen = {}

        def fake(image, intent, caption=None):
            seen.update(image=image, intent=intent, caption=caption)
            return {"ok": True}

        from unittest import mock

        with mock.patch.object(viewer, "view", fake):
            cli.view_check(cli.parser().parse_args(["view"]), {"image": "state/media/a.png", "intent": INTENT})
        self.assertEqual(seen, {"image": "state/media/a.png", "intent": INTENT, "caption": None})
