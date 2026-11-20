"""hands/video's flavors: one binary, a registry of the kinds of video it
makes (media/flavors/), each a group of steps (`video remake keys`). The old
flat verbs (`generate`, `skit`, `remake-keys`, `remake`, `remake-drop`) stay
as aliases that reach the same function with the same body, since the studio
and the frozen tests call them; hands/image does not change. Every handler
here is a fake: nothing is bought."""

import contextlib
import io
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent / "lib"))

from teeny_hands import hand  # noqa: E402
from teeny_hands.media import assets, cli, flavors, remakes, skits  # noqa: E402


def run(argv, body=None, kind="video", registry=None):
    """hands/<kind> ARGV with BODY (JSON) on stdin: (exit code, the JSON it
    printed)."""
    out, err = io.StringIO(), io.StringIO()
    stdin = io.StringIO("" if body is None else json.dumps(body))
    with mock.patch.object(sys, "stdin", stdin), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        if registry is None:
            code = cli.main(kind, argv)
        else:
            code = hand.run(kind, cli.parser(kind, registry), argv)
    return code, json.loads(out.getvalue())


class RegistryTest(unittest.TestCase):
    def test_the_flavors_in_order(self):
        self.assertEqual(list(flavors.REGISTRY), ["clip", "skit", "remake"])

    def test_each_flavor_s_steps_kinds_and_cap(self):
        clip, skit, remake = (flavors.REGISTRY[n] for n in ("clip", "skit", "remake"))
        self.assertEqual([s.name for s in clip.steps], ["make"])
        self.assertEqual([s.name for s in skit.steps], ["make"])
        self.assertEqual([s.name for s in remake.steps], ["keys", "render", "drop"])
        self.assertEqual(clip.kinds, ("video", "image"))
        self.assertEqual(skit.kinds, ("skit", "shot"))
        self.assertEqual(remake.kinds, ("remake", "key", "shot"))
        self.assertEqual([f.shares_video_cap for f in (clip, skit, remake)], [False, True, True])
        self.assertEqual([f.default_step for f in (clip, skit, remake)], [None, "make", "render"])
        self.assertTrue(all(s.takes_body for f in (clip, skit, remake) for s in f.steps))

    def test_a_flavor_s_kind_is_the_first_it_books(self):
        self.assertEqual(flavors.REGISTRY["remake"].kind, "remake")
        self.assertEqual(flavors.REGISTRY["clip"].kind, "video")

    def test_a_default_step_must_be_one_of_its_steps(self):
        step = flavors.Step("go", lambda a, body: {}, "go", True)
        with self.assertRaises(ValueError):
            flavors.Flavor("demo", "a demo", (step,), ("demo",), False, default_step="stop")

    def test_step_names_are_unique(self):
        step = flavors.Step("go", lambda a, body: {}, "go", True)
        with self.assertRaises(ValueError):
            flavors.Flavor("demo", "a demo", (step, step), ("demo",), False)

    def test_flavors_are_frozen(self):
        with self.assertRaises(AttributeError):
            flavors.REGISTRY["skit"].name = "other"

    def test_video_kinds_are_the_registry_s_shared_cap(self):
        self.assertEqual(flavors.video_kinds(), ("skit", "remake"))
        self.assertEqual(skits.VIDEO_KINDS, ("skit", "remake"))
        self.assertEqual(skits.VIDEO_KINDS, flavors.video_kinds())


class FlavorsVerbTest(unittest.TestCase):
    def test_video_flavors_lists_name_summary_steps_and_kinds(self):
        code, out = run(["flavors"])
        self.assertEqual(code, 0)
        self.assertTrue(out["ok"])
        self.assertEqual([f["name"] for f in out["flavors"]], ["clip", "skit", "remake"])
        remake = out["flavors"][2]
        self.assertEqual([s["name"] for s in remake["steps"]], ["keys", "render", "drop"])
        self.assertEqual(remake["kinds"], ["remake", "key", "shot"])
        self.assertEqual(remake["default_step"], "render")
        self.assertTrue(remake["shares_video_cap"])
        self.assertTrue(remake["summary"])
        self.assertTrue(all(s["help"] and s["takes_body"] for s in remake["steps"]))

    def test_video_flavors_reads_no_body(self):
        args = cli.parser("video").parse_args(["flavors"])
        self.assertFalse(args.takes_body)
        self.assertEqual(args.handler(args, None), flavors.describe())


#: Each new spelling, its old one, and the function both must reach.
PAIRS = (
    (["clip", "make"], ["generate"], assets, "generate_video"),
    (["skit", "make"], ["skit"], skits, "generate_skit"),
    (["remake", "keys"], ["remake-keys"], remakes, "generate_keys"),
    (["remake", "render"], ["remake"], remakes, "generate_remake"),
    (["remake", "drop"], ["remake-drop"], remakes, "drop_remake"),
)


class DispatchTest(unittest.TestCase):
    def test_each_nested_verb_reaches_its_old_alias_s_function_with_the_same_body(self):
        for new, old, module, name in PAIRS:
            for argv in (new, old):
                with self.subTest(argv=argv):
                    seen = []
                    with mock.patch.object(module, name, lambda body: seen.append(body) or {"made": name}):
                        code, out = run(argv, {"x": 1})
                    self.assertEqual(code, 0)
                    self.assertEqual(out, {"made": name, "ok": True})
                    self.assertEqual(seen, [{"x": 1}])

    def test_a_refusal_comes_back_the_same_through_either_spelling(self):
        def refuse(body):
            raise hand.Refused("remake takes a JSON object", rule="body")

        for argv in (["remake"], ["remake", "render"]):
            with self.subTest(argv=argv), mock.patch.object(remakes, "generate_remake", refuse):
                self.assertEqual(run(argv), (2, {"ok": False, "refused": "remake takes a JSON object", "rule": "body"}))

    def test_list_and_get_still_read_the_ledger(self):
        with mock.patch.object(assets, "listing", lambda kind, limit: {"kind": kind, "limit": limit}):
            self.assertEqual(run(["list", "--max", "3"]), (0, {"kind": "video", "limit": 3, "ok": True}))
        with mock.patch.object(assets, "get", lambda asset_id: {"id": asset_id}):
            self.assertEqual(run(["get", "7"]), (0, {"id": 7, "ok": True}))


class BadStepTest(unittest.TestCase):
    def test_a_flavor_without_a_default_needs_a_step(self):
        self.assertEqual(run(["clip"]), (1, {"ok": False, "error": "hands/video needs a verb"}))

    def test_an_unknown_step_is_bad_arguments(self):
        self.assertEqual(run(["remake", "bogus"]), (1, {"ok": False, "error": "bad arguments to hands/video"}))

    def test_an_unknown_flavor_is_bad_arguments(self):
        self.assertEqual(run(["khaby", "keys"]), (1, {"ok": False, "error": "bad arguments to hands/video"}))


class ImageTest(unittest.TestCase):
    def test_hands_image_keeps_its_verbs_and_has_no_flavors(self):
        self.assertEqual(cli.parser("image").format_usage(), "usage: hands/image [-h] {generate,list,get,scenes} ...\n")
        for argv in (["flavors"], ["skit"], ["clip", "make"]):
            with self.subTest(argv=argv):
                self.assertEqual(run(argv, kind="image"), (1, {"ok": False, "error": "bad arguments to hands/image"}))

    def test_hands_image_generate_still_makes_an_image(self):
        seen = []
        with mock.patch.object(assets, "generate_image", lambda body: seen.append(body) or {"made": "image"}):
            self.assertEqual(run(["generate"], {"prompt": "p"}, kind="image"), (0, {"made": "image", "ok": True}))
        self.assertEqual(seen, [{"prompt": "p"}])


class NewFlavorTest(unittest.TestCase):
    def test_a_registered_flavor_gets_its_verbs_listing_and_cap(self):
        seen = []
        demo = flavors.Flavor(
            "demo", "a throwaway flavor",
            (flavors.Step("go", lambda a, body: seen.append(body) or {"went": True}, "go (JSON on stdin)", True),),
            ("demo",), True,
        )
        registry = {**flavors.REGISTRY, "demo": demo}
        self.assertEqual(run(["demo", "go"], {"y": 2}, registry=registry), (0, {"went": True, "ok": True}))
        self.assertEqual(seen, [{"y": 2}])
        self.assertEqual(run(["demo"], registry=registry), (1, {"ok": False, "error": "hands/video needs a verb"}))
        self.assertIn("demo", [f["name"] for f in flavors.describe(registry)["flavors"]])
        self.assertEqual(flavors.video_kinds(registry), ("skit", "remake", "demo"))
        self.assertNotIn("demo", flavors.REGISTRY)
        self.assertEqual(run(["demo", "go"]), (1, {"ok": False, "error": "bad arguments to hands/video"}))


if __name__ == "__main__":
    unittest.main()
