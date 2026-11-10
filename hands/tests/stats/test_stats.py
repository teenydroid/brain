"""hands/stats: a snapshot from fake X, Moltbook and GitHub, once a day,
sources without keys skipped, and the report and get read only rows."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from x.support import SECRETS, FakeX
from teeny_hands import hand
from teeny_hands.stats import collect

T0 = 1_789_369_200.0  # 2026-09-14T07:00:00Z
DAY = 86400.0
STATS = Path(__file__).resolve().parents[2] / "stats"


class StatsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        (self.root / "secrets").mkdir()
        self._env = dict(os.environ)
        os.environ["TEENY_ROOT"] = str(self.root)
        os.environ.pop("DRY_RUN", None)
        os.environ["TEENY_NOW"] = str(T0)
        self.fake = FakeX()
        self.addCleanup(self.fake.close)
        self.configure()
        self.fake.route("GET", "/2/users/by/username/teenydroid",
                        {"data": {"id": "1", "username": "teenydroid", "public_metrics": {"followers_count": 120, "following_count": 30, "tweet_count": 400}}})
        self.fake.route("GET", "/agents/me", {"success": True, "agent": {"name": "teenydroid", "karma": 42, "follower_count": 7}})
        self.fake.route("GET", "/users/teenydroid", {"login": "teenydroid", "followers": 9, "public_repos": 3})
        self.fake.route("GET", "/users/teenydroid/repos", [{"stargazers_count": 5}, {"stargazers_count": 2}, {"stargazers_count": 100, "fork": True}])

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)
        hand._ENV_CACHE = None
        self.tmp.cleanup()

    def configure(self, **overrides):
        values = {**SECRETS, "X_API_BASE": self.fake.base, "X_BOT_USERNAME": "teenydroid",
                  "MOLTBOOK_API_KEY": "mk", "MOLTBOOK_API_BASE": self.fake.base,
                  "GITHUB_TOKEN": "ghp_x", "GITHUB_API_BASE": self.fake.base, "DRY_RUN": "1"}
        values.update(overrides)
        (self.root / "secrets" / "teeny.env").write_text("".join(f"{k}={v}\n" for k, v in values.items() if v is not None))
        hand._ENV_CACHE = None

    def rows(self):
        return [tuple(r) for r in collect.db().execute("SELECT date, source, metric, value FROM stats ORDER BY source, metric")]

    def test_a_snapshot_writes_one_row_per_metric(self):
        out = collect.snapshot()
        self.assertEqual(out["written"], {
            "x": {"followers": 120.0, "following": 30.0, "posts": 400.0},
            "moltbook": {"karma": 42.0, "followers": 7.0},
            "github": {"followers": 9.0, "public_repos": 3.0, "stars": 7.0},
        })
        self.assertEqual(len(self.rows()), 8)
        self.assertNotIn("impressions", out["written"]["x"])

    def test_idempotent_per_day(self):
        collect.snapshot()
        self.fake.route("GET", "/agents/me", {"success": True, "agent": {"karma": 50}})
        collect.snapshot()
        rows = self.rows()
        self.assertEqual(len(rows), 8)
        self.assertIn(("2026-09-14", "moltbook", "karma", 50.0), rows)

    def test_missing_keys_skip_a_source(self):
        self.configure(MOLTBOOK_API_KEY=None, GITHUB_TOKEN=None)
        out = collect.snapshot()
        self.assertEqual(set(out["written"]), {"x"})
        self.assertEqual(out["skipped"], {"moltbook": "MOLTBOOK_API_KEY not set", "github": "GITHUB_TOKEN not set"})

    def test_a_failing_source_does_not_stop_the_others(self):
        self.fake.route("GET", "/agents/me", {"success": False, "error": "down"}, status=500)
        out = collect.snapshot()
        self.assertIn("moltbook", out["failed"])
        self.assertEqual(set(out["written"]), {"x", "github"})

    def test_report_and_get(self):
        collect.snapshot()
        os.environ["TEENY_NOW"] = str(T0 + 7 * DAY)
        self.fake.route("GET", "/2/users/by/username/teenydroid",
                        {"data": {"id": "1", "username": "teenydroid", "public_metrics": {"followers_count": 150, "following_count": 30, "tweet_count": 410}}})
        collect.snapshot()
        report = {(m["source"], m["metric"]): m for m in collect.report(7)["metrics"]}
        self.assertEqual((report[("x", "followers")]["value"], report[("x", "followers")]["delta"]), (150, 30))
        self.assertEqual(collect.get("x.followers"), {"source": "x", "metric": "followers", "value": 150, "date": "2026-09-21"})
        self.assertEqual(collect.get("karma")["value"], 42)
        with self.assertRaises(hand.HandError):
            collect.get("followers")  # three sources keep it
        with self.assertRaises(hand.Refused):
            collect.get("x.impressions")

    def test_cli(self):
        done = subprocess.run([sys.executable, "-I", "-B", str(STATS), "snapshot"], capture_output=True, text=True,
                              env=os.environ, timeout=60, stdin=subprocess.DEVNULL)
        out = json.loads(done.stdout)
        self.assertEqual((done.returncode, out["ok"], out["date"]), (0, True, "2026-09-14"))
        for secret in ("mk", "ghp_x", SECRETS["X_API_SECRET"]):
            self.assertNotIn(secret + '"', done.stdout)
        done = subprocess.run([sys.executable, "-I", "-B", str(STATS), "get", "x.nope"], capture_output=True, text=True,
                              env=os.environ, timeout=60, stdin=subprocess.DEVNULL)
        self.assertEqual(done.returncode, 2)
