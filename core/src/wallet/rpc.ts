// JSON-RPC over an injectable fetch. Errors carry the method and the node's
// message, never the URL (it may hold an API key) and never the request.
import type { Deps } from "./endpoints.js";

const TIMEOUT_MS = 30_000;

export async function jsonRpc<T>(deps: Deps, url: string, method: string, params: unknown): Promise<T> {
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
  let body: { result?: T; error?: { message?: string } };
  try {
    body = (await response.json()) as typeof body;
  } catch {
    throw new Error(`rpc ${method}: the node sent something that is not JSON`);
  }
  if (body.error) throw new Error(`rpc ${method}: ${String(body.error.message ?? "error").slice(0, 300)}`);
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
