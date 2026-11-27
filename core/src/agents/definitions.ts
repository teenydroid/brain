// Teeny's agent definitions, read from brain/agents/<id>.toml and the prompts
// bin/lib/prompts.py builds into brain/agent/prompts/. prompts.py stays the
// source of every prompt; this file only reads what it wrote.
import { existsSync, readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { parse } from "smol-toml";
import type { AgentEntry } from "../gateway/config.js";

export interface AgentDef {
  id: string;
  displayName: string;
  whenToUse: string;
  temperature?: number;
  maxIterations?: number;
  /** OpenHuman tool names; empty means no tools at all. */
  tools: string[];
  /** The system prompt as the core would send it, SOUL.md first when the agent keeps identity. */
  prompt: string;
  sandboxMode?: string;
  subagentAllowlist: string[];
  scheduledWorkspace?: string;
  /** `browser_view_only`: the agent's browser may open and read pages, nothing else. */
  browserViewOnly: boolean;
  /** `omit_memory_context`: no recalled memory rides in this agent's turns. */
  omitMemoryContext: boolean;
  /** `memory_block.max_tokens`, when the definition caps what memory may take. */
  memoryBlockMaxTokens?: number;
}

/**
 * OpenHuman tool -> OpenClaw tool ids (docs/gateway/config-tools/tool-policy.md,
 * "Tool groups"). OpenClaw has no separate glob/grep/list tools: reading a
 * directory and searching are done through `read` (and `exec` where the agent
 * has a shell), so those three map to `read` and never widen to `exec`.
 */
export const TOOL_MAP: Record<string, string[]> = {
  shell: ["exec", "process"],
  file_read: ["read"],
  glob: ["read"],
  grep: ["read"],
  list: ["read"],
  file_write: ["write"],
  edit: ["edit"],
  browser: ["browser"],
  tool_search: ["tool_search"],
  memory: ["memory_search", "memory_get"],
  spawn_subagent: ["sessions_spawn", "subagents"],
};

/** Never reachable by an agent, whatever its list says: config, plugins, messaging, nodes, automations. */
export const ALWAYS_DENIED = ["group:automation", "group:messaging", "group:nodes", "gateway", "plugins", "openclaw", "canvas", "apply_patch"];

export function toolIds(names: readonly string[]): string[] {
  const out = new Set<string>();
  for (const name of names) {
    const mapped = TOOL_MAP[name];
    if (!mapped) throw new Error(`agent tool "${name}" has no OpenClaw equivalent (agents/definitions.ts TOOL_MAP)`);
    for (const id of mapped) out.add(id);
  }
  return [...out];
}

function read(path: string): string {
  return existsSync(path) ? readFileSync(path, "utf8").trim() : "";
}

function promptFor(brainDir: string, sp: unknown): string {
  const section = (sp ?? {}) as { file?: { path?: string } | string; inline?: string };
  if (typeof section.inline === "string") return section.inline.trim();
  const file = typeof section.file === "string" ? section.file : section.file?.path;
  if (!file) return "";
  // The built prompt (instructions, then "# your world") wins; the bare
  // instructions are the fallback for a brain whose prompts were not built.
  return read(join(brainDir, "agent", "prompts", file)) || read(join(brainDir, "agents", file));
}

export function loadAgentDef(brainDir: string, tomlFile: string): AgentDef {
  const d = parse(readFileSync(tomlFile, "utf8")) as Record<string, unknown>;
  const id = String(d.id ?? "");
  if (!/^[a-z][a-z0-9-]*$/.test(id)) throw new Error(`${tomlFile}: bad agent id ${JSON.stringify(id)}`);
  const body = promptFor(brainDir, d.system_prompt);
  // The core put SOUL.md (and IDENTITY.md) ahead of the prompt unless omit_identity.
  const identity = d.omit_identity === true ? "" : [read(join(brainDir, "SOUL.md")), read(join(brainDir, "IDENTITY.md"))].filter(Boolean).join("\n\n");
  const tools = ((d.tools ?? {}) as { named?: unknown }).named;
  const subagents = (d.subagents ?? {}) as { allowlist?: unknown };
  return {
    id,
    displayName: String(d.display_name ?? id),
    whenToUse: String(d.when_to_use ?? ""),
    temperature: typeof d.temperature === "number" ? d.temperature : undefined,
    maxIterations: typeof d.max_iterations === "number" ? d.max_iterations : undefined,
    tools: Array.isArray(tools) ? tools.map(String) : [],
    prompt: [identity, body].filter(Boolean).join("\n\n"),
    sandboxMode: typeof d.sandbox_mode === "string" ? d.sandbox_mode : undefined,
    subagentAllowlist: Array.isArray(subagents.allowlist) ? subagents.allowlist.map(String) : [],
    scheduledWorkspace: typeof d.scheduled_workspace === "string" ? d.scheduled_workspace : undefined,
    browserViewOnly: d.browser_view_only === true,
    omitMemoryContext: d.omit_memory_context === true,
    ...(typeof (d.memory_block as { max_tokens?: unknown } | undefined)?.max_tokens === "number"
      ? { memoryBlockMaxTokens: (d.memory_block as { max_tokens: number }).max_tokens }
      : {}),
  };
}

export function loadAgentDefs(brainDir: string): AgentDef[] {
  const dir = join(brainDir, "agents");
  if (!existsSync(dir)) return [];
  return readdirSync(dir)
    .filter((f) => f.endsWith(".toml"))
    .sort()
    .map((f) => loadAgentDef(brainDir, join(dir, f)));
}

/** The OpenClaw tool policy for one agent: tool-less means every tool denied. */
export function toolPolicy(def: AgentDef): AgentEntry["tools"] {
  if (def.tools.length === 0) {
    // docs: tools.allow/deny are case-insensitive and support `*`; deny wins.
    return { allow: [], deny: ["*"] };
  }
  const allow = toolIds(def.tools);
  return { allow, deny: ALWAYS_DENIED.filter((d) => !allow.includes(d)) };
}

export function agentEntry(def: AgentDef, workspace: string): AgentEntry {
  const holdsShell = def.tools.includes("shell");
  return {
    name: def.displayName,
    description: def.whenToUse,
    workspace,
    tools: {
      ...toolPolicy(def),
      // Headless: nobody can answer an approval prompt. Shell agents run only what the
      // exec allowlist admits, and P3's teeny-guardrails plugin owns that list.
      ...(holdsShell ? { exec: { host: "gateway", security: "allowlist", ask: "off" } } : {}),
    },
    ...(def.temperature !== undefined ? { params: { temperature: def.temperature } } : {}),
    ...(def.subagentAllowlist.length > 0 ? { subagents: { allowAgents: def.subagentAllowlist } } : {}),
  };
}
