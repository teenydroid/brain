import { describe, expect, it } from "vitest";
import { AgentRunError, CliAgentRunner, parseAgentJson, type Exec } from "../../src/gateway/client.js";

// The shape `openclaw agent --agent simple --json` answered from a live gateway (openclaw@2026.11.10).
const LIVE = JSON.stringify({
  runId: "r",
  status: "ok",
  summary: "completed",
  result: {
    payloads: [{ text: "pong", mediaUrl: null }],
    meta: { agentMeta: { provider: "local-openai", model: "glm-5.3-flash", usage: { input: 5, output: 1, total: 6, cost: { total: 0.002 } } } },
  },
});

describe("parseAgentJson", () => {
  it("reads the gateway-backed envelope: text, model, usage and cost", () => {
    expect(parseAgentJson(LIVE)).toEqual({
      text: "pong",
      model: "glm-5.3-flash",
      provider: "local-openai",
      usage: { input: 5, output: 1, total: 6, cost: { total: 0.002 } },
      costUsd: 0.002,
    });
  });

  it("reads the exec envelope ({final, costUsd}) and joins several payloads", () => {
    expect(parseAgentJson(JSON.stringify({ ok: true, status: "ok", final: "done", costUsd: 0.5, model: "m" }))).toMatchObject({ text: "done", costUsd: 0.5, model: "m" });
    expect(parseAgentJson(JSON.stringify({ payloads: [{ text: "a" }, { text: "b" }] })).text).toBe("a\n\nb");
  });

  it("raises a failed run with its kind, and refuses non-JSON", () => {
    expect(() => parseAgentJson(JSON.stringify({ ok: false, error: { type: "cli_error", message: "no model" } }))).toThrow(/no model/);
    expect(() => parseAgentJson(JSON.stringify({ status: "timeout", error: { message: "slow", kind: "timeout" } }))).toThrow(AgentRunError);
    expect(() => parseAgentJson("Gateway call: x\n{")).toThrow(/did not answer JSON/);
  });
});

describe("CliAgentRunner", () => {
  function runner(exec: Exec) {
    return new CliAgentRunner({ env: () => ({ OPENCLAW_STATE_DIR: "/s" }), exec, command: { cmd: "node", args: ["oc.mjs"] } });
  }

  it("runs `openclaw agent` with the session key, the model and the budget, the message on stdin and not in argv", async () => {
    const seen: { args: string[]; stdin?: string; env: NodeJS.ProcessEnv }[] = [];
    const r = runner(async (_cmd, args, opts) => {
      seen.push({ args, stdin: opts.stdin, env: opts.env });
      return { stdout: LIVE, stderr: "", code: 0 };
    });
    const res = await r.run({ agentId: "teeny", sessionKey: "agent:teeny:t1", message: "the secret question", model: "local-openai/m", timeoutSecs: 90 });
    expect(res.text).toBe("pong");
    expect(seen).toHaveLength(1);
    expect(seen[0]!.args).toEqual(["oc.mjs", "agent", "--agent", "teeny", "--session-key", "agent:teeny:t1", "--message-file", "-", "--json", "--timeout", "90", "--model", "local-openai/m"]);
    expect(seen[0]!.args.join(" ")).not.toContain("secret question");
    expect(seen[0]!.stdin).toBe("the secret question");
  });

  it("opens the session in the cwd first, and carries on when it already exists", async () => {
    const calls: string[][] = [];
    const r = runner(async (_c, args) => {
      calls.push(args);
      if (args.includes("gateway")) return { stdout: "", stderr: "session already exists", code: 1 };
      return { stdout: LIVE, stderr: "", code: 0 };
    });
    await r.run({ agentId: "mind", sessionKey: "agent:mind:w", message: "m", cwd: "/work", timeoutSecs: 10 });
    expect(calls[0]).toEqual(["oc.mjs", "gateway", "call", "sessions.create", "--params", JSON.stringify({ key: "agent:mind:w", agentId: "mind", cwd: "/work" })]);
    expect(calls[1]).toContain("agent");
  });

  it("fails the turn when the session cannot be opened in the cwd", async () => {
    const r = runner(async () => ({ stdout: "", stderr: "outside the workspace", code: 1 }));
    await expect(r.run({ agentId: "mind", sessionKey: "k", message: "m", cwd: "/etc", timeoutSecs: 10 })).rejects.toThrow(/outside the workspace/);
  });

  it("reports a non-zero exit with no JSON, and an error envelope with one", async () => {
    await expect(runner(async () => ({ stdout: "", stderr: "boom", code: 3 })).run({ agentId: "a", sessionKey: "k", message: "m", timeoutSecs: 1 })).rejects.toThrow(/exited 3: boom/);
    const failed = JSON.stringify({ ok: false, error: { type: "cli_error", message: "Couldn't connect to the AI service" } });
    await expect(runner(async () => ({ stdout: failed, stderr: "", code: 1 })).run({ agentId: "a", sessionKey: "k", message: "m", timeoutSecs: 1 })).rejects.toThrow(/Couldn't connect/);
  });
});
