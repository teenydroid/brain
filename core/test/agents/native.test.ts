// The agents generated for playbooks that run natively: one per `run = "native"` playbook, a shell and nothing
// else, in the act agent's voice, with CortexDB recall like any agent that talks to people.
import { mkdirSync, mkdtempSync, readFileSync, symlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { agentEntry, loadAgentDefs, toolPolicy } from "../../src/agents/definitions.js";
import { buildGuardrailsPolicy } from "../../src/agents/guardrails.js";
import { loadNativeDefs, nativeAddendum, nativeMaxToolCalls } from "../../src/agents/native.js";
import { AgentCatalog } from "../../src/agents/provision.js";
import { buildGatewayConfig } from "../../src/gateway/config.js";
import { REPO } from "../helpers.js";

const BRAIN = join(REPO, "brain");

describe("the agents of native playbooks", () => {
  const { defs, errors } = loadNativeDefs(BRAIN, REPO);

  it("is one agent per playbook that says run = native, and none for a bin/act playbook", () => {
    expect(errors).toEqual({});
    expect(defs.map((d) => d.id).sort()).toEqual(["pb-gm", "pb-moltbook-general"]);
    expect(defs.map((d) => d.id)).not.toContain("pb-morning");
  });

  it("holds a shell and nothing else: exec and its process helper, every other tool denied", () => {
    for (const def of defs) {
      expect(def.tools).toEqual(["shell"]);
      const policy = toolPolicy(def) as { allow: string[]; deny: string[] };
      expect(policy.allow.sort()).toEqual(["exec", "process"]);
      expect(policy.deny).toEqual(expect.arrayContaining(["gateway", "plugins", "group:messaging", "group:automation"]));
      expect(def.subagentAllowlist).toEqual([]);
      expect(def.browserViewOnly).toBe(false);
    }
  });

  it("runs exec through teeny-guardrails alone, like the other shell agents", () => {
    for (const def of defs) expect(agentEntry(def, "/w").tools.exec).toMatchObject({ host: "gateway", security: "full", ask: "off" });
  });

  it("speaks in the act agent's voice, and says how it runs differently", () => {
    const act = loadAgentDefs(BRAIN).find((d) => d.id === "act")!;
    for (const def of defs) {
      expect(def.prompt.startsWith(act.prompt)).toBe(true);
      expect(def.prompt).toContain("# how you run a playbook natively");
      expect(def.temperature).toBe(act.temperature);
      expect(def.displayName).toBe(`Teeny (playbook ${def.native?.playbook})`);
    }
  });

  it("gets CortexDB recall like the act agent, at its budget", () => {
    const act = loadAgentDefs(BRAIN).find((d) => d.id === "act")!;
    for (const def of defs) {
      expect(def.omitMemoryContext).toBe(act.omitMemoryContext);
      expect(def.memoryBlockMaxTokens).toBe(act.memoryBlockMaxTokens);
    }
    const root = mkdtempSync(join(tmpdir(), "native-recall-"));
    const raw = buildGuardrailsPolicy({
      defs,
      paths: { root: REPO, brainDir: BRAIN, stateDir: join(root, "state"), secretsDir: join(root, "secrets"), openclawDir: join(root, "openclaw") },
      env: {},
      workspaceOf: (id) => join(root, "ws", id),
      home: "/nonexistent",
    }) as { recall: { agents: Record<string, { budgetTokens: number }> } };
    expect(Object.keys(raw.recall.agents).sort()).toEqual(["pb-gm", "pb-moltbook-general"]);
    expect(raw.recall.agents["pb-gm"]?.budgetTokens).toBe(act.memoryBlockMaxTokens);
  });

  it("gives a run room for its inputs, the hand and some retries, and no more", () => {
    const gm = defs.find((d) => d.id === "pb-gm")!;
    expect(gm.maxIterations).toBe(nativeMaxToolCalls(gm.native!));
    expect(gm.maxIterations).toBe(6 + 2 * 2 + 3 + 6);
  });

  it("is built into the catalog and the Gateway config's agents when the checkout is known", () => {
    const out = mkdtempSync(join(tmpdir(), "oc-native-"));
    const catalog = new AgentCatalog(BRAIN, out, REPO);
    expect(catalog.ids()).toEqual(expect.arrayContaining(["pb-gm", "pb-moltbook-general", "act", "simple"]));
    expect(catalog.nativeErrors).toEqual({});
    const entries = catalog.entries();
    expect(entries["pb-gm"]).toMatchObject({ name: "Teeny (playbook gm)", tools: { allow: ["exec", "process"] } });
    expect(readFileSync(join(entries["pb-gm"]!.workspace, "AGENTS.md"), "utf8")).toContain("# how you run a playbook natively");
    const cfg = buildGatewayConfig({
      port: 18789,
      modelUrl: "http://127.0.0.1:1/v1",
      model: "stub",
      models: [{ id: "stub" }],
      compactionTriggerTokens: 100_000,
      keepRecentTokens: 40_000,
      agentTimeoutSecs: 1800,
      defaultWorkspace: join(out, "workspaces"),
      agents: entries,
    }) as { agents: { entries: Record<string, { tools: { allow: string[] } }> } };
    expect(Object.keys(cfg.agents.entries)).toEqual(expect.arrayContaining(["pb-gm", "pb-moltbook-general"]));
    expect(cfg.agents.entries["pb-gm"]?.tools.allow.sort()).toEqual(["exec", "process"]);
    // without the checkout's root nothing is generated: a catalog built for another purpose is unchanged
    expect(new AgentCatalog(BRAIN, out).ids()).not.toContain("pb-gm");
  });

  it("leaves a playbook that breaks the rules without an agent, and says why", () => {
    const dir = mkdtempSync(join(tmpdir(), "native-bad-"));
    mkdirSync(join(dir, "playbooks"), { recursive: true });
    const book = (name: string, front: string) => writeFileSync(join(dir, "playbooks", `${name}.md`), `+++\ndescription = "x"\n${front}\n+++\nDo it.\n`);
    book("ok-one", 'output = "post"\nrun = "native"');
    book("tasks-one", 'output = "hand:tasks add"\nrun = "native"');
    book("take", 'output = "post"\nrun = "native"');
    book("plain", 'output = "post"');
    const got = loadNativeDefs(dir, REPO);
    expect(got.defs.map((d) => d.id)).toEqual(["pb-ok-one"]);
    expect(Object.keys(got.errors).sort()).toEqual(["take", "tasks-one"]);
    expect(got.errors["tasks-one"]).toMatch(/cannot run natively/);
    expect(got.errors.take).toMatch(/studio/);
    // a checkout with no hands/ is an error to report, never a crash
    expect(Object.keys(loadNativeDefs(dir, join(dir, "nowhere")).errors).length).toBe(3);
  });

  it("reads every TOML spelling of run = native, and reports an unreadable playbook instead of throwing", () => {
    const dir = mkdtempSync(join(tmpdir(), "native-spell-"));
    mkdirSync(join(dir, "playbooks"), { recursive: true });
    const book = (name: string, front: string) => writeFileSync(join(dir, "playbooks", `${name}.md`), `+++\ndescription = "x"\n${front}\n+++\nDo it.\n`);
    book("single", "output = \"post\"\nrun = 'native'");
    book("indented", 'output = "post"\n  run = "native"');
    symlinkSync(join(dir, "nowhere.md"), join(dir, "playbooks", "dangling.md"));
    const got = loadNativeDefs(dir, REPO);
    expect(got.defs.map((d) => d.id).sort()).toEqual(["pb-indented", "pb-single"]);
    expect(Object.keys(got.errors)).toEqual(["dangling"]);
  });

  it("tells the agent the run cap its playbook sets, not a fixed one", () => {
    expect(nativeAddendum(2)).toContain("beyond the 2 this playbook allows in a run");
  });
});
