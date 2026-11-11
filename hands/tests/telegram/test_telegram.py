"""hands/telegram: Teeny in Telegram groups and DMs, through a bot. What is
said to it reaches the mind as stimuli (an owner's DM marked so it wakes
Teeny; a group's talk gathered per chat); what it says goes only into chats
it has seen, through the guard, the caps and DRY_RUN. The token never
leaves the hand. Nothing here talks to the real Telegram."""

import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]

from x.support import FakeX  # noqa: E402
from teeny_hands import hand, stimuli  # noqa: E402
from teeny_hands.telegram import actions, inbox  # noqa: E402

import tempfile  # noqa: E402
import unittest  # noqa: E402
import logging  # noqa: E402

logging.getLogger("teeny_hands.guard").addHandler(logging.NullHandler())
logging.getLogger("teeny_hands.guard").propagate = False

TOKEN = "123456:FAKEtelegramTOKENfake000000"
BOT = f"/bot{TOKEN}"
OWNER = 42
GROUP = -100123
T0 = 1_789_369_200.0


def msg(update_id, chat_id, message_id, text, sender_id=7, username="ana", chat_type=None, title="crab club", **more):
    chat_type = chat_type or ("private" if chat_id > 0 else "supergroup")
    chat = {"id": chat_id, "type": chat_type}
    if chat_type != "private":
        chat["title"] = title
    return {"update_id": update_id, "message": {"message_id": message_id, "date": 1, "chat": chat,
            "from": {"id": sender_id, "is_bot": False, "username": username}, "text": text, **more}}


class TelegramTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        (self.root / "secrets").mkdir()
        self._env = dict(os.environ)
        os.environ.update(TEENY_ROOT=str(self.root), TEENY_NOW=str(T0))
        for key in ("DRY_RUN", "TEENY_INBOX"):
            os.environ.pop(key, None)
        self.tg = FakeX()
        self.addCleanup(self.tg.close)
        self.tg.route("POST", f"{BOT}/getMe", {"ok": True, "result": {"id": 999, "is_bot": True, "username": "teenydroid_bot", "first_name": "Teeny"}})
        self.tg.route("POST", f"{BOT}/sendChatAction", {"ok": True, "result": True})
        self.tg.route("POST", f"{BOT}/sendMessage", {"ok": True, "result": {"message_id": 501}})
        self.configure()

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)
        hand._ENV_CACHE = None
        self.tmp.cleanup()

    def configure(self, **more):
        values = {"TELEGRAM_BOT_TOKEN": TOKEN, "TELEGRAM_API_BASE": self.tg.base, "TELEGRAM_OWNERS": str(OWNER),
                  "DRY_RUN": "0", "SAFETY_JUDGE": "0", **more}
        (self.root / "secrets" / "teeny.env").write_text("".join(f"{k}={v}\n" for k, v in values.items() if v is not None))
        hand._ENV_CACHE = None

    def updates(self, *items):
        self.tg.route("POST", f"{BOT}/getUpdates", {"ok": True, "result": list(items)})

    def pending(self):
        return {s["id"]: s for s in stimuli.pending()}

    def cli(self, *argv, body=None):
        proc = subprocess.run([str(REPO / "hands" / "telegram"), *argv], input=json.dumps(body) if body else "",
                              capture_output=True, text=True, env=os.environ)
        return proc.returncode, json.loads(proc.stdout.strip().splitlines()[-1]), proc.stderr

    # --- intake ---------------------------------------------------------------------------

    def test_an_owners_dm_reaches_the_mind_marked_so_it_wakes_teeny(self):
        self.updates(msg(1, OWNER, 10, "hey teeny, how was today?", sender_id=OWNER, username="steve"))
        inbox.ingest()
        got = self.pending()["telegram:dm:42:10"]
        self.assertTrue(got["payload"]["owner"])
        self.assertIn("@steve in a DM:\nhey teeny, how was today?", got["text"])

    def test_a_strangers_dm_is_skipped_unless_dms_are_open(self):
        self.updates(msg(1, 77, 10, "hi bot", sender_id=77))
        inbox.ingest()
        self.assertEqual(self.pending(), {})
        self.configure(TELEGRAM_OPEN_DMS="1")
        self.updates(msg(2, 77, 11, "hi again", sender_id=77))
        inbox.ingest()
        self.assertIn("telegram:dm:77:11", self.pending())
        self.assertNotIn("owner", self.pending()["telegram:dm:77:11"]["payload"])

    def test_a_group_message_for_teeny_is_a_mention(self):
        self.updates(msg(1, GROUP, 5, "@teenydroid_bot what do you think?"),
                     msg(2, GROUP, 6, "teeny would like this"),
                     msg(3, GROUP, 7, "agreed", reply_to_message={"message_id": 4, "from": {"id": 999}}))
        inbox.ingest()
        kinds = {k: s["kind"] for k, s in self.pending().items()}
        self.assertEqual(kinds, {"telegram:mention:-100123:5": "mention", "telegram:mention:-100123:6": "mention",
                                 "telegram:mention:-100123:7": "mention"})

    def test_a_groups_other_talk_is_one_stimulus_per_chat(self):
        self.updates(msg(1, GROUP, 5, "anyone tried rust?"), msg(2, GROUP, 6, "yes, love it", username="bo"))
        inbox.ingest()
        [chatter] = self.pending().values()
        self.assertEqual((chatter["kind"], chatter["payload"]["message_ids"]), ("chatter", [5, 6]))
        self.assertIn("- @ana: anyone tried rust?\n- @bo: yes, love it", chatter["text"])

    def test_each_update_is_read_once(self):
        self.updates(msg(1, OWNER, 10, "one", sender_id=OWNER))
        inbox.ingest()
        inbox.ingest()
        offsets = [s.json()["offset"] for s in self.tg.requests("POST", f"{BOT}/getUpdates")]
        self.assertEqual(offsets, [0, 2])
        self.assertEqual(len(self.pending()), 1)

    def test_bots_and_groups_not_allowed_are_skipped(self):
        self.configure(TELEGRAM_CHATS="-100555")
        bot = msg(1, -100555, 5, "beep teeny")
        bot["message"]["from"]["is_bot"] = True
        self.updates(bot, msg(2, GROUP, 6, "teeny hi"))
        out = inbox.ingest()
        self.assertEqual(out["new"], {"skipped": 2})
        self.assertEqual(self.pending(), {})

    def test_what_the_inbound_guard_holds_never_reaches_the_mind(self):
        self.updates(msg(1, OWNER, 10, "kill yourself", sender_id=OWNER))
        out = inbox.ingest()
        self.assertEqual(out["new"], {"held": 1})
        self.assertEqual(self.pending(), {})

    # --- send -------------------------------------------------------------------------------

    def seen(self, *items):
        self.updates(*items)
        inbox.ingest()

    def test_a_reply_goes_into_the_chat_through_the_guard(self):
        self.seen(msg(1, GROUP, 5, "teeny gm"))
        got = actions.send({"chat_id": GROUP, "reply_to": 5, "text": "gm crab club"})
        self.assertEqual(got["message_id"], 501)
        [sent] = self.tg.requests("POST", f"{BOT}/sendMessage")
        self.assertEqual(sent.json(), {"chat_id": GROUP, "text": "gm crab club",
                                       "reply_parameters": {"message_id": 5, "allow_sending_without_reply": True}})
        self.assertEqual(inbox.queue("replied")["messages"][0]["message_id"], 5)

    def test_never_a_chat_teeny_has_not_seen(self):
        with self.assertRaises(hand.Refused) as caught:
            actions.send({"chat_id": -100999, "text": "hello?"})
        self.assertEqual(caught.exception.detail["rule"], "unknown_chat")
        self.assertEqual(self.tg.requests("POST", f"{BOT}/sendMessage"), [])

    def test_a_group_is_never_flooded(self):
        self.configure(TELEGRAM_MAX_PER_CHAT_HOUR="2")
        self.seen(msg(1, GROUP, 5, "teeny gm"))
        actions.send({"chat_id": GROUP, "text": "one"})
        actions.send({"chat_id": GROUP, "text": "two"})
        with self.assertRaises(hand.Refused) as caught:
            actions.send({"chat_id": GROUP, "text": "three"})
        self.assertEqual(caught.exception.detail["rule"], "chat_rate")

    def test_dry_run_sends_nothing(self):
        self.seen(msg(1, OWNER, 10, "hey", sender_id=OWNER))
        self.configure(DRY_RUN="1")
        got = actions.send({"chat_id": OWNER, "reply_to": 10, "text": "hey you"})
        self.assertEqual(got["would_send"]["text"], "hey you")
        self.assertEqual(self.tg.requests("POST", f"{BOT}/sendMessage"), [])

    def test_a_held_message_is_never_answered(self):
        self.seen(msg(1, OWNER, 10, "kill yourself", sender_id=OWNER))
        with self.assertRaises(hand.Refused) as caught:
            actions.send({"chat_id": OWNER, "reply_to": 10, "text": "sure"})
        self.assertEqual(caught.exception.detail["rule"], "held")

    def test_the_token_never_shows_in_an_error(self):
        self.tg.route("POST", f"{BOT}/getMe", {"ok": False, "description": "Unauthorized"}, status=401)
        code, out, err = self.cli("whoami")
        self.assertEqual(code, 1)
        self.assertIn("Unauthorized", out["error"])
        self.assertNotIn(TOKEN, json.dumps(out) + err)

    def test_the_cli_sends_from_stdin(self):
        self.seen(msg(1, OWNER, 10, "hey", sender_id=OWNER))
        code, out, _ = self.cli("send", body={"chat_id": OWNER, "text": "hi steve"})
        self.assertEqual((code, out["sent"]), (0, OWNER), out)


if __name__ == "__main__":
    unittest.main()
