"""Uploads and GIFs. The chunked upload is ported from
crates/teeny-x/src/media_tests.rs; the folder rule and the minted-id record
are the hand's (docs/hands.md, invariant 9); the Giphy lookup is the attach
step of crates/teeny-agent/src/tools/post.rs over teeny-media's giphy.rs."""

from __future__ import annotations

import base64
import os
from unittest import mock

from .support import SECRETS, T0, Guard, Scratch
from teeny_hands import hand
from teeny_hands.x import media, posting_rules, publish
from teeny_hands.x.client import XError

PNG = b"\x89PNG\r\n\x1a\n" + b"\x07" * 992
GIF = b"GIF89a" + b"\x01" * 10
MP4 = b"\x00\x00\x00\x18ftypmp42" + b"\x01" * 100


class Uploading(Scratch):
    def setUp(self):
        super().setUp()
        self.at(T0)
        self.media_dir = self.root / "state" / "media"
        self.assets_dir = self.root / "brain" / "assets"
        self.media_dir.mkdir(parents=True)
        self.assets_dir.mkdir(parents=True)

    def initialize(self, mime: str, category: str, total: int):
        return self.x.route(
            "POST",
            "/2/media/upload/initialize",
            {"data": {"id": "m1", "media_key": "3_m1"}},
            when=lambda s: s.json() == {"media_type": mime, "media_category": category, "total_bytes": total},
        )

    def simple_upload(self):
        self.x.route("POST", "/2/media/upload/initialize", {"data": {"id": "m1"}})
        self.x.route("POST", "/2/media/upload/m1/append", {"data": {"expires_at": 1}})
        self.x.route("POST", "/2/media/upload/m1/finalize", {"data": {"id": "m1"}})

    def minted(self) -> list[dict]:
        conn = hand.db()
        posting_rules.ensure_schema(conn)
        return [dict(r) for r in conn.execute("SELECT * FROM x_media")]


class ChunkedUploadTest(Uploading):
    def test_uploads_a_still_in_one_segment_into_the_image_category(self):
        init = self.initialize("image/png", "tweet_image", len(PNG))
        append = self.x.route("POST", "/2/media/upload/m1/append", {"data": {"expires_at": 1}})
        finalize = self.x.route("POST", "/2/media/upload/m1/finalize", {"data": {"id": "m1"}})
        from teeny_hands.x.client import XApi

        self.assertEqual(media.upload_media(XApi.from_settings(), PNG, "image/png"), "m1")
        self.assertEqual((init.times[0], append.times[0], finalize.times[0]), (1, 1, 1))
        sent = self.x.requests("POST", "/2/media/upload/m1/append")[0]
        self.assertEqual(sent.json(), {"media": base64.b64encode(PNG).decode(), "segment_index": 0})
        self.assertTrue(sent.is_oauth())
        self.assertTrue(self.x.requests("POST", "/2/media/upload/initialize")[0].is_oauth())

    def test_uploads_a_video_in_four_megabyte_chunks_and_waits_for_processing(self):
        total = media.UPLOAD_CHUNK_BYTES * 2 + 10
        self.initialize("video/mp4", "tweet_video", total)
        self.x.route("POST", "/2/media/upload/m1/append", {})
        self.x.route(
            "POST",
            "/2/media/upload/m1/finalize",
            {"data": {"id": "m1", "processing_info": {"state": "pending", "check_after_secs": 0}}},
        )
        status = self.x.route("GET", "/2/media/upload", {"data": {"id": "m1", "processing_info": {"state": "succeeded"}}})
        from teeny_hands.x.client import XApi

        self.assertEqual(media.upload_media(XApi.from_settings(), b"\x01" * total, "video/mp4"), "m1")
        segments = [s.json()["segment_index"] for s in self.x.requests("POST", "/2/media/upload/m1/append")]
        self.assertEqual(segments, [0, 1, 2])
        self.assertEqual(status.times[0], 1)
        q = self.x.requests("GET", "/2/media/upload")[0].query
        self.assertEqual((q["media_id"], q["command"]), ("m1", "STATUS"))

    def test_a_gif_goes_through_the_gif_pipeline_and_a_failed_one_is_an_error(self):
        self.initialize("image/gif", "tweet_gif", 3)
        self.x.route("POST", "/2/media/upload/m1/append", {})
        self.x.route("POST", "/2/media/upload/m1/finalize", {"data": {"id": "m1", "processing_info": {"state": "failed"}}})
        from teeny_hands.x.client import XApi

        with self.assertRaisesRegex(XError, "failed to process media m1"):
            media.upload_media(XApi.from_settings(), b"\x01\x02\x03", "image/gif")

    def test_an_upload_without_a_media_id_is_an_error(self):
        self.x.route("POST", "/2/media/upload/initialize", {"errors": [{"title": "Bad Request"}]})
        from teeny_hands.x.client import XApi

        with self.assertRaisesRegex(XError, "media id"):
            media.upload_media(XApi.from_settings(), PNG, "image/png")


class UploadVerbTest(Uploading):
    def test_uploads_a_file_under_state_media_and_records_the_minted_id(self):
        self.simple_upload()
        (self.media_dir / "chart.png").write_bytes(PNG)
        result = media.upload(str(self.media_dir / "chart.png"))
        self.assertEqual(result, {"media_id": "m1", "mime": "image/png", "bytes": len(PNG)})
        [row] = self.minted()
        self.assertEqual((row["media_id"], row["mime"], row["dry_run"]), ("m1", "image/png", 0))

    def test_takes_a_path_relative_to_the_checkout_and_brain_assets(self):
        self.simple_upload()
        (self.assets_dir / "logo.gif").write_bytes(GIF)
        self.assertEqual(media.upload("brain/assets/logo.gif")["mime"], "image/gif")

    def test_refuses_anything_outside_the_two_folders(self):
        (self.root / "secrets" / "x.png").write_bytes(PNG)
        (self.media_dir / "sub").mkdir()
        outside = self.root / "elsewhere.png"
        outside.write_bytes(PNG)
        (self.media_dir / "link.png").symlink_to(outside)
        (self.media_dir / "env.png").symlink_to(self.root / "secrets" / "teeny.env")
        for path in (
            str(self.root / "secrets" / "x.png"),
            str(self.root / "secrets" / "teeny.env"),
            str(self.media_dir / ".." / ".." / "elsewhere.png"),
            "state/media/../../elsewhere.png",
            str(self.media_dir / "link.png"),
            str(self.media_dir / "env.png"),
            str(self.media_dir / "sub"),
            str(self.media_dir / "missing.png"),
            "/etc/hosts",
        ):
            with self.assertRaises(hand.Refused, msg=path) as caught:
                media.upload(path)
            self.assertEqual(caught.exception.detail["rule"], "path")
            for secret in SECRETS.values():
                self.assertNotIn(secret, caught.exception.reason)
        self.assertEqual(self.x.requests(), [])
        self.assertEqual(self.minted(), [])

    def test_refuses_a_file_that_is_not_an_image_or_a_video(self):
        (self.media_dir / "notes.png").write_bytes(b"just text")
        with self.assertRaises(hand.Refused) as caught:
            media.upload(str(self.media_dir / "notes.png"))
        self.assertEqual(caught.exception.detail["rule"], "type")

    def test_tells_the_types_apart_by_their_bytes(self):
        cases = {
            "image/png": PNG,
            "image/jpeg": b"\xff\xd8\xff\xe0" + b"\x00" * 10,
            "image/gif": GIF,
            "image/webp": b"RIFF\x00\x00\x00\x00WEBPVP8 ",
            "video/mp4": MP4,
        }
        for mime, data in cases.items():
            self.assertEqual(media.sniff(data), mime)

    def test_a_dry_run_uploads_nothing_and_mints_a_rehearsal_id(self):
        self.configure(DRY_RUN="1")
        (self.media_dir / "chart.png").write_bytes(PNG)
        result = media.upload(str(self.media_dir / "chart.png"))
        self.assertTrue(result["dry_run"])
        self.assertTrue(result["media_id"].startswith("dry-run-"))
        self.assertEqual(self.x.requests(), [])
        self.assertEqual(self.minted()[0]["dry_run"], 1)


class GiphyTest(Uploading):
    settings = {"GIPHY_API_KEY": "giphy-KEY-FAKE"}

    def setUp(self):
        super().setUp()
        self.configure(GIPHY_API_BASE=self.x.base)
        self.guard = Guard()
        patcher = mock.patch.object(publish, "check_outbound", self.guard)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.x.route("POST", "/2/tweets", {"data": {"id": "100"}}, status=201)

    def giphy(self, gif_id: str, url: str, size="16") -> None:
        self.x.route(
            "GET",
            f"/v1/gifs/{gif_id}",
            {"data": {"id": gif_id, "title": "gm", "images": {"downsized_medium": {"url": url, "size": size}}}},
        )

    def test_attaches_a_giphy_gif_by_its_id_to_a_live_post(self):
        self.giphy("abc123", f"{self.x.base}/media/abc123.gif")
        self.x.route("GET", "/media/abc123.gif", raw=GIF, content_type="image/gif")
        self.initialize("image/gif", "tweet_gif", len(GIF))
        self.x.route("POST", "/2/media/upload/m1/append", {})
        self.x.route("POST", "/2/media/upload/m1/finalize", {"data": {"id": "m1"}})
        publish.post({"anchor": "hn:1", "text": "gm", "gif_id": "abc123"})
        self.assertEqual(self.x.requests("POST", "/2/tweets")[0].json(), {"text": "gm", "media": {"media_ids": ["m1"]}})
        self.assertEqual(self.x.requests("GET", "/v1/gifs/abc123")[0].query["api_key"], "giphy-KEY-FAKE")
        self.assertEqual(self.minted()[0]["source"], "giphy:abc123")

    def test_refuses_a_gif_giphy_does_not_have(self):
        self.x.route("GET", "/v1/gifs/nope", {"message": "Not Found"}, status=404)
        with self.assertRaises(hand.Refused) as caught:
            publish.post({"anchor": "hn:1", "text": "gm", "gif_id": "nope"})
        self.assertNotIn("giphy-KEY-FAKE", caught.exception.reason)
        self.assertEqual(self.x.requests("POST", "/2/tweets"), [])

    def test_never_downloads_a_rendition_from_somewhere_else(self):
        self.giphy("abc123", "https://evil.example/abc123.gif")
        with self.assertRaises(hand.Refused):
            publish.post({"anchor": "hn:1", "text": "gm", "gif_id": "abc123"})
        self.assertEqual(self.x.requests("POST", "/2/tweets"), [])

    def test_skips_a_rendition_over_the_x_limit(self):
        self.giphy("big", f"{self.x.base}/media/big.gif", size=str(16 * 1024 * 1024))
        with self.assertRaises(hand.Refused):
            publish.post({"anchor": "hn:1", "text": "gm", "gif_id": "big"})
        self.assertEqual(self.x.requests("GET", "/media/big.gif"), [])

    def test_without_a_giphy_key_a_gif_is_refused(self):
        self.configure(GIPHY_API_KEY=None, GIPHY_API_BASE=self.x.base)
        with self.assertRaises(hand.Refused):
            publish.post({"anchor": "hn:1", "text": "gm", "gif_id": "abc123"})
        self.assertEqual(self.x.writes(), [])

    def test_a_gif_that_cannot_be_fetched_releases_the_reply_claim(self):
        from .support import me

        me(self.x, "1000")
        self.x.route("GET", "/2/tweets/5", {"data": {"id": "5", "text": "hi", "author_id": "42"}})
        self.x.route("GET", "/v1/gifs/abc123", {"message": "down"}, status=500)
        with self.assertRaises(hand.HandError):
            publish.reply("5", {"text": "gm", "gif_id": "abc123"})
        conn = hand.db()
        self.assertEqual(conn.execute("SELECT count(*) FROM x_replies").fetchone()[0], 0)


if __name__ == "__main__":
    import unittest

    unittest.main()
