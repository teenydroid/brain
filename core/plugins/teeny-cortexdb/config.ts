// The plugin's config (plugins.entries.teeny-cortexdb.config), which the core writes
// from CORTEX_API_URL and the agent definitions (src/memory/plugin.ts). Nothing in it
// is a secret: the CortexDB key stays sealed in state/memory-engine.json, where
// memory_engine_set put it, and is read when a call is made.
import { assertSafeEndpoint } from "../../src/memory/cortex.js";

export interface CortexPluginConfig {
  /** CortexDB's own URL (CORTEX_API_URL): https, or http on this machine. Never a cloud default. */
  url: string;
  scope: string;
  /** state/memory-engine.json: the sealed API key lives there. */
  engineFile: string;
  masterKeyFile: string;
  recall: {
    /** Agents whose turns get recalled memory, each with its token budget. */
    agents: Record<string, { budgetTokens: number }>;
    timeoutMs: number;
  };
  capture: {
    /** Agents whose finished turns are written to memory. Empty means no capture. */
    agents: string[];
    maxChars: number;
    maxInFlight: number;
  };
  tools: {
    /** Agents that may be given the memory tools (a tool-less agent is never in this list). */
    agents: string[];
    resultTokens: number;
    maxStoreChars: number;
  };
}

const fail = (why: string): never => {
  throw new Error(`teeny-cortexdb: ${why}`);
};

function obj(v: unknown, name: string): Record<string, unknown> {
  if (typeof v !== "object" || v === null || Array.isArray(v)) fail(`${name} must be an object`);
  return v as Record<string, unknown>;
}

function str(v: unknown, name: string): string {
  if (typeof v !== "string" || v === "") fail(`${name} must be a string`);
  return v as string;
}

function absolute(v: unknown, name: string): string {
  const s = str(v, name);
  if (!s.startsWith("/")) fail(`${name} must be an absolute path`);
  return s;
}

function posInt(v: unknown, name: string): number {
  if (typeof v !== "number" || !Number.isInteger(v) || v < 1) fail(`${name} must be a positive integer`);
  return v as number;
}

function strings(v: unknown, name: string): string[] {
  if (!Array.isArray(v) || v.some((x) => typeof x !== "string")) fail(`${name} must be a list of strings`);
  return v as string[];
}

/** Reads the config; throws on anything malformed, so a half-understood config loads nothing. */
export function parseConfig(raw: unknown): CortexPluginConfig {
  const c = obj(raw, "config");
  const url = str(c.url, "url");
  try {
    assertSafeEndpoint(url);
  } catch (e) {
    fail((e as Error).message);
  }
  const recall = obj(c.recall, "recall");
  const agents: Record<string, { budgetTokens: number }> = {};
  for (const [id, a] of Object.entries(obj(recall.agents, "recall.agents"))) {
    agents[id] = { budgetTokens: posInt(obj(a, `recall.agents.${id}`).budgetTokens, `recall.agents.${id}.budgetTokens`) };
  }
  const capture = obj(c.capture, "capture");
  const tools = obj(c.tools, "tools");
  return {
    url,
    scope: str(c.scope, "scope"),
    engineFile: absolute(c.engineFile, "engineFile"),
    masterKeyFile: absolute(c.masterKeyFile, "masterKeyFile"),
    recall: { agents, timeoutMs: posInt(recall.timeoutMs, "recall.timeoutMs") },
    capture: {
      agents: strings(capture.agents, "capture.agents"),
      maxChars: posInt(capture.maxChars, "capture.maxChars"),
      maxInFlight: posInt(capture.maxInFlight, "capture.maxInFlight"),
    },
    tools: {
      agents: strings(tools.agents, "tools.agents"),
      resultTokens: posInt(tools.resultTokens, "tools.resultTokens"),
      maxStoreChars: posInt(tools.maxStoreChars, "tools.maxStoreChars"),
    },
  };
}
