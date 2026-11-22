"""Pillow for the tests that draw or measure pictures (hands/meme, the
image shrink, the viewer check).

Pillow is python3-pil in the spine image. An image built before it was
added has none, and the hand-test sandbox runs on that image, so a test
that needs it is skipped there rather than failing every candidate. CI
installs it and sets TEENY_TESTS_REQUIRE_PILLOW=1, so there a missing
Pillow fails instead of skipping: the tests never pass by not running.
"""

from __future__ import annotations

import importlib.util
import os
import unittest

HAVE_PILLOW = importlib.util.find_spec("PIL") is not None
REQUIRED = os.environ.get("TEENY_TESTS_REQUIRE_PILLOW", "").strip() == "1"


def needs_pillow(target):
    """Skips `target` (a test class or method) when Pillow is missing,
    unless CI requires it."""
    if HAVE_PILLOW or REQUIRED:
        return target
    return unittest.skip("Pillow (python3-pil) is not installed")(target)


class PillowPresentTest(unittest.TestCase):
    def test_pillow_is_installed_where_it_is_required(self):
        if REQUIRED:
            self.assertTrue(HAVE_PILLOW, "TEENY_TESTS_REQUIRE_PILLOW=1 but Pillow is not installed")
