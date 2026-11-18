"""Shared by the hands/meme tests: a scratch TEENY_ROOT with pictures in
state/media/ and brain/assets/, and Pillow helpers."""

from __future__ import annotations

import io
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent / "lib"))
sys.path.insert(0, str(HERE.parent))

from teeny_hands import hand  # noqa: E402

REPO = HERE.parents[2]
T0 = 1_789_369_200.0  # 2026-09-14T07:00:00Z
ART = "brain/assets/tiny/tiny-love.jpg"


class Scratch(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "secrets").mkdir()
        (self.root / "state" / "media").mkdir(parents=True)
        (self.root / "brain" / "assets" / "tiny").mkdir(parents=True)
        shutil.copy(REPO / ART, self.root / ART)
        (self.root / "secrets" / "teeny.env").write_text("DRY_RUN=0\n")
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

    def picture(self, name: str, size=(1000, 800), colour=(40, 90, 160), fmt="PNG") -> str:
        """A flat-coloured picture under state/media/, its relative path."""
        from PIL import Image

        path = self.root / "state" / "media" / name
        Image.new("RGB", size, colour).save(path, fmt)
        return f"state/media/{name}"

    def load(self, relative: str):
        from PIL import Image

        image = Image.open(self.root / relative)
        image.load()
        return image


def changed(a, b, box) -> int:
    """How many pixels differ between `a` and `b` inside `box`."""
    from PIL import ImageChops

    diff = ImageChops.difference(a.convert("RGB").crop(box), b.convert("RGB").crop(box)).convert("L")
    return sum(1 for v in diff.getdata() if v > 40)


def png_bytes(image) -> bytes:
    out = io.BytesIO()
    image.save(out, "PNG")
    return out.getvalue()
