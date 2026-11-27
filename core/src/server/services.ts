// What the RPC handlers share, carried on CoreContext.services so a domain
// module is a plain static RpcMethods map (see registry.ts).
import type { CorePaths } from "../config/paths.js";
import type { Sealer } from "../config/secrets.js";
import type { SettingsStore } from "../config/store.js";
import type { AgentRunner } from "../gateway/client.js";
import type { CostLog } from "../agents/cost.js";

export interface CoreServices {
  paths: CorePaths;
  store: SettingsStore;
  sealer: Sealer | undefined;
  runner: AgentRunner;
  costLog: CostLog;
  /** Is the Gateway child up? */
  gatewayRunning: () => boolean;
  /** Settings the Gateway config is built from changed: rewrite it and restart the Gateway (debounced). */
  configChanged: () => void;
  hasAgent: (id: string) => boolean;
  /** Rewrite the agent's AGENTS.md from its current prompt before a turn; throws for an unknown agent. */
  prepareAgent: (id: string) => void;
  /** Where Chrome is, for the browser readiness probe; undefined when none is found. */
  findChrome: () => string | undefined;
}
