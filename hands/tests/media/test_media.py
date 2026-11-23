"""hands/image and hands/video: Teeny's pictures and clips, made from its
character sheet on Surplus, under hard caps, with the cheapest model first."""

import base64
import os
import shutil
import sys
import tempfile
import json
import unittest
import urllib.parse
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent / "lib"))

from teeny_hands import hand  # noqa: E402
from teeny_hands.media import assets  # noqa: E402
from teeny_hands.media.surplus import SurplusError  # noqa: E402

T0 = 1_789_369_200.0  # 2026-09-14T07:00:00Z
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
#: What a model answers for a figure's stylised portrait (assets.STYLISE).
STYLED = PNG + b"stylised"
MP4 = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64
REPO = HERE.parents[2]


class FakeSurplus:
    """Answers like Surplus. `fail` maps a model to the SurplusError its
    generation raises; `fail_if` takes an edit's body and answers the
    SurplusError that call raises, or None; `quotes` a video model to its
    estimate; `jobs` a video model to the statuses its polls walk through."""

    def __init__(self):
        self.calls = []
        self.fail = {}
        self.fail_if = lambda body: None
        self.quotes = {}
        self.jobs = {}
        self.usage = {}
        self.downloads = []
        self._polls = {}

    def call(self, method, path, body=None, timeout=60.0, headers=None):
        self.calls.append((method, path, body))
        if path.startswith("/api/markets/"):
            return {"offers": [{"available": True, "media_unit_price": 16000, "media_unit": "image"}]}
        if path == "/v1/images/edits":
            if body["model"] in self.fail:
                raise self.fail[body["model"]]
            refused = self.fail_if(body)
            if refused:
                raise refused
            out = STYLED if body["prompt"] == assets.STYLISE else PNG
            return {"data": [{"b64_json": base64.b64encode(out).decode()}]}
        if path == "/v1/video/generations" and method == "POST":
            model = body["model"]
            if model in self.fail:
                raise self.fail[model]
            job = f"job-{model}"
            self._polls[job] = list(self.jobs.get(model, ["running", "succeeded"]))
            return {"id": job, "status": "queued", "estimated_cost_usdc": str(self.quotes.get(model, 63000))}
        if path.startswith("/v1/video/generations/") and method == "GET":
            job = path.rsplit("/", 1)[1]
            status = self._polls[job].pop(0) if len(self._polls[job]) > 1 else self._polls[job][0]
            out = {"id": job, "status": status}
            if status == "succeeded":
                out["results"] = [{"url": f"https://api.surplusintelligence.ai/v1/media/artifacts/{job}/0"}]
                out["usage"] = {"buyer_cost_usdc": str(self.usage.get(job, 72000))}
            if status == "failed":
                out["error"] = {"type": "provider_unavailable", "retryable": True}
            return out
        if method == "DELETE":
            return {}
        raise AssertionError(f"unexpected call {method} {path}")

    def download(self, url, limit, timeout=120.0):
        self.downloads.append(url)
        return MP4

    def models(self, path):
        return [b["model"] for m, p, b in self.calls if p == path and m == "POST"]


class MediaTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "secrets").mkdir()
        (self.root / "brain" / "assets").mkdir(parents=True)
        shutil.copy(REPO / "brain" / "assets" / "teeny-reference.png", self.root / "brain" / "assets" / "teeny-reference.png")
        (self.root / "secrets" / "teeny.env").write_text("DRY_RUN=0\nSURPLUS_API_KEY=inf_test\n")
        self._env = dict(os.environ)
        os.environ["TEENY_ROOT"] = str(self.root)
        os.environ["TEENY_NOW"] = str(T0)
        os.environ.pop("DRY_RUN", None)
        hand._ENV_CACHE = None
        self.api = FakeSurplus()

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)
        hand._ENV_CACHE = None
        self.tmp.cleanup()

    def env(self, **values):
        lines = (self.root / "secrets" / "teeny.env").read_text()
        lines += "".join(f"{k}={v}\n" for k, v in values.items())
        (self.root / "secrets" / "teeny.env").write_text(lines)
        hand._ENV_CACHE = None

    def image(self, prompt="waving at the desk"):
        return assets.generate_image({"prompt": prompt}, api=self.api)

    def video(self, prompt="waves at the camera"):
        return assets.generate_video({"prompt": prompt}, api=self.api, sleep=lambda _: None)

    # --- images ---------------------------------------------------------------

    def test_an_image_is_an_edit_of_teenys_character_sheet(self):
        out = self.image()["asset"]
        method, path, body = next(c for c in self.api.calls if c[1] == "/v1/images/edits")
        self.assertTrue(body["image"].startswith("data:image/png;base64,"))
        reference = base64.b64decode(body["image"].split(",", 1)[1])
        self.assertEqual(reference, (self.root / "brain/assets/teeny-reference.png").read_bytes())
        self.assertIn("Keep Teeny exactly as in the reference", body["prompt"])
        self.assertIn("waving at the desk", body["prompt"])
        self.assertEqual(out["model"], "gpt-image-2-5-flare-edit")
        self.assertEqual((self.root / out["path"]).read_bytes(), PNG)
        self.assertTrue(out["path"].startswith("state/media/"))

    # --- public figures in a picture --------------------------------------------

    def wiki(self, pages):
        """Stand in for Wikipedia and Wikidata: PAGES is {name: (item, kinds, has_photo)}."""
        from teeny_hands.media import figures
        import urllib.error
        seen = []

        def get(url, limit, timeout=30.0):
            seen.append(url)
            if url.startswith(figures.SUMMARY):
                name = urllib.parse.unquote(url[len(figures.SUMMARY):].split("?")[0]).replace("_", " ")
                if name not in pages:
                    raise urllib.error.HTTPError(url, 404, "Not Found", None, None)
                item, _, photo = pages[name]
                page = {"type": "standard", "title": name, "wikibase_item": item}
                if photo:
                    page["originalimage"] = {"source": f"https://upload.wikimedia.org/{item}.png"}
                return json.dumps(page).encode()
            if url.startswith(figures.ENTITY):
                item = url[len(figures.ENTITY):].split(".")[0]
                kinds = next(k for i, k, _ in pages.values() if i == item)
                claims = [{"mainsnak": {"datavalue": {"value": {"id": k}}}} for k in kinds]
                return json.dumps({"entities": {item: {"claims": {"P31": claims}}}}).encode()
            if url.startswith("https://upload.wikimedia.org/"):
                return PNG
            raise AssertionError(f"unexpected url {url}")

        self._get = figures._get
        figures._get = get
        self.addCleanup(lambda: setattr(figures, "_get", self._get))
        return seen

    def scene_edits(self):
        """The edits that drew a picture, the stylised portraits left out."""
        return [b for m, p, b in self.api.calls if p == "/v1/images/edits" and b["prompt"] != assets.STYLISE]

    def test_a_public_figure_in_a_picture_is_drawn_from_their_stylised_portrait(self):
        # 2026-11-23: Venice's gateway refuses a realistic face in a mocking
        # scene and passes a stylised one: flare drew the refused Jensen
        # Huang scene from a caricature of his photo.
        self.wiki({"Sam Altman": ("Q1", ["Q5"], True)})
        out = assets.generate_image({"prompt": "the boss grins in the doorway", "figures": ["Sam Altman"]}, api=self.api)
        stylise, body = [b for m, p, b in self.api.calls if p == "/v1/images/edits"]
        self.assertEqual(stylise["prompt"], assets.STYLISE)
        self.assertNotIn("images", stylise)
        self.assertEqual(base64.b64decode(stylise["image"].split(",", 1)[1]), PNG)  # the photo
        self.assertNotIn("Sam Altman", stylise["prompt"])
        self.assertNotIn("image", body)
        self.assertEqual(len(body["images"]), 2)
        self.assertEqual(base64.b64decode(body["images"][0].split(",", 1)[1]),
                         (self.root / "brain/assets/teeny-reference.png").read_bytes())
        self.assertEqual(base64.b64decode(body["images"][1].split(",", 1)[1]), STYLED)
        self.assertIn("photo 2 is Sam Altman", body["prompt"])
        self.assertEqual(out["asset"]["model"], "gpt-image-2-5-flare-edit")

    def test_a_figure_is_stylised_once_and_the_portrait_kept(self):
        self.wiki({"Sam Altman": ("Q1", ["Q5"], True)})
        assets.generate_image({"prompt": "x", "figures": ["Sam Altman"]}, api=self.api)
        assets.generate_image({"prompt": "y", "figures": ["Sam Altman"]}, api=self.api)
        stylises = [b for m, p, b in self.api.calls if p == "/v1/images/edits" and b["prompt"] == assets.STYLISE]
        self.assertEqual(len(stylises), 1)
        self.assertEqual(base64.b64decode(self.scene_edits()[1]["images"][1].split(",", 1)[1]), STYLED)
        conn = assets._db()
        rows = conn.execute("SELECT kind, purpose, status, cost_micro, path FROM media_assets ORDER BY id").fetchall()
        self.assertEqual([(r["kind"], r["purpose"], r["status"]) for r in rows],
                         [("image", "portrait", "done"), ("image", "post", "done"), ("image", "post", "done")])
        self.assertEqual(rows[0]["cost_micro"], 16000)
        self.assertEqual((self.root / rows[0]["path"]).read_bytes(), STYLED)
        self.assertEqual(assets.listing("image", 5)["spent_today_usd"], 0.048)
        self.assertEqual(assets.scenes(5)["pictures"], 2)  # a portrait is no post's scene

    def test_a_figure_no_model_will_stylise_is_drawn_from_the_photo(self):
        self.wiki({"Sam Altman": ("Q1", ["Q5"], True)})
        self.api.fail_if = lambda body: (SurplusError("Surplus answered 503: no healthy sellers", 503)
                                         if body["prompt"] == assets.STYLISE else None)
        out = assets.generate_image({"prompt": "x", "figures": ["Sam Altman"]}, api=self.api)["asset"]
        self.assertEqual(out["status"], "done")
        self.assertEqual(base64.b64decode(self.scene_edits()[0]["images"][1].split(",", 1)[1]), PNG)
        portrait = assets._db().execute("SELECT status, cost_micro FROM media_assets WHERE purpose = 'portrait'").fetchone()
        self.assertEqual((portrait["status"], portrait["cost_micro"]), ("failed", 0))
        from teeny_hands.media import figures
        self.assertIsNone(figures.stylised("Sam Altman"))

    def test_a_private_person_is_never_drawn(self):
        # 2026-11-23: a fan whose post a story quoted was named in a scene.
        # Someone with no Wikipedia article is not a public figure: no picture.
        self.wiki({"Sam Altman": ("Q1", ["Q5"], True)})
        with self.assertRaises(hand.Refused) as caught:
            assets.generate_image({"prompt": "a fan cheers", "figures": ["Sam Altman", "Teeyah"]}, api=self.api)
        self.assertEqual(caught.exception.detail.get("rule"), "figure")
        self.assertIn("Teeyah has no Wikipedia article", str(caught.exception))
        self.assertEqual(self.api.models("/v1/images/edits"), [])

    def test_a_company_or_a_person_with_no_photo_is_not_a_figure(self):
        self.wiki({"Lionsgate": ("Q2", ["Q4830453"], True), "Jane Roe": ("Q3", ["Q5"], False)})
        for name, why in (("Lionsgate", "is not a person"), ("Jane Roe", "has no photo on Wikipedia")):
            with self.assertRaises(hand.Refused) as caught:
                assets.generate_image({"prompt": "x", "figures": [name]}, api=self.api)
            self.assertIn(f"{name} {why}", str(caught.exception))

    def test_a_figure_is_looked_up_once(self):
        seen = self.wiki({"Sam Altman": ("Q1", ["Q5"], True)})
        assets.generate_image({"prompt": "x", "figures": ["Sam Altman"]}, api=self.api)
        after_first = len(seen)
        self.assertGreater(after_first, 0)
        assets.generate_image({"prompt": "x", "figures": ["Sam Altman"]}, api=self.api)
        self.assertEqual(len(seen), after_first)

    def test_at_most_two_figures_are_drawn_even_when_all_are_famous(self):
        self.wiki({"Sam Altman": ("Q1", ["Q5"], True), "Ann Lee": ("Q6", ["Q5"], True), "Bo Chen": ("Q7", ["Q5"], True)})
        with self.assertRaises(hand.Refused) as caught:
            assets.generate_image({"prompt": "x", "figures": ["Sam Altman", "Ann Lee", "Bo Chen"]}, api=self.api)
        self.assertEqual(caught.exception.detail.get("rule"), "body")
        self.assertIn("at most 2", str(caught.exception))
        self.assertEqual(self.api.models("/v1/images/edits"), [])

    def test_a_sellers_content_refusal_falls_to_the_next_model(self):
        # 2026-11-23: the first model's filter turned down three of ten scenes
        # with a famous person in them, and each cost the post its picture.
        self.api.fail["gpt-image-2-5-flare-edit"] = SurplusError(
            "Surplus answered 400: Your prompt violates the content policy of Venice.ai or the model provider.", 400)
        out = self.image()["asset"]
        self.assertEqual(out["model"], "qwen-edit-uncensored")
        self.assertEqual(self.api.models("/v1/images/edits"), ["gpt-image-2-5-flare-edit", "qwen-edit-uncensored"])

    # --- two-pass likeness -------------------------------------------------------
    # 2026-11-23: Venice's gateway, in front of every model on Surplus, turns
    # down a real person's face together with a mocking scene (the Jensen
    # Huang picture, media_assets 97 and 98), yet draws the scene with Teeny's
    # sheet alone and the person's face with a harmless prompt. The last
    # resort, when even the stylised portrait is refused with the scene.

    POLICY = "Surplus answered 400: Your prompt violates the content policy of Venice.ai or the model provider."

    def refuse_portrait_with_scene(self, scene):
        """Venice: a content refusal when a face comes with the scene's words."""
        self.api.fail_if = lambda body: (SurplusError(self.POLICY, 400)
                                         if "images" in body and scene in body["prompt"] else None)

    def test_a_scene_refused_with_the_portrait_is_drawn_then_given_the_likeness(self):
        self.wiki({"Jensen Huang": ("Q1", ["Q5"], True)})
        scene = "the man in the leather jacket sobs into a GPU"
        self.refuse_portrait_with_scene(scene)
        out = assets.generate_image({"prompt": scene, "figures": ["Jensen Huang"]}, api=self.api)["asset"]
        models = assets.IMAGE_MODELS.split(",")
        edits = self.scene_edits()
        self.assertEqual(len(edits), len(models) + 2)
        scene_pass, likeness = edits[-2], edits[-1]
        self.assertEqual(base64.b64decode(scene_pass["image"].split(",", 1)[1]),
                         (self.root / "brain/assets/teeny-reference.png").read_bytes())
        self.assertNotIn("images", scene_pass)
        self.assertIn(scene, scene_pass["prompt"])
        self.assertNotIn("image", likeness)
        # the scene pass's picture, then the stylised portrait
        self.assertEqual([base64.b64decode(i.split(",", 1)[1]) for i in likeness["images"]], [PNG, STYLED])
        self.assertIn("photo 2 is Jensen Huang", likeness["prompt"])
        self.assertIn("Picture 1 is the scene", likeness["prompt"])
        self.assertNotIn(scene, likeness["prompt"])
        self.assertEqual(out["status"], "done")
        self.assertEqual(out["cost_usd"], 0.032)  # both passes, 0.016 each
        self.assertEqual(out["model"], models[0])
        row = assets._db().execute("SELECT detail FROM media_assets WHERE id = ?", (out["id"],)).fetchone()
        self.assertEqual(json.loads(row["detail"])["two_pass"], {"scene": models[0], "likeness": models[0]})
        self.assertEqual(assets.listing("image", 5)["spent_today_usd"], 0.048)  # and the portrait
        self.assertEqual([a["purpose"] for a in assets.listing("image", 5)["assets"]], ["post", "portrait"])

    def test_two_figures_each_get_their_portrait_in_the_likeness_pass(self):
        self.wiki({"Jensen Huang": ("Q1", ["Q5"], True), "Lisa Su": ("Q2", ["Q5"], True)})
        self.refuse_portrait_with_scene("arm wrestle")
        assets.generate_image({"prompt": "they arm wrestle", "figures": ["Jensen Huang", "Lisa Su"]}, api=self.api)
        likeness = self.scene_edits()[-1]
        self.assertEqual(len(likeness["images"]), 3)
        self.assertIn("photo 2 is Jensen Huang", likeness["prompt"])
        self.assertIn("photo 3 is Lisa Su", likeness["prompt"])

    def test_a_non_content_failure_does_not_draw_in_two_passes(self):
        self.wiki({"Jensen Huang": ("Q1", ["Q5"], True)})
        self.api.fail["gpt-image-2-5-flare-edit"] = SurplusError("Surplus answered 402: insufficient credit", 402)
        with self.assertRaises(hand.HandError):
            assets.generate_image({"prompt": "x", "figures": ["Jensen Huang"]}, api=self.api)
        self.assertEqual([b["model"] for b in self.scene_edits()], ["gpt-image-2-5-flare-edit"])

    def test_a_likeness_pass_refused_fails_the_picture_and_keeps_the_scene_cost(self):
        self.wiki({"Jensen Huang": ("Q1", ["Q5"], True)})
        self.api.fail_if = lambda body: SurplusError(self.POLICY, 400) if "images" in body else None
        with self.assertRaises(hand.HandError) as caught:
            assets.generate_image({"prompt": "x", "figures": ["Jensen Huang"]}, api=self.api)
        self.assertIn("likeness pass", str(caught.exception))
        models = assets.IMAGE_MODELS.split(",")
        self.assertEqual(len(self.scene_edits()), 2 * len(models) + 1)
        row = assets.listing("image", 5)["assets"][0]
        self.assertEqual(row["status"], "failed")
        self.assertEqual(row["cost_usd"], 0.016)  # the scene pass was paid for
        self.assertIn("every model refused them for content", row["error"])
        self.assertEqual(assets.listing("image", 5)["spent_today_usd"], 0.032)  # with the portrait
        self.assertEqual(list((self.root / "state" / "media").glob("image-*")), [])

    def test_the_likeness_pass_stops_at_the_daily_dollar_budget(self):
        self.env(MEDIA_MAX_USD_PER_DAY="0.04")  # the portrait and the scene pass fit, the likeness does not
        self.wiki({"Jensen Huang": ("Q1", ["Q5"], True)})
        self.refuse_portrait_with_scene("sobs")
        with self.assertRaises(hand.Refused) as caught:
            assets.generate_image({"prompt": "he sobs", "figures": ["Jensen Huang"]}, api=self.api)
        self.assertEqual(caught.exception.detail["rule"], "budget")
        self.assertNotIn("images", self.scene_edits()[-1])  # the scene pass was the last call
        self.assertEqual(assets.listing("image", 5)["spent_today_usd"], 0.032)

    def test_a_picture_with_no_figures_never_draws_in_two_passes(self):
        for model in assets.IMAGE_MODELS.split(","):
            self.api.fail[model] = SurplusError(self.POLICY, 400)
        with self.assertRaises(hand.HandError):
            self.image()
        self.assertEqual(self.api.models("/v1/images/edits"), assets.IMAGE_MODELS.split(","))

    def test_a_photo_that_could_not_be_fetched_is_tried_again_next_time(self):
        # 2026-11-23: the photo host answered 429 for full-size originals, and
        # the miss was kept for a month as "has no photo".
        from teeny_hands.media import figures
        self.wiki({"Sam Altman": ("Q1", ["Q5"], True)})
        working = figures._get

        def flaky(url, limit, timeout=30.0):
            if url.startswith("https://upload.wikimedia.org/"):
                raise OSError("429")
            return working(url, limit, timeout)

        figures._get = flaky
        self.addCleanup(lambda: setattr(figures, "_get", working))
        self.assertEqual(figures.portrait("Sam Altman")["why"], "has a photo that could not be fetched")
        self.assertEqual(list(figures._folder().iterdir()), [])
        figures._get = working
        self.assertIn("path", figures.portrait("Sam Altman"))

    def test_a_picture_with_nobody_real_in_it_is_asked_for_as_before(self):
        self.image()
        _, _, body = next(c for c in self.api.calls if c[1] == "/v1/images/edits")
        self.assertIn("image", body)
        self.assertNotIn("images", body)

    def test_no_reference_no_image(self):
        (self.root / "brain/assets/teeny-reference.png").unlink()
        with self.assertRaises(hand.HandError):
            self.image()
        self.assertEqual(self.api.models("/v1/images/edits"), [])

    def test_a_seller_that_cannot_take_it_falls_to_the_next_model(self):
        self.api.fail["gpt-image-2-5-flare-edit"] = SurplusError("Surplus answered 503: no healthy sellers", 503)
        out = self.image()["asset"]
        self.assertEqual(out["model"], "qwen-edit-uncensored")
        self.assertEqual(self.api.models("/v1/images/edits"), ["gpt-image-2-5-flare-edit", "qwen-edit-uncensored"])

    def test_a_bad_request_does_not_burn_through_every_model(self):
        self.api.fail["gpt-image-2-5-flare-edit"] = SurplusError("Surplus answered 402: insufficient credit", 402)
        with self.assertRaises(hand.HandError):
            self.image()
        self.assertEqual(self.api.models("/v1/images/edits"), ["gpt-image-2-5-flare-edit"])

    def test_images_stop_at_the_daily_count(self):
        self.env(MEDIA_IMAGE_MAX_PER_DAY=2)
        self.image()
        self.image()
        with self.assertRaises(hand.Refused) as caught:
            self.image()
        self.assertEqual(caught.exception.detail["rule"], "per_day")

    def test_twenty_images_a_day_by_default(self):
        # The open "tag me and I'll draw us" offer (reply_x with an image)
        # needs more than the posts' handful; 20 at 0.016 stays under $1.
        for _ in range(20):
            self.image()
        with self.assertRaises(hand.Refused) as caught:
            self.image()
        self.assertEqual((caught.exception.detail["rule"], caught.exception.detail["limit"]), ("per_day", 20))

    def test_images_stop_at_the_daily_dollar_budget(self):
        self.env(MEDIA_MAX_USD_PER_DAY="0.03")  # each image is quoted 0.016
        self.image()
        self.image()
        with self.assertRaises(hand.Refused) as caught:
            self.image()
        self.assertEqual(caught.exception.detail["rule"], "budget")

    def test_a_failed_image_costs_nothing_against_the_budget(self):
        for model in assets.IMAGE_MODELS.split(","):
            self.api.fail[model] = SurplusError("Surplus answered 503", 503)
        with self.assertRaises(hand.HandError):
            self.image()
        self.assertEqual(assets.listing("image", 5)["spent_today_usd"], 0)

    def test_a_dry_run_spends_nothing(self):
        self.env(DRY_RUN=1)
        out = self.image()
        self.assertTrue(out["dry_run"])
        self.assertEqual(self.api.calls, [])

    # --- videos ---------------------------------------------------------------

    def test_a_video_animates_a_still_of_teeny(self):
        out = self.video()
        still, clip = out["still"], out["asset"]
        submit = next(b for m, p, b in self.api.calls if p == "/v1/video/generations" and m == "POST")
        self.assertEqual(submit["model"], "longcat-distilled-image-to-video")
        self.assertEqual(base64.b64decode(submit["image_url"].split(",", 1)[1]), PNG)
        self.assertEqual(clip["source_id"], still["id"])
        self.assertEqual((self.root / clip["path"]).read_bytes(), MP4)
        self.assertEqual(clip["cost_usd"], 0.072)
        self.assertFalse(clip["cost_estimated"])

    def test_a_video_quoted_over_the_cap_is_cancelled_and_the_next_model_tried(self):
        self.api.quotes["longcat-distilled-image-to-video"] = 329000
        out = self.video()
        self.assertEqual(out["asset"]["model"], "pixverse-c1-image-to-video")
        self.assertIn(("DELETE", "/v1/video/generations/job-longcat-distilled-image-to-video", None), self.api.calls)

    def test_a_failed_job_falls_to_the_next_model_and_costs_nothing(self):
        self.api.jobs["longcat-distilled-image-to-video"] = ["running", "failed"]
        out = self.video()
        self.assertEqual(out["asset"]["model"], "pixverse-c1-image-to-video")

    def test_videos_stop_at_the_daily_count(self):
        self.env(MEDIA_VIDEO_MAX_PER_DAY=1)
        self.video()
        with self.assertRaises(hand.Refused) as caught:
            self.video()
        self.assertEqual(caught.exception.detail["rule"], "per_day")

    def test_a_videos_start_frame_does_not_use_up_the_image_count(self):
        self.env(MEDIA_IMAGE_MAX_PER_DAY=1)
        self.video()
        self.image()  # the video's still did not count

    def test_a_video_reserves_its_cap_against_the_budget_before_starting(self):
        self.env(MEDIA_MAX_USD_PER_DAY="0.10", MEDIA_VIDEO_MAX_USD_PER_JOB="0.20")
        with self.assertRaises(hand.Refused) as caught:
            self.video()
        self.assertEqual(caught.exception.detail["rule"], "budget")
        self.assertEqual(self.api.calls, [])

    def test_every_model_failing_is_an_error_and_records_nothing_spent(self):
        for model in assets.VIDEO_MODELS.split(","):
            self.api.jobs[model] = ["failed"]
        with self.assertRaises(hand.HandError):
            self.video()
        videos = assets.listing("video", 5)["assets"]
        self.assertEqual(videos[0]["status"], "failed")
        self.assertEqual(videos[0]["cost_usd"], 0)


if __name__ == "__main__":
    unittest.main()
