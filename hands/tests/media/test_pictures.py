"""hands/image's pictures as X takes them: under 5 MB, with alt text, and a
report of the scenes drawn so far so a writer can see what it keeps
repeating."""

import base64
import io
import os
import random
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent / "lib"))
sys.path.insert(0, str(HERE.parent))

from pillow_support import needs_pillow  # noqa: E402
from teeny_hands import hand  # noqa: E402
from teeny_hands.media import assets, fit  # noqa: E402

T0 = 1_789_369_200.0  # 2026-09-14T07:00:00Z
REPO = HERE.parents[2]
SMALL_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def noise_png(width: int, height: int, seed: int = 7) -> bytes:
    """A PNG of random pixels: noise does not compress, so its size is
    about width * height * 3 bytes, like the image model's busy renders."""
    from PIL import Image

    rng = random.Random(seed)
    image = Image.frombytes("RGB", (width, height), bytes(rng.getrandbits(8) for _ in range(width * height * 3)))
    out = io.BytesIO()
    image.save(out, "PNG")
    return out.getvalue()


class PictureSurplus:
    """Answers an image edit with `picture`."""

    def __init__(self, picture: bytes):
        self.picture = picture
        self.calls = []

    def call(self, method, path, body=None, timeout=60.0, headers=None):
        self.calls.append((method, path, body))
        if path.startswith("/api/markets/"):
            return {"offers": [{"available": True, "media_unit_price": 16000, "media_unit": "image"}]}
        if path == "/v1/images/edits":
            return {"data": [{"b64_json": base64.b64encode(self.picture).decode()}]}
        raise AssertionError(f"unexpected call {method} {path}")


class PictureTest(unittest.TestCase):
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

    def generate(self, picture=SMALL_PNG, **body):
        body.setdefault("prompt", "waving at the desk")
        return assets.generate_image(body, api=PictureSurplus(picture))


@needs_pillow
class ShrinkTest(PictureTest):
    def test_a_generated_picture_over_five_megabytes_is_shrunk_under_x_s_limit(self):
        # image-12.png came back at 5,263,141 bytes and X refused its upload.
        big = noise_png(1400, 1300)
        self.assertGreater(len(big), fit.X_IMAGE_MAX_BYTES)
        out = self.generate(big)["asset"]
        written = (self.root / out["path"]).read_bytes()
        self.assertLess(len(written), fit.X_IMAGE_MAX_BYTES)
        from PIL import Image

        picture = Image.open(io.BytesIO(written))
        self.assertGreaterEqual(min(picture.size), fit.MIN_SIDE)
        self.assertTrue(out["path"].endswith(".jpg"), out["path"])
        self.assertEqual(picture.format, "JPEG")

    def test_a_picture_under_the_limit_is_kept_byte_for_byte(self):
        out = self.generate(SMALL_PNG)["asset"]
        self.assertEqual((self.root / out["path"]).read_bytes(), SMALL_PNG)
        self.assertTrue(out["path"].endswith(".png"))

    def test_shrinking_is_deterministic(self):
        big = noise_png(1400, 1300)
        first = fit.under_limit(big, "png")
        second = fit.under_limit(big, "png")
        self.assertEqual(first[0], second[0])
        self.assertEqual(first[2]["from_bytes"], len(big))
        self.assertLess(first[2]["to_bytes"], fit.X_IMAGE_MAX_BYTES)

    def test_a_transparent_picture_stays_a_png_and_is_scaled(self):
        from PIL import Image

        rng = random.Random(3)
        size = (1300, 1300)
        image = Image.frombytes("RGBA", size, bytes(rng.getrandbits(8) for _ in range(size[0] * size[1] * 4)))
        data, ext = fit.encode(image, "png")
        self.assertEqual(ext, "png")
        self.assertLess(len(data), fit.X_IMAGE_MAX_BYTES)
        self.assertLess(Image.open(io.BytesIO(data)).size[0], size[0])

    def test_a_limit_that_would_need_a_tiny_picture_is_refused(self):
        with self.assertRaises(hand.Refused) as caught:
            fit.under_limit(noise_png(400, 400), "png", limit=1_000)
        self.assertEqual(caught.exception.detail["rule"], "size")


class AltTextTest(PictureTest):
    def test_alt_text_is_kept_on_the_asset(self):
        out = self.generate(alt="a small yellow robot waving from a desk")["asset"]
        self.assertEqual(out["alt"], "a small yellow robot waving from a desk")
        self.assertEqual(assets.get(out["id"])["asset"]["alt"], "a small yellow robot waving from a desk")

    def test_without_alt_text_the_field_is_empty(self):
        self.assertIsNone(self.generate()["asset"]["alt"])

    def test_alt_text_over_the_x_limit_or_not_a_string_is_refused(self):
        for alt in ("x" * 1001, 5, ["a"]):
            with self.assertRaises(hand.Refused, msg=repr(alt)):
                self.generate(alt=alt)

    def test_a_dry_run_says_the_alt_text_it_would_keep(self):
        self.env(DRY_RUN=1)
        out = assets.generate_image({"prompt": "waving", "alt": "a robot waving"})
        self.assertEqual(out["alt"], "a robot waving")


class ScenesTest(PictureTest):
    def test_scenes_lists_the_last_pictures_and_the_words_they_repeat(self):
        for prompt in ("a smol yellow robot at a desk with a mug, terminal glowing",
                       "a smol yellow robot at a cluttered desk, coffee mug steaming",
                       "the robot on a beach at noon, sand everywhere"):
            self.generate(prompt=prompt)
        out = assets.scenes(10)
        self.assertEqual([s["scene"] for s in out["scenes"]][0], "the robot on a beach at noon, sand everywhere")
        self.assertEqual(len(out["scenes"]), 3)
        repeated = {r["word"]: r["pictures"] for r in out["repeated"]}
        self.assertEqual(repeated["robot"], 3)
        self.assertEqual(repeated["desk"], 2)
        self.assertEqual(repeated["mug"], 2)
        self.assertNotIn("beach", repeated)
        self.assertNotIn("a", repeated)

    def test_scenes_leaves_out_the_house_style_and_failed_or_frame_pictures(self):
        self.env(MEDIA_STYLE="soft 3D render, warm light")
        self.generate(prompt="reading a letter by the window")
        conn = assets._db()
        try:
            conn.execute("INSERT INTO media_assets (kind, status, prompt, purpose, created_at) VALUES "
                         "('image', 'failed', 'a refused scene', 'post', ?)", (T0,))
            conn.execute("INSERT INTO media_assets (kind, status, prompt, purpose, created_at) VALUES "
                         "('image', 'done', 'a video frame', 'video_frame', ?)", (T0,))
        finally:
            conn.close()
        out = assets.scenes(10)
        self.assertEqual([s["scene"] for s in out["scenes"]], ["reading a letter by the window"])

    def test_scenes_reads_only_the_last_n(self):
        for n in range(5):
            self.generate(prompt=f"scene number {n}")
        self.assertEqual(len(assets.scenes(2)["scenes"]), 2)


if __name__ == "__main__":
    unittest.main()
