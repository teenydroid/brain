"""hands/chart: candles from a fake public endpoint, a PNG out of the
standard library, and the numbers a post may quote."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))

from teeny_hands import hand
from teeny_hands.chart import candles, render

T0 = 1789369200.0

CHART = Path(__file__).resolve().parents[1] / "chart"


class FakeChart:
    """The public candle endpoint, served from localhost."""

    def __init__(self) -> None:
        import http.server
        import threading

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                if self.path.startswith("/coins/tiny/ohlc"):
                    body = json.dumps(outer.rows).encode()
                    self.send_response(outer.status)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(body)
                else:
                    self.send_response(404)
                    self.end_headers()

            def log_message(self, *args: object) -> None:
                pass

        self.rows: list = []
        self.status = 200
        outer = self
        self.server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        self.base = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()

    def candles(self, count: int = 8) -> None:
        self.rows = [
            [
                int((T0 + index * 4 * 3600) * 1000),
                1.0 + index * 0.1,
                1.2 + index * 0.1,
                0.9 + index * 0.1,
                1.1 + index * 0.1,
            ]
            for index in range(count)
        ]


class ChartTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        (self.root / "secrets").mkdir()
        self._env = dict(os.environ)
        os.environ["TEENY_ROOT"] = str(self.root)
        os.environ["TEENY_NOW"] = str(T0)
        os.environ.pop("DRY_RUN", None)
        self.fake = FakeChart()
        self.addCleanup(self.fake.close)
        self.configure()
        self.fake.candles()

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self._env)
        hand._ENV_CACHE = None
        self.tmp.cleanup()

    def configure(self, **overrides: str) -> None:
        values = {"CHART_API_BASE": self.fake.base, "DRY_RUN": "1"}
        values.update(overrides)
        (self.root / "secrets" / "teeny.env").write_text("".join(f"{k}={v}\n" for k, v in values.items()))
        hand._ENV_CACHE = None

    def test_fetch_stores_one_row_per_candle_and_replaces_the_window(self) -> None:
        out = candles.fetch("tiny", 7)
        self.assertEqual((out["candles"], out["days"]), (8, 7))
        self.assertEqual(len(candles.rows("tiny", 7)), 8)
        self.fake.candles(4)
        candles.fetch("tiny", 7)
        self.assertEqual(len(candles.rows("tiny", 7)), 4)

    def test_a_bad_answer_is_an_error_and_stores_nothing(self) -> None:
        self.fake.rows = ["nope"]
        with self.assertRaises(hand.HandError):
            candles.fetch("tiny", 7)
        self.assertEqual(candles.rows("tiny", 7), [])
        self.fake.status = 500
        with self.assertRaises(Exception):
            candles.fetch("tiny", 7)
        self.assertEqual(candles.rows("tiny", 7), [])

    def test_render_draws_a_real_png_of_the_stored_candles(self) -> None:
        candles.fetch("tiny", 7)
        out = render.render(candles.rows("tiny", 7), render.path_for("tiny", 7))
        path = Path(out["path"])
        raw = path.read_bytes()
        self.assertEqual(raw[:8], b"\x89PNG\r\n\x1a\n")
        self.assertGreater(len(raw), 500)
        self.assertEqual((out["width"], out["height"], out["candles"]), (780, 360, 8))
        self.assertEqual(out["last_close"], 1.8)
        self.assertEqual(out["change_pct"], 80.0)
        self.assertEqual(out["high"], 1.9)
        self.assertEqual(out["low"], 0.9)

    def test_render_refuses_without_candles(self) -> None:
        with self.assertRaises(hand.Refused):
            render.render([], render.path_for("tiny", 7))

    def test_read_fetches_renders_and_answers_the_numbers(self) -> None:
        out = json.loads(
            subprocess.run(
                [sys.executable, "-I", "-B", str(CHART), "read"],
                capture_output=True,
                text=True,
                env=os.environ,
                timeout=60,
                stdin=subprocess.DEVNULL,
            ).stdout
        )
        self.assertTrue(out["ok"])
        self.assertEqual(out["candles"], 8)
        self.assertEqual(out["last_close"], 1.8)
        self.assertTrue(Path(out["path"]).exists())

    def test_the_cli_refuses_a_bad_verb_and_a_bad_coin(self) -> None:
        done = subprocess.run(
            [sys.executable, "-I", "-B", str(CHART), "nope"],
            capture_output=True,
            text=True,
            env=os.environ,
            timeout=60,
            stdin=subprocess.DEVNULL,
        )
        self.assertEqual(done.returncode, 1)
        self.assertIn("bad arguments", done.stdout)
        done = subprocess.run(
            [sys.executable, "-I", "-B", str(CHART), "read", "--coin", "../etc"],
            capture_output=True,
            text=True,
            env=os.environ,
            timeout=60,
            stdin=subprocess.DEVNULL,
        )
        self.assertEqual(done.returncode, 1)
        self.assertIn("bad arguments", done.stdout)


if __name__ == "__main__":
    unittest.main()
