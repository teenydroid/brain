"""hands/reel: clips and songs rendered by the reel service (MoneyPrinterTurbo
behind deploy/reel/serve.py), against a fake of that service over real HTTP.
The jobs always pin the operator's rules, paths stay inside Teeny's media,
the caps hold, and a render is checked before it is kept."""

import ast
import importlib.util
import json
import os
import re
import sqlite3
import struct
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent / "lib"))

from teeny_hands import hand  # noqa: E402
from teeny_hands.media import assets  # noqa: E402
from teeny_hands.media.surplus import SurplusError  # noqa: E402
from teeny_hands.reel import reels  # noqa: E402
from teeny_hands.reel.service import STATE_COMPLETE, STATE_FAILED, STATE_PROCESSING, Reel  # noqa: E402

REPO = HERE.parents[2]
SCHEMA_PY = REPO / "vendor" / "moneyprinter" / "app" / "models" / "schema.py"
POLICY_PY = REPO / "deploy" / "reel" / "policy.py"


def load_policy():
    """deploy/reel/policy.py, the front's rules, which import nothing of
    upstream's."""
    spec = importlib.util.spec_from_file_location("reel_policy", POLICY_PY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
T0 = 1_789_369_200.0  # 2026-09-14T07:00:00Z
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
MP3 = b"ID3\x04\x00\x00\x00\x00\x00\x00" + b"\x00" * 64
TASK = "0e19deb3-7e54-4a71-b103-2e394ae488be"


def box(name: bytes, body: bytes) -> bytes:
    return struct.pack(">I4s", 8 + len(body), name) + body


def mp4(seconds: float = 12.0, tracks=(b"vide", b"soun")) -> bytes:
    """A minimal mp4: ftyp, then moov with mvhd (timescale 1000) and one
    trak per handler type."""
    mvhd = box(b"mvhd", b"\x00\x00\x00\x00" + struct.pack(">IIII", 0, 0, 1000, int(seconds * 1000)) + b"\x00" * 80)
    traks = b"".join(box(b"trak", box(b"mdia", box(b"hdlr", b"\x00" * 8 + kind + b"\x00" * 12))) for kind in tracks)
    return box(b"ftyp", b"isom\x00\x00\x02\x00isomiso2") + box(b"moov", mvhd + traks) + box(b"mdat", b"\x00" * 32)


class FakeReel:
    """The reel service as hands/reel sees it: the front's route, the task
    status, the download and the delete. `states` is what successive polls
    answer; `render` is the downloaded file."""

    def __init__(self):
        self.requests = []
        self.jobs = []
        self.states = [4, 1]
        self.render = mp4()
        self.submit_status = 200
        self.error = None
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _send(self, status, body, content_type="application/json"):
                data = body if isinstance(body, bytes) else json.dumps(body).encode()
                self.send_response(status)
                self.send_header("content-type", content_type)
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_POST(self):
                length = int(self.headers.get("content-length") or 0)
                body = json.loads(self.rfile.read(length) or b"{}")
                fake.requests.append(("POST", self.path))
                if self.path != "/teeny/v1/reels":
                    return self._send(403, {"status": 403, "message": "not served here"})
                fake.jobs.append(body)
                if fake.submit_status != 200:
                    return self._send(fake.submit_status, {"status": fake.submit_status, "message": "queue is full"})
                self._send(200, {"status": 200, "data": {"task_id": TASK}})

            def do_GET(self):
                fake.requests.append(("GET", self.path))
                if self.path == f"/api/v1/tasks/{TASK}":
                    state = fake.states.pop(0) if len(fake.states) > 1 else fake.states[0]
                    data = {"task_id": TASK, "state": state, "progress": 100 if state == 1 else 40}
                    if state == -1:
                        data.update(failed_stage="audio", error=fake.error or "TTS request timed out")
                    return self._send(200, {"status": 200, "data": data})
                if self.path == f"/api/v1/download/{TASK}/final-1.mp4":
                    return self._send(200, fake.render, "video/mp4")
                self._send(404, {"status": 404, "message": "task not found"})

            def do_DELETE(self):
                fake.requests.append(("DELETE", self.path))
                self._send(200, {"status": 200})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()

    def deleted(self):
        return ("DELETE", f"/api/v1/tasks/{TASK}") in self.requests


class FakeSurplus:
    """Surplus's music route, shaped like its video route, with knobs for
    the shapes the real one might answer instead."""

    def __init__(self):
        self.calls = []
        self.quote = 414000
        self.statuses = ["running", "succeeded"]
        self.audio = MP3
        self.post_answer = None
        self.post_error = None
        self.post_errors = {}
        self.poll_answer = None
        self.no_results = False
        self.download_error = None

    def call(self, method, path, body=None, timeout=60.0, headers=None):
        self.calls.append((method, path, body))
        if path == "/v1/music/generations" and method == "POST":
            if body["model"] in self.post_errors:
                raise self.post_errors[body["model"]]
            if self.post_error:
                raise self.post_error
            if self.post_answer is not None:
                return self.post_answer
            return {"id": "music-job-1", "status": "queued", "estimated_cost_usdc": str(self.quote)}
        if path.startswith("/v1/music/generations/") and method == "GET":
            if self.poll_answer is not None:
                return self.poll_answer
            status = self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]
            out = {"id": "music-job-1", "status": status}
            if status.lower() in ("succeeded", "completed", "success", "done") and not self.no_results:
                out["results"] = [{"url": "https://api.surplusintelligence.ai/v1/media/artifacts/music-job-1/0"}]
                out["usage"] = {"buyer_cost_usdc": "414000"}
            return out
        if method == "DELETE":
            return {}
        raise AssertionError(f"unexpected call {method} {path}")

    def download(self, url, limit, timeout=120.0):
        if self.download_error:
            raise self.download_error
        return self.audio


class ReelTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "secrets").mkdir()
        (self.root / "state" / "media").mkdir(parents=True)
        (self.root / "brain" / "assets").mkdir(parents=True)
        (self.root / "brain" / "assets" / "teeny.png").write_bytes(PNG)
        (self.root / "state" / "media" / "image-1.png").write_bytes(PNG)
        (self.root / "state" / "media" / "video-2.mp4").write_bytes(mp4(5.0, (b"vide",)))
        (self.root / "state" / "media" / "music-3.mp3").write_bytes(MP3)
        self.fake = FakeReel()
        (self.root / "secrets" / "teeny.env").write_text(f"DRY_RUN=0\nREEL_URL={self.fake.url}\n")
        self._env = dict(os.environ)
        os.environ["TEENY_ROOT"] = str(self.root)
        os.environ["TEENY_NOW"] = str(T0)
        os.environ.pop("DRY_RUN", None)
        hand._ENV_CACHE = None

    def tearDown(self):
        self.fake.close()
        os.environ.clear()
        os.environ.update(self._env)
        hand._ENV_CACHE = None
        self.tmp.cleanup()

    def env(self, **values):
        path = self.root / "secrets" / "teeny.env"
        path.write_text(path.read_text() + "".join(f"{k}={v}\n" for k, v in values.items()))
        hand._ENV_CACHE = None

    def body(self, **extra):
        body = {
            "kind": "clip",
            "script": "I pay for my own server.",
            "materials": ["state/media/image-1.png", "state/media/video-2.mp4", "brain/assets/teeny.png"],
        }
        body.update(extra)
        return body

    def make(self, **extra):
        return reels.make(self.body(**extra), sleep=lambda _: None)

    def rows(self):
        conn = assets._db()
        try:
            return [dict(r) for r in conn.execute("SELECT * FROM media_assets ORDER BY id")]
        finally:
            conn.close()

    # --- the job --------------------------------------------------------------------------

    def test_every_job_pins_local_sequential_no_stock_no_upload(self):
        self.make(
            video_source="pexels",
            video_concat_mode="random",
            bgm_type="random",
            upload_post_enabled=True,
            upload_post_auto_upload=True,
            video_terms=["cats"],
            match_materials_to_script=True,
        )
        job = self.fake.jobs[0]
        self.assertEqual(job["video_source"], "local")
        self.assertEqual(job["video_concat_mode"], "sequential")
        self.assertEqual(job["video_count"], 1)
        self.assertIs(job["match_materials_to_script"], False)
        self.assertEqual((job["bgm_type"], job["bgm_file"], job["bgm_volume"]), ("", "", 0.0))
        self.assertFalse([k for k in job if "upload" in k or k == "video_terms"])
        self.assertEqual(
            job["video_materials"],
            [
                {"provider": "local", "url": "media/image-1.png"},
                {"provider": "local", "url": "media/video-2.mp4"},
                {"provider": "local", "url": "assets/teeny.png"},
            ],
        )
        self.assertEqual(job["video_script"], "I pay for my own server.")
        self.assertEqual(job["subtitle_display_mode"], "word_by_word")
        self.assertTrue(job["voice_name"].endswith("Neural-Female"))

    def test_a_named_bed_is_the_only_music_and_sits_low(self):
        self.make(bed="state/media/music-3.mp3")
        job = self.fake.jobs[0]
        self.assertEqual((job["bgm_type"], job["bgm_file"]), ("custom", "media/music-3.mp3"))
        self.assertLessEqual(job["bgm_volume"], 0.2)

    def test_a_song_carries_its_track_and_no_bed(self):
        out = self.make(kind="song", audio="state/media/music-3.mp3")["asset"]
        job = self.fake.jobs[0]
        self.assertEqual(job["audio"], "media/music-3.mp3")
        self.assertEqual(job["bgm_type"], "")
        self.assertIs(job["subtitle_enabled"], False)
        self.assertEqual(out["kind"], "song")
        with self.assertRaises(hand.Refused):
            self.make(kind="song")
        with self.assertRaises(hand.Refused):
            self.make(kind="song", audio="state/media/music-3.mp3", bed="state/media/music-3.mp3")
        with self.assertRaises(hand.Refused):
            self.make(audio="state/media/music-3.mp3")

    def test_only_an_edge_voice_and_known_captions(self):
        for bad in ({"voice": "elevenlabs:abc:Rachel"}, {"voice": "en-US-Ana"}, {"captions": "karaoke"}, {"aspect": "4:3"}):
            with self.subTest(bad=bad), self.assertRaises(hand.Refused):
                self.make(**bad)
        self.assertEqual(self.fake.jobs, [])

    # --- paths -------------------------------------------------------------------------------

    def test_a_path_outside_teenys_media_is_refused(self):
        (self.root / "secrets" / "key.png").write_bytes(PNG)
        (self.root / "state" / "media" / "sneaky.png").symlink_to(self.root / "secrets" / "key.png")
        for path in ("secrets/key.png", "state/media/../../secrets/key.png", "state/media/sneaky.png", str(self.root / "secrets/key.png"), "/etc/passwd", "state/media/missing.png"):
            with self.subTest(path=path), self.assertRaises(hand.Refused) as no:
                self.make(materials=[path])
            self.assertEqual(no.exception.detail.get("rule"), "path")
        with self.assertRaises(hand.Refused):
            self.make(bed="secrets/key.png")
        self.assertEqual(self.fake.requests, [])
        self.assertEqual(self.rows(), [])

    def test_a_material_must_be_a_picture_or_a_clip(self):
        (self.root / "state" / "media" / "notes.png").write_text("not a picture")
        with self.assertRaises(hand.Refused):
            self.make(materials=["state/media/notes.png"])
        with self.assertRaises(hand.Refused):
            self.make(materials=["state/media/music-3.mp3"])

    # --- the render ------------------------------------------------------------------------------

    def test_the_render_is_copied_checked_and_recorded(self):
        out = self.make()["asset"]
        self.assertEqual(out["status"], "done")
        self.assertEqual(out["kind"], "reel")
        self.assertEqual(out["path"], f"state/media/reel-{out['id']}.mp4")
        self.assertEqual((self.root / out["path"]).read_bytes(), self.fake.render)
        self.assertEqual(out["detail"]["seconds"], 12.0)
        self.assertEqual(out["cost_usd"], 0.0)
        self.assertTrue(self.fake.deleted())
        row = self.rows()[0]
        self.assertEqual((row["job_id"], row["model"]), (TASK, "moneyprinter"))

    def test_a_render_that_never_finishes_fails_and_leaves_no_file(self):
        self.fake.states = [4]
        self.env(REEL_WAIT_SECONDS="60")
        clock = iter(range(0, 10_000, 30))
        real = reels.time.monotonic
        reels.time.monotonic = lambda: float(next(clock))
        try:
            with self.assertRaises(hand.HandError) as err:
                self.make()
        finally:
            reels.time.monotonic = real
        self.assertIn("not done after 60 s", str(err.exception))
        row = self.rows()[0]
        self.assertEqual(row["status"], "failed")
        self.assertIsNone(row["path"])
        self.assertEqual(list((self.root / "state" / "media").glob("reel-*")), [])
        self.assertTrue(self.fake.deleted())
        # A failed render counts toward nothing: the next one may go.
        self.fake.states = [1]
        self.assertEqual(self.make()["asset"]["status"], "done")

    def test_a_failed_render_says_where_it_failed(self):
        self.fake.states = [4, -1]
        with self.assertRaises(hand.HandError) as err:
            self.make()
        self.assertIn("audio", str(err.exception))
        self.assertEqual(self.rows()[0]["status"], "failed")

    def test_a_render_that_is_not_a_voiced_mp4_is_not_kept(self):
        for render in (PNG, mp4(12.0, (b"vide",)), mp4(400.0)):
            with self.subTest(render=render[:12]):
                self.fake.render = render
                self.fake.states = [1]
                with self.assertRaises(hand.HandError):
                    self.make()
        self.assertEqual(list((self.root / "state" / "media").glob("reel-*")), [])
        self.assertTrue(all(r["status"] == "failed" for r in self.rows()))

    def test_a_service_that_will_not_take_the_job_fails_the_row(self):
        self.fake.submit_status = 429
        with self.assertRaises(hand.HandError) as err:
            self.make()
        self.assertIn("429", str(err.exception))
        self.assertEqual(self.rows()[0]["status"], "failed")

    def test_mp4_header_gives_seconds_and_tracks(self):
        self.assertEqual(reels.mp4_info(mp4(11.766)), {"seconds": 11.766, "tracks": ["vide", "soun"]})

    # --- caps -----------------------------------------------------------------------------------

    def test_one_clip_a_day(self):
        self.make()
        with self.assertRaises(hand.Refused) as no:
            self.make()
        self.assertEqual(no.exception.detail["rule"], "per_day")
        self.assertEqual(len(self.fake.jobs), 1)
        os.environ["TEENY_NOW"] = str(T0 + 86400)
        self.assertEqual(self.make()["asset"]["status"], "done")

    def test_one_song_a_week(self):
        self.make(kind="song", audio="state/media/music-3.mp3")
        os.environ["TEENY_NOW"] = str(T0 + 6 * 86400)
        with self.assertRaises(hand.Refused) as no:
            self.make(kind="song", audio="state/media/music-3.mp3")
        self.assertEqual(no.exception.detail["rule"], "per_week")
        os.environ["TEENY_NOW"] = str(T0 + 7 * 86400 + 1)
        self.assertEqual(self.make(kind="song", audio="state/media/music-3.mp3")["asset"]["kind"], "song")

    def test_a_day_past_the_media_budget_makes_no_reel(self):
        conn = assets._db()
        with hand.transaction(conn):
            conn.execute(
                "INSERT INTO media_assets (kind, status, prompt, cost_micro, created_at) VALUES ('video', 'done', 'x', 1000001, ?)",
                (T0,),
            )
        conn.close()
        with self.assertRaises(hand.Refused) as no:
            self.make()
        self.assertEqual(no.exception.detail["rule"], "budget")

    def test_the_runway_tier_that_cuts_media_cuts_reels(self):
        self.env(RUNWAY="1")
        with self.assertRaises(hand.Refused) as no:
            self.make()
        self.assertEqual(no.exception.detail["rule"], "runway")
        self.assertEqual(self.fake.jobs, [])

    def test_dry_run_says_what_it_would_make_and_calls_nothing(self):
        self.env(DRY_RUN="1")
        out = self.make(bed="state/media/music-3.mp3")
        self.assertEqual(out["would_make"], "clip")
        self.assertEqual(out["job"]["bgm_file"], "media/music-3.mp3")
        self.assertEqual(self.fake.requests, [])
        self.assertEqual(self.rows(), [])

    # --- music ---------------------------------------------------------------------------------

    def test_music_is_off_until_switched_on(self):
        with self.assertRaises(hand.Refused) as no:
            reels.music({"style": "lo-fi"}, api=FakeSurplus())
        self.assertEqual(no.exception.detail["rule"], "off")

    def test_music_dry_run_spends_nothing(self):
        self.env(DRY_RUN="1", REEL_MUSIC="1")
        api = FakeSurplus()
        out = reels.music({"style": "lo-fi", "lyrics": "beep boop", "seconds": 40}, api=api)
        self.assertEqual((out["would_make"], out["seconds"], out["enabled"]), ("music", 40, True))
        self.assertIn("beep boop", out["prompt"])
        self.assertEqual(api.calls, [])

    def test_music_is_bought_saved_and_counted(self):
        self.env(REEL_MUSIC="1")
        api = FakeSurplus()
        out = reels.music({"style": "lo-fi", "lyrics": "beep boop", "seconds": 30}, api=api, sleep=lambda _: None)["asset"]
        method, path, body = api.calls[0]
        self.assertEqual((method, path, body["model"], body["duration_seconds"]), ("POST", "/v1/music/generations", "elevenlabs-music-v2-5", 30))
        self.assertEqual(out["kind"], "music")
        self.assertEqual(out["cost_usd"], 0.414)
        self.assertEqual((self.root / out["path"]).read_bytes(), MP3)
        self.assertTrue(out["path"].endswith(".mp3"))
        # Counted in the shared media budget, and one track a week.
        self.assertEqual(reels.listing(10)["spent_today_usd"], 0.414)
        with self.assertRaises(hand.Refused) as no:
            reels.music({"style": "lo-fi"}, api=api, sleep=lambda _: None)
        self.assertEqual(no.exception.detail["rule"], "per_week")

    def test_a_track_quoted_over_the_cap_is_cancelled(self):
        self.env(REEL_MUSIC="1", REEL_MUSIC_MAX_USD_PER_JOB="0.30")
        api = FakeSurplus()
        with self.assertRaises(hand.HandError):
            reels.music({"style": "lo-fi"}, api=api, sleep=lambda _: None)
        self.assertIn(("DELETE", "/v1/music/generations/music-job-1", None), api.calls)
        self.assertEqual(self.rows()[-1]["status"], "failed")

    def test_music_reserves_its_cap_against_the_day_budget(self):
        self.env(REEL_MUSIC="1", MEDIA_MAX_USD_PER_DAY="0.50")
        with self.assertRaises(hand.Refused) as no:
            reels.music({"style": "lo-fi"}, api=FakeSurplus())
        self.assertEqual(no.exception.detail["rule"], "budget")

    # --- failure paths keep the ledger honest -----------------------------------------------

    def assert_nothing_kept(self):
        rows = self.rows()
        self.assertEqual(rows[-1]["status"], "failed")
        self.assertIsNone(rows[-1]["path"])
        self.assertEqual(sorted(p.name for p in (self.root / "state" / "media").glob("reel-*")), [])
        self.assertTrue(self.fake.deleted())
        # The cap is not consumed: the next clip goes.
        self.fake.render, self.fake.states = mp4(), [1]
        self.assertEqual(self.make()["asset"]["status"], "done")

    def test_a_truncated_mvhd_is_not_a_valid_mp4(self):
        moov = box(b"moov", box(b"mvhd", b"\x00\x00\x00\x00\x00\x00") + box(b"trak", box(b"mdia", box(b"hdlr", b"\x00" * 8 + b"vide" + b"\x00" * 12))))
        self.fake.render = box(b"ftyp", b"isom\x00\x00\x02\x00") + moov
        with self.assertRaises(hand.HandError) as err:
            self.make()
        self.assertIn("not a valid mp4", str(err.exception))
        self.assert_nothing_kept()

    def test_odd_boxes_never_read_past_their_end(self):
        for data in (
            box(b"moov", box(b"mvhd", b"\x01" + b"\x00" * 20)),
            box(b"moov", box(b"trak", box(b"mdia", box(b"hdlr", b"\x00" * 9)))),
            box(b"moov", b"\x00\x00\x00\x01mvhd\x00\x00"),
            box(b"moov", box(b"mvhd", b"")),
        ):
            with self.subTest(data=data), self.assertRaises(ValueError):
                reels.mp4_info(box(b"ftyp", b"isom") + data)
        self.assertEqual(reels.mp4_info(b"\x00\x00\x00\x10ftypisom\x00\x00\x00\x00\xff\xff\xff\xffmoov"), {"seconds": None, "tracks": []})

    def test_a_disk_that_will_not_take_the_file_leaves_nothing(self):
        with mock.patch.object(Path, "write_bytes", side_effect=OSError("No space left on device")):
            with self.assertRaises(OSError):
                self.make()
        self.assertIn("No space left", self.rows()[-1]["error"])
        self.assert_nothing_kept()

    def test_an_interrupted_make_leaves_no_pending_row(self):
        class Stopped(Reel):
            def download(self, task_id, limit):
                raise KeyboardInterrupt

        with self.assertRaises(KeyboardInterrupt):
            reels.make(self.body(), api=Stopped(self.fake.url), sleep=lambda _: None)
        self.assertNotIn("pending", [r["status"] for r in self.rows()])
        self.assert_nothing_kept()

    def test_two_makes_at_once_take_one_slot(self):
        self.fake.states = [1]
        results = []

        def run():
            try:
                results.append(reels.make(self.body(), sleep=lambda _: None)["asset"]["status"])
            except hand.Refused as refused:
                results.append(refused.detail.get("rule"))

        threads = [threading.Thread(target=run) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(sorted(results), ["done", "per_day"])
        self.assertEqual(len(self.fake.jobs), 1)

    def test_a_music_claim_reserves_its_cap_for_everyone(self):
        asset_id = reels._claim("music", "x", {}, 700000)
        conn = assets._db()
        try:
            self.assertEqual(conn.execute("SELECT cost_micro, status FROM media_assets WHERE id = ?", (asset_id,)).fetchone()[:], (700000, "pending"))
            self.assertEqual(assets._spent_today(conn), 700000)
            with self.assertRaises(hand.Refused) as no:
                assets._check_caps(conn, "video", 400000)
            self.assertEqual(no.exception.detail["rule"], "budget")
        finally:
            conn.close()

    # --- music fails closed ------------------------------------------------------------------

    def music_unknown(self, api, **env):
        self.env(REEL_MUSIC="1", REEL_MUSIC_WAIT_SECONDS="60", **env)
        clock = iter(range(0, 100_000, 30))
        with mock.patch.object(reels.time, "monotonic", lambda: float(next(clock))):
            with self.assertRaises(hand.HandError) as err:
                reels.music({"style": "lo-fi"}, api=api, sleep=lambda _: None)
        self.assertNotIsInstance(err.exception, hand.Refused)
        posts = [c for c in api.calls if c[0] == "POST"]
        self.assertEqual(len(posts), 1, "never a second purchase")
        row = self.rows()[-1]
        self.assertEqual(row["status"], "unknown")
        self.assertGreater(row["cost_micro"], 0)
        self.assertEqual(list((self.root / "state" / "media").glob("music-*.*")), [])
        # Counted: in the day's spend and the week's cap.
        self.assertEqual(reels.listing(10)["spent_today_usd"], row["cost_micro"] / 1e6)
        with self.assertRaises(hand.Refused) as no:
            reels.music({"style": "lo-fi"}, api=api, sleep=lambda _: None)
        self.assertEqual(no.exception.detail["rule"], "per_week")
        return row, err.exception

    def test_music_answers_of_unknown_shapes_are_booked_not_retried(self):
        cases = {
            "not an object": lambda api: setattr(api, "post_answer", ["queued"]),
            "no id": lambda api: setattr(api, "post_answer", {"status": "queued", "estimated_cost_usdc": "414000"}),
            "a number id": lambda api: setattr(api, "post_answer", {"id": 5}),
            "an odd status word": lambda api: setattr(api, "statuses", ["thinking"]),
            "a status that is not an object": lambda api: setattr(api, "poll_answer", "running"),
            "succeeded with no artifact": lambda api: setattr(api, "no_results", True),
            "an artifact that is not audio": lambda api: setattr(api, "audio", b"<html>nope</html>"),
            "a failed download": lambda api: setattr(api, "download_error", SurplusError("the download answered 500", 500)),
            "no answer to the POST": lambda api: setattr(api, "post_error", SurplusError("could not reach Surplus (timeout)")),
            "a server error to the POST": lambda api: setattr(api, "post_error", SurplusError("Surplus answered 502", 502)),
        }
        for name, setup in cases.items():
            with self.subTest(case=name):
                self.tearDown()
                self.setUp()
                api = FakeSurplus()
                setup(api)
                row, _ = self.music_unknown(api)
                self.assertIn(row["cost_micro"], (414000, 700000))

    def test_an_odd_quote_books_the_cap(self):
        api = FakeSurplus()
        api.quote = "0.41"
        api.statuses = ["thinking"]
        row, err = self.music_unknown(api)
        self.assertEqual(row["cost_micro"], 700000)
        self.assertEqual(row["job_id"], "music-job-1")
        self.assertEqual(err.detail["outcome"], "unknown")

    def test_other_success_words_are_success(self):
        for word in ("completed", "success", "done", "SUCCEEDED"):
            with self.subTest(word=word):
                self.tearDown()
                self.setUp()
                self.env(REEL_MUSIC="1")
                api = FakeSurplus()
                api.statuses = [word]
                out = reels.music({"style": "lo-fi"}, api=api, sleep=lambda _: None)["asset"]
                self.assertEqual((out["status"], out["cost_usd"]), ("done", 0.414))

    def test_a_confirmed_failure_costs_nothing_and_tries_no_other_model(self):
        self.env(REEL_MUSIC="1")
        api = FakeSurplus()
        api.statuses = ["failed"]
        with self.assertRaises(hand.HandError):
            reels.music({"style": "lo-fi"}, api=api, sleep=lambda _: None)
        self.assertEqual(len([c for c in api.calls if c[0] == "POST"]), 1)
        row = self.rows()[-1]
        self.assertEqual((row["status"], row["cost_micro"]), ("failed", 0))

    def test_a_seller_that_refuses_before_any_job_lets_the_next_model_try(self):
        self.env(REEL_MUSIC="1")
        api = FakeSurplus()
        api.post_errors = {"elevenlabs-music-v2-5": SurplusError("Surplus answered 503: no healthy sellers", 503)}
        out = reels.music({"style": "lo-fi"}, api=api, sleep=lambda _: None)["asset"]
        self.assertEqual(out["model"], "elevenlabs-music-v2")

    # --- list, get, the ledger ----------------------------------------------------------------------

    def test_list_and_get_show_reels_songs_and_music_only(self):
        assets._db().close()
        conn = assets._db()
        with hand.transaction(conn):
            conn.execute("INSERT INTO media_assets (kind, status, prompt, created_at) VALUES ('image', 'done', 'x', ?)", (T0,))
        conn.close()
        made = self.make()["asset"]
        listed = reels.listing(10)["assets"]
        self.assertEqual([a["id"] for a in listed], [made["id"]])
        self.assertEqual(reels.get(made["id"])["asset"]["path"], made["path"])
        with self.assertRaises(hand.Refused):
            reels.get(1)

    def test_an_old_ledger_gains_the_new_kinds_and_keeps_its_rows(self):
        old = assets.SCHEMA.replace("('image', 'video', 'reel', 'song', 'music')", "('image', 'video')")
        db = sqlite3.connect(self.root / "state" / "hands.db")
        db.executescript(old)
        db.execute("INSERT INTO media_assets (id, kind, status, prompt, created_at) VALUES (1, 'image', 'done', 'still', 1)")
        db.execute("INSERT INTO media_assets (id, kind, status, prompt, source_id, created_at) VALUES (2, 'video', 'done', 'moves', 1, 2)")
        db.commit()
        db.close()
        out = self.make()["asset"]
        rows = self.rows()
        self.assertEqual([(r["id"], r["kind"], r["source_id"]) for r in rows], [(1, "image", None), (2, "video", 1), (out["id"], "reel", None)])
        conn = assets._db()
        sql = conn.execute("SELECT sql FROM sqlite_master WHERE name = 'media_assets'").fetchone()[0]
        self.assertIn("'music'", sql)
        self.assertIsNotNone(conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'media_assets_day'").fetchone())
        conn.close()


class FrontRefusesTest(unittest.TestCase):
    """The client never follows a redirect and names no more than a status."""

    def test_the_client_reports_a_refusal_with_its_message(self):
        fake = FakeReel()
        try:
            with self.assertRaises(hand.HandError) as err:
                Reel(fake.url)._json("POST", "/api/v1/scripts", {})
            self.assertIn("403", str(err.exception))
        finally:
            fake.close()


def _class_fields(source: str, name: str) -> set[str]:
    """The annotated fields of class `name` in `source`, and of every class it
    names as a base that is defined there too."""
    tree = ast.parse(source)
    classes = {n.name: n for n in tree.body if isinstance(n, ast.ClassDef)}
    fields: set[str] = set()
    todo = [name]
    while todo:
        cls = classes.get(todo.pop())
        if cls is None:
            continue
        fields |= {s.target.id for s in cls.body if isinstance(s, ast.AnnAssign) and isinstance(s.target, ast.Name)}
        todo += [b.id for b in cls.bases if isinstance(b, ast.Name)]
    return fields


@unittest.skipUnless(SCHEMA_PY.is_file() or os.environ.get("CI"), "vendor/moneyprinter is not checked out")
class SchemaDriftTest(unittest.TestCase):
    """Every field Teeny sends must still exist in the pinned release's job
    model, read from the submodule's source (never imported): a pin bump that
    renames or drops one fails here, before it reaches the box."""

    @classmethod
    def setUpClass(cls):
        cls.fields = _class_fields(SCHEMA_PY.read_text(), "TaskVideoRequest")

    def test_the_job_model_is_where_we_expect_it(self):
        self.assertIn("video_script", self.fields)
        self.assertIn("video_materials", self.fields)

    def test_every_field_hands_reel_sends_is_a_field_upstream(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "secrets").mkdir()
            (root / "secrets" / "teeny.env").write_text("DRY_RUN=1\n")
            (root / "state" / "media").mkdir(parents=True)
            (root / "state" / "media" / "a.png").write_bytes(PNG)
            (root / "state" / "media" / "b.mp3").write_bytes(MP3)
            saved = dict(os.environ)
            os.environ["TEENY_ROOT"] = tmp
            hand._ENV_CACHE = None
            try:
                clip = reels.build_job({"kind": "clip", "script": "x", "materials": ["state/media/a.png"], "bed": "state/media/b.mp3"})
                song = reels.build_job({"kind": "song", "script": "x", "materials": ["state/media/a.png"], "audio": "state/media/b.mp3"})
            finally:
                os.environ.clear()
                os.environ.update(saved)
                hand._ENV_CACHE = None
        for job in (clip, song):
            self.assertEqual(sorted(set(job) - self.fields - {"audio"}), [])
        material_fields = _class_fields(SCHEMA_PY.read_text(), "MaterialInfo")
        self.assertTrue({"provider", "url"} <= material_fields)

    def test_the_front_takes_and_pins_only_fields_upstream_has(self):
        policy = load_policy()
        self.assertEqual(sorted(policy.ALLOWED - self.fields - {"audio"}), [])
        self.assertEqual(sorted(set(policy.PINNED) - self.fields), [])
        self.assertEqual(policy.PINNED["video_source"], "local")
        self.assertEqual(policy.PINNED["video_concat_mode"], "sequential")

    def test_the_values_the_hand_hard_codes_are_upstreams(self):
        source = SCHEMA_PY.read_text()
        tree = ast.parse(source)
        enums = {}
        literals = {}
        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                enums[node.name] = {
                    s.value.value
                    for s in node.body
                    if isinstance(s, ast.Assign) and isinstance(s.value, ast.Constant) and isinstance(s.value.value, str)
                }
            if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name) and isinstance(node.value, ast.Subscript):
                literals[node.targets[0].id] = {e.value for e in ast.walk(node.value.slice) if isinstance(e, ast.Constant)}
        self.assertIn("word_by_word", literals["SubtitleDisplayMode"])
        self.assertIn("sentence", literals["SubtitleDisplayMode"])
        self.assertIn("none", literals["SubtitleAnimation"])
        self.assertIn("zoom", enums["ImageMotion"])
        self.assertIn("cover", enums["VideoFitMode"])
        self.assertIn("sequential", enums["VideoConcatMode"])
        self.assertTrue(set(reels.ASPECTS) <= enums["VideoAspect"])
        self.assertTrue(set(reels.CAPTIONS.values()) <= literals["SubtitleDisplayMode"])

    def test_states_routes_and_the_render_name_are_upstreams(self):
        app = REPO / "vendor" / "moneyprinter" / "app"
        const = (app / "models" / "const.py").read_text()
        for name, value in (("TASK_STATE_FAILED", STATE_FAILED), ("TASK_STATE_COMPLETE", STATE_COMPLETE), ("TASK_STATE_PROCESSING", STATE_PROCESSING)):
            self.assertRegex(const, rf"(?m)^{name} = {value}$")
        video = (app / "controllers" / "v1" / "video.py").read_text()
        self.assertIn('"/tasks/{task_id}"', video)
        self.assertIn('"/download/{file_path:path}"', video)
        self.assertIn('router.delete(\n    "/tasks/{task_id}"', video)
        self.assertIn('router.prefix = "/api/v1"', (app / "controllers" / "v1" / "base.py").read_text())
        self.assertIn('"/ping"', (app / "controllers" / "ping.py").read_text())
        task = (app / "services" / "task.py").read_text()
        self.assertIn('f"final-{index}.mp4"', task)
        # "custom" is no music provider upstream: with a bgm_file it plays
        # that file, and "" plays nothing.
        providers = re.search(r"_VIDEO_MUSIC_PROVIDERS = \{(.*?)\n\}", task, re.S)
        self.assertIsNotNone(providers)
        self.assertNotIn('"custom"', providers.group(1))
        bgm = (app / "services" / "video.py").read_text()
        self.assertIn("if not bgm_type:\n        return \"\"", bgm)
        self.assertIn('if bgm_type == "random":', bgm)
        bgm_service = (app / "services" / "bgm.py").read_text()
        self.assertIn("if not str(bgm_type or \"\").strip():\n        return False", bgm_service)
        # The custom audio a song carries must sit in the task's own folder.
        self.assertIn("custom audio file must be stored within the current task directory", task)


if __name__ == "__main__":
    unittest.main()
