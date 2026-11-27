// cron_list, cron_add, cron_update, cron_remove, cron_run: see README.md.
import type { CoreContext, RpcMethods } from "../server/types.js";
import { createCron, type RunAgent } from "./scheduler.js";

export { createCron, type Cron, type CronOptions, type RunAgent } from "./scheduler.js";

const shared = createCron();

export const methods: RpcMethods = shared.methods;

/** Opens storage.db and starts firing jobs. The server calls it once at boot. */
export function startCron(ctx: CoreContext): void {
  shared.start(ctx);
}

export function stopCron(): Promise<void> {
  return shared.stop();
}

/** P1 hands in the function that runs one isolated agent turn. */
export function setAgentRunner(fn: RunAgent | undefined): void {
  shared.setAgentRunner(fn);
}
