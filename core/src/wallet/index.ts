// The wallet domain: wallet_*, web3_*, x402_* and encrypt_secret, served as
// `openhuman.<method>` by the RPC server. See README.md for the design, the
// keyring format and what is and is not implemented.
//
// Every handler is wrapped so that nothing but a scrubbed message ever leaves
// it as an error, and no handler logs a parameter or a result.
import type { RpcHandler, RpcMethods } from "../server/types.js";
import { convertMethods } from "./convert-methods.js";
import type { Deps } from "./endpoints.js";
import { safeError, withSecretScope } from "./errors.js";
import { programMethods } from "./program-methods.js";
import { Runtime } from "./runtime.js";
import { walletMethods } from "./wallet-methods.js";
import { x402Methods } from "./x402.js";

function guarded(handler: RpcHandler): RpcHandler {
  return (params, ctx) =>
    withSecretScope(async () => {
      try {
        return await handler(params ?? {}, ctx);
      } catch (error) {
        throw safeError(error);
      }
    });
}

/** A fresh set of wallet methods with their own quote stores, for tests (DEPS replaces fetch, DNS and the clock). */
export function createMethods(deps: Partial<Deps> = {}): RpcMethods {
  const rt = new Runtime(deps);
  const all: RpcMethods = { ...walletMethods(rt), ...programMethods(rt), ...convertMethods(rt), ...x402Methods(rt) };
  return Object.fromEntries(Object.entries(all).map(([name, handler]) => [name, guarded(handler)]));
}

export const methods: RpcMethods = createMethods();

export { giveawayTxMain } from "./cli.js";
