"""hands/khaby find: Khaby Lame's recent videos, listed through the ingest
service from the accounts in KHABY_ACCOUNTS, the ones already used left
out, kept for the hour."""

from __future__ import annotations

from .support import FakeIngest, Scratch, entry
from teeny_hands.khaby import find
from teeny_hands.remake import ledger
from teeny_hands.remake.service import IngestError

KHABY = "https://www.tiktok.com/@khaby.lame"


def khaby(video_id, **more):
    return entry(video_id, uploader="khaby.lame", **{"title": "when the hack is too much", **more})


class Base(Scratch):
    def setUp(self):
        super().setUp()
        self.ingest = FakeIngest()
        self.ingest.lists[KHABY] = [khaby("7300", views=900), khaby("7301", views=5000), khaby("7302", views=10)]

    def find(self, limit=10, fresh=False):
        return find.find(limit, fresh=fresh, ingest=self.ingest)

    def ids(self, got):
        return [c["id"] for c in got["candidates"]]


class ListingTest(Base):
    def test_the_default_account_is_khaby_lame_listed_through_the_ingest_service(self):
        got = self.find()
        self.assertEqual(self.ingest.asked("listing"), [("listing", KHABY, find.PER_ACCOUNT)])
        self.assertEqual(self.ids(got), ["chart:tt:7301", "chart:tt:7300", "chart:tt:7302"])
        self.assertEqual([c["rank"] for c in got["candidates"]], [1, 2, 3])
        first = got["candidates"][0]
        self.assertEqual((first["url"], first["source"], first["by"], first["views"], first["seconds"]),
                         ("https://www.tiktok.com/@khabylame/video/7301", "tiktok", "khaby.lame", 5000, 20.0))
        self.assertEqual(got["unavailable"], [])
        self.assertFalse(got["cached"])

    def test_each_account_named_is_listed(self):
        self.secrets("KHABY_ACCOUNTS=khaby.lame, @other_one\n")
        self.ingest.lists["https://www.tiktok.com/@other_one"] = [entry("7400", uploader="other_one", views=1)]
        got = self.find()
        self.assertEqual([c[1] for c in self.ingest.asked("listing")], [KHABY, "https://www.tiktok.com/@other_one"])
        self.assertIn("chart:tt:7400", self.ids(got))

    def test_max_caps_the_candidates(self):
        self.assertEqual(self.ids(self.find(limit=1)), ["chart:tt:7301"])

    def test_old_videos_stay_since_he_posts_rarely(self):
        self.ingest.lists[KHABY] = [khaby("7300", hours_old=24 * 400)]
        self.assertEqual(self.ids(self.find()), ["chart:tt:7300"])

    def test_a_video_too_short_or_too_long_or_untitled_is_left_out(self):
        self.ingest.lists[KHABY] = [khaby("7300", seconds=5.0), khaby("7301", seconds=400.0), khaby("7302", title=""),
                                    khaby("7303", seconds=None), khaby("7304")]
        self.assertEqual(sorted(self.ids(self.find())), ["chart:tt:7303", "chart:tt:7304"])

    def test_a_title_the_screen_stops_is_dropped_and_counted(self):
        self.ingest.lists[KHABY] = [khaby("7300", title="man stabbed in the park"), khaby("7301")]
        got = self.find()
        self.assertEqual(self.ids(got), ["chart:tt:7301"])
        self.assertEqual(got["dropped_by_screen"], 1)

    def test_an_entry_that_is_not_a_tiktok_video_is_left_out(self):
        bad = khaby("7300")
        bad["url"] = "https://evil.example/x"
        self.ingest.lists[KHABY] = [bad, {**khaby("7301"), "video_id": "../x"}, khaby("7302")]
        self.assertEqual(self.ids(self.find()), ["chart:tt:7302"])

    def test_a_used_video_is_left_out(self):
        ledger.mark_used("7301", "tiktok", "https://www.tiktok.com/@khabylame/video/7301")
        self.assertEqual(self.ids(self.find()), ["chart:tt:7300", "chart:tt:7302"])


class UnavailableTest(Base):
    def test_no_account_named_is_off_and_lists_nothing(self):
        self.secrets("KHABY_ACCOUNTS= , ,\n")
        got = self.find()
        self.assertEqual(got["candidates"], [])
        self.assertEqual(got["unavailable"], [{"provider": "tiktok", "reason": "off"}])
        self.assertEqual(self.ingest.calls, [])

    def test_a_bad_account_name_is_named_and_not_listed(self):
        self.secrets("KHABY_ACCOUNTS=../etc,khaby.lame\n")
        got = self.find()
        self.assertEqual([c[1] for c in self.ingest.asked("listing")], [KHABY])
        self.assertEqual(got["unavailable"][0]["reason"], "bad_account")
        self.assertEqual(len(got["candidates"]), 3)

    def test_the_ingest_service_failing_is_named_not_raised(self):
        self.ingest.lists[KHABY] = IngestError("could not reach the ingest service (URLError)")
        got = self.find()
        self.assertEqual(got["candidates"], [])
        self.assertEqual(got["unavailable"][0]["provider"], "tiktok")
        self.assertEqual(got["unavailable"][0]["reason"], "ingest")


class CacheTest(Base):
    def test_a_second_find_in_the_hour_is_the_kept_answer_and_lists_nothing(self):
        first = self.find()
        self.ingest.calls.clear()
        again = self.find()
        self.assertTrue(again["cached"])
        self.assertEqual(self.ingest.calls, [])
        self.assertEqual(again["candidates"], first["candidates"])
        self.assertTrue(list((self.root / "state" / "khaby" / "finds").glob("*.json")))

    def test_fresh_lists_again(self):
        self.find()
        self.ingest.calls.clear()
        self.assertFalse(self.find(fresh=True)["cached"])
        self.assertEqual(len(self.ingest.asked("listing")), 1)

    def test_a_kept_answer_drops_what_was_used_since(self):
        self.find()
        ledger.mark_used("7301", "tiktok", "https://www.tiktok.com/@khabylame/video/7301")
        got = self.find()
        self.assertTrue(got["cached"])
        self.assertEqual(self.ids(got), ["chart:tt:7300", "chart:tt:7302"])
        self.assertEqual([c["rank"] for c in got["candidates"]], [1, 2])

    def test_an_empty_answer_is_not_kept(self):
        self.ingest.lists[KHABY] = []
        self.find()
        self.ingest.lists[KHABY] = [khaby("7300")]
        self.assertEqual(self.ids(self.find()), ["chart:tt:7300"])

    def test_a_kept_find_resolves_its_ids(self):
        self.find()
        self.assertEqual(find.resolve("chart:tt:7300")["url"], "https://www.tiktok.com/@khabylame/video/7300")
        self.assertIsNone(find.resolve("chart:tt:9999"))
        self.assertIsNone(find.resolve("nonsense"))
