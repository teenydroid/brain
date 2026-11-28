import { describe, expect, it } from "vitest";
import { conversationOf, threadContext, wakeContext } from "../../plugins/teeny-guardrails/wake.js";
import type { ThreadContextPolicy, WakeContextPolicy } from "../../plugins/teeny-guardrails/policy.js";
import { parsePolicy } from "../../plugins/teeny-guardrails/policy.js";
import { makeFixture } from "./fixture.js";

const policy: WakeContextPolicy = { agents: ["mind"], argv: ["/r/bin/reply-queue"], cwd: "/r", maxChars: 50, timeoutMs: 1000 };

describe("the inbox-pass block put in front of the mind's turn", () => {
  it("runs the command with only HOME, PATH and LANG, for the mind alone, and cuts it to maxChars", async () => {
    const seen: { file: string; env: Record<string, string>; cwd: string }[] = [];
    const run = async (file: string, _a: string[], o: { cwd: string; env: Record<string, string> }) => {
      seen.push({ file, env: o.env, cwd: o.cwd });
      return "x".repeat(400);
    };
    const text = await wakeContext(policy, "mind", run);
    expect(text).toHaveLength(50);
    expect(Object.keys(seen[0]!.env).sort()).toEqual(["HOME", "LANG", "PATH"]);
    expect(await wakeContext(policy, "worker", run)).toBeUndefined();
    expect(await wakeContext(policy, undefined, run)).toBeUndefined();
    expect(await wakeContext(undefined, "mind", run)).toBeUndefined();
  });

  it("logs one line per run of the mind: injected, empty, or failed", async () => {
    const lines: string[] = [];
    await wakeContext(policy, "mind", async () => "block", (l) => lines.push(l));
    await wakeContext(policy, "mind", async () => "", (l) => lines.push(l));
    await wakeContext(policy, "mind", async () => { throw new Error("boom"); }, (l) => lines.push(l));
    await wakeContext(policy, "worker", async () => "block", (l) => lines.push(l));
    expect(lines).toHaveLength(3);
    expect(lines[0]).toMatch(/injected for mind \(5 chars\)/);
    expect(lines[1]).toMatch(/empty/);
    expect(lines[2]).toMatch(/failed.*boom/);
  });

  it("fails open: a command that fails or prints nothing adds nothing", async () => {
    expect(await wakeContext(policy, "mind", async () => { throw new Error("boom"); })).toBeUndefined();
    expect(await wakeContext(policy, "mind", async () => "  \n")).toBeUndefined();
  });

  it("is in the generated policy for the mind when bin/reply-queue exists, at the 1.5k cap", () => {
    const f = makeFixture();
    expect(f.policy.wakeContext).toBeUndefined(); // the fixture's bin/ has no reply-queue
    const withQueue = parsePolicy({ policy: { ...f.policy, wakeContext: { agents: ["mind"], argv: [`${f.root}/bin/reply-queue`], cwd: f.root, maxChars: 1500, timeoutMs: 8000 } } });
    expect(withQueue.wakeContext?.maxChars).toBe(1500);
    expect(() => parsePolicy({ policy: { ...f.policy, wakeContext: { agents: ["mind"], argv: [], cwd: f.root, maxChars: 1500, timeoutMs: 8000 } } })).toThrow(/wakeContext/);
  });
});

describe("the thread block put in front of an X reply turn", () => {
  const thread: ThreadContextPolicy = { agents: ["x-reply"], argv: ["/r/bin/x-thread"], cwd: "/r", sessionPrefix: "x:", maxChars: 60, timeoutMs: 1000 };

  it("passes the conversation id from the session key to bin/x-thread, for x-reply alone", async () => {
    const seen: { file: string; args: string[]; env: Record<string, string> }[] = [];
    const run = async (file: string, args: string[], o: { env: Record<string, string> }) => {
      seen.push({ file, args, env: o.env });
      return "## the thread on X\n" + "y".repeat(200);
    };
    const text = await threadContext(thread, "x-reply", "agent:x-reply:x:1997000000000000001", run);
    expect(seen[0]).toMatchObject({ file: "/r/bin/x-thread", args: ["1997000000000000001"] });
    expect(Object.keys(seen[0]!.env).sort()).toEqual(["HOME", "LANG", "PATH"]);
    expect(text).toHaveLength(60);
    expect(await threadContext(thread, "mind", "agent:mind:x:1", run)).toBeUndefined();
    expect(await threadContext(undefined, "x-reply", "agent:x-reply:x:1", run)).toBeUndefined();
    expect(seen).toHaveLength(1);
  });

  it("never runs for a session that is not one X conversation's, so no argument but an id reaches the command", async () => {
    const run = async () => {
      throw new Error("must not run");
    };
    for (const key of [undefined, "agent:x-reply:once-abc", "agent:x-reply:x:12; rm -rf /", "agent:x-reply:x:", "agent:other:x:123"]) {
      expect(await threadContext(thread, "x-reply", key, run)).toBeUndefined();
    }
    expect(conversationOf("agent:x-reply:x:42", "x-reply", "x:")).toBe("42");
  });

  it("fails open and logs one line a turn", async () => {
    const lines: string[] = [];
    expect(await threadContext(thread, "x-reply", "agent:x-reply:x:5", async () => { throw new Error("boom"); }, (l) => lines.push(l))).toBeUndefined();
    expect(await threadContext(thread, "x-reply", "agent:x-reply:x:5", async () => "  ", (l) => lines.push(l))).toBeUndefined();
    expect(lines[0]).toMatch(/failed.*boom/);
    expect(lines[1]).toMatch(/empty/);
  });

  it("is a checked part of the policy", () => {
    const f = makeFixture();
    const ok = parsePolicy({ policy: { ...f.policy, threadContext: { ...thread, argv: [`${f.root}/bin/x-thread`], cwd: f.root } } });
    expect(ok.threadContext?.sessionPrefix).toBe("x:");
    expect(() => parsePolicy({ policy: { ...f.policy, threadContext: { ...thread, cwd: f.root, sessionPrefix: "x" } } })).toThrow(/sessionPrefix/);
    expect(() => parsePolicy({ policy: { ...f.policy, threadContext: { ...thread, cwd: "relative" } } })).toThrow(/threadContext/);
  });
});
