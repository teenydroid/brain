// hands/lib/teeny_hands/tags.py has no test of its own in Python (every
// writing hand's tests go through it); these pin its rules.

import { describe, expect, it } from "vitest";
import { HandError } from "../../../src/hands/lib/hand.js";
import { COLUMNS, split } from "../../../src/hands/lib/tags.js";

describe("split", () => {
  it("takes format and experiment out of the body and leaves the rest", () => {
    const [rest, tags] = split({ text: "gm", format: " GM ", experiment: 4 });
    expect(rest).toEqual({ text: "gm" });
    expect(tags).toEqual({ format: "gm", experiment: 4 });
  });

  it("reads an experiment given as digits", () => {
    expect(split({ experiment: " 12 " })[1].experiment).toBe(12);
  });

  it("gives null for absent tags and leaves a body that is not an object alone", () => {
    expect(split({ text: "x" })).toEqual([{ text: "x" }, { format: null, experiment: null }]);
    expect(split("text")).toEqual(["text", { format: null, experiment: null }]);
    expect(split(null)).toEqual([null, { format: null, experiment: null }]);
    expect(split([1])).toEqual([[1], { format: null, experiment: null }]);
  });

  it("refuses a bad format", () => {
    for (const bad of ["1gm", "has space", "x".repeat(33), "", 7, ["gm"]]) {
      expect(() => split({ format: bad }), String(bad)).toThrow(HandError);
    }
    expect(() => split({ format: "x".repeat(32) })).not.toThrow();
    expect(() => split({ format: "a-b_c9" })).not.toThrow();
  });

  it("refuses a bad experiment", () => {
    for (const bad of [0, -1, 1.5, "abc", "0", true, "-3", [1]]) {
      expect(() => split({ experiment: bad }), String(bad)).toThrow(/lab experiment id/);
    }
  });

  it("names the columns the ledgers keep", () => {
    expect(COLUMNS).toEqual({ format: "TEXT", experiment: "INTEGER" });
  });
});
