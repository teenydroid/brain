"""hands/video khaby keys, render and drop: a Khaby Lame video with its
setup kept exactly as it is and his reaction re-made with Teeny standing
where he stood. keys edits the first frame of each reaction segment (the
cheap money); render animates those keyframes, trims each to its segment
and cuts them between the source's own kept segments, with the source's
audio and the label (the expensive money). Everything about the video (its
segments, lengths, frames, aspect, files) comes from the dissect's
timeline, never from the body. Off unless the operator turns it on; one of
the videos a day skits and remakes share; a kept segment costs nothing.
Surplus and the reel service are fakes: nothing here spends or renders."""

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
from teeny_hands.media import assets, khaby, remakes, skits  # noqa: E402

from test_remakes import EDIT1, KEYS, M4A, MODELS, PNG, FakeEdits  # noqa: E402
from test_skits import JPEG, SHOT, SKIT, FakeReel, T0  # noqa: E402

DISSECT = "tt-7"
SOURCE = f"media/khaby/{DISSECT}/source.mp4"
AUDIO = f"media/khaby/{DISSECT}/audio.m4a"
EDIT = ("Replace the man at the right of the kitchen with Teeny, standing where he stood, "
        "same framing, same light")
MOTION = "Teeny looks straight at the camera, peels the banana the plain way and opens both hands"


def timeline(roles, dissect=DISSECT, usable=True):
    """A timeline as hands/khaby dissect keeps it: `roles` is (role,
    seconds) a segment, or (role, [seconds, ...]) for one spanning shots."""
    shots, segments, at, n_shot = [], [], 0.0, 0
    for n, (role, lengths) in enumerate(roles, 1):
        lengths = lengths if isinstance(lengths, list) else [lengths]
        start, numbers = at, []
        for seconds in lengths:
            n_shot += 1
            numbers.append(n_shot)
            shots.append({"n": n_shot, "start": round(at, 3), "seconds": seconds,
                          "frame": f"state/media/khaby/{dissect}/shot-{n_shot}.jpg" if usable else None})
            at += seconds
        segments.append({"n": n, "role": role, "start": round(start, 3), "end": round(at, 3),
                         "first_frame": round(start * 30), "last_frame": round(at * 30) - 1,
                         "editable": role == "reaction", "shots": numbers, "why": "", "what": f"segment {n}"})
    return {"dissect_id": dissect, "source": {"video_id": "7", "url": "https://www.tiktok.com/@khaby.lame/video/7",
                                              "title": "the banana", "source": "tiktok", "fps": 30},
            "excerpt": {"start": 0, "seconds": round(at, 3), "frames": round(at * 30)}, "aspect": "9:16",
            "video": f"state/media/khaby/{dissect}/source.mp4" if usable else None,
            "audio": f"state/media/khaby/{dissect}/audio.m4a" if usable else None, "shots": shots,
            "segments": segments, "beats": [{"at": 6.5, "frame": 195, "what": "he looks at the camera"}],
            "joke": "the hard way", "flags": {}, "usable": usable, "why": "" if usable else "flagged: minors",
            "provider": "surplus", "model": "m", "cost_usd": 0.01}


SETUP_REACTION = [("setup", [3.0, 3.0]), ("reaction", 4.2)]


def body(*numbers, **extra):
    return {"dissect": DISSECT, "story": "a life hack that takes nine steps", "logline": "Teeny does it the plain way",
            "segments": [{"n": n, "edit": EDIT, "motion": MOTION} for n in (numbers or (2,))], **extra}


class KhabyTest(unittest.TestCase):
    def setUp(self):
        import tempfile

        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "secrets").mkdir()
        study = self.root / "state" / "media" / "remake" / "s1"
        study.mkdir(parents=True)
        for n in (1, 2):
            (study / f"shot-{n}.jpg").write_bytes(JPEG)
        (study / "audio.m4a").write_bytes(M4A)
        (self.root / "brain" / "assets").mkdir(parents=True)
        (self.root / "brain" / "assets" / "teeny-reference.png").write_bytes(PNG)
        self.write("DRY_RUN=0\nSURPLUS_API_KEY=inf_test\nX_BOT_USERNAME=teenydroid\nMEDIA_SKIT_MAX_PER_DAY=1\n"
                   "MEDIA_KHABY_MAX_PER_DAY=1\nMEDIA_REMAKE_MAX_PER_DAY=1\nMEDIA_MAX_USD_PER_DAY=5.00\n"
                   f"MEDIA_KHABY_MODELS={MODELS}\nMEDIA_REMAKE_MODELS={MODELS}\n")
        self._env = dict(os.environ)
        os.environ["TEENY_ROOT"] = str(self.root)
        os.environ["TEENY_NOW"] = str(T0)
        os.environ.pop("DRY_RUN", None)
        hand._ENV_CACHE = None
        self.api = FakeEdits()
        self.reel = FakeReel()
        self.clock = [0.0]
        self.dissect(SETUP_REACTION)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)
        hand._ENV_CACHE = None
        self.tmp.cleanup()

    def write(self, text):
        (self.root / "secrets" / "teeny.env").write_text(text)
        hand._ENV_CACHE = None

    def env(self, **values):
        self.write((self.root / "secrets" / "teeny.env").read_text() + "".join(f"{k}={v}\n" for k, v in values.items()))

    def dissect(self, roles, **kw):
        kept = timeline(roles, **kw)
        folder = self.root / "state" / "media" / "khaby" / kept["dissect_id"]
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "timeline.json").write_text(json.dumps(kept))
        (folder / "source.mp4").write_bytes(SHOT)
        (folder / "audio.m4a").write_bytes(M4A)
        for shot in kept["shots"]:
            (folder / f"shot-{shot['n']}.jpg").write_bytes(JPEG)
        return folder

    def sleep(self, seconds):
        self.clock[0] += seconds

    def keys(self, given=None):
        return khaby.generate_keys(given or body(), api=self.api, reel=self.reel, clock=lambda: self.clock[0])

    def render(self, given):
        return khaby.generate_khaby(given, api=self.api, reel=self.reel, sleep=self.sleep, clock=lambda: self.clock[0])

    def both(self, given=None):
        made = self.keys(given)
        return made, self.render({"khaby_id": made["khaby_id"]})

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

    def refused(self, call, rule):
        with self.assertRaises(hand.Refused) as caught:
            call()
        self.assertEqual(caught.exception.detail["rule"], rule, caught.exception.reason)
        return caught.exception

    # --- the happy path ----------------------------------------------------------------------

    def test_one_setup_and_one_reaction_make_one_key_one_clip_and_a_cut_of_both(self):
        made, video = self.both()
        kid = made["khaby_id"]
        [edit] = self.api.edits()
        frame, sheet = edit["images"]
        self.assertTrue(frame.startswith("data:image/jpeg;base64,"))
        self.assertTrue(sheet.startswith("data:image/png;base64,"))
        self.assertIn(EDIT, edit["prompt"])
        self.assertIn("where the man stood", edit["prompt"])
        self.assertEqual([(k["n"], k["path"], k["model"]) for k in made["keys"]],
                         [(2, f"state/media/khaby-{kid}-key-2.png", EDIT1)])
        [job] = self.api.videos()
        self.assertTrue(job["prompt"].startswith(MOTION))
        self.assertTrue(job["image_url"].startswith("data:image/png;base64,"))
        self.assertEqual(job["duration_seconds"], 5)
        self.assertEqual(self.reel.jobs, [{"clips": [SOURCE, f"media/khaby-{kid}-shot-2.mp4"],
                                           "label": "AI parody · @teenydroid", "aspect": "9:16",
                                           "trims": [6.0, 4.2], "starts": [0.0, 0.0], "sound": AUDIO}])
        self.assertEqual((video["asset"], video["asset_id"], video["labelled"]),
                         (f"state/media/khaby-{kid}.mp4", kid, True))
        [row] = self.rows("khaby")
        self.assertEqual((row["status"], row["path"]), ("done", f"state/media/khaby-{kid}.mp4"))

    def test_a_kept_segment_costs_nothing_and_gets_no_key_or_job(self):
        self.dissect([("setup", 3.0), ("reaction", 4.2), ("other", 1.8)])
        made = self.keys()
        self.assertEqual(len(self.api.edits()), 1)
        self.assertEqual([(s["status"], s["cost_micro"]) for s in self.rows("shot")], [("pending", 1100000)])
        self.assertEqual(self.spent(), 1200000)
        self.assertEqual(made["reserve_usd"], 1.1)
        self.render({"khaby_id": made["khaby_id"]})
        self.assertEqual(len(self.api.videos()), 1)

    def test_kept_segments_next_to_each_other_are_one_clip(self):
        self.dissect([("setup", 3.0), ("other", 3.0), ("reaction", 4.2), ("other", 1.8)])
        made, _ = self.both(body(3))
        kid = made["khaby_id"]
        [cut] = self.reel.jobs
        self.assertEqual(cut["clips"], [SOURCE, f"media/khaby-{kid}-shot-3.mp4", SOURCE])
        self.assertEqual((cut["trims"], cut["starts"]), ([6.0, 4.2, 1.8], [0.0, 0.0, 10.2]))

    def test_two_reactions_are_two_keys_and_two_clips_in_order(self):
        self.dissect([("setup", 3.0), ("reaction", 2.5), ("setup", 2.0), ("reaction", 4.0)])
        made, _ = self.both(body(2, 4))
        kid = made["khaby_id"]
        self.assertEqual([k["n"] for k in made["keys"]], [2, 4])
        [cut] = self.reel.jobs
        self.assertEqual(cut["clips"], [SOURCE, f"media/khaby-{kid}-shot-2.mp4", SOURCE, f"media/khaby-{kid}-shot-4.mp4"])
        self.assertEqual((cut["trims"], cut["starts"]), ([3.0, 2.5, 2.0, 4.0], [0.0, 0.0, 5.5, 0.0]))

    def test_a_reaction_over_several_shots_is_one_clip_from_its_first_frame(self):
        folder = self.dissect([("setup", 3.0), ("reaction", [2.0, 2.5])])
        (folder / "shot-3.jpg").unlink()  # the second shot's frame is never read
        made, _ = self.both()
        self.assertEqual(len(self.api.edits()), 1)
        [job] = self.api.videos()
        self.assertEqual(job["duration_seconds"], 5)
        self.assertEqual(self.reel.jobs[0]["trims"], [3.0, 4.5])

    # --- the timeline is the only word on the video --------------------------------------------

    def test_a_missing_unusable_or_emptied_timeline_is_refused_before_anything_is_asked(self):
        folder = self.root / "state" / "media" / "khaby" / DISSECT
        cases = [
            lambda: (folder / "timeline.json").unlink(),
            lambda: self.dissect(SETUP_REACTION, usable=False),
            lambda: (folder / "source.mp4").unlink(),
            lambda: (folder / "audio.m4a").unlink(),
            lambda: (folder / "shot-3.jpg").unlink(),
            lambda: (folder / "timeline.json").write_text("not json"),
            lambda: (folder / "timeline.json").write_text(json.dumps({**timeline(SETUP_REACTION), "dissect_id": "tt-8"})),
        ]
        for n, spoil in enumerate(cases):
            with self.subTest(case=n):
                self.dissect(SETUP_REACTION)
                spoil()
                self.refused(self.keys, "timeline")
        self.assertEqual((self.api.calls, self.reel.jobs, self.rows()), ([], [], []))

    def test_the_body_cannot_say_anything_the_timeline_says(self):
        bad = [
            body(aspect="16:9"), body(video="state/media/x.mp4"), body(audio=None), body(shots=[]),
            {**body(), "segments": [{"n": 2, "edit": EDIT, "motion": MOTION, "seconds": 9.0}]},
            {**body(), "segments": [{"n": 2, "edit": EDIT, "motion": MOTION, "frame": "state/media/x.jpg"}]},
            body(1), body(2, 2), body(), {**body(), "segments": []}, {**body(), "dissect": "../tt-7"},
            {**body(), "segments": [{"n": 2, "edit": "short", "motion": MOTION}]},
        ]
        bad[8] = {**body(), "segments": [{"edit": EDIT, "motion": MOTION}]}
        for given in bad:
            with self.subTest(given=given):
                self.refused(lambda: self.keys(given), "body")
        self.assertEqual((self.api.calls, self.rows()), ([], []))

    def test_teeny_is_the_only_reactor(self):
        self.assertIn("khaby_id", self.keys(body(reactor="teeny")))
        self.env(MEDIA_KHABY_MAX_PER_DAY=2, MEDIA_SKIT_MAX_PER_DAY=2)
        for other in ("Elon Musk", "khaby", "", None, 1):
            with self.subTest(reactor=other):
                self.refused(lambda: self.keys(body(reactor=other)), "reactor")

    def test_the_render_takes_only_the_id_and_a_wait(self):
        made = self.keys()
        for given in ({"khaby_id": made["khaby_id"], "sound": AUDIO}, {"khaby_id": made["khaby_id"], "clips": []},
                      {"khaby_id": "1"}, {"remake_id": made["khaby_id"]}, []):
            with self.subTest(given=given):
                self.assertRaises(hand.Refused, self.render, given)
        self.assertEqual(self.api.videos(), [])

    def test_a_render_is_refused_once_the_dissects_files_are_gone(self):
        made = self.keys()
        source = self.root / "state" / "media" / "khaby" / DISSECT / "source.mp4"
        source.unlink()
        self.refused(lambda: self.render({"khaby_id": made["khaby_id"]}), "not_ready")
        self.assertEqual(self.api.videos(), [])
        [row] = self.rows("khaby")
        self.assertEqual((row["status"], json.loads(row["detail"])["stage"]), ("pending", "keys"))
        source.write_bytes(SHOT)
        self.assertTrue(self.render({"khaby_id": made["khaby_id"]})["labelled"])

    # --- lengths and the cutter's limit, before any money ------------------------------------------

    def test_a_reaction_longer_than_a_clip_is_refused_before_anything_is_bought(self):
        for seconds in (10.5, 0.8):
            with self.subTest(seconds=seconds):
                self.dissect([("setup", 3.0), ("reaction", seconds)])
                error = self.refused(self.keys, "length")
                self.assertIn("segment 2", error.reason)
        self.assertEqual((self.api.calls, self.rows()), ([], []))

    def test_a_reaction_no_model_sells_long_enough_is_refused_before_anything_is_bought(self):
        self.api.catalog = {"data": [{"id": m, "durations_seconds": [2, 5]} for m in MODELS.split(",")]}
        self.dissect([("setup", 3.0), ("reaction", 6.0)])
        error = self.refused(self.keys, "length")
        self.assertIn("6", error.reason)
        self.assertEqual((self.api.edits(), self.rows()), ([], []))

    def test_a_timeline_needing_more_clips_than_the_cutter_joins_is_refused(self):
        self.dissect([("setup", 2.0), ("reaction", 2.0)] * 3 + [("setup", 2.0)])
        self.refused(lambda: self.keys(body(2, 4, 6)), "length")
        self.assertEqual((self.api.calls, self.rows()), ([], []))

    # --- off, caps, and the day shared with skits and remakes ---------------------------------------

    def test_off_by_default_and_nothing_is_asked(self):
        self.write("DRY_RUN=0\nSURPLUS_API_KEY=inf_test\nMEDIA_SKIT_MAX_PER_DAY=1\nMEDIA_MAX_USD_PER_DAY=5\n")
        for call in (self.keys, lambda: self.render({"khaby_id": 1})):
            with self.subTest(call=call):
                error = self.refused(call, "off")
                self.assertIn("MEDIA_KHABY_MAX_PER_DAY is 0", error.reason)
        self.write("DRY_RUN=0\nSURPLUS_API_KEY=inf_test\nMEDIA_KHABY_MAX_PER_DAY=1\nMEDIA_MAX_USD_PER_DAY=5\n")
        self.assertIn("MEDIA_SKIT_MAX_PER_DAY", self.refused(self.keys, "off").reason)
        self.assertEqual((self.api.calls, self.reel.jobs, self.rows()), ([], [], []))

    def test_a_dry_run_says_what_it_would_make_and_calls_nothing(self):
        self.write("DRY_RUN=1\n")
        out = self.keys()
        self.assertEqual((out["dry_run"], out["would_generate"], out["enabled"], out["reserve_usd"]),
                         (True, "khaby", False, 1.2))
        self.assertEqual(out["requests"][0]["images"][0], f"state/media/khaby/{DISSECT}/shot-3.jpg")
        later = self.render({"khaby_id": 7})
        self.assertEqual((later["dry_run"], later["would_generate"]), (True, "khaby"))
        self.assertEqual((self.api.calls, self.reel.jobs, self.rows()), ([], [], []))

    def test_its_own_cap_holds_under_a_wider_shared_one(self):
        self.env(MEDIA_SKIT_MAX_PER_DAY=3)
        self.keys()
        self.assertIn("MEDIA_KHABY_MAX_PER_DAY", self.refused(self.keys, "per_day").reason)

    def test_a_skit_today_leaves_no_khaby_video(self):
        skits.generate_skit(SKIT, api=self.api, reel=self.reel, sleep=self.sleep, clock=lambda: self.clock[0])
        self.refused(self.keys, "per_day")

    def test_a_khaby_video_today_leaves_no_skit_or_remake(self):
        self.keys()
        self.refused(lambda: skits.generate_skit(SKIT, api=self.api, reel=self.reel, sleep=self.sleep,
                                                 clock=lambda: self.clock[0]), "per_day")
        self.refused(lambda: remakes.generate_keys(KEYS, api=self.api, reel=self.reel, clock=lambda: self.clock[0]),
                     "per_day")

    def test_a_remake_today_leaves_no_khaby_video(self):
        remakes.generate_keys(KEYS, api=self.api, reel=self.reel, clock=lambda: self.clock[0])
        self.refused(self.keys, "per_day")

    def test_every_reaction_reserves_its_cap_against_the_shared_budget(self):
        self.env(MEDIA_MAX_USD_PER_DAY="1.00")
        self.assertEqual(self.refused(self.keys, "budget").detail["need_usd"], 1.2)
        self.env(MEDIA_KHABY_MAX_USD_PER_SHOT="0.90")
        self.keys()
        self.assertEqual([s["cost_micro"] for s in self.rows("shot")], [800000])

    def test_no_reel_service_no_money_spent(self):
        self.reel.up = False
        with self.assertRaises(hand.HandError):
            self.keys()
        self.assertEqual((self.api.calls, self.rows()), ([], []))

    # --- the reservation: drop, render once, and the stale sweep ---------------------------------

    def test_a_dropped_khaby_video_releases_its_reservation_and_keeps_its_key(self):
        made = self.keys()
        self.assertEqual(khaby.drop_khaby({"khaby_id": made["khaby_id"]}),
                         {"khaby_id": made["khaby_id"], "released_usd": 1.1})
        self.assertEqual([(s["status"], s["cost_micro"]) for s in self.rows("shot")], [("failed", 0)])
        self.assertEqual([k["status"] for k in self.rows("key")], ["done"])
        [row] = self.rows("khaby")
        self.assertTrue(row["status"] == "failed" and row["error"].startswith("dropped"))
        self.assertEqual(self.spent(), 100000)
        self.refused(lambda: self.render({"khaby_id": made["khaby_id"]}), "failed")
        self.assertEqual(khaby.drop_khaby({"khaby_id": made["khaby_id"]})["released_usd"], 0.0)

    def test_a_drop_is_refused_for_an_unknown_or_rendered_one_and_takes_only_the_id(self):
        self.refused(lambda: khaby.drop_khaby({"khaby_id": 99}), "khaby_id")
        made, _ = self.both()
        self.refused(lambda: khaby.drop_khaby({"khaby_id": made["khaby_id"]}), "used")
        self.refused(lambda: self.render({"khaby_id": made["khaby_id"]}), "used")
        for bad in ({"khaby_id": made["khaby_id"], "x": 1}, {"khaby_id": "1"}, []):
            with self.subTest(bad=bad):
                self.assertRaises(hand.Refused, khaby.drop_khaby, bad)

    def test_a_remake_id_is_not_a_khaby_id(self):
        made = remakes.generate_keys(KEYS, api=self.api, reel=self.reel, clock=lambda: self.clock[0])
        self.refused(lambda: khaby.drop_khaby({"khaby_id": made["remake_id"]}), "khaby_id")
        self.refused(lambda: self.render({"khaby_id": made["remake_id"]}), "khaby_id")

    def test_a_stale_one_is_let_go_by_the_next_budget_check(self):
        self.env(MEDIA_MAX_USD_PER_DAY="1.20")
        made = self.keys()
        with self.assertRaises(hand.Refused):
            assets.generate_image({"prompt": "Teeny waves"}, api=self.api)
        os.environ["TEENY_NOW"] = str(T0 + khaby.STALE_SECONDS + 1)
        self.assertEqual(assets.generate_image({"prompt": "Teeny waves"}, api=self.api)["asset"]["status"], "done")
        [row] = self.rows("khaby")
        self.assertTrue(row["error"].startswith("expired"))
        self.refused(lambda: self.render({"khaby_id": made["khaby_id"]}), "stale")


class VerbTest(unittest.TestCase):
    def test_video_khaby_has_keys_render_and_drop_and_no_default(self):
        from unittest import mock

        from teeny_hands.media import cli, flavors

        flavor = flavors.REGISTRY["khaby"]
        self.assertEqual([s.name for s in flavor.steps], ["keys", "render", "drop"])
        self.assertEqual((flavor.kinds, flavor.shares_video_cap, flavor.default_step), (("khaby", "key", "shot"), True, None))
        self.assertIn("khaby", skits.VIDEO_KINDS)
        self.assertNotIn("khaby", cli.ALIASES)
        for step, name in (("keys", "generate_keys"), ("render", "generate_khaby"), ("drop", "drop_khaby")):
            with self.subTest(step=step):
                args = cli.parser("video").parse_args(["khaby", step])
                seen = []
                with mock.patch.object(khaby, name, lambda b: seen.append(b) or {}):
                    args.handler(args, {"x": 1})
                self.assertEqual(seen, [{"x": 1}])
        self.assertIn("khaby", [f["name"] for f in flavors.describe()["flavors"]])
        self.assertFalse(hasattr(cli.parser("video").parse_args(["khaby"]), "handler"))


class KindsTest(unittest.TestCase):
    """A ledger made before khaby videos checks `kind` against a list
    without `khaby`: it is rebuilt once, every row kept."""

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

    def test_an_old_ledger_gains_the_khaby_kind_and_keeps_its_rows(self):
        old = assets.SCHEMA.replace(assets.KINDS_CHECK.split("kind IN ")[1],
                                    "('image', 'video', 'reel', 'song', 'music', 'meme', 'comic', 'skit', 'shot', "
                                    "'remake', 'key')")
        self.assertNotIn("'khaby'", old)
        state = Path(self.tmp.name) / "state"
        state.mkdir()
        conn = sqlite3.connect(state / "hands.db")
        conn.executescript(old)
        conn.execute("ALTER TABLE media_assets ADD COLUMN detail TEXT")
        conn.execute("ALTER TABLE media_assets ADD COLUMN alt TEXT")
        conn.execute("INSERT INTO media_assets (kind, status, prompt, cost_micro, created_at) VALUES ('remake', 'done', 'r', 0, 1)")
        conn.commit()
        conn.close()
        conn = assets._db()
        try:
            self.assertEqual([r["kind"] for r in conn.execute("SELECT kind FROM media_assets")], ["remake"])
            with hand.transaction(conn):
                conn.execute("INSERT INTO media_assets (kind, status, prompt, created_at) VALUES ('khaby', 'done', 'k', 1)")
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
