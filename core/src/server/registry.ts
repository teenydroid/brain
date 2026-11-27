// The method registry: every domain module's RpcMethods merged into one map,
// served as `openhuman.<method>`. A duplicate method name is a startup error.
import { agentMethods } from "../agents/methods.js";
import { memoryMethods } from "../memory/index.js";
import { methods as walletMethods } from "../wallet/index.js";
import { coreMethods } from "./core-methods.js";
import { inboundMethods } from "./inbound-methods.js";
import type { RpcHandler, RpcMethods } from "./types.js";

export const MODULES: RpcMethods[] = [
  agentMethods,
  coreMethods,
  inboundMethods, // inbound_*: a clear "not supported" (TEENY_INBOX=core is off)
  memoryMethods, // memory_*
  walletMethods, // wallet_*, web3_*, x402_*, encrypt_secret
  // The cron methods are not listed here: main.ts wireCron() builds them with
  // the [cron] shell timeout and adds them to the registry it serves.
];

export function buildRegistry(modules: readonly RpcMethods[] = MODULES): Map<string, RpcHandler> {
  const registry = new Map<string, RpcHandler>();
  for (const mod of modules) {
    for (const [name, handler] of Object.entries(mod)) {
      if (registry.has(name)) throw new Error(`rpc method ${name} is served by two modules`);
      registry.set(name, handler);
    }
  }
  return registry;
}
