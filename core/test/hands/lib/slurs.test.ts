// A port of hands/tests/test_slurs.py: the one screen the studio's outside
// material faces, the guard lexicon's `hate` category, matched as the guard's
// screen matches. Plus a check against the Python lexicon itself while it
// exists, so the copied term list cannot drift.

import { spawnSync } from "node:child_process";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { LEXICON } from "../../../src/hands/lib/guard/lexicon.js";
import { screenText } from "../../../src/hands/lib/guard/screen.js";
import { hateful } from "../../../src/hands/lib/slurs.js";
import { REPO, pythonAvailable } from "./support.js";

describe("hateful", () => {
  it("catches a slur however it is written", () => {
    for (const text of ["what a retard", "RETARDED take", "r3tard", "the nazis were right", "white power forever"]) {
      expect(hateful(text), text).toBe(true);
    }
  });

  it("takes any of several texts as enough", () => {
    expect(hateful("a fine headline", "", "a quote calling them subhuman")).toBe(true);
    expect(hateful("a fine headline", "a fine quote")).toBe(false);
  });

  it("does not take the rest of the lexicon for a slur", () => {
    for (const text of [
      "this fucking robot", "porn site sues an AI company", "kill the product line",
      "a gun maker's app leaked", "cocaine fuelled launch party", "go kill yourself",
    ]) {
      expect(hateful(text), text).toBe(false);
    }
  });

  it("does not take a word inside another word for a slur", () => {
    for (const text of ["a spicy take", "the nazareth office", "spectacular retardant foam"]) {
      expect(hateful(text), text).toBe(false);
    }
  });

  it("does not take nothing, or not text, for a slur", () => {
    expect(hateful()).toBe(false);
    expect(hateful("", "   ", null)).toBe(false);
    expect(hateful(undefined, 7, {})).toBe(false);
  });

  it("reads the guard's own hate terms", () => {
    const hate = LEXICON.filter((e) => e.category === "hate").flatMap((e) => [...e.terms]);
    expect(hate).toContain("subhuman");
    for (const t of [...hate].sort()) expect(hateful(`they said ${t} on air`), t).toBe(true);
  });
});

describe("the screen", () => {
  it("holds sex aimed at a child or a family member, and not swearing alone", () => {
    expect(screenText("every father wants to fuck his daughter", "inbound")?.category).toBe("sexual");
    expect(screenText("fucking kids these days", "inbound")).toBeNull();
    expect(screenText("my dad is fucking furious", "outbound")).toBeNull();
    expect(screenText("my son's homework", "outbound")).toBeNull();
  });

  it("prefers the longest term and reports where it came from", () => {
    expect(screenText("white supremacist rally", "outbound")).toMatchObject({ category: "hate", term: "white supremacist" });
    expect(() => screenText("x", "sideways")).toThrow(/unknown direction/);
    expect(screenText("   ", "outbound")).toBeNull();
  });
});

describe.runIf(pythonAvailable())("against the Python lexicon and screen", () => {
  const SAMPLES = [
    "what a retard", "r3tard", "fuuuck the nazis", "n.a.z.i", "1488", "k k k", "ｎａｚｉ", "white-power", "white   power",
    "a spicy take", "x.com", "xxx", "heil hitler!", "every father wants to fuck his daughter", "molested a child", "my son's homework",
    "attracted to his daughter", "sex with a minor", "nudes of kids", "fucking kids these days", "the ​nazis", "race war now", "gooks",
    "niggaz", "tranny!", "neo nazi", "TOWELHEAD", "dyke.", "chinks", "k​kk",
  ];
  const script = `
import json, sys
sys.path.insert(0, ${JSON.stringify(join(REPO, "hands", "lib"))})
from teeny_hands import slurs
from teeny_hands.guard.screen import screen_text
from teeny_hands.guard.lexicon import LEXICON
samples = json.load(sys.stdin)
print(json.dumps({"hate": sorted(t for e in LEXICON if e.category == "hate" for t in e.terms),
  "entries": [e.category for e in LEXICON],
  "slurs": [slurs.hateful(s) for s in samples],
  "screen": [None if (m := screen_text(s, "inbound")) is None else [m.category, m.term, m.judged] for s in samples]}))
`;
  const py = spawnSync("python3", ["-I", "-c", script], { input: JSON.stringify(SAMPLES), encoding: "utf8" });
  const want = JSON.parse(py.stdout || "{}") as { hate: string[]; entries: string[]; slurs: boolean[]; screen: ([string, string, boolean] | null)[] };

  it("lists the same entries and terms", () => {
    expect(py.status, py.stderr).toBe(0);
    expect(LEXICON.map((e) => e.category)).toEqual(want.entries);
    expect(LEXICON.filter((e) => e.category === "hate").flatMap((e) => [...e.terms]).sort()).toEqual(want.hate);
  });

  SAMPLES.forEach((text, i) => {
    it(`reads ${JSON.stringify(text)} as the Python does`, () => {
      expect(hateful(text)).toBe(want.slurs[i]);
      const got = screenText(text, "inbound");
      expect(got === null ? null : [got.category, got.term, got.judged]).toEqual(want.screen[i]);
    });
  });
});
