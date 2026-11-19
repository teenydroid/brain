"""bin/lib/model_window.py: a model a turn asks for gets its window in the
core's registry first, from the endpoint's listing, with nothing already in
the registry lost (the studio preview of 2026-11-19, refused "messages: at
least one message is required" on two models the core had no window for)."""

from __future__ import annotations

import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "bin" / "lib"))
import model_window  # noqa: E402

LISTING = {"data": [{"id": "glm-5.3-flash", "context_length": 1048576},
                    {"id": "claude-fable-5.2", "context_length": 1000000},
                    {"id": "claude-opus-5.5", "context_window": 1000000},
                    {"id": "long-name", "aliases": ["short"], "context_length": 200000},
                    {"id": "no-window"}]}
GLM = {"id": "glm-5.3-flash", "provider": "local-openai", "context_window": 1048576, "vision": False,
       "cost_per_1m_input": 0.1}


class FakeCore:
    def __init__(self, registry):
        self.registry, self.calls = [dict(e) for e in registry], []

    def __call__(self, method, params):
        self.calls.append(method)
        if method == "get_config":
            return {"config": {"model_registry": self.registry}}
        if method == "inference_update_model_settings":
            self.registry = params["model_registry"]
            return {}
        raise AssertionError(method)


class EnsureTest(unittest.TestCase):
    def setUp(self):
        self.fetched = 0

    def listing(self):
        self.fetched += 1
        return LISTING

    def test_a_model_with_no_window_gets_the_endpoints_and_the_rest_is_kept(self):
        core = FakeCore([GLM])
        out = model_window.ensure(["claude-fable-5.2", "claude-opus-5.5"], core, self.listing)
        self.assertEqual(out, {"added": {"claude-fable-5.2": 1000000, "claude-opus-5.5": 1000000}, "unknown": []})
        self.assertEqual(core.registry[0], GLM)
        self.assertEqual([(e["id"], e["provider"], e["context_window"]) for e in core.registry[1:]],
                         [("claude-fable-5.2", "local-openai", 1000000), ("claude-opus-5.5", "local-openai", 1000000)])

    def test_a_model_the_core_knows_costs_one_read_and_no_listing(self):
        core = FakeCore([GLM])
        self.assertEqual(model_window.ensure(["glm-5.3-flash", "", "glm-5.3-flash"], core, self.listing),
                         {"added": {}, "unknown": []})
        self.assertEqual((core.calls, self.fetched), (["get_config"], 0))

    def test_no_model_asks_nothing(self):
        core = FakeCore([GLM])
        model_window.ensure(["", "  "], core, self.listing)
        self.assertEqual(core.calls, [])

    def test_an_entry_with_a_zero_window_is_given_one_in_place(self):
        core = FakeCore([GLM, {"id": "claude-fable-5.2", "provider": "local-openai", "context_window": 0, "vision": True}])
        model_window.ensure(["claude-fable-5.2"], core, self.listing)
        self.assertEqual(core.registry[1], {"id": "claude-fable-5.2", "provider": "local-openai",
                                            "context_window": 1000000, "vision": True})
        self.assertEqual(len(core.registry), 2)

    def test_an_alias_is_found(self):
        core = FakeCore([])
        self.assertEqual(model_window.ensure(["short"], core, self.listing)["added"], {"short": 200000})

    def test_a_model_not_listed_or_listed_without_a_window_is_unknown_and_nothing_is_written(self):
        core = FakeCore([GLM])
        out = model_window.ensure(["nowhere", "no-window"], core, self.listing)
        self.assertEqual(out, {"added": {}, "unknown": ["nowhere", "no-window"]})
        self.assertNotIn("inference_update_model_settings", core.calls)

    def test_a_listing_that_cannot_be_read_is_unknown_not_a_crash(self):
        def broken():
            raise OSError("refused")
        out = model_window.ensure(["claude-fable-5.2"], FakeCore([GLM]), broken)
        self.assertEqual(out["unknown"], ["claude-fable-5.2"])
        self.assertIn("OSError", out["listing_error"])

    def test_a_config_without_a_registry_is_an_error(self):
        with self.assertRaises(model_window.WindowError):
            model_window.ensure(["x"], lambda m, p: {"config": {}}, self.listing)


class CommandTest(unittest.TestCase):
    """`model_window.py ensure`, as bin/ask runs it: against bin/oh (here a
    stand-in) and LADDER_URL from secrets/spine.env, exit 0 whatever."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))
        (self.tmp / "ladder").mkdir()
        (self.tmp / "ladder" / "models").write_text(json.dumps(LISTING))
        (self.tmp / "secrets").mkdir()
        (self.tmp / "secrets" / "spine.env").write_text(f"LADDER_URL=file://{self.tmp / 'ladder'}\n")
        (self.tmp / "registry.json").write_text(json.dumps([GLM]))
        oh = self.tmp / "oh"
        oh.write_text("#!/usr/bin/env bash\n"
                      f"reg={self.tmp / 'registry.json'}\n"
                      'case "$1" in\n'
                      '  get_config) jq -cn --slurpfile r "$reg" \'{config: {model_registry: $r[0]}}\' ;;\n'
                      '  inference_update_model_settings) jq -c .model_registry <<<"$2" >"$reg"; echo "{}" ;;\n'
                      '  *) exit 1 ;;\n'
                      'esac\n')
        oh.chmod(oh.stat().st_mode | stat.S_IEXEC)
        self.env = {"PATH": os.environ["PATH"], "TEENY_OH": str(oh), "TEENY_SECRETS": str(self.tmp / "secrets"),
                    "TEENY_STATE": str(self.tmp / "state")}

    def test_ensure_on_core_registers_from_the_spine_envs_ladder(self):
        out = model_window.ensure_on_core(["claude-opus-5.5"], self.env)
        self.assertEqual(out, {"added": {"claude-opus-5.5": 1000000}, "unknown": []})
        ids = [e["id"] for e in json.loads((self.tmp / "registry.json").read_text())]
        self.assertEqual(ids, ["glm-5.3-flash", "claude-opus-5.5"])

    def test_a_core_that_cannot_be_read_is_an_error_in_the_answer(self):
        out = model_window.ensure_on_core(["claude-opus-5.5"], {**self.env, "TEENY_OH": str(self.tmp / "missing")})
        self.assertEqual((out["added"], out["unknown"]), ({}, []))
        self.assertIn("get_config", out["error"])

    def test_no_ladder_url_is_unknown(self):
        (self.tmp / "secrets" / "spine.env").write_text("")
        out = model_window.ensure_on_core(["claude-opus-5.5"], self.env)
        self.assertEqual(out["unknown"], ["claude-opus-5.5"])


if __name__ == "__main__":
    unittest.main()
