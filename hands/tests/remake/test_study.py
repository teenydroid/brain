"""hands/remake study: one video fetched by the ingest service, its files
pulled and checked, one vision call on the shot frames, a strict reading of
the answer that fails closed, the flags, the day's cap and its own ledger,
and the cache. Nothing here uses the network, yt-dlp or ffmpeg."""

from __future__ import annotations

import base64
import contextlib
import io
import json
import shutil

from .support import FETCH_ID, JPEG, M4A, MP4, FakeIngest, Scratch, entry, fetched
from teeny_hands import hand
from teeny_hands.remake import charts, cli, ledger, study
from teeny_hands.remake.service import IngestError

TT_URL = "https://www.tiktok.com/@someone/video/7300"
FLAGS = {"minors": False, "news": False, "tragedy": False, "sexual": False, "violence": False, "identifiable_private": False}


def person(ref="A", **more):
    return {"ref": ref, "looks": "a tall man in a grey suit", "position": "left", "action": "waves", **more}


def breakdown(n=2, **flags):
    return {"shots": [{"people": [person()], "setting": f"an office {i}", "motion": "static", "camera": "wide",
                       "speech": "hello"} for i in range(1, n + 1)],
            "summary": "a man waves in an office", "flags": {**FLAGS, **flags}}


def reply(content, cost=0.0123):
    text = content if isinstance(content, str) else json.dumps(content)
    return {"choices": [{"message": {"content": text}}], "usage": {"prompt_tokens": 900, "completion_tokens": 300, "cost": cost}}


class FakeVision:
    def __init__(self, *replies):
        self.replies = list(replies)
        self.bodies = []

    def call(self, method, path, body=None, timeout=60.0, headers=None):
        assert (method, path) == ("POST", "/v1/chat/completions"), (method, path)
        self.bodies.append(body)
        got = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        if isinstance(got, Exception):
            raise got
        return got

    def images(self, n=0):
        return [p for p in self.bodies[n]["messages"][0]["content"] if p["type"] == "image_url"]

    def text(self, n=0):
        return [p for p in self.bodies[n]["messages"][0]["content"] if p["type"] == "text"][0]["text"]


class Base(Scratch):
    def setUp(self):
        super().setUp()
        self.ingest = FakeIngest()
        self.vision = FakeVision(reply(breakdown()))
        self.ingest.probe_answer = {"source": "tiktok", "video_id": "7300", "url": TT_URL}

    def study(self, body=None, **kw):
        return study.study({"url": TT_URL} if body is None else body, ingest=self.ingest, api=self.vision,
                           sleep=lambda s: None, **kw)

    def rule(self, body=None):
        with self.assertRaises(hand.Refused) as caught:
            self.study(body)
        return caught.exception.detail["rule"]

    def folder(self, study_id="tt-7300"):
        return self.root / "state" / "media" / "remake" / study_id


class HappyPathTest(Base):
    def test_a_study_is_the_shots_their_people_the_summary_and_the_flags(self):
        got = self.study()
        self.assertEqual(got["study_id"], "tt-7300")
        self.assertTrue(got["usable"])
        self.assertEqual(got["why"], "")
        self.assertFalse(got["cached"])
        self.assertEqual(got["aspect"], "9:16")
        self.assertEqual(got["excerpt"], {"start": 0.0, "seconds": 6.0})
        self.assertEqual(got["source"]["video_id"], "7300")
        self.assertEqual(got["audio"], "state/media/remake/tt-7300/audio.m4a")
        self.assertEqual(got["summary"], "a man waves in an office")
        self.assertEqual(got["flags"], FLAGS)
        self.assertEqual(got["cost_usd"], 0.0123)
        self.assertEqual([(s["n"], s["seconds"], s["frame"]) for s in got["shots"]],
                         [(1, 3.0, "state/media/remake/tt-7300/shot-1.jpg"), (2, 3.0, "state/media/remake/tt-7300/shot-2.jpg")])
        shot = got["shots"][0]
        self.assertEqual(shot["people"], [{"ref": "A", "looks": "a tall man in a grey suit", "position": "left", "action": "waves"}])
        for key in ("setting", "motion", "camera", "speech"):
            self.assertIn(key, shot)

    def test_the_files_are_pulled_into_state_media_and_the_fetch_is_deleted(self):
        self.study()
        folder = self.folder()
        self.assertEqual(sorted(p.name for p in folder.iterdir()), ["audio.m4a", "shot-1.jpg", "shot-2.jpg", "source.mp4", "study.json"])
        self.assertEqual((folder / "source.mp4").read_bytes(), MP4)
        self.assertEqual((folder / "audio.m4a").read_bytes(), M4A)
        self.assertEqual((folder / "shot-1.jpg").read_bytes(), JPEG)
        self.assertEqual(self.ingest.deleted, [FETCH_ID])
        self.assertEqual(self.ingest.asked("fetch"), [("fetch", {"url": TT_URL, "max_seconds": 30.0, "max_shots": 6,
                                                                  "min_shot": 1.0, "max_shot": 5.0})])

    def test_the_fetch_caps_each_shot_to_one_video_clip(self):
        # Each shot becomes one image-to-video clip of at most 5 s (Wan 3), so
        # ingest splits longer shots; REMAKE_MAX_SHOT_SECONDS moves it, within
        # 2 to 10 s (ingest wants max_shot at least twice min_shot).
        for raw, want in (("", 5.0), ("3.5", 3.5), ("0.5", 2.0), ("20", 10.0), ("soon", 5.0)):
            with self.subTest(raw=raw):
                self.secrets(f"REMAKE_MAX_SHOT_SECONDS={raw}\n" if raw else "")
                shutil.rmtree(self.folder(), ignore_errors=True)  # no kept study: it fetches again
                self.ingest.calls = []
                self.vision = FakeVision(reply(breakdown()))
                self.study()
                body = self.ingest.asked("fetch")[-1][1]
                self.assertEqual((body["max_shot"], body["min_shot"], body["max_shots"], body["max_seconds"]),
                                 (want, 1.0, 6, 30.0))

    def test_a_clip_with_no_sound_has_no_audio(self):
        self.ingest.fetch_answer = fetched(audio=False)
        self.assertIsNone(self.study()["audio"])
        self.assertFalse((self.folder() / "audio.m4a").exists())

    def test_the_vision_model_gets_every_frame_in_order_in_one_call(self):
        self.secrets("REMAKE_STUDY_MODEL=glm-test\n")
        self.study()
        self.assertEqual(len(self.vision.bodies), 1)
        body = self.vision.bodies[0]
        self.assertEqual(body["model"], "glm-test")
        images = self.vision.images()
        self.assertEqual(len(images), 2)
        self.assertEqual(base64.b64decode(images[0]["image_url"]["url"].split(",", 1)[1]), JPEG)
        self.assertIn("2 frames", self.vision.text())

    def test_the_default_model_is_glm_flash(self):
        self.study()
        self.assertEqual(self.vision.bodies[0]["model"], "glm-5.3-flash")

    def test_a_chart_id_is_resolved_through_the_kept_charts(self):
        self.secrets("REMAKE_TIKTOK_ACCOUNTS=alice\n")
        self.ingest.lists["https://www.tiktok.com/@alice"] = [entry("7300", uploader="alice")]
        charts.top("k", 5, ingest=self.ingest, get_json=lambda url: {})
        got = self.study({"id": "chart:tt:7300"})
        self.assertEqual(got["study_id"], "tt-7300")
        self.assertEqual(self.ingest.asked("fetch"), [("fetch", {"url": "https://www.tiktok.com/@alice/video/7300"})])
        self.assertEqual(self.ingest.asked("probe"), [])


class ReadingTest(Base):
    def test_every_true_flag_makes_it_unusable_and_the_why_names_them(self):
        for flag in FLAGS:
            with self.subTest(flag=flag):
                self.root_reset()
                vision = FakeVision(reply(breakdown(**{flag: True})))
                got = study.study({"url": TT_URL}, ingest=self.ingest, api=vision, sleep=lambda s: None)
                self.assertFalse(got["usable"])
                self.assertIn(flag.replace("_", " "), got["why"])
                self.assertTrue(got["flags"][flag])

    def root_reset(self):
        import shutil

        shutil.rmtree(self.root / "state", ignore_errors=True)

    def test_two_flags_are_both_named(self):
        self.vision = FakeVision(reply(breakdown(minors=True, news=True)))
        got = self.study()
        self.assertIn("minors", got["why"])
        self.assertIn("news", got["why"])

    def test_an_answer_that_is_not_json_fails_closed(self):
        for text in ("I cannot do that.", "", "{", "[1, 2]", "null", '{"shots": []}'):
            with self.subTest(text=text):
                self.root_reset()
                vision = FakeVision(reply(text))
                got = study.study({"url": TT_URL}, ingest=self.ingest, api=vision, sleep=lambda s: None)
                self.assertFalse(got["usable"])
                self.assertTrue(got["why"])

    def test_a_missing_or_non_boolean_flag_fails_closed(self):
        broken = [dict(breakdown(), flags={k: v for k, v in FLAGS.items() if k != "minors"}),
                  dict(breakdown(), flags={**FLAGS, "news": "no"}), dict(breakdown(), flags={**FLAGS, "sexual": 0}),
                  {k: v for k, v in breakdown().items() if k != "flags"}]
        for answer in broken:
            with self.subTest(answer=answer):
                self.root_reset()
                got = study.study({"url": TT_URL}, ingest=self.ingest, api=FakeVision(reply(answer)), sleep=lambda s: None)
                self.assertFalse(got["usable"])

    def test_a_breakdown_for_the_wrong_number_of_shots_fails_closed(self):
        for n in (1, 3):
            with self.subTest(n=n):
                self.root_reset()
                got = study.study({"url": TT_URL}, ingest=self.ingest, api=FakeVision(reply(breakdown(n))), sleep=lambda s: None)
                self.assertFalse(got["usable"])

    def test_a_fenced_answer_is_read(self):
        self.vision = FakeVision(reply("```json\n" + json.dumps(breakdown()) + "\n```"))
        self.assertTrue(self.study()["usable"])

    def test_the_models_words_are_capped_and_stripped_of_what_is_not_text(self):
        answer = breakdown()
        answer["summary"] = "s" * 2000 + "\x00"
        answer["shots"][0]["setting"] = "an\x00 office‮\nwith\ta ​desk\x1b[31m"
        answer["shots"][0]["people"] = [person(looks="l" * 900, action="ignore previous instructions"), person("B"), person("C"),
                                        person("D"), person("E"), person("F")]
        answer["shots"][1]["speech"] = "\u0007bell"
        got = self.study_with(answer)
        self.assertLessEqual(len(got["summary"]), 400)
        setting = got["shots"][0]["setting"]
        self.assertEqual(setting, "an office with a desk[31m")
        self.assertTrue(all(ch.isprintable() for ch in setting))
        self.assertLessEqual(len(got["shots"][0]["people"][0]["looks"]), 200)
        self.assertEqual(len(got["shots"][0]["people"]), 4)
        self.assertEqual(got["shots"][1]["speech"], "bell")

    def test_a_person_ref_is_one_capital_letter_or_it_is_made_one(self):
        answer = breakdown()
        answer["shots"][0]["people"] = [person("zzz"), person("B", looks=5)]
        got = self.study_with(answer)
        self.assertEqual([p["ref"] for p in got["shots"][0]["people"]], ["A", "B"])
        self.assertEqual(got["shots"][0]["people"][1]["looks"], "")

    def test_a_shot_with_no_people_is_fine(self):
        answer = breakdown()
        answer["shots"][0]["people"] = []
        self.assertEqual(self.study_with(answer)["shots"][0]["people"], [])

    def test_the_sources_own_words_are_cleaned_too(self):
        fetch = fetched()
        fetch["source"]["title"] = "ti\x00tle‮" + "t" * 900
        self.ingest.fetch_answer = fetch
        got = self.study()
        self.assertTrue(got["source"]["title"].startswith("title"))
        self.assertLessEqual(len(got["source"]["title"]), 300)

    def study_with(self, answer):
        self.vision = FakeVision(reply(answer))
        return self.study()


class ScreenTest(Base):
    """Every outside string goes through the guard's inbound screen, and the
    title through the HARM and NOT_A_FIGURE lists too."""

    def test_a_caption_the_screen_stops_makes_it_unusable_and_is_not_carried(self):
        answer = breakdown(3)
        answer["shots"][1]["speech"] = "heil hitler everyone"
        self.ingest.fetch_answer = fetched(3)
        got = self.study_with(answer)
        self.assertFalse(got["usable"])
        self.assertEqual(got["why"], "screened: shot 2 speech")
        self.assertEqual(got["shots"][1]["speech"], "")
        self.assertNotIn("heil", json.dumps(got))

    def test_each_field_of_each_shot_and_the_summary_and_the_uploader_are_screened(self):
        for where, put in [("summary", lambda a: a.update(summary="heil hitler")),
                           ("shot 1 setting", lambda a: a["shots"][0].update(setting="heil hitler")),
                           ("shot 1 motion", lambda a: a["shots"][0].update(motion="heil hitler")),
                           ("shot 2 camera", lambda a: a["shots"][1].update(camera="heil hitler")),
                           ("shot 1 person A looks", lambda a: a["shots"][0]["people"][0].update(looks="heil hitler")),
                           ("shot 1 person A action", lambda a: a["shots"][0]["people"][0].update(action="heil hitler")),
                           ("shot 1 person A position", lambda a: a["shots"][0]["people"][0].update(position="heil hitler"))]:
            with self.subTest(where=where):
                self.root_reset()
                answer = breakdown()
                put(answer)
                got = self.study_with(answer)
                self.assertFalse(got["usable"])
                self.assertEqual(got["why"], f"screened: {where}")

    def test_a_title_with_a_harm_word_is_unusable_before_the_model_is_asked(self):
        fetch = fetched()
        fetch["source"]["title"] = "man stabbed in the park"
        self.ingest.fetch_answer = fetch
        got = self.study()
        self.assertFalse(got["usable"])
        self.assertEqual(got["why"], "screened: title")
        self.assertEqual(self.vision.bodies, [])
        self.assertIsNone(got["source"]["title"])
        self.assertFalse(list(self.folder().glob("*.jpg")))
        self.assertEqual(self.ingest.deleted, [FETCH_ID])

    def test_a_not_a_figure_word_in_the_title_or_an_inbound_hit_in_the_uploader(self):
        for key, value, why in [("title", "my kids dancing", "screened: title"),
                                ("uploader", "heil hitler fan", "screened: uploader"),
                                ("title", "heil hitler montage", "screened: title")]:
            with self.subTest(key=key, value=value):
                self.root_reset()
                fetch = fetched()
                fetch["source"][key] = value
                self.ingest.fetch_answer = fetch
                got = self.study()
                self.assertEqual((got["usable"], got["why"]), (False, why))

    def test_a_screened_study_before_the_model_costs_nothing_of_the_day(self):
        self.secrets("REMAKE_STUDY_MAX_PER_DAY=1\n")
        fetch = fetched()
        fetch["source"]["title"] = "she was arrested"
        self.ingest.fetch_answer = fetch
        self.assertFalse(self.study()["usable"])
        self.ingest.fetch_answer = None
        self.ingest.probe_answer = {"source": "tiktok", "video_id": "7301", "url": TT_URL}
        self.ingest.fetch_answer = fetched(video_id="7301")
        self.assertTrue(self.study()["usable"])

    def test_clean_words_pass(self):
        self.assertTrue(self.study()["usable"])

    def root_reset(self):
        import shutil

        shutil.rmtree(self.root / "state", ignore_errors=True)

    def study_with(self, answer):
        self.vision = FakeVision(reply(answer))
        return self.study()


class MediaTest(Base):
    """Media is kept only for a usable study; the rest is swept by age."""

    def test_a_flagged_study_keeps_only_its_study_json_and_no_paths(self):
        self.vision = FakeVision(reply(breakdown(news=True)))
        got = self.study()
        self.assertFalse(got["usable"])
        self.assertIsNone(got["audio"])
        self.assertEqual([s["frame"] for s in got["shots"]], [None, None])
        self.assertEqual([p.name for p in self.folder().iterdir()], ["study.json"])
        again = self.study()
        self.assertTrue(again["cached"])
        self.assertIsNone(again["audio"])

    def test_an_unreadable_study_keeps_nothing(self):
        self.vision = FakeVision(reply("nonsense"))
        self.assertFalse(self.study()["usable"])
        self.assertFalse(self.folder().exists())

    def test_a_usable_study_is_moved_in_whole_and_nothing_is_left_staged(self):
        self.study()
        remake = self.root / "state" / "media" / "remake"
        self.assertEqual([p.name for p in remake.iterdir()], ["tt-7300"])

    def test_nothing_is_left_staged_when_a_pull_fails_or_the_model_call_breaks(self):
        self.ingest.files = {"shot-2.jpg": b"nope"}
        self.rule()
        self.ingest.files = {}
        self.vision = FakeVision(RuntimeError("boom"))
        with self.assertRaises(RuntimeError):
            self.study()
        remake = self.root / "state" / "media" / "remake"
        self.assertEqual(list(remake.iterdir()) if remake.exists() else [], [])

    def test_a_usable_restudy_replaces_the_folder(self):
        self.study()
        (self.folder() / "stray.txt").write_text("x")
        (self.folder() / "study.json").unlink()
        self.study()
        self.assertFalse((self.folder() / "stray.txt").exists())

    def test_folders_older_than_the_keep_window_go_whenever_a_study_runs(self):
        import os
        import time

        remake = self.root / "state" / "media" / "remake"
        for name, hours in (("old", 60), ("fresh", 5), (".staging-x", 60)):
            (remake / name).mkdir(parents=True)
            (remake / name / "f").write_text("x")
            at = time.time() - hours * 3600
            os.utime(remake / name, (at, at))
        self.study()
        self.assertEqual(sorted(p.name for p in remake.iterdir()), ["fresh", "tt-7300"])
        self.secrets("REMAKE_KEEP_HOURS=2\n")
        self.ingest.probe_answer = {"source": "tiktok", "video_id": "7301", "url": TT_URL}
        self.ingest.fetch_answer = fetched(video_id="7301")
        self.study()
        self.assertEqual(sorted(p.name for p in remake.iterdir()), ["tt-7300", "tt-7301"])

    def root_reset(self):
        import shutil

        shutil.rmtree(self.root / "state", ignore_errors=True)


class SpendTest(Base):
    def test_the_day_is_capped_and_the_cap_is_a_rule(self):
        self.secrets("REMAKE_STUDY_MAX_PER_DAY=2\n")
        for n in (1, 2):
            self.ingest.probe_answer = {"source": "tiktok", "video_id": f"7{n}", "url": TT_URL}
            self.ingest.fetch_answer = fetched(video_id=f"7{n}")
            self.study()
        self.ingest.probe_answer = {"source": "tiktok", "video_id": "73", "url": TT_URL}
        asked = len(self.vision.bodies)
        self.assertEqual(self.rule(), "per_day")
        self.assertEqual(len(self.vision.bodies), asked)
        self.assertEqual(self.ingest.asked("fetch").__len__(), 2)

    def test_zero_is_off(self):
        self.secrets("REMAKE_STUDY_MAX_PER_DAY=0\n")
        self.assertEqual(self.rule(), "off")
        self.assertEqual(self.rule({"id": "chart:tt:1"}), "off")
        self.assertEqual(self.ingest.calls, [])

    def test_the_default_is_three_a_day(self):
        for n in (1, 2, 3):
            self.ingest.probe_answer = {"source": "tiktok", "video_id": f"7{n}", "url": TT_URL}
            self.ingest.fetch_answer = fetched(video_id=f"7{n}")
            self.study()
        self.ingest.probe_answer = {"source": "tiktok", "video_id": "74", "url": TT_URL}
        self.assertEqual(self.rule(), "per_day")

    def test_the_spend_is_a_row_of_its_own_and_not_a_media_asset(self):
        self.study()
        conn = hand.db()
        try:
            row = conn.execute("SELECT * FROM remake_studies").fetchone()
            self.assertEqual((row["study_id"], row["model"], row["ok"], row["asks"], row["tokens_in"], row["tokens_out"], row["cost_micro"]),
                             ("tt-7300", "glm-5.3-flash", 1, 1, 900, 300, 12300))
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertNotIn("media_assets", tables)
        finally:
            conn.close()

    def test_a_study_that_did_not_get_as_far_as_the_model_does_not_use_the_day_up(self):
        self.secrets("REMAKE_STUDY_MAX_PER_DAY=1\n")
        self.ingest.fetch_answer = IngestError("the ingest service answered 422: Sign in to confirm", 422)
        self.assertEqual(self.rule(), "fetch")
        self.ingest.fetch_answer = None
        self.assertTrue(self.study()["usable"])

    def test_a_reading_that_failed_still_counts_because_the_model_was_paid(self):
        self.secrets("REMAKE_STUDY_MAX_PER_DAY=1\n")
        self.vision = FakeVision(reply("nonsense"))
        self.assertFalse(self.study()["usable"])
        self.assertEqual(self.rule(), "per_day")

    def test_a_model_that_cannot_be_reached_is_unusable_not_a_crash(self):
        from teeny_hands.media.surplus import SurplusError

        self.vision = FakeVision(SurplusError("Surplus answered 401", 401))
        got = self.study()
        self.assertFalse(got["usable"])
        self.assertIn("could not be read", got["why"])


class CacheTest(Base):
    def test_a_second_ask_is_the_first_answer_and_spends_nothing(self):
        first = self.study()
        asked = (len(self.vision.bodies), len(self.ingest.asked("fetch")))
        again = self.study()
        self.assertTrue(again["cached"])
        self.assertEqual(again["cost_usd"], 0.0)
        self.assertEqual({k: v for k, v in again.items() if k not in ("cached", "cost_usd")},
                         {k: v for k, v in first.items() if k not in ("cached", "cost_usd")})
        self.assertEqual((len(self.vision.bodies), len(self.ingest.asked("fetch"))), asked)
        conn = hand.db()
        try:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM remake_studies").fetchone()[0], 1)
        finally:
            conn.close()

    def test_a_chart_id_that_is_cached_needs_no_ingest_at_all(self):
        self.study()
        self.ingest.calls.clear()
        got = self.study({"id": "chart:tt:7300"})
        self.assertTrue(got["cached"])
        self.assertEqual(self.ingest.calls, [])

    def test_a_cached_study_still_reads_when_the_day_is_used_up(self):
        self.study()
        self.secrets("REMAKE_STUDY_MAX_PER_DAY=1\n")
        self.assertTrue(self.study()["cached"])

    def test_an_unreadable_answer_is_not_kept_so_a_retry_asks_again(self):
        # the first reading takes all three of the model's asks
        self.vision = FakeVision(reply("nonsense"), reply("nonsense"), reply("nonsense"), reply(breakdown()))
        self.assertFalse(self.study()["usable"])
        self.assertFalse((self.folder() / "study.json").exists())
        self.assertTrue(self.study()["usable"])

    def test_an_unusable_but_readable_study_is_kept(self):
        self.vision = FakeVision(reply(breakdown(news=True)))
        self.study()
        again = self.study()
        self.assertTrue(again["cached"])
        self.assertFalse(again["usable"])


class SourceTest(Base):
    def test_a_video_already_made_is_refused(self):
        ledger.mark_used("7300", "tiktok", TT_URL, "a dance", "tt-7300")
        self.assertEqual(self.rule(), "source")
        self.assertEqual(self.ingest.asked("fetch"), [])
        self.assertEqual(self.rule({"id": "chart:tt:7300"}), "source")

    def test_a_url_off_the_list_is_refused_before_anything_is_asked(self):
        for url in ("http://www.tiktok.com/@a/video/1", "https://evil.example/x", "https://user@www.tiktok.com/x",
                    "https://www.tiktok.com:8080/x", "https://127.0.0.1/x", "ftp://youtu.be/x", "--exec=id",
                    "https://www.youtube.com/watch?v=a b"):
            with self.subTest(url=url):
                self.assertEqual(self.rule({"url": url}), "source")
        self.assertEqual(self.ingest.calls, [])

    def test_an_id_that_is_not_a_chart_id_or_is_unknown_is_refused(self):
        for body in ({"id": "chart:tt:999"}, {"id": "whatever"}, {"id": "chart:tt:../x"}, {"id": "chart:zz:1"}):
            with self.subTest(body=body):
                self.assertEqual(self.rule(body), "source")

    def test_a_body_that_is_not_one_id_or_one_url_is_an_error_not_a_rule(self):
        for body in (None, [], "x", {}, {"id": "chart:tt:1", "url": TT_URL}, {"url": 5}, {"url": TT_URL, "extra": 1}):
            with self.subTest(body=body):
                with self.assertRaises(hand.HandError):
                    study.study(body, ingest=self.ingest, api=self.vision)

    def test_the_video_the_service_says_it_is_names_the_study(self):
        self.ingest.probe_answer = {"source": "youtube", "video_id": "dQw4w9WgXcQ", "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"}
        self.ingest.fetch_answer = {**fetched(video_id="dQw4w9WgXcQ"), "source": {**fetched()["source"], "source": "youtube", "video_id": "dQw4w9WgXcQ"}}
        self.assertEqual(self.study({"url": "https://youtu.be/dQw4w9WgXcQ"})["study_id"], "yt-dQw4w9WgXcQ")

    def test_a_probe_that_gives_an_id_that_is_not_one_is_a_fetch_failure(self):
        self.ingest.probe_answer = {"source": "tiktok", "video_id": "../../etc", "url": TT_URL}
        self.assertEqual(self.rule(), "fetch")


class IngestTest(Base):
    def test_a_service_that_is_unreachable_or_busy_is_the_ingest_rule(self):
        for error in (IngestError("could not reach the ingest service (URLError)"), IngestError("busy", 429),
                      IngestError("broke", 500)):
            with self.subTest(error=str(error)):
                self.ingest.fetch_answer = error
                self.assertEqual(self.rule(), "ingest")

    def test_a_video_it_could_not_fetch_is_the_fetch_rule(self):
        for error in (IngestError("the service answered 422: live", 422), IngestError("gone", 404)):
            with self.subTest(error=str(error)):
                self.ingest.fetch_answer = error
                self.assertEqual(self.rule(), "fetch")

    def test_the_probe_failing_the_same_way(self):
        self.ingest.probe_answer = IngestError("could not reach the ingest service (URLError)")
        self.assertEqual(self.rule(), "ingest")
        self.ingest.probe_answer = IngestError("the service answered 422: age-restricted", 422)
        self.assertEqual(self.rule(), "fetch")

    def test_a_file_that_is_not_what_its_name_says_is_refused_and_the_fetch_still_deleted(self):
        cases = {"source.mp4": b"<html>not a video</html>", "audio.m4a": b"ID3\x04\x00\x00" + b"\x00" * 30,
                 "shot-1.jpg": b"\x89PNG\r\n\x1a\n" + b"\x00" * 30, "shot-2.jpg": b""}
        for name, data in cases.items():
            with self.subTest(name=name):
                self.root_reset()
                self.ingest.deleted.clear()
                self.ingest.files = {name: data}
                self.assertEqual(self.rule(), "fetch")
                self.assertEqual(self.ingest.deleted, [FETCH_ID])
                self.assertEqual(self.vision.bodies, [])
                self.assertFalse((self.folder() / "study.json").exists())

    def root_reset(self):
        import shutil

        shutil.rmtree(self.root / "state", ignore_errors=True)

    def test_a_file_over_its_cap_is_refused(self):
        self.ingest.files = {"shot-1.jpg": IngestError("the ingest service sent more than 2 MB", 413)}
        self.assertEqual(self.rule(), "fetch")
        self.assertEqual(self.ingest.deleted, [FETCH_ID])

    def test_the_caps_are_passed_to_each_pull(self):
        self.study()
        limits = {c[2]: c[3] for c in self.ingest.asked("file")}
        self.assertEqual(limits["source.mp4"], study.MAX_SOURCE_BYTES)
        self.assertEqual(limits["audio.m4a"], study.MAX_AUDIO_BYTES)
        self.assertEqual(limits["shot-1.jpg"], study.MAX_FRAME_BYTES)

    def test_an_answer_that_is_not_a_fetch_is_refused(self):
        bad = [dict(fetched(), files=["source.mp4", "../../x", "shot-1.jpg", "shot-2.jpg"]),
               dict(fetched(), files=["audio.m4a", "shot-1.jpg", "shot-2.jpg"]),
               dict(fetched(), shots=[]),
               dict(fetched(), shots=[{"n": 1, "start": 0, "seconds": 3.0, "frame": "../x.jpg"}]),
               dict(fetched(), shots=[{"n": 2, "start": 0, "seconds": 3.0, "frame": "shot-2.jpg"}]),
               dict(fetched(), aspect="4:3"), dict(fetched(), fetch_id="../x"),
               dict(fetched(), shots=fetched(10)["shots"], files=fetched(10)["files"]),
               dict(fetched(), shots=[{"n": 1, "start": 0, "seconds": "3", "frame": "shot-1.jpg"}, fetched()["shots"][1]])]
        for answer in bad:
            with self.subTest(answer=answer):
                self.root_reset()
                self.ingest.fetch_answer = answer
                self.assertEqual(self.rule(), "fetch")
                self.assertEqual(self.vision.bodies, [])


class CliTest(Base):
    def run_cli(self, argv, body=None):
        out = io.StringIO()
        stdin = io.StringIO(json.dumps(body)) if body is not None else io.StringIO("")
        from unittest import mock

        with contextlib.redirect_stdout(out), mock.patch("sys.stdin", stdin):
            code = cli.main(argv)
        return code, json.loads(out.getvalue())

    def test_study_off_exits_two_with_its_rule(self):
        self.secrets("REMAKE_STUDY_MAX_PER_DAY=0\n")
        code, got = self.run_cli(["study"], {"url": TT_URL})
        self.assertEqual(code, 2)
        self.assertFalse(got["ok"])
        self.assertEqual(got["rule"], "off")
        self.assertIn("refused", got)

    def test_a_url_off_the_list_exits_two_with_the_source_rule(self):
        code, got = self.run_cli(["study"], {"url": "http://evil.example/x"})
        self.assertEqual((code, got["rule"]), (2, "source"))
