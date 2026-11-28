// brain/lanes.toml, read for the teeny-lanes plugin (core/plugins/teeny-lanes). A lane names the sessions it
// claims by session-key glob, the model they run on, and the daily token caps the ledger logs against.
// The core checks the file here and hands the plugin plain JSON; the plugin checks it again.
import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { parse as parseToml } from "smol-toml";

export const LANES_PLUGIN_ID = "teeny-lanes";

export interface LaneSpec {
  id: string;
  model?: string;
  fallbacks?: string[];
  match: string[];
  dailyTokens?: number;
  threadDailyTokens?: number;
}

export interface LanesPluginConfig {
  provider: string;
  ledgerDir: string;
  lanes: LaneSpec[];
}

const ID = /^[a-z][a-z0-9-]{0,31}$/;
const MODEL = /^[A-Za-z0-9][A-Za-z0-9._:/@-]{0,127}$/;

function count(v: unknown, what: string): number | undefined {
  if (v === undefined) return undefined;
  if (typeof v !== "number" || !Number.isInteger(v) || v <= 0) throw new Error(`${what} must be a positive integer`);
  return v;
}

/** The lanes in a lanes.toml text, in file order (the first match wins). Throws on anything it cannot read. */
export function parseLanesToml(text: string): LaneSpec[] {
  const doc = parseToml(text) as { lane?: unknown };
  if (doc.lane === undefined) return [];
  if (!Array.isArray(doc.lane)) throw new Error("lanes.toml: `lane` must be an array of tables ([[lane]])");
  const seen = new Set<string>();
  return doc.lane.map((raw, i) => {
    const l = raw as Record<string, unknown>;
    const where = `lanes.toml: lane ${i + 1}`;
    if (typeof l.id !== "string" || !ID.test(l.id) || l.id === "other") throw new Error(`${where}: id must be a short lowercase name other than "other"`);
    if (seen.has(l.id)) throw new Error(`${where}: id "${l.id}" is used twice`);
    seen.add(l.id);
    if (l.model !== undefined && (typeof l.model !== "string" || !MODEL.test(l.model))) throw new Error(`${where}: model is not a model id`);
    if (l.fallbacks !== undefined && (!Array.isArray(l.fallbacks) || l.fallbacks.some((f) => typeof f !== "string" || !MODEL.test(f)))) {
      throw new Error(`${where}: fallbacks must be a list of model ids`);
    }
    if (l.fallbacks !== undefined && l.model === undefined) throw new Error(`${where}: fallbacks needs a model`);
    if (!Array.isArray(l.match) || l.match.length === 0 || l.match.some((m) => typeof m !== "string" || !m)) {
      throw new Error(`${where}: match must be a non-empty list of session-key globs`);
    }
    const daily = count(l.daily_tokens, `${where}: daily_tokens`);
    const thread = count(l.thread_daily_tokens, `${where}: thread_daily_tokens`);
    return {
      id: l.id,
      match: l.match as string[],
      ...(l.model !== undefined ? { model: l.model as string } : {}),
      ...(l.fallbacks !== undefined ? { fallbacks: l.fallbacks as string[] } : {}),
      ...(daily !== undefined ? { dailyTokens: daily } : {}),
      ...(thread !== undefined ? { threadDailyTokens: thread } : {}),
    };
  });
}

/** brain/lanes.toml's lanes, or none when the file is absent. A file that is there but unreadable throws. */
export function loadLanes(brainDir: string): LaneSpec[] {
  const file = join(brainDir, "lanes.toml");
  if (!existsSync(file)) return [];
  return parseLanesToml(readFileSync(file, "utf8"));
}

/** The models the lanes route to: the Gateway must list them under the provider or a lane's run fails. */
export function laneModels(lanes: readonly LaneSpec[]): string[] {
  const out = new Set<string>();
  for (const l of lanes) {
    if (l.model) out.add(l.model);
    for (const f of l.fallbacks ?? []) out.add(f);
  }
  return [...out];
}

/** The plugin's config: the ledger under state/lanes/. */
export function lanesPluginConfig(lanes: LaneSpec[], provider: string, stateDir: string): LanesPluginConfig {
  return { provider, ledgerDir: join(stateDir, "lanes"), lanes };
}
