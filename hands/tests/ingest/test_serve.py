"""deploy/ingest/serve.py with yt-dlp, ffmpeg and ffprobe faked: how a fetch
is put together (probe, download, excerpt, scenes, shots, audio, frames),
what it refuses, the one-at-a-time rule, the sweep, and the routes over a
real local socket. No test needs yt-dlp, ffmpeg or the network."""

import contextlib
import http.client
import io
import socket
import importlib.util
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "deploy" / "ingest"))
SPEC = importlib.util.spec_from_file_location("ingest_serve", REPO / "deploy" / "ingest" / "serve.py")
serve = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(serve)

URL = "https://www.tiktok.com/@someone/video/7300000000000000000"
INFO = {"id": "7300000000000000000", "title": "a dance", "uploader": "someone", "duration": 40.0, "view_count": 1000,
        "age_limit": 0, "timestamp": 1_795_000_000, "webpage_url": URL, "width": 1080, "height": 1920}


def probe_json(seconds, audio=True, width=720, height=1280, fps="30/1"):
    streams = [{"codec_type": "video", "width": width, "height": height, "avg_frame_rate": fps, "r_frame_rate": fps}]
    if audio:
        streams.append({"codec_type": "audio"})
    return json.dumps({"streams": streams, "format": {"duration": str(seconds)}})


class Fake:
    """The three tools. `cuts` are the scene cuts ffmpeg reports (seconds
    into the excerpt); `info` yt-dlp's answer; `audio` whether the clip has
    sound. Everything it is asked is kept in `calls`."""

    def __init__(self, info=None, cuts=(), audio=True, fail_download=None, fps="30/1"):
        self.info = dict(INFO if info is None else info)
        self.cuts, self.audio, self.fail_download, self.fps = list(cuts), audio, fail_download, fps
        self.calls = []
        self.listing = {"entries": [{"url": "https://www.tiktok.com/@someone/video/1", "id": "1", "title": "one",
                                     "view_count": 5}, {"url": "https://evil.example/x", "id": "2"}]}
        self.excerpt = None

    def __call__(self, argv, timeout):
        self.calls.append(list(argv))
        tool = argv[0]
        if tool == "yt-dlp":
            if "--flat-playlist" in argv:
                return 0, json.dumps(self.listing), ""
            if "--dump-single-json" in argv:
                if self.fail_download:
                    return 1, "", self.fail_download
                return 0, json.dumps(self.info), ""
            if self.fail_download:
                return 1, "", self.fail_download
            Path(argv[argv.index("-o") + 1].replace("%(ext)s", "mp4")).write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"d" * 50)
            return 0, "", ""
        if tool == "ffprobe":
            name = os.path.basename(argv[-1])
            if name.startswith("dl."):
                return 0, probe_json(self.info["duration"], self.audio, fps=self.fps), ""
            return 0, probe_json(self.excerpt if self.excerpt is not None else 20.0, self.audio, fps=self.fps), ""
        if tool == "ffmpeg":
            if argv[-3:] == ["-f", "null", "-"]:
                lines = "".join(f"[Parsed_showinfo_1 @ 0x1] n: 0 pts: 1 pts_time:{c}   pos: 9\n" for c in self.cuts)
                return 0, "", lines
            Path(argv[-1]).write_bytes(b"\xff\xd8\xff\xe0jpeg" if argv[-1].endswith(".jpg") else b"\x00\x00\x00\x18ftypM4A ")
            return 0, "", ""
        raise AssertionError(argv)


class ServiceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.fake = Fake()
        self.service = serve.Service(self.tmp.name, runner=self.fake)

    def fetches(self):
        return sorted(os.listdir(os.path.join(self.tmp.name, "fetches")))

    def test_a_fetch_is_an_excerpt_its_shots_their_frames_and_the_sound(self):
        self.fake.cuts = [7.0, 14.0]
        self.fake.excerpt = 20.0
        got = self.service.fetch({"url": URL, "start": 5, "max_seconds": 20})
        self.assertRegex(got["fetch_id"], r"[0-9a-f]{32}")
        self.assertEqual(got["source"]["video_id"], "7300000000000000000")
        self.assertEqual(got["source"]["source"], "tiktok")
        self.assertEqual(got["excerpt"], {"start": 5.0, "seconds": 20.0})
        self.assertEqual(got["aspect"], "9:16")
        self.assertEqual(got["fps"], 30.0)
        self.assertEqual([(s["n"], s["start"], s["seconds"], s["frame"]) for s in got["shots"]],
                         [(1, 0.0, 7.0, "shot-1.jpg"), (2, 7.0, 7.0, "shot-2.jpg"), (3, 14.0, 6.0, "shot-3.jpg")])
        self.assertEqual(got["files"], ["source.mp4", "audio.m4a", "shot-1.jpg", "shot-2.jpg", "shot-3.jpg"])
        folder = Path(self.tmp.name, "fetches", got["fetch_id"])
        self.assertEqual(sorted(p.name for p in folder.iterdir()), sorted(got["files"]))

    def test_the_download_is_the_one_yt_dlp_is_given_with_the_height_asked(self):
        self.service.fetch({"url": URL, "max_height": 480})
        download = [c for c in self.fake.calls if c[0] == "yt-dlp" and "--dump-single-json" not in c][0]
        self.assertEqual(download[-2:], ["--", URL])
        self.assertIn("height<=480", download[download.index("-f") + 1])
        self.assertTrue(download[download.index("-o") + 1].startswith(self.tmp.name))

    def test_a_clip_without_sound_has_no_audio_file(self):
        self.fake.audio = False
        got = self.service.fetch({"url": URL})
        self.assertNotIn("audio.m4a", got["files"])
        self.assertFalse(any("-vn" in c for c in self.fake.calls))

    def test_the_audio_is_exactly_the_excerpt(self):
        self.fake.cuts = [8.0]
        self.fake.excerpt = 30.0
        got = self.service.fetch({"url": URL})
        audio = [c for c in self.fake.calls if "-vn" in c][0]
        self.assertEqual(audio[audio.index("-t") + 1], f"{got['excerpt']['seconds']:.3f}")

    def test_more_shots_than_asked_shorten_the_excerpt_and_the_file_is_cut_again_to_match(self):
        self.fake.cuts = [5.0, 10.0, 15.0, 20.0, 25.0]
        self.fake.excerpt = 30.0
        got = self.service.fetch({"url": URL, "max_shots": 3})
        self.assertEqual(got["excerpt"]["seconds"], 15.0)
        self.assertEqual(len(got["shots"]), 3)
        cuts = [c for c in self.fake.calls if c[0] == "ffmpeg" and "-ss" in c and "-t" in c and c[-1].endswith("source.mp4")]
        self.assertEqual(cuts[-1][cuts[-1].index("-t") + 1], "15.000")
        self.assertEqual(self.fetches(), [got["fetch_id"]])

    def test_the_excerpt_stops_at_the_end_of_the_video(self):
        self.fake.excerpt = 10.0
        got = self.service.fetch({"url": URL, "start": 30, "max_seconds": 30})
        first = [c for c in self.fake.calls if c[0] == "ffmpeg" and "-ss" in c and c[-1].endswith("ex.mp4")][0]
        self.assertEqual(first[first.index("-t") + 1], "10.000")
        self.assertEqual(got["excerpt"], {"start": 30.0, "seconds": 10.0})

    def test_the_refusals_are_422_and_leave_nothing_behind(self):
        for info, why in [({**INFO, "is_live": True}, "live"), ({**INFO, "age_limit": 18}, "age"),
                          ({**INFO, "duration": 601}, "600"), ({**INFO, "duration": 20.0}, "start")]:
            with self.subTest(why=why):
                fake = Fake(info=info)
                service = serve.Service(self.tmp.name, runner=fake)
                with self.assertRaises(serve.Failure) as caught:
                    service.fetch({"url": URL, "start": 25})
                self.assertEqual(caught.exception.status, 422)
                self.assertFalse(any(c[0] == "ffmpeg" for c in fake.calls))
                self.assertEqual(os.listdir(os.path.join(self.tmp.name, "fetches")), [])

    def test_a_download_that_fails_says_why_and_leaves_nothing(self):
        self.fake.fail_download = "ERROR: [youtube] x: Sign in to confirm you’re not a bot"
        with self.assertRaises(serve.Failure) as caught:
            self.service.fetch({"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"})
        self.assertEqual(caught.exception.status, 422)
        self.assertIn("Sign in to confirm", caught.exception.message)
        self.assertEqual(os.listdir(os.path.join(self.tmp.name, "fetches")), [])

    def test_a_body_that_is_not_allowed_is_400_and_nothing_runs(self):
        for body in ({"url": "http://www.tiktok.com/@a/video/1"}, {"url": "https://evil.example/x"}, {}, [], {"url": URL, "x": 1}):
            with self.subTest(body=body):
                with self.assertRaises(serve.Failure) as caught:
                    self.service.fetch(body)
                self.assertEqual(caught.exception.status, 400)
        self.assertEqual(self.fake.calls, [])

    def test_the_proxy_goes_to_yt_dlp_and_never_back_in_an_error(self):
        proxy = "http://user:hunter2@proxy.example:3128"
        fake = Fake(fail_download=f"ERROR: unable to connect to proxy {proxy}: refused")
        service = serve.Service(self.tmp.name, proxy=proxy, runner=fake)
        with self.assertRaises(serve.Failure) as caught:
            service.probe({"url": URL})
        self.assertIn(proxy, fake.calls[0])
        self.assertNotIn("hunter2", caught.exception.message)
        self.assertNotIn("proxy.example", caught.exception.message)
        ok = serve.Service(self.tmp.name, runner=Fake())
        ok.probe({"url": URL})

    def test_a_download_over_the_cap_is_refused_whatever_yt_dlp_let_through(self):
        with mock.patch.object(serve.policy, "MAX_DOWNLOAD_BYTES", 20):
            with self.assertRaises(serve.Failure) as caught:
                self.service.fetch({"url": URL})
        self.assertEqual(caught.exception.status, 422)
        self.assertIn("too big", caught.exception.message)
        self.assertEqual(os.listdir(os.path.join(self.tmp.name, "fetches")), [])
        self.assertFalse(any(c[0] == "ffmpeg" for c in self.fake.calls))

    def test_a_page_that_is_a_list_of_videos_is_not_a_probe(self):
        self.fake.info = {"_type": "playlist", "id": "someone", "duration": 30, "entries": []}
        with self.assertRaises(serve.Failure) as caught:
            self.service.probe({"url": "https://www.tiktok.com/@someone"})
        self.assertEqual(caught.exception.status, 422)

    def test_a_malformed_proxy_is_ignored_and_said_once_without_its_value(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            service = serve.Service(self.tmp.name, proxy="--exec=id secret", runner=self.fake)
        self.assertIsNone(service.proxy)
        self.assertEqual(len(err.getvalue().strip().splitlines()), 1)
        self.assertIn("INGEST_PROXY_URL", err.getvalue())
        self.assertNotIn("secret", err.getvalue())
        service.probe({"url": URL})
        self.assertNotIn("--proxy", self.fake.calls[0])
        quiet = io.StringIO()
        with contextlib.redirect_stderr(quiet):
            serve.Service(self.tmp.name, proxy="http://u:p@h:1", runner=self.fake)
            serve.Service(self.tmp.name, proxy=None, runner=self.fake)
        self.assertEqual(quiet.getvalue(), "")

    def test_a_second_job_while_one_runs_is_429(self):
        started, release = threading.Event(), threading.Event()
        inner = self.fake

        def slow(argv, timeout):
            started.set()
            release.wait(5)
            return inner(argv, timeout)

        service = serve.Service(self.tmp.name, runner=slow)
        first = threading.Thread(target=lambda: service.probe({"url": URL}))
        first.start()
        self.assertTrue(started.wait(5))
        for call in (service.probe, service.fetch, service.listing):
            with self.assertRaises(serve.Failure) as caught:
                call({"url": URL})
            self.assertEqual(caught.exception.status, 429)
        release.set()
        first.join(5)
        self.assertEqual(service.probe({"url": URL})["video_id"], "7300000000000000000")

    def test_a_probe_is_the_probe_fields(self):
        got = self.service.probe({"url": URL})
        self.assertEqual((got["source"], got["video_id"], got["seconds"], got["live"]), ("tiktok", "7300000000000000000", 40.0, False))
        self.assertEqual(got["published_at"], "2026-11-18T11:06:40Z")

    def test_a_listing_keeps_the_entries_on_the_list(self):
        got = self.service.listing({"url": "https://www.tiktok.com/@someone", "max": 5})
        self.assertEqual([e["video_id"] for e in got["entries"]], ["1"])
        argv = self.fake.calls[0]
        self.assertEqual(argv[argv.index("--playlist-end") + 1], "5")

    def test_a_listing_that_is_one_video_is_unusable(self):
        self.fake.listing = {"id": "1"}
        with self.assertRaises(serve.Failure) as caught:
            self.service.listing({"url": "https://www.tiktok.com/@someone"})
        self.assertEqual(caught.exception.status, 422)

    def test_files_are_served_by_their_exact_names_only(self):
        got = self.service.fetch({"url": URL})
        path = self.service.file_path(got["fetch_id"], "source.mp4")
        self.assertTrue(path.is_file())
        for fetch_id, name in [(got["fetch_id"], "../x"), (got["fetch_id"], "dl.mp4"), ("../..", "source.mp4"),
                               ("0" * 32, "source.mp4"), (got["fetch_id"], "shot-9.jpg")]:
            with self.subTest(name=name):
                with self.assertRaises(serve.Failure) as caught:
                    self.service.file_path(fetch_id, name)
                self.assertEqual(caught.exception.status, 404)

    def test_a_symlink_is_not_served(self):
        got = self.service.fetch({"url": URL})
        link = Path(self.tmp.name, "fetches", got["fetch_id"], "shot-9.jpg")
        link.symlink_to("/etc/hostname")
        with self.assertRaises(serve.Failure):
            self.service.file_path(got["fetch_id"], "shot-9.jpg")

    def test_a_delete_removes_the_fetch_and_a_second_is_404(self):
        got = self.service.fetch({"url": URL})
        self.service.delete(got["fetch_id"])
        self.assertEqual(self.fetches(), [])
        with self.assertRaises(serve.Failure) as caught:
            self.service.delete(got["fetch_id"])
        self.assertEqual(caught.exception.status, 404)

    def test_the_sweep_removes_fetches_older_than_the_limit(self):
        old = self.service.fetch({"url": URL})["fetch_id"]
        new = self.service.fetch({"url": URL})["fetch_id"]
        long_ago = time.time() - 7200
        os.utime(os.path.join(self.tmp.name, "fetches", old), (long_ago, long_ago))
        self.service.sweep(3600)
        self.assertEqual(self.fetches(), [new])
        self.service.sweep(-1)
        self.assertEqual(self.fetches(), [])


class HttpTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.fake = Fake(cuts=[7.0])
        self.fake.excerpt = 14.0
        self.service = serve.Service(self.tmp.name, runner=self.fake)
        self.server = serve.make_server(self.service, "127.0.0.1", 0)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def call(self, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        data = json.dumps(body).encode() if body is not None else None
        conn.request(method, path, body=data, headers={"content-type": "application/json", **(headers or {})})
        response = conn.getresponse()
        raw = response.read()
        conn.close()
        return response.status, response.getheader("content-type"), raw

    def test_a_client_that_goes_quiet_is_dropped(self):
        server = serve.make_server(self.service, "127.0.0.1", 0, timeout=0.3)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.assertEqual(server.RequestHandlerClass.timeout, 0.3)
        with socket.create_connection(("127.0.0.1", server.server_address[1]), timeout=5) as conn:
            conn.sendall(b"POST /teeny/v1/probes HTTP/1.1\r\ncontent-length: 50\r\n\r\n{")
            conn.settimeout(5)
            started = time.time()
            conn.recv(4096)  # the answer or the close, not a hang
            self.assertLess(time.time() - started, 3)

    def test_the_default_socket_timeout_is_thirty_seconds(self):
        self.assertEqual(self.server.RequestHandlerClass.timeout, 30)

    def test_ping(self):
        self.assertEqual(self.call("GET", "/ping")[0], 200)

    def test_the_whole_story_over_the_wire(self):
        status, _, raw = self.call("POST", "/teeny/v1/fetches", {"url": URL})
        self.assertEqual(status, 200)
        data = json.loads(raw)["data"]
        status, kind, raw = self.call("GET", f"/teeny/v1/fetches/{data['fetch_id']}/shot-1.jpg")
        self.assertEqual((status, kind), (200, "image/jpeg"))
        self.assertTrue(raw.startswith(b"\xff\xd8\xff"))
        status, kind, _ = self.call("GET", f"/teeny/v1/fetches/{data['fetch_id']}/source.mp4")
        self.assertEqual((status, kind), (200, "video/mp4"))
        status, kind, _ = self.call("GET", f"/teeny/v1/fetches/{data['fetch_id']}/audio.m4a")
        self.assertEqual((status, kind), (200, "audio/mp4"))
        self.assertEqual(self.call("DELETE", f"/teeny/v1/fetches/{data['fetch_id']}")[0], 200)
        self.assertEqual(self.call("GET", f"/teeny/v1/fetches/{data['fetch_id']}/shot-1.jpg")[0], 404)

    def test_errors_are_the_envelope(self):
        status, kind, raw = self.call("POST", "/teeny/v1/probes", {"url": "https://evil.example/x"})
        self.assertEqual((status, kind), (400, "application/json"))
        self.assertEqual(json.loads(raw)["status"], 400)
        self.assertIn("message", json.loads(raw))
        self.assertEqual(self.call("GET", "/nope")[0], 404)
        self.assertEqual(self.call("PUT", "/teeny/v1/probes", {"url": URL})[0], 404)

    def test_a_body_that_is_not_json_or_has_no_length_or_is_big_is_400(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        conn.request("POST", "/teeny/v1/probes", body=b"not json", headers={"content-type": "application/json"})
        self.assertEqual(conn.getresponse().status, 400)
        conn.close()
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        conn.request("POST", "/teeny/v1/probes", body=b"{" + b" " * 20000 + b"}")
        self.assertEqual(conn.getresponse().status, 400)
        conn.close()

    def test_a_listing_and_a_probe_answer_in_the_envelope(self):
        status, _, raw = self.call("POST", "/teeny/v1/listings", {"url": "https://www.tiktok.com/@someone"})
        self.assertEqual(json.loads(raw)["data"]["entries"][0]["video_id"], "1")
        status, _, raw = self.call("POST", "/teeny/v1/probes", {"url": URL})
        self.assertEqual((status, json.loads(raw)["status"]), (200, 200))


if __name__ == "__main__":
    unittest.main()
