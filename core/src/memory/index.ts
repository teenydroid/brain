// memory_engine_set, memory_learn, memory_items_list, memory_forget: the four
// memory methods the Python calls (core/test/contract/memory_*.json), served
// from CortexDB over HTTP. Inside the Gateway, memory is the teeny-cortexdb
// plugin (plugins/teeny-cortexdb): it owns plugins.slots.memory, recalls into
// each turn, captures turns and serves the memory_* tools, reading the engine
// this module stores. These four RPCs stay the Python callers' path.
import { RpcError, type CoreContext, type RpcMethods } from "../server/types.js";
import { assertSafeEndpoint, CortexClient, CortexError, sameEndpoint } from "./cortex.js";
import { clientFor, loadEngine, saveEngine } from "./engine.js";
import { memoryScope } from "./plugin.js";

/** Tests replace this to mock CortexDB's HTTP. */
export const memoryHttp: { fetch: typeof fetch | undefined } = { fetch: undefined };

function scopeOf(ctx: CoreContext): string {
  return memoryScope(ctx.env);
}

function client(ctx: CoreContext): CortexClient {
  let c: CortexClient | undefined;
  try {
    c = clientFor(loadEngine(ctx.stateDir), ctx.services?.sealer, scopeOf(ctx), memoryHttp.fetch);
  } catch (e) {
    if (e instanceof CortexError) throw new RpcError(`memory: ${e.message}`);
    throw e;
  }
  if (!c) throw new RpcError("memory is not configured: call memory_engine_set first", -32000);
  return c;
}

function wrap<T>(fn: () => Promise<T>): Promise<T> {
  return fn().catch((e: unknown) => {
    if (e instanceof CortexError) throw new RpcError(`memory: ${e.message}`);
    throw e;
  });
}

export const memoryMethods: RpcMethods = {
  // {engine: "cortexdb", endpoint, api_key?} -> {status}
  async memory_engine_set(p, ctx) {
    if (p.engine !== "cortexdb") throw new RpcError("engine must be cortexdb", -32602);
    if (typeof p.endpoint !== "string" || !/^https?:\/\/\S+$/.test(p.endpoint)) throw new RpcError("endpoint must be an http(s) URL", -32602);
    try {
      assertSafeEndpoint(p.endpoint);
    } catch (e) {
      throw new RpcError((e as Error).message, -32602);
    }
    if (p.api_key !== undefined && typeof p.api_key !== "string") throw new RpcError("api_key must be a string", -32602);
    let sealed: string | undefined;
    if (typeof p.api_key === "string" && p.api_key !== "") {
      const sealer = ctx.services?.sealer;
      if (!sealer) throw new RpcError("no master key: cannot store the CortexDB key", -32000);
      sealed = sealer.seal(p.api_key);
    } else {
      // No key given: keep the one already stored for the same endpoint, as a boot without CORTEX_API_KEY expects.
      // A different endpoint never inherits it: the health check below would hand the old service's key to the new one.
      const prev = loadEngine(ctx.stateDir);
      sealed = prev && sameEndpoint(prev.endpoint, p.endpoint) ? prev.apiKeySealed : undefined;
    }
    saveEngine(ctx.stateDir, { engine: "cortexdb", endpoint: p.endpoint, scope: scopeOf(ctx), ...(sealed ? { apiKeySealed: sealed } : {}) });
    const stored = loadEngine(ctx.stateDir);
    // The Gateway's config names the memory plugin only once an engine is set: write it now (debounced, and a
    // restart only when the config really differs), so a first boot's memory reaches the Gateway without a second boot.
    ctx.services?.configChanged();
    const c = clientFor(stored, ctx.services?.sealer, scopeOf(ctx), memoryHttp.fetch);
    const ready = c ? await c.health() : false;
    return { status: ready ? "ready" : "unreachable" };
  },

  // {text, kind, confidence} -> {id}
  async memory_learn(p, ctx) {
    if (typeof p.text !== "string" || p.text.trim() === "") throw new RpcError("text is required", -32602);
    const kind = typeof p.kind === "string" && p.kind ? p.kind : "other";
    const confidence = typeof p.confidence === "number" && p.confidence >= 0 && p.confidence <= 1 ? p.confidence : 0.8;
    const c = client(ctx);
    const got = await wrap(() => c.storeLearning({ text: p.text as string, learningKind: kind, confidence }));
    return { id: got.id };
  },

  // {limit, preview, filter: {kinds, observed_after}, cursor?} -> {items, next_cursor}
  async memory_items_list(p, ctx) {
    const limit = typeof p.limit === "number" && Number.isInteger(p.limit) && p.limit > 0 ? Math.min(p.limit, 500) : 50;
    const filter = (p.filter ?? {}) as { kinds?: unknown; observed_after?: unknown };
    // Only learnings are kept here; another kind is an empty list, not an error.
    if (Array.isArray(filter.kinds) && !filter.kinds.includes("learning")) return { items: [], next_cursor: null };
    const c = client(ctx);
    return wrap(() =>
      c.listLearnings({
        limit,
        ...(typeof p.cursor === "string" && p.cursor ? { cursor: p.cursor } : {}),
        ...(typeof filter.observed_after === "string" ? { observedAfter: filter.observed_after } : {}),
      }),
    );
  },

  // {ids: [string]} -> {forgotten}
  async memory_forget(p, ctx) {
    if (!Array.isArray(p.ids) || p.ids.some((i) => typeof i !== "string")) throw new RpcError("ids must be a list of strings", -32602);
    if (!p.ids.some((i) => (i as string).trim() !== "")) throw new RpcError("forget needs at least one id", -32602);
    const c = client(ctx);
    return { forgotten: await wrap(() => c.forget(p.ids as string[])) };
  },
};
