import { RpcError } from "../server/types.js";

/** Prefix of every spend-policy refusal, so a caller can tell a refusal from a fault. */
export const POLICY_REFUSED_PREFIX = "WALLET_POLICY_REFUSED:";
/** Prefix of every x402 refusal. */
export const X402_REFUSED_PREFIX = "X402_REFUSED:";

export type RefusalKind =
  | "origin"
  | "automation_disabled"
  | "asset_not_allowed"
  | "per_transaction"
  | "daily"
  | "recipient_weekly"
  | "already_attempted"
  | "account_not_allowed"
  | "program_not_allowed"
  | "instruction_not_allowed"
  | "mint_not_allowed"
  | "transaction_not_allowed"
  | "simulation_mismatch"
  | "quote_expired"
  | "policy_unreadable";

/** A spend-policy refusal: thrown before anything is signed. */
export class PolicyRefusal extends Error {
  constructor(
    public readonly kind: RefusalKind,
    public readonly detail: string,
  ) {
    super(`${POLICY_REFUSED_PREFIX} ${detail}`);
    this.name = "PolicyRefusal";
  }
}

export function refusal(kind: RefusalKind, detail: string): PolicyRefusal {
  return new PolicyRefusal(kind, detail);
}

/** A plain wallet failure (not a policy refusal). */
export function fail(message: string): RpcError {
  return new RpcError(message);
}

// ---------------------------------------------------------------------------
// Keeping key material out of errors and logs.
// ---------------------------------------------------------------------------

const tracked = new Set<string>();

/**
 * Registers a sensitive string (a phrase, its words, a base58 secret) so that
 * scrub() removes it from anything that leaves the module. Entries live for
 * the life of the process and are never read back out.
 */
export function trackSecret(text: string): void {
  if (text.length >= 4) tracked.add(text);
}

export function scrub(text: string): string {
  let out = text;
  for (const secret of tracked) {
    if (out.includes(secret)) out = out.split(secret).join("[redacted]");
  }
  return out;
}

/** An error safe to hand to the RPC layer: our own errors pass, anything else is scrubbed. */
export function safeError(error: unknown): RpcError {
  if (error instanceof PolicyRefusal) return new RpcError(scrub(error.message));
  if (error instanceof RpcError) return new RpcError(scrub(error.message), error.code, undefined);
  const message = error instanceof Error ? error.message : String(error);
  return new RpcError(scrub(`wallet: ${message}`.slice(0, 500)));
}
