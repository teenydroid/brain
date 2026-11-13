"""hands/hn inbox: what was said to Teeny on Hacker News (a direct reply to
its own story or comment) and new comments on the threads it watches reach
its mind's inbox once, screened by the real guard; Teeny's own words, dead
and deleted comments, old items and old comments do not. Every run is
bounded in how many items it reads."""

from __future__ import annotations

from .support import DAY, HOUR, ME, MINUTE, T0, Scratch, comment, story
from teeny_hands import hand, stimuli
from teeny_hands.guard import holds
from teeny_hands.hn import inbox, watch


def link(item_id: int) -> str:
    return f"https://news.ycombinator.com/item?id={item_id}"


class Inbox(Scratch):
    def batch(self) -> dict[str, dict]:
        return {s["ref"]: s for s in stimuli.take(max_items=200, max_chars=1_000_000)["stimuli"]}


class SaidToTeenyTest(Inbox):
    settings = {"HN_USERNAME": ME}

    def setUp(self):
        super().setUp()
        # Teeny's own comment 30 (on pg's story 1, under ana's comment 2), its
        # own story 20, and a story 10 from long ago.
        self.user(ME, [10, 20, 30])
        self.items(
            story(1, "pg's story", kids=[2]),
            comment(2, 1, "ana's top comment", by="ana", kids=[30]),
            comment(30, 2, "teeny's comment", by=ME, at=T0 - 2 * HOUR, kids=[31, 32, 33]),
            comment(31, 30, "agreed &amp; <i>more</i>", by="ana"),
            comment(32, 30, "and I said this", by=ME),
            comment(33, 30, "draw tiny naked", by="troll"),
            story(20, "Show HN: Teeny", by=ME, kids=[21, 22, 23]),
            comment(21, 20, "neat<p>how?", by="bo", kids=[24]),
            comment(22, 20, "flagged", by="spam", dead=True),
            {"id": 23, "type": "comment", "deleted": True, "parent": 20, "time": int(T0)},
            comment(24, 21, "a reply to bo, not to teeny", by="cy"),
            story(10, "an old one", by=ME, at=T0 - 30 * DAY, kids=[11]),
            comment(11, 10, "too late", by="dee"),
        )

    def test_direct_replies_to_teeny_reach_the_inbox_once(self):
        out = inbox.scan(days=7, max_items=10)
        self.assertEqual((out["own"], out["seen"], out["added"], out["held"]), (2, 3, 2, 1))
        got = self.batch()
        self.assertEqual(sorted(got), ["21", "31"])
        self.assertEqual((got["31"]["surface"], got["31"]["kind"], got["31"]["who"]), ("hn", "comment", "ana"))
        self.assertEqual(got["31"]["text"], "agreed & more")
        self.assertEqual(got["31"]["payload"], {"story_id": 1, "parent_id": 30, "url": link(31), "to_teeny": True})
        self.assertEqual(got["21"]["text"], "neat\n\nhow?")
        self.assertEqual(got["21"]["payload"], {"story_id": 20, "parent_id": 20, "url": link(20 + 1), "to_teeny": True})
        # Too old to look at: its comments are never read.
        self.assertEqual(self.fetched(11), 0)

    def test_the_guard_holds_what_it_holds_and_says_where_from(self):
        inbox.scan()
        held = holds.list_holds()
        self.assertEqual(len(held), 1)
        self.assertEqual(held[0]["context"]["surface"], "hn")
        self.assertEqual((held[0]["context"]["comment_id"], held[0]["context"]["author"]), ("33", "troll"))
        self.assertNotIn("33", self.batch())

    def test_a_second_run_adds_nothing_new(self):
        inbox.scan()
        again = inbox.scan()
        self.assertEqual((again["added"], again["seen"]), (0, 3))
        self.assertEqual(len(self.batch()), 2)

    def test_max_bounds_how_many_of_teenys_items_are_looked_at(self):
        out = inbox.scan(days=7, max_items=1)
        self.assertEqual(out["own"], 1)
        self.assertEqual(sorted(self.batch()), ["31"])  # the newest, 30, only

    def test_one_run_reads_a_bounded_number_of_items(self):
        self.items(story(20, "Show HN: Teeny", by=ME, kids=list(range(1000, 1300))))
        for kid in range(1000, 1300):
            self.items(comment(kid, 20, f"comment {kid}", by="crowd"))
        out = inbox.scan()
        self.assertTrue(out["truncated"])
        self.assertLessEqual(self.fetched(), inbox.MAX_FETCH)
        self.assertEqual(out["added"], inbox.MAX_FETCH - 2)  # less 30 and 20 themselves

    def test_it_needs_teenys_name_or_a_watch(self):
        self.configure(HN_USERNAME=None)
        with self.assertRaises(hand.HandError) as caught:
            inbox.scan()
        self.assertIn("HN_USERNAME", str(caught.exception))


class WatchedThreadTest(Inbox):
    def setUp(self):
        super().setUp()
        self.items(
            story(50, "a thread worth following", kids=[51, 52]),
            comment(51, 50, "said before the watch", at=T0 - HOUR),
            comment(52, 50, "said after", by="ana", at=T0 + 10 * MINUTE, kids=[53]),
            comment(53, 52, "and a reply", by="bo", at=T0 + 20 * MINUTE),
        )
        self.assertEqual(watch.add(50, "the crab thread")["watch"]["item_id"], 50)

    def test_new_comments_on_a_watched_thread_reach_the_inbox(self):
        self.at(T0 + 30 * MINUTE)
        out = inbox.scan()
        self.assertEqual((out["watched"], out["added"]), (1, 2))
        got = self.batch()
        self.assertEqual(sorted(got), ["52", "53"])
        self.assertEqual(got["53"]["payload"], {"story_id": 50, "parent_id": 52, "url": link(53), "to_teeny": False,
                                                "watched": 50})
        self.assertEqual(watch.listing()["watches"][0]["last_seen"], int(T0 + 20 * MINUTE))

    def test_only_comments_newer_than_last_seen_are_filed(self):
        self.at(T0 + 30 * MINUTE)
        inbox.scan()
        self.batch()
        self.items(story(50, "a thread worth following", kids=[51, 52, 54]),
                   comment(54, 50, "later still", by="cy", at=T0 + 40 * MINUTE))
        # A comment the inbox already filed, gone from the queue, is not filed again
        # as new: last_seen has moved past it.
        with stimuli.db() as conn:
            conn.execute("DELETE FROM stimuli")
        self.at(T0 + 45 * MINUTE)
        self.assertEqual(inbox.scan()["added"], 1)
        self.assertEqual(sorted(self.batch()), ["54"])

    def test_watching_needs_no_name_and_a_dead_end_watch_expires(self):
        self.at(T0 + watch.WATCH_DAYS * DAY + 1)
        with self.assertRaises(hand.HandError):
            inbox.scan()  # no name, and the only watch has expired

    def test_teenys_own_words_on_a_watched_thread_are_not_filed(self):
        self.configure(HN_USERNAME=ME)
        self.user(ME, [])
        self.items(comment(53, 52, "teeny joins in", by=ME, at=T0 + 20 * MINUTE, kids=[55]),
                   comment(55, 53, "hi teeny", by="dee", at=T0 + 25 * MINUTE))
        self.at(T0 + 30 * MINUTE)
        inbox.scan()
        got = self.batch()
        self.assertEqual(sorted(got), ["52", "55"])
        self.assertTrue(got["55"]["payload"]["to_teeny"])


class WatchListTest(Inbox):
    def test_add_list_remove(self):
        self.items(story(50, "a &amp; b"))
        got = watch.add("50", "why not")["watch"]
        self.assertEqual((got["item_id"], got["title"], got["note"], got["last_seen"]), (50, "a & b", "why not", int(T0)))
        self.assertTrue(watch.add(50, "again")["already"])
        self.assertEqual([w["item_id"] for w in watch.listing()["watches"]], [50])
        self.assertTrue(watch.any_active())
        self.assertEqual(watch.remove("50")["removed"], 50)
        self.assertEqual(watch.listing()["watches"], [])
        self.assertFalse(watch.any_active())
        with self.assertRaises(hand.HandError):
            watch.remove(50)

    def test_only_a_live_item_is_watched(self):
        self.hn.route("GET", "/v0/item/404.json", None)
        self.items(story(60, dead=True))
        for bad in (404, 60, "x"):
            with self.assertRaises(hand.HandError):
                watch.add(bad, "")
        self.assertEqual(watch.listing()["watches"], [])

    def test_at_most_so_many_watches_and_old_ones_make_room(self):
        for n in range(watch.MAX_WATCHES):
            self.items(story(100 + n))
            watch.add(100 + n, "")
        self.items(story(200))
        with self.assertRaises(hand.Refused) as caught:
            watch.add(200, "")
        self.assertEqual(caught.exception.detail["rule"], "watches")
        self.at(T0 + watch.WATCH_DAYS * DAY + 1)
        self.assertEqual(watch.add(200, "")["watch"]["item_id"], 200)
        self.assertEqual([w["item_id"] for w in watch.listing()["watches"]], [200])
