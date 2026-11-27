// Turns Teeny's agent definitions into what the Gateway needs: one workspace
// per agent whose AGENTS.md is that agent's system prompt, and the
// agents.entries block of the generated config.
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import type { AgentEntry } from "../gateway/config.js";
import { agentEntry, loadAgentDefs, type AgentDef } from "./definitions.js";

/** A tool-less, prompt-less agent for one-shot completions (inference_agent_chat_simple, the judge). */
export const SIMPLE_AGENT: AgentDef = {
  id: "simple",
  displayName: "One-shot completion",
  whenToUse: "A tool-less one-shot completion; not a chat delegate.",
  tools: [],
  prompt: "",
  subagentAllowlist: [],
};

export class AgentCatalog {
  private defs = new Map<string, AgentDef>();

  constructor(
    private readonly brainDir: string,
    private readonly openclawDir: string,
  ) {
    this.reload();
  }

  reload(): void {
    this.defs = new Map([SIMPLE_AGENT, ...loadAgentDefs(this.brainDir)].map((d) => [d.id, d]));
  }

  ids(): string[] {
    return [...this.defs.keys()];
  }

  has(id: string): boolean {
    return this.defs.has(id);
  }

  workspace(id: string): string {
    return join(this.openclawDir, "workspaces", id);
  }

  /** Rewrites the agent's AGENTS.md when its prompt changed (prompts.py rebuilds them at reflection). */
  prepare(id: string): string {
    // Re-read the definition so a rebuilt prompt reaches the next turn without a restart.
    this.reload();
    const def = this.defs.get(id);
    if (!def) throw new Error(`unknown agent: ${id}`);
    const dir = this.workspace(id);
    mkdirSync(dir, { recursive: true, mode: 0o700 });
    const file = join(dir, "AGENTS.md");
    const text = def.prompt ? `${def.prompt}\n` : "";
    if (!existsSync(file) || readFileSync(file, "utf8") !== text) writeFileSync(file, text, { mode: 0o600 });
    return dir;
  }

  entries(): Record<string, AgentEntry> {
    const out: Record<string, AgentEntry> = {};
    for (const def of this.defs.values()) out[def.id] = agentEntry(def, this.prepare(def.id));
    return out;
  }
}
