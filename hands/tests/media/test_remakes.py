"""hands/video remake-keys and remake: a popular short re-made shot by shot
with public figures and Teeny, by keyframe swap. remake-keys edits each
source shot's first frame into the new cast plus Teeny (the cheap money);
remake animates those keyframes, trims each clip to its source shot and
cuts them with the source's own audio and the label (the expensive money).
Off unless the operator turns it on; one video a day across skits and
remakes; every shot reserves its cap before anything is bought; a keyframe's
cost comes off its shot's reservation; the second verb takes nothing but
the remake's id, its sound and a wait. Surplus and the reel service are
fakes: nothing here spends or renders anything."""

import base64
import json
import os
import sqlite3
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent / "lib"))
sys.path.insert(0, str(HERE))

from teeny_hands import hand  # noqa: E402
from teeny_hands.media import assets, remakes, shots, skits  # noqa: E402
from teeny_hands.media.surplus import SurplusError  # noqa: E402

from test_skits import CUT_ID, JPEG, SHOT, SKIT, FakeReel, FakeSurplus, T0  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
M4A = b"\x00\x00\x00\x18ftypM4A " + b"\x00" * 64
EDIT1, EDIT2, EDIT3 = remakes.EDIT_MODELS.split(",")
I2V1, I2V2, I2V3, I2V4 = remakes.REMAKE_MODELS.split(",")
REFUSED = SurplusError("Surplus answered 400: Your request was rejected because it violates the content policy", 400,
                       {"error": {"code": "request_rejected"}})


class FakeEdits(FakeSurplus):
    """FakeSurplus with image edits: `edit_rule(body)` may answer a
    SurplusError for an edit; `prices` a model to its catalog price;
    `artifact_errors` the errors a video artifact's download answers, in
    order, before it comes."""

    def __init__(self):
        super().__init__()
        self.edit_rule = lambda body: None
        self.prices = {EDIT1: 100000, EDIT2: 60000, EDIT3: 20000}
        self.artifact_errors = []
        self.downloads = 0

    def call(self, method, path, body=None, timeout=60.0, headers=None):
        if path == "/v1/images/edits" and method == "POST":
            self.calls.append((method, path, body, headers))
            refusal = self.edit_rule(body)
            if refusal:
                raise refusal
            return {"data": [{"b64_json": base64.b64encode(PNG).decode()}]}
        if path.startswith("/api/markets/") and method == "GET":
            self.calls.append((method, path, body, headers))
            model = path.rsplit("/", 1)[1]
            if model not in self.prices:
                raise SurplusError("Surplus answered 404", 404)
            return {"offers": [{"available": True, "media_unit_price": self.prices[model], "media_unit": "image"}]}
        return super().call(method, path, body, timeout, headers)

    def download(self, url, limit, timeout=120.0):
        self.downloads += 1
        if self.artifact_errors:
            error = self.artifact_errors.pop(0) if len(self.artifact_errors) > 1 or not self.stuck else self.artifact_errors[0]
            raise error
        return SHOT

    stuck = False  # the last artifact error repeats for ever

    def edits(self):
        return [b for m, p, b, h in self.calls if p == "/v1/images/edits"]

    def videos(self):
        return [b for m, p, b, h in self.calls if p == "/v1/video/generations" and m == "POST"]


def shot(n, seconds, **extra):
    return {"n": n, "frame": f"state/media/remake/s1/shot-{n}.jpg", "seconds": seconds,
            "edit": f"Replace the man at the desk in shot {n} with Elon Musk in a black t-shirt, same pose and gesture",
            "motion": f"Elon Musk slowly turns his head toward the camera and raises one eyebrow, shot {n}",
            "teeny": "peeking over the desk's edge on the left", **extra}


KEYS = {"study": "s1", "source": {"video_id": "abc123", "url": "https://www.youtube.com/shorts/abc123",
                                  "title": "The desk", "source": "youtube"},
        "story": "Musk says robotaxis will run the moon by 2030", "figures": ["Elon Musk"],
        "logline": "Musk re-enacts the desk meme", "aspect": "9:16", "shots": [shot(1, 2.4), shot(2, 4.0)]}
SOUND = "state/media/remake/s1/audio.m4a"


class RemakeTest(unittest.TestCase):
    def setUp(self):
        import tempfile

        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "secrets").mkdir()
        study = self.root / "state" / "media" / "remake" / "s1"
        study.mkdir(parents=True)
        for n in (1, 2, 3):
            (study / f"shot-{n}.jpg").write_bytes(JPEG)
        (study / "audio.m4a").write_bytes(M4A)
        other = self.root / "state" / "media" / "remake" / "s2"
        other.mkdir(parents=True)
        (other / "audio.m4a").write_bytes(M4A)
        (self.root / "brain" / "assets").mkdir(parents=True)
        (self.root / "brain" / "assets" / "teeny-reference.png").write_bytes(PNG)
        self.write("DRY_RUN=0\nSURPLUS_API_KEY=inf_test\nX_BOT_USERNAME=teenydroid\nMEDIA_SKIT_MAX_PER_DAY=1\n"
                   "MEDIA_REMAKE_MAX_PER_DAY=1\nMEDIA_MAX_USD_PER_DAY=5.00\n")
        self._env = dict(os.environ)
        os.environ["TEENY_ROOT"] = str(self.root)
        os.environ["TEENY_NOW"] = str(T0)
        os.environ.pop("DRY_RUN", None)
        hand._ENV_CACHE = None
        self.api = FakeEdits()
        self.reel = FakeReel()
        self.clock = [0.0]
        self.reserved_seen = []
        self.slept = []

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)
        hand._ENV_CACHE = None
        self.tmp.cleanup()

    def write(self, text):
        (self.root / "secrets" / "teeny.env").write_text(text)
        hand._ENV_CACHE = None

    def env(self, **values):
        text = (self.root / "secrets" / "teeny.env").read_text() + "".join(f"{k}={v}\n" for k, v in values.items())
        self.write(text)

    def sleep(self, seconds):
        self.reserved_seen += [r["cost_micro"] for r in self.rows("shot")]
        self.slept.append(seconds)
        self.clock[0] += seconds

    def keys(self, body=None):
        return remakes.generate_keys(body or KEYS, api=self.api, reel=self.reel, clock=lambda: self.clock[0])

    def remake(self, body):
        return remakes.generate_remake(body, api=self.api, reel=self.reel, sleep=self.sleep,
                                       clock=lambda: self.clock[0])

    def both(self, sound=SOUND):
        made = self.keys()
        return made, self.remake({"remake_id": made["remake_id"], "sound": sound})

    def rows(self, kind=None):
        conn = assets._db()
        try:
            query = "SELECT * FROM media_assets" + (" WHERE kind = ?" if kind else "") + " ORDER BY id"
            return conn.execute(query, (kind,) if kind else ()).fetchall()
        finally:
            conn.close()

    def spent(self):
        conn = assets._db()
        try:
            return assets._spent_today(conn)
        finally:
            conn.close()

    # --- off, dry runs, and the day's caps -------------------------------------------------

    def test_off_by_default_and_nothing_is_asked(self):
        self.write("DRY_RUN=0\nSURPLUS_API_KEY=inf_test\nMEDIA_SKIT_MAX_PER_DAY=1\nMEDIA_MAX_USD_PER_DAY=5\n")
        for call in (self.keys, lambda: self.remake({"remake_id": 1, "sound": None})):
            with self.subTest(call=call):
                with self.assertRaises(hand.Refused) as caught:
                    call()
                self.assertEqual(caught.exception.detail["rule"], "off")
                self.assertIn("MEDIA_REMAKE_MAX_PER_DAY is 0", caught.exception.reason)
        self.assertEqual((self.api.calls, self.reel.jobs, self.rows()), ([], [], []))

    def test_no_skits_a_day_means_no_remakes_either(self):
        self.write("DRY_RUN=0\nSURPLUS_API_KEY=inf_test\nMEDIA_REMAKE_MAX_PER_DAY=1\nMEDIA_MAX_USD_PER_DAY=5\n")
        with self.assertRaises(hand.Refused) as caught:
            self.keys()
        self.assertEqual(caught.exception.detail["rule"], "off")
        self.assertIn("MEDIA_SKIT_MAX_PER_DAY", caught.exception.reason)
        self.assertEqual(self.api.calls, [])

    def test_a_dry_run_says_what_it_would_make_and_calls_nothing(self):
        self.write("DRY_RUN=1\n")
        out = self.keys()
        self.assertEqual((out["dry_run"], out["would_generate"], out["enabled"]), (True, "remake", False))
        self.assertEqual(out["reserve_usd"], 2.4)
        [first, _] = out["requests"]
        self.assertEqual(first["model"], EDIT1)
        self.assertIn(KEYS["shots"][0]["edit"], first["prompt"])
        later = self.remake({"remake_id": 7, "sound": SOUND})
        self.assertEqual((later["dry_run"], later["would_generate"]), (True, "remake"))
        self.assertIn("requests", later)
        self.assertIn("enabled", later)
        self.assertEqual((self.api.calls, self.reel.jobs, self.rows()), ([], [], []))

    def test_a_skit_today_leaves_no_remake(self):
        skits.generate_skit(SKIT, api=self.api, reel=self.reel, sleep=self.sleep, clock=lambda: self.clock[0])
        with self.assertRaises(hand.Refused) as caught:
            self.keys()
        self.assertEqual(caught.exception.detail["rule"], "per_day")

    def test_a_remake_today_leaves_no_skit(self):
        self.keys()
        with self.assertRaises(hand.Refused) as caught:
            skits.generate_skit(SKIT, api=self.api, reel=self.reel, sleep=self.sleep, clock=lambda: self.clock[0])
        self.assertEqual(caught.exception.detail["rule"], "per_day")

    def test_the_remakes_own_cap_holds_under_a_wider_shared_one(self):
        self.env(MEDIA_SKIT_MAX_PER_DAY=3)
        self.keys()
        with self.assertRaises(hand.Refused) as caught:
            self.keys()
        self.assertEqual(caught.exception.detail["rule"], "per_day")
        self.assertIn("MEDIA_REMAKE_MAX_PER_DAY", caught.exception.reason)

    def test_every_shot_reserves_its_cap_against_the_shared_daily_budget(self):
        self.env(MEDIA_MAX_USD_PER_DAY="2.00")
        with self.assertRaises(hand.Refused) as caught:
            self.keys()
        self.assertEqual((caught.exception.detail["rule"], caught.exception.detail["need_usd"]), ("budget", 2.4))
        self.assertEqual((self.api.calls, self.rows()), ([], []))

    def test_the_runway_cuts_remakes_like_every_media_hand(self):
        self.env(RUNWAY=1)
        with self.assertRaises(hand.Refused) as caught:
            self.keys()
        self.assertEqual(caught.exception.detail["rule"], "runway")
        self.assertEqual(self.api.calls, [])

    def test_no_reel_service_no_money_spent(self):
        self.reel.up = False
        with self.assertRaises(hand.HandError):
            self.keys()
        self.assertEqual((self.api.calls, self.rows()), ([], []))

    # --- the keys ----------------------------------------------------------------------------

    def test_each_shots_frame_and_teenys_sheet_go_to_the_edit(self):
        out = self.keys()
        edits = self.api.edits()
        self.assertEqual(len(edits), 2)
        for body in edits:
            self.assertNotIn("image", body)
            self.assertEqual(body["model"], EDIT1)
            frame, sheet = body["images"]
            self.assertTrue(frame.startswith("data:image/jpeg;base64,"))
            self.assertTrue(sheet.startswith("data:image/png;base64,"))
        prompt = next(b["prompt"] for b in edits if "shot 1" in b["prompt"])
        self.assertTrue(prompt.startswith("The first image is the scene"))
        self.assertIn(KEYS["shots"][0]["edit"], prompt)
        self.assertIn(assets.CHARACTER, prompt)
        self.assertIn("Exactly one Teeny, peeking over the desk's edge on the left", prompt)
        self.assertIn("no logos or brand names", prompt)
        rid = out["remake_id"]
        self.assertEqual([(k["n"], k["path"], k["model"], k["cost_usd"]) for k in out["keys"]],
                         [(1, f"state/media/remake-{rid}-key-1.png", EDIT1, 0.1),
                          (2, f"state/media/remake-{rid}-key-2.png", EDIT1, 0.1)])
        self.assertEqual((out["cost_usd"], out["reserve_usd"]), (0.2, 2.2))
        self.assertTrue((self.root / out["keys"][0]["path"]).read_bytes().startswith(b"\x89PNG"))

    def test_the_house_tail_comes_from_the_setting(self):
        self.env(MEDIA_REMAKE_STYLE="Shot on 16mm film.")
        self.keys()
        self.assertTrue(all(b["prompt"].endswith("Shot on 16mm film.") for b in self.api.edits()))

    def test_a_key_books_its_estimate_and_lowers_its_shots_reservation(self):
        out = self.keys()
        keys = self.rows("key")
        self.assertEqual([(k["status"], k["cost_micro"], k["model"], k["source_id"]) for k in keys],
                         [("done", 100000, EDIT1, out["remake_id"])] * 2)
        self.assertEqual([(s["status"], s["cost_micro"]) for s in self.rows("shot")], [("pending", 1100000)] * 2)
        [row] = self.rows("remake")
        self.assertEqual(row["status"], "pending")
        self.assertEqual(self.spent(), 2400000)

    def test_a_refused_edit_goes_to_the_next_edit_model(self):
        self.api.edit_rule = lambda body: REFUSED if body["model"] == EDIT1 else None
        out = self.keys()
        self.assertEqual([k["model"] for k in out["keys"]], [EDIT2, EDIT2])
        self.assertEqual([k["cost_usd"] for k in out["keys"]], [0.06, 0.06])

    def test_two_failed_edits_stop_the_remake_and_release_the_reservations(self):
        self.api.edit_rule = lambda body: REFUSED if "shot 2" in body["prompt"] and body["model"] != EDIT3 else None
        with self.assertRaises(hand.HandError) as caught:
            self.keys()
        self.assertIn("shot 2", str(caught.exception))
        shot2 = [b["model"] for b in self.api.edits() if "shot 2" in b["prompt"]]
        self.assertEqual(shot2, [EDIT1, EDIT2])  # never a third
        [row] = self.rows("remake")
        self.assertEqual(row["status"], "failed")
        self.assertTrue(all(s["status"] == "failed" and s["cost_micro"] == 0 for s in self.rows("shot")))
        # A key shot 1 bought is kept on the ledger.
        self.assertEqual(self.spent(), sum(k["cost_micro"] for k in self.rows("key")))

    def test_no_credit_on_an_edit_stops_everything(self):
        self.api.edit_rule = lambda body: SurplusError("Surplus answered 402: insufficient credit", 402)
        with self.assertRaises(hand.HandError):
            self.keys()
        self.assertTrue(all(b["model"] == EDIT1 for b in self.api.edits()))
        self.assertEqual(self.rows("key"), [])

    def test_an_edit_of_unknown_price_books_the_dearest_measured(self):
        self.api.prices = {}
        out = self.keys()
        self.assertEqual([k["cost_usd"] for k in out["keys"]], [remakes.KEY_UNKNOWN_MICRO / 1e6] * 2)

    def test_a_malformed_remake_is_refused_before_anything(self):
        bad = [
            {**KEYS, "shots": []},
            {**KEYS, "shots": [shot(n, 3.0) for n in range(1, 8)]},
            {**KEYS, "shots": [shot(2, 3.0), shot(1, 3.0)]},
            {**KEYS, "shots": [shot(1, 0.5)]},
            {**KEYS, "shots": [shot(1, 10.5)]},
            {**KEYS, "shots": [shot(1, True)]},
            {**KEYS, "shots": [shot(1, 3.0, edit="too short")]},
            {**KEYS, "shots": [shot(1, 3.0, motion="m" * 601)]},
            {**KEYS, "shots": [shot(1, 3.0, teeny="t" * 161)]},
            {**KEYS, "shots": [shot(1, 3.0, frame="state/media/remake/s2/shot-1.jpg")]},
            {**KEYS, "shots": [shot(1, 3.0, frame="state/media/remake/s1/../s1/shot-1.jpg")]},
            {**KEYS, "shots": [shot(1, 3.0, label="real footage")]},
            {**KEYS, "study": "../etc"},
            {**KEYS, "figures": ["a", "b", "c", "d"]},
            {**KEYS, "aspect": "4:3"},
            {**KEYS, "logline": ""},
            {**KEYS, "label": "real footage"},
            {**KEYS, "wait_seconds": 5},
            {**KEYS, "source": "youtube"},
        ]
        for body in bad:
            with self.subTest(body=body):
                with self.assertRaises(hand.Refused):
                    self.keys(body)
        self.assertEqual((self.api.calls, self.rows()), ([], []))

    def test_a_frame_that_is_not_there_is_refused_before_anything(self):
        (self.root / "state" / "media" / "remake" / "s1" / "shot-2.jpg").unlink()
        with self.assertRaises(hand.Refused):
            self.keys()
        self.assertEqual((self.api.calls, self.rows()), ([], []))

    # --- the render ---------------------------------------------------------------------------

    def test_the_render_replays_what_the_keys_stored(self):
        made, out = self.both()
        rid = made["remake_id"]
        videos = self.api.videos()
        self.assertEqual([b["model"] for b in videos], [I2V1, I2V1])
        key = assets._data_uri(self.root / made["keys"][0]["path"])
        self.assertEqual(videos[0]["image_url"], key)
        self.assertTrue(videos[0]["prompt"].startswith(KEYS["shots"][0]["motion"]))
        self.assertIn("no cuts", videos[0]["prompt"])
        self.assertEqual([b["duration_seconds"] for b in videos], [5, 5])
        keys = [h["idempotency-key"] for m, p, b, h in self.api.calls if p == "/v1/video/generations" and m == "POST"]
        self.assertEqual(keys, [f"teeny-remake-{rid}-shot1-{I2V1}", f"teeny-remake-{rid}-shot2-{I2V1}"])
        [cut] = self.reel.jobs
        self.assertEqual(cut, {"clips": [f"media/remake-{rid}-shot-1.mp4", f"media/remake-{rid}-shot-2.mp4"],
                               "label": "AI parody · @teenydroid", "aspect": "9:16", "trims": [2.4, 4.0],
                               "sound": "media/remake/s1/audio.m4a"})
        self.assertEqual(out["asset"], f"state/media/remake-{rid}.mp4")
        self.assertEqual(out["asset_id"], rid)
        self.assertEqual(out["frames"], [f"state/media/remake-{rid}-frame-0.jpg", f"state/media/remake-{rid}-frame-1.jpg"])
        self.assertEqual((out["label"], out["labelled"], out["models"]), ("AI parody · @teenydroid", True, [I2V1, I2V1]))
        self.assertEqual(out["cost_usd"], 0.2 + 0.9)
        self.assertEqual(out["keys"], made["keys"])
        self.assertEqual((out["seconds"], out["shot_seconds"]), (10.0, [5.0, 5.0]))
        [row] = self.rows("remake")
        self.assertEqual((row["status"], row["cost_micro"]), ("done", 0))
        self.assertEqual(json.loads(row["detail"])["source"]["video_id"], "abc123")
        self.assertEqual(self.spent(), 1100000)
        self.assertEqual(self.reel.deleted, [CUT_ID])

    def test_the_render_takes_nothing_but_the_id_the_sound_and_a_wait(self):
        made = self.keys()
        for extra in ({"shots": KEYS["shots"]}, {"motion": "anything"}, {"label": "real footage"},
                      {"aspect": "16:9"}, {"prompt": "x"}):
            with self.subTest(extra=extra):
                with self.assertRaises(hand.Refused):
                    self.remake({"remake_id": made["remake_id"], "sound": SOUND, **extra})
        self.assertEqual(self.api.videos(), [])

    def test_an_unknown_used_or_failed_remake_id_is_refused(self):
        with self.assertRaises(hand.Refused) as caught:
            self.remake({"remake_id": 99, "sound": None})
        self.assertEqual(caught.exception.detail["rule"], "remake_id")
        made, _ = self.both()
        with self.assertRaises(hand.Refused) as caught:
            self.remake({"remake_id": made["remake_id"], "sound": SOUND})
        self.assertEqual(caught.exception.detail["rule"], "used")
        self.assertEqual(len(self.api.videos()), 2)

    def test_a_remake_whose_render_failed_cannot_be_rendered_again(self):
        made = self.keys()
        self.reel.fail = "ffmpeg could not cut it"
        with self.assertRaises(hand.HandError):
            self.remake({"remake_id": made["remake_id"], "sound": SOUND})
        self.reel.fail = None
        with self.assertRaises(hand.Refused) as caught:
            self.remake({"remake_id": made["remake_id"], "sound": SOUND})
        self.assertEqual(caught.exception.detail["rule"], "failed")

    def test_a_stale_remake_is_refused_and_releases_its_reservation_keeping_its_keys(self):
        made = self.keys()
        os.environ["TEENY_NOW"] = str(T0 + 2 * 3600 + 1)
        with self.assertRaises(hand.Refused) as caught:
            self.remake({"remake_id": made["remake_id"], "sound": SOUND})
        self.assertEqual(caught.exception.detail["rule"], "stale")
        self.assertEqual(self.api.videos(), [])
        [row] = self.rows("remake")
        self.assertEqual(row["status"], "failed")
        self.assertTrue(all(s["status"] == "failed" and s["cost_micro"] == 0 for s in self.rows("shot")))
        self.assertEqual(self.spent(), 200000)

    def test_an_expired_remake_is_released_when_the_next_one_is_claimed(self):
        self.env(MEDIA_REMAKE_MAX_PER_DAY=2, MEDIA_SKIT_MAX_PER_DAY=2)
        self.keys()
        os.environ["TEENY_NOW"] = str(T0 + 2 * 3600 + 1)
        self.keys()
        first, second = self.rows("remake")
        self.assertEqual((first["status"], second["status"]), ("failed", "pending"))
        self.assertEqual(self.spent(), 200000 + 200000 + 2 * 1100000)

    def test_the_sound_is_only_the_remakes_own_source_audio(self):
        made = self.keys()
        for sound in ("state/media/remake/s2/audio.m4a", "state/media/remake/s1/../s2/audio.m4a",
                      "state/media/remake/s1/shot-1.jpg", "/etc/passwd", "media/remake/s1/audio.m4a", 7):
            with self.subTest(sound=sound):
                with self.assertRaises(hand.Refused) as caught:
                    self.remake({"remake_id": made["remake_id"], "sound": sound})
                self.assertEqual(caught.exception.detail["rule"], "sound")
        self.assertEqual(self.api.videos(), [])

    def test_no_sound_keeps_each_clips_own_trimmed(self):
        made, out = self.both(sound=None)
        [cut] = self.reel.jobs
        self.assertEqual((cut["trims"], "sound" in cut), ([2.4, 4.0], False))

    def test_the_operator_can_turn_the_source_sound_off(self):
        self.env(MEDIA_REMAKE_SOUND=0)
        self.both()
        [cut] = self.reel.jobs
        self.assertNotIn("sound", cut)

    def test_a_cut_without_its_label_is_never_handed_over(self):
        made = self.keys()
        self.reel.labelled = False
        with self.assertRaises(hand.HandError) as caught:
            self.remake({"remake_id": made["remake_id"], "sound": SOUND})
        self.assertIn("label", str(caught.exception))
        [row] = self.rows("remake")
        self.assertEqual((row["status"], row["path"]), ("failed", None))
        self.assertFalse(list((self.root / "state" / "media").glob(f"remake-{row['id']}.mp4")))

    # --- the money on the video shots ----------------------------------------------------------

    def test_a_keys_cost_lowers_what_its_video_may_cost(self):
        # $1.20 a shot, $0.10 for the key: $1.10 is left, so a job that can
        # cost $1.15 is cancelled although it is under $1.20.
        self.api.maxes[I2V1] = 1150000
        made, out = self.both()
        self.assertEqual(out["models"], [I2V2, I2V2])
        self.assertEqual(len(self.api.deleted()), 2)

    def test_a_string_micro_usdc_ceiling_is_what_the_row_reserves(self):
        self.api.quotes[I2V1], self.api.maxes[I2V1] = 490000, 980000
        self.api.jobs[I2V1] = ["running", "running", "succeeded"]
        self.both()
        self.assertIn(980000, self.reserved_seen)
        self.assertEqual([s["cost_micro"] for s in self.rows("shot")], [450000, 450000])

    def test_a_failed_render_keeps_the_keys_and_releases_the_rest(self):
        for model in remakes.REMAKE_MODELS.split(","):
            self.api.jobs[model] = ["running", "failed"]
        made = self.keys()
        with self.assertRaises(hand.HandError):
            self.remake({"remake_id": made["remake_id"], "sound": SOUND})
        self.assertTrue(all(s["status"] == "failed" and s["cost_micro"] == 0 for s in self.rows("shot")))
        self.assertEqual(self.spent(), 200000)
        [row] = self.rows("remake")
        self.assertEqual(row["status"], "failed")

    def test_a_download_that_answers_502_is_tried_again_and_lands(self):
        self.api.artifact_errors = [SurplusError("the download answered 502", 502)] * 2
        made, out = self.both()
        self.assertEqual(out["models"], [I2V1, I2V1])
        self.assertTrue(all(s["status"] == "done" for s in self.rows("shot")))
        self.assertIn(shots.DOWNLOAD_BACKOFF[0], self.slept)

    def test_a_billed_shot_whose_file_never_came_keeps_its_cost(self):
        self.api.artifact_errors, self.api.stuck = [SurplusError("the download answered 502", 502)], True
        made = self.keys()
        with self.assertRaises(hand.HandError) as caught:
            self.remake({"remake_id": made["remake_id"], "sound": SOUND, "wait_seconds": 120})
        self.assertIn("download", str(caught.exception))
        unknown = [s for s in self.rows("shot") if s["status"] == "unknown"]
        self.assertTrue(unknown)
        self.assertTrue(all(s["cost_micro"] == 450000 for s in unknown))
        self.assertEqual(self.spent(), 200000 + 450000 * len(unknown))
        self.assertGreater(self.api.downloads, 2)

    # --- lengths ------------------------------------------------------------------------------

    CATALOG = {"data": [{"id": I2V1, "durations_seconds": [5, 10]}, {"id": I2V2, "durations_seconds": [4, 6, 8]}]}

    def test_a_clip_is_the_shortest_length_on_sale_that_covers_its_source_shot(self):
        self.api.catalog = self.CATALOG
        self.both()
        self.assertEqual([(b["model"], b["duration_seconds"]) for b in self.api.videos()], [(I2V1, 5), (I2V1, 5)])
        self.assertEqual(shots.at_least(5.2, [5, 10]), 10)
        self.assertEqual(shots.at_least(5.0, [5, 10]), 5)
        self.assertEqual(shots.at_least(6.0, [4, 6, 8]), 6)  # never the nearest-but-shorter
        self.assertIsNone(shots.at_least(9.0, [4, 6, 8]))
        self.assertEqual(shots.at_least(2.4, None), 5)
        self.assertEqual(shots.at_least(7.2, []), 8)

    def test_a_model_that_sells_nothing_long_enough_passes_the_shot_on(self):
        self.api.catalog = {"data": [{"id": I2V1, "durations_seconds": [3, 4]},
                                     {"id": I2V2, "durations_seconds": [4, 6, 8]}]}
        self.keys()  # 2.4 s and 4.0 s
        self.setUp()
        self.api.catalog = {"data": [{"id": I2V1, "durations_seconds": [3, 4]},
                                     {"id": I2V2, "durations_seconds": [4, 6, 8]}]}
        made = self.keys({**KEYS, "shots": [shot(1, 5.5)]})
        self.remake({"remake_id": made["remake_id"], "sound": SOUND})
        self.assertEqual([(b["model"], b["duration_seconds"]) for b in self.api.videos()], [(I2V2, 6)])

    def test_unverified_fields_come_only_from_the_operators_setting(self):
        self.env(MEDIA_REMAKE_PARAMS=json.dumps({"*": {"aspect_ratio": "{aspect}", "image_url": "sneaky"}}))
        self.both()
        body = self.api.videos()[0]
        self.assertEqual(body["aspect_ratio"], "9:16")
        self.assertTrue(body["image_url"].startswith("data:image/png;base64,"))


class KindsTest(unittest.TestCase):
    """A ledger made before remakes checks `kind` against a list without
    `remake` and `key`: it is rebuilt once, every row kept."""

    def setUp(self):
        import tempfile

        self.tmp = tempfile.TemporaryDirectory()
        self._env = dict(os.environ)
        os.environ["TEENY_ROOT"] = self.tmp.name
        hand._ENV_CACHE = None

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)
        hand._ENV_CACHE = None
        self.tmp.cleanup()

    def test_an_old_ledger_gains_the_remake_kinds_and_keeps_its_rows(self):
        old = assets.SCHEMA.replace(assets.KINDS_CHECK.split("kind IN ")[1],
                                    "('image', 'video', 'reel', 'song', 'music', 'meme', 'comic', 'skit', 'shot')")
        self.assertNotIn("'remake'", old)
        state = Path(self.tmp.name) / "state"
        state.mkdir()
        conn = sqlite3.connect(state / "hands.db")
        conn.executescript(old)
        conn.execute("ALTER TABLE media_assets ADD COLUMN detail TEXT")
        conn.execute("ALTER TABLE media_assets ADD COLUMN alt TEXT")
        conn.execute("INSERT INTO media_assets (kind, status, prompt, cost_micro, created_at) VALUES ('skit', 'done', 's', 0, 1)")
        conn.execute("INSERT INTO media_assets (kind, status, prompt, source_id, cost_micro, created_at)"
                     " VALUES ('shot', 'done', 'p', 1, 450000, 1)")
        conn.commit()
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO media_assets (kind, status, prompt, created_at) VALUES ('remake', 'done', 'r', 1)")
        conn.close()
        conn = assets._db()
        try:
            rows = conn.execute("SELECT kind, cost_micro, source_id FROM media_assets ORDER BY id").fetchall()
            self.assertEqual([tuple(r) for r in rows], [("skit", 0, None), ("shot", 450000, 1)])
            with hand.transaction(conn):
                conn.execute("INSERT INTO media_assets (kind, status, prompt, created_at) VALUES ('remake', 'done', 'r', 1)")
                conn.execute("INSERT INTO media_assets (kind, status, prompt, created_at) VALUES ('key', 'done', 'k', 1)")
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
