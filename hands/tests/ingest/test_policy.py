"""deploy/ingest/policy.py, the rules of the `ingest` service: which URLs
reach yt-dlp, the argv it is run with, which videos it refuses, the bodies
and routes it takes, and the names it serves. The module imports nothing
outside the standard library, so it is loaded here as it runs."""

import importlib.util
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location("ingest_policy", REPO / "deploy" / "ingest" / "policy.py")
policy = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(policy)

FETCH_ID = "0123456789abcdef0123456789abcdef"


class UrlTest(unittest.TestCase):
    GOOD = {
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ": "youtube",
        "https://youtube.com/watch?v=dQw4w9WgXcQ": "youtube",
        "https://m.youtube.com/shorts/abcDEF12345": "youtube",
        "https://youtu.be/dQw4w9WgXcQ": "youtube",
        "https://www.tiktok.com/@someone/video/7300000000000000000": "tiktok",
        "https://tiktok.com/@someone": "tiktok",
        "https://vm.tiktok.com/ZMabc123/": "tiktok",
        "https://www.instagram.com/reel/Cabc123/": "instagram",
        "https://instagram.com/reel/Cabc123/": "instagram",
        "HTTPS://WWW.YOUTUBE.COM/watch?v=dQw4w9WgXcQ": "youtube",
    }

    def test_the_allowed_hosts_over_https_pass_with_their_source(self):
        for url, source in self.GOOD.items():
            with self.subTest(url=url):
                checked = policy.check_url(url)
                self.assertEqual(checked["source"], source)
                self.assertTrue(checked["url"].startswith("https://"))

    def test_the_url_is_returned_without_its_fragment(self):
        self.assertEqual(policy.check_url("https://youtu.be/abc#t=10")["url"], "https://youtu.be/abc")

    BAD = [
        "http://www.youtube.com/watch?v=abc",  # not https
        "ftp://www.youtube.com/x",
        "//www.youtube.com/watch?v=abc",
        "https://evil.example/watch?v=abc",
        "https://youtube.com.evil.example/watch?v=abc",
        "https://evil-youtube.com/watch?v=abc",
        "https://notyoutube.com/x",
        "https://music.youtube.com/watch?v=abc",  # not on the list
        "https://user@www.youtube.com/watch?v=abc",  # userinfo
        "https://user:pw@www.youtube.com/watch?v=abc",
        "https://www.youtube.com@evil.example/watch?v=abc",
        "https://www.youtube.com:443/watch?v=abc",  # a port, even the default
        "https://www.youtube.com:8080/watch?v=abc",
        "https://127.0.0.1/x",
        "https://[::1]/x",
        "https://169.254.169.254/latest/meta-data",
        "https://2130706433/x",
        "https://localhost/x",
        "https://www.yоutube.com/watch?v=abc",  # a Cyrillic o
        "https://www.youtube.com。evil.example/x",  # an ideographic full stop
        "https://www.youtu​e.com/watch?v=abc",  # a zero-width space
        "https://www.youtube.com/watch?v=abc def",  # whitespace
        "https://www.youtube.com/watch?v=abc\nx",
        "https://www.youtube.com\\@evil.example/",
        "https://www.youtube.com./watch?v=abc",  # the rooted form is not on the list
        "-https://www.youtube.com/x",
        "--exec=id",
        "file:///etc/passwd",
        "",
        "   ",
        None,
        42,
        ["https://youtu.be/abc"],
        "https://youtu.be/" + "a" * 600,
    ]

    def test_everything_else_is_refused(self):
        for url in self.BAD:
            with self.subTest(url=url):
                with self.assertRaises(policy.RequestError):
                    policy.check_url(url)


class ArgvTest(unittest.TestCase):
    URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"

    def every(self):
        return {
            "probe": policy.probe_argv(self.URL),
            "listing": policy.listing_argv("https://www.tiktok.com/@someone", 10),
            "download": policy.download_argv(self.URL, "/data/f/dl.%(ext)s", 720),
        }

    def test_the_url_is_the_last_word_after_a_double_dash(self):
        for name, argv in self.every().items():
            with self.subTest(name=name):
                self.assertEqual(argv[-2], "--")
                self.assertTrue(argv[-1].startswith("https://"))
                self.assertEqual(argv.count("--"), 1)

    def test_nothing_in_it_is_a_shell_or_an_option_made_of_the_url(self):
        for name, argv in self.every().items():
            with self.subTest(name=name):
                self.assertEqual(argv[0], "yt-dlp")
                self.assertFalse({"sh", "bash", "-c", "/bin/sh"} & set(argv))
                url = argv[-1]
                for word in argv[:-2]:
                    self.assertNotIn(url, word)
                    self.assertFalse(word.startswith("https://") and word != "https://x")

    def test_the_safety_options_are_always_there(self):
        for name, argv in self.every().items():
            with self.subTest(name=name):
                for option in ("--no-exec", "--ignore-config", "--no-cache-dir", "--socket-timeout"):
                    self.assertIn(option, argv)

    def test_a_single_video_never_becomes_a_playlist_but_a_listing_does(self):
        self.assertIn("--no-playlist", policy.probe_argv(self.URL))
        self.assertIn("--no-playlist", policy.download_argv(self.URL, "/d/dl.%(ext)s", 720))
        listing = policy.listing_argv("https://www.tiktok.com/@someone", 10)
        self.assertNotIn("--no-playlist", listing)
        self.assertIn("--flat-playlist", listing)
        self.assertEqual(listing[listing.index("--playlist-end") + 1], "10")

    def test_a_download_is_size_capped_and_its_format_capped_at_the_height(self):
        argv = policy.download_argv(self.URL, "/d/dl.%(ext)s", 480)
        self.assertEqual(argv[argv.index("--max-filesize") + 1], policy.MAX_DOWNLOAD)
        self.assertIn("height<=480", argv[argv.index("-f") + 1])
        self.assertEqual(argv[argv.index("-o") + 1], "/d/dl.%(ext)s")

    def test_the_proxy_is_passed_only_when_one_is_set(self):
        self.assertNotIn("--proxy", policy.probe_argv(self.URL))
        argv = policy.probe_argv(self.URL, proxy="http://proxy.example:3128")
        self.assertEqual(argv[argv.index("--proxy") + 1], "http://proxy.example:3128")
        self.assertEqual(argv[-2:], ["--", self.URL])

    def test_a_url_that_looks_like_an_option_is_refused_before_it_gets_here(self):
        for url in ("--exec=id", "-o/etc/x", "https://youtu.be/x --exec id"):
            with self.subTest(url=url):
                with self.assertRaises(policy.RequestError):
                    policy.check_url(url)
        with self.assertRaises(policy.RequestError):
            policy.probe_argv("--exec=id")

    def test_a_proxy_that_is_not_a_url_is_dropped(self):
        for proxy in ("--exec=id", "file:///x", "http://h:1 --x", ""):
            with self.subTest(proxy=proxy):
                self.assertNotIn("--proxy", policy.probe_argv(self.URL, proxy=proxy))

    def test_the_error_text_never_shows_the_proxy(self):
        text = policy.scrub("ERROR: unable to connect to http://user:secret@proxy.example:3128: refused", "http://user:secret@proxy.example:3128")
        self.assertNotIn("secret", text)
        self.assertNotIn("proxy.example", text)


class RefusalTest(unittest.TestCase):
    def info(self, **more):
        return {"id": "abc", "title": "t", "duration": 30, "age_limit": 0, "is_live": False, **more}

    def test_an_ordinary_short_video_is_taken(self):
        self.assertIsNone(policy.refusal(self.info()))

    def test_a_live_stream_an_age_limit_or_a_long_video_is_refused(self):
        for more in ({"is_live": True}, {"live_status": "is_live"}, {"live_status": "is_upcoming"},
                     {"age_limit": 18}, {"age_limit": 1}, {"duration": 601}, {"duration": None}, {"duration": 0},
                     {"duration": "long"}):
            with self.subTest(more=more):
                self.assertIsInstance(policy.refusal(self.info(**more)), str)

    def test_ten_minutes_exactly_is_the_longest(self):
        self.assertIsNone(policy.refusal(self.info(duration=600)))


class ProbeFieldsTest(unittest.TestCase):
    def test_a_youtube_answer_becomes_the_probe(self):
        info = {"id": "dQw4w9WgXcQ", "title": "T\x00itle\n", "uploader": "Chan", "duration": 212.4, "view_count": 12,
                "age_limit": 0, "is_live": False, "categories": ["Music"], "timestamp": 1_795_000_000,
                "width": 1920, "height": 1080, "webpage_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"}
        got = policy.probe_fields(info, "youtube", "https://youtu.be/dQw4w9WgXcQ")
        self.assertEqual(got, {"source": "youtube", "video_id": "dQw4w9WgXcQ",
                               "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ", "title": "Title",
                               "uploader": "Chan", "seconds": 212.4, "views": 12, "age_limit": 0, "live": False,
                               "category": "Music", "published_at": "2026-11-17T00:26:40Z", "width": 1920,
                               "height": 1080})

    def test_missing_fields_are_null_and_a_foreign_page_url_is_not_taken(self):
        got = policy.probe_fields({"id": "7300", "webpage_url": "https://evil.example/x"}, "tiktok",
                                  "https://vm.tiktok.com/ZM1/")
        self.assertEqual(got["url"], "https://vm.tiktok.com/ZM1/")
        for key in ("title", "uploader", "seconds", "views", "category", "published_at", "width", "height"):
            self.assertIsNone(got[key])
        self.assertEqual((got["age_limit"], got["live"]), (0, False))

    def test_an_id_that_is_not_a_plain_id_is_refused(self):
        for bad in ("../x", "a b", "", None, "a" * 80, 12):
            with self.subTest(bad=bad):
                with self.assertRaises(policy.RequestError):
                    policy.probe_fields({"id": bad}, "youtube", "https://youtu.be/x")

    def test_an_upload_date_stands_in_for_a_timestamp(self):
        got = policy.probe_fields({"id": "x", "upload_date": "20261117"}, "youtube", "https://youtu.be/x")
        self.assertEqual(got["published_at"], "2026-11-17T00:00:00Z")


class ListingEntryTest(unittest.TestCase):
    def test_a_flat_entry_becomes_a_listing_entry(self):
        got = policy.listing_entry({"url": "https://www.tiktok.com/@someone/video/7300", "id": "7300",
                                    "title": "hi", "uploader": "someone", "duration": 12, "view_count": 99,
                                    "timestamp": 1_795_000_000})
        self.assertEqual(got, {"url": "https://www.tiktok.com/@someone/video/7300", "video_id": "7300",
                               "title": "hi", "uploader": "someone", "seconds": 12, "views": 99,
                               "published_at": "2026-11-17T00:26:40Z"})

    def test_an_entry_off_the_list_or_without_an_id_is_dropped(self):
        self.assertIsNone(policy.listing_entry({"url": "https://evil.example/v/1", "id": "1"}))
        self.assertIsNone(policy.listing_entry({"url": "http://www.tiktok.com/@a/video/1", "id": "1"}))
        self.assertIsNone(policy.listing_entry({"url": "https://www.tiktok.com/@a/video/1"}))
        self.assertIsNone(policy.listing_entry("nope"))

    def test_a_listing_may_not_be_a_video_on_a_host_off_the_list(self):
        self.assertEqual(policy.listing_entry({"webpage_url": "https://www.tiktok.com/@a/video/1", "id": "1"})["video_id"], "1")


class BodyTest(unittest.TestCase):
    URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"

    def test_a_fetch_takes_its_defaults(self):
        got = policy.check_fetch({"url": self.URL})
        self.assertEqual({k: v for k, v in got.items() if k != "url"},
                         {"start": 0.0, "max_seconds": 30.0, "max_height": 720, "max_shots": 6, "min_shot": 1.0,
                          "max_shot": 10.0, "source": "youtube"})

    def test_a_fetch_is_bounded(self):
        bad = [{"url": self.URL, "start": -1}, {"url": self.URL, "start": 601}, {"url": self.URL, "max_seconds": 0},
               {"url": self.URL, "max_seconds": 61}, {"url": self.URL, "max_height": 2160},
               {"url": self.URL, "max_height": 100}, {"url": self.URL, "max_shots": 0},
               {"url": self.URL, "max_shots": 10}, {"url": self.URL, "min_shot": 0.1},
               {"url": self.URL, "max_shot": 31}, {"url": self.URL, "min_shot": 6, "max_shot": 8},
               {"url": self.URL, "start": True}, {"url": self.URL, "max_shots": 2.5},
               {"url": self.URL, "max_seconds": "30"}, {"url": self.URL, "extra": 1}, {"url": "http://x"},
               {}, [], "x"]
        for body in bad:
            with self.subTest(body=body):
                with self.assertRaises(policy.RequestError):
                    policy.check_fetch(body)

    def test_a_probe_takes_only_a_url(self):
        self.assertEqual(policy.check_probe({"url": self.URL})["source"], "youtube")
        with self.assertRaises(policy.RequestError):
            policy.check_probe({"url": self.URL, "max": 3})

    def test_a_listing_takes_a_url_and_a_bounded_max(self):
        got = policy.check_listing({"url": "https://www.tiktok.com/@someone"})
        self.assertEqual(got["max"], 10)
        self.assertEqual(policy.check_listing({"url": "https://www.tiktok.com/@someone", "max": 50})["max"], 50)
        for bad in (0, 51, True, "5", 2.5):
            with self.subTest(max=bad):
                with self.assertRaises(policy.RequestError):
                    policy.check_listing({"url": "https://www.tiktok.com/@someone", "max": bad})


class RouteTest(unittest.TestCase):
    def test_the_routes(self):
        self.assertEqual(policy.route("GET", "/ping"), ("ping", {}))
        self.assertEqual(policy.route("POST", "/teeny/v1/probes"), ("probe", {}))
        self.assertEqual(policy.route("POST", "/teeny/v1/listings"), ("listing", {}))
        self.assertEqual(policy.route("POST", "/teeny/v1/fetches"), ("fetch", {}))
        self.assertEqual(policy.route("DELETE", f"/teeny/v1/fetches/{FETCH_ID}"), ("delete", {"fetch_id": FETCH_ID}))
        for name in ("source.mp4", "audio.m4a", "shot-1.jpg", "shot-9.jpg"):
            self.assertEqual(policy.route("GET", f"/teeny/v1/fetches/{FETCH_ID}/{name}"),
                             ("file", {"fetch_id": FETCH_ID, "name": name}))

    def test_every_other_path_or_method_is_nothing(self):
        for method, path in [
            ("GET", "/"), ("GET", "/ping/"), ("POST", "/ping"), ("GET", "/teeny/v1/probes"),
            ("PUT", "/teeny/v1/fetches"), ("GET", f"/teeny/v1/fetches/{FETCH_ID}"),
            ("DELETE", f"/teeny/v1/fetches/{FETCH_ID}/source.mp4"),
            ("GET", f"/teeny/v1/fetches/{FETCH_ID}/shot-0.jpg"), ("GET", f"/teeny/v1/fetches/{FETCH_ID}/shot-10.jpg"),
            ("GET", f"/teeny/v1/fetches/{FETCH_ID}/source.mp4/"), ("GET", f"/teeny/v1/fetches/{FETCH_ID}/../x"),
            ("GET", f"/teeny/v1/fetches/{FETCH_ID}/..%2fsource.mp4"), ("GET", f"/teeny/v1/fetches/{FETCH_ID}//source.mp4"),
            ("GET", "/teeny/v1/fetches/../../etc/passwd"), ("GET", "/teeny/v1/fetches/ABCDEF/source.mp4"),
            ("GET", f"/teeny/v1/fetches/{FETCH_ID[:-1]}/source.mp4"), ("GET", f"/teeny/v1/fetches/{FETCH_ID}/source.mp4?x=1"),
            ("GET", f"/teeny/v1/fetches/{FETCH_ID}/audio.mp3"), ("GET", f"/teeny/v1/fetches/{FETCH_ID}/dl.mp4"),
            ("GET", f"/teeny/v1/fetches/{FETCH_ID}/SOURCE.MP4"), ("GET", f"/teeny/v1/fetches/{FETCH_ID}/source.mp4\n"),
        ]:
            with self.subTest(method=method, path=path):
                self.assertIsNone(policy.route(method, path))

    def test_a_fetch_id_is_32_hex_and_the_server_makes_them(self):
        made = {policy.new_fetch_id() for _ in range(5)}
        self.assertEqual(len(made), 5)
        for fetch_id in made:
            self.assertRegex(fetch_id, r"[0-9a-f]{32}")


class SweepTest(unittest.TestCase):
    def test_the_stale_are_those_older_than_the_limit(self):
        self.assertEqual(policy.stale([("a", 100.0), ("b", 4000.0), ("c", 7000.0)], 8000.0, 3600.0), ["a", "b"])
        self.assertEqual(policy.stale([("a", 7999.0)], 8000.0, -1), ["a"])


if __name__ == "__main__":
    unittest.main()
