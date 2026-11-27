// A port of hands/tests/test_runway.py: the days the wallet's USDC lasts at
// this week's burn, as one `health · runway` stimulus a day and a line in the
// mind's beat header, and the tier from the frozen evals/runway.toml that cuts
// optional spend when it is low. Off unless RUNWAY=1. The wallet is faked
// (the Python faked the core and the chain under it); nothing is signed.
//
// Not ported here, because they test Python consumers that are not ported:
// test_no_picture_is_made_on_a_low_tier (hands/image's media/assets.py),
// test_the_mind_is_told_why (bin/lib/rhythm.py, whose line is `headerLine`'s,
// pinned below) and the two bin/health runway job tests. Those stay in Python.

import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import * as hand from "../../../src/hands/lib/hand.js";
import * as runway from "../../../src/hands/lib/runway.js";
import * as stimuli from "../../../src/hands/lib/stimuli.js";
import { T0, scratch } from "./support.js";

const DAY = 86400;
const s = scratch();

interface Fake {
  held: number;
  weekUsd: number | null;
  unpriced: number;
}
let fake: Fake;

beforeEach(() => {
  s.secrets("RUNWAY=1\n");
  fake = { held: 1000, weekUsd: 70, unpriced: 0 };
  runway.setWalletReads({
    balance: () => ({ usdc: { raw: String(Math.round(fake.held * 1e6)) }, evm: { networks: {} } }),
    costParts: () => {
      if (fake.weekUsd === null) throw new hand.HandError("cost_report: core unreachable");
      return {
        complete: fake.unpriced === 0,
        models: { micro: Math.round(fake.weekUsd * 1e6) },
        x402: { micro: 0 },
        bills: [],
      };
    },
  });
});
afterEach(() => runway.setWalletReads(null));

const inbox = () => stimuli.recent().stimuli.filter((x) => x.surface === "health" && x.kind === "runway");

describe("the tiers", () => {
  it("parse from the frozen file, and the lowest one under applies", () => {
    const table = runway.tiers();
    expect(table.map((t) => t.name)).toEqual(["critical", "low"]);
    expect(runway.tierFor(3, table)?.name).toBe("critical");
    expect(runway.tierFor(10, table)?.name).toBe("low");
    expect(runway.tierFor(100, table)).toBeNull();
    expect(runway.tierFor(null, table)).toBeNull();
  });

  it("fail closed on a bad file", () => {
    const bad = join(mkdtempSync(join(tmpdir(), "tiers-")), "runway.toml");
    writeFileSync(bad, "[tiers.low]\nbelow_days = 0\n");
    expect(() => runway.tiers(bad)).toThrow(hand.HandError);
    writeFileSync(bad, "[tiers.low]\nbelow_days = 5\nmedia = 'no'\n");
    expect(() => runway.tiers(bad)).toThrow(hand.HandError);
    writeFileSync(bad, "[tiers.low]\nbelow_days = 5\nevolve_candidates = 0\n");
    expect(() => runway.tiers(bad)).toThrow(hand.HandError);
    writeFileSync(bad, "not toml [");
    expect(() => runway.tiers(bad)).toThrow();
  });
});

describe("the daily sense", () => {
  it("files one stimulus and cuts optional spend on a low runway", async () => {
    fake.held = 100; // 100 USDC at 10 a day: 10 days, the low tier
    const out = await runway.daily();
    expect([out.days, out.tier, out.filed]).toEqual([10, "low", true]);
    const [got] = inbox();
    expect(got?.text).toContain("100.00 USDC");
    expect(got?.text).toContain("10 days at this rate");
    expect(got?.text).toContain("no pictures or clips");
    expect((await runway.daily()).filed).toBe(false); // once a day
    const lim = runway.limits();
    expect([lim.media, lim.evolve_candidates]).toEqual([false, 2]);
    expect(runway.capCandidates(4)).toEqual([2, "low tier (runway 10 days, under 30): 2 candidate(s), not 4"]);
    expect(runway.capCandidates(1)).toEqual([1, null]);
  });

  it("writes state/runway.json as the Python did", async () => {
    fake.held = 100;
    await runway.daily();
    const text = (await import("node:fs")).readFileSync(join(s.root, "state", "runway.json"), "utf8");
    expect(JSON.parse(text)).toEqual({ at: T0, burn_micro_per_day: 10_000_000, days: 10, tier: "low", usdc_micro: 100_000_000 });
    expect(text).toBe('{"at": 1789369200, "burn_micro_per_day": 10000000, "days": 10, "tier": "low", "usdc_micro": 100000000}');
  });

  it("allows one candidate below the critical tier", async () => {
    fake.held = 30;
    await runway.daily();
    expect(runway.capCandidates(4)[0]).toBe(1);
  });

  it("cuts nothing on a long runway", async () => {
    fake.held = 1000;
    const out = await runway.daily();
    expect(out.tier).toBeNull();
    expect(runway.limits().media).toBe(true);
    expect(runway.capCandidates(4)).toEqual([4, null]);
    expect(inbox()[0]?.text).toContain("nothing is cut");
  });

  it("counts an unread cost as not measured, and unmeasured as the low tier", async () => {
    fake.weekUsd = null;
    const out = await runway.daily();
    expect(out.measured).toBe(false);
    expect(runway.limits().tier).toBe("low");
    expect(inbox()[0]?.text).toContain("could not be measured");
  });

  it("does not take unpriced calls for a cheap week", async () => {
    fake.weekUsd = 0;
    fake.unpriced = 10;
    const out = await runway.daily();
    expect(out.measured).toBe(false);
    expect(runway.limits().tier).toBe("low");
    expect(inbox()[0]?.text).not.toContain("nothing cost money");
  });

  it("says nothing runs down when nothing cost money", async () => {
    fake.weekUsd = 0;
    const out = await runway.daily();
    expect([out.measured, out.days, out.tier]).toEqual([true, null, null]);
    expect(inbox()[0]?.text).toContain("nothing cost money this week");
  });

  it("counts a stale measure as the low tier", async () => {
    await runway.daily();
    expect(runway.limits().tier).toBeNull();
    s.at((runway.STALE_DAYS + 1) * DAY);
    expect(runway.limits().tier).toBe("low");
  });

  it("is off by default: nothing is measured or cut", async () => {
    fake.held = 1;
    await runway.daily();
    s.secrets("");
    expect(String((await runway.daily()).skipped)).toContain("RUNWAY");
    expect(runway.limits().media).toBe(true);
    expect(runway.capCandidates(4)).toEqual([4, null]);
    expect(runway.headerLine()).toBeNull();
  });
});

describe("what the tier reaches", () => {
  it("tells the mind why, in the line its beat header carries", async () => {
    fake.held = 100;
    await runway.daily();
    expect(runway.headerLine()).toBe(
      "Runway: runway 10 days, under 30. You are on the low tier: no pictures or clips, at most 2 evolve candidate(s) a generation.",
    );
  });

  it("says the days when no tier applies", async () => {
    await runway.daily();
    expect(runway.headerLine()).toBe("Runway: 100 days at the current burn.");
  });

  it("does not measure without the wallet's reads", async () => {
    runway.setWalletReads(null);
    const out = await runway.daily();
    expect(out.measured).toBe(false);
    expect(runway.limits().tier).toBe("low");
  });
});
