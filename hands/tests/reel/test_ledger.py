"""The media_assets rebuild that lets hands/reel record its kinds and the
`unknown` status: once, kept rows, safe when run twice at once, when
interrupted, or over an orphaned source_id. And bin/lib/playbook.py keeps a
draft from ever reaching hands/reel."""

import contextlib
import io
import os
import sqlite3
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
sys.path.insert(0, str(REPO / "hands" / "lib"))
sys.path.insert(0, str(REPO / "bin" / "lib"))

import playbook  # noqa: E402
from teeny_hands import hand  # noqa: E402
from teeny_hands.media import assets  # noqa: E402

OLD = assets.SCHEMA.replace(assets.KINDS_CHECK, "kind IN ('image', 'video')").replace(assets.STATUS_CHECK, "status IN ('pending', 'done', 'failed')")


class LedgerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "secrets").mkdir()
        (self.root / "secrets" / "teeny.env").write_text("DRY_RUN=0\n")
        self._env = dict(os.environ)
        os.environ["TEENY_ROOT"] = str(self.root)
        hand._ENV_CACHE = None
        db = sqlite3.connect(hand.state_dir() / "hands.db")
        db.executescript(OLD)
        db.execute("INSERT INTO media_assets (id, kind, status, prompt, created_at) VALUES (1, 'image', 'done', 'still', 1)")
        db.execute("INSERT INTO media_assets (id, kind, status, prompt, source_id, created_at) VALUES (2, 'video', 'done', 'moves', 1, 2)")
        db.commit()
        db.close()

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)
        hand._ENV_CACHE = None
        self.tmp.cleanup()

    def sql(self):
        db = sqlite3.connect(hand.state_dir() / "hands.db")
        try:
            return db.execute("SELECT sql FROM sqlite_master WHERE name = 'media_assets'").fetchone()[0]
        finally:
            db.close()

    def rows(self):
        db = sqlite3.connect(hand.state_dir() / "hands.db")
        try:
            return db.execute("SELECT id, kind, source_id FROM media_assets ORDER BY id").fetchall()
        finally:
            db.close()

    def test_the_rebuild_happens_once_and_keeps_every_row(self):
        assets._db().close()
        first = self.sql()
        self.assertIn(assets.KINDS_CHECK, first)
        self.assertIn(assets.STATUS_CHECK, first)
        with mock.patch.object(assets.hand, "transaction", side_effect=AssertionError("rebuilt twice")):
            assets._db().close()
        self.assertEqual(self.sql(), first)
        self.assertEqual(self.rows(), [(1, "image", None), (2, "video", 1)])

    def test_two_migrators_at_once(self):
        errors = []

        def run():
            try:
                assets._db().close()
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=run) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        self.assertIn(assets.KINDS_CHECK, self.sql())
        self.assertEqual(self.rows(), [(1, "image", None), (2, "video", 1)])

    def test_an_interrupted_rebuild_changes_nothing_and_the_next_call_finishes_it(self):
        conn = hand.open_db(assets.SCHEMA)
        real = conn.execute

        class Crash(sqlite3.Connection):
            pass

        calls = {"n": 0}

        def execute(sql, *args):
            if sql.startswith("DROP TABLE media_assets") and "IF EXISTS" not in sql:
                raise sqlite3.OperationalError("disk I/O error")
            calls["n"] += 1
            return real(sql, *args)

        proxy = mock.Mock(wraps=conn)
        proxy.execute.side_effect = execute
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            assets.allow_reel_kinds(proxy)
        conn.close()
        self.assertIn("not rebuilt", err.getvalue())
        self.assertNotIn("'reel'", self.sql())
        self.assertEqual(self.rows(), [(1, "image", None), (2, "video", 1)])
        # hands/image still works on the unchanged ledger.
        conn = assets._db()
        self.assertIn(assets.KINDS_CHECK, self.sql())
        conn.close()

    def test_an_orphaned_source_id_is_kept_and_reported(self):
        db = sqlite3.connect(hand.state_dir() / "hands.db")
        db.execute("INSERT INTO media_assets (id, kind, status, prompt, source_id, created_at) VALUES (3, 'video', 'done', 'lost', 99, 3)")
        db.commit()
        db.close()
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            conn = assets._db()
        conn.close()
        self.assertIn("1 row(s) name a source_id that is gone", err.getvalue())
        self.assertIn(assets.KINDS_CHECK, self.sql())
        self.assertEqual(self.rows(), [(1, "image", None), (2, "video", 1), (3, "video", 99)])

    def test_a_ledger_that_cannot_be_rebuilt_still_serves_images(self):
        err = io.StringIO()
        with mock.patch.object(assets.re, "sub", return_value="CREATE TABLE media_assets_new (broken"), contextlib.redirect_stderr(err):
            conn = assets._db()
        try:
            self.assertIn("not rebuilt", err.getvalue())
            assets._check_caps(conn, "image", 0)
            with hand.transaction(conn):
                conn.execute("INSERT INTO media_assets (kind, status, prompt, created_at) VALUES ('image', 'pending', 'x', 4)")
        finally:
            conn.close()


class PlaybookTest(unittest.TestCase):
    def test_a_draft_can_never_reach_hands_reel(self):
        self.assertIn("reel", playbook.NOT_OUTPUTS)
        with tempfile.TemporaryDirectory() as tmp:
            brain = Path(tmp)
            (brain / "playbooks").mkdir()
            (brain / "playbooks" / "clip.md").write_text('+++\ndescription = "x"\noutput = "hand:reel make"\n+++\nmake one\n')
            with self.assertRaises(playbook.PlaybookError) as no:
                playbook.load(brain, "clip", REPO / "hands")
            self.assertIn("hands/reel cannot be an output", str(no.exception))


if __name__ == "__main__":
    unittest.main()
