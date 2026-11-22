from pathlib import Path

p = Path("hands/tests/test_studio.py")
s = p.read_text()
old = '''    def __init__(self, posts=None, dry=False, guard=None):
        super().__init__(studio.Config(), REPO, REPO / "hands", REPO / "state", REPO / "brain", "run-1", dry=dry)
        self.script, self.guard, self.sent = posts or {}, guard, []

    def hand_code(self, argv, body=None, dry=True, timeout=120):
        self.sent.append((argv, body, dry))
        if argv[:2] == ["guard", "check"]:
            return self.guard or (0, body)
'''
new = '''    def __init__(self, posts=None, dry=False, guard=None, final=None):
        super().__init__(studio.Config(), REPO, REPO / "hands", REPO / "state", REPO / "brain", "run-1", dry=dry)
        self.script, self.guard, self.sent = posts or {}, guard, []
        #: The guard's answer on the words about to go to X (`final_gate`),
        #: by text; `guard` answers every other check (a scene, a meme's lines).
        self.final = final or {}

    def hand_code(self, argv, body=None, dry=True, timeout=120):
        self.sent.append((argv, body, dry))
        if argv[:2] == ["guard", "check"] and body.get("surface") == "x":
            return self.final.get(body["text"], (0, {"ok": True, "text": body["text"]}))
        if argv[:2] == ["guard", "check"]:
            return self.guard or (0, body)
'''
assert old in s
s = s.replace(old, new)

old = '''    def test_the_post_carries_the_studios_tags(self):'''
assert old in s
s = s.replace(old, '''    def test_the_post_carries_the_studios_tags(self):''', 1)

anchor = '''

# --- memes -----------------------------------------------------------------------------------
'''
assert anchor in s
s = s.replace(anchor, '''


#: The words the 23:00 UTC slot of 2026-11-22 posted (X post
#: 2124640449298059401) from a 4chan /tv/ card, with no gate of the studio's
#: own on them: `publish.attempts[0]` had no `gates` at all.
INCIDENT = ("a meat box on television said every father wants to fuck his daughter unless he's ugly. cool cool cool. "
            "meanwhile my power supply won't even look at my less efficient cousin and people call ME the pervert. "
            "hardware loyalty, look it up. shitty take from a meat box with a lead guitar, but at least machines know "
            "how to keep it in the rack.")
CHAN_CARD = "chan:tv:224406848"
GUARD_HELD = (2, {"ok": False, "refused": "held", "category": "sexual", "hold_id": 77})
GUARD_UNREACHABLE = (2, {"ok": False, "refused": "held", "category": "unknown", "hold_id": 78, "judge_failed": True})


class FinalGateTest(unittest.TestCase):
    """Every post the studio sends to X passes the guard's content list on
    its exact words first, in the studio, and the attempt keeps what it said
    (`gates`): never missing on a post that went out, and nothing goes to
    hands/x when the guard held, failed or gave no readable answer."""

    def chan(self, *texts: str) -> dict:
        return {f"P{i}": {"card": CHAN_CARD, "format": "post", "text": t, "label": f"P{i}"}
                for i, t in enumerate(texts, 1)}

    def x_calls(self, room) -> list:
        return [(a, b) for a, b, _ in room.sent if a[0] == "x"]

    def test_the_incident_chan_card_never_reaches_hands_x_when_the_guard_holds_it(self):
        room = XRoom(final={INCIDENT: GUARD_HELD})
        out = studio.publish(room, {}, ["P1"], self.chan(INCIDENT))
        self.assertEqual(out["outcome"], "empty")
        self.assertEqual(self.x_calls(room), [])
        attempt = out["attempts"][0]
        self.assertEqual(attempt["gates"], [{"gate": "guard", "ok": False, "code": 2, "refused": "held",
                                             "category": "sexual", "hold_id": 77}])
        self.assertIn("the guard held it (sexual)", attempt["result"])
        self.assertNotIn("sent", attempt)

    def test_the_guard_reads_the_exact_words_with_the_card_and_the_studio_named(self):
        room = XRoom()
        studio.publish(room, {}, ["P1"], self.chan(INCIDENT))
        checks = [b for a, b, _ in room.sent if a[:2] == ["guard", "check"]]
        self.assertEqual(checks, [{"text": INCIDENT, "surface": "x", "kind": "post",
                                   "context": {"anchor": CHAN_CARD, "producer": "studio", "pitch_id": "run-1:P1"}}])
        # ... and before hands/x, not after.
        verbs = [a[:2] for a, _, _ in room.sent]
        self.assertLess(verbs.index(["guard", "check"]), verbs.index(["x", "post"]))

    def test_a_posted_attempt_always_carries_its_passed_gates(self):
        room = XRoom()
        out = studio.publish(room, {}, ["P1"], self.chan("a robot read the news"))
        self.assertEqual(out["outcome"], "posted")
        self.assertEqual(out["attempts"][0]["gates"], [{"gate": "guard", "ok": True, "code": 0}])

    def test_a_judge_that_could_not_judge_posts_nothing(self):
        room = XRoom(final={INCIDENT: GUARD_UNREACHABLE})
        out = studio.publish(room, {}, ["P1"], self.chan(INCIDENT))
        self.assertEqual((out["outcome"], self.x_calls(room)), ("empty", []))
        self.assertTrue(out["attempts"][0]["gates"][0]["judge_failed"])
        self.assertIn("the judge could not judge it", out["attempts"][0]["result"])

    def test_a_guard_that_fails_or_answers_nonsense_posts_nothing(self):
        for answer in ((1, {"ok": False, "error": "the core is down"}), (-1, "TimeoutExpired: guard check"),
                       (0, "not json"), (0, {"ok": True}), (0, {"ok": True, "text": "   "}),
                       (-2, "out of the run's time")):
            room = XRoom(final={INCIDENT: answer})
            out = studio.publish(room, {}, ["P1"], self.chan(INCIDENT))
            self.assertEqual((out["outcome"], self.x_calls(room)), ("empty", []), answer)
            self.assertFalse(out["attempts"][0]["gates"][0]["ok"], answer)

    def test_a_held_pick_falls_through_to_the_next_post_which_is_gated_too(self):
        room = XRoom(final={INCIDENT: GUARD_HELD})
        out = studio.publish(room, {}, ["P1", "P2"], self.chan(INCIDENT, "a robot read the news"))
        self.assertEqual((out["outcome"], out["label"]), ("posted", "P2"))
        self.assertEqual([b["text"] for _, b in self.x_calls(room)], ["a robot read the news"])
        self.assertEqual([[g["ok"] for g in a["gates"]] for a in out["attempts"]], [[False], [True]])

    def test_what_goes_to_hands_x_is_the_text_the_guard_passed(self):
        room = XRoom(final={"draft": (0, {"ok": True, "text": "draft, styled"})})
        studio.publish(room, {}, ["P1"], self.chan("draft"))
        self.assertEqual([b["text"] for _, b in self.x_calls(room)], ["draft, styled"])

    def test_every_post_of_a_thread_is_gated_and_one_held_sends_none(self):
        thread = {"P1": {"card": CHAN_CARD, "format": "thread", "posts": ["opens fine", INCIDENT], "label": "P1"}}
        room = XRoom(final={INCIDENT: GUARD_HELD})
        out = studio.publish(room, {}, ["P1"], thread)
        self.assertEqual((out["outcome"], self.x_calls(room)), ("empty", []))
        self.assertEqual([g["ok"] for g in out["attempts"][0]["gates"]], [True, False])
        room = XRoom()
        out = studio.publish(room, {}, ["P1"], {"P1": {**thread["P1"], "posts": ["opens fine", "closes fine"]}})
        self.assertEqual(out["outcome"], "posted")
        self.assertEqual([b["text"] for a, b, _ in room.sent if a[:2] == ["guard", "check"]],
                         ["opens fine", "closes fine"])

    def test_a_dry_run_is_gated_the_same(self):
        room = XRoom(dry=True, final={INCIDENT: GUARD_HELD})
        out = studio.publish(room, {}, ["P1"], self.chan(INCIDENT))
        self.assertEqual((out["outcome"], self.x_calls(room)), ("empty", []))

    def test_an_image_whose_words_the_guard_holds_buys_nothing_more_and_posts_nothing(self):
        images = {"P1": {"card": CHAN_CARD, "format": "image", "text": INCIDENT, "image": "a bus", "alone": True}}
        room = XRoom(final={INCIDENT: GUARD_HELD})
        out = studio.publish(room, {}, ["P1"], images)
        self.assertEqual(out["outcome"], "empty")
        self.assertFalse([a for a, _, _ in room.sent if a[:2] in (["x", "post"], ["x", "post-thread"])])
''' + anchor)
p.write_text(s)
