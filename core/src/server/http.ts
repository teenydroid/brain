// The HTTP side: GET /health and POST /rpc (JSON-RPC 2.0, bearer token). The
// transport is the one core/test/contract/_rpc_envelope.json records and
// bin/oh speaks: a request {jsonrpc, id, method: "openhuman.<m>", params};
// an answer {jsonrpc, id, result} or {jsonrpc, id, error: {code, message}}.
import { timingSafeEqual } from "node:crypto";
import { createServer, type IncomingMessage, type Server, type ServerResponse } from "node:http";
import type { RpcHandler, CoreContext } from "./types.js";
import { RpcError } from "./types.js";

const MAX_BODY = 16 * 1024 * 1024;
const PREFIX = "openhuman.";

export interface HttpOptions {
  registry: ReadonlyMap<string, RpcHandler>;
  token: string;
  ctx: CoreContext;
}

function sameToken(given: string, expected: string): boolean {
  const a = Buffer.from(given);
  const b = Buffer.from(expected);
  return a.length === b.length && timingSafeEqual(a, b);
}

function send(res: ServerResponse, status: number, body: unknown): void {
  const text = JSON.stringify(body);
  res.writeHead(status, { "content-type": "application/json", "content-length": Buffer.byteLength(text) });
  res.end(text);
}

function readBody(req: IncomingMessage): Promise<string> {
  return new Promise((resolve, reject) => {
    const chunks: Buffer[] = [];
    let size = 0;
    req.on("data", (c: Buffer) => {
      size += c.length;
      if (size > MAX_BODY) {
        reject(new RpcError("request body too large", -32600));
        req.destroy();
        return;
      }
      chunks.push(c);
    });
    req.on("end", () => resolve(Buffer.concat(chunks).toString("utf8")));
    req.on("error", reject);
  });
}

const rpcError = (id: unknown, code: number, message: string, data?: unknown) => ({
  jsonrpc: "2.0",
  id: id ?? null,
  error: { code, message, ...(data !== undefined ? { data } : {}) },
});

export async function handleRpc(opts: HttpOptions, raw: string): Promise<unknown> {
  let req: unknown;
  try {
    req = JSON.parse(raw);
  } catch {
    return rpcError(null, -32700, "parse error");
  }
  if (typeof req !== "object" || req === null || Array.isArray(req)) return rpcError(null, -32600, "invalid request");
  const { id, method, params } = req as { id?: unknown; method?: unknown; params?: unknown };
  if (typeof method !== "string") return rpcError(id, -32600, "invalid request: method must be a string");
  const name = method.startsWith(PREFIX) ? method.slice(PREFIX.length) : method;
  const handler = opts.registry.get(name);
  if (!handler) return rpcError(id, -32601, `method not found: ${method}`);
  if (params !== undefined && (typeof params !== "object" || params === null || Array.isArray(params))) {
    return rpcError(id, -32602, "params must be an object");
  }
  const started = Date.now();
  try {
    const result = await handler((params ?? {}) as Record<string, unknown>, opts.ctx);
    opts.ctx.log("info", "rpc", { method: name, ms: Date.now() - started });
    return { jsonrpc: "2.0", id: id ?? null, result: result === undefined ? null : result };
  } catch (e) {
    // The message is quoted by callers (they match WALLET_POLICY_REFUSED: and similar); params are never logged.
    const code = e instanceof RpcError ? e.code : -32000;
    const message = e instanceof Error ? e.message : String(e);
    opts.ctx.log("warn", "rpc failed", { method: name, ms: Date.now() - started, code });
    return rpcError(id, code, message, e instanceof RpcError ? e.data : undefined);
  }
}

export function createHttpServer(opts: HttpOptions): Server {
  const server = createServer((req, res) => {
    void (async () => {
      const url = (req.url ?? "").split("?")[0];
      if (req.method === "GET" && url === "/health") return send(res, 200, { ok: true });
      if (url !== "/rpc") return send(res, 404, { error: "not found" });
      if (req.method !== "POST") return send(res, 405, { error: "method not allowed" });
      const auth = req.headers.authorization ?? "";
      const given = auth.startsWith("Bearer ") ? auth.slice(7).trim() : "";
      if (!given || !sameToken(given, opts.token)) return send(res, 401, { error: "unauthorized" });
      let body: string;
      try {
        body = await readBody(req);
      } catch (e) {
        return send(res, 413, rpcError(null, -32600, e instanceof Error ? e.message : "bad body"));
      }
      send(res, 200, await handleRpc(opts, body));
    })().catch((e: unknown) => {
      opts.ctx.log("error", "http handler crashed", { error: String(e) });
      if (!res.headersSent) send(res, 500, { error: "internal error" });
    });
  });
  // An agent turn lasts minutes: no request or socket timeout of ours.
  server.requestTimeout = 0;
  server.timeout = 0;
  server.headersTimeout = 60_000;
  return server;
}
