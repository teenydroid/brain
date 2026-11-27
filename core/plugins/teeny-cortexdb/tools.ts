// The four tools a shell agent may be given: memory_recall, memory_list, memory_store and
// memory_forget. OpenClaw's own memory_search and memory_get belong to its core (the tool
// catalog's "memory" group), so these names stay clear of them.
//
// Every call is also judged by teeny-guardrails' before_tool_call (its policy names the tools
// each agent holds); the checks here are the tool's own and hold without it.
//
// memory_forget is the one that can destroy. It deletes only the learnings whose text matches
// the query, needs a reason that goes into CortexDB's audit note, and refuses when more than
// MAX_FORGET match, so a loose query forgets nothing. It never asks CortexDB to forget a whole
// scope: the original @cortexdb/openclaw 0.1.4 ignored its query and did exactly that.
import type { CortexClient } from "../../src/memory/cortex.js";
import type { ClientSource } from "./client.js";
import type { CortexPluginConfig } from "./config.js";
import { CHARS_PER_TOKEN, clip } from "./recall.js";
import { screenForStore } from "./screen.js";

export const TOOL_NAMES = ["memory_recall", "memory_list", "memory_store", "memory_forget"] as const;
export type ToolName = (typeof TOOL_NAMES)[number];

/** Most memories one memory_forget may delete; a query that matches more is refused. */
export const MAX_FORGET = 5;
/** A reason shorter than this says nothing. */
export const MIN_REASON_CHARS = 8;
/** A query shorter than this would match nearly everything. */
export const MIN_QUERY_CHARS = 3;
/** Newest learnings a forget looks through for matches (pages of 200). */
const FORGET_SCAN = 2000;
const KINDS = ["fact", "preference", "decision", "other"];

export interface ToolResult {
  ok: boolean;
  text: string;
  details: Record<string, unknown>;
}

const done = (text: string, details: Record<string, unknown> = {}): ToolResult => ({ ok: true, text, details: { ok: true, ...details } });
const refuse = (text: string): ToolResult => ({ ok: false, text, details: { ok: false, error: text } });

const asString = (v: unknown): string => (typeof v === "string" ? v : "");
const asLimit = (v: unknown, dflt: number, max: number): number => (typeof v === "number" && Number.isInteger(v) && v > 0 ? Math.min(v, max) : dflt);

const escapeRe = (s: string): string => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

/** Every word of the query appears in the text as a whole word, whatever the case ("art" is not in "party"). */
export function matches(query: string, text: string): boolean {
  const terms = query.split(/\s+/).filter(Boolean);
  return terms.length > 0 && terms.every((t) => new RegExp(`(?<![\\p{L}\\p{N}_])${escapeRe(t)}(?![\\p{L}\\p{N}_])`, "iu").test(text));
}

export class MemoryTools {
  constructor(
    private readonly config: CortexPluginConfig,
    private readonly source: ClientSource,
  ) {}

  private client(): CortexClient | ToolResult {
    return this.source.get() ?? refuse("memory is not reachable right now");
  }

  async run(name: string, params: Record<string, unknown>): Promise<ToolResult> {
    const client = this.client();
    if ("ok" in client) return client;
    try {
      switch (name) {
        case "memory_recall":
          return await this.recall(client, params);
        case "memory_list":
          return await this.list(client, params);
        case "memory_store":
          return await this.store(client, params);
        case "memory_forget":
          return await this.forget(client, params);
        default:
          return refuse(`no such memory tool: ${name}`);
      }
    } catch (e) {
      return refuse(`memory: ${e instanceof Error ? e.message : String(e)}`);
    }
  }

  private async recall(client: CortexClient, p: Record<string, unknown>): Promise<ToolResult> {
    const query = asString(p.query).trim();
    if (!query) return refuse("query is required");
    const limit = asLimit(p.limit, 10, 30);
    const texts = (await client.recall(query.slice(0, 2000), this.config.tools.resultTokens, limit)).slice(0, limit);
    const body = clip(texts, this.config.tools.resultTokens);
    return done(body || "Nothing in memory bears on that.", { count: texts.length });
  }

  private async list(client: CortexClient, p: Record<string, unknown>): Promise<ToolResult> {
    const limit = asLimit(p.limit, 20, 100);
    const page = await client.listLearnings({ limit, ...(asString(p.cursor) ? { cursor: asString(p.cursor) } : {}) });
    const items = page.items.map((i) => ({ id: i.id, observed_at: i.meta.observed_at, text: i.text.slice(0, 300) }));
    const lines = items.map((i) => `- ${i.id} (${i.observed_at}) ${i.text.replace(/\s+/g, " ")}`);
    const room = this.config.tools.resultTokens * CHARS_PER_TOKEN;
    let text = lines.join("\n");
    if (text.length > room) text = `${text.slice(0, room - 1)}…`;
    return done(text || "No memories stored yet.", { items, next_cursor: page.next_cursor });
  }

  private async store(client: CortexClient, p: Record<string, unknown>): Promise<ToolResult> {
    const raw = asString(p.content);
    if (!raw.trim()) return refuse("content is required");
    if (raw.length > this.config.tools.maxStoreChars) return refuse(`content is ${raw.length} characters; the limit is ${this.config.tools.maxStoreChars}`);
    const screened = screenForStore(raw);
    if (!screened.ok) return refuse(screened.reason);
    const kind = KINDS.includes(asString(p.kind)) ? asString(p.kind) : "other";
    const got = await client.storeLearning({ text: screened.text, learningKind: kind, confidence: 0.8 });
    return done(`Stored (${got.id}).`, { id: got.id });
  }

  private async forget(client: CortexClient, p: Record<string, unknown>): Promise<ToolResult> {
    const query = asString(p.query).trim();
    const reason = asString(p.reason).trim();
    if (query.length < MIN_QUERY_CHARS) return refuse(`query must be at least ${MIN_QUERY_CHARS} characters: it says which memories to delete`);
    if (reason.length < MIN_REASON_CHARS) return refuse(`reason is required (at least ${MIN_REASON_CHARS} characters): it goes into the audit note`);
    const found: { id: string; text: string }[] = [];
    let cursor: string | undefined;
    let scanned = 0;
    do {
      const page = await client.listLearnings({ limit: 200, ...(cursor ? { cursor } : {}) });
      for (const item of page.items) if (matches(query, item.text)) found.push({ id: item.id, text: item.text });
      scanned += page.items.length;
      cursor = page.next_cursor ?? undefined;
    } while (cursor && scanned < FORGET_SCAN);
    if (cursor && found.length > 0) {
      return refuse(`more than ${FORGET_SCAN} memories are stored and I could not look through all of them, so I cannot tell how many match: nothing was deleted. Make the query more specific.`);
    }
    if (found.length === 0 && cursor) return done(`Nothing matched in the newest ${scanned} memories, so nothing was deleted.`, { matched: 0, forgotten: 0 });
    if (found.length === 0) return done("Nothing matched, so nothing was deleted.", { matched: 0, forgotten: 0 });
    if (found.length > MAX_FORGET) {
      return refuse(`${found.length} memories match that query, and I delete at most ${MAX_FORGET} at a time: nothing was deleted. Make the query more specific (memory_list shows what is stored).`);
    }
    const forgotten = await client.forget(
      found.map((f) => f.id),
      { reason },
    );
    const shown = found.map((f) => `- ${f.id} ${f.text.replace(/\s+/g, " ").slice(0, 120)}`).join("\n");
    return done(`Deleted ${forgotten} of ${found.length} matching ${found.length === 1 ? "memory" : "memories"}:\n${shown}`, { matched: found.length, forgotten, ids: found.map((f) => f.id) });
  }
}

/** The schemas the model sees (JSON Schema; the descriptions are all it has to go on). */
export const TOOL_SPECS: Record<ToolName, { label: string; description: string; parameters: Record<string, unknown> }> = {
  memory_recall: {
    label: "Memory recall",
    description: "Search long-term memory (CortexDB) for what is remembered about a topic. Relevant memories are already recalled into your context each turn; use this to look something up on purpose.",
    parameters: {
      type: "object",
      additionalProperties: false,
      required: ["query"],
      properties: {
        query: { type: "string", description: "What to look for, in plain words." },
        limit: { type: "integer", minimum: 1, maximum: 30, description: "Most memories to return (default 10)." },
      },
    },
  },
  memory_list: {
    label: "Memory list",
    description: "List stored memories, newest first, each with the id memory_forget reports.",
    parameters: {
      type: "object",
      additionalProperties: false,
      properties: {
        limit: { type: "integer", minimum: 1, maximum: 100, description: "How many (default 20)." },
        cursor: { type: "string", description: "next_cursor from the previous page." },
      },
    },
  },
  memory_store: {
    label: "Memory store",
    description: "Keep one fact, preference or decision in long-term memory so it is there in later sessions. Never store secrets.",
    parameters: {
      type: "object",
      additionalProperties: false,
      required: ["content"],
      properties: {
        content: { type: "string", description: "The thing to remember, as a self-contained sentence or two." },
        kind: { type: "string", enum: KINDS, description: "What sort of memory it is (default other)." },
      },
    },
  },
  memory_forget: {
    label: "Memory forget",
    description: `Delete the stored memories whose text matches the query (every word of it must appear). Needs a reason, which is written to the audit note. Refuses, and deletes nothing, if more than ${MAX_FORGET} memories match. Use it only to retract something that is wrong or was meant to stay private.`,
    parameters: {
      type: "object",
      additionalProperties: false,
      required: ["query", "reason"],
      properties: {
        query: { type: "string", description: "Words that identify the memory to delete." },
        reason: { type: "string", description: "Why it is being deleted." },
      },
    },
  },
};
