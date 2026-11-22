"""hands/remake used: the studio marks a source after buying its keys, so
charts leaves it out and study refuses it. A write, so a dry room only
says what it would do."""

from __future__ import annotations

import contextlib
import io
import json
from unittest import mock

from .support import FakeIngest, Scratch, entry
from teeny_hands import hand
from teeny_hands.remake import charts, cli, ledger

BODY = {"video_id": "7300", "source": "tiktok", "url": "https://www.tiktok.com/@someone/video/7300",
        "title": "a dance", "study_id": "tt-7300"}


class UsedTest(Scratch):
    def run_cli(self, body):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), mock.patch("sys.stdin", io.StringIO(json.dumps(body))):
            code = cli.main(["used"])
        return code, json.loads(out.getvalue())

    def test_a_dry_room_says_what_it_would_mark_and_writes_nothing(self):
        code, got = self.run_cli(BODY)
        self.assertEqual(code, 0)
        self.assertTrue(got["ok"])
        self.assertTrue(got["dry_run"])
        self.assertEqual(got["would_mark"]["video_id"], "7300")
        self.assertFalse(ledger.seen("7300"))

    def test_live_it_marks_the_source_and_answers_ok(self):
        self.secrets("DRY_RUN=0\n")
        code, got = self.run_cli(BODY)
        self.assertEqual((code, got["ok"], got["marked"]), (0, True, True))
        self.assertNotIn("dry_run", got)
        self.assertTrue(ledger.seen("7300"))
        code, again = self.run_cli(BODY)
        self.assertEqual((code, again["ok"], again["marked"]), (0, True, False))

    def test_charts_then_leaves_the_marked_source_out(self):
        self.secrets("DRY_RUN=0\nREMAKE_TIKTOK_ACCOUNTS=someone\n")
        ingest = FakeIngest()
        ingest.lists["https://www.tiktok.com/@someone"] = [entry("7300"), entry("7301")]
        self.run_cli(BODY)
        got = charts.top("k", 10, ingest=ingest, get_json=lambda url: {})
        self.assertEqual([c["id"] for c in got["candidates"]], ["chart:tt:7301"])

    def test_the_body_is_checked(self):
        self.secrets("DRY_RUN=0\n")
        bad = [{}, {**BODY, "video_id": "../x"}, {**BODY, "source": "vimeo"}, {**BODY, "url": "http://evil.example/x"},
               {**BODY, "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"}, {**BODY, "study_id": "../x"},
               {**BODY, "title": 5}, {**BODY, "extra": 1}, {k: v for k, v in BODY.items() if k != "url"}]
        for body in bad:
            with self.subTest(body=body):
                code, got = self.run_cli(body)
                self.assertEqual(code, 1)
                self.assertFalse(got["ok"])
        self.assertFalse(ledger.seen("7300"))

    def test_title_and_study_id_are_optional_and_the_title_is_cleaned(self):
        self.secrets("DRY_RUN=0\n")
        short = {k: BODY[k] for k in ("video_id", "source", "url")}
        self.assertEqual(self.run_cli(short)[0], 0)
        self.assertEqual(self.run_cli({**BODY, "video_id": "7302", "title": "t\x00i‮tle"})[0], 0)
        conn = hand.db()
        try:
            self.assertEqual(conn.execute("SELECT title FROM remake_sources WHERE video_id = '7302'").fetchone()[0], "title")
        finally:
            conn.close()
