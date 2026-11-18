"""hands/meme caption and comic: exact words drawn by code, fitted to be
legible or refused, under 5 MB, recorded in media_assets."""

from __future__ import annotations

import hashlib
import json
import subprocess

from .support import ART, REPO, Scratch, changed
from pillow_support import needs_pillow
from teeny_hands import hand
from teeny_hands.media import assets, fit
from teeny_hands.meme import compose, memes


@needs_pillow
class CaptionTest(Scratch):
    def caption(self, **body):
        body.setdefault("image", self.picture("flat.png"))
        return memes.caption(body)

    def test_top_and_bottom_words_change_the_picture_only_where_they_are_drawn(self):
        source = self.picture("flat.png")
        out = self.caption(image=source, top="me at 3am", bottom="still not a real boy")["asset"]
        made, before = self.load(out["path"]), self.load(source)
        self.assertEqual(made.size, before.size)
        width, height = made.size
        self.assertGreater(changed(made, before, (0, 0, width, height // 4)), 500)
        self.assertGreater(changed(made, before, (0, 3 * height // 4, width, height)), 500)
        self.assertEqual(changed(made, before, (0, 2 * height // 5, width, 3 * height // 5)), 0)

    def test_the_result_is_recorded_as_a_meme_with_its_words_and_alt_text(self):
        out = self.caption(top="me at 3am")["asset"]
        self.assertEqual((out["kind"], out["status"], out["model"], out["cost_usd"]), ("meme", "done", "compositor", 0.0))
        self.assertTrue(out["path"].startswith("state/media/meme-"))
        self.assertEqual(out["detail"]["blocks"][0]["lines"], ["me at 3am"])
        self.assertIn('"me at 3am"', out["alt"])
        self.assertEqual(memes.get(out["id"])["asset"]["path"], out["path"])
        self.assertEqual(memes.listing(5)["assets"][0]["id"], out["id"])

    def test_given_alt_text_is_kept(self):
        out = self.caption(top="hi", alt="a small yellow robot holding a glowing heart")["asset"]
        self.assertEqual(out["alt"], "a small yellow robot holding a glowing heart")

    def test_the_same_input_makes_the_same_bytes(self):
        first = self.caption(top="same words", bottom="every time")["asset"]
        second = self.caption(top="same words", bottom="every time")["asset"]
        digest = lambda o: hashlib.sha256((self.root / o["path"]).read_bytes()).hexdigest()  # noqa: E731
        self.assertNotEqual(first["path"], second["path"])
        self.assertEqual(digest(first), digest(second))

    def test_the_ready_art_takes_a_caption_and_stays_a_jpeg_under_five_megabytes(self):
        out = self.caption(image=ART, top="small robot", bottom="big feelings")["asset"]
        self.assertTrue(out["path"].endswith(".jpg"))
        self.assertLess((self.root / out["path"]).stat().st_size, fit.X_IMAGE_MAX_BYTES)
        self.assertEqual(self.load(out["path"]).format, "JPEG")

    def test_a_png_too_busy_for_five_megabytes_comes_out_under_them(self):
        from PIL import Image
        import random

        rng = random.Random(1)
        Image.frombytes("RGB", (2048, 1600), rng.randbytes(2048 * 1600 * 3)).save(self.root / "state/media/noise.png")
        out = self.caption(image="state/media/noise.png", top="static")["asset"]
        self.assertLess((self.root / out["path"]).stat().st_size, fit.X_IMAGE_MAX_BYTES)

    def test_label_adds_white_bands_and_leaves_the_picture_whole(self):
        source = self.picture("flat.png", size=(1000, 800))
        out = self.caption(image=source, top="when the build passes", bottom="first try", style="label")["asset"]
        made = self.load(out["path"])
        self.assertEqual(made.size[0], 1000)
        self.assertGreater(made.size[1], 800)
        self.assertEqual(made.getpixel((3, 3)), (255, 255, 255))
        top_band = made.size[1] - 800 - (made.size[1] - 800) // 2
        middle = made.getpixel((500, top_band + 400))
        self.assertTrue(all(abs(a - b) < 8 for a, b in zip(middle, (40, 90, 160))), middle)

    def test_subtitle_sets_smaller_words_than_impact(self):
        impact = self.caption(top="a line of words")["asset"]["detail"]["blocks"][0]["size"]
        subtitle = self.caption(bottom="a line of words", style="subtitle")["asset"]["detail"]["blocks"][0]["size"]
        self.assertLess(subtitle, impact)

    def test_long_words_wrap_at_spaces_and_are_never_changed(self):
        text = "it is 4am and i have read the same error message eleven times"
        out = self.caption(bottom=text)["asset"]
        lines = out["detail"]["blocks"][0]["lines"]
        self.assertGreater(len(lines), 1)
        self.assertEqual(" ".join(lines), text)

    def test_a_writers_newline_is_a_line_break(self):
        lines = self.caption(top="one\ntwo")["asset"]["detail"]["blocks"][0]["lines"]
        self.assertEqual(lines, ["one", "two"])

    def test_a_small_picture_is_drawn_on_at_a_legible_width(self):
        out = self.caption(image=self.picture("small.png", size=(400, 300)), top="hi")["asset"]
        self.assertEqual(self.load(out["path"]).size, (800, 600))

    # --- refusals ------------------------------------------------------------------

    def refused(self, rule, **body):
        with self.assertRaises(hand.Refused) as caught:
            self.caption(**body)
        self.assertEqual(caught.exception.detail["rule"], rule, caught.exception.reason)
        return caught.exception

    def test_words_that_would_need_a_tiny_size_are_refused_not_shrunk(self):
        wide = self.picture("wide.png", size=(1600, 400))
        reason = self.refused("fit", image=wide, top=" ".join(["word"] * 24)).reason
        self.assertIn("legibly", reason)
        self.assertEqual(list((self.root / "state" / "media").glob("meme-*")), [])

    def test_one_word_wider_than_the_picture_is_refused(self):
        self.refused("fit", image=self.picture("narrow.png", size=(200, 900)),
                     top="supercalifragilisticexpialidocious-ishness")

    def test_a_character_the_font_cannot_draw_is_refused(self):
        reason = self.refused("fit", top="gm \U0001F916").reason
        self.assertIn("cannot draw", reason)

    def test_no_words_too_many_words_links_and_held_words_are_refused(self):
        self.refused("text")
        self.refused("text", top="   ")
        self.refused("text", top="x" * (memes.MAX_TEXT + 1))
        self.refused("text", top="read teeny.example.com")
        self.refused("text", top="what the fuck")
        self.refused("text", top="bell\x07here")
        self.refused("text", top=5)

    def test_pictures_outside_the_two_folders_are_refused(self):
        (self.root / "secrets" / "x.png").write_bytes(b"\x89PNG\r\n\x1a\n")
        (self.root / "state" / "media" / "link.png").symlink_to(self.root / "secrets" / "x.png")
        for path in ("secrets/x.png", "state/media/link.png", "state/media/../../secrets/x.png", "/etc/hosts",
                     "state/media/missing.png", "", None):
            self.refused("path", image=path, top="hi")

    def test_a_file_that_is_not_a_picture_or_is_animated_is_refused(self):
        (self.root / "state" / "media" / "notes.png").write_text("not a picture")
        self.refused("source", image="state/media/notes.png", top="hi")
        from PIL import Image

        frames = [Image.new("RGB", (300, 300), c) for c in ((255, 0, 0), (0, 255, 0))]
        frames[0].save(self.root / "state/media/anim.gif", save_all=True, append_images=frames[1:])
        self.refused("source", image="state/media/anim.gif", top="hi")

    def test_a_picture_over_the_pixel_cap_is_refused_before_it_is_decoded(self):
        from PIL import Image

        Image.new("1", (7000, 7000)).save(self.root / "state/media/huge.png")
        self.refused("pixels", image="state/media/huge.png", top="hi")
        with self.assertRaises(hand.Refused) as caught:
            memes.comic({"panels": [{"image": self.picture("ok.png")}, {"image": "state/media/huge.png"}]})
        self.assertEqual(caught.exception.detail["rule"], "pixels")

    def test_a_picture_too_small_to_caption_is_refused(self):
        self.refused("fit", image=self.picture("tiny.png", size=(150, 150)), top="hi")

    def test_an_unknown_style_or_field_is_refused(self):
        self.refused("style", top="hi", style="comic-sans")
        with self.assertRaises(hand.HandError):
            self.caption(top="hi", url="https://example.com/x.png")

    def test_the_daily_cap_counts_memes_and_comics(self):
        self.env(MEME_MAX_PER_DAY=1)
        self.caption(top="one")
        self.refused("per_day", top="two")

    def test_a_dry_run_sets_the_words_and_writes_nothing(self):
        self.env(DRY_RUN=1)
        out = self.caption(top="me at 3am")
        self.assertEqual((out["dry_run"], out["would_make"]), (True, "meme"))
        self.assertEqual(out["blocks"][0]["lines"], ["me at 3am"])
        self.assertEqual(list((self.root / "state" / "media").glob("meme-*")), [])
        self.assertEqual(memes.listing(5)["assets"], [])
        self.refused("fit", image=self.picture("wide.png", size=(1600, 400)), top=" ".join(["word"] * 24))


@needs_pillow
class ComicTest(Scratch):
    def panels(self, n, captions=True):
        colours = [(200, 40, 40), (40, 200, 40), (40, 40, 200), (200, 200, 40)]
        return [{"image": self.picture(f"p{i}.png", colour=colours[i]),
                 **({"caption": f"panel {i + 1} words"} if captions else {})} for i in range(n)]

    def test_three_panels_make_a_row_with_a_lettered_band_under_each(self):
        out = memes.comic({"panels": self.panels(3)})["asset"]
        made = self.load(out["path"])
        cell_w, cell_h = out["detail"]["cell"]
        self.assertEqual(out["kind"], "comic")
        self.assertEqual(made.size[0], 3 * cell_w + 4 * compose.GUTTER)
        self.assertGreater(made.size[1], cell_h + 2 * compose.GUTTER)
        for i, colour in enumerate([(200, 40, 40), (40, 200, 40), (40, 40, 200)]):
            x = compose.GUTTER + i * (cell_w + compose.GUTTER) + cell_w // 2
            pixel = made.getpixel((x, compose.GUTTER + cell_h // 2))
            self.assertTrue(all(abs(a - b) < 12 for a, b in zip(pixel, colour)), (i, pixel))
            band = made.crop((x - cell_w // 2, compose.GUTTER + cell_h, x + cell_w // 2,
                              made.size[1] - compose.GUTTER)).convert("L")
            self.assertGreater(sum(1 for v in band.getdata() if v < 80), 200, f"panel {i} has no lettering")
        self.assertEqual([p["lines"] for p in out["detail"]["panels"]],
                         [["panel 1 words"], ["panel 2 words"], ["panel 3 words"]])

    def test_four_panels_default_to_a_grid(self):
        out = memes.comic({"panels": self.panels(4)})["asset"]
        made = self.load(out["path"])
        self.assertEqual(out["detail"]["layout"], "grid")
        self.assertEqual(made.size[0], 2 * 720 + 3 * compose.GUTTER)

    def test_a_strip_stacks_the_panels_and_captions_can_go_over(self):
        out = memes.comic({"panels": self.panels(2), "layout": "strip", "captions": "over"})["asset"]
        made = self.load(out["path"])
        self.assertEqual(made.size[0], 900 + 2 * compose.GUTTER)
        self.assertGreater(made.size[1], 2 * 675)
        self.assertEqual(made.getpixel((compose.GUTTER + 5, compose.GUTTER + 5)), (255, 255, 255))

    def test_every_panel_is_lettered_at_one_size(self):
        panels = self.panels(3)
        panels[1]["caption"] = "a much longer caption that has to wrap onto more lines"
        out = memes.comic({"panels": panels})["asset"]
        self.assertIsInstance(out["detail"]["font_size"], int)
        self.assertGreater(len(out["detail"]["panels"][1]["lines"]), 1)

    def test_a_comic_without_words_has_no_bands(self):
        out = memes.comic({"panels": self.panels(2, captions=False)})["asset"]
        self.assertEqual(self.load(out["path"]).size[1], 720 + 2 * compose.GUTTER)

    def test_a_comic_is_under_five_megabytes_and_deterministic(self):
        a = memes.comic({"panels": self.panels(3)})["asset"]
        b = memes.comic({"panels": self.panels(3)})["asset"]
        data_a, data_b = (self.root / a["path"]).read_bytes(), (self.root / b["path"]).read_bytes()
        self.assertLess(len(data_a), fit.X_IMAGE_MAX_BYTES)
        self.assertEqual(data_a, data_b)

    def test_refusals(self):
        for body, rule in (({"panels": self.panels(1)}, "panels"), ({"panels": self.panels(4) + self.panels(1)}, "panels"),
                           ({"panels": self.panels(3), "layout": "grid"}, "fit"),
                           ({"panels": self.panels(2), "layout": "diagonal"}, "layout"),
                           ({"panels": [{"image": "secrets/x.png", "caption": "hi"}] * 2}, "path"),
                           ({"panels": [{"image": self.picture("a.png"), "caption": " ".join(["word"] * 40)}] * 2},
                            "text"),
                           ({"panels": [{"image": self.picture("a.png"), "caption": " ".join(["word"] * 17)}] * 4,
                             "layout": "row"}, "fit")):
            with self.assertRaises(hand.Refused, msg=json.dumps(body)[:80]) as caught:
                memes.comic(body)
            self.assertEqual(caught.exception.detail["rule"], rule, caught.exception.reason)


@needs_pillow
class LedgerTest(Scratch):
    def test_a_ledger_made_before_memes_is_rebuilt_to_take_them(self):
        old = assets.SCHEMA.replace(assets.KINDS_CHECK, "kind IN ('image', 'video', 'reel', 'song', 'music')")
        conn = hand.open_db(old)
        conn.execute("INSERT INTO media_assets (kind, status, prompt, created_at) VALUES ('image', 'done', 'x', 1)")
        conn.close()
        out = memes.caption({"image": ART, "top": "hi"})["asset"]
        self.assertEqual(out["kind"], "meme")
        self.assertEqual(assets.get(1)["asset"]["kind"], "image")


@needs_pillow
class CommandTest(Scratch):
    def test_the_hand_runs_as_a_command(self):
        run = subprocess.run([str(REPO / "hands" / "meme"), "caption"], input=json.dumps({"image": ART, "top": "hi"}),
                             capture_output=True, text=True, timeout=120)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertTrue(json.loads(run.stdout)["asset"]["path"].startswith("state/media/meme-"))

    def test_a_refusal_is_exit_two_with_the_rule(self):
        run = subprocess.run([str(REPO / "hands" / "meme"), "caption"], input=json.dumps({"image": ART}),
                             capture_output=True, text=True, timeout=120)
        self.assertEqual(run.returncode, 2)
        self.assertEqual(json.loads(run.stdout)["rule"], "text")
