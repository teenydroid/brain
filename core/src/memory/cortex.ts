// A client for CortexDB's direct HTTP API (v0.10.4), the memory store OpenHuman
// kept Teeny's learnings in. The wire is the one tinymemory's CortexEngine
// speaks (tinymemory's cortex-wire.md and cortex-wire-envelope.md, from the OpenHuman
// tree this core replaced): append-only events under a scope; a learning is
// one event whose `context.labels` carry its lookup digest and its v3 envelope.
// Reads dedupe (the listing emits every event twice) and decode v3 and v2.
//
// This module is plain Node (fetch, crypto): the guardrails plugin imports it
// too, for per-turn recall, so it must not import anything of the core.
import { createHash } from "node:crypto";

/** CortexDB's legacy layout for a node with no scope root, where existing learnings stay. */
export const DEFAULT_SCOPE = "app:tinymemory/app:learnings";

export class CortexError extends Error {
  constructor(
    message: string,
    public readonly status?: number,
  ) {
    super(message);
    this.name = "CortexError";
  }
}

export interface CortexOptions {
  endpoint: string;
  apiKey?: string;
  scope?: string;
  timeoutMs?: number;
  fetch?: typeof fetch;
}

export interface Learning {
  id: string;
  kind: "learning";
  text: string;
  meta: { observed_at: string };
}

export interface ListPage {
  items: Learning[];
  next_cursor: string | null;
}

interface RawEvent {
  id?: string;
  content?: { text?: string };
  context?: { labels?: string[]; observed_at?: string };
}

const sha256 = (s: string): string => createHash("sha256").update(s).digest("hex");
/** tinymemory's lookup digest: the first 16 hex digits of the SHA-256. */
export const digest = (value: string): string => sha256(value).slice(0, 16);
/** An item's id: a content digest, 40 hex digits. */
export const itemId = (kind: string, text: string): string => sha256(`${kind}\0${text}`).slice(0, 40);

/** `2026-11-27T01:02:03.123Z` as RFC3339 with nanoseconds, which is what the Python readers saw. */
export function nanos(date: Date): string {
  return date.toISOString().replace(/\.(\d{3})Z$/, ".$1000000Z");
}

/** Cuts text into slices of at most `max` UTF-8 bytes, at character boundaries. */
function slices(text: string, max: number): string[] {
  const out: string[] = [];
  let cur = "";
  let bytes = 0;
  for (const ch of text) {
    const b = Buffer.byteLength(ch);
    if (bytes + b > max) {
      out.push(cur);
      cur = "";
      bytes = 0;
    }
    cur += ch;
    bytes += b;
  }
  if (cur) out.push(cur);
  return out;
}

/** The text of a stored event: its v3 envelope's own text, or a v2 envelope's. */
export function decodeEvent(event: RawEvent): { id: string; text: string; observedAt: string; kind: string } | undefined {
  const labels = event.context?.labels ?? [];
  const parts = labels
    .map((l) => /^tm:e:(\d{2}):(.*)$/s.exec(l))
    .filter((m): m is RegExpExecArray => m !== null)
    .map((m) => ({ n: Number(m[1]), slice: m[2] as string }))
    .sort((a, b) => a.n - b.n);
  let envelope: Record<string, unknown> | undefined;
  let text: string | undefined;
  if (parts.length > 0 && parts.every((p, i) => p.n === i)) {
    try {
      envelope = JSON.parse(parts.map((p) => p.slice).join("")) as Record<string, unknown>;
      text = event.content?.text ?? "";
    } catch {
      envelope = undefined;
    }
  }
  if (!envelope) {
    let raw = event.content?.text ?? "";
    for (const candidate of [raw, raw.replace(/^\[[a-z]+\]\s/, "")]) {
      try {
        const parsed = JSON.parse(candidate) as Record<string, unknown>;
        if (parsed && typeof parsed === "object" && parsed.v === 2) {
          envelope = parsed;
          text = typeof parsed.text === "string" ? parsed.text : "";
          break;
        }
      } catch {
        raw = candidate;
      }
    }
  }
  if (!envelope || typeof envelope.id !== "string") return undefined;
  const meta = (envelope.meta ?? {}) as { observed_at?: unknown };
  const observed = typeof meta.observed_at === "string" ? meta.observed_at : (event.context?.observed_at ?? "");
  return { id: envelope.id, text: text ?? "", observedAt: observed, kind: String(envelope.kind ?? "") };
}

export class CortexClient {
  private readonly endpoint: string;
  private readonly scope: string;
  private readonly timeoutMs: number;
  private readonly doFetch: typeof fetch;
  private actorState: { known: boolean; value?: string } = { known: false };

  constructor(private readonly opts: CortexOptions) {
    if (!/^https?:\/\//.test(opts.endpoint)) throw new CortexError("the CortexDB endpoint must be an http(s) URL");
    this.endpoint = opts.endpoint.endsWith("/") ? opts.endpoint : `${opts.endpoint}/`;
    this.scope = opts.scope ?? DEFAULT_SCOPE;
    this.timeoutMs = opts.timeoutMs ?? 10_000;
    this.doFetch = opts.fetch ?? fetch;
  }

  private async call(method: "GET" | "POST", route: string, body?: unknown, query?: Record<string, string>): Promise<unknown> {
    const url = new URL(route, this.endpoint);
    for (const [k, v] of Object.entries(query ?? {})) url.searchParams.set(k, v);
    const headers: Record<string, string> = { accept: "application/json" };
    if (body !== undefined) headers["content-type"] = "application/json";
    if (this.opts.apiKey) headers.authorization = `Bearer ${this.opts.apiKey}`;
    if (route !== "v1/auth/whoami") {
      const actor = await this.actor();
      if (actor) headers["x-cortex-actor"] = actor;
    }
    let res: Response;
    try {
      res = await this.doFetch(url, {
        method,
        headers,
        ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
        signal: AbortSignal.timeout(this.timeoutMs),
      });
    } catch (e) {
      throw new CortexError(`CortexDB did not answer: ${(e as Error).name}`);
    }
    const text = await res.text();
    if (!res.ok) {
      if (res.status === 401 || res.status === 403) this.actorState = { known: false };
      // The body can echo a request: keep a short excerpt only, never the key.
      throw new CortexError(`CortexDB answered ${res.status} for ${route}: ${text.slice(0, 160).replace(/\s+/g, " ")}`, res.status);
    }
    if (!text) return {};
    try {
      return JSON.parse(text) as unknown;
    } catch {
      throw new CortexError(`CortexDB answered ${route} with something that is not JSON`);
    }
  }

  /** CortexDB serves every request as an actor: the `caller` whoami reports, asked once. */
  private async actor(): Promise<string | undefined> {
    if (this.actorState.known) return this.actorState.value;
    try {
      const who = (await this.call("GET", "v1/auth/whoami")) as { caller?: unknown };
      this.actorState = { known: true, ...(typeof who.caller === "string" ? { value: who.caller } : {}) };
    } catch (e) {
      // A server before the actor model (404/405) sends no header; any other failure is retried next call.
      if (e instanceof CortexError && (e.status === 404 || e.status === 405)) this.actorState = { known: true };
    }
    return this.actorState.value;
  }

  async health(): Promise<boolean> {
    try {
      await this.call("GET", "v1/admin/health");
      return true;
    } catch {
      return false;
    }
  }

  /** Appends one learning and returns once it is indexed (?wait=indexed). */
  async storeLearning(input: { text: string; learningKind: string; confidence: number; now?: Date }): Promise<{ id: string; eventId: string }> {
    const observedAt = nanos(input.now ?? new Date());
    const id = itemId("learning", input.text);
    const envelope = {
      v: 3,
      id,
      kind: "learning",
      text: "",
      meta: { observed_at: observedAt },
      learning_kind: input.learningKind,
      confidence: input.confidence,
    };
    const parts = slices(JSON.stringify(envelope), 240).map((s, i) => `tm:e:${String(i).padStart(2, "0")}:${s}`);
    const labels = [`tm:i:${digest(id)}`, `tm:k:${digest("learning")}`, "kind:learning", ...parts];
    if (labels.length > 64) throw new CortexError("the learning's envelope does not fit in an event");
    const body = {
      scope: this.scope,
      modality: "observation",
      content: { kind: "message", role: "user", text: input.text },
      context: { labels, observed_at: (input.now ?? new Date()).toISOString() },
    };
    const key = `tm3:${sha256(JSON.stringify(body)).slice(0, 56)}`;
    const answer = (await this.call("POST", "v1/experience", { ...body, idempotency_key: key }, { wait: "indexed" })) as { event_id?: unknown };
    if (typeof answer.event_id !== "string") throw new CortexError("CortexDB took the learning but named no event");
    return { id, eventId: answer.event_id };
  }

  private async eventsPage(cursor: string | undefined, labels?: string[]): Promise<{ items: RawEvent[]; hasMore: boolean; next?: string }> {
    const query: Record<string, string> = { scope: this.scope, limit: "200" };
    if (labels?.length) query.labels = labels.slice(0, 50).join(",");
    if (cursor) query.cursor = cursor;
    const page = (await this.call("GET", "v1/events", undefined, query)) as { items?: RawEvent[]; has_more?: boolean; next_cursor?: string };
    const next = typeof page.next_cursor === "string" ? page.next_cursor : undefined;
    if (next && next === cursor) throw new CortexError("the CortexDB listing does not advance");
    return { items: Array.isArray(page.items) ? page.items : [], hasMore: page.has_more === true && next !== undefined, ...(next ? { next } : {}) };
  }

  /** Learnings, newest first, deduped, written after `observedAfter` when given. The cursor is ours. */
  async listLearnings(opts: { limit: number; cursor?: string; observedAfter?: string }): Promise<ListPage> {
    let at: { c?: string; skip: number } = { skip: 0 };
    if (opts.cursor) {
      try {
        const parsed = JSON.parse(Buffer.from(opts.cursor, "base64url").toString("utf8")) as { c?: string; skip?: number };
        at = { ...(typeof parsed.c === "string" ? { c: parsed.c } : {}), skip: Number.isInteger(parsed.skip) ? (parsed.skip as number) : 0 };
      } catch {
        throw new CortexError("the cursor is not one this listing gave");
      }
    }
    const after = opts.observedAfter ? Date.parse(opts.observedAfter) : undefined;
    const items: Learning[] = [];
    const seen = new Set<string>();
    let cursor = at.c;
    let skip = at.skip;
    for (let pages = 0; pages < 25; pages++) {
      const page = await this.eventsPage(cursor);
      const fresh: Learning[] = [];
      for (const ev of page.items) {
        const d = decodeEvent(ev);
        if (!d || d.kind !== "learning" || seen.has(d.id)) continue;
        seen.add(d.id);
        if (after !== undefined) {
          const t = Date.parse(d.observedAt);
          if (!Number.isNaN(t) && t <= after) continue;
        }
        fresh.push({ id: d.id, kind: "learning", text: d.text, meta: { observed_at: d.observedAt } });
      }
      const slice = fresh.slice(skip);
      const room = opts.limit - items.length;
      items.push(...slice.slice(0, room));
      if (slice.length > room) {
        return { items, next_cursor: Buffer.from(JSON.stringify({ ...(cursor ? { c: cursor } : {}), skip: skip + room })).toString("base64url") };
      }
      skip = 0;
      if (!page.hasMore || !page.next) return { items, next_cursor: null };
      cursor = page.next;
      if (items.length >= opts.limit) {
        return { items, next_cursor: Buffer.from(JSON.stringify({ c: cursor, skip: 0 })).toString("base64url") };
      }
    }
    return { items, next_cursor: cursor ? Buffer.from(JSON.stringify({ c: cursor, skip: 0 })).toString("base64url") : null };
  }

  /** Forgets learnings by item id; returns how many items had events to remove. */
  async forget(ids: string[]): Promise<number> {
    let forgotten = 0;
    for (const id of new Set(ids.map((s) => s.trim()).filter(Boolean))) {
      const labels = [`tm:i:${digest(id)}`];
      const eventIds: string[] = [];
      let cursor: string | undefined;
      for (let pages = 0; pages < 10; pages++) {
        const page = await this.eventsPage(cursor, labels);
        for (const ev of page.items) {
          // The label is a digest: re-check the envelope's real id.
          const d = decodeEvent(ev);
          if (d?.id === id && typeof ev.id === "string" && !eventIds.includes(ev.id)) eventIds.push(ev.id);
        }
        if (!page.hasMore || !page.next) break;
        cursor = page.next;
      }
      for (let i = 0; i < eventIds.length; i += 100) {
        // The selector field is exactly memory_ids: anything else reads as the whole scope.
        await this.call("POST", "v1/forget", {
          scope: this.scope,
          layers: ["events"],
          selector: { memory_ids: eventIds.slice(i, i + 100) },
          cascade: "redact_events",
          audit_note: "teeny: forget",
        });
      }
      if (eventIds.length > 0) forgotten++;
    }
    return forgotten;
  }

  /** The texts recall would put in a turn's context, best first, within `budgetTokens`. */
  async recall(query: string, budgetTokens: number, maxEvents = 30): Promise<string[]> {
    const pack = (await this.call("POST", "v1/recall", {
      scope: this.scope,
      query,
      view: "granular",
      include: ["events"],
      budgets: { max_tokens: budgetTokens, per_layer_limits: { events: maxEvents } },
    })) as { layers?: { events?: RawEvent[] } };
    const out: string[] = [];
    for (const ev of pack.layers?.events ?? []) {
      const d = decodeEvent(ev);
      const text = d ? d.text : (ev.content?.text ?? "").replace(/^\[[a-z]+\]\s/, "");
      if (text.trim()) out.push(text.trim());
    }
    return out;
  }
}
