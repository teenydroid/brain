// A port of hands/tests/test_stimuli.py (InboxTest and FollowUpTest). The
// Python CliTest drives hands/stimuli as a subprocess and stays in Python with
// that hand, running against whatever hands/stimuli is.

import { describe, expect, it } from "vitest";
import * as hand from "../../../src/hands/lib/hand.js";
import * as stimuli from "../../../src/hands/lib/stimuli.js";
import { T0, scratch } from "./support.js";

const s = scratch();
const refs = (items: Record<string, unknown>[]): unknown[] => items.map((x) => x.ref);
const take = (maxItems: number, maxChars: number, leave: [string, string][] = []) => stimuli.take(maxItems, maxChars, leave);
const stimuliOf = (batch: Record<string, unknown>) => batch.stimuli as Record<string, unknown>[];

describe("the inbox", () => {
  it("lands the same event once", () => {
    expect(stimuli.add("x", "mention", "111", "gm teeny", "ana")).toBe(true);
    expect(stimuli.add("x", "mention", "111", "gm teeny again", "ana")).toBe(false);
    expect(stimuli.counts().pending).toBe(1);
  });

  it("has the health check as a surface", () => {
    expect(stimuli.add("health", "alarm", "outage:1", "your model is not answering")).toBe(true);
  });

  it("has the operator on the host as a surface", () => {
    expect(stimuli.add("host", "instruction", "host-1-ab", "tweet that", "operator")).toBe(true);
  });

  it("has hacker news as a surface", () => {
    expect(stimuli.add("hn", "comment", "42", "nice work", "pg")).toBe(true);
    expect(stimuli.bareRef("hn:42")).toBe("42");
  });

  it("treats one ref on two surfaces as two events", () => {
    stimuli.add("x", "mention", "7", "a");
    stimuli.add("moltbook", "comment", "7", "b");
    expect(stimuli.counts().pending).toBe(2);
  });

  it("drains in arrival order and marks the batch", () => {
    stimuli.add("x", "mention", "1", "first");
    s.at(10);
    stimuli.add("moltbook", "comment", "c1", "second", "crab");
    const batch = take(10, 10_000);
    expect(stimuliOf(batch).map((x) => x.text)).toEqual(["first", "second"]);
    expect(stimuliOf(batch)[1]?.who).toBe("crab");
    expect(batch.tick).toBeTruthy();
    expect(stimuli.counts()).toEqual({ pending: 0, taken: 2, done: 0, filed: 0 });
    // A second tick while the first is still out sees nothing new.
    expect(stimuliOf(take(10, 10_000))).toEqual([]);
  });

  it("takes a hand outcome before older waiting stimuli", () => {
    // Live, 2026-11-19: comments owed since the day before, offered again in
    // their old place, filled every batch, and the outcomes of the beat that
    // answered them never reached the mind.
    for (let n = 0; n < 5; n++) stimuli.add("moltbook", "comment", `c${n}`, `old ${n}`);
    s.at(600);
    stimuli.add("hand", "outcome", "t:0", "your comment_moltbook: refused, not sent");
    expect(refs(stimuliOf(take(3, 10_000)))).toEqual(["t:0", "c0", "c1"]);
  });

  it("leaves what it is told to pending in its place", () => {
    stimuli.add("moltbook", "comment", "c1", "owed an answer");
    s.at(10);
    stimuli.add("x", "mention", "1", "gm");
    const batch = take(10, 10_000, [["moltbook", "comment"]]);
    expect(refs(stimuliOf(batch))).toEqual(["1"]);
    expect(refs(stimuli.pending())).toEqual(["c1"]);
    expect(refs(stimuliOf(take(10, 10_000)))).toEqual(["c1"]);
  });

  it("stops at the item cap", () => {
    for (let n = 0; n < 5; n++) stimuli.add("x", "mention", String(n), `m${n}`);
    expect(stimuliOf(take(3, 10_000))).toHaveLength(3);
    expect(stimuli.counts().pending).toBe(2);
  });

  it("stops at the character cap but always takes one", () => {
    stimuli.add("x", "mention", "big", "x".repeat(5000));
    stimuli.add("x", "mention", "next", "y".repeat(10));
    const batch = take(10, 100);
    expect(refs(stimuliOf(batch))).toEqual(["big"]);
    expect((stimuliOf(batch)[0]?.text as string).length).toBeLessThanOrEqual(stimuli.MAX_TEXT_CHARS + 20);
  });

  it("keeps more of a nudge's text than of anything else", () => {
    // A habit's nudge carries its playbook's inputs (bin/act): cut at the
    // usual length, the mind would get half a JSON object.
    stimuli.add("nudge", "world-scan", "world-scan:1", "n".repeat(12_000));
    stimuli.add("nudge", "world-scan", "world-scan:2", "n".repeat(40_000));
    stimuli.add("x", "mention", "big", "x".repeat(12_000));
    const byRef = Object.fromEntries(stimuliOf(take(10, 200_000)).map((x) => [x.ref as string, x.text as string]));
    expect(byRef["world-scan:1"]).toHaveLength(12_000);
    expect(byRef["world-scan:2"]!.length).toBeLessThanOrEqual(stimuli.NUDGE_TEXT_CHARS + 20);
    expect(byRef.big!.length).toBeLessThanOrEqual(stimuli.MAX_TEXT_CHARS + 20);
  });

  it("can be brief enough for a playbook input", () => {
    stimuli.add("nudge", "world-scan", "world-scan:1", "n".repeat(9000), null, { playbook: "world-scan" });
    stimuli.add("x", "mention", "7", "gm teeny", "ana");
    const full = stimuli.recent(10).stimuli;
    expect((full.find((x) => x.ref === "world-scan:1")?.text as string).length).toBe(9000);
    const brief = Object.fromEntries(stimuli.recent(10, 300).stimuli.map((x) => [x.ref as string, x]));
    expect((brief["world-scan:1"]!.text as string).length).toBe(300);
    expect(brief["world-scan:1"]).not.toHaveProperty("payload");
    expect([brief["7"]!.text, brief["7"]!.who, brief["7"]!.status]).toEqual(["gm teeny", "ana", "pending"]);
  });

  it("puts a failed tick's batch back", () => {
    stimuli.add("x", "mention", "1", "first");
    const tick = take(10, 10_000).tick as string;
    stimuli.release(tick);
    expect(refs(stimuliOf(take(10, 10_000)))).toEqual(["1"]);
  });

  it("makes a settled tick done for good", () => {
    stimuli.add("x", "mention", "1", "first");
    const tick = take(10, 10_000).tick as string;
    stimuli.settle(tick);
    expect(stimuli.counts()).toEqual({ pending: 0, taken: 0, done: 1, filed: 0 });
    expect(stimuli.add("x", "mention", "1", "first")).toBe(false);
  });

  it("takes a tick that died back after an hour", () => {
    stimuli.add("x", "mention", "1", "first");
    take(10, 10_000);
    s.at(30 * 60);
    expect(stimuliOf(take(10, 10_000))).toEqual([]);
    s.at(61 * 60);
    expect(stimuliOf(take(10, 10_000))).toHaveLength(1);
  });

  it("round-trips a payload", () => {
    stimuli.add("gh", "comment", "teenydroid/x#4", "nice", null, { repo: "teenydroid/x", number: 4 });
    expect(stimuliOf(take(1, 1000))[0]?.payload).toEqual({ repo: "teenydroid/x", number: 4 });
  });

  it("refuses unknown surfaces", () => {
    expect(() => stimuli.add("myspace", "poke", "1", "hi")).toThrow(hand.HandError);
  });

  it("files a studio pitch, never to be taken", () => {
    stimuli.add("studio", "pitch", "p1", "an idea");
    expect(stimuli.counts()).toEqual({ pending: 0, taken: 0, done: 0, filed: 1 });
    expect(stimuliOf(take(10, 10_000))).toEqual([]);
  });

  it("puts a settled stimulus back with new details, and only a settled one", () => {
    stimuli.add("moltbook", "comment", "c1", "owed");
    expect(stimuli.again("moltbook", "comment", "c1", { n: 1 })).toBe(false);
    stimuli.settle(take(10, 10_000).tick as string);
    expect(stimuli.again("moltbook", "comment", "c1", { n: 2 })).toBe(true);
    expect(stimuli.pending()[0]?.payload).toEqual({ n: 2 });
  });

  it("keeps notes by who, case-blind", () => {
    stimuli.note("remember this", "Ana");
    stimuli.note("and this");
    expect(stimuli.notes(0, "ana").notes).toHaveLength(1);
    expect(stimuli.notes().notes).toHaveLength(2);
    expect(() => stimuli.note("  ")).toThrow(hand.HandError);
  });

  it("writes the ledger in the schema the Python wrote", () => {
    stimuli.add("x", "mention", "1", "gm");
    const conn = hand.db();
    expect(conn.all("PRAGMA table_info(stimuli)").map((r) => r.name)).toEqual([
      "id", "surface", "kind", "ref", "who", "text", "payload", "at", "status", "tick", "taken_at", "updated_at",
    ]);
    expect(conn.get("SELECT id, payload, at, status FROM stimuli")).toEqual({ id: "x:mention:1", payload: "{}", at: T0, status: "pending" });
    conn.close();
  });
});

describe("follow-ups", () => {
  it("arrive when they come due", () => {
    stimuli.followUp(T0 + 3600, "check whether ana's cli shipped", "x:111");
    expect(stimuli.raiseDue()).toBe(0);
    s.at(3601);
    expect(stimuli.raiseDue()).toBe(1);
    const batch = stimuliOf(take(10, 10_000));
    expect([batch[0]?.surface, batch[0]?.kind]).toEqual(["self", "follow_up"]);
    expect(batch[0]?.text).toContain("ana's cli");
    // Raised once only.
    expect(stimuli.raiseDue()).toBe(0);
  });

  it("are refused in the past or too far ahead", () => {
    expect(() => stimuli.followUp(T0 - 10, "yesterday")).toThrow(hand.HandError);
    expect(() => stimuli.followUp(T0 + 400 * 86400, "next year")).toThrow(hand.HandError);
  });

  it("are listed soonest first", () => {
    stimuli.followUp(T0 + 7200, "later");
    stimuli.followUp(T0 + 3600, "sooner");
    expect(stimuli.followUps().follow_ups.map((f) => f.about)).toEqual(["sooner", "later"]);
  });

  it("never come due once dropped", () => {
    const dropped = stimuli.followUp(T0 + 3600, "send the japanese reply", "111");
    const kept = stimuli.followUp(T0 + 3600, "did ana ship?");
    expect(stimuli.dropFollowUp(dropped.id as number)).toEqual([dropped.id]);
    s.at(3601);
    expect(stimuli.raiseDue()).toBe(1);
    const [raised] = stimuliOf(take(10, 10_000));
    expect(raised?.ref).toBe(String(kept.id));
  });

  it("are dropped by ref with or without the surface", () => {
    const a = stimuli.followUp(T0 + 3600, "answer ana", "x:111");
    const b = stimuli.followUp(T0 + 7200, "answer ana again", "111");
    stimuli.followUp(T0 + 3600, "something else", "222");
    expect(stimuli.dropFollowUp(null, "111").sort()).toEqual([a.id, b.id].sort());
    expect(stimuli.followUps().follow_ups.map((f) => f.ref)).toEqual(["222"]);
  });

  it("need an id or a ref to drop, and drop only what waits", () => {
    expect(() => stimuli.dropFollowUp()).toThrow(hand.HandError);
    const f = stimuli.followUp(T0 + 60, "soon");
    s.at(61);
    stimuli.raiseDue();
    expect(stimuli.dropFollowUp(f.id as number)).toEqual([]); // already raised: nothing left to drop
    expect(stimuli.dropFollowUp(999)).toEqual([]);
  });

  it("resolve when the thing they are about is answered", () => {
    const parked = stimuli.followUp(T0 + 3600, "send the japanese reply", "x:111");
    stimuli.followUp(T0 + 3600, "did ana ship?", "x:222");
    stimuli.followUp(T0 + 3600, "no ref at all");
    expect(stimuli.resolve(["111"])).toEqual([{ id: parked.id, about: "send the japanese reply", ref: "x:111" }]);
    s.at(3601);
    expect(stimuli.raiseDue()).toBe(2);
    expect(stimuli.followUps("resolved").follow_ups[0]?.id).toBe(parked.id);
  });

  it("spare the ones resolve is told to keep", () => {
    const f = stimuli.followUp(T0 + 3600, "see whether ana answers back", "111");
    expect(stimuli.resolve(["111"], [f.id as number])).toEqual([]);
    expect(stimuli.followUps().follow_ups).toHaveLength(1);
  });
});
