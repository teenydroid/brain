import { describe, expect, it } from "vitest";
import { wakeContext } from "../../plugins/teeny-guardrails/wake.js";
import type { WakeContextPolicy } from "../../plugins/teeny-guardrails/policy.js";
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
