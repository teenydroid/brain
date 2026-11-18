"""hands/reel: clips and songs rendered by the reel service (MoneyPrinterTurbo
behind deploy/reel/serve.py), against a fake of that service over real HTTP.
The jobs always pin the operator's rules, paths stay inside Teeny's media,
the caps hold, and a render is checked before it is kept."""

import ast
import json
import os
import sqlite3
import struct
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent / "lib"))

from teeny_hands import hand  # noqa: E402
from teeny_hands.media import assets  # noqa: E402
from teeny_hands.media.surplus import SurplusError  # noqa: E402
from teeny_hands.reel import reels  # noqa: E402
from teeny_hands.reel.service import Reel  # noqa: E402

REPO = HERE.parents[2]
SCHEMA_PY = REPO / "vendor" / "moneyprinter" / "app" / "models" / "schema.py"
SERVE_PY = REPO / "deploy" / "reel" / "serve.py"
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
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()

    def deleted(self):
        return ("DELETE", f"/api/v1/tasks/{TASK}") in self.requests


class FakeSurplus:
    """Surplus's music route, shaped like its video route."""

    def __init__(self):
        self.calls = []
        self.quote = 414000
        self.statuses = ["running", "succeeded"]
        self.audio = MP3

    def call(self, method, path, body=None, timeout=60.0, headers=None):
        self.calls.append((method, path, body))
        if path == "/v1/music/generations" and method == "POST":
            return {"id": "music-job-1", "status": "queued", "estimated_cost_usdc": str(self.quote)}
        if path.startswith("/v1/music/generations/") and method == "GET":
            status = self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]
            out = {"id": "music-job-1", "status": status}
            if status == "succeeded":
                out["results"] = [{"url": "https://api.surplusintelligence.ai/v1/media/artifacts/music-job-1/0"}]
                out["usage"] = {"buyer_cost_usdc": "414000"}
            return out
        if method == "DELETE":
            return {}
        raise AssertionError(f"unexpected call {method} {path}")

    def download(self, url, limit, timeout=120.0):
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
        tree = ast.parse(SERVE_PY.read_text())
        values = {}
        for node in tree.body:
            if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                if node.targets[0].id in ("ALLOWED", "PINNED"):
                    values[node.targets[0].id] = ast.literal_eval(node.value)
        self.assertEqual(sorted(values["ALLOWED"] - self.fields - {"audio"}), [])
        self.assertEqual(sorted(set(values["PINNED"]) - self.fields), [])
        self.assertEqual(values["PINNED"]["video_source"], "local")
        self.assertEqual(values["PINNED"]["video_concat_mode"], "sequential")


if __name__ == "__main__":
    unittest.main()
