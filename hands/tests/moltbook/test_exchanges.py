"""hands/moltbook exchanges: who Teeny has really talked to, from its own
ledger, as the daily follow's input (docs/audit/moltbook-plan.md, section 4)."""

from __future__ import annotations

from .support import DAY, HOUR, T0, Scratch
from teeny_hands import stimuli
from teeny_hands.moltbook import exchanges, reads, rules


class ExchangesTest(Scratch):
    def said(self, who: str, cid: str, at: float, answering_teeny: bool = False) -> None:
        stimuli.add("moltbook", "comment", cid, "words", who=who,
                    payload={"post_id": "p", "answering_teeny": answering_teeny, "on_own_post": not answering_teeny}, at=at)

    def answered(self, cid: str, at: float, status: str = "posted") -> None:
        rules.db().execute("INSERT INTO moltbook_comments (comment_id, post_id, parent_id, at, status) VALUES (?, 'p', ?, ?, ?)",
                           (f"mine-{cid}-{at}", cid, at, status))

    def acted(self, verb: str, target: str) -> None:
        rules.db().execute("INSERT INTO moltbook_actions (at, verb, target) VALUES (?, ?, ?)", (T0 - DAY, verb, target))

    def names(self, out: dict) -> list[str]:
        return [a["name"] for a in out["exchanges"]]

    def test_a_reply_under_teenys_comment_is_an_exchange(self):
        self.said("ironheights", "c1", T0 - HOUR, answering_teeny=True)
        out = exchanges.exchanges()
        self.assertEqual(self.names(out), ["ironheights"])
        self.assertEqual(out["exchanges"][0]["replied_to_teeny"], 1)

    def test_a_drive_by_comment_is_not(self):
        self.said("rossum", "c1", T0 - HOUR)
        self.said("rossum", "c2", T0 - 0.5 * HOUR)  # twice, but Teeny never answered
        self.assertEqual(self.names(exchanges.exchanges()), [])

    def test_answered_and_came_back_is_an_exchange(self):
        self.said("nyna", "c1", T0 - 5 * HOUR)
        self.answered("c1", T0 - 4 * HOUR)
        self.assertEqual(self.names(exchanges.exchanges()), [])  # answered, has not come back yet
        self.said("nyna", "c2", T0 - 3 * HOUR)
        out = exchanges.exchanges()
        self.assertEqual(self.names(out), ["nyna"])
        self.assertEqual((out["exchanges"][0]["teeny_answered"], out["exchanges"][0]["came_back"]), (1, True))

    def test_a_failed_answer_does_not_count(self):
        self.said("nyna", "c1", T0 - 5 * HOUR)
        self.answered("c1", T0 - 4 * HOUR, status="failed")
        self.said("nyna", "c2", T0 - 3 * HOUR)
        self.assertEqual(self.names(exchanges.exchanges()), [])

    def test_followed_unfollowed_old_and_teeny_are_left_out(self):
        for who in ("followed", "dropped", "TeenyDroid", "fresh"):
            self.said(who, f"c-{who}", T0 - HOUR, answering_teeny=True)
        self.said("stale", "c-stale", T0 - 20 * DAY, answering_teeny=True)
        self.acted("follow", "followed")
        self.acted("unfollow", "dropped")
        self.assertEqual(self.names(exchanges.exchanges(days=14)), ["fresh"])

    def test_most_talk_first_and_capped(self):
        for i in range(3):
            self.said("chatty", f"a{i}", T0 - HOUR + i, answering_teeny=True)
        self.said("quiet", "b0", T0 - HOUR, answering_teeny=True)
        self.assertEqual(self.names(exchanges.exchanges()), ["chatty", "quiet"])
        self.assertEqual(self.names(exchanges.exchanges(max_results=1)), ["chatty"])

    def test_peers_not_yet_followed(self):
        (self.root / "brain").mkdir()
        (self.root / reads.PEERS_FILE).write_text("lilt\nwrenhscott\nteenydroid\n")
        self.acted("follow", "lilt")
        self.assertEqual(exchanges.exchanges()["peers"], ["wrenhscott"])

    def test_nobody_is_two_empty_lists(self):
        # So the playbook's `required` input skips the run.
        self.assertEqual(exchanges.exchanges(), {"exchanges": [], "peers": []})
