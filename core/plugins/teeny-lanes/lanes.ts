// The lanes: which model a session runs on, and a ledger of the tokens each lane and each thread spends.
// Pure and synchronous so the tests can drive it without a Gateway. The config comes from brain/lanes.toml,
// parsed and checked by the core (core/src/agents/lanes.ts) and checked again here, since a plugin trusts no one.
import { appendFileSync, closeSync, existsSync, fstatSync, mkdirSync, openSync, readFileSync, readSync } from "node:fs";
import { join } from "node:path";

export interface Lane {
  id: string;
  /** The model a matching session runs on. Unset: the lane only labels the ledger, and the agent's own model stands. */
  model?: string;
  /** The run's fallback chain. `[]` is strict: a failed model fails the run instead of quietly moving it. */
  fallbacks?: string[];
  /** Session-key globs; `*` matches any run of characters, colons too. */
  match: string[];
  /** Tokens a UTC day the whole lane is meant to stay under. Logged against, never enforced. */
  dailyTokens?: number;
  /** Tokens a UTC day any one thread (session) in the lane is meant to stay under. Logged against, never enforced. */
  threadDailyTokens?: number;
}

export interface LanesConfig {
  /** The provider every lane model is served by (gateway/config.ts PROVIDER_ID). */
  provider: string;
  /** The directory the ledger writes one JSONL file per UTC day into. */
  ledgerDir: string;
  lanes: Lane[];
}

/** A session no lane claims; logged under this name so nothing spends tokens unseen. */
export const UNLANED = "other";

const ID = /^[a-z][a-z0-9-]{0,31}$/;
const MODEL = /^[A-Za-z0-9][A-Za-z0-9._:/@-]{0,127}$/;

function positive(v: unknown, what: string): number | undefined {
  if (v === undefined) return undefined;
  if (typeof v !== "number" || !Number.isInteger(v) || v <= 0) throw new Error(`${what} must be a positive integer`);
  return v;
}

/** Checks a lanes config, throwing on anything it cannot read: a lane that half-parses is worse than none. */
export function parseLanes(raw: unknown): LanesConfig {
  const o = (raw ?? {}) as Record<string, unknown>;
  if (typeof o.provider !== "string" || !o.provider) throw new Error("provider must be a non-empty string");
  if (typeof o.ledgerDir !== "string" || !o.ledgerDir) throw new Error("ledgerDir must be a non-empty string");
  if (!Array.isArray(o.lanes)) throw new Error("lanes must be an array");
  const seen = new Set<string>();
  const lanes = o.lanes.map((l, i): Lane => {
    const e = (l ?? {}) as Record<string, unknown>;
    const where = `lanes[${i}]`;
    if (typeof e.id !== "string" || !ID.test(e.id) || e.id === UNLANED) throw new Error(`${where}.id must be a short lowercase name other than "${UNLANED}"`);
    if (seen.has(e.id)) throw new Error(`${where}.id "${e.id}" is used twice`);
    seen.add(e.id);
    if (e.model !== undefined && (typeof e.model !== "string" || !MODEL.test(e.model))) throw new Error(`${where}.model is not a model id`);
    if (e.fallbacks !== undefined && (!Array.isArray(e.fallbacks) || e.fallbacks.some((f) => typeof f !== "string" || !MODEL.test(f)))) {
      throw new Error(`${where}.fallbacks must be a list of model ids`);
    }
    if (e.fallbacks !== undefined && e.model === undefined) throw new Error(`${where}.fallbacks needs a model`);
    if (!Array.isArray(e.match) || e.match.length === 0 || e.match.some((m) => typeof m !== "string" || !m)) {
      throw new Error(`${where}.match must be a non-empty list of session-key globs`);
    }
    const daily = positive(e.dailyTokens, `${where}.dailyTokens`);
    const thread = positive(e.threadDailyTokens, `${where}.threadDailyTokens`);
    return {
      id: e.id,
      match: e.match as string[],
      ...(e.model !== undefined ? { model: e.model as string } : {}),
      ...(e.fallbacks !== undefined ? { fallbacks: e.fallbacks as string[] } : {}),
      ...(daily !== undefined ? { dailyTokens: daily } : {}),
      ...(thread !== undefined ? { threadDailyTokens: thread } : {}),
    };
  });
  return { provider: o.provider, ledgerDir: o.ledgerDir, lanes };
}

export function globMatch(pattern: string, key: string): boolean {
  const parts = pattern.split("*");
  if (parts.length === 1) return pattern === key;
  const first = parts[0] as string;
  const last = parts[parts.length - 1] as string;
  if (!key.startsWith(first) || key.length < first.length + last.length) return false;
  let at = first.length;
  for (const mid of parts.slice(1, -1)) {
    const found = key.indexOf(mid, at);
    if (found < 0) return false;
    at = found + mid.length;
  }
  return key.length - last.length >= at && key.endsWith(last);
}

/** The first lane, in file order, with a glob that matches the session key. */
export function laneOf(cfg: LanesConfig, sessionKey: string | undefined): Lane | undefined {
  if (!sessionKey) return undefined;
  return cfg.lanes.find((l) => l.match.some((m) => globMatch(m, sessionKey)));
}

/** before_model_resolve's answer for a session: its lane's model, or nothing (the agent's own model stands). */
export function resolveModel(cfg: LanesConfig, sessionKey: string | undefined): { modelOverride: string; providerOverride: string; fallbacksOverride?: string[] } | undefined {
  const lane = laneOf(cfg, sessionKey);
  if (!lane?.model) return undefined;
  return {
    modelOverride: lane.model,
    providerOverride: cfg.provider,
    ...(lane.fallbacks ? { fallbacksOverride: lane.fallbacks.map((f) => (f.includes("/") ? f : `${cfg.provider}/${f}`)) } : {}),
  };
}

export interface UsageRow {
  ts: number;
  lane: string;
  session: string;
  agent?: string;
  model?: string;
  input: number;
  output: number;
  cacheRead: number;
  total: number;
  /** Day totals after this call. */
  laneDay: number;
  threadDay: number;
  /** Set on the first call of the day that takes the lane or the thread past its cap. */
  over?: ("lane" | "thread")[];
}

/** True when the file exists, is not empty and does not end in a newline. */
function endsMidLine(file: string): boolean {
  let fd: number;
  try {
    fd = openSync(file, "r");
  } catch {
    return false;
  }
  try {
    const size = fstatSync(fd).size;
    if (size === 0) return false;
    const b = Buffer.alloc(1);
    readSync(fd, b, 0, 1, size - 1);
    return b[0] !== 0x0a;
  } finally {
    closeSync(fd);
  }
}

export const utcDay = (ts: number): string => new Date(ts).toISOString().slice(0, 10);

/**
 * One JSONL file per UTC day (`usage-YYYY-MM-DD.jsonl`, mode 0600). The day's totals are rebuilt from that
 * day's file the first time it is touched, so a Gateway restart neither forgets what was spent nor warns twice.
 */
export class Ledger {
  private day = "";
  private lanes = new Map<string, number>();
  private threads = new Map<string, number>();
  private warned = new Set<string>();

  constructor(private readonly cfg: LanesConfig) {}

  file(day: string): string {
    return join(this.cfg.ledgerDir, `usage-${day}.jsonl`);
  }

  private load(day: string): void {
    // Read into temporary state and commit only once the whole file has been read, so a failed read
    // leaves the ledger untouched and is retried on the next call.
    const lanes = new Map<string, number>();
    const threads = new Map<string, number>();
    const warned = new Set<string>();
    const file = this.file(day);
    if (existsSync(file)) {
      for (const line of readFileSync(file, "utf8").split("\n")) {
        if (!line.trim()) continue;
        try {
          const r = JSON.parse(line) as UsageRow;
          if (typeof r.total !== "number" || typeof r.lane !== "string" || typeof r.session !== "string") continue;
          lanes.set(r.lane, (lanes.get(r.lane) ?? 0) + r.total);
          threads.set(r.session, (threads.get(r.session) ?? 0) + r.total);
          for (const o of r.over ?? []) warned.add(o === "lane" ? `lane:${r.lane}` : `thread:${r.session}`);
        } catch {
          // a torn line is skipped, never fatal
        }
      }
    }
    this.day = day;
    this.lanes = lanes;
    this.threads = threads;
    this.warned = warned;
  }

  /** Records one model call's tokens; returns the row written. Never blocks or changes anything else. */
  record(call: { ts: number; session: string; agent?: string; model?: string; input?: number; output?: number; cacheRead?: number; total?: number }): UsageRow {
    const day = utcDay(call.ts);
    if (day !== this.day) this.load(day);
    const lane = laneOf(this.cfg, call.session);
    const laneId = lane?.id ?? UNLANED;
    const input = call.input ?? 0;
    const output = call.output ?? 0;
    const cacheRead = call.cacheRead ?? 0;
    const total = call.total ?? input + output;
    const laneDay = (this.lanes.get(laneId) ?? 0) + total;
    const threadDay = (this.threads.get(call.session) ?? 0) + total;
    const over: ("lane" | "thread")[] = [];
    if (lane?.dailyTokens && laneDay > lane.dailyTokens && !this.warned.has(`lane:${laneId}`)) over.push("lane");
    if (lane?.threadDailyTokens && threadDay > lane.threadDailyTokens && !this.warned.has(`thread:${call.session}`)) over.push("thread");
    const row: UsageRow = {
      ts: call.ts,
      lane: laneId,
      session: call.session,
      ...(call.agent ? { agent: call.agent } : {}),
      ...(call.model ? { model: call.model } : {}),
      input,
      output,
      cacheRead,
      total,
      laneDay,
      threadDay,
      ...(over.length ? { over } : {}),
    };
    mkdirSync(this.cfg.ledgerDir, { recursive: true, mode: 0o700 });
    const file = this.file(day);
    // A crash can leave a last line without its newline; start on a fresh line so this row stays readable.
    const prefix = endsMidLine(file) ? "\n" : "";
    appendFileSync(file, `${prefix}${JSON.stringify(row)}\n`, { mode: 0o600 });
    // Totals and warning markers move only once the row is on disk.
    this.lanes.set(laneId, laneDay);
    this.threads.set(call.session, threadDay);
    if (over.includes("lane")) this.warned.add(`lane:${laneId}`);
    if (over.includes("thread")) this.warned.add(`thread:${call.session}`);
    return row;
  }
}
