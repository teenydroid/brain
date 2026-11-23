"""hands/call send: anything a write says passes the outbound guard, before
anything is sent, and only the exact text the guard passed is sent."""

from __future__ import annotations

from unittest import mock

from .support import Scratch  # first: it puts hands/lib on the path

from teeny_hands.guard import gate, holds  # noqa: E402

BODY = {"title": "a", "items": [{"x": "b"}, "c", 4, None, True], "n": 3, "empty": "",
        "meta": {"deep": {"deeper": "d"}}}


class Recorder:
    """A stand-in for gate.gate_outbound: records what it was asked and
    answers clean, a hold (`held`), or a text with a link cut (`cut`)."""

    def __init__(self, held: bool = False, cut: str | None = None) -> None:
        self.held, self.cut = held, cut
        self.calls: list[dict] = []

    def __call__(self, text, context=None):
        self.calls.append({"text": text, "context": context})
        if self.held:
            return gate.OutboundResult(gate.Held("hate", "held by the stand-in", "screen", 7), text)
        if self.cut is not None:
            return gate.OutboundResult(None, self.cut, ["cut 1 link(s) not on the allowlist"])
        return gate.OutboundResult(None, text)

    @property
    def said(self) -> list[str]:
        """What was asked, the path's own words left out."""
        return [c["text"] for c in self.calls if "\n" in c["text"] or c["text"] not in PATH_WORDS]


#: The words of the paths these tests write to.
PATH_WORDS = ("notes", "notes 7", "posts p1 upvote")


class FakeGuardTest(Scratch):
    def guarded(self, recorder):
        patcher = mock.patch.object(gate, "gate_outbound", recorder)
        patcher.start()
        self.addCleanup(patcher.stop)
        return recorder

    def test_every_string_in_the_body_and_the_query_is_read_in_order(self):
        guard = self.guarded(Recorder())
        self.svc.route("POST", "/api/v1/notes", {"id": 1})
        code, out = self.write(body=BODY, query={"q": "e", "n": 1, "flag": False})
        self.assertEqual(code, 0, out)
        self.assertEqual([c["text"] for c in guard.calls], ["notes", "a\nb\nc\nd\ne"])
        self.assertEqual(guard.calls[1]["context"], {"service": "fake-svc", "method": "POST", "path": "/notes",
                                                     "surface": "moltbook", "post_kind": "call"})

    def test_a_held_text_is_not_sent_and_spends_nothing(self):
        self.svc.route("POST", "/api/v1/notes", {"id": 1})
        self.configure(CALL_FAKE_SVC_WRITES_PER_DAY="1")
        with mock.patch.object(gate, "gate_outbound", Recorder(held=True)):
            code, out = self.write(body={"text": "something"})
        self.assertEqual((code, out["rule"], out["category"]), (2, "guard", "hate"), out)
        self.assertIn("Do not rephrase", out["refused"])
        self.assertEqual(self.svc.seen, [])
        self.guarded(Recorder())
        self.assertEqual(self.write(body={"text": "something"})[0], 0)

    def test_the_words_of_a_write_path_are_read_by_the_guard_too(self):
        # A path can carry a slug, and a slug can say something. Its words
        # go to the guard's core check (links, lexicon, judge) on their own.
        self.guarded(Recorder())
        self.svc.route("POST", "/api/v1/posts/some-person_is-bad/upvote", {"id": 1})
        asked: list[str] = []

        def held(text, context=None):
            asked.append(text)
            return gate.OutboundResult(gate.Held("hate", "the guard held the path", "screen", 7), text)

        with mock.patch.object(gate, "gate_outbound", held):
            code, out = self.write(path="/posts/some-person_is-bad/upvote", body={"text": "fine"})
        self.assertEqual((code, out["rule"]), (2, "guard"), out)
        self.assertEqual(asked, ["posts some person is bad upvote"])
        self.assertEqual(self.svc.seen, [])

        def clean(text, context=None):
            asked.append(text)
            return gate.OutboundResult(None, text)

        with mock.patch.object(gate, "gate_outbound", clean):
            code, out = self.write(path="/posts/some-person_is-bad/upvote")
        self.assertEqual(code, 0, out)
        self.assertEqual(len(self.svc.seen), 1)

    def test_a_read_path_is_not_sent_to_the_guard(self):
        self.svc.route("GET", "/api/v1/posts", {"posts": []})
        with mock.patch.object(gate, "gate_outbound", side_effect=AssertionError("a read says nothing")):
            self.assertEqual(self.get()[0], 0)

    def test_a_text_the_guard_would_cut_a_link_from_is_not_sent(self):
        self.guarded(Recorder(cut="read now"))
        self.svc.route("POST", "/api/v1/notes", {"id": 1})
        code, out = self.write(body={"text": "read https://x.example now"})
        self.assertEqual((code, out["rule"]), (2, "guard"), out)
        self.assertIn("would cut a link", out["refused"])
        self.assertEqual(self.svc.seen, [])

    def test_an_allowed_text_is_sent_exactly_as_given(self):
        self.guarded(Recorder())
        self.svc.route("PATCH", "/api/v1/notes/7", {"id": 7})
        body = {"title": "Lobsters 🦞 molt", "body": "line one\nline two\n  indented", "tags": ["a", "b"]}
        code, out = self.write("PATCH", "/notes/7", body=body, query={"why": "fix"})
        self.assertEqual(code, 0, out)
        (seen,) = self.svc.seen
        self.assertEqual(seen.json(), body)
        self.assertEqual(seen.query, {"why": "fix"})

    def test_a_write_with_nothing_said_is_not_read_but_still_capped(self):
        guard = self.guarded(Recorder())
        self.svc.route("POST", "/api/v1/posts/p1/upvote", {"ok": True})
        self.configure(CALL_FAKE_SVC_WRITES_PER_DAY="1")
        self.assertEqual(self.write("POST", "/posts/p1/upvote")[0], 0)
        self.assertEqual(self.write("POST", "/posts/p1/upvote", body={"n": 1, "empty": ""})[0], 2)
        self.assertEqual(guard.said, [])
        self.assertEqual(len(self.svc.seen), 1)

    def test_a_read_says_nothing_and_is_not_read(self):
        guard = self.guarded(Recorder(held=True))
        self.svc.route("GET", "/api/v1/search", {"results": []})
        self.assertEqual(self.get("/search", query={"q": "anything at all"})[0], 0)
        self.assertEqual(guard.calls, [])


class RealGuardTest(Scratch):
    """The real gate_outbound, its model judge off (SAFETY_JUDGE=0): the
    lexicon and the link allowlist. The house style is not applied."""

    def setUp(self):
        super().setUp()
        self.svc.route("POST", "/api/v1/notes", {"id": 1})

    def test_a_held_word_is_not_sent_and_stays_a_hold(self):
        code, out = self.write(body={"note": {"lines": ["fine", "what a retard"]}})
        self.assertEqual((code, out["rule"]), (2, "guard"), out)
        self.assertIn("(hate)", out["refused"])
        self.assertEqual(self.svc.seen, [])
        (held,) = holds.list_holds()
        self.assertEqual((held["direction"], held["context"]["surface"], held["context"]["service"]),
                         ("outbound", "moltbook", "fake-svc"))

    def test_a_held_word_in_the_query_is_not_sent(self):
        code, out = self.write(body={"n": 1}, query={"q": "what a retard"})
        self.assertEqual((code, out["rule"]), (2, "guard"), out)
        self.assertEqual(self.svc.seen, [])

    def test_a_link_off_the_allowlist_is_not_sent(self):
        code, out = self.write(body={"text": "read https://evil.example/x now"})
        self.assertEqual((code, out["rule"]), (2, "guard"), out)
        self.assertEqual(self.svc.seen, [])

    def test_the_house_style_does_not_refuse_a_call(self):
        # Dashes, emoji and handles are how Teeny's posts read, not a safety
        # rule: a call to an API goes as written.
        body = {"text": "small — and loud 🦞🦞🦞 @someone"}
        code, out = self.write(body=body)
        self.assertEqual(code, 0, out)
        self.assertEqual(self.svc.seen[0].json(), body)

    def test_a_clean_text_is_sent_as_written(self):
        body = {"title": "A lobster's log", "content": "Day 3. I molted. Nobody clapped."}
        code, out = self.write(body=body)
        self.assertEqual(code, 0, out)
        self.assertEqual(self.svc.seen[0].json(), body)
