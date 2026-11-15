"""hands/moltbook keeps what it wrote and the lab tags with it: a post's
body, `format` and `experiment` in moltbook_posts, a comment's in
moltbook_comments; a ledger from before those columns gains them and keeps
its rows."""

from __future__ import annotations

from .support import Scratch
from teeny_hands import hand
from teeny_hands.moltbook import actions, rules


def rows(table: str) -> list[dict]:
    return [dict(r) for r in rules.db().execute(f"SELECT * FROM {table} ORDER BY id")]


class TagsTest(Scratch):
    def test_a_post_keeps_its_body_format_and_experiment(self):
        self.created()
        actions.post({"submolt": "general", "title": "A is B, not C", "body": "the body", "format": "claim", "experiment": 3})
        row = rows("moltbook_posts")[0]
        self.assertEqual((row["title"], row["body"], row["format"], row["experiment"]), ("A is B, not C", "the body", "claim", 3))
        # The tags are not sent to Moltbook.
        self.assertNotIn("format", self.mb.requests("POST", "/posts")[0].json())

    def test_an_untagged_post_keeps_nulls(self):
        self.created()
        actions.post({"submolt": "general", "title": "plain", "body": "x"})
        row = rows("moltbook_posts")[0]
        self.assertEqual((row["format"], row["experiment"]), (None, None))

    def test_a_comment_keeps_its_body_and_format(self):
        self.a_post("x1")
        self.created("comment", "c1", path="/posts/x1/comments")
        actions.comment({"post_id": "x1", "body": "a counter-example", "format": "counter"})
        row = rows("moltbook_comments")[0]
        self.assertEqual((row["body"], row["format"], row["experiment"]), ("a counter-example", "counter", None))

    def test_a_bad_tag_is_an_error_before_anything_is_sent(self):
        for tags in ({"format": "Not A Tag!"}, {"experiment": 0}, {"experiment": "x"}, {"format": 3}):
            with self.assertRaises(hand.HandError):
                actions.post({"submolt": "general", "title": "t", **tags})
        self.assertEqual(self.mb.seen, [])

    def test_an_old_ledger_gains_the_columns_and_keeps_its_rows(self):
        conn = hand.db()
        conn.executescript(
            "CREATE TABLE moltbook_posts (id INTEGER PRIMARY KEY AUTOINCREMENT, post_id TEXT, at REAL NOT NULL,"
            " submolt TEXT NOT NULL, title TEXT NOT NULL, status TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '{}');"
            "INSERT INTO moltbook_posts (post_id, at, submolt, title, status) VALUES ('old', 1, 'general', 'old one', 'verified');"
        )
        conn.close()
        got = rows("moltbook_posts")
        self.assertEqual(len(got), 1)
        self.assertEqual((got[0]["post_id"], got[0]["body"], got[0]["format"]), ("old", None, None))
