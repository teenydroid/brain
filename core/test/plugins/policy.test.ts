import { mkdirSync, readFileSync, rmSync, symlinkSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { parse } from "smol-toml";
import { loadAgentDefs, toolIds } from "../../src/agents/definitions.js";
import { buildGuardrailsPolicy, codeRoots, HANDS_DENY, UNATTENDED_KINDS, VIEW_ACTIONS } from "../../src/agents/guardrails.js";
import { buildGatewayConfig } from "../../src/gateway/config.js";
import { REPO } from "../helpers.js";
import { parsePolicy } from "../../plugins/teeny-guardrails/policy.js";
import { makeFixture } from "./fixture.js";

const defs = loadAgentDefs(join(REPO, "brain"));

describe("parsePolicy rejects a malformed nested policy at registration", () => {
  const good = () => JSON.parse(JSON.stringify(makeFixture().policy)) as Record<string, unknown> & { agents: Record<string, Record<string, unknown>> };
  it("exec without readOnly, a browser without its lists, and a non-list execRw", () => {
    expect(() => parsePolicy({ policy: good() })).not.toThrow();
    const a = good();
    delete (a.agents.mind?.exec as Record<string, unknown>).readOnly;
    expect(() => parsePolicy({ policy: a })).toThrow(/exec\.readOnly/);
    const b = good();
    b.agents.operator!.browser = { actions: ["open"], keychain: false };
    expect(() => parsePolicy({ policy: b })).toThrow(/browser\.actKinds/);
    const c = good();
    c.agents.mind!.execRw = "x";
    expect(() => parsePolicy({ policy: c })).toThrow(/execRw/);
    // an absent optional policy stays valid
    const d = good();
    delete d.agents.operator!.browser;
    delete d.agents.evolver!.exec;
    expect(() => parsePolicy({ policy: d })).not.toThrow();
  });
});

describe("codeRoots", () => {
  it("lists real checkout folders and never a top-level link that leads outside the checkout", () => {
    const f = makeFixture();
    const outside = join(f.root, "..", `outside-${process.pid}`);
    mkdirSync(outside, { recursive: true });
    try {
      symlinkSync(outside, join(f.root, "leak"));
      symlinkSync(join(f.root, "docs"), join(f.root, "docs-alias")); // inside the checkout: fine
      const roots = codeRoots(f.root, f.paths.brainDir);
      expect(roots).toContain(join(f.root, "hands"));
      expect(roots).toContain(join(f.root, "docs-alias"));
      expect(roots).not.toContain(join(f.root, "leak"));
    } finally {
      rmSync(outside, { recursive: true, force: true });
    }
  });
});

describe("the policy generated from brain/agents/*.toml", () => {
  const f = makeFixture();

  it("covers exactly the agents that hold a tool", () => {
    expect(Object.keys(f.policy.agents).sort()).toEqual(defs.filter((d) => d.tools.length > 0).map((d) => d.id).sort());
    expect(Object.keys(f.policy.agents).sort()).toEqual(["babysitter", "evolver", "mind", "operator", "worker"]);
  });

  it("gives each agent exactly the OpenClaw tools its toml maps to, nothing more", () => {
    for (const def of defs.filter((d) => d.tools.length > 0)) {
      expect(f.agent(def.id).tools.sort(), def.id).toEqual(toolIds(def.tools).sort());
    }
    expect(f.agent("operator").tools.sort()).toEqual(["browser", "read", "tool_search", "write"]);
    expect(f.agent("evolver").tools.sort()).toEqual(["edit", "read", "write"]);
    expect(f.agent("babysitter").tools.sort()).toEqual(["exec", "process", "read"]);
    expect(f.agent("mind").tools).toContain("sessions_spawn");
    for (const id of ["operator", "babysitter", "evolver"]) expect(f.agent(id).subagents).toEqual([]);
    expect(f.agent("mind").subagents).toEqual(["worker"]);
    for (const a of Object.values(f.policy.agents)) {
      for (const never of ["apply_patch", "gateway", "cron", "message", "nodes", "plugins"]) expect(a.tools).not.toContain(never);
    }
  });

  it("holds each agent's max_iterations as its tool-call budget", () => {
    for (const def of defs.filter((d) => d.tools.length > 0)) {
      const raw = parse(readFileSync(join(REPO, "brain", "agents", `${def.id}.toml`), "utf8")) as { max_iterations?: number };
      expect(f.agent(def.id).maxToolCalls, def.id).toBe(raw.max_iterations);
    }
    expect(f.agent("mind").maxToolCalls).toBe(250);
  });

  it("only the mind and its worker may run hands and the broker; money hands are closed to both", () => {
    expect(f.agent("mind").exec?.hands).toBe(true);
    expect(f.agent("worker").exec?.hands).toBe(true);
    expect(f.agent("babysitter").exec?.hands).toBe(false);
    expect(f.agent("babysitter").exec?.teenyBins).toEqual([]);
    expect(f.agent("operator").exec).toBeUndefined();
    expect(f.agent("evolver").exec).toBeUndefined();
    expect(f.agent("mind").exec?.handsDeny).toEqual(HANDS_DENY);
    for (const h of ["wallet", "x402", "giveaway", "keychain"]) expect(HANDS_DENY).toContain(h);
  });

  it("view-only agents get the throwaway profile and the reading actions; only the operator may act and use the keychain", () => {
    for (const id of ["mind", "worker"]) {
      expect(f.agent(id).browser).toEqual({ actions: VIEW_ACTIONS, actKinds: [], profile: "view", keychain: false });
    }
    expect(f.agent("operator").browser).toMatchObject({ actKinds: UNATTENDED_KINDS, profile: "teeny", keychain: true });
    expect(f.agent("operator").browser?.actions).toContain("act");
    expect(VIEW_ACTIONS).not.toContain("act");
    expect(UNATTENDED_KINDS).not.toContain("evaluate");
  });

  it("recalls memory for the agents that keep it, within min(3000, memory_block.max_tokens)", () => {
    const recall = f.policy.recall;
    expect(recall?.timeoutMs).toBe(5000);
    for (const def of defs.filter((d) => !d.omitMemoryContext)) {
      expect(recall?.agents[def.id]?.budgetTokens, def.id).toBe(Math.min(3000, def.memoryBlockMaxTokens ?? 3000));
    }
    expect(recall?.agents.mind?.budgetTokens).toBe(2000);
    // a definition with no cap gets the whole [memory.recall] budget_tokens = 3000
    const open = buildGuardrailsPolicy({ defs: [{ ...defs[0]!, id: "free", omitMemoryContext: false, memoryBlockMaxTokens: undefined }], paths: f.paths, env: {}, workspaceOf: (id) => id }) as { recall: { agents: Record<string, { budgetTokens: number }> } };
    expect(open.recall.agents.free?.budgetTokens).toBe(3000);
    for (const id of ["operator", "babysitter", "evolver", "simple"]) expect(recall?.agents[id]).toBeUndefined();
  });

  it("points the browser at the operator's deny and allow lists and the picture sink", () => {
    expect(f.policy.browserDenyFile).toBe(join(f.root, "deploy/browser-deny.txt"));
    expect(f.policy.browserAllowFile).toBe(join(f.root, "deploy/browser-allow.txt"));
    expect(f.policy.keychainFill).toBe(join(f.root, "bin/keychain-fill"));
    expect(f.policy.picturesDir).toBe(join(f.root, "state/browser/screenshots"));
    expect(f.policy.picturesKeep).toBe(24);
  });

  it("OPENHUMAN_BROWSER_DENY_FILE wins over the default list", () => {
    const raw = buildGuardrailsPolicy({
      defs,
      paths: f.paths,
      env: { OPENHUMAN_BROWSER_DENY_FILE: "/srv/other-deny.txt" },
      workspaceOf: (id) => `/w/${id}`,
    });
    expect(raw.browserDenyFile).toBe("/srv/other-deny.txt");
  });
});

describe("the Gateway config that carries it", () => {
  const base = { port: 1, modelUrl: undefined, model: undefined, models: [], compactionTriggerTokens: 1, keepRecentTokens: 1, agentTimeoutSecs: 1, defaultWorkspace: "/w", agents: {} };

  it("loads the plugin by path with its policy, and gives hooks conversation access for recall", () => {
    const cfg = buildGatewayConfig({ ...base, guardrailsPath: "/x/teeny-guardrails", guardrailsPolicy: { root: "/r" } }) as { plugins: Record<string, unknown> };
    expect(cfg.plugins.load).toEqual({ paths: ["/x/teeny-guardrails"] });
    expect(cfg.plugins.entries).toEqual({
      "memory-core": { enabled: false, config: { dreaming: { enabled: false } } },
      "teeny-guardrails": { enabled: true, hooks: { allowConversationAccess: true }, config: { policy: { root: "/r" } } },
    });
    expect(cfg.plugins.allow).toEqual(["browser", "teeny-guardrails"]);
  });

  it("defines the two browser profiles the plugin pins agents to", () => {
    const cfg = buildGatewayConfig({ ...base, chromePath: "/usr/bin/chromium" }) as { browser: { profiles: Record<string, unknown>; executablePath: string; defaultProfile: string } };
    expect(Object.keys(cfg.browser.profiles).sort()).toEqual(["teeny", "view"]);
    expect(cfg.browser.defaultProfile).toBe("teeny");
    expect(cfg.browser.executablePath).toBe("/usr/bin/chromium");
  });
});
