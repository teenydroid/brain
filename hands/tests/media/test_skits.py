"""hands/video skit: a parody video bought one text-to-video job a shot on
Surplus and cut, with its own sound and its label, on the reel service.
Off unless the operator turns it on; the money fences are the media hands'
own; a refusal moves a shot to the next model; a failure costs nothing; an
unlabelled cut is never handed over. Surplus and the reel service are
fakes: nothing here spends or renders anything."""

import json
import os
import struct
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent / "lib"))

from teeny_hands import hand  # noqa: E402
from teeny_hands.guard import skit as rules  # noqa: E402
from teeny_hands.media import assets, skits  # noqa: E402
from teeny_hands.media.surplus import SurplusError  # noqa: E402
from teeny_hands.reel.service import ReelError  # noqa: E402

T0 = 1_789_369_200.0  # 2026-09-14T07:00:00Z
SHOT = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 32
CUT_ID = "0e19deb3-7e54-4a71-b103-2e394ae488be"
FIRST, SECOND, THIRD, FOURTH = skits.SKIT_MODELS.split(",")


def box(name: bytes, body: bytes) -> bytes:
    return struct.pack(">I4s", 8 + len(body), name) + body


def mp4(seconds: float = 10.0) -> bytes:
    mvhd = box(b"mvhd", b"\x00\x00\x00\x00" + struct.pack(">IIII", 0, 0, 1000, int(seconds * 1000)) + b"\x00" * 80)
    traks = b"".join(box(b"trak", box(b"mdia", box(b"hdlr", b"\x00" * 8 + kind + b"\x00" * 12)))
                     for kind in (b"vide", b"soun"))
    return box(b"ftyp", b"isom\x00\x00\x02\x00isomiso2") + box(b"moov", mvhd + traks) + box(b"mdat", b"\x00" * 32)


class FakeSurplus:
    """Text-to-video as Surplus answers it. `fail` maps a model to the
    SurplusError its submission raises; `quotes` a model to its estimate in
    micros; `jobs` a model to the statuses its polls walk through; `errors`
    a model to the error a failed job carries."""

    def __init__(self):
        self.calls = []
        self.fail, self.quotes, self.jobs, self.errors = {}, {}, {}, {}
        self.maxes, self.no_usage, self.late_usage = {}, set(), set()
        self.rates = {}  # a model to its max_cost_usdc per second asked, in micros
        self._asked_done = {}
        self._polls = {}
        self.n = 0

    catalog = None  # GET /v1/models answers this when set, else a 503

    def call(self, method, path, body=None, timeout=60.0, headers=None):
        self.calls.append((method, path, body, headers))
        if path == "/v1/models" and method == "GET":
            if self.catalog is None:
                raise SurplusError("Surplus answered 503", 503)
            return self.catalog
        if path == "/v1/video/generations" and method == "POST":
            model = body["model"]
            if model in self.fail:
                raise self.fail[model]
            self.n += 1
            job = f"job-{self.n}-{model}"
            self._polls[job] = (model, list(self.jobs.get(model, ["running", "succeeded"])))
            out = {"id": job, "status": "queued", "estimated_cost_usdc": str(self.quotes.get(model, 400000))}
            if model in self.maxes:
                out["max_cost_usdc"] = str(self.maxes[model])
            if model in self.rates:
                out["max_cost_usdc"] = str(self.rates[model] * body["duration_seconds"])
            return out
        if path.startswith("/v1/video/generations/") and method == "GET":
            job = path.rsplit("/", 1)[1]
            model, polls = self._polls[job]
            status = polls.pop(0) if len(polls) > 1 else polls[0]
            out = {"id": job, "status": status}
            if status == "succeeded":
                out["results"] = [{"url": f"https://api.surplusintelligence.ai/v1/media/artifacts/{job}/0"}]
                seen = self._asked_done[job] = self._asked_done.get(job, 0) + 1
                late = model in self.late_usage and seen == 1
                if model not in self.no_usage and not late:
                    out["usage"] = {"buyer_cost_usdc": "450000"}
            if status == "failed":
                out["error"] = self.errors.get(model, {"type": "provider_unavailable", "retryable": True})
            return out
        if method == "DELETE":
            return {}
        raise AssertionError(f"unexpected call {method} {path}")

    def download(self, url, limit, timeout=120.0):
        return SHOT

    def submitted(self):
        return [b["model"] for m, p, b, h in self.calls if p == "/v1/video/generations" and m == "POST"]

    def deleted(self):
        return [p.rsplit("/", 1)[1] for m, p, b, h in self.calls if m == "DELETE"]


class FakeReel:
    def __init__(self):
        self.up = True
        self.jobs = []
        self.labelled = True
        self.echo = None
        self.deleted = []
        self.fail = None

    def ping(self):
        if not self.up:
            raise ReelError("could not reach the reel service (ConnectionRefusedError)")

    def cut(self, job, timeout=600.0):
        self.jobs.append(job)
        if self.fail:
            raise ReelError(self.fail)
        return {"cut_id": CUT_ID, "seconds": 10.0, "shots": [5.0, 5.0], "frames": ["frame-0.jpg", "frame-1.jpg"],
                "label": self.echo if self.echo is not None else job["label"], "labelled": self.labelled,
                "audio": [True, False]}

    def cut_file(self, cut_id, name, limit):
        return mp4() if name == "cut.mp4" else JPEG

    def cut_delete(self, cut_id):
        self.deleted.append(cut_id)


SKIT = {"shots": [{"prompt": "Wide anamorphic shot of a marble showroom on the moon, a robotaxi under a velvet sheet"},
                  {"prompt": "Low-angle close-up: the robotaxi beats its owner at chess, the board grows to cover the moon"}],
        "story": "trend:t1", "figures": ["Elon Musk"], "logline": "a robotaxi wins at chess on the moon"}


class SkitTest(unittest.TestCase):
    ONE_TAKE = "MEDIA_SKIT_ONE_TAKE=0\n"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "secrets").mkdir()
        # These tests pin the per-shot render (the one-take's fallback);
        # OneTakeTest below turns the default back on.
        self.write("DRY_RUN=0\nSURPLUS_API_KEY=inf_test\nX_BOT_USERNAME=teenydroid\nMEDIA_SKIT_MAX_PER_DAY=1\n"
                   "MEDIA_MAX_USD_PER_DAY=5.00\n" + self.ONE_TAKE)
        self._env = dict(os.environ)
        os.environ["TEENY_ROOT"] = str(self.root)
        os.environ["TEENY_NOW"] = str(T0)
        os.environ.pop("DRY_RUN", None)
        hand._ENV_CACHE = None
        self.api = FakeSurplus()
        self.reel = FakeReel()
        self.clock = [0.0]
        self.reserved_seen = []

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
        # What the shots' rows carry while the jobs run.
        self.reserved_seen += [r["cost_micro"] for r in self.rows("shot")]
        self.clock[0] += seconds

    def skit(self, body=None):
        return skits.generate_skit(body or SKIT, api=self.api, reel=self.reel, sleep=self.sleep,
                                   clock=lambda: self.clock[0])

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

    # --- off, and the money fences ---------------------------------------------------

    def test_off_by_default_and_nothing_is_asked(self):
        self.write("DRY_RUN=0\nSURPLUS_API_KEY=inf_test\n")
        with self.assertRaises(hand.Refused) as caught:
            self.skit()
        self.assertEqual(caught.exception.detail["rule"], "off")
        self.assertIn("MEDIA_SKIT_MAX_PER_DAY is 0", caught.exception.reason)
        self.assertEqual((self.api.calls, self.reel.jobs), ([], []))
        self.assertEqual(self.rows(), [])

    def test_the_code_defaults_are_conservative(self):
        self.write("DRY_RUN=1\n")
        out = self.skit()
        self.assertEqual((out["enabled"], out["max_usd_per_shot"], out["reserve_usd"]), (False, 1.6, 3.2))
        self.assertEqual(out["models"], skits.SKIT_MODELS.split(","))
        # One take by default, on the one model verified to follow a script,
        # capped at what the shots it replaces may cost.
        self.assertEqual(out["one_take"]["models"], skits.ONE_TAKE_MODELS.split(","))
        self.assertEqual(out["one_take"]["max_usd"], 3.2)

    def test_a_dry_run_says_what_it_would_make_and_calls_nothing(self):
        self.env(DRY_RUN=1)
        out = self.skit()
        self.assertTrue(out["dry_run"])
        self.assertEqual(out["label"], "AI parody · @teenydroid")
        self.assertEqual(out["requests"][0], {"model": FIRST, "prompt": SKIT["shots"][0]["prompt"], "duration_seconds": 5})
        self.assertEqual(self.api.calls, [])

    def test_the_day_has_a_skit_cap(self):
        self.skit()
        with self.assertRaises(hand.Refused) as caught:
            self.skit()
        self.assertEqual(caught.exception.detail["rule"], "per_day")

    def test_a_skit_that_failed_after_buying_counts_toward_the_cap(self):
        self.reel.fail = "ffmpeg could not cut it"
        with self.assertRaises(hand.HandError):
            self.skit()
        self.reel.fail = None
        with self.assertRaises(hand.Refused) as caught:
            self.skit()
        self.assertEqual(caught.exception.detail["rule"], "per_day")

    def test_every_shot_reserves_its_cap_against_the_shared_daily_budget(self):
        self.env(MEDIA_MAX_USD_PER_DAY="1.00", MEDIA_SKIT_MAX_USD_PER_SHOT="0.60")
        with self.assertRaises(hand.Refused) as caught:
            self.skit()
        self.assertEqual(caught.exception.detail["rule"], "budget")
        self.assertEqual(caught.exception.detail["need_usd"], 1.2)
        self.assertEqual((self.api.calls, self.rows()), ([], []))

    def test_whatever_else_was_bought_today_counts(self):
        conn = assets._db()
        with hand.transaction(conn):
            conn.execute("INSERT INTO media_assets (kind, status, prompt, cost_micro, created_at)"
                         " VALUES ('video', 'done', 'v', 4200000, ?)", (T0,))
        conn.close()
        with self.assertRaises(hand.Refused) as caught:
            self.skit()
        self.assertEqual(caught.exception.detail["rule"], "budget")

    def test_the_runway_cuts_skits_like_every_media_hand(self):
        self.env(RUNWAY=1)
        with self.assertRaises(hand.Refused) as caught:
            self.skit()
        self.assertEqual(caught.exception.detail["rule"], "runway")
        self.assertEqual(self.api.calls, [])

    def test_no_reel_service_no_money_spent(self):
        self.reel.up = False
        with self.assertRaises(hand.HandError) as caught:
            self.skit()
        self.assertIn("nothing was bought", str(caught.exception))
        self.assertEqual((self.api.calls, self.rows()), ([], []))

    # --- the shots ------------------------------------------------------------------------

    def test_a_skit_is_one_job_a_shot_cut_with_its_label(self):
        out = self.skit()
        self.assertEqual(self.api.submitted(), [FIRST, FIRST])
        keys = [h["idempotency-key"] for m, p, b, h in self.api.calls if m == "POST"]
        skit_id = out["asset"]["id"]
        self.assertEqual(keys, [f"teeny-skit-{skit_id}-shot1-{FIRST}", f"teeny-skit-{skit_id}-shot2-{FIRST}"])
        [cut] = self.reel.jobs
        self.assertEqual(cut, {"clips": [f"media/skit-{skit_id}-shot-1.mp4", f"media/skit-{skit_id}-shot-2.mp4"],
                               "label": "AI parody · @teenydroid", "aspect": "16:9"})
        self.assertEqual((out["label"], out["labelled"]), ("AI parody · @teenydroid", True))
        self.assertEqual(out["asset"]["path"], f"state/media/skit-{skit_id}.mp4")
        self.assertEqual(out["frames"], [f"state/media/skit-{skit_id}-frame-0.jpg", f"state/media/skit-{skit_id}-frame-1.jpg"])
        self.assertEqual(self.reel.deleted, [CUT_ID])
        detail = out["asset"]["detail"]
        self.assertEqual((detail["story"], detail["figures"], detail["models"], detail["cost_usd"]),
                         ("trend:t1", ["Elon Musk"], [FIRST, FIRST], 0.9))
        self.assertTrue(detail["labelled"])

    def test_the_ledger_puts_the_money_on_the_shots(self):
        out = self.skit()
        [skit_row] = self.rows("skit")
        shots = self.rows("shot")
        self.assertEqual((skit_row["status"], skit_row["cost_micro"]), ("done", 0))
        self.assertEqual([(s["status"], s["cost_micro"], s["estimated"], s["source_id"]) for s in shots],
                         [("done", 450000, 0, out["asset"]["id"])] * 2)
        self.assertEqual(self.spent(), 900000)

    def test_the_label_needs_no_username_to_be_there(self):
        self.write("DRY_RUN=0\nSURPLUS_API_KEY=inf_test\nMEDIA_SKIT_MAX_PER_DAY=1\nMEDIA_MAX_USD_PER_DAY=5\n")
        self.assertEqual(self.skit()["label"], "AI parody")

    def test_the_caller_cannot_set_the_label(self):
        with self.assertRaises(hand.Refused):
            self.skit({**SKIT, "label": "real footage"})

    def test_a_cut_without_its_label_is_never_handed_over(self):
        for labelled, echo in ((False, None), (True, "something else")):
            with self.subTest(labelled=labelled, echo=echo):
                self.setUp()
                self.reel.labelled, self.reel.echo = labelled, echo
                with self.assertRaises(hand.HandError) as caught:
                    self.skit()
                self.assertIn("label", str(caught.exception))
                [row] = self.rows("skit")
                self.assertEqual((row["status"], row["path"]), ("failed", None))
                self.assertFalse(list((self.root / "state" / "media").glob(f"skit-{row['id']}.mp4")))
                self.assertEqual(self.reel.deleted, [CUT_ID])
                self.tearDown()
        self.setUp()

    def test_a_shot_quoted_over_its_cap_is_cancelled_and_the_next_model_asked(self):
        self.api.quotes[FIRST] = 1700000
        out = self.skit()
        self.assertEqual(self.api.submitted(), [FIRST, SECOND, FIRST, SECOND])
        self.assertEqual(len(self.api.deleted()), 2)
        self.assertEqual(out["models"], [SECOND, SECOND])

    def test_the_cap_is_held_against_the_most_a_job_can_cost_not_its_estimate(self):
        # Surplus billed about 20% over its quote (2026-11-19: kling 5 s quoted
        # 598000, billed 717600): max_cost_usdc is what can really be charged.
        # And max_cost_usdc is about twice the bill (kling 5 s: 1435200 max, 717600 billed).
        self.api.quotes[FIRST], self.api.maxes[FIRST] = 717600, 1700000
        out = self.skit()
        self.assertEqual(out["models"], [SECOND, SECOND])
        self.assertEqual(len(self.api.deleted()), 2)

    def test_the_shot_reserves_the_jobs_most_until_its_real_cost_comes(self):
        self.api.quotes[FIRST], self.api.maxes[FIRST] = 598000, 850000
        self.api.jobs[FIRST] = ["running"]
        with self.assertRaises(hand.HandError):
            self.skit({**SKIT, "wait_seconds": 60})
        # While it ran, the row carried the job's most, not its estimate.
        self.assertIn(850000, self.reserved_seen)

    def test_a_billed_cost_that_comes_just_after_success_is_read_once_more(self):
        # seedance-2-0's usage arrived a little after "succeeded" (2026-11-19).
        self.api.quotes[FIRST], self.api.maxes[FIRST] = 741000, 1482000
        self.api.late_usage.add(FIRST)
        self.skit()
        shots = self.rows("shot")
        self.assertEqual([(s["status"], s["cost_micro"], s["estimated"]) for s in shots], [("done", 450000, 0)] * 2)

    def test_a_success_with_no_billed_cost_keeps_the_reservation_as_an_estimate(self):
        # seedance-2-0 succeeded on 2026-11-19 with no usage.buyer_cost_usdc.
        self.api.quotes[FIRST], self.api.maxes[FIRST] = 517275, 700000
        self.api.no_usage.add(FIRST)
        self.skit()
        gets = [p for m, p, b, h in self.api.calls if m == "GET" and p.startswith("/v1/video/generations/")]
        self.assertEqual(len(gets), 6)  # each shot: running, succeeded, then once more for the bill
        shots = self.rows("shot")
        self.assertEqual([(s["status"], s["cost_micro"], s["estimated"]) for s in shots], [("done", 700000, 1)] * 2)

    def test_a_content_refusal_moves_the_shot_to_the_next_model(self):
        # Hosted models differ on real people: a refusal is "try another".
        self.api.fail[FIRST] = SurplusError("Surplus answered 400: the prompt violates the content policy "
                                            "(public figure likeness)", 400)
        out = self.skit()
        self.assertEqual(out["models"], [SECOND, SECOND])

    def test_a_job_refused_while_running_is_resubmitted_to_the_next_model(self):
        self.api.jobs[FIRST] = ["running", "failed"]
        self.api.errors[FIRST] = {"type": "invalid_request", "message": "blocked by the safety filter"}
        out = self.skit()
        self.assertEqual(out["models"], [SECOND, SECOND])
        shots = self.rows("shot")
        self.assertEqual([s["model"] for s in shots], [SECOND, SECOND])
        self.assertEqual(self.spent(), 900000)

    def test_a_seller_outage_moves_on_but_no_credit_stops_everything(self):
        self.api.fail[FIRST] = SurplusError("Surplus answered 503: no healthy sellers", 503)
        self.assertEqual(self.skit()["models"], [SECOND, SECOND])
        self.setUp()
        self.api.fail[FIRST] = SurplusError("Surplus answered 402: insufficient credit", 402)
        with self.assertRaises(hand.HandError):
            self.skit()
        self.assertEqual(self.api.submitted(), [FIRST])  # asked once, no other model, no second shot
        self.assertEqual(self.reel.jobs, [])
        self.assertTrue(all(s["status"] == "failed" and s["cost_micro"] == 0 for s in self.rows("shot")))

    def test_a_shot_no_model_can_make_fails_the_skit_cancels_the_rest_and_costs_nothing(self):
        for model in skits.SKIT_MODELS.split(","):
            self.api.jobs[model] = ["running", "failed"]
        with self.assertRaises(hand.HandError) as caught:
            self.skit()
        self.assertIn("no model could make it", str(caught.exception))
        [skit_row] = self.rows("skit")
        self.assertEqual(skit_row["status"], "failed")
        self.assertTrue(all(s["status"] == "failed" and s["cost_micro"] == 0 for s in self.rows("shot")))
        self.assertEqual(self.spent(), 0)
        self.assertEqual(self.reel.jobs, [])

    def test_a_render_past_the_wait_is_cancelled_and_costs_nothing(self):
        self.api.jobs[FIRST] = ["running"]
        with self.assertRaises(hand.HandError) as caught:
            self.skit({**SKIT, "wait_seconds": 60})
        self.assertIn("not done after 60 s", str(caught.exception))
        self.assertEqual(len(self.api.deleted()), 2)
        self.assertTrue(all(s["status"] == "failed" and s["cost_micro"] == 0 for s in self.rows("shot")))

    def test_a_cut_that_fails_keeps_what_the_shots_cost(self):
        self.reel.fail = "ffmpeg could not cut it"
        with self.assertRaises(hand.HandError) as caught:
            self.skit()
        self.assertEqual(caught.exception.detail["spent_usd"], 0.9)
        [skit_row] = self.rows("skit")
        self.assertEqual((skit_row["status"], skit_row["cost_micro"]), ("failed", 0))
        self.assertEqual(self.spent(), 900000)

    def test_unverified_fields_come_only_from_the_operators_setting(self):
        self.env(MEDIA_SKIT_PARAMS=json.dumps({"*": {"aspect_ratio": "{aspect}", "resolution": "{resolution}",
                                                     "model": "sneaky"},
                                               FIRST: {"generate_audio": True}}),
                 MEDIA_SKIT_RESOLUTION="720p")
        self.skit({**SKIT, "aspect": "9:16"})
        body = next(b for m, p, b, h in self.api.calls if m == "POST")
        self.assertEqual(body, {"model": FIRST, "prompt": SKIT["shots"][0]["prompt"], "duration_seconds": 5,
                                "aspect_ratio": "9:16", "resolution": "720p", "generate_audio": True})

    def test_a_shot_may_name_its_own_length(self):
        self.skit({**SKIT, "shots": [{"prompt": SKIT["shots"][0]["prompt"], "seconds": 8}]})
        body = next(b for m, p, b, h in self.api.calls if m == "POST")
        self.assertEqual(body["duration_seconds"], 8)

    # --- lengths a model sells (GET /v1/models, durations_seconds) --------------------

    CATALOG = {"data": [{"id": "kling-v3-pro-text-to-video", "durations_seconds": list(range(3, 16))},
                        {"id": "seedance-2-0-text-to-video", "durations_seconds": list(range(5, 16))},
                        {"id": "veo3-1-fast-text-to-video", "durations_seconds": [4, 6, 8]},
                        {"id": "wan-2-7-text-to-video"}]}

    def lengths(self):
        return [(b["model"], b["duration_seconds"]) for m, p, b, h in self.api.calls
                if p == "/v1/video/generations" and m == "POST"]

    def one(self, seconds):
        return {**SKIT, "shots": [{"prompt": SKIT["shots"][0]["prompt"], "seconds": seconds}]}

    def test_a_shot_is_asked_for_the_nearest_length_its_model_sells_ties_up(self):
        self.api.catalog = self.CATALOG
        self.env(MEDIA_SKIT_MODELS="veo3-1-fast-text-to-video")
        out = self.skit(self.one(5))
        self.assertEqual(self.lengths(), [("veo3-1-fast-text-to-video", 6)])
        [shot] = self.rows("shot")
        self.assertEqual(json.loads(shot["detail"])["seconds"], 6)
        self.assertTrue(out["asset"])

    def test_a_short_shot_is_lengthened_to_what_seedance_sells(self):
        self.api.catalog = self.CATALOG
        self.env(MEDIA_SKIT_MODELS="seedance-2-0-text-to-video")
        self.skit(self.one(3))
        self.assertEqual(self.lengths(), [("seedance-2-0-text-to-video", 5)])

    def test_each_model_gets_its_own_length_when_a_shot_moves_on(self):
        self.api.catalog = self.CATALOG
        self.env(MEDIA_SKIT_MODELS="veo3-1-fast-text-to-video,kling-v3-pro-text-to-video")
        self.api.fail["veo3-1-fast-text-to-video"] = SurplusError("Surplus answered 503", 503)
        self.skit(self.one(5))
        self.assertEqual(self.lengths(), [("veo3-1-fast-text-to-video", 6), ("kling-v3-pro-text-to-video", 5)])

    def test_the_catalog_is_read_once_a_skit(self):
        self.api.catalog = self.CATALOG
        self.skit()
        self.assertEqual(sum(1 for m, p, b, h in self.api.calls if p == "/v1/models"), 1)

    def test_an_unreadable_catalog_or_a_model_with_no_lengths_sends_the_length_as_asked(self):
        self.env(MEDIA_SKIT_MODELS="veo3-1-fast-text-to-video")
        self.skit(self.one(5))
        self.assertEqual(self.lengths(), [("veo3-1-fast-text-to-video", 5)])
        self.setUp()
        self.api.catalog = self.CATALOG
        self.env(MEDIA_SKIT_MODELS="wan-2-7-text-to-video")
        self.skit(self.one(7))
        self.assertEqual(self.lengths(), [("wan-2-7-text-to-video", 7)])

    def test_a_malformed_skit_is_refused_before_anything(self):
        for bad in ({**SKIT, "shots": []}, {**SKIT, "shots": SKIT["shots"] * 3}, {**SKIT, "aspect": "4:3"},
                    {**SKIT, "logline": ""}, {**SKIT, "story": None}, {**SKIT, "wait_seconds": 5}):
            with self.subTest(bad=bad):
                with self.assertRaises(hand.Refused):
                    self.skit(bad)
        self.assertEqual(self.api.calls, [])


WAN3, SEEDANCE, WAN27 = "venice-wan-3-0-text-to-video", "seedance-2-0-text-to-video", "wan-2-7-text-to-video"
#: max_cost_usdc a second asked, as each was seen on a paid one-take render (2026-11-20):
#: wan-3-0 30 s max 7020000; seedance-2-0 15 s max 5112000; wan-2-7 15 s max 3267000.
WAN3_RATE, SEEDANCE_RATE, WAN27_RATE = 234000, 340800, 217800
ONE_TAKE_CATALOG = {"data": [{"id": WAN3, "durations_seconds": [2, 5, 10, 15, 20, 25, 30]},
                             {"id": SEEDANCE, "durations_seconds": list(range(5, 16))},
                             {"id": WAN27, "durations_seconds": [5, 10, 15]},
                             {"id": "kling-v3-pro-text-to-video", "durations_seconds": list(range(3, 16))}]}
THREE = {"shots": [{"prompt": "Wide: Jensen Huang grips a three-storey brass lever on the lawn and hauls it down"},
                   {"prompt": "Low angle over his shoulder: desks fly out of the building. He says: \"that one had my stapler.\""},
                   {"prompt": "Crane shot rising from Jensen Huang on the lawn as the whole building folds into a box"}],
         "story": "trend:t1", "figures": ["Jensen Huang"],
         "logline": "jensen huang pulls a giant lever and his office empties itself onto the lawn",
         "look": {"Jensen Huang": "silver hair, black glasses, black leather jacket, holding a paper coffee cup"},
         "setting": "an enormous striped lawn in front of a jewel-shaped glass office building at golden hour, "
                    "anamorphic lens, film grain",
         "score": "one continuous orchestral score that builds to a choir, then one elevator ding"}


class OneTakeTest(unittest.TestCase):
    """The default: a skit of two or more shots is one job, one multi-shot
    script on the first one-take model (wan-3-0 at up to 20 s and 720p, with
    its sound; then seedance-2-0 and wan-2-7, each seen on a paid render to
    keep a face, a set and a soundtrack across hard cuts in one job), capped
    at what the shots it replaces may cost, and the per-shot render only
    when that job cannot be had."""

    ONE_TAKE = ""
    tearDown, write, env, sleep = SkitTest.tearDown, SkitTest.write, SkitTest.env, SkitTest.sleep
    skit, rows, spent = SkitTest.skit, SkitTest.rows, SkitTest.spent

    def setUp(self):
        SkitTest.setUp(self)
        self.api.catalog = ONE_TAKE_CATALOG
        self.api.rates.update({WAN3: WAN3_RATE, SEEDANCE: SEEDANCE_RATE, WAN27: WAN27_RATE})

    def posts(self):
        return [(b, h) for m, p, b, h in self.api.calls if p == "/v1/video/generations" and m == "POST"]

    def kept(self, body=THREE):
        return rules.shape(body)[0]

    def shots(self, n):
        extra = {"prompt": "Top-down shot: Jensen Huang rides the folded building like a sled down the lawn"}
        return {**THREE, "shots": (THREE["shots"] + [extra])[:n]}

    def test_the_defaults_are_wan_3_then_seedance_then_wan_2_7_at_20_s_and_720p(self):
        self.assertEqual(skits.ONE_TAKE_MODELS.split(","), [WAN3, SEEDANCE, WAN27])
        self.assertEqual(skits.ONE_TAKE_SECONDS, 20)
        self.assertEqual(skits.MAX_PER_SECOND, {WAN3: WAN3_RATE, SEEDANCE: SEEDANCE_RATE, WAN27: WAN27_RATE})

    def test_a_skit_is_one_job_with_one_script_at_20_s_720p_with_sound(self):
        out = self.skit(THREE)
        [(body, headers)] = self.posts()
        skit_id = out["asset"]["id"]
        # Three shots may cost $4.80; 20 s on wan-3-0 holds $4.68: the shots
        # share the 20 s in proportion (the odd seconds to the later shots).
        self.assertEqual(body, {"model": WAN3, "prompt": rules.script(self.kept(), [6, 7, 7]), "duration_seconds": 20,
                                "resolution": "720p", "aspect_ratio": "16:9", "audio": True})
        self.assertTrue(body["prompt"].startswith("A 20-second cinematic comedy short in 3 shots with hard cuts"))
        for bit in ("Jensen Huang: silver hair, black glasses", "Location: an enormous striped lawn",
                    "Score: one continuous orchestral score", "Shot 1 (0-6s): Wide:", "Shot 2 (6-13s):",
                    "Shot 3 (13-20s): Crane shot"):
            self.assertIn(bit, body["prompt"])
        self.assertEqual(headers["idempotency-key"], f"teeny-skit-{skit_id}-take-{WAN3}-20s")
        [cut] = self.reel.jobs
        self.assertEqual(cut["clips"], [f"media/skit-{skit_id}-take.mp4"])
        self.assertEqual(cut["label"], "AI parody · @teenydroid")
        self.assertEqual(out["models"], [WAN3])
        detail = out["asset"]["detail"]
        self.assertEqual((detail["one_take"], detail["shot_lengths"], detail["models"]), (True, [6, 7, 7], [WAN3]))

    def test_each_model_gets_only_the_fields_verified_for_it(self):
        self.env(MEDIA_SKIT_ONE_TAKE_MODELS=SEEDANCE)
        self.skit({**THREE, "aspect": "9:16"})
        [(body, _)] = self.posts()
        self.assertEqual(set(body), {"model", "prompt", "duration_seconds", "resolution"})
        self.assertEqual(body["resolution"], "720p")
        self.setUp()
        self.env(MEDIA_SKIT_ONE_TAKE_MODELS=WAN27)
        self.skit(THREE)
        [(body, _)] = self.posts()
        self.assertEqual(set(body), {"model", "prompt", "duration_seconds"})
        self.setUp()
        self.env(MEDIA_SKIT_RESOLUTION="1080p")
        self.skit({**THREE, "aspect": "9:16"})
        [(body, _)] = self.posts()
        self.assertEqual((body["resolution"], body["aspect_ratio"], body["audio"]), ("1080p", "9:16", True))

    def test_the_operators_params_still_win(self):
        self.env(MEDIA_SKIT_PARAMS=json.dumps({WAN3: {"audio": False}}))
        self.skit(THREE)
        [(body, _)] = self.posts()
        self.assertIs(body["audio"], False)

    def test_the_cap_is_the_per_shot_cap_times_the_shots_it_replaces(self):
        self.assertEqual(skits.take_cap(1600000, 2), 3200000)
        self.assertEqual(skits.take_cap(1600000, 3), 4800000)
        self.assertEqual(skits.take_cap(1600000, 4), 6400000)

    def test_the_fitted_length_for_two_three_and_four_shots_on_each_model(self):
        # (shots, model) -> seconds, for the $1.60 a shot cap and a 20 s limit.
        want = {(2, WAN3): 10, (3, WAN3): 20, (4, WAN3): 20,        # 15 s is $3.51, over $3.20
                (2, SEEDANCE): 9, (3, SEEDANCE): 14, (4, SEEDANCE): 15,
                (2, WAN27): 10, (3, WAN27): 15, (4, WAN27): 15}       # it sells 5, 10, 15 only
        sold = {row["id"]: row["durations_seconds"] for row in ONE_TAKE_CATALOG["data"]}
        for (n, model), seconds in want.items():
            with self.subTest(shots=n, model=model):
                got = skits.fit(20, skits.take_cap(1600000, n), sold[model], skits.MAX_PER_SECOND[model])
                self.assertEqual(got, seconds)
                self.assertLessEqual(got * skits.MAX_PER_SECOND[model], skits.take_cap(1600000, n))

    def test_two_shots_on_wan_3_are_cut_to_10_s_to_fit_the_cap(self):
        two = self.shots(2)
        self.skit(two)
        [(body, _)] = self.posts()
        self.assertEqual(body["duration_seconds"], 10)
        self.assertEqual(body["prompt"], rules.script(self.kept(two), [5, 5]))

    def test_four_shots_on_wan_3_are_20_s_never_past_the_limit(self):
        self.env(MEDIA_MAX_USD_PER_DAY="7.00")  # four shots reserve $6.40
        four = self.shots(4)
        self.skit(four)
        [(body, _)] = self.posts()
        self.assertEqual(body["duration_seconds"], 20)
        self.assertEqual(body["prompt"], rules.script(self.kept(four), [5, 5, 5, 5]))

    def test_the_limit_is_a_setting_snapped_down_to_what_the_model_sells(self):
        self.env(MEDIA_SKIT_ONE_TAKE_SECS="17")
        self.skit(THREE)
        [(body, _)] = self.posts()
        self.assertEqual(body["duration_seconds"], 15)  # wan-3-0 sells 15 or 20: never over 17
        sold = list(range(5, 16))
        self.assertEqual(skits.fit(15, 6400000, sold, SEEDANCE_RATE), 15)
        self.assertEqual(skits.fit(10, 4800000, sold, SEEDANCE_RATE), 10)
        self.assertEqual(skits.fit(15, 1600000, sold, SEEDANCE_RATE), None)  # 4 s fits, but 5 s is the least sold
        self.assertEqual(skits.fit(12, 4800000, [4, 6, 8], None), 8)
        self.assertEqual(skits.split(14, [5, 5, 5]), [4, 5, 5])
        self.assertEqual(skits.split(9, [6, 3]), [6, 3])
        self.assertEqual(skits.split(15, [6, 6, 6, 6]), [3, 4, 4, 4])
        self.assertEqual(skits.split(20, [5, 5, 5]), [6, 7, 7])

    def test_a_job_over_the_cap_is_cancelled_and_asked_again_shorter_at_its_own_rate(self):
        # A model with no verified rate: its own max_cost_usdc says how long fits.
        self.env(MEDIA_SKIT_ONE_TAKE_MODELS="kling-v3-pro-text-to-video")
        self.api.rates["kling-v3-pro-text-to-video"] = 400000
        self.skit(THREE)
        self.assertEqual([b["duration_seconds"] for b, h in self.posts()], [15, 12])
        self.assertEqual(len(self.api.deleted()), 1)
        [take] = self.rows("shot")
        self.assertEqual((take["status"], take["model"]), ("done", "kling-v3-pro-text-to-video"))

    def test_the_take_reserves_the_whole_cap_then_its_most_then_its_bill(self):
        self.api.late_usage.add(WAN3)
        out = self.skit(THREE)
        # One row for the one job, carrying what the three shots would have.
        self.assertIn(4680000, self.reserved_seen)  # 20 s x $0.234 while it ran
        [take] = self.rows("shot")
        [skit_row] = self.rows("skit")
        self.assertEqual((take["status"], take["cost_micro"], take["estimated"], take["source_id"]),
                         ("done", 450000, 0, out["asset"]["id"]))  # the bill, read once more after success
        self.assertEqual((skit_row["status"], skit_row["cost_micro"]), ("done", 0))
        detail = json.loads(take["detail"])
        self.assertEqual((detail["one_take"], detail["seconds"], detail["lengths"]), (True, 20, [6, 7, 7]))
        self.assertEqual(take["path"], f"state/media/skit-{out['asset']['id']}-take.mp4")
        self.assertEqual(self.spent(), 450000)

    def test_the_take_is_held_against_the_daily_budget_like_its_shots(self):
        self.env(MEDIA_MAX_USD_PER_DAY="4.00")
        with self.assertRaises(hand.Refused) as caught:
            self.skit(THREE)
        self.assertEqual((caught.exception.detail["rule"], caught.exception.detail["need_usd"]), ("budget", 4.8))
        self.assertEqual(self.api.calls, [])

    def test_a_refused_take_moves_to_the_next_one_take_model(self):
        self.api.fail[WAN3] = SurplusError("Surplus answered 400: the prompt violates the content policy", 400)
        out = self.skit(THREE)
        self.assertEqual(self.api.submitted(), [WAN3, SEEDANCE])
        self.assertEqual([b["duration_seconds"] for b, h in self.posts()], [20, 14])
        self.assertEqual(out["models"], [SEEDANCE])

    def test_a_take_no_one_take_model_will_make_falls_back_to_one_job_a_shot(self):
        for model in (WAN3, SEEDANCE, WAN27):
            self.api.fail[model] = SurplusError("Surplus answered 400: the prompt violates the content policy "
                                                "(public figure likeness)", 400)
        out = self.skit(THREE)
        self.assertEqual(self.api.submitted(), [WAN3, SEEDANCE, WAN27, FIRST, FIRST, FIRST])
        # Each fallback shot carries the same look, setting and score.
        kept = self.kept()
        prompts = [b["prompt"] for b, h in self.posts()[3:]]
        self.assertEqual(prompts, [rules.shot_prompt(kept, n) for n in range(3)])
        self.assertTrue(all(p.startswith("Jensen Huang: silver hair") for p in prompts))
        rows = self.rows("shot")
        self.assertEqual([(r["status"], r["cost_micro"]) for r in rows],
                         [("failed", 0)] + [("done", 450000)] * 3)
        self.assertEqual(out["models"], [FIRST] * 3)
        self.assertFalse(out["asset"]["detail"]["one_take"])
        self.assertEqual(len(self.reel.jobs[0]["clips"]), 3)
        self.assertEqual(self.spent(), 1350000)

    def test_a_take_that_fails_while_running_falls_back_too(self):
        self.env(MEDIA_SKIT_ONE_TAKE_MODELS=WAN3)
        self.api.jobs[WAN3] = ["running", "failed"]
        out = self.skit(THREE)
        self.assertEqual(self.api.submitted(), [WAN3, FIRST, FIRST, FIRST])
        self.assertEqual(out["models"], [FIRST] * 3)

    def test_a_take_no_length_of_can_fit_the_cap_falls_back(self):
        # $0.80 a shot: $1.60 for two. On seedance 4 s would fit, but it sells
        # 5 s at least ($1.704).
        self.env(MEDIA_SKIT_MAX_USD_PER_SHOT="0.80", MEDIA_SKIT_ONE_TAKE_MODELS=SEEDANCE)
        self.skit(self.shots(2))
        self.assertEqual(self.api.submitted(), [FIRST, FIRST])

    def test_no_credit_on_the_take_stops_the_skit(self):
        self.api.fail[WAN3] = SurplusError("Surplus answered 402: insufficient credit", 402)
        with self.assertRaises(hand.HandError):
            self.skit(THREE)
        self.assertEqual(self.api.submitted(), [WAN3])
        self.assertTrue(all(r["status"] == "failed" and r["cost_micro"] == 0 for r in self.rows("shot")))

    def test_one_take_can_be_turned_off_and_a_one_shot_skit_is_one_shot(self):
        self.env(MEDIA_SKIT_ONE_TAKE="0")
        self.skit(THREE)
        self.assertEqual(self.api.submitted(), [FIRST, FIRST, FIRST])
        self.setUp()
        self.skit({**THREE, "shots": THREE["shots"][:1]})
        self.assertEqual(self.api.submitted(), [FIRST])

    def test_a_dry_run_shows_the_one_take_request(self):
        self.env(DRY_RUN=1)
        out = self.skit(THREE)
        request = out["one_take"]["request"]
        self.assertEqual((request["model"], request["duration_seconds"], request["resolution"], request["audio"]),
                         (WAN3, 20, "720p", True))
        self.assertEqual(out["one_take"]["max_usd"], 4.8)
        self.assertEqual(self.api.calls, [])


if __name__ == "__main__":
    unittest.main()
