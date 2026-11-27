import { mkdtempSync, mkdirSync, readFileSync, readdirSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { agentEntry, loadAgentDef, loadAgentDefs, toolIds, toolPolicy } from "../../src/agents/definitions.js";
import { AgentCatalog, SIMPLE_AGENT } from "../../src/agents/provision.js";
import { REPO } from "../helpers.js";

const BRAIN = join(REPO, "brain");
// bin/lib/agent_check.py NO_TOOLS: the drafting agents have no tools at all.
const NO_TOOLS = ["teeny", "act", "judge", "reviewer", "querier", "teeny-mentions", "teeny-mail", "writer", "editor"];

describe("agent definitions from brain/agents/*.toml", () => {
  const defs = loadAgentDefs(BRAIN);

  it("loads every definition in the brain", () => {
    const files = readdirSync(join(BRAIN, "agents")).filter((f) => f.endsWith(".toml"));
    expect(defs.map((d) => d.id).sort()).toEqual(files.map((f) => f.replace(".toml", "")).sort());
  });

  it.each(NO_TOOLS)("%s is tool-less: allow nothing, deny everything", (id) => {
    const def = defs.find((d) => d.id === id);
    expect(def, id).toBeDefined();
    expect(def?.tools).toEqual([]);
    expect(toolPolicy(def!)).toEqual({ allow: [], deny: ["*"] });
    expect(agentEntry(def!, "/w").tools).toEqual({ allow: [], deny: ["*"] });
  });

  it("the tool-less simple agent (judge, health probe) denies everything too", () => {
    expect(toolPolicy(SIMPLE_AGENT)).toEqual({ allow: [], deny: ["*"] });
  });

  it("the mind keeps its shell, files, browser, memory and its worker, and loses config and messaging", () => {
    const mind = defs.find((d) => d.id === "mind")!;
    const policy = toolPolicy(mind) as { allow: string[]; deny: string[] };
    expect(policy.allow).toEqual(expect.arrayContaining(["exec", "read", "write", "edit", "browser", "sessions_spawn"]));
    expect(policy.deny).toEqual(expect.arrayContaining(["gateway", "plugins", "group:messaging", "group:automation"]));
    for (const denied of policy.deny) expect(policy.allow).not.toContain(denied);
    const entry = agentEntry(mind, "/w");
    expect(entry.subagents).toEqual({ allowAgents: ["worker"] });
    expect(entry.tools.exec).toMatchObject({ security: "full", ask: "off" });
  });

  it("the evolver gets file tools and no shell (it edits a worktree and runs nothing)", () => {
    const ids = toolIds(defs.find((d) => d.id === "evolver")!.tools);
    expect(ids).toEqual(expect.arrayContaining(["read", "write", "edit"]));
    expect(ids).not.toContain("exec");
    expect(agentEntry(defs.find((d) => d.id === "evolver")!, "/w").tools.exec).toBeUndefined();
  });

  it("glob, grep and list never widen to exec", () => {
    expect(toolIds(["glob", "grep", "list"])).toEqual(["read"]);
  });

  it("refuses a tool it cannot map rather than dropping it", () => {
    expect(() => toolIds(["telepathy"])).toThrow(/no OpenClaw equivalent/);
  });

  it("carries temperature, and names each agent", () => {
    const act = defs.find((d) => d.id === "act")!;
    expect(agentEntry(act, "/w")).toMatchObject({ name: "Teeny (playbook draft)", params: { temperature: 0.8 } });
  });
});

describe("prompts", () => {
  function brain(files: Record<string, string>): string {
    const dir = mkdtempSync(join(tmpdir(), "brain-"));
    for (const [name, text] of Object.entries(files)) {
      mkdirSync(join(dir, name, ".."), { recursive: true });
      writeFileSync(join(dir, name), text);
    }
    return dir;
  }
  const toml = (extra: string) => `id = "x"\ndisplay_name = "X"\nwhen_to_use = "t"\n${extra}\n[tools]\nnamed = []\n`;

  it("takes the built prompt over the bare instructions, and puts SOUL.md first unless omit_identity", () => {
    const dir = brain({
      "SOUL.md": "I am soul.",
      "agents/x.toml": toml('omit_identity = false\n[system_prompt]\nfile = { path = "x.md" }'),
      "agents/x.md": "bare",
      "agent/prompts/x.md": "built",
    });
    expect(loadAgentDef(dir, join(dir, "agents/x.toml")).prompt).toBe("I am soul.\n\nbuilt");
  });

  it("falls back to the instructions when prompts.py has not built one, and drops SOUL.md on omit_identity", () => {
    const dir = brain({
      "SOUL.md": "I am soul.",
      "agents/x.toml": toml('omit_identity = true\n[system_prompt]\nfile = { path = "x.md" }'),
      "agents/x.md": "bare",
    });
    expect(loadAgentDef(dir, join(dir, "agents/x.toml")).prompt).toBe("bare");
  });

  it("reads an inline prompt", () => {
    const dir = brain({ "agents/x.toml": toml('omit_identity = true\n[system_prompt]\ninline = """\nhello\n"""') });
    expect(loadAgentDef(dir, join(dir, "agents/x.toml")).prompt).toBe("hello");
  });

  it("refuses a bad agent id", () => {
    const dir = brain({ "agents/x.toml": 'id = "Bad Id"\n' });
    expect(() => loadAgentDef(dir, join(dir, "agents/x.toml"))).toThrow(/bad agent id/);
  });
});

describe("AgentCatalog", () => {
  it("writes each agent's prompt to its workspace as AGENTS.md, and rewrites it when prompts.py rebuilds it", () => {
    const dir = mkdtempSync(join(tmpdir(), "brain-"));
    mkdirSync(join(dir, "agents"), { recursive: true });
    mkdirSync(join(dir, "agent/prompts"), { recursive: true });
    writeFileSync(join(dir, "agents/x.toml"), 'id = "x"\nomit_identity = true\n[system_prompt]\nfile = { path = "x.md" }\n[tools]\nnamed = []\n');
    writeFileSync(join(dir, "agent/prompts/x.md"), "v1");
    const out = mkdtempSync(join(tmpdir(), "oc-"));
    const catalog = new AgentCatalog(dir, out);
    const entries = catalog.entries();
    expect(Object.keys(entries).sort()).toEqual(["simple", "x"]);
    const ws = entries.x!.workspace;
    expect(readFileSync(join(ws, "AGENTS.md"), "utf8")).toBe("v1\n");
    writeFileSync(join(dir, "agent/prompts/x.md"), "v2");
    catalog.prepare("x");
    expect(readFileSync(join(ws, "AGENTS.md"), "utf8")).toBe("v2\n");
    expect(() => catalog.prepare("nobody")).toThrow(/unknown agent/);
  });
});
