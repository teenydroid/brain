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
        publish.post({"text": "gm from the lab", "format": "gm", "experiment": 2})
        row = self.ledger()[0]
        self.assertEqual((row["text"], row["format"], row["experiment"]), ("gm from the lab", "gm", 2))
        self.assertEqual(self.x.requests("POST", "/2/tweets")[0].json(), {"text": "gm from the lab"})

    def test_a_reply_records_its_format(self):
        me(self.x)
        self.x.route("GET", "/2/tweets/55", {"data": {"id": "55", "text": "hi", "author_id": "9"}})
        publish.reply("55", {"text": "a reply", "format": "answer"})
        row = self.ledger()[0]
        self.assertEqual((row["kind"], row["format"], row["experiment"]), ("reply", "answer", None))

    def test_a_bad_tag_is_an_error_and_nothing_is_sent(self):
        with self.assertRaises(hand.HandError):
            publish.post({"text": "gm", "format": "GM!!"})
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
