// The method registry: every domain module's RpcMethods merged into one map,
// served as `openhuman.<method>`. A duplicate method name is a startup error.
import { agentMethods } from "../agents/methods.js";
import { methods as walletMethods } from "../wallet/index.js";
import { coreMethods } from "./core-methods.js";
import type { RpcHandler, RpcMethods } from "./types.js";

export const MODULES: RpcMethods[] = [
  agentMethods,
  coreMethods,
  walletMethods, // wallet_*, web3_*, x402_*, encrypt_secret
  // The cron methods are not listed here: main.ts wireCron() builds them with
  // the [cron] shell timeout and the Gateway agent runner, and adds them to the
  // registry it serves. P2 (cron on OpenClaw automations) swaps wireCron's body.
  // MERGE SLOTS: the other phases' modules join here when their branches merge.
  // Each exports an RpcMethods map; add its import above and its name below.
  //   P3:  import { memoryMethods } from "../memory/index.js";      // memory_*
  // (Those paths do not exist on this branch; do not import them until they do.)
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
