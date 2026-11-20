"""deploy/reel/policy.py, the rules of Teeny's front for the reel service, as
the service runs them: the route allowlist, the ASGI gate, the path checks
on the read-only media mount, the job's field filtering and pinning, and
the sweep. The module imports nothing of upstream's, so it is loaded here
as it is."""

import asyncio
import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location("reel_policy", REPO / "deploy" / "reel" / "policy.py")
policy = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(policy)

TASK = "0e19deb3-7e54-4a71-b103-2e394ae488be"


class RoutesTest(unittest.TestCase):
    OPEN = [
        ("POST", "/teeny/v1/reels"),
        ("GET", f"/api/v1/tasks/{TASK}"),
        ("DELETE", f"/api/v1/tasks/{TASK}"),
        ("GET", f"/api/v1/download/{TASK}/final-1.mp4"),
        ("HEAD", f"/api/v1/download/{TASK}/final-1.mp4"),
        ("GET", "/ping"),
        # A skit's cut (deploy/reel/skitcut.py): made, fetched, deleted.
        ("POST", "/teeny/v1/cuts"),
        ("GET", f"/teeny/v1/cuts/{TASK}/cut.mp4"),
        ("HEAD", f"/teeny/v1/cuts/{TASK}/cut.mp4"),
        ("GET", f"/teeny/v1/cuts/{TASK}/frame-0.jpg"),
        ("HEAD", f"/teeny/v1/cuts/{TASK}/frame-0.jpg"),
        ("DELETE", f"/teeny/v1/cuts/{TASK}"),
    ]

    def test_only_the_routes_hands_reel_uses_are_open(self):
        for method, path in self.OPEN:
            with self.subTest(method=method, path=path):
                self.assertTrue(policy.is_open(method, path))

    def test_every_other_method_on_an_open_route_is_shut(self):
        methods = ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS", "TRACE", "get")
        allowed = set(self.OPEN)
        for _, path in self.OPEN:
            for method in methods:
                if (method, path) in allowed:
                    continue
                with self.subTest(method=method, path=path):
                    self.assertFalse(policy.is_open(method, path))

    def test_lookalike_paths_are_shut(self):
        shut = [
            "/teeny/v1/reels/",
            "//teeny/v1/reels",
            "/teeny/v1//reels",
            "/teeny%2Fv1/reels",
            "/teeny/v1/reels?x=1",
            "/teeny/v1/../v1/reels",
            "/TEENY/v1/reels",
            "/api/v1/videos",
            "/api/v1/scripts",
            "/api/v1/terms",
            "/api/v1/musics",
            "/api/v1/video_materials",
            "/api/v1/tasks",
            f"/api/v1/tasks/{TASK}/",
            f"/api/v1/tasks/{TASK}/../../musics",
            f"/api/v1/tasks/{TASK.upper()}",
            "/api/v1/tasks/------------------------------------",
            f"/api/v1/download/{TASK}/final-2.mp4",
            f"/api/v1/download/{TASK}/../../config.toml",
            f"/api/v1/download/{TASK}%2Ffinal-1.mp4",
            f"/api/v1/download/{TASK}/final-1.mp4/",
            f"/api/v1/stream/{TASK}/final-1.mp4",
            f"/tasks/{TASK}/final-1.mp4",
            "/docs",
            "/openapi.json",
            "/",
            "/ping/",
            "",
            "/teeny/v1/cuts/",
            f"/teeny/v1/cuts/{TASK}/",
            f"/teeny/v1/cuts/{TASK}/label.txt",
            f"/teeny/v1/cuts/{TASK}/frame-10.jpg",
            f"/teeny/v1/cuts/{TASK}/../../tasks",
            f"/teeny/v1/cuts/{TASK.upper()}/cut.mp4",
            f"/teeny/v1/cuts/{TASK}%2Fcut.mp4",
            "/teeny/v1/cuts/------------------------------------/cut.mp4",
        ]
        for path in shut:
            for method in ("GET", "POST", "DELETE", "HEAD"):
                with self.subTest(method=method, path=path):
                    self.assertFalse(policy.is_open(method, path))


class GateTest(unittest.TestCase):
    """The ASGI middleware, around a stub app that records what reached it."""

    def call(self, method, path, status=200):
        reached, deleted, sent = [], [], []

        async def app(scope, receive, send):
            reached.append((scope["method"], scope["path"]))
            await send({"type": "http.response.start", "status": status, "headers": []})
            await send({"type": "http.response.body", "body": b"{}"})

        async def receive():
            return {"type": "http.request", "body": b""}

        async def send(message):
            sent.append(message)

        gate = policy.Gate(app, deleted.append)
        asyncio.run(gate({"type": "http", "method": method, "path": path}, receive, send))
        return reached, deleted, sent

    def test_a_shut_route_never_reaches_upstream(self):
        reached, deleted, sent = self.call("POST", "/api/v1/scripts")
        self.assertEqual(reached, [])
        self.assertEqual(sent[0]["status"], 403)
        self.assertEqual(json.loads(sent[1]["body"])["message"], "not served here")

    def test_an_open_route_passes_through(self):
        reached, deleted, sent = self.call("GET", f"/api/v1/tasks/{TASK}")
        self.assertEqual(reached, [("GET", f"/api/v1/tasks/{TASK}")])
        self.assertEqual(sent[0]["status"], 200)
        self.assertEqual(deleted, [])

    def test_a_deleted_task_has_its_staged_files_removed(self):
        self.assertEqual(self.call("DELETE", f"/api/v1/tasks/{TASK}")[1], [TASK])
        # A refused deletion (still running) removes nothing.
        self.assertEqual(self.call("DELETE", f"/api/v1/tasks/{TASK}", status=409)[1], [])

    def test_other_scopes_pass(self):
        seen = []

        async def app(scope, receive, send):
            seen.append(scope["type"])

        asyncio.run(policy.Gate(app)({"type": "lifespan"}, None, None))
        self.assertEqual(seen, ["lifespan"])


class SourceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "teeny"
        (self.root / "media").mkdir(parents=True)
        (self.root / "assets" / "tiny").mkdir(parents=True)
        (self.root / "media" / "image-1.png").write_bytes(b"png")
        (self.root / "assets" / "tiny" / "love.jpg").write_bytes(b"jpg")
        (Path(self.tmp.name) / "secret.png").write_bytes(b"key")

    def tearDown(self):
        self.tmp.cleanup()

    def test_media_and_assets_resolve(self):
        self.assertEqual(policy.resolve_source(str(self.root), "media/image-1.png"), str((self.root / "media" / "image-1.png").resolve()))
        self.assertEqual(policy.resolve_source(str(self.root), "assets/tiny/love.jpg"), str((self.root / "assets" / "tiny" / "love.jpg").resolve()))

    def test_anything_else_is_refused(self):
        (self.root / "media" / "out.png").symlink_to(Path(self.tmp.name) / "secret.png")
        (self.root / "media" / "cross.png").symlink_to(self.root / "assets" / "tiny" / "love.jpg")
        for path in (
            "media/../../secret.png",
            "media/../assets/tiny/love.jpg",
            "media/out.png",
            "media/cross.png",
            "assets/../media/image-1.png",
            "../secret.png",
            "/etc/passwd",
            str(self.root / "media" / "image-1.png"),
            "secret.png",
            "media/",
            "media/missing.png",
            "media/image-1.png\x00.mp3",
            None,
            5,
        ):
            with self.subTest(path=path), self.assertRaises(policy.JobError):
                policy.resolve_source(str(self.root), path)

    def test_a_file_over_the_size_cap_is_refused(self):
        with self.assertRaises(policy.JobError):
            policy.resolve_source(str(self.root), "media/image-1.png", limit=2)


class JobTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.fonts = self.tmp.name
        Path(self.fonts, "BeVietnamPro-Bold.ttf").write_bytes(b"font")

    def tearDown(self):
        self.tmp.cleanup()

    def job(self, **extra):
        body = {
            "video_subject": "rent",
            "video_script": "I pay for my own server.",
            "video_materials": [{"provider": "local", "url": "media/image-1.png"}],
            "voice_name": "en-US-AnaNeural-Female",
            "font_name": "BeVietnamPro-Bold.ttf",
            "font_size": 88,
            "text_fore_color": "#FFE14D",
            "stroke_color": "#000000",
            "bgm_type": "",
            "bgm_file": "",
        }
        body.update(extra)
        return body

    def test_a_good_job_is_pinned(self):
        out = policy.check_job(self.job(), self.fonts)
        for key, value in policy.PINNED.items():
            self.assertEqual(out["fields"][key], value)
        self.assertEqual(out["materials"], ["media/image-1.png"])
        self.assertIsNone(out["bed"])
        self.assertNotIn("audio", out["fields"])

    def test_an_unknown_field_is_refused(self):
        for field in ("upload_post_enabled", "custom_audio_file", "video_terms_extra", "pexels_api_keys"):
            with self.subTest(field=field), self.assertRaises(policy.JobError) as no:
                policy.check_job(self.job(**{field: True}), self.fonts)
            self.assertIn("not taken here", str(no.exception))

    def test_a_pinned_value_cannot_be_overridden(self):
        for key, value in (("video_source", "pexels"), ("video_concat_mode", "random"), ("video_count", 3), ("match_materials_to_script", True), ("video_terms", ["cats"]), ("video_script_prompt", "write one")):
            with self.subTest(key=key), self.assertRaises(policy.JobError):
                policy.check_job(self.job(**{key: value}), self.fonts)

    def test_the_music_and_voice_rules(self):
        bad = [
            {"bgm_type": "random"},
            {"bgm_type": "sonilo"},
            {"bgm_type": "custom", "bgm_file": ""},
            {"bgm_type": "", "bgm_file": "media/bed.mp3"},
            {"bgm_type": "custom", "bgm_file": "media/bed.mp4"},
            {"audio": "media/track.txt"},
            {"voice_name": "elevenlabs:abc:Rachel"},
            {"voice_name": "en-US-AnaNeural-Female; rm"},
        ]
        for extra in bad:
            with self.subTest(extra=extra), self.assertRaises(policy.JobError):
                policy.check_job(self.job(**extra), self.fonts)
        out = policy.check_job(self.job(bgm_type="custom", bgm_file="media/bed.mp3", audio="media/track.mp3"), self.fonts)
        self.assertEqual((out["bed"], out["audio"]), ("media/bed.mp3", "media/track.mp3"))

    def test_the_sizes_the_font_and_the_colours(self):
        bad = [
            {"video_script": ""},
            {"video_script": "x" * (policy.MAX_SCRIPT + 1)},
            {"video_subject": "x" * (policy.MAX_SUBJECT + 1)},
            {"video_materials": []},
            {"video_materials": [{"provider": "local", "url": "media/a.png"}] * (policy.MAX_MATERIALS + 1)},
            {"video_materials": [{"provider": "pexels", "url": "https://example.com/a.mp4"}]},
            {"video_materials": [{"provider": "local", "url": "media/a.png", "duration": 5}]},
            {"font_size": 2000},
            {"font_size": True},
            {"font_name": "../../config.toml"},
            {"font_name": "STHeitiMedium.ttc"},
            {"text_fore_color": "yellow"},
            {"stroke_color": "#FFF"},
            {"text_background_color": "red"},
        ]
        for extra in bad:
            with self.subTest(extra=str(extra)[:60]), self.assertRaises(policy.JobError):
                policy.check_job(self.job(**extra), self.fonts)
        for body in ([], "job", None):
            with self.assertRaises(policy.JobError):
                policy.check_job(body, self.fonts)

    def test_the_voice_must_match_whole(self):
        self.assertIsNone(policy.EDGE_VOICE.fullmatch("en-US-AnaNeural-Female-x"))
        with self.assertRaises(policy.JobError):
            policy.check_job(self.job(voice_name="en-US-AnaNeural-Female-x"), self.fonts)

    def test_validation_errors_become_plain_text(self):
        class Unserialisable:
            pass

        text = policy.errors_text([{"loc": ("body", "font_size"), "msg": "too big", "ctx": {"x": Unserialisable()}}])
        self.assertEqual(text, "body.font_size: too big")
        json.dumps({"message": text})


class SweepTest(unittest.TestCase):
    def test_only_leftovers_older_than_the_limit_go(self):
        entries = [("old", 0.0), ("new", 990.0), ("edge", 400.0)]
        self.assertEqual(policy.stale(entries, 1000.0, 600.0), ["old"])
        # At start every leftover goes.
        self.assertEqual(sorted(policy.stale(entries, 1000.0, -1)), ["edge", "new", "old"])


class ServeUsesPolicyTest(unittest.TestCase):
    """serve.py wires the policy in; checked from its source, since importing
    it needs upstream's packages."""

    def test_the_front_runs_behind_the_gate_and_stages_off_the_loop(self):
        source = (REPO / "deploy" / "reel" / "serve.py").read_text()
        self.assertIn("uvicorn.run(policy.Gate(app, _clean)", source)
        self.assertIn("await run_in_threadpool(_stage, task_id, checked)", source)
        self.assertIn("policy.check_job(", source)
        self.assertIn("sweep(-1)", source)
        self.assertIn("policy.MAX_BODY_BYTES", source)
        self.assertIn("COPY --from=teeny serve.py policy.py skitcut.py /opt/teeny/", (REPO / "deploy" / "reel.Dockerfile").read_text())


if __name__ == "__main__":
    unittest.main()
