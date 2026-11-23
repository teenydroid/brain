"""media/films.py (`hands/film`): a story film made audio first, one paid
step at a time, from a manifest it can pick up from. Surplus, the reel
service and Wikipedia are faked; nothing is bought."""

import base64
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent / "lib"))
sys.path.insert(0, str(HERE))

from teeny_hands import hand  # noqa: E402
from teeny_hands.media import assets, films  # noqa: E402
from teeny_hands.media.surplus import SurplusError  # noqa: E402

from test_skits import JPEG, SHOT, FakeReel, mp4  # noqa: E402

T0 = 1_789_369_200.0
REPO = HERE.parents[2]
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
MP3 = b"ID3\x04\x00\x00\x00\x00\x00\x00" + b"\xff\xfb" * 64
STORY = {"id": "trend:ka8y52dv", "title": "Gary Marcus says the jig is almost up over Musk's reported AI "
         "cash-crunch warning", "summary": "Musk warned AI could need hundreds of billions more.", "untrusted": True}
ACTION = ("Wide anamorphic shot of a cathedral-sized empty gold vault: Elon Musk shakes a bus-sized gold piggy bank "
          "over a tin cup and it coughs out one rusty bolt with a long brass fart.")
FILM = {
    "figures": ["Elon Musk"], "logline": "elon musk begs a gold vault for hundreds of billions and gets a bolt",
    "look": {"Elon Musk": "black leather jacket, a tin cup"}, "setting": "a cathedral-sized empty gold vault",
    "score": "a pompous tuba march", "aspect": "9:16",
    "text": "musk says ai needs hundreds of billions more. my parody: he asks the vault",
    "beats": [
        {"subject": "figure", "action": ACTION, "narration": "this man needs hundreds of billions",
         "sfx": ["a long brass fart"]},
        {"subject": "figure", "action": ACTION.replace("shakes", "kicks"), "narration": "the vault gives a bolt"},
        {"subject": "teeny", "action": "Macro shot: Teeny, the small yellow robot, lowers its newspaper in the "
                                       "same vault and stares into the lens, deadpan.",
         "narration": "i run on ten dollars a day"},
    ],
}


def alignment(text):
    chars = list(text)
    return {"characters": chars, "character_start_times_seconds": [i * 0.08 for i in range(len(chars))],
            "character_end_times_seconds": [(i + 1) * 0.08 for i in range(len(chars))]}


class FakeSurplus:
    def __init__(self):
        self.calls = []
        self.video_fail = {}
        self.jobs = {}
        self.n = 0
        self._polls = {}
        self.catalog = {"data": [{"id": m, "durations_seconds": [5, 10, 15]} for m in
                                 films.TEENY_MODELS.split(",") + films.FIGURE_MODELS.split(",")]}

    def send(self, path, body=None, limit=0, timeout=120.0, fields=None, file=None):
        self.calls.append(("SEND", path, body))
        if "/text-to-speech/" in path:
            return json.dumps({"audio_base64": base64.b64encode(MP3).decode(),
                               "alignment": alignment(body["text"])}).encode(), 120
        return MP3, 2000

    def call(self, method, path, body=None, timeout=60.0, headers=None):
        self.calls.append((method, path, body))
        if path == "/v1/models":
            return self.catalog
        if path.startswith("/api/markets/"):
            return {"offers": [{"available": True, "media_unit_price": 40000, "media_unit": "image"}]}
        if path == "/v1/images/edits":
            return {"data": [{"b64_json": base64.b64encode(PNG).decode()}]}
        if path == "/v1/video/generations" and method == "POST":
            model = body["model"]
            if model in self.video_fail:
                raise self.video_fail[model]
            self.n += 1
            job = f"job-{self.n}-{model}"
            self._polls[job] = list(self.jobs.get(model, ["running", "succeeded"]))
            return {"id": job, "status": "queued", "max_cost_usdc": "3000000"}
        if path.startswith("/v1/video/generations/") and method == "GET":
            job = path.rsplit("/", 1)[1]
            polls = self._polls[job]
            word = polls.pop(0) if len(polls) > 1 else polls[0]
            out = {"id": job, "status": word}
            if word == "succeeded":
                out["results"] = [{"url": f"https://api.surplusintelligence.ai/v1/media/artifacts/{job}/0"}]
                out["usage"] = {"buyer_cost_usdc": "1500000"}
            return out
        if method == "DELETE":
            return {}
        raise AssertionError(f"unexpected call {method} {path}")

    def download(self, url, limit, timeout=120.0):
        return SHOT

    def videos(self):
        return [b for m, p, b in self.calls if p == "/v1/video/generations" and m == "POST"]


class FilmReel(FakeReel):
    def film(self, job, timeout=600.0):
        self.jobs.append(job)
        return {"cut_id": "0e19deb3-7e54-4a71-b103-2e394ae488be", "seconds": 9.0, "beats": [3, 3, 3],
                "frames": ["frame-0.jpg"], "label": job["label"], "labelled": True, "stills": [],
                "loudness": {"input_i": "-20"}}


class FilmTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "secrets").mkdir()
        (self.root / "brain" / "assets").mkdir(parents=True)
        shutil.copy(REPO / "brain" / "assets" / "teeny-reference.png", self.root / "brain" / "assets")
        (self.root / "secrets" / "teeny.env").write_text("DRY_RUN=0\nSURPLUS_API_KEY=inf_test\n")
        self._env = dict(os.environ)
        os.environ["TEENY_ROOT"] = str(self.root)
        os.environ["TEENY_NOW"] = str(T0)
        os.environ.pop("DRY_RUN", None)
        hand._ENV_CACHE = None
        self.api, self.reel = FakeSurplus(), FilmReel()
        photo = self.root / "musk.jpg"
        photo.write_bytes(JPEG)
        patcher = mock.patch.object(assets, "_portraits",
                                    side_effect=lambda names: [{"name": n, "title": n, "path": photo} for n in names])
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)
        hand._ENV_CACHE = None
        self.tmp.cleanup()

    def env(self, **values):
        path = self.root / "secrets" / "teeny.env"
        path.write_text(path.read_text() + "".join(f"{k}={v}\n" for k, v in values.items()))
        hand._ENV_CACHE = None

    def make(self, film=FILM):
        return films.make({"film": film, "story": STORY}, api=self.api, reel=self.reel, sleep=lambda _: None,
                          clock=iter(range(0, 10**6, 5)).__next__)

    def rows(self):
        conn = assets._db()
        try:
            return [dict(r) for r in conn.execute("SELECT * FROM media_assets ORDER BY id")]
        finally:
            conn.close()

    def test_a_film_is_made_audio_first_and_cut_on_the_narrations_clock(self):
        out = self.make()
        self.assertEqual(out["status"], "done")
        kinds = [c[1] for c in self.api.calls if c[0] == "SEND"]
        # The narration is bought before any picture or clip.
        first_picture = next(i for i, c in enumerate(self.api.calls) if c[1] == "/v1/images/edits")
        self.assertIn("/text-to-speech/", self.api.calls[0][1])
        self.assertLess(0, first_picture)
        self.assertTrue(any(k.endswith("/music") for k in kinds))
        self.assertTrue(any(k.endswith("/sound-generation") for k in kinds))
        [job] = self.reel.jobs
        manifest = films.load(out["id"])
        self.assertEqual([b["t0"] for b in job["beats"]], [b["t0"] for b in manifest["clock"]["beats"]])
        self.assertEqual(job["pieces"], [{k: p[k] for k in ("from", "to", "at")} for p in manifest["clock"]["pieces"]])
        self.assertTrue(all(b["clip"].startswith(f"media/film/f{out['id']}/") for b in job["beats"]))
        self.assertTrue((self.root / out["cut"]["path"]).is_file())

    def test_each_subject_goes_to_its_own_models_from_its_own_still(self):
        self.make()
        videos = self.api.videos()
        self.assertEqual([v["model"] for v in videos],
                         ["venice-wan-3-0-image-to-video", "venice-wan-3-0-image-to-video",
                          "venice-seedance-2-5-image-to-video-basic"])
        self.assertTrue(all(v["image_url"].startswith("data:image/png;base64,") for v in videos))
        self.assertTrue(all("No music" in v["prompt"] for v in videos))

    def test_every_clip_asks_for_the_film_resolution_with_no_params_set(self):
        """FILM_RESOLUTION reached no job while FILM_PARAMS was empty, so the
        models made 1080p, quoted over the clip cap and were cancelled (f65)."""
        self.make()
        self.assertTrue(all(v.get("resolution") == "720p" for v in self.api.videos()), self.api.videos())

    def test_a_figure_is_drawn_from_its_photo_without_teeny_and_teeny_from_its_sheet(self):
        self.make()
        edits = [b for m, p, b in self.api.calls if p == "/v1/images/edits"]
        figure, teeny = edits[0], edits[2]
        self.assertIn("photo 1 is Elon Musk", figure["prompt"])
        self.assertIn("never photo-real", figure["prompt"])
        self.assertNotIn("image", figure)
        self.assertEqual(len(figure["images"]), 1)
        self.assertIn("image", teeny)
        self.assertNotIn("leather jacket", teeny["prompt"])

    def test_each_clip_is_bought_at_the_shortest_length_that_covers_its_beat(self):
        out = self.make()
        manifest = films.load(out["id"])
        for video, window in zip(self.api.videos(), manifest["clock"]["beats"]):
            self.assertGreaterEqual(video["duration_seconds"], window["t1"] - window["t0"])
            self.assertEqual(video["duration_seconds"], 5)

    def test_a_beat_whose_clip_cannot_be_had_plays_its_still_and_the_film_ships(self):
        for model in films.TEENY_MODELS.split(","):
            self.api.video_fail[model] = SurplusError("Surplus answered 400: content policy refused", 400)
        out = self.make()
        self.assertEqual(out["status"], "done")
        self.assertEqual(out["stills"], ["b3"])
        beats = self.reel.jobs[0]["beats"]
        self.assertIn("plate", beats[2])
        self.assertIn("clip", beats[0])

    def test_a_refused_clip_gives_its_hold_back_so_the_next_beat_is_still_bought(self):
        """f65: both figure clips were refused but kept their $5 holds, so the
        Teeny clip after them was refused for budget and the film stopped."""
        self.env(FILM_MAX_USD="10.50", FILM_MAX_USD_PER_CLIP="5.00")
        for model in films.FIGURE_MODELS.split(","):
            self.api.video_fail[model] = SurplusError("Surplus answered 400: content policy refused", 400)
        out = self.make()
        self.assertEqual(out["status"], "done", out)
        self.assertEqual(out["stills"], ["b1", "b2"])
        self.assertEqual([v["model"] for v in self.api.videos()][-1], "venice-seedance-2-5-image-to-video-basic")
        conn = assets._db()
        refused = conn.execute("SELECT status, cost_micro, error FROM media_assets WHERE kind = 'shot' AND "
                               "status != 'done'").fetchall()
        self.assertTrue(refused)
        self.assertTrue(all(r["status"] == "failed" and r["cost_micro"] == 0 and "refused" in r["error"]
                            for r in refused))

    def test_no_sound_effect_plays_over_a_word_or_another_effect(self):
        """f99: each effect was set halfway through its beat, on top of the
        narration, and a beat's two effects 1 s apart overlapped each other."""
        out = self.make()
        manifest = films.load(out["id"])
        words = films.captions(manifest)
        played = sorted((e["at"], e["at"] + films.narration.SFX_SECONDS) for e in manifest["effects"] if e.get("file"))
        self.assertTrue(played)
        for start, end in played:
            for word in words:
                self.assertFalse(start < word["end"] and word["start"] < end, (start, end, word))
        for (a0, a1), (b0, b1) in zip(played, played[1:]):
            self.assertLessEqual(a1, b0 + 1e-6)
        self.assertLessEqual(played[-1][1], manifest["clock"]["length"] + 1e-6)

    def test_captions_are_the_words_moved_onto_the_films_timeline(self):
        out = self.make()
        manifest = films.load(out["id"])
        words = films.captions(manifest)
        self.assertEqual([w["text"] for w in words][:3], ["this", "man", "needs"])
        second = manifest["clock"]["pieces"][1]
        moved = next(w for w in words if w["text"] == "vault")
        self.assertGreaterEqual(moved["start"], second["at"])

    def test_a_film_is_paid_from_its_own_week_never_the_skits_day(self):
        self.make()
        conn = assets._db()
        try:
            self.assertEqual(assets._spent_today(conn), 0)
            self.assertGreater(films._week_spent(conn), 0)
        finally:
            conn.close()
        self.assertTrue(all(r["source_id"] for r in self.rows() if r["kind"] != "film"))

    def test_a_week_over_budget_refuses_a_film_before_anything_is_bought(self):
        # The first film spends about $4.60 of the week; a second's $15
        # reservation would take it past $18.
        self.env(FILM_MAX_USD="15", FILM_MAX_USD_PER_WEEK="18")
        self.make()
        bought = len(self.api.calls)
        with self.assertRaises(hand.Refused) as caught:
            self.make()
        self.assertEqual(caught.exception.detail.get("budget_usd"), 18.0)
        self.assertEqual(len(self.api.calls), bought)

    def test_a_film_that_stopped_is_carried_on_and_nothing_is_bought_twice(self):
        with mock.patch.object(films, "cut", side_effect=RuntimeError("deploy")):
            with self.assertRaises(RuntimeError):
                self.make()
        film_id = [r for r in self.rows() if r["kind"] == "film"][0]["id"]
        bought = len(self.api.calls)
        out = films.run(film_id, api=self.api, reel=self.reel, sleep=lambda _: None)
        self.assertEqual(out["status"], "done")
        paid = [c for c in self.api.calls[bought:] if c[0] == "SEND" or c[1] in ("/v1/images/edits",
                                                                                "/v1/video/generations")]
        self.assertEqual(paid, [])

    def test_a_beat_too_long_for_any_clip_fails_the_film_before_a_picture_is_bought(self):
        long = {**FILM, "beats": [dict(FILM["beats"][0], narration=" ".join(["blah"] * 60)), *FILM["beats"][1:]]}
        with self.assertRaises(hand.Refused):
            self.make(long)
        self.assertFalse(any(c[1] == "/v1/images/edits" for c in self.api.calls))
        film = [r for r in self.rows() if r["kind"] == "film"][0]
        self.assertEqual((film["status"], film["cost_micro"]), ("failed", 0))

    def test_the_screen_runs_again_and_a_dry_run_buys_nothing(self):
        bad = {**FILM, "beats": [dict(FILM["beats"][0], narration="buy $TINY now"), *FILM["beats"][1:]]}
        with self.assertRaises(hand.Refused):
            self.make(bad)
        self.env(DRY_RUN=1)
        out = self.make()
        self.assertTrue(out["dry_run"])
        self.assertEqual(self.api.calls, [])

    def test_a_film_is_due_every_other_day_and_only_with_room_in_the_week(self):
        self.assertTrue(films.due()["due"])
        self.make()
        self.assertFalse(films.due()["due"])
        os.environ["TEENY_NOW"] = str(T0 + 2 * 86400)
        self.assertTrue(films.due()["due"])
        self.env(FILM_MAX_USD_PER_WEEK="18")
        self.assertFalse(films.due()["due"])
