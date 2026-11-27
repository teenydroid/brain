// The native playbooks' shell, in code: the acting hand once a run and up to max_per_day a day, never another
// hand, the inputs as declared and run with DRY_RUN=1, one run of a playbook at a time. The prompt says the same;
// none of this depends on the model reading it.
import { execFileSync } from "node:child_process";
import { existsSync, mkdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { blocked, makeFixture, rewritten, write, type Fixture } from "./fixture.js";

const NOON = Date.parse("2026-11-27T12:00:00Z");
const POST = `printf '%s' '{"text":"gm, and/or not","anchor":"hn-1"}' | hands/x post`;
const MPOST = `printf '%s' '{"submolt":"general","title":"t","body":"b"}' | hands/moltbook post`;

const exec = (f: Fixture, agent: string, command: string, runId: string) => f.call(agent, "exec", { command }, { runId });
const countFile = (f: Fixture, playbook: string, day = "2026-11-27") => join(f.paths.stateDir, "act", playbook, day);
const taken = (f: Fixture, playbook: string, day = "2026-11-27"): number => (existsSync(countFile(f, playbook, day)) ? Number(readFileSync(countFile(f, playbook, day), "utf8")) : 0);
const end = (f: Fixture, agent: string, runId: string, success = true) => f.guard.runEnded({ runId, success }, { agentId: agent, runId });

describe("the native playbook agents' policy", () => {
  const f = makeFixture({ now: () => NOON });

  it("holds a shell and nothing else: no file tool, no browser, no helpers, no way to write", () => {
    for (const id of ["pb-gm", "pb-moltbook-general"]) {
      const a = f.agent(id);
      expect(a.tools.sort()).toEqual(["exec", "process"]);
      expect(a.browser).toBeUndefined();
      expect(a.subagents).toEqual([]);
      expect(a.rw).toEqual([]);
      expect(a.execRw).toEqual([]);
      expect(a.exec?.writers).toEqual([]);
      expect(a.exec?.tools).toEqual([]);
      expect(a.exec?.teenyBins).toEqual([]);
      // money and keys stay closed to it as to every agent
      expect(a.exec?.handsDeny).toEqual(expect.arrayContaining(["wallet", "x402", "giveaway", "bounty", "surplus", "keychain"]));
    }
  });

  it("carries exactly the playbook's inputs, its one acting hand and its caps", () => {
    const gm = f.agent("pb-gm").exec?.native;
    expect(gm).toMatchObject({ playbook: "gm", maxPerDay: 1, maxPerRun: 1, hand: { name: "x", verbs: ["post"] } });
    expect(gm?.inputs).toEqual([
      ["hands/x", "slot"],
      ["hands/listen", "digest", "--days", "1", "--max", "5"],
      ["hands/hn", "top", "--max", "5"],
      ["hands/story", "desk", "--hours", "36"],
      ["hands/moltbook", "material"],
      ["hands/gh", "activity", "--days", "1"],
    ]);
    expect(gm?.helpers).toEqual([["hands/image", "generate"], ["hands/x", "upload"]]);
    expect(gm?.writer).toBeUndefined();
    const mg = f.agent("pb-moltbook-general").exec?.native;
    expect(mg).toMatchObject({ playbook: "moltbook-general", maxPerDay: 9, maxPerRun: 1, hand: { name: "moltbook", verbs: ["post"] }, helpers: [], writer: "moltbook-general" });
  });
});

describe("a native run's shell: inputs", () => {
  it("allows each declared input as declared, and puts DRY_RUN=1 in front of it", async () => {
    const f = makeFixture({ now: () => NOON });
    for (const input of f.agent("pb-gm").exec!.native!.inputs) {
      const d = await exec(f, "pb-gm", input.join(" "), "r1");
      expect(blocked(d), input.join(" ")).toBeUndefined();
      expect(rewritten(d)?.command).toBe(`DRY_RUN=1 ${join(f.root, input[0] as string)}${input.length > 1 ? ` ${input.slice(1).join(" ")}` : ""}`);
    }
    // an input is not an action: nothing was counted
    expect(taken(f, "gm")).toBe(0);
  });

  it("refuses an input with another verb or other arguments, and a quoted-away spelling of it", async () => {
    const f = makeFixture({ now: () => NOON });
    for (const c of ["hands/x slot --all", "hands/x mentions", "hands/hn top --max 500", "hands/story desk", "hands/listen digest --days 1 --max 5 extra", "hands/x  'slot' --wat"]) {
      expect(blocked(await exec(f, "pb-gm", c, "r1")), c).toMatch(/may not run/);
    }
  });

  it("does not let a command set DRY_RUN, the kill switches or anything else in the environment", async () => {
    const f = makeFixture({ now: () => NOON });
    for (const c of ["DRY_RUN=0 hands/x slot", "X_POSTS=1 hands/x post", `env DRY_RUN=0 hands/x slot`]) {
      expect(blocked(await exec(f, "pb-gm", c, "r1")), c).toBeTruthy();
    }
    expect(blocked(await f.call("pb-gm", "exec", { command: "hands/x slot", env: { DRY_RUN: "0" } }, { runId: "r1" }))).toMatch(/environment/);
  });
});

describe("a native run's shell: the acting hand", () => {
  it("is allowed once, with its body on stdin, and counted for the day", async () => {
    const f = makeFixture({ now: () => NOON });
    const d = await exec(f, "pb-gm", POST, "r1");
    expect(blocked(d)).toBeUndefined();
    // run as the judged file, with whatever environment the gateway has: nothing is put in front of it
    expect(rewritten(d)?.command).toBe(`printf '%s' '{"text":"gm, and/or not","anchor":"hn-1"}' | ${join(f.root, "hands/x")} post`);
    expect(taken(f, "gm")).toBe(1);
  });

  it("is refused the second time in the same run (the per-run cap), whatever the words", async () => {
    const f = makeFixture({ now: () => NOON });
    expect(blocked(await exec(f, "pb-gm", POST, "r1"))).toBeUndefined();
    expect(blocked(await exec(f, "pb-gm", POST.replace("gm,", "again,"), "r1"))).toMatch(/allows 1 call to hands\/x in one run/);
    expect(taken(f, "gm")).toBe(1);
  });

  it("counts two calls in one command as two", async () => {
    const f = makeFixture({ now: () => NOON });
    expect(blocked(await exec(f, "pb-gm", `${POST} ; ${POST}`, "r1"))).toMatch(/in one run/);
    expect(taken(f, "gm")).toBe(0);
  });

  it("is refused in a later run once the day's max_per_day is spent, and allowed again the next UTC day", async () => {
    let now = NOON;
    const f = makeFixture({ now: () => now });
    expect(blocked(await exec(f, "pb-gm", POST, "r1"))).toBeUndefined();
    end(f, "pb-gm", "r1");
    expect(blocked(await exec(f, "pb-gm", POST, "r2"))).toMatch(/taken its 1 action for today \(1 of 1\)/);
    end(f, "pb-gm", "r2");
    expect(taken(f, "gm")).toBe(1);
    now = Date.parse("2026-11-28T00:00:01Z");
    expect(blocked(await exec(f, "pb-gm", POST, "r3"))).toBeUndefined();
    expect(taken(f, "gm", "2026-11-28")).toBe(1);
  });

  it("stops moltbook-general at its ninth post of the day across runs, one a run", async () => {
    const f = makeFixture({ now: () => NOON });
    for (let i = 1; i <= 9; i++) {
      expect(blocked(await exec(f, "pb-moltbook-general", MPOST, `m${i}`)), `post ${i}`).toBeUndefined();
      expect(blocked(await exec(f, "pb-moltbook-general", MPOST, `m${i}`)), `post ${i} again`).toMatch(/in one run/);
      end(f, "pb-moltbook-general", `m${i}`);
    }
    expect(blocked(await exec(f, "pb-moltbook-general", MPOST, "m10"))).toMatch(/taken its 9 actions for today \(9 of 9\)/);
    expect(taken(f, "moltbook-general")).toBe(9);
  });

  it("shares the day's count with bin/act: a playbook that bin/act already took today is spent here too", async () => {
    const f = makeFixture({ now: () => NOON });
    write(countFile(f, "gm"), "1\n");
    expect(blocked(await exec(f, "pb-gm", POST, "r1"))).toMatch(/1 of 1/);
  });

  it("gives the day's reservation back when the hand refuses (exit 2), never when it errors", async () => {
    const f = makeFixture({ now: () => NOON });
    const d = await exec(f, "pb-gm", POST, "r1");
    const command = String(rewritten(d)?.command);
    f.guard.observe({ toolName: "exec", params: { command }, result: { details: { exitCode: 2 } } }, { agentId: "pb-gm", runId: "r1" });
    expect(taken(f, "gm")).toBe(0);
    // the run's own count stays: a refused call was still a call
    expect(blocked(await exec(f, "pb-gm", POST, "r1"))).toMatch(/in one run/);
    // an error (exit 1) may have acted: it keeps its reservation
    const g = makeFixture({ now: () => NOON });
    const e = await exec(g, "pb-gm", POST, "r1");
    g.guard.observe({ toolName: "exec", params: { command: String(rewritten(e)?.command) }, result: { exitCode: 1 } }, { agentId: "pb-gm", runId: "r1" });
    expect(taken(g, "gm")).toBe(1);
    // and with no exit code to read nothing is given back
    const h = makeFixture({ now: () => NOON });
    const k = await exec(h, "pb-gm", POST, "r1");
    h.guard.observe({ toolName: "exec", params: { command: String(rewritten(k)?.command) }, result: "done" }, { agentId: "pb-gm", runId: "r1" });
    expect(taken(h, "gm")).toBe(1);
  });

  it("gives a refusal back to the day it was taken on, even after midnight UTC", async () => {
    let now = Date.parse("2026-11-27T23:59:59Z");
    const f = makeFixture({ now: () => now });
    const command = String(rewritten(await exec(f, "pb-gm", POST, "r1"))?.command);
    now = Date.parse("2026-11-28T00:00:05Z");
    f.guard.observe({ toolName: "exec", params: { command }, result: { exitCode: 2 } }, { agentId: "pb-gm", runId: "r1" });
    expect(taken(f, "gm", "2026-11-27")).toBe(0);
    expect(taken(f, "gm", "2026-11-28")).toBe(0);
  });

  it("never gives the day back for an exit 2 that another command of the line may have made", async () => {
    for (const line of [`${POST} ; hands/x slot`, `hands/x slot && ${POST}`]) {
      const f = makeFixture({ now: () => NOON });
      const d = await exec(f, "pb-gm", line, "r1");
      expect(blocked(d)).toBeUndefined();
      f.guard.observe({ toolName: "exec", params: { command: String(rewritten(d)?.command) }, result: { exitCode: 2 } }, { agentId: "pb-gm", runId: "r1" });
      expect(taken(f, "gm")).toBe(1);
    }
  });

  it("refuses a background exec, whose exit code would never be read", async () => {
    const f = makeFixture({ now: () => NOON });
    expect(blocked(await f.call("pb-gm", "exec", { command: POST, background: true }, { runId: "r1" }))).toMatch(/foreground/);
    expect(taken(f, "gm")).toBe(0);
  });

  it("allows no action while today's count file cannot be read, rather than counting it as unused", async () => {
    const f = makeFixture({ now: () => NOON });
    mkdirSync(countFile(f, "gm"), { recursive: true });
    expect(blocked(await exec(f, "pb-gm", POST, "r1"))).toMatch(/could not be read/);
    const g = makeFixture({ now: () => NOON });
    write(countFile(g, "gm"), "garbage\n");
    expect(blocked(await exec(g, "pb-gm", POST, "r1"))).toMatch(/could not be read/);
  });

  it("names the playbook to a hand: output as TEENY_WRITER, as bin/act did, and not to hands/x", async () => {
    const f = makeFixture({ now: () => NOON });
    expect(rewritten(await exec(f, "pb-moltbook-general", MPOST, "r1"))?.command).toContain(`| TEENY_WRITER=moltbook-general ${join(f.root, "hands/moltbook")} post`);
    expect(String(rewritten(await exec(f, "pb-gm", POST, "r1"))?.command)).not.toContain("TEENY_WRITER");
  });
});

describe("a native run's shell: nothing else", () => {
  it("refuses every other hand and every other verb of the acting hand", async () => {
    const f = makeFixture({ now: () => NOON });
    for (const c of [
      "hands/x like 1", "hands/x reply --to 1", "hands/x post-thread", "hands/x unsay 1", "hands/x follow-up", "hands/x peer-suggest",
      "hands/moltbook post", "hands/moltbook comment", "hands/gh whoami", "hands/stats top", "hands/tasks add",
      "hands/wallet pay --to x --amount 1", "hands/keychain secret --host x",
    ]) {
      expect(blocked(await exec(f, "pb-gm", c, "r1")), c).toBeTruthy();
    }
    for (const c of ["hands/x post", "hands/moltbook comment", "hands/moltbook follow", "hands/image generate", "hands/x upload --file a.png"]) {
      expect(blocked(await exec(f, "pb-moltbook-general", c, "r2")), c).toMatch(/may not run|not allowed/);
    }
    expect(taken(f, "gm")).toBe(0);
    expect(taken(f, "moltbook-general")).toBe(0);
  });

  it("allows gm's media helpers (they are not counted) and not moltbook-general's", async () => {
    const f = makeFixture({ now: () => NOON });
    expect(blocked(await exec(f, "pb-gm", `printf '%s' '{"prompt":"a desk at dawn"}' | hands/image generate`, "r1"))).toBeUndefined();
    expect(blocked(await exec(f, "pb-gm", "hands/x upload --file state/media/a.png", "r1"))).toBeUndefined();
    expect(taken(f, "gm")).toBe(0);
    expect(blocked(await exec(f, "pb-moltbook-general", "hands/image generate", "r2"))).toBeTruthy();
  });

  it("refuses programs and paths beyond the hands: the broker, files, a shell, a scripting language", async () => {
    const f = makeFixture({ now: () => NOON });
    for (const c of ["bin/teeny pulse", "cat secrets/teeny.env", "sh -c 'hands/x post'", "python3 -c 1", "curl https://example.org", "hands/x slot > hands/x", "rm -rf state", "git push", "bin/doctor"]) {
      expect(blocked(await exec(f, "pb-gm", c, "r1")), c).toBeTruthy();
    }
  });

  it("refuses a run's file tools and its browser outright", async () => {
    const f = makeFixture({ now: () => NOON });
    expect(blocked(await f.call("pb-gm", "read", { path: "SOUL.md" }, { runId: "r1" }))).toMatch(/has no read tool/);
    expect(blocked(await f.call("pb-gm", "write", { path: "x" }, { runId: "r1" }))).toMatch(/has no write tool/);
    expect(blocked(await f.call("pb-gm", "browser", { action: "open" }, { runId: "r1" }))).toMatch(/has no browser tool/);
  });

  it("spends a run's tool-call budget like any agent: the run ends at max_iterations", async () => {
    const f = makeFixture({ now: () => NOON });
    const budget = f.agent("pb-gm").maxToolCalls;
    let last;
    for (let i = 0; i <= budget; i++) last = await exec(f, "pb-gm", "hands/x slot", "r1");
    expect(blocked(last)).toMatch(/tool calls/);
  });
});

describe("a native run's lock: one run of a playbook at a time", () => {
  it("refuses a second run while the first holds the lock, and allows it once the first ends", async () => {
    const f = makeFixture({ now: () => NOON });
    expect(blocked(await exec(f, "pb-gm", "hands/x slot", "r1"))).toBeUndefined();
    expect(f.guard.native.locked("gm")).toBe(true);
    expect(blocked(await exec(f, "pb-gm", "hands/x slot", "r2"))).toMatch(/already running/);
    // and the other playbook's lock is its own
    expect(blocked(await exec(f, "pb-moltbook-general", "hands/moltbook material", "m1"))).toBeUndefined();
    end(f, "pb-gm", "r1");
    expect(f.guard.native.locked("gm")).toBe(false);
    expect(blocked(await exec(f, "pb-gm", "hands/x slot", "r2"))).toBeUndefined();
  });

  it("is the lock bin/act takes (state/locks/act-<name>): a live bin/act run holds a native one out", async () => {
    const f = makeFixture({ now: () => NOON });
    // an owner on this host with a pid that is alive: this test's parent, as bin/act's `$$ HOST start` line would say
    write(join(f.paths.stateDir, "locks", "act-gm", "owner"), `${process.ppid} ${process.env.HOSTNAME || (await import("node:os")).hostname()} 1\n`);
    expect(blocked(await exec(f, "pb-gm", "hands/x slot", "r1"))).toMatch(/already running/);
  });

  it("takes over a lock whose owner is gone, on another host, or a native run this process no longer has", async () => {
    const f = makeFixture({ now: () => NOON });
    write(join(f.paths.stateDir, "locks", "act-gm", "owner"), "999999 some-host 1\n");
    expect(blocked(await exec(f, "pb-gm", "hands/x slot", "r1"))).toBeUndefined();
    end(f, "pb-gm", "r1");
    write(join(f.paths.stateDir, "locks", "act-gm", "owner"), `${process.pid} ${process.env.HOSTNAME || (await import("node:os")).hostname()} 0 old-run\n`);
    expect(blocked(await exec(f, "pb-gm", "hands/x slot", "r2"))).toBeUndefined();
  });

  it("writes the owner line bin/act's lock_stale reads: pid, host and the process's real start time", async () => {
    const f = makeFixture({ now: () => NOON });
    await exec(f, "pb-gm", "hands/x slot", "r1");
    const [pid, , start] = readFileSync(join(f.paths.stateDir, "locks", "act-gm", "owner"), "utf8").trim().split(/\s+/);
    const stat = readFileSync(`/proc/${process.pid}/stat`, "utf8");
    expect(pid).toBe(String(process.pid));
    expect(start).toBe(stat.slice(stat.lastIndexOf(")") + 2).split(" ")[19]);
    expect(start).not.toBe("0");
  });

  it("writes the run's record where bin/health reads bin/act's, and releases the lock", async () => {
    const f = makeFixture({ now: () => NOON });
    await exec(f, "pb-gm", "hands/x slot", "r1");
    end(f, "pb-gm", "r1");
    await exec(f, "pb-gm", "hands/x slot", "r2");
    f.guard.runEnded({ runId: "r2", success: false, error: "the model provider failed" }, { agentId: "pb-gm" });
    const lines = readFileSync(join(f.paths.stateDir, "act", "gm", "runs.jsonl"), "utf8").trim().split("\n").map((l) => JSON.parse(l));
    expect(lines[0]).toMatchObject({ ok: true, playbook: "gm", native: true, acted: 0 });
    expect(lines[1]).toMatchObject({ ok: false, playbook: "gm", native: true, failure: "playbook", error: "the model provider failed" });
    expect(f.guard.native.locked("gm")).toBe(false);
  });
});

describe("DRY_RUN and the kill switches reach the hand", () => {
  // The plugin never sets, clears or widens the environment: an input is forced to DRY_RUN=1 (as bin/act did), the
  // acting hand keeps whatever the gateway was started with, and the playbook's name goes to a hand: output.
  function hands(f: Fixture) {
    for (const hand of ["x", "moltbook"]) write(join(f.root, "hands", hand), `#!/bin/sh\necho "$1 dry=\${DRY_RUN-unset} writer=\${TEENY_WRITER-unset} kill=\${X_POSTS-unset}"\n`, 0o755);
  }
  const run = (command: string, env: Record<string, string>): string => execFileSync("sh", ["-c", command], { env: { PATH: process.env.PATH ?? "", ...env } }).toString().trim();

  it("an input sees DRY_RUN=1 whatever the gateway's environment says", async () => {
    const f = makeFixture({ now: () => NOON });
    hands(f);
    const command = String(rewritten(await exec(f, "pb-gm", "hands/x slot", "r1"))?.command);
    expect(run(command, {})).toBe("slot dry=1 writer=unset kill=unset");
    expect(run(command, { DRY_RUN: "0" })).toBe("slot dry=1 writer=unset kill=unset");
  });

  it("the acting hand sees the gateway's DRY_RUN and kill switch untouched, set or not", async () => {
    const f = makeFixture({ now: () => NOON });
    hands(f);
    const command = String(rewritten(await exec(f, "pb-gm", POST, "r1"))?.command);
    expect(run(command, { DRY_RUN: "1", X_POSTS: "0" })).toBe("post dry=1 writer=unset kill=0");
    expect(run(command, {})).toBe("post dry=unset writer=unset kill=unset");
  });

  it("a hand: output also gets the playbook's name, which the agent cannot set", async () => {
    const f = makeFixture({ now: () => NOON });
    hands(f);
    const command = String(rewritten(await exec(f, "pb-moltbook-general", MPOST, "r1"))?.command);
    expect(run(command, { DRY_RUN: "1" })).toBe("post dry=1 writer=moltbook-general kill=unset");
    expect(blocked(await exec(f, "pb-moltbook-general", `TEENY_WRITER=x ${MPOST}`, "r2"))).toBeTruthy();
  });
});
