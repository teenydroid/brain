// A CortexDB v0.10.4 stand-in over real HTTP on loopback, for the teeny-cortexdb plugin: the same
// wire core/src/memory/cortex.ts speaks (events by scope with a doubled listing, forget by memory_ids,
// recall as layers.events). Every request is kept so a test can say what did and did not go out.
import { createServer, type IncomingMessage, type Server } from "node:http";
import type { AddressInfo } from "node:net";

export interface Event {
  id: string;
  scope: string;
  content: { text: string };
  context: { labels: string[]; observed_at?: string };
}

export interface Seen {
  method: string;
  path: string;
  query: Record<string, string>;
  body?: Record<string, unknown>;
  auth: string | undefined;
}

export class FakeCortexServer {
  events: Event[] = [];
  seen: Seen[] = [];
  /** Answer every request with 503. */
  down = false;
  /** Hold every answer back this long (ms). */
  delayMs = 0;
  private n = 0;
  private server: Server | undefined;
  url = "";

  async start(): Promise<void> {
    this.server = createServer((req, res) => void this.handle(req, res));
    await new Promise<void>((r) => this.server?.listen(0, "127.0.0.1", r));
    this.url = `http://127.0.0.1:${(this.server.address() as AddressInfo).port}`;
  }

  async stop(): Promise<void> {
    this.server?.closeAllConnections();
    await new Promise<void>((r) => (this.server ? this.server.close(() => r()) : r()));
  }

  /** Requests to one route, in order. */
  to(method: string, path: string): Seen[] {
    return this.seen.filter((s) => s.method === method && s.path === path);
  }

  private async handle(req: IncomingMessage, res: import("node:http").ServerResponse): Promise<void> {
    const chunks: Buffer[] = [];
    for await (const c of req) chunks.push(c as Buffer);
    const url = new URL(req.url ?? "/", "http://x");
    const raw = Buffer.concat(chunks).toString("utf8");
    const body = raw ? (JSON.parse(raw) as Record<string, unknown>) : undefined;
    const query = Object.fromEntries(url.searchParams);
    this.seen.push({ method: req.method ?? "GET", path: url.pathname, query, ...(body ? { body } : {}), auth: req.headers.authorization });
    if (this.delayMs) await new Promise((r) => setTimeout(r, this.delayMs));
    const json = (v: unknown, status = 200): void => {
      res.writeHead(status, { "content-type": "application/json" });
      res.end(JSON.stringify(v));
    };
    if (this.down) return json({ error: "down" }, 503);
    switch (`${req.method} ${url.pathname}`) {
      case "GET /v1/admin/health":
        return json({ status: "ok" });
      case "GET /v1/auth/whoami":
        return json({ caller: "user:local" });
      case "POST /v1/experience": {
        const ctx = body?.context as { labels: string[]; observed_at?: string };
        const ev: Event = { id: `evt_${++this.n}`, scope: String(body?.scope), content: { text: (body?.content as { text: string }).text }, context: ctx };
        this.events.unshift(ev);
        return json({ event_id: ev.id, status: "accepted" }, 202);
      }
      case "GET /v1/events": {
        let items = this.events.filter((e) => e.scope === query.scope);
        if (query.labels) {
          const want = query.labels.split(",");
          items = items.filter((e) => e.context.labels.some((l) => want.includes(l)));
        }
        const start = query.cursor ? Number(query.cursor) : 0;
        const doubled = items.flatMap((e) => [e, e]); // the listing emits every event twice
        const page = doubled.slice(start, start + Math.min(Number(query.limit ?? 200), 200));
        const more = start + page.length < doubled.length;
        return json({ items: page, has_more: more, ...(more ? { next_cursor: String(start + page.length) } : {}) });
      }
      case "POST /v1/forget": {
        const ids = ((body?.selector as { memory_ids?: string[] } | undefined)?.memory_ids ?? []) as string[];
        this.events = this.events.filter((e) => !ids.includes(e.id));
        return json({ forgotten: ids.length });
      }
      case "POST /v1/recall": {
        const words = String(body?.query ?? "").toLowerCase().split(/\W+/).filter((w) => w.length > 2);
        const hits = this.events.filter((e) => e.scope === body?.scope && words.some((w) => e.content.text.toLowerCase().includes(w)));
        return json({ pack_id: "p", layers: { events: hits.slice(0, 30) } });
      }
      default:
        return json({ error: "nope" }, 404);
    }
  }
}
