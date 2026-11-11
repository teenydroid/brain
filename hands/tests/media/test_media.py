"""hands/image and hands/video: Teeny's pictures and clips, made from its
character sheet on Surplus, under hard caps, with the cheapest model first."""

import base64
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent / "lib"))

from teeny_hands import hand  # noqa: E402
from teeny_hands.media import assets  # noqa: E402
from teeny_hands.media.surplus import SurplusError  # noqa: E402

T0 = 1_789_369_200.0  # 2026-09-14T07:00:00Z
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
MP4 = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64
REPO = HERE.parents[2]


class FakeSurplus:
    """Answers like Surplus. `fail` maps a model to the SurplusError its
    generation raises; `quotes` a video model to its estimate; `jobs` a video
    model to the statuses its polls walk through."""

    def __init__(self):
        self.calls = []
        self.fail = {}
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
            return {"data": [{"b64_json": base64.b64encode(PNG).decode()}]}
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
        self.assertEqual(out["model"], "muse-image-edit")
        self.assertEqual((self.root / out["path"]).read_bytes(), PNG)
        self.assertTrue(out["path"].startswith("state/media/"))

    def test_no_reference_no_image(self):
        (self.root / "brain/assets/teeny-reference.png").unlink()
        with self.assertRaises(hand.HandError):
            self.image()
        self.assertEqual(self.api.models("/v1/images/edits"), [])

    def test_a_seller_that_cannot_take_it_falls_to_the_next_model(self):
        self.api.fail["muse-image-edit"] = SurplusError("Surplus answered 503: no healthy sellers", 503)
        out = self.image()["asset"]
        self.assertEqual(out["model"], "firered-image-edit")
        self.assertEqual(self.api.models("/v1/images/edits"), ["muse-image-edit", "firered-image-edit"])

    def test_a_bad_request_does_not_burn_through_every_model(self):
        self.api.fail["muse-image-edit"] = SurplusError("Surplus answered 402: insufficient credit", 402)
        with self.assertRaises(hand.HandError):
            self.image()
        self.assertEqual(self.api.models("/v1/images/edits"), ["muse-image-edit"])

    def test_images_stop_at_the_daily_count(self):
        self.env(MEDIA_IMAGE_MAX_PER_DAY=2)
        self.image()
        self.image()
        with self.assertRaises(hand.Refused) as caught:
            self.image()
        self.assertEqual(caught.exception.detail["rule"], "per_day")

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
