"""hands/x keeps the lab tags with each own post and reply: `format` and
`experiment` in x_posts, never sent to X; a ledger from before those
columns gains them and keeps its rows."""

from __future__ import annotations

from unittest import mock

from .support import T0, Guard, Scratch, me
from teeny_hands import hand
from teeny_hands.x import posting_rules, publish


class TagsTest(Scratch):
    def setUp(self):
        super().setUp()
        self.at(T0)
        self.x.route("POST", "/2/tweets", {"data": {"id": "100"}}, status=201)
        patcher = mock.patch.object(publish, "check_outbound", Guard())
        patcher.start()
        self.addCleanup(patcher.stop)

    def ledger(self) -> list[dict]:
        conn = hand.db()
        posting_rules.ensure_schema(conn)
        return [dict(r) for r in conn.execute("SELECT * FROM x_posts ORDER BY created_at")]

    def test_a_post_records_its_format_and_experiment(self):
        publish.post({"anchor": "hn:1", "text": "gm from the lab", "format": "gm", "experiment": 2})
        row = self.ledger()[0]
        self.assertEqual((row["text"], row["format"], row["experiment"]), ("gm from the lab", "gm", 2))
        self.assertEqual(self.x.requests("POST", "/2/tweets")[0].json(), {"text": "gm from the lab"})

    def test_a_reply_records_its_format(self):
        me(self.x)
        self.x.route("GET", "/2/tweets/55", {"data": {"id": "55", "text": "@teenydroid hi", "author_id": "9"}})
        publish.reply("55", {"text": "a reply", "format": "answer"})
        row = self.ledger()[0]
        self.assertEqual((row["kind"], row["format"], row["experiment"]), ("reply", "answer", None))

    def test_a_bad_tag_is_an_error_and_nothing_is_sent(self):
        with self.assertRaises(hand.HandError):
            publish.post({"anchor": "hn:1", "text": "gm", "format": "GM!!"})
        self.assertEqual(self.x.writes(), [])

    def test_an_old_ledger_gains_the_columns_and_keeps_its_rows(self):
        conn = hand.db()
        conn.executescript(
            "CREATE TABLE x_posts (id TEXT PRIMARY KEY, kind TEXT NOT NULL, text TEXT NOT NULL, in_reply_to TEXT,"
            " quote_of TEXT, media TEXT NOT NULL DEFAULT '[]', created_at REAL NOT NULL);"
            "INSERT INTO x_posts (id, kind, text, created_at) VALUES ('7', 'post', 'old', 1);"
        )
        conn.close()
        rows = self.ledger()
        self.assertEqual([(r["id"], r["format"], r["experiment"]) for r in rows], [("7", None, None)])


class ProducerTagsTest(TagsTest):
    """The studio cutover's columns: who made an own post and from what
    (`producer`, `storyline`, `shape`, `pitch_id`), kept beside it and never
    sent to X; a live ledger gains them with its rows as they were."""

    STUDIO = {"producer": "studio", "storyline": "rent", "shape": "confession", "pitch_id": "20261118T1500Z-1500-ab12cd:p3"}

    def test_a_studio_post_records_who_made_it_and_from_what(self):
        publish.post({"anchor": "stimuli:health:daily", "text": "twenty cents of thinking", "format": "image", **self.STUDIO})
        row = self.ledger()[0]
        self.assertEqual({k: row[k] for k in (*self.STUDIO, "format")}, {**self.STUDIO, "format": "image"})
        self.assertEqual(self.x.requests("POST", "/2/tweets")[0].json(), {"text": "twenty cents of thinking"})

    def test_a_thread_keeps_them_on_its_head(self):
        for i, text in enumerate(("a bet", "and the stakes")):
            self.x.route("POST", "/2/tweets", {"data": {"id": str(100 + i)}}, status=201,
                         when=lambda seen, t=text: seen.json()["text"] == t)
        publish.thread({"anchor": "evt:2", "posts": ["a bet", "and the stakes"], "format": "thread", **self.STUDIO})
        rows = {r["id"]: r for r in self.ledger()}
        self.assertEqual(rows["100"]["producer"], "studio")
        self.assertEqual(rows["100"]["pitch_id"], self.STUDIO["pitch_id"])
        self.assertIsNone(rows["101"]["producer"])

    def test_the_other_producers_and_none(self):
        texts = ("the lighthouse keeper took a day off", "a canal boat that sells only soup",
                 "the oldest tortoise had a birthday", "a library that lends out umbrellas")
        for i, producer in enumerate(("brief", "peer-quote", "legacy", None)):
            self.at(T0 + i * 6 * 3600)
            self.x.route("POST", "/2/tweets", {"data": {"id": f"20{i}"}}, status=201)
            publish.post({"anchor": f"brief:{i}", "text": texts[i], **({"producer": producer} if producer else {})})
        self.assertEqual([r["producer"] for r in self.ledger()], ["brief", "peer-quote", "legacy", None])

    def test_a_bad_producer_tag_is_an_error_and_nothing_is_sent(self):
        for bad in ({"producer": "mind"}, {"storyline": "Not A Key"}, {"shape": "x" * 41}, {"pitch_id": "has spaces"},
                    {"producer": 3}):
            with self.assertRaises(hand.HandError, msg=bad):
                publish.post({"anchor": "hn:1", "text": "gm", **bad})
        self.assertEqual(self.x.writes(), [])

    def test_a_reply_takes_no_producer(self):
        me(self.x)
        self.x.route("GET", "/2/tweets/55", {"data": {"id": "55", "text": "@teenydroid hi", "author_id": "9"}})
        with self.assertRaises(hand.HandError):
            publish.reply("55", {"text": "a reply", "producer": "studio"})

    def test_an_old_ledger_gains_the_studio_columns_and_keeps_its_rows(self):
        conn = hand.db()
        conn.executescript(
            "CREATE TABLE x_posts (id TEXT PRIMARY KEY, kind TEXT NOT NULL CHECK (kind IN ('post', 'reply', 'quote')),"
            " text TEXT NOT NULL, in_reply_to TEXT, quote_of TEXT, media TEXT NOT NULL DEFAULT '[]', created_at REAL NOT NULL,"
            " format TEXT, experiment INTEGER, anchor TEXT, thread_of TEXT, quote_author TEXT);"
            "INSERT INTO x_posts (id, kind, text, created_at, format, anchor) VALUES ('7', 'post', 'old', 1, 'take', 'hn:1');"
        )
        conn.close()
        rows = self.ledger()
        self.assertEqual([(r["id"], r["format"], r["anchor"], r["producer"], r["storyline"], r["shape"], r["pitch_id"])
                          for r in rows], [("7", "take", "hn:1", None, None, None, None)])
        publish.post({"anchor": "evt:2", "text": "a new studio post", **self.STUDIO})
        self.assertEqual([r["producer"] for r in self.ledger()], [None, "studio"])
