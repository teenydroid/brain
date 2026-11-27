// What the core hands the teeny-cortexdb plugin (plugins/teeny-cortexdb): the config of
// plugins.entries.teeny-cortexdb, and the decision whether the plugin is in the Gateway's
// config at all. The plugin is Teeny's memory slot owner inside the Gateway (recall, capture,
// the memory tools); the memory_* RPCs in ./index.ts stay the Python callers' path, on the same
// CortexDB, scope and client (./cortex.ts).
import { existsSync } from "node:fs";
import { join, resolve } from "node:path";
import type { AgentDef } from "../agents/definitions.js";
import { assertSafeEndpoint, DEFAULT_SCOPE } from "./cortex.js";
import { loadEngine } from "./engine.js";

export const CORTEX_PLUGIN_ID = "teeny-cortexdb";

/** [memory.recall] budget_tokens (bin/lib/core_sections/context.py). */
export const RECALL_BUDGET_TOKENS = 3000;
/** [memory] recall_each_turn_ms. */
export const RECALL_EACH_TURN_MS = 5000;

/**
 * The agents whose finished turns are written to memory: the ones that think for Teeny or talk
 * to people. Never the guard judge, the one-shot simple agent, the act/editor/writer drafts or
 * any tool-less checker (judge, querier, reviewer), and not the operator, babysitter or evolver,
 * whose turns are browser or code work with nothing of Teeny's to remember.
 */
export const CAPTURE_AGENTS = ["mind", "worker", "teeny", "teeny-mail", "teeny-mentions"];
/** Characters of a turn kept by capture, and writes allowed at once. */
export const CAPTURE_MAX_CHARS = 2000;
export const CAPTURE_MAX_IN_FLIGHT = 4;
/** Tokens a memory tool's answer may take, and characters memory_store accepts. */
export const TOOL_RESULT_TOKENS = 1500;
export const STORE_MAX_CHARS = 2000;

type Env = Readonly<Record<string, string | undefined>>;

export function memoryScope(env: Env): string {
  return env.TEENY_MEMORY_SCOPE || DEFAULT_SCOPE;
}

export function cortexPluginIfPresent(path: string): string | undefined {
  return existsSync(join(path, "openclaw.plugin.json")) ? path : undefined;
}

/**
 * CortexDB's URL as the plugin should use it: the engine memory_engine_set stored (the spine
 * boots it from CORTEX_API_URL, which it never exports to the core), else CORTEX_API_URL in the
 * core's own environment. Undefined when neither is set or the URL is not https or loopback:
 * memory is then not configured, and the plugin is left out of the Gateway.
 */
export function cortexUrl(stateDir: string, env: Env): string | undefined {
  const url = loadEngine(stateDir)?.endpoint ?? (env.CORTEX_API_URL ?? "").trim();
  if (!url) return undefined;
  try {
    assertSafeEndpoint(url);
  } catch {
    return undefined;
  }
  return url;
}

export interface CortexPluginInput {
  defs: readonly AgentDef[];
  stateDir: string;
  secretsDir: string;
  env: Env;
}

/** The plugin's config, or undefined when memory is not configured. */
export function buildCortexPluginConfig(input: CortexPluginInput): Record<string, unknown> | undefined {
  const url = cortexUrl(input.stateDir, input.env);
  if (!url) return undefined;
  const ids = new Set(input.defs.map((d) => d.id));
  const recallAgents: Record<string, { budgetTokens: number }> = {};
  for (const def of input.defs) {
    if (def.omitMemoryContext) continue;
    const budgetTokens = Math.min(RECALL_BUDGET_TOKENS, Math.floor(def.memoryBlockMaxTokens ?? RECALL_BUDGET_TOKENS));
    // A budget under one token means this agent takes no memory; leaving it out keeps the plugin's config valid for everyone else.
    if (!(budgetTokens >= 1)) continue;
    recallAgents[def.id] = { budgetTokens };
  }
  return {
    url,
    scope: memoryScope(input.env),
    engineFile: join(input.stateDir, "memory-engine.json"),
    masterKeyFile: resolve(input.env.OPENHUMAN_KEYRING_MASTER_KEY_FILE || join(input.secretsDir, "openhuman.key")),
    recall: { agents: recallAgents, timeoutMs: RECALL_EACH_TURN_MS },
    capture: { agents: CAPTURE_AGENTS.filter((id) => ids.has(id)), maxChars: CAPTURE_MAX_CHARS, maxInFlight: CAPTURE_MAX_IN_FLIGHT },
    tools: {
      // Only an agent whose definition lists the "memory" tool, and only one that holds a tool at all.
      agents: input.defs.filter((d) => d.tools.includes("memory") && d.tools.length > 0).map((d) => d.id),
      resultTokens: TOOL_RESULT_TOKENS,
      maxStoreChars: STORE_MAX_CHARS,
    },
  };
}
