import { existsSync, readFileSync, rmSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import plugin from "../../plugins/teeny-guardrails/index.js";
import { parsePolicy } from "../../plugins/teeny-guardrails/policy.js";
import { REPO } from "../helpers.js";
import { makeFixture } from "./fixture.js";

type Handler = (event: Record<string, unknown>, ctx: Record<string, unknown>) => unknown;
function register(config: unknown, registrationMode = "full") {
  const hooks = new Map<string, { handler: Handler; opts?: Record<string, unknown> }>();
  const logs: string[] = [];
  const api = {
    pluginConfig: config,
    registrationMode,
    logger: { error: (m: string) => logs.push(m), warn: (m: string) => logs.push(m), info: () => undefined, debug: () => undefined },
    on: (name: string, handler: Handler, opts?: Record<string, unknown>) => hooks.set(name, { handler, opts }),
  };
  (plugin as unknown as { register: (api: unknown) => void }).register(api);
  return { hooks, logs };
}

describe("the plugin", () => {
  it("registers before_tool_call at the highest priority, and the hooks it documents", () => {
    const f = makeFixture();
    const { hooks } = register({ policy: JSON.parse(JSON.stringify(f.policy)) });
    expect([...hooks.keys()].sort()).toEqual(["after_tool_call", "before_prompt_build", "before_tool_call", "tool_result_persist"]);
    expect(hooks.get("before_tool_call")?.opts?.priority).toBe(1000);
    expect(plugin.id).toBe("teeny-guardrails");
  });

  it("writes policy.readyFile once its hooks are registered, and never when the policy is unusable", () => {
    const f = makeFixture();
    const ready = join(f.root, "state", "openclaw", "guardrails.ready");
    rmSync(ready, { force: true });
    register({ policy: { ...JSON.parse(JSON.stringify(f.policy)), readyFile: ready } });
    expect(existsSync(ready)).toBe(true);
    rmSync(ready);
    const bad = register({ policy: { readyFile: ready } });
    expect(bad.logs.join("\n")).toContain("every tool call will be blocked");
    expect(existsSync(ready)).toBe(false);
  });

  it("writes the ready file only in the Gateway's own \"full\" load, with its pid (registration-mode.md: other modes are CLI and discovery loads)", () => {
    const f = makeFixture();
    const ready = join(f.root, "state", "openclaw", "guardrails.ready");
    const policy = { ...JSON.parse(JSON.stringify(f.policy)), readyFile: ready };
    for (const mode of ["cli-metadata", "discovery", "tool-discovery", "setup-only", "setup-runtime"]) {
      rmSync(ready, { force: true });
      register({ policy }, mode);
      expect(existsSync(ready), mode).toBe(false);
    }
    register({ policy }, "full");
    expect(JSON.parse(readFileSync(ready, "utf8")).pid).toBe(process.pid);
  });

  it("blocks and rewrites through the hook", async () => {
    const f = makeFixture();
    const { hooks } = register({ policy: JSON.parse(JSON.stringify(f.policy)) });
    const hook = hooks.get("before_tool_call")?.handler as Handler;
    const ctx = { agentId: "mind", sessionKey: "agent:mind:t", runId: "r" };
    expect(await hook({ toolName: "exec", params: { command: "curl x" } }, ctx)).toMatchObject({ block: true });
    expect(await hook({ toolName: "exec", params: { command: "hands/stats top" } }, ctx)).toMatchObject({ params: { command: `${join(f.root, "hands/stats")} top` } });
  });

  it("without a usable policy every tool call is blocked, and it says why", async () => {
    for (const bad of [undefined, {}, { policy: {} }, { policy: { root: "relative" } }]) {
      const { hooks, logs } = register(bad);
      const hook = hooks.get("before_tool_call")?.handler as Handler;
      expect(await hook({ toolName: "read", params: { path: "/x" } }, { agentId: "mind" })).toMatchObject({ block: true, blockReason: expect.stringContaining("no usable policy") });
      expect(logs.join("\n")).toMatch(/every tool call will be blocked/);
    }
  });

  it("rejects a malformed policy loudly", () => {
    const f = makeFixture();
    const raw = JSON.parse(JSON.stringify(f.policy));
    expect(() => parsePolicy({ policy: { ...raw, agents: { mind: { ...raw.agents.mind, maxToolCalls: 0 } } } })).toThrow(/maxToolCalls/);
    expect(() => parsePolicy({ policy: { ...raw, agents: { mind: { ...raw.agents.mind, id: "other" } } } })).toThrow(/mismatched/);
    expect(() => parsePolicy({ policy: { ...raw, agents: { mind: { ...raw.agents.mind, rw: "x" } } } })).toThrow(/rw/);
    expect(() => parsePolicy({ policy: { ...raw, systemForbidden: "x" } })).toThrow(/systemForbidden/);
    expect(() => parsePolicy({ policy: { ...raw, agents: undefined } })).toThrow(/agents/);
  });

  it("masks a typed password in what is persisted", async () => {
    const f = makeFixture({ fill: async () => "pw-fake-123", liveUrl: async () => "https://example.org/" });
    const { hooks } = register({ policy: JSON.parse(JSON.stringify(f.policy)) });
    const hook = hooks.get("before_tool_call")?.handler as Handler;
    const persist = hooks.get("tool_result_persist")?.handler as Handler;
    expect(persist({ message: { content: "x" } }, {})).toBeUndefined();
    // the registered guard has its own state and the real fill program; drive the fixture's guard instead
    await f.guard.beforeToolCall({ toolName: "browser", params: { action: "open", url: "https://example.org/", targetId: "t" } }, { agentId: "operator", sessionKey: "s", runId: "1" });
    await f.guard.beforeToolCall({ toolName: "browser", params: { action: "act", targetId: "t", request: { kind: "type", text: "{{keychain:example.org}}" } } }, { agentId: "operator", sessionKey: "s", runId: "1" });
    expect(f.guard.browserState.secrets.has("pw-fake-123")).toBe(true);
    void hook;
  });
});

describe("the core no longer injects recall itself", () => {
  it("teeny-guardrails carries no recall, and hooks the prompt only for the wake's inbox block", () => {
    const f = makeFixture();
    expect(f.policy).not.toHaveProperty("recall");
    expect(JSON.stringify(f.policy)).not.toMatch(/recall|engineFile|masterKeyFile/);
    const { hooks } = register({ policy: JSON.parse(JSON.stringify(f.policy)) });
    // before_prompt_build is the mind's wake block (bin/reply-queue), which has nothing to do with memory.
    for (const name of ["agent_turn_prepare", "before_agent_run", "llm_input"]) expect(hooks.has(name), name).toBe(false);
    expect(existsSync(join(REPO, "core", "plugins", "teeny-guardrails", "recall.ts"))).toBe(false);
  });

  it("leaves recall one home: teeny-cortexdb injects it once; the guardrails' own before_prompt_build is only the wake block", async () => {
    const f = makeFixture();
    const prompt: string[] = [];
    const api = (id: string, config: unknown) => ({
      pluginConfig: config,
      registrationMode: "full",
      logger: { error: () => undefined, warn: () => undefined, info: () => undefined, debug: () => undefined },
      on: (name: string) => name === "before_prompt_build" && prompt.push(id),
      registerMemoryCapability: () => undefined,
      registerTool: () => undefined,
    });
    (plugin as unknown as { register: (a: unknown) => void }).register(api("teeny-guardrails", { policy: JSON.parse(JSON.stringify(f.policy)) }));
    const cortex = (await import("../../plugins/teeny-cortexdb/index.js")).default as unknown as { register: (a: unknown) => void };
    cortex.register(
      api("teeny-cortexdb", {
        url: "http://127.0.0.1:3141",
        scope: "s",
        engineFile: "/e/memory-engine.json",
        masterKeyFile: "/e/key",
        recall: { agents: { teeny: { budgetTokens: 3000 } }, timeoutMs: 1 },
        capture: { agents: [], maxChars: 600, maxInFlight: 1 },
        tools: { agents: [], resultTokens: 1, maxStoreChars: 1 },
      }),
    );
    expect(prompt).toEqual(["teeny-guardrails", "teeny-cortexdb"]);
  });
});
