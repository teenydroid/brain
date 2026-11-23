"""hands/call send under DRY_RUN (the box's, or the mind's rehearsal, which
the broker forces into the hand's environment): a write is checked and not
sent; a read says nothing to anyone, so it is sent and counted."""

from __future__ import annotations

import os
from unittest import mock

from .support import Scratch
from .test_guard import Recorder

from teeny_hands.guard import gate  # noqa: E402


class DryRunTest(Scratch):
    def setUp(self):
        super().setUp()
        self.svc.route("POST", "/api/v1/notes", {"id": 1})
        self.svc.route("GET", "/api/v1/posts", {"posts": []})

    def test_a_write_is_guard_checked_and_not_sent(self):
        self.configure(DRY_RUN="1", CALL_FAKE_SVC_WRITES_PER_DAY="1")
        guard = Recorder()
        with mock.patch.object(gate, "check_outbound", guard):
            for _ in range(2):  # a rehearsal spends nothing of the day
                code, out = self.write(body={"text": "hello"})
                self.assertEqual((code, out), (0, {"ok": True, "dry_run": True,
                                                   "would": {"service": "fake-svc", "method": "POST",
                                                             "path": "/notes"}}))
        self.assertEqual([c["text"] for c in guard.calls], ["hello", "hello"])
        self.assertEqual(self.svc.seen, [])
        self.assertEqual([(r["sent"], r["outcome"]) for r in self.log()], [(0, "dry_run"), (0, "dry_run")])

    def test_a_held_write_is_still_refused_in_a_rehearsal(self):
        self.configure(DRY_RUN="1")
        code, out = self.write(body={"text": "what a retard"})
        self.assertEqual((code, out["rule"]), (2, "guard"), out)
        self.assertEqual(self.svc.seen, [])

    def test_a_capped_off_write_is_refused_in_a_rehearsal(self):
        self.configure(DRY_RUN="1", CALL_FAKE_SVC_WRITES_PER_DAY="0")
        code, out = self.write(body={"text": "hello"})
        self.assertEqual((code, out["rule"]), (2, "cap"), out)

    def test_the_minds_rehearsal_forced_from_the_environment_holds_a_write(self):
        self.configure(DRY_RUN="0")
        os.environ["DRY_RUN"] = "1"  # what bin/broker serve sets while MIND_DRY_RUN is not 0
        code, out = self.write(body={"text": "hello"})
        self.assertEqual((code, out.get("dry_run")), (0, True), out)
        self.assertEqual(self.svc.seen, [])

    def test_a_read_is_sent_and_counted(self):
        self.configure(DRY_RUN="1", CALL_FAKE_SVC_READS_PER_DAY="1")
        code, out = self.get("/posts")
        self.assertEqual((code, out["status"]), (0, 200), out)
        self.assertEqual(self.get("/posts")[0], 2)
        self.assertEqual(len(self.svc.seen), 1)
