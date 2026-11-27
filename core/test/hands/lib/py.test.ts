// The Python behaviours the framework borrows (json.dumps, int(), repr, scrub,
// the cache key), checked against Python itself where it is 3.11, and against
// written-down expectations where it is not.

import { spawnSync } from "node:child_process";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { codePointOrder, dumps, pyFloat, pyInt, pyRepr, pySplit, pyStrip } from "../../../src/hands/lib/py.js";
import { ReadCache } from "../../../src/hands/lib/readcache.js";
import { scrub } from "../../../src/hands/lib/scrub.js";
import * as hand from "../../../src/hands/lib/hand.js";
import { REPO, pythonAvailable, scratch } from "./support.js";

describe("dumps", () => {
  it("spaces as json.dumps does and sorts by code point", () => {
    expect(dumps({ b: 1, a: [1, { d: null, c: true }], é: "x" }, { sortKeys: true })).toBe('{"a": [1, {"c": true, "d": null}], "b": 1, "\\u00e9": "x"}');
    expect(dumps({ "𝄞": 1, "￿": 2 }, { sortKeys: true, ensureAscii: false })).toBe('{"￿": 2, "𝄞": 1}');
    expect(codePointOrder("￿", "𝄞")).toBeLessThan(0);
  });

  it("escapes non-ASCII only when asked", () => {
    expect(dumps("é\n😀")).toBe('"\\u00e9\\n\\ud83d\\ude00"');
    expect(dumps("é\n😀", { ensureAscii: false })).toBe('"é\\n😀"');
  });

  it("leaves out undefined keys and uses the fallback for what JSON cannot hold", () => {
    expect(dumps({ a: undefined, b: 1 })).toBe('{"b": 1}');
    expect(dumps([new Date(0)], { fallback: String })).toContain("1970");
  });
});

describe("int, float, strip, repr", () => {
  it("reads int() as Python reads it", () => {
    expect(pyInt(" 5 ")).toBe(5);
    expect(pyInt("+5")).toBe(5);
    expect(pyInt("-5")).toBe(-5);
    expect(pyInt("1_000")).toBe(1000);
    for (const bad of ["5.0", "", " ", "1e3", "1__0", "_1", "0x10", "five"]) expect(pyInt(bad), bad).toBeNull();
  });

  it("reads float() as Python reads it", () => {
    expect(pyFloat("1.5")).toBe(1.5);
    expect(pyFloat(" 2 ")).toBe(2);
    expect(pyFloat("1e3")).toBe(1000);
    expect(pyFloat("inf")).toBe(Infinity);
    expect(pyFloat(".5")).toBe(0.5);
    for (const bad of ["", "e3", "1.2.3", "abc", "."]) expect(pyFloat(bad), bad).toBeNull();
  });

  it("splits and strips on Python's whitespace", () => {
    expect(pySplit("  a \t b\n\x1cc d ")).toEqual(["a", "b", "c", "d"]);
    expect(pyStrip("﻿x﻿")).toBe("﻿x﻿");
    expect(pyStrip(" \x1fx　")).toBe("x");
  });

  it("quotes as repr does", () => {
    expect(pyRepr("it's")).toBe('"it\'s"');
    expect(pyRepr("a'b\"c")).toBe("'a\\'b\"c'");
    expect(pyRepr("x")).toBe("'x'");
    expect(pyRepr(5)).toBe("5");
    expect(pyRepr(null)).toBe("None");
  });
});

describe.runIf(pythonAvailable())("scrub and the cache key against the Python", () => {
  const SCRUBS: string[] = [
    "see https://example.com/a?b=c now", "ftp://x.y/z", "host 10.0.0.1:8080 and 192.168.1.1.", "key sk-abcdef1234 and ghp_ABCDEFG",
    "AKIAABCDEFGH", "a".repeat(40), "plain   words\nover\tlines", "x".repeat(400), "é".repeat(310), "word " .repeat(80),
    "xoxb-123456 and github_pat_abcdef", "1.2.3.4.5", "http://", "ünïcode https://exämple.com/ü ok", "tail   ", "a b c",
    "7" + "b".repeat(32) + "é",
  ];
  const KEYS: [string, unknown[]][] = [["story", ["top", 10]], ["item", ["42"]], ["search", ["é q", null, 5, true, 1.5]], ["x", [{ a: 1, b: [2] }]]];
  const script = `
import json, sys
sys.path.insert(0, ${JSON.stringify(join(REPO, "hands", "lib"))})
from teeny_hands import hand, scrub, readcache
data = json.load(sys.stdin)
cache = readcache.ReadCache("t_cache", "KEY", {"a": 1}, "ON")
print(json.dumps({"scrub": [scrub.scrub(t) for t in data["scrubs"]], "short": [scrub.scrub(t, 20) for t in data["scrubs"]],
  "keys": [cache._key(k, tuple(parts)) for k, parts in data["keys"]]}))
`;
  scratch();
  it("scrubs the same line", () => {
    const py = spawnSync("python3", ["-I", "-c", script], { input: JSON.stringify({ scrubs: SCRUBS, keys: KEYS }), encoding: "utf8", env: { ...process.env, TEENY_ROOT: "/nonexistent-root" } });
    expect(py.status, py.stderr).toBe(0);
    const want = JSON.parse(py.stdout) as { scrub: string[]; short: string[]; keys: string[] };
    expect(SCRUBS.map((t) => scrub(t))).toEqual(want.scrub);
    expect(SCRUBS.map((t) => scrub(t, 20))).toEqual(want.short);
    hand.resetSecrets();
    const cache = new ReadCache("t_cache", "KEY", { a: 1 }, "ON");
    const key = (k: string, parts: unknown[]): string => (cache as unknown as { key(k: string, p: unknown[]): string }).key(k, parts);
    expect(KEYS.map(([k, parts]) => key(k, parts))).toEqual(want.keys);
  });
});

describe("scrub", () => {
  it("writes down what it promises", () => {
    expect(scrub("see https://example.com/a?b=c now")).toBe("see [url] now");
    expect(scrub("at 10.0.0.1:8080 ok")).toBe("at [host] ok");
    expect(scrub("key sk-abcdef1234 here")).toBe("key [redacted] here");
    expect(scrub("a   b\n c")).toBe("a b c");
    expect(scrub("x".repeat(400))).toBe("[redacted]");
    expect(scrub("ab ".repeat(200), 10)).toBe("ab ab ab a...");
    expect(scrub("word ".repeat(80)).endsWith("...")).toBe(true);
  });
});
