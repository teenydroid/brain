"""media/audio.py: a story film's sound from ElevenLabs on Surplus's Provider
Market (narration with word times, a score, effects, a transcript), and the
Surplus client's byte-and-upload call it rides on."""

import base64
import json
import os
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent / "lib"))

from teeny_hands import hand  # noqa: E402
from teeny_hands.media import audio  # noqa: E402
from teeny_hands.media.surplus import Surplus, SurplusError  # noqa: E402

MP3 = b"ID3\x04\x00\x00\x00\x00\x00\x00" + b"\xff\xfb" * 64


def alignment(text, step=0.1):
    chars = list(text)
    return {"characters": chars,
            "character_start_times_seconds": [round(i * step, 3) for i in range(len(chars))],
            "character_end_times_seconds": [round((i + 1) * step, 3) for i in range(len(chars))]}


class FakeSurplus:
    """The Provider Market's ElevenLabs routes: `fail` maps a voice model to
    the error its call raises."""

    def __init__(self):
        self.sent = []
        self.fail = {}
        self.speech = None
        self.transcript = None

    def send(self, path, body=None, limit=0, timeout=120.0, fields=None, file=None):
        self.sent.append({"path": path, "body": body, "fields": fields, "file": file})
        if "/text-to-speech/" in path:
            if body["model_id"] in self.fail:
                raise self.fail[body["model_id"]]
            answer = self.speech or {"audio_base64": base64.b64encode(MP3).decode(),
                                     "alignment": alignment(body["text"])}
            return json.dumps(answer).encode(), 160
        if path.endswith("/speech-to-text"):
            if self.transcript is not None:
                return json.dumps({"words": self.transcript}).encode(), 55
            return json.dumps({"words": [
                {"text": "one", "start": 0.0, "end": 0.4, "type": "word"},
                {"text": " ", "start": 0.4, "end": 0.5, "type": "spacing"},
                {"text": "fart", "start": 0.5, "end": 0.9, "type": "word"},
                {"text": "(laughs)", "start": 1.0, "end": 1.4, "type": "audio_event"}]}).encode(), 55
        if path.endswith("/sound-generation") or path.endswith("/music"):
            return MP3, 1500
        raise AssertionError(f"unexpected send {path}")


class AudioTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        (root / "secrets").mkdir()
        (root / "secrets" / "teeny.env").write_text("DRY_RUN=0\n")
        self._env = dict(os.environ)
        os.environ["TEENY_ROOT"] = str(root)
        os.environ.pop("DRY_RUN", None)
        hand._ENV_CACHE = None
        self.api = FakeSurplus()

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)
        hand._ENV_CACHE = None
        self.tmp.cleanup()

    def env(self, **values):
        path = Path(os.environ["TEENY_ROOT"]) / "secrets" / "teeny.env"
        path.write_text(path.read_text() + "".join(f"{k}={v}\n" for k, v in values.items()))
        hand._ENV_CACHE = None

    def test_the_characters_times_become_words(self):
        words = audio.words_of(alignment("big  fart\nnow"))
        self.assertEqual([w["text"] for w in words], ["big", "fart", "now"])
        self.assertEqual((words[1]["start"], words[1]["end"]), (0.5, 0.9))

    def test_an_alignment_of_the_wrong_shape_is_no_words(self):
        broken = alignment("one two")
        broken["character_end_times_seconds"].pop()
        self.assertEqual(audio.words_of(broken), [])
        self.assertEqual(audio.words_of({"characters": ["a"], "character_start_times_seconds": ["x"],
                                         "character_end_times_seconds": [1]}), [])
        self.assertEqual(audio.words_of(None), [])

    def test_the_narration_comes_back_with_its_words_and_its_cost(self):
        got = audio.speak("i will now unveil the toilet", api=self.api)
        self.assertEqual(got["audio"], MP3)
        self.assertEqual([w["text"] for w in got["words"]], ["i", "will", "now", "unveil", "the", "toilet"])
        self.assertEqual(got["cost_micro"], 160)
        sent = self.api.sent[0]
        self.assertEqual(sent["path"], f"{audio.ROUTE}/text-to-speech/{audio.VOICE}/with-timestamps"
                                       "?output_format=mp3_44100_128")
        self.assertEqual(sent["body"], {"text": "i will now unveil the toilet", "model_id": "eleven_v3"})

    def test_a_voice_model_refused_for_itself_falls_to_the_next(self):
        self.api.fail["eleven_v3"] = SurplusError("Surplus answered 404: no sellers", 404)
        got = audio.speak("hello", api=self.api)
        self.assertEqual(got["model"], "eleven_multilingual_v2")
        self.assertEqual([s["body"]["model_id"] for s in self.api.sent], ["eleven_v3", "eleven_multilingual_v2"])

    def test_an_auth_or_money_refusal_is_final_and_tries_nothing_else(self):
        self.api.fail["eleven_v3"] = SurplusError("Surplus answered 402: insufficient_credit", 402)
        with self.assertRaises(hand.HandError):
            audio.speak("hello", api=self.api)
        self.assertEqual(len(self.api.sent), 1)

    def test_a_text_refusal_is_final_but_a_model_refusal_falls_through(self):
        self.api.fail["eleven_v3"] = SurplusError("Surplus answered 422: text violates policy", 422)
        with self.assertRaises(hand.HandError):
            audio.speak("hello", api=self.api)
        self.assertEqual(len(self.api.sent), 1)
        self.api.sent.clear()
        self.api.fail["eleven_v3"] = SurplusError("Surplus answered 400: model not available", 400)
        self.assertEqual(audio.speak("hello", api=self.api)["model"], "eleven_multilingual_v2")

    def test_a_billed_failure_keeps_its_cost_in_the_error(self):
        self.api.fail["eleven_v3"] = SurplusError("the answer is over 64 MB", cost_micro=777)
        with self.assertRaises(hand.HandError) as caught:
            audio.speak("hello", api=self.api)
        self.assertEqual(caught.exception.detail["cost_micro"], 777)

    def test_empty_narration_audio_is_an_error_that_says_what_it_cost(self):
        self.api.speech = {"audio_base64": "", "alignment": alignment("hi")}
        with self.assertRaises(hand.HandError) as caught:
            audio.speak("hi", api=self.api)
        self.assertEqual(caught.exception.detail["cost_micro"], 160)

    def test_invalid_times_are_no_words_so_the_normalized_alignment_is_used(self):
        for bad in (-1.0, float("nan"), float("inf")):
            broken = alignment("ab")
            broken["character_start_times_seconds"][0] = bad
            self.assertEqual(audio.words_of(broken), [])
        backwards = alignment("ab")
        backwards["character_end_times_seconds"][0] = -0.5
        self.assertEqual(audio.words_of(backwards), [])
        reversed_ = alignment("ab")
        reversed_["character_start_times_seconds"][1] = 5.0
        self.assertEqual(audio.words_of(reversed_), [])
        self.api.speech = {"audio_base64": base64.b64encode(MP3).decode(), "alignment": reversed_,
                           "normalized_alignment": alignment("ab")}
        self.assertEqual([w["text"] for w in audio.speak("ab", api=self.api)["words"]], ["ab"])

    def test_a_narration_over_the_models_limit_is_refused_before_it_is_bought(self):
        with self.assertRaises(hand.Refused):
            audio.speak("x" * 2501, api=self.api)
        self.assertEqual(self.api.sent, [])

    def test_the_voice_is_the_operators_pick_and_must_look_like_an_id(self):
        self.env(FILM_VOICE_ID="IKne3meq5aSn9XLyUdCD")
        audio.speak("hi", api=self.api)
        self.assertIn("/text-to-speech/IKne3meq5aSn9XLyUdCD/", self.api.sent[0]["path"])
        self.env(FILM_VOICE_ID="../../v1/voices")
        with self.assertRaises(hand.Refused):
            audio.speak("hi", api=self.api)

    def test_unreadable_speech_is_an_error_that_still_says_what_it_cost(self):
        self.api.speech = {"audio_base64": "not base64!!"}
        with self.assertRaises(hand.HandError) as caught:
            audio.speak("hi", api=self.api)
        self.assertEqual(caught.exception.detail["cost_micro"], 160)

    def test_a_dry_run_buys_nothing(self):
        self.env(DRY_RUN=1)
        self.assertTrue(audio.speak("hi", api=self.api)["dry_run"])
        self.assertTrue(audio.sound("a wet fart", 2, api=self.api)["dry_run"])
        self.assertTrue(audio.score("tuba march", 60, api=self.api)["dry_run"])
        self.assertEqual(self.api.sent, [])

    def test_a_transcript_keeps_only_the_words(self):
        got = audio.transcribe(MP3, api=self.api)
        self.assertEqual(got["words"], [{"text": "one", "start": 0.0, "end": 0.4},
                                        {"text": "fart", "start": 0.5, "end": 0.9}])
        sent = self.api.sent[0]
        self.assertEqual(sent["fields"], {"model_id": "scribe_v2", "timestamps_granularity": "word"})
        self.assertEqual(sent["file"], ("take.mp3", MP3, "audio/mpeg"))

    def test_a_transcript_drops_words_with_impossible_times(self):
        self.api.transcript = [
            {"text": "ok", "start": 0, "end": 0.3, "type": "word"},
            {"text": "back", "start": 2, "end": 1, "type": "word"},
            {"text": "neg", "start": -1, "end": 1, "type": "word"},
            {"text": "inf", "start": 0, "end": 1e999, "type": "word"}]
        self.assertEqual(audio.transcribe(MP3, api=self.api)["words"], [{"text": "ok", "start": 0.0, "end": 0.3}])

    def test_effects_and_score_are_bounded_and_the_score_is_instrumental(self):
        with self.assertRaises(hand.Refused):
            audio.sound("boom", 30, api=self.api)
        with self.assertRaises(hand.Refused):
            audio.score("march", 5, api=self.api)
        audio.score("a pompous tuba march", 75, api=self.api)
        self.assertEqual(self.api.sent[-1]["body"], {"prompt": "a pompous tuba march. Instrumental, no vocals.",
                                                     "music_length_ms": 75000, "force_instrumental": True})


class SendTest(unittest.TestCase):
    """Surplus.send against a local server: bytes back, the billed cost from
    its header, and a multipart upload that carries the fields and the file."""

    def setUp(self):
        seen = self.seen = {}

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("content-length") or 0)
                seen["body"] = self.rfile.read(length)
                seen["type"] = self.headers.get("content-type")
                seen["auth"] = self.headers.get("authorization")
                if self.path.endswith("/refuse"):
                    payload = json.dumps({"error": {"message": "no sellers for this model"}}).encode()
                    self.send_response(404)
                    self.send_header("content-length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
                    return
                self.send_response(200)
                self.send_header("x-si-buyer-cost-micro", "1500")
                self.send_header("content-length", str(len(MP3)))
                self.end_headers()
                self.wfile.write(MP3)

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.api = Surplus(key="k", base=f"http://127.0.0.1:{self.server.server_address[1]}")

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def test_bytes_come_back_with_the_billed_cost(self):
        data, cost = self.api.send("/v1/resources/elevenlabs/v1/sound-generation", {"text": "boom"})
        self.assertEqual((data, cost), (MP3, 1500))
        self.assertEqual(json.loads(self.seen["body"]), {"text": "boom"})
        self.assertEqual(self.seen["auth"], "Bearer k")

    def test_an_upload_is_multipart_with_its_fields_and_file(self):
        self.api.send("/stt", fields={"model_id": "scribe_v2"}, file=("take.mp3", MP3, "audio/mpeg"))
        self.assertTrue(self.seen["type"].startswith("multipart/form-data; boundary="))
        body = self.seen["body"]
        self.assertIn(b'name="model_id"\r\n\r\nscribe_v2\r\n', body)
        self.assertIn(b'filename="take.mp3"\r\ncontent-type: audio/mpeg\r\n\r\n' + MP3, body)

    def test_a_refusal_says_the_status_and_surplus_s_message(self):
        with self.assertRaises(SurplusError) as caught:
            self.api.send("/refuse", {"x": 1})
        self.assertEqual(caught.exception.status, 404)
        self.assertIn("no sellers", str(caught.exception))

    def test_an_answer_over_the_limit_is_refused_and_keeps_the_billed_cost(self):
        with self.assertRaises(SurplusError) as caught:
            self.api.send("/x", {"x": 1}, limit=10)
        self.assertEqual(caught.exception.cost_micro, 1500)


if __name__ == "__main__":
    unittest.main()
