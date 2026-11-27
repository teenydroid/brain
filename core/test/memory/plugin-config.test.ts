// What the core writes into the Gateway's config for the memory plugin: the slot, the allow list,
// the plugin's config; and that it is absent, with the slot "none", when CortexDB is not configured.
import { EventEmitter } from "node:events";
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";
import { loadAgentDefs } from "../../src/agents/definitions.js";
import { startCore } from "../../src/main.js";
import { saveEngine } from "../../src/memory/engine.js";
import { buildCortexPluginConfig, cortexUrl, RECALL_BUDGET_TOKENS } from "../../src/memory/plugin.js";
import { REPO } from "../helpers.js";

class QuietChild extends EventEmitter {
  pid = 1;
  exitCode: number | null = null;
  killed = false;
  stdout = new EventEmitter();
  stderr = new EventEmitter();
  kill(): boolean {
    this.exitCode = 0;
    this.emit("exit", 0, null);
    return true;
  }
}

type Cfg = { plugins: { slots: { memory: string }; allow: string[]; load?: { paths: string[] }; entries: Record<string, { enabled: boolean; hooks?: unknown; config?: Record<string, unknown> }> }; agents: { entries: Record<string, { tools: { allow: string[]; deny: string[] } }> } };
const defs = loadAgentDefs(join(REPO, "brain"));
const roots: string[] = [];
afterEach(() => {
  for (const r of roots.splice(0)) rmSync(r, { recursive: true, force: true });
});

function checkout(): string {
  const root = mkdtempSync(join(tmpdir(), "teeny-core-"));
  roots.push(root);
  mkdirSync(join(root, "secrets"));
  mkdirSync(join(root, "core", "plugins", "teeny-guardrails"), { recursive: true });
  mkdirSync(join(root, "core", "plugins", "teeny-cortexdb"), { recursive: true });
  writeFileSync(join(root, "core", "plugins", "teeny-cortexdb", "openclaw.plugin.json"), "{}");
  return root;
}

async function boot(root: string, extra: Record<string, string> = {}) {
  const core = await startCore(
    { PATH: "/usr/bin", TEENY_ROOT: root, OPENHUMAN_WORKSPACE: join(REPO, "brain"), OPENHUMAN_CORE_PORT: "0", OPENHUMAN_CORE_TOKEN: "tok", OPENHUMAN_KEYRING_MASTER_KEY_FILE: "/nonexistent", ...extra },
    { log: () => undefined, spawn: () => new QuietChild() as never, settleMs: 100, bootSettleMs: 100, bootWindowMs: 0 },
  );
  const config = (): Cfg => JSON.parse(readFileSync(join(root, "state", "openclaw", "openclaw.json"), "utf8")) as Cfg;
  const rpc = async (method: string, params: Record<string, unknown>) =>
    (await (await fetch(`http://127.0.0.1:${core.port}/rpc`, { method: "POST", headers: { "content-type": "application/json", authorization: "Bearer tok" }, body: JSON.stringify({ jsonrpc: "2.0", id: 1, method: `openhuman.${method}`, params }) })).json()) as { result?: unknown; error?: unknown };
  return { core, config, rpc };
}

describe("the memory plugin in the Gateway's config", () => {
  it("is absent when CortexDB is not configured (the lab): slot none, memory-core off, no memory tool for anyone", async () => {
    const { core, config } = await boot(checkout());
    try {
      const c = config();
      expect(c.plugins.slots.memory).toBe("none");
      expect(c.plugins.allow).toEqual(["browser", "teeny-guardrails"]);
      expect(c.plugins.load?.paths.some((p) => p.includes("teeny-cortexdb"))).toBe(false);
      expect(c.plugins.entries["teeny-cortexdb"]).toBeUndefined();
      expect(c.plugins.entries["memory-core"]?.enabled).toBe(false);
      for (const [id, e] of Object.entries(c.agents.entries)) expect(e.tools.allow.filter((t) => t.startsWith("memory")), id).toEqual([]);
    } finally {
      await core.stop();
    }
  });

  it("owns the memory slot when CORTEX_API_URL is set: allowed, loaded by path, configured, memory-core still off", async () => {
    const root = checkout();
    const { core, config } = await boot(root, { CORTEX_API_URL: "http://127.0.0.1:3141", TEENY_MEMORY_SCOPE: "app:test/scope" });
    try {
      const c = config();
      expect(c.plugins.slots.memory).toBe("teeny-cortexdb");
      expect(c.plugins.allow).toEqual(["browser", "teeny-guardrails", "teeny-cortexdb"]);
      expect(c.plugins.load?.paths).toEqual([join(root, "core", "plugins", "teeny-guardrails"), join(root, "core", "plugins", "teeny-cortexdb")]);
      expect(c.plugins.entries["memory-core"]).toEqual({ enabled: false, config: { dreaming: { enabled: false } } });
      const entry = c.plugins.entries["teeny-cortexdb"];
      expect(entry).toMatchObject({ enabled: true, hooks: { allowConversationAccess: true } });
      expect(entry?.config).toMatchObject({
        url: "http://127.0.0.1:3141",
        scope: "app:test/scope",
        engineFile: join(root, "state", "memory-engine.json"),
        recall: { timeoutMs: 5000, agents: { mind: { budgetTokens: 2000 }, teeny: { budgetTokens: 2000 }, act: { budgetTokens: 1500 } } },
        capture: { agents: ["mind", "worker", "teeny", "teeny-mail", "teeny-mentions"] },
        tools: { agents: ["mind"] },
      });
      // no secret of any kind in the plugin's config: the key stays sealed in the engine file
      expect(JSON.stringify(entry?.config)).not.toMatch(/apikey|api_key|bearer|"token"|secret|password/i);
      // the mind holds the four tools; no tool-less agent holds one
      expect(c.agents.entries.mind?.tools.allow).toEqual(expect.arrayContaining(["memory_recall", "memory_list", "memory_store", "memory_forget"]));
      expect(c.agents.entries.mind?.tools.allow).not.toContain("memory_search");
      for (const id of ["teeny", "simple", "judge", "act", "writer", "editor"]) expect(c.agents.entries[id]?.tools, id).toEqual({ allow: [], deny: ["*"] });
      for (const id of ["worker", "operator", "babysitter", "evolver"]) expect(c.agents.entries[id]?.tools.allow.filter((t) => t.startsWith("memory")), id).toEqual([]);
    } finally {
      await core.stop();
    }
  });

  it("is left out for a URL that is neither https nor on this machine", async () => {
    const { core, config } = await boot(checkout(), { CORTEX_API_URL: "http://cortex.example.com:3141" });
    try {
      expect(config().plugins.slots.memory).toBe("none");
      expect(config().plugins.entries["teeny-cortexdb"]).toBeUndefined();
    } finally {
      await core.stop();
    }
  });

  it("follows memory_engine_set: a boot that sets the engine brings the plugin into the config", async () => {
    const root = checkout();
    const { core, config, rpc } = await boot(root);
    try {
      expect(config().plugins.slots.memory).toBe("none");
      await rpc("memory_engine_set", { engine: "cortexdb", endpoint: "http://127.0.0.1:1" });
      await vi.waitFor(() => expect(config().plugins.slots.memory).toBe("teeny-cortexdb"), { timeout: 3000 });
      expect(config().plugins.entries["teeny-cortexdb"]?.config).toMatchObject({ url: "http://127.0.0.1:1" });
    } finally {
      await core.stop();
    }
  });
});

describe("buildCortexPluginConfig", () => {
  const input = (env: Record<string, string>, stateDir: string) => ({ defs, stateDir, secretsDir: join(stateDir, "..", "secrets"), env });

  it("takes the URL from the stored engine first, then CORTEX_API_URL, and is undefined with neither", () => {
    const state = mkdtempSync(join(tmpdir(), "cx-state-"));
    roots.push(state);
    expect(buildCortexPluginConfig(input({}, state))).toBeUndefined();
    expect(cortexUrl(state, { CORTEX_API_URL: "  " })).toBeUndefined();
    expect(cortexUrl(state, { CORTEX_API_URL: "http://127.0.0.1:3141" })).toBe("http://127.0.0.1:3141");
    saveEngine(state, { engine: "cortexdb", endpoint: "https://cortex.test" });
    expect(cortexUrl(state, { CORTEX_API_URL: "http://127.0.0.1:3141" })).toBe("https://cortex.test");
  });

  it("serves the scope the RPCs serve, and keeps min(3000, memory_block.max_tokens) for every agent that keeps its memory context", () => {
    const state = mkdtempSync(join(tmpdir(), "cx-state-"));
    roots.push(state);
    const built = buildCortexPluginConfig(input({ CORTEX_API_URL: "http://127.0.0.1:3141" }, state)) as { scope: string; recall: { agents: Record<string, { budgetTokens: number }> } };
    expect(built.scope).toBe("app:tinymemory/app:learnings");
    for (const def of defs.filter((d) => !d.omitMemoryContext)) expect(built.recall.agents[def.id]?.budgetTokens, def.id).toBe(Math.min(3000, def.memoryBlockMaxTokens ?? 3000));
    for (const id of ["operator", "babysitter", "evolver", "judge", "worker"]) expect(built.recall.agents[id], id).toBeUndefined();
    const open = buildCortexPluginConfig({ ...input({ CORTEX_API_URL: "http://127.0.0.1:3141" }, state), defs: [{ ...defs[0]!, id: "free", omitMemoryContext: false, memoryBlockMaxTokens: undefined }] }) as { recall: { agents: Record<string, { budgetTokens: number }> } };
    expect(open.recall.agents.free?.budgetTokens).toBe(RECALL_BUDGET_TOKENS);
    expect(RECALL_BUDGET_TOKENS).toBe(3000);
  });

  it("leaves out an agent whose memory_block.max_tokens is below one token, so its config stays valid for the rest", () => {
    const state = mkdtempSync(join(tmpdir(), "cx-state-"));
    roots.push(state);
    const mk = (id: string, max: number | undefined) => ({ ...defs[0]!, id, omitMemoryContext: false, memoryBlockMaxTokens: max });
    const built = buildCortexPluginConfig({ ...input({ CORTEX_API_URL: "http://127.0.0.1:3141" }, state), defs: [mk("zero", 0), mk("neg", -5), mk("frac", 10.9), mk("ok", 500)] }) as { recall: { agents: Record<string, { budgetTokens: number }> } };
    expect(Object.keys(built.recall.agents).sort()).toEqual(["frac", "ok"]);
    expect(built.recall.agents.frac?.budgetTokens).toBe(10);
  });

  it("hands the plugin an absolute key file, as the core resolves a relative override", () => {
    const state = mkdtempSync(join(tmpdir(), "cx-state-"));
    roots.push(state);
    const built = buildCortexPluginConfig(input({ CORTEX_API_URL: "http://127.0.0.1:3141", OPENHUMAN_KEYRING_MASTER_KEY_FILE: "secrets/k.key" }, state)) as { masterKeyFile: string };
    expect(built.masterKeyFile).toBe(resolve("secrets/k.key"));
  });
});
