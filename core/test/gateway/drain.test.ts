import { describe, expect, it } from "vitest";
import type { AgentRunRequest, AgentRunResult, AgentRunner } from "../../src/gateway/client.js";
import { DrainingRunner } from "../../src/gateway/drain.js";

const req = (n: number): AgentRunRequest => ({ agentId: "a", sessionKey: `agent:a:${n}`, message: "m", timeoutSecs: 5 });

function controlled() {
  const releases: (() => void)[] = [];
  const started: number[] = [];
  const inner: AgentRunner = {
    run: (r) =>
      new Promise<AgentRunResult>((resolve) => {
        started.push(Number(r.sessionKey.split(":")[2]));
        releases.push(() => resolve({ text: "done" }));
      }),
  };
  return { inner, releases, started };
}

describe("DrainingRunner", () => {
  it("waits for the turn in flight, holds new turns while quiescent, and lets them through on release", async () => {
    const { inner, releases, started } = controlled();
    const d = new DrainingRunner(inner);
    const first = d.run(req(1));
    expect(d.running).toBe(1);
    let drained = false;
    const q = d.quiesce(10_000).then((ok) => (drained = ok));
    const held = d.run(req(2));
    await Promise.resolve();
    expect(started).toEqual([1]); // turn 2 is held at the door
    expect(drained).toBe(false); // and the restart waits for turn 1
    releases[0]!();
    await first;
    await q;
    expect(drained).toBe(true);
    expect(started).toEqual([1]);
    d.release();
    await Promise.resolve();
    await Promise.resolve();
    expect(started).toEqual([1, 2]);
    releases[1]!();
    expect((await held).text).toBe("done");
  });

  it("gives up waiting after the deadline and says so", async () => {
    const { inner, releases } = controlled();
    const d = new DrainingRunner(inner);
    const first = d.run(req(1));
    expect(await d.quiesce(5)).toBe(false);
    d.release();
    releases[0]!();
    await first;
  });

  it("is already drained when nothing runs", async () => {
    const d = new DrainingRunner(controlled().inner);
    expect(await d.quiesce(1000)).toBe(true);
    d.release();
  });
});
