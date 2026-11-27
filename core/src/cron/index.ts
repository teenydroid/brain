// cron_list, cron_add, cron_update, cron_remove, cron_run over OpenClaw automations: see README.md.
import type { CoreContext, RpcMethods } from "../server/types.js";
import { createCron } from "./scheduler.js";

export { createCron, type Cron, type CronOptions } from "./scheduler.js";
export { commandOf, shellArgv, view } from "./map.js";

const shared = createCron();

export const methods: RpcMethods = shared.methods;

/** The server calls it once at boot: pins cron.skipMissedJobs. */
export function startCron(ctx: CoreContext): Promise<void> {
  return shared.start(ctx);
}

export function stopCron(): Promise<void> {
  return shared.stop();
}
