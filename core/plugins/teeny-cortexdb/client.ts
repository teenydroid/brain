// The CortexDB client the plugin talks through: the core's own (src/memory/cortex.ts),
// pointed at the configured URL, with the key memory_engine_set sealed in the engine file
// when that file names the same endpoint. The engine file is read on every call, so a
// boot that sets the engine after the Gateway is up is picked up; the client itself is
// kept while the sealed key does not change (it remembers the actor CortexDB asked for).
import { dirname } from "node:path";
import { Sealer } from "../../src/config/secrets.js";
import { CortexClient, sameEndpoint } from "../../src/memory/cortex.js";
import { loadEngine } from "../../src/memory/engine.js";
import type { CortexPluginConfig } from "./config.js";

export interface ClientSource {
  /** The client for this moment, or undefined when it cannot be built (a missing master key, say). */
  get(): CortexClient | undefined;
}

export function clientSource(config: CortexPluginConfig, fetchImpl?: typeof fetch): ClientSource {
  let cached: { sealed: string | undefined; client: CortexClient } | undefined;
  return {
    get() {
      const stored = loadEngine(dirname(config.engineFile));
      const sealed = stored && sameEndpoint(stored.endpoint, config.url) ? stored.apiKeySealed : undefined;
      if (cached && cached.sealed === sealed) return cached.client;
      let apiKey: string | undefined;
      if (sealed) {
        try {
          apiKey = Sealer.fromFile(config.masterKeyFile).open(sealed);
        } catch {
          return undefined; // a key we cannot open: no call, rather than a call without it
        }
      }
      try {
        const client = new CortexClient({ endpoint: config.url, ...(apiKey ? { apiKey } : {}), scope: config.scope, ...(fetchImpl ? { fetch: fetchImpl } : {}) });
        cached = { sealed, client };
        return client;
      } catch {
        return undefined;
      }
    },
  };
}
