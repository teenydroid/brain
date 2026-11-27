import { EventEmitter } from "node:events";
import type { ChildProcess } from "node:child_process";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { GatewaySupervisor } from "../../src/gateway/supervisor.js";

class FakeChild extends EventEmitter {
  pid = 1234;
  exitCode: number | null = null;
  killed = false;
  stdout = new EventEmitter();
  stderr = new EventEmitter();
  signals: string[] = [];
  autoExitOnTerm = true;
  kill(sig: string): boolean {
    this.signals.push(sig);
    if (sig === "SIGKILL" || this.autoExitOnTerm) this.die(null, sig as NodeJS.Signals);
    return true;
  }
  die(code: number | null, signal: NodeJS.Signals | null = null): void {
    this.exitCode = code ?? 0;
    this.emit("exit", code, signal);
  }
}

let children: FakeChild[];
let spawned: { cmd: string; args: string[]; env: Record<string, string> }[];
let prepares: number;

function make(over: Partial<ConstructorParameters<typeof GatewaySupervisor>[0]> = {}) {
  return new GatewaySupervisor({
    port: 18789,
    command: { cmd: "node", args: ["openclaw.mjs"] },
    backoffMs: { initial: 1000, max: 4000 },
    stableMs: 60_000,
    prepare: () => {
      prepares += 1;
      return { OPENCLAW_STATE_DIR: "/s", OPENCLAW_GATEWAY_TOKEN: "t" };
    },
    spawn: (cmd, args, opts) => {
      const c = new FakeChild();
      children.push(c);
      spawned.push({ cmd, args, env: opts.env as Record<string, string> });
      return c as unknown as ChildProcess;
    },
    ...over,
  });
}

beforeEach(() => {
  vi.useFakeTimers();
  children = [];
  spawned = [];
  prepares = 0;
});
afterEach(() => vi.useRealTimers());

describe("GatewaySupervisor", () => {
  it("starts `openclaw gateway run` on loopback with the prepared environment", async () => {
    const s = make();
    await s.start();
    expect(spawned[0]).toEqual({
      cmd: "node",
      args: ["openclaw.mjs", "gateway", "run", "--port", "18789", "--bind", "loopback"],
      env: { OPENCLAW_STATE_DIR: "/s", OPENCLAW_GATEWAY_TOKEN: "t" },
    });
    expect(s.running).toBe(true);
  });

  it("restarts after a crash with doubling backoff, capped, and re-prepares the config each time", async () => {
    const s = make();
    await s.start();
    for (const wait of [1000, 2000, 4000, 4000]) {
      children.at(-1)!.die(1);
      expect(s.running).toBe(false);
      const before = children.length;
      await vi.advanceTimersByTimeAsync(wait - 1);
      expect(children.length).toBe(before);
      await vi.advanceTimersByTimeAsync(1);
      expect(children.length).toBe(before + 1);
    }
    expect(prepares).toBe(5);
    expect(s.restartCount).toBe(4);
  });

  it("resets the backoff after a stable run", async () => {
    const s = make();
    await s.start();
    children[0]!.die(1);
    await vi.advanceTimersByTimeAsync(1000);
    await vi.advanceTimersByTimeAsync(61_000);
    children[1]!.die(1);
    const before = children.length;
    await vi.advanceTimersByTimeAsync(1000);
    expect(children.length).toBe(before + 1);
  });

  it("stop() sends SIGTERM, waits for the exit and never restarts", async () => {
    const s = make();
    await s.start();
    await s.stop();
    expect(children[0]!.signals).toEqual(["SIGTERM"]);
    await vi.advanceTimersByTimeAsync(60_000);
    expect(children).toHaveLength(1);
    expect(s.running).toBe(false);
  });

  it("stop() during a backoff cancels the pending restart", async () => {
    const s = make();
    await s.start();
    children[0]!.die(1);
    await s.stop();
    await vi.advanceTimersByTimeAsync(60_000);
    expect(children).toHaveLength(1);
  });

  it("kills with SIGKILL a gateway that ignores SIGTERM", async () => {
    const s = make({ killAfterMs: 5000 });
    await s.start();
    children[0]!.autoExitOnTerm = false;
    const stopped = s.stop();
    await vi.advanceTimersByTimeAsync(5000);
    await stopped;
    expect(children[0]!.signals).toEqual(["SIGTERM", "SIGKILL"]);
  });

  it("restartNow() restarts at once on fresh config without counting a failure", async () => {
    const s = make();
    await s.start();
    await s.restartNow();
    await vi.advanceTimersByTimeAsync(0);
    expect(children).toHaveLength(2);
    expect(children[0]!.signals).toEqual(["SIGTERM"]);
    expect(prepares).toBe(2);
    expect(s.restartCount).toBe(0);
  });

  it("restartNow() starts a gateway that is not running", async () => {
    const s = make();
    await s.restartNow();
    expect(children).toHaveLength(1);
  });

  it("retries a restart whose prepare() threw, on the backoff, until it starts", async () => {
    let calls = 0;
    const s = make({
      prepare: () => {
        calls += 1;
        if (calls === 2 || calls === 3) throw new Error("agents/x.toml is mid-edit");
        return { OPENCLAW_STATE_DIR: "/s" };
      },
    });
    await s.start();
    expect(children).toHaveLength(1);
    await s.restartNow(); // prepare #2 throws: no child, but a retry is queued
    expect(children).toHaveLength(1);
    expect(s.running).toBe(false);
    await vi.advanceTimersByTimeAsync(1000); // prepare #3 throws again
    expect(s.running).toBe(false);
    await vi.advanceTimersByTimeAsync(2000); // prepare #4 works
    expect(children).toHaveLength(2);
    expect(s.running).toBe(true);
    await s.stop();
  });

  it("retries a launch whose first start failed", async () => {
    let calls = 0;
    const s = make({
      prepare: () => {
        calls += 1;
        if (calls === 1) throw new Error("ENOSPC");
        return {};
      },
    });
    await s.start().catch(() => undefined);
    await vi.advanceTimersByTimeAsync(1000);
    expect(s.running).toBe(true);
    await s.stop();
  });

  it("kills a Gateway that ignores SIGTERM during a restart, and restarts it", async () => {
    const s = make({ killAfterMs: 5000 });
    await s.start();
    children[0]!.autoExitOnTerm = false;
    const restarted = s.restartNow();
    await vi.advanceTimersByTimeAsync(4999);
    expect(children[0]!.signals).toEqual(["SIGTERM"]);
    await vi.advanceTimersByTimeAsync(2);
    expect(children[0]!.signals).toEqual(["SIGTERM", "SIGKILL"]);
    await restarted;
    expect(children).toHaveLength(2);
    await s.stop();
  });

  it("does not start a second Gateway when a restart arrives while a launch is still preparing", async () => {
    let release!: () => void;
    const gate = new Promise<void>((r) => (release = r));
    let calls = 0;
    const s = make({
      prepare: async () => {
        calls += 1;
        if (calls === 1) await gate;
        return {};
      },
    });
    const starting = s.start();
    const restarting = s.restartNow();
    release();
    await starting;
    await restarting;
    // one Gateway from the first launch; the restart then replaced it, never ran beside it
    expect(children.filter((c) => c.exitCode === null)).toHaveLength(1);
    expect(children.length).toBeLessThanOrEqual(2);
    await s.stop();
  });
});
