"""bin/lib/provider_error.py: a failed draft turn's error, read as the kind of
model-provider failure it was, so bin/act can record it, skip cron's quick
retries of one that cannot clear (2026-11-14: HTTP 402 for three hours, each
run tried three times in twenty seconds), and bin/health can raise the alarm."""

import subprocess
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "bin" / "lib"))

import provider_error  # noqa: E402

# What the core said on the box, 2026-11-14.
LIVE_402 = ("[oh] openhuman.inference_agent_chat: driver failed: model error: local-openai returned HTTP 402"
            " (insufficient_allowance): Your wallet's spending approval does not cover this request")
LIVE_CUT = ("[oh] openhuman.inference_agent_chat: driver failed: model error: local-openai returned:"
            " provider stream ended before a completion signal")


class ProviderErrorTest(unittest.TestCase):
    def test_each_kind_of_provider_failure_has_its_class(self):
        cases = {
            LIVE_402: "payment",
            "local-openai returned HTTP 401 (invalid_api_key)": "auth",
            "local-openai API key not set": "auth",
            "local-openai returned HTTP 429: rate limit reached": "rate_limit",
            "local-openai returned HTTP 502 Bad Gateway": "server",
            LIVE_CUT: "server",
            "core unreachable: timed out": "timeout",
            "error sending request: connection refused": "connection",
        }
        for text, kind in cases.items():
            with self.subTest(text=text):
                self.assertEqual(provider_error.classify(text), kind)

    def test_a_failure_that_is_not_the_providers_has_no_class(self):
        for text in ("invalid params: message too long", "nothing to ask", ""):
            with self.subTest(text=text):
                self.assertIsNone(provider_error.classify(text))

    def test_only_no_money_and_a_refused_key_are_lasting(self):
        self.assertEqual(set(provider_error.LASTING), {"payment", "auth"})

    def test_the_command_prints_the_class_and_whether_it_lasts(self):
        def run(text):
            return subprocess.run([sys.executable, str(REPO / "bin" / "lib" / "provider_error.py")], input=text,
                                  capture_output=True, text=True, check=True).stdout.strip()
        self.assertEqual(run(LIVE_402), "payment lasting")
        self.assertEqual(run(LIVE_CUT), "server passing")
        self.assertEqual(run("invalid params"), "")


if __name__ == "__main__":
    unittest.main()
