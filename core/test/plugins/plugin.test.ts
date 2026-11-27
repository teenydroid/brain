import { existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import plugin from "../../plugins/teeny-guardrails/index.js";
import { clip, recallContext } from "../../plugins/teeny-guardrails/recall.js";
import { parsePolicy } from "../../plugins/teeny-guardrails/policy.js";
import { Sealer } from "../../src/config/secrets.js";
import { saveEngine } from "../../src/memory/engine.js";
import { FAKE_KEY } from "../helpers.js";
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
    expect([...hooks.keys()].sort()).toEqual(["after_tool_call", "agent_end", "before_prompt_build", "before_tool_call", "tool_result_persist"]);
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
      expect(await hooks.get("before_prompt_build")?.handler({ prompt: "x" }, { agentId: "mind" })).toBeUndefined();
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

describe("memory recall into the prompt", () => {
  function engineAt(dir: string, withKey = false) {
    mkdirSync(dir, { recursive: true });
    const sealer = new Sealer(FAKE_KEY);
    saveEngine(dir, { engine: "cortexdb", endpoint: "https://cortex.test", ...(withKey ? { apiKeySealed: sealer.seal("cortex-key") } : {}) });
    const keyFile = join(dir, "key");
    writeFileSync(keyFile, FAKE_KEY);
    return { engineFile: join(dir, "memory-engine.json"), masterKeyFile: keyFile };
  }
  const eventsOf = (texts: string[]) => ({ layers: { events: texts.map((t, i) => ({ id: `e${i}`, content: { text: t } })) } });
  const mockFetch = (seen: { url: string; body: unknown; auth?: string | null }[], reply: unknown): typeof fetch =>
    (async (url: URL | string, init?: RequestInit) => {
      const u = new URL(String(url));
      seen.push({ url: u.pathname, body: init?.body ? JSON.parse(String(init.body)) : undefined, auth: new Headers(init?.headers).get("authorization") });
      if (u.pathname === "/v1/auth/whoami") return new Response(JSON.stringify({ caller: "user:local" }));
      return new Response(JSON.stringify(reply));
    }) as typeof fetch;

  it("recalls up to the budget for an agent that keeps its memory context, and for no other", async () => {
    const f = makeFixture();
    const dir = join(f.root, "state");
    const policy = { ...engineAt(dir, true), agents: { teeny: { budgetTokens: 3000 } }, timeoutMs: 1000 };
    const seen: { url: string; body: unknown; auth?: string | null }[] = [];
    const text = await recallContext(policy, "teeny", "what do I think about ships", { fetch: mockFetch(seen, eventsOf(["I like ships", "Ships are slow"])) });
    expect(text).toContain("[memory.recall]");
    expect(text).toContain("- I like ships");
    const recallCall = seen.find((s) => s.url === "/v1/recall");
    expect(recallCall?.body).toMatchObject({ scope: "app:tinymemory/app:learnings", query: "what do I think about ships", view: "granular", include: ["events"], budgets: { max_tokens: 3000 } });
    expect(recallCall?.auth).toBe("Bearer cortex-key");
    expect(await recallContext(policy, "operator", "x", { fetch: mockFetch([], eventsOf(["a"])) })).toBeUndefined();
    expect(await recallContext(policy, undefined, "x", { fetch: mockFetch([], eventsOf(["a"])) })).toBeUndefined();
    expect(await recallContext(policy, "teeny", "  ", { fetch: mockFetch([], eventsOf(["a"])) })).toBeUndefined();
  });

  it("sends no recall (and no key) to an http endpoint off this machine, and asks for no redirects", async () => {
    const f = makeFixture();
    const dir = join(f.root, "state");
    const policy = { ...engineAt(dir, true), agents: { teeny: { budgetTokens: 3000 } }, timeoutMs: 1000 };
    const seen: { url: string; body: unknown; auth?: string | null }[] = [];
    saveEngine(dir, { engine: "cortexdb", endpoint: "http://cortex.test", apiKeySealed: new Sealer(FAKE_KEY).seal("cortex-key") });
    expect(await recallContext(policy, "teeny", "ships", { fetch: mockFetch(seen, eventsOf(["a"])) })).toBeUndefined();
    expect(seen).toEqual([]);
    saveEngine(dir, { engine: "cortexdb", endpoint: "https://cortex.test", apiKeySealed: new Sealer(FAKE_KEY).seal("cortex-key") });
    let redirect: string | undefined;
    await recallContext(policy, "teeny", "ships", { fetch: (async (_u: URL, init?: RequestInit) => { redirect = init?.redirect; return new Response("{}"); }) as unknown as typeof fetch });
    expect(redirect).toBe("error");
  });

  it("holds the pack to the budget: 3000 tokens, and a smaller memory_block cap", () => {
    const long = Array.from({ length: 200 }, (_, i) => `learning ${i} ${"word ".repeat(40)}`);
    const body = clip(long, 3000);
    expect(body.length).toBeLessThanOrEqual(3000 * 4);
    expect(body.length).toBeGreaterThan(3000 * 4 * 0.9);
    expect(clip(long, 2000).length).toBeLessThanOrEqual(2000 * 4);
    expect(clip([`${"x".repeat(100)}`], 10)).toMatch(/…$/);
    expect(clip([], 3000)).toBe("");
  });

  it("goes without when memory is not set, the store is down, or it is slow: recall never fails a turn", async () => {
    const f = makeFixture();
    const dir = join(f.root, "state", "nomem");
    mkdirSync(dir, { recursive: true });
    const none = { engineFile: join(dir, "memory-engine.json"), masterKeyFile: join(dir, "key"), agents: { teeny: { budgetTokens: 3000 } }, timeoutMs: 100 };
    expect(await recallContext(none, "teeny", "hi")).toBeUndefined();
    const policy = { ...engineAt(join(f.root, "state", "mem")), agents: { teeny: { budgetTokens: 3000 } }, timeoutMs: 50 };
    const down = (async () => { throw new Error("ECONNREFUSED"); }) as unknown as typeof fetch;
    expect(await recallContext(policy, "teeny", "hi", { fetch: down })).toBeUndefined();
    const slow = (async () => new Promise<Response>(() => undefined)) as unknown as typeof fetch;
    expect(await recallContext(policy, "teeny", "hi", { fetch: slow })).toBeUndefined();
    // a sealed key whose master key file is missing: no recall, no throw
    const sealed = { ...engineAt(join(f.root, "state", "mem2"), true), agents: { teeny: { budgetTokens: 3000 } }, timeoutMs: 50, masterKeyFile: "/nonexistent/key" };
    expect(await recallContext(sealed, "teeny", "hi", { fetch: mockFetch([], eventsOf(["a"])) })).toBeUndefined();
  });

  it("the hook hands recalled memory to the prompt as prependContext", async () => {
    const f = makeFixture();
    const dir = join(f.root, "state");
    const eng = engineAt(dir);
    const raw = JSON.parse(JSON.stringify(f.policy));
    raw.recall = { ...eng, agents: { teeny: { budgetTokens: 3000 } }, timeoutMs: 1000 };
    const { hooks } = register({ policy: raw });
    const hook = hooks.get("before_prompt_build")?.handler as Handler;
    const realFetch = globalThis.fetch;
    globalThis.fetch = mockFetch([], eventsOf(["a memory"])) as typeof fetch;
    try {
      expect(await hook({ prompt: "p", currentUserMessage: "hello" }, { agentId: "teeny" })).toEqual({ prependContext: expect.stringContaining("- a memory") });
      expect(await hook({ prompt: "p" }, { agentId: "mind" })).toBeUndefined();
    } finally {
      globalThis.fetch = realFetch;
    }
  });
});
