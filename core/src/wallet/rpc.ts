// JSON-RPC over an injectable fetch. Errors carry the method and the node's
// numeric error code, never the node's text (it may echo the URL, which can hold
// an API key), never the URL and never the request.
import type { Deps } from "./endpoints.js";

const TIMEOUT_MS = 30_000;

/** True when URL may carry a credential safely: https, or http to a loopback node with nothing secret in the URL. */
export function transportAllowed(url: string): boolean {
  let u: URL;
  try {
    u = new URL(url);
  } catch {
    return false;
  }
  if (u.protocol === "https:") return true;
  if (u.protocol !== "http:") return false;
  const loopback = u.hostname === "localhost" || u.hostname === "127.0.0.1" || u.hostname === "[::1]";
  return loopback && u.username === "" && u.password === "" && u.search === "" && (u.pathname === "/" || u.pathname === "");
}

export async function jsonRpc<T>(deps: Deps, url: string, method: string, params: unknown, exactKeys: readonly string[] = []): Promise<T> {
  if (!transportAllowed(url)) throw new Error(`rpc ${method}: the node endpoint must be https (http only for a plain loopback node)`);
  let response: Response;
  try {
    response = await deps.fetch(url, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ jsonrpc: "2.0", id: 1, method, params }),
      signal: AbortSignal.timeout(TIMEOUT_MS),
      redirect: "error",
    });
  } catch {
    throw new Error(`rpc ${method}: could not reach the node`);
  }
  if (!response.ok) throw new Error(`rpc ${method}: the node answered HTTP ${response.status}`);
  let body: { result?: T; error?: { code?: unknown } };
  try {
    let text = await response.text();
    // A u64 past 2^53 would be rounded by JSON.parse: for the named keys keep the digits as a string.
    for (const key of exactKeys) text = text.replace(new RegExp(`("${key}"\\s*:\\s*)(\\d{16,})(?=\\s*[,}\\]])`, "g"), '$1"$2"');
    body = JSON.parse(text) as typeof body;
  } catch {
    throw new Error(`rpc ${method}: the node sent something that is not JSON`);
  }
  if (body.error) {
    const code = body.error.code;
    throw new Error(`rpc ${method}: the node reported an error${typeof code === "number" && Number.isSafeInteger(code) ? ` (code ${code})` : ""}`);
  }
  if (body.result === undefined) throw new Error(`rpc ${method}: the node sent no result`);
  return body.result;
}

export class SolanaRpc {
  constructor(
    private readonly deps: Deps,
    private readonly url: string,
  ) {}

  call<T>(method: string, params: unknown): Promise<T> {
    return jsonRpc<T>(this.deps, this.url, method, params);
  }

  /** Like call, but the integers under EXACTKEYS reach the caller as exact decimal strings. */
  callExact<T>(method: string, params: unknown, exactKeys: readonly string[]): Promise<T> {
    return jsonRpc<T>(this.deps, this.url, method, params, exactKeys);
  }
}

export class EvmRpc {
  constructor(
    private readonly deps: Deps,
    private readonly url: string,
  ) {}

  call<T>(method: string, params: unknown[] = []): Promise<T> {
    return jsonRpc<T>(this.deps, this.url, method, params);
  }
}
