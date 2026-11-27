// Where memory_engine_set keeps the engine the core was pointed at: one JSON
// file in state/, mode 0600, with the API key sealed under the master key like
// every other secret the core stores (config/secrets.ts).
import { chmodSync, mkdirSync, readFileSync, renameSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { Sealer } from "../config/secrets.js";
import { CortexClient, DEFAULT_SCOPE } from "./cortex.js";

export const ENGINE_FILE = "memory-engine.json";

export interface StoredEngine {
  engine: "cortexdb";
  endpoint: string;
  apiKeySealed?: string;
  scope?: string;
}

export function engineFile(stateDir: string): string {
  return join(stateDir, ENGINE_FILE);
}

export function saveEngine(stateDir: string, engine: StoredEngine): void {
  const file = engineFile(stateDir);
  mkdirSync(dirname(file), { recursive: true });
  const tmp = `${file}.tmp`;
  writeFileSync(tmp, JSON.stringify(engine, null, 2), { mode: 0o600 });
  chmodSync(tmp, 0o600);
  renameSync(tmp, file);
}

export function loadEngine(stateDir: string): StoredEngine | undefined {
  try {
    const parsed = JSON.parse(readFileSync(engineFile(stateDir), "utf8")) as Partial<StoredEngine>;
    if (parsed.engine !== "cortexdb" || typeof parsed.endpoint !== "string") return undefined;
    return parsed as StoredEngine;
  } catch {
    return undefined;
  }
}

/** A client for the stored engine, or undefined when none is set. */
export function clientFor(stored: StoredEngine | undefined, sealer: Sealer | undefined, scope?: string, fetchImpl?: typeof fetch): CortexClient | undefined {
  if (!stored) return undefined;
  const apiKey = stored.apiKeySealed ? sealer?.open(stored.apiKeySealed) : undefined;
  return new CortexClient({
    endpoint: stored.endpoint,
    ...(apiKey ? { apiKey } : {}),
    scope: scope ?? stored.scope ?? DEFAULT_SCOPE,
    ...(fetchImpl ? { fetch: fetchImpl } : {}),
  });
}
