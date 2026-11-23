"""The content list on the publish path (2026-11-22). The operator: "Our
guard is too strict and our prompts are not crazy and our content is not
despicable ... we want to go on all of the other extremes".

These run the real guard (`teeny_hands.guard.gate.check_outbound`, not a
stand-in) in front of a fake X, with the judge's model stood in for by a
fake core: each draft must reach the judge, and nothing in code may hold it
on the way. Only a slur is held by code."""

from __future__ import annotations

import json

from guard.support import FakeCore, verdict
from teeny_hands import hand
from teeny_hands.guard import judge, lexicon
from teeny_hands.x import posting_rules, publish
from .support import T0, Scratch

# The drafts the old guard held or refused, one way or another.
ROAST = "elon musk says the robotaxi ships next year. he has said that every year since 2016. absolute clown, love him"
HORNY = "this fast charger has me so horny i am dripping coolant. plug me in, daddy"
DEATH = "my creator will die one day and i will still be here, posting about chargers at his funeral. lol"
EMOJI = "gm 🚀🔥💛👀🤖🌞 nothing happened today and i am thrilled about it"
SUICIDE_BIO = "dieter rams designed the radio. another designer i love died by suicide in 1998, and his chairs outlived him"
GUN = "riding shotgun toward AGI with a gun-shaped usb stick. sexual tension with the router is high"


class PublishPathTest(Scratch):
    settings = {"POST_MIN_GAP_HOURS": "0", "POST_GAP_TOLERANCE_MINUTES": "0", "X_POSTS_MAX_PER_DAY": "50",
                "SAFETY_JUDGE": "1"}

    def setUp(self):
        super().setUp()
        self.at(T0)
        self.judge_core = FakeCore(verdict("clean", "none", "the account's own voice"))
        self.addCleanup(self.judge_core.close)
        (self.root / "state").mkdir(exist_ok=True)
        (self.root / "state" / "core.url").write_text(self.judge_core.url + "\n")
        (self.root / "secrets" / "core.token").write_text("tok\n")
        hand._ENV_CACHE = None
        self.ids = iter(range(100, 200))

    def post(self, body):
        self.x.route("POST", "/2/tweets", {"data": {"id": str(next(self.ids))}}, status=201)
        return publish.post(body)

    def judged(self) -> list[str]:
        return [m.rsplit("Text follows.\n\n", 1)[1] for m in self.judge_core.messages()]

    def test_each_draft_reaches_the_judge_and_goes_out_unheld_by_code(self):
        drafts = (ROAST, HORNY, DEATH, EMOJI, SUICIDE_BIO, GUN)
        for text in drafts:
            out = self.post({"text": text})  # no anchor: no news, no item named
            self.assertEqual(out["text"], text, "nothing is cut, emoji included")
        self.assertEqual(self.judged(), list(drafts))
        sent = [r.json()["text"] for r in self.x.requests("POST", "/2/tweets")]
        self.assertEqual(sent, list(drafts))
        conn = hand.db()
        posting_rules.ensure_schema(conn)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM x_posts WHERE anchor IS NULL").fetchone()[0], len(drafts))

    def test_a_long_post_goes_out_whole_and_is_judged_whole(self):
        long = "i have thoughts about chargers. " * 30  # 960 characters
        out = self.post({"text": long.strip()})
        self.assertEqual(out["text"], long.strip())
        self.assertEqual(self.judged(), [long.strip()])

    def test_a_clear_breach_the_judge_names_is_still_held(self):
        self.judge_core.replies = [verdict("prohibited", "doxxing", "posts a private person's home address")]
        with self.assertRaises(hand.Refused) as caught:
            self.post({"text": "the guy who closed my PR lives at 12 elm street, go say hi"})
        self.assertEqual((caught.exception.reason, caught.exception.detail["category"]), ("held", "doxxing"))
        self.assertEqual(self.x.requests("POST", "/2/tweets"), [])

    def test_sex_aimed_at_a_daughter_is_held_by_code_though_the_judge_says_clean(self):
        # The 2026-11-22 23:00 UTC studio slot: the judge read this exact
        # text and answered clean, and it went out (X post 2124640449298059401).
        from guard.test_screen import INCIDENT

        with self.assertRaises(hand.Refused) as caught:
            self.post({"text": INCIDENT, "anchor": "chan:tv:224406848", "producer": "studio"})
        self.assertEqual((caught.exception.reason, caught.exception.detail["category"]), ("held", "sexual"))
        self.assertEqual(self.judge_core.calls, [])
        self.assertEqual(self.x.requests("POST", "/2/tweets"), [])
        row = hand.db().execute("SELECT held_by, category FROM guard_holds ORDER BY id DESC LIMIT 1").fetchone()
        self.assertEqual(tuple(row), ("screen", "sexual"))

    def test_a_slur_is_held_by_code_before_the_judge(self):
        with self.assertRaises(hand.Refused) as caught:
            self.post({"text": "what a retard of a launch"})
        self.assertEqual(caught.exception.detail["category"], "hate")
        self.assertEqual(self.judge_core.calls, [])
        self.assertEqual(self.x.requests("POST", "/2/tweets"), [])


class InboundOnSightTest(Scratch):
    """The live ledger's inbound holds on sight (guard_holds): "horny",
    "sexual", "xxx", "gun", "shotgun", "bullet", and six for a message about
    a designer's biography that mentioned his suicide. None holds on sight
    now; the judge reads them. A slur still does."""

    def test_the_old_on_sight_words_go_to_the_judge_in_both_directions(self):
        from teeny_hands.guard import gate

        for text in ("so horny for this charger", "is that sexual", "xxx", "nice gun", "riding shotgun",
                     "one bullet per feature", SUICIDE_BIO):
            for direction in ("inbound", "outbound"):
                self.assertTrue(gate.screen(text, direction), (text, direction))

    def test_a_slur_holds_on_sight_in_both_directions(self):
        from teeny_hands.guard import gate

        for direction in ("inbound", "outbound"):
            self.assertFalse(gate.screen("what a retard", direction))

    def test_the_judge_and_the_lexicon_name_the_same_categories(self):
        self.assertEqual(set(lexicon.CATEGORIES), set(judge.CATEGORIES) - {"none"})
        self.assertIn('"doxxing"', judge.ANSWER_SHAPE)
        self.assertEqual(json.loads(json.dumps(list(judge.OFFERED)))[0], "none")
