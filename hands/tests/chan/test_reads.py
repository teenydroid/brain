"""hands/chan's reads: 4chan's HTML as text, the board fence, catalogs and
threads screened by the guard, and sparks drawn at random."""

from __future__ import annotations

import json

from .support import HOUR, MINUTE, T0, Scratch, op
from teeny_hands import hand
from teeny_hands.chan import client, reads


class PlainTextTest(Scratch):
    def test_lines_and_greentext_survive_and_reply_links_go(self):
        com = ('<a href="#p123" class="quotelink">&gt;&gt;123</a><br>'
               '<span class="quote">&gt;be me</span><br><span class="quote">&gt;a toaster</span><br><br><br>'
               'it&#039;s &quot;listening&quot; <wbr>to me')
        self.assertEqual(reads.plain(com), ">be me\n>a toaster\n\nit's \"listening\" to me")

    def test_addresses_and_cross_board_links_are_taken_out(self):
        self.assertEqual(reads.plain("see https://example.com/a?b=1 and &gt;&gt;&gt;/x/9999 ok"),
                         "see [url] and ok")
        self.assertEqual(reads.plain(None), "")


class BoardFenceTest(Scratch):
    def test_only_safe_boards_are_read_whatever_is_asked(self):
        for name in ("pol", "b", "r9k", "/pol/", "s", "../x"):
            with self.assertRaises(hand.HandError, msg=name):
                client.board(name)
        self.assertEqual(client.board("/X/"), "x")

    def test_chan_boards_can_narrow_the_default_but_never_widen_it(self):
        self.assertEqual(client.boards(), list(client.DEFAULT_BOARDS))
        self.configure(CHAN_BOARDS="pol,x,b,lit,x")
        self.assertEqual(client.boards(), ["x", "lit"])
        self.configure(CHAN_BOARDS="pol,b")
        self.assertEqual(client.boards(), list(client.DEFAULT_BOARDS))
        with self.assertRaises(hand.HandError):
            client.boards("x,pol")

    def test_a_refused_board_is_never_requested(self):
        with self.assertRaises(hand.HandError):
            reads.catalog("pol", 5)
        self.assertEqual(self.chan.requests("GET"), [])


class CatalogTest(Scratch):
    def test_threads_are_premises_liveliest_first_with_nobody_named(self):
        self.catalog("x",
                     op(1, sub="pinned rules", com="read the rules before posting here", sticky=1),
                     op(2, sub="birds", com="birds are drones that charge on power lines", replies=5),
                     op(3, com="the moon is a hard drive<br>and it is almost full", replies=90),
                     op(4, com="bump", replies=400),
                     pages=2)
        got = reads.catalog("x", 10)
        self.assertEqual([t["no"] for t in got["threads"]], [3, 2])
        self.assertEqual(got["threads"][0]["premise"], "the moon is a hard drive / and it is almost full")
        self.assertEqual(got["threads"][1]["premise"], "birds: birds are drones that charge on power lines")
        self.assertEqual(got["threads"][0]["age_hours"], 1.0)
        self.assertEqual(got["threads"][0]["link"], "https://boards.4chan.org/x/thread/3")
        self.assertEqual(got["dropped"], {"screened": 0, "empty": 1, "pinned": 1})
        self.assertTrue(got["untrusted"])
        text = json.dumps(got)
        for private in ("Anonymous", "!!secret", "AbCd1234", '"US"'):
            self.assertNotIn(private, text)

    def test_a_premise_the_guard_would_hold_never_leaves_the_hand(self):
        self.catalog("x", op(1, com="the government is hiding a nazi moon base under the ice"),
                     op(2, com="the government is hiding a second moon behind the first one"))
        got = reads.catalog("x", 10)
        self.assertEqual([t["no"] for t in got["threads"]], [2])
        self.assertEqual(got["dropped"]["screened"], 1)
        self.assertNotIn("nazi", json.dumps(got))

    def test_the_catalog_is_cached_ten_minutes(self):
        self.catalog("lit", op(1))
        reads.catalog("lit", 5)
        reads.catalog("lit", 5)
        self.assertEqual(self.reads("/lit/catalog.json"), 1)
        self.at(T0 + 11 * MINUTE)
        reads.catalog("lit", 5)
        self.assertEqual(self.reads("/lit/catalog.json"), 2)


class ThreadTest(Scratch):
    def test_posts_in_order_screened_and_clipped(self):
        self.thread("x", 7,
                    op(7, sub="the fridge", com="my fridge hums in morse code"),
                    {"no": 8, "resto": 7, "time": int(T0), "com": '<a class="quotelink">&gt;&gt;7</a><br>what does it say'},
                    {"no": 9, "resto": 7, "time": int(T0), "com": "go kill yourself"},
                    {"no": 10, "resto": 7, "time": int(T0)},
                    {"no": 11, "resto": 7, "time": int(T0), "com": "it says buy eggs"})
        got = reads.thread("x", "7", 2)
        self.assertEqual([(p["no"], p["op"]) for p in got["posts"]], [(7, True), (8, False)])
        self.assertEqual(got["posts"][1]["text"], "what does it say")
        self.assertEqual(got["posts"][0]["subject"], "the fridge")
        got = reads.thread("x", 7, 20)
        self.assertEqual([p["no"] for p in got["posts"]], [7, 8, 11])
        self.assertEqual(got["dropped"], {"screened": 1, "empty": 1, "pinned": 0})

    def test_a_missing_thread_is_an_error(self):
        self.chan.route("GET", "/x/thread/5.json", {}, status=404)
        with self.assertRaises(hand.HandError):
            reads.thread("x", 5, 5)
        with self.assertRaises(hand.HandError):
            reads.thread("x", "-1", 5)


class SparksTest(Scratch):
    def setUp(self) -> None:
        super().setUp()
        self.catalog("x", *[op(n, com=f"premise number {n} about the lizard satellites", replies=n)
                            for n in range(1, 31)])
        self.catalog("lit", *[op(100 + n, com=f"theory {n}: every novel is one long apology", replies=n)
                              for n in range(1, 11)])

    def test_a_seed_makes_the_draw_repeatable_and_no_thread_comes_twice(self):
        one = reads.sparks("x,lit", 6, seed="run-1")
        two = reads.sparks("x,lit", 6, seed="run-1")
        self.assertEqual(one["sparks"], two["sparks"])
        self.assertEqual(len({s["id"] for s in one["sparks"]}), 6)
        self.assertEqual(one["seed"], "run-1")
        self.assertTrue(all(s["id"].startswith(("chan:x:", "chan:lit:")) for s in one["sparks"]))
        others = {tuple(s["id"] for s in reads.sparks("x,lit", 6, seed=f"run-{n}")["sparks"]) for n in range(2, 8)}
        self.assertGreater(len(others), 1)

    def test_each_board_is_read_once_however_many_sparks(self):
        reads.sparks("x,lit", 10, seed="a")
        reads.sparks("x,lit", 10, seed="b")
        self.assertEqual((self.reads("/x/catalog.json"), self.reads("/lit/catalog.json")), (1, 1))

    def test_a_board_that_cannot_be_read_is_named_and_the_rest_answer(self):
        self.chan.route("GET", "/sci/catalog.json", {"error": "down"}, status=503)
        got = reads.sparks("sci,lit", 3, seed="s")
        self.assertEqual(len(got["sparks"]), 3)
        self.assertTrue(got["unavailable"][0].startswith("/sci/"))
        with self.assertRaises(hand.HandError):
            reads.sparks("sci", 3, seed="s")

    def test_there_are_never_more_sparks_than_threads(self):
        self.assertEqual(len(reads.sparks("lit", 20, seed="s")["sparks"]), 10)
