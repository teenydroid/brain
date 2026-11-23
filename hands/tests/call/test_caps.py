"""hands/call send: the day's caps, read from secrets/, and the call log."""

from __future__ import annotations

import json
import sqlite3

from .support import DAY, KEY, T0, Scratch


class OffTest(Scratch):
    def test_reads_and_writes_are_off_until_the_operator_sets_a_number(self):
        self.svc.route("GET", "/api/v1/posts", {"posts": []})
        self.svc.route("POST", "/api/v1/notes", {"id": 1})
        for value in (None, "0", "lots", "-3"):
            self.configure(CALL_FAKE_SVC_READS_PER_DAY=value, CALL_FAKE_SVC_WRITES_PER_DAY=value)
            code, out = self.get("/posts")
            self.assertEqual((code, out.get("rule")), (2, "cap"), (value, out))
            self.assertIn("CALL_FAKE_SVC_READS_PER_DAY", out["refused"])
            code, out = self.write(body={"n": 1})
            self.assertEqual((code, out.get("rule")), (2, "cap"), (value, out))
            self.assertIn("CALL_FAKE_SVC_WRITES_PER_DAY", out["refused"])
        self.assertEqual(self.svc.seen, [])

    def test_the_environment_cannot_switch_a_cap_on(self):
        import os

        self.configure(CALL_FAKE_SVC_READS_PER_DAY=None)
        os.environ["CALL_FAKE_SVC_READS_PER_DAY"] = "100"
        code, out = self.get("/posts")
        self.assertEqual(code, 2, out)
        self.assertEqual(self.svc.seen, [])


class CapTest(Scratch):
    def test_the_read_cap_counts_reads_sent_and_resets_each_utc_day(self):
        self.configure(CALL_FAKE_SVC_READS_PER_DAY="2")
        self.svc.route("GET", "/api/v1/posts", {"posts": []})
        self.assertEqual(self.get("/posts")[0], 0)
        self.assertEqual(self.get("/posts")[0], 0)
        code, out = self.get("/posts")
        self.assertEqual((code, out["rule"]), (2, "cap"))
        self.assertIn("CALL_FAKE_SVC_READS_PER_DAY", out["refused"])
        self.assertEqual(len(self.svc.seen), 2)
        # Writes are counted apart.
        self.svc.route("POST", "/api/v1/notes", {"id": 1})
        self.assertEqual(self.write(body={"n": 1})[0], 0)
        self.at(T0 + DAY)  # 2026-09-15
        self.assertEqual(self.get("/posts")[0], 0)

    def test_the_write_cap_counts_writes_sent(self):
        self.configure(CALL_FAKE_SVC_WRITES_PER_DAY="1")
        self.svc.route("POST", "/api/v1/notes", {"id": 1})
        self.svc.route("GET", "/api/v1/posts", {"posts": []})
        self.assertEqual(self.write(body={"n": 1})[0], 0)
        code, out = self.write("DELETE", "/notes/1")
        self.assertEqual((code, out["rule"]), (2, "cap"))
        self.assertIn("CALL_FAKE_SVC_WRITES_PER_DAY", out["refused"])
        self.assertEqual(self.get("/posts")[0], 0)
        self.assertEqual([s.method for s in self.svc.seen], ["POST", "GET"])

    def test_a_call_refused_before_it_was_sent_spends_nothing(self):
        self.configure(CALL_FAKE_SVC_READS_PER_DAY="1", CALL_FAKE_SVC_WRITES_PER_DAY="1")
        self.svc.route("GET", "/api/v1/posts", {"posts": []})
        self.svc.route("POST", "/api/v1/notes", {"id": 1})
        self.assertEqual(self.get("/nope/../x")[0], 2)
        self.assertEqual(self.write("POST", "/posts")[0], 2)
        self.assertEqual(self.write(body={"text": "a" * 20001})[0], 2)
        self.assertEqual(self.get("/posts")[0], 0)
        self.assertEqual(self.write(body={"n": 1})[0], 0)

    def test_a_call_the_service_failed_was_still_sent_and_counts(self):
        self.configure(CALL_FAKE_SVC_WRITES_PER_DAY="1")
        self.svc.route("POST", "/api/v1/notes", {"error": "boom"}, status=500)
        self.assertEqual(self.write(body={"n": 1})[0], 1)
        self.assertEqual(self.write(body={"n": 1})[0], 2)
        self.assertEqual(len(self.svc.seen), 1)

    def test_a_cap_above_its_most_is_held_to_it(self):
        self.configure(CALL_FAKE_SVC_WRITES_PER_DAY="100000")
        self.svc.route("POST", "/api/v1/notes", {"id": 1})
        self.assertEqual(self.write(body={"n": 1})[0], 0)
        conn = sqlite3.connect(self.root / "state" / "hands.db")
        with conn:
            conn.executemany("INSERT INTO call_log (at, service, method, path, kind, sent, outcome) "
                             "VALUES (?, 'fake-svc', 'POST', '/notes', 'write', 1, 'ok')", [(T0,)] * 499)
        conn.close()
        code, out = self.write(body={"n": 1})
        self.assertEqual((code, out["rule"]), (2, "cap"), out)
        self.assertIn("500", out["refused"])


class LogTest(Scratch):
    def test_each_call_is_one_row_without_the_body_or_the_key(self):
        self.svc.route("GET", "/api/v1/posts", {"posts": []})
        self.svc.route("POST", "/api/v1/notes", {"id": 1})
        self.get("/posts", query={"q": "find me"})
        self.write(body={"text": "a note about lobsters " * 20})
        self.write("POST", "/posts")
        self.call({"service": "nowhere", "method": "GET", "path": "/x"})
        rows = self.log()
        self.assertEqual([(r["service"], r["method"], r["path"], r["kind"], r["sent"], r["outcome"]) for r in rows], [
            ("fake-svc", "GET", "/posts", "read", 1, "ok"),
            ("fake-svc", "POST", "/notes", "write", 1, "ok"),
            ("fake-svc", "POST", "/posts", "write", 0, "refused"),
            ("nowhere", None, None, None, 0, "refused"),
        ])
        self.assertEqual(rows[0]["status"], 200)
        self.assertIn("not allowed", rows[2]["detail"])
        text = json.dumps(rows)
        for never in ("lobsters", "find me", KEY):
            self.assertNotIn(never, text)
        self.assertEqual(self.leaked(), [])

    def test_a_field_too_long_or_strange_is_clipped_in_the_log(self):
        self.call({"service": "s" * 500, "method": "GET", "path": "/x"})
        (row,) = self.log()
        self.assertLessEqual(len(row["service"]), 40)
        self.assertLessEqual(len(row["detail"]), 200)
