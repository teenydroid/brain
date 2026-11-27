"""The /attest grading resource: an unpaid call gets the frozen price and
the corpus count; a settled payment grades the body and writes one inflow
row, a `sale` with ref `x402:attest`; a body that can never grade is
refused before payment (nobody pays for a doomed call); a paid call whose
grading fails is recorded (the money settled) and answered with a
refundable 422, never a silent 200. The verdict is never free. Reuses the /stats harness: the core, the chain and the
facilitator are faked; Teeny's wallet signs nothing."""

from __future__ import annotations

import base64
import io
import json
import os
import socket
import subprocess
import sys
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from wallet.support import OWN, USDC_MAINNET
from wallet.test_paywall import PRICE, TX, PaywallTest, header
from teeny_hands import hand
from teeny_hands.wallet import paywall

ATTEST_PRICE = 10_000  # evals/prices.toml: attest_grade_usdc = "0.01"
DESCRIPTION = "One challenge answer graded against the challenge text (attest corpus, 23 frames)"

TEXT = "What is 32 times 4?"
GOOD = {"text": TEXT, "operation": "multiply 32 by 4", "answer": "128"}
BAD = {"text": "What is 32 plus 4?", "operation": "multiply 32 by 4", "answer": "36"}


class AttestTest(PaywallTest):
    def post(self, body=None, payment=None):
        return paywall.handle("POST", "/attest", {"X-PAYMENT": payment} if payment else {}, body)

    def test_an_unpaid_call_gets_the_attest_price_and_the_corpus_count(self):
        status, _, body = self.post(GOOD)
        self.assertEqual(status, 402)
        (req,) = body["accepts"]
        self.assertEqual((req["scheme"], req["network"], req["maxAmountRequired"], req["payTo"], req["asset"]),
                         ("exact", "solana", str(ATTEST_PRICE), OWN, USDC_MAINNET))
        self.assertEqual(req["description"], DESCRIPTION)
        self.assertEqual(self.facilitator.calls, [])

    def test_an_unpaid_call_never_carries_the_verdict(self):
        status, _, body = self.post(GOOD)
        self.assertEqual(status, 402)
        self.assertNotIn("verdict", body)
        self.assertNotIn("attested_operations", body)

    def test_an_ungradeable_body_is_refused_for_free(self):
        for body in ({"text": "x"}, {"operation": "add"}, {"text": "", "operation": "add"},
                     {"text": "x" * 4001, "operation": "add"}, "not an object", None):
            status, _, resp = self.post(body)
            self.assertEqual(status, 402, body)
            self.assertIn("accepts", resp)
        self.assertEqual(self.facilitator.calls, [])

    def test_a_settled_payment_grades_and_records_the_sale(self):
        status, extra, body = self.post(GOOD, header())
        self.assertEqual(status, 200, body)
        self.assertEqual(body["verdict"], "attested")
        self.assertEqual(body["applied_operation"], "multiply")
        self.assertEqual(body["corpus_frames"], 23)
        self.assertIn("phrase 'times' near", body["attested_operations"]["multiply"][0])
        receipt = json.loads(base64.b64decode(extra["X-PAYMENT-RESPONSE"]))
        self.assertEqual(receipt["transaction"], TX)
        (row,) = self.inflows()
        self.assertEqual((row["signature"], row["category"], row["ref"], row["amount_raw"]),
                         (TX, "sale", "x402:attest", ATTEST_PRICE))
        # Teeny's wallet moved nothing and signed nothing.
        self.assertEqual(self.moved(), [])

    def test_an_ungradeable_body_is_refused_before_a_payment_reaches_the_facilitator(self):
        # Regression: the shape check used to run only when no X-PAYMENT came,
        # so a buyer who paid with a bad body was charged and then got a 422.
        for body in (None, [], {"text": TEXT}, {"text": TEXT, "operation": ""}, {"text": "x" * 4001, "operation": "multiply"}):
            with self.subTest(body=body):
                status, _, got = self.post(body, header())
                self.assertEqual(status, 402)
                self.assertNotIn("verdict", got)
        self.assertEqual(self.facilitator.calls, [])
        self.assertEqual(self.inflows(), [])

    def test_a_wrong_operation_still_costs_and_still_says_not_attested(self):
        status, _, body = self.post(BAD, header())
        self.assertEqual(status, 200, body)
        self.assertEqual(body["verdict"], "not-attested")
        (row,) = self.inflows()
        self.assertEqual((row["category"], row["ref"]), ("sale", "x402:attest"))

    def test_a_paid_call_whose_grading_fails_is_refundable_not_silent(self):
        original = paywall.grade_request

        def broken(body):
            raise RuntimeError("grader exploded")

        paywall.grade_request = broken
        try:
            status, _, body = self.post(GOOD, header())
        finally:
            paywall.grade_request = original
        self.assertEqual(status, 422, body)
        self.assertEqual(body["payment"], TX)
        # The money arrived and is on the books; the 422 is what a person
        # refunds from.
        (row,) = self.inflows()
        self.assertEqual((row["category"], row["ref"]), ("sale", "x402:attest"))

    def test_get_on_attest_is_405(self):
        self.assertEqual(paywall.handle("GET", "/attest", {})[0], 405)

    def test_unknown_paths_stay_404(self):
        self.assertEqual(paywall.handle("POST", "/other", {}, GOOD)[0], 404)

    def test_stats_price_is_untouched(self):
        status, _, body = paywall.handle("GET", "/stats", {})
        self.assertEqual(status, 402)
        (req,) = body["accepts"]
        self.assertEqual(req["maxAmountRequired"], str(PRICE))

    # -- endpoint off by default, payTo, replay --------------------------

    def test_attest_is_off_until_switched_on(self):
        self.configure(X402_ENDPOINT=None)
        self.assertEqual(self.post(GOOD)[0], 404)
        self.assertEqual(self.post(GOOD, header())[0], 404)
        self.assertEqual(self.facilitator.calls, [])
        self.assertEqual(self.inflows(), [])

    def test_attest_pays_only_teenys_own_address(self):
        _, _, body = self.post(GOOD)
        (req,) = body["accepts"]
        self.assertEqual(req["payTo"], OWN)
        self.assertEqual(req["resource"].rsplit("/", 1)[1], "attest")
        self.assertEqual(self.post(GOOD, header())[0], 200)
        sent = self.facilitator.calls[0][1]["paymentRequirements"]
        self.assertEqual((sent["payTo"], sent["maxAmountRequired"]), (OWN, str(ATTEST_PRICE)))
        self.assertEqual(self.moved(), [])

    def test_a_replayed_attest_payment_gets_nothing(self):
        self.assertEqual(self.post(GOOD, header())[0], 200)
        status, _, body = self.post(GOOD, header())
        self.assertEqual((status, body["error"]), (402, "that payment was already used"))
        self.assertNotIn("verdict", body)
        self.assertEqual(len(self.inflows()), 1)

    # -- the request body cap --------------------------------------------

    def test_read_body_refuses_before_reading(self):
        class Tripwire(io.BytesIO):
            def read(self, *a):
                raise AssertionError("the body was read")

        for headers, status in (({}, 411), ({"Content-Length": ""}, 411), ({"Content-Length": "abc"}, 400),
                                ({"Content-Length": "-1"}, 400), ({"Content-Length": "1_0"}, 400),
                                ({"Content-Length": "1.5"}, 400), ({"Content-Length": "\u0661"}, 400),
                                ({"Content-Length": str(paywall.MAX_BODY + 1)}, 413),
                                ({"content-length": "99999999999"}, 413)):
            with self.subTest(headers=headers), self.assertRaises(paywall.BodyRefused) as got:
                paywall.read_body(headers, Tripwire())
            self.assertEqual(got.exception.status, status)

    def test_read_body_never_reads_past_what_it_declared(self):
        stream = io.BytesIO(b"x" * 100)
        self.assertEqual(paywall.read_body({"Content-Length": "10"}, stream), b"x" * 10)
        self.assertEqual(stream.tell(), 10)
        self.assertEqual(paywall.read_body({"Content-Length": "0"}, io.BytesIO()), b"")
        edge = io.BytesIO(b"y" * (paywall.MAX_BODY + 5))
        self.assertEqual(len(paywall.read_body({"Content-Length": str(paywall.MAX_BODY)}, edge)), paywall.MAX_BODY)
        self.assertEqual(edge.tell(), paywall.MAX_BODY)

    def test_the_body_cap_fits_the_text_limit(self):
        full = json.dumps({"text": "a" * paywall.ATTEST_TEXT_LIMIT, "operation": "o" * 200, "answer": "1"})
        self.assertLess(len(full), paywall.MAX_BODY)

    def test_the_service_refuses_bad_lengths_without_reading_the_body(self):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        server = subprocess.Popen([sys.executable, str(Path(__file__).resolve().parents[3] / "bin" / "paywall"),
                                   "--host", "127.0.0.1", "--port", str(port)], env=os.environ,
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(server.wait)
        self.addCleanup(server.kill)
        deadline = time.monotonic() + 30
        while True:
            self.assertIsNone(server.poll(), "bin/paywall exited before it answered")
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=1).close()
                break
            except (urllib.error.URLError, OSError):
                self.assertLess(time.monotonic(), deadline, "bin/paywall did not answer /health within 30 s")
                time.sleep(0.05)

        def raw(length_line: str) -> str:
            # Headers only, never a body: a server that tried to read one would hang until the timeout.
            with socket.create_connection(("127.0.0.1", port), timeout=5) as conn:
                conn.sendall(f"POST /attest HTTP/1.1\r\nHost: x\r\n{length_line}Connection: close\r\n\r\n".encode())
                return conn.recv(4096).decode().split("\r\n", 1)[0]

        self.assertIn(" 413 ", raw(f"Content-Length: {paywall.MAX_BODY + 1}\r\n"))
        self.assertIn(" 413 ", raw("Content-Length: 99999999999999\r\n"))
        self.assertIn(" 400 ", raw("Content-Length: abc\r\n"))
        self.assertIn(" 411 ", raw(""))
        # a fair-sized body still reaches the paywall (402: no payment)
        body = json.dumps(GOOD).encode()
        with socket.create_connection(("127.0.0.1", port), timeout=5) as conn:
            conn.sendall(b"POST /attest HTTP/1.1\r\nHost: x\r\nContent-Length: %d\r\nConnection: close\r\n\r\n" % len(body) + body)
            self.assertIn(" 402 ", conn.recv(4096).decode().split("\r\n", 1)[0])


class AttestCorpusMirror(unittest.TestCase):
    """The vendored grader must keep reproducing the measured corpus truth
    (forgeloop's 23 frames, measured 2026-11-24): 14 of the 18 verified
    frames blocked, f01 blessed on 'total' alone (known bug), f07's
    rejected add blessed (known bug), f10/f13/f17 refused. If this drifts
    the grader changed and the corpus receipt is stale: stop and
    re-measure before selling."""

    def test_the_measured_truth_holds(self):
        from teeny_hands.wallet import attest

        with open(Path(__file__).resolve().parent / "fixtures" / "forgeloop-corpus.json") as f:
            frames = json.load(f)
        blocked, blessed, rejected_shipped = [], [], []
        for fr in frames:
            op = fr["op"] if fr["op"] != "-" else "add"
            report = attest.grade(fr["text"], op, fr.get("answer"))
            ships = report["verdict"] == "attested"
            if fr["outcome"] == "verified" and not ships:
                blocked.append(fr["id"])
            if fr["outcome"] == "refused" and ships:
                blessed.append(fr["id"])
            if fr["outcome"] == "rejected" and ships:
                rejected_shipped.append(fr["id"])
        self.assertEqual(sorted(blocked),
                         ["f02", "f03", "f04", "f05", "f06", "f08", "f09", "f11",
                          "f12", "f15", "f16", "f19", "f21", "f23"])
        self.assertEqual(blessed, ["f01"])
        self.assertEqual(rejected_shipped, ["f07"])


if __name__ == "__main__":
    unittest.main()
