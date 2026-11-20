"""hands/khaby dissect: one video fetched by the ingest service, sent whole to
a video-input model, and its answer held to the code's rules: frames from
ingest's frame rate, boundaries snapped to ingest's cuts, roles and what is
editable, the setup-then-reaction shape, the flags and the screen; the day's
cap, the cache and the spend. Nothing here uses the network."""

from __future__ import annotations

import base64
import json
import os
import shutil
import time
from unittest import mock

from .support import (FETCH_ID, M4A, MP4, TT_URL, FakeIngest, FakeModel, Scratch, gemini_reply, khaby_fetch, reading,
                      segment, surplus_reply)
from teeny_hands import hand
from teeny_hands.khaby import dissect, find
from teeny_hands.remake import ledger, study
from teeny_hands.remake.service import IngestError

FLAG_NAMES = ("minors", "news", "tragedy", "sexual", "violence", "identifiable_private")
MODEL = "some-video-model"


class Base(Scratch):
    def setUp(self):
        super().setUp()
        self.secrets(f"KHABY_DISSECT_MODEL={MODEL}\n")
        self.ingest = FakeIngest()
        self.ingest.probe_answer = {"source": "tiktok", "video_id": "7300", "url": TT_URL}
        self.ingest.fetch_answer = khaby_fetch()
        self.model = FakeModel(surplus_reply(reading()))

    def dissect(self, body=None):
        return dissect.dissect({"url": TT_URL} if body is None else body, ingest=self.ingest, api=self.model,
                               sleep=lambda s: None)

    def answer(self, answer, **fetch):
        self.reset()
        if fetch:
            self.ingest.fetch_answer = khaby_fetch(**fetch)
        self.model = FakeModel(surplus_reply(answer))
        return self.dissect()

    def rule(self, body=None):
        with self.assertRaises(hand.Refused) as caught:
            self.dissect(body)
        return caught.exception.detail["rule"]

    def reset(self):
        shutil.rmtree(self.root / "state", ignore_errors=True)

    def folder(self, dissect_id="tt-7300"):
        return self.root / "state" / "media" / "khaby" / dissect_id


class HappyPathTest(Base):
    def test_a_dissect_is_the_setup_the_reaction_and_his_beats(self):
        got = self.dissect()
        self.assertEqual(got["dissect_id"], "tt-7300")
        self.assertTrue(got["usable"], got["why"])
        self.assertEqual(got["why"], "")
        self.assertFalse(got["cached"])
        self.assertEqual(got["joke"], "the hack was never needed")
        self.assertEqual(got["flags"], dict.fromkeys(FLAG_NAMES, False))
        self.assertEqual(got["cost_usd"], 0.0412)
        self.assertEqual((got["source"]["url"], got["source"]["fps"], got["source"]["width"], got["source"]["height"]),
                         (TT_URL, 30.0, 720, 1280))
        self.assertEqual(got["excerpt"], {"start": 0.0, "seconds": 12.0, "frames": 360})
        setup, reaction = got["segments"]
        self.assertEqual(setup, {"n": 1, "role": "setup", "start": 0.0, "end": 4.0, "first_frame": 0, "last_frame": 119,
                                 "editable": False, "shots": [1], "why": setup["why"],
                                 "what": "a woman peels a banana with a drill"})
        self.assertEqual((reaction["n"], reaction["role"], reaction["start"], reaction["end"], reaction["first_frame"],
                          reaction["last_frame"], reaction["editable"], reaction["shots"]),
                         (2, "reaction", 4.0, 12.0, 120, 359, True, [2, 3]))
        self.assertTrue(setup["why"] and reaction["why"])
        self.assertEqual(got["beats"], [{"at": 4.5, "frame": 135, "what": "looks at the camera"},
                                        {"at": 7.0, "frame": 210, "what": "peels the banana by hand"},
                                        {"at": 10.2, "frame": 306, "what": "opens his hands"}])

    def test_the_files_are_pulled_into_state_media_khaby_and_the_fetch_is_deleted(self):
        got = self.dissect()
        self.assertEqual(sorted(p.name for p in self.folder().iterdir()),
                         ["audio.m4a", "shot-1.jpg", "shot-2.jpg", "shot-3.jpg", "source.mp4", "timeline.json"])
        self.assertEqual((self.folder() / "source.mp4").read_bytes(), MP4)
        self.assertEqual((self.folder() / "audio.m4a").read_bytes(), M4A)
        self.assertEqual(json.loads((self.folder() / "timeline.json").read_text())["dissect_id"], "tt-7300")
        self.assertEqual(got["video"], "state/media/khaby/tt-7300/source.mp4")
        self.assertEqual(got["audio"], "state/media/khaby/tt-7300/audio.m4a")
        self.assertEqual([s["frame"] for s in got["shots"]], [f"state/media/khaby/tt-7300/shot-{n}.jpg" for n in (1, 2, 3)])
        self.assertEqual(self.ingest.deleted, [FETCH_ID])
        self.assertFalse((self.root / "state" / "media" / "remake" / "tt-7300").exists())

    def test_the_whole_video_is_asked_for(self):
        self.dissect()
        (asked,) = self.ingest.asked("fetch")
        self.assertEqual(asked[1]["url"], TT_URL)
        self.assertEqual(asked[1]["max_seconds"], 60)

    def test_a_find_id_is_resolved_through_the_kept_finds(self):
        self.ingest.lists["https://www.tiktok.com/@khaby.lame"] = [
            {"url": TT_URL, "video_id": "7300", "title": "hack", "uploader": "khaby.lame", "seconds": 12.0, "views": 9}]
        find.find(5, ingest=self.ingest)
        got = self.dissect({"id": "chart:tt:7300"})
        self.assertEqual(got["dissect_id"], "tt-7300")
        self.assertEqual(self.ingest.asked("probe"), [])
        self.assertEqual(self.ingest.asked("fetch")[0][1]["url"], TT_URL)


class ProviderTest(Base):
    def test_surplus_gets_the_whole_video_as_a_video_url_part_and_the_prompt(self):
        self.dissect()
        ((method, path, body),) = self.model.calls
        self.assertEqual((method, path), ("POST", "/v1/chat/completions"))
        self.assertEqual(body["model"], MODEL)
        video, text = body["messages"][0]["content"]
        self.assertEqual(video["type"], "video_url")
        uri = video["video_url"]["url"]
        self.assertTrue(uri.startswith("data:video/mp4;base64,"))
        self.assertEqual(base64.b64decode(uri.split(",", 1)[1]), MP4)
        self.assertEqual(text["type"], "text")
        prompt = text["text"]
        self.assertIn("12 seconds", prompt)
        self.assertIn("4, 7.5", prompt)
        self.assertIn("deadpan", prompt)
        self.assertIn("never a name", prompt.lower())

    def test_gemini_gets_the_video_inline_by_generate_content(self):
        self.secrets("KHABY_DISSECT_PROVIDER=gemini\nKHABY_DISSECT_MODEL=gemini-video\n")
        self.model = FakeModel(gemini_reply(reading()))
        got = self.dissect()
        self.assertTrue(got["usable"], got["why"])
        ((method, path, body),) = self.model.calls
        self.assertEqual((method, path), ("POST", "/v1beta/models/gemini-video:generateContent"))
        video, text = body["contents"][0]["parts"]
        self.assertEqual(video["inline_data"]["mime_type"], "video/mp4")
        self.assertEqual(base64.b64decode(video["inline_data"]["data"]), MP4)
        self.assertIn("deadpan", text["text"])
        self.assertEqual(body["generationConfig"]["temperature"], 0)
        self.assertIsNone(got["cost_usd"])
        conn = hand.db()
        try:
            row = conn.execute("SELECT tokens_in, tokens_out, cost_micro FROM remake_studies").fetchone()
            self.assertEqual(tuple(row), (9000, 500, None))
        finally:
            conn.close()

    def test_the_gemini_client_sends_its_key_in_a_header_never_in_the_address(self):
        import io
        import urllib.error

        client = dissect.Gemini(key="sekrit-key")
        seen = []

        class Answer(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def opened(request, timeout):
            seen.append(request)
            return Answer(json.dumps(gemini_reply("{}")).encode())

        with mock.patch.object(dissect._OPENER, "open", opened):
            client.call("POST", "/v1beta/models/m:generateContent", {"contents": []})
        (request,) = seen
        self.assertEqual(request.full_url, "https://generativelanguage.googleapis.com/v1beta/models/m:generateContent")
        self.assertEqual(request.get_header("X-goog-api-key"), "sekrit-key")

        def refused(request, timeout):
            raise urllib.error.HTTPError(request.full_url, 403, "no", {}, io.BytesIO(b'{"error": {"message": "bad key"}}'))

        with mock.patch.object(dissect._OPENER, "open", refused):
            with self.assertRaises(dissect.GeminiError) as caught:
                client.call("POST", "/v1beta/models/m:generateContent", {"contents": []})
        self.assertEqual(caught.exception.status, 403)
        self.assertNotIn("sekrit", str(caught.exception))
        self.assertNotIn("googleapis", str(caught.exception))

    def test_a_model_that_cannot_be_reached_is_unusable_not_a_crash(self):
        from teeny_hands.media.surplus import SurplusError

        self.model = FakeModel(SurplusError("Surplus answered 401", 401))
        got = self.dissect()
        self.assertFalse(got["usable"])
        self.assertIn("could not be read", got["why"])


class ModelSettingTest(Base):
    def test_with_no_model_set_dissect_is_refused_before_anything_is_asked(self):
        self.secrets("")
        self.assertEqual(self.rule(), "model")
        with self.assertRaises(hand.Refused) as caught:
            self.dissect()
        self.assertIn("KHABY_DISSECT_MODEL", caught.exception.reason)
        self.assertEqual(self.ingest.calls, [])
        self.assertEqual(self.model.calls, [])

    def test_a_provider_that_is_not_one_is_refused(self):
        self.secrets(f"KHABY_DISSECT_MODEL={MODEL}\nKHABY_DISSECT_PROVIDER=openai\n")
        self.assertEqual(self.rule(), "model")
        self.assertEqual(self.ingest.calls, [])

    def test_a_model_name_that_is_not_one_is_refused(self):
        self.secrets("KHABY_DISSECT_MODEL=../../v1/x?y\n")
        self.assertEqual(self.rule(), "model")

    def test_a_provider_with_no_key_is_refused_before_the_ingest_service_is_asked(self):
        for provider, key in (("gemini", "GEMINI_API_KEY"), ("surplus", "SURPLUS_API_KEY")):
            with self.subTest(provider=provider):
                self.secrets(f"KHABY_DISSECT_MODEL={MODEL}\nKHABY_DISSECT_PROVIDER={provider}\n")
                with self.assertRaises(hand.Refused) as caught:
                    dissect.dissect({"url": TT_URL}, ingest=self.ingest)
                self.assertEqual(caught.exception.detail["rule"], "model")
                self.assertIn(key, caught.exception.reason)
        self.assertEqual(self.ingest.calls, [])


class SnappingTest(Base):
    def test_each_boundary_is_snapped_to_the_nearest_cut_within_a_second(self):
        got = self.answer(reading([segment(0.4, 4.9, "setup"), segment(4.9, 7.0, "reaction"), segment(7.0, 11.2, "reaction")]))
        self.assertTrue(got["usable"], got["why"])
        self.assertEqual([(s["start"], s["end"]) for s in got["segments"]], [(0.0, 4.0), (4.0, 7.5), (7.5, 12.0)])
        self.assertEqual([s["shots"] for s in got["segments"]], [[1], [2], [3]])

    def test_a_boundary_more_than_a_second_from_any_cut_makes_it_unusable(self):
        got = self.answer(reading([segment(0, 5.2, "setup"), segment(5.2, 12.0, "reaction")]))
        self.assertFalse(got["usable"])
        self.assertIn("segment 1 ends at 5.2 s, 1.2 s from the nearest cut", got["why"])
        self.assertEqual(sorted(p.name for p in self.folder().iterdir()), ["timeline.json"])
        self.assertIsNone(got["video"])

    def test_two_boundaries_on_one_cut_leave_an_empty_segment(self):
        got = self.answer(reading([segment(0, 4.0, "setup"), segment(4.0, 4.3, "other"), segment(4.3, 12.0, "reaction")]))
        self.assertFalse(got["usable"])
        self.assertIn("segment 2 is empty once snapped to the cuts", got["why"])

    def test_segments_with_a_gap_or_an_overlap_or_short_of_the_end_do_not_run_end_to_end(self):
        for segments in ([segment(0, 4.0, "setup"), segment(7.5, 12.0, "reaction")],
                         [segment(0, 7.5, "setup"), segment(4.0, 12.0, "reaction")],
                         [segment(0, 4.0, "setup"), segment(4.0, 7.5, "reaction")],
                         [segment(4.0, 7.5, "setup"), segment(7.5, 12.0, "reaction")]):
            with self.subTest(segments=segments):
                got = self.answer(reading(segments))
                self.assertFalse(got["usable"])
                self.assertIn("the segments do not run end to end", got["why"])


class FrameTest(Base):
    def test_frames_come_from_the_seconds_at_ingests_frame_rate(self):
        got = self.answer(reading([segment(0, 2.5, "setup"), segment(2.5, 6.507, "reaction")],
                                  beats=[{"at": 3.0, "what": "looks"}, {"at": 6.5, "what": "shrugs"}]),
                          lengths=(2.503, 4.004), fps=29.97)
        self.assertTrue(got["usable"], got["why"])
        setup, reaction = got["segments"]
        self.assertEqual((setup["start"], setup["end"], setup["first_frame"], setup["last_frame"]), (0.0, 2.503, 0, 74))
        self.assertEqual((reaction["start"], reaction["end"], reaction["first_frame"], reaction["last_frame"]),
                         (2.503, 6.507, 75, 194))
        self.assertEqual([b["frame"] for b in got["beats"]], [89, 194])
        self.assertEqual(got["excerpt"]["frames"], 195)

    def test_frames_the_model_offers_are_never_taken(self):
        answer = reading()
        answer["segments"][0].update(first_frame=999, last_frame=1000)
        answer["beats"][0]["frame"] = 5000
        got = self.answer(answer)
        self.assertEqual((got["segments"][0]["first_frame"], got["segments"][0]["last_frame"]), (0, 119))
        self.assertEqual(got["beats"][0]["frame"], 135)

    def test_a_beat_outside_the_video_makes_it_unusable(self):
        got = self.answer(reading(beats=[{"at": 5.0, "what": "looks"}, {"at": 13.0, "what": "leaves"}]))
        self.assertFalse(got["usable"])
        self.assertIn("beat 2 at 13 s is outside the video", got["why"])

    def test_beats_are_put_in_order(self):
        got = self.answer(reading(beats=[{"at": 9.0, "what": "b"}, {"at": 5.0, "what": "a"}]))
        self.assertEqual([b["what"] for b in got["beats"]], ["a", "b"])


class RolesTest(Base):
    def test_setup_is_kept_and_reaction_is_editable_and_other_is_kept(self):
        got = self.answer(reading([segment(0, 4.0, "setup"), segment(4.0, 7.5, "reaction"), segment(7.5, 12.0, "other")]))
        self.assertTrue(got["usable"], got["why"])
        self.assertEqual([(s["role"], s["editable"]) for s in got["segments"]],
                         [("setup", False), ("reaction", True), ("other", False)])

    def test_unusable_without_a_long_enough_setup_followed_by_a_reaction(self):
        cases = {"no setup": [segment(0, 12.0, "reaction")],
                 "no reaction": [segment(0, 4.0, "setup"), segment(4.0, 12.0, "other")],
                 "reaction first": [segment(0, 7.5, "reaction"), segment(7.5, 12.0, "setup")],
                 "setup too short": [segment(0, 1.5, "setup"), segment(1.5, 12.0, "reaction")]}
        for name, segments in cases.items():
            with self.subTest(name):
                fetch = {"lengths": (1.5, 10.5)} if name == "setup too short" else {}
                got = self.answer(reading(segments), **fetch)
                self.assertFalse(got["usable"])
                self.assertIn("no setup of 2 s or more followed by a reaction", got["why"])

    def test_an_other_segment_between_setup_and_reaction_is_fine(self):
        got = self.answer(reading([segment(0, 4.0, "setup"), segment(4.0, 7.5, "other"), segment(7.5, 12.0, "reaction")]))
        self.assertTrue(got["usable"], got["why"])


class ReadingTest(Base):
    def test_every_true_flag_makes_it_unusable_and_the_why_names_it(self):
        for flag in FLAG_NAMES:
            with self.subTest(flag=flag):
                got = self.answer(reading(**{flag: True}))
                self.assertFalse(got["usable"])
                self.assertTrue(got["flags"][flag])
                self.assertIn(f"flagged: {flag.replace('_', ' ')}", got["why"])
                self.assertEqual(sorted(p.name for p in self.folder().iterdir()), ["timeline.json"])
                self.assertIsNone(got["audio"])
                self.assertEqual([s["frame"] for s in got["shots"]], [None, None, None])

    def test_an_answer_not_in_the_shape_asked_fails_closed_and_keeps_nothing(self):
        broken = ["I cannot watch videos.", "", "[1]", "null",
                  {**reading(), "segments": []},
                  {**reading(), "segments": [segment(0, 4, "setup"), segment(4, 12, "khaby")]},
                  {**reading(), "segments": [segment(0, "4", "setup"), segment(4, 12, "reaction")]},
                  {**reading(), "segments": [segment(4, 0, "setup"), segment(4, 12, "reaction")]},
                  {**reading(), "segments": [segment(-1, 4, "setup"), segment(4, 12, "reaction")]},
                  {**reading(), "beats": "none"},
                  {**reading(), "beats": [{"at": "soon", "what": "x"}]},
                  {**reading(), "flags": {k: False for k in FLAG_NAMES if k != "news"}},
                  {**reading(), "flags": {**dict.fromkeys(FLAG_NAMES, False), "sexual": 0}},
                  {k: v for k, v in reading().items() if k != "flags"}]
        for answer in broken:
            with self.subTest(answer=answer):
                got = self.answer(answer)
                self.assertFalse(got["usable"])
                self.assertIn("could not be read", got["why"])
                self.assertFalse(self.folder().exists())

    def test_a_fenced_answer_is_read(self):
        got = self.answer("```json\n" + json.dumps(reading()) + "\n```")
        self.assertTrue(got["usable"], got["why"])

    def test_the_models_words_are_capped_and_stripped_of_what_is_not_text(self):
        answer = reading()
        answer["joke"] = "j" * 900 + "\x00"
        answer["segments"][0]["what"] = "a\x00 drill‮\nand\ta banana\x1b"
        answer["beats"][0]["what"] = "w" * 900
        got = self.answer(answer)
        self.assertLessEqual(len(got["joke"]), dissect.CAPS["joke"])
        self.assertEqual(got["segments"][0]["what"], "a drill and a banana")
        self.assertLessEqual(len(got["beats"][0]["what"]), dissect.CAPS["beat"])

    def test_too_many_segments_or_beats_fail_closed(self):
        for answer in ({**reading(), "segments": [segment(i, i + 1, "setup") for i in range(10)]},
                       {**reading(), "beats": [{"at": 5.0, "what": "x"}] * 13}):
            got = self.answer(answer)
            self.assertIn("could not be read", got["why"])


class ScreenTest(Base):
    def test_the_models_words_the_screen_stops_are_blanked_and_named(self):
        for where, put in [("joke", lambda a: a.update(joke="heil hitler")),
                           ("segment 2 what", lambda a: a["segments"][1].update(what="heil hitler")),
                           ("beat 1 what", lambda a: a["beats"][0].update(what="heil hitler"))]:
            with self.subTest(where=where):
                answer = reading()
                put(answer)
                got = self.answer(answer)
                self.assertFalse(got["usable"])
                self.assertEqual(got["why"], f"screened: {where}")
                self.assertNotIn("heil", json.dumps(got))

    def test_a_title_the_screen_stops_is_unusable_before_the_model_is_asked(self):
        fetch = khaby_fetch()
        fetch["source"]["title"] = "man stabbed in the park"
        self.ingest.fetch_answer = fetch
        got = self.dissect()
        self.assertEqual((got["usable"], got["why"]), (False, "screened: title"))
        self.assertEqual(self.model.calls, [])
        self.assertEqual(self.ingest.deleted, [FETCH_ID])
        self.assertEqual(sorted(p.name for p in self.folder().iterdir()), ["timeline.json"])


class SpendTest(Base):
    def test_the_spend_is_a_dissect_row_of_the_remake_ledger(self):
        self.dissect()
        conn = hand.db()
        try:
            row = conn.execute("SELECT * FROM remake_studies").fetchone()
            self.assertEqual((row["study_id"], row["kind"], row["model"], row["stage"], row["ok"], row["usable"], row["asks"],
                              row["tokens_in"], row["tokens_out"], row["cost_micro"]),
                             ("tt-7300", "dissect", MODEL, "done", 1, 1, 1, 12000, 600, 41200))
        finally:
            conn.close()

    def test_the_day_is_capped_at_three_by_default(self):
        for n in (1, 2, 3):
            self.ingest.probe_answer = {"source": "tiktok", "video_id": f"7{n}", "url": TT_URL}
            self.ingest.fetch_answer = khaby_fetch(video_id=f"7{n}")
            self.dissect()
        self.ingest.probe_answer = {"source": "tiktok", "video_id": "74", "url": TT_URL}
        asked = len(self.model.calls)
        self.assertEqual(self.rule(), "per_day")
        self.assertEqual(len(self.model.calls), asked)
        self.assertEqual(len(self.ingest.asked("fetch")), 3)

    def test_zero_is_off(self):
        self.secrets(f"KHABY_DISSECT_MODEL={MODEL}\nKHABY_DISSECT_MAX_PER_DAY=0\n")
        self.assertEqual(self.rule(), "off")
        self.assertEqual(self.ingest.calls, [])

    def test_the_dissect_cap_and_the_study_cap_are_counted_apart(self):
        self.secrets(f"KHABY_DISSECT_MODEL={MODEL}\nKHABY_DISSECT_MAX_PER_DAY=1\n")
        for n in range(3):
            self.assertIsNotNone(ledger.claim(f"tt-9{n}", "glm", 10))
        self.assertTrue(self.dissect()["usable"])
        self.assertEqual(ledger.used_today(), 3)
        self.ingest.probe_answer = {"source": "tiktok", "video_id": "7301", "url": TT_URL}
        self.assertEqual(self.rule(), "per_day")

    def test_a_dissect_that_never_reached_the_model_gives_its_claim_back(self):
        self.secrets(f"KHABY_DISSECT_MODEL={MODEL}\nKHABY_DISSECT_MAX_PER_DAY=1\n")
        self.ingest.fetch_answer = IngestError("the ingest service answered 422: Sign in to confirm", 422)
        self.assertEqual(self.rule(), "fetch")
        self.ingest.fetch_answer = IngestError("could not reach the ingest service (URLError)")
        self.assertEqual(self.rule(), "ingest")
        self.ingest.fetch_answer = khaby_fetch()
        self.assertTrue(self.dissect()["usable"])

    def test_an_unreadable_answer_still_counts_because_the_model_was_paid(self):
        self.secrets(f"KHABY_DISSECT_MODEL={MODEL}\nKHABY_DISSECT_MAX_PER_DAY=1\n")
        self.model = FakeModel(surplus_reply("nonsense"))
        self.assertFalse(self.dissect()["usable"])
        self.assertEqual(len(self.model.calls), 3)
        self.assertEqual(self.rule(), "per_day")

    def test_a_video_over_the_size_sent_is_refused_and_gives_its_claim_back(self):
        self.secrets(f"KHABY_DISSECT_MODEL={MODEL}\nKHABY_DISSECT_MAX_PER_DAY=1\n")
        with mock.patch.object(dissect, "MAX_VIDEO_BYTES", len(MP4) - 1):
            self.assertEqual(self.rule(), "fetch")
        self.assertEqual(self.model.calls, [])
        self.assertEqual(self.ingest.deleted, [FETCH_ID])
        self.assertTrue(self.dissect()["usable"])

    def test_shot_times_that_do_not_add_up_to_the_excerpt_are_refused(self):
        fetch = khaby_fetch()
        fetch["excerpt"]["seconds"] = 20.0
        self.ingest.fetch_answer = fetch
        self.assertEqual(self.rule(), "fetch")
        self.assertEqual(self.model.calls, [])


class CacheTest(Base):
    def test_a_second_ask_is_the_kept_timeline_and_spends_nothing(self):
        first = self.dissect()
        calls = (len(self.model.calls), len(self.ingest.asked("fetch")))
        again = self.dissect()
        self.assertTrue(again["cached"])
        self.assertEqual(again["cost_usd"], 0.0)
        drop = ("cached", "cost_usd")
        self.assertEqual({k: v for k, v in again.items() if k not in drop}, {k: v for k, v in first.items() if k not in drop})
        self.assertEqual((len(self.model.calls), len(self.ingest.asked("fetch"))), calls)

    def test_a_kept_timeline_reads_when_the_day_is_used_up(self):
        self.dissect()
        self.secrets(f"KHABY_DISSECT_MODEL={MODEL}\nKHABY_DISSECT_MAX_PER_DAY=1\n")
        self.assertTrue(self.dissect()["cached"])

    def test_an_unusable_but_readable_timeline_is_kept(self):
        self.model = FakeModel(surplus_reply(reading(news=True)))
        self.dissect()
        again = self.dissect()
        self.assertTrue(again["cached"])
        self.assertFalse(again["usable"])

    def test_an_unreadable_answer_is_not_kept_so_a_retry_asks_again(self):
        self.model = FakeModel(*[surplus_reply("nonsense")] * 3, surplus_reply(reading()))
        self.assertFalse(self.dissect()["usable"])
        self.assertTrue(self.dissect()["usable"])


class SweepTest(Base):
    def test_folders_older_than_the_keep_window_go_and_the_remake_ones_are_not_touched(self):
        media = self.root / "state" / "media"
        for where, hours in (("khaby/old", 60), ("khaby/fresh", 5), ("khaby/.staging-x", 60), ("remake/old", 60)):
            (media / where).mkdir(parents=True)
            at = time.time() - hours * 3600
            os.utime(media / where, (at, at))
        self.dissect()
        self.assertEqual(sorted(p.name for p in (media / "khaby").iterdir()), ["fresh", "tt-7300"])
        self.assertTrue((media / "remake" / "old").exists())
        self.secrets(f"KHABY_DISSECT_MODEL={MODEL}\nKHABY_KEEP_HOURS=2\n")
        self.ingest.probe_answer = {"source": "tiktok", "video_id": "7301", "url": TT_URL}
        self.ingest.fetch_answer = khaby_fetch(video_id="7301")
        self.dissect()
        self.assertEqual(sorted(p.name for p in (media / "khaby").iterdir()), ["tt-7300", "tt-7301"])


class SourceTest(Base):
    def test_a_video_already_used_is_refused(self):
        ledger.mark_used("7300", "tiktok", TT_URL)
        self.assertEqual(self.rule(), "source")
        self.assertEqual(self.ingest.asked("fetch"), [])

    def test_an_id_that_is_not_a_find_id_or_is_unknown_is_refused(self):
        for body in ({"id": "chart:tt:999"}, {"id": "whatever"}, {"id": "chart:tt:../x"}):
            with self.subTest(body=body):
                self.assertEqual(self.rule(body), "source")

    def test_a_url_off_the_list_is_refused_before_anything_is_asked(self):
        for url in ("http://www.tiktok.com/@a/video/1", "https://evil.example/x"):
            self.assertEqual(self.rule({"url": url}), "source")
        self.assertEqual(self.ingest.calls, [])

    def test_a_body_that_is_not_one_id_or_one_url_is_an_error(self):
        for body in (None, [], {}, {"id": "chart:tt:1", "url": TT_URL}, {"url": 5}):
            with self.subTest(body=body):
                with self.assertRaises(hand.HandError):
                    dissect.dissect(body, ingest=self.ingest, api=self.model)

    def test_the_fetch_is_held_to_the_video_asked_for(self):
        self.ingest.fetch_answer = khaby_fetch(video_id="9999")
        self.assertEqual(self.rule(), "fetch")
        self.assertEqual(self.ingest.deleted, [FETCH_ID])

    def test_a_study_and_a_dissect_of_one_video_keep_to_their_own_folders(self):
        self.dissect()
        self.assertEqual(study.ROOT, "remake")
        self.assertFalse((self.root / "state" / "media" / "remake" / "tt-7300").exists())
