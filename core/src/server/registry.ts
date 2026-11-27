// The method registry: every domain module's RpcMethods merged into one map,
// served as `openhuman.<method>`. A duplicate method name is a startup error.
import { agentMethods } from "../agents/methods.js";
import { coreMethods } from "./core-methods.js";
import type { RpcHandler, RpcMethods } from "./types.js";

export const MODULES: RpcMethods[] = [
  agentMethods,
  coreMethods,
  // MERGE SLOTS: the other phases' modules join here when their branches merge.
  // Each exports an RpcMethods map; add its import above and its name below.
  //   P2:  import { cronMethods } from "../cron/index.js";          // cron_*, inbound_*
  //   P4:  import { walletMethods } from "../wallet/index.js";      // wallet_*, web3_*, x402_*
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
