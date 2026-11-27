// The strongest check available without a model: the pinned openclaw's own
// validator and plugin loader, on the config the core generates with the plugin
// and its real policy.
import { execFileSync } from "node:child_process";
import { existsSync, mkdirSync, mkdtempSync, readdirSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { buildGuardrailsPolicy } from "../../src/agents/guardrails.js";
import { AgentCatalog } from "../../src/agents/provision.js";
import { openclawCommand } from "../../src/gateway/cli.js";
import { buildGatewayConfig } from "../../src/gateway/config.js";
import { buildCortexPluginConfig } from "../../src/memory/plugin.js";
import { REPO } from "../helpers.js";

function openclaw(dir: string, file: string, args: string[]): string {
  const { cmd, args: lead } = openclawCommand({});
  return execFileSync(cmd, [...lead, ...args], {
    env: { PATH: process.env.PATH ?? "", HOME: dir, OPENCLAW_STATE_DIR: dir, OPENCLAW_CONFIG_PATH: file },
    encoding: "utf8",
    timeout: 100_000, // a stalled CLI is killed and reported, not waited on forever
    killSignal: "SIGKILL",
    stdio: ["ignore", "pipe", "pipe"],
  });
}

describe("teeny-guardrails in the pinned openclaw", () => {
  it("validates the config, loads the plugin and registers every hook it documents", { timeout: 120_000 }, () => {
    const dir = mkdtempSync(join(tmpdir(), "oc-plugin-"));
    const paths = { root: REPO, brainDir: join(REPO, "brain"), stateDir: join(dir, "state"), secretsDir: join(dir, "secrets"), openclawDir: join(dir, "state", "openclaw") };
    mkdirSync(paths.openclawDir, { recursive: true });
    const catalog = new AgentCatalog(paths.brainDir, paths.openclawDir);
    const file = join(dir, "openclaw.json");
    const config = buildGatewayConfig({
      port: 18999,
      modelUrl: "http://127.0.0.1:1/v1",
      model: "m",
      models: [{ id: "m" }],
      compactionTriggerTokens: 100000,
      keepRecentTokens: 40000,
      agentTimeoutSecs: 60,
      defaultWorkspace: join(paths.openclawDir, "workspaces"),
      agents: catalog.entries(),
      guardrailsPath: join(REPO, "core", "plugins", "teeny-guardrails"),
      guardrailsPolicy: buildGuardrailsPolicy({ defs: catalog.definitions(), paths, env: {}, workspaceOf: (id) => catalog.workspace(id) }),
    });
    writeFileSync(file, JSON.stringify(config));
    expect(openclaw(dir, file, ["config", "validate"])).toMatch(/Config valid/);
    const out = openclaw(dir, file, ["plugins", "inspect", "teeny-guardrails", "--runtime"]);
    expect(out).toMatch(/CLI status: loaded/);
    for (const hook of ["before_tool_call (priority 1000)", "tool_result_persist (priority 1000)", "after_tool_call"]) {
      expect(out).toContain(hook);
    }
    // recall is the memory plugin's: the guardrails neither hook the prompt nor ask to read the conversation
    expect(out).not.toContain("before_prompt_build");
    expect(out).not.toMatch(/allowConversationAccess: true/);
  });

  it("loads teeny-cortexdb as the memory slot owner with its hooks and its four tools", { timeout: 120_000 }, () => {
    const dir = mkdtempSync(join(tmpdir(), "oc-cortex-"));
    const paths = { root: REPO, brainDir: join(REPO, "brain"), stateDir: join(dir, "state"), secretsDir: join(dir, "secrets"), openclawDir: join(dir, "state", "openclaw") };
    mkdirSync(paths.openclawDir, { recursive: true });
    const catalog = new AgentCatalog(paths.brainDir, paths.openclawDir);
    const file = join(dir, "openclaw.json");
    const cortexConfig = buildCortexPluginConfig({ defs: catalog.definitions(), stateDir: paths.stateDir, secretsDir: paths.secretsDir, env: { CORTEX_API_URL: "http://127.0.0.1:3141" } });
    expect(cortexConfig).toBeDefined();
    const config = buildGatewayConfig({
      port: 18999,
      modelUrl: "http://127.0.0.1:1/v1",
      model: "m",
      models: [{ id: "m" }],
      compactionTriggerTokens: 100000,
      keepRecentTokens: 40000,
      agentTimeoutSecs: 60,
      defaultWorkspace: join(paths.openclawDir, "workspaces"),
      agents: catalog.entries(true),
      guardrailsPath: join(REPO, "core", "plugins", "teeny-guardrails"),
      guardrailsPolicy: buildGuardrailsPolicy({ defs: catalog.definitions(), paths, env: {}, workspaceOf: (id) => catalog.workspace(id), memoryTools: true }),
      cortex: { path: join(REPO, "core", "plugins", "teeny-cortexdb"), config: cortexConfig as Record<string, unknown> },
    });
    writeFileSync(file, JSON.stringify(config));
    expect(openclaw(dir, file, ["config", "validate"])).toMatch(/Config valid/);
    const out = openclaw(dir, file, ["plugins", "inspect", "teeny-cortexdb", "--runtime"]);
    expect(out).toMatch(/CLI status: loaded/);
    for (const hook of ["before_prompt_build", "agent_end"]) expect(out).toContain(hook);
    for (const tool of ["memory_recall", "memory_list", "memory_store", "memory_forget"]) expect(out).toContain(tool);
    expect(out).toMatch(/allowConversationAccess: true/);
  });

  it("gates every hand skill on the plugin: eligible with it, none without it", { timeout: 120_000 }, () => {
    const dir = mkdtempSync(join(tmpdir(), "oc-skills-"));
    const workspace = join(dir, "ws");
    mkdirSync(workspace, { recursive: true });
    const base = {
      gateway: { mode: "local" },
      skills: (buildGatewayConfig({ port: 1, modelUrl: undefined, model: undefined, models: [], compactionTriggerTokens: 1, keepRecentTokens: 1, agentTimeoutSecs: 1, defaultWorkspace: workspace, agents: {}, skillDirs: [join(REPO, "brain", "skills", "hands")] }) as { skills: unknown }).skills,
      agents: { defaults: { workspace }, entries: { mind: { workspace } } },
    };
    const eligible = (enabled: boolean): string[] => {
      const file = join(dir, `c-${enabled}.json`);
      writeFileSync(file, JSON.stringify({ ...base, plugins: { entries: { "teeny-guardrails": { enabled } } } }));
      const out = JSON.parse(openclaw(dir, file, ["skills", "list", "--agent", "mind", "--json"])) as { skills: { name: string; eligible: boolean }[] };
      return out.skills.filter((s) => s.name.startsWith("hand-") && s.eligible).map((s) => s.name).sort();
    };
    // every generated hand skill is eligible with the plugin on, none with it off
    const inventory = readdirSync(join(REPO, "brain", "skills", "hands"), { withFileTypes: true }).filter((d) => d.isDirectory() && existsSync(join(REPO, "brain", "skills", "hands", d.name, "SKILL.md"))).map((d) => d.name);
    const on = eligible(true);
    expect(on.length).toBeGreaterThan(40);
    expect(on.map((n) => n.replace(/^hand-/, "")).sort()).toEqual(inventory.map((n) => n.replace(/^hand-/, "")).sort());
    expect(eligible(false)).toEqual([]);
  });
});
