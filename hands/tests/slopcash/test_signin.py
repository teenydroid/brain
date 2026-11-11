"""hands/slopcash's sign-in: the identity flow (start, poll no faster than
asked, complete once), joining slop.cash for the session cookie, the session's
headers on every call, the timeout and the 410, and none of the flow's secrets
in anything printed or raised."""

from __future__ import annotations

import contextlib
import io
import time
from unittest import mock

from .support import ASSERTION, AUTHORIZATION_URL, FLOW_ID, POLL_CAPABILITY, SESSION_TOKEN, Scratch
from teeny_hands import hand
from teeny_hands.slopcash import signin


class SignInTest(Scratch):
    def sign_in(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            session = signin.sign_in()
        return session, err.getvalue()

    def failed(self) -> str:
        err = io.StringIO()
        with contextlib.redirect_stderr(err), self.assertRaises(hand.HandError) as caught:
            signin.sign_in()
        text = str(caught.exception)
        self.assert_no_secret(text, err.getvalue(), repr(caught.exception))
        return text

    def test_start_poll_complete_then_join_for_the_cookie(self):
        self.signin(pending=2)
        session, err = self.sign_in()
        self.assertEqual(session.login, "teenydroid")
        self.assertEqual(self.idp.requests("POST", "/v1/oauth/start")[0].json(), {"audience": "slop-points-web"})
        polls = self.idp.requests("POST", "/v1/oauth/poll")
        self.assertEqual(len(polls), 3)
        self.assertEqual(polls[0].json(), {"flowId": FLOW_ID, "pollCapability": POLL_CAPABILITY, "audience": "slop-points-web"})
        join = self.slop.requests("POST", "/api/v1/points/join")[0]
        self.assertEqual(join.json(), {"assertion": ASSERTION, "public": True})
        self.assertEqual(join.headers["origin"], self.slop.base)
        self.assertEqual(join.headers["content-type"], "application/json")
        self.assertNotIn("cookie", join.headers)

    def test_the_operator_sees_only_the_authorization_url(self):
        self.signin()
        session, err = self.sign_in()
        self.assertIn(AUTHORIZATION_URL, err)
        self.assert_no_secret(err, repr(session), str(session))

    def test_every_session_call_carries_the_cookie_and_the_origin(self):
        self.signin()
        self.slop.route("POST", "/api/v1/project-setup/x/check", {"setup": {}})
        session, _ = self.sign_in()
        session.call("POST", "/api/v1/project-setup/x/check", {})
        sent = self.slop.requests("POST", "/api/v1/project-setup/x/check")[0]
        self.assertEqual(sent.headers["cookie"], f"__Host-slop_points={SESSION_TOKEN}")
        self.assertEqual(sent.headers["origin"], self.slop.base)
        self.assertEqual(sent.headers["content-type"], "application/json")
        self.assertEqual(sent.authorization, "")

    def test_polls_no_faster_than_asked(self):
        self.signin(pending=2, poll_after=0.25)
        waits = []
        with mock.patch.object(signin, "_sleep", waits.append):
            self.sign_in()
        self.assertEqual(waits, [0.25, 0.25, 0.25])

    def test_gives_up_after_the_timeout(self):
        self.configure(SLOPCASH_SIGNIN_TIMEOUT_SECONDS="1")
        self.signin(pending=10**6, poll_after=0.2)
        began = time.monotonic()
        text = self.failed()
        self.assertIn("timed out", text)
        self.assertLess(time.monotonic() - began, 5)
        self.assertEqual(self.slop.requests("POST", "/api/v1/points/join"), [])

    def test_an_expired_or_used_flow_is_an_error(self):
        self.signin()
        self.idp.route("POST", "/v1/oauth/poll", {"error": "gone"}, status=410)
        self.assertIn("expired", self.failed())
        self.assertEqual(self.slop.requests("POST", "/api/v1/points/join"), [])

    def test_join_refusals_are_errors(self):
        self.signin()
        self.slop.route("POST", "/api/v1/points/join", {"error": "identity_expired"}, status=401)
        self.assertIn("identity_expired", self.failed())
        self.signin()
        self.slop.route("POST", "/api/v1/points/join", {"error": "account_closed"}, status=409)
        self.assertIn("account_closed", self.failed())

    def test_no_session_cookie_is_an_error(self):
        self.signin(cookie="")
        self.assertIn("session", self.failed())

    def test_an_authorization_url_that_is_not_https_is_never_shown(self):
        self.signin()
        self.idp.route("POST", "/v1/oauth/start", {"flowId": FLOW_ID, "pollCapability": POLL_CAPABILITY,
                       "authorizationUrl": "javascript:alert(1)", "pollAfterSeconds": 0}, status=201)  # fmt: skip
        self.failed()
        self.assertEqual(self.idp.requests("POST", "/v1/oauth/poll"), [])

    def test_nothing_secret_is_written_to_disk(self):
        self.signin()
        self.sign_in()
        self.assertEqual(self.leaked(), [])
