// The model meter: a pass-through on loopback between the Gateway and the model endpoint (Surplus Intelligence), so
// every model call is billed, cached and traced the way Surplus actually served it.
//
// The Gateway's `local-openai` provider points here (main.ts); each request goes on to LOCAL_OPENAI_URL unchanged
// except for two things, and each answer comes back unchanged except for one:
//
// - prompt_cache_key: set to a hash of the request's stable prefix (system prompt and tool definitions). OpenClaw
//   would key by session, and a cron run is a new session every time, so two runs of one agent, which share a prompt
//   of tens of thousands of tokens, never shared a key. Upstream providers use the key to route a request back to
//   the cache that holds its prefix.
// - usage.cost: Surplus reports what it charged as usage.buyer_cost_micro (integer micro-USD). OpenClaw takes a
//   numeric usage.cost as the provider-billed total and prefers it to its own estimate, so the meter adds it: the
//   Gateway's usage, the core's cost ledger and cost_report then carry the real charge.
//
// With tracing on (gateway/otel.ts), each call also becomes one `surplus.charge` generation in Langfuse, a child of
// the Gateway's model-call span (OpenClaw sends that span's traceparent with the request). It carries Surplus's own
// token counts and charge, the provider that served it, and the list price the charge was discounted from (the
// call's tokens at that provider's published rate, from Surplus's public GET /v1/prices: the figure Surplus's own
// request log calls direct_cost_micro). It is the one place Langfuse counts tokens and money: the collector strips the
// Gateway's own usage figures (deploy/langfuse/otel-collector.yaml), which would count every call twice.
//
// No key is held here: the Gateway's Authorization header passes through untouched.
import { createHash, randomBytes } from "node:crypto";
import { createServer, type IncomingMessage, type Server, type ServerResponse } from "node:http";
import { request as httpsRequest } from "node:https";
import { request as httpRequest, type IncomingHttpHeaders } from "node:http";
import type { AddressInfo } from "node:net";

export const METER_PORT = 18790;

/** Headers a hop must not forward (RFC 9110 §7.6.1), plus the ones the meter recomputes. */
const HOP = new Set(["connection", "keep-alive", "proxy-connection", "transfer-encoding", "te", "trailer", "upgrade", "host", "content-length", "accept-encoding"]);

export interface Charge {
  model: string;
  status: number;
  startMs: number;
  endMs: number;
  /** W3C traceparent the Gateway sent, if any. */
  traceparent?: string;
  promptTokens?: number;
  cachedTokens?: number;
  outputTokens?: number;
  buyerCostMicro?: number;
  provider?: string;
  requestId?: string;
  /** The call's tokens at the serving provider's published rate (Surplus's direct_cost_micro), when known. */
  directCostMicro?: number;
}

export interface MeterOptions {
  /** The real endpoint, e.g. https://api.surplusintelligence.ai/v1 (LOCAL_OPENAI_URL). */
  upstream: string;
  port?: number;
  /** OTLP/HTTP base (TEENY_OTEL_URL); no spans without it. */
  otlpUrl?: string;
  /** Called with each finished charge, after the span is sent (tests, logs). */
  onCharge?: (c: Charge) => void;
  log?: (level: "warn", msg: string, fields?: Record<string, unknown>) => void;
  /** Published rates per model and provider (GET /v1/prices); fetched from the upstream when not given. */
  prices?: () => Promise<PriceBook | undefined>;
}

/** The stable prefix of a chat request: its system and developer messages and its tools, which repeat across runs. */
export function promptCacheKey(body: Record<string, unknown>): string | undefined {
  const messages = Array.isArray(body.messages) ? (body.messages as Array<Record<string, unknown>>) : [];
  const head: unknown[] = [];
  for (const m of messages) {
    if (m.role !== "system" && m.role !== "developer") break;
    head.push(m.content);
  }
  if (head.length === 0 && !body.tools) return undefined;
  const h = createHash("sha256").update(JSON.stringify([body.model ?? "", head, body.tools ?? null])).digest("hex");
  return `teeny-${h.slice(0, 32)}`;
}

/** The request body with the prefix's cache key in place of OpenClaw's per-session one. */
export function withCacheKey(raw: Buffer): Buffer {
  let body: Record<string, unknown>;
  try {
    body = JSON.parse(raw.toString("utf8")) as Record<string, unknown>;
  } catch {
    return raw;
  }
  if (!body || typeof body !== "object" || Array.isArray(body)) return raw;
  const key = promptCacheKey(body);
  if (!key) return raw;
  return Buffer.from(JSON.stringify({ ...body, prompt_cache_key: key }), "utf8");
}

/** A usage object with Surplus's charge added as OpenClaw's provider-billed usage.cost (USD). */
export function billUsage(usage: unknown): unknown {
  if (!usage || typeof usage !== "object") return usage;
  const u = usage as Record<string, unknown>;
  const micro = u.buyer_cost_micro;
  if (typeof micro !== "number" || !Number.isFinite(micro) || micro < 0 || u.cost !== undefined) return usage;
  return { ...u, cost: micro / 1_000_000 };
}

/** Reads the token counts and charge off one usage object into the charge. */
function noteUsage(c: Charge, usage: unknown): void {
  if (!usage || typeof usage !== "object") return;
  const u = usage as Record<string, unknown>;
  const num = (v: unknown): number | undefined => (typeof v === "number" && Number.isFinite(v) && v >= 0 ? v : undefined);
  c.promptTokens = num(u.prompt_tokens) ?? c.promptTokens;
  c.outputTokens = num(u.completion_tokens) ?? c.outputTokens;
  const details = u.prompt_tokens_details as Record<string, unknown> | undefined;
  c.cachedTokens = num(details?.cached_tokens) ?? c.cachedTokens;
  c.buyerCostMicro = num(u.buyer_cost_micro) ?? c.buyerCostMicro;
}

/** One JSON answer, billed. */
export function billJson(text: string, c: Charge): string {
  let doc: Record<string, unknown>;
  try {
    doc = JSON.parse(text) as Record<string, unknown>;
  } catch {
    return text;
  }
  if (!doc || typeof doc !== "object" || !("usage" in doc)) return text;
  noteUsage(c, doc.usage);
  if (typeof doc.model === "string") c.model = doc.model;
  return JSON.stringify({ ...doc, usage: billUsage(doc.usage) });
}

/** One SSE line, billed when it is a data line carrying usage. */
export function billSseLine(line: string, c: Charge): string {
  if (!line.startsWith("data:")) return line;
  const payload = line.slice(5).trim();
  if (!payload.startsWith("{") || !payload.includes('"usage"')) return line;
  const billed = billJson(payload, c);
  return billed === payload ? line : `data: ${billed}`;
}

function parseTraceparent(tp: string | undefined): { traceId: string; spanId: string } | undefined {
  const m = /^[0-9a-f]{2}-([0-9a-f]{32})-([0-9a-f]{16})-[0-9a-f]{2}$/.exec((tp ?? "").trim().toLowerCase());
  const [traceId, spanId] = [m?.[1], m?.[2]];
  if (!traceId || !spanId || /^0+$/.test(traceId) || /^0+$/.test(spanId)) return undefined;
  return { traceId, spanId };
}

/** The `surplus.charge` span for one call, as OTLP/JSON. */
export function chargeSpan(c: Charge): Record<string, unknown> {
  const parent = parseTraceparent(c.traceparent);
  const str = (key: string, v: string) => ({ key, value: { stringValue: v } });
  const dbl = (key: string, v: number) => ({ key, value: { doubleValue: v } });
  const prompt = c.promptTokens ?? 0;
  const cached = Math.min(c.cachedTokens ?? 0, prompt);
  const usage: Record<string, number> = { input: prompt - cached, input_cached_tokens: cached };
  if (c.outputTokens !== undefined) usage.output = c.outputTokens;
  const attrs = [
    str("langfuse.observation.type", "generation"),
    str("langfuse.observation.model.name", c.model),
    str("gen_ai.request.model", c.model),
    str("langfuse.observation.usage_details", JSON.stringify(usage)),
    dbl("langfuse.observation.metadata.http_status", c.status),
  ];
  if (c.buyerCostMicro !== undefined) {
    attrs.push(str("langfuse.observation.cost_details", JSON.stringify({ total: c.buyerCostMicro / 1_000_000 })));
  }
  if (prompt > 0) attrs.push(dbl("langfuse.observation.metadata.cache_hit_pct", Math.round((cached / prompt) * 1000) / 10));
  if (c.directCostMicro !== undefined) {
    attrs.push(dbl("langfuse.observation.metadata.list_cost_usd", c.directCostMicro / 1_000_000));
    if (c.buyerCostMicro !== undefined && c.directCostMicro > 0) {
      attrs.push(dbl("langfuse.observation.metadata.discount_pct", Math.round((1 - c.buyerCostMicro / c.directCostMicro) * 1000) / 10));
    }
  }
  if (c.provider) attrs.push(str("langfuse.observation.metadata.provider", c.provider));
  if (c.requestId) attrs.push(str("langfuse.observation.metadata.surplus_request_id", c.requestId));
  if (c.status >= 400) attrs.push(str("langfuse.observation.level", "ERROR"));
  return {
    resourceSpans: [
      {
        resource: { attributes: [str("service.name", "teeny")] },
        scopeSpans: [
          {
            scope: { name: "teeny.meter" },
            spans: [
              {
                traceId: parent?.traceId ?? randomBytes(16).toString("hex"),
                spanId: randomBytes(8).toString("hex"),
                ...(parent ? { parentSpanId: parent.spanId } : {}),
                name: "surplus.charge",
                kind: 3,
                startTimeUnixNano: `${BigInt(c.startMs) * 1_000_000n}`,
                endTimeUnixNano: `${BigInt(Math.max(c.endMs, c.startMs)) * 1_000_000n}`,
                attributes: attrs,
                status: { code: c.status >= 400 ? 2 : 1 },
              },
            ],
          },
        ],
      },
    ],
  };
}

function forwardHeaders(h: IncomingHttpHeaders): Record<string, string | string[]> {
  const out: Record<string, string | string[]> = {};
  for (const [k, v] of Object.entries(h)) if (v !== undefined && !HOP.has(k.toLowerCase())) out[k] = v;
  out["accept-encoding"] = "identity";
  return out;
}

function send(url: URL, method: string, headers: Record<string, string | string[]>, body?: Buffer): Promise<IncomingMessage> {
  return new Promise((resolve, reject) => {
    const req = (url.protocol === "https:" ? httpsRequest : httpRequest)(url, { method, headers }, resolve);
    req.on("error", reject);
    req.end(body);
  });
}

async function readAll(stream: IncomingMessage): Promise<Buffer> {
  const parts: Buffer[] = [];
  for await (const chunk of stream) parts.push(chunk as Buffer);
  return Buffer.concat(parts);
}

/** USD per million tokens, by model, then by provider: Surplus's published reference rates. */
export type PriceBook = Record<string, Record<string, { input?: number; output?: number; cacheRead?: number; cacheWrite?: number }>>;

/** GET /v1/prices as a PriceBook. */
export function readPriceBook(doc: unknown): PriceBook {
  const book: PriceBook = {};
  const models = (doc as { models?: unknown })?.models;
  if (!Array.isArray(models)) return book;
  for (const m of models as Array<Record<string, unknown>>) {
    if (typeof m.model !== "string" || !Array.isArray(m.providers)) continue;
    const row: PriceBook[string] = {};
    for (const p of m.providers as Array<Record<string, unknown>>) {
      if (typeof p.provider === "string" && p.pricing && typeof p.pricing === "object") row[p.provider] = p.pricing as PriceBook[string][string];
    }
    book[m.model] = row;
  }
  return book;
}

/**
 * What the call would have cost at list price, in micro-USD: its tokens at the serving provider's published rate,
 * or OpenRouter's when that provider publishes none. That is how Surplus's request log prices direct_cost_micro
 * (checked against it, 2026-11-28: glm-5.3-flash on mistral at OpenRouter's rate, claude-opus-5.5 on venice at
 * venice's).
 */
export function listCostMicro(book: PriceBook | undefined, c: Charge): number | undefined {
  const rates = book?.[c.model];
  if (!rates) return undefined;
  const rate = (c.provider && rates[c.provider]) || rates.openrouter;
  if (!rate || typeof rate.input !== "number" || typeof rate.output !== "number" || c.promptTokens === undefined) return undefined;
  const cached = Math.min(c.cachedTokens ?? 0, c.promptTokens);
  const usd = (c.promptTokens - cached) * rate.input + cached * (rate.cacheRead ?? rate.input) + (c.outputTokens ?? 0) * rate.output;
  return Math.round(usd);
}

async function exportSpan(otlpUrl: string, span: Record<string, unknown>): Promise<void> {
  const res = await send(new URL(`${otlpUrl.replace(/\/+$/, "")}/v1/traces`), "POST", { "content-type": "application/json" }, Buffer.from(JSON.stringify(span)));
  res.resume();
}

export async function startMeter(opts: MeterOptions): Promise<{ url: string; port: number; close: () => Promise<void> }> {
  const upstream = new URL(opts.upstream.replace(/\/+$/, ""));
  const log = opts.log ?? (() => undefined);
  // The published rates move slowly; one read an hour.
  let book: { at: number; book: PriceBook | undefined } | undefined;
  const fetchPrices =
    opts.prices ??
    (async (): Promise<PriceBook | undefined> => {
      const res = await send(new URL(`${upstream.href.replace(/\/+$/, "")}/prices`), "GET", { accept: "application/json" });
      const text = (await readAll(res)).toString("utf8");
      return res.statusCode === 200 ? readPriceBook(JSON.parse(text)) : undefined;
    });
  const prices = async (): Promise<PriceBook | undefined> => {
    if (!book || Date.now() - book.at > 3_600_000) {
      book = { at: Date.now(), book: await fetchPrices().catch(() => book?.book) };
    }
    return book.book;
  };
  const settle = async (c: Charge): Promise<void> => {
    if (opts.otlpUrl) {
      c.directCostMicro = listCostMicro(await prices(), c);
      await exportSpan(opts.otlpUrl, chargeSpan(c)).catch((e: unknown) => log("warn", "the meter could not send a charge span", { error: e instanceof Error ? e.message : String(e) }));
    }
    opts.onCharge?.(c);
  };

  const handle = async (req: IncomingMessage, res: ServerResponse): Promise<void> => {
    const startMs = Date.now();
    const raw = await readAll(req);
    const isChat = req.method === "POST" && /\/chat\/completions$/.test((req.url ?? "").split("?")[0] ?? "");
    const body = isChat ? withCacheKey(raw) : raw;
    let requested = "";
    try {
      requested = String((JSON.parse(raw.toString("utf8")) as Record<string, unknown>).model ?? "");
    } catch {
      // not JSON: forwarded as is
    }
    const target = new URL(`${upstream.pathname.replace(/\/+$/, "")}${(req.url ?? "/").replace(/^\/v1(?=\/|$)/, "")}`, upstream);
    const headers = forwardHeaders(req.headers);
    if (body.length) headers["content-length"] = String(body.length);
    const traceparent = typeof req.headers.traceparent === "string" ? req.headers.traceparent : undefined;
    const charge: Charge = { model: requested, status: 0, startMs, endMs: startMs, traceparent };

    let up: IncomingMessage;
    try {
      up = await send(target, req.method ?? "GET", headers, body.length ? body : undefined);
    } catch (e) {
      res.writeHead(502, { "content-type": "application/json" });
      res.end(JSON.stringify({ error: { message: `the meter could not reach the model endpoint: ${e instanceof Error ? e.message : String(e)}`, type: "teeny_meter_error" } }));
      return;
    }
    charge.status = up.statusCode ?? 502;
    charge.requestId = (up.headers["x-request-id"] as string | undefined) ?? undefined;
    charge.provider = (up.headers["x-si-provider-family"] as string | undefined) ?? undefined;
    const header = Number(up.headers["x-si-buyer-cost-micro"]);
    if (Number.isFinite(header) && header >= 0) charge.buyerCostMicro = header;
    const outHeaders: Record<string, string | string[]> = {};
    for (const [k, v] of Object.entries(up.headers)) if (v !== undefined && !HOP.has(k.toLowerCase())) outHeaders[k] = v;
    const type = String(up.headers["content-type"] ?? "");

    if (isChat && type.includes("text/event-stream")) {
      res.writeHead(charge.status, outHeaders);
      let rest = "";
      up.setEncoding("utf8");
      for await (const chunk of up) {
        const lines = (rest + (chunk as string)).split("\n");
        rest = lines.pop() ?? "";
        res.write(lines.map((l) => billSseLine(l, charge)).join("\n") + (lines.length ? "\n" : ""));
      }
      if (rest) res.write(billSseLine(rest, charge));
      res.end();
    } else if (isChat && type.includes("json")) {
      const text = billJson((await readAll(up)).toString("utf8"), charge);
      outHeaders["content-length"] = String(Buffer.byteLength(text));
      res.writeHead(charge.status, outHeaders);
      res.end(text);
    } else {
      res.writeHead(charge.status, outHeaders);
      up.pipe(res);
      await once(up, "end");
      return;
    }
    charge.endMs = Date.now();
    void settle(charge).catch(() => undefined);
  };

  const server: Server = createServer((req, res) => {
    handle(req, res).catch((e: unknown) => {
      log("warn", "the meter dropped a request", { error: e instanceof Error ? e.message : String(e) });
      if (!res.headersSent) res.writeHead(502);
      res.end();
    });
  });
  server.listen(opts.port ?? METER_PORT, "127.0.0.1");
  await once(server, "listening");
  const port = (server.address() as AddressInfo).port;
  return {
    url: `http://127.0.0.1:${port}/v1`,
    port,
    close: () =>
      new Promise((r) => {
        server.closeAllConnections();
        server.close(() => r());
      }),
  };
}

function once(emitter: NodeJS.EventEmitter, event: string): Promise<void> {
  return new Promise((resolve, reject) => {
    emitter.once(event, () => resolve());
    emitter.once("error", reject);
  });
}
