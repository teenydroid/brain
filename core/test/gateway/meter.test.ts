import { createServer, request, type IncomingMessage, type Server } from "node:http";
import type { AddressInfo } from "node:net";
import { afterEach, describe, expect, it } from "vitest";
import {
  billSseLine,
  billUsage,
  chargeSpan,
  listCostMicro,
  promptCacheKey,
  readPriceBook,
  startMeter,
  type Charge,
  type PriceBook,
} from "../../src/gateway/meter.js";

// What Surplus answered on 2026-11-28 (trimmed), and its published rates for the same models.
const SURPLUS_USAGE = { prompt_tokens: 4145, completion_tokens: 4096, total_tokens: 8241, prompt_tokens_details: { cached_tokens: 2304 }, buyer_cost_micro: 232 };
const BOOK: PriceBook = readPriceBook({
  models: [
    { model: "glm-5.3-flash", providers: [{ provider: "zai", pricing: { input: 0.075, output: 0.25, cacheRead: 0.015 } }, { provider: "openrouter", pricing: { input: 0.12, output: 0.4, cacheRead: 0.03 } }] },
    { model: "claude-opus-5.5", providers: [{ provider: "venice", pricing: { input: 4, output: 20, cacheRead: 0.2, cacheWrite: 5 } }] },
  ],
});

interface OtlpAttr { key: string; value: { stringValue?: string; doubleValue?: number } }
interface OtlpSpan { traceId: string; parentSpanId?: string; name: string; attributes: OtlpAttr[] }
interface OtlpDoc { resourceSpans: Array<{ scopeSpans: Array<{ spans: OtlpSpan[] }> }> }
const firstSpan = (doc: unknown): OtlpSpan => (doc as OtlpDoc).resourceSpans[0]!.scopeSpans[0]!.spans[0]!;

const listen = async (server: Server): Promise<number> => {
  server.listen(0, "127.0.0.1");
  await new Promise((r) => server.once("listening", r));
  return (server.address() as AddressInfo).port;
};
const body = async (m: IncomingMessage): Promise<string> => {
  let s = "";
  for await (const c of m) s += c;
  return s;
};

describe("the prompt cache key", () => {
  const sys = { role: "system", content: "You are the act agent. ".repeat(50) };
  const tools = [{ type: "function", function: { name: "exec" } }];

  it("is the same for two runs of one agent, whatever the conversation and the session", () => {
    const a = promptCacheKey({ model: "glm-5.3-flash", messages: [sys, { role: "user", content: "run 1, 15:54 UTC" }], tools, prompt_cache_key: "session-1" });
    const b = promptCacheKey({ model: "glm-5.3-flash", messages: [sys, { role: "user", content: "run 2, 16:10 UTC" }, { role: "assistant", content: "…" }], tools, prompt_cache_key: "session-2" });
    expect(a).toMatch(/^teeny-[0-9a-f]{32}$/);
    expect(a).toBe(b);
  });

  it("differs when the prefix does: another agent's prompt, other tools, another model", () => {
    const base = promptCacheKey({ model: "glm-5.3-flash", messages: [sys], tools });
    expect(promptCacheKey({ model: "glm-5.3-flash", messages: [{ role: "system", content: "You are the mind." }], tools })).not.toBe(base);
    expect(promptCacheKey({ model: "glm-5.3-flash", messages: [sys], tools: [] })).not.toBe(base);
    expect(promptCacheKey({ model: "claude-opus-5.5", messages: [sys], tools })).not.toBe(base);
  });

  it("is left alone when there is no stable prefix", () => {
    expect(promptCacheKey({ model: "m", messages: [{ role: "user", content: "hi" }] })).toBeUndefined();
  });
});

describe("billing a Surplus answer", () => {
  it("adds Surplus's charge as OpenClaw's provider-billed usage.cost, in USD", () => {
    expect(billUsage(SURPLUS_USAGE)).toEqual({ ...SURPLUS_USAGE, cost: 0.000232 });
    expect(billUsage({ prompt_tokens: 1 })).toEqual({ prompt_tokens: 1 }); // no charge reported: nothing invented
    expect(billUsage({ buyer_cost_micro: 5, cost: 0.1 })).toEqual({ buyer_cost_micro: 5, cost: 0.1 }); // a provider's own cost stands
  });

  it("bills the usage chunk of a stream and passes every other line through", () => {
    const c: Charge = { model: "glm-5.3-flash", status: 200, startMs: 0, endMs: 0 };
    expect(billSseLine('data: {"choices":[{"delta":{"content":"hi"}}]}', c)).toBe('data: {"choices":[{"delta":{"content":"hi"}}]}');
    expect(billSseLine("data: [DONE]", c)).toBe("data: [DONE]");
    expect(JSON.parse(billSseLine(`data: ${JSON.stringify({ choices: [], usage: SURPLUS_USAGE })}`, c).slice(6)).usage.cost).toBe(0.000232);
    expect(c).toMatchObject({ promptTokens: 4145, cachedTokens: 2304, outputTokens: 4096, buyerCostMicro: 232 });
  });

  it("prices the list cost the way Surplus's request log does", () => {
    // The log's direct_cost_micro for these calls: 1928 (glm on mistral, which publishes no rate: OpenRouter's) and 144.
    expect(listCostMicro(BOOK, { model: "glm-5.3-flash", provider: "mistral", status: 200, startMs: 0, endMs: 0, promptTokens: 4145, cachedTokens: 2304, outputTokens: 4096 })).toBe(1928);
    expect(listCostMicro(BOOK, { model: "claude-opus-5.5", provider: "venice", status: 200, startMs: 0, endMs: 0, promptTokens: 11, outputTokens: 5 })).toBe(144);
    expect(listCostMicro(BOOK, { model: "unknown", status: 200, startMs: 0, endMs: 0, promptTokens: 1 })).toBeUndefined();
  });

  it("makes one Langfuse generation under the Gateway's model-call span, with Surplus's tokens, charge and discount", () => {
    const span = chargeSpan({
      model: "glm-5.3-flash",
      status: 200,
      startMs: 1_000,
      endMs: 2_000,
      traceparent: "00-5b8efff798038103d269b633813fc60c-eee19b7ec3c1b174-01",
      promptTokens: 4145,
      cachedTokens: 2304,
      outputTokens: 4096,
      buyerCostMicro: 232,
      directCostMicro: 1928,
      provider: "mistral",
    });
    const s = firstSpan(span);
    expect(s.traceId).toBe("5b8efff798038103d269b633813fc60c");
    expect(s.parentSpanId).toBe("eee19b7ec3c1b174");
    expect(s.name).toBe("surplus.charge");
    const attr = Object.fromEntries(s.attributes.map((a) => [a.key, a.value.stringValue ?? a.value.doubleValue])) as Record<string, string | number | undefined>;
    const json = (k: string): unknown => JSON.parse(String(attr[k]));
    expect(attr["langfuse.observation.type"]).toBe("generation");
    expect(json("langfuse.observation.usage_details")).toEqual({ input: 1841, input_cached_tokens: 2304, output: 4096 });
    expect(json("langfuse.observation.cost_details")).toEqual({ total: 0.000232 });
    expect(attr["langfuse.observation.metadata.cache_hit_pct"]).toBe(55.6);
    expect(attr["langfuse.observation.metadata.list_cost_usd"]).toBe(0.001928);
    expect(attr["langfuse.observation.metadata.discount_pct"]).toBe(88);
    expect(attr["langfuse.observation.metadata.provider"]).toBe("mistral");
  });
});

describe("the meter between the Gateway and the endpoint", () => {
  const servers: Server[] = [];
  let close: (() => Promise<void>) | undefined;
  afterEach(async () => {
    await close?.();
    close = undefined;
    for (const s of servers.splice(0)) await new Promise((r) => s.close(r));
  });

  async function rig(answer: (req: IncomingMessage, sent: string, res: import("node:http").ServerResponse) => void) {
    const seen: Array<{ path: string; headers: IncomingMessage["headers"]; body: string }> = [];
    const upstream = createServer(async (req, res) => {
      const sent = await body(req);
      seen.push({ path: req.url ?? "", headers: req.headers, body: sent });
      answer(req, sent, res);
    });
    const spans: unknown[] = [];
    const collector = createServer(async (req, res) => {
      spans.push(JSON.parse(await body(req)));
      res.end("{}");
    });
    servers.push(upstream, collector);
    const [up, col] = [await listen(upstream), await listen(collector)];
    const charges: Charge[] = [];
    const meter = await startMeter({ upstream: `http://127.0.0.1:${up}/v1`, port: 0, otlpUrl: `http://127.0.0.1:${col}`, prices: async () => BOOK, onCharge: (c) => charges.push(c) });
    close = meter.close;
    return { meter, seen, spans, charges };
  }

  const post = (url: string, payload: unknown, headers: Record<string, string> = {}) =>
    new Promise<{ status: number; text: string }>((resolve, reject) => {
      const data = JSON.stringify(payload);
      const req = request(url, { method: "POST", headers: { "content-type": "application/json", "content-length": String(Buffer.byteLength(data)), ...headers } }, async (res) =>
        resolve({ status: res.statusCode ?? 0, text: await body(res) }),
      );
      req.on("error", reject);
      req.end(data);
    });
  const until = async (ok: () => boolean) => {
    for (let i = 0; i < 100 && !ok(); i++) await new Promise((r) => setTimeout(r, 20));
  };

  it("passes the call through with its key and traceparent, keyed by its prefix, and bills the answer", async () => {
    const { meter, seen, spans } = await rig((_req, _sent, res) => {
      res.writeHead(200, { "content-type": "application/json", "x-si-provider-family": "mistral", "x-si-buyer-cost-micro": "232" });
      res.end(JSON.stringify({ model: "glm-5.3-flash", choices: [{ message: { content: "ok" } }], usage: SURPLUS_USAGE }));
    });
    const tp = "00-5b8efff798038103d269b633813fc60c-eee19b7ec3c1b174-01";
    const answer = await post(`${meter.url}/chat/completions`, { model: "glm-5.3-flash", messages: [{ role: "system", content: "sys" }, { role: "user", content: "hi" }], prompt_cache_key: "session-9" }, { authorization: "Bearer sk-test", traceparent: tp });

    expect(answer.status).toBe(200);
    expect(JSON.parse(answer.text).usage.cost).toBe(0.000232);
    expect(seen[0]!.path).toBe("/v1/chat/completions");
    expect(seen[0]!.headers.authorization).toBe("Bearer sk-test");
    expect(seen[0]!.headers.traceparent).toBe(tp);
    expect(JSON.parse(seen[0]!.body).prompt_cache_key).toMatch(/^teeny-/);

    await until(() => spans.length > 0);
    const s = firstSpan(spans[0]);
    expect(s.parentSpanId).toBe("eee19b7ec3c1b174");
    const attrs = JSON.stringify(s.attributes);
    expect(attrs).toContain("discount_pct");
    expect(attrs).not.toContain("sk-test"); // the key never reaches a span
  });

  it("bills a stream's final usage chunk without holding the stream back", async () => {
    const { meter, charges } = await rig((_req, _sent, res) => {
      res.writeHead(200, { "content-type": "text/event-stream" });
      res.write('data: {"choices":[{"delta":{"content":"o"}}]}\n\n');
      res.write(`data: ${JSON.stringify({ choices: [], usage: SURPLUS_USAGE })}\n\n`);
      res.end("data: [DONE]\n\n");
    });
    const answer = await post(`${meter.url}/chat/completions`, { model: "glm-5.3-flash", stream: true, messages: [{ role: "user", content: "hi" }] });
    const usageLine = answer.text.split("\n").find((l) => l.includes('"usage"')) ?? "";
    expect(JSON.parse(usageLine.slice(6)).usage.cost).toBe(0.000232);
    expect(answer.text).toContain("data: [DONE]");
    await until(() => charges.length > 0);
    expect(charges[0]).toMatchObject({ promptTokens: 4145, cachedTokens: 2304, buyerCostMicro: 232, directCostMicro: 1928 });
  });

  it("passes anything else through untouched, and says so when the endpoint is down", async () => {
    const { meter, seen } = await rig((_req, _sent, res) => {
      res.writeHead(200, { "content-type": "application/json" });
      res.end('{"data":[{"id":"glm-5.3-flash"}]}');
    });
    const models = await new Promise<string>((resolve) => request(`${meter.url}/models`, async (res) => resolve(await body(res))).end());
    expect(models).toBe('{"data":[{"id":"glm-5.3-flash"}]}');
    expect(seen[0]!.path).toBe("/v1/models");

    const down = await startMeter({ upstream: "http://127.0.0.1:9/v1", port: 0 });
    const failed = await post(`${down.url}/chat/completions`, { model: "m", messages: [] });
    await down.close();
    expect(failed.status).toBe(502);
    expect(failed.text).toContain("teeny_meter_error");
  });
});
