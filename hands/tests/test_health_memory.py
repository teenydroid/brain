"""bin/health: the alarm on Teeny's memory losing its embeddings. From
2026-11-20 to -23 CortexDB's embedding seller (venice-embed-1 on Surplus)
answered `503 no_healthy_sellers` for hours at a time, some 46,000 failed
calls in all; recall degraded, a turn's pack timed out, and nothing said so.
CortexDB's own `/v1/admin/ready` reports the embedding provider's last
success and failure: no success for MEMORY_OUTAGE_SECS, on
MEMORY_FAILURES_TO_ALARM checks in a row, raises one `health` alarm and one
issue; the first success after that clears both. A blip with a recent
success is `degraded` on the line and raises nothing. Against a fake
CortexDB, a fake core, a fake GitHub and a pinned clock (test_health.py's
scaffolding)."""

import unittest

import test_health
from test_health import ISSUES, QUARTER, health
from x.support import FakeX

READY = "/v1/admin/ready"
BASE = test_health.HealthTest


def ready(ok: bool, last_ok: float, last_fail: float | None = None, fails: int = 0) -> dict:
    """CortexDB's ready answer, as v0.10.4 gives it (2026-11-23)."""
    return {"ready": True, "degraded": not ok, "checks": {"embeddings": {
        "ok": ok, "ok_count": 3308516, "fail_count": fails,
        "last_success_ms": int(last_ok * 1000),
        "last_failure_ms": int(last_fail * 1000) if last_fail else None,
        "last_error": "embedding provider error: server error (503): no_healthy_sellers" if not ok else None,
    }}}


class MemoryAlarmTest(unittest.TestCase):
    setUp_base = BASE.setUp
    tearDown = BASE.tearDown
    configure = BASE.configure
    at = BASE.at
    state = BASE.state
    inbox = BASE.inbox

    def setUp(self):
        self.setUp_base()
        self.cortex = FakeX()
        self.addCleanup(self.cortex.close)
        (self.root / "secrets" / "spine.env").write_text(f"CORTEX_API_URL={self.cortex.base}\n")

    def answer(self, body, status: int = 200) -> None:
        self.cortex.routes = []
        self.cortex.route("GET", READY, body, status=status)

    def alarms(self) -> list[dict]:
        return [s for s in self.inbox() if s["kind"] == "alarm" and s["ref"].startswith("memory:")]

    def now(self, offset: float = 0) -> float:
        return test_health.T0 + offset

    def test_working_embeddings_raise_nothing(self):
        self.answer(ready(True, self.now(-30)))
        out = health.check()
        self.assertEqual(out["memory"]["embeddings"], "ok")
        self.assertIsNone(out["memory_alarm"])
        self.assertEqual((self.alarms(), self.gh.requests("POST", ISSUES)), ([], []))

    def test_a_blip_with_a_recent_success_is_degraded_not_an_alarm(self):
        for n in range(3):
            self.at(n * QUARTER)
            self.answer(ready(False, self.now(n * QUARTER - 20), self.now(n * QUARTER - 5), fails=12 * n))
            out = health.check()
        self.assertEqual(out["memory"]["embeddings"], "degraded")
        self.assertIsNone(out["memory_alarm"])
        self.assertEqual(self.alarms(), [])

    def test_no_embedding_for_two_checks_raises_one_alarm_and_one_issue(self):
        self.answer(ready(False, self.now(-3600), self.now(-5), fails=40000))
        first = health.check()
        self.assertEqual(first["memory"]["embeddings"], "down")
        self.assertIsNone(first["memory_alarm"], "one check is not an alarm")
        self.at(QUARTER)
        self.answer(ready(False, self.now(-3600), self.now(QUARTER - 5), fails=40500))
        out = health.check()
        self.assertEqual(out["memory_alarm"], "raised")
        self.assertEqual(out["memory"]["embed_failures"], 500)
        (alarm,) = self.alarms()
        self.assertIn("memory", alarm["text"])
        self.assertIn("recall", alarm["text"])
        (opened,) = self.gh.requests("POST", ISSUES)
        self.assertIn("embedding", opened.json()["title"])
        # The next check files nothing more.
        self.at(2 * QUARTER)
        self.assertEqual(health.check()["memory_alarm"], "open")
        self.assertEqual((len(self.gh.requests("POST", ISSUES)), len(self.alarms())), (1, 1))

    def test_an_unreachable_cortexdb_is_down(self):
        self.answer({"error": "boom"}, status=503)
        self.assertEqual(health.check()["memory"]["embeddings"], "unreachable")
        self.at(QUARTER)
        self.assertEqual(health.check()["memory_alarm"], "raised")

    def test_a_success_clears_it_and_closes_the_issue(self):
        self.answer(ready(False, self.now(-3600), self.now(-5)))
        health.check()
        self.at(QUARTER)
        self.assertEqual(health.check()["memory_alarm"], "raised")
        self.at(2 * QUARTER)
        self.answer(ready(True, self.now(2 * QUARTER - 10)))
        out = health.check()
        self.assertEqual(out["memory_alarm"], "cleared")
        self.assertEqual(len(self.gh.requests("PATCH", f"{ISSUES}/41")), 1)
        self.assertTrue(any(s["kind"] == "recovered" and s["ref"].startswith("memory:") for s in self.inbox()))
        self.assertIsNone(self.state()["memory_alarm"])

    def test_an_embeddings_answer_that_is_not_an_object_is_unreachable_not_a_crash(self):
        # Live shape: {"checks": {"embeddings": [1]}} raised AttributeError after the guarded
        # block, so the check died before the state was saved and monitoring stopped.
        for malformed in ({"checks": {"embeddings": [1]}}, {"checks": {"embeddings": "ok"}}, ["not", "an", "object"]):
            self.answer(malformed)
            self.assertEqual(health.check()["memory"]["embeddings"], "unreachable", malformed)
        self.at(QUARTER)
        self.assertEqual(health.check()["memory_alarm"], "raised")

    def test_a_failed_close_is_retried_and_the_recovery_is_told_once(self):
        # Live shape: a refused close dropped the alarm and its issue number, so the
        # issue stayed open after memory recovered and nothing could retry it.
        self.answer(ready(False, self.now(-3600), self.now(-5)))
        health.check()
        self.at(QUARTER)
        self.assertEqual(health.check()["memory_alarm"], "raised")
        self.at(2 * QUARTER)
        self.answer(ready(True, self.now(2 * QUARTER - 10)))
        self.gh.route("PATCH", f"{ISSUES}/41", {"message": "server error"}, status=500)
        out = health.check()
        self.assertEqual(out["memory_alarm"], "closing")
        self.assertEqual(self.state()["memory_alarm"]["issue"], 41)
        self.at(3 * QUARTER)
        self.gh.route("PATCH", f"{ISSUES}/41", {"number": 41, "state": "closed"})
        self.assertEqual(health.check()["memory_alarm"], "cleared")
        self.assertIsNone(self.state()["memory_alarm"])
        recovered = [s for s in self.inbox() if s["kind"] == "recovered" and s["ref"].startswith("memory:")]
        self.assertEqual(len(recovered), 1)

    def test_no_memory_configured_is_no_check(self):
        (self.root / "secrets" / "spine.env").write_text("SPINE_MODEL=m\n")
        out = health.check()
        self.assertIsNone(out["memory"])
        self.assertIsNone(out["memory_alarm"])
        self.assertEqual(self.cortex.seen, [])


if __name__ == "__main__":
    unittest.main()
