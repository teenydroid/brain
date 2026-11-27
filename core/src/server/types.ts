// The shape every domain module (agents, cron, memory, wallet, ...) exports so
// the RPC server can serve it as `openhuman.<method>`. The method names and the
// JSON in and out are the contract in core/test/contract/.

export interface CoreContext {
  /** Process environment as the core saw it at boot (never contains secrets' values in logs). */
  env: Readonly<Record<string, string | undefined>>;
  /** Repo root, e.g. /srv/teeny. */
  root: string;
  /** Teeny's state directory (state/). */
  stateDir: string;
  log: (level: "debug" | "info" | "warn" | "error", msg: string, fields?: Record<string, unknown>) => void;
}

/** Throw this from a handler to answer with a JSON-RPC error. */
export class RpcError extends Error {
  constructor(
    message: string,
    public readonly code: number = -32000,
    public readonly data?: unknown,
  ) {
    super(message);
    this.name = "RpcError";
  }
}

export type RpcHandler = (params: Record<string, unknown>, ctx: CoreContext) => Promise<unknown>;

/** A domain module: method name without the `openhuman.` prefix -> handler. */
export type RpcMethods = Record<string, RpcHandler>;
