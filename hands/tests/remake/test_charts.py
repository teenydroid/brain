"""hands/remake charts: what is popular on TikTok (a listed account's recent
videos through the ingest service) and YouTube (the Data API's most-popular
chart), filtered and ranked, kept for a slot, and every way a provider can
give nothing. Nothing here uses the network."""

from __future__ import annotations

import io
import json
import urllib.error
from unittest import mock

from .support import FakeIngest, Scratch, entry, item
from teeny_hands import hand
from teeny_hands.remake import charts, ledger
from teeny_hands.remake.service import IngestError

def vid(n):
    return f"vid{n:08d}"


KEY = "AIzaSyTESTKEY-0123456789abcdefghijklmnop"
TT = "https://www.tiktok.com/@{}"


class Base(Scratch):
    def setUp(self):
        super().setUp()
        self.ingest = FakeIngest()
        self.asked_urls = []
        self.youtube_items = []

    def get_json(self, url):
        self.asked_urls.append(url)
        if isinstance(self.youtube_items, Exception):
            raise self.youtube_items
        return {"items": self.youtube_items}

    def top(self, slot="2026-11-18:morning", limit=15, fresh=False):
        return charts.top(slot, limit, fresh=fresh, ingest=self.ingest, get_json=self.get_json)

    def tiktok(self, *accounts, **lists):
        self.secrets(f"REMAKE_TIKTOK_ACCOUNTS={','.join(accounts)}\n")
        for name, entries in lists.items():
            self.ingest.lists[TT.format(name)] = entries

    def unavailable(self, got, provider):
        return next(u for u in got["unavailable"] if u["provider"] == provider)

    def ids(self, got):
        return [c["id"] for c in got["candidates"]]


class TikTokTest(Base):
    def test_each_account_is_listed_and_its_videos_become_candidates(self):
        self.tiktok("alice", "@bob", alice=[entry("1", views=500, uploader="alice")], bob=[entry("2", views=900, uploader="bob")])
        got = self.top()
        self.assertEqual(self.ids(got), ["chart:tt:2", "chart:tt:1"])
        first = got["candidates"][0]
        self.assertEqual(first, {"id": "chart:tt:2", "url": "https://www.tiktok.com/@bob/video/2", "source": "tiktok",
                                 "title": "a dance", "by": "bob", "seconds": 20.0, "views": 900, "age_hours": 5.0,
                                 "rank": 1, "provider": "tiktok"})
        self.assertEqual([c[1] for c in self.ingest.asked("listing")], [TT.format("alice"), TT.format("bob")])

    def test_a_video_older_than_the_window_is_left_out(self):
        self.tiktok("alice", alice=[entry("1", hours_old=100), entry("2", hours_old=200)])
        self.assertEqual(self.ids(self.top()), ["chart:tt:1"])
        self.secrets("REMAKE_TIKTOK_ACCOUNTS=alice\nREMAKE_MAX_AGE_HOURS=300\n")
        self.assertEqual(sorted(self.ids(self.top("other"))), ["chart:tt:1", "chart:tt:2"])

    def test_a_video_with_no_known_age_or_length_stays_for_the_study_to_judge(self):
        self.tiktok("alice", alice=[entry("1", hours_old=None, seconds=None)])
        got = self.top()
        self.assertEqual(self.ids(got), ["chart:tt:1"])
        self.assertIsNone(got["candidates"][0]["age_hours"])
        self.assertIsNone(got["candidates"][0]["seconds"])

    def test_the_length_must_be_eight_to_180_seconds(self):
        self.tiktok("alice", alice=[entry("1", seconds=7.9), entry("2", seconds=8), entry("3", seconds=180), entry("4", seconds=180.1)])
        self.assertEqual(sorted(self.ids(self.top())), ["chart:tt:2", "chart:tt:3"])

    def test_an_account_name_that_is_not_one_is_not_listed_and_is_said_so(self):
        self.tiktok("alice", "../x", "a b", alice=[entry("1")])
        got = self.top()
        self.assertEqual(self.ids(got), ["chart:tt:1"])
        self.assertEqual([c[1] for c in self.ingest.asked("listing")], [TT.format("alice")])
        self.assertIn({"provider": "tiktok", "reason": "bad_account"}, [{k: u[k] for k in ("provider", "reason")} for u in got["unavailable"]])

    def test_an_account_that_fails_is_named_and_the_others_come(self):
        self.tiktok("alice", "bob", bob=[entry("2")])
        self.ingest.lists[TT.format("alice")] = IngestError("could not reach the ingest service (URLError)")
        got = self.top()
        self.assertEqual(self.ids(got), ["chart:tt:2"])
        self.assertEqual(got["unavailable"][0]["provider"], "tiktok")
        self.assertEqual(got["unavailable"][0]["reason"], "ingest")
        self.assertIn("ingest service", got["unavailable"][0]["error"])


class YouTubeTest(Base):
    def setUp(self):
        super().setUp()
        self.secrets(f"YOUTUBE_API_KEY={KEY}\n")

    def test_the_most_popular_chart_is_asked_for_the_region(self):
        self.youtube_items = [item("aaaaaaaaaaa", views=10), item("bbbbbbbbbbb", views=99)]
        got = self.top()
        self.assertEqual(self.ids(got), ["chart:yt:bbbbbbbbbbb", "chart:yt:aaaaaaaaaaa"])
        self.assertEqual(got["candidates"][0], {"id": "chart:yt:bbbbbbbbbbb", "url": "https://www.youtube.com/watch?v=bbbbbbbbbbb",
                                                "source": "youtube", "title": "a song", "by": "A Channel", "seconds": 45.0,
                                                "views": 99, "age_hours": 6.0, "rank": 1, "provider": "youtube"})
        asked = self.asked_urls[0]
        for part in ("https://www.googleapis.com/youtube/v3/videos?", "chart=mostPopular", "maxResults=25",
                     "regionCode=US", "part=snippet%2CcontentDetails%2Cstatistics"):
            self.assertIn(part, asked)
        self.secrets(f"YOUTUBE_API_KEY={KEY}\nREMAKE_REGION=GB\n")
        self.top("other")
        self.assertIn("regionCode=GB", self.asked_urls[1])

    def test_a_live_news_age_restricted_or_wrongly_long_video_is_left_out(self):
        ok, short, long_ = vid(1), vid(2), vid(3)
        self.youtube_items = [
            item(ok),
            item(vid(10), live="live"), item(vid(11), live="upcoming"),
            item(vid(12), category="25"),
            item(vid(13), rating={"ytRating": "ytAgeRestricted"}),
            item(vid(14), duration="PT7S"), item(vid(15), duration="PT3M1S"),
            item(vid(16), duration="P0D"), item(long_, duration="PT3M"), item(short, duration="PT8S"),
            item(vid(17), duration="PT1H"), item(vid(18), duration="soon"),
        ]
        self.assertEqual(sorted(self.ids(self.top())), sorted(f"chart:yt:{i}" for i in (ok, short, long_)))

    def test_the_key_is_in_the_request_and_nowhere_in_the_answer(self):
        self.youtube_items = [item("aaaaaaaaaaa")]
        got = self.top()
        self.assertIn(KEY, self.asked_urls[0])
        self.assertNotIn(KEY, json.dumps(got))
        self.assertNotIn(KEY, json.dumps(charts.top("2026-11-18:morning", 15, ingest=self.ingest, get_json=self.get_json)))
        for path in self.root.rglob("*"):
            if path.is_file() and path.name != "teeny.env":
                self.assertNotIn(KEY.encode(), path.read_bytes(), path)

    def test_a_failure_that_carries_the_key_never_shows_it(self):
        self.youtube_items = urllib.error.URLError(f"cannot reach https://www.googleapis.com/youtube/v3/videos?key={KEY}")
        got = self.top()
        self.assertEqual(got["candidates"], [])
        self.assertEqual(self.unavailable(got, "youtube")["reason"], "api")
        self.assertNotIn(KEY, json.dumps(got))
        self.youtube_items = RuntimeError(f"boom {KEY}")
        got = self.top("other")
        self.assertNotIn(KEY, json.dumps(got))
        self.assertEqual(self.unavailable(got, "youtube")["reason"], "api")

    def test_the_real_request_turns_an_http_error_into_its_status_and_reason_only(self):
        body = io.BytesIO(json.dumps({"error": {"errors": [{"reason": "quotaExceeded"}], "message": f"key {KEY}"}}).encode())
        error = urllib.error.HTTPError(f"https://www.googleapis.com/youtube/v3/videos?key={KEY}", 403, "Forbidden", {}, body)
        with mock.patch.object(charts._OPENER, "open", side_effect=error):
            with self.assertRaises(charts.ProviderError) as caught:
                charts._get_json(f"https://www.googleapis.com/youtube/v3/videos?key={KEY}")
        self.assertNotIn(KEY, str(caught.exception))
        self.assertIn("403", str(caught.exception))
        self.assertIn("quotaExceeded", str(caught.exception))
        with mock.patch.object(charts._OPENER, "open", side_effect=urllib.error.URLError(f"x {KEY}")):
            with self.assertRaises(charts.ProviderError) as caught:
                charts._get_json(f"https://www.googleapis.com/youtube/v3/videos?key={KEY}")
        self.assertNotIn(KEY, str(caught.exception))

    def test_an_answer_that_is_not_a_list_of_items_is_unavailable(self):
        self.get_json = lambda url: {"items": "nope"}
        got = charts.top("k", 15, ingest=self.ingest, get_json=self.get_json)
        self.assertEqual(self.unavailable(got, "youtube")["reason"], "api")


class FilterTest(Base):
    def setUp(self):
        super().setUp()
        self.secrets(f"YOUTUBE_API_KEY={KEY}\nREMAKE_TIKTOK_ACCOUNTS=alice\n")

    def test_a_title_with_a_harm_or_a_not_a_figure_word_is_left_out(self):
        self.youtube_items = [item("aaaaaaaaaaa", title="man stabbed in the park"), item("bbbbbbbbbbb", title="my kids dancing"),
                              item("ccccccccccc", title="cute robot dancing")]
        self.ingest.lists[TT.format("alice")] = [entry("1", title="she was arrested"), entry("2", title="a nice walk")]
        got = self.top()
        self.assertEqual(sorted(self.ids(got)), ["chart:tt:2", "chart:yt:ccccccccccc"])
        self.assertEqual(got["dropped_by_screen"], 3)

    def test_the_inbound_screen_covers_the_title_and_the_uploader(self):
        self.youtube_items = [item("aaaaaaaaaaa", title="heil hitler montage"), item("bbbbbbbbbbb", channel="heil hitler fans"),
                              item("ccccccccccc")]
        self.ingest.lists[TT.format("alice")] = [entry("1", uploader="heil hitler")]
        got = self.top()
        self.assertEqual(self.ids(got), ["chart:yt:ccccccccccc"])
        self.assertEqual(got["dropped_by_screen"], 3)

    def test_a_video_already_made_into_a_remake_is_left_out(self):
        self.youtube_items = [item("aaaaaaaaaaa"), item("bbbbbbbbbbb")]
        self.ingest.lists[TT.format("alice")] = [entry("1"), entry("2")]
        ledger.mark_used("1", "tiktok", "https://www.tiktok.com/@alice/video/1", "a dance", "tt-1")
        ledger.mark_used("aaaaaaaaaaa", "youtube", "https://www.youtube.com/watch?v=aaaaaaaaaaa", "a song", "yt-aaaaaaaaaaa")
        self.assertEqual(sorted(self.ids(self.top())), ["chart:tt:2", "chart:yt:bbbbbbbbbbb"])

    def test_candidates_are_ranked_by_views_across_providers_and_limited(self):
        self.youtube_items = [item("aaaaaaaaaaa", views=300), item("bbbbbbbbbbb", views=100)]
        self.ingest.lists[TT.format("alice")] = [entry("1", views=200), entry("2", views=None)]
        got = self.top(limit=3)
        self.assertEqual(self.ids(got), ["chart:yt:aaaaaaaaaaa", "chart:tt:1", "chart:yt:bbbbbbbbbbb"])
        self.assertEqual([c["rank"] for c in got["candidates"]], [1, 2, 3])

    def test_a_video_listed_twice_is_one_candidate(self):
        self.ingest.lists[TT.format("alice")] = [entry("1", views=5), entry("1", views=9)]
        self.assertEqual(self.ids(self.top()), ["chart:tt:1"])

    def test_text_is_one_clean_line_and_capped(self):
        self.ingest.lists[TT.format("alice")] = [entry("1", title="a\x00 b\n\tc" + "x" * 500, uploader="n‮ame")]
        got = self.top()["candidates"][0]
        self.assertTrue(got["title"].startswith("a b c"))
        self.assertLessEqual(len(got["title"]), 200)
        self.assertEqual(got["by"], "name")


class NothingTest(Base):
    def test_with_no_provider_on_it_answers_nothing_and_says_why_without_raising(self):
        got = self.top()
        self.assertEqual(got["candidates"], [])
        self.assertEqual(sorted((u["provider"], u["reason"]) for u in got["unavailable"]), [("tiktok", "off"), ("youtube", "off")])
        self.assertFalse(got["cached"])
        self.assertEqual(self.ingest.calls, [])
        self.assertEqual(self.asked_urls, [])

    def test_an_empty_answer_is_not_kept(self):
        self.top()
        self.assertFalse(list((self.root / "state" / "remake" / "charts").glob("*.json")))

    def test_it_works_in_a_dry_room(self):
        self.secrets("DRY_RUN=1\nREMAKE_TIKTOK_ACCOUNTS=alice\n")
        self.ingest.lists[TT.format("alice")] = [entry("1")]
        self.assertEqual(self.ids(self.top()), ["chart:tt:1"])


class CacheTest(Base):
    def setUp(self):
        super().setUp()
        self.tiktok("alice", alice=[entry("1", views=3), entry("2", views=2), entry("3", views=1)])

    def test_a_slot_read_twice_asks_nothing_the_second_time(self):
        first = self.top()
        n = len(self.ingest.calls)
        again = self.top()
        self.assertEqual(len(self.ingest.calls), n)
        self.assertTrue(again["cached"])
        self.assertEqual(again["candidates"], first["candidates"])
        self.top("2026-11-18:evening")
        self.assertGreater(len(self.ingest.calls), n)
        n = len(self.ingest.calls)
        self.assertFalse(self.top(fresh=True)["cached"])
        self.assertGreater(len(self.ingest.calls), n)

    def test_a_cached_read_drops_what_was_made_since_and_honours_the_limit(self):
        self.top()
        ledger.mark_used("1", "tiktok", "https://www.tiktok.com/@alice/video/1", "a dance", "tt-1")
        got = self.top(limit=1)
        self.assertTrue(got["cached"])
        self.assertEqual(self.ids(got), ["chart:tt:2"])

    def test_with_no_slot_the_key_is_the_utc_hour_and_a_bad_slot_is_an_error(self):
        self.assertEqual(charts.top(None, 5, ingest=self.ingest, get_json=self.get_json)["key"], "2026-11-18T11")
        with self.assertRaises(hand.HandError):
            charts.top("../etc/passwd", 5, ingest=self.ingest, get_json=self.get_json)

    def test_a_chart_id_is_resolved_from_the_kept_charts(self):
        self.top()
        found = charts.resolve("chart:tt:2")
        self.assertEqual(found["url"], "https://www.tiktok.com/@someone/video/2")
        self.assertIsNone(charts.resolve("chart:tt:999"))
        self.assertIsNone(charts.resolve("chart:tt:../x"))
        self.assertEqual(charts.resolve("chart:yt:dQw4w9WgXcQ")["url"], "https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        self.assertIsNone(charts.resolve("chart:yt:short"))
        self.assertIsNone(charts.resolve("whatever"))


class CliTest(Base):
    def test_charts_prints_one_json_object(self):
        import contextlib
        from teeny_hands.remake import cli

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = cli.main(["charts", "--max", "3"])
        self.assertEqual(code, 0)
        got = json.loads(out.getvalue())
        self.assertTrue(got["ok"])
        self.assertEqual(got["candidates"], [])
